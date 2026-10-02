#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Text generation using Google Gemini AI with smart segment merging

🎯 ИНТЕГРИРОВАНА ВИРУСНАЯ СИСТЕМА ТЕКСТОВ
📝 ИНТЕГРИРОВАНЫ LONG/SHORT TEXT ENHANCERS

Модуль для генерации текста через Google Gemini AI.

Возможности:
- Генерация вирусного текста с разными персонами
- Поддержка 12+ языков
- Умное разбиение на сегменты для TTS
- Chain of Thought промптинг
- Anti-Robot Flow (рваный ритм)
- Multiple Hooks (5 хуков, выбор лучшего)
- Seamless Loop (бесконечная петля)
- Comment prompts (вопросы для комментариев)

Персоны:
- 🔥 Вирусный (универсальный)
- ⚔️ UFC Комментатор (бои, VS)
- 🕵️ Конспиролог (тайны, НЛО)
- 💪 Мотиватор (бизнес, успех)
- 👻 Хоррор (страшилки)
- 😎 Друган (факты, лайфхаки)
- 📚 Серьезный (документальный)
"""

# print(">>> EXECUTING text_generator.py V14-NEW-SDK <<<")  # Debug output disabled

import json
from typing import Dict, Any, Callable, List
import requests

# 🌍 Система переводов
from core.translations import t, get_language_instruction
from core.title_strategy import subject_first_prompt_rules
from core.channel_analytics import load_enabled_feedback_instruction


_CONTENT_FALLBACK_LABELS = {
    'Russian': {'example': 'пример', 'angle': 'отдельный ракурс'},
    'English': {'example': 'example', 'angle': 'distinct angle'},
    'Spanish': {'example': 'ejemplo', 'angle': 'enfoque diferente'},
    'French': {'example': 'exemple', 'angle': 'angle distinct'},
    'German': {'example': 'Beispiel', 'angle': 'eigener Blickwinkel'},
    'Chinese': {'example': '示例', 'angle': '独立视角'},
    'Japanese': {'example': '例', 'angle': '別の視点'},
    'Korean': {'example': '예시', 'angle': '별도 관점'},
    'Portuguese': {'example': 'exemplo', 'angle': 'ângulo distinto'},
    'Italian': {'example': 'esempio', 'angle': 'prospettiva distinta'},
    'Hindi': {'example': 'उदाहरण', 'angle': 'अलग दृष्टिकोण'},
    'Arabic': {'example': 'مثال', 'angle': 'زاوية مختلفة'},
}


def _localized_content_fallback(
    main_theme: str,
    language: str,
    label_kind: str,
    ordinal: int,
) -> str:
    """Build a deterministic emergency topic without mixing languages."""
    labels = _CONTENT_FALLBACK_LABELS.get(
        str(language or ''),
        _CONTENT_FALLBACK_LABELS['English'],
    )
    label = labels.get(label_kind, labels['angle'])
    return f"{main_theme}: {label} {max(1, int(ordinal))}"

# Опциональный импорт json_repair с fallback
try:
    from json_repair import repair_json
    HAS_JSON_REPAIR = True
except ImportError:
    HAS_JSON_REPAIR = False
    def repair_json(text):
        """
        Fallback: улучшенная очистка и восстановление JSON.
        
        Обрабатывает:
        - Markdown code blocks
        - Незакрытые строки: "text → "text"
        - Незакрытые объекты: {... → {...}
        - Незакрытые массивы: [... → [...]
        - Обрезанные ключи/значения
        """
        import re
        
        text = text.strip()
        
        # 1. Удаляем markdown code blocks
        if text.startswith('```json'):
            text = text[7:]
        if text.startswith('```'):
            text = text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()
        
        # 2. Исправляем незакрытые строки (обрезанные посередине)
        # Паттерн: "text без закрывающей кавычки в конце
        # Ищем последнюю незакрытую строку
        in_string = False
        escape_next = False
        last_string_start = -1
        
        for i, char in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if char == '\\':
                escape_next = True
                continue
            if char == '"':
                if in_string:
                    in_string = False
                else:
                    in_string = True
                    last_string_start = i
        
        # Если строка не закрыта - закрываем
        if in_string and last_string_start >= 0:
            # Находим конец строки (до запятой, скобки или конца)
            text = text + '"'
        
        # 3. Считаем и закрываем незакрытые скобки
        open_braces = 0
        open_brackets = 0
        in_string = False
        escape_next = False
        
        for char in text:
            if escape_next:
                escape_next = False
                continue
            if char == '\\':
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == '{':
                open_braces += 1
            elif char == '}':
                open_braces -= 1
            elif char == '[':
                open_brackets += 1
            elif char == ']':
                open_brackets -= 1
        
        # 4. Убираем trailing запятые перед закрытием
        text = re.sub(r',\s*$', '', text)
        
        # 5. Закрываем незакрытые скобки
        if open_brackets > 0:
            text = text + ']' * open_brackets
        if open_braces > 0:
            text = text + '}' * open_braces
        
        # 6. Убираем trailing запятые перед } или ]
        text = re.sub(r',(\s*[}\]])', r'\1', text)
        
        return text.strip()

from core.viral_text_system import ViralTextSystem
from core.short_text_enhancer import ShortTextEnhancer
from core.subscribe_cta import (
    build_long_subscribe_cta_instruction,
    build_long_subscribe_cta_plan,
    build_subscribe_cta_instruction,
    ensure_long_spoken_subscribe_ctas,
    ensure_spoken_subscribe_cta,
    has_spoken_subscribe_cta,
    long_subscribe_cta_count,
)
from core.cost_tracker import get_tracker

# Импортируем дефолтный логгер из централизованного модуля
from core.logging_utils import get_default_logger
from core.gemini_models import FAST_TEXT_MODEL, PRO_TEXT_MODEL, generate_content_url

# Алиас для обратной совместимости
_dummy_log = get_default_logger()


CREATIVE_ANGLE_PLAYBOOK = (
    "Open on one concrete incident and reconstruct what changed, step by step.",
    "Frame the story around a consequential choice and its trade-off.",
    "Trace one clear cause-and-effect chain; remove unrelated background.",
    "Test a common misconception against specific, supportable evidence.",
    "Start from an overlooked physical detail and expand to its consequence.",
    "Build the story as a fair comparison between two approaches or outcomes.",
    "Use a before-versus-after timeline with one decisive turning point.",
    "Explain the hidden mechanism: how the process actually works.",
    "Lead with the consequence, then reveal the cause without fake mystery.",
    "Answer one narrow question completely instead of surveying the whole topic.",
    "Show scale and constraints with only verified or explicitly qualified numbers.",
    "Follow the viewpoint of a real role involved, without inventing a first-person witness.",
    "Analyze the failure mode: what breaks first, why, and what follows.",
    "Center a genuine tension where every available option has a cost.",
    "Use a scene-first structure: observable action, context, turn, payoff.",
    "End with a practical implication that changes how the viewer understands the topic.",
    "Expose a contradiction between appearance and reality using concrete details.",
    "Connect past, present, and a cautious future implication without presenting speculation as fact.",
)


def build_creative_angle_instruction(video_num: int, theme: str) -> str:
    """Return a stable, batch-aware narrative lens for substantive variety."""
    try:
        item_number = max(1, int(video_num))
    except (TypeError, ValueError):
        item_number = 1
    angle = CREATIVE_ANGLE_PLAYBOOK[(item_number - 1) % len(CREATIVE_ANGLE_PLAYBOOK)]
    return f"""
BATCH CREATIVE VARIATION — ITEM {item_number}:
- Keep the exact topic: "{theme}".
- Narrative lens: {angle}
- Make the central claim, opening beat, order of evidence, and payoff materially
  different from adjacent batch items. Changing only adjectives, title wording,
  subtitle styling, or shot order is not sufficient.
- Never invent facts, sources, quotations, statistics, people, or events to make
  the angle work. Narrow or qualify the claim when evidence is uncertain.
""".strip()


def count_words(text: str, language: str = 'English') -> int:
    """
    Подсчёт слов с учётом азиатских языков (японский, китайский, корейский).
    
    Для азиатских языков считаем символы / 2 как приблизительное количество слов,
    так как в них нет пробелов между словами.
    """
    if not text:
        return 0
    
    # Азиатские языки без пробелов между словами
    if language in ['Japanese', 'Chinese', 'Korean']:
        import re
        # Убираем пробелы и пунктуацию
        clean_text = re.sub(r'[\s\.,!?;:。！？、；：「」『』（）\[\]【】""''…—\\-]', '', text)
        # Примерно 2 символа = 1 слово для CJK
        return max(1, len(clean_text) // 2)
    else:
        # Для языков с пробелами - стандартный подсчёт
        return len(text.split())


def split_text_by_sentences(text: str, language: str = 'Russian') -> List[str]:
    """
    Универсальное разбиение текста на предложения для разных языков.
    
    Поддерживаемые языки:
    - Russian, English, Spanish, French, German, Italian, Portuguese (латиница)
    - Japanese (。！？)
    - Chinese (。！？)
    - Korean (. ! ?)
    - Arabic (. ! ?)
    - Hindi (। ! ?)
    """
    import re
    
    # Определяем паттерн разбиения в зависимости от языка
    if language in ['Japanese', 'Chinese']:
        # Японский и китайский: 。！？
        pattern = r'(?<=[。！？])\s*'
    elif language == 'Hindi':
        # Хинди: । (деванагари), плюс стандартные
        pattern = r'(?<=[।॥.!?])\s*'
    elif language == 'Korean':
        # Корейский: стандартные знаки
        pattern = r'(?<=[.!?])\s*'
    elif language == 'Arabic':
        # Арабский использует собственный вопросительный знак.
        pattern = r'(?<=[.!?؟])\s*'
    else:
        # Латиница (Russian, English, Spanish, French, German, Italian, Portuguese)
        pattern = r'(?<=[.!?])\s+'
    
    # Разбиваем текст
    sentences = re.split(pattern, text)
    
    # Очищаем и фильтруем
    sentences = [s.strip() for s in sentences if s.strip()]
    
    return sentences


class TextGenerator:
    """Generate text content for shorts using Gemini AI or AiTunnel.ru (alternative provider).

    Provider is auto-detected from the API key prefix:
      • Keys starting with 'sk-aitunnel-' → AiTunnel (https://api.aitunnel.ru/v1/)
      • All other keys                    → Google Gemini REST API

    AiTunnel is fully OpenAI-compatible and supports the same Gemini model names
    (gemini-2.5-flash, gemini-2.5-pro, etc.), so no model remapping is needed.
    """

    def __init__(self):
        self.model = None
        self.api_key = None
        self.generation_config = None
        self.chat_session = None
        self.thinking_budget = None

    @staticmethod
    def _optional_channel_feedback(target_duration: float) -> str:
        """Load opt-in channel guidance without making analytics a dependency."""
        if float(target_duration or 0) > 180:
            return ""
        try:
            return load_enabled_feedback_instruction()
        except Exception:
            return ""

    @staticmethod
    def _is_valid_gemini_api_key(api_key: str) -> bool:
        """Return True if key looks like a usable API token.

        Accepts both Google Gemini keys (typically starting with 'AIza') and
        AiTunnel keys (starting with 'sk-aitunnel-'). Any ASCII-only string
        of length >= 20 without spaces is considered valid at this stage.
        """
        if not api_key or not isinstance(api_key, str):
            return False
        api_key = api_key.strip()
        try:
            api_key.encode('ascii')
        except UnicodeEncodeError:
            return False
        if len(api_key) < 20:
            return False
        # Accept any printable ASCII (no whitespace)
        return all(33 <= ord(ch) <= 126 and not ch.isspace() for ch in api_key)

    @staticmethod
    def _is_aitunnel_key(api_key: str) -> bool:
        """Return True if this key belongs to AiTunnel.ru (prefix 'sk-aitunnel-')."""
        return bool(api_key and api_key.strip().startswith('sk-aitunnel-'))

    def _aitunnel_generate_content(self, model: str, api_key: str, prompt_text: str,
                                   generation_config: dict | None = None,
                                   response_schema: dict | None = None) -> dict:
        """
        Call AiTunnel REST API (OpenAI-compatible) and return a Gemini-format dict.

        Endpoint : POST https://api.aitunnel.ru/v1/chat/completions
        Auth     : Authorization: Bearer <sk-aitunnel-xxx>

        The returned dict mimics a Gemini generateContent response so that all
        existing callers can parse it without any changes.
        """
        url = "https://api.aitunnel.ru/v1/chat/completions"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key.strip()}"
        }

        payload: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt_text}],
        }

        # Map Gemini generation config → OpenAI params
        if generation_config:
            cfg = generation_config.copy()
            wants_json = cfg.pop('json_mode', False) or bool(response_schema)
            cfg.pop('responseMimeType', None)
            cfg.pop('responseSchema', None)
            if 'maxOutputTokens' in cfg:
                payload['max_tokens'] = cfg.pop('maxOutputTokens')
            if 'temperature' in cfg:
                payload['temperature'] = min(cfg.pop('temperature'), 2.0)  # OpenAI cap
            if 'topP' in cfg:
                payload['top_p'] = cfg.pop('topP')
            cfg.pop('topK', None)  # Not supported by OpenAI API
            if wants_json:
                payload['response_format'] = {"type": "json_object"}

        # Dynamic timeout based on prompt size
        prompt_length = len(prompt_text)
        timeout = 60 if prompt_length < 1000 else (
            120 if prompt_length < 5000 else (
            180 if prompt_length < 10000 else 300))

        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)

        if resp.status_code == 200:
            data = resp.json()
            content = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
            usage  = data.get('usage', {})
            # Wrap in Gemini-compatible structure
            return {
                "candidates": [{
                    "content": {
                        "parts": [{"text": content}],
                        "role": "model"
                    },
                    "finishReason": "STOP"
                }],
                "usageMetadata": {
                    "promptTokenCount":     usage.get('prompt_tokens', 0),
                    "candidatesTokenCount": usage.get('completion_tokens', 0),
                    "totalTokenCount":      usage.get('total_tokens', 0)
                },
                "_provider": "aitunnel"  # Internal marker
            }
        elif resp.status_code == 401:
            raise ValueError("AiTunnel: неверный API ключ (401). Проверьте ключ в настройках.")
        elif resp.status_code == 403:
            raise ValueError("AiTunnel: доступ запрещён (403). Возможно, закончился баланс.")
        elif resp.status_code == 429:
            raise ValueError("AiTunnel: превышен лимит запросов (429). Повторите позже.")
        elif resp.status_code == 404:
            raise ValueError(f"AiTunnel: модель '{model}' не найдена (404).")
        else:
            raise ValueError(f"AiTunnel ошибка {resp.status_code}: {resp.text[:300]}")

    def _rest_generate_content(self, model: str, api_key: str, prompt_text: str, generation_config: dict | None = None, safety_settings: dict | None = None, response_schema: dict | None = None) -> dict:
        """
        Unified content-generation dispatcher.

        Routes the request to the correct backend based on the API key prefix:
          • 'sk-aitunnel-...' → AiTunnel (OpenAI-compatible, api.aitunnel.ru)
          • anything else     → Google Gemini REST API

        Always returns a Gemini-format dict so callers need no changes.

        Args:
            model: Model name (same names work on both providers)
            api_key: API key — Gemini or AiTunnel
            prompt_text: User prompt
            generation_config: Generation parameters
            safety_settings: Gemini safety settings (ignored for AiTunnel)
            response_schema: JSON Schema for structured output
        """
        if not self._is_valid_gemini_api_key(api_key):
            raise ValueError("Некорректный API ключ. Для Gemini ключ обычно начинается с 'AIza', для AiTunnel — с 'sk-aitunnel-'. Минимум 20 символов.")

        # КЭШИРОВАНИЕ ЗАПРОСОВ К API (Экономия денег)
        import hashlib, json, os, time
        cache_file = None
        try:
            cache_dir = "cache/texts"
            os.makedirs(cache_dir, exist_ok=True)
            
            # Уникальный хэш на основе текста промпта и настроек (без ключа API)
            hash_content = f"{model}:{prompt_text}:{json.dumps(generation_config, sort_keys=True) if generation_config else ''}:{json.dumps(response_schema, sort_keys=True) if response_schema else ''}"
            prompt_hash = hashlib.md5(hash_content.encode('utf-8')).hexdigest()
            cache_file = os.path.join(cache_dir, f"{prompt_hash}.json")
            
            # Если такой запрос был менее 24 часов назад - возвращаем из кэша
            if os.path.exists(cache_file):
                if time.time() - os.path.getmtime(cache_file) < 86400:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cached_result = json.load(f)
                    _dummy_log(f"⚡ [КЭШ] Текст загружен из кэша (хэш: {prompt_hash[:8]}). Экономия денег!")
                    return cached_result
        except Exception as e:
            _dummy_log(f"⚠️ Ошибка чтения кэша текстов: {e}")

        # Инициализация APIKeyManager
        from core.api_key_manager import get_key_manager
        key_manager = get_key_manager()
        
        tried_keys = set()
        
        # Поддерживаемые модели с fallback
        model_fallback_chain = {
            PRO_TEXT_MODEL: [PRO_TEXT_MODEL, FAST_TEXT_MODEL],
            FAST_TEXT_MODEL: [FAST_TEXT_MODEL, PRO_TEXT_MODEL]
        }
        models_to_try = model_fallback_chain.get(model, [model])
        url_template = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        # Формируем payload для Gemini
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt_text}]}]
        }
        if generation_config:
            config = generation_config.copy()
            if response_schema or config.get('json_mode', False):
                config['responseMimeType'] = 'application/json'
                config.pop('json_mode', None)
                if response_schema:
                    config['responseSchema'] = response_schema
            payload["generationConfig"] = config
        if safety_settings:
            payload["safetySettings"] = safety_settings

        while True:
            tried_keys.add(api_key)
            is_aitunnel = self._is_aitunnel_key(api_key)
            
            # ── AiTunnel branch ──────────────────────────────────────────────────
            if is_aitunnel:
                try:
                    result = self._aitunnel_generate_content(
                        model=model,
                        api_key=api_key,
                        prompt_text=prompt_text,
                        generation_config=generation_config,
                        response_schema=response_schema
                    )
                    # Cost tracking
                    try:
                        usage = result.get('usageMetadata', {})
                        input_tokens  = usage.get('promptTokenCount', 0)
                        output_tokens = usage.get('candidatesTokenCount', 0)
                        if input_tokens > 0 or output_tokens > 0:
                            tracker = get_tracker()
                            tracker.add_text_generation(input_tokens, output_tokens, model=model)
                    except Exception:
                        pass
                    # Save to cache
                    try:
                        if 'cache_file' in locals() and cache_file:
                            with open(cache_file, 'w', encoding='utf-8') as f:
                                json.dump(result, f, ensure_ascii=False)
                    except Exception:
                        pass
                    return result
                except ValueError as val_err:
                    err_msg = str(val_err)
                    if "403" in err_msg or "429" in err_msg:
                        model_category = 'pro' if 'pro' in model else 'flash'
                        key_manager.mark_exhausted(api_key, model_category)
                        
                        next_key, _ = key_manager.get_best_available()
                        if next_key and next_key not in tried_keys:
                            _dummy_log(
                                "⚠️ Ошибка AiTunnel на текущем ключе. "
                                "Переключаемся на следующий ключ."
                            )
                            api_key = next_key
                            self.api_key = next_key
                            continue
                    raise val_err
                except Exception as e:
                    raise ValueError(f"AiTunnel ошибка: {e}") from e
            
            # ── Gemini branch ────────────────────────────────────────────────────
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "x-goog-api-key": api_key
            }
            
            # Динамический timeout на основе размера промпта
            prompt_length = len(prompt_text)
            timeout = 60 if prompt_length < 1000 else (
                120 if prompt_length < 5000 else (
                180 if prompt_length < 10000 else 300))
            
            last_error = None
            key_rotated = False
            
            for model_name in models_to_try:
                try:
                    url = url_template.format(model=model_name)
                    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
                    
                    if resp.status_code == 200:
                        result = resp.json()
                        
                        # Cost tracking
                        try:
                            usage = result.get('usageMetadata', {})
                            input_tokens = usage.get('promptTokenCount', 0)
                            output_tokens = usage.get('candidatesTokenCount', 0)
                            if input_tokens > 0 or output_tokens > 0:
                                tracker = get_tracker()
                                tracker.add_text_generation(input_tokens, output_tokens, model=model_name)
                        except Exception:
                            pass
                        
                        # Save to cache
                        try:
                            if 'cache_file' in locals() and cache_file:
                                with open(cache_file, 'w', encoding='utf-8') as f:
                                    json.dump(result, f, ensure_ascii=False)
                        except Exception as e:
                            _dummy_log(f"⚠️ Ошибка сохранения кэша текстов: {e}")
                            
                        return result
                    elif resp.status_code == 503:
                        last_error = f"Модель '{model_name}' перегружена (503)"
                        import time
                        time.sleep(3)
                        continue
                    elif resp.status_code == 404:
                        last_error = f"Модель '{model_name}' не найдена"
                        continue
                    elif resp.status_code >= 400:
                        error_text = resp.text[:200]
                        
                        if resp.status_code in (403, 429):
                            model_category = 'pro' if 'pro' in model_name else 'flash'
                            key_manager.mark_exhausted(api_key, model_category)
                            
                            next_key, _ = key_manager.get_best_available()
                            if next_key and next_key not in tried_keys:
                                _dummy_log(
                                    f"⚠️ Лимит/ошибка Gemini ({resp.status_code}) на текущем ключе. "
                                    "Переключаемся на следующий ключ."
                                )
                                api_key = next_key
                                self.api_key = next_key
                                key_rotated = True
                                break  # Break model loop to retry with new key
                        
                        if resp.status_code == 403:
                            raise ValueError(f"Ошибка доступа к Gemini API (403): {error_text}")
                        elif resp.status_code == 429:
                            last_error = f"Rate limit (429) для модели '{model_name}'"
                            import time
                            retry_after = int(resp.headers.get('Retry-After', 5))
                            time.sleep(min(retry_after, 30))
                            continue
                        elif resp.status_code == 400:
                            raise ValueError(f"Неверный запрос (400): {error_text}")
                        else:
                            raise ValueError(f"Gemini ошибка {resp.status_code}: {error_text}")
                except requests.exceptions.RequestException as e:
                    last_error = f"Сетевая ошибка: {e}"
                    continue
                except ValueError:
                    raise
            
            if key_rotated:
                continue
                
            raise ValueError(f"Все модели не сработали: {last_error}")
    
    # JSON Schemas для структурированных ответов
    VIDEO_SCRIPT_SCHEMA = {
        "type": "object",
        "properties": {
            "clickbait_title": {"type": "string"},
            "description": {"type": "string"},
            "full_text": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["clickbait_title", "full_text"]
    }
    
    CHAPTER_PLAN_SCHEMA = {
        "type": "object",
        "properties": {
            "clickbait_title": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "narrative_contract": {"type": "string"},
            "chapters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "macro_block": {"type": "string"},
                        "focus": {"type": "string"},
                        "purpose": {"type": "string"},
                        "key_points": {"type": "array", "items": {"type": "string"}},
                        "bridge_from_previous": {"type": "string"},
                        "bridge_to_next": {"type": "string"},
                        "avoid_repeating": {"type": "array", "items": {"type": "string"}}
                    }
                }
            }
        },
        "required": ["chapters"]
    }
    
    CHAPTER_TEXT_SCHEMA = {
        "type": "object",
        "properties": {
            "chapter_text": {"type": "string"}
        },
        "required": ["chapter_text"]
    }

    @staticmethod
    def _build_long_form_consistency_contract(theme: str, target_duration: float, num_chapters: int, words_per_chapter: int, language: str = 'Russian') -> str:
        minutes = max(1, int(round(float(target_duration or 0) / 60)))
        macro_block_note = (
            "- For videos longer than 60 minutes, group chapters into macro-blocks of 4-6 chapters; each block must answer one clear sub-question.\n"
            if target_duration >= 3600 else
            "- Keep the chapters in one clear chain with a beginning, development, and payoff.\n"
        )
        return f"""LONG-FORM CONSISTENCY CONTRACT:
- Treat the whole {minutes}-minute video as one continuous program, not as separate shorts.
- Keep one stable central thesis about "{theme}" from the first chapter to the final chapter.
- Build and follow a content bible: main entities, timeline/order, recurring terms, promises, boundaries, and facts that must stay consistent.
- Every chapter needs a different job: setup, context, cause, conflict, detail, consequence, comparison, resolution, or practical takeaway.
- Carry context forward. Do not restart with a generic intro in every chapter.
- Use bridge_from_previous and bridge_to_next so the listener feels one continuous line.
- Track covered facts and avoid repeating the same explanation unless the new chapter adds a new angle.
- Keep tone steady and listenable: no tabloid shouting, no fake cliffhanger every chapter, no forced drama.
- Target about {words_per_chapter} words per chapter, but prioritize coherent thought over filler.
{macro_block_note}- If facts are uncertain, phrase them carefully; do not invent dates, names, or claims to fill time."""

    @staticmethod
    def _format_long_form_plan_window(chapters: List[Dict[str, Any]], current_index: int, window: int = 2) -> str:
        if not chapters:
            return "No chapter plan available."

        lines = []
        last_index = len(chapters) - 1
        for idx, chapter in enumerate(chapters):
            should_show = (
                idx == 0
                or idx == last_index
                or abs(idx - current_index) <= window
                or (len(chapters) > 12 and idx % 5 == 0)
            )
            if not should_show:
                continue

            title = str(chapter.get('title') or f"Chapter {idx + 1}").strip()
            macro_block = str(chapter.get('macro_block') or '').strip()
            focus = str(chapter.get('focus') or '').strip()
            purpose = str(chapter.get('purpose') or '').strip()
            marker = "CURRENT" if idx == current_index else f"{idx + 1}/{len(chapters)}"
            detail = " | ".join(part for part in [macro_block, focus, purpose] if part)
            lines.append(f"- {marker}: {title}" + (f" -> {detail}" if detail else ""))

        return "\n".join(lines)

    @staticmethod
    def _build_long_form_rolling_memory(chapter_texts: List[str], language: str = 'Russian', max_chars: int = 2400) -> str:
        if not chapter_texts:
            return "ROLLING MEMORY: No previous chapters yet. Start with the central promise, then move forward."

        import re
        snippets = []
        start_index = max(0, len(chapter_texts) - 3)
        for idx, text in enumerate(chapter_texts[start_index:], start=start_index + 1):
            clean = re.sub(r'\s+', ' ', str(text or '')).strip()
            if not clean:
                continue
            if len(clean) > 420:
                head = clean[:210].rsplit(' ', 1)[0]
                tail = clean[-210:].split(' ', 1)[-1]
                clean = f"OPENING: {head}... ENDING: ...{tail}"
            snippets.append(f"Chapter {idx}: {clean}")

        joined = " | ".join(snippets)
        if len(joined) > max_chars:
            joined = joined[:max_chars].rsplit(' ', 1)[0] + "..."

        return (
            "ROLLING MEMORY:\n"
            f"- Already covered: {joined}\n"
            "- Continue from this point. Do not restart the whole topic, do not repeat the same setup, and do not contradict earlier facts."
        )

    @staticmethod
    def _build_long_form_coverage_ledger(
        chapters: List[Dict[str, Any]],
        completed_count: int,
        max_chars: int = 6000,
    ) -> str:
        """Keep durable plan facts visible after they leave the rolling window."""
        if completed_count <= 0:
            return "COVERAGE LEDGER: Nothing has been covered yet."

        lines = ["COVERAGE LEDGER (persistent content bible):"]
        for index, chapter in enumerate(chapters[:completed_count]):
            title = str(chapter.get('title') or f"Chapter {index + 1}").strip()
            macro = str(chapter.get('macro_block') or '').strip()
            key_points = chapter.get('key_points') or []
            if not isinstance(key_points, list):
                key_points = [str(key_points)]
            covered = "; ".join(str(point).strip() for point in key_points[:5] if str(point).strip())
            summary = str(chapter.get('continuity_summary') or '').strip()
            details = " | ".join(part for part in [macro, covered, summary] if part)
            lines.append(f"- {index + 1}: {title}" + (f" -> {details}" if details else ""))

        ledger = "\n".join(lines)
        if len(ledger) > max_chars:
            # Preserve both the foundation and the most recent covered facts.
            head = "\n".join(lines[:4])
            tail_lines = lines[-12:]
            ledger = head + "\n- ... earlier ledger entries retained in the global plan ...\n" + "\n".join(tail_lines)
        return ledger[:max_chars]

    @staticmethod
    def _chapter_continuity_summary(text: str, language: str = 'Russian', max_chars: int = 700) -> str:
        """Create a cheap, deterministic hand-off containing both setup and payoff."""
        parts = split_text_by_sentences(str(text or ''), language)
        if not parts:
            return ""
        selected = parts[:2]
        if len(parts) > 3:
            selected += parts[-2:]
        summary = " ".join(selected)
        if len(summary) > max_chars:
            summary = summary[:max_chars].rsplit(' ', 1)[0] + "..."
        return summary

    @staticmethod
    def _chapter_exact_repetition_ratio(
        candidate: str,
        previous_chapters: List[str],
        language: str = 'Russian',
    ) -> float:
        """Return the share of substantial candidate sentences already used verbatim."""
        import re

        def normalize(sentence: str) -> str:
            return re.sub(r'[^\w\u0400-\u04ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+', ' ', sentence.lower()).strip()

        candidate_sentences = [
            normalize(sentence)
            for sentence in split_text_by_sentences(candidate, language)
        ]
        candidate_sentences = [sentence for sentence in candidate_sentences if len(sentence) >= 45]
        if not candidate_sentences or not previous_chapters:
            return 0.0

        previous = set()
        for chapter in previous_chapters:
            previous.update(
                normalize(sentence)
                for sentence in split_text_by_sentences(chapter, language)
                if len(normalize(sentence)) >= 45
            )
        repeated = sum(1 for sentence in candidate_sentences if sentence in previous)
        return repeated / len(candidate_sentences)

    def _refresh_long_form_derivatives(
        self,
        result: Dict[str, Any],
        num_segments: int,
        language: str,
    ) -> Dict[str, Any]:
        """Keep narration, subtitles and visual prompts in sync after insertions."""
        full_text = str(result.get('full_text') or '').strip()
        text_parts = self._merge_short_segments(
            split_text_by_sentences(full_text, language),
            min_words_per_segment=8,
        )
        if not text_parts and full_text:
            text_parts = [full_text]

        visual_parts = list(text_parts)
        requested = max(1, int(num_segments or 1))
        if visual_parts and len(visual_parts) < requested:
            expanded = []
            total = len(visual_parts)
            for index in range(requested):
                base_index = index % total
                cycle = index // total
                base = visual_parts[base_index]
                context = visual_parts[(base_index + 1) % total] if total > 1 else base
                if cycle:
                    base = (
                        f"{base} Visual variation {cycle + 1}: use a distinct subject detail, "
                        f"camera angle, composition and setting; next context: {context}"
                    )
                expanded.append(base)
            visual_parts = expanded
        elif len(visual_parts) > requested:
            indices = [int(i * len(visual_parts) / requested) for i in range(requested)]
            visual_parts = [visual_parts[index] for index in indices]

        result['text_parts'] = text_parts
        result['text_parts_for_images'] = visual_parts
        result['original_unique_count'] = len(set(visual_parts))
        return result

    @staticmethod
    def test_api_key_via_rest(api_key: str) -> tuple[bool, str]:
        """Perform a lightweight live request to verify the key works.

        Auto-detects provider from key prefix:
          • 'sk-aitunnel-' → AiTunnel chat/completions endpoint
          • anything else  → Gemini generateContent endpoint

        Returns (ok, details).
        """
        key = api_key.strip() if api_key else ""
        # ── AiTunnel test ─────────────────────────────────────────────────────
        if key.startswith('sk-aitunnel-'):
            try:
                url = "https://api.aitunnel.ru/v1/chat/completions"
                headers = {
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {key}",
                }
                payload = {
                    "model": FAST_TEXT_MODEL,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 5
                }
                resp = requests.post(url, json=payload, headers=headers, timeout=30)
                if resp.status_code == 200:
                    return True, "200 OK (AiTunnel)"
                if resp.status_code == 401:
                    return False, "401: Неверный AiTunnel ключ"
                if resp.status_code == 403:
                    return False, "403: Доступ запрещён AiTunnel — проверьте баланс"
                if resp.status_code == 429:
                    return False, "429: Превышен лимит AiTunnel"
                if resp.status_code == 404:
                    return False, f"404: Модель не найдена на AiTunnel ({FAST_TEXT_MODEL})"
                return False, f"{resp.status_code}: {resp.text[:200]}"
            except Exception as e:
                return False, f"AiTunnel сетевая ошибка: {e}"
        # ── Gemini test ───────────────────────────────────────────────────────
        try:
            url = generate_content_url(FAST_TEXT_MODEL)
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "x-goog-api-key": key,
            }
            payload = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                return True, "200 OK (Gemini)"
            if resp.status_code == 404:
                return False, "404: Модель не найдена (проверьте имя модели)"
            if resp.status_code == 403:
                return False, "403: Доступ запрещён — проверьте ключ и права в проекте"
            if resp.status_code == 401:
                return False, "401: Неавторизовано — ключ отклонён"
            if resp.status_code == 429:
                return False, "429: Превышен лимит запросов"
            return False, f"{resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            return False, f"Сетевая ошибка: {e}"

    def generate_concrete_examples(self, main_theme: str, num_videos: int, api_key: str = None, 
                                    log_callback: Callable[[str], None] = _dummy_log, 
                                    language: str = 'Russian') -> list:
        """
        Генерирует КОНКРЕТНЫЕ примеры из общей темы.
        
        Например:
        - "производственные технологии" -> ["лазерная резка металла", "роботизированная сварка", "контроль качества деталей", ...]
        - "итальянская кухня" -> ["Паста карбонара", "Тортеллини", "Ризотто", "Тирамису", ...]
        - "космос" -> ["Юрий Гагарин", "Высадка на Луну", "Чёрные дыры", "МКС", ...]
        
        Args:
            main_theme: Общая тема
            num_videos: Количество конкретных примеров
            api_key: API ключ Gemini
            log_callback: Функция логирования
            language: Язык генерации
            
        Returns:
            Список конкретных примеров/подтем
        """
        if not self._is_valid_gemini_api_key(api_key):
            raise ValueError("❌ Gemini API ключ не предоставлен или некорректный.")
        
        # Language-specific instructions
        language_instructions = {
            'Russian': 'Отвечай ТОЛЬКО на русском языке (кириллица)',
            'English': 'Answer ONLY in English',
            'Hindi': 'Answer ONLY in Hindi (हिन्दी)',
            'Spanish': 'Answer ONLY in Spanish (Español)',
            'French': 'Answer ONLY in French (Français)',
            'German': 'Answer ONLY in German (Deutsch)',
            'Chinese': 'Answer ONLY in Chinese (中文)',
            'Japanese': 'Answer ONLY in Japanese (日本語)',
            'Korean': 'Answer ONLY in Korean (한국어)',
            'Arabic': 'Answer ONLY in Arabic (العربية)',
            'Portuguese': 'Answer ONLY in Portuguese (Português)',
            'Italian': 'Answer ONLY in Italian (Italiano)'
        }
        lang_instruction = language_instructions.get(language, f'Answer in {language}')
        
        # Для больших батчей разбиваем на части
        if num_videos > 20:
            log_callback(f"📦 Большой батч ({num_videos} примеров) - разбиваем на части...")
            batch_size = 15
            all_examples = []
            num_batches = (num_videos + batch_size - 1) // batch_size
            
            for batch_num in range(num_batches):
                batch_count = min(batch_size, num_videos - len(all_examples))
                log_callback(f"📦 Батч {batch_num + 1}/{num_batches}: генерация {batch_count} примеров...")
                
                try:
                    batch_examples = self._generate_concrete_examples_batch(
                        main_theme, batch_count, api_key, log_callback, lang_instruction,
                        existing_examples=all_examples,
                        language=language,
                    )
                    unique_batch = self._ensure_uniqueness(batch_examples, all_examples, log_callback)
                    all_examples.extend(unique_batch)
                    log_callback(f"✅ Батч {batch_num + 1}: получено {len(unique_batch)} уникальных примеров")
                except Exception as e:
                    log_callback(f"⚠️ Ошибка в батче {batch_num + 1}: {e}")
                    for i in range(batch_count):
                        all_examples.append(_localized_content_fallback(
                            main_theme,
                            language,
                            'example',
                            len(all_examples) + 1,
                        ))
            
            final_unique = self._ensure_uniqueness(all_examples, [], log_callback)
            refill_attempt = 0
            while len(final_unique) < num_videos and refill_attempt < 3:
                refill_attempt += 1
                missing = num_videos - len(final_unique)
                log_callback(f"🔄 Добираем недостающие темы: {missing} (попытка {refill_attempt}/3)")
                try:
                    refill = self._generate_concrete_examples_batch(
                        main_theme,
                        missing + 2,
                        api_key,
                        log_callback,
                        lang_instruction,
                        existing_examples=final_unique,
                        language=language,
                    )
                    unique_refill = self._ensure_uniqueness(refill, final_unique, log_callback)
                    if not unique_refill:
                        continue
                    final_unique.extend(unique_refill[:missing])
                except Exception as error:
                    log_callback(f"⚠️ Не удалось добрать темы: {error}")
            log_callback(f"🎯 Итого уникальных примеров: {len(final_unique)}/{num_videos}")
            return final_unique[:num_videos]
        else:
            examples = self._generate_concrete_examples_batch(
                main_theme,
                num_videos,
                api_key,
                log_callback,
                lang_instruction,
                language=language,
            )
            return self._ensure_uniqueness(examples, [], log_callback)
    
    def _generate_concrete_examples_batch(self, main_theme: str, num_examples: int, api_key: str,
                                           log_callback: Callable[[str], None], lang_instruction: str,
                                           existing_examples: list = None,
                                           language: str = 'Russian') -> list:
        """Генерирует батч конкретных примеров из темы."""
        
        existing_context = ""
        if existing_examples and len(existing_examples) > 0:
            existing_context = "\n\nУЖЕ СГЕНЕРИРОВАНО (избегай повторений):\n" + "\n".join([f"- {e}" for e in existing_examples[-15:]])
        
        prompt = f"""Тема: "{main_theme}"

🎯 ЗАДАЧА: Сгенерируй {num_examples} КОНКРЕТНЫХ примеров/случаев/персон/объектов по этой теме.

⚠️ КРИТИЧЕСКИ ВАЖНО:
- Каждый пример должен быть КОНКРЕТНЫМ (имя, название, дата, место)
- НЕ абстрактные категории, а РЕАЛЬНЫЕ примеры
- Каждый пример = отдельное видео о КОНКРЕТНОМ объекте/персоне/событии

📋 ПРИМЕРЫ ПРАВИЛЬНОЙ ГЕНЕРАЦИИ:

Тема "производственные технологии":
✅ "Лазерная резка металла - как станок держит точность"
✅ "Роботизированная сварка - где экономится время"
✅ "Контроль качества деталей - почему брак ловят на раннем этапе"
✅ "Пятиосевая обработка - когда одна установка заменяет несколько операций"
✅ "Автоматическая подача материала - как уменьшают простой"
❌ "Современные технологии: общий обзор" (слишком абстрактно!)
❌ "Производство в целом" (нет конкретики!)

Тема "итальянская кухня":
✅ "Паста карбонара - римский рецепт с гуанчиале"
✅ "Тортеллини - пельмени из Болоньи"
✅ "Ризотто алла миланезе - шафрановый рис"
✅ "Тирамису - десерт из Венето"
✅ "Пицца Маргарита - история создания в Неаполе"
❌ "Итальянские блюда" (слишком общо!)
❌ "Паста в целом" (нужен конкретный вид!)

Тема "космос":
✅ "Юрий Гагарин - первый человек в космосе 12 апреля 1961"
✅ "Аполлон-11 - высадка на Луну 20 июля 1969"
✅ "Чёрная дыра в центре Млечного Пути - Стрелец A*"
✅ "Вояджер-1 - самый далёкий объект от Земли"
✅ "Взрыв Челленджера - трагедия 28 января 1986"
❌ "Интересные факты о космосе" (нет конкретики!)

{existing_context}

{lang_instruction}

ФОРМАТ ОТВЕТА: Только нумерованный список, по одному примеру на строку.
Каждый пример должен содержать КОНКРЕТНОЕ имя/название + краткое пояснение.

1."""

        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                # Маршрутизируем через единый диспетчер (поддерживает Gemini + AiTunnel)
                response = self._rest_generate_content(
                    model=FAST_TEXT_MODEL,
                    api_key=api_key,
                    prompt_text=prompt,
                    generation_config={
                        "temperature": 0.9,
                        "topP": 0.95,
                        "topK": 40,
                        "maxOutputTokens": 4096
                    }
                )

                text = (response.get('candidates') or [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')

                # Парсим нумерованный список
                examples = []
                lines = text.strip().split('\n')

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    # Убираем номер в начале (1., 2., 1), 2), 1:, 2: и т.д.)
                    import re
                    cleaned = re.sub(r'^[\d]+[.\):\-]\s*', '', line)
                    cleaned = cleaned.strip()

                    if cleaned and len(cleaned) > 3:
                        cleaned = cleaned.strip('*-•◦▪▸►')
                        if cleaned:
                            examples.append(cleaned)

                if examples:
                    log_callback(f"✅ Сгенерировано {len(examples)} конкретных примеров")

                    # Дополняем если мало
                    if len(examples) < num_examples:
                        log_callback(f"⚠️ Получено меньше примеров ({len(examples)}) чем запрошено ({num_examples})")
                        for i in range(len(examples), num_examples):
                            examples.append(_localized_content_fallback(
                                main_theme,
                                language,
                                'example',
                                i + 1,
                            ))

                    return examples[:num_examples]
                else:
                    raise ValueError("Не удалось извлечь примеры из ответа")

            except Exception as e:
                log_callback(f"⚠️ Попытка {attempt + 1}/{max_attempts}: {e}")
                if attempt < max_attempts - 1:
                    import time
                    time.sleep(2)

        # Fallback
        log_callback("⚠️ Используем fallback генерацию примеров")
        return [
            _localized_content_fallback(main_theme, language, 'example', i + 1)
            for i in range(num_examples)
        ]

    def generate_subtopics(self, main_theme: str, num_videos: int, api_key: str = None, log_callback: Callable[[str], None] = _dummy_log, language: str = 'Russian', content_style: str = 'viral') -> list:
        """Generate multiple subtopics from a main theme using plain text format (more reliable for large batches)"""
        
        if not self._is_valid_gemini_api_key(api_key):
            raise ValueError("❌ Gemini API ключ не предоставлен или некорректный.")

        # Language-specific instructions
        language_instructions = {
            'Russian': 'Используй только КИРИЛЛИЦУ (русский язык)',
            'English': 'Use only ENGLISH language',
            'Hindi': 'Use only HINDI (हिन्दी) language with Devanagari script',
            'Spanish': 'Use only SPANISH (Español) language',
            'French': 'Use only FRENCH (Français) language',
            'German': 'Use only GERMAN (Deutsch) language',
            'Chinese': 'Use only CHINESE (中文) language with Chinese characters',
            'Japanese': 'Use only JAPANESE (日本語) language with Japanese characters',
            'Korean': 'Use only KOREAN (한국어) language with Hangul script',
            'Arabic': 'Use only ARABIC (العربية) language with Arabic script',
            'Portuguese': 'Use only PORTUGUESE (Português) language',
            'Italian': 'Use only ITALIAN (Italiano) language'
        }
        
        lang_instruction = language_instructions.get(language, f'Use only {language} language')
        
        # For large batches (>20), split into multiple requests
        if num_videos > 20:
            log_callback(f"📦 Большой батч ({num_videos} видео) - разбиваем на части...")
            batch_size = 15
            all_subtopics = []
            num_batches = (num_videos + batch_size - 1) // batch_size
            
            for batch_num in range(num_batches):
                batch_count = min(batch_size, num_videos - len(all_subtopics))
                log_callback(f"📦 Батч {batch_num + 1}/{num_batches}: генерация {batch_count} подтем...")
                
                try:
                    # Передаем существующие подтемы для избежания повторений
                    batch_subtopics = self._generate_subtopics_batch(
                        main_theme, batch_count, api_key, log_callback, lang_instruction,
                        existing_subtopics=all_subtopics,  # Контекст предыдущих батчей
                        content_style=content_style,
                        language=language,
                    )
                    
                    # Проверка уникальности
                    unique_batch = self._ensure_uniqueness(batch_subtopics, all_subtopics, log_callback)
                    all_subtopics.extend(unique_batch)
                    
                    log_callback(f"✅ Батч {batch_num + 1}/{num_batches}: получено {len(unique_batch)} уникальных подтем")
                except Exception as e:
                    log_callback(f"⚠️ Ошибка в батче {batch_num + 1}: {e}")
                    # Add fallback subtopics for failed batch
                    for i in range(batch_count):
                        all_subtopics.append(_localized_content_fallback(
                            main_theme,
                            language,
                            'angle',
                            len(all_subtopics) + 1,
                        ))
            
            # Финальная проверка уникальности
            final_unique = self._ensure_uniqueness(all_subtopics, [], log_callback)
            log_callback(f"🎯 Итого уникальных подтем: {len(final_unique)}/{num_videos}")
            
            return self._complete_subtopic_count(
                final_unique,
                main_theme,
                num_videos,
                api_key,
                log_callback,
                lang_instruction,
                content_style,
                language=language,
            )
        else:
            # Small batch - single request
            subtopics = self._generate_subtopics_batch(
                main_theme,
                num_videos,
                api_key,
                log_callback,
                lang_instruction,
                content_style=content_style,
                language=language,
            )
            return self._complete_subtopic_count(
                self._ensure_uniqueness(subtopics, [], log_callback),
                main_theme,
                num_videos,
                api_key,
                log_callback,
                lang_instruction,
                content_style,
                language=language,
            )

    def _complete_subtopic_count(
        self,
        subtopics: list,
        main_theme: str,
        requested_count: int,
        api_key: str,
        log_callback: Callable[[str], None],
        lang_instruction: str,
        content_style: str,
        language: str = 'Russian',
    ) -> list:
        """Refill AI under-production so a requested batch never silently shrinks."""
        completed = self._ensure_uniqueness(list(subtopics or []), [], log_callback)
        for refill_attempt in range(1, 4):
            missing = max(0, int(requested_count) - len(completed))
            if not missing:
                break
            log_callback(
                f"🔄 Нейросеть вернула мало тем: добираем {missing} "
                f"(попытка {refill_attempt}/3)"
            )
            try:
                refill = self._generate_subtopics_batch(
                    main_theme,
                    missing + 2,
                    api_key,
                    log_callback,
                    lang_instruction,
                    existing_subtopics=completed,
                    content_style=content_style,
                    language=language,
                )
            except Exception as error:
                log_callback(f"⚠️ Не удалось добрать темы: {error}")
                continue
            completed.extend(self._ensure_uniqueness(refill, completed, log_callback)[:missing])

        # Network/model degradation must not turn a 15-video request into 10.
        # These conservative fallbacks keep the exact user topic and remain
        # visibly distinct; the script generator still develops each angle.
        while len(completed) < int(requested_count):
            ordinal = len(completed) + 1
            completed.append(_localized_content_fallback(
                main_theme,
                language,
                'angle',
                ordinal,
            ))

        log_callback(f"🎯 Итоговое число тем: {len(completed)}/{requested_count}")
        return completed[:requested_count]

    def _ensure_uniqueness(self, new_subtopics: list, existing_subtopics: list, log_callback: Callable[[str], None]) -> list:
        """Ensure all subtopics are unique (no duplicates or very similar topics)"""
        unique_subtopics = []
        all_existing = existing_subtopics.copy()
        
        for subtopic in new_subtopics:
            # Нормализуем для сравнения (lowercase, без пунктуации)
            normalized = subtopic.lower().strip().rstrip('?.!,')
            
            # Проверяем на точные дубликаты
            is_duplicate = False
            for existing in all_existing:
                existing_normalized = existing.lower().strip().rstrip('?.!,')
                
                # Точное совпадение
                if normalized == existing_normalized:
                    is_duplicate = True
                    break
                
                # Очень похожие (>80% совпадение слов)
                words_new = set(normalized.split())
                words_existing = set(existing_normalized.split())
                if len(words_new) > 0 and len(words_existing) > 0:
                    intersection = len(words_new & words_existing)
                    union = len(words_new | words_existing)
                    similarity = intersection / union if union > 0 else 0
                    if similarity > 0.8:  # 80% похожести
                        is_duplicate = True
                        break
            
            if not is_duplicate:
                unique_subtopics.append(subtopic)
                all_existing.append(subtopic)
            else:
                log_callback(f"   ⚠️ Пропущен дубликат: {subtopic[:50]}...")
        
        return unique_subtopics

    def _generate_subtopics_batch(
        self,
        main_theme: str,
        num_videos: int,
        api_key: str,
        log_callback: Callable[[str], None],
        lang_instruction: str,
        existing_subtopics: list = None,
        content_style: str = 'viral',
        language: str = 'Russian',
    ) -> list:
        """Generate a batch of subtopics using plain text format (no JSON)"""
        
        # Добавляем контекст существующих подтем для избежания повторений
        existing_context = ""
        if existing_subtopics and len(existing_subtopics) > 0:
            existing_context = "\n\nALREADY GENERATED (avoid similar topics):\n" + "\n".join([f"- {t}" for t in existing_subtopics[-10:]])  # Последние 10
        
        # Разные промпты для разных стилей
        if content_style == 'serious':
            # СЕРЬЕЗНЫЙ СТИЛЬ: документальный, образовательный, факты
            prompt = f"""Based on the main theme "{main_theme}", create {num_videos} UNIQUE and INFORMATIVE subtopics for educational YouTube Shorts.

🎯 CRITICAL: MAXIMUM DIVERSITY REQUIRED!
- Each subtopic MUST explore a COMPLETELY DIFFERENT aspect
- NO repetition, NO similar angles, NO overlapping content
- Think like you're creating 30 different documentaries, not 30 variations of one topic

📊 DIVERSITY CATEGORIES (use ALL of these):
1. Historical events (specific dates, people, battles)
2. Scientific explanations (physics, chemistry, biology)
3. Technological aspects (how things work, innovations)
4. Cultural impact (society, traditions, art)
5. Economic factors (money, trade, business)
6. Psychological aspects (human behavior, emotions)
7. Geographical variations (different countries, regions)
8. Comparative analysis (vs other things, before/after)
9. Future predictions (what will happen, trends)
10. Hidden secrets (unknown facts, conspiracies)
11. Personal stories (real people, experiences)
12. Statistical data (numbers, records, extremes)

🔥 CLICKBAIT TITLES (but educational):
- Use numbers: "5 facts", "3 reasons", "7 secrets"
- Use power words: "shocking", "incredible", "hidden", "secret", "truth"
- Use questions: "Why...", "How...", "What if..."
- Use urgency: "before it's too late", "you need to know"
- Use curiosity gaps: "The truth about...", "What they don't tell you..."

EXAMPLES for "майонез" (30 different angles):
✅ "История майонеза: как соус из Испании покорил СССР"
✅ "Химия майонеза: почему масло и яйца не разделяются"
✅ "Майонез в космосе: как космонавты едят соусы на МКС"
✅ "Майонез vs кетчуп: экономическая война соусов в России"
✅ "Психология майонеза: почему русские добавляют его везде"
✅ "Майонез в Японии: как азиаты изменили европейский соус"
✅ "Провалье майонеза: самый дорогой соус в мире стоит $200"
✅ "Майонез до и после: как изменился состав за 100 лет"
✅ "Будущее майонеза: веганские и молекулярные версии"
✅ "Секретный ингредиент: что производители скрывают в составе"
✅ "История человека, который изобрел майонез в тюбике"
✅ "Мировой рекорд: 50 кг майонеза за 10 минут"

{existing_context}

LANGUAGE REQUIREMENT:
{lang_instruction}
Every returned title must be entirely in {language}. The examples above show
structure only; do not copy their language.

IMPORTANT: Return ONLY a numbered list, one subtopic per line. NO JSON, NO explanations.
Each title should be CLICKBAIT but EDUCATIONAL.

Format:
1. First unique subtopic
2. Second unique subtopic
3. Third unique subtopic
...

Start with "1." immediately:"""
        else:
            # ВИРУСНЫЙ СТИЛЬ: креативность, приколы, провокации
            prompt = f"""Based on the main theme "{main_theme}", create {num_videos} ULTRA-VIRAL and CLICKBAIT subtopics for YouTube Shorts.

🎯 CRITICAL: MAXIMUM DIVERSITY + MAXIMUM CLICKBAIT!
- Each subtopic MUST be COMPLETELY DIFFERENT and SHOCKING
- NO repetition, NO similar angles, NO boring content
- Think like you're creating 30 viral TikToks, not 30 similar videos

🔥 VIRAL CATEGORIES (use ALL of these):
1. Shocking facts (mind-blowing, unbelievable)
2. Funny/absurd (comedy, parody, memes)
3. Scary/creepy (horror, danger, warnings)
4. Controversial (debates, hot takes, unpopular opinions)
5. Life hacks (tips, tricks, secrets)
6. Experiments (what if, testing, trying)
7. Comparisons (vs battles, rankings, tier lists)
8. Mysteries (unsolved, conspiracy, hidden truth)
9. Extreme cases (records, limits, extremes)
10. Personal challenges (I tried, 24 hours, survival)
11. Forbidden knowledge (illegal, banned, censored)
12. Future predictions (apocalypse, trends, changes)

💣 ULTRA-CLICKBAIT FORMULAS:
- Shock: "ВЫ НЕ ПОВЕРИТЕ...", "ШОКИРУЮЩАЯ ПРАВДА..."
- Fear: "ОПАСНОСТЬ!", "НЕ ДЕЛАЙ ЭТО!", "ПРЕДУПРЕЖДЕНИЕ!"
- Curiosity: "ЧТО БУДЕТ ЕСЛИ...", "СЕКРЕТ, КОТОРЫЙ...", "ТО, ЧТО..."
- Numbers: "5 СПОСОБОВ", "3 ПРИЧИНЫ", "7 СЕКРЕТОВ"
- Urgency: "СРОЧНО!", "ПРЯМО СЕЙЧАС!", "ПОКА НЕ ПОЗДНО!"
- Controversy: "ЗАПРЕЩЕНО!", "СКРЫВАЮТ!", "НЕ ПОКАЖУТ ПО ТВ!"
- Personal: "Я ПОПРОБОВАЛ...", "МОЙ ОПЫТ...", "Я ВЫЖИЛ..."
- Questions: "ПОЧЕМУ...", "КАК...", "ЧТО ЕСЛИ..."

EXAMPLES for "майонез" (30 VIRAL angles):
✅ "ЧТО БУДЕТ, ЕСЛИ СЪЕСТЬ 10 КГ МАЙОНЕЗА ЗА РАЗ?"
✅ "МАЙОНЕЗ + КОЛА = ВЗРЫВ? Опасный эксперимент!"
✅ "СЕКРЕТ МАЙОНЕЗА: что производители скрывают 50 лет"
✅ "Я ел только майонез 30 дней. Вот что случилось..."
✅ "МАЙОНЕЗ УБИВАЕТ? Врачи в шоке от этих фактов!"
✅ "5 СПОСОБОВ использовать майонез НЕ ПО НАЗНАЧЕНИЮ"
✅ "МАЙОНЕЗ vs БЕНЗИН: что горит лучше? Эксперимент!"
✅ "Самый дорогой майонез в мире стоит как квартира"
✅ "ЗАПРЕЩЕННЫЙ майонез: почему его нельзя в США"
✅ "Майонез в космосе: космонавты раскрыли секрет"
✅ "МАЙОНЕЗ ИЗ НАСЕКОМЫХ? Будущее уже здесь!"
✅ "Я заменил кровь на майонез. Врачи не верили..."
✅ "МАЙОНЕЗ + МИКРОВОЛНОВКА = ? НЕ ПОВТОРЯЙ!"
✅ "Как майонез разрушил жизнь миллионера"
✅ "МАЙОНЕЗ ВМЕСТО БЕНЗИНА? Безумный эксперимент!"

{existing_context}

LANGUAGE REQUIREMENT:
{lang_instruction}
Every returned title must be entirely in {language}. The examples above show
structure only; do not copy their language.

IMPORTANT: Return ONLY a numbered list, one subtopic per line. NO JSON, NO explanations.
Each title MUST be ULTRA-CLICKBAIT and VIRAL.
Use CAPS, emojis in text, questions, shock value!

Format:
1. First viral subtopic
2. Second viral subtopic
3. Third viral subtopic
...

Start with "1." immediately:"""

        max_attempts = 3
        response = None
        
        for attempt in range(max_attempts):
            try:
                if attempt > 0:
                    log_callback(f"🔄 Попытка {attempt + 1}/{max_attempts} генерации подтем...")
                
                # Используем text/plain вместо JSON для надежности
                response = self._rest_generate_content(
                    model=FAST_TEXT_MODEL,  # Gemini Flash для экономии бюджета
                    api_key=api_key,
                    prompt_text=prompt,
                    generation_config={
                        "temperature": 0.9 + (attempt * 0.05),  # Умеренная температура (>1.0 вызывает галлюцинации)
                        "topP": 0.95,  # Больше вариативности
                        "topK": 64,  # Больше вариантов
                        "maxOutputTokens": 8192,  # Еще больше токенов для больших списков (30+ видео)
                        # НЕ указываем responseMimeType - пусть будет plain text
                    }
                )
                break
            except Exception as e:
                if attempt == max_attempts - 1:
                    log_callback("❌ Все попытки генерации подтем не удались")
                    raise
                log_callback(f"⚠️ Попытка {attempt + 1} не удалась: {e}")
                import time
                time.sleep(2 * (attempt + 1))  # Exponential backoff
                continue
        
        if not response:
            raise ValueError("Не удалось получить ответ от Gemini API")
        
        try:
            # Extract text from response
            candidates = response.get('candidates', [])
            if not candidates:
                raise ValueError("API Gemini вернул пустой ответ при генерации подтем.")
            
            parts = candidates[0].get('content', {}).get('parts', [])
            response_text = ''
            for part in parts:
                if 'text' in part and part['text']:
                    response_text += part['text']
            
            response_text = response_text.strip()
            if not response_text:
                raise ValueError("API Gemini не вернул текст для подтем.")
            
            log_callback(f"📝 Получен ответ: {len(response_text)} символов")
            
            # Parse plain text list
            subtopics = []
            lines = response_text.split('\n')
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Remove numbering (1., 2., 1), 2), -, *, etc.)
                import re
                cleaned = re.sub(r'^[\d]+[\.\)]\s*', '', line)  # Remove "1. " or "1) "
                cleaned = re.sub(r'^[-*•]\s*', '', cleaned)  # Remove "- " or "* "
                cleaned = cleaned.strip().strip('"').strip("'").strip(',')
                
                # Skip empty or too short lines
                if cleaned and len(cleaned) > 10:
                    subtopics.append(cleaned)
            
            if not subtopics:
                log_callback("⚠️ Не удалось извлечь подтемы из текста. Пробуем альтернативный парсинг...")
                # Fallback: try to extract any meaningful lines
                for line in lines:
                    line = line.strip().strip('"').strip("'").strip(',').strip('-').strip('*').strip()
                    if line and len(line) > 15 and not line.startswith('{') and not line.startswith('['):
                        subtopics.append(line)
            
            if subtopics:
                log_callback(f"✅ Извлечено {len(subtopics)} подтем из текста")
                
                # Ensure we have enough subtopics
                if len(subtopics) < num_videos:
                    log_callback(f"⚠️ Получено меньше подтем ({len(subtopics)}) чем запрошено ({num_videos}). Дополняем...")
                    for i in range(len(subtopics), num_videos):
                        subtopics.append(_localized_content_fallback(
                            main_theme,
                            language,
                            'angle',
                            i + 1,
                        ))
                
                return subtopics[:num_videos]
            else:
                raise ValueError("Не удалось извлечь ни одной подтемы из ответа")
        
        except Exception as e:
            log_callback(f"❌ Ошибка обработки ответа: {e}")
            log_callback(f"📋 Первые 500 символов ответа: {response_text[:500] if 'response_text' in locals() else 'N/A'}")
            # Fallback: generate simple subtopics
            log_callback("⚠️ Используем fallback генерацию подтем")
            return [
                _localized_content_fallback(main_theme, language, 'angle', i + 1)
                for i in range(num_videos)
            ]




    def generate_safe_keywords(self, main_theme: str, api_key: str = None, log_callback: Callable[[str], None] = _dummy_log) -> List[str]:
        """Generate safe, neutral keywords for a potentially sensitive theme using REST API."""
        if not api_key or not self._is_valid_gemini_api_key(api_key):
            log_callback("⚠️ API ключ не предоставлен или некорректный. Используется тема по умолчанию.")
            return [main_theme]

        prompt = f"""
        Для сложной или потенциально чувствительной темы "{main_theme}", предложи 3-4 нейтральных объекта, символа или локации на русском языке, которые визуально ассоциируются с темой, но не показывают напрямую насилие, конфликт или конкретных личностей.

        Примеры:
        - Тема: "Трамп обратился к странам НАТО по вопросу санкций" -> Ключевые слова: ["Здание штаб-квартиры НАТО в Брюсселе", "Белый дом в Вашингтоне", "Флаги стран-участниц НАТО", "Зал для пресс-конференций"]
        - Тема: "Война в Чечне" -> Ключевые слова: ["горный пейзаж", "старая архитектура Грозного", "мемориал памяти", "символ скорби"]
        - Тема: "COVID-19" -> Ключевые слова: ["микроскоп", "лабораторное оборудование", "пустые улицы города", "медицинская маска"]

        Верни результат в формате JSON:
        {{
            "keywords": ["слово1", "слово2", "слово3"]
        }}
        """
        try:
            result = self._rest_generate_content(
                model='gemini-2.5-flash',
                api_key=api_key,
                prompt_text=prompt,
                generation_config={
                    'responseMimeType': 'application/json',
                    'temperature': 0.5
                }
            )
            
            # Извлекаем текст из ответа
            text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            if not text or not text.strip():
                raise ValueError("API Gemini вернул пустой ответ при генерации ключевых слов.")
            
            # 🔧 Используем json_repair для надежного парсинга
            try:
                parsed = json.loads(text.strip())
            except json.JSONDecodeError:
                log_callback("⚠️ Битый JSON от Gemini, пытаемся починить...")
                parsed = json.loads(repair_json(text.strip()))
            
            keywords = parsed.get("keywords", [])
            
            if not keywords:
                log_callback(f"⚠️ Gemini не смог сгенерировать ключевые слова. Используем тему '{main_theme}'.")
                return [main_theme]
                
            log_callback(f"✅ Сгенерированы безопасные ключевые слова: {keywords}")
            return keywords

        except Exception as e:
            log_callback(f"❌ Ошибка при генерации безопасных ключевых слов: {e}. Используем тему '{main_theme}'.")
            return [main_theme]

    def create_model_with_thinking(self, api_key: str, thinking_budget: int = 1024):
        """Store API key for REST-based generation.
        
        Note: This method stores the API key and the requested thinking budget for
        compatibility with older GUI flows. Current REST generation routes may
        choose not to send it to a specific model.
        """
        self.api_key = api_key
        self.thinking_budget = thinking_budget

    def generate_text_with_chat(self, theme: str, api_key: str = None, num_shots: int = 1, target_duration: float = 30.0, log_callback: Callable[[str], None] = _dummy_log, language: str = 'Russian') -> Dict[str, Any]:
        """Generate text using REST API (chat sessions replaced with stateless calls)"""

        if not api_key or not self._is_valid_gemini_api_key(api_key):
            raise ValueError("❌ Gemini API ключ не предоставлен или некорректный.")

        # Calculate words needed
        words_per_minute = 150
        target_words = int((target_duration / 60) * words_per_minute)
        target_words = max(50, min(target_words, 300))

        # Языковая инструкция
        lang_instructions = {
            'Russian': 'Ясный информативный стиль на русском',
            'English': 'Clear informative style in English',
            'Spanish': 'Estilo informativo claro en español',
            'French': 'Style informatif clair en français',
            'German': 'Klarer informativer Stil auf Deutsch',
        }
        lang_style = lang_instructions.get(language, f'Clear informative style in {language}')

        system_instruction = f"""You are a creative YouTube Shorts storyteller.
        Respond only with valid JSON. Create engaging content ONLY in {language} language."""

        prompt = f"""{system_instruction}

        Create a YouTube Short about "{theme}" in JSON format.

        Requirements:
        - {target_words} words of text
        - {lang_style}
        - Factual delivery without excessive emotions
        - Structure: title, description, full_text, hashtags
        - ALL text MUST be in {language} language!

        Return ONLY JSON without additional comments.
        """

        try:
            result = self._rest_generate_content(
                model='gemini-2.5-flash',
                api_key=api_key,
                prompt_text=prompt,
                generation_config={
                    'temperature': 1.0,
                    'responseMimeType': 'application/json'
                }
            )
            
            # Извлекаем текст из ответа
            text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            
            # 🔧 Используем json_repair для надежного парсинга
            try:
                parsed = json.loads(text.strip())
            except json.JSONDecodeError:
                log_callback("⚠️ Битый JSON от Gemini, пытаемся починить...")
                parsed = json.loads(repair_json(text.strip()))
            return parsed
        except Exception as e:
            log_callback(f"⚠️ Ошибка генерации: {e}. Переключаемся на обычный режим.")
            # Fallback to regular generation
            return self.generate_text(theme, api_key, target_duration=target_duration, log_callback=log_callback, language=language)

    def generate_text(self, theme: str, api_key: str = None, target_duration: float = 30.0, num_segments: int = 4, video_num: int = 1, char_limit: int = None, log_callback: Callable[[str], None] = _dummy_log, language: str = 'Russian', video_width: int = 1920, video_height: int = 1080, strict_text_theme: bool = True, persona_id: str = None, use_viral_system: bool = True, enable_seamless_loop: bool = True, enable_comment_bait: bool = False) -> Dict[str, Any]:
        """Generate title, description and text content for a short with specific timing and segment count.
        
        Args:
            persona_id: ID персоны из ViralTextSystem (conspiracy, motivator, horror, bro, viral, serious)
                       Если None - автоматический выбор на основе темы
            use_viral_system: Использовать вирусную систему текстов (6 технологий)
            enable_seamless_loop: Бесконечная петля (конец → начало) для повторного просмотра
            enable_comment_bait: Добавить уместный вопрос для комментариев
        """

        # Use REST API to avoid client header encoding issues
        if not self._is_valid_gemini_api_key(api_key):
            raise ValueError("❌ Gemini API ключ не предоставлен или некорректный.")
        
        # A single viral prompt often returns a Shorts-sized script even when the
        # requested video is several minutes long. Multipart generation enforces
        # a minimum word count and keeps narration close to the selected duration.
        if target_duration >= 120:
            log_callback(
                f"🎬 Видео длительностью {target_duration/60:.1f} минут - "
                "используем генерацию с контролем объёма текста"
            )
            
            result = self.generate_long_text_multipart(
                theme,
                api_key,
                target_duration,
                num_segments,
                log_callback,
                language,
                variation_index=video_num,
            )
            
            # 💬 ИНТЕРАКТИВНЫЕ ЭЛЕМЕНТЫ для длинных видео
            from core.engagement_injector import EngagementInjector
            if EngagementInjector.should_inject_engagement(target_duration):
                log_callback("💬 Добавление интерактивных элементов...")
                engagement_points = EngagementInjector.generate_engagement_points(
                    duration=target_duration,
                    language=language,
                    include_subscription_cta=False,
                )
                if engagement_points:
                    result['full_text'] = EngagementInjector.inject_into_text(
                        text=result.get('full_text', ''),
                        engagement_points=engagement_points
                    )
                    log_callback(f"   ✅ Добавлено {len(engagement_points)} интерактивных элементов")
            
            # 🔄 СИСТЕМА РЕКАПОВ для длинных видео
            from core.recap_generator import RecapGenerator
            if RecapGenerator.should_generate_recaps(target_duration):
                log_callback("🔄 Добавление рекапов...")
                recap_points = RecapGenerator.generate_recap_points(
                    duration=target_duration
                )
                if recap_points:
                    result['full_text'] = RecapGenerator.inject_recaps(
                        text=result.get('full_text', ''),
                        recap_points=recap_points,
                        language=language
                    )
                    log_callback(f"   ✅ Добавлено {len(recap_points)} рекапов")

            cta_text = ensure_long_spoken_subscribe_ctas(
                result.get('full_text', ''),
                language=language,
                target_duration=target_duration,
            )
            if cta_text != result.get('full_text', ''):
                result['full_text'] = cta_text
                log_callback(f"🔔 План подписки для лонга доведён до {long_subscribe_cta_count(target_duration)} точек")

            # Engagement, recaps and CTA mutate full_text after chapter generation.
            # Rebuild every downstream representation once so subtitles, visuals and
            # TTS can never operate on different versions of the narration.
            result = self._refresh_long_form_derivatives(result, num_segments, language)
            return result
        
        # 🎯 ВИРУСНАЯ СИСТЕМА: Автоматический выбор персоны или использование указанной
        if use_viral_system:
            # Автоматический выбор персоны на основе темы
            if persona_id is None:
                persona_id = ViralTextSystem.get_recommended_persona(theme)
                persona_name = ViralTextSystem.PERSONAS[persona_id]['name']
                log_callback(f"🎭 Автоматически выбрана персона: {persona_name}")
            else:
                persona_name = ViralTextSystem.PERSONAS.get(persona_id, ViralTextSystem.PERSONAS['viral'])['name']
                log_callback(f"🎭 Используется персона: {persona_name}")
            
            # 🔧 ИСПРАВЛЕНО: strict_text_theme теперь НЕ меняет персону
            # Вместо этого добавляем инструкцию строгого следования теме в промпт
            # Это позволяет использовать seamless_loop и comment_bait с любой персоной
            if strict_text_theme:
                log_callback(f"📚 Строгое следование теме включено (персона сохранена: {persona_name})")
        else:
            # Старая логика без вирусной системы
            is_vertical = video_height > video_width
            is_short = target_duration <= 60
            use_hook = is_vertical and is_short and not strict_text_theme
            
            if use_hook:
                log_callback("🎣 Вертикальный Short - добавляем цепляющий хук")
            elif is_vertical and is_short and strict_text_theme:
                log_callback("📚 Строгое следование теме - без вирусных хуков, серьезный стиль")

        # Calculate words needed for target duration (average 150 words per minute for speech)
        words_per_minute = 150
        target_words = int((target_duration / 60) * words_per_minute)
        
        # 🎯 ГЕНЕРАЦИЯ ПРОМПТА: Вирусная система или старая логика
        if use_viral_system:
            # ✅ ИСПОЛЬЗУЕМ ВИРУСНУЮ СИСТЕМУ С 6 ТЕХНОЛОГИЯМИ
            log_callback("🚀 Генерация промпта через вирусную систему...")
            
            # Определяем какие технологии включить
            is_short = target_duration <= 60
            # Петля и вопросы для комментариев только для коротких видео
            use_loop = enable_seamless_loop and is_short
            use_bait = enable_comment_bait and is_short and persona_id != 'serious'
            use_viral_hooks = persona_id != 'serious'
            
            prompt = ViralTextSystem.create_viral_prompt(
                theme=theme,
                duration=target_duration,
                language=language,
                persona_id=persona_id,
                use_chain_of_thought=True,  # Технология 1: Chain of Thought
                generate_multiple_hooks=use_viral_hooks,  # Технология 2: Multiple Hooks
                enable_seamless_loop=use_loop,  # Технология 5: Seamless Loop
                enable_comment_bait=use_bait  # Технология 6: вопросы для комментариев
            )
            
            # 🔧 ДОБАВЛЯЕМ инструкцию строгого следования теме если включено
            if strict_text_theme:
                strict_instruction = f"""

⚠️ CRITICAL: STRICT THEME FOLLOWING ENABLED!
You MUST write ONLY about the exact topic: "{theme}"
- Do NOT deviate from the topic
- Do NOT add unrelated information
- Do NOT interpret the topic freely
- Include key words from the topic in your text
- Stay focused on the specific subject matter
- Do NOT invent names, dates, locations, quests, artifacts, documents, quotes, statistics, or canon details.
- Treat an unsupported claim as a theory/interpretation and label it clearly; never present it as an archive, proof, or official fact.
- Prefer fewer concrete claims over confident fabrication when the prompt does not provide enough evidence.
- If the topic is "cucumbers" - write about cucumbers, not vegetables in general
- If the topic is "prostitution in The Witcher" - write about that specific topic in the game/books

"""
                prompt = strict_instruction + prompt
            
            tech_count = 4 + (1 if use_loop else 0) + (1 if use_bait else 0)
            log_callback(f"✅ Промпт создан с {tech_count} технологиями: {ViralTextSystem.PERSONAS[persona_id]['name']}")
            if strict_text_theme:
                log_callback("   📚 Строгое следование теме включено")
            if use_loop:
                log_callback("   🔄 Бесконечная петля включена (плавный повторный просмотр)")
            if use_bait:
                log_callback("   🎣 Вопрос зрителю включён (вовлечение без гарантированных процентов)")
            
        else:
            # ❌ СТАРАЯ ЛОГИКА (для обратной совместимости)
            log_callback("⚠️ Используется старая система генерации промптов")
            
            # --- Character limit instruction ---
            chars_per_second = 10
            auto_char_limit = int(target_duration * chars_per_second)
            auto_char_limit = max(50, min(auto_char_limit, 50000))
            
            if char_limit and char_limit > 0:
                final_char_limit = max(50, char_limit)
            else:
                final_char_limit = auto_char_limit
            
            # Language-specific instructions
            language_names = {
                'Russian': 'Russian (русский)',
                'English': 'English',
                'Hindi': 'Hindi (हिन्दी)',
                'Spanish': 'Spanish (Español)',
                'French': 'French (Français)',
                'German': 'German (Deutsch)',
                'Chinese': 'Chinese (中文)',
                'Japanese': 'Japanese (日本語)',
                'Korean': 'Korean (한국어)',
                'Arabic': 'Arabic (العربية)',
                'Portuguese': 'Portuguese (Português)',
                'Italian': 'Italian (Italiano)'
            }
            
            lang_name = language_names.get(language, language)
            title_architecture = subject_first_prompt_rules(language)
            
            # Определяем формат видео
            is_vertical = video_height > video_width
            is_short = target_duration <= 60
            use_hook = is_vertical and is_short and not strict_text_theme
            
            if use_hook:
                # ПРОМПТ ДЛЯ SHORTS С ХУКОМ
                import random
                hook_examples = [
                    '"Start with the person, object, or event already in motion."',
                    '"Open on a clear choice, conflict, cost, or visible change."',
                    '"Make the first line understandable without any setup."',
                ]
                selected_hooks = random.sample(hook_examples, min(3, len(hook_examples)))
                hooks_text = '\n'.join([f'✅ {hook}' for hook in selected_hooks])
                
                prompt = f"""Create a VIRAL YouTube Shorts script about "{theme}".
LANGUAGE: Write EVERYTHING ONLY in {lang_name} language! DO NOT mix English words into the {lang_name} text (e.g. no "tasty", "hot", "insane", "bro"). 100% {lang_name} words ONLY!
Duration: {target_duration:.0f} seconds (~{target_words} words)
Keep full_text under about {final_char_limit} characters.
SHORTS FEED RULES:
- First 1.5 seconds: concrete subject + action/conflict/cost.
- Avoid generic mystery words like SECRET, HIDDEN, SHOCK, and ALL CAPS.
- For 25-35 seconds use 4 tight beats: hook -> context -> turning point -> payoff.
- Add a new concrete beat every 4-6 seconds. Remove filler and repeated topic phrases.
TITLE: sentence case, normal punctuation, concrete conflict/action.
{title_architecture}
{build_subscribe_cta_instruction(language, video_kind="short")}

🎣 HOOK: Start with: {hooks_text}
Return JSON: {{"clickbait_title": "...", "description": "...", "full_text": "...", "hashtags": [...]}}
"""
            else:
                # ОБЫЧНЫЙ ПРОМПТ
                prompt = f"""Create a professional YouTube video script about "{theme}".
LANGUAGE: Write EVERYTHING ONLY in {lang_name} language! DO NOT mix English words into the {lang_name} text (e.g. no "tasty", "hot", "insane", "bro"). 100% {lang_name} words ONLY!
Duration: {target_duration:.0f} seconds (~{target_words} words)
Keep full_text under about {final_char_limit} characters.

TITLE: sentence case, normal punctuation, concrete conflict/action, no ALL CAPS.
{title_architecture}
{build_subscribe_cta_instruction(language, video_kind="short" if is_short else "long")}
Return JSON: {{"clickbait_title": "...", "description": "...", "full_text": "...", "hashtags": [...]}}
"""

        feedback = self._optional_channel_feedback(target_duration)
        if feedback:
            prompt = feedback + "\n\n" + prompt
            log_callback("Channel analytics profile applied as optional, non-factual guidance.")
        prompt = build_creative_angle_instruction(video_num, theme) + "\n\n" + prompt
        try:
            angle_number = (max(1, int(video_num)) - 1) % len(CREATIVE_ANGLE_PLAYBOOK) + 1
        except (TypeError, ValueError):
            angle_number = 1
        log_callback(
            f"🎭 Вариативность партии: творческий ракурс "
            f"{angle_number}/{len(CREATIVE_ANGLE_PLAYBOOK)}"
        )

        try:
            # Calculate required tokens based on duration
            # Примерно 1.5 токена на слово, 150 слов в минуту
            # + большой запас на JSON структуру и метаданные (x3 для безопасности)
            target_words = int((target_duration / 60) * 150)
            required_tokens = int(target_words * 1.5 * 3) + 2000  # x3 запас + 2000 для метаданных
            max_tokens = max(8192, min(required_tokens, 16384))  # От 8192 до 16384 (макс для Gemini 2.5)
            
            log_callback(f"📊 Расчёт токенов: {target_words} слов → {required_tokens} требуется → {max_tokens} установлено")
            
            # Настройки в зависимости от режима строгого следования теме
            if strict_text_theme:
                # Строгий режим: меньше креативности, больше точности
                temperature = 0.6
                top_p = 0.8
                top_k = 40
            else:
                # Креативный режим: больше свободы интерпретации
                temperature = 0.9
                top_p = 0.85
                top_k = 50
            
            # 🎯 JSON MODE: Используем responseSchema для гарантированного структурированного ответа
            response = self._rest_generate_content(
                model=FAST_TEXT_MODEL,  # Flash быстрее и дешевле
                api_key=api_key,
                prompt_text=prompt,
                generation_config={
                    "temperature": temperature,
                    "topP": top_p,
                    "topK": top_k,
                    "maxOutputTokens": max_tokens,
                    "json_mode": True  # Включаем JSON Mode
                },
                safety_settings=[
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
                ],
                response_schema=self.VIDEO_SCRIPT_SCHEMA  # Схема для валидации
            )

            candidates = response.get('candidates', [])
            if not candidates:
                log_callback(f"⚠️ Полный ответ API: {json.dumps(response, ensure_ascii=False, indent=2)[:1000]}")
                block_reason = response.get('promptFeedback', {}).get('blockReason', '')
                reason_suffix = f": {block_reason}" if block_reason else ""
                raise ValueError(f"No candidates in Gemini response{reason_suffix}")
            
            # Check for finish_reason
            finish_reason = candidates[0].get('finishReason', 'UNKNOWN')
            log_callback(f"📊 Finish reason: {finish_reason}")
            
            # Check for safety ratings
            if 'safetyRatings' in candidates[0]:
                log_callback(f"🛡️ Safety ratings: {candidates[0]['safetyRatings']}")

            parts = candidates[0].get('content', {}).get('parts', [])
            response_text = "".join([p.get('text', '') for p in parts]).strip()
            if not response_text:
                log_callback(f"⚠️ Candidate structure: {json.dumps(candidates[0], ensure_ascii=False, indent=2)[:1000]}")
                raise ValueError("API Gemini вернул пустой ответ.")
            
            # Debug: show response length and preview
            log_callback(f"📊 Получен ответ: {len(response_text)} символов")
            log_callback(f"📋 Первые 300 символов: {response_text[:300]}")

            # Parse JSON response with json_repair
            # Initialize result to None before parsing
            result = None
            try:
                result = json.loads(response_text)
                log_callback("✅ Ответ Gemini успешно распарсен как JSON.")
            except json.JSONDecodeError as e:
                log_callback(f"⚠️ Не удалось распарсить JSON напрямую: {e}. Пробуем починить...")
                
                # 🔧 НОВЫЙ ПОДХОД: Используем json_repair для автоматического исправления
                try:
                    result = json.loads(repair_json(response_text))
                    log_callback("✅ JSON успешно починен с помощью json_repair!")
                except Exception as repair_error:
                    log_callback(f"⚠️ json_repair не помог: {repair_error}. Пробуем ручное извлечение...")
                    
                    # Fallback: Try to extract JSON from markdown code blocks
                    if '```json' in response_text:
                        start_idx = response_text.find('```json') + 7
                        end_idx = response_text.find('```', start_idx)
                        if end_idx > start_idx:
                            json_candidate = response_text[start_idx:end_idx].strip()
                            try:
                                result = json.loads(repair_json(json_candidate))
                                log_callback("✅ JSON извлечён из markdown блока и починен.")
                            except Exception:
                                pass
                    
                    # Last resort: Try to find JSON boundaries
                    if result is None:
                        start_idx = response_text.find('{')
                        end_idx = response_text.rfind('}') + 1
                        if start_idx != -1 and end_idx > start_idx:
                            json_candidate = response_text[start_idx:end_idx]
                            try:
                                result = json.loads(repair_json(json_candidate))
                                log_callback("✅ JSON успешно извлечен и починен fallback-методом.")
                            except Exception as e2:
                                # Try to fix common JSON issues in long texts
                                log_callback(f"⚠️ Попытка исправить JSON: {e2}")
                                # Save raw response for debugging
                                log_callback(f"📋 Первые 500 символов ответа: {response_text[:500]}")
                                log_callback(f"📋 Последние 500 символов ответа: {response_text[-500:]}")
                                raise ValueError(f"Не удалось распарсить JSON даже после извлечения. Ошибка: {e2}")
                    else:
                        raise ValueError("Не удалось найти границы JSON в ответе для ручного извлечения.")

            # Quality check for generated content
            quality_issues = self._check_content_quality(result, num_segments, language)
            if quality_issues:
                log_callback(f"⚠️ Обнаружены проблемы с качеством контента: {quality_issues}")

            # --- НОВАЯ ЛОГИКА: Работаем с монолитным текстом ---
            full_text_combined = result.get('full_text', '')
            
            if not full_text_combined or not full_text_combined.strip():
                log_callback("⚠️ 'full_text' пустой. Пробуем 'text_parts'.")
                text_parts = result.get('text_parts', [])
                if text_parts and isinstance(text_parts, list):
                    full_text_combined = " ".join(text_parts)
                else:
                    raise ValueError("Ни 'full_text', ни 'text_parts' не найдены в ответе.")
            
            log_callback(f"📝 Получен текст: {len(full_text_combined)} символов, {count_words(full_text_combined, language)} слов")

            # --- ОЧИСТКА ТЕКСТА ОТ МЕТАДАННЫХ И РЕЖИССЕРСКИХ УКАЗАНИЙ ---
            import re
            
            # Удаляем режиссерские указания в скобках
            full_text_combined = re.sub(r'\([^)]*нарезк[^)]*\)', '', full_text_combined, flags=re.IGNORECASE)
            full_text_combined = re.sub(r'\([^)]*картин[^)]*\)', '', full_text_combined, flags=re.IGNORECASE)
            full_text_combined = re.sub(r'\([^)]*кадр[^)]*\)', '', full_text_combined, flags=re.IGNORECASE)
            full_text_combined = re.sub(r'\([^)]*визуал[^)]*\)', '', full_text_combined, flags=re.IGNORECASE)
            full_text_combined = re.sub(r'\([^)]*начало[^)]*\)', '', full_text_combined, flags=re.IGNORECASE)
            full_text_combined = re.sub(r'\([^)]*конец[^)]*\)', '', full_text_combined, flags=re.IGNORECASE)
            full_text_combined = re.sub(r'\([^)]*музык[^)]*\)', '', full_text_combined, flags=re.IGNORECASE)
            full_text_combined = re.sub(r'\([^)]*звук[^)]*\)', '', full_text_combined, flags=re.IGNORECASE)
            full_text_combined = re.sub(r'\([^)]*эффект[^)]*\)', '', full_text_combined, flags=re.IGNORECASE)
            
            # Удаляем пустые скобки
            full_text_combined = re.sub(r'\(\s*\)', '', full_text_combined)
            
            # Удаляем множественные пробелы
            full_text_combined = re.sub(r'\s+', ' ', full_text_combined).strip()
            
            log_callback(f"🧹 Текст очищен от метаданных: {len(full_text_combined)} символов")

            # --- ПРИМЕНЯЕМ ЛИМИТ СИМВОЛОВ (жесткая обрезка по длительности) ---
            # Рассчитываем адекватный лимит символов для целевой длительности
            # Около 15 символов в секунду - это предел для разборчивого чтения TTS
            # Если текст сильно длиннее, TTS будет длиться 50-60 сек вместо 30 сек
            max_chars_for_duration = int(target_duration * 16)
            
            # Используем меньший лимит из char_limit (если задан) и рассчитанного по длительности
            effective_char_limit = max_chars_for_duration
            if char_limit and char_limit > 0 and char_limit < max_chars_for_duration:
                effective_char_limit = char_limit
                
            # Применяем обрезку только для коротких видео (Shorts), где время критично
            if target_duration <= 60 and len(full_text_combined) > effective_char_limit:
                log_callback(f"✂️ Применяем жесткий лимит для TTS: {len(full_text_combined)} → {effective_char_limit} символов")
                # Обрезаем по границе предложения
                truncated = full_text_combined[:effective_char_limit]
                # Находим последнюю точку, восклицательный или вопросительный знак
                last_sentence_end = max(
                    truncated.rfind('.'),
                    truncated.rfind('!'),
                    truncated.rfind('?')
                )
                if last_sentence_end > effective_char_limit * 0.5:  # Если нашли конец предложения во второй половине
                    full_text_combined = truncated[:last_sentence_end + 1]
                else:
                    # Обрезаем по последнему пробелу
                    last_space = truncated.rfind(' ')
                    if last_space > effective_char_limit * 0.8:
                        full_text_combined = truncated[:last_space] + '...'
                    else:
                        full_text_combined = truncated + '...'
                log_callback(f"✂️ Текст обрезан до {len(full_text_combined)} символов (чтобы уложиться в {target_duration} сек)")

            video_kind = "short" if target_duration <= 60 else "long"
            cta_char_limit = effective_char_limit if target_duration <= 60 else None
            cta_text = ensure_spoken_subscribe_cta(
                full_text_combined,
                language=language,
                video_kind=video_kind,
                max_chars=cta_char_limit,
            )
            if cta_text != full_text_combined:
                full_text_combined = cta_text
                log_callback("🔔 Добавлен органичный призыв подписаться в озвучиваемый текст")

            # --- Разбиваем монолитный текст на части для субтитров ---
            # Используем универсальную функцию разбиения для всех языков
            text_parts = split_text_by_sentences(full_text_combined, language)
            
            log_callback(f"📝 Разбито на {len(text_parts)} предложений (язык: {language})")
            
            # 🔔 ИНТЕРАКТИВНЫЕ ЭЛЕМЕНТЫ И ENGAGEMENT (только для старой системы — вирусная уже включает их в промпт)
            if not use_viral_system and len(text_parts) >= 3:
                import random
                
                # 📚 МУЛЬТИЯЗЫЧНЫЕ ПРИЗЫВЫ К ПОДПИСКЕ
                subscribe_ctas_by_lang = {
                    'Russian': [
                        # Органичные, встроенные в контекст
                        "Кстати, если тебе интересно, подпишись",
                        "Подпишись, дальше будет ещё интереснее",
                        "Ставь лайк если узнал что-то новое",
                        "Подписывайся, такого больше нигде не расскажут",
                        "Жми на колокольчик чтобы не пропустить",
                        "Сохрани это видео, пригодится",
                        "Подпишись, у меня много такого контента",
                        "Лайк если согласен, подписка если хочешь ещё",
                    ],
                    'English': [
                        "By the way, subscribe if you find this interesting",
                        "Subscribe, it gets even better",
                        "Like if you learned something new",
                        "Subscribe, you won't find this anywhere else",
                        "Hit the bell to not miss anything",
                        "Save this video, you'll need it",
                        "Subscribe, I have more content like this",
                        "Like if you agree, subscribe for more",
                    ],
                    'Spanish': [
                        "Por cierto, suscríbete si te interesa",
                        "Suscríbete, esto se pone mejor",
                        "Dale like si aprendiste algo nuevo",
                        "Suscríbete, no encontrarás esto en otro lugar",
                    ],
                    'German': [
                        "Übrigens, abonniere wenn es dich interessiert",
                        "Abonniere, es wird noch besser",
                        "Like wenn du etwas Neues gelernt hast",
                    ],
                    'French': [
                        "Au fait, abonne-toi si ça t'intéresse",
                        "Abonne-toi, ça devient encore mieux",
                        "Like si tu as appris quelque chose",
                    ],
                }
                
                interactive_questions_by_lang = {
                    'Russian': [
                        "А ты как думаешь?",
                        "Согласен? Пиши в комментах",
                        "Знал об этом?",
                        "Как бы ты поступил?",
                        "Твоё мнение?",
                        "Веришь в это?",
                        "Что скажешь?",
                    ],
                    'English': [
                        "What do you think?",
                        "Agree? Comment below",
                        "Did you know this?",
                        "What would you do?",
                        "Your thoughts?",
                        "Do you believe this?",
                        "What do you say?",
                    ],
                    'Spanish': [
                        "¿Qué opinas?",
                        "¿Estás de acuerdo?",
                        "¿Lo sabías?",
                    ],
                    'German': [
                        "Was denkst du?",
                        "Stimmst du zu?",
                        "Wusstest du das?",
                    ],
                    'French': [
                        "Qu'en penses-tu?",
                        "Tu es d'accord?",
                        "Tu le savais?",
                    ],
                }
                
                cliffhangers_by_lang = {
                    'Russian': [
                        "Но это ещё не всё",
                        "А дальше самое интересное",
                        "Продолжение следует",
                        "Но самое шокирующее впереди",
                        "Это только начало",
                        "Следующее видео тебя удивит",
                    ],
                    'English': [
                        "But that's not all",
                        "The best part is coming",
                        "To be continued",
                        "The most shocking part is next",
                        "This is just the beginning",
                        "Next video will surprise you",
                    ],
                    'Spanish': [
                        "Pero eso no es todo",
                        "Lo mejor está por venir",
                        "Continuará",
                    ],
                    'German': [
                        "Aber das ist noch nicht alles",
                        "Das Beste kommt noch",
                        "Fortsetzung folgt",
                    ],
                    'French': [
                        "Mais ce n'est pas tout",
                        "Le meilleur reste à venir",
                        "À suivre",
                    ],
                }
                
                # Получаем фразы для нужного языка (fallback на English)
                subscribe_ctas = subscribe_ctas_by_lang.get(language, subscribe_ctas_by_lang['English'])
                interactive_questions = interactive_questions_by_lang.get(language, interactive_questions_by_lang['English'])
                cliffhangers = cliffhangers_by_lang.get(language, cliffhangers_by_lang['English'])
                already_has_subscribe_cta = has_spoken_subscribe_cta(full_text_combined)
                
                # 🎬 РАЗНАЯ ЛОГИКА ДЛЯ SHORTS И LONGS
                if is_short:
                    # SHORTS: 1 призыв в середине, 1 вопрос, 1 cliffhanger
                    if not already_has_subscribe_cta:
                        cta = random.choice(subscribe_ctas)
                        insert_position = int(len(text_parts) * 0.45)
                        text_parts.insert(insert_position, cta)
                        already_has_subscribe_cta = True
                        log_callback(f"🔔 Добавлен призыв к подписке: '{cta}'")
                    
                    question = random.choice(interactive_questions)
                    question_position = int(len(text_parts) * 0.65)
                    text_parts.insert(question_position, question)
                    log_callback(f"💬 Добавлен интерактивный вопрос: '{question}'")
                    
                    cliffhanger = random.choice(cliffhangers)
                    text_parts.append(cliffhanger)
                    log_callback(f"🎬 Добавлен cliffhanger: '{cliffhanger}'")
                else:
                    # LONGS: Несколько призывов распределённых по видео
                    # Каждые ~2 минуты контента добавляем элемент
                    num_engagements = max(2, int(target_duration / 120))  # 1 на каждые 2 минуты
                    
                    # Позиции: 25%, 50%, 75% и в конце
                    positions = [0.25, 0.5, 0.75]
                    if num_engagements > 3:
                        positions = [i / (num_engagements + 1) for i in range(1, num_engagements + 1)]
                    
                    inserted = 0
                    for i, pos in enumerate(positions[:num_engagements]):
                        insert_idx = int(len(text_parts) * pos) + inserted
                        
                        # Чередуем типы: вопрос, призыв, вопрос, призыв...
                        if i % 2 == 0:
                            element = random.choice(interactive_questions)
                            log_callback(f"💬 [{int(pos*100)}%] Вопрос: '{element}'")
                        elif not already_has_subscribe_cta:
                            element = random.choice(subscribe_ctas)
                            already_has_subscribe_cta = True
                            log_callback(f"🔔 [{int(pos*100)}%] Призыв: '{element}'")
                        else:
                            element = random.choice(interactive_questions)
                            log_callback(f"💬 [{int(pos*100)}%] Вопрос: '{element}'")
                        
                        text_parts.insert(insert_idx, element)
                        inserted += 1
                    
                    # Cliffhanger в конец
                    cliffhanger = random.choice(cliffhangers)
                    text_parts.append(cliffhanger)
                    log_callback(f"🎬 Добавлен финальный cliffhanger: '{cliffhanger}'")

                full_text_combined = " ".join(text_parts)
            
            # Объединяем очень короткие предложения
            merged_text_parts = self._merge_short_segments(text_parts, min_words_per_segment=8, max_segments=None)
            if len(merged_text_parts) < len(text_parts):
                log_callback(f"🔗 Объединены короткие сегменты: {len(text_parts)} → {len(merged_text_parts)} частей")
            
            # 🎬 SHORT TEXT ENHANCER: Постобработка для коротких видео
            if is_short and target_duration <= 60:
                log_callback("🎬 Применяем ShortTextEnhancer для улучшения текста...")
                try:
                    enhanced_text = ShortTextEnhancer.enhance_short_text(
                        text=full_text_combined,
                        duration=target_duration,
                        language=language,
                        remove_forbidden=True,  # Удаляем слова-паразиты
                        add_pauses=True,        # Добавляем драматические паузы
                        add_numbers=False,      # Не заменяем абстракции на числа (может исказить факты)
                        add_contrast=False,     # Контрастные переходы уже есть в вирусной системе
                        add_cta=False,          # CTA уже добавлены выше
                        number_chance=0.3
                    )
                    if enhanced_text and len(enhanced_text) > len(full_text_combined) * 0.5:
                        full_text_combined = enhanced_text
                        # Пересобираем text_parts и мержим короткие сегменты
                        text_parts_enhanced = split_text_by_sentences(full_text_combined, language)
                        merged_text_parts = self._merge_short_segments(text_parts_enhanced, min_words_per_segment=8, max_segments=None)
                        log_callback(f"✅ Текст улучшен ShortTextEnhancer: {len(full_text_combined)} символов")
                except Exception as enhance_error:
                    log_callback(f"⚠️ ShortTextEnhancer не применён: {enhance_error}")

            cta_text = ensure_spoken_subscribe_cta(
                full_text_combined,
                language=language,
                video_kind=video_kind,
                max_chars=cta_char_limit,
            )
            if cta_text != full_text_combined:
                full_text_combined = cta_text
                text_parts = split_text_by_sentences(full_text_combined, language)
                merged_text_parts = self._merge_short_segments(text_parts, min_words_per_segment=8, max_segments=None)
                log_callback("🔔 Призыв подписаться сохранён после постобработки текста")
            
            # Проверка длины текста (без обрезки, только логирование)
            expected_chars = int(target_duration * 10)
            if len(full_text_combined) < expected_chars * 0.5:
                log_callback(f"⚠️ Текст короче ожидаемого: {len(full_text_combined)} символов (ожидалось ~{expected_chars})")
                log_callback("   💡 AI сгенерировал меньше контента. Попробуйте перегенерировать.")
            
            # Save original unique count BEFORE expansion
            original_unique_count = len(set(merged_text_parts))
            
            # ИСПРАВЛЕНО: Сохраняем оригинальные text_parts для субтитров
            original_text_parts = merged_text_parts.copy()
            
            # Adjust text parts for images to match num_segments
            if len(merged_text_parts) < num_segments:
                # Expand: duplicate parts intelligently
                log_callback(f"📝 Расширение текстовых частей: {len(merged_text_parts)} → {num_segments} для соответствия количеству шотов")
                expanded_parts = []
                for i in range(num_segments):
                    expanded_parts.append(merged_text_parts[i % len(merged_text_parts)])
                merged_text_parts = expanded_parts
            elif len(merged_text_parts) > num_segments:
                # Reduce: select evenly distributed parts
                log_callback(f"📝 Сокращение текстовых частей: {len(merged_text_parts)} → {num_segments} для соответствия количеству шотов")
                # Select parts at even intervals
                indices = [int(i * len(merged_text_parts) / num_segments) for i in range(num_segments)]
                merged_text_parts = [merged_text_parts[i] for i in indices]
            
            return {
                'title': result.get('clickbait_title', theme),  # Без префикса "Всё о"
                'description': result.get('description', f"{t('detailed_story', language)} {theme}"),
                'full_text': full_text_combined, # Для субтитров
                'text_parts': original_text_parts, # ✅ ИСПРАВЛЕНО: Оригинальные для субтитров (не расширенные!)
                'text_parts_for_images': merged_text_parts, # Расширенные для изображений
                'hashtags': result.get('hashtags', [f'#{theme}']),
                'original_unique_count': original_unique_count  # Для проверки вариаций
            }

        except (json.JSONDecodeError, ValueError) as e:
            log_callback(f"❌ Ошибка обработки ответа Gemini: {e}")
            raise
        except Exception as e:
            log_callback(f"❌ Непредвиденная ошибка API Gemini: {e}")
            import traceback
            log_callback(f"📋 Traceback: {traceback.format_exc()}")
            raise

    def _check_content_quality(self, result: dict, num_segments: int, language: str = 'Russian') -> list:
        """
        Check quality of generated content and return list of issues
        """
        issues = []

        # Check for required fields
        required_fields = ['clickbait_title', 'description', 'hashtags']
        for field in required_fields:
            if field not in result or not result[field]:
                issues.append(f"Missing or empty field: {field}")

        # 🔧 ИСПРАВЛЕНО: Проверяем full_text (основное поле от Gemini), а не text_parts
        full_text = result.get('full_text', '')
        if not full_text or not full_text.strip():
            issues.append("Missing or empty 'full_text'")
        else:
            # Check text content quality with language awareness
            text_issues = self._check_text_quality(full_text, language)
            issues.extend(text_issues)

        # Check hashtags
        if 'hashtags' in result and result['hashtags']:
            hashtag_issues = self._check_hashtags_quality(result['hashtags'])
            issues.extend(hashtag_issues)

        return issues

    def _check_text_quality(self, text: str, language: str = 'Russian') -> list:
        """
        Check quality of generated text (language-aware)
        """
        issues = []

        if not text or len(text.strip()) < 10:
            issues.append("Text too short")
            return issues

        # 🔧 ИСПРАВЛЕНО: Проверка письменности только для кириллических языков
        if language in ('Russian', 'Ukrainian', 'Bulgarian', 'Serbian'):
            cyrillic_chars = sum(1 for char in text if '\u0400' <= char <= '\u04FF')
            total_chars = sum(1 for char in text if char.isalpha())
            if total_chars > 0:
                cyrillic_ratio = cyrillic_chars / total_chars
                if cyrillic_ratio < 0.7:
                    percent = int(cyrillic_ratio * 100)
                    issues.append(f"Low Cyrillic ratio: {percent}% (<70%)")

        # Check length
        word_count = len(text.split())
        if word_count < 10:
            issues.append(f"Text too short: {word_count} words")
        elif word_count > 400:
            issues.append(f"Text too long: {word_count} words")

        return issues

    def _check_hashtags_quality(self, hashtags: list) -> list:
        """
        Check quality of hashtags
        """
        issues = []

        if not hashtags or len(hashtags) < 2:
            issues.append("Too few hashtags")

        # Check for Cyrillic hashtags
        for hashtag in hashtags:
            if not hashtag.startswith('#'):
                issues.append(f"Hashtag without #: {hashtag}")
            # Check if there's at least one letter after the #
            elif not any(char.isalpha() for char in hashtag[1:]):
                issues.append(f"Hashtag contains no letters: {hashtag}")

        return issues

    def translate_to_english(self, text: str, log_callback: Callable[[str], None] = _dummy_log, api_key: str = None) -> str:
        """Translates a given text to English using the Gemini REST API."""
        # Используем сохранённый api_key или переданный
        key = api_key or self.api_key
        if not key or not self._is_valid_gemini_api_key(key):
            log_callback("⚠️ API ключ не предоставлен для перевода.")
            return text  # Return original text if API key is not available

        log_callback(f"🌍 Перевод на английский: '{text[:50]}...'")
        try:
            # A simple prompt for translation
            prompt = f"Translate the following Russian text to English. Return only the translated text, without any introductory phrases or explanations. Text to translate: \"{text}\""
            
            result = self._rest_generate_content(
                model=FAST_TEXT_MODEL,
                api_key=key,
                prompt_text=prompt,
                generation_config={
                    'temperature': 0.2  # Lower temperature for more precise translation
                }
            )
            
            # Извлекаем текст из ответа
            translated_text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
            
            if translated_text:
                log_callback(f"✅ Перевод: '{translated_text[:50]}...'")
                return translated_text
            else:
                log_callback("⚠️ Пустой ответ от API, возвращаем оригинал")
                return text
        except Exception as e:
            log_callback(f"❌ Ошибка при переводе текста: {e}")
            return text  # Return original text in case of an error

    def generate_long_text_multipart(self, theme: str, api_key: str, target_duration: float, num_segments: int, log_callback: Callable[[str], None] = _dummy_log, language: str = 'Russian', variation_index: int = 1) -> Dict[str, Any]:
        """
        Generate text for VERY LONG videos (>10 minutes) using multi-part generation.
        
        Strategy:
        1. Split video into chapters (~5 minutes each)
        2. Generate text for each chapter separately
        3. Combine all chapters into one coherent text
        
        This bypasses Gemini's token limit and ensures high-quality content for long videos.
        
        Args:
            theme: Video theme
            api_key: Gemini API key
            target_duration: Total video duration in seconds
            num_segments: Total number of shots/segments
            log_callback: Logging function
            
        Returns:
            Dict with title, description, full_text, text_parts, etc.
        """
        
        # Determine chapter length based on video duration
        # Для коротких видео (5-10 мин) - 1-2 главы
        # Для средних (10-30 мин) - 2-6 глав  
        # Для длинных (30+ мин) - больше глав (уменьшены до 5 мин для лучшего качества)
        
        if target_duration <= 600:  # До 10 минут
            CHAPTER_DURATION = target_duration  # Одна глава на всё видео
            num_chapters = 1
        elif target_duration <= 1800:  # 10-30 минут
            CHAPTER_DURATION = 300  # 5 минут на главу
            num_chapters = max(2, min(6, int(target_duration / CHAPTER_DURATION)))
        else:  # Больше 30 минут
            # 🔧 ОПТИМИЗАЦИЯ: Уменьшено с 10 до 5 минут на главу
            # Gemini лучше справляется с ~750 словами чем с ~1500
            CHAPTER_DURATION = 300  # 5 минут на главу (было 600)
            num_chapters = max(4, int(target_duration / CHAPTER_DURATION))  # Минимум 4 главы
        
        # Пересчитываем реальную длительность главы
        CHAPTER_DURATION = target_duration / num_chapters
        
        log_callback(f"📚 Многоэтапная генерация: {num_chapters} глав по ~{CHAPTER_DURATION:.0f}s")
        log_callback(f"   Общая длительность: {target_duration}s ({target_duration/60:.1f} минут)")
        
        # Step 1: Generate chapter plan
        log_callback(f"📋 Шаг 1/{num_chapters+2}: Создание плана глав...")
        
        # Рассчитываем целевое количество слов на всё видео
        total_target_words = int((target_duration / 60) * 150)
        words_per_chapter = total_target_words // num_chapters
        subscribe_cta_plan = build_long_subscribe_cta_plan(target_duration, num_chapters)
        log_callback(f"🔔 План подписки для лонга: {len(subscribe_cta_plan)} точек на {target_duration/60:.1f} мин")
        
        # Определяем язык для промпта (используем систему переводов)
        lang_instruction = get_language_instruction(language)
        long_form_contract = self._build_long_form_consistency_contract(
            theme=theme,
            target_duration=target_duration,
            num_chapters=num_chapters,
            words_per_chapter=words_per_chapter,
            language=language,
        )
        creative_angle = build_creative_angle_instruction(variation_index, theme)
        
        plan_prompt = f"""Create a plan for a video about "{theme}" with duration {target_duration/60:.0f} minutes.
{lang_instruction}

⚠️ STRICT: Create EXACTLY {num_chapters} chapters (no more, no less!)
Each chapter ~{words_per_chapter} words (~{CHAPTER_DURATION/60:.1f} minutes)
Total ~{total_target_words} words for the entire video.

{creative_angle}

{"For a short video, create ONE cohesive story without splitting." if num_chapters == 1 else f"Split into {num_chapters} logical parts."}

{long_form_contract}

{subject_first_prompt_rules(language)}

Return JSON:
{{
    "clickbait_title": "Clear, intriguing title in sentence case",
    "description": "Brief description",
    "narrative_contract": "One paragraph describing the central thesis, tone, timeline/order, recurring terms, and boundaries for the whole video",
    "chapters": [
        {{
            "title": "Chapter 1 title",
            "macro_block": "Macro-block name shared by 4-6 related chapters",
            "focus": "Chapter 1 focus",
            "purpose": "Why this chapter exists in the whole arc",
            "key_points": ["Concrete point 1", "Concrete point 2"],
            "bridge_from_previous": "How this continues from the previous chapter, empty only for chapter 1",
            "bridge_to_next": "How this prepares the next chapter",
            "avoid_repeating": ["Ideas already covered or too generic for this chapter"]
        }},
        ... (EXACTLY {num_chapters} chapters)
    ],
    "hashtags": ["#tag1", "#tag2"]
}}
"""
        
        try:
            # 🎯 JSON MODE с схемой для плана глав
            plan_response = self._rest_generate_content(
                model=FAST_TEXT_MODEL,
                api_key=api_key,
                prompt_text=plan_prompt,
                generation_config={
                    "temperature": 0.6,
                    "topP": 0.75,
                    "topK": 40,
                    # A 24-36 chapter JSON plan does not reliably fit into 4096
                    # tokens, especially in Cyrillic and CJK languages.
                    "maxOutputTokens": max(8192, min(32768, num_chapters * 700)),
                    "json_mode": True
                },
                response_schema=self.CHAPTER_PLAN_SCHEMA
            )
            
            candidates = plan_response.get('candidates', [])
            if not candidates:
                raise ValueError("No plan generated")
            
            parts = candidates[0].get('content', {}).get('parts', [])
            plan_text = "".join([p.get('text', '') for p in parts]).strip()
            # 🔧 Используем json_repair для надежного парсинга
            try:
                plan = json.loads(plan_text)
            except json.JSONDecodeError:
                log_callback("⚠️ Битый JSON плана, пытаемся починить...")
                plan = json.loads(repair_json(plan_text))
            
            chapters = plan.get('chapters', [])
            
            # 🎯 СТРОГО контролируем количество глав
            if len(chapters) > num_chapters:
                log_callback(f"⚠️ Получено {len(chapters)} глав, обрезаем до {num_chapters}...")
                chapters = chapters[:num_chapters]
            elif len(chapters) < num_chapters:
                log_callback(f"⚠️ Получено {len(chapters)} глав вместо {num_chapters}, дополняем...")
                for i in range(len(chapters), num_chapters):
                    chapters.append({
                        "title": f"{t('chapter', language)} {i+1}",
                        "macro_block": f"Macro-block {i // 5 + 1}",
                        "focus": f"{t('continuation', language)} '{theme}'",
                        "purpose": "Continue the central narrative without restarting the topic",
                        "key_points": [],
                        "bridge_from_previous": "Continue from the previous chapter",
                        "bridge_to_next": "Prepare the next chapter",
                        "avoid_repeating": []
                    })

            for i, chapter in enumerate(chapters):
                chapter.setdefault("macro_block", f"Macro-block {i // 5 + 1}")
            
            log_callback(f"✅ План создан: {len(chapters)} глав")
            for i, ch in enumerate(chapters[:5]):  # Show first 5
                log_callback(f"   {i+1}. {ch.get('title', 'Без названия')}")
            if len(chapters) > 5:
                log_callback(f"   ... и ещё {len(chapters)-5} глав")
            
        except Exception as e:
            log_callback(f"⚠️ Ошибка создания плана: {e}. Используем простое разбиение.")
            # Fallback: simple chapter division
            chapters = []
            for i in range(num_chapters):
                chapters.append({
                    "title": f"{t('part', language)} {i+1}: {theme}",
                    "macro_block": f"Macro-block {i // 5 + 1}",
                    "focus": f"{t('aspect', language)} {i+1}: advance the argument with a new concrete question",
                    "purpose": [
                        "Orient the listener and establish the central question",
                        "Add historical or causal context",
                        "Examine mechanisms and concrete evidence",
                        "Compare consequences and competing interpretations",
                        "Synthesize the block and hand off the next question",
                    ][i % 5],
                    "key_points": [f"A distinct, non-repeated angle {i+1} of {theme}"],
                    "bridge_from_previous": "Continue from the previous chapter",
                    "bridge_to_next": "Prepare the next chapter",
                    "avoid_repeating": []
                })
            plan = {
                "clickbait_title": theme,  # Без префикса "Всё о"
                "description": f"{t('detailed_story', language)} {theme}",
                "narrative_contract": f"One coherent long-form video about {theme}.",
                "hashtags": [f"#{theme}"]
            }
        
        plan_contract = str(plan.get('narrative_contract') or '').strip()
        if not plan_contract:
            plan_contract = f"One coherent long-form video about {theme}, with stable tone, order, terms, and boundaries."

        # Step 2: Generate text for each chapter
        all_chapter_texts = []
        
        for i, chapter in enumerate(chapters):
            log_callback(f"📝 Шаг {i+2}/{num_chapters+2}: Генерация главы {i+1}/{num_chapters}: '{chapter.get('title', 'Без названия')}'...")
            
            chapter_duration = CHAPTER_DURATION if i < num_chapters - 1 else (target_duration - i * CHAPTER_DURATION)
            chapter_words = int((chapter_duration / 60) * 150)  # 150 words per minute
            
            # Рассчитываем СТРОГИЙ лимит слов для главы
            min_words = int(chapter_words * 0.9)
            max_words = int(chapter_words * 1.1)
            
            # Определяем язык для промпта (используем систему переводов)
            lang_note = get_language_instruction(language)
            key_points = chapter.get('key_points') or []
            if isinstance(key_points, list):
                key_points_text = "\n".join(f"- {point}" for point in key_points[:8]) or "- Add concrete, non-repeated points for this chapter."
            else:
                key_points_text = f"- {key_points}"
            avoid_repeating = chapter.get('avoid_repeating') or []
            if isinstance(avoid_repeating, list):
                avoid_repeating_text = "\n".join(f"- {item}" for item in avoid_repeating[:8]) or "- Do not repeat generic intros or already covered setup."
            else:
                avoid_repeating_text = f"- {avoid_repeating}"
            plan_window = self._format_long_form_plan_window(chapters, i)
            rolling_memory = self._build_long_form_rolling_memory(all_chapter_texts, language)
            coverage_ledger = self._build_long_form_coverage_ledger(chapters, len(all_chapter_texts))
            chapter_cta_slots = [
                slot for slot in subscribe_cta_plan
                if int(slot.get("chapter_index", -1)) == i
            ]
            subscribe_cta_instruction = build_long_subscribe_cta_instruction(
                language,
                chapter_slots=chapter_cta_slots,
                total_slots=len(subscribe_cta_plan),
            )
            
            # 📝 LONG TEXT ENHANCER: Улучшаем промпт с эмоциональной аркой
            base_chapter_prompt = f"""Write text for a video chapter about "{theme}".
{lang_note}

{long_form_contract}

GLOBAL NARRATIVE CONTRACT FROM PLAN:
{plan_contract}

PLAN WINDOW:
{plan_window}

{rolling_memory}

{coverage_ledger}

Chapter {i+1}/{num_chapters}: "{chapter.get('title', '')}"
Focus: {chapter.get('focus', '')}
Purpose: {chapter.get('purpose', '')}
Bridge from previous: {chapter.get('bridge_from_previous', '')}
Bridge to next: {chapter.get('bridge_to_next', '')}

Key points to cover:
{key_points_text}

Avoid repeating:
{avoid_repeating_text}

⚠️ CRITICAL - WORD LIMIT:
- MINIMUM: {min_words} words (MANDATORY!)
- TARGET: {chapter_words} words
- MAXIMUM: {max_words} words  
- This is ~{chapter_duration:.0f} seconds of narration (150 words/minute)
- TEXT MUST BE AT LEAST {min_words} WORDS!

STYLE:
- Long-form documentary/explainer style: clear, steady, information dense
- Short sentences for AI narrator, but not choppy
- {"Set the central promise and orientation" if i == 0 else "Continue from previous context without reintroducing the whole topic"}
- {"Finale with synthesis and a natural call to action" if i == num_chapters-1 else "End with a useful bridge to the next chapter, not a fake cliffhanger"}
- Add DETAILS, FACTS, EXAMPLES to reach required length

{subscribe_cta_instruction}

FORBIDDEN:
- Do NOT use: "chapter", "section", "part"
- Do NOT write less than {min_words} words!
- Do NOT shout, overdramatize, or pad with generic hype.
- Do NOT contradict the plan contract or earlier chapters.
"""
            
            # 🎭 Добавляем драматургическую арку (inline, заменяет удалённый LongTextEnhancer)
            arc_position = (i + 1) / num_chapters
            if arc_position <= 0.25:
                arc_note = "NARRATIVE ARC: Opening - orient the viewer, establish the central promise, and start the first concrete thread."
            elif arc_position <= 0.5:
                arc_note = "NARRATIVE ARC: Development - add causes, context, examples, and consequences without repeating earlier setup."
            elif arc_position <= 0.75:
                arc_note = "NARRATIVE ARC: Deepening - connect the strongest details and resolve the main questions raised so far."
            else:
                arc_note = "NARRATIVE ARC: Resolution - synthesize the material, close open loops, and end naturally."

            chapter_prompt = base_chapter_prompt + f"\n{arc_note}\n"
            
            # Добавляем формат вывода (на языке видео)
            chapter_prompt += f"""

{t('return_json', language)}:
{{
    "chapter_text": "{t('chapter_text', language)} ({t('minimum_words', language)} {min_words} {t('words', language)}!)"
}}
"""
            
            # 🔄 RETRY логика - пробуем до 3 раз, НЕТ fallback на generic текст
            max_retries = 3
            chapter_text = None
            
            for attempt in range(max_retries):
                try:
                    # 🎯 Достаточно токенов для русского текста + JSON
                    # Русский текст ~2-3 токена на слово, + JSON обёртка
                    max_tokens = max(8192, int(chapter_words * 4) + 500)
                    
                    if attempt == 0:
                        # Первая попытка - JSON mode
                        chapter_response = self._rest_generate_content(
                            model=FAST_TEXT_MODEL,
                            api_key=api_key,
                            prompt_text=chapter_prompt,
                            generation_config={
                                "temperature": 0.65,
                                "topP": 0.8,
                                "topK": 40,
                                "maxOutputTokens": max_tokens,
                                "json_mode": True
                            },
                            response_schema=self.CHAPTER_TEXT_SCHEMA
                        )
                    else:
                        # Повторные попытки - без JSON mode, простой текст
                        log_callback(f"   🔄 {t('retry_attempt', language)} {attempt + 1}/{max_retries} ({t('simple_mode', language)})...")
                        
                        # 🌍 Промпт на языке видео
                        lang_inst = get_language_instruction(language)
                        simple_prompt = f"""Write the next long-form video chapter about "{theme}".
{lang_inst}

{long_form_contract}

GLOBAL NARRATIVE CONTRACT FROM PLAN:
{plan_contract}

PLAN WINDOW:
{plan_window}

{rolling_memory}

{coverage_ledger}

Chapter title: {chapters[i].get('title', f"{t('part', language)} {i+1}")}
Focus: {chapter.get('focus', '')}
Purpose: {chapter.get('purpose', '')}
Volume: {chapter_words} words (~{chapter_duration:.0f} seconds narration)

Rules:
- Continue from the previous context; do not restart the entire topic.
- Keep a calm long-form documentary/explainer tone.
{subscribe_cta_instruction}
- Cover these key points:
{key_points_text}
- Avoid repeating:
{avoid_repeating_text}
- Write only the narration text.
"""
                        
                        chapter_response = self._rest_generate_content(
                            model=FAST_TEXT_MODEL,
                            api_key=api_key,
                            prompt_text=simple_prompt,
                            generation_config={
                                "temperature": 0.7,
                                "maxOutputTokens": max_tokens
                            }
                        )
                    
                    candidates = chapter_response.get('candidates', [])
                    if not candidates:
                        raise ValueError(t('empty_response', language))
                    
                    parts = candidates[0].get('content', {}).get('parts', [])
                    raw_text = "".join([p.get('text', '') for p in parts]).strip()
                    
                    if not raw_text:
                        raise ValueError(t('empty_text', language))
                    
                    # Парсим JSON или используем как есть
                    if attempt == 0:
                        try:
                            chapter_data = json.loads(raw_text)
                            chapter_text = chapter_data.get('chapter_text', '')
                        except json.JSONDecodeError:
                            log_callback(f"   ⚠️ {t('broken_json', language)}...")
                            chapter_data = json.loads(repair_json(raw_text))
                            chapter_text = chapter_data.get('chapter_text', '')
                    else:
                        # Простой текст без JSON
                        chapter_text = raw_text
                    
                    if chapter_text and len(chapter_text) > 100:
                        # 🔧 ПРОВЕРКА: Достаточно ли слов?
                        actual_words = count_words(chapter_text, language)
                        if actual_words < min_words * 0.8:  # Меньше 80% от минимума
                            log_callback(f"   ⚠️ Слишком мало слов: {actual_words} < {min_words} (минимум)")
                            if attempt < max_retries - 1:
                                raise ValueError(f"Недостаточно слов: {actual_words} < {min_words}")
                            # На последней попытке принимаем что есть

                        repetition_ratio = self._chapter_exact_repetition_ratio(
                            chapter_text,
                            all_chapter_texts,
                            language,
                        )
                        if repetition_ratio > 0.10:
                            log_callback(
                                f"   ⚠️ Дословные повторы из прошлых глав: {repetition_ratio:.0%}"
                            )
                            if attempt < max_retries - 1:
                                raise ValueError(
                                    f"Слишком много дословных повторов: {repetition_ratio:.0%}"
                                )
                        
                        all_chapter_texts.append(chapter_text)
                        chapter['start_time'] = i * CHAPTER_DURATION
                        chapter['duration'] = chapter_duration
                        chapter['text'] = chapter_text
                        chapter['continuity_summary'] = self._chapter_continuity_summary(
                            chapter_text,
                            language,
                        )
                        log_callback(f"   ✅ Глава {i+1}: {len(chapter_text)} символов, {actual_words} слов")
                        break  # Успех!
                    else:
                        raise ValueError(f"Слишком короткий текст: {len(chapter_text) if chapter_text else 0} символов")
                        
                except Exception as e:
                    log_callback(f"   ⚠️ Попытка {attempt + 1}/{max_retries} не удалась: {e}")
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(2)  # Пауза перед retry
                    else:
                        # ❌ ВСЕ попытки провалились - КРИТИЧЕСКАЯ ОШИБКА
                        raise RuntimeError(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось сгенерировать текст для главы {i+1} после {max_retries} попыток. Последняя ошибка: {e}")
        
        # Step 3: Combine all chapters
        log_callback(f"🔗 Шаг {num_chapters+2}/{num_chapters+2}: Объединение {len(all_chapter_texts)} глав...")
        
        full_text_combined = " ".join(all_chapter_texts)
        cta_text = ensure_long_spoken_subscribe_ctas(
            full_text_combined,
            language=language,
            target_duration=target_duration,
        )
        if cta_text != full_text_combined:
            full_text_combined = cta_text
            log_callback(f"🔔 Применён умный план подписки: {len(subscribe_cta_plan)} точек")
        log_callback(f"✅ Итоговый текст: {len(full_text_combined)} символов, {count_words(full_text_combined, language)} слов")
        
        # Split into sentences for subtitles using universal function
        text_parts = split_text_by_sentences(full_text_combined, language)
        
        log_callback(f"📝 Разбито на {len(text_parts)} предложений (язык: {language})")
        
        # Merge short segments
        merged_text_parts = self._merge_short_segments(text_parts, min_words_per_segment=8)
        if len(merged_text_parts) < len(text_parts):
            log_callback(f"🔗 Объединены короткие сегменты: {len(text_parts)} → {len(merged_text_parts)} частей")
        
        # Save original for subtitles
        original_text_parts = merged_text_parts.copy()
        original_unique_count = len(set(merged_text_parts))
        
        # Adjust for images
        if len(merged_text_parts) < num_segments:
            log_callback(f"📝 Расширение текстовых частей: {len(merged_text_parts)} → {num_segments}")
            expanded_parts = []
            for i in range(num_segments):
                expanded_parts.append(merged_text_parts[i % len(merged_text_parts)])
            merged_text_parts = expanded_parts
        elif len(merged_text_parts) > num_segments:
            log_callback(f"📝 Сокращение текстовых частей: {len(merged_text_parts)} → {num_segments}")
            indices = [int(i * len(merged_text_parts) / num_segments) for i in range(num_segments)]
            merged_text_parts = [merged_text_parts[i] for i in indices]

        actual_words = count_words(full_text_combined, language)
        repetition_ratios = []
        previous_texts = []
        for chapter_text in all_chapter_texts:
            repetition_ratios.append(
                self._chapter_exact_repetition_ratio(chapter_text, previous_texts, language)
            )
            previous_texts.append(chapter_text)
        quality_warnings = []
        volume_ratio = actual_words / max(1, total_target_words)
        if volume_ratio < 0.85 or volume_ratio > 1.15:
            quality_warnings.append(
                f"Narration volume is {volume_ratio:.0%} of the target"
            )
        max_repetition = max(repetition_ratios, default=0.0)
        if max_repetition > 0.10:
            quality_warnings.append(
                f"A chapter repeats {max_repetition:.0%} of substantial sentences verbatim"
            )
        if any(not (chapter.get('key_points') or []) for chapter in chapters):
            quality_warnings.append("Some chapter plan entries have no concrete key points")
        if quality_warnings:
            for warning in quality_warnings:
                log_callback(f"⚠️ Long-form QA: {warning}")
        else:
            log_callback("✅ Long-form QA: объём, структура и дословные повторы в норме")
        
        return {
            'title': plan.get('clickbait_title', theme),  # Без префикса "Всё о"
            'description': plan.get('description', f"{t('detailed_story', language)} {theme}"),
            'full_text': full_text_combined,
            'text_parts': original_text_parts,  # For subtitles
            'text_parts_for_images': merged_text_parts,  # For images
            'hashtags': plan.get('hashtags', [f'#{theme}']),
            'original_unique_count': original_unique_count,
            'chapters': chapters,  # Save chapter info for reference
            'language': language,  # ✅ Добавляем язык для корректного разбиения
            'long_form_quality': {
                'target_words': total_target_words,
                'actual_words': actual_words,
                'volume_ratio': volume_ratio,
                'chapter_count': len(chapters),
                'max_exact_sentence_repetition_ratio': max_repetition,
                'warnings': quality_warnings,
            },
        }

    def _merge_short_segments(self, text_parts: List[str], min_words_per_segment: int = 10, max_segments: int = None) -> List[str]:
        """
        Merge short text segments to ensure minimum word count per segment.
        Prevents very short segments (5-8 words) that do not make sense.
        
        Args:
            text_parts: List of text segments
            min_words_per_segment: Minimum words required per segment (default 10, lowered from 15)
            max_segments: Maximum number of segments to keep (optional)
        
        Returns:
            Merged list of text parts with better length
        """
        if not text_parts:
            return []
        
        # If all segments are very short, don't merge them all into one
        # Keep at least 2-3 segments for variety
        total_words = sum(len(part.split()) for part in text_parts)
        avg_words = total_words / len(text_parts) if text_parts else 0
        
        # If average is very low (< 8 words), lower the threshold
        if avg_words < 8:
            min_words_per_segment = max(5, int(avg_words * 1.5))
        
        merged = []
        current_segment = ""
        
        for part in text_parts:
            # Count words in current combined segment
            combined = f"{current_segment} {part}".strip()
            word_count = len(combined.split())
            
            if word_count >= min_words_per_segment:
                # Current combined segment is long enough
                merged.append(combined)
                current_segment = ""
            else:
                # Add to current segment and continue
                current_segment = combined
        
        # Don't lose the last segment
        if current_segment.strip():
            if len(merged) > 0 and len(merged) >= 2:
                # Merge with previous only if we already have at least 2 segments
                merged[-1] = f"{merged[-1]} {current_segment}".strip()
            else:
                # Keep as separate segment to maintain variety
                merged.append(current_segment)
        
        # Ensure we have at least 2 segments for variety (unless original had only 1)
        if len(merged) == 1 and len(text_parts) > 1:
            # Split the single merged segment back into 2-3 parts
            full_text = merged[0]
            words = full_text.split()
            if len(words) >= 10:
                # Split into 2-3 roughly equal parts
                num_parts = min(3, len(text_parts))
                words_per_part = len(words) // num_parts
                merged = []
                for i in range(num_parts):
                    start = i * words_per_part
                    end = start + words_per_part if i < num_parts - 1 else len(words)
                    merged.append(' '.join(words[start:end]))

        if max_segments and max_segments > 0 and len(merged) > max_segments:
            kept = merged[:max_segments - 1]
            tail = ' '.join(merged[max_segments - 1:]).strip()
            merged = kept + ([tail] if tail else [])
        
        return merged
