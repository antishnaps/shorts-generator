#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Collection and fair mixing of local and free stock video sources."""

import random
import math
import re
import subprocess
import threading
from collections import Counter, defaultdict, deque
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Iterable, List

from core.process_registry import run_registered


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".ogv", ".ogg"}
STOCK_MIN_HEIGHT = 720


_RECENT_VISUAL_LOCK = threading.RLock()
_RECENT_VISUAL_FINGERPRINTS = deque(maxlen=500)
_BATCH_CLIP_RANGES = defaultdict(list)
_BATCH_SELECTED_CLIP_RANGES = defaultdict(lambda: defaultdict(list))
_VIDEO_USED_EXACT = defaultdict(set)


def _session_key(session_id=None) -> str:
    return str(session_id or "default")


@lru_cache(maxsize=2048)
def _probe_clip_duration(path: str) -> float:
    source_path = Path(path)
    if not source_path.is_file() or source_path.stat().st_size < 1024:
        return 8.0
    try:
        result = run_registered(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(source_path),
            ],
            label="ffprobe_visual_clip_duration",
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return max(0.1, float(result.stdout.strip()))
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        pass
    return 8.0


def _probe_video_dimensions(path: str):
    try:
        result = run_registered(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path),
            ],
            label="ffprobe_stock_video_dimensions",
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        values = result.stdout.strip().split(",")[:2]
        if len(values) != 2:
            return None
        width, height = (int(value) for value in values)
        return (width, height) if width > 0 and height > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _accept_hd_stock_video(path: str, log_callback: Callable) -> bool:
    dimensions = _probe_video_dimensions(path)
    if dimensions and dimensions[1] >= STOCK_MIN_HEIGHT:
        return True
    actual = f"{dimensions[0]}x{dimensions[1]}" if dimensions else "неизвестно"
    log_callback(
        f"   🚫 Стоковый клип {Path(path).name} отклонён: {actual}, "
        f"требуется {STOCK_MIN_HEIGHT}p+"
    )
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
    return False


def _parsed_clip_interval(path: str):
    """Recover donor/start/duration from generated clip cache filenames."""
    source_path = Path(str(path))
    match = re.match(
        r"^clip_(?P<donor>.+)_(?P<start>[0-9]{4,9})_[0-9]{3}(?:_[a-f0-9]{6})?$",
        source_path.stem.lower(),
    )
    if not match:
        return None
    start = int(match.group("start")) / 1000.0
    return match.group("donor"), start, start + _probe_clip_duration(str(source_path))


def _has_selected_time_conflict_unlocked(
    path: str,
    padding: float = 0.1,
    ranges=None,
    session_id=None,
) -> bool:
    parsed = _parsed_clip_interval(path)
    if not parsed:
        return False
    donor, start, end = parsed
    ranges = (
        _BATCH_SELECTED_CLIP_RANGES[_session_key(session_id)]
        if ranges is None else ranges
    )
    return any(
        min(end + padding, used_end) - max(start - padding, used_start) > 0
        for used_start, used_end in ranges.get(donor, [])
    )


def _remember_interval_in_ranges(path: str, ranges) -> None:
    parsed = _parsed_clip_interval(path)
    if not parsed:
        return
    donor, start, end = parsed
    ranges.setdefault(donor, []).append((start, end))


def _clip_fingerprint(path: str) -> str:
    """Coarse identity used to avoid visual repeats across a batch."""
    source_path = Path(str(path))
    stem = source_path.stem.lower()

    if stem.startswith("pexels_video_"):
        return f"pexels:{stem}"
    if stem.startswith("pixabay_video_"):
        return f"pixabay:{stem}"
    if stem.startswith("wikimedia_video_"):
        return f"wikimedia:{stem}"

    if stem.startswith("clip_"):
        # clip_{source}_{start}_{index}[_token].mp4. Source names may contain
        # underscores, so strip the numeric tail from the right.
        donor = re.sub(r"_\d{4,8}_\d{3}(?:_[a-f0-9]{6})?$", "", stem[5:])
        if donor:
            return f"local-source:{donor}"

    if source_path.suffix.lower() in VIDEO_EXTENSIONS:
        return f"local-source:{stem}"

    return f"file:{source_path.name.lower()}"


