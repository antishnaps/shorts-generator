#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автоматическое логирование всех операций в файлы
"""

from pathlib import Path
from datetime import datetime
from typing import Optional, Callable

from core.secret_redaction import redact_setting, redact_text


class FileLogger:
    """
    Автоматический логгер, который пишет все логи в файл
    Каждая генерация создаёт свой лог-файл
    """
    
    def __init__(self, output_dir: str, video_name: str = None):
        """
        Args:
            output_dir: Папка для сохранения логов
            video_name: Название видео (опционально)
        """
        # Создаём подпапку logs внутри output_dir
        self.output_dir = Path(output_dir) / "logs"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Создаём имя лог-файла
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if video_name:
            # Очищаем имя от недопустимых символов
            safe_name = self._sanitize_filename(video_name)
            log_filename = f"{timestamp}_{safe_name}_log.txt"
        else:
            log_filename = f"{timestamp}_generation_log.txt"
        
        self.log_file = self.output_dir / log_filename
        self.start_time = datetime.now()
        
        # Записываем заголовок
        self._write_header()
    
    def _sanitize_filename(self, name: str) -> str:
        """Очистка имени файла от недопустимых символов"""
        import re
        # Убираем недопустимые символы
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        # Заменяем пробелы на подчеркивания
        name = name.replace(' ', '_')
        # Ограничиваем длину
        return name[:50]
    
    def _write_header(self):
        """Записываем заголовок лог-файла"""
        header = f"""
{'=' * 80}
SHORTS GENERATOR - LOG FILE
{'=' * 80}
Дата: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}
Лог-файл: {self.log_file.name}
{'=' * 80}

"""
        with open(self.log_file, 'w', encoding='utf-8') as f:
            f.write(header)
    
    def log(self, message: str):
        """
        Записать сообщение в лог
        
        Args:
            message: Текст сообщения
        """
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_line = f"[{timestamp}] {redact_text(message)}\n"
        
        # Пишем в файл
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(log_line)
        except Exception as e:
            print(f"⚠️ Ошибка записи в лог: {e}")
    
    def log_section(self, title: str):
        """Записать заголовок секции"""
        section = f"\n{'=' * 80}\n{title}\n{'=' * 80}\n"
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(section)
        except Exception as e:
            print(f"⚠️ Ошибка записи в лог: {e}")
    
    def log_error(self, error: Exception, context: str = ""):
        """
        Записать ошибку с полным traceback
        
        Args:
            error: Объект исключения
            context: Контекст ошибки
        """
        import traceback
        
        error_text = redact_text(f"""
{'!' * 80}
ОШИБКА: {context}
{'!' * 80}
Тип: {type(error).__name__}
Сообщение: {str(error)}

Traceback:
{traceback.format_exc()}
{'!' * 80}

""")
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(error_text)
        except Exception as e:
            print(f"⚠️ Ошибка записи в лог: {e}")
    
    def log_settings(self, settings: dict):
        """Записать настройки генерации"""
        self.log_section("НАСТРОЙКИ ГЕНЕРАЦИИ")
        for key, value in settings.items():
            self.log(f"  {key}: {redact_setting(key, value)}")
    
    def finalize(self):
        """Завершить лог-файл"""
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()
        
        footer = f"""
{'=' * 80}
ЗАВЕРШЕНО
{'=' * 80}
Время начала: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}
Время окончания: {end_time.strftime('%Y-%m-%d %H:%M:%S')}
Длительность: {duration:.2f} секунд ({duration/60:.2f} минут)
{'=' * 80}
"""
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(footer)
        except Exception as e:
            print(f"⚠️ Ошибка записи в лог: {e}")
    
    def get_log_path(self) -> str:
        """Получить путь к лог-файлу"""
        return str(self.log_file)


class DualLogger:
    """
    Логгер, который пишет одновременно в файл и в callback
    Используется для GUI
    """
    
    def __init__(self, file_logger: FileLogger, gui_callback: Optional[Callable] = None):
        """
        Args:
            file_logger: FileLogger для записи в файл
            gui_callback: Callback для GUI (опционально)
        """
        self.file_logger = file_logger
        self.gui_callback = gui_callback
    
    def __call__(self, message: str):
        """Позволяет использовать как функцию"""
        self.log(message)
    
    def log(self, message: str):
        """Записать сообщение (алиас для совместимости)"""
        message = redact_text(message)
        # Пишем в файл
        self.file_logger.log(message)
        
        # Пишем в GUI
        if self.gui_callback:
            try:
                self.gui_callback(message)
            except Exception as e:
                print(f"⚠️ Ошибка GUI callback: {e}")
    
    def log_section(self, title: str):
        """Записать заголовок секции"""
        self.file_logger.log_section(title)
        if self.gui_callback:
            self.gui_callback(f"\n{'=' * 60}\n{title}\n{'=' * 60}")
    
    def log_error(self, error: Exception, context: str = ""):
        """Записать ошибку"""
        self.file_logger.log_error(error, context)
        if self.gui_callback:
            self.gui_callback(redact_text(f"❌ ОШИБКА: {context} - {str(error)}"))
    
    def log_settings(self, settings: dict):
        """Записать настройки"""
        self.file_logger.log_settings(settings)
    
    def finalize(self):
        """Завершить лог"""
        self.file_logger.finalize()
        if self.gui_callback:
            self.gui_callback(f"\n✅ Лог сохранён: {self.file_logger.get_log_path()}")
    
    def get_log_path(self) -> str:
        """Получить путь к лог-файлу"""
        return self.file_logger.get_log_path()


def create_logger(output_dir: str, video_name: str = None, 
                 gui_callback: Optional[Callable] = None) -> DualLogger:
    """
    Создать логгер для генерации
    
    Args:
        output_dir: Папка для сохранения логов
        video_name: Название видео (опционально)
        gui_callback: Callback для GUI (опционально)
    
    Returns:
        DualLogger, который пишет в файл и GUI
    """
    file_logger = FileLogger(output_dir, video_name)
    return DualLogger(file_logger, gui_callback)
