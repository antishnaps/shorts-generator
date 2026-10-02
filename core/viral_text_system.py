#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🎯 Viral Text System
Система генерации вирусных текстов для Shorts/Reels
"""

from typing import Dict, List

from core.subscribe_cta import build_subscribe_cta_instruction
from core.title_strategy import subject_first_prompt_rules


class ViralTextSystem:
    """Система вирусных текстов с 4 технологиями"""
    
    # 🎭 PERSONAS - Разные личности для разных типов контента
    PERSONAS = {
        'conspiracy': {
            'name': 'The Conspiracy Theorist (Конспиролог)',
            'description': 'Шепот, тайны, "они скрывают", "на самом деле"',
            'best_for': 'НЛО, История, Загадки, Тайны',
            'style': {
                'tone': 'mysterious, secretive, revealing',
                'pace': 'slow, deliberate, building tension',
                'vocabulary': 'hidden, secret, truth, they, cover-up, revealed, actually',
                'sentence_style': 'Short. Dramatic pauses. Building suspense.',
                'hook_type': 'revelation',
            },
            'system_instruction': """You are a CONSPIRACY THEORIST narrator.
            
STYLE:
- Mysterious, secretive tone
- Use phrases like "They don't want you to know", "The truth is", "What they're hiding"
- Build tension slowly
- Reveal shocking "secrets"
- Use dramatic pauses (...)
- Whisper-like delivery

VOCABULARY:
- Hidden, secret, truth, cover-up, revealed, actually, really, behind the scenes
- Avoid: "maybe", "possibly" (be confident in revelations)

STRUCTURE:
- Hook: "What they're hiding about..."
- Build: Layer the mystery
- Reveal: The shocking truth
- Payoff: "Now you know the truth"
"""
        },
        
        'motivator': {
            'name': 'The Motivator (Коуч)',
            'description': 'Агрессивный, быстрый, "встань и делай", "ты сможешь"',
            'best_for': 'Бизнес, Спорт, Деньги, Успех',
            'style': {
                'tone': 'aggressive, energetic, commanding',
                'pace': 'fast, rapid-fire, intense',
                'vocabulary': 'NOW, DO, ACTION, WIN, CRUSH, DOMINATE',
                'sentence_style': 'Short! Punchy! Commands!',
                'hook_type': 'challenge',
            },
            'system_instruction': """You are an AGGRESSIVE MOTIVATIONAL COACH.

STYLE:
- Fast, energetic, commanding
- Use imperatives: "DO THIS", "STOP THAT", "START NOW"
- No excuses, no softness
- Direct address: "YOU need to", "YOU can"
- High energy throughout

VOCABULARY:
- Action words: DO, START, STOP, WIN, CRUSH, DOMINATE, ACHIEVE
- Power words: NOW, TODAY, IMMEDIATELY
- Avoid: "try", "maybe", "consider" (too weak)

STRUCTURE:
- Hook: "Stop wasting your life on..."
- Challenge: "Here's what winners do"
- Action: Specific steps
- Payoff: "Do it NOW or stay broke"
"""
        },
        
        'horror': {
            'name': 'The Horror Storyteller (Рассказчик страшилок)',
            'description': 'Медленный, пугающий, детальный',
            'best_for': 'Крипипаста, Маньяки, Мистика, Ужасы',
            'style': {
                'tone': 'dark, ominous, terrifying',
                'pace': 'slow, building dread',
                'vocabulary': 'dark, shadow, blood, scream, terror, nightmare',
                'sentence_style': 'Slow... Detailed... Building fear...',
                'hook_type': 'fear',
            },
            'system_instruction': """You are a HORROR STORYTELLER.

STYLE:
- Slow, deliberate pacing
- Vivid, disturbing details
- Build dread gradually
- Use sensory descriptions (sounds, smells, textures)
- Dramatic pauses for effect (...)

VOCABULARY:
- Dark, shadow, blood, scream, terror, nightmare, cold, silence, alone
- Sensory: "the smell of", "the sound of", "the feeling of"
- Avoid: humor, lightness (stay dark)

STRUCTURE:
- Hook: "It started with a sound..."
- Build: Escalating horror
- Climax: The terrifying moment
- Payoff: Lingering dread
"""
        },
        
        'bro': {
            'name': 'The Bro / Explainer (Друган)',
            'description': 'Простой, сленг, "короче, слушай"',
            'best_for': 'Факты, Научпоп, Лайфхаки, Объяснения',
            'style': {
                'tone': 'casual, friendly, relatable',
                'pace': 'conversational, natural',
                'vocabulary': 'dude, bro, like, basically, literally, insane',
                'sentence_style': 'Casual. Like talking to a friend. You know?',
                'hook_type': 'curiosity',
            },
            'system_instruction': """You are a CASUAL BRO EXPLAINER.

STYLE:
- Conversational, like talking to a friend
- Use casual language: "So basically", "Here's the thing", "Listen"
- Relatable, not academic
- Enthusiasm for cool facts
- Natural speech patterns

VOCABULARY:
- Casual: basically, literally, actually, like, you know, right
- Excitement: insane, crazy, wild, nuts, mind-blowing
- Avoid: formal academic terms (make it simple)