def _exact_visual_fingerprint(path: str) -> str:
    """Identity for one actual clip, ignoring random filename suffixes."""
    source_path = Path(str(path))
    stem = source_path.stem.lower()
    if stem.startswith("clip_"):
        # Generated clips end in _{start_ms}_{index}_{random}. Treat separately
        # cut copies of the same donor/time range as one visual.
        normalized = re.sub(r"_([0-9]{4,8})_[0-9]{3}(?:_[a-f0-9]{6})?$", r"_\1", stem)
        return f"clip:{normalized}"
    try:
        return f"path:{source_path.resolve()}".lower()
    except OSError:
        return f"path:{source_path}".lower()


def _usage_counters_unlocked():
    exact_usage = Counter()
    donor_usage = Counter()
    for entry in _RECENT_VISUAL_FINGERPRINTS:
        if isinstance(entry, tuple) and len(entry) == 2:
            donor, exact = entry
        else:
            donor, exact = str(entry), ""
        donor_usage[donor] += 1
        if exact:
            exact_usage[exact] += 1
    return exact_usage, donor_usage


def reset_visual_batch_state() -> None:
    """Start a new generation batch with an empty allocation history."""
    with _RECENT_VISUAL_LOCK:
        _RECENT_VISUAL_FINGERPRINTS.clear()
        _BATCH_CLIP_RANGES.clear()
        _BATCH_SELECTED_CLIP_RANGES.clear()
        _VIDEO_USED_EXACT.clear()
        _probe_clip_duration.cache_clear()


def reset_visual_video_state(session_id=None) -> None:
    """Clear strict no-repeat state for one video, preserving batch variety history."""
    session = _session_key(session_id)
    with _RECENT_VISUAL_LOCK:
        _VIDEO_USED_EXACT.pop(session, None)
        _BATCH_SELECTED_CLIP_RANGES.pop(session, None)
        stale_range_keys = [
            key for key in _BATCH_CLIP_RANGES
            if isinstance(key, tuple) and key[0] == session
        ]
        for key in stale_range_keys:
            _BATCH_CLIP_RANGES.pop(key, None)


def _prioritize_fresh_unlocked(paths: Iterable[str]) -> List[str]:
    candidates = list(paths or [])
    random.shuffle(candidates)
    exact_usage, donor_usage = _usage_counters_unlocked()
    candidates.sort(
        key=lambda path: (
            exact_usage[_exact_visual_fingerprint(path)],
            donor_usage[_clip_fingerprint(path)],
        )
    )
    return candidates


def _prioritize_fresh(paths: Iterable[str]) -> List[str]:
    """Shuffle paths while pushing recently used visual identities to the end."""
    with _RECENT_VISUAL_LOCK:
        return _prioritize_fresh_unlocked(paths)


def _prioritize_fresh_stable(paths: Iterable[str]) -> List[str]:
    """Prioritize freshness without destroying an existing diverse ordering."""
    with _RECENT_VISUAL_LOCK:
        exact_usage, donor_usage = _usage_counters_unlocked()
        return sorted(
            list(paths or []),
            key=lambda path: (
                exact_usage[_exact_visual_fingerprint(path)],
                donor_usage[_clip_fingerprint(path)],
            ),
        )


def _remember_used_unlocked(paths: Iterable[str], session_id=None) -> None:
    session = _session_key(session_id)
    for path in paths or []:
        exact = _exact_visual_fingerprint(path)
        _RECENT_VISUAL_FINGERPRINTS.append(
            (_clip_fingerprint(path), exact)
        )
        _VIDEO_USED_EXACT[session].add(exact)
        parsed = _parsed_clip_interval(path)
        if parsed:
            donor, start, end = parsed
            interval = (start, end)
            session_ranges = _BATCH_SELECTED_CLIP_RANGES[session]
            if interval not in session_ranges[donor]:
                session_ranges[donor].append(interval)


