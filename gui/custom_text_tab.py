#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Custom Text Tab - Добавление кастомного текста в описания видео
"""

import json
from pathlib import Path
import logging

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QTextEdit, QCheckBox, QComboBox, QGroupBox
)
from PyQt5.QtCore import Qt

from gui.styles_v2 import ColorsV2, get_sizes
from gui.translations import t


class CustomTextTab(QWidget):
    """Вкладка для добавления кастомного текста в описания"""
    
    PROGRESS_FILE = Path("custom_text_progress.json")
    
    def __init__(self, parent_window=None):
        super().__init__()
        self.parent_window = parent_window
        self.current_video_index = 0  # Текущий индекс видео
        self.total_videos = 0  # Общее количество видео
        self.init_ui()
        self.load_settings()
        self._load_progress()  # Загружаем прогресс при запуске
    
    @property
    def config_manager(self):
        """Получить config_manager из parent_window"""
        if self.parent_window and hasattr(self.parent_window, 'config_manager'):
            return self.parent_window.config_manager
        # Fallback: создаём свой
        from core.config_manager import ConfigManager
        return ConfigManager()
    
    def init_ui(self):
        """Инициализация интерфейса"""
        sizes = get_sizes()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(sizes.SPACING_MD, sizes.SPACING_MD,
                                   sizes.SPACING_MD, sizes.SPACING_MD)
        layout.setSpacing(sizes.SPACING_LG)
        
        # === Главный чекбокс ===
        self.enable_custom_text_cb = QCheckBox(t('custom_desc_enable'))
        self.enable_custom_text_cb.setStyleSheet(f"""
            QCheckBox {{
                font-size: 16px;
                font-weight: bold;
                color: {ColorsV2.TEXT_PRIMARY};
                padding: 8px;
            }}
        """)
        self.enable_custom_text_cb.stateChanged.connect(self._on_enabled_changed)
        layout.addWidget(self.enable_custom_text_cb)
        
        # === Настройки ===
        settings_card = QGroupBox(t('custom_desc_settings'))
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setSpacing(sizes.SPACING_SM)
        
        # Первая строка: позиция
        position_row = QHBoxLayout()
        position_row.addWidget(QLabel(t('custom_desc_position')))
        self.position_combo = QComboBox()
        self.position_combo.addItems([
            t('custom_desc_start'), t('custom_desc_between'),
            t('custom_desc_split'), t('custom_desc_end')
        ])
        self.position_combo.setCurrentIndex(3)  # По умолчанию "Конец"
        self.position_combo.currentIndexChanged.connect(self._on_position_changed)
        position_row.addWidget(self.position_combo)
        position_row.addStretch()
        settings_layout.addLayout(position_row)
        
        # Вторая строка: AI кастомизация
        self.ai_customize_cb = QCheckBox(t('custom_desc_ai'))
        self.ai_customize_cb.setStyleSheet(f"""
            QCheckBox {{
                font-size: 13px;
                color: {ColorsV2.TEXT_PRIMARY};
                padding: 4px;
            }}
        """)
        self.ai_customize_cb.setToolTip(t('custom_desc_ai_hint'))
        self.ai_customize_cb.stateChanged.connect(self._on_ai_customize_changed)
        settings_layout.addWidget(self.ai_customize_cb)
        
        # Третья строка: Лимит 2000 символов
        self.limit_2000_cb = QCheckBox(t('custom_desc_limit'))
        self.limit_2000_cb.setStyleSheet(f"""
            QCheckBox {{
                font-size: 13px;
                color: {ColorsV2.TEXT_PRIMARY};
                padding: 4px;
            }}
        """)
        self.limit_2000_cb.setToolTip(t('custom_desc_limit_hint'))
        self.limit_2000_cb.stateChanged.connect(self._on_limit_2000_changed)
        settings_layout.addWidget(self.limit_2000_cb)
        
        # Четвертая строка: Инста файлы
        self.insta_files_cb = QCheckBox(t('custom_desc_instagram'))
        self.insta_files_cb.setStyleSheet(f"""
            QCheckBox {{
                font-size: 13px;
                color: {ColorsV2.TEXT_PRIMARY};
                padding: 4px;
            }}
        """)
        self.insta_files_cb.setToolTip(t('custom_desc_instagram_hint'))
        self.insta_files_cb.stateChanged.connect(self._on_insta_files_changed)
        settings_layout.addWidget(self.insta_files_cb)
        
        layout.addWidget(settings_card)
        
        # === Текстовое поле ===
        text_card = QGroupBox()  # Убираем заголовок, добавим его отдельно
        text_layout = QVBoxLayout(text_card)
        text_layout.setSpacing(sizes.SPACING_SM)
        
        # Заголовок с счётчиком в одной строке
        header_container = QWidget()
        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(0, 0, 0, 8)
        
        header_label = QLabel(t('custom_desc_your_text'))
        header_label.setStyleSheet(f"""
            QLabel {{
                color: {ColorsV2.TEXT_PRIMARY};
                font-size: {sizes.FONT_LG}px;
                font-weight: 600;
            }}
        """)
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        
        # Счётчик символов (справа от заголовка)
        self.char_count_label = QLabel(t('custom_desc_characters').format(count=0))
        self.char_count_label.setStyleSheet(f"""
            QLabel {{
                background-color: {ColorsV2.BG_LIGHT};
                color: {ColorsV2.TEXT_SECONDARY};
                font-size: {sizes.FONT_SM}px;
                padding: 4px 10px;
                border-radius: 12px;
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
            }}
        """)
        header_layout.addWidget(self.char_count_label)
        
        text_layout.addWidget(header_container)
        
        # Подсказка
        hint_label = QLabel(t('custom_desc_hint'))
        hint_label.setStyleSheet(f"""
            QLabel {{
                color: {ColorsV2.TEXT_SECONDARY};
                font-size: 12px;
                padding: 4px;
            }}
        """)
        hint_label.setWordWrap(True)
        text_layout.addWidget(hint_label)
        
        # Текстовое поле
        self.custom_text_edit = QTextEdit()
        # 🔧 Только plain text при вставке — иначе тащится HTML/фон из браузера
        self.custom_text_edit.setAcceptRichText(False)
        self.custom_text_edit.setPlaceholderText(t('custom_desc_placeholder'))
        self.custom_text_edit.setMinimumHeight(200)
        from PyQt5.QtWidgets import QSizePolicy
        self.custom_text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.custom_text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)  # Всегда показывать scrollbar
        self.custom_text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {ColorsV2.BG_INPUT};
                border: 2px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
                font-family: 'Consolas', 'Courier New', monospace;
                color: {ColorsV2.TEXT_PRIMARY};
            }}
            QTextEdit:focus {{
                border-color: {ColorsV2.ACCENT_BLUE};
            }}
            /* Видимый scrollbar */
            QScrollBar:vertical {{
                background-color: {ColorsV2.BG_MEDIUM};
                width: 10px;
                margin: 0;
                border-left: 1px solid {ColorsV2.BORDER_DEFAULT};
            }}
            QScrollBar::handle:vertical {{
                background-color: {ColorsV2.ACCENT_BLUE};
                border-radius: 2px;
                min-height: 20px;
                margin: 1px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {ColorsV2.ACCENT_BLUE_HOVER};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)
        self.custom_text_edit.textChanged.connect(self._on_text_changed)
        text_layout.addWidget(self.custom_text_edit)
        
        layout.addWidget(text_card)
        
        # === Раздельные файлы метаданных (скрытый чекбокс для совместимости) ===
        # Этот чекбокс нужен для main_window_v2.py, но не отображается в UI
        self.use_separate_metadata_cb = QCheckBox()
        self.use_separate_metadata_cb.setVisible(False)  # Скрываем
        layout.addWidget(self.use_separate_metadata_cb)
        
        layout.addStretch()
    
    def load_settings(self):
        """Загрузить настройки из config"""
        try:
            # Enabled
            enabled = self.config_manager.get('user_settings.custom_description_text.enabled', False)
            self.enable_custom_text_cb.setChecked(enabled)
            
            # Position
            position = self.config_manager.get('user_settings.custom_description_text.position', 'end')
            position_map = {'start': 0, 'between': 1, 'split': 2, 'end': 3}
            self.position_combo.setCurrentIndex(position_map.get(position, 3))
            
            # Text
            text = self.config_manager.get('user_settings.custom_description_text.text', '')
            self.custom_text_edit.setPlainText(text)
            
            # AI customize
            ai_customize = self.config_manager.get('user_settings.custom_description_text.ai_customize', False)
            self.ai_customize_cb.setChecked(ai_customize)
            
            # Limit 2000
            limit_2000 = self.config_manager.get('user_settings.custom_description_text.limit_2000', False)
            self.limit_2000_cb.setChecked(limit_2000)
            
            # Insta files
            insta_files = self.config_manager.get('user_settings.custom_description_text.generate_insta_files', False)
            self.insta_files_cb.setChecked(insta_files)
            
            # Separate metadata (скрытый чекбокс)
            separate_enabled = self.config_manager.get('user_settings.separate_metadata.enabled', False)
            self.use_separate_metadata_cb.setChecked(separate_enabled)
            
            # Update char count
            self._update_char_count()
            
        except Exception as e:
            logging.debug(f"Ошибка загрузки настроек custom text: {e}")
    
    def _on_enabled_changed(self, state):
        """Обработка изменения чекбокса"""
        # Не сохраняем здесь - сохранение через main_window auto-save
        self._update_char_count()
    
    def _on_position_changed(self, index):
        """Обработка изменения позиции"""
        # Не сохраняем здесь - сохранение через main_window auto-save
        pass
    
    def _on_ai_customize_changed(self, state):
        """Обработка изменения чекбокса AI кастомизации"""
        # Не сохраняем здесь - сохранение через main_window auto-save
        pass
    
    def _on_limit_2000_changed(self, state):
        """Обработка изменения чекбокса лимита 2000 символов"""
        # Не сохраняем здесь - сохранение через main_window auto-save
        self._update_char_count()  # Обновляем счетчик при изменении
        
    def _on_insta_files_changed(self, state):
        """Обработка изменения чекбокса генерации инста файлов"""
        pass
    
    def _on_text_changed(self):
        """Обработка изменения текста"""
        # Не сохраняем здесь - сохранение через main_window auto-save
        self._update_char_count()
    
    def _update_char_count(self):
        """Обновить счётчик символов"""
        text = self.custom_text_edit.toPlainText()
        char_count = len(text)
        
        # Если включен лимит 2000 - показываем сколько останется на описание
        if self.limit_2000_cb.isChecked():
            remaining = 2000 - char_count
            if remaining < 0:
                self.char_count_label.setStyleSheet(f"""
                    QLabel {{
                        background-color: {ColorsV2.ACCENT_RED};
                        color: white;
                        font-size: 11px;
                        font-weight: bold;
                        padding: 4px 10px;
                        border-radius: 12px;
                    }}
                """)
                self.char_count_label.setText(t('custom_desc_overflow').format(count=char_count, over=abs(remaining)))
            elif remaining < 200:
                self.char_count_label.setStyleSheet(f"""
                    QLabel {{
                        background-color: {ColorsV2.ACCENT_ORANGE};
                        color: white;
                        font-size: 11px;
                        font-weight: bold;
                        padding: 4px 10px;
                        border-radius: 12px;
                    }}
                """)
                self.char_count_label.setText(t('custom_desc_remaining').format(count=char_count, remaining=remaining))
            else:
                sizes = get_sizes()
                self.char_count_label.setStyleSheet(f"""
                    QLabel {{
                        background-color: {ColorsV2.BG_LIGHT};
                        color: {ColorsV2.TEXT_SECONDARY};
                        font-size: {sizes.FONT_SM}px;
                        padding: 4px 10px;
                        border-radius: 12px;
                        border: 1px solid {ColorsV2.BORDER_DEFAULT};
                    }}
                """)
                self.char_count_label.setText(t('custom_desc_remaining').format(count=char_count, remaining=remaining))
        else:
            # Обычный режим без лимита
            sizes = get_sizes()
            if char_count > 2000:
                self.char_count_label.setStyleSheet(f"""
                    QLabel {{
                        background-color: {ColorsV2.ACCENT_RED};
                        color: white;
                        font-size: 11px;
                        font-weight: bold;
                        padding: 4px 10px;
                        border-radius: 12px;
                    }}
                """)
                self.char_count_label.setText(t('custom_desc_many').format(count=char_count))
            else:
                self.char_count_label.setStyleSheet(f"""
                    QLabel {{
                        background-color: {ColorsV2.BG_LIGHT};
                        color: {ColorsV2.TEXT_SECONDARY};
                        font-size: {sizes.FONT_SM}px;
                        padding: 4px 10px;
                        border-radius: 12px;
                        border: 1px solid {ColorsV2.BORDER_DEFAULT};
                    }}
                """)
                self.char_count_label.setText(t('custom_desc_characters').format(count=char_count))
    


    def _save_progress(self):
        """Сохранить прогресс генерации"""
        try:
            progress_data = {
                'current_video_index': self.current_video_index,
                'total_videos': self.total_videos,
                'timestamp': str(Path.cwd())  # Для проверки что это тот же проект
            }
            with open(self.PROGRESS_FILE, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f, ensure_ascii=False, indent=2)
            logging.debug(f"Прогресс custom text сохранен: {self.current_video_index}/{self.total_videos}")
        except Exception as e:
            logging.warning(f"Ошибка сохранения прогресса custom text: {e}")
    
    def _load_progress(self):
        """Загрузить сохраненный прогресс"""
        try:
            if self.PROGRESS_FILE.exists():
                with open(self.PROGRESS_FILE, 'r', encoding='utf-8') as f:
                    progress_data = json.load(f)
                
                self.current_video_index = progress_data.get('current_video_index', 0)
                self.total_videos = progress_data.get('total_videos', 0)
                
                if self.current_video_index > 0:
                    logging.info(f"📂 Загружен прогресс кастомного текста: {self.current_video_index}/{self.total_videos} видео")
                    return True
            else:
                logging.debug("Файл прогресса кастомного текста не найден")
        except Exception as e:
            logging.warning(f"Ошибка загрузки прогресса кастомного текста: {e}")
        
        return False
    
    def _clear_progress(self):
        """Очистить сохраненный прогресс"""
        try:
            if self.PROGRESS_FILE.exists():
                self.PROGRESS_FILE.unlink()
            self.current_video_index = 0
            self.total_videos = 0
            logging.debug("Прогресс custom text очищен")
        except Exception as e:
            logging.warning(f"Ошибка очистки прогресса custom text: {e}")
    
    def start_generation(self, total_videos):
        """Начать генерацию (вызывается перед стартом)"""
        self.total_videos = total_videos
        # НЕ сбрасываем current_video_index если есть прогресс
        if not self.has_unfinished_generation():
            self.current_video_index = 0
        self._save_progress()
    
    def on_video_completed(self):
        """Вызывается после завершения каждого видео"""
        self.current_video_index += 1
        self._save_progress()
    
    def on_generation_completed(self):
        """Вызывается после завершения всей генерации"""
        self._clear_progress()
    
    def has_unfinished_generation(self):
        """Проверить есть ли незавершенная генерация"""
        return self.current_video_index > 0 and self.current_video_index < self.total_videos
    
    def get_resume_info(self):
        """Получить информацию для возобновления"""
        return {
            'completed': self.current_video_index,
            'total': self.total_videos,
            'remaining': self.total_videos - self.current_video_index
        }
    
    def get_start_index(self):
        """Получить индекс для начала генерации (для возобновления)"""
        return self.current_video_index if self.has_unfinished_generation() else 0
