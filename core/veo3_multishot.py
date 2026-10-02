#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Veo 3 Multi-Shot System
- Multi-action shots with cinematic camera movements
- Auto-extension for long intros (16s, 24s, 32s+)
"""

import time
import json
from pathlib import Path
from typing import Callable, Optional, List

# 🌍 Unicode-safe temp директории
from core.utils import safe_temp_file
from core.process_registry import run_registered


# Импортируем дефолтный логгер из централизованного модуля
from core.logging_utils import get_default_logger

# Алиас для обратной совместимости
_dummy_log = get_default_logger()


# ============================================================
# CINEMATIC CAMERA MOVEMENTS
# ============================================================

CAMERA_MOVEMENTS = {
    "dolly_in": {
        "name": "Dolly In",
        "description": "Smooth forward movement towards subject",
        "prompt": "smooth dolly in, camera glides forward towards the subject"
    },
    "dolly_out": {
        "name": "Dolly Out",
        "description": "Backward movement revealing the scene",
        "prompt": "dolly out, camera pulls back revealing the full scene"
    },
    "crane_up": {
        "name": "Crane Up",
        "description": "Vertical rise revealing scale",
        "prompt": "crane shot rising upward, revealing the grand scale"
    },
    "crane_down": {
        "name": "Crane Down",
        "description": "Descending from above to subject",
        "prompt": "crane descending from above, approaching the subject"
    },
    "orbit_left": {
        "name": "Orbit Left",
        "description": "Circular movement around subject (left)",
        "prompt": "orbiting camera movement circling left around the subject"
    },
    "orbit_right": {
        "name": "Orbit Right",
        "description": "Circular movement around subject (right)",
        "prompt": "orbiting camera movement circling right around the subject"
    },
    "tracking_left": {
        "name": "Tracking Left",
        "description": "Lateral movement following action",
        "prompt": "tracking shot moving left, following the action"
    },
    "tracking_right": {
        "name": "Tracking Right",
        "description": "Lateral movement following action",
        "prompt": "tracking shot moving right, following the action"
    },
    "push_in": {
        "name": "Push In",
        "description": "Fast dramatic zoom towards subject",
        "prompt": "dramatic push in, fast zoom towards the subject's face"
    },
    "pull_back": {
        "name": "Pull Back",
        "description": "Dramatic reveal pulling away",
        "prompt": "dramatic pull back, revealing the epic scale of the scene"
    },
    "tilt_up": {
        "name": "Tilt Up",
        "description": "Vertical pan from bottom to top",
        "prompt": "tilt up from ground level, revealing height and grandeur"
    },
    "tilt_down": {
        "name": "Tilt Down",
        "description": "Vertical pan from top to bottom",
        "prompt": "tilt down from sky, descending to reveal the subject"
    },
    "steadicam_follow": {
        "name": "Steadicam Follow",
        "description": "Smooth following shot",
        "prompt": "steadicam following shot, smooth movement behind the subject"
    },
    "jib_arc": {
        "name": "Jib Arc",
        "description": "Sweeping arc movement",
        "prompt": "sweeping jib arc, elegant curved camera path"
    },
    "static_wide": {
        "name": "Static Wide",
        "description": "Locked wide establishing shot",
        "prompt": "static wide shot, locked camera, establishing the scene"
    },
    "handheld_intimate": {
        "name": "Handheld Intimate",
        "description": "Subtle handheld for intimacy",
        "prompt": "subtle handheld movement, intimate and personal feel"
    }
}

# Recommended sequences for different moods
CAMERA_SEQUENCES = {
    "epic": ["crane_up", "orbit_right", "push_in", "pull_back"],
    "dramatic": ["tilt_down", "dolly_in", "push_in", "crane_up"],
    "mysterious": ["dolly_in", "orbit_left", "tilt_up", "static_wide"],
    "action": ["tracking_right", "push_in", "steadicam_follow", "pull_back"],
    "emotional": ["dolly_in", "handheld_intimate", "crane_up", "orbit_left"],
    "documentary": ["static_wide", "dolly_in", "tracking_left", "tilt_up"],
    "cinematic": ["crane_down", "orbit_right", "dolly_in", "jib_arc"]
}


def get_camera_sequence(mood: str = "cinematic", num_shots: int = 4) -> List[str]:
    """Get camera movement sequence for mood"""
    sequence = CAMERA_SEQUENCES.get(mood, CAMERA_SEQUENCES["cinematic"])
    # Cycle through if need more shots
    result = []
    for i in range(num_shots):
        result.append(sequence[i % len(sequence)])
    return result


def get_camera_prompt(movement_key: str) -> str:
    """Get prompt text for camera movement"""
    movement = CAMERA_MOVEMENTS.get(movement_key, CAMERA_MOVEMENTS["dolly_in"])
    return movement["prompt"]


# ============================================================
# MULTI-ACTION SHOT GENERATOR
# ============================================================

def generate_multiaction_prompt(
    theme: str,
    subject: str,
    actions: List[str],
    camera_movements: List[str],
    duration: int = 8,
    api_key: str = None,
    log_callback: Callable[[str], None] = _dummy_log
) -> str:
    """
    Generate a multi-action prompt for Veo 3 with INTERNAL camera transitions.
    
    Veo 3 generates ONE continuous clip, so we describe camera movements
    and transitions WITHIN the clip using temporal language.
    
    Args:
        theme: Main theme/setting
        subject: Main subject (e.g., "a sailor", "a detective")
        actions: List of actions for each shot
        camera_movements: List of camera movement keys
        duration: Total duration in seconds
        api_key: Optional API key for AI enhancement
        log_callback: Logging callback
        
    Returns:
        Formatted prompt for Veo 3
    """
    num_shots = len(actions)
    shot_duration = duration / num_shots
    
    log_callback(f"   🎬 Генерация multi-action промпта: {num_shots} переходов по {shot_duration:.1f}s")
    
    # Temporal transition words for smooth internal cuts
    transitions = [
        "The scene opens with",
        "Then the camera transitions to",
        "Next, we see",
        "Finally,"
    ]
    
    # Build continuous sequence description
    sequence_parts = []
    for i, (action, cam_key) in enumerate(zip(actions, camera_movements)):
        cam_prompt = get_camera_prompt(cam_key)
        transition = transitions[i] if i < len(transitions) else "Then,"
        
        part = f"{transition} {cam_prompt}, {subject} {action}"
        sequence_parts.append(part)
    
    # Combine into flowing narrative
    sequence_text = ". ".join(sequence_parts)
    
    base_prompt = (
        f"Cinematic continuous shot with dynamic camera movement. Setting: {theme}. "
        f"{sequence_text}. "
        f"Smooth camera transitions, professional cinematography, 4K quality, "
        f"dramatic lighting, film grain aesthetic. "
        f"NO text overlays, NO watermarks, NO logos, NO subtitles."
    )
    
    # Try AI enhancement if API key provided
    if api_key:
        try:
            enhanced = _enhance_multiaction_prompt_ai(
                base_prompt, theme, subject, actions, camera_movements, api_key, log_callback
            )
            if enhanced:
                return enhanced
        except Exception as e:
            log_callback(f"   ⚠️ AI enhancement failed: {e}")
    
    return base_prompt


def _enhance_multiaction_prompt_ai(
    base_prompt: str,
    theme: str,
    subject: str,
    actions: List[str],
    camera_movements: List[str],
    api_key: str,
    log_callback: Callable[[str], None]
) -> Optional[str]:
    """Enhance multi-action prompt using AI for INTERNAL camera transitions"""
    import requests
    
    shots_desc = "\n".join([
        f"- Moment {i+1}: {action} with {CAMERA_MOVEMENTS[cam]['name']} camera movement"
        for i, (action, cam) in enumerate(zip(actions, camera_movements))
    ])
    
    ai_prompt = f"""Create a Veo 3 video prompt for a SINGLE continuous 8-second clip with INTERNAL camera transitions.

