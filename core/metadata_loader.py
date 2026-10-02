#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Metadata Loader - Загрузка метаданных из раздельных файлов
Поддерживает 3 отдельных файла: titles.txt, descriptions.txt, hashtags.txt
"""

from pathlib import Path
from typing import List, Dict, Optional, Tuple


class MetadataLoader:
    """Загрузчик метаданных из раздельных файлов"""
    
    @staticmethod
    def read_lines(file_path: str) -> List[str]:
        """
        Читает файл построчно, удаляет пустые строки
        
        Args:
            file_path: Путь к файлу
            
        Returns:
            Список строк (без пустых)
        """
        try:
            with open(file_path, 'rb') as f:
                raw_content = f.read()
            
            # Удаляем BOM если есть
            if raw_content.startswith(b'\xef\xbb\xbf'):
                raw_content = raw_content[3:]
            
            # Декодируем в UTF-8
            content = raw_content.decode('utf-8')
            
            # Разбиваем на строки (сохраняем пустые для правильного маппинга видео↔строка)
            lines = [line.strip() for line in content.splitlines()]
            # Убираем только trailing пустые строки в конце файла
            while lines and not lines[-1]:
                lines.pop()
            return lines
            
        except Exception as e:
            raise Exception(f"Ошибка чтения файла {file_path}: {e}")
    
    @staticmethod
    def validate_files(titles_path: str, descriptions_path: str, hashtags_path: str) -> Tuple[bool, Optional[str], int]:
        """
        Валидация файлов метаданных
        
        Args:
            titles_path: Путь к файлу с названиями
            descriptions_path: Путь к файлу с описаниями
            hashtags_path: Путь к файлу с хештегами
            
        Returns:
            Tuple: (valid: bool, error: Optional[str], video_count: int)
        """
        # Проверяем что все файлы выбраны
        if not all([titles_path, descriptions_path, hashtags_path]):
            return False, "Необходимо выбрать все 3 файла", 0
        
        # Проверяем что файлы существуют
        for path in [titles_path, descriptions_path, hashtags_path]:
            if not Path(path).exists():
                return False, f"Файл не найден: {path}", 0
        
        try:
            # Читаем файлы
            titles = MetadataLoader.read_lines(titles_path)
            descriptions = MetadataLoader.read_lines(descriptions_path)
            hashtags = MetadataLoader.read_lines(hashtags_path)
            
            # Проверяем что файлы не пустые
            if not titles:
                return False, "Файл с названиями пустой", 0
            if not descriptions:
                return False, "Файл с описаниями пустой", 0
            if not hashtags:
                return False, "Файл с хештегами пустой", 0
            
            # Считаем количество видео (минимум из 3 файлов)
            counts = [len(titles), len(descriptions), len(hashtags)]
            video_count = min(counts)
            
            # Предупреждение если количество строк не совпадает
            if len(set(counts)) > 1:
                warning = f"⚠️ Количество строк не совпадает: titles={len(titles)}, descriptions={len(descriptions)}, hashtags={len(hashtags)}. Будет создано {video_count} видео."
                return True, warning, video_count
            
            return True, None, video_count
            
        except Exception as e:
            return False, f"Ошибка валидации: {e}", 0
    
    @staticmethod
    def load_metadata(titles_path: str, descriptions_path: str, hashtags_path: str) -> List[Dict[str, str]]:
        """
        Загружает метаданные из 3 файлов
        
        Args:
            titles_path: Путь к файлу с названиями
            descriptions_path: Путь к файлу с описаниями
            hashtags_path: Путь к файлу с хештегами
            
        Returns:
            Список словарей с метаданными для каждого видео
            [
                {'title': '...', 'description': '...', 'hashtags': '...'},
                ...
            ]
        """
        # Валидация
        valid, error, video_count = MetadataLoader.validate_files(
            titles_path, descriptions_path, hashtags_path
        )
        
        if not valid:
            raise ValueError(error)
        
        # Читаем файлы
        titles = MetadataLoader.read_lines(titles_path)
        descriptions = MetadataLoader.read_lines(descriptions_path)
        hashtags = MetadataLoader.read_lines(hashtags_path)
        
        # Создаём список метаданных
        metadata = []
        for i in range(video_count):
            metadata.append({
                'title': titles[i],
                'description': descriptions[i],
                'hashtags': hashtags[i]
            })
        
        return metadata


# Пример использования
if __name__ == "__main__":
    # Тест
    loader = MetadataLoader()
    
    # Пример валидации
    valid, error, count = loader.validate_files(
        "titles.txt",
        "descriptions.txt", 
        "hashtags.txt"
    )
    
    print(f"Valid: {valid}")
    print(f"Error: {error}")
    print(f"Videos: {count}")
