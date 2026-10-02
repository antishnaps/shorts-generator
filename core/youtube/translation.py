#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Translation Utilities
Функции для перевода текста через Gemini AI.
"""

import re
import time
import threading
from pathlib import Path
from typing import Dict, Callable


def _dummy_log(msg):
    """Заглушка для логирования."""
    print(msg)


# Кэш переводов для API
_translation_cache: Dict[str, str] = {}
_translation_cache_lock = threading.Lock()
_TRANSLATION_CACHE_FILE = Path("cache/translation_cache.json")

# Загрузка кэша с диска
try:
    if _TRANSLATION_CACHE_FILE.exists():
        import json
        with open(_TRANSLATION_CACHE_FILE, 'r', encoding='utf-8') as f:
            _translation_cache = json.load(f)
except Exception:
    pass

def _save_translation_cache():
    try:
        import json
        _TRANSLATION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = _TRANSLATION_CACHE_FILE.with_suffix(_TRANSLATION_CACHE_FILE.suffix + '.tmp')
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(_translation_cache, f, ensure_ascii=False, indent=2)
        tmp_file.replace(_TRANSLATION_CACHE_FILE)
    except Exception:
        pass


# Словарь переводов для fallback без AI
_TRANSLATION_DICTIONARY = {
    # Еда
    'сыр': 'cheese', 'бри': 'brie', 'сыр бри': 'brie cheese', 'сыр бри де мо': 'brie de meaux cheese',
    'камамбер': 'camembert', 'пармезан': 'parmesan', 'моцарелла': 'mozzarella',
    'пицца': 'pizza', 'паста': 'pasta', 'суши': 'sushi', 'рамен': 'ramen',
    'шоколад': 'chocolate', 'кофе': 'coffee', 'чай': 'tea', 'вино': 'wine',
    'сало': 'pork fat cooking', 'шпик': 'pork bacon', 'мангалица': 'mangalica pig',
    # Технологии
    'искусственный интеллект': 'artificial intelligence', 'нейросеть': 'neural network',
    'программирование': 'programming', 'компьютер': 'computer', 'телефон': 'phone',
    # Животные и природа
    'коты': 'cats', 'кошки': 'cats', 'кошечки': 'cute cats', 'котята': 'kittens',
    'собаки': 'dogs', 'щенки': 'puppies', 'космос': 'space universe',
    # Общее
    'история': 'history', 'производство': 'production', 'как сделать': 'how to make',
    'секрет': 'secret', 'факты': 'facts', 'топ': 'top', 'лучший': 'best',
    'рецепт': 'recipe', 'обзор': 'review', 'сравнение': 'comparison',
}


def is_cyrillic(text: str) -> bool:
    """Проверяет содержит ли текст кириллицу."""
    return bool(re.search(r'[\u0400-\u04FF]', text))


_NON_LATIN_SEARCH_SCRIPT_RE = re.compile(
    r"[\u0370-\u03ff\u0400-\u052f\u0530-\u058f\u0590-\u06ff"
    r"\u0750-\u077f\u0900-\u097f\u0e00-\u0e7f\u10a0-\u10ff"
    r"\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]"
)


def needs_english_search_translation(text: str) -> bool:
    """Return whether a global-footage query needs an English companion.

    This is script-based rather than language-name based, so Korean, Japanese,
    Chinese, Arabic and other non-Latin topics get the same treatment as
    Cyrillic topics without maintaining a topic or language dictionary.
    """

    return bool(_NON_LATIN_SEARCH_SCRIPT_RE.search(str(text or "")))


def translate_to_english_ai(text: str, api_key: str = None, log_callback: Callable = None) -> str:
    """
    Переводит текст на английский через Gemini AI.
    Используется для поиска YouTube.
    
    Args:
        text: Текст для перевода
        api_key: API ключ Gemini (опционально, загружается из config.json)
        log_callback: Функция логирования
        
    Returns:
        Переведенный текст или оригинал
    """
    if log_callback is None:
        log_callback = _dummy_log
    
    cache_key = text.lower().strip()
    curated_translation = _TRANSLATION_DICTIONARY.get(cache_key)
    if curated_translation:
        return curated_translation

    # Проверяем кэш
    with _translation_cache_lock:
        if cache_key in _translation_cache:
            return _translation_cache[cache_key]
    
    # Latin-script queries already work well in global search. Non-Latin
    # scripts receive an English companion through the same bounded Gemini
    # translation path.
    if not needs_english_search_translation(text):
        return text
    
    # Загружаем API ключ из config.json если не передан
    if not api_key:
        try:
            import json
            config_path = Path(__file__).parent.parent.parent / "config.json"
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    api_key = config.get('user_settings', {}).get('gemini_api_key', '')
        except Exception:
            pass
    
    if not api_key:
        log_callback("⚠️ Gemini API ключ не найден, используем транслитерацию")
        return _transliterate(text)
    
    try:
        from core.gemini_models import FAST_TEXT_MODEL
        from core.text_generator import TextGenerator
        
        prompt = f"""Translate this text to English for YouTube video search. 
