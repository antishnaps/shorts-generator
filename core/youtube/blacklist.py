#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Blacklist Management
Handles blocking of channels, videos, and keywords.
"""

import json
import threading
from typing import Dict, Optional

from .constants import BLACKLIST_FILE


# Blacklist format:
# {
#   "channels": ["channel_id_1", "channel_name_2"],
#   "videos": ["video_id_1", "video_id_2"],
#   "keywords": ["slideshow", "compilation"]  # Keywords to block
# }

_blacklist_cache: Optional[Dict] = None
_blacklist_lock = threading.Lock()


def load_blacklist() -> Dict:
    """Загружает blacklist из JSON файла."""
    global _blacklist_cache
    
    with _blacklist_lock:
        if _blacklist_cache is not None:
            return _blacklist_cache
        
        default_blacklist = {
            "channels": [],
            "videos": [],
            "keywords": ["slideshow", "slide show", "photo compilation"]
        }
        
        if not BLACKLIST_FILE.exists():
            # Создаем файл с дефолтными значениями
            try:
                BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f:
                    json.dump(default_blacklist, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            _blacklist_cache = default_blacklist
            return _blacklist_cache
        
        try:
            with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
                _blacklist_cache = json.load(f)
        except Exception:
            _blacklist_cache = default_blacklist
        
        return _blacklist_cache


def is_blacklisted(video_id: Optional[str] = None, channel: Optional[str] = None, title: Optional[str] = None) -> bool:
    """Проверяет, находится ли видео/канал/заголовок в blacklist."""
    blacklist = load_blacklist()
    
    # Проверка video_id
    if video_id:
        blocked_videos = blacklist.get('videos', []) or []
        if video_id in blocked_videos:
            return True
    
    # Проверка канала (по ID или имени)
    if channel:
        channel_lower = channel.lower()
        blocked_channels = blacklist.get('channels', []) or []
        for blocked in blocked_channels:
            if blocked and (blocked.lower() in channel_lower or channel_lower in blocked.lower()):
                return True
    
    # Проверка ключевых слов в заголовке
    if title:
        title_lower = title.lower()
        blocked_keywords = blacklist.get('keywords', []) or []
        for keyword in blocked_keywords:
            if keyword and keyword.lower() in title_lower:
                return True
    
    return False


def add_to_blacklist(video_id: Optional[str] = None, channel: Optional[str] = None, keyword: Optional[str] = None):
    """Добавляет элемент в blacklist."""
    global _blacklist_cache
    
    blacklist = load_blacklist()
    
    if video_id and video_id not in blacklist['videos']:
        blacklist['videos'].append(video_id)
    if channel and channel not in blacklist['channels']:
        blacklist['channels'].append(channel)
    if keyword and keyword not in blacklist['keywords']:
        blacklist['keywords'].append(keyword)
    
    try:
        with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(blacklist, f, indent=2, ensure_ascii=False)
        _blacklist_cache = blacklist
    except Exception:
        pass


def save_blacklist(blacklist: Dict):
    """Сохраняет blacklist в файл."""
    global _blacklist_cache
    
    try:
        with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(blacklist, f, indent=2, ensure_ascii=False)
        _blacklist_cache = blacklist
    except Exception:
        pass
