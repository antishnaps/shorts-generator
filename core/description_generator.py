#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video Description Generator
Generates YouTube/social media descriptions with:
- Engaging description text
- Timecodes
- Auto-hashtags (релевантные хэштеги)
- A/B тестирование заголовков (несколько вариантов)
"""

import json
import re
import requests
import time
import unicodedata
from pathlib import Path
from typing import Callable, Optional, Dict, List


# Импортируем дефолтный логгер из централизованного модуля
from core.logging_utils import get_default_logger
from core.gemini_models import DESCRIPTION_MODEL_CHAIN, FAST_TEXT_MODEL, PRO_TEXT_MODEL
from core.opening_hooks import build_opening_hook_package
from core.title_strategy import (
    rank_title_candidates,
    subject_first_prompt_rules,
    title_candidate_score,
    title_has_specific_evidence,
    topic_evidence_ratio,
)

# Алиас для обратной совместимости
_dummy_log = get_default_logger()

# ⏳ Кулдаун моделей: если модель вернула 503/429/timeout — не трогаем её 25 минут
_model_cooldowns: dict = {}  # model_name -> unix timestamp до которого модель на паузе
MODEL_COOLDOWN_SECONDS = 25 * 60  # 25 минут


# 🏷️ КАТЕГОРИИ ХЭШТЕГОВ ПО ТЕМАМ
HASHTAG_CATEGORIES = {
    'gaming': ['#gaming', '#gamer', '#videogames', '#gameplay', '#игры', '#геймер', '#летсплей'],
    'anime': ['#anime', '#аниме', '#manga', '#otaku', '#animeedit', '#анимеэдит'],
    'movies': ['#movies', '#film', '#cinema', '#кино', '#фильм', '#moviereview', '#обзор'],
    'cartoons': ['#cartoon', '#animation', '#мультфильм', '#мультик', '#анимация', '#toons'],
    'facts': ['#facts', '#didyouknow', '#факты', '#интересно', '#познавательно', '#education'],
    'humor': ['#funny', '#humor', '#comedy', '#смешно', '#юмор', '#мемы', '#memes'],
    'tech': ['#tech', '#technology', '#gadgets', '#технологии', '#гаджеты', '#обзортехники'],
    'science': ['#science', '#наука', '#space', '#космос', '#physics', '#biology'],
    'history': ['#history', '#история', '#historical', '#исторические', '#факты'],
    'sports': ['#sports', '#спорт', '#football', '#футбол', '#basketball', '#fitness'],
    'music': ['#music', '#музыка', '#song', '#песня', '#musicvideo', '#клип'],
    'food': ['#food', '#еда', '#cooking', '#готовка', '#recipe', '#рецепт'],
    'travel': ['#travel', '#путешествия', '#tourism', '#туризм', '#adventure'],
    'lifestyle': ['#lifestyle', '#лайфстайл', '#motivation', '#мотивация', '#life'],
}

# 🔥 ВИРУСНЫЕ ХЭШТЕГИ (всегда добавляем)
VIRAL_HASHTAGS_BY_LANGUAGE = {
    'Russian': ['#Shorts', '#вирусное', '#тренды', '#рекомендации', '#длявас'],
    'English': ['#Shorts', '#viral', '#trending', '#recommended', '#fyp'],
    'Spanish': ['#Shorts', '#viral', '#tendencias', '#recomendado', '#parati'],
    'French': ['#Shorts', '#viral', '#tendances', '#recommandé', '#pourtoi'],
    'German': ['#Shorts', '#viral', '#trending', '#empfohlen', '#fürdich'],
    'Chinese': ['#Shorts', '#短视频', '#热门', '#推荐', '#趋势'],
    'Japanese': ['#Shorts', '#ショート', '#おすすめ', '#トレンド', '#話題'],
    'Korean': ['#Shorts', '#쇼츠', '#추천', '#인기영상', '#트렌드'],
    'Portuguese': ['#Shorts', '#viral', '#tendências', '#recomendado', '#paravocê'],
    'Italian': ['#Shorts', '#virale', '#tendenze', '#consigliato', '#perte'],
    'Hindi': ['#Shorts', '#शॉर्ट्स', '#वायरल', '#ट्रेंडिंग', '#सुझाव'],
    'Arabic': ['#Shorts', '#فيديو_قصير', '#رائج', '#ترند', '#مقترح'],
}

TITLE_MAX_LENGTH = 100
TITLE_TARGET_MAX_LENGTH = 86
SHORT_DESCRIPTION_MAX_WORDS = 80

_TITLE_DUPLICATE_SUFFIX_RE = re.compile(
    r'(?:[_-]\d+|\s+\(\d+\)|\s+copy)$',
    flags=re.IGNORECASE,
)
_TITLE_STALE_CURIOSITY_RE = re.compile(
    r'\b(?:secret|hidden|shocking|shock|unbelievable|nobody knew|they hid|'
    r'секрет\w*|скрыва\w*|скрыли|шок\w*|тайн\w*|невероятн\w*)\b',
    flags=re.IGNORECASE | re.UNICODE,
)
_TITLE_ACTION_STAKES_RE = re.compile(
    r'\b(?:chose|choose|refused|risked|saved|lost|survived|decided|changed|cost|'
    r'выбра\w*|отказа\w*|предпоч[её]л\w*|решил\w*|решилась|спас\w*|'
    r'потеря\w*|выжил\w*|измени\w*|стоил\w*)\b',
    flags=re.IGNORECASE | re.UNICODE,
)
_TITLE_ALL_CAPS_WORD_RE = re.compile(r'\b[A-ZА-ЯЁ]{5,}\b', flags=re.UNICODE)

_TITLE_TERMINAL_RE = re.compile(r'[.!?…。！？؟]$')
_TITLE_NOISE_PREFIX_RE = re.compile(
    r'^\s*(?:вариант\s*)?(?:\d+[.)]|[1-9]️⃣|[-–—•*]+)\s*',
    flags=re.IGNORECASE,
)
_RU_QUESTION_STARTERS = (
    'как ', 'почему ', 'зачем ', 'кто ', 'что ', 'где ', 'когда ',
    'куда ', 'откуда ', 'правда ли ', 'неужели ', 'разве ', 'кем ',
    'какой ', 'какая ', 'какие ', 'смог бы ', 'смогла ли ', 'смогли ли ',
)
_EN_QUESTION_STARTERS = (
    'how ', 'why ', 'who ', 'what ', 'where ', 'when ', 'which ', 'did ',
    'does ', 'do ', 'is ', 'are ', 'can ', 'could ', 'would ', 'should ',
)
_TITLE_WEAK_ENDINGS = {
    'и', 'а', 'но', 'или', 'что', 'как', 'кто', 'где', 'когда', 'почему',
    'зачем', 'в', 'во', 'на', 'с', 'со', 'к', 'ко', 'по', 'за', 'из', 'от',
    'до', 'для', 'при', 'про', 'через', 'без', 'над', 'под', 'между',
    'целую', 'целый', 'целая', 'целые', 'одну', 'один', 'одного', 'самый',
    'самая', 'самое', 'самые', 'этот', 'эта', 'это', 'эти',
}
_TITLE_EXCLAMATION_MARKERS = (
    'скрывал', 'скрывали', 'шок', 'тайна', 'секрет', 'невозможн',
    'последн', 'ужас', 'легендарн', 'смертельн', 'правда',
)

_FALLBACK_LABELS = {
    'Russian': {'video': 'Видео', 'variant': 'Вариант', 'title': 'Название'},
    'English': {'video': 'Video', 'variant': 'Variant', 'title': 'Title'},
    'Spanish': {'video': 'Vídeo', 'variant': 'Variante', 'title': 'Título'},
    'French': {'video': 'Vidéo', 'variant': 'Variante', 'title': 'Titre'},
    'German': {'video': 'Video', 'variant': 'Variante', 'title': 'Titel'},
    'Chinese': {'video': '视频', 'variant': '版本', 'title': '标题'},
    'Japanese': {'video': '動画', 'variant': '案', 'title': 'タイトル'},
    'Korean': {'video': '동영상', 'variant': '버전', 'title': '제목'},
    'Portuguese': {'video': 'Vídeo', 'variant': 'Variante', 'title': 'Título'},
    'Italian': {'video': 'Video', 'variant': 'Variante', 'title': 'Titolo'},
    'Hindi': {'video': 'वीडियो', 'variant': 'विकल्प', 'title': 'शीर्षक'},
    'Arabic': {'video': 'فيديو', 'variant': 'خيار', 'title': 'عنوان'},
}


def _unicode_word_tokens(value: str) -> List[str]:
    """Split text into Unicode letter/mark/number tokens.

    Python's ``\\w`` is Unicode-aware for letters, but it does not keep every
    combining mark used by Devanagari and several other writing systems.  This
    scanner keeps marks attached to their base character and therefore works
    for all content languages exposed by the GUI.
    """
    tokens: List[str] = []
    current: List[str] = []
    for character in str(value or ''):
        category = unicodedata.category(character)
        is_word_character = category[:1] in {'L', 'N'} or (
            category[:1] == 'M' and bool(current)
        )
        if is_word_character:
            current.append(character)
        elif current:
            tokens.append(''.join(current))
            current = []
    if current:
        tokens.append(''.join(current))
    return tokens


def _is_hashtag_character(character: str) -> bool:
    category = unicodedata.category(character)
    return character == '_' or category[:1] in {'L', 'M', 'N'}


def _localized_fallback_label(language: str, key: str) -> str:
    labels = _FALLBACK_LABELS.get(language, _FALLBACK_LABELS['English'])
    return labels.get(key, _FALLBACK_LABELS['English'][key])


def _clean_title_text(value: str) -> str:
    """Remove generated-list noise without changing the title meaning."""
    title = str(value or '').strip()
    title = _TITLE_NOISE_PREFIX_RE.sub('', title)
    title = _TITLE_DUPLICATE_SUFFIX_RE.sub('', title).strip()
    title = title.strip(' "\'«»“”„`')
    title = re.sub(r'(?<!\w)#[^\s#]+', '', title, flags=re.UNICODE)
    title = re.sub(r'\s+', ' ', title)
    title = re.sub(r'\s+([,.:;!?…])', r'\1', title)
    title = re.sub(r'([,.:;!?…])(?=\S)', r'\1 ', title)
    title = re.sub(r'([!?]){3,}', r'\1', title)
    title = re.sub(r'\.{4,}', '...', title)
    return title.strip()


def _soften_loud_title_words(title: str) -> str:
    """Convert loud single words to sentence case while keeping short acronyms."""
    def repl(match: re.Match) -> str:
        word = match.group(0)
        return word[:1] + word[1:].lower()

    return _TITLE_ALL_CAPS_WORD_RE.sub(repl, title)


def _looks_like_question(title: str, language: str) -> bool:
    if str(title or '').rstrip().endswith(('?', '？', '؟')):
        return True
    lower = title.casefold()
    starters = _RU_QUESTION_STARTERS if language == 'Russian' else _EN_QUESTION_STARTERS
    return any(lower.startswith(starter) for starter in starters)


def _looks_like_exclamation(title: str) -> bool:
    lower = title.casefold()
    return any(marker in lower for marker in _TITLE_EXCLAMATION_MARKERS)


def _looks_incomplete_title(title: str) -> bool:
    words = _unicode_word_tokens(title.casefold())
    return bool(words and words[-1] in _TITLE_WEAK_ENDINGS)


def _trim_title(title: str, limit: int = TITLE_MAX_LENGTH) -> str:
    if len(title) <= limit:
        return title

    trimmed = title[:limit - 1].rstrip()
    last_space = trimmed.rfind(' ')
    if last_space >= 45:
        trimmed = trimmed[:last_space].rstrip()
    return f"{trimmed}…"


def _normalize_generated_title(value: str, fallback: str = '', language: str = 'Russian') -> str:
    """Return a publishable title: clean, complete-looking, with terminal punctuation."""
    title = _clean_title_text(value) or _clean_title_text(fallback)
    if not title:
        return _localized_fallback_label(language, 'video')

    title = _soften_loud_title_words(title)
    title = title[0].upper() + title[1:] if title[0].islower() else title
    if not _TITLE_TERMINAL_RE.search(title):
        if _looks_like_question(title, language):
            title += '?'
        elif _looks_like_exclamation(title):
            title += '!'
        else:
            title += '.'

    return _trim_title(title)


def _title_quality_score(title: str, language: str) -> int:
    clean = _clean_title_text(title)
    if not clean:
        return -100

    words = _unicode_word_tokens(clean)
    score = 0
    if 4 <= len(words) <= 12:
        score += 20
    if 35 <= len(clean) <= TITLE_TARGET_MAX_LENGTH:
        score += 20
    if _TITLE_TERMINAL_RE.search(clean):
        score += 12
    if _looks_like_question(clean, language) or _looks_like_exclamation(clean):
        score += 8
    if _TITLE_ACTION_STAKES_RE.search(clean):
        score += 14
    if _TITLE_STALE_CURIOSITY_RE.search(clean):
        score -= 14
    if _TITLE_ALL_CAPS_WORD_RE.search(_clean_title_text(title)):
        score -= 12
    if _looks_incomplete_title(clean):
        score -= 25
    if len(clean) > TITLE_MAX_LENGTH:
        score -= 15

    letters = [c for c in clean if c.isalpha()]
    if letters:
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper_ratio > 0.72:
            score -= 10

    return score


def _normalize_title_variants(
    variants: List[str],
    fallback_title: str,
    language: str,
    expected_count: int = 3,
) -> List[str]:
    cleaned: List[str] = []
    seen = set()

    for raw_title in list(variants or []) + ([fallback_title] if fallback_title else []):
        normalized = _normalize_generated_title(raw_title, fallback_title, language)
        key = normalized.casefold()
        if key not in seen and normalized:
            seen.add(key)
            cleaned.append(normalized)

    cleaned.sort(key=lambda item: _title_quality_score(item, language), reverse=True)
    return cleaned[:expected_count]


def _select_primary_title(
    description_data: Dict,
    fallback_title: str,
    language: str,
    subject_topic: str = "",
    parent_theme: str = "",
    recent_titles: List[str] = None,
    enforce_subject: bool = True,
) -> str:
    variants = _normalize_title_variants(
        description_data.get('title_variants', []),
        fallback_title,
        language,
    )
    if variants:
        if enforce_subject:
            variants = rank_title_candidates(
                variants,
                subject_topic=subject_topic or fallback_title or parent_theme,
                parent_theme=parent_theme,
                previous_titles=recent_titles or [],
            )
        description_data['title_variants'] = variants

    candidates = []
    explicit_title = description_data.get('title')
    if explicit_title:
        candidates.append(_normalize_generated_title(explicit_title, fallback_title, language))
    candidates.extend(variants)
    if fallback_title:
        candidates.append(_normalize_generated_title(fallback_title, fallback_title, language))

    if not candidates:
        return "Видео"

    return max(
        candidates,
        key=lambda item: (
            _title_quality_score(item, language)
            + (
                title_candidate_score(
                    item,
                    subject_topic=subject_topic or fallback_title or parent_theme,
                    parent_theme=parent_theme,
                    previous_titles=recent_titles or [],
                )
                if enforce_subject
                else 0
            )
        ),
    )


def _extract_hashtag_tokens(value) -> List[str]:
    if isinstance(value, str):
        tokens: List[str] = []
        text = str(value)
        index = 0
        while index < len(text):
            if text[index] != '#' or (index > 0 and _is_hashtag_character(text[index - 1])):
                index += 1
                continue
            end = index + 1
            while end < len(text) and _is_hashtag_character(text[end]):
                end += 1
            if end > index + 1:
                tokens.append(text[index:end])
            index = max(end, index + 1)
        return tokens
    if isinstance(value, (list, tuple, set)):
        tokens = []
        for item in value:
            tokens.extend(_extract_hashtag_tokens(str(item)))
        return tokens
    return []


def _normalize_hashtags(value, limit: int = 15, require_shorts: bool = True) -> List[str]:
    tokens = _extract_hashtag_tokens(value)
    normalized: List[str] = []
    seen = set()
    has_shorts = False

    for tag in tokens:
        # _extract_hashtag_tokens already guarantees a Unicode-safe body.
        # Avoid ``\w`` cleanup here: it strips valid trailing Devanagari marks.
        clean = tag.strip()
        if not clean.startswith('#') or len(clean) <= 1:
            continue

        if clean.casefold() in {'#short', '#shorts', '#шортс'}:
            clean = '#Shorts'
            has_shorts = True

        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(clean)

    if require_shorts and not has_shorts and '#shorts' not in seen:
        normalized.append('#Shorts')

    if require_shorts and len(normalized) > limit and '#Shorts' in normalized:
        without_shorts = [tag for tag in normalized if tag.casefold() != '#shorts']
        normalized = without_shorts[:max(0, limit - 1)] + ['#Shorts']
    else:
        normalized = normalized[:limit]

    return normalized


_TOPIC_HASHTAG_STOPWORDS = {
    'а', 'без', 'в', 'во', 'для', 'до', 'его', 'ее', 'её', 'же', 'за', 'и', 'из', 'или',
    'как', 'ко', 'на', 'над', 'не', 'но', 'о', 'об', 'от', 'по', 'под', 'при', 'про',
    'с', 'со', 'у', 'что', 'это', 'этот', 'эта', 'эти', 'the', 'and', 'for', 'from',
    'with', 'about', 'this', 'that', 'these', 'those', 'video', 'short', 'shorts',
}


def _hashtag_case(token: str) -> str:
    if not token:
        return ''
    if re.search(r'[А-Яа-яЁё]', token):
        return ''.join(part[:1].upper() + part[1:] for part in token.split())
    return token.replace(' ', '').lower()


def _generate_topic_hashtags(theme: str, language: str) -> List[str]:
    """Build topic-specific hashtags from the supplied theme without niche hardcoding."""
    min_length = 2 if language in {'Chinese', 'Japanese', 'Korean'} else 3
    words = [
        word
        for word in _unicode_word_tokens(str(theme or '').casefold())
        if len(word) >= min_length and word not in _TOPIC_HASHTAG_STOPWORDS
    ]

    candidates: List[str] = []
    for first, second in zip(words, words[1:]):
        if len(first) + len(second) <= 28:
            candidates.append(f'{first} {second}')
    candidates.extend(words)

    tags = []
    for candidate in candidates:
        clean = _hashtag_case(candidate)
        if clean and len(clean) <= 32:
            tags.append(f'#{clean}')

    return _normalize_hashtags(tags, limit=6, require_shorts=False)


def _topic_keywords(theme: str, limit: int = 4, language: str = '') -> List[str]:
    """Return compact searchable phrases from the theme without another AI call."""
    min_length = 2 if language in {'Chinese', 'Japanese', 'Korean'} else 4
    words = [
        word
        for word in _unicode_word_tokens(str(theme or '').casefold())
        if len(word) >= min_length and word not in _TOPIC_HASHTAG_STOPWORDS
    ]
    phrases: List[str] = []

    for first, second in zip(words, words[1:]):
        phrase = f"{first} {second}"
        if len(phrase) <= 34:
            phrases.append(phrase)
    phrases.extend(words)

    result: List[str] = []
    seen = set()
    for phrase in phrases:
        key = phrase.casefold()
        if key not in seen:
            seen.add(key)
            result.append(phrase)
        if len(result) >= limit:
            break
    return result


def _description_has_topic_evidence(description: str, theme: str, language: str = '') -> bool:
    """Return True when the public description already contains theme terms."""
    description_words = set(
        _unicode_word_tokens(str(description or '').casefold())
    )
    if not description_words:
        return False
    min_length = 2 if language in {'Chinese', 'Japanese', 'Korean'} else 4
    topic_words = [
        word
        for word in _unicode_word_tokens(str(theme or '').casefold())
        if len(word) >= min_length and word not in _TOPIC_HASHTAG_STOPWORDS
    ]
    return any(word in description_words for word in topic_words)


def _title_has_topic_evidence(title: str, theme: str) -> bool:
    """Return lexical topic evidence without any niche/category dictionary."""
    return topic_evidence_ratio(title, theme) > 0


def _build_theme_anchored_title(theme: str, language: str) -> str:
    """Create a conservative fallback without a catalog-style stock suffix."""
    clean_theme = _clean_title_text(theme)
    if not clean_theme:
        return _localized_fallback_label(language, 'video')
    return _normalize_generated_title(clean_theme, clean_theme, language)


def _ensure_description_topic_keywords(description: str, theme: str, language: str) -> str:
    """Append a small SEO keyword line when the generated description is too generic."""
    clean = str(description or '').strip()
    if len(clean) < 40 or not theme or _description_has_topic_evidence(clean, theme, language):
        return clean

    keywords = _topic_keywords(theme, limit=4, language=language)
    if not keywords:
        return clean

    labels = {
        'Russian': 'Темы', 'English': 'Topics', 'Spanish': 'Temas',
        'French': 'Sujets', 'German': 'Themen', 'Chinese': '主题',
        'Japanese': 'テーマ', 'Korean': '주제', 'Portuguese': 'Temas',
        'Italian': 'Temi', 'Hindi': 'विषय', 'Arabic': 'الموضوعات',
    }
    label = labels.get(language, 'Topics')
    separator = '' if clean.endswith(('.', '!', '?', '…', '。', '！', '？', '؟')) else '.'
    return f"{clean}{separator}\n\n{label}: {', '.join(keywords)}."


def _trim_description_words(description: str, max_words: int) -> str:
    """Bound verbose AI metadata without touching short, hand-written copy."""
    clean = str(description or '').strip()
    words = re.findall(r'\S+', clean, flags=re.UNICODE)
    if len(words) <= max_words:
        return clean
    trimmed = ' '.join(words[:max_words]).rstrip(' ,;:-')
    if trimmed and trimmed[-1] not in '.!?…':
        trimmed += '…'
    return trimmed


def _apply_metadata_quality_rules(
    description_data: Dict,
    fallback_title: str,
    theme: str,
    language: str,
    full_text: str = "",
    subject_theme: str = "",
    recent_titles: List[str] = None,
    video_duration: float = None,
) -> Dict:
    """Central post-processing for generated/publication metadata."""
    if not isinstance(description_data, dict):
        description_data = {}

    exact_subject = subject_theme or theme or fallback_title
    enforce_subject = bool(str(subject_theme or '').strip())
    description_data['title'] = _select_primary_title(
        description_data,
        fallback_title,
        language,
        subject_topic=exact_subject,
        parent_theme=theme,
        recent_titles=recent_titles or [],
        enforce_subject=enforce_subject,
    )
    if enforce_subject and not title_has_specific_evidence(
        description_data.get('title', ''),
        exact_subject,
        theme,
    ):
        anchored_title = _build_theme_anchored_title(exact_subject, language)
        description_data['title'] = anchored_title
        existing_variants = description_data.get('title_variants') or []
        description_data['title_variants'] = _normalize_title_variants(
            [anchored_title] + existing_variants,
            anchored_title,
            language,
        )

    hook_package = build_opening_hook_package(
        title=description_data.get('title') or fallback_title,
        theme=theme,
        full_text=full_text or description_data.get('description', ''),
        language=language,
        existing_hooks=description_data.get('opening_hooks'),
    )
    description_data['opening_hook_package'] = hook_package
    description_data['opening_hooks'] = hook_package.get('opening_hooks', [])
    description_data['primary_hook'] = hook_package.get('primary_hook', '')
    description_data['first_shot_title'] = hook_package.get('first_shot_title', '')
    description_data['opening_hook_family'] = hook_package.get('primary_family', '')

    merged_hashtags = []
    merged_hashtags.extend(_extract_hashtag_tokens(description_data.get('hashtags', [])))
    merged_hashtags.extend(_generate_smart_hashtags(theme, language))
    is_short = video_duration is not None and float(video_duration) <= 60.0
    description_data['hashtags'] = _normalize_hashtags(
        merged_hashtags,
        limit=5 if is_short else 12,
        require_shorts=is_short,
    )

    if description_data.get('description'):
        description_data['description'] = re.sub(
            r'\s+\b(?:RU|EN|ES|FR|DE)\b\s*$',
            '',
            str(description_data.get('description', '')).strip(),
            flags=re.IGNORECASE,
        )
        description_data['description'] = _ensure_description_topic_keywords(
            description_data['description'],
            theme,
            language,
        )
        if is_short:
            description_data['description'] = _trim_description_words(
                description_data['description'],
                SHORT_DESCRIPTION_MAX_WORDS,
            )

    return description_data


def generate_video_description(
    theme: str,
    title: str,
    full_text: str,
    video_duration: float,
    language: str,
    api_key: str,
    output_dir: Path,
    log_callback: Callable[[str], None] = _dummy_log,
    generate_ab_titles: bool = True,
    num_title_variants: int = 3,
    config_manager = None,
    video_num: int = 1,
    video_filename: str = None,
    subject_theme: str = None,
    recent_titles: List[str] = None,
    title_reserver: Callable[[List[str]], str] = None,
) -> Optional[Dict]:
    """
    Generate a complete video description for YouTube/social media.
    
    Args:
        theme: Main theme of the video
        title: Video title
        full_text: Full narration text
        video_duration: Video duration in seconds
        language: Language for description
        api_key: Google AI API key
        output_dir: Directory to save description file
        log_callback: Logging callback
        generate_ab_titles: Генерировать ли A/B варианты заголовков
        num_title_variants: Количество вариантов заголовков
        config_manager: Config manager для настроек
        video_num: Номер видео в батче (для раздельных файлов метаданных)
        subject_theme: Конкретная тема этого видео внутри общей темы серии
        recent_titles: Недавние заголовки батча для защиты от повторения формы
        title_reserver: Атомарно выбирает и резервирует финальный вариант в батче
        
    Returns:
        Dict with paths to files and data, or None
    """
    log_callback("📝 Генерация описания для видео...")
    
    try:
        # Create descriptions folder
        desc_dir = output_dir / "descriptions"
        desc_dir.mkdir(parents=True, exist_ok=True)
        
        # 🆕 ПРОВЕРКА: Используются ли раздельные файлы метаданных?
        use_separate_metadata = False
        metadata_from_files = None
        
        if config_manager:
            try:
                separate_enabled = config_manager.get('user_settings.separate_metadata.enabled', False)
                if separate_enabled:
                    # Файлы всегда в generated/metadata/
                    metadata_dir = Path("generated") / "metadata"
                    titles_path = str(metadata_dir / "titles.txt")
                    descriptions_path = str(metadata_dir / "descriptions.txt")
                    hashtags_path = str(metadata_dir / "hashtags.txt")
                    
                    if all([Path(titles_path).exists(), Path(descriptions_path).exists(), Path(hashtags_path).exists()]):
                        log_callback(f"   📋 Используются раздельные файлы метаданных (видео #{video_num})")
                        
                        # Загружаем метаданные
                        from core.metadata_loader import MetadataLoader
                        
                        all_metadata = MetadataLoader.load_metadata(
                            titles_path, descriptions_path, hashtags_path
                        )
                        
                        # Берём метаданные для текущего видео (video_num начинается с 1)
                        if video_num <= len(all_metadata):
                            metadata_from_files = all_metadata[video_num - 1]
                            use_separate_metadata = True
                            log_callback("   ✅ Метаданные загружены из файлов")
                            log_callback(f"      Название: {metadata_from_files['title'][:50]}...")
                        else:
                            log_callback(f"   ⚠️ Видео #{video_num} выходит за пределы метаданных ({len(all_metadata)} строк)")
                    else:
                        log_callback(f"   ⚠️ Файлы метаданных не найдены в {metadata_dir}")
                        
            except Exception as e:
                log_callback(f"   ⚠️ Ошибка загрузки раздельных метаданных: {e}")

        
        # 📝 Получаем настройки кастомного текста (ПЕРЕД генерацией)
        custom_text_settings = None
        if config_manager:
            try:
                enabled = config_manager.get('user_settings.custom_description_text.enabled', False)
                if enabled:
                    custom_text_settings = {
                        'enabled': True,
                        'position': config_manager.get('user_settings.custom_description_text.position', 'end'),
                        'text': config_manager.get('user_settings.custom_description_text.text', ''),
                        'ai_customize': config_manager.get('user_settings.custom_description_text.ai_customize', False),
                        'limit_2000': config_manager.get('user_settings.custom_description_text.limit_2000', False),
                        'generate_insta_files': config_manager.get('user_settings.custom_description_text.generate_insta_files', False)
                    }
                    if custom_text_settings['text']:
                        log_callback(f"   📝 Кастомный текст: добавлен ({custom_text_settings['position']})")
            except Exception as e:
                log_callback(f"   ⚠️ Ошибка загрузки кастомного текста: {e}")
        
        # Генерируем или используем готовые метаданные
        metadata_was_generated = False
        if use_separate_metadata and metadata_from_files:
            # Используем метаданные из файлов
            description_data = {
                'title': metadata_from_files['title'],
                'description': metadata_from_files['description'],
                'hashtags': metadata_from_files['hashtags'].split() if metadata_from_files['hashtags'] else []
            }
            log_callback("   📋 Метаданные из файлов (без AI генерации)")
        else:
            # Generate description using AI
            description_data = _generate_description_ai(
                theme=theme,
                title=title,
                full_text=full_text,
                video_duration=video_duration,
                language=language,
                api_key=api_key,
                log_callback=log_callback,
                generate_ab_titles=generate_ab_titles,
                num_title_variants=num_title_variants,
                custom_text_settings=custom_text_settings,
                subject_theme=subject_theme,
                recent_titles=recent_titles,
            )
            
            if not description_data:
                log_callback("   ⚠️ Не удалось сгенерировать описание")
                return None
            metadata_was_generated = True
            
        if video_filename:
            description_data['source_video_filename'] = Path(video_filename).name

        description_data = _apply_metadata_quality_rules(
            description_data=description_data,
            fallback_title=title,
            theme=theme,
            language=language,
            full_text=full_text,
            subject_theme=subject_theme or title,
            recent_titles=recent_titles,
            video_duration=video_duration,
        )

        if callable(title_reserver):
            candidates = [description_data.get('title', title)]
            candidates.extend(description_data.get('title_variants') or [])
            chosen_title = str(title_reserver(candidates) or '').strip()
            if chosen_title:
                description_data['title'] = chosen_title
                variants = [chosen_title] + list(description_data.get('title_variants') or [])
                description_data['title_variants'] = list(dict.fromkeys(variants))[:3]

        if metadata_was_generated:
            _auto_save_metadata_to_files(
                video_num=video_num,
                title=description_data.get('title', title),
                description=description_data.get('description', ''),
                hashtags=' '.join(description_data.get('hashtags', [])),
                opening_hooks=description_data.get('opening_hooks', []),
                log_callback=log_callback,
                output_dir=output_dir
            )
        
        # Format and save description
        formatted_desc = _format_description(description_data, video_duration, custom_text_settings, language=language, log_callback=log_callback)
        
        generate_insta = custom_text_settings.get('generate_insta_files', False) if custom_text_settings else False
        if not generate_insta and config_manager:
            try:
                generate_insta = config_manager.get('user_settings.custom_description_text.generate_insta_files', False)
            except Exception:
                pass
                
        if generate_insta:
            try:
                # НОВАЯ ЛОГИКА: Парсим эталонное описание вместо генерации нового
                # Эталонное описание уже сгенерировано выше (в переменной description)
                
                # Извлекаем компоненты из эталонного описания
                titles = _extract_titles_from_description(formatted_desc)
                info_text = _extract_info_block(formatted_desc)
                hashtags_list = _extract_hashtags_from_description(formatted_desc)
                
                # Если не удалось извлечь, используем fallback
                if not titles:
                    titles = description_data.get('title_variants', [title])[:3]
                if not info_text:
                    # Fallback: используем кастомный текст или AI описание
                    info_text = custom_text_settings.get('text', '') if custom_text_settings and custom_text_settings.get('enabled') else description_data.get('description', '')
                if not hashtags_list:
                    hashtags_list = description_data.get('hashtags', [])
                
                _auto_save_instagram_files(
                    video_num=video_num,
                    titles=titles,
                    description_text=info_text,
                    hashtags=hashtags_list,
                    output_dir=output_dir,
                    log_callback=log_callback,
                    language=language
                )
            except Exception as e:
                log_callback(f"   ⚠️ Ошибка сохранения файлов Instagram: {e}")
        
        # Create safe filename from generated title variants
        safe_title = _make_laconic_filename(description_data, title)
        
        # Дедупликация: если файл уже существует, добавляем суффикс
        desc_file = desc_dir / f"{safe_title}.txt"
        counter = 2
        while desc_file.exists():
            desc_file = desc_dir / f"{safe_title}_{counter}.txt"
            counter += 1
        
        with open(desc_file, 'w', encoding='utf-8') as f:
            f.write(formatted_desc)
        
        log_callback(f"   ✅ Описание сохранено: {desc_file.name}")
        
        # Результат без отдельного файла A/B тестов (данные уже в основном описании)
        result = {
            'description_file': str(desc_file),
            'description_data': description_data,
            'safe_title': safe_title
        }
        
        # A/B варианты заголовков доступны в description_data['title_variants']
        if generate_ab_titles and 'title_variants' in description_data:
            result['title_variants'] = description_data['title_variants']
            log_callback(f"   🎯 A/B заголовки: {len(description_data['title_variants'])} вариантов (в основном файле)")
        
        return result
        
    except Exception as e:
        log_callback(f"   ❌ Ошибка генерации описания: {e}")
        return None


def _make_laconic_filename(description_data: Dict, fallback_title: str) -> str:
    """Mirror the selected public title in the filename without changing its idea."""
    best = (
        description_data.get('title')
        or next(iter(description_data.get('title_variants') or []), '')
        or fallback_title
    )
    best = _clean_title_text(best)
    # Windows-invalid punctuation is removed, while the full subject-led wording
    # is preserved.  The old 3-5 word rewrite was a second source of sameness.
    safe = "".join(c for c in best if c.isalnum() or c in (' ', '-', '_')).strip()
    safe = re.sub(r'\s+', ' ', safe)[:96].rstrip(' ._-')
    if safe and safe[0].islower():
        safe = safe[0].upper() + safe[1:]
    return safe or "video_description"


def _generate_smart_hashtags(theme: str, language: str) -> List[str]:
    """Генерирует умные хэштеги на основе темы"""
    
    hashtags = []
    theme_lower = str(theme or '').casefold()
    hashtags.extend(_generate_topic_hashtags(theme, language))
    
    # Определяем категорию по ключевым словам
    category_keywords = {
        'gaming': ['игр', 'game', 'геймер', 'gamer', 'играть', 'play'],
        'anime': ['аниме', 'anime', 'манга', 'manga', 'отаку'],
        'movies': ['фильм', 'movie', 'кино', 'cinema', 'сериал', 'series'],
        'cartoons': ['мультфильм', 'мультик', 'cartoon', 'animation', 'гриффин', 'симпсон', 'рик и морти'],
        'facts': ['факт', 'fact', 'интересн', 'знал', 'правда', 'truth'],
        'humor': ['смешн', 'funny', 'юмор', 'humor', 'прикол', 'мем'],
        'tech': ['техн', 'tech', 'гаджет', 'gadget', 'телефон', 'компьютер'],
        'science': ['наук', 'science', 'космос', 'space', 'физик', 'биолог'],
        'history': ['истори', 'history', 'древн', 'ancient', 'война', 'war', 'вов', 'геро', 'подвиг'],
    }
    
    # Находим подходящие категории
    matched_categories = []
    for category, keywords in category_keywords.items():
        if any(kw in theme_lower for kw in keywords):
            matched_categories.append(category)
    
    # The legacy category lists mix Russian and English. Keep them only for
    # those two languages; other languages already receive topic-specific tags
    # and must not get foreign-script metadata appended after generation.
    if language in {'Russian', 'English'}:
        for category in matched_categories[:3]:
            if category in HASHTAG_CATEGORIES:
                category_tags = HASHTAG_CATEGORIES[category]
                if language == 'English':
                    category_tags = [tag for tag in category_tags if tag.isascii()]
                hashtags.extend(category_tags[:7])

    # Добавляем вирусные хэштеги
    hashtags.extend(
        VIRAL_HASHTAGS_BY_LANGUAGE.get(language, VIRAL_HASHTAGS_BY_LANGUAGE['English'])[:6]
    )

    return _normalize_hashtags(hashtags, limit=12, require_shorts=True)


def _generate_description_ai(
    theme: str,
    title: str,
    full_text: str,
    video_duration: float,
    language: str,
    api_key: str,
    log_callback: Callable[[str], None],
    generate_ab_titles: bool = True,
    num_title_variants: int = 3,
    custom_text_settings: Dict = None,
    subject_theme: str = None,
    recent_titles: List[str] = None,
) -> Optional[Dict]:
    """Generate description components using AI
    
    Args:
        custom_text_settings: Settings for custom text (optional)
            {
                'enabled': bool,
                'text': str,
                'limit_2000': bool  # If True, adjust description length
            }
    """
    
    # Determine language instruction
    lang_instruction = {
        'Russian': 'ОТВЕЧАЙ СТРОГО И ТОЛЬКО НА РУССКОМ ЯЗЫКЕ. Никаких других языков в тексте.',
        'English': 'ANSWER STRICTLY AND ONLY IN ENGLISH. No other languages in text.',
        'Spanish': 'RESPONDE ESTRICTA Y ÚNICAMENTE EN ESPAÑOL. Ningún otro idioma en el texto.',
        'French': 'RÉPONDEZ STRICTEMENT ET UNIQUEMENT EN FRANÇAIS. Aucune autre langue dans le texte.',
        'German': 'ANTWORTE STRENG UND NUR AUF DEUTSCH. Keine anderen Sprachen im Text.',
    }.get(language, f'Answer STRICTLY and ONLY in {language}. No other languages.')
    
    # Calculate duration
    duration_int = int(video_duration)
    
    # 📏 Рассчитываем лимит символов для описания
    is_short = video_duration <= 60
    description_length_instruction = (
        "40-80 слов, максимум 2 коротких абзаца"
        if is_short
        else "100-180 слов"
    )
    hashtag_instruction = "3-5" if is_short else "5-8"
    description_length_note = ""
    
    if custom_text_settings and custom_text_settings.get('enabled') and custom_text_settings.get('limit_2000'):
        custom_text = custom_text_settings.get('text', '')
        custom_text_length = len(custom_text)
        
        if custom_text_length > 0:
            # Оставляем место для форматирования (заголовки, хэштеги, теги) ~300 символов
            # И для самого кастомного текста
            available_for_description = 2000 - custom_text_length - 300
            
            if available_for_description < 100:
                available_for_description = 100  # Минимум 100 символов
                log_callback(f"   ⚠️ Кастомный текст слишком длинный ({custom_text_length} символов), описание будет минимальным")
            
            # Конвертируем в слова (примерно 5 символов на слово)
            words_limit = available_for_description // 5
            if is_short:
                words_limit = min(words_limit, SHORT_DESCRIPTION_MAX_WORDS)
            description_length_instruction = f"{words_limit} слов (максимум {available_for_description} символов)"
            description_length_note = f"\n⚠️ ВАЖНО: Описание должно быть КОРОТКИМ - максимум {available_for_description} символов, так как добавлен кастомный текст длиной {custom_text_length} символов. Общий лимит: 2000 символов."
            
            log_callback(f"   📏 Лимит 2000 символов: кастомный текст {custom_text_length} → описание до {available_for_description} символов")
    
    # 🎯 ВАРИАНТЫ ТЕСТИРОВАНИЯ: Добавляем генерацию вариантов заголовков
    ab_titles_instruction = ""
    ab_titles_schema = ""
    if generate_ab_titles:
        ab_titles_instruction = f"""
