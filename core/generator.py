#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Main shorts generator class

Основной модуль для генерации коротких видео (Shorts/Reels/TikTok).

Возможности:
- Генерация текста через Gemini AI
- AI генерация изображений (Google Gemini)
- Fallback на Pixabay для изображений (TASK 12)
- Автоподбор музыки по настроению через Freesound (TASK 12)
- YouTube клипы микширование
- Veo 3 AI-интро
- Параллельная генерация
- Субтитры и TTS озвучка
"""

# Generator V5 - Improved Cleanup

import time
from pathlib import Path
import logging
import traceback
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, wait, FIRST_COMPLETED
import re
import json
import hashlib
import math
import threading

from .text_generator import TextGenerator
from .image_processor import ImageProcessor
from .video_renderer import VideoRenderer
from .file_logger import create_logger, DualLogger
from .audio_processor import AudioProcessor
from .utils import (
    TempFileManager,
    cleanup_generated_folder,
    get_enabled_metadata_dirs,
    normalize_language,
)
from .cost_tracker import reset_tracker, log_cost_summary
from .settings_schema import (
    normalize_video_settings,
    normalize_subtitle_settings,
    normalize_audio_settings,
    normalize_overlay_settings,
    normalize_youtube_mixer_settings,
)
from .subtitle_text import join_subtitle_units, split_subtitle_units
from .media_distribution import MediaDistributionPlanner
from .opening_hooks import build_opening_hook_package, replace_generic_script_opening
from .title_strategy import (
    is_near_duplicate_title,
    looks_catalog_like,
    subject_first_prompt_rules,
    title_has_specific_evidence,
)

# 🎯 Глобальный пул потоков для ограничения вложенной параллельности
try:
    from .utils import get_global_thread_pool
    _USE_GLOBAL_POOL = True
except ImportError:
    _USE_GLOBAL_POOL = False


class GenerationCancelledError(RuntimeError):
    """Raised after a user-requested stop has preserved resumable batch state."""


class ShortsGenerator:
    """
    Orchestrates the video generation process.
    
    Основной класс для генерации коротких видео. Координирует работу
    всех компонентов: генерация текста, изображений, аудио, рендеринг.
    
    Attributes:
        text_generator: Генератор текста через Gemini AI
        image_processor: Обработчик изображений (AI + Pixabay fallback)
        audio_processor: Обработчик аудио (TTS + музыка)
        video_renderer: Рендерер финального видео
        stop_flag: Флаг для остановки генерации
        api_key: Gemini API ключ
        
    Example:
        >>> generator = ShortsGenerator()
        >>> generator.generate_shorts(
        ...     source_data={'theme': 'Космос', 'language': 'Russian'},
        ...     num_videos=5,
        ...     music_path='music/',
        ...     api_key='AIza...',
        ...     video_settings={'duration': 60, 'width': 1080, 'height': 1920},
        ...     output_path='generated/',
        ...     progress_callback=lambda p: print(f'{p}%'),
        ...     log_callback=print,
        ...     subtitle_settings={'enabled': True}
        ... )
    """

    def __init__(self):
        """Инициализация генератора и всех компонентов."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self.text_generator = TextGenerator()
        self.image_processor = ImageProcessor()
        self.audio_processor = AudioProcessor()
        self.video_renderer = VideoRenderer()
        self.media_distribution = MediaDistributionPlanner()
        self.stop_flag = False
        self.api_key = None  # Will be set on first generate call
        self._sessions_warmed = False  # Track if sessions are pre-warmed
        self._title_lock = threading.Lock()
        self._used_titles = set()
        self._used_title_values = []
    
    def warmup_api_sessions(self, api_key: str, log_callback=None):
        """
        Pre-warm API sessions for faster first requests - TURBO MODE.
        
        Прогревает HTTP сессии для ускорения первых запросов к API.
        
        Args:
            api_key: Gemini API ключ
            log_callback: Функция для логирования
        """
        if self._sessions_warmed:
            return
        
        try:
            if log_callback:
                log_callback("🔥 Прогрев API сессий...")
            
            # Warmup image generator session
            from core.google_image_generator import GoogleImageGenerator
            img_gen = GoogleImageGenerator()
            img_gen.warmup_session(api_key)
            
            self._sessions_warmed = True
            if log_callback:
                log_callback("✅ API сессии готовы")
        except Exception as e:
            if log_callback:
                log_callback(f"⚠️ Warmup не удался: {e}")

    def _get_manifest_path(self, output_dir: Path) -> Path:
        return output_dir / "batch_manifest.json"

    def _theme_hash(self, theme: str) -> str:
        return hashlib.sha256(str(theme).encode('utf-8')).hexdigest()[:16]

    _BATCH_FINGERPRINT_VERSION = 2
    _BATCH_PATH_FIELDS = (
        'path', 'folder', 'file', 'source_video', 'first_shot_image',
    )

    @classmethod
    def _is_batch_secret_field(cls, key: object) -> bool:
        normalized = str(key or '').strip().casefold().replace('-', '_')
        parts = {part for part in normalized.split('_') if part}
        if normalized in {'apikey', 'api_key', 'key', 'password', 'authorization'}:
            return True
        if normalized.endswith((
            '_api_key', '_api_keys',
            '_access_key', '_access_keys',
            '_private_key', '_private_keys',
        )):
            return True
        return bool(
            parts.intersection(
                {'token', 'secret', 'password', 'cookie', 'cookies', 'credential', 'authorization'}
            )
        )

    @classmethod
    def _is_batch_path_field(cls, key: object) -> bool:
        normalized = str(key or '').strip().casefold().replace('-', '_')
        parts = {part for part in normalized.split('_') if part}
        return (
            normalized in cls._BATCH_PATH_FIELDS
            or bool(parts.intersection({'path', 'folder', 'directory', 'dir', 'file', 'files'}))
        )

    @staticmethod
    def _batch_source_identity(value: object) -> dict:
        """Return a stable, content-sensitive identity without reading large media."""
        raw_path = str(value or '').strip()
        path = Path(raw_path).expanduser()
        try:
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError):
            resolved = path.absolute()
        identity = {'path': str(resolved).replace('\\', '/'), 'exists': path.exists()}
        try:
            if path.is_file():
                stat = path.stat()
                identity.update({
                    'kind': 'file',
                    'size': int(stat.st_size),
                    'mtime_ns': int(stat.st_mtime_ns),
                })
            elif path.is_dir():
                inventory = []
                for item in sorted(
                    (candidate for candidate in path.rglob('*') if candidate.is_file()),
                    key=lambda candidate: candidate.relative_to(path).as_posix().casefold(),
                ):
                    try:
                        stat = item.stat()
                        inventory.append((
                            item.relative_to(path).as_posix(),
                            int(stat.st_size),
                            int(stat.st_mtime_ns),
                        ))
                    except OSError:
                        inventory.append((item.relative_to(path).as_posix(), None, None))
                inventory_bytes = json.dumps(
                    inventory, ensure_ascii=False, separators=(',', ':')
                ).encode('utf-8')
                identity.update({
                    'kind': 'directory',
                    'files': len(inventory),
                    'inventory_sha256': hashlib.sha256(inventory_bytes).hexdigest(),
                })
            else:
                identity['kind'] = 'missing'
        except (OSError, RuntimeError):
            identity['kind'] = 'unreadable'
        return identity

    @classmethod
    def _canonical_batch_value(cls, value: object, field_name: str = ''):
        """Canonicalize settings for resume matching while excluding credentials."""
        if callable(value):
            return None
        if value is None or isinstance(value, (bool, int, str)):
            if isinstance(value, str) and value.strip() and cls._is_batch_path_field(field_name):
                return cls._batch_source_identity(value)
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else str(value)
        if isinstance(value, Path):
            return cls._batch_source_identity(value)
        if isinstance(value, bytes):
            return {
                'bytes': len(value),
                'sha256': hashlib.sha256(value).hexdigest(),
            }
        if isinstance(value, dict):
            canonical = {}
            for key in sorted(value, key=lambda item: str(item).casefold()):
                if cls._is_batch_secret_field(key) or str(key).startswith('_'):
                    continue
                normalized = cls._canonical_batch_value(value[key], str(key))
                if normalized is not None or value[key] is None:
                    canonical[str(key)] = normalized
            return canonical
        if isinstance(value, (list, tuple)):
            return [cls._canonical_batch_value(item, field_name) for item in value]
        if isinstance(value, (set, frozenset)):
            canonical = [cls._canonical_batch_value(item, field_name) for item in value]
            return sorted(
                canonical,
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
            )
        if hasattr(value, '__dict__'):
            public_fields = {
                key: item for key, item in vars(value).items()
                if not str(key).startswith('_')
            }
            return {
                'type': f'{type(value).__module__}.{type(value).__qualname__}',
                'fields': cls._canonical_batch_value(public_fields),
            }
        return str(value)

    @staticmethod
    def _effective_batch_settings(
        *,
        video_settings: dict,
        subtitle_settings: dict,
        audio_settings: dict,
        overlay_settings: dict,
        youtube_mixer_settings: dict,
        music_path: str = '',
        media_path: str = None,
        use_ai_image_generation: bool = False,
        use_triple_template: bool = False,
        unlimited_images: bool = False,
        num_unique_images: int = 5,
        strict_theme_following: bool = True,
        enable_scene_variety: bool = True,
        image_model: str = '',
        veo3_settings: dict = None,
        custom_images_folder: str = None,
        use_only_custom_images: bool = False,
        use_image_cache: bool = False,
        save_to_image_cache: bool = False,
        image_pool_settings: dict = None,
        avatar_settings: dict = None,
        final_output_settings: dict = None,
    ) -> dict:
        final_output_settings = final_output_settings or {}
        if 'user_settings' in final_output_settings:
            final_output_settings = final_output_settings.get('user_settings') or {}
        return {
            'video_settings': video_settings,
            'subtitle_settings': subtitle_settings,
            'audio_settings': audio_settings,
            'overlay_settings': overlay_settings,
            'youtube_mixer_settings': youtube_mixer_settings,
            'music_path': music_path,
            'media_path': media_path,
            'image_settings': {
                'use_ai_image_generation': use_ai_image_generation,
                'use_triple_template': use_triple_template,
                'unlimited_images': unlimited_images,
                'num_unique_images': num_unique_images,
                'strict_theme_following': strict_theme_following,
                'enable_scene_variety': enable_scene_variety,
                'image_model': image_model,
                'custom_images_folder': custom_images_folder,
                'use_only_custom_images': use_only_custom_images,
                'use_image_cache': use_image_cache,
                'save_to_image_cache': save_to_image_cache,
                'image_pool_settings': image_pool_settings or {},
            },
            'veo3_settings': veo3_settings or {'enabled': False},
            'avatar_settings': avatar_settings or {'enabled': False},
            'final_output_settings': final_output_settings,
        }

    @classmethod
    def _batch_hash(
        cls,
        themes: list,
        source_data: dict,
        video_settings: dict,
        effective_settings: dict = None,
    ) -> str:
        payload = {
            'fingerprint_version': cls._BATCH_FINGERPRINT_VERSION,
            'themes': [str(theme) for theme in themes],
            'source_data': source_data or {},
            'effective_settings': effective_settings or {'video_settings': video_settings or {}},
        }
        canonical = cls._canonical_batch_value(payload)
        serialized = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        ).encode('utf-8')
        return hashlib.sha256(serialized).hexdigest()[:16]

    def _atomic_write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + '.tmp')
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp_path.replace(path)

    def _load_json_file(self, path: Path, default=None):
        if default is None:
            default = {}
        try:
            if path.exists():
                return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return default
        return default

    def _init_batch_manifest(
        self,
        output_dir: Path,
        themes: list,
        source_data: dict,
        video_settings: dict,
        effective_settings: dict = None,
    ) -> dict:
        manifest_path = self._get_manifest_path(output_dir)
        batch_hash = self._batch_hash(
            themes, source_data, video_settings, effective_settings
        )
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        existing = self._load_json_file(manifest_path, {})
        if existing.get('batch_hash') == batch_hash:
            existing['updated_at'] = now
            existing.setdefault('completed', {})
            existing.setdefault('failed', {})
            existing.setdefault('skipped', {})
            self._atomic_write_json(manifest_path, existing)
            return existing

        manifest = {
            'version': 2,
            'fingerprint_version': self._BATCH_FINGERPRINT_VERSION,
            'batch_hash': batch_hash,
            'created_at': now,
            'updated_at': now,
            'main_theme': source_data.get('theme', ''),
            'language': source_data.get('language', ''),
            'total': len(themes),
            'themes': {str(i + 1): {'theme': str(t), 'hash': self._theme_hash(t)} for i, t in enumerate(themes)},
            'settings': {
                'duration': video_settings.get('duration'),
                'width': video_settings.get('width'),
                'height': video_settings.get('height'),
                'fps': video_settings.get('fps'),
            },
            'completed': {},
            'failed': {},
            'skipped': {},
        }
        self._atomic_write_json(manifest_path, manifest)
        return manifest

    def _update_batch_manifest(self, output_dir: Path, video_num: int, status: str, theme: str, filename: str = None, error: str = None) -> None:
        manifest_path = self._get_manifest_path(output_dir)
        if not hasattr(self.__class__, '_resume_lock'):
            import threading
            self.__class__._resume_lock = threading.RLock()
        with self.__class__._resume_lock:
            manifest = self._load_json_file(manifest_path, {})
            if not manifest:
                return
            now = time.strftime('%Y-%m-%d %H:%M:%S')
            entry = {
                'theme': str(theme),
                'theme_hash': self._theme_hash(theme),
                'updated_at': now,
            }
            if filename:
                entry['filename'] = filename
            if error:
                entry['error'] = str(error)[:500]
            video_key = str(video_num)
            for bucket in ('completed', 'failed', 'skipped'):
                manifest.setdefault(bucket, {}).pop(video_key, None)
            manifest.setdefault(status, {})[video_key] = entry
            manifest['updated_at'] = now
            self._atomic_write_json(manifest_path, manifest)

    @staticmethod
    def _retry_with_backoff(func, max_attempts=3, base_delay=1.0, log_callback=None):
        """
        Retry a function with exponential backoff.
        
        Args:
            func: Function to retry (should be callable and return a value)
            max_attempts: Maximum number of retry attempts
            base_delay: Initial delay in seconds (doubles after each attempt)
            log_callback: Optional logging callback
        
        Returns:
            Result of func if successful, None if all attempts fail
        """
        for attempt in range(max_attempts):
            try:
                return func()
            except Exception as e:
                if attempt < max_attempts - 1:
                    delay = base_delay * (2 ** attempt)  # Exponential backoff
                    if log_callback:
                        log_callback(f"⚠️ Попытка {attempt + 1}/{max_attempts} не удалась: {e}. Повтор через {delay:.1f}s...")
                    time.sleep(delay)
                else:
                    if log_callback:
                        log_callback(f"❌ Все {max_attempts} попытки исчерпаны: {e}")
                    return None
        return None

    def _get_themes(self, source_data: dict, num_videos: int, log_callback) -> list:
        """Generate subtopics from main theme using Gemini AI OR use custom texts
        
        ЛОГИКА:
        - Если custom_texts предоставлены: используем их вместо AI генерации
        - strict_text_theme=True: генерируем КОНКРЕТНЫЕ примеры из темы
          Например: "космические миссии" -> ["Apollo 11", "Вояджер-1", "Марсоход Curiosity", ...]
        - strict_text_theme=False: генерируем разнообразные подтемы (старое поведение)
        """
        # 📝 НОВОЕ: Проверка на custom texts
        custom_texts = source_data.get('custom_texts')
        if custom_texts:
            log_callback(f"📝 Используются кастомные тексты: {len(custom_texts)} файлов")
            # Возвращаем titles из custom texts
            themes = [text.title for text in custom_texts]
            for i, theme in enumerate(themes, 1):
                log_callback(f"   {i}. {theme}")
            return themes
        
        # Стандартная AI генерация тем
        main_theme = source_data.get('theme', 'интересные факты')
        language = normalize_language(source_data.get('language', 'Russian'))
        content_style = source_data.get('content_style', 'viral')
        strict_text_theme = source_data.get('strict_text_theme', True)
        
        log_callback(f"🤖 Генерация {num_videos} подтем для '{main_theme}'...")
        log_callback(f"🌍 Язык: {language}")
        
        if strict_text_theme:
            # 🎯 НОВЫЙ РЕЖИМ: Генерируем КОНКРЕТНЫЕ примеры из темы
            log_callback("🎯 Режим: КОНКРЕТНЫЕ ПРИМЕРЫ (строгое следование теме)")
            return self.text_generator.generate_concrete_examples(
                main_theme, num_videos, self.api_key, log_callback, language
            )
        else:
            # Старый режим: разнообразные подтемы
            style_emoji = "🔥" if content_style == 'viral' else "📚"
            style_name = "Вирусный" if content_style == 'viral' else "Серьезный"
            log_callback(f"{style_emoji} Стиль: {style_name}")
            return self.text_generator.generate_subtopics(main_theme, num_videos, self.api_key, log_callback, language, content_style)

    def generate_shorts(self, source_data: dict, num_videos: int, music_path: str, api_key: str,
                        video_settings: dict, output_path: str, progress_callback,
                        log_callback, subtitle_settings: dict, media_path: str = None,
                        use_ai_image_generation: bool = False, google_ai_api_key: str = None,
                        overlay_settings: dict = None, worker=None, use_triple_template=False,
                        audio_settings: dict = None, unlimited_images: bool = False, num_unique_images: int = 5,
                        strict_theme_following: bool = True, enable_scene_variety: bool = True,
                        image_model: str = "gemini-3.1-flash-image", veo3_settings: dict = None,
                        youtube_mixer_settings: dict = None, custom_images_folder: str = None,
                        use_only_custom_images: bool = False,
                        use_image_cache: bool = False, save_to_image_cache: bool = False, image_callback=None,
                        image_pool_settings: dict = None, avatar_settings: dict = None,
                        final_output_settings: dict = None, publish_callback=None):
        video_settings = normalize_video_settings(video_settings)
        subtitle_settings = normalize_subtitle_settings(subtitle_settings)
        audio_settings = normalize_audio_settings(audio_settings)
        overlay_settings = normalize_overlay_settings(overlay_settings)
        youtube_mixer_settings = normalize_youtube_mixer_settings(youtube_mixer_settings)
        visual_source_error = self._validate_visual_source_config(youtube_mixer_settings)
        if visual_source_error:
            log_callback(f"❌ {visual_source_error}")
            raise ValueError(visual_source_error)
        from core.visual_source_manager import reset_visual_batch_state
        reset_visual_batch_state()
        
        
        # 💰 Сброс трекера стоимости для новой генерации
        reset_tracker()
        
        # 🔥 TURBO: Pre-warm API sessions for faster requests
        self.api_key = api_key
        if use_ai_image_generation and google_ai_api_key:
            self.warmup_api_sessions(google_ai_api_key, log_callback)
    
        # ✅ Проверка доступного места на диске
        import shutil
        output_dir = Path(output_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        disk_usage = shutil.disk_usage(output_dir)
        free_gb = disk_usage.free / (1024**3)
        
        # Оценка необходимого места: ~500MB на видео
        required_gb = num_videos * 0.5
        
        log_callback("💾 Проверка места на диске:")
        log_callback(f"   Свободно: {free_gb:.1f} GB")
        log_callback(f"   Требуется: ~{required_gb:.1f} GB")
        
        if free_gb < required_gb:
            error_msg = f"❌ Недостаточно места на диске! Свободно: {free_gb:.1f} GB, требуется: ~{required_gb:.1f} GB"
            log_callback(error_msg)
            raise Exception(error_msg)
        
        if free_gb < 1.0:
            log_callback(f"⚠️ ВНИМАНИЕ: Мало места на диске ({free_gb:.1f} GB). Рекомендуется освободить место.")
        
        # Pass new image settings to _generate_single_video
        kwargs = {
            'music_path': music_path,
            'video_settings': video_settings,
            'subtitle_settings': subtitle_settings,
            'media_path': media_path,
            'use_ai_image_generation': use_ai_image_generation,
            'overlay_settings': overlay_settings,
            'use_triple_template': use_triple_template,
            'log_callback': log_callback,
            'audio_settings': audio_settings,
            'google_ai_api_key': google_ai_api_key,
            'api_key': api_key,
            'unlimited_images': unlimited_images,
            'num_unique_images': num_unique_images,
            'language': normalize_language(source_data.get('language', 'Russian')),  # Передаём язык
            'strict_theme_following': strict_theme_following,  # ✅ Передаём настройку строгого следования теме (изображения)
            'strict_text_theme': source_data.get('strict_text_theme', True),  # ✅ Передаём настройку строгого следования теме (текст)
            'enable_scene_variety': enable_scene_variety,  # ✅ Система разнообразия сцен
            'image_model': image_model,  # 🎨 Модель для генерации изображений
            'veo3_settings': veo3_settings or {'enabled': False},  # 🎥 Настройки Veo 3
            'youtube_mixer_settings': youtube_mixer_settings or {'enabled': False},  # 🎬 YouTube Mixer
            'source_data': source_data,  # 🎭 Передаём source_data для viral настроек (seamless_loop, comment_bait)
            'batch_total_videos': num_videos,  # 🆕 Количество видео в батче для YouTube Mixer
            'custom_images_folder': custom_images_folder,  # 📁 Папка с пользовательскими изображениями
            'use_only_custom_images': use_only_custom_images,  # ✅ Использовать ТОЛЬКО свои картинки
            'use_image_cache': use_image_cache,  # 💾 Использовать кэш изображений
            'save_to_image_cache': save_to_image_cache,  # 💾 Сохранять в кэш
            'image_callback': image_callback,  # 🖼️ Callback для превью
            'image_pool_settings': image_pool_settings or {'use_image_pool': False},  # 🖼️ Настройки пула изображений
            'avatar_settings': avatar_settings or {'enabled': False},
            'publish_callback': publish_callback,
        }
        batch_effective_settings = self._effective_batch_settings(
            video_settings=video_settings,
            subtitle_settings=subtitle_settings,
            audio_settings=audio_settings,
            overlay_settings=overlay_settings,
            youtube_mixer_settings=youtube_mixer_settings,
            music_path=music_path,
            media_path=media_path,
            use_ai_image_generation=use_ai_image_generation,
            use_triple_template=use_triple_template,
            unlimited_images=unlimited_images,
            num_unique_images=num_unique_images,
            strict_theme_following=strict_theme_following,
            enable_scene_variety=enable_scene_variety,
            image_model=image_model,
            veo3_settings=veo3_settings,
            custom_images_folder=custom_images_folder,
            use_only_custom_images=use_only_custom_images,
            use_image_cache=use_image_cache,
            save_to_image_cache=save_to_image_cache,
            image_pool_settings=image_pool_settings,
            avatar_settings=avatar_settings,
            final_output_settings=final_output_settings,
        )
        
        self.output_dir = Path(output_path)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._reset_title_registry(self.output_dir)
        
        self.google_ai_api_key = google_ai_api_key
        self.api_key = api_key
        self.text_generator.api_key = api_key  # Передаём API ключ в text_generator

        # 📝 Создаём автоматический логгер
        theme_name = source_data.get('theme', 'generation')
        file_logger = create_logger(
            output_dir=str(Path(__file__).resolve().parent.parent),
            video_name=f"batch_{num_videos}videos",
            gui_callback=log_callback
        )
        
        # Логируем настройки
        file_logger.log_section("ПАРАМЕТРЫ ГЕНЕРАЦИИ")
        file_logger.log(f"Тема: {theme_name}")
        file_logger.log(f"Количество видео: {num_videos}")
        file_logger.log(f"Разрешение: {video_settings.get('width')}x{video_settings.get('height')}")
        file_logger.log(f"FPS: {video_settings.get('fps')}")
        file_logger.log(f"Длительность: {video_settings.get('duration')}s")
        file_logger.log(f"AI генерация изображений: {'✓' if use_ai_image_generation else '✗'}")
        file_logger.log(f"Veo 3 AI-интро: {'✓' if veo3_settings and veo3_settings.get('enabled') else '✗'}")
        file_logger.log(f"Язык: {normalize_language(source_data.get('language', 'Russian'))}")
        
        # Заменяем log_callback на dual logger
        log_callback = file_logger
        kwargs['log_callback'] = file_logger

        log_callback(f"🎯 ЗАПУСК generate_shorts: source='{source_data.get('type', 'Автоматически')}', num_videos={num_videos}")
        # Use proper validation for API key check
        gemini_valid = TextGenerator._is_valid_gemini_api_key(self.api_key) if self.api_key else False
        log_callback(f"🤖 API ключи: Gemini={'✓' if gemini_valid else '✗'}, Google AI={'✓' if self.google_ai_api_key else '✗'}")
        log_callback(f"📁 Выходная директория: {self.output_dir}")

        source_data.setdefault('metadata_theme', source_data.get('theme', theme_name))

        try:
            themes = self._get_themes(source_data, num_videos, log_callback)
        except ValueError as e:
            # More specific error message for API key issues
            error_msg = str(e)
            if "Gemini API ключ" in error_msg or "API ключ" in error_msg:
                log_callback(f"❌ Не удалось получить темы: {error_msg}")
                log_callback("💡 Проверьте, что Gemini API ключ начинается с 'AIza' и содержит минимум 20 символов.")
            else:
                log_callback(f"❌ Не удалось получить темы: {error_msg}")
            return
        except Exception as e:
            log_callback(f"❌ Не удалось получить темы: {e}")
            return
        
        batch_manifest = self._init_batch_manifest(
            self.output_dir,
            themes,
            source_data,
            video_settings,
            batch_effective_settings,
        )
        manifest_completed = batch_manifest.get('completed', {})

        failed_count = 0
        completed_count = 0
        processed_count = 0
        cancelled = False
        batch_video_max_attempts = self._batch_video_max_attempts(source_data)
        if batch_video_max_attempts > 1:
            log_callback(
                f"🛟 Контроль полноты партии: до {batch_video_max_attempts} попыток на каждый ролик"
            )
        for i, theme in enumerate(themes):
            if worker and worker.stop_flag:
                log_callback("🛑 Генерация остановлена пользователем.")
                cancelled = True
                break

            video_num = i + 1

            manifest_entry = manifest_completed.get(str(video_num), {})
            saved_filename = manifest_entry.get('filename')
            saved_path = self.output_dir / saved_filename if saved_filename else None
            if saved_path and saved_path.is_file():
                log_callback(f"⏭️ Видео #{video_num} уже готово: {saved_path.name}")
                self._update_batch_manifest(
                    self.output_dir,
                    video_num,
                    'completed',
                    theme,
                    filename=saved_path.name,
                )
                if progress_callback:
                    progress_callback(int((video_num / max(1, len(themes))) * 100))
                completed_count += 1
                processed_count += 1
                continue
            
            # 📊 Granular progress: set current video for stage-based progress
            if worker and hasattr(worker, 'set_current_video'):
                worker.set_current_video(video_num)
            else:
                # Fallback: simple progress based on video count
                progress = int((video_num / num_videos) * 100)
                if progress_callback:
                    progress_callback(progress)
            
            log_callback(f"\n{'='*20} ВИДЕО #{video_num}/{num_videos}: Тема - '{theme}' {'='*20}")
            
            try:
                self._run_batch_video_attempts(
                    lambda: self._generate_single_video(
                        theme=theme,
                        video_num=video_num,
                        output_dir=self.output_dir,
                        worker=worker,  # Pass worker for granular progress
                        **kwargs
                    ),
                    video_num=video_num,
                    theme=theme,
                    max_attempts=batch_video_max_attempts,
                    log_callback=log_callback,
                    should_stop=lambda: bool(worker and worker.stop_flag),
                )
                completed_count += 1
                processed_count += 1
            except Exception as error:
                if worker and worker.stop_flag:
                    cancelled = True
                    log_callback(
                        f"🛑 Видео #{video_num} прервано по запросу пользователя; "
                        "оно останется ожидающим для продолжения партии."
                    )
                    break
                failed_count += 1
                processed_count += 1
                self._update_batch_manifest(
                    self.output_dir,
                    video_num,
                    'failed',
                    theme,
                    error=error,
                )
                log_callback(f"⚠️ Видео #{video_num} пропущено после ошибки: {error}")
                log_callback("➡️ Партия продолжает генерацию следующего видео.")
                continue

        total_count = len(themes)
        pending_count = max(0, total_count - processed_count)
        cancelled = bool(cancelled and pending_count > 0)
        full_success = not cancelled and failed_count == 0 and pending_count == 0

        if cancelled:
            log_callback(
                f"\n🛑 Партия остановлена: готово {completed_count}/{total_count}, "
                f"ошибок {failed_count}, ожидают {pending_count}."
            )
        elif failed_count or pending_count:
            log_callback(
                f"\n⚠️ Партия завершена не полностью: готово {completed_count}/{total_count}, "
                f"ошибок {failed_count}, ожидают {pending_count}."
            )
        else:
            log_callback("\n🎉 Генерация всех видео завершена!")
        
        # 💰 Вывод стоимости генерации
        log_cost_summary(log_callback)
        
        # 🧹 Очистка YouTube кэша после завершения генерации
        youtube_mixer_settings = kwargs.get('youtube_mixer_settings', {})
        if youtube_mixer_settings and youtube_mixer_settings.get('enabled', False):
            try:
                from core.youtube.batch_cache import clear_batch_cache
                # Очищаем только память текущей партии. Исходные видео остаются
                # в ограниченном дисковом кэше для ускорения следующих запусков.
                clear_batch_cache()
            except Exception as cleanup_error:
                log_callback(f"⚠️ Не удалось очистить YouTube кэш: {cleanup_error}")
        
        if isinstance(log_callback, DualLogger):
            log_callback.finalize()

        cleanup_generated_folder(
            str(self.output_dir),
            file_logger.gui_callback if isinstance(file_logger, DualLogger) else log_callback,
            keep_images=False,
            skip_youtube_clips=False,
            keep_youtube_download_cache=True,
            keep_metadata=True,
            metadata_dirs_to_keep=get_enabled_metadata_dirs(final_output_settings),
            keep_recovery_files=not full_success,
        )
        if cancelled:
            raise GenerationCancelledError(
                f"Генерация остановлена пользователем: создано {completed_count}/{total_count}, "
                f"ошибок {failed_count}, осталось {pending_count}. "
                "Партия сохранена для продолжения."
            )
        if failed_count or pending_count:
            raise RuntimeError(
                f"Партия не завершена полностью: создано {completed_count}/{total_count}, "
                f"ошибок {failed_count}, осталось {pending_count}. "
                "Незавершённые номера сохранены в batch manifest для повтора."
            )

    def generate_shorts_parallel(self, source_data: dict, num_videos: int, music_path: str, api_key: str,
                               video_settings: dict, output_path: str, progress_callback,
                               log_callback, subtitle_settings: dict, media_path: str = None,
                               use_ai_image_generation: bool = False, google_ai_api_key: str = None,
                               overlay_settings: dict = None, worker=None, use_triple_template=False,
                               audio_settings: dict = None, num_workers: int = 4, unlimited_images: bool = False, num_unique_images: int = 5,
                               strict_theme_following: bool = True, force_parallel: bool = False, enable_scene_variety: bool = True,
                               image_model: str = "gemini-3.1-flash-image", veo3_settings: dict = None,
                               youtube_mixer_settings: dict = None, custom_images_folder: str = None,
                               use_only_custom_images: bool = False,
                               use_image_cache: bool = False, save_to_image_cache: bool = False, image_callback=None,
                               image_pool_settings: dict = None, avatar_settings: dict = None,
                               final_output_settings: dict = None, publish_callback=None):
        """
        Generate videos in parallel using ThreadPoolExecutor.
        
        Args:
            num_workers: Number of parallel workers (1-4 recommended)
            force_parallel: If True, always use parallel generation (ignore auto-detection)
            ... other args same as generate_shorts
        """
        video_settings = normalize_video_settings(video_settings)
        subtitle_settings = normalize_subtitle_settings(subtitle_settings)
        audio_settings = normalize_audio_settings(audio_settings)
        overlay_settings = normalize_overlay_settings(overlay_settings)
        youtube_mixer_settings = normalize_youtube_mixer_settings(youtube_mixer_settings)
        batch_effective_settings = self._effective_batch_settings(
            video_settings=video_settings,
            subtitle_settings=subtitle_settings,
            audio_settings=audio_settings,
            overlay_settings=overlay_settings,
            youtube_mixer_settings=youtube_mixer_settings,
            music_path=music_path,
            media_path=media_path,
            use_ai_image_generation=use_ai_image_generation,
            use_triple_template=use_triple_template,
            unlimited_images=unlimited_images,
            num_unique_images=num_unique_images,
            strict_theme_following=strict_theme_following,
            enable_scene_variety=enable_scene_variety,
            image_model=image_model,
            veo3_settings=veo3_settings,
            custom_images_folder=custom_images_folder,
            use_only_custom_images=use_only_custom_images,
            use_image_cache=use_image_cache,
            save_to_image_cache=save_to_image_cache,
            image_pool_settings=image_pool_settings,
            avatar_settings=avatar_settings,
            final_output_settings=final_output_settings,
        )
        visual_source_error = self._validate_visual_source_config(youtube_mixer_settings)
        if visual_source_error:
            log_callback(f"❌ {visual_source_error}")
            raise ValueError(visual_source_error)
        from core.visual_source_manager import reset_visual_batch_state
        reset_visual_batch_state()


        output_dir = Path(output_path)
        file_logger = None

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            self._reset_title_registry(output_dir)

            # 💰 Сброс трекера стоимости для новой генерации
            reset_tracker()
            
            # 🔑 Сброс состояния API ключей для новой генерации
            # Это позволяет повторно использовать ключи которые были "исчерпаны" в предыдущей сессии
            try:
                from core.api_key_manager import get_key_manager
                key_manager = get_key_manager()
                key_manager.reset()
                log_callback("🔑 Состояние API ключей сброшено для новой генерации")
            except Exception:
                pass
            
            # Keep the current launch keys available before any theme generation starts.
            self.api_key = api_key
            self.google_ai_api_key = google_ai_api_key
            self.text_generator.api_key = api_key
            
            theme_name = source_data.get('theme', 'generation')
            file_logger = create_logger(
                output_dir=str(Path(__file__).resolve().parent.parent),
                video_name=f"parallel_{num_videos}videos",
                gui_callback=log_callback
            )
            
            # Логируем настройки
            file_logger.log_section("ПАРАЛЛЕЛЬНАЯ ГЕНЕРАЦИЯ")
            file_logger.log(f"Тема: {theme_name}")
            file_logger.log(f"Количество видео: {num_videos}")
            file_logger.log(f"Воркеров: {num_workers}")
            file_logger.log(f"Разрешение: {video_settings.get('width')}x{video_settings.get('height')}")
            
            # Заменяем log_callback на dual logger
            original_log_callback = log_callback
            log_callback = file_logger
            
            # 🎯 УМНАЯ ЛОГИКА ВЫБОРА РЕЖИМА ГЕНЕРАЦИИ
            is_horizontal = video_settings.get('width', 1920) > video_settings.get('height', 1080)
            
            has_custom_texts = bool(source_data.get('custom_texts'))
            use_parallel = has_custom_texts or force_parallel
            
            if not use_parallel:
                effective_workers = 1
                log_callback(f"📝 ПОСЛЕДОВАТЕЛЬНАЯ ГЕНЕРАЦИЯ: {num_videos} видео")
            else:
                effective_workers = self._calculate_effective_workers(
                    requested_workers=num_workers,
                    num_videos=num_videos,
                    has_custom_texts=has_custom_texts,
                    audio_settings=audio_settings,
                    video_settings=video_settings,
                )
                log_callback(f"🚀 ПАРАЛЛЕЛЬНАЯ ГЕНЕРАЦИЯ: {num_videos} видео, {effective_workers} воркер(ов)")
                if effective_workers < max(1, int(num_workers or 1)):
                    log_callback(f"   🚦 Выбрано {num_workers}, безопасный предел для текущих настроек: {effective_workers}")
                
                log_callback(f"   📐 Формат: {'Горизонтальное' if is_horizontal else 'Вертикальное'} ({video_settings.get('width')}x{video_settings.get('height')})")
                log_callback(f"   ⏱️ Длительность: {video_settings.get('duration')}s")
                if force_parallel:
                    log_callback("   🔧 Принудительный режим (force_parallel=True)")
            
            num_workers = effective_workers
            
            # 🎯 Логируем статистику глобального пула потоков
            if _USE_GLOBAL_POOL:
                pool = get_global_thread_pool()
                log_callback(f"🧵 GlobalThreadPool: {pool.get_stats_str()}")
            
            # Get main theme (original user input)
            main_theme = source_data.get('theme', 'интересные факты')
            
            # 🔧 УЛУЧШЕНО: Умное извлечение визуальной темы из рекламных/кастомных текстов
            source_data.setdefault('metadata_theme', main_theme)
            custom_texts = source_data.get('custom_texts')
            if custom_texts and len(custom_texts) > 0:
                log_callback("📝 Кастомные тексты: анализ содержимого для определения главного объекта...")
                
                # Пытаемся использовать Gemini для определения визуальной темы
                try:
                    # Объединяем первые 3 текста (или меньше если их меньше)
                    texts_to_analyze = custom_texts[:min(3, len(custom_texts))]
                    combined_text = ' '.join([text.text for text in texts_to_analyze])
                    
                    # Ограничиваем длину для анализа (первые 1000 слов)
                    words_for_analysis = combined_text.split()[:1000]
                    text_for_analysis = ' '.join(words_for_analysis)
                    
                    # Пытаемся извлечь объект через Gemini (НАПРЯМУЮ, без viral_text_system!)
                    if api_key:
                        log_callback("   🤖 Использую Gemini для определения визуальной темы...")
                        
                        from core.gemini_client import GeminiClient
                        _theme_client = GeminiClient(api_key)
                        
                        prompt = f'''Прочитай эти рекламные/информационные тексты и определи их ГЛАВНЫЙ ВИЗУАЛЬНЫЙ ОБЪЕКТ.
Что нужно нарисовать на картинке? (например: "клубничный пирог", "морковный чизкейк", "гоночная машина").
Верни ТОЛЬКО 1-4 слова. Без объяснений, без кавычек, без точек.

Текст:
{text_for_analysis[:2000]}

Главный объект:'''
                        
                        _theme_resp = _theme_client.generate_text(prompt, temperature=0.3, max_tokens=30)
                        keywords_text = _theme_resp.raw_text if _theme_resp.success else ''
                        
                        if keywords_text and keywords_text.strip():
                            keywords_text = keywords_text.strip().replace('"', '').replace("'", "")
                            # Валидация: тема не должна быть длиннее 50 символов
                            if len(keywords_text) > 50:
                                keywords_text = ' '.join(keywords_text.split()[:4])
                            log_callback(f"   ✅ Gemini определил тему: {keywords_text}")
                            main_theme = keywords_text
                            
                            # Переопределяем theme в source_data чтобы все системы (YouTube/Images) использовали его
                            source_data['theme'] = main_theme
                        else:
                            raise Exception("Gemini не вернул результат")
                    else:
                        raise Exception("API ключ недоступен")
                        
                except Exception as gemini_error:
                    # Fallback: просто берем первые слова
                    log_callback(f"   ⚠️ Ошибка Gemini ({gemini_error}), использую заголовки...")
                    titles = [text.title for text in custom_texts if hasattr(text, 'title') and text.title]
                    if titles:
                        main_theme = ', '.join(titles[:5])
                    else:
                        main_theme = source_data.get('theme', 'интересные факты')
            
            # Get themes (subtopics)
            themes = self._get_themes(source_data, num_videos, log_callback)
            if not themes:
                log_callback(f"❌ Не удалось получить темы: {source_data}")
                return
            
            expected_batch_hash = self._batch_hash(
                themes,
                source_data,
                video_settings,
                batch_effective_settings,
            )
            previous_manifest = self._load_json_file(
                self._get_manifest_path(output_dir),
                {},
            )
            can_resume_batch = previous_manifest.get('batch_hash') == expected_batch_hash
            batch_manifest = self._init_batch_manifest(
                output_dir,
                themes,
                source_data,
                video_settings,
                batch_effective_settings,
            )
            log_callback(
                f"🧾 Batch manifest: {batch_manifest.get('batch_hash', 'unknown')} "
                f"({'resume' if can_resume_batch else 'new'})"
            )
            
            # 🚀 YOUTUBE PRE-DOWNLOAD: Скачиваем видео для всего батча ЗАРАНЕЕ
            # Это экономит время - не ждём скачивания для каждого видео отдельно
            # 🔧 ФИКС: Проверяем что не используются локальные видео
            custom_videos_folder = youtube_mixer_settings.get('custom_videos_folder') if youtube_mixer_settings else None
            use_local_videos = bool(
                youtube_mixer_settings
                and youtube_mixer_settings.get('enable_local_videos', True)
                and custom_videos_folder
                and Path(custom_videos_folder).is_dir()
            )
            
            # "Only custom images" controls the image source only. It must not
            # disable pre-downloading online video clips for a mixed-media batch.
            if (
                youtube_mixer_settings
                and youtube_mixer_settings.get('enabled', False)
                and youtube_mixer_settings.get('enable_youtube', True)
                and num_videos > 1
                and not use_local_videos
                and not youtube_mixer_settings.get('remake_source_video_enabled', False)
            ):
                try:
                    from core.youtube_mixer import YouTubeMixer, estimate_youtube_source_budget
                    from core.youtube.batch_cache import clear_batch_cache
                    
                    log_callback(f"\n{'='*60}")
                    log_callback(f"🚀 YOUTUBE PRE-DOWNLOAD: Скачивание видео для батча из {num_videos} видео")
                    log_callback(f"{'='*60}")
                    
                    # Очищаем старый батч-кэш
                    clear_batch_cache()
                    try:
                        from core.visual_relevance import clear_visual_relevance_runtime_cache

                        clear_visual_relevance_runtime_cache()
                    except Exception:
                        pass
                    
                    mixer = YouTubeMixer(
                        api_key=api_key,
                        log_callback=log_callback,
                        mixer_settings=youtube_mixer_settings,
                    )
                    
                    # Рассчитываем сколько контента нужно для всего батча.
                    # Mirror the actual render shot math instead of assuming
                    # a fixed 6s clip, otherwise fast 3-5s shorts get a thin
                    # YouTube pool and start reusing the same clips.
                    video_duration = video_settings.get('duration', 60)
                    youtube_percent = youtube_mixer_settings.get('youtube_percent', 30) / 100
                    shot_duration = video_settings.get('shot_duration') or (
                        video_settings.get('shot_min_duration', 3) + video_settings.get('shot_max_duration', 5)
                    ) / 2
                    shots_per_video = max(1, math.ceil(video_duration / max(0.5, shot_duration)))
                    clips_per_video = max(3, math.ceil(shots_per_video * youtube_percent))
                    base_clips_needed = clips_per_video * num_videos
                    total_clips_needed = min(
                        math.ceil(base_clips_needed * 1.25),
                        base_clips_needed + max(num_videos, 20),
                    )
                    
                    # Скачиваем контент по главной теме
                    budget = estimate_youtube_source_budget(
                        total_clips_needed=total_clips_needed,
                        target_duration_minutes=0,
                        min_clip_duration=youtube_mixer_settings.get('clip_min_duration', 6),
                        max_clip_duration=youtube_mixer_settings.get('clip_max_duration', 8),
                        batch_total_videos=num_videos,
                        target_orientation='any',
                        mixer_settings=youtube_mixer_settings,
                    )
                    target_duration = budget['target_duration_minutes']
                    
                    log_callback(
                        f"📊 Нужно клипов: ~{base_clips_needed} "
                        f"({clips_per_video} на видео), целевой пул: {total_clips_needed}"
                    )
                    log_callback(
                        f"⏱️ Скачиваем: ~{target_duration:.1f} мин контента "
                        f"({budget['min_videos_to_try']}-{budget['max_videos_to_try']} источников)"
                    )
                    
                    theme_cache_key = hashlib.md5(main_theme.lower().encode()).hexdigest()[:12]
                    cache_candidates = (
                        output_dir / "youtube_clips" / theme_cache_key,
                        output_dir / "youtube_clips" / "youtube_clips" / theme_cache_key,
                    )
                    reusable_clips = []
                    for cache_candidate in cache_candidates:
                        if cache_candidate.exists():
                            reusable_clips.extend(cache_candidate.rglob("clip_*.mp4"))
                    reusable_clips = list(dict.fromkeys(path.resolve() for path in reusable_clips))
                    reuse_threshold = max(clips_per_video, math.ceil(total_clips_needed * 0.7))

                    if len(reusable_clips) >= reuse_threshold:
                        pre_clips = [str(path) for path in reusable_clips]
                        log_callback(
                            f"♻️ Resume: используем {len(pre_clips)} уже подготовленных клипов "
                            "без повторного скачивания"
                        )
                    else:
                        # Скачиваем и нарезаем клипы с semantic fallback.
                        pre_clips = mixer.download_and_extract_clips_parallel(
                            theme=main_theme,
                            total_clips_needed=total_clips_needed,
                            target_duration_minutes=target_duration,
                            output_dir=output_dir,
                            min_clip_duration=youtube_mixer_settings.get('clip_min_duration', 6),
                            max_clip_duration=youtube_mixer_settings.get('clip_max_duration', 8),
                            batch_total_videos=num_videos,
                        )
                    
                    if pre_clips:
                        youtube_mixer_settings['_pre_download_clips_ready'] = True
                        youtube_mixer_settings['_pre_download_clip_paths'] = [
                            str(path) for path in pre_clips if Path(path).exists()
                        ]
                        log_callback(f"✅ Pre-download завершён: {len(pre_clips)} клипов готово")
                        log_callback(f"   💡 Эти клипы будут использованы для всех {num_videos} видео")
                    else:
                        youtube_mixer_settings['_pre_download_clips_ready'] = False
                        youtube_mixer_settings['_pre_download_clip_paths'] = []
                        log_callback("⚠️ Pre-download не дал клипов, каждое видео скачает свои")
                    
                    log_callback(f"{'='*60}\n")
                    
                except Exception as pre_dl_error:
                    youtube_mixer_settings['_pre_download_clips_ready'] = False
                    youtube_mixer_settings['_pre_download_clip_paths'] = []
                    log_callback(f"⚠️ YouTube pre-download ошибка: {pre_dl_error}")
                    log_callback("   Продолжаем без pre-download")
            
            # Prepare tasks
            tasks = {}
            start_time = time.time()
            
            # 🔒 Thread-safe логирование и rate limiting
            import threading
            log_lock = threading.Lock()
            task_state_lock = threading.Lock()
            task_started_at = {}
            batch_video_max_attempts = self._batch_video_max_attempts(source_data)
            if batch_video_max_attempts > 1:
                log_callback(
                    f"🛟 Контроль полноты партии: до {batch_video_max_attempts} попыток на каждый ролик"
                )
            
            def thread_safe_log(message):
                """Thread-safe wrapper для log_callback"""
                with log_lock:
                    log_callback(message)

            def run_tracked_task(task_id, **task_kwargs):
                """Record real execution time; queue wait must not look like a timeout."""
                with task_state_lock:
                    task_started_at[task_id] = time.monotonic()
                thread_safe_log(f"▶️ Задача {task_id}: начата")
                return self._run_batch_video_attempts(
                    lambda: self._generate_single_video_wrapper(**task_kwargs),
                    video_num=int(task_kwargs.get('video_num') or 0),
                    theme=str(task_kwargs.get('theme') or ''),
                    max_attempts=batch_video_max_attempts,
                    log_callback=thread_safe_log,
                    should_stop=lambda: bool(worker and worker.stop_flag),
                )
            
            # Resume is controlled only by the fingerprinted batch manifest.
            _manifest_completed = batch_manifest.get('completed', {}) if can_resume_batch else {}
            if can_resume_batch:
                if _manifest_completed:
                    log_callback(f"📂 Manifest resume: найдено {len(_manifest_completed)} завершённых видео текущей партии")
            
            # Use ThreadPoolExecutor for parallel video generation
            executor = ThreadPoolExecutor(max_workers=num_workers)
            try:
                # Submit all tasks
                skipped_count = 0
                completed_count = 0  # 🔧 FIX: Инициализация счетчика завершенных видео
                processed_count = 0  # 🔧 Учитывает и упавшие задачи (для прогресс-бара)
                runtime_completed_ids = set()
                runtime_failed_ids = set()
                cancelled = False
                for i, theme in enumerate(themes):
                    if worker and worker.stop_flag:
                        log_callback("🛑 Генерация остановлена пользователем.")
                        cancelled = True
                        break
                    
                    video_num = i + 1
                    task_id = f"video_{video_num}"
                    safe_theme = self._sanitize_filename(theme)[:100]
                    expected_video_path = output_dir / f"{safe_theme}.mp4"
                    
                    # 🔍 ПРОВЕРКА СУЩЕСТВУЮЩИХ ВИДЕО (улучшенный resume)
                    _video_exists = False
                    
                    # Способ 1: fingerprinted batch manifest.
                    _manifest_entry = _manifest_completed.get(str(video_num), {})
                    if _manifest_entry.get('theme_hash') and _manifest_entry.get('theme_hash') != self._theme_hash(theme):
                        _manifest_entry = {}
                    _saved_filename = _manifest_entry.get('filename')
                    if _saved_filename and (output_dir / _saved_filename).exists():
                        _video_exists = True
                        thread_safe_log(f"⏭️ Видео #{video_num} уже существует: {_saved_filename}")
                    
                    # Способ 2: filename fallback допустим только для manifest,
                    # fingerprint которого совпадает с текущими настройками.
                    if can_resume_batch and not _video_exists:
                        if expected_video_path.exists():
                            _video_exists = True
                            thread_safe_log(f"⏭️ Видео #{video_num} уже существует: {expected_video_path.name}")
                    
                    if _video_exists:
                        thread_safe_log("   Пропускаем генерацию")
                        skipped_count += 1
                        completed_count += 1  # Считаем как завершенное
                        processed_count += 1  # Прогресс тоже двигается
                        runtime_completed_ids.add(video_num)
                        self._update_batch_manifest(output_dir, video_num, 'skipped', theme, filename=_saved_filename or expected_video_path.name)
                        
                        # Обновляем прогресс
                        progress = int((completed_count / len(themes)) * 100) if themes else 0
                        if progress_callback:
                            progress_callback(progress)
                        
                        continue  # Пропускаем эту задачу
                    
                    thread_safe_log(f"📤 Задача {task_id}: Отправлена (тема: '{theme}')")
                    
                    # 🔒 SECURITY FIX: Передаём API ключи явно, не через kwargs
                    future = executor.submit(
                        run_tracked_task,
                        task_id,
                        theme=theme,
                        video_num=video_num,
                        output_dir=output_dir,
                        api_key=api_key,
                        google_ai_api_key=google_ai_api_key,
                        kwargs={
                            'music_path': music_path,
                            'video_settings': video_settings,
                            'subtitle_settings': subtitle_settings,
                            'media_path': media_path,
                            'use_ai_image_generation': use_ai_image_generation,
                            'overlay_settings': overlay_settings,
                            'use_triple_template': use_triple_template,
                            'log_callback': thread_safe_log,
                            'audio_settings': audio_settings,
                            'main_theme': main_theme,
                            'unlimited_images': unlimited_images,
                            'num_unique_images': num_unique_images,
                            'language': normalize_language(source_data.get('language', 'Russian')),
                            'strict_theme_following': strict_theme_following,
                            'strict_text_theme': source_data.get('strict_text_theme', True),
                            'enable_scene_variety': enable_scene_variety,
                            'image_model': image_model,
                            'veo3_settings': veo3_settings or {'enabled': False},
                            'youtube_mixer_settings': youtube_mixer_settings or {'enabled': False},
                            'source_data': source_data,  # 🎭 Передаём source_data для viral настроек (seamless_loop, comment_bait)
                            'batch_total_videos': num_videos,  # 🆕 Количество видео в батче для YouTube Mixer
                            'custom_images_folder': custom_images_folder,  # 📁 Папка с пользовательскими изображениями
                            'use_only_custom_images': use_only_custom_images,  # ✅ ТОЛЬКО свои картинки
                            'use_image_cache': use_image_cache,  # 💾 Использовать кэш изображений
                            'save_to_image_cache': save_to_image_cache,  # 💾 Сохранять в кэш
                            'image_callback': image_callback,  # 🖼️ Callback для превью
                            'image_pool_settings': image_pool_settings or {'use_image_pool': False},  # 🖼️ Настройки пула изображений
                            'avatar_settings': avatar_settings or {'enabled': False},  # 🤖 Настройки ИИ аватара
                            'worker': worker,  # 🛑 Передаём воркер для проверки stop_flag внутри задачи
                            'publish_callback': publish_callback,
                        }
                    )
                    tasks[task_id] = (future, theme)
                
                # Логируем статистику пропущенных
                if skipped_count > 0:
                    thread_safe_log(f"\n✅ Пропущено {skipped_count} уже существующих видео")
                    thread_safe_log(f"📊 Будет создано: {len(tasks)} новых видео\n")
                
                # Process completed tasks
                
                # Длительные задачи нельзя безопасно убить внутри ThreadPoolExecutor.
                # Поэтому это порог предупреждения, а не причина выбрасывать результат.
                video_duration = video_settings.get('duration', 60)
                # Формула: 10 секунд обработки на 1 секунду видео + 600 секунд базовый (для YouTube Pipeline)
                # YouTube Pipeline может занять 5-10 минут на скачивание и нарезку
                runtime_warning_after = min(1800, max(600, video_duration * 10 + 600))
                log_callback(
                    f"⏱️ Контроль долгих задач: предупреждение после "
                    f"{runtime_warning_after/60:.1f} минут фактической работы; задача не будет потеряна"
                )
                 
                pending = {future: (task_id, theme) for task_id, (future, theme) in tasks.items()}
                warned_long_running = set()
                while pending:
                    if worker and worker.stop_flag:
                        thread_safe_log("🛑 Прерывание оставшихся задач по требованию...")
                        cancelled = True
                        for future in pending:
                            future.cancel()
                        break

                    done, _ = wait(pending.keys(), timeout=0.5, return_when=FIRST_COMPLETED)
                    now = time.monotonic()

                    for future, (task_id, theme) in list(pending.items()):
                        if future in done:
                            continue
                        with task_state_lock:
                            started_at = task_started_at.get(task_id)
                        if (
                            started_at is not None
                            and now - started_at > runtime_warning_after
                            and future not in warned_long_running
                        ):
                            warned_long_running.add(future)
                            elapsed_minutes = (now - started_at) / 60
                            thread_safe_log(
                                f"⚠️ Задача {task_id} работает дольше обычного "
                                f"({elapsed_minutes:.1f} мин), продолжаем ждать корректный результат"
                            )
                            thread_safe_log(f"   Тема: '{theme}'")

                    for future in done:
                        if future not in pending:
                            continue
                        task_id, theme = pending.pop(future)
                        
                        if worker and worker.stop_flag:
                            cancelled = True
                            break
                        
                        try:
                            future.result()
                            completed_count += 1
                            runtime_completed_ids.add(int(task_id.split('_')[-1]))
                            thread_safe_log(f"✅ Задача {task_id} завершена!")
                            
                        except TimeoutError as timeout_err:
                            thread_safe_log(f"⏱️ Внутренняя операция задачи {task_id} завершилась по таймауту")
                            thread_safe_log(f"   Тема: '{theme}'")
                            thread_safe_log(f"   Ошибка: {timeout_err}")
                            thread_safe_log(f"   Traceback:\n{traceback.format_exc()}")
                            runtime_failed_ids.add(int(task_id.split('_')[-1]))
                            self._update_batch_manifest(output_dir, int(task_id.split('_')[-1]), 'failed', theme, error=timeout_err)
                            
                        except Exception as e:
                            thread_safe_log(f"❌ Задача {task_id} не удалась: {e}")
                            thread_safe_log(f"   Тема: '{theme}'")
                            thread_safe_log(f"   Traceback:\n{traceback.format_exc()}")
                            runtime_failed_ids.add(int(task_id.split('_')[-1]))
                            self._update_batch_manifest(output_dir, int(task_id.split('_')[-1]), 'failed', theme, error=e)
                        
                        # 🔧 FIX: считаем обработанным даже если задача упала/таймаут —
                        # иначе прогресс-бар застревает (например 39/40 = 97%) и UI выглядит зависшим
                        processed_count += 1

                        # Update progress (используем len(themes) как базу, чтобы skipped учитывались)
                        progress = int((processed_count / len(themes)) * 100) if themes else 0
                        if progress_callback:
                            progress_callback(progress)
                        
                        thread_safe_log(f"📊 Прогресс: {processed_count}/{len(themes)} ({progress}%) — успешно: {completed_count}")
                
            finally:
                # Не оставляем невидимые генераторы после финального отчёта GUI.
                executor.shutdown(wait=True, cancel_futures=True)

            # Final summary. Re-read the manifest because running tasks can finish
            # while executor.shutdown() waits after a cancellation request.
            elapsed_time = time.time() - start_time
            _total = len(themes)
            final_manifest = self._load_json_file(self._get_manifest_path(output_dir), {})

            def _manifest_ids(bucket_name, require_file=False):
                result = set()
                for raw_id, entry in (final_manifest.get(bucket_name, {}) or {}).items():
                    try:
                        video_id = int(raw_id)
                    except (TypeError, ValueError):
                        continue
                    if not 1 <= video_id <= _total:
                        continue
                    if require_file:
                        filename = (entry or {}).get('filename')
                        if not filename or not (output_dir / filename).is_file():
                            continue
                    result.add(video_id)
                return result

            success_ids = set(runtime_completed_ids)
            success_ids.update(_manifest_ids('completed', require_file=True))
            success_ids.update(_manifest_ids('skipped', require_file=True))
            failed_ids = set(runtime_failed_ids)
            failed_ids.update(_manifest_ids('failed'))
            failed_ids.difference_update(success_ids)
            success_count = len(success_ids)
            failed_count = len(failed_ids)
            pending_count = max(0, _total - success_count - failed_count)
            cancelled = bool(cancelled and pending_count > 0)
            full_success = not cancelled and failed_count == 0 and pending_count == 0
            
            thread_safe_log(f"\n{'='*60}")
            mode_label = 'ПАРАЛЛЕЛЬНАЯ' if num_workers > 1 else 'ПОСЛЕДОВАТЕЛЬНАЯ'
            if full_success:
                thread_safe_log(f"🎉 {mode_label} ГЕНЕРАЦИЯ ЗАВЕРШЕНА!")
            elif cancelled:
                thread_safe_log(f"🛑 {mode_label} ГЕНЕРАЦИЯ ОСТАНОВЛЕНА")
            else:
                thread_safe_log(f"⚠️ {mode_label} ГЕНЕРАЦИЯ ЗАВЕРШЕНА НЕ ПОЛНОСТЬЮ")
            thread_safe_log(f"{'='*60}")
            thread_safe_log(f"⏱️ Общее время: {elapsed_time:.1f}s ({elapsed_time/60:.1f} мин)")
            # 🔧 FIX: делим на len(themes), а не len(tasks) — иначе после resume
            # (когда tasks=1 новых, а success_count=40 включая skipped) видим '40/1'
            thread_safe_log(f"📊 Успешно: {success_count}/{_total} видео")
            if failed_count > 0:
                thread_safe_log(f"❌ Не удалось: {failed_count}/{_total} видео")
            if pending_count > 0:
                thread_safe_log(f"⏸️ Ожидают запуска/повтора: {pending_count}/{_total} видео")
            # Средняя скорость считается по фактически отработавшим задачам (а не skipped)
            _worked = max(1, len(tasks))
            thread_safe_log(f"⚡ Средняя скорость: {elapsed_time/_worked:.1f}s/видео")
            if num_workers > 1:
                # The legacy estimate simplifies to the worker count. Avoid a
                # zero-division crash for an immediately cancelled empty run.
                speedup = float(num_workers)
                thread_safe_log(f"🚀 Ускорение: ~{speedup:.1f}x (vs последовательная)")
            
            # 🎯 Финальная статистика глобального пула потоков
            if _USE_GLOBAL_POOL:
                pool = get_global_thread_pool()
                stats = pool.get_stats()
                thread_safe_log(f"🧵 Потоки: peak={stats['peak_threads']}, acquired={stats['total_acquired']}, released={stats['total_released']}")
            
            thread_safe_log(f"{'='*60}\n")
            
            # 💰 Вывод стоимости генерации
            log_cost_summary(thread_safe_log)
            
            # 🧹 Очистка YouTube кэша после завершения генерации
            if youtube_mixer_settings and youtube_mixer_settings.get('enabled', False):
                try:
                    from core.youtube_mixer import YouTubeMixer
                    from core.youtube.batch_cache import clear_batch_cache
                    # Очищаем только память текущей партии. Дисковый кэш
                    # исходников ограничивается во время общей очистки.
                    clear_batch_cache()
                except Exception as cleanup_error:
                    thread_safe_log(f"⚠️ Не удалось очистить YouTube кэш: {cleanup_error}")
            
            # Finalize before strict cleanup. Recovery files are useful only while
            # the batch is actively running; completed output stays publisher-ready.
            if isinstance(file_logger, DualLogger):
                file_logger.finalize()
                file_logger = None

            try:
                cleanup_generated_folder(
                    str(output_dir),
                    original_log_callback,
                    keep_images=False,
                    skip_youtube_clips=False,
                    keep_youtube_download_cache=True,
                    keep_metadata=True,
                    metadata_dirs_to_keep=get_enabled_metadata_dirs(final_output_settings),
                    keep_recovery_files=not full_success,
                )
            except Exception as cleanup_error:
                thread_safe_log(f"⚠️ Ошибка автоочистки: {cleanup_error}")
            if cancelled:
                raise GenerationCancelledError(
                    f"Генерация остановлена пользователем: создано {success_count}/{_total}, "
                    f"ошибок {failed_count}, осталось {pending_count}. "
                    "Партия сохранена для продолжения."
                )
            if failed_count or pending_count:
                raise RuntimeError(
                    f"Партия не завершена полностью: создано {success_count}/{_total}, "
                    f"ошибок {failed_count}, осталось {pending_count}. "
                    "Незавершённые номера сохранены в batch manifest для повтора."
                )
        except GenerationCancelledError as error:
            log_callback(f"🛑 {error}")
            if isinstance(file_logger, DualLogger):
                file_logger.finalize()
            raise
        except Exception as e:
            log_callback(f"❌ Ошибка параллельной генерации: {e}")
            log_callback(f"📋 Traceback: {traceback.format_exc()}")
            
            # Финализируем лог даже при ошибке
            if isinstance(file_logger, DualLogger):
                file_logger.log_error(e, "Параллельная генерация")
                file_logger.finalize()
            raise
    
    def _generate_single_video_wrapper(self, theme: str, video_num: int, output_dir: Path, 
                                       api_key: str, google_ai_api_key: str, kwargs: dict):
        """
        Wrapper for single video generation in thread.
        
        🔒 SECURITY FIX: API ключи передаются явно, а не через kwargs
        """
        try:
            # Добавляем API ключи в kwargs для передачи в _generate_single_video
            kwargs['api_key'] = api_key
            kwargs['google_ai_api_key'] = google_ai_api_key
            
            # 📊 Устанавливаем video_num для потокобезопасного прогресса
            worker = kwargs.get('worker')
            if worker and hasattr(worker, 'set_current_video'):
                worker.set_current_video(video_num)
            
            self._generate_single_video(
                theme=theme,
                video_num=video_num,
                output_dir=output_dir,
                **kwargs
            )
            return f"Video {video_num} for '{theme}' generated successfully"
        except Exception as e:
            # Детальное логирование ошибки (используем глобальный traceback)
            import traceback as tb
            error_details = tb.format_exc()
            raise Exception(f"Failed to generate video {video_num}: {e}\n{error_details}")

    def _generate_single_video(self, theme: str, video_num: int, output_dir: Path, **kwargs):
        """Generates a single video from start to finish in a single pass."""
        log_callback = kwargs['log_callback']
        worker = kwargs.get('worker')  # Get worker for granular progress
        
        # Helper function for stage progress
        def emit_stage_progress(stage: str, percent: int = 100):
            """Emit granular progress for current stage."""
            if worker and hasattr(worker, 'stage_progress_callback'):
                worker.stage_progress_callback(stage, percent)
        
        # Используем TempFileManager для гарантированной очистки
        temp_manager = TempFileManager(log_callback=log_callback)
        
        try:
            kwargs = dict(kwargs)
            video_settings = dict(kwargs['video_settings'])
            kwargs['video_settings'] = video_settings
            total_duration = video_settings.get('duration', 60)
            log_callback(f"\n{'='*60}")
            log_callback(f"🎬 ВИДЕО #{video_num}: '{theme}'")
            log_callback(f"{'='*60}")

            # 1. Generate all text content at once
            log_callback("📝 Этап 1/4: Генерация текста...")
            emit_stage_progress('text', 0)  # Start text stage
            text_content = self._get_text_content(theme, total_duration, video_num, kwargs)
            
            if not text_content:
                raise Exception("Не удалось сгенерировать текстовый контент")
            
            log_callback(f"✅ Текст сгенерирован: {len(text_content.get('full_text', ''))} символов")
            if text_content.get('estimated_duration'):
                total_duration = max(1.0, float(text_content['estimated_duration']))
                video_settings['duration'] = total_duration
            emit_stage_progress('text', 100)  # Text stage complete

            # 🚀 ОПТИМИЗАЦИЯ: Параллельная генерация АУДИО + ИЗОБРАЖЕНИЙ
            # Изображения не зависят от точной длительности аудио на этапе генерации
            log_callback("🚀 Этап 2-3/4: ПАРАЛЛЕЛЬНАЯ генерация аудио + изображений...")
            emit_stage_progress('audio', 0)  # Start audio/images stage
            
            from concurrent.futures import ThreadPoolExecutor
            import time as time_module
            
            parallel_start = time_module.time()
            
            # 🌍 ИСПРАВЛЕНО: Для кастомных текстов, которые являются просто рекламными скриптами
            # (где тема/заголовок это просто первые 50 символов), мы используем `main_theme`
            # который был специально извлечён Gemini как визуальный объект (например, "тарталетки с кремом").
            # Если использовать theme (заголовок), то генератор получит обрезанное предложение и нарисует бред.
            original_theme = kwargs.get('main_theme', theme)
            video_language = normalize_language(kwargs.get('language', 'Russian'))
            
            # Переводим тему на английский если язык видео не русский
            if video_language != 'Russian' and original_theme:
                # Проверяем есть ли кириллица в теме
                has_cyrillic = any('\u0400' <= c <= '\u04FF' for c in original_theme)
                if has_cyrillic:
                    log_callback(f"🌍 Тема на русском, язык видео: {video_language} - переводим...")
                    translated_theme = self.text_generator.translate_to_english(
                        original_theme, 
                        log_callback, 
                        self.api_key
                    )
                    original_theme = translated_theme
                    log_callback(f"✅ Тема для изображений: {original_theme}")
            
            # 🎬 Инициализируем youtube_clips до блока with (чтобы была доступна снаружи)
            youtube_clips = []
            
            # 🚀 ОПТИМИЗАЦИЯ: Запускаем ВСЁ параллельно: аудио + изображения + YouTube
            youtube_settings = kwargs.get('youtube_mixer_settings', {})
            youtube_enabled = youtube_settings.get('enabled', False)
            has_first_shot_image = bool(
                text_content.get('first_shot_image_path')
                and Path(str(text_content.get('first_shot_image_path'))).is_file()
            )
            strict_video_ratio_requested = self._is_video_only_requested(youtube_settings)
            video_only_requested = strict_video_ratio_requested and not has_first_shot_image
            try:
                effective_video_ratio = max(
                    0.0, min(1.0, float(youtube_settings.get('video_ratio', 0.3)))
                )
            except (TypeError, ValueError):
                effective_video_ratio = 0.3
            allow_clip_reuse_for_video = False
            # Увеличиваем workers если YouTube включен
            max_workers = 4 if youtube_enabled else 3
            
            image_paths = []
            images_future = None
            youtube_future = None
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Задача 1: Генерация аудио (с retry на уровне генератора)
                AUDIO_RETRIES = 3
                audio_path, audio_duration, voiceover_path = None, 0, None
                
                for audio_attempt in range(AUDIO_RETRIES):
                    audio_future = executor.submit(
                        self._process_audio,
                        text_content, output_dir, kwargs
                    )
                    
                    # Задача 2: Генерация изображений (запускаем только на первой попытке)
                    if audio_attempt == 0:
                        if video_only_requested:
                            log_callback("🎬 Выбрано 100% видео: генерация изображений пропущена")
                        else:
                            images_future = executor.submit(
                                self._get_image_paths,
                                text_content, output_dir, total_duration, kwargs, original_theme
                            )
                        
                        # 🚀 Задача 3: YouTube Pipeline (ПАРАЛЛЕЛЬНО с изображениями!)
                        if youtube_enabled:
                            log_callback("🚀 Запуск пайплайна видеоклипов параллельно с изображениями...")
                            visual_kwargs = dict(kwargs)
                            visual_kwargs['_visual_text_parts'] = (
                                text_content.get('text_parts')
                                or [text_content.get('full_text', '')]
                            )
                            visual_kwargs['_remake_source_video'] = text_content.get(
                                'remake_source_video'
                            )
                            visual_kwargs['_remake_first_shot_image'] = text_content.get(
                                'first_shot_image_path'
                            )
                            youtube_future = executor.submit(
                                self._process_youtube_clips,
                                original_theme, total_duration, youtube_settings, video_settings,  # ✅ ФИКС: используем ОРИГИНАЛЬНУЮ тему пользователя, а не AI-сгенерированную
                                output_dir, visual_kwargs, log_callback
                            )
                    
                    # Ждем аудио
                    audio_path, audio_duration, voiceover_path = audio_future.result()
                    
                    if audio_path and audio_duration > 0:
                        break  # Успех!
                    
                    if audio_attempt < AUDIO_RETRIES - 1:
                        audio_provider = (kwargs.get('audio_settings') or {}).get('provider', 'gemini')
                        wait_time = self._audio_retry_wait(audio_provider, audio_attempt)
                        log_callback(f"⏳ Аудио не создано. Ожидание {wait_time}s перед повтором (попытка {audio_attempt + 2}/{AUDIO_RETRIES})...")
                        import time
                        time.sleep(wait_time)
                
                if not audio_path or audio_duration <= 0:
                    raise Exception("Не удалось создать аудио. Генерация остановлена.")
                
                # Add both voiceover and final audio to temp files for cleanup
                if voiceover_path and voiceover_path != audio_path:
                    temp_manager.add(voiceover_path)
                temp_manager.add(audio_path)
                log_callback(f"✅ Аудио готово: {audio_duration:.2f}s")
                emit_stage_progress('audio', 100)  # Audio stage complete
                
                # Update total duration based on actual audio
                total_duration = audio_duration
                
                # Задача 4: Получение timestamps (запускаем после аудио готово)
                timestamps_future = None
                if kwargs.get('subtitle_settings', {}).get('enabled', False):
                    language = normalize_language(kwargs.get('language', 'Russian'))
                    timestamps_future = executor.submit(
                        self.audio_processor.get_word_timestamps,
                        audio_path, self.api_key, language, log_callback,
                        text_content.get('full_text', '')
                    )
                
                # Ждем изображения
                image_paths = images_future.result() if images_future else []
                log_callback(f"✅ Изображения готовы: {len(image_paths) if image_paths else 0} шт.")
                emit_stage_progress('images', 100)  # Images stage complete
                
                # 🚀 Ждем YouTube клипы (уже выполняются параллельно!)
                if youtube_future:
                    try:
                        log_callback("⏳ Ожидание пайплайна видеоклипов...")
                        # In 100% video mode a late YouTube/Pexels batch is better than
                        # falling back to an empty timeline and failing the render.
                        youtube_timeout = 1200 if video_only_requested else 600
                        youtube_clips = youtube_future.result(timeout=youtube_timeout)
                        log_callback(f"📦 Пайплайн видеоклипов вернул: {type(youtube_clips).__name__}, len={len(youtube_clips) if youtube_clips else 0}")
                        if youtube_clips:
                            log_callback(f"✅ Видеоклипы готовы: {len(youtube_clips)} шт.")
                            for i, clip in enumerate(youtube_clips[:3]):
                                log_callback(f"   📹 Клип {i+1}: {Path(clip).name if isinstance(clip, str) else clip}")
                            # Shared batch clips are owned by the batch cleanup. A
                            # per-video TempFileManager must not delete them while
                            # other parallel workers still need the same pool.
                        else:
                            # 🔄 FALLBACK: Пробуем использовать клипы из батч-кэша
                            theme_cache_key = hashlib.md5(original_theme.lower().encode()).hexdigest()[:12]
                            log_callback("⚠️ Видеоклипы не найдены, пробуем кэш партии...")
                            try:
                                clips_dir = output_dir / "youtube_clips" / theme_cache_key
                                if clips_dir.exists():
                                    existing_clips = list(clips_dir.rglob("clip_*.mp4"))
                                    if existing_clips:
                                        # 🎲 Случайная выборка вместо первых N
                                        import random
                                        youtube_clips = [str(c) for c in random.sample(existing_clips, min(5, len(existing_clips)))]
                                        log_callback(f"   🔄 Найдено {len(youtube_clips)} клипов в кэше (случайная выборка)")
                            except Exception:
                                pass
                    except FuturesTimeoutError:
                        log_callback(f"⏱️ Пайплайн видеоклипов превысил timeout ({youtube_timeout}s)")
                        log_callback("   Беру только уже нарезанные клипы...")
                        if not video_only_requested:
                            youtube_future.cancel()
                        youtube_clips = None
                        log_callback("⚠️ Используем только AI изображения")
                        youtube_clips = []
                        try:
                            theme_cache_key = hashlib.md5(original_theme.lower().encode()).hexdigest()[:12]
                            # 1. Проверяем основную тематическую папку youtube_clips
                            clips_dir = output_dir / "youtube_clips" / theme_cache_key
                            log_callback(f"🔍 Поиск клипов в: {clips_dir}")
                            if clips_dir.exists():
                                existing_clips = list(clips_dir.rglob("clip_*.mp4"))
                                log_callback(f"   📂 Найдено файлов: {len(existing_clips)}")
                                if existing_clips:
                                    import random
                                    # 🎲 Случайная выборка до 30 клипов
                                    youtube_clips = [str(c) for c in random.sample(existing_clips, min(30, len(existing_clips)))]
                                    log_callback(f"✅ Найдено {len(youtube_clips)} частично скачанных клипов в {clips_dir} (случайная выборка)")
                                    for i, clip in enumerate(youtube_clips[:5]):
                                        log_callback(f"   📹 Клип {i+1}: {Path(clip).name}")
                            else:
                                log_callback(f"   ⚠️ Папка не существует: {clips_dir}")
                            
                            # 2. Проверяем generated/youtube_clips (альтернативная папка)
                            if not youtube_clips:
                                alt_clips_dir = Path("generated") / "youtube_clips" / theme_cache_key
                                log_callback(f"🔍 Поиск клипов в альтернативной папке: {alt_clips_dir}")
                                if alt_clips_dir.exists():
                                    alt_clips = list(alt_clips_dir.rglob("clip_*.mp4"))
                                    log_callback(f"   📂 Найдено файлов: {len(alt_clips)}")
                                    if alt_clips:
                                        import random
                                        # 🎲 Случайная выборка до 30 клипов
                                        youtube_clips = [str(c) for c in random.sample(alt_clips, min(30, len(alt_clips)))]
                                        log_callback(f"✅ Найдено {len(youtube_clips)} клипов в {alt_clips_dir} (случайная выборка)")
                            
                            if not youtube_clips:
                                log_callback("⚠️ Частичные клипы не найдены ни в одной папке - используем только AI изображения")
                                log_callback("   📂 Проверенные папки:")
                                log_callback(f"      • {output_dir / 'youtube_clips' / theme_cache_key}")
                                log_callback(f"      • generated/youtube_clips/{theme_cache_key}")
                        except Exception as e:
                            log_callback(f"⚠️ Ошибка получения частичных результатов: {e}")
                            import traceback
                            log_callback(f"   {traceback.format_exc()[:500]}")
                            youtube_clips = []
                    except Exception as yt_error:
                        log_callback(f"⚠️ Ошибка пайплайна видеоклипов: {yt_error}")
                        log_callback(f"   {traceback.format_exc()[:300]}")
                        youtube_clips = []
                
                # Получаем timestamps (если запускали)
                word_timestamps = None
                if timestamps_future:
                    word_timestamps = timestamps_future.result()
                    if word_timestamps:
                        # The audio layer may return Edge WordBoundary, Vosk or
                        # Gemini timestamps and already logs the chosen source.
                        log_callback(f"✅ Синхронизация: получено {len(word_timestamps)} слов")
                        # Добавляем timestamps в text_content для использования в субтитрах
                        text_content['word_timestamps'] = word_timestamps
                    else:
                        # 🚀 TIER 2 FALLBACK: SubtitleSynchronizer (Forced Alignment)
                        log_callback("⚠️ Точные timestamps недоступны, используем forced alignment...")
                        
                        try:
                            from core.subtitle_sync import SubtitleSynchronizer
                            
                            full_text = text_content.get('full_text', '')
                            if not full_text and 'script' in text_content:
                                full_text = text_content['script']
                            
                            if full_text:
                                # Получаем синхронизированные субтитры через анализ аудио
                                synced_subs = SubtitleSynchronizer.synchronize_subtitles(
                                    text=full_text,
                                    audio_path=str(audio_path),
                                    max_words_per_subtitle=3
                                )
                                
                                if synced_subs:
                                    # Конвертируем в формат word_timestamps
                                    word_timestamps = SubtitleSynchronizer.convert_to_word_timestamps(synced_subs)
                                    text_content['word_timestamps'] = word_timestamps
                                    log_callback(f"✅ Forced alignment: {len(synced_subs)} сегментов, {len(word_timestamps)} слов синхронизировано")
                                    log_callback("   📊 Точность: ~85% (vs 95% у Gemini STT, 60% у пропорционального)")
                                else:
                                    log_callback("⚠️ Forced alignment не смог синхронизировать (нет текста/аудио)")
                                    log_callback("   ⏭️ Используем пропорциональное распределение (точность ~60%)")
                            else:
                                log_callback("⚠️ Нет текста для синхронизации")
                                log_callback("   ⏭️ Используем пропорциональное распределение")
                        
                        except Exception as e:
                            log_callback(f"⚠️ Ошибка forced alignment: {str(e)[:100]}")
                            log_callback("   ⏭️ Используем пропорциональное распределение")
            
            shot_duration_for_fallback = video_settings.get('shot_duration') or (
                video_settings.get('shot_min_duration', 3)
                + video_settings.get('shot_max_duration', 3)
            ) / 2
            required_visuals = max(
                1,
                int(audio_duration / max(0.5, float(shot_duration_for_fallback))),
            )
            unique_video_clips = list(dict.fromkeys(
                str(path) for path in (youtube_clips or []) if path and Path(path).is_file()
            ))
            youtube_clips = unique_video_clips
            unique_images = list(dict.fromkeys(
                str(path) for path in (image_paths or []) if path and Path(path).is_file()
            ))
            image_paths = unique_images
            # Renderer intentionally ignores synthetic fallback images whenever real
            # video clips are available.  Count the same usable pool here; otherwise
            # fallback placeholders can hide a real shortage and the render stage
            # aborts instead of applying controlled clip reuse.
            from pathlib import Path as _Path
            has_only_fallbacks = bool(image_paths) and all(
                'fallback' in _Path(p).name.lower() or 'bg_gradient' in _Path(p).name.lower()
                for p in image_paths
            )
            usable_image_count = 0 if youtube_clips and has_only_fallbacks else len(image_paths)
            available_visuals = len(youtube_clips) + usable_image_count
            allow_clip_reuse_for_video = bool(
                youtube_clips
                and available_visuals < required_visuals
                and youtube_settings.get('allow_clip_reuse_when_insufficient', True)
            )
            if allow_clip_reuse_for_video:
                log_callback(
                    f"🔁 Доступно {available_visuals}/{required_visuals} "
                    "уникальных визуалов. Рендер продолжится на полученном пуле: "
                    "все уникальные элементы используются до повторов, а неизбежные "
                    "повторы равномерно разводятся по таймлайну."
                )

            if video_only_requested and not youtube_clips:
                raise RuntimeError(
                    "Выбран режим 100% видео, но включённые источники не дали ни одного клипа. "
                    "Проверьте источник видеоряда, доступ к YouTube/Pexels или папку локальных видео."
                )

            # 🔧 ФИКС: Проверяем, что видеоряд не состоит целиком из заглушек
            if (
                not video_only_requested
                and has_only_fallbacks
                and not youtube_clips
            ):
                raise RuntimeError(
                    "Не удалось сгенерировать AI-изображения и скачать YouTube-видео. "
                    "Рендеринг отменён во избежание пустого/серого видеоряда."
                )

            if not image_paths and not video_only_requested:
                raise Exception("Не удалось получить изображения. Генерация остановлена.")
            
            protected_first_shot = str(text_content.get('first_shot_image_path') or '')
            temp_manager.add_many(
                path for path in image_paths
                if not protected_first_shot
                or str(Path(path).resolve()) != str(Path(protected_first_shot).resolve())
            )
            
            # Логируем время параллельной генерации
            parallel_elapsed = time_module.time() - parallel_start
            log_callback(f"⚡ Параллельная генерация завершена за {parallel_elapsed:.1f}s")
            log_callback(f"   💡 Экономия времени: ~{parallel_elapsed * 0.4:.1f}s vs последовательно")

            # 4. Render the final video in one go
            log_callback("🎞️ Этап 4/4: Рендеринг финального видео...")
            emit_stage_progress('render', 0)  # Start render stage
            
            # 🎯 ИСПОЛЬЗУЕМ TITLE ДЛЯ НАЗВАНИЯ ФАЙЛА (если есть)
            # Это даст уникальные названия для каждого видео
            if text_content and 'title' in text_content:
                video_title = text_content['title']
                log_callback(f"📝 Название видео: {video_title}")
            else:
                video_title = theme
            
            safe_theme = self._sanitize_filename(video_title)
            # Используем _get_unique_filepath для автоматической уникальности
            # Без добавления номеров к названию — если файл существует, добавится (2), (3) и т.д.
            base_path = output_dir / f"{safe_theme[:100]}.mp4"
            final_video_path = self._get_unique_filepath(base_path)
            
            # 🚀 ВЫБОР МЕТОДА РЕНДЕРИНГА
            # Оптимизированный One-Pass рендер для видео с умеренным количеством изображений
            use_optimized = video_settings.get('use_optimized_render', False)
            num_images = len(image_paths)
            
            # 🔧 ФИКС: Выбор метода рендеринга на основе количества изображений
            # One-Pass: быстрый, но лимит Windows командной строки ~50 изображений
            # Batch: медленнее, но без лимитов - ВСЕГДА соблюдает настройки GUI
            
            # Рассчитываем сколько шотов будет в итоге
            shot_duration = video_settings.get('shot_duration') or \
                           (video_settings.get('shot_min_duration', 2) + video_settings.get('shot_max_duration', 2)) / 2
            estimated_shots = int(audio_duration / shot_duration) if shot_duration > 0 else num_images
            
            # Если шотов больше 50 - используем batch rendering (без лимитов)
            if estimated_shots > 50:
                log_callback(f"📹 Много шотов ({estimated_shots}) - используем batch рендеринг (без лимитов, соблюдает все настройки GUI)")
                use_optimized = False
            elif youtube_clips:
                # 🎬 YouTube клипы требуют стандартный рендер (поддерживает mixed_media)
                log_callback(f"🎬 Видеоклипы ({len(youtube_clips)}) — используем стандартный рендеринг")
                use_optimized = False
            elif num_images > 5 and not use_optimized:
                log_callback(f"🚀 Используем оптимизированный One-Pass рендер ({num_images} изображений)")
                use_optimized = True
            
            # 🔧 КРИТИЧЕСКИЙ ФИКС: Гарантируем очистку временных файлов даже при ошибке
            try:
                if use_optimized:
                    log_callback("🚀 Используем оптимизированный One-Pass рендеринг")
                    self.video_renderer.render_optimized_one_pass(
                        image_paths=image_paths,
                        audio_path=audio_path,
                        output_path=final_video_path,
                        video_settings=video_settings,
                        subtitle_settings=kwargs['subtitle_settings'],
                        text_content=text_content,
                        log_callback=log_callback,
                        media_path=kwargs.get('media_path'),
                        overlay_settings=kwargs.get('overlay_settings'),
                        veo3_settings=kwargs.get('veo3_settings'),
                        google_ai_api_key=kwargs.get('google_ai_api_key'),
                        theme=theme
                    )
                else:
                    log_callback("📹 Используем стандартный рендеринг")
                    # Создаём mixed_media если есть YouTube клипы
                    if youtube_clips:
                        # Рассчитываем количество шотов
                        shot_dur = video_settings.get('shot_duration') or \
                                  (video_settings.get('shot_min_duration', 3) + video_settings.get('shot_max_duration', 3)) / 2
                        total_shots = max(1, int(audio_duration / shot_dur))
                        yt_settings = kwargs.get('youtube_mixer_settings', {})
                        video_ratio = effective_video_ratio
                        
                        # 🧠 УМНЫЙ ПОДБОР КЛИПОВ: Анализируем текст для оптимального распределения
                        use_smart_matching = yt_settings.get('smart_clip_matching', True)
                        remake_source_mode = bool(
                            yt_settings.get('remake_source_video_enabled', False)
                            and text_content.get('remake_source_video')
                        )
                        remake_first_shot_only = bool(
                            yt_settings.get('remake_first_shot_only', False)
                            and image_paths
                        )
                        
                        if remake_source_mode:
                            # RemakeShotMixer already produced a deliberate shuffled order.
                            # Preserve it and reserve exactly one optional image for frame one.
                            use_first_image = bool(
                                yt_settings.get('remake_first_shot_only', False)
                                and image_paths
                            )
                            mixed_media = self._create_remake_media_sequence(
                                youtube_clips,
                                total_shots=max(1, total_shots),
                                first_image=(image_paths[0] if use_first_image else None),
                            )
                            video_count = sum(
                                media_type == 'video' for _, media_type in mixed_media
                            )
                            log_callback(
                                f"   🎬 Ремейк-монтаж: {video_count} перемешанных шотов"
                                + (" + пользовательский первый кадр" if use_first_image else "")
                            )
                        elif use_smart_matching and text_content.get('text_parts'):
                            log_callback("   🧠 Умный подбор клипов на основе текста...")
                            try:
                                from core.smart_clip_matcher import (
                                    InsufficientUniqueVisualsError,
                                    create_smart_mixed_media,
                                )
                                # The source manager already balances local/Pexels,
                                # but the render stage may use only the first N
                                # clips. Shuffle here too so every video gets a
                                # fresh subset from the expanded candidate pool.
                                import random
                                shuffled_youtube_clips = youtube_clips.copy() if youtube_clips else []
                                random.shuffle(shuffled_youtube_clips)
                                mixed_media = create_smart_mixed_media(
                                    text_parts=text_content.get('text_parts', []),
                                    image_paths=image_paths,
                                    clip_paths=shuffled_youtube_clips,
                                    youtube_ratio=video_ratio,
                                    log_callback=log_callback,
                                    total_shots=total_shots,  # 🔧 КРИТИЧНО: Передаём количество шотов!
                                    allow_video_reuse=allow_clip_reuse_for_video,
                                )
                            except InsufficientUniqueVisualsError:
                                raise
                            except Exception as smart_err:
                                log_callback(f"   ⚠️ Умный подбор не удался: {smart_err}, используем стандартный")
                                # 🎲 КРИТИЧНО: Перемешиваем youtube_clips для каждого видео
                                import random
                                shuffled_youtube_clips = youtube_clips.copy() if youtube_clips else []
                                random.shuffle(shuffled_youtube_clips)
                                mixed_media = self._create_mixed_media_list(
                                    image_paths, shuffled_youtube_clips, 
                                    total_shots=total_shots, 
                                    video_ratio=video_ratio,
                                    start_with_images=video_settings.get('start_with_images', False),
                                    first_shot_from_pool=video_settings.get('first_shot_from_pool', False),
                                    allow_video_reuse=allow_clip_reuse_for_video,
                                )
                        else:
                            log_callback(f"   🔧 Создание mixed_media: {total_shots} шотов, ratio={video_ratio}")
                            # 🎲 КРИТИЧНО: Перемешиваем youtube_clips для каждого видео
                            import random
                            shuffled_youtube_clips = youtube_clips.copy() if youtube_clips else []
                            random.shuffle(shuffled_youtube_clips)
                            mixed_media = self._create_mixed_media_list(
                                image_paths, shuffled_youtube_clips, 
                                total_shots=total_shots, 
                                video_ratio=video_ratio,
                                start_with_images=video_settings.get('start_with_images', False),
                                first_shot_from_pool=video_settings.get('first_shot_from_pool', False),
                                allow_video_reuse=allow_clip_reuse_for_video,
                            )

                        if remake_first_shot_only and not remake_source_mode:
                            ordered_video_clips = [
                                path for path, media_type in (mixed_media or [])
                                if media_type == 'video'
                            ] or list(youtube_clips)
                            mixed_media = self._create_remake_media_sequence(
                                ordered_video_clips,
                                total_shots=max(1, total_shots),
                                first_image=image_paths[0],
                            )
                            log_callback(
                                "   🖼️ Пользовательская картинка занимает только первый шот"
                            )
                    else:
                        mixed_media = None
                    
                    # 🖼️ ПОСТ-ОБРАБОТКА: ставим картинку из пула первым шотом,
                    # не меняя количество шотов и рассчитанную пропорцию медиа.
                    if (
                        mixed_media
                        and video_settings.get('first_shot_from_pool', False)
                        and image_paths
                        and video_ratio < 0.999
                    ):
                        mixed_media = self._put_pool_image_first(
                            mixed_media, image_paths, log_callback
                        )

                    self.video_renderer.render_full_video(
                        image_paths=image_paths,
                        audio_path=audio_path,
                        output_path=final_video_path,
                        video_settings=video_settings,
                        subtitle_settings=kwargs['subtitle_settings'],
                        text_content=text_content,
                        log_callback=log_callback,
                        media_path=kwargs.get('media_path'),
                        overlay_settings=kwargs.get('overlay_settings'),
                        use_triple_template=kwargs.get('use_triple_template', False),
                        veo3_settings=kwargs.get('veo3_settings'),
                        google_ai_api_key=kwargs.get('google_ai_api_key'),
                        theme=theme,
                        mixed_media=mixed_media
                    )

                avatar_settings = kwargs.get('avatar_settings') or {'enabled': False}
                if avatar_settings.get('enabled'):
                    log_callback("🤖 Создаём и встраиваем AI-аватар HeyGen...")
                    from core.heygen_client import apply_heygen_avatar

                    apply_heygen_avatar(
                        final_video_path=final_video_path,
                        full_text=text_content.get('full_text', ''),
                        title=text_content.get('title', theme),
                        avatar_settings=avatar_settings,
                        video_settings=video_settings,
                        log_callback=log_callback,
                    )
                
                # Добавляем .ass файл субтитров в список для очистки
                ass_path = final_video_path.parent / f"{final_video_path.stem}.ass"
                if ass_path.exists():
                    temp_manager.add(ass_path)
                    
            except Exception as render_error:
                log_callback(f"\n{'='*60}")
                log_callback("❌ ОШИБКА РЕНДЕРИНГА")
                log_callback(f"❌ Видео #{video_num}: '{theme}'")
                log_callback(f"❌ Ошибка: {render_error}")
                log_callback(f"{'='*60}")
                
                # Пробрасываем исключение дальше (очистка в finally)
                raise

            log_callback(f"\n{'='*60}")
            log_callback(f"✅ ВИДЕО #{video_num} УСПЕШНО СОЗДАНО")
            log_callback(f"📁 Путь: {final_video_path}")
            log_callback(f"⏱️ Длительность: {audio_duration:.2f}s")
            log_callback(f"{'='*60}\n")
            emit_stage_progress('render', 100)  # Render stage complete - video done!
            
            # The fingerprinted manifest is the single recovery authority.
            try:
                self._update_batch_manifest(
                    output_dir,
                    video_num,
                    'completed',
                    theme,
                    filename=final_video_path.name,
                )
                log_callback(
                    f"   💾 Manifest resume: видео #{video_num} сохранено "
                    f"({final_video_path.name})"
                )
            except Exception as manifest_err:
                log_callback(f"   ⚠️ Не удалось обновить manifest resume: {manifest_err}")
            
            # 🎯 Вызываем callback для обновления прогресса (для custom text tab)
            video_completed_callback = kwargs.get('video_completed_callback')
            if video_completed_callback:
                try:
                    video_completed_callback()
                except Exception as cb_err:
                    log_callback(f"   ⚠️ Ошибка callback: {cb_err}")
            
            # 🔒 Очистка метаданных видео (анти-детекция)
            try:
                from core.utils import strip_video_metadata
                strip_video_metadata(str(final_video_path), log_callback)
            except Exception as meta_err:
                log_callback(f"   ⚠️ Не удалось очистить метаданные: {meta_err}")
            
            # 📝 Генерация описания для YouTube/соцсетей (с A/B заголовками)
            desc_result = None
            try:
                from core.description_generator import generate_video_description
                from core.config_manager import ConfigManager
                
                language = normalize_language(kwargs.get('language', 'Russian'))
                
                # Загружаем config_manager для кастомного текста
                try:
                    config_manager = ConfigManager()
                except Exception:
                    config_manager = None
                
                metadata_theme = (
                    (kwargs.get('source_data') or {}).get('metadata_theme')
                    or (kwargs.get('source_data') or {}).get('theme')
                    or kwargs.get('main_theme')
                    or theme
                )
                working_title_key = self._title_key(text_content.get('title', ''))
                exact_title_subject = text_content.get('title_subject') or theme
                recent_titles = [
                    item for item in self._recent_used_titles(12)
                    if self._title_key(item) != working_title_key
                ]

                def reserve_metadata_title(candidates):
                    for candidate in candidates:
                        candidate = str(candidate or '').strip()
                        if not candidate:
                            continue
                        if (
                            text_content.get('custom_text_mode')
                            and self._title_key(candidate) == working_title_key
                        ):
                            return candidate
                        if self._reserve_diverse_title(
                            candidate,
                            subject_topic=exact_title_subject,
                            parent_theme=metadata_theme,
                        ):
                            return candidate

                    fallback = self._reserve_fallback_title(
                        text_content.get('full_text', ''),
                        exact_title_subject,
                        video_num,
                    )
                    log_callback(
                        f"   ♻️ A/B варианты повторяли форму серии; "
                        f"выбран предметный fallback: '{fallback}'"
                    )
                    return fallback

                desc_result = generate_video_description(
                    theme=metadata_theme,
                    subject_theme=exact_title_subject,
                    title=text_content.get('title', theme),
                    full_text=text_content.get('full_text', ''),
                    video_duration=audio_duration,
                    language=language,
                    api_key=self.api_key,
                    output_dir=final_video_path.parent,
                    log_callback=log_callback,
                    generate_ab_titles=True,  # 🎯 A/B тестирование заголовков
                    num_title_variants=3,
                    config_manager=config_manager,  # 📝 Для кастомного текста
                    video_num=video_num,  # 🆕 Номер видео для раздельных метаданных
                    video_filename=final_video_path.stem,  # 📁 Имя файла описания = имя видео
                    recent_titles=recent_titles,
                    title_reserver=reserve_metadata_title,
                )
                
                # Логируем A/B варианты если есть
                if desc_result and 'title_variants' in desc_result:
                    log_callback("   🎯 A/B заголовки сохранены")
                
                if desc_result and desc_result.get('safe_title'):
                    new_video_path = self._get_unique_filepath(final_video_path.with_name(f"{desc_result['safe_title']}.mp4"))
                    if new_video_path != final_video_path:
                        final_video_path.rename(new_video_path)
                        final_video_path = new_video_path
                        log_callback(f"   🏷️ Видео переименовано по A/B заголовку: {final_video_path.name}")
                        
                        try:
                            self._update_batch_manifest(
                                output_dir,
                                video_num,
                                'completed',
                                theme,
                                filename=final_video_path.name,
                            )
                        except Exception as manifest_err:
                            log_callback(
                                "   ⚠️ Не удалось обновить manifest "
                                f"после переименования: {manifest_err}"
                            )
                    
            except Exception as desc_err:
                log_callback(f"   ⚠️ Описание не создано: {desc_err}")

            publish_callback = kwargs.get('publish_callback')
            if publish_callback:
                try:
                    description_data = {}
                    title_variants = []
                    description_file = ""
                    safe_title = ""
                    if desc_result:
                        description_data = desc_result.get('description_data') or {}
                        title_variants = desc_result.get('title_variants') or description_data.get('title_variants') or []
                        description_file = desc_result.get('description_file') or ""
                        safe_title = desc_result.get('safe_title') or ""

                    publish_callback({
                        'video_path': str(final_video_path),
                        'description_file': description_file,
                        'metadata': description_data,
                        'title': (
                            description_data.get('title')
                            or (title_variants[0] if title_variants else None)
                            or text_content.get('title')
                            or safe_title
                            or final_video_path.stem
                        ),
                        'description': description_data.get('description', ''),
                        'hashtags': description_data.get('hashtags', []),
                        'title_variants': title_variants,
                        'opening_hooks': description_data.get('opening_hooks') or text_content.get('opening_hooks', []),
                        'opening_hook_family': (
                            description_data.get('opening_hook_family')
                            or text_content.get('opening_hook_family', '')
                        ),
                        'first_shot_hook': (
                            description_data.get('first_shot_title')
                            or text_content.get('first_shot_hook', '')
                        ),
                        'language': normalize_language(kwargs.get('language', 'Russian')),
                        'duration_seconds': audio_duration,
                        'width': video_settings.get('width'),
                        'height': video_settings.get('height'),
                        'theme': theme,
                        'video_num': video_num,
                        'batch_output_dir': str(output_dir),
                        'batch_manifest': str(self._get_manifest_path(output_dir)),
                    })
                    log_callback("   YouTube: видео передано в очередь автозагрузки")
                except Exception as cb_err:
                    log_callback(f"   ⚠️ YouTube callback: {cb_err}")
            
            # 🖼️ Генерация превью отключена
            
            # Собираем все временные файлы для очистки
            # Veo 3 temp files
            veo3_raw = final_video_path.parent / "veo3_intro_raw.mp4"
            veo3_silent = final_video_path.parent / "veo3_intro_silent.mp4"
            if veo3_raw.exists():
                temp_manager.add(veo3_raw)
            if veo3_silent.exists():
                temp_manager.add(veo3_silent)
            
            # Veo 3 reference images
            for ref_file in final_video_path.parent.glob("veo3_ref_shot*.png"):
                temp_manager.add(ref_file)
            
            # Trimmed audio
            trimmed_audio = final_video_path.parent / f"{final_video_path.stem}_trimmed_audio.mp3"
            if trimmed_audio.exists():
                temp_manager.add(trimmed_audio)
            
            # Filter scripts
            for filter_script in final_video_path.parent.glob("filter_script_*.txt"):
                temp_manager.add(filter_script)
            
            # Intermediate files
            with_intro = final_video_path.parent / f"{final_video_path.stem}_with_intro.mp4"
            if with_intro.exists():
                temp_manager.add(with_intro)
            
            # Temp video files
            for temp_video in final_video_path.parent.glob("*_temp*.mp4"):
                temp_manager.add(temp_video)

            # Cleanup temporary files
            temp_manager.cleanup()

        except Exception as e:
            import traceback as tb  # Локальный импорт для избежания конфликтов
            log_callback(f"\n{'='*60}")
            log_callback(f"❌ ОШИБКА при генерации видео #{video_num}")
            log_callback(f"❌ Тема: '{theme}'")
            log_callback(f"❌ Ошибка: {e}")
            log_callback(f"{'='*60}")
            log_callback(f"📋 Traceback:\n{tb.format_exc()}")
            
            # Cleanup on error - force delete all temp files
            temp_manager.cleanup(force=True)
            raise e  # Re-raise to be caught by the executor
        
        finally:
            # Гарантированная очистка при любом исходе
            # 🔒 КРИТИЧНО: Удаляем ТОЛЬКО файлы, отслеживаемые ЭТИМ воркером!
            # НЕ используем cleanup_generated_folder с glob-паттернами,
            # потому что в параллельном режиме другие воркеры могут
            # ещё использовать свои final_audio_*.mp3 и voiceover_*.mp3.
            temp_manager.cleanup()
    
    def _cleanup_temp_files(self, file_paths: list, log_callback):
        """Clean up temporary files after video generation."""
        if not file_paths:
            return
        
        cleaned = 0
        for file_path in file_paths:
            try:
                path_obj = Path(file_path)
                if path_obj.exists() and path_obj.is_file():
                    # Delete temporary audio and image files
                    # Проверяем по имени файла и расширению
                    should_delete = (
                        'assets' in str(path_obj) or 
                        'voiceover' in path_obj.name or 
                        'final_audio' in path_obj.name or
                        'ai_image_' in path_obj.name or  # AI-generated images
                        path_obj.name.startswith('voiceover_') or
                        path_obj.name.startswith('final_audio_') or
                        (path_obj.suffix.lower() in ['.ass', '.png', '.jpg', '.jpeg'] and 'ai_image' in path_obj.name)
                    )
                    
                    if should_delete:
                        path_obj.unlink()
                        cleaned += 1
            except Exception:
                pass
        
        if cleaned > 0:
            log_callback(f"🗑️ Очищено {cleaned} временных файлов")
            
    # ============================================================
    # 🚀 PIPELINE PARALLELISM: Разделение на prepare + render
    # Позволяет готовить контент для видео N+1 пока рендерится N
    # ============================================================
    
    def _prepare_video_content(self, video_num: int, theme: str, output_dir: Path, **kwargs):
        """
        
        Этап 1 pipeline: текст → аудио → изображения → YouTube клипы
        
        Args:
            video_num: Номер видео
            theme: Тема видео
            output_dir: Папка для сохранения
            **kwargs: Настройки генерации
            
        Returns:
            VideoContent dataclass с подготовленным контентом
            или VideoContent с error если что-то пошло не так
        """
        from core.pipeline_manager import VideoContent
        import time as time_module
        
        log_callback = kwargs.get('log_callback', print)
        
        try:
            kwargs = dict(kwargs)
            video_settings = dict(kwargs['video_settings'])
            kwargs['video_settings'] = video_settings
            total_duration = video_settings.get('duration', 60)
            
            log_callback(f"\n{'='*60}")
            log_callback(f"📝 PREPARE #{video_num}: '{theme}'")
            log_callback(f"{'='*60}")
            
            prepare_start = time_module.time()
            
            # 1. Генерация текста
            log_callback("📝 Генерация текста...")
            text_content = self._get_text_content(theme, total_duration, video_num, kwargs)
            
            if not text_content:
                return VideoContent(
                    video_num=video_num,
                    theme=theme,
                    text_content={},
                    audio_path='',
                    audio_duration=0,
                    image_paths=[],
                    youtube_clips=[],
                    error="Не удалось сгенерировать текст"
                )
            
            log_callback(f"✅ Текст: {len(text_content.get('full_text', ''))} символов")
            
            # 2. Параллельная генерация аудио + изображений + YouTube
            original_theme = kwargs.get('main_theme', theme)
            video_language = normalize_language(kwargs.get('language', 'Russian'))
            
            # Перевод темы если нужно
            if video_language != 'Russian' and original_theme:
                has_cyrillic = any('\u0400' <= c <= '\u04FF' for c in original_theme)
                if has_cyrillic:
                    translated_theme = self.text_generator.translate_to_english(
                        original_theme, log_callback, self.api_key
                    )
                    original_theme = translated_theme
            
            youtube_settings = kwargs.get('youtube_mixer_settings', {})
            youtube_enabled = youtube_settings.get('enabled', False)
            video_only_requested = self._is_video_only_requested(youtube_settings)
            audio_path, audio_duration, voiceover_path = None, 0, None
            image_paths = []
            youtube_clips = []
            word_timestamps = None
            
            max_workers = 4 if youtube_enabled else 3
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Аудио
                audio_future = executor.submit(
                    self._process_audio, text_content, output_dir, kwargs
                )
                
                # Изображения
                images_future = None
                if video_only_requested:
                    log_callback("🎬 Выбрано 100% видео: генерация изображений пропущена")
                else:
                    images_future = executor.submit(
                        self._get_image_paths, text_content, output_dir, total_duration, kwargs, original_theme
                    )
                
                # YouTube
                youtube_future = None
                if youtube_enabled:
                    visual_kwargs = dict(kwargs)
                    visual_kwargs['_visual_text_parts'] = (
                        text_content.get('text_parts')
                        or [text_content.get('full_text', '')]
                    )
                    youtube_future = executor.submit(
                        self._process_youtube_clips,
                        theme, total_duration, youtube_settings, video_settings,
                        output_dir, visual_kwargs, log_callback
                    )
                
                # Timestamps
                timestamps_future = None
                if kwargs.get('subtitle_settings', {}).get('enabled', False):
                    # Ждём аудио сначала
                    audio_path, audio_duration, voiceover_path = audio_future.result()
                    if audio_path:
                        language = normalize_language(kwargs.get('language', 'Russian'))
                        timestamps_future = executor.submit(
                            self.audio_processor.get_word_timestamps,
                            audio_path, self.api_key, language, log_callback,
                            text_content.get('full_text', '')
                        )
                else:
                    audio_path, audio_duration, voiceover_path = audio_future.result()
                
                # Собираем результаты
                if not audio_path or audio_duration <= 0:
                    return VideoContent(
                        video_num=video_num,
                        theme=theme,
                        text_content=text_content,
                        audio_path='',
                        audio_duration=0,
                        image_paths=[],
                        youtube_clips=[],
                        error="Не удалось создать аудио"
                    )
                
                image_paths = (images_future.result() or []) if images_future else []
                
                if youtube_future:
                    try:
                        youtube_timeout = 1200 if video_only_requested else 180
                        youtube_clips = youtube_future.result(timeout=youtube_timeout) or []
                    except FuturesTimeoutError:
                        # Отменяем задачу и пытаемся получить частичные результаты
                        log_callback(f"⏱️ Пайплайн видеоклипов превысил timeout ({youtube_timeout}s), беру частичные клипы...")
                        if not video_only_requested:
                            youtube_future.cancel()
                        youtube_clips = []
                        try:
                            clips_dir = output_dir / "youtube_clips"
                            if clips_dir.exists():
                                existing_clips = list(clips_dir.rglob("clip_*.mp4"))
                                if existing_clips:
                                    import random
                                    # 🎲 Случайная выборка до 20 клипов
                                    youtube_clips = [str(c) for c in random.sample(existing_clips, min(20, len(existing_clips)))]
                                    log_callback(f"✅ Таймаут: используем {len(youtube_clips)} частично скачанных клипов (случайная выборка)")
                        except Exception:
                            pass
                    except Exception:
                        youtube_clips = []
                
                if timestamps_future:
                    word_timestamps = timestamps_future.result()
                    if word_timestamps:
                        text_content['word_timestamps'] = word_timestamps
            
            if video_only_requested and not youtube_clips:
                return VideoContent(
                    video_num=video_num,
                    theme=theme,
                    text_content=text_content,
                    audio_path=str(audio_path) if audio_path else '',
                    audio_duration=audio_duration,
                    image_paths=[],
                    youtube_clips=[],
                    error=(
                        "Выбран режим 100% видео, но включённые источники не дали ни одного клипа. "
                        "Проверьте источник видеоряда, доступ к YouTube/Pexels или папку локальных видео."
                    ),
                )

            if not image_paths and not video_only_requested:
                return VideoContent(
                    video_num=video_num,
                    theme=theme,
                    text_content=text_content,
                    audio_path=str(audio_path) if audio_path else '',
                    audio_duration=audio_duration,
                    image_paths=[],
                    youtube_clips=[],
                    error="Не удалось получить изображения"
                )
            
            # Определяем output path
            video_title = text_content.get('title', theme)
            safe_theme = self._sanitize_filename(video_title)
            base_path = output_dir / f"{safe_theme[:100]}.mp4"
            final_path = self._get_unique_filepath(base_path)
            
            prepare_elapsed = time_module.time() - prepare_start
            log_callback(f"✅ Prepare #{video_num} готов за {prepare_elapsed:.1f}s")
            log_callback(f"   📊 Аудио: {audio_duration:.1f}s, изображения: {len(image_paths)}, видеоклипы: {len(youtube_clips)}")
            
            return VideoContent(
                video_num=video_num,
                theme=theme,
                text_content=text_content,
                audio_path=str(audio_path),
                audio_duration=audio_duration,
                image_paths=[str(p) for p in image_paths],
                youtube_clips=[str(c) for c in youtube_clips],
                word_timestamps=word_timestamps,
                output_path=str(final_path)
            )
            
        except Exception as e:
            log_callback(f"❌ Prepare #{video_num} ошибка: {e}")
            return VideoContent(
                video_num=video_num,
                theme=theme,
                text_content={},
                audio_path='',
                audio_duration=0,
                image_paths=[],
                youtube_clips=[],
                error=str(e)
            )
    
    def _render_prepared_video(self, content, **kwargs) -> Optional[str]:
        """
        Рендерит видео из подготовленного контента.
        
        Этап 2 pipeline: только рендеринг (GPU-intensive)
        
        Args:
            content: VideoContent с подготовленным контентом
            **kwargs: Настройки рендеринга
            
        Returns:
            Путь к готовому видео или None при ошибке
        """
        import time as time_module
        
        log_callback = kwargs.get('log_callback', print)
        
        if content.error:
            log_callback(f"❌ Render #{content.video_num}: контент с ошибкой - {content.error}")
            return None
        
        try:
            render_start = time_module.time()
            
            log_callback(f"\n{'='*60}")
            log_callback(f"🎬 RENDER #{content.video_num}: '{content.theme}'")
            log_callback(f"{'='*60}")
            
            video_settings = kwargs['video_settings']
            subtitle_settings = kwargs.get('subtitle_settings', {'enabled': False})
            
            output_path = Path(content.output_path)
            audio_path = Path(content.audio_path)
            image_paths = [Path(p) for p in content.image_paths]
            youtube_clips = content.youtube_clips
            text_content = content.text_content
            
            # Выбор метода рендеринга
            shot_duration = video_settings.get('shot_duration') or \
                           (video_settings.get('shot_min_duration', 2) + video_settings.get('shot_max_duration', 2)) / 2
            estimated_shots = int(content.audio_duration / shot_duration) if shot_duration > 0 else len(image_paths)
            
            use_optimized = video_settings.get('use_optimized_render', False)
            
            if estimated_shots > 50 or youtube_clips:
                use_optimized = False
            elif len(image_paths) > 5:
                use_optimized = True
            
            # Создаём mixed_media если есть YouTube клипы
            mixed_media = None
            if youtube_clips and not use_optimized:
                yt_settings = kwargs.get('youtube_mixer_settings', {})
                video_ratio = yt_settings.get('video_ratio', 0.3)
                total_shots = max(1, int(content.audio_duration / shot_duration))
                allow_video_reuse = bool(
                    youtube_clips
                    and len(set(youtube_clips)) + len(set(str(path) for path in image_paths)) < total_shots
                    and yt_settings.get('allow_clip_reuse_when_insufficient', True)
                )
                
                use_smart = yt_settings.get('smart_clip_matching', True)
                
                # 🎲 КРИТИЧНО: Перемешиваем youtube_clips для каждого видео один раз
                import random
                shuffled_youtube_clips = youtube_clips.copy() if youtube_clips else []
                random.shuffle(shuffled_youtube_clips)
                
                if use_smart and text_content.get('text_parts'):
                    try:
                        from core.smart_clip_matcher import create_smart_mixed_media
                        mixed_media = create_smart_mixed_media(
                            text_parts=text_content.get('text_parts', []),
                            image_paths=[str(p) for p in image_paths],
                            clip_paths=shuffled_youtube_clips,
                            youtube_ratio=video_ratio,
                            log_callback=log_callback,
                            total_shots=total_shots,
                            allow_video_reuse=allow_video_reuse,
                        )
                    except Exception:
                        mixed_media = self._create_mixed_media_list(
                            [str(p) for p in image_paths], shuffled_youtube_clips,
                            total_shots=total_shots, video_ratio=video_ratio,
                            allow_video_reuse=allow_video_reuse,
                        )
                else:
                    mixed_media = self._create_mixed_media_list(
                        [str(p) for p in image_paths], shuffled_youtube_clips,
                        total_shots=total_shots, video_ratio=video_ratio,
                        allow_video_reuse=allow_video_reuse,
                    )
            
            # 🖼️ ПОСТ-ОБРАБОТКА: Принудительно ставим картинку из пула первым шотом
            if (
                mixed_media
                and video_settings.get('first_shot_from_pool', False)
                and image_paths
                and video_ratio < 0.999
            ):
                mixed_media = self._put_pool_image_first(
                    mixed_media, image_paths, log_callback
                )
            
            # Рендеринг
            if use_optimized:
                log_callback("🚀 One-Pass рендер...")
                self.video_renderer.render_optimized_one_pass(
                    image_paths=[str(p) for p in image_paths],
                    audio_path=str(audio_path),
                    output_path=output_path,
                    video_settings=video_settings,
                    subtitle_settings=subtitle_settings,
                    text_content=text_content,
                    log_callback=log_callback,
                    media_path=kwargs.get('media_path'),
                    overlay_settings=kwargs.get('overlay_settings'),
                    veo3_settings=kwargs.get('veo3_settings'),
                    google_ai_api_key=kwargs.get('google_ai_api_key'),
                    theme=content.theme
                )
            else:
                log_callback("📹 Стандартный рендер...")
                self.video_renderer.render_full_video(
                    image_paths=[str(p) for p in image_paths],
                    audio_path=str(audio_path),
                    output_path=output_path,
                    video_settings=video_settings,
                    subtitle_settings=subtitle_settings,
                    text_content=text_content,
                    log_callback=log_callback,
                    media_path=kwargs.get('media_path'),
                    overlay_settings=kwargs.get('overlay_settings'),
                    use_triple_template=kwargs.get('use_triple_template', False),
                    veo3_settings=kwargs.get('veo3_settings'),
                    google_ai_api_key=kwargs.get('google_ai_api_key'),
                    theme=content.theme,
                    mixed_media=mixed_media
                )
            
            # Очистка метаданных
            if output_path.exists():
                try:
                    from core.utils import strip_video_metadata
                    strip_video_metadata(str(output_path), log_callback)
                except Exception:
                    pass
            
            render_elapsed = time_module.time() - render_start
            log_callback(f"✅ Render #{content.video_num} готов за {render_elapsed:.1f}s")
            log_callback(f"📁 {output_path}")
            
            return str(output_path) if output_path.exists() else None
            
        except Exception as e:
            log_callback(f"❌ Render #{content.video_num} ошибка: {e}")
            log_callback(f"   {traceback.format_exc()[:200]}")
            return None
    
    def generate_with_pipeline(self, themes: List[str], output_dir: Path, 
                               log_callback=None, **kwargs) -> Dict[int, str]:
        """
        Генерация видео с использованием pipeline parallelism.
        
        Пока рендерится видео N, готовится контент для видео N+1.
        Ускорение ~30-40% на батчах из 5+ видео.
        
        Args:
            themes: Список тем для генерации
            output_dir: Папка для сохранения
            log_callback: Функция логирования
            **kwargs: Настройки генерации
            
        Returns:
            Dict[video_num, output_path] - результаты генерации
            
        Example:
            >>> results = generator.generate_with_pipeline(
            ...     themes=['тема1', 'тема2', 'тема3'],
            ...     output_dir=Path('generated'),
            ...     video_settings={'duration': 60, 'width': 1080, 'height': 1920},
            ...     subtitle_settings={'enabled': True},
            ...     log_callback=print
            ... )
        """
        from core.pipeline_manager import PipelineManager, VideoContent
        import time as time_module
        
        log = log_callback or print
        
        if len(themes) < 2:
            log("⚠️ Pipeline не эффективен для < 2 видео, используем обычную генерацию")
            # Fallback на обычную генерацию
            results = {}
            for i, theme in enumerate(themes):
                video_num = i + 1
                try:
                    self._generate_single_video(theme, video_num, output_dir, 
                                               log_callback=log, **kwargs)
                    # Находим созданный файл
                    safe_theme = self._sanitize_filename(theme)[:100]
                    for f in output_dir.glob(f"{safe_theme}*.mp4"):
                        results[video_num] = str(f)
                        break
                except Exception as e:
                    log(f"❌ Видео {video_num} не создано: {e}")
            return results
        
        log(f"\n{'='*60}")
        log(f"🚀 PIPELINE PARALLELISM: {len(themes)} видео")
        log(f"{'='*60}")
        
        start_time = time_module.time()
        
        # Создаём функции для pipeline
        def prepare_func(video_num: int, theme: str, **kw) -> VideoContent:
            return self._prepare_video_content(
                video_num, theme, output_dir,
                log_callback=log, **kwargs
            )
        
        def render_func(content: VideoContent) -> Optional[str]:
            return self._render_prepared_video(content, log_callback=log, **kwargs)
        
        # Создаём и запускаем pipeline
        pipeline = PipelineManager(
            prepare_func=prepare_func,
            render_func=render_func,
            max_prepared=2,  # Максимум 2 видео в очереди на рендер
            log_callback=log
        )
        
        # Отправляем все задачи
        for i, theme in enumerate(themes):
            pipeline.submit(video_num=i+1, theme=theme)
        
        # Ждём завершения
        results = pipeline.wait_all()
        
        elapsed = time_module.time() - start_time
        
        log(f"\n{'='*60}")
        log(pipeline.get_stats_summary())
        log(f"   Общее время: {elapsed:.1f}s ({elapsed/60:.1f} мин)")
        log(f"{'='*60}\n")
        
        return results
            
    def _sanitize_filename(self, filename: str) -> str:
        """Removes illegal characters from a filename and cleans it up."""
        cleaned = re.sub(r'[\\/*?:"<>|\'`]', "", filename)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = cleaned.strip()
        cleaned = cleaned.rstrip('.')
        return cleaned

    def _process_smart_visual_clips(
        self,
        theme: str,
        total_duration: float,
        source_settings: dict,
        video_settings: dict,
        output_dir: Path,
        kwargs: dict,
        log_callback
    ) -> list:
        """Collect and mix local, YouTube, Pexels and Pixabay videos."""
        from core.visual_source_manager import VisualSourceManager

        manager = VisualSourceManager(
            log_callback,
            session_id=kwargs.get('_visual_session_id'),
        )
        shot_duration = video_settings.get('shot_duration') or (
            video_settings.get('shot_min_duration', 3) + video_settings.get('shot_max_duration', 3)
        ) / 2
        target_count = max(
            1,
            math.ceil(total_duration / max(0.5, shot_duration) * source_settings.get('video_ratio', 0.3)),
        )
        clip_pool_headroom = max(
            1.0,
            min(2.0, float(source_settings.get('clip_pool_headroom', 1.5))),
        )
        candidate_target_count = math.ceil(target_count * clip_pool_headroom)
        mode = source_settings.get('source_mode', 'smart_mix')
        target_orientation = 'horizontal' if video_settings.get('width', 1080) > video_settings.get('height', 1920) else 'vertical'
        online_allowed = self._has_online_video_sources_enabled(source_settings)

        log_callback(f"🎞️ Умный микс источников: режим={mode}, цель={target_count} клипов")
        local = []
        local_folder = source_settings.get('custom_videos_folder')
        if source_settings.get('enable_local_videos', True) and local_folder:
            local_only_settings = dict(source_settings)
            local_only_settings.update({
                'source_mode': 'youtube',
                '_source_orchestrated': True,
                'enable_youtube': False,
                'custom_videos_folder': local_folder,
            })
            local_only_kwargs = dict(kwargs)
            local = self._process_youtube_clips(
                theme, total_duration, local_only_settings, video_settings,
                output_dir, local_only_kwargs, log_callback
            )
            if local:
                log_callback(f"   📁 Клипы из локальных видео: {len(local)}")
        elif source_settings.get('enable_local_videos', True):
            if not any((
                source_settings.get('enable_youtube'),
                source_settings.get('enable_pexels'),
                source_settings.get('enable_pixabay_videos'),
                source_settings.get('enable_wikimedia_videos'),
            )):
                log_callback(
                    "   ⚠️ Включены только локальные видео, но корректная папка "
                    "с материалами не найдена; проверяю аварийные источники"
                )
            else:
                log_callback("   ⚠️ Локальный источник включён, но папка с видео не найдена")

        youtube = []
        if online_allowed and source_settings.get('enable_youtube', True):
            youtube_only_settings = dict(source_settings)
            youtube_only_settings.update({
                'source_mode': 'youtube',
                '_source_orchestrated': True,
                'custom_videos_folder': None,
            })
            youtube = self._process_youtube_clips(
                theme, total_duration, youtube_only_settings, video_settings,
                output_dir, kwargs, log_callback
            )
            if youtube:
                log_callback(f"   ▶️ YouTube: {len(youtube)}")

        primary_count = len(local) + len(youtube)
        stock_settings = dict(source_settings)
        stock_enabled = bool(
            stock_settings.get('enable_pexels')
            or stock_settings.get('enable_pixabay_videos')
            or stock_settings.get('enable_wikimedia_videos')
        )
        stock_count = 0
        expected_stock_slots = 0
        if online_allowed and stock_enabled:
            configured_stock_limit = int(source_settings.get('max_stock_clips', 24))
            dynamic_stock_limit = min(36, max(1, configured_stock_limit))
            if primary_count >= candidate_target_count:
                if local or youtube:
                    # ``smart_mix`` must remain a real multi-source mode even
                    # when a primary source already filled the nominal quota.
                    # Previously this happened only for local videos: a
                    # YouTube + stock selection silently degraded to YouTube
                    # only whenever YouTube returned enough clips.
                    expected_stock_slots = max(1, math.ceil(candidate_target_count * 0.35))
                    desired_stock_pool = max(12, candidate_target_count * 2, expected_stock_slots * 6)
                    stock_count = min(dynamic_stock_limit, desired_stock_pool)
            else:
                # Primary sources don't fully cover the target; ask for the
                # shortfall. Local-only libraries get a little headroom so the
                # mixer can still pick fresh stock clips from a broader pool;
                # YouTube keeps the exact old shortfall behaviour.
                expected_stock_slots = max(0, candidate_target_count - primary_count)
                if local and not youtube:
                    desired_stock_pool = max(expected_stock_slots, math.ceil(expected_stock_slots * 1.5))
                else:
                    desired_stock_pool = expected_stock_slots
                stock_count = min(desired_stock_pool, dynamic_stock_limit)

        stock_settings["gemini_api_key"] = kwargs.get("api_key")
        stock_api_keys = kwargs.get("_visual_api_keys") or kwargs.get("gemini_api_keys") or []
        if not isinstance(stock_api_keys, (list, tuple)):
            stock_api_keys = [stock_api_keys]
        stock_settings["gemini_api_keys"] = list(dict.fromkeys(
            key for key in list(stock_api_keys) + [
                kwargs.get("api_key"),
                kwargs.get("google_ai_api_key"),
                getattr(self, 'api_key', None),
            ]
            if key
        ))
        stock_settings["_visual_context"] = kwargs.get("_visual_context")
        stock_settings["_primary_available"] = bool(primary_count)
        stock = manager.collect_stock_videos(
            theme=theme,
            output_dir=output_dir,
            count=stock_count,
            settings=stock_settings,
            target_orientation=target_orientation,
        ) if stock_count else {}
        if stock:
            stock_total = (
                len(stock.get('pexels', []))
                + len(stock.get('pixabay', []))
                + len(stock.get('wikimedia', []))
            )
            if expected_stock_slots and stock_total > expected_stock_slots:
                log_callback(
                    f"   🌐 Стоки: Pexels={len(stock.get('pexels', []))}, "
                    f"Pixabay={len(stock.get('pixabay', []))}, "
                    f"Wikimedia={len(stock.get('wikimedia', []))} "
                    f"(расширенный пул; в финал ≈{expected_stock_slots})"
                )
            else:
                log_callback(
                    f"   🌐 Стоки: Pexels={len(stock.get('pexels', []))}, "
                    f"Pixabay={len(stock.get('pixabay', []))}, "
                    f"Wikimedia={len(stock.get('wikimedia', []))}"
                )

        mixed = manager.mix(youtube, local, stock, candidate_target_count, mode)
        log_callback(f"✅ Умный микс собран: {len(mixed)} клипов")
        return mixed
    
    def _process_youtube_clips(
        self,
        theme: str,
        total_duration: float,
        youtube_settings: dict,
        video_settings: dict,
        output_dir: Path,
        kwargs: dict,
        log_callback
    ) -> list:
        """
        Полный YouTube pipeline: поиск → скачка → motion check → нарезка.
        Выполняется ПАРАЛЛЕЛЬНО с генерацией изображений.
        
        🚀 ОПТИМИЗАЦИЯ: Сначала проверяет батч-кэш (pre-downloaded клипы)
        🎬 CUSTOM VIDEOS: Если указана папка со своими видео - использует их
        
        Args:
            theme: Тема для поиска видео
            total_duration: Общая длительность видео (сек)
            youtube_settings: Настройки YouTube Mixer
            video_settings: Настройки видео (для расчёта шотов)
            output_dir: Папка для сохранения
            kwargs: Дополнительные параметры
            log_callback: Функция логирования
            
        Returns:
            Список путей к YouTube клипам или пустой список при ошибке
        """
        kwargs = dict(kwargs or {})
        visual_session_id = kwargs.get('_visual_session_id')
        if not visual_session_id:
            import uuid
            visual_session_id = uuid.uuid4().hex
            kwargs['_visual_session_id'] = visual_session_id
        if not kwargs.get('_visual_session_initialized'):
            from core.visual_source_manager import reset_visual_video_state
            reset_visual_video_state(visual_session_id)
            kwargs['_visual_session_initialized'] = True
        remake_source = Path(str(kwargs.get('_remake_source_video') or ''))
        if (
            youtube_settings.get('remake_source_video_enabled', False)
            and str(kwargs.get('_remake_source_video') or '').strip()
            and remake_source.is_file()
        ):
            try:
                from core.remake_shot_mixer import RemakeShotMixer

                shot_min = float(
                    youtube_settings.get(
                        'remake_shot_min_duration',
                        video_settings.get('shot_min_duration', 2.5),
                    )
                )
                shot_max = float(
                    youtube_settings.get(
                        'remake_shot_max_duration',
                        video_settings.get('shot_max_duration', 5.0),
                    )
                )
                average_shot = max(0.8, (shot_min + shot_max) / 2)
                total_shots = max(1, math.ceil(total_duration / average_shot))
                has_first_image = bool(
                    youtube_settings.get('remake_first_shot_only', False)
                    and
                    kwargs.get('_remake_first_shot_image')
                    and Path(str(kwargs.get('_remake_first_shot_image'))).is_file()
                )
                desired_clips = max(1, total_shots - (1 if has_first_image else 0))
                seed_material = "|".join(
                    [
                        str(remake_source.resolve()),
                        str(theme),
                        " ".join(str(part) for part in (kwargs.get('_visual_text_parts') or []))[:500],
                    ]
                )
                log_callback(
                    f"🎬 Ремейк-видеоряд: нарезаем и перемешиваем {remake_source.name}"
                )
                remixed = RemakeShotMixer(log_callback).prepare_shots(
                    source_path=remake_source,
                    output_dir=output_dir / 'youtube_clips' / 'remake_sources',
                    target_duration=total_duration,
                    min_duration=shot_min,
                    max_duration=shot_max,
                    target_count=desired_clips,
                    seed=seed_material,
                )
                if remixed:
                    return remixed
            except Exception as exc:
                log_callback(
                    f"   ⚠️ Не удалось перемешать исходный видеоряд: {str(exc)[:180]}. "
                    "Переключаемся на выбранные резервные источники."
                )

        visual_context = kwargs.get('_visual_context')
        visual_api_keys = []
        configured_keys = kwargs.get('gemini_api_keys') or []
        if isinstance(configured_keys, (list, tuple)):
            visual_api_keys.extend(configured_keys)
        visual_api_keys.extend([
            kwargs.get('api_key'),
            kwargs.get('google_ai_api_key'),
            getattr(self, 'api_key', None),
        ])
        try:
            from core.api_key_manager import get_key_manager

            visual_api_keys.extend(get_key_manager().keys)
        except Exception:
            pass
        visual_api_keys = list(dict.fromkeys(key for key in visual_api_keys if key))
        kwargs['_visual_api_keys'] = visual_api_keys
        visual_text_parts = kwargs.get('_visual_text_parts') or []
        if not visual_context and visual_text_parts:
            try:
                from core.visual_relevance import build_visual_brief

                visual_context = build_visual_brief(
                    theme,
                    visual_text_parts,
                    api_keys=visual_api_keys,
                    settings=youtube_settings,
                    log_callback=log_callback,
                )
                kwargs['_visual_context'] = visual_context
            except Exception as exc:
                log_callback(f"   ⚠️ Визуальный бриф недоступен: {str(exc)[:120]}")

        def rank_for_current_script(paths, required_count):
            paths = [str(path) for path in (paths or []) if path]
            if not paths or not visual_context:
                return paths, None
            try:
                from core.visual_relevance import rank_clips_by_visual_relevance

                ranking = rank_clips_by_visual_relevance(
                    paths,
                    visual_context,
                    visual_api_keys,
                    settings=youtube_settings,
                    required_count=required_count,
                    log_callback=log_callback,
                )
                recommended = list(ranking.recommended_paths)
                only_local_available = bool(
                    youtube_settings.get('enable_local_videos')
                    and not youtube_settings.get('enable_youtube')
                    and not youtube_settings.get('enable_pexels')
                    and not youtube_settings.get('enable_pixabay_videos')
                    and not youtube_settings.get('enable_wikimedia_videos')
                )
                if (
                    ranking.analysis_available
                    and only_local_available
                    and len(recommended) < required_count
                ):
                    # Semantic scoring is advisory for a local-only run. If it
                    # rejects too many otherwise valid, unique clips, use the
                    # remaining ranked clips to fill the timeline instead of
                    # failing the whole video.
                    recommended_set = set(recommended)
                    for path in ranking.ranked_paths:
                        if path in recommended_set:
                            continue
                        recommended.append(path)
                        recommended_set.add(path)
                        if len(recommended) >= required_count:
                            break
                    recommended = recommended[:required_count]
                    log_callback(
                        f"   Local-only fallback: {len(recommended)}/{required_count} "
                        "unique clips retained after semantic ranking"
                    )
                return recommended, ranking
            except Exception as exc:
                log_callback(f"   ⚠️ Ранжирование кадров пропущено: {str(exc)[:120]}")
                return paths, None

        def finalize_clip_selection(paths, limit):
            from core.visual_source_manager import (
                fresh_ranked_existing_paths,
                reserve_ranked_existing_paths,
            )
            allocator = (
                fresh_ranked_existing_paths
                if youtube_settings.get('_source_orchestrated')
                else reserve_ranked_existing_paths
            )
            return allocator(paths, limit, session_id=visual_session_id)

        if (
            not youtube_settings.get('_source_orchestrated')
            and youtube_settings.get('source_mode', 'youtube') in {'fallback', 'smart_mix'}
        ):
            return self._process_smart_visual_clips(
                theme, total_duration, youtube_settings, video_settings,
                output_dir, kwargs, log_callback
            )
        try:
            from core.youtube_mixer import YouTubeMixer, estimate_youtube_source_budget
            from core.youtube.utils import calculate_youtube_duration
            import time as time_module
            
            pipeline_start = time_module.time()
            
            video_ratio = youtube_settings.get('video_ratio', 0.3)
            clip_min_duration = youtube_settings.get('clip_min_duration', 3)
            clip_max_duration = youtube_settings.get('clip_max_duration', 8)
            custom_videos_folder = youtube_settings.get('custom_videos_folder')
            online_sources_enabled = self._has_online_video_sources_enabled(youtube_settings)
            
            # Рассчитываем количество шотов
            shot_duration = video_settings.get('shot_duration') or \
                           (video_settings.get('shot_min_duration', 3) + video_settings.get('shot_max_duration', 3)) / 2
            total_shots_needed = max(1, math.ceil(total_duration / shot_duration))
            base_clips_needed = math.ceil(total_shots_needed * video_ratio)
            clip_pool_headroom = max(
                1.0,
                min(2.0, float(youtube_settings.get('clip_pool_headroom', 1.5))),
            )
            youtube_clips_needed = math.ceil(base_clips_needed * clip_pool_headroom)
            
            # 📐 Определяем ориентацию видео из настроек
            video_width = video_settings.get('width', 1920)
            video_height = video_settings.get('height', 1080)
            if video_width > video_height:
                target_orientation = 'horizontal'  # Горизонтальное видео (16:9)
            else:
                target_orientation = 'vertical'    # Вертикальное видео (9:16 shorts)
            
            source_label = (
                "Локальные видео"
                if youtube_settings.get('enable_local_videos', False) and not online_sources_enabled
                else "Пайплайн видеоклипов"
            )
            log_callback(f"🎬 {source_label}: нужно {youtube_clips_needed} клипов")
            log_callback(f"📐 Ориентация: {target_orientation} ({video_width}x{video_height})")
            
            if youtube_clips_needed <= 0:
                return []

            # The pre-download stage owns the exact clip paths. Reusing that list is
            # more reliable than reconstructing a cache directory from a theme hash.
            seed_clips = []
            pre_downloaded_clips = [
                Path(path)
                for path in youtube_settings.get('_pre_download_clip_paths', [])
                if path and Path(path).exists()
            ]
            if pre_downloaded_clips and youtube_settings.get('enable_youtube', True):
                from core.visual_source_manager import (
                    fresh_existing_paths,
                )
                # Ranking candidates must not reserve them. Only clips that
                # actually survive relevance ranking belong to this video.
                cache_candidates = fresh_existing_paths(
                    pre_downloaded_clips, session_id=visual_session_id
                )[
                    :max(youtube_clips_needed * 2, youtube_clips_needed)
                ]
                ranked_cache, cache_ranking = rank_for_current_script(
                    cache_candidates, youtube_clips_needed
                )
                if cache_ranking and cache_ranking.analysis_available:
                    usable_scored_cache = [
                        path for path in ranked_cache if path in cache_ranking.matches
                    ]
                    strong_cache = list(cache_ranking.accepted_paths)
                    minimum_ratio = float(
                        youtube_settings.get('visual_relevance_cached_min_ratio', 0.65)
                    )
                    minimum_validated = max(1, math.ceil(youtube_clips_needed * minimum_ratio))
                    if (
                        len(strong_cache) >= minimum_validated
                        and len(usable_scored_cache) >= youtube_clips_needed
                    ):
                        selected_clips = finalize_clip_selection(
                            usable_scored_cache, youtube_clips_needed
                        )
                        log_callback(
                            f"🚀 Общий кэш подтверждён по сценарию: {len(selected_clips)} "
                            f"из {len(pre_downloaded_clips)} клипов"
                        )
                        return selected_clips
                    seed_clips = usable_scored_cache
                    log_callback(
                        f"🔎 Общий кэш дал только {len(strong_cache)}/{minimum_validated} "
                        "сильных совпадений — запускаю точечный поиск по сценарию"
                    )
                elif len(ranked_cache) >= youtube_clips_needed:
                    selected_clips = finalize_clip_selection(
                        ranked_cache, youtube_clips_needed
                    )
                    log_callback(
                        f"🚀 Используем общий кэш партии: {len(selected_clips)} "
                        f"из {len(pre_downloaded_clips)} клипов (Gemini недоступен)"
                    )
                    return selected_clips
                else:
                    seed_clips = ranked_cache
             
            theme_cache_key = hashlib.md5(theme.lower().encode()).hexdigest()[:12]
            
            # 🎬 CUSTOM VIDEOS: Если указана папка со своими видео - используем их
            if (
                youtube_settings.get('enable_local_videos', True)
                and custom_videos_folder
                and Path(custom_videos_folder).is_dir()
            ):
                log_callback(f"📁 Использование своих видео из: {custom_videos_folder}")
                
                video_extensions = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
                custom_videos_path = Path(custom_videos_folder)
                
                # Ищем все видео файлы (включая подпапки)
                custom_videos = []
                for ext in video_extensions:
                    custom_videos.extend(custom_videos_path.rglob(f"*{ext}"))
                    custom_videos.extend(custom_videos_path.rglob(f"*{ext.upper()}"))
                
                if custom_videos:
                    log_callback(f"   📹 Найдено {len(custom_videos)} видео файлов")
                    
                    # Создаём папку для клипов
                    clips_dir = output_dir / "youtube_clips"
                    clips_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Balance donors using batch history, then cut several clips
                    # per donor in one pass. Range reservations are strict only
                    # inside the current video session.
                    from core.youtube_mixer import YouTubeMixer
                    mixer = YouTubeMixer(log_callback=log_callback)
                    mixer.visual_session_id = visual_session_id
                    
                    extracted_clips = []
                    from collections import Counter
                    from core.visual_source_manager import (
                        reserve_diverse_path_cycle,
                    )

                    donor_plan = reserve_diverse_path_cycle(custom_videos, youtube_clips_needed)
                    donor_counts = Counter(donor_plan)
                    donor_order = list(dict.fromkeys(donor_plan))
                    for raw_video_path in donor_order:
                        try:
                            clips = mixer.extract_random_clips(
                                video_path=str(raw_video_path),
                                output_dir=clips_dir,
                                num_clips=donor_counts[raw_video_path],
                                min_clip_duration=clip_min_duration,
                                max_clip_duration=clip_max_duration,
                                existing_clips=extracted_clips,
                            )
                            extracted_clips.extend(clips or [])
                        except Exception as exc:
                            log_callback(
                                f"   ⚠️ Пропущен локальный донор {Path(raw_video_path).name}: "
                                f"{str(exc)[:100]}"
                            )

                    # A short/corrupt donor may yield fewer clips than planned.
                    # Refill one-by-one from the remaining donors; the global
                    # range allocator guarantees these attempts cannot overlap
                    # any range already used in this video.
                    refill_without_progress = 0
                    refill_plan = reserve_diverse_path_cycle(
                        custom_videos,
                        max(0, youtube_clips_needed - len(extracted_clips))
                        + len(custom_videos),
                    )
                    for raw_video_path in refill_plan:
                        if len(extracted_clips) >= youtube_clips_needed:
                            break
                        before = len(extracted_clips)
                        try:
                            clips = mixer.extract_random_clips(
                                video_path=str(raw_video_path),
                                output_dir=clips_dir,
                                num_clips=1,
                                min_clip_duration=clip_min_duration,
                                max_clip_duration=clip_max_duration,
                                existing_clips=extracted_clips,
                            )
                            extracted_clips.extend(clips or [])
                        except Exception:
                            pass
                        refill_without_progress = (
                            refill_without_progress + 1
                            if len(extracted_clips) == before else 0
                        )
                        if refill_without_progress >= len(custom_videos):
                            break
                    
                    if extracted_clips:
                        extracted_clips, _ranking = rank_for_current_script(
                            extracted_clips, youtube_clips_needed
                        )
                        pipeline_time = time_module.time() - pipeline_start
                        log_callback(f"⏱️ Custom Videos Pipeline завершён за {pipeline_time:.1f}s")
                        log_callback(f"📊 Извлечено {len(extracted_clips)} клипов из своих видео")
                        selected = finalize_clip_selection(
                            extracted_clips, youtube_clips_needed
                        )
                        if len(selected) < youtube_clips_needed:
                            log_callback(
                                f"⚠️ Уникальных локальных клипов только {len(selected)}/"
                                f"{youtube_clips_needed}; повторять диапазоны не будем"
                            )
                        return selected
                    else:
                        if not online_sources_enabled:
                            log_callback("⚠️ Не удалось извлечь клипы из своих видео, а онлайн-источники клипов отключены")
                            return []
                        log_callback("⚠️ Не удалось извлечь клипы из своих видео, переключаемся на YouTube...")
                else:
                    if not online_sources_enabled:
                        log_callback("⚠️ Папка со своими видео пуста или не содержит видео, а онлайн-источники клипов отключены")
                        return []
                    log_callback("⚠️ Папка пуста или не содержит видео, переключаемся на YouTube...")

            if not online_sources_enabled:
                if youtube_settings.get('enable_local_videos', False):
                    log_callback(
                        "⚠️ Включены только локальные видео, но корректная папка с видео не найдена"
                    )
                else:
                    log_callback("⚠️ Все источники видеоклипов отключены")
                return []

            # 🚀 ОПТИМИЗАЦИЯ: Проверяем тематический батч-кэш (pre-downloaded клипы)
            clips_dir = output_dir / "youtube_clips" / theme_cache_key
            legacy_clips_dir = output_dir / "youtube_clips" / "youtube_clips" / theme_cache_key
            if not clips_dir.exists() and legacy_clips_dir.exists():
                clips_dir = legacy_clips_dir
                log_callback("📦 Найден кэш старого формата, используем без повторного скачивания")
            if clips_dir.exists():
                existing_clips = list(clips_dir.rglob("clip_*.mp4"))
                if existing_clips and len(existing_clips) >= youtube_clips_needed:
                    from core.visual_source_manager import (
                        fresh_existing_paths,
                    )
                    cache_candidates = fresh_existing_paths(
                        existing_clips, session_id=visual_session_id
                    )[
                        :max(youtube_clips_needed * 2, youtube_clips_needed)
                    ]
                    ranked_cache, cache_ranking = rank_for_current_script(
                        cache_candidates, youtube_clips_needed
                    )
                    if cache_ranking and cache_ranking.analysis_available:
                        usable_scored_cache = [
                            path for path in ranked_cache if path in cache_ranking.matches
                        ]
                        strong_cache = list(cache_ranking.accepted_paths)
                        minimum_validated = max(
                            1,
                            math.ceil(
                                youtube_clips_needed
                                * float(youtube_settings.get('visual_relevance_cached_min_ratio', 0.65))
                            ),
                        )
                        if (
                            len(strong_cache) >= minimum_validated
                            and len(usable_scored_cache) >= youtube_clips_needed
                        ):
                            selected_clips = finalize_clip_selection(
                                usable_scored_cache, youtube_clips_needed
                            )
                            log_callback(
                                f"🚀 Тематический кэш подтверждён по сценарию: "
                                f"{len(selected_clips)} из {len(existing_clips)}"
                            )
                            return selected_clips
                        seed_clips.extend(
                            path for path in usable_scored_cache if path not in seed_clips
                        )
                        log_callback(
                            f"🔎 Тематический кэш не покрывает сценарий "
                            f"({len(strong_cache)}/{minimum_validated} сильных) — дополняю поиском"
                        )
                    elif len(ranked_cache) >= youtube_clips_needed:
                        selected_clips = finalize_clip_selection(
                            ranked_cache, youtube_clips_needed
                        )
                        log_callback(
                            f"🚀 Используем pre-downloaded клипы: {len(selected_clips)} "
                            f"из {len(existing_clips)} (Gemini недоступен)"
                        )
                        return selected_clips
                elif existing_clips:
                    log_callback(f"📦 Батч-кэш: {len(existing_clips)} клипов (нужно {youtube_clips_needed})")
                    if youtube_settings.get('_pre_download_clips_ready'):
                        from core.visual_source_manager import fresh_existing_paths
                        selected_clips = fresh_existing_paths(
                            existing_clips, session_id=visual_session_id
                        )
                        selected_clips, cache_ranking = rank_for_current_script(
                            selected_clips, youtube_clips_needed
                        )
                        if cache_ranking and cache_ranking.analysis_available:
                            selected_clips = [
                                path for path in selected_clips if path in cache_ranking.matches
                            ]
                            seed_clips.extend(
                                path for path in selected_clips if path not in seed_clips
                            )
                            log_callback(
                                f"🔎 Батч-кэш дал {len(selected_clips)} проверенных клипов; "
                                "недостающее добираю точечным поиском"
                            )
                        else:
                            log_callback(
                                f"🔒 Батч-режим: используем доступные клипы: {len(selected_clips)}"
                            )
                            return finalize_clip_selection(
                                selected_clips, youtube_clips_needed
                            )
                    log_callback("🔄 Pre-download не набрал достаточно клипов — запускаем поиск для текущего видео")
            elif youtube_settings.get('_pre_download_clips_ready'):
                log_callback("⚠️ Батч-режим: pre-download включён, но клипы не найдены — запускаем YouTube скачивание для текущего видео")
            elif kwargs.get('batch_total_videos', 1) > 1:
                log_callback("🔄 Батч-кэш пуст — запускаем поиск видео для текущего ролика")
            
            # Передаём api_key для AI перевода тем
            mixer_api_key = kwargs.get('api_key') or kwargs.get('google_ai_api_key')
            mixer = YouTubeMixer(
                log_callback=log_callback,
                api_key=mixer_api_key,
                youtube_data_api_key=youtube_settings.get('youtube_data_api_key'),
                mixer_settings=youtube_settings,
            )
            mixer.visual_session_id = visual_session_id
            
            # Рассчитываем сколько минут контента скачать
            budget = estimate_youtube_source_budget(
                total_clips_needed=youtube_clips_needed,
                target_duration_minutes=calculate_youtube_duration(
                    num_videos=1,
                    video_duration_seconds=total_duration,
                    video_ratio=video_ratio,
                ),
                min_clip_duration=clip_min_duration,
                max_clip_duration=clip_max_duration,
                batch_total_videos=kwargs.get('batch_total_videos', 1),
                target_orientation=target_orientation,
                mixer_settings=youtube_settings,
            )
            target_duration_minutes = budget['target_duration_minutes']
            
            batch_total = kwargs.get('batch_total_videos', 1)
            
            # 🚀 Используем ПАРАЛЛЕЛЬНУЮ версию!
            youtube_clips = mixer.download_and_extract_clips_parallel(
                theme=theme,
                total_clips_needed=youtube_clips_needed,
                target_duration_minutes=target_duration_minutes,
                output_dir=output_dir,
                min_clip_duration=clip_min_duration,
                max_clip_duration=clip_max_duration,
                batch_total_videos=batch_total,
                max_download_concurrent=1,  # Один запрос за раз снижает риск антибот-блокировки
                max_extract_concurrent=4,   # Ограничение CPU
                target_orientation=target_orientation,  # 📐 Фильтр по ориентации
                visual_context=visual_context,
            )
            
            pipeline_time = time_module.time() - pipeline_start
            log_callback(f"⏱️ Пайплайн видеоклипов завершён за {pipeline_time:.1f}s")
            log_callback(f"📊 _process_youtube_clips возвращает: {len(youtube_clips) if youtube_clips else 0} клипов")
            
            if youtube_clips:
                combined_clips = list(dict.fromkeys(seed_clips + list(youtube_clips)))
                combined_clips, _ranking = rank_for_current_script(
                    combined_clips, youtube_clips_needed
                )
                fresh_youtube_clips = finalize_clip_selection(
                    combined_clips, youtube_clips_needed
                )
                return fresh_youtube_clips
            return finalize_clip_selection(seed_clips, youtube_clips_needed)
            
        except Exception as yt_error:
            log_callback(f"⚠️ Ошибка пайплайна видеоклипов: {yt_error}")
            log_callback(f"   {traceback.format_exc()[:300]}")
            return []

    @staticmethod
    def _calculate_effective_workers(
        requested_workers: int,
        num_videos: int,
        has_custom_texts: bool,
        audio_settings: dict,
        video_settings: dict,
    ) -> int:
        """Use the selected parallelism while keeping API and render load bounded."""
        requested = max(1, min(int(requested_workers or 1), 8))
        total = max(1, int(num_videos or 1))
        provider = str((audio_settings or {}).get('provider', 'gemini')).lower()
        tts_enabled = bool((audio_settings or {}).get('enabled', True))
        duration = float((video_settings or {}).get('duration', 30) or 30)
        pixels = int((video_settings or {}).get('width', 1080) or 1080) * int(
            (video_settings or {}).get('height', 1920) or 1920
        )

        api_cap = 4 if has_custom_texts else 3
        if tts_enabled and provider == 'gemini':
            api_cap = min(api_cap, 2)
        elif tts_enabled and provider == 'edge':
            api_cap = min(api_cap, 3)

        render_cap = 4
        if duration >= 300 or pixels > 2560 * 1440:
            render_cap = 2
        elif duration >= 120:
            render_cap = 3
        return max(1, min(requested, total, api_cap, render_cap))
    
    def _create_mixed_media_list(self, image_paths: list, youtube_clips: list, 
                                   total_shots: int = None, video_ratio: float = 0.3,
                                   start_with_images: bool = False,
                                   first_shot_from_pool: bool = False,
                                   allow_video_reuse: bool = False) -> list:
        """Build a varied image/video sequence through the dedicated planner."""
        return self.media_distribution.create_mixed_media_list(
            image_paths,
            youtube_clips,
            total_shots=total_shots,
            video_ratio=video_ratio,
            start_with_images=start_with_images,
            first_shot_from_pool=first_shot_from_pool,
            allow_video_reuse=allow_video_reuse,
        )

    @staticmethod
    def _create_remake_media_sequence(
        video_clips: list,
        total_shots: int,
        first_image: str | Path | None = None,
    ) -> list:
        """Preserve the remixer order and optionally reserve shot zero for an image."""
        total = max(1, int(total_shots or 1))
        clips = [str(path) for path in (video_clips or []) if path]
        sequence = []
        if first_image:
            sequence.append((str(first_image), 'image'))
        video_slots = total - len(sequence)
        if video_slots <= 0 or not clips:
            return sequence
        sequence.extend(
            (clips[index % len(clips)], 'video')
            for index in range(video_slots)
        )
        return sequence

    @staticmethod
    def _put_pool_image_first(mixed_media: list, image_paths: list, log_callback) -> list:
        """Put an image first without adding a shot or changing media counts."""
        if not mixed_media or not image_paths:
            return mixed_media
        if mixed_media[0][1] == 'image':
            log_callback(f"🖼️ Первый шот уже картинка: {Path(mixed_media[0][0]).name}")
            return mixed_media

        image_index = next(
            (index for index, (_, media_type) in enumerate(mixed_media[1:], start=1)
             if media_type == 'image'),
            None,
        )
        if image_index is not None:
            mixed_media[0], mixed_media[image_index] = mixed_media[image_index], mixed_media[0]
            log_callback(
                f"🖼️ Первый шот: картинка из плана ({Path(mixed_media[0][0]).name}); "
                "пропорция сохранена"
            )
            return mixed_media

        first_image = str(image_paths[0])
        mixed_media[0] = (first_image, 'image')
        log_callback(
            f"🖼️ Первый шот заменён на картинку из пула ({Path(first_image).name}); "
            "в плане не было изображений"
        )
        return mixed_media

    @staticmethod
    def _extract_title_subject(text: str, theme: str = '', filename: str = '') -> str:
        """Extract the text-specific subject instead of a repeated template opening."""
        source = re.sub(r'^\s*\(\d+\)\s*', '', str(text or '')).strip()
        patterns = (
            r'[«"]([^»"\n]{3,80})[»"]',
            r'когда\s+речь\s+(?:ид[её]т|заходит)\s+про\s+(.+?)(?:,|\.|;|\n)',
            r'на\s+первый\s+взгляд\s+(.+?)\s+может\s+',
            r'в\s+вопросе\s+(.+?)\s+особенно\s+',
            r'^\s*тема\s+(.+?)\s+важна\s+',
        )
        subject = ''
        for pattern in patterns:
            match = re.search(pattern, source, flags=re.IGNORECASE)
            if match:
                subject = match.group(1)
                break

        if not subject:
            filename_subject = re.sub(r'^\d+[\s_.-]*', '', Path(str(filename or '')).stem)
            if filename_subject and re.search(r'[А-Яа-яЁё]', filename_subject):
                subject = filename_subject.replace('_', ' ')

        if not subject:
            subject = str(theme or '').strip()
        if not subject:
            subject = re.split(r'[,.!?;:\n—–]', source, maxsplit=1)[0]

        subject = re.sub(r'[^0-9A-Za-zА-Яа-яЁё\- ]+', ' ', subject)
        subject = ' '.join(subject.split()).strip(' -')
        words = subject.split()[:7]
        while words and len(' '.join(words)) > 42:
            words.pop()
        return ' '.join(words).strip() or 'полезный разбор'

    @classmethod
    def _fallback_title_candidates(
        cls, text: str, theme: str, video_num: int, filename: str = ''
    ) -> list:
        """Return factual, text-derived fallbacks without stock title templates."""
        subject = cls._extract_title_subject(text, theme, filename)
        subject_display = subject[:1].upper() + subject[1:] if subject else 'Полезный разбор'
        candidates = [subject_display]
        for sentence in re.split(r'(?<=[.!?])\s+|[\r\n]+', str(text or ''))[:4]:
            clean = re.sub(r'^\s*\(\d+\)\s*', '', sentence).strip(' \t\r\n"\'«»')
            words = clean.split()
            if len(words) < 3:
                continue
            compact = ' '.join(words[:12]).rstrip(' ,.:;!?-')
            if len(compact) > 96:
                compact = compact[:96].rsplit(' ', 1)[0].rstrip(' ,.:;!?-')
            if subject.casefold() not in compact.casefold():
                compact = f"{subject_display} {compact}"[:96].rstrip(' ,.:;!?-')
            candidates.append(compact)

        unique = []
        seen = set()
        for candidate in candidates:
            key = cls._title_key(candidate)
            if key and key not in seen:
                seen.add(key)
                unique.append(candidate)
        return unique or [subject_display]

    @classmethod
    def _build_fallback_title(
        cls, text: str, theme: str, video_num: int, filename: str = ''
    ) -> str:
        """Build a text-specific catchy title without relying on AI."""
        return cls._fallback_title_candidates(text, theme, video_num, filename)[0]

    @staticmethod
    def _title_key(title: str) -> str:
        # Compare the form that can actually become a Windows filename.
        normalized = re.sub(r'[<>:"/\\|?*]+', ' ', str(title or ''))
        normalized = re.sub(r'[^0-9A-Za-zА-Яа-яЁё\- ]+', ' ', normalized)
        return re.sub(r'\s+', ' ', normalized).strip().casefold()

    def _reset_title_registry(self, output_dir: Path) -> None:
        """Seed title uniqueness with files already present for safe resume."""
        if not hasattr(self, '_title_lock'):
            self._title_lock = threading.Lock()
        existing_paths = sorted(
            (path for path in Path(output_dir).glob('*.mp4') if path.is_file()),
            key=lambda path: path.name.casefold(),
        )
        existing_values = [path.stem for path in existing_paths if self._title_key(path.stem)]
        existing = {self._title_key(value) for value in existing_values}
        with self._title_lock:
            self._used_titles = existing
            self._used_title_values = existing_values

    def _recent_used_titles(self, limit: int = 10) -> list:
        with self._title_lock:
            values = getattr(self, '_used_title_values', [])
            return list(values[-max(1, int(limit)):])

    def _reserve_title(self, title: str) -> bool:
        """Atomically reserve a title across parallel video workers."""
        key = self._title_key(title)
        if not key:
            return False
        with self._title_lock:
            if key in self._used_titles:
                return False
            self._used_titles.add(key)
            if not hasattr(self, '_used_title_values'):
                self._used_title_values = []
            self._used_title_values.append(str(title).strip())
            return True

    def _reserve_diverse_title(
        self,
        title: str,
        subject_topic: str = '',
        parent_theme: str = '',
    ) -> bool:
        """Atomically reject exact duplicates and repeated title skeletons."""
        key = self._title_key(title)
        if not key:
            return False
        with self._title_lock:
            if key in self._used_titles:
                return False
            previous = list(getattr(self, '_used_title_values', []))
            if is_near_duplicate_title(
                title,
                previous,
                anchors=(subject_topic, parent_theme),
            ):
                return False
            self._used_titles.add(key)
            if not hasattr(self, '_used_title_values'):
                self._used_title_values = []
            self._used_title_values.append(str(title).strip())
            return True

    def _reserve_fallback_title(
        self, text: str, theme: str, video_num: int, filename: str = ''
    ) -> str:
        """Reserve the first unused subject-specific fallback title."""
        candidates = self._fallback_title_candidates(text, theme, video_num, filename)
        for candidate in candidates:
            if self._reserve_title(candidate):
                return candidate

        base = candidates[0]
        suffix = max(1, int(video_num or 1))
        while True:
            candidate = f"{base[:54].rstrip()} {suffix}"
            if self._reserve_title(candidate):
                return candidate
            suffix += 1

    @staticmethod
    def _is_video_only_requested(youtube_settings: dict) -> bool:
        """Return True when the user explicitly requested an all-video timeline."""
        if not youtube_settings or not youtube_settings.get('enabled', False):
            return False
        try:
            return float(youtube_settings.get('video_ratio', 0.0)) >= 0.999
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _has_online_video_sources_enabled(youtube_settings: dict) -> bool:
        """Return True when any online clip source is enabled for the current run."""
        if not youtube_settings or youtube_settings.get('enabled') is False:
            return False
        return any([
            bool(youtube_settings.get('enable_youtube', True)),
            bool(youtube_settings.get('enable_pexels', False)),
            bool(youtube_settings.get('enable_pixabay_videos', False)),
            bool(youtube_settings.get('enable_wikimedia_videos', False)),
        ])

    @staticmethod
    def _validate_visual_source_config(youtube_settings: dict) -> Optional[str]:
        """Validate strict source selections shared by GUI, queue and series runs."""
        if not youtube_settings or not youtube_settings.get('enabled', False):
            return None

        enable_youtube = bool(youtube_settings.get('enable_youtube', True))
        enable_local = bool(youtube_settings.get('enable_local_videos', True))
        enable_pexels = bool(youtube_settings.get('enable_pexels', False))
        enable_pixabay = bool(youtube_settings.get('enable_pixabay_videos', False))
        enable_wikimedia = bool(youtube_settings.get('enable_wikimedia_videos', False))
        if not any((enable_youtube, enable_local, enable_pexels, enable_pixabay, enable_wikimedia)):
            return "Не выбран ни один источник видеоклипов"

        folder_value = str(youtube_settings.get('custom_videos_folder') or '').strip()
        folder = Path(folder_value) if folder_value else None
        extensions = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
        local_usable = bool(
            enable_local
            and folder
            and folder.is_dir()
            and any(path.is_file() and path.suffix.lower() in extensions for path in folder.rglob('*'))
        )
        pexels_usable = bool(enable_pexels and youtube_settings.get('pexels_api_key'))
        pixabay_usable = bool(enable_pixabay and youtube_settings.get('pixabay_api_key'))
        wikimedia_usable = enable_wikimedia
        if not any((enable_youtube, local_usable, pexels_usable, pixabay_usable, wikimedia_usable)):
            if enable_local and not any((enable_youtube, enable_pexels, enable_pixabay, enable_wikimedia)):
                return (
                    "Выбран режим «только локальные видео», но папка не указана, "
                    "не существует или не содержит видеофайлов"
                )
            if enable_pexels or enable_pixabay:
                return "Для выбранного стокового видеоисточника не настроен API-ключ"
            return "Нет доступных источников видеоклипов"
        return None

    @staticmethod
    def _batch_video_max_attempts(source_data: dict) -> int:
        """Return a bounded whole-video retry count for incomplete batches."""
        try:
            requested = int((source_data or {}).get('batch_video_max_attempts', 2) or 2)
        except (TypeError, ValueError):
            requested = 2
        return max(1, min(3, requested))

    @staticmethod
    def _run_batch_video_attempts(
        operation,
        *,
        video_num: int,
        theme: str,
        max_attempts: int,
        log_callback,
        should_stop=None,
        base_delay: float = 2.0,
    ):
        """Retry a complete video pipeline, then re-raise the last real error."""
        last_error = None
        for attempt in range(1, max(1, int(max_attempts)) + 1):
            try:
                return operation()
            except Exception as error:
                last_error = error
                if attempt >= max_attempts or (should_stop and should_stop()):
                    break
                delay = max(0.0, float(base_delay)) * attempt
                log_callback(
                    f"🔁 Видео #{video_num} не собрано с попытки {attempt}/{max_attempts}: {error}"
                )
                log_callback(
                    f"   Повторяем весь ролик через {delay:.0f} сек., тема: '{theme}'"
                )
                if delay:
                    time.sleep(delay)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Видео #{video_num} не было запущено")

    @staticmethod
    def _audio_retry_wait(provider: str, attempt_index: int) -> int:
        """Keep free Edge TTS outages from blocking a generation for minutes."""
        if str(provider or '').lower() == 'edge':
            return 5 * (max(0, int(attempt_index)) + 1)
        return 60 * (max(0, int(attempt_index)) + 1)

    @staticmethod
    def _trim_text_to_word_budget(
        text: str,
        max_words: int,
        language: str = 'English',
    ) -> str:
        """Trim narration by Unicode-safe units near a sentence boundary.

        Whitespace-delimited languages retain their historical word-based
        behavior. Chinese, Japanese, and unspaced Hangul use the same compact
        display units as subtitles, so duration auto-fit cannot mistake a
        complete script for a single word.
        """
        original = str(text or '').strip()
        units, separator = split_subtitle_units(original)
        if len(units) <= max_words:
            return original

        truncated = join_subtitle_units(
            units[:max(1, int(max_words))],
            separator,
        ).strip()
        sentence_endings = '.!?\u3002\uff01\uff1f\u061f\u0964\u0965'
        last_sentence_end = max(
            (truncated.rfind(character) for character in sentence_endings),
            default=-1,
        )
        if last_sentence_end >= len(truncated) * 0.65:
            return truncated[:last_sentence_end + 1].strip()
        trailing_punctuation = '.,!?;:\u3002\uff01\uff1f\u061f\u0964\u0965\uff1b\uff1a\u3001\uff0c'
        ellipsis = '\u2026' if language in {
            'Chinese', 'Japanese', 'Korean', 'Arabic', 'Hindi'
        } else '...'
        return truncated.rstrip(trailing_punctuation) + ellipsis
    
    def _create_youtube_first_distribution(self, images: list, clips: list, 
                                            num_images: int, num_clips: int, total_shots: int,
                                            start_with_images: bool = False,
                                            first_shot_from_pool: bool = False) -> list:
        """Compatibility wrapper for callers that still use the old helper name."""
        return self.media_distribution.create_video_first_distribution(
            images,
            clips,
            num_images,
            num_clips,
            total_shots,
            start_with_images=start_with_images,
            first_shot_from_pool=first_shot_from_pool,
        )
    
    def _create_no_repeat_sequence(self, items: list, total_needed: int, item_type: str) -> list:
        """Compatibility wrapper for the dedicated distribution planner."""
        return self.media_distribution.create_no_repeat_sequence(items, total_needed, item_type)
    
    def _interleave_no_repeat(self, images: list, clips: list, num_images: int, num_clips: int) -> list:
        """Compatibility wrapper for even image/video interleaving."""
        return self.media_distribution.interleave_no_repeat(images, clips, num_images, num_clips)
    
    def _fix_consecutive_repeats(self, media_list: list) -> list:
        """Compatibility wrapper for repeat repair."""
        return self.media_distribution.fix_consecutive_repeats(media_list)
    
    def _get_unique_filepath(self, base_path: Path) -> Path:
        """Генерирует уникальное имя файла, добавляя номер только если файл существует."""
        if not base_path.exists():
            return base_path
        
        # Файл существует - добавляем номер
        stem = base_path.stem
        suffix = base_path.suffix
        parent = base_path.parent
        
        counter = 2
        while True:
            new_path = parent / f"{stem}_{counter}{suffix}"
            if not new_path.exists():
                return new_path
            counter += 1

    def _attach_opening_hook_package(
        self,
        text_content: dict,
        theme: str,
        language: str,
        log_callback=None,
        target_duration: float = None,
        variation_index: int = None,
    ) -> dict:
        """Attach universal first-screen hook metadata before rendering."""
        if not isinstance(text_content, dict):
            return text_content

        hook_package = build_opening_hook_package(
            title=text_content.get('title', ''),
            theme=text_content.get('theme') or theme,
            full_text=text_content.get('full_text', ''),
            language=language,
            existing_hooks=text_content.get('opening_hooks'),
            variation_index=variation_index,
        )
        text_content['opening_hook_package'] = hook_package
        text_content['opening_hooks'] = hook_package.get('opening_hooks', [])
        text_content['opening_hook_family'] = hook_package.get('primary_family', '')
        text_content['first_shot_hook'] = hook_package.get('first_shot_title', '')
        text_content['primary_hook'] = hook_package.get('primary_hook', '')

        is_short = target_duration is not None and float(target_duration) <= 60.0
        if is_short and not text_content.get('custom_text_mode'):
            original_full_text = str(text_content.get('full_text') or '')
            improved_full_text = replace_generic_script_opening(
                original_full_text,
                text_content.get('primary_hook', ''),
                language,
            )
            if improved_full_text != original_full_text:
                text_content['full_text'] = improved_full_text
                text_parts = text_content.get('text_parts')
                if isinstance(text_parts, list) and text_parts:
                    text_parts[0] = replace_generic_script_opening(
                        str(text_parts[0] or ''),
                        text_content.get('primary_hook', ''),
                        language,
                    )
                if log_callback:
                    log_callback("🎯 Удалено шаблонное вступление: ролик начинается сразу с предметного хука")
        if log_callback and text_content.get('first_shot_hook'):
            log_callback(f"🎯 Хук первого кадра: {text_content['first_shot_hook']}")
        return text_content

    def _get_text_content(self, theme, duration, video_num, kwargs):
        log_callback = kwargs['log_callback']
        subtitle_settings = kwargs['subtitle_settings']
        
        # 📝 НОВОЕ: Проверка на custom text
        source_data = kwargs.get('source_data', {})
        custom_texts = source_data.get('custom_texts')
        
        # Получаем тему из GUI (нужна для анализа и AI генерации названий)
        gui_theme = source_data.get('theme', '') or kwargs.get('main_theme', theme)
        
        # 🔍 УМНОЕ ВЫЖИГАНИЕ: Анализ всех кастомных текстов для определения общей темы
        if custom_texts and video_num == 1:
            # Анализируем все тексты только один раз (для первого видео)
            if not hasattr(self, '_custom_texts_common_theme'):
                api_key = kwargs.get('google_ai_api_key') or kwargs.get('api_key') or ''
                config_manager = kwargs.get('config_manager')
                if not api_key and config_manager:
                    api_key = config_manager.get_user_setting('gemini_api_key') or ''
                
                if api_key:
                    from core.custom_text_analyzer import analyze_custom_texts_theme
                    self._custom_texts_common_theme = analyze_custom_texts_theme(
                        custom_texts=custom_texts,
                        api_key=api_key,
                        main_theme=gui_theme,  # Используем тему из GUI, а не название первого текста
                        log_callback=log_callback
                    )
                else:
                    log_callback("⚠️ Нет API ключа для анализа темы, используем тему из GUI")
                    self._custom_texts_common_theme = gui_theme
        
        if custom_texts:
            # Используем custom text вместо AI генерации
            # video_num начинается с 1, индекс с 0
            text_index = video_num - 1
            
            if text_index < len(custom_texts):
                custom_text = custom_texts[text_index]
                log_callback(f"📝 Используется кастомный текст #{video_num}: '{custom_text.title}'")
                log_callback(f"   Длина текста: {len(custom_text.text)} символов")
                log_callback(f"   Расчётная длительность: {custom_text.duration}s")
                
                # Создаём text_content в формате как у AI генератора
                # Разбиваем текст на сегменты для субтитров
                min_shot_duration = kwargs['video_settings'].get('shot_min_duration', 3)
                max_shot_duration = kwargs['video_settings'].get('shot_max_duration', 5)
                avg_shot_duration = (min_shot_duration + max_shot_duration) / 2
                num_segments = max(1, int(custom_text.duration / avg_shot_duration))
                
                # Простое разбиение текста на предложения
                sentences = re.split(r'[.!?]+', custom_text.text)
                sentences = [s.strip() for s in sentences if s.strip()]
                
                # Распределяем предложения по сегментам
                text_parts = []
                sentences_per_segment = max(1, len(sentences) // num_segments)
                
                for i in range(num_segments):
                    start_idx = i * sentences_per_segment
                    end_idx = start_idx + sentences_per_segment if i < num_segments - 1 else len(sentences)
                    segment_sentences = sentences[start_idx:end_idx]
                    if segment_sentences:
                        text_parts.append('. '.join(segment_sentences) + '.')
                
                # Если не удалось разбить - используем весь текст как один сегмент
                if not text_parts:
                    text_parts = [custom_text.text]
                
                actual_title = custom_text.title
                
                # Check the explicit flag set by TextLoader instead of making guesses
                if getattr(custom_text, 'is_fallback_title', False):
                    # Включаем глубокую ревизию: генерируем короткий заголовок с помощью AI
                    api_key = kwargs.get('google_ai_api_key') or kwargs.get('api_key') or ''
                    config_manager = kwargs.get('config_manager')
                    if not api_key and config_manager:
                         api_key = config_manager.get_user_setting('gemini_api_key') or ''
                         
                    if api_key:
                        log_callback("💡 Замечен бессмысленный заголовок из обрывка текста. Генерируем нормальное название через AI...")
                        from core.gemini_client import GeminiClient
                        client = GeminiClient(api_key)
                        
                        clean_theme = getattr(self, '_custom_texts_common_theme', '') or gui_theme
                        if len(clean_theme) > 180:
                            clean_theme = clean_theme[:180].rsplit(' ', 1)[0]
                        
                        # Извлекаем уникальную тему текста, а не повторяющееся начало шаблона.
                        _title_subject = self._extract_title_subject(
                            custom_text.text,
                            clean_theme,
                            getattr(custom_text, 'filename', ''),
                        )
                        _title_context = ' '.join(str(custom_text.text or '').split())[:700]
                        _clean_subject = _title_subject
                        _clean_context = _title_context
                        _clean_theme_for_title = clean_theme
                        _clean_subject = ' '.join(_clean_subject.split())
                        _clean_context = ' '.join(_clean_context.split())
                        _clean_theme_for_title = ' '.join(_clean_theme_for_title.split())
                        
                        actual_title = None
                        for _title_attempt in range(2):
                            try:
                                _recent_titles = self._recent_used_titles(10)
                                _used_list = '\n'.join(f"- {item}" for item in _recent_titles) if _recent_titles else '- пока нет'
                                _title_rules = subject_first_prompt_rules(
                                    normalize_language(kwargs.get('language', 'Russian'))
                                )
                                prompt = f"""Придумай короткое цепляющее название для вертикального видео.

Общая тема серии (контекст): {_clean_theme_for_title}
Конкретный предмет этого видео (главный якорь): {_clean_subject}
Фрагмент сценария (источник фактов): {_clean_context}

Уже использованные заголовки, чью конструкцию нельзя повторять:
{_used_list}

{_title_rules}

Требования:
1. От 4 до 13 слов, максимум 100 символов
2. Нормальная пунктуация и естественная законченная фраза
3. Конкретный предмет — смысловой центр; общая тема вплетена естественно
4. Никаких перечней ключевых слов и пар существительных через двоеточие
5. Не выдумывай факты и не копируй синтаксис предыдущих заголовков

Ответ (ТОЛЬКО название):"""
                                
                                # Клиент сам перебирает актуальную цепочку текстовых моделей.
                                resp = client.generate_text(prompt, temperature=0.85, max_tokens=80)
                                if not (resp.success and resp.raw_text):
                                    _ai_error = resp.error or 'пустой ответ'
                                    log_callback(f"   ⚠️ Попытка {_title_attempt+1}/2: AI не создал название ({_ai_error})")
                                    import time as _time; _time.sleep(1)
                                    continue
                                
                                # 🔍 Проверка на обрезку по токенам
                                if resp.finish_reason == 'MAX_TOKENS':
                                    log_callback(f"   ⚠️ Попытка {_title_attempt+1}/2: ответ обрезан (MAX_TOKENS): '{resp.raw_text[:30]}'")
                                    import time as _time; _time.sleep(1)
                                    continue
                                
                                candidate = resp.raw_text.strip(' \n\r"\'*.)')
                                # Убираем маркдаун и лишние символы
                                candidate = candidate.lstrip('#- ').strip()
                                # Берём только первую строку (если Gemini вернул пояснение)
                                if '\n' in candidate:
                                    candidate = candidate.split('\n')[0].strip()
                                
                                # Валидация
                                _reject_reason = None
                                if candidate.upper().startswith('THOUGHT') or 'мысль вслух' in candidate.lower():
                                    _reject_reason = "AI вернул THOUGHT"
                                elif len(candidate) > 100:
                                    _reject_reason = f"слишком длинное ({len(candidate)})"
                                elif len(candidate) < 10:
                                    _reject_reason = f"слишком короткое ({len(candidate)})"
                                elif candidate.lower() == clean_theme.lower():
                                    _reject_reason = "совпадает с common_theme"
                                elif not title_has_specific_evidence(
                                    candidate,
                                    _clean_subject,
                                    _clean_theme_for_title,
                                ):
                                    _reject_reason = "нет конкретного предмета видео"
                                elif looks_catalog_like(candidate):
                                    _reject_reason = "похоже на каталог ключевых слов"
                                
                                # Проверка обрезки
                                if not _reject_reason:
                                    words = candidate.split()
                                    if words and len(words[-1]) <= 2 and len(words) > 1:
                                        _reject_reason = f"обрезано: '{words[-1]}'"
                                    elif candidate.endswith('-'):
                                        _reject_reason = "обрезано (дефис)"
                                
                                if _reject_reason:
                                    log_callback(f"   🔄 Попытка {_title_attempt+1}/2 отклонена ({_reject_reason}): '{candidate}'")
                                    import time as _time; _time.sleep(1)
                                    continue
                                
                                # Атомарный резерв не даёт параллельным видео выбрать одно имя.
                                if self._reserve_diverse_title(
                                    candidate,
                                    subject_topic=_clean_subject,
                                    parent_theme=_clean_theme_for_title,
                                ):
                                    actual_title = candidate
                                    break
                                log_callback(
                                    f"   🔄 Попытка {_title_attempt+1}/2 отклонена "
                                    f"(повторяет уже использованный заголовок или его конструкцию): '{candidate}'"
                                )
                                
                            except Exception as e:
                                log_callback(f"   ⚠️ Попытка {_title_attempt+1}/2 ошибка: {e}")
                                import time as _time; _time.sleep(2)
                        
                        # Если AI не ответил — используем уникальную тему текущего текста.
                        if not actual_title:
                            actual_title = self._reserve_fallback_title(
                                custom_text.text,
                                clean_theme,
                                video_num,
                                getattr(custom_text, 'filename', ''),
                            )
                            log_callback(f"   ⚠️ Все попытки AI исчерпаны, используем: '{actual_title}'")
                        
                        custom_text.title = actual_title
                        log_callback(f"🌟 Название видео: '{actual_title}'")
                    else:
                        # Нет API ключа — строим уникальное название по теме текущего текста.
                        _base = getattr(self, '_custom_texts_common_theme', '') or gui_theme
                        actual_title = self._reserve_fallback_title(
                            custom_text.text,
                            _base,
                            video_num,
                            getattr(custom_text, 'filename', ''),
                        )
                        custom_text.title = actual_title
                        log_callback(f"   ⚠️ Нет API ключа для AI-названия, используем: '{actual_title}'")
                else:
                    # Явные заголовки из файлов тоже защищаем от случайных дублей.
                    if not self._reserve_title(actual_title):
                        actual_title = self._reserve_fallback_title(
                            custom_text.text,
                            gui_theme,
                            video_num,
                            getattr(custom_text, 'filename', ''),
                        )
                        custom_text.title = actual_title
                        log_callback(f"   ♻️ Повтор заголовка заменён на: '{actual_title}'")
                
                text_content = {
                    'full_text': custom_text.text,
                    'text_parts': text_parts,
                    'title': actual_title,
                    'theme': theme,
                    'title_subject': self._extract_title_subject(
                        custom_text.text,
                        theme,
                        getattr(custom_text, 'filename', ''),
                    ),
                    'remake_source_video': getattr(custom_text, 'remake_source_video', None),
                    'first_shot_image_path': getattr(custom_text, 'first_shot_image_path', None),
                    'estimated_duration': custom_text.duration,
                    'custom_text_mode': True,  # Флаг что это custom text
                    'common_theme': getattr(self, '_custom_texts_common_theme', theme)  # Общая тема для выжигания
                }
                text_content = self._attach_opening_hook_package(
                    text_content,
                    theme,
                    normalize_language(kwargs.get('language', 'Russian')),
                    log_callback,
                    target_duration=duration,
                    variation_index=video_num,
                )
                
                log_callback(f"✅ Кастомный текст подготовлен: {len(text_parts)} сегментов")
                return text_content
            else:
                log_callback(f"⚠️ Кастомный текст #{video_num} не найден (доступно {len(custom_texts)})")
                # Fallback на AI генерацию
        
        # Стандартная AI генерация текста
        log_callback(f"📄 Этап 1 (Видео #{video_num}): Запрос текста для темы '{theme}'...")
        request_theme = theme
        for attempt in range(3):
            try:
                min_shot_duration = kwargs['video_settings'].get('shot_min_duration', 3)
                max_shot_duration = kwargs['video_settings'].get('shot_max_duration', 5)
                avg_shot_duration = (min_shot_duration + max_shot_duration) / 2
                num_segments = max(1, math.ceil(duration / avg_shot_duration))
                
                log_callback(f"📊 Расчетное количество сегментов: {num_segments} (видео {duration}s / средний шот {avg_shot_duration}s)")
                
                # Get language from kwargs (нормализуем для TTS)
                language = normalize_language(kwargs.get('language', 'Russian'))
                log_callback(f"🌍 ЯЗЫК ДЛЯ ГЕНЕРАЦИИ ТЕКСТА: '{language}'")
                
                # 🎭 ВИРУСНАЯ СИСТЕМА: Получаем параметры из source_data
                persona_id = source_data.get('persona_id', None)
                enable_seamless_loop = source_data.get('enable_seamless_loop', True)
                enable_comment_bait = source_data.get('enable_comment_bait', False)
                strict_text_theme = kwargs.get('strict_text_theme', True)
                
                # 📊 Логируем состояние viral опций
                log_callback(f"🎭 Viral настройки: strict_theme={strict_text_theme}, seamless_loop={enable_seamless_loop}, comment_bait={enable_comment_bait}")
                
                text_content = self.text_generator.generate_text(
                    theme=request_theme,
                    api_key=self.api_key,
                    target_duration=duration,
                    num_segments=num_segments,
                    video_num=video_num,
                    char_limit=subtitle_settings.get('char_limit'),
                    log_callback=log_callback,
                    language=language,
                    video_width=kwargs['video_settings'].get('width', 1920),
                    video_height=kwargs['video_settings'].get('height', 1080),
                    strict_text_theme=kwargs.get('strict_text_theme', True),  # ✅ Передаем настройку строгого следования теме
                    persona_id=persona_id,  # 🎭 Передаем выбранную персону
                    use_viral_system=True,  # 🎯 Включаем вирусную систему
                    enable_seamless_loop=enable_seamless_loop,  # 🔄 Бесконечная петля
                    enable_comment_bait=enable_comment_bait  # 🎣 Comment bait
                )
                text_content = self._attach_opening_hook_package(
                    text_content,
                    theme,
                    language,
                    log_callback,
                    target_duration=duration,
                    variation_index=video_num,
                )
                return text_content
            except Exception as e:
                error_str = str(e)
                if "PROHIBITED_CONTENT" in error_str.upper():
                    safe_theme = self._sanitize_theme_for_safety(theme)
                    if request_theme != safe_theme:
                        request_theme = safe_theme
                        log_callback(
                            "🛡️ Gemini заблокировал исходную формулировку. "
                            "Повторяем как нейтральный историко-образовательный рассказ."
                        )
                        continue
                # Увеличенная задержка для 503 errors (overload)
                if "503" in error_str or "overloaded" in error_str.lower():
                    wait_time = 5 + (attempt * 3)  # 5, 8, 11 секунд
                    log_callback(f"⚠️ Модель перегружена (503). Ожидание {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    log_callback(f"⚠️ Этап 1 (Видео #{video_num}): Ошибка генерации текста (попытка {attempt + 1}/3). {e}")
                    time.sleep(2)
        log_callback(f"❌ Не удалось сгенерировать текст для темы '{theme}' после 3 попыток.")
        return None

    @staticmethod
    def _sanitize_theme_for_safety(theme: str) -> str:
        """Rephrase a blocked historical topic without graphic or instructional details."""
        safe = str(theme or "").strip()
        replacements = (
            (r"\b\d{1,2}-летн\w*", "юный"),
            (r"подорвавш\w*\s+себя\s+гранат\w*\s+вместе\s+с\s+окруживш\w*\s+его\s+карател\w*", "не сдавшийся врагу в последнем бою"),
            (r"подорвал\w*\s+себя[^,.;]*", "погиб в окружении, не сдавшись врагу"),
            (r"закрыв\w*\s+(?:грудью|телом)\s+амбразур\w*", "ценой собственной жизни остановивший вражескую огневую точку"),
            (r"казнен\w*", "погибший"),
            (r"ликвидировавш\w*", "проводивший операции против"),
            (r"уничтоживш\w*\s+\d+\s+(?:солдат\w*|человек\w*)", "ставший одним из самых результативных бойцов"),
        )
        for pattern, replacement in replacements:
            safe = re.sub(pattern, replacement, safe, flags=re.IGNORECASE)
        safe = re.sub(r"\s+", " ", safe).strip(" ,.;")
        return (
            f"Историко-образовательный рассказ: {safe}. "
            "Сосредоточься на биографии, мужестве и исторической памяти; "
            "излагай нейтрально, без натуралистических деталей и описания способов причинения вреда."
        )

    def _build_local_script(self, theme: str, duration: int, kwargs) -> dict:
        """Offline fallback: build a neutral, safe script without external APIs."""
        min_shot = kwargs['video_settings'].get('shot_min_duration', 3)
        max_shot = kwargs['video_settings'].get('shot_max_duration', 5)
        avg_shot = (min_shot + max_shot) / 2
        num_segments = max(1, int(duration / avg_shot))
        # Estimate words
        target_words = max(80, min(300, int(duration / 60 * 150)))
        words_per_segment = max(10, target_words // num_segments)
        base_sentences = [
            f"{theme}: краткий факт без оценочных суждений.",
            f"Термины и определения по теме '{theme}' изложены нейтрально.",
            f"Исторический и контекстный обзор темы '{theme}'.",
            f"Практическое значение и применение '{theme}'.",
            "Интересные детали, упомянутые без упора на спорные моменты.",
        ]
        text_parts = []
        for i in range(num_segments):
            part_sentences = []
            while len(" ".join(part_sentences).split()) < words_per_segment:
                part_sentences.append(base_sentences[(i + len(part_sentences)) % len(base_sentences)])
            text_parts.append(" ".join(part_sentences))
        full_text = " ".join(text_parts)
        return {
            'title': f"Интересные факты: {theme}",
            'description': f"Нейтральный обзор темы '{theme}'.",
            'full_text': full_text,
            'text_parts': text_parts,
            'hashtags': [f"#{theme.split(maxsplit=1)[0]}"]
        }

    def _cleanup_temp_assets(self, output_dir: Path, log_callback):
        """Clean up temporary assets after successful video generation"""
        import time
        
        try:
            assets_dir = output_dir / "assets"
            deleted_count = 0
            
            if assets_dir.exists():
                # Delete AI generated images
                for img in assets_dir.glob("ai_image_*.png"):
                    try:
                        img.unlink()
                        deleted_count += 1
                    except Exception as e:
                        log_callback(f"⚠️ Не удалось удалить {img}: {e}")
                        
                # Delete subtitle files
                for sub in assets_dir.glob("subtitle_*.txt"):
                    try:
                        sub.unlink()
                        deleted_count += 1
                    except Exception as e:
                        log_callback(f"⚠️ Не удалось удалить {sub}: {e}")
                        
                # Delete fallback images
                for fb in assets_dir.glob("fallback_*.png"):
                    try:
                        fb.unlink()
                        deleted_count += 1
                    except Exception as e:
                        log_callback(f"⚠️ Не удалось удалить {fb}: {e}")
            
            # Delete temp audio files (wait a bit for file handles to close)
            time.sleep(0.5)
            for pattern in ("voiceover_*.mp3", "voiceover_*.wav", "*.edge_raw.mp3"):
                for vo in output_dir.glob(pattern):
                    try:
                        vo.unlink()
                        deleted_count += 1
                    except Exception:
                        # File still in use - skip it
                        pass
            
            # Delete temp final_audio files
            for fa in output_dir.glob("final_audio_*.mp3"):
                try:
                    fa.unlink()
                    deleted_count += 1
                except Exception:
                    # File still in use - skip it
                    pass
                    
            if deleted_count > 0:
                log_callback(f"🗑️ Очищено {deleted_count} временных файлов")
                
        except Exception as e:
            log_callback(f"⚠️ Ошибка при очистке: {e}")

    def _process_audio(self, text_content, output_dir, kwargs):
        """Process audio: generate voiceover and add background music."""
        log_callback = kwargs['log_callback']
        music_path = kwargs['music_path']
        
        log_callback("🎵 Этап 2: Обработка аудио...")
        
        audio_settings = kwargs.get('audio_settings') or {'enabled': True, 'provider': 'edge', 'voice': '', 'speech_speed': 1.0}
        tts_enabled = audio_settings.get('enabled', True)
        
        # Если озвучка отключена — используем только музыку
        if not tts_enabled:
            log_callback("🔇 Озвучка отключена — используем только музыку")
            video_settings = kwargs.get('video_settings', {})
            target_duration = video_settings.get('duration', 30)
            
            # Создаём аудио только из музыки
            if music_path and Path(music_path).exists():
                try:
                    final_audio_path = self.audio_processor.create_music_only_audio(
                        music_path=music_path,
                        duration=target_duration,
                        output_dir=output_dir,
                        log_callback=log_callback,
                        volume=video_settings.get('music_volume', 0.5)
                    )
                    if final_audio_path:
                        log_callback(f"✅ Музыка готова: {target_duration:.1f}s")
                        return final_audio_path, target_duration, None
                except Exception as e:
                    log_callback(f"⚠️ Ошибка создания музыки: {e}")
            
            log_callback("❌ Нет музыки для видео без озвучки")
            return None, 0, None
        
        # Validate text content
        full_text = text_content.get('full_text', '')
        if not full_text or not full_text.strip():
            log_callback("❌ Текст для озвучки пуст. Невозможно создать аудио.")
            return None, 0, None
        
        log_callback(f"📝 Текст для озвучки: {len(full_text)} символов")
        
        try:
            # Get language from kwargs (нормализуем для TTS)
            language = normalize_language(kwargs.get('language', 'Russian'))
            log_callback(f"🌍 ЯЗЫК ДЛЯ ОЗВУЧКИ: '{language}'")
            
            # Определяем формат видео для экспрессии
            video_settings = kwargs.get('video_settings', {})
            is_vertical = video_settings.get('height', 1080) > video_settings.get('width', 1920)
            source_data = kwargs.get('source_data', {}) or {}
            persona_id = source_data.get('persona_id')
            if persona_id:
                log_callback(f"🎭 Стиль озвучки по персоне: {persona_id}")
            
            voiceover_path, voiceover_duration = self.audio_processor.text_to_speech(
                text=full_text,
                output_dir=output_dir,
                log_callback=log_callback,
                provider=audio_settings.get('provider', 'edge'),
                voice_name=audio_settings.get('voice'),
                api_key=self.api_key,
                speech_speed=audio_settings.get('speech_speed', 1.0),
                language=language,
                is_vertical=is_vertical,
                persona_id=persona_id,
                edge_pitch_hz=audio_settings.get('edge_pitch_hz', 0),
                edge_volume_percent=audio_settings.get('edge_volume_percent', 0),
            )
            
            if not voiceover_path or voiceover_duration <= 0:
                log_callback("❌ Не удалось создать озвучку. Генерация остановлена.")
                return None, 0, None

            target_duration = float(video_settings.get('duration', 0) or 0)
            provider = str(audio_settings.get('provider', 'edge') or 'edge').lower()
            requested_speed = float(audio_settings.get('speech_speed', 1.0) or 1.0)
            effective_speed = requested_speed
            auto_fit_duration = bool(audio_settings.get('auto_fit_duration', True))

            # Preserve the complete script whenever a small, natural Edge rate
            # adjustment can fit it into the requested duration.
            if (
                provider == 'edge'
                and auto_fit_duration
                and target_duration > 0
                and voiceover_duration > target_duration * 1.08
            ):
                desired_speed = requested_speed * voiceover_duration / (target_duration * 0.98)
                max_auto_speed = min(1.60, max(1.35, requested_speed + 0.15))
                fitted_speed = min(max_auto_speed, max(requested_speed, desired_speed))
                if fitted_speed >= requested_speed + 0.02:
                    log_callback(
                        f"⏱️ Edge TTS длиннее цели: {voiceover_duration:.1f}s вместо "
                        f"{target_duration:.1f}s. Сохраняем весь текст и меняем скорость "
                        f"{requested_speed:.2f}x → {fitted_speed:.2f}x"
                    )
                    fitted_path, fitted_duration = self.audio_processor.text_to_speech(
                        text=full_text,
                        output_dir=output_dir,
                        log_callback=log_callback,
                        provider=provider,
                        voice_name=audio_settings.get('voice'),
                        api_key=self.api_key,
                        speech_speed=fitted_speed,
                        language=language,
                        is_vertical=is_vertical,
                        persona_id=persona_id,
                        edge_pitch_hz=audio_settings.get('edge_pitch_hz', 0),
                        edge_volume_percent=audio_settings.get('edge_volume_percent', 0),
                    )
                    if fitted_path and fitted_duration > 0:
                        old_voiceover = Path(voiceover_path)
                        if str(fitted_path) != str(voiceover_path) and old_voiceover.exists():
                            try:
                                old_voiceover.unlink()
                            except OSError:
                                pass
                        voiceover_path = fitted_path
                        voiceover_duration = fitted_duration
                        effective_speed = fitted_speed
                        log_callback(f"✅ Полный текст подогнан: {voiceover_duration:.2f}s")

            if (
                provider == 'edge'
                and target_duration > 0
                and voiceover_duration > target_duration * 1.15
            ):
                narration_units, _unit_separator = split_subtitle_units(full_text)
                current_words = len(narration_units)
                target_words = max(
                    10,
                    int(current_words * target_duration / voiceover_duration * 0.98),
                )
                adjusted_text = self._trim_text_to_word_budget(
                    full_text,
                    target_words,
                    language,
                )

                if adjusted_text and adjusted_text != full_text:
                    log_callback(
                        f"✂️ Edge TTS длиннее цели: {voiceover_duration:.1f}s вместо "
                        f"{target_duration:.1f}s. Сокращаем текст: "
                        f"{current_words} → "
                        f"{len(split_subtitle_units(adjusted_text)[0])} слов"
                    )
                    adjusted_path, adjusted_duration = self.audio_processor.text_to_speech(
                        text=adjusted_text,
                        output_dir=output_dir,
                        log_callback=log_callback,
                        provider=provider,
                        voice_name=audio_settings.get('voice'),
                        api_key=self.api_key,
                        speech_speed=effective_speed,
                        language=language,
                        is_vertical=is_vertical,
                        persona_id=persona_id,
                        edge_pitch_hz=audio_settings.get('edge_pitch_hz', 0),
                        edge_volume_percent=audio_settings.get('edge_volume_percent', 0),
                    )
                    if adjusted_path and adjusted_duration > 0:
                        old_voiceover = Path(voiceover_path)
                        if str(adjusted_path) != str(voiceover_path) and old_voiceover.exists():
                            try:
                                old_voiceover.unlink()
                            except OSError:
                                pass
                        voiceover_path = adjusted_path
                        voiceover_duration = adjusted_duration
                        full_text = adjusted_text
                        text_content['full_text'] = adjusted_text
                        from core.text_generator import split_text_by_sentences
                        text_content['text_parts'] = split_text_by_sentences(adjusted_text, language)
                        log_callback(f"✅ Длительность Edge TTS скорректирована: {voiceover_duration:.2f}s")
            
            log_callback(f"✅ Озвучка создана: {voiceover_duration:.2f}s")
            
            # Add background music (optional)
            try:
                final_audio_path = self.audio_processor.add_background_music(
                    voiceover_path,
                    music_path,
                    voiceover_duration,
                    output_dir,
                    log_callback,
                    music_volume=audio_settings.get(
                        'music_volume',
                        video_settings.get('music_volume', 0.25),
                    )
                )
                # Return: final_audio_path, duration, voiceover_path (for cleanup)
                return final_audio_path, voiceover_duration, voiceover_path
            except Exception as e:
                log_callback(f"⚠️ Ошибка добавления музыки: {e}. Используем только озвучку.")
                # Return: voiceover_path, duration, None (no separate voiceover to cleanup)
                return voiceover_path, voiceover_duration, None
                
        except Exception as e:
            log_callback(f"❌ Критическая ошибка обработки аудио: {e}")
            log_callback(f"📋 Traceback: {traceback.format_exc()}")
            return None, 0, None

    def _generate_all_images_once(self, theme: str, output_dir: Path, total_duration: float, kwargs):
        """Generate ALL images ONCE for the entire video (not per chunk!)"""
        log_callback = kwargs['log_callback']
        use_ai = kwargs['use_ai_image_generation']
        
        log_callback(f"\n🖼️ Генерация изображений для всего видео ({total_duration}s)...")
        
        # Calculate total images needed for entire video
        avg_shot = (kwargs['video_settings'].get('shot_min_duration', 3) + kwargs['video_settings'].get('shot_max_duration', 6)) / 2
        total_images_needed = max(1, math.ceil(total_duration / avg_shot))
        
        # For now, generate a simple text description for images
        simple_text_parts = [theme] * 3  # Use theme as base
        
        image_paths = self.image_processor.get_images_for_video(
            text_generator=self.text_generator,
            text_parts=simple_text_parts,
            title=theme,
            num_images=total_images_needed,
            output_dir=output_dir,
            use_ai_generation=use_ai,
            google_ai_api_key=self.google_ai_api_key,
            video_settings=kwargs['video_settings'],
            unlimited_images=kwargs.get('unlimited_images', False),
            num_unique_images=kwargs.get('num_unique_images', 5),
            log_callback=log_callback,
            original_theme=theme,  # ✅ Передаём оригинальную тему
            strict_theme_following=kwargs.get('strict_theme_following', True),
            image_model=kwargs.get('image_model', 'gemini-3.1-flash-image'),
            use_triple_template=kwargs.get('use_triple_template', False),  # 🎬 Triple template
            custom_images_folder=kwargs.get('custom_images_folder', None),  # 📁 Пользовательские изображения
            use_only_custom_images=kwargs.get('use_only_custom_images', False),  # ✅ ТОЛЬКО свои картинки
            use_image_cache=kwargs.get('use_image_cache', False),  # 💾 Использовать кэш
            save_to_image_cache=kwargs.get('save_to_image_cache', False),  # 💾 Сохранять в кэш
            image_callback=kwargs.get('image_callback', None)  # 🖼️ Callback для превью
        )
        
        if image_paths:
            log_callback(f"✅ Сгенерировано {len(image_paths)} изображений для всего видео")
        else:
            log_callback("❌ Не удалось сгенерировать изображения")
        
        return image_paths
    
    def _get_image_paths(self, text_content, output_dir, duration, kwargs, original_theme=None):
        """DEPRECATED - use _generate_all_images_once() instead"""
        log_callback = kwargs['log_callback']
        use_ai = kwargs['use_ai_image_generation']

        log_callback(f"🖼️ Этап 3 (Видео #{text_content.get('title', 'N/A')}): Обработка изображений...")
        first_shot_image = Path(str(text_content.get('first_shot_image_path') or ''))
        if str(text_content.get('first_shot_image_path') or '').strip() and first_shot_image.is_file():
            log_callback(
                f"🖼️ Пользовательский первый кадр: {first_shot_image.name}"
            )
            return [str(first_shot_image.resolve())]
        num_images = max(1, int(duration / ((kwargs['video_settings'].get('shot_min_duration', 3) + kwargs['video_settings'].get('shot_max_duration', 6)) / 2)))

        # 🖼️ ПРОВЕРКА ПУЛА ИЗОБРАЖЕНИЙ
        image_pool_settings = kwargs.get('image_pool_settings', {})
        if image_pool_settings.get('use_image_pool') and image_pool_settings.get('pool_path'):
            pool_path = Path(image_pool_settings['pool_path'])
            if pool_path.exists():
                log_callback(f"🖼️ Использование пула изображений: {pool_path.name}")
                
                from core.image_processor import ImageProcessor
                pool_images = ImageProcessor.get_images_from_pool(
                    pool_path=pool_path,
                    num_images=num_images,
                    exclude=kwargs.get('_used_pool_images', [])  # Исключаем уже использованные
                )
                
                if pool_images:
                    log_callback(f"   ✅ Получено {len(pool_images)} изображений из пула")
                    return pool_images
                else:
                    log_callback("   ⚠️ Пул пуст, переключаемся на AI генерацию")

        # Get original unique count before expansion (if available)
        original_unique_count = text_content.get('original_unique_count', None)
        
        image_paths = self.image_processor.get_images_for_video(
            text_generator=self.text_generator,
            text_parts=text_content.get('text_parts_for_images', text_content.get('text_parts', [])),  # ✅ Используем расширенные для изображений
            title=text_content.get('title', ''),
            num_images=num_images,
            output_dir=output_dir,
            use_ai_generation=use_ai,
            google_ai_api_key=self.google_ai_api_key,
            video_settings=kwargs['video_settings'],
            unlimited_images=kwargs.get('unlimited_images', False),
            num_unique_images=kwargs.get('num_unique_images', 5),
            log_callback=log_callback,
            original_unique_count=original_unique_count,
            original_theme=original_theme,  # ✅ Передаём оригинальную тему пользователя
            strict_theme_following=kwargs.get('strict_theme_following', True),
            enable_scene_variety=kwargs.get('enable_scene_variety', True),  # ✅ Система разнообразия сцен
            image_model=kwargs.get('image_model', 'gemini-3.1-flash-image'),
            use_triple_template=kwargs.get('use_triple_template', False),  # 🎬 Triple template
            custom_images_folder=kwargs.get('custom_images_folder', None),  # 📁 Пользовательские изображения
            use_only_custom_images=kwargs.get('use_only_custom_images', False),  # ✅ ТОЛЬКО свои картинки
            use_image_cache=kwargs.get('use_image_cache', False),  # 💾 Использовать кэш
            save_to_image_cache=kwargs.get('save_to_image_cache', False),  # 💾 Сохранять в кэш
            image_callback=kwargs.get('image_callback', None)  # 🖼️ Callback для превью
        )
        
        if not image_paths:
            log_callback("❌ Не удалось получить изображения. Генерация видео невозможна.")
            return []

        # 🖼️ ПЕРВЫЙ ШОТ ИЗ ПУЛА
        # Первый шот всегда будет картинкой из пула (без указания конкретного файла)
        first_shot_from_pool = kwargs.get('video_settings', {}).get('first_shot_from_pool', False)
        if first_shot_from_pool and image_paths:
            log_callback(f"🖼️ Первый шот: картинка из пула ({Path(str(image_paths[0])).name})")

        log_callback(f"✅ Этап 3 (Видео #{text_content.get('title', 'N/A')}): Изображения готовы.")
        return image_paths

    def generate_single_video(self, theme: str, output_dir: str, filename: str = None,
                             video_settings: dict = None, language: str = 'Russian',
                             is_vertical: bool = True, **kwargs) -> bool:
        """
        Публичный метод для генерации одного видео (для серийного контента)
        
        Args:
            theme: Тема видео
            output_dir: Папка для сохранения
            filename: Имя файла (без расширения)
            video_settings: Настройки видео
            language: Язык
            is_vertical: Вертикальное видео
            **kwargs: Дополнительные параметры
        
        Returns:
            True если успешно, False если ошибка
        """
        # Нормализуем язык (убираем эмодзи флага если есть)
        language = normalize_language(language)
        
        try:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            
            # Дефолтные настройки если не переданы
            if video_settings is None:
                video_settings = {
                    'width': 1080,
                    'height': 1920,
                    'fps': 60,
                    'duration': 30,
                    'shot_min_duration': 4,
                    'shot_max_duration': 4,
                    'enable_animation': True,
                    'animation_type': 'mix',
                    'animation_speed': 50
                }
            
            # Подготовка kwargs
            full_kwargs = {
                'music_path': kwargs.get('music_path', None),  # Музыка опциональна
                'video_settings': video_settings,
                'subtitle_settings': kwargs.get('subtitle_settings', {
                    'enabled': True,
                    'font_size': 51,
                    'font_color': 'white',
                    'bg_color': 'black',
                    'bg_opacity': 0.3,
                    'position': 'bottom',
                    'animated_subtitle': True
                }),
                'media_path': kwargs.get('media_path'),
                'use_ai_image_generation': kwargs.get('use_ai_image_generation', True),
                'overlay_settings': kwargs.get('overlay_settings', {}),
                'use_triple_template': kwargs.get('use_triple_template', False),
                'log_callback': kwargs.get('log_callback', print),
                'audio_settings': kwargs.get('audio_settings', {
                    'provider': 'gemini',
                    'voice': 'Kore',
                    'speech_speed': 1.0
                }),
                'main_theme': theme,
                'google_ai_api_key': kwargs.get('google_ai_api_key') or getattr(self, 'google_ai_api_key', None) or getattr(self, 'api_key', None),
                'api_key': kwargs.get('api_key') or getattr(self, 'api_key', None),
                'unlimited_images': kwargs.get('unlimited_images', False),
                'num_unique_images': kwargs.get('num_unique_images', 5),
                'language': language,
                'image_model': kwargs.get('image_model', 'gemini-2.5-flash-image'),  # 🎨 Модель изображений
                'veo3_settings': kwargs.get('veo3_settings', {'enabled': False}),  # 🎥 Настройки Veo 3
                'youtube_mixer_settings': kwargs.get('youtube_mixer_settings', {'enabled': False})  # 🎬 YouTube Mixer
            }
            
            # Устанавливаем API ключи если не установлены
            if not hasattr(self, 'api_key') or not self.api_key:
                self.api_key = full_kwargs['api_key']
            if not hasattr(self, 'google_ai_api_key') or not self.google_ai_api_key:
                self.google_ai_api_key = full_kwargs['google_ai_api_key']
            
            # Генерация видео
            self._generate_single_video(
                theme=theme,
                video_num=1,
                output_dir=output_path,
                **full_kwargs
            )
            
            return True
        
        except Exception as e:
            log_callback = kwargs.get('log_callback', print)
            log_callback(f"❌ Ошибка генерации видео: {e}")
            import traceback
            traceback.print_exc()
            return False
