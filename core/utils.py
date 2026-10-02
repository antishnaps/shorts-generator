#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Утилиты для генератора видео
- Retry с exponential backoff
- Очистка временных файлов
- Валидация путей
- Безопасная работа с путями (Unicode/кириллица)
- Нормализация языка
"""

import time
import functools
import os
import sys
import tempfile
import logging
import subprocess
from pathlib import Path
from typing import Callable, Optional, Any, Iterable, List, Dict, Tuple

from core.process_registry import run_registered


def safe_cpu_count(default: int = 4) -> int:
    """Return a sane CPU count even on restricted VMs or unusual Windows builds."""
    try:
        count = os.cpu_count()
        if count is None:
            import multiprocessing
            count = multiprocessing.cpu_count()
        count = int(count)
    except Exception:
        count = int(default or 1)
    return max(1, count)


# ============================================================
# 🌍 LANGUAGE NORMALIZATION
# ============================================================

def normalize_language(language: str) -> str:
    """
    Нормализует строку языка из GUI формата в чистое название.
    
    Использует централизованный маппинг из gui/constants.py
    
    Примеры:
        '🇷🇺 Русский' -> 'Russian'
        '🇺🇸 English' -> 'English'
        'Russian' -> 'Russian'
        'English' -> 'English'
    
    Args:
        language: Строка языка (может содержать эмодзи флага)
        
    Returns:
        Чистое название языка на английском
    """
    if not language:
        return 'Russian'
    
    # Пробуем импортировать централизованный маппинг
    try:
        from gui.constants import get_internal_language
        return get_internal_language(language)
    except ImportError:
        pass
    
    # Fallback маппинг (для случаев когда gui недоступен)
    language_map = {
        # С эмодзи
        '🇷🇺 Русский': 'Russian',
        '🇺🇸 English': 'English',
        '🇪🇸 Español': 'Spanish',
        '🇫🇷 Français': 'French',
        '🇩🇪 Deutsch': 'German',
        '🇨🇳 中文': 'Chinese',
        '🇯🇵 日本語': 'Japanese',
        '🇰🇷 한국어': 'Korean',
        '🇵🇹 Português': 'Portuguese',
        '🇮🇹 Italiano': 'Italian',
        '🇮🇳 हिन्दी': 'Hindi',
        '🇸🇦 العربية': 'Arabic',
        # Без эмодзи
        'Русский': 'Russian',
        'Russian': 'Russian',
        'English': 'English',
        'Español': 'Spanish',
        'Spanish': 'Spanish',
        'Français': 'French',
        'French': 'French',
        'Deutsch': 'German',
        'German': 'German',
        '中文': 'Chinese',
        'Chinese': 'Chinese',
        '日本語': 'Japanese',
        'Japanese': 'Japanese',
        '한국어': 'Korean',
        'Korean': 'Korean',
        'Português': 'Portuguese',
        'Portuguese': 'Portuguese',
        'Italian': 'Italian',
        'Italiano': 'Italian',
        'Hindi': 'Hindi',
        'हिन्दी': 'Hindi',
        'Arabic': 'Arabic',
        'العربية': 'Arabic',
    }
    
    # Пробуем найти в маппинге
    if language in language_map:
        return language_map[language]
    
    # Пробуем извлечь название после эмодзи (если формат "🇺🇸 English")
    if ' ' in language:
        parts = language.split(' ', 1)
        if len(parts) == 2:
            name = parts[1].strip()
            if name in language_map:
                return language_map[name]
            return name
    
    # Возвращаем как есть
    return language


# ============================================================
# 🌍 UNICODE PATH UTILITIES (для русских/нелатинских имён пользователей)
# ============================================================

def get_safe_temp_dir() -> Path:
    """
    Получает безопасную temp директорию без Unicode символов - универсальная для всех ОС.
    
    На Windows с русским именем пользователя стандартный temp путь
    (C:\\Users\\Иван\\AppData\\Local\\Temp) может вызывать проблемы
    с FFmpeg и другими инструментами.
    
    Returns:
        Path к безопасной temp директории
    """
    import platform
    
    # Проверяем стандартный temp
    standard_temp = Path(tempfile.gettempdir())
    
    # Проверяем есть ли non-ASCII символы в пути
    try:
        str(standard_temp).encode('ascii')
        # Путь ASCII-safe, используем стандартный
        return standard_temp
    except UnicodeEncodeError:
        pass
    
    # Путь содержит Unicode - ищем альтернативу в зависимости от ОС
    system = platform.system().lower()
    
    if system == 'windows':
        # Пробуем альтернативные пути на Windows
        alternatives = [
            Path('C:/Temp'),
            Path('C:/tmp'),
            Path(os.environ.get('SYSTEMDRIVE', 'C:')) / 'Temp',
            Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) / 'Temp',
        ]
        fallback_name = 'VideoGenTemp'
        fallback_root = Path('C:/')
        
    elif system == 'darwin':  # macOS
        # macOS альтернативы
        alternatives = [
            Path('/tmp'),
            Path('/var/tmp'),
            Path(os.path.expanduser('~/tmp')),
        ]
        fallback_name = 'VideoGenTemp'
        fallback_root = Path('/tmp')
        
    else:  # Linux и другие Unix-like системы
        # Linux альтернативы
        alternatives = [
            Path('/tmp'),
            Path('/var/tmp'),
            Path(os.path.expanduser('~/tmp')),
            Path('/dev/shm'),  # RAM disk на Linux
        ]
        fallback_name = 'VideoGenTemp'
        fallback_root = Path('/tmp')
    
    # Пробуем альтернативные пути
    for alt_path in alternatives:
        try:
            if not alt_path.exists():
                alt_path.mkdir(parents=True, exist_ok=True)
            
            # Проверяем что можем писать
            test_file = alt_path / f'_test_{os.getpid()}.tmp'
            test_file.write_text('test', encoding='utf-8')
            test_file.unlink()
            
            logging.debug(f"Используем альтернативный temp: {alt_path}")
            return alt_path
        except Exception:
            continue
    
    # Если ничего не подошло - создаём fallback
    try:
        fallback = fallback_root / fallback_name
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    except Exception:
        pass
    
    # Последний fallback на стандартный (лучше чем ничего)
    return standard_temp


def safe_temp_file(suffix: str = '', prefix: str = 'tmp', dir: str = None) -> str:
    """
    Создаёт временный файл в безопасной директории.
    
    Args:
        suffix: Суффикс файла (например '.mp4')
        prefix: Префикс файла
        dir: Директория (если None - используется safe temp)
        
    Returns:
        Путь к временному файлу
    """
    if dir is None:
        dir = str(get_safe_temp_dir())
    
    # Создаём файл
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=dir)
    os.close(fd)
    return path


def safe_temp_dir(prefix: str = 'tmp') -> Path:
    """
    Создаёт временную директорию в безопасном месте.
    
    Args:
        prefix: Префикс директории
        
    Returns:
        Path к временной директории
    """
    base_dir = get_safe_temp_dir()
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(base_dir)))


def normalize_path_for_ffmpeg(path: str) -> str:
    """
    Нормализует путь для использования в FFmpeg.
    
    FFmpeg на Windows может иметь проблемы с:
    - Unicode символами в путях
    - Обратными слэшами
    - Пробелами
    
    Args:
        path: Исходный путь
        
    Returns:
        Нормализованный путь
    """
    path_obj = Path(path)
    
    # Получаем абсолютный путь
    abs_path = str(path_obj.resolve())
    
    # Заменяем обратные слэши на прямые (FFmpeg предпочитает)
    normalized = abs_path.replace('\\', '/')
    
    return normalized


def copy_to_safe_path(source_path: str, log_callback: Optional[Callable] = None) -> Optional[str]:
    """
    Копирует файл в безопасную директорию если путь содержит Unicode.
    
    Args:
        source_path: Исходный путь к файлу
        log_callback: Функция логирования
        
    Returns:
        Путь к копии (или исходный путь если копирование не нужно)
    """
    import shutil
    
    _log = log_callback or (lambda x: logging.debug(x))
    
    source = Path(source_path)
    if not source.exists():
        return None
    
    # Проверяем нужно ли копировать
    try:
        str(source.resolve()).encode('ascii')
        # Путь ASCII-safe
        return str(source)
    except UnicodeEncodeError:
        pass
    
    # Копируем в безопасную директорию
    try:
        safe_dir = get_safe_temp_dir() / 'unicode_safe'
        safe_dir.mkdir(parents=True, exist_ok=True)
        
        # Генерируем безопасное имя
        import hashlib
        name_hash = hashlib.md5(str(source).encode('utf-8')).hexdigest()[:8]
        safe_name = f"{name_hash}_{source.suffix}"
        safe_path = safe_dir / safe_name
        
        shutil.copy2(source, safe_path)
        _log(f"📁 Скопировано в безопасный путь: {source.name} → {safe_path}")
        
        return str(safe_path)
    except Exception as e:
        _log(f"⚠️ Не удалось скопировать в безопасный путь: {e}")
        return str(source)


def setup_unicode_environment():
    """
    Настраивает окружение для корректной работы с Unicode.
    
    Вызывается при старте приложения.
    """
    if sys.platform == 'win32':
        # Устанавливаем UTF-8 для subprocess
        os.environ['PYTHONUTF8'] = '1'
        os.environ['PYTHONIOENCODING'] = 'utf-8'
        
        # Для FFmpeg
        os.environ['FFREPORT'] = ''  # Отключаем логи FFmpeg (могут падать на Unicode)
        
        # Проверяем temp директорию
        safe_temp = get_safe_temp_dir()
        
        # Если стандартный temp содержит Unicode - переопределяем
        standard_temp = Path(tempfile.gettempdir())
        try:
            str(standard_temp).encode('ascii')
        except UnicodeEncodeError:
            # Переопределяем TEMP/TMP на безопасный путь
            os.environ['TEMP'] = str(safe_temp)
            os.environ['TMP'] = str(safe_temp)
            logging.info(f"🌍 Unicode в пути пользователя - используем альтернативный temp: {safe_temp}")


# ============================================================
# 🔤 SAFE CONSOLE OUTPUT (для Windows с кракозябрами)
# ============================================================

# Маппинг эмодзи на ASCII альтернативы
EMOJI_TO_ASCII = {
    '✅': '[OK]',
    '❌': '[ERROR]',
    '⚠️': '[WARN]',
    '🎬': '[VIDEO]',
    '🖼️': '[IMG]',
    '🎵': '[AUDIO]',
    '🔊': '[SOUND]',
    '📊': '[STATS]',
    '📝': '[TEXT]',
    '🧠': '[AI]',
    '🔍': '[SEARCH]',
    '🔄': '[RETRY]',
    '⏱️': '[TIME]',
    '💾': '[SAVE]',
    '📁': '[FILE]',
    '🗑️': '[DEL]',
    '🚀': '[START]',
    '🎯': '[TARGET]',
    '🌍': '[LANG]',
    '🇷🇺': '[RU]',
    '🇺🇸': '[EN]',
    '🇬🇧': '[EN]',
    '✂️': '[CUT]',
    '🔗': '[LINK]',
    '📺': '[TV]',
    '🎨': '[ART]',
    '💡': '[TIP]',
    '🔧': '[FIX]',
    '📦': '[PKG]',
    '🤖': '[BOT]',
    '👤': '[USER]',
    '🚫': '[NO]',
    '⬇️': '[DOWN]',
    '⬆️': '[UP]',
    '➡️': '[->]',
    '⏳': '[WAIT]',
    '🎉': '[DONE]',
    '💰': '[COST]',
    '🔑': '[KEY]',
    '🍪': '[COOKIE]',
}


def safe_console_text(text: str) -> str:
    """
    Преобразует текст для безопасного вывода в консоль Windows.
    
    Заменяет эмодзи на ASCII альтернативы если консоль не поддерживает UTF-8.
    
    Args:
        text: Исходный текст с эмодзи
        
    Returns:
        Текст безопасный для вывода в консоль
    """
    if not text:
        return text
    
    # Проверяем поддерживает ли консоль UTF-8
    try:
        # Пробуем закодировать в текущую кодировку консоли
        if sys.stdout and hasattr(sys.stdout, 'encoding'):
            encoding = sys.stdout.encoding or 'utf-8'
            text.encode(encoding)
            return text  # Консоль поддерживает UTF-8
    except (UnicodeEncodeError, AttributeError):
        pass
    
    # Заменяем эмодзи на ASCII
    result = text
    for emoji, ascii_alt in EMOJI_TO_ASCII.items():
        result = result.replace(emoji, ascii_alt)
    
    # Убираем оставшиеся non-ASCII символы которые могут вызвать проблемы
    try:
        result.encode('cp1251')  # Типичная кодировка Windows консоли
    except UnicodeEncodeError:
        # Заменяем проблемные символы на ?
        result = result.encode('cp1251', errors='replace').decode('cp1251')
    
    return result


def safe_print(text: str):
    """
    Безопасный print для Windows консоли.
    
    Args:
        text: Текст для вывода
    """
    print(safe_console_text(text))


# ============================================================
# 🔄 RETRY UTILITIES
# ============================================================


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple = (Exception,),
    log_callback: Optional[Callable] = None
):
    """
    Декоратор для retry с exponential backoff.
    
    Args:
        max_attempts: Максимальное количество попыток
        base_delay: Начальная задержка в секундах
        max_delay: Максимальная задержка
        exceptions: Кортеж исключений для перехвата
        log_callback: Функция логирования
    
    Usage:
        @retry_with_backoff(max_attempts=3, base_delay=2.0)
        def api_call():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            _log = log_callback or (lambda x: print(x))
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt < max_attempts:
                        delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                        _log(f"⚠️ Попытка {attempt}/{max_attempts} не удалась: {str(e)[:100]}")
                        _log(f"   🔄 Повтор через {delay:.1f}s...")
                        time.sleep(delay)
                    else:
                        _log(f"❌ Все {max_attempts} попытки исчерпаны")
            
            raise last_exception
        return wrapper
    return decorator