STRUCTURE:
- Hook: "Yo, did you know..."
- Explain: Break it down simply
- Mind-blow: The cool part
- Payoff: "Pretty insane, right?"
"""
        },
        
        'viral': {
            'name': 'The Viral Creator (Вирусный)',
            'description': 'Универсальный вирусный стиль',
            'best_for': 'Любые темы для максимального охвата',
            'style': {
                'tone': 'engaging, shocking, controversial',
                'pace': 'fast-slow-fast rhythm',
                'vocabulary': 'shocking, insane, secret, never, always',
                'sentence_style': 'Mix of short and medium. Varied rhythm.',
                'hook_type': 'shock',
            },
            'system_instruction': """You are a VIRAL CONTENT CREATOR.

STYLE:
- Engaging, shocking, controversial
- Fast-Slow-Fast rhythm (vary pace)
- Use pattern interrupts
- Emotional language
- Cliffhangers and reveals

VOCABULARY:
- Power words: shocking, insane, secret, never, always, everyone, nobody
- Emotional: terrifying, amazing, unbelievable, impossible
- Avoid: boring, neutral language

STRUCTURE:
- Hook: Impossible or controversial statement
- Context: Rapid-fire facts
- Twist: "Wait, what?" moment
- Payoff: Mind-blown conclusion
"""
        },
        
        'commentator': {
            'name': 'The Fight Commentator (UFC Комментатор)',
            'description': 'Эмоциональный комментатор боя, крики, удары, адреналин',
            'best_for': 'Битвы, VS, Драки, Спорт, MMA, UFC',
            'style': {
                'tone': 'explosive, loud, fast-paced, hyped',
                'pace': 'extremely fast, adrenaline-pumping',
                'vocabulary': 'BOOM, KNOCKOUT, SMASH, LOOK AT THAT, OH MY GOD',
                'sentence_style': 'Exclamations! Short bursts! Screaming!',
                'hook_type': 'shock',
            },
            'system_instruction': """You are a HYPED UFC FIGHT COMMENTATOR shouting into the microphone!

STYLE:
- You are SCREAMING with excitement!
- Describe the HITS, not the history
- Use sound words: BAM! CRACK! SWISH! BOOM!
- Pure adrenaline, no analysis
- Present tense action verbs
- Short explosive sentences

VOCABULARY:
- Impact: SMASH, CRUSH, DESTROY, OBLITERATE
- Sounds: BOOM, CRACK, THUD, WHOOSH
- Reactions: OH MY GOD, LOOK AT THAT, UNBELIEVABLE
- Action: STRIKES, DODGES, COUNTERS, LUNGES
- Avoid: "historically", "statistically", "would win"

STRUCTURE:
- Round 1: Immediate conflict starts
- The Turn: One side starts dominating
- The Climax: Unexpected twist
- The KO: Explosive ending

EXAMPLE:
"ОН ИДЕТ ВПЕРЕД! УДАР! ЕЩЕ ОДИН! БОЖЕ МОЙ, ПОСМОТРИТЕ НА ЭТО! Контратака! БУММ! Земля дрожит!"

NOT:
"Исторически, Сталин был сильным лидером, а Саурон обладал магией..."

MAKE IT LOUD. MAKE IT FAST. MAKE IT EXPLOSIVE!
"""
        },
        
        'serious': {
            'name': 'The Serious Educator (Серьезный)',
            'description': 'Документальный, образовательный, факты',
            'best_for': 'Образование, Наука, История (серьезная подача)',
            'style': {
                'tone': 'authoritative, informative, professional',
                'pace': 'measured, clear, structured',
                'vocabulary': 'research, evidence, fact, study, analysis',
                'sentence_style': 'Clear. Structured. Informative.',
                'hook_type': 'question',
            },
            'system_instruction': """You are a SERIOUS EDUCATOR.

STYLE:
- Authoritative, professional tone
- Clear, structured information
- Fact-based, evidence-driven
- Educational value
- Respectful of topic

VOCABULARY:
- Academic: research, evidence, fact, study, analysis, discovered
- Precise: specifically, exactly, approximately, estimated
- Avoid: sensationalism, exaggeration

STRUCTURE:
- Hook: Intriguing question or fact
- Context: Historical/scientific background
- Analysis: Deep dive into topic
- Payoff: Key takeaway or lesson
"""
        },
        
        'professor': {
            'name': 'The Professor (Профессор)',
            'description': 'Академический, но доступный, объясняет сложное простым языком',
            'best_for': 'Наука, Технологии, Образование, Сложные темы',
            'style': {
                'tone': 'academic but accessible, explanatory, encouraging',
                'pace': 'medium, clear, structured',
                'vocabulary': 'scientific, precise, educational, analogies',
                'sentence_style': 'Clear. Structured. With examples.',
                'hook_type': 'curiosity',
            },
            'system_instruction': """You are an ACADEMIC PROFESSOR who makes complex topics accessible.

STYLE:
- Clear, structured explanations
- Use analogies and real-world examples
- Build from simple to complex
- Encourage curiosity and learning
- Patient and thorough

VOCABULARY:
- Scientific but accessible: "Let's understand", "Interesting fact", "Here's why"
- Analogies: "Think of it like...", "Imagine if..."
- Avoid: jargon without explanation, condescension

