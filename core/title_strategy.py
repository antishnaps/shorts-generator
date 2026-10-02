#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Universal title relevance and diversity helpers.

The module intentionally contains no topic/category dictionaries.  It compares
the concrete subject, the parent topic and previous titles lexically, leaving
semantic phrasing to the configured text model.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Iterable, List, Sequence, Tuple


_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+", flags=re.UNICODE)
_SEPARATOR_RE = re.compile(r"[:|/;]|\s[-–—]\s")
_QUESTION_START_RE = re.compile(
    r"^(?:как|почему|зачем|кто|что|где|когда|какой|какая|какие|"
    r"how|why|who|what|where|when|which)\b",
    flags=re.IGNORECASE | re.UNICODE,
)
_RU_VERB_RE = re.compile(
    r"(?:лся|лась|лось|лись|ешь|ете|ем|ет|ёт|ут|ют|ит|ат|ят|ил|ила|или|"
    r"ала|али|ел|ела|ели|ать|ять|ить|еть|уть|ться|ется|ются)$",
    flags=re.IGNORECASE | re.UNICODE,
)
_EN_VERB_RE = re.compile(r"(?:ed|ing|ize|ise|ates?|ifies|s)$", flags=re.IGNORECASE)

# Function words are linguistic plumbing, not subject-matter rules.
_STOPWORDS = {
    "а", "без", "бы", "был", "была", "были", "было", "в", "во", "вот",
    "для", "до", "его", "ее", "её", "за", "и", "из", "или", "их", "к",
    "как", "ко", "когда", "кто", "ли", "на", "над", "не", "но", "о", "об",
    "от", "по", "под", "почему", "при", "про", "с", "со", "так", "что",
    "это", "этот", "эта", "эти", "the", "a", "an", "and", "or", "of", "to",
    "in", "on", "for", "from", "with", "without", "by", "at", "is", "are",
    "was", "were", "be", "been", "being", "this", "that", "these", "those",
    "how", "why", "who", "what", "where", "when", "which", "did", "does",
}


