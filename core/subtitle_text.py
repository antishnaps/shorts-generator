"""Unicode-aware text units for animated and fallback subtitles."""

from __future__ import annotations

import unicodedata
from typing import Iterable, List, Tuple


def _is_compact_script(character: str) -> bool:
    code = ord(character)
    return (
        0x2E80 <= code <= 0x2FFF  # CJK radicals and punctuation
        or 0x3040 <= code <= 0x30FF  # Hiragana / Katakana
        or 0x31F0 <= code <= 0x31FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF  # CJK unified ideographs
        or 0xAC00 <= code <= 0xD7AF  # Hangul syllables
    )


def _is_unicode_mark(character: str) -> bool:
    return unicodedata.category(character).startswith("M")


def _is_regional_indicator(character: str) -> bool:
    return 0x1F1E6 <= ord(character) <= 0x1F1FF


def _is_virama(character: str) -> bool:
    name = unicodedata.name(character, "")
    return "VIRAMA" in name or "HALANT" in name


def _grapheme_like_units(text: str) -> List[str]:
    units: List[str] = []
    for character in text:
        if character.isspace():
            continue
        if _is_unicode_mark(character) and units:
            units[-1] += character
        elif unicodedata.category(character).startswith("P") and units:
            units[-1] += character
        else:
            units.append(character)
    return units


def split_graphemes(text: str, preserve_whitespace: bool = True) -> List[str]:
    """Split text into display-safe typewriter units without extra packages.

    Combining marks, variation selectors and zero-width-joiner emoji sequences
    stay attached to their base character. Whitespace is attached to the next
    visible unit so the typewriter animation never emits a useless frame whose
    only change is a trailing space.
    """
    normalized = unicodedata.normalize("NFC", str(text or ""))
    units: List[str] = []
    pending_space = ""
    join_next = False

    for character in normalized:
        if character.isspace():
            if preserve_whitespace:
                pending_space += " "
            continue

        code = ord(character)
        is_variation_selector = 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF
        is_modifier = 0x1F3FB <= code <= 0x1F3FF
        is_joiner = character == "\u200d"
        is_regional_pair = bool(units) and _is_regional_indicator(character) and (
            sum(_is_regional_indicator(item) for item in units[-1]) % 2 == 1
        )
        attach = bool(units) and (
            join_next
            or is_joiner
            or is_variation_selector
            or is_modifier
            or is_regional_pair
            or _is_unicode_mark(character)
        )

        if attach:
            units[-1] += pending_space + character
            pending_space = ""
        else:
            units.append(pending_space + character)
            pending_space = ""
        join_next = is_joiner or _is_virama(character)

    if pending_space and units:
        units[-1] += pending_space
    return units


def split_subtitle_units(text: str) -> Tuple[List[str], str]:
    """Return readable animation units and the separator used to rejoin them.

    Space-delimited languages keep natural words. Chinese, Japanese, and
    unspaced Hangul are split into visible character units so an animated
    subtitle cannot collapse into one full-duration static event.
    """
    normalized = unicodedata.normalize("NFC", str(text or ""))
    normalized = " ".join(normalized.split())
    if not normalized:
        return [], " "

    words = normalized.split()
    if len(words) > 1:
        return words, " "
    if any(_is_compact_script(character) for character in normalized):
        return _grapheme_like_units(normalized), ""
    return words, " "


def join_subtitle_units(units: Iterable[str], separator: str = " ") -> str:
    return separator.join(str(unit) for unit in units if str(unit))
