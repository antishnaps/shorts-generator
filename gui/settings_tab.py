#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Settings Tab - Application settings and configuration

Contains:
- API Keys management (multiple keys support)
- Output paths
- Application preferences
- UI Language selection
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QCheckBox,
    QScrollArea, QFrame, QFileDialog,
    QComboBox, QStackedWidget, QTextEdit
)
from PyQt5.QtCore import Qt, pyqtSignal

from gui.styles_v2 import ColorsV2, get_sizes
from gui.localized_dialogs import (
    ask_yes_no,
    show_critical,
    show_information,
    show_warning,
)
from gui.translations import get_ui_language_list, get_language_code, set_ui_language, t, UI_LANGUAGES


class SettingsTab(QWidget):
    """Application settings tab"""
    
    # Сигнал для уведомления о смене языка UI
    language_changed = pyqtSignal(str)
    
    def __init__(self, parent_window=None):
        super().__init__()
        self.parent_window = parent_window
        self.init_ui()
    
    def init_ui(self):
        """Initialize settings UI"""
        sizes = get_sizes()
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(sizes.SPACING_LG)
        layout.setContentsMargins(sizes.SPACING_MD, sizes.SPACING_MD,
                                   sizes.SPACING_MD, sizes.SPACING_MD)

        # === UI Language Card ===
        lang_card = QGroupBox(t('ui_language'))
        lang_card.setStyleSheet(f"""
            QGroupBox {{
                background-color: {ColorsV2.BG_CARD};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 8px;
                margin-top: 16px;
                padding: 16px;
                padding-top: 28px;
            }}
            QGroupBox::title {{
                color: {ColorsV2.ACCENT_CYAN};
                font-weight: 600;
            }}
        """)
        lang_layout = QHBoxLayout(lang_card)
        lang_layout.setSpacing(sizes.SPACING_MD)
        
        lang_label = QLabel(t('ui_language') + ":")
        lang_label.setMinimumWidth(sizes.scale(100))
        lang_layout.addWidget(lang_label)
        
        self.ui_language_combo = QComboBox()
        self.ui_language_combo.addItems(get_ui_language_list())
        self.ui_language_combo.setCurrentText(UI_LANGUAGES.get('Russian', '🇷🇺 Русский'))
        self.ui_language_combo.currentTextChanged.connect(self._on_ui_language_changed)
        lang_layout.addWidget(self.ui_language_combo)
        
        lang_hint = QLabel(t('applies_immediately'))
        lang_hint.setStyleSheet(f"color: {ColorsV2.TEXT_MUTED}; font-size: 11px;")
        lang_layout.addWidget(lang_hint)
        
        lang_layout.addStretch()
        layout.addWidget(lang_card)

        # === API Keys Card ===
        api_card = QGroupBox(t('settings_api_card'))
        api_card.setStyleSheet(f"""
            QGroupBox {{
                background-color: {ColorsV2.BG_CARD};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 8px;
                margin-top: 16px;
                padding: 16px;
                padding-top: 28px;
            }}
            QGroupBox::title {{
                color: {ColorsV2.ACCENT_BLUE};
                font-weight: 600;
            }}
        """)
        api_layout = QVBoxLayout(api_card)
        api_layout.setSpacing(sizes.SPACING_MD)
        
        # Gemini API Keys (multiple) / AiTunnel keys
        gemini_label = QLabel(t('gemini_keys_label'))
        api_layout.addWidget(gemini_label)
        
        self.gemini_api_input = QTextEdit()
        self.gemini_api_input.setPlaceholderText(
            t('gemini_keys_placeholder')
        )
        self.gemini_api_input.setMaximumHeight(100)
        self.gemini_api_input.setStyleSheet(f"""
            QTextEdit {{
                background-color: {ColorsV2.BG_INPUT};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 4px;
                padding: 8px;
                font-family: monospace;
            }}
        """)
        self.gemini_api_input.setAccessibleName(t('gemini_keys_label'))

        self.gemini_masked_input = QLineEdit()
        self.gemini_masked_input.setReadOnly(True)
        self.gemini_masked_input.setFocusPolicy(Qt.NoFocus)
        self.gemini_masked_input.setAccessibleName(t('gemini_keys_label'))
        self.gemini_masked_input.setAccessibleDescription(t('api_keys_hidden_hint'))

        self.gemini_secret_stack = QStackedWidget()
        self.gemini_secret_stack.addWidget(self.gemini_masked_input)
        self.gemini_secret_stack.addWidget(self.gemini_api_input)
        self.gemini_secret_stack.setCurrentIndex(0)
        self.gemini_secret_stack.setMaximumHeight(100)
        api_layout.addWidget(self.gemini_secret_stack)
        
        gemini_btns = QHBoxLayout()
        self.gemini_show_btn = QPushButton(t('show_keys'))
        self.gemini_show_btn.setMinimumWidth(140)
        self.gemini_show_btn.setCheckable(True)
        self.gemini_show_btn.setToolTip(t('api_keys_hidden_hint'))
        self.gemini_show_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ColorsV2.BG_LIGHT};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 4px;
                padding: 6px 12px;
                color: {ColorsV2.TEXT_SECONDARY};
            }}
            QPushButton:hover {{
                background-color: {ColorsV2.BG_HOVER};
                color: {ColorsV2.TEXT_PRIMARY};
            }}
            QPushButton:checked {{
                background-color: {ColorsV2.ACCENT_BLUE};
                color: white;
            }}
        """)
        self.gemini_show_btn.toggled.connect(self._toggle_api_visibility)
        gemini_btns.addWidget(self.gemini_show_btn)
        
        test_gemini_btn = QPushButton(t('test'))
        test_gemini_btn.setFixedWidth(80)
        test_gemini_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ColorsV2.BG_LIGHT};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 4px;
                padding: 6px 12px;
                color: {ColorsV2.TEXT_SECONDARY};
            }}
            QPushButton:hover {{
                background-color: {ColorsV2.ACCENT_GREEN};
                color: white;
                border-color: {ColorsV2.ACCENT_GREEN};
            }}
        """)
        test_gemini_btn.clicked.connect(self._test_gemini_api)
        gemini_btns.addWidget(test_gemini_btn)        
        self.keys_count_label = QLabel(t('keys_count').format(count=0))
        self.keys_count_label.setStyleSheet(f"color: {ColorsV2.ACCENT_BLUE}; font-weight: 500; margin-left: 8px;")
        gemini_btns.addWidget(self.keys_count_label)
        
        gemini_btns.addStretch()
        api_layout.addLayout(gemini_btns)
        
        # Update keys count on text change
        self.gemini_api_input.textChanged.connect(self._update_keys_count)
        
        # HeyGen API Key
        heygen_row = QHBoxLayout()
        heygen_label = QLabel("HeyGen API:")
        heygen_label.setMinimumWidth(sizes.scale(150))
        heygen_row.addWidget(heygen_label)
        
        self.heygen_api_input = QLineEdit()
        self.heygen_api_input.setPlaceholderText(t('heygen_unavailable_placeholder'))
        self.heygen_api_input.setEchoMode(QLineEdit.Password)
        heygen_row.addWidget(self.heygen_api_input)
        self.heygen_test_btn = QPushButton(t('test'))
        self.heygen_test_btn.clicked.connect(self._test_heygen_api)
        heygen_row.addWidget(self.heygen_test_btn)
        api_layout.addLayout(heygen_row)
        
        # Pixabay API Key
        pixabay_row = QHBoxLayout()
        pixabay_label = QLabel("Pixabay API:")
        pixabay_label.setMinimumWidth(sizes.scale(150))
        pixabay_row.addWidget(pixabay_label)
        
        self.pixabay_api_input = QLineEdit()
        self.pixabay_api_input.setPlaceholderText(t('pixabay_image_placeholder'))
        self.pixabay_api_input.setEchoMode(QLineEdit.Password)
        pixabay_row.addWidget(self.pixabay_api_input)
        self.pixabay_test_btn = QPushButton(t('test'))
        self.pixabay_test_btn.clicked.connect(self._test_pixabay_api)
        pixabay_row.addWidget(self.pixabay_test_btn)
        api_layout.addLayout(pixabay_row)

        # Pexels API Key
        pexels_row = QHBoxLayout()
        pexels_label = QLabel("Pexels Video API:")
        pexels_label.setMinimumWidth(sizes.scale(150))
        pexels_row.addWidget(pexels_label)

        self.pexels_api_input = QLineEdit()
        self.pexels_api_input.setPlaceholderText(t('pexels_video_placeholder'))
        self.pexels_api_input.setEchoMode(QLineEdit.Password)
        pexels_row.addWidget(self.pexels_api_input)
        self.pexels_test_btn = QPushButton(t('test'))
        self.pexels_test_btn.clicked.connect(self._test_pexels_api)
        pexels_row.addWidget(self.pexels_test_btn)
        api_layout.insertLayout(3, pexels_row)

        # YouTube Data API Key
        youtube_data_row = QHBoxLayout()
        youtube_data_label = QLabel("YouTube Data API:")
        youtube_data_label.setMinimumWidth(sizes.scale(150))
        youtube_data_row.addWidget(youtube_data_label)
        self.youtube_data_api_input = QLineEdit()
        self.youtube_data_api_input.setPlaceholderText(t('youtube_data_api_placeholder'))
        self.youtube_data_api_input.setEchoMode(QLineEdit.Password)
        youtube_data_row.addWidget(self.youtube_data_api_input)
        self.youtube_data_test_btn = QPushButton(t('test'))
        self.youtube_data_test_btn.clicked.connect(self._test_youtube_data_api)
        youtube_data_row.addWidget(self.youtube_data_test_btn)
        api_layout.addLayout(youtube_data_row)

        # YouTube authenticated session cookies
        youtube_cookies_row = QHBoxLayout()
        youtube_cookies_label = QLabel(t("youtube_cookies_title") + ":")
        youtube_cookies_label.setMinimumWidth(sizes.scale(150))
        youtube_cookies_row.addWidget(youtube_cookies_label)
        self.youtube_cookies_status = QLabel()
        self.youtube_cookies_status.setMinimumWidth(sizes.scale(180))
        youtube_cookies_row.addWidget(self.youtube_cookies_status, 1)
        self.youtube_cookies_import_btn = QPushButton(t('youtube_cookies_import'))
        self.youtube_cookies_import_btn.clicked.connect(self._import_youtube_cookies)
        youtube_cookies_row.addWidget(self.youtube_cookies_import_btn)
        self.youtube_cookies_refresh_btn = QPushButton(t('youtube_cookies_refresh'))
        self.youtube_cookies_refresh_btn.clicked.connect(
            self._refresh_youtube_cookies_from_browser
        )
        youtube_cookies_row.addWidget(self.youtube_cookies_refresh_btn)
        self.youtube_cookies_test_btn = QPushButton(t('test'))
        self.youtube_cookies_test_btn.clicked.connect(self._check_youtube_cookies)
        youtube_cookies_row.addWidget(self.youtube_cookies_test_btn)
        api_layout.addLayout(youtube_cookies_row)
        self._update_youtube_cookies_status()
        
        # Freesound API Key
        freesound_row = QHBoxLayout()
        freesound_label = QLabel("Freesound API:")
        freesound_label.setMinimumWidth(sizes.scale(150))
        freesound_row.addWidget(freesound_label)
        
        self.freesound_api_input = QLineEdit()
        self.freesound_api_input.setPlaceholderText(t('freesound_placeholder'))
        self.freesound_api_input.setEchoMode(QLineEdit.Password)
        freesound_row.addWidget(self.freesound_api_input)
        self.freesound_test_btn = QPushButton(t('test'))
        self.freesound_test_btn.clicked.connect(self._test_freesound_api)
        freesound_row.addWidget(self.freesound_test_btn)
        api_layout.addLayout(freesound_row)

        self._secret_line_edits = (
            self.heygen_api_input,
            self.pixabay_api_input,
            self.pexels_api_input,
            self.youtube_data_api_input,
            self.freesound_api_input,
        )
        for secret_input in self._secret_line_edits:
            secret_input.setAccessibleDescription(t('api_keys_hidden_hint'))
        
        # API info
        api_info = QLabel(
            t('api_sources_hint')
        )
        api_info.setStyleSheet(f"color: {ColorsV2.TEXT_MUTED}; font-size: 12px;")
        api_layout.addWidget(api_info)
        
        layout.addWidget(api_card)
        

        # === Preferences Card ===
        prefs_card = QGroupBox(t('prefs_card'))
        prefs_card.setStyleSheet(f"""
            QGroupBox {{
                background-color: {ColorsV2.BG_CARD};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 8px;
                margin-top: 16px;
                padding: 16px;
                padding-top: 28px;
            }}
            QGroupBox::title {{
                color: {ColorsV2.ACCENT_CYAN};
                font-weight: 600;
            }}
        """)
        prefs_layout = QVBoxLayout(prefs_card)
        prefs_layout.setSpacing(sizes.SPACING_SM)
        
        self.auto_save_cb = QCheckBox(t('auto_save'))
        self.auto_save_cb.setChecked(True)
        prefs_layout.addWidget(self.auto_save_cb)
        
        self.remember_window_cb = QCheckBox(t('remember_window'))
        self.remember_window_cb.setChecked(True)
        prefs_layout.addWidget(self.remember_window_cb)

        self.motion_cb = QCheckBox(t('smooth_interface_motion'))
        self.motion_cb.setChecked(True)
        self.motion_cb.setToolTip(t('smooth_interface_motion_hint'))
        prefs_layout.addWidget(self.motion_cb)
        
        layout.addWidget(prefs_card)
        
        # === Actions Card ===
        actions_card = QGroupBox(t('actions_card'))
        actions_card.setStyleSheet(f"""
            QGroupBox {{
                background-color: {ColorsV2.BG_CARD};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 8px;
                margin-top: 16px;
                padding: 16px;
                padding-top: 28px;
            }}
        """)
        actions_layout = QHBoxLayout(actions_card)
        actions_layout.setSpacing(sizes.SPACING_MD)
        
        save_btn = QPushButton(t('save_settings'))
        save_btn.clicked.connect(self._save_settings)
        actions_layout.addWidget(save_btn)
        
        reset_btn = QPushButton(t('reset_settings'))
        reset_btn.clicked.connect(self._reset_settings)
        actions_layout.addWidget(reset_btn)
        
        export_btn = QPushButton(t('export_settings'))
        export_btn.clicked.connect(self._export_settings)
        actions_layout.addWidget(export_btn)
        
        import_btn = QPushButton(t('import_settings'))
        import_btn.clicked.connect(self._import_settings)
        actions_layout.addWidget(import_btn)
        
        actions_layout.addStretch()
        layout.addWidget(actions_card)
        
        layout.addStretch()
        scroll.setWidget(container)
        main_layout.addWidget(scroll)
    
    def _on_ui_language_changed(self, display_name: str):
        """Handle UI language change"""
        lang_code = get_language_code(display_name)
        set_ui_language(lang_code)
        if self.parent_window and getattr(self.parent_window, '_loading_settings', False):
            return
        self.language_changed.emit(lang_code)
        
        # Save to config
        if self.parent_window and hasattr(self.parent_window, 'save_user_settings'):
            self.parent_window.save_user_settings()
    
    def _toggle_api_visibility(self, checked: bool):
        """Reveal secrets only after an explicit action and re-hide as a unit."""
        self.gemini_show_btn.setText(t('hide_keys') if checked else t('show_keys'))
        self.gemini_secret_stack.setCurrentIndex(1 if checked else 0)
        echo_mode = QLineEdit.Normal if checked else QLineEdit.Password
        for secret_input in self._secret_line_edits:
            secret_input.setEchoMode(echo_mode)
        if checked:
            self.gemini_api_input.setFocus(Qt.OtherFocusReason)

    def hideEvent(self, event):
        """Never leave API keys visible after navigating away from Settings."""
        if hasattr(self, 'gemini_show_btn') and self.gemini_show_btn.isChecked():
            self.gemini_show_btn.setChecked(False)
        super().hideEvent(event)
    
    def _update_keys_count(self):
        """Update the count of API keys"""
        text = self.gemini_api_input.toPlainText()
        keys = [k.strip() for k in text.strip().split('\n') if k.strip()]
        count = len(keys)
        self.keys_count_label.setText(t('keys_count').format(count=count))
        self.gemini_masked_input.setText(
            f"••••••••  ·  {t('keys_count').format(count=count)}"
        )
    
    def get_gemini_api_keys(self) -> list:
        """Get list of Gemini API keys"""
        text = self.gemini_api_input.toPlainText()
        keys = [k.strip() for k in text.strip().split('\n') if k.strip()]
        return keys
        
    def get_heygen_api_key(self) -> str:
        """Get HeyGen API key"""
        return self.heygen_api_input.text().strip() if hasattr(self, 'heygen_api_input') else ""
    
    def set_gemini_api_keys(self, keys):
        """Set Gemini API keys from list or string"""
        if isinstance(keys, list):
            self.gemini_api_input.setPlainText('\n'.join(keys))
        else:
            self.gemini_api_input.setPlainText(str(keys))
        self._update_keys_count()
    
    def _test_gemini_api(self):
        """Test API key (Gemini or AiTunnel — auto-detected)."""
        keys = self.get_gemini_api_keys()
        if not keys:
            show_warning(self, t('error'), t('enter_api_key'))
            return

        results = []
        try:
            from core.text_generator import TextGenerator
            for key_index, key in enumerate(keys[:3], start=1):  # Тестируем до 3 ключей
                key = key.strip()
                if not key:
                    continue
                provider = "AiTunnel" if key.startswith("sk-aitunnel-") else "Gemini"
                ok, detail = TextGenerator.test_api_key_via_rest(key)
                status = "✅" if ok else "❌"
                results.append(
                    t("settings_api_key_result").format(
                        status=status,
                        provider=provider,
                        number=key_index,
                        detail=detail,
                    )
                )
        except Exception as e:
            results.append(t('api_test_failed').format(error=e))

        msg = "\n".join(results) if results else t('keys_not_found')
        show_information(self, t('api_test_title'), msg)

    def _test_heygen_api(self):
        key = self.get_heygen_api_key()
        if not key:
            show_warning(self, t('error'), t('enter_api_key'))
            return
        try:
            from core.heygen_client import HeyGenClient

            HeyGenClient(key).validate_key()
            show_information(self, "HeyGen API", t('heygen_api_ok'))
        except Exception as exc:
            show_critical(
                self, "HeyGen API", t("runtime_error_detail").format(error=exc)
            )

    def _test_pexels_api(self):
        key = self.pexels_api_input.text().strip()
        if not key:
            show_warning(self, t('error'), t('enter_api_key'))
            return
        try:
            from core.pexels_client import PexelsClient

            videos = PexelsClient(key).search_videos(
                "cats", per_page=1, orientation="portrait"
            )
            if not videos:
                raise RuntimeError(t('pexels_test_empty'))
            show_information(
                self,
                "Pexels Video API",
                t('pexels_test_ok').format(id=videos[0].get('id', '—')),
            )
        except Exception as exc:
            show_critical(
                self,
                "Pexels Video API",
                t("runtime_error_detail").format(error=exc),
            )

    def _test_pixabay_api(self):
        key = self.pixabay_api_input.text().strip()
        if not key:
            show_warning(self, t('error'), t('enter_api_key'))
            return
        try:
            from core.pixabay_client import PixabayClient

            videos = PixabayClient(key).search_videos(
                "nature", per_page=3, min_width=720, min_height=720, safesearch=True,
            )
            if not videos:
                raise RuntimeError(t('pixabay_test_empty'))
            show_information(
                self,
                "Pixabay Video API",
                t('pixabay_test_ok').format(id=videos[0].get('id', '—')),
            )
        except Exception as exc:
            show_critical(
                self,
                "Pixabay Video API",
                t("runtime_error_detail").format(error=exc),
            )

    def _test_freesound_api(self):
        key = self.freesound_api_input.text().strip()
        if not key:
            show_warning(self, t('error'), t('enter_api_key'))
            return
        try:
            from core.freesound_client import FreesoundClient

            sounds = FreesoundClient(key).search_sfx("whoosh", page_size=1)
            if not sounds:
                raise RuntimeError(t('freesound_test_empty'))
            show_information(
                self,
                "Freesound API",
                t('freesound_test_ok').format(id=sounds[0].get('id', '—')),
            )
        except Exception as exc:
            show_critical(
                self,
                "Freesound API",
                t("runtime_error_detail").format(error=exc),
            )

    def _test_youtube_data_api(self):
        key = self.youtube_data_api_input.text().strip()
        if not key:
            show_warning(self, t('error'), t('enter_api_key'))
            return
        try:
            from core.youtube.search import search_youtube_api

            videos = search_youtube_api(
                "cats",
                key,
                max_results=1,
                video_duration="any",
                video_definition="high",
                log_callback=lambda _message: None,
            )
            if not videos:
                raise RuntimeError(t('youtube_data_test_empty'))
            show_information(
                self,
                "YouTube Data API",
                t('youtube_data_test_ok').format(
                    title=videos[0].get('title', t('youtube_data_video_fallback'))
                ),
            )
        except Exception as exc:
            show_critical(
                self,
                "YouTube Data API",
                t("runtime_error_detail").format(error=exc),
            )

    @staticmethod
    def _youtube_cookies_path():
        from pathlib import Path

        return Path(__file__).resolve().parent.parent / "youtube_cookies.txt"

    def _update_youtube_cookies_status(self):
        from core.youtube import has_current_youtube_auth_cookies

        authorized = has_current_youtube_auth_cookies(
            str(self._youtube_cookies_path())
        )
        self.youtube_cookies_status.setText(
            t('youtube_cookies_ready') if authorized else t('youtube_cookies_missing')
        )
        color = ColorsV2.ACCENT_GREEN if authorized else ColorsV2.TEXT_MUTED
        self.youtube_cookies_status.setStyleSheet(f"color: {color};")
        return authorized

    def _check_youtube_cookies(self):
        authorized = self._update_youtube_cookies_status()
        message = (
            t('youtube_cookies_ready_message')
            if authorized
            else t('youtube_cookies_missing_message')
        )
        show_information(self, t("youtube_cookies_title"), message)

    def _import_youtube_cookies(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            t('youtube_cookies_import'),
            "",
            t("cookies_files_filter"),
        )
        if not file_path:
            return

        import os
        import shutil
        import tempfile
        from pathlib import Path

        from core.youtube import (
            has_current_youtube_auth_cookies,
            normalize_cookies_file,
        )
        from core.youtube_mixer import reset_youtube_access_state

        destination = self._youtube_cookies_path()
        temp_fd, temp_name = tempfile.mkstemp(
            suffix=".txt", prefix="youtube_cookies_import_", dir=str(destination.parent)
        )
        os.close(temp_fd)
        temp_path = Path(temp_name)
        try:
            shutil.copy2(file_path, temp_path)
            normalize_cookies_file(str(temp_path))
            if not has_current_youtube_auth_cookies(str(temp_path)):
                raise ValueError(t('youtube_cookies_invalid_message'))
            if destination.exists():
                shutil.copy2(destination, destination.with_suffix(".txt.backup"))
            temp_path.replace(destination)
            reset_youtube_access_state()
            self._update_youtube_cookies_status()
            show_information(
                self,
                t("youtube_cookies_title"),
                t('youtube_cookies_imported_message'),
            )
        except Exception as exc:
            show_warning(
                self, t('error'), t("runtime_error_detail").format(error=exc)
            )
        finally:
            temp_path.unlink(missing_ok=True)

    def _refresh_youtube_cookies_from_browser(self):
        from core.youtube import refresh_youtube_cookies
        from core.youtube_mixer import reset_youtube_access_state

        if refresh_youtube_cookies(str(self._youtube_cookies_path())):
            reset_youtube_access_state()
            self._update_youtube_cookies_status()
            show_information(
                self,
                t("youtube_cookies_title"),
                t('youtube_cookies_refreshed_message'),
            )
            return
        self._update_youtube_cookies_status()
        show_warning(
            self,
            t("youtube_cookies_title"),
            t('youtube_cookies_browser_failed_message'),
        )
    

    
    def _save_settings(self):
        """Save current settings"""
        if self.parent_window and hasattr(self.parent_window, 'save_user_settings'):
            self.parent_window.save_user_settings()
            show_information(self, t('saved'), t('settings_saved'))
        else:
            show_warning(self, t('error'), t('settings_save_failed'))
    
    def _reset_settings(self):
        """Reset settings to defaults"""
        if ask_yes_no(
            self, t('reset_settings'),
            t('reset_confirm'),
        ):
            if self.parent_window:
                self.parent_window._allow_secret_clear_once = True
            # Reset API keys
            self.gemini_api_input.clear()
            self.heygen_api_input.clear()
            self.pixabay_api_input.clear()
            self.pexels_api_input.clear()
            self.youtube_data_api_input.clear()
            self.freesound_api_input.clear()
            

            # Reset preferences
            self.auto_save_cb.setChecked(True)
            self.remember_window_cb.setChecked(True)
            
            # Reset language
            self.ui_language_combo.setCurrentText(UI_LANGUAGES.get('Russian', '🇷🇺 Русский'))
            
            show_information(self, t('reset_settings'), t('settings_reset'))
    
    def _export_settings(self):
        """Export settings to file"""
        if not self.parent_window:
            return
            
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            t('export_settings'),
            "settings_backup.json",
            t("json_files_filter"),
        )
        if file_path:
            try:
                import shutil
                # Copy current config
                shutil.copy(self.parent_window.config_file, file_path)
                show_information(
                    self,
                    t('export_settings'),
                    f"{t('export_success')}\n{file_path}",
                )
            except Exception as e:
                show_warning(
                    self, t('error'), t('export_failed').format(error=e)
                )
    
    def _import_settings(self):
        """Import settings from file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, t('import_settings'), "", t("json_files_filter")
        )
        if file_path:
            try:
                import shutil
                # Backup current config
                if self.parent_window and self.parent_window.config_file.exists():
                    shutil.copy(self.parent_window.config_file, 
                               str(self.parent_window.config_file) + '.backup')
                
                # Copy imported config
                shutil.copy(file_path, self.parent_window.config_file)
                
                # Reload settings
                self.parent_window.load_settings()
                
                show_information(self, t('import_settings'), t('import_success'))
            except Exception as e:
                show_warning(
                    self, t('error'), t('import_failed').format(error=e)
                )