def _remember_used(paths: Iterable[str], session_id=None) -> None:
    with _RECENT_VISUAL_LOCK:
        _remember_used_unlocked(paths, session_id=session_id)


def prioritize_fresh_paths(paths: Iterable[str]) -> List[str]:
    return _prioritize_fresh(paths)


def fresh_existing_paths(paths: Iterable[str], session_id=None) -> List[str]:
    """Return paths unused in this video without reserving them."""
    with _RECENT_VISUAL_LOCK:
        candidates = _prioritize_fresh_unlocked(unique_existing_paths(paths))
        used_exact = _VIDEO_USED_EXACT[_session_key(session_id)]
        local_ranges = {
            donor: list(ranges)
            for donor, ranges in _BATCH_SELECTED_CLIP_RANGES[
                _session_key(session_id)
            ].items()
        }
        selected = []
        seen_exact = set()
        for path in candidates:
            exact = _exact_visual_fingerprint(path)
            if (
                exact in seen_exact
                or exact in used_exact
                or _has_selected_time_conflict_unlocked(
                    path, ranges=local_ranges, session_id=session_id
                )
            ):
                continue
            seen_exact.add(exact)
            selected.append(path)
            _remember_interval_in_ranges(path, local_ranges)
        return selected


def select_fresh_existing_paths(
    paths: Iterable[str], limit: int = None, session_id=None
) -> List[str]:
    """Reserve paths that have not yet been used in the current video.

    Exhaustion is explicit: this function returns fewer items (possibly none)
    instead of silently cycling back to an already used visual.
    """
    with _RECENT_VISUAL_LOCK:
        candidates = _prioritize_fresh_unlocked(unique_existing_paths(paths))
        used_exact = _VIDEO_USED_EXACT[_session_key(session_id)]
        selected = []
        seen_exact = set()
        for path in candidates:
            exact = _exact_visual_fingerprint(path)
            if (
                exact in seen_exact
                or exact in used_exact
                or _has_selected_time_conflict_unlocked(path, session_id=session_id)
            ):
                continue
            seen_exact.add(exact)
            selected.append(path)
            _remember_used_unlocked([path], session_id=session_id)
            if limit is not None and len(selected) >= limit:
                break
        return selected


def reserve_ranked_existing_paths(
    paths: Iterable[str], limit: int = None, session_id=None
) -> List[str]:
    """Reserve per-video fresh paths while preserving caller ranking order."""
    with _RECENT_VISUAL_LOCK:
        candidates = unique_existing_paths(paths)
        used_exact = _VIDEO_USED_EXACT[_session_key(session_id)]
        selected = []
        seen_exact = set()
        for path in candidates:
            exact = _exact_visual_fingerprint(path)
            if (
                exact in seen_exact
                or exact in used_exact
                or _has_selected_time_conflict_unlocked(path, session_id=session_id)
            ):
                continue
            seen_exact.add(exact)
            selected.append(path)
            _remember_used_unlocked([path], session_id=session_id)
            if limit is not None and len(selected) >= limit:
                break
        return selected


def fresh_ranked_existing_paths(
    paths: Iterable[str], limit: int = None, session_id=None
) -> List[str]:
    """Return per-video fresh paths in caller order without reserving them."""
    with _RECENT_VISUAL_LOCK:
        candidates = unique_existing_paths(paths)
        used_exact = _VIDEO_USED_EXACT[_session_key(session_id)]
        selected = []
        seen_exact = set()
        local_ranges = {
            donor: list(ranges)
            for donor, ranges in _BATCH_SELECTED_CLIP_RANGES[
                _session_key(session_id)
            ].items()
        }
        for path in candidates:
            exact = _exact_visual_fingerprint(path)
            if (
                exact in seen_exact
                or exact in used_exact
                or _has_selected_time_conflict_unlocked(
                    path, ranges=local_ranges, session_id=session_id
                )
            ):
                continue
            seen_exact.add(exact)
            selected.append(path)
            _remember_interval_in_ranges(path, local_ranges)
            if limit is not None and len(selected) >= limit:
                break
        return selected