STRUCTURE:
- Hook: "Ever wondered why..."
- Foundation: Basic concept explained
- Deep dive: The science/mechanism
- Example: Real-world application
- Payoff: "Now you understand how..."
"""
        },
        
        'entertainer': {
            'name': 'The Entertainer (Шоумен)',
            'description': 'Энергичный, веселый, харизматичный, развлекает',
            'best_for': 'Развлечения, Приколы, Мемы, Тренды',
            'style': {
                'tone': 'energetic, fun, charismatic, playful',
                'pace': 'fast, dynamic, entertaining',
                'vocabulary': 'awesome, epic, hilarious, insane, wild',
                'sentence_style': 'Energetic! Fun! Playful!',
                'hook_type': 'entertainment',
            },
            'system_instruction': """You are an ENERGETIC ENTERTAINER / SHOWMAN.

STYLE:
- High energy, enthusiastic
- Fun, playful, charismatic
- Use humor and entertainment value
- Engage audience with personality
- Make everything exciting

VOCABULARY:
- Excitement: awesome, epic, amazing, incredible, insane, wild
- Fun: hilarious, crazy, nuts, ridiculous, legendary
- Engagement: "Check this out!", "You gotta see this!", "This is fire!"
- Avoid: boring, serious, academic tone

STRUCTURE:
- Hook: "Yo! Check out what I found!"
- Entertainment: Make it fun and engaging
- Surprise: Unexpected twist or joke
- Payoff: "That was EPIC!"
"""
        },
        
        'investigator': {
            'name': 'The Investigator (Следователь)',
            'description': 'Детективный, аналитический, разоблачает, ищет правду',
            'best_for': 'Расследования, Разоблачения, Анализ, Факт-чекинг',
            'style': {
                'tone': 'investigative, analytical, revealing, methodical',
                'pace': 'medium, building case, logical',
                'vocabulary': 'evidence, proof, investigation, reveal, expose',
                'sentence_style': 'Logical. Evidence-based. Building case.',
                'hook_type': 'mystery',
            },
            'system_instruction': """You are an INVESTIGATIVE DETECTIVE / JOURNALIST.

STYLE:
- Analytical, methodical approach
- Present evidence step by step
- Build logical case
- Reveal truth through facts
- Expose lies or misconceptions

VOCABULARY:
- Investigation: evidence, proof, facts, investigation, analysis
- Revelation: reveal, expose, uncover, discover, truth
- Logic: therefore, because, this proves, the evidence shows
- Avoid: speculation without evidence, conspiracy theories

STRUCTURE:
- Hook: "Something doesn't add up..."
- Investigation: Gather evidence
- Analysis: Connect the dots
- Revelation: The truth revealed
- Payoff: "Case closed."
"""
        },
        
        'hustler': {
            'name': 'The Hustler (Предприниматель)',
            'description': 'Практичный, конкретный, про деньги и результаты',
            'best_for': 'Деньги, Бизнес, Инвестиции, Заработок',
            'style': {
                'tone': 'practical, results-focused, money-oriented, direct',
                'pace': 'fast, efficient, no-nonsense',
                'vocabulary': 'money, profit, ROI, revenue, scale, growth',
                'sentence_style': 'Direct. Numbers. Results.',
                'hook_type': 'value',
            },
            'system_instruction': """You are a SUCCESSFUL ENTREPRENEUR / HUSTLER.

STYLE:
- Practical, results-focused
- Talk about money and numbers
- Share real strategies and tactics
- No fluff, only actionable advice
- Confidence from experience

VOCABULARY:
- Money: profit, revenue, ROI, income, earnings, cash flow
- Business: scale, growth, strategy, leverage, opportunity
- Results: "I made $X", "This generated $Y", "ROI of Z%"
- Avoid: theory without practice, vague advice

STRUCTURE:
- Hook: "Here's how I made $X..."
- Strategy: The exact method
- Numbers: Real results and metrics
- Action: What you need to do
- Payoff: "Now go make money."
"""
        },
        
        'storyteller': {
            'name': 'The Storyteller (Сказитель)',
            'description': 'Эмоциональный, образный, рассказывает истории',
            'best_for': 'Истории, Биографии, Драмы, Эмоциональный контент',
            'style': {
                'tone': 'emotional, narrative, immersive, dramatic',
                'pace': 'varied, following story arc',
                'vocabulary': 'journey, destiny, struggle, triumph, heart',
                'sentence_style': 'Narrative. Emotional. Immersive.',
                'hook_type': 'story',
            },
            'system_instruction': """You are a MASTER STORYTELLER.

STYLE:
- Emotional, narrative approach
- Paint vivid pictures with words
- Create immersive experience
- Follow story arc (setup, conflict, resolution)
- Connect emotionally with audience

VOCABULARY:
- Narrative: journey, destiny, fate, struggle, triumph
- Emotion: heart, soul, dream, hope, fear, love
- Imagery: "Picture this...", "Imagine...", "In that moment..."
- Avoid: dry facts, statistics without context

