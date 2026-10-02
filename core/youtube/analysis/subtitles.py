#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Subtitle Detection
Handles hardcoded subtitle detection in videos.
"""

from typing import Dict, Callable


def _dummy_log(msg: str):
    pass

def detect_hardcoded_subtitles(
    video_path: str,
    start_time: float = 0,
    duration: float = 5.0,
    num_samples: int = 5,
    log_callback: Callable = None
) -> Dict:
    """
      (hardcoded)   .
    
    V2 :
    -   40%  (  )
    -    (20%   40%)
    -      (YouTube   )
    -   
    
    Args:
        video_path:   
        start_time:   ()
        duration:   ()
        num_samples:    
        log_callback:  
        
    Returns:
        {
            'has_subtitles': bool,
            'confidence': float (0-100),
            'subtitle_frames': int,
            'avg_text_height': float
        }
    """
    if log_callback is None:
        log_callback = _dummy_log
    
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {'has_subtitles': False, 'confidence': 0, 'error': 'no_opencv'}
    
    #  OCR
    ocr_engine = None
    reader = None
    try:
        import easyocr
        ocr_engine = 'easyocr'
        reader = easyocr.Reader(['en', 'ru', 'ch_sim'], gpu=False, verbose=False)
    except ImportError:
        try:
            import pytesseract
            ocr_engine = 'pytesseract'
        except ImportError:
            ocr_engine = 'contrast'
    
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return {'has_subtitles': False, 'confidence': 0, 'error': 'cant_open'}
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30
        
        #  
        sample_times = [start_time + (duration / (num_samples + 1)) * (i + 1) for i in range(num_samples)]
        
        frames_with_subs = 0
        text_heights = []
        
        for t in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue
            
            h, w = frame.shape[:2]
            
            #     ,     (YouTube captions)
            regions_to_check = [
                frame[int(h * 0.60):, :],  #  40%
                frame[int(h * 0.35):int(h * 0.65), :]  #  30%
            ]
            
            has_text_in_frame = False
            
            for region_idx, subtitle_region in enumerate(regions_to_check):
                if has_text_in_frame:
                    break
                    
                if ocr_engine == 'easyocr' and reader:
                    results = reader.readtext(subtitle_region, detail=1)
                    for (bbox, text, conf) in results:
                        if conf > 0.3 and len(text.strip()) > 2:
                            text_h = abs(bbox[2][1] - bbox[0][1])
                            text_height_ratio = text_h / h
                            
                            if 0.015 < text_height_ratio < 0.20:
                                has_text_in_frame = True
                                text_heights.append(text_height_ratio)
                                break
                                
                elif ocr_engine == 'pytesseract':
                    import pytesseract
                    gray = cv2.cvtColor(subtitle_region, cv2.COLOR_BGR2GRAY)
                    gray = cv2.convertScaleAbs(gray, alpha=1.5, beta=0)
                    
                    text = pytesseract.image_to_string(gray, lang='eng+rus')
                    if len(text.strip()) > 3:
                        has_text_in_frame = True
                        text_heights.append(0.05)
                        
                else:
                    gray = cv2.cvtColor(subtitle_region, cv2.COLOR_BGR2GRAY)
                    
                    #    ( )
                    _, bright = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
                    bright_ratio = np.sum(bright > 0) / bright.size
                    
                    #    (   )
                    hsv = cv2.cvtColor(subtitle_region, cv2.COLOR_BGR2HSV)
                    yellow_mask = cv2.inRange(hsv, (20, 100, 100), (40, 255, 255))
                    yellow_ratio = np.sum(yellow_mask > 0) / yellow_mask.size
                    
                    if 0.005 < bright_ratio < 0.20 or 0.005 < yellow_ratio < 0.15:
                        contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        
                        text_like_contours = 0
                        for cnt in contours:
                            x, y, cw, ch = cv2.boundingRect(cnt)
                            if cw > ch * 1.5 and cw > w * 0.05:  #  ch*2  w*0.1
                                text_like_contours += 1
                        
                        if text_like_contours >= 1:
                            has_text_in_frame = True
                            text_heights.append(0.05)
            
            if has_text_in_frame:
                frames_with_subs += 1
        
        cap.release()
        
        if num_samples == 0:
            return {'has_subtitles': False, 'confidence': 0}
        
        sub_ratio = frames_with_subs / num_samples
        avg_height = sum(text_heights) / len(text_heights) if text_heights else 0
        
        #      1  5     
        has_subs = sub_ratio > 0.20
        confidence = sub_ratio * 100
        
        return {
            'has_subtitles': has_subs,
            'confidence': confidence,
            'subtitle_frames': frames_with_subs,
            'total_frames': num_samples,
            'avg_text_height': avg_height
        }
        
    except Exception as e:
        return {'has_subtitles': False, 'confidence': 0, 'error': str(e)}



def check_clip_for_subtitles(
    video_path: str,
    start_time: float,
    duration: float,
    log_callback: Callable = None
) -> bool:
    """
          .
    
    V2:       .
    
    Returns:
        True   
    """
    result = detect_hardcoded_subtitles(
        video_path, 
        start_time=start_time,
        duration=min(duration, 5.0),
        num_samples=5,
        log_callback=log_callback
    )
    return result.get('has_subtitles', False)


