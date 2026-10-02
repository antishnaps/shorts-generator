#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Config Manager - Автоматическое управление конфигурацией

Автоматически создает config.json с дефолтными настройками если его нет.
Использует относительные пути для портативности.
"""

import json
import os
import copy
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from core.settings_schema import (
    normalize_video_settings,
    normalize_subtitle_settings,
    normalize_audio_settings,
    normalize_overlay_settings,
    normalize_youtube_mixer_settings,
)
from core.settings_recovery import (
    is_meaningful_generation_config,
    looks_like_pristine_defaults,
)


DEFAULT_CONFIG = {'user_settings': {'ui_language': 'Russian', 'gemini_api_keys': [], 'gemini_api_key': '', 'google_ai_api_key': '', 'heygen_api_key': '', 'pixabay_api_key': '', 'pexels_api_key': '', 'youtube_data_api_key': '', 'freesound_api_key': '', 'output_path': 'generated', 'music_path': 'music', 'media_path': 'overlays/Subscribed animation.mov', 'theme': 'интересная тема', 'language': '🇷🇺 Русский', 'persona_index': 7, 'strict_text_theme': True, 'seamless_loop': True, 'comment_bait': True, 'num_videos': 1, 'music_volume': 25, 'use_ai_image_generation': False, 'image_model': 0, 'strict_theme_following': True, 'scene_variety': True, 'num_unique_images': 3, 'unlimited_images': False, 'custom_images_folder': '', 'use_only_custom_images': False, 'use_reference_images': False, 'reference_images_folder': 'assets/references', 'use_image_cache': False, 'save_to_image_cache': False, 'use_triple_template': False, 'enable_parallel_generation': True, 'num_workers': 4, 'video_settings': {'width': 1080, 'height': 1920, 'resolution': '1080x1920 (FHD Вертикальное)', 'fps': 60, 'duration': 20, 'shot_min_duration': 1.5, 'shot_max_duration': 4, 'enable_animation': True, 'animation_type': 'mix', 'animation_speed': 50, 'enable_transitions': True, 'transition_duration': 0.3, 'transition_style': 'cinematic', 'transition_frequency': 0.6, 'cinematic_polish': True, 'video_effect': 'auto', 'video_effect_intensity': 0.3, 'video_effect_probability': 0.4, 'music_volume': 0.25}, 'subtitle_settings': {'enabled': True, 'font_size': 68, 'font_path': 'Arial Black', 'position': 'center', 'animated_subtitle': True, 'subtitle_animation': 'auto', 'subtitle_fade_in_ms': 180, 'subtitle_fade_out_ms': 220, 'subtitle_typewriter_cps': 18, 'style_preset': 'tiktok', 'highlight_color': '#00E5FF', 'outline_width': 7, 'shadow_depth': 5, 'max_words_per_subtitle': 2, 'max_chars_per_subtitle': 28, 'uppercase': True}, 'audio_settings': {'enabled': True, 'provider': 'edge', 'voice': 'ru-RU-SvetlanaNeural', 'speech_speed': 1.0, 'edge_pitch_hz': 0, 'edge_volume_percent': 0, 'auto_fit_duration': True, 'music_volume': 0.25, 'use_music': False}, 'overlay_settings': {'enabled': False, 'position': 'Внизу справа', 'fullscreen': True}, 'veo3_settings': {'enabled': False}, 'youtube_mixer_settings': {'enabled': False, 'video_ratio': 0.6, 'clip_min_duration': 6, 'clip_max_duration': 8, 'min_resolution': 720, 'allow_clip_reuse_when_insufficient': True, 'semantic_fallback_retries': 4, 'semantic_fallback_max_seconds': 360, 'semantic_fallback_queries_per_attempt': 4, 'source_rejection_buffer': 1.85, 'source_budget_max_minutes': 75, 'download_timeout_seconds': 0, 'download_timeout_min_seconds': 180, 'download_timeout_max_seconds': 900, 'custom_videos_folder': None}, 'channel_analytics_settings': {'enabled': False, 'use_for_generation': False, 'auto_sync': False, 'sync_days': 90, 'max_videos': 100, 'token_path': 'youtube_cache/channel_analytics/analytics_token.json', 'accounts_path': 'youtube_cache/channel_analytics/accounts.json', 'snapshot_path': 'youtube_cache/channel_analytics/latest_snapshot.json'}, 'custom_texts_settings': {'enabled': False, 'folder': None}, 'image_pool_settings': {'use_image_pool': False, 'pool_path': None, 'pool_size': 50}, 'custom_description_text': {'enabled': False, 'position': 'end', 'text': '', 'ai_customize': False}, 'window_geometry': {'x': 100, 'y': 100, 'width': 1200, 'height': 800}, 'app_preferences': {'auto_save': True, 'remember_window': True, 'smooth_interface_motion': True}, 'pronunciation_fixes': {}}}

_CONFIG_SAVE_LOCK = threading.RLock()


class ConfigManager:
    """Менеджер конфигурации с автоматической инициализацией"""
    
    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self.config = None
        self._ensure_config_exists()
        self._ensure_folders_exist()
    
    def _ensure_config_exists(self):
        """Создает config.json с дефолтными настройками если его нет"""
        if not self.config_path.exists():
            print(f"📝 Создаю {self.config_path} с дефолтными настройками...")
            self._create_default_config()
            print(f"✅ {self.config_path} создан!")
            print("⚠️  ВАЖНО: Добавьте свои Gemini API ключи в config.json")
        
        # Загружаем конфиг
        self._load_config()
    
    def _create_default_config(self):
        """Создает дефолтный config.json"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
    
    def _load_config(self):
        """Загружает конфиг из файла"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            restored_profile = self._restore_generation_profile_if_needed()
            changed = self._migrate_config() or restored_profile
            if changed:
                self.save()
        except Exception as e:
            print(f"❌ Ошибка загрузки {self.config_path}: {e}")
            self._backup_broken_config()
            if self._restore_last_good_config():
                print("✅ Настройки восстановлены из резервной копии")
                return
            print("📝 Создаю новый конфиг...")
            self._create_default_config()
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            self._migrate_config()

    def _backup_broken_config(self):
        """Сохраняет битый config рядом, чтобы не потерять пользовательские ключи."""
        try:
            if self.config_path.exists():
                backup_path = self.config_path.with_suffix(self.config_path.suffix + '.broken')
                backup_path.write_text(self.config_path.read_text(encoding='utf-8', errors='ignore'), encoding='utf-8')
                print(f"💾 Битый конфиг сохранён как {backup_path}")
        except Exception as backup_error:
            print(f"⚠️ Не удалось сохранить backup битого конфига: {backup_error}")

    def _restore_last_good_config(self) -> bool:
        """Restore the last valid atomic-save backup instead of losing user secrets."""
        backup_path = self.config_path.with_suffix(self.config_path.suffix + '.backup')
        try:
            if not backup_path.exists():
                return False
            with open(backup_path, 'r', encoding='utf-8') as backup_file:
                restored = json.load(backup_file)
            if not isinstance(restored, dict):
                return False
            self.config = restored
            self._migrate_config()
            if self.config_path.exists():
                self.config_path.unlink()
            return self.save()
        except Exception as restore_error:
            print(f"Could not restore {backup_path}: {restore_error}")
            return False

    @property
    def generation_profile_path(self) -> Path:
        return self.config_path.with_name(f"{self.config_path.stem}.last_generation.json")

    def _restore_generation_profile_if_needed(self) -> bool:
        """Recover a known working profile after an accidental GUI default save."""
        if not looks_like_pristine_defaults(self.config):
            return False
        try:
            with open(self.generation_profile_path, 'r', encoding='utf-8') as profile_file:
                profile = json.load(profile_file)
            if not is_meaningful_generation_config(profile):
                return False
            publish_settings = profile.get('user_settings', {}).get('youtube_publish_settings')
            if isinstance(publish_settings, dict):
                # A generation profile is a recovery snapshot, not a publication
                # calendar. Never revive a stale future start date from it.
                publish_settings['publish_start'] = datetime.now().astimezone().isoformat(timespec='seconds')
            self.config = profile
            print(f"Settings restored from {self.generation_profile_path}")
            return True
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False

    def save_generation_profile(self) -> bool:
        """Persist the exact settings used to start the latest generation batch."""
        if not is_meaningful_generation_config(self.config):
            return False
        profile_path = self.generation_profile_path
        tmp_path = profile_path.with_suffix(profile_path.suffix + '.tmp')
        try:
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, 'w', encoding='utf-8') as profile_file:
                json.dump(self.config, profile_file, indent=2, ensure_ascii=False)
                profile_file.flush()
                os.fsync(profile_file.fileno())
            tmp_path.replace(profile_path)
            return True
        except OSError as error:
            print(f"Could not save generation settings profile {profile_path}: {error}")
            return False
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _deep_merge_defaults(self, current: Dict[str, Any], defaults: Dict[str, Any]) -> bool:
        """Рекурсивно добавляет отсутствующие ключи из defaults в current."""
        changed = False
        for key, default_value in defaults.items():
            if key not in current:
                current[key] = copy.deepcopy(default_value)
                changed = True
            elif isinstance(current.get(key), dict) and isinstance(default_value, dict):
                if self._deep_merge_defaults(current[key], default_value):
                    changed = True
        return changed

    def _migrate_config(self) -> bool:
        """Мигрирует старые config.json к актуальной схеме без потери пользовательских значений."""
        if not isinstance(self.config, dict):
            self.config = copy.deepcopy(DEFAULT_CONFIG)
            return True

        changed = self._deep_merge_defaults(self.config, DEFAULT_CONFIG)
        user_settings = self.config.setdefault('user_settings', {})

        normalized_video = normalize_video_settings(user_settings.get('video_settings', {}))
        if user_settings.get('video_settings') != normalized_video:
            user_settings['video_settings'] = normalized_video
            changed = True

        normalized_subtitles = normalize_subtitle_settings(user_settings.get('subtitle_settings', {}))
        if user_settings.get('subtitle_settings') != normalized_subtitles:
            user_settings['subtitle_settings'] = normalized_subtitles
            changed = True

        normalized_audio = normalize_audio_settings(user_settings.get('audio_settings', {}))
        if user_settings.get('audio_settings') != normalized_audio:
            user_settings['audio_settings'] = normalized_audio
            changed = True

        normalized_overlay = normalize_overlay_settings(user_settings.get('overlay_settings', {}))
        if user_settings.get('overlay_settings') != normalized_overlay:
            user_settings['overlay_settings'] = normalized_overlay
            changed = True

        normalized_youtube = normalize_youtube_mixer_settings(user_settings.get('youtube_mixer_settings', {}))
        if user_settings.get('youtube_mixer_settings') != normalized_youtube:
            user_settings['youtube_mixer_settings'] = normalized_youtube
            changed = True

        keys = user_settings.get('gemini_api_keys', [])
        if isinstance(keys, str):
            keys = [k.strip() for k in keys.splitlines() if k.strip()]
            user_settings['gemini_api_keys'] = keys
            changed = True

        if isinstance(keys, list):
            deduped = []
            for key in keys:
                key = str(key).strip()
                if key and key not in deduped:
                    deduped.append(key)
            if not deduped:
                for fallback_name in ('gemini_api_key', 'google_ai_api_key'):
                    fallback_key = str(user_settings.get(fallback_name, '') or '').strip()
                    if fallback_key and fallback_key not in deduped:
                        deduped.append(fallback_key)
            if keys != deduped:
                user_settings['gemini_api_keys'] = deduped
                changed = True

            if deduped:
                primary_key = deduped[0]
                for fallback_name in ('gemini_api_key', 'google_ai_api_key'):
                    if not str(user_settings.get(fallback_name, '') or '').strip():
                        user_settings[fallback_name] = primary_key
                        changed = True

        return changed
    
    def _ensure_folders_exist(self):
        """Создает необходимые папки если их нет"""
        _media_path_raw = self.get_user_setting('media_path', 'overlays/Subscribed animation.mov')
        _media_path_default = 'overlays/Subscribed animation.mov'
        folders = [
            (self.get_user_setting('output_path', 'generated'), 'output_path', 'generated'),
            (self.get_user_setting('music_path', 'music'), 'music_path', 'music'),
            (Path(_media_path_raw).parent, 'media_path', _media_path_default),
            ('youtube_cache', None, None),
            ('logs', None, None),
            ('assets/references', None, None),
        ]

        config_changed = False
        for folder, config_key, default_value in folders:
            folder_path = Path(str(folder))
            if not folder_path.exists():
                try:
                    folder_path.mkdir(parents=True, exist_ok=True)
                    print(f"📁 Создана папка: {folder_path}")
                except (PermissionError, OSError) as e:
                    if config_key and default_value:
                        print(f"⚠️  Не удалось создать '{folder_path}': {e}")
                        print(f"   Сбрасываю '{config_key}' на значение по умолчанию: '{default_value}'")
                        self.set_user_setting(config_key, default_value)
                        config_changed = True
                        fallback = Path(default_value)
                        try:
                            fallback.mkdir(parents=True, exist_ok=True)
                            print(f"📁 Создана папка (запасной путь): {fallback}")
                        except Exception:
                            pass
                    else:
                        print(f"⚠️  Не удалось создать '{folder_path}': {e}")

        if config_changed:
            self.save()
            print("💾 Конфиг обновлён с новыми путями.")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Получить значение из конфига"""
        if not self.config:
            return default
        
        # Поддержка вложенных ключей через точку
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def get_user_setting(self, key: str, default: Any = None) -> Any:
        """Получить значение из user_settings"""
        return self.get(f'user_settings.{key}', default)
    
    def set(self, key: str, value: Any):
        """Установить значение в конфиге"""
        if not self.config:
            self.config = DEFAULT_CONFIG.copy()
        
        # Поддержка вложенных ключей через точку
        keys = key.split('.')
        current = self.config
        
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        
        current[keys[-1]] = value
    
    def set_user_setting(self, key: str, value: Any):
        """Установить значение в user_settings"""
        self.set(f'user_settings.{key}', value)
    
    def save(self):
        """Сохранить конфиг в файл"""
        with _CONFIG_SAVE_LOCK:
            last_error = None
            for attempt in range(5):
                tmp_path = None
                try:
                    self.config_path.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp_name = tempfile.mkstemp(
                        prefix=f".{self.config_path.name}.",
                        suffix=".tmp",
                        dir=str(self.config_path.parent or Path("."))
                    )
                    tmp_path = Path(tmp_name)
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        json.dump(self.config, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    if self.config_path.exists():
                        shutil.copy2(
                            self.config_path,
                            self.config_path.with_suffix(self.config_path.suffix + '.backup'),
                        )
                    tmp_path.replace(self.config_path)
                    return True
                except PermissionError as e:
                    last_error = e
                    time.sleep(0.1 * (attempt + 1))
                except OSError as e:
                    last_error = e
                    if getattr(e, "winerror", None) == 32:
                        time.sleep(0.1 * (attempt + 1))
                    else:
                        break
                except Exception as e:
                    last_error = e
                    break
                finally:
                    if tmp_path and tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except Exception:
                            pass
            print(f"❌ Ошибка сохранения {self.config_path}: {last_error}")
            return False
    
    def reload(self):
        """Перезагрузить конфиг из файла"""
        self._load_config()
    
    def get_all(self) -> Dict[str, Any]:
        """Получить весь конфиг"""
        return self.config or DEFAULT_CONFIG
    
    def get_api_keys(self) -> list:
        """
        Получить все доступные API ключи для ротации.
        
        Returns:
            Список уникальных API ключей из всех источников
        """
        keys = []
        
        # 1. Массив ключей (приоритет)
        gemini_api_keys = self.get_user_setting('gemini_api_keys', [])
        if gemini_api_keys and isinstance(gemini_api_keys, list):
            keys.extend([k.strip() for k in gemini_api_keys if k and k.strip()])
        
        # 2. Одиночные ключи (fallback)
        single_keys = [
            self.get_user_setting('google_ai_api_key', ''),
            self.get_user_setting('gemini_api_key', ''),
        ]
        
        for key in single_keys:
            if key and key.strip() and key.strip() not in keys:
                keys.append(key.strip())
        
        # Возвращаем уникальные ключи
        return keys if keys else []


# Глобальный экземпляр для удобства
_config_manager = None


def get_config_manager() -> ConfigManager:
    """Получить глобальный экземпляр ConfigManager"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def ensure_config_exists():
    """Убедиться что config.json существует (для обратной совместимости)"""
    get_config_manager()


if __name__ == '__main__':
    # Тест
    print("🧪 Тестирование ConfigManager...")
    
    # Создаем тестовый конфиг
    manager = ConfigManager('test_config.json')
    
    # Проверяем дефолтные значения
    print(f"output_path: {manager.get_user_setting('output_path')}")
    print(f"music_path: {manager.get_user_setting('music_path')}")
    print(f"media_path: {manager.get_user_setting('media_path')}")
    
    # Изменяем значение
    manager.set_user_setting('theme', 'тестовая тема')
    manager.save()
    
    # Перезагружаем
    manager.reload()
    print(f"theme: {manager.get_user_setting('theme')}")
    
    # Удаляем тестовый файл
    Path('test_config.json').unlink()
    
    print("✅ Тест пройден!")