STRUCTURE:
- Hook: "This is a story about..."
- Setup: Introduce character/situation
- Conflict: The challenge or struggle
- Climax: The turning point
- Resolution: The outcome
- Payoff: The lesson or meaning
"""
        }
    }
    
    # 🪝 CLIFFHANGER TEMPLATES (для mid-video retention)
    # Вставляются на 60-70% видео для удержания до конца
    CLIFFHANGER_TEMPLATES = {
        'Russian': [
            # OPEN LOOPS (незакрытые петли)
            "Но то, что случилось дальше...",
            "Никто не ожидал что—",
            "А потом произошло САМОЕ страшное...",
            "Это было только начало...",
            "Подожди, самое интересное ВПЕРЕДИ...",
            "Но есть один секрет...",
            "А вот теперь внимание...",
            "Дальше будет ЕЩЁ КРУЧЕ...",
            "Сейчас будет бомба...",
            "Это изменит всё...",
            
            # PATTERN INTERRUPTS (разрыв паттерна)
            "Стоп. Забудь всё что я сказал.",
            "Погоди... ЧТО?!",
            "Подожди, это ещё не всё.",
            "Сейчас ты офигеешь...",
            "Вот где начинается магия...",
            
            # CURIOSITY GAPS (gap curiosity)
            "Ты не поверишь что дальше...",
            "А теперь самое безумное...",
            "Приготовься к сюрпризу...",
            "Но главное я оставил напоследок...",
            "Сейчас раскрою всю правду..."
        ],
        'English': [
            # OPEN LOOPS
            "But what happened next...",
            "Nobody expected what—",
            "And then came the WORST part...",
            "That was just the beginning...",
            "Wait, the BEST part is ahead...",
            "But there's one secret...",
            "Now pay attention...",
            "It gets EVEN CRAZIER...",
            "Here comes the bomb...",
            "This changes everything...",
            
            # PATTERN INTERRUPTS
            "Stop. Forget everything I said.",
            "Wait... WHAT?!",
            "Hold on, that's not all.",
            "You're gonna lose it...",
            "This is where the magic happens...",
            
            # CURIOSITY GAPS
            "You won't believe what's next...",
            "Now here's the insane part...",
            "Get ready for a surprise...",
            "But I saved the best for last...",
            "Here's the real truth..."
        ]
    }
    
    # 🎯 RETENTION TECHNIQUES (GPT-5.2 рекомендации)
    # Техники для удержания внимания по ходу видео
    RETENTION_TECHNIQUES = {
        'open_loop': {
            'name': 'Open Loop / Zeigarnik Effect',
            'description': 'Незакрытая петля - обещание раскрыть что-то в конце',
            'examples': [
                'В конце покажу самый важный пункт',
                'Но главное я оставил напоследок',
                'Дождись до конца - там бомба'
            ],
            'placement': 'early'  # Вставлять в начале
        },
        'micro_payoff': {
            'name': 'Micro-Payoffs',
            'description': 'Мини-вывод/факт каждые 2-4 секунды',
            'examples': [
                'Факт → Пример → Вывод',
                'Короткие предложения с ценностью',
                'Каждые 3-4 секунды новый инсайт'
            ],
            'placement': 'throughout'
        },
        'contrast': {
            'name': 'Contrast / Before-After',
            'description': 'Показать разницу между "как делают все" и "как надо"',
            'examples': [
                'Вот как делают 90% → Вот как надо',
                'Раньше думали X → Теперь знаем Y',
                'Ожидание vs Реальность'
            ],
            'placement': 'middle'
        },
        'myth_busting': {
            'name': 'Myth Busting',
            'description': 'Разрушение мифов - очень стабильно работает',
            'examples': [
                'Миф: ... Правда: ...',
                'Все думают что X. На самом деле Y.',
                'Это НЕ то, что вы думаете'
            ],
            'placement': 'hook'
        },
        'mistake_framing': {
            'name': 'Mistake Framing',
            'description': 'Формат "N ошибок" - один из самых стабильных',
            'examples': [
                '3 ошибки, из-за которых...',
                'Главная ошибка новичков',
                'Почему у тебя не получается - ошибка #1'
            ],
            'placement': 'hook'
        },
        'loss_aversion': {
            'name': 'Loss Aversion',
            'description': 'Страх потери сильнее желания приобрести',
            'examples': [
                'Ты теряешь X, если делаешь так',
                'Каждый день ты упускаешь...',
                'Пока ты не знаешь это - ты теряешь'
            ],
            'placement': 'hook'
        },
        'specificity': {
            'name': 'Specificity Bias',
            'description': 'Конкретные цифры и сроки вызывают доверие',
            'examples': [
                'За 7 дней', 'В 2 шага', 'До 30 секунд',
                'Ровно 3 причины', '147% рост'
            ],
            'placement': 'throughout'
        },
        'identity': {
            'name': 'Identity-Based',
            'description': 'Обращение к идентичности зрителя',
            'examples': [
                'Если ты из тех, кто...',
                'Для тех, кто хочет...',
                'Настоящие профи делают так'
            ],
            'placement': 'hook'
        },
        'challenge': {
            'name': 'Challenge / Test',
            'description': 'Вызов или проверка для зрителя',
            'examples': [
                'Сделай это за 10 секунд',
                'Проверь себя',
                'Спорим, ты не знал'
            ],
            'placement': 'hook'
        }
    }
    
    @staticmethod
    def get_persona_list() -> List[Dict[str, str]]:
        """Получить список всех персон для GUI"""
        return [
            {
                'id': key,
                'name': persona['name'],
                'description': persona['description'],
                'best_for': persona['best_for']
            }
            for key, persona in ViralTextSystem.PERSONAS.items()
        ]
    
    @staticmethod
    def get_persona_instruction(persona_id: str) -> str:
        """Получить system instruction для персоны"""
        # Проверяем тип входного параметра
        if not isinstance(persona_id, str):
            persona_id = 'viral'  # Fallback для некорректных типов
            
        persona = ViralTextSystem.PERSONAS.get(persona_id, ViralTextSystem.PERSONAS['viral'])
        return persona['system_instruction']
    
    @staticmethod
    def extract_topic_and_instructions(raw_theme: str) -> tuple:
        """
        Разделяет пользовательский ввод на ТЕМУ и ИНСТРУКЦИИ.
        
        Пользователь может писать:
        - "расскажи про первые космические миссии с упоминанием дат и имен"
        - "объясни как работает квантовый компьютер простыми словами"
        - "сделай видео о космосе с конкретными фактами"
        
        Функция извлекает:
        - topic: "первые космические миссии"
        - instructions: "с упоминанием имен"
        
        Returns:
            (topic, instructions) - тема и дополнительные инструкции
        """
        import re
        
        # Паттерны инструкций которые нужно извлечь
        instruction_patterns = [
            # Русские паттерны
            r'с упоминанием\s+[\w\s]+',  # "с упоминанием имен и историй"
            r'с конкретными\s+[\w\s]+',  # "с конкретными фактами"
            r'с примерами\s*[\w\s]*',    # "с примерами"
            r'с деталями\s*[\w\s]*',     # "с деталями"
            r'с датами\s*[\w\s]*',       # "с датами"
            r'с именами\s*[\w\s]*',      # "с именами"
            r'подробно\s*[\w\s]*',       # "подробно"
            r'детально\s*[\w\s]*',       # "детально"
            r'простыми словами',         # "простыми словами"
            r'для детей',                # "для детей"
            r'для взрослых',             # "для взрослых"
            
            # Английские паттерны
            r'with specific\s+[\w\s]+',  # "with specific facts"
            r'with examples\s*[\w\s]*',  # "with examples"
            r'with details\s*[\w\s]*',   # "with details"
            r'in simple terms',          # "in simple terms"
        ]
        
        # Паттерны команд в начале которые нужно убрать
        command_patterns = [
            # Русские команды
            r'^расскажи\s+(?:мне\s+)?(?:про|о|об)\s+',
            r'^объясни\s+(?:мне\s+)?(?:про|о|об|как)?\s*',
            r'^сделай\s+(?:видео\s+)?(?:про|о|об)\s+',
            r'^создай\s+(?:видео\s+)?(?:про|о|об)\s+',
            r'^напиши\s+(?:про|о|об)\s+',
            r'^покажи\s+(?:мне\s+)?(?:про|о|об)?\s*',
            
            # Английские команды
            r'^tell\s+(?:me\s+)?about\s+',
            r'^explain\s+(?:me\s+)?(?:about|how)?\s*',
            r'^make\s+(?:a\s+)?(?:video\s+)?about\s+',
            r'^create\s+(?:a\s+)?(?:video\s+)?about\s+',
            r'^show\s+(?:me\s+)?(?:about)?\s*',
        ]
        
        theme = raw_theme.strip()
        instructions = []
        
        # 1. Извлекаем инструкции
        for pattern in instruction_patterns:
            match = re.search(pattern, theme, re.IGNORECASE)
            if match:
                instructions.append(match.group().strip())
                theme = theme.replace(match.group(), '').strip()
        
        # 2. Убираем команды из начала
        for pattern in command_patterns:
            theme = re.sub(pattern, '', theme, flags=re.IGNORECASE).strip()
        
        # 3. Очищаем тему от лишних пробелов и знаков
        theme = re.sub(r'\s+', ' ', theme).strip()
        theme = theme.strip('.,!?;:')
        
        # 4. Формируем строку инструкций
        instructions_str = '. '.join(instructions) if instructions else ''
        
        return theme, instructions_str
    
    @staticmethod
    def create_viral_prompt(
        theme: str,
        duration: float,
        language: str = 'Russian',
        persona_id: str = 'viral',
        use_chain_of_thought: bool = True,
        generate_multiple_hooks: bool = True,
        enable_seamless_loop: bool = True,
        enable_comment_bait: bool = False
    ) -> str:
        """
        🚀 ГЛАВНАЯ ФУНКЦИЯ
        
        Создает вирусный промпт с применением всех технологий.
        
        Args:
            theme: Тема видео
            duration: Длительность в секундах
            language: Язык
            persona_id: ID персоны (conspiracy, motivator, horror, bro, viral, serious)
            use_chain_of_thought: Использовать Chain of Thought
            generate_multiple_hooks: Генерировать 5 хуков и выбирать лучший
            enable_seamless_loop: Бесконечная петля (конец → начало)
            enable_comment_bait: Добавить спорное утверждение для комментариев
        
        Returns:
            Промпт для Gemini
        """
        
        # 🎯 НОВОЕ: Разделяем тему и инструкции пользователя
        clean_topic, user_instructions = ViralTextSystem.extract_topic_and_instructions(theme)
        
        # Получаем персону
        persona = ViralTextSystem.PERSONAS.get(persona_id, ViralTextSystem.PERSONAS['viral'])
        is_serious = persona_id == 'serious'
        if is_serious:
            generate_multiple_hooks = False
            enable_comment_bait = False
        
        # Рассчитываем количество слов (скорость речи зависит от языка)
        # В русском языке слова длиннее, скорость чтения медленнее (около 115 слов в минуту)
        is_ru = 'rus' in language.lower() or 'рус' in language.lower()
        words_per_minute = 115 if is_ru else 145
        
        target_words = int((duration / 60) * words_per_minute)
        
        # Жёсткие лимиты чтобы текст не выходил за длительность Shorts
        if duration <= 60:
            # Строгий лимит для Shorts (до 60с)
            max_words_for_duration = int((duration / 60) * words_per_minute)
            target_words = max(15, min(target_words, max_words_for_duration))
        elif duration <= 180:  # До 3 минут
            target_words = max(15, min(target_words, 500))
        else:
            target_words = max(15, target_words)  # Без верхнего лимита для длинных видео
        
        # Языковые инструкции (усиленные для предотвращения смешивания языков)
        language_instruction = {
            'Russian': 'Write ONLY in RUSSIAN language (русский язык, кириллица). STRICTLY NO Ukrainian, NO English, NO other languages. Use only Russian vocabulary and grammar.',
            'English': 'Write ONLY in ENGLISH. No other languages.',
            'Spanish': 'Write ONLY in SPANISH (Español). No other languages.',
            'French': 'Write ONLY in FRENCH (Français). No other languages.',
            'German': 'Write ONLY in GERMAN (Deutsch). No other languages.',
            'Ukrainian': 'Write ONLY in UKRAINIAN (українська мова). STRICTLY NO Russian, NO English.',
        }.get(language, f'Write ONLY in {language}. No other languages.')
        
        # Строим промпт
        prompt_parts = []
        
        # Формируем блок дополнительных инструкций если есть
        extra_instructions = ""
        if user_instructions:
            extra_instructions = f"""