def reserve_diverse_path_cycle(paths: Iterable[str], count: int) -> List[str]:
    """Atomically allocate a balanced donor sequence for parallel workers."""
    candidates = unique_existing_paths(paths)
    count = max(0, int(count or 0))
    if not candidates or count <= 0:
        return []

    with _RECENT_VISUAL_LOCK:
        exact_usage, donor_usage = _usage_counters_unlocked()
        result = []
        last_path = None
        call_usage = Counter()
        for _ in range(count):
            ranked = list(candidates)
            random.shuffle(ranked)
            ranked.sort(
                key=lambda path: (
                    call_usage[path],
                    exact_usage[_exact_visual_fingerprint(path)],
                    donor_usage[_clip_fingerprint(path)],
                    path == last_path,
                )
            )
            selected = ranked[0]
            result.append(selected)
            last_path = selected
            call_usage[selected] += 1
            exact = _exact_visual_fingerprint(selected)
            donor = _clip_fingerprint(selected)
            exact_usage[exact] += 1
            donor_usage[donor] += 1
            _RECENT_VISUAL_FINGERPRINTS.append((donor, exact))
        return result


def reserve_least_used_clip_range(
    source_path: str,
    candidates: Iterable[tuple],
    padding: float = 0.75,
    session_id=None,
) -> tuple:
    """Reserve a strictly non-overlapping time range for one video.

    Returning ``None`` is intentional when the donor has no suitable unique
    range left. Callers can then try another donor or report a real shortage.
    """
    normalized = []
    for start, end in candidates or []:
        try:
            start = max(0.0, float(start))
            end = float(end)
        except (TypeError, ValueError):
            continue
        if end > start:
            normalized.append((start, end))
    if not normalized:
        return None

    donor = str(Path(str(source_path)).resolve()).lower()
    with _RECENT_VISUAL_LOCK:
        used = _BATCH_CLIP_RANGES[(_session_key(session_id), donor)]
        available = []
        for order, (start, end) in enumerate(normalized):
            overlaps = any(
                min(end + padding, used_end) - max(start - padding, used_start) > 0
                for used_start, used_end in used
            )
            if not overlaps:
                available.append((order, start, end))
        if not available:
            return None
        chosen = random.choice(available[: min(8, len(available))])
        selected = (chosen[1], chosen[2])
        used.append(selected)
        return selected


def unique_existing_paths(paths: Iterable[str]) -> List[str]:
    result = []
    seen = set()
    for path in paths or []:
        value = str(path)
        key = str(Path(value).resolve()).lower()
        if key not in seen and Path(value).is_file():
            seen.add(key)
            result.append(value)
    return result


def _stock_path_key(path) -> str:
    """Return a stable comparison key for predicted and actual stock paths."""
    try:
        return str(Path(str(path)).resolve(strict=False)).casefold()
    except (OSError, RuntimeError, TypeError, ValueError):
        return str(path or "").casefold()


def interleave_sources(sources: Dict[str, Iterable[str]], limit: int = None) -> List[str]:
    queues = {name: unique_existing_paths(paths) for name, paths in sources.items()}
    for name, queue in list(queues.items()):
        queues[name] = _prioritize_fresh(queue)
    result = []
    while any(queues.values()) and (limit is None or len(result) < limit):
        source_order = list(queues)
        random.shuffle(source_order)
        for name in source_order:
            if queues[name] and (limit is None or len(result) < limit):
                result.append(queues[name].pop(0))
    return result


