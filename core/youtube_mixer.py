#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Video Mixer V2
Downloads videos from YouTube by topic and cuts clips for use in AI generator.

Features:
- Search videos by topic
- Download via yt-dlp (multiple clients, bypass blocks)
- Smart cutting (interesting moments, avoid faces/text)
- Caching for performance
- Integration with AI generator

V2 improvements:
- Blacklist channels/videos (youtube_blacklist.json)
- Smart search queries via AI (synonyms, related topics)
- Priority by quality (1080p > 720p, more views = better)
- Slideshow content detection (motion analysis)
- Batch cache (reuse clips within session)
"""

import os
import random
import subprocess
import hashlib
import shutil
import re
import math
from importlib.util import find_spec
from pathlib import Path
from typing import List, Dict, Optional, Callable, Tuple
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
import threading
import requests

# Lazy imports to avoid circular dependencies
# Модули YouTube будут импортироваться внутри функций при необходимости

# Импорт функций из модулей YouTube
from core.youtube.translation import (
    needs_english_search_translation,
    translate_to_english_ai,
)
from core.youtube.relevance import (
    calculate_relevance,
    calculate_topic_evidence,
    calculate_tiered_relevance,
    calculate_smart_video_count,
    check_semantic_relevance_with_gemini,
    has_meaningful_keyword_overlap,
    has_meaningful_topic_evidence,
    has_specific_route_evidence,
)
from core.youtube.smart_search import (
    generate_semantic_fallback_queries,
    generate_smart_queries,
    generate_visual_search_plan,
)
from core.youtube.query_planner import (
    SearchQuery,
    build_exhaustive_query_ladder,
    build_query_ladder,
    build_universal_queries,
    detect_query_language,
    extract_visual_core,
)
from core.youtube.blacklist import is_blacklisted, add_to_blacklist
from core.youtube.constants import USER_AGENTS
from core.youtube.cookies import (
    auto_export_youtube_cookies,
    ensure_youtube_cookies,
    refresh_youtube_cookies,
)
from core.youtube.cookie_normalizer import (
    create_isolated_cookie_snapshot,
    has_current_youtube_auth_cookies,
    normalize_cookies_file,
)
from core.youtube.download_policy import (
    YouTubeDownloadFailure,
    YouTubeFailureRegistry,
    build_youtube_client_strategies,
    build_youtube_js_runtime_options,
    classify_youtube_download_error,
    is_supported_ytdlp_version,
)
from core.youtube.proxy import describe_proxy, get_auto_proxy as _get_auto_proxy
from core.youtube.analysis import (
    detect_text_watermarks,
    find_interesting_moments,
    has_watermark,
    is_likely_slideshow_by_metadata,
    select_best_clip_time,
    is_slideshow_by_motion,
)
from core.youtube.utils import calculate_youtube_duration
from core.youtube.video_utils import repair_video_file, validate_video_file


def _dummy_log(msg):
    print(msg)


__all__ = [
    "YouTubeMixer",
    "_get_auto_proxy",
    "auto_export_youtube_cookies",
    "build_youtube_format_selector",
    "calculate_youtube_duration",
    "detect_text_watermarks",
    "estimate_youtube_download_timeout",
    "estimate_youtube_source_budget",
    "find_interesting_moments",
    "generate_semantic_fallback_queries",
    "is_slideshow_by_motion",
    "normalize_cookies_file",
    "select_best_clip_time",
]


YOUTUBE_DEFAULT_MIN_HEIGHT = 720
YOUTUBE_HARD_MIN_HEIGHT = 480
YOUTUBE_MAX_HEIGHT = 1080


def normalize_youtube_min_height(value, default: int = YOUTUBE_DEFAULT_MIN_HEIGHT) -> int:
    """Return a safe source-quality floor; never allow the old 144p/240p fallbacks."""
    try:
        height = int(value)
    except (TypeError, ValueError):
        height = int(default)
    return max(YOUTUBE_HARD_MIN_HEIGHT, min(YOUTUBE_MAX_HEIGHT, height))


def build_youtube_format_selector(
    min_height: int = YOUTUBE_DEFAULT_MIN_HEIGHT,
    *,
    prefer_hls: bool = False,
) -> str:
    """Prefer an editor-friendly HD video stream without downloading source audio.

    YouTube files are visual donors: clip extraction always passes ``-an`` and
    the generated narration replaces source audio.  Selecting video-only here
    avoids a second CDN transfer and an unnecessary merge.  A combined stream
    remains the final compatibility fallback, still inside the quality floor.
    """
    min_height = normalize_youtube_min_height(min_height)
    protocol_filter = "[protocol^=m3u8]" if prefer_hls else ""
    return (
        f"bv{protocol_filter}[height<={YOUTUBE_MAX_HEIGHT}][height>={min_height}]"
        f"[vcodec^=avc1][ext=mp4]/"
        f"bv{protocol_filter}[height<={YOUTUBE_MAX_HEIGHT}]"
        f"[height>={min_height}][ext=mp4]/"
        f"bv{protocol_filter}[height<={YOUTUBE_MAX_HEIGHT}]"
        f"[height>={min_height}]/"
        f"b{protocol_filter}[height<={YOUTUBE_MAX_HEIGHT}]"
        f"[height>={min_height}][ext=mp4]/"
        f"b{protocol_filter}[height<={YOUTUBE_MAX_HEIGHT}][height>={min_height}]"
    )


def probe_video_dimensions(video_path: str | Path) -> Optional[Tuple[int, int]]:
    """Read the first video stream dimensions, failing closed when they are unknown."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="ignore",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        values = result.stdout.strip().split(",")[:2]
        if len(values) != 2:
            return None
        width, height = (int(value) for value in values)
        if width <= 0 or height <= 0:
            return None
        return width, height
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def calculate_candidate_quality_score(video: Dict) -> int:
    """Rank relevant real footage above low-quality or mostly static candidates."""
    title = str(video.get("title") or "").lower()
    score = int(video.get("relevance") or 0) * 4
    score += min(40, max(0, int(video.get("topic_relevance") or 0)))
    semantic_relevance = int(video.get("semantic_relevance") or 0)
    if semantic_relevance >= 70:
        score += 24
    elif semantic_relevance >= 40:
        score += 12
    score += min(28, max(0, int(video.get("topic_evidence_score") or 0)) // 3)
    if video.get("specific_route_evidence"):
        score += 14
    tier = str(video.get("search_tier") or "exact").lower()
    score += {"exact": 32, "subject": 20, "context": 6, "broad": -28}.get(tier, -10)
    if (
        tier in {"context", "broad"}
        and int(video.get("topic_relevance") or 0) < 15
        and int(video.get("topic_evidence_score") or 0) < 25
        and semantic_relevance < 40
    ):
        score -= 40

    height = int(video.get("height") or 0)
    if height >= 1080:
        score += 45
    elif height >= 720:
        score += 30
    elif height and height < YOUTUBE_DEFAULT_MIN_HEIGHT:
        score -= 60

    view_count = int(video.get("view_count") or 0)
    if view_count >= 1_000_000:
        score += 25
    elif view_count >= 100_000:
        score += 15
    elif view_count >= 10_000:
        score += 8

    footage_terms = (
        "footage", "documentary", "nature", "wildlife", "walking", "drone",
        "4k", "hd", "making", "preparation", "process", "factory", "farm",
        "playing", "running", "driving", "tour", "pov",
    )
    static_terms = (
        "lyrics", "podcast", "audiobook", "audio only", "visualizer",
        "animation", "animated", "top 10", "facts about", "for kids", "для детей",
        "#shorts", "youtube shorts", "reaction", "explained", "talking head",
    )
    score += 12 if any(term in title for term in footage_terms) else 0
    score -= 35 if any(term in title for term in static_terms) else 0
    synthetic_terms = ("ai story", "aicat", "catai", "ai generated", "нейросет")
    score -= 80 if any(term in title for term in synthetic_terms) else 0

    duration = int(video.get("duration") or 0)
    # Ideal for clipping: 5-60 min. Short clips (<90s) have few interesting moments.
    # Very long (>3600s / 1h) are fine if we pick diverse moments.
    if duration >= 300 and duration <= 3600:
        score += 20   # Sweet spot: 5 min to 1 hour
    elif duration > 90 and duration < 300:
        score += 8    # Short but workable
    elif duration > 3600:
        score += 12   # Long: good variety but slower to process
    elif duration > 0 and duration <= 90:
        score -= 20   # Very short: few interesting moments

    live_status = str(video.get("live_status") or "").lower()
    if live_status in {"is_live", "is_upcoming", "post_live"}:
        score -= 100
    return score




def is_youtube_login_or_age_gate(error: str) -> bool:
    """Identify videos that need a signed-in/age-verified YouTube session."""
    message = str(error or "").lower()
    if is_youtube_bot_challenge(message):
        return False
    return any(
        marker in message
        for marker in (
            "sign in to confirm your age",
            "confirm your age",
            "age-restricted",
            "age restricted",
            "inappropriate for some users",
            "login_required",
        )
    )




def select_diverse_candidates(
    videos: List[Dict],
    limit: int,
    max_per_channel: int = 2,
    max_per_query: int = 3,
) -> List[Dict]:
    """Keep the best candidates while balancing channels and search routes."""
    if limit <= 0:
        return []
    selected = []
    deferred = []
    channel_counts = {}
    query_counts = {}
    for video in videos:
        channel_key = str(
            video.get("channel_id") or video.get("channel") or video.get("uploader") or ""
        ).strip().lower()
        query_key = str(video.get("search_query") or "").strip().lower()
        channel_full = channel_key and channel_counts.get(channel_key, 0) >= max_per_channel
        query_full = query_key and query_counts.get(query_key, 0) >= max_per_query
        if channel_full or query_full:
            deferred.append(video)
            continue
        selected.append(video)
        if channel_key:
            channel_counts[channel_key] = channel_counts.get(channel_key, 0) + 1
        if query_key:
            query_counts[query_key] = query_counts.get(query_key, 0) + 1
        if len(selected) >= limit:
            return selected
    for video in deferred:
        selected.append(video)
        if len(selected) >= limit:
            break
    return selected


def rotate_candidate_pool_for_session(
    videos: List[Dict],
    session_key: str,
    selection_size: int,
) -> List[Dict]:
    """Rotate only the high-quality pool so batch videos use different donors."""

    items = list(videos or [])
    key = str(session_key or "").strip()
    if len(items) < 2 or not key:
        return items
    pool_size = min(len(items), max(4, int(selection_size or 1) * 3))
    pool = items[:pool_size]
    digest = hashlib.sha256(key.encode("utf-8", errors="ignore")).digest()
    offset = int.from_bytes(digest[:4], "big") % pool_size
    return pool[offset:] + pool[:offset] + items[pool_size:]


def _clamp_number(value, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(minimum, min(maximum, number))


def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    return int(round(_clamp_number(value, default, minimum, maximum)))


def estimate_youtube_source_budget(
    total_clips_needed: int,
    target_duration_minutes: float = 0,
    min_clip_duration: float = 3.0,
    max_clip_duration: float = 8.0,
    batch_total_videos: int = 1,
    target_orientation: str = "any",
    mixer_settings: Optional[Dict] = None,
) -> Dict[str, float]:
    """Estimate source pool size and timeouts from the actual clip job."""
    settings = mixer_settings or {}
    clips_needed = _clamp_int(total_clips_needed, 1, 1, 2000)
    clip_min = _clamp_number(min_clip_duration, 3.0, 0.5, 120.0)
    clip_max = _clamp_number(max_clip_duration, max(clip_min, 8.0), clip_min, 180.0)
    avg_clip_seconds = (clip_min + clip_max) / 2.0
    requested_seconds = clips_needed * avg_clip_seconds
    batch_size = _clamp_int(batch_total_videos, 1, 1, 1000)

    rejection_factor = _clamp_number(
        settings.get("source_rejection_buffer"),
        1.85,
        1.1,
        4.0,
    )
    if clips_needed <= 6:
        rejection_factor = max(1.45, rejection_factor - 0.25)
    elif clips_needed >= 40:
        rejection_factor += min(0.6, math.log2(clips_needed / 20.0) * 0.18)

    orientation_factor = 1.25 if str(target_orientation or "any").lower() != "any" else 1.0
    batch_factor = 1.0 + min(1.25, math.log2(batch_size) / 4.0) if batch_size > 1 else 1.0
    variety_factor = 1.0 + min(0.7, clips_needed / 100.0)

    estimated_seconds = requested_seconds * rejection_factor * orientation_factor * batch_factor * variety_factor
    legacy_floor_seconds = max(0.0, _clamp_number(target_duration_minutes, 0, 0, 240) * 60.0)
    minimum_seconds = max(180.0, requested_seconds * 1.5, legacy_floor_seconds)
    maximum_minutes_default = 75.0 if batch_size > 1 else 45.0
    maximum_minutes = _clamp_number(
        settings.get("source_budget_max_minutes"),
        maximum_minutes_default,
        5.0,
        240.0,
    )
    target_seconds = max(minimum_seconds, estimated_seconds)
    target_duration = min(maximum_minutes, target_seconds / 60.0)

    clips_per_useful_source = max(2.0, min(5.0, 22.0 / max(avg_clip_seconds, 1.0)))
    min_videos = _clamp_int(
        math.ceil(clips_needed / clips_per_useful_source),
        2,
        1,
        16 if batch_size == 1 else 32,
    )
    max_videos = _clamp_int(
        math.ceil(min_videos * 1.8 + math.log2(max(batch_size, 1)) * 2.0),
        max(2, min_videos),
        min_videos,
        18 if batch_size == 1 else 50,
    )
    search_results = _clamp_int(
        max(calculate_smart_video_count(target_duration * 60, clips_needed, batch_size), max_videos * 2),
        max_videos * 2,
        2,
        80,
    )

    return {
        "target_duration_minutes": round(target_duration, 2),
        "requested_clip_seconds": round(requested_seconds, 1),
        "estimated_source_seconds": round(target_duration * 60.0, 1),
        "min_videos_to_try": min_videos,
        "max_videos_to_try": max_videos,
        "search_results": search_results,
        "avg_clip_seconds": round(avg_clip_seconds, 2),
    }


def estimate_youtube_download_timeout(
    video_info: Optional[Dict] = None,
    mixer_settings: Optional[Dict] = None,
    target_duration_minutes: float = 0,
) -> int:
    """Return a per-source download deadline that tolerates slow YouTube/network bursts."""
    settings = mixer_settings or {}
    configured = settings.get("download_timeout_seconds")
    configured_value = _clamp_int(configured, 0, 0, 1800) if configured not in (None, "") else 0
    if configured_value > 0:
        base_timeout = configured_value
    else:
        duration = 0.0
        if video_info:
            duration = _clamp_number(video_info.get("duration"), 0, 0, 21600)
        target_seconds = _clamp_number(target_duration_minutes, 0, 0, 240) * 60.0
        reference_duration = max(duration, min(target_seconds, 1800.0))
        base_timeout = int(150 + min(600, reference_duration * 0.22))
        height = int((video_info or {}).get("height") or 0)
        if height >= 1080:
            base_timeout += 90
        elif height >= 720:
            base_timeout += 45

    minimum = _clamp_int(settings.get("download_timeout_min_seconds"), 180, 60, 900)
    maximum = _clamp_int(settings.get("download_timeout_max_seconds"), 900, minimum, 2400)
    return max(minimum, min(maximum, int(base_timeout)))


def count_strong_topic_candidates(videos: List[Dict], relevance_theme: str) -> int:
    """Count candidates strong enough to skip expensive broad/context searches."""
    count = 0
    seen_ids = set()
    for video in videos:
        video_id = video.get("id") or video.get("url") or video.get("title")
        if video_id in seen_ids:
            continue
        seen_ids.add(video_id)
        tier = str(video.get("search_tier") or "exact").lower()
        if tier not in {"exact", "subject"}:
            continue
        if is_likely_slideshow_by_metadata(video.get("title", ""), video.get("channel", "")):
            continue
        if is_mismatched_media_title(video.get("title", ""), relevance_theme):
            continue
        scores = calculate_tiered_relevance(
            video_title=video.get("title", ""),
            theme=relevance_theme,
            search_query=video.get("search_query", relevance_theme),
            tier=tier,
            video_description=video.get("description", ""),
        )
        evidence = calculate_topic_evidence(
            video.get("title", ""),
            relevance_theme,
            video.get("description", ""),
        )
        if (
            scores["effective"] >= 55
            and (scores["topic"] >= 35 or int(evidence.get("score") or 0) >= 25)
        ):
            count += 1
    return count


_TITLE_BOILERPLATE = {
    "4k", "8k", "hd", "uhd", "official", "video", "videos", "footage",
    "documentary", "full", "episode", "part",
}


def title_fingerprint(title: str) -> str:
    """Normalize cosmetic title differences to detect likely reuploads."""
    words = re.findall(r"[a-zа-яё0-9]+", str(title or "").lower(), re.IGNORECASE)
    useful = [
        word for word in words
        if word not in _TITLE_BOILERPLATE and not re.fullmatch(r"20\d{2}", word)
    ]
    return " ".join(useful)


def matches_target_orientation(width: int, height: int, target_orientation: str) -> bool:
    """Reject square/vertical sources only when a horizontal source is required."""
    width = int(width or 0)
    height = int(height or 0)
    if not width or not height or target_orientation != "horizontal":
        return True
    return width / height >= 1.15


def interleave_clips_by_source(clip_paths: List[str], limit: int) -> List[str]:
    """Spread clips from different source videos across the final sequence."""
    if limit <= 0:
        return []
    groups: Dict[str, List[str]] = {}
    for clip_path in clip_paths:
        stem = Path(clip_path).stem
        source = re.sub(r"^clip_", "", stem)
        source = re.sub(r"_\d{4,8}_\d{3}(?:_[a-f0-9]{6})?$", "", source)
        groups.setdefault(source, []).append(clip_path)
    for clips in groups.values():
        random.shuffle(clips)

    result = []
    while groups and len(result) < limit:
        for source in list(groups):
            clips = groups[source]
            if clips:
                result.append(clips.pop())
                if len(result) >= limit:
                    break
            if not clips:
                groups.pop(source, None)
    return result


_MISMATCHED_MEDIA_GROUPS = (
    # NOTE: movie/film group
    ("movie", "film", "cinema", "trailer", "кино", "фильм", "трейлер"),
    (
        "music video", "official video", "official audio", "lyrics", "song",
        "music", "album", "record", "concert", "karaoke", "remix",
        "singing", "dancing", "песня", "клип", "музыка", "альбом", "концерт",
        "караоке", "ремикс", "песен", "поют", "танцуют",
    ),
    (
        "animation", "animated", "cartoon", "for kids", "nursery", "episode",
        "анимация", "анимации", "мультфильм", "для детей", "серия", "эпизод",
    ),
    ("podcast", "audiobook", "audio only", "подкаст", "аудиокнига"),
    ("ai story", "ai cat", "aicat", "catai", "ai generated", "not ai", "нейросет"),
    (
        "game", "gameplay", "walkthrough", "playthrough", "let's play", "lets play",
        "игра", "геймплей", "прохождение", "обзор игры", "игру"
    ),
)
def _looks_like_named_media_topic(video_title: str, theme: str) -> bool:
    """Allow a named film/game/show when its title is grounded in the topic.

    This replaces the old ever-growing dictionary of game names.  The rule is
    language- and subject-agnostic: a one-token named topic must occur in the
    candidate, while a longer topic needs strong multi-token coverage.
    """

    raw_theme = str(theme or "").strip()
    topic_core = extract_visual_core(raw_theme, max_words=8)
    topic_terms = re.findall(r"[^\W_]{2,}", topic_core.casefold(), re.UNICODE)
    if not topic_terms:
        return False
    evidence = calculate_topic_evidence(video_title, topic_core)
    overlap_count = int(evidence.get("title_overlap_count") or 0)
    if len(topic_terms) == 1:
        # A lowercase one-word topic such as "cats" is fundamentally
        # ambiguous and must not silently become "Cats: The Video Game".
        # Preserve one-word media names only when the user supplied a proper-
        # name signal; otherwise they can make the intent explicit with
        # "game"/"film". This is universal and contains no title dictionary.
        raw_words = re.findall(r"[^\W_]+", raw_theme, re.UNICODE)
        proper_name_signal = bool(
            raw_words
            and raw_words[0][:1].isupper()
            and raw_theme != raw_theme.casefold()
        )
        return overlap_count == 1 and proper_name_signal
    return overlap_count >= 2 and float(evidence.get("title_coverage") or 0) >= 0.5


def is_mismatched_media_title(video_title: str, theme: str) -> bool:
    """Reject games, movies and other media formats unless the topic asks for them."""
    title = str(video_title or "").lower()
    raw_topic = str(theme or "")
    topic = raw_topic.lower()
    
    for group_index, group in enumerate(_MISMATCHED_MEDIA_GROUPS):
        if any(term in title for term in group) and not any(term in topic for term in group):
            # Named media topics often omit literal words such as "game" or
            # "movie".  Ground them by title overlap instead of a name list.
            if group_index in {0, 5} and _looks_like_named_media_topic(title, raw_topic):
                continue
            return True
    return False


def is_youtube_bot_challenge(error: str) -> bool:
    """Identify YouTube's bot challenge separately from age/login gates."""
    message = str(error or "").lower()
    return (
        ("sign in to confirm you" in message and "not a bot" in message)
        or "confirm you're not a bot" in message
        or "confirm you’re not a bot" in message
    )


_youtube_access_lock = threading.Lock()
_youtube_block_until = 0.0
_youtube_block_notice_logged = False
_youtube_block_cookie_signature = None
_youtube_block_reason = None
_youtube_failed_downloads = YouTubeFailureRegistry(max_entries=1024)
# Generator workers may each create a mixer.  Keep the total number of active
# yt-dlp downloads bounded process-wide, not merely inside each mixer.
_youtube_global_download_slots = threading.BoundedSemaphore(2)


def _youtube_cookie_signature():
    cookie_file = Path(__file__).parent.parent / "youtube_cookies.txt"
    try:
        stat = cookie_file.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def _youtube_connection_signature():
    """Fingerprint the local YouTube route without exposing credentials.

    A cookie refresh or an explicit proxy/VPN endpoint change should allow a
    previously failed ID to be negotiated again. A system-wide VPN cannot
    always be identified without an external request, so its failures remain
    bounded by the registry TTL rather than triggering a network probe here.
    """

    try:
        proxy_url = _get_auto_proxy(lambda _message: None) or "direct"
    except Exception:
        proxy_url = "direct"
    proxy_digest = hashlib.sha256(str(proxy_url).encode("utf-8")).hexdigest()[:16]
    return _youtube_cookie_signature(), proxy_digest


def reset_youtube_access_state() -> None:
    """Clear the process-wide YouTube access cooldown."""
    global _youtube_block_until, _youtube_block_notice_logged
    global _youtube_block_cookie_signature, _youtube_block_reason
    with _youtube_access_lock:
        _youtube_block_until = 0.0
        _youtube_block_notice_logged = False
        _youtube_block_cookie_signature = None
        _youtube_block_reason = None
    _youtube_failed_downloads.clear()



def youtube_access_temporarily_blocked() -> bool:
    """Return the process-wide direct YouTube cooldown state."""
    global _youtube_block_until, _youtube_block_notice_logged
    global _youtube_block_cookie_signature, _youtube_block_reason
    with _youtube_access_lock:
        current_signature = _youtube_cookie_signature()
        if (
            _youtube_block_until > time.monotonic()
            and current_signature != _youtube_block_cookie_signature
        ):
            _youtube_block_until = 0.0
            _youtube_block_notice_logged = False
            _youtube_block_cookie_signature = None
            _youtube_block_reason = None
            _youtube_failed_downloads.clear()
        return time.monotonic() < _youtube_block_until


def mark_youtube_bot_block(cooldown_seconds: int = 60) -> None:
    """Pause every direct YouTube workflow after a confirmed bot challenge."""
    global _youtube_block_until, _youtube_block_notice_logged
    global _youtube_block_cookie_signature, _youtube_block_reason
    with _youtube_access_lock:
        _youtube_block_until = max(
            _youtube_block_until,
            time.monotonic() + max(30, int(cooldown_seconds)),
        )
        _youtube_block_notice_logged = False
        _youtube_block_cookie_signature = _youtube_cookie_signature()
        _youtube_block_reason = "bot_challenge"


def mark_youtube_rate_limit(cooldown_seconds: int = 60) -> None:
    """Stop the batch from amplifying YouTube's documented request-rate limit."""

    global _youtube_block_until, _youtube_block_notice_logged
    global _youtube_block_cookie_signature, _youtube_block_reason
    with _youtube_access_lock:
        _youtube_block_until = max(
            _youtube_block_until,
            time.monotonic() + max(30, int(cooldown_seconds)),
        )
        _youtube_block_notice_logged = False
        _youtube_block_cookie_signature = _youtube_cookie_signature()
        _youtube_block_reason = "rate_limited"


class YouTubeMixer:
    """     YouTube ."""
    
    #     
    # Downloaded source videos are disposable. OAuth credentials and the
    # publisher queue live one level above and must survive mixer cleanup.
    DEFAULT_CACHE_DIR = Path("youtube_cache") / "downloads"

    def __init__(
        self,
        cache_dir: str = None,
        log_callback: Callable = _dummy_log,
        api_key: str = None,
        api_keys: List[str] = None,
        youtube_data_api_key: str = None,
        mixer_settings: dict = None,
    ):
        self.log = log_callback
        self.api_key = api_key
        self.youtube_data_api_key = youtube_data_api_key
        self.mixer_settings = mixer_settings or {}
        try:
            import json
            config_path = Path('config.json')
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    if not self.mixer_settings:
                        self.mixer_settings = config.get('user_settings', {}).get('youtube_mixer_settings', {})
                    if not api_keys and api_key:
                        config_keys = config.get('user_settings', {}).get('gemini_api_keys', [])
                        if not self.youtube_data_api_key:
                            self.youtube_data_api_key = config.get('user_settings', {}).get('youtube_data_api_key')
                        if config_keys and isinstance(config_keys, list):
                            api_keys = [k for k in config_keys if k]
                            self.log(f'🔑 Загружено {len(api_keys)} API ключей из config.json')
                    elif not self.youtube_data_api_key:
                        self.youtube_data_api_key = config.get('user_settings', {}).get('youtube_data_api_key')
        except Exception:
            pass
        self.api_keys = api_keys or ([api_key] if api_key else [])
        self.CACHE_DIR = Path(cache_dir) if cache_dir else self.DEFAULT_CACHE_DIR
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._search_result_cache = {}
        self._search_result_cache_lock = threading.Lock()
        self._download_failure_lock = threading.Lock()
        self._last_download_failures: Dict[str, YouTubeDownloadFailure] = {}

    def _minimum_youtube_height(self) -> int:
        return normalize_youtube_min_height(
            (self.mixer_settings or {}).get("min_resolution", YOUTUBE_DEFAULT_MIN_HEIGHT)
        )

    def _youtube_downloads_temporarily_blocked(self) -> bool:
        global _youtube_block_until, _youtube_block_notice_logged
        global _youtube_block_cookie_signature, _youtube_block_reason
        with _youtube_access_lock:
            current_signature = _youtube_cookie_signature()
            if (
                _youtube_block_until > time.monotonic()
                and current_signature != _youtube_block_cookie_signature
            ):
                _youtube_block_until = 0.0
                _youtube_block_notice_logged = False
                _youtube_block_cookie_signature = None
                _youtube_block_reason = None
                _youtube_failed_downloads.clear()
                self.log('🍪 Cookies YouTube обновлены — повторный доступ разрешён')
            return time.monotonic() < _youtube_block_until

    def _mark_youtube_bot_block(self, cooldown_seconds: int = 60) -> None:
        global _youtube_block_until, _youtube_block_notice_logged
        global _youtube_block_cookie_signature, _youtube_block_reason
        with _youtube_access_lock:
            _youtube_block_until = max(
                _youtube_block_until,
                time.monotonic() + max(30, int(cooldown_seconds)),
            )
            _youtube_block_notice_logged = False
            _youtube_block_cookie_signature = _youtube_cookie_signature()
            _youtube_block_reason = "bot_challenge"

    def _mark_youtube_rate_limit(self, cooldown_seconds: int = 60) -> None:
        mark_youtube_rate_limit(cooldown_seconds)

    def _record_download_failure(
        self,
        video_id: str,
        failure: YouTubeDownloadFailure,
        *,
        remember: bool = True,
    ) -> None:
        with self._download_failure_lock:
            self._last_download_failures[str(video_id or "unknown")] = failure
        if remember and video_id:
            _youtube_failed_downloads.remember(
                video_id,
                failure,
                connection_signature=_youtube_connection_signature(),
            )

    def _download_failure_summary(self) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        with self._download_failure_lock:
            failures = list(self._last_download_failures.values())
        for failure in failures:
            summary[failure.code] = summary.get(failure.code, 0) + 1
        return summary

    @staticmethod
    def is_yt_dlp_available() -> bool:
        """Проверяет доступность yt-dlp через Python import."""
        return find_spec('yt_dlp') is not None
    
    def _get_cache_key(self, theme: str) -> str:
        """    ."""
        return hashlib.md5(theme.lower().encode()).hexdigest()[:12]
    
    def _get_cache_dir(self, theme: str) -> Path:
        """    ."""
        cache_key = self._get_cache_key(theme)
        cache_dir = self.CACHE_DIR / cache_key
        cache_dir.mkdir(exist_ok=True)
        return cache_dir

    @staticmethod
    def _clone_video_results(videos: List[Dict]) -> List[Dict]:
        return [dict(video) for video in videos]

    def _search_cache_key(
        self,
        query: str,
        max_results: int,
        min_duration: int,
        max_duration: int,
        filter_slideshow: bool,
    ) -> Tuple[str, int, int, int, bool, bool]:
        return (
            str(query or "").casefold().strip(),
            int(max_results or 0),
            int(min_duration or 0),
            int(max_duration or 0),
            bool(filter_slideshow),
            bool(self.youtube_data_api_key),
        )

    def _get_cached_search_results(self, cache_key) -> Optional[List[Dict]]:
        with self._search_result_cache_lock:
            cached = self._search_result_cache.get(cache_key)
        if cached is None:
            return None
        return self._clone_video_results(cached)

    def _store_search_results(self, cache_key, videos: List[Dict]) -> List[Dict]:
        cloned = self._clone_video_results(videos)
        with self._search_result_cache_lock:
            self._search_result_cache[cache_key] = cloned
        return self._clone_video_results(cloned)
    
    def _generate_alternative_queries(self, theme: str, attempt: int) -> List[str]:
        """
             Gemini AI.
        
        AI       
         footage ( ).
        
        Args:
            theme:  
            attempt:   (1-3)
            
        Returns:
              
        """
        queries = []
        if self.api_keys:
            queries.extend(generate_smart_queries(
                theme,
                api_key=self.api_key,
                api_keys=self.api_keys,
                log_callback=self.log,
                max_queries=5,
            ))
        queries.extend(self._generate_simple_queries(theme, attempt))
        return list(dict.fromkeys(query for query in queries if query))[:8]

    def _generate_exhaustive_queries(
        self,
        theme: str,
        visual_context: Optional[Dict] = None,
        max_queries: int = 20,
    ) -> List[str]:
        """Return deterministic exact-to-generic queries for the last-resort pass."""
        translated = ""
        if needs_english_search_translation(theme):
            try:
                translated = translate_to_english_ai(
                    theme,
                    api_key=self.api_key,
                    log_callback=lambda _message: None,
                )
            except (KeyError, ValueError, TypeError, AttributeError):
                translated = ""
        contextual_queries = [
            str(query)
            for query in ((visual_context or {}).get("search_queries") or [])
            if query
        ]
        return [
            item.text
            for item in build_exhaustive_query_ladder(
                theme,
                translated_theme=translated,
                contextual_queries=contextual_queries,
                max_queries=max_queries,
            )
        ]
    
    def _generate_simple_queries(self, theme: str, attempt: int) -> List[str]:
        """Generate universal deterministic queries when AI is unavailable."""
        translated = ""
        if needs_english_search_translation(theme):
            try:
                translated = translate_to_english_ai(
                    theme,
                    api_key=self.api_key,
                    log_callback=lambda _message: None,
                )
            except (KeyError, ValueError, TypeError, AttributeError):
                translated = ""
        return build_universal_queries(
            theme,
            translated_theme=translated,
            attempt=attempt,
            max_queries=5,
        )

    def _generate_single_smart_query(self, theme: str) -> Optional[str]:
        """Compatibility wrapper returning one grounded non-exact route."""

        plan = generate_visual_search_plan(
            theme=theme,
            api_key=self.api_key,
            api_keys=self.api_keys,
            log_callback=self.log,
            max_queries=6,
        )
        return next(
            (item.text for item in plan if item.tier in {"context", "broad"}),
            None,
        )

    def _generate_multilang_queries(self, theme: str) -> List[str]:
        """Compatibility wrapper returning language-diverse grounded routes."""

        plan = generate_visual_search_plan(
            theme=theme,
            api_key=self.api_key,
            api_keys=self.api_keys,
            log_callback=self.log,
            max_queries=10,
        )
        result = []
        languages = set()
        for item in plan:
            language = item.language or detect_query_language(item.text)
            if language in languages:
                continue
            languages.add(language)
            result.append(item.text)
            if len(result) >= 5:
                break
        return result
    
    def _search_single_query(
        self,
        query: str,
        max_results: int,
        min_duration: int,
        max_duration: int,
        timeout: int = 120,
        filter_slideshow: bool = True
    ) -> List[Dict]:
        """
        Поиск видео на YouTube с fallback стратегиями для обхода блокировок.
        
        V2: blacklist, фильтр слайдшоу/таймлапсов.
        V3: Fallback стратегии для России/VPN/DNS проблем.
        """
        query = str(query or "").strip()
        if not query or not re.search(r"\w", query, re.UNICODE):
            self.log("   Search query is empty after normalization")
            return []
        search_cache_key = self._search_cache_key(
            query, max_results, min_duration, max_duration, filter_slideshow
        )
        cached_results = self._get_cached_search_results(search_cache_key)
        if cached_results is not None:
            self.log(f"   📦 Search cache: {len(cached_results)} videos for '{query[:50]}'")
            return cached_results
        if self._youtube_downloads_temporarily_blocked():
            self.log("   ⏱️ Поиск YouTube пропущен: доступ временно заблокирован")
            return []

        if self.youtube_data_api_key:
            from core.youtube.search import search_youtube_api

            api_videos = search_youtube_api(
                query=query,
                api_key=self.youtube_data_api_key,
                max_results=max_results * (3 if filter_slideshow else 2),
                video_duration="any",
                video_definition="high",
                log_callback=self.log,
            )
            accepted = []
            for video in api_videos:
                duration = int(video.get("duration") or 0)
                title = video.get("title") or "Unknown"
                channel = video.get("channel") or ""
                video_id = video.get("id")
                known_height = int(video.get("height") or 0)
                if not video_id or duration < min_duration or duration > max_duration:
                    continue
                if known_height and known_height < self._minimum_youtube_height():
                    continue
                if is_blacklisted(video_id=video_id, channel=channel, title=title):
                    continue
                if filter_slideshow and is_likely_slideshow_by_metadata(title, channel):
                    continue
                if is_mismatched_media_title(title, query):
                    continue
                video["search_query"] = query
                video["relevance"] = calculate_relevance(title, query, "")
                accepted.append(video)
            if accepted:
                self.log(f"   ✅ YouTube Data API: найдено {len(accepted)} подходящих видео")
                accepted.sort(key=calculate_candidate_quality_score, reverse=True)
                selected = select_diverse_candidates(accepted, max_results, max_per_channel=2)
                return self._store_search_results(search_cache_key, selected)
            self.log("   ⚠️ YouTube Data API не дал подходящих видео, используем стандартный поиск")
        
        # One consistent request is less likely to trigger YouTube protection than
        # retrying the same search through several artificial client variants.
        strategies = [
            {
                'name': 'Standard',
                'opts': {},
                'desc': 'Стандартный запрос'
            },
        ]
        
        # Пробуем каждую стратегию
        for strategy_idx, strategy in enumerate(strategies, 1):
            try:
                self.log(f"   📱 Попытка {strategy_idx}/{len(strategies)}: {strategy['name']}...")
                
                # Базовые настройки
                # Keep raw discovery bounded.  The previous 4x multiplier could
                # request 320 entries for each of six routes and exhaust a
                # YouTube session before the first download began.
                search_multiplier = 2 if filter_slideshow else 1
                raw_result_limit = min(
                    100,
                    max(12, int(max_results) * search_multiplier),
                )
                search_query = f"ytsearch{raw_result_limit}:{query}"
                
                import yt_dlp
                
                # Путь к cookies
                cookies_path = Path(__file__).parent.parent / "youtube_cookies.txt"
                
                # Автоматическое определение VPN/Proxy
                proxy_url = _get_auto_proxy(self.log)
                
                # Базовые опции
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': True,
                    'socket_timeout': max(5, min(30, int(timeout))),
                    'ignoreerrors': True,
                    'nocheckcertificate': True,
                    'prefer_insecure': False,
                    'sleep_interval_requests': 1.0,
                }
                # Advertise only a runtime compatible with current yt-dlp EJS.
                # In particular, Node <22 must not be offered automatically.
                ydl_opts.update(build_youtube_js_runtime_options())
                
                # Добавляем cookies если не запрещено стратегией
                if strategy['opts'].get('cookiefile') is not False:
                    if (
                        cookies_path.exists()
                        and has_current_youtube_auth_cookies(str(cookies_path))
                    ):
                        ydl_opts['cookiefile'] = str(cookies_path)
                
                # Добавляем прокси если найден
                if proxy_url:
                    ydl_opts['proxy'] = proxy_url
                
                # Применяем опции стратегии
                ydl_opts.update(strategy['opts'])
                
                # Пробуем выполнить запрос
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(search_query, download=False)
                        
                        if not info:
                            self.log(f"   ⚠️ {strategy['name']}: Поиск не вернул результатов")
                            continue
                        
                        entries = info.get('entries', [])
                        if not entries:
                            self.log(f"   ⚠️ {strategy['name']}: Нет видео в результатах")
                            continue
                        
                        # Успех! Обрабатываем результаты
                        videos = []
                        seen_ids = set()
                        filtered_blacklist = 0
                        filtered_slideshows = 0
                        filtered_media_mismatch = 0
                        
                        for entry in entries:
                            if not entry:
                                continue
                            
                            try:
                                video_id = entry.get('id')
                                duration = entry.get('duration', 0) or 0
                                title = entry.get('title', 'Unknown')
                                channel = entry.get('channel', '') or entry.get('uploader', '')
                                view_count = entry.get('view_count', 0) or 0
                                live_status = entry.get('live_status', '') or ''
                                known_height = int(entry.get('height', 0) or 0)
                                
                                if not video_id or video_id in seen_ids:
                                    continue
                                
                                seen_ids.add(video_id)
                                
                                # Фильтр по длительности
                                if duration < min_duration or duration > max_duration:
                                    continue

                                if known_height and known_height < self._minimum_youtube_height():
                                    continue

                                if live_status in {'is_live', 'is_upcoming', 'post_live'}:
                                    continue
                                
                                # Проверка чёрного списка
                                if is_blacklisted(video_id=video_id, channel=channel, title=title):
                                    filtered_blacklist += 1
                                    continue

                                if filter_slideshow and is_likely_slideshow_by_metadata(title, channel):
                                    filtered_slideshows += 1
                                    continue

                                if is_mismatched_media_title(title, query):
                                    filtered_media_mismatch += 1
                                    continue
                                
                                description = entry.get('description', '') or ''
                                videos.append({
                                    'id': video_id,
                                    'title': title,
                                    'duration': duration,
                                    'channel': channel,
                                    'channel_id': entry.get('channel_id', '') or entry.get('uploader_id', ''),
                                    'view_count': view_count,
                                    'description': description,
                                    'height': entry.get('height', 0) or 0,
                                    'width': entry.get('width', 0) or 0,
                                    'fps': entry.get('fps', 0) or 0,
                                    'live_status': live_status,
                                    'search_query': query,
                                    'relevance': calculate_relevance(title, query, description),
                                    'url': f'https://www.youtube.com/watch?v={video_id}'
                                })
                                
                                if len(videos) >= raw_result_limit:
                                    break
                            
                            except Exception:
                                continue
                        
                        if videos:
                            self.log(
                                f"   ✅ {strategy['name']}: Найдено {len(videos)} видео "
                                f"(blacklist={filtered_blacklist}, slideshows={filtered_slideshows}, "
                                f"media_mismatch={filtered_media_mismatch})"
                            )
                            videos.sort(
                                key=lambda video: (
                                    calculate_candidate_quality_score(video),
                                    video.get('view_count', 0),
                                ),
                                reverse=True,
                            )
                            selected = select_diverse_candidates(videos, max_results, max_per_channel=2)
                            return self._store_search_results(search_cache_key, selected)
                        else:
                            self.log(f"   ⚠️ {strategy['name']}: Видео не прошли фильтры")
                            continue
                
                except Exception as e:
                    error_msg = str(e)
                    
                    # Проверяем тип ошибки
                    failure = classify_youtube_download_error(error_msg)
                    if failure.code == "bot_challenge":
                        self._mark_youtube_bot_block(failure.global_cooldown_seconds)
                    elif failure.code == "rate_limited":
                        self._mark_youtube_rate_limit(failure.global_cooldown_seconds)
                    self.log(
                        f"   ⚠️ {strategy['name']}: {failure.code} — {failure.summary}"
                    )
                    
                    # Продолжаем со следующей стратегией
                    continue
            
            except Exception as e:
                self.log(f"   ❌ {strategy['name']}: Критическая ошибка - {str(e)[:100]}")
                continue
        
        # Все стратегии провалились
        self.log(f"   ❌ Все {len(strategies)} стратегий провалились для '{query[:50]}'")
        return []

    def search_youtube_videos(
        self,
        theme: str,
        max_results: int = 10,
        min_duration: int = 30,
        max_duration: int = 7200,
        use_smart_queries: bool = True,
        max_query_searches: int = 6,
        visual_context: Optional[Dict] = None,
        original_theme: Optional[str] = None,
    ) -> List[Dict]:
        
        min_duration = max(0, int(min_duration or 0))
        max_duration = max(min_duration, int(max_duration or min_duration))
        """
           YouTube     .
        
        V2 :
        -     AI (, stock footage)
        - Blacklist 
        -   /
        
        Args:
            theme:   
            max_results:  
            min_duration:   ()
            max_duration:   ()
            use_smart_queries:  AI   
            
        Returns:
                 
        """
        self.log("🔍 Начинаю поиск видео")
        
        if not self.is_yt_dlp_available():
            self.log("⚠️ yt-dlp не установлен! Запустите: pip install yt-dlp")
            return []
        
        all_videos = []
        seen_ids = set()
        seen_titles = set()
        query_searches_used = 0

        def _run_query(query):
            nonlocal query_searches_used
            if query_searches_used >= max(1, int(max_query_searches)):
                return []
            query_searches_used += 1
            return self._search_single_query(
                query, max_results, min_duration, max_duration, timeout=30
            )

        # ────────────────────────────────────────────────────────
        # УПРОЩЁННЫЙ 3-УРОВНЕВЫЙ ПОИСК
        # Уровень 1: первые 3 слова темы (точный запрос)
        # Уровень 2: широкий синоним (1 AI-запрос)
        # Уровень 3: только 1-2 ключевых слова
        # Каждый уровень запускается только если предыдущий не дал результатов
        # ────────────────────────────────────────────────────────

        def _add_videos(videos, planned_query=None):
            added = 0
            for v in videos:
                title_signature = title_fingerprint(v.get("title", ""))
                if v['id'] in seen_ids or (title_signature and title_signature in seen_titles):
                    continue
                seen_ids.add(v['id'])
                if title_signature:
                    seen_titles.add(title_signature)
                if planned_query is not None:
                    v['search_query'] = planned_query.text
                    v['search_tier'] = planned_query.tier
                    v['query_weight'] = planned_query.weight
                    v['fallback_search_route'] = bool(
                        original_theme
                        and str(original_theme).strip().casefold()
                        != str(theme).strip().casefold()
                    )
                all_videos.append(v)
                added += 1
            return added
        
        # Очищаем тему от пунктуации для поиска
        translated_theme = ""
        if needs_english_search_translation(theme):
            translated_theme = translate_to_english_ai(
                theme, api_key=self.api_key, log_callback=self.log
            )
        ground_truth_theme = str(original_theme or theme).strip()
        translated_ground_truth = translated_theme if ground_truth_theme == theme else ""
        if needs_english_search_translation(ground_truth_theme) and not translated_ground_truth:
            translated_ground_truth = translate_to_english_ai(
                ground_truth_theme,
                api_key=self.api_key,
                log_callback=lambda _message: None,
            )
        relevance_theme = f"{ground_truth_theme} {translated_ground_truth}".strip()
        script_queries = [
            str(query).strip()
            for query in ((visual_context or {}).get('search_queries') or [])
            if str(query or '').strip()
        ]
        if script_queries:
            # A visual brief has already spent the model call on the actual
            # narration. Reuse its grounded queries instead of asking a second
            # planner to reinterpret the broader topic.
            base_plan = build_query_ladder(
                theme,
                translated_theme,
                max_queries=max(1, int(max_query_searches)),
            )
            query_limit = max(1, int(max_query_searches))
            exact_routes = [item for item in base_plan if item.tier == 'exact'][:2]
            subject_routes = [item for item in base_plan if item.tier == 'subject'][:1]
            reserve_subject = 1 if subject_routes and query_limit - len(exact_routes) > 2 else 0
            script_slots = max(1, query_limit - len(exact_routes) - reserve_subject)
            script_routes = [
                SearchQuery(
                    text=query,
                    tier='context',
                    language=detect_query_language(query),
                    source='youtube',
                    weight=78,
                )
                for query in script_queries[:script_slots]
            ]
            query_plan = []
            seen_query_text = set()
            for item in exact_routes + script_routes + subject_routes + base_plan:
                key = item.text.casefold()
                if key in seen_query_text:
                    continue
                seen_query_text.add(key)
                query_plan.append(item)
                if len(query_plan) >= query_limit:
                    break
        elif use_smart_queries:
            query_plan = generate_visual_search_plan(
                theme=theme,
                translated_theme=translated_theme,
                api_key=self.api_key,
                api_keys=self.api_keys,
                log_callback=self.log,
                max_queries=max(1, int(max_query_searches)),
            )
        else:
            query_plan = build_query_ladder(
                theme,
                translated_theme,
                max_queries=max(1, int(max_query_searches)),
            )

        fallback_route_search = bool(
            original_theme
            and str(original_theme).strip().casefold()
            != str(theme).strip().casefold()
        )
        if fallback_route_search:
            # The route may be close context generated by Gemini, but it is
            # never an exact claim about the immutable user topic.
            query_plan = [
                SearchQuery(
                    text=item.text,
                    tier=("context" if item.tier in {"exact", "subject"} else item.tier),
                    language=item.language,
                    source=item.source,
                    weight=min(item.weight, 72),
                )
                for item in query_plan
            ]

        context_queries_tried = 0
        required_context_queries = (
            min(
                len([item for item in query_plan if item.tier == 'context']),
                int((self.mixer_settings or {}).get('visual_search_min_context_queries', 2)),
            )
            if script_queries
            else 0
        )
        for query_index, planned_query in enumerate(query_plan):
            if (
                len(all_videos) >= max_results
                and planned_query.tier in {"context", "broad"}
                and context_queries_tried >= required_context_queries
            ):
                break
            self.log(
                f"🔍 Поиск [{planned_query.tier}/{planned_query.language}]: "
                f"'{planned_query.text[:70]}'"
            )
            found = _run_query(planned_query.text)
            if planned_query.tier == 'context':
                context_queries_tried += 1
            added = _add_videos(found, planned_query)
            next_query = query_plan[query_index + 1] if query_index + 1 < len(query_plan) else None
            should_skip_broad_search = False
            if next_query and next_query.tier in {"context", "broad"}:
                strong_count = count_strong_topic_candidates(all_videos, relevance_theme)
                needed_strong = int(
                    (self.mixer_settings or {}).get(
                        'early_stop_strong_candidates',
                        max(1, min(max_results, 2)),
                    )
                )
                should_skip_broad_search = (
                    strong_count >= needed_strong
                    and context_queries_tried >= required_context_queries
                )
            if added:
                self.log(f"   ✅ Найдено +{added} видео")
            else:
                self.log("   ⚠️ Нет результатов")
            if should_skip_broad_search:
                self.log("⚡ Достаточно точных источников, широкие YouTube-запросы пропущены")
                break
        
        # НОВОЕ: Фильтрация по релевантности (Task 0.3)
        # Отклоняем видео с релевантностью < 5% (или 0% для сложных тем)
        # Для видео 0-30% делаем дополнительную проверку через Gemini
        filtered_videos = []
        rejected_count = 0
        semantic_check_count = 0
        semantic_approved_count = 0
        lexical_fallback_count = 0
        semantic_skipped_count = 0
        rejection_reasons: Dict[str, int] = {}
        verbose_rejections = bool(
            (self.mixer_settings or {}).get('verbose_candidate_rejections', False)
        )

        def _note_rejection(reason: str, detail: str) -> None:
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            if verbose_rejections:
                self.log(f"   ↳ {detail}")
        
        # 🎯 АДАПТИВНЫЙ ПОРОГ: Для сложных/специфичных тем снижаем порог до 0%
        # Определяем "сложную тему" по длине (>100 символов) или наличию специфичных слов
        is_complex_theme = (
            len(extract_visual_core(ground_truth_theme).split()) >= 7
            or len(ground_truth_theme) > 100
        )
        # Читаем из настроек, с адаптивным фолбеком
        base_threshold = int(
            (self.mixer_settings or {}).get(
                'relevance_base_threshold', 8 if is_complex_theme else 12
            )
        )
        semantic_threshold = int(
            (self.mixer_settings or {}).get(
                'relevance_semantic_threshold', 50 if is_complex_theme else 30
            )
        )
        max_semantic_checks = int(
            (self.mixer_settings or {}).get(
                'max_semantic_relevance_checks',
                max(2, min(6, max_results * 2)),
            )
        )
        
        if is_complex_theme:
            self.log(f"🎯 Сложная тема обнаружена - используем Gemini для всех видео (порог: {base_threshold}%, семантика: 0-{semantic_threshold}%)")
        
        for v in all_videos:
            if is_likely_slideshow_by_metadata(v.get('title', ''), v.get('channel', '')):
                rejected_count += 1
                _note_rejection(
                    "slideshow",
                    f"Слайд-шоу по метаданным: '{v.get('title', 'N/A')[:50]}'",
                )
                continue
            if is_mismatched_media_title(v.get('title', ''), relevance_theme):
                rejected_count += 1
                _note_rejection(
                    "media_type",
                    f"Неподходящий тип: '{v.get('title', 'N/A')[:50]}'",
                )
                continue

            tier_scores = calculate_tiered_relevance(
                video_title=v.get('title', ''),
                theme=relevance_theme,
                search_query=v.get('search_query', relevance_theme),
                tier=v.get('search_tier', 'exact'),
                video_description=v.get('description', ''),
            )
            relevance = tier_scores['effective']
            v['topic_relevance'] = tier_scores['topic']
            v['route_relevance'] = tier_scores['route']
            topic_evidence = calculate_topic_evidence(
                v.get('title', ''),
                relevance_theme,
                v.get('description', ''),
            )
            v['topic_evidence_score'] = int(topic_evidence.get('score') or 0)
            route_evidence_ok = (
                v.get('search_tier') == 'context'
                and has_specific_route_evidence(
                    v.get('title', ''),
                    v.get('search_query', relevance_theme),
                    v.get('description', ''),
                )
            )
            v['specific_route_evidence'] = bool(route_evidence_ok)
            route_evidence_can_ground = bool(
                route_evidence_ok and not v.get('fallback_search_route')
            )
            v['relevance'] = relevance  # Сохраняем для сортировки

            # A broad route may match its own generic wording perfectly while
            # having nothing to do with the original topic. Force it through
            # semantic/lexical validation instead of auto-accepting it.
            if (
                v.get('search_tier') in {'context', 'broad'}
                and v.get('topic_relevance', 0) < 20
                and not has_meaningful_topic_evidence(
                    v.get('title', ''),
                    relevance_theme,
                    v.get('description', ''),
                )
                and not route_evidence_can_ground
            ):
                relevance = min(relevance, semantic_threshold)
                v['relevance'] = relevance
            
            # Базовый порог: 0-5% в зависимости от сложности темы
            if relevance < base_threshold:
                rejected_count += 1
                _note_rejection(
                    "low_relevance",
                    f"Низкая релевантность {relevance}%: '{v.get('title', 'N/A')[:50]}'",
                )
            # Пограничная зона 0-50%: проверяем через Gemini для сложных тем
            elif (
                relevance <= semantic_threshold
                and self.api_keys
                and semantic_check_count < max_semantic_checks
            ):
                semantic_check_count += 1
                semantic_relevance = check_semantic_relevance_with_gemini(
                    v.get('title', ''),
                    ground_truth_theme,
                    self.api_keys,
                    self.log,
                    video_description=v.get('description', ''),
                    search_query=v.get('search_query', relevance_theme),
                    channel=v.get('channel', ''),
                )
                
                if semantic_relevance >= 40:  # Gemini подтвердил релевантность
                    v['relevance'] = semantic_relevance  # Обновляем оценку
                    v['semantic_relevance'] = semantic_relevance
                    filtered_videos.append(v)
                    semantic_approved_count += 1
                    self.log(f"✅ Одобрено Gemini: '{v.get('title', 'N/A')[:50]}' (базовая: {relevance}% → Gemini: {semantic_relevance}%)")
                elif (
                    semantic_relevance < 0
                    and relevance >= 25
                    and (
                        has_meaningful_topic_evidence(
                            v.get('title', ''),
                            relevance_theme,
                            v.get('description', ''),
                        )
                        or has_meaningful_keyword_overlap(
                            v.get('title', ''), relevance_theme
                        )
                    )
                ):
                    filtered_videos.append(v)
                    lexical_fallback_count += 1
                    self.log(
                        f"✅ Принято без Gemini: '{v.get('title', 'N/A')[:50]}' "
                        f"(лексическая релевантность: {relevance}%)"
                    )
                else:
                    rejected_count += 1
                    if semantic_relevance >= 0:
                        detail = (
                            f"Gemini отклонил {relevance}%→{semantic_relevance}%: "
                            f"'{v.get('title', 'N/A')[:50]}'"
                        )
                        _note_rejection("gemini", detail)
                    else:
                        _note_rejection(
                            "gemini_unavailable",
                            f"Gemini недоступен, {relevance}%: '{v.get('title', 'N/A')[:50]}'",
                        )
            elif relevance <= semantic_threshold:
                if self.api_keys and semantic_check_count >= max_semantic_checks:
                    semantic_skipped_count += 1
                context_route_ok = (
                    v.get('search_tier') == 'context'
                    and v.get('route_relevance', 0) >= 25
                )
                topic_evidence_ok = has_meaningful_topic_evidence(
                    v.get('title', ''),
                    relevance_theme,
                    v.get('description', ''),
                )
                if (
                    context_route_ok
                    and (topic_evidence_ok or route_evidence_can_ground)
                ) or (
                    relevance >= 25 and topic_evidence_ok
                ):
                    filtered_videos.append(v)
                    lexical_fallback_count += 1
                else:
                    rejected_count += 1
                    _note_rejection(
                        "semantic_budget",
                        f"Не прошло без Gemini, {relevance}%: "
                        f"'{v.get('title', 'N/A')[:50]}'",
                    )
            else:
                # Релевантность > порога - принимаем без дополнительной проверки
                filtered_videos.append(v)

        for video in filtered_videos:
            video['quality_score'] = calculate_candidate_quality_score(video)
        
        if rejected_count > 0:
            reason_summary = ", ".join(
                f"{reason}={count}" for reason, count in sorted(rejection_reasons.items())
            )
            self.log(
                f"🔍 Отфильтровано {rejected_count} кандидатов"
                + (f" ({reason_summary})" if reason_summary else "")
            )
        
        if semantic_check_count > 0:
            self.log(
                f"🤖 Gemini проверил {semantic_check_count} видео, "
                f"одобрено: {semantic_approved_count}, принято по словам: {lexical_fallback_count}"
            )
        if semantic_skipped_count > 0:
            self.log(f"⚡ Gemini-проверки ограничены: пропущено {semantic_skipped_count} спорных видео")
        
        # Сортировка: общая оценка качества > релевантность > просмотры.
        filtered_videos.sort(key=lambda v: (
            v.get('quality_score', 0),
            v.get('relevance', 0),
            v.get('view_count', 0),
        ), reverse=True)
        
        if filtered_videos:
            self.log(f"✅ Найдено {len(filtered_videos)} релевантных видео")
            # Показываем топ-3 с релевантностью
            for i, v in enumerate(filtered_videos[:3]):
                rel = v.get('relevance', 0)
                self.log(f"   #{i+1}: [{rel}%] {v.get('title', 'N/A')[:50]}")
            return select_diverse_candidates(filtered_videos, max_results, max_per_channel=2)
        else:
            self.log(f"❌ Не найдено релевантных видео для темы '{ground_truth_theme}'")
            return []
    
    def _try_innertube_download(
        self,
        video_id: str,
        video_url: str,
        output_dir: Path,
        min_resolution: int = YOUTUBE_DEFAULT_MIN_HEIGHT,
        target_orientation: str = 'any'
    ) -> Optional[str]:
        """
        🔄 FALLBACK: Скачивание через InnerTube API (прямые запросы к YouTube).
        
        УЛУЧШЕННАЯ ВЕРСИЯ V2:
        - Использует yt-dlp для получения прямых URL (обходит расшифровку подписей)
        - Скачивает через requests (обходит блокировки yt-dlp)
        - Работает даже когда yt-dlp не может скачать напрямую
        
        Args:
            video_id: ID видео
            video_url: URL видео
            output_dir: Папка для сохранения
            min_resolution: Минимальное разрешение
            target_orientation: Ориентация видео
            
        Returns:
            Путь к скачанному файлу или None
        """
        self.log("🔄 InnerTube API V2: yt-dlp экстрактор + requests скачивание")
        min_resolution = normalize_youtube_min_height(min_resolution)
        
        try:
            import yt_dlp
            
            # Настройки для получения информации без скачивания
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'skip_download': True,
                'socket_timeout': 45,
                'retries': 5,
                'fragment_retries': 5,
            }
            
            # Добавляем VPN если есть
            proxy_url = _get_auto_proxy(self.log)
            if proxy_url:
                ydl_opts['proxy'] = proxy_url
            
            self.log("   📡 Получаем прямые URL через yt-dlp...")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                
                if not info:
                    self.log("   ❌ Не удалось получить информацию")
                    return None
                
                formats = info.get('formats', [])
                if not formats:
                    self.log("   ❌ Нет форматов")
                    return None
                
                self.log(f"   ✅ Найдено {len(formats)} форматов")
                
                # Фильтруем форматы
                suitable_formats = []
                for fmt in formats:
                    url = fmt.get('url')
                    if not url:
                        continue
                    
                    height = fmt.get('height') or 0
                    width = fmt.get('width') or 0
                    ext = fmt.get('ext', '')
                    
                    # Только видео форматы
                    if ext not in ['mp4', 'webm']:
                        continue
                    
                    # Проверка разрешения
                    if height < min_resolution:
                        continue
                    
                    # Проверка ориентации
                    if not matches_target_orientation(width, height, target_orientation):
                        continue
                    
                    suitable_formats.append({
                        'url': url,
                        'height': height,
                        'width': width,
                        'ext': ext,
                        'filesize': fmt.get('filesize') or 0
                    })
                
                if not suitable_formats:
                    self.log("   ⚠️ Нет подходящих форматов")
                    return None
                
                # Сортируем по качеству
                suitable_formats.sort(key=lambda f: f['height'], reverse=True)
                best_format = suitable_formats[0]
                
                video_url_direct = best_format['url']
                height = best_format['height']
                width = best_format['width']
                
                self.log(f"   📐 Выбран формат: {width}x{height}")
                
                # Скачиваем через requests
                output_path = output_dir / f"{video_id}.mp4"
                temp_path = output_dir / f"{video_id}_innertube.mp4"
                
                self.log("   ⬇️ Скачивание через requests...")
                
                headers = {
                    'User-Agent': random.choice(USER_AGENTS),
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Range': 'bytes=0-',  # Поддержка докачки
                }
                
                # 🌐 КРИТИЧНО: Добавляем прокси для requests (для России/заблокированных стран)
                proxies = None
                if proxy_url:
                    proxies = {
                        'http': proxy_url,
                        'https': proxy_url
                    }
                    self.log(f"   🌐 requests использует прокси: {proxy_url}")
                
                # ⏱️ ОПТИМИЗАЦИЯ: Таймаут на подключение И чтение (не зависать)
                response = requests.get(video_url_direct, stream=True, headers=headers, timeout=(10, 60), proxies=proxies)
                
                if response.status_code not in [200, 206]:  # 206 = Partial Content
                    self.log(f"   ❌ Ошибка скачивания: HTTP {response.status_code}")
                    return None
                
                # Сохраняем файл с таймаутом на чтение
                downloaded_size = 0
                start_time = time.time()
                max_download_time = 90  # Максимум 90 секунд на скачивание
                
                with open(temp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=1024*1024):  # 1MB chunks
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            
                            # Проверка таймаута
                            if time.time() - start_time > max_download_time:
                                self.log(f"   ⏱️ Превышен таймаут скачивания ({max_download_time}s)")
                                temp_path.unlink(missing_ok=True)
                                return None
                
                if not temp_path.exists() or temp_path.stat().st_size < 1000:
                    self.log("   ❌ Файл пустой")
                    temp_path.unlink(missing_ok=True)
                    return None
                
                size_mb = downloaded_size / (1024 * 1024)
                self.log(f"   📊 Скачано: {size_mb:.1f} MB")
                
                # 🔧 ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ: Показываем что делаем
                self.log("   🔍 Валидация видео...")
                validation_start = time.time()
                
                actual_dimensions = probe_video_dimensions(temp_path)
                if not actual_dimensions:
                    self.log("   ❌ Не удалось подтвердить разрешение скачанного видео")
                    temp_path.unlink(missing_ok=True)
                    return None
                actual_width, actual_height = actual_dimensions
                if actual_height < min_resolution:
                    self.log(
                        f"   ❌ Фактическое разрешение {actual_width}x{actual_height} "
                        f"ниже обязательных {min_resolution}p"
                    )
                    temp_path.unlink(missing_ok=True)
                    return None

                # Валидация видео
                if not validate_video_file(str(temp_path), deep_check=True):
                    validation_time = time.time() - validation_start
                    self.log(f"   ⏱️ Валидация заняла {validation_time:.1f}s")
                    self.log("   🔧 Видео повреждено, попытка восстановления")
                    repaired = repair_video_file(str(temp_path), self.log)
                    if not repaired:
                        self.log("   ❌ Не удалось восстановить видео")
                        temp_path.unlink(missing_ok=True)
                        return None
                else:
                    validation_time = time.time() - validation_start
                    self.log(f"   ✅ Валидация завершена за {validation_time:.1f}s")
                
                # Переименовываем в финальный файл
                try:
                    if output_path.exists():
                        output_path.unlink()
                    temp_path.rename(output_path)
                except Exception:
                    shutil.copy2(temp_path, output_path)
                    temp_path.unlink(missing_ok=True)
                
                self.log("✅ InnerTube API V2: Видео успешно скачано")
                return str(output_path)
                
        except Exception as e:
            self.log(f"❌ InnerTube API V2 ошибка: {str(e)[:100]}")
            return None
    
    def _try_android_download(
        self,
        video_id: str,
        video_url: str,
        output_dir: Path,
        min_resolution: int = YOUTUBE_DEFAULT_MIN_HEIGHT,
        target_orientation: str = 'any'
    ) -> Optional[str]:
        """
        🔄 FALLBACK: Скачивание через Android Client (без cookies).
        
        УЛУЧШЕНИЯ V3:
        - 🚀 Быстрая проверка доступности (extract_info без download)
        - 🎯 Умный порядок клиентов (самые надежные первыми)
        - ⚡ Ранний выход при первом успехе
        - 🔧 Лучшая обработка ошибок
        - 📊 Детальная диагностика
        - 💾 Меньше временных файлов
        
        ПРЕИМУЩЕСТВА:
        - Работает БЕЗ cookies (обходит блокировки по IP)
        - Предоставляет ВСЕ форматы (144p-4K)
        - Не требует авторизации
        - Быстрее чем V2 (проверка перед скачиванием)
        
        Args:
            video_id: ID видео
            video_url: URL видео
            output_dir: Папка для сохранения
            min_resolution: Минимальное разрешение
            target_orientation: Ориентация видео
            
        Returns:
            Путь к скачанному файлу или None
        """
        self.log("🔄 Android Client: Скачивание без cookies")
        min_resolution = normalize_youtube_min_height(min_resolution)
        
        try:
            import yt_dlp
            
            # 🎯 ОПТИМИЗИРОВАННЫЙ ПОРЯДОК (от самых надежных к менее надежным)
            # Testsuite и Music - самые стабильные, Creator и VR - запасные
            android_clients = [
                ('android_testsuite', 'Android Testsuite'),
                ('android_music', 'Android Music'),
                ('android_creator', 'Android Creator'),
                ('android_vr', 'Android VR'),
                ('android', 'Android'),
            ]
            
            # 🚀 ОПТИМИЗАЦИЯ: Пробуем клиенты с быстрой проверкой
            for attempt, (client_id, client_name) in enumerate(android_clients, 1):
                self.log(f"   📱 Попытка {attempt}/{len(android_clients)}: {client_name}...")
                
                try:
                    # 🎯 БЫСТРАЯ ПРОВЕРКА: Сначала проверяем доступность БЕЗ скачивания
                    check_opts = {
                        'quiet': True,
                        'no_warnings': True,
                        'socket_timeout': 10,  # Быстрый timeout для проверки
                        'extractor_args': {
                            'youtube': {
                                'player_client': [client_id],
                                'skip': ['hls', 'dash'],
                            }
                        },
                        'cookiefile': None,  # БЕЗ cookies
                    }
                    
                    # Добавляем VPN если есть
                    proxy_url = _get_auto_proxy(self.log)
                    if proxy_url:
                        check_opts['proxy'] = proxy_url
                    
                    # Проверяем доступность
                    try:
                        with yt_dlp.YoutubeDL(check_opts) as ydl:
                            info = ydl.extract_info(video_url, download=False)
                            
                            if not info:
                                self.log(f"   ❌ {client_name}: Нет информации о видео")
                                continue
                            
                            # Проверка длительности
                            duration = info.get('duration', 0)
                            if duration and duration < 60:
                                self.log(f"   ⏱️ Видео слишком короткое ({duration}s < 60s)")
                                return None
                            
                            # Проверяем что есть подходящие форматы
                            formats = info.get('formats', [])
                            if not formats:
                                self.log(f"   ❌ {client_name}: Нет доступных форматов")
                                continue
                            
                            self.log(f"   ✅ {client_name}: Видео доступно, начинаем скачивание...")
                            
                    except Exception as check_error:
                        error_msg = str(check_error)
                        if 'not available' in error_msg.lower():
                            self.log(f"   ❌ {client_name}: Видео недоступно")
                        elif 'private' in error_msg.lower():
                            self.log(f"   ❌ {client_name}: Видео приватное")
                        else:
                            self.log(f"   ⚠️ {client_name}: Ошибка проверки - {error_msg[:100]}")
                        continue
                    
                    # 📥 СКАЧИВАНИЕ: Если проверка прошла - скачиваем
                    temp_path = output_dir / f"temp_{video_id}_android_{attempt}.mp4"
                    if temp_path.exists():
                        temp_path.unlink()
                    
                    # Every selector branch enforces the same floor. Never append
                    # an unqualified ``best``: on restricted clients it can be 144p.
                    smart_format = build_youtube_format_selector(min_resolution)
                    
                    ydl_opts = {
                        'format': smart_format,
                        'outtmpl': str(temp_path),
                        'quiet': True,
                        'no_warnings': True,
                        'socket_timeout': 25,  # Увеличен timeout для стабильности
                        'retries': 3,  # Больше retry
                        'fragment_retries': 5,  # Больше retry для фрагментов
                        'extractor_retries': 3,  # Больше retry для экстрактора
                        'http_chunk_size': 10485760,  # 10MB чанки
                        'extractor_args': {
                            'youtube': {
                                'player_client': [client_id],
                                'skip': ['hls', 'dash'],
                            }
                        },
                        'cookiefile': None,  # БЕЗ cookies
                        'concurrent_fragment_downloads': 4,
                    }
                    
                    # Добавляем VPN если есть
                    if proxy_url:
                        ydl_opts['proxy'] = proxy_url
                    
                    # 🎯 УМНЫЙ USER-AGENT
                    user_agents = [
                        'com.google.android.youtube/19.02.39 (Linux; U; Android 13) gzip',
                        'com.google.android.youtube/18.49.37 (Linux; U; Android 12) gzip',
                        'com.google.android.apps.youtube.music/6.42.52 (Linux; U; Android 13) gzip',
                    ]
                    if attempt <= len(user_agents):
                        ydl_opts['http_headers'] = {'User-Agent': user_agents[attempt - 1]}
                    
                    # 📥 СКАЧИВАНИЕ
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([video_url])
                    
                    # Проверяем что файл скачался
                    if not temp_path.exists() or temp_path.stat().st_size < 1000:
                        self.log(f"   ❌ {client_name}: Файл пустой или не создан")
                        continue
                    
                    size_mb = temp_path.stat().st_size / (1024 * 1024)
                    self.log(f"   📊 {client_name}: Скачано {size_mb:.1f} MB")
                    
                    # Проверяем разрешение и ориентацию
                    dimensions = probe_video_dimensions(temp_path)
                    if not dimensions:
                        self.log("   ❌ Не удалось подтвердить разрешение, пробуем следующий клиент")
                        temp_path.unlink(missing_ok=True)
                        continue
                    dl_width, dl_height = dimensions
                    # Android is a transport fallback, not a quality fallback.
                    if dl_height < min_resolution:
                        self.log(f"   ⚠️ Разрешение {dl_height}p — ниже обязательных {min_resolution}p, пробуем следующий клиент")
                        temp_path.unlink(missing_ok=True)
                        continue
                    if not matches_target_orientation(dl_width, dl_height, target_orientation):
                        self.log("   ⚠️ Неправильная ориентация (вертикальное вместо горизонтального)")
                        temp_path.unlink(missing_ok=True)
                        continue
                    self.log(f"   📐 Разрешение: {dl_width}x{dl_height}")
                    
                    # Валидация видео
                    if not validate_video_file(str(temp_path), deep_check=True):
                        self.log("   🔧 Видео повреждено, попытка восстановления...")
                        repaired = repair_video_file(str(temp_path), self.log)
                        if not repaired:
                            self.log("   ❌ Не удалось восстановить видео, пробуем следующий клиент")
                            temp_path.unlink(missing_ok=True)
                            continue
                    
                    # Переименовываем в финальный файл
                    final_path = output_dir / f"{video_id}.mp4"
                    try:
                        if final_path.exists():
                            final_path.unlink()
                        temp_path.rename(final_path)
                    except Exception:
                        shutil.copy2(temp_path, final_path)
                        temp_path.unlink(missing_ok=True)
                    
                    self.log(f"✅ Android Client ({client_name}): Видео успешно скачано")
                    return str(final_path)
                    
                except Exception as e:
                    error_msg = str(e)
                    if 'not available' in error_msg.lower():
                        self.log(f"   ❌ {client_name}: Видео недоступно")
                    elif 'private' in error_msg.lower():
                        self.log(f"   ❌ {client_name}: Видео приватное")
                    elif 'timeout' in error_msg.lower():
                        self.log(f"   ⏱️ {client_name}: Timeout")
                    else:
                        self.log(f"   ❌ {client_name}: {error_msg[:150]}")
                    
                    # Очищаем временный файл
                    if temp_path.exists():
                        temp_path.unlink(missing_ok=True)
                    continue
            
            self.log("   ❌ Все Android клиенты провалились")
            return None
            
        except Exception as e:
            self.log(f"❌ Android Client критическая ошибка: {str(e)[:150]}")
            import traceback
            self.log(f"   Traceback: {traceback.format_exc()[:300]}")
            return None
    
    def _try_tv_embedded_download(
        self,
        video_id: str,
        video_url: str,
        output_dir: Path,
        min_resolution: int = YOUTUBE_DEFAULT_MIN_HEIGHT,
        target_orientation: str = 'any'
    ) -> Optional[str]:
        """
        🔄 FALLBACK: Скачивание через TV Embedded Client (с cookies).
        
        УЛУЧШЕНИЯ V2:
        - 🚀 Быстрая проверка доступности (extract_info без download)
        - 🎯 Умный выбор формата (1080p+ приоритет)
        - ⚡ Увеличены timeout и retry
        - 🔧 Лучшая обработка ошибок
        - 📊 Детальная диагностика
        
        ПРЕИМУЩЕСТВА:
        - Даёт высокое качество (1080p+)
        - Высокий битрейт (лучше чем Android)
        - Стабильное скачивание
        - Работает когда стандартные методы не дают качество
        
        Args:
            video_id: ID видео
            video_url: URL видео
            output_dir: Папка для сохранения
            min_resolution: Минимальное разрешение
            target_orientation: Ориентация видео
            
        Returns:
            Путь к скачанному файлу или None
        """
        self.log("🔄 TV Embedded Client: Скачивание высокого качества")
        min_resolution = normalize_youtube_min_height(min_resolution)
        
        try:
            import yt_dlp
            
            # Проверяем наличие cookies
            cookies_file = Path(__file__).parent.parent / "youtube_cookies.txt"
            has_cookies = cookies_file.exists()
            
            if not has_cookies:
                self.log("   ⚠️ TV Embedded требует cookies, пропускаем")
                return None
            
            # 🎯 БЫСТРАЯ ПРОВЕРКА: Сначала проверяем доступность БЕЗ скачивания
            check_opts = {
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 10,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['tv_embedded'],
                    }
                },
                'cookiefile': str(cookies_file),
            }
            
            # Добавляем VPN если есть
            proxy_url = _get_auto_proxy(self.log)
            if proxy_url:
                check_opts['proxy'] = proxy_url
            
            # Проверяем доступность
            try:
                with yt_dlp.YoutubeDL(check_opts) as ydl:
                    info = ydl.extract_info(video_url, download=False)
                    
                    if not info:
                        self.log("   ❌ TV Embedded: Нет информации о видео")
                        return None
                    
                    # Проверка длительности
                    duration = info.get('duration', 0)
                    if duration and duration < 60:
                        self.log(f"   ⏱️ Видео слишком короткое ({duration}s < 60s)")
                        return None
                    
                    # Проверяем что есть подходящие форматы
                    formats = info.get('formats', [])
                    if not formats:
                        self.log("   ❌ TV Embedded: Нет доступных форматов")
                        return None
                    
                    self.log("   ✅ TV Embedded: Видео доступно, начинаем скачивание...")
                    
            except Exception as check_error:
                error_msg = str(check_error)
                if 'not available' in error_msg.lower():
                    self.log("   ❌ TV Embedded: Видео недоступно")
                elif 'private' in error_msg.lower():
                    self.log("   ❌ TV Embedded: Видео приватное")
                else:
                    self.log(f"   ⚠️ TV Embedded: Ошибка проверки - {error_msg[:100]}")
                return None
            
            # 📥 СКАЧИВАНИЕ: Если проверка прошла - скачиваем
            temp_path = output_dir / f"temp_{video_id}_tv.mp4"
            if temp_path.exists():
                temp_path.unlink()
            
            format_str = build_youtube_format_selector(min_resolution)
            
            ydl_opts = {
                'format': format_str,
                'outtmpl': str(temp_path),
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 25,  # Увеличен timeout
                'retries': 3,  # Больше retry
                'fragment_retries': 5,  # Больше retry для фрагментов
                'extractor_retries': 3,  # Больше retry для экстрактора
                'http_chunk_size': 10485760,  # 10MB чанки
                'extractor_args': {
                    'youtube': {
                        'player_client': ['tv_embedded'],
                    }
                },
                'cookiefile': str(cookies_file),
                'concurrent_fragment_downloads': 4,
            }
            
            # Добавляем VPN если есть
            if proxy_url:
                ydl_opts['proxy'] = proxy_url
            
            self.log("   📺 Пробуем TV Embedded...")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            # Проверяем что файл скачался
            if not temp_path.exists() or temp_path.stat().st_size < 1000:
                self.log("   ❌ TV Embedded: Файл пустой или не создан")
                return None
            
            size_mb = temp_path.stat().st_size / (1024 * 1024)
            self.log(f"   📊 TV Embedded: Скачано {size_mb:.1f} MB")
            
            # Проверяем разрешение и ориентацию
            dimensions = probe_video_dimensions(temp_path)
            if not dimensions:
                self.log("   ❌ Не удалось подтвердить разрешение")
                temp_path.unlink(missing_ok=True)
                return None
            dl_width, dl_height = dimensions
            if dl_height < min_resolution:
                self.log(f"   ⚠️ Разрешение {dl_height}p < {min_resolution}p")
                temp_path.unlink(missing_ok=True)
                return None
            if not matches_target_orientation(dl_width, dl_height, target_orientation):
                self.log("   ⚠️ Неправильная ориентация (вертикальное вместо горизонтального)")
                temp_path.unlink(missing_ok=True)
                return None
            self.log(f"   📐 Разрешение: {dl_width}x{dl_height}")
            
            # Валидация видео
            if not validate_video_file(str(temp_path), deep_check=True):
                self.log("   🔧 Видео повреждено, попытка восстановления...")
                repaired = repair_video_file(str(temp_path), self.log)
                if not repaired:
                    self.log("   ❌ Не удалось восстановить видео")
                    temp_path.unlink(missing_ok=True)
                    return None
            
            # Переименовываем в финальный файл
            final_path = output_dir / f"{video_id}.mp4"
            try:
                if final_path.exists():
                    final_path.unlink()
                temp_path.rename(final_path)
            except Exception:
                shutil.copy2(temp_path, final_path)
                temp_path.unlink(missing_ok=True)
            
            self.log("✅ TV Embedded: Видео успешно скачано")
            return str(final_path)
            
        except Exception as e:
            error_msg = str(e)
            if 'not available' in error_msg.lower():
                self.log("   ❌ TV Embedded: Видео недоступно")
            elif 'private' in error_msg.lower():
                self.log("   ❌ TV Embedded: Видео приватное")
            elif 'timeout' in error_msg.lower():
                self.log("   ⏱️ TV Embedded: Timeout")
            else:
                self.log(f"   ❌ TV Embedded: {error_msg[:150]}")
            return None
    
    def download_video(
        self,
        video_url: str,
        output_dir: Path,
        video_id: str = None,
        target_orientation: str = 'any',  # 'vertical', 'horizontal', 'any'
        min_resolution: Optional[int] = None,
        max_download_seconds: int = 120,
        allow_fallbacks: bool = True,
    ) -> Optional[str]:
        """
        РЎРєР°С‡РёРІР°РµС‚ РІРёРґРµРѕ СЃ YouTube С‡РµСЂРµР· yt_dlp Python API.
        
        рџ”§ FIX: РСЃРїРѕР»СЊР·СѓРµС‚ import yt_dlp РІРјРµСЃС‚Рѕ subprocess РґР»СЏ СЂРµС€РµРЅРёСЏ
        РїСЂРѕР±Р»РµРјС‹ NAL errors РІ PyQt5 GUI (РїРµСЂРµРїРѕР»РЅРµРЅРёРµ Р±СѓС„РµСЂР° pipes).
        
        Args:
            target_orientation: Р¤РёР»СЊС‚СЂ РїРѕ РѕСЂРёРµРЅС‚Р°С†РёРё:
                - 'horizontal': С‚РѕР»СЊРєРѕ РіРѕСЂРёР·РѕРЅС‚Р°Р»СЊРЅС‹Рµ (width > height)
                - 'vertical': РІРµСЂС‚РёРєР°Р»СЊРЅС‹Рµ Р РіРѕСЂРёР·РѕРЅС‚Р°Р»СЊРЅС‹Рµ (РґР»СЏ С€РѕСЂС‚СЃРѕРІ)
                - 'any': Р»СЋР±С‹Рµ
            min_resolution: РњРёРЅРёРјР°Р»СЊРЅРѕРµ СЂР°Р·СЂРµС€РµРЅРёРµ РїРѕ РІС‹СЃРѕС‚Рµ (720 РёР»Рё 480)
        """
        import yt_dlp

        min_resolution = normalize_youtube_min_height(
            self._minimum_youtube_height() if min_resolution is None else min_resolution
        )
        
        if not video_id:
            video_id = hashlib.md5(video_url.encode()).hexdigest()[:8]

        cached_failure = _youtube_failed_downloads.lookup(
            video_id,
            connection_signature=_youtube_connection_signature(),
        )
        if cached_failure:
            self._record_download_failure(video_id, cached_failure, remember=False)
            self.log(
                f"⏭️ {video_id}: повтор пропущен — ранее {cached_failure.code}"
            )
            return None
        
        output_path = output_dir / f"{video_id}.mp4"
        temp_path = output_dir / f"{video_id}_downloading.mp4"
        download_started = time.monotonic()
        download_deadline = download_started + max(15, int(max_download_seconds))

        def safe_unlink(path) -> None:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

        def cleanup_download_artifacts() -> None:
            for pattern in (f"temp_{video_id}.*", f"temp_{video_id}_*.*"):
                for artifact in output_dir.glob(pattern):
                    safe_unlink(artifact)

        def ensure_download_time() -> None:
            if time.monotonic() >= download_deadline:
                raise TimeoutError(
                    f"YouTube download exceeded {max_download_seconds}s for {video_id}"
                )

        def deadline_progress_hook(_status: Dict) -> None:
            ensure_download_time()
        
        # DISABLED: get_batch_cached_video was removed
        # batch_cached = get_batch_cached_video(video_id)
        # if batch_cached and Path(batch_cached).exists():
        #     if str(output_path) != batch_cached:
        #         try:
        #             shutil.copy2(batch_cached, output_path)
        #             self.log("📦 Использовано видео из кэша")
        #             return str(output_path)
        #         except Exception:
        #             pass
        #     else:
        #         self.log("📦 Найдено видео в кэше")
        #         return batch_cached
        
        if output_path.exists() and output_path.stat().st_size > 10000:
            cached_dimensions = probe_video_dimensions(output_path)
            if not cached_dimensions:
                self.log("⚠️ Не удалось проверить кэш, удаляем")
                safe_unlink(output_path)
            else:
                cached_width, cached_height = cached_dimensions
                orientation_ok = matches_target_orientation(
                    cached_width, cached_height, target_orientation
                )
                if cached_height >= min_resolution and orientation_ok:
                    self.log(f"✅ Кэш подходит ({cached_width}x{cached_height})")
                    return str(output_path)
                self.log(
                    f"⚠️ Кэш не подходит "
                    f"({cached_width}x{cached_height}, {target_orientation}); удаляем"
                )
                safe_unlink(output_path)

        if self._youtube_downloads_temporarily_blocked():
            global _youtube_block_notice_logged, _youtube_block_reason
            with _youtube_access_lock:
                if not _youtube_block_notice_logged:
                    if _youtube_block_reason == "rate_limited":
                        self.log(
                            "⏱️ YouTube ограничил частоту запросов; новые попытки "
                            "в этой сессии временно остановлены"
                        )
                    else:
                        self.log(
                            "⏱️ YouTube запросил антибот-подтверждение; новые "
                            "попытки в этой сессии временно остановлены"
                        )
                    _youtube_block_notice_logged = True
            return None
        
        ffmpeg_location = None
        ffmpeg_exe = shutil.which("ffmpeg")
        if ffmpeg_exe:
            ffmpeg_location = os.path.dirname(ffmpeg_exe)
        
        cookies_file = Path(__file__).parent.parent / "youtube_cookies.txt"
        has_cookies_file = ensure_youtube_cookies(
            str(cookies_file),
            self.log,
            auto_export=True,
        ) is not None
        # yt-dlp's current default negotiation must stay first.  A stale system
        # profile used to force Android together with browser cookies, which
        # reproducibly hid ordinary HD formats behind SABR-only responses.
        download_strategies = build_youtube_client_strategies(has_cookies_file)

        age_restricted_detected = False
        cookies_refresh_attempted = False
        last_failure: Optional[YouTubeDownloadFailure] = None
        failure_codes_seen = set()
        
        # Пробуем каждую стратегию по очереди
        for strategy_index, strategy in enumerate(download_strategies, 1):
            if strategy.required_failure_codes and not (
                set(strategy.required_failure_codes) & failure_codes_seen
            ):
                continue
            # Public videos are negotiated without browser state.  Supplying
            # cookies merely because a file exists can change YouTube's player
            # response and hide otherwise available formats.  Session cookies
            # are therefore used only after an explicit login/age gate.
            if strategy.use_cookies and not age_restricted_detected:
                continue
            if age_restricted_detected and not strategy.use_cookies:
                if has_cookies_file:
                    continue
            attempt_cookie_file = None
            use_cookies_for_attempt = bool(
                has_cookies_file and strategy.use_cookies
            )
            try:
                ensure_download_time()
            except TimeoutError as e:
                self.log(f"⏱️ {e}")
                cleanup_download_artifacts()
                return None
            self.log(
                f"⬇️ HD-источник ({min_resolution}p+): {strategy.name} "
                f"[{strategy_index}/{len(download_strategies)}]"
            )
            
            try:
                # Очищаем старые временные файлы
                for old_pattern in (f"temp_{video_id}.*", f"temp_{video_id}_*.*"):
                    for old_temp in output_dir.glob(old_pattern):
                        safe_unlink(old_temp)
                
                attempt_token = f"{strategy_index}_{int(time.monotonic() * 1000)}_{threading.get_ident()}"
                temp_template = str(output_dir / f"temp_{video_id}_{attempt_token}.%(ext)s")
                
                # 🌐 Автоматическое определение VPN/Proxy
                proxy_url = _get_auto_proxy(self.log)
                
                ydl_opts = {
                    'format': build_youtube_format_selector(
                        min_resolution,
                        prefer_hls=strategy.prefer_hls,
                    ),
                    'outtmpl': temp_template,
                    'noplaylist': True,
                    'quiet': True,
                    'no_warnings': True,
                    'socket_timeout': 20,
                    'retries': 1,
                    'fragment_retries': 1,
                    'extractor_retries': 1,
                    'file_access_retries': 1,
                    'abort_on_unavailable_fragments': True,
                    'concurrent_fragment_downloads': 2,
                    'http_chunk_size': 10485760,  # 10MB
                    'noprogress': True,
                    'continuedl': False,
                    'sleep_interval_requests': 1.0,
                    'nocheckcertificate': True,
                    'merge_output_format': 'mp4',
                    'postprocessors': [{
                        'key': 'FFmpegVideoRemuxer',
                        'preferedformat': 'mp4',
                    }],
                    'ffmpeg_location': ffmpeg_location,
                    'cachedir': False,
                    'progress_hooks': [deadline_progress_hook],
                }
                ydl_opts.update(build_youtube_js_runtime_options())
                if strategy.player_clients:
                    ydl_opts['extractor_args'] = {
                        'youtube': {'player_client': list(strategy.player_clients)}
                    }
                
                # Добавляем прокси если найден
                if proxy_url:
                    ydl_opts['proxy'] = proxy_url
                    self.log(
                        f"   🌐 yt-dlp использует прокси: {describe_proxy(proxy_url)}"
                    )
                
                # Каждый параллельный yt-dlp получает личную копию cookies.
                # yt-dlp сохраняет cookie jar при закрытии и иначе потоки могут
                # повредить общий Netscape-файл.
                if use_cookies_for_attempt:
                    attempt_cookie_file = create_isolated_cookie_snapshot(
                        str(cookies_file),
                        str(output_dir),
                        prefix=f"youtube_{video_id}",
                    )
                    if attempt_cookie_file:
                        ydl_opts['cookiefile'] = str(attempt_cookie_file)
                        self.log("   🍪 yt-dlp использует изолированную копию cookies")
                    else:
                        use_cookies_for_attempt = False
                
                # One extraction negotiates metadata/formats and downloads the
                # already selected streams.  The old extract_info(False) +
                # download(url) sequence repeated the whole player request and
                # doubled the rate-limit pressure for every source.
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        ensure_download_time()
                        info = ydl.extract_info(video_url, download=True)
                        ensure_download_time()
                    except Exception as e:
                        err_str = str(e)
                        if is_youtube_login_or_age_gate(err_str):
                            age_restricted_detected = True
                        raise  # Пробрасываем ошибку дальше
                    
                    if not info:
                        last_failure = YouTubeDownloadFailure(
                            "empty_metadata",
                            "yt-dlp не вернул метаданные",
                            retry_with_another_client=True,
                            cache_ttl_seconds=120,
                        )
                        continue

                    requested_formats = info.get('requested_formats') or []
                    negotiated_heights = sorted({
                        int(item.get('height') or 0)
                        for item in requested_formats
                        if isinstance(item, dict) and int(item.get('height') or 0) > 0
                    })
                    negotiated_format = str(info.get('format_id') or "auto")
                    height_note = (
                        ",".join(f"{height}p" for height in negotiated_heights)
                        if negotiated_heights else "проверка после загрузки"
                    )
                    self.log(
                        f"   🎛️ Формат согласован: {negotiated_format}; {height_note}"
                    )
                    
                    # Duration is checked again even though discovery already
                    # applies bounds; direct callers may bypass discovery.
                    _mx_cfg = getattr(self, 'mixer_settings', None) or {}
                    _max_dur = int(_mx_cfg.get('max_video_duration_seconds', 3600))
                    _min_dur = int(_mx_cfg.get('min_video_duration_seconds', 30))
                    duration = info.get('duration', 0)
                    if duration and duration < _min_dur:
                        self.log(f"⏱️ Видео слишком короткое ({duration}s < {_min_dur}s)")
                        cleanup_download_artifacts()
                        return None
                    if duration and duration > _max_dur:
                        self.log(f"⏱️ Видео слишком длинное ({duration/60:.1f} мин > {_max_dur//60} мин)")
                        cleanup_download_artifacts()
                        return None
                
                # Проверяем что файл скачался
                downloaded_files = [
                    path for path in output_dir.glob(f"temp_{video_id}_{attempt_token}.*")
                    if path.is_file() and not path.name.endswith(('.part', '.ytdl'))
                ]
                downloaded_files.sort(
                    key=lambda path: path.stat().st_size if path.exists() else 0,
                    reverse=True,
                )
                if not downloaded_files:
                    last_failure = YouTubeDownloadFailure(
                        "output_missing",
                        "yt-dlp завершился без выходного файла",
                        retry_with_another_client=True,
                        cache_ttl_seconds=120,
                    )
                    self.log("❌ YouTube[output_missing]: файл не создан")
                    continue
                
                temp_path = downloaded_files[0]
                
                if not temp_path.exists() or temp_path.stat().st_size < 1000:
                    last_failure = YouTubeDownloadFailure(
                        "output_empty",
                        "выходной файл пустой",
                        retry_with_another_client=True,
                        cache_ttl_seconds=120,
                    )
                    self.log("❌ YouTube[output_empty]: файл пустой")
                    continue
                
                # Проверяем разрешение и ориентацию
                dimensions = probe_video_dimensions(temp_path)
                if not dimensions:
                    last_failure = YouTubeDownloadFailure(
                        "probe_failed",
                        "ffprobe не подтвердил параметры видео",
                        retry_with_another_client=True,
                        cache_ttl_seconds=120,
                    )
                    self.log("❌ Не удалось подтвердить разрешение скачанного файла")
                    safe_unlink(temp_path)
                    continue
                dl_width, dl_height = dimensions
                if dl_height < min_resolution:
                    last_failure = YouTubeDownloadFailure(
                        "quality_floor",
                        f"фактическое разрешение {dl_height}p ниже {min_resolution}p",
                        retry_with_another_client=True,
                        cache_ttl_seconds=900,
                    )
                    self.log(
                        f"⚠️ Разрешение {dl_height}p < {min_resolution}p; "
                        "низкокачественный источник удалён"
                    )
                    safe_unlink(temp_path)
                    continue
                if not matches_target_orientation(dl_width, dl_height, target_orientation):
                    last_failure = YouTubeDownloadFailure(
                        "orientation",
                        f"формат {dl_width}x{dl_height} не подходит для {target_orientation}",
                        retry_with_another_client=False,
                        cache_ttl_seconds=900,
                    )
                    self.log("⚠️ Неправильная ориентация (вертикальное вместо горизонтального)")
                    safe_unlink(temp_path)
                    continue
                self.log(f"📐 Разрешение: {dl_width}x{dl_height}")
                
                # Валидация видео
                if not validate_video_file(str(temp_path), deep_check=True):
                    self.log("🔧 Видео повреждено, попытка восстановления")
                    repaired = repair_video_file(str(temp_path), self.log)
                    if not repaired:
                        last_failure = YouTubeDownloadFailure(
                            "invalid_media",
                            "файл повреждён и не восстановлен",
                            retry_with_another_client=True,
                            cache_ttl_seconds=300,
                        )
                        self.log("❌ Не удалось восстановить видео")
                        safe_unlink(temp_path)
                        continue
                
                # Переименовываем в финальный файл
                final_path = output_dir / f"{video_id}.mp4"
                try:
                    if final_path.exists():
                        safe_unlink(final_path)
                    temp_path.rename(final_path)
                except Exception:
                    shutil.copy2(temp_path, final_path)
                    safe_unlink(temp_path)
                
                self.log(f"✅ Видео успешно скачано: {video_id}")
                return str(final_path)
                
            except yt_dlp.utils.DownloadError as e:
                failure = classify_youtube_download_error(e)
                last_failure = failure
                failure_codes_seen.add(failure.code)
                self.log(
                    f"❌ YouTube[{failure.code}] {strategy.name}: {failure.summary}"
                )
                if time.monotonic() >= download_deadline:
                    self.log(f"⏱️ Лимит скачивания {max_download_seconds}s исчерпан")
                    cleanup_download_artifacts()
                    self._record_download_failure(video_id, failure)
                    return None
                if failure.code == "bot_challenge":
                    if use_cookies_for_attempt and not cookies_refresh_attempted:
                        cookies_refresh_attempted = True
                        if refresh_youtube_cookies(str(cookies_file), self.log):
                            reset_youtube_access_state()
                            self.log("   🔄 Cookies обновлены; следующий клиент использует новую сессию")
                            continue
                    self._mark_youtube_bot_block(failure.global_cooldown_seconds)
                    self.log(
                        "   ⏱️ Подтверждена антибот-блокировка YouTube; "
                        "текущие cookies требуют обновления, остальные попытки пропущены"
                    )
                    break
                if failure.code == "rate_limited":
                    self._mark_youtube_rate_limit(failure.global_cooldown_seconds)
                    self.log(
                        "   ⏱️ Лимит запросов подтверждён; пакетные повторы остановлены"
                    )
                    break
                if failure.code == "login_required":
                    age_restricted_detected = True
                    if not use_cookies_for_attempt and has_cookies_file:
                        self.log(
                            "   🔞 YouTube запросил вход; следующая ограниченная "
                            "попытка использует изолированную копию cookies"
                        )
                        continue
                    if use_cookies_for_attempt and not cookies_refresh_attempted:
                        cookies_refresh_attempted = True
                        if refresh_youtube_cookies(str(cookies_file), self.log):
                            reset_youtube_access_state()
                            self.log("   🔄 Повторяем один раз с обновлёнными cookies")
                            continue
                    self.log("   🔞 Источник требует вход/возрастную проверку YouTube; добавлен в blacklist и пропущен")
                    add_to_blacklist(video_id=video_id)
                    cleanup_download_artifacts()
                    self._record_download_failure(video_id, failure)
                    return None
                if not failure.retry_with_another_client:
                    break
                continue
                
            except Exception as e:
                failure = classify_youtube_download_error(e)
                last_failure = failure
                failure_codes_seen.add(failure.code)
                self.log(
                    f"❌ YouTube[{failure.code}] {strategy.name}: {failure.summary}"
                )
                if time.monotonic() >= download_deadline:
                    self.log(f"⏱️ Лимит скачивания {max_download_seconds}s исчерпан")
                    cleanup_download_artifacts()
                    self._record_download_failure(video_id, failure)
                    return None
                if failure.code == "bot_challenge":
                    if use_cookies_for_attempt and not cookies_refresh_attempted:
                        cookies_refresh_attempted = True
                        if refresh_youtube_cookies(str(cookies_file), self.log):
                            reset_youtube_access_state()
                            continue
                    self._mark_youtube_bot_block(failure.global_cooldown_seconds)
                    break
                if failure.code == "rate_limited":
                    self._mark_youtube_rate_limit(failure.global_cooldown_seconds)
                    break
                if failure.code == "login_required":
                    age_restricted_detected = True
                    if not use_cookies_for_attempt and has_cookies_file:
                        self.log(
                            "   🔞 YouTube запросил вход; следующая ограниченная "
                            "попытка использует изолированную копию cookies"
                        )
                        continue
                    if use_cookies_for_attempt and not cookies_refresh_attempted:
                        cookies_refresh_attempted = True
                        if refresh_youtube_cookies(str(cookies_file), self.log):
                            reset_youtube_access_state()
                            continue
                    self.log("   🔞 Источник требует вход/возрастную проверку YouTube; добавлен в blacklist и пропущен")
                    add_to_blacklist(video_id=video_id)
                    cleanup_download_artifacts()
                    self._record_download_failure(video_id, failure)
                    return None
                if not failure.retry_with_another_client:
                    break
                continue
            finally:
                if attempt_cookie_file:
                    attempt_cookie_file.unlink(missing_ok=True)

        cleanup_download_artifacts()
        if not last_failure:
            last_failure = YouTubeDownloadFailure(
                "unknown",
                "ни одно согласование YouTube не создало пригодный файл",
                cache_ttl_seconds=120,
            )
        self._record_download_failure(video_id, last_failure)
        # Direct client impersonation fallbacks are disabled: extra requests make
        # temporary YouTube blocks last longer and do not improve reliability.
        direct_client_fallbacks_enabled = bool(
            (self.mixer_settings or {}).get('enable_legacy_youtube_fallbacks', False)
        )
        if (
            not direct_client_fallbacks_enabled
            or not allow_fallbacks
            or time.monotonic() >= download_deadline
            or self._youtube_downloads_temporarily_blocked()
        ):
            self.log(
                f"⏱️ YouTube завершён без файла: {last_failure.code}; "
                "медленные legacy-методы отключены"
            )
            return None
        
        # 🔄 FALLBACK: InnerTube API (прямые запросы к YouTube)
        self.log("\n" + "="*60)
        self.log("🔄 FALLBACK: Пробуем InnerTube API (прямые запросы)")
        self.log("="*60)
        
        innertube_result = self._try_innertube_download(
            video_id, video_url, output_dir, min_resolution, target_orientation
        )
        
        if innertube_result:
            return innertube_result
        
        # 🔄 FALLBACK: TV Embedded (высокое качество с cookies)
        self.log("\n" + "="*60)
        self.log("🔄 FALLBACK: Пробуем TV Embedded (высокое качество)")
        self.log("="*60)
        
        tv_result = self._try_tv_embedded_download(
            video_id, video_url, output_dir, min_resolution, target_orientation
        )
        
        if tv_result:
            return tv_result
        
        # 🔄 FALLBACK: Android Client (без cookies)
        self.log("\n" + "="*60)
        self.log("🔄 FALLBACK: Пробуем Android Client (БЕЗ cookies)")
        self.log("="*60)
        
        android_result = self._try_android_download(
            video_id, video_url, output_dir, min_resolution, target_orientation
        )
        
        if android_result:
            return android_result
        
        # Если все стратегии провалились
        self.log("\n" + "="*60)
        self.log("❌ ВСЕ МЕТОДЫ СКАЧИВАНИЯ ПРОВАЛИЛИСЬ")
        self.log("="*60)
        
        # Проверяем причину провала
        if age_restricted_detected:
            if not has_cookies_file:
                self.log("\n🔞 ПРИЧИНА: Видео с возрастным ограничением")
                self.log("💡 РЕШЕНИЕ: Добавьте youtube_cookies.txt")
                self.log("📖 Инструкция: docs/YOUTUBE_COOKIES_UPDATE_GUIDE.md")
            else:
                self.log("\n🔞 Видео с возрастным ограничением недоступно даже с cookies")
        else:
            # Возможная блокировка IP
            self.log("\n🚫 ВОЗМОЖНАЯ ПРИЧИНА: YouTube заблокировал ваш IP как бота")
            self.log("\n💡 РЕШЕНИЯ:")
            self.log("   1. Добавьте cookies (youtube_cookies.txt) - РЕКОМЕНДУЕТСЯ")
            self.log("   2. Включите VPN/Proxy")
            self.log("   3. Подождите 24-48 часов")
            self.log("   4. Используйте другую сеть (мобильный интернет)")
            self.log("\n📖 Инструкции:")
            self.log("   • Cookies: docs/YOUTUBE_COOKIES_UPDATE_GUIDE.md")
            self.log("   • Быстрый старт: _dev/QUICK_START_UNIVERSAL_DOWNLOAD.md")
            
            if not has_cookies_file:
                self.log("\n⚠️ У вас НЕТ cookies - это снижает шансы на успех!")
        
        self.log("="*60)
        return None


    def extract_random_clips(
        self,
        video_path: str,
        output_dir: Path,
        num_clips: int = 5,
        min_clip_duration: float = 3.0,
        max_clip_duration: float = 8.0,
        avoid_start_percent: float = 0.1,
        avoid_end_percent: float = 0.1,
        existing_clips: List[str] = None,
        use_smart_selection: bool = True,  # V2:   
        avoid_faces: bool = True,  # V2:   
        avoid_subtitles: bool = True  # V2:   
    ) -> List[str]:
        """
           .
        
        V2 :
        -    ( ,  )
        -     
        -     
        
        Args:
            video_path:    
            output_dir:   
            num_clips:  
            min_clip_duration:    ()
            max_clip_duration:    ()
            avoid_start_percent:      ()
            avoid_end_percent:      ()
            existing_clips:    
            use_smart_selection:    
            avoid_faces:     
            avoid_subtitles:     
            
        Returns:
               
        """
        import ffmpeg
        import subprocess
        
        # 🔧 ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ
        self.log(f"✂️ Начинаю нарезку клипов из {Path(video_path).name}...")
        extract_start = time.time()
        
        #    ( retry  fallback )
        duration = None
        
        #  1: ffmpeg-python
        for probe_attempt in range(3):
            try:
                probe = ffmpeg.probe(video_path)
                duration = float(probe['format']['duration'])
                break
            except Exception as e:
                if probe_attempt < 2:
                    self.log(f"    ffprobe  {probe_attempt + 1}/3  : {str(e)[:50]}")
                    import time as time_module
                    time_module.sleep(1)
        
        #  2:   ffprobe     
        if duration is None or duration <= 0:
            try:
                result = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0 and result.stdout.strip():
                    duration = float(result.stdout.strip())
                    self.log(f"   ✅ Длительность видео (ffprobe): {duration:.1f}s")
            except Exception as e:
                self.log(f"     ffprobe   : {str(e)[:50]}")
        
        if duration is None or duration <= 0:
            self.log(f"      : {duration}")
            return []
        
        #    ( /)
        # Добавляем небольшой джиттер к началу, чтобы клипы из одного видео 
        # в разных генерациях начинались по-разному
        start_jitter = random.uniform(0, 0.05) * duration
        safe_start = duration * avoid_start_percent + start_jitter
        safe_end = duration * (1 - avoid_end_percent)
        safe_duration = safe_end - safe_start
        
        if safe_duration < min_clip_duration * 2:
            self.log("        ")
            return []
        
        clips = []
        used_ranges = []
        
        video_name = Path(video_path).stem
        
        #   
        existing_clip_names = set()
        if existing_clips:
            for clip in existing_clips:
                existing_clip_names.add(Path(clip).name)
        
        interesting_moments = []
        # A bounded scan improves clip quality without inspecting the whole video.
        if use_smart_selection and duration > 30:
            try:
                self.log("🎯 Поиск интересных моментов в видео")
                interesting_moments = find_interesting_moments(
                    video_path,
                    num_moments=max(num_clips * 2, 6),
                    max_samples=min(48, max(18, num_clips * 5)),
                    log_callback=self.log
                )
                if interesting_moments:
                    self.log(f"   ?  {len(interesting_moments)}  ")
            except Exception as e:
                self.log(f"       : {e}")
        
        #     
        segment_duration = safe_duration / max(num_clips, 1)
        
        #   
        faces_filtered = 0
        subtitles_filtered = 0
        slow_clip_qa_enabled = os.environ.get('CONTENTBOT_ENABLE_SLOW_CLIP_QA', '').strip().lower() in {
            '1', 'true', 'yes', 'on'
        }
        qa_avoid_faces = bool(avoid_faces and slow_clip_qa_enabled)
        qa_avoid_subtitles = bool(avoid_subtitles and slow_clip_qa_enabled)
        analyze_clip_for_faces = None
        check_clip_for_subtitles = None

        if qa_avoid_faces or qa_avoid_subtitles:
            try:
                from core.youtube.analysis import analyze_clip_for_faces as _analyze_clip_for_faces
                from core.youtube.analysis import check_clip_for_subtitles as _check_clip_for_subtitles
                analyze_clip_for_faces = _analyze_clip_for_faces
                check_clip_for_subtitles = _check_clip_for_subtitles
            except Exception as e:
                self.log(f"   ⚠️ Clip QA filters unavailable, continuing without them: {e}")
                qa_avoid_faces = False
                qa_avoid_subtitles = False
        elif avoid_faces or avoid_subtitles:
            self.log("   ℹ️ Heavy face/subtitle clip QA is off for speed")

        from core.visual_source_manager import reserve_least_used_clip_range
        
        for i in range(num_clips):
            self.log(f"      📹 Нарезка клипа {i+1}/{num_clips}...")
            clip_duration = random.uniform(min_clip_duration, max_clip_duration)
            
            start_time = None
            
            if interesting_moments and use_smart_selection:
                #      
                segment_start = safe_start + i * segment_duration
                segment_end = segment_start + segment_duration
                
                for moment in interesting_moments:
                    moment_time = moment['time']
                    if segment_start <= moment_time <= segment_end:
                        candidate_start = max(safe_start, moment_time - clip_duration / 2)
                        candidate_end = candidate_start + clip_duration
                        
                        #  
                        overlap = False
                        for used_start, used_end in used_ranges:
                            if not (candidate_end + 2 < used_start or candidate_start > used_end + 2):
                                overlap = True
                                break
                        
                        if overlap:
                            continue
                        
                        # ОПТИМИЗАЦИЯ: Отключаем проверки для скорости
                        # if avoid_faces:
                        #     face_info = analyze_clip_for_faces(
                        #         video_path, candidate_start, clip_duration, num_samples=3
                        #     )
                        #     if face_info['has_large_faces']:
                        #         faces_filtered += 1
                        #         continue
                        # 
                        # if avoid_subtitles:
                        #     if check_clip_for_subtitles(video_path, candidate_start, clip_duration):
                        #         subtitles_filtered += 1
                        #         continue
                        if qa_avoid_faces and analyze_clip_for_faces:
                            face_info = analyze_clip_for_faces(
                                video_path, candidate_start, clip_duration, num_samples=3
                            )
                            if face_info['has_large_faces']:
                                faces_filtered += 1
                                continue

                        if qa_avoid_subtitles and check_clip_for_subtitles:
                            if check_clip_for_subtitles(video_path, candidate_start, clip_duration):
                                subtitles_filtered += 1
                                continue

                        start_time = candidate_start
                        interesting_moments.remove(moment)  #  
                        break
            
            # Fallback: Случайный выбор в сегменте с джиттером
            if start_time is None:
                # Добавляем случайное смещение к сегменту, чтобы избежать "одинаковых" начал
                jitter = random.uniform(-segment_duration * 0.2, segment_duration * 0.2)
                segment_start = max(safe_start, safe_start + i * segment_duration + jitter)
                segment_end = min(safe_end - clip_duration, segment_start + segment_duration)
                
                if segment_end <= segment_start:
                    segment_end = segment_start + 1
                
                #    ( 10 )
                for attempt in range(10):
                    candidate_start = random.uniform(segment_start, min(segment_end, safe_end - clip_duration))
                    candidate_end = candidate_start + clip_duration
                    
                    #  
                    overlap = False
                    for used_start, used_end in used_ranges:
                        if not (candidate_end + 2 < used_start or candidate_start > used_end + 2):
                            overlap = True
                            break
                    
                    if overlap:
                        continue
                    
                    # ОПТИМИЗАЦИЯ: Отключаем проверки лиц и субтитров для скорости
                    # Это экономит 5-15 секунд на видео
                    # if avoid_faces and attempt < 3:
                    #     face_info = analyze_clip_for_faces(
                    #         video_path, candidate_start, clip_duration, num_samples=3
                    #     )
                    #     if face_info['has_large_faces']:
                    #         faces_filtered += 1
                    #         continue
                    # 
                    # if avoid_subtitles and attempt < 3:
                    #     if check_clip_for_subtitles(video_path, candidate_start, clip_duration):
                    #         subtitles_filtered += 1
                    #         continue
                    if qa_avoid_faces and analyze_clip_for_faces and attempt < 3:
                        face_info = analyze_clip_for_faces(
                            video_path, candidate_start, clip_duration, num_samples=3
                        )
                        if face_info['has_large_faces']:
                            faces_filtered += 1
                            continue

                    if qa_avoid_subtitles and check_clip_for_subtitles and attempt < 3:
                        if check_clip_for_subtitles(video_path, candidate_start, clip_duration):
                            subtitles_filtered += 1
                            continue

                    start_time = candidate_start
                    break
            
            if start_time is None:
                continue

            # Parallel workers used to rediscover the same first "interesting"
            # moment in a donor. Offer a broad, stratified candidate set to the
            # video-session allocator and atomically reserve a unique time range.
            range_candidates = [(start_time, start_time + clip_duration)]
            for moment in interesting_moments:
                try:
                    moment_start = max(safe_start, float(moment['time']) - clip_duration / 2)
                    moment_start = min(moment_start, safe_end - clip_duration)
                    if moment_start >= safe_start:
                        range_candidates.append((moment_start, moment_start + clip_duration))
                except (KeyError, TypeError, ValueError):
                    continue

            # A dense stratified set lets the per-video allocator find remaining
            # free gaps even late in a long timeline.
            candidate_slots = max(48, num_clips * 8)
            available_start_span = max(0.0, safe_end - clip_duration - safe_start)
            if available_start_span > 0:
                bucket_width = available_start_span / candidate_slots
                for slot in range(candidate_slots):
                    bucket_start = safe_start + slot * bucket_width
                    bucket_end = min(
                        safe_end - clip_duration,
                        bucket_start + max(bucket_width, 0.001),
                    )
                    candidate_start = random.uniform(bucket_start, bucket_end)
                    range_candidates.append(
                        (candidate_start, candidate_start + clip_duration)
                    )

            reserved_range = reserve_least_used_clip_range(
                video_path,
                range_candidates,
                padding=min(1.0, max(0.25, clip_duration * 0.15)),
                session_id=getattr(self, "visual_session_id", None),
            )
            if not reserved_range:
                self.log(
                    f"      ⚠️ В {Path(video_path).name} не осталось "
                    "непересекающихся диапазонов нужной длины"
                )
                continue
            start_time, reserved_end = reserved_range
            clip_duration = reserved_end - start_time
            
            end_time = start_time + clip_duration
            used_ranges.append((start_time, end_time))
            
            start_ms = int(start_time * 1000)
            unique_suffix = f"{random.getrandbits(24):06x}"
            clip_path = output_dir / f"clip_{video_name}_{start_ms:07d}_{i:03d}_{unique_suffix}.mp4"
            
            #       
            if clip_path.name in existing_clip_names:
                self.log(f"     {clip_path.name}  , ")
                continue
            
            #   start_time   
            if start_time >= duration or start_time < 0:
                continue
            
            #  clip_duration    
            actual_clip_duration = min(clip_duration, duration - start_time - 0.5)
            if actual_clip_duration < min_clip_duration:
                continue
            
            try:
                # ОПТИМИЗАЦИЯ: Сначала пробуем быстрый copy codec (в 10-20 раз быстрее!)
                # Только если не работает - перекодируем
                cmd_copy = [
                    'ffmpeg', '-y',
                    '-ss', str(start_time),
                    '-i', video_path,
                    '-t', str(actual_clip_duration),
                    '-c:v', 'copy',
                    '-an',
                    '-loglevel', 'error',
                    str(clip_path)
                ]
                
                result = subprocess.run(cmd_copy, capture_output=True, timeout=30, text=True)
                
                # Если copy не сработал - перекодируем (медленнее, но надежнее)
                if result.returncode != 0 or not clip_path.exists() or clip_path.stat().st_size < 1000:
                    self.log(f"      ⚠️ Copy codec не сработал для клипа {i+1}, перекодирую...")
                    cmd_reencode = [
                        'ffmpeg', '-y',
                        '-ss', str(start_time),
                        '-i', video_path,
                        '-t', str(actual_clip_duration),
                        '-c:v', 'libx264',
                        '-preset', 'veryfast',  # Изменено с ultrafast на veryfast (лучше качество)
                        '-crf', '23',
                        '-movflags', '+faststart',
                        '-an',
                        '-loglevel', 'error',
                        str(clip_path)
                    ]
                    result = subprocess.run(cmd_reencode, capture_output=True, timeout=45, text=True)
                
                if clip_path.exists() and clip_path.stat().st_size > 1000:
                    try:
                        probe_cmd = [
                            'ffprobe', '-v', 'error',
                            '-select_streams', 'v:0',
                            '-show_entries', 'stream=duration,codec_name',
                            '-of', 'json',
                            str(clip_path)
                        ]
                        probe_result = subprocess.run(
                            probe_cmd, capture_output=True, text=True, timeout=10
                        )
                        
                        if probe_result.returncode != 0 or (probe_result.stderr and 'moov atom not found' in probe_result.stderr):
                            self.log("⚠️ Клип поврежден (moov atom not found), пересоздаю...")
                            clip_path.unlink(missing_ok=True)
                            
                            # Используем copy codec для скорости
                            cmd_fix = [
                                'ffmpeg', '-y',
                                '-ss', str(start_time),
                                '-i', video_path,
                                '-t', str(actual_clip_duration),
                                '-c:v', 'libx264',
                                '-preset', 'veryfast',
                                '-crf', '23',
                                '-movflags', '+faststart',
                                '-an',
                                '-loglevel', 'error',
                                str(clip_path)
                            ]
                            subprocess.run(cmd_fix, capture_output=True, timeout=45)
                            
                            #   
                            if not clip_path.exists() or clip_path.stat().st_size < 1000:
                                self.log(f"❌ Не удалось восстановить клип {i+1}")
                                continue
                        
                        # Быстрая проверка валидности (с таймаутом)
                        try:
                            if not validate_video_file(str(clip_path), deep_check=False):  # Отключаем deep_check для скорости
                                self.log(f"⚠️ Клип {i+1} не прошел валидацию, пропускаю")
                                clip_path.unlink(missing_ok=True)
                                continue
                        except Exception as val_error:
                            self.log(f"⚠️ Ошибка валидации клипа {i+1}: {str(val_error)[:30]}")
                            # Оставляем клип, если валидация упала
                        
                        clips.append(str(clip_path))
                        self.log(f"     {i+1}: {start_time:.1f}s-{end_time:.1f}s ({actual_clip_duration:.1f}s)")
                        
                    except Exception as probe_error:
                        self.log(f"       {i+1}: {str(probe_error)[:50]}")
                        #    
                        clip_path.unlink(missing_ok=True)
                        continue
                    
            except subprocess.TimeoutExpired:
                self.log(f"       {i}")
                continue
            except Exception as e:
                self.log(f"      : {str(e)[:50]}")
                continue
        
        if faces_filtered > 0 or subtitles_filtered > 0:
            parts = []
            if faces_filtered > 0:
                parts.append(f" {faces_filtered}  ")
            if subtitles_filtered > 0:
                parts.append(f" {subtitles_filtered}  ")
            self.log(f"   : {', '.join(parts)}")
        
        # 🔧 ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ
        extract_time = time.time() - extract_start
        self.log(f"✅ Нарезка завершена за {extract_time:.1f}s, получено {len(clips)} клипов")
        
        return clips
    
    def download_and_extract_clips(
        self,
        theme: str,
        total_clips_needed: int,
        target_duration_minutes: float,
        output_dir: Path,
        min_clip_duration: float = 3.0,
        max_clip_duration: float = 8.0,
        batch_total_videos: int = 1,
        target_orientation: str = "any",
    ) -> List[str]:
        """
         :     .
        
        Args:
            theme:   
            total_clips_needed:      
            target_duration_minutes:    
            output_dir:   
            min_clip_duration: .  
            max_clip_duration: .  
            batch_total_videos:      (   )
            
        Returns:
               
        """
        if batch_total_videos > 1:
            #  target_duration   
            #    -   30  
            adjusted_duration = min(target_duration_minutes * batch_total_videos * 0.5, 30.0)
            self.log(
                f"📦 План батча: {batch_total_videos} видео, "
                f"лимит исходников {adjusted_duration:.1f} мин"
            )
            target_duration_minutes = adjusted_duration
        
        self.log(f"\n{'='*60}")
        self.log("🎬 YouTube Mixer: Подготовка клипов")
        self.log(f"{'='*60}")
        self.log(f"📝 Тема: {theme}")
        self.log(f"🎞️  Клипов нужно: {total_clips_needed}")
        self.log(f"⏱️  Длительность: ~{target_duration_minutes:.1f} мин")
        
        cache_dir = self._get_cache_dir(theme)
        
        #       
        theme_hash = self._get_cache_key(theme)
        clips_dir = output_dir / "youtube_clips" / theme_hash
        clips_dir.mkdir(parents=True, exist_ok=True)
        
        self.log(f"📁 Папка клипов: youtube_clips/{theme_hash}")
        
        #        
        for old_clip in clips_dir.glob("clip_*.mp4"):
            try:
                #    ffprobe
                probe_cmd = [
                    'ffprobe', '-v', 'error',
                    '-select_streams', 'v:0',
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    str(old_clip)
                ]
                probe_result = subprocess.run(
                    probe_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
                )
                
                #  ffprobe     - 
                if probe_result.returncode != 0 or not probe_result.stdout.strip():
                    self.log(f"   ⚠️ Битый клип удалён: {old_clip.name}")
                    old_clip.unlink(missing_ok=True)
            except Exception:
                #    -  
                old_clip.unlink(missing_ok=True)
        
        #         
        #    YouTube ,   
        
        #      
        for old_clip in clips_dir.glob("clip_*.mp4"):
            try:
                old_clip.unlink(missing_ok=True)
            except Exception:
                pass
        
        self.log("     (  )")
        
        #  
        # Рассчитываем target_seconds ПЕРЕД использованием
        budget = estimate_youtube_source_budget(
            total_clips_needed=total_clips_needed,
            target_duration_minutes=target_duration_minutes,
            min_clip_duration=min_clip_duration,
            max_clip_duration=max_clip_duration,
            batch_total_videos=batch_total_videos,
            target_orientation=target_orientation,
            mixer_settings=self.mixer_settings,
        )
        target_duration_minutes = budget["target_duration_minutes"]
        target_seconds = target_duration_minutes * 60
        self.log(
            f"Smart source budget: {target_duration_minutes:.1f} min, "
            f"{budget['min_videos_to_try']}-{budget['max_videos_to_try']} sources, "
            f"{budget['search_results']} search candidates"
        )
        videos = self.search_youtube_videos(
            theme=theme,
            max_results=int(budget["search_results"]),
            min_duration=240,  # 4 РјРёРЅСѓС‚С‹ РјРёРЅРёРјСѓРј (РѕС‚СЃРµРёРІР°РµС‚ shorts)
            max_duration=1500  #  25 
        )
        
        if not videos:
            self.log(
                "⚠️ Точный поиск не дал видео; "
                "продолжаю по семантической лестнице"
            )
        
        #       
        downloaded_videos = []
        total_downloaded_duration = 0
        slideshow_threshold = int(
            (self.mixer_settings or {}).get('slideshow_threshold', 45)
        )
        all_clips = []
        clips_lock = threading.Lock()
        extraction_futures = []
        extraction_executor = ThreadPoolExecutor(
            max_workers=min(4, max(1, total_clips_needed))
        )
        video_counter = 0  # Счетчик для логирования
        
        # 🎯 Вспомогательная функция для нарезки клипов (Task 0.5)
        def extract_clips_async(video_path_str, video_idx, clips_needed):
            """Нарезает клипы из видео в отдельном потоке."""
            try:
                self.log(f"🎬 Анализ #{video_idx} начат ({Path(video_path_str).name})")
                clips = self.extract_random_clips(
                    video_path=video_path_str,
                    output_dir=clips_dir,
                    num_clips=clips_needed,
                    min_clip_duration=min_clip_duration,
                    max_clip_duration=max_clip_duration,
                    existing_clips=[]  # Будем объединять потом
                )
                self.log(f"✅ Анализ #{video_idx} завершен: {len(clips)} клипов из {Path(video_path_str).name}")
                return clips
            except Exception as e:
                self.log(f"❌ Ошибка анализа #{video_idx}: {str(e)[:100]}")
                return []
        
        self.log("\n🔍 Фильтрация видео (минимум 720p)")
        
        for video in videos:
            if total_downloaded_duration >= target_seconds:
                break
            
            video_path = self.download_video(
                video_url=video['url'],
                output_dir=cache_dir,
                video_id=video['id'],
                target_orientation=target_orientation,
            )
            
            if video_path:
                time.sleep(random.uniform(1.0, 3.0))
                is_slideshow, confidence = is_slideshow_by_motion(
                    video_path, threshold=slideshow_threshold, log_callback=self.log
                )
                
                if is_slideshow and confidence >= 0:
                    self.log(f"      ⚠️ Слайдшоу: {Path(video_path).name} (уверенность: {confidence:.0f}%)")
                    #       
                    try:
                        Path(video_path).unlink(missing_ok=True)
                    except (OSError, PermissionError):
                        pass
                    continue
                
                has_wm, wm_confidence = has_watermark(video_path, threshold=60.0, log_callback=self.log)
                if has_wm and wm_confidence >= 0:
                    self.log(f"      🚫 Водяной знак: {Path(video_path).name} (уверенность: {wm_confidence:.0f}%)")
                    try:
                        Path(video_path).unlink(missing_ok=True)
                    except (OSError, PermissionError):
                        pass
                    continue
                
                if confidence >= 0:
                    self.log(f"      ✅ Видео принято: {Path(video_path).name} (слайдшоу: {confidence:.0f}%)")
                
                # Сохраняем путь И информацию о видео (включая релевантность)
                downloaded_videos.append((video_path, video))
                total_downloaded_duration += video['duration']
                self.log(f"    : {total_downloaded_duration/60:.1f}/{target_duration_minutes:.1f} ")
                
                # 🎯 НОВОЕ: Сразу запускаем нарезку клипов (Task 0.5)
                video_counter += 1
                clips_per_video = max(1, math.ceil(total_clips_needed / len(videos)))
                future = extraction_executor.submit(extract_clips_async, video_path, video_counter, clips_per_video)
                extraction_futures.append(future)
        
        if (
            total_downloaded_duration < target_seconds * 0.5
            and needs_english_search_translation(theme)
        ):
            self.log(f"\n⚠️ Недостаточно видео 720p+ ({total_downloaded_duration/60:.1f} мин), ищем на английском")
            
            english_theme = translate_to_english_ai(theme, api_key=self.api_key, log_callback=self.log)
            if english_theme.lower() != theme.lower():
                self.log(f"     : '{english_theme}'")
                
                english_videos = self.search_youtube_videos(
                    theme=english_theme,
                    max_results=int(budget["search_results"]),
                    min_duration=240,  # 4 РјРёРЅСѓС‚С‹ РјРёРЅРёРјСѓРј
                    max_duration=1500
                )
                
                #   
                seen_ids = {Path(v[0]).stem for v in downloaded_videos}  # v[0] - это video_path
                english_videos = [v for v in english_videos if v['id'] not in seen_ids]
                
                for video in english_videos:
                    if total_downloaded_duration >= target_seconds:
                        break
                    
                    video_path = self.download_video(
                        video_url=video['url'],
                        output_dir=cache_dir,
                        video_id=video['id'],
                        target_orientation=target_orientation,
                    )
                    
                    if video_path:

                    

                    
                        time.sleep(random.uniform(1.0, 3.0))
                        is_slideshow, confidence = is_slideshow_by_motion(
                            video_path, threshold=slideshow_threshold, log_callback=self.log
                        )
                        
                        if is_slideshow and confidence >= 0:
                            self.log(f"      ⚠️ Слайдшоу (EN): {Path(video_path).name} (уверенность: {confidence:.0f}%)")
                            try:
                                Path(video_path).unlink(missing_ok=True)
                            except (OSError, PermissionError):
                                pass
                            continue
                        
                        has_wm, wm_confidence = has_watermark(video_path, threshold=60.0, log_callback=self.log)
                        if has_wm and wm_confidence >= 0:
                            self.log(f"      🚫 Водяной знак (EN): {Path(video_path).name} (уверенность: {wm_confidence:.0f}%)")
                            try:
                                Path(video_path).unlink(missing_ok=True)
                            except (OSError, PermissionError):
                                pass
                            continue
                        
                        if confidence >= 0:
                            self.log(f"      ✅ Видео принято (EN): {Path(video_path).name} (слайдшоу: {confidence:.0f}%)")
                        
                        # Сохраняем путь И информацию о видео
                        downloaded_videos.append((video_path, video))
                        total_downloaded_duration += video['duration']
                        self.log(f"     (EN): {total_downloaded_duration/60:.1f}/{target_duration_minutes:.1f} ")
                        
                        # 🎯 НОВОЕ: Сразу запускаем нарезку клипов (Task 0.5)
                        video_counter += 1
                        clips_per_video = max(1, math.ceil(total_clips_needed / len(videos)))
                        future = extraction_executor.submit(extract_clips_async, video_path, video_counter, clips_per_video)
                        extraction_futures.append(future)
        
        if not downloaded_videos:
            self.log("❌ Не удалось скачать видео (требуется 720p+)")
            extraction_executor.shutdown(wait=False)
            return []
        
        # 🎯 НОВОЕ: Собираем результаты нарезки клипов (Task 0.5)
        self.log("\n⏳ Ожидание завершения нарезки клипов")
        
        try:
            for future in as_completed(extraction_futures, timeout=300):  # 5 минут таймаут
                try:
                    clips = future.result()
                    with clips_lock:
                        all_clips.extend(clips)
                except Exception as e:
                    self.log(f"❌ Ошибка получения результата: {str(e)[:100]}")
        except Exception as e:
            self.log(f"⚠️ Таймаут или ошибка при сборе результатов: {str(e)[:100]}")
        finally:
            extraction_executor.shutdown(wait=True)
        
        #   
        self.log(f"\n✅ Итого: {len(all_clips)} клипов готово")
        self.log(f"{'='*60}\n")
        
        return interleave_clips_by_source(all_clips, total_clips_needed)
    
    def download_videos_parallel(
        self,
        videos: List[Dict],
        cache_dir: Path,
        max_concurrent: int = 1,
        target_orientation: str = 'any',  # 'vertical', 'horizontal', 'any'
        target_duration_minutes: float = 0,
    ) -> List[Tuple[str, Dict]]:
        """
              concurrency.
        
        Args:
            videos:       (id, url, duration, title)
            cache_dir:   
            max_concurrent:    (default: 2)
            target_orientation:    ('vertical', 'horizontal', 'any')
            
        Returns:
              (__, video_info)   
        """
        if not videos:
            return []

        with self._download_failure_lock:
            self._last_download_failures = {}
        
        self.log(f"⬇️ Загрузка {len(videos)} видео (параллельно: {max_concurrent})...")
        min_source_height = self._minimum_youtube_height()
        self.log(f"🎯 Контроль качества YouTube: не ниже {min_source_height}p")
        
        downloaded = []
        bot_error_count = 0
        download_semaphore = threading.Semaphore(max_concurrent)
        per_video_timeouts = {
            str(video.get('id') or index): estimate_youtube_download_timeout(
                video,
                self.mixer_settings,
                target_duration_minutes=target_duration_minutes,
            )
            for index, video in enumerate(videos)
        }
        def download_with_semaphore(video: Dict) -> Tuple[Optional[str], Dict]:
            """       concurrency."""
            nonlocal bot_error_count
            with download_semaphore:
                timeout_seconds = per_video_timeouts.get(str(video.get('id')), 180)
                slot_wait = max(5, min(45, int(timeout_seconds * 0.25)))
                acquired_global_slot = _youtube_global_download_slots.acquire(
                    timeout=slot_wait
                )
                if not acquired_global_slot:
                    failure = YouTubeDownloadFailure(
                        "global_capacity",
                        f"глобальный слот yt-dlp не освободился за {slot_wait}s",
                        cache_ttl_seconds=15,
                    )
                    self._record_download_failure(
                        str(video.get('id') or ''), failure, remember=False
                    )
                    return (None, video)
                try:
                    video_path = self.download_video(
                        video_url=video['url'],
                        output_dir=cache_dir,
                        video_id=video['id'],
                        target_orientation=target_orientation,
                        min_resolution=min_source_height,
                        max_download_seconds=timeout_seconds,
                        allow_fallbacks=False,
                    )
                    return (video_path, video)
                except Exception as e:
                    err_str = str(e)
                    self.log(f"      {video['id']}: {err_str[:50]}")
                    if "Sign in to confirm" in err_str or "bot" in err_str.lower():
                        bot_error_count += 1
                    return (None, video)
                finally:
                    _youtube_global_download_slots.release()
        
        # A real batch deadline is required: as_completed without timeout used
        # to wait forever despite calculating and logging a budget.
        configured_batch_timeout = int(
            (self.mixer_settings or {}).get('download_batch_timeout_seconds', 0) or 0
        )
        if configured_batch_timeout > 0:
            batch_timeout = max(30, min(900, configured_batch_timeout))
        else:
            batch_timeout = max(
                90,
                min(300, max(per_video_timeouts.values(), default=180) + 45),
            )
        self.log(
            f"⏱️ Download budget: per video "
            f"{min(per_video_timeouts.values(), default=0)}-"
            f"{max(per_video_timeouts.values(), default=0)}s, "
            f"batch={batch_timeout}s"
        )

        executor = ThreadPoolExecutor(max_workers=max_concurrent)
        futures = {
            executor.submit(download_with_semaphore, video): video
            for video in videos
        }
        try:
            for future in as_completed(futures, timeout=batch_timeout):
                try:
                    video_path, video_info = future.result()
                    if video_path:
                        try:
                            duration_cmd = [
                                'ffprobe', '-v', 'error',
                                '-show_entries', 'format=duration',
                                '-of', 'csv=p=0',
                                video_path
                            ]
                            dur_result = subprocess.run(
                                duration_cmd, capture_output=True, text=True, 
                                timeout=10, encoding='utf-8', errors='ignore'
                            )
                            if dur_result.returncode == 0 and dur_result.stdout.strip():
                                video_duration = float(dur_result.stdout.strip())
                                configured_min_duration = max(
                                    1,
                                    int((self.mixer_settings or {}).get(
                                        'min_video_duration_seconds', 30
                                    )),
                                )
                                configured_max_duration = max(
                                    configured_min_duration,
                                    int((self.mixer_settings or {}).get(
                                        'max_video_duration_seconds', 3600
                                    )),
                                )
                                if video_duration < configured_min_duration:
                                    self.log(
                                        f"⏱️ Видео слишком короткое "
                                        f"({video_duration}s < {configured_min_duration}s)"
                                    )
                                    Path(video_path).unlink(missing_ok=True)
                                    continue
                                if video_duration > configured_max_duration:
                                    self.log(
                                        f"⏱️ Видео слишком длинное "
                                        f"({video_duration/60:.1f} мин > "
                                        f"{configured_max_duration/60:.1f} мин)"
                                    )
                                    Path(video_path).unlink(missing_ok=True)
                                    continue
                        except Exception:
                            file_size_mb = Path(video_path).stat().st_size / (1024 * 1024)
                            if file_size_mb < 2.0:
                                self.log(f"⚠️ Файл слишком маленький ({file_size_mb:.1f}MB < 2MB)")
                                Path(video_path).unlink(missing_ok=True)
                                continue

                        # This gate is deliberately outside duration probing: a
                        # metadata error must never let a low-resolution file in.
                        dimensions = probe_video_dimensions(video_path)
                        if not dimensions:
                            self.log("⚠️ Не удалось подтвердить разрешение источника")
                            Path(video_path).unlink(missing_ok=True)
                            continue
                        width, height = dimensions
                        if height < min_source_height:
                            self.log(
                                f"🚫 Источник {width}x{height} ниже обязательных "
                                f"{min_source_height}p — удалён"
                            )
                            Path(video_path).unlink(missing_ok=True)
                            continue
                        if not matches_target_orientation(width, height, target_orientation):
                            self.log(
                                f"📐 Неподходящий формат {width}x{height} для "
                                f"{target_orientation}"
                            )
                            Path(video_path).unlink(missing_ok=True)
                            continue

                        if validate_video_file(video_path, deep_check=True):
                            downloaded.append((video_path, video_info))
                        else:
                            self.log("🔧 Видео повреждено, попытка восстановления")
                            repaired = repair_video_file(video_path, log_callback=self.log)
                            if repaired:
                                downloaded.append((repaired, video_info))
                            else:
                                self.log(f"   ⚠️ Не удалось восстановить {Path(video_path).name}, пропускаем")
                                Path(video_path).unlink(missing_ok=True)
                except Exception as e:
                    video = futures[future]
                    self.log(f"    /  {video.get('id', '?')}: {str(e)[:50]}")
        except FuturesTimeoutError:
            self.log(
                f"⏱️ Общий лимит YouTube-загрузок {batch_timeout}s исчерпан; "
                "незавершённые источники отменяются"
            )
        finally:
            for future, video in futures.items():
                if not future.done():
                    future.cancel()
                    self.log(
                        f"⏱️ Batch download timeout: skipping unfinished source {video.get('id', '?')}"
                    )
            executor.shutdown(wait=False, cancel_futures=True)
        
        if bot_error_count >= len(videos) and len(videos) > 0:
            self.log("   🚫 Обнаружена бот-блокировка YouTube (все видео). Пропускаем fallback.")
            return []
        failure_summary = self._download_failure_summary()
        failure_note = ", ".join(
            f"{reason}={count}" for reason, count in sorted(failure_summary.items())
        )
        self.log(
            f"   ✅ Загружено {len(downloaded)}/{len(videos)}"
            + (f"; отказов: {failure_note}" if failure_note else "")
        )
        return downloaded
    

    def _prefer_hd_downloads(
        self,
        downloaded: List[Tuple[str, Dict]],
        minimum_needed: int,
        min_height: Optional[int] = None,
    ) -> List[Tuple[str, Dict]]:
        """Enforce the YouTube quality floor even when it leaves fewer sources."""
        del minimum_needed  # Quality is mandatory; source count must not weaken it.
        min_height = normalize_youtube_min_height(
            self._minimum_youtube_height() if min_height is None else min_height
        )

        preferred = []
        low_resolution = []
        for video_path, video_info in downloaded:
            dimensions = probe_video_dimensions(video_path)
            if not dimensions or dimensions[1] < min_height:
                low_resolution.append((video_path, video_info))
            else:
                preferred.append((video_path, video_info))

        for video_path, _video_info in low_resolution:
            try:
                Path(video_path).unlink(missing_ok=True)
            except (OSError, PermissionError):
                pass
        if low_resolution:
            self.log(
                f"🎯 HD-фильтр {min_height}p+: исключено низких/непроверенных — "
                f"{len(low_resolution)}, осталось — {len(preferred)}"
            )
        return preferred



    def extract_clips_parallel(
        self,
        video_paths_with_info: List[Tuple[str, Dict]],
        output_dir: Path,
        clips_per_video: int,
        min_duration: float = 3.0,
        max_duration: float = 8.0,
        max_concurrent: int = 4
    ) -> List[str]:
        """
             .
        
        Args:
            video_paths_with_info:   (, video_info)
            output_dir:   
            clips_per_video:      
            min_duration:   
            max_duration:   
            max_concurrent:   ffmpeg 
            
        Returns:
                 
        """
        if not video_paths_with_info:
            return []
        
        self.log(f"✂️ Нарезка клипов из {len(video_paths_with_info)} видео (параллельно: {max_concurrent})...")
        
        all_clips = []
        clips_lock = threading.Lock()
        extraction_semaphore = threading.Semaphore(max_concurrent)
        clip_future_timeout = int(
            max(
                180,
                min(900, 90 + max(1, int(clips_per_video)) * max(1.0, float(max_duration)) * 10),
            )
        )
        
        def extract_with_semaphore(video_path: str, existing_clips: List[str]) -> List[str]:
            """      ."""
            video_name = Path(video_path).stem
            self.log(f"      🎬 Начало обработки: {video_name}")
            with extraction_semaphore:
                try:
                    clips = self.extract_random_clips(
                        video_path=video_path,
                        output_dir=output_dir,
                        num_clips=clips_per_video,
                        min_clip_duration=min_duration,
                        max_clip_duration=max_duration,
                        existing_clips=existing_clips
                    )
                    self.log(f"      ✅ Завершено: {video_name} ({len(clips)} клипов)")
                    return clips
                except Exception as e:
                    self.log(f"      ❌ Ошибка {Path(video_path).name}: {str(e)[:50]}")
                    return []
        
        #   
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {}
            
            for video_path, video_info in video_paths_with_info:
                #       
                with clips_lock:
                    current_clips = list(all_clips)
                
                future = executor.submit(extract_with_semaphore, video_path, current_clips)
                futures[future] = video_path
            
            for future in as_completed(futures):
                try:
                    clips = future.result(timeout=clip_future_timeout)
                    if clips:
                        with clips_lock:
                            all_clips.extend(clips)
                        video_name = Path(futures[future]).name
                        self.log(f"   ? {len(clips)}   {video_name}")
                except Exception as e:
                    video_path = futures[future]
                    self.log(f"      {Path(video_path).name}: {str(e)[:50]}")
        
        self.log(f"   ✅ Нарезано клипов: {len(all_clips)}")
        return all_clips
    
    def download_and_extract_clips_parallel(
        self,
        theme: str,
        total_clips_needed: int,
        target_duration_minutes: float,
        output_dir: Path,
        min_clip_duration: float = 3.0,
        max_clip_duration: float = 8.0,
        batch_total_videos: int = 1,
        max_download_concurrent: int = 2,
        max_extract_concurrent: int = 4,
        unique_video_id: str = '',
        target_orientation: str = 'any',  # 'vertical', 'horizontal', 'any'
        visual_context: Optional[Dict] = None,
    ) -> List[str]:
        """
         :   +  .
        
        Args:
            theme:   
            total_clips_needed:   
            target_duration_minutes:    
            output_dir:   
            min_clip_duration: .  
            max_clip_duration: .  
            batch_total_videos:    
            max_download_concurrent: .  
            max_extract_concurrent: .  ffmpeg
            unique_video_id:  ID    
            
        Returns:
               
        """
        import math
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        #  target_duration  
        if batch_total_videos > 1:
            adjusted_duration = min(target_duration_minutes * batch_total_videos * 0.5, 30.0)
            self.log(
                f"📦 План батча: {batch_total_videos} видео, "
                f"лимит исходников {adjusted_duration:.1f} мин"
            )
            target_duration_minutes = adjusted_duration
        
        self.log(f"\n{'='*60}")
        self.log("🎬 YouTube Mixer: Генерация клипов")
        self.log(f"{'='*60}")
        self.log(f"📝 Тема: {theme}")
        self.log(f"🎞️  Клипов нужно: {total_clips_needed}")
        self.log(f"⏱️  Длительность: ~{target_duration_minutes:.1f} мин")
        self.log(f"⚡ Параллелизм: {max_download_concurrent} загрузок, {max_extract_concurrent} ffmpeg")
        try:
            import yt_dlp
            version = yt_dlp.version.__version__
        except Exception:
            version = "недоступен"
        cookie_file = Path(__file__).parent.parent / "youtube_cookies.txt"
        cookie_status = (
            "авторизация найдена"
            if has_current_youtube_auth_cookies(str(cookie_file))
            else "авторизация не найдена"
        )
        runtime_options = build_youtube_js_runtime_options()
        runtime_names = ",".join(runtime_options.get("js_runtimes", {})) or "нет"
        self.log(
            f"🩺 Доступ YouTube: yt-dlp={version}, cookies={cookie_status}, "
            f"JS-runtime={runtime_names}"
        )
        if version != "недоступен" and not is_supported_ytdlp_version(version):
            self.log(
                "⚠️ yt-dlp устарел для текущего YouTube; требуется версия "
                "2026.08.19 или новее"
            )
        if not runtime_options:
            self.log(
                "⚠️ Совместимый JS-runtime не найден: установите Deno либо Node 22+"
            )
        
        if target_orientation != 'any':
            self.log(f"📐 Ориентация: {target_orientation}")
        # Read from mixer settings if available, fallback to 45
        slideshow_threshold = int(
            (self.mixer_settings or {}).get('slideshow_threshold', 45)
        )
        
        # 🎯 Рассчитываем target_seconds для умной скачки видео
        target_seconds = target_duration_minutes * 60
        
        start_time = time.time()

        if self._youtube_downloads_temporarily_blocked():
            self.log(
                "⏱️ YouTube уже подтвердил антибот-блокировку в этой сессии. "
                "Пропускаем поиск и сразу используем резервные источники."
            )
            return []
        
        cache_dir = self._get_cache_dir(theme)
        theme_hash = self._get_cache_key(theme)
        
        #  unique_video_id      
        if unique_video_id:
            clips_dir = output_dir / "youtube_clips" / f"{theme_hash}_{unique_video_id}"
        else:
            clips_dir = output_dir / "youtube_clips" / theme_hash
        clips_dir.mkdir(parents=True, exist_ok=True)
        
        #   
        for old_clip in clips_dir.glob("clip_*.mp4"):
            try:
                old_clip.unlink(missing_ok=True)
            except Exception:
                pass
        
        # Short outputs should not require every useful source video to be 4+ minutes.
        budget = estimate_youtube_source_budget(
            total_clips_needed=total_clips_needed,
            target_duration_minutes=target_duration_minutes,
            min_clip_duration=min_clip_duration,
            max_clip_duration=max_clip_duration,
            batch_total_videos=batch_total_videos,
            target_orientation=target_orientation,
            mixer_settings=self.mixer_settings,
        )
        target_duration_minutes = budget["target_duration_minutes"]
        target_seconds = target_duration_minutes * 60
        adaptive_min_duration = max(60, min(240, int(target_seconds * 0.5)))
        source_max_duration = max(
            adaptive_min_duration,
            min(
                10800,
                int((self.mixer_settings or {}).get('max_video_duration_seconds', 3600)),
            ),
        )
        self.log(
            f"Smart source budget: {target_duration_minutes:.1f} min, "
            f"{budget['min_videos_to_try']}-{budget['max_videos_to_try']} sources, "
            f"{budget['search_results']} search candidates"
        )
        videos = self.search_youtube_videos(
            theme=theme,
            max_results=int(budget["search_results"]),
            min_duration=adaptive_min_duration,
            max_duration=source_max_duration,
            visual_context=visual_context,
        )
        
        if not videos:
            self.log(
                "⚠️ Точный поиск не дал видео; "
                "продолжаю по семантической лестнице"
            )

        videos = [
            v for v in videos
            if not v.get('duration') or v.get('duration', 0) <= source_max_duration
        ]
        videos.sort(
            key=lambda v: (
                v.get('quality_score', 0),
                v.get('relevance', 0),
                v.get('view_count', 0),
            ),
            reverse=True,
        )
        videos = rotate_candidate_pool_for_session(
            videos,
            unique_video_id,
            int(budget["max_videos_to_try"]),
        )
        
        if not videos:
            self.log(
                "⚠️ В точной выдаче не осталось подходящих исходников; "
                "перехожу к более широким запросам"
            )
        
        # 2.  
        download_start = time.time()
        
        # Cycle 16: Pre-download dedup by title fingerprint
        # Prevents downloading near-identical reuploaded videos
        _pre_seen_fingerprints: set = set()
        videos_to_download = []
        estimated_duration = 0
        min_videos_to_try = min(len(videos), int(budget["min_videos_to_try"]))
        max_videos_to_try = min(len(videos), max(min_videos_to_try, int(budget["max_videos_to_try"])))

        for video in videos:
            fp = title_fingerprint(video.get('title', ''))
            if fp and fp in _pre_seen_fingerprints:
                continue  # Skip near-duplicate
            if fp:
                _pre_seen_fingerprints.add(fp)
            videos_to_download.append(video)
            estimated_duration += video.get('duration', 60)
            if len(videos_to_download) >= max_videos_to_try:
                break
            if estimated_duration >= target_seconds and len(videos_to_download) >= min_videos_to_try:
                break
        
        downloaded = self.download_videos_parallel(
                videos=videos_to_download,
                cache_dir=cache_dir,
                max_concurrent=max_download_concurrent,
                target_orientation=target_orientation,
                target_duration_minutes=target_duration_minutes,
            )
        downloaded = self._prefer_hd_downloads(downloaded, minimum_needed=1)
        
        download_time = time.time() - download_start
        self.log(f"  : {download_time:.1f}s")

        if not downloaded and self._youtube_downloads_temporarily_blocked():
            reason = _youtube_block_reason or "access_blocked"
            self.log(
                f"⏱️ YouTube приостановлен ({reason}) после первой загрузки; "
                "Семантические повторы YouTube пропущены"
            )
            return []
        
        if not downloaded:
            self.log("⚠️ Не удалось скачать видео, переход к семантическому fallback")
            valid_videos = []
            slideshow_video_ids = set()
            motion_start = time.time()
            goto_semantic_fallback = True
        else:
            goto_semantic_fallback = False
        
        if goto_semantic_fallback:
            self.log(
                "🔄 Переход к семантическому fallback: пригодных "
                "YouTube-источников пока нет"
            )
            downloaded = []
        else:
            slideshow_video_ids = set()
            max_motion_analysis_time = int(
                (self.mixer_settings or {}).get('motion_analysis_total_timeout_seconds', 30)
            )

            self.log("")  # Пустая строка для читаемости

            self.log(f"🎬 Начинаю анализ {len(downloaded)} видео на наличие движения")

            motion_timeout = int(
                (self.mixer_settings or {}).get('motion_analysis_per_video_timeout_seconds', 20)
            )

        
        def analyze_video(video_data):
            """      watermark"""
            video_path, video_info = video_data
            
            try:
                # Slideshow 
                is_slideshow, confidence = is_slideshow_by_motion(
                    video_path, threshold=slideshow_threshold, log_callback=lambda x: None, timeout=motion_timeout
                )
                
                if is_slideshow and confidence >= 0:
                    return ('slideshow', video_path, video_info, confidence)
                
                # Watermark 
                has_wm, wm_confidence = has_watermark(
                    video_path, threshold=60.0, log_callback=lambda x: None
                )
                
                if has_wm and wm_confidence >= 0:
                    return ('watermark', video_path, video_info, wm_confidence)
                
                return ('valid', video_path, video_info, confidence)
                
            except Exception:
                #     
                return ('valid', video_path, video_info, -1)
        
        valid_videos = []
        motion_start = time.time()
        
        if downloaded:
            motion_workers = int(
                (self.mixer_settings or {}).get('motion_analysis_workers', min(4, len(downloaded)))
            )
            with ThreadPoolExecutor(max_workers=max(1, motion_workers)) as executor:
                futures = {executor.submit(analyze_video, vd): vd for vd in downloaded}

                processed_futures = set()  #   futures

                try:
                    for future in as_completed(futures, timeout=max_motion_analysis_time):
                        try:
                            result_type, video_path, video_info, confidence = future.result(timeout=30)
                            video_name = Path(video_path).name

                            if result_type == 'slideshow':
                                self.log(f"      ⚠️ Слайдшоу: {video_name} (уверенность: {confidence:.0f}%)")
                                slideshow_video_ids.add(video_info.get('id', Path(video_path).stem))
                                try:
                                    Path(video_path).unlink(missing_ok=True)
                                except (OSError, PermissionError):
                                    pass
                            elif result_type == 'watermark':
                                self.log(f"      🚫 Водяной знак: {video_name} (уверенность: {confidence:.0f}%)")
                                slideshow_video_ids.add(video_info.get('id', Path(video_path).stem))
                                try:
                                    Path(video_path).unlink(missing_ok=True)
                                except (OSError, PermissionError):
                                    pass
                            else:
                                if confidence >= 0:
                                    self.log(f"      ✅ Видео принято: {video_name} (слайдшоу: {confidence:.0f}%)")
                                valid_videos.append((video_path, video_info))

                            processed_futures.add(future)

                        except Exception:
                            #     
                            video_data = futures[future]
                            valid_videos.append(video_data)
                            self.log(f"      (): {Path(video_data[0]).name}")

                except FuturesTimeoutError:
                    #        
                    self.log(f"⏱️ Motion analysis timed out ({max_motion_analysis_time}s), adding remaining to valid")
                    for future, video_data in futures.items():
                        if future not in processed_futures:
                            valid_videos.append(video_data)
                            self.log(f"      Video: {Path(video_data[0]).name}")

        motion_time = time.time() - motion_start
        self.log(f"\n🎬 Анализ завершен: {motion_time:.1f}s ({len(valid_videos)} валидных / {len(downloaded)} всего)")
        

        # DEBUG лог удален
        
        # ===  SEMANTIC FALLBACK LOGIC  ===
        
        # Calculate requirements - УМНОЕ СКАЧИВАНИЕ
        clips_per_useful_source = 4
        min_required_videos = min(
            6, max(1, math.ceil(total_clips_needed / clips_per_useful_source))
        )
        
        #  DEBUG INFO
        self.log("\n[SLIDESHOW CHECK]")
        self.log(f"{'='*60}")
        self.log(" SEMANTIC FALLBACK DIAGNOSTICS")
        self.log(f"{'='*60}")
        self.log(f"     Clips Needed: {total_clips_needed}")
        self.log(f"     Clips per useful source: {clips_per_useful_source}")
        self.log(f"     Min Videos Req: {min_required_videos}")
        self.log(f"     Valid Videos: {len(valid_videos)}")
        self.log(f"    Gemini API Key: {'✅ Set' if self.api_key else '❌ Missing'}")
        
        need_fallback = len(valid_videos) < min_required_videos
        self.log(f"     Need Fallback? {need_fallback} ({len(valid_videos)} < {min_required_videos})")
        self.log(f"{'='*60}")
        max_retry_attempts = int(
            (self.mixer_settings or {}).get('semantic_fallback_retries', 4)
        )
        retry_attempt = 0
        retry_start_time = time.time()
        max_retry_total_time = int(
            (self.mixer_settings or {}).get('semantic_fallback_max_seconds', 360)
        )

        failed_queries_history = []
        attempted_queries = {str(theme or '').casefold().strip()}
        attempted_video_ids = {
            str(video.get('id')) for video in videos if video.get('id')
        }
        exhaustive_queries = self._generate_exhaustive_queries(
            theme,
            visual_context=visual_context,
            max_queries=20,
        )
        queries_per_attempt = max(
            2,
            min(
                8,
                int((self.mixer_settings or {}).get('semantic_fallback_queries_per_attempt', 4)),
            ),
        )
        
        while len(valid_videos) < min_required_videos and retry_attempt < max_retry_attempts:
            if self._youtube_downloads_temporarily_blocked():
                self.log("⏱️ Семантический поиск остановлен общим YouTube circuit breaker")
                break
            # Check timeout
            if time.time() - retry_start_time > max_retry_total_time:
                self.log(f"     Timeout reached ({max_retry_total_time}s), stopping retries.")
                break
                
            retry_attempt += 1
            self.log(f"\n RETRY {retry_attempt}/{max_retry_attempts}: Looking for more content...")

            if failed_queries_history:
                self.log(f"⚠️ Неудачные запросы: {', '.join(failed_queries_history[:3])}")
            
            # Generate alternative queries
            alternative_queries = []
            
            if self.api_keys:
                self.log("      Generating semantic fallback queries via Gemini...")
                try:
                    alternative_queries = generate_semantic_fallback_queries(
                        theme,
                        api_key=self.api_key,
                        api_keys=self.api_keys,
                        log_callback=self.log,
                        max_queries=5,
                        failed_queries=failed_queries_history if failed_queries_history else None,
                        failure_reasons=self._download_failure_summary(),
                    )
                    self.log(f"    Gemini returned {len(alternative_queries)} queries.")
                except Exception as e:
                    error_msg = str(e)
                    self.log(f"     Gemini fallback failed: {error_msg[:100]}")
                    
                    # ИСПРАВЛЕНО: НЕ отключаем API навсегда при 429
                    # Retry логика уже встроена в generate_semantic_fallback_queries
                    if '429' in error_msg or 'RESOURCE_EXHAUSTED' in error_msg:
                        self.log("      ⚠️ Gemini API временно недоступен, используем simple fallback")
            
            # Always append the deterministic ladder. This guarantees that an
            # unavailable AI or several over-specific rewrites still progress
            # all the way to the core visual subject.
            deterministic_pending = [
                query for query in exhaustive_queries
                if query.casefold().strip() not in attempted_queries
            ]
            deterministic_quota = max(2, queries_per_attempt // 2)
            alternative_queries = (
                deterministic_pending[:deterministic_quota]
                + alternative_queries
                + self._generate_alternative_queries(theme, retry_attempt)
                + deterministic_pending[deterministic_quota:]
            )
            alternative_queries = list(dict.fromkeys(
                query for query in alternative_queries
                if query and query.casefold().strip() not in attempted_queries
            ))

            for alt_query in alternative_queries[:queries_per_attempt]:
                attempted_queries.add(alt_query.casefold().strip())

                if alt_query not in failed_queries_history:
                    failed_queries_history.append(alt_query)
                if valid_videos:
                    # Break only if we have enough videos? No, break inner loop to re-check condition
                    if len(valid_videos) >= min_required_videos:
                        break
                
                # Check timeout again inside loop
                if time.time() - retry_start_time > max_retry_total_time:
                    self.log("  Timeout inside loop, breaking.")
                    break
                    
                self.log(f"     Searching: '{alt_query[:50]}...'")
                
                # Search new videos
                new_videos = self.search_youtube_videos(
                    theme=alt_query,
                    max_results=int(budget["search_results"]),
                    min_duration=adaptive_min_duration,
                    max_duration=source_max_duration,
                    use_smart_queries=False,
                    max_query_searches=2,
                    original_theme=theme,
                )
                
                # Filter already seen
                new_videos = [
                    video for video in new_videos
                    if str(video.get('id') or '') not in slideshow_video_ids
                    and str(video.get('id') or '') not in attempted_video_ids
                ]
                attempted_video_ids.update(
                    str(video.get('id')) for video in new_videos if video.get('id')
                )
                
                if not new_videos:
                    self.log("        No new videos found.")
                    continue
                
                # Download new videos
                new_downloaded = self.download_videos_parallel(
                    videos=new_videos[:4],
                    cache_dir=cache_dir,
                    max_concurrent=max_download_concurrent,
                    target_orientation=target_orientation,
                    target_duration_minutes=target_duration_minutes,
                )
                new_downloaded = self._prefer_hd_downloads(
                    new_downloaded,
                    minimum_needed=1,
                )
                
                # Analyze motion for new videos
                retry_motion_start = time.time()
                for video_path, video_info in new_downloaded:
                    # Skip motion if taking too long
                    if time.time() - retry_motion_start > 60:  # 1 minute motion limit
                        self.log("     Motion analysis taking too long, skipping rest.")
                        remaining_idx = new_downloaded.index((video_path, video_info))
                        for remaining_video, remaining_info in new_downloaded[remaining_idx:]:
                            valid_videos.append((remaining_video, remaining_info))
                        break
                    
                    is_slideshow, confidence = is_slideshow_by_motion(
                        video_path, threshold=slideshow_threshold, log_callback=self.log
                    )
                    
                    if is_slideshow and confidence >= 0:
                        self.log(f"      ⚠️ Слайдшоу: {Path(video_path).name} (уверенность: {confidence:.0f}%)")
                        slideshow_video_ids.add(video_info.get('id', Path(video_path).stem))
                        try:
                            Path(video_path).unlink(missing_ok=True)
                        except (OSError, PermissionError):
                            pass
                    else:
                        if confidence >= 0:
                            self.log(f"      ✅ Видео принято: {Path(video_path).name} (слайдшоу: {confidence:.0f}%)")
                        valid_videos.append((video_path, video_info))
        

        # Final Report
        self.log("\n[FALLBACK COMPLETE]")
        self.log(f"{'='*60}")
        self.log(" SEMANTIC FALLBACK RESULTS")
        self.log(f"      Total Valid Videos: {len(valid_videos)}")
        self.log(f"      Required: {min_required_videos}")
        self.log(f"      Attempts Used: {retry_attempt}")
        self.log(f"{'='*60}")
        
        if not valid_videos:
            self.log("❌ Failed to find any suitable videos.")
            return []
        
        # 4. Extract Clips
        extract_start = time.time()
        
        clips_per_video = max(1, math.ceil(total_clips_needed / len(valid_videos)))
        
        all_clips = self.extract_clips_parallel(
            video_paths_with_info=valid_videos,
            output_dir=clips_dir,
            clips_per_video=clips_per_video,
            min_duration=min_clip_duration,
            max_duration=max_clip_duration,
            max_concurrent=max_extract_concurrent
        )
        
        if not all_clips and valid_videos:
            self.log("   Extraction failed, retrying...")
            
            #     
            cached_videos = list(cache_dir.glob("*.mp4"))
            cached_videos = [v for v in cached_videos if v.name not in [Path(vp).name for vp, _ in valid_videos]]
            cached_videos = [
                video for video in cached_videos
                if (
                    (dimensions := probe_video_dimensions(video))
                    and dimensions[1] >= self._minimum_youtube_height()
                )
            ]
            
            for cached_video in cached_videos[:3]:  #   3  
                try:
                    #     
                    import ffmpeg
                    probe = ffmpeg.probe(str(cached_video))
                    duration = float(probe['format']['duration'])
                    
                    if duration > 30:
                        self.log(f"     : {cached_video.name}")
                        fallback_clips = self.extract_random_clips(
                            video_path=str(cached_video),
                            output_dir=clips_dir,
                            num_clips=total_clips_needed,
                            min_clip_duration=min_clip_duration,
                            max_clip_duration=max_clip_duration
                        )
                        if fallback_clips:
                            all_clips = fallback_clips
                            self.log(f"   ? Fallback : {len(all_clips)} ")
                            break
                except Exception:
                    self.log(f"      : {cached_video.name}")
                    continue
        
        extract_time = time.time() - extract_start
        total_time = time.time() - start_time
        
        # 
        self.log(f"\n{'='*60}")
        self.log("✅ YouTube Pipeline завершён")
        self.log(f"⏱️  Время: Загрузка {download_time:.1f}s | Motion {motion_time:.1f}s | Нарезка {extract_time:.1f}s")
        self.log(f"📊 Итого: {total_time:.1f}s")
        self.log(f"🎞️  Клипов готово: {len(all_clips)}")
        self.log(f"{'='*60}\n")
        
        selected_clips = interleave_clips_by_source(all_clips, total_clips_needed)
        if visual_context and selected_clips:
            try:
                from core.visual_relevance import rank_clips_by_visual_relevance

                visual_ranking = rank_clips_by_visual_relevance(
                    selected_clips,
                    visual_context,
                    self.api_keys,
                    settings=self.mixer_settings,
                    required_count=total_clips_needed,
                    log_callback=self.log,
                )
                if visual_ranking.recommended_paths:
                    selected_clips = visual_ranking.recommended_paths[:total_clips_needed]
                elif visual_ranking.analysis_available:
                    stock_backup_enabled = bool(
                        (self.mixer_settings or {}).get('enable_pexels')
                        or (self.mixer_settings or {}).get('enable_pixabay_videos')
                        or (self.mixer_settings or {}).get('enable_wikimedia_videos')
                    )
                    if stock_backup_enabled:
                        selected_clips = []
                        self.log(
                            "   🔁 YouTube-клипы визуально не совпали; "
                            "освобождаю места для включённых стоков"
                        )
                    else:
                        # YouTube is the only configured source. Keep a usable
                        # timeline, but preserve the visual ranking order.
                        selected_clips = visual_ranking.ranked_paths[:total_clips_needed]
            except Exception as exc:
                self.log(f"   ⚠️ Проверка кадров недоступна: {str(exc)[:120]}")
        return selected_clips
    
    def mix_with_images(
        self,
        image_paths: List[str],
        clip_paths: List[str],
        video_ratio: float = 0.3
    ) -> List[Tuple[str, str]]:
        """
           -.
        
        Args:
            image_paths:   AI-
            clip_paths:   YouTube 
            video_ratio:   (0.0-1.0)
            
        Returns:
              (, )   = 'image'  'video'
        """
        if not clip_paths:
            return [(p, 'image') for p in image_paths]
        
        total_items = len(image_paths)
        num_videos = int(total_items * video_ratio)
        num_images = total_items - num_videos
        
        #     
        selected_images = image_paths[:num_images] if num_images > 0 else []
        selected_clips = clip_paths[:num_videos] if num_videos > 0 else []
        
        #    
        mixed = [(p, 'image') for p in selected_images]
        mixed.extend([(p, 'video') for p in selected_clips])
        
        # 
        random.shuffle(mixed)
        
        return mixed
    
    def clear_cache(self, theme: str = None):
        """  (    )."""
        if theme:
            cache_dir = self._get_cache_dir(theme)
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
                self.log(f"🗑️ Кэш темы '{theme}' очищен")
        else:
            if self.CACHE_DIR.exists():
                shutil.rmtree(self.CACHE_DIR)
                self.CACHE_DIR.mkdir()
                self.log("🗑️ Кэш очищен")
    
    def get_cache_size(self) -> float:
        """    MB."""
        total_size = 0
        if self.CACHE_DIR.exists():
            for file in self.CACHE_DIR.rglob('*'):
                if file.is_file():
                    total_size += file.stat().st_size
        return total_size / (1024 * 1024)
