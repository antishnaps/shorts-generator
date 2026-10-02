#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Scene Variety Generator - создает разнообразные сцены для одной темы
Пример: "борщ" → девушка ест борщ, тарелка борща, кастрюля в детсаду, и т.д.
"""

import warnings
warnings.filterwarnings('ignore', category=FutureWarning, module='google')

import random
import re
from typing import List, Dict

class SceneVarietyGenerator:
    """Генерирует разнообразные сцены для одной темы"""
    
    # 🎬 ТИПЫ СЦЕН ДЛЯ ВОЕННОЙ/ИСТОРИЧЕСКОЙ ТЕМАТИКИ
    MILITARY_SCENE_TYPES = [
        {
            "type": "hero_portrait",
            "description": "Dramatic close-up portrait of soldier in {subject}",
            "focus": "Face, emotion, determination, heroism",
            "examples": "Soldier's face, eyes, expression, uniform details"
        },
        {
            "type": "action_combat",
            "description": "Dynamic action shot of soldiers in combat during {subject}",
            "focus": "Movement, action, intensity, battle",
            "examples": "Running, shooting, advancing, defending"
        },
        {
            "type": "equipment_detail",
            "description": "Close-up of military equipment and weapons in {subject}",
            "focus": "Weapons, gear, uniform details, authenticity",
            "examples": "Rifle, helmet, medals, insignia"
        },
        {
            "type": "battlefield_wide",
            "description": "Epic wide shot of battlefield during {subject}",
            "focus": "Scale, environment, atmosphere, drama",
            "examples": "Trenches, ruins, landscape, smoke"
        },
        {
            "type": "memorial_monument",
            "description": "Solemn shot of memorial or monument related to {subject}",
            "focus": "Respect, memory, honor, history",
            "examples": "Monument, eternal flame, memorial wall"
        },
        {
            "type": "historical_document",
            "description": "Close-up of historical documents or photos from {subject}",
            "focus": "Authenticity, history, documentation",
            "examples": "Orders, letters, photographs, medals"
        },
        {
            "type": "group_soldiers",
            "description": "Group shot of soldiers together during {subject}",
            "focus": "Brotherhood, unity, camaraderie",
            "examples": "Squad, unit, comrades, team"
        },
        {
            "type": "aftermath_scene",
            "description": "Powerful aftermath scene showing impact of {subject}",
            "focus": "Consequences, sacrifice, victory, loss",
            "examples": "Victory banner, ruins, aftermath"
        }
    ]

    HISTORICAL_SCENE_TYPES = [
        {
            "type": "period_portrait",
            "description": "Period-accurate portrait scene for {subject}",
            "focus": "Era-specific clothing, faces, everyday details",
            "examples": "Portrait, gesture, clothing, authentic environment"
        },
        {
            "type": "artifact_detail",
            "description": "Close-up of era-specific artifact related to {subject}",
            "focus": "Objects, materials, texture, provenance",
            "examples": "Tool, document, map, object, photo"
        },
        {
            "type": "location_context",
            "description": "Wide establishing shot of the place and era around {subject}",
            "focus": "Architecture, environment, time period",
            "examples": "Street, workshop, home, public place, landscape"
        },
        {
            "type": "daily_life",
            "description": "Everyday-life scene showing how {subject} existed in its time",
            "focus": "Human context, routine, believable period details",
            "examples": "Work, travel, conversation, preparation"
        },
        {
            "type": "turning_point",
            "description": "Key moment or consequence connected with {subject}",
            "focus": "Action, decision, impact, aftermath",
            "examples": "Discovery, meeting, invention, public reaction"
        },
    ]
    
    # 🎬 ТИПЫ СЦЕН (универсальные для любой темы)
    SCENE_TYPES = [
        # КРУПНЫЕ ПЛАНЫ (Close-ups)
        {
            "type": "extreme_closeup",
            "description": "Extreme close-up macro shot of {subject}",
            "focus": "Microscopic details, texture, material properties",
            "examples": "Surface texture, droplets, steam, ingredients"
        },
        {
            "type": "detail_shot",
            "description": "Tight detail shot focusing on {subject}",
            "focus": "Specific element in sharp focus",
            "examples": "Spoon in bowl, hand holding, garnish detail"
        },
        
        # СРЕДНИЕ ПЛАНЫ (Medium shots)
        {
            "type": "table_setting",
            "description": "Medium shot of {subject} on table with context",
            "focus": "Object in environment, surrounding elements",
            "examples": "Plate on table, utensils, napkin, background"
        },
        {
            "type": "preparation",
            "description": "Medium shot of {subject} being prepared",
            "focus": "Process, hands in action, ingredients",
            "examples": "Cooking, mixing, pouring, cutting"
        },
        
        # ШИРОКИЕ ПЛАНЫ (Wide shots)
        {
            "type": "environment",
            "description": "Wide establishing shot of {subject} in environment",
            "focus": "Full context, location, atmosphere",
            "examples": "Kitchen, restaurant, home, outdoor setting"
        },
        {
            "type": "serving",
            "description": "Wide shot of {subject} being served",
            "focus": "Service moment, presentation, people",
            "examples": "Waiter serving, family dinner, buffet"
        },
        
        # ДЕЙСТВИЯ (Actions)
        {
            "type": "eating",
            "description": "Person eating/tasting {subject}",
            "focus": "Human interaction, emotion, enjoyment",
            "examples": "Taking bite, tasting, savoring, reaction"
        },
        {
            "type": "cooking",
            "description": "Chef/person cooking {subject}",
            "focus": "Culinary process, skill, technique",
            "examples": "Stirring pot, adding ingredients, checking taste"
        },
        
        # КОНТЕКСТНЫЕ (Contextual)
        {
            "type": "ingredients",
            "description": "Ingredients for {subject} arranged artistically",
            "focus": "Raw materials, colors, composition",
            "examples": "Fresh vegetables, spices, herbs laid out"
        },
        {
            "type": "cultural",
            "description": "{subject} in cultural/traditional context",
            "focus": "Heritage, tradition, authenticity",
            "examples": "Traditional setting, cultural elements, heritage"
        }
    ]
    
    # 👥 ПЕРСОНАЖИ (для разнообразия)
    CHARACTERS = [
        "young woman",
        "elderly grandmother",
        "professional chef",
        "happy child",
        "family member",
        "restaurant customer",
        "home cook",
        "food blogger"
    ]
    
    # 📍 ЛОКАЦИИ (для разнообразия)
    LOCATIONS = [
        "modern kitchen",
        "rustic home kitchen",
        "professional restaurant kitchen",
        "cozy dining room",
        "outdoor picnic setting",
        "traditional family table",
        "school cafeteria",
        "street food stall"
    ]
    
    # 🎨 СТИЛИ ПОДАЧИ (для разнообразия)
    PRESENTATION_STYLES = [
        "elegant plating",
        "rustic homestyle",
        "modern minimalist",
        "traditional authentic",
        "street food casual",
        "fine dining luxury",
        "family-style generous",
        "artistic gourmet"
    ]
    
    # 🎯 KEYWORD-TO-VISUAL MAPPING (для умной синхронизации с текстом)
    KEYWORD_TO_VISUAL = {
        # ВОЕННАЯ/БОЕВАЯ ТЕМАТИКА
        r'топор|оружие|меч|удар|винтовка|автомат': 'extreme closeup of weapon in action, dramatic angle, sharp details',
        r'враг|солдат|бой|война|сражение|атак': 'wide battlefield shot, epic scale, dramatic lighting, smoke and atmosphere',
        r'кровь|ранен|смерть|погиб|жертв': 'dark moody atmosphere, high contrast, somber lighting, serious tone',
        r'победа|триумф|слав': 'low angle triumphant shot, golden hour, inspiring',
        r'танк|техника|т-34|пушка|орудие': 'military vehicle closeup, powerful angle, metal details, combat ready',
        r'командир|офицер|генерал|полковник': 'portrait shot, stern expression, military bearing, authority',
        
        # ЭМОЦИОНАЛЬНЫЕ МОМЕНТЫ
        r'невозможн|шокирующ|страшн|ужасн': 'intense dramatic lighting, extreme perspective, shocking composition',
        r'опасн|рискован|критическ': 'tension-building angle, dark shadows, dramatic atmosphere',
        r'храбр|смел|отважн|мужеств': 'heroic low angle, inspiring lighting, powerful composition',
        
        # ДЕЙСТВИЯ
        r'бежал|бег|побежал|рванул': 'dynamic motion blur, action shot, speed lines, fast movement',
        r'прыгнул|прыжок|скочил': 'freeze frame mid-air, dynamic angle, captured motion',
        r'взрыв|детонация|бомба': 'explosion aftermath, debris flying, dramatic impact, fire and smoke',
        
        # ЛОКАЦИИ
        r'окоп|траншея|укрытие': 'trench perspective, muddy details, defensive position',
        r'город|здан|руин|развалин': 'urban warfare setting, destroyed buildings, rubble',
        r'поле|лес|природа': 'outdoor battlefield, natural cover, terrain',
        
        # ВРЕМЕННЫЕ МАРКЕРЫ
        r'ночь|темнот|сумерки': 'night scene, low key lighting, dramatic shadows',
        r'утро|рассвет|заря': 'golden hour lighting, soft warm tones, hopeful atmosphere',
        r'зима|мороз|снег': 'winter landscape, cold blue tones, harsh conditions'
    }
    
    @staticmethod
    def enhance_prompt_with_keywords(base_prompt: str, text_segment: str) -> str:
        """
        Улучшает промпт на основе ключевых слов из текста
        
        Args:
            base_prompt: Базовый промпт для изображения
            text_segment: Сегмент текста (для анализа ключевых слов)
            
        Returns:
            Улучшенный промпт с визуальными модификаторами
        """
        if not text_segment:
            return base_prompt
        
        import re
        
        # Анализируем текст на наличие ключевых слов
        enhancements = []
        for pattern, visual_modifier in SceneVarietyGenerator.KEYWORD_TO_VISUAL.items():
            if re.search(pattern, text_segment, re.IGNORECASE):
                enhancements.append(visual_modifier)
        
        # Если нашли совпадения - добавляем модификаторы
        if enhancements:
            # Берем первые 2 наиболее релевантных
            selected_enhancements = enhancements[:2]
            enhanced_prompt = f"{base_prompt}, {', '.join(selected_enhancements)}"
            return enhanced_prompt
        
        return base_prompt
    
    @staticmethod
    def generate_scene_variations(theme: str, num_variations: int = 5) -> List[Dict[str, str]]:
        """
        Генерирует разнообразные сцены для одной темы
        
        Args:
            theme: Основная тема (например "борщ", "пицца", "кофе")
            num_variations: Количество вариаций
            
        Returns:
            List of scene descriptions with variety
        """
        variations = []
        used_types = set()
        
        # Определяем категорию темы для умного подбора сцен
        category = SceneVarietyGenerator._detect_category(theme)
        
        # Выбираем набор сцен в зависимости от категории
        if category == 'military_historical':
            scene_pool = SceneVarietyGenerator.MILITARY_SCENE_TYPES
        elif category == 'historical':
            scene_pool = SceneVarietyGenerator.HISTORICAL_SCENE_TYPES
        else:
            scene_pool = SceneVarietyGenerator.SCENE_TYPES
        
        for i in range(num_variations):
            # Выбираем тип сцены (избегаем повторений)
            available_types = [st for st in scene_pool 
                             if st["type"] not in used_types]
            
            if not available_types:
                # Если все типы использованы - сбрасываем
                used_types.clear()
                available_types = scene_pool
            
            scene_type = random.choice(available_types)
            used_types.add(scene_type["type"])
            
            # Генерируем описание сцены
            scene_desc = SceneVarietyGenerator._generate_scene_description(
                theme, scene_type, category, i
            )
            
            variations.append({
                "scene_number": i + 1,
                "scene_type": scene_type["type"],
                "description": scene_desc,
                "focus": scene_type["focus"]
            })
        
        return variations
    
    @staticmethod
    def _detect_category(theme: str) -> str:
        """Определяет категорию темы для умного подбора сцен"""
        theme_lower = theme.lower()
        
        # ПРИОРИТЕТ 1: Военная тематика
        military_keywords = ['солдат', 'война', 'битва', 'сражение', 'танк', 'оружие',
                            'крепость', 'фронт', 'армия', 'военн',
                            'soldier', 'war', 'battle', 'tank', 'weapon', 'fortress']
        if any(kw in theme_lower for kw in military_keywords):
            return 'military_historical'

        # ПРИОРИТЕТ 2: Историческая тематика без автоматического ухода в войну
        historical_keywords = ['истори', 'древн', 'эпох', 'век', 'архив', 'history', 'historical', 'ancient', 'century', 'era']
        has_year = bool(re.search(r'\b(?:1[0-9]{3}|20[0-2][0-9])\b', theme_lower))
        if has_year or any(kw in theme_lower for kw in historical_keywords):
            return 'historical'
        
        # ПРИОРИТЕТ 2: Еда и напитки
        food_keywords = ['борщ', 'суп', 'пицца', 'паста', 'салат', 'мясо', 'рыба', 
                        'десерт', 'торт', 'хлеб', 'каша', 'блюдо', 'еда', 'food']
        drink_keywords = ['кофе', 'чай', 'сок', 'вода', 'напиток', 'коктейль', 'вино',
                         'coffee', 'tea', 'drink']
        
        if any(kw in theme_lower for kw in food_keywords):
            return 'food'
        elif any(kw in theme_lower for kw in drink_keywords):
            return 'drink'
        
        # ПРИОРИТЕТ 3: Технологии/гаджеты
        tech_keywords = ['iphone', 'android', 'компьютер', 'ноутбук', 'телефон', 'гаджет',
                        'программ', 'код', 'software', 'hardware', 'tech']
        if any(kw in theme_lower for kw in tech_keywords):
            return 'technology'
        
        # По умолчанию
        return 'general'
    
    @staticmethod
    def _generate_scene_description(theme: str, scene_type: Dict, category: str, index: int) -> str:
        """Генерирует детальное описание сцены"""
        
        # Базовое описание из типа сцены
        base_desc = scene_type["description"].format(subject=theme)
        
        # Для военной/исторической тематики - возвращаем базовое описание
        if category in {'military_historical', 'historical'}:
            return base_desc
        
        # Добавляем контекстные детали в зависимости от типа сцены (для еды/напитков)
        if scene_type["type"] == "eating":
            character = random.choice(SceneVarietyGenerator.CHARACTERS)
            return f"{character} enjoying {theme}, {base_desc}"
            
        elif scene_type["type"] == "cooking":
            location = random.choice([loc for loc in SceneVarietyGenerator.LOCATIONS 
                                     if 'kitchen' in loc])
            return f"{base_desc} in {location}"
            
        elif scene_type["type"] == "table_setting":
            style = random.choice(SceneVarietyGenerator.PRESENTATION_STYLES)
            return f"{theme} with {style}, {base_desc}"
            
        elif scene_type["type"] == "environment":
            location = random.choice(SceneVarietyGenerator.LOCATIONS)
            return f"{base_desc} in {location}"
            
        elif scene_type["type"] == "serving":
            location = random.choice([loc for loc in SceneVarietyGenerator.LOCATIONS 
                                     if any(word in loc for word in ['restaurant', 'dining', 'cafeteria'])])
            return f"{base_desc} in {location}"
            
        else:
            # Для остальных типов - базовое описание
            return base_desc
    
    @staticmethod
    def get_all_variety_prompts_batch(theme: str, total_variations: int, api_key: str) -> list:
        """
        🚀 ОПТИМИЗАЦИЯ: Генерирует ВСЕ разнообразные промпты ОДНИМ батч-вызовом
        
        Args:
            theme: Основная тема
            total_variations: Количество вариаций
            api_key: API ключ Gemini
            
        Returns:
            Список промптов для каждой вариации
        """
        try:
            import requests
            
            # Промпт для AI: генерация ВСЕХ сцен сразу
            ai_prompt = f"""You are a professional cinematographer creating diverse visual scenes for video.

