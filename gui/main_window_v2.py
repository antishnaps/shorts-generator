#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Main Window V2 - Modern UI with tabbed interface

Features:
- Modern dark theme (GitHub-inspired)
- Organized tabs: Generation, Series, Queue, Monitor, Settings
- Preview and logs in separate Monitor tab
"""

import logging
import os
import threading
from pathlib import Path
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QProgressBar,
    QTextEdit, QFrame, QMessageBox, QSizePolicy, QApplication
)
from PyQt5.QtCore import Qt, pyqtSlot, QMetaObject, QTimer
from PyQt5.QtGui import QPixmap, QTextCursor

from gui.styles_v2 import (
    ColorsV2, get_sizes, get_modern_style, get_tab_style_v2,
    get_primary_button_style, get_danger_button_style, get_ghost_button_style
)
from gui.app_shell import BrandHeader, MainNavigation
from gui.icons import brand_icon, modernize_widget_tree
from gui.motion import install_motion, set_motion_enabled
from gui.generation_tab import GenerationTab
from gui.settings_tab import SettingsTab
from gui.custom_text_tab import CustomTextTab
from gui.generation_worker import GenerationWorker
from gui.constants import DEFAULT_CONFIG_PATH
from gui.locales.runtime_youtube_main import (
    UPLOAD_EVENT_COMPLETED,
    UPLOAD_EVENT_FAILED,
    UPLOAD_EVENT_RETRY,
    UPLOAD_EVENT_UPLOADING,
)
from gui.localized_dialogs import (
    ask_yes_no,
    show_critical,
    show_information,
    show_warning,
)
from gui.translations import get_language_code, set_ui_language, t
from core.settings_schema import normalize_subtitle_settings
from core.subtitle_styles import normalize_subtitle_position
from core.settings_recovery import should_preserve_previous_config


_ORIGINAL_QMESSAGEBOX_WARNING = getattr(QMessageBox, "warning")


def _show_warning(parent, title: str, text: str) -> int:
    """Use localized buttons while preserving legacy test/host monkeypatches."""
    warning_method = getattr(QMessageBox, "warning")
    if warning_method != _ORIGINAL_QMESSAGEBOX_WARNING:
        return warning_method(parent, title, text)
    return show_warning(parent, title, text)


class MainWindowV2(QMainWindow):
    """Modern main application window."""
    
    # Маппинг русских названий анимаций на внутренние значения
    ANIMATION_TYPE_MAP = {
        "Микс (случайный)": "mix",
        "Панорама влево": "pan_left",
        "Панорама вправо": "pan_right",
        "Зум в центр": "zoom_center",
    }
    
    def __init__(self):
        super().__init__()
        self.config_file = Path(DEFAULT_CONFIG_PATH)
        self.generation_thread = None
        self.generator = None
        self.videos_generated = 0
        self._save_timer = None  # Debounce timer for auto-save
        self._loading_settings = False  # Flag to prevent auto-save during loading
        self._settings_loaded_successfully = False
        self._allow_secret_clear_once = False
        
        
        # Initialize config_manager BEFORE init_ui (needed by CustomTextTab)
        from core.config_manager import ConfigManager
        self.config_manager = ConfigManager()
        set_ui_language(self.config_manager.get_user_setting('ui_language', 'Russian'))
        initial_preferences = self.config_manager.get_user_setting('app_preferences', {}) or {}
        set_motion_enabled(initial_preferences.get('smooth_interface_motion', True))
        
        self.init_ui()
        self._connect_auto_save()  # Connect all widgets to auto-save
        self.load_settings()
        
        
        # 🚀 PERF: Initialize generator in background thread (non-blocking)
        self._init_generator_background()
    
    def init_ui(self):
        """Initialize the modern UI"""
        sizes = get_sizes()
        self.setWindowTitle("ContentBot Pro — Free")
        self.setWindowIcon(brand_icon())
        self.setMinimumSize(sizes.WINDOW_MIN_WIDTH, sizes.WINDOW_MIN_HEIGHT)
        self.resize(sizes.WINDOW_DEFAULT_WIDTH, sizes.WINDOW_DEFAULT_HEIGHT)
        self._center_on_screen()
        
        # Apply modern style
        self.setStyleSheet(get_modern_style())

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        self.brand_header = BrandHeader(self, show_support=True)
        main_layout.addWidget(self.brand_header)

        # === Main Tabs ===
        self.main_tabs = QTabWidget()
        self.main_tabs.setStyleSheet(get_tab_style_v2())
        
        navigation_items = []

        # Tab 1: Generation
        self.generation_tab = GenerationTab(self)
        self.main_tabs.addTab(self.generation_tab, t('tab_generation'))
        navigation_items.append((t('tab_generation'), "generation"))
        
        # Tab 2: Series (generator=None initially, updated in background)
        try:
            from gui.series_tab_qt import SeriesTabQt
            self.series_tab = SeriesTabQt(None, self)
            self.main_tabs.addTab(self.series_tab, t('tab_series'))
            navigation_items.append((t('tab_series'), "series"))
        except Exception as e:
            logging.error(f"Failed to load series tab: {e}")
            self.series_tab = None
        
        # Tab 3: Queue (generator=None initially, updated in background)
        try:
            from gui.queue_tab import QueueTab
            self.queue_tab = QueueTab(None, self)
            self.main_tabs.addTab(self.queue_tab, t('tab_queue'))
            navigation_items.append((t('tab_queue'), "queue"))
        except Exception as e:
            logging.error(f"Failed to load queue tab: {e}")
            self.queue_tab = None
        
        # Tab 4: Monitor (Preview + Logs)
        self.monitor_tab = self._create_monitor_tab()
        self.main_tabs.addTab(self.monitor_tab, t('tab_monitor'))
        navigation_items.append((t('tab_monitor'), "monitor"))
        
        # Tab 5: Custom Text
        self.custom_text_tab = CustomTextTab(self)
        self.main_tabs.addTab(self.custom_text_tab, t('tab_custom_text'))
        navigation_items.append((t('tab_custom_text'), "text"))
        
        # Tab 6: Static Video
        try:
            from gui.static_video_tab import StaticVideoTab
            self.static_video_tab = StaticVideoTab()
            self.main_tabs.addTab(self.static_video_tab, t('tab_static_video'))
            navigation_items.append((t('tab_static_video'), "static"))
        except Exception as e:
            logging.error(f"Failed to load static video tab: {e}")
            self.static_video_tab = None

        # Tab 7: Channel remake preparation
        try:
            from gui.channel_clone_tab import ChannelCloneTab
            self.channel_clone_tab = ChannelCloneTab(self)
            self.main_tabs.addTab(self.channel_clone_tab, t('tab_channel_clone'))
            navigation_items.append((t('tab_channel_clone'), "clone"))
        except Exception as e:
            logging.error(f"Failed to load channel clone tab: {e}")
            self.channel_clone_tab = None

        # Tab 8: YouTube auto-publisher
        try:
            from gui.youtube_publish_tab import YouTubePublishTab
            self.youtube_publish_tab = YouTubePublishTab(self)
            self.youtube_publish_tab.log_message.connect(self._on_youtube_log)
            self.youtube_publish_tab.upload_activity.connect(self._on_youtube_activity)
            self.main_tabs.addTab(self.youtube_publish_tab, "YouTube")
            navigation_items.append(("YouTube", "youtube"))
        except Exception as e:
            logging.error(f"Failed to load YouTube publish tab: {e}")
            self.youtube_publish_tab = None

        # Optional read-only channel analytics
        try:
            from gui.channel_analytics_tab import ChannelAnalyticsTab
            self.channel_analytics_tab = ChannelAnalyticsTab(self)
            self.channel_analytics_tab.log_message.connect(self.add_log)
            self.main_tabs.addTab(self.channel_analytics_tab, t('tab_analytics'))
            navigation_items.append((t('tab_analytics'), "analytics"))
        except Exception as e:
            logging.error(f"Failed to load channel analytics tab: {e}")
            self.channel_analytics_tab = None

        # Settings
        self.settings_tab = SettingsTab(self)
        self.main_tabs.addTab(self.settings_tab, t('tab_settings'))
        navigation_items.append((t('tab_settings'), "settings"))

        self.main_navigation = MainNavigation(self)
        self.main_navigation.set_items(navigation_items)
        self.main_navigation.tab_selected.connect(self.main_tabs.setCurrentIndex)
        self.main_tabs.currentChanged.connect(self.main_navigation.set_current)
        self.main_tabs.currentChanged.connect(self._sync_control_panel_visibility)
        self.main_tabs.tabBar().hide()
        main_layout.addWidget(self.main_navigation)
        
        main_layout.addWidget(self.main_tabs, 1)
        
        # === Bottom Control Panel ===
        self.control_panel = self._create_control_panel()
        main_layout.addWidget(self.control_panel)
        self._sync_control_panel_visibility(self.main_tabs.currentIndex())
        
        # Status bar
        self.statusBar().showMessage(t("shell_ready"))
        self.statusBar().setStyleSheet(f"""
            QStatusBar {{
                background-color: {ColorsV2.BG_DARK};
                color: {ColorsV2.TEXT_SECONDARY};
                border-top: 1px solid {ColorsV2.BORDER_DEFAULT};
                padding: 4px 12px;
            }}
        """)
        modernize_widget_tree(central)
        self._motion_controller = install_motion(central)

    def _sync_control_panel_visibility(self, _index=None):
        """Keep the generic generation controls away from specialized workflows."""
        if not hasattr(self, "control_panel"):
            return
        channel_tab = getattr(self, "channel_clone_tab", None)
        youtube_tab = getattr(self, "youtube_publish_tab", None)
        analytics_tab = getattr(self, "channel_analytics_tab", None)
        self.control_panel.setVisible(
            self.main_tabs.currentWidget() not in (channel_tab, youtube_tab, analytics_tab)
        )
    
    def _connect_auto_save(self):
        """Connect all widgets to auto-save on change"""
        from PyQt5.QtCore import QTimer
        
        # Create debounce timer (saves 500ms after last change)
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._do_auto_save)
        
        gt = self.generation_tab
        st = self.settings_tab
        
        # === Generation Tab widgets ===
        # Theme tab
        gt.theme_input.textChanged.connect(self._schedule_auto_save)
        gt.language_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.persona_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.strict_theme_cb.toggled.connect(self._schedule_auto_save)
        gt.seamless_loop_cb.toggled.connect(self._schedule_auto_save)
        gt.comment_bait_cb.toggled.connect(self._schedule_auto_save)
        
        # Video tab
        gt.num_videos_spin.valueChanged.connect(self._schedule_auto_save)
        gt.resolution_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.fps_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.hours_spin.valueChanged.connect(self._schedule_auto_save)
        gt.minutes_spin.valueChanged.connect(self._schedule_auto_save)
        gt.seconds_spin.valueChanged.connect(self._schedule_auto_save)
        gt.enable_animation_cb.toggled.connect(self._schedule_auto_save)
        gt.animation_type_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.animation_speed_spin.valueChanged.connect(self._schedule_auto_save)
        gt.enable_transitions_cb.toggled.connect(self._schedule_auto_save)
        gt.transition_duration_spin.valueChanged.connect(self._schedule_auto_save)
        gt.shot_min_spin.valueChanged.connect(self._schedule_auto_save)
        gt.shot_max_spin.valueChanged.connect(self._schedule_auto_save)
        
        # Audio tab
        gt.enable_tts_cb.toggled.connect(self._schedule_auto_save)
        gt.tts_provider_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.tts_voice_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.speech_speed_spin.valueChanged.connect(self._schedule_auto_save)
        gt.edge_pitch_spin.valueChanged.connect(self._schedule_auto_save)
        gt.edge_volume_spin.valueChanged.connect(self._schedule_auto_save)
        gt.auto_fit_tts_cb.toggled.connect(self._schedule_auto_save)
        gt.enable_music_cb.toggled.connect(self._schedule_auto_save)
        gt.music_path_input.textChanged.connect(self._schedule_auto_save)
        gt.music_volume_slider.valueChanged.connect(self._schedule_auto_save)
        
        # Visual tab
        gt.enable_ai_images_cb.toggled.connect(self._schedule_auto_save)
        gt.image_model_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.strict_images_cb.toggled.connect(self._schedule_auto_save)
        gt.scene_variety_cb.toggled.connect(self._schedule_auto_save)
        gt.num_unique_images_spin.valueChanged.connect(self._schedule_auto_save)
        gt.unlimited_images_cb.toggled.connect(self._schedule_auto_save)
        gt.custom_images_input.textChanged.connect(self._schedule_auto_save)
        gt.use_image_cache_cb.toggled.connect(self._schedule_auto_save)
        gt.save_to_cache_cb.toggled.connect(self._schedule_auto_save)
        if hasattr(gt, 'first_shot_image_cb'):
            gt.first_shot_image_cb.toggled.connect(self._schedule_auto_save)
        if hasattr(gt, 'burn_first_shot_title_cb'):
            gt.burn_first_shot_title_cb.toggled.connect(self._schedule_auto_save)
        if hasattr(gt, 'first_shot_title_input'):
            gt.first_shot_title_input.textChanged.connect(self._schedule_auto_save)
        gt.enable_subtitles_cb.toggled.connect(self._schedule_auto_save)
        gt.font_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.font_size_spin.valueChanged.connect(self._schedule_auto_save)
        gt.subtitle_position_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.animated_subtitles_cb.toggled.connect(self._schedule_auto_save)
        gt.subtitle_animation_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.subtitle_fade_spin.valueChanged.connect(self._schedule_auto_save)
        gt.subtitle_typewriter_speed_spin.valueChanged.connect(self._schedule_auto_save)
        gt.subtitle_style_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.subtitle_color_input.textChanged.connect(self._schedule_auto_save)
        gt.subtitle_highlight_input.textChanged.connect(self._schedule_auto_save)
        gt.subtitle_words_spin.valueChanged.connect(self._schedule_auto_save)
        gt.subtitle_outline_spin.valueChanged.connect(self._schedule_auto_save)
        gt.subtitle_bg_opacity_spin.valueChanged.connect(self._schedule_auto_save)
        gt.subtitle_timing_offset_spin.valueChanged.connect(self._schedule_auto_save)
        gt.subtitle_uppercase_cb.toggled.connect(self._schedule_auto_save)
        gt.enable_overlay_cb.toggled.connect(self._schedule_auto_save)
        gt.overlay_path_input.textChanged.connect(self._schedule_auto_save)
        gt.overlay_position_combo.currentIndexChanged.connect(self._schedule_auto_save)
        if hasattr(gt, 'overlay_fullscreen_cb'):
            gt.overlay_fullscreen_cb.toggled.connect(self._schedule_auto_save)
        gt.video_effect_combo.currentIndexChanged.connect(self._schedule_auto_save)
        gt.video_effect_intensity_spin.valueChanged.connect(self._schedule_auto_save)
        gt.video_effect_probability_spin.valueChanged.connect(self._schedule_auto_save)
        
        # Reference images
        gt.use_reference_images_cb.toggled.connect(self._schedule_auto_save)
        gt.reference_images_input.textChanged.connect(self._schedule_auto_save)
        
        # Image pool
        gt.use_image_pool_cb.toggled.connect(self._schedule_auto_save)
        gt.pool_size_spin.valueChanged.connect(self._schedule_auto_save)
        gt.start_with_images_cb.toggled.connect(self._schedule_auto_save)
        gt.use_only_custom_images_cb.toggled.connect(self._schedule_auto_save)
        gt.use_custom_texts_cb.toggled.connect(self._schedule_auto_save)
        gt.custom_texts_folder_input.textChanged.connect(self._schedule_auto_save)

        # ⚡ Glitch transitions
        if hasattr(gt, 'enable_glitch_cb'):
            gt.enable_glitch_cb.toggled.connect(self._schedule_auto_save)
        if hasattr(gt, 'glitch_style_combo'):
            gt.glitch_style_combo.currentIndexChanged.connect(self._schedule_auto_save)
        if hasattr(gt, 'glitch_duration_spin'):
            gt.glitch_duration_spin.valueChanged.connect(self._schedule_auto_save)
        if hasattr(gt, 'glitch_frequency_spin'):
            gt.glitch_frequency_spin.valueChanged.connect(self._schedule_auto_save)
        if hasattr(gt, 'glitch_intensity_spin'):
            gt.glitch_intensity_spin.valueChanged.connect(self._schedule_auto_save)
        # Advanced tab
        gt.enable_parallel_cb.toggled.connect(self._schedule_auto_save)
        gt.num_workers_spin.valueChanged.connect(self._schedule_auto_save)
        gt.output_path_input.textChanged.connect(self._schedule_auto_save)
        gt.enable_veo3_cb.toggled.connect(self._schedule_auto_save)
        for widget in (
            gt.veo3_model_combo, gt.veo3_duration_combo, gt.veo3_resolution_combo,
            gt.veo3_aspect_combo, gt.veo3_style_combo, gt.veo3_content_mode_combo,
            gt.veo3_prompt_source_combo, gt.veo3_motion_combo, gt.veo3_camera_combo,
            gt.veo3_placement_combo, gt.veo3_audio_policy_combo, gt.veo3_realism_combo,
        ):
            widget.currentIndexChanged.connect(self._schedule_auto_save)
        gt.veo3_custom_prompt_edit.textChanged.connect(self._schedule_auto_save)
        gt.veo3_negative_prompt_edit.textChanged.connect(self._schedule_auto_save)
        gt.veo3_no_text_cb.toggled.connect(self._schedule_auto_save)
        gt.veo3_multishot_cb.toggled.connect(self._schedule_auto_save)
        gt.veo3_actions_spin.valueChanged.connect(self._schedule_auto_save)
        gt.veo3_insert_count_spin.valueChanged.connect(self._schedule_auto_save)
        gt.veo3_insert_position_spin.valueChanged.connect(self._schedule_auto_save)
        gt.veo3_blend_spin.valueChanged.connect(self._schedule_auto_save)
        gt.veo3_reference_frames_spin.valueChanged.connect(self._schedule_auto_save)
        gt.veo3_seed_spin.valueChanged.connect(self._schedule_auto_save)
        gt.enable_youtube_mix_cb.toggled.connect(self._schedule_auto_save)
        gt.yt_video_ratio_spin.valueChanged.connect(self._schedule_auto_save)
        gt.yt_clip_min_spin.valueChanged.connect(self._schedule_auto_save)
        gt.yt_clip_max_spin.valueChanged.connect(self._schedule_auto_save)
        gt.custom_videos_input.textChanged.connect(self._schedule_auto_save)  # 🎬 Custom videos folder
        gt.source_youtube_cb.toggled.connect(self._schedule_auto_save)
        gt.source_local_cb.toggled.connect(self._schedule_auto_save)
        gt.source_pexels_cb.toggled.connect(self._schedule_auto_save)
        gt.source_pixabay_cb.toggled.connect(self._schedule_auto_save)
        gt.source_wikimedia_cb.toggled.connect(self._schedule_auto_save)

        # Avatar tab
        for widget in (
            gt.enable_avatar_cb, gt.avatar_start_cb, gt.avatar_end_cb,
            gt.avatar_random_cb,
        ):
            widget.toggled.connect(self._schedule_auto_save)
        for widget in (gt.avatar_id_input, gt.avatar_voice_id_input, gt.avatar_bg_path):
            widget.textChanged.connect(self._schedule_auto_save)
        for widget in (gt.avatar_style_combo, gt.avatar_bg_combo):
            widget.currentIndexChanged.connect(self._schedule_auto_save)
        
        # === Settings Tab widgets ===
        st.gemini_api_input.textChanged.connect(self._schedule_auto_save)
        st.pixabay_api_input.textChanged.connect(self._schedule_auto_save)
        st.pexels_api_input.textChanged.connect(self._schedule_auto_save)
        st.freesound_api_input.textChanged.connect(self._schedule_auto_save)
        if hasattr(st, 'heygen_api_input'):
            st.heygen_api_input.textChanged.connect(self._schedule_auto_save)
        st.auto_save_cb.toggled.connect(self._schedule_auto_save)
        st.remember_window_cb.toggled.connect(self._schedule_auto_save)
        st.motion_cb.toggled.connect(self._on_motion_setting_changed)
        st.motion_cb.toggled.connect(self._schedule_auto_save)

        
        # === Custom Text Tab widgets ===
        ct = self.custom_text_tab
        ct.enable_custom_text_cb.toggled.connect(self._schedule_auto_save)
        ct.position_combo.currentIndexChanged.connect(self._schedule_auto_save)
        ct.custom_text_edit.textChanged.connect(self._schedule_auto_save)
        ct.ai_customize_cb.toggled.connect(self._schedule_auto_save)
        ct.limit_2000_cb.toggled.connect(self._schedule_auto_save)
        ct.insta_files_cb.toggled.connect(self._schedule_auto_save)
        ct.use_separate_metadata_cb.toggled.connect(self._schedule_auto_save)

        if self.static_video_tab is not None:
            self.static_video_tab.settings_changed.connect(self._schedule_auto_save)
        if self.youtube_publish_tab is not None:
            self.youtube_publish_tab.settings_changed.connect(self._schedule_auto_save)
        if self.channel_analytics_tab is not None:
            self.channel_analytics_tab.settings_changed.connect(self._schedule_auto_save)
        
        # UI Language change handler
        st.language_changed.connect(self._on_ui_language_changed)
    
    def _on_ui_language_changed(self, lang_code: str):
        """Apply a UI language immediately by rebuilding the idle window."""
        if self._loading_settings:
            return
        if self._has_active_background_work():
            # SettingsTab has already updated the process-wide translator before
            # emitting the signal.  Revert both the selector and translator so
            # a rejected switch cannot leave a half-localized running window or
            # accidentally persist the un-applied language.
            from gui.translations import UI_LANGUAGES
            previous_language = self.config_manager.get_user_setting(
                'ui_language', 'Russian'
            )
            set_ui_language(previous_language)
            self.settings_tab.ui_language_combo.blockSignals(True)
            self.settings_tab.ui_language_combo.setCurrentText(
                UI_LANGUAGES.get(previous_language, UI_LANGUAGES['Russian'])
            )
            self.settings_tab.ui_language_combo.blockSignals(False)
            _show_warning(self, t('ui_language'), t('language_busy'))
            return
        self.save_user_settings()
        self._language_reload_index = self.main_tabs.currentIndex()
        QTimer.singleShot(0, self._reload_for_language)

    @staticmethod
    def _thread_is_running(thread) -> bool:
        """Return whether a Qt worker is live without touching or stopping it."""
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except (AttributeError, RuntimeError):
            # A QObject may already have been deleted by its completion slot.
            return False

    def _has_active_background_work(self) -> bool:
        """Keep a language rebuild from interrupting any background operation.

        ``_reload_for_language`` replaces the entire window.  The same workers
        that ``closeEvent`` waits for must therefore make a language change busy;
        otherwise the new window is shown before the old one can close, or an
        in-flight task is stopped merely because the interface language changed.
        """
        if self._thread_is_running(getattr(self, 'generation_thread', None)):
            return True

        tab_threads = (
            ('generation_tab', ('_pool_thread',)),
            ('queue_tab', ('worker',)),
            ('series_tab', ('subtopic_thread', 'generation_thread')),
            ('youtube_publish_tab', ('worker', 'connect_worker')),
            ('static_video_tab', ('worker',)),
            ('channel_clone_tab', ('_worker',)),
            ('channel_analytics_tab', ('connect_worker', 'sync_worker')),
        )
        for tab_name, thread_names in tab_threads:
            tab = getattr(self, tab_name, None)
            if tab is None:
                continue
            if any(
                self._thread_is_running(getattr(tab, thread_name, None))
                for thread_name in thread_names
            ):
                return True

        # ``closeEvent`` intentionally keeps the window open while full YouTube
        # automation owns pending uploads, even between worker iterations.  Treat
        # that state as busy too, so a language reload cannot leave two windows.
        youtube_tab = getattr(self, 'youtube_publish_tab', None)
        auto_upload = getattr(youtube_tab, 'enable_auto_upload_cb', None)
        has_pending = getattr(youtube_tab, 'has_pending_uploads', None)
        try:
            auto_upload_enabled = bool(
                auto_upload is not None and auto_upload.isChecked()
            )
        except (AttributeError, RuntimeError):
            auto_upload_enabled = False
        if auto_upload_enabled and callable(has_pending):
            try:
                if has_pending():
                    return True
            except Exception:
                # If the queue is momentarily locked or unreadable, rebuilding
                # the whole window is less safe than postponing the switch.
                return True
        return False

    def _on_motion_setting_changed(self, enabled: bool):
        set_motion_enabled(enabled)
        if hasattr(self, '_motion_controller'):
            self._motion_controller.scan()

    def _reload_for_language(self):
        geometry = self.geometry()
        new_window = MainWindowV2()
        new_window.setGeometry(geometry)
        new_window.main_tabs.setCurrentIndex(
            min(getattr(self, '_language_reload_index', 0), new_window.main_tabs.count() - 1)
        )
        app = QApplication.instance()
        app._contentbot_main_window = new_window
        new_window.show()
        self.close()
    
    def _schedule_auto_save(self):
        """Schedule auto-save with debounce (500ms delay)"""
        if getattr(self, '_loading_settings', False):
            return  # Skip auto-save during loading
        if self._save_timer:
            self._save_timer.start(500)  # Reset timer to 500ms
    
    def _do_auto_save(self):
        """Perform the actual auto-save"""
        try:
            self.save_user_settings()
            self.statusBar().showMessage(t("settings_saved"), 2000)
        except Exception as e:
            logging.debug(f"Auto-save error: {e}")
    
    def _center_on_screen(self):
        """Center the window on the primary screen"""
        try:
            screen = QApplication.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                w, h = self.width(), self.height()
                x = (geom.width() - w) // 2 + geom.x()
                y = (geom.height() - h) // 2 + geom.y()
                self.move(max(0, x), max(0, y))
        except Exception:
            pass

    def showEvent(self, event):
        """Re-center on first show only (frame geometry is now accurate)"""
        super().showEvent(event)
        if not getattr(self, '_was_shown', False):
            self._was_shown = True
            self._center_on_screen()

    def _init_generator_background(self):
        """Initialize the generator in a background thread to avoid freezing the GUI."""
        def _do_init():
            try:
                logging.info("⚙️ Инициализация генератора в фоновом потоке...")
                from core.generator import ShortsGenerator
                self.generator = ShortsGenerator()
                logging.info("✅ Генератор инициализирован")
            except Exception as e:
                logging.error(f"Failed to init generator in background: {e}")
                self.generator = None
            finally:
                # Notify main thread via queued signal-safe call
                QMetaObject.invokeMethod(self, '_on_generator_ready', Qt.QueuedConnection)

        t = threading.Thread(target=_do_init, daemon=True, name="GeneratorInit")
        t.start()

    @pyqtSlot()
    def _on_generator_ready(self):
        """Called in the main Qt thread when the background generator init completes."""
        # Propagate generator to tabs that were created with generator=None
        if self.series_tab is not None and hasattr(self.series_tab, 'generator'):
            self.series_tab.generator = self.generator
        if self.queue_tab is not None and hasattr(self.queue_tab, 'generator'):
            self.queue_tab.generator = self.generator
        if self.generator is not None:
            self.brand_header.set_status(t("shell_ready"), t("generator_connected"), "success")
            self.statusBar().showMessage(t("generator_ready_message"), 3000)
            logging.info("✅ Генератор передан в вкладки")
        else:
            self.brand_header.set_status(t("shell_attention"), t("generator_unavailable"), "danger")
            self.statusBar().showMessage(t("generator_init_error"), 5000)
    
    def _create_monitor_tab(self):
        """Create Monitor tab with Preview and Logs"""
        sizes = get_sizes()
        tab = QWidget()
        outer_layout = QVBoxLayout(tab)
        outer_layout.setContentsMargins(sizes.SPACING_MD, sizes.SPACING_MD,
                                        sizes.SPACING_MD, sizes.SPACING_MD)
        outer_layout.setSpacing(sizes.SPACING_MD)

        overview = QFrame()
        overview.setObjectName("monitorOverview")
        overview.setStyleSheet(
            f"QFrame#monitorOverview {{ background:{ColorsV2.BG_CARD}; "
            f"border:1px solid {ColorsV2.BORDER_DEFAULT}; border-radius:12px; }}"
        )
        overview_layout = QHBoxLayout(overview)
        overview_layout.setContentsMargins(16, 12, 16, 12)
        overview_layout.setSpacing(12)
        overview_title = QLabel(t("monitor_overview"))
        overview_title.setStyleSheet(
            f"color:{ColorsV2.TEXT_PRIMARY}; font-size:15px; font-weight:600;"
        )
        overview_title.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        overview_layout.addWidget(overview_title)
        overview_layout.addStretch()
        self.monitor_state_value = QLabel(t("monitor_waiting"))
        self.monitor_state_value.setStyleSheet(
            f"color:{ColorsV2.ACCENT_CYAN}; font-weight:600;"
        )
        self.monitor_progress_value = QLabel("0%")
        self.monitor_progress_value.setStyleSheet(
            f"color:{ColorsV2.ACCENT_BLUE_HOVER}; font-weight:600;"
        )
        self.monitor_file_value = QLabel(t("monitor_no_file"))
        self.monitor_file_value.setStyleSheet(f"color:{ColorsV2.TEXT_MUTED};")
        self.monitor_file_value.setWordWrap(True)
        for label, value in (
            (t("monitor_status"), self.monitor_state_value),
            (t("monitor_progress"), self.monitor_progress_value),
            (t("monitor_current_file"), self.monitor_file_value),
        ):
            block = QVBoxLayout()
            caption = QLabel(label)
            caption.setStyleSheet(f"color:{ColorsV2.TEXT_MUTED}; font-size:10px;")
            block.addWidget(caption)
            block.addWidget(value)
            overview_layout.addLayout(block)
        outer_layout.addWidget(overview)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(sizes.SPACING_MD)
        outer_layout.addLayout(layout, 1)
        
        # === Left: Preview ===
        
        # === Left: Preview ===
        preview_card = QFrame()
        preview_card.setStyleSheet(f"""
            QFrame {{
                background-color: {ColorsV2.BG_CARD};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 12px;
            }}
        """)
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 16, 16, 16)
        preview_layout.setSpacing(12)
        
        # Preview header
        preview_header = QLabel(t("monitor_preview"))
        preview_header.setStyleSheet(f"""
            color: {ColorsV2.TEXT_PRIMARY};
            font-size: 16px;
            font-weight: 600;
        """)
        preview_layout.addWidget(preview_header)
        
        # Preview image
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(200, 200)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.setStyleSheet(f"""
            QLabel {{
                background-color: {ColorsV2.BG_INPUT};
                border: 2px dashed {ColorsV2.BORDER_DEFAULT};
                border-radius: 8px;
                color: {ColorsV2.TEXT_MUTED};
                font-size: 14px;
            }}
        """)
        self.preview_label.setText(t("monitor_preview_empty"))
        preview_layout.addWidget(self.preview_label, 1)
        
        # Preview stats
        self.preview_stats = QLabel(t("monitor_waiting"))
        self.preview_stats.setStyleSheet(f"color: {ColorsV2.TEXT_MUTED}; font-size: 12px;")
        preview_layout.addWidget(self.preview_stats)
        
        layout.addWidget(preview_card, 1)
        
        # === Right: Logs ===
        logs_card = QFrame()
        logs_card.setStyleSheet(f"""
            QFrame {{
                background-color: {ColorsV2.BG_CARD};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 12px;
            }}
        """)
        logs_layout = QVBoxLayout(logs_card)
        logs_layout.setContentsMargins(16, 16, 16, 16)
        logs_layout.setSpacing(12)
        
        # Logs header with buttons
        logs_header = QHBoxLayout()
        logs_title = QLabel(t("monitor_logs"))
        logs_title.setStyleSheet(f"""
            color: {ColorsV2.TEXT_PRIMARY};
            font-size: 16px;
            font-weight: 600;
        """)
        logs_header.addWidget(logs_title)
        logs_header.addStretch()
        
        # Clear button
        clear_btn = QPushButton(t("monitor_clear"))
        clear_btn.setStyleSheet(get_ghost_button_style())
        clear_btn.clicked.connect(self._clear_logs)
        logs_header.addWidget(clear_btn)
        
        # Export button
        export_btn = QPushButton(t("monitor_export"))
        export_btn.setStyleSheet(get_ghost_button_style())
        export_btn.clicked.connect(self._export_logs)
        logs_header.addWidget(export_btn)
        
        logs_layout.addLayout(logs_header)
        
        # Log text
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(f"""
            QTextEdit {{
                background-color: {ColorsV2.BG_DARKEST};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 8px;
                padding: 12px;
                font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
                font-size: 12px;
                color: {ColorsV2.TEXT_PRIMARY};
                line-height: 1.4;
            }}
        """)
        self.log_text.document().setMaximumBlockCount(2000)
        logs_layout.addWidget(self.log_text, 1)
        
        layout.addWidget(logs_card, 1)
        
        return tab

    def _create_control_panel(self):
        """Create bottom control panel with progress and buttons"""
        panel = QFrame()
        panel.setObjectName("commandDock")
        panel.setMinimumHeight(72)
        panel.setMaximumHeight(110)
        
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)
        
        # Left: Stats
        stats_layout = QVBoxLayout()
        stats_layout.setSpacing(4)
        
        self.stats_label = QLabel(t("session_stats"))
        self.stats_label.setStyleSheet(f"color: {ColorsV2.TEXT_PRIMARY}; font-weight: 600;")
        stats_layout.addWidget(self.stats_label)
        
        self.videos_label = QLabel(t("videos_generated").format(count=self.videos_generated))
        self.videos_label.setStyleSheet(f"color: {ColorsV2.TEXT_MUTED}; font-size: 12px;")
        stats_layout.addWidget(self.videos_label)
        
        layout.addLayout(stats_layout)
        
        # Center: Progress
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(6)
        
        progress_header = QHBoxLayout()
        self.progress_label = QLabel(t("ready_to_generate"))
        self.progress_label.setStyleSheet(f"color: {ColorsV2.TEXT_PRIMARY}; font-weight: 500;")
        progress_header.addWidget(self.progress_label)
        
        self.video_counter_label = QLabel("")
        self.video_counter_label.setStyleSheet(f"color: {ColorsV2.ACCENT_BLUE};")
        progress_header.addWidget(self.video_counter_label)
        progress_header.addStretch()
        progress_layout.addLayout(progress_header)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {ColorsV2.BG_LIGHT};
                border: none;
                border-radius: 6px;
                text-align: center;
                color: white;
                font-weight: 600;
                min-height: 24px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {ColorsV2.ACCENT_BLUE}, stop:1 {ColorsV2.ACCENT_CYAN});
                border-radius: 6px;
            }}
        """)
        progress_layout.addWidget(self.progress_bar)
        
        layout.addLayout(progress_layout, 1)
        
        # Right: Buttons
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(12)
        
        self.generate_btn = QPushButton(t('generate'))
        self.generate_btn.setStyleSheet(get_primary_button_style())
        sizes = get_sizes()
        self.generate_btn.setMinimumWidth(sizes.scale(100))
        self.generate_btn.setMinimumHeight(sizes.BUTTON_HEIGHT_LG)
        self.generate_btn.clicked.connect(self.start_generation)
        buttons_layout.addWidget(self.generate_btn)
        
        self.stop_btn = QPushButton(t('stop'))
        self.stop_btn.setStyleSheet(get_danger_button_style())
        self.stop_btn.setMinimumWidth(sizes.scale(70))
        self.stop_btn.setMinimumHeight(sizes.BUTTON_HEIGHT_LG)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_generation)
        buttons_layout.addWidget(self.stop_btn)
        
        layout.addLayout(buttons_layout)
        
        return panel
    
    # === Generation Actions ===
    
    def start_generation(self):
        """Start video generation"""
        # Validate API key first
        api_keys = self.settings_tab.get_gemini_api_keys()
        if not api_keys:
            _show_warning(self, t('error'), t('gemini_key_required'))
            self.main_tabs.setCurrentWidget(self.settings_tab)
            return
        
        api_key = api_keys[0]  # Primary key for generation
        
        gt = self.generation_tab

        # Validate visual video sources before creating workers. In local-only
        # mode an empty folder must never silently turn into an online search.
        if gt.enable_youtube_mix_cb.isChecked():
            enable_youtube = gt.source_youtube_cb.isChecked()
            enable_local = gt.source_local_cb.isChecked()
            enable_pexels = gt.source_pexels_cb.isChecked()
            enable_pixabay = gt.source_pixabay_cb.isChecked()
            enable_wikimedia = gt.source_wikimedia_cb.isChecked()
            if not any((enable_youtube, enable_local, enable_pexels, enable_pixabay, enable_wikimedia)):
                _show_warning(
                    self,
                    t('video_sources_title'),
                    t('select_video_source'),
                )
                self.main_tabs.setCurrentWidget(gt)
                return

            if enable_local and not enable_youtube and not enable_pexels and not enable_pixabay and not enable_wikimedia:
                local_folder_text = gt.custom_videos_input.text().strip()
                local_folder = Path(local_folder_text) if local_folder_text else None
                video_extensions = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
                has_local_videos = (
                    local_folder is not None
                    and local_folder.is_dir()
                    and any(
                        path.is_file() and path.suffix.lower() in video_extensions
                        for path in local_folder.rglob('*')
                    )
                )
                if not has_local_videos:
                    _show_warning(
                        self,
                        t('local_video_title'),
                        t('local_video_folder_required'),
                    )
                    self.main_tabs.setCurrentWidget(gt)
                    gt.custom_videos_input.setFocus()
                    return
        
        # Check if custom text mode is enabled
        custom_text_settings = gt.get_custom_text_settings()
        custom_text_enabled = custom_text_settings.get('enabled', False)
        
        # Validate theme OR custom texts
        if custom_text_enabled:
            # Custom text mode validation
            custom_texts = custom_text_settings.get('texts')
            if not custom_texts:
                error_msg = custom_text_settings.get('error', t('custom_text_not_loaded'))
                if not error_msg or error_msg == "No texts loaded":
                    error_msg = t('custom_text_not_loaded')
                _show_warning(
                    self,
                    t('error'),
                    t('custom_text_mode_error').format(error=error_msg),
                )
                self.main_tabs.setCurrentWidget(gt)
                return
            
            # --- PRE-GENERATION VALIDATION ---
            visual_title = gt.theme_input.text().strip() if hasattr(gt, 'theme_input') else ''
            
            # Проверяем, нужно ли делать AI валидацию
            use_only_custom_images = hasattr(gt, 'use_only_custom_images_cb') and gt.use_only_custom_images_cb.isChecked()
            should_validate = visual_title and len(custom_texts) > 0 and gt.enable_ai_images_cb.isChecked() and not use_only_custom_images

            if should_validate:
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    from core.gemini_client import GeminiClient
                    client = GeminiClient(api_key=api_key)
                    text_content = custom_texts[0].text
                    
                    prompt = t('visual_match_prompt').format(
                        title=visual_title, text=text_content[:1000]
                    )
                    schema = {
                        "type": "object",
                        "properties": {
                            "match": {"type": "boolean"},
                            "reason": {"type": "string"}
                        },
                        "required": ["match", "reason"]
                    }
                    response = client.generate_json(
                        prompt,
                        custom_schema=schema,
                        models=['gemini-3.1-flash-lite', 'gemini-2.5-flash-lite', 'gemini-2.5-flash'],
                    )
                    if response.success and response.data:
                        if not response.data.get('match', True):
                            QApplication.restoreOverrideCursor()
                            reason = response.data.get('reason', '')
                            proceed = ask_yes_no(
                                self,
                                t('visual_mismatch_title'),
                                t('visual_mismatch_message').format(
                                    visual=visual_title,
                                    reason=reason,
                                ),
                            )
                            if not proceed:
                                self.main_tabs.setCurrentWidget(gt)
                                return
                except Exception as e:
                    import logging
                    logging.warning(f"Validation error: {e}")
                finally:
                    QApplication.restoreOverrideCursor()
            # ---------------------------------
            
            # Use custom texts count as num_videos
            num_videos = len(custom_texts)
            theme = t('custom_texts_theme').format(count=num_videos)
            self.add_log(t('runtime_custom_text_loaded_log').format(count=num_videos))
        else:
            # Standard mode validation
            theme = gt.get_theme()
            if not theme:
                _show_warning(self, t('error'), t('enter_generation_theme'))
                self.main_tabs.setCurrentWidget(gt)
                return
            num_videos = gt.get_num_videos()
        
        # Initialize APIKeyManager with all keys
        try:
            from core.api_key_manager import get_key_manager
            key_manager = get_key_manager()
            key_manager.set_keys(api_keys)
            self.add_log(t('runtime_api_keys_loaded_log').format(count=len(api_keys)))
        except Exception as e:
            logging.warning(f"Could not init APIKeyManager: {e}")
        
        # Auto-save settings before generation (in case of crash)
        if self.save_user_settings():
            self.config_manager.save_generation_profile()
        
        # Switch to monitor tab
        self.main_tabs.setCurrentWidget(self.monitor_tab)
        
        # Update UI state
        self.generate_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.brand_header.set_status(t("shell_creating"), t("generation_in_progress"), "busy")
        self.progress_label.setText(t("generation_in_progress"))
        self.monitor_state_value.setText(t("generation_in_progress"))
        self.monitor_progress_value.setText("0%")
        self.progress_bar.setValue(0)
        
        # Get settings from generation tab
        gt = self.generation_tab
        
        # Duration: use from settings OR auto-calculate from custom text
        if custom_text_enabled and custom_texts:
            # Use duration from first custom text as base
            duration = custom_texts[0].duration
            self.add_log(t('runtime_auto_duration_log').format(duration=duration))
        else:
            duration = self.generation_tab.get_duration_seconds()
        
        width, height = self.generation_tab.get_resolution()
        
        # Get persona_id from combo
        from gui.constants import PERSONA_MAPPING
        persona_id = PERSONA_MAPPING.get(gt.persona_combo.currentIndex(), None)
        
        # Маппинг русских названий анимаций на внутренние значения
        anim_type = gt.animation_type_combo.currentData()
        if not anim_type:
            anim_text = gt.animation_type_combo.currentText()
            anim_type = self.ANIMATION_TYPE_MAP.get(anim_text, "none")
        
        # Первый шот из пула — читаем из чекбокса (по умолчанию включён)
        first_shot_from_pool = gt.first_shot_image_cb.isChecked() if hasattr(gt, 'first_shot_image_cb') else True
        burn_first_shot_title = gt.burn_first_shot_title_cb.isChecked() if hasattr(gt, 'burn_first_shot_title_cb') else True
        first_shot_title_custom = gt.first_shot_title_input.toPlainText().strip() if hasattr(gt, 'first_shot_title_input') else ''

        video_settings = {
            'width': width,
            'height': height,
            'fps': int(gt.fps_combo.currentText().split()[0]),
            'duration': duration,
            'shot_min_duration': gt.shot_min_spin.value(),
            'shot_max_duration': gt.shot_max_spin.value(),
            'enable_animation': gt.enable_animation_cb.isChecked(),
            'animation_type': anim_type,
            'animation_speed': gt.animation_speed_spin.value(),
            'enable_transitions': gt.enable_transitions_cb.isChecked(),
            'transition_duration': gt.transition_duration_spin.value(),
            'music_volume': gt.music_volume_slider.value() / 100.0,
            'start_with_images': gt.start_with_images_cb.isChecked(),
            'first_shot_from_pool': first_shot_from_pool,  # 🖼️ Первый шот из пула
            'burn_first_shot_title': burn_first_shot_title, # 🔥 Выжигать название
            'first_shot_title_custom': first_shot_title_custom, # 📝 Пользовательское название
            # ⚡ Glitch transitions
            **gt.get_glitch_settings(),
            **gt.get_video_effect_settings(),
        }
        
        source_data = {
            'theme': theme,
            'language': self.generation_tab.get_language(),
            'persona_id': persona_id,
            'enable_seamless_loop': gt.seamless_loop_cb.isChecked(),
            'enable_comment_bait': gt.comment_bait_cb.isChecked(),
            'custom_texts': custom_texts if custom_text_enabled else None,  # 📝 Добавляем custom texts
        }
        
        audio_settings = {
            'enabled': gt.enable_tts_cb.isChecked(),
            'provider': ('edge', 'gemini')[gt.tts_provider_combo.currentIndex()],
            'voice': gt.tts_voice_combo.currentText().split()[0],
            'speech_speed': gt.speech_speed_spin.value(),
            'edge_pitch_hz': gt.edge_pitch_spin.value(),
            'edge_volume_percent': gt.edge_volume_spin.value(),
            'auto_fit_duration': True,
            'music_volume': gt.music_volume_slider.value() / 100.0,
        }
        
        subtitle_settings = normalize_subtitle_settings({
            'enabled': gt.enable_subtitles_cb.isChecked(),
            'font_size': gt.font_size_spin.value(),
            'font_path': gt.font_combo.currentText(),
            'position': gt.subtitle_position_combo.currentText(),
            **gt.get_subtitle_animation_settings(),
            'style_preset': ('tiktok', 'clean', 'hormozi', 'boxed', 'cinema', 'minimal')[gt.subtitle_style_combo.currentIndex()],
            'font_color': gt.subtitle_color_input.text(),
            'highlight_color': gt.subtitle_highlight_input.text(),
            'outline_width': gt.subtitle_outline_spin.value(),
            'bg_opacity': gt.subtitle_bg_opacity_spin.value() / 100.0,
            'max_words_per_subtitle': gt.subtitle_words_spin.value(),
            'timing_offset': gt.subtitle_timing_offset_spin.value(),
            'uppercase': gt.subtitle_uppercase_cb.isChecked(),
        })
        
        # Create worker with ALL settings
        self.generation_thread = GenerationWorker(
            source_data=source_data,
            num_videos=num_videos,
            music_path=gt.music_path_input.text() if gt.enable_music_cb.isChecked() else None,
            api_key=api_key,
            video_settings=video_settings,
            output_path=gt.output_path_input.text() or "generated",
            subtitle_settings=subtitle_settings,
            media_path=gt.overlay_path_input.text() if gt.enable_overlay_cb.isChecked() else None,
            use_ai_image_generation=gt.enable_ai_images_cb.isChecked(),
            strict_theme_following=gt.strict_images_cb.isChecked(),
            strict_text_theme=gt.strict_theme_cb.isChecked(),
            google_ai_api_key=api_key,
            overlay_settings={
                'position': (
                    gt.overlay_position_combo.currentData()
                    or gt.overlay_position_combo.currentText()
                ),
                'margin': 50,
                'fullscreen': gt.overlay_fullscreen_cb.isChecked() if hasattr(gt, 'overlay_fullscreen_cb') else False,
            },
            use_triple_template=False,
            audio_settings=audio_settings,
            enable_parallel_generation=True,
            num_workers=max(1, min(4, (os.cpu_count() or 4) // 2)),
            unlimited_images=gt.unlimited_images_cb.isChecked(),
            num_unique_images=gt.num_unique_images_spin.value(),
            enable_scene_variety=gt.scene_variety_cb.isChecked(),
            image_model={
                0: 'gemini-3.1-flash-image',          # Рекомендуемая
                1: 'gemini-3-pro-image',               # Максимальное качество
                2: 'gemini-2.5-flash-image'           # Базовая
            }.get(gt.image_model_combo.currentIndex(), 'gemini-3.1-flash-image'),
            veo3_settings=gt.get_veo3_settings(),  # Используем новый метод с полными настройками
            youtube_mixer_settings={
                'enabled': gt.enable_youtube_mix_cb.isChecked(),
                'video_ratio': gt.yt_video_ratio_spin.value() / 100.0,
                'clip_min_duration': gt.yt_clip_min_spin.value(),
                'clip_max_duration': gt.yt_clip_max_spin.value(),
                'custom_videos_folder': gt.custom_videos_input.text() or None,  # 🎬 Папка со своими видео
                'source_mode': self._derive_visual_source_mode(
                    gt.source_youtube_cb.isChecked(),
                    gt.source_local_cb.isChecked(),
                    gt.source_pexels_cb.isChecked(),
                    gt.source_wikimedia_cb.isChecked(),
                    gt.source_pixabay_cb.isChecked(),
                ),
                'enable_youtube': gt.source_youtube_cb.isChecked(),
                'enable_local_videos': gt.source_local_cb.isChecked(),
                'enable_pexels': gt.source_pexels_cb.isChecked(),
                'enable_pixabay_videos': gt.source_pixabay_cb.isChecked(),
                'enable_wikimedia_videos': gt.source_wikimedia_cb.isChecked(),
                'pexels_api_key': self.settings_tab.pexels_api_input.text().strip() or None,
                'youtube_data_api_key': self.settings_tab.youtube_data_api_input.text().strip() or None,
                'pixabay_api_key': self.settings_tab.pixabay_api_input.text().strip() or None,
            },
            use_image_cache=False,
            save_to_image_cache=False,
            custom_images_folder=gt.custom_images_input.text() or None,
            use_only_custom_images=gt.use_only_custom_images_cb.isChecked(),
            use_reference_images=gt.use_reference_images_cb.isChecked(),
            reference_images_folder=gt.reference_images_input.text() or None,
            avatar_settings={
                **gt.get_avatar_settings(),
                'api_key': self.settings_tab.get_heygen_api_key(),
            },
            final_output_settings={
                'custom_description_text': {
                    'generate_insta_files': self.custom_text_tab.insta_files_cb.isChecked(),
                },
                'separate_metadata': {
                    'enabled': self.custom_text_tab.use_separate_metadata_cb.isChecked(),
                },
            },
        )
        
        # Connect signals
        self.generation_thread.progress.connect(self._on_progress)
        self.generation_thread.log.connect(self._on_log)
        self.generation_thread.finished.connect(self._on_finished)
        self.generation_thread.new_image.connect(self._on_new_image)
        if self.youtube_publish_tab is not None:
            self.generation_thread.publish_package_ready.connect(
                self.youtube_publish_tab.handle_generated_package
            )
        
        # Start
        self.generation_thread.start()
        self.add_log(t('runtime_generation_start_log').format(count=num_videos))
        self.add_log(t('runtime_generation_topic_log').format(theme=theme))
        self.add_log(t('runtime_generation_resolution_log').format(width=width, height=height))
        self.add_log(t('runtime_generation_duration_log').format(duration=duration))
        self.add_log("─" * 40)
    
    def stop_generation(self):
        """Stop video generation gracefully"""
        if self.generation_thread:
            self.add_log(t('runtime_generation_stopping_log'))
            self.progress_label.setText(t("generation_stopping"))
            self.monitor_state_value.setText(t("generation_stopping"))
            self.stop_btn.setEnabled(False) # Предотвращаем спам кликами
            
            self.generation_thread.stop()
            
            try:
                from core.process_registry import get_process_registry
                registry = get_process_registry()
                active_count = registry.active_count()
                if active_count:
                    killed = registry.terminate_all(timeout=3.0)
                    self.add_log(t('runtime_processes_stopped_log').format(
                        active=active_count,
                        killed=killed,
                    ))
                else:
                    self.add_log(t('runtime_no_active_processes_log'))
            except Exception as stop_error:
                self.add_log(t('runtime_process_stop_failed_log').format(error=stop_error))
            
            # _on_finished вызовется автоматически по сигналу 'finished'
    
    def _on_progress(self, value):
        """Handle progress update"""
        self.progress_bar.setValue(value)
        self.monitor_progress_value.setText(f"{int(value)}%")
    
    def _on_log(self, message):
        """Handle log message"""
        self.add_log(message)

    def _on_youtube_log(self, message: str) -> None:
        """Mirror the localized uploader log without interpreting its wording."""
        self.add_log(t('runtime_youtube_log').format(message=message))

    def _on_youtube_activity(self, event: str, payload: dict) -> None:
        """Update monitor state from the locale-neutral uploader event contract."""
        details = dict(payload or {})
        title = str(details.get("title") or "").strip()
        if event == UPLOAD_EVENT_UPLOADING:
            self.monitor_state_value.setText(t('monitor_youtube_uploading'))
            self.monitor_file_value.setText(title or t('monitor_youtube_queue_video'))
            progress = details.get("progress")
            if progress is not None:
                try:
                    percent = max(0, min(100, int(progress)))
                except (TypeError, ValueError):
                    pass
                else:
                    self.monitor_progress_value.setText(f"{percent}%")
            if not (self.generation_thread and self.generation_thread.isRunning()):
                self.brand_header.set_status(t("shell_uploading"), t("shell_youtube_uploading"), "busy")
        elif event == UPLOAD_EVENT_COMPLETED:
            if title:
                self.monitor_file_value.setText(title)
            if self.youtube_publish_tab and self.youtube_publish_tab.has_pending_uploads():
                self.monitor_state_value.setText(t('monitor_youtube_continues'))
            else:
                self.monitor_state_value.setText(t('monitor_youtube_completed'))
                self.brand_header.set_status(t("shell_ready"), t("shell_workspace_ready"), "success")
        elif event == UPLOAD_EVENT_RETRY:
            if title:
                self.monitor_file_value.setText(title)
            self.monitor_state_value.setText(t('monitor_youtube_retry'))
        elif event == UPLOAD_EVENT_FAILED:
            if title:
                self.monitor_file_value.setText(title)
            self.monitor_state_value.setText(t('generation_failed'))
            if not (self.generation_thread and self.generation_thread.isRunning()):
                self.brand_header.set_status(t("shell_error"), t("generation_failed"), "danger")
    
    def _on_finished(self):
        """Handle generation finished"""
        close_after_finish = getattr(self, '_close_when_generation_stops', False)
        self.generate_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.brand_header.set_status(t("shell_ready"), t("shell_workspace_ready"), "success")
        
        is_stopped = self.generation_thread and self.generation_thread.stop_flag
        error_message = getattr(self.generation_thread, 'error_message', None) if self.generation_thread else None
        
        if is_stopped:
            self.progress_label.setText(t("generation_stopped"))
            self.monitor_state_value.setText(t("generation_stopped"))
            self.add_log("─" * 40)
            self.add_log(t('runtime_generation_cancelled_log'))
        elif error_message:
            self.progress_label.setText(t('generation_failed'))
            self.monitor_state_value.setText(t('generation_failed'))
            self.brand_header.set_status(t("shell_error"), t("generation_failed"), "danger")
            self.add_log("─" * 40)
            self.add_log(t('runtime_generation_failed_log').format(error=error_message))
        else:
            self.progress_label.setText(t("ready_to_generate"))
            self.monitor_state_value.setText(t("generation_completed"))
            self.monitor_progress_value.setText("100%")
            self.progress_bar.setValue(100)
            self.add_log("─" * 40)
            self.add_log(t('runtime_generation_completed_log'))
            if self.youtube_publish_tab and self.youtube_publish_tab.has_pending_uploads():
                pending = self.youtube_publish_tab.pending_upload_count()
                self.monitor_state_value.setText(t('generation_completed_with_youtube').format(count=pending))
                self.brand_header.set_status(t("shell_uploading"), t("shell_youtube_queue_active"), "busy")
                self.add_log(t('runtime_youtube_pending_log').format(count=pending))
            
            # Update stats — учитываем реальное количество видео
            num_generated = getattr(self.generation_thread, 'num_videos', 1) if self.generation_thread else 1
            self.videos_generated += num_generated
            self.videos_label.setText(t("videos_generated").format(count=self.videos_generated))
        
        # Cleanup - wait for thread to fully finish before deleting
        if self.generation_thread:
            # Wait for thread to finish (max 5 seconds)
            if self.generation_thread.isRunning():
                self.generation_thread.wait(5000)
            self.generation_thread.blockSignals(True)
            self.generation_thread.deleteLater()
            self.generation_thread = None
        if close_after_finish:
            self._close_when_generation_stops = False
            QTimer.singleShot(0, self.close)
    
    def _on_new_image(self, image_path):
        """Handle new image - show in preview"""
        try:
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.preview_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.preview_label.setPixmap(scaled)
                filename = Path(image_path).name
                self.preview_stats.setText(filename)
                self.monitor_file_value.setText(filename)
        except Exception as e:
            logging.debug(f"Preview error: {e}")
    
    def add_log(self, message):
        """Add message to log"""
        self.log_text.append(message)
        max_blocks = 3000
        doc = self.log_text.document()
        if doc.blockCount() > max_blocks:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.Start)
            for _ in range(doc.blockCount() - max_blocks):
                cursor.select(QTextCursor.BlockUnderCursor)
                cursor.removeSelectedText()
                cursor.deleteChar()
        # Auto-scroll to bottom
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _clear_logs(self):
        """Clear log text"""
        should_clear = ask_yes_no(
            self,
            t("monitor_clear"),
            t("monitor_clear_confirm"),
        )
        if should_clear:
            self.log_text.clear()
    
    def _export_logs(self):
        """Export logs to file"""
        from PyQt5.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getSaveFileName(
            self, t("monitor_export"), "logs.txt", t("text_files_filter")
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(self.log_text.toPlainText())
                show_information(
                    self,
                    t("monitor_export"),
                    t("monitor_exported").format(path=file_path),
                )
            except Exception as e:
                _show_warning(
                    self,
                    t("error"),
                    t("monitor_export_failed").format(error=e),
                )
    
    # === Settings ===
    
    def load_settings(self):
        """Load ALL settings from config"""
        try:
            import json
            if not self.config_file.exists():
                return
            
            # Stop auto-save timer during loading
            if self._save_timer:
                self._save_timer.stop()
            
            # Set flag to prevent auto-save during loading
            self._loading_settings = True
                
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            user_settings = config.get('user_settings', {})
            def _as_int(value, default=0):
                try:
                    return int(float(value))
                except (TypeError, ValueError):
                    return default

            def _as_float(value, default=0.0):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return default

            def _as_percent_slider(value, default=25):
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    numeric = float(default)
                if numeric <= 1.0:
                    numeric *= 100.0
                return max(0, min(100, int(round(numeric))))
            
            gt = self.generation_tab  # shortcut
            st = self.settings_tab
            
            try:
                # === UI Language ===
                ui_lang = user_settings.get('ui_language', 'Russian')
                from gui.translations import UI_LANGUAGES, set_ui_language
                set_ui_language(ui_lang)
                if ui_lang in UI_LANGUAGES:
                    st.ui_language_combo.setCurrentText(UI_LANGUAGES[ui_lang])
                
                # === API Keys (Settings Tab) - support for multiple keys ===
                gemini_keys = user_settings.get('gemini_api_keys', [])
                if not gemini_keys:
                    # Fallback to single key
                    single_key = user_settings.get('gemini_api_key', '')
                    if single_key:
                        gemini_keys = [single_key]
                st.set_gemini_api_keys(gemini_keys)
                
                st.pixabay_api_input.setText(user_settings.get('pixabay_api_key', ''))
                st.pexels_api_input.setText(user_settings.get('pexels_api_key', ''))
                st.youtube_data_api_input.setText(user_settings.get('youtube_data_api_key', ''))
                if hasattr(st, 'heygen_api_input'):
                    st.heygen_api_input.setText(user_settings.get('heygen_api_key', ''))
                st.freesound_api_input.setText(user_settings.get('freesound_api_key', ''))
                

                
                # === Theme Tab ===
                gt.theme_input.setText(user_settings.get('theme', ''))
                
                # Language - use setCurrentText for exact match
                language = user_settings.get('language', '🇷🇺 Русский')
                idx = gt.language_combo.findText(language)
                if idx >= 0:
                    gt.language_combo.setCurrentIndex(idx)
                
                # Persona
                gt.persona_combo.setCurrentIndex(_as_int(user_settings.get('persona_index', 0), 0))
                
                # Viral options
                gt.strict_theme_cb.setChecked(user_settings.get('strict_text_theme', True))
                gt.seamless_loop_cb.setChecked(user_settings.get('seamless_loop', True))
                gt.comment_bait_cb.setChecked(user_settings.get('comment_bait', False))
                
                # === Video Tab ===
                gt.num_videos_spin.setValue(_as_int(user_settings.get('num_videos', 5), 5))
                
                # Resolution - use findText for exact match
                video_settings = user_settings.get('video_settings', {})
                resolution = video_settings.get('resolution', '').replace('x', '×')
                resolution_loaded = False
                for i in range(gt.resolution_combo.count()):
                    if resolution in gt.resolution_combo.itemText(i):
                        gt.resolution_combo.setCurrentIndex(i)
                        resolution_loaded = True
                        break
                if not resolution_loaded:
                    width = _as_int(video_settings.get('width', 1080), 1080)
                    height = _as_int(video_settings.get('height', 1920), 1920)
                    for i in range(gt.resolution_combo.count()):
                        text = gt.resolution_combo.itemText(i).replace('×', 'x')
                        if f"{width}x{height}" in text:
                            gt.resolution_combo.setCurrentIndex(i)
                            break
                
                # FPS
                fps = video_settings.get('fps', 30)
                for i in range(gt.fps_combo.count()):
                    if str(fps) in gt.fps_combo.itemText(i):
                        gt.fps_combo.setCurrentIndex(i)
                        break
                
                # Duration
                duration = _as_int(video_settings.get('duration', 30), 30)
                gt.hours_spin.setValue(duration // 3600)
                gt.minutes_spin.setValue((duration % 3600) // 60)
                gt.seconds_spin.setValue(duration % 60)
                
                # Animation
                gt.enable_animation_cb.setChecked(video_settings.get('enable_animation', False))
                gt.animation_speed_spin.setValue(_as_int(video_settings.get('animation_speed', 50), 50))
                
                # Animation type
                anim_type = video_settings.get('animation_type', '')
                animation_index = {
                    'mix': 0,
                    'pan_left': 1,
                    'pan_right': 2,
                    'zoom_center': 3,
                }.get(anim_type, 0)
                gt.animation_type_combo.setCurrentIndex(animation_index)
                
                # Transitions
                gt.enable_transitions_cb.setChecked(video_settings.get('enable_transitions', True))
                gt.transition_duration_spin.setValue(_as_float(video_settings.get('transition_duration', 0.3), 0.3))

                # ⚡ Glitch transitions
                if hasattr(gt, 'enable_glitch_cb'):
                    glitch_on = video_settings.get('glitch_enabled', False)
                    gt.enable_glitch_cb.setChecked(glitch_on)
                    if hasattr(gt, '_glitch_settings_widget'):
                        gt._glitch_settings_widget.setVisible(glitch_on)
                if hasattr(gt, 'glitch_style_combo'):
                    glitch_style = video_settings.get('glitch_style', 'random')
                    for i in range(gt.glitch_style_combo.count()):
                        if gt.glitch_style_combo.itemData(i) == glitch_style:
                            gt.glitch_style_combo.setCurrentIndex(i)
                            break
                if hasattr(gt, 'glitch_duration_spin'):
                    gt.glitch_duration_spin.setValue(_as_float(video_settings.get('glitch_duration', 0.4), 0.4))
                if hasattr(gt, 'glitch_frequency_spin'):
                    gt.glitch_frequency_spin.setValue(int(video_settings.get('glitch_frequency', 0.5) * 100))
                if hasattr(gt, 'glitch_intensity_spin'):
                    gt.glitch_intensity_spin.setValue(int(video_settings.get('glitch_intensity', 0.65) * 100))

                effect = video_settings.get('video_effect', 'auto')
                effect_index = gt.video_effect_combo.findData(effect)
                gt.video_effect_combo.setCurrentIndex(effect_index if effect_index >= 0 else 1)
                gt.video_effect_intensity_spin.setValue(
                    _as_int(_as_float(video_settings.get('video_effect_intensity', 0.30), 0.30) * 100, 30)
                )
                gt.video_effect_probability_spin.setValue(
                    _as_int(_as_float(video_settings.get('video_effect_probability', 0.40), 0.40) * 100, 40)
                )
                gt._on_video_effect_changed()
                
                # Shot duration
                gt.shot_min_spin.setValue(_as_float(video_settings.get('shot_min_duration', 1.5), 1.5))
                gt.shot_max_spin.setValue(_as_float(video_settings.get('shot_max_duration', 4), 4.0))
                gt.start_with_images_cb.setChecked(video_settings.get('start_with_images', True))
                
                # === Audio Tab ===
                audio_settings = user_settings.get('audio_settings', {})
                gt.enable_tts_cb.setChecked(audio_settings.get('enabled', True))
                
                # Music checkbox
                gt.enable_music_cb.setChecked(audio_settings.get('use_music', True))
                
                # TTS Provider
                provider = audio_settings.get('provider', 'edge')
                gt.tts_provider_combo.setCurrentIndex({'edge': 0, 'gemini': 1}.get(provider, 0))
                
                # Voice
                voice = audio_settings.get('voice', 'Kore')
                for i in range(gt.tts_voice_combo.count()):
                    if voice.split()[0] in gt.tts_voice_combo.itemText(i):
                        gt.tts_voice_combo.setCurrentIndex(i)
                        break
                
                # Speed
                gt.speech_speed_spin.setValue(_as_float(audio_settings.get('speech_speed', 1.0), 1.0))
                gt.edge_pitch_spin.setValue(_as_int(audio_settings.get('edge_pitch_hz', 0), 0))
                gt.edge_volume_spin.setValue(_as_int(audio_settings.get('edge_volume_percent', 0), 0))
                gt.auto_fit_tts_cb.setChecked(True)
                
                # Music volume
                music_vol = _as_percent_slider(
                    audio_settings.get(
                        'music_volume',
                        video_settings.get('music_volume', user_settings.get('music_volume', 25)),
                    ),
                    25,
                )
                gt.music_volume_slider.setValue(music_vol)
                
                # Music path in generation tab (sync with settings tab)
                gt.music_path_input.setText(user_settings.get('music_path', ''))
                
                # === Visual Tab ===
                gt.enable_ai_images_cb.setChecked(user_settings.get('use_ai_image_generation', True))
                gt.image_model_combo.setCurrentIndex(_as_int(user_settings.get('image_model', 0), 0))
                gt.strict_images_cb.setChecked(user_settings.get('strict_theme_following', True))
                gt.scene_variety_cb.setChecked(user_settings.get('scene_variety', True))
                gt.num_unique_images_spin.setValue(_as_int(user_settings.get('num_unique_images', 15), 15))
                gt.unlimited_images_cb.setChecked(user_settings.get('unlimited_images', False))
                gt.custom_images_input.setText(user_settings.get('custom_images_folder', ''))
                gt.use_only_custom_images_cb.setChecked(user_settings.get('use_only_custom_images', False))

                # Первый шот из пула
                first_shot_enabled = user_settings.get('first_shot_image_enabled', False)
                if hasattr(gt, 'first_shot_image_cb'):
                    gt.first_shot_image_cb.setChecked(first_shot_enabled)
                if hasattr(gt, 'burn_first_shot_title_cb'):
                    gt.burn_first_shot_title_cb.setChecked(bool(first_shot_enabled and user_settings.get('burn_first_shot_title', True)))
                    gt.burn_first_shot_title_cb.setEnabled(bool(first_shot_enabled))
                if hasattr(gt, 'first_shot_title_input'):
                    gt.first_shot_title_input.setPlainText(user_settings.get('first_shot_title_custom', ''))
                    if hasattr(gt, '_update_first_shot_title_state'):
                        gt._update_first_shot_title_state()

                gt.use_reference_images_cb.setChecked(user_settings.get('use_reference_images', False))
                gt.reference_images_input.setText(user_settings.get('reference_images_folder', 'assets/references'))
                
                # Image cache
                gt.use_image_cache_cb.setChecked(False)
                gt.save_to_cache_cb.setChecked(False)
                
                # Subtitles
                subtitle_settings = normalize_subtitle_settings(
                    user_settings.get('subtitle_settings', {})
                )
                gt.enable_subtitles_cb.setChecked(subtitle_settings.get('enabled', True))
                gt.font_size_spin.setValue(_as_int(subtitle_settings.get('font_size', 48), 48))
                subtitle_animation = subtitle_settings.get('subtitle_animation', 'auto')
                gt.animated_subtitles_cb.setChecked(subtitle_animation != 'none')
                animation_index = gt.subtitle_animation_combo.findData(subtitle_animation)
                gt.subtitle_animation_combo.setCurrentIndex(
                    animation_index if animation_index >= 0 else 0
                )
                gt.subtitle_fade_spin.setValue(
                    _as_int(subtitle_settings.get('subtitle_fade_out_ms', 220), 220)
                )
                gt.subtitle_typewriter_speed_spin.setValue(
                    _as_int(subtitle_settings.get('subtitle_typewriter_cps', 18), 18)
                )
                gt._on_subtitle_animation_changed()
                gt.subtitle_style_combo.setCurrentIndex(
                    {'tiktok': 0, 'clean': 1, 'hormozi': 2, 'boxed': 3, 'cinema': 4, 'minimal': 5}.get(
                        subtitle_settings.get('style_preset', 'tiktok'), 0
                    )
                )
                gt.subtitle_color_input.setText(subtitle_settings.get('font_color', '#FFFFFF'))
                gt.subtitle_highlight_input.setText(subtitle_settings.get('highlight_color', '#FFD43B'))
                gt.subtitle_outline_spin.setValue(_as_int(subtitle_settings.get('outline_width', 3), 3))
                gt.subtitle_bg_opacity_spin.setValue(_as_int(_as_float(subtitle_settings.get('bg_opacity', 0.0), 0.0) * 100, 0))
                gt.subtitle_words_spin.setValue(_as_int(subtitle_settings.get('max_words_per_subtitle', 3), 3))
                gt.subtitle_timing_offset_spin.setValue(_as_float(subtitle_settings.get('timing_offset', 0.0), 0.0))
                gt.subtitle_uppercase_cb.setChecked(subtitle_settings.get('uppercase', False))
                
                # Font
                font_path = subtitle_settings.get('font_path', 'Arial')
                for i in range(gt.font_combo.count()):
                    if font_path in gt.font_combo.itemText(i):
                        gt.font_combo.setCurrentIndex(i)
                        break
                
                # Subtitle position
                sub_pos = normalize_subtitle_position(subtitle_settings.get('position', ''), 'bottom')
                for i in range(gt.subtitle_position_combo.count()):
                    if normalize_subtitle_position(gt.subtitle_position_combo.itemText(i), 'bottom') == sub_pos:
                        gt.subtitle_position_combo.setCurrentIndex(i)
                        break
                
                # Overlay
                overlay_settings = user_settings.get('overlay_settings', {})
                gt.enable_overlay_cb.setChecked(overlay_settings.get('enabled', True))
                gt.overlay_path_input.setText(user_settings.get('media_path', ''))
                if hasattr(gt, 'overlay_fullscreen_cb'):
                    gt.overlay_fullscreen_cb.setChecked(overlay_settings.get('fullscreen', False))
                
                # Overlay position
                overlay_pos = overlay_settings.get('position', '')
                overlay_index = gt.overlay_position_combo.findData(overlay_pos)
                if overlay_index < 0:
                    # Compatibility with configs saved before positions used
                    # stable IDs and therefore contained a localized label.
                    for i in range(gt.overlay_position_combo.count()):
                        if overlay_pos in gt.overlay_position_combo.itemText(i):
                            overlay_index = i
                            break
                if overlay_index >= 0:
                    gt.overlay_position_combo.setCurrentIndex(overlay_index)
                
                # === Advanced Tab ===
                gt.enable_parallel_cb.setChecked(user_settings.get('enable_parallel_generation', True))
                gt.num_workers_spin.setValue(_as_int(user_settings.get('num_workers', 4), 4))
                
                # Veo3 settings - загружаем полные настройки
                # Avatar settings
                avatar_settings = user_settings.get('avatar_settings', {})
                if hasattr(gt, 'enable_avatar_cb'):
                    gt.enable_avatar_cb.setChecked(avatar_settings.get('enabled', False))
                    gt.avatar_id_input.setText(avatar_settings.get('avatar_id', ''))
                    if hasattr(gt, 'avatar_voice_id_input'):
                        gt.avatar_voice_id_input.setText(avatar_settings.get('voice_id', ''))
                    gt.avatar_start_cb.setChecked(avatar_settings.get('start_hook', True))
                    gt.avatar_end_cb.setChecked(avatar_settings.get('end_call', False))
                    if hasattr(gt, 'avatar_random_cb'):
                        gt.avatar_random_cb.setChecked(avatar_settings.get('random_spots', False))
                    style = avatar_settings.get('style', '')
                    if style:
                        idx = gt.avatar_style_combo.findText(style)
                        if idx >= 0: gt.avatar_style_combo.setCurrentIndex(idx)
                    bg = avatar_settings.get('background', '')
                    if bg:
                        idx = gt.avatar_bg_combo.findText(bg)
                        if idx >= 0: gt.avatar_bg_combo.setCurrentIndex(idx)
                    gt.avatar_bg_path.setText(avatar_settings.get('custom_bg_path', '') or '')

                veo3_settings = user_settings.get('veo3_settings', {})
                gt.enable_veo3_cb.setChecked(veo3_settings.get('enabled', False))
                if hasattr(gt, 'veo3_duration_combo'):
                    duration = int(veo3_settings.get('intro_duration', veo3_settings.get('duration', 8)))
                    gt.veo3_duration_combo.setCurrentIndex({8: 0, 16: 1, 24: 2, 32: 3}.get(duration, 0))
                if hasattr(gt, 'veo3_resolution_combo'):
                    resolution = veo3_settings.get('resolution', '1080p')
                    res_idx = 0 if resolution == '720p' else 1
                    gt.veo3_resolution_combo.setCurrentIndex(res_idx)
                if hasattr(gt, 'veo3_aspect_combo'):
                    aspect = veo3_settings.get('aspect_ratio', '9:16')
                    aspect_idx = 0 if aspect == '9:16' else 1
                    gt.veo3_aspect_combo.setCurrentIndex(aspect_idx)
                if hasattr(gt, 'veo3_model_combo'):
                    model = veo3_settings.get('model', 'veo-3.1-fast-generate-preview')
                    for index in range(gt.veo3_model_combo.count()):
                        if gt.veo3_model_combo.itemData(index) == model:
                            gt.veo3_model_combo.setCurrentIndex(index)
                            break
                if hasattr(gt, 'veo3_style_combo'):
                    gt.veo3_style_combo.setCurrentIndex(int(veo3_settings.get('style', 0)))
                for attr, key, default in (
                    ('veo3_content_mode_combo', 'content_mode', 'intro'),
                    ('veo3_prompt_source_combo', 'prompt_source', 'auto'),
                    ('veo3_motion_combo', 'motion_intensity', 'balanced'),
                    ('veo3_camera_combo', 'camera_style', 'auto'),
                    ('veo3_placement_combo', 'placement', 'replace_start'),
                    ('veo3_audio_policy_combo', 'audio_policy', 'strip'),
                    ('veo3_realism_combo', 'realism', 'auto'),
                ):
                    combo = getattr(gt, attr, None)
                    if combo is not None:
                        value = veo3_settings.get(key, default)
                        for index in range(combo.count()):
                            if combo.itemData(index) == value:
                                combo.setCurrentIndex(index)
                                break
                if hasattr(gt, 'veo3_custom_prompt_edit'):
                    gt.veo3_custom_prompt_edit.setPlainText(veo3_settings.get('custom_prompt', '') or '')
                    gt._update_veo_prompt_controls()
                if hasattr(gt, 'veo3_negative_prompt_edit'):
                    gt.veo3_negative_prompt_edit.setPlainText(veo3_settings.get('negative_prompt', '') or '')
                if hasattr(gt, 'veo3_no_text_cb'):
                    gt.veo3_no_text_cb.setChecked(veo3_settings.get('avoid_text', True))
                if hasattr(gt, 'veo3_multishot_cb'):
                    gt.veo3_multishot_cb.setChecked(veo3_settings.get('use_multishot', True))
                if hasattr(gt, 'veo3_actions_spin'):
                    gt.veo3_actions_spin.setValue(int(veo3_settings.get('num_actions', 4)))
                if hasattr(gt, 'veo3_insert_count_spin'):
                    gt.veo3_insert_count_spin.setValue(int(veo3_settings.get('insert_count', 1)))
                if hasattr(gt, 'veo3_insert_position_spin'):
                    gt.veo3_insert_position_spin.setValue(int(veo3_settings.get('insert_position_percent', 35)))
                if hasattr(gt, 'veo3_blend_spin'):
                    gt.veo3_blend_spin.setValue(float(veo3_settings.get('blend_seconds', 0.4)))
                if hasattr(gt, 'veo3_reference_frames_spin'):
                    gt.veo3_reference_frames_spin.setValue(int(veo3_settings.get('reference_frames', 1)))
                if hasattr(gt, 'veo3_seed_spin'):
                    gt.veo3_seed_spin.setValue(int(veo3_settings.get('seed') or 0))
                
                gt.triple_template_cb.setChecked(False)
                
                # YouTube Mixer settings
                yt_settings = user_settings.get('youtube_mixer_settings', {})
                gt.enable_youtube_mix_cb.setChecked(yt_settings.get('enabled', False))
                gt.yt_video_ratio_spin.setValue(_as_int(_as_float(yt_settings.get('video_ratio', 0.3), 0.3) * 100, 30))
                gt.yt_clip_min_spin.setValue(_as_int(yt_settings.get('clip_min_duration', 3), 3))
                gt.yt_clip_max_spin.setValue(_as_int(yt_settings.get('clip_max_duration', 8), 8))
                gt.custom_videos_input.setText(yt_settings.get('custom_videos_folder', '') or '')
                gt.source_youtube_cb.setChecked(yt_settings.get('enable_youtube', True))
                gt.source_local_cb.setChecked(yt_settings.get('enable_local_videos', True))
                gt.source_pexels_cb.setChecked(yt_settings.get('enable_pexels', False))
                gt.source_wikimedia_cb.setChecked(yt_settings.get('enable_wikimedia_videos', False))
                gt.source_pixabay_cb.setChecked(yt_settings.get('enable_pixabay_videos', False))
                
                # Custom texts settings
                custom_texts_settings = user_settings.get('custom_texts_settings', {})
                if custom_texts_settings:
                    gt.use_custom_texts_cb.setChecked(custom_texts_settings.get('enabled', False))
                    folder_path = custom_texts_settings.get('folder', '')
                    gt.custom_texts_folder_input.setText(folder_path or '')
                    
                    # 🔧 АВТОЗАГРУЗКА: Загружаем тексты если путь указан
                    if folder_path and Path(folder_path).exists():
                        try:
                            gt._load_custom_texts(Path(folder_path))
                        except Exception as e:
                            logging.warning(f"Failed to auto-load custom texts: {e}")
                
                # Image pool settings
                pool_settings = user_settings.get('image_pool_settings', {})
                gt.use_image_pool_cb.setChecked(pool_settings.get('use_image_pool', False))
                gt.pool_size_spin.setValue(_as_int(pool_settings.get('pool_size', 50), 50))
                pool_path = pool_settings.get('pool_path')
                if pool_path:
                    pool_index = gt.pool_combo.findData(pool_path)
                    if pool_index >= 0:
                        gt.pool_combo.setCurrentIndex(pool_index)
                
                # Custom description text (custom_text_tab)
                custom_desc_settings = user_settings.get('custom_description_text', {})
                if custom_desc_settings:
                    self.custom_text_tab.enable_custom_text_cb.setChecked(custom_desc_settings.get('enabled', False))
                    position = custom_desc_settings.get('position', 'end')
                    position_map = {'start': 0, 'between': 1, 'split': 2, 'end': 3}
                    self.custom_text_tab.position_combo.setCurrentIndex(position_map.get(position, 3))
                    self.custom_text_tab.custom_text_edit.setPlainText(custom_desc_settings.get('text', ''))
                    self.custom_text_tab.ai_customize_cb.setChecked(custom_desc_settings.get('ai_customize', False))
                    self.custom_text_tab.limit_2000_cb.setChecked(custom_desc_settings.get('limit_2000', False))
                    self.custom_text_tab.insta_files_cb.setChecked(custom_desc_settings.get('generate_insta_files', False))
                
                # Separate metadata files (custom_text_tab)
                separate_metadata = user_settings.get('separate_metadata', {})
                if separate_metadata:
                    self.custom_text_tab.use_separate_metadata_cb.setChecked(separate_metadata.get('enabled', False))
                
                # Output path in generation tab (sync with settings tab)
                gt.output_path_input.setText(user_settings.get('output_path', 'generated'))

                if self.static_video_tab is not None:
                    self.static_video_tab.apply_saved_state(
                        user_settings.get('static_video_settings', {})
                    )

                if self.youtube_publish_tab is not None:
                    self.youtube_publish_tab.apply_saved_state(
                        user_settings.get('youtube_publish_settings', {})
                    )
                if self.channel_analytics_tab is not None:
                    analytics_state = dict(user_settings.get('channel_analytics_settings', {}) or {})
                    if not analytics_state.get('client_secrets_path'):
                        analytics_state['client_secrets_path'] = str(
                            (user_settings.get('youtube_publish_settings', {}) or {}).get(
                                'client_secrets_path', ''
                            ) or ''
                        )
                    self.channel_analytics_tab.apply_saved_state(
                        analytics_state
                    )
                
                # === Window geometry ===
                sizes = get_sizes()
                geom = user_settings.get('window_geometry', {})
                if geom:
                    self.setGeometry(
                        geom.get('x', 100),
                        geom.get('y', 50),
                        geom.get('width', sizes.WINDOW_DEFAULT_WIDTH),
                        geom.get('height', sizes.WINDOW_DEFAULT_HEIGHT)
                    )
                
                # === App preferences ===
                app_prefs = user_settings.get('app_preferences', {})
                st.auto_save_cb.setChecked(app_prefs.get('auto_save', True))
                st.remember_window_cb.setChecked(app_prefs.get('remember_window', True))
                st.motion_cb.setChecked(app_prefs.get('smooth_interface_motion', True))
                set_motion_enabled(st.motion_cb.isChecked())
                
            finally:
                # Re-enable auto-save after loading
                self._loading_settings = False
            
            self._settings_loaded_successfully = True
            self.add_log(t('runtime_settings_loaded_log'))
            QTimer.singleShot(0, self._report_recoverable_batch)
            
        except Exception as e:
            self._loading_settings = False
            logging.exception(f"Error loading settings: {e}")

    def _report_recoverable_batch(self):
        """Surface crash recovery without unexpectedly starting paid API work."""
        try:
            from core.recovery_manager import find_recoverable_batch

            recovery = find_recoverable_batch(self.generation_tab.output_path_input.text())
            if not recovery:
                return
            self.add_log(t('runtime_recovery_found_log').format(
                completed=recovery['completed'],
                total=recovery['total'],
                remaining=recovery['remaining'],
            ))
            self.statusBar().showMessage(
                t('runtime_recovery_status').format(
                    completed=recovery['completed'],
                    total=recovery['total'],
                ),
                10000,
            )
        except Exception as error:
            logging.debug(f"Recovery discovery failed: {error}")
    
    def save_user_settings(self):
        """Save ALL settings to config"""
        if self._loading_settings:
            return False
        if self.config_file.exists() and not self._settings_loaded_successfully:
            logging.error("Refusing to save GUI defaults because settings did not load successfully")
            return False
        try:
            import json
            
            gt = self.generation_tab
            st = self.settings_tab
            

            
            # Get resolution
            width, height = gt.get_resolution()
            
            config = {
                'user_settings': {
                    # UI Language
                    'ui_language': get_language_code(st.ui_language_combo.currentText()) if hasattr(st, 'ui_language_combo') else 'Russian',
                    
                    # API Keys - support for multiple keys
                    'gemini_api_keys': st.get_gemini_api_keys() if hasattr(st, 'get_gemini_api_keys') else [st.gemini_api_input.toPlainText().strip()],
                    'gemini_api_key': st.get_gemini_api_keys()[0] if hasattr(st, 'get_gemini_api_keys') and st.get_gemini_api_keys() else st.gemini_api_input.toPlainText().strip(),
                    'google_ai_api_key': st.get_gemini_api_keys()[0] if hasattr(st, 'get_gemini_api_keys') and st.get_gemini_api_keys() else st.gemini_api_input.toPlainText().strip(),
                    'heygen_api_key': st.get_heygen_api_key() if hasattr(st, 'get_heygen_api_key') else '',
                    'pixabay_api_key': st.pixabay_api_input.text().strip(),
                    'pexels_api_key': st.pexels_api_input.text().strip(),
                    'youtube_data_api_key': st.youtube_data_api_input.text().strip(),
                    'freesound_api_key': st.freesound_api_input.text().strip(),
                    
                    # Paths
                    'output_path': gt.output_path_input.text(),
                    'music_path': gt.music_path_input.text(),
                    'media_path': gt.overlay_path_input.text(),
                    
                    # Theme
                    'theme': gt.theme_input.text(),
                    'language': gt.language_combo.currentText(),
                    'persona_index': gt.persona_combo.currentIndex(),
                    
                    # Viral
                    'strict_text_theme': gt.strict_theme_cb.isChecked(),
                    'seamless_loop': gt.seamless_loop_cb.isChecked(),
                    'comment_bait': gt.comment_bait_cb.isChecked(),
                    
                    # Video
                    'num_videos': gt.num_videos_spin.value(),
                    'music_volume': gt.music_volume_slider.value(),
                    
                    # AI Images
                    'use_ai_image_generation': gt.enable_ai_images_cb.isChecked(),
                    'image_model': gt.image_model_combo.currentIndex(),
                    'strict_theme_following': gt.strict_images_cb.isChecked(),
                    'scene_variety': gt.scene_variety_cb.isChecked(),
                    'num_unique_images': gt.num_unique_images_spin.value(),
                    'unlimited_images': gt.unlimited_images_cb.isChecked(),
                    'custom_images_folder': gt.custom_images_input.text(),
                    'use_only_custom_images': gt.use_only_custom_images_cb.isChecked(),
                    'first_shot_image_enabled': gt.first_shot_image_cb.isChecked() if hasattr(gt, 'first_shot_image_cb') else False,
                    'burn_first_shot_title': gt.burn_first_shot_title_cb.isChecked() if hasattr(gt, 'burn_first_shot_title_cb') else True,
                    'first_shot_title_custom': gt.first_shot_title_input.toPlainText() if hasattr(gt, 'first_shot_title_input') else '',
                    'use_reference_images': gt.use_reference_images_cb.isChecked(),
                    'reference_images_folder': gt.reference_images_input.text(),
                    'use_image_cache': False,
                    'save_to_image_cache': False,
                    'use_triple_template': False,
                    
                    # Parallel
                    'enable_parallel_generation': gt.enable_parallel_cb.isChecked(),
                    'num_workers': gt.num_workers_spin.value(),
                    
                    # Video settings
                    'video_settings': {
                        'width': width,
                        'height': height,
                        'resolution': gt.resolution_combo.currentText().replace('×', 'x'),
                        'fps': int(gt.fps_combo.currentText().split()[0]),
                        'duration': gt.get_duration_seconds(),
                        'shot_min_duration': gt.shot_min_spin.value(),
                        'shot_max_duration': gt.shot_max_spin.value(),
                        'enable_animation': gt.enable_animation_cb.isChecked(),
                        # The visible label is localized.  Persist the stable
                        # item data so changing the UI language cannot turn a
                        # valid animation into ``none``.
                        'animation_type': (
                            gt.animation_type_combo.currentData()
                            or self.ANIMATION_TYPE_MAP.get(
                                gt.animation_type_combo.currentText(), "none"
                            )
                        ),
                        'animation_speed': gt.animation_speed_spin.value(),
                        'enable_transitions': gt.enable_transitions_cb.isChecked(),
                        'transition_duration': gt.transition_duration_spin.value(),
                        'music_volume': gt.music_volume_slider.value() / 100.0,
                        'start_with_images': gt.start_with_images_cb.isChecked(),
                        'first_shot_from_pool': gt.first_shot_image_cb.isChecked() if hasattr(gt, 'first_shot_image_cb') else False,
                        'burn_first_shot_title': gt.burn_first_shot_title_cb.isChecked() if hasattr(gt, 'burn_first_shot_title_cb') else True,
                        'first_shot_title_custom': gt.first_shot_title_input.toPlainText() if hasattr(gt, 'first_shot_title_input') else '',
                        **(gt.get_glitch_settings() if hasattr(gt, 'get_glitch_settings') else {}),
                        **gt.get_video_effect_settings(),
                    },
                    
                    # Subtitle settings
                    'subtitle_settings': normalize_subtitle_settings({
                        'enabled': gt.enable_subtitles_cb.isChecked(),
                        'font_size': gt.font_size_spin.value(),
                        'font_path': gt.font_combo.currentText(),
                        'position': gt.subtitle_position_combo.currentText(),
                        **gt.get_subtitle_animation_settings(),
                        'style_preset': ('tiktok', 'clean', 'hormozi', 'boxed', 'cinema', 'minimal')[gt.subtitle_style_combo.currentIndex()],
                        'font_color': gt.subtitle_color_input.text(),
                        'highlight_color': gt.subtitle_highlight_input.text(),
                        'outline_width': gt.subtitle_outline_spin.value(),
                        'bg_opacity': gt.subtitle_bg_opacity_spin.value() / 100.0,
                        'max_words_per_subtitle': gt.subtitle_words_spin.value(),
                        'timing_offset': gt.subtitle_timing_offset_spin.value(),
                        'uppercase': gt.subtitle_uppercase_cb.isChecked(),
                    }),
                    
                    # Audio settings
                    'audio_settings': {
                        'enabled': gt.enable_tts_cb.isChecked(),
                        'provider': ('edge', 'gemini')[gt.tts_provider_combo.currentIndex()],
                        'voice': gt.tts_voice_combo.currentText(),
                        'speech_speed': gt.speech_speed_spin.value(),
                        'edge_pitch_hz': gt.edge_pitch_spin.value(),
                        'edge_volume_percent': gt.edge_volume_spin.value(),
                        'auto_fit_duration': True,
                        'music_volume': gt.music_volume_slider.value() / 100.0,
                        'use_music': gt.enable_music_cb.isChecked(),
                    },
                    
                    # Overlay settings
                    'overlay_settings': {
                        'enabled': gt.enable_overlay_cb.isChecked(),
                        'position': (
                            gt.overlay_position_combo.currentData()
                            or gt.overlay_position_combo.currentText()
                        ),
                        'margin': 50,
                        'fullscreen': gt.overlay_fullscreen_cb.isChecked() if hasattr(gt, 'overlay_fullscreen_cb') else False,
                    },
                    
                    # Veo3 settings - полные настройки
                    'avatar_settings': gt.get_avatar_settings() if hasattr(gt, 'get_avatar_settings') else None,
                    'veo3_settings': gt.get_veo3_settings(),
                    
                    # YouTube mixer settings
                    'youtube_mixer_settings': {
                        'enabled': gt.enable_youtube_mix_cb.isChecked(),
                        'video_ratio': gt.yt_video_ratio_spin.value() / 100.0,
                        'youtube_percent': gt.yt_video_ratio_spin.value(),
                        'clip_min_duration': gt.yt_clip_min_spin.value(),
                        'clip_max_duration': gt.yt_clip_max_spin.value(),
                        'custom_videos_folder': gt.custom_videos_input.text() or None,  # 🎬 Папка со своими видео
                        'source_mode': self._derive_visual_source_mode(
                            gt.source_youtube_cb.isChecked(),
                            gt.source_local_cb.isChecked(),
                            gt.source_pexels_cb.isChecked(),
                            gt.source_wikimedia_cb.isChecked(),
                            gt.source_pixabay_cb.isChecked(),
                        ),
                        'enable_youtube': gt.source_youtube_cb.isChecked(),
                        'enable_local_videos': gt.source_local_cb.isChecked(),
                        'enable_pexels': gt.source_pexels_cb.isChecked(),
                        'enable_pixabay_videos': gt.source_pixabay_cb.isChecked(),
                        'enable_wikimedia_videos': gt.source_wikimedia_cb.isChecked(),
                        'pexels_api_key': st.pexels_api_input.text().strip() or None,
                        'youtube_data_api_key': st.youtube_data_api_input.text().strip() or None,
                        'pixabay_api_key': st.pixabay_api_input.text().strip() or None,
                    },
                    
                    # Custom texts settings
                    'custom_texts_settings': {
                        'enabled': gt.use_custom_texts_cb.isChecked(),
                        'folder': gt.custom_texts_folder_input.text() or None,
                    },
                    
                    # Image pool settings
                    'image_pool_settings': gt.get_image_pool_settings(),
                    
                    # Custom description text (from custom_text_tab)
                    'custom_description_text': {
                        'enabled': self.custom_text_tab.enable_custom_text_cb.isChecked(),
                        'position': ['start', 'between', 'split', 'end'][self.custom_text_tab.position_combo.currentIndex()],
                        'text': self.custom_text_tab.custom_text_edit.toPlainText(),
                        'ai_customize': self.custom_text_tab.ai_customize_cb.isChecked(),
                        'limit_2000': self.custom_text_tab.limit_2000_cb.isChecked(),
                        'generate_insta_files': self.custom_text_tab.insta_files_cb.isChecked() if hasattr(self.custom_text_tab, 'insta_files_cb') else False,
                    },
                    
                    # Separate metadata files (from custom_text_tab)
                    'separate_metadata': {
                        'enabled': self.custom_text_tab.use_separate_metadata_cb.isChecked(),
                    },

                    'static_video_settings': (
                        self.static_video_tab.get_saved_state()
                        if self.static_video_tab is not None else {}
                    ),
                    'youtube_publish_settings': (
                        self.youtube_publish_tab.get_saved_state()
                        if self.youtube_publish_tab is not None else {}
                    ),
                    'channel_analytics_settings': (
                        self.channel_analytics_tab.get_saved_state()
                        if self.channel_analytics_tab is not None else {}
                    ),
                    
                    # Window geometry
                    'window_geometry': {
                        'x': self.x(),
                        'y': self.y(),
                        'width': self.width(),
                        'height': self.height(),
                    },
                    
                    # App preferences
                    'app_preferences': {
                        'auto_save': st.auto_save_cb.isChecked(),
                        'remember_window': st.remember_window_cb.isChecked(),
                        'smooth_interface_motion': st.motion_cb.isChecked(),
                    },
                }
            }
            
            # Мержим с существующим конфигом чтобы не затирать
            # ключи которых нет в GUI (pronunciation_fixes и т.д.)
            existing = None
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
                existing_us = existing.get('user_settings', {})
                # Сохраняем ключи из файла которых нет в новом конфиге
                for key, value in existing_us.items():
                    if key not in config['user_settings']:
                        config['user_settings'][key] = value

                # Пустое поле во время загрузки/пересоздания GUI не должно удалять ключ.
                # Удаление секретов допускается только после явного подтверждённого сброса.
                if not self._allow_secret_clear_once:
                    for key in (
                        'gemini_api_keys', 'gemini_api_key', 'google_ai_api_key',
                        'heygen_api_key', 'pixabay_api_key', 'pexels_api_key', 'youtube_data_api_key', 'freesound_api_key',
                    ):
                        new_value = config['user_settings'].get(key)
                        old_value = existing_us.get(key)
                        if self._secret_value_is_empty(new_value) and not self._secret_value_is_empty(old_value):
                            config['user_settings'][key] = old_value

                    existing_youtube = existing_us.get('youtube_mixer_settings', {})
                    new_youtube = config['user_settings'].get('youtube_mixer_settings', {})
                    for key in ('pexels_api_key', 'pixabay_api_key', 'youtube_data_api_key'):
                        if self._secret_value_is_empty(new_youtube.get(key)):
                            fallback_value = existing_youtube.get(key) or config['user_settings'].get(key)
                            if not self._secret_value_is_empty(fallback_value):
                                new_youtube[key] = fallback_value
            except (FileNotFoundError, json.JSONDecodeError):
                pass

            if should_preserve_previous_config(existing, config):
                logging.error("Refusing to replace meaningful settings with pristine GUI defaults")
                return False
            
            self.config_manager.config = config
            if self.config_manager.save():
                logging.info("Settings saved successfully")
                return True
            else:
                logging.error("Error saving settings: config manager save failed")
                return False
            
        except Exception as e:
            logging.error(f"Error saving settings: {e}")
            return False
        finally:
            self._allow_secret_clear_once = False

    @staticmethod
    def _secret_value_is_empty(value):
        if isinstance(value, (list, tuple, set)):
            return not any(str(item or '').strip() for item in value)
        return not str(value or '').strip()

    @staticmethod
    def _derive_visual_source_mode(
        enable_youtube: bool,
        enable_local: bool,
        enable_pexels: bool,
        enable_wikimedia: bool = False,
        enable_pixabay: bool = False,
    ) -> str:
        stock_enabled = enable_pexels or enable_wikimedia or enable_pixabay
        if not any((enable_youtube, enable_local, stock_enabled)):
            return 'none'
        if enable_local and not enable_youtube and not stock_enabled:
            return 'local'
        if stock_enabled and (enable_youtube or enable_local):
            return 'smart_mix'
        if stock_enabled:
            return 'fallback'
        if enable_youtube and enable_local:
            return 'smart_mix'
        return 'youtube'
    
    def get_all_settings(self):
        """Get all settings for compatibility with other tabs (queue_tab, series_tab).
        
        This is the canonical snapshot source for the Queue. It must stay in sync
        with save_user_settings to avoid silent config gaps.
        """
        from core.settings_schema import (
            normalize_video_settings,
            normalize_subtitle_settings,
            normalize_audio_settings,
            normalize_overlay_settings,
            normalize_youtube_mixer_settings,
        )
        from core.gemini_models import image_model_from_ui_index
        gt = self.generation_tab
        st = self.settings_tab
        gemini_keys = st.get_gemini_api_keys() if hasattr(st, 'get_gemini_api_keys') else []
        primary_gemini_key = gemini_keys[0] if gemini_keys else ''
        width, height = gt.get_resolution()
        
        # Get persona_id from combo
        from gui.constants import PERSONA_MAPPING
        persona_id = PERSONA_MAPPING.get(gt.persona_combo.currentIndex(), None)
        
        video_settings = normalize_video_settings({
            'width': width,
            'height': height,
            'fps': int(gt.fps_combo.currentText().split()[0]),
            'duration': gt.get_duration_seconds(),
            'shot_min_duration': gt.shot_min_spin.value(),
            'shot_max_duration': gt.shot_max_spin.value(),
            'enable_animation': gt.enable_animation_cb.isChecked(),
            'animation_type': (
                gt.animation_type_combo.currentData()
                or self.ANIMATION_TYPE_MAP.get(
                    gt.animation_type_combo.currentText(), "none"
                )
            ),
            'animation_speed': gt.animation_speed_spin.value(),
            'enable_transitions': gt.enable_transitions_cb.isChecked() if hasattr(gt, 'enable_transitions_cb') else False,
            'transition_duration': gt.transition_duration_spin.value() if hasattr(gt, 'transition_duration_spin') else 0.5,
            'music_volume': gt.music_volume_slider.value() / 100.0,
            'first_shot_from_pool': gt.first_shot_image_cb.isChecked() if hasattr(gt, 'first_shot_image_cb') else True,
            'burn_first_shot_title': gt.burn_first_shot_title_cb.isChecked() if hasattr(gt, 'burn_first_shot_title_cb') else True,
            'first_shot_title_custom': gt.first_shot_title_input.toPlainText().strip() if hasattr(gt, 'first_shot_title_input') else '',
            # ⚡ Glitch
            **(gt.get_glitch_settings() if hasattr(gt, 'get_glitch_settings') else {}),
            **gt.get_video_effect_settings(),
        })
        subtitle_settings = normalize_subtitle_settings({
            'enabled': gt.enable_subtitles_cb.isChecked(),
            'font_size': gt.font_size_spin.value(),
            'font_path': gt.font_combo.currentText() if hasattr(gt, 'font_combo') else '',
            'position': gt.subtitle_position_combo.currentText(),
            **gt.get_subtitle_animation_settings(),
            'style_preset': ('tiktok', 'clean', 'hormozi', 'boxed', 'cinema', 'minimal')[gt.subtitle_style_combo.currentIndex()],
            'font_color': gt.subtitle_color_input.text(),
            'highlight_color': gt.subtitle_highlight_input.text(),
            'outline_width': gt.subtitle_outline_spin.value(),
            'bg_opacity': gt.subtitle_bg_opacity_spin.value() / 100.0,
            'max_words_per_subtitle': gt.subtitle_words_spin.value(),
            'timing_offset': gt.subtitle_timing_offset_spin.value(),
            'uppercase': gt.subtitle_uppercase_cb.isChecked(),
        })
        audio_settings = normalize_audio_settings(self.get_audio_settings())
        overlay_settings = normalize_overlay_settings({
            'enabled': gt.enable_overlay_cb.isChecked() if hasattr(gt, 'enable_overlay_cb') else False,
            'position': (
                gt.overlay_position_combo.currentData()
                or gt.overlay_position_combo.currentText()
            ) if hasattr(gt, 'overlay_position_combo') else 'bottom_right',
            'margin': 50,
            'fullscreen': gt.overlay_fullscreen_cb.isChecked() if hasattr(gt, 'overlay_fullscreen_cb') else False,
        })
        youtube_mixer_settings = normalize_youtube_mixer_settings({
            'enabled': gt.enable_youtube_mix_cb.isChecked(),
            'video_ratio': gt.yt_video_ratio_spin.value() / 100.0,
            'youtube_percent': gt.yt_video_ratio_spin.value(),
            'clip_min_duration': gt.yt_clip_min_spin.value(),
            'clip_max_duration': gt.yt_clip_max_spin.value(),
            'custom_videos_folder': gt.custom_videos_input.text() or None,
            'source_mode': self._derive_visual_source_mode(
                gt.source_youtube_cb.isChecked(),
                gt.source_local_cb.isChecked(),
                gt.source_pexels_cb.isChecked(),
                gt.source_wikimedia_cb.isChecked(),
                gt.source_pixabay_cb.isChecked(),
            ),
            'enable_youtube': gt.source_youtube_cb.isChecked(),
            'enable_local_videos': gt.source_local_cb.isChecked(),
            'enable_pexels': gt.source_pexels_cb.isChecked(),
            'enable_pixabay_videos': gt.source_pixabay_cb.isChecked(),
            'enable_wikimedia_videos': gt.source_wikimedia_cb.isChecked(),
            'pexels_api_key': st.pexels_api_input.text().strip() or None,
            'youtube_data_api_key': st.youtube_data_api_input.text().strip() or None,
            'pixabay_api_key': st.pixabay_api_input.text().strip() or None,
        })
        
        return {
            'source_data': {
                'theme': gt.get_theme(),
                'language': gt.get_language(),
                'persona_id': persona_id,
                'enable_seamless_loop': gt.seamless_loop_cb.isChecked(),
                'enable_comment_bait': gt.comment_bait_cb.isChecked(),
                'strict_text_theme': gt.strict_theme_cb.isChecked(),
            },
            'api_key': primary_gemini_key,
            'google_ai_api_key': primary_gemini_key,
            'output_path': gt.output_path_input.text() or "generated",
            'music_path': gt.music_path_input.text(),
            'media_path': gt.overlay_path_input.text() if hasattr(gt, 'overlay_path_input') else '',
            'video_settings': video_settings,
            'subtitle_settings': subtitle_settings,
            'audio_settings': audio_settings,
            # AI Images
            'use_ai_images': gt.enable_ai_images_cb.isChecked(),
            'use_ai_image_generation': gt.enable_ai_images_cb.isChecked(),
            'strict_theme_following': gt.strict_images_cb.isChecked(),
            'strict_text_theme': gt.strict_theme_cb.isChecked(),
            'enable_scene_variety': gt.scene_variety_cb.isChecked(),
            'num_unique_images': gt.num_unique_images_spin.value() if hasattr(gt, 'num_unique_images_spin') else 5,
            'unlimited_images': gt.unlimited_images_cb.isChecked() if hasattr(gt, 'unlimited_images_cb') else False,
            'custom_images_folder': gt.custom_images_input.text() if hasattr(gt, 'custom_images_input') else '',
            'use_only_custom_images': gt.use_only_custom_images_cb.isChecked() if hasattr(gt, 'use_only_custom_images_cb') else False,
            'use_reference_images': gt.use_reference_images_cb.isChecked() if hasattr(gt, 'use_reference_images_cb') else False,
            'reference_images_folder': gt.reference_images_input.text() if hasattr(gt, 'reference_images_input') else '',
            'use_image_cache': False,
            'save_to_image_cache': False,
            'use_triple_template': False,
            'image_model': image_model_from_ui_index(gt.image_model_combo.currentIndex() if hasattr(gt, 'image_model_combo') else 0),
            # Overlay
            'overlay_settings': overlay_settings,
            # Parallel generation
            'enable_parallel': gt.enable_parallel_cb.isChecked(),
            'num_workers': gt.num_workers_spin.value(),
            # YouTube mixer
            'youtube_mixer_settings': youtube_mixer_settings,
            'youtube_settings': youtube_mixer_settings,
            # Custom texts from folder
            'custom_texts_settings': {
                'enabled': gt.use_custom_texts_cb.isChecked() if hasattr(gt, 'use_custom_texts_cb') else False,
                'folder': (gt.custom_texts_folder_input.text() or None) if hasattr(gt, 'custom_texts_folder_input') else None,
            },
            # Avatar settings
            'avatar_settings': gt.get_avatar_settings() if hasattr(gt, 'get_avatar_settings') else None,
            # Veo3 video generation
            'veo3_settings': gt.get_veo3_settings() if hasattr(gt, 'get_veo3_settings') else None,
            # Image pool
            'image_pool_settings': gt.get_image_pool_settings() if hasattr(gt, 'get_image_pool_settings') else None,
            'custom_description_text': {
                'enabled': self.custom_text_tab.enable_custom_text_cb.isChecked(),
                'position': ['start', 'between', 'split', 'end'][self.custom_text_tab.position_combo.currentIndex()],
                'text': self.custom_text_tab.custom_text_edit.toPlainText(),
                'ai_customize': self.custom_text_tab.ai_customize_cb.isChecked(),
                'limit_2000': self.custom_text_tab.limit_2000_cb.isChecked(),
                'generate_insta_files': self.custom_text_tab.insta_files_cb.isChecked(),
            },
            'separate_metadata': {
                'enabled': self.custom_text_tab.use_separate_metadata_cb.isChecked(),
            },
        }
    
    def get_audio_settings(self):
        """Get audio/TTS settings"""
        return {
            'enabled': self.generation_tab.enable_tts_cb.isChecked(),
            'provider': ('edge', 'gemini')[self.generation_tab.tts_provider_combo.currentIndex()],
            'voice': self.generation_tab.tts_voice_combo.currentText().split(' ')[0],
            'speech_speed': self.generation_tab.speech_speed_spin.value(),
            'edge_pitch_hz': self.generation_tab.edge_pitch_spin.value(),
            'edge_volume_percent': self.generation_tab.edge_volume_spin.value(),
            'auto_fit_duration': True,
            'music_volume': self.generation_tab.music_volume_slider.value() / 100.0,
            'use_music': self.generation_tab.enable_music_cb.isChecked(),
        }
    
    
    
    
    # === Compatibility properties for series_tab and queue_tab ===
    
    @property
    def gemini_api_input(self):
        """Compatibility: return settings_tab's gemini input"""
        return self.settings_tab.gemini_api_input
    
    @property
    def music_path_input(self):
        """Compatibility: return settings_tab's music path input"""
        return self.settings_tab.music_path_input
    
    @property
    def output_path_input(self):
        """Compatibility: return settings_tab's output path input"""
        return self.settings_tab.output_path_input
    
    @property
    def resolution_combo(self):
        """Compatibility: return generation_tab's resolution combo"""
        return self.generation_tab.resolution_combo
    
    @property
    def fps_combo(self):
        """Compatibility: return generation_tab's fps combo"""
        return self.generation_tab.fps_combo
    
    @property
    def enable_animation_cb(self):
        """Compatibility: return generation_tab's animation checkbox"""
        return self.generation_tab.enable_animation_cb
    
    def closeEvent(self, event):
        """Handle window close - save settings and cleanup"""
        # Auto-save settings
        if self.settings_tab.auto_save_cb.isChecked():
            self.save_user_settings()
            self.add_log(t('runtime_settings_autosaved_log'))
        
        if self.generation_thread and self.generation_thread.isRunning():
            should_close = ask_yes_no(
                self,
                t('close_confirm_title'),
                t('generation_running_exit'),
            )
            if not should_close:
                event.ignore()
                return
            self._close_when_generation_stops = True
            self.generation_thread.stop()
            self.add_log(t('runtime_closing_network_log'))
            event.ignore()
            return
        if self.youtube_publish_tab is not None:
            settings = self.youtube_publish_tab._sync_settings()
            pending = self.youtube_publish_tab.pending_upload_count()
            if settings.enabled and settings.auto_start and pending:
                self.main_tabs.setCurrentWidget(self.youtube_publish_tab)
                show_information(
                    self,
                    t('youtube_continues_title'),
                    t('youtube_continues_message').format(count=pending),
                )
                event.ignore()
                return
            if not self.youtube_publish_tab.stop_uploader(10000):
                event.ignore()
                return
            if not self.youtube_publish_tab.stop_account_connection(10000):
                event.ignore()
                return
        if self.channel_analytics_tab is not None:
            if not self.channel_analytics_tab.stop_workers(10000):
                self.main_tabs.setCurrentWidget(self.channel_analytics_tab)
                show_information(
                    self,
                    t('background_finishing_title'),
                    t('background_finishing_message'),
                )
                event.ignore()
                return
        for background_tab in (
            self.generation_tab,
            self.static_video_tab,
            self.channel_clone_tab,
            self.queue_tab,
            self.series_tab,
        ):
            if background_tab is None:
                continue
            stop_background = getattr(background_tab, "stop_background_threads", None)
            if not callable(stop_background):
                continue
            if not stop_background(10000):
                self.main_tabs.setCurrentWidget(background_tab)
                show_information(
                    self,
                    t('background_finishing_title'),
                    t('background_finishing_message'),
                )
                event.ignore()
                return
        event.accept()


if __name__ == "__main__":
    import sys
    from PyQt5.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    window = MainWindowV2()
    window.show()
    sys.exit(app.exec_())