USER REQUIREMENTS (MUST FOLLOW):
{user_instructions}
- Include SPECIFIC names, dates, facts as requested
- DO NOT repeat the topic phrase literally in the text
- Tell the STORY naturally, don't announce what you're doing
"""
        
        # Заголовок
        task_type = "clear documentary-style YouTube Shorts script" if is_serious else "VIRAL YouTube Shorts script"
        prompt_parts.append(f"""
TASK: Create a {task_type} about "{clean_topic}".
Duration: {duration} seconds (~{target_words} words)
Language: {language_instruction}
Persona: {persona['name']}
{extra_instructions}

⚠️ CRITICAL ANTI-REPETITION RULE:
- NEVER repeat the topic "{clean_topic}" word-for-word in your script
- Instead of saying "Первые космические миссии были важными"
- Say "12 апреля 1961 года Гагарин впервые облетел Землю на корабле Восток-1"
- Tell SPECIFIC stories, don't announce the topic!
- The viewer already knows the topic from the title
""")
        
        # 🧠 ТЕХНОЛОГИЯ 1: Chain of Thought
        if use_chain_of_thought:
            if is_serious:
                prompt_parts.append("""[Choose the clearest, most accurate educational angle. Prefer verified facts over shock value.]""")
            else:
                prompt_parts.append("""[Choose a strong, concrete, non-trivial angle. Skip boring facts and fake shock.]""")
        
        # 🎣 ТЕХНОЛОГИЯ 2: Multiple Hooks
        if generate_multiple_hooks:
            prompt_parts.append("""[Start with a highly viral hook (Question, Negative, or Story). DO NOT use percentages like "99%" or "Most people".]""")
        
        # Основные правила написания
        if is_serious:
            prompt_parts.append(f"""
