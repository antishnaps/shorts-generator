#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pixabay API Client
Интеграция с Pixabay для получения бесплатных изображений и видео.

TASK 12: Pixabay/Freesound Integration

Pixabay предоставляет бесплатные изображения и видео под лицензией Pixabay,
которая позволяет использование в коммерческих проектах без указания авторства.

Получить API ключ: https://pixabay.com/api/docs/

Использование:
    >>> client = PixabayClient(api_key='your-key')
    >>> images = client.search_images('космос', per_page=10)
    >>> for img in images:
    ...     path = client.download_image(img, Path('output/'))
    ...     print(f'Downloaded: {path}')
"""

import os
import requests
from pathlib import Path
from typing import List, Dict, Optional, Callable
import hashlib
import json


class PixabayClient:
    """
    Клиент для работы с Pixabay API.
    
    Поддерживает:
    - Поиск изображений по запросу
    - Поиск видео по запросу
    - Фильтрация по ориентации, цвету, типу
    - Кэширование результатов поиска
    - Скачивание медиа файлов
    
    Attributes:
        api_key: API ключ Pixabay
        session: HTTP сессия для запросов
        _search_cache: Кэш результатов поиска
    """
    
    BASE_URL = "https://pixabay.com/api/"
    VIDEO_URL = "https://pixabay.com/api/videos/"
    
    # Кэш для результатов поиска
    _search_cache: Dict[str, List[Dict]] = {}
    
    def __init__(self, api_key: str = None, log_callback: Callable = None):
        """
        Инициализация клиента.
        
        Args:
            api_key: API ключ Pixabay (или из переменной окружения PIXABAY_API_KEY)
            log_callback: Функция для логирования
        """
        self.api_key = api_key or os.environ.get('PIXABAY_API_KEY', '')
        self.log = log_callback or print
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'ShortsGenerator/1.0'
        })
    
    def _get_cache_key(self, query: str, media_type: str, **kwargs) -> str:
        """Генерирует ключ кэша для запроса."""
        key_data = f"{query}_{media_type}_{json.dumps(kwargs, sort_keys=True)}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def search_images(
        self,
        query: str,
        per_page: int = 20,
        image_type: str = "photo",
        orientation: str = "all",
        min_width: int = 0,
        min_height: int = 0,
        colors: str = None,
        safesearch: bool = True
    ) -> List[Dict]:
        """
        Поиск изображений на Pixabay.
        
        Args:
            query: Поисковый запрос
            per_page: Количество результатов (3-200)
            image_type: Тип изображения (all, photo, illustration, vector)
            orientation: Ориентация (all, horizontal, vertical)
            min_width: Минимальная ширина
            min_height: Минимальная высота
            colors: Цвета (grayscale, transparent, red, orange, yellow, green, turquoise, blue, lilac, pink, white, gray, black, brown)
            safesearch: Безопасный поиск
            
        Returns:
            Список изображений с метаданными
        """
        if not self.api_key:
            self.log("⚠️ Pixabay API ключ не задан")
            return []
        
        # Проверяем кэш
        cache_key = self._get_cache_key(query, "image", per_page=per_page, 
                                        image_type=image_type, orientation=orientation)
        if cache_key in self._search_cache:
            self.log(f"📦 Pixabay: результаты из кэша для '{query}'")
            return self._search_cache[cache_key]
        
        params = {
            'key': self.api_key,
            'q': query,
            'per_page': min(200, max(3, per_page)),
            'image_type': image_type,
            'orientation': orientation,
            'safesearch': str(safesearch).lower(),
            'min_width': min_width,
            'min_height': min_height
        }
        
        if colors:
            params['colors'] = colors
        
        try:
            self.log(f"🔍 Pixabay: поиск изображений '{query}'...")
            response = self.session.get(self.BASE_URL, params=params, timeout=30, stream=False)  # Python 3.14 fix
            response.raise_for_status()
            
            data = response.json()
            hits = data.get('hits', [])
            
            self.log(f"✅ Pixabay: найдено {len(hits)} изображений")
            
            # Кэшируем результаты
            self._search_cache[cache_key] = hits
            
            return hits
            
        except requests.RequestException as e:
            self.log(f"❌ Pixabay ошибка: {e}")
            return []
    
    def search_videos(
        self,
        query: str,
        per_page: int = 20,
        video_type: str = "all",
        min_width: int = 0,
        min_height: int = 0,
        safesearch: bool = True
    ) -> List[Dict]:
        """
        Поиск видео на Pixabay.
        
        Args:
            query: Поисковый запрос
            per_page: Количество результатов (3-200)
            video_type: Тип видео (all, film, animation)
            min_width: Минимальная ширина
            min_height: Минимальная высота
            safesearch: Безопасный поиск
            
        Returns:
            Список видео с метаданными
        """
        if not self.api_key:
            self.log("⚠️ Pixabay API ключ не задан")
            return []
        
        # Проверяем кэш
        cache_key = self._get_cache_key(query, "video", per_page=per_page, video_type=video_type)
        if cache_key in self._search_cache:
            self.log(f"📦 Pixabay: видео из кэша для '{query}'")
            return self._search_cache[cache_key]
        
        params = {
            'key': self.api_key,
            'q': query,
            'per_page': min(200, max(3, per_page)),
            'video_type': video_type,
            'safesearch': str(safesearch).lower(),
            'min_width': min_width,
            'min_height': min_height
        }
        
        try:
            self.log(f"🔍 Pixabay: поиск видео '{query}'...")
            response = self.session.get(self.VIDEO_URL, params=params, timeout=30, stream=False)  # Python 3.14 fix
            response.raise_for_status()
            
            data = response.json()
            hits = data.get('hits', [])
            
            self.log(f"✅ Pixabay: найдено {len(hits)} видео")
            
            # Кэшируем результаты
            self._search_cache[cache_key] = hits
            
            return hits
            
        except requests.RequestException as e:
            self.log(f"❌ Pixabay ошибка: {e}")
            return []
    
    def download_image(
        self,
        image_data: Dict,
        output_dir: Path,
        size: str = "largeImageURL"
    ) -> Optional[str]:
        """
        Скачивает изображение.
        
        Args:
            image_data: Данные изображения из search_images()
            output_dir: Папка для сохранения
            size: Размер (previewURL, webformatURL, largeImageURL, fullHDURL)
            
        Returns:
            Путь к скачанному файлу или None
        """
        url = image_data.get(size) or image_data.get('largeImageURL')
        if not url:
            return None
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Генерируем имя файла
        image_id = image_data.get('id', 'unknown')
        ext = url.split('.')[-1].split('?')[0]
        filename = f"pixabay_{image_id}.{ext}"
        output_path = output_dir / filename
        
        if output_path.exists():
            return str(output_path)
        
        try:
            response = self.session.get(url, timeout=60, stream=False)  # Python 3.14 fix
            response.raise_for_status()
            
            with open(output_path, 'wb') as f:
                f.write(response.content)
            
            self.log(f"📥 Скачано: {filename}")
            return str(output_path)
            
        except Exception as e:
            self.log(f"❌ Ошибка скачивания: {e}")
            return None
    
    def download_video(
        self,
        video_data: Dict,
        output_dir: Path,
        quality: str = "small"
    ) -> Optional[str]:
        """
        Скачивает видео.
        
        Args:
            video_data: Данные видео из search_videos()
            output_dir: Папка для сохранения
            quality: Предпочтительный профиль (large, medium, small, tiny).
                ``small`` у Pixabay — полноценные 1080p и лучше подходит для
                FHD Shorts, чем тяжёлый 4K-исходник.
            
        Returns:
            Путь к скачанному файлу или None
        """
        video_info = self.choose_video_file(video_data, quality)
        
        if not video_info:
            return None
        
        url = video_info.get('url')
        if not url:
            return None
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        video_id = video_data.get('id', 'unknown')
        filename = f"pixabay_video_{video_id}.mp4"
        output_path = output_dir / filename
        
        if output_path.exists() and output_path.stat().st_size > 0:
            return str(output_path)

        temporary_path = output_path.with_suffix(output_path.suffix + '.part')
        
        try:
            self.log(f"📥 Скачивание видео {video_id}...")
            response = self.session.get(url, timeout=120, stream=True)
            response.raise_for_status()
            
            with open(temporary_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            if not temporary_path.exists() or temporary_path.stat().st_size <= 0:
                raise ValueError('скачан пустой файл')
            temporary_path.replace(output_path)
            
            self.log(f"✅ Видео скачано: {filename}")
            return str(output_path)
            
        except Exception as e:
            self.log(f"❌ Ошибка скачивания видео: {e}")
            temporary_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            return None

    @staticmethod
    def choose_video_file(video_data: Dict, quality: str = "small") -> Optional[Dict]:
        """Return a HD Pixabay profile without needlessly preferring 4K."""
        videos = video_data.get('videos', {})
        preferred_order = list(
            dict.fromkeys([quality, 'small', 'medium', 'large', 'tiny'])
        )
        return next(
            (
                videos.get(name)
                for name in preferred_order
                if videos.get(name)
                and videos[name].get('url')
                and int(videos[name].get('height') or 0) >= 720
            ),
            None,
        )
    
    def clear_cache(self):
        """Очищает кэш поиска"""
        self._search_cache.clear()
        self.log("🗑️ Pixabay кэш очищен")


def get_pixabay_images_for_theme(
    theme: str,
    count: int = 10,
    api_key: str = None,
    output_dir: Path = None,
    orientation: str = "all",
    log_callback: Callable = None
) -> List[str]:
    """
    Удобная функция для получения изображений по теме.
    
    Args:
        theme: Тема для поиска
        count: Количество изображений
        api_key: API ключ
        output_dir: Папка для сохранения
        orientation: Ориентация (all, horizontal, vertical)
        log_callback: Функция логирования
        
    Returns:
        Список путей к скачанным изображениям
    """
    client = PixabayClient(api_key=api_key, log_callback=log_callback)
    
    if output_dir is None:
        output_dir = Path("generated/pixabay_images")
    
    images = client.search_images(
        query=theme,
        per_page=count * 2,  # Запрашиваем больше на случай ошибок
        orientation=orientation,
        min_width=1280,
        min_height=720
    )
    
    downloaded = []
    for img in images[:count]:
        path = client.download_image(img, output_dir)
        if path:
            downloaded.append(path)
        if len(downloaded) >= count:
            break
    
    return downloaded
