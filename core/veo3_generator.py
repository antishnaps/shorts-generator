#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Veo 3.1 API client for AI video generation
V2: Global rate limiting to prevent 429 errors
"""

import time
import json
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional, List
from google import genai
from google.genai import types
from PIL import Image
import io

from core.logging_utils import get_default_logger
from core.gemini_models import DEFAULT_VEO_MODEL, VEO_MODEL_CHAIN
from core.process_registry import run_registered

# 🚦 ГЛОБАЛЬНЫЙ СЕМАФОР ДЛЯ VEO 3
# Veo 3 имеет лимит ~5 RPM, поэтому ограничиваем до 1 одновременного запроса
# и добавляем БОЛЬШОЙ интервал между запросами для стабильности
_GLOBAL_VEO3_SEMAPHORE = threading.Semaphore(1)  # Только 1 одновременный запрос
_VEO3_LOCK = threading.Lock()
_LAST_VEO3_REQUEST_TIME = 0.0
_VEO3_MIN_INTERVAL = 45.0  # 45 секунд между запросами (~1.3 RPM) - стабильность важнее скорости


def enforce_veo3_rate_limit(log_callback=None):
    """
    Enforce minimum interval between Veo 3 requests.
    Call this BEFORE making a Veo 3 request.
    """
    global _LAST_VEO3_REQUEST_TIME
    with _VEO3_LOCK:
        now = time.time()
        elapsed = now - _LAST_VEO3_REQUEST_TIME
        if elapsed < _VEO3_MIN_INTERVAL:
            wait_time = _VEO3_MIN_INTERVAL - elapsed
            if log_callback:
                log_callback(f"   🚦 Veo3 rate limit: ожидание {wait_time:.1f}s...")
            time.sleep(wait_time)
        _LAST_VEO3_REQUEST_TIME = time.time()


def get_veo3_semaphore() -> threading.Semaphore:
    """Get global Veo 3 semaphore for rate limiting"""
    return _GLOBAL_VEO3_SEMAPHORE


class Veo3Generator:
    """
    Client for Veo 3.1 API
    Generates AI videos from reference images
    """
    
    def __init__(self, model: str = None):
        # Veo 3.1 model family, selectable from the GUI.
        self.model = model if model in VEO_MODEL_CHAIN else DEFAULT_VEO_MODEL
        self.client = None
        
    # Алиас для обратной совместимости (статический метод заменен на переменную)
    _dummy_log = get_default_logger()
    
    def generate_video_from_images(
        self,
        reference_images: List[str],  # Paths to reference images
        prompt: str,
        api_key: str,
        output_path: str,
        duration: int = 5,
        resolution: str = "1080p",
        fps: int = 24,
        aspect_ratio: str = "9:16",
        theme: str = "",
        log_callback: Callable[[str], None] = None
    ) -> Optional[str]:
        """
        Generate video from reference images using Veo 3.1
        
        Args:
            reference_images: List of paths to reference images (1-3 images)
            prompt: Text prompt describing the desired video
            api_key: Google AI API key
            output_path: Path to save the generated video
            duration: Video duration in seconds (4, 6, or 8)
            resolution: "720p" or "1080p"
            fps: Frames per second (24 recommended)
            aspect_ratio: "9:16" (vertical) or "16:9" (horizontal)
            log_callback: Callback for logging
            
        Returns:
            Path to generated video or None if failed
        """
        log_callback = log_callback or get_default_logger()
        log_callback("🎬 Генерация AI-видео через Veo 3.1...")
        log_callback(f"   📊 Параметры: {duration}s, {resolution}, {aspect_ratio}")
        log_callback(f"   🖼️ Референсных кадров: {len(reference_images)}")
        
        try:
            # Initialize client
            if not self.client:
                self.client = genai.Client(api_key=api_key)
            
            # Validate duration (Veo 3.1 supports 4, 6, 8 seconds)
            if duration not in [4, 6, 8]:
                log_callback(f"   ⚠️ Длительность {duration}s не поддерживается, используем 8s")
                duration = 8
            
            # IMAGE-TO-VIDEO: Правильное решение для SDK 1.52.0
            log_callback("   📸 Загрузка изображения для анимации...")
            
            # Загружаем первое изображение
            first_image_path = reference_images[0]
            pil_img = Image.open(first_image_path)
            if pil_img.mode != 'RGB':
                pil_img = pil_img.convert('RGB')
            
            log_callback(f"   ✅ Изображение загружено: {pil_img.size}, {pil_img.mode}")
            
            # Конвертируем в RAW bytes (НЕ base64!)
            buffered = io.BytesIO()
            pil_img.save(buffered, format='PNG')
            img_raw_bytes = buffered.getvalue()
            buffered.close()  # Закрываем буфер
            
            # ПРАВИЛЬНЫЙ способ для SDK 1.52.0: types.Image с RAW BYTES
            image_input = types.Image(
                image_bytes=img_raw_bytes,  # RAW bytes, не base64!
                mime_type="image/png"
            )
            
            log_callback(f"   ✅ types.Image создан: {len(img_raw_bytes)} raw bytes")
            
            # Build config для image-to-video
            config = types.GenerateVideosConfig(
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                duration_seconds=duration,
                person_generation="ALLOW_ADULT"
            )
            
            log_callback("   🚀 Отправка запроса в Veo 3.1 (image-to-video)...")
            
            # 🚦 ГЛОБАЛЬНЫЙ RATE LIMITING для Veo 3
            # Используем семафор + минимальный интервал
            global_semaphore = get_veo3_semaphore()
            
            # 🔧 FIX: Retry ВЫНЕСЕН за пределы семафора чтобы не блокировать другие запросы
            max_retries = 8
            retry_delay = 60  # 60 seconds base delay - stability over speed
            operation = None
            
            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        log_callback(f"   🔄 Попытка {attempt + 1}/{max_retries}...")
                    
                    # Семафор захватывается ТОЛЬКО на время API вызова
                    with global_semaphore:
                        # Enforce minimum interval between requests
                        enforce_veo3_rate_limit(log_callback)
                        
                        # Apply additional rate limiting from rate_limiter module
                        try:
                            from core.rate_limiter import get_global_throttler
                            throttler = get_global_throttler(log_callback=log_callback)
                            throttler.throttle('veo3')
                        except Exception:
                            pass  # Continue without additional throttling
                        
                        operation = self.client.models.generate_videos(
                            model=self.model,
                            prompt=prompt,
                            image=image_input,  # types.Image с raw bytes!
                            config=config
                        )
                    break  # Success, exit retry loop
                    
                except Exception as api_error:
                    error_str = str(api_error)
                    
                    # Check for rate limit (429)
                    if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
                        if attempt < max_retries - 1:
                            # Longer delays for stability: 60s, 90s, 120s, 150s, 180s...
                            wait_time = min(retry_delay + (30 * attempt), 300)
                            log_callback(f"   ⏸️ Rate limit (429) - ожидание {wait_time}s перед повтором...")
                            # 🔧 FIX: sleep ПОСЛЕ освобождения семафора
                            time.sleep(wait_time)
                            continue
                        else:
                            log_callback(f"   ❌ Квота API исчерпана после {max_retries} попыток")
                            log_callback("   💡 Решения:")
                            log_callback("      1. Подождите 5-10 минут")
                            log_callback("      2. Проверьте лимиты на https://aistudio.google.com/")
                            raise api_error
                    
                    # Other errors - retry
                    if attempt < max_retries - 1:
                        log_callback(f"   ⚠️ Ошибка: {str(api_error)[:100]} - повтор...")
                        # 🔧 FIX: sleep ПОСЛЕ освобождения семафора
                        time.sleep(retry_delay)
                        continue
                    else:
                        log_callback(f"   ❌ Не удалось сгенерировать после {max_retries} попыток")
                        raise api_error
            
            if not operation:
                log_callback("   ❌ Не удалось создать операцию генерации")
                return None
            
            # Poll until done
            log_callback("   ⏳ Ожидание генерации (может занять до 6 минут)...")
            poll_count = 0
            max_polls = 36  # 6 minutes with 10s intervals
            
            while not operation.done and poll_count < max_polls:
                time.sleep(10)
                operation = self.client.operations.get(operation)
                poll_count += 1
                
                if poll_count % 3 == 0:  # Log every 30 seconds
                    log_callback(f"   ⏳ Генерация... ({poll_count * 10}s)")
            
            if not operation.done:
                log_callback("   ❌ Timeout: генерация не завершилась за 6 минут")
                return None
            
            # Check for errors
            if hasattr(operation, 'error') and operation.error:
                log_callback(f"   ❌ Ошибка API: {operation.error}")
                return None
            
            # Get generated video
            if not operation.response or not operation.response.generated_videos:
                log_callback("   ❌ Veo 3.1 не вернул видео")
                return None
            
            generated_video = operation.response.generated_videos[0]
            
            # Download video
            log_callback("   💾 Скачивание видео...")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            
            self.client.files.download(file=generated_video.video)
            generated_video.video.save(output_path)
            
            # Validate generated video
            if not Path(output_path).exists():
                log_callback("   ❌ Файл не был сохранён")
                return None
            
            file_size = Path(output_path).stat().st_size / 1024 / 1024
            
            # Check minimum file size (corrupted videos are usually tiny)
            if file_size < 0.1:  # Less than 100KB
                log_callback(f"   ❌ Видео повреждено (размер {file_size:.2f} MB слишком мал)")
                try:
                    if Path(output_path).exists():
                        Path(output_path).unlink()  # Delete corrupted file
                except Exception as e:
                    log_callback(f"   ⚠️ Не удалось удалить повреждённый файл: {e}")
                return None
            
            # Validate video with ffprobe
            try:
                probe_cmd = [
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_format',
                    '-show_streams',
                    output_path
                ]
                
                result = run_registered(
                    probe_cmd, 
                    label="ffprobe_veo3_generated_video",
                    capture_output=True, 
                    text=True, 
                    encoding='utf-8',
                    errors='ignore',
                    timeout=30  # Увеличен timeout для больших файлов
                )
                
                if result.returncode != 0:
                    log_callback("   ❌ Видео повреждено (ffprobe failed)")
                    try:
                        if Path(output_path).exists():
                            Path(output_path).unlink()
                    except Exception as e:
                        log_callback(f"   ⚠️ Не удалось удалить повреждённый файл: {e}")
                    return None
                
                data = json.loads(result.stdout)
                
                # Check if video has valid streams
                streams = data.get('streams')
                if not streams or not isinstance(streams, list):
                    log_callback("   ❌ Видео не содержит потоков")
                    try:
                        if Path(output_path).exists():
                            Path(output_path).unlink()
                    except Exception as e:
                        log_callback(f"   ⚠️ Не удалось удалить повреждённый файл: {e}")
                    return None
                
                # Check duration
                format_data = data.get('format', {})
                if not isinstance(format_data, dict):
                    log_callback("   ⚠️ Некорректный формат данных ffprobe")
                    log_callback(f"   ✅ AI-видео сгенерировано: {file_size:.2f} MB")
                    return output_path
                
                duration_str = format_data.get('duration', '0')
                try:
                    video_duration = float(duration_str)
                except (ValueError, TypeError):
                    log_callback("   ⚠️ Не удалось определить длительность видео")
                    log_callback(f"   ✅ AI-видео сгенерировано: {file_size:.2f} MB")
                    return output_path
                
                if video_duration < 1:
                    log_callback(f"   ❌ Видео слишком короткое ({video_duration:.1f}s)")
                    try:
                        if Path(output_path).exists():
                            Path(output_path).unlink()
                    except Exception as e:
                        log_callback(f"   ⚠️ Не удалось удалить повреждённый файл: {e}")
                    return None
                
                log_callback(f"   ✅ AI-видео сгенерировано: {file_size:.2f} MB, {video_duration:.1f}s")
                
            except subprocess.TimeoutExpired:
                log_callback("   ⚠️ Timeout валидации (>30s), но файл существует")
                log_callback(f"   ✅ AI-видео сгенерировано: {file_size:.2f} MB")
            except json.JSONDecodeError as e:
                log_callback(f"   ⚠️ Ошибка парсинга JSON от ffprobe: {e}")
                log_callback(f"   ✅ AI-видео сгенерировано: {file_size:.2f} MB")
            except Exception as validation_error:
                log_callback(f"   ⚠️ Не удалось валидировать видео: {validation_error}")
                # Continue anyway - file exists and has reasonable size
                log_callback(f"   ✅ AI-видео сгенерировано: {file_size:.2f} MB")
            
            return output_path
            
        except Exception as e:
            log_callback(f"   ❌ Ошибка генерации Veo 3.1: {str(e)[:200]}")
            import traceback
            log_callback(f"   🔍 Traceback: {traceback.format_exc()[:500]}")
            return None
    
    def generate_intro_video(
        self,
        theme: str,
        intro_text: str,
        reference_images: List[str],
        api_key: str,
        output_path: str,
        duration: int = 5,
        style: str = "auto",
        resolution: str = "1080p",
        fps: int = 24,
        aspect_ratio: str = "9:16",
        log_callback: Callable[[str], None] = None
    ) -> Optional[str]:
        """
        Generate intro video with automatic prompt generation
        
        Args:
            theme: Main theme of the video
            intro_text: Text for the intro segment
            reference_images: List of reference image paths
            api_key: Google AI API key
            output_path: Output video path
            duration: Duration in seconds (4, 6, or 8)
            style: Camera style ("auto", "shocked", "curious", "dramatic", "dynamic")
            resolution: "720p" or "1080p"
            fps: 24 (recommended for Veo 3.1)
            aspect_ratio: "9:16" or "16:9"
            log_callback: Logging callback
            
        Returns:
            Path to generated video or None
        """
        
        # Adjust duration to supported values
        # ВАЖНО: 1080p требует 8 секунд!
        if resolution == "1080p":
            if duration != 8:
                log_callback(f"   ⚠️ 1080p требует 8 секунд, изменяем с {duration}s на 8s")
                duration = 8
        else:
            # 720p поддерживает 4, 6, 8 секунд
            if duration <= 4:
                duration = 4
            elif duration <= 6:
                duration = 6
            else:
                duration = 8
        
        # Generate Veo 3.1 prompt (with AI JSON Mode if api_key available)
        prompt = self._generate_veo3_prompt(intro_text, style, log_callback, api_key)
        
        log_callback(f"   📝 Промпт для Veo 3.1: {prompt[:100]}...")
        
        # Generate video
        return self.generate_video_from_images(
            reference_images=reference_images,
            prompt=prompt,
            api_key=api_key,
            output_path=output_path,
            duration=duration,
            resolution=resolution,
            fps=fps,
            aspect_ratio=aspect_ratio,
            theme=theme,
            log_callback=log_callback
        )
    
    def _generate_veo3_prompt(
        self,
        intro_text: str,
        style: str = "auto",
        log_callback: Callable[[str], None] = None,
        api_key: str = None
    ) -> str:
        """
        Generate optimized prompt for Veo 3 based on intro text.
        Uses AI JSON Mode when api_key is provided.
        """
        
        # Пробуем AI-генерацию через JSON Mode
        if api_key:
            try:
                ai_prompt = self._generate_ai_veo3_prompt(intro_text, style, api_key, log_callback)
                if ai_prompt:
                    return ai_prompt
            except Exception as e:
                log_callback(f"   ⚠️ AI генерация промпта не удалась: {e}")
        
        # Fallback: базовая генерация
        base_prompt = (
            "Professional cinematic video with smooth camera movement. "
            "High quality cinematography, 4K detail, perfect lighting. "
            "NO text overlays, NO watermarks, NO logos, NO subtitles."
        )
        
        camera_movements = {
            "shocked": (
                "Dramatic fast zoom in, intense camera push towards subject, "
                "sudden focus shift, energetic movement, shocking reveal"
            ),
            "curious": (
                "Slow smooth dolly in, gentle approach, gradual reveal, "
                "contemplative pacing, building curiosity"
            ),
            "dramatic": (
                "Sweeping crane shot, epic cinematic movement, "
                "grand scale reveal, dramatic lighting change"
            ),
            "dynamic": (
                "Quick dynamic camera movements, energetic cuts, "
                "fast-paced action, multiple angle shifts"
            )
        }
        
        if style == "auto":
            intro_upper = intro_text.upper()
            
            if any(word in intro_upper for word in ["ЧТО БУДЕТ", "ЕСЛИ", "WHAT IF"]):
                style = "curious"
                log_callback("   🎬 Авто-стиль: Любопытство")
            elif any(word in intro_upper for word in ["ШОКИРУЮЩ", "НЕ ПОВЕРИТЕ", "SHOCKING"]):
                style = "shocked"
                log_callback("   🎬 Авто-стиль: Шок")
            elif any(word in intro_upper for word in ["СЕКРЕТ", "ПРАВДА", "SECRET", "TRUTH"]):
                style = "dramatic"
                log_callback("   🎬 Авто-стиль: Драма")
            else:
                style = "dynamic"
                log_callback("   🎬 Авто-стиль: Динамика")
        
        camera_prompt = camera_movements.get(style, camera_movements["dynamic"])
        return f"{base_prompt} {camera_prompt}"
    
    def _generate_ai_veo3_prompt(
        self,
        intro_text: str,
        style: str,
        api_key: str,
        log_callback: Callable[[str], None] = None
    ) -> str:
        """AI-генерация промпта для Veo 3 через Gemini JSON Mode."""
        import requests
        
        veo3_prompt_schema = {
            "type": "object",
            "properties": {
                "main_prompt": {"type": "string", "description": "Main video description"},
                "camera_work": {"type": "string", "description": "Camera movements"},
                "visual_style": {"type": "string", "description": "Visual aesthetics"},
                "lighting": {"type": "string", "description": "Lighting description"},
                "mood": {"type": "string", "description": "Video mood/atmosphere"}
            },
            "required": ["main_prompt"]
        }
        
        ai_system_prompt = f"""Create a detailed Veo 3 video prompt based on this intro text:
"{intro_text}"

Style preference: {style}

Requirements:
- main_prompt: Main scene description (50-100 words), visual elements, actions
- camera_work: Camera movements (zoom, pan, dolly, crane, tracking)
- visual_style: Cinematic style, color palette, composition
- lighting: Lighting setup (natural, dramatic, soft, etc.)
- mood: Atmosphere (epic, mysterious, energetic, calm)

Write in ENGLISH for Veo 3!
Focus on VISUAL details, not text content.
NO text overlays, NO watermarks, NO logos in the video."""
        
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": ai_system_prompt}]}],
            "generationConfig": {
                "temperature": 0.8,
                "maxOutputTokens": 1024,
                "responseMimeType": "application/json",
                "responseSchema": veo3_prompt_schema
            }
        }
        
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise Exception(f"API error {resp.status_code}")
        
        data = resp.json()
        candidates = data.get('candidates', [])
        if not candidates:
            raise Exception("No candidates")
        
        parts = candidates[0].get('content', {}).get('parts', [])
        response_text = "".join([p.get('text', '') for p in parts]).strip()
        
        result = json.loads(response_text)
        
        final_prompt = result.get('main_prompt', '')
        if result.get('camera_work'):
            final_prompt += f" CAMERA: {result['camera_work']}."
        if result.get('visual_style'):
            final_prompt += f" STYLE: {result['visual_style']}."
        if result.get('lighting'):
            final_prompt += f" LIGHTING: {result['lighting']}."
        if result.get('mood'):
            final_prompt += f" MOOD: {result['mood']}."
        
        final_prompt += " NO text overlays, NO watermarks, NO logos, NO subtitles."
        
        log_callback(f"   🤖 AI промпт сгенерирован: {len(final_prompt)} символов")
        return final_prompt


# Singleton instance
_default_veo3_generator = Veo3Generator()


def generate_intro_video(
    theme: str,
    intro_text: str,
    reference_images: List[str],
    api_key: str,
    output_path: str,
    duration: int = 5,
    style: str = "auto",
    resolution: str = "1080p",
    fps: int = 24,
    aspect_ratio: str = "9:16",
    log_callback: Callable[[str], None] = None
) -> Optional[str]:
    """Wrapper function for backward compatibility"""
    return _default_veo3_generator.generate_intro_video(
        theme=theme,
        intro_text=intro_text,
        reference_images=reference_images,
        api_key=api_key,
        output_path=output_path,
        duration=duration,
        style=style,
        resolution=resolution,
        fps=fps,
        aspect_ratio=aspect_ratio,
        log_callback=log_callback
    )
