#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Relevance Functions
Handles video relevance checking and translation.
"""

import re
import json
import math
from pathlib import Path
from typing import Callable, Optional, List, Dict
from difflib import SequenceMatcher

from .utils import _dummy_log
import threading

_RELEVANCE_CACHE_FILE = Path("cache/relevance_cache.json")
_relevance_cache = {}
_relevance_cache_lock = threading.Lock()

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS = {
    "about", "and", "for", "from", "how", "the", "this", "video", "with",
    "без", "был", "для", "его", "как", "или", "это", "над", "под", "при",
    "про", "что", "эта", "этот", "из", "на", "по", "с", "со", "в", "во", "и",
}
_RU_SUFFIXES = (
    "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими", "ий", "ый", "ая",
    "яя", "ое", "ее", "ые", "ие", "ов", "ев", "ам", "ям", "ах", "ях", "ом",
    "ем", "ой", "ей", "ы", "и", "а", "я", "у", "ю", "е", "о",
)
_GENERIC_ROUTE_STEMS = {
    "archive", "archival", "broll", "b-roll", "clip", "clips", "documentary",
    "footage", "generic", "hd", "historical", "popular", "real", "scene",
    "scenes", "stock", "video", "videos",
}


def _meaningful_stems(text: str) -> List[str]:
    stems = []
    for token in _TOKEN_RE.findall(str(text or "").lower()):
        minimum_length = 2 if any(ord(char) > 127 for char in token) else 3
        if len(token) < minimum_length or token in _STOPWORDS:
            continue
        stem = token
        if re.search(r"[а-яё]", token) and len(token) >= 6:
            for suffix in _RU_SUFFIXES:
                if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                    stem = token[:-len(suffix)]
                    break
        stems.append(stem)
    return stems


def has_meaningful_keyword_overlap(video_title: str, theme: str) -> bool:
    """Return True when title and theme share at least one meaningful word stem."""
    return bool(set(_meaningful_stems(video_title)) & set(_meaningful_stems(theme)))


def calculate_topic_evidence(
    video_title: str,
    theme: str,
    video_description: str = "",
) -> Dict[str, object]:
    """Return compact lexical evidence that a candidate is about the original topic."""
    theme_stems = set(_meaningful_stems(theme)[:16])
    title_stems = set(_meaningful_stems(video_title))
    description_stems = set(_meaningful_stems(video_description)[:80])
    title_overlap = sorted(title_stems & theme_stems)
    description_overlap = sorted(description_stems & theme_stems)
    denominator = max(1, min(len(theme_stems), 6))
    title_coverage = len(title_overlap) / denominator
    description_coverage = len(description_overlap) / denominator
    score = min(55, len(title_overlap) * 22) + min(30, len(description_overlap) * 8)
    if title_overlap and description_overlap:
        score += 10
    if max(title_coverage, description_coverage) >= 0.5:
        score += 15
    return {
        "theme_terms": len(theme_stems),
        "title_overlap": title_overlap,
        "description_overlap": description_overlap,
        "title_overlap_count": len(title_overlap),
        "description_overlap_count": len(description_overlap),
        "title_coverage": round(title_coverage, 3),
        "description_coverage": round(description_coverage, 3),
        "score": max(0, min(100, score)),
    }


def has_meaningful_topic_evidence(
    video_title: str,
    theme: str,
    video_description: str = "",
) -> bool:
    """Return True when title or description contains enough original-topic evidence."""
    evidence = calculate_topic_evidence(video_title, theme, video_description)
    theme_terms = int(evidence["theme_terms"] or 0)
    if theme_terms <= 0:
        return False
    if int(evidence["title_overlap_count"] or 0) > 0:
        return True
    description_overlap = int(evidence["description_overlap_count"] or 0)
    if theme_terms <= 2:
        return description_overlap >= 1
    return description_overlap >= 2 or float(evidence["description_coverage"] or 0) >= 0.35


def has_specific_route_evidence(
    video_title: str,
    search_query: str,
    video_description: str = "",
) -> bool:
    """Return True for non-generic context routes that the candidate clearly matches."""
    route_stems = set(_meaningful_stems(search_query)[:16])
    specific_stems = {
        stem for stem in route_stems
        if stem not in _GENERIC_ROUTE_STEMS and not stem.isdigit()
    }
    if len(specific_stems) < 2:
        return False
    evidence = calculate_topic_evidence(video_title, search_query, video_description)
    return (
        int(evidence["title_overlap_count"] or 0) >= 2
        or int(evidence["score"] or 0) >= 40
    )


try:
    if _RELEVANCE_CACHE_FILE.exists():
        with open(_RELEVANCE_CACHE_FILE, 'r', encoding='utf-8') as f:
            _relevance_cache = json.load(f)
except Exception:
    pass


def _save_relevance_cache():
    try:
        _RELEVANCE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_RELEVANCE_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_relevance_cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def calculate_relevance(video_title: str, theme: str, video_description: str = "") -> int:
    """Score arbitrary topics using phrases and meaningful token overlap.

    Scoring layers (all capped at 100):
    - Sequence similarity of title vs theme (0..20)
    - Full theme phrase found in title (+55)
    - Per meaningful stem overlap (+18 each, capped at 54)
    - Majority stem coverage bonus (+12)
    - Per-word exact match in title: each theme word found verbatim (+5, up to +20)
    - Description stem overlap at half weight (0..10)
    """
    title_lower = str(video_title or "").lower().strip()
    theme_lower = str(theme or "").lower().strip()
    desc_lower = str(video_description or "").lower().strip()
    if not title_lower or not theme_lower:
        return 0

    theme_stems = set(_meaningful_stems(theme_lower)[:16])
    title_stems = set(_meaningful_stems(title_lower))
    description_stems = set(_meaningful_stems(desc_lower))
    overlap = title_stems & theme_stems
    description_overlap = description_stems & theme_stems

    score = int(SequenceMatcher(None, theme_lower, title_lower).ratio() * 20)
    if theme_lower in title_lower:
        score += 55
    elif theme_lower and theme_lower in desc_lower:
        score += 18
    score += min(54, len(overlap) * 18)
    if theme_stems and len(overlap) / min(len(theme_stems), 6) >= 0.5:
        score += 12

    # Per-word exact match bonus: every theme word (>=4 chars) that appears
    # verbatim as a whole word in the title gets +5 (capped at +20)
    word_bonus = 0
    for word in _TOKEN_RE.findall(theme_lower):
        minimum_length = 2 if any(ord(char) > 127 for char in word) else 4
        if len(word) < minimum_length:
            continue
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", title_lower):
            word_bonus += 5
    score += min(20, word_bonus)

    score += min(18, len(description_overlap) * 4)
    if not overlap and description_overlap and len(description_overlap) >= min(2, len(theme_stems)):
        score += 12
    if theme_stems and not overlap and not description_overlap:
        score = min(score, 18)
    return max(0, min(100, score))


def calculate_tiered_relevance(
    video_title: str,
    theme: str,
    search_query: str,
    tier: str = "exact",
    video_description: str = "",
) -> Dict[str, int]:
    """Score a candidate against both the topic and its approved search route.

    A semantic context route may rescue footage for an obscure subject, but its
    score is capped below an exact match. Broad routes are capped aggressively.
    """
    topic_score = calculate_relevance(video_title, theme, video_description)
    route_score = calculate_relevance(video_title, search_query, video_description)
    route_caps = {"exact": 100, "subject": 82, "context": 60, "broad": 38}
    cap = route_caps.get(str(tier or "broad").lower(), 38)
    effective_score = max(topic_score, min(route_score, cap))
    return {
        "topic": topic_score,
        "route": route_score,
        "effective": effective_score,
    }


def check_semantic_relevance_with_gemini(
    video_title: str,
    theme: str,
    api_keys: List[str],
    log_callback: Optional[Callable] = None,
    video_description: str = "",
    search_query: str = "",
    channel: str = "",
) -> int:
    """Check semantic relevance of a video to a theme via Gemini AI.

    Used for borderline cases (8-50% base relevance).
    Returns relevance 0-100, or -1 if check failed.
    """
    if log_callback is None:
        log_callback = _dummy_log

    if not api_keys:
        return -1

    cache_parts = [theme.lower().strip(), video_title.lower().strip()]
    if video_description or search_query or channel:
        cache_parts.extend(
            [
                str(video_description or "").lower().strip()[:240],
                str(search_query or "").lower().strip(),
                str(channel or "").lower().strip(),
            ]
        )
    cache_key = "::".join(cache_parts)
    with _relevance_cache_lock:
        if cache_key in _relevance_cache:
            log_callback(f"📦 Использована кэшированная релевантность для: {video_title[:30]}")
            return _relevance_cache[cache_key]

    # Use the same REST dispatcher as text generation.
    for key_index, api_key in enumerate(api_keys):
        try:
            from core.gemini_models import FAST_TEXT_MODEL
            from core.text_generator import TextGenerator

            description_preview = str(video_description or "").strip()[:1200]
            prompt = f"""Rate the relevance of a YouTube video to a content topic.