THEME: "{theme}"

TASK: Generate {total_variations} DIFFERENT visual scene descriptions for this theme.
Each scene must be UNIQUE and show the theme from a DIFFERENT perspective/angle/context.

REQUIREMENTS:
1. Each scene must be VISUALLY DIFFERENT from others
2. Mix of shot types: extreme close-ups, medium shots, wide shots, low angles, high angles
3. Mix of contexts: action, static, detail, environment, people, objects
4. Stay TRUE to the theme - all scenes must be about "{theme}"
5. Use cinematic language: camera angles, lighting, composition

FORMAT: Output EXACTLY {total_variations} scene descriptions, one per line, numbered.
Example format:
Scene 1: [description]
Scene 2: [description]
Scene 3: [description]

NOW generate ALL {total_variations} scenes for "{theme}":"""

            # REST API call
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": ai_prompt}]}],
                "generationConfig": {
                    "temperature": 0.9,  # Высокая креативность для разнообразия
                    "topP": 0.95,
                    "topK": 64,
                    "maxOutputTokens": 500  # Больше токенов для всех сцен
                }
            }
            
            response = requests.post(url, json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                if text:
                    # Парсим ответ: ищем строки вида "Scene N: ..."
                    import re
                    lines = text.strip().split('\n')
                    scene_prompts = []
                    
                    for line in lines:
                        # Ищем паттерн "Scene N:" или просто номер
                        match = re.search(r'(?:Scene\s+\d+:|^\d+[.:])\s*(.+)', line, re.IGNORECASE)
                        if match:
                            scene_desc = match.group(1).strip()
                            if scene_desc:
                                scene_prompts.append(scene_desc)
                    
                    # Если получили нужное количество - возвращаем
                    if len(scene_prompts) >= total_variations:
                        return scene_prompts[:total_variations]
                    
                    # Если получили хоть что-то - дополняем fallback'ом
                    if scene_prompts:
                        while len(scene_prompts) < total_variations:
                            idx = len(scene_prompts)
                            fallback = SceneVarietyGenerator._get_variety_prompt_fallback(theme, idx, total_variations)
                            scene_prompts.append(fallback)
                        return scene_prompts
            
            # Fallback: генерируем все через старую систему
            return [SceneVarietyGenerator._get_variety_prompt_fallback(theme, i, total_variations) 
                    for i in range(total_variations)]
                
        except Exception:
            # Fallback: генерируем все через старую систему
            return [SceneVarietyGenerator._get_variety_prompt_fallback(theme, i, total_variations) 
                    for i in range(total_variations)]
    
    @staticmethod
    def get_variety_prompt_ai(theme: str, variation_index: int, total_variations: int, api_key: str) -> str:
        """
        Генерирует разнообразные промпты через AI (Gemini)
        
        Args:
            theme: Основная тема (например "борщ", "лазерная резка металла")
            variation_index: Индекс текущей вариации (0-based)
            total_variations: Общее количество вариаций
            api_key: API ключ Gemini
            
        Returns:
            AI-generated diverse prompt
        """
        try:
            import requests
            
            # Промпт для AI: генерация разнообразных сцен
            ai_prompt = f"""You are a professional cinematographer creating diverse visual scenes for video.