Theme: {theme}
Subject: {subject}
Sequence of moments:
{shots_desc}

CRITICAL REQUIREMENTS:
1. Describe ONE continuous video with smooth camera transitions WITHIN the clip
2. Use temporal language: "opens with", "then transitions to", "camera moves to reveal", "finally"
3. Each moment should flow naturally into the next
4. Describe dynamic camera movements (dolly, crane, orbit, tracking)
5. Add vivid visual details (lighting, atmosphere, textures)
6. Keep under 150 words total
7. Write in ENGLISH
8. End with: "NO text overlays, NO watermarks, NO logos."

EXAMPLE FORMAT:
"Cinematic continuous shot. The scene opens with a dramatic low-angle view of [subject], then the camera smoothly dollies forward revealing [detail]. Next, the camera orbits around as [action]. Finally, a crane shot rises to show [climax]. Dramatic lighting, film grain, 4K quality. NO text overlays, NO watermarks, NO logos."

Return ONLY the enhanced prompt, nothing else."""

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    
    payload = {
        "contents": [{"role": "user", "parts": [{"text": ai_prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 400}
    }
    
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    if resp.status_code != 200:
        return None
    
    data = resp.json()
    candidates = data.get('candidates', [])
    if not candidates:
        return None
    
    parts = candidates[0].get('content', {}).get('parts', [])
    result = "".join([p.get('text', '') for p in parts]).strip()
    
    # Clean up any markdown or quotes
    result = result.strip('"\'')
    
    # Validate result - must be substantial (not just "NO text overlays...")
    if len(result) < 100:
        log_callback(f"   ⚠️ AI prompt too short ({len(result)} chars), using base prompt")
        return None  # Will fallback to base_prompt
    
    if "NO text" not in result.lower():
        result += " NO text overlays, NO watermarks, NO logos."
    
    log_callback(f"   🤖 AI-enhanced prompt: {len(result)} chars")
    return result


def generate_actions_for_theme(
    theme: str,
    subject: str,
    num_actions: int,
    api_key: str,
    log_callback: Callable[[str], None] = _dummy_log
) -> List[str]:
    """
    Generate contextual actions for a theme using AI.
    Actions are designed to flow naturally within ONE continuous clip.
    
    Args:
        theme: Main theme
        subject: Main subject
        num_actions: Number of actions to generate
        api_key: Google AI API key
        log_callback: Logging callback
        
    Returns:
        List of action descriptions
    """
    import requests
    
    log_callback(f"   🎭 Генерация {num_actions} действий для темы...")
    
    # Fallback actions - designed for smooth transitions
    fallback_actions = [
        "emerges from shadow, face illuminated by dramatic light",
        "moves forward with determination, environment revealed around them",
        "pauses and turns, showing emotion in their expression",
        "reaches toward something significant, camera following the movement"
    ]
    
    try:
        ai_prompt = f"""Generate {num_actions} sequential visual MOMENTS for ONE continuous 8-second cinematic video.

