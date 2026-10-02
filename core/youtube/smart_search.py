#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Smart Search
Генерация умных поисковых запросов через Gemini AI.
"""

import json
from pathlib import Path
import threading
import time
from typing import List, Dict, Callable

from .query_planner import (
    SearchQuery,
    build_query_ladder,
    merge_query_plans,
    normalize_search_query,
    parse_query_plan_payload,
)


def _dummy_log(msg):
    """Заглушка для логирования."""
    print(msg)


# Кэш для semantic fallback queries и smart queries
_semantic_fallback_cache: Dict[str, List[str]] = {}
_semantic_fallback_lock = threading.Lock()

_smart_search_cache: Dict[str, List[str]] = {}
_smart_search_lock = threading.Lock()

_visual_plan_cache: Dict[str, List[SearchQuery]] = {}
_visual_plan_lock = threading.Lock()

_SEARCH_CACHE_FILE = Path("cache/search_queries_cache.json")
_QUERY_PLAN_VERSION = 2


def _response_text(response: Dict) -> str:
    return (
        (response.get("candidates") or [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )


def _generate_json_text(
    prompt: str,
    keys: List[str],
    log_callback: Callable,
    *,
    max_attempts_per_key: int = 2,
) -> str:
    """Call the shared Gemini REST dispatcher with bounded key rotation."""

    from core.gemini_models import FAST_TEXT_MODEL
    from core.text_generator import TextGenerator

    attempts_per_key = max(1, min(2, int(max_attempts_per_key or 1)))
    for key_index, current_key in enumerate(key for key in keys if key):
        for attempt in range(attempts_per_key):
            try:
                response = TextGenerator()._rest_generate_content(
                    model=FAST_TEXT_MODEL,
                    api_key=current_key,
                    prompt_text=prompt,
                    generation_config={"temperature": 0.2, "maxOutputTokens": 1100},
                )
                text = _response_text(response).strip()
                if text:
                    if key_index > 0:
                        log_callback(f"Gemini query planner switched to key #{key_index + 1}")
                    return text
                break
            except Exception as exc:
                error = str(exc)
                transient = any(
                    marker in error
                    for marker in ("429", "RESOURCE_EXHAUSTED", "500", "502", "503", "504")
                )
                if not transient:
                    log_callback(f"Gemini query planner unavailable: {error[:120]}")
                    break
                if attempt + 1 < attempts_per_key:
                    # One short retry is enough; the outer pipeline has its own
                    # bounded search ladder and must not stall on model quota.
                    time.sleep(0.5 * (attempt + 1))
    return ""


def _serialize_plan(plan: List[SearchQuery]) -> List[Dict[str, object]]:
    return [
        {
            "query": item.text,
            "tier": item.tier,
            "language": item.language,
            "source": item.source,
            "weight": item.weight,
        }
        for item in plan
    ]


def _deserialize_plan(items) -> List[SearchQuery]:
    if not isinstance(items, list):
        return []
    plan = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("query") or item.get("text") or "").strip()
        if not text:
            continue
        plan.append(
            SearchQuery(
                text=text,
                tier=str(item.get("tier") or "context"),
                language=str(item.get("language") or "auto"),
                source=str(item.get("source") or "youtube"),
                weight=int(item.get("weight") or 72),
            )
        )
    return plan

try:
    if _SEARCH_CACHE_FILE.exists():
        with open(_SEARCH_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            _semantic_fallback_cache = data.get('semantic', {})
            _smart_search_cache = data.get('smart', {})
            _visual_plan_cache = {
                key: _deserialize_plan(value)
                for key, value in data.get('visual_plan', {}).items()
            }
except Exception:
    pass

def _save_search_cache():
    try:
        _SEARCH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SEARCH_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'semantic': _semantic_fallback_cache,
                'smart': _smart_search_cache,
                'visual_plan': {
                    key: _serialize_plan(value)
                    for key, value in _visual_plan_cache.items()
                },
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def generate_smart_queries(
    theme: str,
    api_key: str = None,
    api_keys: List[str] = None,
    log_callback: Callable = None,
    max_queries: int = 5
) -> List[str]:
    """Compatibility API backed by the same universal tiered planner."""

    log_callback = log_callback or _dummy_log
    query_limit = max(1, int(max_queries or 1))
    cache_key = f"v{_QUERY_PLAN_VERSION}:{theme.casefold().strip()}:{query_limit}"
    with _smart_search_lock:
        cached = _smart_search_cache.get(cache_key)
        if cached:
            return list(cached[:query_limit])

    plan = generate_visual_search_plan(
        theme=theme,
        api_key=api_key,
        api_keys=api_keys,
        log_callback=log_callback,
        max_queries=query_limit,
    )
    queries = [item.text for item in plan]
    with _smart_search_lock:
        _smart_search_cache[cache_key] = queries
        _save_search_cache()
    return queries[:query_limit]


def generate_visual_search_plan(
    theme: str,
    translated_theme: str = "",
    api_key: str = None,
    api_keys: List[str] = None,
    log_callback: Callable = None,
    max_queries: int = 10,
) -> List[SearchQuery]:
    """Create a multilingual strict-to-context visual search plan.

    English is always useful for global footage, while additional languages
    are selected for the topic (for example German/Polish/Russian archives for
    Eastern European history) instead of being added at random.
    """
    log_callback = log_callback or _dummy_log
    deterministic = build_query_ladder(
        theme,
        translated_theme,
        max_queries=max(4, min(max_queries, 6)),
    )
    keys = [key for key in (api_keys or [api_key]) if key]
    if not keys:
        return deterministic[:max_queries]
    cache_key = (
        f"v{_QUERY_PLAN_VERSION}:{theme.casefold().strip()}::"
        f"{translated_theme.casefold().strip()}::{int(max_queries)}"
    )
    with _visual_plan_lock:
        cached = _visual_plan_cache.get(cache_key)
        if cached:
            return cached[:max_queries]

    prompt = f"""Create a multilingual YouTube B-roll search plan for this exact topic.

