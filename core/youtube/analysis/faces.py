#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Face Detection
Handles face detection in video frames.
"""

from typing import Dict, Tuple

def detect_faces_in_frame(frame, min_face_ratio: float = 0.08) -> Tuple[int, float]:
    """
       .
    
    Args:
        frame:  (numpy array, BGR)
        min_face_ratio:     
        
    Returns:
        (_, ____)
    """
    try:
        import cv2
    except ImportError:
        return 0, 0.0
    
    try:
        #  Haar Cascade (,   OpenCV)
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        frame_area = h * w
        
        # Scale the minimum face size from the caller's threshold.  The old
        # implementation had a `min_face_ratio` argument, but the detector
        # always used a hard-coded 5%, so callers could not actually tune it.
        min_ratio = max(0.01, min(float(min_face_ratio), 0.5))

        #  
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(max(1, int(w * min_ratio)), max(1, int(h * min_ratio)))
        )
        
        if len(faces) == 0:
            return 0, 0.0
        
        #    
        max_face_ratio = 0.0
        for (x, y, fw, fh) in faces:
            face_area = fw * fh
            ratio = face_area / frame_area
            max_face_ratio = max(max_face_ratio, ratio)
        
        return len(faces), max_face_ratio
        
    except Exception:
        return 0, 0.0


def analyze_clip_for_faces(
    video_path: str,
    start_time: float,
    duration: float,
    num_samples: int = 5,
    max_face_ratio: float = 0.15  #  > 15%  =  
) -> Dict:
    """
         .
    
    Returns:
        {
            'has_large_faces': bool,
            'avg_face_count': float,
            'max_face_ratio': float,
            'face_frames_ratio': float  # %   
        }
    """
    try:
        import cv2
    except ImportError:
        return {'has_large_faces': False, 'avg_face_count': 0, 'max_face_ratio': 0, 'face_frames_ratio': 0}
    
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return {'has_large_faces': False, 'avg_face_count': 0, 'max_face_ratio': 0, 'face_frames_ratio': 0}
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30
        
        #     
        sample_times = [start_time + (duration / (num_samples + 1)) * (i + 1) for i in range(num_samples)]
        
        face_counts = []
        face_ratios = []
        frames_with_faces = 0
        
        for t in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue
            
            #   
            small = cv2.resize(frame, (320, 180))
            
            count, ratio = detect_faces_in_frame(small)
            face_counts.append(count)
            face_ratios.append(ratio)
            
            if count > 0:
                frames_with_faces += 1
        
        cap.release()
        
        if not face_counts:
            return {'has_large_faces': False, 'avg_face_count': 0, 'max_face_ratio': 0, 'face_frames_ratio': 0}
        
        avg_count = sum(face_counts) / len(face_counts)
        max_ratio = max(face_ratios)
        face_frames_ratio = frames_with_faces / len(face_counts)
        
        #  " " :
        # -   > max_face_ratio 
        # -     > 70%  (talking head)
        has_large = max_ratio > max_face_ratio or (face_frames_ratio > 0.7 and avg_count >= 1)
        
        return {
            'has_large_faces': has_large,
            'avg_face_count': avg_count,
            'max_face_ratio': max_ratio,
            'face_frames_ratio': face_frames_ratio
        }
        
    except Exception:
        return {'has_large_faces': False, 'avg_face_count': 0, 'max_face_ratio': 0, 'face_frames_ratio': 0}


# ============================================================
# ============================================================