- title_variants: {num_title_variants} АЛЬТЕРНАТИВНЫХ заголовков для тестирования
  Каждый вариант должен быть предметным, правдивым и кликабельным:
  - Длина: 45-85 символов, максимум 100 символов.
  - Каждый заголовок должен быть законченным предложением, а не обрывком.
  - В конце обязателен знак препинания: ?, ! или .
  - Нельзя заканчивать предлогом, союзом или незавершенным оборотом.
  - Не добавляй хэштеги, эмодзи, кавычки и markdown.
  - Не пиши весь заголовок капсом: допускается капсом только 1-2 ключевых слова.
  - Варианты должны отличаться не заменой одного слова, а самой мыслью и конструкцией.
  - Не назначай вариантам фиксированные типы. Выбери разные реальные акценты из сценария.
  - Не более одного вопроса; числа используй только если они есть и важны в сценарии."""
        ab_titles_schema = '"title_variants": ["Вариант 1", "Вариант 2", "Вариант 3"],'

    exact_subject = str(subject_theme or title or theme or '').strip()
    recent_title_text = '\n'.join(
        f"- {str(item).strip()}" for item in (recent_titles or []) if str(item or '').strip()
    ) or "- нет"
    title_rules = subject_first_prompt_rules(language)
    
    ai_prompt = f"""Создай полное описание для YouTube видео. ВАЖНО: Весь возвращаемый контент должен быть строго на одном языке ({language}). Не смешивай языки!

