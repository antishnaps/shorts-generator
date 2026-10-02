#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Video Utilities
Функции для валидации и восстановления видео файлов.
"""

import os
import json
import subprocess
from typing import Optional, Callable


def _dummy_log(msg):
    """Заглушка для логирования."""
    print(msg)


def validate_video_file(video_path: str, deep_check: bool = False) -> bool:
    """Validate that a video file is not corrupted.
    
    Args:
        video_path: Path to video file
        deep_check: If True, decode first few seconds to catch NAL unit errors
        
    Returns:
        True if video is valid, False if corrupted
    """
    if not os.path.exists(video_path):
        return False
    
    # Check file size (minimum 10KB for a valid video)
    file_size = os.path.getsize(video_path)
    if file_size < 10240:
        print(f"[VALIDATION] File too small ({file_size} bytes): {video_path}")
        return False
    
    # Use ffprobe to check video integrity
    try:
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name,width,height,duration',
            '-of', 'json',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            print(f"[VALIDATION] ffprobe failed for: {video_path}")
            return False
        
        # Check stderr for NAL unit errors (even with returncode 0)
        stderr = result.stderr.lower() if result.stderr else ''
        if 'invalid nal' in stderr or 'error splitting' in stderr:
            print(f"[VALIDATION] NAL unit errors detected: {video_path}")
            return False
        
        # Parse JSON output
        data = json.loads(result.stdout)
        streams = data.get('streams', [])
        
        if not streams:
            print(f"[VALIDATION] No video streams found: {video_path}")
            return False
        
        # Check for valid codec
        codec = streams[0].get('codec_name', '')
        if not codec:
            print(f"[VALIDATION] No codec detected: {video_path}")
            return False
        
        if deep_check:
            decode_cmd = [
                'ffmpeg', '-y',
                '-v', 'error',
                '-i', video_path,
                '-t', '2',  # First 2 seconds
                '-f', 'null',
                '-'
            ]
            decode_result = subprocess.run(decode_cmd, capture_output=True, text=True, timeout=30)
            decode_stderr = decode_result.stderr.lower() if decode_result.stderr else ''
            
            if 'invalid nal' in decode_stderr or 'error splitting' in decode_stderr:
                print(f"[VALIDATION] Deep check failed (NAL errors): {video_path}")
                return False
        
        return True
        
    except subprocess.TimeoutExpired:
        print(f"[VALIDATION] Timeout checking: {video_path}")
        return False
    except json.JSONDecodeError:
        print(f"[VALIDATION] Invalid ffprobe output: {video_path}")
        return False
    except Exception as e:
        print(f"[VALIDATION] Error checking {video_path}: {e}")
        return False


def repair_video_file(video_path: str, log_callback: Callable = None) -> Optional[str]:
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
        log_callback = _dummy_log
    
    repaired_path = video_path.replace('.mp4', '_repaired.mp4')
    
    # Strategy 1: Fast remux (fixes container issues)
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
    except Exception as e:
        log_callback(f"❌ Ошибка remux: {str(e)[:100]}")
        if os.path.exists(repaired_path):
            os.remove(repaired_path)
    
    # Strategy 2: Re-encode (fixes NAL unit errors)
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
