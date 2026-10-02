#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Параллельная генерация через API для ускорения создания контента
Оптимизирует генерацию изображений, аудио и других API-зависимых компонентов
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Callable, Optional
import time
import threading
from pathlib import Path

# 🎯 Глобальный пул потоков для ограничения вложенной параллельности
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


class ParallelAPIGenerator:
    """Параллельная генерация контента через API с умным rate limiting"""
    
    def __init__(self, max_workers: int = 5, log_callback: Optional[Callable] = None):
        """
        Args:
            max_workers: Максимальное количество параллельных API запросов (рекомендуется 4-6)
            log_callback: Функция для логирования
        """
        self.max_workers = max(1, min(max_workers, 8))  # Ограничение 1-8 воркеров (увеличено)
        self.log_callback = log_callback or (lambda x: print(x))
        self.rate_limiter = threading.Semaphore(max_workers)
        self.log_lock = threading.Lock()
    
    def thread_safe_log(self, message: str):
        """Thread-safe логирование"""
        with self.log_lock:
            self.log_callback(message)
    
    def generate_images_parallel(
        self,
        image_prompts: List[str],
        image_generator,
        output_dir: Path,
        aspect_ratio: str,
        width: int,
        height: int,
        google_ai_api_key: str,
        image_model: str = "gemini-3.1-flash-image"
    ) -> List[Optional[str]]:
        """
        Параллельная генерация изображений через API.
        
        Args:
            image_prompts: Список промптов для генерации
            image_generator: Экземпляр GoogleImageGenerator
            output_dir: Директория для сохранения
            aspect_ratio: Соотношение сторон
            width: Ширина
            height: Высота
            google_ai_api_key: API ключ
            image_model: Модель для генерации
            
        Returns:
            Список путей к сгенерированным изображениям (None если ошибка)
        """
        self.thread_safe_log(f"🖼️ Параллельная генерация {len(image_prompts)} изображений ({self.max_workers} воркеров)...")
        
        start_time = time.time()
        results = [None] * len(image_prompts)
        
        def generate_single_image(index: int, prompt: str) -> tuple:
            """Генерация одного изображения с rate limiting"""
            with self.rate_limiter:  # Ограничиваем одновременные запросы
                try:
                    self.thread_safe_log(f"   🎨 Изображение {index + 1}/{len(image_prompts)}: начало генерации...")
                    
                    output_path = output_dir / f"ai_image_{index + 1}.png"
                    
                    # 🎯 Используем глобальный пул для ограничения вложенности
                    if _USE_GLOBAL_POOL:
                        with thread_slot('imagen', priority='medium'):
                            image_path = image_generator.generate_image(
                                prompt=prompt,
                                output_path=str(output_path),
                                aspect_ratio=aspect_ratio,
                                api_key=google_ai_api_key,
                                model=image_model
                            )
                    else:
                        image_path = image_generator.generate_image(
                            prompt=prompt,
                            output_path=str(output_path),
                            aspect_ratio=aspect_ratio,
                            api_key=google_ai_api_key,
                            model=image_model
                        )
                    
                    if image_path:
                        self.thread_safe_log(f"   ✅ Изображение {index + 1}: готово")
                        return (index, image_path)
                    else:
                        self.thread_safe_log(f"   ❌ Изображение {index + 1}: ошибка генерации")
                        return (index, None)
                        
                except Exception as e:
                    self.thread_safe_log(f"   ❌ Изображение {index + 1}: {str(e)[:100]}")
                    return (index, None)
        
        # Запускаем параллельную генерацию
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(generate_single_image, i, prompt): i 
                for i, prompt in enumerate(image_prompts)
            }
            
            completed = 0
            for future in as_completed(futures):
                try:
                    index, image_path = future.result(timeout=120)  # 2 минуты на изображение
                    results[index] = image_path
                    completed += 1
                    
                    progress = int((completed / len(image_prompts)) * 100)
                    self.thread_safe_log(f"   📊 Прогресс: {completed}/{len(image_prompts)} ({progress}%)")
                    
                except Exception as e:
                    self.thread_safe_log(f"   ⚠️ Ошибка обработки результата: {str(e)[:100]}")
        
        elapsed = time.time() - start_time
        successful = sum(1 for r in results if r is not None)
        
        self.thread_safe_log("✅ Генерация изображений завершена:")
        self.thread_safe_log(f"   • Успешно: {successful}/{len(image_prompts)}")
        self.thread_safe_log(f"   • Время: {elapsed:.1f}s ({elapsed/len(image_prompts):.1f}s/изображение)")
        self.thread_safe_log(f"   • Ускорение: ~{len(image_prompts)*10/elapsed:.1f}x vs последовательно")
        
        return results
    
    def generate_audio_chunks_parallel(
        self,
        text_chunks: List[str],
        audio_processor,
        output_dir: Path,
        provider: str,
        voice_name: str,
        api_key: str,
        speech_speed: float,
        language: str,
        is_vertical: bool,
        persona_id: str = None
    ) -> List[tuple]:
        """
        Параллельная генерация аудио чанков.
        
        Args:
            text_chunks: Список текстовых фрагментов
            audio_processor: Экземпляр AudioProcessor
            output_dir: Директория для сохранения
            provider: TTS провайдер
            voice_name: Имя голоса
            api_key: API ключ
            speech_speed: Скорость речи
            language: Язык
            is_vertical: Вертикальное ли видео
            
        Returns:
            Список кортежей (audio_path, duration)
        """
        if len(text_chunks) <= 1:
            # Если один чанк - не используем параллелизм
            return []
        
        self.thread_safe_log(f"🎤 Параллельная генерация {len(text_chunks)} аудио чанков ({self.max_workers} воркеров)...")
        
        start_time = time.time()
        results = [None] * len(text_chunks)
        
        def generate_single_chunk(index: int, text: str) -> tuple:
            """Генерация одного аудио чанка"""
            with self.rate_limiter:
                try:
                    self.thread_safe_log(f"   🎵 Чанк {index + 1}/{len(text_chunks)}: начало генерации...")
                    
                    # Генерируем аудио для чанка
                    chunk_path, chunk_duration = audio_processor.text_to_speech(
                        text=text,
                        output_dir=output_dir,
                        log_callback=self.thread_safe_log,
                        provider=provider,
                        voice_name=voice_name,
                        api_key=api_key,
                        speech_speed=speech_speed,
                        language=language,
                        is_vertical=is_vertical,
                        persona_id=persona_id
                    )
                    
                    if chunk_path and chunk_duration > 0:
                        self.thread_safe_log(f"   ✅ Чанк {index + 1}: готово ({chunk_duration:.1f}s)")
                        return (index, (chunk_path, chunk_duration))
                    else:
                        self.thread_safe_log(f"   ❌ Чанк {index + 1}: ошибка генерации")
                        return (index, None)
                        
                except Exception as e:
                    self.thread_safe_log(f"   ❌ Чанк {index + 1}: {str(e)[:100]}")
                    return (index, None)
        
        # Запускаем параллельную генерацию
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(generate_single_chunk, i, chunk): i 
                for i, chunk in enumerate(text_chunks)
            }
            
            completed = 0
            for future in as_completed(futures):
                try:
                    index, result = future.result(timeout=180)  # 3 минуты на чанк
                    results[index] = result
                    completed += 1
                    
                    progress = int((completed / len(text_chunks)) * 100)
                    self.thread_safe_log(f"   📊 Прогресс: {completed}/{len(text_chunks)} ({progress}%)")
                    
                except Exception as e:
                    self.thread_safe_log(f"   ⚠️ Ошибка обработки результата: {str(e)[:100]}")
        
        elapsed = time.time() - start_time
        successful = sum(1 for r in results if r is not None)
        
        self.thread_safe_log("✅ Генерация аудио завершена:")
        self.thread_safe_log(f"   • Успешно: {successful}/{len(text_chunks)}")
        self.thread_safe_log(f"   • Время: {elapsed:.1f}s")
        
        return results
    
    def batch_api_calls(
        self,
        tasks: List[Dict[str, Any]],
        task_function: Callable,
        task_name: str = "задача"
    ) -> List[Any]:
        """
        Универсальный метод для параллельного выполнения API запросов.
        
        Args:
            tasks: Список задач (словарей с параметрами)
            task_function: Функция для выполнения (принимает **task_params)
            task_name: Название задачи для логирования
            
        Returns:
            Список результатов
        """
        self.thread_safe_log(f"⚡ Параллельное выполнение {len(tasks)} {task_name} ({self.max_workers} воркеров)...")
        
        start_time = time.time()
        results = [None] * len(tasks)
        
        def execute_task(index: int, task_params: Dict[str, Any]) -> tuple:
            """Выполнение одной задачи"""
            with self.rate_limiter:
                try:
                    self.thread_safe_log(f"   ⚙️ {task_name.capitalize()} {index + 1}/{len(tasks)}: начало...")
                    
                    result = task_function(**task_params)
                    
                    self.thread_safe_log(f"   ✅ {task_name.capitalize()} {index + 1}: готово")
                    return (index, result)
                    
                except Exception as e:
                    self.thread_safe_log(f"   ❌ {task_name.capitalize()} {index + 1}: {str(e)[:100]}")
                    return (index, None)
        
        # Запускаем параллельное выполнение
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(execute_task, i, task): i 
                for i, task in enumerate(tasks)
            }
            
            completed = 0
            for future in as_completed(futures):
                try:
                    index, result = future.result(timeout=300)  # 5 минут на задачу
                    results[index] = result
                    completed += 1
                    
                    progress = int((completed / len(tasks)) * 100)
                    self.thread_safe_log(f"   📊 Прогресс: {completed}/{len(tasks)} ({progress}%)")
                    
                except Exception as e:
                    self.thread_safe_log(f"   ⚠️ Ошибка обработки результата: {str(e)[:100]}")
        
        elapsed = time.time() - start_time
        successful = sum(1 for r in results if r is not None)
        
        self.thread_safe_log("✅ Выполнение завершено:")
        self.thread_safe_log(f"   • Успешно: {successful}/{len(tasks)}")
        self.thread_safe_log(f"   • Время: {elapsed:.1f}s ({elapsed/len(tasks):.1f}s/{task_name})")
        
        return results

    def generate_batch_prompts(
        self,
        themes: List[str],
        api_key: str,
        num_prompts_per_theme: int = 1
    ) -> Dict[str, List[str]]:
        """
        🚀 BATCH генерация промптов - один API вызов для всех тем.
        Экономит до 80% времени на API вызовах для промптов.
        
        Args:
            themes: Список тем для генерации промптов
            api_key: API ключ Gemini
            num_prompts_per_theme: Количество промптов на тему
            
        Returns:
            Словарь {тема: [список промптов]}
        """
        import requests
        import json
        
        self.thread_safe_log(f"🚀 BATCH генерация промптов для {len(themes)} тем...")
        
        # JSON Schema для batch промптов
        batch_schema = {
            "type": "object",
            "properties": {
                "prompts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "theme": {"type": "string"},
                            "prompt": {"type": "string"}
                        },
                        "required": ["theme", "prompt"]
                    }
                }
            },
            "required": ["prompts"]
        }
        
        themes_list = "\n".join([f"{i+1}. {theme}" for i, theme in enumerate(themes)])
        
        system_prompt = f"""Generate {num_prompts_per_theme} ULTRA-PHOTOREALISTIC image prompt(s) for EACH of these themes:

{themes_list}

For EACH theme, create a detailed cinematic prompt with:
- IMAX 70mm quality description
- Micro-level details (textures, lighting, atmosphere)
- Camera and lens specifications
- Color palette and mood

Return JSON with array of objects, each having "theme" (original theme) and "prompt" (detailed prompt).
Write prompts in ENGLISH. NO text/watermarks in images."""

        try:
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
            headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
            
            payload = {
                "contents": [{"role": "user", "parts": [{"text": system_prompt}]}],
                "generationConfig": {
                    "temperature": 0.8,
                    "maxOutputTokens": 8192,
                    "responseMimeType": "application/json",
                    "responseSchema": batch_schema
                }
            }
            
            start_time = time.time()
            resp = requests.post(url, json=payload, headers=headers, timeout=120)
            
            if resp.status_code != 200:
                raise Exception(f"API error {resp.status_code}")
            
            data = resp.json()
            candidates = data.get('candidates', [])
            if not candidates:
                raise Exception("No candidates")
            
            parts = candidates[0].get('content', {}).get('parts', [])
            response_text = "".join([p.get('text', '') for p in parts]).strip()
            response_text = response_text.replace('\n', ' ').replace('\r', '')
            
            result = json.loads(response_text)
            prompts_list = result.get('prompts', [])
            
            # Группируем по темам
            prompts_by_theme = {}
            for item in prompts_list:
                theme = item.get('theme', '')
                prompt = item.get('prompt', '')
                if theme and prompt:
                    if theme not in prompts_by_theme:
                        prompts_by_theme[theme] = []
                    prompts_by_theme[theme].append(prompt)
            
            elapsed = time.time() - start_time
            self.thread_safe_log(f"✅ BATCH промпты готовы: {len(prompts_list)} промптов за {elapsed:.1f}s")
            self.thread_safe_log(f"   💡 Экономия: ~{len(themes) * 2 - elapsed:.1f}s vs последовательно")
            
            return prompts_by_theme
            
        except Exception as e:
            self.thread_safe_log(f"⚠️ BATCH генерация не удалась: {e}")
            return {}

    def run_parallel_stages(
        self,
        stages: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        🚀 Параллельное выполнение независимых этапов генерации.
        
        Args:
            stages: Список этапов [{name, function, args, kwargs}]
            
        Returns:
            Словарь {stage_name: result}
        """
        self.thread_safe_log(f"🚀 Параллельное выполнение {len(stages)} этапов...")
        
        start_time = time.time()
        results = {}
        
        def run_stage(stage: Dict[str, Any]) -> tuple:
            """Выполнение одного этапа"""
            name = stage.get('name', 'unknown')
            func = stage.get('function')
            args = stage.get('args', [])
            kwargs = stage.get('kwargs', {})
            
            try:
                self.thread_safe_log(f"   🔄 {name}: запуск...")
                result = func(*args, **kwargs)
                self.thread_safe_log(f"   ✅ {name}: готово")
                return (name, result)
            except Exception as e:
                self.thread_safe_log(f"   ❌ {name}: {str(e)[:100]}")
                return (name, None)
        
        with ThreadPoolExecutor(max_workers=len(stages)) as executor:
            futures = [executor.submit(run_stage, stage) for stage in stages]
            
            for future in as_completed(futures):
                try:
                    name, result = future.result(timeout=600)
                    results[name] = result
                except Exception as e:
                    self.thread_safe_log(f"   ⚠️ Ошибка этапа: {str(e)[:100]}")
        
        elapsed = time.time() - start_time
        self.thread_safe_log(f"✅ Все этапы завершены за {elapsed:.1f}s")
        
        return results
