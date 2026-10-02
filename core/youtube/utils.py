#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Utils - Утилиты для работы с YouTube
"""

import re
from .constants import TRANSLATION_DICTIONARY


def _dummy_log(msg):
    """Заглушка для логирования"""
    print(msg)


def _parse_duration(iso_duration: str) -> int:
    """
    Парсит ISO 8601 duration в секунды.
    
    Args:
        iso_duration: Строка формата PT1H2M3S
        
    Returns:
        Длительность в секундах
        
    Examples:
        >>> _parse_duration("PT1H2M3S")
        3723
        >>> _parse_duration("PT5M30S")
        330
    """
    if not iso_duration or iso_duration == "P0D":
        return 0
    
    pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
    match = re.match(pattern, iso_duration)
    
    if not match:
        return 0
    
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    
    return hours * 3600 + minutes * 60 + seconds


def is_cyrillic(text: str) -> bool:
    """
    Проверяет содержит ли текст кириллицу.
    
    Args:
        text: Текст для проверки
        
    Returns:
        True если текст содержит кириллицу
        
    Examples:
        >>> is_cyrillic("привет")
        True
        >>> is_cyrillic("hello")
        False
    """
    return bool(re.search(r'[\u0400-\u04FF]', text))


def _transliterate(text: str) -> str:
    """
    Транслитерирует кириллицу в латиницу (fallback).
    
    Args:
        text: Текст для транслитерации
        
    Returns:
        Транслитерированный текст
        
    Examples:
        >>> _transliterate("привет")
        "privet"
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


def _translate_from_dictionary(text: str) -> str:
    """
    Переводит текст используя словарь.
    
    Args:
        text: Текст для перевода
        
    Returns:
        Переведенный текст или оригинал если перевод не найден
        
    Examples:
        >>> _translate_from_dictionary("сыр")
        "cheese"
        >>> _translate_from_dictionary("unknown")
        "unknown"
    """
    text_lower = text.lower().strip()
    
    # Прямое совпадение в словаре
    if text_lower in TRANSLATION_DICTIONARY:
        return TRANSLATION_DICTIONARY[text_lower]
    
    # Возвращаем оригинал без изменений
    return text


def calculate_youtube_duration(
    num_videos: int,
    video_duration_seconds: float,
    video_ratio: float = 0.3
) -> float:
    """
    Рассчитывает необходимую длительность YouTube контента.
    
    Args:
        num_videos: Количество видео для генерации
        video_duration_seconds: Длительность одного видео (секунды)
        video_ratio: Доля YouTube контента (0.0-1.0)
        
    Returns:
        Необходимая длительность YouTube контента в минутах
        
    Examples:
        >>> calculate_youtube_duration(10, 60, 0.3)
        9.0  # 10 видео * 60 сек * 0.3 * 1.5 / 60
    """
    total_content_minutes = (num_videos * video_duration_seconds) / 60
    youtube_minutes_needed = total_content_minutes * video_ratio
    # Добавляем 50% запас
    return youtube_minutes_needed * 1.5

