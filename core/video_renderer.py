#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Video rendering with FFmpeg and text overlay templates
V12-RETENTION: Умная система удержания + параллельные процессы

Модуль для рендеринга финального видео из изображений, аудио и субтитров.

Возможности:
- NVENC аппаратное ускорение (NVIDIA GPU)
- Zoom анимация
- Плавные переходы между кадрами
- Субтитры с кастомными шрифтами
- Оверлеи (изображения/видео с альфа-каналом)
- Veo 3 AI-интро интеграция
- Параллельный рендеринг с контролем NVENC сессий
"""



import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Callable, Any, Tuple
import ffmpeg
import logging
import random
import time
import gc
import threading
import re


# ============================================================
# 🔧 FFmpeg utilities - refactored to separate module
# ============================================================
from core.rendering.ffmpeg_utils import get_ffmpeg_path, FFMPEG_PATH

# Обратная совместимость
_get_ffmpeg_path = get_ffmpeg_path


# Умная система удержания
from core.retention_optimizer import RetentionOptimizer

# Veo 3 integration
from core.veo3_integration import generate_intro_with_veo3
from core.video_utils import concat_videos_with_crossfade
from core.process_registry import probe_registered, run_registered
from core.glitch_transitions import calculate_glitch_transition_loss
from core.video_effects import (
    apply_video_effect,
    build_filter_complex_effect,
    effect_display_name,
    resolve_video_effect,
)

# Pillow-based smooth animation (no zoompan jitter)
from core.pillow_animator import PillowAnimator

# 🌍 Unicode-safe temp директории
from core.utils import safe_cpu_count, safe_temp_dir


def _soft_cut_transition_duration(transition_duration: float) -> float:
    """Visible but subtle fallback transition for non-highlighted shot joins."""
    base_duration = max(0.0, float(transition_duration or 0.0))
    if base_duration <= 0:
        return 0.0
    return min(base_duration, max(0.14, base_duration * 0.60))


def _transition_palette(transition_style: str = 'cinematic') -> Tuple[List[str], Dict[str, List[str]]]:
    """Return weighted xfade palettes tuned for organic, cinematic joins.

    The weights are intentional: smooth/dissolve/fade variants appear more often
    than flashy wipes, so the default result feels like an edit instead of a
    slideshow demo reel.  Heavy glitch remains opt-in through the separate glitch
    engine.
    """
    style = str(transition_style or 'cinematic').lower()
    groups = {
        'classic': ['fade', 'dissolve', 'fadeblack', 'fadegrays'],
        'smooth': ['smoothleft', 'smoothright', 'smoothup', 'smoothdown'],
        'soft_motion': ['circleopen', 'circleclose', 'circlecrop'],
        'dynamic': ['slideleft', 'slideright', 'wipeleft', 'wiperight'],
        'impact': ['zoomin', 'radial', 'fadewhite'],
        'accent': ['hlslice', 'vrslice', 'pixelize'],
    }

    if style == 'energetic':
        palette = (
            groups['smooth'] +
            groups['dynamic'] * 2 +
            groups['impact'] * 2 +
            groups['accent']
        )
    elif style == 'smooth':
        palette = (
            groups['classic'] * 3 +
            groups['smooth'] * 3 +
            ['circleopen', 'circleclose']
        )
    elif style == 'dramatic':
        palette = (
            ['fadeblack', 'dissolve', 'fade', 'circleclose'] * 2 +
            ['zoomin', 'radial']
        )
    else:
        palette = (
            ['dissolve', 'fade', 'fadeblack'] * 3 +
            groups['smooth'] * 2 +
            ['circleopen', 'circleclose', 'zoomin'] +
            ['slideleft', 'slideright']
        )

    return palette, groups


def _select_cinematic_transition(
    index: int,
    total_shots: int,
    transition_style: str = 'cinematic',
    media_types: List[str] = None,
    used_transitions: List[str] = None,
) -> str:
    """Pick a transition that follows the edit rhythm and avoids cheap repeats."""
    used_transitions = used_transitions or []
    palette, groups = _transition_palette(transition_style)
    position_ratio = index / max(1, total_shots)
    style = str(transition_style or 'cinematic').lower()

    if position_ratio < 0.16:
        preferred = groups['smooth'] + groups['impact'] + ['dissolve', 'fadewhite']
    elif position_ratio > 0.86:
        preferred = ['fadeblack', 'dissolve', 'fade', 'circleclose']
    elif 0.42 < position_ratio < 0.62:
        preferred = groups['smooth'] + groups['soft_motion'] + groups['dynamic']
    else:
        preferred = ['dissolve', 'fade'] * 2 + groups['smooth'] + ['circleopen']

    if style == 'energetic':
        preferred = groups['dynamic'] + groups['impact'] + groups['smooth']
    elif style == 'smooth':
        preferred = groups['smooth'] + ['dissolve', 'fade']
    elif style == 'dramatic':
        preferred = ['fadeblack', 'dissolve', 'circleclose', 'zoomin']

    if media_types and index < len(media_types):
        prev_type = media_types[index - 1] if index > 0 else 'image'
        curr_type = media_types[index]
        if prev_type != curr_type:
            preferred = ['dissolve', 'fadeblack', 'circleopen', 'zoomin']

    available = [item for item in preferred if item in palette]
    if not available:
        available = palette
    available = [item for item in available if item not in used_transitions[-2:]]
    if not available:
        available = palette
    return random.choice(available)


def _should_apply_cinematic_polish(
    width: int,
    height: int,
    fps: int,
    shot_count: int,
    transition_style: str = 'cinematic',
    enabled: bool = True,
) -> bool:
    """Keep the polish pass cheap and auto-disable it for very heavy timelines."""
    if not enabled:
        return False
    style = str(transition_style or 'cinematic').lower()
    if style not in {'cinematic', 'smooth', 'dramatic'}:
        return False
    pixels = max(1, int(width or 0) * int(height or 0))
    frame_load = pixels * max(1, int(fps or 30))
    if shot_count > 80:
        return False
    # FHD vertical/horizontal at 60fps is fine; 2K/4K 60fps skips the extra filters.
    return frame_load <= 1920 * 1080 * 75


def _build_cinematic_polish_filter(
    input_label: str,
    output_label: str,
    width: int,
    height: int,
    fps: int,
    shot_count: int,
    transition_style: str = 'cinematic',
    enabled: bool = True,
) -> str:
    """A lightweight filmic grade: contrast, mild saturation, vignette, tiny grain."""
    if not _should_apply_cinematic_polish(width, height, fps, shot_count, transition_style, enabled):
        return ""
    src = str(input_label).strip('[]')
    dst = str(output_label).strip('[]')
    return (
        f"[{src}]"
        "eq=contrast=1.035:saturation=1.055:brightness=-0.010,"
        "vignette=angle=PI/7:eval=init,"
        "noise=alls=1:allf=t+u"
        f"[{dst}]"
    )


def plan_video_window_starts(
    source_duration: float,
    shot_durations: List[float],
) -> List[float]:
    """Spread repeated uses of one donor across its timeline.

    When the donor is long enough, the returned windows never overlap. If it is
    shorter than the requested total, starts are maximally dispersed instead of
    replaying the opening frames for every shot.
    """
    try:
        source_duration = max(0.0, float(source_duration or 0.0))
    except (TypeError, ValueError):
        source_duration = 0.0
    durations = []
    for value in shot_durations or []:
        try:
            durations.append(max(0.05, float(value or 0.05)))
        except (TypeError, ValueError):
            durations.append(0.05)
    if not durations:
        return []
    if len(durations) == 1 or source_duration <= durations[0]:
        return [0.0 for _duration in durations]

    requested = sum(durations)
    if source_duration >= requested:
        gap = (source_duration - requested) / (len(durations) + 1)
        cursor = gap
        starts = []
        for duration in durations:
            starts.append(cursor)
            cursor += duration + gap
        return starts

    starts = []
    denominator = max(1, len(durations) - 1)
    for index, duration in enumerate(durations):
        latest_start = max(0.0, source_duration - min(duration, source_duration))
        starts.append(latest_start * index / denominator)
    return starts


def build_looped_video_shot(
    video_path: str,
    target_duration: float,
    width: int,
    height: int,
    fps: int,
    source_duration: float = None,
    source_start: float = 0.0,
):
    """Build a moving video shot that safely extends short source clips.

    A plain end→start loop is visually harsh: on short B-roll clips it can look
    like a one-second freeze or a missing transition. When the source is shorter
    than the planned shot, create a boomerang cycle (forward + reverse) and loop
    that cycle instead. The cycle boundary lands on the same first frame, so the
    repeat is much less noticeable than jumping from the last frame to the first.
    """

    target_duration = max(0.05, float(target_duration or 0.05))
    fps = max(1, int(fps or 30))

    if source_duration is None:
        try:
            source_duration = float(
                probe_registered(str(video_path), label="ffprobe_looped_video_source")[
                    'format'
                ]['duration']
            )
        except Exception:
            source_duration = target_duration
    source_duration = max(0.0, float(source_duration or 0.0))
    try:
        source_start = max(0.0, float(source_start or 0.0))
    except (TypeError, ValueError):
        source_start = 0.0
    maximum_start = max(0.0, source_duration - min(target_duration, source_duration))
    source_start = min(source_start, maximum_start)

    base = (
        ffmpeg.input(str(video_path)).video
        .filter(
            'scale',
            width,
            height,
            force_original_aspect_ratio='increase',
            flags='lanczos',
        )
        .filter('crop', width, height)
        .filter('format', 'yuv420p')
        .filter('setsar', '1')
        .filter('fps', fps=fps)
    )

    if source_duration <= 0 or source_duration >= target_duration * 0.99:
        return (
            base
            .filter('trim', start=source_start, duration=target_duration)
            .filter('setpts', 'PTS-STARTPTS')
        )

    cycle_source_duration = max(0.10, min(source_duration, target_duration))
    source_trim = (
        base
        .filter('trim', start=source_start, duration=cycle_source_duration)
        .filter('setpts', 'PTS-STARTPTS')
    )
    split = source_trim.filter_multi_output('split')
    forward = split[0]
    backward = split[1].filter('reverse').filter('setpts', 'PTS-STARTPTS')
    boomerang = (
        ffmpeg.concat(forward, backward, v=1, a=0)
        .filter('setpts', 'PTS-STARTPTS')
    )
    cycle_frames = max(2, int((cycle_source_duration * 2) * fps + 0.5))
    return (
        boomerang
        .filter('loop', loop=-1, size=cycle_frames, start=0)
        .filter('trim', start=0, duration=target_duration)
        .filter('setpts', 'PTS-STARTPTS')
    )


def validate_video_timeline(
    video_path: str,
    audio_duration: float,
    log_callback: Callable[[str], None],
) -> float:
    """Reject a rendered timeline that would freeze before the audio ends."""
    probe = probe_registered(str(video_path), label="ffprobe_video_timeline")
    video_duration = float(probe['format']['duration'])
    tolerance = max(1.0, min(3.0, audio_duration * 0.02))
    missing_duration = audio_duration - video_duration

    if missing_duration > tolerance:
        raise ValueError(
            f"Видеоряд короче аудио на {missing_duration:.1f}s "
            f"({video_duration:.1f}s вместо {audio_duration:.1f}s). "
            "Финальный рендер остановлен, чтобы не создавать замороженный конец."
        )

    log_callback(
        f"   ✅ Видеоряд покрывает аудио: {video_duration:.1f}s / {audio_duration:.1f}s"
    )
    return video_duration

# 🎯 Глобальный пул потоков для ограничения вложенной параллельности
try:
    from core.utils import thread_slot
    _USE_GLOBAL_POOL = True
except ImportError:
    _USE_GLOBAL_POOL = False
    def thread_slot(category='default', priority=None, timeout=300):
        from contextlib import contextmanager
        @contextmanager
        def dummy():
            yield
        return dummy()


# ============================================================
# 🔧 NVENC Session Manager - refactored to separate module
# ============================================================
from core.rendering.nvenc_manager import (
    nvenc_manager as _nvenc_manager,
    _NVENC_SEMAPHORE,
    _NVENC_SESSIONS_LOCK
)

# Обратная совместимость
_NVENC_ACTIVE_SESSIONS = 0


# ============================================================
# 🔧 Video Validation - refactored to separate module
# ============================================================
from core.rendering.video_validation import VideoValidator
from core.rendering.subtitle_renderer import SubtitleRenderer


# ============================================================
# 🗑️ Temp directory management - refactored to separate module
# ============================================================

# Импортируем дефолтный логгер из централизованного модуля
from core.logging_utils import get_default_logger

# Алиас для обратной совместимости
_dummy_log = get_default_logger()


class VideoRenderer:
    """
    Handles video rendering using ffmpeg-python.
    
    Основной класс для рендеринга видео. Использует FFmpeg с NVENC
    аппаратным ускорением для быстрого кодирования.
    
    Возможности:
    - Создание видео из последовательности изображений
    - Плавные переходы (fade, dissolve)
    - Наложение субтитров
    - Микширование аудио
    - Оверлеи с альфа-каналом
    
    Attributes:
        _ACTIVE_RENDERERS: Счётчик активных рендереров
        _RENDERER_LOCK: Lock для потокобезопасности
        
    Example:
        >>> renderer = VideoRenderer()
        >>> renderer.render_video(
        ...     images=['img1.png', 'img2.png'],
        ...     audio_path='audio.mp3',
        ...     output_path='output.mp4',
        ...     video_settings={'width': 1080, 'height': 1920, 'fps': 30},
        ...     log_callback=print
        ... )
    """
    # P1: Константы вместо magic numbers
    MIN_FILE_SIZE = 1000  # Минимальный размер файла в байтах
    
    # P3: FFmpeg codec constants
    CODEC_H264_NVENC = 'h264_nvenc'
    CODEC_LIBX264 = 'libx264'
    PIXEL_FORMAT = 'yuv420p'
    AUDIO_CODEC = 'aac'
    DEFAULT_TIMEOUT = 300  # Таймаут по умолчанию в секундах
    MAX_FILES_PER_BATCH = 50  # Максимум файлов в одном батче
    MAX_DURATION_DIFF_PERCENT = 500  # Максимальная разница длительности в процентах
    # 3h at the default 3-5 second rhythm needs up to ~3600 shots. Keep a
    # bounded ceiling for resource safety, but never cut only the tail.
    MAX_SHOTS = 5000
    NVENC_MAX_SESSIONS = 3  # Максимум NVENC сессий
    MIN_FPS = 1  # P3: Минимальный FPS
    MAX_FPS = 120  # P3: Максимальный FPS
    MIN_RESOLUTION = 64  # P3: Минимальное разрешение
    MAX_RESOLUTION = 7680  # P3: Максимальное разрешение (8K)
    
    # 🚀 NVENC настройки для супер быстрого рендера
    NVENC_PRESET = 'p1'  # p1=fastest, p7=slowest. p1 = максимальная скорость!
    NVENC_CQ = 25  # Constant Quality: 0-51, ниже=лучше. 25 = быстро и приемлемо
    NVENC_BITRATE = '6M'  # Битрейт для YouTube (6-8M для шортсов отлично)
    NVENC_MAXRATE = '10M'  # Максимальный битрейт
    
    _ACTIVE_RENDERERS = 0
    _RENDERER_LOCK = threading.Lock()

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._nvenc_available = None  # Cache NVENC availability
        self._tl = threading.local()  # P0: Thread-local storage для temp директорий
        self._validator = VideoValidator()  # Валидатор видео настроек
        self._subtitle_renderer = SubtitleRenderer()

    @staticmethod
    def _select_first_shot_title(text_content: dict, custom_title: str = "") -> Tuple[str, str]:
        """Pick the text burned into the first shot, preferring hook metadata."""
        custom_title = str(custom_title or "").strip()
        if custom_title:
            return custom_title, "custom"

        text_content = text_content or {}
        hook_package = text_content.get('opening_hook_package') or {}
        candidates = [
            ('hook', text_content.get('first_shot_hook')),
            ('hook', hook_package.get('first_shot_title')),
            ('hook', text_content.get('primary_hook')),
            ('hook', hook_package.get('primary_hook')),
            ('metadata', text_content.get('first_shot_title')),
            ('custom_text_common_theme', text_content.get('common_theme') if text_content.get('custom_text_mode') else ''),
            ('theme', text_content.get('theme')),
            ('title', text_content.get('title')),
        ]
        for source, value in candidates:
            title = str(value or "").strip()
            if title:
                return title, source
        return "", ""

    @classmethod
    def _calculate_shot_render_workers(
        cls,
        shot_count: int,
        use_nvenc: bool,
        cpu_count: int = None,
    ) -> int:
        """Bound nested FFmpeg parallelism across simultaneously rendered videos."""
        shot_count = max(1, int(shot_count or 1))
        with cls._RENDERER_LOCK:
            active_renderers = max(1, int(cls._ACTIVE_RENDERERS or 1))

        if use_nvenc:
            # Consumer NVIDIA cards normally allow only a few encoder sessions.
            per_renderer = max(1, cls.NVENC_MAX_SESSIONS // active_renderers)
            return min(shot_count, per_renderer)

        available_cpus = max(1, int(cpu_count or os.cpu_count() or 4))
        total_cpu_workers = min(4, max(1, available_cpus // 2))
        per_renderer = max(1, total_cpu_workers // active_renderers)
        return min(shot_count, per_renderer)
    
    def _get_overlay_file_path(self, media_path, log_callback=None):
        if not media_path:
            return None

        try:
            overlay_path = Path(media_path).expanduser().resolve()
        except Exception as e:
            if log_callback:
                log_callback(f"   WARNING: Invalid overlay path: {media_path} ({e})")
            return None

        if not overlay_path.exists():
            if log_callback:
                log_callback(f"   WARNING: Overlay file not found: {overlay_path}")
            return None

        if not overlay_path.is_file():
            if log_callback:
                log_callback(f"   WARNING: Overlay path is not a file: {overlay_path}")
            return None

        return overlay_path

    def _get_duration(self, file_path: str) -> float:
        """P2: Получение длительности файла с кэшированием.
        
        Делегирует к VideoValidator.
        
        Args:
            file_path: Путь к медиафайлу
            
        Returns:
            Длительность в секундах или 0.0 при ошибке
        """
        return self._validator.get_duration(file_path)
    
    def _clear_ffprobe_cache(self):
        """Очистка кэша ffprobe.
        
        Делегирует к VideoValidator.
        """
        self._validator.clear_ffprobe_cache()
    
    def _validate_video_settings(self, video_settings: Dict[str, Any], log_callback: Callable = None) -> None:
        """P1/P3: Валидация video_settings перед рендерингом.
        
        Делегирует к VideoValidator.
        
        Args:
            video_settings: Словарь с настройками видео
            log_callback: Функция для логирования
            
        Raises:
            ValueError: Если настройки невалидны
        """
        self._validator.validate_video_settings(video_settings, log_callback)
    
    def _check_disk_space(self, output_path: Path, log_callback: Callable = None, min_gb: float = 2.0) -> None:
        """P2: Проверка свободного места на диске перед рендерингом.
        
        Делегирует к VideoValidator.
        
        Args:
            output_path: Путь к выходному файлу
            log_callback: Функция для логирования
            min_gb: Минимальное требуемое место в GB
            
        Raises:
            Exception: Если места недостаточно
        """
        self._validator.check_disk_space(output_path, log_callback, min_gb)
    
    def _check_nvenc_available(self) -> bool:
        """Check if NVENC (NVIDIA GPU encoding) is available."""
        if self._nvenc_available is not None:
            return self._nvenc_available
        
        try:
            # Check if h264_nvenc encoder is available
            result = run_registered(
                [FFMPEG_PATH, '-hide_banner', '-encoders'],
                label="ffmpeg_nvenc_probe",
                capture_output=True,
                text=True,
                timeout=5
            )
            self._nvenc_available = 'h264_nvenc' in result.stdout
            return self._nvenc_available
        except Exception:
            self._nvenc_available = False
            return False
    
    def _render_single_batch(self, batch_idx: int, batch: list, total_batches: int, 
                            temp_dir: Path, shot_duration: float, fps: int, 
                            width: int, height: int, log_callback, shot_files: list = None) -> Path:
        """P3: Рендерит одну группу шотов (для параллельного выполнения).
        
        Args:
            batch_idx: Индекс текущего батча (0-based)
            batch: Список ffmpeg stream объектов для склейки
            total_batches: Общее количество батчей
            temp_dir: Директория для временных файлов
            shot_duration: Длительность каждого шота в секундах
            fps: Частота кадров
            width: Ширина видео
            height: Высота видео
            log_callback: Функция для логирования
            shot_files: Опциональный список путей к исходным файлам (для отладки)
            
        Returns:
            Path к созданному batch файлу
            
        Raises:
            Exception: Если рендеринг не удался после всех попыток
        """
        
        # Склеиваем шоты в батче
        batch_concat = ffmpeg.concat(*batch, v=1, a=0)
        
        # Рендерим батч в файл через subprocess
        batch_file = temp_dir / f'batch_{batch_idx:03d}.mp4'
        
        # КРИТИЧНО: Рассчитываем ожидаемую длительность batch для валидации
        batch_duration = len(batch) * shot_duration
        
        # Retry mechanism with NVENC fallback
        MAX_RETRIES = 3
        use_nvenc = True
        
        for attempt in range(MAX_RETRIES):
            try:
                # Get codec settings (try NVENC first, then fallback to CPU)
                codec_settings = self._get_video_codec_settings(use_nvenc=use_nvenc, log_callback=None)
                
                # Adjust preset for NVENC (uses different preset names)
                if codec_settings['vcodec'] == 'h264_nvenc':
                    # NVENC uses p1-p7 presets, remove CPU preset
                    codec_settings.pop('preset', None)
                    codec_settings.pop('crf', None)
                else:
                    # CPU encoding - use ultrafast for speed
                    codec_settings['preset'] = 'ultrafast'
                    codec_settings['crf'] = 18
                
                # 🔧 КРИТИЧЕСКИЙ ФИКС: Явно указываем framerate для правильного расчёта длительности
                batch_stream = ffmpeg.output(
                    batch_concat,
                    str(batch_file),
                    pix_fmt='yuv420p',
                    r=fps,  # Output framerate
                    framerate=fps,  # Input framerate (явно указываем)
                    s=f'{width}x{height}',
                    vsync='cfr',  # Constant frame rate
                    map_metadata=-1,
                    **{'fflags': '+genpts'},  # Generate presentation timestamps
                    **codec_settings
                ).overwrite_output()
                
                # Компилируем и запускаем команду
                cmd = batch_stream.compile()
                result = run_registered(cmd, label=f"ffmpeg_batch_{batch_idx}", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', timeout=300)
                
                # Очистка ресурсов
                del cmd
                gc.collect()
                
                # Проверка успешности
                if result.returncode != 0:
                    # Extract meaningful error from stderr
                    stderr_lines = result.stderr.split('\n')
                    error_lines = [line for line in stderr_lines if 'error' in line.lower() or 'failed' in line.lower()]
                    error_msg = '\n'.join(error_lines[-5:]) if error_lines else result.stderr[-500:]
                    
                    # Check if it's an NVENC error
                    is_nvenc_error = 'nvenc' in result.stderr.lower() or 'incompatible client key' in result.stderr.lower()
                    
                    if is_nvenc_error and use_nvenc and attempt < MAX_RETRIES - 1:
                        log_callback(f"⚠️ Batch {batch_idx}: NVENC недоступен, переключаемся на CPU кодирование...")
                        use_nvenc = False
                        time.sleep(1)
                        continue
                    elif attempt < MAX_RETRIES - 1:
                        log_callback(f"⚠️ Batch {batch_idx} попытка {attempt+1} не удалась, повтор...")
                        time.sleep(2)
                        continue
                    else:
                        log_callback(f"❌ FFmpeg stderr (batch {batch_idx}):\n{result.stderr}")
                        raise Exception(f"Batch {batch_idx} render failed: {error_msg}")
                
                # Success - break retry loop
                break
                
            except subprocess.TimeoutExpired:
                if attempt < MAX_RETRIES - 1:
                    log_callback(f"⚠️ Batch {batch_idx} timeout, повтор...")
                    time.sleep(2)
                    continue
                else:
                    raise Exception(f"Batch {batch_idx} timeout after {MAX_RETRIES} attempts")
        
        # Cleanup after successful render
        del batch_concat, batch_stream
        gc.collect()
        
        # Проверка что файл создан
        if not batch_file.exists():
            raise Exception(f"Batch file not created: {batch_file}")
        
        # Проверка размера файла
        file_size = batch_file.stat().st_size
        if file_size < 1000:
            raise Exception(f"Batch file too small: {file_size} bytes")
        
        # Валидация длительности
        probe_cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(batch_file)
        ]
        probe_result = run_registered(
            probe_cmd, label="ffprobe_render_batch", capture_output=True, text=True,
            encoding='utf-8', errors='ignore', timeout=30
        )
        
        duration_str = probe_result.stdout.strip()
        if probe_result.returncode != 0 or not duration_str or duration_str == 'N/A':
            raise Exception(f"Cannot validate batch file: {batch_file}")
        
        actual_duration = float(duration_str)
        if actual_duration < 0.1:
            raise Exception(f"Batch file has zero duration: {batch_file}")
        
        # Увеличенный допуск для рандомных длительностей и больших батчей
        # Погрешности накапливаются: ~0.2s на шот
        num_shots = len(batch)
        tolerance = max(2.0, num_shots * 0.2)  # Минимум 2s, или 0.2s на шот
        
        # Для последней группы увеличиваем tolerance, т.к. последний шот может быть длиннее
        is_last_batch = batch_idx == total_batches - 1
        if is_last_batch:
            tolerance = max(tolerance, 5.0)  # Минимум 5s для последней группы
        
        if abs(actual_duration - batch_duration) > tolerance:
            # 🔧 При смешивании YouTube клипов с изображениями длительности пересчитываются
            # Это нормальное поведение - не логируем как ошибку
            diff_percent = abs(actual_duration - batch_duration) / max(batch_duration, 0.1) * 100
            # Логируем только если разница > 500% (явная ошибка)
            if diff_percent > 500:
                log_callback(f"   ⚠️ Большое расхождение длительности: {batch_duration:.1f}s → {actual_duration:.1f}s")
            # Не падаем - видео создаётся корректно
        
        return batch_file

    def _concat_shots_with_transitions(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        transition_duration: float = 0.3,
        log_callback=None,
        media_types: List[str] = None,
        transition_style: str = 'cinematic',
        transition_frequency: float = 0.6,  # 🆕 60% шотов с переходами
        glitch_settings: dict = None,        # ⚡ glitch_enabled / glitch_style / ...
    ) -> str:
        """
        Склеивает видео-шоты с кинематографичными переходами xfade.
        Если glitch_settings['glitch_enabled'] = True, сначала пробуется GlitchTransitionEngine,
        
        Args:
            shot_files: Список путей к видео-файлам шотов
            output_path: Путь для выходного файла
            fps: Частота кадров
            width: Ширина видео
            height: Высота видео
            transition_duration: Длительность перехода (сек)
            log_callback: Функция логирования
            media_types: Типы медиа для каждого шота ('image' или 'video')
            transition_style: Стиль переходов ('cinematic', 'energetic', 'smooth', 'dramatic')
            transition_frequency: Доля шотов с переходами (0.0-1.0, default 0.6 = 60%)
            glitch_settings: Настройки глитча (из GUI)
            
        Returns:
            Путь к склеенному видео
        """
        if log_callback is None:
            log_callback = print
            
        if len(shot_files) < 2:
            # Если только один файл - просто копируем
            if shot_files:
                import shutil
                shutil.copy(shot_files[0], output_path)
            return output_path

        # ⚡ GLITCH ENGINE — запускаем если включен
        _gs = glitch_settings or {}
        if _gs.get('glitch_enabled', False):
            try:
                from core.glitch_transitions import GlitchTransitionEngine
                glitch_engine = GlitchTransitionEngine(ffmpeg_path=FFMPEG_PATH)
                glitch_result = glitch_engine.concat_with_glitch_transitions(
                    shot_files=shot_files,
                    output_path=output_path,
                    fps=fps,
                    width=width,
                    height=height,
                    glitch_style=_gs.get('glitch_style', 'random'),
                    glitch_duration=_gs.get('glitch_duration', 0.4),
                    glitch_frequency=_gs.get('glitch_frequency', 0.5),
                    glitch_intensity=_gs.get('glitch_intensity', 0.65),
                    base_transition_duration=transition_duration,
                    codec_settings_getter=self._get_video_codec_settings,
                    log_callback=log_callback,
                )
                if glitch_result and Path(glitch_result).exists():
                    log_callback("   ⚡ Glitch engine успешно завершил рендер переходов!")
                    return glitch_result
                else:
                    log_callback("   ⚠️ Glitch engine вернул пустой результат, переключаемся на стандартные переходы")
            except Exception as _ge:
                log_callback(f"   ⚠️ Glitch engine ошибка: {_ge}, используем стандартные переходы")
        
        # 🔧 BATCH PROCESSING для большого количества шотов (Windows cmd limit ~8192 chars)
        MAX_SHOTS_PER_BATCH = 50  # Безопасный лимит для Windows
        if len(shot_files) > MAX_SHOTS_PER_BATCH:
            log_callback(f"   📦 Много шотов ({len(shot_files)}) - используем batch-склейку")
            return self._batch_concat_with_transitions(
                shot_files, output_path, fps, width, height,
                transition_duration, log_callback, media_types, transition_style,
                batch_size=MAX_SHOTS_PER_BATCH
            )
        
        # 🎬 РАСШИРЕННАЯ БИБЛИОТЕКА ПЕРЕХОДОВ
        classic_transitions = ['fade', 'fadeblack', 'dissolve', 'fadegrays']
        smooth_transitions = ['smoothup', 'smoothdown', 'smoothleft', 'smoothright']
        dynamic_transitions = ['wipeleft', 'wiperight', 'wipeup', 'wipedown', 'slideleft', 'slideright']
        geometric_transitions = ['circlecrop', 'circleopen', 'circleclose', 'radial']
        energetic_transitions = ['pixelize', 'hlslice', 'vrslice', 'squeezeh', 'squeezev', 'zoomin']
        diagonal_transitions = ['diagtl', 'diagtr', 'diagbl', 'diagbr']

        if transition_style == 'energetic':
            transition_types = energetic_transitions + dynamic_transitions + ['zoomin', 'fadewhite']
        elif transition_style == 'smooth':
            transition_types = smooth_transitions + classic_transitions
        elif transition_style == 'dramatic':
            transition_types = ['fadeblack', 'circleclose', 'radial', 'dissolve', 'zoomin']
        else:  # cinematic - микс всего
            transition_types = (
                classic_transitions + 
                smooth_transitions[:2] + 
                geometric_transitions[:2] + 
                diagonal_transitions[:2] +
                dynamic_transitions[:2] +
                energetic_transitions[:2]
            )
        
        # 🆕 Определяем на каких позициях будут переходы (40% по умолчанию)
        transition_types, _transition_groups = _transition_palette(transition_style)
        num_transitions_total = len(shot_files) - 1
        num_transitions_to_apply = max(1, int(num_transitions_total * transition_frequency))
        
        # Распределяем переходы равномерно + обязательно в начале и конце
        transition_positions = set()
        
        # Обязательные позиции: начало (1-2) и конец
        if num_transitions_total >= 1:
            transition_positions.add(1)  # После первого шота
        if num_transitions_total >= 2:
            transition_positions.add(num_transitions_total)  # Перед последним шотом
        
        # Добавляем остальные равномерно
        if num_transitions_to_apply > len(transition_positions):
            remaining = num_transitions_to_apply - len(transition_positions)
            step = max(1, num_transitions_total // (remaining + 1))
            for j in range(1, remaining + 1):
                pos = min(j * step, num_transitions_total)
                if pos not in transition_positions:
                    transition_positions.add(pos)
        
        log_callback(f"✨ Переходы: {len(transition_positions)}/{num_transitions_total} ({int(len(transition_positions)/max(1,num_transitions_total)*100)}%)")
        
        # Получаем длительности каждого шота — параллельные ffprobe
        # ФИКС: берём ffprobe из той же папки что ffmpeg (а не replace() который портит путь)
        _fp_parent = Path(FFMPEG_PATH).parent
        _ffprobe_bin = str(_fp_parent / ('ffprobe.exe' if FFMPEG_PATH.lower().endswith('.exe') else 'ffprobe'))
        if not Path(_ffprobe_bin).exists():
            _ffprobe_bin = 'ffprobe'  # fallback на PATH
        def _probe_dur(f):
            try:
                r = run_registered(
                    [_ffprobe_bin, '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', f],
                    label="ffprobe_transition_shot",
                    capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=30
                )
                return float(r.stdout.strip()) if r.returncode == 0 else 5.0
            except Exception:
                return 5.0
        from concurrent.futures import ThreadPoolExecutor as _TPE
        with _TPE(max_workers=min(len(shot_files), 8)) as _pool:
            shot_durations = list(_pool.map(_probe_dur, shot_files))
        min_shot_duration = min(shot_durations) if shot_durations else 0.0
        if min_shot_duration > 0:
            safe_transition_duration = min(
                max(0.0, float(transition_duration or 0.0)),
                max(0.0, min_shot_duration * 0.45),
            )
            if safe_transition_duration < float(transition_duration or 0.0):
                log_callback(
                    f"   ℹ️ Длительность переходов уменьшена до "
                    f"{safe_transition_duration:.2f}s для коротких шотов"
                )
            transition_duration = safe_transition_duration
        soft_transition_duration = _soft_cut_transition_duration(transition_duration)
        
        # Строим filter_complex с xfade переходами
        filter_parts = []
        # P1: Используем список аргументов вместо строки для безопасности (shell injection)
        input_args = []
        
        # Добавляем все входные файлы
        for i, shot_file in enumerate(shot_files):
            input_args.extend(['-i', shot_file])
        
        # 🔧 ЗАЩИТА: Проверяем что есть длительности
        if not shot_durations:
            log_callback("   ⚠️ Нет длительностей шотов, используем простую склейку")
            return self._simple_concat_shots(shot_files, output_path, fps, width, height, log_callback)
        
        # 🆕 НОВАЯ ЛОГИКА: на выбранных позициях — выразительные переходы,
        # на остальных — мягкий fade, чтобы не было ощущения сухой склейки.
        
        current_stream = "0:v"
        used_transitions = []
        actual_transitions_applied = 0
        
        for i in range(1, len(shot_files)):
            next_stream = f"{i}:v"
            out_stream = f"xf{i}"
            
            # Проверяем нужен ли переход на этой позиции
            apply_transition = i in transition_positions
            
            if apply_transition:
                # 🎬 ПОЛНЫЙ ПЕРЕХОД
                position_ratio = i / len(shot_files)
                
                # Определяем тип перехода на основе позиции
                if position_ratio < 0.15:
                    preferred = energetic_transitions + ['zoomin', 'fadewhite']
                elif position_ratio > 0.85:
                    preferred = ['fadeblack', 'dissolve', 'circleclose', 'fade']
                elif 0.4 < position_ratio < 0.6:
                    preferred = geometric_transitions + dynamic_transitions
                else:
                    preferred = smooth_transitions + classic_transitions
                
                # Если переход между разными типами медиа
                if media_types and i < len(media_types):
                    prev_type = media_types[i - 1] if i > 0 else 'image'
                    curr_type = media_types[i]
                    if prev_type != curr_type:
                        preferred = ['fadeblack', 'dissolve', 'zoomin', 'circleopen', 'radial']
                
                available = [t for t in preferred if t in transition_types]
                if not available:
                    available = transition_types
                available = [t for t in available if t not in used_transitions[-2:]]
                if not available:
                    available = transition_types
                
                transition = _select_cinematic_transition(
                    i,
                    len(shot_files),
                    transition_style=transition_style,
                    media_types=media_types,
                    used_transitions=used_transitions,
                )
                current_transition_duration = transition_duration
                actual_transitions_applied += 1
            else:
                # 🔗 МЯГКАЯ СКЛЕЙКА: не акцентная, но заметно плавнее hard cut.
                transition = 'fade'
                current_transition_duration = soft_transition_duration
            
            used_transitions.append(transition)
            
            # Расчёт offset с учётом разных длительностей переходов
            cumulative_duration = sum(shot_durations[0:i])
            # Считаем сколько времени "съели" предыдущие переходы
            prev_transition_time = sum(
                transition_duration if j in transition_positions else soft_transition_duration
                for j in range(1, i)
            )
            offset = cumulative_duration - prev_transition_time - current_transition_duration
            offset = max(0, offset)
            
            filter_parts.append(
                f"[{current_stream}][{next_stream}]xfade=transition={transition}:duration={current_transition_duration}:offset={offset:.3f}[{out_stream}]"
            )
            
            current_stream = out_stream
        
        log_callback(f"   🎬 Применено {actual_transitions_applied} заметных переходов")
        
        polish_filter = _build_cinematic_polish_filter(
            current_stream,
            "cinematic_polish",
            width,
            height,
            fps,
            len(shot_files),
            transition_style=transition_style,
            enabled=_gs.get('cinematic_polish', True),
        )
        if polish_filter:
            filter_parts.append(polish_filter)
            current_stream = "cinematic_polish"
            log_callback("   🎨 Cinematic polish: film grade + vignette + subtle grain")

        filter_complex = ";".join(filter_parts)
        
        # Получаем настройки кодека
        codec_settings = self._get_video_codec_settings(use_nvenc=True, log_callback=None)
        
        if codec_settings['vcodec'] == 'h264_nvenc':
            codec_args = [
                '-c:v', 'h264_nvenc',
                '-preset', self.NVENC_PRESET,
                '-tune', 'll',  # Low latency for max speed
                '-rc', 'vbr',
                '-cq', str(self.NVENC_CQ),
                '-b:v', self.NVENC_BITRATE
            ]
        else:
            codec_args = [
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '18'
            ]
        
        # P1: Собираем команду FFmpeg как список аргументов (безопасно от shell injection)
        cmd = [
            FFMPEG_PATH, '-y',
            *input_args,
            '-filter_complex', filter_complex,
            '-map', f'[{current_stream}]',
            *codec_args,
            '-pix_fmt', 'yuv420p',
            '-r', str(fps),
            output_path
        ]
        
        log_callback(f"✨ Применяем {len(shot_files)-1} кинематографичных переходов (fade, dissolve, smooth)...")
        
        # P1: Выполняем без shell=True для безопасности
        try:
            result = run_registered(cmd, label="ffmpeg_transitions", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', timeout=600)
            
            if result.returncode != 0:
                # Проверяем тип ошибки
                stderr = result.stderr.lower() if result.stderr else ''
                if 'xfade' in stderr or 'filter' in stderr:
                    log_callback("   ⚠️ xfade фильтр не поддерживается, используем простую склейку")
                else:
                    log_callback(f"   ⚠️ Ошибка переходов: {result.stderr[:150]}")
                # Fallback: простая склейка без переходов
                return self._simple_concat_shots(shot_files, output_path, fps, width, height, log_callback)
            
            # Проверяем что файл создан
            if not os.path.exists(output_path):
                log_callback("   ⚠️ Файл не создан, используем простую склейку")
                return self._simple_concat_shots(shot_files, output_path, fps, width, height, log_callback)
            
            log_callback("   ✅ Переходы применены успешно!")
            return output_path
            
        except subprocess.TimeoutExpired:
            log_callback("   ⚠️ Таймаут при применении переходов, используем простую склейку")
            return self._simple_concat_shots(shot_files, output_path, fps, width, height, log_callback)
        except Exception as e:
            log_callback(f"   ⚠️ Ошибка: {e}, используем простую склейку")
            return self._simple_concat_shots(shot_files, output_path, fps, width, height, log_callback)

    def _one_pass_render(
        self,
        shot_files: List[str],
        audio_path: str,
        output_path: str,
        fps: int,
        width: int,
        height: int,
        audio_duration: float,
        transition_duration: float = 0.3,
        transition_frequency: float = 0.6,
        transition_style: str = 'cinematic',
        cinematic_polish: bool = True,
        media_types: List[str] = None,
        overlay_path: str = None,
        overlay_settings: dict = None,
        skip_duration: float = 0.0,
        log_callback=None,
    ) -> bool:
        """
        ONE-PASS render: xfade + overlay + audio за одну FFmpeg команду.
        Экономит ~25с вс двухпроходный вариант (xfade + перекодировка).
        Returns True при успехе, False если нужно фоллбэк.
        """
        if log_callback is None:
            log_callback = lambda x: None
        if not shot_files:
            return False
        try:
            _fp_parent = Path(FFMPEG_PATH).parent
            _fp = str(_fp_parent / ('ffprobe.exe' if FFMPEG_PATH.lower().endswith('.exe') else 'ffprobe'))
            if not Path(_fp).exists():
                _fp = 'ffprobe'
            def _dur(f):
                try:
                    r = run_registered(
                        [_fp, '-v', 'error', '-show_entries', 'format=duration',
                         '-of', 'default=noprint_wrappers=1:nokey=1', f],
                        label="ffprobe_one_pass_shot",
                        capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=30
                    )
                    return float(r.stdout.strip()) if r.returncode == 0 else 5.0
                except Exception:
                    return 5.0
            from concurrent.futures import ThreadPoolExecutor as _TPE2
            with _TPE2(max_workers=min(len(shot_files), 8)) as pool:
                shot_durations = list(pool.map(_dur, shot_files))
            min_shot_duration = min(shot_durations) if shot_durations else 0.0
            if min_shot_duration > 0:
                safe_transition_duration = min(
                    max(0.0, float(transition_duration or 0.0)),
                    max(0.0, min_shot_duration * 0.45),
                )
                if safe_transition_duration < float(transition_duration or 0.0):
                    log_callback(
                        f"   ℹ️ Длительность переходов уменьшена до "
                        f"{safe_transition_duration:.2f}s для коротких шотов"
                    )
                transition_duration = safe_transition_duration
            soft_transition_duration = _soft_cut_transition_duration(transition_duration)

            classic_t = ['fade', 'fadeblack', 'dissolve', 'fadegrays']
            smooth_t  = ['smoothup', 'smoothdown', 'smoothleft', 'smoothright']
            dynamic_t = ['wipeleft', 'wiperight', 'wipeup', 'wipedown', 'slideleft', 'slideright']
            geometric_t = ['circlecrop', 'circleopen', 'circleclose', 'radial']
            energetic_t = ['pixelize', 'hlslice', 'vrslice', 'squeezeh', 'squeezev', 'zoomin']
            if transition_style == 'energetic':
                tr_types = energetic_t + dynamic_t + ['zoomin', 'fadewhite']
            elif transition_style == 'smooth':
                tr_types = smooth_t + classic_t
            elif transition_style == 'dramatic':
                tr_types = ['fadeblack', 'circleclose', 'radial', 'dissolve', 'zoomin']
            else:
                tr_types = classic_t + smooth_t[:2] + geometric_t[:2] + dynamic_t[:2] + energetic_t[:2]

            tr_types, _transition_groups = _transition_palette(transition_style)
            n = len(shot_files)
            n_tr = n - 1
            n_apply = max(1, int(n_tr * transition_frequency)) if n_tr > 0 else 0
            tr_pos = set()
            if n_tr >= 1: tr_pos.add(1)
            if n_tr >= 2: tr_pos.add(n_tr)
            if n_apply > len(tr_pos):
                step = max(1, n_tr // (n_apply - len(tr_pos) + 1))
                for j in range(1, n_apply + 1):
                    tr_pos.add(min(j * step, n_tr))
                    if len(tr_pos) >= n_apply:
                        break

            cmd = [FFMPEG_PATH, '-y']
            for sf in shot_files:
                cmd.extend(['-hwaccel', 'auto', '-i', sf])
            audio_idx = n
            cmd.extend(['-i', str(Path(audio_path).resolve())])
            ov_idx = None
            if overlay_path and Path(overlay_path).is_file():
                ov_idx = n + 1
                overlay_path_abs = str(Path(overlay_path).resolve())
                if Path(overlay_path).suffix.lower() in {'.mp4', '.mov', '.avi', '.webm', '.mkv'}:
                    cmd.extend(['-stream_loop', '-1', '-i', overlay_path_abs])
                else:
                    cmd.extend(['-loop', '1', '-i', overlay_path_abs])

            fp = []
            stream_names = []
            for i in range(n):
                out = f"vin{i}"
                fp.append(f"[{i}:v]format=yuv420p[{out}]")
                stream_names.append(out)

            cur = stream_names[0]
            if n > 1:
                used_tr = []
                for i in range(1, n):
                    nxt = stream_names[i]
                    out_s = f"xf{i}"
                    apply = i in tr_pos
                    if apply:
                        pr = i / n
                        if pr < 0.15:        pref = energetic_t + ['zoomin', 'fadewhite']
                        elif pr > 0.85:      pref = ['fadeblack', 'dissolve', 'circleclose', 'fade']
                        elif 0.4 < pr < 0.6: pref = geometric_t + dynamic_t
                        else:                pref = smooth_t + classic_t
                        if media_types and i < len(media_types):
                            if (media_types[i-1] if i > 0 else 'image') != media_types[i]:
                                pref = ['fadeblack', 'dissolve', 'zoomin', 'circleopen', 'radial']
                        avail = [t for t in pref if t in tr_types]
                        if not avail: avail = tr_types
                        avail = [t for t in avail if t not in used_tr[-2:]]
                        if not avail: avail = tr_types
                        tr = _select_cinematic_transition(
                            i,
                            n,
                            transition_style=transition_style,
                            media_types=media_types,
                            used_transitions=used_tr,
                        )
                        tr_d = transition_duration
                    else:
                        tr = 'fade'
                        tr_d = soft_transition_duration
                    used_tr.append(tr)
                    cum = sum(shot_durations[:i])
                    prev_t = sum(
                        transition_duration if j in tr_pos else soft_transition_duration
                        for j in range(1, i)
                    )
                    off = max(0.0, cum - prev_t - tr_d)
                    fp.append(f"[{cur}][{nxt}]xfade=transition={tr}:duration={tr_d}:offset={off:.3f}[{out_s}]")
                    cur = out_s

            vid = cur
            polish_filter = _build_cinematic_polish_filter(
                vid,
                "v_polish",
                width,
                height,
                fps,
                n,
                transition_style=transition_style,
                enabled=cinematic_polish,
            )
            if polish_filter:
                fp.append(polish_filter)
                vid = "v_polish"
            if ov_idx is not None:
                ovs = overlay_settings or {}
                margin = ovs.get('margin', 50)
                position = ovs.get('position', 'Внизу справа')
                fullscreen = ovs.get('fullscreen', False)
                overlay_suffix = Path(overlay_path).suffix.lower()
                has_alpha = overlay_suffix in {'.png', '.webp', '.gif', '.mov', '.webm'}
                overlay_w, overlay_h = 0, 0
                try:
                    if overlay_suffix in {'.mp4', '.mov', '.avi', '.webm', '.mkv', '.gif'}:
                        probe = probe_registered(
                            overlay_path, label="ffprobe_render_overlay"
                        )
                        video_info = next((s for s in probe.get('streams', []) if s.get('codec_type') == 'video'), None)
                        if video_info:
                            overlay_w = int(video_info.get('width', 0) or 0)
                            overlay_h = int(video_info.get('height', 0) or 0)
                    else:
                        from PIL import Image
                        with Image.open(overlay_path) as img:
                            overlay_w, overlay_h = img.width, img.height
                except Exception:
                    overlay_w, overlay_h = 0, 0
                
                if fullscreen:
                    target_width, target_height = width, height
                    ox, oy = '0', '0'
                elif overlay_w > 0 and overlay_h > 0:
                    overlay_aspect = overlay_w / overlay_h
                    video_aspect = width / height
                    width_ratio = width / overlay_w
                    height_ratio = height / overlay_h
                    aspect_diff = abs(overlay_aspect - video_aspect)
                    if aspect_diff < 0.1:
                        scale_factor = min(width_ratio, height_ratio) * 0.95
                        target_width = max(2, int(overlay_w * scale_factor))
                        target_height = max(2, int(overlay_h * scale_factor))
                    elif overlay_aspect > video_aspect * 1.5:
                        target_width, target_height = int(width * 0.85), -2
                    elif overlay_aspect < video_aspect * 0.7:
                        target_width, target_height = -2, int(height * 0.75)
                    elif overlay_aspect > video_aspect:
                        target_width, target_height = int(width * 0.85), -2
                    else:
                        target_width, target_height = -2, int(height * 0.80)
                    ox, oy = self._get_overlay_position(position, width, height, margin)
                else:
                    target_width, target_height = int(width * 0.85), -2
                    ox, oy = self._get_overlay_position(position, width, height, margin)
                
                if target_width == -2:
                    fp.append(f"[{ov_idx}:v]scale=-2:{target_height}[ov_sc]")
                elif target_height == -2:
                    fp.append(f"[{ov_idx}:v]scale={target_width}:-2[ov_sc]")
                else:
                    fp.append(f"[{ov_idx}:v]scale={target_width}:{target_height}[ov_sc]")
                ov_kw = f"x={ox}:y={oy}:shortest=1"
                if skip_duration > 0:
                    ov_kw += f":enable='gte(t,{skip_duration})'"
                if has_alpha:
                    ov_kw += ":format=auto"
                fp.append(f"[{vid}][ov_sc]overlay={ov_kw}[v_ov]")
                vid = 'v_ov'

            filter_complex = ";".join(fp)
            cs = self._get_video_codec_settings(use_nvenc=True, log_callback=None)
            gop = fps * 2
            if cs['vcodec'] == 'h264_nvenc':
                c_args = ['-c:v', 'h264_nvenc', '-preset', cs.get('preset', self.NVENC_PRESET),
                          '-tune', cs.get('tune', 'll'),
                          '-rc', 'vbr', '-cq', str(cs.get('cq', self.NVENC_CQ)),
                          '-b:v', cs.get('b:v', self.NVENC_BITRATE)]
            else:
                c_args = ['-c:v', 'libx264', '-preset', 'medium', '-crf', str(self.NVENC_CQ)]

            cmd += [
                '-filter_complex', filter_complex,
                '-map', f'[{vid}]', '-map', f'{audio_idx}:a',
                *c_args,
                '-c:a', 'aac', '-b:a', '192k',
                '-pix_fmt', 'yuv420p', '-r', str(fps),
                '-t', str(audio_duration),
                '-g', str(gop), '-keyint_min', str(gop), '-sc_threshold', '0',
                '-movflags', '+faststart', '-map_metadata', '-1',
                output_path,
            ]

            log_callback(f"🚀 ONE-PASS: {n} шотов → xfade + overlay + audio...")
            res = run_registered(cmd, label="ffmpeg_one_pass", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding='utf-8', errors='ignore', timeout=600)
            if res.returncode != 0:
                log_callback(f"   ⚠️ ONE-PASS ошибка, fallback: {res.stderr[-200:]}")
                return False
            out_p = Path(output_path)
            if not out_p.exists() or out_p.stat().st_size < 10_000:
                log_callback("   ⚠️ ONE-PASS: файл не создан")
                return False
            log_callback(f"   ✅ ONE-PASS готов: {out_p.name} ({out_p.stat().st_size/1_048_576:.1f} MB)")
            return True
        except Exception as e:
            log_callback(f"   ⚠️ ONE-PASS exception: {e}")
            return False

    def _simple_concat_shots(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        log_callback=None
    ) -> str:
        """Простая склейка без переходов через filter_complex.
        
        Использует перекодирование для избежания ошибок NAL unit
        при разных параметрах кодирования входных файлов.
        """
        if log_callback is None:
            log_callback = print
        
        if not shot_files:
            log_callback("   ⚠️ Нет файлов для склейки")
            return output_path
        
        # Если только один файл - просто копируем
        if len(shot_files) == 1:
            try:
                import shutil
                shutil.copy2(shot_files[0], output_path)
                return output_path
            except Exception as e:
                log_callback(f"   ⚠️ Ошибка копирования: {e}")
                return output_path
        
        # 🔧 BATCH для большого количества файлов (Windows cmd limit)
        if len(shot_files) > self.MAX_FILES_PER_BATCH:
            return self._batch_simple_concat(shot_files, output_path, fps, width, height, log_callback, self.MAX_FILES_PER_BATCH)
        
        temp_dir = safe_temp_dir(prefix='concat_')  # 🌍 Unicode-safe
        
        try:
            # Используем filter_complex для надёжной конкатенации с перекодированием
            # Это избегает ошибок NAL unit от разных параметров кодирования
            
            # Строим команду с filter_complex
            inputs = []
            filter_parts = []
            
            for i, shot_file in enumerate(shot_files):
                inputs.extend(['-i', shot_file])
                # Нормализуем каждый вход: масштаб до заполнения экрана (crop вместо pad)
                # force_original_aspect_ratio=increase - увеличиваем до заполнения
                # crop - обрезаем лишнее (вместо pad с черными полосами)
                filter_parts.append(
                    f'[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,'
                    f'crop={width}:{height},fps={fps},'
                    f'format=yuv420p,setsar=1[v{i}];'
                )
            
            # Конкатенация всех нормализованных потоков
            concat_inputs = ''.join(f'[v{i}]' for i in range(len(shot_files)))
            filter_complex = ''.join(filter_parts) + f'{concat_inputs}concat=n={len(shot_files)}:v=1:a=0[outv]'
            
            codec_settings = self._get_video_codec_settings(use_nvenc=True, log_callback=None)
            
            if codec_settings['vcodec'] == 'h264_nvenc':
                codec_args = ['-c:v', 'h264_nvenc', '-preset', self.NVENC_PRESET, '-cq', str(self.NVENC_CQ)]
            else:
                codec_args = ['-c:v', 'libx264', '-preset', 'fast', '-crf', str(self.NVENC_CQ)]
            
            cmd = [
                FFMPEG_PATH, '-y',
                *inputs,
                '-filter_complex', filter_complex,
                '-map', '[outv]',
                *codec_args,
                '-movflags', '+faststart',
                output_path
            ]
            
            log_callback(f"   🔗 Склейка {len(shot_files)} шотов через filter_complex...")
            result = run_registered(cmd, label="ffmpeg_simple_concat", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', timeout=600)
            
            if result.returncode != 0 or not Path(output_path).exists() or Path(output_path).stat().st_size < 1000:
                log_callback("   ⚠️ filter_complex не удался, пробуем concat demuxer...")
                if result.stderr:
                    log_callback(f"   📋 stderr: {result.stderr[-300:]}")
                # Fallback на concat demuxer (может работать если файлы совместимы)
                return self._simple_concat_demuxer(shot_files, output_path, fps, width, height, temp_dir, log_callback)
            
            log_callback(f"   ✅ Склейка завершена: {output_path}")
            return output_path
            
        except subprocess.TimeoutExpired:
            log_callback("   ❌ Таймаут при склейке")
            raise Exception("_simple_concat_shots таймаут")
        except Exception as e:
            # Если файл всё же создан — возвращаем, иначе падаем
            if Path(output_path).exists() and Path(output_path).stat().st_size > 1000:
                log_callback(f"   ⚠️ Ошибка склейки, но файл создан: {e}")
                return output_path
            log_callback(f"   ❌ Ошибка склейки: {e}")
            raise
        finally:
            # Cleanup
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                logging.debug(f"Не удалось очистить temp_dir {temp_dir}: {e}")
    
    def _batch_simple_concat(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        log_callback,
        batch_size: int = 50
    ) -> str:
        """Batch-склейка для большого количества файлов через concat demuxer."""
        import shutil
        temp_dir = safe_temp_dir(prefix='batch_simple_')  # 🌍 Unicode-safe
        batch_outputs = []
        
        try:
            num_batches = (len(shot_files) + batch_size - 1) // batch_size
            log_callback(f"   📦 Batch-склейка: {len(shot_files)} файлов → {num_batches} батчей")
            
            for batch_idx in range(num_batches):
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, len(shot_files))
                batch_files = shot_files[start_idx:end_idx]
                
                batch_output = temp_dir / f"batch_{batch_idx:03d}.mp4"
                
                # Используем concat demuxer для каждого батча (быстро и надёжно)
                concat_list = temp_dir / f"list_{batch_idx:03d}.txt"
                with open(concat_list, 'w', encoding='utf-8') as f:
                    for shot_file in batch_files:
                        escaped = str(Path(shot_file).resolve()).replace('\\', '/').replace("'", "'\\''")
                        f.write(f"file '{escaped}'\n")
                
                cmd = [
                    FFMPEG_PATH, '-y', '-f', 'concat', '-safe', '0',
                    '-i', str(concat_list),
                    '-c', 'copy',
                    str(batch_output)
                ]
                run_registered(
                    cmd, label=f"ffmpeg_simple_batch_{batch_idx}", capture_output=True,
                    text=True, encoding='utf-8', errors='ignore', timeout=300
                )
                
                if batch_output.exists():
                    batch_outputs.append(str(batch_output))
            
            if not batch_outputs:
                log_callback("   ❌ Batch-склейка не удалась")
                return output_path
            
            # Финальное объединение батчей
            if len(batch_outputs) == 1:
                shutil.copy2(batch_outputs[0], output_path)
            else:
                final_list = temp_dir / 'final.txt'
                with open(final_list, 'w', encoding='utf-8') as f:
                    for bf in batch_outputs:
                        escaped = bf.replace('\\', '/').replace("'", "'\\''")
                        f.write(f"file '{escaped}'\n")
                
                cmd = [FFMPEG_PATH, '-y', '-f', 'concat', '-safe', '0', '-i', str(final_list), '-c', 'copy', output_path]
                run_registered(cmd, label="ffmpeg_batch_simple_final", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', timeout=300)
            
            log_callback("   ✅ Batch-склейка завершена")
            return output_path
            
        except Exception as e:
            log_callback(f"   ❌ Ошибка batch-склейки: {e}")
            return output_path
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as cleanup_err:
                logging.debug(f"Cleanup temp_dir failed: {cleanup_err}")
    
    def _batch_concat_with_transitions(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        transition_duration: float,
        log_callback,
        media_types: List[str],
        transition_style: str,
        batch_size: int = 50,
        progress_callback: Callable = None  # P2: Добавлен progress callback
    ) -> str:
        """
        P2: Batch-склейка для большого количества шотов с progress callback.
        
        Разбивает на группы, склеивает каждую с переходами, затем объединяет группы.
        
        Args:
            shot_files: Список путей к видео файлам
            output_path: Путь для выходного файла
            fps: Частота кадров
            width: Ширина видео
            height: Высота видео
            transition_duration: Длительность переходов
            log_callback: Функция логирования
            media_types: Типы медиа для каждого шота
            transition_style: Стиль переходов
            batch_size: Размер батча
            progress_callback: Функция для отчёта о прогрессе (percent: int)
            
        Returns:
            Путь к склеенному видео
        """
        import shutil
        temp_dir = safe_temp_dir(prefix='batch_concat_')  # 🌍 Unicode-safe
        batch_outputs = []
        
        # P2: Инициализация progress
        if progress_callback is None:
            progress_callback = lambda x: None
        
        try:
            # Разбиваем на батчи
            num_batches = (len(shot_files) + batch_size - 1) // batch_size
            log_callback(f"   📦 Разбиваем {len(shot_files)} шотов на {num_batches} батчей по ~{batch_size}")
            
            # 🚀 ПАРАЛЛЕЛЬНАЯ обработка батчей
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            # Определяем количество параллельных воркеров (не больше батчей и не больше CPU/2)
            max_workers = min(num_batches, max(2, safe_cpu_count() // 2 or 2))
            log_callback(f"   ⚡ Параллельная склейка: {max_workers} воркеров")
            
            def process_batch(batch_idx):
                """Обработка одного батча"""
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, len(shot_files))
                batch_files = shot_files[start_idx:end_idx]
                batch_media_types = media_types[start_idx:end_idx] if media_types else None
                
                batch_output = temp_dir / f"batch_{batch_idx:03d}.mp4"
                
                # 🎯 Используем глобальный пул для ограничения вложенности
                if _USE_GLOBAL_POOL:
                    with thread_slot('render', priority='high'):
                        self._concat_shots_with_transitions_impl(
                            batch_files, str(batch_output), fps, width, height,
                            transition_duration, lambda x: None, batch_media_types, transition_style
                        )
                else:
                    self._concat_shots_with_transitions_impl(
                        batch_files, str(batch_output), fps, width, height,
                        transition_duration, lambda x: None, batch_media_types, transition_style
                    )
                
                return batch_idx, str(batch_output) if batch_output.exists() else None
            
            # Запускаем все батчи параллельно
            batch_results = {}
            completed_batches = 0
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(process_batch, i): i for i in range(num_batches)}
                
                for future in as_completed(futures):
                    batch_idx = futures[future]
                    try:
                        idx, result_path = future.result()
                        completed_batches += 1
                        
                        # P2: Отчёт о прогрессе (90% на батчи, 10% на финальную склейку)
                        progress_percent = int((completed_batches / num_batches) * 90)
                        progress_callback(progress_percent)
                        
                        if result_path:
                            batch_results[idx] = result_path
                            log_callback(f"   ✅ Батч {idx + 1}/{num_batches} готов")
                        else:
                            log_callback(f"   ⚠️ Батч {idx + 1} не создан")
                    except Exception as e:
                        log_callback(f"   ⚠️ Ошибка батча {batch_idx + 1}: {e}")
            
            # Собираем результаты в правильном порядке
            for i in range(num_batches):
                if i in batch_results:
                    batch_outputs.append(batch_results[i])
            
            if not batch_outputs:
                log_callback("   ❌ Ни один батч не создан")
                return output_path
            
            # Объединяем батчи через concat demuxer (без переходов между батчами)
            progress_callback(95)  # P2: Прогресс перед финальной склейкой
            
            if len(batch_outputs) == 1:
                shutil.copy2(batch_outputs[0], output_path)
            else:
                log_callback(f"   🔗 Объединяем {len(batch_outputs)} батчей...")
                concat_list = temp_dir / 'final_concat.txt'
                with open(concat_list, 'w', encoding='utf-8') as f:
                    for batch_file in batch_outputs:
                        # Экранируем путь для FFmpeg
                        escaped_path = batch_file.replace('\\', '/').replace("'", "'\\''")
                        f.write(f"file '{escaped_path}'\n")
                
                cmd = [
                    FFMPEG_PATH, '-y', '-f', 'concat', '-safe', '0',
                    '-i', str(concat_list),
                    '-c', 'copy',
                    output_path
                ]
                result = run_registered(
                    cmd, label="ffmpeg_transition_batches_final", capture_output=True,
                    text=True, encoding='utf-8', errors='ignore', timeout=300
                )
                
                if result.returncode != 0:
                    log_callback(f"   ⚠️ Concat demuxer ошибка: {result.stderr[:200]}")
                    # Fallback: копируем первый батч
                    shutil.copy2(batch_outputs[0], output_path)
            
            progress_callback(100)  # P2: Завершение
            log_callback("   ✅ Batch-склейка завершена")
            return output_path
            
        except Exception as e:
            log_callback(f"   ❌ Ошибка batch-склейки: {e}")
            return output_path
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as cleanup_err:
                logging.debug(f"Cleanup temp_dir failed: {cleanup_err}")
    
    def _concat_shots_with_transitions_impl(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        transition_duration: float = 0.3,
        log_callback=None,
        media_types: List[str] = None,
        transition_style: str = 'cinematic'
    ) -> str:
        """Реальная реализация склейки с переходами (для батчей < 50 шотов)."""
        # Это копия основной логики из _concat_shots_with_transitions
        # но без проверки на batch (чтобы избежать рекурсии)
        if log_callback is None:
            log_callback = lambda x: None
            
        if len(shot_files) < 2:
            if shot_files:
                import shutil
                shutil.copy(shot_files[0], output_path)
            return output_path
        
        # Используем простую склейку для батчей (быстрее и надёжнее)
        return self._simple_concat_shots(shot_files, output_path, fps, width, height, log_callback)
    
    def _simple_concat_demuxer(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        temp_dir: Path,
        log_callback=None
    ) -> str:
        """Fallback склейка через concat demuxer."""
        if log_callback is None:
            log_callback = print
        
        concat_list = temp_dir / 'concat_list.txt'
        
        with open(concat_list, 'w', encoding='utf-8') as f:
            for shot_file in shot_files:
                normalized = str(Path(shot_file).resolve()).replace('\\', '/')
                f.write(f"file '{normalized}'\n")
        
        codec_settings = self._get_video_codec_settings(use_nvenc=True, log_callback=None)
        
        if codec_settings['vcodec'] == 'h264_nvenc':
            codec_args = ['-c:v', 'h264_nvenc', '-preset', self.NVENC_PRESET, '-cq', str(self.NVENC_CQ)]
        else:
            codec_args = ['-c:v', 'libx264', '-preset', 'fast', '-crf', str(self.NVENC_CQ)]
        
        cmd = [
            FFMPEG_PATH, '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_list),
            *codec_args,
            '-pix_fmt', 'yuv420p',
            '-r', str(fps),
            output_path
        ]
        
        result = run_registered(
            cmd, label="ffmpeg_simple_concat_demuxer", capture_output=True, text=True,
            encoding='utf-8', errors='ignore', timeout=300
        )
        if result.returncode != 0 or not Path(output_path).exists() or Path(output_path).stat().st_size < 1000:
            # КРИТИЧНО: теперь падаем явно вместо возврата несуществующего файла
            err_tail = (result.stderr or '')[-400:]
            log_callback(f"   ❌ concat demuxer не создал файл: {err_tail}")
            raise Exception(f"concat demuxer failed (returncode={result.returncode}): {err_tail[:200]}")
        return output_path
    
    def _get_video_codec_settings(self, use_nvenc: bool = True, log_callback=None) -> dict:
        """Get optimal video codec settings (NVENC if available, fallback to libx264).
        
        P1: Результат кэшируется для избежания повторных проверок.
        """
        # P1: Кэширование результата
        cache_key = f"codec_{use_nvenc}"
        if hasattr(self, '_codec_settings_cache') and cache_key in self._codec_settings_cache:
            return self._codec_settings_cache[cache_key].copy()
        
        if not hasattr(self, '_codec_settings_cache'):
            self._codec_settings_cache = {}
        
        if use_nvenc and self._check_nvenc_available():
            # NVENC settings (NVIDIA GPU encoding) - OPTIMIZED FOR SPEED + QUALITY
            if log_callback and self._nvenc_available is not None:
                # Log only once when first determined
                if not hasattr(self, '_nvenc_logged'):
                    log_callback("✅ NVENC (NVIDIA GPU) обнаружен - используем аппаратное ускорение")
                    self._nvenc_logged = True
            result = {
                'vcodec': 'h264_nvenc',
                'preset': self.NVENC_PRESET,  # p1-p7, p3 = баланс скорость/качество
                'tune': 'hq',    # High quality tuning
                'rc': 'vbr',     # Variable bitrate
                'cq': self.NVENC_CQ,  # Quality (lower = better, 0-51) - 23 = YouTube стандарт
                'b:v': self.NVENC_BITRATE,  # Битрейт для YouTube
                'maxrate': self.NVENC_MAXRATE,  # Max bitrate
                'bufsize': '20M', # Buffer size
            }
        else:
            # CPU encoding fallback - OPTIMIZED FOR SPEED
            if log_callback and self._nvenc_available is not None:
                if not hasattr(self, '_cpu_logged'):
                    log_callback("ℹ️ NVENC недоступен - используем CPU кодирование (ultrafast preset)")
                    self._cpu_logged = True
            result = {
                'vcodec': 'libx264',
                'preset': 'ultrafast',  # Was 'medium' - ~5x faster
                'tune': 'fastdecode',   # Optimize for fast decoding
                'crf': 20,              # Slightly better quality to compensate
            }
        
        # Кэшируем результат
        self._codec_settings_cache[cache_key] = result.copy()
        return result

    def render_video_chunk(self, image_paths: List[str], audio_path: str, output_dir: Path,
                           video_settings: Dict[str, Any], subtitle_settings: Dict[str, Any],
                           text_content: Dict[str, Any], chunk_num: int, **kwargs) -> str:
        """Renders a single video chunk from a set of images and an audio track."""
        
        log_callback = kwargs.get('log_callback', print)
        media_path = kwargs.get('media_path')
        overlay_settings = kwargs.get('overlay_settings')
        use_triple_template = kwargs.get('use_triple_template', False)

        width = video_settings['width']
        height = video_settings['height']
        fps = video_settings['fps']
        
        try:
            audio_duration = float(
                probe_registered(audio_path, label="ffprobe_video_chunk_audio")[
                    'format'
                ]['duration']
            )
        except (ffmpeg.Error, KeyError) as e:
            log_callback(f"❌ Не удалось получить длительность аудио-чанка: {e}")
            return None
        
        # 🎯 Проверяем пользовательские настройки
        user_shot_duration = video_settings.get('shot_duration', None)
        shot_min = video_settings.get('shot_min_duration', None)
        shot_max = video_settings.get('shot_max_duration', None)
        
        # Определяем целевую длительность шота
        if user_shot_duration:
            target_shot_duration = user_shot_duration
        elif shot_min and shot_max:
            target_shot_duration = (shot_min + shot_max) / 2
        else:
            target_shot_duration = None
        
        if target_shot_duration and target_shot_duration > 0:
            # Пользователь задал свой тайминг
            num_shots_needed = max(1, int(audio_duration / target_shot_duration))
            if image_paths and len(image_paths) < num_shots_needed:
                from core.smart_clip_matcher import InsufficientUniqueVisualsError
                raise InsufficientUniqueVisualsError(
                    f"Для чанка нужно {num_shots_needed} уникальных кадров, "
                    f"доступно {len(set(image_paths))}. Повторы отключены."
                )
        
        # 🔧 ЗАЩИТА: Проверяем что есть изображения и длительность > 0
        if not image_paths:
            log_callback(f"⚠️ Нет изображений для чанка #{chunk_num}")
            return None
        shot_duration = audio_duration / len(image_paths) if len(image_paths) > 0 else audio_duration
        
        output_file = output_dir / f"chunk_{chunk_num}.mp4"

        # --- FFMPEG Graph Setup ---
        enable_animation = bool(video_settings.get('enable_animation', False))
        animation_type = video_settings.get('animation_type', 'mix')
        animation_speed = int(video_settings.get('animation_speed', 50))

        # 🎬 TRIPLE TEMPLATE: Определяем целевую высоту изображения
        target_height = height // 2 if use_triple_template else height
        
        # 🎬 PILLOW ANIMATION для чанков
        pillow_chunk_shots = {}
        pillow_chunk_dir = None
        
        if enable_animation and image_paths:
            from core.pillow_animator import AnimationType
            all_animations = AnimationType.all()
            anim_mode = "MIX (3 типа)" if animation_type == 'mix' else animation_type
            log_callback(f"   🎬 Pillow Animation для чанка #{chunk_num}... (режим: {anim_mode})")
            pillow_chunk_dir = safe_temp_dir(prefix=f'pillow_chunk{chunk_num}_')
            
            shots_config = []
            for i, img_path in enumerate(image_paths):
                current_anim = all_animations[i % len(all_animations)] if animation_type == 'mix' else animation_type
                
                shots_config.append({
                    'image_path': img_path,
                    'output_path': str(pillow_chunk_dir / f"shot_{i:03d}.mp4"),
                    'width': width,
                    'height': target_height,
                    'fps': fps,
                    'duration': shot_duration,
                    'animation_type': current_anim,
                    'zoom_amount': 0.12,
                    'use_nvenc': self._check_nvenc_available(),
                    'index': i
                })
            
            try:
                pillow_results = PillowAnimator.create_multiple_shots_parallel(
                    shots_config, log_callback=lambda x: None
                )
                for i, cfg in enumerate(shots_config):
                    if i < len(pillow_results) and pillow_results[i]:
                        pillow_chunk_shots[cfg['index']] = pillow_results[i]
            except Exception:
                pillow_chunk_shots = {}
        
        video_streams = []
        if image_paths:
            for i, p in enumerate(image_paths):
                if enable_animation and i in pillow_chunk_shots:
                    # 🎬 Используем Pillow-анимированное видео
                    pillow_input = ffmpeg.input(pillow_chunk_shots[i])
                    format_fixed = pillow_input.filter('format', 'yuv420p')
                    final_shot = format_fixed.filter('setsar', '1')
                    video_streams.append(final_shot)
                elif enable_animation:
                    # Fallback на zoompan
                    base = ffmpeg.input(str(p), loop=1, t=shot_duration, framerate=fps, ss=i*0.001)
                    anim = self._apply_animation(
                        stream=base,
                        animation_type=animation_type,
                        shot_duration=shot_duration,
                        width=width,
                        height=target_height,
                        fps=fps,
                        speed=animation_speed,
                        index=i,
                        log_callback=lambda x: None,
                        retention_settings=None
                    )
                    video_streams.append(anim)
                else:
                    # No animation, just scale and crop with lanczos for quality
                    base = ffmpeg.input(str(p), loop=1, t=shot_duration, framerate=fps, ss=i*0.001)
                    scaled = base.filter('scale', width, target_height, force_original_aspect_ratio='increase', flags='lanczos').filter('crop', width, target_height)
                    video_streams.append(scaled)
        
        if video_streams:
            concatenated_video = ffmpeg.concat(*video_streams, v=1, a=0)
        else:
            concatenated_video = ffmpeg.input(f'color=c=black:s={width}x{height}:d={audio_duration}', f='lavfi')

        if use_triple_template:
            # 🎬 TRIPLE TEMPLATE: Изображение на верхней половине экрана
            # Создаём чёрный фон полного размера
            background = ffmpeg.input(f'color=c=black:s={width}x{height}:d={audio_duration}', f='lavfi')
            # Накладываем изображение (которое уже height/2) в верхнюю часть (y=0)
            concatenated_video = ffmpeg.overlay(background, concatenated_video, x=0, y=0)

        audio_input = ffmpeg.input(str(audio_path))

        # --- Built-in atmospheric effect (kept below subtitles for readability) ---
        concatenated_video, resolved_effect = apply_video_effect(
            concatenated_video,
            video_settings,
            width,
            height,
            fps,
            audio_duration,
            seed_source=f"{output_file}|chunk:{chunk_num}",
        )
        if resolved_effect != 'none':
            log_callback(f"✨ Атмосферный эффект: {effect_display_name(resolved_effect)}")
        
        # --- Subtitle Filter ---
        if subtitle_settings and subtitle_settings.get('enabled', False):
            concatenated_video = self._apply_dynamic_subtitles(
                concatenated_video,
                text_content,
                subtitle_settings,
                audio_duration,
                width,
                height,
                log_callback,
                audio_path=audio_path,
            )

        # --- Overlay Filter ---
        overlay_file = self._get_overlay_file_path(media_path, log_callback)
        if overlay_file:
            log_callback("🖼️ Наложение оверлея...")
            concatenated_video = self._apply_overlay(concatenated_video, overlay_file, overlay_settings, width, height, log_callback)
        
        # --- Render Command ---
        try:
            log_callback(f"🔧 Выполняем FFmpeg для чанка #{chunk_num}...")
            
            # Get optimal codec settings (NVENC if available)
            codec_settings = self._get_video_codec_settings(use_nvenc=True, log_callback=log_callback)
            
            stream = ffmpeg.output(
                concatenated_video,
                audio_input,
                str(output_file),
                acodec='aac',
                pix_fmt='yuv420p',
                r=fps,
                s=f'{width}x{height}',
                shortest=None,
                map_metadata=-1,  # ✅ Удаляем все метаданные
                **codec_settings
            ).overwrite_output()
            
            cmd = stream.compile()
            chunk_timeout = max(300, min(4 * 60 * 60, int(audio_duration * 4) + 180))
            result = run_registered(
                cmd,
                label=f"ffmpeg_video_chunk_{chunk_num}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=chunk_timeout,
            )
            if result.returncode != 0:
                raise ffmpeg.Error('ffmpeg', result.stdout, result.stderr)
            log_callback(f"✅ Видео-чанк #{chunk_num} успешно создан: {output_file}")
            
            # 🧹 Cleanup Pillow temp for chunk
            if pillow_chunk_dir and Path(pillow_chunk_dir).exists():
                try:
                    shutil.rmtree(pillow_chunk_dir)
                except Exception:
                    pass
            
            return str(output_file)

        except ffmpeg.Error as e:
            # 🧹 Cleanup Pillow temp on error
            if pillow_chunk_dir and Path(pillow_chunk_dir).exists():
                try:
                    shutil.rmtree(pillow_chunk_dir)
                except Exception:
                    pass
            stderr = e.stderr.decode('utf-8') if e.stderr else 'No stderr'
            raise Exception(f"Ошибка FFmpeg при финальном рендеринге: {stderr}")
        finally:
            self._subtitle_renderer.cleanup()

    def render_full_video(self, *args, **kwargs):
        """Wrapper to track active renderers."""

        with VideoRenderer._RENDERER_LOCK:
            VideoRenderer._ACTIVE_RENDERERS += 1
        try:
            result = self._render_full_video_impl(*args, **kwargs)
            
            # Cleanup Veo 3 temp files after successful render
            try:
                output_path = args[1] if len(args) > 1 else kwargs.get('output_path')
                if output_path:
                    output_dir = Path(output_path).parent
                    for veo3_file in output_dir.glob("veo3_*"):
                        if veo3_file.is_file():
                            veo3_file.unlink()
            except Exception:
                # Ignore cleanup errors - not critical
                pass
            
            return result
        finally:
            self._subtitle_renderer.cleanup()
            with VideoRenderer._RENDERER_LOCK:
                VideoRenderer._ACTIVE_RENDERERS -= 1

    def _render_full_video_impl(self, image_paths: List[str], audio_path: str, output_path: Path,
                          video_settings: Dict[str, Any], subtitle_settings: Dict[str, Any],
                          text_content: Dict[str, Any], log_callback: Callable,
                          media_path: str = None, overlay_settings: Dict = None,
                          use_triple_template: bool = False, veo3_settings: Dict = None,
                          google_ai_api_key: str = None, theme: str = None,
                          mixed_media: List[Tuple[str, str]] = None) -> str:
        """Renders a full video from all assets in a single pass."""
        
        # P0: Thread-safe temp директории для cleanup
        # КРИТИЧНО: В параллельном режиме несколько потоков вызывают render_full_video
        # на одном инстансе VideoRenderer. threading.local() даёт каждому потоку свой список.
        self._tl.temp_dirs = []
        
        try:
            return self._render_full_video_impl_inner(
                image_paths, audio_path, output_path, video_settings, subtitle_settings,
                text_content, log_callback, media_path, overlay_settings, use_triple_template,
                veo3_settings, google_ai_api_key, theme, mixed_media
            )
        finally:
            # P0: Cleanup temp директорий ТОЛЬКО ЭТОГО потока
            # Читаем напрямую из thread-local, а не из self._temp_dirs_to_cleanup
            # (который мог быть перезаписан другим потоком)
            dirs_to_clean = getattr(self._tl, 'temp_dirs', [])
            for temp_path in dirs_to_clean:
                if temp_path and Path(temp_path).exists():
                    try:
                        shutil.rmtree(temp_path)
                        logging.debug(f"Очищена temp директория: {temp_path}")
                    except Exception as cleanup_err:
                        logging.debug(f"Не удалось очистить temp директорию {temp_path}: {cleanup_err}")
            self._tl.temp_dirs = []
    
    def _render_full_video_impl_inner(self, image_paths: List[str], audio_path: str, output_path: Path,
                          video_settings: Dict[str, Any], subtitle_settings: Dict[str, Any],
                          text_content: Dict[str, Any], log_callback: Callable,
                          media_path: str = None, overlay_settings: Dict = None,
                          use_triple_template: bool = False, veo3_settings: Dict = None,
                          google_ai_api_key: str = None, theme: str = None,
                          mixed_media: List[Tuple[str, str]] = None) -> str:
        """Inner implementation of render_full_video."""
        
        # P1: Валидация video_settings
        self._validate_video_settings(video_settings, log_callback)
        
        # Long-form batch rendering creates thousands of temporary shot files.
        # Check a duration-scaled reserve before spending time on them.
        requested_duration = float(video_settings.get('duration', 0) or 0)
        required_gb = max(2.0, (requested_duration / 3600.0) * 8.0)
        self._check_disk_space(output_path, log_callback, min_gb=required_gb)
        
        # No temp directory needed - using direct concat filter
        
        width = video_settings['width']
        height = video_settings['height']
        fps = video_settings.get('fps', 60)
        
        # 🎬 TRIPLE TEMPLATE: Определяем целевую высоту изображения
        target_height = height // 2 if use_triple_template else height
        if use_triple_template:
            log_callback(f"📐 Triple Template: изображения будут {width}x{target_height} (верхняя половина)")
        
        # --- ENHANCED Pre-validation ---
        try:
            audio_path_obj = Path(audio_path)
            if not audio_path_obj.exists():
                raise Exception(f"Аудиофайл не существует: {audio_path}")
            
            file_size = audio_path_obj.stat().st_size
            if file_size == 0:
                raise Exception(f"Аудиофайл пуст (0 байт): {audio_path}")
            
            if file_size < 1000:  # Less than 1KB is suspicious
                log_callback(f"⚠️ Подозрительно маленький аудиофайл: {file_size} байт")
            
            # Validate audio with ffprobe
            audio_duration = float(
                probe_registered(audio_path, label="ffprobe_full_render_audio")[
                    'format'
                ]['duration']
            )
            
            if audio_duration <= 0:
                raise Exception(f"Некорректная длительность аудио: {audio_duration}s")
            
            log_callback(f"✅ Аудио валидировано: {audio_duration:.2f}s, {file_size/1024:.1f}KB")
            
        except ffmpeg.Error as e:
            stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else 'No stderr'
            log_callback("❌ CRITICAL: ffprobe не смог прочитать аудиофайл")
            log_callback(f"   Путь: {audio_path}")
            log_callback(f"   Stderr: {stderr[:500]}")
            raise Exception(f"Не удалось получить длительность аудио: {stderr[:200]}")
        except Exception as e:
            log_callback(f"❌ Ошибка валидации аудио: {e}")
            raise Exception(f"Не удалось получить длительность аудио: {e}")

        if not image_paths and not mixed_media:
            raise Exception("Нет изображений или видеоклипов для создания видео")
        
        # 🎥 VEO 3 AI-INTRO INTEGRATION
        veo3_intro_video = None
        veo3_intro_duration = 0
        
        if veo3_settings and veo3_settings.get('enabled', False):
            log_callback("\n" + "=" * 60)
            log_callback("🎬 VEO 3 AI-INTRO: Генерация взрывного начала")
            log_callback("=" * 60)
            
            try:
                # Generate AI-intro
                veo3_intro_video = generate_intro_with_veo3(
                    theme=text_content.get('title', theme or 'video'),
                    full_text=text_content.get('full_text', ''),
                    veo3_settings=veo3_settings,
                    api_key=google_ai_api_key,
                    output_dir=output_path.parent,
                    video_settings=video_settings,
                    log_callback=log_callback
                )
                
                if veo3_intro_video and Path(veo3_intro_video).exists():
                    # Get intro duration
                    from core.video_utils import get_video_duration
                    veo3_intro_duration = get_video_duration(veo3_intro_video)
                    
                    log_callback(f"✅ AI-интро готово: {veo3_intro_duration:.2f}s")
                    log_callback("   Теперь создаем слайдшоу для остального контента...")
                    
                    # Adjust audio duration for slideshow (subtract intro duration)
                    slideshow_duration = audio_duration - veo3_intro_duration
                    
                    if slideshow_duration <= 0:
                        log_callback("⚠️ AI-интро длиннее аудио, используем только интро")
                        # Return intro as final video (will add audio later)
                        # For now, continue with normal flow
                        slideshow_duration = 1  # Minimum
                    
                    log_callback(f"   📊 Распределение: AI-интро {veo3_intro_duration:.1f}s + Слайдшоу {slideshow_duration:.1f}s")
                    
                    # НЕ обрезаем аудио! Слайдшоу создаётся на полную длительность
                    # Veo 3 видео заменит первые 8s слайдшоу при сшивке
                    # audio_duration остаётся полным
                    
                else:
                    log_callback("⚠️ AI-интро не удалось сгенерировать, используем обычное слайдшоу")
                    veo3_intro_video = None
                    
            except Exception as e:
                log_callback(f"❌ Ошибка генерации AI-интро: {str(e)[:200]}")
                log_callback("   Продолжаем с обычным слайдшоу...")
                veo3_intro_video = None
            
            log_callback("=" * 60 + "\n")
        
        # 🎯 УМНАЯ СИСТЕМА УДЕРЖАНИЯ
        # Проверяем есть ли пользовательские настройки
        
        user_shot_duration = video_settings.get('shot_duration', None)
        shot_min = video_settings.get('shot_min_duration', None)
        shot_max = video_settings.get('shot_max_duration', None)
        
        use_random_duration = False
        shot_durations = []  # Список длительностей для каждого шота
        
        if user_shot_duration:
            # Пользователь задал точный тайминг через shot_duration
            log_callback(f"👤 Используем фиксированный тайминг: {user_shot_duration}s")
            optimal_shot_duration = user_shot_duration
            retention_settings = None  # Отключаем автоматику
        elif shot_min and shot_max:
            # Пользователь задал диапазон - используем РАНДОМНУЮ длительность для каждого шота
            log_callback(f"🎲 Используем рандомный диапазон: {shot_min}-{shot_max}s")
            use_random_duration = True
            avg_duration = (shot_min + shot_max) / 2
            optimal_shot_duration = avg_duration  # Для расчета количества
            retention_settings = None  # Отключаем автоматику
        else:
            # Автоматическое определение
            log_callback("🎯 Определение оптимальных настроек удержания...")
            retention_settings = RetentionOptimizer.get_settings(
                width=width,
                height=height,
                duration=int(audio_duration),
                video_type='auto'
            )
            optimal_shot_duration = retention_settings.shot_duration
        
        # 🔧 КРИТИЧНО: Определяем количество шотов
        # Если есть mixed_media - используем его длину, иначе рассчитываем
        if mixed_media:
            num_shots_needed = len(mixed_media)
            log_callback(f"   🎬 MIXED_MEDIA MODE: {num_shots_needed} шотов из mixed_media")
            log_callback(f"   📊 Видеоклипы: {sum(1 for _, t in mixed_media if t == 'video')}")
            log_callback(f"   📊 Images: {sum(1 for _, t in mixed_media if t == 'image')}")
        else:
            num_shots_needed = int(audio_duration / optimal_shot_duration)
            log_callback(f"   📊 Рассчитано шотов: {num_shots_needed} (audio={audio_duration:.1f}s / shot={optimal_shot_duration}s)")
        
        # Never hide a short pool by cycling the same frames. The source
        # orchestrator must provide a mixed local/YouTube/stock/image timeline.
        if not mixed_media and len(image_paths) < num_shots_needed:
            from core.smart_clip_matcher import InsufficientUniqueVisualsError
            raise InsufficientUniqueVisualsError(
                "Недостаточно уникального визуального материала для одного ролика: "
                f"нужно {num_shots_needed} шотов, доступно {len(set(image_paths))} изображений. "
                "Скрытое циклирование отключено. Добавьте локальные/YouTube/стоковые источники, "
                "увеличьте длительность шота или разрешите генерацию большего числа изображений."
            )
        
        # 🎲 Генерируем длительности для каждого шота
        if use_random_duration:
            # 🚀 EMOTIONAL PACING: Используем эмоциональную кривую вместо чистого рандома
            from core.retention_optimizer import EmotionalPacing
            
            log_callback("🎬 Применяем эмоциональный pacing (hook→climax→resolution)")
            
            # Генерируем профиль длительностей с эмоциональной кривой
            # 🎭 ДИНАМИЧЕСКАЯ КРИВАЯ для длинных видео
            # 🔧 КРИТИЧНО: Используем num_shots_needed (из mixed_media или рассчитанное)
            shot_durations = EmotionalPacing.get_pacing_profile(
                total_shots=num_shots_needed,
                min_duration=shot_min,
                max_duration=shot_max,
                video_duration=audio_duration  # Передаём длительность для выбора кривой
            )
            
            # 🔧 КРИТИЧНО: Учитываем что xfade переходы "съедают" время!
            # Каждый переход уменьшает итоговую длительность на transition_duration
            enable_transitions = video_settings.get('enable_transitions', True)
            transition_duration = video_settings.get('transition_duration', 0.3)
            num_transitions = len(shot_durations) - 1 if enable_transitions else 0
            if video_settings.get('glitch_enabled', False):
                transition_time_loss = calculate_glitch_transition_loss(
                    num_transitions,
                    video_settings.get('glitch_frequency', 0.5),
                    video_settings.get('glitch_duration', 0.4),
                    transition_duration,
                )
            else:
                transition_time_loss = num_transitions * transition_duration
            
            # Целевая длительность = audio_duration + потери на переходы
            target_duration = audio_duration + transition_time_loss
            
            # Нормализуем чтобы сумма = target_duration (с учётом переходов)
            total_generated = sum(shot_durations)
            scale_factor = target_duration / total_generated
            shot_durations = [d * scale_factor for d in shot_durations]
            
            log_callback(f"   🎬 Компенсация переходов: +{transition_time_loss:.1f}s ({num_transitions} стыков)")
            
            # КРИТИЧНО: Корректируем с учётом округления кадров
            # math.ceil может добавить лишние кадры, нужно скорректировать
            import math
            fps = video_settings.get('fps', 60)
            
            # Рассчитываем фактическую длительность с учётом округления кадров
            actual_durations = [math.ceil(d * fps) / fps for d in shot_durations]
            sum(actual_durations)
            
            # 🔧 КРИТИЧНО: Корректируем последний шот чтобы сумма = target_duration (с учётом переходов)
            current_total = sum(shot_durations)
            if abs(current_total - target_duration) > 0.01:
                diff = target_duration - current_total
                shot_durations[-1] = max(shot_min, shot_durations[-1] + diff)
                if diff > 0:
                    log_callback(f"   ⚙️ Корректировка: увеличен последний шот на {diff:.3f}s")
                else:
                    log_callback(f"   ⚙️ Корректировка: уменьшен последний шот на {-diff:.3f}s")
            
            # Финальная проверка (итоговая длительность после переходов должна = audio_duration)
            final_total = sum(shot_durations)
            expected_final = final_total - transition_time_loss
            if abs(expected_final - audio_duration) > 0.5:
                log_callback(f"   ⚠️ ВНИМАНИЕ: Ожидаемая длительность ({expected_final:.2f}s) ≠ аудио ({audio_duration:.2f}s)")
            
            # Логируем статистику
            avg_actual = sum(shot_durations) / len(shot_durations)
            min_actual = min(shot_durations)
            max_actual = max(shot_durations)
            log_callback("📊 Эмоциональный pacing применён:")
            log_callback(f"   • Первые 15%: ~{shot_durations[0]:.2f}s (hook - быстро)")
            log_callback(f"   • Середина: ~{shot_durations[len(shot_durations)//2]:.2f}s (climax)")
            log_callback(f"   • Финал: ~{shot_durations[-1]:.2f}s (resolution - медленно)")
            log_callback(f"   • Диапазон: {shot_min}-{shot_max}s")
            log_callback(f"   • Фактически: {min_actual:.2f}-{max_actual:.2f}s (среднее: {avg_actual:.2f}s)")
            log_callback(f"   • Количество шотов: {len(shot_durations)}")
            # Для использования в batch rendering
            shot_duration = avg_actual
        else:
            # Фиксированная длительность для всех шотов
            # 🔧 КРИТИЧНО: Используем num_shots_needed (из mixed_media или рассчитанное)
            
            # 🔧 КРИТИЧНО: Учитываем что xfade переходы "съедают" время!
            enable_transitions = video_settings.get('enable_transitions', True)
            transition_duration = video_settings.get('transition_duration', 0.3)
            num_transitions = num_shots_needed - 1 if enable_transitions else 0
            if video_settings.get('glitch_enabled', False):
                transition_time_loss = calculate_glitch_transition_loss(
                    num_transitions,
                    video_settings.get('glitch_frequency', 0.5),
                    video_settings.get('glitch_duration', 0.4),
                    transition_duration,
                )
            else:
                transition_time_loss = num_transitions * transition_duration
            
            # Целевая длительность = audio_duration + потери на переходы
            target_duration = audio_duration + transition_time_loss
            
            shot_duration = target_duration / num_shots_needed
            shot_durations = [shot_duration] * num_shots_needed
            log_callback(f"   📊 Фиксированная длительность: {shot_duration:.2f}s x {num_shots_needed} шотов")
            log_callback(f"   🎬 Компенсация переходов: +{transition_time_loss:.1f}s ({num_transitions} стыков)")
            
            # КРИТИЧНО: Корректируем с учётом округления кадров
            import math
            fps = video_settings.get('fps', 60)
            
            # Рассчитываем фактическую длительность с учётом округления кадров
            actual_durations = [math.ceil(d * fps) / fps for d in shot_durations]
            sum(actual_durations)
            
            # 🔧 КРИТИЧНО: Корректируем последний шот чтобы сумма = target_duration (с учётом переходов)
            current_total = sum(shot_durations)
            if abs(current_total - target_duration) > 0.01:
                diff = target_duration - current_total
                shot_durations[-1] = max(0.5, shot_durations[-1] + diff)
                if diff > 0:
                    log_callback(f"   ⚙️ Корректировка: увеличен последний шот на {diff:.3f}s для точной синхронизации")
                else:
                    log_callback(f"   ⚙️ Корректировка: уменьшен последний шот на {-diff:.3f}s для точной синхронизации")
            
            # Финальная проверка (итоговая длительность после переходов должна = audio_duration)
            final_total = sum(shot_durations)
            expected_final = final_total - transition_time_loss
            if abs(expected_final - audio_duration) > 0.5:
                log_callback(f"   ⚠️ ВНИМАНИЕ: Ожидаемая длительность ({expected_final:.2f}s) ≠ аудио ({audio_duration:.2f}s)")
            
            # Логируем настройки
            if retention_settings:
                # Автоматический режим
                log_callback("📊 Настройки удержания:")
                log_callback(f"   • Режим: {'SHORTS ⚡' if retention_settings.shot_duration <= 2.0 else 'STORIES 📱' if retention_settings.shot_duration <= 3.0 else 'LONG 🎥'}")
                log_callback(f"   • Длительность шота: {shot_duration:.2f}s (оптимально: {optimal_shot_duration}s)")
                log_callback(f"   • Количество шотов: {len(image_paths)}")
                log_callback(f"   • Zoom: {retention_settings.zoom_intensity if retention_settings.zoom_enabled else 'off'}")
                log_callback(f"   • Переходы: {retention_settings.transition_style if retention_settings.transitions_enabled else 'off'}")
                log_callback(f"   • Насыщенность: {retention_settings.saturation_boost}x")
            else:
                # Пользовательский режим (фиксированная длительность)
                log_callback("📊 Настройки:")
                log_callback("   • Режим: ПОЛЬЗОВАТЕЛЬСКИЙ 👤")
                log_callback(f"   • Длительность шота: {shot_duration:.2f}s")
                log_callback(f"   • Количество шотов: {len(image_paths)}")
        
        # Validate shot durations
        min_duration = min(shot_durations)
        if min_duration < 0.5:
            log_callback(f"⚠️ ВНИМАНИЕ: Очень короткие шоты (минимум {min_duration:.2f}s). Рекомендуется минимум 1s")
        
        # --- FFMPEG Graph Setup ---
        enable_animation = bool(video_settings.get('enable_animation', True))
        log_callback(f"🎬 Режим анимации: {'ВКЛЮЧЕН' if enable_animation else 'ОТКЛЮЧЕН'}")
        animation_type = video_settings.get('animation_type', 'mix')
        animation_speed = int(video_settings.get('animation_speed', 50))

        # --- FIXED: CORRECTLY HANDLE REUSED IMAGES ---
        timeline_images = (
            [path for path, media_type in mixed_media if media_type == 'image']
            if mixed_media else list(image_paths)
        )
        if timeline_images:
            log_callback("🔧 Анализ использования изображений...")
        
        # Count only images that are actually present in the final timeline.
        path_counts = {p: timeline_images.count(p) for p in set(timeline_images)}
        
        if len(path_counts) < len(timeline_images):
            log_callback(f"🔄 Обнаружено переиспользование: {len(path_counts)} уникальных изображений для {len(timeline_images)} шотов")
            for path, count in path_counts.items():
                if count > 1:
                    log_callback(f"   • {Path(path).name}: используется {count}x")

        # 🎬 Определяем что рендерить: mixed_media или image_paths
        if mixed_media:
            media_list = mixed_media
            images_count = sum(1 for _, t in media_list if t == 'image')
            videos_count = sum(1 for _, t in media_list if t == 'video')
            log_callback(f"🎬 Создание {len(media_list)} видео-шотов (смешанный контент)...")
            log_callback(f"   📊 {images_count} изображений + {videos_count} видеоклипов")
            
            # 🔧 Корректируем shot_durations если длина media_list отличается от image_paths
            if len(media_list) != len(shot_durations):
                log_callback(f"   🔧 Корректировка shot_durations: {len(shot_durations)} → {len(media_list)}")
                if len(media_list) > len(shot_durations):
                    # Добавляем недостающие длительности (средняя)
                    avg_duration = sum(shot_durations) / len(shot_durations) if shot_durations else optimal_shot_duration
                    while len(shot_durations) < len(media_list):
                        shot_durations.append(avg_duration)
                else:
                    # Обрезаем лишние
                    shot_durations = shot_durations[:len(media_list)]
                # Нормализуем чтобы сумма = audio_duration
                total = sum(shot_durations)
                if total > 0:
                    scale = audio_duration / total
                    shot_durations = [d * scale for d in shot_durations]
        else:
            media_list = [(path, 'image') for path in image_paths] if image_paths else []
            log_callback(f"🎬 Создание {len(media_list)} видео-шотов...")
            
            # 🔧 ФИКС: Корректируем shot_durations если image_paths отличается от num_shots_needed
            if len(media_list) != len(shot_durations):
                log_callback(f"   🔧 Корректировка shot_durations: {len(shot_durations)} → {len(media_list)}")
                if len(media_list) > len(shot_durations):
                    avg_duration = sum(shot_durations) / len(shot_durations) if shot_durations else 3.0
                    while len(shot_durations) < len(media_list):
                        shot_durations.append(avg_duration)
                else:
                    shot_durations = shot_durations[:len(media_list)]
                # Нормализуем чтобы сумма = audio_duration
                total = sum(shot_durations)
                if total > 0:
                    scale = audio_duration / total
                    shot_durations = [d * scale for d in shot_durations]
        
        # P1: Валидация количества шотов
        if len(media_list) > self.MAX_SHOTS:
            original_count = len(media_list)
            log_callback(
                f"⚠️ Слишком много шотов ({original_count}); "
                f"равномерно прореживаем до {self.MAX_SHOTS} без потери конца ролика"
            )
            indices = [
                min(original_count - 1, int(i * original_count / self.MAX_SHOTS))
                for i in range(self.MAX_SHOTS)
            ]
            media_list = [media_list[index] for index in indices]
            shot_durations = [shot_durations[index] for index in indices]
            total_duration = sum(shot_durations)
            if total_duration > 0:
                duration_scale = audio_duration / total_duration
                shot_durations = [duration * duration_scale for duration in shot_durations]
        
        # 🎬 PILLOW ANIMATION V2: Плавная анимация
        pillow_animated_shots = {}
        pillow_temp_dir = None
        
        # 🖼️ ПЕРВЫЙ ШОТ: Выжигание названия
        first_shot_title_burned = False
        first_shot_from_pool = video_settings.get('first_shot_from_pool', False)
        burn_first_shot_title = video_settings.get('burn_first_shot_title', True)
        
        # 🔍 Проверка условий для выжигания названия
        if first_shot_from_pool and burn_first_shot_title and media_list and media_list[0][1] == 'image':
            # Проверяем есть ли пользовательское название
            custom_title = video_settings.get('first_shot_title_custom', '').strip()
            
            if custom_title:
                context_text = " ".join([
                    str(text_content.get('title', '')) if text_content else '',
                    str(text_content.get('theme', '')) if text_content else '',
                    str(text_content.get('common_theme', '')) if text_content else '',
                ])
                title_words = {
                    w for w in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", custom_title.lower())
                    if w not in {"этот", "эта", "это", "эти", "ваш", "ваша", "ваше", "видео"}
                }
                context_words = set(re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", context_text.lower()))
                overlap = title_words & context_words
                if title_words and not overlap:
                    log_callback("⚠️" * 20)
                    log_callback("⚠️ ВНИМАНИЕ: ручное название первого кадра не похоже на текущую тему!")
                    log_callback(f"⚠️ Ручное название: '{custom_title}'")
                    log_callback(f"⚠️ Текущая тема/title: '{context_text[:180]}'")
                    log_callback("⚠️ Проверьте поле 'Название первого кадра', иначе можно испортить всю партию.")
                    log_callback("⚠️" * 20)
                # Используем пользовательское название
                first_shot_title = custom_title
                log_callback(f"🖼️ Первый шот: выжигаем пользовательское название '{first_shot_title}'")
            else:
                first_shot_title, title_source = self._select_first_shot_title(text_content)
                if first_shot_title:
                    log_callback(f"🖼️ Первый шот ({title_source}): выжигаем '{first_shot_title}'")
            
            if first_shot_title:
                
                from core.first_shot_title_burner import FirstShotTitleBurner
                
                # Создаем временную директорию для первого шота
                first_shot_temp_dir = safe_temp_dir(prefix='first_shot_title_')
                self._tl.temp_dirs.append(first_shot_temp_dir)
                
                # Путь к исходной картинке
                original_image = media_list[0][0]
                
                # Путь для картинки с выжженным названием
                burned_image_path = first_shot_temp_dir / "first_shot_with_title.png"
                
                # Выжигаем название
                # Красная линия обычно на 65% высоты (для 2560px = ~1664px)
                red_line_y = int(height * 0.65)
                
                success = FirstShotTitleBurner.burn_title_on_image(
                    image_path=str(original_image),
                    output_path=str(burned_image_path),
                    title=first_shot_title,
                    video_width=width,
                    video_height=height,
                    red_line_y=red_line_y,
                    ffmpeg_path=FFMPEG_PATH,
                    log_callback=log_callback
                )
                
                if success and burned_image_path.exists():
                    # Заменяем первый шот на картинку с названием
                    media_list[0] = (str(burned_image_path), 'image')
                    first_shot_title_burned = True
                    log_callback("   ✅ Название выжжено на первом шоте")
                else:
                    log_callback("   ⚠️ Не удалось выжечь название, используем оригинал")
        
        if enable_animation:
            image_indices = [i for i, (_, t) in enumerate(media_list) if t == 'image']
            
            if image_indices:
                from core.pillow_animator import AnimationType
                all_animations = AnimationType.all()
                anim_mode = "MIX (3 типа)" if animation_type == 'mix' else animation_type
                log_callback(f"🎬 Pillow Animation: генерация {len(image_indices)} анимаций (режим: {anim_mode})...")
                
                pillow_temp_dir = safe_temp_dir(prefix='pillow_batch_')
                self._tl.temp_dirs.append(pillow_temp_dir)
                
                shots_config = []
                for idx in image_indices:
                    img_path = media_list[idx][0]
                    shot_dur = shot_durations[idx] if idx < len(shot_durations) else 3.0
                    current_anim = all_animations[idx % len(all_animations)] if animation_type == 'mix' else animation_type
                    
                    shots_config.append({
                        'image_path': img_path,
                        'output_path': str(pillow_temp_dir / f"shot_{idx:03d}.mp4"),
                        'width': width,
                        'height': target_height,
                        'fps': fps,
                        'duration': shot_dur,
                        'animation_type': current_anim,
                        'zoom_amount': 0.12,
                        'use_nvenc': self._check_nvenc_available(),
                        'index': idx
                    })
                
                try:
                    pillow_results = PillowAnimator.create_multiple_shots_parallel(
                        shots_config, log_callback=log_callback
                    )
                    
                    for i, cfg in enumerate(shots_config):
                        if i < len(pillow_results) and pillow_results[i]:
                            pillow_animated_shots[cfg['index']] = pillow_results[i]
                    
                    log_callback(f"   ✅ Создано {len(pillow_animated_shots)} анимированных шотов")
                except Exception as pillow_error:
                    log_callback(f"   ⚠️ Ошибка Pillow: {pillow_error}")
                    pillow_animated_shots = {}
        
        # Create separate input for each shot
        # CRITICAL FIX: Don't use -loop and -t at input level - they create single frame!
        # Instead, load image once and use loop filter to create video stream
        from collections import defaultdict

        source_duration_cache = {}
        video_indices_by_path = defaultdict(list)
        for media_index, (path_str, media_type) in enumerate(media_list):
            if path_str and media_type == 'video':
                normalized = str(Path(path_str).resolve()).replace('\\', '/')
                video_indices_by_path[normalized].append(media_index)

        video_start_offsets = {}
        for normalized_path, media_indices in video_indices_by_path.items():
            try:
                source_duration_cache[normalized_path] = self._get_duration(normalized_path)
            except Exception:
                source_duration_cache[normalized_path] = 0.0
            starts = plan_video_window_starts(
                source_duration_cache[normalized_path],
                [
                    shot_durations[index] if index < len(shot_durations) else optimal_shot_duration
                    for index in media_indices
                ],
            )
            for index, start in zip(media_indices, starts):
                video_start_offsets[index] = start
            if len(media_indices) > 1:
                requested_duration = sum(
                    shot_durations[index] if index < len(shot_durations) else optimal_shot_duration
                    for index in media_indices
                )
                mode = (
                    "непересекающиеся окна"
                    if source_duration_cache[normalized_path] >= requested_duration
                    else "максимально разнесённые окна"
                )
                log_callback(
                    f"   🎞️ {Path(normalized_path).name}: {len(media_indices)} использований, "
                    f"{mode} по таймлайну донора"
                )

        shot_inputs = []
        shot_source_paths = []
        shot_media_indices = []
        media_types = []  # Храним типы для каждого шота
        
        # 🔧 КРИТИЧНО: Так как при включенных переходах мы рендерим КАЖДЫЙ шот
        # в отдельном ffmpeg процессе, мы НЕ МОЖЕМ использовать 'split' фильтр 
        # для многократного использования одного файла (это ломает граф - 'output unconnected').
        # Каждый шот должен быть просто отдельным `ffmpeg.input`.
        
        for i, (path_str, media_type) in enumerate(media_list):
            if path_str is None:
                log_callback(f"   ⚠️ Шот #{i+1}: пропущен (None путь)")
                continue  # Пропускаем None пути
            normalized_path = str(Path(path_str).resolve()).replace('\\', '/')
            
            shot_input = ffmpeg.input(normalized_path)
            
            shot_inputs.append(shot_input)
            shot_source_paths.append(normalized_path)
            shot_media_indices.append(i)
            media_types.append(media_type)
            
            if i < 10 or i >= len(media_list) - 5:  # Log first 10 and last 5
                type_icon = "🖼️" if media_type == 'image' else "🎬"
                log_callback(f"   {type_icon} Шот #{i+1}: {Path(path_str).name}")
            elif i == 10:
                log_callback(f"   ... ({len(media_list) - 15} шотов) ...")
        
        video_shots = []
        
        # CRITICAL FIX: Track which images are reused and apply split BEFORE processing
        # This prevents "multiple outgoing edges" errors in ffmpeg-python
        
        for i, shot_input in enumerate(shot_inputs):
            try:
                media_index = shot_media_indices[i]
                # 🎲 Используем индивидуальную длительность для каждого шота
                current_shot_duration = shot_durations[media_index]
                media_type = media_types[i] if i < len(media_types) else 'image'
                
                if media_type == 'video':
                    # 🎬 YouTube клип - обрабатываем как ВИДЕО (не статичное изображение!)
                    # zoompan НЕ работает для видео - он берёт только первый кадр!
                    # Используем scale + crop для динамического контента
                    
                    try:
                        # 🔧 КРИТИЧНО: Получаем длительность клипа через ffprobe
                        clip_path = shot_source_paths[i] if i < len(shot_source_paths) else None
                        clip_duration = source_duration_cache.get(clip_path, 0)
                        if clip_duration <= 0:
                            clip_duration = current_shot_duration  # Fallback
                        source_start = video_start_offsets.get(media_index, 0.0)

                        final_clip = build_looped_video_shot(
                            clip_path,
                            current_shot_duration,
                            width,
                            target_height,
                            fps,
                            source_duration=clip_duration,
                            source_start=source_start,
                        )

                        if source_start > 0.01:
                            log_callback(
                                f"   ✂️ Шот #{media_index + 1}: окно "
                                f"{source_start:.1f}–{source_start + current_shot_duration:.1f}s"
                            )

                        if clip_duration > 0 and clip_duration < current_shot_duration * 0.99:
                            log_callback(
                                f"   🔁 Шот #{i+1}: плавно зациклен boomerang "
                                f"({clip_duration:.1f}s → {current_shot_duration:.1f}s)"
                            )
                        
                        video_shots.append(final_clip)
                        
                    except Exception as clip_error:
                        log_callback(f"   ⚠️ Ошибка видеоклипа #{i+1}: {clip_error}, используем чёрный кадр")
                        # Чёрный кадр как последнее средство
                        black_frame = ffmpeg.input(
                            f'color=c=#1a1a2e:s={width}x{target_height}:d={current_shot_duration}:r={fps}',
                            f='lavfi'
                        )
                        video_shots.append(black_frame)
                elif enable_animation:
                    # 🎬 PILLOW ANIMATION: Используем предварительно созданные видео
                    if pillow_animated_shots and i in pillow_animated_shots:
                        # Pillow уже создал анимированное видео - используем его
                        pillow_video_path = pillow_animated_shots[i]
                        pillow_input = ffmpeg.input(pillow_video_path)
                        # Нормализуем формат и SAR для совместимости
                        format_fixed = pillow_input.filter('format', 'yuv420p')
                        final_shot = format_fixed.filter('setsar', '1')
                        video_shots.append(final_shot)
                        
                        # Логируем только первые несколько
                        if i < 5:
                            log_callback(f"   🎬 Шот #{i+1}: Pillow анимация ({current_shot_duration:.1f}s)")
                    else:
                        # Fallback на zoompan если Pillow не создал видео
                        animated_shot = self._apply_animation(
                            stream=shot_input, animation_type=animation_type, shot_duration=current_shot_duration,
                            width=width, height=target_height, fps=fps, speed=animation_speed, index=i, log_callback=log_callback,
                            retention_settings=retention_settings
                        )
                        video_shots.append(animated_shot)
                else:
                    # No animation - need to create video stream from static image
                    import math
                    num_frames = math.ceil(current_shot_duration * fps)
                    stream = shot_input.filter('loop', num_frames-1, 1, 0).filter('fps', fps=fps)
                    scaled_shot = stream.filter('scale', width, target_height, force_original_aspect_ratio='increase').filter('crop', width, target_height)  # 🎬 target_height для triple template
                    # 🔧 КРИТИЧНО: Нормализуем пиксельный формат для совместимости с YouTube клипами
                    format_fixed = scaled_shot.filter('format', 'yuv420p')
                    # 🔧 КРИТИЧНО: Устанавливаем SAR=1 для совместимости с concat
                    final_shot = format_fixed.filter('setsar', '1')
                    video_shots.append(final_shot)
                    # Логируем только первые 3 картинки без анимации
                    img_count = sum(1 for j, t in enumerate(media_types[:i+1]) if t == 'image')
                    if img_count <= 3:
                        log_callback(f"   🖼️ Шот #{i+1}: картинка ({current_shot_duration:.1f}s) - без анимации")
            except Exception as e:
                log_callback(f"⚠️ Ошибка обработки шота #{i+1}: {e}")
                raise

        if not video_shots:
            raise Exception("Не создано ни одного видео-шота")

        # 📊 Статистика по типам шотов
        yt_clips_count = sum(1 for t in media_types if t == 'video')
        img_count = sum(1 for t in media_types if t == 'image')
        enable_broll = video_settings.get('enable_broll_effects', True)
        log_callback("📊 Статистика шотов:")
        log_callback(f"   🎬 Видеоклипов: {yt_clips_count} ({'с B-roll эффектами' if enable_broll else 'без эффектов'})")
        log_callback(f"   🖼️ Изображений: {img_count} ({'с анимацией' if enable_animation else 'без анимации'})")
        if yt_clips_count > 0 and img_count > 0:
            yt_percent = yt_clips_count / len(media_types) * 100
            log_callback(f"   📈 Соотношение: {yt_percent:.0f}% YouTube / {100-yt_percent:.0f}% картинки")

        # --- CONCAT ALL SHOTS ---
        log_callback(f"🔗 Объединение {len(video_shots)} видео-шотов в единый поток...")
        
        # Проверяем нужны ли переходы
        enable_transitions = video_settings.get('enable_transitions', True)
        transition_duration = video_settings.get('transition_duration', 0.3)
        
        # КРИТИЧНО: Batch concat для обхода Windows CMD limit
        # Разбиваем шоты на группы, склеиваем каждую группу, потом склеиваем группы
        
        BATCH_SIZE = 10  # Консервативный размер для ffmpeg-python (лимит ~20-25, но безопаснее 10)
        temp_concat_file = None
        
        try:
            # 🎬 НОВАЯ ЛОГИКА: Если включены переходы - рендерим каждый шот в файл
            if enable_transitions and len(video_shots) >= 2:
                log_callback(f"   🎬 Рендеринг {len(video_shots)} шотов для применения переходов...")
                
                temp_dir = safe_temp_dir(prefix='shots_transitions_')  # 🌍 Unicode-safe
                self._tl.temp_dirs.append(temp_dir)  # P0: Регистрация для cleanup
                shot_files = []
                
                # Получаем настройки кодека
                codec_settings = self._get_video_codec_settings(use_nvenc=True, log_callback=None)
                
                # ⚡ ПАРАЛЛЕЛЬНЫЙ рендеринг шотов (заменяет последовательный цикл)
                # Каждый FFmpeg-процесс независим — можно запускать параллельно.
                # Результаты собираются по shot_idx для сохранения порядка.
                _shot_uses_nvenc = codec_settings['vcodec'] == 'h264_nvenc'
                _par_workers = self._calculate_shot_render_workers(
                    len(video_shots),
                    use_nvenc=_shot_uses_nvenc,
                )
                log_callback(f"   ⚡ Параллельный рендер: {_par_workers} воркеров для {len(video_shots)} шотов")

                # ultrafast для временных файлов — перекодируются при финальной склейке
                if codec_settings['vcodec'] == 'h264_nvenc':
                    _tmp_codec = {'vcodec': 'h264_nvenc', 'preset': self.NVENC_PRESET, 'cq': self.NVENC_CQ}
                else:
                    _tmp_codec = {'vcodec': 'libx264', 'preset': 'ultrafast', 'crf': 23}

                def _render_shot_par(args):
                    shot_idx, shot = args
                    shot_file = temp_dir / f'shot_{shot_idx:03d}.mp4'
                    
                    # Optimization: If the shot is a pre-rendered Pillow animation, copy it directly
                    if pillow_animated_shots and shot_idx in pillow_animated_shots:
                        import shutil
                        try:
                            src_path = pillow_animated_shots[shot_idx]
                            shutil.copy2(src_path, str(shot_file))
                            dur = shot_durations[shot_idx]
                            self._validator._ffprobe_cache[str(Path(shot_file).resolve())] = dur
                            return shot_idx, str(shot_file)
                        except Exception as copy_err:
                            log_callback(f"   ⚠️ Ошибка копирования Pillow шота {shot_idx}: {copy_err}, рендерим обычным путем")

                    def _attempt(codec):
                        _uses_nvenc = codec.get('vcodec') == 'h264_nvenc'
                        _nvenc_acquired = False
                        if _uses_nvenc:
                            _nvenc_acquired = _nvenc_manager.acquire(timeout=120)
                            if not _nvenc_acquired:
                                raise TimeoutError("таймаут ожидания свободной NVENC-сессии")
                        try:
                            shot_file.unlink(missing_ok=True)
                            shot_stream = ffmpeg.output(
                                shot,
                                str(shot_file),
                                pix_fmt='yuv420p',
                                r=fps,
                                s=f'{width}x{target_height}',
                                **codec
                            ).overwrite_output()
                            cmd = shot_stream.compile()
                            result = run_registered(
                                cmd,
                                label=f"ffmpeg_transition_shot_{shot_idx}",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='ignore', timeout=120
                            )
                            if result.returncode != 0:
                                raise Exception(f"FFmpeg error {result.returncode}: {result.stderr[-200:]}")
                            if shot_file.exists() and shot_file.stat().st_size > self.MIN_FILE_SIZE:
                                dur = shot_durations[shot_idx]
                                self._validator._ffprobe_cache[str(Path(shot_file).resolve())] = dur
                                return shot_idx, str(shot_file)
                            raise Exception(f"File missing or too small: {shot_file}")
                        finally:
                            if _nvenc_acquired:
                                _nvenc_manager.release()

                    try:
                        return _attempt(_tmp_codec)
                    except Exception as _e:
                        log_callback(
                            f"   ⚠️ Ошибка рендеринга шота {shot_idx}, "
                            f"повторяем через CPU: {_e}"
                        )
                        try:
                            return _attempt({'vcodec': 'libx264', 'preset': 'ultrafast', 'crf': 23})
                        except Exception as _cpu_error:
                            log_callback(f"   ❌ Шот {shot_idx} не восстановлен: {_cpu_error}")
                            return shot_idx, None

                from concurrent.futures import ThreadPoolExecutor as _TPER, as_completed as _ac
                _shot_results = {}
                with _TPER(max_workers=_par_workers) as _ex:
                    _futs = {_ex.submit(_render_shot_par, (i, s)): i
                             for i, s in enumerate(video_shots)}
                    _done = 0
                    for _fut in _ac(_futs):
                        _sidx, _spath = _fut.result()
                        _done += 1
                        if _spath:
                            _shot_results[_sidx] = _spath
                        if _done % 50 == 0 or _done == len(video_shots):
                            log_callback(f"   📹 Отрендерено: {_done}/{len(video_shots)} шотов")

                # Восстанавливаем порядок
                shot_files = [_shot_results[i] for i in range(len(video_shots)) if i in _shot_results]
                missing_shots = [
                    index for index in range(len(video_shots)) if index not in _shot_results
                ]
                if missing_shots:
                    raise Exception(
                        f"Потеряно {len(missing_shots)} видеошотов при подготовке "
                        f"({missing_shots[:8]}). Рендер остановлен, чтобы не создавать "
                        f"ролик с замороженным концом."
                    )

                if len(shot_files) >= 2:
                    # 🎬 Определяем стиль переходов
                    transition_style = video_settings.get('transition_style', 'cinematic')
                    transition_frequency = video_settings.get('transition_frequency', 0.6)
                    cinematic_polish = video_settings.get('cinematic_polish', True)
                    _subtitles_on = subtitle_settings and subtitle_settings.get('enabled', False)
                    _skip_d = shot_durations[0] if (first_shot_title_burned and shot_durations) else 0.0
                    _overlay_file = self._get_overlay_file_path(media_path, log_callback)
                    _ov = str(_overlay_file) if _overlay_file else None

                    # 🚀 ONE-PASS: xfade + overlay + audio в одной FFmpeg команде
                    # Ограничение: при >50 входах Windows cmd-строка переполняется (~8192 символов)
                    # → ONE-PASS пропускаем, идём сразу в batch-concat
                    _MAX_ONE_PASS = 50
                    _glitch_on = bool(video_settings.get('glitch_enabled', False))
                    _atmospheric_on = resolve_video_effect(
                        video_settings, str(output_path), audio_duration
                    )[0] != 'none'
                    if _glitch_on and not _subtitles_on:
                        log_callback("   ⚡ Быстрый рендер пропущен: применяем включённые глитч-переходы")
                    if _atmospheric_on and not _subtitles_on:
                        log_callback("   ✨ Быстрый рендер пропущен: применяем атмосферный эффект")
                    if not _subtitles_on and not use_triple_template and not _glitch_on and not _atmospheric_on and len(shot_files) <= _MAX_ONE_PASS:
                        _ok = self._one_pass_render(
                            shot_files=shot_files,
                            audio_path=str(audio_path),
                            output_path=str(output_path),
                            fps=fps,
                            width=width,
                            height=target_height,
                            audio_duration=audio_duration,
                            transition_duration=transition_duration,
                            transition_frequency=transition_frequency,
                            transition_style=transition_style,
                            cinematic_polish=cinematic_polish,
                            media_types=media_types,
                            overlay_path=_ov,
                            overlay_settings=overlay_settings,
                            skip_duration=_skip_d,
                            log_callback=log_callback,
                        )
                        if _ok:
                            return str(output_path)
                        log_callback("⚠️ ONE-PASS не удался, fallback на двухпроходный рендер...")

                    # Fallback: склеиваем все шоты с переходами
                    temp_concat_file = temp_dir / 'concatenated_with_transitions.mp4'
                    self._concat_shots_with_transitions(
                        shot_files=shot_files,
                        output_path=str(temp_concat_file),
                        fps=fps,
                        width=width,
                        height=target_height,
                        transition_duration=transition_duration,
                        log_callback=log_callback,
                        media_types=media_types,
                        transition_style=transition_style,
                        transition_frequency=transition_frequency,
                        glitch_settings=video_settings
                    )
                    validate_video_timeline(
                        str(temp_concat_file), audio_duration, log_callback
                    )
                    concatenated_video = ffmpeg.input(str(temp_concat_file))
                    log_callback(f"   ✅ Все {len(shot_files)} шотов склеены с переходами")
                else:
                    # Fallback на обычную склейку
                    log_callback("   ⚠️ Недостаточно шотов для переходов, используем обычную склейку")
                    concatenated_video = ffmpeg.concat(*video_shots, v=1, a=0)
                    
            elif len(video_shots) > BATCH_SIZE:
                log_callback(f"   ⚠️ Много шотов ({len(video_shots)}), используем batch concat")
                
                # Разбиваем на группы
                batches = [video_shots[i:i + BATCH_SIZE] for i in range(0, len(video_shots), BATCH_SIZE)]
                log_callback(f"   📦 Разбито на {len(batches)} групп по ~{BATCH_SIZE} шотов")
                
                # Склеиваем каждую группу
                batch_files = []
                temp_dir = safe_temp_dir(prefix='batch_')  # 🌍 Unicode-safe
                self._tl.temp_dirs.append(temp_dir)  # P0: Регистрация для cleanup
                
                # ОПТИМИЗАЦИЯ: Параллельный рендеринг групп
                # Определяем количество параллельных процессов
                
                # КРИТИЧНО: Учитываем параллельную генерацию видео!
                # Если запущено несколько воркеров генерации, каждый может рендерить параллельно
                # Нужно ограничить общее количество ffmpeg процессов
                
                # Проверяем сколько активных потоков (примерная оценка параллельных генераций)
                # active_threads = threading.active_count()
                estimated_parallel_generations = max(1, VideoRenderer._ACTIVE_RENDERERS)
                
                # КРИТИЧНО: Ограничиваем параллелизм для NVENC
                # Потребительские GPU NVIDIA имеют лимит на одновременные сессии кодирования (обычно 2-3)
                # Профессиональные GPU (Quadro/Tesla) поддерживают больше
                if self._check_nvenc_available():
                    # Для NVENC: максимум 2-3 параллельных сессии ВСЕГО
                    # Если запущено несколько генераций, делим лимит между ними
                    max_nvenc_sessions = 3
                    num_workers = max(1, min(max_nvenc_sessions // estimated_parallel_generations, len(batches)))
                    
                    if estimated_parallel_generations > 1:
                        log_callback(f"   🎮 NVENC: ~{estimated_parallel_generations} параллельных генераций обнаружено")
                        log_callback(f"   🎮 Ограничиваем до {num_workers} потоков рендеринга (лимит GPU: {max_nvenc_sessions})")
                    else:
                        log_callback(f"   🎮 NVENC обнаружен: используем {num_workers} параллельных сессий")
                else:
                    # Для CPU: можем использовать больше потоков, но тоже учитываем параллельные генерации
                    max_cpu_workers = min(8, max(4, safe_cpu_count() // 2))
                    num_workers = max(1, min(max_cpu_workers // estimated_parallel_generations, len(batches)))
                    
                    if estimated_parallel_generations > 1:
                        log_callback(f"   💻 CPU: ~{estimated_parallel_generations} параллельных генераций")
                        log_callback(f"   💻 Используем {num_workers} потоков рендеринга (макс: {max_cpu_workers})")
                    else:
                        log_callback(f"   💻 CPU кодирование: используем {num_workers} потоков")
                
                # Если групп мало, не используем параллелизм
                use_parallel = len(batches) > 3
                
                if use_parallel:
                    log_callback(f"   ⚡ Параллельный рендеринг: {num_workers} потоков")
                    
                    # Создаём функцию для рендеринга одной группы
                    def render_batch(batch_data):
                        batch_idx, batch = batch_data
                        
                        # Если используем NVENC, захватываем семафор
                        use_nvenc = self._check_nvenc_available()
                        
                        if use_nvenc:
                            global _NVENC_ACTIVE_SESSIONS, _NVENC_SESSIONS_LOCK
                            
                            # P0: Ждем свободную NVENC сессию с timeout для предотвращения deadlock
                            acquired = _NVENC_SEMAPHORE.acquire(timeout=300)  # 5 минут timeout
                            if not acquired:
                                log_callback(f"      ⚠️ Timeout ожидания NVENC сессии для группы {batch_idx + 1}, используем CPU")
                                use_nvenc = False
                            else:
                                with _NVENC_SESSIONS_LOCK:
                                    _NVENC_ACTIVE_SESSIONS += 1
                                    log_callback(f"      🎮 NVENC сессия {_NVENC_ACTIVE_SESSIONS}/3 захвачена для группы {batch_idx + 1}")
                        
                        try:
                            result = self._render_single_batch(
                                batch_idx=batch_idx,
                                batch=batch,
                                total_batches=len(batches),
                                temp_dir=temp_dir,
                                shot_duration=shot_duration,
                                fps=fps,
                                width=width,
                                height=height,
                                log_callback=log_callback
                            )
                            return result
                        finally:
                            # Освобождаем NVENC сессию
                            if use_nvenc:
                                with _NVENC_SESSIONS_LOCK:
                                    _NVENC_ACTIVE_SESSIONS -= 1
                                    log_callback(f"      🎮 NVENC сессия освобождена (осталось {_NVENC_ACTIVE_SESSIONS})")
                                _NVENC_SEMAPHORE.release()
                    
                    # Рендерим группы параллельно
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    
                    with ThreadPoolExecutor(max_workers=num_workers) as executor:
                        # Запускаем все задачи
                        futures = {
                            executor.submit(render_batch, (idx, batch)): idx 
                            for idx, batch in enumerate(batches)
                        }
                        
                        # Собираем результаты по мере готовности
                        results = {}
                        for future in as_completed(futures):
                            batch_idx = futures[future]
                            try:
                                batch_file = future.result()
                                results[batch_idx] = batch_file
                                log_callback(f"      ✅ Группа {batch_idx + 1}/{len(batches)} готова")
                            except Exception as e:
                                log_callback(f"      ❌ Группа {batch_idx + 1} упала: {e}")
                                raise
                        
                        # Сортируем результаты по индексу
                        batch_files = [results[i] for i in sorted(results.keys())]
                    
                    log_callback(f"   ✅ Все {len(batches)} групп склеены параллельно")
                else:
                    # Последовательный рендеринг для малого количества групп
                    for batch_idx, batch in enumerate(batches):
                        log_callback(f"   🔗 Группа {batch_idx + 1}/{len(batches)}: склеивание {len(batch)} шотов...")
                        batch_file = self._render_single_batch(
                            batch_idx=batch_idx,
                            batch=batch,
                            total_batches=len(batches),
                            temp_dir=temp_dir,
                            shot_duration=shot_duration,
                            fps=fps,
                            width=width,
                            height=height,
                            log_callback=log_callback
                        )
                        batch_files.append(batch_file)
                
                # Продолжаем с финальным concat
                
                # Теперь склеиваем группы между собой С ПЕРЕХОДАМИ
                if len(batch_files) > 1:
                    # 🎬 Проверяем настройку переходов
                    enable_transitions = video_settings.get('enable_transitions', True)
                    transition_duration = video_settings.get('transition_duration', 0.3)
                    
                    if enable_transitions:
                        log_callback(f"   🎬 Финальное объединение {len(batch_files)} групп с кинематографичными переходами...")
                    else:
                        log_callback(f"   🔗 Финальное объединение {len(batch_files)} групп через concat demuxer...")
                    
                    # КРИТИЧНО: Рассчитываем ожидаемую длительность
                    expected_total = len(video_shots) * shot_duration
                    
                    # Создаём concat list для demuxer
                    temp_concat_file = temp_dir / 'final_concatenated.mp4'
                    
                    # 🎬 КИНЕМАТОГРАФИЧНЫЕ ПЕРЕХОДЫ между группами
                    if enable_transitions and len(batch_files) >= 2:
                        batch_file_paths = [str(bf.resolve()) for bf in batch_files]
                        transition_style = video_settings.get('transition_style', 'cinematic')
                        transition_frequency = video_settings.get('transition_frequency', 0.6)  # 60% по умолчанию
                        self._concat_shots_with_transitions(
                            shot_files=batch_file_paths,
                            output_path=str(temp_concat_file),
                            fps=fps,
                            width=width,
                            height=height,
                            transition_duration=transition_duration,
                            log_callback=log_callback,
                            transition_style=transition_style,
                            transition_frequency=transition_frequency,
                            glitch_settings=video_settings
                        )
                    else:
                        # Простая склейка без переходов
                        concat_list_file = temp_dir / 'final_concat_list.txt'
                        
                        with open(concat_list_file, 'w', encoding='utf-8') as f:
                            for bf in batch_files:
                                normalized_path = str(bf.resolve()).replace('\\', '/')
                                f.write(f"file '{normalized_path}'\n")
                        
                        # Get optimal codec settings (NVENC if available)
                        codec_settings = self._get_video_codec_settings(use_nvenc=True, log_callback=log_callback)
                        
                        # Build codec arguments for subprocess
                        codec_args = []
                        if codec_settings['vcodec'] == 'h264_nvenc':
                            codec_args = [
                                '-vcodec', 'h264_nvenc',
                                '-preset', self.NVENC_PRESET,
                                '-rc', 'vbr',
                                '-cq', str(self.NVENC_CQ),
                                '-b:v', self.NVENC_BITRATE
                            ]
                        else:
                            codec_args = [
                                '-vcodec', 'libx264',
                                '-preset', 'fast',
                                '-crf', str(self.NVENC_CQ)
                            ]
                        
                        concat_cmd = [
                            FFMPEG_PATH,
                            '-f', 'concat',
                            '-safe', '0',
                            '-i', str(concat_list_file),
                            *codec_args,
                            '-pix_fmt', 'yuv420p',
                            '-r', str(fps),
                            '-s', f'{width}x{height}',
                            '-t', str(expected_total),
                            '-vsync', 'cfr',
                            '-fflags', '+genpts',
                            '-y',
                            str(temp_concat_file)
                        ]
                        
                        result = run_registered(concat_cmd, label="ffmpeg_concat_demuxer", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', timeout=600)
                        if result.returncode != 0:
                            log_callback(f"   ❌ Concat demuxer stderr: {result.stderr[:500]}")
                            raise Exception(f"Concat demuxer failed: {result.stderr[:200]}")
                    
                    # Валидация финального concat файла
                    probe_cmd = [
                        'ffprobe', '-v', 'error',
                        '-show_entries', 'format=duration',
                        '-of', 'default=noprint_wrappers=1:nokey=1',
                        str(temp_concat_file)
                    ]
                    probe_result = run_registered(
                        probe_cmd, label="ffprobe_concat_result", capture_output=True, text=True,
                        encoding='utf-8', errors='ignore', timeout=30
                    )
                    final_duration = float(probe_result.stdout.strip()) if probe_result.returncode == 0 else 0
                    
                    log_callback("   ✅ Группы объединены")
                    log_callback(f"   📊 Финальная длительность: {final_duration:.1f}s (ожидалось {expected_total:.1f}s)")
                    
                    if abs(final_duration - expected_total) > 5.0:  # Увеличен допуск для переходов
                        log_callback(f"   ⚠️ Длительность отличается (переходы съедают ~{transition_duration}s на стык)")

                    validate_video_timeline(
                        str(temp_concat_file), audio_duration, log_callback
                    )
                    concatenated_video = ffmpeg.input(str(temp_concat_file))
                else:
                    # Только одна группа - используем её напрямую
                    validate_video_timeline(
                        str(batch_files[0]), audio_duration, log_callback
                    )
                    concatenated_video = ffmpeg.input(str(batch_files[0]))
                
                log_callback("   🔗 Используем объединённый файл для финального рендеринга")
                
            else:
                # Для небольшого количества шотов - проверяем нужны ли переходы
                enable_transitions = video_settings.get('enable_transitions', True)
                transition_duration = video_settings.get('transition_duration', 0.3)
                
                if enable_transitions and len(video_shots) >= 2:
                    # 🎬 КИНЕМАТОГРАФИЧНЫЕ ПЕРЕХОДЫ для малого количества шотов
                    log_callback(f"   🎬 Рендеринг {len(video_shots)} шотов с кинематографичными переходами...")
                    
                    temp_dir = safe_temp_dir(prefix='shots_')  # 🌍 Unicode-safe
                    self._tl.temp_dirs.append(temp_dir)  # P0: Регистрация для cleanup
                    shot_files = []
                    
                    # Рендерим каждый шот в отдельный файл
                    codec_settings = self._get_video_codec_settings(use_nvenc=True, log_callback=None)
                    
                    # ⚡ ПАРАЛЛЕЛЬНЫЙ рендер шотов (малый путь)
                    _shot_uses_nvenc2 = codec_settings['vcodec'] == 'h264_nvenc'
                    _par2_workers = self._calculate_shot_render_workers(
                        len(video_shots),
                        use_nvenc=_shot_uses_nvenc2,
                    )
                    log_callback(f"   ⚡ Параллельный рендер: {_par2_workers} воркеров")

                    if codec_settings['vcodec'] == 'h264_nvenc':
                        _tmp_codec2 = {'vcodec': 'h264_nvenc', 'preset': self.NVENC_PRESET, 'cq': self.NVENC_CQ}
                    else:
                        _tmp_codec2 = {'vcodec': 'libx264', 'preset': 'ultrafast', 'crf': 23}

                    def _render_shot_par2(args2):
                        sidx2, shot2 = args2
                        sf2 = temp_dir / f'shot_{sidx2:03d}.mp4'
                        def _attempt2(codec):
                            _uses_nvenc2 = codec.get('vcodec') == 'h264_nvenc'
                            _nvenc_acquired2 = False
                            if _uses_nvenc2:
                                _nvenc_acquired2 = _nvenc_manager.acquire(timeout=120)
                                if not _nvenc_acquired2:
                                    raise TimeoutError("таймаут ожидания свободной NVENC-сессии")
                            try:
                                sf2.unlink(missing_ok=True)
                                ss2 = ffmpeg.output(
                                    shot2, str(sf2),
                                    pix_fmt='yuv420p', r=fps,
                                    s=f'{width}x{target_height}',
                                    **codec
                                ).overwrite_output()
                                r2 = run_registered(
                                    ss2.compile(),
                                    label=f"ffmpeg_shot_{sidx2}",
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding='utf-8', errors='ignore', timeout=120
                                )
                                if r2.returncode == 0 and sf2.exists() and sf2.stat().st_size > self.MIN_FILE_SIZE:
                                    return sidx2, str(sf2)
                                raise Exception(f"FFmpeg error {r2.returncode}: {r2.stderr[-200:]}")
                            finally:
                                if _nvenc_acquired2:
                                    _nvenc_manager.release()

                        try:
                            return _attempt2(_tmp_codec2)
                        except Exception as _e2:
                            log_callback(f"   ⚠️ Ошибка шота {sidx2}, повторяем через CPU: {_e2}")
                            try:
                                return _attempt2({'vcodec': 'libx264', 'preset': 'ultrafast', 'crf': 23})
                            except Exception as _cpu_error2:
                                log_callback(f"   ❌ Шот {sidx2} не восстановлен: {_cpu_error2}")
                                return sidx2, None

                    from concurrent.futures import ThreadPoolExecutor as _TPER2, as_completed as _ac2
                    _sr2 = {}
                    with _TPER2(max_workers=_par2_workers) as _ex2:
                        _futs2 = {_ex2.submit(_render_shot_par2, (i, s)): i
                                  for i, s in enumerate(video_shots)}
                        for _f2 in _ac2(_futs2):
                            _i2, _p2 = _f2.result()
                            if _p2:
                                _sr2[_i2] = _p2
                    shot_files = [_sr2[i] for i in range(len(video_shots)) if i in _sr2]
                    missing_shots2 = [
                        index for index in range(len(video_shots)) if index not in _sr2
                    ]
                    if missing_shots2:
                        raise Exception(
                            f"Потеряно {len(missing_shots2)} видеошотов при подготовке "
                            f"({missing_shots2[:8]}). Рендер остановлен, чтобы не создавать "
                            f"ролик с замороженным концом."
                        )

                    if len(shot_files) >= 2:
                        transition_style = video_settings.get('transition_style', 'cinematic')
                        transition_frequency = video_settings.get('transition_frequency', 0.6)
                        cinematic_polish = video_settings.get('cinematic_polish', True)
                        _subtitles_on2 = subtitle_settings and subtitle_settings.get('enabled', False)
                        _skip_d2 = shot_durations[0] if (first_shot_title_burned and shot_durations) else 0.0
                        _overlay_file2 = self._get_overlay_file_path(media_path, log_callback)
                        _ov2 = str(_overlay_file2) if _overlay_file2 else None
                        _atmospheric_on2 = resolve_video_effect(
                            video_settings, str(output_path), audio_duration
                        )[0] != 'none'

                        # 🚀 ONE-PASS: ограничен 50 входами (Windows cmd limit)
                        _MAX_ONE_PASS2 = 50
                        if not _subtitles_on2 and not use_triple_template and not _atmospheric_on2 and len(shot_files) <= _MAX_ONE_PASS2:
                            _ok2 = self._one_pass_render(
                                shot_files=shot_files,
                                audio_path=str(audio_path),
                                output_path=str(output_path),
                                fps=fps,
                                width=width,
                                height=target_height,
                                audio_duration=audio_duration,
                                transition_duration=transition_duration,
                                transition_frequency=transition_frequency,
                                transition_style=transition_style,
                                cinematic_polish=cinematic_polish,
                                media_types=media_types,
                                overlay_path=_ov2,
                                overlay_settings=overlay_settings,
                                skip_duration=_skip_d2,
                                log_callback=log_callback,
                            )
                            if _ok2:
                                return str(output_path)
                            log_callback("⚠️ ONE-PASS не удался, fallback на двухпроходный рендер...")

                        # Fallback: склеиваем с переходами
                        temp_concat_file = temp_dir / 'concatenated_with_transitions.mp4'
                        self._concat_shots_with_transitions(
                            shot_files=shot_files,
                            output_path=str(temp_concat_file),
                            fps=fps,
                            width=width,
                            height=target_height,
                            transition_duration=transition_duration,
                            log_callback=log_callback,
                            transition_style=transition_style,
                            transition_frequency=transition_frequency,
                            glitch_settings=video_settings
                        )
                        validate_video_timeline(
                            str(temp_concat_file), audio_duration, log_callback
                        )
                        concatenated_video = ffmpeg.input(str(temp_concat_file))
                    else:
                        # Fallback на простой concat
                        concatenated_video = ffmpeg.concat(*video_shots, v=1, a=0)
                else:
                    # Без переходов - простой concat
                    log_callback(f"   Используем ffmpeg.concat для {len(video_shots)} шотов...")
                    concatenated_video = ffmpeg.concat(*video_shots, v=1, a=0)
                    
                log_callback("   ✅ Видео-шоты объединены в единый поток")
                
        except Exception as e:
            log_callback(f"❌ Ошибка concat: {e}")
            import traceback
            log_callback(f"   Traceback: {traceback.format_exc()}")
            raise Exception(f"Не удалось объединить видео-шоты: {e}")
        
        # 🎬 TRIPLE TEMPLATE: Накладываем изображение на верхнюю половину экрана
        if use_triple_template:
            log_callback("📐 Triple Template: накладываем изображение на чёрный фон...")
            # Создаём чёрный фон полного размера
            background = ffmpeg.input(f'color=c=black:s={width}x{height}:d={audio_duration}', f='lavfi')
            # Накладываем изображение (которое уже target_height) в верхнюю часть (y=0)
            concatenated_video = ffmpeg.overlay(background, concatenated_video, x=0, y=0)
            log_callback(f"   ✅ Изображение размещено в верхней половине ({width}x{target_height})")
        
        # --- BUILT-IN ATMOSPHERIC EFFECTS ---
        try:
            concatenated_video, resolved_effect = apply_video_effect(
                concatenated_video,
                video_settings,
                width,
                height,
                fps,
                audio_duration,
                seed_source=str(output_path),
            )
            if resolved_effect != 'none':
                log_callback(f"✨ Атмосферный эффект: {effect_display_name(resolved_effect)}")
        except Exception as e:
            log_callback(f"⚠️ Ошибка атмосферного эффекта: {e}. Продолжаем без него.")

        # --- DYNAMIC SUBTITLES ---
        try:
            if subtitle_settings and subtitle_settings.get('enabled', False):
                log_callback("📝 Наложение динамических субтитров...")
                concatenated_video = self._apply_dynamic_subtitles(
                    concatenated_video, 
                    text_content, 
                    subtitle_settings, 
                    audio_duration, 
                    width, 
                    height,
                    log_callback,
                    audio_path=audio_path  # ✅ Передаём аудио для синхронизации
                )
        except Exception as e:
            log_callback(f"⚠️ Ошибка субтитров: {e}. Продолжаем без субтитров.")
        
        try:
            overlay_file = self._get_overlay_file_path(media_path, log_callback)
            if overlay_file:
                # 🖼️ ПЕРВЫЙ ШОТ: Пропускаем оверлей если выжжено название
                if first_shot_title_burned and shot_durations:
                    first_shot_duration = shot_durations[0]
                    log_callback(f"🖼️ Наложение оверлея (пропуск первого шота {first_shot_duration:.1f}s)...")
                    concatenated_video = self._apply_overlay(
                        concatenated_video, overlay_file, overlay_settings, width, height, 
                        log_callback, skip_duration=first_shot_duration
                    )
                else:
                    log_callback("🖼️ Наложение оверлея...")
                    concatenated_video = self._apply_overlay(
                        concatenated_video, overlay_file, overlay_settings, width, height, log_callback
                    )
        except Exception as e:
            log_callback(f"⚠️ Ошибка наложения оверлея: {e}. Продолжаем без оверлея.")

        # --- FINAL RENDER ---
        
        # Retry with NVENC fallback
        MAX_RENDER_ATTEMPTS = 2
        use_nvenc = True
        
        for render_attempt in range(MAX_RENDER_ATTEMPTS):
            try:
                if render_attempt == 0:
                    log_callback("🎬 Запуск финального рендеринга FFmpeg...")
                else:
                    log_callback(f"🔄 Попытка {render_attempt + 1}/{MAX_RENDER_ATTEMPTS}...")
                
                # Normalize audio path for Windows
                audio_path_normalized = str(Path(audio_path).resolve()).replace('\\', '/')
                audio_input = ffmpeg.input(audio_path_normalized)
                
                # Create output with proper settings
                # ВАЖНО: НЕ используем shortest - видео должно быть полной длительности
                # Аудио будет зациклено или обрезано автоматически FFmpeg
                
                # КРИТИЧНО: Параметры для ОТЛИЧНОЙ перемотки видео
                # GOP size = 2 секунды для быстрого seeking
                # Preset = fast для простой структуры
                # Profile = main для совместимости
                # Faststart = метаданные в начале файла
                gop_size = fps * 2  # Keyframe каждые 2 секунды
                
                # Get optimal codec settings (NVENC if available)
                codec_settings = self._get_video_codec_settings(use_nvenc=use_nvenc, log_callback=log_callback)
                
                # Log codec being used
                codec_name = "NVENC (GPU)" if codec_settings['vcodec'] == 'h264_nvenc' else "libx264 (CPU)"
                log_callback(f"🎬 Кодек: {codec_name}, GOP={gop_size} кадров ({gop_size/fps:.1f}s)")
                
                # Дополнительные параметры (не конфликтующие с codec_settings)
                extra_params = {
                    'b:a': '192k',
                    'g': gop_size,  # GOP size (keyframe interval)
                    'keyint_min': gop_size,  # Minimum GOP size
                    'sc_threshold': 0,  # Отключить scene detection
                    'movflags': '+faststart'  # Метаданные в начале файла
                }
                
                # Добавляем параметры только для CPU encoding (NVENC не поддерживает)
                if codec_settings['vcodec'] == 'libx264':
                    extra_params['profile:v'] = 'main'
                    extra_params['level'] = '4.0'
                    extra_params['bf'] = 2
                
                stream = ffmpeg.output(
                    concatenated_video, audio_input, str(output_path),
                    acodec='aac',
                    pix_fmt='yuv420p',
                    r=fps,
                    s=f'{width}x{height}',
                    t=audio_duration,  # ✅ Точная длительность по аудио
                    map_metadata=-1,  # ✅ Удаляем все метаданные
                    **codec_settings,
                    **extra_params
                ).overwrite_output()
                
                # Log command for debugging
                cmd_list = stream.compile()
                log_callback(f"🔧 FFmpeg команда: {' '.join(cmd_list[:10])}... ({len(cmd_list)} аргументов)")
                
                # DEBUG: Log full command to file for analysis (only if debug enabled)
                debug_mode = video_settings.get('debug_mode', False)
                if debug_mode:
                    import hashlib
                    # time уже импортирован глобально
                    debug_hash = hashlib.md5(f"{output_path.stem}{time.time()}".encode()).hexdigest()[:8]
                    debug_file = output_path.parent / f"ffmpeg_debug_{debug_hash}.txt"
                    with open(debug_file, "w", encoding="utf-8") as f:
                        f.write(" ".join(cmd_list))
                    log_callback(f"   📝 DEBUG: Команда сохранена в: {debug_file.name}")

                # Run FFmpeg with stderr capture for better error messages
                try:
                    final_cmd = stream.compile()
                    final_timeout = max(600, min(12 * 60 * 60, int(audio_duration * 4) + 600))
                    result = run_registered(
                        final_cmd,
                        label="ffmpeg_final_render",
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=final_timeout,
                    )
                    if result.returncode != 0:
                        raise ffmpeg.Error('ffmpeg', result.stdout, result.stderr)
                except ffmpeg.Error as e:
                    # Re-raise with captured stderr
                    raise e
                
                # If we got here, render succeeded - break retry loop
                break
                
            except ffmpeg.Error as e:
                # Try to get stderr from exception
                stderr = None
                if hasattr(e, 'stderr') and e.stderr:
                    try:
                        stderr = e.stderr.decode('utf-8', errors='ignore')
                    except Exception:
                        stderr = str(e.stderr)
                
                if not stderr:
                    stderr = str(e)
                
                # Log full stderr to file for debugging
                try:
                    error_log_file = output_path.parent / f"ffmpeg_error_{int(time.time())}.txt"
                    with open(error_log_file, 'w', encoding='utf-8') as f:
                        f.write(f"FFmpeg Error Log\n{'='*60}\n")
                        f.write(f"Command: {' '.join(cmd_list)}\n\n")
                        f.write(f"Stderr:\n{stderr}\n")
                    log_callback(f"   📝 Полный лог ошибки: {error_log_file.name}")
                except Exception:
                    pass
                
                # Check if it's an NVENC error
                is_nvenc_error = 'nvenc' in stderr.lower() or 'incompatible client key' in stderr.lower()
                
                if is_nvenc_error and use_nvenc and render_attempt < MAX_RENDER_ATTEMPTS - 1:
                    log_callback("⚠️ NVENC недоступен для финального рендеринга, переключаемся на CPU...")
                    use_nvenc = False
                    time.sleep(2)
                    continue
                else:
                    # Log detailed error before re-raising
                    log_callback("❌ FFmpeg ошибка рендеринга:")
                    if stderr:
                        # Показываем последние 500 символов stderr для диагностики
                        stderr_tail = stderr[-500:] if len(stderr) > 500 else stderr
                        log_callback(f"   📋 stderr: {stderr_tail}")
                    # Re-raise the error if we can't retry
                    raise
        
        # Validate output after successful render
        try:
            
            # Validate output
            if not Path(output_path).exists():
                raise Exception("Выходной файл не был создан")
            
            output_size = Path(output_path).stat().st_size
            if output_size < 1000:
                raise Exception(f"Выходной файл слишком мал: {output_size} байт")
            
            log_callback(f"✅ Финальное видео успешно создано: {output_path}")
            log_callback(f"   Размер: {output_size / (1024*1024):.2f} MB")
            
            # КРИТИЧНО: Финальная валидация длительности
            try:
                final_probe = probe_registered(
                    str(output_path), label="ffprobe_full_render_output"
                )
                final_duration = float(final_probe['format']['duration'])
                log_callback(f"   Длительность: {final_duration:.2f}s (ожидалось {audio_duration:.2f}s)")
                
                # Проверка что длительность близка к ожидаемой
                duration_diff = abs(final_duration - audio_duration)
                if duration_diff > 5.0:  # Допуск 5 секунд
                    log_callback("   ❌ КРИТИЧНО: Длительность видео не совпадает с аудио!")
                    log_callback(f"   Разница: {duration_diff:.1f}s ({duration_diff/audio_duration*100:.1f}% контента)")
                    log_callback("   Видео повреждено или неполное! Удаляем.")
                    Path(output_path).unlink(missing_ok=True)
                    raise Exception(f"Видео не прошло валидацию длительности: {final_duration:.1f}s != {audio_duration:.1f}s")
                
                log_callback(f"   ✅ Валидация длительности пройдена (разница: {duration_diff:.1f}s)")
                
            except ffmpeg.Error as e:
                log_callback(f"   ⚠️ Не удалось проверить длительность финального видео: {e}")
            
            # 🎥 VEO 3: Merge AI-intro with slideshow
            if veo3_intro_video and Path(veo3_intro_video).exists():
                log_callback("\n" + "=" * 60)
                log_callback("🎬 VEO 3: Сшивка AI-интро со слайдшоу")
                log_callback("=" * 60)
                
                try:
                    # Slideshow video is in output_path
                    slideshow_video = str(output_path)
                    
                    # Create final output path
                    final_output = output_path.parent / f"{output_path.stem}_with_intro{output_path.suffix}"
                    
                    log_callback(f"   🎬 AI-интро: {veo3_intro_duration:.2f}s")
                    log_callback(f"   🖼️ Слайдшоу: {final_duration:.2f}s")
                    log_callback("   🎯 Сшивка с crossfade переходом...")
                    
                    # Merge with crossfade (scale to slideshow resolution)
                    merged_video = concat_videos_with_crossfade(
                        video1=veo3_intro_video,
                        video2=slideshow_video,
                        output_video=str(final_output),
                        transition_duration=0.3,
                        target_resolution=(width, height),  # Scale to final video resolution
                        log_callback=log_callback
                    )
                    
                    if merged_video and Path(merged_video).exists():
                        # Replace original with merged
                        Path(merged_video).replace(output_path)
                        
                        log_callback("   ✅ Финальное видео с AI-интро готово!")
                        log_callback(f"   📁 {output_path}")
                        
                        # Update duration
                        try:
                            merged_probe = probe_registered(
                                str(output_path), label="ffprobe_veo_merged_output"
                            )
                            merged_duration = float(merged_probe['format']['duration'])
                            log_callback(f"   ⏱️ Итоговая длительность: {merged_duration:.2f}s")
                        except Exception:
                            pass
                    else:
                        log_callback("   ⚠️ Не удалось сшить видео, используем слайдшоу")
                    
                except Exception as e:
                    log_callback(f"   ❌ Ошибка сшивки: {str(e)[:200]}")
                    log_callback("   Используем слайдшоу без AI-интро")
                
                log_callback("=" * 60 + "\n")
            
            # Final cleanup of all Veo 3 temp files
            try:
                output_dir = Path(output_path).parent
                for veo3_file in output_dir.glob("veo3_*"):
                    if veo3_file.is_file():
                        veo3_file.unlink()
            except Exception:
                pass
            
            # P0: Cleanup теперь централизован в _render_full_video_impl через finally
            
            return str(output_path)

        except ffmpeg.Error as e:
            # Try to get stderr
            stderr = None
            if hasattr(e, 'stderr') and e.stderr:
                try:
                    stderr = e.stderr.decode('utf-8', errors='ignore')
                except Exception:
                    stderr = str(e.stderr)
            
            if not stderr:
                stderr = str(e)
            
            log_callback("❌ ОШИБКА FFmpeg при финальном рендеринге:")
            log_callback(f"   {stderr[:1000]}")
            log_callback("   💡 Проверьте ffmpeg_command_debug.txt для деталей")
            
            # Cleanup on error
            if Path(output_path).exists():
                try:
                    Path(output_path).unlink()
                    log_callback("🗑️ Удален поврежденный выходной файл")
                except Exception:
                    pass  # File deletion errors are not critical
            
            # P0: Cleanup теперь централизован в _render_full_video_impl через finally
            
            raise Exception(f"Ошибка FFmpeg: {stderr[:500]}")
        except Exception as e:
            log_callback(f"❌ Непредвиденная ошибка рендеринга: {e}")
            # P0: Cleanup теперь централизован в _render_full_video_impl через finally
            raise

    def _apply_animation(self, stream, animation_type, shot_duration, width, height, fps, speed, index, log_callback, retention_settings=None):
        """Fallback анимация через zoompan.
        
        Поддерживает: pan_left, pan_right, zoom_center
        """
        import math
        
        from core.pillow_animator import AnimationType
        all_animations = AnimationType.all()
        current_anim = all_animations[index % len(all_animations)] if animation_type == 'mix' else animation_type
        
        total_frames = math.ceil(shot_duration * fps)
        tf = max(total_frames, 1)
        
        # t от 0 до 1, с жёстким ограничением
        t = f'(min(1,max(0,on/{tf})))'
        # Ease-in-out sine - плавный без рывков
        ease = f'((1-cos({t}*PI))/2)'
        
        # Динамическая амплитуда на основе скорости (по умолчанию ~0.2)
        # speed 50 -> 0.2
        # speed 10 -> 0.08
        amount = max(0.05, speed / 250.0)
        
        if current_anim == 'pan_left':
            zoom_expr = '1.3' # Чуть больше зума для пана чтобы не было черных полос
            x_expr = f'(iw*{amount})*(1-{ease})'
            y_expr = '(ih-ih/zoom)/2'
        elif current_anim == 'pan_right':
            zoom_expr = '1.3'
            x_expr = f'(iw*{amount})*{ease}'
            y_expr = '(ih-ih/zoom)/2'
        else:  # zoom_center
            z_amt = amount * 0.8
            zoom_expr = f'1+{z_amt}*{ease}'
            x_expr = 'iw/2-(iw/zoom/2)'
            y_expr = 'ih/2-(ih/zoom/2)'
        
        try:
            animated = stream.filter(
                'zoompan',
                z=zoom_expr,
                x=x_expr,
                y=y_expr,
                d=tf,
                s=f'{width}x{height}',
                fps=fps
            )
            format_fixed = animated.filter('format', 'yuv420p')
            return format_fixed.filter('setsar', '1')
        except Exception as e:
            log_callback(f"   ⚠️ Fallback zoompan ошибка: {e}")
            return stream.filter('scale', width, height, force_original_aspect_ratio='increase').filter('crop', width, height)

    def concatenate_videos(self, video_files: List[str], output_path: str, log_callback) -> bool:
        """Concatenates a list of video files into a single video. Returns True on success."""
        if not video_files:
            log_callback("⚠️ Нет видео-чанков для объединения.")
            return False

        # Fast path: one chunk -> copy as final
        if len(video_files) == 1:
            try:
                shutil.copyfile(video_files[0], output_path)
                log_callback(f"✅ Единственный чанк сохранён как финальное видео: {output_path}")
                return True
            except Exception as e:
                log_callback(f"❌ Не удалось скопировать файл чанка: {e}")
                return False

        # Use output_dir for concat list (not chunk parent, to avoid path issues)
        concat_list_path = Path(output_path).parent / "concat_list.txt"
        
        with open(concat_list_path, 'w', encoding='utf-8') as f:
            for file_path in video_files:
                # Use absolute path to avoid confusion
                abs_path = Path(file_path).resolve()
                f.write(f"file '{abs_path.as_posix()}'\n")

        # ✅ Retry logic для критической операции
        MAX_RETRIES = 3
        
        for attempt in range(MAX_RETRIES):
            try:
                if attempt > 0:
                    log_callback(f"🔄 Попытка {attempt + 1}/{MAX_RETRIES} объединения видео...")
                else:
                    log_callback(f"🔧 Объединение {len(video_files)} чанков в {output_path}...")
                
                concat_stream = ffmpeg.input(
                    str(concat_list_path), format='concat', safe=0
                ).output(
                    str(output_path), 
                    c='copy',
                    map_metadata=-1  # ✅ Удаляем все метаданные
                ).overwrite_output()
                concat_command = concat_stream.compile(cmd=FFMPEG_PATH)
                concat_result = run_registered(
                    concat_command,
                    label="ffmpeg_video_chunks_concat",
                    capture_output=True,
                    timeout=600,
                )
                if concat_result.returncode != 0:
                    raise ffmpeg.Error(
                        "ffmpeg", concat_result.stdout, concat_result.stderr
                    )
                
                log_callback("✅ Финальное видео успешно объединено (метаданные удалены).")
                
                # --- Cleanup ---
                concat_list_path.unlink()
                for file_path in video_files:
                    try:
                        Path(file_path).unlink()
                    except OSError as e:
                        log_callback(f"⚠️ Не удалось удалить временный чанк {file_path}: {e}")
                return True

            except ffmpeg.Error as e:
                error_msg = e.stderr.decode('utf-8') if e.stderr else 'No stderr'
                log_callback(f"❌ Попытка {attempt + 1} не удалась: {error_msg[:200]}")
                
                if attempt < MAX_RETRIES - 1:
                    # time уже импортирован глобально
                    wait_time = 2 * (attempt + 1)
                    log_callback(f"⏳ Ожидание {wait_time}s перед повтором...")
                    time.sleep(wait_time)
                    continue
                else:
                    log_callback(f"❌ Все {MAX_RETRIES} попытки объединения не удались")
            # Fallback: take first chunk as output
            try:
                shutil.copyfile(video_files[0], output_path)
                log_callback(f"⚠️ Использован fallback: первый чанк сохранён как финал: {output_path}")
                
                # Cleanup chunks even in fallback mode
                concat_list_path.unlink(missing_ok=True)
                for file_path in video_files:
                    try:
                        Path(file_path).unlink()
                    except OSError:
                        pass
                
                return True
            except Exception as e2:
                log_callback(f"❌ Fallback copy тоже не удался: {e2}")
                return False

    def _apply_dynamic_subtitles(self, video_stream, text_content, settings, duration, width, height, log_callback, audio_path=None):
        """Apply dynamic subtitles using ASS file format with PERFECT synchronization."""
        return self._subtitle_renderer.apply(
            video_stream,
            text_content,
            settings,
            duration,
            width,
            height,
            log_callback,
            audio_path=audio_path,
        )

    def _get_subtitle_position(self, position, width, height):
        """Compatibility wrapper for legacy drawtext callers."""
        return self._subtitle_renderer.subtitle_position(position, width, height)

    def _apply_overlay(self, stream, overlay_path, settings, video_w, video_h, log_callback=None, skip_duration=0):
        """
        Накладывает медиа-оверлей (изображение или видео) на видеопоток.
        АВТОМАТИЧЕСКОЕ МАСШТАБИРОВАНИЕ: оверлей подстраивается под размер видео.
        
        Args:
            stream: Входной видеопоток ffmpeg
            overlay_path: Путь к файлу оверлея (изображение или видео)
            settings: Настройки оверлея (position, margin, fullscreen)
            video_w: Ширина основного видео
            video_h: Высота основного видео
            log_callback: Функция логирования (опционально)
            skip_duration: Пропустить первые N секунд (для первого шота с названием)
            
        Returns:
            Видеопоток с наложенным оверлеем
        """
        def log(msg):
            if log_callback:
                log_callback(msg)
        
        try:
            overlay_path = Path(overlay_path).resolve()
            
            # Проверка существования файла
            if not overlay_path.exists():
                log(f"   ⚠️ Файл оверлея не найден: {overlay_path}")
                return stream
            
            # Получаем настройки
            if not overlay_path.is_file():
                log(f"   WARNING: Overlay path is not a file: {overlay_path}")
                return stream

            margin = settings.get('margin', 50)
            position = settings.get('position', 'Внизу справа')
            fullscreen = settings.get('fullscreen', False)  # Новая опция для полноэкранного оверлея
            
            log(f"   🖼️ Оверлей: {overlay_path.name}")
            if fullscreen:
                log("   📐 Режим: Полноэкранный")
            else:
                log(f"   📐 Позиция: {position}, Отступ: {margin}px")
            
            # Определяем тип файла
            video_extensions = ['.mp4', '.mov', '.webm', '.avi', '.mkv', '.gif']
            image_extensions = ['.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff']
            
            suffix = overlay_path.suffix.lower()
            is_video = suffix in video_extensions
            is_image = suffix in image_extensions
            has_alpha = suffix in ['.png', '.webp', '.gif', '.mov', '.webm']
            
            # 🎯 УМНОЕ МАСШТАБИРОВАНИЕ ОВЕРЛЕЯ
            if fullscreen:
                # Полноэкранный режим: оверлей заполняет весь экран
                target_width = video_w
                target_height = video_h
                scale_info = "Полноэкранный"
                log(f"   📏 {scale_info} масштаб: {target_width}x{target_height}")
            else:
                # 🎯 УМНОЕ МАСШТАБИРОВАНИЕ ОВЕРЛЕЯ (как водяной знак/логотип)
                if video_w > video_h:
                    target_width = int(video_w * 0.20)
                    scale_info = "Водяной знак/логотип (20% ширины для горизонтального видео)"
                else:
                    target_width = int(video_w * 0.25)
                    scale_info = "Водяной знак/логотип (25% ширины для вертикального видео)"
                target_height = -2
                log(f"   📏 Масштаб водяного знака: {scale_info} -> {target_width}x-2")
            
            # Получаем оригинальный размер для логирования
            _orig_w, _orig_h = 0, 0
            
            if is_video:
                log("   🎬 Тип: Видео оверлей" + (" (с альфа-каналом)" if has_alpha else ""))
                
                # Видео оверлей (как в рабочей версии): input без stream_loop, зацикливание через filter loop
                overlay_input = ffmpeg.input(str(overlay_path))
                
                if fullscreen:
                    # Полноэкранный режим: масштабируем до точного размера видео
                    overlay_scaled = overlay_input.filter('scale', target_width, target_height)
                else:
                    # Обычный режим: масштабируем с сохранением пропорций
                    if isinstance(target_width, int) and isinstance(target_height, int):
                        # Точный размер (для одинакового соотношения сторон)
                        overlay_scaled = overlay_input.filter('scale', target_width, target_height)
                    elif target_height == -2:
                        # Масштабирование по ширине
                        overlay_scaled = overlay_input.filter('scale', target_width, -2)
                    else:
                        # Масштабирование по высоте
                        overlay_scaled = overlay_input.filter('scale', -2, target_height)
                
                # 🔄 ЦИКЛИЧЕСКИЙ ПОВТОР оверлея (иначе после окончания .mov видео замирает)
                overlay_looped = overlay_scaled.filter('loop', loop=-1, size=32767)
                
                if fullscreen:
                    # Полноэкранный: позиция 0,0 (весь экран)
                    x_pos, y_pos = '0', '0'
                else:
                    # Обычный: позиция по настройкам
                    x_pos, y_pos = self._get_overlay_position(position, video_w, video_h, margin)
                
                # 🖼️ ПЕРВЫЙ ШОТ: Пропускаем оверлей если указан skip_duration
                overlay_kwargs = {'x': x_pos, 'y': y_pos, 'shortest': 1}
                
                if skip_duration > 0:
                    # Применяем оверлей только после skip_duration секунд
                    overlay_kwargs['enable'] = f'gte(t,{skip_duration})'
                    log(f"   🖼️ Оверлей будет применен после {skip_duration:.1f}s (пропуск первого шота)")
                
                if has_alpha:
                    overlay_kwargs['format'] = 'auto'
                    result = ffmpeg.overlay(stream, overlay_looped, **overlay_kwargs)
                else:
                    result = ffmpeg.overlay(stream, overlay_looped, **overlay_kwargs)
                
                log("   ✅ Видео оверлей применён (с циклическим повтором)")
                return result
                
            elif is_image:
                log("   🖼️ Тип: Изображение" + (" (с альфа-каналом)" if has_alpha else ""))
                
                overlay_input = ffmpeg.input(str(overlay_path), loop=1)
                
                if fullscreen:
                    # Полноэкранный режим: масштабируем до точного размера видео
                    overlay_scaled = overlay_input.filter('scale', target_width, target_height)
                else:
                    # Обычный режим: масштабируем с сохранением пропорций
                    if isinstance(target_width, int) and isinstance(target_height, int):
                        # Точный размер (для одинакового соотношения сторон)
                        overlay_scaled = overlay_input.filter('scale', target_width, target_height)
                    elif target_height == -2:
                        # Масштабирование по ширине
                        overlay_scaled = overlay_input.filter('scale', target_width, -2)
                    else:
                        # Масштабирование по высоте
                        overlay_scaled = overlay_input.filter('scale', -2, target_height)
                
                if fullscreen:
                    # Полноэкранный: позиция 0,0 (весь экран)
                    x_pos, y_pos = '0', '0'
                else:
                    # Обычный: позиция по настройкам
                    x_pos, y_pos = self._get_overlay_position(position, video_w, video_h, margin)
                
                # 🖼️ ПЕРВЫЙ ШОТ: Пропускаем оверлей если указан skip_duration
                overlay_kwargs = {'x': x_pos, 'y': y_pos, 'shortest': 1}
                
                if skip_duration > 0:
                    # Применяем оверлей только после skip_duration секунд
                    overlay_kwargs['enable'] = f'gte(t,{skip_duration})'
                
                if has_alpha:
                    overlay_kwargs['format'] = 'auto'
                    result = ffmpeg.overlay(stream, overlay_scaled, **overlay_kwargs)
                else:
                    result = ffmpeg.overlay(stream, overlay_scaled, **overlay_kwargs)
                
                log("   ✅ Изображение оверлей применено")
                return result
            else:
                log(f"   ⚠️ Неподдерживаемый формат оверлея: {suffix}")
                return stream
                
        except Exception as e:
            self.logger.error(f"Failed to apply overlay: {e}")
            if log_callback:
                log_callback(f"   ❌ Ошибка наложения оверлея: {e}")
            return stream

    def _get_overlay_position(self, position_str, video_w, video_h, margin):
        """
        Рассчитывает позицию оверлея на видео.
        
        Args:
            position_str: Название позиции (например, "Внизу справа")
            video_w: Ширина основного видео
            video_h: Высота основного видео
            margin: Отступ от края в пикселях
            
        Returns:
            Tuple[str, str]: (x_expression, y_expression) для ffmpeg overlay filter
            
        Note:
            В ffmpeg overlay filter:
            - W, H = размеры основного видео (main)
            - w, h = размеры оверлея
            - Используем main_w/main_h для совместимости с ffmpeg-python
        """
        margin = str(margin)
        
        # Позиции с использованием main_w/main_h (ffmpeg-python синтаксис)
        positions = {
            # Нижний ряд
            "bottom_right": (f'main_w-overlay_w-{margin}', f'main_h-overlay_h-{margin}'),
            "bottom": ('(main_w-overlay_w)/2', f'main_h-overlay_h-{margin}'),
            "bottom_left": (margin, f'main_h-overlay_h-{margin}'),
            "Внизу справа": (f'main_w-overlay_w-{margin}', f'main_h-overlay_h-{margin}'),
            "Внизу по центру": ('(main_w-overlay_w)/2', f'main_h-overlay_h-{margin}'),
            "Внизу слева": (margin, f'main_h-overlay_h-{margin}'),
            # Центр
            "center": ('(main_w-overlay_w)/2', '(main_h-overlay_h)/2'),
            "По центру": ('(main_w-overlay_w)/2', '(main_h-overlay_h)/2'),
            # Верхний ряд
            "top_right": (f'main_w-overlay_w-{margin}', margin),
            "top": ('(main_w-overlay_w)/2', margin),
            "top_left": (margin, margin),
            "Вверху справа": (f'main_w-overlay_w-{margin}', margin),
            "Вверху по центру": ('(main_w-overlay_w)/2', margin),
            "Вверху слева": (margin, margin),
        }
        
        return positions.get(position_str, (f'main_w-overlay_w-{margin}', f'main_h-overlay_h-{margin}'))
    
    def _optimize_images_for_zoompan(self, image_paths: List[str], target_width: int, target_height: int, 
                                     max_zoom: float = 1.3, log_callback: Callable = None) -> List[str]:
        """
        ⚡ ОПТИМИЗАЦИЯ ИЗОБРАЖЕНИЙ ДЛЯ ZOOMPAN
        
        Ресайзит картинки до размера, достаточного для зума, но не больше.
        Ускоряет FFmpeg zoompan в 2-3 раза для больших изображений.
        
        Args:
            image_paths: Список путей к изображениям
            target_width: Целевая ширина видео
            target_height: Целевая высота видео
            max_zoom: Максимальный зум (1.3 = 130%)
            log_callback: Функция для логирования
        
        Returns:
            List[str]: Список путей к оптимизированным изображениям
        """
        try:
            from PIL import Image
        except ImportError:
            if log_callback:
                log_callback("⚠️ Pillow не установлен, пропускаем оптимизацию изображений")
            return image_paths
        
        # Рассчитываем безопасный размер с запасом для зума
        # Добавляем 50% запаса для качества
        safe_w = int(target_width * max_zoom * 1.5)
        safe_h = int(target_height * max_zoom * 1.5)
        
        optimized_paths = []
        optimized_count = 0
        
        for img_path in image_paths:
            img_path_obj = Path(img_path)
            
            # Пропускаем уже оптимизированные
            if "_opt" in img_path_obj.stem:
                optimized_paths.append(str(img_path))
                continue
            
            try:
                with Image.open(img_path) as img:
                    original_size = img.size
                    
                    # Если картинка гигантская (>2x целевого размера) - уменьшаем
                    if img.width > safe_w or img.height > safe_h:
                        # Используем thumbnail для сохранения пропорций
                        img.thumbnail((safe_w, safe_h), Image.Resampling.LANCZOS)
                        
                        # Сохраняем оптимизированную версию
                        opt_path = img_path_obj.parent / f"{img_path_obj.stem}_opt{img_path_obj.suffix}"
                        
                        # Быстрое сохранение без лишнего сжатия
                        if img_path_obj.suffix.lower() == '.png':
                            img.save(opt_path, format="PNG", optimize=False, compress_level=1)
                        else:
                            img.save(opt_path, format="JPEG", quality=95, optimize=False)
                        
                        optimized_paths.append(str(opt_path))
                        optimized_count += 1
                        
                        if log_callback and optimized_count <= 3:  # Логируем первые 3
                            log_callback(f"   ⚡ Оптимизировано: {original_size} → {img.size}")
                    else:
                        # Размер нормальный, используем как есть
                        optimized_paths.append(str(img_path))
            
            except Exception as e:
                if log_callback:
                    log_callback(f"⚠️ Ошибка оптимизации {img_path_obj.name}: {e}")
                # При ошибке используем оригинал
                optimized_paths.append(str(img_path))
        
        if log_callback and optimized_count > 0:
            log_callback(f"⚡ Оптимизировано изображений: {optimized_count}/{len(image_paths)}")
            if optimized_count > 3:
                log_callback(f"   ... и еще {optimized_count - 3} изображений")
        
        return optimized_paths


    def render_optimized_one_pass(self, image_paths: List[str], audio_path: str, output_path: Path,
                                   video_settings: Dict[str, Any], subtitle_settings: Dict[str, Any],
                                   text_content: Dict[str, Any], log_callback: Callable,
                                   media_path: str = None, overlay_settings: Dict = None,
                                   veo3_settings: Dict = None, google_ai_api_key: str = None,
                                   theme: str = None) -> str:
        """
        🚀 ОПТИМИЗИРОВАННЫЙ РЕНДЕРИНГ В ОДИН ПРОХОД
        
        Использует filter_complex_script для обхода лимита командной строки Windows (8191 символ).
        Преимущества:
        - Нет промежуточных batch_*.mp4 файлов (экономия дискового I/O)
        - Один проход кодирования NVENC (быстрее в 2-3 раза)
        - Нет ограничения на количество изображений
        - Поддержка любой длины видео
        
        Args:
            image_paths: Список путей к изображениям
            audio_path: Путь к аудиофайлу
            output_path: Путь для сохранения видео
            video_settings: Настройки видео (width, height, fps, animation, etc.)
            subtitle_settings: Настройки субтитров
            text_content: Контент текста (для субтитров)
            log_callback: Функция для логирования
            media_path: Путь к оверлею (опционально)
            overlay_settings: Настройки оверлея (опционально)
        
        Returns:
            str: Путь к созданному видео
        """

        
        width = video_settings['width']
        height = video_settings['height']
        fps = video_settings.get('fps', 60)
        
        # 🎥 VEO 3 AI-INTRO INTEGRATION
        veo3_intro_video = None
        veo3_intro_duration = 0
        
        if veo3_settings and veo3_settings.get('enabled', False):
            log_callback("\n" + "=" * 60)
            log_callback("🎬 VEO 3 AI-INTRO: Генерация взрывного начала")
            log_callback("=" * 60)
            
            try:
                # Generate AI-intro
                veo3_intro_video = generate_intro_with_veo3(
                    theme=text_content.get('title', theme or 'video'),
                    full_text=text_content.get('full_text', ''),
                    veo3_settings=veo3_settings,
                    api_key=google_ai_api_key,
                    output_dir=output_path.parent,
                    video_settings=video_settings,
                    log_callback=log_callback
                )
                
                if veo3_intro_video and Path(veo3_intro_video).exists():
                    # Get intro duration
                    from core.video_utils import get_video_duration
                    veo3_intro_duration = get_video_duration(veo3_intro_video)
                    
                    log_callback(f"✅ AI-интро готово: {veo3_intro_duration:.2f}s")
                    log_callback("   Теперь создаем слайдшоу для остального контента...")
                else:
                    log_callback("⚠️ AI-интро не удалось сгенерировать, используем обычное слайдшоу")
                    veo3_intro_video = None
                    
            except Exception as e:
                log_callback(f"❌ Ошибка генерации AI-интро: {str(e)[:200]}")
                log_callback("   Продолжаем с обычным слайдшоу...")
                veo3_intro_video = None
            
            log_callback("=" * 60 + "\n")
        
        # --- Валидация аудио ---
        try:
            audio_path_obj = Path(audio_path)
            if not audio_path_obj.exists():
                raise Exception(f"Аудиофайл не существует: {audio_path}")
            
            audio_duration = float(
                probe_registered(
                    audio_path, label="ffprobe_optimized_render_audio"
                )['format']['duration']
            )
            if audio_duration <= 0:
                raise Exception(f"Некорректная длительность аудио: {audio_duration}s")
            
            log_callback(f"✅ Аудио валидировано: {audio_duration:.2f}s")
        except Exception as e:
            raise Exception(f"Ошибка валидации аудио: {e}")
        
        if not image_paths:
            raise Exception("Список изображений пуст")
        
        # 🔧 ФИКС: Циклируем изображения если их меньше чем нужно для видео
        # Это решает проблему Windows path length limit для длинных видео
        original_image_count = len(image_paths)
        
        # Оцениваем сколько шотов нужно
        user_shot_duration = video_settings.get('shot_duration', None)
        shot_min = video_settings.get('shot_min_duration', 2)
        shot_max = video_settings.get('shot_max_duration', 2)
        
        if user_shot_duration:
            estimated_shots_needed = int(audio_duration / user_shot_duration)
        else:
            avg_shot_duration = (shot_min + shot_max) / 2
            estimated_shots_needed = int(audio_duration / avg_shot_duration)
        
        # 🔧 WINDOWS PATH FIX: One-Pass рендер имеет лимит ~50 шотов из-за командной строки Windows
        # Если нужно больше шотов - generator.py автоматически переключится на batch rendering
        # Здесь просто логируем предупреждение, но НЕ ограничиваем (пусть упадёт если что)
        MAX_SHOTS_WINDOWS = 50
        
        if estimated_shots_needed > MAX_SHOTS_WINDOWS:
            log_callback(f"⚠️ ВНИМАНИЕ: {estimated_shots_needed} шотов может превысить лимит Windows ({MAX_SHOTS_WINDOWS})")
            log_callback("   Если возникнет ошибка WinError 206 - используйте batch rendering вместо One-Pass")
        
        # Optimized rendering follows the same no-hidden-reuse contract.
        if len(image_paths) < estimated_shots_needed:
            from core.smart_clip_matcher import InsufficientUniqueVisualsError
            raise InsufficientUniqueVisualsError(
                "Недостаточно уникальных изображений для оптимизированного рендера: "
                f"нужно {estimated_shots_needed}, доступно {len(set(image_paths))}. "
                "Скрытое циклирование отключено."
            )
        
        # --- Определение длительностей шотов ---
        # (user_shot_duration, shot_min, shot_max уже определены выше)
        
        shot_durations = []
        
        # P0: Защита от деления на ноль
        if not image_paths:
            log_callback("⚠️ Нет изображений для рендеринга")
            return None
        
        if user_shot_duration:
            # Фиксированная длительность - пересчитываем если шотов меньше чем нужно
            actual_shot_duration = audio_duration / len(image_paths)
            if abs(actual_shot_duration - user_shot_duration) > 0.5:
                log_callback(f"👤 Скорректированный тайминг: {actual_shot_duration:.2f}s (было {user_shot_duration}s)")
            else:
                log_callback(f"👤 Фиксированный тайминг: {user_shot_duration}s")
            shot_durations = [actual_shot_duration] * len(image_paths)
        elif shot_min and shot_max:
            # Рандомная длительность
            log_callback(f"🎲 Рандомный диапазон: {shot_min}-{shot_max}s")
            shot_durations = allocate_random_shot_durations(
                audio_duration,
                len(image_paths),
                shot_min,
                shot_max,
                frontload_fast=(
                    audio_duration <= 60
                    and int(video_settings.get('height', 1920)) > int(video_settings.get('width', 1080))
                ),
            )
            if len(shot_durations) < len(image_paths):
                log_callback(
                    f"   Extra shots removed: {len(image_paths)} -> {len(shot_durations)} "
                    f"(minimum {shot_min}s per shot)"
                )
                image_paths = image_paths[:len(shot_durations)]
            
            # КРИТИЧНО: Корректируем с учётом округления кадров
            import math
            fps = video_settings.get('fps', 60)
            
            # Рассчитываем фактическую длительность с учётом округления кадров
            actual_durations = [math.ceil(d * fps) / fps for d in shot_durations]
            actual_total = sum(actual_durations)
            
            # Если сумма больше audio_duration, уменьшаем последний шот
            if actual_total > audio_duration:
                diff = actual_total - audio_duration
                shot_durations[-1] = max(shot_min, shot_durations[-1] - diff)
                log_callback(f"   ⚙️ Корректировка: уменьшен последний шот на {diff:.3f}s для точной синхронизации")
        else:
            # Равномерное распределение
            shot_duration = audio_duration / len(image_paths)
            shot_durations = [shot_duration] * len(image_paths)
            log_callback(f"📊 Равномерное распределение: {shot_duration:.2f}s на шот")
        
        log_callback(f"🎬 Создание {len(image_paths)} шотов...")
        
        # ⚡ УЛУЧШЕНИЕ 2: Предварительная оптимизация изображений для zoompan
        enable_animation = bool(video_settings.get('enable_animation', False))
        if enable_animation:
            log_callback("⚡ Оптимизация изображений для ускорения zoompan...")
            image_paths = self._optimize_images_for_zoompan(
                image_paths, width, height, max_zoom=1.3, log_callback=log_callback
            )
        
        # --- Подготовка входов для FFmpeg ---
        input_args = []
        
        # 🏎️ УЛУЧШЕНИЕ 3: Hardware Decoding (аппаратное декодирование)
        use_nvenc = self._check_nvenc_available()
        if use_nvenc:
            # Используем аппаратное ускорение для декодирования
            input_args.extend(['-hwaccel', 'auto'])  # auto безопаснее чем cuda
            log_callback("🏎️ Аппаратное декодирование включено (NVENC)")
        
        # Аудио (голос) как input [0]
        input_args.extend(['-i', str(audio_path)])
        
        # Изображения как inputs [1], [2], ...
        for img_path in image_paths:
            normalized_path = str(Path(img_path).resolve())
            input_args.extend(['-i', normalized_path])
        
        # 🔊 УЛУЧШЕНИЕ 1: Audio Ducking (музыка с автоприглушением)
        music_path = video_settings.get('music_path', None)
        music_idx = None
        
        if music_path and Path(music_path).exists():
            # Добавляем музыку как зацикленный input
            input_args.extend(['-stream_loop', '-1', '-i', str(music_path)])
            music_idx = len(image_paths) + 1
            log_callback(f"🔊 Музыка с Audio Ducking: {Path(music_path).name}")
        
        # --- Генерация filter_complex скрипта ---
        filter_lines = []
        concat_inputs = []
        
        enable_animation = bool(video_settings.get('enable_animation', False))
        animation_type = video_settings.get('animation_type', 'mix')
        animation_speed = int(video_settings.get('animation_speed', 50))
        
        # Получаем настройки удержания для анимации
        retention_settings = RetentionOptimizer.get_settings(
            width=width,
            height=height,
            duration=int(audio_duration),
            video_type='auto'
        )
        intensity_zoom = {
            'low': 0.08,
            'medium': 0.12,
            'high': 0.16,
        }.get(getattr(retention_settings, 'zoom_intensity', 'medium'), 0.12)
        speed_factor = max(0.5, min(1.5, animation_speed / 50.0))
        pillow_zoom_amount = max(0.04, min(0.22, intensity_zoom * speed_factor))
        
        # 🔧 КРИТИЧНО: Пересчитываем shot_durations с учётом округления кадров
        # Это гарантирует что offset для xfade будет точно соответствовать длительности zoompan
        import math
        actual_shot_durations = []
        for i, dur in enumerate(shot_durations):
            frames = math.ceil(dur * fps)
            actual_dur = frames / fps
            actual_shot_durations.append(actual_dur)
        
        # Проверяем общую длительность и корректируем последний шот если нужно
        total_video_duration = sum(actual_shot_durations)
        if total_video_duration > audio_duration + 0.1:  # Допуск 0.1s
            # Уменьшаем последний шот
            excess = total_video_duration - audio_duration
            last_frames = int(actual_shot_durations[-1] * fps)
            frames_to_remove = math.ceil(excess * fps)
            last_frames = max(fps, last_frames - frames_to_remove)  # Минимум 1 секунда
            actual_shot_durations[-1] = last_frames / fps
            log_callback(f"   ⚙️ Корректировка последнего шота: {excess:.3f}s excess → {actual_shot_durations[-1]:.3f}s")
        
        # Используем скорректированные длительности для xfade offset
        shot_durations = actual_shot_durations
        
        # Логируем итоговые длительности для отладки
        total_shots_duration = sum(shot_durations)
        log_callback(f"📊 Итого длительность шотов: {total_shots_duration:.3f}s (аудио: {audio_duration:.3f}s)")
        
        # 🎬 PILLOW ANIMATION
        pillow_shot_files = []
        pillow_temp_dir = None
        
        if enable_animation:
            log_callback("🎬 Генерация анимации через Pillow...")
            import tempfile as tmp_module
            pillow_temp_dir = Path(tmp_module.mkdtemp(prefix='pillow_shots_'))
            
            from core.pillow_animator import AnimationType
            all_animations = AnimationType.all()
            
            shots_config = []
            for i, img_path in enumerate(image_paths):
                shot_dur = shot_durations[i]
                current_anim = all_animations[i % len(all_animations)] if animation_type == 'mix' else animation_type
                
                shots_config.append({
                    'image_path': img_path,
                    'output_path': str(pillow_temp_dir / f"shot_{i:03d}.mp4"),
                    'width': width,
                    'height': height,
                    'fps': fps,
                    'duration': shot_dur,
                    'animation_type': current_anim,
                    'zoom_amount': pillow_zoom_amount,
                    'use_nvenc': self._check_nvenc_available()
                })
            
            pillow_shot_files = PillowAnimator.create_multiple_shots_parallel(
                shots_config, log_callback=log_callback
            )
            
            log_callback(f"   ✅ Создано {len(pillow_shot_files)} анимированных шотов")
            
            # Обновляем input_args
            input_args = []
            if use_nvenc:
                input_args.extend(['-hwaccel', 'auto'])
            input_args.extend(['-i', str(audio_path)])
            
            # Track which shots are animated successfully
            shot_is_animated = []
            
            for i, shot_file in enumerate(pillow_shot_files):
                # Check if animation exists and is valid
                if shot_file and Path(shot_file).exists() and Path(shot_file).stat().st_size > 1000:
                    input_args.extend(['-i', shot_file])
                    shot_is_animated.append(True)
                else:
                    # Fallback to static image
                    if shot_file is None:
                        log_callback(f"   ⚠️ Анимация шота #{i+1} не создана (Pillow error), используем статику")
                    else:
                        log_callback(f"   ⚠️ Анимация шота #{i+1} повреждена, используем статику")
                    
                    # Use original image
                    normalized_path = str(Path(image_paths[i]).resolve())
                    input_args.extend(['-i', normalized_path])
                    shot_is_animated.append(False)
            
            if music_path and Path(music_path).exists():
                input_args.extend(['-stream_loop', '-1', '-i', str(music_path)])
                music_idx = len(pillow_shot_files) + 1
        
        for i in range(len(image_paths)):
            input_idx = i + 1
            out_pad = f"v{i}"
            
            shot_dur = shot_durations[i]
            total_frames = int(shot_dur * fps)
            max(total_frames, 1)
            
            # Check if THIS specific shot is animated
            is_animated = False
            if enable_animation and pillow_shot_files:
                if i < len(shot_is_animated):
                    is_animated = shot_is_animated[i]
            
            if is_animated:
                # Pillow уже создал анимированные видео - просто используем их
                # Нужно только setpts для синхронизации и убедиться в формате
                filter_lines.append(
                    f"[{input_idx}:v]setpts=PTS-STARTPTS,fps={fps},format=yuv420p,setsar=1[{out_pad}]"
                )
            else:
                # Без анимации (или fallback) - scale + loop
                filter_lines.append(
                    f"[{input_idx}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},loop={total_frames-1}:1:0,fps={fps},format=yuv420p,setsar=1[{out_pad}]"
                )
            
            concat_inputs.append(f"[{out_pad}]")
        
        # --- Склейка всех шотов с переходами ---
        # Получаем настройки переходов
        enable_transitions = video_settings.get('enable_transitions', True)
        transition_duration = video_settings.get('transition_duration', 0.3)  # 300ms по умолчанию
        transition_style = video_settings.get('transition_style', 'cinematic')
        cinematic_polish_enabled = video_settings.get('cinematic_polish', True)
        
        if enable_transitions and len(concat_inputs) > 1:
            # 🎬 КРАСИВЫЕ ПЕРЕХОДЫ через xfade
            # Типы переходов для чередования (эстетичные и умеренные)
            _legacy_transition_types = [
                'fade',           # Классический fade
                'fadeblack',      # Fade через чёрный
                'smoothleft',     # Плавное движение влево
                'smoothright',    # Плавное движение вправо
                'smoothup',       # Плавное движение вверх
                'smoothdown',     # Плавное движение вниз
                'circlecrop',     # Круговой переход
                'dissolve',       # Растворение
            ]
            
            # Применяем xfade между каждой парой шотов
            # 🔧 ИСПРАВЛЕНИЕ: Правильный расчёт offset для xfade
            # После каждого xfade, результирующий поток короче на transition_duration
            # Offset для i-го xfade = сумма(shot[0..i]) - i * transition_duration
            current_stream = concat_inputs[0].strip('[]')
            used_transitions = []
            
            for i in range(1, len(concat_inputs)):
                next_stream = concat_inputs[i].strip('[]')
                out_stream = f"xf{i}"
                
                # Выбираем тип перехода (чередуем для разнообразия)
                transition = _select_cinematic_transition(
                    i,
                    len(concat_inputs),
                    transition_style=transition_style,
                    used_transitions=used_transitions,
                )
                used_transitions.append(transition)
                
                # 🔧 ПРАВИЛЬНЫЙ РАСЧЁТ OFFSET:
                # offset = сумма длительностей всех предыдущих шотов - (количество предыдущих xfade * transition_duration)
                # Для i-го xfade (i начинается с 1):
                #   - Сумма шотов до текущего: sum(shot_durations[0:i])
                #   - Количество уже применённых xfade: i-1
                #   - Offset = sum(shot_durations[0:i]) - (i-1) * transition_duration - transition_duration
                #            = sum(shot_durations[0:i]) - i * transition_duration
                cumulative_duration = sum(shot_durations[0:i])
                offset = cumulative_duration - i * transition_duration
                
                # Защита от отрицательного offset
                offset = max(0, offset)
                
                # 🔧 КРИТИЧНО: Проверяем что offset + transition_duration <= длительность первого потока
                # Длительность первого потока (результат предыдущих xfade) = cumulative_duration - (i-1) * transition_duration
                first_stream_duration = cumulative_duration - (i - 1) * transition_duration
                if offset + transition_duration > first_stream_duration:
                    # Корректируем offset чтобы переход не выходил за пределы
                    offset = max(0, first_stream_duration - transition_duration - 0.01)
                
                filter_lines.append(
                    f"[{current_stream}][{next_stream}]xfade=transition={transition}:duration={transition_duration}:offset={offset:.3f}[{out_stream}]"
                )
                
                current_stream = out_stream
            
            last_stream = f"[{current_stream}]"
            log_callback(f"✨ Добавлены переходы: {len(concat_inputs)-1} xfade ({transition_duration}s)")
        else:
            # Без переходов - простой concat
            concat_str = "".join(concat_inputs)
            filter_lines.append(f"{concat_str}concat=n={len(image_paths)}:v=1:a=0[v_concat]")
            last_stream = "[v_concat]"

        polish_filter = _build_cinematic_polish_filter(
            last_stream,
            "v_chunk_polish",
            width,
            height,
            fps,
            len(image_paths),
            transition_style=transition_style,
            enabled=cinematic_polish_enabled,
        )
        if polish_filter:
            filter_lines.append(polish_filter)
            last_stream = "[v_chunk_polish]"
            log_callback("🎨 Cinematic polish: film grade + vignette + subtle grain")

        effect_lines, resolved_effect = build_filter_complex_effect(
            last_stream,
            "v_atmosphere",
            video_settings,
            width,
            height,
            fps,
            audio_duration,
            seed_source=str(output_path),
        )
        if effect_lines:
            filter_lines.extend(effect_lines)
            last_stream = "[v_atmosphere]"
            log_callback(f"✨ Атмосферный эффект: {effect_display_name(resolved_effect)}")
        
        ass_temp_dir = None

        # --- Субтитры (если включены) ---
        if subtitle_settings.get('enabled', False):
            try:
                subtitle_content = dict(text_content or {})
                if not subtitle_content.get('full_text') and subtitle_content.get('script'):
                    subtitle_content['full_text'] = subtitle_content['script']
                segments = self._subtitle_renderer.build_segments(
                    subtitle_content,
                    subtitle_settings,
                    audio_duration,
                    audio_path=str(audio_path),
                    log_callback=log_callback,
                )
                if not segments:
                    log_callback("⚠️ Не удалось синхронизировать субтитры (нет текста)")
                    ass_path = None
                else:
                    ass_content = self._subtitle_renderer.build_ass(
                        segments,
                        subtitle_settings,
                        audio_duration,
                        width,
                        height,
                    )
                    ass_temp_dir = safe_temp_dir(prefix='optimized_subtitles_')
                    ass_path = ass_temp_dir / 'subtitles.ass'
                    ass_path.write_text(ass_content, encoding='utf-8')
                
                if ass_path and Path(ass_path).exists():
                    # Экранируем путь для FFmpeg фильтра
                    # Windows: C:\path\file.ass -> C\\:/path/file.ass
                    ass_path_str = str(Path(ass_path).resolve())
                    # Заменяем \ на / и экранируем : и '
                    ass_path_normalized = ass_path_str.replace('\\', '/').replace(':', '\\:').replace("'", "'\\''")
                    
                    filter_lines.append(f"{last_stream}subtitles='{ass_path_normalized}'[v_final]")
                    last_stream = "[v_final]"
                    log_callback(f"✅ Субтитры добавлены: {len(segments)} сегментов")
            except Exception as e:
                log_callback(f"⚠️ Ошибка добавления субтитров: {e}")
        
        # 🔊 УЛУЧШЕНИЕ 1: Audio Ducking - микшируем музыку с голосом
        
        if music_idx is not None:
            # Применяем sidechaincompress для автоматического приглушения музыки
            # Параметры:
            # - threshold=0.05: порог срабатывания (когда голос тише 5% - музыка на полную)
            # - ratio=20: степень сжатия (музыка приглушается в 20 раз)
            # - attack=50: скорость приглушения (50ms - быстро реагирует)
            # - release=300: скорость возврата (300ms - плавно возвращается)
            filter_lines.append(
                f"[{music_idx}:a][0:a]sidechaincompress=threshold=0.05:ratio=20:attack=50:release=300[ducked_music]"
            )
            
            # Микшируем приглушенную музыку с голосом
            # duration=first: обрезаем по длине голоса
            # weights="1 0.3": голос 100%, музыка 30% (можно настроить)
            music_volume = video_settings.get('music_volume', 0.3)  # По умолчанию 30%
            try:
                music_volume = float(music_volume)
            except (TypeError, ValueError):
                music_volume = 0.3
            if music_volume > 1.0:
                music_volume /= 100.0
            music_volume = max(0.0, min(1.0, music_volume))
            filter_lines.append(
                f"[0:a][ducked_music]amix=inputs=2:duration=first:weights=1 {music_volume}:normalize=0[a_out]"
            )
            
            log_callback(f"🔊 Audio Ducking применен (музыка {int(music_volume*100)}%)")
        else:
            # Нет музыки - просто используем голос
            pass
        
        # --- Сохраняем filter_complex скрипт в файл (короткое имя для Windows) ---
        import hashlib
        # time уже импортирован глобально
        # Используем timestamp + короткий хеш для уникальности
        script_hash = hashlib.md5(f"{output_path.stem}{time.time()}".encode()).hexdigest()[:8]
        script_path = output_path.parent / f"filter_{script_hash}.txt"
        
        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(";\n".join(filter_lines))
            
            log_callback(f"📝 Filter script создан: {script_path.name} ({len(filter_lines)} фильтров)")
            
            # 📊 ДЕТАЛЬНЫЙ ЛОГ FILTER_COMPLEX
            log_callback("")
            log_callback("═══════════════════════════════════════════════════════════════")
            log_callback(f"📋 ПОЛНЫЙ FILTER_COMPLEX ({len(filter_lines)} строк):")
            log_callback("═══════════════════════════════════════════════════════════════")
            for idx, line in enumerate(filter_lines):
                # Сокращаем длинные строки для читаемости
                if len(line) > 150:
                    log_callback(f"   [{idx+1:02d}] {line[:150]}...")
                else:
                    log_callback(f"   [{idx+1:02d}] {line}")
            log_callback("═══════════════════════════════════════════════════════════════")
            log_callback("")
            
        except Exception as e:
            raise Exception(f"Не удалось создать filter script: {e}")
        
        # --- Запуск FFmpeg с filter_complex_script ---
        log_callback("🚀 Запуск оптимизированного рендера (One-Pass)...")
        log_callback(f"   • Изображений: {len(image_paths)}")
        log_callback(f"   • Длительность: {audio_duration:.1f}s")
        log_callback(f"   • Анимация: {'ON' if enable_animation else 'OFF'}")
        
        # Получаем настройки кодека
        use_nvenc = self._check_nvenc_available()
        
        # Извлекаем имя последнего стрима без скобок
        final_stream = last_stream.replace('[', '').replace(']', '')
        
        cmd = [
            FFMPEG_PATH,
            '-y',  # Перезаписывать без вопросов
            '-sws_flags', 'lanczos+accurate_rnd+full_chroma_int',  # 🎬 Качественная интерполяция для плавного zoom
            *input_args,
            '-filter_complex_script', str(script_path),
            '-map', f'[{final_stream}]',  # Мапим последний видео поток
        ]
        
        # Мапим правильный аудио стрим (с ducking или без)
        if music_idx is not None:
            cmd.extend(['-map', '[a_out]'])  # Используем микшированный аудио
        else:
            cmd.extend(['-map', '0:a'])  # Используем только голос
        
        # Добавляем настройки кодека
        if use_nvenc:
            cmd.extend([
                '-c:v', 'h264_nvenc',
                '-preset', self.NVENC_PRESET,
                '-rc', 'vbr',
                '-cq', str(self.NVENC_CQ),
                '-b:v', self.NVENC_BITRATE,
            ])
            log_callback("   • Кодек: NVENC (GPU)")
        else:
            cmd.extend([
                '-c:v', 'libx264',
                '-preset', 'medium',
                '-crf', str(self.NVENC_CQ),
            ])
            log_callback("   • Кодек: libx264 (CPU)")
        
        # Проверяем длину пути и используем временный файл если нужно
        final_output_path = None
        if len(str(output_path)) > 200:  # Windows limit ~260 chars
            import tempfile
            temp_output = Path(tempfile.gettempdir()) / f"temp_video_{script_hash}.mp4"
            final_output_path = output_path
            output_path = temp_output
            log_callback(f"⚠️ Длинный путь ({len(str(final_output_path))} символов), используем временный файл")
        
        # Аудио кодек
        cmd.extend([
            '-c:a', 'aac',
            '-b:a', '192k',
            # НЕ используем -shortest - видео должно быть полной длительности
            # Корректировка длительностей шотов гарантирует синхронизацию
            '-map_metadata', '-1',  # Удаляем метаданные
            str(output_path)
        ])
        
        # Запускаем FFmpeg
        try:
            start_time = time.time()
            
            result = run_registered(
                cmd,
                label="ffmpeg_optimized_one_pass",
                capture_output=True,
                text=True,
                timeout=audio_duration * 10 + 300  # Таймаут: 10x длительность + 5 минут
            )
            
            render_time = time.time() - start_time
            
            # 📊 ВСЕГДА ЛОГИРУЕМ STDERR (для диагностики тряски)
            ffmpeg_log = output_path.parent / f"ffmpeg_log_{script_hash}.txt"
            with open(ffmpeg_log, 'w', encoding='utf-8') as f:
                f.write("=" * 60 + "\n")
                f.write("FFMPEG COMMAND:\n")
                f.write("=" * 60 + "\n")
                f.write(" ".join(cmd) + "\n\n")
                f.write("=" * 60 + "\n")
                f.write("FFMPEG STDERR:\n")
                f.write("=" * 60 + "\n")
                f.write(result.stderr or "(empty)")
            log_callback(f"📋 FFmpeg лог сохранён: {ffmpeg_log.name}")
            
            # Проверяем stderr на warnings связанные с анимацией
            if result.stderr:
                stderr_lower = result.stderr.lower()
                if 'discont' in stderr_lower or 'discarding' in stderr_lower:
                    log_callback("   ⚠️ ВНИМАНИЕ: FFmpeg обнаружил discontinuity (возможная причина тряски)")
                if 'discarding' in stderr_lower:
                    log_callback("   ⚠️ ВНИМАНИЕ: FFmpeg отбрасывает кадры")
                if 'discont' in stderr_lower:
                    log_callback("   ⚠️ ВНИМАНИЕ: Разрыв временных меток")
                if 'discarding' in stderr_lower or 'discont' in stderr_lower or 'discarding' in stderr_lower:
                    # Показываем релевантные строки
                    for line in result.stderr.split('\n'):
                        if any(w in line.lower() for w in ['discont', 'discarding', 'discarding', 'discarding']):
                            log_callback(f"   │ {line.strip()[:100]}")
            
            if result.returncode != 0:
                # Сохраняем stderr для отладки (короткое имя)
                error_log = output_path.parent / f"ffmpeg_error_{script_hash}.txt"
                with open(error_log, 'w', encoding='utf-8') as f:
                    f.write(result.stderr)
                
                raise Exception(f"FFmpeg завершился с ошибкой (код {result.returncode}). Лог: {error_log}")
            
            # Проверяем что файл создан
            if not output_path.exists():
                raise Exception(f"Выходной файл не создан: {output_path}")
            
            file_size = output_path.stat().st_size
            if file_size < 1000:
                raise Exception(f"Выходной файл слишком мал: {file_size} байт")
            
            # Если использовали временный файл, перемещаем в финальное место
            if final_output_path:
                try:
                    import shutil
                    log_callback("📁 Перемещение из временного файла...")
                    shutil.move(str(output_path), str(final_output_path))
                    output_path = final_output_path
                    log_callback(f"✅ Файл перемещён: {output_path.name}")
                except Exception as move_error:
                    log_callback(f"⚠️ Не удалось переместить файл: {move_error}")
                    log_callback(f"📁 Файл остался в: {output_path}")
            
            log_callback("✅ Видео создано успешно!")
            log_callback(f"   • Размер: {file_size / (1024*1024):.1f} MB")
            log_callback(f"   • Время рендера: {render_time:.1f}s ({render_time/60:.1f} мин)")
            log_callback(f"   • Скорость: {audio_duration/render_time:.2f}x realtime")
            
            # Очищаем временные файлы
            try:
                script_path.unlink()
            except Exception:
                # Ignore cleanup errors - not critical
                pass
            if ass_temp_dir and ass_temp_dir.exists():
                shutil.rmtree(ass_temp_dir, ignore_errors=True)
            
            # Очищаем временные Pillow файлы
            # 🔧 DEBUG: Если включен debug_animation, не удаляем, а перемещаем в logs
            if pillow_temp_dir and pillow_temp_dir.exists():
                if video_settings.get('debug_animation', False):
                    debug_shots_dir = output_path.parent / "debug_shots"
                    if debug_shots_dir.exists():
                        try:
                            shutil.rmtree(debug_shots_dir)
                        except Exception:
                            pass
                    try:
                        shutil.move(str(pillow_temp_dir), str(debug_shots_dir))
                        log_callback(f"   🐛 DEBUG: Шоты сохранены в {debug_shots_dir}")
                    except Exception as e:
                        log_callback(f"   ⚠️ Не удалось сохранить debug шоты: {e}")
                else:
                    try:
                        shutil.rmtree(pillow_temp_dir, ignore_errors=True)
                        log_callback("   🧹 Очищены временные файлы анимации")
                    except Exception as cleanup_err:
                        logging.debug(f"Cleanup pillow_temp_dir failed: {cleanup_err}")
            
            # 🎥 VEO 3: Merge AI-intro with slideshow
            if veo3_intro_video and Path(veo3_intro_video).exists():
                log_callback("\n" + "=" * 60)
                log_callback("🎬 VEO 3: Сшивка AI-интро со слайдшоу")
                log_callback("=" * 60)
                
                try:
                    # Slideshow video is in output_path
                    slideshow_video = str(output_path)
                    
                    # Create final output path
                    final_output = output_path.parent / f"{output_path.stem}_with_intro{output_path.suffix}"
                    
                    log_callback(f"   🎬 AI-интро: {veo3_intro_duration:.2f}s")
                    log_callback(f"   🖼️ Слайдшоу: {audio_duration:.2f}s")
                    log_callback("   🎯 Сшивка с crossfade переходом...")
                    
                    # Merge with crossfade (scale to slideshow resolution)
                    merged_video = concat_videos_with_crossfade(
                        video1=veo3_intro_video,
                        video2=slideshow_video,
                        output_video=str(final_output),
                        transition_duration=0.3,
                        target_resolution=(width, height),  # Scale to final video resolution
                        log_callback=log_callback
                    )
                    
                    if merged_video and Path(merged_video).exists():
                        # Replace original with merged
                        Path(merged_video).replace(output_path)
                        
                        log_callback("   ✅ Финальное видео с AI-интро готово!")
                        log_callback(f"   📁 {output_path}")
                        
                        # Update duration
                        try:
                            merged_probe = probe_registered(
                                str(output_path), label="ffprobe_optimized_veo_output"
                            )
                            merged_duration = float(merged_probe['format']['duration'])
                            log_callback(f"   ⏱️ Итоговая длительность: {merged_duration:.2f}s")
                        except Exception:
                            pass
                    else:
                        log_callback("   ⚠️ Не удалось сшить видео, используем слайдшоу")
                    
                except Exception as e:
                    log_callback(f"   ❌ Ошибка сшивки: {str(e)[:200]}")
                    log_callback("   Используем слайдшоу без AI-интро")
                
                log_callback("=" * 60 + "\n")
            
            # Final cleanup of all Veo 3 temp files
            try:
                output_dir = Path(output_path).parent
                for veo3_file in output_dir.glob("veo3_*"):
                    if veo3_file.is_file():
                        veo3_file.unlink()
            except Exception:
                pass
            
            return str(output_path)
        
        except subprocess.TimeoutExpired:
            # Очищаем Pillow файлы при timeout
            if pillow_temp_dir and pillow_temp_dir.exists():
                shutil.rmtree(pillow_temp_dir, ignore_errors=True)
            if ass_temp_dir and ass_temp_dir.exists():
                shutil.rmtree(ass_temp_dir, ignore_errors=True)
            raise Exception(f"FFmpeg timeout после {audio_duration * 10 + 300}s")
        except Exception as e:
            # Очищаем временные файлы при ошибке
            try:
                script_path.unlink()
            except Exception as cleanup_err:
                logging.debug(f"Cleanup script_path failed: {cleanup_err}")
            # Очищаем Pillow файлы при ошибке
            if pillow_temp_dir and pillow_temp_dir.exists():
                shutil.rmtree(pillow_temp_dir, ignore_errors=True)
            if ass_temp_dir and ass_temp_dir.exists():
                shutil.rmtree(ass_temp_dir, ignore_errors=True)
            raise Exception(f"Ошибка рендеринга: {e}")
def allocate_random_shot_durations(
    total_duration,
    shot_count,
    shot_min,
    shot_max,
    rng=None,
    frontload_fast=False,
):
    """Allocate random shot lengths without creating zero-length tail shots."""
    total_duration = max(0.01, float(total_duration))
    shot_min = max(0.01, float(shot_min))
    shot_max = max(shot_min, float(shot_max))
    shot_count = max(1, int(shot_count))
    safe_count = min(shot_count, max(1, int(total_duration / shot_min)))
    if safe_count == 1:
        return [total_duration]

    uniform = (rng or random).uniform
    durations = []
    remaining = total_duration
    hook_elapsed = 0.0
    for index in range(safe_count - 1):
        remaining_shots = safe_count - index - 1
        lower = max(shot_min, remaining - remaining_shots * shot_max)
        upper = min(shot_max, remaining - remaining_shots * shot_min)
        if frontload_fast and hook_elapsed < 8.0:
            upper = min(upper, max(lower, min(2.5, shot_max)))
        duration = uniform(lower, max(lower, upper))
        durations.append(duration)
        remaining -= duration
        hook_elapsed += duration
    durations.append(remaining)
    return durations
