#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔄 СИСТЕМА РЕКАПОВ ДЛЯ ДЛИННЫХ ВИДЕО
Автоматические напоминания о предыдущем контенте
"""

from typing import List, Dict, Optional

from core.subtitle_text import split_subtitle_units


class RecapGenerator:
    """Генерирует краткие напоминания для длинных видео"""
    
    # Минимальная длительность для рекапов
    MIN_DURATION = 300  # 5 минут
    
    # Интервал между рекапами
    RECAP_INTERVAL = 300  # 5 минут

    @staticmethod
    def recap_interval(duration: float) -> int:
        """Use macro-block recaps instead of interrupting a long video every 5m."""
        duration = float(duration or 0)
        if duration >= 2 * 60 * 60:
            return 20 * 60
        if duration >= 60 * 60:
            return 15 * 60
        return RecapGenerator.RECAP_INTERVAL
    
    # Шаблоны рекапов для всех языков
    RECAP_TEMPLATES = {
        'Russian': [
            "Напомню: мы говорили о {topic}.",
            "Итак, мы уже узнали что {summary}.",
            "Давайте вспомним: {summary}.",
            "Как вы помните, {summary}.",
            "Резюмируя предыдущее: {summary}.",
            "Вернёмся к тому, что {summary}."
        ],
        'English': [
            "Let me remind you: we talked about {topic}.",
            "So, we already learned that {summary}.",
            "Let's recall: {summary}.",
            "As you remember, {summary}.",
            "Summarizing the previous: {summary}.",
            "Let's go back to the fact that {summary}."
        ],
        'Spanish': [
            "Recordemos: hablamos de {topic}.",
            "Entonces, ya aprendimos que {summary}.",
            "Recordemos: {summary}.",
            "Como recordarás, {summary}.",
            "Resumiendo lo anterior: {summary}.",
            "Volvamos al hecho de que {summary}."
        ],
        'French': [
            "Rappelons: nous avons parlé de {topic}.",
            "Donc, nous avons déjà appris que {summary}.",
            "Rappelons-nous: {summary}.",
            "Comme vous vous en souvenez, {summary}.",
            "En résumant ce qui précède: {summary}.",
            "Revenons au fait que {summary}."
        ],
        'German': [
            "Erinnern wir uns: wir sprachen über {topic}.",
            "Also, wir haben bereits gelernt, dass {summary}.",
            "Erinnern wir uns: {summary}.",
            "Wie Sie sich erinnern, {summary}.",
            "Zusammenfassend: {summary}.",
            "Kommen wir zurück zu der Tatsache, dass {summary}."
        ],
        'Chinese': [
            "让我提醒你：我们谈到了{topic}。",
            "所以，我们已经了解到{summary}。",
            "让我们回顾一下：{summary}。",
            "正如你所记得的，{summary}。",
            "总结前面的内容：{summary}。",
            "让我们回到{summary}这个事实。"
        ],
        'Japanese': [
            "思い出してください：{topic}について話しました。",
            "つまり、{summary}ということを学びました。",
            "振り返りましょう：{summary}。",
            "覚えているように、{summary}。",
            "前の内容をまとめると：{summary}。",
            "{summary}という事実に戻りましょう。"
        ],
        'Korean': [
            "기억해 주세요: {topic}에 대해 이야기했습니다.",
            "그래서 우리는 {summary}라는 것을 배웠습니다.",
            "다시 떠올려 봅시다: {summary}.",
            "기억하시겠지만, {summary}.",
            "이전 내용을 요약하면: {summary}.",
            "{summary}라는 사실로 돌아가 봅시다."
        ],
        'Portuguese': [
            "Lembre-se: falamos sobre {topic}.",
            "Então, já aprendemos que {summary}.",
            "Vamos relembrar: {summary}.",
            "Como você se lembra, {summary}.",
            "Resumindo o anterior: {summary}.",
            "Voltemos ao fato de que {summary}."
        ],
        'Italian': [
            "Ricordiamo: abbiamo parlato di {topic}.",
            "Finora abbiamo scoperto che {summary}.",
            "Ricapitoliamo: {summary}.",
            "Come ricorderai, {summary}.",
            "Riassumendo quanto visto: {summary}.",
            "Torniamo al punto secondo cui {summary}."
        ],
        'Hindi': [
            "याद करें: हमने {topic} के बारे में बात की थी।",
            "अब तक हमने जाना कि {summary}।",
            "संक्षेप में याद करें: {summary}।",
            "जैसा आपको याद होगा, {summary}।",
            "पिछली बातों का सार यह है: {summary}।",
            "आइए फिर उस बात पर लौटें कि {summary}।"
        ],
        'Arabic': [
            "لنتذكر: تحدثنا عن {topic}.",
            "حتى الآن عرفنا أن {summary}.",
            "لنلخص ما سبق: {summary}.",
            "كما تذكر، {summary}.",
            "خلاصة ما سبق هي: {summary}.",
            "لنعد إلى النقطة التي تقول إن {summary}."
        ]
    }
    
    @staticmethod
    def should_generate_recaps(duration: float) -> bool:
        """Определяет нужны ли рекапы"""
        return duration >= RecapGenerator.MIN_DURATION
    
    @staticmethod
    def generate_recap_points(
        duration: float,
        chapters: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """
        Определяет позиции для рекапов
        
        Args:
            duration: Длительность видео (секунды)
            chapters: Опциональный список глав
            
        Returns:
            [
                {"time": 300, "chapter_index": 0},
                {"time": 600, "chapter_index": 1},
                ...
            ]
        """
        if not RecapGenerator.should_generate_recaps(duration):
            return []
        
        points = []
        
        if chapters and len(chapters) > 1:
            # Рекап после каждой главы (кроме первой)
            for i, ch in enumerate(chapters[1:], 1):
                time = ch.get('start_time', i * RecapGenerator.RECAP_INTERVAL)
                points.append({
                    'time': time,
                    'chapter_index': i - 1,  # Рекап предыдущей главы
                    'has_chapter': True,
                    'position': max(0.0, min(1.0, float(time) / float(duration))),
                })
        else:
            # Автоматические рекапы каждые 5 минут
            interval = RecapGenerator.recap_interval(duration)
            current = interval
            chapter_index = 0
            while current < duration - 60:  # Не добавляем в последнюю минуту
                points.append({
                    'time': current,
                    'chapter_index': chapter_index,
                    'has_chapter': False,
                    'position': max(0.0, min(1.0, float(current) / float(duration))),
                })
                current += interval
                chapter_index += 1
        
        return points
    
    @staticmethod
    def generate_recap_text(
        previous_content: str,
        language: str = 'Russian',
        max_words: int = 15
    ) -> str:
        """
        Генерирует текст рекапа на основе предыдущего контента
        
        Args:
            previous_content: Текст предыдущей части
            language: Язык
            max_words: Максимум слов в рекапе
            
        Returns:
            Текст рекапа
        """
        import random
        
        # Выбираем шаблон
        templates = RecapGenerator.RECAP_TEMPLATES.get(
            language,
            RecapGenerator.RECAP_TEMPLATES['English']  # Fallback на английский
        )
        template = random.choice(templates)
        
        # Извлекаем ключевые моменты из предыдущего контента
        # Простая эвристика: берём первые N слов
        units, separator = split_subtitle_units(previous_content)
        if len(units) > max_words:
            summary = separator.join(units[:max_words]) + '...'
        else:
            summary = previous_content
        
        # Формируем рекап
        if '{topic}' in template:
            # Извлекаем тему (первые 3-5 слов)
            topic_units = units[:min(5, len(units))]
            topic = separator.join(topic_units)
            recap = template.format(topic=topic)
        else:
            recap = template.format(summary=summary)
        
        return recap
    
    @staticmethod
    def inject_recaps(
        text: str,
        recap_points: List[Dict],
        chapters: Optional[List[Dict]] = None,
        language: str = 'Russian',
        words_per_second: float = 2.5
    ) -> str:
        """
        Внедряет рекапы в текст
        
        Args:
            text: Исходный текст
            recap_points: Точки рекапов из generate_recap_points
            chapters: Список глав (если есть)
            language: Язык
            words_per_second: Скорость речи
            
        Returns:
            Текст с внедрёнными рекапами
        """
        if not recap_points:
            return text
        
        units, separator = split_subtitle_units(text)
        if not units:
            return text
        compact = separator == ''
        total_units = len(units)
        
        # Сортируем точки по времени
        sorted_points = sorted(recap_points, key=lambda x: x['time'])
        
        # Внедряем рекапы
        result_units = []
        last_insert_pos = 0
        
        for point in sorted_points:
            # Вычисляем позицию в тексте
            if compact:
                unit_position = int(float(point.get('position', 0.0)) * total_units)
            else:
                unit_position = int(point['time'] * words_per_second)
            unit_position = max(0, min(unit_position, total_units - 1))
            
            # Добавляем слова до этой позиции
            result_units.extend(units[last_insert_pos:unit_position])
            
            # Генерируем рекап
            if chapters and point.get('has_chapter'):
                chapter_idx = point['chapter_index']
                if chapter_idx < len(chapters):
                    chapter_text = chapters[chapter_idx].get('text', '')
                    recap = RecapGenerator.generate_recap_text(
                        chapter_text,
                        language=language
                    )
                else:
                    # Fallback: рекап предыдущих слов
                    previous_text = separator.join(
                        units[max(0, unit_position - 50):unit_position]
                    )
                    recap = RecapGenerator.generate_recap_text(
                        previous_text,
                        language=language
                    )
            else:
                # Рекап предыдущих слов
                previous_text = separator.join(
                    units[max(0, unit_position - 50):unit_position]
                )
                recap = RecapGenerator.generate_recap_text(
                    previous_text,
                    language=language
                )
            
            # Добавляем рекап
            if compact and result_units and not str(result_units[-1]).endswith(('。', '！', '？', '.', '!', '?')):
                result_units.append('。' if language in {'Chinese', 'Japanese'} else '.')
            result_units.append(recap)
            
            last_insert_pos = unit_position
        
        # Добавляем оставшиеся слова
        result_units.extend(units[last_insert_pos:])
        
        return separator.join(result_units)
