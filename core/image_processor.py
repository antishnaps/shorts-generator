#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Image processing and AI image generation with intelligent fallback system
V20-AI-FALLBACK: Gemini автоматически переформулирует заблокированные промпты
V21-CACHE: Интеграция с глобальным кэшем изображений
"""

# 🔇 Подавляем предупреждения ДО любых импортов
import warnings
warnings.filterwarnings('ignore', message='.*OpenType support.*')
warnings.filterwarnings('ignore', message='.*python-dotenv.*')

import time
from pathlib import Path
from PIL import Image
from typing import Callable, List, Optional
import random
from .google_image_generator import GoogleImageGenerator
from .smart_visual_prompting import SmartVisualPrompting
from .image_cache import get_image_cache
from .gemini_models import next_image_model

def _sanitize_theme_for_imagen(theme: str) -> str:
    """
    Очищает тему от имён знаменитостей и сложных терминов для Imagen API.
    Заменяет конкретные имена на общие описания.
    
    ВАЖНО: Применяется ТОЛЬКО к темам про кино/актёров, не трогает технические темы!
    """
    import re
    
    # Проверяем, является ли тема кинематографической
    cinema_keywords = ['фильм', 'кино', 'актёр', 'режиссёр', 'комедия', 'драма', 'сериал']
    is_cinema_theme = any(keyword in theme.lower() for keyword in cinema_keywords)
    
    # Если тема НЕ про кино - возвращаем как есть
    if not is_cinema_theme:
        return theme
    
    # Словарь замен ТОЛЬКО для кинематографических тем (case-insensitive)
    replacements = {
        # Люди из кино: обезличиваем роль, а не подставляем конкретную эпоху/нишу
        r'\b(режисс[её]р|director)\s+[A-ZА-ЯЁ][\wА-Яа-яЁё-]+(?:\s+[A-ZА-ЯЁ][\wА-Яа-яЁё-]+){0,2}\b': 'film director',
        r'\b(акт[её]р|актриса|actor|actress)\s+[A-ZА-ЯЁ][\wА-Яа-яЁё-]+(?:\s+[A-ZА-ЯЁ][\wА-Яа-яЁё-]+){0,2}\b': 'film actor',

        # Фильмы (убираем кавычки и названия)
        r'[""«»]([^""«»]+)[""«»]': 'feature film',
        r'фильм[а-я]*\s+[""«»]?[А-Яа-я\s]+[""«»]?': 'feature film',
    }
    
    cleaned = theme
    for pattern, replacement in replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    
    # Убираем двойные пробелы
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    return cleaned

class ImageProcessor:
    """
    Handles all image processing tasks.
    
    Поддерживает:
    - AI генерацию изображений через Google Gemini
    - Fallback на Pixabay для бесплатных изображений (TASK 12)
    - Локальные пользовательские изображения
    """
    
    def __init__(self):
        self.image_generator = GoogleImageGenerator()
        self._pixabay_client = None
        self._pixabay_cache_dir = Path("generated/pixabay_images")
    
    def _create_manual_visual_prompt(self, text: str, safety_level: int = 0) -> str:
        """
        Создаёт визуальный промпт вручную без Gemini (fallback).
        Анализирует тему и создаёт детальное описание.
        """
        text_lower = text.lower()
        safety_suffix = ""
        if safety_level >= 1:
            safety_suffix = (
                ". Keep the scene neutral, non-graphic, policy-safe, documentary-style, "
                "without explicit violence, gore, hate symbols, shocking close-ups, or real-person likeness"
            )
        if safety_level >= 2:
            safety_suffix += (
                ". Use symbolic, distant, non-identifying visuals and avoid sensitive real-world identifiers"
            )
        
        # Базовые шаблоны для разных тем
        if any(word in text_lower for word in ['римск', 'легион', 'рим', 'roman', 'legion']):
            return (
                "Ancient Roman legionary soldiers in battle formation, wearing detailed lorica segmentata "
                "armor with intricate metallic plates and leather straps, weathered red capes flowing in wind, "
                "gladius swords with polished blades, large rectangular scutum shields with golden eagle emblems "
                "and battle damage. Mediterranean landscape background with ancient stone architecture, "
                "dramatic golden hour lighting with volumetric god rays piercing through dust particles, "
                "rim lighting on armor edges creating metallic reflections, atmospheric haze, "
                "cinematic depth of field with bokeh background"
            ) + safety_suffix
        
        elif any(word in text_lower for word in ['викинг', 'скандинав', 'viking', 'norse']):
            return (
                "Viking warriors in dynamic battle stance, wearing detailed chainmail armor with realistic "
                "metallic reflections, weathered round wooden shields bearing intricate Norse symbols and runes, "
                "double-edged axes and swords with battle-worn blades. Dramatic Nordic fjord landscape "
                "with towering mountains, stormy sky with lightning strikes, heavy rain with visible droplets, "
                "atmospheric fog rolling over water, volumetric lighting through storm clouds, "
                "sparks flying from clashing weapons, desaturated color palette with high contrast"
            ) + safety_suffix
        
        elif any(word in text_lower for word in ['самурай', 'япон', 'samurai', 'japan']):
            return (
                "Samurai warrior in traditional armor, detailed lamellar plates with ornate patterns, "
                "katana sword with damascus steel blade showing intricate folding patterns, "
                "menacing kabuto helmet with decorative maedate crest. Japanese landscape with cherry blossoms, "
                "traditional architecture, dramatic sunset with golden and purple sky, "
                "volumetric fog with light rays, atmospheric particles, cinematic composition"
            ) + safety_suffix
        
        elif any(word in text_lower for word in ['средневек', 'рыцар', 'замок', 'medieval', 'knight', 'castle']):
            return (
                "Medieval knight in full plate armor with intricate engravings, weathered metal showing "
                "battle damage and realistic wear, longsword with detailed crossguard, heraldic shield. "
                "Gothic castle background with stone walls and towers, dramatic lighting with torch fire, "
                "atmospheric mist, volumetric light rays through castle windows, "
                "cinematic depth of field, moody color grading"
            ) + safety_suffix
        
        elif any(word in text_lower for word in ['космос', 'космическ', 'space', 'spacecraft', 'sci-fi']):
            return (
                "Futuristic spacecraft in deep space, sleek aerodynamic design with intricate metallic panels, "
                "weathered hull showing space travel wear and micro-meteorite impacts, glowing blue plasma engines "
                "with lens flares and light trails, detailed mechanical components. Epic nebula background "
                "with swirling cosmic dust in purple and teal hues, distant stars with bokeh effect, "
                "volumetric lighting from engine glow, rim lighting on ship edges, atmospheric space dust particles"
            ) + safety_suffix
        
        elif any(word in text_lower for word in ['апартеид', 'apartheid', 'сегрегац', 'segregation', 'расизм', 'racism']):
            return (
                "Powerful historical documentary scene from apartheid era South Africa, "
                "black and white archival photography style, people of different races showing dignity and resilience, "
                "vintage 1950s-1980s aesthetic with authentic period details, "
                "dramatic photojournalistic composition capturing social injustice, "
                "intimate portraits showing human emotion and struggle, "
                "weathered textures and grainy film quality, "
                "chiaroscuro lighting with deep shadows symbolizing oppression, "
                "documentary photography by renowned photojournalists, "
                "historical significance and emotional depth, "
                "shallow depth of field focusing on faces and hands, "
                "atmospheric dust and harsh sunlight, "
                "award-winning documentary photography"
            ) + safety_suffix
        
        # Общий fallback для любой темы
        else:
            # Для неизвестных тем создаём универсальное драматичное описание
            # БЕЗ русского текста - только английские визуальные термины
            return (
                "Dramatic historical scene with emotional depth, "
                "people in period-appropriate clothing showing intense emotions, "
                "vintage documentary photography style from mid-20th century, "
                "black and white archival aesthetic with film grain, "
                "dramatic chiaroscuro lighting with deep shadows, "
                "intimate close-up portraits showing human struggle and resilience, "
                "weathered textures and authentic period details, "
                "photojournalistic composition capturing raw human emotion, "
                "shallow depth of field focusing on faces and hands, "
                "atmospheric haze with dust particles in air, "
                "cinematic storytelling through visual narrative, "
                "award-winning documentary photography"
            ) + safety_suffix

    def _generate_visual_prompt_from_text(self, text: str, text_generator, log_callback, safety_level: int = 0) -> str:
        """
        Генерирует визуальное описание сцены из текста с помощью Gemini.
        Возвращает нейтральное описание без имён и конкретных личностей.
        
        Args:
            text: Исходный текст
            text_generator: Генератор текста с моделью Gemini
            log_callback: Функция логирования
            safety_level: Уровень безопасности (0=нормальный, 1=безопасный, 2=максимально безопасный)
        """
        # Инициализируем Gemini если ещё не инициализирован
        if not text_generator or not text_generator.api_key:
            log_callback("   ❌ ОШИБКА: API ключ не найден для Gemini")
            raise ValueError("Gemini API key required for image prompt generation")
        
        # 🎨 БЫСТРЫЙ РЕЖИМ: используем тему напрямую без цензуры
        # Google AI Studio показывает что Gemini Flash Image нормально работает с любыми темами
        log_callback("   🎨 Быстрый режим: используем оригинальную тему без замен")
        log_callback("   ✅ Тема передается как есть (Google сам справляется с фильтрацией)")
        prompt = _sanitize_theme_for_imagen(text)
        if safety_level >= 1:
            prompt = (
                f"{prompt}. Neutral, non-graphic, policy-safe visual description, "
                "no gore, no explicit violence, no hate symbols, no real-person likeness"
            )
        if safety_level >= 2:
            prompt = (
                f"{prompt}. Prefer symbolic documentary visuals, distant composition, "
                "anonymous subjects, and avoid sensitive real-world identifiers"
            )
        return prompt
    
    def get_pixabay_client(self, api_key: str = None, log_callback=None):
        """
        Получает или создаёт клиент Pixabay.
        
        TASK 12: Pixabay/Freesound Integration
        
        Args:
            api_key: API ключ Pixabay
            log_callback: Функция логирования
            
        Returns:
            PixabayClient или None
        """
        if self._pixabay_client is None and api_key:
            try:
                from core.pixabay_client import PixabayClient
                self._pixabay_client = PixabayClient(
                    api_key=api_key,
                    log_callback=log_callback or print
                )
            except ImportError:
                if log_callback:
                    log_callback("⚠️ PixabayClient не найден")
                return None
        return self._pixabay_client
    
    def get_pixabay_images(
        self,
        query: str,
        count: int,
        output_dir: Path,
        orientation: str = "vertical",
        pixabay_api_key: str = None,
        log_callback=None
    ) -> list:
        """
        Получает изображения с Pixabay как fallback для AI генерации.
        
        TASK 12: Pixabay/Freesound Integration
        
        Args:
            query: Поисковый запрос (тема)
            count: Количество изображений
            output_dir: Папка для сохранения
            orientation: Ориентация (vertical, horizontal, all)
            pixabay_api_key: API ключ Pixabay
            log_callback: Функция логирования
            
        Returns:
            Список путей к скачанным изображениям
        """
        log = log_callback or print
        
        if not pixabay_api_key:
            log("⚠️ Pixabay API ключ не задан")
            return []
        
        client = self.get_pixabay_client(pixabay_api_key, log)
        if not client:
            return []
        
        try:
            log(f"🔍 Поиск изображений на Pixabay: '{query}'...")
            
            # Ищем изображения
            images = client.search_images(
                query=query,
                per_page=count * 2,  # Запрашиваем больше на случай ошибок
                orientation=orientation,
                min_width=1080,
                min_height=1920 if orientation == "vertical" else 1080
            )
            
            if not images:
                log(f"⚠️ Изображения не найдены для '{query}'")
                return []
            
            # Скачиваем
            downloaded = []
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            for img in images[:count]:
                path = client.download_image(img, output_dir, size="largeImageURL")
                if path:
                    downloaded.append(path)
                if len(downloaded) >= count:
                    break
            
            log(f"✅ Скачано {len(downloaded)} изображений с Pixabay")
            return downloaded
            
        except Exception as e:
            log(f"❌ Ошибка Pixabay: {e}")
            return []
    
    def clear_pixabay_cache(self, log_callback=None):
        """Очищает кэш скачанных изображений Pixabay."""
        log = log_callback or print
        
        if self._pixabay_cache_dir.exists():
            import shutil
            try:
                shutil.rmtree(self._pixabay_cache_dir)
                log("🗑️ Кэш Pixabay очищен")
            except Exception as e:
                log(f"⚠️ Ошибка очистки кэша: {e}")
    

    def _generate_image_with_fallback(self, text_segment: str, text_generator, google_ai_api_key: str,
                                     output_path: Path, aspect_ratio: str, width: int, height: int,
                                     variation_index: int, log_callback, strict_theme_following: bool = True,
                                     image_model: str = "gemini-3.1-flash-image",
                                     custom_images_folder: str = None,
                                     use_cache: bool = True,
                                     save_to_cache: bool = True,
                                     topic: str = None,
                                     image_callback = None,
                                     reference_image_paths: list = None) -> Optional[str]:
        """
        Генерирует изображение с автоматическим fallback на безопасные промпты.
        
        Стратегия:
        0. Проверка кэша (если use_cache=True)
        1. Проверка APIKeyManager на исчерпанные модели
        2. Попытка с оригинальным промптом
        3. Если блок → Gemini переформулирует в безопасную версию (уровень 1)
        4. Если снова блок → Gemini создаёт абстрактную версию (уровень 2)
        5. Если всё ещё блок → Generic безопасный промпт
        6. Если всё провалилось → Пользовательские изображения (если указана папка)
        7. Сохранение в кэш (если save_to_cache=True)
        
        Args:
            use_cache: Использовать кэш для поиска похожих изображений
            save_to_cache: Сохранять сгенерированные изображения в кэш
            topic: Тема для организации кэша (original_theme)
            image_callback: Callback для превью изображения в GUI
        """
        
        # 🔑 РОТАЦИЯ API КЛЮЧЕЙ при исчерпании квоты
        try:
            from core.api_key_manager import get_key_manager
            from core.config_manager import ConfigManager
            
            key_manager = get_key_manager()
            config_manager = ConfigManager()
            
            # Получаем все доступные ключи из конфига
            all_keys = config_manager.get_api_keys()  # Возвращает список всех ключей
            
            # Определяем короткое имя модели для APIKeyManager
            model_short = 'imagen_flash' if 'flash' in image_model.lower() else 'imagen_pro'
            
            # Проверяем текущий ключ
            if key_manager.is_exhausted(google_ai_api_key, model_short):
                log_callback(f"⏭️ Текущий ключ исчерпан для {image_model}, ищем другой...")
                
                # Ищем первый доступный ключ
                found_key = None
                for key in all_keys:
                    if not key_manager.is_exhausted(key, model_short):
                        found_key = key
                        break
                
                if found_key:
                    log_callback("🔄 Переключаемся на другой API-ключ")
                    google_ai_api_key = found_key
                else:
                    # Все ключи исчерпаны для текущей модели, пробуем альтернативную
                    log_callback(f"⚠️ Все ключи исчерпаны для {image_model}, пробуем альтернативную модель...")
                    
                    alt_model = next_image_model(image_model)
                    alt_short = (
                        'imagen_flash'
                        if alt_model and 'flash' in alt_model.lower()
                        else 'imagen_pro'
                    )
                    
                    # Ищем ключ для альтернативной модели
                    if alt_model:
                        for key in all_keys:
                            if not key_manager.is_exhausted(key, alt_short):
                                found_key = key
                                break
                    
                    if found_key and alt_model:
                        log_callback(f"🔄 Переключаемся на {alt_model} с другим API-ключом")
                        image_model = alt_model
                        model_short = alt_short
                        google_ai_api_key = found_key
                    else:
                        log_callback("❌ Все ключи и модели исчерпаны, используем кэш/fallback")
        except Exception as e:
            # Если APIKeyManager недоступен, продолжаем без него
            log_callback(f"⚠️ Ротация ключей недоступна: {str(e)[:50]}")
            key_manager = None
            model_short = None
        
        # 💾 ПРОВЕРКА КЭША
        if use_cache:
            cache = get_image_cache(log_callback=log_callback, enabled=True)
            
            # Точное совпадение по промпту
            cached_path = cache.get(text_segment, width, height, topic)
            if cached_path:
                log_callback(f"   💾 Найдено в кэше: {cached_path.name}")
                # Копируем в output_path
                import shutil
                shutil.copy2(cached_path, output_path)
                # 🖼️ Превью из кэша
                if image_callback:
                    image_callback(str(output_path))
                return str(output_path)
            
            # Поиск похожих изображений
            similar = cache.find_similar(text_segment, topic, min_match=3)
            if similar:
                best_match, score = similar[0]
                if score >= 0.5:  # 50%+ совпадение тегов
                    log_callback(f"   💾 Похожее изображение (score={score:.0%}): {best_match.name}")
                    import shutil
                    shutil.copy2(best_match, output_path)
                    # 🖼️ Превью похожего изображения
                    if image_callback:
                        image_callback(str(output_path))
                    return str(output_path)
        
        # 🧠 УМНЫЙ ПРОМПТИНГ
        log_callback("   🧠 Применяем умный промптинг...")
        
        # Определяем вертикальное ли видео
        is_vertical = height > width
        
        # 🔥 СТРОГИЙ vs КРЕАТИВНЫЙ РЕЖИМ
        if strict_theme_following:
            # 🎯 СТРОГИЙ РЕЖИМ: Отключаем метафоры и "умные" интерпретации
            # Берем текст КАК ЕСТЬ, добавляем только технические детали композиции
            log_callback("   🎯 СТРОГИЙ РЕЖИМ: Отключаем SmartVisualPrompting, используем прямой текст")
            enhanced_prompt = text_segment
            detected_mood = "neutral"
            
            # Добавляем только композицию для вертикального видео (без метафор)
            if is_vertical:
                enhanced_prompt += ". Vertical 9:16 composition, centered subject, cinematic framing"
        else:
            # 🎨 КРЕАТИВНЫЙ РЕЖИМ: Используем полную мощь SmartVisualPrompting
            # Система переводит текст в визуальные метафоры и улучшает композицию
            log_callback("   🎨 КРЕАТИВНЫЙ РЕЖИМ: Применяем SmartVisualPrompting с метафорами")
            enhanced_prompt, detected_mood = SmartVisualPrompting.enhance_prompt_with_smart_system(
                text=text_segment,
                theme="",
                is_vertical=is_vertical,
                use_character_anchor=False,
                character_anchor=""
            )
        
        log_callback(f"   🎨 Настроение: {detected_mood}")
        log_callback(f"   📝 Промпт: {enhanced_prompt[:100]}...")
        
        # 🎬 КИНЕМАТОГРАФИЧЕСКИЕ РАКУРСЫ: Расширенная коллекция
        # Используем разнообразные ракурсы для визуального разнообразия
        cinematic_angles = [
            # Классические ракурсы
            "Cinematic wide angle establishing shot, sweeping vista:",
            "Dramatic low angle hero shot, powerful perspective:",
            "High angle bird's eye view, aerial perspective:",
            "Eye level medium shot, natural perspective:",
            "Extreme close-up macro detail shot:",
            
            # Динамические ракурсы
            "Dutch angle tilted composition, dynamic tension:",
            "Over-the-shoulder perspective, intimate viewpoint:",
            "Point of view shot, first person perspective:",
            "Tracking shot with motion blur, dynamic movement:",
            "Crane shot descending perspective, revealing composition:",
            
            # Художественные ракурсы
            "Worm's eye view from ground level, dramatic upward angle:",
            "God's eye view directly overhead, symmetrical composition:",
            "Profile shot side angle, silhouette emphasis:",
            "Three-quarter angle, dimensional depth:",
            "Extreme wide shot, environmental context:",
            
            # Кинематографические техники
            "Shallow depth of field, bokeh background:",
            "Deep focus composition, layered depth:",
            "Rack focus transition, selective sharpness:",
            "Tilt-shift miniature effect, toy-like perspective:",
            "Anamorphic lens flare, cinematic widescreen:",
            
            # Специальные ракурсы
            "Reflection shot in water/mirror, symmetrical duality:",
            "Through window/doorway framing, natural vignette:",
            "Silhouette backlit shot, dramatic contrast:",
            "Golden hour magic hour lighting, warm glow:",
            "Blue hour twilight atmosphere, cool tones:",
            
            # Продвинутые композиции
            "Rule of thirds composition, balanced framing:",
            "Leading lines perspective, guided eye movement:",
            "Symmetrical centered composition, formal balance:",
            "Diagonal composition, dynamic energy:",
            "Frame within frame, layered composition:"
        ]
        
        # 🎯 ВЫБОР РАКУРСА в зависимости от режима
        if not strict_theme_following:
            # КРЕАТИВНЫЙ РЕЖИМ: Используем полный набор ракурсов
            camera_angle = cinematic_angles[variation_index % len(cinematic_angles)]
            enhanced_prompt = f"{camera_angle} {enhanced_prompt}"
            log_callback(f"   🎬 Ракурс камеры: {camera_angle[:60]}...")
        else:
            # СТРОГИЙ РЕЖИМ: Используем базовые ракурсы (без креативных эффектов)
            basic_angles = [
                "Wide angle shot:",
                "Medium shot:",
                "Close-up shot:",
                "Low angle perspective:",
                "High angle perspective:",
                "Eye level shot:",
                "Aerial view:",
                "Detail shot:"
            ]
            camera_angle = basic_angles[variation_index % len(basic_angles)]
            enhanced_prompt = f"{camera_angle} {enhanced_prompt}"
            log_callback(f"   🎬 Базовый ракурс: {camera_angle}")
        
        # 🎥 КИНЕМАТОГРАФИЧЕСКИЕ ДЕТАЛИ для улучшения качества
        cinematic_details = [
            "professional cinematography, film grain texture",
            "cinematic color grading, moody atmosphere",
            "dramatic lighting setup, volumetric rays",
            "shallow depth of field, bokeh background",
            "golden hour lighting, warm color palette",
            "high contrast lighting, deep shadows",
            "soft diffused lighting, ethereal mood",
            "rim lighting, edge highlights"
        ]
        
        # Выбираем детали на основе variation_index
        selected_detail = cinematic_details[variation_index % len(cinematic_details)]
        
        # Финальный промпт с кинематографическими деталями
        final_prompt = (
            f"{enhanced_prompt}. "
            f"{selected_detail}, "
            f"professional photography, high quality, photorealistic. "
            # Чистота изображения
            f"No text, no watermarks, no logos, clean professional image"
        )
        
        # Попытка генерации
        img_path = self.image_generator.generate_image(
            prompt=final_prompt,
            api_key=google_ai_api_key,
            output_path=str(output_path),
            log_callback=log_callback,
            aspect_ratio=aspect_ratio,
            video_width=width,
            video_height=height,
            use_gemini_flash=True,
            variation_index=0,  # Сбрасываем вариации, т.к. промпт уже изменён
            strict_theme_following=strict_theme_following,
            image_model=image_model,
            reference_image_paths=reference_image_paths
        )
        
        if img_path and Path(img_path).exists():
            # 💾 Сохраняем в кэш
            if save_to_cache:
                cache = get_image_cache(log_callback=log_callback, enabled=True)
                cache.put(text_segment, width, height, Path(img_path), topic)
            # 🖼️ Превью после генерации
            if image_callback:
                image_callback(img_path)
            return img_path
        log_callback("   🔄 Попытка 2: Безопасная переформулировка...")
        safe_description = self._generate_visual_prompt_from_text(
            text_segment, text_generator, log_callback, safety_level=1
        )
        log_callback(f"   📝 Безопасное описание: {safe_description[:80]}...")
        
        safe_prompt = (
            f"{safe_description}. "
            f"Professional photography, cinematic lighting, artistic composition, "
            f"high quality, photorealistic. "
            f"Clean image, no text, no watermarks"
        )
        
        output_path_safe = output_path.parent / f"{output_path.stem}_safe{output_path.suffix}"
        img_path = self.image_generator.generate_image(
            prompt=safe_prompt,
            api_key=google_ai_api_key,
            output_path=str(output_path_safe),
            log_callback=log_callback,
            aspect_ratio=aspect_ratio,
            video_width=width,
            video_height=height,
            use_gemini_flash=True,
            variation_index=0,
            strict_theme_following=strict_theme_following,
            image_model=image_model,
            reference_image_paths=reference_image_paths
        )
        
        if img_path and Path(img_path).exists():
            # 💾 Сохраняем в кэш (safe prompt)
            if save_to_cache:
                cache = get_image_cache(log_callback=log_callback, enabled=True)
                cache.put(safe_prompt, width, height, Path(img_path), topic)
            # 🖼️ Превью после генерации (safe)
            if image_callback:
                image_callback(img_path)
            return img_path
        
        # Попытка 3: Generic безопасный промпт (последний шанс) - УСКОРЕНО
        log_callback("   🔄 Попытка 3: Generic безопасный промпт...")
        # 🎯 ТЕМАТИЧЕСКИЙ fallback: используем тему но в безопасной формулировке
        # вместо случайного пейзажа/интерьера — берём тему и оборачиваем в безопасные визуальные шаблоны
        short_topic = text_segment[:60].strip() if text_segment else "business"
        generic_prompts = [
            f"Cinematic close-up of luxury items symbolizing {short_topic}, professional photography, dramatic lighting",
            f"Abstract concept visualization of {short_topic}, modern minimal design, vibrant colors",
            f"Elegant interior representing {short_topic} industry, sophisticated atmosphere, professional photography",
            f"Night cityscape with lights representing {short_topic} business, cinematic, dramatic",
            f"Business and finance concept: {short_topic}, modern office, professional setting"
        ]
        generic_prompt = generic_prompts[variation_index % len(generic_prompts)]
        log_callback(f"   📝 Generic промпт: {generic_prompt[:100]}...")
        
        output_path_generic = output_path.parent / f"{output_path.stem}_generic{output_path.suffix}"
        img_path = self.image_generator.generate_image(
            prompt=generic_prompt,
            api_key=google_ai_api_key,
            output_path=str(output_path_generic),
            log_callback=log_callback,
            aspect_ratio=aspect_ratio,
            video_width=width,
            video_height=height,
            use_gemini_flash=True,
            variation_index=0,
            strict_theme_following=strict_theme_following,
            image_model=image_model,
            reference_image_paths=reference_image_paths
        )
        
        if img_path and Path(img_path).exists():
            # 💾 Сохраняем в кэш (generic prompt)
            if save_to_cache:
                cache = get_image_cache(log_callback=log_callback, enabled=True)
                cache.put(generic_prompt, width, height, Path(img_path), topic)
            # 🖼️ Превью после генерации (generic)
            if image_callback:
                image_callback(img_path)
            return img_path
        
        # Попытка 4: Пользовательские изображения (если указана папка)
        if custom_images_folder and Path(custom_images_folder).is_dir():
            log_callback(f"   🔄 Попытка 4: Пользовательские изображения из {custom_images_folder}")
            try:
                import shutil
                image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
                custom_images = [
                    f for f in Path(custom_images_folder).iterdir()
                    if f.suffix.lower() in image_extensions
                ]
                
                if custom_images:
                    # Выбираем изображение по индексу вариации
                    selected_image = custom_images[variation_index % len(custom_images)]
                    output_path_custom = output_path.parent / f"{output_path.stem}_custom{output_path.suffix}"
                    
                    # Копируем и масштабируем изображение
                    from PIL import Image
                    with Image.open(selected_image) as img:
                        # Масштабируем под нужный размер
                        img_resized = img.resize((width, height), Image.Resampling.LANCZOS)
                        img_resized.save(str(output_path_custom), quality=95)
                    
                    log_callback(f"   ✅ Использовано пользовательское изображение: {selected_image.name}")
                    # 🖼️ Превью пользовательского изображения
                    if image_callback:
                        image_callback(str(output_path_custom))
                    return str(output_path_custom)
                else:
                    log_callback("   ⚠️ Папка пользовательских изображений пуста")
            except Exception as e:
                log_callback(f"   ❌ Ошибка загрузки пользовательского изображения: {str(e)[:80]}")
        
        return None

    def get_images_for_video(self, text_generator, text_parts: list, title: str, num_images: int, output_dir: Path, 
                             use_ai_generation: bool, google_ai_api_key: str, video_settings: dict, 
                             unlimited_images: bool, num_unique_images: int, log_callback, 
                             original_unique_count: int = None, original_theme: str = None, 
                             strict_theme_following: bool = True, enable_scene_variety: bool = True,
                             image_model: str = "gemini-3.1-flash-image",
                             use_triple_template: bool = False,
                             custom_images_folder: str = None,
                             use_only_custom_images: bool = False,
                             use_image_cache: bool = False,
                             save_to_image_cache: bool = False,
                             image_callback = None,
                             use_reference_images: bool = False,
                             reference_images_folder: str = None) -> list:
        """
        Generate or retrieve images for video.
        
        Args:
            use_triple_template: If True, generates images at half height for top-half placement
            custom_images_folder: Path to folder with user images for fallback
            use_image_cache: Check cache before generating new images
            save_to_image_cache: Save generated images to cache for reuse
        
        Returns:
            List of image paths, or empty list if generation failed
        """
        
        
        # Validate inputs
        if num_images <= 0:
            log_callback(f"❌ Некорректное количество изображений: {num_images}")
            return []
            
        # 🖼️ ПРОВЕРКА РЕФЕРЕНСНЫХ ИЗОБРАЖЕНИЙ
        reference_image_paths = None
        if use_reference_images and reference_images_folder and Path(reference_images_folder).is_dir():
            image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'}
            refs = [str(f) for f in Path(reference_images_folder).iterdir() if f.is_file() and f.suffix.lower() in image_extensions]
            if refs:
                reference_image_paths = refs
                log_callback(f"🖼️ Найдено {len(reference_image_paths)} референсных изображений")
        
        
        if not text_parts:
            log_callback("⚠️ Нет текстовых частей для генерации изображений")
            text_parts = [title] if title else ["default scene"]
        
        # Determine number of unique images to generate
        if unlimited_images:
            max_unique_images = num_images
            log_callback(f"🎨 Безлимитный режим: {max_unique_images} уникальных изображений")
        else:
            max_unique_images = min(num_unique_images, num_images)
            log_callback(f"🎨 Ограниченный режим: {max_unique_images} уникальных изображений (для {num_images} кадров)")
        
        image_paths = []
        assets_dir = output_dir / "assets"
        assets_dir.mkdir(exist_ok=True, parents=True)

        # Получаем разрешение из video_settings
        if 'width' in video_settings and 'height' in video_settings:
            width = video_settings['width']
            height = video_settings['height']
        elif 'resolution' in video_settings:
            width, height = video_settings['resolution']
        else:
            # Fallback на стандартное разрешение
            width, height = 1920, 1080
            log_callback(f"⚠️ Разрешение не найдено в video_settings, используем {width}x{height}")
        
        # 🎬 TRIPLE TEMPLATE: Генерируем изображения на половину высоты
        if use_triple_template:
            image_height = height // 2
            log_callback(f"📐 Triple Template: изображения {width}x{image_height} (половина экрана)")
            # Для половины экрана используем широкий формат
            aspect_ratio = "16:9"
        else:
            image_height = height
            aspect_ratio = "16:9" if width > height else "9:16"

        generated_images = []  # Store successfully generated images
        
        # 📁 РЕЖИМ СВОИХ ИЗОБРАЖЕНИЙ
        # Используем свои картинки если:
        # 1. Включен чекбокс "Использовать ТОЛЬКО свои картинки" (use_only_custom_images=True)
        # 2. ИЛИ указана папка и НЕ используется AI (старая логика для совместимости)
        use_custom = (use_only_custom_images or (custom_images_folder and not use_ai_generation))
        
        # Проверка: если включен режим "ТОЛЬКО свои картинки" но папка не указана
        if use_only_custom_images and not custom_images_folder:
            log_callback("❌ ОШИБКА: Включен режим 'ТОЛЬКО свои картинки', но папка не указана!")
            log_callback("   Решение: Укажите папку с изображениями или отключите чекбокс")
            return []  # Останавливаем генерацию
        
        if custom_images_folder and Path(custom_images_folder).is_dir() and use_custom:
            if use_only_custom_images:
                log_callback(f"✅ Режим ТОЛЬКО СВОИ КАРТИНКИ: {custom_images_folder}")
            else:
                log_callback(f"📁 Использование своих изображений из: {custom_images_folder}")
            
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
            
            # 🔍 Рекурсивный поиск изображений (включая подпапки)
            custom_images = []
            root_path = Path(custom_images_folder)
            
            # Сначала ищем в корневой папке
            for f in root_path.iterdir():
                if f.is_file() and f.suffix.lower() in image_extensions:
                    custom_images.append(f)
            
            # Затем ищем в подпапках (рекурсивно)
            for subdir in root_path.iterdir():
                if subdir.is_dir():
                    for f in subdir.rglob('*'):
                        if f.is_file() and f.suffix.lower() in image_extensions:
                            custom_images.append(f)
            
            # Собираем список и перемешиваем его для каждого видео,
            # чтобы в разных роликах были случайные (разные) картинки
            custom_images = sorted(custom_images, key=lambda x: x.name.lower())
            random.shuffle(custom_images)
            
            if not custom_images:
                if use_only_custom_images:
                    # Если включен режим "ТОЛЬКО свои картинки" но их нет - это ошибка!
                    log_callback("❌ ОШИБКА: Включен режим 'ТОЛЬКО свои картинки', но папка пуста!")
                    log_callback(f"   Папка: {custom_images_folder}")
                    log_callback("   Решение: Добавьте изображения в папку или отключите чекбокс")
                    return []  # Останавливаем генерацию
                else:
                    log_callback("⚠️ Папка с изображениями пуста! Переключаемся на AI генерацию...")
                    # Fallback на AI генерацию - продолжаем выполнение ниже
            else:
                # Показываем статистику по папкам
                folders_with_images = set(f.parent for f in custom_images)
                if len(folders_with_images) > 1:
                    log_callback(f"   📷 Найдено {len(custom_images)} изображений в {len(folders_with_images)} папках")
                else:
                    log_callback(f"   📷 Найдено {len(custom_images)} изображений")
                
                # Обрабатываем изображения
                from PIL import Image
                for i in range(max_unique_images):
                    try:
                        # Циклически выбираем изображение
                        source_image = custom_images[i % len(custom_images)]
                        output_path = assets_dir / f"custom_image_{i}_{int(time.time()*1000)}.png"
                        
                        with Image.open(source_image) as img:
                            # Конвертируем в RGB если нужно
                            if img.mode in ('RGBA', 'P'):
                                img = img.convert('RGB')
                            
                            # Масштабируем под нужный размер с сохранением пропорций и обрезкой
                            img_ratio = img.width / img.height
                            target_ratio = width / image_height
                            
                            if img_ratio > target_ratio:
                                # Изображение шире - обрезаем по бокам
                                new_width = int(img.height * target_ratio)
                                left = (img.width - new_width) // 2
                                img = img.crop((left, 0, left + new_width, img.height))
                            else:
                                # Изображение выше - обрезаем сверху/снизу
                                new_height = int(img.width / target_ratio)
                                top = (img.height - new_height) // 2
                                img = img.crop((0, top, img.width, top + new_height))
                            
                            # Масштабируем до нужного размера
                            img_resized = img.resize((width, image_height), Image.Resampling.LANCZOS)
                            img_resized.save(str(output_path), quality=95)
                        
                        generated_images.append(str(output_path))
                        log_callback(f"   ✅ [{i+1}/{max_unique_images}] {source_image.name}")
                        
                        # 🖼️ Отправляем превью
                        if image_callback:
                            image_callback(str(output_path))
                        
                    except Exception as e:
                        log_callback(f"   ❌ Ошибка обработки {source_image.name}: {str(e)[:50]}")
                
                if generated_images:
                    log_callback(f"✅ Обработано {len(generated_images)} изображений из папки")
                    return generated_images
                else:
                    log_callback("⚠️ Не удалось обработать изображения из папки, переключаемся на AI...")
        
        if use_ai_generation and google_ai_api_key:
            log_callback(f"🤖 Генерация {max_unique_images} изображений через AI...")
            
            # 🎬 СИСТЕМА РАЗНООБРАЗИЯ СЦЕН
            if enable_scene_variety:
                log_callback("🎬 Система разнообразия сцен: ВКЛЮЧЕНА")
                log_callback("   💡 Каждое изображение будет иметь уникальную сцену/ракурс")
            
            # Use original unique count if provided, otherwise count now
            if original_unique_count is not None:
                unique_count = original_unique_count
                log_callback(f"📊 Используем оригинальное количество уникальных частей: {unique_count}")
            else:
                # Count unique text parts (remove duplicates to check variety)
                unique_text_parts = list(set(text_parts))
                unique_count = len(unique_text_parts)
            
            # 🚀 ПАРАЛЛЕЛЬНАЯ ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ
            # Если изображений >= 3, используем параллельную генерацию для ускорения
            use_parallel = max_unique_images >= 3
            
            # 📊 Счётчик для прогресса
            images_generated = [0]  # Используем список для мутабельности в closure
            
            if use_parallel:
                log_callback("⚡ ПАРАЛЛЕЛЬНАЯ генерация изображений (ускорение ~3x)")
                
                # 🎯 ВЫБОР БАЗОВОЙ ТЕМЫ в зависимости от режима
                if strict_theme_following:
                    base_theme = original_theme if original_theme else title
                else:
                    if title:
                        base_theme = title
                    else:
                        base_theme = original_theme if original_theme else "cinematic scene"
                
                # 🚀 ОПТИМИЗАЦИЯ: Генерируем ВСЕ сцены ОДНИМ батч-вызовом
                scene_prompts = []
                if enable_scene_variety:
                    from .scene_variety_generator import SceneVarietyGenerator
                    scene_prompts = SceneVarietyGenerator.get_all_variety_prompts_batch(
                        base_theme, max_unique_images, google_ai_api_key
                    )
                    if not scene_prompts:
                        # Fallback: используем базовую тему для всех
                        scene_prompts = [base_theme] * max_unique_images
                else:
                    scene_prompts = [base_theme] * max_unique_images
                
                # Подготовка задач для параллельной генерации
                image_tasks = []
                for i in range(max_unique_images):
                    # Используем pre-generated scene prompt
                    text_segment = f"{base_theme}. {scene_prompts[i]}" if scene_prompts[i] != base_theme else base_theme
                    
                    # Уникальный seed и вариация
                    unique_seed = random.randint(1000, 9999)
                    text_with_seed = f"{text_segment} [variation {unique_seed}]"
                    random_variation = random.randint(0, 14)
                    unique_timestamp = int(time.time() * 1000000) + i  # Уникальность
                    output_path = assets_dir / f"ai_image_{i}_{unique_timestamp}.png"
                    
                    image_tasks.append({
                        'index': i,
                        'text_segment': text_with_seed,
                        'output_path': output_path,
                        'variation_index': random_variation
                    })
                
                # Используем параллельный генератор с увеличенным числом воркеров
                from .parallel_api_generator import ParallelAPIGenerator
                parallel_gen = ParallelAPIGenerator(max_workers=5, log_callback=log_callback)  # Увеличено с 3 до 5
                
                # Функция для генерации одного изображения
                def generate_single_image_task(index, text_segment, output_path, variation_index):
                    img_path = self._generate_image_with_fallback(
                        text_segment=text_segment,
                        text_generator=text_generator,
                        google_ai_api_key=google_ai_api_key,
                        output_path=output_path,
                        aspect_ratio=aspect_ratio,
                        width=width,
                        height=image_height,  # 🎬 Используем image_height для triple template
                        variation_index=variation_index,
                        log_callback=log_callback,
                        strict_theme_following=strict_theme_following,
                        image_model=image_model,
                        custom_images_folder=custom_images_folder,
                        use_cache=use_image_cache,
                        save_to_cache=save_to_image_cache,
                        topic=original_theme,
                        image_callback=image_callback,  # 🖼️ Передаём callback для превью
                        reference_image_paths=reference_image_paths
                    )
                    # 📊 Обновляем счётчик и логируем прогресс
                    images_generated[0] += 1
                    log_callback(f"   📊 Прогресс: {images_generated[0]}/{max_unique_images} изображений")
                    return img_path if img_path and Path(img_path).exists() else None
                
                # Запускаем параллельную генерацию
                results = parallel_gen.batch_api_calls(
                    tasks=image_tasks,
                    task_function=generate_single_image_task,
                    task_name="изображение"
                )
                
                # Собираем успешные результаты
                for result in results:
                    if result:
                        generated_images.append(result)
                        # Превью уже отправлено в _generate_image_with_fallback
                
            else:
                # ПОСЛЕДОВАТЕЛЬНАЯ генерация для малого количества изображений
                log_callback("📝 Последовательная генерация изображений")
                
                for i in range(max_unique_images):
                    log_callback(f"🎨 Изображение {i+1}/{max_unique_images}...")
                    
                    # 🎯 ВЫБОР БАЗОВОЙ ТЕМЫ в зависимости от режима
                    if strict_theme_following:
                        base_theme = original_theme if original_theme else title
                        log_callback(f"   🎯 Строгий режим - оригинальная тема: {base_theme}")
                    else:
                        if title:
                            base_theme = title
                            log_callback(f"   📌 Базовая тема: {title}")
                        else:
                            base_theme = original_theme if original_theme else "cinematic scene"
                            log_callback(f"   📌 Fallback тема: {base_theme}")
                    
                    # 🎬 ДОБАВЛЯЕМ РАЗНООБРАЗИЕ СЦЕН
                    if enable_scene_variety:
                        from .scene_variety_generator import SceneVarietyGenerator
                        scene_variety = SceneVarietyGenerator.get_variety_prompt_ai(
                            base_theme, i, max_unique_images, google_ai_api_key
                        )
                        text_segment = f"{base_theme}. {scene_variety}"
                        log_callback(f"   🎬 AI сцена: {scene_variety[:80]}...")
                    else:
                        text_segment = base_theme
                    
                    try:
                        unique_timestamp = int(time.time() * 1000000)
                        output_path = assets_dir / f"ai_image_{i}_{unique_timestamp}.png"
                        random_variation = random.randint(0, 14)
                        unique_seed = random.randint(1000, 9999)
                        text_with_seed = f"{text_segment} [variation {unique_seed}]"
                        
                        img_path = self._generate_image_with_fallback(
                            text_segment=text_with_seed,
                            text_generator=text_generator,
                            google_ai_api_key=google_ai_api_key,
                            output_path=output_path,
                            aspect_ratio=aspect_ratio,
                            width=width,
                            height=image_height,  # 🎬 Используем image_height для triple template
                            variation_index=random_variation,
                            log_callback=log_callback,
                            strict_theme_following=strict_theme_following,
                            image_model=image_model,
                            custom_images_folder=custom_images_folder,
                            use_cache=use_image_cache,
                            save_to_cache=save_to_image_cache,
                            topic=original_theme,
                            image_callback=image_callback,  # 🖼️ Передаём callback для превью (кэш)
                            reference_image_paths=reference_image_paths
                        )
                        
                        if img_path and Path(img_path).exists():
                            generated_images.append(img_path)
                            log_callback(f"✅ Изображение {i+1}/{max_unique_images} сгенерировано успешно")
                            # Превью уже отправлено в _generate_image_with_fallback
                        else:
                            log_callback(f"❌ Изображение {i+1}/{max_unique_images} не удалось сгенерировать даже с fallback")
                            
                    except Exception as e:
                        log_callback(f"   ❌ Ошибка: {str(e)[:100]}")
                    
                    # Небольшая пауза между изображениями
                    if i < max_unique_images - 1:
                        time.sleep(0.3)
            
            # Check if we got any images
            if not generated_images:
                log_callback("❌ НЕ УДАЛОСЬ СГЕНЕРИРОВАТЬ НИ ОДНОГО ИЗОБРАЖЕНИЯ!")
                log_callback("   Возможные причины:")
                log_callback("   • Safety filter блокирует контент (попробуйте другую тему)")
                log_callback("   • Квота API исчерпана (проверьте лимиты в Google Cloud Console)")
                log_callback("   • Проблемы с API ключом или биллингом")
                log_callback("   • Все fallback модели (Gemini + Imagen) недоступны")
                log_callback("")
                log_callback("💡 Решения:")
                log_callback("   • Попробуйте другую тему (избегайте имена людей, политику)")
                log_callback("   • Проверьте квоты в Google Cloud Console")
                log_callback("   • Убедитесь что биллинг включен (платный API)")
                log_callback("   • Подождите и попробуйте позже")
                log_callback("")
                log_callback("⏭️ ПРОПУСКАЕМ это видео...")
                return []  # Return empty list - video generation will be skipped
            
            log_callback(f"✅ Успешно сгенерировано: {len(generated_images)}/{max_unique_images} изображений")
            
            # 🔧 ФИКС: Не дублируем пути к изображениям!
            # Возвращаем только уникальные изображения, video_renderer сам будет их циклировать
            # Это решает проблему Windows path length limit для длинных видео
            if len(generated_images) < num_images:
                log_callback(f"🔄 Видео требует {num_images} кадров, будет использовано {len(generated_images)} уникальных изображений")
                log_callback("   💡 Video renderer автоматически зациклит изображения")
            
            # Возвращаем только уникальные изображения
            image_paths = generated_images.copy()
                
        else:
            # AI отключён — создаём плейсхолдер. smart_clip_matcher обнаружит "fallback"
            # в имени файла и заменит эти шоты на YouTube клипы.
            if not use_ai_generation:
                log_callback("📹 AI генерация отключена. Шоты заполнит YouTube клипами.")
            else:
                log_callback("⚠️ API ключ не предоставлен. Шоты заполнит YouTube клипами.")

            fallback_path = self._create_solid_color_image(
                width, height, assets_dir, f"fallback_{int(time.time())}.png"
            )
            for i in range(num_images):
                image_paths.append(fallback_path)

        log_callback(f"📊 Итого: {len(image_paths)} изображений (уникальных: {len(set(image_paths))})")
        return image_paths

    def _create_solid_color_image(self, width, height, output_dir, filename, color=(26, 26, 46)):
        """Creates a simple solid color image as a fallback."""
        img = Image.new('RGB', (width, height), color=color)
        filepath = Path(output_dir) / filename
        img.save(str(filepath))
        return str(filepath)

    def _extract_keyword(self, text: str) -> str:
        """A simple keyword extractor (can be improved)."""
        # For now, just use the first few words, cleaned up.
        words = text.split()
        keyword = " ".join(words[:5])
        return "".join(c for c in keyword if c.isalnum() or c.isspace())

    # ============================================================
    # 🖼️ IMAGE POOL GENERATION - Пре-генерация картинок для серий
    # ============================================================
    
    def generate_image_pool(
        self,
        theme: str,
        num_images: int,
        api_key: str,
        output_dir: Path,
        video_settings: dict,
        log_callback: Callable,
        image_model: str = "gemini-3.1-flash-image",
        strict_theme_following: bool = True,
        progress_callback: Callable = None,
        image_callback: Callable = None
    ) -> List[str]:
        """
        Генерирует пул изображений для серии видео.
        
        Экономит API вызовы: вместо генерации картинок для каждого видео,
        генерируем один раз и переиспользуем.
        
        Args:
            theme: Тема для генерации изображений
            num_images: Количество изображений в пуле
            api_key: Google AI API ключ
            output_dir: Папка для сохранения (generated/image_pools/{theme_hash}/)
            video_settings: Настройки видео (width, height)
            log_callback: Функция логирования
            image_model: Модель для генерации
            strict_theme_following: Строгое следование теме
            progress_callback: Callback для прогресса (0-100)
            image_callback: Callback для превью изображений
            
        Returns:
            Список путей к сгенерированным изображениям
        """
        import hashlib
        
        log_callback(f"\n{'='*60}")
        log_callback("🖼️ ГЕНЕРАЦИЯ ПУЛА ИЗОБРАЖЕНИЙ")
        log_callback(f"{'='*60}")
        log_callback(f"📌 Тема: {theme}")
        log_callback(f"📊 Количество: {num_images}")
        
        # Создаём папку пула
        theme_hash = hashlib.md5(theme.lower().encode()).hexdigest()[:12]
        pool_dir = output_dir / "image_pools" / theme_hash
        pool_dir.mkdir(parents=True, exist_ok=True)
        
        # Сохраняем метаданные пула
        metadata_path = pool_dir / "pool_metadata.json"
        import json
        metadata = {
            'theme': theme,
            'theme_hash': theme_hash,
            'num_images': num_images,
            'image_model': image_model,
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'video_width': video_settings.get('width', 1080),
            'video_height': video_settings.get('height', 1920),
        }
        
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        
        log_callback(f"📁 Папка пула: image_pools/{theme_hash}/")
        
        width = video_settings.get('width', 1080)
        height = video_settings.get('height', 1920)
        aspect_ratio = "16:9" if width > height else "9:16"
        
        # Генерируем разнообразные промпты для темы
        log_callback(f"🧠 Генерация {num_images} уникальных промптов...")
        
        prompts = self._generate_diverse_prompts(
            theme=theme,
            num_prompts=num_images,
            api_key=api_key,
            log_callback=log_callback
        )
        
        if not prompts:
            log_callback("❌ Не удалось сгенерировать промпты")
            return []
        
        log_callback(f"✅ Сгенерировано {len(prompts)} промптов")
        
        # Генерируем изображения
        generated_paths = []
        cache = get_image_cache(log_callback=log_callback, enabled=True)
        
        for i, prompt in enumerate(prompts):
            if progress_callback:
                progress = int((i / num_images) * 100)
                progress_callback(progress)
            
            log_callback(f"\n🎨 [{i+1}/{num_images}] Генерация...")
            log_callback(f"   📝 Промпт: {prompt[:80]}...")
            
            output_path = pool_dir / f"pool_image_{i:04d}.png"
            
            # Проверяем кэш
            cached = cache.get(prompt, width, height, theme)
            if cached:
                log_callback("   💾 Из кэша!")
                # Копируем в пул
                import shutil
                shutil.copy2(cached, output_path)
                generated_paths.append(str(output_path))
                if image_callback:
                    image_callback(str(output_path))
                continue
            
            # Генерируем новое изображение
            try:
                img_path = self.image_generator.generate_image(
                    prompt=prompt,
                    api_key=api_key,
                    output_path=str(output_path),
                    log_callback=log_callback,
                    aspect_ratio=aspect_ratio,
                    video_width=width,
                    video_height=height,
                    use_gemini_flash=True,
                    variation_index=i,
                    strict_theme_following=strict_theme_following,
                    image_model=image_model
                )
                
                if img_path and Path(img_path).exists():
                    generated_paths.append(img_path)
                    
                    # Сохраняем в кэш
                    cache.put(prompt, width, height, Path(img_path), theme)
                    
                    if image_callback:
                        image_callback(img_path)
                    
                    log_callback(f"   ✅ Сохранено: {Path(img_path).name}")
                else:
                    log_callback("   ⚠️ Не удалось сгенерировать")
                    
            except Exception as e:
                log_callback(f"   ❌ Ошибка: {str(e)[:80]}")
            
            # Небольшая пауза между запросами
            time.sleep(0.5)
        
        if progress_callback:
            progress_callback(100)
        
        log_callback(f"\n{'='*60}")
        log_callback(f"✅ ПУЛА ГОТОВ: {len(generated_paths)}/{num_images} изображений")
        log_callback(f"📁 Путь: {pool_dir}")
        log_callback(f"{'='*60}\n")
        
        return generated_paths
    
    def _generate_diverse_prompts(
        self,
        theme: str,
        num_prompts: int,
        api_key: str,
        log_callback: Callable
    ) -> List[str]:
        """
        Генерирует разнообразные промпты для темы через Gemini.
        
        Создаёт промпты с разными:
        - Ракурсами (крупный план, общий план, с высоты)
        - Освещением (дневное, закат, ночь)
        - Стилями (реалистичный, кинематографичный, драматичный)
        """
        try:
            from google import genai
            from google.genai import types
            
            prompt = f"""Generate {num_prompts} unique, diverse image prompts for the topic: "{theme}"

