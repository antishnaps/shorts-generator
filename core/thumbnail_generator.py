#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Thumbnail Generator
Автоматическое создание превью для YouTube/социальных сетей.

Функции:
- Генерация превью через AI (Gemini Image)
- Наложение текста с эффектами
- Оптимизация для кликабельности
- Несколько вариантов для A/B тестирования
"""

import os
import logging
import requests
import base64
from pathlib import Path
from typing import Callable, Optional, List, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance


# Импортируем дефолтный логгер из централизованного модуля
from core.logging_utils import get_default_logger

# Алиас для обратной совместимости
_dummy_log = get_default_logger()


# Стили текста для превью
THUMBNAIL_STYLES = {
    'bold_impact': {
        'font_size_ratio': 0.12,  # Относительно высоты
        'stroke_width': 8,
        'stroke_color': (0, 0, 0),
        'text_color': (255, 255, 255),
        'shadow': True,
        'shadow_offset': (4, 4),
        'position': 'center',  # center, top, bottom
    },
    'youtube_classic': {
        'font_size_ratio': 0.10,
        'stroke_width': 6,
        'stroke_color': (0, 0, 0),
        'text_color': (255, 255, 0),  # Жёлтый
        'shadow': True,
        'shadow_offset': (3, 3),
        'position': 'bottom',
    },
    'minimal': {
        'font_size_ratio': 0.08,
        'stroke_width': 4,
        'stroke_color': (0, 0, 0),
        'text_color': (255, 255, 255),
        'shadow': False,
        'position': 'bottom',
    },
    'dramatic': {
        'font_size_ratio': 0.14,
        'stroke_width': 10,
        'stroke_color': (139, 0, 0),  # Тёмно-красный
        'text_color': (255, 255, 255),
        'shadow': True,
        'shadow_offset': (5, 5),
        'position': 'center',
    },
    'neon': {
        'font_size_ratio': 0.11,
        'stroke_width': 6,
        'stroke_color': (0, 255, 255),  # Cyan
        'text_color': (255, 0, 255),  # Magenta
        'shadow': True,
        'shadow_offset': (3, 3),
        'position': 'center',
    }
}


class ThumbnailGenerator:
    """Генератор превью для видео"""
    
    # Стандартные размеры превью
    YOUTUBE_SIZE = (1280, 720)
    SHORTS_SIZE = (1080, 1920)
    
    def __init__(self, api_key: str = None, log_callback: Callable = _dummy_log):
        self.api_key = api_key
        self.log = log_callback
    
    def generate_thumbnail(
        self,
        title: str,
        theme: str,
        output_dir: Path,
        base_image_path: str = None,
        style: str = 'bold_impact',
        size: Tuple[int, int] = None,
        num_variants: int = 1
    ) -> List[str]:
        """
        Генерирует превью для видео.
        
        Args:
            title: Заголовок для превью
            theme: Тема видео (для AI генерации фона)
            output_dir: Папка для сохранения
            base_image_path: Путь к базовому изображению (опционально)
            style: Стиль текста из THUMBNAIL_STYLES
            size: Размер превью (по умолчанию YouTube 1280x720)
            num_variants: Количество вариантов для A/B тестирования
            
        Returns:
            Список путей к сгенерированным превью
        """
        self.log("🖼️ Генерация превью...")
        
        if size is None:
            size = self.YOUTUBE_SIZE
        
        thumbnails = []
        thumb_dir = output_dir / "thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        
        # Получаем или генерируем базовое изображение
        if base_image_path and Path(base_image_path).exists():
            base_images = [base_image_path]
        else:
            # Генерируем через AI
            base_images = self._generate_ai_backgrounds(
                theme=theme,
                size=size,
                num_images=num_variants,
                output_dir=thumb_dir
            )
        
        if not base_images:
            self.log("   ⚠️ Не удалось получить базовые изображения")
            return []
        
        # Создаём варианты с разными стилями текста
        styles_to_use = [style]
        if num_variants > 1:
            all_styles = list(THUMBNAIL_STYLES.keys())
            styles_to_use = [style] + [s for s in all_styles if s != style][:num_variants-1]
        
        for i, (base_img, text_style) in enumerate(zip(
            base_images * len(styles_to_use),  # Повторяем изображения если нужно
            styles_to_use
        )):
            if i >= num_variants:
                break
                
            try:
                thumb_path = self._create_thumbnail_with_text(
                    base_image_path=base_img,
                    title=title,
                    style=text_style,
                    size=size,
                    output_path=thumb_dir / f"thumbnail_v{i+1}.jpg"
                )
                if thumb_path:
                    thumbnails.append(thumb_path)
                    self.log(f"   ✅ Превью {i+1}: {Path(thumb_path).name} (стиль: {text_style})")
            except Exception as e:
                self.log(f"   ⚠️ Ошибка создания превью {i+1}: {e}")
        
        return thumbnails
    
    def _generate_ai_backgrounds(
        self,
        theme: str,
        size: Tuple[int, int],
        num_images: int,
        output_dir: Path
    ) -> List[str]:
        """Генерирует фоновые изображения через AI"""
        
        if not self.api_key:
            self.log("   ⚠️ API ключ не указан, используем цветной фон")
            return self._create_gradient_backgrounds(size, num_images, output_dir)
        
        backgrounds = []
        
        prompt = f"""Create a dramatic, eye-catching YouTube thumbnail background for topic: {theme}
        