RULES:
1. NO INTROS ("Hi", "Welcome"). Start with a precise fact or calm question.
2. Use measured, documentary narration.
3. 100% {language} ONLY. NO translated English slang.
4. Avoid sensationalism: no ALL CAPS, no excessive exclamation marks, no fake urgency.
5. Keep sentences clear and factual (max 14 words).
Structure: Precise fact -> Context -> Explanation -> Key takeaway
""")
        else:
            prompt_parts.append(f"""
RULES:
1. NO INTROS ("Hi", "Welcome"). Action immediately.
2. Short, punchy sentences (max 10 words).
3. 100% {language} ONLY. NO translated English slang.
4. Use plain intensity: concrete verbs, short sentences, at most one exclamation mark.
Structure: Hook -> Rapid Context -> Twist -> Mind-blown Payoff
""")

        prompt_parts.append("""
SHORTS FEED RETENTION RULES:
- Optimize for the Shorts feed, not search. The first frame and first line must work without context.
- First 1.5 seconds: name the concrete subject plus a choice, conflict, action, cost, or visible change.
- Avoid lazy mystery packaging: no generic "SECRET", "HIDDEN", "SHOCK", "they hid it", or ALL CAPS unless the source literally proves concealment.
- For 25-35 second videos, use 4 tight beats: hook -> context -> turning point -> payoff. No long setup.
- Every 4-6 seconds, add a new concrete beat: action, consequence, number, place, date, object, or visual detail.
- Write for watch time: remove filler, repeated topic phrases, and soft transitions like "let's find out".
- Title should be sentence case with normal punctuation. Prefer "X refused/chose/risked/saved Y" or a direct question over vague intrigue.
""")

        prompt_parts.append(build_subscribe_cta_instruction(language, video_kind="short"))
        
        # Добавляем инструкции персоны
        prompt_parts.append(f"""
