#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎵 УМНАЯ МУЗЫКА ДЛЯ ДЛИННЫХ ВИДЕО
Автоматическая смена музыкальных треков с плавными переходами
"""

import os
import random
import subprocess
from typing import List, Dict, Optional

from core.process_registry import run_registered


class MusicSequencer:
    """Автоматическая смена музыки в длинных видео"""
    
    # Минимальная длительность для смены музыки
    MIN_DURATION_FOR_CHANGE = 120  # 2 минуты
    
    # Длительность crossfade между треками
    CROSSFADE_DURATION = 3.0  # секунды
    
    @staticmethod
    def should_use_sequence(duration: float) -> bool:
        """Определяет нужна ли смена музыки"""
        return duration >= MusicSequencer.MIN_DURATION_FOR_CHANGE
    
    @staticmethod
    def create_music_sequence(
        duration: float,
        music_path: str,
        base_volume: float = 0.25,
        chapters: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """
        Создаёт последовательность музыкальных треков
        
        Args:
            duration: Общая длительность видео (секунды)
            music_path: Путь к папке с музыкой
            chapters: Опциональный список глав для синхронизации
            
        Returns:
            [
                {"start": 0, "end": 120, "track": "intro.mp3", "volume": 0.25},
                {"start": 117, "end": 300, "track": "buildup.mp3", "volume": 0.20},
                ...
            ]
        """
        if not MusicSequencer.should_use_sequence(duration):
            # Короткое видео - одна музыка
            return []
        try:
            base_volume = float(base_volume)
        except (TypeError, ValueError):
            base_volume = 0.25
        if base_volume > 1.0:
            base_volume /= 100.0
        base_volume = max(0.0, min(1.0, base_volume))
        
        # Получаем список доступных треков
        if not os.path.exists(music_path):
            return []
        
        music_files = [
            f for f in os.listdir(music_path)
            if f.lower().endswith(('.mp3', '.wav', '.aac', '.flac', '.m4a'))
        ]
        
        if len(music_files) < 2:
            # Недостаточно треков для смены
            return []
        
        # Определяем точки смены музыки
        if chapters and len(chapters) > 1:
            # Используем главы как точки смены
            change_points = [ch.get('start_time', 0) for ch in chapters[1:]]
        else:
            # Автоматические точки каждые 2-3 минуты
            interval = random.uniform(120, 180)  # 2-3 минуты
            change_points = []
            current = interval
            while current < duration - 30:  # Не меняем в последние 30s
                change_points.append(current)
                current += interval
        
        # Создаём последовательность треков
        sequence = []
        random.shuffle(music_files)  # Перемешиваем для разнообразия
        
        start_time = 0
        for i, end_time in enumerate(change_points + [duration]):
            track_index = i % len(music_files)
            track = music_files[track_index]
            
            # Динамическая громкость в зависимости от фазы
            position = start_time / duration
            if position < 0.1:
                volume = base_volume
            elif position > 0.85:
                volume = base_volume * 0.75
            else:
                volume = base_volume * 0.85
            
            sequence.append({
                'start': start_time,
                'end': end_time,
                'track': os.path.join(music_path, track),
                'volume': volume,
                'crossfade': MusicSequencer.CROSSFADE_DURATION if i > 0 else 0
            })
            
            start_time = end_time
        
        return sequence
    
    @staticmethod
    def apply_music_sequence(
        voiceover_path: str,
        sequence: List[Dict],
        output_path: str,
        log_callback=None
    ) -> Optional[str]:
        """
        Применяет последовательность музыкальных треков к озвучке
        
        Args:
            voiceover_path: Путь к файлу озвучки
            sequence: Последовательность треков из create_music_sequence
            output_path: Путь для сохранения результата
            log_callback: Функция для логирования
            
        Returns:
            Путь к результату или None при ошибке
        """
        if not sequence:
            return None
        
        if log_callback:
            log_callback(f"🎵 Применение музыкальной последовательности ({len(sequence)} треков)")
        
        try:
            # Строим сложный FFmpeg фильтр для смены музыки с crossfade
            filter_parts = []
            
            # Загружаем все треки
            inputs = ['-i', voiceover_path]
            for i, seg in enumerate(sequence):
                inputs.extend(['-ss', str(seg['start']), '-i', seg['track']])
            
            # Создаём фильтр для каждого трека
            for i, seg in enumerate(sequence):
                track_idx = i + 1  # +1 потому что 0 = voiceover
                duration = seg['end'] - seg['start']
                volume = seg['volume']
                crossfade = seg.get('crossfade', 0)
                
                # Обрезаем и настраиваем громкость
                filter_parts.append(
                    f"[{track_idx}:a]atrim=0:{duration},volume={volume},asetpts=PTS-STARTPTS[music{i}]"
                )
            
            # Объединяем все треки с crossfade
            if len(sequence) == 1:
                mix_filter = "[music0]"
            else:
                # Последовательное смешивание с crossfade
                mix_filter = "[music0]"
                for i in range(1, len(sequence)):
                    crossfade = sequence[i].get('crossfade', 3)
                    mix_filter = f"{mix_filter}[music{i}]acrossfade=d={crossfade}[mix{i}]"
                    if i < len(sequence) - 1:
                        mix_filter = f"[mix{i}]"
                    else:
                        mix_filter = f"[mix{i}]"
            
            # Финальное смешивание с озвучкой
            final_filter = f"{';'.join(filter_parts)};[0:a]{mix_filter}amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
            
            cmd = [
                'ffmpeg', '-y'
            ] + inputs + [
                '-filter_complex', final_filter,
                '-map', '[aout]',
                '-c:a', 'libmp3lame',
                '-b:a', '192k',
                '-ar', '44100',
                output_path
            ]
            
            result = run_registered(
                cmd,
                label="ffmpeg_music_sequence",
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=300
            )
            
            if result.returncode != 0:
                if log_callback:
                    log_callback(f"⚠️ Ошибка FFmpeg при смене музыки: {result.stderr[:200]}")
                return None
            
            if log_callback:
                log_callback("✅ Музыкальная последовательность применена")
            
            return output_path
            
        except subprocess.TimeoutExpired:
            if log_callback:
                log_callback("❌ Timeout при применении музыкальной последовательности")
            return None
        except Exception as e:
            if log_callback:
                log_callback(f"❌ Ошибка музыкальной последовательности: {e}")
            return None
