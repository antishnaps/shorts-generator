#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from dataclasses import dataclass, asdict
from typing import Any, Dict

from core.subtitle_styles import normalize_hex_color, normalize_subtitle_position


# The GUI exposes hours, so the schema must not silently turn a multi-hour
# request into a one-hour video. Three hours is the supported and tested
# long-form ceiling; values above it are clamped deliberately in one place.
MAX_VIDEO_DURATION_SECONDS = 3 * 60 * 60


def _to_int(value: Any, default: int, min_value: int = None, max_value: int = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if min_value is not None:
        result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result


def _to_float(value: Any, default: float, min_value: float = None, max_value: float = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if min_value is not None:
        result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result


def _to_volume_fraction(value: Any, default: float = 0.25) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if result > 1.0:
        result = result / 100.0
    return max(0.0, min(1.0, result))


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on", "да"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "нет"}:
            return False
    if value is None:
        return default
    return bool(value)


def _to_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _first_token(value: Any, default: str = "") -> str:
    text = _to_str(value, default).strip()
    return text.split()[0] if text else default


@dataclass
class VideoSettings:
    width: int = 1080
    height: int = 1920
    resolution: str = "1080x1920 (FHD Вертикальное)"
    fps: int = 60
    duration: float = 20.0
    shot_min_duration: float = 1.5
    shot_max_duration: float = 4.0
    enable_animation: bool = True
    animation_type: str = "mix"
    animation_speed: int = 50
    enable_transitions: bool = True
    transition_duration: float = 0.3
    transition_style: str = "cinematic"
    transition_frequency: float = 0.6
    cinematic_polish: bool = True
    music_volume: float = 0.25
    start_with_images: bool = True
    first_shot_from_pool: bool = True
    burn_first_shot_title: bool = True
    first_shot_title_custom: str = ""
    debug_mode: bool = False
    # ⚡ Glitch transitions
    glitch_enabled: bool = False
    glitch_style: str = "random"
    glitch_duration: float = 0.4
    glitch_frequency: float = 0.5
    glitch_intensity: float = 0.65
    # Asset-free atmospheric overlays. ``auto`` is deterministic per output
    # video and intentionally subtle/occasional.
    video_effect: str = "auto"
    video_effect_intensity: float = 0.30
    video_effect_probability: float = 0.40


@dataclass
class SubtitleSettings:
    enabled: bool = True
    font_size: int = 68
    font_path: str = "Arial Black"
    position: str = "center"
    animated_subtitle: bool = True
    subtitle_animation: str = "auto"
    subtitle_fade_in_ms: int = 180
    subtitle_fade_out_ms: int = 220
    subtitle_typewriter_cps: int = 18
    style_preset: str = "tiktok"
    font_color: str = "#FFFFFF"
    highlight_color: str = "#00E5FF"
    outline_color: str = "#000000"
    outline_width: int = 7
    shadow_depth: int = 5
    bg_color: str = "#000000"
    bg_opacity: float = 0.0
    max_words_per_subtitle: int = 2
    max_chars_per_subtitle: int = 28
    timing_offset: float = 0.0
    uppercase: bool = True


@dataclass
class AudioSettings:
    enabled: bool = True
    provider: str = "gemini"
    voice: str = "Kore"
    speech_speed: float = 1.0
    edge_pitch_hz: int = 0
    edge_volume_percent: int = 0
    auto_fit_duration: bool = True
    music_volume: float = 0.25
    use_music: bool = True


@dataclass
class OverlaySettings:
    enabled: bool = True
    position: str = "Внизу справа"
    margin: int = 50
    fullscreen: bool = False


@dataclass
class YouTubeMixerSettings:
    enabled: bool = False
    video_ratio: float = 0.6
    youtube_percent: float = 60.0
    clip_min_duration: float = 6.0
    clip_max_duration: float = 8.0
    clip_pool_headroom: float = 1.5
    custom_videos_folder: str = None
    source_mode: str = "youtube"
    enable_youtube: bool = True
    enable_local_videos: bool = True
    enable_pexels: bool = False
    enable_pixabay_videos: bool = False
    enable_wikimedia_videos: bool = False
    pexels_api_key: str = None
    pixabay_api_key: str = None
    youtube_data_api_key: str = None
    min_resolution: int = 720
    allow_clip_reuse_when_insufficient: bool = True
    semantic_fallback_retries: int = 4
    semantic_fallback_max_seconds: int = 360
    semantic_fallback_queries_per_attempt: int = 4
    max_stock_clips: int = 24
    source_rejection_buffer: float = 1.85
    source_budget_max_minutes: float = 75.0
    download_timeout_seconds: int = 0
    download_timeout_min_seconds: int = 180
    download_timeout_max_seconds: int = 900
    visual_relevance_enabled: bool = True
    visual_brief_max_segments: int = 14
    visual_brief_max_queries: int = 8
    visual_search_min_context_queries: int = 2
    visual_relevance_frames_per_clip: int = 3
    visual_relevance_batch_size: int = 4
    visual_relevance_max_api_calls: int = 3
    visual_relevance_max_clips: int = 14
    visual_relevance_accept_score: int = 52
    visual_relevance_uncertain_score: int = 30
    visual_relevance_cached_min_ratio: float = 0.65
    visual_relevance_soft_backfill: bool = True


def normalize_video_settings(settings: Dict[str, Any] = None) -> Dict[str, Any]:
    settings = settings or {}
    # Валидация glitch_style
    from core.glitch_transitions import get_glitch_styles
    valid_glitch_styles = get_glitch_styles()
    raw_glitch_style = _to_str(settings.get('glitch_style'), 'random')
    glitch_style = raw_glitch_style if raw_glitch_style in valid_glitch_styles else 'random'

    transition_style = _to_str(settings.get('transition_style'), 'cinematic').lower()
    if transition_style not in {'cinematic', 'energetic', 'smooth', 'dramatic'}:
        transition_style = 'cinematic'

    video_effect = _to_str(settings.get('video_effect'), 'auto').strip().lower()
    if video_effect not in {
        'none', 'auto', 'cinematic_dust', 'film_grain', 'old_film',
        'vhs', 'soft_bloom', 'light_leak',
    }:
        video_effect = 'auto'

    result = VideoSettings(
        width=_to_int(settings.get('width'), 1080, 64, 7680),
        height=_to_int(settings.get('height'), 1920, 64, 7680),
        resolution=_to_str(settings.get('resolution'), '1080x1920 (FHD Вертикальное)'),
        fps=_to_int(settings.get('fps'), 60, 1, 120),
        duration=_to_float(
            settings.get('duration'),
            20.0,
            1.0,
            float(MAX_VIDEO_DURATION_SECONDS),
        ),
        shot_min_duration=_to_float(settings.get('shot_min_duration'), 1.5, 0.2, 120.0),
        shot_max_duration=_to_float(settings.get('shot_max_duration'), 4.0, 0.2, 120.0),
        enable_animation=_to_bool(settings.get('enable_animation'), True),
        animation_type=_to_str(settings.get('animation_type'), 'mix'),
        animation_speed=_to_int(settings.get('animation_speed'), 50, 1, 100),
        enable_transitions=_to_bool(settings.get('enable_transitions'), True),
        transition_duration=_to_float(settings.get('transition_duration'), 0.3, 0.0, 3.0),
        transition_style=transition_style,
        transition_frequency=_to_float(settings.get('transition_frequency'), 0.6, 0.0, 1.0),
        cinematic_polish=_to_bool(settings.get('cinematic_polish'), True),
        music_volume=_to_volume_fraction(settings.get('music_volume'), 0.25),
        start_with_images=_to_bool(settings.get('start_with_images'), True),
        first_shot_from_pool=_to_bool(settings.get('first_shot_from_pool'), True),
        burn_first_shot_title=_to_bool(settings.get('burn_first_shot_title'), True),
        first_shot_title_custom=_to_str(settings.get('first_shot_title_custom'), '').strip(),
        debug_mode=_to_bool(settings.get('debug_mode'), False),
        # ⚡ Glitch
        glitch_enabled=_to_bool(settings.get('glitch_enabled'), False),
        glitch_style=glitch_style,
        glitch_duration=_to_float(settings.get('glitch_duration'), 0.4, 0.1, 2.0),
        glitch_frequency=_to_float(settings.get('glitch_frequency'), 0.5, 0.0, 1.0),
        glitch_intensity=_to_float(settings.get('glitch_intensity'), 0.65, 0.1, 1.0),
        video_effect=video_effect,
        video_effect_intensity=_to_float(settings.get('video_effect_intensity'), 0.30, 0.0, 1.0),
        video_effect_probability=_to_float(settings.get('video_effect_probability'), 0.40, 0.0, 1.0),
    )
    if result.shot_max_duration < result.shot_min_duration:
        result.shot_max_duration = result.shot_min_duration
    return asdict(result)


def normalize_subtitle_settings(settings: Dict[str, Any] = None) -> Dict[str, Any]:
    settings = settings or {}
    preset = _to_str(settings.get('style_preset'), 'tiktok').lower()
    if preset not in {'clean', 'tiktok', 'hormozi', 'boxed', 'cinema', 'minimal'}:
        preset = 'tiktok'
    animated = _to_bool(settings.get('animated_subtitle'), True)
    if 'subtitle_animation' in settings:
        animation = _to_str(settings.get('subtitle_animation'), 'auto').strip().lower()
    elif 'animated_subtitle' in settings:
        animation = 'word_focus' if animated else 'none'
    else:
        animation = 'auto'
    animation_aliases = {
        'animated': 'word_focus', 'highlight': 'word_focus', 'word': 'word_focus',
        'typing': 'typewriter', 'type': 'typewriter', 'off': 'none', 'static': 'none',
    }
    animation = animation_aliases.get(animation, animation)
    if animation not in {'auto', 'word_focus', 'fade', 'typewriter', 'none'}:
        animation = 'auto'
    return asdict(SubtitleSettings(
        enabled=_to_bool(settings.get('enabled'), True),
        font_size=_to_int(settings.get('font_size'), 68, 8, 200),
        font_path=_to_str(settings.get('font_path'), 'Arial Black'),
        position=normalize_subtitle_position(settings.get('position'), 'center'),
        animated_subtitle=animation != 'none',
        subtitle_animation=animation,
        subtitle_fade_in_ms=_to_int(settings.get('subtitle_fade_in_ms'), 180, 0, 1200),
        subtitle_fade_out_ms=_to_int(settings.get('subtitle_fade_out_ms'), 220, 0, 1200),
        subtitle_typewriter_cps=_to_int(settings.get('subtitle_typewriter_cps'), 18, 4, 60),
        style_preset=preset,
        font_color=normalize_hex_color(settings.get('font_color'), '#FFFFFF'),
        highlight_color=normalize_hex_color(settings.get('highlight_color'), '#00E5FF'),
        outline_color=normalize_hex_color(settings.get('outline_color'), '#000000'),
        outline_width=_to_int(settings.get('outline_width'), 7, 0, 12),
        shadow_depth=_to_int(settings.get('shadow_depth'), 5, 0, 12),
        bg_color=normalize_hex_color(settings.get('bg_color'), '#000000'),
        bg_opacity=_to_float(settings.get('bg_opacity'), 0.0, 0.0, 1.0),
        max_words_per_subtitle=_to_int(settings.get('max_words_per_subtitle'), 2, 1, 8),
        max_chars_per_subtitle=_to_int(settings.get('max_chars_per_subtitle'), 28, 8, 80),
        timing_offset=_to_float(settings.get('timing_offset'), 0.0, -2.0, 2.0),
        uppercase=_to_bool(settings.get('uppercase'), True),
    ))


def normalize_audio_settings(settings: Dict[str, Any] = None) -> Dict[str, Any]:
    settings = settings or {}
    provider = _to_str(settings.get('provider'), 'gemini').lower()
    if provider not in {'gemini', 'edge'}:
        provider = 'edge'
    return asdict(AudioSettings(
        enabled=_to_bool(settings.get('enabled'), True),
        provider=provider,
        voice=_first_token(settings.get('voice'), 'Kore'),
        speech_speed=_to_float(settings.get('speech_speed'), 1.0, 0.5, 2.0),
        edge_pitch_hz=_to_int(settings.get('edge_pitch_hz'), 0, -30, 30),
        edge_volume_percent=_to_int(settings.get('edge_volume_percent'), 0, -30, 30),
        auto_fit_duration=True,
        music_volume=_to_volume_fraction(settings.get('music_volume'), 0.25),
        use_music=_to_bool(settings.get('use_music'), True),
    ))


def normalize_overlay_settings(settings: Dict[str, Any] = None) -> Dict[str, Any]:
    settings = settings or {}
    return asdict(OverlaySettings(
        enabled=_to_bool(settings.get('enabled'), True),
        position=_to_str(settings.get('position'), 'Внизу справа'),
        margin=_to_int(settings.get('margin'), 50, 0, 1000),
        fullscreen=_to_bool(settings.get('fullscreen'), False),
    ))


def normalize_youtube_mixer_settings(settings: Dict[str, Any] = None) -> Dict[str, Any]:
    settings = settings or {}
    video_ratio = _to_float(settings.get('video_ratio'), 0.6, 0.0, 1.0)
    youtube_percent = _to_float(settings.get('youtube_percent'), video_ratio * 100.0, 0.0, 100.0)
    enable_youtube = _to_bool(settings.get('enable_youtube'), True)
    enable_local_videos = _to_bool(settings.get('enable_local_videos'), True)
    enable_pexels = _to_bool(settings.get('enable_pexels'), False)
    enable_pixabay_videos = _to_bool(settings.get('enable_pixabay_videos'), False)
    enable_wikimedia_videos = _to_bool(settings.get('enable_wikimedia_videos'), False)
    source_flags_explicit = any(
        key in settings
        for key in (
            'enable_youtube', 'enable_local_videos', 'enable_pexels',
            'enable_pixabay_videos', 'enable_wikimedia_videos',
        )
    )
    online_stock_enabled = enable_pexels or enable_pixabay_videos or enable_wikimedia_videos
    if not source_flags_explicit:
        source_mode = _to_str(settings.get('source_mode'), 'youtube').lower()
        if source_mode not in {'youtube', 'local', 'fallback', 'smart_mix', 'none'}:
            source_mode = 'youtube'
        # Legacy configs used to store only source_mode. Keep those snapshots
        # coherent so "local" does not silently keep YouTube enabled via the
        # dataclass defaults.
        if source_mode == 'none':
            enable_youtube = False
            enable_local_videos = False
            enable_pexels = False
            enable_pixabay_videos = False
            enable_wikimedia_videos = False
        elif source_mode == 'youtube':
            enable_youtube = True
            enable_local_videos = False
            enable_pexels = False
            enable_pixabay_videos = False
            enable_wikimedia_videos = False
        elif source_mode == 'local':
            enable_youtube = False
            enable_local_videos = True
            enable_pexels = False
            enable_pixabay_videos = False
            enable_wikimedia_videos = False
        elif source_mode == 'fallback':
            enable_youtube = False
            enable_local_videos = False
            enable_pexels = True
            enable_pixabay_videos = False
            enable_wikimedia_videos = False
        elif source_mode == 'smart_mix':
            enable_youtube = True
            enable_local_videos = True
            enable_pexels = False
            enable_pixabay_videos = False
            enable_wikimedia_videos = False
    elif not any((enable_youtube, enable_local_videos, online_stock_enabled)):
        source_mode = 'none'
    elif online_stock_enabled and (enable_youtube or enable_local_videos):
        source_mode = 'smart_mix'
    elif online_stock_enabled:
        source_mode = 'fallback'
    elif enable_youtube and enable_local_videos:
        source_mode = 'smart_mix'
    elif enable_local_videos:
        source_mode = 'local'
    else:
        source_mode = 'youtube'
    result = YouTubeMixerSettings(
        enabled=_to_bool(settings.get('enabled'), False),
        video_ratio=video_ratio,
        youtube_percent=youtube_percent,
        clip_min_duration=_to_float(settings.get('clip_min_duration'), 6.0, 0.5, 60.0),
        clip_max_duration=_to_float(settings.get('clip_max_duration'), 8.0, 0.5, 120.0),
        clip_pool_headroom=_to_float(settings.get('clip_pool_headroom'), 1.5, 1.0, 2.0),
        custom_videos_folder=settings.get('custom_videos_folder') or None,
        source_mode=source_mode,
        enable_youtube=enable_youtube,
        enable_local_videos=enable_local_videos,
        enable_pexels=enable_pexels,
        enable_pixabay_videos=enable_pixabay_videos,
        enable_wikimedia_videos=enable_wikimedia_videos,
        pexels_api_key=settings.get('pexels_api_key') or None,
        pixabay_api_key=settings.get('pixabay_api_key') or None,
        youtube_data_api_key=settings.get('youtube_data_api_key') or None,
        min_resolution=_to_int(settings.get('min_resolution'), 720, 480, 1080),
        allow_clip_reuse_when_insufficient=_to_bool(
            settings.get(
                'allow_clip_reuse_when_insufficient',
                settings.get('emergency_visual_fallback_enabled'),
            ),
            True,
        ),
        semantic_fallback_retries=_to_int(settings.get('semantic_fallback_retries'), 4, 0, 8),
        semantic_fallback_max_seconds=_to_int(
            settings.get('semantic_fallback_max_seconds'), 360, 30, 900
        ),
        semantic_fallback_queries_per_attempt=_to_int(
            settings.get('semantic_fallback_queries_per_attempt'), 4, 2, 8
        ),
        max_stock_clips=_to_int(settings.get('max_stock_clips'), 24, 1, 100),
        source_rejection_buffer=_to_float(settings.get('source_rejection_buffer'), 1.85, 1.1, 4.0),
        source_budget_max_minutes=_to_float(settings.get('source_budget_max_minutes'), 75.0, 5.0, 240.0),
        download_timeout_seconds=_to_int(settings.get('download_timeout_seconds'), 0, 0, 1800),
        download_timeout_min_seconds=_to_int(settings.get('download_timeout_min_seconds'), 180, 60, 900),
        download_timeout_max_seconds=_to_int(settings.get('download_timeout_max_seconds'), 900, 180, 2400),
        visual_relevance_enabled=_to_bool(settings.get('visual_relevance_enabled'), True),
        visual_brief_max_segments=_to_int(settings.get('visual_brief_max_segments'), 14, 3, 30),
        visual_brief_max_queries=_to_int(settings.get('visual_brief_max_queries'), 8, 2, 20),
        visual_search_min_context_queries=_to_int(settings.get('visual_search_min_context_queries'), 2, 0, 8),
        visual_relevance_frames_per_clip=_to_int(settings.get('visual_relevance_frames_per_clip'), 3, 2, 5),
        visual_relevance_batch_size=_to_int(settings.get('visual_relevance_batch_size'), 4, 1, 6),
        visual_relevance_max_api_calls=_to_int(settings.get('visual_relevance_max_api_calls'), 3, 0, 10),
        visual_relevance_max_clips=_to_int(settings.get('visual_relevance_max_clips'), 14, 2, 60),
        visual_relevance_accept_score=_to_int(settings.get('visual_relevance_accept_score'), 52, 30, 90),
        visual_relevance_uncertain_score=_to_int(settings.get('visual_relevance_uncertain_score'), 30, 0, 90),
        visual_relevance_cached_min_ratio=_to_float(settings.get('visual_relevance_cached_min_ratio'), 0.65, 0.0, 1.0),
        visual_relevance_soft_backfill=_to_bool(
            settings.get('visual_relevance_soft_backfill'), True
        ),
    )
    if result.clip_max_duration < result.clip_min_duration:
        result.clip_max_duration = result.clip_min_duration
    if result.download_timeout_max_seconds < result.download_timeout_min_seconds:
        result.download_timeout_max_seconds = result.download_timeout_min_seconds
    if result.visual_relevance_uncertain_score > result.visual_relevance_accept_score:
        result.visual_relevance_uncertain_score = result.visual_relevance_accept_score
    return asdict(result)


def normalize_generation_settings(video_settings=None, subtitle_settings=None, audio_settings=None, overlay_settings=None, youtube_mixer_settings=None) -> Dict[str, Dict[str, Any]]:
    return {
        'video_settings': normalize_video_settings(video_settings),
        'subtitle_settings': normalize_subtitle_settings(subtitle_settings),
        'audio_settings': normalize_audio_settings(audio_settings),
        'overlay_settings': normalize_overlay_settings(overlay_settings),
        'youtube_mixer_settings': normalize_youtube_mixer_settings(youtube_mixer_settings),
    }