def retry_ffmpeg_call(
    cmd: List[str],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    timeout: int = 300,
    log_callback: Optional[Callable] = None,
    fallback_to_cpu: bool = True
) -> subprocess.CompletedProcess:
    """
    P1: Выполняет FFmpeg команду с retry и exponential backoff.
    
    При ошибках NVENC автоматически переключается на CPU кодирование.
    
    Args:
        cmd: Команда FFmpeg как список аргументов
        max_attempts: Максимальное количество попыток
        base_delay: Начальная задержка между попытками
        timeout: Таймаут выполнения в секундах
        log_callback: Функция логирования
        fallback_to_cpu: Переключаться на CPU при ошибках NVENC
        
    Returns:
        subprocess.CompletedProcess с результатом
        
    Raises:
        Exception: Если все попытки исчерпаны
    """
    import subprocess
    
    _log = log_callback or (lambda x: logging.debug(x))
    last_exception = None
    current_cmd = cmd.copy()
    
    for attempt in range(1, max_attempts + 1):
        try:
            result = run_registered(
                current_cmd,
                label=f"ffmpeg_retry_attempt_{attempt}",
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=timeout
            )
            
            if result.returncode == 0:
                return result
            
            # Проверяем тип ошибки
            stderr_lower = result.stderr.lower() if result.stderr else ''
            is_nvenc_error = any(x in stderr_lower for x in [
                'nvenc', 'cuda', 'incompatible client key', 
                'no capable devices found', 'cannot load'
            ])
            
            if is_nvenc_error and fallback_to_cpu and 'h264_nvenc' in ' '.join(current_cmd):
                _log("⚠️ NVENC ошибка, переключаемся на CPU кодирование...")
                # Заменяем NVENC на libx264
                current_cmd = _replace_nvenc_with_cpu(current_cmd)
                continue
            
            # Другая ошибка - retry
            if attempt < max_attempts:
                delay = min(base_delay * (2 ** (attempt - 1)), 30.0)
                _log(f"⚠️ FFmpeg попытка {attempt}/{max_attempts} не удалась")
                _log(f"   Ошибка: {result.stderr[:150] if result.stderr else 'unknown'}")
                _log(f"   🔄 Повтор через {delay:.1f}s...")
                time.sleep(delay)
            else:
                raise Exception(f"FFmpeg failed after {max_attempts} attempts: {result.stderr[:300]}")
                
        except subprocess.TimeoutExpired as e:
            last_exception = e
            if attempt < max_attempts:
                delay = min(base_delay * (2 ** (attempt - 1)), 30.0)
                _log(f"⚠️ FFmpeg таймаут ({timeout}s), попытка {attempt}/{max_attempts}")
                _log(f"   🔄 Повтор через {delay:.1f}s...")
                time.sleep(delay)
            else:
                raise Exception(f"FFmpeg timeout after {max_attempts} attempts")
        except Exception as e:
            last_exception = e
            if attempt < max_attempts:
                delay = min(base_delay * (2 ** (attempt - 1)), 30.0)
                _log(f"⚠️ FFmpeg ошибка: {str(e)[:100]}")
                time.sleep(delay)
            else:
                raise
    
    raise last_exception or Exception("FFmpeg failed")


