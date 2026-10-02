#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Gemini API Client с поддержкой JSON Mode и Structured Output
Обеспечивает надёжные структурированные ответы от Gemini API
"""

import base64
import json
import time
import requests
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass

from core.gemini_models import TEXT_MODEL_CHAIN


@dataclass
class GeminiResponse:
    """Структурированный ответ от Gemini API"""
    success: bool
    data: Optional[Any] = None
    raw_text: Optional[str] = None
    error: Optional[str] = None
    model_used: Optional[str] = None
    finish_reason: Optional[str] = None
    status_code: Optional[int] = None
    retry_after: int = 0
    terminal: bool = False


class GeminiClient:
    """
    Клиент для Gemini API с поддержкой:
    - JSON Mode (гарантированный валидный JSON)
    - Response Schema (типизированные ответы)
    - Automatic retry с fallback моделями
    - Rate limiting
    """
    
    # Цепочка fallback моделей (единый источник: core.gemini_models)
    MODEL_CHAIN = TEXT_MODEL_CHAIN
    _MODEL_COOLDOWNS = {}
    
    # JSON Schemas для разных типов контента
    SCHEMAS = {
        'video_script': {
            "type": "object",
            "properties": {
                "clickbait_title": {
                    "type": "string",
                    "description": "Короткий цепляющий заголовок видео"
                },
                "description": {
                    "type": "string", 
                    "description": "Описание видео для YouTube"
                },
                "full_text": {
                    "type": "string",
                    "description": "Полный текст сценария для озвучки"
                },
                "hashtags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Хэштеги для видео"
                }
            },
            "required": ["clickbait_title", "description", "full_text"]
        },
        
        'chapter_plan': {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Заголовок видео"
                },
                "description": {
                    "type": "string",
                    "description": "Краткое описание"
                },
                "chapters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "focus": {"type": "string"}
                        },
                        "required": ["title", "focus"]
                    },
                    "description": "Список глав"
                }
            },
            "required": ["title", "chapters"]
        },
        
        'chapter_text': {
            "type": "object",
            "properties": {
                "chapter_text": {
                    "type": "string",
                    "description": "Текст главы"
                }
            },
            "required": ["chapter_text"]
        },
        
        'subtopics': {
            "type": "object",
            "properties": {
                "subtopics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Список подтем"
                }
            },
            "required": ["subtopics"]
        },
        
        'image_prompt': {
            "type": "object", 
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Детальный промпт для генерации изображения"
                },
                "style": {
                    "type": "string",
                    "description": "Стиль изображения"
                },
                "mood": {
                    "type": "string",
                    "description": "Настроение/атмосфера"
                }
            },
            "required": ["prompt"]
        }
    }
    
    def __init__(self, api_key: str, log_callback: Optional[Callable] = None):
        self.api_key = api_key
        self.log = log_callback or (lambda x: print(x))
        self._last_request_time = 0
        self._min_interval = 0.1  # 100ms между запросами
        
    def _rate_limit(self):
        """Простой rate limiter"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()
    
    def generate_json(
        self,
        prompt: str,
        schema_name: Optional[str] = None,
        custom_schema: Optional[Dict] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        models: Optional[List[str]] = None
    ) -> GeminiResponse:
        """
        Генерация структурированного JSON ответа.
        
        Args:
            prompt: Текст промпта
            schema_name: Имя предопределённой схемы (video_script, chapter_plan, etc.)
            custom_schema: Кастомная JSON Schema
            temperature: Температура генерации (0.0-1.0)
            max_tokens: Максимум токенов в ответе
            models: Список моделей для попытки (по умолчанию MODEL_CHAIN)
            
        Returns:
            GeminiResponse с данными или ошибкой
        """
        self._rate_limit()
        
        # Определяем схему
        schema = None
        if custom_schema:
            schema = custom_schema
        elif schema_name and schema_name in self.SCHEMAS:
            schema = self.SCHEMAS[schema_name]
        
        # Формируем конфиг генерации
        generation_config = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json"
        }
        
        # Добавляем схему если есть
        if schema:
            generation_config["responseSchema"] = schema
        
        # Safety settings - разрешаем всё для креативного контента
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
        
        # Пробуем модели по очереди
        models_to_try = models or self.MODEL_CHAIN
        last_error = None
        
        for model in models_to_try:
            cooldown_key = (self.api_key, model)
            cooldown_until = self._MODEL_COOLDOWNS.get(cooldown_key, 0)
            if cooldown_until > time.time():
                last_error = f"Модель {model} временно пропущена после 429"
                continue
            try:
                result = self._call_api(model, prompt, generation_config, safety_settings)
                if result.success:
                    result.model_used = model
                    return result
                if result.terminal:
                    result.model_used = model
                    return result
                last_error = result.error
                if result.status_code == 429:
                    cooldown = max(15, min(result.retry_after or 60, 300))
                    self._MODEL_COOLDOWNS[cooldown_key] = time.time() + cooldown
                    self.log(f"Gemini {model}: лимит 429, пробуем следующую бесплатную модель.")
            except Exception as e:
                last_error = str(e)
                continue
        
        return GeminiResponse(
            success=False,
            error=f"Все модели не сработали. Последняя ошибка: {last_error}"
        )
    
    def generate_text(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 16384,
        models: Optional[List[str]] = None
    ) -> GeminiResponse:
        """
        Генерация обычного текста (без JSON Mode).
        
        Args:
            prompt: Текст промпта
            temperature: Температура генерации
            max_tokens: Максимум токенов (по умолчанию 16384 — для рерайта длинных рецептов)
            models: Список моделей
            
        Returns:
            GeminiResponse с raw_text
        """
        self._rate_limit()
        
        generation_config = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens
        }
        
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
        
        models_to_try = models or self.MODEL_CHAIN
        last_error = None
        
        for model in models_to_try:
            cooldown_key = (self.api_key, model)
            cooldown_until = self._MODEL_COOLDOWNS.get(cooldown_key, 0)
            if cooldown_until > time.time():
                last_error = f"Модель {model} временно пропущена после 429"
                continue
            try:
                result = self._call_api(model, prompt, generation_config, safety_settings, json_mode=False)
                if result.success:
                    result.model_used = model
                    return result
                if result.terminal:
                    result.model_used = model
                    return result
                last_error = result.error
                if result.status_code == 429:
                    cooldown = max(15, min(result.retry_after or 60, 300))
                    self._MODEL_COOLDOWNS[cooldown_key] = time.time() + cooldown
                    self.log(f"Gemini {model}: лимит 429, пробуем следующую бесплатную модель.")
            except Exception as e:
                last_error = str(e)
                continue
        
        return GeminiResponse(
            success=False,
            error=f"Все модели не сработали. Последняя ошибка: {last_error}"
        )

    def generate_json_with_images(
        self,
        prompt: str,
        images: List[Dict[str, Any]],
        custom_schema: Optional[Dict] = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        models: Optional[List[str]] = None,
    ) -> GeminiResponse:
        """Generate structured JSON from text plus labelled in-memory images.

        ``images`` contains dictionaries with ``data`` (bytes), optional
        ``mime_type`` and an optional human-readable ``label``. Keeping this in
        the shared client gives visual workflows the same model fallback,
        cooldown and error handling as ordinary Gemini requests.
        """
        self._rate_limit()
        generation_config: Dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        }
        if custom_schema:
            generation_config["responseSchema"] = custom_schema

        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        media_parts: List[Dict[str, Any]] = []
        for index, image in enumerate(images or []):
            data = image.get("data")
            if not isinstance(data, (bytes, bytearray)) or not data:
                continue
            label = str(image.get("label") or f"IMAGE_{index}").strip()
            media_parts.append({"text": f"\nIMAGE LABEL: {label}\n"})
            media_parts.append({
                "inlineData": {
                    "mimeType": str(image.get("mime_type") or "image/jpeg"),
                    "data": base64.b64encode(bytes(data)).decode("ascii"),
                }
            })
        if not media_parts:
            return GeminiResponse(success=False, error="Нет валидных изображений для анализа")

        models_to_try = models or self.MODEL_CHAIN
        last_error = None
        for model in models_to_try:
            cooldown_key = (self.api_key, model)
            cooldown_until = self._MODEL_COOLDOWNS.get(cooldown_key, 0)
            if cooldown_until > time.time():
                last_error = f"Модель {model} временно пропущена после 429"
                continue
            try:
                result = self._call_api(
                    model,
                    prompt,
                    generation_config,
                    safety_settings,
                    media_parts=media_parts,
                )
                if result.success or result.terminal:
                    result.model_used = model
                    return result
                last_error = result.error
                if result.status_code == 429:
                    cooldown = max(15, min(result.retry_after or 60, 300))
                    self._MODEL_COOLDOWNS[cooldown_key] = time.time() + cooldown
            except Exception as exc:
                last_error = str(exc)
        return GeminiResponse(
            success=False,
            error=f"Все мультимодальные модели не сработали. Последняя ошибка: {last_error}",
        )
    
    def _call_api(
        self,
        model: str,
        prompt: str,
        generation_config: Dict,
        safety_settings: List[Dict],
        json_mode: bool = True,
        media_parts: Optional[List[Dict[str, Any]]] = None,
    ) -> GeminiResponse:
        """Вызов Gemini API"""
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "x-goog-api-key": self.api_key
        }
        
        parts = [{"text": prompt}]
        if media_parts:
            parts.extend(media_parts)
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation_config,
            "safetySettings": safety_settings
        }
        
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=180)  # 🔧 VPN FIX: 120 → 180 сек
            
            if resp.status_code == 503:
                return GeminiResponse(success=False, error=f"Модель {model} перегружена (503)")
            
            if resp.status_code == 404:
                return GeminiResponse(success=False, error=f"Модель {model} не найдена (404)")
            
            if resp.status_code == 429:
                try:
                    provider_message = str((resp.json().get("error") or {}).get("message") or "")
                except Exception:
                    provider_message = ""
                account_exhausted = any(
                    marker in provider_message.lower()
                    for marker in ("prepayment credits are depleted", "billing account", "billing required")
                )
                try:
                    retry_after = int(float(resp.headers.get("Retry-After", 0) or 0))
                except (TypeError, ValueError):
                    retry_after = 0
                return GeminiResponse(
                    success=False,
                    error=(
                        "Баланс Gemini API исчерпан. Google не переводит оплачиваемый проект "
                        "обратно на free tier: пополните баланс или используйте ключ нового "
                        "бесплатного AI Studio проекта."
                        if account_exhausted
                        else f"Превышен бесплатный лимит запросов для {model} (429)"
                    ),
                    status_code=429,
                    retry_after=retry_after,
                    terminal=account_exhausted,
                )

            if resp.status_code != 200:
                return GeminiResponse(success=False, error=f"API ошибка {resp.status_code}: {resp.text[:200]}")
            
            data = resp.json()
            candidates = data.get('candidates', [])
            
            if not candidates:
                return GeminiResponse(success=False, error="Нет candidates в ответе")
            
            finish_reason = candidates[0].get('finishReason', 'UNKNOWN')
            parts = candidates[0].get('content', {}).get('parts', [])
            text = "".join([p.get('text', '') for p in parts]).strip()
            
            if not text:
                return GeminiResponse(success=False, error="Пустой ответ от API")
            
            # 🚨 Проверяем причину завершения — MAX_TOKENS означает обрезанный текст
            if finish_reason == 'MAX_TOKENS':
                return GeminiResponse(
                    success=False,
                    raw_text=text,  # Сохраняем частичный текст для диагностики
                    error=f"Текст обрезан моделью {model} (MAX_TOKENS, получено ~{len(text)} символов). Нужна модель с большим лимитом.",
                    finish_reason=finish_reason
                )
            
            # Парсим JSON если нужно
            if json_mode:
                try:
                    parsed = json.loads(text)
                    return GeminiResponse(
                        success=True,
                        data=parsed,
                        raw_text=text,
                        finish_reason=finish_reason
                    )
                except json.JSONDecodeError as e:
                    # Пробуем починить JSON
                    try:
                        from json_repair import repair_json
                        repaired = repair_json(text)
                        parsed = json.loads(repaired)
                        return GeminiResponse(
                            success=True,
                            data=parsed,
                            raw_text=text,
                            finish_reason=finish_reason
                        )
                    except:
                        return GeminiResponse(
                            success=False,
                            raw_text=text,
                            error=f"Не удалось распарсить JSON: {e}"
                        )
            else:
                return GeminiResponse(
                    success=True,
                    raw_text=text,
                    finish_reason=finish_reason
                )
                
        except requests.exceptions.Timeout:
            return GeminiResponse(success=False, error="Таймаут запроса (180s)")
        except requests.exceptions.RequestException as e:
            return GeminiResponse(success=False, error=f"Сетевая ошибка: {e}")