ОБЩАЯ ТЕМА СЕРИИ (КОНТЕКСТ И SEO): {theme}
КОНКРЕТНЫЙ ПРЕДМЕТ ЭТОГО ВИДЕО (ГЛАВНЫЙ СМЫСЛОВОЙ ЯКОРЬ): {exact_subject}
РАБОЧЕЕ НАЗВАНИЕ: {title}
ДЛИТЕЛЬНОСТЬ: {duration_int} секунд
ТЕКСТ ВИДЕО (ИСТОЧНИК ФАКТОВ И КОНКРЕТИКИ): {full_text[:1200]}

НЕДАВНИЕ ЗАГОЛОВКИ ЭТОЙ СЕРИИ, ЧЬЮ ФОРМУ НЕЛЬЗЯ КОПИРОВАТЬ:
{recent_title_text}

{title_rules}

{lang_instruction}{description_length_note}

Верни JSON с полями:
{{
    "description": "Предметное описание видео ({description_length_instruction}). Без пересказа всего ролика и шаблонной воды. Один естественный призыв к действию.",
    {ab_titles_schema}
    "hashtags": ["#Хештег1", "#Хештег2", "..."]
}}

ТРЕБОВАНИЯ:
- В title_variants главным героем фразы должен быть КОНКРЕТНЫЙ ПРЕДМЕТ ЭТОГО ВИДЕО, а общая тема серии должна быть вплетена естественно как контекст, роль, эпоха, область или ставка.
- Не заменяй конкретного героя, объект или событие общей категорией. Если сценарий о частном примере, именно он и делает заголовок предметным.
- Не бери тему из YouTube-источников, b-roll или случайной иллюстративной детали, которой нет в конкретном предмете и сценарии.
- Не составляй заголовок как SEO-перечень, пару существительных через двоеточие или один шаблон с заменённым названием.
- description: Конкретное и читабельное описание ({description_length_instruction}); без SEO-полотна, списков ради объёма и повторения заголовка{ab_titles_instruction}
- hashtags: {hashtag_instruction} релевантных хештегов. Сначала тематические, потом общие. Не дублируй один и тот же смысл разными написаниями.
- language: ВЕСЬ ТЕКСТ (заголовки, описание, хэштеги) ДОЛЖЕН БЫТЬ ТОЛЬКО НА ЯЗЫКЕ: {language}. НИКАКОГО СМЕШИВАНИЯ ЯЗЫКОВ!