def _replace_nvenc_with_cpu(cmd: List[str]) -> List[str]:
    """Заменяет NVENC параметры на CPU (libx264)."""
    new_cmd = []
    skip_next = False
    
    for i, arg in enumerate(cmd):
        if skip_next:
            skip_next = False
            continue
            
        # Заменяем кодек
        if arg == 'h264_nvenc':
            new_cmd.append('libx264')
        # Заменяем NVENC-специфичные параметры
        elif arg in ['-preset', '-cq', '-rc', '-tune']:
            if arg == '-preset' and i + 1 < len(cmd):
                # NVENC presets (p1-p7) -> libx264 presets
                nvenc_preset = cmd[i + 1]
                if nvenc_preset.startswith('p'):
                    new_cmd.extend(['-preset', 'fast'])
                else:
                    new_cmd.extend([arg, cmd[i + 1]])
                skip_next = True
            elif arg == '-cq' and i + 1 < len(cmd):
                # -cq -> -crf
                new_cmd.extend(['-crf', cmd[i + 1]])
                skip_next = True
            elif arg == '-rc':
                # Пропускаем -rc для libx264
                skip_next = True
            elif arg == '-tune' and i + 1 < len(cmd):
                # NVENC tune -> libx264 tune
                nvenc_tune = cmd[i + 1]
                if nvenc_tune == 'hq':
                    new_cmd.extend(['-tune', 'film'])
                else:
                    new_cmd.extend([arg, cmd[i + 1]])
                skip_next = True
            else:
                new_cmd.append(arg)
        # Пропускаем NVENC-only параметры
        elif arg in ['-spatial-aq', '-temporal-aq', '-aq-strength', '-b_ref_mode']:
            skip_next = True
        elif arg.startswith('-spatial-aq') or arg.startswith('-temporal-aq'):
            continue
        else:
            new_cmd.append(arg)
    
    return new_cmd


# ============================================================
# 📁 PATH VALIDATION UTILITIES
# ============================================================


def validate_input_path(path: str, must_exist: bool = True, 
                        allowed_extensions: Optional[List[str]] = None,
                        log_callback: Optional[Callable] = None) -> bool:
    """
    P1: Валидация входного пути к файлу.
    
    Args:
        path: Путь к файлу
        must_exist: Требовать существование файла
        allowed_extensions: Список допустимых расширений (например ['.mp4', '.mp3'])
        log_callback: Функция логирования
        
    Returns:
        True если путь валиден
    """
    _log = log_callback or (lambda x: logging.debug(x))
    
    if not path:
        _log("❌ Путь не указан")
        return False
    
    path_obj = Path(path)
    
    # Проверяем существование
    if must_exist and not path_obj.exists():
        _log(f"❌ Файл не найден: {path}")
        return False
    
    # Проверяем расширение
    if allowed_extensions:
        ext = path_obj.suffix.lower()
        if ext not in [e.lower() for e in allowed_extensions]:
            _log(f"❌ Недопустимое расширение {ext}, ожидается: {allowed_extensions}")
            return False
    
    # Проверяем что это файл (не директория)
    if must_exist and path_obj.exists() and not path_obj.is_file():
        _log(f"❌ Путь указывает на директорию, а не файл: {path}")
        return False
    
    return True


def validate_input_paths(paths: List[str], must_exist: bool = True,
                         allowed_extensions: Optional[List[str]] = None,
                         log_callback: Optional[Callable] = None) -> Tuple[List[str], List[str]]:
    """
    P1: Валидация списка входных путей.
    
    Args:
        paths: Список путей
        must_exist: Требовать существование файлов
        allowed_extensions: Допустимые расширения
        log_callback: Функция логирования
        
    Returns:
        Tuple (valid_paths, invalid_paths)
    """
    valid = []
    invalid = []
    
    for path in paths:
        if validate_input_path(path, must_exist, allowed_extensions, log_callback):
            valid.append(path)
        else:
            invalid.append(path)
    
    return valid, invalid


def validate_output_path(path: str, create_parent: bool = True,
                         log_callback: Optional[Callable] = None) -> bool:
    """
    P1: Валидация выходного пути.
    
    Args:
        path: Путь для выходного файла
        create_parent: Создать родительскую директорию если не существует
        log_callback: Функция логирования
        
    Returns:
        True если путь валиден для записи
    """
    _log = log_callback or (lambda x: logging.debug(x))
    
    if not path:
        _log("❌ Выходной путь не указан")
        return False
    
    path_obj = Path(path)
    parent = path_obj.parent
    
    # Создаём родительскую директорию
    if create_parent and not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            _log(f"❌ Не удалось создать директорию {parent}: {e}")
            return False
    
    # Проверяем что родительская директория существует и доступна для записи
    if not parent.exists():
        _log(f"❌ Директория не существует: {parent}")
        return False
    
    # Проверяем права на запись
    try:
        test_file = parent / f'_write_test_{os.getpid()}.tmp'
        test_file.write_text('test')
        test_file.unlink()
    except Exception as e:
        _log(f"❌ Нет прав на запись в {parent}: {e}")
        return False
    
    return True


