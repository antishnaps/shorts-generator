from core.settings_schema import (
    normalize_audio_settings,
    normalize_subtitle_settings,
    normalize_video_settings,
    normalize_youtube_mixer_settings,
)


def test_subtitle_settings_clamp_and_keep_new_fields():
    result = normalize_subtitle_settings({
        "style_preset": "hormozi",
        "font_color": "yellow",
        "highlight_color": "bad-color",
        "position": "Arriba",
        "max_words_per_subtitle": 99,
        "max_chars_per_subtitle": 2,
        "timing_offset": 9,
        "uppercase": "yes",
    })
    assert result["style_preset"] == "hormozi"
    assert result["font_color"] == "#FFFF00"
    assert result["highlight_color"] == "#00E5FF"
    assert result["position"] == "top"
    assert result["max_words_per_subtitle"] == 8
    assert result["max_chars_per_subtitle"] == 8
    assert result["timing_offset"] == 2.0
    assert result["uppercase"] is True


def test_unknown_subtitle_preset_falls_back_to_tiktok():
    assert normalize_subtitle_settings({"style_preset": "wat"})["style_preset"] == "tiktok"


def test_subtitle_defaults_are_retention_ready():
    result = normalize_subtitle_settings({})
    assert result["enabled"] is True
    assert result["style_preset"] == "tiktok"
    assert result["animated_subtitle"] is True
    assert result["font_path"] == "Arial Black"
    assert result["max_words_per_subtitle"] == 2
    assert result["uppercase"] is True
    assert result["subtitle_animation"] == "auto"
    assert result["subtitle_typewriter_cps"] == 18


def test_subtitle_animation_settings_are_validated_and_backward_compatible():
    typewriter = normalize_subtitle_settings({
        "subtitle_animation": "typing",
        "subtitle_fade_in_ms": 9999,
        "subtitle_fade_out_ms": -1,
        "subtitle_typewriter_cps": 999,
    })
    legacy = normalize_subtitle_settings({"animated_subtitle": True})
    disabled = normalize_subtitle_settings({"animated_subtitle": False})

    assert typewriter["subtitle_animation"] == "typewriter"
    assert typewriter["subtitle_fade_in_ms"] == 1200
    assert typewriter["subtitle_fade_out_ms"] == 0
    assert typewriter["subtitle_typewriter_cps"] == 60
    assert legacy["subtitle_animation"] == "word_focus"
    assert disabled["subtitle_animation"] == "none"
    assert disabled["animated_subtitle"] is False


def test_atmospheric_video_effect_settings_are_bounded():
    result = normalize_video_settings({
        "video_effect": "VHS",
        "video_effect_intensity": 3,
        "video_effect_probability": -2,
    })
    invalid = normalize_video_settings({"video_effect": "unknown"})

    assert result["video_effect"] == "vhs"
    assert result["video_effect_intensity"] == 1.0
    assert result["video_effect_probability"] == 0.0
    assert invalid["video_effect"] == "auto"


def test_localized_subtitle_positions_are_canonicalized():
    assert normalize_subtitle_settings({"position": "Вверху"})["position"] == "top"
    assert normalize_subtitle_settings({"position": "中心"})["position"] == "center"
    assert normalize_subtitle_settings({"position": "Inferior"})["position"] == "bottom"


def test_edge_is_a_supported_audio_provider():
    assert normalize_audio_settings({"provider": "EDGE"})["provider"] == "edge"


def test_music_volume_accepts_percent_or_fraction_values():
    assert normalize_audio_settings({"music_volume": 9})["music_volume"] == 0.09
    assert normalize_audio_settings({"music_volume": 0.09})["music_volume"] == 0.09


def test_audio_empty_voice_falls_back_to_default():
    assert normalize_audio_settings({"voice": ""})["voice"] == "Kore"
    assert normalize_audio_settings({"voice": None})["voice"] == "Kore"
    assert normalize_audio_settings({"voice": "ru-RU-SvetlanaNeural friendly"})["voice"] == "ru-RU-SvetlanaNeural"


def test_audio_auto_fit_is_always_enabled():
    assert normalize_audio_settings({"auto_fit_duration": False})["auto_fit_duration"] is True