REQUIREMENTS:
1. Each prompt must be DIFFERENT - vary angles, lighting, composition, mood
2. Include variety: close-ups, wide shots, aerial views, dramatic angles
3. Vary lighting: daylight, sunset, night, dramatic shadows, soft light
4. Vary styles: photorealistic, cinematic, documentary, artistic
5. Each prompt should be 2-3 sentences, highly detailed
6. Focus on VISUAL elements that would make compelling video backgrounds
7. NO text, logos, or watermarks in the images
8. Prompts should be in ENGLISH for best AI image generation

Example variety for "Ancient Rome":
- "Dramatic wide shot of the Roman Colosseum at sunset, golden light casting long shadows, crowds of spectators in togas, dust particles in the air, cinematic composition"
- "Close-up of a Roman legionary's face, weathered bronze helmet with red plume, intense eyes, battle scars, shallow depth of field, dramatic side lighting"
- "Aerial view of ancient Roman forum, marble columns and temples, busy marketplace below, morning mist, photorealistic detail"

Return ONLY a JSON array of {num_prompts} prompt strings, no explanation:
["prompt1", "prompt2", ...]"""

            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.9,  # Высокая температура для разнообразия
                    max_output_tokens=4000,
                )
            )
            
            text = response.text.strip()
            
            # Убираем markdown если есть
            if text.startswith('```'):
                text = text.split('```')[1]
                if text.startswith('json'):
                    text = text[4:]
            
            import json
            prompts = json.loads(text)
            
            if isinstance(prompts, list) and prompts:
                return prompts[:num_prompts]
                
        except Exception as e:
            log_callback(f"⚠️ AI генерация промптов не удалась: {e}")
        
        # Fallback: простые промпты
        log_callback("🔄 Используем fallback промпты")
        fallback_prompts = []
        
        styles = [
            "photorealistic, highly detailed, 8K resolution",
            "cinematic, dramatic lighting, movie still",
            "documentary style, natural lighting",
            "artistic, vibrant colors, dynamic composition",
            "moody atmosphere, dramatic shadows",
        ]
        
        angles = [
            "wide establishing shot",
            "close-up detail shot",
            "medium shot",
            "aerial view from above",
            "low angle dramatic shot",
        ]
        
        for i in range(num_prompts):
            style = styles[i % len(styles)]
            angle = angles[i % len(angles)]
            fallback_prompts.append(f"{theme}, {angle}, {style}")
        
        return fallback_prompts
    
    @staticmethod
    def get_available_pools(output_dir: Path) -> List[dict]:
        """
        Возвращает список доступных пулов изображений.
        
        Returns:
            Список словарей с информацией о пулах:
            [{'theme': str, 'hash': str, 'num_images': int, 'path': Path}, ...]
        """
        pools = []
        pools_dir = output_dir / "image_pools"
        
        if not pools_dir.exists():
            return pools
        
        import json
        
        for pool_dir in pools_dir.iterdir():
            if not pool_dir.is_dir():
                continue
            
            metadata_path = pool_dir / "pool_metadata.json"
            if metadata_path.exists():
                try:
                    with open(metadata_path, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                    
                    # Считаем реальное количество изображений
                    actual_images = len(list(pool_dir.glob("pool_image_*.png")))
                    
                    pools.append({
                        'theme': metadata.get('theme', 'Unknown'),
                        'hash': metadata.get('theme_hash', pool_dir.name),
                        'num_images': actual_images,
                        'created_at': metadata.get('created_at', ''),
                        'path': pool_dir,
                    })
                except Exception:
                    # Пул без метаданных
                    actual_images = len(list(pool_dir.glob("pool_image_*.png")))
                    if actual_images > 0:
                        pools.append({
                            'theme': pool_dir.name,
                            'hash': pool_dir.name,
                            'num_images': actual_images,
                            'created_at': '',
                            'path': pool_dir,
                        })
        
        # Сортируем по дате создания (новые первые)
        pools.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return pools
    
    @staticmethod
    def get_images_from_pool(pool_path: Path, num_images: int, exclude: List[str] = None) -> List[str]:
        """
        Получает изображения из пула без повторов.
        
        Args:
            pool_path: Путь к папке пула
            num_images: Сколько изображений нужно
            exclude: Список путей для исключения (уже использованные)
            
        Returns:
            Список путей к изображениям
        """
        if not pool_path.exists():
            return []
        
        exclude = exclude or []
        exclude_set = set(exclude)
        
        # Получаем все изображения из пула
        all_images = [str(p) for p in pool_path.glob("pool_image_*.png")]
        
        # Фильтруем уже использованные
        available = [img for img in all_images if img not in exclude_set]
        
        if not available:
            # Если все использованы - берём заново (с перемешиванием)
            available = all_images.copy()
        
        # Перемешиваем для рандомности
        random.shuffle(available)
        
        # Возвращаем нужное количество (с повторами если не хватает)
        result = []
        while len(result) < num_images:
            for img in available:
                result.append(img)
                if len(result) >= num_images:
                    break
            if not available:
                break
        
        return result[:num_images]
