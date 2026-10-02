#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Veo 3 integration module for AI-intro generation
Supports multi-action shots and extended intros (16s, 24s, 32s+)
"""

from pathlib import Path
from typing import Optional, Callable, Dict, List
import re

from .veo3_generator import Veo3Generator
from .veo3_multishot import (
    get_multishot_generator,
    CAMERA_MOVEMENTS,
    CAMERA_SEQUENCES
)
from .google_image_generator import GoogleImageGenerator
from .video_utils import remove_audio_from_video


# Импортируем дефолтный логгер из централизованного модуля
from core.logging_utils import get_default_logger

# Алиас для обратной совместимости
_dummy_log = get_default_logger()


def _generate_simple_veo3_prompt(
    theme: str,
    intro_text: str,
    style: str,
    api_key: str,
    log_callback: Callable[[str], None] = _dummy_log
) -> str:
    """
    Generate a simple but effective prompt for Veo 3 video generation.
    Uses AI to create an optimized prompt based on theme and style.
    """
    import requests
    
    # Style-specific keywords
    style_keywords = {
        "auto": "cinematic, dynamic camera movement, professional",
        "shocked": "dramatic zoom, intense reaction, shocking reveal",
        "curious": "slow reveal, mysterious atmosphere, building tension",
        "dramatic": "epic scale, dramatic lighting, powerful composition",
        "dynamic": "fast cuts, energetic movement, action-packed"
    }
    
    style_hint = style_keywords.get(style, style_keywords["auto"])
    
    # Fallback prompt
    fallback_prompt = (
        f"Cinematic video about {theme}. Visual brief: {intro_text[:300]}. {style_hint}. "
        f"Professional cinematography, smooth camera movement, 4K quality. "
        f"NO text overlays, NO watermarks, NO logos."
    )
    
    try:
        # Use AI to generate optimized prompt
        ai_prompt = f"""Create a short Veo 3 video prompt (50-80 words) for this theme:
Theme: {theme}
Style: {style_hint}
Visual brief: {intro_text[:300]}

Requirements:
- Describe VISUAL action and camera movement
- Use cinematic language
- NO text/watermarks/logos in video
- Write in ENGLISH

Return ONLY the prompt text, nothing else."""

        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": ai_prompt}]}],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 256
            }
        }
        
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            log_callback(f"   ⚠️ AI prompt error {resp.status_code}, using fallback")
            return fallback_prompt
        
        data = resp.json()
        candidates = data.get('candidates', [])
        if not candidates:
            return fallback_prompt
        
        parts = candidates[0].get('content', {}).get('parts', [])
        result = "".join([p.get('text', '') for p in parts]).strip()
        
        # Ensure no text/watermarks
        if "NO text" not in result:
            result += " NO text overlays, NO watermarks, NO logos."
        
        log_callback(f"   🤖 AI промпт сгенерирован ({len(result)} символов)")
        return result
        
    except Exception as e:
        log_callback(f"   ⚠️ AI prompt failed: {e}, using fallback")
        return fallback_prompt


def _generate_ai_multishot_prompt(
    theme: str,
    intro_text: str,
    shot_descriptions: list,
    cuts_text: str,
    api_key: str,
    log_callback: Callable[[str], None] = _dummy_log
) -> str:
    """
    Generate AI-enhanced multi-shot prompt for Veo 3 using JSON Mode.
    Falls back to basic prompt if AI fails.
    """
    import requests
    import json
    
    # Fallback prompt
    fallback_prompt = (
        f"Quick cuts montage with static shots: {cuts_text}. "
        f"Hard cuts between each shot. No camera movement within shots. "
        f"Professional editing, abrupt transitions."
    )
    
    try:
        # JSON Schema for Veo 3 multi-shot prompt
        veo3_schema = {
            "type": "object",
            "properties": {
                "main_prompt": {"type": "string", "description": "Main video description with shots"},
                "visual_style": {"type": "string", "description": "Visual aesthetics and color palette"},
                "transitions": {"type": "string", "description": "Transition style between shots"},
                "mood": {"type": "string", "description": "Overall mood and atmosphere"}
            },
            "required": ["main_prompt"]
        }
        
        shots_text = "\n".join([f"- {desc}" for desc in shot_descriptions])
        
        ai_system_prompt = f"""Create a detailed Veo 3 video prompt for a multi-shot intro.