Keep it short and search-friendly. Only return the translation, nothing else.
If it's a movie/show/game name, use the official English title.

Text: {text}"""
        
        # Retry с exponential backoff
        max_retries = 3
        base_delay = 1.0
        
        for attempt in range(max_retries):
            try:
                # Rate limiting
                time.sleep(0.5)
                
                response = TextGenerator()._rest_generate_content(
                    model=FAST_TEXT_MODEL,
                    api_key=api_key,
                    prompt_text=prompt,
                    generation_config={
                        "temperature": 0.1,
                        "maxOutputTokens": 100,
                    },
                )

                translated = (
                    (response.get("candidates") or [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
                translated = translated.strip('"\'')
                
                # Кэшируем
                with _translation_cache_lock:
                    _translation_cache[cache_key] = translated
                    _save_translation_cache()
                
                log_callback(f"✅ AI перевод: '{text}' → '{translated}'")
                return translated
                
            except Exception as e:
                error_msg = str(e)
                is_429 = '429' in error_msg or 'RESOURCE_EXHAUSTED' in error_msg
                
                if is_429 and attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    log_callback(f"⏳ Gemini перегружен (перевод), повтор через {wait_time}s")
                    time.sleep(wait_time)
                    continue
                else:
                    # Fallback на словарь/транслитерацию
                    raise e
        
    except Exception as e:
        log_callback(f"⚠️ Ошибка AI перевода: {str(e)[:50]}, используем fallback")
        # Пробуем словарь
        fallback = _translate_from_dictionary(text)
        if fallback != text:
            log_callback(f"✅ Словарь: '{text}' → '{fallback}'")
            with _translation_cache_lock:
                _translation_cache[cache_key] = fallback
                _save_translation_cache()
            return fallback
        # Последний fallback - транслитерация
        log_callback("⚠️ Используем транслитерацию")
        return _transliterate(text)


def _translate_from_dictionary(text: str) -> str:
    """
    Переводит текст используя словарь.
    
    Args:
        text: Текст для перевода
        
    Returns:
        Переведенный текст или оригинал
    """
    text_lower = text.lower().strip()
    
    # Прямое совпадение в словаре
    if text_lower in _TRANSLATION_DICTIONARY:
        return _TRANSLATION_DICTIONARY[text_lower]

    # For long generated topics, retain known concrete concepts instead of
    # transliterating the whole sentence when AI translation is unavailable.
    matches = []
    for source, translated in sorted(
        _TRANSLATION_DICTIONARY.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if re.search(rf"(?<!\w){re.escape(source)}(?!\w)", text_lower):
            if translated not in matches:
                matches.append(translated)
        if len(matches) >= 4:
            break
    if matches:
        return " ".join(matches)
    
    # Возвращаем оригинал без изменений
    # (не заменяем отдельные слова, чтобы избежать кракозябр)
    return text


def _transliterate(text: str) -> str:
    """
    Транслитерирует кириллицу в латиницу (fallback).
    
    Args:
        text: Текст для транслитерации
        
    Returns:
        Транслитерированный текст
    """
    translit_map = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'Yo',
        'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M',
        'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
        'Ф': 'F', 'Х': 'Kh', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Shch',
        'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya'
    }
    
    result = []
    for char in text:
        result.append(translit_map.get(char, char))
    
    return ''.join(result)
