 #!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
📋 ОЧЕРЕДЬ ЗАДАЧ
Простая очередь тем для генерации. Все настройки берутся из первой вкладки.
"""

import json
import logging
import copy
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog,
    QProgressBar, QScrollArea, QFrame
)
from PyQt5.QtCore import QThread, pyqtSignal

# P4: Импорт констант
from PyQt5.QtGui import QColor

from core.settings_schema import (
    normalize_video_settings,
    normalize_subtitle_settings,
    normalize_audio_settings,
    normalize_overlay_settings,
    normalize_youtube_mixer_settings,
)
from core.gemini_models import DEFAULT_IMAGE_MODEL, image_model_from_ui_index
from gui.styles_v2 import (
    ColorsV2,
    get_ai_button_style,
    get_danger_button_style,
    get_ghost_button_style,
    get_primary_button_style,
)
from gui.localized_dialogs import (
    ask_yes_no,
    localize_dialog_button_box,
    show_critical,
    show_information,
    show_warning,
)
from gui.translations import t


def _runtime_bool(value) -> str:
    return t('runtime_value_yes') if value else t('runtime_value_no')


def _runtime_custom_text_position(value) -> str:
    key = {
        'start': 'runtime_position_start',
        'between': 'runtime_position_between',
        'split': 'runtime_position_split',
        'end': 'runtime_position_end',
    }.get(str(value).strip().lower())
    return t(key) if key else str(value)


def _runtime_content_language(value) -> str:
    normalized = str(value or '').strip()
    key = {
        'Russian': 'runtime_content_language_russian',
        'English': 'runtime_content_language_english',
        'Spanish': 'runtime_content_language_spanish',
        'French': 'runtime_content_language_french',
        'German': 'runtime_content_language_german',
        'Chinese': 'runtime_content_language_chinese',
        'Japanese': 'runtime_content_language_japanese',
        'Korean': 'runtime_content_language_korean',
        'Portuguese': 'runtime_content_language_portuguese',
        'Italian': 'runtime_content_language_italian',
        'Hindi': 'runtime_content_language_hindi',
        'Arabic': 'runtime_content_language_arabic',
    }.get(normalized)
    return t(key) if key else (normalized or '?')


def _runtime_persona(value) -> str:
    key = {
        None: 'persona_auto',
        '': 'persona_auto',
        'viral': 'persona_viral',
        'commentator': 'persona_ufc',
        'conspiracy': 'persona_conspiracy',
        'motivator': 'persona_motivator',
        'horror': 'persona_horror',
        'bro': 'persona_bro',
        'serious': 'persona_serious',
    }.get(value)
    return t(key) if key else str(value)


def _atomic_write_json(path: Path, data) -> None:
    path = Path(path)
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def _backup_broken_json(path: Path) -> None:
    try:
        path = Path(path)
        if path.exists():
            backup_path = path.with_suffix(path.suffix + '.broken')
            backup_path.write_text(path.read_text(encoding='utf-8', errors='ignore'), encoding='utf-8')
    except Exception as e:
        logging.debug(f"Не удалось сохранить backup битого JSON {path}: {e}")


@dataclass
class QueueTask:
    """
    Задача в очереди генерации видео.
    
    Attributes:
        theme: Тема для генерации видео
        num_videos: Количество видео для генерации по этой теме
        status: Статус задачи:
            - 'pending': ожидает выполнения
            - 'running': выполняется
            - 'completed': успешно завершена
            - 'failed': завершена с ошибкой
        task_settings: Снапшот настроек для задачи
        snapshot_version: Версия снапшота (для миграции)
    """
    theme: str
    num_videos: int
    status: str = "pending"  # pending, running, completed, failed
    task_settings: dict = None  # Снапшот настроек для задачи
    snapshot_version: int = 2  # Текущая версия снапшота
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'QueueTask':
        """Загружает задачу из словаря с автоматической миграцией"""
        version = data.get('snapshot_version', 1)
        
        # Миграция v1 → v2
        if version == 1:
            data = cls._migrate_v1_to_v2(data)
        
        return cls(
            theme=data.get('theme', ''),
            num_videos=data.get('num_videos', 1),
            status=data.get('status', 'pending'),
            task_settings=data.get('task_settings', None),
            snapshot_version=data.get('snapshot_version', 2)
        )
    
    @staticmethod
    def _migrate_v1_to_v2(data: dict) -> dict:
        """Миграция снапшота v1 → v2 (добавляет custom_text_snapshot)"""
        if 'task_settings' in data and data['task_settings']:
            settings = data['task_settings']
            
            # Добавляем custom_text_snapshot если его нет
            if 'custom_text_snapshot' not in settings:
                settings['custom_text_snapshot'] = {
                    'enabled': False,
                    'text': '',
                    'position': 'end',
                    'ai_customize': False,
                    'limit_2000': False,
                    'generate_insta_files': False,
                    'use_separate_metadata': False
                }
        
        data['snapshot_version'] = 2
        return data


class QueueWorker(QThread):
    """Воркер для выполнения очереди задач"""
    task_started = pyqtSignal(int, str)
    task_progress = pyqtSignal(int, int, str)
    task_completed = pyqtSignal(int, bool, str)
    queue_completed = pyqtSignal(int, int)
    log_message = pyqtSignal(str)
    new_image = pyqtSignal(str)  # Сигнал для обновления превью во время работы очереди
    
    def __init__(self, tasks: List[QueueTask], generator, settings: dict):
        super().__init__()
        self.tasks = tasks
        self.generator = generator
        self.settings = settings
        self._stop_requested = False
        self.stop_flag = False
    
    def stop(self):
        self._stop_requested = True
        self.stop_flag = True
    
    def _save_queue_to_file(self):
        """Сохраняет текущее состояние очереди в файл"""
        try:
            from pathlib import Path
            
            queue_file = Path("queue_autosave.json")
            tasks_data = []
            
            for task in self.tasks:
                tasks_data.append({
                    'theme': task.theme,
                    'num_videos': task.num_videos,
                    'status': task.status,
                    'task_settings': task.task_settings,
                    'snapshot_version': 2
                })
            
            _atomic_write_json(queue_file, tasks_data)
                
        except Exception as e:
            self.log_message.emit(
                t('queue_runtime_save_failed').format(error=e)
            )
    
    def image_callback(self, image_path: str) -> None:
        """Callback для превью — эмитит сигнал в главное окно."""
        self.new_image.emit(image_path)

    def _normalize_task_settings(self, settings: dict) -> dict:
        settings = copy.deepcopy(settings or {})
        settings['video_settings'] = normalize_video_settings(settings.get('video_settings', {}))
        settings['subtitle_settings'] = normalize_subtitle_settings(settings.get('subtitle_settings', {}))
        settings['audio_settings'] = normalize_audio_settings(settings.get('audio_settings', {}))
        settings['overlay_settings'] = normalize_overlay_settings(settings.get('overlay_settings', {}))
        youtube_settings = settings.get('youtube_mixer_settings') or settings.get('youtube_settings') or {}
        youtube_settings = normalize_youtube_mixer_settings(youtube_settings)
        settings['youtube_mixer_settings'] = youtube_settings
        settings['youtube_settings'] = youtube_settings
        if isinstance(settings.get('image_model'), int):
            settings['image_model'] = image_model_from_ui_index(settings.get('image_model'))
        return settings
    
    def run(self):
        logging.debug("QueueWorker.run started with %s tasks", len(self.tasks))
        for i, task in enumerate(self.tasks):
            logging.debug("Queue task %s: %s (%s)", i + 1, task.theme, task.status)
        
        completed = 0
        failed = 0
        
        for i, task in enumerate(self.tasks):
            if self._stop_requested:
                self.log_message.emit(t('queue_runtime_stopped'))
                break
            
            if task.status != "pending":
                continue
            
            self.task_started.emit(i, task.theme)
            self.log_message.emit(f"\n{'='*50}")
            self.log_message.emit(t('queue_runtime_task_heading').format(
                current=i + 1,
                total=len(self.tasks),
                theme=task.theme,
            ))
            self.log_message.emit(
                t('queue_runtime_video_count').format(count=task.num_videos)
            )
            self.log_message.emit(f"{'='*50}")
            
            try:
                # Берем настройки конкретной задачи (снапшот) если он есть, иначе общие (запасной вариант)
                task_settings = self._normalize_task_settings(task.task_settings if task.task_settings else self.settings)
                
                # 🔑 ИНЪЕКЦИЯ СВЕЖИХ API КЛЮЧЕЙ: Используем текущие ключи вместо старых из снапшота
                # Это позволяет использовать актуальные API ключи даже если задача была добавлена давно
                if self.settings.get('api_key'):
                    task_settings['api_key'] = self.settings['api_key']
                if self.settings.get('google_ai_api_key'):
                    task_settings['google_ai_api_key'] = self.settings['google_ai_api_key']
                
                # 🖼️ ИНЪЕКЦИЯ НАСТРОЕК КАСТОМНЫХ МАТЕРИАЛОВ: Используем текущие настройки
                # Это позволяет использовать готовые картинки/видео даже если задача была добавлена без них
                if self.settings.get('custom_images_folder'):
                    task_settings['custom_images_folder'] = self.settings['custom_images_folder']
                if self.settings.get('use_only_custom_images') is not None:
                    task_settings['use_only_custom_images'] = self.settings['use_only_custom_images']
                if self.settings.get('use_reference_images') is not None:
                    task_settings['use_reference_images'] = self.settings['use_reference_images']
                if self.settings.get('reference_images_folder'):
                    task_settings['reference_images_folder'] = self.settings['reference_images_folder']
                
                # 🎬 ИНЪЕКЦИЯ НАСТРОЕК КАСТОМНЫХ ВИДЕО
                current_youtube_settings = self.settings.get('youtube_mixer_settings') or self.settings.get('youtube_settings')
                if current_youtube_settings:
                    task_settings['youtube_mixer_settings'] = normalize_youtube_mixer_settings(current_youtube_settings)
                    task_settings['youtube_settings'] = task_settings['youtube_mixer_settings']
                
                # 1. Инъекция кастомного текста в глобальный конфиг перед запуском генератора
                if task_settings and 'custom_text_snapshot' in task_settings:
                    from core.config_manager import get_config_manager
                    cm = get_config_manager()
                    cts = task_settings['custom_text_snapshot']
                    
                    cm.set('user_settings.custom_description_text.enabled', cts.get('enabled', False))
                    cm.set('user_settings.custom_description_text.text', cts.get('text', ''))
                    cm.set('user_settings.custom_description_text.position', cts.get('position', 'end'))
                    cm.set('user_settings.custom_description_text.ai_customize', cts.get('ai_customize', False))
                    cm.set('user_settings.custom_description_text.limit_2000', cts.get('limit_2000', False))
                    cm.set('user_settings.custom_description_text.generate_insta_files', cts.get('generate_insta_files', False))
                    if 'use_separate_metadata' in cts:
                        cm.set('user_settings.separate_metadata.enabled', cts.get('use_separate_metadata', False))
                    
                    # ПРИМЕЧАНИЕ: Мы не используем cm.save() здесь намеренно. 
                    # Это временная инъекция для генератора, и мы не хотим, 
                    # чтобы старые снапшоты перезаписывали текущие пользовательские 
                    # настройки в config.json.
                else:
                    # Если снапшота нет (старая сохраненная задача из очереди), 
                    # обязательно сбрасываем состояние в памяти, чтобы избежать "утечек"
                    from core.config_manager import get_config_manager
                    cm = get_config_manager()
                    cm.set('user_settings.custom_description_text.enabled', False)
                    cm.set('user_settings.separate_metadata.enabled', False)
                
                source_data = task_settings.get('source_data', {}).copy()
                source_data['theme'] = task.theme
                # INJECTION: strict_text_theme must be injected directly into source_data
                # as generator.py reads it from source_data instead of kwargs.
                if 'strict_text_theme' in task_settings:
                    source_data['strict_text_theme'] = task_settings.get('strict_text_theme')
                
                # 📝 ИНЪЕКЦИЯ КАСТОМНОГО ТЕКСТА из снапшота в source_data
                if task_settings and 'custom_text_snapshot' in task_settings:
                    cts = task_settings['custom_text_snapshot']
                    if cts.get('enabled', False) and cts.get('text'):
                        # Создаем список custom_texts для генератора
                        # Один и тот же текст используется для всех видео
                        from dataclasses import dataclass
                        
                        @dataclass
                        class CustomText:
                            title: str
                            text: str
                            duration: int
                        
                        # Создаем список из N копий одного текста (где N = num_videos)
                        custom_text_obj = CustomText(
                            title=task.theme,
                            text=cts['text'],
                            duration=60  # Примерная длительность
                        )
                        
                        # Создаем список для всех видео
                        source_data['custom_texts'] = [custom_text_obj] * task.num_videos
                        
                        self.log_message.emit(
                            t('queue_runtime_custom_text_snapshot').format(
                                characters=len(cts['text'])
                            )
                        )
                        self.log_message.emit(
                            t('queue_runtime_custom_text_all_videos').format(
                                count=task.num_videos
                            )
                        )
                
                # 🗂️ ОТДЕЛЬНАЯ ПАПКА: Создаем папку для каждой задачи по имени темы
                import re
                from pathlib import Path
                
                logging.debug("Creating output folder for queue task %s: %s", i + 1, task.theme)
                
                # Очищаем имя темы для использования в пути
                # Убираем только недопустимые символы для Windows/Linux файловых систем
                safe_theme = re.sub(r'[<>:"/\\|?*]', '', task.theme)  # Убираем недопустимые символы
                safe_theme = safe_theme.strip()  # Убираем пробелы по краям
                
                logging.debug("Queue task %s safe theme: %s", i + 1, safe_theme)
                
                # Ограничиваем длину (Windows имеет лимит 255 символов для имени файла)
                if len(safe_theme) > 100:
                    safe_theme = safe_theme[:100].strip()
                    logging.debug("Queue task %s trimmed safe theme: %s", i + 1, safe_theme)
                
                # Если после очистки имя пустое, используем fallback
                if not safe_theme:
                    safe_theme = f"task_{i+1}"
                    logging.debug("Queue task %s uses fallback safe theme: %s", i + 1, safe_theme)
                
                # Создаем путь: output_path/theme_name/
                base_output = task_settings.get('output_path', 'generated')
                task_output_path = str(Path(base_output) / safe_theme)
                
                logging.debug("Queue task %s output: base=%s final=%s", i + 1, base_output, task_output_path)
                
                self.log_message.emit(
                    t('queue_runtime_task_folder').format(path=task_output_path)
                )
                
                def log_callback(msg):
                    self.log_message.emit(msg)
                
                def progress_callback(percent, task_index=i):
                    self.task_progress.emit(
                        task_index,
                        percent,
                        t('queue_runtime_task_progress').format(
                            index=task_index + 1,
                            percent=percent,
                        ),
                    )
                
                common_kwargs = {
                    'source_data': source_data,
                    'num_videos': task.num_videos,
                    'music_path': task_settings.get('music_path', ''),
                    'api_key': task_settings.get('api_key', ''),
                    'video_settings': task_settings.get('video_settings', {}),
                    'output_path': task_output_path,  # ← Используем отдельную папку для задачи
                    'progress_callback': progress_callback,
                    'log_callback': log_callback,
                    'subtitle_settings': task_settings.get('subtitle_settings', {}),
                    'audio_settings': task_settings.get('audio_settings', {}),
                    'use_ai_image_generation': task_settings.get(
                        'use_ai_image_generation',
                        task_settings.get('use_ai_images', True)
                    ),
                    'google_ai_api_key': task_settings.get('google_ai_api_key', ''),
                    'youtube_mixer_settings': task_settings.get('youtube_mixer_settings') or task_settings.get('youtube_settings'),
                    
                    # ПАРАМЕТРЫ ПОВЕДЕНИЯ:
                    'strict_theme_following': task_settings.get('strict_theme_following', True),
                    'enable_scene_variety': task_settings.get('enable_scene_variety', True),
                    'use_image_cache': task_settings.get('use_image_cache', False),
                    'save_to_image_cache': task_settings.get('save_to_image_cache', False),
                    
                    # ПАРАМЕТРЫ РАСШИРЕННОГО ИНТЕРФЕЙСА:
                    'overlay_settings': task_settings.get('overlay_settings', {}),
                    'use_triple_template': task_settings.get('use_triple_template', False),
                    'unlimited_images': task_settings.get('unlimited_images', False),
                    'num_unique_images': task_settings.get('num_unique_images', 5),
                    'image_model': task_settings.get('image_model', DEFAULT_IMAGE_MODEL),
                    'custom_images_folder': task_settings.get('custom_images_folder', None),
                    'use_only_custom_images': task_settings.get('use_only_custom_images', False),
                    
                    # ПАРАМЕТРЫ ПУТЕЙ И ПОВЕДЕНИЯ VEO3 / POOLS:
                    'media_path': task_settings.get('media_path', None),
                    'veo3_settings': task_settings.get('veo3_settings', None),
                    'image_pool_settings': task_settings.get('image_pool_settings', None),
                    
                    # image_callback: обновляет превью в главном окне в реальном времени
                    'image_callback': self.image_callback,
                    'final_output_settings': task_settings,
                    
                    'worker': self,  # <-- ВАЖНО: Позволяет внутри генератора читать stop_flag для отмены задач!
                }
                
                # Используем настройки ЧЕРЕЗ task_settings
                if task_settings.get('enable_parallel', True):
                    self.generator.generate_shorts_parallel(
                        **common_kwargs,
                        num_workers=task_settings.get('num_workers', 4),
                    )
                else:
                    self.generator.generate_shorts(**common_kwargs)
                
                task.status = "completed"
                completed += 1
                self.task_completed.emit(
                    i,
                    True,
                    t('queue_runtime_task_completed_signal').format(theme=task.theme),
                )
                self.log_message.emit(
                    t('queue_runtime_task_completed').format(index=i + 1)
                )
                
                # 💾 АВТОСОХРАНЕНИЕ статуса в файл после каждой задачи
                self._save_queue_to_file()
                
            except Exception as e:
                task.status = "failed"
                failed += 1
                self.task_completed.emit(
                    i,
                    False,
                    t('queue_runtime_error_prefix').format(error=str(e)[:100]),
                )
                self.log_message.emit(
                    t('queue_runtime_task_failed').format(index=i + 1, error=e)
                )
                
                # 💾 АВТОСОХРАНЕНИЕ статуса в файл после ошибки
                self._save_queue_to_file()
        
        self.queue_completed.emit(completed, failed)


class QueueTab(QWidget):
    """Вкладка очереди задач - только темы"""
    
    QUEUE_FILE = Path("queue_autosave.json")
    
    def __init__(self, generator, parent_window) -> None:
        super().__init__()
        self.generator = generator
        self.parent_window = parent_window
        self.tasks: List[QueueTask] = []
        self.worker: Optional[QueueWorker] = None
        self.init_ui()
        self._load_autosave()
    
    def _validate_parent_window(self) -> bool:
        """P4: Проверяет что parent_window валиден и имеет нужные атрибуты."""
        if self.parent_window is None:
            return False
        
        # Проверяем наличие критичных методов
        required_attrs = ['get_all_settings', 'add_log']
        for attr in required_attrs:
            if not hasattr(self.parent_window, attr):
                return False
        
        return True
    
    def init_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        
        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(8, 8, 8, 8)
        
        # === ДОБАВЛЕНИЕ ЗАДАЧИ ===
        add_group = QGroupBox(t('queue_add_card'))
        add_layout = QHBoxLayout(add_group)
        add_layout.setContentsMargins(10, 20, 10, 10)
        
        add_layout.addWidget(QLabel(t('queue_theme')))
        self.theme_input = QLineEdit()
        self.theme_input.setPlaceholderText(t('queue_theme_placeholder'))
        self.theme_input.returnPressed.connect(self.add_task)
        add_layout.addWidget(self.theme_input, 1)
        
        add_layout.addWidget(QLabel(t('queue_videos')))
        self.num_videos_spin = QSpinBox()
        self.num_videos_spin.setRange(1, 100)
        self.num_videos_spin.setValue(5)
        self.num_videos_spin.setToolTip(t('queue_videos_hint'))
        add_layout.addWidget(self.num_videos_spin)
        
        self.add_btn = QPushButton(t('queue_add'))
        self.add_btn.clicked.connect(self.add_task)
        self.add_btn.setStyleSheet(get_primary_button_style())
        add_layout.addWidget(self.add_btn)
        
        main_layout.addWidget(add_group)
        
        # === ТАБЛИЦА ОЧЕРЕДИ ===
        queue_group = QGroupBox(t('queue_card'))
        queue_layout = QVBoxLayout(queue_group)
        queue_layout.setContentsMargins(10, 20, 10, 10)
        
        self.queue_table = QTableWidget()
        self.queue_table.setColumnCount(3)
        self.queue_table.setHorizontalHeaderLabels([t('queue_theme').rstrip(':'), t('queue_videos').rstrip(':'), t('queue_status')])
        header = self.queue_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.queue_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.queue_table.verticalHeader().setVisible(False)
        queue_layout.addWidget(self.queue_table, 1)
        
        # Кнопки управления
        btn_row = QHBoxLayout()
        
        self.show_settings_btn = QPushButton(t('queue_settings'))
        self.show_settings_btn.clicked.connect(self.show_task_settings)
        self.show_settings_btn.setStyleSheet(get_ai_button_style())
        self.show_settings_btn.setToolTip(t('queue_settings_hint'))
        btn_row.addWidget(self.show_settings_btn)
        
        self.remove_btn = QPushButton(t('queue_remove'))
        self.remove_btn.clicked.connect(self.remove_selected)
        self.remove_btn.setStyleSheet(get_danger_button_style())
        btn_row.addWidget(self.remove_btn)
        
        self.clear_btn = QPushButton(t('queue_clear'))
        self.clear_btn.clicked.connect(self.clear_queue)
        btn_row.addWidget(self.clear_btn)
        
        self.move_up_btn = QPushButton("⬆")
        self.move_up_btn.clicked.connect(self.move_up)
        btn_row.addWidget(self.move_up_btn)
        
        self.move_down_btn = QPushButton("⬇")
        self.move_down_btn.clicked.connect(self.move_down)
        btn_row.addWidget(self.move_down_btn)
        
        btn_row.addStretch()
        
        self.save_btn = QPushButton(t('queue_save'))
        self.save_btn.clicked.connect(self.save_queue)
        self.save_btn.setStyleSheet(get_ghost_button_style())
        btn_row.addWidget(self.save_btn)
        
        self.load_btn = QPushButton(t('queue_load'))
        self.load_btn.clicked.connect(self.load_queue)
        self.load_btn.setStyleSheet(get_ghost_button_style())
        btn_row.addWidget(self.load_btn)
        
        queue_layout.addLayout(btn_row)
        main_layout.addWidget(queue_group, 1)
        
        # === ЗАПУСК ===
        run_group = QGroupBox(t('queue_run_card'))
        run_layout = QVBoxLayout(run_group)
        run_layout.setContentsMargins(10, 20, 10, 10)
        
        # Прогресс
        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat(t('queue_waiting'))
        progress_row.addWidget(self.progress_bar, 1)
        self.progress_label = QLabel("0/0")
        progress_row.addWidget(self.progress_label)
        run_layout.addLayout(progress_row)
        
        # Кнопки
        btn_row2 = QHBoxLayout()
        self.start_btn = QPushButton(t('queue_start'))
        self.start_btn.clicked.connect(self.start_queue)
        self.start_btn.setStyleSheet(get_primary_button_style())
        btn_row2.addWidget(self.start_btn, 2)
        
        self.stop_btn = QPushButton(t('queue_stop'))
        self.stop_btn.clicked.connect(self.stop_queue)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(get_danger_button_style())
        btn_row2.addWidget(self.stop_btn, 1)
        run_layout.addLayout(btn_row2)
        
        main_layout.addWidget(run_group)
        
        scroll.setWidget(container)
        outer_layout.addWidget(scroll)
    
    def add_task(self) -> None:
        """Добавляет задачу в очередь со снимком ТЕКУЩИХ настроек."""
        theme = self.theme_input.text().strip()
        if not theme:
            return
        
        # Захватываем снимок всех настроек (включая custom_text) в момент добавления задачи
        current_settings = self._get_settings_from_parent()
        
        task = QueueTask(
            theme=theme, 
            num_videos=self.num_videos_spin.value(),
            task_settings=current_settings
        )
        self.tasks.append(task)
        self._refresh_table()
        self._autosave()
        self.theme_input.clear()
        self.theme_input.setFocus()
        self._log(t('queue_runtime_added').format(
            theme=theme,
            count=task.num_videos,
        ))
    
    def _log(self, message: str) -> None:
        """Логирует в основной лог родительского окна."""
        if hasattr(self.parent_window, 'log_text'):
            self.parent_window.log_text.append(message)
            self.parent_window.log_text.verticalScrollBar().setValue(
                self.parent_window.log_text.verticalScrollBar().maximum()
            )
    
    def _refresh_table(self) -> None:
        """Обновляет таблицу."""
        self.queue_table.setRowCount(len(self.tasks))
        
        for i, task in enumerate(self.tasks):
            self.queue_table.setItem(i, 0, QTableWidgetItem(task.theme))
            self.queue_table.setItem(i, 1, QTableWidgetItem(str(task.num_videos)))
            
            status_item = QTableWidgetItem(t(f'queue_status_{task.status}'))
            if task.status == "completed":
                status_item.setBackground(QColor(ColorsV2.ACCENT_GREEN))
            elif task.status == "failed":
                status_item.setBackground(QColor(ColorsV2.ACCENT_RED))
            elif task.status == "running":
                status_item.setBackground(QColor(ColorsV2.ACCENT_BLUE))
            self.queue_table.setItem(i, 2, status_item)
        
        total = len(self.tasks)
        pending = sum(1 for t in self.tasks if t.status == "pending")
        self.progress_label.setText(f"{total - pending}/{total}")
    
    def _autosave(self):
        """Автосохранение очереди"""
        try:
            data = [task.to_dict() for task in self.tasks]
            _atomic_write_json(self.QUEUE_FILE, data)
        except Exception as e:
            # P1: Логируем ошибку вместо тихого игнорирования
            import logging
            logging.debug(f"Ошибка автосохранения очереди: {e}")
    
    def _load_autosave(self):
        """Загрузка автосохранённой очереди"""
        try:
            if self.QUEUE_FILE.exists():
                with open(self.QUEUE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.tasks = [QueueTask.from_dict(d) for d in data]
                # Сбрасываем running статусы на pending (если было прервано)
                for task in self.tasks:
                    if task.status == "running":
                        task.status = "pending"
                self._refresh_table()
                if self.tasks:
                    self._log(
                        t('queue_runtime_autosave_loaded').format(
                            count=len(self.tasks)
                        )
                    )
        except Exception as e:
            # P1: Логируем ошибку вместо тихого игнорирования
            import logging
            logging.debug(f"Ошибка загрузки автосохранения: {e}")
            _backup_broken_json(self.QUEUE_FILE)
    
    def show_task_settings(self) -> None:
        """Показывает настройки выбранной задачи в диалоге"""
        selected = self.queue_table.currentRow()
        if selected < 0:
            show_warning(self, t('error'), t('queue_select_task'))
            return
        
        task = self.tasks[selected]
        
        # Создаем диалог
        from PyQt5.QtWidgets import QDialog, QTextEdit, QVBoxLayout, QDialogButtonBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle(t('queue_task_settings_title').format(theme=task.theme))
        dialog.resize(700, 600)
        
        layout = QVBoxLayout(dialog)
        
        # Текстовое поле с настройками
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet("font-family: 'Consolas', 'Courier New', monospace; font-size: 11px;")
        
        # Формируем текст с настройками
        settings_text = self._format_task_settings(task)
        text_edit.setPlainText(settings_text)
        
        layout.addWidget(text_edit)
        
        # Кнопки
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        localize_dialog_button_box(buttons)
        buttons.accepted.connect(dialog.accept)
        layout.addLayout(QHBoxLayout())
        layout.addWidget(buttons)
        
        dialog.exec_()
    
    def _format_task_settings(self, task: QueueTask) -> str:
        """Форматирует настройки задачи в читаемый текст"""
        lines = []
        lines.append("="*70)
        lines.append(t('queue_settings_report_heading').format(theme=task.theme))
        lines.append("="*70)
        lines.append("")
        
        # Основная информация
        lines.append(t('queue_settings_section_basic'))
        lines.append(t('queue_settings_theme').format(value=task.theme))
        lines.append(t('queue_settings_video_count').format(value=task.num_videos))
        status_key = f'queue_status_{task.status}'
        status = t(status_key)
        lines.append(t('queue_settings_status').format(value=status))
        lines.append(t('queue_settings_snapshot_version').format(
            value=task.snapshot_version
        ))
        lines.append("")
        
        if not task.task_settings:
            lines.append(t('queue_settings_missing'))
            return "\n".join(lines)
        
        settings = task.task_settings
        
        # Кастомный текст (КРИТИЧНО!)
        lines.append(t('queue_settings_section_custom_text'))
        if 'custom_text_snapshot' in settings:
            cts = settings['custom_text_snapshot']
            lines.append(t('queue_settings_enabled').format(
                value=_runtime_bool(cts.get('enabled'))
            ))
            if cts.get('enabled'):
                text = cts.get('text', '')
                preview = text[:100] + "..." if len(text) > 100 else text
                lines.append(t('queue_settings_text').format(value=preview))
                lines.append(t('queue_settings_position').format(
                    value=_runtime_custom_text_position(cts.get('position', 'end'))
                ))
                lines.append(t('queue_settings_ai_customization').format(
                    value=_runtime_bool(cts.get('ai_customize'))
                ))
                lines.append(t('queue_settings_limit_2000').format(
                    value=_runtime_bool(cts.get('limit_2000'))
                ))
                lines.append(t('queue_settings_instagram_metadata').format(
                    value=_runtime_bool(cts.get('generate_insta_files'))
                ))
                lines.append(t('queue_settings_separate_metadata').format(
                    value=_runtime_bool(cts.get('use_separate_metadata'))
                ))
        else:
            lines.append(t('queue_settings_not_configured'))
        lines.append("")
        
        # Видео настройки
        lines.append(t('queue_settings_section_video'))
        if 'video_settings' in settings:
            vs = settings['video_settings']
            lines.append(t('queue_settings_resolution').format(
                width=vs.get('width', '?'),
                height=vs.get('height', '?'),
            ))
            lines.append(t('queue_settings_fps').format(value=vs.get('fps', '?')))
            lines.append(t('queue_settings_duration_seconds').format(
                value=vs.get('duration', '?')
            ))
            lines.append(t('queue_settings_animation').format(
                value=_runtime_bool(vs.get('enable_animation'))
            ))
            lines.append(t('queue_settings_transitions').format(
                value=_runtime_bool(vs.get('enable_transitions'))
            ))
        lines.append("")
        
        # Субтитры
        lines.append(t('queue_settings_section_subtitles'))
        if 'subtitle_settings' in settings:
            ss = settings['subtitle_settings']
            lines.append(t('queue_settings_enabled').format(
                value=_runtime_bool(ss.get('enabled'))
            ))
            if ss.get('enabled'):
                lines.append(t('queue_settings_font_size').format(
                    value=ss.get('font_size', '?')
                ))
                lines.append(t('queue_settings_position').format(
                    value=ss.get('position', '?')
                ))
                lines.append(t('queue_settings_animated').format(
                    value=_runtime_bool(ss.get('animated_subtitle'))
                ))
        lines.append("")
        
        # Аудио/TTS
        lines.append(t('queue_settings_section_audio'))
        if 'audio_settings' in settings:
            aus = settings['audio_settings']
            lines.append(t('queue_settings_enabled').format(
                value=_runtime_bool(aus.get('enabled'))
            ))
            if aus.get('enabled'):
                lines.append(t('queue_settings_provider').format(
                    value=aus.get('provider', '?')
                ))
                lines.append(t('queue_settings_voice').format(
                    value=aus.get('voice', '?')
                ))
                lines.append(t('queue_settings_speech_speed').format(
                    value=aus.get('speech_speed', '?')
                ))
        lines.append("")
        
        # AI изображения
        use_ai_images = settings.get('use_ai_image_generation', settings.get('use_ai_images', True))
        lines.append(t('queue_settings_section_images'))
        lines.append(t('queue_settings_ai_generation').format(
            value=_runtime_bool(use_ai_images)
        ))
        if use_ai_images:
            lines.append(t('queue_settings_model').format(
                value=settings.get('image_model', '?')
            ))
            lines.append(t('queue_settings_strict_theme').format(
                value=_runtime_bool(settings.get('strict_theme_following'))
            ))
            lines.append(t('queue_settings_scene_variety').format(
                value=_runtime_bool(settings.get('enable_scene_variety'))
            ))
            lines.append(t('queue_settings_unique_images').format(
                value=settings.get('num_unique_images', '?')
            ))
            lines.append(t('queue_settings_unlimited_images').format(
                value=_runtime_bool(settings.get('unlimited_images'))
            ))
        
        if settings.get('use_only_custom_images'):
            lines.append(t('queue_settings_custom_images_only'))
            lines.append(t('queue_settings_folder').format(
                value=settings.get('custom_images_folder', '?')
            ))
        lines.append("")
        
        # YouTube микс
        lines.append(t('queue_settings_section_youtube'))
        youtube_settings = settings.get('youtube_mixer_settings') or settings.get('youtube_settings')
        if youtube_settings:
            ys = normalize_youtube_mixer_settings(youtube_settings)
            lines.append(t('queue_settings_enabled').format(
                value=_runtime_bool(ys.get('enabled'))
            ))
            if ys.get('enabled'):
                lines.append(t('queue_settings_video_ratio').format(
                    value=ys.get('video_ratio', 0) * 100
                ))
                lines.append(t('queue_settings_clip_duration_seconds').format(
                    minimum=ys.get('clip_min_duration', '?'),
                    maximum=ys.get('clip_max_duration', '?'),
                ))
        lines.append("")
        
        # Параллельная генерация
        lines.append(t('queue_settings_section_parallel'))
        lines.append(t('queue_settings_enabled').format(
            value=_runtime_bool(settings.get('enable_parallel'))
        ))
        if settings.get('enable_parallel'):
            lines.append(t('queue_settings_workers').format(
                value=settings.get('num_workers', '?')
            ))
        lines.append("")
        
        # Язык и персона
        lines.append(t('queue_settings_section_language_persona'))
        if 'source_data' in settings:
            sd = settings['source_data']
            lines.append(t('queue_settings_language').format(
                value=_runtime_content_language(sd.get('language', '?'))
            ))
            lines.append(t('queue_settings_persona').format(
                value=_runtime_persona(sd.get('persona_id'))
            ))
        lines.append("")
        
        lines.append("="*70)
        
        return "\n".join(lines)
    
    def remove_selected(self) -> None:
        """Удаляет выбранные задачи из очереди."""
        rows = set(item.row() for item in self.queue_table.selectedItems())
        for row in sorted(rows, reverse=True):
            if row < len(self.tasks):
                del self.tasks[row]
        self._refresh_table()
        self._autosave()
    
    def clear_queue(self) -> None:
        """Очищает всю очередь."""
        if self.tasks:
            if ask_yes_no(self, t('queue_clear_title'), t('queue_clear_question')):
                self.tasks.clear()
                self._refresh_table()
                self._autosave()
    
    def move_up(self) -> None:
        """Перемещает выбранную задачу вверх."""
        rows = list(set(item.row() for item in self.queue_table.selectedItems()))
        if rows and rows[0] > 0:
            idx = rows[0]
            self.tasks[idx], self.tasks[idx-1] = self.tasks[idx-1], self.tasks[idx]
            self._refresh_table()
            self._autosave()
            self.queue_table.selectRow(idx - 1)
    
    def move_down(self) -> None:
        """Перемещает выбранную задачу вниз."""
        rows = list(set(item.row() for item in self.queue_table.selectedItems()))
        if rows and rows[0] < len(self.tasks) - 1:
            idx = rows[0]
            self.tasks[idx], self.tasks[idx+1] = self.tasks[idx+1], self.tasks[idx]
            self._refresh_table()
            self._autosave()
            self.queue_table.selectRow(idx + 1)
    
    def save_queue(self) -> None:
        """Сохраняет очередь в файл."""
        if not self.tasks:
            show_warning(self, t('error'), t('queue_empty'))
            return
        path, _ = QFileDialog.getSaveFileName(self, t('queue_save_title'), "", "JSON (*.json)")
        if path:
            data = [task.to_dict() for task in self.tasks]
            _atomic_write_json(Path(path), data)
            self._log(t('queue_runtime_saved').format(path=path))
    
    def load_queue(self) -> None:
        """Загружает очередь из файла."""
        path, _ = QFileDialog.getOpenFileName(self, t('queue_load_title'), "", "JSON (*.json)")
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.tasks = [QueueTask.from_dict(d) for d in data]
                for task in self.tasks:
                    task.status = "pending"
                self._refresh_table()
                self._log(
                    t('queue_runtime_loaded').format(count=len(self.tasks))
                )
            except Exception as e:
                show_critical(
                    self,
                    t('error'),
                    t('queue_runtime_load_failed').format(error=e),
                )
    
    def start_queue(self) -> None:
        """Запускает выполнение очереди."""
        # 🔄 АВТОМАТИЧЕСКАЯ ПЕРЕЗАГРУЗКА очереди из файла перед запуском
        if self.QUEUE_FILE.exists():
            try:
                with open(self.QUEUE_FILE, 'r', encoding='utf-8') as f:
                    import json
                    tasks_data = json.load(f)
                    
                # Обновляем задачи из файла
                self.tasks = [QueueTask.from_dict(task_data) for task_data in tasks_data]
                
                # Обновляем таблицу
                self._refresh_table()
                self._log(
                    t('queue_runtime_reloaded').format(count=len(self.tasks))
                )
            except Exception as e:
                self._log(
                    t('queue_runtime_reload_failed').format(error=e)
                )
        
        pending = [t for t in self.tasks if t.status == "pending"]
        if not pending:
            show_warning(self, t('error'), t('queue_no_tasks'))
            return
        
        settings = self._get_settings_from_parent()
        if not settings.get('api_key'):
            show_warning(self, t('error'), t('queue_api_missing'))
            return
        
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(t('queue_runtime_starting_progress'))
        
        # P2: Передаём копию списка для потокобезопасности
        tasks_copy = list(self.tasks)
        self.worker = QueueWorker(tasks_copy, self.generator, settings)
        self.worker.task_started.connect(self._on_task_started)
        self.worker.task_progress.connect(self._on_task_progress)
        self.worker.task_completed.connect(self._on_task_completed)
        self.worker.queue_completed.connect(self._on_queue_completed)
        self.worker.log_message.connect(self._on_log_message)
        self.worker.start()
        
        self._log(t('queue_runtime_launch').format(count=len(pending)))
    
    def stop_queue(self) -> None:
        """Останавливает выполнение очереди."""
        if self.worker:
            self.worker.stop()
            self._log(t('queue_runtime_stopping'))

    def stop_background_threads(self, wait_ms: int = 3000) -> bool:
        worker = self.worker
        if worker is None:
            return True
        if worker.isRunning():
            worker.stop()
            if not worker.wait(wait_ms):
                return False
        try:
            worker.blockSignals(True)
            worker.deleteLater()
        except RuntimeError:
            pass
        if self.worker is worker:
            self.worker = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        return True
    
    def _get_settings_from_parent(self) -> dict:
        """
        Получает ВСЕ настройки из главного окна, включая вкладку Custom Text.
        Служит для создания полного независимого снимка (Snapshot).
        """
        pw = self.parent_window
        base_settings = {}
        
        # Получаем базовые настройки через централизованный метод
        if pw is not None and hasattr(pw, 'get_all_settings'):
            try:
                base_settings = pw.get_all_settings()
            except Exception as e:
                import traceback
                logging.error(f"Ошибка get_all_settings: {e}")
                logging.error(f"Traceback: {traceback.format_exc()}")
                logging.debug("Используем fallback настройки")
        
        if not base_settings:
            # Fallback на минимальные настройки если parent_window недоступен
            base_settings = {
                'source_data': {'language': 'Russian', 'content_style': 'viral'},
                'api_key': '',
                'google_ai_api_key': '',
                'music_path': '',
                'output_path': 'generated',
                'video_settings': {
                    'shot_min_duration': 3.0,
                    'shot_max_duration': 5.0,
                    'fps': 60,
                    'duration': 60,
                    'width': 1080,
                    'height': 1920,
                },
                'subtitle_settings': {'enabled': True},
                'audio_settings': {'enabled': True, 'provider': 'edge'},
                'use_ai_images': True,
                'use_ai_image_generation': True,
                'enable_parallel': True,
                'num_workers': 4,
            }
            
        # P0: Сбор ИНЫХ скрытых настроек, которые не включены в get_all_settings()
        if pw is not None:
            try:
                # Добавляем media_path из SettingsTab
                if hasattr(pw, 'settings_tab') and hasattr(pw.settings_tab, 'media_path_input'):
                    base_settings['media_path'] = pw.settings_tab.media_path_input.text()
                
                # Добавляем overlay_settings через get_overlay_settings()
                if hasattr(pw, 'get_overlay_settings'):
                    base_settings['overlay_settings'] = pw.get_overlay_settings()
                elif hasattr(pw, 'generation_tab') and hasattr(pw.generation_tab, 'overlay_position_combo'):
                    combo = pw.generation_tab.overlay_position_combo
                    base_settings['overlay_settings'] = {
                        'position': combo.currentData() or combo.currentText()
                    }
                
                # Добавляем логику шаблонов и папок картинок, если не добавлено
                if hasattr(pw, 'generation_tab'):
                    gt = pw.generation_tab
                    base_settings['use_triple_template'] = gt.triple_template_cb.isChecked() if hasattr(gt, 'triple_template_cb') else False
                    base_settings['unlimited_images'] = gt.unlimited_images_cb.isChecked() if hasattr(gt, 'unlimited_images_cb') else False
                    base_settings['num_unique_images'] = gt.num_unique_images_spin.value() if hasattr(gt, 'num_unique_images_spin') else base_settings.get('num_unique_images', 5)
                    
                    if isinstance(base_settings.get('image_model'), int):
                        base_settings['image_model'] = image_model_from_ui_index(base_settings.get('image_model'))
                    elif hasattr(gt, 'image_model_combo') and 'image_model' not in base_settings:
                        base_settings['image_model'] = DEFAULT_IMAGE_MODEL

                        
                # VEO3 и Image pools
                if hasattr(pw, 'get_veo3_settings'):
                    base_settings['veo3_settings'] = pw.get_veo3_settings()
                if hasattr(pw, 'image_pool_settings'):
                    base_settings['image_pool_settings'] = pw.image_pool_settings
                    
            except Exception as e:
                logging.debug(f"Ошибка дополнения расширенных настроек: {e}")
                
        # P0: Сбор настроек из CustomTextTab в снапшот задачи
        if pw is not None and hasattr(pw, 'custom_text_tab'):
            try:
                ct = pw.custom_text_tab
                # Маппинг индекса позиции в ключи для генератора
                pos_map = ['start', 'between', 'split', 'end']
                pos_idx = ct.position_combo.currentIndex() if hasattr(ct, 'position_combo') else 3
                pos_val = pos_map[pos_idx] if 0 <= pos_idx < len(pos_map) else 'end'
                
                # Ищем чекбоксы безопасно, так как они могли быть добавлены недавно
                base_settings['custom_text_snapshot'] = {
                    'enabled': ct.enable_custom_text_cb.isChecked() if hasattr(ct, 'enable_custom_text_cb') else False,
                    'text': ct.custom_text_edit.toPlainText() if hasattr(ct, 'custom_text_edit') else '',
                    'position': pos_val,
                    'ai_customize': getattr(ct, 'ai_customize_cb', None).isChecked() if hasattr(ct, 'ai_customize_cb') else False,
                    'limit_2000': getattr(ct, 'limit_2000_cb', None).isChecked() if hasattr(ct, 'limit_2000_cb') else False,
                    'generate_insta_files': getattr(ct, 'insta_files_cb', None).isChecked() if hasattr(ct, 'insta_files_cb') else False,
                    'use_separate_metadata': getattr(ct, 'use_separate_metadata_cb', None).isChecked() if hasattr(ct, 'use_separate_metadata_cb') else False,
                }
            except Exception as e:
                logging.debug(f"Ошибка сбора custom text snapshot: {e}")
                
        youtube_settings = base_settings.get('youtube_mixer_settings') or base_settings.get('youtube_settings') or {}
        youtube_settings = normalize_youtube_mixer_settings(youtube_settings)
        base_settings['video_settings'] = normalize_video_settings(base_settings.get('video_settings', {}))
        base_settings['subtitle_settings'] = normalize_subtitle_settings(base_settings.get('subtitle_settings', {}))
        base_settings['audio_settings'] = normalize_audio_settings(base_settings.get('audio_settings', {}))
        base_settings['overlay_settings'] = normalize_overlay_settings(base_settings.get('overlay_settings', {}))
        base_settings['youtube_mixer_settings'] = youtube_settings
        base_settings['youtube_settings'] = youtube_settings
                
        return base_settings
    
    def _on_task_started(self, idx: int, theme: str):
        self.tasks[idx].status = "running"
        self._refresh_table()
        self._autosave()  # Сохраняем что задача началась
        self.progress_bar.setFormat(f"{theme[:30]}...")
    
    def _on_task_progress(self, idx: int, percent: int, message: str):
        # P0: Защита от деления на ноль
        if self.tasks:
            overall = int((idx * 100 + percent) / len(self.tasks))
        else:
            overall = 0
        self.progress_bar.setValue(overall)
    
    def _on_task_completed(self, idx: int, success: bool, message: str):
        self._refresh_table()
        self._autosave()  # Сохраняем прогресс после каждой задачи
    
    def _on_queue_completed(self, completed: int, failed: int):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat(f"✅ {completed} / ❌ {failed}")
        self._log(t('queue_runtime_done').format(
            completed=completed,
            failed=failed,
        ))
        
        # P4: Отключаем сигналы перед удалением QThread
        if self.worker is not None:
            try:
                self.worker.blockSignals(True)
                self.worker.task_started.disconnect()
                self.worker.task_progress.disconnect()
                self.worker.task_completed.disconnect()
                self.worker.queue_completed.disconnect()
                self.worker.log_message.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.worker.deleteLater()
            self.worker = None
        
        show_information(
            self, t('done_title'),
            t('queue_done_message').format(completed=completed, failed=failed),
        )
    
    def _on_log_message(self, message: str):
        self._log(message)