Requirements:
- Vibrant, high-contrast colors
- Dramatic lighting with depth
- NO text or letters
- Cinematic composition
- Suitable for text overlay
- Professional quality
- Engaging and clickable"""
        
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        
        for i in range(num_images):
            try:
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseModalities": ["image", "text"],
                        "temperature": 0.9 + i * 0.05,  # Вариативность
                    }
                }
                
                resp = requests.post(url, json=payload, headers=headers, timeout=60)
                
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get('candidates', [])
                    
                    for candidate in candidates:
                        parts = candidate.get('content', {}).get('parts', [])
                        for part in parts:
                            if 'inlineData' in part:
                                image_data = part['inlineData'].get('data', '')
                                if image_data:
                                    # Сохраняем изображение
                                    img_bytes = base64.b64decode(image_data)
                                    bg_path = output_dir / f"bg_{i+1}.png"
                                    
                                    with open(bg_path, 'wb') as f:
                                        f.write(img_bytes)
                                    
                                    # Масштабируем до нужного размера
                                    img = Image.open(bg_path)
                                    img = img.resize(size, Image.Resampling.LANCZOS)
                                    img.save(bg_path)
                                    
                                    backgrounds.append(str(bg_path))
                                    self.log(f"   🎨 AI фон {i+1} сгенерирован")
                                    break
                        if len(backgrounds) > i:
                            break
                            
            except Exception as e:
                self.log(f"   ⚠️ Ошибка AI генерации фона {i+1}: {e}")
        
        # Если AI не сработал - создаём градиентные фоны
        if not backgrounds:
            backgrounds = self._create_gradient_backgrounds(size, num_images, output_dir)
        
        return backgrounds
    
    def _create_gradient_backgrounds(
        self,
        size: Tuple[int, int],
        num_images: int,
        output_dir: Path
    ) -> List[str]:
        """Создаёт градиентные фоны как fallback"""
        
        gradients = [
            [(20, 20, 40), (80, 40, 120)],      # Тёмно-фиолетовый
            [(40, 20, 20), (120, 40, 40)],      # Тёмно-красный
            [(20, 40, 60), (40, 100, 140)],     # Тёмно-синий
            [(40, 40, 20), (100, 100, 40)],     # Тёмно-золотой
            [(20, 40, 40), (40, 120, 100)],     # Тёмно-бирюзовый
        ]
        
        backgrounds = []
        
        for i in range(min(num_images, len(gradients))):
            color1, color2 = gradients[i]
            
            img = Image.new('RGB', size)
            draw = ImageDraw.Draw(img)
            
            # Вертикальный градиент
            for y in range(size[1]):
                ratio = y / size[1]
                r = int(color1[0] + (color2[0] - color1[0]) * ratio)
                g = int(color1[1] + (color2[1] - color1[1]) * ratio)
                b = int(color1[2] + (color2[2] - color1[2]) * ratio)
                draw.line([(0, y), (size[0], y)], fill=(r, g, b))
            
            bg_path = output_dir / f"bg_gradient_{i+1}.png"
            img.save(bg_path)
            backgrounds.append(str(bg_path))
        
        return backgrounds
    
    def _create_thumbnail_with_text(
        self,
        base_image_path: str,
        title: str,
        style: str,
        size: Tuple[int, int],
        output_path: Path
    ) -> Optional[str]:
        """Создаёт превью с наложением текста"""
        
        style_config = THUMBNAIL_STYLES.get(style, THUMBNAIL_STYLES['bold_impact'])
        
        # Открываем и масштабируем базовое изображение
        img = Image.open(base_image_path).convert('RGB')
        img = img.resize(size, Image.Resampling.LANCZOS)
        
        # Увеличиваем контраст и насыщенность
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.2)
        enhancer = ImageEnhance.Color(img)
        img = enhancer.enhance(1.3)
        
        # Добавляем виньетку (затемнение по краям)
        img = self._add_vignette(img)
        
        draw = ImageDraw.Draw(img)
        
        # Рассчитываем размер шрифта
        font_size = int(size[1] * style_config['font_size_ratio'])
        
        # Пробуем загрузить шрифт
        font = self._get_font(font_size)
        
        # Подготавливаем текст (разбиваем на строки)
        # Сокращаем заголовок для превью
        short_title = self._shorten_title(title, max_words=5)
        wrapped_text = self._wrap_text(short_title, font, size[0] * 0.9, draw)
        
        # Рассчитываем позицию текста
        text_bbox = draw.multiline_textbbox((0, 0), wrapped_text, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        
        if style_config['position'] == 'center':
            x = (size[0] - text_width) // 2
            y = (size[1] - text_height) // 2
        elif style_config['position'] == 'top':
            x = (size[0] - text_width) // 2
            y = int(size[1] * 0.1)
        else:  # bottom
            x = (size[0] - text_width) // 2
            y = int(size[1] * 0.7)
        
        # Рисуем тень
        if style_config['shadow']:
            shadow_offset = style_config['shadow_offset']
            draw.multiline_text(
                (x + shadow_offset[0], y + shadow_offset[1]),
                wrapped_text,
                font=font,
                fill=(0, 0, 0, 180),
                align='center'
            )
        
        # Рисуем обводку
        stroke_width = style_config['stroke_width']
        stroke_color = style_config['stroke_color']
        
        # Рисуем текст с обводкой
        draw.multiline_text(
            (x, y),
            wrapped_text,
            font=font,
            fill=style_config['text_color'],
            stroke_width=stroke_width,
            stroke_fill=stroke_color,
            align='center'
        )
        
        # Сохраняем
        img.save(output_path, 'JPEG', quality=95)
        
        return str(output_path)
    
    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Получает шрифт для текста - универсальный для всех ОС"""
        import platform
        
        # Определяем ОС и соответствующие пути к шрифтам
        system = platform.system().lower()
        
        font_paths = []
        
        if system == "windows":
            # Windows font paths
            font_paths.extend([
                "C:/Windows/Fonts/impact.ttf",
                "C:/Windows/Fonts/arialbd.ttf", 
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/calibrib.ttf",
                "C:/Windows/Fonts/tahoma.ttf"
            ])
        elif system == "darwin":  # macOS
            # macOS font paths
            font_paths.extend([
                "/System/Library/Fonts/Helvetica.ttc",
                "/System/Library/Fonts/Arial.ttf",
                "/System/Library/Fonts/Arial Bold.ttf",
                "/Library/Fonts/Arial.ttf",
                "/System/Library/Fonts/Impact.ttf"
            ])
        else:  # Linux and other Unix-like systems
            # Linux font paths
            font_paths.extend([
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
                "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            ])
        
        # Попытка загрузить шрифт
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    return ImageFont.truetype(font_path, size)
                except Exception as e:
                    logging.debug(f"Failed to load font {font_path}: {e}")
                    continue
        
        # Fallback: попытка использовать системный шрифт по умолчанию
        try:
            return ImageFont.load_default()
        except:
            # Последний fallback: создаем простой шрифт
            logging.warning("Could not load any font, using PIL default")
            return ImageFont.load_default()
    
    def _wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.Draw) -> str:
        """Разбивает текст на строки"""
        
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            test_line = ' '.join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            
            if bbox[2] - bbox[0] <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
        
        if current_line:
            lines.append(' '.join(current_line))
        
        return '\n'.join(lines)
    
    def _shorten_title(self, title: str, max_words: int = 5) -> str:
        """Сокращает заголовок для превью"""
        
        # Убираем лишние символы
        title = title.replace(':', ' ').replace('!', ' ').replace('?', ' ')
        words = title.split()
        
        if len(words) <= max_words:
            return title.upper()
        
        # Берём первые N слов
        return ' '.join(words[:max_words]).upper()
    
    def _add_vignette(self, img: Image.Image) -> Image.Image:
        """Добавляет виньетку (затемнение по краям)"""
        
        width, height = img.size
        
        # Создаём маску виньетки
        vignette = Image.new('L', (width, height), 255)
        ImageDraw.Draw(vignette)
        
        # Рисуем радиальный градиент
        center_x, center_y = width // 2, height // 2
        max_dist = ((width/2)**2 + (height/2)**2) ** 0.5
        
        for y in range(height):
            for x in range(width):
                dist = ((x - center_x)**2 + (y - center_y)**2) ** 0.5
                ratio = dist / max_dist
                # Затемняем края
                brightness = int(255 * (1 - ratio * 0.4))
                vignette.putpixel((x, y), brightness)
        
        # Применяем размытие для плавности
        vignette = vignette.filter(ImageFilter.GaussianBlur(radius=50))
        
        # Применяем виньетку
        result = Image.composite(img, Image.new('RGB', img.size, (0, 0, 0)), vignette)
        
        return result


def generate_thumbnails(
    title: str,
    theme: str,
    output_dir: Path,
    api_key: str = None,
    base_image: str = None,
    num_variants: int = 3,
    log_callback: Callable = _dummy_log
) -> List[str]:
    """
    Удобная функция для генерации превью.
    
    Args:
        title: Заголовок видео
        theme: Тема видео
        output_dir: Папка для сохранения
        api_key: API ключ для AI генерации
        base_image: Базовое изображение (опционально)
        num_variants: Количество вариантов
        log_callback: Функция логирования
        
    Returns:
        Список путей к превью
    """
    generator = ThumbnailGenerator(api_key=api_key, log_callback=log_callback)
    
    return generator.generate_thumbnail(
        title=title,
        theme=theme,
        output_dir=output_dir,
        base_image_path=base_image,
        num_variants=num_variants
    )