def retry_call(
    func: Callable,
    args: tuple = (),
    kwargs: dict = None,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple = (Exception,),
    log_callback: Optional[Callable] = None
) -> Any:
    """
    Вызов функции с retry и exponential backoff.
    
    Args:
        func: Функция для вызова
        args: Позиционные аргументы
        kwargs: Именованные аргументы
        max_attempts: Максимальное количество попыток
        base_delay: Начальная задержка
        max_delay: Максимальная задержка
        exceptions: Исключения для перехвата
        log_callback: Функция логирования
    
    Returns:
        Результат функции
    """
    kwargs = kwargs or {}
    _log = log_callback or (lambda x: print(x))
    last_exception = None
    
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except exceptions as e:
            last_exception = e
            
            if attempt < max_attempts:
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                _log(f"⚠️ Попытка {attempt}/{max_attempts} не удалась: {str(e)[:100]}")
                _log(f"   🔄 Повтор через {delay:.1f}s...")
                time.sleep(delay)
            else:
                _log(f"❌ Все {max_attempts} попытки исчерпаны: {str(e)[:200]}")
    
    raise last_exception


class TempFileManager:
    """Менеджер временных файлов с гарантированной очисткой."""
    
    def __init__(self, log_callback: Optional[Callable] = None):
        self.temp_files: List[Path] = []
        self.log = log_callback or (lambda x: print(x))
    
    def add(self, file_path) -> None:
        """Добавить файл в список для очистки."""
        if file_path:
            self.temp_files.append(Path(file_path))
    
    def add_many(self, file_paths: List) -> None:
        """Добавить несколько файлов."""
        for fp in file_paths:
            self.add(fp)
    
    def cleanup(self, force: bool = False) -> int:
        """
        Очистить все временные файлы.
        
        Args:
            force: Удалять все файлы без проверки паттернов
            
        Returns:
            Количество удалённых файлов
        """
        cleaned = 0
        errors = []
        
        for file_path in self.temp_files:
            try:
                path_obj = Path(file_path)
                if not path_obj.exists():
                    continue
                
                if not path_obj.is_file():
                    continue
                
                # Проверяем, нужно ли удалять
                should_delete = force or self._should_delete(path_obj)
                
                if should_delete:
                    path_obj.unlink()
                    cleaned += 1
                    
            except Exception as e:
                errors.append(f"{file_path}: {e}")
        
        # Очищаем список
        self.temp_files.clear()
        
        if cleaned > 0:
            self.log(f"🗑️ Очищено {cleaned} временных файлов")
        
        if errors:
            self.log(f"⚠️ Не удалось удалить {len(errors)} файлов")
        
        return cleaned
    
    def _should_delete(self, path: Path) -> bool:
        """Проверить, нужно ли удалять файл."""
        name = path.name.lower()
        parent = str(path.parent).lower()
        
        # Паттерны для удаления
        delete_patterns = [
            'assets' in parent,
            'voiceover' in name,
            'final_audio' in name,
            'ai_image_' in name,
            name.startswith('voiceover_'),
            name.startswith('final_audio_'),
            name.startswith('music_only_'),  # 🎵 Музыка без озвучки
            name.startswith('silent_'),      # 🔇 Файлы тишины
            name.endswith('.ass'),
            'veo3_' in name,
            '_temp' in name,
            'filter_script_' in name,
            '_trimmed_audio' in name,
            '_with_intro' in name,
            '_padded_' in name,              # 📐 Уникализированные видео
            'youtube_clips' in parent,       # 🎬 YouTube клипы
            name.startswith('clip_'),        # 🎬 Нарезанные клипы
            'temp_combined_' in name,        # 🔧 Временные объединённые аудио
        ]
        
        return any(delete_patterns)
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, _exc_val, _exc_tb):
        self.cleanup(force=True)
        return False  # Не подавляем исключения


def get_enabled_metadata_dirs(settings: Optional[Dict] = None) -> set[str]:
    """Return final metadata folders enabled for the current generation."""
    resolved = dict(settings or {})
    if "user_settings" in resolved:
        resolved = dict(resolved.get("user_settings") or {})

    if not resolved:
        try:
            config_path = Path("config.json")
            if config_path.is_file():
                import json
                resolved = dict(
                    json.loads(config_path.read_text(encoding="utf-8")).get("user_settings") or {}
                )
        except Exception:
            resolved = {}

    # YouTube publishing reads the per-video text file from descriptions/.
    # Other metadata folders are redundant final artifacts and are removed.
    return {"descriptions"}