TOPIC: "{theme}"
SEARCH QUERY USED: "{search_query}"
VIDEO TITLE: "{video_title}"
CHANNEL: "{channel}"
VIDEO DESCRIPTION: "{description_preview}"

Rate from 0 to 100:
- 80-100: Direct source material for this exact topic or exact subject.
- 50-79: Clearly related visual source material for the same subject, era, place, process, object, or event.
- 20-49: Only loose background/context; usable only if no better source exists.
- 0-19: Generic, unrelated, clickbait, slideshow, or matched only the search route but not the original topic.

Note: If the topic is about entertainment media (game, film, book, show),
videos about that media (gameplay, review, lore, analysis) ARE relevant.
If the topic is real-world (history, science, nature), documentaries and
news footage ARE relevant even if in a different language.
Prefer visual relevance to the original topic over generic wording in the search query.

Return ONLY a single integer 0-100. No explanation, no units."""

            response = TextGenerator()._rest_generate_content(
                model=FAST_TEXT_MODEL,
                api_key=api_key,
                prompt_text=prompt,
                generation_config={"temperature": 0.0, "maxOutputTokens": 20},
            )

            text = (
                (response.get("candidates") or [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
            )
            match = re.search(r'\d+', text)
            if match:
                relevance = int(match.group())
                if 0 <= relevance <= 100:
                    with _relevance_cache_lock:
                        _relevance_cache[cache_key] = relevance
                        _save_relevance_cache()
                    return relevance

        except Exception as e:
            error_str = str(e)
            if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
                if key_index < len(api_keys) - 1:
                    continue
            if log_callback:
                log_callback(f"⚠️ Gemini relevance check failed: {error_str[:120]}")

    return -1  # Failed to check


def calculate_smart_video_count(
    target_duration_seconds: float,
    total_clips_needed: int,
    batch_size: int = 1
) -> int:
    """Calculate optimal number of source videos to download.

    Scaling logic (correctly handles 30s to 10h range):
    - 30s video  → 2 sources  (minimal, prefer cache)
    - 60s video  → 3 sources
    - 90s video  → 3-4 sources  (typical short)
    - 5min video → 5-6 sources
    - 30min video → 8-10 sources
    - 10h video  → 30-40 sources (batch)

    Safety buffer: 40% of videos may fail slideshow/motion check.
    """
    d = float(target_duration_seconds or 60)

    if d <= 45:
        base_count = 2
    elif d <= 90:
        base_count = 3
    elif d <= 180:
        base_count = 4
    elif d <= 600:
        # 3–10 min
        base_count = max(4, int(d / 60) + 2)
    elif d <= 3600:
        # 10 min – 1 hour
        base_count = max(6, int(d / 300) + 4)
    else:
        # > 1 hour (long-form / batch)
        base_count = max(10, int(d / 1800) + 8)

    # Safety multiplier: 40% videos may be rejected by motion check
    optimal_count = int(math.ceil(base_count * 1.4))

    # Single-video cap: 10 sources
    optimal_count = min(10, optimal_count)

    # Batch scaling: logarithmic to avoid 600 downloads for 100 shorts
    if batch_size > 1:
        batch_multiplier = 1 + math.log2(batch_size) / 2.5
        optimal_count = int(optimal_count * batch_multiplier)
        optimal_count = min(40, optimal_count)

    return max(2, optimal_count)