def _stem(word: str) -> str:
    word = str(word or "").casefold()
    if len(word) <= 4:
        return word
    if re.search(r"[а-яё]", word, flags=re.IGNORECASE):
        # A short prefix is deliberately used instead of a language/domain
        # dictionary: it catches common Russian inflections across any topic.
        return word[: min(6, len(word) - 1)]
    for suffix in ("ingly", "edly", "ation", "ment", "ness", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def meaningful_stems(text: str) -> Tuple[str, ...]:
    """Return stable content-word stems in their original order."""
    result: List[str] = []
    for raw in _WORD_RE.findall(str(text or "")):
        folded = raw.casefold()
        if len(folded) < 3 or folded in _STOPWORDS:
            continue
        stem = _stem(folded)
        if stem and stem not in result:
            result.append(stem)
    return tuple(result)


def topic_evidence_ratio(title: str, topic: str) -> float:
    """Measure lexical evidence for a topic with light inflection tolerance."""
    topic_terms = set(meaningful_stems(topic))
    if not topic_terms:
        return 1.0
    title_terms = set(meaningful_stems(title))
    return len(title_terms & topic_terms) / len(topic_terms)


def specific_topic_terms(subject_topic: str, parent_theme: str = "") -> Tuple[str, ...]:
    """Return terms that distinguish this video's subject from its series."""
    subject_terms = list(meaningful_stems(subject_topic))
    parent_terms = set(meaningful_stems(parent_theme))
    distinct = [term for term in subject_terms if term not in parent_terms]
    return tuple(distinct or subject_terms)


def title_has_specific_evidence(title: str, subject_topic: str, parent_theme: str = "") -> bool:
    required = set(specific_topic_terms(subject_topic, parent_theme))
    if not required:
        return True
    return bool(required & set(meaningful_stems(title)))


def looks_catalog_like(title: str) -> bool:
    """Detect keyword-stack labels instead of a natural proposition."""
    clean = str(title or "").strip()
    if not clean:
        return True
    words = _WORD_RE.findall(clean)
    if len(words) < 2:
        return True
    separators = len(_SEPARATOR_RE.findall(clean))
    if separators >= 2 or clean.count(",") >= 2:
        return True
    if separators == 0:
        return False
    if _QUESTION_START_RE.search(clean):
        return False
    lower_words = [word.casefold() for word in words]
    has_predicate = any(
        _RU_VERB_RE.search(word)
        or _EN_VERB_RE.search(word)
        or word in {"стал", "стала", "стали", "сталo", "became", "made", "changed", "saved", "lost"}
        for word in lower_words
    )
    return not has_predicate


def _repetition_payload(title: str, anchors: Sequence[str]) -> Tuple[str, ...]:
    # Remove only the concrete subject.  Keep the parent theme: repeating the
    # same parent-theme phrase plus the same predicate is exactly the batch
    # sameness we want to detect, while a different predicate remains distinct.
    anchor_terms = set()
    for anchor in tuple(anchors)[:1]:
        anchor_terms.update(meaningful_stems(anchor))
    return tuple(term for term in meaningful_stems(title) if term not in anchor_terms)


def repetition_similarity(title: str, other: str, anchors: Sequence[str] = ()) -> float:
    """Compare reusable wording after removing the current topic anchors."""
    left = _repetition_payload(title, anchors)
    right = _repetition_payload(other, anchors)
    if not left or not right:
        return 0.0

    left_set, right_set = set(left), set(right)
    shared = len(left_set & right_set)
    containment = shared / max(1, min(len(left_set), len(right_set)))
    sequence = SequenceMatcher(None, " ".join(left), " ".join(right)).ratio()
    if shared < 2:
        containment *= 0.5
    return max(containment, sequence)


def is_near_duplicate_title(
    title: str,
    previous_titles: Iterable[str],
    anchors: Sequence[str] = (),
    threshold: float = 0.72,
) -> bool:
    key = " ".join(meaningful_stems(title))
    for previous in previous_titles or ():
        if key and key == " ".join(meaningful_stems(previous)):
            return True
        if repetition_similarity(title, previous, anchors) >= threshold:
            return True
    return False


def title_candidate_score(
    title: str,
    subject_topic: str,
    parent_theme: str = "",
    previous_titles: Iterable[str] = (),
) -> int:
    """Score subject specificity, natural phrasing and batch diversity."""
    clean = str(title or "").strip()
    if not clean:
        return -1000

    words = _WORD_RE.findall(clean)
    score = 0
    if 4 <= len(words) <= 13:
        score += 18
    elif 2 <= len(words) <= 16:
        score += 6
    else:
        score -= 12

    if 28 <= len(clean) <= 96:
        score += 12
    if title_has_specific_evidence(clean, subject_topic, parent_theme):
        score += 45
    elif specific_topic_terms(subject_topic, parent_theme):
        score -= 55

    if parent_theme and meaningful_stems(parent_theme) != meaningful_stems(subject_topic):
        parent_ratio = topic_evidence_ratio(clean, parent_theme)
        score += min(18, round(parent_ratio * 24))

    if _QUESTION_START_RE.search(clean) or any(
        _RU_VERB_RE.search(word.casefold()) or _EN_VERB_RE.search(word.casefold())
        for word in words
    ):
        score += 12
    if looks_catalog_like(clean):
        score -= 38

    anchors = (subject_topic, parent_theme)
    similarities = [
        repetition_similarity(clean, previous, anchors)
        for previous in (previous_titles or ())
        if str(previous or "").strip()
    ]
    if similarities:
        closest = max(similarities)
        if closest >= 0.72:
            score -= 70
        elif closest >= 0.55:
            score -= 24
    return score


def rank_title_candidates(
    candidates: Iterable[str],
    subject_topic: str,
    parent_theme: str = "",
    previous_titles: Iterable[str] = (),
) -> List[str]:
    """Return unique candidates ordered by relevance and non-repetition."""
    seen = set()
    unique: List[str] = []
    for candidate in candidates or ():
        clean = re.sub(r"\s+", " ", str(candidate or "")).strip()
        key = " ".join(meaningful_stems(clean))
        if clean and key and key not in seen:
            seen.add(key)
            unique.append(clean)
    return sorted(
        unique,
        key=lambda item: title_candidate_score(
            item,
            subject_topic=subject_topic,
            parent_theme=parent_theme,
            previous_titles=previous_titles,
        ),
        reverse=True,
    )


def subject_first_prompt_rules(language: str = "Russian") -> str:
    """Reusable model instructions, independent of any content niche."""
    return f"""TITLE ARCHITECTURE ({language}):
- Identify the most concrete person, group, event, object, place, work, mechanism, or decision that the script is actually about.
- Make that concrete subject the semantic lead. The broad series theme is context, not a replacement for the subject.
- Express one true proposition from the script: an action, change, conflict, consequence, choice, or question.
- Weave the broad theme into the sentence only in a natural grammatical role. Never bolt it on as an SEO suffix.
- Never output a catalog label, keyword chain, taxonomy, or a colon-separated pair of noun phrases.
- Do not reuse the same opening, predicate, promise, or sentence skeleton with only the subject swapped.
- Do not invent names, facts, numbers, motives, or outcomes absent from the supplied material."""
