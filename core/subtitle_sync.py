#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
КАРДИНАЛЬНОЕ РЕШЕНИЕ СИНХРОНИЗАЦИИ СУБТИТРОВ
Принудительная синхронизация через анализ аудио
"""

import difflib
import math
import os
import subprocess
import re
import shutil
import unicodedata
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from core.subtitle_text import join_subtitle_units, split_subtitle_units
from core.process_registry import run_registered


def _media_tool_path(name: str) -> str:
    """Prefer the bundled FFmpeg tools so fallback sync works outside PATH."""
    executable = f"{name}.exe" if os.name == "nt" else name
    bundled = Path(__file__).resolve().parents[1] / "tools" / "ffmpeg" / executable
    if bundled.is_file():
        return str(bundled)
    return shutil.which(executable) or shutil.which(name) or name


class SubtitleSynchronizer:
    """Принудительная синхронизация субтитров с аудио"""

    @staticmethod
    def _normalize_word_timestamp(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            word = str(item.get('word') or item.get('text') or '').strip()
            start = float(item.get('start', item.get('start_time')))
            end = float(item.get('end', item.get('end_time')))
            if not word or not math.isfinite(start) or not math.isfinite(end) or end <= start:
                return None
            return {
                'word': word,
                'start': max(0.0, start),
                'end': max(0.0, end),
                'no_space': bool(item.get('no_space', False)),
            }
        except Exception:
            return None

    @staticmethod
    def align_word_timestamps_to_text(
        word_timestamps: List[Dict[str, Any]],
        original_text: str,
    ) -> List[Dict[str, Any]]:
        """Map STT/TTS boundaries back to the exact source writing.

        Speech services may normalize abbreviations, punctuation, or split CJK
        text into units that differ from the authored transcript.  Subtitles
        must still display the authored text, while retaining the measured
        audio timing.  This aligner is Unicode-aware and works for both
        whitespace-delimited languages and compact CJK/Hangul text.
        """
        source_units, source_separator = split_subtitle_units(original_text)
        if not source_units:
            return []

        measured: List[Dict[str, Any]] = []
        for raw_item in word_timestamps or []:
            item = SubtitleSynchronizer._normalize_word_timestamp(raw_item)
            if not item:
                continue
            units, separator = split_subtitle_units(item['word'])
            if separator == '' and len(units) > 1:
                unit_duration = (item['end'] - item['start']) / len(units)
                for index, unit in enumerate(units):
                    measured.append({
                        'word': unit,
                        'start': item['start'] + index * unit_duration,
                        'end': item['start'] + (index + 1) * unit_duration,
                    })
            else:
                measured.append(item)

        if not measured:
            return []

        def comparable(value: str) -> str:
            normalized = unicodedata.normalize('NFKC', str(value or '')).casefold()
            return ''.join(
                character for character in normalized
                if unicodedata.category(character)[0] in {'L', 'N'}
            )

        source_keys = [comparable(unit) for unit in source_units]
        measured_keys = [comparable(item['word']) for item in measured]
        matcher = difflib.SequenceMatcher(None, source_keys, measured_keys, autojunk=False)
        aligned: List[Dict[str, Any]] = []

        def append_distributed(units, start: float, end: float) -> None:
            if not units:
                return
            duration = max(0.001, end - start)
            for index, unit in enumerate(units):
                aligned.append({
                    'word': unit,
                    'start': start + duration * index / len(units),
                    'end': start + duration * (index + 1) / len(units),
                    'no_space': source_separator == '',
                })

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                for source_index, measured_index in zip(range(i1, i2), range(j1, j2)):
                    item = measured[measured_index]
                    aligned.append({
                        'word': source_units[source_index],
                        'start': item['start'],
                        'end': item['end'],
                        'no_space': source_separator == '',
                    })
            elif tag == 'replace' and j1 < j2:
                append_distributed(
                    source_units[i1:i2],
                    measured[j1]['start'],
                    measured[j2 - 1]['end'],
                )
            elif tag == 'delete':
                previous_end = aligned[-1]['end'] if aligned else (
                    measured[j1]['start'] if j1 < len(measured) else 0.0
                )
                next_start = measured[j1]['start'] if j1 < len(measured) else previous_end + 0.05 * max(1, i2 - i1)
                if next_start <= previous_end:
                    next_start = previous_end + 0.05 * max(1, i2 - i1)
                append_distributed(source_units[i1:i2], previous_end, next_start)
            # ``insert`` means the recognizer hallucinated or expanded a spoken
            # form (for example "ДСП" -> three letter names).  It is omitted
            # because the authored transcript remains authoritative.

        aligned.sort(key=lambda item: (item['start'], item['end']))
        return aligned

    @staticmethod
    def create_subtitles_from_word_timestamps(
        word_timestamps: List[Dict[str, Any]],
        max_words_per_subtitle: int = 3,
        max_chars_per_subtitle: int = 34,
        max_duration: float = 2.2,
        min_duration: float = 0.45,
        pause_break: float = 0.35,
        lead_in: float = 0.03,
        lead_out: float = 0.08,
        audio_duration: Optional[float] = None
    ) -> List[Tuple[float, float, str]]:
        normalized = []
        for item in word_timestamps or []:
            normalized_item = SubtitleSynchronizer._normalize_word_timestamp(item)
            if normalized_item:
                units, separator = split_subtitle_units(normalized_item['word'])
                if separator == '' and len(units) > 1:
                    unit_duration = (
                        normalized_item['end'] - normalized_item['start']
                    ) / len(units)
                    has_hangul = any(
                        0xAC00 <= ord(character) <= 0xD7AF
                        for character in normalized_item['word']
                    )
                    for index, unit in enumerate(units):
                        normalized.append({
                            'word': unit,
                            'start': normalized_item['start'] + index * unit_duration,
                            'end': normalized_item['start'] + (index + 1) * unit_duration,
                            # Separate Korean STT words, but keep characters
                            # inside one returned word together. CJK timestamps
                            # conventionally render without spaces.
                            'space_before': bool(index == 0 and normalized and has_hangul),
                        })
                else:
                    normalized_item['space_before'] = bool(
                        normalized and not normalized_item.get('no_space')
                    )
                    normalized.append(normalized_item)

        normalized.sort(key=lambda x: x['start'])
        if not normalized:
            return []

        subtitles = []
        current_words = []
        current_start = None
        current_end = None

        def render_words(items):
            text = ''
            for word_item in items:
                if text and word_item.get('space_before', True):
                    text += ' '
                text += word_item['word']
            return text.strip()

        def flush():
            nonlocal current_words, current_start, current_end
            if not current_words or current_start is None or current_end is None:
                return
            text = render_words(current_words)
            if not text:
                current_words = []
                current_start = None
                current_end = None
                return
            start = max(0.0, current_start - lead_in)
            end = current_end + lead_out
            if audio_duration:
                end = min(end, audio_duration)
            if end - start < min_duration:
                end = start + min_duration
                if audio_duration:
                    end = min(end, audio_duration)
            if end > start:
                subtitles.append((start, end, text))
            current_words = []
            current_start = None
            current_end = None

        sentence_end = ('.', '!', '?', '…', '。', '！', '？', '؟', '।', '॥')
        soft_break = (',', ';', ':')

        for item in normalized:
            word = item['word']
            start = item['start']
            end = item['end']

            gap = 0.0 if current_end is None else start - current_end
            projected_words = len(current_words) + 1
            projected_item = dict(item)
            projected_item.setdefault('space_before', bool(current_words))
            projected_text = render_words(current_words + [projected_item])
            projected_duration = 0.0 if current_start is None else end - current_start

            should_flush_before = (
                bool(current_words) and (
                    gap >= pause_break or
                    projected_words > max_words_per_subtitle or
                    len(projected_text) > max_chars_per_subtitle or
                    projected_duration > max_duration
                )
            )

            if should_flush_before:
                flush()

            if current_start is None:
                current_start = start
            item.setdefault('space_before', bool(current_words))
            current_words.append(item)
            current_end = end

            stripped = word.rstrip()
            should_flush_after = (
                stripped.endswith(sentence_end) or
                (stripped.endswith(soft_break) and len(current_words) >= 2)
            )
            if should_flush_after:
                flush()

        flush()

        fixed = []
        for start, end, text in subtitles:
            if fixed and start < fixed[-1][1]:
                prev_start, prev_end, prev_text = fixed[-1]
                midpoint = (prev_end + start) / 2
                fixed[-1] = (prev_start, max(prev_start + 0.1, midpoint), prev_text)
                start = max(start, fixed[-1][1])
            if end > start:
                fixed.append((start, end, text))

        return fixed
    
    @staticmethod
    def split_into_short_phrases(text: str, max_words: int = 3) -> List[str]:
        """
        Разбивает текст на короткие фразы по 2-3 слова.
        
        Args:
            text: Исходный текст
            max_words: Максимум слов в одной фразе
            
        Returns:
            Список коротких фраз
        """
        # Очищаем текст
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Разбиваем на слова
        words, separator = split_subtitle_units(text)
        
        # Группируем по max_words
        phrases = []
        for i in range(0, len(words), max_words):
            phrase = join_subtitle_units(words[i:i + max_words], separator)
            phrases.append(phrase)
        
        return phrases
    
    @staticmethod
    def extract_audio_silences(audio_path: str, silence_thresh: int = -40, min_silence_len: int = 300) -> List[Tuple[float, float]]:
        """
        Извлекает паузы из аудио через ffmpeg.
        Паузы = места где можно менять субтитры.
        
        Args:
            audio_path: Путь к аудио файлу
            silence_thresh: Порог тишины в dB (по умолчанию -40dB)
            min_silence_len: Минимальная длина паузы в мс
            
        Returns:
            Список (start, end) паузы в секундах
        """
        try:
            # Используем ffmpeg silencedetect filter
            cmd = [
                _media_tool_path('ffmpeg'),
                '-i', audio_path,
                '-af', f'silencedetect=noise={silence_thresh}dB:d={min_silence_len/1000}',
                '-f', 'null',
                '-'
            ]
            
            result = run_registered(
                cmd,
                label="ffmpeg_subtitle_silence_detect",
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=60,
            )
            output = result.stderr
            
            # Парсим вывод
            silences = []
            silence_start = None
            
            for line in output.split('\n'):
                if 'silence_start' in line:
                    match = re.search(r'silence_start: ([\d.]+)', line)
                    if match:
                        silence_start = float(match.group(1))
                elif 'silence_end' in line and silence_start is not None:
                    match = re.search(r'silence_end: ([\d.]+)', line)
                    if match:
                        silence_end = float(match.group(1))
                        silences.append((silence_start, silence_end))
                        silence_start = None
            
            return silences
            
        except Exception:
            # Fallback: нет пауз
            return []
    
    @staticmethod
    def get_audio_duration(audio_path: str) -> float:
        """Получает длительность аудио через ffprobe"""
        try:
            cmd = [
                _media_tool_path('ffprobe'),
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                audio_path
            ]
            result = run_registered(
                cmd,
                label="ffprobe_subtitle_audio_duration",
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=10,
            )
            return float(result.stdout.strip())
        except Exception:
            return 0.0
    
    @staticmethod
    def estimate_phrase_duration(phrase: str, speech_rate: float = 2.5) -> float:
        """
        Оценивает длительность произнесения фразы.
        
        Args:
            phrase: Фраза для оценки
            speech_rate: Слов в секунду (по умолчанию 2.5 = 150 слов/мин)
            
        Returns:
            Примерная длительность в секундах
        """
        words, _separator = split_subtitle_units(phrase)
        word_count = len(words)
        
        # Базовая длительность = количество слов / скорость речи
        base_duration = word_count / speech_rate
        
        # Минимум 1 секунда на фразу (чтобы успеть прочитать)
        return max(base_duration, 1.0)
    
    @staticmethod
    def synchronize_subtitles(text: str, audio_path: str, max_words_per_subtitle: int = 3) -> List[Tuple[float, float, str]]:
        """
        КАРДИНАЛЬНАЯ СИНХРОНИЗАЦИЯ субтитров с аудио.
        
        НОВЫЙ АЛГОРИТМ:
        1. Разбиваем текст на короткие фразы (2-3 слова)
        2. Оцениваем длительность каждой фразы
        3. Масштабируем под реальную длительность аудио
        4. Возвращаем точные тайминги
        
        Args:
            text: Текст для субтитров
            audio_path: Путь к аудио файлу
            max_words_per_subtitle: Максимум слов в субтитре
            
        Returns:
            Список (start_time, end_time, text) для каждого субтитра
        """
        # 1. Разбиваем текст на короткие фразы
        phrases = SubtitleSynchronizer.split_into_short_phrases(text, max_words_per_subtitle)
        
        if not phrases:
            return []
        
        # 2. Получаем длительность аудио
        duration = SubtitleSynchronizer.get_audio_duration(audio_path)
        
        if duration <= 0:
            return []
        
        # 3. Оцениваем длительность каждой фразы
        phrase_durations = []
        total_estimated_duration = 0.0
        
        for phrase in phrases:
            estimated = SubtitleSynchronizer.estimate_phrase_duration(phrase)
            phrase_durations.append(estimated)
            total_estimated_duration += estimated
        
        # 4. Распределяем всю дорожку без переполнения. Старый код сначала
        # масштабировал фразы, а затем принудительно делал каждую не короче
        # 0.8 секунды. При большом числе фраз это уводило таймлайн за конец
        # аудио, и последние субтитры становились нулевой длины и исчезали.
        phrase_count = len(phrase_durations)
        minimum_duration = 0.8
        if duration >= minimum_duration * phrase_count:
            flexible_duration = duration - minimum_duration * phrase_count
            allocated_durations = [
                minimum_duration + flexible_duration * estimated / total_estimated_duration
                for estimated in phrase_durations
            ]
        else:
            allocated_durations = [
                duration * estimated / total_estimated_duration
                for estimated in phrase_durations
            ]
        
        # 5. Создаем субтитры с точными таймингами
        subtitles = []
        current_time = 0.0
        
        for i, phrase in enumerate(phrases):
            start_time = current_time
            
            end_time = (
                duration
                if i == phrase_count - 1
                else min(start_time + allocated_durations[i], duration)
            )
            
            subtitles.append((start_time, end_time, phrase))
            
            current_time = end_time
        
        return subtitles
    
    @staticmethod
    def convert_to_word_timestamps(synced_subs: List[Tuple[float, float, str]]) -> List[dict]:
        """
        Конвертирует синхронизированные субтитры в формат word_timestamps
        для совместимости с динамическими субтитрами
        
        Args:
            synced_subs: [(start, end, text), ...]
            
        Returns:
            [{'word': 'слово', 'start': 0.0, 'end': 1.2}, ...]
        """
        word_timestamps = []
        
        for start_time, end_time, text in synced_subs:
            words, separator = split_subtitle_units(text)
            if not words:
                continue
            
            # Распределяем время между словами в фразе
            word_duration = (end_time - start_time) / len(words)
            
            for i, word in enumerate(words):
                word_start = start_time + (i * word_duration)
                word_end = word_start + word_duration
                
                word_timestamps.append({
                    'word': word,
                    'start': word_start,
                    'end': word_end,
                    'no_space': separator == '',
                })
        
        return word_timestamps