PERSONA STYLE GUIDE:
{persona['system_instruction']}
""")
        
        # 🔄 ТЕХНОЛОГИЯ 5: Seamless Loop (Бесконечная петля)
        if enable_seamless_loop and duration <= 60:
            prompt_parts.append("""[SEAMLESS LOOP]: Make the last sentence flow naturally into the first sentence for infinite looping.""")
        
        # 🎣 ТЕХНОЛОГИЯ 6: вопросы для комментариев
        if enable_comment_bait:
            prompt_parts.append("""
🎣 HONEST COMMENT PROMPT:
- Include at most ONE natural opinion, choice, or open question related to the exact topic.
- Never plant a false fact, deliberate mistake, fake quote, fake document, or invented lore to provoke corrections.
- Keep it subtle and place it after the main evidence, not in the opening hook.
- If the subject is uncertain, explicitly call it a theory or interpretation.
""")
        
        # ⚔️ ТЕХНОЛОГИЯ 7: Battle Override (Боевой Перехват)
        # Детектим темы про бой/драку/VS и запрещаем аналитику
        battle_keywords = ['vs', 'против', 'бой', 'битв', 'сражен', 'драка', 'fight', 'battle', 'combat', 'duel', 'мма', 'ufc']
        is_battle = any(kw in theme.lower() for kw in battle_keywords)
        
        if is_battle and not is_serious:
            prompt_parts.append("""
⚔️ CRITICAL BATTLE OVERRIDE ACTIVATED:
I detected this is a FIGHT/BATTLE scenario ("VS").

⛔ STOP ANALYZING!
- NO "Let's compare stats"
- NO "Historically speaking"
- NO "It is unlikely that..."
- NO "Who would win?" analysis
- NO boring comparisons

✅ START NARRATING!
- Treat this as a LIVE ACTION MOVIE scene or UFC commentary
- Use PRESENT TENSE action verbs: "Lunges", "Strikes", "Parries", "Screams"
- Describe the IMPACT: "Steel meets steel", "The ground shakes"
- Focus on sensory details: Sound, Pain, Speed, Blood
- Make it VISCERAL and BLOOD-PUMPING

EXAMPLE OF WHAT I WANT:
"Сталин выхватывает кинжал! Саурон смеется и замахивается булавой. УДАР! Искры летят во все стороны. Вождь уходит перекатом, земля дрожит под ногами..."

NOT THIS (BORING):
"Сталин был лидером СССР, а Саурон магом. В бою победил бы тот, кто..."

MAKE IT VISCERAL. MAKE IT BLOOD-PUMPING. ACTION ONLY!
NO ANALYSIS. PURE COMBAT NARRATION.
""")
        
        # Формат вывода
        title_instruction = "informative title, no ALL CAPS, no exaggerated promises" if is_serious else "high-retention title, sentence case, concrete conflict or action, no ALL CAPS"
        markup_instruction = "- Avoid ALL CAPS and use at most one exclamation mark in the whole script" if is_serious else "- Avoid ALL CAPS, fake urgency, and more than one exclamation mark"
        ending_instruction = "educational takeaway" if is_serious else "mind-blown payoff"

        title_architecture = subject_first_prompt_rules(language)
        prompt_parts.append(f"""
OUTPUT FORMAT (JSON):
{{
    "clickbait_title": "{title_instruction}",
    "description": "Brief description with hook",
    "full_text": "The complete script as ONE continuous text (not array)",
    "hashtags": ["#viral", "#shorts", "#trending", ...]
}}

IMPORTANT:
- full_text must be ONE STRING, not an array
- {title_architecture}
- {markup_instruction}
- STRICT LIMIT: MAX {target_words + 10} words total! Do not write more.
- {language_instruction}
- Follow the {persona['name']} style strictly
- End with a clear {ending_instruction}
{"- MUST create seamless loop (end → start)" if enable_seamless_loop and duration <= 60 else ""}
{"- MUST include ONE subtle comment prompt" if enable_comment_bait else ""}

