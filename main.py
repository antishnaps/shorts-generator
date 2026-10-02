#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ContentBot Pro application launcher."""

# ============================================================
# 🔇 КРИТИЧНО: Подавление warnings ДО ВСЕХ импортов (Python 3.14)
# ============================================================
import warnings
import sys
import os

from core.windows_subprocess import configure_hidden_subprocesses
from core.runtime_environment import is_packaged_runtime

configure_hidden_subprocesses()

if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ContentBotPro.Desktop.1"
        )
    except Exception:
        pass

# Подавляем ВСЕ warnings
warnings.filterwarnings('ignore')
os.environ['PYTHONWARNINGS'] = 'ignore'

# Подавляем HTTPResponse finalization errors и перехватываем все падения (Logless crash fix)
def _global_exception_handler(exc_type, exc_value, exc_traceback):
    """Глобальный перехватчик исключений - записывает ошибки в лог"""
    if exc_type is ValueError and 'I/O operation on closed file' in str(exc_value):
        return  # Игнорируем
    
    # Пытаемся записать в логгер, если он уже инициализирован
    try:
        import logging
        if logging.getLogger().handlers:
            logging.critical("Необработанное исключение:", exc_info=(exc_type, exc_value, exc_traceback))
    except Exception:
        pass
        
    # Выводим в стандартный обработчик (консоль)
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

sys.excepthook = _global_exception_handler

import threading
def _thread_exception_handler(args):
    _global_exception_handler(args.exc_type, args.exc_value, args.exc_traceback)
threading.excepthook = _thread_exception_handler

# ============================================================
# 🔧 КРИТИЧНО: Фикс окружения для предотвращения зависания
# Решает проблему зависания у некоторых пользователей
# ============================================================
import locale

# 1. LOCALE FIX: Принудительно устанавливаем C locale для чисел
# Это предотвращает проблему "запятая vs точка" в FFmpeg командах
# У пользователей с RU/DE локалью float форматируется как "0,5" вместо "0.5"
# FFmpeg не понимает запятую и спамит ошибки в stderr -> переполнение буфера -> deadlock
try:
    locale.setlocale(locale.LC_NUMERIC, 'C')
except Exception:
    pass

# 2. OPENCV MSMF FIX: Отключаем Microsoft Media Foundation backend
# MSMF часто зависает на Windows при открытии некоторых MP4/MKV файлов
# Принудительно используем FFmpeg backend (более стабильный)
os.environ["OPENCV_VIDEOIO_PRIORITY_MSMF"] = "0"
os.environ['QT_OPENGL'] = 'software'
os.environ['QT_LOGGING_RULES'] = '*.debug=false;qt.qpa.fonts=false;qt.qpa.fonts.warning=false;qt.qpa.fonts.debug=false'

# 3. STDOUT ENCODING FIX: Предотвращаем зависание на print() с кириллицей
# Если консоль не поддерживает UTF-8, print() может зависнуть
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


# ============================================================
# 🔧 КРИТИЧНО: Фикс Qt platform plugins ДО любых импортов PyQt
# ============================================================
import sys

