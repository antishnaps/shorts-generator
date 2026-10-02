#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GUI Constants - централизованное хранение констант для GUI

P1: Вынесены хардкод списки из main_window.py
"""

# ============================================================
# 🌍 ЯЗЫКИ - ЕДИНСТВЕННЫЙ ИСТОЧНИК ИСТИНЫ
# ============================================================

# Маппинг: GUI название -> внутреннее название (English)
LANGUAGE_MAP = {
    "🇷🇺 Русский": "Russian",
    "🇺🇸 English": "English",
    "🇪🇸 Español": "Spanish",
    "🇫🇷 Français": "French",
    "🇩🇪 Deutsch": "German",
    "🇨🇳 中文": "Chinese",
    "🇯🇵 日本語": "Japanese",
    "🇰🇷 한국어": "Korean",
    "🇵🇹 Português": "Portuguese",
    "🇮🇹 Italiano": "Italian",
    "🇮🇳 हिन्दी": "Hindi",
    "🇸🇦 العربية": "Arabic",
}

# Список для ComboBox (с флагами)
LANGUAGES_LIST = list(LANGUAGE_MAP.keys())

# Язык по умолчанию
DEFAULT_LANGUAGE_INTERNAL = "Russian"


def get_internal_language(gui_language: str) -> str:
    """
    Конвертирует GUI название языка во внутреннее.
    
    Args:
        gui_language: Название из ComboBox (например "🇷🇺 Русский")
        
    Returns:
        Внутреннее название (например "Russian")
    """
    # Прямой маппинг
    if gui_language in LANGUAGE_MAP:
        return LANGUAGE_MAP[gui_language]
    
    # Проверяем старый формат "Русский (Russian)"
    if '(' in gui_language and ')' in gui_language:
        # Извлекаем English название из скобок
        start = gui_language.rfind('(') + 1
        end = gui_language.rfind(')')
        if start > 0 and end > start:
            return gui_language[start:end]
    
    # Проверяем без эмодзи
    for gui_name, internal in LANGUAGE_MAP.items():
        if gui_language in gui_name or internal.lower() == gui_language.lower():
            return internal
    
    # Fallback
    return gui_language if gui_language else DEFAULT_LANGUAGE_INTERNAL

# ============================================================
# 👤 ПЕРСОНЫ
# ============================================================

# Персоны текста
PERSONAS = [
    "🤖 Авто (выбор по теме)",
    "🔥 Вирусный (универсальный)",
    "⚔️ UFC Комментатор (бои, VS)",
    "🕵️ Конспиролог (тайны, НЛО)",
    "💪 Мотиватор (бизнес, успех)",
    "👻 Хоррор (страшилки, мистика)",
    "😎 Друган (факты, лайфхаки)",
    "📚 Серьезный (документальный)"
]

# Маппинг индекса персоны на ID
PERSONA_MAPPING = {
    0: None,        # Авто
    1: 'viral',     # Вирусный
    2: 'commentator',  # UFC Комментатор
    3: 'conspiracy',   # Конспиролог
    4: 'motivator',    # Мотиватор
    5: 'horror',       # Хоррор
    6: 'bro',          # Друган
    7: 'serious'       # Серьезный
}

# Голоса Gemini TTS (30 голосов)
GEMINI_VOICES = [
    "Kore (Firm)",
    "Puck (Upbeat)",
    "Charon (Informative)",
    "Fenrir (Excitable)",
    "Algieba (Smooth)",
    "Algenib (Gravelly)",
    "Gacrux (Mature)",
    "Zephyr (Bright)",
    "Leda (Youthful)",
    "Callirrhoe (Easy-going)",
    "Orus (Firm)",
    "Autonoe (Bright)",
    "Umbriel (Easy-going)",
    "Erinome (Clear)",
    "Laomedeia (Upbeat)",
    "Schedar (Even)",
    "Achird (Friendly)",
    "Sadachbia (Lively)",
    "Aoede (Breezy)",
    "Enceladus (Breathy)",
    "Achernar (Soft)",
    "Zubenelgenubi (Casual)",
    "Sadaltager (Knowledgeable)",
    "Iapetus (Clear)",
    "Despina (Smooth)",
    "Rasalgethi (Informative)",
    "Alnilam (Firm)",
    "Pulcherrima (Forward)",
    "Vindemiatrix (Gentle)",
    "Sulafat (Warm)",
]
DEFAULT_VOICE = "Kore (Firm)"

# Curated Edge TTS voices grouped by content language. Keeping this local makes
# provider switching instant and avoids a network request from the GUI.
EDGE_TTS_VOICES = {
    "Russian": ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"],
    "English": ["en-US-JennyNeural", "en-US-GuyNeural", "en-US-AriaNeural", "en-US-DavisNeural"],
    "Spanish": ["es-ES-ElviraNeural", "es-ES-AlvaroNeural"],
    "French": ["fr-FR-DeniseNeural", "fr-FR-HenriNeural"],
    "German": ["de-DE-KatjaNeural", "de-DE-ConradNeural"],
    "Chinese": ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"],
    "Japanese": ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural"],
    "Korean": ["ko-KR-SunHiNeural", "ko-KR-InJoonNeural"],
    "Portuguese": ["pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"],
    "Italian": ["it-IT-ElsaNeural", "it-IT-DiegoNeural"],
    "Hindi": ["hi-IN-SwaraNeural", "hi-IN-MadhurNeural"],
    "Arabic": ["ar-SA-ZariyahNeural", "ar-SA-HamedNeural"],
}

# TTS провайдеры
TTS_PROVIDERS = ["Edge TTS", "Gemini"]
TTS_PROVIDER_MAP = {
    'Edge TTS': 'edge',
    'Gemini': 'gemini'
}
TTS_PROVIDER_BACK_MAP = {
    'edge': 'Edge TTS',
    'gemini': 'Gemini'
}

# Типы анимации (3 типа)
ANIMATION_TYPES = [
    "Микс (случайный)",
    "Панорама влево",
    "Панорама вправо",
    "Зум в центр"
]

# Маппинг типов анимации: отображаемое название -> внутреннее значение
ANIMATION_TYPE_MAP = {
    "Микс (случайный)": "mix",
    "Панорама влево": "pan_left",
    "Панорама вправо": "pan_right",
    "Зум в центр": "zoom_center",
}

# Обратный маппинг: внутреннее значение -> отображаемое название
ANIMATION_TYPE_BACK_MAP = {v: k for k, v in ANIMATION_TYPE_MAP.items()}

# Разрешения видео
RESOLUTIONS = [
    "1080x1920 (FHD Vertical)",
    "720x1280 (HD Vertical)",
    "1920x1080 (FHD Horizontal)",
    "1280x720 (HD Horizontal)",
    "2160x3840 (4K Vertical)",
    "3840x2160 (4K Horizontal)"
]
DEFAULT_RESOLUTION = "1080x1920 (FHD Vertical)"

# FPS
FPS_OPTIONS = ["30 fps", "60 fps", "24 fps"]
DEFAULT_FPS = "60 fps"

# Качество видео
QUALITY_OPTIONS = ["Высокое", "Среднее", "Низкое"]
DEFAULT_QUALITY = "Высокое"

# Позиции субтитров
SUBTITLE_POSITIONS = [
    "Внизу по центру",
    "Вверху по центру",
    "Внизу слева",
    "Внизу справа"
]
DEFAULT_SUBTITLE_POSITION = "Внизу по центру"

# Пути по умолчанию
DEFAULT_OUTPUT_PATH = "generated"
DEFAULT_CONFIG_PATH = "config.json"
DEFAULT_YOUTUBE_CACHE_PATH = "generated/youtube_cache"

# Лимиты
MAX_VIDEOS_PER_BATCH = 100
MAX_SHOT_DURATION = 30.0
MIN_SHOT_DURATION = 0.5
MAX_VIDEO_DURATION = 3 * 60 * 60  # 3 часа — проверенный long-form предел

# P3: Magic strings для FFmpeg кодеков
CODEC_H264_NVENC = 'h264_nvenc'
CODEC_LIBX264 = 'libx264'
PIXEL_FORMAT_YUV420P = 'yuv420p'

# P3: Статусы задач
TASK_STATUS_PENDING = 'pending'
TASK_STATUS_RUNNING = 'running'
TASK_STATUS_COMPLETED = 'completed'
TASK_STATUS_FAILED = 'failed'

# P3: Маппинг позиций субтитров на внутренние значения
SUBTITLE_POSITION_MAP = {
    "Внизу по центру": "bottom_center",
    "Вверху по центру": "top_center",
    "Внизу слева": "bottom_left",
    "Внизу справа": "bottom_right"
}
SUBTITLE_POSITION_BACK_MAP = {v: k for k, v in SUBTITLE_POSITION_MAP.items()}

# P3: Маппинг качества на внутренние значения
QUALITY_MAP = {
    "Высокое": "high",
    "Среднее": "medium",
    "Низкое": "low"
}
QUALITY_BACK_MAP = {v: k for k, v in QUALITY_MAP.items()}