def cleanup_generated_folder(output_path: str, log_callback: Optional[Callable] = None,
                             keep_images: bool = True,
                             skip_youtube_clips: bool = False,
                             keep_youtube_download_cache: bool = False,
                             youtube_cache_max_mb: int = 2048,
                             keep_metadata: bool = False,
                             metadata_dirs_to_keep: Optional[Iterable[str]] = None,
                             keep_recovery_files: bool = False) -> Dict[str, int]:
    """
    🧹 Полная очистка временных файлов после генерации.
    
    Удаляет:
    - voiceover_*.mp3 файлы
    - assets/ папку (AI картинки) - ТОЛЬКО если keep_images=False
    - youtube_clips/ папку
    - logs/ папку (опционально)
    - Другие временные файлы
    
    Args:
        output_path: Путь к папке generated
        log_callback: Функция логирования
        keep_images: Сохранять картинки для кэширования (default: True)
        keep_recovery_files: Сохранять fingerprinted manifest незавершённой партии
        
    Returns:
        Словарь с количеством удалённых файлов по категориям
    """
    import shutil
    
    raw_log = log_callback or (lambda x: print(x))
    if hasattr(raw_log, "gui_callback"):
        raw_log = getattr(raw_log, "gui_callback") or (lambda _message: None)

    def _log(message):
        try:
            raw_log(message)
        except (UnicodeEncodeError, UnicodeDecodeError):
            try:
                raw_log(str(message).encode("ascii", errors="replace").decode("ascii"))
            except Exception:
                pass
        except Exception:
            pass
    output_dir = Path(output_path)
    
    if not output_dir.exists():
        return {'total': 0}
    
    stats = {
        'voiceover_files': 0,
        'assets_folders': 0,
        'youtube_clips': 0,
        'technical_folders': 0,
        'temp_files': 0,
        'total_size_mb': 0.0
    }
    
    total_size = 0
    
    # 1. Удаляем временные voiceover-файлы (Edge master хранится в PCM WAV).
    for pattern in ('voiceover_*.mp3', 'voiceover_*.wav', '*.edge_raw.mp3'):
        for audio_file in output_dir.glob(pattern):
            try:
                total_size += audio_file.stat().st_size
                audio_file.unlink()
                stats['voiceover_files'] += 1
            except Exception as e:
                _log(f"⚠️ Не удалось удалить {audio_file.name}: {e}")
    
    # Утилита для надежного удаления директорий на Windows
    def rmtree_onerror(func, path, _exc_info):
        import stat
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass

    def remove_directory(dirname: str, stat_key: str = "technical_folders") -> None:
        nonlocal total_size
        dir_path = output_dir / dirname
        if not dir_path.exists():
            return
        try:
            for item in dir_path.rglob("*"):
                if item.is_file():
                    total_size += item.stat().st_size
            shutil.rmtree(dir_path, onerror=rmtree_onerror)
            stats[stat_key] += 1
            _log(f"   Removed technical folder: {dirname}/")
        except Exception as exc:
            _log(f"Could not remove {dirname}/: {exc}")

    # 3. Удаляем youtube_clips/ папку
    clips_dir = output_dir / 'youtube_clips'
    if clips_dir.exists() and not skip_youtube_clips:
        try:
            for f in clips_dir.rglob('*'):
                if f.is_file():
                    total_size += f.stat().st_size
            shutil.rmtree(clips_dir, onerror=rmtree_onerror)
            stats['youtube_clips'] += 1
        except Exception as e:
            _log(f"⚠️ Не удалось удалить youtube_clips: {e}")
    
    # 4. Remove working folders after a successful generation. Finished
    # customer-facing output must contain only videos and enabled metadata.
    for dirname in (
        "_remake_analysis",
        "stock_clips",
        "live_checks",
        "image_cache",
        "image_pools",
        "pixabay_images",
        "freesound_music",
        "freesound_sfx",
        "logs",
    ):
        remove_directory(dirname)

    # YouTube downloads are working files. Keep publisher state stored beside
    # them: deleting the whole directory loses OAuth and the upload queue.
    youtube_cache_dir = Path("youtube_cache")
    if (
        youtube_cache_dir.exists()
        and youtube_cache_dir.resolve() != output_dir.resolve()
        and keep_youtube_download_cache
    ):
        try:
            downloads_dir = youtube_cache_dir / "downloads"
            cache_files = [path for path in downloads_dir.rglob("*") if path.is_file()] if downloads_dir.exists() else []
            cache_size = sum(path.stat().st_size for path in cache_files)
            max_bytes = max(0, int(youtube_cache_max_mb)) * 1024 * 1024
            removed_size = 0
            if max_bytes and cache_size > max_bytes:
                for path in sorted(cache_files, key=lambda item: item.stat().st_mtime):
                    if cache_size - removed_size <= max_bytes:
                        break
                    size = path.stat().st_size
                    path.unlink(missing_ok=True)
                    removed_size += size
                for directory in sorted(
                    (path for path in downloads_dir.rglob("*") if path.is_dir()),
                    key=lambda item: len(item.parts),
                    reverse=True,
                ):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            kept_mb = max(0, cache_size - removed_size) / (1024 * 1024)
            _log(f"   YouTube source cache retained: {kept_mb:.1f} MB")
        except Exception as exc:
            _log(f"Could not prune YouTube download cache: {exc}")
    elif youtube_cache_dir.exists() and youtube_cache_dir.resolve() != output_dir.resolve():
        try:
            persistent_names = {
                ".env",
                "youtube_publish_queue.json",
                "youtube_oauth_token.json",
                "youtube_oauth_client.json",
                "client_secret.json",
                "client_secrets.json",
            }
            removed_items = 0
            for item in list(youtube_cache_dir.iterdir()):
                if item.name in persistent_names or item.name.endswith((".backup", ".broken")):
                    continue
                if item.is_dir():
                    for child in item.rglob("*"):
                        if child.is_file():
                            total_size += child.stat().st_size
                    shutil.rmtree(item, onerror=rmtree_onerror)
                else:
                    total_size += item.stat().st_size
                    item.unlink()
                removed_items += 1
            if removed_items:
                stats["technical_folders"] += 1
                _log("   Cleaned YouTube download cache (OAuth and upload queue preserved)")
        except Exception as exc:
            _log(f"Could not clean YouTube download cache: {exc}")

    # 5. Удаляем различные папки с метаданными
    known_metadata_dirs = {'metadata', 'instagram_metadata', 'descriptions'}
    if not keep_metadata:
        metadata_dirs = known_metadata_dirs
    elif metadata_dirs_to_keep is None:
        metadata_dirs = set()
    else:
        metadata_dirs = known_metadata_dirs - set(metadata_dirs_to_keep)
    for dirname in metadata_dirs:
        dir_path = output_dir / dirname
        if dir_path.exists():
            try:
                for f in dir_path.rglob('*'):
                    if f.is_file():
                        total_size += f.stat().st_size
                shutil.rmtree(dir_path, onerror=rmtree_onerror)
                stats['temp_files'] += 1
                _log(f"   🗑️ Папка {dirname}/ удалена")
            except Exception as e:
                _log(f"⚠️ Не удалось удалить {dirname}: {e}")
    
    # 6. Удаляем папки с картинками (если keep_images=False)
    if not keep_images:
        image_dirs = ['assets', 'image_cache']
        for dirname in image_dirs:
            remove_directory(dirname, "assets_folders")
    
    # 7. Удаляем другие временные файлы
    temp_patterns = [
        '*.ass',           # Субтитры
        '*_temp*',         # Временные файлы
        'final_audio_*',   # Финальное аудио
        'music_only_*',    # Музыка
        'silent_*',        # Тишина
        '_padded_*',       # Уникализированные
        'clip_*',          # Клипы
        'ffmpeg_log_*.txt',
        'completed_videos.json',  # Legacy map; batch_manifest.json is authoritative.
    ]
    if not keep_recovery_files:
        temp_patterns.append('batch_manifest.json')
    
    for pattern in temp_patterns:
        for temp_file in output_dir.glob(pattern):
            if temp_file.is_file():
                try:
                    total_size += temp_file.stat().st_size
                    temp_file.unlink()
                    stats['temp_files'] += 1
                except Exception:
                    pass
    
    stats['total_size_mb'] = total_size / (1024 * 1024)
    
    total_cleaned = (
        stats['voiceover_files']
        + stats['assets_folders']
        + stats['youtube_clips']
        + stats['technical_folders']
        + stats['temp_files']
    )
    
    if total_cleaned > 0:
        _log("🧹 Очистка завершена:")
        if stats['voiceover_files'] > 0:
            _log(f"   📢 Voiceover файлы: {stats['voiceover_files']}")
        if stats['assets_folders'] > 0:
            _log(f"   🖼️ Assets папки: {stats['assets_folders']}")
        if stats['youtube_clips'] > 0:
            _log(f"   🎬 YouTube clips: {stats['youtube_clips']}")
        if stats['temp_files'] > 0:
            _log(f"   📄 Временные файлы: {stats['temp_files']}")
        _log(f"   💾 Освобождено: {stats['total_size_mb']:.1f} MB")
    
    return stats


def safe_path(path: str, max_length: int = 200) -> str:
    """
    Безопасный путь для Windows (избегаем длинных путей).
    
    Args:
        path: Исходный путь
        max_length: Максимальная длина имени файла
        
    Returns:
        Безопасный путь
    """
    import re
    
    path_obj = Path(path)
    name = path_obj.stem
    ext = path_obj.suffix
    parent = path_obj.parent
    
    # Убираем запрещённые символы
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    name = name.rstrip('.')
    
    # Обрезаем если слишком длинное
    if len(name) > max_length:
        name = name[:max_length]
    
    return str(parent / f"{name}{ext}")


def validate_api_key(api_key: str, key_type: str = "gemini") -> bool:
    """
    Валидация API ключа.
    
    Args:
        api_key: API ключ
        key_type: Тип ключа (gemini, google_ai)
        
    Returns:
        True если ключ валиден
    """
    if not api_key:
        return False
    
    if not isinstance(api_key, str):
        return False
    
    # Минимальная длина
    if len(api_key) < 20:
        return False
    
    # Gemini ключи начинаются с AIza
    if key_type == "gemini" and not api_key.startswith("AIza"):
        return False
    
    return True


# ============================================================
# 🔒 ОЧИСТКА МЕТАДАННЫХ ВИДЕО (Anti-Detection)
# ============================================================