# Фикс для "Could not find the Qt platform plugin windows"
# Нужно установить путь к плагинам ДО импорта PyQt5
def _fix_qt_plugins():
    """Устанавливает путь к Qt плагинам для PyQt5"""
    try:
        # Ищем PyQt5 в site-packages
        import site
        for site_dir in site.getsitepackages() + [site.getusersitepackages()]:
            if site_dir:
                qt_plugins = os.path.join(site_dir, 'PyQt5', 'Qt5', 'plugins')
                if os.path.exists(qt_plugins):
                    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = qt_plugins
                    return
                # Альтернативный путь
                qt_plugins2 = os.path.join(site_dir, 'PyQt5', 'Qt', 'plugins')
                if os.path.exists(qt_plugins2):
                    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = qt_plugins2
                    return
    except Exception:
        # Игнорируем ошибки поиска PyQt5
        pass
    
    # Fallback: ищем в venv
    venv_path = os.path.join(os.path.dirname(__file__), 'venv', 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    if os.path.exists(venv_path):
        os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = venv_path

_fix_qt_plugins()

# ============================================================
# 🔧 Bytecode кэширование: оставляем включенным для быстрого запуска
# Python будет использовать .pyc файлы вместо перекомпиляции
# ============================================================

import logging
from pathlib import Path
from datetime import datetime

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    # Force UTF-8 for stdout/stderr
    import io
    
    # Проверяем что stdout/stderr существуют (в .exe могут быть None)
    if sys.stdout is not None and hasattr(sys.stdout, 'buffer'):
        # Сохраняем оригинальные buffer перед переопределением
        _original_stdout_buffer = sys.stdout.buffer
        _original_stderr_buffer = sys.stderr.buffer if sys.stderr is not None and hasattr(sys.stderr, 'buffer') else None
        
        sys.stdout = io.TextIOWrapper(_original_stdout_buffer, encoding='utf-8', errors='replace')
        if _original_stderr_buffer is not None:
            sys.stderr = io.TextIOWrapper(_original_stderr_buffer, encoding='utf-8', errors='replace')
        
        # Теперь фильтруем HTTPResponse errors в stderr
        if sys.stderr is not None and _original_stderr_buffer is not None:
            class SuppressHTTPErrors:
                def __init__(self, stream, original_buffer):
                    self.stream = stream
                    self.buffer = original_buffer  # Используем оригинальный buffer
                
                def write(self, data):
                    suppressed = (
                        'I/O operation on closed file' in data or
                        'http.client.HTTPResponse' in data or
                        'OpenType support missing' in data
                    )
                    if not suppressed:
                        self.stream.write(data)
                    return len(data)  # Возвращаем количество записанных байт
                
                def flush(self):
                    self.stream.flush()
                
                def __getattr__(self, name):
                    # Проксируем все остальные атрибуты к оригинальному stream
                    return getattr(self.stream, name)
            
            sys.stderr = SuppressHTTPErrors(sys.stderr, _original_stderr_buffer)

# Warnings уже подавлены в начале файла

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import qInstallMessageHandler

def _qt_message_handler(mode, context, message):
    if 'OpenType support missing' in message:
        return
    try:
        sys.__stderr__.write(message + '\n')
    except Exception:
        pass

qInstallMessageHandler(_qt_message_handler)

from gui.main_window_v2 import MainWindowV2
from gui.icons import brand_icon


def run_smoke_test(app_dir: Path) -> int:
    """Run a no-window startup check for packaged release verification."""
    failures = []

    def require(condition: bool, label: str) -> None:
        if not condition:
            failures.append(label)

    require((app_dir / "ContentBotPro.exe").is_file() or not is_packaged_runtime(), "application exe")

    if is_packaged_runtime():
        resource_roots = (app_dir, app_dir / "_internal")
        require(
            any((root / "config.json.template").is_file() for root in resource_roots),
            "config template",
        )
        require(
            any(
                (root / "assets" / "branding" / "contentbot_mark.png").is_file()
                for root in resource_roots
            ),
            "branding asset",
        )

        # YouTube's current extractor needs both the yt-dlp EJS solver data and
        # a supported JavaScript runtime.  Verify the actual packaged runtime so
        # a customer build cannot pass smoke testing while silently losing HD
        # formats on its first real download.
        try:
            import yt_dlp_ejs

            solver_root = Path(yt_dlp_ejs.__file__).resolve().parent / "yt" / "solver"
            require((solver_root / "core.min.js").is_file(), "YouTube EJS core solver")
            require((solver_root / "lib.min.js").is_file(), "YouTube EJS library")
        except Exception:
            require(False, "YouTube EJS package")

        try:
            from core.youtube.download_policy import detect_youtube_js_runtimes

            detect_youtube_js_runtimes.cache_clear()
            require(bool(detect_youtube_js_runtimes()), "YouTube JavaScript runtime")
        except Exception:
            require(False, "YouTube JavaScript runtime")
    else:
        require((app_dir / "config.json.template").is_file(), "config template")
        require((app_dir / "assets" / "branding" / "contentbot_mark.png").is_file(), "branding asset")

    if failures:
        print("SMOKE_FAIL " + "; ".join(failures))
        return 2
    print("SMOKE_OK")
    return 0


def check_ffmpeg() -> bool:
    """Check if FFmpeg is installed and accessible"""
    import subprocess
    import sys
    from core.process_registry import run_registered
    
    # Флаг для скрытия окна на Windows
    if sys.platform == 'win32':
        creation_flags = 0x08000000  # CREATE_NO_WINDOW
    else:
        creation_flags = 0
    
    # 1. Проверяем системный FFmpeg
    try:
        result = run_registered(
            ['ffmpeg', '-version'],
            label="startup_ffmpeg_check",
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=creation_flags
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    # 2. Пробуем imageio-ffmpeg (установлен через pip)
    try:
        import imageio_ffmpeg
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_path:
            # Добавляем в PATH для других модулей
            import os
            ffmpeg_dir = os.path.dirname(ffmpeg_path)
            os.environ['PATH'] = ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')
            print(f"✅ Используем imageio-ffmpeg: {ffmpeg_path}")
            return True
    except ImportError:
        pass
    except Exception:
        pass
    
    return False


def show_ffmpeg_error():
    """Show FFmpeg installation instructions"""
    from PyQt5.QtWidgets import QApplication, QMessageBox
    
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Critical)
    msg.setWindowTitle("FFmpeg не найден")
    msg.setText("FFmpeg не установлен или не найден в PATH!")
    msg.setInformativeText(
        "FFmpeg необходим для работы программы.\n\n"
        "Установка:\n"
        "1. Скачайте FFmpeg: https://ffmpeg.org/download.html\n"
        "2. Или через winget: winget install ffmpeg\n"
        "3. Или через choco: choco install ffmpeg\n\n"
        "После установки перезапустите программу."
    )
    msg.addButton("Открыть сайт FFmpeg", QMessageBox.ActionRole)
    msg.addButton("Выход", QMessageBox.RejectRole)
    
    result = msg.exec_()
    
    if result == 0:  # Open website
        import webbrowser
        webbrowser.open("https://ffmpeg.org/download.html")


def setup_logging():
    """Setup file logging for the application"""
    # Create logs directory
    logs_dir = Path('logs')
    logs_dir.mkdir(exist_ok=True)
    
    # Create log filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = logs_dir / f'app_{timestamp}.log'
    
    # Configure root logger
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Reduce noise from external libraries (Show only Warnings/Errors)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)
    logging.getLogger('moviepy').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    logging.getLogger('google').setLevel(logging.WARNING)
    logging.getLogger('google_genai').setLevel(logging.WARNING)
    # OAuth libraries can include access and refresh tokens in DEBUG messages.
    logging.getLogger('requests_oauthlib').setLevel(logging.WARNING)
    logging.getLogger('oauthlib').setLevel(logging.WARNING)
    
    logging.info(f"📝 Логирование запущено: {log_file}")
    return log_file


