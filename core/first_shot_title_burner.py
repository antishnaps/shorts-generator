#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🖼️ First Shot Title Burner (Pillow Edition)
Выжигает название на первом шоте из пула.

Использует Pillow (PIL) для рендеринга красивого бокса с текстом:
- Единый скруглённый прямоугольник вокруг всего текста
- Белый полупрозрачный фон (85%)
- Чёрный текст по центру
- Тень за боксом для глубины
- Поддержка многострочного текста
"""

from pathlib import Path
from typing import Tuple, List
import logging

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logging.warning("Pillow не установлен — выжигание названия будет недоступно")


class FirstShotTitleBurner:
    """Система выжигания названий на первом шоте (Pillow)"""

    # ─── Настройки стилей ───────────────────────────────────────
    STYLE_SHORT = {  # До 12 символов (Борщ, Моти, Чизкейк)
        'font_size': 120,
        'box_padding_x': 60,
        'box_padding_y': 40,
        'box_radius': 24,
        'max_width_percent': 0.85,
        'position_y_percent': 0.48,   # Центр бокса на 48% высоты
    }

    STYLE_MEDIUM = {  # 12-25 символов (Морковный чизкейк)
        'font_size': 90,
        'box_padding_x': 50,
        'box_padding_y': 35,
        'box_radius': 20,
        'max_width_percent': 0.90,
        'position_y_percent': 0.48,
    }

    STYLE_LONG = {  # 25+ символов (Воздушные нежные профитроли)
        'font_size': 70,
        'box_padding_x': 45,
        'box_padding_y': 30,
        'box_radius': 18,
        'max_width_percent': 0.95,
        'position_y_percent': 0.48,
    }

    # ─── Пути к шрифтам (по приоритету) ─────────────────────────
    FONT_PATHS = [
        'C:/Windows/Fonts/arial.ttf',
        'C:/Windows/Fonts/arialbd.ttf',
        'C:/Windows/Fonts/verdana.ttf',
        'C:/Windows/Fonts/tahoma.ttf',
        'C:/Windows/Fonts/segoeui.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',      # Linux
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/System/Library/Fonts/Helvetica.ttc',                    # macOS
    ]

    # ─── Визуальные настройки ───────────────────────────────────
    BOX_COLOR = (255, 255, 255, 217)     # Белый, 85% непрозрачность
    TEXT_COLOR = (30, 30, 30, 255)       # Почти чёрный текст
    SHADOW_COLOR = (0, 0, 0, 50)         # Лёгкая тень за боксом
    SHADOW_OFFSET = (4, 4)               # Смещение тени
    BORDER_COLOR = (200, 200, 200, 180)  # Тонкая серая обводка бокса
    BORDER_WIDTH = 2

    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _get_style_for_title(title: str) -> dict:
        """Определяет стиль на основе длины названия"""
        length = len(title)
        if length <= 12:
            return FirstShotTitleBurner.STYLE_SHORT
        elif length <= 25:
            return FirstShotTitleBurner.STYLE_MEDIUM
        else:
            return FirstShotTitleBurner.STYLE_LONG

    @staticmethod
    def _find_font(size: int) -> ImageFont.FreeTypeFont:
        """
        Ищет доступный шрифт на диске.
        Возвращает ImageFont или fallback.
        """
        for font_path in FirstShotTitleBurner.FONT_PATHS:
            if Path(font_path).exists():
                try:
                    return ImageFont.truetype(font_path, size)
                except Exception:
                    continue
        # Fallback — встроенный шрифт PIL (некрасивый, но работает)
        logging.warning("Не найден ни один системный шрифт, используем fallback")
        return ImageFont.load_default()

    @staticmethod
    def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
        """
        Умный перенос текста, сохраняющий пользовательские переносы (\n),
        а также переносящий слова, если строка превышает max_width.
        """
        if not text or not text.strip():
            return [text]

        final_lines = []
        # Разбиваем текст по явно заданным переносам строк
        for manual_line in text.split('\n'):
            words = manual_line.split()
            if not words:
                continue

            current_line = []
            for word in words:
                test_line = ' '.join(current_line + [word])
                bbox = font.getbbox(test_line)
                line_width = bbox[2] - bbox[0]

                if line_width <= max_width:
                    current_line.append(word)
                else:
                    if current_line:
                        final_lines.append(' '.join(current_line))
                    current_line = [word]

            if current_line:
                final_lines.append(' '.join(current_line))

        return final_lines if final_lines else [text]

    @staticmethod
    def _measure_text_block(
        lines: List[str],
        font: ImageFont.FreeTypeFont,
        line_spacing: int = 12
    ) -> Tuple[int, int, List[int]]:
        """
        Измеряет суммарные размеры текстового блока.

        Returns:
            (total_width, total_height, list_of_line_widths)
        """
        line_widths = []
        total_height = 0

        for i, line in enumerate(lines):
            bbox = font.getbbox(line)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            line_widths.append(w)
            total_height += h
            if i < len(lines) - 1:
                total_height += line_spacing

        max_width = max(line_widths) if line_widths else 0
        return max_width, total_height, line_widths

    @staticmethod
    def burn_title_on_image(
        image_path: str,
        output_path: str,
        title: str,
        video_width: int = 1440,
        video_height: int = 2560,
        red_line_y: int = 0,
        ffmpeg_path: str = 'ffmpeg',   # не используется, оставлен для совместимости
        log_callback=None
    ) -> bool:
        """
        Выжигает название на картинке с помощью Pillow.

        Рисует единый скруглённый бокс с полупрозрачным белым фоном,
        внутри которого размещён чёрный текст по центру.

        Args:
            image_path: Путь к исходной картинке
            output_path: Путь для сохранения результата
            title: Название для первого кадра
            video_width: Ширина выходного видео
            video_height: Высота выходного видео
            red_line_y: Y-координата красной линии (не используется в Pillow-версии)
            ffmpeg_path: Не используется (для обратной совместимости)
            log_callback: Функция логирования

        Returns:
            True если успешно, False если ошибка
        """
        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                logging.info(msg)

        if not PIL_AVAILABLE:
            log("   ❌ Pillow не установлен — пропускаем выжигание названия")
            return False

        try:
            # 1. Открываем исходную картинку и масштабируем под видео
            img = Image.open(image_path).convert('RGBA')
            img = img.resize((video_width, video_height), Image.LANCZOS)

            # 2. Определяем стиль
            style = FirstShotTitleBurner._get_style_for_title(title)
            font_size = style['font_size']
            pad_x = style['box_padding_x']
            pad_y = style['box_padding_y']
            radius = style['box_radius']
            max_text_width = int(video_width * style['max_width_percent']) - 2 * pad_x
            center_y_percent = style['position_y_percent']

            log(f"   🎨 Стиль: font={font_size}px, padding={pad_x}x{pad_y}, radius={radius}")

            # 3. Загружаем шрифт
            font = FirstShotTitleBurner._find_font(font_size)

            # 4. Переносим текст на строки
            lines = FirstShotTitleBurner._wrap_text(title, font, max_text_width)
            num_lines = len(lines)
            log(f"   📝 Текст: '{title}' → {num_lines} строк")

            # 5. Измеряем текстовый блок
            line_spacing = 14
            text_w, text_h, line_widths = FirstShotTitleBurner._measure_text_block(
                lines, font, line_spacing
            )

            # 6. Рассчитываем размеры бокса
            box_w = text_w + 2 * pad_x
            box_h = text_h + 2 * pad_y

            # 7. Позиция бокса (по центру X, на center_y_percent по Y)
            box_x = (video_width - box_w) // 2
            box_y = int(video_height * center_y_percent) - box_h // 2
            # Ограничиваем чтобы бокс не выходил за границы
            box_y = max(50, min(box_y, video_height - box_h - 50))

            log(f"   📍 Бокс: {box_w}x{box_h} @ ({box_x}, {box_y})")

            # 8. Создаём слой для бокса (RGBA для прозрачности)
            overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            # 9. Рисуем тень за боксом
            shadow_rect = (
                box_x + FirstShotTitleBurner.SHADOW_OFFSET[0],
                box_y + FirstShotTitleBurner.SHADOW_OFFSET[1],
                box_x + box_w + FirstShotTitleBurner.SHADOW_OFFSET[0],
                box_y + box_h + FirstShotTitleBurner.SHADOW_OFFSET[1],
            )
            draw.rounded_rectangle(
                shadow_rect,
                radius=radius,
                fill=FirstShotTitleBurner.SHADOW_COLOR,
            )

            # 10. Рисуем основной бокс
            box_rect = (box_x, box_y, box_x + box_w, box_y + box_h)
            draw.rounded_rectangle(
                box_rect,
                radius=radius,
                fill=FirstShotTitleBurner.BOX_COLOR,
                outline=FirstShotTitleBurner.BORDER_COLOR,
                width=FirstShotTitleBurner.BORDER_WIDTH,
            )

            # 11. Рисуем текст (каждая строка по центру)
            current_y = box_y + pad_y
            for i, line in enumerate(lines):
                bbox = font.getbbox(line)
                lw = bbox[2] - bbox[0]
                lh = bbox[3] - bbox[1]
                # Центрируем строку внутри бокса
                line_x = box_x + (box_w - lw) // 2
                # Компенсируем offset шрифта (bbox[1] может быть отрицательным)
                draw.text(
                    (line_x, current_y - bbox[1]),
                    line,
                    fill=FirstShotTitleBurner.TEXT_COLOR,
                    font=font,
                )
                current_y += lh + (line_spacing if i < num_lines - 1 else 0)

            # 12. Накладываем overlay на исходную картинку
            result = Image.alpha_composite(img, overlay)

            # 13. Сохраняем как PNG (без потерь)
            # Если выходной формат JPG — конвертируем в RGB
            out_path = Path(output_path)
            if out_path.suffix.lower() in ('.jpg', '.jpeg'):
                result = result.convert('RGB')

            result.save(str(out_path), quality=95)

            # Проверяем что файл создан
            if not out_path.exists() or out_path.stat().st_size < 1000:
                log("   ❌ Выходной файл не создан или слишком мал")
                return False

            log(f"   ✅ Название выжжено на картинке ({out_path.stat().st_size // 1024} KB)")
            return True

        except Exception as e:
            log(f"   ❌ Ошибка выжигания названия: {e}")
            import traceback
            log(f"   {traceback.format_exc()[:300]}")
            return False

    @staticmethod
    def should_burn_title(
        first_shot_from_pool: bool,
        image_paths: list,
        mixed_media: list = None
    ) -> bool:
        """
        Определяет нужно ли выжигать название на первом шоте.

        Args:
            first_shot_from_pool: Включена ли опция "первый шот из пула"
            image_paths: Список путей к картинкам
            mixed_media: Список mixed_media (если есть)

        Returns:
            True если нужно выжигать, False если нет
        """
        if not first_shot_from_pool:
            return False

        if not image_paths:
            return False

        # Если есть mixed_media — проверяем что первый элемент это картинка
        if mixed_media:
            if not mixed_media or mixed_media[0][1] != 'image':
                return False

        return True
