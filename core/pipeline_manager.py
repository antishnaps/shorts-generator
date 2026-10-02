#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline Manager - координатор параллельной генерации видео.

Реализует pipeline parallelism:
- Пока рендерится видео N, готовится контент для видео N+1
- Разделение на этапы: prepare_content → render_video
- Очередь готового контента с backpressure

Архитектура:
```
Видео 1: [Prepare ████] → [Render ████████████████]
Видео 2:          [Prepare ████] → ждёт GPU → [Render ████████]
Видео 3:                   [Prepare ████] → [Render ████]
```

Ускорение: ~30-40% на батчах из 5+ видео
"""

import threading
import queue
import time
from typing import Dict, List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass


@dataclass
class VideoContent:
    """Подготовленный контент для рендеринга."""
    video_num: int
    theme: str
    text_content: Dict
    audio_path: str
    audio_duration: float
    image_paths: List[str]
    youtube_clips: List[str]
    word_timestamps: Optional[List] = None
    output_path: Optional[str] = None
    error: Optional[str] = None


@dataclass 
class PipelineStats:
    """Статистика pipeline."""
    videos_prepared: int = 0
    videos_rendered: int = 0
    videos_failed: int = 0
    total_prepare_time: float = 0
    total_render_time: float = 0
    overlap_time: float = 0  # Время когда prepare и render работали параллельно


class PipelineManager:
    """
    Менеджер pipeline для параллельной генерации видео.
    
    Использование:
    ```python
    pipeline = PipelineManager(
        prepare_func=generator._prepare_video_content,
        render_func=generator._render_video,
        max_prepared=2,  # Максимум 2 видео в очереди на рендер
        log_callback=log
    )
    
    # Добавляем задачи
    for i, theme in enumerate(themes):
        pipeline.submit(video_num=i+1, theme=theme, **kwargs)
    
    # Ждём завершения
    results = pipeline.wait_all()
    ```
    """
    
    def __init__(
        self,
        prepare_func: Callable,
        render_func: Callable,
        max_prepared: int = 2,
        log_callback: Callable = None
    ):
        """
        Args:
            prepare_func: Функция подготовки контента (text, audio, images)
            render_func: Функция рендеринга видео
            max_prepared: Максимум подготовленных видео в очереди (backpressure)
            log_callback: Функция логирования
        """
        self.prepare_func = prepare_func
        self.render_func = render_func
        self.max_prepared = max_prepared
        self.log = log_callback or print
        
        # Очередь подготовленного контента
        self._prepared_queue: queue.Queue[VideoContent] = queue.Queue(maxsize=max_prepared)
        
        # Статистика
        self.stats = PipelineStats()
        
        # Управление потоками
        self._prepare_executor: Optional[ThreadPoolExecutor] = None
        self._render_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._all_submitted = threading.Event()
        
        # Результаты
        self._results: Dict[int, str] = {}  # video_num -> output_path
        self._results_lock = threading.Lock()
        
        # Отслеживание времени для overlap
        self._prepare_active = threading.Event()
        self._render_active = threading.Event()
        self._overlap_start: Optional[float] = None

    def start(self):
        """Запускает pipeline."""
        self._stop_event.clear()
        self._all_submitted.clear()
        
        # Запускаем render worker в отдельном потоке
        self._render_thread = threading.Thread(target=self._render_worker, daemon=True)
        self._render_thread.start()
        
        # Executor для prepare задач
        self._prepare_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prepare")
        
        self.log("🚀 Pipeline запущен")

    def submit(self, video_num: int, theme: str, **kwargs) -> Future:
        """
        Отправляет видео на подготовку.
        
        Args:
            video_num: Номер видео
            theme: Тема видео
            **kwargs: Дополнительные параметры для prepare_func
            
        Returns:
            Future для отслеживания завершения prepare
        """
        if self._prepare_executor is None:
            self.start()
        
        future = self._prepare_executor.submit(
            self._prepare_and_queue,
            video_num, theme, kwargs
        )
        return future

    def _prepare_and_queue(self, video_num: int, theme: str, kwargs: Dict):
        """Подготавливает контент и добавляет в очередь."""
        start_time = time.time()
        self._prepare_active.set()
        self._check_overlap()
        
        try:
            self.log(f"📝 Prepare #{video_num}: '{theme[:30]}...'")
            
            # Вызываем функцию подготовки
            content = self.prepare_func(video_num, theme, **kwargs)
            
            if content and not content.error:
                # Добавляем в очередь (блокируется если очередь полная - backpressure)
                self._prepared_queue.put(content, timeout=600)  # 10 мин таймаут
                self.stats.videos_prepared += 1
                
                elapsed = time.time() - start_time
                self.stats.total_prepare_time += elapsed
                self.log(f"✅ Prepare #{video_num} готов за {elapsed:.1f}s (в очереди: {self._prepared_queue.qsize()})")
            else:
                error_msg = content.error if content else "Unknown error"
                self.log(f"❌ Prepare #{video_num} ошибка: {error_msg}")
                self.stats.videos_failed += 1
                
        except queue.Full:
            self.log(f"⚠️ Prepare #{video_num}: очередь переполнена (таймаут)")
            self.stats.videos_failed += 1
        except Exception as e:
            self.log(f"❌ Prepare #{video_num} исключение: {e}")
            self.stats.videos_failed += 1
        finally:
            self._prepare_active.clear()
            self._check_overlap()

    def _render_worker(self):
        """Воркер рендеринга - берёт контент из очереди и рендерит."""
        while not self._stop_event.is_set():
            try:
                # Ждём контент из очереди
                content = self._prepared_queue.get(timeout=1.0)
            except queue.Empty:
                # Проверяем не пора ли завершаться
                if self._all_submitted.is_set() and self._prepared_queue.empty():
                    break
                continue
            
            start_time = time.time()
            self._render_active.set()
            self._check_overlap()
            
            try:
                self.log(f"🎬 Render #{content.video_num}: '{content.theme[:30]}...'")
                
                # Вызываем функцию рендеринга
                output_path = self.render_func(content)
                
                if output_path:
                    with self._results_lock:
                        self._results[content.video_num] = output_path
                    self.stats.videos_rendered += 1
                    
                    elapsed = time.time() - start_time
                    self.stats.total_render_time += elapsed
                    self.log(f"✅ Render #{content.video_num} готов за {elapsed:.1f}s")
                else:
                    self.log(f"❌ Render #{content.video_num} не создал файл")
                    self.stats.videos_failed += 1
                    
            except Exception as e:
                self.log(f"❌ Render #{content.video_num} исключение: {e}")
                self.stats.videos_failed += 1
            finally:
                self._render_active.clear()
                self._check_overlap()
                self._prepared_queue.task_done()

    def _check_overlap(self):
        """Отслеживает время когда prepare и render работают параллельно."""
        both_active = self._prepare_active.is_set() and self._render_active.is_set()
        
        if both_active and self._overlap_start is None:
            self._overlap_start = time.time()
        elif not both_active and self._overlap_start is not None:
            self.stats.overlap_time += time.time() - self._overlap_start
            self._overlap_start = None

    def mark_all_submitted(self):
        """Сигнализирует что все задачи отправлены."""
        self._all_submitted.set()

    def wait_all(self, timeout: float = None) -> Dict[int, str]:
        """
        Ждёт завершения всех задач.
        
        Args:
            timeout: Таймаут в секундах (None = бесконечно)
            
        Returns:
            Dict[video_num, output_path]
        """
        self.mark_all_submitted()
        
        # Ждём завершения prepare executor
        if self._prepare_executor:
            self._prepare_executor.shutdown(wait=True)
        
        # Ждём завершения render thread
        if self._render_thread:
            self._render_thread.join(timeout=timeout)
        
        return self._results.copy()

    def stop(self):
        """Останавливает pipeline."""
        self._stop_event.set()
        self._all_submitted.set()
        
        if self._prepare_executor:
            self._prepare_executor.shutdown(wait=False)
        
        if self._render_thread and self._render_thread.is_alive():
            self._render_thread.join(timeout=5)

    def get_stats_summary(self) -> str:
        """Возвращает сводку статистики."""
        s = self.stats
        total_time = s.total_prepare_time + s.total_render_time - s.overlap_time
        
        lines = [
            "📊 Pipeline статистика:",
            f"   Подготовлено: {s.videos_prepared}",
            f"   Отрендерено: {s.videos_rendered}",
            f"   Ошибок: {s.videos_failed}",
            f"   Время prepare: {s.total_prepare_time:.1f}s",
            f"   Время render: {s.total_render_time:.1f}s",
            f"   Overlap (экономия): {s.overlap_time:.1f}s",
        ]
        
        if s.overlap_time > 0 and total_time > 0:
            speedup = (s.total_prepare_time + s.total_render_time) / total_time
            lines.append(f"   🚀 Ускорение: {speedup:.2f}x")
        
        return '\n'.join(lines)



def create_simple_pipeline(
    themes: List[str],
    prepare_func: Callable,
    render_func: Callable,
    log_callback: Callable = None,
    max_prepared: int = 2
) -> Dict[int, str]:
    """
    Простой интерфейс для pipeline генерации.
    
    Args:
        themes: Список тем для генерации
        prepare_func: Функция подготовки (video_num, theme, **kwargs) -> VideoContent
        render_func: Функция рендеринга (VideoContent) -> output_path
        log_callback: Функция логирования
        max_prepared: Максимум видео в очереди
        
    Returns:
        Dict[video_num, output_path]
        
    Example:
        ```python
        results = create_simple_pipeline(
            themes=['тема1', 'тема2', 'тема3'],
            prepare_func=lambda n, t, **kw: prepare_video(n, t, kw),
            render_func=lambda c: render_video(c),
            log_callback=print
        )
        ```
    """
    log = log_callback or print
    
    if len(themes) < 2:
        log("⚠️ Pipeline не эффективен для < 2 видео, используем последовательную генерацию")
        # Fallback на последовательную генерацию
        results = {}
        for i, theme in enumerate(themes):
            video_num = i + 1
            content = prepare_func(video_num, theme)
            if content and not content.error:
                output = render_func(content)
                if output:
                    results[video_num] = output
        return results
    
    pipeline = PipelineManager(
        prepare_func=prepare_func,
        render_func=render_func,
        max_prepared=max_prepared,
        log_callback=log
    )
    
    log(f"\n{'='*60}")
    log(f"🚀 PIPELINE PARALLELISM: {len(themes)} видео")
    log(f"{'='*60}")
    
    start_time = time.time()
    
    # Отправляем все задачи
    for i, theme in enumerate(themes):
        pipeline.submit(video_num=i+1, theme=theme)
    
    # Ждём завершения
    results = pipeline.wait_all()
    
    elapsed = time.time() - start_time
    
    log(f"\n{'='*60}")
    log(pipeline.get_stats_summary())
    log(f"   Общее время: {elapsed:.1f}s ({elapsed/60:.1f} мин)")
    log(f"{'='*60}\n")
    
    return results