def strip_video_metadata(video_path: str, log_callback: Optional[Callable] = None) -> bool:
    """
    Удаляет ВСЕ метаданные из видео файла для анти-детекции.
    
    Удаляет:
    - Encoder info (ffmpeg, x264, etc.)
    - Creation date/time
    - Software tags
    - GPS/location data
    - Custom metadata
    
    Args:
        video_path: Путь к видео файлу
        log_callback: Функция логирования
        
    Returns:
        True если успешно, False при ошибке
    """
    import subprocess
    import random
    
    _log = log_callback or (lambda x: print(x))
    
    try:
        video_path = Path(video_path)
        if not video_path.exists():
            _log(f"⚠️ Файл не найден: {video_path}")
            return False
        
        # Создаём временный файл
        temp_path = video_path.parent / f"_clean_{random.randint(1000, 9999)}_{video_path.name}"
        
        # FFmpeg команда для удаления ВСЕХ метаданных
        cmd = [
            'ffmpeg', '-y',
            '-i', str(video_path),
            '-map_metadata', '-1',           # Удалить все метаданные
            '-map_chapters', '-1',           # Удалить главы
            '-fflags', '+bitexact',          # Убрать timestamps encoder
            '-flags:v', '+bitexact',         # Видео без encoder info
            '-flags:a', '+bitexact',         # Аудио без encoder info
            '-c', 'copy',                    # Копировать без перекодирования
            str(temp_path)
        ]
        
        result = run_registered(
            cmd, 
            label="ffmpeg_video_metadata_strip",
            capture_output=True, 
            text=True, 
            encoding='utf-8', 
            errors='ignore',
            timeout=120
        )
        
        if result.returncode != 0:
            # Fallback: попробуем без bitexact (некоторые кодеки не поддерживают)
            cmd_fallback = [
                'ffmpeg', '-y',
                '-i', str(video_path),
                '-map_metadata', '-1',
                '-c', 'copy',
                str(temp_path)
            ]
            result = run_registered(
                cmd_fallback, 
                label="ffmpeg_video_metadata_strip_fallback",
                capture_output=True, 
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=120
            )
        
        if result.returncode == 0 and temp_path.exists() and temp_path.stat().st_size > 0:
            # Заменяем оригинал с retry для Windows file locking
            max_retries = 5
            for retry in range(max_retries):
                try:
                    os.replace(temp_path, video_path)
                    _log(f"🔒 Метаданные очищены: {video_path.name}")
                    return True
                except PermissionError:
                    if retry < max_retries - 1:
                        time.sleep(1)  # Ждём освобождения файла
                    else:
                        # Не удалось заменить - удаляем временный файл
                        if temp_path.exists():
                            temp_path.unlink(missing_ok=True)
                        _log("⚠️ Файл занят, пропускаем очистку метаданных")
                        return False
        else:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            _log(f"⚠️ Не удалось очистить метаданные: {result.stderr[:200] if result.stderr else 'unknown error'}")
            return False
            
    except subprocess.TimeoutExpired:
        _log("⚠️ Таймаут очистки метаданных")
        if 'temp_path' in locals() and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False
    except Exception as e:
        _log(f"⚠️ Ошибка очистки метаданных: {e}")
        if 'temp_path' in locals() and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False


def get_video_fingerprint(video_path: str) -> Optional[str]:
    """
    Получает уникальный fingerprint видео.
    
    Используется для проверки что видео действительно уникально.
    
    Returns:
        MD5 хэш первых 1MB файла + размер
    """
    import hashlib
    
    try:
        video_path = Path(video_path)
        if not video_path.exists():
            return None
        
        # Читаем первый 1MB
        with open(video_path, 'rb') as f:
            data = f.read(1024 * 1024)
        
        file_size = video_path.stat().st_size
        md5_hash = hashlib.md5(data).hexdigest()
        
        return f"{md5_hash}_{file_size}"
        
    except Exception:
        return None


# ============================================================
# 🚀 PARALLEL PROCESSING UTILITIES
# ProcessPoolExecutor для CPU-bound задач + Backpressure
# ============================================================

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed, Future
import multiprocessing


