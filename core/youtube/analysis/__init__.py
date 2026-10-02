#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Analysis Module
Video analysis functions for motion, faces, subtitles, and watermarks detection.
"""

from .motion import (
    is_likely_slideshow_by_metadata,
    crop_subtitles_roi,
    calculate_internal_motion,
    calculate_rigidity_score,
    classify_slideshow_differences,
    analyze_video_for_slideshow,
    is_slideshow_by_motion,
    calculate_motion_score
)

from .faces import (
    detect_faces_in_frame,
    analyze_clip_for_faces
)

from .subtitles import (
    detect_hardcoded_subtitles,
    check_clip_for_subtitles
)

from .watermarks import (
    detect_text_watermarks,
    has_watermark
)

from .moments import (
    find_interesting_moments,
    select_best_clip_time
)

__all__ = [
    # Motion analysis
    'is_likely_slideshow_by_metadata',
    'crop_subtitles_roi',
    'calculate_internal_motion',
    'calculate_rigidity_score',
    'classify_slideshow_differences',
    'analyze_video_for_slideshow',
    'is_slideshow_by_motion',
    'calculate_motion_score',
    # Face detection
    'detect_faces_in_frame',
    'analyze_clip_for_faces',
    # Subtitle detection
    'detect_hardcoded_subtitles',
    'check_clip_for_subtitles',
    # Watermark detection
    'detect_text_watermarks',
    'has_watermark',
    # Interesting moments
    'find_interesting_moments',
    'select_best_clip_time',
]
