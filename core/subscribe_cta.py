#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helpers for natural spoken subscription requests in generated narration.
"""

from __future__ import annotations

import re


_LANGUAGE_ALIASES = {
    "ru": "Russian",
    "rus": "Russian",
    "russian": "Russian",
    "русский": "Russian",
    "en": "English",
    "eng": "English",
    "english": "English",
    "es": "Spanish",
    "spanish": "Spanish",
    "fr": "French",
    "french": "French",
    "de": "German",
    "german": "German",
    "pt": "Portuguese",
    "portuguese": "Portuguese",
    "it": "Italian",
    "italian": "Italian",
    "uk": "Ukrainian",
    "ukrainian": "Ukrainian",
    "hi": "Hindi",
    "hindi": "Hindi",
    "zh": "Chinese",
    "chinese": "Chinese",
    "ja": "Japanese",
    "japanese": "Japanese",
    "ko": "Korean",
    "korean": "Korean",
    "ar": "Arabic",
    "arabic": "Arabic",
}


_CTA_PHRASES = {
    "Russian": "Если хотите больше таких историй без воды, подпишитесь на канал.",
    "English": "If you want more stories like this, subscribe to the channel.",
    "Spanish": "Si quieres más historias como esta, suscríbete al canal.",
    "French": "Si vous voulez d'autres histoires comme celle-ci, abonnez-vous à la chaîne.",
    "German": "Wenn Sie mehr solche Geschichten möchten, abonnieren Sie den Kanal.",
    "Portuguese": "Se você quer mais histórias como esta, inscreva-se no canal.",
    "Italian": "Se vuoi altre storie così, iscriviti al canale.",
    "Ukrainian": "Якщо хочете більше таких історій, підпишіться на канал.",
    "Hindi": "ऐसी और कहानियों के लिए चैनल को सब्सक्राइब करें.",
    "Chinese": "想看更多这样的故事, 记得订阅频道.",
    "Japanese": "こういう話をもっと見たい方は、チャンネル登録をお願いします.",
    "Korean": "이런 이야기를 더 보고 싶다면 채널을 구독해 주세요.",
    "Arabic": "إذا أردت المزيد من هذه القصص، اشترك في القناة.",
}


_SUBSCRIBE_MARKER_RE = re.compile(
    r"("
    r"subscrib|"
    r"подпис|подпиш|"
    r"suscr[ií]b|"
    r"abonn|"
    r"iscriv|"
    r"inscrev|"
    r"підпис|"
    r"सब्सक्राइब|"
    r"订阅|訂閱|"
    r"チャンネル登録|"
    r"구독|"
    r"اشترك"
    r")",
    re.IGNORECASE,
)

_SENTENCE_RE = re.compile(r"[^.!?。！？]+(?:[.!?。！？]+|$)", re.DOTALL)


def _normalize_language(language: str | None) -> str:
    key = str(language or "").strip().lower()
    return _LANGUAGE_ALIASES.get(key, str(language or "English").strip() or "English")


_CTA_ROLE_PHRASES = {
    "Russian": {
        "soft": "Если такой формат вам полезен, подпишитесь на канал.",
        "mid": "Подписывайтесь, если хотите больше таких разборов.",
        "final": "Если хотите больше таких историй без воды, подпишитесь на канал.",
    },
    "English": {
        "soft": "If this format is useful, subscribe to the channel.",
        "mid": "Subscribe if you want more breakdowns like this.",
        "final": "If you want more stories like this, subscribe to the channel.",
    },
}

_LONG_ROLE_PLACEMENTS = {
    "soft": "after the first useful payoff or clear value moment, never in the opening",
    "mid": "after a natural mini-summary or transition between major ideas",
    "final": "in the final 10-15% as part of the closing thought",
}

_LONG_SLOT_RATIOS = {
    1: [0.90],
    2: [0.38, 0.90],
    3: [0.24, 0.58, 0.90],
    4: [0.20, 0.43, 0.68, 0.90],
    5: [0.16, 0.34, 0.54, 0.74, 0.91],
    6: [0.14, 0.30, 0.46, 0.62, 0.78, 0.92],
}

_LONG_SLOT_ROLES = {
    1: ["final"],
    2: ["soft", "final"],
    3: ["soft", "mid", "final"],
    4: ["soft", "mid", "mid", "final"],
    5: ["soft", "mid", "mid", "mid", "final"],
    6: ["soft", "mid", "mid", "mid", "mid", "final"],
}


def subscribe_cta_phrase(language: str = "Russian", role: str = "final") -> str:
    """Return a short generic spoken subscription phrase for a language."""
    normalized = _normalize_language(language)
    role_phrases = _CTA_ROLE_PHRASES.get(normalized)
    if role_phrases:
        return role_phrases.get(role, role_phrases["final"])
    return _CTA_PHRASES.get(normalized, _CTA_PHRASES["English"])


def has_spoken_subscribe_cta(text: str) -> bool:
    """Detect whether narration already asks for a subscription."""
    return bool(text and _SUBSCRIBE_MARKER_RE.search(text))


def count_spoken_subscribe_ctas(text: str) -> int:
    """Count spoken subscription requests by marker occurrences."""
    return len(_SUBSCRIBE_MARKER_RE.findall(text or ""))


def long_subscribe_cta_count(target_duration: float) -> int:
    """Choose a sane number of subscription prompts for a long video."""
    minutes = max(0.0, float(target_duration or 0) / 60.0)
    if minutes < 12:
        return 1
    if minutes < 35:
        return 2
    if minutes < 75:
        return 3
    if minutes < 150:
        return 4
    if minutes < 240:
        return 5
    return 6


def build_long_subscribe_cta_plan(target_duration: float, num_chapters: int) -> list[dict]:
    """Plan CTA slots by video duration and map them to generated chapters."""
    total = long_subscribe_cta_count(target_duration)
    ratios = _LONG_SLOT_RATIOS[total]
    roles = _LONG_SLOT_ROLES[total]
    max_chapter = max(0, int(num_chapters or 1) - 1)

    slots = []
    for index, (ratio, role) in enumerate(zip(ratios, roles), start=1):
        if max_chapter == 0:
            chapter_index = 0
        else:
            chapter_index = min(max_chapter, max(0, round(ratio * max_chapter)))
        slots.append(
            {
                "slot": index,
                "total": total,
                "ratio": ratio,
                "chapter_index": chapter_index,
                "role": role,
                "placement": _LONG_ROLE_PLACEMENTS[role],
            }
        )
    return slots


def build_long_subscribe_cta_instruction(
    language: str = "Russian",
    chapter_slots: list[dict] | None = None,
    total_slots: int = 1,
) -> str:
    """Build long-form prompt instructions for the planned CTA slots."""
    chapter_slots = list(chapter_slots or [])
    if not chapter_slots:
        return f"""SPOKEN SUBSCRIPTION CTA:
- Do not ask viewers to subscribe in this generated part.
- The long-form CTA plan has {total_slots} total subscription prompt(s), but none belongs here.
"""

    lines = [
        "SPOKEN SUBSCRIPTION CTA:",
        f"- This generated part owns {len(chapter_slots)} of {total_slots} planned subscription prompt(s). Include exactly that many, no more.",
        f"- Keep every subscription prompt calm, short, topic-neutral, and in {language}.",
        '- No begging, no bell reminder, no "subscribe right now", no ad-break feeling.',
        "- Use different wording across the video; do not repeat the exact same CTA sentence.",
    ]
    if len(chapter_slots) > 1:
        lines.append("- If multiple prompts belong here, spread them far apart.")

    for slot in chapter_slots:
        role = str(slot.get("role", "final"))
        ratio = int(round(float(slot.get("ratio", 0.9)) * 100))
        phrase = subscribe_cta_phrase(language, role)
        lines.append(
            f'- Slot {slot.get("slot", "?")}/{slot.get("total", total_slots)}: around {ratio}% of the full video, '
            f'{slot.get("placement", _LONG_ROLE_PLACEMENTS.get(role, _LONG_ROLE_PLACEMENTS["final"]))}. '
            f'Example tone only: "{phrase}"'
        )

    return "\n".join(lines) + "\n"


def build_subscribe_cta_instruction(
    language: str = "Russian",
    video_kind: str = "short",
    final_chapter: bool = True,
) -> str:
    """Build prompt instructions for an organic spoken subscription CTA."""
    phrase = subscribe_cta_phrase(language)

    if video_kind == "long" and not final_chapter:
        return build_long_subscribe_cta_instruction(language, [], total_slots=1)

    if video_kind == "long":
        slot = {
            "slot": 1,
            "total": 1,
            "ratio": 0.9,
            "role": "final",
            "placement": _LONG_ROLE_PLACEMENTS["final"],
        }
        return build_long_subscribe_cta_instruction(language, [slot], total_slots=1)

    return f"""SPOKEN SUBSCRIPTION CTA:
