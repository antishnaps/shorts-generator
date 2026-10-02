#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Audio processing: text-to-speech and background music handling
V2-RESILIENT-CHUNKS: Улучшенная обработка ошибок при генерации аудио частей
V3-GLOBAL-TTS-LIMIT: Глобальный лимит на параллельные TTS запросы
V4-COST-TRACKING: Отслеживание стоимости TTS
"""

import os
import re
import hashlib
import time
import threading
from collections import OrderedDict
from pathlib import Path
# Совместимость с moviepy 1.x и 2.x
try:
    # moviepy 2.x
    from moviepy import AudioFileClip, AudioClip
except ImportError:
    # moviepy 1.x
    from moviepy.editor import AudioFileClip, AudioClip
from typing import List, Optional
import requests
import random
import shutil
import subprocess
import tempfile
import unicodedata
from contextlib import contextmanager
from core.cost_tracker import get_tracker
from core.gemini_models import TTS_MODEL_CHAIN
from core.process_registry import run_registered


# ============================================================
# 🔧 FFmpeg path setup - использует локальный FFmpeg из tools/ffmpeg/
# ============================================================
def _get_ffmpeg_path() -> str:
    """Возвращает путь к ffmpeg - универсальный для всех ОС"""
    import platform
    
    # Определяем расширение исполняемого файла в зависимости от ОС
    exe_ext = ".exe" if platform.system().lower() == "windows" else ""
    
    # 1. Проверяем локальный FFmpeg в tools/ffmpeg/ (полная сборка)
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_ffmpeg = os.path.join(script_dir, 'tools', 'ffmpeg', f'ffmpeg{exe_ext}')
    if os.path.exists(local_ffmpeg):
        return local_ffmpeg
    
    # 2. Проверяем системный ffmpeg
    if shutil.which('ffmpeg'):
        return 'ffmpeg'
    
    # 3. Пробуем imageio-ffmpeg (урезанная сборка, fallback)
    try:
        import imageio_ffmpeg
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_path and os.path.exists(ffmpeg_path):
            return ffmpeg_path
    except ImportError:
        pass
    except Exception:
        pass
    
    # 4. Fallback - надеемся что ffmpeg в PATH
    return 'ffmpeg'

# Инициализируем путь к ffmpeg при импорте модуля
FFMPEG_PATH = _get_ffmpeg_path()


def _unlink_with_retry(path: str | Path, *, attempts: int = 6, delay: float = 0.05) -> bool:
    """Best-effort removal for short-lived Windows/antivirus file locks."""
    target = Path(path)
    for attempt in range(max(1, int(attempts))):
        try:
            target.unlink(missing_ok=True)
            return True
        except PermissionError:
            if attempt + 1 < attempts:
                time.sleep(max(0.0, delay) * (attempt + 1))
        except OSError:
            return False
    return False


def _get_ffprobe_path() -> str:
    """Resolve ffprobe next to the bundled ffmpeg before consulting PATH."""
    ffmpeg = Path(FFMPEG_PATH)
    probe_name = "ffprobe.exe" if ffmpeg.suffix.lower() == ".exe" else "ffprobe"
    sibling = ffmpeg.with_name(probe_name)
    if sibling.is_file():
        return str(sibling)
    return shutil.which(probe_name) or shutil.which("ffprobe") or "ffprobe"


# 🚦 ГЛОБАЛЬНЫЙ СЕМАФОР ДЛЯ TTS
# Ограничивает общее количество параллельных TTS запросов во всей программе
# Это предотвращает 429 ошибки при генерации множества видео параллельно
_GLOBAL_TTS_SEMAPHORE = threading.Semaphore(1)  # Максимум 1 одновременный TTS запрос (строго последовательно)
_TTS_SEMAPHORE_LOCK = threading.Lock()
_LAST_TTS_REQUEST_TIME = 0.0  # Время последнего TTS запроса
_TTS_MIN_INTERVAL = 5.0  # 🔧 VPN FIX: Увеличен интервал 3.0 → 5.0 сек для стабильности через VPN
_TTS_CACHE_VERSION = 2


class _KeyedLockPool:
    """Bounded-lifetime locks for deterministic concurrent file generation."""

    def __init__(self):
        self._guard = threading.Lock()
        self._entries = {}

    @contextmanager
    def hold(self, key: str):
        with self._guard:
            lock, users = self._entries.get(key, (threading.Lock(), 0))
            self._entries[key] = (lock, users + 1)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
            with self._guard:
                current_lock, current_users = self._entries.get(key, (lock, 1))
                if current_lock is lock and current_users <= 1:
                    self._entries.pop(key, None)
                elif current_lock is lock:
                    self._entries[key] = (lock, current_users - 1)


_TTS_OUTPUT_LOCKS = _KeyedLockPool()


def get_tts_semaphore() -> threading.Semaphore:
    """Get global TTS semaphore for rate limiting"""
    return _GLOBAL_TTS_SEMAPHORE


def _get_system_proxy() -> str:
    """
    Автоматически определяет системный прокси для Gemini API.
    Порядок приоритетов:
      1. Переменные окружения HTTPS_PROXY / HTTP_PROXY / ALL_PROXY
      2. Системный прокси Windows (реестр Internet Settings)
      3. None — прокси не нужен (VPN роутит трафик на уровне ОС)
    """
    # 1. Проверяем переменные окружения (их могут выставить VPN-клиенты)
    for var in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy'):
        val = os.environ.get(var, '').strip()
        if val:
            return val

    # 2. Читаем системный прокси Windows из реестра (IE / WinHTTP настройки)
    try:
        import winreg
        reg_path = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            proxy_enable = winreg.QueryValueEx(key, 'ProxyEnable')[0]
            if proxy_enable:
                proxy_server = winreg.QueryValueEx(key, 'ProxyServer')[0].strip()
                if proxy_server:
                    if '://' not in proxy_server:
                        proxy_server = f'http://{proxy_server}'
                    return proxy_server
    except Exception:
        pass

    return None


class _ProxyContext:
    """
    Context manager: временно применяет системный прокси через env-переменные
    чтобы httpx (используемый google-genai) подхватил его автоматически.
    Восстанавливает оригинальные значения после выхода.
    """
    def __init__(self, proxy_url: str):
        self.proxy_url = proxy_url
        self._saved = {}

    def __enter__(self):
        if self.proxy_url:
            for var in ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY'):
                self._saved[var] = os.environ.get(var)
                os.environ[var] = self.proxy_url
        return self

    def __exit__(self, *_):
        for var, val in self._saved.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val


def enforce_tts_rate_limit():
    """
    Enforce minimum interval between TTS requests.
    Call this BEFORE making a TTS request.
    """
    global _LAST_TTS_REQUEST_TIME
    with _TTS_SEMAPHORE_LOCK:
        now = time.time()
        elapsed = now - _LAST_TTS_REQUEST_TIME
        if elapsed < _TTS_MIN_INTERVAL:
            wait_time = _TTS_MIN_INTERVAL - elapsed
            time.sleep(wait_time)
        _LAST_TTS_REQUEST_TIME = time.time()


def _call_tts_api_limited(callback, enforce_interval: bool = True):
    """Run exactly one remote TTS request without overlapping another one."""
    with get_tts_semaphore():
        if enforce_interval:
            enforce_tts_rate_limit()
            try:
                from core.rate_limiter import get_global_throttler
                get_global_throttler().throttle('gemini_tts')
            except Exception:
                pass
        return callback()


def _create_genai_client(api_key: str, timeout_seconds: float):
    """Create a Gemini client with a real transport-level deadline.

    ``future.result(timeout=...)`` does not stop a running HTTP request and a
    ``ThreadPoolExecutor`` context manager waits for that worker while exiting.
    Configuring the SDK transport is therefore required for the timeout to be
    effective. Retries stay under our explicit key/model retry policy instead
    of multiplying the deadline inside the SDK.
    """
    from google import genai
    from google.genai import types

    try:
        timeout_ms = int(float(timeout_seconds) * 1000)
    except (TypeError, ValueError, OverflowError):
        timeout_ms = 90_000
    timeout_ms = max(1_000, timeout_ms)
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=timeout_ms,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def _is_request_timeout(error: BaseException) -> bool:
    """Recognize timeout errors without depending on a specific HTTP backend."""
    if isinstance(error, (TimeoutError, requests.exceptions.Timeout)):
        return True
    error_type = type(error).__name__.lower()
    error_text = str(error).lower()
    return (
        'timeout' in error_type
        or 'timed out' in error_text
        or 'deadline exceeded' in error_text
    )


class AudioProcessor:
    """Handles text-to-speech and adding background music."""

    _EXPRESSIVE_GEMINI_TTS_PERSONAS = {'horror', 'conspiracy', 'motivator', 'commentator'}

    @staticmethod
    def _resolve_gemini_tts_persona(persona_id: str = None, is_vertical: bool = False) -> str:
        persona = str(persona_id or '').strip().lower()
        known_personas = {
            'serious', 'calm', 'documentary', 'longform', 'horror',
            'conspiracy', 'motivator', 'commentator', 'bro', 'viral',
        }
        if persona in known_personas:
            return persona
        return 'calm' if is_vertical else 'serious'

    @staticmethod
    def _should_use_expressive_gemini_tags(persona_id: str = None) -> bool:
        persona = str(persona_id or '').strip().lower()
        return persona in AudioProcessor._EXPRESSIVE_GEMINI_TTS_PERSONAS

    def __init__(self):
        """Initialize AudioProcessor with model consistency tracking."""
        self.last_successful_model = None
        self.last_successful_key = None
        # A duration auto-fit can synthesize the same script several times.
        # Keep successful stress preprocessing local to the processor so those
        # retries do not spend another Gemini request.
        self._stress_cache = OrderedDict()
        self._stress_cache_lock = threading.Lock()
        self._stress_cache_limit = 32

    @staticmethod
    def _clamp_music_volume(volume, default: float = 0.25) -> float:
        try:
            value = float(volume)
        except (TypeError, ValueError):
            value = default
        if value > 1.0:
            value = value / 100.0
        return max(0.0, min(1.0, value))

    @staticmethod
    def _call_gemini_tts_rest(text: str, model_name: str, voice_name: str,
                              api_key: str, proxy_url: str, timeout: int) -> bytes:
        """
        Делает вызов Gemini TTS напрямую через REST API используя `requests`
        с явной передачей прокси. Это обходит проблему того что httpx внутри
        google-genai SDK может игнорировать env-переменные прокси.

        Returns:
            bytes — сырые PCM/WAV данные аудио
        Raises:
            Exception если сервер вернул ошибку или аудио не найдено
        """
        import base64
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice_name}
                    }
                }
            }
        }
        proxies = {"https": proxy_url, "http": proxy_url} if proxy_url else None
        resp = requests.post(url, json=payload, proxies=proxies, timeout=timeout)
        if resp.status_code != 200:
            # Бросаем исключение в том же формате что google-genai SDK
            raise Exception(f"{resp.status_code} {resp.json().get('error', {}).get('status', '')}. "
                            f"{resp.json()}")
        data = resp.json()
        
        # 💰 COST TRACKING
        try:
            from core.cost_tracker import get_tracker
            usage = data.get('usageMetadata', {})
            input_tokens = usage.get('promptTokenCount', 0)
            output_tokens = usage.get('candidatesTokenCount', 0)
            if input_tokens > 0 or output_tokens > 0:
                get_tracker().add_gemini_tts(
                    input_tokens=input_tokens,
                    audio_tokens=output_tokens,
                    model=model_name,
                )
        except Exception:
            pass

        # Ответ: candidates[0].content.parts[0].inlineData.data (base64)
        try:
            b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
            return base64.b64decode(b64)
        except (KeyError, IndexError) as exc:
            raise ValueError(f"Gemini TTS REST: аудио не найдено в ответе — {exc}")

    @staticmethod
    def _extract_retry_delay(error_str: str, attempt: int = 0) -> float:
        """
        Extract retry delay from API error message.
        Falls back to exponential backoff if not found.
        
        Args:
            error_str: Error message string
            attempt: Current attempt number for exponential backoff
            
        Returns:
            Delay in seconds
        """
        import re
        
        # Try to extract "retry in Xs" or "retryDelay: Xs" from error
        patterns = [
            r'retry in (\d+(?:\.\d+)?)',
            r'retryDelay.*?(\d+(?:\.\d+)?)',
            r'Please retry in (\d+(?:\.\d+)?)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, error_str, re.IGNORECASE)
            if match:
                delay = float(match.group(1))
                # Add buffer (10s minimum, or +50% of suggested delay)
                return max(delay * 1.5, delay + 10.0)
        
        # Fallback: exponential backoff (30s, 60s, 120s, 180s, 240s)
        # Увеличены интервалы для лучшей работы с квотами
        base_delay = 30
        max_delay = 240
        return min(base_delay * (2 ** attempt), max_delay)

    @staticmethod
    def get_optimal_voice_for_language(language: str, content_type: str = 'general') -> str:
        """
        Автоматически выбирает оптимальный голос для языка и типа контента.
        """
        # Карта оптимальных голосов для каждого языка
        voice_map = {
            'Russian': {'general': 'Kore', 'dramatic': 'Charon', 'educational': 'Aoede', 'energetic': 'Fenrir'},
            'English': {'general': 'Puck', 'dramatic': 'Charon', 'educational': 'Kore', 'energetic': 'Fenrir'},
            'French': {'general': 'Aoede', 'dramatic': 'Charon', 'educational': 'Kore', 'energetic': 'Puck'},
            'German': {'general': 'Charon', 'dramatic': 'Charon', 'educational': 'Kore', 'energetic': 'Fenrir'},
            'Spanish': {'general': 'Puck', 'dramatic': 'Charon', 'educational': 'Aoede', 'energetic': 'Fenrir'},
            'Italian': {'general': 'Aoede', 'dramatic': 'Charon', 'educational': 'Kore', 'energetic': 'Puck'},
            'Portuguese': {'general': 'Aoede', 'dramatic': 'Charon', 'educational': 'Kore', 'energetic': 'Fenrir'},
            'Japanese': {'general': 'Kore', 'dramatic': 'Charon', 'educational': 'Aoede', 'energetic': 'Puck'},
            'Chinese': {'general': 'Kore', 'dramatic': 'Charon', 'educational': 'Aoede', 'energetic': 'Fenrir'},
            'Korean': {'general': 'Puck', 'dramatic': 'Charon', 'educational': 'Kore', 'energetic': 'Fenrir'},
            'Arabic': {'general': 'Charon', 'dramatic': 'Charon', 'educational': 'Aoede', 'energetic': 'Fenrir'},
            'Hindi': {'general': 'Aoede', 'dramatic': 'Charon', 'educational': 'Kore', 'energetic': 'Puck'},
            'Turkish': {'general': 'Puck', 'dramatic': 'Charon', 'educational': 'Kore', 'energetic': 'Fenrir'},
            'Polish': {'general': 'Kore', 'dramatic': 'Charon', 'educational': 'Aoede', 'energetic': 'Fenrir'},
            'Dutch': {'general': 'Puck', 'dramatic': 'Charon', 'educational': 'Kore', 'energetic': 'Fenrir'},
            'Swedish': {'general': 'Aoede', 'dramatic': 'Charon', 'educational': 'Kore', 'energetic': 'Puck'}
        }
        
        if language in voice_map:
            voice = voice_map[language].get(content_type, voice_map[language]['general'])
            print(f"🎤 Автовыбор голоса: {language} ({content_type}) → {voice}")
            return voice
        else:
            default_voice = 'Puck'
            print(f"⚠️ Язык '{language}' не найден в карте, используем {default_voice}")
            return default_voice

    @staticmethod
    def build_gemini31_tts_prompt(text: str, language: str = 'Russian', is_vertical: bool = False, voice_name: str = 'Kore', persona_id: str = None) -> str:
        """
        🎙️ GEMINI 3.1 TTS: Максимальное использование возможностей новой модели.
        
        Строит полный Advanced Prompt с:
        - Audio Profile (персона диктора)
        - Scene (контекст и обстановка)
        - Director's Notes (режиссёрские указания)
        - Audio Tags ([excited], [whispers], [short pause] и т.д.)
        
        Gemini 3.1 TTS понимает эти теги нативно и генерирует
        по-настоящему живую, экспрессивную речь.
        """
        import re
        
        # The authored transcript is authoritative.  Do not remove adjacent
        # repeated sentences here: rhetorical repetition and refrains are valid,
        # and deleting them makes the audio diverge from subtitles/visual timing.
        text = unicodedata.normalize('NFC', str(text or '')).strip()
        
        # ====== ШАГ 2: Инъекция Audio Tags для ключевых слов ======
        # Теги применяем к конкретным предложениям/словам, а не ко всему тексту
        
        # Слова-крючки → [excited] в начале предложения
        hook_triggers = {
            'Russian': ['шок', 'секрет', 'правда', 'невероятно', 'внимание', 'невозможно', 'никогда', 'впервые', 'поверить'],
            'English': ['shocking', 'secret', 'truth', 'incredible', 'impossible', 'never', 'first time', 'unbelievable'],
            'German': ['schockierend', 'geheimnis', 'wahrheit', 'unglaublich', 'unmöglich', 'niemals', 'zum ersten mal'],
            'French': ['choquant', 'secret', 'vérité', 'incroyable', 'impossible', 'jamais', 'pour la première fois'],
            'Spanish': ['impactante', 'secreto', 'verdad', 'increíble', 'imposible', 'nunca', 'por primera vez'],
            'Italian': ['sconvolgente', 'segreto', 'verità', 'incredibile', 'impossibile', 'mai', 'per la prima volta'],
            'Portuguese': ['chocante', 'segredo', 'verdade', 'incrível', 'impossível', 'nunca', 'pela primeira vez'],
            'Japanese': ['衝撃', '秘密', '真実', '信じられない', '不可能', '決して', '初めて'],
            'Korean': ['충격', '비밀', '진실', '믿기 어렵', '불가능', '절대', '처음'],
            'Chinese': ['震惊', '秘密', '真相', '难以置信', '不可能', '从未', '第一次'],
            'Hindi': ['चौंकाने', 'रहस्य', 'सच्चाई', 'अविश्वसनीय', 'असंभव', 'कभी नहीं', 'पहली बार'],
            'Arabic': ['صادم', 'سر', 'الحقيقة', 'مذهل', 'مستحيل', 'أبداً', 'لأول مرة'],
        }
        # Слова-паузы → [short pause] перед ними (для драматизма)
        pause_triggers = {
            'Russian': ['но', 'однако', 'вот', 'оказывается', 'а теперь', 'и вот', 'дело в том'],
            'English': ['but', 'however', 'turns out', 'here\'s the thing', 'and now'],
            'German': ['aber', 'jedoch', 'wie sich herausstellt', 'und jetzt'],
            'French': ['mais', 'cependant', 'il s’avère', 'et maintenant'],
            'Spanish': ['pero', 'sin embargo', 'resulta que', 'y ahora'],
            'Italian': ['ma', 'tuttavia', 'si scopre che', 'e ora'],
            'Portuguese': ['mas', 'porém', 'acontece que', 'e agora'],
            'Japanese': ['しかし', 'ところが', '実は', 'そして今'],
            'Korean': ['하지만', '그러나', '알고 보니', '이제'],
            'Chinese': ['但是', '然而', '原来', '现在'],
            'Hindi': ['लेकिन', 'हालाँकि', 'पता चला', 'अब'],
            'Arabic': ['لكن', 'ومع ذلك', 'اتضح أن', 'والآن'],
        }
        # Слова-шёпот → [whispers] для тихих раскрытий
        whisper_triggers = {
            'Russian': ['никто не знает', 'скрывают', 'тайна', 'под секретом', 'между нами'],
            'English': ['nobody knows', 'they hide', 'secret', 'confidential', 'between us'],
            'German': ['niemand weiß', 'sie verbergen', 'geheimnis', 'vertraulich', 'unter uns'],
            'French': ['personne ne sait', 'ils cachent', 'secret', 'confidentiel', 'entre nous'],
            'Spanish': ['nadie sabe', 'lo ocultan', 'secreto', 'confidencial', 'entre nosotros'],
            'Italian': ['nessuno lo sa', 'nascondono', 'segreto', 'confidenziale', 'tra noi'],
            'Portuguese': ['ninguém sabe', 'eles escondem', 'segredo', 'confidencial', 'entre nós'],
            'Japanese': ['誰も知らない', '隠している', '秘密', '内密', 'ここだけの話'],
            'Korean': ['아무도 모른다', '숨기고 있다', '비밀', '기밀', '우리끼리'],
            'Chinese': ['没人知道', '他们隐瞒', '秘密', '机密', '我们之间'],
            'Hindi': ['कोई नहीं जानता', 'वे छिपाते हैं', 'रहस्य', 'गोपनीय', 'हमारे बीच'],
            'Arabic': ['لا أحد يعرف', 'يخفون', 'سر', 'سري', 'بيننا'],
        }
        
        effective_persona_id = AudioProcessor._resolve_gemini_tts_persona(persona_id, is_vertical=is_vertical)
        allow_expressive_tags = AudioProcessor._should_use_expressive_gemini_tags(effective_persona_id)
        lang_hooks = hook_triggers.get(language, ())
        lang_pauses = pause_triggers.get(language, ())
        lang_whispers = whisper_triggers.get(language, ())
        
        # Разбиваем на предложения для обработки
        sentence_parts = [
            part for part in re.split(r'(?<=[.!?。！？؟।॥])\s*', text) if part
        ]
        tagged_parts = []
        
        for part in sentence_parts:
            part_lower = part.lower()
            
            # Проверяем на шёпот (высокий приоритет)
            is_whisper = any(w in part_lower for w in lang_whispers)
            # Проверяем на возбуждение/крючок
            is_excited = any(w in part_lower for w in lang_hooks)
            
            # Добавляем паузу перед коротким словом-связкой внутри предложения
            for trigger in lang_pauses:
                # Добавляем паузу только если это начало предложения или после знака препинания
                part = re.sub(
                    rf'(?<=[,;]\s)({re.escape(trigger)}\b)',
                    r'[short pause] \1',
                    part,
                    flags=re.IGNORECASE,
                    count=1
                )
            
            if is_whisper and allow_expressive_tags:
                tagged_parts.append(f'[whispers] {part}')
            elif is_excited and allow_expressive_tags and not is_vertical:  # В вертикальных видео весь текст и так живой
                tagged_parts.append(f'[excitedly] {part}')
            else:
                tagged_parts.append(part)
        
        tagged_text = ' '.join(tagged_parts)
        
        # ====== ШАГ 3: Строим Advanced Prompt с Audio Profile ======
        
        persona_profiles = {
            'calm': (
                "Alex C.",
                "Calm YouTube Narrator",
                "A clean narration booth. The narrator sounds close, steady, and easy to listen to for a long time.",
                "Style: Calm, clear, modern YouTube narration.\nPace: Medium, with natural pauses and no rushing.\nEmotion: Controlled interest, intensity 3/10, no hype, no shouting, no theatrical trailer voice.\nArticulation: Smooth and precise. Keep emphasis useful, not dramatic."
            ),
            'documentary': (
                "Sam D.",
                "Documentary Narrator",
                "A quiet documentary narration booth. The narrator sounds calm, credible, and focused on facts.",
                "Style: Serious, measured, documentary.\nPace: Medium-slow with natural pauses between facts.\nEmotion: Controlled curiosity, no hype, no shouting, no sales tone.\nArticulation: Precise and clear. Names, numbers, and dates must be easy to understand."
            ),
            'longform': (
                "Mikhail L.",
                "Long-Form Explainer",
                "A relaxed studio for extended viewing. The narrator keeps the listener oriented without forcing emotion.",
                "Style: Long-form explainer, steady and grounded.\nPace: Medium-slow, consistent across chunks.\nEmotion: Mild engagement, no overacting, no sudden bursts.\nArticulation: Stable volume and pronunciation across the whole recording."
            ),
            'serious': (
                "Sam D.",
                "Documentary Educator",
                "A quiet documentary narration booth. The narrator sounds calm, credible, and focused on facts.",
                "Style: Serious, measured, documentary.\nPace: Medium-slow with natural pauses between facts.\nEmotion: Controlled curiosity, no hype, no shouting, no sales tone.\nArticulation: Precise and clear. Numbers, names, and dates must be easy to understand."
            ),
            'horror': (
                "Mara N.",
                "Dark Story Narrator",
                "A dim, tense storytelling room. The narrator speaks close to the microphone with restrained suspense.",
                "Style: Dark, ominous, suspenseful.\nPace: Slow with longer pauses before reveals.\nEmotion: Quiet tension, not screaming.\nArticulation: Soft but clear, with careful emphasis on sensory details."
            ),
            'conspiracy': (
                "Eli R.",
                "Mystery Investigator",
                "A late-night investigative recording. The narrator sounds secretive and controlled.",
                "Style: Mysterious, investigative, confidential.\nPace: Deliberate with short pauses before key reveals.\nEmotion: Suspenseful but believable.\nArticulation: Lower intensity, clear consonants, hint of secrecy."
            ),
            'motivator': (
                "Max K.",
                "Motivational Coach",
                "A high-energy coaching session. The narrator sounds direct, urgent, and confident.",
                "Style: Energetic, commanding, punchy.\nPace: Fast but intelligible.\nEmotion: Strong drive and conviction.\nArticulation: Sharp attacks on action words, powerful endings."
            ),
            'commentator': (
                "Rick C.",
                "Sports Commentator",
                "A live sports commentary booth. The narrator reacts with controlled adrenaline.",
                "Style: Fast, reactive, sports-commentary energy.\nPace: Quick with bursts of excitement.\nEmotion: Competitive and intense.\nArticulation: Crisp, rhythmic, with big emphasis on action moments."
            ),
            'bro': (
                "Leo B.",
                "Friendly Explainer",
                "A casual studio conversation. The narrator sounds relaxed and close to the viewer.",
                "Style: Conversational, friendly, informal.\nPace: Natural and easy-going.\nEmotion: Warm confidence, light humor.\nArticulation: Clear but not formal."
            ),
            'viral': (
                "Alex V.",
                "Controlled Shorts Narrator",
                "A sleek recording studio. The narrator is direct and focused without sounding theatrical.",
                "Style: Compact, confident, and clean. No filler words.\nPace: Medium-fast, but comfortable to listen to.\nEmotion: Mild urgency, intensity 4/10, no shouting, no fake shock.\nArticulation: Clear consonants, light emphasis on concrete actions and stakes."
            ),
        }

        if effective_persona_id in persona_profiles:
            profile_name, profile_role, scene, directors_notes = persona_profiles[effective_persona_id]
        else:
            profile_name, profile_role, scene, directors_notes = persona_profiles['calm' if is_vertical else 'serious']
        
        # Финальный Advanced Prompt для Gemini 3.1 TTS
        advanced_prompt = (
            f"# AUDIO PROFILE: {profile_name} — \"{profile_role}\"\n\n"
            f"## THE SCENE\n{scene}\n\n"
            f"## DIRECTOR'S NOTES\n{directors_notes}\n\n"
            "## DELIVERY GUARDRAILS\n"
            f"Speak the transcript exactly in {language}. Do not translate, transliterate, "
            "summarize, or replace any words. Preserve the original writing system.\n"
            "Keep the voice human and restrained. Avoid overacting, fake suspense, yelling, breathy drama, "
            "and sudden emotional jumps unless the selected persona explicitly requires it. "
            "For long scripts, keep pace, volume, and emotional intensity consistent across chunks.\n\n"
            f"## TRANSCRIPT\n{tagged_text}"
        )
        
        return advanced_prompt

    @staticmethod
    def _prepare_edge_text(text: str, language: str, log_callback=None) -> str:
        """Normalize text for clear Edge speech without fake emotional punctuation."""
        original = str(text or '')
        text = unicodedata.normalize('NFC', original)
        text = text.replace('\u00a0', ' ')

        if str(language or '').lower() == 'russian':
            # Common production abbreviations and units that Edge otherwise reads
            # inconsistently in a Russian sentence.
            replacements = (
                (r'\bДСП\b', 'дэ эс пэ'),
                (r'\bМДФ\b', 'эм дэ эф'),
                (r'\bЛДСП\b', 'эл дэ эс пэ'),
                (r'\bЧПУ\b', 'че пэ у'),
                (r'\bПВХ\b', 'пэ вэ ха'),
                (r'\bEVA\b', 'ЭВА'),
                (r'\bPUR\b', 'ПУР'),
            )
            for pattern, replacement in replacements:
                text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

            unit_replacements = (
                (r'(\d+(?:[.,]\d+)?)\s*мм\b', r'\1 миллиметров'),
                (r'(\d+(?:[.,]\d+)?)\s*см\b', r'\1 сантиметров'),
                (r'(\d+(?:[.,]\d+)?)\s*мкм\b', r'\1 микрометров'),
                (r'(\d+(?:[.,]\d+)?)\s*кг\b', r'\1 килограммов'),
                (r'(\d+(?:[.,]\d+)?)\s*°\s*C\b', r'\1 градусов Цельсия'),
                (r'(\d+(?:[.,]\d+)?)\s*%', r'\1 процентов'),
            )
            for pattern, replacement in unit_replacements:
                text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
            text = re.sub(r'№\s*(\d+)', r'номер \1', text)

        # Edge voices already infer intonation from ordinary punctuation. Repeated
        # viral markers create robotic pauses, so keep a clean narration form.
        text = re.sub(r'\?\s*!+|!+\s*\?', '?', text)
        text = re.sub(r'!{2,}', '!', text)
        text = re.sub(r'\.{3,}', ', ', text)
        text = re.sub(r'\s*[—–]\s*', ', ', text)
        text = re.sub(r'\s*;\s*', '; ', text)

        # Give very long clauses a real sentence boundary at natural connectors.
        sentences = re.split(r'(?<=[.!?])\s+', text)
        shaped = []
        for sentence in sentences:
            if len(sentence.split()) > 30:
                sentence = re.sub(
                    r',\s+(?=(?:но|однако|поэтому|при этом|из-за этого|в результате)\b)',
                    '. ',
                    sentence,
                    count=1,
                    flags=re.IGNORECASE,
                )
            shaped.append(sentence)
        text = ' '.join(shaped)

        text = re.sub(r'\s+([.!?,;:])', r'\1', text)
        text = re.sub(r'([.!?])(?=[А-ЯA-ZЁ])', r'\1 ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        terminal_marks = '.!?。！？؟।॥…'
        if text and text[-1] not in terminal_marks:
            if str(language or '').lower() in {'japanese', 'chinese'}:
                text += '。'
            elif str(language or '').lower() == 'hindi':
                text += '।'
            else:
                text += '.'

        if log_callback and text != original:
            log_callback("   🗣️ Текст адаптирован для естественной Edge-озвучки")
        return text

    @staticmethod
    def add_emotional_markers(text: str, language: str = 'Russian', is_vertical: bool = False) -> str:
        """Добавляет экспрессию в текст для TTS (legacy-метод для 2.5 моделей).
        
        Для Gemini 3.1 используй build_gemini31_tts_prompt() — он работает с нативными Audio Tags.
        """
        import re
        
        # Intentional repetition is part of the authored transcript (hooks,
        # refrains, quotations). Never delete repeated sentences or phrases in
        # an audio-only preprocessing step: doing so desynchronizes narration,
        # subtitles and the visual plan.
        text = unicodedata.normalize('NFC', str(text or '')).strip()
        
        # Усиленная экспрессия
        caps_words = {
            'Russian': ['шок', 'секрет', 'правда', 'внимание', 'важно', 'срочно', 'никогда', 'всегда', 'только', 'впервые', 'невероятно'],
            'English': ['shock', 'secret', 'truth', 'attention', 'important', 'urgent', 'never', 'always', 'only', 'first', 'incredible']
        }
        words = caps_words.get(language, caps_words['English'])
        for word in words:
            pattern = rf'\b({re.escape(word)})\b'  # Экранируем спецсимволы
            text = re.sub(pattern, lambda m: m.group(1).upper(), text, count=1, flags=re.IGNORECASE)
            
        power_words = {
            'Russian': ['невероятно', 'шокирующ', 'удивительн', 'потрясающ', 'узнай', 'смотри', 'представь', 'думаешь', 'знаешь', 'поверишь', 'скрывают', 'тайна', 'откры', 'разоблач', 'правда', 'ложь', 'обман', 'скандал'],
            'English': ['incredible', 'shocking', 'amazing', 'stunning', 'discover', 'look', 'imagine', 'think', 'know', 'believe', 'hidden', 'secret', 'reveal', 'expose', 'truth', 'lie', 'scam', 'scandal']
        }
        words = power_words.get(language, power_words['English'])
        for word in words:
            pattern = rf'([^.!?]*{re.escape(word)}[^.!?]*)\.'  # Экранируем спецсимволы
            text = re.sub(pattern, r'\1!', text, flags=re.IGNORECASE)
            
        sentences = [s.strip() for s in re.split(r'[.!?]', text) if s.strip()]
        if sentences:
            first = sentences[0]
            if not any(first.endswith(p) for p in ['!', '?']):
                text = text.replace(first + '.', first + '!', 1)
                
        dramatic_words = {
            'Russian': ['но', 'однако', 'вот', 'это', 'здесь', 'сейчас', 'потому что', 'дело в том', 'оказывается', 'представь', 'а теперь', 'и вот'],
            'English': ['but', 'however', 'here', 'this', 'now', 'because', 'the thing is', 'turns out', 'imagine', 'and now', 'and here']
        }
        words = dramatic_words.get(language, dramatic_words['English'])
        for word in words:
            pattern = rf'([^.…]) ({re.escape(word)}\b)'  # Экранируем спецсимволы
            text = re.sub(pattern, r'\1... \2', text, flags=re.IGNORECASE, count=3)
            
        if is_vertical:
            text = re.sub(r'\?(?!\!)', '?!', text)
            sentences = re.split(r'([.!?]+)', text)
            result = []
            exclamation_counter = 0
            for i, part in enumerate(sentences):
                if part.strip() and not re.match(r'^[.!?]+$', part):
                    exclamation_counter += 1
                    if result and result[-1] not in [' ', '.', '!', '?']: result.append(' ')
                    result.append(part)
                    if i + 1 < len(sentences) and sentences[i + 1] == '.':
                        if exclamation_counter % 2 == 0: result.append('!')
                        else: result.append(sentences[i + 1])
                    elif i + 1 < len(sentences):
                        result.append(sentences[i + 1])
                elif part.strip() and re.match(r'^[.!?]+$', part):
                    if not result or result[-1] not in ['.', '!', '?']: result.append(part)
            text = ''.join(result)
            
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\.{4,}', '...', text)
        text = re.sub(r'!{3,}', '!!', text)
        text = re.sub(r'\s+([.!?,;:])', r'\1', text)
        return text.strip()

    @staticmethod
    def _apply_pronunciation_fixes(text: str, log_callback) -> str:
        """Применяет словарь произношений из config.json к тексту перед TTS.
        
        Загружает pronunciation_fixes из user_settings и заменяет слова/фразы
        для корректного ударения и произношения нейросетью.
        """
        try:
            from core.config_manager import get_config_manager
            config = get_config_manager()
            fixes = config.get_user_setting('pronunciation_fixes', {})
            
            if not fixes:
                return text
            
            count = 0
            # Long phrases first; whole-word, case-insensitive matching avoids
            # replacing a short key inside another word.
            ordered_fixes = sorted(fixes.items(), key=lambda item: len(str(item[0])), reverse=True)
            for original, replacement in ordered_fixes:
                original = str(original or '').strip()
                replacement = str(replacement or '').strip()
                if not original or not replacement:
                    continue
                left = r'(?<!\w)' if original[0].isalnum() else ''
                right = r'(?!\w)' if original[-1].isalnum() else ''
                pattern = f"{left}{re.escape(original)}{right}"
                text, replacements = re.subn(
                    pattern,
                    lambda _match, value=replacement: value,
                    text,
                    flags=re.IGNORECASE,
                )
                count += replacements
            
            if count > 0:
                log_callback(f"   🗣️ Применено {count} фиксов произношения")
        except Exception:
            # Не ломаем TTS из-за ошибки загрузки конфига
            pass
        
        return text

    @staticmethod
    def _timestamp_sidecar_path(audio_path: str) -> Path:
        return Path(audio_path).with_suffix('.timestamps.json')

    @staticmethod
    def _load_tts_word_timestamps(audio_path: str, original_text: str = "") -> Optional[List[dict]]:
        """Load provider-native word boundaries stored beside a TTS file."""
        sidecar = AudioProcessor._timestamp_sidecar_path(audio_path)
        if not sidecar.is_file():
            return None
        try:
            import json

            payload = json.loads(sidecar.read_text(encoding='utf-8'))
            timestamps = payload.get('timestamps') if isinstance(payload, dict) else payload
            if not isinstance(timestamps, list):
                return None
            from core.subtitle_sync import SubtitleSynchronizer

            normalized = [
                item for item in (
                    SubtitleSynchronizer._normalize_word_timestamp(raw)
                    for raw in timestamps
                )
                if item
            ]
            if not normalized:
                return None
            if original_text:
                return SubtitleSynchronizer.align_word_timestamps_to_text(
                    normalized, original_text
                ) or None
            return normalized
        except (OSError, ValueError, TypeError):
            return None

    def get_word_timestamps(self, audio_path: str, api_key: str, language: str, log_callback, original_text: str = "") -> Optional[List[dict]]:
        """Получить word-level timestamps используя локальный Vosk (лучшее качество) или Gemini STT."""
        try:
            # Edge TTS exposes native WordBoundary metadata during synthesis.
            # It is more precise, free, and language-correct than transcribing
            # the generated audio again.
            native_timestamps = self._load_tts_word_timestamps(audio_path, original_text)
            if native_timestamps:
                log_callback(
                    f"🎯 Используем точные границы TTS-провайдера: {len(native_timestamps)}"
                )
                return native_timestamps

            # 🚀 TIER 1: Локальный Vosk STT (Бесплатно, быстро, точно)
            try:
                from core.vosk_stt import get_vosk_word_timestamps
                log_callback("🎙️ Попытка получить timestamps через локальный Vosk...")
                vosk_timestamps = get_vosk_word_timestamps(audio_path, language, log_callback, original_text)
                if vosk_timestamps and len(vosk_timestamps) > 0:
                    return vosk_timestamps
            except Exception as e:
                log_callback(f"⚠️ Ошибка вызова Vosk: {e}. Пробуем Gemini...")

            # 🚀 TIER 2: Gemini Speech-to-Text (Fallback)
            audio_duration = self._get_audio_duration(audio_path)
            if audio_duration > 360:
                if not self._is_valid_ascii_api_key(api_key):
                    log_callback("   ℹ️ Нет валидного API-ключа для многочастного STT; используем локальную синхронизацию")
                    return None
                return self._get_chunked_word_timestamps(
                    audio_path=audio_path,
                    api_key=api_key,
                    language=language,
                    log_callback=log_callback,
                    original_text=original_text,
                    audio_duration=audio_duration,
                )

            log_callback("🎙️ Получение timestamps через Gemini Speech-to-Text...")
            from google import genai
            from google.genai import types
            
            with open(audio_path, 'rb') as f:
                audio_data = f.read()

            audio_mime_types = {
                '.mp3': 'audio/mpeg',
                '.wav': 'audio/wav',
                '.m4a': 'audio/mp4',
                '.mp4': 'audio/mp4',
                '.ogg': 'audio/ogg',
                '.opus': 'audio/ogg',
                '.flac': 'audio/flac',
            }
            audio_mime_type = audio_mime_types.get(
                Path(audio_path).suffix.lower(),
                'audio/mpeg',
            )
            transcript_hint = str(original_text or '').strip()[:12000]
            transcription_prompt = (
                f"Transcribe the audio in {language}. Do not translate it. "
                "Return JSON only as an array of objects with word, start, and end. "
                "Use word-level or natural writing-unit timestamps for the language."
            )
            if transcript_hint:
                transcription_prompt += (
                    " Align to this exact original transcript and preserve its writing: "
                    + transcript_hint
                )
            
            timeout = max(45, min(180, int(max(1.0, audio_duration) * 0.3) + 30))
            client = _create_genai_client(api_key, timeout)
            
            def _get_timestamps():
                return client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[types.Content(parts=[
                        types.Part.from_bytes(data=audio_data, mime_type=audio_mime_type),
                        types.Part(text=transcription_prompt)
                    ])]
                )
            
            try:
                response = _get_timestamps()
                log_callback("   ✅ Ответ получен")
            except Exception as e:
                if _is_request_timeout(e):
                    log_callback(
                        f"   ⚠️ Timeout (>{timeout}s) - "
                        "используем равномерное распределение"
                    )
                else:
                    log_callback(f"   ⚠️ Ошибка запроса: {str(e)[:100]}")
                return None
            
            if response and response.text:
                # 💰 COST TRACKING
                try:
                    from core.cost_tracker import get_tracker
                    tracker = get_tracker()
                    if hasattr(response, 'usage_metadata') and response.usage_metadata:
                        input_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
                        output_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
                        if input_tokens > 0 or output_tokens > 0:
                            tracker.add_text_generation(input_tokens, output_tokens, model='gemini-2.5-flash')
                except Exception:
                    pass

                import json
                import re
                # Пробуем найти JSON в ответе (может быть в markdown блоке)
                json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                if json_match:
                    try:
                        timestamps = json.loads(json_match.group(0))
                        if original_text:
                            from core.subtitle_sync import SubtitleSynchronizer
                            timestamps = SubtitleSynchronizer.align_word_timestamps_to_text(
                                timestamps, original_text
                            )
                        log_callback(f"   ✅ Получено {len(timestamps)} word timestamps (Gemini STT)")
                        return timestamps
                    except json.JSONDecodeError as e:
                        log_callback(f"   ⚠️ Невалидный JSON от Gemini: {str(e)[:50]}")
                        log_callback("   💡 Используем равномерное распределение timestamps")
                        return None
                else:
                    log_callback("   ⚠️ JSON не найден в ответе Gemini")
                    log_callback("   💡 Используем равномерное распределение timestamps")
                    return None
            return None
        except Exception as e:
            log_callback(f"   ⚠️ Ошибка получения timestamps: {e}")
            log_callback("   💡 Fallback: равномерное распределение timestamps")
            return None

    def _get_chunked_word_timestamps(
        self,
        audio_path: str,
        api_key: str,
        language: str,
        log_callback,
        original_text: str = "",
        audio_duration: float = 0.0,
        chunk_seconds: int = 300,
    ) -> Optional[List[dict]]:
        """Transcribe long audio in bounded pieces and stitch absolute timestamps."""
        from core.subtitle_text import join_subtitle_units, split_subtitle_units

        log_callback(
            f"🧩 Длинная дорожка {audio_duration / 60:.1f} мин: "
            f"STT по чанкам до {chunk_seconds // 60} мин"
        )
        with tempfile.TemporaryDirectory(prefix="shorts_stt_") as temp_dir:
            pattern = str(Path(temp_dir) / "chunk_%04d.mp3")
            command = [
                FFMPEG_PATH,
                '-y',
                '-i', str(audio_path),
                '-vn',
                '-f', 'segment',
                '-segment_time', str(chunk_seconds),
                '-reset_timestamps', '1',
                '-ac', '1',
                '-ar', '16000',
                '-c:a', 'libmp3lame',
                '-b:a', '64k',
                pattern,
            ]
            timeout = max(300, min(1800, int(max(audio_duration, 1.0) * 0.2) + 120))
            result = run_registered(
                command,
                label="ffmpeg_stt_audio_chunks",
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=timeout,
            )
            chunk_paths = sorted(Path(temp_dir).glob("chunk_*.mp3"))
            if result.returncode != 0 or not chunk_paths:
                log_callback(f"   ⚠️ Не удалось разбить аудио для STT: {result.stderr[-300:]}")
                return None

            units, separator = split_subtitle_units(original_text)
            combined = []
            offset = 0.0
            for index, chunk_path in enumerate(chunk_paths):
                start_unit = round(index * len(units) / len(chunk_paths)) if units else 0
                end_unit = round((index + 1) * len(units) / len(chunk_paths)) if units else 0
                text_hint = join_subtitle_units(units[start_unit:end_unit], separator) if units else ""
                timestamps = self.get_word_timestamps(
                    str(chunk_path),
                    api_key,
                    language,
                    log_callback,
                    text_hint,
                )
                if not timestamps:
                    log_callback(
                        f"   ⚠️ STT-чанк {index + 1}/{len(chunk_paths)} не синхронизирован; "
                        "переходим к безопасному равномерному fallback"
                    )
                    return None

                for item in timestamps:
                    adjusted = dict(item)
                    adjusted['start'] = float(item.get('start', item.get('start_time', 0))) + offset
                    adjusted['end'] = float(item.get('end', item.get('end_time', 0))) + offset
                    combined.append(adjusted)
                offset += self._get_audio_duration(str(chunk_path))
                log_callback(f"   ✅ STT-чанк {index + 1}/{len(chunk_paths)}")

            return combined or None

    @staticmethod
    def _remove_audio_metadata(audio_path: str) -> None:
        """Remove all metadata from audio file using ffmpeg."""
        temp_path = None
        try:
            source_path = Path(audio_path)
            temp_path = str(
                source_path.with_name(f"{source_path.stem}.temp{source_path.suffix}")
            )
            cmd = [
                FFMPEG_PATH, '-i', audio_path,
                '-map_metadata', '-1',
                '-codec', 'copy', '-y', temp_path
            ]
            run_registered(
                cmd, 
                label="ffmpeg_audio_metadata_strip",
                check=True, 
                capture_output=True,
                encoding='utf-8',
                errors='ignore',
                timeout=30
            )
            
            # Проверяем что temp файл создан и не пустой
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                shutil.move(temp_path, audio_path)
            else:
                print("⚠️ Temp file not created or empty, keeping original")
                
        except subprocess.TimeoutExpired:
            print("⚠️ Timeout removing metadata, keeping original")
        except Exception as e:
            print(f"⚠️ Failed to remove metadata: {e}")
        finally:
            # Cleanup temp file if it exists
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception as cleanup_error:
                    print(f"⚠️ Failed to cleanup temp file: {cleanup_error}")

    def _is_valid_ascii_api_key(self, api_key: str) -> bool:
        """Check if API key is valid ASCII and has reasonable length."""
        if not api_key or len(api_key) < 20: return False
        try:
            api_key.encode('ascii')
            return ' ' not in api_key
        except UnicodeEncodeError:
            return False

    def _extract_voice_name(self, voice_name: str) -> str:
        """Extract pure voice name from format like 'Voice (Description)'."""
        if not voice_name: return 'Kore'
        if '(' in voice_name: return voice_name.split('(', maxsplit=1)[0].strip()
        return voice_name.strip()

    def _split_text_into_chunks(self, text: str, max_chars: int, language: str) -> List[str]:
        """Split text into chunks respecting sentence boundaries."""
        from core.text_generator import split_text_by_sentences
        from core.subtitle_text import split_graphemes

        max_chars = max(1, int(max_chars))

        def split_oversized(value: str) -> List[str]:
            """Bound a sentence without breaking Unicode grapheme sequences."""
            pieces = []
            current = ""
            # Keep natural word boundaries where they exist.  A CJK sentence or
            # a long URL can be one token, so those tokens are split by visible
            # grapheme-like units as a last resort.
            for token in re.findall(r'\S+(?:\s+|$)', str(value or '')):
                if len(current) + len(token) <= max_chars:
                    current += token
                    continue
                if current.strip():
                    pieces.append(current.strip())
                    current = ""

                stripped_token = token.strip()
                if len(stripped_token) <= max_chars:
                    current = stripped_token
                    continue

                for unit in split_graphemes(stripped_token, preserve_whitespace=False):
                    if current and len(current) + len(unit) > max_chars:
                        pieces.append(current)
                        current = ""
                    current += unit
            if current.strip():
                pieces.append(current.strip())
            return pieces

        sentences = split_text_by_sentences(text, language)
        chunks = []
        current_chunk = ""
        for sentence in sentences:
            for piece in split_oversized(sentence):
                separator = "" if not current_chunk else " "
                if len(current_chunk) + len(separator) + len(piece) <= max_chars:
                    current_chunk += separator + piece
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = piece
        if current_chunk:
            chunks.append(current_chunk.strip())
        return chunks

    def text_to_speech(self, text: str, output_dir: Path, log_callback, provider: str = 'gemini', voice_name: str = None, api_key: str = None, speech_speed: float = 1.0, language: str = 'Russian', is_vertical: bool = False, persona_id: str = None, edge_pitch_hz: int = 0, edge_volume_percent: int = 0):
        """Converts text to speech using Gemini TTS with automatic retry on quota exceeded."""
        
        
        if voice_name is None:
            voice_name = self.get_optimal_voice_for_language(language, content_type='general')
            log_callback(f"🎤 Автоматически выбран голос: {voice_name} для языка {language}")
        
        log_callback("=" * 60)
        log_callback("🎤 НАЧАЛО ГЕНЕРАЦИИ ОЗВУЧКИ")
        log_callback(f"   • Язык: {language}, Провайдер: {provider}, Голос: {voice_name}")
        
        if not text.strip():
            log_callback("⚠️ Текст для озвучки пуст. Пропускаем.")
            return None, 0
        
        provider = str(provider or 'gemini').lower()
        if provider not in {'edge', 'gemini'}:
            log_callback(f"⚠️ Провайдер TTS '{provider}' больше не поддерживается, используем Edge TTS.")
            provider = 'edge'

        # Сначала пользовательский словарь: он имеет приоритет над авто-нормализацией.
        text = self._apply_pronunciation_fixes(text, log_callback)

        if provider == 'edge':
            text = self._prepare_edge_text(text, language, log_callback)
        elif provider == 'gemini':
            if self._should_use_expressive_gemini_tags(persona_id):
                text = self.add_emotional_markers(text, language, is_vertical=is_vertical)
            else:
                before_normalize = text
                text = re.sub(r'!{2,}', '!', text)
                text = re.sub(r'\.{3,}', '.', text)
                text = self._prepare_edge_text(text, language, None)
                if log_callback and text != before_normalize:
                    log_callback("   TTS text normalized for calm Gemini delivery")
        # Контекстный редактор работает один раз на полном подготовленном тексте,
        # до TTS-чанкинга. Это даёт Gemini контекст для омографов и одинаково
        # передаёт Unicode-ударения в Gemini, Edge и локальный Windows fallback.
        text = self._prepare_contextual_stress(
            text=text,
            api_key=api_key,
            language=language,
            provider=provider,
            log_callback=log_callback,
        )
        
        # �🔧 УМЕНЬШЕН ЛИМИТ: Gemini TTS глючит на длинных текстах (ускоряет конец)
        # Разбиваем на чанки по ~1 минуте (~150 слов × ~6 символов = ~900 символов)
        # Используем 1000 для запаса на границы предложений
        # Edge сохраняет контекст на более длинном фрагменте лучше, чем при частой
        # склейке коротких MP3. Gemini оставляем на проверенном меньшем лимите.
        MAX_CHUNK_SIZE = 3000 if provider == 'edge' else 1000
        if len(text) > MAX_CHUNK_SIZE:
            log_callback(f"📚 Длинный текст ({len(text)} символов) - разбиваем на части...")
            chunks = self._split_text_into_chunks(text, MAX_CHUNK_SIZE, language)
            
            # 🔧 ОТКЛЮЧЕНА ПАРАЛЛЕЛЬНАЯ ГЕНЕРАЦИЯ - всегда последовательно
            # Причина: 429 rate limit при параллельной генерации нескольких видео
            log_callback(f"📝 Последовательная генерация {len(chunks)} чанков")
            chunk_files = []
            total_duration = 0.0
            
            for i, chunk in enumerate(chunks, 1):
                MAX_CHUNK_RETRIES = 5
                chunk_path = None
                chunk_duration = 0
                
                # Validate against language-aware narration units. The old 40%
                # character heuristic accepted severely truncated chunks and was
                # especially unreliable for Korean/CJK scripts.
                from core.text_generator import count_words
                narration_units = max(1, count_words(chunk, language))
                expected_duration = narration_units / 2.5  # 150 units/minute
                min_expected_duration = max(1.0, expected_duration * 0.65)
                
                for retry in range(MAX_CHUNK_RETRIES):
                    chunk_path, chunk_duration = self._generate_single_audio(
                        text=chunk, output_dir=output_dir, is_vertical=is_vertical,
                        log_callback=log_callback, provider=provider, voice_name=voice_name,
                        api_key=api_key, speech_speed=speech_speed, language=language,
                        persona_id=persona_id,
                        edge_pitch_hz=edge_pitch_hz,
                        edge_volume_percent=edge_volume_percent,
                    )
                    
                    # 🔧 FIX: Валидация длительности чанка
                    if chunk_path and chunk_duration > 0:
                        if chunk_duration < min_expected_duration:
                            log_callback(f"⚠️ Чанк {i}: слишком короткий ({chunk_duration:.1f}s < {min_expected_duration:.1f}s мин.), retry...")
                            # Удаляем битый файл
                            try:
                                if os.path.exists(chunk_path):
                                    os.remove(chunk_path)
                            except Exception:
                                pass
                            chunk_path = None
                            chunk_duration = 0
                        else:
                            break  # Чанк валидный - выходим из retry цикла
                    
                    if retry < MAX_CHUNK_RETRIES - 1:
                        # Экспоненциальный backoff: 10s, 20s, 40s, 60s
                        wait_time = min(10 * (2 ** retry), 60)
                        log_callback(f"⏳ Чанк {i}: ожидание {wait_time}s перед повтором...")
                        time.sleep(wait_time)
                
                if not chunk_path:
                    log_callback(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось создать часть {i}")
                    return None, 0
                chunk_files.append(chunk_path)
                total_duration += chunk_duration
                log_callback(f"   ✅ Чанк {i}/{len(chunks)}: готово ({chunk_duration:.1f}s, ожид. мин. {min_expected_duration:.1f}s)")

            
            combined_path = self._combine_audio_files(chunk_files, output_dir, log_callback)
            if not combined_path: return None, 0
            combined_duration = self._get_audio_duration(combined_path)
            return combined_path, combined_duration or total_duration
        
        return self._generate_single_audio(
            text, output_dir, log_callback, provider, voice_name, api_key,
            speech_speed, language, is_vertical=is_vertical, persona_id=persona_id,
            edge_pitch_hz=edge_pitch_hz,
            edge_volume_percent=edge_volume_percent,
        )

    def _generate_single_audio(self, text: str, output_dir: Path, log_callback, provider: str = 'gemini', voice_name: str = None, api_key: str = None, speech_speed: float = 1.0, language: str = 'Russian', is_vertical: bool = False, persona_id: str = None, edge_pitch_hz: int = 0, edge_volume_percent: int = 0):
        """
        Generate audio for a single text chunk.
        
        🔧 FIX: Убран внутренний retry - теперь retry делается ТОЛЬКО во внешнем цикле
        (generate_chunk_with_retry), чтобы не держать семафор во время sleep.
        """
        if not text.strip(): return None, 0
        try:
            provider = str(provider or 'gemini').lower()
            if provider not in {'edge', 'gemini'}:
                log_callback(f"⚠️ Провайдер TTS '{provider}' больше не поддерживается, используем Edge TTS.")
                provider = 'edge'

            fingerprint = hashlib.sha256(
                repr((
                    _TTS_CACHE_VERSION,
                    text,
                    provider,
                    voice_name or '',
                    round(float(speech_speed or 1.0), 4),
                    language,
                    bool(is_vertical),
                    persona_id or '',
                    int(edge_pitch_hz or 0),
                    int(edge_volume_percent or 0),
                )).encode('utf-8')
            ).hexdigest()[:24]
            filepath = output_dir / f"voiceover_{fingerprint}.mp3"
            # Identical scripts can arrive from several batch workers at once.
            # Lock the deterministic cache path and re-check it inside the lock
            # so only one worker pays for or writes a particular TTS chunk.
            with _TTS_OUTPUT_LOCKS.hold(str(filepath.resolve())):
                cached_candidates = (
                    [filepath.with_suffix('.wav'), filepath]
                    if provider == 'edge'
                    else [filepath]
                )
                for cached_path in cached_candidates:
                    if cached_path.is_file() and cached_path.stat().st_size > 1000:
                        cached_duration = self._get_audio_duration(str(cached_path))
                        if cached_duration > 0:
                            log_callback(f"   ♻️ TTS-чанк уже готов, используем кэш ({cached_duration:.1f}s)")
                            return str(cached_path), cached_duration

                if provider == 'edge':
                    return self._try_edge_tts(
                        text, str(filepath), voice_name, language, speech_speed, log_callback,
                        edge_pitch_hz=edge_pitch_hz,
                        edge_volume_percent=edge_volume_percent,
                    )

                if not self._is_valid_ascii_api_key(api_key):
                    log_callback("❌ Невалидный Gemini API ключ")
                    return None, 0

                # Один вызов без retry: повторы делает внешний цикл чанков.
                try:
                    return self._try_gemini_tts(
                        text=text, output_path=str(filepath), api_key=api_key,
                        voice_name=voice_name or 'Kore', log_callback=log_callback,
                        speech_speed=speech_speed, language=language, is_vertical=is_vertical,
                        persona_id=persona_id
                    )
                except PermissionError as perm_err:
                    log_callback(f"❌ Ошибка авторизации: {perm_err}")
                    raise
                except (requests.exceptions.RequestException, ConnectionError) as net_err:
                    error_str = str(net_err)
                    log_callback(f"❌ Сетевая ошибка TTS: {error_str[:200]}")
                    return None, 0
                except ValueError as val_err:
                    log_callback(f"❌ Ошибка валидации: {val_err}")
                    raise
                except Exception as e:
                    error_str = str(e)
                    if '403' in error_str or '401' in error_str:
                        raise PermissionError(f"Ошибка авторизации: {error_str}")
                    log_callback(f"❌ Ошибка TTS: {error_str[:200]}")
                    return None, 0
            
        except PermissionError:
            # Пробрасываем ошибки авторизации
            raise
        except Exception as e:
            # Критические ошибки
            log_callback(f"❌ Критическая ошибка TTS: {e}")
            return None, 0

    @staticmethod
    def _master_edge_audio(raw_path: str, output_path: str, log_callback) -> bool:
        """Decode Edge's low-bitrate MP3 once and keep a mastered PCM intermediate."""
        filters = (
            'highpass=f=70,'
            'acompressor=threshold=0.14:ratio=2:attack=8:release=100:makeup=1.15,'
            'loudnorm=I=-16:TP=-1.5:LRA=8'
        )
        try:
            run_registered(
                [
                    FFMPEG_PATH, '-y', '-i', raw_path,
                    '-af', filters,
                    '-ac', '1', '-ar', '24000',
                    '-c:a', 'pcm_s16le', output_path,
                ],
                label="ffmpeg_edge_audio_master",
                check=True,
                capture_output=True,
                timeout=120,
            )
            if Path(output_path).is_file() and Path(output_path).stat().st_size > 1000:
                log_callback("   🎚️ Edge-голос выровнен по громкости и разборчивости")
                return True
        except Exception as exc:
            log_callback(f"   ⚠️ Мастеринг Edge пропущен: {str(exc)[:120]}")
        Path(output_path).unlink(missing_ok=True)
        return False

    @staticmethod
    def _persist_edge_word_boundaries(metadata_path: str, audio_path: str) -> int:
        """Convert Edge JSONL tick offsets into a small atomic seconds sidecar."""
        metadata_file = Path(metadata_path)
        if not metadata_file.is_file():
            return 0
        try:
            import json

            timestamps = []
            for line in metadata_file.read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get('type') != 'WordBoundary':
                    continue
                start = float(item.get('offset', 0.0)) / 10_000_000
                duration = float(item.get('duration', 0.0)) / 10_000_000
                word = str(item.get('text') or '').strip()
                if word and duration > 0:
                    timestamps.append({
                        'word': word,
                        'start': start,
                        'end': start + duration,
                    })
            if not timestamps:
                return 0

            sidecar = AudioProcessor._timestamp_sidecar_path(audio_path)
            temporary = sidecar.with_suffix(sidecar.suffix + '.tmp')
            temporary.write_text(
                json.dumps(
                    {'version': 1, 'provider': 'edge', 'timestamps': timestamps},
                    ensure_ascii=False,
                    separators=(',', ':'),
                ),
                encoding='utf-8',
            )
            os.replace(temporary, sidecar)
            return len(timestamps)
        except (OSError, ValueError, TypeError):
            return 0

    def _try_edge_tts(self, text: str, output_path: str, voice_name: str, language: str, speech_speed: float, log_callback, edge_pitch_hz: int = 0, edge_volume_percent: int = 0) -> tuple:
        """Generate natural free speech using Microsoft Edge voices."""
        raw_output_path = str(Path(output_path).with_suffix('.edge_raw.mp3'))
        mastered_output_path = str(Path(output_path).with_suffix('.wav'))
        metadata_output_path = str(Path(output_path).with_suffix('.edge_metadata.jsonl'))
        timestamp_sidecar = self._timestamp_sidecar_path(output_path)
        try:
            import asyncio
            import inspect
            import edge_tts

            default_voices = {
                'russian': 'ru-RU-SvetlanaNeural',
                'english': 'en-US-AriaNeural',
                'german': 'de-DE-KatjaNeural',
                'spanish': 'es-ES-ElviraNeural',
                'french': 'fr-FR-DeniseNeural',
                'italian': 'it-IT-ElsaNeural',
                'portuguese': 'pt-BR-FranciscaNeural',
                'ukrainian': 'uk-UA-PolinaNeural',
                'polish': 'pl-PL-ZofiaNeural',
                'japanese': 'ja-JP-NanamiNeural',
                'chinese': 'zh-CN-XiaoxiaoNeural',
                'korean': 'ko-KR-SunHiNeural',
                'arabic': 'ar-EG-SalmaNeural',
                'hindi': 'hi-IN-SwaraNeural',
            }
            voice = voice_name if voice_name and '-' in voice_name else default_voices.get(str(language or '').lower(), 'ru-RU-SvetlanaNeural')
            rate_percent = int((max(0.5, min(2.0, float(speech_speed))) - 1.0) * 100)
            rate = f"{rate_percent:+d}%"
            pitch_hz = max(-30, min(30, int(edge_pitch_hz or 0)))
            volume_percent = max(-30, min(30, int(edge_volume_percent or 0)))
            pitch = f"{pitch_hz:+d}Hz"
            volume = f"{volume_percent:+d}%"
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            edge_proxy = _get_system_proxy()

            async def generate():
                communicator = edge_tts.Communicate(
                    text=text,
                    voice=voice,
                    rate=rate,
                    pitch=pitch,
                    volume=volume,
                    boundary='WordBoundary',
                    proxy=edge_proxy,
                )
                # edge-tts supports a metadata JSONL path.  Keep compatibility
                # with older versions and simple test doubles that only accept
                # the audio path.
                parameters = inspect.signature(communicator.save).parameters
                if len(parameters) >= 2:
                    await communicator.save(raw_output_path, metadata_output_path)
                else:
                    await communicator.save(raw_output_path)

            last_error = None
            for attempt in range(2):
                try:
                    Path(raw_output_path).unlink(missing_ok=True)
                    Path(metadata_output_path).unlink(missing_ok=True)
                    timestamp_sidecar.unlink(missing_ok=True)
                    _call_tts_api_limited(
                        lambda: asyncio.run(generate()),
                        enforce_interval=False,
                    )
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt == 0:
                        import time
                        time.sleep(1.0)
            if last_error:
                raise last_error

            boundary_count = self._persist_edge_word_boundaries(
                metadata_output_path, output_path
            )
            if boundary_count:
                log_callback(f"   🎯 Получено точных Edge WordBoundary: {boundary_count}")

            if self._master_edge_audio(raw_output_path, mastered_output_path, log_callback):
                final_output_path = mastered_output_path
                Path(raw_output_path).unlink(missing_ok=True)
            else:
                Path(output_path).unlink(missing_ok=True)
                shutil.move(raw_output_path, output_path)
                final_output_path = output_path

            duration = self._get_audio_duration(final_output_path)
            if duration <= 0:
                return None, 0
            log_callback(
                f"✅ Edge TTS озвучка готова: {voice}, {duration:.1f} сек "
                f"(скорость {rate}, тон {pitch})"
            )
            return final_output_path, duration
        except ImportError:
            log_callback("❌ Edge TTS не установлен.")
            return self._try_windows_tts(
                text, output_path, language, speech_speed, log_callback
            )
        except Exception as exc:
            Path(raw_output_path).unlink(missing_ok=True)
            Path(mastered_output_path).unlink(missing_ok=True)
            timestamp_sidecar.unlink(missing_ok=True)
            log_callback(f"❌ Edge TTS временно недоступен: {exc}")
            return self._try_windows_tts(
                text, output_path, language, speech_speed, log_callback
            )
        finally:
            Path(metadata_output_path).unlink(missing_ok=True)

    def _try_windows_tts(
        self,
        text: str,
        output_path: str,
        language: str,
        speech_speed: float,
        log_callback,
    ) -> tuple:
        """Use an installed Windows voice when the free Edge service is unavailable."""
        if os.name != "nt":
            return None, 0

        wav_path = str(Path(output_path).with_suffix(".windows_tts.wav"))
        pythoncom_module = None
        voice = None
        stream = None
        audio_format = None
        try:
            import pythoncom
            import win32com.client

            pythoncom_module = pythoncom
            pythoncom.CoInitialize()
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            requested_language = str(language or "").lower()
            preferred_markers = {
                "russian": ("russian", "irina", "pavel"),
                "english": ("english", "zira", "david"),
            }.get(requested_language, (requested_language,))
            for token in voice.GetVoices():
                description = str(token.GetDescription() or "").lower()
                if any(marker and marker in description for marker in preferred_markers):
                    voice.Voice = token
                    break

            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            audio_format = win32com.client.Dispatch("SAPI.SpAudioFormat")
            audio_format.Type = 22  # 22 kHz, 16-bit, mono
            stream.Format = audio_format
            stream.Open(wav_path, 3, False)
            voice.AudioOutputStream = stream
            voice.Rate = max(-10, min(10, round((float(speech_speed) - 1.0) * 6)))
            voice.Speak(text)
            stream.Close()

            run_registered(
                [
                    FFMPEG_PATH,
                    "-y",
                    "-i",
                    wav_path,
                    "-c:a",
                    "libmp3lame",
                    "-q:a",
                    "2",
                    "-map_metadata",
                    "-1",
                    output_path,
                ],
                label="ffmpeg_windows_tts_encode",
                check=True,
                capture_output=True,
                timeout=120,
            )
            duration = self._get_audio_duration(output_path)
            if duration <= 0:
                return None, 0
            log_callback(
                f"✅ Бесплатная резервная озвучка Windows готова: {duration:.1f} сек"
            )
            return output_path, duration
        except Exception as exc:
            log_callback(f"❌ Резервная озвучка Windows недоступна: {exc}")
            return None, 0
        finally:
            # SAPI keeps the file handle through SpVoice even after
            # SpFileStream.Close().  Detach the stream and release all COM
            # wrappers before deleting the temporary WAV, otherwise an Edge
            # outage can be turned into an unrelated WinError 32.
            if voice is not None:
                try:
                    voice.AudioOutputStream = None
                except Exception:
                    pass
            if stream is not None:
                try:
                    stream.Close()
                except Exception:
                    pass
            audio_format = None
            stream = None
            voice = None
            try:
                if pythoncom_module is not None:
                    pythoncom_module.CoUninitialize()
            except Exception:
                pass
            _unlink_with_retry(wav_path)

    def text_to_speech_multi_speaker(self, text_segments: List[str], output_dir: Path, log_callback, voices: List[str], provider: str = 'gemini', api_key: str = None, speech_speed: float = 1.0, language: str = 'Russian') -> tuple:
        """Generate speech from multiple text segments using different speakers."""
        if not text_segments or not voices: return None, 0
        try:
            audio_files = []
            total_duration = 0.0
            for idx, text in enumerate(text_segments):
                if not text.strip(): continue
                voice_name = voices[idx % len(voices)]
                segment_path, duration = self.text_to_speech(
                    text=text, output_dir=output_dir, log_callback=lambda msg: None,
                    provider=provider, voice_name=voice_name, api_key=api_key,
                    speech_speed=speech_speed, language=language
                )
                if segment_path:
                    audio_files.append(segment_path)
                    total_duration += duration
            
            if not audio_files: return None, 0
            if len(audio_files) == 1: return audio_files[0], total_duration
            
            combined_path = self._combine_audio_files(audio_files, output_dir, log_callback)
            return combined_path, total_duration
        except Exception as e:
            log_callback(f"❌ Multi-speaker error: {e}")
            return None, 0

    _RUSSIAN_WORD_RE = re.compile(
        r"[А-Яа-яЁё\u0301]+(?:[-‐‑‒–—][А-Яа-яЁё\u0301]+)*"
    )
    _RUSSIAN_VOWELS = frozenset("АЕЁИОУЫЭЮЯаеёиоуыэюя")

    @staticmethod
    def _is_russian_language(language: str) -> bool:
        normalized = str(language or '').strip().lower().replace('_', '-')
        return (
            normalized == 'ru'
            or normalized.startswith('ru-')
            or 'russian' in normalized
            or 'рус' in normalized
        )

    @classmethod
    def _needs_contextual_stress(cls, text: str) -> bool:
        """Return True when the text contains an unstressed Russian word."""
        for match in cls._RUSSIAN_WORD_RE.finditer(str(text or '')):
            word = match.group(0)
            if '\u0301' in word or 'ё' in word.lower():
                continue
            if sum(char in cls._RUSSIAN_VOWELS for char in word) >= 2:
                return True
        return False

    @staticmethod
    def _strip_stress_mark(text: str) -> str:
        return unicodedata.normalize('NFC', str(text or '').replace('\u0301', ''))

    @classmethod
    def _stress_positions(cls, word: str) -> Optional[List[int]]:
        """Return stressed Russian-letter indexes, or None for invalid marks."""
        normalized = unicodedata.normalize('NFC', word)
        letter_index = -1
        previous_char = ''
        positions = []
        for char in normalized:
            if char == '\u0301':
                if previous_char not in cls._RUSSIAN_VOWELS or letter_index < 0:
                    return None
                positions.append(letter_index)
                continue
            if re.match(r'[А-Яа-яЁё]', char):
                letter_index += 1
                previous_char = char
            elif unicodedata.combining(char):
                continue
            else:
                previous_char = ''
        return positions

    @classmethod
    def _transfer_word_stress(cls, source_word: str, candidate_word: str) -> str:
        # User-provided/manual accents always win over the automatic result.
        if '\u0301' in source_word:
            return source_word
        # A separate accent mark is unnecessary for monosyllables and for words
        # containing ё; rejecting it also catches a common model over-marking.
        if (
            sum(char in cls._RUSSIAN_VOWELS for char in source_word) < 2
            or 'ё' in source_word.lower()
        ):
            return source_word

        positions = cls._stress_positions(candidate_word)
        max_marks = len(re.findall(r'[-‐‑‒–—]', source_word)) + 1
        if not positions or len(positions) > max_marks or len(set(positions)) != len(positions):
            return source_word

        output = []
        letter_index = -1
        for char in source_word:
            output.append(char)
            if re.match(r'[А-Яа-яЁё]', char):
                letter_index += 1
                if letter_index in positions:
                    output.append('\u0301')
        return ''.join(output)

    @classmethod
    def _merge_stress_marks(cls, source_text: str, candidate_text: str) -> Optional[str]:
        """Transfer only valid stress marks while preserving source text exactly.

        Gemini is allowed to reflow whitespace or wrap the answer in a Markdown
        fence, but it may not add, remove, reorder or change Russian words. The
        returned string always retains the source punctuation, numbers, casing,
        tags and spacing.
        """
        candidate = str(candidate_text or '').strip()
        fenced = re.fullmatch(r"```(?:text|txt)?\s*\n?(.*?)\n?```", candidate, re.DOTALL | re.IGNORECASE)
        if fenced:
            candidate = fenced.group(1)

        source_matches = list(cls._RUSSIAN_WORD_RE.finditer(source_text))
        candidate_matches = list(cls._RUSSIAN_WORD_RE.finditer(candidate))
        if not source_matches or len(source_matches) != len(candidate_matches):
            return None

        for source_match, candidate_match in zip(source_matches, candidate_matches):
            if cls._strip_stress_mark(source_match.group(0)) != cls._strip_stress_mark(candidate_match.group(0)):
                return None

        pieces = []
        cursor = 0
        for source_match, candidate_match in zip(source_matches, candidate_matches):
            pieces.append(source_text[cursor:source_match.start()])
            pieces.append(cls._transfer_word_stress(source_match.group(0), candidate_match.group(0)))
            cursor = source_match.end()
        pieces.append(source_text[cursor:])
        return ''.join(pieces)

    def _prepare_contextual_stress(
        self,
        text: str,
        api_key: str,
        language: str,
        provider: str,
        log_callback,
    ) -> str:
        if not self._is_russian_language(language) or not self._needs_contextual_stress(text):
            return text

        if not self._is_valid_ascii_api_key(api_key):
            if provider == 'edge' and log_callback:
                log_callback(
                    "   ℹ️ Edge TTS работает без ключа; контекстная проверка ударений "
                    "пропущена, потому что Gemini API ключ не настроен."
                )
            return text

        # A multi-hour narration can be tens of thousands of words. Sending it
        # as one pronunciation request is fragile and can exceed model limits.
        if len(text) > 12000:
            chunks = self._split_text_into_chunks(text, 8000, language)
            if log_callback:
                log_callback(f"   🧩 Проверка ударений по частям: {len(chunks)} чанков")
            prepared = []
            for index, chunk in enumerate(chunks, 1):
                with self._stress_cache_lock:
                    cached_chunk = self._stress_cache.get(chunk)
                if cached_chunk is None:
                    cached_chunk = self._add_stress_marks(chunk, api_key, log_callback)
                    if cached_chunk != chunk and '\u0301' in cached_chunk:
                        with self._stress_cache_lock:
                            self._stress_cache[chunk] = cached_chunk
                            self._stress_cache.move_to_end(chunk)
                            while len(self._stress_cache) > self._stress_cache_limit:
                                self._stress_cache.popitem(last=False)
                prepared.append(cached_chunk)
                if log_callback and (index == len(chunks) or index % 10 == 0):
                    log_callback(f"   ✅ Ударения: {index}/{len(chunks)}")
            return " ".join(prepared)

        with self._stress_cache_lock:
            cached = self._stress_cache.get(text)
            if cached is not None:
                self._stress_cache.move_to_end(text)
                if log_callback:
                    log_callback("   ♻️ Используем уже проверенные ударения")
                return cached

        stressed = self._add_stress_marks(text, api_key, log_callback)
        if stressed != text and '\u0301' in stressed:
            with self._stress_cache_lock:
                self._stress_cache[text] = stressed
                self._stress_cache.move_to_end(text)
                while len(self._stress_cache) > self._stress_cache_limit:
                    self._stress_cache.popitem(last=False)
        return stressed

    @staticmethod
    def _add_stress_marks(text: str, api_key: str, log_callback) -> str:
        """
        Расставляет ударения в русском тексте используя Gemini API.
        Добавляет символ ́ (U+0301) после ударной гласной.
        Автоматически применяет системный прокси если обнаружен.
        """
        log = log_callback if callable(log_callback) else (lambda _message: None)
        try:
            from core.gemini_client import GeminiClient

            log("📝 Контекстная проверка русских ударений через Gemini...")

            prompt = f"""Ты — редактор произношения русского текста для синтеза речи.

ЗАДАЧА
Добавь комбинируемый знак ударения U+0301 сразу после ударной гласной в русских многосложных словах. Выбирай ударение по смыслу всего текста, особенно у омографов, имён, фамилий, топонимов, аббревиатур и редких терминов.

СТРОГИЕ ОГРАНИЧЕНИЯ
1. Не переписывай, не исправляй и не сокращай текст.
2. Не меняй буквы, регистр, числа, знаки препинания, пробелы, переносы строк и служебные теги.
3. Разрешённое изменение только одно: добавление U+0301 после русской гласной.
4. Не ставь знак в односложных словах. Буква «ё» уже однозначно ударная и дополнительного знака не требует.
5. Сохрани уже имеющиеся знаки U+0301: это ручные пользовательские исправления с высшим приоритетом.
6. Верни только обработанный текст без пояснений, заголовка и Markdown.

ТЕКСТ
<tts_text>
{text}
</tts_text>"""

            proxy_url = _get_system_proxy()
            if proxy_url:
                log(f"🌐 [Ударения] Используем системный прокси: {proxy_url}")

            with _ProxyContext(proxy_url):
                response = GeminiClient(
                    api_key=api_key,
                    log_callback=log,
                ).generate_text(
                    prompt=prompt,
                    temperature=0.0,
                    max_tokens=max(4096, min(65536, int(len(text) * 0.8) + 1024)),
                )

            if response and response.success and response.raw_text:
                stressed_text = AudioProcessor._merge_stress_marks(text, response.raw_text)
                stress_mark = '\u0301'
                if stressed_text is None:
                    log("⚠️ Gemini изменил слова в ответе; небезопасный результат ударений отклонён")
                    return text
                new_marks = max(0, stressed_text.count(stress_mark) - text.count(stress_mark))
                if new_marks > 0:
                    log(f"✅ Добавлены контекстные ударения ({new_marks} шт.)")
                    return stressed_text
                else:
                    log("⚠️ Gemini не добавил ударения, используем оригинальный текст")
                    return text
            else:
                error = getattr(response, 'error', None)
                suffix = f": {str(error)[:120]}" if error else ""
                log(f"⚠️ Не удалось проверить ударения{suffix}; используем оригинальный текст")
                return text

        except Exception as e:
            log(f"⚠️ Ошибка расстановки ударений: {str(e)[:100]}")
            return text

    @staticmethod
    def _extract_gemini_tts_audio(response) -> Optional[bytes]:
        """Return the first audio payload without assuming a complete SDK response."""
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                inline_data = getattr(part, "inline_data", None)
                data = getattr(inline_data, "data", None)
                if data:
                    return data
        return None

    def _try_gemini_tts(self, text: str, output_path: str, api_key: str, voice_name: str, log_callback, speech_speed: float = 1.0, language: str = 'Russian', is_vertical: bool = False, persona_id: str = None) -> tuple:
        """Try to generate speech using Gemini TTS API with fallback to Pro model.
        Автоматически определяет и применяет системный прокси/VPN.
        """

        clean_voice_name = self._extract_voice_name(voice_name)
        
        # 🆕 GEMINI 3.1 ADVANCED PROMPT: Сохраняем оригинал для fallback-моделей
        _original_text = text
        _gemini31_prompt = self.build_gemini31_tts_prompt(text, language, is_vertical, clean_voice_name, persona_id=persona_id)
        log_callback(f"🎙️ [3.1 TTS] Подготовлен Advanced Prompt ({len(_gemini31_prompt)} симв.)")
        if persona_id:
            log_callback(f"🎭 [TTS] Стиль озвучки: {persona_id}")

        # 🌐 Автоопределение системного прокси (VPN-клиенты могут его выставлять)
        _proxy_url = _get_system_proxy()
        if _proxy_url:
            log_callback(f"🌐 [TTS] Обнаружен системный прокси: {_proxy_url}")

        from google.genai import types
        
        # 🔧 УДАЛЕНО: Принудительная подмена голоса для вертикальных видео
        # Пользователь должен сам выбирать голос в GUI
        # if is_vertical and clean_voice_name not in ['Fenrir', 'Puck']: clean_voice_name = 'Fenrir'
        
        # 🔑 Интеграция с APIKeyManager для запоминания исчерпанных квот
        from core.api_key_manager import get_key_manager
        key_manager = get_key_manager()
        key_manager.set_log_callback(log_callback)
        
        # 🔑 FIX: Собираем ВСЕ доступные ключи для ротации
        all_keys = list(key_manager.keys) if key_manager.keys else [api_key]
        if api_key not in all_keys:
            all_keys.insert(0, api_key)  # Основной ключ первым
        
        # 🔄 FALLBACK: 3.1 → Flash → Pro при 429 (с памятью на сессию)
        # 🎤 VOICE CONSISTENCY FIX: Если уже использовали модель - используем её первой
        models_to_try = [
            (TTS_MODEL_CHAIN[0], "3.1 Flash", "3.1flash"),
            (TTS_MODEL_CHAIN[1], "Flash", "flash"),
            (TTS_MODEL_CHAIN[2], "Pro", "pro"),
        ]
        
        # 🎤 FIX: Если есть запомненная модель - ставим её первой для consistency
        if self.last_successful_model:
            # Находим запомненную модель и ставим её первой
            remembered_model = None
            other_models = []
            for model_tuple in models_to_try:
                if model_tuple[2] == self.last_successful_model:
                    remembered_model = model_tuple
                else:
                    other_models.append(model_tuple)
            
            if remembered_model:
                models_to_try = [remembered_model] + other_models
                log_callback(f"🎤 Используем запомненную модель {remembered_model[1]} для consistency голоса")
        
        # 🎤 FIX: Если есть запомненный ключ - ставим его первым
        if self.last_successful_key and self.last_successful_key in all_keys:
            all_keys.remove(self.last_successful_key)
            all_keys.insert(0, self.last_successful_key)
        
        last_error = None
        response = None
        audio_data = None
        
        # 🔑 FIX: Пробуем ВСЕ комбинации ключ + модель
        for model_name, model_label, model_short in models_to_try:
            for current_key in all_keys:
                # 🔑 ПРОВЕРКА: Пропускаем если комбинация уже исчерпана
                if key_manager.is_exhausted(current_key, model_short):
                    log_callback(f"⏭️ {model_label}: текущий ключ уже исчерпан, пробуем следующий...")
                    continue
                
                log_callback(f"🎤 Gemini TTS ({model_label}): {clean_voice_name}, {language}")
                
                # 🆕 Выбираем входной текст: 3.1 → Advanced Prompt, остальные → оригинальный текст
                _is_31_model = '3.1' in model_name
                _tts_input = _gemini31_prompt if _is_31_model else _original_text
                if _is_31_model:
                    log_callback("✨ [3.1 TTS] Используем Advanced Prompt с Audio Tags")

                # 🔧 VPN FIX: таймауты учитывают медленный канал через VPN.
                tts_timeout = 180 if 'pro' in model_name.lower() else 90

                # 🌐 Если есть прокси — используем REST API через requests (явный прокси),
                # иначе — SDK (httpx env vars могут не работать внутри потоков).
                def call_tts_api(model=model_name, key=current_key, proxy=_proxy_url, tts_input=_tts_input):
                    def invoke():
                        if proxy:
                            # REST-путь: requests явно передаёт прокси.
                            raw_audio = AudioProcessor._call_gemini_tts_rest(
                                text=tts_input,
                                model_name=model,
                                voice_name=clean_voice_name,
                                api_key=key,
                                proxy_url=proxy,
                                timeout=90 if 'pro' not in model.lower() else 180
                            )

                            class _RestResponse:
                                class _Candidate:
                                    def __init__(self, data):
                                        self.content = type('C', (), {
                                            'parts': [type('P', (), {
                                                'inline_data': type('I', (), {'data': data})()
                                            })()]
                                        })()

                                def __init__(self, data):
                                    self.candidates = [self._Candidate(data)]

                            return _RestResponse(raw_audio)

                        with _ProxyContext(proxy):
                            cli = _create_genai_client(key, tts_timeout)
                            return cli.models.generate_content(
                                model=model, contents=tts_input,
                                config=types.GenerateContentConfig(
                                    response_modalities=["AUDIO"],
                                    speech_config=types.SpeechConfig(
                                        voice_config=types.VoiceConfig(
                                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                                voice_name=clean_voice_name)
                                        )
                                    )
                                )
                            )

                    # Every model/key attempt, including retries, is serialized.
                    return _call_tts_api_limited(invoke)

                try:
                    response = call_tts_api(
                        model_name, current_key, _proxy_url, _tts_input
                    )
                    audio_data = self._extract_gemini_tts_audio(response)
                    if not audio_data:
                        last_error = ValueError(
                            f"Gemini TTS returned no audio payload ({model_label})"
                        )
                        response = None
                        log_callback(
                            f"⚠️ {model_label} вернул пустой аудиоответ; "
                            "пробуем следующий ключ или модель"
                        )
                        continue
                    # 🎤 FIX: Запоминаем успешную модель и ключ для consistency
                    self.last_successful_model = model_short
                    self.last_successful_key = current_key
                    # Успех - выходим из ОБОИХ циклов
                    break
                except Exception as e:
                    error_str = str(e)

                    if _is_request_timeout(e):
                        log_callback(f"⏱️ TTS таймаут ({tts_timeout}s) на {model_label}")
                        last_error = TimeoutError(
                            f"TTS API timeout after {tts_timeout}s ({model_label})"
                        )
                        continue
                    
                    # 🔄 SSL и 500/INTERNAL ошибки - временные, retry с backoff
                    is_ssl_error = 'SSL' in error_str or '_ssl.c' in error_str or 'EOF occurred' in error_str
                    is_500_error = '500' in error_str or 'INTERNAL' in error_str or 'internal error' in error_str.lower()
                    
                    if is_ssl_error or is_500_error:
                        error_type = "SSL" if is_ssl_error else "500/INTERNAL"
                        max_retries = 5  # 🔧 VPN FIX: 3 → 5 retry для SSL/500 ошибок
                        
                        for retry_num in range(max_retries):
                            wait_time = (retry_num + 1) * 3  # 🔧 VPN FIX: 2,4,6 → 3,6,9,12,15 сек
                            log_callback(f"⚠️ {error_type} ошибка, retry {retry_num + 1}/{max_retries} через {wait_time}s...")
                            time.sleep(wait_time)
                            
                            try:
                                # Пересоздаём клиент на случай проблем с соединением
                                response = call_tts_api(
                                    model_name, current_key, _proxy_url, _tts_input
                                )
                                audio_data = self._extract_gemini_tts_audio(response)
                                if not audio_data:
                                    response = None
                                    raise ValueError(
                                        f"Gemini TTS returned no audio payload ({model_label})"
                                    )
                                # 🎤 FIX: Запоминаем успешную модель и ключ после retry
                                self.last_successful_model = model_short
                                self.last_successful_key = current_key
                                log_callback(f"✅ {error_type} retry успешен!")
                                break  # Успех - выходим из retry цикла
                            except Exception as retry_e:
                                str(retry_e)
                                if retry_num == max_retries - 1:
                                    log_callback(f"❌ {error_type} retry исчерпаны")
                                    last_error = retry_e
                                continue
                        else:
                            # Все retry исчерпаны - пробуем следующий ключ/модель
                            continue
                        # Retry успешен - выходим из цикла ключей
                        break
                    
                    elif '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
                        # 🔑 ЗАПОМИНАЕМ: Помечаем комбинацию как исчерпанную
                        key_manager.mark_exhausted(current_key, model_short)
                        last_error = e
                        continue  # Пробуем следующий ключ
                    elif '403' in error_str or 'PERMISSION_DENIED' in error_str or '401' in error_str or 'UNAUTHENTICATED' in error_str:
                        # 🔑 Ошибка ключа: пропускаем ключ и пробуем следующий
                        log_callback("⚠️ Ошибка авторизации (403/401) для текущего ключа, пропускаем его")
                        key_manager.mark_exhausted(current_key, model_short)
                        last_error = e
                        continue  # Пробуем следующий ключ
                    elif 'PROHIBITED_CONTENT' in error_str:
                        # 🔄 3.1 может отклонить промпт — fallback на следующую модель
                        log_callback(f"⚠️ {model_label}: PROHIBITED_CONTENT, переключаемся на следующую модель")
                        last_error = e
                        break  # Выходим из цикла ключей → следующая модель
                    else:
                        # Другая ошибка - пробрасываем
                        raise
            else:
                # Внутренний цикл (ключи) завершился без break - пробуем следующую модель
                continue
            # Внутренний цикл завершился с break - выходим из внешнего
            break
        else:
            # Все комбинации исчерпаны
            if last_error:
                raise last_error
            raise ValueError("All TTS models exhausted")
        
        if response is None:
            raise ValueError("No TTS response - all models exhausted or skipped")
        
        if not audio_data:
            raise ValueError("No audio data")
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        temp_raw_path = output_path.replace('.mp3', '_temp.raw')
        
        try:
            with open(temp_raw_path, 'wb') as f: f.write(audio_data)
            
            # Используем стандартный resampler (swr) - soxr часто недоступен в essentials builds
            # Порядок: сначала без soxr, потом с soxr (если вдруг доступен)
            for use_soxr in [False, True]:
                ffmpeg_cmd = [
                    FFMPEG_PATH, '-y', '-f', 's16le', '-ar', '24000', '-ac', '1', '-i', temp_raw_path
                ]
                if use_soxr:
                    audio_filters = [
                        'aresample=44100:resampler=soxr', 'highpass=f=80', 'lowpass=f=8000', 'loudnorm=I=-16:TP=-1.5:LRA=11'
                    ]
                else:
                    # Стандартный resampler (swr) - работает везде
                    audio_filters = [
                        'aresample=44100', 'highpass=f=80', 'lowpass=f=8000', 'loudnorm=I=-16:TP=-1.5:LRA=11'
                    ]
                if speech_speed != 1.0: audio_filters.append(f'atempo={speech_speed}')
                
                ffmpeg_cmd.extend(['-af', ','.join(audio_filters), '-c:a', 'libmp3lame', '-q:a', '2', '-map_metadata', '-1', output_path])
                
                try:
                    run_registered(
                        ffmpeg_cmd, 
                        label="ffmpeg_gemini_tts_encode",
                        check=True, 
                        capture_output=True, 
                        timeout=30
                    )
                    break  # Успех - выходим из цикла
                except subprocess.CalledProcessError as e:
                    stderr_text = e.stderr.decode('utf-8', errors='ignore') if e.stderr else 'No stderr'
                    stderr_text.lower()
                    # Если стандартный resampler не сработал, попробуем soxr (маловероятно что поможет)
                    # Если soxr не сработал - это финальная ошибка
                    if not use_soxr:
                        continue  # Попробуем с soxr
                    stdout_text = e.stdout.decode('utf-8', errors='ignore') if e.stdout else 'No stdout'
                    raise ValueError(f"FFmpeg error (code {e.returncode}):\nSTDERR: {stderr_text}\nSTDOUT: {stdout_text}")
            
            # 💰 COST TRACKING: Отслеживаем токены (Gemini TTS)
            try:
                tracker = get_tracker()
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    input_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
                    output_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
                    if input_tokens > 0 or output_tokens > 0:
                        tracker.add_gemini_tts(
                            input_tokens=input_tokens,
                            audio_tokens=output_tokens,
                            model=model_name,
                        )
            except Exception:
                pass  # Не ломаем генерацию из-за трекинга
            
            audio_clip = None
            try:
                audio_clip = AudioFileClip(output_path)
                duration = audio_clip.duration
                return output_path, duration
            finally:
                if audio_clip:
                    try:
                        audio_clip.close()
                    except Exception:
                        pass
        finally:
            if os.path.exists(temp_raw_path): 
                try:
                    os.remove(temp_raw_path)
                except Exception:
                    pass

    def _combine_tts_timestamp_sidecars(
        self,
        audio_files: list,
        combined_path: str,
        log_callback=None,
    ) -> int:
        """Merge complete provider-native boundary tracks with chunk offsets."""
        destination = self._timestamp_sidecar_path(combined_path)
        combined_timestamps = []
        offset = 0.0

        for audio_file in audio_files:
            duration = self._get_audio_duration(str(audio_file))
            timestamps = self._load_tts_word_timestamps(str(audio_file))
            if duration <= 0 or not timestamps:
                destination.unlink(missing_ok=True)
                return 0

            for item in timestamps:
                start = max(0.0, min(duration, float(item['start'])))
                end = max(start, min(duration, float(item['end'])))
                if end <= start:
                    continue
                combined_timestamps.append({
                    'word': item['word'],
                    'start': offset + start,
                    'end': offset + end,
                    'no_space': bool(item.get('no_space', False)),
                })
            offset += duration

        if not combined_timestamps:
            destination.unlink(missing_ok=True)
            return 0

        import json

        temporary = destination.with_suffix(destination.suffix + '.tmp')
        try:
            temporary.write_text(
                json.dumps(
                    {
                        'version': 1,
                        'provider': 'combined-native',
                        'timestamps': combined_timestamps,
                    },
                    ensure_ascii=False,
                    separators=(',', ':'),
                ),
                encoding='utf-8',
            )
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

        if log_callback:
            log_callback(
                f"   🎯 Объединены точные TTS-границы: {len(combined_timestamps)}"
            )
        return len(combined_timestamps)

    def _combine_audio_files(self, audio_files: list, output_dir: Path, log_callback) -> str:
        """Single-flight wrapper for deterministic long-form TTS concatenation."""
        if not audio_files:
            return None
        if len(audio_files) == 1:
            return str(audio_files[0])
        resolved = [str(Path(path).resolve()).replace('\\', '/') for path in audio_files]
        digest = hashlib.sha256("\n".join(resolved).encode('utf-8')).hexdigest()[:24]
        combined_path = output_dir / f"voiceover_combined_{digest}.wav"
        with _TTS_OUTPUT_LOCKS.hold(str(combined_path.resolve())):
            return self._combine_audio_files_locked(
                audio_files, output_dir, log_callback
            )

    def _combine_audio_files_locked(self, audio_files: list, output_dir: Path, log_callback) -> str:
        """Stream-concatenate TTS chunks with FFmpeg without opening all clips in RAM."""
        concat_path = None
        try:
            if not audio_files:
                return None
            if len(audio_files) == 1:
                return str(audio_files[0])

            resolved = [str(Path(path).resolve()).replace('\\', '/') for path in audio_files]
            digest = hashlib.sha256("\n".join(resolved).encode('utf-8')).hexdigest()[:24]
            combined_path = output_dir / f"voiceover_combined_{digest}.wav"
            if combined_path.is_file() and combined_path.stat().st_size > 1000:
                duration = self._get_audio_duration(str(combined_path))
                if duration > 0:
                    try:
                        self._combine_tts_timestamp_sidecars(
                            audio_files, str(combined_path), log_callback
                        )
                    except Exception as boundary_error:
                        log_callback(
                            "   ⚠️ Точные границы TTS-чанков не объединены: "
                            f"{str(boundary_error)[:120]}"
                        )
                    log_callback(f"   ♻️ Объединённая озвучка уже готова ({duration:.1f}s)")
                    return str(combined_path)

            concat_path = output_dir / f"tts_concat_{digest}.txt"
            concat_lines = []
            for path in resolved:
                escaped = path.replace("'", "'\\''")
                concat_lines.append(f"file '{escaped}'")
            concat_path.write_text("\n".join(concat_lines) + "\n", encoding='utf-8')

            source_duration = sum(max(0.0, self._get_audio_duration(path)) for path in resolved)
            timeout = max(300, min(7200, int(source_duration * 0.5) + 120))
            command = [
                FFMPEG_PATH,
                '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_path),
                '-vn',
                '-af', 'loudnorm=I=-16:TP=-1.5:LRA=8',
                '-ac', '1',
                '-ar', '24000',
                '-c:a', 'pcm_s16le',
                '-map_metadata', '-1',
                str(combined_path),
            ]
            result = run_registered(
                command,
                label="ffmpeg_tts_chunks_concat",
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=timeout,
            )
            if result.returncode != 0 or not combined_path.is_file():
                raise RuntimeError(result.stderr[-800:] or f"FFmpeg exited with {result.returncode}")

            try:
                self._combine_tts_timestamp_sidecars(
                    audio_files, str(combined_path), log_callback
                )
            except Exception as boundary_error:
                log_callback(
                    "   ⚠️ Точные границы TTS-чанков не объединены: "
                    f"{str(boundary_error)[:120]}"
                )

            # Metadata is stripped in the same FFmpeg pass above. Avoid copying
            # a 0.5 GB three-hour WAV through a second 30-second operation.
            return str(combined_path)
            
        except Exception as e:
            log_callback(f"❌ Ошибка объединения аудио: {e}")
            return None
        finally:
            if concat_path:
                Path(concat_path).unlink(missing_ok=True)

    def _create_silent_audio(self, duration: float, output_dir: Path) -> str:
        """Create a silent audio MP3 of given duration (seconds)."""
        filename = f"silent_{int(duration)}s.mp3"
        filepath = output_dir / filename
        clip = None
        try:
            clip = AudioClip(lambda t: 0.0, duration=duration, fps=44100)
            clip.write_audiofile(str(filepath), fps=44100, nbytes=2, codec='mp3', logger=None)
            return str(filepath)
        finally:
            if clip:
                try:
                    clip.close()
                except Exception:
                    pass

    def _get_audio_duration(self, audio_path: str) -> float:
        """Получить длительность аудио файла"""
        try:
            cmd = [
                _get_ffprobe_path(),
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                audio_path
            ]
            
            result = run_registered(
                cmd, 
                label="ffprobe_audio_duration",
                capture_output=True, 
                text=True, 
                encoding='utf-8',
                errors='ignore',
                timeout=10
            )
            
            if result.returncode != 0:
                return 0.0
            
            import json
            data = json.loads(result.stdout)
            format_data = data.get('format', {})
            if not isinstance(format_data, dict):
                return 0.0
            
            duration_str = format_data.get('duration', '0.0')
            try:
                return float(duration_str)
            except (ValueError, TypeError):
                return 0.0
                
        except subprocess.TimeoutExpired:
            return 0.0
        except json.JSONDecodeError:
            return 0.0
        except Exception:
            return 0.0
    
    def add_background_music(self, voiceover_path, music_path, voiceover_duration, output_dir, log_callback, music_volume=0.25):
        """Mixes the voiceover with background music using pure FFmpeg subprocess."""
        log_callback("=" * 60)
        log_callback("🎶 ДОБАВЛЕНИЕ ФОНОВОЙ МУЗЫКИ (FFmpeg)")
        
        if not os.path.exists(voiceover_path): return None
        if not music_path or not os.path.exists(music_path): return voiceover_path
        music_volume = self._clamp_music_volume(music_volume)
        if music_volume <= 0:
            log_callback("🔇 Громкость музыки 0% - используем только голос")
            return voiceover_path
        log_callback(f"🔊 Громкость фоновой музыки: {music_volume * 100:.0f}%")
        
        try:
            # 🎵 УМНАЯ МУЗЫКА ДЛЯ ДЛИННЫХ ВИДЕО
            from core.music_sequencer import MusicSequencer
            
            # Проверяем нужна ли смена музыки
            if MusicSequencer.should_use_sequence(voiceover_duration):
                log_callback(f"🎵 Длинное видео ({voiceover_duration/60:.1f} мин) - используем музыкальную последовательность")
                
                # Создаём последовательность треков
                sequence = MusicSequencer.create_music_sequence(
                    duration=voiceover_duration,
                    music_path=music_path,
                    base_volume=music_volume
                )
                
                if sequence:
                    # Применяем последовательность
                    timestamp = int(time.time() * 1000000)
                    final_audio_path = output_dir / f"final_audio_{timestamp}.mp3"
                    
                    result = MusicSequencer.apply_music_sequence(
                        voiceover_path=voiceover_path,
                        sequence=sequence,
                        output_path=str(final_audio_path),
                        log_callback=log_callback
                    )
                    
                    if result and Path(result).exists():
                        return str(result)
                    else:
                        log_callback("⚠️ Не удалось применить последовательность, используем обычную музыку")
            
            # Обычная логика для коротких видео или fallback
            music_files = [f for f in os.listdir(music_path) if f.lower().endswith(('.mp3', '.wav', '.aac', '.flac', '.m4a'))]
            if not music_files: return voiceover_path
            
            random_music_file = os.path.join(music_path, random.choice(music_files))
            
            # Получаем длительность музыкального файла
            music_duration = self._get_audio_duration(random_music_file)
            
            # Выбираем рандомное место начала (если музыка длиннее озвучки)
            start_offset = 0
            if music_duration > voiceover_duration + 5:  # Если музыка достаточно длинная
                # Выбираем рандомное место, оставляя запас в конце
                max_start = music_duration - voiceover_duration - 2
                start_offset = random.uniform(0, max(0, max_start))
                log_callback(f"🎵 Музыка: {os.path.basename(random_music_file)} (начало с {start_offset:.1f}s)")
            else:
                log_callback(f"🎵 Музыка: {os.path.basename(random_music_file)}")
            
            timestamp = int(time.time() * 1000000)
            final_audio_path = output_dir / f"final_audio_{timestamp}.mp3"
            
            fade_start = max(0, voiceover_duration - 2)
            filter_complex = (
                f"[1:a]volume={music_volume:.4f},apad,atrim=0:{voiceover_duration:.3f},"
                f"afade=t=out:st={fade_start:.3f}:d=2[music_raw];"
                f"[music_raw][0:a]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=250[music];"
                f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
            )
            
            # Добавляем -ss для начала с рандомного места
            cmd = [
                FFMPEG_PATH, '-y', 
                '-i', str(voiceover_path),
                '-ss', str(start_offset),  # Начинаем с рандомного места
                '-stream_loop', '-1', 
                '-i', str(random_music_file),
                '-filter_complex', filter_complex,
                '-map', '[aout]', '-c:a', 'libmp3lame', '-b:a', '192k', '-ar', '44100', str(final_audio_path)
            ]
            
            run_registered(
                cmd, 
                label="ffmpeg_background_music_mix",
                check=True, 
                capture_output=True, 
                encoding='utf-8',
                errors='ignore',
                timeout=int(voiceover_duration * 2 + 30)
            )
            
            if not final_audio_path.exists(): 
                return voiceover_path
            
            log_callback(f"✅ Фоновая музыка добавлена: {os.path.basename(random_music_file)}")
            return str(final_audio_path)
            
        except subprocess.TimeoutExpired as timeout_err:
            log_callback(f"❌ Timeout при добавлении музыки (>{int(voiceover_duration * 2 + 30)}s)")
            log_callback(f"   Ошибка: {timeout_err}")
            # Возвращаем None чтобы сигнализировать об ошибке
            # Вызывающий код должен решить что делать
            return None
        except subprocess.CalledProcessError as ffmpeg_err:
            log_callback(f"❌ FFmpeg ошибка при добавлении музыки: {ffmpeg_err}")
            log_callback(f"   Stderr: {ffmpeg_err.stderr if hasattr(ffmpeg_err, 'stderr') else 'N/A'}")
            return None
        except Exception as e:
            log_callback(f"❌ Неизвестная ошибка добавления музыки: {e}")
            import traceback
            log_callback(f"   Traceback:\n{traceback.format_exc()}")
            return None

    def create_music_only_audio(self, music_path: str, duration: float, output_dir: Path, 
                                 log_callback, volume: float = 0.5) -> str:
        """
        Создаёт аудио только из музыки (без озвучки).
        Используется когда TTS отключен.
        
        Args:
            music_path: Путь к папке с музыкой
            duration: Целевая длительность в секундах
            output_dir: Папка для сохранения
            log_callback: Функция логирования
            volume: Громкость музыки (0.0 - 1.0)
            
        Returns:
            Путь к созданному аудио файлу или None
        """
        import random
        import subprocess
        
        log_callback(f"🎵 Создание аудио только из музыки ({duration:.1f}s)...")
        
        if not music_path or not os.path.exists(music_path):
            log_callback("❌ Папка с музыкой не найдена")
            return None
        
        # Находим музыкальные файлы
        music_files = [f for f in os.listdir(music_path) 
                       if f.lower().endswith(('.mp3', '.wav', '.aac', '.flac', '.m4a'))]
        
        if not music_files:
            log_callback("❌ Нет музыкальных файлов в папке")
            return None
        
        # Выбираем случайный трек
        random_music = random.choice(music_files)
        music_file_path = os.path.join(music_path, random_music)
        
        log_callback(f"   🎶 Трек: {random_music}")
        
        # Создаём выходной файл
        timestamp = int(time.time() * 1000000)
        output_path = output_dir / f"music_only_{timestamp}.mp3"
        
        try:
            # FFmpeg команда: обрезаем музыку до нужной длительности и применяем громкость
            cmd = [
                FFMPEG_PATH, '-y',
                '-stream_loop', '-1',  # Зацикливаем если музыка короче
                '-i', music_file_path,
                '-t', str(duration),  # Обрезаем до нужной длительности
                '-af', f'volume={volume},afade=t=in:st=0:d=1,afade=t=out:st={duration-2}:d=2',  # Громкость + fade in/out
                '-c:a', 'libmp3lame',
                '-b:a', '192k',
                '-ar', '44100',
                str(output_path)
            ]
            
            result = run_registered(
                cmd,
                label="ffmpeg_music_only",
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=60
            )
            
            if result.returncode != 0:
                log_callback(f"❌ FFmpeg ошибка: {result.stderr[:200]}")
                return None
            
            if output_path.exists() and output_path.stat().st_size > 0:
                log_callback(f"   ✅ Музыка готова: {output_path.name}")
                return str(output_path)
            else:
                log_callback("❌ Файл не создан")
                return None
                
        except subprocess.TimeoutExpired:
            log_callback("❌ Timeout при создании музыки")
            return None
        except Exception as e:
            log_callback(f"❌ Ошибка: {e}")
            return None
