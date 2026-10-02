#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Image caching system V2 - тематическое кэширование для переиспользования

Возможности:
- Кэширование по темам (court_cases, history, science и т.д.)
- Поиск похожих картинок по тегам
- Статистика экономии ($0.039 за картинку)
- Интеграция с custom_images_folder
"""

import hashlib
import json
import shutil
import re
from pathlib import Path
from typing import Optional, Dict, Callable, List, Tuple
from datetime import datetime


# Стоимость генерации одной картинки (Gemini 2.5 Flash)
IMAGE_COST_USD = 0.039


class ImageCache:
    """Cache for generated images with topic-based indexing and tag search"""

    def __init__(self, cache_dir: Path = None, max_age_days: int = 365, 
                 log_callback: Optional[Callable] = None, enabled: bool = True):
        """
        Initialize image cache.
        
        Args:
            cache_dir: Directory to store cache (default: generated/image_cache)
            max_age_days: Maximum age of cached images in days (default: 365 - год)
            log_callback: Optional logging function
            enabled: Enable/disable caching
        """
        if cache_dir is None:
            cache_dir = Path("generated")
        self.cache_dir = Path(cache_dir) / "image_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age_days = max_age_days
        self.log_callback = log_callback or (lambda x: None)
        self.enabled = enabled
        self.index_file = self.cache_dir / "index.json"
        self.index = self._load_index()
        
        # Статистика сессии
        self._session_hits = 0
        self._session_misses = 0

    def _load_index(self) -> Dict:
        """Load the cache index from disk."""
        if self.index_file.exists():
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Миграция старого формата
                    if 'images' not in data:
                        return {'images': data, 'topics': {}, 'stats': {'total_saved_usd': 0.0}}
                    return data
            except Exception:
                return {'images': {}, 'topics': {}, 'stats': {'total_saved_usd': 0.0}}
        return {'images': {}, 'topics': {}, 'stats': {'total_saved_usd': 0.0}}

    def _save_index(self):
        """Save the cache index to disk."""
        try:
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(self.index, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log_callback(f"⚠️ Ошибка сохранения кэша: {e}")

    def _get_prompt_hash(self, prompt: str) -> str:
        """Generate hash of a prompt."""
        return hashlib.md5(prompt.lower().strip().encode()).hexdigest()[:12]
    
    def _extract_tags(self, prompt: str) -> List[str]:
        """Extract tags from prompt for similarity search."""
        # Убираем спецсимволы и разбиваем на слова
        words = re.sub(r'[^\w\s]', ' ', prompt.lower()).split()
        # Фильтруем короткие слова и стоп-слова
        stop_words = {'a', 'an', 'the', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were'}
        tags = [w for w in words if len(w) > 2 and w not in stop_words]
        return list(set(tags))[:20]  # Максимум 20 тегов
    
    def _normalize_topic(self, topic: str) -> str:
        """Normalize topic name for folder."""
        # Убираем спецсимволы, заменяем пробелы на _
        normalized = re.sub(r'[^\w\s-]', '', topic.lower())
        normalized = re.sub(r'\s+', '_', normalized.strip())
        return normalized[:50] or 'general'  # Максимум 50 символов
    
    def _get_topic_dir(self, topic: str) -> Path:
        """Get or create topic directory."""
        topic_name = self._normalize_topic(topic)
        topic_dir = self.cache_dir / topic_name
        topic_dir.mkdir(parents=True, exist_ok=True)
        return topic_dir

    def get(self, prompt: str, width: int, height: int, topic: str = None) -> Optional[Path]:
        """
        Retrieve cached image if available.
        
        Args:
            prompt: Image generation prompt
            width: Image width
            height: Image height
            topic: Optional topic for organized storage
        
        Returns:
            Path to cached image or None
        """
        if not self.enabled:
            return None
            
        key = f"{self._get_prompt_hash(prompt)}_{width}x{height}"
        images = self.index.get('images', {})
        
        if key not in images:
            self._session_misses += 1
            return None

        cache_entry = images[key]
        cached_path = Path(cache_entry['path'])

        # Check if file exists
        if not cached_path.exists():
            del images[key]
            self._save_index()
            self._session_misses += 1
            return None

        # Check age (default 365 days)
        cache_time = datetime.fromisoformat(cache_entry['timestamp'])
        if (datetime.now() - cache_time).days > self.max_age_days:
            cached_path.unlink()
            del images[key]
            self._save_index()
            self._session_misses += 1
            return None

        self._session_hits += 1
        # Обновляем статистику экономии
        self.index['stats']['total_saved_usd'] = self.index.get('stats', {}).get('total_saved_usd', 0) + IMAGE_COST_USD
        self._save_index()
        
        self.log_callback(f"✅ Кэш: {cached_path.name} (сэкономлено ${IMAGE_COST_USD:.3f})")
        return cached_path
    
    def find_similar(self, prompt: str, topic: str = None, min_match: int = 3) -> List[Tuple[Path, float]]:
        """
        Find similar images by tags.
        
        Args:
            prompt: Search prompt
            topic: Optional topic to search in
            min_match: Minimum matching tags
            
        Returns:
            List of (path, similarity_score) sorted by score
        """
        if not self.enabled:
            return []
            
        search_tags = set(self._extract_tags(prompt))
        if not search_tags:
            return []
        
        results = []
        images = self.index.get('images', {})
        
        for key, entry in images.items():
            # Фильтр по теме если указана
            if topic and entry.get('topic') != self._normalize_topic(topic):
                continue
                
            cached_path = Path(entry['path'])
            if not cached_path.exists():
                continue
                
            entry_tags = set(entry.get('tags', []))
            matching = len(search_tags & entry_tags)
            
            if matching >= min_match:
                score = matching / max(len(search_tags), len(entry_tags))
                results.append((cached_path, score))
        
        # Сортируем по score (больше = лучше)
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:10]  # Топ 10

    def put(self, prompt: str, width: int, height: int, image_path: Path, topic: str = None):
        """
        Cache an image.
        
        Args:
            prompt: Image generation prompt
            width: Image width
            height: Image height
            image_path: Path to the generated image
            topic: Optional topic for organized storage
        """
        if not self.enabled:
            return
            
        key = f"{self._get_prompt_hash(prompt)}_{width}x{height}"
        tags = self._extract_tags(prompt)
        topic_name = self._normalize_topic(topic) if topic else 'general'
        
        # Копируем в папку темы
        topic_dir = self._get_topic_dir(topic_name)
        cache_filename = f"{key}_{Path(image_path).stem}{Path(image_path).suffix}"
        cache_path = topic_dir / cache_filename
        
        try:
            shutil.copy2(image_path, cache_path)
        except Exception as e:
            self.log_callback(f"⚠️ Ошибка кэширования: {e}")
            return
        
        images = self.index.get('images', {})
        images[key] = {
            'prompt_hash': self._get_prompt_hash(prompt),
            'prompt_preview': prompt[:100],  # Первые 100 символов для отладки
            'width': width,
            'height': height,
            'path': str(cache_path),
            'topic': topic_name,
            'tags': tags,
            'timestamp': datetime.now().isoformat()
        }
        self.index['images'] = images
        
        # Обновляем статистику тем
        topics = self.index.get('topics', {})
        if topic_name not in topics:
            topics[topic_name] = {'count': 0, 'size_mb': 0}
        topics[topic_name]['count'] += 1
        self.index['topics'] = topics
        
        self._save_index()
        self.log_callback(f"💾 Кэш: {cache_path.name} [{topic_name}]")

    def clear_old(self):
        """Remove cached images older than max_age_days."""
        now = datetime.now()
        expired = []
        images = self.index.get('images', {})
        
        for key, entry in images.items():
            cache_time = datetime.fromisoformat(entry['timestamp'])
            if (now - cache_time).days > self.max_age_days:
                expired.append(key)

        for key in expired:
            try:
                cached_path = Path(images[key]['path'])
                if cached_path.exists():
                    cached_path.unlink()
                del images[key]
                self.log_callback(f"🗑️ Удален старый кэш: {cached_path.name}")
            except Exception as e:
                self.log_callback(f"⚠️ Ошибка удаления кэша: {e}")

        if expired:
            self.index['images'] = images
            self._save_index()
    
    def clear_topic(self, topic: str):
        """Clear all images for a specific topic."""
        topic_name = self._normalize_topic(topic)
        topic_dir = self.cache_dir / topic_name
        
        if topic_dir.exists():
            shutil.rmtree(topic_dir)
            self.log_callback(f"🗑️ Очищена тема: {topic_name}")
        
        # Удаляем из индекса
        images = self.index.get('images', {})
        to_delete = [k for k, v in images.items() if v.get('topic') == topic_name]
        for key in to_delete:
            del images[key]
        
        # Удаляем из статистики тем
        topics = self.index.get('topics', {})
        if topic_name in topics:
            del topics[topic_name]
        
        self.index['images'] = images
        self.index['topics'] = topics
        self._save_index()
    
    def clear_all(self):
        """Clear entire cache."""
        # Удаляем все папки тем
        for item in self.cache_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
        
        self.index = {'images': {}, 'topics': {}, 'stats': {'total_saved_usd': 0.0}}
        self._save_index()
        self.log_callback("🗑️ Весь кэш картинок очищен")

    def get_stats(self) -> Dict:
        """Get cache statistics."""
        images = self.index.get('images', {})
        topics = self.index.get('topics', {})
        stats = self.index.get('stats', {})
        
        total_size = 0
        valid_count = 0
        
        for entry in images.values():
            path = Path(entry['path'])
            if path.exists():
                total_size += path.stat().st_size
                valid_count += 1
        
        return {
            'cached_images': valid_count,
            'cache_size_mb': round(total_size / (1024 * 1024), 2),
            'topics': list(topics.keys()),
            'topics_count': {k: v.get('count', 0) for k, v in topics.items()},
            'total_saved_usd': round(stats.get('total_saved_usd', 0), 2),
            'session_hits': self._session_hits,
            'session_misses': self._session_misses,
            'hit_rate': round(self._session_hits / max(1, self._session_hits + self._session_misses) * 100, 1),
            'cache_dir': str(self.cache_dir)
        }
    
    def get_topic_images(self, topic: str) -> List[Dict]:
        """Get all images for a topic."""
        topic_name = self._normalize_topic(topic)
        images = self.index.get('images', {})
        
        result = []
        for key, entry in images.items():
            if entry.get('topic') == topic_name:
                path = Path(entry['path'])
                if path.exists():
                    result.append({
                        'path': str(path),
                        'prompt_preview': entry.get('prompt_preview', ''),
                        'tags': entry.get('tags', []),
                        'timestamp': entry.get('timestamp', '')
                    })
        return result


# Глобальный экземпляр кэша (singleton)
_global_cache: Optional[ImageCache] = None


def get_image_cache(cache_dir: Path = None, log_callback: Callable = None, enabled: bool = True) -> ImageCache:
    """Get or create global image cache instance."""
    global _global_cache
    
    if _global_cache is None:
        _global_cache = ImageCache(
            cache_dir=cache_dir,
            log_callback=log_callback,
            enabled=enabled
        )
    elif log_callback:
        _global_cache.log_callback = log_callback
    
    _global_cache.enabled = enabled
    return _global_cache


def reset_image_cache():
    """Reset global cache instance."""
    global _global_cache
    _global_cache = None