def main():
    """Launch the application"""
    # Windows may launch a packaged app with System32 as its working directory.
    app_dir = Path(sys.executable).resolve().parent if is_packaged_runtime() else Path(__file__).resolve().parent
    os.chdir(app_dir)

    if "--smoke-test" in sys.argv:
        sys.exit(run_smoke_test(app_dir))

    
    
    # Ensure config.json exists with default settings
    from core.config_manager import ensure_config_exists
    ensure_config_exists()
    
    
    # Setup logging first
    setup_logging()
    
    # Run startup validation
    from core.startup_validator import StartupValidator
    
    validator = StartupValidator(verbose=False)
    
    # Check if profile is fresh (< 7 days old)
    status = validator.run_quick_checks()
    
    # Check if system is ready
    if not status.ready:
        print("\n❌ Система не готова к запуску!")
        print("Исправьте ошибки выше и перезапустите программу.\n")
        sys.exit(1)
    
    # Show warnings but continue
    if status.warnings and validator.verbose:
        print("\n⚠️ Программа запустится с ограниченным функционалом")
        print("Некоторые фичи могут быть недоступны\n")
    
    # High DPI support - ОТКЛЮЧЕНО для совместимости с Windows 11
    # Некоторые системы показывают белое окно с включенным scaling
    # QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    # QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    app.setApplicationName("ContentBot Pro — Free")
    app.setWindowIcon(brand_icon())
    
    window = MainWindowV2()
    # Минимальный размер уже установлен в init_ui() через DPI-aware get_sizes()
    
    # Фикс белого окна: показываем окно после полной инициализации
    from PyQt5.QtCore import QTimer
    QTimer.singleShot(50, window.show)  # Задержка 50ms (было 100)
    
    logging.info("🚀 Приложение запущено")
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
