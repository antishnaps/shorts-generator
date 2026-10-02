"""Universal, tiered search-query planning for visual sources."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Iterable, List, Sequence


# Python's Unicode ``\w`` covers Hangul, Han, Arabic, Devanagari and other
# scripts. Excluding underscore keeps generated search terms natural.
_TOKEN_RE = re.compile(r"[^\W_][\w-]*", re.UNICODE)
_CLAUSE_RE = re.compile(r"\s*(?:[—–;|]|\.(?:\s|$))\s*")
_NOISE_WORDS = {
    "a", "about", "an", "and", "best", "facts", "for", "from", "how", "in",
    "interesting", "most", "of", "on", "secret", "the", "to", "top", "video",
    "what", "why", "with", "who", "was", "were", "is", "are",
    "без", "в", "во", "для", "из", "и", "или", "интересный", "как", "который",
    "на", "не", "необычный", "о", "об", "от", "по", "почему", "про", "ролик",
    "самый", "секрет", "с", "со", "топ", "факты", "что", "это", "был", "была",
    "были", "стал", "стала", "его", "ее", "их",
}
_VISUAL_SUFFIXES = {
    "exact": ("real footage", "documentary"),
    "subject": ("real footage", "in action"),
    "context": ("documentary footage", "historical footage"),
    "broad": ("real footage", "b roll"),
}


@dataclass(frozen=True)
class SearchQuery:
    """A query with its fallback strength and intended source."""

    text: str
    tier: str = "exact"
    language: str = "auto"
    source: str = "youtube"
    weight: int = 100


def detect_query_language(value: str) -> str:
    """Return a stable script-level language hint for query routing."""
    text = str(value or "")
    if re.search(r"[\uac00-\ud7af]", text):
        return "ko"
    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\u0600-\u06ff\u0750-\u077f]", text):
        return "ar"
    if re.search(r"[\u0590-\u05ff]", text):
        return "he"
    if re.search(r"[\u0900-\u097f]", text):
        return "hi"
    if re.search(r"[\u0e00-\u0e7f]", text):
        return "th"
    if re.search(r"[А-Яа-яЁё]", text):
        return "ru"
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text):
        return "cjk"
    return "en" if re.search(r"[A-Za-z]", text) else "auto"


def parse_query_plan_payload(payload, max_queries: int = 10) -> List[SearchQuery]:
    """Validate an AI query plan and discard drift-prone malformed entries."""
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        match = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if not match:
            return []
        try:
            payload = json.loads(match.group(0))
        except (TypeError, ValueError):
            return []
    if not isinstance(payload, list):
        return []

    allowed_tiers = {"exact", "subject", "context", "broad"}
    weights = {"exact": 100, "subject": 88, "context": 72, "broad": 58}
    result: List[SearchQuery] = []
    seen = set()
    for item in payload:
        if isinstance(item, str):
            text, tier, language = item, "context", "auto"
        elif isinstance(item, dict):
            text = item.get("query") or item.get("text") or ""
            tier = str(item.get("tier") or "context").lower()
            language = str(item.get("language") or "auto").lower()
        else:
            continue
        normalized = normalize_search_query(text)
        if tier not in allowed_tiers or not 2 <= len(normalized.split()) <= 12:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(
            SearchQuery(
                normalized,
                tier=tier,
                language=language if language != "auto" else detect_query_language(normalized),
                weight=weights[tier],
            )
        )
        if len(result) >= max_queries:
            break
    return result


def merge_query_plans(*plans: Sequence[SearchQuery], max_queries: int = 10) -> List[SearchQuery]:
    """Merge plans strict-first while reserving room for semantic context."""
    result: List[SearchQuery] = []
    seen = set()
    tier_limits = {
        "exact": min(2, max_queries),
        "subject": min(2, max_queries),
        "context": max(1, max_queries // 3),
        "broad": 1,
    }
    for tier in ("exact", "subject", "context", "broad"):
        added_for_tier = 0
        for plan in plans:
            for item in plan:
                key = item.text.casefold()
                if item.tier != tier or key in seen:
                    continue
                if added_for_tier >= tier_limits[tier]:
                    continue
                seen.add(key)
                result.append(item)
                added_for_tier += 1
                if len(result) >= max_queries:
                    return result
    # If a tier was unavailable, fill remaining slots without changing order.
    for tier in ("exact", "subject", "context", "broad"):
        for plan in plans:
            for item in plan:
                key = item.text.casefold()
                if item.tier != tier or key in seen:
                    continue
                seen.add(key)
                result.append(item)
                if len(result) >= max_queries:
                    return result
    return result


def normalize_search_query(value: str, max_words: int = 12) -> str:
    """Return a bounded search-safe phrase while preserving any language."""
    tokens = _TOKEN_RE.findall(str(value or ""))
    return " ".join(tokens[:max_words]).strip()


def extract_visual_core(value: str, max_words: int = 8) -> str:
    """Remove prompt filler but preserve concrete subjects, actions and places."""
    result = []
    seen = set()
    for token in _TOKEN_RE.findall(str(value or "")):
        lowered = token.casefold()
        if lowered in _NOISE_WORDS or len(lowered) < 2 or lowered in seen:
            continue
        seen.add(lowered)
        result.append(token)
        if len(result) >= max_words:
            break
    return " ".join(result)


def extract_subject(value: str, max_words: int = 5) -> str:
    """Keep the leading named subject instead of truncating arbitrary topic words."""
    text = str(value or "").strip()
    first_clause = _CLAUSE_RE.split(text, maxsplit=1)[0]
    if "," in first_clause:
        first_clause = first_clause.split(",", 1)[0]
    subject = extract_visual_core(first_clause, max_words=max_words)
    if len(subject.split()) == 1:
        # One-word names are too ambiguous; retain a little context.
        subject = extract_visual_core(text, max_words=max(2, max_words))
    return subject


def _append_unique(result: List[str], values: Iterable[str], max_queries: int) -> None:
    seen = {item.casefold() for item in result}
    for value in values:
        normalized = normalize_search_query(value)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        result.append(normalized)
        seen.add(key)
        if len(result) >= max_queries:
            return


def _append_plan(
    result: List[SearchQuery],
    values: Iterable[str],
    *,
    tier: str,
    weight: int,
    max_queries: int,
    language: str = "auto",
    source: str = "youtube",
    allow_single_word: bool = False,
) -> None:
    seen = {item.text.casefold() for item in result}
    for value in values:
        normalized = normalize_search_query(value)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        # Broad one-word searches are the main source of topic drift.
        if tier != "exact" and len(normalized.split()) < 2 and not allow_single_word:
            continue
        result.append(SearchQuery(normalized, tier, language, source, weight))
        seen.add(key)
        if len(result) >= max_queries:
            return


def build_query_ladder(
    theme: str,
    translated_theme: str = "",
    contextual_queries: Sequence[str] | None = None,
    max_queries: int = 10,
) -> List[SearchQuery]:
    """Build a strict-to-broad ladder without losing the topic's visual domain.

    ``contextual_queries`` are normally produced by the semantic planner. They
    should describe roles, actions, places or era footage rather than unrelated
    popular content.
    """
    original = normalize_search_query(theme)
    translated = normalize_search_query(translated_theme)
    original_subject = extract_subject(theme)
    translated_subject = extract_subject(translated_theme) if translated else ""
    original_core = extract_visual_core(theme)
    translated_core = extract_visual_core(translated_theme) if translated else ""
    result: List[SearchQuery] = []

    _append_plan(result, (original, translated), tier="exact", weight=100, max_queries=max_queries)
    _append_plan(
        result,
        (translated_subject, original_subject),
        tier="subject",
        weight=88,
        max_queries=max_queries,
    )
    _append_plan(
        result,
        contextual_queries or (),
        tier="context",
        weight=72,
        max_queries=max_queries,
    )
    _append_plan(
        result,
        (translated_core, original_core),
        tier="broad",
        weight=58,
        max_queries=max_queries,
    )

    base = translated_subject or original_subject or translated_core or original_core
    if base and len(result) < max_queries:
        suffixes = _VISUAL_SUFFIXES["subject"]
        _append_plan(
            result,
            (f"{base} {suffix}" for suffix in suffixes),
            tier="subject",
            weight=82,
            max_queries=max_queries,
        )
    return result[:max_queries]


def _strip_terminal_location(value: str) -> str:
    """Remove a trailing location while keeping the visible subject/action."""
    normalized = normalize_search_query(value)
    if not normalized:
        return ""
    patterns = (
        r"\s+(?:\u0438\u0437|from|near)\s+[\w-]+(?:\s+[\w-]+){0,2}$",
        r"\s+(?:\u0432|\u0432\u043e|\u043d\u0430|in|at)\s+[\w-]+(?:\s+[\w-]+){0,1}$",
    )
    for pattern in patterns:
        shortened = re.sub(pattern, "", normalized, flags=re.IGNORECASE).strip()
        if shortened != normalized and shortened:
            return shortened
    return normalized


def build_exhaustive_query_ladder(
    theme: str,
    translated_theme: str = "",
    contextual_queries: Sequence[str] | None = None,
    max_queries: int = 20,
) -> List[SearchQuery]:
    """Build a long strict-to-generic YouTube fallback ladder.

    Ordinary search deliberately avoids ambiguous one-word queries. This
    exhaustive ladder is used only after specific searches failed, so its last
    tier may reduce a niche topic to a single reusable visual subject.
    """
    max_queries = max(1, int(max_queries or 1))
    contexts = [normalize_search_query(item) for item in (contextual_queries or ())]
    result = build_query_ladder(
        theme,
        translated_theme,
        contextual_queries=contexts,
        max_queries=min(max_queries, 10),
    )

    original = normalize_search_query(theme)
    translated = normalize_search_query(translated_theme)
    location_relaxed = [
        _strip_terminal_location(value)
        for value in (original, translated, *contexts)
        if value
    ]
    _append_plan(
        result,
        location_relaxed,
        tier="context",
        weight=66,
        max_queries=max(1, max_queries - 1),
    )

    visual_cores = [
        extract_visual_core(value, max_words=8)
        for value in (*location_relaxed, original, translated)
        if value
    ]
    _append_plan(
        result,
        visual_cores,
        tier="broad",
        weight=56,
        max_queries=max(1, max_queries - 1),
    )

    # Progressively shorten the visible subject. The one-word candidate is
    # intentionally last: it is the final chance to get usable B-roll, not the
    # default search route.
    progressive = []
    terminal = []
    for value in visual_cores:
        words = normalize_search_query(value).split()
        if not words:
            continue
        for size in range(min(4, len(words)), 1, -1):
            progressive.append(" ".join(words[:size]))
        terminal.append(words[0])
    _append_plan(
        result,
        progressive,
        tier="broad",
        weight=50,
        max_queries=max(1, max_queries - 1),
    )
    _append_plan(
        result,
        terminal,
        tier="broad",
        weight=42,
        max_queries=max_queries,
        allow_single_word=True,
    )
    return result[:max_queries]


def build_universal_queries(
    theme: str,
    translated_theme: str = "",
    attempt: int = 1,
    max_queries: int = 5,
) -> List[str]:
    """Compatibility wrapper returning tiered YouTube query text."""
    plan = build_query_ladder(theme, translated_theme, max_queries=max_queries)
    if attempt > 1 and plan:
        base = next((item.text for item in plan if item.tier in {"subject", "broad"}), plan[0].text)
        suffixes = _VISUAL_SUFFIXES["context" if attempt == 2 else "broad"]
        values = [item.text for item in plan]
        _append_unique(values, (f"{base} {suffix}" for suffix in suffixes), max_queries)
        return values[:max_queries]
    return [item.text for item in plan]


def build_stock_queries(
    theme: str,
    translated_theme: str = "",
    contextual_queries: Sequence[str] | None = None,
    max_queries: int = 3,
) -> List[str]:
    """Build concrete English-first queries suitable for stock search engines."""
    translated_core = extract_visual_core(translated_theme, max_words=6)
    original_core = extract_visual_core(theme, max_words=6)
    contextual = [extract_visual_core(item, max_words=6) for item in (contextual_queries or ())]
    result: List[str] = []

    _append_unique(result, contextual, max_queries)
    _append_unique(result, (translated_core, original_core), max_queries)
    for core in contextual + [translated_core, original_core]:
        words = core.split()
        if len(words) > 3:
            _append_unique(result, (" ".join(words[:3]), " ".join(words[-3:])), max_queries)
    return [query for query in result[:max_queries] if len(query.split()) >= 2]


__all__ = [
    "SearchQuery",
    "build_exhaustive_query_ladder",
    "build_query_ladder",
    "build_stock_queries",
    "build_universal_queries",
    "detect_query_language",
    "extract_subject",
    "extract_visual_core",
    "merge_query_plans",
    "normalize_search_query",
    "parse_query_plan_payload",
]
