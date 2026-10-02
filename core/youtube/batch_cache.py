#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Batch Cache
Кэш для переиспользования видео в рамках одной сессии генерации.
"""

import threading
from pathlib import Path
from typing import Dict, Optional


# Глобальный кэш: video_id -> путь к файлу
_batch_video_cache: Dict[str, str] = {}
_batch_video_cache_lock = threading.Lock()


def get_batch_cached_video(video_id: str) -> Optional[str]:
    """Получить видео из батч-кэша сессии."""
    with _batch_video_cache_lock:
        path = _batch_video_cache.get(video_id)
        if path and Path(path).exists():
            return path
        return None


def add_to_batch_cache(video_id: str, path: str):
    """Добавить видео в батч-кэш."""
    with _batch_video_cache_lock:
        _batch_video_cache[video_id] = path


def clear_batch_cache():
    """Очистить батч-кэш (вызывается в конце генерации)."""
    with _batch_video_cache_lock:
        _batch_video_cache.clear()
