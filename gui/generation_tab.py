#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generation Tab - Modern tabbed interface for video generation settings

Organizes settings into logical sub-tabs:
- 📝 Тема - Theme, language, persona
- 🎬 Видео - Resolution, duration, animation
- 🎤 Аудио - TTS, music settings
- 🖼️ Визуал - Images, overlays, subtitles
"""

import re
from pathlib import Path
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QLineEdit, QPushButton, QGroupBox,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QSlider, QScrollArea, QFrame, QGridLayout,
    QPlainTextEdit
)
from PyQt5.QtCore import Qt

from gui.styles_v2 import (
    ColorsV2, get_sizes, get_sub_tab_style
)
from gui.constants import EDGE_TTS_VOICES, GEMINI_VOICES, get_internal_language
from gui.color_picker import ColorPickerLineEdit
from gui.localized_dialogs import ask_yes_no, show_information, show_warning
from gui.translations import t


class GenerationTab(QWidget):
    """
    Modern generation settings tab with sub-tabs for organization.
    """
    
    def __init__(self, parent_window=None):
        super().__init__(parent_window)
        self.parent_window = parent_window
        self.init_ui()
    
    def init_ui(self):
        """Initialize the tabbed interface"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sub-tabs for different settings categories
        self.sub_tabs = QTabWidget()
        self.sub_tabs.setStyleSheet(get_sub_tab_style())
        self.sub_tabs.setDocumentMode(True)
        
        # Create sub-tabs
        self.sub_tabs.addTab(self._create_theme_tab(), t('subtab_theme'))
        self.sub_tabs.addTab(self._create_video_tab(), t('subtab_video'))
        self.sub_tabs.addTab(self._create_audio_tab(), t('subtab_audio'))
        self.sub_tabs.addTab(self._create_visual_tab(), t('subtab_visual'))
        self.sub_tabs.addTab(self._create_avatar_tab(), t('subtab_avatar'))
        self.sub_tabs.addTab(self._create_advanced_tab(), t('subtab_advanced'))
        self.language_combo.currentIndexChanged.connect(self._refresh_tts_voices)
        self._refresh_tts_voices()
        
        layout.addWidget(self.sub_tabs)
    
    def _create_scroll_area(self, widget):
        """Wrap widget in scroll area"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(widget)
        return scroll
    
    def _create_theme_tab(self):
        """Theme and content settings"""
        sizes = get_sizes()
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(sizes.SPACING_LG)
        layout.setContentsMargins(sizes.SPACING_MD, sizes.SPACING_MD, 
                                   sizes.SPACING_MD, sizes.SPACING_MD)
        
        # === Theme Input Card ===
        theme_card = QGroupBox(t('theme_card'))
        theme_layout = QVBoxLayout(theme_card)
        theme_layout.setSpacing(sizes.SPACING_MD)
        
        # Theme input with larger font
        self.theme_input = QLineEdit()
        self.theme_input.setPlaceholderText(t('theme_placeholder'))
        self.theme_input.setMinimumHeight(48)
        self.theme_input.setStyleSheet(f"""
            QLineEdit {{
                font-size: 15px;
                padding: 12px 16px;
                background-color: {ColorsV2.BG_INPUT};
                border: 2px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 8px;
            }}
            QLineEdit:focus {{
                border-color: {ColorsV2.ACCENT_BLUE};
            }}
        """)
        theme_layout.addWidget(self.theme_input)
        layout.addWidget(theme_card)
        
        # === Custom Text Mode Card ===
        custom_text_card = QGroupBox("📝 " + t('custom_text_mode'))
        custom_text_layout = QVBoxLayout(custom_text_card)
        custom_text_layout.setSpacing(sizes.SPACING_MD)
        
        # Checkbox to enable custom text mode
        self.use_custom_texts_cb = QCheckBox(t('use_custom_texts'))
        self.use_custom_texts_cb.setStyleSheet(f"""
            QCheckBox {{
                font-size: 14px;
                font-weight: 500;
                color: {ColorsV2.TEXT_PRIMARY};
            }}
        """)
        self.use_custom_texts_cb.stateChanged.connect(self._on_custom_text_mode_changed)
        custom_text_layout.addWidget(self.use_custom_texts_cb)
        
        # Folder selection layout
        folder_layout = QHBoxLayout()
        folder_layout.setSpacing(sizes.SPACING_SM)
        
        self.custom_texts_folder_input = QLineEdit()
        self.custom_texts_folder_input.setPlaceholderText(t('select_custom_texts_folder'))
        self.custom_texts_folder_input.setReadOnly(True)
        self.custom_texts_folder_input.setEnabled(False)
        folder_layout.addWidget(self.custom_texts_folder_input, 1)
        
        self.custom_texts_folder_btn = QPushButton(t('browse'))
        self.custom_texts_folder_btn.setEnabled(False)
        self.custom_texts_folder_btn.clicked.connect(self._select_custom_texts_folder)
        self.custom_texts_folder_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ColorsV2.ACCENT_BLUE};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {ColorsV2.ACCENT_BLUE_HOVER};
            }}
            QPushButton:disabled {{
                background-color: {ColorsV2.BG_LIGHT};
                color: {ColorsV2.TEXT_MUTED};
            }}
        """)
        folder_layout.addWidget(self.custom_texts_folder_btn)
        custom_text_layout.addLayout(folder_layout)
        
        # Status label
        self.custom_texts_status_label = QLabel("")
        self.custom_texts_status_label.setStyleSheet(f"""
            QLabel {{
                color: {ColorsV2.TEXT_SECONDARY};
                font-size: 12px;
                padding: 4px 0;
            }}
        """)
        self.custom_texts_status_label.setVisible(False)
        custom_text_layout.addWidget(self.custom_texts_status_label)
        layout.addWidget(custom_text_card)
        
        # === Language & Persona Card ===
        lang_card = QGroupBox(t('language_style_card'))
        lang_layout = QGridLayout(lang_card)
        lang_layout.setSpacing(sizes.SPACING_MD)
        
        # Language - используем централизованный список
        from gui.constants import LANGUAGES_LIST
        lang_layout.addWidget(QLabel(t('content_language')), 0, 0)
        self.language_combo = QComboBox()
        self.language_combo.addItems(LANGUAGES_LIST)
        lang_layout.addWidget(self.language_combo, 0, 1)
        
        # Persona
        lang_layout.addWidget(QLabel(t('text_persona')), 1, 0)
        self.persona_combo = QComboBox()
        self.persona_combo.addItems([
            t('persona_auto'),
            t('persona_viral'),
            t('persona_ufc'),
            t('persona_conspiracy'),
            t('persona_motivator'),
            t('persona_horror'),
            t('persona_bro'),
            t('persona_serious')
        ])
        lang_layout.addWidget(self.persona_combo, 1, 1)
        layout.addWidget(lang_card)
        
        # === Viral Options Card ===
        viral_card = QGroupBox(t('viral_tech_card'))
        viral_layout = QVBoxLayout(viral_card)
        viral_layout.setSpacing(sizes.SPACING_SM)
        
        self.strict_theme_cb = QCheckBox(t('strict_theme'))
        self.strict_theme_cb.setChecked(True)
        viral_layout.addWidget(self.strict_theme_cb)
        
        self.seamless_loop_cb = QCheckBox(t('seamless_loop'))
        self.seamless_loop_cb.setChecked(True)
        viral_layout.addWidget(self.seamless_loop_cb)
        
        self.comment_bait_cb = QCheckBox(t('comment_bait'))
        self.comment_bait_cb.setChecked(False)
        viral_layout.addWidget(self.comment_bait_cb)
        layout.addWidget(viral_card)
        
        layout.addStretch()
        return self._create_scroll_area(container)

    def _apply_suggestion(self, suggestion):
        """Apply quick suggestion to theme input"""
        # Remove emoji prefix
        text = suggestion.split(' ', 1)[1] if ' ' in suggestion else suggestion
        current = self.theme_input.text()
        if current:
            self.theme_input.setText(f"{current}, {text.lower()}")
        else:
            self.theme_input.setText(text)

    def _create_video_tab(self):
        """Video settings: resolution, duration, animation"""
        sizes = get_sizes()
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(sizes.SPACING_LG)
        layout.setContentsMargins(sizes.SPACING_MD, sizes.SPACING_MD,
                                   sizes.SPACING_MD, sizes.SPACING_MD)
        
        # === Basic Settings Card ===
        basic_card = QGroupBox(t('basic_params_card'))
        basic_layout = QGridLayout(basic_card)
        basic_layout.setSpacing(sizes.SPACING_MD)
        
        # Number of videos
        basic_layout.addWidget(QLabel(t('num_videos')), 0, 0)
        self.num_videos_spin = QSpinBox()
        self.num_videos_spin.setRange(1, 100)
        self.num_videos_spin.setValue(5)
        basic_layout.addWidget(self.num_videos_spin, 0, 1)
        
        # Resolution
        basic_layout.addWidget(QLabel(t('resolution')), 1, 0)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems([
            t('resolution_fhd_vertical'), t('resolution_fhd_horizontal'),
            t('resolution_2k_vertical'), t('resolution_2k_horizontal'),
            t('resolution_hd_vertical'), t('resolution_hd_horizontal'),
            t('resolution_4k_vertical'), t('resolution_4k_horizontal'),
        ])
        basic_layout.addWidget(self.resolution_combo, 1, 1)
        
        # FPS
        basic_layout.addWidget(QLabel(t('frame_rate')), 2, 0)
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["24 fps", "30 fps", "60 fps"])
        self.fps_combo.setCurrentIndex(1)
        basic_layout.addWidget(self.fps_combo, 2, 1)
        
        layout.addWidget(basic_card)
        
        # === Duration Card ===
        duration_card = QGroupBox(t('duration_card'))
        duration_layout = QHBoxLayout(duration_card)
        duration_layout.setSpacing(sizes.SPACING_MD)
        
        duration_layout.addWidget(QLabel(t('hours')))
        self.hours_spin = QSpinBox()
        # Keep the visible control aligned with the central settings schema.
        # Longer values used to be accepted here and then silently cut to 1h.
        self.hours_spin.setRange(0, 3)
        duration_layout.addWidget(self.hours_spin)
        
        duration_layout.addWidget(QLabel(t('minutes')))
        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(0, 59)
        duration_layout.addWidget(self.minutes_spin)
        
        duration_layout.addWidget(QLabel(t('seconds')))
        self.seconds_spin = QSpinBox()
        self.seconds_spin.setRange(0, 59)
        self.seconds_spin.setValue(30)
        duration_layout.addWidget(self.seconds_spin)
        self.hours_spin.valueChanged.connect(self._on_duration_hours_changed)
        
        duration_layout.addStretch()
        layout.addWidget(duration_card)
        
        # === Animation Card ===
        anim_card = QGroupBox(t('animation_card'))
        anim_layout = QVBoxLayout(anim_card)
        anim_layout.setSpacing(sizes.SPACING_MD)
        
        # Animation toggle
        self.enable_animation_cb = QCheckBox(t('enable_animation'))
        self.enable_animation_cb.setChecked(False)
        anim_layout.addWidget(self.enable_animation_cb)
        
        # Animation settings row
        anim_settings = QHBoxLayout()
        anim_settings.addWidget(QLabel(t('animation_type')))
        self.animation_type_combo = QComboBox()
        self.animation_type_combo.addItem(t('animation_mix'), 'mix')
        self.animation_type_combo.addItem(t('animation_pan_left'), 'pan_left')
        self.animation_type_combo.addItem(t('animation_pan_right'), 'pan_right')
        self.animation_type_combo.addItem(t('animation_zoom_center'), 'zoom_center')
        self.animation_type_combo.setEnabled(False)
        anim_settings.addWidget(self.animation_type_combo)
        
        anim_settings.addWidget(QLabel(t('animation_speed')))
        self.animation_speed_spin = QSpinBox()
        self.animation_speed_spin.setRange(10, 100)
        self.animation_speed_spin.setValue(50)
        self.animation_speed_spin.setSuffix("%")
        self.animation_speed_spin.setEnabled(False)
        anim_settings.addWidget(self.animation_speed_spin)
        anim_settings.addStretch()
        anim_layout.addLayout(anim_settings)
        
        # Connect animation toggle
        self.enable_animation_cb.toggled.connect(self.animation_type_combo.setEnabled)
        self.enable_animation_cb.toggled.connect(self.animation_speed_spin.setEnabled)
        
        # Transitions
        self.enable_transitions_cb = QCheckBox(t('enable_transitions'))
        self.enable_transitions_cb.setChecked(True)
        self.enable_transitions_cb.toggled.connect(self._on_transitions_toggled)
        anim_layout.addWidget(self.enable_transitions_cb)
        
        trans_settings = QHBoxLayout()
        trans_settings.addWidget(QLabel(t('transition_duration')))
        self.transition_duration_spin = QDoubleSpinBox()
        self.transition_duration_spin.setRange(0.1, 1.0)
        self.transition_duration_spin.setValue(0.3)
        self.transition_duration_spin.setSuffix(t('seconds_suffix'))
        trans_settings.addWidget(self.transition_duration_spin)
        trans_settings.addStretch()
        anim_layout.addLayout(trans_settings)

        # ─── ⚡ GLITCH TRANSITIONS ───────────────────────────────────────────
        glitch_separator = QFrame()
        glitch_separator.setFrameShape(QFrame.HLine)
        glitch_separator.setStyleSheet(f"color: {ColorsV2.BORDER_DEFAULT};")
        anim_layout.addWidget(glitch_separator)

        # Header row
        glitch_header = QHBoxLayout()
        self.enable_glitch_cb = QCheckBox(t('glitch_enable'))
        self.enable_glitch_cb.setChecked(False)
        self.enable_glitch_cb.setToolTip(t('glitch_details_hint'))
        self.enable_glitch_cb.setStyleSheet(f"""
            QCheckBox {{
                font-size: 13px;
                font-weight: 600;
                color: {ColorsV2.ACCENT_ORANGE};
            }}
        """)
        self.enable_glitch_cb.toggled.connect(self._on_glitch_toggled)
        glitch_header.addWidget(self.enable_glitch_cb)
        glitch_header.addStretch()
        anim_layout.addLayout(glitch_header)

        # Glitch settings container (hidden by default)
        self._glitch_settings_widget = QWidget()
        glitch_settings_layout = QVBoxLayout(self._glitch_settings_widget)
        glitch_settings_layout.setContentsMargins(16, 0, 0, 0)
        glitch_settings_layout.setSpacing(6)

        # Style selector
        style_row = QHBoxLayout()
        style_row.addWidget(QLabel(t('glitch_style')))
        self.glitch_style_combo = QComboBox()
        # Заполняем из модуля glitch_transitions
        try:
            from core.glitch_transitions import get_glitch_style_labels
            labels = get_glitch_style_labels()
            localized_keys = {
                'rgb_split': 'glitch_rgb_split', 'scanlines': 'glitch_scanlines',
                'pixel_sort': 'glitch_pixel_sort', 'vhs': 'glitch_vhs',
                'digital_decay': 'glitch_digital_decay', 'chromatic': 'glitch_chromatic',
                'random': 'glitch_random',
            }
            for key, label in labels.items():
                self.glitch_style_combo.addItem(t(localized_keys.get(key, 'glitch_random')), userData=key)
        except Exception:
            self.glitch_style_combo.addItem(t('glitch_random'), userData="random")
        style_row.addWidget(self.glitch_style_combo)
        style_row.addStretch()
        glitch_settings_layout.addLayout(style_row)

        # Duration + frequency row
        params_row = QHBoxLayout()
        params_row.addWidget(QLabel(t('duration')))
        self.glitch_duration_spin = QDoubleSpinBox()
        self.glitch_duration_spin.setRange(0.1, 1.5)
        self.glitch_duration_spin.setValue(0.4)
        self.glitch_duration_spin.setSingleStep(0.05)
        self.glitch_duration_spin.setSuffix(t('seconds_suffix'))
        self.glitch_duration_spin.setToolTip(t('glitch_duration_hint'))
        params_row.addWidget(self.glitch_duration_spin)

        params_row.addWidget(QLabel(t('frequency')))
        self.glitch_frequency_spin = QSpinBox()
        self.glitch_frequency_spin.setRange(10, 100)
        self.glitch_frequency_spin.setValue(50)
        self.glitch_frequency_spin.setSuffix(" %")
        self.glitch_frequency_spin.setToolTip(t('glitch_frequency_hint'))
        params_row.addWidget(self.glitch_frequency_spin)

        params_row.addWidget(QLabel(t('intensity')))
        self.glitch_intensity_spin = QSpinBox()
        self.glitch_intensity_spin.setRange(10, 100)
        self.glitch_intensity_spin.setValue(65)
        self.glitch_intensity_spin.setSuffix(" %")
        self.glitch_intensity_spin.setToolTip(t('glitch_intensity_hint'))
        params_row.addWidget(self.glitch_intensity_spin)
        params_row.addStretch()
        glitch_settings_layout.addLayout(params_row)

        # Performance warning
        glitch_warn = QLabel(t('glitch_warning'))
        glitch_warn.setWordWrap(True)
        glitch_warn.setStyleSheet(f"""
            QLabel {{
                color: {ColorsV2.ACCENT_ORANGE};
                font-size: 11px;
                font-style: italic;
                padding: 2px 0;
            }}
        """)
        glitch_settings_layout.addWidget(glitch_warn)

        self._glitch_settings_widget.setVisible(False)
        anim_layout.addWidget(self._glitch_settings_widget)
        # ─────────────────────────────────────────────────────────────────────
        
        # Shot duration
        shot_settings = QHBoxLayout()
        shot_settings.addWidget(QLabel(t('shot_duration')))
        self.shot_min_spin = QDoubleSpinBox()
        self.shot_min_spin.setRange(0.5, 10.0)
        self.shot_min_spin.setDecimals(1)
        self.shot_min_spin.setSingleStep(0.5)
        self.shot_min_spin.setValue(1.5)
        self.shot_min_spin.setSuffix(t('seconds_suffix'))
        shot_settings.addWidget(self.shot_min_spin)
        shot_settings.addWidget(QLabel("—"))
        self.shot_max_spin = QDoubleSpinBox()
        self.shot_max_spin.setRange(0.5, 15.0)
        self.shot_max_spin.setDecimals(1)
        self.shot_max_spin.setSingleStep(0.5)
        self.shot_max_spin.setValue(4.0)
        self.shot_max_spin.setSuffix(t('seconds_suffix'))
        shot_settings.addWidget(self.shot_max_spin)
        shot_settings.addStretch()
        anim_layout.addLayout(shot_settings)
        
        # Start with images checkbox
        self.start_with_images_cb = QCheckBox(t('start_with_images'))
        self.start_with_images_cb.setChecked(False)
        self.start_with_images_cb.setToolTip(t('start_with_images_hint'))
        anim_layout.addWidget(self.start_with_images_cb)
        
        layout.addWidget(anim_card)
        layout.addWidget(self._create_visual_sources_card())
        
        layout.addStretch()
        return self._create_scroll_area(container)

    def _create_audio_tab(self):
        """Audio settings: TTS, music"""
        sizes = get_sizes()
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(sizes.SPACING_LG)
        layout.setContentsMargins(sizes.SPACING_MD, sizes.SPACING_MD,
                                   sizes.SPACING_MD, sizes.SPACING_MD)
        
        # === TTS Card ===
        tts_card = QGroupBox(t('tts_card'))
        tts_layout = QVBoxLayout(tts_card)
        tts_layout.setSpacing(sizes.SPACING_MD)
        
        self.enable_tts_cb = QCheckBox(t('enable_tts'))
        self.enable_tts_cb.setChecked(True)
        self.enable_tts_cb.toggled.connect(self._on_tts_toggled)
        tts_layout.addWidget(self.enable_tts_cb)
        
        # Provider row
        provider_row = QHBoxLayout()
        self.provider_label = QLabel(t('provider'))
        provider_row.addWidget(self.provider_label)
        self.tts_provider_combo = QComboBox()
        self.tts_provider_combo.addItems([
            t('edge_provider'),
            t('gemini_provider'),
        ])
        self.tts_provider_combo.currentIndexChanged.connect(self._on_tts_provider_changed)
        provider_row.addWidget(self.tts_provider_combo)
        provider_row.addStretch()
        tts_layout.addLayout(provider_row)
        
        # Voice row
        voice_row = QHBoxLayout()
        self.voice_label = QLabel(t('voice'))
        voice_row.addWidget(self.voice_label)
        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.addItems(GEMINI_VOICES)
        self.tts_voice_combo.setToolTip(t('gemini_voice_hint'))
        self.tts_voice_combo.setEnabled(False)
        voice_row.addWidget(self.tts_voice_combo)
        voice_row.addStretch()
        tts_layout.addLayout(voice_row)
        
        # Speed row
        speed_row = QHBoxLayout()
        self.speed_label = QLabel(t('speed'))
        speed_row.addWidget(self.speed_label)
        self.speech_speed_spin = QDoubleSpinBox()
        self.speech_speed_spin.setRange(0.5, 2.0)
        self.speech_speed_spin.setValue(1.0)
        self.speech_speed_spin.setSingleStep(0.05)
        self.speech_speed_spin.setDecimals(2)
        self.speech_speed_spin.setSuffix("x")
        speed_row.addWidget(self.speech_speed_spin)
        speed_row.addStretch()
        tts_layout.addLayout(speed_row)

        # Edge TTS supports one global prosody block: rate, pitch and volume.
        edge_tuning_row = QHBoxLayout()
        self.edge_pitch_label = QLabel(t('edge_pitch'))
        edge_tuning_row.addWidget(self.edge_pitch_label)
        self.edge_pitch_spin = QSpinBox()
        self.edge_pitch_spin.setRange(-30, 30)
        self.edge_pitch_spin.setValue(0)
        self.edge_pitch_spin.setSuffix(" Hz")
        self.edge_pitch_spin.setToolTip(t('edge_pitch_hint'))
        edge_tuning_row.addWidget(self.edge_pitch_spin)
        self.edge_volume_label = QLabel(t('edge_volume'))
        edge_tuning_row.addWidget(self.edge_volume_label)
        self.edge_volume_spin = QSpinBox()
        self.edge_volume_spin.setRange(-30, 30)
        self.edge_volume_spin.setValue(0)
        self.edge_volume_spin.setSuffix("%")
        self.edge_volume_spin.setToolTip(t('edge_volume_hint'))
        edge_tuning_row.addWidget(self.edge_volume_spin)
        edge_tuning_row.addStretch()
        tts_layout.addLayout(edge_tuning_row)

        self.auto_fit_tts_cb = QCheckBox(t('auto_fit_tts'))
        self.auto_fit_tts_cb.setChecked(True)
        self.auto_fit_tts_cb.setToolTip(t('auto_fit_tts_hint'))
        tts_layout.addWidget(self.auto_fit_tts_cb)
        self.auto_fit_tts_cb.setVisible(False)
        
        layout.addWidget(tts_card)
        
        # === Music Card ===
        music_card = QGroupBox(t('music_card'))
        music_layout = QVBoxLayout(music_card)
        music_layout.setSpacing(sizes.SPACING_MD)
        
        # Enable music checkbox
        self.enable_music_cb = QCheckBox(t('enable_music'))
        self.enable_music_cb.setChecked(True)
        self.enable_music_cb.toggled.connect(self._on_music_toggled)
        music_layout.addWidget(self.enable_music_cb)
        
        # Path row
        path_row = QHBoxLayout()
        self.music_path_input = QLineEdit()
        self.music_path_input.setPlaceholderText(t('music_folder'))
        path_row.addWidget(self.music_path_input)
        
        self.music_browse_btn = QPushButton(t('browse'))
        self.music_browse_btn.setMaximumWidth(sizes.scale(100))
        self.music_browse_btn.clicked.connect(self._browse_music)
        path_row.addWidget(self.music_browse_btn)
        music_layout.addLayout(path_row)
        
        # Volume row
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel(t('volume')))
        self.music_volume_slider = QSlider(Qt.Horizontal)
        self.music_volume_slider.setRange(0, 100)
        self.music_volume_slider.setValue(25)
        volume_row.addWidget(self.music_volume_slider)
        self.volume_label = QLabel("25%")
        self.volume_label.setMinimumWidth(sizes.scale(40))
        volume_row.addWidget(self.volume_label)
        music_layout.addLayout(volume_row)
        
        # Connect slider
        self.music_volume_slider.valueChanged.connect(
            lambda v: self.volume_label.setText(f"{v}%")
        )
        
        layout.addWidget(music_card)
        
        layout.addStretch()
        return self._create_scroll_area(container)
    
    def _create_visual_tab(self):
        """Visual settings: images, overlays, subtitles"""
        sizes = get_sizes()
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(sizes.SPACING_LG)
        layout.setContentsMargins(sizes.SPACING_MD, sizes.SPACING_MD,
                                   sizes.SPACING_MD, sizes.SPACING_MD)
        
        # === AI Images Card ===
        ai_card = QGroupBox(t('ai_images_card'))
        ai_layout = QVBoxLayout(ai_card)
        ai_layout.setSpacing(sizes.SPACING_MD)
        
        self.enable_ai_images_cb = QCheckBox(t('use_gemini'))
        self.enable_ai_images_cb.setChecked(True)
        self.enable_ai_images_cb.toggled.connect(self._on_ai_images_toggled)
        ai_layout.addWidget(self.enable_ai_images_cb)
        
        # Model row
        model_row = QHBoxLayout()
        self.image_model_label = QLabel(t('model'))
        model_row.addWidget(self.image_model_label)
        self.image_model_combo = QComboBox()
        self.image_model_combo.addItem(t('image_model_flash'), 'gemini-3.1-flash-image')
        self.image_model_combo.addItem(t('image_model_pro'), 'gemini-3-pro-image')
        self.image_model_combo.addItem(t('image_model_legacy'), 'gemini-2.5-flash-image')
        model_row.addWidget(self.image_model_combo)
        model_row.addStretch()
        ai_layout.addLayout(model_row)
        
        self.strict_images_cb = QCheckBox(t('strict_images'))
        self.strict_images_cb.setChecked(True)
        ai_layout.addWidget(self.strict_images_cb)
        
        self.scene_variety_cb = QCheckBox(t('scene_variety'))
        self.scene_variety_cb.setChecked(True)
        ai_layout.addWidget(self.scene_variety_cb)
        
        # Number of unique images
        images_row = QHBoxLayout()
        self.images_label = QLabel(t('unique_images'))
        images_row.addWidget(self.images_label)
        self.num_unique_images_spin = QSpinBox()
        self.num_unique_images_spin.setRange(3, 100)
        self.num_unique_images_spin.setValue(15)
        images_row.addWidget(self.num_unique_images_spin)
        
        self.unlimited_images_cb = QCheckBox(t('unlimited'))
        self.unlimited_images_cb.toggled.connect(lambda checked: self.num_unique_images_spin.setEnabled(not checked))
        images_row.addWidget(self.unlimited_images_cb)
        images_row.addStretch()
        ai_layout.addLayout(images_row)
        
        # Custom images folder
        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel(t('custom_images')))
        self.custom_images_input = QLineEdit()
        self.custom_images_input.setPlaceholderText(t('custom_images_placeholder'))
        custom_row.addWidget(self.custom_images_input)
        
        custom_browse_btn = QPushButton("📁")
        sizes = get_sizes()
        custom_browse_btn.setFixedSize(sizes.ICON_BUTTON_SIZE, sizes.ICON_BUTTON_SIZE)
        custom_browse_btn.setStyleSheet("font-size: 16px; padding: 0px;")
        custom_browse_btn.clicked.connect(self._browse_custom_images)
        custom_row.addWidget(custom_browse_btn)
        ai_layout.addLayout(custom_row)
        
        # Чекбокс "Использовать только свои картинки"
        self.use_only_custom_images_cb = QCheckBox(t('custom_images_only'))
        self.use_only_custom_images_cb.setChecked(False)
        self.use_only_custom_images_cb.setToolTip(t('custom_images_only_hint'))
        ai_layout.addWidget(self.use_only_custom_images_cb)

        # --- Первый шот из пула ---
        self.first_shot_image_cb = QCheckBox(t('first_shot_from_pool'))
        self.first_shot_image_cb.setChecked(True)
        self.first_shot_image_cb.setToolTip(t('first_shot_from_pool_hint'))
        ai_layout.addWidget(self.first_shot_image_cb)
        
        self.burn_first_shot_title_cb = QCheckBox(t('burn_first_shot_title'))
        self.burn_first_shot_title_cb.setChecked(True)
        self.burn_first_shot_title_cb.setToolTip(t('burn_first_shot_title_hint'))
        
        self.first_shot_image_cb.toggled.connect(self._on_first_shot_image_toggled)
        ai_layout.addWidget(self.burn_first_shot_title_cb)
        
        # Поле для ввода названия для выжигания
        title_row = QHBoxLayout()
        title_label = QLabel(t('first_shot_title'))
        self.first_shot_title_input = QPlainTextEdit()
        self.first_shot_title_input.setMaximumHeight(70)
        self.first_shot_title_input.setPlaceholderText(t('first_shot_title_placeholder'))
        self.first_shot_title_input.setToolTip(t('first_shot_title_hint'))
        title_row.addWidget(title_label)
        title_row.addWidget(self.first_shot_title_input)
        ai_layout.addLayout(title_row)
        
        self.burn_first_shot_title_cb.toggled.connect(self._update_first_shot_title_state)
        self._update_first_shot_title_state()
        # --------------------------------

        # Reference images (Logos/Products)
        self.use_reference_images_cb = QCheckBox(t('reference_images'))
        self.use_reference_images_cb.setChecked(False)
        self.use_reference_images_cb.setToolTip(t('reference_images_hint'))
        ai_layout.addWidget(self.use_reference_images_cb)

        ref_row = QHBoxLayout()
        self.reference_images_input = QLineEdit()
        self.reference_images_input.setPlaceholderText(t('reference_images_placeholder'))
        self.reference_images_input.setText("assets/references")
        self.reference_images_input.setEnabled(False)
        ref_row.addWidget(self.reference_images_input)

        self.ref_browse_btn = QPushButton("📁")
        self.ref_browse_btn.setFixedSize(sizes.ICON_BUTTON_SIZE, sizes.ICON_BUTTON_SIZE)
        self.ref_browse_btn.setStyleSheet("font-size: 16px; padding: 0px;")
        self.ref_browse_btn.clicked.connect(self._browse_reference_images)
        self.ref_browse_btn.setEnabled(False)
        self.ref_browse_btn.setToolTip(t('reference_browse_hint'))
        ref_row.addWidget(self.ref_browse_btn)
        ai_layout.addLayout(ref_row)

        self.use_reference_images_cb.toggled.connect(self.reference_images_input.setEnabled)
        self.use_reference_images_cb.toggled.connect(self.ref_browse_btn.setEnabled)
        
        layout.addWidget(ai_card)
        
        # === Image Cache Card ===
        cache_card = QGroupBox(t('image_cache_card'))
        self.image_cache_card = cache_card
        cache_layout = QVBoxLayout(cache_card)
        cache_layout.setSpacing(sizes.SPACING_MD)
        
        self.use_image_cache_cb = QCheckBox(t('use_cache'))
        self.use_image_cache_cb.setChecked(False)
        self.use_image_cache_cb.setEnabled(False)
        cache_layout.addWidget(self.use_image_cache_cb)
        
        self.save_to_cache_cb = QCheckBox(t('save_to_cache'))
        self.save_to_cache_cb.setChecked(False)
        self.save_to_cache_cb.setEnabled(False)
        cache_layout.addWidget(self.save_to_cache_cb)
        
        # Cache stats row
        stats_row = QHBoxLayout()
        self.cache_stats_label = QLabel(t('cache_stats'))
        self.cache_stats_label.setStyleSheet(f"color: {ColorsV2.TEXT_SECONDARY}; font-size: 12px;")
        stats_row.addWidget(self.cache_stats_label)
        stats_row.addStretch()
        
        refresh_cache_btn = QPushButton("🔄")
        refresh_cache_btn.setFixedSize(sizes.ICON_BUTTON_SIZE, sizes.ICON_BUTTON_SIZE)
        refresh_cache_btn.setStyleSheet("font-size: 16px; padding: 0px;")
        refresh_cache_btn.clicked.connect(self._refresh_cache_stats)
        stats_row.addWidget(refresh_cache_btn)
        
        clear_cache_btn = QPushButton("🗑️")
        clear_cache_btn.setFixedSize(sizes.ICON_BUTTON_SIZE, sizes.ICON_BUTTON_SIZE)
        clear_cache_btn.setStyleSheet("font-size: 16px; padding: 0px;")
        clear_cache_btn.clicked.connect(self._clear_image_cache)
        stats_row.addWidget(clear_cache_btn)
        
        cache_layout.addLayout(stats_row)
        
        layout.addWidget(cache_card)
        cache_card.setVisible(False)
        
        # === Image Pool Card (для серий) ===
        pool_card = QGroupBox(t('image_pool_card'))
        pool_layout = QVBoxLayout(pool_card)
        pool_layout.setSpacing(sizes.SPACING_MD)
        
        # Описание
        pool_desc = QLabel(t('image_pool_description'))
        pool_desc.setStyleSheet(f"color: {ColorsV2.TEXT_SECONDARY}; font-size: 11px;")
        pool_desc.setWordWrap(True)
        pool_layout.addWidget(pool_desc)
        
        # Чекбокс использования пула
        self.use_image_pool_cb = QCheckBox(t('use_image_pool'))
        self.use_image_pool_cb.setChecked(False)
        self.use_image_pool_cb.toggled.connect(self._on_image_pool_toggled)
        pool_layout.addWidget(self.use_image_pool_cb)
        
        # Контейнер настроек пула (скрыт по умолчанию)
        self.pool_settings_widget = QWidget()
        pool_settings_layout = QVBoxLayout(self.pool_settings_widget)
        pool_settings_layout.setContentsMargins(0, 0, 0, 0)
        pool_settings_layout.setSpacing(sizes.SPACING_SM)
        
        # Выбор существующего пула
        pool_select_row = QHBoxLayout()
        pool_select_row.addWidget(QLabel(t('select_pool')))
        self.pool_combo = QComboBox()
        self.pool_combo.setMinimumWidth(sizes.scale(200))
        pool_select_row.addWidget(self.pool_combo, 1)
        
        refresh_pools_btn = QPushButton("🔄")
        refresh_pools_btn.setFixedSize(sizes.ICON_BUTTON_SIZE, sizes.ICON_BUTTON_SIZE)
        refresh_pools_btn.setToolTip(t('refresh_pools_hint'))
        refresh_pools_btn.clicked.connect(self._refresh_pool_list)
        pool_select_row.addWidget(refresh_pools_btn)
        pool_settings_layout.addLayout(pool_select_row)
        
        # Генерация нового пула
        gen_pool_row = QHBoxLayout()
        gen_pool_row.addWidget(QLabel(t('images_in_pool')))
        self.pool_size_spin = QSpinBox()
        self.pool_size_spin.setRange(10, 200)
        self.pool_size_spin.setValue(50)
        self.pool_size_spin.setToolTip(t('pool_size_hint'))
        gen_pool_row.addWidget(self.pool_size_spin)
        gen_pool_row.addStretch()
        
        self.generate_pool_btn = QPushButton(t('generate_pool'))
        self.generate_pool_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ColorsV2.ACCENT_BLUE};
                color: white;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {ColorsV2.ACCENT_BLUE_HOVER};
            }}
        """)
        self.generate_pool_btn.clicked.connect(self._generate_image_pool)
        gen_pool_row.addWidget(self.generate_pool_btn)
        pool_settings_layout.addLayout(gen_pool_row)
        
        # Статус пула
        self.pool_status_label = QLabel("")
        self.pool_status_label.setStyleSheet(f"color: {ColorsV2.TEXT_SECONDARY}; font-size: 11px;")
        pool_settings_layout.addWidget(self.pool_status_label)
        
        pool_layout.addWidget(self.pool_settings_widget)
        self.pool_settings_widget.setVisible(False)
        
        layout.addWidget(pool_card)
        pool_card.setVisible(False)
        
        # === Subtitles Card ===
        subs_card = QGroupBox(t('subtitles_card'))
        subs_layout = QVBoxLayout(subs_card)
        subs_layout.setSpacing(sizes.SPACING_MD)
        
        self.enable_subtitles_cb = QCheckBox(t('enable_subtitles'))
        self.enable_subtitles_cb.setChecked(True)
        self.enable_subtitles_cb.toggled.connect(self._on_subtitles_toggled)
        subs_layout.addWidget(self.enable_subtitles_cb)
        
        # Font row
        font_row = QHBoxLayout()
        self.font_label = QLabel(t('font'))
        font_row.addWidget(self.font_label)
        self.font_combo = QComboBox()
        self.font_combo.addItems(["Arial Black", "Impact", "Arial", "Roboto", "Open Sans"])
        font_row.addWidget(self.font_combo)
        
        self.font_size_label = QLabel(t('font_size'))
        font_row.addWidget(self.font_size_label)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(24, 96)
        self.font_size_spin.setValue(68)
        font_row.addWidget(self.font_size_spin)
        font_row.addStretch()
        subs_layout.addLayout(font_row)
        
        # Position row
        pos_row = QHBoxLayout()
        self.subtitle_pos_label = QLabel(t('position'))
        pos_row.addWidget(self.subtitle_pos_label)
        self.subtitle_position_combo = QComboBox()
        self.subtitle_position_combo.addItems([
            t('pos_bottom'), t('pos_center'), t('pos_top')
        ])
        pos_row.addWidget(self.subtitle_position_combo)
        pos_row.addStretch()
        subs_layout.addLayout(pos_row)
        
        self.animated_subtitles_cb = QCheckBox(t('animated_subtitles'))
        self.animated_subtitles_cb.setChecked(True)
        subs_layout.addWidget(self.animated_subtitles_cb)

        style_grid = QGridLayout()
        style_grid.addWidget(QLabel(t('subtitle_style')), 0, 0)
        self.subtitle_style_combo = QComboBox()
        self.subtitle_style_combo.addItems([
            "TikTok Punch", t('subtitle_style_clean'), "Hormozi / Shorts",
            t('subtitle_style_boxed'), t('subtitle_style_cinema'), t('subtitle_style_minimal')
        ])
        style_grid.addWidget(self.subtitle_style_combo, 0, 1)

        style_grid.addWidget(QLabel(t('subtitle_text_color')), 0, 2)
        self.subtitle_color_input = ColorPickerLineEdit("#FFFFFF")
        self.subtitle_color_input.setPlaceholderText("#FFFFFF")
        style_grid.addWidget(self.subtitle_color_input, 0, 3)

        style_grid.addWidget(QLabel(t('subtitle_highlight_color')), 1, 0)
        self.subtitle_highlight_input = ColorPickerLineEdit("#00E5FF")
        self.subtitle_highlight_input.setPlaceholderText("#00E5FF")
        style_grid.addWidget(self.subtitle_highlight_input, 1, 1)

        style_grid.addWidget(QLabel(t('subtitle_words')), 1, 2)
        self.subtitle_words_spin = QSpinBox()
        self.subtitle_words_spin.setRange(1, 8)
        self.subtitle_words_spin.setValue(2)
        style_grid.addWidget(self.subtitle_words_spin, 1, 3)

        style_grid.addWidget(QLabel(t('subtitle_outline')), 2, 0)
        self.subtitle_outline_spin = QSpinBox()
        self.subtitle_outline_spin.setRange(0, 12)
        self.subtitle_outline_spin.setValue(7)
        style_grid.addWidget(self.subtitle_outline_spin, 2, 1)

        style_grid.addWidget(QLabel(t('subtitle_background')), 2, 2)
        self.subtitle_bg_opacity_spin = QSpinBox()
        self.subtitle_bg_opacity_spin.setRange(0, 100)
        self.subtitle_bg_opacity_spin.setValue(0)
        self.subtitle_bg_opacity_spin.setSuffix("%")
        style_grid.addWidget(self.subtitle_bg_opacity_spin, 2, 3)

        style_grid.addWidget(QLabel(t('subtitle_timing_offset')), 3, 0)
        self.subtitle_timing_offset_spin = QDoubleSpinBox()
        self.subtitle_timing_offset_spin.setRange(-2.0, 2.0)
        self.subtitle_timing_offset_spin.setSingleStep(0.05)
        self.subtitle_timing_offset_spin.setSuffix(t('seconds_suffix'))
        style_grid.addWidget(self.subtitle_timing_offset_spin, 3, 1)

        self.subtitle_uppercase_cb = QCheckBox(t('subtitle_uppercase'))
        self.subtitle_uppercase_cb.setChecked(True)
        style_grid.addWidget(self.subtitle_uppercase_cb, 3, 2, 1, 2)

        style_grid.addWidget(QLabel(t('subtitle_animation')), 4, 0)
        self.subtitle_animation_combo = QComboBox()
        for label_key, value in (
            ('subtitle_animation_auto', 'auto'),
            ('subtitle_animation_word_focus', 'word_focus'),
            ('subtitle_animation_fade', 'fade'),
            ('subtitle_animation_typewriter', 'typewriter'),
            ('subtitle_animation_none', 'none'),
        ):
            self.subtitle_animation_combo.addItem(t(label_key), value)
        style_grid.addWidget(self.subtitle_animation_combo, 4, 1)

        self.subtitle_fade_label = QLabel(t('subtitle_fade_duration'))
        style_grid.addWidget(self.subtitle_fade_label, 4, 2)
        self.subtitle_fade_spin = QSpinBox()
        self.subtitle_fade_spin.setRange(0, 1200)
        self.subtitle_fade_spin.setValue(220)
        self.subtitle_fade_spin.setSuffix(t('milliseconds_suffix'))
        style_grid.addWidget(self.subtitle_fade_spin, 4, 3)

        self.subtitle_typewriter_label = QLabel(t('subtitle_typewriter_speed'))
        style_grid.addWidget(self.subtitle_typewriter_label, 5, 0)
        self.subtitle_typewriter_speed_spin = QSpinBox()
        self.subtitle_typewriter_speed_spin.setRange(4, 60)
        self.subtitle_typewriter_speed_spin.setValue(18)
        self.subtitle_typewriter_speed_spin.setSuffix(t('characters_per_second_suffix'))
        style_grid.addWidget(self.subtitle_typewriter_speed_spin, 5, 1)

        subtitle_animation_hint = QLabel(t('subtitle_animation_hint'))
        subtitle_animation_hint.setWordWrap(True)
        subtitle_animation_hint.setStyleSheet(
            f"color: {ColorsV2.TEXT_SECONDARY}; font-size: 11px;"
        )
        style_grid.addWidget(subtitle_animation_hint, 5, 2, 1, 2)
        subs_layout.addLayout(style_grid)
        self.animated_subtitles_cb.toggled.connect(self._on_subtitle_animation_toggled)
        self.subtitle_animation_combo.currentIndexChanged.connect(
            self._on_subtitle_animation_changed
        )
        self._on_subtitle_animation_changed()
        
        layout.addWidget(subs_card)
        
        # === Overlay Card ===
        overlay_card = QGroupBox(t('overlay_card'))
        overlay_layout = QVBoxLayout(overlay_card)
        overlay_layout.setSpacing(sizes.SPACING_MD)
        
        # Enable overlay checkbox
        self.enable_overlay_cb = QCheckBox(t('enable_overlay'))
        self.enable_overlay_cb.setChecked(True)
        self.enable_overlay_cb.toggled.connect(self._on_overlay_toggled)
        overlay_layout.addWidget(self.enable_overlay_cb)
        
        # Path row
        overlay_path_row = QHBoxLayout()
        self.overlay_path_input = QLineEdit()
        self.overlay_path_input.setPlaceholderText(t('overlay_placeholder'))
        overlay_path_row.addWidget(self.overlay_path_input)
        
        self.overlay_browse_btn = QPushButton("📁")
        self.overlay_browse_btn.setFixedSize(sizes.ICON_BUTTON_SIZE, sizes.ICON_BUTTON_SIZE)
        self.overlay_browse_btn.setStyleSheet("font-size: 16px; padding: 0px;")
        self.overlay_browse_btn.clicked.connect(self._browse_overlay)
        overlay_path_row.addWidget(self.overlay_browse_btn)
        overlay_layout.addLayout(overlay_path_row)
        
        # Position row
        overlay_pos_row = QHBoxLayout()
        self.overlay_pos_label = QLabel(t('position'))
        overlay_pos_row.addWidget(self.overlay_pos_label)
        self.overlay_position_combo = QComboBox()
        for label_key, value in (
            ('pos_bottom_right', 'bottom_right'),
            ('pos_bottom_left', 'bottom_left'),
            ('pos_top_right', 'top_right'),
            ('pos_top_left', 'top_left'),
            ('pos_center', 'center'),
        ):
            self.overlay_position_combo.addItem(t(label_key), value)
        overlay_pos_row.addWidget(self.overlay_position_combo)
        
        # Fullscreen checkbox
        self.overlay_fullscreen_cb = QCheckBox(t('overlay_fullscreen'))
        self.overlay_fullscreen_cb.setChecked(False)
        overlay_pos_row.addWidget(self.overlay_fullscreen_cb)
        
        overlay_pos_row.addStretch()
        overlay_layout.addLayout(overlay_pos_row)

        effect_separator = QFrame()
        effect_separator.setFrameShape(QFrame.HLine)
        effect_separator.setStyleSheet(f"color: {ColorsV2.BORDER_DEFAULT};")
        overlay_layout.addWidget(effect_separator)

        effect_hint = QLabel(t('video_effect_hint'))
        effect_hint.setWordWrap(True)
        effect_hint.setStyleSheet(f"color: {ColorsV2.TEXT_SECONDARY}; font-size: 11px;")
        overlay_layout.addWidget(effect_hint)

        effect_row = QGridLayout()
        effect_row.addWidget(QLabel(t('video_effect')), 0, 0)
        self.video_effect_combo = QComboBox()
        for label_key, value in (
            ('video_effect_none', 'none'),
            ('video_effect_auto', 'auto'),
            ('video_effect_dust', 'cinematic_dust'),
            ('video_effect_grain', 'film_grain'),
            ('video_effect_old_film', 'old_film'),
            ('video_effect_vhs', 'vhs'),
            ('video_effect_bloom', 'soft_bloom'),
            ('video_effect_light_leak', 'light_leak'),
        ):
            self.video_effect_combo.addItem(t(label_key), value)
        self.video_effect_combo.setCurrentIndex(1)
        effect_row.addWidget(self.video_effect_combo, 0, 1)

        self.video_effect_intensity_label = QLabel(t('video_effect_intensity'))
        effect_row.addWidget(self.video_effect_intensity_label, 0, 2)
        self.video_effect_intensity_spin = QSpinBox()
        self.video_effect_intensity_spin.setRange(0, 100)
        self.video_effect_intensity_spin.setValue(30)
        self.video_effect_intensity_spin.setSuffix('%')
        effect_row.addWidget(self.video_effect_intensity_spin, 0, 3)

        self.video_effect_probability_label = QLabel(t('video_effect_probability'))
        effect_row.addWidget(self.video_effect_probability_label, 1, 0)
        self.video_effect_probability_spin = QSpinBox()
        self.video_effect_probability_spin.setRange(0, 100)
        self.video_effect_probability_spin.setValue(40)
        self.video_effect_probability_spin.setSuffix('%')
        effect_row.addWidget(self.video_effect_probability_spin, 1, 1)
        overlay_layout.addLayout(effect_row)
        self.video_effect_combo.currentIndexChanged.connect(self._on_video_effect_changed)
        self._on_video_effect_changed()
        
        layout.addWidget(overlay_card)
        
        layout.addStretch()
        return self._create_scroll_area(container)

    def _create_avatar_tab(self):
        """Show the current, honest status of the planned HeyGen integration."""
        sizes = get_sizes()
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(sizes.SPACING_LG)
        layout.setContentsMargins(sizes.SPACING_MD, sizes.SPACING_MD,
                                   sizes.SPACING_MD, sizes.SPACING_MD)
        
        avatar_card = QGroupBox(t('avatar_card'))
        avatar_layout = QVBoxLayout(avatar_card)
        avatar_layout.setSpacing(sizes.SPACING_MD)

        status = QLabel(t('avatar_connected'))
        status.setWordWrap(True)
        status.setStyleSheet(
            f"color: {ColorsV2.ACCENT_ORANGE}; font-size: 13px; font-weight: 600;"
        )
        avatar_layout.addWidget(status)

        hint = QLabel(t('avatar_connected_hint'))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {ColorsV2.TEXT_SECONDARY}; font-size: 12px;")
        avatar_layout.addWidget(hint)

        self.enable_avatar_cb = QCheckBox(t('enable_avatar'))
        self.enable_avatar_cb.setChecked(False)
        self.enable_avatar_cb.toggled.connect(self._on_avatar_toggled)
        avatar_layout.addWidget(self.enable_avatar_cb)

        id_row = QHBoxLayout()
        id_row.addWidget(QLabel(t('avatar_id_label')))
        self.avatar_id_input = QLineEdit()
        self.avatar_id_input.setPlaceholderText(t('avatar_id_placeholder'))
        self.avatar_id_input.setEnabled(False)
        id_row.addWidget(self.avatar_id_input)
        avatar_layout.addLayout(id_row)

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel(t('avatar_voice_id_label')))
        self.avatar_voice_id_input = QLineEdit()
        self.avatar_voice_id_input.setPlaceholderText(t('avatar_voice_id_placeholder'))
        self.avatar_voice_id_input.setEnabled(False)
        voice_row.addWidget(self.avatar_voice_id_input)
        avatar_layout.addLayout(voice_row)

        seg_label = QLabel(t('avatar_appearance'))
        seg_label.setStyleSheet("font-size: 12px; margin-top: 4px;")
        avatar_layout.addWidget(seg_label)

        seg_layout = QHBoxLayout()
        self.avatar_start_cb = QCheckBox(t('avatar_start'))
        self.avatar_start_cb.setChecked(True)
        self.avatar_start_cb.setEnabled(False)
        self.avatar_start_cb.setToolTip(t('avatar_start_hint'))
        seg_layout.addWidget(self.avatar_start_cb)

        self.avatar_end_cb = QCheckBox(t('avatar_end'))
        self.avatar_end_cb.setChecked(False)
        self.avatar_end_cb.setEnabled(False)
        self.avatar_end_cb.setToolTip(t('avatar_end_hint'))
        seg_layout.addWidget(self.avatar_end_cb)

        self.avatar_random_cb = QCheckBox(t('avatar_random'))
        self.avatar_random_cb.setChecked(False)
        self.avatar_random_cb.setEnabled(False)
        self.avatar_random_cb.setToolTip(t('avatar_random_hint'))
        seg_layout.addWidget(self.avatar_random_cb)
        seg_layout.addStretch()
        avatar_layout.addLayout(seg_layout)

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel(t('avatar_style')))
        self.avatar_style_combo = QComboBox()
        self.avatar_style_combo.addItems(
            [t('avatar_half_body'), t('avatar_close_up'), t('avatar_circle')]
        )
        self.avatar_style_combo.setEnabled(False)
        style_row.addWidget(self.avatar_style_combo)
        style_row.addStretch()
        avatar_layout.addLayout(style_row)

        bg_row = QHBoxLayout()
        bg_row.addWidget(QLabel(t('avatar_background')))
        self.avatar_bg_combo = QComboBox()
        self.avatar_bg_combo.addItems(
            [t('avatar_custom_bg'), t('avatar_transparent_bg'), t('avatar_green_bg')]
        )
        self.avatar_bg_combo.setEnabled(False)
        bg_row.addWidget(self.avatar_bg_combo)

        self.avatar_bg_path = QLineEdit()
        self.avatar_bg_path.setPlaceholderText(t('avatar_bg_path'))
        self.avatar_bg_path.setEnabled(False)
        bg_row.addWidget(self.avatar_bg_path, 1)

        # This is an icon-sized control; a localized "Browse" label is clipped
        # in every non-trivial locale (and can look like a broken button).
        self.avatar_bg_btn = QPushButton("📁")
        self.avatar_bg_btn.setFixedSize(sizes.ICON_BUTTON_SIZE, sizes.ICON_BUTTON_SIZE)
        self.avatar_bg_btn.setToolTip(t('browse'))
        self.avatar_bg_btn.setEnabled(False)
        self.avatar_bg_btn.clicked.connect(self._browse_avatar_bg)
        bg_row.addWidget(self.avatar_bg_btn)

        avatar_layout.addLayout(bg_row)

        self.avatar_bg_combo.currentIndexChanged.connect(
            lambda idx: (self.avatar_bg_path.setEnabled(idx == 0 and self.enable_avatar_cb.isChecked()), 
                         self.avatar_bg_btn.setEnabled(idx == 0 and self.enable_avatar_cb.isChecked()))
        )

        layout.addWidget(avatar_card)
        layout.addStretch()
        return self._create_scroll_area(container)

    def _on_avatar_toggled(self, enabled):
        """Handle avatar toggle - enable/disable related settings"""
        self.avatar_id_input.setEnabled(enabled)
        self.avatar_voice_id_input.setEnabled(enabled)
        self.avatar_start_cb.setEnabled(enabled)
        self.avatar_end_cb.setEnabled(enabled)
        self.avatar_random_cb.setEnabled(enabled)
        self.avatar_style_combo.setEnabled(enabled)
        self.avatar_bg_combo.setEnabled(enabled)
        is_custom_bg = self.avatar_bg_combo.currentIndex() == 0
        self.avatar_bg_path.setEnabled(enabled and is_custom_bg)
        self.avatar_bg_btn.setEnabled(enabled and is_custom_bg)

    def _browse_avatar_bg(self):
        """Browse for custom avatar background"""
        from PyQt5.QtWidgets import QFileDialog
        from pathlib import Path
        file_path, _ = QFileDialog.getOpenFileName(
            self, t('avatar_select_background'), str(Path.home()), t('image_files_filter')
        )
        if file_path:
            self.avatar_bg_path.setText(file_path)

    def _create_advanced_tab(self):
        """Advanced settings: performance, experimental"""
        sizes = get_sizes()
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(sizes.SPACING_LG)
        layout.setContentsMargins(sizes.SPACING_MD, sizes.SPACING_MD,
                                   sizes.SPACING_MD, sizes.SPACING_MD)
        
        # === Performance Card ===
        perf_card = QGroupBox(t('performance_card'))
        perf_layout = QVBoxLayout(perf_card)
        perf_layout.setSpacing(sizes.SPACING_MD)
        
        self.enable_parallel_cb = QCheckBox(t('parallel_generation'))
        self.enable_parallel_cb.setChecked(False)
        perf_layout.addWidget(self.enable_parallel_cb)
        
        workers_row = QHBoxLayout()
        workers_row.addWidget(QLabel(t('workers')))
        self.num_workers_spin = QSpinBox()
        self.num_workers_spin.setRange(1, 16)
        self.num_workers_spin.setValue(4)
        self.num_workers_spin.setEnabled(False)
        workers_row.addWidget(self.num_workers_spin)
        workers_row.addStretch()
        perf_layout.addLayout(workers_row)
        
        self.enable_parallel_cb.toggled.connect(self.num_workers_spin.setEnabled)
        
        layout.addWidget(perf_card)
        perf_card.setVisible(False)
        
        # === Output Card ===
        output_card = QGroupBox(t('output_card'))
        output_layout = QVBoxLayout(output_card)
        output_layout.setSpacing(sizes.SPACING_MD)
        
        output_row = QHBoxLayout()
        self.output_path_input = QLineEdit()
        self.output_path_input.setPlaceholderText(t('output_placeholder'))
        self.output_path_input.setText("generated")
        output_row.addWidget(self.output_path_input)
        
        output_browse_btn = QPushButton(t('browse'))
        output_browse_btn.setMaximumWidth(sizes.scale(100))
        output_browse_btn.clicked.connect(self._browse_output)
        output_row.addWidget(output_browse_btn)
        output_layout.addLayout(output_row)
        
        layout.addWidget(output_card)
        
        # === AI Content Card ===
        exp_card = QGroupBox(t('experimental_card'))
        exp_layout = QVBoxLayout(exp_card)
        exp_layout.setSpacing(sizes.SPACING_MD)
        
        self.enable_veo3_cb = QCheckBox(t('veo3_intro'))
        self.enable_veo3_cb.setChecked(False)
        self.enable_veo3_cb.stateChanged.connect(self._toggle_veo3_settings)
        exp_layout.addWidget(self.enable_veo3_cb)
        
        ai_hint = QLabel(t('veo3_hint'))
        ai_hint.setWordWrap(True)
        ai_hint.setStyleSheet(f"color: {ColorsV2.TEXT_SECONDARY}; font-size: 11px;")
        exp_layout.addWidget(ai_hint)

        # Veo 3.1 settings
        self.veo3_settings_widget = QWidget()
        veo3_layout = QGridLayout(self.veo3_settings_widget)
        veo3_layout.setContentsMargins(20, 5, 0, 5)
        veo3_layout.setSpacing(sizes.SPACING_SM)

        veo3_layout.addWidget(QLabel(t('model')), 0, 0)
        self.veo3_model_combo = QComboBox()
        self.veo3_model_combo.addItem("Veo 3.1 Quality", "veo-3.1-generate-preview")
        self.veo3_model_combo.addItem("Veo 3.1 Fast", "veo-3.1-fast-generate-preview")
        self.veo3_model_combo.addItem("Veo 3.1 Lite", "veo-3.1-lite-generate-preview")
        self.veo3_model_combo.setCurrentIndex(1)
        self.veo3_model_combo.currentIndexChanged.connect(self._update_veo_model_hint)
        veo3_layout.addWidget(self.veo3_model_combo, 0, 1)

        self.veo3_model_hint = QLabel()
        self.veo3_model_hint.setWordWrap(True)
        self.veo3_model_hint.setStyleSheet(
            f"color: {ColorsV2.TEXT_SECONDARY}; font-size: 11px;"
        )
        veo3_layout.addWidget(self.veo3_model_hint, 1, 0, 1, 2)

        veo3_layout.addWidget(QLabel(t('duration')), 2, 0)
        self.veo3_duration_combo = QComboBox()
        self.veo3_duration_combo.addItems(["8 s", "16 s", "24 s", "32 s"])
        veo3_layout.addWidget(self.veo3_duration_combo, 2, 1)

        veo3_layout.addWidget(QLabel(t('resolution')), 3, 0)
        self.veo3_resolution_combo = QComboBox()
        self.veo3_resolution_combo.addItems(["720p", "1080p"])
        self.veo3_resolution_combo.setCurrentIndex(1)
        veo3_layout.addWidget(self.veo3_resolution_combo, 3, 1)

        veo3_layout.addWidget(QLabel(t('format')), 4, 0)
        self.veo3_aspect_combo = QComboBox()
        self.veo3_aspect_combo.addItems(["9:16", "16:9"])
        veo3_layout.addWidget(self.veo3_aspect_combo, 4, 1)

        veo3_layout.addWidget(QLabel(t('veo3_style')), 5, 0)
        self.veo3_style_combo = QComboBox()
        self.veo3_style_combo.addItems([
            t('veo3_style_auto'), t('veo3_style_dramatic'), t('veo3_style_mysterious'),
            t('veo3_style_epic'), t('veo3_style_action'),
        ])
        veo3_layout.addWidget(self.veo3_style_combo, 5, 1)

        veo3_layout.addWidget(QLabel(t('veo3_content_mode')), 6, 0)
        self.veo3_content_mode_combo = QComboBox()
        self.veo3_content_mode_combo.addItem(t('veo3_mode_intro'), 'intro')
        self.veo3_content_mode_combo.addItem(t('veo3_mode_preview'), 'preview')
        self.veo3_content_mode_combo.addItem(t('veo3_mode_broll'), 'broll')
        veo3_layout.addWidget(self.veo3_content_mode_combo, 6, 1)

        veo3_layout.addWidget(QLabel(t('veo3_prompt_source')), 7, 0)
        self.veo3_prompt_source_combo = QComboBox()
        self.veo3_prompt_source_combo.addItem(t('veo3_prompt_auto'), 'auto')
        self.veo3_prompt_source_combo.addItem(t('veo3_prompt_theme'), 'theme')
        self.veo3_prompt_source_combo.addItem(t('veo3_prompt_custom'), 'custom')
        self.veo3_prompt_source_combo.currentIndexChanged.connect(self._update_veo_prompt_controls)
        veo3_layout.addWidget(self.veo3_prompt_source_combo, 7, 1)

        self.veo3_custom_prompt_edit = QPlainTextEdit()
        self.veo3_custom_prompt_edit.setPlaceholderText(t('veo3_custom_prompt_placeholder'))
        self.veo3_custom_prompt_edit.setFixedHeight(sizes.scale(72))
        self.veo3_custom_prompt_edit.setVisible(False)
        self.veo3_custom_prompt_edit.setEnabled(False)
        veo3_layout.addWidget(self.veo3_custom_prompt_edit, 8, 0, 1, 2)

        veo3_layout.addWidget(QLabel(t('veo3_motion')), 9, 0)
        self.veo3_motion_combo = QComboBox()
        self.veo3_motion_combo.addItem(t('veo3_motion_calm'), 'calm')
        self.veo3_motion_combo.addItem(t('veo3_motion_balanced'), 'balanced')
        self.veo3_motion_combo.addItem(t('veo3_motion_dynamic'), 'dynamic')
        self.veo3_motion_combo.setCurrentIndex(1)
        veo3_layout.addWidget(self.veo3_motion_combo, 9, 1)

        veo3_layout.addWidget(QLabel(t('veo3_camera')), 10, 0)
        self.veo3_camera_combo = QComboBox()
        self.veo3_camera_combo.addItem(t('veo3_camera_auto'), 'auto')
        self.veo3_camera_combo.addItem(t('veo3_camera_documentary'), 'documentary')
        self.veo3_camera_combo.addItem(t('veo3_camera_handheld'), 'handheld')
        self.veo3_camera_combo.addItem(t('veo3_camera_drone'), 'drone')
        self.veo3_camera_combo.addItem(t('veo3_camera_macro'), 'macro')
        veo3_layout.addWidget(self.veo3_camera_combo, 10, 1)

        self.veo3_no_text_cb = QCheckBox(t('veo3_no_text'))
        self.veo3_no_text_cb.setChecked(True)
        veo3_layout.addWidget(self.veo3_no_text_cb, 11, 0, 1, 2)

        self.veo3_multishot_cb = QCheckBox(t('veo3_multishot'))
        self.veo3_multishot_cb.setChecked(True)
        veo3_layout.addWidget(self.veo3_multishot_cb, 12, 0, 1, 2)

        veo3_layout.addWidget(QLabel(t('veo3_actions')), 13, 0)
        self.veo3_actions_spin = QSpinBox()
        self.veo3_actions_spin.setRange(2, 6)
        self.veo3_actions_spin.setValue(4)
        veo3_layout.addWidget(self.veo3_actions_spin, 13, 1)
        self.veo3_multishot_cb.toggled.connect(self.veo3_actions_spin.setEnabled)

        veo3_layout.addWidget(QLabel(t('veo3_placement')), 14, 0)
        self.veo3_placement_combo = QComboBox()
        self.veo3_placement_combo.addItem(t('veo3_place_replace_start'), "replace_start")
        self.veo3_placement_combo.addItem(t('veo3_place_intro'), "intro")
        self.veo3_placement_combo.addItem(t('veo3_place_middle_insert'), "middle_insert")
        self.veo3_placement_combo.addItem(t('veo3_place_chapter_broll'), "chapter_broll")
        veo3_layout.addWidget(self.veo3_placement_combo, 14, 1)

        veo3_layout.addWidget(QLabel(t('veo3_insert_count')), 15, 0)
        self.veo3_insert_count_spin = QSpinBox()
        self.veo3_insert_count_spin.setRange(1, 6)
        self.veo3_insert_count_spin.setValue(1)
        self.veo3_insert_count_spin.setToolTip(t('veo3_insert_count_hint'))
        veo3_layout.addWidget(self.veo3_insert_count_spin, 15, 1)

        veo3_layout.addWidget(QLabel(t('veo3_insert_position')), 16, 0)
        self.veo3_insert_position_spin = QSpinBox()
        self.veo3_insert_position_spin.setRange(5, 95)
        self.veo3_insert_position_spin.setValue(35)
        self.veo3_insert_position_spin.setSuffix("%")
        self.veo3_insert_position_spin.setToolTip(t('veo3_insert_position_hint'))
        veo3_layout.addWidget(self.veo3_insert_position_spin, 16, 1)

        veo3_layout.addWidget(QLabel(t('veo3_blend')), 17, 0)
        self.veo3_blend_spin = QDoubleSpinBox()
        self.veo3_blend_spin.setRange(0.0, 2.0)
        self.veo3_blend_spin.setSingleStep(0.1)
        self.veo3_blend_spin.setValue(0.4)
        self.veo3_blend_spin.setSuffix(" s")
        self.veo3_blend_spin.setToolTip(t('veo3_blend_hint'))
        veo3_layout.addWidget(self.veo3_blend_spin, 17, 1)

        veo3_layout.addWidget(QLabel(t('veo3_audio_policy')), 18, 0)
        self.veo3_audio_policy_combo = QComboBox()
        self.veo3_audio_policy_combo.addItem(t('veo3_audio_strip'), "strip")
        self.veo3_audio_policy_combo.addItem(t('veo3_audio_keep_if_no_tts'), "keep_if_no_tts")
        self.veo3_audio_policy_combo.addItem(t('veo3_audio_duck'), "duck")
        veo3_layout.addWidget(self.veo3_audio_policy_combo, 18, 1)

        veo3_layout.addWidget(QLabel(t('veo3_reference_frames')), 19, 0)
        self.veo3_reference_frames_spin = QSpinBox()
        self.veo3_reference_frames_spin.setRange(1, 3)
        self.veo3_reference_frames_spin.setValue(1)
        self.veo3_reference_frames_spin.setToolTip(t('veo3_reference_frames_hint'))
        veo3_layout.addWidget(self.veo3_reference_frames_spin, 19, 1)

        veo3_layout.addWidget(QLabel(t('veo3_realism')), 20, 0)
        self.veo3_realism_combo = QComboBox()
        self.veo3_realism_combo.addItem(t('veo3_realism_auto'), "auto")
        self.veo3_realism_combo.addItem(t('veo3_realism_photoreal'), "photoreal")
        self.veo3_realism_combo.addItem(t('veo3_realism_archival'), "archival")
        self.veo3_realism_combo.addItem(t('veo3_realism_cinematic'), "cinematic")
        veo3_layout.addWidget(self.veo3_realism_combo, 20, 1)

        veo3_layout.addWidget(QLabel(t('veo3_seed')), 21, 0)
        self.veo3_seed_spin = QSpinBox()
        self.veo3_seed_spin.setRange(0, 999999)
        self.veo3_seed_spin.setValue(0)
        self.veo3_seed_spin.setSpecialValueText(t('auto'))
        veo3_layout.addWidget(self.veo3_seed_spin, 21, 1)

        self.veo3_negative_prompt_edit = QPlainTextEdit()
        self.veo3_negative_prompt_edit.setPlaceholderText(t('veo3_negative_prompt_placeholder'))
        self.veo3_negative_prompt_edit.setFixedHeight(sizes.scale(64))
        veo3_layout.addWidget(QLabel(t('veo3_negative_prompt')), 22, 0)
        veo3_layout.addWidget(self.veo3_negative_prompt_edit, 22, 1)

        self.veo3_settings_widget.setVisible(False)
        exp_layout.addWidget(self.veo3_settings_widget)
        
        self.triple_template_cb = QCheckBox(t('half_screen'))
        self.triple_template_cb.setChecked(False)
        self.triple_template_cb.setVisible(False)
        self._update_veo_model_hint()
        
        layout.addWidget(exp_card)
        
        layout.addStretch()
        return self._create_scroll_area(container)

    def _create_visual_sources_card(self):
        """Create source selection controls for the Video tab."""
        sizes = get_sizes()
        yt_card = QGroupBox(t('youtube_mixer_card'))
        yt_layout = QVBoxLayout(yt_card)
        yt_layout.setSpacing(sizes.SPACING_MD)
        
        self.enable_youtube_mix_cb = QCheckBox(t('enable_youtube_mix'))
        self.enable_youtube_mix_cb.setChecked(False)
        yt_layout.addWidget(self.enable_youtube_mix_cb)

        source_checks = QHBoxLayout()
        self.source_youtube_cb = QCheckBox("YouTube")
        self.source_youtube_cb.setChecked(True)
        self.source_local_cb = QCheckBox(t('local_videos'))
        self.source_local_cb.setChecked(True)
        self.source_pexels_cb = QCheckBox("Pexels")
        self.source_wikimedia_cb = QCheckBox("Wikimedia Commons")
        self.source_wikimedia_cb.setToolTip(t('wikimedia_source_tooltip'))
        self.source_pixabay_cb = QCheckBox("Pixabay")
        self.source_pixabay_cb.setToolTip(t('pixabay_source_tooltip'))
        self.source_pixabay_cb.setChecked(False)
        for checkbox in (
            self.source_youtube_cb,
            self.source_local_cb,
            self.source_pexels_cb,
            self.source_pixabay_cb,
            self.source_wikimedia_cb,
        ):
            checkbox.setEnabled(False)
            source_checks.addWidget(checkbox)
        source_checks.addStretch()
        yt_layout.addLayout(source_checks)

        stock_hint = QLabel(t('pexels_source_hint'))
        stock_hint.setStyleSheet(f"color: {ColorsV2.TEXT_SECONDARY}; font-size: 11px;")
        yt_layout.addWidget(stock_hint)
        
        # Video ratio
        ratio_row = QHBoxLayout()
        ratio_row.addWidget(QLabel(t('youtube_ratio')))
        self.yt_video_ratio_spin = QSpinBox()
        self.yt_video_ratio_spin.setRange(10, 100)
        self.yt_video_ratio_spin.setValue(30)
        self.yt_video_ratio_spin.setSuffix("%")
        self.yt_video_ratio_spin.setToolTip(t('video_only_ratio_hint'))
        self.yt_video_ratio_spin.setEnabled(False)
        ratio_row.addWidget(self.yt_video_ratio_spin)
        ratio_row.addStretch()
        yt_layout.addLayout(ratio_row)
        
        # Clip duration
        clip_row = QHBoxLayout()
        clip_row.addWidget(QLabel(t('clip_duration')))
        self.yt_clip_min_spin = QSpinBox()
        self.yt_clip_min_spin.setRange(1, 15)
        self.yt_clip_min_spin.setValue(3)
        self.yt_clip_min_spin.setSuffix(t('seconds_suffix'))
        self.yt_clip_min_spin.setToolTip(t('clip_min_hint'))
        self.yt_clip_min_spin.setEnabled(False)
        clip_row.addWidget(self.yt_clip_min_spin)
        clip_row.addWidget(QLabel("—"))
        self.yt_clip_max_spin = QSpinBox()
        self.yt_clip_max_spin.setRange(2, 20)
        self.yt_clip_max_spin.setValue(8)
        self.yt_clip_max_spin.setSuffix(t('seconds_suffix'))
        self.yt_clip_max_spin.setToolTip(t('clip_max_hint'))
        self.yt_clip_max_spin.setEnabled(False)
        clip_row.addWidget(self.yt_clip_max_spin)
        clip_row.addStretch()
        yt_layout.addLayout(clip_row)
        
        # 🎬 Custom videos folder
        custom_videos_row = QHBoxLayout()
        custom_videos_row.addWidget(QLabel(t('custom_videos')))
        self.custom_videos_input = QLineEdit()
        self.custom_videos_input.setPlaceholderText(t('custom_videos_placeholder'))
        self.custom_videos_input.setToolTip(t('custom_videos_hint'))
        self.custom_videos_input.setEnabled(False)
        custom_videos_row.addWidget(self.custom_videos_input)
        
        custom_videos_browse_btn = QPushButton("📁")
        custom_videos_browse_btn.setFixedSize(sizes.ICON_BUTTON_SIZE, sizes.ICON_BUTTON_SIZE)
        custom_videos_browse_btn.setStyleSheet("font-size: 16px; padding: 0px;")
        custom_videos_browse_btn.setToolTip(t('select_video_folder'))
        custom_videos_browse_btn.clicked.connect(self._browse_custom_videos)
        custom_videos_browse_btn.setEnabled(False)
        self.custom_videos_browse_btn = custom_videos_browse_btn
        custom_videos_row.addWidget(custom_videos_browse_btn)
        yt_layout.addLayout(custom_videos_row)
        
        # Connect enable checkbox to enable/disable settings
        self.enable_youtube_mix_cb.toggled.connect(self.yt_video_ratio_spin.setEnabled)
        self.enable_youtube_mix_cb.toggled.connect(self.yt_clip_min_spin.setEnabled)
        self.enable_youtube_mix_cb.toggled.connect(self.yt_clip_max_spin.setEnabled)
        self.enable_youtube_mix_cb.toggled.connect(self.custom_videos_input.setEnabled)
        self.enable_youtube_mix_cb.toggled.connect(self.custom_videos_browse_btn.setEnabled)
        self.enable_youtube_mix_cb.toggled.connect(self.source_youtube_cb.setEnabled)
        self.enable_youtube_mix_cb.toggled.connect(self.source_local_cb.setEnabled)
        self.enable_youtube_mix_cb.toggled.connect(self.source_pexels_cb.setEnabled)
        self.enable_youtube_mix_cb.toggled.connect(self.source_pixabay_cb.setEnabled)
        self.enable_youtube_mix_cb.toggled.connect(self.source_wikimedia_cb.setEnabled)
        
        return yt_card
    
    def _toggle_veo3_settings(self, state):
        """Показать/скрыть настройки Veo 3"""
        self.veo3_settings_widget.setVisible(state == 2)  # Qt.Checked = 2

    def _update_veo_model_hint(self):
        hints = (
            t('veo_quality_hint'),
            t('veo_fast_hint'),
            t('veo_lite_hint'),
        )
        self.veo3_model_hint.setText(hints[self.veo3_model_combo.currentIndex()])

    def _update_veo_prompt_controls(self):
        custom_enabled = self.veo3_prompt_source_combo.currentData() == 'custom'
        self.veo3_custom_prompt_edit.setEnabled(custom_enabled)
        self.veo3_custom_prompt_edit.setVisible(custom_enabled)
    
    def get_avatar_settings(self) -> dict:
        """Get AI Avatar settings"""
        enabled = (
            getattr(self, 'enable_avatar_cb', None)
            and self.enable_avatar_cb.isEnabled()
            and self.enable_avatar_cb.isChecked()
        )
        if not enabled:
            return {'enabled': False}
            
        return {
            'enabled': True,
            'avatar_id': self.avatar_id_input.text().strip(),
            'voice_id': self.avatar_voice_id_input.text().strip(),
            'start_hook': self.avatar_start_cb.isChecked(),
            'end_call': self.avatar_end_cb.isChecked(),
            'full_video': False,
            'random_spots': self.avatar_random_cb.isChecked(),
            'random_count': 1,
            'style': self.avatar_style_combo.currentText(),
            'background': self.avatar_bg_combo.currentText(),
            'custom_bg_path': self.avatar_bg_path.text().strip() if self.avatar_bg_combo.currentIndex() == 0 else None,
            'test_mode': False
        }

    def get_veo3_settings(self) -> dict:
        """Return settings understood by both current and legacy Veo pipelines."""
        if not self.enable_veo3_cb.isChecked():
            return {'enabled': False}
        duration = int(self.veo3_duration_combo.currentText().split()[0])
        resolution = self.veo3_resolution_combo.currentText()
        aspect_text = self.veo3_aspect_combo.currentText()
        aspect_ratio = "9:16" if "9:16" in aspect_text else "16:9"
        return {
            'enabled': True,
            'duration': duration,
            'intro_duration': duration,
            'resolution': resolution,
            'quality': self.veo3_resolution_combo.currentIndex(),
            'aspect_ratio': aspect_ratio,
            'style': self.veo3_style_combo.currentIndex(),
            'content_mode': self.veo3_content_mode_combo.currentData(),
            'prompt_source': self.veo3_prompt_source_combo.currentData(),
            'custom_prompt': self.veo3_custom_prompt_edit.toPlainText().strip(),
            'motion_intensity': self.veo3_motion_combo.currentData(),
            'camera_style': self.veo3_camera_combo.currentData(),
            'avoid_text': self.veo3_no_text_cb.isChecked(),
            'model': self.veo3_model_combo.currentData(),
            'use_multishot': self.veo3_multishot_cb.isChecked(),
            'num_actions': self.veo3_actions_spin.value(),
            'placement': self.veo3_placement_combo.currentData(),
            'insert_count': self.veo3_insert_count_spin.value(),
            'insert_position_percent': self.veo3_insert_position_spin.value(),
            'blend_seconds': round(self.veo3_blend_spin.value(), 2),
            'audio_policy': self.veo3_audio_policy_combo.currentData(),
            'reference_frames': self.veo3_reference_frames_spin.value(),
            'realism': self.veo3_realism_combo.currentData(),
            'seed': self.veo3_seed_spin.value() or None,
            'negative_prompt': self.veo3_negative_prompt_edit.toPlainText().strip(),
        }
    
    # === Public methods for getting/setting values ===
    
    def get_theme(self) -> str:
        """Get current theme text"""
        return self.theme_input.text().strip()
    
    def get_language(self) -> str:
        """Get selected language (clean name without emoji)"""
        from gui.constants import get_internal_language
        lang_text = self.language_combo.currentText()
        return get_internal_language(lang_text)
    
    def get_persona_index(self) -> int:
        """Get selected persona index"""
        return self.persona_combo.currentIndex()
    
    def get_num_videos(self) -> int:
        """Get number of videos to generate"""
        return self.num_videos_spin.value()
    
    def get_duration_seconds(self) -> int:
        """Get total duration in seconds"""
        return (self.hours_spin.value() * 3600 + 
                self.minutes_spin.value() * 60 + 
                self.seconds_spin.value())

    def _on_duration_hours_changed(self, hours: int) -> None:
        """Keep the compound duration control inside the supported 3h ceiling."""
        at_limit = int(hours) >= 3
        if at_limit:
            self.minutes_spin.setValue(0)
            self.seconds_spin.setValue(0)
        self.minutes_spin.setMaximum(0 if at_limit else 59)
        self.seconds_spin.setMaximum(0 if at_limit else 59)
    
    def get_resolution(self) -> tuple:
        """Get resolution as (width, height)"""
        text = self.resolution_combo.currentText()
        # Accept saved/custom labels such as "1080×1920", "1080x1920" or "1080 х 1920".
        match = re.search(r'(\d+)\s*(?:x|×|х|Х|Г—)\s*(\d+)', text, re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 1080, 1920
    
    def is_tts_enabled(self) -> bool:
        """Check if TTS is enabled"""
        return self.enable_tts_cb.isChecked()
    
    def is_ai_images_enabled(self) -> bool:
        """Check if AI image generation is enabled"""
        return self.enable_ai_images_cb.isChecked()
    
    # === Browse dialogs ===
    
    def _browse_music(self):
        """Browse for music folder"""
        from PyQt5.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, t('select_music_folder'))
        if folder:
            self.music_path_input.setText(folder)
    
    def _browse_overlay(self):
        """Browse for overlay file"""
        from PyQt5.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getOpenFileName(
            self, t('select_overlay_file'),
            "", t('media_files_filter')
        )
        if file_path:
            self.overlay_path_input.setText(file_path)
    
    def _browse_custom_images(self):
        """Browse for custom images folder"""
        from PyQt5.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, t('select_images_folder'))
        if folder:
            self.custom_images_input.setText(folder)

    def _on_first_shot_image_toggled(self, enabled):
        self.burn_first_shot_title_cb.setEnabled(enabled)
        if not enabled:
            self.burn_first_shot_title_cb.setChecked(False)
        self._update_first_shot_title_state()

    def _update_first_shot_title_state(self):
        first_shot_enabled = self.first_shot_image_cb.isChecked()
        burn_title_enabled = self.burn_first_shot_title_cb.isChecked()
        self.first_shot_title_input.setEnabled(first_shot_enabled and burn_title_enabled)

    # _browse_first_shot_image removed:
    # первый шот теперь берётся автоматически из пула, без выбора файла

    def _browse_custom_videos(self):
        """Browse for custom videos folder"""
        from PyQt5.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, t('select_video_folder'))
        if folder:
            self.custom_videos_input.setText(folder)
    
    def _browse_output(self):
        """Browse for output folder"""
        from PyQt5.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, t('select_output_folder'))
        if folder:
            self.output_path_input.setText(folder)
            
    def _browse_reference_images(self):
        """Browse for reference images folder"""
        from PyQt5.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, t('select_reference_folder'))
        if folder:
            self.reference_images_input.setText(folder)

    # === Image Cache Methods ===
    
    def _refresh_cache_stats(self):
        """Refresh and display image cache statistics"""
        try:
            from core.image_cache import get_image_cache
            cache = get_image_cache(enabled=True)
            stats = cache.get_stats()
            
            self.cache_stats_label.setText(t('cache_stats').format(
                images=stats['cached_images'], size=stats['cache_size_mb'],
                saved=f"{stats['total_saved_usd']:.2f}",
            ))
        except Exception as e:
            self.cache_stats_label.setText(t('operation_error').format(error=str(e)[:30]))
    
    def _clear_image_cache(self):
        """Clear all cached images with confirmation"""
        if ask_yes_no(
            self,
            t('cache_clear_title'),
            t('cache_clear_question'),
            default_yes=False,
        ):
            try:
                from core.image_cache import get_image_cache
                cache = get_image_cache(enabled=True)
                cache.clear_all()
                self._refresh_cache_stats()
                show_information(self, t('done_title'), t('cache_cleared'))
            except Exception as e:
                show_warning(
                    self,
                    t('error'),
                    t('cache_clear_failed').format(error=e),
                )
    
    def get_cache_settings(self) -> dict:
        """Get image cache settings"""
        return {
            'use_image_cache': False,
            'save_to_image_cache': False
        }

    # === Image Pool Methods ===
    
    def _on_image_pool_toggled(self, enabled):
        """Handle image pool toggle - show/hide pool settings"""
        self.pool_settings_widget.setVisible(enabled)
        if enabled:
            self._refresh_pool_list()
    
    def _refresh_pool_list(self):
        """Refresh list of available image pools"""
        try:
            from core.image_processor import ImageProcessor
            from pathlib import Path
            
            output_dir = Path(self.output_path_input.text() or "generated")
            pools = ImageProcessor.get_available_pools(output_dir)
            
            self.pool_combo.clear()
            self.pool_combo.addItem(t('pool_select_placeholder'), None)
            
            for pool in pools:
                display_text = f"{pool['theme'][:30]}... ({t('pool_item_count').format(count=pool['num_images'])})"
                self.pool_combo.addItem(display_text, pool['path'])
            
            if pools:
                self.pool_status_label.setText(t('pool_found').format(count=len(pools)))
            else:
                self.pool_status_label.setText(t('pool_not_found'))
                
        except Exception as e:
            self.pool_status_label.setText(t('operation_error').format(error=str(e)[:50]))
    
    def _generate_image_pool(self):
        """Generate a new image pool for the current theme"""
        from PyQt5.QtWidgets import QProgressDialog
        from PyQt5.QtCore import Qt
        
        # Получаем тему
        theme = self.theme_input.text().strip()
        if not theme:
            show_warning(self, t('error'), t('pool_theme_required'))
            return
        
        # Получаем API ключ
        api_key = None
        try:
            # Пробуем получить из родительского окна
            if hasattr(self, 'parent_window') and self.parent_window:
                settings_tab = getattr(self.parent_window, 'settings_tab', None)
                if settings_tab:
                    api_key = settings_tab.get_gemini_api_key()
            
            # Fallback на config.json
            if not api_key:
                import json
                from pathlib import Path
                config_path = Path("config.json")
                if config_path.exists():
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                    api_key = config.get('user_settings', {}).get('gemini_api_key', '')
        except Exception:
            pass
        
        if not api_key:
            show_warning(self, t('error'), t('api_key_missing_settings'))
            return
        
        num_images = self.pool_size_spin.value()
        
        # Подтверждение
        if not ask_yes_no(
            self,
            t('pool_generation_title'),
            t('pool_generation_confirm').format(
                count=num_images, theme=theme[:100], seconds=num_images * 3
            ),
            default_yes=False,
        ):
            return
        
        # Создаём прогресс диалог
        progress = QProgressDialog(
            t('pool_generation_progress').format(current=0, total=num_images),
            t('cancel_action'),
            0, 100,
            self
        )
        progress.setWindowTitle("🖼️ " + t('pool_generation_title'))
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        
        # Запускаем генерацию в отдельном потоке
        from PyQt5.QtCore import QThread, pyqtSignal
        
        class PoolGeneratorThread(QThread):
            progress_signal = pyqtSignal(int)
            log_signal = pyqtSignal(str)
            finished_signal = pyqtSignal(list)
            error_signal = pyqtSignal(str)
            
            def __init__(self, theme, num_images, api_key, output_dir, video_settings, image_model):
                super().__init__()
                self.theme = theme
                self.num_images = num_images
                self.api_key = api_key
                self.output_dir = output_dir
                self.video_settings = video_settings
                self.image_model = image_model
                self._stop_requested = False
            
            def stop(self):
                self._stop_requested = True
            
            def run(self):
                try:
                    from core.image_processor import ImageProcessor
                    processor = ImageProcessor()
                    
                    paths = processor.generate_image_pool(
                        theme=self.theme,
                        num_images=self.num_images,
                        api_key=self.api_key,
                        output_dir=self.output_dir,
                        video_settings=self.video_settings,
                        log_callback=lambda msg: self.log_signal.emit(msg),
                        image_model=self.image_model,
                        progress_callback=lambda p: self.progress_signal.emit(p)
                    )
                    
                    self.finished_signal.emit(paths)
                except Exception as e:
                    self.error_signal.emit(str(e))
        
        # Получаем настройки
        from pathlib import Path
        output_dir = Path(self.output_path_input.text() or "generated")
        width, height = self.get_resolution()
        video_settings = {
            'width': width,
            'height': height,
            'fps': int(self.fps_combo.currentText().split()[0]),
            'duration': self.get_duration_seconds(),
        }
        image_model = self.image_model_combo.currentData() or self.image_model_combo.currentText()
        
        # Создаём и запускаем поток
        self._pool_thread = PoolGeneratorThread(
            theme, num_images, api_key, output_dir, video_settings, image_model
        )
        
        def on_progress(value):
            progress.setValue(value)
            progress.setLabelText(t('pool_generation_progress').format(
                current=int(value * num_images / 100), total=num_images
            ))
        
        def on_finished(paths):
            progress.close()
            self._refresh_pool_list()
            show_information(
                self,
                t('done_title'),
                t('pool_created').format(count=len(paths))
            )
        
        def on_error(error):
            progress.close()
            show_warning(
                self,
                t('error'),
                t('pool_create_failed').format(error=error),
            )
        
        self._pool_thread.progress_signal.connect(on_progress)
        self._pool_thread.finished_signal.connect(on_finished)
        self._pool_thread.error_signal.connect(on_error)
        self._pool_thread.finished.connect(self._cleanup_pool_thread)
        
        progress.canceled.connect(self._pool_thread.stop)
        
        self._pool_thread.start()

    def _cleanup_pool_thread(self):
        thread = self.sender() or getattr(self, "_pool_thread", None)
        if thread is None:
            return
        try:
            thread.deleteLater()
        except RuntimeError:
            pass
        if getattr(self, "_pool_thread", None) is thread:
            self._pool_thread = None

    def stop_background_threads(self, wait_ms: int = 3000) -> bool:
        thread = getattr(self, "_pool_thread", None)
        if thread is None:
            return True
        if thread.isRunning():
            try:
                thread.stop()
            except AttributeError:
                pass
            if not thread.wait(wait_ms):
                return False
        self._cleanup_pool_thread()
        return True
    
    def get_image_pool_settings(self) -> dict:
        """Get image pool settings"""
        pool_path = None
        if self.use_image_pool_cb.isChecked():
            pool_path = self.pool_combo.currentData()
        
        return {
            'use_image_pool': self.use_image_pool_cb.isChecked(),
            'pool_path': str(pool_path) if pool_path else None,
            'pool_size': self.pool_size_spin.value()
        }

    # === Toggle handlers for linked UI elements ===
    
    def _on_tts_provider_changed(self, index):
        """Refresh provider-specific voices without a network round-trip."""
        self._refresh_tts_voices()
        tts_enabled = self.enable_tts_cb.isChecked()
        self.tts_voice_combo.setEnabled(tts_enabled)
        self.voice_label.setEnabled(tts_enabled)
        edge_enabled = tts_enabled and index == 0
        for widget in (
            self.edge_pitch_label, self.edge_pitch_spin,
            self.edge_volume_label, self.edge_volume_spin,
            self.auto_fit_tts_cb,
        ):
            widget.setEnabled(edge_enabled)

    def _refresh_tts_voices(self, _index=None):
        if not hasattr(self, 'tts_voice_combo') or not hasattr(self, 'tts_provider_combo'):
            return
        previous = self.tts_voice_combo.currentText()
        if self.tts_provider_combo.currentIndex() == 0:
            language = get_internal_language(self.language_combo.currentText())
            voices = EDGE_TTS_VOICES.get(language, EDGE_TTS_VOICES["English"])
            tooltip = t('edge_voices_language_hint')
        else:
            voices = GEMINI_VOICES
            tooltip = t('gemini_voice_hint')
        self.tts_voice_combo.blockSignals(True)
        self.tts_voice_combo.clear()
        self.tts_voice_combo.addItems(voices)
        if previous in voices:
            self.tts_voice_combo.setCurrentText(previous)
        self.tts_voice_combo.setToolTip(tooltip)
        self.tts_voice_combo.blockSignals(False)
    
    def _on_ai_images_toggled(self, enabled):
        """Handle AI images toggle - enable/disable related settings"""
        self.image_model_combo.setEnabled(enabled)
        self.image_model_label.setEnabled(enabled)
        self.strict_images_cb.setEnabled(enabled)
        self.scene_variety_cb.setEnabled(enabled)
        self.num_unique_images_spin.setEnabled(enabled and not self.unlimited_images_cb.isChecked())
        self.images_label.setEnabled(enabled)
        self.unlimited_images_cb.setEnabled(enabled)
    
    def _on_subtitles_toggled(self, enabled):
        """Handle subtitles toggle - enable/disable related settings"""
        self.font_combo.setEnabled(enabled)
        self.font_label.setEnabled(enabled)
        self.font_size_spin.setEnabled(enabled)
        self.font_size_label.setEnabled(enabled)
        self.subtitle_position_combo.setEnabled(enabled)
        self.subtitle_pos_label.setEnabled(enabled)
        self.animated_subtitles_cb.setEnabled(enabled)
        for widget in (
            self.subtitle_style_combo, self.subtitle_color_input, self.subtitle_highlight_input,
            self.subtitle_words_spin, self.subtitle_outline_spin, self.subtitle_bg_opacity_spin,
            self.subtitle_timing_offset_spin, self.subtitle_uppercase_cb,
        ):
            widget.setEnabled(enabled)
        self._on_subtitle_animation_toggled(
            enabled and self.animated_subtitles_cb.isChecked()
        )

    def _on_subtitle_animation_toggled(self, enabled):
        enabled = bool(enabled and self.enable_subtitles_cb.isChecked())
        self.subtitle_animation_combo.setEnabled(enabled)
        self._on_subtitle_animation_changed()

    def _on_subtitle_animation_changed(self, _index=None):
        enabled = (
            self.enable_subtitles_cb.isChecked()
            and self.animated_subtitles_cb.isChecked()
        )
        mode = self.subtitle_animation_combo.currentData() or 'auto'
        fade_enabled = enabled and mode in {'auto', 'fade', 'typewriter'}
        typewriter_enabled = enabled and mode in {'auto', 'typewriter'}
        self.subtitle_fade_label.setEnabled(fade_enabled)
        self.subtitle_fade_spin.setEnabled(fade_enabled)
        self.subtitle_typewriter_label.setEnabled(typewriter_enabled)
        self.subtitle_typewriter_speed_spin.setEnabled(typewriter_enabled)

    def get_subtitle_animation_settings(self) -> dict:
        mode = self.subtitle_animation_combo.currentData() or 'auto'
        if not self.animated_subtitles_cb.isChecked():
            mode = 'none'
        fade_ms = self.subtitle_fade_spin.value()
        return {
            'animated_subtitle': mode != 'none',
            'subtitle_animation': mode,
            'subtitle_fade_in_ms': min(fade_ms, 180) if fade_ms else 0,
            'subtitle_fade_out_ms': fade_ms,
            'subtitle_typewriter_cps': self.subtitle_typewriter_speed_spin.value(),
        }

    def _on_video_effect_changed(self, _index=None):
        effect = self.video_effect_combo.currentData() or 'none'
        enabled = effect != 'none'
        self.video_effect_intensity_label.setEnabled(enabled)
        self.video_effect_intensity_spin.setEnabled(enabled)
        auto = effect == 'auto'
        self.video_effect_probability_label.setEnabled(auto)
        self.video_effect_probability_spin.setEnabled(auto)

    def get_video_effect_settings(self) -> dict:
        return {
            'video_effect': self.video_effect_combo.currentData() or 'none',
            'video_effect_intensity': self.video_effect_intensity_spin.value() / 100.0,
            'video_effect_probability': self.video_effect_probability_spin.value() / 100.0,
        }
    
    def _on_tts_toggled(self, enabled):
        """Handle TTS toggle - enable/disable related settings"""
        self.tts_provider_combo.setEnabled(enabled)
        self.provider_label.setEnabled(enabled)
        self.tts_voice_combo.setEnabled(enabled)
        self.voice_label.setEnabled(enabled)
        self.speech_speed_spin.setEnabled(enabled)
        self.speed_label.setEnabled(enabled)
        edge_enabled = enabled and self.tts_provider_combo.currentIndex() == 0
        for widget in (
            self.edge_pitch_label, self.edge_pitch_spin,
            self.edge_volume_label, self.edge_volume_spin,
            self.auto_fit_tts_cb,
        ):
            widget.setEnabled(edge_enabled)
    
    def _on_transitions_toggled(self, enabled):
        """Handle transitions toggle - enable/disable duration and glitch sub-option"""
        self.transition_duration_spin.setEnabled(enabled)
        # Glitch requires transitions to be on
        self.enable_glitch_cb.setEnabled(enabled)
        if not enabled and hasattr(self, 'enable_glitch_cb'):
            self.enable_glitch_cb.setChecked(False)
    
    def _on_music_toggled(self, enabled):
        """Handle music toggle - enable/disable music path and browse button"""
        self.music_path_input.setEnabled(enabled)
        self.music_browse_btn.setEnabled(enabled)
    
    def _on_overlay_toggled(self, enabled):
        """Handle overlay toggle - enable/disable overlay path, browse button and position"""
        self.overlay_path_input.setEnabled(enabled)
        self.overlay_browse_btn.setEnabled(enabled)
        self.overlay_position_combo.setEnabled(enabled)
        self.overlay_pos_label.setEnabled(enabled)
        if hasattr(self, 'overlay_fullscreen_cb'):
            self.overlay_fullscreen_cb.setEnabled(enabled)

    def _on_glitch_toggled(self, enabled):
        """Handle glitch toggle - show/hide glitch settings"""
        self._glitch_settings_widget.setVisible(enabled)
        # Glitch works on top of standard transitions — keep transitions enabled
        # but we can hint the user if transitions are off
        if enabled and hasattr(self, 'enable_transitions_cb') and not self.enable_transitions_cb.isChecked():
            self.enable_transitions_cb.setChecked(True)

    def get_glitch_settings(self) -> dict:
        """Returns current glitch settings dict for the pipeline"""
        enabled = hasattr(self, 'enable_glitch_cb') and self.enable_glitch_cb.isChecked()
        if not enabled:
            return {'glitch_enabled': False, 'glitch_style': 'random',
                    'glitch_duration': 0.4, 'glitch_frequency': 0.5, 'glitch_intensity': 0.65}
        # style key is stored as userData in the combo
        style_key = 'random'
        if hasattr(self, 'glitch_style_combo'):
            style_key = self.glitch_style_combo.currentData() or 'random'
        return {
            'glitch_enabled': True,
            'glitch_style': style_key,
            'glitch_duration': self.glitch_duration_spin.value() if hasattr(self, 'glitch_duration_spin') else 0.4,
            'glitch_frequency': (self.glitch_frequency_spin.value() / 100.0) if hasattr(self, 'glitch_frequency_spin') else 0.5,
            'glitch_intensity': (self.glitch_intensity_spin.value() / 100.0) if hasattr(self, 'glitch_intensity_spin') else 0.65,
        }

    # === Custom Text Mode handlers ===
    
    def _on_custom_text_mode_changed(self, state):
        """Handle custom text mode checkbox state change"""
        enabled = state == Qt.Checked
        
        # Enable/disable folder selection
        self.custom_texts_folder_input.setEnabled(enabled)
        self.custom_texts_folder_btn.setEnabled(enabled)
        
        # Enable/disable theme input (disabled when custom text mode is active)
        self.theme_input.setEnabled(not enabled)
        
        # Update status
        if enabled:
            self.custom_texts_status_label.setText("ℹ️ " + t('custom_text_mode_active'))
            self.custom_texts_status_label.setVisible(True)
            # Clear theme input to avoid confusion
            if not self.theme_input.text():
                self.theme_input.setPlaceholderText(t('custom_text_mode_placeholder'))
            
            # 🔄 AUTO-LOAD: Загрузить тексты если папка указана
            folder = self.custom_texts_folder_input.text().strip()
            if folder and Path(folder).exists():
                self._load_custom_texts(Path(folder))
        else:
            self.custom_texts_status_label.setVisible(False)
            self.custom_texts_folder_input.clear()
            self.theme_input.setPlaceholderText(t('theme_placeholder'))
            # Clear loaded texts
            if hasattr(self, '_loaded_custom_texts'):
                self._loaded_custom_texts = None
    
    def _select_custom_texts_folder(self):
        """Open folder selection dialog for custom texts"""
        from PyQt5.QtWidgets import QFileDialog
        from pathlib import Path
        
        folder = QFileDialog.getExistingDirectory(
            self,
            t('select_custom_texts_folder'),
            str(Path.home()),
            QFileDialog.ShowDirsOnly
        )
        
        if folder:
            self._load_custom_texts(Path(folder))
    
    def _load_custom_texts(self, folder_path):
        """Load and validate custom texts from folder"""
        from core.text_loader import TextLoader
        
        try:
            loader = TextLoader()
            texts = loader.scan_folder(folder_path)
            
            if not texts:
                self.custom_texts_status_label.setText(f"⚠️ {t('no_texts_found')}")
                self.custom_texts_status_label.setStyleSheet(f"""
                    QLabel {{
                        color: {ColorsV2.ACCENT_ORANGE};
                        font-size: 12px;
                        padding: 4px 0;
                    }}
                """)
                self.custom_texts_status_label.setVisible(True)
                return
            
            # Get summary
            summary = loader.get_summary(texts)
            
            # Store loaded texts
            self._loaded_custom_texts = texts
            
            # Update UI
            self.custom_texts_folder_input.setText(str(folder_path))
            
            # Show status
            if summary['invalid'] > 0:
                status_text = f"✅ {t('texts_loaded')}: {summary['valid']}/{summary['total']} (⚠️ {summary['invalid']} {t('invalid')})"
                color = ColorsV2.ACCENT_ORANGE
            else:
                status_text = f"✅ {t('texts_loaded')}: {summary['valid']}/{summary['total']}"
                color = ColorsV2.ACCENT_GREEN
            
            self.custom_texts_status_label.setText(status_text)
            self.custom_texts_status_label.setStyleSheet(f"""
                QLabel {{
                    color: {color};
                    font-size: 12px;
                    padding: 4px 0;
                    font-weight: 500;
                }}
            """)
            self.custom_texts_status_label.setVisible(True)
            
            # 🔄 АВТООБНОВЛЕНИЕ: Обновить количество видео в очереди
            self._auto_update_queue_num_videos(len(texts))
            
        except Exception as e:
            self.custom_texts_status_label.setText(
                "❌ " + t("runtime_error_detail").format(error=e)
            )
            self.custom_texts_status_label.setStyleSheet(f"""
                QLabel {{
                    color: {ColorsV2.ACCENT_RED};
                    font-size: 12px;
                    padding: 4px 0;
                }}
            """)
            self.custom_texts_status_label.setVisible(True)
    
    def _auto_update_queue_num_videos(self, num_texts):
        """Автоматически обновляет количество видео в очереди"""
        try:
            # Получаем доступ к главному окну и вкладке очереди
            parent_window = self.parent_window or self.parent()
            if parent_window:
                if hasattr(parent_window, 'queue_tab'):
                    queue_tab = parent_window.queue_tab
                    if hasattr(queue_tab, 'num_videos_spin'):
                        # Устанавливаем количество
                        queue_tab.num_videos_spin.setValue(num_texts)
                        # Логируем
                        if hasattr(queue_tab, '_log'):
                            queue_tab._log(
                                "🔄 "
                                + t("queue_video_count_updated").format(
                                    count=num_texts
                                )
                            )
        except Exception as e:
            import logging
            logging.debug(f"Ошибка автообновления количества в очереди: {e}")
    
    def get_custom_text_settings(self):
        """Get custom text mode settings"""
        if not self.use_custom_texts_cb.isChecked():
            return {
                'enabled': False,
                'texts': None
            }
        
        if not hasattr(self, '_loaded_custom_texts') or not self._loaded_custom_texts:
            return {
                'enabled': True,
                'texts': None,
                'error': t('custom_text_not_loaded')
            }
        
        from core.text_loader import TextLoader
        loader = TextLoader()
        valid_texts = loader.get_valid_texts(self._loaded_custom_texts)
        
        return {
            'enabled': True,
            'texts': valid_texts
        }
