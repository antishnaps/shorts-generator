#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🌍 СИСТЕМА ПЕРЕВОДОВ ДЛЯ ГЕНЕРАТОРА
Все строки на языке выбранном в GUI
"""

from typing import Dict

# Переводы для всех поддерживаемых языков
TRANSLATIONS: Dict[str, Dict[str, str]] = {
    'Russian': {
        # Названия глав
        'chapter': 'Глава',
        'part': 'Часть',
        'continuation': 'Продолжение темы',
        'aspect': 'Аспект',
        'detailed_story': 'Подробный рассказ о',
        
        # Промпты генерации
        'write_engaging_text': 'Напиши захватывающий текст про',
        'for_video': 'для видео',
        'volume': 'Объём',
        'words': 'слов',
        'seconds_narration': 'секунд озвучки',
        'style_instruction': 'СТИЛЬ: Жёлто-таблоидный, эмоциональный, короткие предложения.',
        'write_only_text': 'Напиши ТОЛЬКО текст, без JSON, без заголовков',
        
        # JSON формат
        'return_json': 'Верни JSON',
        'chapter_text': 'Текст главы',
        'minimum_words': 'МИНИМУМ',
        
        # Ошибки и статусы
        'empty_response': 'Пустой ответ от API',
        'empty_text': 'Пустой текст в ответе',
        'broken_json': 'Битый JSON, пробуем починить',
        'retry_attempt': 'Попытка',
        'simple_mode': 'простой режим',
        'not_enough_words': 'Недостаточно слов',
    },
    'English': {
        'chapter': 'Chapter',
        'part': 'Part',
        'continuation': 'Continuation of',
        'aspect': 'Aspect',
        'detailed_story': 'Detailed story about',
        
        'write_engaging_text': 'Write engaging text about',
        'for_video': 'for a video',
        'volume': 'Length',
        'words': 'words',
        'seconds_narration': 'seconds of narration',
        'style_instruction': 'STYLE: Tabloid, emotional, short sentences.',
        'write_only_text': 'Write ONLY the text, no JSON, no headers',
        
        'return_json': 'Return JSON',
        'chapter_text': 'Chapter text',
        'minimum_words': 'MINIMUM',
        
        'empty_response': 'Empty API response',
        'empty_text': 'Empty text in response',
        'broken_json': 'Broken JSON, trying to fix',
        'retry_attempt': 'Attempt',
        'simple_mode': 'simple mode',
        'not_enough_words': 'Not enough words',
    },
    'Spanish': {
        'chapter': 'Capítulo',
        'part': 'Parte',
        'continuation': 'Continuación de',
        'aspect': 'Aspecto',
        'detailed_story': 'Historia detallada sobre',
        
        'write_engaging_text': 'Escribe un texto atractivo sobre',
        'for_video': 'para un video',
        'volume': 'Longitud',
        'words': 'palabras',
        'seconds_narration': 'segundos de narración',
        'style_instruction': 'ESTILO: Tabloide, emocional, frases cortas.',
        'write_only_text': 'Escribe SOLO el texto, sin JSON, sin encabezados',
        
        'return_json': 'Devuelve JSON',
        'chapter_text': 'Texto del capítulo',
        'minimum_words': 'MÍNIMO',
        
        'empty_response': 'Respuesta vacía de la API',
        'empty_text': 'Texto vacío en la respuesta',
        'broken_json': 'JSON roto, intentando arreglar',
        'retry_attempt': 'Intento',
        'simple_mode': 'modo simple',
        'not_enough_words': 'No hay suficientes palabras',
    },
    'French': {
        'chapter': 'Chapitre',
        'part': 'Partie',
        'continuation': 'Suite de',
        'aspect': 'Aspect',
        'detailed_story': 'Histoire détaillée sur',
        
        'write_engaging_text': 'Écris un texte captivant sur',
        'for_video': 'pour une vidéo',
        'volume': 'Longueur',
        'words': 'mots',
        'seconds_narration': 'secondes de narration',
        'style_instruction': 'STYLE: Tabloïd, émotionnel, phrases courtes.',
        'write_only_text': 'Écris UNIQUEMENT le texte, sans JSON, sans en-têtes',
        
        'return_json': 'Retourne JSON',
        'chapter_text': 'Texte du chapitre',
        'minimum_words': 'MINIMUM',
        
        'empty_response': 'Réponse API vide',
        'empty_text': 'Texte vide dans la réponse',
        'broken_json': 'JSON cassé, tentative de réparation',
        'retry_attempt': 'Tentative',
        'simple_mode': 'mode simple',
        'not_enough_words': 'Pas assez de mots',
    },
    'German': {
        'chapter': 'Kapitel',
        'part': 'Teil',
        'continuation': 'Fortsetzung von',
        'aspect': 'Aspekt',
        'detailed_story': 'Ausführliche Geschichte über',
        
        'write_engaging_text': 'Schreibe einen fesselnden Text über',
        'for_video': 'für ein Video',
        'volume': 'Länge',
        'words': 'Wörter',
        'seconds_narration': 'Sekunden Erzählung',
        'style_instruction': 'STIL: Boulevard, emotional, kurze Sätze.',
        'write_only_text': 'Schreibe NUR den Text, kein JSON, keine Überschriften',
        
        'return_json': 'Gib JSON zurück',
        'chapter_text': 'Kapiteltext',
        'minimum_words': 'MINIMUM',
        
        'empty_response': 'Leere API-Antwort',
        'empty_text': 'Leerer Text in der Antwort',
        'broken_json': 'Defektes JSON, versuche zu reparieren',
        'retry_attempt': 'Versuch',
        'simple_mode': 'einfacher Modus',
        'not_enough_words': 'Nicht genug Wörter',
    },
    'Chinese': {
        'chapter': '章节',
        'part': '部分',
        'continuation': '继续',
        'aspect': '方面',
        'detailed_story': '详细故事关于',
        
        'write_engaging_text': '写一段关于以下主题的引人入胜的文字',
        'for_video': '用于视频',
        'volume': '长度',
        'words': '字',
        'seconds_narration': '秒旁白',
        'style_instruction': '风格：小报式、情感化、短句。',
        'write_only_text': '只写文字，不要JSON，不要标题',
        
        'return_json': '返回JSON',
        'chapter_text': '章节文字',
        'minimum_words': '最少',
        
        'empty_response': 'API响应为空',
        'empty_text': '响应中文字为空',
        'broken_json': 'JSON损坏，尝试修复',
        'retry_attempt': '尝试',
        'simple_mode': '简单模式',
        'not_enough_words': '字数不够',
    },
    'Japanese': {
        'chapter': '章',
        'part': 'パート',
        'continuation': '続き',
        'aspect': '側面',
        'detailed_story': '詳細なストーリー',
        
        'write_engaging_text': '以下のトピックについて魅力的なテキストを書いてください',
        'for_video': '動画用',
        'volume': '長さ',
        'words': '語',
        'seconds_narration': '秒のナレーション',
        'style_instruction': 'スタイル：タブロイド、感情的、短い文。',
        'write_only_text': 'テキストのみを書いてください、JSONなし、見出しなし',
        
        'return_json': 'JSONを返す',
        'chapter_text': '章のテキスト',
        'minimum_words': '最低',
        
        'empty_response': 'APIレスポンスが空です',
        'empty_text': 'レスポンスのテキストが空です',
        'broken_json': 'JSONが壊れています、修復を試みます',
        'retry_attempt': '試行',
        'simple_mode': 'シンプルモード',
        'not_enough_words': '語数が足りません',
    },
    'Korean': {
        'chapter': '장',
        'part': '파트',
        'continuation': '계속',
        'aspect': '측면',
        'detailed_story': '상세한 이야기',
        
        'write_engaging_text': '다음 주제에 대해 매력적인 텍스트를 작성하세요',
        'for_video': '영상용',
        'volume': '길이',
        'words': '단어',
        'seconds_narration': '초 내레이션',
        'style_instruction': '스타일: 타블로이드, 감정적, 짧은 문장.',
        'write_only_text': '텍스트만 작성하세요, JSON 없이, 제목 없이',
        
        'return_json': 'JSON 반환',
        'chapter_text': '장 텍스트',
        'minimum_words': '최소',
        
        'empty_response': 'API 응답이 비어 있습니다',
        'empty_text': '응답의 텍스트가 비어 있습니다',
        'broken_json': 'JSON이 손상되었습니다, 수정 시도 중',
        'retry_attempt': '시도',
        'simple_mode': '간단 모드',
        'not_enough_words': '단어가 부족합니다',
    },
    'Portuguese': {
        'chapter': 'Capítulo',
        'part': 'Parte',
        'continuation': 'Continuação de',
        'aspect': 'Aspecto',
        'detailed_story': 'História detalhada sobre',
        
        'write_engaging_text': 'Escreva um texto envolvente sobre',
        'for_video': 'para um vídeo',
        'volume': 'Comprimento',
        'words': 'palavras',
        'seconds_narration': 'segundos de narração',
        'style_instruction': 'ESTILO: Tabloide, emocional, frases curtas.',
        'write_only_text': 'Escreva APENAS o texto, sem JSON, sem cabeçalhos',
        
        'return_json': 'Retorne JSON',
        'chapter_text': 'Texto do capítulo',
        'minimum_words': 'MÍNIMO',
        
        'empty_response': 'Resposta da API vazia',
        'empty_text': 'Texto vazio na resposta',
        'broken_json': 'JSON quebrado, tentando consertar',
        'retry_attempt': 'Tentativa',
        'simple_mode': 'modo simples',
        'not_enough_words': 'Palavras insuficientes',
    },
    'Italian': {
        'chapter': 'Capitolo',
        'part': 'Parte',
        'continuation': 'Continuazione di',
        'aspect': 'Aspetto',
        'detailed_story': 'Racconto dettagliato su',
        'write_engaging_text': 'Scrivi un testo coinvolgente su',
        'for_video': 'per un video',
        'volume': 'Lunghezza',
        'words': 'parole',
        'seconds_narration': 'secondi di narrazione',
        'style_instruction': 'STILE: Giornalistico, coinvolgente, frasi brevi.',
        'write_only_text': 'Scrivi SOLO il testo, senza JSON e senza intestazioni',
        'return_json': 'Restituisci JSON',
        'chapter_text': 'Testo del capitolo',
        'minimum_words': 'MINIMO',
        'empty_response': 'Risposta API vuota',
        'empty_text': 'Testo vuoto nella risposta',
        'broken_json': 'JSON non valido, tentativo di correzione',
        'retry_attempt': 'Tentativo',
        'simple_mode': 'modalità semplice',
        'not_enough_words': 'Parole insufficienti',
    },
    'Hindi': {
        'chapter': 'अध्याय',
        'part': 'भाग',
        'continuation': 'इस विषय की अगली कड़ी',
        'aspect': 'पहलू',
        'detailed_story': 'विस्तृत विवरण',
        'write_engaging_text': 'इस विषय पर रोचक पाठ लिखें',
        'for_video': 'वीडियो के लिए',
        'volume': 'लंबाई',
        'words': 'शब्द',
        'seconds_narration': 'सेकंड की आवाज़',
        'style_instruction': 'शैली: स्पष्ट, रोचक और छोटे वाक्य।',
        'write_only_text': 'केवल पाठ लिखें, JSON या शीर्षक नहीं',
        'return_json': 'JSON लौटाएँ',
        'chapter_text': 'अध्याय का पाठ',
        'minimum_words': 'न्यूनतम',
        'empty_response': 'API से खाली उत्तर',
        'empty_text': 'उत्तर में पाठ खाली है',
        'broken_json': 'JSON अमान्य है, सुधारने का प्रयास',
        'retry_attempt': 'प्रयास',
        'simple_mode': 'सरल मोड',
        'not_enough_words': 'शब्द पर्याप्त नहीं हैं',
    },
    'Arabic': {
        'chapter': 'الفصل',
        'part': 'الجزء',
        'continuation': 'متابعة موضوع',
        'aspect': 'الجانب',
        'detailed_story': 'شرح مفصل عن',
        'write_engaging_text': 'اكتب نصًا جذابًا عن',
        'for_video': 'لفيديو',
        'volume': 'الطول',
        'words': 'كلمة',
        'seconds_narration': 'ثانية من السرد',
        'style_instruction': 'الأسلوب: واضح وجذاب بجمل قصيرة.',
        'write_only_text': 'اكتب النص فقط، من دون JSON أو عناوين',
        'return_json': 'أعد JSON',
        'chapter_text': 'نص الفصل',
        'minimum_words': 'الحد الأدنى',
        'empty_response': 'استجابة API فارغة',
        'empty_text': 'لا يوجد نص في الاستجابة',
        'broken_json': 'JSON غير صالح، جارٍ إصلاحه',
        'retry_attempt': 'محاولة',
        'simple_mode': 'الوضع البسيط',
        'not_enough_words': 'عدد الكلمات غير كافٍ',
    },
}


def t(key: str, language: str = 'Russian') -> str:
    """
    Получить перевод по ключу.
    
    Args:
        key: Ключ перевода
        language: Язык (Russian, English, Spanish, etc.)
        
    Returns:
        Переведённая строка или ключ если перевод не найден
    """
    lang_dict = TRANSLATIONS.get(language, TRANSLATIONS.get('English', {}))
    return lang_dict.get(key, TRANSLATIONS['English'].get(key, key))


def get_language_instruction(language: str) -> str:
    """
    Возвращает инструкцию для AI писать на определённом языке.
    """
    instructions = {
        'Russian': 'Write ALL content in Russian (русский язык). Use only Cyrillic script.',
        'English': 'Write ALL content in English.',
        'Spanish': 'Write ALL content in Spanish (Español).',
        'French': 'Write ALL content in French (Français).',
        'German': 'Write ALL content in German (Deutsch).',
        'Chinese': 'Write ALL content in Chinese (中文). Use Simplified Chinese characters.',
        'Japanese': 'Write ALL content in Japanese (日本語). Use appropriate mix of Hiragana, Katakana and Kanji.',
        'Korean': 'Write ALL content in Korean (한국어). Use Hangul script.',
        'Portuguese': 'Write ALL content in Portuguese (Português).',
        'Italian': 'Write ALL content in Italian (Italiano).',
        'Hindi': 'Write ALL content in Hindi (हिन्दी). Use Devanagari script.',
        'Arabic': 'Write ALL content in Arabic (العربية). Use Arabic script and natural right-to-left punctuation.',
    }
    return instructions.get(language, f'Write ALL content in {language}.')
