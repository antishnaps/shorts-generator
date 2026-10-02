#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚡ GLITCH TRANSITIONS ENGINE
Настоящие глитч-переходы через продвинутые FFmpeg фильтры.

Поддерживаемые стили:
- rgb_split     : Хроматическая аберрация (RGB сдвиг)
- scanlines     : Горизонтальные полосы-глитч
- pixel_sort    : Пиксельная сортировка / dataglitch
- vhs           : VHS-деградация (noise + warp)
- digital_decay : Цифровой распад / digital artifacts
- chromatic     : Полный хроматический коллапс
- random        : Случайный из всех выше
"""

import random
import subprocess
import shutil
import logging
from pathlib import Path
from typing import List, Optional, Callable

from core.process_registry import run_registered

logger = logging.getLogger(__name__)

# Все доступные глитч-стили
GLITCH_STYLES = [
    'rgb_split',
    'scanlines',
    'pixel_sort',
    'vhs',
    'digital_decay',
    'chromatic',
]

GLITCH_STYLE_LABELS = {
    'rgb_split':     '🔴 RGB Сдвиг (хроматика)',
    'scanlines':     '📺 Скан-полосы (VHS)',
    'pixel_sort':    '🔲 Пиксельный глитч',
    'vhs':           '📼 VHS деградация',
    'digital_decay': '💾 Цифровой распад',
    'chromatic':     '🌈 Хроматический коллапс',
    'random':        '🎲 Случайный микс',
}

GLITCH_STYLE_DESCRIPTIONS = {
    'rgb_split':     'Смещение RGB каналов с хроматической аберрацией',
    'scanlines':     'Горизонтальные глитч-полосы в стиле VHS',
    'pixel_sort':    'Пиксельный глитч с цифровыми артефактами',
    'vhs':           'VHS-деградация с шумом и искажением',
    'digital_decay': 'Цифровой распад с блочными артефактами',
    'chromatic':     'Полный хроматический коллапс всех каналов',
    'random':        'Случайный глитч-стиль для каждого перехода',
}


def select_glitch_positions(num_transitions: int, frequency: float) -> set:
    """Spread glitch transitions across the timeline without early clustering."""
    total = max(0, int(num_transitions))
    if total <= 0:
        return set()
    count = max(1, min(total, int(round(total * max(0.0, min(1.0, frequency))))))
    if count == 1:
        return {1}
    return {
        int(round(1 + index * (total - 1) / (count - 1)))
        for index in range(count)
    }


def calculate_glitch_transition_loss(
    num_transitions: int,
    glitch_frequency: float,
    glitch_duration: float,
    base_transition_duration: float,
) -> float:
    """Calculate the exact timeline overlap used by the glitch engine."""
    total = max(0, int(num_transitions))
    glitch_count = len(select_glitch_positions(total, glitch_frequency))
    normal_count = total - glitch_count
    return (
        glitch_count * max(0.0, float(glitch_duration))
        + normal_count * max(0.0, float(base_transition_duration))
    )


class GlitchTransitionEngine:
    """
    Движок для рендера настоящих глитч-переходов через FFmpeg filter_complex.
    
    Глитч-переход работает по схеме:
    1. Генерируем N промежуточных "сломанных" кадров через сложный filter_complex
    2. Применяем как мини-видео-вставку между шотами
    3. Потребление ресурсов: высокое (CPU), но опционально
    """
    
    # Длительность глитч-вставки (сек)
    DEFAULT_GLITCH_DURATION = 0.4
    
    # Количество "глитч-кадров" (чем больше - тем дольше рендер)
    DEFAULT_GLITCH_FRAMES = 12  # ~0.4s @ 30fps
    MAX_SINGLE_PASS_SHOTS = 40
    
    def __init__(self, ffmpeg_path: str = 'ffmpeg'):
        self.ffmpeg_path = ffmpeg_path
        self._last_used_style = None
        self._styles_used = []
        self._intensity = 0.65

    def _amount(self, value: int, minimum: int = 1) -> int:
        return max(minimum, int(round(value * self._intensity)))
    
    def _pick_style(self, style: str) -> str:
        """Выбирает конкретный стиль (если 'random' - случайный, не повторяющийся)"""
        if style == 'random':
            available = [s for s in GLITCH_STYLES if s != self._last_used_style]
            chosen = random.choice(available if available else GLITCH_STYLES)
            self._last_used_style = chosen
            self._styles_used.append(chosen)
            return chosen
        chosen = style if style in GLITCH_STYLES else random.choice(GLITCH_STYLES)
        self._styles_used.append(chosen)
        return chosen

    @staticmethod
    def _effect_window(offset: float, duration: float) -> str:
        """Return a timeline expression accepted by FFmpeg filters."""
        return f"enable='between(t,{offset:.3f},{offset + duration:.3f})'"

    @staticmethod
    def _effect_label(prefix: str, offset: float) -> str:
        return f"{prefix}_{max(0, int(round(offset * 1000)))}"
    
    # ─────────────────────────────────────────────────────────────────────────
    # FILTER_COMPLEX BUILDERS — каждый возвращает (filter_str, output_label)
    # ─────────────────────────────────────────────────────────────────────────
    
    def _build_rgb_split_filter(self, in_a: str, in_b: str, w: int, h: int,
                                 t: float, dur: float) -> str:
        """
        RGB Split / хроматическая аберрация:
        Разделяет R/G/B каналы и смещает их в разные стороны с нарастанием.
        """
        amp = self._amount(30)
        vertical = self._amount(8)
        base = self._effect_label("rgb_base", t)
        window = self._effect_window(t, dur)
        return (
            f"[{in_a}][{in_b}]xfade=transition=fade:duration={dur:.3f}:offset={t:.3f}[{base}];"
            f"[{base}]rgbashift=rh={amp}:rv=-{vertical}:gh=-{amp//4}:"
            f"bh=-{amp}:bv={vertical}:edge=wrap:{window}[glitch_out]"
        ), "glitch_out"
    
    def _build_scanlines_filter(self, in_a: str, in_b: str, w: int, h: int,
                                  t: float, dur: float) -> str:
        """
        Scanlines / VHS-полосы:
        Горизонтальные полосы с разным смещением — как сбой синхронизации VHS.
        """
        noise = self._amount(22)
        base = self._effect_label("scan_base", t)
        window = self._effect_window(t, dur)
        return (
            f"[{in_a}][{in_b}]xfade=transition=hlslice:duration={dur:.3f}:offset={t:.3f}[{base}];"
            f"[{base}]noise=alls={noise}:allf=t+p:{window}[glitch_out]"
        ), "glitch_out"
    
    def _build_pixel_sort_filter(self, in_a: str, in_b: str, w: int, h: int,
                                  t: float, dur: float) -> str:
        """
        Pixel Sort / цифровой глитч:
        Рандомные блоки смещаются горизонтально — dataglitch эффект.
        """
        shift = self._amount(24)
        base = self._effect_label("pixel_base", t)
        window = self._effect_window(t, dur)
        return (
            f"[{in_a}][{in_b}]xfade=transition=pixelize:duration={dur:.3f}:offset={t:.3f}[{base}];"
            f"[{base}]chromashift=cbh={shift}:crh=-{shift}:edge=wrap:{window}[glitch_out]"
        ), "glitch_out"
    
    def _build_vhs_filter(self, in_a: str, in_b: str, w: int, h: int,
                          t: float, dur: float) -> str:
        """
        VHS Degradation:
        Noise + warp + luma smear = старая видеокассета.
        """
        noise = self._amount(25)
        shift = self._amount(14)
        base = self._effect_label("vhs_base", t)
        noisy = self._effect_label("vhs_noise", t)
        window = self._effect_window(t, dur)
        return (
            f"[{in_a}][{in_b}]xfade=transition=hlslice:duration={dur:.3f}:offset={t:.3f}[{base}];"
            f"[{base}]noise=alls={noise}:allf=t+u:{window}[{noisy}];"
            f"[{noisy}]chromashift=cbh=-{shift}:crh={shift}:edge=smear:{window}[glitch_out]"
        ), "glitch_out"
    
    def _build_digital_decay_filter(self, in_a: str, in_b: str, w: int, h: int,
                                     t: float, dur: float) -> str:
        """
        Digital Decay / цифровой распад:
        Блочные артефакты + случайные инверсии блоков.
        """
        noise = self._amount(42)
        shift = self._amount(18)
        base = self._effect_label("decay_base", t)
        decayed = self._effect_label("decay_noise", t)
        window = self._effect_window(t, dur)
        return (
            f"[{in_a}][{in_b}]xfade=transition=pixelize:duration={dur:.3f}:offset={t:.3f}[{base}];"
            f"[{base}]noise=alls={noise}:allf=t+u:{window}[{decayed}];"
            f"[{decayed}]chromashift=cbv={shift}:crv=-{shift}:edge=wrap:{window}[glitch_out]"
        ), "glitch_out"
    
    def _build_chromatic_filter(self, in_a: str, in_b: str, w: int, h: int,
                                 t: float, dur: float) -> str:
        """
        Chromatic Collapse:
        Максимальный хаос — все каналы разъезжаются по своим траекториям.
        """
        amp = self._amount(50)
        vertical = self._amount(20)
        noise = self._amount(16)
        base = self._effect_label("chrom_base", t)
        shifted = self._effect_label("chrom_shift", t)
        window = self._effect_window(t, dur)
        return (
            f"[{in_a}][{in_b}]xfade=transition=fade:duration={dur:.3f}:offset={t:.3f}[{base}];"
            f"[{base}]rgbashift=rh={amp}:rv=-{vertical}:gh=-{amp//3}:gv={vertical//2}:"
            f"bh=-{amp}:bv={vertical}:edge=wrap:{window}[{shifted}];"
            f"[{shifted}]noise=alls={noise}:allf=t+u:{window}[glitch_out]"
        ), "glitch_out"
    
    def build_filter(self, style: str, in_a: str, in_b: str,
                     w: int, h: int, offset: float, dur: float) -> tuple:
        """
        Возвращает (filter_str, output_label) для заданного стиля.
        """
        builders = {
            'rgb_split':     self._build_rgb_split_filter,
            'scanlines':     self._build_scanlines_filter,
            'pixel_sort':    self._build_pixel_sort_filter,
            'vhs':           self._build_vhs_filter,
            'digital_decay': self._build_digital_decay_filter,
            'chromatic':     self._build_chromatic_filter,
        }
        
        actual_style = self._pick_style(style)
        builder = builders.get(actual_style, self._build_rgb_split_filter)
        
        try:
            return builder(in_a, in_b, w, h, offset, dur)
        except Exception as e:
            logger.warning(f"Glitch filter build failed for '{actual_style}': {e}, falling back to rgb_split")
            return self._build_rgb_split_filter(in_a, in_b, w, h, offset, dur)
    
    def concat_with_glitch_transitions(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        glitch_style: str = 'random',
        glitch_duration: float = 0.4,
        glitch_frequency: float = 0.5,
        glitch_intensity: float = 0.65,
        base_transition_duration: float = 0.3,
        codec_settings_getter: Optional[Callable] = None,
        log_callback: Optional[Callable] = None,
    ) -> str:
        """
        Склеивает шоты с настоящими глитч-переходами.
        
        Args:
            shot_files:            Список путей к видео-файлам шотов
            output_path:           Путь выходного файла
            fps:                   FPS видео
            width, height:         Разрешение
            glitch_style:          Стиль глитча ('random', 'rgb_split', 'vhs', ...)
            glitch_duration:       Длительность глитч-перехода (сек), рекомендуется 0.3-0.6
            glitch_frequency:      Доля шотов с глитч-переходом (0.0-1.0)
            codec_settings_getter: Функция, возвращающая dict с настройками кодека
            log_callback:          Функция логирования
            
        Returns:
            Путь к выходному файлу
        """
        if log_callback is None:
            log_callback = print
        self._intensity = max(0.1, min(1.0, float(glitch_intensity)))
        self._last_used_style = None
        self._styles_used = []
        
        if len(shot_files) < 2:
            if shot_files:
                shutil.copy(shot_files[0], output_path)
            return output_path
        
        n = len(shot_files)
        if n > self.MAX_SINGLE_PASS_SHOTS:
            log_callback(
                f"   ℹ️ Глитч-переходы пропущены: {n} шотов превышают безопасный лимит "
                f"{self.MAX_SINGLE_PASS_SHOTS} для Windows. Используем стабильные стандартные переходы."
            )
            return ""

        log_callback(
            f"⚡ GLITCH ENGINE: {n} шотов, стиль='{glitch_style}', "
            f"длит.={glitch_duration:.2f}s, сила={self._intensity:.0%}"
        )
        
        # Получаем длительности шотов
        shot_durations = self._probe_durations(shot_files, log_callback)
        safe_duration = max(0.1, min(float(glitch_duration), min(shot_durations) * 0.45))
        if safe_duration < glitch_duration:
            log_callback(
                f"   ℹ️ Длительность глитча уменьшена до {safe_duration:.2f}s "
                f"для коротких шотов"
            )
        glitch_duration = safe_duration
        safe_base_duration = max(
            0.1,
            min(float(base_transition_duration), min(shot_durations) * 0.45),
        )
        if safe_base_duration < base_transition_duration:
            log_callback(
                f"   ℹ️ Длительность обычного перехода уменьшена до "
                f"{safe_base_duration:.2f}s для коротких шотов"
            )
        base_transition_duration = safe_base_duration
        
        # Определяем позиции с глитчем
        num_transitions = n - 1
        glitch_positions = select_glitch_positions(num_transitions, glitch_frequency)
        
        log_callback(f"   ⚡ Глитч-позиции: {sorted(glitch_positions)} из {num_transitions}")
        
        # Строим filter_complex
        try:
            filter_parts, final_stream = self._build_full_filter(
                shot_files, shot_durations, width, height,
                glitch_style, glitch_duration, glitch_positions, fps,
                base_transition_duration,
            )
            if self._styles_used:
                log_callback(f"   🎛️ Использованные стили: {', '.join(self._styles_used)}")
        except Exception as e:
            log_callback(f"   ⚠️ Ошибка построения glitch filter: {e}")
            logger.exception("Glitch filter build error")
            return ""
        
        filter_complex = ";".join(filter_parts)
        
        # Кодек
        codec_args = self._get_codec_args(codec_settings_getter, log_callback)
        
        # Собираем команду
        input_args = []
        for sf in shot_files:
            input_args.extend(['-hwaccel', 'auto', '-i', sf])
        
        cmd = [
            self.ffmpeg_path, '-y',
            *input_args,
            '-filter_complex', filter_complex,
            '-map', f'[{final_stream}]',
            *codec_args,
            '-pix_fmt', 'yuv420p',
            '-r', str(fps),
            output_path
        ]
        
        log_callback(f"   🔧 Запускаем GLITCH render ({len(shot_files)} шотов)...")
        
        try:
            result = run_registered(
                cmd, label="ffmpeg_glitch_transitions", capture_output=True, text=True,
                encoding='utf-8', errors='ignore', timeout=900
            )
            
            if result.returncode != 0:
                stderr = result.stderr[-500:] if result.stderr else ''
                log_callback(f"   ⚠️ Glitch render ошибка: {stderr}")
                logger.warning(f"Glitch FFmpeg error: {result.stderr[-1000:]}")
                Path(output_path).unlink(missing_ok=True)
                return ""  # Сигнал для fallback
            
            if not Path(output_path).exists():
                log_callback("   ⚠️ Glitch файл не создан")
                return ""

            expected_duration = sum(shot_durations) - calculate_glitch_transition_loss(
                num_transitions,
                glitch_frequency,
                glitch_duration,
                base_transition_duration,
            )
            actual_duration = self._probe_durations([output_path], log_callback)[0]
            duration_tolerance = max(0.5, min(2.0, expected_duration * 0.02))
            if actual_duration < expected_duration - duration_tolerance:
                log_callback(
                    f"   ⚠️ Glitch render неполный: {actual_duration:.1f}s вместо "
                    f"{expected_duration:.1f}s. Используем стабильные переходы."
                )
                Path(output_path).unlink(missing_ok=True)
                return ""
            
            size_mb = Path(output_path).stat().st_size / 1_048_576
            log_callback(f"   ✅ Glitch transitions готово! ({size_mb:.1f} MB)")
            log_callback(
                f"   ⚡ Применено глитч-переходов: "
                f"{len(glitch_positions)}/{num_transitions}"
            )
            return output_path
            
        except subprocess.TimeoutExpired:
            log_callback("   ⚠️ Glitch render таймаут (>15мин), используем стандартные переходы")
            Path(output_path).unlink(missing_ok=True)
            return ""
        except Exception as e:
            log_callback(f"   ⚠️ Glitch render exception: {e}")
            Path(output_path).unlink(missing_ok=True)
            return ""
    
    def _build_full_filter(
        self,
        shot_files: List[str],
        shot_durations: List[float],
        width: int,
        height: int,
        glitch_style: str,
        glitch_duration: float,
        glitch_positions: set,
        fps: int,
        base_transition_duration: float = 0.3,
    ):
        """Строит весь filter_complex для цепочки шотов с глитч-переходами"""
        n = len(shot_files)
        filter_parts = []
        
        # Нормализуем каждый входной поток
        for i in range(n):
            filter_parts.append(
                f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p[vin{i}]"
            )
        
        current_stream = "vin0"
        
        for i in range(1, n):
            next_stream = f"vin{i}"
            out_stream = f"xf{i}"
            
            # Накопленный offset
            cum_dur = sum(shot_durations[:i])
            # Вычитаем длительности предыдущих переходов
            prev_transitions_time = sum(
                glitch_duration if j in glitch_positions else base_transition_duration
                for j in range(1, i)
            )
            
            if i in glitch_positions:
                # ГЛИТЧ переход
                actual_dur = glitch_duration
                offset = max(0.0, cum_dur - prev_transitions_time - actual_dur)
                
                flt, out_lbl = self.build_filter(
                    glitch_style, current_stream, next_stream,
                    width, height, offset, actual_dur
                )
                # Переименовываем финальный лейбл фильтра в out_stream
                flt = flt.replace(f"[{out_lbl}]", f"[{out_stream}]", 1)
                filter_parts.extend(flt.split(";"))
            else:
                # Между глитчами сохраняем обычный плавный переход.
                actual_dur = base_transition_duration
                offset = max(0.0, cum_dur - prev_transitions_time - actual_dur)
                filter_parts.append(
                    f"[{current_stream}][{next_stream}]xfade=transition=fade:"
                    f"duration={actual_dur:.3f}:offset={offset:.3f}[{out_stream}]"
                )
            
            current_stream = out_stream
        
        return filter_parts, current_stream
    
    def _get_ffprobe_path(self) -> str:
        """Resolve ffprobe next to ffmpeg without changing directory names."""
        ffmpeg_path = Path(self.ffmpeg_path)
        ffprobe_name = 'ffprobe.exe' if ffmpeg_path.suffix.lower() == '.exe' else 'ffprobe'
        sibling = ffmpeg_path.with_name(ffprobe_name)
        if sibling.exists():
            return str(sibling)
        return shutil.which(ffprobe_name) or ffprobe_name

    def _probe_durations(self, shot_files: List[str], log_callback: Callable) -> List[float]:
        """Параллельный ffprobe для получения длительностей"""
        from concurrent.futures import ThreadPoolExecutor
        
        ffprobe_bin = self._get_ffprobe_path()
        
        def _probe(f):
            try:
                r = run_registered(
                    [ffprobe_bin, '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', f],
                    label="ffprobe_glitch_transition_shot",
                    capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=30
                )
                duration = float(r.stdout.strip()) if r.returncode == 0 else 0.0
                return duration if duration > 0 else None
            except Exception:
                return None
        
        with ThreadPoolExecutor(max_workers=min(len(shot_files), 8)) as pool:
            durations = list(pool.map(_probe, shot_files))

        failed = [index for index, duration in enumerate(durations) if duration is None]
        if failed:
            raise RuntimeError(
                f"Не удалось определить длительность {len(failed)} шотов: {failed[:8]}"
            )
        
        return durations
    
    def _get_codec_args(self, codec_settings_getter: Optional[Callable], log_callback: Callable) -> List[str]:
        """Получает аргументы кодека"""
        try:
            if codec_settings_getter:
                cs = codec_settings_getter(use_nvenc=True, log_callback=None)
                if cs.get('vcodec') == 'h264_nvenc':
                    return [
                        '-c:v', 'h264_nvenc',
                        '-preset', 'p2',  # Быстрый preset для промежуточного файла
                        '-rc', 'vbr',
                        '-cq', '24',
                        '-b:v', '8M',
                    ]
        except Exception:
            pass
        # Fallback CPU
        return ['-c:v', 'libx264', '-preset', 'fast', '-crf', '20']


# ─────────────────────────────────────────────────────────────────────────────
# Удобная функция для использования из других модулей
# ─────────────────────────────────────────────────────────────────────────────

def get_glitch_style_labels() -> dict:
    """Возвращает словарь {style_key: display_label} для GUI"""
    return GLITCH_STYLE_LABELS


def get_glitch_styles() -> List[str]:
    """Возвращает список доступных стилей (включая 'random')"""
    return ['random'] + GLITCH_STYLES