Верни ТОЛЬКО JSON, без markdown."""

    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    
    # Используем JSON Mode для гарантированного валидного JSON
    description_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": f"Описание видео: {description_length_instruction}"},
            "title_variants": {
                "type": "array", 
                "items": {"type": "string"},
                "description": "A/B варианты заголовков для тестирования"
            },
            "hashtags": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["description", "hashtags"] + (["title_variants"] if generate_ab_titles else [])
    }
    
    payload = {
        "contents": [{"role": "user", "parts": [{"text": ai_prompt}]}],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
            "responseSchema": description_schema
        }
    }
    
    MODEL_CHAIN = DESCRIPTION_MODEL_CHAIN
    RETRIES_PER_MODEL = 2
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
    
    for model_idx, model_name in enumerate(MODEL_CHAIN):
        # ⏳ Проверяем кулдаун
        cooldown_until = _model_cooldowns.get(model_name, 0)
        if time.time() < cooldown_until:
            remaining_min = int((cooldown_until - time.time()) / 60) + 1
            log_callback(f"   ⏳ {model_name}: кулдаун ещё {remaining_min} мин, пропускаем")
            continue

        url = f"{BASE_URL}/{model_name}:generateContent"
        
        for attempt in range(RETRIES_PER_MODEL):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=60)
                
                # 🔧 ОБРАБОТКА 503/429 — переходим к следующей модели + ставим кулдаун
                if resp.status_code in (503, 429):
                    wait_time = 3 * (attempt + 1)
                    if attempt < RETRIES_PER_MODEL - 1:
                        log_callback(f"   ⚠️ {model_name}: {resp.status_code}. Retry {attempt + 1}/{RETRIES_PER_MODEL}, ожидание {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        # Ставим кулдаун 25 минут для этой модели
                        _model_cooldowns[model_name] = time.time() + MODEL_COOLDOWN_SECONDS
                        next_model = MODEL_CHAIN[model_idx + 1] if model_idx + 1 < len(MODEL_CHAIN) else None
                        if next_model:
                            log_callback(f"   🚫 {model_name}: {resp.status_code} — кулдаун 25 мин → fallback на {next_model}")
                        else:
                            log_callback(f"   ❌ Все модели перегружены ({resp.status_code})")
                            return None
                        break  # Переходим к следующей модели
                
                # Другие ошибки API
                if resp.status_code != 200:
                    log_callback(f"   ⚠️ {model_name}: API error {resp.status_code}: {resp.text[:200]}")
                    # 404 = модель не существует — пропускаем сразу
                    if resp.status_code == 404:
                        log_callback(f"   ⚠️ Модель {model_name} недоступна, пропускаем")
                        break
                    return None
                
                # Успешный ответ
                data = resp.json()
                candidates = data.get('candidates', [])
                if not candidates:
                    log_callback(f"   ⚠️ {model_name}: пустой ответ (нет candidates)")
                    return None
                
                parts = candidates[0].get('content', {}).get('parts', [])
                result_text = "".join([p.get('text', '') for p in parts]).strip()
                
                if not result_text:
                    log_callback(f"   ⚠️ {model_name}: пустой текст")
                    return None
                
                result = json.loads(result_text)
                
                if model_idx > 0 or attempt > 0:
                    log_callback(f"   🤖 AI описание сгенерировано ({model_name}, попытка {attempt + 1})")
                else:
                    log_callback("   🤖 AI описание сгенерировано")
                
                return result
                
            except json.JSONDecodeError as e:
                log_callback(f"   ⚠️ {model_name}: JSON parse error: {e}")
                return None
            except requests.exceptions.Timeout:
                log_callback(f"   ⚠️ {model_name}: timeout, попытка {attempt + 1}/{RETRIES_PER_MODEL}")
                if attempt < RETRIES_PER_MODEL - 1:
                    time.sleep(3)
                    continue
                # Timeout после всех попыток — кулдаун
                _model_cooldowns[model_name] = time.time() + MODEL_COOLDOWN_SECONDS
                log_callback(f"   🚫 {model_name}: timeout exhausted — кулдаун 25 мин")
                break  # Переходим к следующей модели
            except Exception as e:
                log_callback(f"   ⚠️ {model_name}: ошибка: {e}")
                return None
    
    log_callback("   ❌ Все модели исчерпаны, описание не сгенерировано")
    return None


def _split_description_smart(description: str) -> tuple:
    """
    Smart split of description into main content and farewell/CTA.
    
    Args:
        description: Full AI-generated description
        
    Returns:
        Tuple of (main_content, farewell_cta)
    """
    # Farewell markers (in order of priority)
    FAREWELL_MARKERS = [
        "ждет вас ниже в описании",
        "в описании под видео",
        "полный рецепт",
        "не забудьте",
        "поставить лайк",
        "подписаться",
        "поделиться",
    ]
    
    # Search for markers
    best_split_pos = -1
    
    for marker in FAREWELL_MARKERS:
        pos = description.lower().find(marker.lower())
        if pos != -1:
            # Find end of sentence (next period after marker)
            period_pos = description.find('.', pos)
            if period_pos != -1:
                best_split_pos = period_pos + 1  # Include the period
                break
    
    # Fallback: split at 70% of description length
    if best_split_pos == -1:
        fallback_pos = int(len(description) * 0.7)
        # Find nearest sentence boundary (period)
        period_pos = description.find('.', fallback_pos)
        if period_pos != -1:
            best_split_pos = period_pos + 1
        else:
            # No period found, split at 70%
            best_split_pos = fallback_pos
    
    # Split description
    main_content = description[:best_split_pos].strip()
    farewell_cta = description[best_split_pos:].strip()
    
    return main_content, farewell_cta


def _auto_save_metadata_to_files(
    video_num: int,
    title: str,
    description: str,
    hashtags: str,
    log_callback: Callable[[str], None],
    output_dir: Path = None,
    opening_hooks: List[Dict] = None,
):
    """
    Автоматически сохраняет метаданные в файлы по мере генерации видео
    
    Args:
        video_num: Номер видео (начинается с 1)
        title: Название видео
        description: Описание видео
        hashtags: Хештеги (строка через пробел)
        log_callback: Функция для логирования
        output_dir: Папка вывода (если None, используется generated/)
    """
    try:
        from pathlib import Path
        
        # Папка для метаданных (используем output_dir если передан)
        if output_dir:
            metadata_dir = output_dir / "metadata"
        else:
            metadata_dir = Path("generated") / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        
        # Пути к файлам
        titles_file = metadata_dir / "titles.txt"
        descriptions_file = metadata_dir / "descriptions.txt"
        hashtags_file = metadata_dir / "hashtags.txt"
        opening_hooks_file = metadata_dir / "opening_hooks.txt"
        
        # Читаем существующие файлы (только непустые строки)
        existing_titles = []
        existing_descriptions = []
        existing_hashtags = []
        existing_opening_hooks = []
        
        if titles_file.exists():
            with open(titles_file, 'r', encoding='utf-8') as f:
                # Читаем все строки, включая пустые
                existing_titles = [line.rstrip('\n\r') for line in f.readlines()]
        
        if descriptions_file.exists():
            with open(descriptions_file, 'r', encoding='utf-8') as f:
                existing_descriptions = [line.rstrip('\n\r') for line in f.readlines()]
        
        if hashtags_file.exists():
            with open(hashtags_file, 'r', encoding='utf-8') as f:
                existing_hashtags = [line.rstrip('\n\r') for line in f.readlines()]

        if opening_hooks_file.exists():
            with open(opening_hooks_file, 'r', encoding='utf-8') as f:
                existing_opening_hooks = [line.rstrip('\n\r') for line in f.readlines()]
        
        # Расширяем списки до нужного размера (если нужно)
        while len(existing_titles) < video_num:
            existing_titles.append("")
        while len(existing_descriptions) < video_num:
            existing_descriptions.append("")
        while len(existing_hashtags) < video_num:
            existing_hashtags.append("")
        while len(existing_opening_hooks) < video_num:
            existing_opening_hooks.append("")
        
        # Обновляем данные для текущего видео (video_num начинается с 1)
        # Убираем ВСЕ переносы строк и заменяем на пробелы
        clean_title = _normalize_generated_title(title, fallback=f"Видео {video_num}")
        clean_hashtags = " ".join(_normalize_hashtags(hashtags, limit=15, require_shorts=True))
        clean_hooks = []
        for hook in opening_hooks or []:
            if isinstance(hook, dict):
                text = str(hook.get('text') or '').replace('|', ' ').replace('\n', ' ').replace('\r', ' ').strip()
                if text:
                    clean_hooks.append(text)
            else:
                text = str(hook).replace('|', ' ').replace('\n', ' ').replace('\r', ' ').strip()
                if text:
                    clean_hooks.append(text)
        existing_titles[video_num - 1] = clean_title.replace('\n', ' ').replace('\r', ' ').strip()
        existing_descriptions[video_num - 1] = description.replace('\n', ' ').replace('\r', ' ').strip()
        existing_hashtags[video_num - 1] = clean_hashtags.replace('\n', ' ').replace('\r', ' ').strip()
        existing_opening_hooks[video_num - 1] = " | ".join(clean_hooks[:5]).strip()
        
        # ПЕРЕЗАПИСЫВАЕМ файлы полностью (не добавляем, а заменяем)
        with open(titles_file, 'w', encoding='utf-8') as f:
            for t in existing_titles:
                f.write(f"{t}\n")
        
        with open(descriptions_file, 'w', encoding='utf-8') as f:
            for d in existing_descriptions:
                f.write(f"{d}\n")
        
        with open(hashtags_file, 'w', encoding='utf-8') as f:
            for h in existing_hashtags:
                f.write(f"{h}\n")

        with open(opening_hooks_file, 'w', encoding='utf-8') as f:
            for h in existing_opening_hooks:
                f.write(f"{h}\n")
        
        log_callback(f"   💾 Метаданные сохранены в файлы (видео #{video_num})")
        
    except Exception as e:
        log_callback(f"   ⚠️ Ошибка автосохранения метаданных: {e}")


def _format_description(data: Dict, video_duration: float, custom_text_settings: Dict = None, language: str = 'Russian', include_titles: bool = True, include_hashtags: bool = True, instagram_mode: bool = False, log_callback=None) -> str:
    """Format description data into final text with beautiful formatting

    Args:
        data: Description data from AI
        video_duration: Video duration in seconds
        custom_text_settings: Settings for custom text
            {
                'enabled': bool,
                'position': 'start' | 'between' | 'split' | 'end',
                'text': str,
                'ai_customize': bool,  # Enable AI customization
                'limit_2000': bool     # Limit custom text to 2000 chars
            }
        language: Language of the description
        log_callback: Функция логирования (если None — используется print)
    """
    if log_callback is None:
        log_callback = lambda msg: print(msg)
    
    lines = []
    
    # A/B Title variants (если есть) - В САМОМ ВЕРХУ
    title_variants = data.get('title_variants', [])
    if title_variants and include_titles:
        lines.append("╔═══════════════════════════════════════╗")
        lines.append("║   🎯 ВАРИАНТЫ ЗАГОЛОВКОВ               ║")
        lines.append("╚═══════════════════════════════════════╝")
        lines.append("")
        for i, variant in enumerate(title_variants, 1):
            lines.append(f"   {i}️⃣ {variant}")
        lines.append("")
        lines.append("─" * 50)
        lines.append("")
    
    # Получаем кастомный текст (с AI кастомизацией если включено)
    custom_text = ""
    limit_2000 = False
    if custom_text_settings and custom_text_settings.get('enabled'):
        custom_text = custom_text_settings.get('text', '').strip()
        limit_2000 = custom_text_settings.get('limit_2000', False)
        
        # AI кастомизация если включена
        if custom_text and custom_text_settings.get('ai_customize', False):
            try:
                from core.text_customizer import TextCustomizer
                from core.gemini_client import GeminiClient
                from core.config_manager import ConfigManager

                original_custom_text = custom_text  # сохраняем оригинал для fallback

                # Защищаем ссылки, артикулы, контакты
                text_with_placeholders, protected = TextCustomizer.extract_protected_elements(custom_text)

                # Проверяем содержит ли текст рецепт (ингредиенты + инструкции)
                is_recipe = any(keyword in custom_text.lower() for keyword in [
                    'ингредиенты', 'приготовление', 'выпекайте', 'взбейте',
                    'компоненты', 'порядок', 'инструкция'
                ])

                # Для Instagram режима ВСЕГДА применяем лимит 2000 символов
                max_length = None
                if limit_2000:
                    max_length = 2000
                    if is_recipe:
                        log_callback(f"   📝 Обнаружен рецепт — AI уложит в {max_length} символов")
                    else:
                        log_callback(f"   📏 Лимит {max_length} символов для Instagram")
                elif is_recipe:
                    log_callback("   📝 Обнаружен рецепт — AI сохранит полностью (без лимита)")

                # Создаём промпт для AI с учётом лимита
                prompt = TextCustomizer.get_customization_prompt(
                    text_with_placeholders,
                    language=language,
                    max_length=max_length
                )

                # Получаем API ключ
                config_manager = ConfigManager()
                api_key = config_manager.get_user_setting('gemini_api_key')

                if api_key:
                    client = GeminiClient(api_key)

                    # ── ПОПЫТКА 1: Flash, температура 0.65 (не провоцирует "фантазирование")
                    customized_response = client.generate_text(
                        prompt,
                        temperature=0.65,
                        max_tokens=16384,
                        models=[FAST_TEXT_MODEL]
                    )

                    # ── ПОПЫТКА 2: при MAX_TOKENS переключаемся на Pro с ещё большим лимитом
                    if not customized_response.success and 'MAX_TOKENS' in (customized_response.error or ''):
                        log_callback(f"   ⚠️ Flash обрезал текст (MAX_TOKENS) → retry через {PRO_TEXT_MODEL}...")
                        customized_response = client.generate_text(
                            prompt,
                            temperature=0.65,
                            max_tokens=32768,
                            models=[PRO_TEXT_MODEL]
                        )

                    if customized_response.success:
                        result_text = customized_response.raw_text

                        # ── Валидация целостности: результат не должен быть аномально коротким
                        min_expected_len = max(100, int(len(original_custom_text) * 0.4))
                        if len(result_text) < min_expected_len:
                            log_callback(
                                f"   ⚠️ AI вернул подозрительно короткий текст "
                                f"({len(result_text)} симв. < ожидаемых {min_expected_len}), "
                                f"используется оригинал"
                            )
                        else:
                            # ── Проверяем, что все защищённые элементы сохранены в результате
                            restored = TextCustomizer.restore_protected_elements(result_text, protected)
                            # 🔧 ИСПРАВЛЕНО: проверяем наличие ОРИГИНАЛЬНЫХ значений (не плейсхолдеров)
                            missing = [orig for placeholder, orig in protected.items() if orig not in restored]
                            if missing:
                                log_callback(
                                    f"   ⚠️ AI потерял {len(missing)} защищённых элемент(ов), "
                                    f"используется оригинал"
                                )
                            else:
                                custom_text = restored
                                log_callback(
                                    f"   ✅ Кастомный текст перефразирован через AI "
                                    f"(Язык: {language}, {len(custom_text)} симв.)"
                                )
                                if max_length and len(custom_text) > max_length:
                                    log_callback(
                                        f"   ⚠️ AI превысил лимит ({max_length} симв.), "
                                        f"рецепт сохранён полностью"
                                    )
                    else:
                        log_callback(
                            f"   ⚠️ AI кастомизация не удалась: {customized_response.error} "
                            f"— используется оригинальный текст"
                        )
                else:
                    log_callback("   ⚠️ API ключ не найден, используется оригинальный текст")

            except Exception as e:
                log_callback(f"   ⚠️ Ошибка AI кастомизации: {e}, используется оригинальный текст")
    
    # === INSTAGRAM MODE ===
    # Если включен режим Instagram, мы оставляем ТОЛЬКО кастомный текст,
    # без AI-описания, без заголовков, без хештегов и без рамок.
    if instagram_mode:
        if custom_text:
            return _format_custom_text_block(custom_text, limit_2000=limit_2000, instagram_mode=True)
        else:
            return _format_description_block(data.get('description', ''))
    
    # === POSITION: START ===
    if custom_text and custom_text_settings.get('position') == 'start':
        lines.append(_format_custom_text_block(custom_text, limit_2000=limit_2000, other_content_length=len('\n'.join(lines))))
        lines.append("")
    
    # Main description
    description = data.get('description', '')
    
    # === POSITION: SPLIT (Smart splitting) ===
    if custom_text and custom_text_settings.get('position') == 'split':
        if description:
            # Split description intelligently
            main_content, farewell_cta = _split_description_smart(description)
            
            # Add main content with formatting
            lines.append(_format_description_block(main_content))
            lines.append("")
            
            # Add custom text
            lines.append(_format_custom_text_block(custom_text, limit_2000=limit_2000, other_content_length=len('\n'.join(lines))))
            lines.append("")
            
            # Add farewell/CTA
            if farewell_cta:
                lines.append(_format_cta_block(farewell_cta))
                lines.append("")
            
            # Skip normal description processing
            description = ""
    
    # Main description (if not split)
    if description:
        lines.append(_format_description_block(description))
        lines.append("")
    
    # Timecodes have been removed per user request
    
    # === POSITION: BETWEEN ===
    if custom_text and custom_text_settings.get('position') == 'between':
        lines.append(_format_custom_text_block(custom_text, limit_2000=limit_2000, other_content_length=len('\n'.join(lines))))
        lines.append("")
    
    # Hashtags
    hashtags = data.get('hashtags', [])
    if hashtags and include_hashtags:
        lines.append("┌─────────────────────────────────────┐")
        lines.append("│  🏷️  ХЕШТЕГИ                        │")
        lines.append("└─────────────────────────────────────┘")
        lines.append("")
        # Группируем хэштеги по 5 в строке для читаемости
        for i in range(0, len(hashtags), 5):
            chunk = hashtags[i:i+5]
            lines.append("   " + " ".join(chunk))
        lines.append("")
    
    # A/B Title variants moved to the top
    
    
    # === POSITION: END ===
    if custom_text and custom_text_settings.get('position') == 'end':
        lines.append(_format_custom_text_block(custom_text, limit_2000=limit_2000, other_content_length=len('\n'.join(lines))))
    
    return "\n".join(lines)


def _format_description_block(text: str) -> str:
    """Форматирует основной блок описания с красивой структурой"""
    if not text:
        return ""
    
    # Разбиваем на предложения
    sentences = text.replace('! ', '!\n').replace('. ', '.\n').replace('? ', '?\n').split('\n')
    sentences = [s.strip() for s in sentences if s.strip()]
    
    formatted_lines = []
    
    # Группируем по 2-3 предложения в абзац
    for i in range(0, len(sentences), 2):
        paragraph = ' '.join(sentences[i:i+2])
        formatted_lines.append(paragraph)
        if i + 2 < len(sentences):  # Не добавляем пустую строку после последнего абзаца
            formatted_lines.append("")
    
    return '\n'.join(formatted_lines)


def _format_custom_text_block(text: str, limit_2000: bool = False, instagram_mode: bool = False, other_content_length: int = 0) -> str:
    """Форматирует кастомный текст с красивой рамкой
    
    Args:
        text: Кастомный текст для форматирования
        limit_2000: Если True, обрезает ВЕСЬ ИТОГОВЫЙ БЛОК до 2000 символов (включая форматирование)
        instagram_mode: Если True, возвращает текст без ASCII рамок
        other_content_length: Длина остального контента (заголовки, хештеги, рамки) для динамического расчёта overhead
    """
    if not text:
        return ""
    
    # 📏 Применяем лимит 2000 символов если включен
    if limit_2000:
        if instagram_mode:
            # Для Instagram: просто обрезаем текст до 2000 символов (без рамок)
            if len(text) > 2000:
                text = text[:1997]  # Оставляем место для "..."
                # Находим последний пробел чтобы не разрывать слово
                last_space = text.rfind(' ')
                if last_space > 1900:  # Если пробел не слишком далеко
                    text = text[:last_space]
                text += "..."
        else:
            # Для обычных описаний: учитываем форматирование
            # Динамический расчёт overhead на основе фактического контента
            if other_content_length > 0:
                # Добавляем ~130 символов на рамку информационного блока + отступы
                FORMATTING_OVERHEAD = other_content_length + 130
            else:
                # Fallback: если длина неизвестна, используем консервативную оценку
                FORMATTING_OVERHEAD = 1000
            
            max_text_length = 2000 - FORMATTING_OVERHEAD
            if len(text) > max_text_length:
                # Обрезаем текст, оставляя место для "..."
                text = text[:max_text_length - 3]
                # Находим последний пробел чтобы не разрывать слово
                last_space = text.rfind(' ')
                if last_space > max_text_length - 100:  # Если пробел не слишком далеко
                    text = text[:last_space]
                text += "..."
    
    lines = []
    if not instagram_mode:
        lines.append("╔═══════════════════════════════════════╗")
        lines.append("║        📝 ИНФОРМАЦИЯ К ВИДЕО          ║")
        lines.append("╚═══════════════════════════════════════╝")
        lines.append("")
    
    # Разбиваем текст на логические блоки
    # Ищем разделители типа "Ингредиенты:", "Приготовление:" и т.д.
    text_lines = text.split('\n')
    for line in text_lines:
        line = line.strip()
        if not line:
            lines.append("")
            continue
        
        # Заголовки (содержат ":" в конце или ключевые слова)
        if line.endswith(':') or any(keyword in line.lower() for keyword in ['ингредиенты', 'приготовление', 'артикул']):
            lines.append(f"✨ {line}")
        else:
            lines.append(f"   {line}")
    
    return '\n'.join(lines)


def _format_cta_block(text: str) -> str:
    """Форматирует блок призыва к действию"""
    if not text:
        return ""
    
    lines = []
    lines.append("┌─────────────────────────────────────┐")
    lines.append("│  💫 ПРИЗЫВ К ДЕЙСТВИЮ               │")
    lines.append("└─────────────────────────────────────┘")
    lines.append("")
    lines.append(f"   {text}")
    
    return '\n'.join(lines)


def _extract_titles_from_description(full_description: str) -> List[str]:
    """
    Извлекает 3 варианта заголовков из эталонного описания.
    
    Ищет блок:
    ╔═══════════════════════════════════════╗
    ║   🎯 ВАРИАНТЫ ЗАГОЛОВКОВ               ║
    ╚═══════════════════════════════════════╝
    
    1️⃣ Заголовок 1
    2️⃣ Заголовок 2
    3️⃣ Заголовок 3
    """
    titles = []
    
    # Ищем блок с заголовками
    title_block_pattern = r'🎯 ВАРИАНТЫ ЗАГОЛОВКОВ.*?─{40,}'
    match = re.search(title_block_pattern, full_description, re.DOTALL)
    
    if match:
        block = match.group(0)
        # Извлекаем строки с эмодзи номеров (1️⃣ 2️⃣ 3️⃣)
        title_lines = re.findall(r'[1-3]️⃣\s*(.+)', block)
        
        for title in title_lines:
            # Убираем эмодзи в конце и лишние пробелы
            cleaned = re.sub(r'\s*[🫐🤫✨💫🎯📝🏷️]+\s*$', '', title).strip()
            if cleaned:
                titles.append(cleaned)
    
    # Если не нашли, возвращаем пустой список (fallback будет в _auto_save_instagram_files)
    return titles[:3]  # Максимум 3


def _extract_info_block(full_description: str) -> str:
    """
    Извлекает информационный блок из эталонного описания (без рамок).
    
    Ищет нейтральный блок "ИНФОРМАЦИЯ К ВИДЕО".
    """
    
    # Ищем начало информационного блока
    info_start = full_description.find('📝 ИНФОРМАЦИЯ К ВИДЕО')
    
    if info_start == -1:
        return ""
    
    # Ищем конец блока (следующая рамка хештегов)
    info_text = full_description[info_start:]
    
    # 🔧 ИСПРАВЛЕНО: Ищем блок хештегов по заголовку, а не по рамке
    # Это позволяет включить весь текст между информационным блоком и хештегами
    hashtag_start = info_text.find('🏷️  ХЕШТЕГИ')
    if hashtag_start == -1:
        # Если нет блока хештегов, берем до конца
        info_section = info_text
    else:
        # Берем до начала заголовка хештегов (включая текст перед ним)
        # Ищем рамку перед заголовком хештегов
        hashtag_frame_start = info_text.rfind('┌─────', 0, hashtag_start)
        if hashtag_frame_start != -1:
            info_section = info_text[:hashtag_frame_start]
        else:
            info_section = info_text[:hashtag_start]
    
    # Пропускаем заголовок блока (3 строки рамки)
    lines = info_section.split('\n')
    content_start = 0
    for i, line in enumerate(lines):
        if '╚═══' in line:
            content_start = i + 1
            break
    
    # Собираем контент
    content_lines = []
    for i in range(content_start, len(lines)):
        line = lines[i]
        
        # 🔧 ИСПРАВЛЕНО: Сохраняем отступы для структуры (списки, подпункты)
        # Убираем только начальные 3 пробела (форматирование блока)
        if line.startswith('   '):
            cleaned = line[3:]
        else:
            cleaned = line
        
        # 🔧 ИСПРАВЛЕНО: Не удаляем строки с эмодзи - они часть оформления
        # Пропускаем только полностью пустые строки в начале
        if cleaned or content_lines:  # Добавляем строку если она не пустая ИЛИ уже есть контент
            content_lines.append(cleaned)
    
    info = '\n'.join(content_lines).strip()
    
    # Убираем лишние пустые строки в конце
    while info.endswith('\n\n\n'):
        info = info[:-1]
    
    return info


def _extract_hashtags_from_description(full_description: str) -> List[str]:
    """
    Извлекает хештеги из эталонного описания.
    
    Ищет блок:
    ┌─────────────────────────────────────┐
    │  🏷️  ХЕШТЕГИ                        │
    └─────────────────────────────────────┘
    
    #хештег1 #хештег2 #хештег3
    """
    
    hashtags = []
    
    # Ищем начало блока с хештегами
    hashtag_start = full_description.find('🏷️')
    
    if hashtag_start != -1:
        # Берем текст от начала блока до конца
        hashtag_section = full_description[hashtag_start:]
        
        # Извлекаем все хештеги (#слово, включая кириллицу)
        found_hashtags = re.findall(r'#[а-яА-ЯёЁa-zA-Z0-9_]+', hashtag_section)
        hashtags.extend(found_hashtags)
    
    return hashtags


def _auto_save_instagram_files(
    video_num: int,
    titles: List[str],
    description_text: str,
    hashtags: List[str],
    output_dir: Path,
    log_callback: Callable[[str], None],
    language: str = 'Russian'
):
    """
    Сохраняет 3 файла для авто-публикации в Instagram:
    ФАЙЛ С НАЗВАНИЯМИ ДЛЯ ВИДЕО.txt, ФАЙЛ С ОПИСАНИЕМ ДЛЯ ВИДЕО.txt, ХЕШТЕГИ.txt
    
    НОВАЯ ЛОГИКА: Использует данные из эталонного описания (не генерирует заново)
    
    Args:
        video_num: Номер видео (1-40)
        titles: Список из 3 заголовков (извлечены из эталонного описания)
        description_text: Текст описания (информационный блок из эталонного описания)
        hashtags: Список хештегов (извлечены из эталонного описания)
        output_dir: Папка для сохранения
        log_callback: Функция для логирования
        language: Язык (не используется, оставлен для совместимости)
    
    Формат:
    - Названия: 3 варианта для каждого видео, разделенные пустой строкой
    - Описания: 1 строка = 1 видео (многострочный текст конвертируется через \n)
    - Хештеги: 1 строка = все хештеги для 1 видео (максимум 12)
    """
    try:
        # Папка для метаданных Instagram (в той же папке что и видео)
        metadata_dir = output_dir / "instagram_metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        
        # Пути к файлам
        titles_file = metadata_dir / "ФАЙЛ С НАЗВАНИЯМИ ДЛЯ ВИДЕО.txt"
        descriptions_file = metadata_dir / "ФАЙЛ С ОПИСАНИЕМ ДЛЯ ВИДЕО.txt"
        hashtags_file = metadata_dir / "ХЕШТЕГИ.txt"
        
        # --- 1. НАЗВАНИЯ (используем переданные из эталонного описания) ---
        existing_titles_blocks = []
        try:
            if titles_file.exists():
                with open(titles_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    existing_titles_blocks = content.split('\n\n')
        except Exception as e:
            log_callback(f"   ⚠️ Ошибка чтения файла названий: {e}")
            existing_titles_blocks = []
        
        while len(existing_titles_blocks) < video_num:
            existing_titles_blocks.append("")
        
        normalized_titles = _normalize_title_variants(titles or [], "", language, expected_count=3)

        # НОВАЯ ЛОГИКА: Используем titles из эталонного описания (не генерируем через API)
        if normalized_titles and len(normalized_titles) >= 3:
            existing_titles_blocks[video_num - 1] = '\n'.join(normalized_titles[:3])
            log_callback("   ✅ Использованы названия из эталонного описания")
        elif normalized_titles and len(normalized_titles) > 0:
            while len(normalized_titles) < 3:
                label = _localized_fallback_label(language, 'variant')
                normalized_titles.append(
                    _normalize_generated_title(
                        f"{label} {len(normalized_titles) + 1}",
                        language=language,
                    )
                )
            existing_titles_blocks[video_num - 1] = '\n'.join(normalized_titles[:3])
            log_callback("   ✅ Использованы названия из эталонного описания (дополнены)")
        else:
            label = _localized_fallback_label(language, 'title')
            existing_titles_blocks[video_num - 1] = "\n".join(
                f"{label} {index}" for index in range(1, 4)
            )
            log_callback("   ⚠️ Названия не найдены, используется fallback")
        
        try:
            with open(titles_file, 'w', encoding='utf-8') as f:
                f.write('\n\n'.join(existing_titles_blocks))
        except Exception as e:
            log_callback(f"   ⚠️ Ошибка сохранения файла названий: {e}")
            
        # --- 2. ОПИСАНИЕ (конвертируем многострочное в одну строку через \n) ---
        existing_descs = []
        try:
            if descriptions_file.exists():
                with open(descriptions_file, 'r', encoding='utf-8') as f:
                    existing_descs = [line.rstrip('\n\r') for line in f.readlines()]
        except Exception as e:
            log_callback(f"   ⚠️ Ошибка чтения файла описаний: {e}")
            existing_descs = []
                
        while len(existing_descs) < video_num:
            existing_descs.append("")
        
        # Конвертация согласно ТЗ:
        # - Каждая строка текста → \n (без пробелов до/после)
        # - Пустая строка → \n\n (без пробелов до/после)
        # - Результат: 1 описание = 1 строка в файле
        
        lines = description_text.split('\n')
        converted_parts = []
        
        prev_was_empty = False
        for line in lines:
            stripped = line.strip()
            
            if stripped:
                # Непустая строка
                if prev_was_empty and converted_parts:
                    # Предыдущая была пустая - добавляем двойной перенос
                    converted_parts.append('\\n\\n')
                elif converted_parts:
                    # Обычный перенос между строками
                    converted_parts.append('\\n')
                
                converted_parts.append(stripped)
                prev_was_empty = False
            else:
                # Пустая строка - запоминаем
                prev_was_empty = True
        
        # Склеиваем без пробелов
        literal_desc = ''.join(converted_parts)
        
        # Очищаем markdown-артефакты (AI иногда добавляет ** и * несмотря на запрет)
        literal_desc = re.sub(r'\*\*(.+?)\*\*', r'\1', literal_desc)  # **bold** → bold
        literal_desc = re.sub(r'^\*\s{2,}', '- ', literal_desc)  # *   item → - item (markdown list)
        literal_desc = re.sub(r'\\n\*\s{2,}', r'\\n- ', literal_desc)  # \n*   item → \n- item
        literal_desc = literal_desc.replace('\\n*   ', '\\n- ')  # literal \n*   → \n- 
        
        # ПРОБЛЕМА 1: Валидация длины описания (лимит Instagram 2000 символов)
        if len(literal_desc) > 2000:
            log_callback(f"   ⚠️ Описание обрезано: {len(literal_desc)} → 2000 символов")
            literal_desc = literal_desc[:1997]
            # Находим последний пробел чтобы не разрывать слово
            last_space = literal_desc.rfind(' ')
            if last_space > 1900:
                literal_desc = literal_desc[:last_space]
            literal_desc += "..."
        
        existing_descs[video_num - 1] = literal_desc
        
        try:
            with open(descriptions_file, 'w', encoding='utf-8') as f:
                for d in existing_descs:
                    f.write(f"{d}\n")
        except Exception as e:
            log_callback(f"   ⚠️ Ошибка сохранения файла описаний: {e}")
                
        # --- 3. ХЕШТЕГИ (1 строка = все хештеги для 1 видео, без переносов) ---
        existing_hashtags = []
        try:
            if hashtags_file.exists():
                with open(hashtags_file, 'r', encoding='utf-8') as f:
                    existing_hashtags = [line.rstrip('\n\r') for line in f.readlines()]
        except Exception as e:
            log_callback(f"   ⚠️ Ошибка чтения файла хештегов: {e}")
            existing_hashtags = []
                
        while len(existing_hashtags) < video_num:
            existing_hashtags.append("")
        
        normalized_tags = _normalize_hashtags(
            list(hashtags or []),
            limit=12,
            require_shorts=False,
        )
        
        # Формируем строку: все хештеги через пробел, БЕЗ переносов
        existing_hashtags[video_num - 1] = ' '.join(normalized_tags)
        
        try:
            with open(hashtags_file, 'w', encoding='utf-8') as f:
                for h in existing_hashtags:
                    f.write(f"{h}\n")
        except Exception as e:
            log_callback(f"   ⚠️ Ошибка сохранения файла хештегов: {e}")
                
        log_callback(f"   📱 Instagram-файлы обновлены (видео #{video_num})")
        
    except Exception as e:
        log_callback(f"   ⚠️ Ошибка сохранения файлов Instagram: {e}")
