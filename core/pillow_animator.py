#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pillow Animator V13 - Python Engine (True Lanczos, Zero Jitter).

V13 Features:
- Replaced FFmpeg 'zoompan' with pure Python/Pillow rendering
- True Sub-pixel Lanczos Resampling (via Dynamic Cropping)
- Zero Jitter / Aliasing
- Cinematic Quartic Easing
- Optimized Pipe Streaming to FFmpeg
"""

import subprocess
import os
import logging
import math
import random
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
from core.process_registry import get_process_registry, run_registered

try:
    from core.utils import thread_slot
except ImportError:
    def thread_slot(category='default', priority=None, timeout=300):
        from contextlib import contextmanager
        @contextmanager
        def dummy():
            yield
        return dummy()

logger = logging.getLogger(__name__)

# Determine correct Resampling filter for Pillow version
try:
    RESAMPLING_FILTER = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLING_FILTER = Image.LANCZOS

def get_easing(t, ease_type='quartic_out'):
    """Calculate easing factor t (0..1) -> (0..1)"""
    t = max(0.0, min(1.0, t))
    if ease_type == 'linear':
        return t
    elif ease_type == 'sine':
        return (1 - math.cos(t * math.pi)) / 2
    elif ease_type == 'quartic_out':
        return 1 - pow(1 - t, 4)
    return t

def clamp_box(x, y, w, h, max_w, max_h):
    """Ensure crop box is within image bounds."""
    # 1. Clamp size
    w = min(w, max_w)
    h = min(h, max_h)
    
    # 2. Clamp Position
    if x < 0: x = 0
    if y < 0: y = 0
    if x + w > max_w: x = max_w - w
    if y + h > max_h: y = max_h - h
    
    return x, y, w, h

class AnimationType:
    """Clean animation types - Cinematic movements with advanced effects."""
    PAN_LEFT = 'pan_left'
    PAN_RIGHT = 'pan_right'
    PAN_UP = 'pan_up'
    PAN_DOWN = 'pan_down'
    ZOOM_IN = 'zoom_in'
    ZOOM_OUT = 'zoom_out'
    
    # Новые эффекты
    PARALLAX_UP = 'parallax_up'
    PARALLAX_DOWN = 'parallax_down'
    PARALLAX_LEFT = 'parallax_left'
    PARALLAX_RIGHT = 'parallax_right'
    DOLLY_ZOOM_IN = 'dolly_zoom_in'  # Эффект Вертиго (zoom in + pan out)
    DOLLY_ZOOM_OUT = 'dolly_zoom_out'  # Обратный эффект Вертиго
    ROTATE_ZOOM = 'rotate_zoom'  # Вращение с зумом (имитация)
    
    @classmethod
    def all(cls):
        return [
            cls.PAN_LEFT, cls.PAN_RIGHT, cls.PAN_UP, cls.PAN_DOWN,
            cls.ZOOM_IN, cls.ZOOM_OUT,
            cls.PARALLAX_UP, cls.PARALLAX_DOWN, cls.PARALLAX_LEFT, cls.PARALLAX_RIGHT,
            cls.DOLLY_ZOOM_IN, cls.DOLLY_ZOOM_OUT, cls.ROTATE_ZOOM
        ]


class PillowAnimator:
    _nvenc_available = None
    _animation_cache = {}
    _cache_max_size = 500  # Increased: 413-shot batches need more room
    _cache_lock = None
    
    # Python Engine Configuration
    PAN_ZOOM_FACTOR = 1.15 # 15% Zoom for pans (Slower = Smoother)
    DEFAULT_TIMEOUT = 300

    @classmethod
    def _get_cache_lock(cls):
        if cls._cache_lock is None:
            import threading
            cls._cache_lock = threading.Lock()
        return cls._cache_lock

    @classmethod
    def _get_cache_key(cls, image_path, width, height, duration, animation_type, zoom_amount):
        import hashlib
        # Version 13 indicates Python Engine
        key_str = f"{image_path}_{width}x{height}_{duration}_{animation_type}_{zoom_amount}_v13_python"
        return hashlib.md5(key_str.encode()).hexdigest()

    @classmethod
    def _get_from_cache(cls, cache_key):
        with cls._get_cache_lock():
            cached = cls._animation_cache.get(cache_key)
            if cached:
                path, _ = cached
                if Path(path).exists():
                    cls._animation_cache[cache_key] = (path, time.time())
                    return path
            if cache_key in cls._animation_cache:
                del cls._animation_cache[cache_key]
        return None

    @classmethod
    def _add_to_cache(cls, cache_key, output_path):
        with cls._get_cache_lock():
            if len(cls._animation_cache) >= cls._cache_max_size:
                sorted_items = sorted(cls._animation_cache.items(), key=lambda x: x[1][1])
                for key, _ in sorted_items[:cls._cache_max_size // 4]:
                    del cls._animation_cache[key]
            cls._animation_cache[cache_key] = (output_path, time.time())

    @classmethod
    def clear_cache(cls):
        with cls._get_cache_lock():
            cls._animation_cache.clear()

    @classmethod
    def check_nvenc(cls):
        if cls._nvenc_available is not None:
            return cls._nvenc_available
        try:
            result = run_registered(
                ['ffmpeg', '-hide_banner', '-encoders'],
                label="ffmpeg_pillow_nvenc_probe",
                capture_output=True,
                text=True,
                timeout=5,
            )
            cls._nvenc_available = 'h264_nvenc' in result.stdout
        except Exception:
            cls._nvenc_available = False
        return cls._nvenc_available

    @staticmethod
    def sync_duration_to_frames(duration, fps):
        frames = round(duration * fps)
        return frames / fps, frames

    @classmethod
    def _get_codec_args(cls, use_nvenc):
        if use_nvenc and cls.check_nvenc():
            return ['-c:v', 'h264_nvenc', '-preset', 'p1', '-cq', '23',
                '-profile:v', 'high', '-rc', 'vbr',
                '-b:v', '6M', '-maxrate', '10M', '-bufsize', '20M']
        return ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20',
            '-profile:v', 'high', '-level', '4.2',
            '-pix_fmt', 'yuv420p']

    @classmethod
    def _render_frame_stream(cls, img, output_path, width, height, fps, duration,
                            animation_type, zoom_amount, use_nvenc):
        """Internal method to stream generated frames to FFmpeg."""
        
        img_w, img_h = img.size
        target_ratio = width / height
        img_ratio = img_w / img_h
        
        # Calculate Base Crop (Max fit)
        if img_ratio > target_ratio:
            base_h = img_h
            base_w = int(base_h * target_ratio)
        else:
            base_w = img_w
            base_h = int(base_w / target_ratio)
            
        base_x = (img_w - base_w) // 2
        base_y = (img_h - base_h) // 2
        
        if base_w <= 0 or base_h <= 0:
            raise ValueError("Invalid image dimensions for crop")

        # Setup Animation Logic
        focus_x = 0.5 + (random.random() - 0.5) * 0.4 # +/- 0.2
        focus_y = 0.5 + (random.random() - 0.5) * 0.4
        
        # Scales
        scale_zoomed = 1.0 - zoom_amount
        scale_pan = 1.0 / cls.PAN_ZOOM_FACTOR # e.g. 1/1.3 = 0.77
        
        w_zoomed = base_w * scale_zoomed
        h_zoomed = base_h * scale_zoomed
        
        w_pan = base_w * scale_pan
        h_pan = base_h * scale_pan
        
        # Helper helpers
        def get_rect_from_center(cx, cy, w, h):
            return clamp_box(cx - w/2, cy - h/2, w, h, img_w, img_h)

        base_cx = base_x + base_w * 0.5
        base_cy = base_y + base_h * 0.5
        target_cx = base_x + base_w * focus_x
        target_cy = base_y + base_h * focus_y

        if animation_type == 'zoom_in':
            # Start: Base. End: Zoomed Targeted.
            s_rect = get_rect_from_center(base_cx, base_cy, base_w, base_h)
            e_rect = get_rect_from_center(target_cx, target_cy, w_zoomed, h_zoomed)
            ease_func = 'sine'
            
        elif animation_type == 'zoom_out':
            # Start: Zoomed Targeted. End: Base.
            s_rect = get_rect_from_center(target_cx, target_cy, w_zoomed, h_zoomed)
            e_rect = get_rect_from_center(base_cx, base_cy, base_w, base_h)
            ease_func = 'sine'
            
        elif 'pan' in animation_type:
            # Panning Logic (Fixed Crop Size)
            w, h = w_pan, h_pan
            
            # Bounds for panning within Base Crop
            min_x = base_x
            max_x = base_x + base_w - w
            min_y = base_y
            max_y = base_y + base_h - h
            
            c_x = base_x + (base_w - w) / 2
            c_y = base_y + (base_h - h) / 2
            
            if animation_type == 'pan_left':
                 s_rect = (max_x, c_y, w, h)
                 e_rect = (min_x, c_y, w, h)
            elif animation_type == 'pan_right':
                 s_rect = (min_x, c_y, w, h)
                 e_rect = (max_x, c_y, w, h)
            elif animation_type == 'pan_up':
                 s_rect = (c_x, max_y, w, h)
                 e_rect = (c_x, min_y, w, h)
            elif animation_type == 'pan_down':
                 s_rect = (c_x, min_y, w, h)
                 e_rect = (c_x, max_y, w, h)
            else:
                 s_rect = (c_x, c_y, w, h)
                 e_rect = s_rect

            # Re-clamp
            s_rect = clamp_box(*s_rect, img_w, img_h)
            e_rect = clamp_box(*e_rect, img_w, img_h)
            ease_func = 'sine'
            
        elif 'parallax' in animation_type:
            # Параллакс эффект: медленный zoom + быстрый pan
            # Создаёт эффект глубины и объёма
            parallax_zoom = 0.08  # Небольшой зум
            w_start = base_w * (1.0 - parallax_zoom)
            h_start = base_h * (1.0 - parallax_zoom)
            w_end = base_w
            h_end = base_h
            
            # Направление движения
            if animation_type == 'parallax_up':
                s_rect = get_rect_from_center(base_cx, base_cy + base_h * 0.15, w_start, h_start)
                e_rect = get_rect_from_center(base_cx, base_cy - base_h * 0.15, w_end, h_end)
            elif animation_type == 'parallax_down':
                s_rect = get_rect_from_center(base_cx, base_cy - base_h * 0.15, w_start, h_start)
                e_rect = get_rect_from_center(base_cx, base_cy + base_h * 0.15, w_end, h_end)
            elif animation_type == 'parallax_left':
                s_rect = get_rect_from_center(base_cx + base_w * 0.15, base_cy, w_start, h_start)
                e_rect = get_rect_from_center(base_cx - base_w * 0.15, base_cy, w_end, h_end)
            elif animation_type == 'parallax_right':
                s_rect = get_rect_from_center(base_cx - base_w * 0.15, base_cy, w_start, h_start)
                e_rect = get_rect_from_center(base_cx + base_w * 0.15, base_cy, w_end, h_end)
            else:
                s_rect = get_rect_from_center(base_cx, base_cy, w_start, h_start)
                e_rect = get_rect_from_center(base_cx, base_cy, w_end, h_end)
            
            ease_func = 'quartic_out'  # Плавное замедление
            
        elif 'dolly_zoom' in animation_type:
            # Dolly Zoom (эффект Вертиго): zoom + противоположный pan
            # Создаёт драматический эффект изменения перспективы
            dolly_zoom_amount = 0.12
            
            if animation_type == 'dolly_zoom_in':
                # Zoom in + pan out from center
                w_start = base_w
                h_start = base_h
                w_end = base_w * (1.0 - dolly_zoom_amount)
                h_end = base_h * (1.0 - dolly_zoom_amount)
                
                s_rect = get_rect_from_center(base_cx, base_cy, w_start, h_start)
                e_rect = get_rect_from_center(target_cx, target_cy, w_end, h_end)
            else:  # dolly_zoom_out
                # Zoom out + pan in to center
                w_start = base_w * (1.0 - dolly_zoom_amount)
                h_start = base_h * (1.0 - dolly_zoom_amount)
                w_end = base_w
                h_end = base_h
                
                s_rect = get_rect_from_center(target_cx, target_cy, w_start, h_start)
                e_rect = get_rect_from_center(base_cx, base_cy, w_end, h_end)
            
            ease_func = 'quartic_out'
            
        elif animation_type == 'rotate_zoom':
            # Имитация вращения через диагональный pan + zoom
            # Создаёт динамичный кинематографический эффект
            rotate_zoom_amount = 0.10
            w_start = base_w * (1.0 + rotate_zoom_amount)
            h_start = base_h * (1.0 + rotate_zoom_amount)
            w_end = base_w * (1.0 - rotate_zoom_amount)
            h_end = base_h * (1.0 - rotate_zoom_amount)
            
            # Диагональное движение (левый верхний -> правый нижний)
            offset = 0.12
            s_rect = get_rect_from_center(
                base_cx - base_w * offset, 
                base_cy - base_h * offset, 
                w_start, h_start
            )
            e_rect = get_rect_from_center(
                base_cx + base_w * offset, 
                base_cy + base_h * offset, 
                w_end, h_end
            )
            
            ease_func = 'sine'
            
        elif 'pan' in animation_type:
            # Panning Logic (Fixed Crop Size)
            w, h = w_pan, h_pan
            
            # Bounds for panning within Base Crop
            min_x = base_x
            max_x = base_x + base_w - w
            min_y = base_y
            max_y = base_y + base_h - h
            
            c_x = base_x + (base_w - w) / 2
            c_y = base_y + (base_h - h) / 2
            
            if animation_type == 'pan_left':
                 s_rect = (max_x, c_y, w, h)
                 e_rect = (min_x, c_y, w, h)
            elif animation_type == 'pan_right':
                 s_rect = (min_x, c_y, w, h)
                 e_rect = (max_x, c_y, w, h)
            elif animation_type == 'pan_up':
                 s_rect = (c_x, max_y, w, h)
                 e_rect = (c_x, min_y, w, h)
            elif animation_type == 'pan_down':
                 s_rect = (c_x, min_y, w, h)
                 e_rect = (c_x, max_y, w, h)
            else:
                 s_rect = (c_x, c_y, w, h)
                 e_rect = s_rect

            # Re-clamp
            s_rect = clamp_box(*s_rect, img_w, img_h)
            e_rect = clamp_box(*e_rect, img_w, img_h)
            ease_func = 'sine'
            
        else:
            s_rect = (base_x, base_y, base_w, base_h)
            e_rect = s_rect
            ease_func = 'linear'

        # Build FFmpeg Command
        # FIX: Добавлены -r (output fps), -g (GOP size), -vsync cfr для плавной анимации
        gop_size = fps * 2  # Keyframe каждые 2 секунды
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'rawvideo', '-vcodec', 'rawvideo',
            '-s', f'{width}x{height}', '-pix_fmt', 'rgb24', '-r', str(fps), '-i', '-',
            *cls._get_codec_args(use_nvenc),
            '-r', str(fps),  # Явный выходной FPS
            '-g', str(gop_size),  # GOP size для плавности
            '-vsync', 'cfr',  # Constant frame rate (без дублирования/пропуска кадров)
            '-movflags', '+faststart',
            output_path
        ]
        
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        registry = get_process_registry()
        registry.register(proc, "ffmpeg_pillow_animator")
        
        start_time = time.time()
        # FIX: Используем round() вместо int() для точного расчёта кадров
        total_frames = round(duration * fps)
        
        try:
            try:
                for i in range(total_frames):
                    if time.time() - start_time > cls.DEFAULT_TIMEOUT:
                        raise TimeoutError("Rendering timeout")

                    if total_frames > 1:
                        progress = i / (total_frames - 1)
                    else:
                        progress = 0.0
                        
                    t = get_easing(progress, ease_func)
                    
                    # Interpolate Rect
                    cur_x = s_rect[0] + (e_rect[0] - s_rect[0]) * t
                    cur_y = s_rect[1] + (e_rect[1] - s_rect[1]) * t
                    cur_w = s_rect[2] + (e_rect[2] - s_rect[2]) * t
                    cur_h = s_rect[3] + (e_rect[3] - s_rect[3]) * t
                    
                    if cur_w < 1: cur_w = 1
                    if cur_h < 1: cur_h = 1
                    
                    box = (cur_x, cur_y, cur_x + cur_w, cur_y + cur_h)
                    
                    # PIL Resize (Lanczos)
                    frame = img.resize((width, height), resample=RESAMPLING_FILTER, box=box)
                    
                    # Write to Pipe
                    proc.stdin.write(frame.tobytes())
                    
            except Exception as e:
                try:
                    proc.stdin.close()
                    proc.stdin = None
                except Exception:
                    pass
                if proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=0.5)
                    except Exception:
                        if proc.poll() is None:
                            proc.kill()
                _, stderr = proc.communicate()
                err = stderr.decode(errors='replace') if stderr else str(e)
                raise Exception(f"Render Loop Error: {e} | FFmpeg: {err}")
                
            proc.stdin.close()
            proc.wait()
        finally:
            registry.unregister(proc)
        
        if proc.returncode != 0:
            err = proc.stderr.read().decode() if proc.stderr else "Unknown FFmpeg Error"
            raise Exception(f"FFmpeg exited with {proc.returncode}: {err}")


    @classmethod
    def create_animated_shot(cls, image_path, output_path, width, height, fps, duration,
            animation_type='zoom_in', zoom_amount=0.15, use_nvenc=True, 
            log_callback=None, use_cache=True, **kwargs):
        
        if log_callback is None:
            log_callback = lambda x: None
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        clean_duration, _ = cls.sync_duration_to_frames(duration, fps)
        
        # Caching
        if use_cache:
            cache_key = cls._get_cache_key(image_path, width, height, clean_duration, animation_type, zoom_amount)
            cached = cls._get_from_cache(cache_key)
            if cached:
                import shutil
                try:
                    shutil.copy2(cached, output_path)
                    return output_path
                except Exception:
                    pass

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Load Image (Once)
            try:
                 img = Image.open(image_path).convert('RGB')
            except Exception as e:
                 raise Exception(f"Failed to load image: {e}")

            # Stream Render
            cls._render_frame_stream(
                img=img, output_path=output_path, width=width, height=height,
                fps=fps, duration=clean_duration, animation_type=animation_type,
                zoom_amount=zoom_amount, use_nvenc=use_nvenc
            )
            
            # Cache Success
            if use_cache and os.path.exists(output_path):
                cls._add_to_cache(cache_key, output_path)
                
            return output_path
            
        except Exception as e:
            # Fallback for NVENC failure
            if use_nvenc and ('nvenc' in str(e).lower() or 'cuda' in str(e).lower()):
                logger.warning(f"NVENC failed ({e}), retrying with CPU...")
                return cls.create_animated_shot(image_path, output_path, width, height,
                    fps, duration, animation_type, zoom_amount, use_nvenc=False, 
                    log_callback=log_callback, use_cache=False)
            
            log_callback(f"Animation failed: {e}")
            raise

    @classmethod
    def create_multiple_shots_parallel(cls, shots_config, max_workers=None, log_callback=None,
                                        use_batch_render=True, batch_size=10):
        import threading
        import shutil

        if log_callback is None:
            log_callback = print
        if not shots_config:
            return []

        # --- Dynamic worker count (default: half of CPU cores, capped at shot count) ---
        if max_workers is None or max_workers <= 0:
            import os as _os
            cpu = _os.cpu_count() or 4
            max_workers = min(len(shots_config), max(4, cpu // 2))
        if use_batch_render:
            max_workers = min(max_workers, max(1, int(batch_size or 1)))

        results = [None] * len(shots_config)

        # --- Deduplication pre-pass ---
        # Build a signature for each shot. If two shots share the same signature
        # (same image, same size/fps/duration/animation), only render the first;
        # the rest are simply copied from that result.
        sig_to_first_idx = {}   # signature -> index of first occurrence
        duplicates = {}         # index -> index of canonical shot to copy from

        for i, cfg in enumerate(shots_config):
            sig = (
                str(cfg.get('image_path', '')),
                cfg.get('width', 1920),
                cfg.get('height', 1080),
                cfg.get('fps', 60),
                round(cfg.get('duration', 3.0), 3),
                cfg.get('animation_type', 'zoom_in'),
                round(cfg.get('zoom_amount', 0.15), 4),
            )
            if sig in sig_to_first_idx:
                duplicates[i] = sig_to_first_idx[sig]
            else:
                sig_to_first_idx[sig] = i

        unique_indices = [i for i in range(len(shots_config)) if i not in duplicates]
        dup_count = len(duplicates)
        if dup_count:
            log_callback(f"Animation V13 (Python Engine): Processing {len(shots_config)} shots "
                         f"({len(unique_indices)} unique + {dup_count} deduplicated)...")
        else:
            log_callback(f"Animation V13 (Python Engine): Processing {len(shots_config)} shots...")

        # Lock to guard copy-after-render for duplicates
        result_lock = threading.Lock()

        def process_shot(idx, config):
            try:
                path = cls.create_animated_shot(
                    image_path=config['image_path'], output_path=config['output_path'],
                    width=config.get('width', 1920), height=config.get('height', 1080),
                    fps=config.get('fps', 60), duration=config.get('duration', 3.0),
                    animation_type=config.get('animation_type', 'zoom_in'),
                    zoom_amount=config.get('zoom_amount', 0.15),
                    use_nvenc=config.get('use_nvenc', True))
                return idx, path, None
            except Exception as e:
                return idx, None, str(e)

        # Render only unique shots in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(process_shot, i, shots_config[i]): i
                for i in unique_indices
            }
            completed = 0
            for future in as_completed(futures):
                idx, path, error = future.result()
                completed += 1
                with result_lock:
                    results[idx] = path

                if error:
                    log_callback(f"   [Error] Shot {idx+1}: {error}")
                else:
                    sz = os.path.getsize(path) / 1024
                    shot_cfg = shots_config[idx]
                    anim_type = shot_cfg.get('animation_type', '?')
                    # ⚠️ threshold: ultrafast h264 short clips are legitimately 20-60 KB
                    symbol = "✅" if sz > 50 else "⚠️"
                    log_callback(f"   {symbol} Shot {idx+1}: {anim_type} -> {sz:.1f} KB")

                if completed % 5 == 0 or completed == len(unique_indices):
                    log_callback(f"   Progress: {completed}/{len(unique_indices)} unique "
                                 f"(+{dup_count} copies pending)")

        # Copy duplicate shots from their canonical source
        if duplicates:
            copied = 0
            for dup_idx, src_idx in duplicates.items():
                src_path = results[src_idx]
                if src_path and Path(src_path).exists():
                    dst_path = shots_config[dup_idx]['output_path']
                    try:
                        Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_path, dst_path)
                        results[dup_idx] = dst_path
                        copied += 1
                    except Exception as copy_err:
                        log_callback(f"   [CopyError] Shot {dup_idx+1}: {copy_err}")
                else:
                    log_callback(f"   [CopySkip] Shot {dup_idx+1}: source not available")
            log_callback(f"   ♻️ Скопировано дубликатов: {copied}/{dup_count}")

        return results

    @classmethod
    def get_animation_for_index(cls, index, style='mix'):
        if style == 'mix':
            types = AnimationType.all()
            return types[index % len(types)]
        return style

    # Alias for compatibility
    create_animated_shot_fast = create_animated_shot