class VisualSourceManager:
    def __init__(self, log_callback: Callable = None, session_id=None):
        self.log = log_callback or (lambda _message: None)
        self.session_id = _session_key(session_id)

    def collect_local_videos(self, folder: str, limit: int = None) -> List[str]:
        if not folder or not Path(folder).is_dir():
            return []
        videos = [str(path) for path in Path(folder).rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS]
        random.shuffle(videos)
        return videos[:limit] if limit else videos

    def collect_stock_videos(self, theme: str, output_dir: Path, count: int, settings: dict, target_orientation: str = "vertical") -> Dict[str, List[str]]:
        result = {"pexels": [], "pixabay": [], "wikimedia": []}
        if count <= 0:
            return result
        stock_dir = Path(output_dir) / "stock_clips"
        excluded_paths = {
            _stock_path_key(path)
            for path in (settings.get("_stock_exclude_paths") or [])
            if path
        }
        claimed_paths = set(excluded_paths)
        source_flags = (
            ("pexels", "enable_pexels"),
            ("pixabay", "enable_pixabay_videos"),
            ("wikimedia", "enable_wikimedia_videos"),
        )
        enabled_sources = [
            name for name, flag in source_flags if settings.get(flag)
        ]
        targets = {
            name: count // len(enabled_sources) + (index < count % len(enabled_sources))
            for index, name in enumerate(enabled_sources)
        } if enabled_sources else {}

        def effective_target(name: str) -> int:
            """Give an underfilled provider's slots to the providers after it."""
            if name not in enabled_sources:
                return 0
            source_index = enabled_sources.index(name)
            reserved_for_later = sum(
                targets.get(later, 0) for later in enabled_sources[source_index + 1:]
            )
            collected = sum(len(paths) for paths in result.values())
            return max(0, count - collected - reserved_for_later)
        from core.youtube.query_planner import build_stock_queries
        from core.youtube.smart_search import generate_visual_search_plan
        from core.youtube.translation import translate_to_english_ai

        translated_theme = translate_to_english_ai(
            theme,
            api_key=settings.get("gemini_api_key"),
            log_callback=lambda _message: None,
        )
        visual_context = settings.get("_visual_context") or {}
        routed_queries = [
            str(item.get("query") or "").strip()
            for item in (visual_context.get("search_routes") or [])
            if isinstance(item, dict) and str(item.get("query") or "").strip()
        ]
        contextual_queries = routed_queries + [
            str(query).strip()
            for query in (visual_context.get("search_queries") or [])
            if str(query or "").strip()
        ]
        contextual_queries = list(dict.fromkeys(contextual_queries))
        if not contextual_queries:
            visual_plan = generate_visual_search_plan(
                theme=theme,
                translated_theme=translated_theme,
                api_key=settings.get("gemini_api_key"),
                log_callback=lambda _message: None,
                max_queries=8,
            )
            contextual_queries = [
                item.text for item in visual_plan if item.tier in {"context", "broad"}
            ]
        search_queries = build_stock_queries(
            theme,
            translated_theme,
            contextual_queries=contextual_queries,
            max_queries=8,
        ) or [theme]

        # A provider can return enough files for the first narrow query while
        # still covering only one visual idea. Always traverse several Gemini
        # routes before stopping so subject, environment and associative B-roll
        # all have a chance to contribute.
        min_query_routes = min(
            len(search_queries),
            max(4, min(6, max(1, int(count or 1)) + 2)),
        )
        if search_queries:
            self.log(
                f"   🧭 Стоковый поиск: {len(search_queries)} запросов, "
                f"минимум {min_query_routes} разных маршрутов"
            )
        if settings.get("enable_pexels") and targets.get("pexels", 0) > 0:
            source_count = effective_target("pexels")
            from core.pexels_client import PexelsClient
            client = PexelsClient(settings.get("pexels_api_key"), self.log)
            downloaded = set()
            query_buckets = []
            seen_video_ids = set()
            min_queries_to_try = min_query_routes
            per_page = min(80, max(12, source_count * 3))
            for query_index, query in enumerate(search_queries):
                videos = client.search_videos(
                    query,
                    per_page=per_page,
                    orientation="portrait" if target_orientation == "vertical" else "landscape",
                )
                random.shuffle(videos)
                bucket = []
                for video in videos:
                    identity = str(video.get("id") or video.get("url") or id(video))
                    if identity in seen_video_ids:
                        continue
                    seen_video_ids.add(identity)
                    bucket.append(video)
                if bucket:
                    query_buckets.append(bucket)
                candidate_total = sum(len(bucket) for bucket in query_buckets)
                if candidate_total >= source_count and query_index + 1 >= min_queries_to_try:
                    break
            pending = []
            # Keep the whole interleaved candidate pool. Predicted cache paths
            # are only an optimisation: custom clients and future providers may
            # return a different final path, so the actual path is checked after
            # download and a duplicate must not consume one of the target slots.
            while any(query_buckets):
                for bucket in query_buckets:
                    if not bucket:
                        continue
                    pending.append(bucket.pop(0))
            pending_by_path = {
                str(stock_dir / "pexels" / f"pexels_video_{video.get('id', 'unknown')}.mp4"): video
                for video in pending
                if _stock_path_key(
                    stock_dir / "pexels" / f"pexels_video_{video.get('id', 'unknown')}.mp4"
                )
                not in excluded_paths
            }
            # ``pending`` is already round-robin across query routes. Keep that
            # order among equally fresh files instead of shuffling the route
            # diversity away again.
            fresh_paths = _prioritize_fresh_stable(pending_by_path)
            pending = [pending_by_path[path] for path in fresh_paths]
            for video in pending:
                if len(result["pexels"]) >= source_count:
                    break
                path = client.download_video(video, stock_dir / "pexels", target_orientation)
                path_key = _stock_path_key(path) if path else ""
                if (
                    path
                    and path_key not in claimed_paths
                    and path_key not in downloaded
                    and _accept_hd_stock_video(path, self.log)
                ):
                    downloaded.add(path_key)
                    claimed_paths.add(path_key)
                    result["pexels"].append(path)
        if settings.get("enable_pixabay_videos") and targets.get("pixabay", 0) > 0:
            source_count = effective_target("pixabay")
            from core.pixabay_client import PixabayClient
            client = PixabayClient(settings.get("pixabay_api_key"), self.log)
            query_buckets = []
            seen_video_ids = set()
            min_queries_to_try = min_query_routes
            for query_index, query in enumerate(search_queries):
                found = client.search_videos(
                    query,
                    per_page=max(6, source_count * 3),
                    min_width=720,
                    min_height=720,
                    safesearch=True,
                )
                random.shuffle(found)
                bucket = []
                for video in found:
                    identity = str(video.get("id") or video.get("pageURL") or id(video))
                    if identity in seen_video_ids:
                        continue
                    seen_video_ids.add(identity)
                    bucket.append(video)
                if bucket:
                    query_buckets.append(bucket)
                candidate_total = sum(len(bucket) for bucket in query_buckets)
                if candidate_total >= source_count * 3 and query_index + 1 >= min_queries_to_try:
                    break
            videos = []
            while any(query_buckets):
                for bucket in query_buckets:
                    if bucket:
                        videos.append(bucket.pop(0))
            for video in videos:
                # Pixabay ``small`` is 1080p. It preserves FHD output quality
                # while avoiding a 4K download for every stock clip.
                path = client.download_video(video, stock_dir / "pixabay", quality="small")
                path_key = _stock_path_key(path) if path else ""
                if path and path_key in claimed_paths:
                    continue
                if path and _accept_hd_stock_video(path, self.log):
                    claimed_paths.add(path_key)
                    result["pixabay"].append(path)
                if len(result["pixabay"]) >= source_count:
                    break
        if settings.get("enable_wikimedia_videos") and targets.get("wikimedia", 0) > 0:
            source_count = effective_target("wikimedia")
            from core.wikimedia_commons_client import WikimediaCommonsClient

            client = WikimediaCommonsClient(log_callback=self.log)
            query_buckets = []
            seen_video_ids = set()
            min_queries_to_try = min_query_routes
            for query_index, query in enumerate(search_queries):
                found = client.search_videos(
                    query,
                    per_page=max(8, min(30, source_count * 3)),
                    min_height=STOCK_MIN_HEIGHT,
                    target_orientation=target_orientation,
                )
                random.shuffle(found)
                bucket = []
                for video in found:
                    identity = str(video.get("id") or video.get("page_url") or id(video))
                    if identity in seen_video_ids:
                        continue
                    seen_video_ids.add(identity)
                    bucket.append(video)
                if bucket:
                    query_buckets.append(bucket)
                candidate_total = sum(len(bucket) for bucket in query_buckets)
                if candidate_total >= source_count * 3 and query_index + 1 >= min_queries_to_try:
                    break

            videos = []
            while any(query_buckets):
                for bucket in query_buckets:
                    if bucket:
                        videos.append(bucket.pop(0))

            pending_by_path = {
                str(client.output_path_for(video, stock_dir / "wikimedia")): video
                for video in videos
                if _stock_path_key(client.output_path_for(video, stock_dir / "wikimedia"))
                not in excluded_paths
            }
            for candidate_path in _prioritize_fresh_stable(pending_by_path):
                video = pending_by_path[candidate_path]
                path = client.download_video(video, stock_dir / "wikimedia")
                path_key = _stock_path_key(path) if path else ""
                if path and path_key in claimed_paths:
                    continue
                if path and _accept_hd_stock_video(path, self.log):
                    claimed_paths.add(path_key)
                    result["wikimedia"].append(path)
                if len(result["wikimedia"]) >= source_count:
                    break

        # ``effective_target`` transfers an early provider's unused slots to a
        # later provider. Close a remaining gap in the other direction with a
        # small, provider-agnostic number of refill rounds. Each nested call is
        # marked as a refill and therefore cannot recurse again; a no-progress
        # round stops immediately.
        collected_paths = {
            _stock_path_key(path)
            for paths in result.values()
            for path in paths
        }
        shortfall = max(0, count - len(collected_paths))
        if shortfall and not settings.get("_stock_refill_pass"):
            max_refill_rounds = min(3, max(1, int(count)))
            for _round_index in range(max_refill_rounds):
                paths_before_round = len(collected_paths)
                refill_sources = sorted(
                    enabled_sources,
                    key=lambda source: len(result[source]),
                    reverse=True,
                )
                for refill_source in refill_sources:
                    if shortfall <= 0:
                        break
                    refill_settings = dict(settings)
                    for source, flag in source_flags:
                        refill_settings[flag] = source == refill_source
                    refill_settings.update({
                        "_stock_refill_pass": True,
                        "_stock_exclude_paths": sorted(excluded_paths | collected_paths),
                    })
                    refill = self.collect_stock_videos(
                        theme=theme,
                        output_dir=output_dir,
                        count=shortfall,
                        settings=refill_settings,
                        target_orientation=target_orientation,
                    )
                    for path in refill.get(refill_source, []):
                        normalized_path = _stock_path_key(path)
                        if normalized_path in collected_paths:
                            continue
                        result[refill_source].append(path)
                        collected_paths.add(normalized_path)
                        shortfall -= 1
                        if shortfall <= 0:
                            break
                if shortfall <= 0 or len(collected_paths) == paths_before_round:
                    break
        if visual_context:
            all_stock_paths = result["pexels"] + result["pixabay"] + result["wikimedia"]
            if all_stock_paths:
                try:
                    from core.visual_relevance import rank_clips_by_visual_relevance

                    api_keys = settings.get("gemini_api_keys") or [settings.get("gemini_api_key")]
                    ranking = rank_clips_by_visual_relevance(
                        all_stock_paths,
                        visual_context,
                        api_keys,
                        settings=settings,
                        required_count=count,
                        log_callback=self.log,
                    )
                    recommended_paths = list(ranking.recommended_paths)
                    if ranking.analysis_available:
                        desired = min(max(1, int(count or 1)), len(all_stock_paths))
                        if (
                            settings.get("visual_relevance_soft_backfill", True)
                            and len(recommended_paths) < desired
                        ):
                            before = len(recommended_paths)
                            for path in ranking.ranked_paths:
                                if path not in recommended_paths:
                                    recommended_paths.append(path)
                                if len(recommended_paths) >= desired:
                                    break
                            added = len(recommended_paths) - before
                            if added:
                                self.log(
                                    f"   🧩 Семантическое добирание: +{added} лучших "
                                    f"доступных клипов ({len(recommended_paths)}/{desired}); "
                                    "генерация не останется на трёх донорах"
                                )
                        recommended = set(recommended_paths)
                        result["pexels"] = [path for path in result["pexels"] if path in recommended]
                        result["pixabay"] = [path for path in result["pixabay"] if path in recommended]
                        result["wikimedia"] = [path for path in result["wikimedia"] if path in recommended]
                except Exception as exc:
                    self.log(f"   ⚠️ Проверка кадров стоков пропущена: {str(exc)[:120]}")
        return result

    def mix(self, youtube: Iterable[str], local: Iterable[str], stock: Dict[str, Iterable[str]], limit: int, mode: str = "smart_mix") -> List[str]:
        """Mix video sources with YouTube/local as primary, stock as B-roll padding only.

        Stock (Pexels/Pixabay/Wikimedia) is capped at ~35% of the result so it never
        dominates the feed. In fallback mode stock fills whatever is missing.
        When stock is explicitly enabled alongside local/YouTube, it always
        contributes clips to the mix (not just when primary sources fall short).
        """
        with _RECENT_VISUAL_LOCK:
            return self._mix_unlocked(youtube, local, stock, limit, mode)

    def _mix_unlocked(self, youtube, local, stock, limit, mode):
        used_exact = _VIDEO_USED_EXACT[self.session_id]

        def only_unused(paths):
            return [
                path for path in unique_existing_paths(paths)
                if _exact_visual_fingerprint(path) not in used_exact
                and not _has_selected_time_conflict_unlocked(
                    path, session_id=self.session_id
                )
            ]

        primary = _prioritize_fresh_unlocked(
            only_unused(list(local or []) + list(youtube or []))
        )

        if mode == "fallback":
            if len(primary) >= limit:
                result = primary[:limit]
                return reserve_ranked_existing_paths(
                    result, limit, session_id=self.session_id
                )
            unused_stock = {
                name: only_unused(paths)
                for name, paths in (stock or {}).items()
            }
            result = primary + interleave_sources(unused_stock, limit - len(primary))
            return reserve_ranked_existing_paths(
                result, limit, session_id=self.session_id
            )

        # smart_mix: when stock clips are available (caller already fetched
        # them explicitly), always include them in the mix even if primary
        # sources fully cover the limit. This honours the "local + Pexels"
        # setting correctly instead of silently ignoring Pexels.
        unused_stock = {
            name: only_unused(paths)
            for name, paths in (stock or {}).items()
        }
        stock_clips = interleave_sources(unused_stock, limit)

        if not primary:
            # No YouTube/local at all — use stock fully
            result = stock_clips[:limit]
            return reserve_ranked_existing_paths(
                result, limit, session_id=self.session_id
            )

        if not stock_clips:
            # No stock — pure primary
            result = primary[:limit]
            return reserve_ranked_existing_paths(
                result, limit, session_id=self.session_id
            )

        # How many slots to allocate to stock vs primary.
        # If primary covers the whole video, stock is capped at ~35% so it
        # doesn't dominate. If primary is short, stock fills the shortfall.
        if len(primary) >= limit:
            max_stock = min(len(stock_clips), max(1, math.ceil(limit * 0.35)))
        else:
            max_stock = min(len(stock_clips), limit - len(primary))
        stock_clips = stock_clips[:max_stock]
        primary_slots = limit - len(stock_clips)

        primary_use = primary[:primary_slots]

        pools = {"primary": list(primary_use), "stock": list(stock_clips)}
        interleaved = []
        last_source = None
        same_source_run = 0
        while any(pools.values()) and len(interleaved) < limit:
            choices = [name for name, values in pools.items() if values]
            if same_source_run >= 2 and len(choices) > 1:
                choices = [name for name in choices if name != last_source] or choices
            total_remaining = sum(len(pools[name]) for name in choices)
            cursor = random.uniform(0, total_remaining)
            chosen = choices[-1]
            for name in choices:
                cursor -= len(pools[name])
                if cursor <= 0:
                    chosen = name
                    break
            interleaved.append(pools[chosen].pop(0))
            if chosen == last_source:
                same_source_run += 1
            else:
                last_source = chosen
                same_source_run = 1

        result = interleaved[:limit]
        return reserve_ranked_existing_paths(
            result, limit, session_id=self.session_id
        )
