#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎬 SHORT TEXT ENHANCER - Улучшение текстов для Shorts

5 техник для повышения качества и retention:
1. Ритмические паузы (драматургические "...")
2. Контрастные переходы (резкие смены тона)
3. Числовая конкретика (замена абстракций на числа)
4. Микро-CTA по позиции
5. Forbidden words фильтр (удаление воды)
"""

import re
import random
from typing import List


class ShortTextEnhancer:
    """Улучшение текстов для коротких видео (до 60 сек)"""
    
    # ============================================================
    # 🚫 FORBIDDEN WORDS - Слова-паразиты для удаления
    # ============================================================
    
    FORBIDDEN_WORDS = {
        'Russian': [
            # Вводные слова-паразиты
            r'\bв общем\b',
            r'\bкак бы\b',
            r'\bну\b',
            r'\bтипа\b',
            r'\bв принципе\b',
            r'\bсобственно\b',
            r'\bкороче\b',
            r'\bтак сказать\b',
            r'\bпонимаешь\b',
            r'\bзначит\b',
            r'\bвот\b',
            r'\bэто самое\b',
            r'\bкак говорится\b',
            r'\bможно сказать\b',
            r'\bтак вот\b',
            r'\bна самом деле\b',
            r'\bпо сути\b',
            r'\bв целом\b',
            r'\bв общем-то\b',
            r'\bкак-то так\b',
            # Ослабляющие слова
            r'\bнаверное\b',
            r'\bвозможно\b',
            r'\bскорее всего\b',
            r'\bвроде бы\b',
            r'\bкажется\b',
            r'\bпо-моему\b',
            r'\bдумаю что\b',
        ],
        'English': [
            r'\bbasically\b',
            r'\bliterally\b',
            r'\bactually\b',
            r'\byou know\b',
            r'\blike\b',
            r'\bkind of\b',
            r'\bsort of\b',
            r'\bi mean\b',
            r'\bi think\b',
            r'\bi guess\b',
            r'\bprobably\b',
            r'\bmaybe\b',
            r'\bperhaps\b',
            r'\bin general\b',
            r'\bto be honest\b',
            r'\bhonestly\b',
        ]
    }
    
    # ============================================================
    # 🔄 CONTRAST TRANSITIONS - Контрастные переходы
    # ============================================================
    
    CONTRAST_TRANSITIONS = {
        'Russian': {
            'positive_to_negative': [
                "Всё было идеально... А ПОТОМ ВСЁ РУХНУЛО.",
                "Казалось, победа близка... Но судьба решила иначе.",
                "Успех был так близок... И тут случилось НЕМЫСЛИМОЕ.",
                "Всё шло по плану... Пока не произошло ЭТО.",
                "Он был на вершине... А через секунду — на дне.",
            ],
            'negative_to_positive': [
                "Всё казалось потерянным... Но тут случилось ЧУДО.",
                "Надежды не было... И вдруг — ПОВОРОТ!",
                "Он был сломлен... Но встал и ПОБЕДИЛ.",
                "Мир рушился... А потом всё изменилось.",
                "Это был конец... Или только НАЧАЛО?",
            ],
            'calm_to_chaos': [
                "Тишина... А потом — ВЗРЫВ!",
                "Всё было спокойно... ВНЕЗАПНО!",
                "Обычный день... Который стал КОШМАРОМ.",
                "Ничто не предвещало... А потом — ХАОС!",
            ],
            'universal': [
                "Но подожди... Это ещё не всё!",
                "И тут... ПОВОРОТ!",
                "Казалось бы, конец... НО НЕТ!",
                "Всё изменилось... За ОДНУ секунду.",
            ]
        },
        'English': {
            'positive_to_negative': [
                "Everything was perfect... Then it ALL FELL APART.",
                "Victory seemed certain... But fate had other plans.",
                "Success was so close... Then the UNTHINKABLE happened.",
            ],
            'negative_to_positive': [
                "All seemed lost... Then a MIRACLE happened.",
                "There was no hope... And then — a TWIST!",
                "He was broken... But he rose and WON.",
            ],
            'calm_to_chaos': [
                "Silence... Then — EXPLOSION!",
                "Everything was calm... SUDDENLY!",
                "An ordinary day... That became a NIGHTMARE.",
            ],
            'universal': [
                "But wait... There's MORE!",
                "And then... THE TWIST!",
                "Seemed like the end... BUT NO!",
            ]
        },
        'German': {'universal': ["Doch warte... Das ist noch nicht alles!", "Und dann... kam die WENDE!"]},
        'French': {'universal': ["Mais attends... Ce n’est pas tout !", "Et là... TOUT BASCULE !"]},
        'Spanish': {'universal': ["Pero espera... ¡Eso no es todo!", "Y entonces... ¡TODO CAMBIÓ!"]},
        'Italian': {'universal': ["Ma aspetta... Non è tutto!", "E poi... CAMBIA TUTTO!"]},
        'Portuguese': {'universal': ["Mas espere... Isso não é tudo!", "E então... TUDO MUDOU!"]},
        'Japanese': {'universal': ["でも待ってください…まだ続きがあります！", "その瞬間…すべてが変わりました！"]},
        'Korean': {'universal': ["하지만 잠깐… 아직 끝이 아닙니다!", "바로 그때… 모든 것이 바뀌었습니다!"]},
        'Chinese': {'universal': ["但先等等……事情还没结束！", "就在这时……一切都变了！"]},
        'Hindi': {'universal': ["लेकिन रुकिए… बात अभी बाकी है!", "और तभी… सब कुछ बदल गया!"]},
        'Arabic': {'universal': ["لكن انتظر… فالأمر لم ينتهِ بعد!", "وفجأة… تغيّر كل شيء!"]},
    }
    
    # ============================================================
    # 📢 MICRO CTA - Призывы по позиции
    # ============================================================
    
    MICRO_CTA = {
        'Russian': {
            'early': [
                "Подожди, дальше круче!",
                "Не уходи, сейчас будет!",
                "Стой, это важно!",
                "Секунду, вот оно!",
            ],
            'middle': [
                "Смотри до конца!",
                "Сейчас будет бомба!",
                "Внимание, главное!",
                "Приготовься!",
            ],
            'end': [
                "Лайк если согласен!",
                "Сохрани это!",
                "Напиши что думаешь!",
                "Подписка = ещё больше!",
                "Отправь другу!",
            ]
        },
        'English': {
            'early': [
                "Wait, it gets better!",
                "Don't go, here it comes!",
                "Stop, this is important!",
            ],
            'middle': [
                "Watch till the end!",
                "Here comes the bomb!",
                "Pay attention!",
            ],
            'end': [
                "Like if you agree!",
                "Save this!",
                "Comment your thoughts!",
                "Subscribe for more!",
            ]
        },
        'German': {
            'early': ["Warte, gleich wird es spannend!"],
            'middle': ["Bleib bis zum Ende dran!"],
            'end': ["Speichere das und abonniere für mehr!"],
        },
        'French': {
            'early': ["Attends, la suite est encore plus forte !"],
            'middle': ["Regarde jusqu’à la fin !"],
            'end': ["Enregistre et abonne-toi pour la suite !"],
        },
        'Spanish': {
            'early': ["Espera, lo mejor viene ahora!"],
            'middle': ["Mira hasta el final!"],
            'end': ["Guárdalo y sígueme para ver más!"],
        },
        'Italian': {
            'early': ["Aspetta, il meglio deve ancora venire!"],
            'middle': ["Guarda fino alla fine!"],
            'end': ["Salva il video e seguimi per altri contenuti!"],
        },
        'Portuguese': {
            'early': ["Espere, a melhor parte vem agora!"],
            'middle': ["Assista até o fim!"],
            'end': ["Salve e siga para ver mais!"],
        },
        'Japanese': {
            'early': ["待ってください、ここからが本番です！"],
            'middle': ["最後まで見てください！"],
            'end': ["保存して、続きもチェックしてください！"],
        },
        'Korean': {
            'early': ["잠깐, 이제부터가 진짜입니다!"],
            'middle': ["끝까지 확인해 보세요!"],
            'end': ["저장하고 다음 영상도 확인하세요!"],
        },
        'Chinese': {
            'early': ["先别走，精彩的还在后面！"],
            'middle': ["一定要看到最后！"],
            'end': ["记得收藏并关注更多内容！"],
        },
        'Hindi': {
            'early': ["रुकिए, असली बात अब शुरू होती है!"],
            'middle': ["अंत तक ज़रूर देखिए!"],
            'end': ["इसे सहेजें और आगे के लिए फ़ॉलो करें!"],
        },
        'Arabic': {
            'early': ["انتظر، فالأفضل لم يأتِ بعد!"],
            'middle': ["شاهد حتى النهاية!"],
            'end': ["احفظ الفيديو وتابع للمزيد!"],
        },
    }
    
    # ============================================================
    # 🔢 NUMBER REPLACEMENTS - Замены абстракций на числа
    # ============================================================
    
    ABSTRACT_TO_NUMBERS = {
        'Russian': {
            r'\bмного\b': ['847', '1,247', '2,891', '156'],
            r'\bнемного\b': ['17', '23', '8', '12'],
            r'\bнесколько\b': ['7', '4', '11', '6'],
            r'\bтысячи\b': ['4,782', '8,341', '2,156'],
            r'\bмиллионы\b': ['47 миллионов', '8.3 миллиона', '156 миллионов'],
            r'\bдавно\b': ['в 1847 году', 'в 1923 году', '127 лет назад'],
            r'\bнедавно\b': ['3 дня назад', 'на прошлой неделе', '11 дней назад'],
            r'\bбыстро\b': ['за 3.7 секунды', 'за 0.8 секунды', 'за 12 секунд'],
            r'\bмедленно\b': ['47 минут', '2 часа 17 минут', '6 часов'],
            r'\bдолго\b': ['847 дней', '3 года и 4 месяца', '17 лет'],
            r'\bогромный\b': ['размером с 3 футбольных поля', '47 метров', '8-этажный'],
            r'\bмаленький\b': ['размером с ноготь', '2.3 сантиметра', 'меньше спички'],
            r'\bвысокий\b': ['187 см', '2.1 метра', 'выше баскетболиста'],
            r'\bпочти все\b': ['94%', '87%', '91%'],
            r'\bбольшинство\b': ['73%', '68%', '81%'],
            r'\bменьшинство\b': ['12%', '7%', '23%'],
            r'\bполовина\b': ['47%', '52%', '49%'],
        },
        'English': {
            r'\bmany\b': ['847', '1,247', '2,891'],
            r'\bfew\b': ['7', '4', '11'],
            r'\bseveral\b': ['17', '23', '8'],
            r'\blong ago\b': ['in 1847', 'in 1923', '127 years ago'],
            r'\brecently\b': ['3 days ago', 'last week', '11 days ago'],
            r'\bquickly\b': ['in 3.7 seconds', 'in 0.8 seconds'],
            r'\bslowly\b': ['47 minutes', '2 hours 17 minutes'],
            r'\bhuge\b': ['size of 3 football fields', '47 meters tall'],
            r'\btiny\b': ['size of a fingernail', '2.3 centimeters'],
            r'\bmost people\b': ['73%', '68%', '81%'],
            r'\balmost everyone\b': ['94%', '87%', '91%'],
        }
    }
    
    # ============================================================
    # ⏸️ PAUSE RULES - Правила расстановки пауз
    # ============================================================
    
    PAUSE_TRIGGERS = {
        'Russian': {
            'before_reveal': [
                r'оказалось',
                r'выяснилось', 
                r'правда в том',
                r'секрет в том',
                r'ответ',
                r'причина',
                r'истина',
            ],
            'after_shock': [
                r'мёртв',
                r'умер',
                r'погиб',
                r'взорвал',
                r'уничтожил',
                r'исчез',
                r'пропал',
                r'НИКТО',
                r'НИЧЕГО',
                r'НЕВОЗМОЖНО',
            ],
            'dramatic_wrap': [
                r'навсегда',
                r'никогда',
                r'впервые',
                r'последний раз',
                r'единственный',
            ]
        },
        'English': {
            'before_reveal': [
                r'turned out',
                r'the truth is',
                r'the secret is',
                r'the answer',
                r'the reason',
            ],
            'after_shock': [
                r'dead',
                r'died',
                r'exploded',
                r'destroyed',
                r'disappeared',
                r'NOBODY',
                r'NOTHING',
                r'IMPOSSIBLE',
            ],
            'dramatic_wrap': [
                r'forever',
                r'never',
                r'first time',
                r'last time',
                r'the only',
            ]
        },
        'German': {
            'before_reveal': [r'es stellte sich heraus', r'die wahrheit', r'der grund'],
            'after_shock': [r'tot', r'zerstört', r'verschwunden'],
            'dramatic_wrap': [r'für immer', r'niemals', r'zum ersten mal'],
        },
        'French': {
            'before_reveal': [r'il s’avère', r'la vérité', r'la raison'],
            'after_shock': [r'mort', r'détruit', r'disparu'],
            'dramatic_wrap': [r'pour toujours', r'jamais', r'pour la première fois'],
        },
        'Spanish': {
            'before_reveal': [r'resultó que', r'la verdad', r'la razón'],
            'after_shock': [r'muerto', r'destruido', r'desapareció'],
            'dramatic_wrap': [r'para siempre', r'nunca', r'por primera vez'],
        },
        'Italian': {
            'before_reveal': [r'si è scoperto', r'la verità', r'il motivo'],
            'after_shock': [r'morto', r'distrutto', r'scomparso'],
            'dramatic_wrap': [r'per sempre', r'mai', r'per la prima volta'],
        },
        'Portuguese': {
            'before_reveal': [r'descobriu-se', r'a verdade', r'o motivo'],
            'after_shock': [r'morto', r'destruído', r'desapareceu'],
            'dramatic_wrap': [r'para sempre', r'nunca', r'pela primeira vez'],
        },
        'Japanese': {
            'before_reveal': [r'実は', r'真実', r'理由'],
            'after_shock': [r'死亡', r'破壊', r'消え'],
            'dramatic_wrap': [r'永遠に', r'決して', r'初めて'],
        },
        'Korean': {
            'before_reveal': [r'알고 보니', r'진실은', r'이유는'],
            'after_shock': [r'사망', r'파괴', r'사라졌'],
            'dramatic_wrap': [r'영원히', r'절대', r'처음으로'],
        },
        'Chinese': {
            'before_reveal': [r'原来', r'真相', r'原因'],
            'after_shock': [r'死亡', r'摧毁', r'消失'],
            'dramatic_wrap': [r'永远', r'从未', r'第一次'],
        },
        'Hindi': {
            'before_reveal': [r'पता चला', r'सच्चाई', r'कारण'],
            'after_shock': [r'मृत', r'नष्ट', r'गायब'],
            'dramatic_wrap': [r'हमेशा के लिए', r'कभी नहीं', r'पहली बार'],
        },
        'Arabic': {
            'before_reveal': [r'اتضح أن', r'الحقيقة', r'السبب'],
            'after_shock': [r'مات', r'دُمّر', r'اختفى'],
            'dramatic_wrap': [r'إلى الأبد', r'أبداً', r'لأول مرة'],
        },
    }

    # ============================================================
    # 🎯 MAIN METHODS
    # ============================================================
    
    @classmethod
    def remove_forbidden_words(cls, text: str, language: str = 'Russian') -> str:
        """Удаляет слова-паразиты из текста."""
        forbidden = cls.FORBIDDEN_WORDS.get(language, ())
        
        result = text
        for pattern in forbidden:
            result = re.sub(pattern, '', result, flags=re.IGNORECASE)
        
        # Убираем двойные пробелы
        result = re.sub(r'\s+', ' ', result)
        result = re.sub(r'\s+([.,!?;:])', r'\1', result)
        result = re.sub(r'^\s*[,.:;]\s*', '', result)
        result = re.sub(r'([.!?])\s*,\s*', r'\1 ', result)
        result = re.sub(r'\s+,\s+([А-ЯA-Z])', r' \1', result)
        result = re.sub(r'[,]{2,}', ',', result)
        result = re.sub(r'[.]{2,}(?![.])', '.', result)
        
        return result.strip()
    
    @classmethod
    def add_rhythmic_pauses(cls, text: str, language: str = 'Russian') -> str:
        """Добавляет драматургические паузы в правильных местах."""
        triggers = cls.PAUSE_TRIGGERS.get(language)
        if not triggers:
            return text
        result = text
        
        for pattern in triggers['before_reveal']:
            result = re.sub(
                rf'(\s)({pattern})',
                r'\1... \2',
                result,
                flags=re.IGNORECASE
            )
        
        for pattern in triggers['after_shock']:
            result = re.sub(
                rf'({pattern})(\s|[.,!?])',
                r'\1...\2',
                result,
                flags=re.IGNORECASE
            )
        
        for pattern in triggers['dramatic_wrap']:
            result = re.sub(
                rf'(\s)({pattern})(\s|[.,!?])',
                r'...\1\2\3',
                result,
                flags=re.IGNORECASE
            )
        
        result = re.sub(r'\.{4,}', '...', result)
        result = re.sub(r'\s+\.\.\.', '...', result)
        
        return result
    
    @classmethod
    def add_number_specifics(
        cls, 
        text: str, 
        language: str = 'Russian',
        replacement_chance: float = 0.7
    ) -> str:
        """Заменяет абстрактные слова на конкретные числа (с рандомизацией)."""
        # Генерируем случайные числа для каждого вызова
        replacements_russian = {
            r'\bмного\b': [str(random.randint(500, 3000)), f'{random.randint(1, 9)},{random.randint(100, 999)}'],
            r'\bнемного\b': [str(random.randint(5, 30))],
            r'\bнесколько\b': [str(random.randint(3, 15))],
            r'\bтысячи\b': [f'{random.randint(2, 9)},{random.randint(100, 999)}'],
            r'\bмиллионы\b': [f'{random.randint(1, 200)} миллионов'],
            r'\bдавно\b': [f'в {random.randint(1800, 1990)} году', f'{random.randint(50, 200)} лет назад'],
            r'\bнедавно\b': [f'{random.randint(1, 14)} дней назад'],
            r'\bбыстро\b': [f'за {random.randint(1, 10)}.{random.randint(1, 9)} секунды'],
            r'\bмедленно\b': [f'{random.randint(20, 90)} минут'],
            r'\bдолго\b': [f'{random.randint(100, 999)} дней'],
            r'\bогромный\b': [f'{random.randint(20, 100)} метров'],
            r'\bмаленький\b': [f'{random.randint(1, 5)}.{random.randint(1, 9)} сантиметра'],
            r'\bвысокий\b': [f'{random.randint(180, 220)} см'],
            r'\bпочти все\b': [f'{random.randint(85, 98)}%'],
            r'\bбольшинство\b': [f'{random.randint(60, 85)}%'],
            r'\bменьшинство\b': [f'{random.randint(5, 25)}%'],
            r'\bполовина\b': [f'{random.randint(45, 55)}%'],
        }
        
        replacements_english = {
            r'\bmany\b': [str(random.randint(500, 3000))],
            r'\bfew\b': [str(random.randint(3, 15))],
            r'\bseveral\b': [str(random.randint(5, 20))],
            r'\blong ago\b': [f'in {random.randint(1800, 1990)}', f'{random.randint(50, 200)} years ago'],
            r'\brecently\b': [f'{random.randint(1, 14)} days ago'],
            r'\bquickly\b': [f'in {random.randint(1, 10)}.{random.randint(1, 9)} seconds'],
            r'\bslowly\b': [f'{random.randint(20, 90)} minutes'],
            r'\bhuge\b': [f'{random.randint(20, 100)} meters tall'],
            r'\btiny\b': [f'{random.randint(1, 5)}.{random.randint(1, 9)} centimeters'],
            r'\bmost people\b': [f'{random.randint(60, 85)}%'],
            r'\balmost everyone\b': [f'{random.randint(85, 98)}%'],
        }
        
        replacements = (
            replacements_russian if language == 'Russian'
            else replacements_english if language == 'English'
            else {}
        )
        result = text
        
        for pattern, options in replacements.items():
            if random.random() < replacement_chance:
                replacement = random.choice(options)
                result = re.sub(pattern, replacement, result, count=1, flags=re.IGNORECASE)
        
        return result
    
    @classmethod
    def insert_contrast_transition(
        cls,
        text: str,
        position: float = 0.5,
        transition_type: str = 'universal',
        language: str = 'Russian'
    ) -> str:
        """Вставляет контрастный переход в указанную позицию."""
        transitions = cls.CONTRAST_TRANSITIONS.get(language)
        if not transitions:
            return text
        transition_options = transitions.get(transition_type, transitions['universal'])
        
        protected = text.replace('...', '<<<PAUSE>>>')
        sentences = re.split(r'(?<=[.!?。！？؟।॥])\s*', protected)
        sentences = [s.replace('<<<PAUSE>>>', '...') for s in sentences]
        sentences = [re.sub(r'^[,\s]+', '', s).strip() for s in sentences if s.strip()]
        
        if len(sentences) < 3:
            return text
        
        insert_idx = int(len(sentences) * position)
        insert_idx = max(1, min(insert_idx, len(sentences) - 1))
        
        transition = random.choice(transition_options)
        sentences.insert(insert_idx, transition)
        
        separator = '' if language in {'Japanese', 'Chinese'} else ' '
        return separator.join(sentences)
    
    @classmethod
    def insert_micro_cta(
        cls,
        text: str,
        duration: float,
        language: str = 'Russian',
        cta_positions: List[str] = None
    ) -> str:
        """Вставляет микро-CTA в зависимости от позиции в видео."""
        if cta_positions is None:
            cta_positions = ['middle', 'end'] if duration <= 60 else ['early', 'middle', 'end']
        
        ctas = cls.MICRO_CTA.get(language)
        if not ctas:
            return text
        
        protected = text.replace('...', '<<<PAUSE>>>')
        sentences = re.split(r'(?<=[.!?。！？؟।॥])\s*', protected)
        sentences = [s.replace('<<<PAUSE>>>', '...') for s in sentences]
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if len(sentences) < 4:
            return text
        
        result_sentences = sentences.copy()
        offset = 0
        
        if 'early' in cta_positions and len(sentences) >= 6:
            early_idx = int(len(sentences) * 0.3) + offset
            cta = random.choice(ctas['early'])
            result_sentences.insert(early_idx, cta)
            offset += 1
        
        if 'middle' in cta_positions and len(sentences) >= 4:
            middle_idx = int(len(sentences) * 0.7) + offset
            cta = random.choice(ctas['middle'])
            result_sentences.insert(middle_idx, cta)
            offset += 1
        
        if 'end' in cta_positions:
            cta = random.choice(ctas['end'])
            result_sentences.append(cta)
        
        separator = '' if language in {'Japanese', 'Chinese'} else ' '
        return separator.join(result_sentences)
    
    @classmethod
    def enhance_short_text(
        cls,
        text: str,
        duration: float = 60.0,
        language: str = 'Russian',
        remove_forbidden: bool = True,
        add_pauses: bool = True,
        add_numbers: bool = True,
        add_contrast: bool = True,
        add_cta: bool = True,
        number_chance: float = 0.5,
        contrast_position: float = 0.6,
        cta_positions: List[str] = None
    ) -> str:
        """
        🚀 ГЛАВНЫЙ МЕТОД - Применяет все улучшения к тексту.
        """
        result = text
        
        if remove_forbidden:
            result = cls.remove_forbidden_words(result, language)
        
        if add_numbers:
            result = cls.add_number_specifics(result, language, number_chance)
        
        if add_pauses:
            result = cls.add_rhythmic_pauses(result, language)
        
        if add_contrast and duration >= 30:
            result = cls.insert_contrast_transition(
                result, 
                position=contrast_position,
                language=language
            )
        
        if add_cta:
            result = cls.insert_micro_cta(result, duration, language, cta_positions)
        
        return result
    
    @classmethod
    def get_enhancement_prompt_additions(
        cls,
        language: str = 'Russian',
        duration: float = 60.0
    ) -> str:
        """Возвращает дополнения к промпту для AI-генерации."""
        forbidden = cls.FORBIDDEN_WORDS.get(language, ())
        forbidden_examples = [p.replace(r'\b', '').replace('\\b', '') for p in forbidden[:10]]
        
        return f"""
🚫 FORBIDDEN WORDS (NEVER USE):
{', '.join(forbidden_examples)}
These words are "filler" and make text weak. Remove them!

🔢 NUMBER SPECIFICS:
- Replace "много" with specific numbers: "847", "1,247"
- Replace "давно" with dates: "в 1847 году", "127 лет назад"
- Replace "быстро" with time: "за 3.7 секунды"
- Specific numbers = TRUST and AUTHORITY

⏸️ RHYTHMIC PAUSES:
- Add "..." BEFORE revelations: "И тут... оказалось что"
- Add "..." AFTER shocking words: "Он погиб... в тот же день"
- Wrap dramatic words: "Это было... навсегда ..."

🔄 CONTRAST TRANSITIONS:
- Use sudden tone shifts at 50-70% of the script
- Example: "Всё было идеально... А ПОТОМ ВСЁ РУХНУЛО."
- Creates emotional rollercoaster = retention!

📢 MICRO-CTA:
{"- At 70%: 'Смотри до конца!' or 'Сейчас будет бомба!'" if duration <= 60 else "- At 30%: 'Подожди, дальше круче!'"}
- At end: 'Лайк если согласен!' or 'Сохрани это!'
"""
