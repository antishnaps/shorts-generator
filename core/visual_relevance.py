"""Universal script-to-footage planning and frame-level relevance checks.

The module deliberately contains no topic dictionaries.  It derives concrete
subjects and scenes from the current script, then validates sampled clip frames
against that same script.  When multimodal analysis is unavailable every public
entry point fails open and preserves the existing media pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from core.youtube.query_planner import extract_visual_core, normalize_search_query


_brief_cache: Dict[str, Dict[str, Any]] = {}
_visual_matches: Dict[Tuple[str, str], Dict[str, Any]] = {}
_cache_lock = threading.RLock()


@dataclass
class VisualRankingResult:
    """Result of the bounded multimodal ranking stage."""

    ranked_paths: List[str]
    recommended_paths: List[str]
    accepted_paths: List[str]
    matches: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    scored_count: int = 0
    analysis_available: bool = False


def script_fingerprint(text_parts: Sequence[str] | None) -> str:
    """Return a stable identity for the narration used during clip matching."""
    normalized = "\n".join(
        " ".join(str(part or "").split()).casefold()
        for part in (text_parts or [])
        if str(part or "").strip()
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _brief_fingerprint(theme: str, text_parts: Sequence[str]) -> str:
    payload = f"{str(theme or '').strip().casefold()}::{script_fingerprint(text_parts)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(minimum, min(maximum, number))


def _unique_text(values: Iterable[Any], limit: int, max_length: int = 120) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = " ".join(str(value or "").split()).strip()[:max_length]
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _representative_segments(text_parts: Sequence[str], limit: int) -> List[Tuple[int, str]]:
    parts = [(index, " ".join(str(text or "").split()).strip()) for index, text in enumerate(text_parts)]
    parts = [(index, text) for index, text in parts if text]
    if len(parts) <= limit:
        return parts
    positions = {
        min(len(parts) - 1, round(step * (len(parts) - 1) / max(1, limit - 1)))
        for step in range(limit)
    }
    return [parts[index] for index in sorted(positions)]


def _normalize_queries(values: Iterable[Any], limit: int) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        query = normalize_search_query(str(value or ""), max_words=12)
        key = query.casefold()
        if not query or not 2 <= len(query.split()) <= 12 or key in seen:
            continue
        seen.add(key)
        result.append(query)
        if len(result) >= limit:
            break
    return result


def _normalize_search_routes(values: Iterable[Any], limit: int) -> List[Dict[str, str]]:
    """Validate Gemini's universal exact-to-associative retrieval ladder."""
    allowed_tiers = {"exact", "subject", "environment", "associative"}
    result: List[Dict[str, str]] = []
    seen = set()
    for value in values or []:
        if not isinstance(value, dict):
            continue
        query = normalize_search_query(str(value.get("query") or ""), max_words=12)
        tier = str(value.get("tier") or "").strip().casefold()
        purpose = " ".join(str(value.get("purpose") or "").split()).strip()[:160]
        key = query.casefold()
        if (
            tier not in allowed_tiers
            or not 2 <= len(query.split()) <= 12
            or key in seen
        ):
            continue
        seen.add(key)
        result.append({"query": query, "tier": tier, "purpose": purpose})
        if len(result) >= limit:
            break
    return result


def _fallback_brief(theme: str, text_parts: Sequence[str], settings: Dict[str, Any]) -> Dict[str, Any]:
    max_segments = _bounded_int(settings.get("visual_brief_max_segments"), 14, 3, 30)
    max_queries = _bounded_int(settings.get("visual_brief_max_queries"), 8, 2, 20)
    selected = _representative_segments(text_parts, max_segments)
    segments = []
    query_candidates: List[str] = []
    for index, text in selected:
        visual_core = extract_visual_core(text, max_words=8)
        query = normalize_search_query(visual_core, max_words=8)
        if len(query.split()) >= 2:
            query_candidates.append(query)
        segments.append({
            "index": index,
            "text": text[:280],
            "subjects": [visual_core] if visual_core else [],
            "actions": [],
            "setting": "",
            "queries": [query] if len(query.split()) >= 2 else [],
        })
    return {
        "version": 1,
        "theme": " ".join(str(theme or "").split()).strip(),
        "summary": " ".join(str(theme or "").split()).strip(),
        "entities": [],
        "segments": segments,
        "search_queries": _normalize_queries(query_candidates, max_queries),
        "search_routes": [],
        "negative_cues": [],
        "script_fingerprint": script_fingerprint(text_parts),
        "brief_fingerprint": _brief_fingerprint(theme, text_parts),
        "api_generated": False,
    }