def test_visual_source_settings_are_backward_compatible():
    result = normalize_youtube_mixer_settings({"enabled": True, "video_ratio": 0.4})
    assert result["source_mode"] == "youtube"
    assert result["enable_youtube"] is True
    assert result["enable_local_videos"] is False
    assert result["video_ratio"] == 0.4


def test_legacy_source_mode_expands_to_coherent_flags():
    cases = {
        "none": (False, False, False, False, False),
        "youtube": (True, False, False, False, False),
        "local": (False, True, False, False, False),
        "fallback": (False, False, True, False, False),
        "smart_mix": (True, True, False, False, False),
    }
    for source_mode, expected_flags in cases.items():
        result = normalize_youtube_mixer_settings({"enabled": True, "source_mode": source_mode})
        assert result["source_mode"] == source_mode
        assert (
            result["enable_youtube"],
            result["enable_local_videos"],
            result["enable_pexels"],
            result["enable_pixabay_videos"],
            result["enable_wikimedia_videos"],
        ) == expected_flags


def test_visual_source_settings_validate_new_fields():
    result = normalize_youtube_mixer_settings({
        "source_mode": "smart_mix",
        "enable_pexels": "true",
        "enable_pixabay_videos": 1,
        "enable_wikimedia_videos": "true",
        "youtube_data_api_key": "youtube-key",
        "max_stock_clips": 1000,
    })
    assert result["source_mode"] == "smart_mix"
    assert result["enable_pexels"] is True
    assert result["enable_pixabay_videos"] is True
    assert result["enable_wikimedia_videos"] is True
    assert result["youtube_data_api_key"] == "youtube-key"
    assert result["max_stock_clips"] == 100


def test_visual_relevance_defaults_are_bounded_and_universal():
    result = normalize_youtube_mixer_settings({"enabled": True})
    assert result["allow_clip_reuse_when_insufficient"] is True
    assert result["semantic_fallback_retries"] == 4
    assert result["semantic_fallback_max_seconds"] == 360
    assert result["semantic_fallback_queries_per_attempt"] == 4
    assert result["visual_relevance_enabled"] is True
    assert result["visual_relevance_soft_backfill"] is True
    assert result["visual_relevance_frames_per_clip"] == 3
    assert result["visual_relevance_max_api_calls"] == 3
    assert result["visual_relevance_uncertain_score"] < result["visual_relevance_accept_score"]

    repaired = normalize_youtube_mixer_settings({
        "visual_relevance_frames_per_clip": 50,
        "visual_relevance_max_api_calls": -2,
        "visual_relevance_accept_score": 45,
        "visual_relevance_uncertain_score": 80,
    })
    assert repaired["visual_relevance_frames_per_clip"] == 5
    assert repaired["visual_relevance_max_api_calls"] == 0
    assert repaired["visual_relevance_uncertain_score"] == 45


def test_local_only_flags_override_stale_youtube_mode():
    result = normalize_youtube_mixer_settings({
        "enabled": True,
        "source_mode": "youtube",
        "enable_youtube": False,
        "enable_local_videos": True,
        "enable_pexels": False,
        "enable_pixabay_videos": False,
    })
    assert result["source_mode"] == "local"
    assert result["enable_youtube"] is False


def test_youtube_clip_duration_range_is_repaired():
    result = normalize_youtube_mixer_settings({
        "clip_min_duration": 12,
        "clip_max_duration": 3,
    })
    assert result["clip_min_duration"] == 12
    assert result["clip_max_duration"] == 12


def test_source_flags_override_every_stale_mode_value():
    cases = [
        ({"enable_youtube": False, "enable_local_videos": False, "enable_pexels": False}, "none"),
        ({"enable_youtube": True, "enable_local_videos": False, "enable_pexels": False}, "youtube"),
        ({"enable_youtube": False, "enable_local_videos": True, "enable_pexels": False}, "local"),
        ({"enable_youtube": False, "enable_local_videos": False, "enable_pexels": True}, "fallback"),
        ({"enable_youtube": True, "enable_local_videos": True, "enable_pexels": False}, "smart_mix"),
    ]
    for flags, expected_mode in cases:
        for stale_mode in ("youtube", "local", "fallback", "smart_mix", "garbage"):
            result = normalize_youtube_mixer_settings({"source_mode": stale_mode, **flags})
            assert result["source_mode"] == expected_mode
