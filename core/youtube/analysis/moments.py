"""Find visually interesting moments and choose clip start times."""

import random
from typing import Callable, Dict, List, Optional

from ..utils import _dummy_log


def find_interesting_moments(
    video_path: str,
    num_moments: int = 10,
    min_motion_threshold: float = 10.0,  # Cycle 18: 15→10 for slow game footage
    max_samples: int = 48,
    log_callback: Optional[Callable] = None,
) -> List[Dict]:
    """Find high-motion moments and scene changes in a video.

    Cycle 18 improvements:
    - Minimum gap between selected moments raised to 8s (was 5s) to avoid
      selecting clips from the same scene
    - min_motion_threshold lowered to 10.0 for slow-paced game/cutscene footage
    - Quality gate: moments with score < 5 skipped (near-static)
    """
    log = log_callback or _dummy_log

    try:
        import cv2
        import numpy as np
    except ImportError:
        return []

    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0
        if duration < 10:
            cap.release()
            return []

        sample_range = max(1, int(total_frames * 0.8))
        sample_interval = max(1, int(fps), sample_range // max(1, max_samples))
        moments = []
        prev_gray = None
        prev_hist = None
        start_frame = int(total_frames * 0.1)
        end_frame = int(total_frames * 0.9)

        for frame_num in range(start_frame, end_frame, sample_interval):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ok, frame = cap.read()
            if not ok:
                continue

            current_time = frame_num / fps
            small = cv2.resize(frame, (320, 180))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            hist = cv2.calcHist(
                [small], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256]
            )
            cv2.normalize(hist, hist)

            if prev_gray is not None:
                motion_score = float(np.mean(cv2.absdiff(gray, prev_gray)))
                scene_change = 0.0
                if prev_hist is not None:
                    correlation = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                    if correlation < 0.7:
                        scene_change = (1 - correlation) * 50

                total_score = motion_score + scene_change
                # Cycle 20: quality gate — skip near-static frames
                if total_score >= 5 and total_score > min_motion_threshold:
                    moments.append(
                        {
                            "time": current_time,
                            "score": total_score,
                            "type": "scene_change" if scene_change > 20 else "high_motion",
                            "motion": motion_score,
                            "scene_change": scene_change,
                        }
                    )

            prev_gray = gray
            prev_hist = hist

        cap.release()
        moments.sort(key=lambda item: item["score"], reverse=True)

        # Cycle 18: 8s minimum gap (was 5s) to ensure scene diversity
        filtered = []
        for moment in moments:
            if not any(abs(moment["time"] - saved["time"]) < 8 for saved in filtered):
                filtered.append(moment)
            if len(filtered) >= num_moments:
                break
        return filtered
    except Exception as exc:
        log(f"Interesting-moment analysis failed: {exc}")
        return []


def select_best_clip_time(
    video_path: str,
    clip_duration: float,
    interesting_moments: Optional[List[Dict]] = None,
    avoid_faces: bool = True,
    log_callback: Optional[Callable] = None,
) -> Optional[float]:
    """Choose a clip start around an interesting moment."""
    log = log_callback or _dummy_log

    try:
        import cv2
    except ImportError:
        return None

    try:
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0
        cap.release()
    except (cv2.error, OSError, ValueError, AttributeError):
        return None

    if duration < clip_duration + 5:
        return None

    if not interesting_moments:
        interesting_moments = find_interesting_moments(
            video_path, num_moments=20, log_callback=log
        )

    if not interesting_moments:
        safe_start = duration * 0.1
        safe_end = duration * 0.9 - clip_duration
        return random.uniform(safe_start, safe_end) if safe_end > safe_start else None

    analyze_clip_for_faces = None
    if avoid_faces:
        from .faces import analyze_clip_for_faces

    for moment in interesting_moments:
        start_time = max(0, moment["time"] - clip_duration / 2)
        if start_time + clip_duration > duration * 0.9:
            continue
        if analyze_clip_for_faces:
            face_info = analyze_clip_for_faces(video_path, start_time, clip_duration)
            if face_info["has_large_faces"]:
                continue
        return start_time

    return max(0, interesting_moments[0]["time"] - clip_duration / 2)


__all__ = ["find_interesting_moments", "select_best_clip_time"]
