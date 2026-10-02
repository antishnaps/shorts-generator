#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🧠 Smart Visual Prompting System
Интеллектуальная система генерации визуальных промптов для Imagen
"""

import re
from typing import Tuple


class SmartVisualPrompting:
    """Умная система промптинга для идеального визуала"""
    
    @staticmethod
    def translate_abstract_to_concrete(text: str) -> str:
        """
        🧠 VISUAL TRANSLATION LAYER
        
        Переводит абстрактные концепции в конкретные физические объекты.
        
        Примеры:
        - "Свобода" → "Разорванная цепь на пыльной земле"
        - "Время уходит" → "Песочные часы с последними падающими песчинками"
        - "Успех" → "Мозолистые руки, капающий пот, горящая лампа"
        """
        
        # Словарь абстракций → конкретика
        translations = {
            # Эмоции
            r'\bсвобод[аы]?\b': 'broken chain lying on dusty ground, open prison door with light streaming through',
            r'\bfreedom\b': 'broken chain lying on dusty ground, open prison door with light streaming through',
            
            r'\bстрах[а]?\b': 'trembling hands gripping edge, wide fearful eyes in darkness, cold sweat droplets',
            r'\bfear\b': 'trembling hands gripping edge, wide fearful eyes in darkness, cold sweat droplets',
            
            r'\bлюбов[ьи]?\b': 'intertwined hands with wedding rings, warm embrace silhouette at sunset',
            r'\blove\b': 'intertwined hands with wedding rings, warm embrace silhouette at sunset',
            
            # Время
            r'\bвремя\s+уходит\b': 'hourglass with last grains of sand falling, dramatic lighting on glass',
            r'\btime\s+running\s+out\b': 'hourglass with last grains of sand falling, dramatic lighting on glass',
            
            r'\bпрошлое\b': 'old sepia photograph with torn edges, vintage pocket watch stopped at specific time',
            r'\bpast\b': 'old sepia photograph with torn edges, vintage pocket watch stopped at specific time',
            
            # Успех/Неудача
            r'\bуспех[а]?\b': 'calloused hands with dirt under nails, sweat dripping on desk, burning lamp in night office',
            r'\bsuccess\b': 'calloused hands with dirt under nails, sweat dripping on desk, burning lamp in night office',
            
            r'\bпоражени[ея]\b': 'broken trophy lying in dust, torn medal on ground, empty podium',
            r'\bfailure\b': 'broken trophy lying in dust, torn medal on ground, empty podium',
            
            # Абстрактные концепции
            r'\bнадежд[аы]?\b': 'single green sprout breaking through cracked concrete, morning dew on leaves',
            r'\bhope\b': 'single green sprout breaking through cracked concrete, morning dew on leaves',
            
            r'\bодиночеств[оа]\b': 'single chair in empty room, long shadow on wall, dust particles in light beam',
            r'\bloneliness\b': 'single chair in empty room, long shadow on wall, dust particles in light beam',
        }
        
        result = text
        for pattern, replacement in translations.items():
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        
        return result
    
    @staticmethod
    def detect_mood(text: str) -> str:
        """
        🎨 MOOD DETECTION
        
        Определяет эмоциональное настроение текста.
        """
        text_lower = text.lower()
        
        # Ужас/Триллер
        if any(word in text_lower for word in ['страх', 'ужас', 'кошмар', 'темнота', 'смерть', 'horror', 'fear', 'nightmare', 'death']):
            return 'horror'
        
        # Радость/Счастье
        if any(word in text_lower for word in ['радость', 'счастье', 'веселье', 'праздник', 'happy', 'joy', 'celebration']):
            return 'happy'
        
        # Технологии/Будущее
        if any(word in text_lower for word in ['технолог', 'будущее', 'киберпанк', 'робот', 'tech', 'future', 'cyberpunk', 'robot']):
            return 'tech'
        
        # История/Ностальгия
        if any(word in text_lower for word in ['история', 'прошлое', 'винтаж', 'ретро', 'history', 'vintage', 'retro', 'past']):
            return 'history'
        
        # Драма/Грусть
        if any(word in text_lower for word in ['грусть', 'печаль', 'драма', 'потеря', 'sad', 'drama', 'loss', 'melancholy']):
            return 'drama'
        
        # Экшн/Энергия
        if any(word in text_lower for word in ['битва', 'война', 'бой', 'экшн', 'battle', 'war', 'fight', 'action']):
            return 'action'
        
        # По умолчанию - нейтральное
        return 'neutral'
    
    @staticmethod
    def get_color_grading(mood: str) -> str:
        """
        🎨 DYNAMIC COLOR PALETTE
        
        Возвращает цветовую палитру (Color Grading) под настроение.
        """
        palettes = {
            'horror': (
                "Bleach Bypass color grading, cold greenish desaturated tones, "
                "high contrast with deep shadows, eerie atmospheric lighting, "
                "muted colors with sickly green and blue undertones"
            ),
            'happy': (
                "Vibrant saturated warm colors, golden hour lighting, "
                "soft glowing highlights, cheerful bright palette, "
                "orange and yellow dominant tones, high key lighting"
            ),
            'tech': (
                "Neon purple and cyan color palette, dark background with high gloss, "
                "cyberpunk aesthetic with electric blue accents, "
                "slick metallic surfaces with specular highlights, "
                "futuristic color grading with magenta and teal"
            ),
            'history': (
                "Muted earth tones with sepia undertones, vintage Kodak film aesthetic, "
                "warm nostalgic color palette, film grain texture, "
                "faded colors like old photographs, soft diffused lighting"
            ),
            'drama': (
                "Moody chiaroscuro lighting, deep shadows with low key lighting, "
                "desaturated colors with blue-grey tones, "
                "dramatic contrast between light and dark, "
                "melancholic color palette with muted blues and greys"
            ),
            'action': (
                "High contrast orange and teal color grading, "
                "dynamic lighting with strong highlights, "
                "saturated colors with cinematic blockbuster look, "
                "warm orange skin tones against cool blue backgrounds"
            ),
            'neutral': (
                "Natural cinematic color grading, balanced warm and cool tones, "
                "realistic lighting with subtle color correction, "
                "professional film look with moderate saturation"
            )
        }
        
        return palettes.get(mood, palettes['neutral'])
    
    @staticmethod
    def create_character_anchor(theme: str, text: str) -> str:
        """
        ⚓ CHARACTER CONSISTENCY ANCHOR
        
        Создает "паспорт персонажа" для консистентности в серии.
        """
        text_lower = text.lower()
        theme_lower = theme.lower()
        
        # Определяем тип персонажа
        if any(word in text_lower + theme_lower for word in ['солдат', 'воин', 'военн', 'soldier', 'warrior', 'military']):
            return (
                "Young battle-hardened soldier, 25-30 years old, weathered face with dirt and scars, "
                "short military haircut, intense determined eyes, wearing faded combat uniform with patches, "
                "visible stubble, strong jawline, realistic skin texture with pores and imperfections"
            )
        
        elif any(word in text_lower + theme_lower for word in ['учёный', 'профессор', 'исследователь', 'scientist', 'professor', 'researcher']):
            return (
                "Middle-aged scientist, 40-50 years old, intelligent thoughtful expression, "
                "wire-rimmed glasses, slightly disheveled grey hair, wearing white lab coat, "
                "tired eyes from long hours, subtle wrinkles showing wisdom, "
                "realistic skin with age spots and fine lines"
            )
        
        elif any(word in text_lower + theme_lower for word in ['девушка', 'женщина', 'героиня', 'woman', 'girl', 'heroine']):
            return (
                "Young woman, 25-30 years old, determined expression, "
                "natural beauty with minimal makeup, realistic skin texture, "
                "expressive eyes showing emotion, practical hairstyle, "
                "wearing functional clothing appropriate to context"
            )
        
        elif any(word in text_lower + theme_lower for word in ['старик', 'пожилой', 'дед', 'old man', 'elderly', 'grandfather']):
            return (
                "Elderly man, 65-75 years old, weathered wise face with deep wrinkles, "
                "grey beard and hair, kind but tired eyes, "
                "age spots and liver spots on skin, realistic aging details, "
                "wearing simple worn clothing"
            )
        
        # Нейтральный персонаж
        return (
            "Adult person, 30-40 years old, neutral expression, "
            "realistic facial features with natural skin texture, "
            "appropriate clothing for the context, "
            "photorealistic human details"
        )
    
    @staticmethod
    def get_vertical_composition_rules() -> str:
        """
        📐 VERTICAL COMPOSITION (9:16 Safety Zone)
        
        Правила композиции для вертикального видео с учетом UI элементов.
        """
        return (
            "CRITICAL COMPOSITION FOR VERTICAL VIDEO (9:16 aspect ratio): "
            "Main subject MUST be centered in the UPPER MIDDLE part of the frame. "
            "Leave the BOTTOM 30% of the image EMPTY or with blurred background (negative space for text overlays). "
            "Leave the RIGHT EDGE clear (for interface buttons and likes). "
            "Eye level or focal point should be at the top 1/3 line (rule of thirds). "
            "Avoid placing important details in bottom third or right edge. "
            "Use vertical composition with subject filling upper 2/3 of frame."
        )
    
    @staticmethod
    def enhance_prompt_with_smart_system(
        text: str,
        theme: str = "",
        is_vertical: bool = True,
        use_character_anchor: bool = False,
        character_anchor: str = ""
    ) -> Tuple[str, str]:
        """
        🚀 ГЛАВНАЯ ФУНКЦИЯ
        
        Применяет все 4 технологии умного промптинга.
        
        Returns:
            (enhanced_prompt, mood) - улучшенный промпт и определенное настроение
        """
        
        # 1. Visual Translation Layer - переводим абстракции в конкретику
        concrete_text = SmartVisualPrompting.translate_abstract_to_concrete(text)
        
        # 2. Mood Detection & Color Grading
        mood = SmartVisualPrompting.detect_mood(text + " " + theme)
        color_grading = SmartVisualPrompting.get_color_grading(mood)
        
        # 3. Character Anchor (если нужен)
        if use_character_anchor:
            if not character_anchor:
                character_anchor = SmartVisualPrompting.create_character_anchor(theme, text)
            prompt_parts = [f"Close-up of {character_anchor}", concrete_text]
        else:
            prompt_parts = [concrete_text]
        
        # 4. Vertical Composition (для вертикальных видео)
        if is_vertical:
            composition_rules = SmartVisualPrompting.get_vertical_composition_rules()
            prompt_parts.append(composition_rules)
        
        # Добавляем color grading
        prompt_parts.append(color_grading)
        
        # Добавляем технические детали
        prompt_parts.append(
            "Shot on RED Komodo 6K camera with Zeiss Master Prime lens, "
            "cinematic depth of field with bokeh background, "
            "professional color grading, film grain texture, "
            "photorealistic rendering with ray tracing"
        )
        
        # Собираем финальный промпт
        enhanced_prompt = ". ".join(prompt_parts)
        
        return enhanced_prompt, mood


# Пример использования:
if __name__ == "__main__":
    # Тест 1: Абстрактная концепция
    text1 = "Успех требует жертв"
    enhanced1, mood1 = SmartVisualPrompting.enhance_prompt_with_smart_system(
        text1,
        theme="Мотивация",
        is_vertical=True
    )
    print(f"Оригинал: {text1}")
    print(f"Настроение: {mood1}")
    print(f"Улучшенный: {enhanced1[:200]}...")
    print()
    
    # Тест 2: С персонажем
    text2 = "Солдат смотрит на поле боя"
    enhanced2, mood2 = SmartVisualPrompting.enhance_prompt_with_smart_system(
        text2,
        theme="Война",
        is_vertical=True,
        use_character_anchor=True
    )
    print(f"Оригинал: {text2}")
    print(f"Настроение: {mood2}")
    print(f"Улучшенный: {enhanced2[:200]}...")