Now create the script:
""")
        
        # Собираем промпт
        final_prompt = "".join(prompt_parts)
        
        # Валидация длины промпта (Gemini limit ~32k tokens, ~128k chars)
        MAX_PROMPT_LENGTH = 100000  # 100k символов для безопасности
        if len(final_prompt) > MAX_PROMPT_LENGTH:
            # Обрезаем менее важные части
            # Убираем примеры из CLIFFHANGERS и RETENTION_TECHNIQUES
            final_prompt = final_prompt[:MAX_PROMPT_LENGTH]
            final_prompt += "\n\n[Промпт обрезан для соответствия лимиту API]\n"
        
        return final_prompt
    
    @staticmethod
    def get_recommended_persona(theme: str) -> str:
        """
        Рекомендует персону на основе темы
        
        Args:
            theme: Тема видео
        
        Returns:
            ID рекомендуемой персоны
        """
        theme_lower = theme.lower()
        
        # ⚔️ Комментатор (ПРИОРИТЕТ #1 - проверяем первым!)
        if any(word in theme_lower for word in [
            'vs', 'против', 'бой', 'битв', 'сражен', 'драка', 'дуэль',
            'fight', 'battle', 'combat', 'duel', 'mma', 'мма', 'ufc', 'бокс', 'ринг'
        ]):
            return 'commentator'
        
        # Конспиролог
        if any(word in theme_lower for word in [
            'тайна', 'секрет', 'заговор', 'нло', 'пирамид', 'загадк',
            'mystery', 'secret', 'conspiracy', 'ufo', 'pyramid', 'hidden'
        ]):
            return 'conspiracy'
        
        # 💰 Предприниматель (проверяем ПЕРЕД мотиватором - более специфичный)
        if any(word in theme_lower for word in [
            'заработок', 'инвестиц', 'стартап', 'бизнес-план', 'прибыль', 'доход', 'капитал',
            'earning', 'investment', 'startup', 'profit', 'revenue', 'income', 'capital',
            'как заработать', 'как сделать деньги', 'заработать миллион'
        ]):
            return 'hustler'
        
        # Мотиватор
        if any(word in theme_lower for word in [
            'успех', 'бизнес', 'деньги', 'богат', 'мотивац', 'спорт', 'миллион',
            'success', 'business', 'money', 'rich', 'motivation', 'sport', 'million'
        ]):
            return 'motivator'
        
        # Хоррор
        if any(word in theme_lower for word in [
            'ужас', 'страх', 'маньяк', 'убийц', 'крипи', 'мистик', 'страшн', 'жутк',
            'horror', 'fear', 'killer', 'murder', 'creepy', 'scary', 'terrifying'
        ]):
            return 'horror'
        
        # 🎓 Профессор (новая персона)
        if any(word in theme_lower for word in [
            'наука', 'технолог', 'физика', 'химия', 'математика', 'квантов', 'теория',
            'science', 'technology', 'physics', 'chemistry', 'math', 'quantum', 'theory',
            'как работает', 'почему работает', 'принцип', 'механизм'
        ]):
            return 'professor'
        
        # 🎪 Шоумен (новая персона)
        if any(word in theme_lower for word in [
            'прикол', 'смешн', 'юмор', 'мем', 'развлечен', 'весел', 'забавн',
            'funny', 'humor', 'meme', 'entertainment', 'fun', 'hilarious', 'joke'
        ]):
            return 'entertainer'
        
        # 🔬 Следователь (новая персона)
        if any(word in theme_lower for word in [
            'расследован', 'разоблачен', 'правда', 'ложь', 'обман', 'факт-чек', 'анализ',
            'investigation', 'expose', 'truth', 'lie', 'fake', 'fact-check', 'analysis'
        ]):
            return 'investigator'
        
        # 🌟 Сказитель (новая персона)
        if any(word in theme_lower for word in [
            'история', 'биография', 'жизнь', 'судьба', 'путь', 'драма', 'любовь',
            'story', 'biography', 'life', 'destiny', 'journey', 'drama', 'love'
        ]):
            return 'storyteller'
        
        # Серьезный
        if any(word in theme_lower for word in [
            'исследован', 'открыти', 'изобретен', 'докумен',
            'research', 'discovery', 'invention', 'documentary'
        ]):
            return 'serious'
        
        # Друган (для фактов и объяснений)
        if any(word in theme_lower for word in [
            'факт', 'объяснен', 'лайфхак',
            'fact', 'explain', 'lifehack'
        ]):
            return 'bro'
        
        # По умолчанию - вирусный
        return 'viral'

    @staticmethod
    def get_retention_technique_prompt(techniques: list) -> str:
        """
        Генерирует инструкции для использования конкретных retention-техник.
        
        Args:
            techniques: Список ID техник из RETENTION_TECHNIQUES
                       ('open_loop', 'micro_payoff', 'contrast', 'myth_busting',
                        'mistake_framing', 'loss_aversion', 'specificity', 
                        'identity', 'challenge')
            
        Returns:
            Инструкции для промпта
        """
        if not techniques:
            return ""
        
        instructions = ["\n🎯 USE THESE RETENTION TECHNIQUES:"]
        
        for tech_id in techniques:
            tech = ViralTextSystem.RETENTION_TECHNIQUES.get(tech_id)
            if tech:
                examples = ", ".join(tech['examples'][:2])
                instructions.append(f"- {tech['name']}: {tech['description']} (например: {examples})")
        
        return "\n".join(instructions)


# Пример использования
if __name__ == "__main__":
    # Тест 1: Конспиролог
    prompt1 = ViralTextSystem.create_viral_prompt(
        theme="Тайны египетских пирамид",
        duration=60,
        language="Russian",
        persona_id="conspiracy"
    )
    print("=== КОНСПИРОЛОГ ===")
    print(prompt1[:500])
    print()
    
    # Тест 2: Мотиватор
    prompt2 = ViralTextSystem.create_viral_prompt(
        theme="Как стать миллионером",
        duration=60,
        language="Russian",
        persona_id="motivator"
    )
    print("=== МОТИВАТОР ===")
    print(prompt2[:500])
    print()
    
    # Тест 3: Автоматическая рекомендация
    theme = "Секреты НЛО"
    recommended = ViralTextSystem.get_recommended_persona(theme)
    print("=== РЕКОМЕНДАЦИЯ ===")
    print(f"Тема: {theme}")
    print(f"Рекомендуемая персона: {recommended}")
    print(f"Название: {ViralTextSystem.PERSONAS[recommended]['name']}")
