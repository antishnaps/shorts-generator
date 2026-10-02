#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Motion Analysis
Handles video motion analysis and slideshow detection.
"""

from typing import Dict, Tuple, Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import numpy as np
import time

try:
    from ..constants import SLIDESHOW_KEYWORDS, SLIDESHOW_CHANNELS
except ImportError:
    SLIDESHOW_KEYWORDS = []
    SLIDESHOW_CHANNELS = []

def _dummy_log(msg: str):
    pass


def is_likely_slideshow_by_metadata(title: str, channel: str = None) -> bool:
    """
      ,     .
    
    Args:
        title:  
        channel:   ()
        
    Returns:
        True   
    """
    title_lower = title.lower()
    
    #  
    for keyword in SLIDESHOW_KEYWORDS:
        if keyword.lower() in title_lower:
            return True
    
    #  
    if channel:
        channel_lower = channel.lower()
        for keyword in SLIDESHOW_CHANNELS:
            if keyword.lower() in channel_lower:
                return True
    
    return False


def crop_subtitles_roi(frame, bottom_crop_percent: float = 0.25):
    """
          .
            .
    
    Args:
        frame:  (numpy array)
        bottom_crop_percent:    (0.25 = 25%)
        
    Returns:
         
    """
    h = frame.shape[0]
    new_h = int(h * (1 - bottom_crop_percent))
    return frame[:new_h, :]


def calculate_internal_motion(gray1, gray2, threshold: int = 20):
    """
     " " -    .
    
           (  )
      (  ).
    
    Args:
        gray1:   (grayscale)
        gray2:   (grayscale)
        threshold:     (15-25)
        
    Returns:
        float:    (0.0 - 1.0)
    """
    import cv2
    
    try:
        # Лё blur    
        g1 = cv2.GaussianBlur(gray1, (5, 5), 0)
        g2 = cv2.GaussianBlur(gray2, (5, 5), 0)
        
        # 
        diff = cv2.absdiff(g1, g2)
        
        #  ( )
        _, thresh = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
        
        #    
        non_zero = cv2.countNonZero(thresh)
        total_pixels = gray1.shape[0] * gray1.shape[1]
        
        return non_zero / total_pixels
        
    except Exception:
        return 0.0


def calculate_rigidity_score(prev_gray, curr_gray):
    """
     Rigidity Score -  "" .
    
     (Ken Burns) =     .
     %  (>0.85) =       = .
     %  (<0.50) =    =  .
    
      residual_diff -     .
     residual =   = .
     residual = ""   =  .
    
    Args:
        prev_gray:   (grayscale)
        curr_gray:   (grayscale)
        
    Returns:
        tuple: (rigidity_score, residual_diff, movement_magnitude)  (None, None, None)
    """
    import cv2
    import numpy as np
    
    try:
        # 1.   
        p0 = cv2.goodFeaturesToTrack(
            prev_gray, mask=None, maxCorners=200,
            qualityLevel=0.01, minDistance=30, blockSize=7
        )
        
        if p0 is None or len(p0) < 10:
            return None, None, None  #  
        
        # 2.    Optical Flow
        p1, st, err = cv2.calcOpticalFlowPyrLK(prev_gray, curr_gray, p0, None)
        
        #    
        good_new = p1[st == 1]
        good_old = p0[st == 1]
        
        if len(good_new) < 10:
            return None, None, None
        
        # 3.     (Scale + Rotation + Translation)
        M, inliers = cv2.estimateAffinePartial2D(good_old, good_new)
        
        if inliers is None or M is None:
            return None, None, None
        
        # 4. Rigidity Score:      
        rigidity_ratio = np.sum(inliers) / len(good_new)
        
        # 5. Residual Difference:     
        #     ,     ""
        h, w = prev_gray.shape
        warped_prev = cv2.warpAffine(prev_gray, M, (w, h))
        residual = cv2.absdiff(warped_prev, curr_gray)
        residual_diff = np.mean(residual)
        
        # 6. Movement Magnitude:    (Ken Burns  )
        #  translation  scale  
        tx, ty = M[0, 2], M[1, 2]  # Translation
        scale = np.sqrt(M[0, 0]**2 + M[0, 1]**2)  # Scale factor
        movement = np.sqrt(tx**2 + ty**2) + abs(scale - 1.0) * 100
        
        return rigidity_ratio, residual_diff, movement
        
    except Exception:
        return None, None, None


def classify_slideshow_differences(differences) -> Tuple[bool, float]:
    """Classify several within-scene frame differences.

    Thresholds tuned to avoid false-positives on game footage (cutscenes,
    dialogue, slow pans) which legitimately have low per-frame diff but are
    NOT slideshows in the photo-compilation sense.
    """
    values = [float(value) for value in differences if value is not None]
    if not values:
        return False, 0.0
    median_diff = float(np.median(values))
    # static_ratio: fraction of pairs with diff below a VERY low threshold
    # Raised from 3.0 → 1.2 so that slow game pans (diff ~2-4) don't trigger
    static_ratio = sum(value < 1.2 for value in values) / len(values)
    if static_ratio >= 0.95 or median_diff < 0.5:
        # Virtually zero movement across all samples — almost certainly a photo
        confidence = 98.0
    elif static_ratio >= 0.85 or median_diff < 1.2:
        # A mostly static sequence with an occasional large jump is the
        # characteristic pattern of a photo slideshow with hard cuts.
        confidence = 90.0
    elif static_ratio >= 0.70 or median_diff < 2.5:
        confidence = 55.0
    else:
        confidence = max(0.0, 35.0 - median_diff * 2.0)
    # Require confidence >= 75 to label as slideshow (was 70)
    return confidence >= 75.0, confidence


def analyze_video_for_slideshow(
    video_path: str, 
    num_samples: int = 15,
    log_callback: Callable = None
) -> Dict:
    """
    Проверяет несколько участков видео на движение внутри сцен.
    """
    if log_callback is None:
        log_callback = _dummy_log
    
    analysis_start = time.time()
    
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {'is_slideshow': False, 'confidence': 0, 'error': 'no_opencv'}
    
    try:
        cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            return {'is_slideshow': False, 'confidence': 0, 'error': 'cant_open'}
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        duration = total_frames / fps if fps > 0 else 0
        
        if total_frames < 60 or fps <= 0 or duration < 3:
            cap.release()
            return {'is_slideshow': False, 'confidence': 0, 'error': 'too_short'}
        
        pair_count = max(3, min(7, int(num_samples or 6)))
        differences = []
        for ratio in np.linspace(0.15, 0.85, pair_count):
            center = int(total_frames * float(ratio))
            frames = []
            for frame_index in (
                max(0, int(center - fps * 0.5)),
                min(total_frames - 1, int(center + fps * 0.5)),
            ):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, frame = cap.read()
                if ok:
                    small = cv2.resize(frame, (320, 180))
                    frames.append(crop_subtitles_roi(
                        cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 0.25
                    ))
            if len(frames) == 2:
                differences.append(float(np.mean(cv2.absdiff(frames[0], frames[1]))))
        cap.release()

        if not differences:
            return {'is_slideshow': False, 'confidence': 0, 'error': 'cant_read_frames'}

        is_slideshow, confidence = classify_slideshow_differences(differences)
        mean_diff = float(np.mean(differences))
        
        analysis_time = time.time() - analysis_start
        log_callback(f"   ⚡ Быстрый анализ завершён за {analysis_time:.2f}s (slideshow: {is_slideshow}, diff: {mean_diff:.1f})")
        
        return {
            'is_slideshow': is_slideshow,
            'confidence': confidence,
            'reasons': [f"fast_diff({mean_diff:.1f})"],
            'metrics': {
                'duration': round(duration, 1),
                'frame_diff_mean': round(mean_diff, 2),
                'sample_pairs': len(differences),
            }
        }
        
    except Exception as e:
        log_callback(f"     Ошибка анализа: {str(e)[:100]}")
        return {'is_slideshow': False, 'confidence': 0, 'error': str(e)[:50]}


def is_slideshow_by_motion(video_path: str, threshold: float = 50.0, log_callback: Callable = None, timeout: int = 45) -> Tuple[bool, float]:
    """
           .
    
    Args:
        video_path:   
        threshold:   (0-100,  = )
        log_callback:  
        timeout:   (  45 )
        
    Returns:
        (is_slideshow, confidence_score)
    """
    # Do not let ThreadPoolExecutor.__exit__ wait for a timed-out OpenCV task.
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(analyze_video_for_slideshow, video_path, log_callback=log_callback)
    try:
        result = future.result(timeout=timeout)
    except FuturesTimeoutError:
        future.cancel()
        if log_callback:
            log_callback(f"⏱️ Motion analysis timed out ({timeout}s), assuming not slideshow")
        return False, -1.0
    except Exception as e:
        if log_callback:
            log_callback(f"   Motion analysis error: {e}")
        return False, -1.0
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    
    if 'error' in result:
        #        
        return False, -1.0
    
    is_slideshow = result['confidence'] >= threshold
    
    #    
    if log_callback and is_slideshow:
        reasons = ', '.join(result.get('reasons', []))
        log_callback(f"    Slideshow detected: {result['confidence']}% ({reasons})")
    
    return is_slideshow, result['confidence']


# Legacy function for backward compatibility
def calculate_motion_score(video_path: str, num_samples: int = 8, log_callback: Callable = None) -> float:
    """
    Legacy wrapper    analyze_video_for_slideshow.
      score (100 =  , 0 = ).
    """
    result = analyze_video_for_slideshow(video_path, num_samples=num_samples, log_callback=log_callback)
    
    if 'error' in result:
        return -1.0
    
    # :  slideshow_score =  motion_score
    motion_score = 100 - result['confidence']
    return motion_score
