#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Universal opening-hook helpers for Shorts/Reels/TikTok style videos.

The goal is not to hard-code a niche, but to turn any topic/title/script into
a compact first-screen hook plus several labeled variants for later analytics.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


MAX_FIRST_SHOT_CHARS = 64
MAX_HOOK_CHARS = 88

_NOISE_PREFIX_RE = re.compile(
    r"^\s*(?:вариант\s*)?(?:\d+[.)]|[-–—•*]+)\s*",
    flags=re.IGNORECASE,
)
_HASHTAG_RE = re.compile(r"(?<!\w)#[^\s#]+", flags=re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_STALE_CURIOSITY_RE = re.compile(
    r"\b(?:secret|hidden|shocking|shock|unbelievable|nobody knew|they hid|"
    r"секрет\w*|скрыва\w*|скрыли|шок\w*|тайн\w*|невероятн\w*)\b",
    flags=re.IGNORECASE | re.UNICODE,
)
_ACTION_STAKES_RE = re.compile(
    r"\b(?:chose|choose|refused|risked|saved|lost|survived|decided|changed|cost|"
    r"выбра\w*|отказа\w*|предпоч[её]л\w*|решил\w*|решилась|спас\w*|"
    r"потеря\w*|выжил\w*|измени\w*|стоил\w*)\b",
    flags=re.IGNORECASE | re.UNICODE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")

_LANGUAGE_ALIASES = {
    "russian": "Russian",
    "русский": "Russian",
    "english": "English",
    "spanish": "Spanish",
    "french": "French",
    "german": "German",
    "chinese": "Chinese",
    "japanese": "Japanese",
    "korean": "Korean",
    "portuguese": "Portuguese",
    "italian": "Italian",
    "hindi": "Hindi",
    "arabic": "Arabic",
}

_FALLBACK_SUBJECTS = {
    "Russian": "этот момент",
    "English": "this moment",
    "Spanish": "este momento",
    "French": "ce moment",
    "German": "dieser Moment",
    "Chinese": "这一刻",
    "Japanese": "この瞬間",
    "Korean": "이 순간",
    "Portuguese": "este momento",
    "Italian": "questo momento",
    "Hindi": "यह पल",
    "Arabic": "هذه اللحظة",
}

_HOOK_TEMPLATES = {
    "Russian": (
        ("consequence", "{subject} меняет всё уже в первый момент."),
        ("contrast", "{subject} выглядит обычно — пока не происходит это."),
        ("stakes", "{subject}: одна деталь решает весь исход."),
        ("direct", "Смотрите, что происходит с {subject}."),
    ),
    "English": (
        ("consequence", "{subject} changes everything in the first moment."),
        ("contrast", "{subject} looks ordinary—until this happens."),
        ("stakes", "{subject}: one detail decides the outcome."),
        ("direct", "Watch what happens to {subject}."),
    ),
    "Spanish": (
        ("consequence", "{subject} lo cambia todo desde el primer instante."),
        ("contrast", "{subject} parece normal hasta que ocurre esto."),
        ("stakes", "{subject}: un detalle decide el resultado."),
        ("direct", "Mira lo que ocurre con {subject}."),
    ),
    "French": (
        ("consequence", "{subject} change tout dès le premier instant."),
        ("contrast", "{subject} paraît ordinaire jusqu'à ce que tout bascule."),
        ("stakes", "{subject} : un détail décide de l'issue."),
        ("direct", "Regardez ce qui arrive à {subject}."),
    ),
    "German": (
        ("consequence", "{subject} verändert schon im ersten Moment alles."),
        ("contrast", "{subject} wirkt gewöhnlich, bis das hier passiert."),
        ("stakes", "{subject}: Ein Detail entscheidet den Ausgang."),
        ("direct", "Sieh, was mit {subject} passiert."),
    ),
    "Chinese": (
        ("consequence", "{subject}从第一秒就改变了一切。"),
        ("contrast", "{subject}看似普通，直到这一幕发生。"),
        ("stakes", "{subject}：一个细节决定结局。"),
        ("direct", "看看{subject}接下来发生了什么。"),
    ),
    "Japanese": (
        ("consequence", "{subject}が最初の一秒ですべてを変える。"),
        ("contrast", "{subject}は普通に見える。だが次の瞬間、状況が変わる。"),
        ("stakes", "{subject}。たった一つの違いが結末を決める。"),
        ("direct", "{subject}に何が起きるか見てほしい。"),
    ),
    "Korean": (
        ("consequence", "{subject}이 첫 순간부터 모든 걸 바꾼다."),
        ("contrast", "{subject}은 평범해 보이지만 곧 상황이 뒤집힌다."),
        ("stakes", "{subject}, 단 하나의 차이가 결말을 결정한다."),
        ("direct", "{subject}에 무슨 일이 생기는지 보자."),
    ),
    "Portuguese": (
        ("consequence", "{subject} muda tudo logo no primeiro instante."),
        ("contrast", "{subject} parece comum até isto acontecer."),
        ("stakes", "{subject}: um detalhe decide o resultado."),
        ("direct", "Veja o que acontece com {subject}."),
    ),
    "Italian": (
        ("consequence", "{subject} cambia tutto fin dal primo istante."),
        ("contrast", "{subject} sembra normale finché non accade questo."),
        ("stakes", "{subject}: un dettaglio decide il risultato."),
        ("direct", "Guarda cosa succede a {subject}."),
    ),
    "Hindi": (
        ("consequence", "{subject} पहले ही पल में सब कुछ बदल देता है।"),
        ("contrast", "{subject} सामान्य लगता है, फिर अचानक सब बदल जाता है।"),
        ("stakes", "{subject}: एक छोटी बात नतीजा तय करती है।"),
        ("direct", "देखिए {subject} के साथ आगे क्या होता है।"),
    ),
    "Arabic": (
        ("consequence", "{subject} يغيّر كل شيء منذ اللحظة الأولى."),
        ("contrast", "يبدو {subject} عادياً حتى يحدث هذا."),
        ("stakes", "{subject}: تفصيل واحد يحسم النتيجة."),
        ("direct", "شاهد ما يحدث لـ {subject}."),
    ),
}

_GENERIC_OPENING_PREFIXES = {
    "Russian": (
        "вы когда-нибудь", "задумывались ли", "когда-нибудь задумывались",
        "в этом видео", "в этом ролике", "сегодня мы", "сейчас мы",
        "добро пожаловать", "давайте разбер", "многие не знают",
    ),
    "English": (
        "have you ever", "did you ever wonder", "in this video", "today we",
        "welcome", "let's find out", "let us find out", "many people don't know",
    ),
    "Spanish": ("alguna vez", "en este video", "hoy vamos", "bienvenid", "vamos a descubrir"),
    "French": ("avez-vous déjà", "dans cette vidéo", "aujourd'hui nous", "bienvenue", "découvrons"),
    "German": ("hast du dich jemals", "in diesem video", "heute werden wir", "willkommen", "finden wir heraus"),
    "Chinese": ("你有没有想过", "在这个视频中", "今天我们", "欢迎", "让我们来看看"),
    "Japanese": ("考えたことがありますか", "この動画では", "今日は", "ようこそ", "見てみましょう"),
    "Korean": ("생각해 본 적", "이 영상에서는", "오늘은", "환영합니다", "알아보겠습니다"),
    "Portuguese": ("você já se perguntou", "neste vídeo", "hoje vamos", "bem-vindo", "vamos descobrir"),
    "Italian": ("ti sei mai chiesto", "in questo video", "oggi vedremo", "benvenut", "scopriamo"),
    "Hindi": ("क्या आपने कभी", "इस वीडियो में", "आज हम", "स्वागत", "आइए जानते"),
    "Arabic": ("هل تساءلت يوماً", "في هذا الفيديو", "اليوم سوف", "مرحباً", "دعونا نكتشف"),
}

_RU_WEAK_PREFIXES = (
    "интересные факты:",
    "интересные факты про",
    "всё о",
    "все о",
    "почему",
    "как",
)
_EN_WEAK_PREFIXES = (
    "interesting facts:",
    "interesting facts about",
    "everything about",
    "all about",
)

_RU_STOPWORDS = {
    "это",
    "этот",
    "эта",
    "эти",
    "видео",
    "ролик",
    "история",
    "факт",
    "факты",
    "почему",
    "как",
    "что",
}
_EN_STOPWORDS = {
    "this",
    "that",
    "these",
    "video",
    "short",
    "story",
    "fact",
    "facts",
    "why",
    "how",
    "what",
}


def _language_key(language: str) -> str:
    value = str(language or "English").strip().casefold()
    for alias, canonical in _LANGUAGE_ALIASES.items():
        if value.startswith(alias):
            return canonical
    return "English"


def _is_russian(language: str) -> bool:
    return _language_key(language) == "Russian"


def is_generic_opening(value: Any, language: str = "Russian") -> bool:
    """Detect feed-killing introductions that delay the concrete subject."""
    text = _clean_text(value).casefold()
    if not text:
        return False
    prefixes = _GENERIC_OPENING_PREFIXES.get(
        _language_key(language),
        _GENERIC_OPENING_PREFIXES["English"],
    )
    return any(text.startswith(prefix) for prefix in prefixes)


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    text = _NOISE_PREFIX_RE.sub("", text)
    text = _HASHTAG_RE.sub("", text)
    text = text.strip(" \t\r\n\"'«»“”„`")
    text = re.sub(r"\s+([,.:;!?…])", r"\1", text)
    text = re.sub(r"([,.:;!?…])(?=\S)", r"\1 ", text)
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


def _strip_terminal(value: str) -> str:
    return _clean_text(value).rstrip(" .,!?:;…").strip()


def _remove_weak_prefixes(value: str, language: str) -> str:
    text = _strip_terminal(value)
    lowered = text.casefold()
    prefixes = _RU_WEAK_PREFIXES if _is_russian(language) else _EN_WEAK_PREFIXES
    for prefix in prefixes:
        if lowered.startswith(prefix):
            text = text[len(prefix):].strip(" :—-")
            break
    return _strip_terminal(text)


def _first_sentence(full_text: str) -> str:
    text = _clean_text(full_text)
    if not text:
        return ""
    sentence = _SENTENCE_RE.split(text, maxsplit=1)[0]
    return _strip_terminal(sentence)


def _shorten_words(value: str, max_words: int, max_chars: int) -> str:
    text = _strip_terminal(value)
    if len(text) <= max_chars and len(text.split()) <= max_words:
        return text

    words = text.split()
    shortened = " ".join(words[:max_words]).strip()
    if len(shortened) > max_chars:
        shortened = shortened[:max_chars].rsplit(" ", 1)[0].strip()
    return _strip_terminal(shortened) or _strip_terminal(text[:max_chars])


def _meaningful_words(value: str, language: str) -> List[str]:
    stopwords = _RU_STOPWORDS if _is_russian(language) else _EN_STOPWORDS
    words = [
        word
        for word in re.findall(r"[^\W_]{2,}", value.casefold(), flags=re.UNICODE)
        if word not in stopwords
    ]
    if words:
        return words
    compact = re.sub(r"[\W_]+", "", value, flags=re.UNICODE)
    return [compact] if compact else []


def extract_hook_subject(
    title: str = "",
    theme: str = "",
    full_text: str = "",
    language: str = "Russian",
) -> str:
    """Extract a short, topic-neutral subject for hook templates."""
    candidates = [
        _remove_weak_prefixes(title, language),
        _remove_weak_prefixes(theme, language),
        _first_sentence(full_text),
    ]
    for candidate in candidates:
        if is_generic_opening(candidate, language):
            continue
        candidate = _shorten_words(candidate, max_words=7, max_chars=58)
        if len(_meaningful_words(candidate, language)) >= 1:
            return candidate
    return _FALLBACK_SUBJECTS.get(_language_key(language), "this moment")


def _template_candidates(subject: str, language: str) -> List[Dict[str, str]]:
    templates = _HOOK_TEMPLATES.get(_language_key(language), _HOOK_TEMPLATES["English"])
    return [
        {"family": family, "text": template.format(subject=subject)}
        for family, template in templates
    ]


def _score_hook(text: str, family: str, subject: str, language: str) -> int:
    clean = _clean_text(text)
    words = clean.split()
    score = 0
    if 4 <= len(words) <= 10:
        score += 18
    if 24 <= len(clean) <= MAX_FIRST_SHOT_CHARS:
        score += 18
    if clean.endswith("?"):
        score += 10
    if family in {"question", "contrast", "detail"}:
        score += 8
    if family == "title_stakes":
        score += 16
    if _ACTION_STAKES_RE.search(clean):
        score += 12
    if _STALE_CURIOSITY_RE.search(clean):
        score -= 10
    subject_words = set(_meaningful_words(subject, language))
    hook_words = set(_meaningful_words(clean, language))
    if subject_words and subject_words & hook_words:
        score += 8
    if len(clean) > MAX_HOOK_CHARS:
        score -= 20
    return score


def _dedupe_hooks(candidates: Iterable[Dict[str, str]], language: str) -> List[Dict[str, Any]]:
    seen = set()
    result: List[Dict[str, Any]] = []
    for candidate in candidates:
        text = _clean_text(candidate.get("text", ""))
        if not text or is_generic_opening(text, language):
            continue
        key = re.sub(r"\W+", "", text.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "family": str(candidate.get("family") or "direct"),
                "text": text,
            }
        )
    return result


def replace_generic_script_opening(
    full_text: str,
    replacement: str,
    language: str = "Russian",
) -> str:
    """Replace only a generic first sentence, preserving the rest verbatim."""
    text = str(full_text or "").strip()
    replacement = _clean_text(replacement)
    if not text or not replacement:
        return text

    match = re.match(r"^\s*(.*?[.!?…。！？])(?:\s+|$)", text, flags=re.DOTALL)
    first_sentence = match.group(1).strip() if match else text
    if not is_generic_opening(first_sentence, language):
        return text

    if replacement[-1] not in ".!?…。！？":
        replacement += "."
    rest = text[match.end():].lstrip() if match else ""
    return f"{replacement} {rest}".strip()


def build_opening_hook_package(
    title: str = "",
    theme: str = "",
    full_text: str = "",
    language: str = "Russian",
    existing_hooks: Iterable[Any] | None = None,
    variation_index: int | None = None,
) -> Dict[str, Any]:
    """Build a publishable first-screen hook package for any content niche."""
    subject = extract_hook_subject(title=title, theme=theme, full_text=full_text, language=language)
    candidates: List[Dict[str, str]] = []

    title_hook = _remove_weak_prefixes(title, language)
    if title_hook and _ACTION_STAKES_RE.search(title_hook):
        candidates.append(
            {
                "family": "title_stakes",
                "text": _shorten_words(title_hook, max_words=9, max_chars=MAX_HOOK_CHARS),
            }
        )

    if existing_hooks:
        for item in existing_hooks:
            if isinstance(item, dict):
                candidates.append(
                    {
                        "family": str(item.get("family") or item.get("type") or "ai"),
                        "text": str(item.get("text") or ""),
                    }
                )
            else:
                candidates.append({"family": "ai", "text": str(item)})

    first_sentence = _first_sentence(full_text)
    if first_sentence:
        candidates.append(
            {
                "family": "script_open",
                "text": _shorten_words(first_sentence, max_words=9, max_chars=MAX_HOOK_CHARS),
            }
        )

    candidates.extend(_template_candidates(subject, language))
    hooks = _dedupe_hooks(candidates, language)
    for hook in hooks:
        hook["score"] = _score_hook(hook["text"], hook["family"], subject, language)

    hooks.sort(key=lambda item: item["score"], reverse=True)
    hooks = hooks[:5]
    primary = hooks[0] if hooks else {"family": "direct", "text": subject, "score": 0}
    if hooks and variation_index is not None and primary.get("family") != "title_stakes":
        # Reusing the same highest-scoring template across a 15-video batch is
        # still recognisably templated. Rotate only among near-equal candidates
        # so creative variety never requires accepting a weak or generic hook.
        best_score = int(primary.get("score", 0))
        eligible = [
            item for item in hooks
            if int(item.get("score", 0)) >= best_score - 10
        ]
        if eligible:
            primary = eligible[max(0, int(variation_index) - 1) % len(eligible)]
    first_shot_title = _shorten_words(primary["text"], max_words=9, max_chars=MAX_FIRST_SHOT_CHARS)

    return {
        "subject": subject,
        "primary_hook": primary["text"],
        "first_shot_title": first_shot_title,
        "primary_family": primary["family"],
        "opening_hooks": hooks,
    }
