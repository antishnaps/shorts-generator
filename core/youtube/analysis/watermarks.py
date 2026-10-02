#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Watermark Detection
Handles text watermark detection using OCR.
"""

from typing import Dict, Tuple, Callable


def _dummy_log(msg: str):
    pass

def detect_text_watermarks(
    video_path: str,
    num_samples: int = 5,
    text_area_threshold: float = 0.02,
    log_callback: Callable = None
) -> Dict:
    """
            OCR.
    
      :
    1.           
    2.     (> 1-2% )
    3.     (  watermark)
    
    Args:
        video_path:   
        num_samples:    
        text_area_threshold:    (0.02 = 2%)
        log_callback:  
        
    Returns:
        Dict  :
        - has_watermark: bool
        - confidence: float (0-100)
        - text_found: list of detected texts
        - reasons: list of reasons
    """
    if log_callback is None:
        log_callback = _dummy_log
    
    try:
        import cv2
    except ImportError:
        return {'has_watermark': False, 'confidence': 0, 'error': 'no_opencv'}
    
    #   OCR
    ocr_engine = None
    try:
        import easyocr
        ocr_engine = 'easyocr'
    except ImportError:
        try:
            import pytesseract
            ocr_engine = 'pytesseract'
        except ImportError:
            return {'has_watermark': False, 'confidence': 0, 'error': 'no_ocr'}
    
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return {'has_watermark': False, 'confidence': 0, 'error': 'cant_open'}
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        
        if total_frames < 30 or fps <= 0:
            cap.release()
            return {'has_watermark': False, 'confidence': 0, 'error': 'too_short'}
        
        #   
        frame_indices = [int(total_frames * i / (num_samples + 1)) for i in range(1, num_samples + 1)]
        
        #  EasyOCR   
        reader = None
        if ocr_engine == 'easyocr':
            reader = easyocr.Reader(['en', 'ru'], gpu=False, verbose=False)
        
        all_texts = []
        corner_texts = []  #    ( watermarks)
        
        for frame_idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                continue
            
            h, w = frame.shape[:2]
            
            #    (  watermarks)
            corner_size = 0.15  # 15%  
            corners = {
                'top_left': (0, 0, int(w * corner_size), int(h * corner_size)),
                'top_right': (int(w * (1 - corner_size)), 0, w, int(h * corner_size)),
                'bottom_left': (0, int(h * (1 - corner_size)), int(w * corner_size), h),
                'bottom_right': (int(w * (1 - corner_size)), int(h * (1 - corner_size)), w, h),
            }
            
            # OCR   
            if ocr_engine == 'easyocr':
                results = reader.readtext(frame, detail=1)
                for (bbox, text, conf) in results:
                    if conf > 0.5 and len(text) > 2:
                        #   
                        cx = (bbox[0][0] + bbox[2][0]) / 2
                        cy = (bbox[0][1] + bbox[2][1]) / 2
                        
                        #   
                        text_w = abs(bbox[2][0] - bbox[0][0])
                        text_h = abs(bbox[2][1] - bbox[0][1])
                        text_area = (text_w * text_h) / (w * h)
                        
                        all_texts.append({
                            'text': text,
                            'conf': conf,
                            'area': text_area,
                            'cx': cx,
                            'cy': cy
                        })
                        
                        #     
                        for corner_name, (x1, y1, x2, y2) in corners.items():
                            if x1 <= cx <= x2 and y1 <= cy <= y2:
                                corner_texts.append({
                                    'text': text,
                                    'corner': corner_name,
                                    'conf': conf
                                })
                                break
            
            elif ocr_engine == 'pytesseract':
                import pytesseract
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)
                
                for i, text in enumerate(data['text']):
                    conf = int(data['conf'][i])
                    if conf > 50 and len(text.strip()) > 2:
                        x, y, tw, th = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
                        cx, cy = x + tw/2, y + th/2
                        text_area = (tw * th) / (w * h)
                        
                        all_texts.append({
                            'text': text,
                            'conf': conf / 100,
                            'area': text_area,
                            'cx': cx,
                            'cy': cy
                        })
                        
                        for corner_name, (x1, y1, x2, y2) in corners.items():
                            if x1 <= cx <= x2 and y1 <= cy <= y2:
                                corner_texts.append({
                                    'text': text,
                                    'corner': corner_name,
                                    'conf': conf / 100
                                })
                                break
        
        cap.release()
        
        #  
        watermark_score = 0
        reasons = []
        
        # 1.       = watermark
        if len(corner_texts) >= 2:
            #   
            corner_counts = {}
            for ct in corner_texts:
                corner = ct['corner']
                corner_counts[corner] = corner_counts.get(corner, 0) + 1
            
            #        50%+ 
            for corner, count in corner_counts.items():
                if count >= num_samples * 0.5:
                    watermark_score += 50
                    reasons.append(f"persistent_corner_text({corner}:{count}/{num_samples})")
                elif count >= 2:
                    watermark_score += 25
                    reasons.append(f"corner_text({corner}:{count})")
        
        # 2.   
        if all_texts:
            max_area = max(t['area'] for t in all_texts)
            sum(t['area'] for t in all_texts) / len(all_texts)
            
            if max_area > 0.05:  # > 5% 
                watermark_score += 30
                reasons.append(f"large_text_area({max_area:.1%})")
            elif max_area > text_area_threshold:
                watermark_score += 15
                reasons.append(f"medium_text_area({max_area:.1%})")
        
        # 3.   (       )
        text_counts = {}
        for t in all_texts:
            text_lower = t['text'].lower().strip()
            if len(text_lower) > 3:
                text_counts[text_lower] = text_counts.get(text_lower, 0) + 1
        
        for text, count in text_counts.items():
            if count >= num_samples * 0.6:  #  60%+ 
                watermark_score += 40
                reasons.append(f"repeated_text('{text[:20]}...':{count})")
                break
        
        has_watermark = watermark_score >= 50
        confidence = min(100, watermark_score)
        
        return {
            'has_watermark': has_watermark,
            'confidence': confidence,
            'text_found': [t['text'] for t in all_texts[:10]],
            'corner_texts': corner_texts,
            'reasons': reasons
        }
        
    except Exception as e:
        log_callback(f"    OCR error: {e}")
        return {'has_watermark': False, 'confidence': 0, 'error': str(e)}


def has_watermark(video_path: str, threshold: float = 50.0, log_callback: Callable = None) -> Tuple[bool, float]:
    """
          .
    
    Args:
        video_path:   
        threshold:   (0-100)
        log_callback:  
        
    Returns:
        (has_watermark, confidence)
    """
    result = detect_text_watermarks(video_path, log_callback=log_callback)
    
    if 'error' in result:
        return False, -1.0
    
    has_wm = result['confidence'] >= threshold
    
    if log_callback and has_wm:
        reasons = ', '.join(result.get('reasons', []))
        log_callback(f"    Watermark detected: {result['confidence']}% ({reasons})")
    
    return has_wm, result['confidence']