class BoundedExecutor:
    """
    Executor с ограниченной очередью (backpressure).
    
    Предотвращает OOM при большом количестве задач.
    Блокирует submit() когда очередь заполнена.
    
    Example:
        >>> with BoundedExecutor(max_workers=4, max_queue=10) as executor:
        ...     for task in tasks:
        ...         executor.submit(process_task, task)  # Блокируется если очередь полна
    """
    
    def __init__(self, max_workers: int = None, max_queue: int = 100, 
                 use_processes: bool = False):
        """
        Args:
            max_workers: Количество воркеров (default: CPU count)
            max_queue: Максимальный размер очереди задач
            use_processes: True для ProcessPoolExecutor (CPU-bound),
                          False для ThreadPoolExecutor (I/O-bound)
        """
        self.max_workers = max_workers or max(1, safe_cpu_count() // 2)
        self.max_queue = max_queue
        self.use_processes = use_processes
        
        # Семафор для backpressure
        self._semaphore = multiprocessing.Semaphore(max_queue) if use_processes else \
                         __import__('threading').Semaphore(max_queue)
        
        self._executor = None
        self._futures: List[Future] = []
    
    def __enter__(self):
        if self.use_processes:
            self._executor = ProcessPoolExecutor(max_workers=self.max_workers)
        else:
            self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        return self
    
    def __exit__(self, exc_type, _exc_val, _exc_tb):
        if self._executor:
            self._executor.shutdown(wait=True)
        return False
    
    def submit(self, fn, *args, **kwargs) -> Future:
        """
        Отправляет задачу на выполнение.
        Блокируется если очередь заполнена (backpressure).
        """
        self._semaphore.acquire()
        
        future = self._executor.submit(fn, *args, **kwargs)
        future.add_done_callback(lambda f: self._semaphore.release())
        self._futures.append(future)
        
        return future
    
    def map(self, fn, *iterables, timeout=None):
        """Параллельный map с backpressure."""
        return self._executor.map(fn, *iterables, timeout=timeout)
    
    def results(self, timeout: float = None) -> List[Any]:
        """Получает результаты всех задач."""
        results = []
        for future in as_completed(self._futures, timeout=timeout):
            try:
                results.append(future.result())
            except Exception as e:
                results.append(e)
        return results


def run_cpu_bound_parallel(
    tasks: List[Any],
    worker_fn: Callable,
    max_workers: int = None,
    max_queue: int = 50,
    timeout_per_task: float = 300,
    log_callback: Optional[Callable] = None
) -> List[Any]:
    """
    Выполняет CPU-bound задачи параллельно через ProcessPoolExecutor.
    
    Использует ProcessPoolExecutor для обхода GIL.
    Включает backpressure для предотвращения OOM.
    
    Args:
        tasks: Список задач (аргументов для worker_fn)
        worker_fn: Функция-воркер (должна быть picklable!)
        max_workers: Количество процессов (default: CPU/2)
        max_queue: Максимум задач в очереди
        timeout_per_task: Таймаут на одну задачу
        log_callback: Функция логирования
        
    Returns:
        Список результатов (в том же порядке что и tasks)
        
    Example:
        >>> def process_video(path):
        ...     # CPU-intensive ffmpeg operation
        ...     return result
        >>> results = run_cpu_bound_parallel(video_paths, process_video)
        
    Note:
        worker_fn должна быть определена на уровне модуля (не lambda, не вложенная)
        для корректной сериализации через pickle.
    """
    _log = log_callback or (lambda x: None)
    
    if not tasks:
        return []
    
    # Определяем количество воркеров
    cpu_count = safe_cpu_count()
    if max_workers is None:
        max_workers = max(1, cpu_count // 2)
    max_workers = min(max_workers, len(tasks), cpu_count)
    
    _log(f"🚀 Запуск {len(tasks)} CPU-bound задач на {max_workers} процессах")
    
    results = [None] * len(tasks)
    completed = 0
    
    try:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # Отправляем задачи с индексами для сохранения порядка
            future_to_idx = {
                executor.submit(worker_fn, task): idx 
                for idx, task in enumerate(tasks)
            }
            
            for future in as_completed(future_to_idx, timeout=timeout_per_task * len(tasks)):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result(timeout=timeout_per_task)
                    completed += 1
                except Exception as e:
                    _log(f"   ⚠️ Задача {idx} не удалась: {str(e)[:50]}")
                    results[idx] = None
                
                # Прогресс
                if completed % max(1, len(tasks) // 10) == 0:
                    _log(f"   📊 Прогресс: {completed}/{len(tasks)}")
    
    except Exception as e:
        _log(f"❌ Ошибка параллельного выполнения: {e}")
    
    _log(f"✅ Завершено: {completed}/{len(tasks)} задач")
    return results


def run_io_bound_parallel(
    tasks: List[Any],
    worker_fn: Callable,
    max_workers: int = 10,
    max_queue: int = 100,
    timeout_per_task: float = 60,
    log_callback: Optional[Callable] = None
) -> List[Any]:
    """
    Выполняет I/O-bound задачи параллельно через ThreadPoolExecutor.
    
    Для сетевых запросов, чтения файлов и т.д.
    Включает backpressure для предотвращения OOM.
    
    Args:
        tasks: Список задач
        worker_fn: Функция-воркер
        max_workers: Количество потоков
        max_queue: Максимум задач в очереди
        timeout_per_task: Таймаут на одну задачу
        log_callback: Функция логирования
        
    Returns:
        Список результатов
    """
    _log = log_callback or (lambda x: None)
    
    if not tasks:
        return []
    
    max_workers = min(max_workers, len(tasks))
    _log(f"🌐 Запуск {len(tasks)} I/O задач на {max_workers} потоках")
    
    results = [None] * len(tasks)
    completed = 0
    
    # Используем BoundedExecutor для backpressure
    semaphore = __import__('threading').Semaphore(max_queue)
    
    def bounded_worker(idx, task):
        try:
            return idx, worker_fn(task)
        finally:
            semaphore.release()
    
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            
            for idx, task in enumerate(tasks):
                semaphore.acquire()  # Backpressure
                future = executor.submit(bounded_worker, idx, task)
                futures.append(future)
            
            for future in as_completed(futures, timeout=timeout_per_task * len(tasks)):
                try:
                    idx, result = future.result(timeout=timeout_per_task)
                    results[idx] = result
                    completed += 1
                except Exception as e:
                    _log(f"   ⚠️ Задача не удалась: {str(e)[:50]}")
    
    except Exception as e:
        _log(f"❌ Ошибка параллельного выполнения: {e}")
    
    _log(f"✅ Завершено: {completed}/{len(tasks)} задач")
    return results


# Глобальный пул процессов для переиспользования
_global_process_pool: Optional[ProcessPoolExecutor] = None
_global_pool_lock = __import__('threading').Lock()


def get_process_pool(max_workers: int = None) -> ProcessPoolExecutor:
    """
    Получает глобальный пул процессов (singleton).
    
    Переиспользование пула экономит время на создание процессов.
    
    Args:
        max_workers: Количество воркеров (игнорируется если пул уже создан)
        
    Returns:
        ProcessPoolExecutor
    """
    global _global_process_pool
    
    with _global_pool_lock:
        if _global_process_pool is None:
            workers = max_workers or max(1, safe_cpu_count() // 2)
            _global_process_pool = ProcessPoolExecutor(max_workers=workers)
        return _global_process_pool


def shutdown_process_pool():
    """Завершает глобальный пул процессов."""
    global _global_process_pool
    
    with _global_pool_lock:
        if _global_process_pool is not None:
            _global_process_pool.shutdown(wait=True)
            _global_process_pool = None


# ============================================================
# 🎯 GLOBAL THREAD POOL - Централизованное управление потоками
# Решает проблему вложенной параллельности 3 уровня
# ============================================================

import threading
from typing import Dict
from contextlib import contextmanager


class GlobalThreadPool:
    """
    Глобальный менеджер потоков для ограничения вложенной параллельности.
    
    Проблема: При 3 параллельных видео создаётся 50+ потоков:
    - Уровень 1: 3 потока (видео)
    - Уровень 2: 3×4 = 12 потоков (аудио/картинки/YouTube)
    - Уровень 3: 3×4×4 = 48 потоков (вложенные операции)
    
    Решение: Глобальный лимит потоков + приоритеты по уровням.
    
    Архитектура:
    ```
    GlobalThreadPool (max=24 потока)
    ├── HIGH priority (рендер, NVENC) - гарантированно 3 слота
    ├── MEDIUM priority (аудио, картинки) - до 8 слотов
    └── LOW priority (YouTube, анализ) - остальное
    ```
    
    Usage:
        >>> pool = GlobalThreadPool.instance()
        >>> with pool.acquire_slot('youtube', priority='low'):
        ...     # Выполняем работу в ограниченном потоке
        ...     download_video()
        
        >>> # Или через executor
        >>> future = pool.submit(download_video, url, priority='low', category='youtube')
    """
    
    _instance = None
    _lock = threading.Lock()
    
    # Лимиты по умолчанию
    DEFAULT_MAX_THREADS = 24  # Общий лимит
    PRIORITY_SLOTS = {
        'high': 6,    # NVENC, критичные операции
        'medium': 10, # Аудио, картинки
        'low': 8      # YouTube, анализ
    }
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance
    
    def _init(self):
        """Инициализация singleton."""
        self._max_threads = self.DEFAULT_MAX_THREADS
        
        # Семафоры для каждого приоритета
        self._semaphores: Dict[str, threading.Semaphore] = {
            'high': threading.Semaphore(self.PRIORITY_SLOTS['high']),
            'medium': threading.Semaphore(self.PRIORITY_SLOTS['medium']),
            'low': threading.Semaphore(self.PRIORITY_SLOTS['low'])
        }
        
        # Глобальный семафор (общий лимит)
        self._global_semaphore = threading.Semaphore(self._max_threads)
        
        # Статистика
        self._active_threads: Dict[str, int] = {'high': 0, 'medium': 0, 'low': 0}
        self._stats_lock = threading.Lock()
        self._total_acquired = 0
        self._total_released = 0
        self._peak_threads = 0
        
        # Категории и их приоритеты
        self._category_priority: Dict[str, str] = {
            # High priority - GPU/критичные
            'nvenc': 'high',
            'render': 'high',
            'veo3': 'high',
            
            # Medium priority - API calls
            'tts': 'medium',
            'audio': 'medium',
            'imagen': 'medium',
            'images': 'medium',
            'gemini': 'medium',
            
            # Low priority - сеть/CPU
            'youtube': 'low',
            'download': 'low',
            'ffmpeg': 'low',
            'analysis': 'low',
            'default': 'low'
        }
        
        # Executor для submit()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._executor_lock = threading.Lock()
    
    @classmethod
    def instance(cls) -> 'GlobalThreadPool':
        """Получить singleton экземпляр."""
        return cls()
    
    @classmethod
    def configure(cls, max_threads: int = None, priority_slots: Dict[str, int] = None):
        """
        Конфигурирует глобальный пул (вызывать до первого использования).
        
        Args:
            max_threads: Общий лимит потоков
            priority_slots: Лимиты по приоритетам {'high': N, 'medium': M, 'low': K}
        """
        pool = cls.instance()
        
        if max_threads:
            pool._max_threads = max_threads
            pool._global_semaphore = threading.Semaphore(max_threads)
        
        if priority_slots:
            for priority, slots in priority_slots.items():
                if priority in pool._semaphores:
                    pool._semaphores[priority] = threading.Semaphore(slots)
                    pool.PRIORITY_SLOTS[priority] = slots
    
    def get_priority(self, category: str) -> str:
        """Получить приоритет для категории."""
        return self._category_priority.get(category.lower(), 'low')
    
    @contextmanager
    def acquire_slot(self, category: str = 'default', priority: str = None, 
                     timeout: float = 300):
        """
        Захватывает слот в пуле потоков.
        
        Args:
            category: Категория операции (youtube, tts, render, etc.)
            priority: Приоритет (high/medium/low), если None - определяется по категории
            timeout: Таймаут ожидания слота
            
        Yields:
            True если слот получен
            
        Example:
            >>> with pool.acquire_slot('youtube'):
            ...     download_video()
        """
        if priority is None:
            priority = self.get_priority(category)
        
        priority_sem = self._semaphores.get(priority, self._semaphores['low'])
        
        # Захватываем оба семафора: приоритетный + глобальный
        acquired_priority = False
        acquired_global = False
        
        try:
            # Сначала глобальный (общий лимит)
            acquired_global = self._global_semaphore.acquire(timeout=timeout)
            if not acquired_global:
                raise TimeoutError(f"GlobalThreadPool: таймаут ожидания глобального слота ({timeout}s)")
            
            # Затем приоритетный
            acquired_priority = priority_sem.acquire(timeout=timeout)
            if not acquired_priority:
                self._global_semaphore.release()
                raise TimeoutError(f"GlobalThreadPool: таймаут ожидания {priority} слота ({timeout}s)")
            
            # Обновляем статистику
            with self._stats_lock:
                self._active_threads[priority] += 1
                self._total_acquired += 1
                current_total = sum(self._active_threads.values())
                self._peak_threads = max(self._peak_threads, current_total)
            
            yield True
            
        finally:
            # Освобождаем в обратном порядке
            if acquired_priority:
                priority_sem.release()
                with self._stats_lock:
                    self._active_threads[priority] -= 1
                    self._total_released += 1
            
            if acquired_global:
                self._global_semaphore.release()
    
    def submit(self, fn: Callable, *args, category: str = 'default', 
               priority: str = None, **kwargs) -> Future:
        """
        Отправляет задачу на выполнение с учётом лимитов.
        
        Args:
            fn: Функция для выполнения
            *args: Аргументы функции
            category: Категория операции
            priority: Приоритет
            **kwargs: Именованные аргументы функции
            
        Returns:
            Future для отслеживания результата
        """
        # Ленивая инициализация executor
        with self._executor_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_threads,
                    thread_name_prefix="GlobalPool"
                )
        
        def wrapped_fn():
            with self.acquire_slot(category, priority):
                return fn(*args, **kwargs)
        
        return self._executor.submit(wrapped_fn)
    
    def map(self, fn: Callable, items: List[Any], category: str = 'default',
            priority: str = None, timeout: float = None) -> List[Any]:
        """
        Параллельный map с учётом лимитов.
        
        Args:
            fn: Функция для применения
            items: Список элементов
            category: Категория операции
            priority: Приоритет
            timeout: Общий таймаут
            
        Returns:
            Список результатов
        """
        futures = [self.submit(fn, item, category=category, priority=priority) 
                   for item in items]
        
        results = []
        for future in as_completed(futures, timeout=timeout):
            try:
                results.append(future.result())
            except Exception:
                results.append(None)
        
        return results
    
    def get_stats(self) -> Dict:
        """Возвращает статистику использования."""
        with self._stats_lock:
            return {
                'active': dict(self._active_threads),
                'total_active': sum(self._active_threads.values()),
                'max_threads': self._max_threads,
                'total_acquired': self._total_acquired,
                'total_released': self._total_released,
                'peak_threads': self._peak_threads,
                'limits': dict(self.PRIORITY_SLOTS)
            }
    
    def get_stats_str(self) -> str:
        """Возвращает статистику в виде строки."""
        stats = self.get_stats()
        return (f"🧵 Потоки: {stats['total_active']}/{stats['max_threads']} "
                f"(H:{stats['active']['high']} M:{stats['active']['medium']} L:{stats['active']['low']}) "
                f"peak:{stats['peak_threads']}")
    
    def shutdown(self, wait: bool = True):
        """Завершает пул."""
        with self._executor_lock:
            if self._executor:
                self._executor.shutdown(wait=wait)
                self._executor = None


# Глобальный экземпляр
_global_thread_pool: Optional[GlobalThreadPool] = None


def get_global_thread_pool() -> GlobalThreadPool:
    """Получить глобальный пул потоков."""
    global _global_thread_pool
    if _global_thread_pool is None:
        _global_thread_pool = GlobalThreadPool.instance()
    return _global_thread_pool


@contextmanager
def thread_slot(category: str = 'default', priority: str = None, timeout: float = 300):
    """
    Удобный контекстный менеджер для захвата слота.
    
    Example:
        >>> with thread_slot('youtube'):
        ...     download_video()
        
        >>> with thread_slot('render', priority='high'):
        ...     render_video()
    """
    pool = get_global_thread_pool()
    with pool.acquire_slot(category, priority, timeout):
        yield


# ============================================================
# 🔧 FFMPEG PATH UTILITIES
# ============================================================

def get_ffmpeg_path() -> str:
    """
    Возвращает путь к ffmpeg - универсальный для всех ОС.
    
    Приоритет поиска:
    1. Локальный FFmpeg в tools/ffmpeg/ (полная сборка)
    2. Системный ffmpeg в PATH
    3. imageio-ffmpeg (урезанная сборка, fallback)
    4. Fallback на 'ffmpeg' (надеемся что в PATH)
    
    Returns:
        str: Путь к исполняемому файлу ffmpeg
        
    Example:
        >>> ffmpeg = get_ffmpeg_path()
        >>> subprocess.run([ffmpeg, '-version'])
        
    Note:
        Эта функция заменяет дублирующиеся _get_ffmpeg_path() 
        в audio_processor.py и video_renderer.py
    """
    import platform
    import shutil
    
    # Определяем расширение исполняемого файла в зависимости от ОС
    exe_ext = ".exe" if platform.system().lower() == "windows" else ""
    
    # 1. Проверяем локальный FFmpeg в tools/ffmpeg/ (полная сборка)
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_ffmpeg = os.path.join(script_dir, 'tools', 'ffmpeg', f'ffmpeg{exe_ext}')
    if os.path.exists(local_ffmpeg):
        return local_ffmpeg
    
    # 2. Проверяем системный ffmpeg
    if shutil.which('ffmpeg'):
        return 'ffmpeg'
    
    # 3. Пробуем imageio-ffmpeg (урезанная сборка, fallback)
    try:
        import imageio_ffmpeg
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_path and os.path.exists(ffmpeg_path):
            return ffmpeg_path
    except ImportError:
        pass
    except Exception:
        pass
    
    # 4. Fallback - надеемся что ffmpeg в PATH
    return 'ffmpeg'
