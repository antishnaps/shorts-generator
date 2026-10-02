#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Series Tab - Генерация серий связанных видео
Полнофункциональная версия с AI генерацией подтем и batch обработкой

P2-18: Использует унифицированные стили из gui.styles
"""

import json
import logging
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QTextEdit, QGroupBox,
    QSpinBox, QProgressBar, QScrollArea, QFrame,
)
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QTextCursor

from core.gemini_models import FAST_TEXT_MODEL, DEFAULT_IMAGE_MODEL, generate_content_url, image_model_from_ui_index
from core.settings_schema import (
    normalize_video_settings,
    normalize_subtitle_settings,
    normalize_audio_settings,
    normalize_overlay_settings,
    normalize_youtube_mixer_settings,
)
from gui.constants import get_internal_language
from gui.localized_dialogs import ask_yes_no, show_information, show_warning

# P2-18: Импорт унифицированных стилей
from gui.styles_v2 import (
    ColorsV2,
    get_group_box_style, get_input_style,
    get_spin_box_style, get_text_edit_style, get_log_text_style,
    get_progress_bar_style, get_success_button_style, get_danger_button_style,
    get_suggestion_button_style, get_ai_button_style
)
from gui.translations import t

# P2-22: Константы
API_TIMEOUT = 60  # Таймаут для API запросов в секундах


_CONTENT_EPISODE_LABELS = {
    "Russian": "Часть {index}",
    "English": "Part {index}",
    "Spanish": "Parte {index}",
    "French": "Partie {index}",
    "German": "Teil {index}",
    "Chinese": "第 {index} 集",
    "Japanese": "パート {index}",
    "Korean": "{index}부",
    "Portuguese": "Parte {index}",
    "Italian": "Parte {index}",
    "Hindi": "भाग {index}",
    "Arabic": "الجزء {index}",
}


def _content_episode_label(index: int, content_language: str) -> str:
    """Build a generated-content fallback without consulting the UI locale."""
    language = get_internal_language(str(content_language or "Russian"))
    template = _CONTENT_EPISODE_LABELS.get(
        language,
        _CONTENT_EPISODE_LABELS["English"],
    )
    return template.format(index=max(1, int(index)))


def _default_series_subtopics(count: int, content_language: str) -> list[dict]:
    return [
        {"title": _content_episode_label(index, content_language)}
        for index in range(1, max(0, int(count)) + 1)
    ]


class SubtopicGeneratorThread(QThread):
    """Thread for AI subtopic generation"""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    log = pyqtSignal(str)
    
    def __init__(self, theme, num_episodes, api_key, content_language):
        super().__init__()
        self.theme = theme
        self.num_episodes = num_episodes
        self.api_key = api_key
        self.content_language = content_language
        self._stop_requested = False  # P2: Флаг для отмены
    
    def stop(self):
        """P2: Метод для остановки генерации"""
        self._stop_requested = True
    
    def run(self):
        try:
            import requests
            
            # P2: Проверка флага отмены
            if self._stop_requested:
                self.log.emit(t('series_runtime_subtopics_canceled'))
                return
            
            self.log.emit(t('series_runtime_subtopics_generating').format(
                count=self.num_episodes,
                theme=self.theme,
            ))
            
            # JSON Schema для подтем
            schema = {
                "type": "object",
                "properties": {
                    "subtopics": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"}
                            },
                            "required": ["title"]
                        }
                    }
                },
                "required": ["subtopics"]
            }

            prompt = t('series_runtime_subtopic_prompt').format(
                count=self.num_episodes,
                theme=self.theme,
                content_language=self.content_language,
            )

            url = generate_content_url(FAST_TEXT_MODEL)
            headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
            
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.9,
                    "maxOutputTokens": 4096,
                    "responseMimeType": "application/json",
                    "responseSchema": schema
                }
            }
            
            resp = requests.post(url, json=payload, headers=headers, timeout=API_TIMEOUT)
            
            if resp.status_code != 200:
                # P3: Детализированное сообщение об ошибке
                error_detail = ""
                try:
                    error_detail = resp.text[:200]
                except Exception:
                    pass
                raise Exception(t('series_runtime_api_error').format(
                    status=resp.status_code,
                    detail=error_detail,
                ))
            
            data = resp.json()
            candidates = data.get('candidates', [])
            if not candidates:
                raise Exception(t('series_runtime_no_ai_response'))
            
            parts = candidates[0].get('content', {}).get('parts', [])
            response_text = "".join([p.get('text', '') for p in parts]).strip()
            
            result = json.loads(response_text)
            subtopics = result.get('subtopics', [])
            
            self.log.emit(
                t('series_runtime_subtopics_generated').format(
                    count=len(subtopics)
                )
            )
            self.finished.emit(subtopics)
            
        except Exception as e:
            self.error.emit(str(e))


class SeriesGeneratorThread(QThread):
    """Thread for generating video series"""
    progress = pyqtSignal(int, int)  # current, total
    episode_started = pyqtSignal(int, str)  # episode_num, title
    episode_finished = pyqtSignal(int, str, bool)  # episode_num, path, success
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, generator, settings, subtopics, parent_settings):
        super().__init__()
        self.generator = generator
        self.settings = settings
        self.subtopics = subtopics
        self.parent_settings = self._normalize_parent_settings(parent_settings)
        self._is_cancelled = False
    
    def _normalize_parent_settings(self, parent_settings):
        parent_settings = dict(parent_settings or {})
        youtube_settings = parent_settings.get('youtube_mixer_settings') or parent_settings.get('youtube_settings') or {}
        parent_settings['video_settings'] = normalize_video_settings(parent_settings.get('video_settings', {}))
        parent_settings['subtitle_settings'] = normalize_subtitle_settings(parent_settings.get('subtitle_settings', {}))
        parent_settings['audio_settings'] = normalize_audio_settings(parent_settings.get('audio_settings', {}))
        parent_settings['overlay_settings'] = normalize_overlay_settings(parent_settings.get('overlay_settings', {}))
        parent_settings['youtube_mixer_settings'] = normalize_youtube_mixer_settings(youtube_settings)
        parent_settings['youtube_settings'] = parent_settings['youtube_mixer_settings']
        if isinstance(parent_settings.get('image_model'), int):
            parent_settings['image_model'] = image_model_from_ui_index(parent_settings.get('image_model'))
        return parent_settings
    
    def cancel(self):
        self._is_cancelled = True
    
    def run(self):
        try:
            total = len(self.subtopics)
            successful = 0
            failed = 0
            source_settings = self.parent_settings.get('source_data') or {}
            language = (
                source_settings.get('language')
                or self.parent_settings.get('language', 'Russian')
            )
            
            self.log.emit(
                t('series_runtime_generation_started').format(total=total)
            )
            self.log.emit(t('series_runtime_episode_duration').format(
                seconds=self.settings['duration']
            ))
            self.log.emit("="*50)
            
            for i, subtopic in enumerate(self.subtopics):
                if self._is_cancelled:
                    self.log.emit(t('series_runtime_generation_canceled'))
                    break
                
                title = subtopic.get('title') or _content_episode_label(
                    i + 1,
                    language,
                )
                self.episode_started.emit(i + 1, title)
                self.log.emit(t('series_runtime_episode_heading').format(
                    current=i + 1,
                    total=total,
                    title=title,
                ))
                
                try:
                    # Формируем тему для генерации
                    main_theme = self.settings.get('main_theme', '')
                    episode_theme = f"{main_theme}: {title}"
                    
                    video_settings = dict(self.parent_settings.get('video_settings', {}))
                    video_settings['duration'] = self.settings.get('duration', video_settings.get('duration', 30))
                    
                    # Генерируем видео
                    self.generator.generate_shorts(
                        source_data={
                            'theme': episode_theme,
                            'language': language,
                            'persona_id': source_settings.get('persona_id'),
                            'strict_text_theme': source_settings.get('strict_text_theme', self.parent_settings.get('strict_text_theme', True)),
                        },
                        num_videos=1,
                        music_path=self.parent_settings.get('music_path', ''),
                        api_key=self.parent_settings.get('api_key') or self.parent_settings.get('gemini_api_key', ''),
                        video_settings=video_settings,
                        output_path=self.parent_settings.get('output_path', 'generated'),
                        progress_callback=lambda p: None,
                        log_callback=lambda msg: self.log.emit(f"   {msg}"),
                        subtitle_settings=self.parent_settings.get('subtitle_settings', {}),
                        use_ai_image_generation=self.parent_settings.get(
                            'use_ai_image_generation',
                            self.parent_settings.get('use_ai_images', True)
                        ),
                        google_ai_api_key=self.parent_settings.get('google_ai_api_key', ''),
                        media_path=self.parent_settings.get('media_path', None),
                        overlay_settings=self.parent_settings.get('overlay_settings', {}),
                        audio_settings=self.parent_settings.get('audio_settings', {}),
                        num_unique_images=self.parent_settings.get('num_unique_images', self.settings.get('images_per_episode', 5)),
                        strict_theme_following=self.parent_settings.get('strict_theme_following', True),
                        enable_scene_variety=self.parent_settings.get('enable_scene_variety', True),
                        image_model=self.parent_settings.get('image_model', DEFAULT_IMAGE_MODEL),
                        youtube_mixer_settings=self.parent_settings.get('youtube_mixer_settings'),
                        custom_images_folder=self.parent_settings.get('custom_images_folder', None),
                        use_only_custom_images=self.parent_settings.get('use_only_custom_images', False),
                        use_image_cache=self.parent_settings.get('use_image_cache', False),
                        save_to_image_cache=self.parent_settings.get('save_to_image_cache', False),
                        image_pool_settings=self.parent_settings.get('image_pool_settings', None),
                        avatar_settings=self.parent_settings.get('avatar_settings', None),
                    )
                    
                    successful += 1
                    self.episode_finished.emit(i + 1, title, True)
                    self.log.emit(
                        t('series_runtime_episode_ready').format(index=i + 1)
                    )
                    
                except Exception as e:
                    failed += 1
                    self.episode_finished.emit(i + 1, title, False)
                    self.log.emit(t('series_runtime_episode_error').format(
                        index=i + 1,
                        error=str(e)[:100],
                    ))
                
                self.progress.emit(i + 1, total)
            
            self.log.emit("\n" + "="*50)
            self.log.emit(t('series_runtime_generation_complete'))
            self.log.emit(t('series_runtime_success_count').format(
                successful=successful,
                total=total,
            ))
            if failed > 0:
                self.log.emit(
                    t('series_runtime_failed_count').format(failed=failed)
                )
            
            self.finished.emit(
                True,
                t('series_runtime_summary').format(
                    successful=successful,
                    total=total,
                ),
            )
            
        except Exception as e:
            self.finished.emit(False, str(e))


class SeriesTabQt(QWidget):
    """Tab for series video generation - Full implementation"""
    
    def __init__(self, generator, parent=None):
        super().__init__(parent)
        self.generator = generator
        self.parent_window = parent
        self.generation_thread = None
        self.subtopic_thread = None
        self.generated_subtopics = []
        self._subtopics_lock = False  # P0: Простая блокировка для race condition
        self.init_ui()
    
    def _validate_parent_window(self) -> bool:
        """P4: Проверяет что parent_window валиден и имеет нужные атрибуты."""
        if self.parent_window is None:
            return False
        
        # Проверяем наличие критичных методов/атрибутов
        required_attrs = ['get_all_settings', 'add_log']
        for attr in required_attrs:
            if not hasattr(self.parent_window, attr):
                return False
        
        return True
    
    def init_ui(self):
        """Initialize the series tab UI"""
        # Main scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Header
        header = QLabel(t('series_header'))
        header.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {ColorsV2.ACCENT_CYAN};"
        )
        layout.addWidget(header)
        
        desc = QLabel(t('series_description'))
        desc.setStyleSheet(f"color: {ColorsV2.TEXT_SECONDARY}; margin-bottom: 10px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        # === MAIN THEME GROUP ===
        theme_group = QGroupBox(t('series_theme_card'))
        theme_group.setStyleSheet(self._group_style())
        theme_layout = QVBoxLayout(theme_group)
        
        self.series_theme_input = QLineEdit()
        self.series_theme_input.setPlaceholderText(t('series_theme_placeholder'))
        self.series_theme_input.setStyleSheet(self._input_style())
        theme_layout.addWidget(self.series_theme_input)
        
        # Quick theme suggestions
        suggestions_layout = QHBoxLayout()
        suggestions_label = QLabel(t('series_ideas'))
        suggestions_label.setStyleSheet(f"color: {ColorsV2.TEXT_MUTED};")
        suggestions_layout.addWidget(suggestions_label)
        
        for suggestion in [t('series_idea_history'), t('series_idea_space'), t('series_idea_psychology'), t('series_idea_myths')]:
            btn = QPushButton(suggestion)
            btn.setStyleSheet(get_suggestion_button_style())  # P2-18: Унифицированный стиль
            btn.clicked.connect(lambda checked, t=suggestion: self.series_theme_input.setText(t))
            suggestions_layout.addWidget(btn)
        suggestions_layout.addStretch()
        theme_layout.addLayout(suggestions_layout)
        
        layout.addWidget(theme_group)
        
        # === SERIES SETTINGS GROUP ===
        settings_group = QGroupBox(t('series_settings'))
        settings_group.setStyleSheet(self._group_style())
        settings_layout = QVBoxLayout(settings_group)
        
        # Row 1: Episodes count + Duration info
        row1 = QHBoxLayout()
        
        episodes_label = QLabel(t('series_episodes'))
        self.episodes_spin = QSpinBox()
        self.episodes_spin.setRange(2, 30)
        self.episodes_spin.setValue(5)
        self.episodes_spin.setStyleSheet(self._spin_style())
        self.episodes_spin.valueChanged.connect(self._update_estimate)
        
        # 🔧 Длительность берётся из основной вкладки "Генерация"
        duration_label = QLabel(t('series_duration'))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(15, 1800)
        self.duration_spin.setValue(30)
        self.duration_spin.setSuffix(t('seconds_suffix'))
        self.duration_spin.setStyleSheet(self._spin_style())
        self.duration_spin.setEnabled(False)  # Read-only - берётся из основной вкладки
        self.duration_spin.setToolTip(t('series_from_generation'))
        
        row1.addWidget(episodes_label)
        row1.addWidget(self.episodes_spin)
        row1.addSpacing(20)
        row1.addWidget(duration_label)
        row1.addWidget(self.duration_spin)
        row1.addStretch()
        settings_layout.addLayout(row1)
        
        # Row 2: Images per episode info
        row2 = QHBoxLayout()
        images_label = QLabel(t('series_images'))
        self.images_spin = QSpinBox()
        self.images_spin.setRange(3, 100)
        self.images_spin.setValue(5)
        self.images_spin.setStyleSheet(self._spin_style())
        self.images_spin.setEnabled(False)  # Read-only - берётся из основной вкладки
        self.images_spin.setToolTip(t('series_from_generation'))
        
        row2.addWidget(images_label)
        row2.addWidget(self.images_spin)
        row2.addStretch()
        settings_layout.addLayout(row2)
        
        # Info label about settings
        info_label = QLabel(t('series_settings_note'))
        info_label.setStyleSheet(
            f"color: {ColorsV2.TEXT_MUTED}; font-size: 11px; font-style: italic;"
        )
        settings_layout.addWidget(info_label)
        
        # Estimate label
        self.estimate_label = QLabel()
        self.estimate_label.setStyleSheet(
            f"color: {ColorsV2.ACCENT_GREEN}; font-size: 12px; margin-top: 5px;"
        )
        self._update_estimate()
        settings_layout.addWidget(self.estimate_label)
        
        layout.addWidget(settings_group)
        
        # === SUBTOPICS GROUP ===
        subtopics_group = QGroupBox(t('series_subtopics'))
        subtopics_group.setStyleSheet(self._group_style())
        subtopics_layout = QVBoxLayout(subtopics_group)
        
        # AI generate button
        ai_btn_layout = QHBoxLayout()
        self.auto_subtopics_btn = QPushButton(t('series_generate_subtopics'))
        self.auto_subtopics_btn.setStyleSheet(get_ai_button_style())  # P2-18: Унифицированный стиль
        self.auto_subtopics_btn.clicked.connect(self.generate_subtopics)
        ai_btn_layout.addWidget(self.auto_subtopics_btn)
        ai_btn_layout.addStretch()
        subtopics_layout.addLayout(ai_btn_layout)
        
        # Subtopics text area
        self.subtopics_text = QTextEdit()
        self.subtopics_text.setPlaceholderText(t('series_subtopics_placeholder'))
        self.subtopics_text.setMinimumHeight(150)
        self.subtopics_text.setStyleSheet(get_text_edit_style())  # P2-18: Унифицированный стиль
        subtopics_layout.addWidget(self.subtopics_text)
        
        layout.addWidget(subtopics_group)

        # === PROGRESS GROUP ===
        progress_group = QGroupBox(t('series_progress'))
        progress_group.setStyleSheet(self._group_style())
        progress_layout = QVBoxLayout(progress_group)
        
        # Episode progress
        episode_layout = QHBoxLayout()
        self.episode_label = QLabel(t('series_episode_progress').format(current=0, total=0))
        self.episode_label.setStyleSheet("font-weight: bold;")
        episode_layout.addWidget(self.episode_label)
        episode_layout.addStretch()
        progress_layout.addLayout(episode_layout)
        
        self.series_progress = QProgressBar()
        self.series_progress.setValue(0)
        self.series_progress.setStyleSheet(get_progress_bar_style())  # P2-18: Унифицированный стиль
        progress_layout.addWidget(self.series_progress)
        
        # Log area
        self.series_log = QTextEdit()
        self.series_log.setReadOnly(True)
        self.series_log.setMinimumHeight(200)
        self.series_log.setStyleSheet(get_log_text_style())  # P2-18: Унифицированный стиль
        progress_layout.addWidget(self.series_log)
        
        layout.addWidget(progress_group)
        
        # === CONTROL BUTTONS ===
        buttons_layout = QHBoxLayout()
        
        self.start_btn = QPushButton(t('series_start'))
        self.start_btn.setStyleSheet(get_success_button_style())  # P2-18: Унифицированный стиль
        self.start_btn.clicked.connect(self.start_generation)
        
        self.cancel_btn = QPushButton(t('series_cancel'))
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setStyleSheet(get_danger_button_style())  # P2-18: Унифицированный стиль
        self.cancel_btn.clicked.connect(self.cancel_generation)
        
        buttons_layout.addWidget(self.start_btn)
        buttons_layout.addWidget(self.cancel_btn)
        buttons_layout.addStretch()
        
        layout.addLayout(buttons_layout)
        layout.addStretch()
        
        scroll.setWidget(content)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
    
    def _group_style(self):
        """P2-18: Использует унифицированный стиль"""
        return get_group_box_style()
    
    def _input_style(self):
        """P2-18: Использует унифицированный стиль"""
        return get_input_style()
    
    def _spin_style(self):
        """P2-18: Использует унифицированный стиль"""
        return get_spin_box_style()
    
    def sync_from_main_tab(self):
        """Синхронизирует настройки из основной вкладки 'Генерация'"""
        try:
            if hasattr(self, 'parent_window') and self.parent_window:
                gt = getattr(self.parent_window, 'generation_tab', None)
                if gt:
                    # Синхронизируем длительность
                    if hasattr(gt, 'get_duration_seconds'):
                        duration = gt.get_duration_seconds()
                        self.duration_spin.setValue(duration)
                    
                    # Синхронизируем количество картинок
                    if hasattr(gt, 'num_unique_images_spin'):
                        images = gt.num_unique_images_spin.value()
                        self.images_spin.setValue(images)
                    
                    self._update_estimate()
        except Exception as e:
            logging.debug(f"Ошибка синхронизации настроек серий: {e}")
    
    def _update_estimate(self):
        # Сначала синхронизируем из основной вкладки
        try:
            if hasattr(self, 'parent_window') and self.parent_window:
                gt = getattr(self.parent_window, 'generation_tab', None)
                if gt and hasattr(gt, 'get_duration_seconds'):
                    duration = gt.get_duration_seconds()
                    if duration != self.duration_spin.value():
                        self.duration_spin.blockSignals(True)
                        self.duration_spin.setValue(duration)
                        self.duration_spin.blockSignals(False)
        except (AttributeError, RuntimeError) as e:
            logging.debug(f"Failed to sync duration from generation tab: {e}")
        
        episodes = self.episodes_spin.value()
        duration = self.duration_spin.value()
        total_duration = episodes * duration
        est_time = episodes * 60  # ~60 sec per episode
        
        mins = total_duration // 60
        secs = total_duration % 60
        est_mins = est_time // 60
        
        self.estimate_label.setText(t('series_estimate').format(
            episodes=episodes, duration=duration, minutes=mins,
            seconds=secs, estimate=est_mins,
        ))
    
    def log(self, message):
        self.series_log.append(message)
        max_blocks = 3000
        doc = self.series_log.document()
        if doc.blockCount() > max_blocks:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.Start)
            for _ in range(doc.blockCount() - max_blocks):
                cursor.select(QTextCursor.BlockUnderCursor)
                cursor.removeSelectedText()
                cursor.deleteChar()
        scrollbar = self.series_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _read_config_fallback(self) -> dict:
        """P3: Общий метод для чтения config.json как fallback"""
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
                return config.get('user_settings', {})
        except Exception:
            return {}

    def _parent_api_key(self) -> str:
        if self.parent_window is None:
            return ""
        settings_tab = getattr(self.parent_window, 'settings_tab', None)
        if settings_tab is not None and hasattr(settings_tab, 'get_gemini_api_keys'):
            keys = settings_tab.get_gemini_api_keys()
            if keys:
                return keys[0]
        widget = getattr(self.parent_window, 'gemini_api_input', None)
        if widget is None:
            return ""
        if hasattr(widget, 'toPlainText'):
            return widget.toPlainText().strip()
        if hasattr(widget, 'text'):
            return widget.text().strip()
        return ""

    def get_api_key(self):
        """Get API key from parent window"""
        # P0: Используем parent_window вместо прямого чтения config.json
        if self.parent_window is not None:
            try:
                api_key = self._parent_api_key()
                if api_key:
                    return api_key
            except AttributeError:
                pass
        # Fallback на config.json
        return self._read_config_fallback().get('gemini_api_key', '')
    
    def get_parent_settings(self):
        """
        P1: Get settings from parent window.
        
        Использует централизованный метод get_all_settings() если доступен.
        """
        # P1: Используем централизованный метод если доступен
        if self.parent_window is not None and hasattr(self.parent_window, 'get_all_settings'):
            try:
                return self.parent_window.get_all_settings()
            except Exception as e:
                import logging
                logging.debug(f"Ошибка get_all_settings в series_tab: {e}")
        
        # Fallback: прямой доступ к parent_window
        if self.parent_window is not None:
            try:
                # P0: Безопасный парсинг resolution с дефолтными значениями
                width, height = 1920, 1080  # Дефолтные значения
                try:
                    res_text = self.parent_window.resolution_combo.currentText()
                    res_parts = res_text.split('x')
                    if len(res_parts) >= 2:
                        width = int(res_parts[0])
                        height = int(res_parts[1].split()[0])
                except (ValueError, IndexError, AttributeError):
                    pass  # Используем дефолтные значения
                
                # P0: Безопасный парсинг fps
                fps = 30  # Дефолтное значение
                try:
                    fps_text = self.parent_window.fps_combo.currentText().split()[0]
                    fps = int(fps_text)
                except (ValueError, IndexError, AttributeError):
                    pass  # Используем дефолтное значение
                
                return {
                    'music_path': self.parent_window.music_path_input.text(),
                    'gemini_api_key': self._parent_api_key(),
                    'google_ai_api_key': self._parent_api_key(),
                    'output_path': self.parent_window.output_path_input.text(),
                    'width': width,
                    'height': height,
                    'fps': fps,
                    'enable_animation': self.parent_window.enable_animation_cb.isChecked(),
                    'audio_settings': self.parent_window.get_audio_settings() if hasattr(self.parent_window, 'get_audio_settings') else {}
                }
            except (AttributeError, ValueError, IndexError):
                pass
        # Fallback на config.json
        return self._read_config_fallback()

    def get_content_language(self) -> str:
        """Return the Generation tab's content language, never the UI locale."""
        parent_settings = self.get_parent_settings() or {}
        source_settings = parent_settings.get('source_data') or {}
        content_language = (
            source_settings.get('language')
            or parent_settings.get('language')
        )
        if not content_language:
            generation_tab = getattr(self.parent_window, 'generation_tab', None)
            if generation_tab is not None and hasattr(generation_tab, 'get_language'):
                content_language = generation_tab.get_language()
        return get_internal_language(content_language or 'Russian')
    
    def generate_subtopics(self):
        """Generate subtopics using AI"""
        theme = self.series_theme_input.text().strip()
        if not theme:
            show_warning(self, t('error'), t('series_theme_required'))
            return
        
        api_key = self.get_api_key()
        if not api_key:
            show_warning(self, t('error'), t('series_api_missing'))
            return
        
        num_episodes = self.episodes_spin.value()
        
        self.auto_subtopics_btn.setEnabled(False)
        self.auto_subtopics_btn.setText(t('series_generating'))
        self.log(t('series_subtopic_log').format(count=num_episodes, theme=theme))
        
        self.subtopic_thread = SubtopicGeneratorThread(
            theme,
            num_episodes,
            api_key,
            self.get_content_language(),
        )
        self.subtopic_thread.finished.connect(self._on_subtopics_generated)
        self.subtopic_thread.error.connect(self._on_subtopics_error)
        self.subtopic_thread.log.connect(self.log)
        self.subtopic_thread.start()
    
    def _on_subtopics_generated(self, subtopics):
        self.auto_subtopics_btn.setEnabled(True)
        self.auto_subtopics_btn.setText(t('series_generate_subtopics'))
        
        # P0: Защита от race condition
        if self._subtopics_lock:
            return
        self._subtopics_lock = True
        
        try:
            self.generated_subtopics = subtopics
            
            # Format subtopics for display
            text_lines = []
            for i, st in enumerate(subtopics, 1):
                title = st.get('title', t('series_subtopic_default').format(index=i))
                desc = st.get('description', '')
                if desc:
                    text_lines.append(f"{i}. {title}\n   📝 {desc}")
                else:
                    text_lines.append(f"{i}. {title}")
            
            self.subtopics_text.setPlainText("\n\n".join(text_lines))
            self.log(t('series_subtopics_generated').format(count=len(subtopics)))
        finally:
            self._subtopics_lock = False
        
        # P4: Отключаем пользовательские сигналы перед удалением QThread
        if self.subtopic_thread is not None:
            try:
                self.subtopic_thread.finished.disconnect()
                self.subtopic_thread.error.disconnect()
                self.subtopic_thread.log.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.subtopic_thread.deleteLater()
            self.subtopic_thread = None
    
    def _on_subtopics_error(self, error):
        self.auto_subtopics_btn.setEnabled(True)
        self.auto_subtopics_btn.setText(t('series_generate_subtopics'))
        self.log(t('series_subtopics_failed').format(error=error))
        show_warning(
            self,
            t('error'),
            t('series_subtopics_failed').format(error=error),
        )
    
    def _parse_subtopics_from_text(self):
        """Parse subtopics from text area"""
        text = self.subtopics_text.toPlainText().strip()
        if not text:
            return []
        
        subtopics = []
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('📝'):
                continue
            
            # Remove numbering
            import re
            clean = re.sub(r'^\d+[\.\)]\s*', '', line)
            if clean:
                subtopics.append({'title': clean})
        
        return subtopics
    
    def start_generation(self):
        """Start series generation"""
        theme = self.series_theme_input.text().strip()
        if not theme:
            show_warning(self, t('error'), t('series_theme_required'))
            return
        
        # Get subtopics
        subtopics = self.generated_subtopics if self.generated_subtopics else self._parse_subtopics_from_text()
        
        if not subtopics:
            # Generate default subtopics
            num = self.episodes_spin.value()
            subtopics = _default_series_subtopics(
                num,
                self.get_content_language(),
            )
            self.log(t('series_auto_numbering'))
        
        # Confirm
        confirmed = ask_yes_no(
            self, t('series_confirm_title'),
            t('series_confirm_start').format(
                theme=theme, episodes=len(subtopics), duration=self.duration_spin.value(),
                estimate=len(subtopics),
            ),
        )
        
        if not confirmed:
            return
        
        # Prepare settings
        settings = {
            'main_theme': theme,
            'duration': self.duration_spin.value(),
            'images_per_episode': self.images_spin.value()
        }
        
        parent_settings = self.get_parent_settings()
        
        # Start generation
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.series_progress.setValue(0)
        self.episode_label.setText(t('series_episode_progress').format(current=0, total=len(subtopics)))
        
        self.generation_thread = SeriesGeneratorThread(
            self.generator, settings, subtopics, parent_settings
        )
        self.generation_thread.progress.connect(self._on_progress)
        self.generation_thread.episode_started.connect(self._on_episode_started)
        self.generation_thread.episode_finished.connect(self._on_episode_finished)
        self.generation_thread.log.connect(self.log)
        self.generation_thread.finished.connect(self._on_generation_finished)
        self.generation_thread.start()
    
    def _on_progress(self, current, total):
        # P0: Защита от деления на ноль
        percent = int((current / total) * 100) if total > 0 else 0
        self.series_progress.setValue(percent)
        self.episode_label.setText(t('series_episode_progress').format(current=current, total=total))
    
    def _on_episode_started(self, num, title):
        self.log(f"\n{'='*40}")
        self.log(t('series_episode_started').format(index=num, title=title))
        self.log(f"{'='*40}")
    
    def _on_episode_finished(self, num, title, success):
        if success:
            self.log(t('series_episode_ok').format(index=num))
        else:
            self.log(t('series_episode_failed').format(index=num))
    
    def _on_generation_finished(self, success, message):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        
        # P4: Отключаем пользовательские сигналы перед удалением QThread
        if self.generation_thread is not None:
            try:
                self.generation_thread.progress.disconnect()
                self.generation_thread.episode_started.disconnect()
                self.generation_thread.episode_finished.disconnect()
                self.generation_thread.log.disconnect()
                self.generation_thread.finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.generation_thread.deleteLater()
            self.generation_thread = None
        
        if success:
            self.log(f"\n🎉 {message}")
            show_information(
                self,
                t('series_done_title'),
                t('series_done_message').format(summary=message),
            )
        else:
            self.log(t('series_runtime_error_log').format(error=message))
            show_warning(
                self,
                t('error'),
                t('series_failed_message').format(error=message),
            )
    
    def cancel_generation(self):
        """Cancel ongoing generation"""
        if self.generation_thread:
            self.generation_thread.cancel()
            self.log(t('series_canceling'))

    def stop_background_threads(self, wait_ms: int = 3000) -> bool:
        threads = [
            ("subtopic_thread", "stop"),
            ("generation_thread", "cancel"),
        ]
        for attr_name, stop_method in threads:
            thread = getattr(self, attr_name, None)
            if thread is None:
                continue
            if thread.isRunning():
                stopper = getattr(thread, stop_method, None)
                if callable(stopper):
                    stopper()
                if not thread.wait(wait_ms):
                    return False
            try:
                thread.blockSignals(True)
                thread.deleteLater()
            except RuntimeError:
                pass
            if getattr(self, attr_name, None) is thread:
                setattr(self, attr_name, None)
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.auto_subtopics_btn.setEnabled(True)
        return True
