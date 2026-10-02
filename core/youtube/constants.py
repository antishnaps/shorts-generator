#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Constants - Константы для работы с YouTube
"""

from pathlib import Path

# ============================================================
# USER AGENTS
# ============================================================

# 🎭 Ротация User-Agent для обхода детекции ботов
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
]

# ============================================================
# COOKIES
# ============================================================

# Cookies необходимые для авторизации на YouTube
AUTH_COOKIES = [
    '__Secure-1PSID',
    '__Secure-3PSID',
    'SID',
    'SAPISID',
    '__Secure-1PAPISID',
    '__Secure-3PAPISID',
    'LOGIN_INFO',
]

# ============================================================
# BLACKLIST
# ============================================================

# Path to blacklist file
BLACKLIST_FILE = Path(__file__).parent.parent.parent / "youtube_blacklist.json"

# ============================================================
# SLIDESHOW DETECTION
# ============================================================

# Ключевые слова для определения слайдшоу
SLIDESHOW_KEYWORDS = [
    'slideshow', 'slide show', 'photo compilation', 'image compilation',
    'pictures', 'photos only', 'photo gallery', 'still images',
    'фото', 'слайдшоу', 'фотографии', 'подборка фото',
    'compilation of photos', 'picture montage', 'image montage',
    'memorial', 'tribute', 'in memory', 'памяти'
]

# 🎬 Каналы которые часто делают слайдшоу (расширенный список)
SLIDESHOW_CHANNELS = [
    'slideshow', 'photo gallery', 'picture gallery', 'still images',
    'слайдшоу', 'фотогалерея', 'фото галерея'
]

# ============================================================
# TRANSLATION
# ============================================================

# Словарь для быстрого перевода часто встречающихся слов
TRANSLATION_DICTIONARY = {
    # Сыры
    'сыр': 'cheese', 'бри': 'brie', 'сыр бри': 'brie cheese', 'сыр бри де мо': 'brie de meaux cheese',
    'камамбер': 'camembert', 'чеддер': 'cheddar', 'пармезан': 'parmesan',
    'моцарелла': 'mozzarella', 'гауда': 'gouda', 'рокфор': 'roquefort',
    
    # Еда
    'еда': 'food', 'блюдо': 'dish', 'рецепт': 'recipe', 'готовка': 'cooking',
    'кухня': 'cuisine', 'ресторан': 'restaurant', 'повар': 'chef',
    
    # Животные
    'кот': 'cat', 'кошка': 'cat', 'собака': 'dog', 'птица': 'bird',
    'рыба': 'fish', 'лошадь': 'horse', 'корова': 'cow',
    
    # Природа
    'лес': 'forest', 'море': 'sea', 'гора': 'mountain', 'река': 'river',
    'озеро': 'lake', 'пляж': 'beach', 'небо': 'sky', 'солнце': 'sun',
    
    # Общее
    'видео': 'video', 'фото': 'photo', 'картинка': 'picture', 'изображение': 'image',
    'музыка': 'music', 'песня': 'song', 'фильм': 'movie', 'игра': 'game'
}
