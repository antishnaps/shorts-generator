#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Download Functions
Handles video file validation and repair.
"""

import os
import subprocess
from typing import Optional, Callable

from .video_utils import validate_video_file


def repair_video_file(video_path: str, log_callback: Optional[Callable] = None) -> Optional[str]:
    """Attempt to repair a corrupted video file.
    
    Стратегия:
    1. Быстрый ремукс (-c copy) — исправляет проблемы контейнера
    2. Если не помогло — перекодирование (ultrafast) — исправляет битые NAL units
    
    Args:
        video_path: Path to corrupted video
        log_callback: Optional logging function
        
    Returns:
        Path to repaired video or None if repair failed
    """
    if log_callback is None:
        def log_callback(message):
            print(message)
    
    repaired_path = video_path.replace('.mp4', '_repaired.mp4')
    
    # Strategy 1: Fast remux (-c copy)
    try:
        cmd = [
            'ffmpeg', '-y',
            '-err_detect', 'ignore_err',
            '-fflags', '+genpts',
            '-i', video_path,
            '-c', 'copy',
            '-movflags', '+faststart',
            repaired_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30, encoding='utf-8', errors='ignore')
        
        if result.returncode == 0 and os.path.exists(repaired_path):
            if validate_video_file(repaired_path, deep_check=True):
                os.replace(repaired_path, video_path)
                log_callback("✅ Видео успешно восстановлено (remux)")
                return video_path
            else:
                os.remove(repaired_path)
                log_callback("⚠️ Remux не помог, пробуем перекодирование")
    except Exception as e:
        log_callback(f"❌ Ошибка remux: {str(e)[:100]}")
        if os.path.exists(repaired_path):
            os.remove(repaired_path)
    
    # Strategy 2: Re-encode (ultrafast)
    try:
        cmd = [
            'ffmpeg', '-y',
            '-err_detect', 'ignore_err',
            '-fflags', '+genpts+discardcorrupt',
            '-i', video_path,
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', '28',
            '-r', '60',
            '-c:a', 'aac',
            '-vsync', 'cfr',
            '-movflags', '+faststart',
            repaired_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=90, encoding='utf-8', errors='ignore')
        
        if result.returncode == 0 and os.path.exists(repaired_path):
            if validate_video_file(repaired_path, deep_check=True):
                os.replace(repaired_path, video_path)
                log_callback("✅ Видео успешно восстановлено (re-encode)")
                return video_path
            else:
                os.remove(repaired_path)
                log_callback("⚠️ Восстановление не помогло")
    except subprocess.TimeoutExpired:
        log_callback("⏱️ Таймаут восстановления видео")
        if os.path.exists(repaired_path):
            os.remove(repaired_path)
    except Exception as e:
        log_callback(f"❌ Ошибка re-encode: {str(e)[:100]}")
        if os.path.exists(repaired_path):
            os.remove(repaired_path)
    
    return None
