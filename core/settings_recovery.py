"""Helpers that keep a meaningful GUI configuration from being replaced by defaults."""

from __future__ import annotations

from typing import Any, Mapping


def _user_settings(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(config, Mapping):
        return {}
    settings = config.get("user_settings", {})
    return settings if isinstance(settings, Mapping) else {}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_meaningful_generation_config(config: Mapping[str, Any] | None) -> bool:
    """Return whether a config contains an established generation workflow."""
    settings = _user_settings(config)
    mixer = settings.get("youtube_mixer_settings", {})
    publisher = settings.get("youtube_publish_settings", {})
    return bool(
        str(settings.get("theme", "") or "").strip()
        or _as_int(settings.get("num_videos", 5), 5) > 5
        or (isinstance(mixer, Mapping) and mixer.get("enabled") is True)
        or (isinstance(publisher, Mapping) and publisher.get("enabled") is True)
    )


def looks_like_pristine_defaults(config: Mapping[str, Any] | None) -> bool:
    """Detect the default GUI state that previously overwrote user settings."""
    settings = _user_settings(config)
    video = settings.get("video_settings", {})
    mixer = settings.get("youtube_mixer_settings", {})
    publisher = settings.get("youtube_publish_settings", {})
    return bool(
        not str(settings.get("theme", "") or "").strip()
        and _as_int(settings.get("num_videos", 5), 5) == 5
        and settings.get("use_ai_image_generation", True) is True
        and isinstance(video, Mapping)
        and video.get("enable_animation", False) is False
        and isinstance(mixer, Mapping)
        and mixer.get("enabled", False) is False
        and isinstance(publisher, Mapping)
        and publisher.get("enabled", False) is False
    )


def should_preserve_previous_config(
    previous: Mapping[str, Any] | None,
    candidate: Mapping[str, Any] | None,
) -> bool:
    """Block an accidental meaningful-to-default configuration downgrade."""
    return is_meaningful_generation_config(previous) and looks_like_pristine_defaults(candidate)