Theme: {theme}
Intro text: {intro_text[:200]}

Shot breakdown:
{shots_text}

Requirements:
- main_prompt: Detailed description of the multi-shot sequence (80-150 words)
  Include: scene descriptions, visual elements, camera angles for each shot
  Use keywords: "hard cut", "static shot", "fixed camera"
- visual_style: Cinematic style, color grading, composition
- transitions: Hard cuts, abrupt transitions (NO smooth transitions)
- mood: Atmosphere matching the theme

Write in ENGLISH for Veo 3!
Focus on VISUAL details. NO text overlays, NO watermarks, NO logos."""

        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": ai_system_prompt}]}],
            "generationConfig": {
                "temperature": 0.8,
                "maxOutputTokens": 1024,
                "responseMimeType": "application/json",
                "responseSchema": veo3_schema
            }
        }
        
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        if resp.status_code != 200:
            log_callback(f"   ⚠️ AI prompt API error {resp.status_code}, using fallback")
            return fallback_prompt
        
        data = resp.json()
        candidates = data.get('candidates', [])
        if not candidates:
            return fallback_prompt
        
        parts = candidates[0].get('content', {}).get('parts', [])
        response_text = "".join([p.get('text', '') for p in parts]).strip()
        
        # Clean response text (remove potential newlines in strings)
        response_text = response_text.replace('\n', ' ').replace('\r', '')
        result = json.loads(response_text)
        
        # Build final prompt
        final_prompt = result.get('main_prompt', fallback_prompt)
        
        if result.get('visual_style'):
            final_prompt += f" STYLE: {result['visual_style']}."
        if result.get('transitions'):
            final_prompt += f" TRANSITIONS: {result['transitions']}."
        if result.get('mood'):
            final_prompt += f" MOOD: {result['mood']}."
        
        final_prompt += " NO text overlays, NO watermarks, NO logos, NO subtitles."
        
        log_callback(f"   🤖 AI промпт сгенерирован: {len(final_prompt)} символов")
        return final_prompt
        
    except Exception as e:
        log_callback(f"   ⚠️ AI prompt generation failed: {e}, using fallback")
        return fallback_prompt


def extract_intro_text(full_text: str, intro_duration: int, log_callback: Callable[[str], None] = _dummy_log) -> str:
    """
    Extract text for intro segment based on duration
    
    Args:
        full_text: Full text for the video
        intro_duration: Duration of intro in seconds
        log_callback: Logging callback
        
    Returns:
        Text for intro segment
    """
    # Estimate: ~2-3 words per second for TTS
    words_per_second = 2.5
    target_words = int(intro_duration * words_per_second)
    
    # Split into sentences
    sentences = re.split(r'[.!?]+', full_text)
    sentences = [s.strip() for s in sentences if s.strip()]
    
    if not sentences:
        return full_text[:100]  # Fallback
    
    # Take sentences until we reach target word count
    intro_text = ""
    word_count = 0
    
    for sentence in sentences:
        words = sentence.split()
        sentence_words = len(words)
        
        # Check if adding this sentence would exceed target
        if word_count + sentence_words <= target_words * 1.2:  # Allow 20% overflow
            intro_text += sentence + ". "
            word_count += sentence_words
        else:
            # If we haven't added anything yet, add at least first sentence
            if not intro_text:
                intro_text = sentence + ". "
                word_count = sentence_words
            break
    
    if not intro_text:
        intro_text = sentences[0] + "."
        word_count = len(sentences[0].split())
    
    log_callback(f"   📝 Текст интро: {len(intro_text)} символов, ~{word_count} слов")
    
    return intro_text.strip()


def build_veo3_prompt_context(
    theme: str,
    full_text: str,
    veo3_settings: dict,
    intro_duration: int,
    log_callback: Callable[[str], None] = _dummy_log,
) -> Dict[str, str]:
    """Build the visual brief used by reference-image and Veo prompt generation."""
    settings = dict(veo3_settings or {})
    base_intro = extract_intro_text(full_text, intro_duration, log_callback)
    mode = str(settings.get("content_mode") or "intro")
    prompt_source = str(settings.get("prompt_source") or "auto")
    motion = str(settings.get("motion_intensity") or "balanced")
    camera = str(settings.get("camera_style") or "auto")
    placement = str(settings.get("placement") or "replace_start")
    realism = str(settings.get("realism") or "auto")
    custom_prompt = str(settings.get("custom_prompt") or "").strip()
    negative_prompt = str(settings.get("negative_prompt") or "").strip()

    mode_hints = {
        "intro": "Create a strong opening hook that matches the first seconds of the narration.",
        "preview": "Create a teaser that previews the most important moment without resolving it.",
        "broll": "Create cinematic supporting B-roll that illustrates the topic without acting like an ad.",
    }
    motion_hints = {
        "calm": "Use restrained movement, slow reveals and stable composition.",
        "balanced": "Use moderate cinematic movement with clear readable action.",
        "dynamic": "Use energetic movement, fast visual progression and stronger action beats.",
    }
    camera_hints = {
        "auto": "Choose the best camera language for the subject.",
        "documentary": "Use grounded documentary framing and realistic archival-feeling coverage.",
        "handheld": "Use subtle handheld realism without shaky chaos.",
        "drone": "Use wide establishing movement and large-scale spatial context.",
        "macro": "Use close detail shots, textures and precise visual evidence.",
    }
    placement_hints = {
        "replace_start": "Design it as a clean replacement for the first seconds of the final video.",
        "intro": "Design it as a standalone opening clip that can sit before the main edit.",
        "middle_insert": "Design it as a mid-video insert that can interrupt the edit without feeling like a new topic.",
        "chapter_broll": "Design it as modular B-roll for chapter breaks, with a clear beginning and ending frame.",
    }
    realism_hints = {
        "auto": "",
        "photoreal": "Prioritize photoreal physical detail, natural light, real materials and believable scale.",
        "archival": "Use archival documentary texture, practical lighting and grounded historical realism.",
        "cinematic": "Use polished cinematic contrast, controlled color and composed dramatic depth.",
    }

    if prompt_source == "custom" and custom_prompt:
        subject_text = custom_prompt[:900]
    elif prompt_source == "theme":
        subject_text = f"Topic: {theme}."
    else:
        subject_text = f"Topic: {theme}. Narration excerpt: {base_intro}"

    safety = "No text overlays, no subtitles, no watermarks, no logos." if settings.get("avoid_text", True) else ""
    prompt_text = " ".join(
        part.strip()
        for part in (
            subject_text,
            mode_hints.get(mode, mode_hints["intro"]),
            motion_hints.get(motion, motion_hints["balanced"]),
            camera_hints.get(camera, camera_hints["auto"]),
            placement_hints.get(placement, placement_hints["replace_start"]),
            realism_hints.get(realism, ""),
            safety,
            f"Avoid: {negative_prompt[:500]}." if negative_prompt else "",
        )
        if part and part.strip()
    )
    return {
        "intro_text": base_intro,
        "prompt_text": prompt_text,
        "mode": mode,
        "prompt_source": prompt_source,
        "motion": motion,
        "camera": camera,
        "placement": placement,
        "realism": realism,
    }


def generate_reference_images(
    theme: str,
    intro_text: str,
    num_frames: int,
    api_key: str,
    output_dir: Path,
    video_settings: dict,
    log_callback: Callable[[str], None] = _dummy_log
) -> List[str]:
    """
    Generate reference images for Veo 3 using Gemini 3 Pro Image
    
    Args:
        theme: Main theme
        intro_text: Text for intro
        num_frames: Number of reference frames (1-3)
        api_key: Google AI API key
        output_dir: Output directory
        video_settings: Video settings (for aspect ratio)
        log_callback: Logging callback
        
    Returns:
        List of paths to generated reference images
    """
    log_callback(f"   🖼️ Генерация {num_frames} референсных кадров для Veo 3...")
    
    # Determine aspect ratio
    width = video_settings.get('width', 1080)
    height = video_settings.get('height', 1920)
    
    if width > height:
        aspect_ratio = "16:9"
    else:
        aspect_ratio = "9:16"
    
    # Create output directory
    ref_dir = output_dir / "veo3_references"
    ref_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize image generator
    image_gen = GoogleImageGenerator()
    
    reference_images = []
    
    for i in range(num_frames):
        log_callback(f"   📸 Генерация референса {i+1}/{num_frames}...")
        
        # Generate prompt for reference frame
        if num_frames == 1:
            # Single frame - main shot
            prompt = generate_reference_prompt(theme, intro_text, "main")
        else:
            # Multiple frames - sequence
            frame_types = ["wide", "medium", "close"]
            frame_type = frame_types[i % len(frame_types)]
            prompt = generate_reference_prompt(theme, intro_text, frame_type)
        
        log_callback(f"   📝 Промпт: {prompt[:80]}...")
        
        # Generate image
        output_path = ref_dir / f"reference_{i+1}.png"
        
        # Пробуем сначала gemini-3-pro-image, затем fallback на gemini-2.5-flash-image
        models_to_try = ["gemini-3-pro-image", "gemini-3.1-flash-image", "gemini-2.5-flash-image"]
        result = None
        
        for model in models_to_try:
            result = image_gen.generate_image(
                prompt=prompt,
                api_key=api_key,
                output_path=str(output_path),
                aspect_ratio=aspect_ratio,
                video_width=width,
                video_height=height,
                log_callback=log_callback,
                use_gemini_flash=False,
                variation_index=i,
                strict_theme_following=True,
                image_model=model
            )
            
            if result and Path(result).exists():
                break
            else:
                if model == models_to_try[0]:
                    log_callback(f"   ⚠️ {model} не удалось, пробуем fallback...")
        
        if result and Path(result).exists():
            reference_images.append(result)
            log_callback(f"   ✅ Референс {i+1} готов")
        else:
            log_callback(f"   ⚠️ Референс {i+1} не удалось сгенерировать")
    
    if not reference_images:
        log_callback("   ❌ Не удалось сгенерировать ни одного референса")
        return []
    
    log_callback(f"   ✅ Сгенерировано {len(reference_images)} референсов")
    return reference_images


def generate_reference_prompt(theme: str, intro_text: str, frame_type: str = "main") -> str:
    """
    Generate prompt for reference image based on frame type
    
    Args:
        theme: Main theme
        intro_text: Intro text
        frame_type: "main", "wide", "medium", "close"
        
    Returns:
        Prompt for image generation
    """
    # Base prompt
    base = (
        f"Ultra-photorealistic, IMAX-grade, hyper-cinematic image. "
        f"Theme: {theme}. "
        f"Visual brief: {intro_text[:500]}. "
        f"Professional cinematography, 4K quality, perfect lighting. "
        f"NO text, NO watermarks, NO logos, NO subtitles. "
    )
    
    # Frame-specific prompts
    if frame_type == "wide":
        specific = (
            "Wide establishing shot, epic scale, dramatic composition. "
            "Sweeping vista, environmental context, cinematic grandeur. "
        )
    elif frame_type == "medium":
        specific = (
            "Medium shot, balanced composition, clear subject focus. "
            "Professional framing, engaging perspective, dynamic angle. "
        )
    elif frame_type == "close":
        specific = (
            "Close-up shot, intense detail, emotional connection. "
            "Dramatic lighting, shallow depth of field, powerful impact. "
        )
    else:  # main
        specific = (
            "Dramatic hero shot, perfect composition, maximum impact. "
            "Cinematic lighting, professional quality, attention-grabbing. "
        )
    
    # Analyze intro text for mood
    intro_upper = intro_text.upper()
    if any(word in intro_upper for word in ["ШОКИРУЮЩ", "НЕ ПОВЕРИТЕ", "SHOCKING"]):
        mood = "Shocked expression, dramatic reaction, intense emotion. "
    elif any(word in intro_upper for word in ["ЧТО БУДЕТ", "ЕСЛИ", "WHAT IF"]):
        mood = "Curious expression, contemplative mood, questioning gaze. "
    elif any(word in intro_upper for word in ["СЕКРЕТ", "ПРАВДА", "SECRET"]):
        mood = "Mysterious atmosphere, dramatic shadows, revealing moment. "
    else:
        mood = "Dynamic energy, engaging presence, captivating scene. "
    
    return base + specific + mood


def generate_intro_with_veo3(
    theme: str,
    full_text: str,
    veo3_settings: dict,
    api_key: str,
    output_dir: Path,
    video_settings: dict,
    log_callback: Callable[[str], None] = _dummy_log
) -> Optional[str]:
    """
    Complete cycle of AI-intro generation with Veo 3.
    
    Supports:
    - Multi-action shots (multiple actions + camera movements in one clip)
    - Extended intros (16s, 24s, 32s via clip concatenation)
    
    Args:
        theme: Main theme
        full_text: Full text for the video
        veo3_settings: Veo 3 settings dict
        api_key: Google AI API key
        output_dir: Output directory
        video_settings: Video settings
        log_callback: Logging callback
        
    Returns:
        Path to generated intro video (without audio) or None if failed
    """
    import uuid
    import time
    
    # Generate unique ID for this intro to prevent file conflicts in parallel generation
    unique_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    
    try:
        log_callback("=" * 60)
        log_callback("🎬 ГЕНЕРАЦИЯ AI-ИНТРО ЧЕРЕЗ VEO 3 MULTI-SHOT")
        log_callback("=" * 60)
        
        # Extract settings
        intro_duration = veo3_settings.get('intro_duration', 8)
        quality_index = veo3_settings.get('quality', 0)
        style_index = veo3_settings.get('style', 0)
        
        # NEW: Multi-shot settings
        use_multishot = veo3_settings.get('use_multishot', True)
        num_actions = veo3_settings.get('num_actions', 4)  # Actions per 8s clip
        veo_model = veo3_settings.get('model', 'veo-3.1-fast-generate-preview')
        requested_aspect_ratio = veo3_settings.get('aspect_ratio')
        
        # Map quality index to string
        quality_map = {0: "720p", 1: "1080p"}
        quality = quality_map.get(quality_index, "720p")
        
        # Map style index to mood for camera sequences
        style_to_mood = {
            0: "cinematic",   # auto
            1: "dramatic",    # shocked
            2: "mysterious",  # curious
            3: "epic",        # dramatic
            4: "action"       # dynamic
        }
        mood = style_to_mood.get(style_index, "cinematic")
        
        # For 1080p, duration must be 8s per clip
        if quality == "1080p":
            clip_duration = 8
        else:
            clip_duration = 8  # Always use 8s for best quality
        
        log_callback("⚙️ Настройки:")
        log_callback(f"   📊 Длительность: {intro_duration}s")
        log_callback(f"   🎬 Качество: {quality}")
        log_callback(f"   🎨 Настроение: {mood}")
        log_callback(f"   🎭 Multi-shot: {'ДА' if use_multishot else 'НЕТ'}")
        if use_multishot:
            log_callback(f"   🎯 Действий на клип: {num_actions}")
        
        # Step 1: Extract intro text
        log_callback("\n📝 Шаг 1/5: Извлечение текста для интро...")
        prompt_context = build_veo3_prompt_context(
            theme=theme,
            full_text=full_text,
            veo3_settings=veo3_settings,
            intro_duration=intro_duration,
            log_callback=log_callback,
        )
        intro_text = prompt_context["intro_text"]
        veo_prompt_text = prompt_context["prompt_text"]
        log_callback(f"   Текст интро: \"{intro_text[:100]}...\"")
        
        # Step 2: Generate reference image
        log_callback("\n🖼️ Шаг 2/5: Генерация референсного изображения...")
        
        # Determine aspect ratio
        width = video_settings.get('width', 1080)
        height = video_settings.get('height', 1920)
        
        if requested_aspect_ratio in {"9:16", "16:9"}:
            aspect_ratio = requested_aspect_ratio
        elif width > height:
            aspect_ratio = "16:9"
        else:
            aspect_ratio = "9:16"
        
        reference_images = generate_reference_images(
            theme=theme,
            intro_text=veo_prompt_text,
            num_frames=1,
            api_key=api_key,
            output_dir=output_dir,
            video_settings=video_settings,
            log_callback=log_callback
        )
        
        if not reference_images:
            log_callback("   ❌ Не удалось сгенерировать референс, отмена AI-интро")
            return None
        
        # Step 3: Generate video with Veo 3
        log_callback("\n🎥 Шаг 3/5: Генерация AI-видео через Veo 3...")
        
        # Use multi-shot generator for extended intros or multi-action
        if use_multishot or intro_duration > 8:
            log_callback("   🚀 Используем Multi-Shot генератор")
            
            multishot_gen = get_multishot_generator()
            multishot_gen.veo.model = veo_model
            
            raw_video_path = multishot_gen.generate_extended_intro(
                theme=theme,
                intro_text=veo_prompt_text,
                reference_image=reference_images[0],
                api_key=api_key,
                output_dir=output_dir,
                target_duration=intro_duration,
                resolution=quality,
                aspect_ratio=aspect_ratio,
                mood=mood,
                num_actions=num_actions,
                log_callback=log_callback,
                unique_id=unique_id  # Prevent file conflicts in parallel generation
            )
        else:
            # Legacy single-shot generation
            log_callback("   📹 Используем стандартный генератор")
            
            video_prompt = _generate_simple_veo3_prompt(
                theme=theme,
                intro_text=veo_prompt_text,
                style=mood,
                api_key=api_key,
                log_callback=log_callback
            )
            
            log_callback(f"   🎬 Veo 3 промпт: {video_prompt[:150]}...")
            
            veo3_gen = Veo3Generator(model=veo_model)
            raw_video_path = output_dir / f"veo3_intro_raw_{unique_id}.mp4"
            
            raw_video_path = veo3_gen.generate_video_from_images(
                reference_images=reference_images,
                prompt=video_prompt,
                api_key=api_key,
                output_path=str(raw_video_path),
                duration=clip_duration,
                resolution=quality,
                fps=24,
                aspect_ratio=aspect_ratio,
                theme=theme,
                log_callback=log_callback
            )
        
        if not raw_video_path or not Path(raw_video_path).exists():
            log_callback("   ❌ Veo 3 не смог сгенерировать видео")
            return None
        
        log_callback(f"   ✅ AI-видео сгенерировано: {Path(raw_video_path).stat().st_size / 1024 / 1024:.2f} MB")
        
        # Step 4: Remove audio from Veo 3 video
        log_callback("\n🔇 Шаг 4/5: Удаление аудио из Veo 3 видео...")
        
        silent_video_path = output_dir / f"veo3_intro_silent_{unique_id}.mp4"
        
        result_silent = remove_audio_from_video(
            input_video=str(raw_video_path),
            output_video=str(silent_video_path),
            log_callback=log_callback
        )
        
        if not result_silent or not Path(result_silent).exists():
            log_callback("   ⚠️ Не удалось удалить аудио, используем оригинал")
            result_silent = str(raw_video_path)
        
        log_callback("\n✅ Шаг 5/5: AI-интро готово!")
        log_callback(f"   📁 Путь: {result_silent}")
        log_callback("=" * 60)
        
        return result_silent
        
    except Exception as e:
        log_callback(f"❌ Критическая ошибка генерации AI-интро: {str(e)[:200]}")
        import traceback
        log_callback(f"   Traceback: {traceback.format_exc()[:500]}")
        return None


def get_available_camera_movements() -> Dict[str, str]:
    """Get available camera movements for UI"""
    return {key: val["name"] for key, val in CAMERA_MOVEMENTS.items()}


def get_available_moods() -> List[str]:
    """Get available moods for UI"""
    return list(CAMERA_SEQUENCES.keys())