TOPIC: {theme}
ENGLISH TRANSLATION: {translated_theme or "unknown"}

Return ONLY a JSON array of objects with keys query, tier, language.
Allowed tiers: exact, subject, context, broad.

Rules:
1. Keep the exact person/place/object in exact and subject queries.
2. Context queries must preserve role, action, event, place, era, or visual domain.
3. If exact footage is unlikely, broaden gradually: person -> role/action -> event/place -> era/domain.
4. Include English and the original language. Add 1-3 other languages ONLY when archives in those languages are likely to contain relevant footage.
5. Use concrete visible scenes, not abstract ideas. Never drift to merely popular content.
6. Each query must contain 2-12 words. Prefer real footage, archive, documentary, newsreel, reenactment, in action where appropriate.
7. Include at least 3 context queries whenever the exact subject may have little footage.
8. Produce at most {max_queries} diverse queries.

Example shape:
[
  {{"query":"named subject real footage","tier":"exact","language":"en"}},
  {{"query":"related visible action documentary","tier":"context","language":"en"}}
]"""

    text = _generate_json_text(prompt, keys, log_callback)
    ai_plan = parse_query_plan_payload(text, max_queries=max_queries)
    if ai_plan:
        # Exact/subject identity belongs to the user topic and its real
        # translation. Letting a model invent those tiers can silently replace
        # an unknown person with a famous but unrelated one.
        ai_plan = [item for item in ai_plan if item.tier in {"context", "broad"}]
    if ai_plan:
        merged = merge_query_plans(deterministic, ai_plan, max_queries=max_queries)
        with _visual_plan_lock:
            _visual_plan_cache[cache_key] = merged
            _save_search_cache()
        languages = sorted({item.language for item in merged if item.language})
        log_callback(
            f"Multilingual visual plan: {len(merged)} queries; "
            f"languages={','.join(languages) or 'auto'}"
        )
        return merged
    return deterministic[:max_queries]


def generate_semantic_fallback_queries(
    theme: str,
    api_key: str = None,
    api_keys: List[str] = None,
    log_callback: Callable = None,
    max_queries: int = 6,
    failed_queries: List[str] = None,
    failure_reasons: Dict[str, int] = None,
) -> List[str]:
    """Generate new grounded context routes after earlier routes failed.

    Failure causes are supplied separately so the model does not incorrectly
    assume that every miss was a slideshow.  The original topic remains the
    immutable relevance target; these strings are search routes only.
    """

    log_callback = log_callback or _dummy_log
    query_limit = max(1, min(12, int(max_queries or 1)))
    keys = [key for key in (api_keys or [api_key]) if key]
    if not keys:
        log_callback("⚠️ Gemini API ключ не найден")
        return []

    failed_queries = [
        normalize_search_query(query)
        for query in (failed_queries or [])
        if normalize_search_query(query)
    ]
    normalized_reasons = {
        str(reason): max(0, int(count or 0))
        for reason, count in (failure_reasons or {}).items()
        if count
    }
    cache_key = f"v{_QUERY_PLAN_VERSION}:semantic:{theme.casefold().strip()[:160]}"
    if not failed_queries and not normalized_reasons:
        with _semantic_fallback_lock:
            cached = _semantic_fallback_cache.get(cache_key)
            if cached:
                return list(cached[:query_limit])

    attempted_block = "\n".join(f"- {query}" for query in failed_queries[:16]) or "- none"
    reason_block = (
        "\n".join(f"- {reason}: {count}" for reason, count in sorted(normalized_reasons.items()))
        or "- no structured failure reason available"
    )
    prompt = f"""Create the next bounded YouTube B-roll search routes.

IMMUTABLE ORIGINAL TOPIC: {theme}
ALREADY ATTEMPTED ROUTES:
{attempted_block}
OBSERVED FAILURE CATEGORIES (do not invent other causes):
{reason_block}

Return ONLY a JSON array of objects with keys query, tier, language.
Allowed tiers here: context, broad.

Rules:
1. Produce at most {query_limit} NEW routes, never repeat an attempted route.
2. Preserve a defensible visual relation to the original topic through at
   least one of: subject class, visible action, place/region, era/event,
   physical setting, or an immediately associated object.
3. Progress gradually from close context to broad associative B-roll.  Broad
   does not mean random, popular, or merely cinematic.
4. Prefer scenes with real motion and enough source duration for several cuts.
5. Use the original language and English. Add another language only when it is
   genuinely useful for regional or archival footage.
6. Each query must contain 2-12 words and name a visible scene.
7. Search-route wording must not redefine the immutable original topic.
"""

    text = _generate_json_text(prompt, keys, log_callback)
    plan = [
        item
        for item in parse_query_plan_payload(text, max_queries=query_limit * 2)
        if item.tier in {"context", "broad"}
    ]
    attempted = {query.casefold() for query in failed_queries}
    queries = []
    seen = set()
    for item in plan:
        key = item.text.casefold()
        if key in attempted or key in seen:
            continue
        seen.add(key)
        queries.append(item.text)
        if len(queries) >= query_limit:
            break

    if queries and not failed_queries and not normalized_reasons:
        with _semantic_fallback_lock:
            _semantic_fallback_cache[cache_key] = list(queries)
            _save_search_cache()
    if queries:
        log_callback(f"✅ Gemini дал {len(queries)} новых семантических маршрутов")
    return queries
