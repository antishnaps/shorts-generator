#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🔍 Custom Text Analyzer
Анализирует кастомные тексты и определяет общую тему для выжигания на первом шоте
"""

import logging
from typing import List, Optional


def analyze_custom_texts_theme(
    custom_texts: List,
    api_key: str,
    main_theme: str = "",
    log_callback=None
) -> str:
    """
    Анализирует все кастомные тексты и определяет общую тему.
    
    Использует Gemini для анализа содержимого всех текстов и определения
    общей темы, которая будет выжигаться на первом шоте.
    
    Args:
        custom_texts: Список объектов CustomText с полем .text
        api_key: API ключ Gemini
        main_theme: Основная тема из GUI (опционально, для контекста)
        log_callback: Функция логирования
        
    Returns:
        str: Общая тема (например "Кексы", "Собаки", "Рецепты")
             Если анализ не удался - возвращает main_theme
    """
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            logging.info(msg)
    
    if not custom_texts:
        log("⚠️ Нет текстов для анализа, используем тему из GUI")
        return main_theme or "Видео"
    
    if not api_key:
        log("⚠️ Нет API ключа для анализа, используем тему из GUI")
        return main_theme or "Видео"
    
    try:
        log(f"🔍 Анализ {len(custom_texts)} кастомных текстов для определения общей темы...")
        
        # Собираем все тексты (ограничиваем каждый до 500 символов для экономии токенов)
        texts_sample = []
        for i, ct in enumerate(custom_texts[:10], 1):  # Максимум 10 текстов для анализа
            text_preview = ct.text[:500] if hasattr(ct, 'text') else str(ct)[:500]
            texts_sample.append(f"Текст {i}: {text_preview}...")
        
        combined_texts = "\n\n".join(texts_sample)
        
        # Формируем промпт для Gemini
        prompt = f"""Проанализируй эти тексты и определи ОДНУ общую тему (1-3 слова максимум).

Тексты:
{combined_texts}

Контекст из GUI: {main_theme if main_theme else 'не указан'}

Верни ТОЛЬКО название темы (без кавычек, без точек, без объяснений).
Примеры хороших ответов: "Кексы", "Собаки", "Рецепты десертов", "Путешествия"

Тема:"""
        
        # Вызываем Gemini
        from core.gemini_client import GeminiClient
        client = GeminiClient(api_key)
        
        response = client.generate_text(
            prompt,
            temperature=0.3,  # Низкая температура для стабильности
            max_tokens=200    # Увеличено для русского языка (было 100)
        )
        
        if response.success and response.raw_text:
            theme = response.raw_text.strip(' \n\r"\'*.')
            
            # Валидация: тема не должна быть слишком длинной
            if len(theme) > 50:
                log(f"⚠️ Тема слишком длинная ({len(theme)} символов), обрезаем")
                theme = theme[:50]
            
            # Валидация: тема не должна быть пустой
            if not theme or len(theme) < 2:
                log("⚠️ Тема слишком короткая, используем тему из GUI")
                return main_theme or "Видео"
            
            log(f"✅ Определена общая тема: '{theme}'")
            return theme
        else:
            error_msg = response.error if hasattr(response, 'error') else "Пустой ответ"
            log(f"⚠️ Gemini не смог определить тему: {error_msg}")
            return main_theme or "Видео"
            
    except Exception as e:
        log(f"❌ Ошибка анализа текстов: {e}")
        return main_theme or "Видео"


def should_analyze_custom_texts(
    custom_texts: List,
    video_num: int,
    cached_theme: Optional[str] = None
) -> bool:
    """
    Определяет нужно ли анализировать тексты.
    
    Args:
        custom_texts: Список кастомных текстов
        video_num: Номер текущего видео
        cached_theme: Кэшированная тема (если уже анализировали)
        
    Returns:
        bool: True если нужно анализировать, False если использовать кэш
    """
    # Если нет текстов - не анализируем
    if not custom_texts:
        return False
    
    # Если уже есть кэш - не анализируем повторно
    if cached_theme:
        return False
    
    # Анализируем только для первого видео
    return video_num == 1