- Include exactly one brief spoken subscription prompt inside full_text.
- Put it after the hook and useful context have started, never as the first sentence.
- Make it fit the topic and tone; no begging, no bell reminder, no "subscribe right now".
- Keep it short and in {language}.
- Example tone only, adapt naturally if needed: "{phrase}"
"""


def _sentence_chunks(text: str) -> list[str]:
    chunks = [match.group(0).strip() for match in _SENTENCE_RE.finditer(text or "")]
    return [chunk for chunk in chunks if chunk]


def _sentence_ratio(index: int, total: int) -> float:
    if total <= 1:
        return 1.0
    return index / float(total - 1)


def _find_matching_cta_sentence(
    chunks: list[str],
    target_ratio: float,
    used_indexes: set[int],
    tolerance: float,
) -> int | None:
    best_index = None
    best_distance = None
    total = len(chunks)
    for index, chunk in enumerate(chunks):
        if index in used_indexes or not has_spoken_subscribe_cta(chunk):
            continue
        distance = abs(_sentence_ratio(index, total) - target_ratio)
        if distance <= tolerance and (best_distance is None or distance < best_distance):
            best_index = index
            best_distance = distance
    return best_index


def _compose_with_cta(text: str, phrase: str, video_kind: str) -> str:
    text = (text or "").strip()
    phrase = phrase.strip()
    if not text:
        return phrase

    if video_kind == "long":
        return f"{text}\n\n{phrase}"

    chunks = _sentence_chunks(text)
    if len(chunks) >= 3:
        insert_index = max(1, len(chunks) - 1)
        chunks.insert(insert_index, phrase)
        return " ".join(chunks)

    return f"{text.rstrip()} {phrase}"


def _trim_for_cta(text: str, phrase: str, max_chars: int | None) -> str:
    if not max_chars:
        return text

    available = max_chars - len(phrase) - 1
    if available < 40:
        return text

    if len(text) <= available:
        return text

    trimmed = text[:available].rstrip()
    last_sentence_end = max(
        trimmed.rfind("."),
        trimmed.rfind("!"),
        trimmed.rfind("?"),
        trimmed.rfind("。"),
        trimmed.rfind("！"),
        trimmed.rfind("？"),
    )
    if last_sentence_end > available * 0.5:
        return trimmed[: last_sentence_end + 1].strip()

    last_space = trimmed.rfind(" ")
    if last_space > 30:
        return trimmed[:last_space].strip()
    return trimmed.strip()


def ensure_spoken_subscribe_cta(
    text: str,
    language: str = "Russian",
    video_kind: str = "short",
    max_chars: int | None = None,
) -> str:
    """
    Ensure narration contains one spoken subscription request.

    This is a fallback for model misses. Prompt instructions should usually make
    the CTA more contextual than the generic phrase used here.
    """
    clean_text = (text or "").strip()
    if not clean_text or has_spoken_subscribe_cta(clean_text):
        return clean_text

    phrase = subscribe_cta_phrase(language)
    trimmed_text = _trim_for_cta(clean_text, phrase, max_chars)
    updated = _compose_with_cta(trimmed_text, phrase, video_kind)

    if max_chars and len(updated) > max_chars:
        return updated[:max_chars].rstrip()
    return updated


def ensure_long_spoken_subscribe_ctas(
    text: str,
    language: str = "Russian",
    target_duration: float = 0,
) -> str:
    """Ensure a long narration has the duration-scaled number of CTA slots."""
    clean_text = (text or "").strip()
    if not clean_text:
        return clean_text

    plan = build_long_subscribe_cta_plan(target_duration, num_chapters=1)
    chunks = _sentence_chunks(clean_text)
    if not chunks:
        return clean_text

    used_indexes: set[int] = set()
    tolerance = 0.16 if len(plan) <= 3 else 0.11

    for slot in plan:
        target_ratio = float(slot.get("ratio", 0.9))
        existing_index = _find_matching_cta_sentence(
            chunks,
            target_ratio,
            used_indexes,
            tolerance,
        )
        if existing_index is not None:
            used_indexes.add(existing_index)
            continue

        role = str(slot.get("role", "final"))
        phrase = subscribe_cta_phrase(language, role)
        if role == "final":
            insert_index = len(chunks)
        else:
            insert_index = min(len(chunks), max(1, round(target_ratio * len(chunks))))

        chunks.insert(insert_index, phrase)
        used_indexes = {index + 1 if index >= insert_index else index for index in used_indexes}
        used_indexes.add(insert_index)

    return " ".join(chunks).strip()