THEME: "{theme}"

TASK: Generate {total_variations} DIFFERENT visual scene descriptions for this theme.
Each scene must be UNIQUE and show the theme from a DIFFERENT perspective/angle/context.

CURRENT SCENE: #{variation_index + 1} of {total_variations}

REQUIREMENTS:
1. Each scene must be VISUALLY DIFFERENT from others
2. Mix of shot types: extreme close-ups, medium shots, wide shots
3. Mix of contexts: action, static, detail, environment, people
4. Stay TRUE to the theme - all scenes must be about "{theme}"
5. Use cinematic language: camera angles, lighting, composition

EXAMPLES for "coffee":
Scene 1: Extreme close-up of coffee beans with steam rising
Scene 2: Barista pouring latte art, medium shot from side
Scene 3: Woman enjoying coffee in cozy cafe, wide shot
Scene 4: Coffee cup on wooden table with morning light, overhead shot
Scene 5: Espresso machine in action, dynamic close-up

EXAMPLES for "factory quality control":
Scene 1: Extreme close-up of a precision gauge touching a finished part
Scene 2: Wide shot of an operator checking parts near a production line
Scene 3: Overhead shot of rejected and approved samples on a workbench
Scene 4: Medium shot of a technician reviewing measurements on a monitor
Scene 5: Detail shot of engraved serial numbers under inspection light

