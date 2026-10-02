#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video processing utilities for Veo 3 integration
"""

import subprocess
import json
from pathlib import Path
from typing import Optional, Callable

from core.process_registry import probe_registered, run_registered

# 🌍 Unicode-safe temp директории
from core.utils import safe_temp_file


# Импортируем дефолтный логгер из централизованного модуля
from core.logging_utils import get_default_logger

# Алиас для обратной совместимости
_dummy_log = get_default_logger()


def remove_audio_from_video(
    input_video: str,
    output_video: str,
    log_callback: Callable[[str], None] = _dummy_log
) -> Optional[str]:
    """
    Remove audio track from video using FFmpeg
    
    Args:
        input_video: Path to input video
        output_video: Path to output video (without audio)
        log_callback: Logging callback
        
    Returns:
        Path to output video or None if failed
    """
    try:
        log_callback("   🔇 Удаление аудио из видео...")
        
        # Check if input has audio
        has_audio = check_video_has_audio(input_video)
        
        if not has_audio:
            log_callback("   ℹ️ Видео уже без звука, копируем...")
            import shutil
            shutil.copy(input_video, output_video)
            return output_video
        
        # FFmpeg command to remove audio
        cmd = [
            'ffmpeg',
            '-i', input_video,
            '-c:v', 'copy',  # Copy video stream without re-encoding
            '-an',           # Remove audio
            '-y',            # Overwrite output
            output_video
        ]
        
        result = run_registered(
            cmd,
            label="ffmpeg_remove_video_audio",
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=60
        )
        
        if result.returncode != 0:
            log_callback(f"   ❌ FFmpeg ошибка: {result.stderr[:200]}")
            return None
        
        log_callback("   ✅ Аудио удалено")
        return output_video
        
    except subprocess.TimeoutExpired:
        log_callback("   ❌ Timeout при удалении аудио")
        return None
    except Exception as e:
        log_callback(f"   ❌ Ошибка удаления аудио: {str(e)[:200]}")
        return None


def check_video_has_audio(video_path: str) -> bool:
    """
    Check if video has audio track
    
    Args:
        video_path: Path to video file
        
    Returns:
        True if video has audio, False otherwise
    """
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            video_path
        ]
        
        result = run_registered(
            cmd,
            label="ffprobe_video_audio_streams",
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=10
        )
        
        if result.returncode != 0:
            return False
        
        data = json.loads(result.stdout)
        streams = data.get('streams', [])
        
        # Check if any stream is audio
        has_audio = any(
            stream.get('codec_type') == 'audio'
            for stream in streams
        )
        
        return has_audio
        
    except Exception:
        return False


def get_video_duration(video_path: str) -> float:
    """
    Get video duration in seconds
    
    Args:
        video_path: Path to video file
        
    Returns:
        Duration in seconds or 0.0 if failed
    """
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            video_path
        ]
        
        result = run_registered(
            cmd,
            label="ffprobe_video_duration",
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=10
        )
        
        if result.returncode != 0:
            return 0.0
        
        data = json.loads(result.stdout)
        duration = float(data.get('format', {}).get('duration', 0.0))
        
        return duration
        
    except Exception:
        return 0.0


def concat_videos_with_crossfade(
    video1: str,
    video2: str,
    output_video: str,
    transition_duration: float = 0.3,
    target_resolution: tuple = None,
    log_callback: Callable[[str], None] = _dummy_log
) -> Optional[str]:
    """
    Concatenate two videos with crossfade transition
    
    Args:
        video1: Path to first video
        video2: Path to second video
        output_video: Path to output video
        transition_duration: Crossfade duration in seconds
        target_resolution: Target resolution (width, height) or None for video2 resolution
        log_callback: Logging callback
        
    Returns:
        Path to output video or None if failed
    """
    
    try:
        log_callback("   🎬 Сшивка видео с crossfade переходом...")
        
        # Check video2 duration - for long videos use simple concat (xfade is too slow)
        video2_duration = get_video_duration(video2)
        if video2_duration > 600:  # > 10 minutes
            log_callback(f"   ⚡ Длинное видео ({video2_duration:.0f}s) - используем быструю сшивку")
            return simple_concat_videos([video1, video2], output_video, log_callback)
        
        # Get video info
        probe1 = probe_registered(video1, label="ffprobe_crossfade_first")
        probe2 = probe_registered(video2, label="ffprobe_crossfade_second")
        
        video_stream1 = next((s for s in probe1['streams'] if s['codec_type'] == 'video'), None)
        video_stream2 = next((s for s in probe2['streams'] if s['codec_type'] == 'video'), None)
        
        if not video_stream1 or not video_stream2:
            log_callback("   ❌ Не удалось получить информацию о видео")
            return None
        
        width1 = int(video_stream1['width'])
        height1 = int(video_stream1['height'])
        width2 = int(video_stream2['width'])
        height2 = int(video_stream2['height'])
        
        # Get FPS
        fps1_str = video_stream1.get('r_frame_rate', '24/1')
        fps2_str = video_stream2.get('r_frame_rate', '60/1')
        
        # Parse FPS (format: "60/1" or "24000/1001")
        def parse_fps(fps_str):
            try:
                parts = fps_str.split('/')
                if len(parts) == 2:
                    numerator = float(parts[0])
                    denominator = float(parts[1])
                    if denominator == 0:
                        return 24.0  # Fallback
                    return numerator / denominator
                else:
                    return float(parts[0])
            except (ValueError, IndexError, ZeroDivisionError):
                return 24.0  # Fallback to 24fps
        
        fps1 = parse_fps(fps1_str)
        fps2 = parse_fps(fps2_str)
        
        duration1 = float(probe1['format']['duration'])
        duration2 = float(probe2['format']['duration'])
        
        log_callback(f"   📊 Видео 1: {width1}x{height1}, {duration1:.2f}s, {fps1:.2f}fps")
        log_callback(f"   📊 Видео 2: {width2}x{height2}, {duration2:.2f}s, {fps2:.2f}fps")
        
        # Determine target resolution and FPS
        if target_resolution:
            target_width, target_height = target_resolution
        else:
            # Use video2 resolution as target (main slideshow)
            target_width, target_height = width2, height2
        
        # Use video2 FPS as target (main slideshow)
        target_fps = fps2
        
        # Check if scaling or FPS conversion is needed for video1
        need_convert1 = (width1 != target_width or height1 != target_height or abs(fps1 - target_fps) > 0.1)
        
        # If video1 needs conversion, pre-convert it to a temp file first
        # This is more reliable than doing it in the xfade filter
        video1_converted = video1
        temp_video1 = None
        
        if need_convert1:
            log_callback(f"   🔧 Конвертация видео 1: {width1}x{height1}@{fps1:.0f}fps → {target_width}x{target_height}@{target_fps:.0f}fps")
            
            # Create temp file for converted video (🌍 Unicode-safe)
            temp_video1 = safe_temp_file(suffix='.mp4', prefix='convert_')
            
            # Convert video1 to match video2 parameters
            # 🔧 КРИТИЧНО: increase + crop вместо decrease + pad для заполнения экрана
            convert_cmd = [
                'ffmpeg', '-y',
                '-i', video1,
                '-vf', f'scale={target_width}:{target_height}:force_original_aspect_ratio=increase,crop={target_width}:{target_height},fps={target_fps},setsar=1',
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '18',
                '-pix_fmt', 'yuv420p',
                '-an',  # No audio from intro
                temp_video1
            ]
            
            convert_result = run_registered(
                convert_cmd,
                label="ffmpeg_crossfade_intro_convert",
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=120
            )
            
            if convert_result.returncode != 0:
                log_callback("   ⚠️ Не удалось конвертировать видео 1, пробуем простую сшивку")
                if temp_video1 and Path(temp_video1).exists():
                    Path(temp_video1).unlink()
                return simple_concat_videos([video1, video2], output_video, log_callback)
            
            video1_converted = temp_video1
            log_callback("   ✅ Видео 1 конвертировано")
            
            # Update duration after conversion
            try:
                probe1_new = probe_registered(
                    video1_converted, label="ffprobe_crossfade_converted_intro"
                )
                duration1 = float(probe1_new['format']['duration'])
            except Exception:
                pass
        
        # Calculate offset for crossfade
        offset = max(0, duration1 - transition_duration)
        
        # Simple xfade filter (both videos now have same parameters)
        filter_complex = f'[0:v][1:v]xfade=transition=fade:duration={transition_duration}:offset={offset}[outv]'
        
        # FFmpeg command with xfade filter
        cmd = [
            'ffmpeg', '-y',
            '-i', video1_converted,
            '-i', video2,
            '-filter_complex', filter_complex,
            '-map', '[outv]',
            '-map', '1:a?',  # Audio from video2 (slideshow with audio)
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-shortest',  # Match shortest stream
            output_video
        ]
        
        # Calculate timeout based on video duration (minimum 5 min, ~30s per minute of video)
        xfade_timeout = max(300, int(duration2 * 0.5) + 120)
        log_callback(f"   ⏱️ Xfade timeout: {xfade_timeout}s")
        
        result = run_registered(
            cmd,
            label="ffmpeg_video_crossfade",
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=xfade_timeout
        )
        
        # Cleanup temp file
        if temp_video1 and Path(temp_video1).exists():
            try:
                Path(temp_video1).unlink()
            except Exception:
                pass
        
        if result.returncode != 0:
            log_callback(f"   ❌ FFmpeg ошибка (код {result.returncode}):")
            log_callback(f"   📝 Команда: {' '.join(cmd[:10])}...")
            log_callback(f"   📝 Filter: {filter_complex[:200]}...")
            # Show last 500 chars of stderr (most relevant error info)
            stderr_lines = result.stderr.strip().split('\n')
            log_callback("   📝 Последние строки ошибки:")
            for line in stderr_lines[-10:]:
                log_callback(f"      {line}")
            
            # Fallback: try simple concat without crossfade
            log_callback("   🔄 Пробуем простую сшивку без перехода...")
            return simple_concat_videos([video1, video2], output_video, log_callback)
        
        final_duration = get_video_duration(output_video)
        log_callback(f"   ✅ Видео сшито: {target_width}x{target_height}, {final_duration:.2f}s")
        
        return output_video
        
    except subprocess.TimeoutExpired:
        log_callback("   ❌ Timeout при сшивке видео")
        # Cleanup temp file on error
        if temp_video1 and Path(temp_video1).exists():
            try:
                Path(temp_video1).unlink()
            except Exception:
                pass
        return None
    except Exception as e:
        log_callback(f"   ❌ Ошибка сшивки: {str(e)[:200]}")
        import traceback
        log_callback(f"   🔍 Traceback: {traceback.format_exc()[:300]}")
        # Cleanup temp file on error
        if temp_video1 and Path(temp_video1).exists():
            try:
                Path(temp_video1).unlink()
            except Exception:
                pass
        return None


def simple_concat_videos(
    videos: list,
    output_video: str,
    log_callback: Callable[[str], None] = _dummy_log
) -> Optional[str]:
    """
    Simple concatenation of videos without transition
    
    Args:
        videos: List of video paths
        output_video: Path to output video
        log_callback: Logging callback
        
    Returns:
        Path to output video or None if failed
    """
    try:
        log_callback(f"   🎬 Простая сшивка {len(videos)} видео...")
        
        if len(videos) != 2:
            log_callback(f"   ⚠️ Поддерживается только 2 видео, получено {len(videos)}")
            return None
        
        # Get video info to determine scaling
        probe1 = probe_registered(videos[0], label="ffprobe_concat_first")
        probe2 = probe_registered(videos[1], label="ffprobe_concat_second")
        
        v1 = next(s for s in probe1['streams'] if s['codec_type'] == 'video')
        v2 = next(s for s in probe2['streams'] if s['codec_type'] == 'video')
        
        w1, h1 = int(v1['width']), int(v1['height'])
        w2, h2 = int(v2['width']), int(v2['height'])
        
        # Use second video resolution as target
        target_w, target_h = w2, h2
        
        # Get FPS from videos
        fps1_str = v1.get('r_frame_rate', '24/1')
        fps2_str = v2.get('r_frame_rate', '60/1')
        
        def parse_fps(fps_str):
            try:
                parts = fps_str.split('/')
                if len(parts) == 2:
                    numerator = float(parts[0])
                    denominator = float(parts[1])
                    if denominator == 0:
                        return 60.0  # Fallback
                    return numerator / denominator
                else:
                    return float(parts[0])
            except (ValueError, IndexError, ZeroDivisionError):
                return 60.0  # Fallback to 60fps
        
        fps1 = parse_fps(fps1_str)
        fps2 = parse_fps(fps2_str)
        
        # Use second video FPS as target (main slideshow)
        target_fps = fps2
        
        log_callback(f"   📊 Видео 1: {w1}x{h1}, {fps1:.2f}fps")
        log_callback(f"   📊 Видео 2: {w2}x{h2}, {fps2:.2f}fps")
        log_callback(f"   🎯 Целевое разрешение: {target_w}x{target_h}, {target_fps:.2f}fps")
        
        # Build filter: scale and normalize FPS only if needed
        # Video 1 always needs FPS conversion (Veo 3 is 24fps)
        # 🔧 КРИТИЧНО: increase + crop вместо decrease + pad для заполнения экрана
        v0_filter = (
            f'[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,'
            f'crop={target_w}:{target_h},setsar=1,fps={target_fps}[v0];'
        )
        
        # Video 2: only process if needed (avoid precision loss)
        need_scale = (w2 != target_w or h2 != target_h)
        need_fps = abs(fps2 - target_fps) > 0.1
        
        # Check if video 1 has audio (it shouldn't - Veo 3 intro is silent)
        has_audio1 = check_video_has_audio(videos[0])
        has_audio2 = check_video_has_audio(videos[1])
        
        log_callback(f"   🎵 Аудио: видео1={has_audio1}, видео2={has_audio2}")
        
        # Get durations first (needed for trim)
        duration1 = float(probe1['format']['duration'])
        duration2 = float(probe2['format']['duration'])
        
        # Trim video 2 to remove first N seconds (will be replaced by video 1)
        v1_filters = [f'trim=start={duration1}', 'setpts=PTS-STARTPTS']
        
        if need_scale:
            log_callback(f"   🔧 Масштабирование видео 2: {w2}x{h2} → {target_w}x{target_h}")
            # 🔧 КРИТИЧНО: increase + crop вместо decrease + pad для заполнения экрана
            v1_filters.append(f'scale={target_w}:{target_h}:force_original_aspect_ratio=increase')
            v1_filters.append(f'crop={target_w}:{target_h}')
        v1_filters.append('setsar=1')
        if need_fps:
            log_callback(f"   🔧 Конвертация FPS видео 2: {fps2:.2f}fps → {target_fps:.2f}fps")
            v1_filters.append(f'fps={target_fps}')
        
        v1_filter = f'[1:v]{",".join(v1_filters)}[v1];'
        
        # Concat: video 1 (intro) + trimmed video 2 (rest of slideshow)
        filter_complex = v0_filter + v1_filter + '[v0][v1]concat=n=2:v=1:a=0[outv]'
        
        log_callback(f"   ✂️ Обрезаем первые {duration1:.2f}s из слайдшоу и заменяем на Veo 3 интро")
        
        # Audio processing: use full audio from video 2 (slideshow)
        # Video 1 (Veo 3 intro) replaces first N seconds of slideshow video
        # Audio stays unchanged - full voiceover + music from the beginning
        if has_audio2:
            audio_map = '1:a'
            log_callback(f"   🎵 Используем полное аудио из слайдшоу ({duration2:.2f}s)")
        else:
            audio_map = '1:a?'
        
        cmd = [
            'ffmpeg',
            '-i', videos[0],
            '-i', videos[1],
            '-filter_complex', filter_complex,
            '-map', '[outv]',
            '-map', audio_map,
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-pix_fmt', 'yuv420p',
            '-y',
            output_video
        ]
        
        result = run_registered(
            cmd,
            label="ffmpeg_veo_simple_concat",
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=300
        )
        
        if result.returncode != 0:
            log_callback(f"   ❌ FFmpeg ошибка: {result.stderr[:200]}")
            return None
        
        log_callback("   ✅ Видео сшито")
        return output_video
        
    except Exception as e:
        log_callback(f"   ❌ Ошибка сшивки: {str(e)[:200]}")
        return None
