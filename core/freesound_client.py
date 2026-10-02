#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Freesound API Client
Интеграция с Freesound для получения бесплатных звуковых эффектов и музыки.

TASK 12: Pixabay/Freesound Integration

Freesound - крупнейшая база звуков под лицензиями Creative Commons.
Для безопасного использования в создаваемых роликах клиент принимает только CC0.

Получить API ключ: https://freesound.org/apiv2/apply/

Использование:
    >>> client = FreesoundClient(api_key='your-key')
    >>> # Поиск по настроению
    >>> sounds = client.search_by_mood('dramatic', duration_min=30, duration_max=120)
    >>> # Поиск музыки
    >>> music = client.search_music('epic cinematic', duration_min=60)
    >>> # Поиск звуковых эффектов
    >>> sfx = client.search_sfx('whoosh', duration_max=5)
    >>> # Скачивание
    >>> path = client.download_sound(sounds[0], Path('output/'))
"""

import os
import requests
import sys
from pathlib import Path
from typing import List, Dict, Optional, Callable
import hashlib
import json


class FreesoundClient:
    """
    Клиент для работы с Freesound API.
    
    Поддерживает:
    - Поиск звуков по запросу
    - Поиск по настроению (happy, sad, dramatic, calm, energetic, mysterious, romantic)
    - Поиск фоновой музыки
    - Поиск звуковых эффектов (SFX)
    - Фильтрация по длительности
    - Кэширование результатов
    - Скачивание preview (без OAuth) или полных файлов
    
    Attributes:
        api_key: API ключ Freesound
        session: HTTP сессия для запросов
        MOOD_TAGS: Маппинг настроений на теги Freesound
        _search_cache: Кэш результатов поиска
    """
    
    BASE_URL = "https://freesound.org/apiv2"
    CC0_LICENSE = "Creative Commons 0"
    CC0_FILTER = 'license:"Creative Commons 0"'
    
    # Маппинг настроений на теги Freesound
    MOOD_TAGS = {
        'happy': ['happy', 'upbeat', 'cheerful', 'joyful', 'positive'],
        'sad': ['sad', 'melancholic', 'emotional', 'somber'],
        'dramatic': ['dramatic', 'epic', 'cinematic', 'intense', 'tension'],
        'calm': ['calm', 'peaceful', 'relaxing', 'ambient', 'meditation'],
        'energetic': ['energetic', 'action', 'fast', 'dynamic', 'powerful'],
        'mysterious': ['mysterious', 'dark', 'suspense', 'eerie', 'horror'],
        'romantic': ['romantic', 'love', 'soft', 'gentle'],
        'neutral': ['background', 'ambient', 'atmospheric']
    }
    
    # Кэш для результатов поиска
    _search_cache: Dict[str, List[Dict]] = {}

    @staticmethod
    def _default_log(message: str) -> None:
        """Log safely when Windows' active console encoding cannot print emoji."""
        try:
            print(message)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe_message = str(message).encode(encoding, errors="replace").decode(encoding)
            print(safe_message)
    
    def __init__(self, api_key: str = None, log_callback: Callable = None):
        """
        Инициализация клиента.
        
        Args:
            api_key: API ключ Freesound (или из переменной окружения FREESOUND_API_KEY)
            log_callback: Функция для логирования
        """
        self.api_key = api_key or os.environ.get('FREESOUND_API_KEY', '')
        self.log = log_callback or self._default_log
        self.session = requests.Session()
    
    def _get_cache_key(self, query: str, **kwargs) -> str:
        """Генерирует ключ кэша"""
        key_data = f"{query}_{json.dumps(kwargs, sort_keys=True)}"
        return hashlib.md5(key_data.encode()).hexdigest()

    @classmethod
    def is_cc0_sound(cls, sound_data: Dict) -> bool:
        """Keep automatic downloads free of attribution and NC restrictions."""
        license_value = str(sound_data.get('license') or '').strip().lower().rstrip('/')
        return license_value in {
            cls.CC0_LICENSE.lower(),
            'http://creativecommons.org/publicdomain/zero/1.0',
            'https://creativecommons.org/publicdomain/zero/1.0',
        }

    @classmethod
    def _with_cc0_filter(cls, filter_params: str = None) -> str:
        filters = [str(filter_params or '').strip(), cls.CC0_FILTER]
        return ' '.join(part for part in filters if part)
    
    def search_sounds(
        self,
        query: str,
        page_size: int = 15,
        filter_params: str = None,
        sort: str = "rating_desc",
        fields: str = "id,name,tags,description,duration,license,previews,download"
    ) -> List[Dict]:
        """
        Поиск звуков на Freesound.
        
        Args:
            query: Поисковый запрос
            page_size: Количество результатов (1-150)
        filter_params: Дополнительные фильтры (например, "duration:[1 TO 30]").
            CC0 добавляется автоматически.
            sort: Сортировка (score, duration_desc, duration_asc, created_desc, created_asc, downloads_desc, downloads_asc, rating_desc, rating_asc)
            fields: Поля для возврата
            
        Returns:
            Список звуков с метаданными
        """
        if not self.api_key:
            self.log("⚠️ Freesound API ключ не задан")
            return []
        
        # Проверяем кэш
        safe_filter = self._with_cc0_filter(filter_params)
        cache_key = self._get_cache_key(query, page_size=page_size, filter_params=safe_filter)
        if cache_key in self._search_cache:
            self.log(f"📦 Freesound: результаты из кэша для '{query}'")
            return self._search_cache[cache_key]
        
        params = {
            'token': self.api_key,
            'query': query,
            'page_size': min(150, max(1, page_size)),
            'sort': sort,
            'fields': fields
        }
        
        params['filter'] = safe_filter
        
        try:
            self.log(f"🔍 Freesound: поиск '{query}'...")
            response = self.session.get(
                f"{self.BASE_URL}/search/text/",
                params=params,
                timeout=30
            )
            response.raise_for_status()
            
            data = response.json()
            results = data.get('results', [])
            
            self.log(f"✅ Freesound: найдено {len(results)} звуков")
            
            # Кэшируем результаты
            self._search_cache[cache_key] = results
            
            return results
            
        except requests.RequestException as e:
            self.log(f"❌ Freesound ошибка: {e}")
            return []
    
    def search_by_mood(
        self,
        mood: str,
        duration_min: float = 5,
        duration_max: float = 60,
        page_size: int = 10
    ) -> List[Dict]:
        """
        Поиск звуков по настроению.
        
        Args:
            mood: Настроение (happy, sad, dramatic, calm, energetic, mysterious, romantic, neutral)
            duration_min: Минимальная длительность в секундах
            duration_max: Максимальная длительность в секундах
            page_size: Количество результатов
            
        Returns:
            Список звуков
        """
        tags = self.MOOD_TAGS.get(mood.lower(), self.MOOD_TAGS['neutral'])
        query = ' OR '.join(tags)
        
        filter_params = f"duration:[{duration_min} TO {duration_max}]"
        
        return self.search_sounds(
            query=query,
            page_size=page_size,
            filter_params=filter_params
        )
    
    def search_music(
        self,
        query: str = "background music",
        duration_min: float = 30,
        duration_max: float = 300,
        page_size: int = 10
    ) -> List[Dict]:
        """
        Поиск фоновой музыки.
        
        Args:
            query: Поисковый запрос
            duration_min: Минимальная длительность
            duration_max: Максимальная длительность
            page_size: Количество результатов
            
        Returns:
            Список музыкальных треков
        """
        filter_params = f"duration:[{duration_min} TO {duration_max}] tag:music"
        
        return self.search_sounds(
            query=query,
            page_size=page_size,
            filter_params=filter_params,
            sort="rating_desc"
        )
    
    def search_sfx(
        self,
        query: str,
        duration_max: float = 10,
        page_size: int = 10
    ) -> List[Dict]:
        """
        Поиск звуковых эффектов.
        
        Args:
            query: Поисковый запрос (например, "whoosh", "explosion", "click")
            duration_max: Максимальная длительность
            page_size: Количество результатов
            
        Returns:
            Список звуковых эффектов
        """
        filter_params = f"duration:[0 TO {duration_max}]"
        
        return self.search_sounds(
            query=query,
            page_size=page_size,
            filter_params=filter_params,
            sort="downloads_desc"
        )
    
    def download_sound(
        self,
        sound_data: Dict,
        output_dir: Path,
        use_preview: bool = True
    ) -> Optional[str]:
        """
        Скачивает звук.
        
        Args:
            sound_data: Данные звука из search_sounds()
            output_dir: Папка для сохранения
            use_preview: Использовать preview (не требует OAuth) или полный файл
            
        Returns:
            Путь к скачанному файлу или None
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if not self.is_cc0_sound(sound_data):
            self.log("⚠️ Freesound: пропуск не-CC0 звука")
            return None
        
        sound_id = sound_data.get('id', 'unknown')
        name = sound_data.get('name', 'sound').replace(' ', '_')[:50]
        
        if use_preview:
            # Preview доступен без OAuth
            previews = sound_data.get('previews', {})
            url = previews.get('preview-hq-mp3') or previews.get('preview-lq-mp3')
            ext = 'mp3'
        else:
            # Полный файл требует OAuth
            url = sound_data.get('download')
            ext = 'wav'
        
        if not url:
            self.log(f"⚠️ URL не найден для звука {sound_id}")
            return None
        
        filename = f"freesound_{sound_id}_{name}.{ext}"
        output_path = output_dir / filename
        
        if output_path.exists():
            return str(output_path)
        
        try:
            # Для preview не нужен токен в URL
            if use_preview:
                response = self.session.get(url, timeout=60)
            else:
                response = self.session.get(
                    url,
                    params={'token': self.api_key},
                    timeout=60
                )
            response.raise_for_status()
            
            with open(output_path, 'wb') as f:
                f.write(response.content)
            
            self.log(f"📥 Скачано: {filename}")
            return str(output_path)
            
        except Exception as e:
            self.log(f"❌ Ошибка скачивания: {e}")
            return None
    
    def clear_cache(self):
        """Очищает кэш поиска"""
        self._search_cache.clear()
        self.log("🗑️ Freesound кэш очищен")


def get_music_for_mood(
    mood: str,
    count: int = 5,
    api_key: str = None,
    output_dir: Path = None,
    duration_min: float = 30,
    duration_max: float = 180,
    log_callback: Callable = None
) -> List[str]:
    """
    Удобная функция для получения музыки по настроению.
    
    Args:
        mood: Настроение (happy, sad, dramatic, calm, energetic, mysterious, romantic, neutral)
        count: Количество треков
        api_key: API ключ
        output_dir: Папка для сохранения
        duration_min: Минимальная длительность
        duration_max: Максимальная длительность
        log_callback: Функция логирования
        
    Returns:
        Список путей к скачанным файлам
    """
    client = FreesoundClient(api_key=api_key, log_callback=log_callback)
    
    if output_dir is None:
        output_dir = Path("generated/freesound_music")
    
    sounds = client.search_by_mood(
        mood=mood,
        duration_min=duration_min,
        duration_max=duration_max,
        page_size=count * 2
    )
    
    downloaded = []
    for sound in sounds[:count]:
        path = client.download_sound(sound, output_dir, use_preview=True)
        if path:
            downloaded.append(path)
        if len(downloaded) >= count:
            break
    
    return downloaded


def get_sfx(
    effect_type: str,
    count: int = 5,
    api_key: str = None,
    output_dir: Path = None,
    log_callback: Callable = None
) -> List[str]:
    """
    Удобная функция для получения звуковых эффектов.
    
    Args:
        effect_type: Тип эффекта (whoosh, explosion, click, notification, etc.)
        count: Количество эффектов
        api_key: API ключ
        output_dir: Папка для сохранения
        log_callback: Функция логирования
        
    Returns:
        Список путей к скачанным файлам
    """
    client = FreesoundClient(api_key=api_key, log_callback=log_callback)
    
    if output_dir is None:
        output_dir = Path("generated/freesound_sfx")
    
    sounds = client.search_sfx(
        query=effect_type,
        duration_max=10,
        page_size=count * 2
    )
    
    downloaded = []
    for sound in sounds[:count]:
        path = client.download_sound(sound, output_dir, use_preview=True)
        if path:
            downloaded.append(path)
        if len(downloaded) >= count:
            break
    
    return downloaded
