"""Compatibility imports for the canonical YouTube Data API implementation."""

from .search import _get_video_details, search_youtube_api
from .utils import _parse_duration


__all__ = ["_get_video_details", "_parse_duration", "search_youtube_api"]
