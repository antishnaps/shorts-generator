#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
💬 ИНТЕРАКТИВНЫЕ ЭЛЕМЕНТЫ ДЛЯ ДЛИННЫХ ВИДЕО
Автоматическое добавление вопросов, призывов к действию и точек взаимодействия
"""

from typing import List, Dict
import random

from core.subtitle_text import split_subtitle_units


class EngagementInjector:
    """Добавляет интерактивные элементы в длинные видео"""
    
    # Минимальная длительность для добавления элементов
    MIN_DURATION = 60  # 1 минута
    
    # Интервал между элементами (секунды)
    ENGAGEMENT_INTERVAL = 120  # 2 минуты

    @staticmethod
    def engagement_interval(duration: float) -> int:
        """Scale interaction frequency so long documentaries do not feel spammy."""
        duration = float(duration or 0)
        if duration >= 2 * 60 * 60:
            return 10 * 60
        if duration >= 60 * 60:
            return 8 * 60
        if duration >= 30 * 60:
            return 5 * 60
        return EngagementInjector.ENGAGEMENT_INTERVAL
    
    # Типы интерактивных элементов для всех языков
    ENGAGEMENT_TYPES = {
        'question': {
            'Russian': [
                "А вы как думаете?",
                "Что бы вы сделали в такой ситуации?",
                "Согласны с этим?",
                "Как вы считаете, это правда?",
                "Вы когда-нибудь сталкивались с этим?",
                "Напишите в комментариях ваше мнение!",
                "Как вы относитесь к этому?",
                "Вы знали об этом?"
            ],
            'English': [
                "What do you think?",
                "What would you do in this situation?",
                "Do you agree with this?",
                "Do you think this is true?",
                "Have you ever experienced this?",
                "Write your opinion in the comments!",
                "How do you feel about this?",
                "Did you know about this?"
            ],
            'Spanish': [
                "¿Qué opinas?",
                "¿Qué harías en esta situación?",
                "¿Estás de acuerdo?",
                "¿Crees que esto es verdad?",
                "¿Alguna vez has experimentado esto?",
                "¡Escribe tu opinión en los comentarios!",
                "¿Qué piensas de esto?",
                "¿Sabías esto?"
            ],
            'French': [
                "Qu'en pensez-vous?",
                "Que feriez-vous dans cette situation?",
                "Êtes-vous d'accord?",
                "Pensez-vous que c'est vrai?",
                "Avez-vous déjà vécu cela?",
                "Écrivez votre avis dans les commentaires!",
                "Comment vous sentez-vous à ce sujet?",
                "Le saviez-vous?"
            ],
            'German': [
                "Was denken Sie?",
                "Was würden Sie in dieser Situation tun?",
                "Stimmen Sie zu?",
                "Glauben Sie, dass das wahr ist?",
                "Haben Sie das schon erlebt?",
                "Schreiben Sie Ihre Meinung in die Kommentare!",
                "Wie stehen Sie dazu?",
                "Wussten Sie das?"
            ],
            'Chinese': [
                "你怎么看？",
                "在这种情况下你会怎么做？",
                "你同意吗？",
                "你认为这是真的吗？",
                "你有过这样的经历吗？",
                "在评论中写下你的看法！",
                "你对此有何感想？",
                "你知道这个吗？"
            ],
            'Japanese': [
                "どう思いますか？",
                "この状況であなたならどうしますか？",
                "同意しますか？",
                "これは本当だと思いますか？",
                "こんな経験はありますか？",
                "コメントであなたの意見を書いてください！",
                "これについてどう感じますか？",
                "知っていましたか？"
            ],
            'Korean': [
                "어떻게 생각하세요?",
                "이 상황에서 어떻게 하시겠어요?",
                "동의하시나요?",
                "이게 사실이라고 생각하세요?",
                "이런 경험이 있으신가요?",
                "댓글에 의견을 남겨주세요!",
                "이것에 대해 어떻게 느끼세요?",
                "알고 계셨나요?"
            ],
            'Portuguese': [
                "O que você acha?",
                "O que você faria nessa situação?",
                "Você concorda?",
                "Você acha que isso é verdade?",
                "Você já passou por isso?",
                "Escreva sua opinião nos comentários!",
                "Como você se sente sobre isso?",
                "Você sabia disso?"
            ],
            'Italian': [
                "Cosa ne pensi?",
                "Cosa faresti in questa situazione?",
                "Sei d'accordo?",
                "Pensi che sia vero?",
                "Ti è mai successo?",
                "Scrivi la tua opinione nei commenti!",
                "Come la vedi?",
                "Lo sapevi?"
            ],
            'Hindi': [
                "आप क्या सोचते हैं?",
                "आप इस स्थिति में क्या करते?",
                "क्या आप सहमत हैं?",
                "क्या आपको लगता है कि यह सच है?",
                "क्या आपके साथ कभी ऐसा हुआ है?",
                "टिप्पणियों में अपनी राय लिखें!",
                "आप इसे कैसे देखते हैं?",
                "क्या आप यह जानते थे?"
            ],
            'Arabic': [
                "ما رأيك؟",
                "ماذا كنت ستفعل في هذا الموقف؟",
                "هل توافق؟",
                "هل تعتقد أن هذا صحيح؟",
                "هل مررت بهذا من قبل؟",
                "اكتب رأيك في التعليقات!",
                "كيف ترى هذا الأمر؟",
                "هل كنت تعرف ذلك؟"
            ]
        },
        'cta': {
            'Russian': [
                "Подпишитесь, чтобы не пропустить продолжение!",
                "Ставьте лайк, если вам интересно!",
                "Поделитесь этим видео с друзьями!",
                "Не забудьте подписаться на канал!",
                "Включите уведомления, чтобы не пропустить новые видео!",
                "Оставьте комментарий - мне важно ваше мнение!",
                "Поддержите канал лайком!"
            ],
            'English': [
                "Subscribe to not miss the continuation!",
                "Like if you find this interesting!",
                "Share this video with friends!",
                "Don't forget to subscribe!",
                "Turn on notifications for new videos!",
                "Leave a comment - your opinion matters!",
                "Support the channel with a like!"
            ],
            'Spanish': [
                "¡Suscríbete para no perderte la continuación!",
                "¡Dale like si te parece interesante!",
                "¡Comparte este video con tus amigos!",
                "¡No olvides suscribirte!",
                "¡Activa las notificaciones para nuevos videos!",
                "¡Deja un comentario - tu opinión importa!",
                "¡Apoya el canal con un like!"
            ],
            'French': [
                "Abonnez-vous pour ne pas manquer la suite!",
                "Likez si vous trouvez ça intéressant!",
                "Partagez cette vidéo avec vos amis!",
                "N'oubliez pas de vous abonner!",
                "Activez les notifications pour les nouvelles vidéos!",
                "Laissez un commentaire - votre avis compte!",
                "Soutenez la chaîne avec un like!"
            ],
            'German': [
                "Abonnieren Sie, um die Fortsetzung nicht zu verpassen!",
                "Liken Sie, wenn Sie das interessant finden!",
                "Teilen Sie dieses Video mit Freunden!",
                "Vergessen Sie nicht zu abonnieren!",
                "Aktivieren Sie Benachrichtigungen für neue Videos!",
                "Hinterlassen Sie einen Kommentar - Ihre Meinung zählt!",
                "Unterstützen Sie den Kanal mit einem Like!"
            ],
            'Chinese': [
                "订阅以免错过后续内容！",
                "如果你觉得有趣就点赞！",
                "与朋友分享这个视频！",
                "别忘了订阅！",
                "打开通知以获取新视频！",
                "留下评论 - 你的意见很重要！",
                "点赞支持频道！"
            ],
            'Japanese': [
                "続きを見逃さないように登録してください！",
                "面白いと思ったらいいねしてください！",
                "友達とこの動画をシェアしてください！",
                "チャンネル登録をお忘れなく！",
                "新しい動画の通知をオンにしてください！",
                "コメントを残してください - あなたの意見は大切です！",
                "いいねでチャンネルを応援してください！"
            ],
            'Korean': [
                "계속 보시려면 구독하세요!",
                "재미있으시면 좋아요를 눌러주세요!",
                "친구들과 이 영상을 공유하세요!",
                "구독하는 것을 잊지 마세요!",
                "새 영상 알림을 켜주세요!",
                "댓글을 남겨주세요 - 여러분의 의견이 중요합니다!",
                "좋아요로 채널을 응원해주세요!"
            ],
            'Portuguese': [
                "Inscreva-se para não perder a continuação!",
                "Curta se você achar interessante!",
                "Compartilhe este vídeo com amigos!",
                "Não esqueça de se inscrever!",
                "Ative as notificações para novos vídeos!",
                "Deixe um comentário - sua opinião importa!",
                "Apoie o canal com um like!"
            ],
            'Italian': [
                "Iscriviti per non perdere il seguito!",
                "Metti mi piace se ti interessa!",
                "Condividi questo video con gli amici!",
                "Non dimenticare di iscriverti!",
                "Attiva le notifiche per i nuovi video!",
                "Lascia un commento: la tua opinione conta!",
                "Sostieni il canale con un mi piace!"
            ],
            'Hindi': [
                "आगे की कहानी न चूकने के लिए चैनल को सब्सक्राइब करें!",
                "दिलचस्प लगे तो लाइक करें!",
                "इस वीडियो को दोस्तों के साथ साझा करें!",
                "चैनल को सब्सक्राइब करना न भूलें!",
                "नए वीडियो के लिए सूचनाएँ चालू करें!",
                "टिप्पणी करें—आपकी राय महत्वपूर्ण है!",
                "लाइक करके चैनल का समर्थन करें!"
            ],
            'Arabic': [
                "اشترك كي لا يفوتك الجزء التالي!",
                "اضغط إعجاب إذا وجدت المحتوى ممتعًا!",
                "شارك هذا الفيديو مع أصدقائك!",
                "لا تنس الاشتراك في القناة!",
                "فعّل الإشعارات للفيديوهات الجديدة!",
                "اترك تعليقًا، فرأيك مهم!",
                "ادعم القناة بالإعجاب!"
            ]
        },
        'teaser': {
            'Russian': [
                "Но это ещё не всё...",
                "Дальше будет ещё интереснее!",
                "Самое интересное впереди!",
                "Подождите, это ещё не конец!",
                "А теперь самое главное...",
                "Но есть один нюанс...",
                "Сейчас я расскажу самое важное!"
            ],
            'English': [
                "But that's not all...",
                "It gets even more interesting!",
                "The best part is coming!",
                "Wait, this isn't the end!",
                "And now the most important part...",
                "But there's one catch...",
                "Now I'll tell you the most important thing!"
            ],
            'Spanish': [
                "Pero eso no es todo...",
                "¡Se pone aún más interesante!",
                "¡Lo mejor está por venir!",
                "¡Espera, esto no es el final!",
                "Y ahora lo más importante...",
                "Pero hay un detalle...",
                "¡Ahora te contaré lo más importante!"
            ],
            'French': [
                "Mais ce n'est pas tout...",
                "Ça devient encore plus intéressant!",
                "Le meilleur est à venir!",
                "Attendez, ce n'est pas la fin!",
                "Et maintenant le plus important...",
                "Mais il y a un détail...",
                "Maintenant je vais vous dire le plus important!"
            ],
            'German': [
                "Aber das ist noch nicht alles...",
                "Es wird noch interessanter!",
                "Das Beste kommt noch!",
                "Warten Sie, das ist noch nicht das Ende!",
                "Und jetzt das Wichtigste...",
                "Aber es gibt einen Haken...",
                "Jetzt erzähle ich Ihnen das Wichtigste!"
            ],
            'Chinese': [
                "但这还不是全部...",
                "接下来会更有趣！",
                "最精彩的部分即将到来！",
                "等等，这还没结束！",
                "现在是最重要的部分...",
                "但有一个问题...",
                "现在我要告诉你最重要的事情！"
            ],
            'Japanese': [
                "でもこれだけじゃない...",
                "もっと面白くなります！",
                "一番いいところはこれから！",
                "待って、まだ終わりじゃない！",
                "そして今、最も重要なこと...",
                "でも一つ問題が...",
                "今から一番大事なことを話します！"
            ],
            'Korean': [
                "하지만 이게 전부가 아닙니다...",
                "더 흥미로워집니다!",
                "가장 좋은 부분이 다가옵니다!",
                "잠깐, 아직 끝이 아닙니다!",
                "그리고 이제 가장 중요한 것...",
                "하지만 한 가지 문제가...",
                "지금 가장 중요한 것을 말씀드리겠습니다!"
            ],
            'Portuguese': [
                "Mas isso não é tudo...",
                "Fica ainda mais interessante!",
                "A melhor parte está chegando!",
                "Espere, isso não é o fim!",
                "E agora o mais importante...",
                "Mas há um detalhe...",
                "Agora vou contar o mais importante!"
            ],
            'Italian': [
                "Ma non è tutto...",
                "Ora diventa ancora più interessante!",
                "La parte migliore deve ancora arrivare!",
                "Aspetta, non è ancora finita!",
                "E ora il punto più importante...",
                "Ma c'è un dettaglio...",
                "Adesso arriva la parte essenziale!"
            ],
            'Hindi': [
                "लेकिन बात यहीं खत्म नहीं होती...",
                "आगे यह और भी दिलचस्प हो जाता है!",
                "सबसे महत्वपूर्ण हिस्सा अभी बाकी है!",
                "रुकिए, यह अभी समाप्त नहीं हुआ!",
                "और अब सबसे अहम बात...",
                "लेकिन इसमें एक महत्वपूर्ण पहलू है...",
                "अब ध्यान दीजिए, असली बात यही है!"
            ],
            'Arabic': [
                "لكن هذا ليس كل شيء...",
                "والآن يصبح الأمر أكثر إثارة!",
                "أفضل جزء لم يأتِ بعد!",
                "انتظر، لم تنتهِ القصة بعد!",
                "والآن نصل إلى النقطة الأهم...",
                "لكن هناك تفصيل مهم...",
                "والآن إليك أهم ما في الأمر!"
            ]
        }
    }
    
    @staticmethod
    def should_inject_engagement(duration: float) -> bool:
        """Определяет нужны ли интерактивные элементы"""
        return duration >= EngagementInjector.MIN_DURATION
    
    @staticmethod
    def generate_engagement_points(
        duration: float,
        language: str = 'Russian',
        chapters: List[Dict] = None,
        include_subscription_cta: bool = True,
    ) -> List[Dict]:
        """
        Генерирует точки взаимодействия для видео
        
        Args:
            duration: Длительность видео (секунды)
            language: Язык ('Russian' или 'English')
            chapters: Опциональный список глав
            
        Returns:
            [
                {"time": 60, "type": "question", "text": "А вы как думаете?"},
                {"time": 180, "type": "cta", "text": "Подпишитесь!"},
                ...
            ]
        """
        if not EngagementInjector.should_inject_engagement(duration):
            return []
        
        points = []
        
        # Определяем позиции для элементов
        if chapters and len(chapters) > 1:
            # Используем главы как ориентир
            positions = []
            for i, ch in enumerate(chapters):
                if i > 0:  # Пропускаем первую главу
                    positions.append(ch.get('start_time', 0))
        else:
            # Автоматические позиции каждые 2 минуты
            positions = []
            interval = EngagementInjector.engagement_interval(duration)
            current = interval
            while current < duration - 30:  # Не добавляем в последние 30s
                positions.append(current)
                current += interval
        
        # Создаём элементы для каждой позиции
        for i, time in enumerate(positions):
            position_ratio = time / duration
            
            # Выбираем тип элемента в зависимости от позиции
            if position_ratio < 0.3:
                # Начало - вопросы для вовлечения
                element_type = 'question'
            elif position_ratio < 0.7:
                # Середина - тизеры для удержания
                element_type = 'teaser'
            else:
                # Конец - призывы к действию
                element_type = 'cta'

            if element_type == 'cta' and not include_subscription_cta:
                element_type = 'teaser'
            
            # Выбираем случайный текст
            texts = EngagementInjector.ENGAGEMENT_TYPES[element_type].get(
                language,
                EngagementInjector.ENGAGEMENT_TYPES[element_type]['English']  # Fallback на английский
            )
            text = random.choice(texts)
            
            points.append({
                'time': time,
                'type': element_type,
                'text': text,
                'position': position_ratio,
                'language': language,
            })
        
        return points
    
    @staticmethod
    def inject_into_text(
        text: str,
        engagement_points: List[Dict],
        words_per_second: float = 2.5
    ) -> str:
        """
        Внедряет интерактивные элементы в текст
        
        Args:
            text: Исходный текст
            engagement_points: Точки взаимодействия из generate_engagement_points
            words_per_second: Скорость речи (слов в секунду)
            
        Returns:
            Текст с внедрёнными элементами
        """
        if not engagement_points:
            return text
        
        units, separator = split_subtitle_units(text)
        if not units:
            return text
        compact = separator == ''
        total_units = len(units)
        
        # Сортируем точки по времени
        sorted_points = sorted(engagement_points, key=lambda x: x['time'])
        
        # Внедряем элементы
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
            
            # Добавляем интерактивный элемент
            if compact and result_units and not str(result_units[-1]).endswith(('。', '！', '？', '.', '!', '?')):
                language = point.get('language')
                result_units.append('。' if language in {'Chinese', 'Japanese'} else '.')
            result_units.append(point['text'])
            
            last_insert_pos = unit_position
        
        # Добавляем оставшиеся слова
        result_units.extend(units[last_insert_pos:])
        
        return separator.join(result_units)
