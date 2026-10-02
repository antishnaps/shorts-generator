#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
УМНАЯ СИСТЕМА ОПТИМИЗАЦИИ УДЕРЖАНИЯ
Автоматически подбирает настройки для максимального retention
"""

from typing import Dict, Any
from dataclasses import dataclass
import random


class EmotionalPacing:
    """
    Система эмоционального пейсинга для видео
    Автоматически подбирает длительность кадров для максимального удержания
    """
    
    # Кривая эмоционального пейсинга для КОРОТКИХ видео (<2 минут)
    # (start_position, end_position, base_duration)
    PACING_CURVE = {
        'hook': (0.0, 0.15, 0.8),       # 0-15%: БЫСТРО (hook)
        'buildup': (0.15, 0.5, 2.0),    # 15-50%: нормально (развитие)
        'climax': (0.5, 0.85, 1.2),     # 50-85%: БЫСТРО (кульминация)
        'resolution': (0.85, 1.0, 2.5)  # 85-100%: медленно (финал)
    }

    # Feed-first curve: roughly the first 6-8 seconds stay visually dense.
    SHORTS_PACING_CURVE = {
        'hook': (0.0, 0.35, 0.8),
        'buildup': (0.35, 0.60, 2.0),
        'climax': (0.60, 0.90, 1.2),
        'resolution': (0.90, 1.0, 2.5),
    }
    
    # 🎭 ДИНАМИЧЕСКАЯ КРИВАЯ для ДЛИННЫХ видео (2+ минуты)
    # Несколько пиков удержания + передышки
    LONG_VIDEO_CURVE = {
        'hook': (0.0, 0.05, 0.6),       # 0-5%: ОЧЕНЬ БЫСТРО (захват)
        'intro': (0.05, 0.15, 1.5),     # 5-15%: нормально (введение)
        'buildup1': (0.15, 0.35, 2.2),  # 15-35%: медленно (детали)
        'climax1': (0.35, 0.45, 0.8),   # 35-45%: БЫСТРО (первый пик)
        'valley': (0.45, 0.55, 2.8),    # 45-55%: медленно (передышка)
        'buildup2': (0.55, 0.75, 1.8),  # 55-75%: нарастание
        'climax2': (0.75, 0.90, 0.7),   # 75-90%: ОЧЕНЬ БЫСТРО (финальный пик)
        'outro': (0.90, 1.0, 2.0)       # 90-100%: медленно (вывод)
    }
    
    @staticmethod
    def get_shot_duration(shot_index: int, total_shots: int, min_duration: float = None, max_duration: float = None, video_duration: float = 60) -> float:
        """
        Получить длительность кадра на основе позиции в видео
        
        Args:
            shot_index: Индекс текущего кадра (0-based)
            total_shots: Общее количество кадров
            min_duration: Минимальная длительность (для рандома)
            max_duration: Максимальная длительность (для рандома)
            video_duration: Общая длительность видео (для выбора кривой)
            
        Returns:
            Длительность кадра в секундах
        """
        if total_shots <= 1:
            return 2.0  # default
        
        # Позиция в видео (0.0 - 1.0)
        position = shot_index / (total_shots - 1)
        
        # 🎭 Выбираем кривую в зависимости от длительности видео
        if video_duration <= 60:
            curve = EmotionalPacing.SHORTS_PACING_CURVE
        elif video_duration >= 120:  # 2+ минуты = длинное видео
            curve = EmotionalPacing.LONG_VIDEO_CURVE
        else:
            curve = EmotionalPacing.PACING_CURVE
        
        # Определяем фазу
        # 🔧 ИСПРАВЛЕНИЕ: Включаем последний кадр (position=1.0) в последнюю фазу
        for phase, (start, end, base_duration) in curve.items():
            # Для последней фазы включаем end (<=), для остальных - строго меньше (<)
            is_last_phase = end >= 1.0
            in_phase = (start <= position < end) or (is_last_phase and position == end)
            
            if in_phase:
                # Если заданы границы (рандомный режим), используем их
                if min_duration and max_duration:
                    # 🔧 ИСПРАВЛЕНИЕ: Нормализуем base_duration в пределах min/max
                    # Вычисляем позицию base_duration относительно диапазона кривой
                    curve_min = min(v[2] for v in curve.values())  # Минимум кривой
                    curve_max = max(v[2] for v in curve.values())  # Максимум кривой
                    
                    if curve_max > curve_min:
                        # Нормализуем base_duration в диапазон 0-1
                        normalized = (base_duration - curve_min) / (curve_max - curve_min)
                        # Применяем к пользовательскому диапазону
                        target_center = min_duration + normalized * (max_duration - min_duration)
                    else:
                        target_center = (min_duration + max_duration) / 2
                    
                    # Добавляем небольшую вариацию ±10%
                    variation = (max_duration - min_duration) * 0.1
                    target_min = max(min_duration, target_center - variation)
                    target_max = min(max_duration, target_center + variation)
                    
                    return random.uniform(target_min, target_max)
                else:
                    # Фиксированная длительность
                    return base_duration
        
        # Fallback (последний кадр) - также учитываем min/max
        last_phase = list(curve.values())[-1]
        base_duration = last_phase[2]
        
        if min_duration and max_duration:
            # Применяем те же правила нормализации
            curve_min = min(v[2] for v in curve.values())
            curve_max = max(v[2] for v in curve.values())
            
            if curve_max > curve_min:
                normalized = (base_duration - curve_min) / (curve_max - curve_min)
                target_center = min_duration + normalized * (max_duration - min_duration)
            else:
                target_center = (min_duration + max_duration) / 2
            
            variation = (max_duration - min_duration) * 0.1
            target_min = max(min_duration, target_center - variation)
            target_max = min(max_duration, target_center + variation)
            
            return random.uniform(target_min, target_max)
        
        return base_duration
    
    @staticmethod
    def get_pacing_profile(total_shots: int, min_duration: float = None, max_duration: float = None, video_duration: float = 60) -> list:
        """
        Получить полный профиль длительностей для всех кадров
        
        Args:
            total_shots: Количество кадров
            min_duration: Минимальная длительность
            max_duration: Максимальная длительность
            video_duration: Общая длительность видео
            
        Returns:
            Список длительностей для каждого кадра
        """
        return [
            EmotionalPacing.get_shot_duration(i, total_shots, min_duration, max_duration, video_duration)
            for i in range(total_shots)
        ]


@dataclass
class RetentionSettings:
    """Настройки для оптимизации удержания"""
    
    # Визуальные эффекты
    shot_duration: float          # Длительность одного кадра (секунды)
    zoom_enabled: bool            # Использовать zoom эффекты
    zoom_intensity: str           # 'low', 'medium', 'high'
    transitions_enabled: bool     # Использовать переходы между кадрами
    transition_style: str         # 'fade', 'swipe', 'zoom', 'random'
    
    # Аудио эффекты
    sfx_enabled: bool             # Звуковые эффекты
    sfx_frequency: str            # 'low', 'medium', 'high'
    music_dynamics: bool          # Динамическая музыка (нарастание, ducking)
    
    # Контент
    use_hook: bool                # Использовать хук в начале
    cliffhanger_interval: int     # Интервал микро-клиффхэнгеров (секунды)
    questions_enabled: bool       # Добавлять вопросы к зрителю
    
    # Субтитры
    subtitle_animation: bool      # Анимированные субтитры
    subtitle_emphasis: bool       # Выделение важных слов
    emoji_enabled: bool           # Эмодзи в субтитрах
    
    # UI элементы
    progress_bar: bool            # Показывать прогресс-бар
    
    # Цветокоррекция
    color_boost: bool             # Усиление цветов
    saturation_boost: float       # Насыщенность (1.0 = без изменений)
    contrast_boost: float         # Контраст (1.0 = без изменений)


class RetentionOptimizer:
    """Умная система оптимизации удержания"""
    
    @staticmethod
    def get_settings(
        width: int,
        height: int,
        duration: int,
        video_type: str = 'auto'
    ) -> RetentionSettings:
        """
        Автоматически определяет оптимальные настройки удержания
        
        Args:
            width: Ширина видео
            height: Высота видео
            duration: Длительность в секундах
            video_type: 'auto', 'vertical', 'horizontal'
            
        Returns:
            RetentionSettings с оптимальными настройками
        """
        
        # Определяем формат
        is_vertical = height > width
        
        # Автоопределение типа видео
        if video_type == 'auto':
            if is_vertical:
                video_type = 'vertical'  # Вертикальные (Shorts, Reels, Stories)
            else:
                video_type = 'horizontal'  # Горизонтальные (YouTube, обычные видео)
        
        # Выбираем профиль настроек
        if video_type == 'vertical':
            return RetentionOptimizer._get_vertical_settings(duration)
        else:
            return RetentionOptimizer._get_horizontal_settings(duration)
    
    @staticmethod
    def _get_vertical_settings(duration: int) -> RetentionSettings:
        """
        АГРЕССИВНЫЙ РЕЖИМ для вертикальных видео
        Цель: Максимальное удержание (Shorts, Reels, Stories)
        """
        return RetentionSettings(
            # Визуальные эффекты - МАКСИМУМ
            shot_duration=1.5,              # Быстрая смена каждые 1.5 сек
            zoom_enabled=True,              # Zoom на каждом кадре
            zoom_intensity='high',          # Активный zoom (1.0 → 1.3)
            transitions_enabled=True,       # Переходы между кадрами
            transition_style='random',      # Разнообразие
            
            # Аудио эффекты - ЧАСТЫЕ
            sfx_enabled=True,               # Звуковые эффекты
            sfx_frequency='high',           # Whoosh при каждой смене
            music_dynamics=True,            # Нарастание к кульминации
            
            # Контент - АГРЕССИВНЫЙ
            use_hook=True,                  # Обязательный хук
            cliffhanger_interval=10,        # Клиффхэнгер каждые 10 сек
            questions_enabled=True,         # Вопросы к зрителю
            
            # Субтитры - АНИМИРОВАННЫЕ
            subtitle_animation=True,        # Слова появляются
            subtitle_emphasis=True,         # Важные слова крупнее
            emoji_enabled=True,             # Эмодзи для акцентов
            
            # UI элементы
            progress_bar=True,              # Показываем прогресс
            
            # Цветокоррекция - ЯРКАЯ
            color_boost=True,               # Усиление цветов
            saturation_boost=1.2,           # +20% насыщенности
            contrast_boost=1.15             # +15% контраста
        )
    
    @staticmethod
    def _get_horizontal_settings(duration: int) -> RetentionSettings:
        """
        СПОКОЙНЫЙ РЕЖИМ для горизонтальных видео
        Цель: Качественное повествование (YouTube, обычные видео)
        """
        return RetentionSettings(
            # Визуальные эффекты - МИНИМАЛЬНЫЕ
            shot_duration=4.0,              # Медленная смена
            zoom_enabled=True,              # Легкий zoom
            zoom_intensity='low',           # Плавный zoom (1.0 → 1.1)
            transitions_enabled=True,       # Плавные переходы
            transition_style='fade',        # Только fade
            
            # Аудио эффекты - РЕДКИЕ
            sfx_enabled=False,              # Без звуковых эффектов
            sfx_frequency='low',            # Минимум
            music_dynamics=False,           # Ровная музыка
            
            # Контент - СПОКОЙНЫЙ
            use_hook=False,                 # Хук опционален
            cliffhanger_interval=60,        # Клиффхэнгер каждую минуту
            questions_enabled=False,        # Без вопросов
            
            # Субтитры - КЛАССИЧЕСКИЕ
            subtitle_animation=False,       # Статичные
            subtitle_emphasis=False,        # Без выделения
            emoji_enabled=False,            # Без эмодзи
            
            # UI элементы
            progress_bar=False,             # Не показываем
            
            # Цветокоррекция - ЕСТЕСТВЕННАЯ
            color_boost=True,               # Легкое усиление
            saturation_boost=1.05,          # +5% насыщенности
            contrast_boost=1.05             # +5% контраста
        )
    
    @staticmethod
    def get_zoom_params(intensity: str, duration: float) -> Dict[str, Any]:
        """
        Получить параметры zoom эффекта
        
        Args:
            intensity: 'low', 'medium', 'high'
            duration: Длительность кадра в секундах
            
        Returns:
            Параметры для ffmpeg zoompan фильтра
        """
        zoom_configs = {
            'low': {
                'zoom_start': 1.0,
                'zoom_end': 1.1,
                'speed': 0.0005
            },
            'medium': {
                'zoom_start': 1.0,
                'zoom_end': 1.2,
                'speed': 0.001
            },
            'high': {
                'zoom_start': 1.0,
                'zoom_end': 1.3,
                'speed': 0.0015
            }
        }
        
        config = zoom_configs.get(intensity, zoom_configs['medium'])
        
        # Рассчитываем количество кадров (30 fps)
        frames = int(duration * 30)
        
        return {
            'zoom_start': config['zoom_start'],
            'zoom_end': config['zoom_end'],
            'speed': config['speed'],
            'frames': frames,
            'filter': f"zoompan=z='min(zoom+{config['speed']},{config['zoom_end']})':d={frames}:s=1080x1920"
        }
    
    @staticmethod
    def get_transition_filter(style: str) -> str:
        """
        Получить ffmpeg фильтр для перехода
        
        Args:
            style: 'fade', 'swipe', 'zoom', 'random'
            
        Returns:
            Строка фильтра для ffmpeg
        """
        transitions = {
            'fade': 'fade',
            'swipe': 'xfade=transition=wipeleft:duration=0.3',
            'zoom': 'xfade=transition=zoomin:duration=0.3',
            'dissolve': 'xfade=transition=dissolve:duration=0.3'
        }
        
        if style == 'random':
            import random
            style = random.choice(['fade', 'swipe', 'zoom', 'dissolve'])
        
        return transitions.get(style, 'fade')
    
    @staticmethod
    def get_sfx_timing(frequency: str, duration: int) -> list:
        """
        Получить тайминги для звуковых эффектов
        
        Args:
            frequency: 'low', 'medium', 'high'
            duration: Длительность видео в секундах
            
        Returns:
            Список временных меток для SFX
        """
        intervals = {
            'low': 10,      # Каждые 10 секунд
            'medium': 5,    # Каждые 5 секунд
            'high': 2       # Каждые 2 секунды
        }
        
        interval = intervals.get(frequency, 5)
        
        # Генерируем тайминги
        timings = []
        current = interval
        while current < duration:
            timings.append(current)
            current += interval
        
        return timings
    
    @staticmethod
    def should_add_cliffhanger(current_time: int, interval: int, duration: int) -> bool:
        """
        Определить нужно ли добавить клиффхэнгер
        
        Args:
            current_time: Текущее время в секундах
            interval: Интервал клиффхэнгеров
            duration: Общая длительность
            
        Returns:
            True если нужен клиффхэнгер
        """
        # Не добавляем в самом начале и конце
        if current_time < 5 or current_time > duration - 10:
            return False
        
        # Проверяем интервал
        return current_time % interval == 0
    
    @staticmethod
    def get_cliffhanger_phrases(language: str = 'Russian') -> list:
        """
        Получить фразы для клиффхэнгеров
        
        Args:
            language: Язык фраз
            
        Returns:
            Список фраз-клиффхэнгеров
        """
        phrases = {
            'Russian': [
                "Но это ещё не всё...",
                "А теперь самое интересное...",
                "Подожди, есть кое-что...",
                "Ты не поверишь что дальше...",
                "И вот тут начинается магия...",
                "Но есть один секрет...",
                "А вот теперь внимание...",
                "Приготовься к сюрпризу...",
                "Это изменит всё...",
                "Сейчас будет бомба..."
            ],
            'English': [
                "But that's not all...",
                "Now here's the interesting part...",
                "Wait, there's more...",
                "You won't believe what's next...",
                "And here's where the magic happens...",
                "But there's one secret...",
                "Now pay attention...",
                "Get ready for a surprise...",
                "This changes everything...",
                "Here comes the bomb..."
            ]
        }
        
        return phrases.get(language, phrases['English'])