NOW generate Scene #{variation_index + 1} for "{theme}":
- Make it VISUALLY DISTINCT from other scenes
- Use specific camera angles and shot types
- Include lighting and mood
- Keep it concise (1-2 sentences)

Output ONLY the scene description, no explanations:"""

            # REST API call
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": ai_prompt}]}],
                "generationConfig": {
                    "temperature": 0.9,  # Высокая креативность для разнообразия
                    "topP": 0.95,
                    "topK": 64,
                    "maxOutputTokens": 200
                }
            }
            
            response = requests.post(url, json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                if text:
                    return text.strip()
            
            # Fallback на старую систему
            return SceneVarietyGenerator._get_variety_prompt_fallback(theme, variation_index, total_variations)
                
        except Exception:
            # Fallback на старую систему при ошибке
            return SceneVarietyGenerator._get_variety_prompt_fallback(theme, variation_index, total_variations)
    
    @staticmethod
    def _get_variety_prompt_fallback(theme: str, variation_index: int, total_variations: int) -> str:
        """Fallback метод если AI не работает"""
        variations = SceneVarietyGenerator.generate_scene_variations(theme, total_variations)
        current_variation = variations[variation_index % len(variations)]
        
        prompt = f"{current_variation['description']}. "
        prompt += f"Focus on: {current_variation['focus']}. "
        
        return prompt
    
    @staticmethod
    def get_variety_prompt(theme: str, variation_index: int, total_variations: int) -> str:
        """
        Генерирует промпт с разнообразием для конкретной вариации
        (Старый метод для обратной совместимости)
        """
        return SceneVarietyGenerator._get_variety_prompt_fallback(theme, variation_index, total_variations)


# Пример использования:
if __name__ == "__main__":
    # Тест: генерация вариаций для "борщ"
    theme = "борщ"
    variations = SceneVarietyGenerator.generate_scene_variations(theme, 8)
    
    print(f"🎬 РАЗНООБРАЗИЕ СЦЕН ДЛЯ ТЕМЫ: '{theme}'")
    print("=" * 60)
    
    for var in variations:
        print(f"\n{var['scene_number']}. {var['scene_type'].upper()}")
        print(f"   📝 {var['description']}")
        print(f"   🎯 Focus: {var['focus']}")
