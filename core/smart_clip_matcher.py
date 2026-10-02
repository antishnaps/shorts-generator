#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Smart Clip Matcher
Умный подбор YouTube клипов на основе анализа текста озвучки.

Функции:
- Анализ эмоций и ключевых слов в тексте
- Подбор релевантных клипов по контексту
- Оптимизация порядка клипов для максимального соответствия
"""

import re
from pathlib import Path
from typing import List, Tuple, Optional, Callable
from dataclasses import dataclass
import random


# Импортируем дефолтный логгер из централизованного модуля
from core.logging_utils import get_default_logger
from core.process_registry import run_registered

# Алиас для обратной совместимости
_dummy_log = get_default_logger()


class InsufficientUniqueVisualsError(RuntimeError):
    """Raised instead of silently repeating a visual in one rendered video."""


@dataclass
class TextSegment:
    """Сегмент текста с метаданными"""
    text: str
    index: int
    emotion: str  # neutral, excited, dramatic, funny, serious
    keywords: List[str]
    intensity: float  # 0.0 - 1.0


@dataclass
class ClipMetadata:
    """Метаданные YouTube клипа"""
    path: str
    duration: float
    brightness: float  # 0.0 - 1.0 (тёмный - светлый)
    motion: float  # 0.0 - 1.0 (статичный - динамичный)
    scene_type: str  # action, dialogue, landscape, closeup


class SmartClipMatcher:
    """Умный подбор клипов на основе контекста"""
    
    # Ключевые слова для определения эмоций
    EMOTION_KEYWORDS = {
        'excited': [
            'шок', 'невероятно', 'удивительно', 'вау', 'офигеть', 'круто',
            'amazing', 'incredible', 'wow', 'awesome', 'unbelievable',
            '!', 'ШОК', 'ПРАВДА', 'СЕКРЕТ'
        ],
        'dramatic': [
            'смерть', 'конец', 'трагедия', 'ужас', 'страшно', 'опасно',
            'death', 'end', 'tragedy', 'horror', 'dangerous', 'dark',
            'никогда', 'навсегда', 'последний'
        ],
        'funny': [
            'смешно', 'ржака', 'прикол', 'хаха', 'лол', 'угар',
            'funny', 'lol', 'haha', 'joke', 'hilarious',
            '😂', '🤣', 'комедия'
        ],
        'serious': [
            'важно', 'серьёзно', 'факт', 'правда', 'история', 'наука',
            'important', 'serious', 'fact', 'truth', 'history', 'science',
            'доказано', 'исследование'
        ],
        'action': [
            'бой', 'драка', 'взрыв', 'погоня', 'бежать', 'атака',
            'fight', 'explosion', 'chase', 'run', 'attack', 'battle',
            'война', 'сражение'
        ]
    }
    
    def __init__(self, log_callback: Callable = _dummy_log):
        self.log = log_callback
    
    def analyze_text_segments(self, text_parts: List[str]) -> List[TextSegment]:
        """
        Анализирует сегменты текста и определяет эмоции/ключевые слова.
        
        Args:
            text_parts: Список текстовых сегментов (по одному на шот)
            
        Returns:
            Список TextSegment с метаданными
        """
        segments = []
        
        for i, text in enumerate(text_parts):
            emotion = self._detect_emotion(text)
            keywords = self._extract_keywords(text)
            intensity = self._calculate_intensity(text)
            
            segments.append(TextSegment(
                text=text,
                index=i,
                emotion=emotion,
                keywords=keywords,
                intensity=intensity
            ))
        
        return segments
    
    def _detect_emotion(self, text: str) -> str:
        """Определяет эмоцию текста"""
        
        text_lower = text.lower()
        
        # Подсчитываем совпадения для каждой эмоции
        scores = {}
        for emotion, keywords in self.EMOTION_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw.lower() in text_lower)
            scores[emotion] = score
        
        # Возвращаем эмоцию с максимальным счётом
        if max(scores.values()) > 0:
            return max(scores, key=scores.get)
        
        return 'neutral'
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Извлекает ключевые слова из текста"""
        
        # Убираем пунктуацию и разбиваем на слова
        words = re.findall(r'\b\w+\b', text.lower())
        
        # Фильтруем стоп-слова
        stop_words = {
            'и', 'в', 'на', 'с', 'по', 'для', 'это', 'что', 'как', 'но', 'а',
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'не', 'да', 'нет', 'так', 'же', 'ещё', 'уже', 'только', 'вот'
        }
        
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        return keywords[:5]  # Топ 5 ключевых слов
    
    def _calculate_intensity(self, text: str) -> float:
        """Рассчитывает интенсивность/энергию текста"""
        
        intensity = 0.5  # Базовая интенсивность
        
        # Восклицательные знаки увеличивают интенсивность
        intensity += text.count('!') * 0.1
        
        # КАПС увеличивает интенсивность
        caps_ratio = sum(1 for c in text if c.isupper()) / max(len(text), 1)
        intensity += caps_ratio * 0.3
        
        # Короткие предложения = более динамично
        words = text.split()
        if len(words) < 5:
            intensity += 0.1
        
        return min(1.0, max(0.0, intensity))
    
    def analyze_clips(self, clip_paths: List[str]) -> List[ClipMetadata]:
        """
        Анализирует YouTube клипы для получения метаданных.
        
        Args:
            clip_paths: Пути к видео-клипам
            
        Returns:
            Список ClipMetadata
        """
        clips_metadata = []
        
        for clip_path in clip_paths:
            try:
                metadata = self._analyze_single_clip(clip_path)
                clips_metadata.append(metadata)
            except Exception:
                # Fallback метаданные
                clips_metadata.append(ClipMetadata(
                    path=clip_path,
                    duration=3.0,
                    brightness=0.5,
                    motion=0.5,
                    scene_type='unknown'
                ))
        
        return clips_metadata
    
    def _analyze_single_clip(self, clip_path: str) -> ClipMetadata:
        """Анализирует один клип"""
        
        # Получаем длительность через ffprobe
        duration = 3.0
        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                clip_path
            ]
            result = run_registered(
                cmd,
                label="ffprobe_smart_clip_duration",
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                duration = float(result.stdout.strip())
        except:
            pass
        
        # Анализируем яркость (средняя яркость первого кадра)
        brightness = 0.5
        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'frame=pkt_pts_time',
                '-of', 'csv=p=0',
                '-read_intervals', '%+#1',  # Только первый кадр
                clip_path
            ]
            # Упрощённый анализ - используем имя файла как hint
            filename = Path(clip_path).stem.lower()
            if any(word in filename for word in ['dark', 'night', 'black']):
                brightness = 0.3
            elif any(word in filename for word in ['bright', 'day', 'white']):
                brightness = 0.7
        except:
            pass
        
        # Оценка движения на основе длительности и позиции в видео
        # Короткие клипы обычно более динамичные
        motion = 0.5 + (1.0 - min(duration / 5.0, 1.0)) * 0.3
        
        # Определяем тип сцены (упрощённо)
        scene_type = 'general'
        
        return ClipMetadata(
            path=clip_path,
            duration=duration,
            brightness=brightness,
            motion=motion,
            scene_type=scene_type
        )
    
    def match_clips_to_segments(
        self,
        text_segments: List[TextSegment],
        clips_metadata: List[ClipMetadata],
        youtube_ratio: float = 0.5,
        total_shots: int = None
    ) -> List[Tuple[int, Optional[str]]]:
        """
        Сопоставляет клипы с текстовыми сегментами.
        
        Args:
            text_segments: Анализированные сегменты текста
            clips_metadata: Метаданные клипов
            youtube_ratio: Доля YouTube клипов (0.0 - 1.0)
            total_shots: Общее количество шотов (для правильного расчёта ratio)
            
        Returns:
            Список кортежей (segment_index, clip_path или None для изображения)
        """
        # 🔧 FIX: Используем total_shots для расчёта num_youtube, а не len(text_segments)
        num_segments = len(text_segments)
        base_count = total_shots if total_shots else num_segments
        num_youtube = int(base_count * youtube_ratio)
        
        # Определяем какие сегменты получат YouTube клипы
        # Приоритет: высокая интенсивность, action/excited эмоции
        
        # Сортируем сегменты по приоритету для YouTube
        segment_priorities = []
        for seg in text_segments:
            priority = seg.intensity
            
            # Бонус за определённые эмоции
            if seg.emotion in ['excited', 'action']:
                priority += 0.3
            elif seg.emotion == 'dramatic':
                priority += 0.2
            elif seg.emotion == 'funny':
                priority += 0.1
            
            segment_priorities.append((seg.index, priority))
        
        # Сортируем по приоритету (высокий → низкий)
        segment_priorities.sort(key=lambda x: x[1], reverse=True)
        
        # Выбираем топ N сегментов для YouTube
        youtube_segment_indices = set(idx for idx, _ in segment_priorities[:num_youtube])
        
        # Распределяем клипы
        result = []
        available_clips = list(clips_metadata)
        random.shuffle(available_clips)  # Рандомизируем порядок клипов
        
        clip_index = 0
        for seg in text_segments:
            if seg.index in youtube_segment_indices and clip_index < len(available_clips):
                # Выбираем наиболее подходящий клип
                best_clip = self._find_best_clip(seg, available_clips[clip_index:])
                result.append((seg.index, best_clip.path if best_clip else None))
                clip_index += 1
            else:
                # Изображение
                result.append((seg.index, None))
        
        return result
    
    def _find_best_clip(
        self,
        segment: TextSegment,
        available_clips: List[ClipMetadata]
    ) -> Optional[ClipMetadata]:
        """Находит наиболее подходящий клип для сегмента"""
        
        if not available_clips:
            return None
        
        # Простой scoring
        best_clip = None
        best_score = -1
        
        for clip in available_clips:
            score = 0
            
            # Соответствие интенсивности и движения
            intensity_match = 1.0 - abs(segment.intensity - clip.motion)
            score += intensity_match * 0.5
            
            # Для драматичных сегментов предпочитаем тёмные клипы
            if segment.emotion == 'dramatic':
                score += (1.0 - clip.brightness) * 0.3
            
            # Для весёлых/excited - светлые
            if segment.emotion in ['funny', 'excited']:
                score += clip.brightness * 0.3
            
            if score > best_score:
                best_score = score
                best_clip = clip
        
        return best_clip
    
    def create_smart_mixed_media(
        self,
        text_parts: List[str],
        image_paths: List[str],
        clip_paths: List[str],
        youtube_ratio: float = 0.5,
        total_shots: int = None,
        allow_video_reuse: bool = False,
    ) -> List[Tuple[str, str]]:
        """
        Создаёт умный mixed_media список с оптимальным распределением.
        
        ВАЖНО:
        - повторы отключены по умолчанию
        - при allow_video_reuse скачанный видеопул циклически перемешивается
        - Соблюдается youtube_ratio (например 0.5 = каждый 2-3 шот YouTube)
        - YouTube шоты в начале (первые 1-2 шота) для захвата внимания
        - Равномерное распределение по всей длине видео
        
        Args:
            text_parts: Текстовые сегменты
            image_paths: Пути к изображениям
            clip_paths: Пути к YouTube клипам
            youtube_ratio: Доля YouTube контента (0.0 - 1.0)
            total_shots: Общее количество шотов (если None - используем len(text_parts))
            
        Returns:
            Список кортежей (path, type) для video_renderer
        """
        self.log("🧠 Умный подбор клипов на основе текста...")
        
        # 🔧 КРИТИЧНО: Используем total_shots если передан, иначе len(text_parts)
        num_shots = total_shots if total_shots else len(text_parts)

        # Cache/source merging can pass the same path more than once. Keep one
        # copy and require the final timeline to use every visual at most once.
        clip_paths = list(dict.fromkeys(str(path) for path in clip_paths if path))
        clip_keys = set(clip_paths)
        image_paths = [
            path for path in dict.fromkeys(str(path) for path in image_paths if path)
            if path not in clip_keys
        ]
        if (
            len(clip_paths) + len(image_paths) < num_shots
            and not (allow_video_reuse and clip_paths)
        ):
            raise InsufficientUniqueVisualsError(
                "Недостаточно уникального визуального материала: "
                f"нужно {num_shots} шотов, доступно "
                f"{len(clip_paths)} видео + {len(image_paths)} изображений. "
                "Повторы отключены."
            )

        clip_cycle = []
        image_cycle = []
        semantic_matches = {}
        clip_usage = {str(path): 0 for path in clip_paths}
        last_clip = None
        try:
            from core.visual_relevance import get_clip_visual_match, script_fingerprint

            narration_fp = script_fingerprint(text_parts)
            semantic_matches = {
                str(path): match
                for path in clip_paths
                if (match := get_clip_visual_match(str(path), narration_fp))
            }
            if semantic_matches:
                self.log(
                    f"   👁️ Семантические оценки кадров доступны для "
                    f"{len(semantic_matches)}/{len(clip_paths)} клипов"
                )
        except Exception:
            semantic_matches = {}

        def next_from_cycle(items: List[str], cycle_name: str) -> str:
            nonlocal clip_cycle, image_cycle
            if cycle_name == 'clip':
                if not clip_cycle:
                    clip_cycle = list(items)
                    random.shuffle(clip_cycle)
                    if last_clip and len(clip_cycle) > 1 and str(clip_cycle[-1]) == last_clip:
                        clip_cycle[0], clip_cycle[-1] = clip_cycle[-1], clip_cycle[0]
                return clip_cycle.pop()
            if not image_cycle:
                image_cycle = list(items)
                random.shuffle(image_cycle)
            return image_cycle.pop()

        def next_clip_for_shot(shot_index: int) -> str:
            """Prefer the clip visually matched to the current narration segment."""
            nonlocal last_clip
            if not semantic_matches or not text_parts:
                selected = next_from_cycle(clip_paths, 'clip')
                selected_key = str(selected)
                clip_usage[selected_key] = clip_usage.get(selected_key, 0) + 1
                last_clip = str(selected)
                return selected
            segment_index = min(
                len(text_parts) - 1,
                int(shot_index * len(text_parts) / max(1, num_shots)),
            )
            best_path = None
            best_score = float('-inf')
            minimum_usage = min(clip_usage.values(), default=0)
            least_used_paths = [
                raw_path for raw_path in clip_paths
                if clip_usage.get(str(raw_path), 0) == minimum_usage
            ] or list(clip_paths)
            non_repeating_paths = [
                raw_path for raw_path in least_used_paths
                if str(raw_path) != last_clip
            ]
            candidate_paths = non_repeating_paths or least_used_paths
            for raw_path in candidate_paths:
                path = str(raw_path)
                match = semantic_matches.get(path)
                if match:
                    segment_scores = match.get('segment_scores') or {}
                    specific_score = segment_scores.get(segment_index)
                    if specific_score is None:
                        specific_score = float(match.get('score') or 0) * 0.55
                    score = float(specific_score) + float(match.get('score') or 0) * 0.2
                    best_segment = int(match.get('best_segment_index', -1))
                    if best_segment == segment_index:
                        score += 35
                    elif best_segment >= 0:
                        score -= min(25, abs(best_segment - segment_index) * 4)
                else:
                    score = 0
                if score > best_score:
                    best_score = score
                    best_path = raw_path
            selected = best_path if best_path is not None else next_from_cycle(clip_paths, 'clip')
            selected_key = str(selected)
            clip_usage[selected_key] = clip_usage.get(selected_key, 0) + 1
            last_clip = selected_key
            return selected
        
        # Если нет клипов - только картинки
        if not clip_paths:
            self.log("   ⚠️ Нет видеоклипов, используем только изображения")
            mixed_media = []
            for i in range(num_shots):
                if image_paths:
                    mixed_media.append((next_from_cycle(image_paths, 'image'), 'image'))
            return mixed_media
        
        # Если нет картинок — только клипы
        if not image_paths:
            if len(clip_paths) < num_shots and not allow_video_reuse:
                raise InsufficientUniqueVisualsError(
                    f"Нужно {num_shots} уникальных видеоклипов, доступно {len(clip_paths)}. "
                    "Повторы отключены."
                )
            self.log("   ⚠️ Нет изображений, используем только видеоклипы")
            mixed_media = []
            for i in range(num_shots):
                mixed_media.append((next_clip_for_shot(i), 'video'))
            return mixed_media

        # Если все image_paths являются заглушками (fallback) — заменяем клипами
        _all_fallback = all('fallback' in Path(p).name.lower() for p in image_paths)
        if _all_fallback and clip_paths:
            if len(clip_paths) < num_shots and not allow_video_reuse:
                raise InsufficientUniqueVisualsError(
                    f"Нужно {num_shots} уникальных видеоклипов, доступно {len(clip_paths)}. "
                    "Изображения-заглушки не используются, повторы отключены."
                )
            self.log("   ⚠️ Все изображения являются заглушками. Заменяем все шоты на видеоклипы")
            mixed_media = []
            for i in range(num_shots):
                mixed_media.append((next_clip_for_shot(i), 'video'))
            return mixed_media

        # 🎬 НОВАЯ ЛОГИКА: Равномерное распределение с циклическим переиспользованием
        mixed_media = []
        
        # Рассчитываем сколько YouTube шотов нужно
        if youtube_ratio <= 0:
            if len(image_paths) < num_shots:
                raise InsufficientUniqueVisualsError(
                    f"Режим без видео требует {num_shots} уникальных изображений, "
                    f"доступно {len(image_paths)}. Повторы отключены."
                )
            num_youtube_shots = 0
        elif youtube_ratio >= 1:
            if len(clip_paths) < num_shots and not allow_video_reuse:
                raise InsufficientUniqueVisualsError(
                    f"Режим 100% видео требует {num_shots} уникальных клипов, "
                    f"доступно {len(clip_paths)}. Повторы отключены."
                )
            num_youtube_shots = num_shots
        else:
            requested_youtube_shots = int(num_shots * youtube_ratio)
            minimum_youtube_shots = max(0, num_shots - len(image_paths))
            desired_youtube_shots = max(requested_youtube_shots, minimum_youtube_shots)
            num_youtube_shots = (
                min(num_shots, desired_youtube_shots)
                if allow_video_reuse
                else min(len(clip_paths), desired_youtube_shots)
            )
        num_image_shots = num_shots - num_youtube_shots
        
        self.log(f"   📊 План: {num_youtube_shots} видеоклипов + {num_image_shots} изображений")
        self.log(
            f"   📹 Доступно клипов: {len(clip_paths)} "
            + (
                "(контролируемое переиспользование включено)"
                if allow_video_reuse
                else "(повторное использование отключено)"
            )
        )
        
        # Рассчитываем интервал между YouTube клипами
        # Например: 355 шотов, 50% YouTube = 177 YouTube шотов
        # Интервал = 355 / 177 ≈ 2 (каждый 2-й шот YouTube)
        clip_index = 0
        image_index = 0
        youtube_positions = set()
        
        # 🎬 ФАЗА 1: Первые 2 шота ВСЕГДА YouTube (захват внимания)
        intro_clips = min(2, num_youtube_shots, len(clip_paths))
        for i in range(intro_clips):
            youtube_positions.add(i)
        
        # 🎬 ФАЗА 2: Распределяем остальные YouTube позиции равномерно
        remaining_youtube = num_youtube_shots - intro_clips
        if remaining_youtube > 0 and num_shots > intro_clips:
            # Начинаем с позиции после intro
            step = (num_shots - intro_clips) / remaining_youtube
            for i in range(remaining_youtube):
                pos = int(intro_clips + i * step)
                # Избегаем дублирования позиций
                while pos in youtube_positions and pos < num_shots:
                    pos += 1
                if pos < num_shots:
                    youtube_positions.add(pos)
        
        # 🎬 ФАЗА 3: Создаём mixed_media без переиспользования визуалов
        for shot_idx in range(num_shots):
            if shot_idx in youtube_positions:
                # YouTube клип (циклы перемешиваются заново)
                clip_path = next_clip_for_shot(shot_idx)
                mixed_media.append((clip_path, 'video'))
                
                # Логируем только первые 50 и последние 10
                if shot_idx < 50 or shot_idx >= num_shots - 10:
                    if shot_idx < 2:
                        self.log(f"   🎬 Шот {shot_idx + 1}: видеоклип (INTRO)")
                    else:
                        self.log(f"   🎬 Шот {shot_idx + 1}: видеоклип")
                elif shot_idx == 50:
                    self.log(f"   ... (пропуск логов для шотов 51-{num_shots - 10})")
                
                clip_index += 1
            else:
                # Изображение (циклы перемешиваются заново)
                image_path = next_from_cycle(image_paths, 'image')
                mixed_media.append((image_path, 'image'))
                
                # Логируем только первые 50 и последние 10
                if shot_idx < 50 or shot_idx >= num_shots - 10:
                    self.log(f"   🖼️ Шот {shot_idx + 1}: изображение")
                
                image_index += 1
        
        # Статистика
        yt_count = sum(1 for _, t in mixed_media if t == 'video')
        img_count = sum(1 for _, t in mixed_media if t == 'image')
        actual_ratio = yt_count / max(len(mixed_media), 1) * 100
        
        self.log(f"📊 Итого: {yt_count} видеоклипов ({actual_ratio:.0f}%) + {img_count} изображений")
        self.log(f"   🔄 Клипы переиспользованы: {clip_index} раз (из {len(clip_paths)} уникальных)")
        
        return mixed_media


def create_smart_mixed_media(
    text_parts: List[str],
    image_paths: List[str],
    clip_paths: List[str],
    youtube_ratio: float = 0.5,
    log_callback: Callable = _dummy_log,
    total_shots: int = None,
    allow_video_reuse: bool = False,
) -> List[Tuple[str, str]]:
    """
    Удобная функция для создания умного mixed_media.
    
    Args:
        total_shots: Общее количество шотов (КРИТИЧНО для правильной длительности!)
    """
    matcher = SmartClipMatcher(log_callback=log_callback)
    return matcher.create_smart_mixed_media(
        text_parts=text_parts,
        image_paths=image_paths,
        clip_paths=clip_paths,
        youtube_ratio=youtube_ratio,
        total_shots=total_shots,
        allow_video_reuse=allow_video_reuse,
    )