def build_visual_brief(
    theme: str,
    text_parts: Sequence[str] | None,
    api_keys: Sequence[str] | None = None,
    settings: Optional[Dict[str, Any]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Turn an arbitrary narration into concrete, searchable visual intent."""
    settings = settings or {}
    log = log_callback or (lambda _message: None)
    clean_parts = [" ".join(str(part or "").split()).strip() for part in (text_parts or [])]
    clean_parts = [part for part in clean_parts if part]
    fallback = _fallback_brief(theme, clean_parts, settings)
    cache_key = fallback["brief_fingerprint"]
    enabled = bool(settings.get("visual_relevance_enabled", True))
    usable_keys = [str(key).strip() for key in (api_keys or []) if str(key or "").strip()]
    # The shared Gemini client speaks the native Gemini multimodal protocol.
    usable_keys = [key for key in usable_keys if not key.startswith("sk-aitunnel-")]
    with _cache_lock:
        cached = _brief_cache.get(cache_key)
        if cached and (cached.get("api_generated") or not (enabled and clean_parts and usable_keys)):
            return json.loads(json.dumps(cached, ensure_ascii=False))

    if not enabled or not clean_parts or not usable_keys:
        with _cache_lock:
            _brief_cache[cache_key] = fallback
        return fallback

    prompt_segments = [
        {"index": segment["index"], "text": segment["text"]}
        for segment in fallback["segments"]
    ]
    max_queries = _bounded_int(settings.get("visual_brief_max_queries"), 8, 2, 20)
    prompt = f"""Create a universal visual retrieval brief from this narration.

TOPIC: {fallback['theme']}
NARRATION SEGMENTS:
{json.dumps(prompt_segments, ensure_ascii=False)}

Return JSON only. Identify concrete things that can actually be seen: named
subjects, objects, actions, places, environments, processes and distinctive
visual attributes. Do not invent names or facts. A named item must be present in
the input or be its direct translation/transliteration. Keep abstract claims out
of search queries unless they are grounded in a visible scene.

For each supplied segment index return subjects, actions, setting and up to two
YouTube-friendly search queries. Also build search_routes: a universal retrieval
ladder with tier exact, subject, environment, or associative. It must cover:
1. exact — the concrete narrated event/person/place when searchable;
2. subject — the same visible object, activity or process without rare names;
3. environment — geography, architecture, interiors, era or surroundings;
4. associative — honest nearby B-roll which supports the idea or mood without
   pretending to depict the exact event.

For compound topics, deliberately separate their visible dimensions. A rare
event in a place should yield both generic footage of the event/activity and
independent footage of the place/environment. Prefer concrete scenes such as
objects, people doing things, interiors, exteriors and landscapes; never use
abstract symbolism or unrelated viral material. Include at least one useful
route for every applicable tier and enough diverse routes to fill the limit.

Also return a deduplicated global search_queries list with at most {max_queries}
entries, ordered from the most specific script content to broader but still
faithful visible context. Queries may use the original language or a useful
search-language translation. Each query must have 2-12 words. negative_cues
should describe visibly wrong content types only when the narration itself makes
that distinction.
"""
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string"},
                        "visual_cues": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name"],
                },
            },
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "subjects": {"type": "array", "items": {"type": "string"}},
                        "actions": {"type": "array", "items": {"type": "string"}},
                        "setting": {"type": "string"},
                        "queries": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["index", "subjects", "actions", "queries"],
                },
            },
            "search_queries": {"type": "array", "items": {"type": "string"}},
            "search_routes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "tier": {
                            "type": "string",
                            "enum": ["exact", "subject", "environment", "associative"],
                        },
                        "purpose": {"type": "string"},
                    },
                    "required": ["query", "tier"],
                },
            },
            "negative_cues": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["segments", "search_queries", "search_routes"],
    }

    from core.gemini_client import GeminiClient

    ai_data: Optional[Dict[str, Any]] = None
    for key in usable_keys:
        response = GeminiClient(key, log_callback=lambda _message: None).generate_json(
            prompt,
            custom_schema=schema,
            temperature=0.0,
            max_tokens=2200,
        )
        if response.success and isinstance(response.data, dict):
            ai_data = response.data
            break
        if response.terminal:
            break
    if not ai_data:
        with _cache_lock:
            _brief_cache[cache_key] = fallback
        log("   🧭 Визуальный бриф: используется локальный универсальный план")
        return fallback

    valid_indices = {segment["index"] for segment in fallback["segments"]}
    ai_segments: Dict[int, Dict[str, Any]] = {}
    query_candidates: List[Any] = list(ai_data.get("search_queries") or [])
    for item in ai_data.get("segments") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if index not in valid_indices:
            continue
        queries = _normalize_queries(item.get("queries") or [], 2)
        query_candidates.extend(queries)
        ai_segments[index] = {
            "subjects": _unique_text(item.get("subjects") or [], 8, 80),
            "actions": _unique_text(item.get("actions") or [], 6, 80),
            "setting": " ".join(str(item.get("setting") or "").split())[:120],
            "queries": queries,
        }

    merged_segments = []
    for segment in fallback["segments"]:
        merged = dict(segment)
        if segment["index"] in ai_segments:
            ai_segment = ai_segments[segment["index"]]
            for key in ("subjects", "actions", "setting", "queries"):
                if ai_segment.get(key):
                    merged[key] = ai_segment[key]
        merged_segments.append(merged)

    entities = []
    for item in ai_data.get("entities") or []:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name") or "").split()).strip()[:100]
        if not name:
            continue
        entities.append({
            "name": name,
            "kind": " ".join(str(item.get("kind") or "").split())[:60],
            "visual_cues": _unique_text(item.get("visual_cues") or [], 6, 80),
        })
        if len(entities) >= 16:
            break

    search_routes = _normalize_search_routes(
        ai_data.get("search_routes") or [], max_queries
    )
    route_queries = [item["query"] for item in search_routes]
    brief = dict(fallback)
    brief.update({
        "summary": " ".join(str(ai_data.get("summary") or fallback["summary"]).split())[:300],
        "entities": entities,
        "segments": merged_segments,
        "search_queries": _normalize_queries(
            route_queries + query_candidates + fallback["search_queries"], max_queries
        ),
        "search_routes": search_routes,
        "negative_cues": _unique_text(ai_data.get("negative_cues") or [], 10, 100),
        "api_generated": True,
    })
    with _cache_lock:
        _brief_cache[cache_key] = brief
    log(
        f"   🧭 Визуальный бриф: {len(brief['entities'])} сущностей, "
        f"{len(brief['search_queries'])} запросов, "
        f"{len(brief['search_routes'])} маршрутов поиска"
    )
    return json.loads(json.dumps(brief, ensure_ascii=False))


def _path_key(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(path)


def _file_signature(path: str) -> str:
    try:
        stat = Path(path).stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        return "missing"


def get_clip_visual_match(path: str, narration_fingerprint: str) -> Optional[Dict[str, Any]]:
    """Return cached frame-level evidence for final timeline matching."""
    key = (str(narration_fingerprint or ""), _path_key(path))
    with _cache_lock:
        match = _visual_matches.get(key)
        if not match or match.get("file_signature") != _file_signature(path):
            return None
        return dict(match)


def _store_clip_visual_match(path: str, narration_fingerprint: str, match: Dict[str, Any]) -> None:
    stored = dict(match)
    stored["path"] = str(path)
    stored["file_signature"] = _file_signature(path)
    with _cache_lock:
        _visual_matches[(narration_fingerprint, _path_key(path))] = stored


def _make_contact_sheet(path: str, frame_count: int) -> Optional[bytes]:
    """Sample a clip at distributed positions and return one compact JPEG."""
    try:
        import cv2
        import numpy as np

        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            return None
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total_frames <= 0:
            capture.release()
            return None
        if frame_count <= 1:
            positions = [0.5]
        else:
            positions = [0.12 + (0.76 * index / (frame_count - 1)) for index in range(frame_count)]
        frames = []
        cell_width, cell_height = 256, 160
        for position in positions:
            capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(total_frames - 1, int(position * total_frames))))
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            scale = min(cell_width / max(1, width), cell_height / max(1, height))
            resized = cv2.resize(
                frame,
                (max(1, int(width * scale)), max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
            canvas = np.zeros((cell_height, cell_width, 3), dtype=np.uint8)
            y = (cell_height - resized.shape[0]) // 2
            x = (cell_width - resized.shape[1]) // 2
            canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
            frames.append(canvas)
        capture.release()
        if not frames:
            return None
        sheet = np.concatenate(frames, axis=1)
        ok, encoded = cv2.imencode(".jpg", sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 76])
        return encoded.tobytes() if ok else None
    except Exception:
        return None


def _visual_score_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "clips": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "clip_id": {"type": "string"},
                        "score": {"type": "integer"},
                        "reject": {"type": "boolean"},
                        "best_segment_index": {"type": "integer"},
                        "matched_subjects": {"type": "array", "items": {"type": "string"}},
                        "segment_scores": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "index": {"type": "integer"},
                                    "score": {"type": "integer"},
                                },
                                "required": ["index", "score"],
                            },
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["clip_id", "score", "reject", "best_segment_index"],
                },
            }
        },
        "required": ["clips"],
    }


def _score_visual_batch(
    labelled_images: List[Dict[str, Any]],
    brief: Dict[str, Any],
    api_keys: Sequence[str],
) -> List[Dict[str, Any]]:
    entities = [item.get("name") for item in brief.get("entities") or [] if item.get("name")]
    segments = [
        {
            "index": item.get("index"),
            "text": str(item.get("text") or "")[:180],
            "subjects": item.get("subjects") or [],
            "actions": item.get("actions") or [],
            "setting": item.get("setting") or "",
        }
        for item in brief.get("segments") or []
    ]
    prompt = f"""Evaluate each labelled contact sheet against one narration.

TOPIC: {brief.get('theme', '')}
VISIBLE ENTITIES: {json.dumps(entities, ensure_ascii=False)}
NARRATION SEGMENTS: {json.dumps(segments, ensure_ascii=False)}
WRONG VISUAL CUES: {json.dumps(brief.get('negative_cues') or [], ensure_ascii=False)}

Each image is a contact sheet containing distributed frames from one video clip.
Return one result for every IMAGE LABEL. Score visual evidence, not filename or
assumed source metadata: 80-100 directly shows a concrete narrated subject or
action; 55-79 clearly fits a specific segment; 30-54 is faithful contextual
B-roll; 0-29 is contradictory, unreadable, or genuinely unrelated. Geographic
scenery, architecture, interiors, era context, generic versions of the narrated
activity, and other honest associative B-roll should score 30-54 when they
support the topic even if they do not show the exact named event. Do not reject
a clip merely because it is broader than the narration. Set reject true only for
a visible contradiction, a wrong visual domain, or score below 30.
best_segment_index must be one supplied index, or -1 when no segment fits.
segment_scores may contain up to three strongest segment matches. Keep reason
under 20 words. JSON only.
"""
    from core.gemini_client import GeminiClient

    for key in api_keys:
        if str(key).startswith("sk-aitunnel-"):
            continue
        response = GeminiClient(str(key), log_callback=lambda _message: None).generate_json_with_images(
            prompt,
            labelled_images,
            custom_schema=_visual_score_schema(),
            temperature=0.0,
            max_tokens=1800,
        )
        if response.success:
            data = response.data
            if isinstance(data, dict) and isinstance(data.get("clips"), list):
                return data["clips"]
        if response.terminal:
            break
    return []


def _validated_match(item: Dict[str, Any], valid_segments: set[int]) -> Optional[Dict[str, Any]]:
    clip_id = str(item.get("clip_id") or "").strip()
    if not clip_id:
        return None
    score = int(round(_bounded_float(item.get("score"), 0, 0, 100)))
    try:
        best_segment = int(item.get("best_segment_index", -1))
    except (TypeError, ValueError):
        best_segment = -1
    if best_segment not in valid_segments:
        best_segment = -1
    segment_scores: Dict[int, int] = {}
    for candidate in item.get("segment_scores") or []:
        if not isinstance(candidate, dict):
            continue
        try:
            index = int(candidate.get("index"))
        except (TypeError, ValueError):
            continue
        if index in valid_segments:
            segment_scores[index] = int(round(_bounded_float(candidate.get("score"), 0, 0, 100)))
    return {
        "clip_id": clip_id,
        "score": score,
        "reject": bool(item.get("reject")) or score < 30,
        "best_segment_index": best_segment,
        "matched_subjects": _unique_text(item.get("matched_subjects") or [], 8, 80),
        "segment_scores": segment_scores,
        "reason": " ".join(str(item.get("reason") or "").split())[:160],
    }


def rank_clips_by_visual_relevance(
    clip_paths: Sequence[str],
    brief: Optional[Dict[str, Any]],
    api_keys: Sequence[str] | None,
    settings: Optional[Dict[str, Any]] = None,
    required_count: int = 0,
    log_callback: Optional[Callable[[str], None]] = None,
) -> VisualRankingResult:
    """Rank clip paths using a bounded number of frame-analysis calls."""
    settings = settings or {}
    log = log_callback or (lambda _message: None)
    unique_paths = list(dict.fromkeys(str(path) for path in (clip_paths or []) if path))
    empty_result = VisualRankingResult(unique_paths, unique_paths, [], {})
    if (
        not unique_paths
        or not brief
        or not brief.get("script_fingerprint")
        or not settings.get("visual_relevance_enabled", True)
    ):
        return empty_result

    usable_keys = [str(key).strip() for key in (api_keys or []) if str(key or "").strip()]
    usable_keys = [key for key in usable_keys if not key.startswith("sk-aitunnel-")]
    narration_fp = str(brief["script_fingerprint"])
    frames_per_clip = _bounded_int(settings.get("visual_relevance_frames_per_clip"), 3, 2, 5)
    batch_size = _bounded_int(settings.get("visual_relevance_batch_size"), 4, 1, 6)
    max_calls = _bounded_int(settings.get("visual_relevance_max_api_calls"), 3, 0, 10)
    configured_max = _bounded_int(settings.get("visual_relevance_max_clips"), 14, 2, 60)
    dynamic_max = max(batch_size, max(1, int(required_count or 1)) * 2)
    candidates = unique_paths[:min(configured_max, dynamic_max)]

    matches: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    for path in candidates:
        cached = get_clip_visual_match(path, narration_fp)
        if cached:
            matches[path] = cached
        else:
            missing.append(path)

    calls_used = 0
    if usable_keys and max_calls > 0:
        for start in range(0, len(missing), batch_size):
            if calls_used >= max_calls:
                break
            batch_paths = missing[start:start + batch_size]
            labelled_images = []
            label_to_path = {}
            for index, path in enumerate(batch_paths):
                image_data = _make_contact_sheet(path, frames_per_clip)
                if not image_data:
                    continue
                label = f"CLIP_{calls_used}_{index}"
                labelled_images.append({"label": label, "mime_type": "image/jpeg", "data": image_data})
                label_to_path[label] = path
            if not labelled_images:
                continue
            calls_used += 1
            raw_scores = _score_visual_batch(labelled_images, brief, usable_keys)
            valid_segments = {int(item.get("index")) for item in brief.get("segments") or []}
            for raw_item in raw_scores:
                if not isinstance(raw_item, dict):
                    continue
                match = _validated_match(raw_item, valid_segments)
                if not match or match["clip_id"] not in label_to_path:
                    continue
                path = label_to_path[match["clip_id"]]
                _store_clip_visual_match(path, narration_fp, match)
                matches[path] = get_clip_visual_match(path, narration_fp) or match

    scored = [path for path in unique_paths if path in matches]
    if not scored:
        return empty_result

    accept_score = _bounded_int(settings.get("visual_relevance_accept_score"), 52, 30, 90)
    uncertain_score = _bounded_int(settings.get("visual_relevance_uncertain_score"), 30, 0, accept_score)
    accepted = sorted(
        [path for path in scored if matches[path]["score"] >= accept_score and not matches[path]["reject"]],
        key=lambda path: matches[path]["score"],
        reverse=True,
    )
    uncertain = sorted(
        [
            path for path in scored
            if uncertain_score <= matches[path]["score"] < accept_score and not matches[path]["reject"]
        ],
        key=lambda path: matches[path]["score"],
        reverse=True,
    )
    unscored = [path for path in unique_paths if path not in matches]
    rejected = sorted(
        [path for path in scored if path not in accepted and path not in uncertain],
        key=lambda path: matches[path]["score"],
        reverse=True,
    )
    recommended = accepted + uncertain + unscored
    ranked = recommended + [path for path in rejected if path not in recommended]
    log(
        f"   👁️ Проверка кадров: {len(scored)} оценено, "
        f"{len(accepted)} точных, {len(uncertain)} контекстных, {len(rejected)} отклонено "
        f"({calls_used} Gemini-запросов)"
    )
    return VisualRankingResult(
        ranked_paths=ranked,
        recommended_paths=recommended,
        accepted_paths=accepted,
        matches=matches,
        scored_count=len(scored),
        analysis_available=True,
    )


def clear_visual_relevance_runtime_cache() -> None:
    """Clear per-process caches (used between independent generation batches/tests)."""
    with _cache_lock:
        _brief_cache.clear()
        _visual_matches.clear()


__all__ = [
    "VisualRankingResult",
    "build_visual_brief",
    "clear_visual_relevance_runtime_cache",
    "get_clip_visual_match",
    "rank_clips_by_visual_relevance",
    "script_fingerprint",
]
