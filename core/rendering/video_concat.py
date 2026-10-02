#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🎬 Video Concatenation Module
Модуль для склейки видео с переходами и без
"""

import subprocess
import logging
import shutil
import random
from pathlib import Path
from typing import List, Callable, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# Импорты из других модулей
from core.rendering.ffmpeg_utils import FFMPEG_PATH
from core.process_registry import run_registered
from core.utils import safe_cpu_count, safe_temp_dir

# Глобальный пул потоков
try:
    from core.utils import thread_slot
    _USE_GLOBAL_POOL = True
except ImportError:
    _USE_GLOBAL_POOL = False
    def thread_slot(category='default', priority=None, timeout=300):
        from contextlib import contextmanager
        @contextmanager
        def dummy():
            yield
        return dummy()


class VideoConcatenator:
    """
    Класс для склейки видео файлов с переходами и без.
    
    Возможности:
    - Простая склейка без переходов (filter_complex, concat demuxer)
    - Склейка с кинематографичными переходами (xfade)
    - Batch-обработка для большого количества файлов
    - Параллельная обработка батчей
    """
    
    # Константы
    MAX_FILES_PER_BATCH = 50  # Максимум файлов в одном батче (Windows cmd limit)
    
    # NVENC настройки
    NVENC_PRESET = 'p1'  # Fastest: все файлы здесь промежуточные, перекодируются в финале
    NVENC_CQ = 26  # Немного выше CQ (хватает для intermediate)
    
    def __init__(self, codec_settings_getter: Callable):
        """
        Args:
            codec_settings_getter: Функция для получения настроек кодека
        """
        self.get_codec_settings = codec_settings_getter
    
    def simple_concat_shots(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        log_callback: Optional[Callable] = None
    ) -> str:
        """
        Простая склейка без переходов через filter_complex.
        
        Использует перекодирование для избежания ошибок NAL unit
        при разных параметрах кодирования входных файлов.
        
        Args:
            shot_files: Список путей к видео файлам
            output_path: Путь для выходного файла
            fps: Частота кадров
            width: Ширина видео
            height: Высота видео
            log_callback: Функция для логирования
            
        Returns:
            Путь к склеенному видео
        """
        if log_callback is None:
            log_callback = print
        
        if not shot_files:
            log_callback("   ⚠️ Нет файлов для склейки")
            return output_path
        
        # Если только один файл - просто копируем
        if len(shot_files) == 1:
            try:
                shutil.copy2(shot_files[0], output_path)
                return output_path
            except Exception as e:
                log_callback(f"   ⚠️ Ошибка копирования: {e}")
                return output_path
        
        # Batch для большого количества файлов
        if len(shot_files) > self.MAX_FILES_PER_BATCH:
            return self._batch_simple_concat(
                shot_files, output_path, fps, width, height, log_callback
            )
        
        temp_dir = safe_temp_dir(prefix='concat_')
        
        try:
            # Используем filter_complex для надёжной конкатенации
            inputs = []
            filter_parts = []
            
            for i, shot_file in enumerate(shot_files):
                inputs.extend(['-i', shot_file])
                # Нормализуем каждый вход
                filter_parts.append(
                    f'[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,'
                    f'crop={width}:{height},fps={fps},'
                    f'format=yuv420p,setsar=1[v{i}];'
                )
            
            # Конкатенация всех нормализованных потоков
            concat_inputs = ''.join(f'[v{i}]' for i in range(len(shot_files)))
            filter_complex = ''.join(filter_parts) + f'{concat_inputs}concat=n={len(shot_files)}:v=1:a=0[outv]'
            
            codec_settings = self.get_codec_settings(use_nvenc=True, log_callback=None)
            
            if codec_settings['vcodec'] == 'h264_nvenc':
                codec_args = ['-c:v', 'h264_nvenc', '-preset', self.NVENC_PRESET, '-cq', str(self.NVENC_CQ)]
            else:
                codec_args = ['-c:v', 'libx264', '-preset', 'fast', '-crf', str(self.NVENC_CQ)]
            
            cmd = [
                FFMPEG_PATH, '-y',
                *inputs,
                '-filter_complex', filter_complex,
                '-map', '[outv]',
                *codec_args,
                '-movflags', '+faststart',
                output_path
            ]
            
            log_callback(f"   🔗 Склейка {len(shot_files)} шотов через filter_complex...")
            result = run_registered(
                cmd, label="ffmpeg_video_concat", capture_output=True, text=True,
                encoding='utf-8', errors='ignore', timeout=600
            )
            
            if result.returncode != 0:
                log_callback("   ⚠️ filter_complex не удался, пробуем concat demuxer...")
                return self._simple_concat_demuxer(
                    shot_files, output_path, fps, width, height, temp_dir, log_callback
                )
            
            log_callback(f"   ✅ Склейка завершена: {output_path}")
            return output_path
            
        except subprocess.TimeoutExpired:
            log_callback("   ⚠️ Таймаут при склейке")
            return output_path
        except Exception as e:
            log_callback(f"   ⚠️ Ошибка склейки: {e}")
            return output_path
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                logging.debug(f"Не удалось очистить temp_dir {temp_dir}: {e}")
    
    def _batch_simple_concat(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        log_callback: Callable
    ) -> str:
        """Batch-склейка для большого количества файлов через concat demuxer."""
        temp_dir = safe_temp_dir(prefix='batch_simple_')
        batch_outputs = []
        
        try:
            batch_size = self.MAX_FILES_PER_BATCH
            num_batches = (len(shot_files) + batch_size - 1) // batch_size
            log_callback(f"   📦 Batch-склейка: {len(shot_files)} файлов → {num_batches} батчей")
            
            for batch_idx in range(num_batches):
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, len(shot_files))
                batch_files = shot_files[start_idx:end_idx]
                
                batch_output = temp_dir / f"batch_{batch_idx:03d}.mp4"
                
                # Используем concat demuxer для каждого батча
                concat_list = temp_dir / f"list_{batch_idx:03d}.txt"
                with open(concat_list, 'w', encoding='utf-8') as f:
                    for shot_file in batch_files:
                        escaped = str(Path(shot_file).resolve()).replace('\\', '/').replace("'", "'\\''")
                        f.write(f"file '{escaped}'\n")
                
                cmd = [
                    FFMPEG_PATH, '-y', '-f', 'concat', '-safe', '0',
                    '-i', str(concat_list),
                    '-c', 'copy',
                    str(batch_output)
                ]
                run_registered(
                    cmd, label=f"ffmpeg_video_concat_batch_{batch_idx}", capture_output=True, text=True,
                    encoding='utf-8', errors='ignore', timeout=300
                )
                
                if batch_output.exists():
                    batch_outputs.append(str(batch_output))
            
            if not batch_outputs:
                log_callback("   ❌ Batch-склейка не удалась")
                return output_path
            
            # Финальное объединение батчей
            if len(batch_outputs) == 1:
                shutil.copy2(batch_outputs[0], output_path)
            else:
                final_list = temp_dir / 'final.txt'
                with open(final_list, 'w', encoding='utf-8') as f:
                    for bf in batch_outputs:
                        escaped = bf.replace('\\', '/').replace("'", "'\\''")
                        f.write(f"file '{escaped}'\n")
                
                cmd = [
                    FFMPEG_PATH, '-y', '-f', 'concat', '-safe', '0', 
                    '-i', str(final_list), '-c', 'copy', output_path
                ]
                run_registered(
                    cmd, label="ffmpeg_video_concat_batches_final", capture_output=True, text=True,
                    encoding='utf-8', errors='ignore', timeout=300
                )
            
            log_callback("   ✅ Batch-склейка завершена")
            return output_path
            
        except Exception as e:
            log_callback(f"   ❌ Ошибка batch-склейки: {e}")
            return output_path
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as cleanup_err:
                logging.debug(f"Cleanup temp_dir failed: {cleanup_err}")
    
    def _simple_concat_demuxer(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        temp_dir: Path,
        log_callback: Optional[Callable] = None
    ) -> str:
        """Fallback склейка через concat demuxer."""
        if log_callback is None:
            log_callback = print
        
        concat_list = temp_dir / 'concat_list.txt'
        
        with open(concat_list, 'w', encoding='utf-8') as f:
            for shot_file in shot_files:
                normalized = str(Path(shot_file).resolve()).replace('\\', '/')
                f.write(f"file '{normalized}'\n")
        
        codec_settings = self.get_codec_settings(use_nvenc=True, log_callback=None)
        
        if codec_settings['vcodec'] == 'h264_nvenc':
            codec_args = ['-c:v', 'h264_nvenc', '-preset', self.NVENC_PRESET, '-cq', str(self.NVENC_CQ)]
        else:
            codec_args = ['-c:v', 'libx264', '-preset', 'fast', '-crf', str(self.NVENC_CQ)]
        
        cmd = [
            FFMPEG_PATH, '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_list),
            *codec_args,
            '-pix_fmt', 'yuv420p',
            '-r', str(fps),
            output_path
        ]
        
        run_registered(
            cmd, label="ffmpeg_video_concat_demuxer", capture_output=True, text=True,
            encoding='utf-8', errors='ignore', timeout=300
        )
        return output_path
    
    def concat_shots_with_transitions(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        transition_duration: float = 0.3,
        log_callback: Optional[Callable] = None,
        media_types: Optional[List[str]] = None,
        transition_style: str = 'cinematic',
        transition_frequency: float = 0.6
    ) -> str:
        """
        Склеивает видео-шоты с кинематографичными переходами xfade.
        
        Args:
            shot_files: Список путей к видео-файлам шотов
            output_path: Путь для выходного файла
            fps: Частота кадров
            width: Ширина видео
            height: Высота видео
            transition_duration: Длительность перехода (сек)
            log_callback: Функция логирования
            media_types: Типы медиа для каждого шота ('image' или 'video')
            transition_style: Стиль переходов ('cinematic', 'energetic', 'smooth', 'dramatic')
            transition_frequency: Доля шотов с переходами (0.0-1.0, default 0.6 = 60%)
            
        Returns:
            Путь к склеенному видео
        """
        if log_callback is None:
            log_callback = print
            
        if len(shot_files) < 2:
            if shot_files:
                shutil.copy(shot_files[0], output_path)
            return output_path
        
        # Batch processing для большого количества шотов
        if len(shot_files) > self.MAX_FILES_PER_BATCH:
            log_callback(f"   📦 Много шотов ({len(shot_files)}) - используем batch-склейку")
            return self._batch_concat_with_transitions(
                shot_files, output_path, fps, width, height,
                transition_duration, log_callback, media_types, transition_style
            )
        
        # Библиотека переходов
        classic_transitions = ['fade', 'fadeblack', 'dissolve', 'fadegrays']
        smooth_transitions = ['smoothup', 'smoothdown', 'smoothleft', 'smoothright']
        dynamic_transitions = ['wipeleft', 'wiperight', 'wipeup', 'wipedown', 'slideleft', 'slideright']
        geometric_transitions = ['circlecrop', 'circleopen', 'circleclose', 'radial']
        energetic_transitions = ['pixelize', 'hlslice', 'vrslice', 'squeezeh', 'squeezev', 'zoomin']
        diagonal_transitions = ['diagtl', 'diagtr', 'diagbl', 'diagbr']
        
        # Выбираем набор переходов в зависимости от стиля
        if transition_style == 'energetic':
            transition_types = energetic_transitions + dynamic_transitions + ['zoomin', 'fadewhite']
        elif transition_style == 'smooth':
            transition_types = smooth_transitions + classic_transitions
        elif transition_style == 'dramatic':
            transition_types = ['fadeblack', 'circleclose', 'radial', 'dissolve', 'zoomin']
        else:  # cinematic
            transition_types = (
                classic_transitions + 
                smooth_transitions[:2] + 
                geometric_transitions[:2] + 
                diagonal_transitions[:2] +
                dynamic_transitions[:2] +
                energetic_transitions[:2]
            )
        
        # Определяем позиции переходов
        num_transitions_total = len(shot_files) - 1
        num_transitions_to_apply = max(1, int(num_transitions_total * transition_frequency))
        
        transition_positions = set()
        if num_transitions_total >= 1:
            transition_positions.add(1)
        if num_transitions_total >= 2:
            transition_positions.add(num_transitions_total)
        
        if num_transitions_to_apply > len(transition_positions):
            remaining = num_transitions_to_apply - len(transition_positions)
            step = max(1, num_transitions_total // (remaining + 1))
            for j in range(1, remaining + 1):
                pos = min(j * step, num_transitions_total)
                if pos not in transition_positions:
                    transition_positions.add(pos)
        
        log_callback(f"✨ Переходы: {len(transition_positions)}/{num_transitions_total} ({int(len(transition_positions)/max(1,num_transitions_total)*100)}%)")
        
        # Получаем длительности шотов — параллельные ffprobe
        # ФИКС: берём ffprobe из папки ffmpeg а не replace() (который портит путь)
        from pathlib import Path as _P
        _fp_parent = _P(FFMPEG_PATH).parent
        ffprobe_bin = str(_fp_parent / ('ffprobe.exe' if FFMPEG_PATH.lower().endswith('.exe') else 'ffprobe'))
        if not _P(ffprobe_bin).exists():
            ffprobe_bin = 'ffprobe'

        def _probe_duration(shot_file):
            try:
                probe_cmd = [
                    ffprobe_bin, '-v', 'error',
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    shot_file
                ]
                result = run_registered(
                    probe_cmd, label="ffprobe_video_concat_shot", capture_output=True, text=True,
                    encoding='utf-8', errors='ignore', timeout=30
                )
                return float(result.stdout.strip()) if result.returncode == 0 else 5.0
            except Exception:
                return 5.0

        with ThreadPoolExecutor(max_workers=min(len(shot_files), 8)) as _pool:
            shot_durations = list(_pool.map(_probe_duration, shot_files))
        
        # Строим filter_complex с xfade
        # Hardware decode per-input (на Windows DXVA2/D3D11VA, скорее decode без нагрузки CPU)
        input_args = []
        for i, shot_file in enumerate(shot_files):
            input_args.extend(['-hwaccel', 'auto', '-i', shot_file])
        
        if not shot_durations:
            log_callback("   ⚠️ Нет длительностей шотов, используем простую склейку")
            return self.simple_concat_shots(shot_files, output_path, fps, width, height, log_callback)
        
        # format=yuv420p для каждого входа — обеспечивает совместимость при hw decode
        filter_prefix = []
        stream_names = []
        for i in range(len(shot_files)):
            out = f"vin{i}"
            filter_prefix.append(f"[{i}:v]format=yuv420p[{out}]")
            stream_names.append(out)

        # Строим цепочку переходов
        filter_parts = filter_prefix
        current_stream = stream_names[0]
        used_transitions = []
        actual_transitions_applied = 0
        
        for i in range(1, len(shot_files)):
            out_stream = f"xf{i}"
            
            apply_transition = i in transition_positions
            next_stream = stream_names[i]
            
            if apply_transition:
                # Полный переход
                position_ratio = i / len(shot_files)
                
                if position_ratio < 0.15:
                    preferred = energetic_transitions + ['zoomin', 'fadewhite']
                elif position_ratio > 0.85:
                    preferred = ['fadeblack', 'dissolve', 'circleclose', 'fade']
                elif 0.4 < position_ratio < 0.6:
                    preferred = geometric_transitions + dynamic_transitions
                else:
                    preferred = smooth_transitions + classic_transitions
                
                if media_types and i < len(media_types):
                    prev_type = media_types[i - 1] if i > 0 else 'image'
                    curr_type = media_types[i]
                    if prev_type != curr_type:
                        preferred = ['fadeblack', 'dissolve', 'zoomin', 'circleopen', 'radial']
                
                available = [t for t in preferred if t in transition_types]
                if not available:
                    available = transition_types
                available = [t for t in available if t not in used_transitions[-2:]]
                if not available:
                    available = transition_types
                
                transition = random.choice(available)
                current_transition_duration = transition_duration
                actual_transitions_applied += 1
            else:
                # Простая склейка (минимальный fade)
                transition = 'fade'
                current_transition_duration = 0.05
            
            used_transitions.append(transition)
            
            # Расчёт offset
            cumulative_duration = sum(shot_durations[0:i])
            prev_transition_time = sum(
                transition_duration if j in transition_positions else 0.05
                for j in range(1, i)
            )
            offset = cumulative_duration - prev_transition_time - current_transition_duration
            offset = max(0, offset)
            
            filter_parts.append(
                f"[{current_stream}][{next_stream}]xfade=transition={transition}:duration={current_transition_duration}:offset={offset:.3f}[{out_stream}]"
            )
            
            current_stream = out_stream

        
        log_callback(f"   🎬 Применено {actual_transitions_applied} заметных переходов")
        
        filter_complex = ";".join(filter_parts)
        
        codec_settings = self.get_codec_settings(use_nvenc=True, log_callback=None)
        
        if codec_settings['vcodec'] == 'h264_nvenc':
            codec_args = [
                '-c:v', 'h264_nvenc',
                '-preset', self.NVENC_PRESET,
                '-rc', 'vbr',
                '-cq', str(self.NVENC_CQ),
                '-b:v', '10M'
            ]
        else:
            codec_args = [
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '18'
            ]
        
        cmd = [
            FFMPEG_PATH, '-y',
            *input_args,
            '-filter_complex', filter_complex,
            '-map', f'[{current_stream}]',
            *codec_args,
            '-pix_fmt', 'yuv420p',
            '-r', str(fps),
            output_path
        ]
        
        log_callback(f"✨ Применяем {len(shot_files)-1} кинематографичных переходов...")
        
        try:
            result = run_registered(
                cmd, label="ffmpeg_video_concat_transitions", capture_output=True, text=True,
                encoding='utf-8', errors='ignore', timeout=600
            )
            
            if result.returncode != 0:
                stderr = result.stderr.lower() if result.stderr else ''
                if 'xfade' in stderr or 'filter' in stderr:
                    log_callback("   ⚠️ xfade фильтр не поддерживается, используем простую склейку")
                else:
                    log_callback(f"   ⚠️ Ошибка переходов: {result.stderr[:150]}")
                return self.simple_concat_shots(shot_files, output_path, fps, width, height, log_callback)
            
            if not Path(output_path).exists():
                log_callback("   ⚠️ Файл не создан, используем простую склейку")
                return self.simple_concat_shots(shot_files, output_path, fps, width, height, log_callback)
            
            log_callback("   ✅ Переходы применены успешно!")
            return output_path
            
        except subprocess.TimeoutExpired:
            log_callback("   ⚠️ Таймаут при применении переходов, используем простую склейку")
            return self.simple_concat_shots(shot_files, output_path, fps, width, height, log_callback)
        except Exception as e:
            log_callback(f"   ⚠️ Ошибка: {e}, используем простую склейку")
            return self.simple_concat_shots(shot_files, output_path, fps, width, height, log_callback)
    
    def _batch_concat_with_transitions(
        self,
        shot_files: List[str],
        output_path: str,
        fps: int,
        width: int,
        height: int,
        transition_duration: float,
        log_callback: Callable,
        media_types: Optional[List[str]],
        transition_style: str,
        progress_callback: Optional[Callable] = None
    ) -> str:
        """
        Batch-склейка для большого количества шотов с переходами.
        
        Разбивает на группы, склеивает каждую, затем объединяет группы.
        """
        temp_dir = safe_temp_dir(prefix='batch_concat_')
        batch_outputs = []
        
        if progress_callback is None:
            progress_callback = lambda x: None
        
        try:
            batch_size = self.MAX_FILES_PER_BATCH
            num_batches = (len(shot_files) + batch_size - 1) // batch_size
            log_callback(f"   📦 Разбиваем {len(shot_files)} шотов на {num_batches} батчей по ~{batch_size}")
            
            # Параллельная обработка батчей
            max_workers = min(num_batches, max(2, safe_cpu_count() // 2 or 2))
            log_callback(f"   ⚡ Параллельная склейка: {max_workers} воркеров")
            
            def process_batch(batch_idx):
                """Обработка одного батча"""
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, len(shot_files))
                batch_files = shot_files[start_idx:end_idx]
                
                batch_output = temp_dir / f"batch_{batch_idx:03d}.mp4"
                
                if _USE_GLOBAL_POOL:
                    with thread_slot('render', priority='high'):
                        self.simple_concat_shots(
                            batch_files, str(batch_output), fps, width, height, lambda x: None
                        )
                else:
                    self.simple_concat_shots(
                        batch_files, str(batch_output), fps, width, height, lambda x: None
                    )
                
                return batch_idx, str(batch_output) if batch_output.exists() else None
            
            # Запускаем все батчи параллельно
            batch_results = {}
            completed_batches = 0
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(process_batch, i): i for i in range(num_batches)}
                
                for future in as_completed(futures):
                    batch_idx = futures[future]
                    try:
                        idx, result_path = future.result()
                        completed_batches += 1
                        
                        progress_percent = int((completed_batches / num_batches) * 90)
                        progress_callback(progress_percent)
                        
                        if result_path:
                            batch_results[idx] = result_path
                            log_callback(f"   ✅ Батч {idx + 1}/{num_batches} готов")
                        else:
                            log_callback(f"   ⚠️ Батч {idx + 1} не создан")
                    except Exception as e:
                        log_callback(f"   ⚠️ Ошибка батча {batch_idx + 1}: {e}")
            
            # Собираем результаты в правильном порядке
            for i in range(num_batches):
                if i in batch_results:
                    batch_outputs.append(batch_results[i])
            
            if not batch_outputs:
                log_callback("   ❌ Ни один батч не создан")
                return output_path
            
            # Объединяем батчи
            progress_callback(95)
            
            if len(batch_outputs) == 1:
                shutil.copy2(batch_outputs[0], output_path)
            else:
                log_callback(f"   🔗 Объединяем {len(batch_outputs)} батчей...")
                concat_list = temp_dir / 'final_concat.txt'
                with open(concat_list, 'w', encoding='utf-8') as f:
                    for batch_file in batch_outputs:
                        escaped_path = batch_file.replace('\\', '/').replace("'", "'\\''")
                        f.write(f"file '{escaped_path}'\n")
                
                cmd = [
                    FFMPEG_PATH, '-y', '-f', 'concat', '-safe', '0',
                    '-i', str(concat_list),
                    '-c', 'copy',
                    output_path
                ]
                result = run_registered(
                    cmd, label="ffmpeg_video_concat_transition_batches", capture_output=True, text=True,
                    encoding='utf-8', errors='ignore', timeout=300
                )
                
                if result.returncode != 0:
                    log_callback(f"   ⚠️ Concat demuxer ошибка: {result.stderr[:200]}")
                    shutil.copy2(batch_outputs[0], output_path)
            
            progress_callback(100)
            log_callback("   ✅ Batch-склейка завершена")
            return output_path
            
        except Exception as e:
            log_callback(f"   ❌ Ошибка batch-склейки: {e}")
            return output_path
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as cleanup_err:
                logging.debug(f"Cleanup temp_dir failed: {cleanup_err}")