# Удобные функции для быстрого использования
def generate_video_script(
    api_key: str,
    theme: str,
    duration: int,
    language: str = "Russian",
    log_callback: Optional[Callable] = None
) -> Optional[Dict]:
    """
    Быстрая генерация сценария видео.
    
    Returns:
        Dict с clickbait_title, description, full_text, hashtags или None
    """
    client = GeminiClient(api_key, log_callback)
    
    prompt = f"""Создай сценарий для YouTube видео.

Тема: {theme}
Длительность: {duration} секунд
Язык: {language}

Требования:
- clickbait_title: короткий цепляющий заголовок без ложных обещаний
- description: SEO-оптимизированное описание
- full_text: Полный текст для озвучки (~{duration * 2.5} слов)
- hashtags: 5-10 релевантных хэштегов

Пиши на {language} языке!"""

    response = client.generate_json(prompt, schema_name='video_script')
    
    if response.success:
        return response.data
    else:
        if log_callback:
            log_callback(f"❌ Ошибка генерации: {response.error}")
        return None


def generate_chapter_plan(
    api_key: str,
    theme: str,
    num_chapters: int,
    language: str = "Russian",
    log_callback: Optional[Callable] = None
) -> Optional[Dict]:
    """
    Генерация плана глав для длинного видео.
    
    Returns:
        Dict с title, description, chapters или None
    """
    client = GeminiClient(api_key, log_callback)
    
    prompt = f"""Создай план для длинного видео.

Тема: {theme}
Количество глав: {num_chapters}
Язык: {language}

Требования:
- title: Общий заголовок видео
- description: Краткое описание
- chapters: Массив глав, каждая с title и focus

Каждая глава должна раскрывать уникальный аспект темы.
Пиши на {language} языке!"""

    response = client.generate_json(prompt, schema_name='chapter_plan')
    
    if response.success:
        return response.data
    else:
        if log_callback:
            log_callback(f"❌ Ошибка генерации плана: {response.error}")
        return None
