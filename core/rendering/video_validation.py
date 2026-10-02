#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Video validation utilities for VideoRenderer.

Модуль для валидации настроек видео и проверки ресурсов перед рендерингом.
"""

import shutil
import logging
from pathlib import Path
from typing import Dict, Any, Callable

from core.process_registry import run_registered


class VideoValidator:
    """
    Класс для валидации видео настроек и проверки ресурсов.
    
    Возможности:
    - Валидация video_settings (разрешение, fps, shot_min/max)
    - Проверка свободного места на диске
    - Получение длительности медиафайлов с кэшированием
    - Очистка кэша ffprobe
    """
    
    # Константы для валидации
    MIN_FPS = 1
    MAX_FPS = 120
    MIN_RESOLUTION = 64
    MAX_RESOLUTION = 7680  # 8K
    
    def __init__(self):
        self._ffprobe_cache = {}  # Кэш результатов ffprobe
    
    def get_duration(self, file_path: str) -> float:
        """
        Получение длительности файла с кэшированием.
        
        Args:
            file_path: Путь к медиафайлу
            
        Returns:
            Длительность в секундах или 0.0 при ошибке
        """
        # Нормализуем путь для кэша
        normalized_path = str(Path(file_path).resolve())
        
        # Проверяем кэш
        if normalized_path in self._ffprobe_cache:
            return self._ffprobe_cache[normalized_path]
        
        try:
            probe_cmd = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                file_path
            ]
            result = run_registered(
                probe_cmd, 
                label="ffprobe_video_validation",
                capture_output=True, 
                text=True, 
                encoding='utf-8', 
                errors='ignore', 
                timeout=30
            )
            
            if result.returncode == 0 and result.stdout.strip():
                duration = float(result.stdout.strip())
                self._ffprobe_cache[normalized_path] = duration
                return duration
        except Exception as e:
            logging.debug(f"ffprobe ошибка для {file_path}: {e}")
        
        return 0.0
    
    def clear_ffprobe_cache(self):
        """Очистка кэша ffprobe."""
        self._ffprobe_cache.clear()
    
    def validate_video_settings(self, video_settings: Dict[str, Any], 
                               log_callback: Callable = None) -> None:
        """
        Валидация video_settings перед рендерингом.
        
        Args:
            video_settings: Словарь с настройками видео
            log_callback: Функция для логирования
            
        Raises:
            ValueError: Если настройки невалидны
        """
        if log_callback is None:
            log_callback = lambda x: None
        
        # Проверка обязательных полей и конвертация resolution
        if 'resolution' in video_settings and ('width' not in video_settings or 'height' not in video_settings):
            # Конвертируем resolution tuple в width/height
            width, height = video_settings['resolution']
            video_settings['width'] = width
            video_settings['height'] = height
        
        required_fields = ['width', 'height', 'fps']
        for field in required_fields:
            if field not in video_settings:
                raise ValueError(f"Отсутствует обязательное поле: {field}")
        
        width = video_settings['width']
        height = video_settings['height']
        fps = video_settings['fps']
        
        # Валидация fps
        if not isinstance(fps, (int, float)) or fps <= 0:
            raise ValueError(f"Некорректный fps: {fps}. Должен быть положительным числом.")
        if fps < self.MIN_FPS or fps > self.MAX_FPS:
            log_callback(f"⚠️ FPS {fps} вне рекомендуемого диапазона ({self.MIN_FPS}-{self.MAX_FPS})")
        
        # Валидация разрешения
        if not isinstance(width, int) or width < self.MIN_RESOLUTION or width > self.MAX_RESOLUTION:
            raise ValueError(f"Некорректная ширина: {width}. Допустимо: {self.MIN_RESOLUTION}-{self.MAX_RESOLUTION}")
        if not isinstance(height, int) or height < self.MIN_RESOLUTION or height > self.MAX_RESOLUTION:
            raise ValueError(f"Некорректная высота: {height}. Допустимо: {self.MIN_RESOLUTION}-{self.MAX_RESOLUTION}")
        
        # Валидация shot_min/shot_max
        shot_min = video_settings.get('shot_min', 3)
        shot_max = video_settings.get('shot_max', 5)
        if shot_min > shot_max:
            log_callback(f"⚠️ shot_min ({shot_min}) > shot_max ({shot_max}), меняем местами")
            video_settings['shot_min'], video_settings['shot_max'] = shot_max, shot_min
    
    def check_disk_space(self, output_path: Path, log_callback: Callable = None, 
                        min_gb: float = 2.0) -> None:
        """
        Проверка свободного места на диске перед рендерингом.
        
        Args:
            output_path: Путь к выходному файлу
            log_callback: Функция для логирования
            min_gb: Минимальное требуемое место в GB
            
        Raises:
            Exception: Если места недостаточно
        """
        if log_callback is None:
            log_callback = lambda x: None
        
        try:
            output_dir = Path(output_path).parent
            if not output_dir.exists():
                output_dir.mkdir(parents=True, exist_ok=True)
            
            disk_usage = shutil.disk_usage(output_dir)
            free_gb = disk_usage.free / (1024 ** 3)
            
            if free_gb < min_gb:
                raise Exception(
                    f"Недостаточно места на диске: {free_gb:.1f} GB свободно, "
                    f"требуется минимум {min_gb} GB для рендеринга"
                )
            elif free_gb < min_gb * 2:
                log_callback(f"⚠️ Мало места на диске: {free_gb:.1f} GB свободно")
            else:
                logging.debug(f"Свободное место: {free_gb:.1f} GB")
                
        except Exception as e:
            if "Недостаточно места" in str(e):
                raise
            # Игнорируем другие ошибки проверки места
            logging.debug(f"Не удалось проверить место на диске: {e}")