Theme: {theme}
Subject: {subject}

CRITICAL REQUIREMENTS:
- Moments must FLOW naturally from one to the next (no hard cuts)
- Each moment is ~2 seconds of continuous action
- Focus on MOVEMENT that camera can follow smoothly
- Include environmental/lighting changes where possible
- Actions should BUILD in intensity (start subtle, end dramatic)
- Write in ENGLISH
- Return as JSON array of strings

GOOD EXAMPLES (flowing sequence):
["emerges from darkness into light", "walks forward as camera reveals the scene", "stops and turns with intense expression", "raises hand dramatically as camera rises"]

BAD EXAMPLES (disconnected):
["sits at desk", "runs through forest", "swims in ocean", "flies through sky"]

Return ONLY the JSON array, nothing else."""

        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": ai_prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 256}
        }
        
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            log_callback(f"   ⚠️ AI actions API error {resp.status_code}")
            return fallback_actions[:num_actions]
        
        data = resp.json()
        candidates = data.get('candidates', [])
        if not candidates:
            return fallback_actions[:num_actions]
        
        parts = candidates[0].get('content', {}).get('parts', [])
        result_text = "".join([p.get('text', '') for p in parts]).strip()
        
        if not result_text:
            log_callback("   ⚠️ Empty AI response, using fallback")
            return fallback_actions[:num_actions]
        
        # Parse JSON - clean up potential markdown code blocks
        if "```" in result_text:
            # Extract content between ``` markers
            parts_split = result_text.split("```")
            if len(parts_split) >= 2:
                result_text = parts_split[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
                result_text = result_text.strip()
        
        # Try to find JSON array in the text
        import re
        json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
        if json_match:
            result_text = json_match.group(0)
        
        actions = json.loads(result_text)
        
        if isinstance(actions, list) and len(actions) >= num_actions:
            log_callback(f"   ✅ Сгенерировано {len(actions)} действий")
            return actions[:num_actions]
        
        log_callback("   ⚠️ Invalid actions format, using fallback")
        return fallback_actions[:num_actions]
        
    except json.JSONDecodeError as e:
        log_callback(f"   ⚠️ JSON parse error: {str(e)[:50]}, using fallback")
        return fallback_actions[:num_actions]
    except Exception as e:
        log_callback(f"   ⚠️ AI actions failed: {e}")
        return fallback_actions[:num_actions]


# ============================================================
# AUTO-EXTENSION SYSTEM (for long intros)
# ============================================================

def calculate_clips_needed(target_duration: int, clip_duration: int = 8) -> int:
    """Calculate number of 8-second clips needed for target duration"""
    import math
    return math.ceil(target_duration / clip_duration)


def extract_last_frame(
    video_path: str,
    output_path: str,
    log_callback: Callable[[str], None] = _dummy_log
) -> Optional[str]:
    """
    Extract the last frame from a video for continuity.
    
    Args:
        video_path: Path to video
        output_path: Path to save the frame
        log_callback: Logging callback
        
    Returns:
        Path to extracted frame or None
    """
    try:
        # Get video duration first
        probe_cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            video_path
        ]
        
        result = run_registered(
            probe_cmd, capture_output=True, text=True,
            label="ffprobe_veo3_last_frame_source",
            encoding='utf-8', errors='ignore', timeout=10
        )
        
        if result.returncode != 0:
            return None
        
        data = json.loads(result.stdout)
        duration = float(data.get('format', {}).get('duration', 0))
        
        if duration <= 0:
            return None
        
        # Extract frame at duration - 0.1s (to avoid black frame)
        frame_time = max(0, duration - 0.1)
        
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(frame_time),
            '-i', video_path,
            '-vframes', '1',
            '-q:v', '2',
            output_path
        ]
        
        result = run_registered(
            cmd, capture_output=True, text=True,
            label="ffmpeg_veo3_last_frame_extract",
            encoding='utf-8', errors='ignore', timeout=30
        )
        
        if result.returncode == 0 and Path(output_path).exists():
            log_callback(f"   📸 Извлечён последний кадр: {output_path}")
            return output_path
        
        return None
        
    except Exception as e:
        log_callback(f"   ⚠️ Ошибка извлечения кадра: {e}")
        return None


def concat_clips_with_crossfade(
    clips: List[str],
    output_path: str,
    crossfade_duration: float = 0.5,
    log_callback: Callable[[str], None] = _dummy_log
) -> Optional[str]:
    """
    Concatenate multiple clips with crossfade transitions.
    
    Args:
        clips: List of video clip paths
        output_path: Output video path
        crossfade_duration: Duration of crossfade in seconds
        log_callback: Logging callback
        
    Returns:
        Path to concatenated video or None
    """
    if len(clips) < 2:
        if clips:
            import shutil
            shutil.copy(clips[0], output_path)
            return output_path
        return None
    
    log_callback(f"   🎬 Склейка {len(clips)} клипов с crossfade ({crossfade_duration}s)...")
    
    try:
        # Get durations of all clips
        durations = []
        for clip in clips:
            probe_cmd = [
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                clip
            ]
            result = run_registered(
                probe_cmd, capture_output=True, text=True,
                label="ffprobe_veo3_multishot_clip",
                encoding='utf-8', errors='ignore', timeout=10
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                dur = float(data.get('format', {}).get('duration', 8))
                durations.append(dur)
            else:
                durations.append(8.0)  # Default
        
        # Build complex filter for multiple xfade
        # For N clips, we need N-1 xfade operations
        filter_parts = []
        
        # Input labels
        for i in range(len(clips)):
            filter_parts.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}];")
        
        # Chain xfade operations
        current_offset = 0
        prev_label = "v0"
        
        for i in range(1, len(clips)):
            # Offset is cumulative duration minus crossfade overlaps
            current_offset += durations[i-1] - crossfade_duration
            
            out_label = f"xf{i}" if i < len(clips) - 1 else "outv"
            
            filter_parts.append(
                f"[{prev_label}][v{i}]xfade=transition=fade:"
                f"duration={crossfade_duration}:offset={current_offset:.2f}[{out_label}];"
            )
            
            prev_label = out_label
        
        # Remove trailing semicolon
        filter_complex = "".join(filter_parts).rstrip(";")
        
        # Build ffmpeg command
        cmd = ['ffmpeg', '-y']
        for clip in clips:
            cmd.extend(['-i', clip])
        
        cmd.extend([
            '-filter_complex', filter_complex,
            '-map', '[outv]',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '18',
            '-pix_fmt', 'yuv420p',
            '-an',  # No audio for intro clips
            output_path
        ])
        
        result = run_registered(
            cmd, capture_output=True, text=True,
            label="ffmpeg_veo3_crossfade_concat",
            encoding='utf-8', errors='ignore', timeout=300
        )
        
        if result.returncode != 0:
            log_callback("   ⚠️ Crossfade failed, trying simple concat...")
            return _simple_concat_clips(clips, output_path, log_callback)
        
        # Verify output
        if Path(output_path).exists():
            from .video_utils import get_video_duration
            final_dur = get_video_duration(output_path)
            log_callback(f"   ✅ Склеено: {final_dur:.1f}s")
            return output_path
        
        return None
        
    except Exception as e:
        log_callback(f"   ❌ Ошибка склейки: {e}")
        return _simple_concat_clips(clips, output_path, log_callback)


def _simple_concat_clips(
    clips: List[str],
    output_path: str,
    log_callback: Callable[[str], None]
) -> Optional[str]:
    """Simple concatenation without transitions (fallback)"""
    try:
        # Create concat file (🌍 Unicode-safe)
        concat_file = safe_temp_file(suffix='.txt', prefix='concat_')
        with open(concat_file, 'w', encoding='utf-8') as f:
            for clip in clips:
                f.write(f"file '{clip}'\n")
        
        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_file,
            '-c', 'copy',
            output_path
        ]
        
        result = run_registered(
            cmd, capture_output=True, text=True,
            label="ffmpeg_veo3_simple_concat",
            encoding='utf-8', errors='ignore', timeout=120
        )
        
        # Cleanup
        Path(concat_file).unlink(missing_ok=True)
        
        if result.returncode == 0 and Path(output_path).exists():
            log_callback("   ✅ Простая склейка выполнена")
            return output_path
        
        return None
        
    except Exception as e:
        log_callback(f"   ❌ Simple concat failed: {e}")
        return None


# ============================================================
# MAIN EXTENDED INTRO GENERATOR
# ============================================================

class Veo3MultishotGenerator:
    """
    Extended Veo 3 generator with multi-action shots and auto-extension.
    """
    
    def __init__(self):
        from .veo3_generator import Veo3Generator
        self.veo3 = Veo3Generator()
    
    def generate_extended_intro(
        self,
        theme: str,
        intro_text: str,
        reference_image: str,
        api_key: str,
        output_dir: Path,
        target_duration: int = 8,
        resolution: str = "1080p",
        aspect_ratio: str = "9:16",
        mood: str = "cinematic",
        num_actions: int = 4,
        log_callback: Callable[[str], None] = _dummy_log,
        unique_id: str = None
    ) -> Optional[str]:
        """
        Generate extended intro with multi-action shots.
        
        For durations > 8s, generates multiple clips and concatenates them.
        
        Args:
            theme: Main theme
            intro_text: Text for intro
            reference_image: Path to reference image
            api_key: Google AI API key
            output_dir: Output directory
            target_duration: Target duration in seconds (8, 16, 24, 32, etc.)
            resolution: "720p" or "1080p"
            aspect_ratio: "9:16" or "16:9"
            mood: Mood for camera sequence ("epic", "dramatic", "cinematic", etc.)
            num_actions: Number of actions per 8-second clip
            log_callback: Logging callback
            unique_id: Unique identifier for file naming (prevents conflicts in parallel generation)
            
        Returns:
            Path to generated intro video or None
        """
        # Generate unique_id if not provided
        if not unique_id:
            import uuid
            unique_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        log_callback("=" * 60)
        log_callback("🎬 VEO 3 MULTI-SHOT EXTENDED INTRO")
        log_callback("=" * 60)
        log_callback(f"   🎯 Целевая длительность: {target_duration}s")
        log_callback(f"   🎨 Настроение: {mood}")
        log_callback(f"   🎭 Действий на клип: {num_actions}")
        
        # Calculate clips needed
        clips_needed = calculate_clips_needed(target_duration, 8)
        log_callback(f"   📊 Требуется клипов: {clips_needed}")
        
        # Extract subject from theme/intro
        subject = self._extract_subject(theme, intro_text, api_key, log_callback)
        
        # Generate clips
        generated_clips = []
        current_reference = reference_image
        
        for clip_num in range(clips_needed):
            log_callback(f"\n🎥 Генерация клипа {clip_num + 1}/{clips_needed}...")
            
            # Generate actions for this clip
            actions = generate_actions_for_theme(
                theme, subject, num_actions, api_key, log_callback
            )
            
            # Get camera sequence
            camera_sequence = get_camera_sequence(mood, num_actions)
            
            # Generate multi-action prompt
            prompt = generate_multiaction_prompt(
                theme=theme,
                subject=subject,
                actions=actions,
                camera_movements=camera_sequence,
                duration=8,
                api_key=api_key,
                log_callback=log_callback
            )
            
            log_callback(f"   📝 Промпт: {prompt[:100]}...")
            
            # Generate clip
            clip_path = output_dir / f"veo3_clip_{unique_id}_{clip_num + 1}.mp4"
            
            result = self.veo3.generate_video_from_images(
                reference_images=[current_reference],
                prompt=prompt,
                api_key=api_key,
                output_path=str(clip_path),
                duration=8,
                resolution=resolution,
                fps=24,
                aspect_ratio=aspect_ratio,
                theme=theme,
                log_callback=log_callback
            )
            
            if result and Path(result).exists():
                generated_clips.append(result)
                log_callback(f"   ✅ Клип {clip_num + 1} готов")
                
                # Extract last frame for next clip (continuity)
                if clip_num < clips_needed - 1:
                    next_ref = output_dir / f"ref_frame_{unique_id}_{clip_num + 1}.png"
                    extracted = extract_last_frame(result, str(next_ref), log_callback)
                    if extracted:
                        current_reference = extracted
                        log_callback("   🔗 Continuity: используем последний кадр для следующего клипа")
            else:
                log_callback(f"   ❌ Клип {clip_num + 1} не удался")
                # Continue with remaining clips
        
        if not generated_clips:
            log_callback("❌ Не удалось сгенерировать ни одного клипа")
            return None
        
        # Concatenate clips if multiple
        if len(generated_clips) == 1:
            final_path = output_dir / f"veo3_intro_final_{unique_id}.mp4"
            import shutil
            shutil.copy(generated_clips[0], final_path)
            log_callback(f"\n✅ Интро готово: {final_path}")
            return str(final_path)
        
        # Concatenate with crossfade
        final_path = output_dir / f"veo3_intro_final_{unique_id}.mp4"
        result = concat_clips_with_crossfade(
            clips=generated_clips,
            output_path=str(final_path),
            crossfade_duration=0.5,
            log_callback=log_callback
        )
        
        if result:
            log_callback(f"\n✅ Extended интро готово: {result}")
            
            # Cleanup individual clips
            for clip in generated_clips:
                try:
                    Path(clip).unlink(missing_ok=True)
                except:
                    pass
            
            return result
        
        # Fallback: return first clip
        log_callback("⚠️ Склейка не удалась, возвращаем первый клип")
        return generated_clips[0]
    
    def _extract_subject(
        self,
        theme: str,
        intro_text: str,
        api_key: str,
        log_callback: Callable[[str], None]
    ) -> str:
        """Extract main subject from theme using AI"""
        import requests
        
        try:
            ai_prompt = f"""Extract the main visual SUBJECT from this theme for a video.

Theme: {theme}
Intro text: {intro_text[:200]}

Return a short description of the main subject (2-5 words).
Examples: "a mysterious detective", "an ancient warrior", "a young scientist"

Return ONLY the subject description, nothing else."""

            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
            headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
            
            payload = {
                "contents": [{"role": "user", "parts": [{"text": ai_prompt}]}],
                "generationConfig": {"temperature": 0.5, "maxOutputTokens": 50}
            }
            
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get('candidates', [])
                if candidates:
                    parts = candidates[0].get('content', {}).get('parts', [])
                    subject = "".join([p.get('text', '') for p in parts]).strip()
                    if subject and len(subject) < 100:
                        log_callback(f"   👤 Субъект: {subject}")
                        return subject
        except:
            pass
        
        # Fallback
        return "the main subject"


# Singleton instance
_multishot_generator = None

def get_multishot_generator() -> Veo3MultishotGenerator:
    """Get singleton instance of multishot generator"""
    global _multishot_generator
    if _multishot_generator is None:
        _multishot_generator = Veo3MultishotGenerator()
    return _multishot_generator
