#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FFmpeg utilities and path management
Extracted from video_renderer.py for better modularity
"""

import os
import shutil
import logging
import atexit
import threading
from pathlib import Path
from typing import List


# ============================================================
# 🔧 FFmpeg path setup - использует локальный FFmpeg из tools/ffmpeg/
# ============================================================
def get_ffmpeg_path() -> str:
    """Возвращает путь к ffmpeg - универсальный для всех ОС"""
    import platform
    
    # Определяем расширение исполняемого файла в зависимости от ОС
    exe_ext = ".exe" if platform.system().lower() == "windows" else ""
    
    # 1. Проверяем локальный FFmpeg в tools/ffmpeg/ (полная сборка)
    script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    local_ffmpeg = os.path.join(script_dir, 'tools', 'ffmpeg', f'ffmpeg{exe_ext}')
    if os.path.exists(local_ffmpeg):
        return local_ffmpeg
    
    # 2. Проверяем системный ffmpeg
    if shutil.which('ffmpeg'):
        return 'ffmpeg'
    
    # 3. Пробуем imageio-ffmpeg (урезанная сборка, fallback)
    try:
        import imageio_ffmpeg
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_path and os.path.exists(ffmpeg_path):
            return ffmpeg_path
    except ImportError:
        pass
    except Exception:
        pass
    
    # 4. Fallback - надеемся что ffmpeg в PATH
    return 'ffmpeg'


# Инициализируем путь к ffmpeg при импорте модуля
FFMPEG_PATH = get_ffmpeg_path()


# ============================================================
# 🗑️ Temp directory management
# ============================================================
_GLOBAL_TEMP_DIRS: List[Path] = []
_GLOBAL_TEMP_LOCK = threading.Lock()


def register_temp_dir(temp_dir: Path) -> None:
    """Регистрирует temp директорию для глобального cleanup."""
    with _GLOBAL_TEMP_LOCK:
        if temp_dir not in _GLOBAL_TEMP_DIRS:
            _GLOBAL_TEMP_DIRS.append(temp_dir)


def unregister_temp_dir(temp_dir: Path) -> None:
    """Удаляет temp директорию из глобального списка."""
    with _GLOBAL_TEMP_LOCK:
        if temp_dir in _GLOBAL_TEMP_DIRS:
            _GLOBAL_TEMP_DIRS.remove(temp_dir)


def cleanup_all_temp_dirs() -> None:
    """Очищает все зарегистрированные temp директории (вызывается при выходе)."""
    with _GLOBAL_TEMP_LOCK:
        dirs_to_clean = _GLOBAL_TEMP_DIRS.copy()
    
    for temp_dir in dirs_to_clean:
        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
                logging.debug(f"Cleanup temp dir: {temp_dir}")
        except Exception as e:
            logging.debug(f"Failed to cleanup {temp_dir}: {e}")


# Регистрируем cleanup при выходе
atexit.register(cleanup_all_temp_dirs)
