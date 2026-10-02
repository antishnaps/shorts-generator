#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YouTube automatic upload tab."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List

from PyQt5.QtCore import QDateTime, QThread, QTime, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from core.youtube_publisher import (
    ACTIVE_STATUSES,
    FINAL_STATUSES,
    MAX_PARALLEL_UPLOADS,
    YOUTUBE_ERROR_RETRY_SCHEDULED,
    PublishSettings,
    YouTubePublishQueue,
    YouTubeAccountStore,
    YouTubeUploader,
    build_package_from_video,
    is_quota_exhausted_error,
    is_upload_limit_error,
    parse_datetime,
    has_saved_youtube_token,
    resolve_oauth_client_source,
    upload_due_batch,
)
from gui.styles_v2 import (
    ColorsV2,
    get_group_box_style,
    get_sizes,
)
from gui.constants import get_internal_language
from gui.locales.runtime_youtube_main import (
    UPLOAD_EVENT_COMPLETED,
    UPLOAD_EVENT_FAILED,
    UPLOAD_EVENT_RETRY,
    UPLOAD_EVENT_UPLOADING,
)
from gui.localized_dialogs import show_warning
from gui.translations import t
from gui.youtube_core_localization import (
    localize_core_event,
    localize_oauth_source,
    localize_queue_error,
    localize_youtube_error,
    oauth_browser_messages,
)


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
_CONTENT_LANGUAGE_TRANSLATION_KEYS = {
    "Russian": "runtime_content_language_russian",
    "English": "runtime_content_language_english",
    "Spanish": "runtime_content_language_spanish",
    "French": "runtime_content_language_french",
    "German": "runtime_content_language_german",
    "Chinese": "runtime_content_language_chinese",
    "Japanese": "runtime_content_language_japanese",
    "Korean": "runtime_content_language_korean",
    "Portuguese": "runtime_content_language_portuguese",
    "Italian": "runtime_content_language_italian",
    "Hindi": "runtime_content_language_hindi",
    "Arabic": "runtime_content_language_arabic",
}
YOUTUBE_CATEGORIES = (
    ("yt_category_film", "1"),
    ("yt_category_autos", "2"),
    ("yt_category_music", "10"),
    ("yt_category_pets", "15"),
    ("yt_category_sports", "17"),
    ("yt_category_travel", "19"),
    ("yt_category_gaming", "20"),
    ("yt_category_people", "22"),
    ("yt_category_comedy", "23"),
    ("yt_category_entertainment", "24"),
    ("yt_category_news", "25"),
    ("yt_category_style", "26"),
    ("yt_category_education", "27"),
    ("yt_category_science", "28"),
)


def _localized_content_language(value) -> str:
    """Localize a persisted content-language ID for display only."""
    raw_value = str(value or "").strip()
    if not raw_value:
        return "-"
    language = get_internal_language(raw_value)
    translation_key = _CONTENT_LANGUAGE_TRANSLATION_KEYS.get(language)
    return t(translation_key) if translation_key else raw_value


class YouTubeUploadWorker(QThread):
    log = pyqtSignal(str)
    queue_changed = pyqtSignal()
    task_progress = pyqtSignal(str, int)
    activity = pyqtSignal(str, dict)

    def __init__(self, settings: PublishSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _sleep_seconds(self, seconds: int) -> None:
        for _ in range(max(1, seconds)):
            if self._stop_requested:
                return
            self.msleep(1000)

    def _report_task_progress(self, task_id: str, percent: int) -> None:
        """Publish progress as both the legacy signal and a locale-neutral event."""
        value = max(0, min(100, int(percent)))
        task_key = str(task_id or "")
        self.task_progress.emit(task_key, value)
        self.activity.emit(
            UPLOAD_EVENT_UPLOADING,
            {"task_id": task_key, "progress": value},
        )

    def _report_core_event(self, event: str, details: Dict) -> None:
        self.log.emit(localize_core_event(event, details))

    def run(self) -> None:
        queue = YouTubePublishQueue(self.settings.queue_path)
        try:
            self._run_worker(queue)
        except PermissionError as error:
            # WinError 5: queue file was briefly locked — recover and keep going
            try:
                recovered = queue.recover_interrupted_uploads()
                if recovered:
                    self.queue_changed.emit()
            except Exception:
                pass
            self.log.emit(t('yt_worker_file_locked').format(error=error))
        except Exception as error:
            recovered = queue.recover_interrupted_uploads()
            if recovered:
                self.queue_changed.emit()
            self.log.emit(
                t('yt_worker_crashed').format(error=localize_youtube_error(error))
            )

    def _run_worker(self, queue: YouTubePublishQueue) -> None:
        parallelism = max(1, int(self.settings.max_parallel_uploads or 1))
        self.log.emit(t('yt_worker_started').format(count=parallelism))
        interrupted = queue.recover_interrupted_uploads()
        if interrupted:
            self.queue_changed.emit()
            self.log.emit(t('yt_interrupted_restored').format(count=interrupted))
        recovered = queue.retry_transient_failures(settings=self.settings)
        if recovered:
            self.queue_changed.emit()
            self.log.emit(t('yt_retry_restored').format(count=recovered))

        channel = YouTubeUploader(self.settings).channel_info(allow_interactive=False)
        if not channel.get("connected"):
            raise RuntimeError(
                localize_youtube_error(
                    channel.get("error") or t('yt_channel_unavailable'),
                    code=str(channel.get("error_code") or ""),
                )
            )
        self.log.emit(t('yt_access_confirmed').format(
            channel=channel.get('channel_title') or channel.get('channel_id')
        ))

        # 🔄 Сверка очереди с реальными видео на канале
        # (предотвращает дублирование после сбоя или ручной загрузки)
        try:
            self.log.emit(t('yt_reconcile_start'))
            _uploader = YouTubeUploader(self.settings)
            reconciled = queue.reconcile_with_youtube(
                uploader=_uploader,
                event_callback=self._report_core_event,
                max_results=200,
            )
            if reconciled:
                self.queue_changed.emit()
                self.log.emit(t('yt_reconcile_done').format(count=reconciled))
            else:
                self.log.emit(t('yt_reconcile_none'))
        except Exception as rec_err:
            self.log.emit(t('yt_reconcile_failed').format(error=rec_err))

        rebalanced = queue.rebalance_upload_waves(self.settings)
        if rebalanced:
            self.queue_changed.emit()
            self.log.emit(t('yt_waves_rebalanced').format(count=rebalanced))

        queue.reload()
        pending = [task for task in queue.tasks() if task.get("status") in ACTIVE_STATUSES]
        queued_times = [
            parse_datetime(task.get("upload_after"))
            for task in pending
            if task.get("status") == "queued" and parse_datetime(task.get("upload_after"))
        ]
        if pending:
            message = t('yt_queue_pending_log').format(count=len(pending))
            if queued_times:
                message += t('yt_next_attempt_log').format(
                    time=min(queued_times).strftime('%Y-%m-%d %H:%M')
                )
            self.log.emit(message)

        while not self._stop_requested:
            outcomes = upload_due_batch(
                self.settings,
                queue,
                stop_requested=lambda: self._stop_requested,
                progress_callback=self._report_task_progress,
                queue_changed_callback=self.queue_changed.emit,
                account_store=YouTubeAccountStore(self.settings.accounts_path),
                event_callback=self._report_core_event,
            )
            if not outcomes:
                self._sleep_seconds(2)
                continue

            for outcome in outcomes:
                title = outcome.get("title") or outcome.get("task_id") or t('yt_video_fallback')
                status = outcome.get("status")
                if status in {"uploaded", "scheduled"}:
                    url = outcome.get("youtube_url") or outcome.get("youtube_video_id") or t('yt_done_fallback')
                    self.log.emit(t('yt_uploaded_log').format(title=title, result=url))
                    self.activity.emit(
                        UPLOAD_EVENT_COMPLETED,
                        {"title": str(title), "status": str(status)},
                    )
                elif status == "retrying":
                    display_error = localize_queue_error(outcome)
                    self.log.emit(t('yt_retry_log').format(title=title, error=display_error))
                    self.activity.emit(
                        UPLOAD_EVENT_RETRY,
                        {"title": str(title), "error": display_error},
                    )
                elif status == "failed":
                    display_error = localize_queue_error(outcome)
                    self.log.emit(t('yt_failed_log').format(title=title, error=display_error))
                    self.activity.emit(
                        UPLOAD_EVENT_FAILED,
                        {"title": str(title), "error": display_error},
                    )

        self.log.emit(t('yt_worker_stopped_log'))

class YouTubeConnectWorker(QThread):
    result = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, settings: PublishSettings, parent=None, force_account_selection: bool = False):
        super().__init__(parent)
        self.settings = settings
        self.force_account_selection = force_account_selection

    def run(self) -> None:
        try:
            uploader = YouTubeUploader(
                self.settings,
                oauth_browser_messages=oauth_browser_messages(),
            )
            info = uploader.channel_info(
                force_account_selection=self.force_account_selection
            )
            self.result.emit(info)
        except Exception as e:
            self.error.emit(localize_youtube_error(e))


class YouTubePublishTab(QWidget):
    settings_changed = pyqtSignal()
    log_message = pyqtSignal(str)
    upload_activity = pyqtSignal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: YouTubeUploadWorker | None = None
        self.connect_worker: YouTubeConnectWorker | None = None
        self._loading = False
        self._progress: Dict[str, int] = {}
        self._visible_task_ids: List[str] = []
        self._channel_title = ""
        self._channel_id = ""
        self._last_channel_check = ""
        self._pending_token_path = ""
        self.settings = PublishSettings()
        self.queue = YouTubePublishQueue(self.settings.queue_path)
        self.account_store = YouTubeAccountStore(self.settings.accounts_path)
        self._build_ui()
        self._connect_signals()
        self._automation_watchdog = QTimer(self)
        self._automation_watchdog.setInterval(30_000)
        self._automation_watchdog.timeout.connect(self._ensure_automation_running)
        self._automation_watchdog.start()
        self._render_cached_channel()
        self.refresh_queue()

    def _compact_primary_style(self) -> str:
        sizes = get_sizes()
        return f"""
            QPushButton {{
                background: {ColorsV2.GRADIENT_BLUE};
                color: {ColorsV2.TEXT_PRIMARY};
                border: none;
                border-radius: {sizes.RADIUS_LG}px;
                padding: 7px 12px;
                font-weight: 700;
                font-size: {sizes.FONT_SM}px;
                min-height: 32px;
            }}
            QPushButton:hover {{
                background: {ColorsV2.GRADIENT_BLUE_HOVER};
            }}
            QPushButton:disabled {{
                background-color: {ColorsV2.BG_LIGHT};
                color: {ColorsV2.TEXT_MUTED};
            }}
        """

    def _compact_danger_style(self) -> str:
        sizes = get_sizes()
        return f"""
            QPushButton {{
                background-color: transparent;
                color: {ColorsV2.ACCENT_RED};
                border: 1px solid #5B3135;
                border-radius: {sizes.RADIUS_LG}px;
                padding: 7px 12px;
                font-weight: 700;
                font-size: {sizes.FONT_SM}px;
                min-height: 32px;
            }}
            QPushButton:hover {{
                background-color: #261418;
                border-color: {ColorsV2.ACCENT_RED};
            }}
            QPushButton:disabled {{
                color: {ColorsV2.TEXT_MUTED};
                border-color: {ColorsV2.BORDER_DEFAULT};
            }}
        """

    def _compact_tool_style(self) -> str:
        sizes = get_sizes()
        return f"""
            QPushButton {{
                background-color: {ColorsV2.BG_ELEVATED};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: {sizes.RADIUS_LG}px;
                padding: 7px 10px;
                color: {ColorsV2.TEXT_SECONDARY};
                font-size: {sizes.FONT_SM}px;
                font-weight: 700;
                min-height: 32px;
            }}
            QPushButton:hover {{
                background-color: {ColorsV2.BG_HOVER};
                border-color: {ColorsV2.BORDER_FOCUS};
                color: {ColorsV2.TEXT_PRIMARY};
            }}
        """

    def _compact_field_style(self) -> str:
        sizes = get_sizes()
        return f"""
            QLineEdit, QComboBox, QDateTimeEdit, QTimeEdit, QSpinBox {{
                background-color: {ColorsV2.BG_INPUT};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: {sizes.RADIUS_MD}px;
                padding: 5px 8px;
                color: {ColorsV2.TEXT_PRIMARY};
                min-height: 28px;
            }}
            QLineEdit:focus, QComboBox:focus, QDateTimeEdit:focus, QTimeEdit:focus, QSpinBox:focus {{
                border-color: {ColorsV2.BORDER_FOCUS};
            }}
        """

    def _card_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{ColorsV2.TEXT_SECONDARY}; font-size:12px;")
        return label

    def _prepare_button(self, button: QPushButton, minimum_width: int = 0) -> QPushButton:
        button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        button.setMinimumHeight(34)
        if minimum_width:
            button.setMinimumWidth(minimum_width)
        return button

    def _prepare_field(self, widget: QWidget, minimum_width: int = 0) -> QWidget:
        widget.setStyleSheet(self._compact_field_style())
        widget.setMinimumHeight(32)
        if minimum_width:
            widget.setMinimumWidth(minimum_width)
        return widget

    def _build_ui(self) -> None:
        sizes = get_sizes()

        # --- Корневой layout: просто держит QScrollArea ---
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Scroll area ---
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: transparent; border: none; }}"
            f"QScrollBar:vertical {{ background: {ColorsV2.BG_ELEVATED}; width: 8px; border-radius: 4px; }}"
            f"QScrollBar::handle:vertical {{ background: #444; border-radius: 4px; min-height: 20px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
            f"QScrollBar:horizontal {{ background: {ColorsV2.BG_ELEVATED}; height: 8px; border-radius: 4px; }}"
            f"QScrollBar::handle:horizontal {{ background: #444; border-radius: 4px; min-width: 20px; }}"
            f"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}"
        )
        root.addWidget(scroll)

        # --- Внутренний виджет со всем содержимым ---
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(sizes.SPACING_MD, sizes.SPACING_MD, sizes.SPACING_MD, sizes.SPACING_MD)
        inner_layout.setSpacing(sizes.SPACING_MD)
        scroll.setWidget(inner)

        top = QHBoxLayout()
        top.setSpacing(sizes.SPACING_SM)
        inner_layout.addLayout(top)

        top.addWidget(self._build_account_card(), 9)
        top.addWidget(self._build_automation_card(), 10)
        top.addWidget(self._build_publish_card(), 12)

        queue_card = self._card(t('yt_queue_card'))
        queue_layout = QVBoxLayout(queue_card)
        queue_layout.setSpacing(10)

        activity_row = QHBoxLayout()
        self.worker_state_label = QLabel(t('yt_uploader_stopped'))
        self.worker_state_label.setStyleSheet(f"color:{ColorsV2.TEXT_SECONDARY}; font-weight:700;")
        self.queue_summary_label = QLabel(t('yt_queue_summary').format(queued=0, uploading=0, done=0, failed=0))
        self.queue_summary_label.setStyleSheet(f"color:{ColorsV2.TEXT_SECONDARY};")
        self.next_upload_label = QLabel("")
        self.next_upload_label.setStyleSheet(f"color:{ColorsV2.TEXT_MUTED};")
        activity_row.addWidget(self.worker_state_label)
        activity_row.addSpacing(14)
        activity_row.addWidget(self.queue_summary_label)
        activity_row.addStretch(1)
        activity_row.addWidget(self.next_upload_label)
        queue_layout.addLayout(activity_row)

        toolbar = QHBoxLayout()
        self.btn_start = QPushButton(t('yt_upload_now'))
        self.btn_start.setStyleSheet(self._compact_primary_style())
        self._prepare_button(self.btn_start, 175)
        self.btn_stop = QPushButton(t('yt_stop'))
        self.btn_stop.setStyleSheet(self._compact_danger_style())
        self._prepare_button(self.btn_stop, 120)
        self.btn_stop.setEnabled(False)
        self.btn_add_files = QPushButton(t('yt_add_videos'))
        self.btn_add_files.setStyleSheet(self._compact_tool_style())
        self._prepare_button(self.btn_add_files, 120)
        self.btn_add_folder = QPushButton(t('yt_add_folder'))
        self.btn_add_folder.setStyleSheet(self._compact_tool_style())
        self._prepare_button(self.btn_add_folder, 120)
        self.btn_refresh = QPushButton(t('yt_refresh'))
        self.btn_refresh.setStyleSheet(self._compact_tool_style())
        self._prepare_button(self.btn_refresh, 95)
        self.btn_clear_done = QPushButton(t('yt_clear_done'))
        self.btn_clear_done.setStyleSheet(self._compact_tool_style())
        self._prepare_button(self.btn_clear_done, 120)
        self.btn_retry_failed = QPushButton(t('yt_retry_failed'))
        self.btn_retry_failed.setStyleSheet(self._compact_tool_style())
        self._prepare_button(self.btn_retry_failed, 125)
        self.btn_reschedule_pending = QPushButton(t('yt_reschedule'))
        self.btn_reschedule_pending.setStyleSheet(self._compact_tool_style())
        self._prepare_button(self.btn_reschedule_pending, 170)
        self.btn_reschedule_pending.setToolTip(t('yt_reschedule_hint'))
        self.btn_remove_selected = QPushButton(t('yt_remove_selected'))
        self.btn_remove_selected.setStyleSheet(self._compact_tool_style())
        self._prepare_button(self.btn_remove_selected, 135)
        for button in (
            self.btn_start,
            self.btn_stop,
            self.btn_add_files,
            self.btn_add_folder,
            self.btn_refresh,
            self.btn_clear_done,
            self.btn_retry_failed,
            self.btn_reschedule_pending,
            self.btn_remove_selected,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        queue_layout.addLayout(toolbar)

        self.queue_table = QTableWidget(0, 7)
        self.queue_table.setHorizontalHeaderLabels(
            [t('yt_table_video'), t('yt_table_type'), t('yt_table_language'),
             t('yt_table_upload_start'), t('yt_table_publish'), t('yt_table_status'),
             t('yt_table_result')]
        )
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.queue_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 6):
            self.queue_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.queue_table.setMinimumHeight(210)
        self.queue_table.setStyleSheet(
            f"""
            QTableWidget {{
                background-color: {ColorsV2.BG_INPUT};
                color: {ColorsV2.TEXT_PRIMARY};
                gridline-color: {ColorsV2.BORDER_DEFAULT};
                border: 1px solid {ColorsV2.BORDER_DEFAULT};
                border-radius: 8px;
            }}
            QHeaderView::section {{
                background-color: {ColorsV2.BG_ELEVATED};
                color: {ColorsV2.TEXT_SECONDARY};
                border: none;
                padding: 7px;
                font-weight: 600;
            }}
            """
        )
        queue_layout.addWidget(self.queue_table, 1)
        inner_layout.addWidget(queue_card, 1)

        log_card = self._card(t('yt_log_card'))
        log_layout = QVBoxLayout(log_card)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(90)
        self.log_text.setStyleSheet(
            f"QTextEdit {{ background:{ColorsV2.BG_INPUT}; color:{ColorsV2.TEXT_SECONDARY}; "
            f"border:1px solid {ColorsV2.BORDER_DEFAULT}; border-radius:8px; padding:8px; }}"
        )
        log_layout.addWidget(self.log_text)
        inner_layout.addWidget(log_card)

    def _build_account_card(self) -> QGroupBox:
        card = self._card(t('yt_account_card'))
        card.setMinimumWidth(300)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 18, 12, 12)
        layout.setSpacing(10)

        self.status_label = QLabel(t('yt_not_connected'))
        self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_ORANGE}; font-weight:600;")
        self.channel_label = QLabel(t('yt_channel_none'))
        self.channel_label.setStyleSheet(f"color:{ColorsV2.TEXT_SECONDARY};")
        self.auth_source_label = QLabel("")
        self.auth_source_label.setWordWrap(True)
        self.auth_source_label.setStyleSheet(f"color:{ColorsV2.TEXT_MUTED}; font-size:12px;")
        layout.addWidget(self.status_label)
        layout.addWidget(self.channel_label)
        layout.addWidget(self.auth_source_label)

        self.account_combo = QComboBox()
        self.account_combo.setToolTip(t('yt_active_account_hint'))
        self._prepare_field(self.account_combo)
        layout.addWidget(self.account_combo)

        self.btn_connect = QPushButton(t('yt_add_account'))
        self.btn_connect.setStyleSheet(self._compact_primary_style())
        self._prepare_button(self.btn_connect)
        layout.addWidget(self.btn_connect)

        action_row = QHBoxLayout()
        self.btn_check_account = QPushButton(t('yt_check'))
        self.btn_check_account.setStyleSheet(self._compact_tool_style())
        self._prepare_button(self.btn_check_account, 100)
        action_row.addWidget(self.btn_check_account)
        self.btn_disconnect = QPushButton(t('yt_remove'))
        self.btn_disconnect.setStyleSheet(self._compact_tool_style())
        self._prepare_button(self.btn_disconnect, 125)
        action_row.addWidget(self.btn_disconnect)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.client_secrets_input = QLineEdit(card)
        self.client_secrets_input.setVisible(False)

        note = QLabel(t('yt_multi_account_note'))
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{ColorsV2.TEXT_MUTED}; font-size:12px;")
        layout.addWidget(note)
        layout.addStretch(1)
        return card

    def _build_automation_card(self) -> QGroupBox:
        card = self._card(t('yt_automation_card'))
        card.setMinimumWidth(320)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 18, 12, 12)
        layout.setSpacing(10)

        self.enable_auto_upload_cb = QCheckBox(t('yt_full_auto'))
        self.enable_auto_upload_cb.setChecked(False)
        layout.addWidget(self.enable_auto_upload_cb)
        # Kept as an internal compatibility alias for previously saved settings.
        self.auto_start_cb = QCheckBox(self)
        self.auto_start_cb.hide()

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        self.start_delay_spin = self._spin(0, 1440, 15)
        self.start_delay_spin.setSuffix(t('yt_minutes_suffix'))
        self.start_delay_spin.setToolTip(t('yt_start_delay_hint'))
        self.wave_interval_spin = self._spin(0, 1440, 180)
        self.wave_interval_spin.setSuffix(t('yt_minutes_suffix'))
        self.wave_interval_spin.setToolTip(t('yt_wave_interval_hint'))
        self.parallel_uploads_spin = self._spin(1, MAX_PARALLEL_UPLOADS, 1)
        self.parallel_uploads_spin.setSuffix(t('yt_videos_suffix'))
        self.parallel_uploads_spin.setToolTip(t('yt_parallel_hint'))
        form.addRow(t('yt_start_after'), self.start_delay_spin)
        form.addRow(t('yt_wave_pause'), self.wave_interval_spin)
        form.addRow(t('yt_parallel'), self.parallel_uploads_spin)
        layout.addLayout(form)

        note = QLabel(t('yt_auto_note'))
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{ColorsV2.TEXT_MUTED}; font-size:12px;")
        layout.addWidget(note)
        layout.addStretch(1)
        return card

    def _build_publish_card(self) -> QGroupBox:
        card = self._card(t('yt_publish_card'))
        card.setMinimumWidth(360)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 18, 12, 12)
        layout.setSpacing(10)
        form = QFormLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.privacy_combo = QComboBox()
        self.privacy_combo.addItem(t('yt_mode_schedule'), "schedule")
        self.privacy_combo.addItem(t('yt_mode_private'), "private")
        self.privacy_combo.addItem(t('yt_mode_public'), "public")
        self._prepare_field(self.privacy_combo, 160)

        self.videos_per_day_spin = self._spin(1, 50, 3)
        self.publish_start_edit = QDateTimeEdit()
        self.publish_start_edit.setCalendarPopup(True)
        self.publish_start_edit.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.publish_start_edit.setDateTime(QDateTime.currentDateTime())
        self._prepare_field(self.publish_start_edit, 160)

        self.window_start_edit = QTimeEdit()
        self.window_start_edit.setDisplayFormat("HH:mm")
        self.window_start_edit.setTime(QTime(10, 0))
        self._prepare_field(self.window_start_edit, 95)
        self.window_end_edit = QTimeEdit()
        self.window_end_edit.setDisplayFormat("HH:mm")
        self.window_end_edit.setTime(QTime(22, 0))
        self._prepare_field(self.window_end_edit, 95)
        self.publish_jitter_spin = self._spin(0, 240, 60)
        self.publish_jitter_spin.setSuffix(t('yt_minutes_suffix'))
        self.publish_jitter_spin.setToolTip(t('yt_jitter_hint'))

        self.category_combo = QComboBox()
        for title_key, category_id in YOUTUBE_CATEGORIES:
            self.category_combo.addItem(t(title_key), category_id)
        self.category_combo.setCurrentIndex(self.category_combo.findData("24"))
        self._prepare_field(self.category_combo, 180)
        self.notify_subscribers_cb = QCheckBox(t('yt_notify'))
        self.made_for_kids_cb = QCheckBox(t('yt_kids'))
        self.synthetic_media_cb = QCheckBox(t('yt_synthetic'))
        self.synthetic_media_cb.setChecked(True)

        form.addRow(t('yt_mode'), self.privacy_combo)
        form.addRow(t('yt_per_day'), self.videos_per_day_spin)
        form.addRow(t('yt_start'), self.publish_start_edit)
        form.addRow(t('yt_window_from'), self.window_start_edit)
        form.addRow(t('yt_window_to'), self.window_end_edit)
        form.addRow(t('yt_publish_jitter'), self.publish_jitter_spin)
        form.addRow(t('yt_category'), self.category_combo)
        layout.addLayout(form)
        layout.addWidget(self.notify_subscribers_cb)
        layout.addWidget(self.made_for_kids_cb)
        layout.addWidget(self.synthetic_media_cb)
        layout.addStretch(1)
        return card

    def _card(self, title: str) -> QGroupBox:
        card = QGroupBox(title)
        card.setStyleSheet(get_group_box_style())
        return card

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        self._prepare_field(spin, 76)
        spin.setMaximumWidth(90)
        return spin

    def _connect_signals(self) -> None:
        self.btn_connect.clicked.connect(self.add_account)
        self.btn_check_account.clicked.connect(self.check_account)
        self.btn_disconnect.clicked.connect(self.disconnect_account)
        self.account_combo.currentIndexChanged.connect(self._on_account_selected)
        self.btn_start.clicked.connect(self.start_upload_now)
        self.btn_stop.clicked.connect(self._disable_automation)
        self.btn_add_files.clicked.connect(self.add_video_files)
        self.btn_add_folder.clicked.connect(self.add_video_folder)
        self.btn_refresh.clicked.connect(self.refresh_queue)
        self.btn_clear_done.clicked.connect(self.clear_completed)
        self.btn_retry_failed.clicked.connect(self.retry_failed)
        self.btn_reschedule_pending.clicked.connect(self.reschedule_pending)
        self.btn_remove_selected.clicked.connect(self.remove_selected_tasks)
        self.privacy_combo.currentIndexChanged.connect(self._update_publish_controls)
        self.enable_auto_upload_cb.toggled.connect(self._on_automation_toggled)

        widgets = [
            self.client_secrets_input,
            self.category_combo,
            self.enable_auto_upload_cb,
            self.start_delay_spin,
            self.wave_interval_spin,
            self.parallel_uploads_spin,
            self.privacy_combo,
            self.videos_per_day_spin,
            self.publish_start_edit,
            self.window_start_edit,
            self.window_end_edit,
            self.publish_jitter_spin,
            self.notify_subscribers_cb,
            self.made_for_kids_cb,
            self.synthetic_media_cb,
        ]
        for widget in widgets:
            signal = None
            if hasattr(widget, "textChanged"):
                signal = widget.textChanged
            elif hasattr(widget, "toggled"):
                signal = widget.toggled
            elif hasattr(widget, "valueChanged"):
                signal = widget.valueChanged
            elif hasattr(widget, "dateTimeChanged"):
                signal = widget.dateTimeChanged
            elif hasattr(widget, "timeChanged"):
                signal = widget.timeChanged
            elif hasattr(widget, "currentIndexChanged"):
                signal = widget.currentIndexChanged
            if signal is not None:
                signal.connect(self._emit_settings_changed)

    def _emit_settings_changed(self, *_) -> None:
        if self._loading:
            return
        self._update_auth_source_label(self._settings_from_ui())
        self.settings_changed.emit()

    def _on_automation_toggled(self, enabled: bool) -> None:
        self.auto_start_cb.setChecked(enabled)
        if self._loading:
            return
        if enabled:
            settings = self._sync_settings()
            released = self.queue.start_queued_now(settings)
            if released:
                self.log(t('runtime_yt_full_auto_released').format(count=released))
                self.refresh_queue()
            QTimer.singleShot(0, self._ensure_automation_running)
        elif self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn_stop.setEnabled(False)
            self.worker_state_label.setText(t('yt_uploader_stopping'))

    def _disable_automation(self, *_args) -> None:
        """Emergency stop: turn off full automation and stop the worker."""
        self.enable_auto_upload_cb.setChecked(False)
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn_stop.setEnabled(False)

    def _ensure_automation_running(self) -> None:
        """Keep the in-app queue worker alive while full automation is enabled."""
        settings = self._sync_settings()
        if not (settings.enabled and settings.auto_start):
            return
        if self.worker and self.worker.isRunning():
            return
        if not self._has_auth_material(settings):
            return
        released = self.queue.start_queued_now(settings)
        if released:
            self.refresh_queue()
        self.start_uploader()

    def _update_auth_source_label(self, settings: PublishSettings | None = None) -> None:
        settings = settings or self.settings
        source = localize_oauth_source(resolve_oauth_client_source(settings))
        login_source = localize_oauth_source(
            resolve_oauth_client_source(settings, include_token=False)
        )
        if source.has_token:
            color = ColorsV2.ACCENT_GREEN
            text = t('yt_auth_saved')
        elif source.ready:
            color = ColorsV2.ACCENT_CYAN
            text = t('yt_auth_browser_ready')
        elif source.source == "invalid_file":
            color = ColorsV2.ACCENT_RED
            text = t('yt_auth_invalid')
        else:
            color = ColorsV2.ACCENT_ORANGE
            text = t('yt_auth_unavailable')

        self.auth_source_label.setText(text)
        tooltip = source.message or (
            t('yt_auth_tooltip_ready')
            if source.ready
            else t('yt_auth_tooltip_unavailable')
        )
        if source.label:
            tooltip = f"{source.label}: {tooltip}"
        if source.path:
            tooltip = f"{tooltip}\n{source.path}".strip()
        self.auth_source_label.setToolTip(tooltip)
        self.auth_source_label.setStyleSheet(f"color:{color}; font-size:12px;")

        if hasattr(self, "btn_connect"):
            self.btn_connect.setEnabled(login_source.ready)
            self.btn_connect.setText(
                t('yt_add_account') if login_source.ready else t('yt_auth_unavailable')
            )
            self.btn_connect.setToolTip(
                ""
                if login_source.ready
                else t('yt_auth_tooltip_unavailable')
            )
        if hasattr(self, "btn_check_account"):
            self.btn_check_account.setEnabled(source.has_token)
        if hasattr(self, "btn_disconnect"):
            self.btn_disconnect.setEnabled(self.account_combo.count() > 0)

    def _update_publish_controls(self) -> None:
        is_schedule = self.privacy_combo.currentData() == "schedule"
        for widget in (
            self.videos_per_day_spin,
            self.publish_start_edit,
            self.window_start_edit,
            self.window_end_edit,
            self.publish_jitter_spin,
        ):
            widget.setEnabled(is_schedule)

    def _settings_from_ui(self) -> PublishSettings:
        start_delay = self.start_delay_spin.value()
        wave_interval = self.wave_interval_spin.value()
        full_auto = self.enable_auto_upload_cb.isChecked()
        settings = PublishSettings(
            enabled=full_auto,
            auto_start=full_auto,
            client_secrets_path=self.client_secrets_input.text().strip(),
            token_path=self.settings.token_path,
            queue_path=self.settings.queue_path,
            accounts_path=self.settings.accounts_path,
            active_account_id=self.settings.active_account_id,
            privacy_mode=self.privacy_combo.currentData() or "schedule",
            videos_per_day=self.videos_per_day_spin.value(),
            publish_start=self.publish_start_edit.dateTime().toPyDateTime().astimezone().isoformat(timespec="seconds"),
            publish_window_start=self.window_start_edit.time().toString("HH:mm"),
            publish_window_end=self.window_end_edit.time().toString("HH:mm"),
            publish_jitter_minutes=self.publish_jitter_spin.value(),
            first_upload_delay_min_minutes=start_delay,
            first_upload_delay_max_minutes=start_delay,
            upload_delay_min_minutes=wave_interval,
            upload_delay_max_minutes=wave_interval,
            max_parallel_uploads=self.parallel_uploads_spin.value(),
            notify_subscribers=self.notify_subscribers_cb.isChecked(),
            made_for_kids=self.made_for_kids_cb.isChecked(),
            contains_synthetic_media=self.synthetic_media_cb.isChecked(),
            category_id=str(self.category_combo.currentData() or "24"),
            channel_title=self._channel_title,
            channel_id=self._channel_id,
            last_channel_check=self._last_channel_check,
        )
        return settings

    def _sync_settings(self) -> PublishSettings:
        self.settings = self._settings_from_ui()
        if Path(self.queue.path) != Path(self.settings.queue_path):
            self.queue = YouTubePublishQueue(self.settings.queue_path)
        return self.settings

    def get_saved_state(self) -> Dict:
        return self._settings_from_ui().to_dict()

    def apply_saved_state(self, state: Dict) -> None:
        self._loading = True
        interrupted = 0
        try:
            self.settings = PublishSettings.from_dict(state)
            self.queue = YouTubePublishQueue(self.settings.queue_path)
            interrupted = self.queue.recover_interrupted_uploads()
            self.account_store = YouTubeAccountStore(self.settings.accounts_path)
            self._channel_title = self.settings.channel_title
            self._channel_id = self.settings.channel_id
            self._last_channel_check = self.settings.last_channel_check
            if (
                self._channel_id
                and has_saved_youtube_token(self.settings)
                and not self.account_store.get(self._channel_id)
            ):
                self.account_store.upsert_channel(
                    {
                        "channel_id": self._channel_id,
                        "channel_title": self._channel_title,
                    },
                    self.settings.token_path,
                )
            candidate_ids = [
                self.settings.active_account_id,
                self.account_store.active_account_id(),
                self._channel_id,
            ]
            candidate_ids.extend(
                str(account.get("id") or "")
                for account in self.account_store.accounts()
            )
            active_id = ""
            for candidate_id in dict.fromkeys(candidate_ids):
                account = self.account_store.get(candidate_id) if candidate_id else None
                if not account:
                    continue
                candidate_settings = PublishSettings(
                    token_path=str(account.get("token_path") or "")
                )
                if has_saved_youtube_token(candidate_settings):
                    active_id = candidate_id
                    break
            if not active_id:
                active_id = next((item for item in candidate_ids if item), "")
            active = self.account_store.set_active(active_id) if active_id else None
            if active:
                self.settings.active_account_id = str(active["id"])
                self.settings.token_path = str(active["token_path"])
                self._channel_id = str(active.get("channel_id") or "")
                self._channel_title = str(active.get("channel_title") or "")
            full_auto = self.settings.enabled and self.settings.auto_start
            self.enable_auto_upload_cb.setChecked(full_auto)
            self.auto_start_cb.setChecked(full_auto)
            self.client_secrets_input.setText(self.settings.client_secrets_path)
            legacy_start_range = (
                self.settings.first_upload_delay_min_minutes
                != self.settings.first_upload_delay_max_minutes
            )
            legacy_wave_range = (
                self.settings.upload_delay_min_minutes
                != self.settings.upload_delay_max_minutes
            )
            self.start_delay_spin.setValue(
                0 if legacy_start_range else self.settings.first_upload_delay_min_minutes
            )
            self.wave_interval_spin.setValue(
                0 if legacy_wave_range else self.settings.upload_delay_min_minutes
            )
            self.parallel_uploads_spin.setValue(self.settings.max_parallel_uploads)
            index = self.privacy_combo.findData(self.settings.privacy_mode)
            self.privacy_combo.setCurrentIndex(max(0, index))
            self.videos_per_day_spin.setValue(self.settings.videos_per_day)
            # The start date must follow the computer clock on every launch.
            # A date persisted months ago would otherwise silently send a new
            # batch into the distant future.
            self.publish_start_edit.setDateTime(QDateTime.currentDateTime())
            self.window_start_edit.setTime(QTime.fromString(self.settings.publish_window_start, "HH:mm"))
            self.window_end_edit.setTime(QTime.fromString(self.settings.publish_window_end, "HH:mm"))
            self.publish_jitter_spin.setValue(self.settings.publish_jitter_minutes)
            category_index = self.category_combo.findData(self.settings.category_id)
            if category_index < 0:
                self.category_combo.addItem(
                    t('yt_category_other').format(id=self.settings.category_id),
                    self.settings.category_id,
                )
                category_index = self.category_combo.count() - 1
            self.category_combo.setCurrentIndex(max(0, category_index))
            self.notify_subscribers_cb.setChecked(self.settings.notify_subscribers)
            self.made_for_kids_cb.setChecked(self.settings.made_for_kids)
            self.synthetic_media_cb.setChecked(self.settings.contains_synthetic_media)
        finally:
            self._loading = False
        self._refresh_account_combo()
        self._update_publish_controls()
        self._render_cached_channel()
        self.refresh_queue()
        if interrupted:
            self.log(t('yt_interrupted_restored').format(count=interrupted))
        QTimer.singleShot(0, self._ensure_automation_running)

    def _render_cached_channel(self) -> None:
        if self._channel_title or self._channel_id:
            if has_saved_youtube_token(self.settings):
                self.status_label.setText(t('yt_connected'))
                self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_GREEN}; font-weight:600;")
            else:
                self.status_label.setText(t('yt_google_login_required'))
                self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_ORANGE}; font-weight:600;")
            self.channel_label.setText(t('yt_channel').format(title=self._channel_title or '-', id=self._channel_id or '-'))
        else:
            self.status_label.setText(t('yt_not_connected'))
            self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_ORANGE}; font-weight:600;")
            self.channel_label.setText(t('yt_channel_none'))
        self._update_auth_source_label(self.settings)

    def _refresh_account_combo(self) -> None:
        active_id = self.settings.active_account_id or self.account_store.active_account_id()
        accounts = self.account_store.accounts()
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        for account in accounts:
            title = account.get("channel_title") or account.get("channel_id") or t('yt_channel_default')
            account_settings = PublishSettings(
                token_path=str(account.get("token_path") or "")
            )
            if not has_saved_youtube_token(account_settings):
                title = t('yt_account_login_required').format(title=title)
            self.account_combo.addItem(str(title), str(account.get("id") or ""))
        index = self.account_combo.findData(active_id)
        if index < 0 and self.account_combo.count():
            index = 0
        self.account_combo.setCurrentIndex(index)
        self.account_combo.setVisible(bool(accounts))
        self.account_combo.blockSignals(False)
        self._update_auth_source_label(self.settings)

    def _on_account_selected(self, _index: int) -> None:
        if self._loading:
            return
        account_id = str(self.account_combo.currentData() or "")
        if not account_id:
            return
        if self.worker and self.worker.isRunning():
            show_warning(self, "YouTube", t('yt_stop_before_switch'))
            self._refresh_account_combo()
            return
        account = self.account_store.set_active(account_id)
        if not account:
            return
        self.settings.active_account_id = account_id
        self.settings.token_path = str(account.get("token_path") or self.settings.token_path)
        self._channel_id = str(account.get("channel_id") or "")
        self._channel_title = str(account.get("channel_title") or "")
        self._last_channel_check = str(account.get("last_used_at") or "")
        self._render_cached_channel()
        self.log(t('yt_active_channel_log').format(channel=self._channel_title or self._channel_id))
        self.settings_changed.emit()

    def _has_auth_material(self, settings: PublishSettings) -> bool:
        return has_saved_youtube_token(settings)

    def add_account(self) -> None:
        if self.worker and self.worker.isRunning():
            show_warning(self, "YouTube", t('yt_stop_before_add_account'))
            return
        self._pending_token_path = str(self.account_store.token_path_for_new_account())
        self._start_account_connection(force_account_selection=True)

    def check_account(self) -> None:
        self._pending_token_path = ""
        self._start_account_connection(force_account_selection=False)

    def _start_account_connection(self, force_account_selection: bool) -> None:
        if self.connect_worker and self.connect_worker.isRunning():
            self.log(t('yt_login_in_progress'))
            return
        if not self.client_secrets_input.text().strip():
            auto_source = resolve_oauth_client_source(self._settings_from_ui(), include_token=False)
            if auto_source.source == "auto_file" and auto_source.path:
                self.client_secrets_input.setText(str(Path(auto_source.path).resolve()))
                self.log(t('yt_login_auto_found'))

        settings = self._sync_settings()
        if force_account_selection:
            settings.token_path = self._pending_token_path
        auth_source = resolve_oauth_client_source(
            settings, include_token=not force_account_selection
        )
        if not auth_source.ready:
            self._update_auth_source_label(settings)
            self.log(t('yt_oauth_missing'))
            return
            
        self.btn_connect.setEnabled(False)
        self.btn_check_account.setEnabled(False)
        self.status_label.setText(t('yt_checking_saved_login') if auth_source.has_token else t('yt_opening_browser'))
        self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_CYAN}; font-weight:600;")
        self.log(
            t('yt_opening_new_account')
            if force_account_selection
            else t('yt_checking_youtube_login')
        )
        
        self.connect_worker = YouTubeConnectWorker(
            settings,
            self,
            force_account_selection=force_account_selection,
        )
        self.connect_worker.result.connect(self._on_connect_finished)
        self.connect_worker.error.connect(self._on_connect_error)
        self.connect_worker.finished.connect(self._on_connect_worker_stopped)
        self.connect_worker.start()

    def _on_connect_finished(self, info: dict) -> None:
        self.btn_connect.setEnabled(True)
        self.btn_check_account.setEnabled(True)
        if info.get("connected"):
            self._channel_title = info.get("channel_title", "")
            self._channel_id = info.get("channel_id", "")
            self._last_channel_check = datetime.now().astimezone().isoformat(timespec="seconds")
            connected_token_path = self._pending_token_path or self.settings.token_path
            connected_settings = PublishSettings(token_path=connected_token_path)
            if connected_token_path and has_saved_youtube_token(connected_settings):
                account = self.account_store.upsert_channel(info, connected_token_path)
                self.settings.active_account_id = str(account["id"])
                self.settings.token_path = str(account["token_path"])
                self._pending_token_path = ""
                self._refresh_account_combo()
            self.status_label.setText(t('yt_connected'))
            self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_GREEN}; font-weight:600;")
            self.channel_label.setText(t('yt_channel').format(title=self._channel_title, id=self._channel_id))
            self._update_auth_source_label(self._settings_from_ui())
            self.log(t('yt_channel_accessible').format(channel=self._channel_title))
            self.settings_changed.emit()
            # Запустить загрузчик автоматически, если включён «Полный автомат»
            QTimer.singleShot(500, self._ensure_automation_running)
        else:
            self._on_connect_error(
                localize_youtube_error(
                    info.get("error") or t('yt_channel_not_found'),
                    code=str(info.get("error_code") or ""),
                )
            )

    def _on_connect_error(self, error: str) -> None:
        self.btn_connect.setEnabled(True)
        self.btn_check_account.setEnabled(True)
        if self._pending_token_path:
            pending = Path(self._pending_token_path)
            if pending.is_file() or pending.with_suffix(pending.suffix + ".backup").is_file():
                self.settings.token_path = str(pending)
                self.log(t('yt_login_saved_check_incomplete'))
            self._pending_token_path = ""
        self.status_label.setText(t('yt_connection_error'))
        self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_RED}; font-weight:600;")
        self.channel_label.setText(
            t('yt_channel').format(title=self._channel_title, id=self._channel_id)
            if self._channel_id else t('yt_channel_none')
        )
        self._update_auth_source_label(self.settings)
        self.log(t('yt_auth_error').format(error=error))
        show_warning(self, "YouTube", t('yt_auth_error').format(error=error))

    def _on_connect_worker_stopped(self) -> None:
        worker = self.sender()
        if worker is not None:
            worker.deleteLater()
        if worker is self.connect_worker:
            self.connect_worker = None
        self.btn_connect.setEnabled(True)
        self.btn_check_account.setEnabled(True)

    def disconnect_account(self) -> None:
        if self.worker and self.worker.isRunning():
            show_warning(self, "YouTube", t('yt_stop_before_switch'))
            return

        account_id = str(self.account_combo.currentData() or self.settings.active_account_id or "")
        if not account_id:
            return
        removed_title = self._channel_title or self._channel_id
        if not self.account_store.remove(account_id, delete_token=True):
            return
        next_id = self.account_store.active_account_id()
        next_account = self.account_store.set_active(next_id) if next_id else None
        if next_account:
            self.settings.active_account_id = str(next_account["id"])
            self.settings.token_path = str(next_account["token_path"])
            self._channel_title = str(next_account.get("channel_title") or "")
            self._channel_id = str(next_account.get("channel_id") or "")
        else:
            self.settings.active_account_id = ""
            self.settings.token_path = str(PublishSettings().token_path)
            self._channel_title = ""
            self._channel_id = ""
        self._last_channel_check = ""
        self._refresh_account_combo()
        self._render_cached_channel()
        self.log(t('yt_account_removed_log').format(account=removed_title))
        self.settings_changed.emit()

    def start_uploader(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        settings = self._sync_settings()
        if not self._has_auth_material(settings):
            self.status_label.setText(t('yt_google_login_required'))
            self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_ORANGE}; font-weight:600;")
            show_warning(
                self,
                "YouTube",
                t('yt_login_help'),
            )
            return
        self.worker = YouTubeUploadWorker(settings)
        self.worker.log.connect(self.log)
        self.worker.queue_changed.connect(self.refresh_queue)
        self.worker.task_progress.connect(self._on_task_progress)
        self.worker.activity.connect(self._on_worker_activity)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()
        self.worker_state_label.setText(t('yt_uploader_running'))
        self.worker_state_label.setStyleSheet(f"color:{ColorsV2.ACCENT_CYAN}; font-weight:700;")
        self.btn_stop.setEnabled(True)
        self.refresh_queue()

    def start_upload_now(self) -> None:
        """Release queued videos immediately and ensure the in-app worker is running."""
        settings = self._sync_settings()
        if not self._has_auth_material(settings):
            self.start_uploader()
            return
        released = self.queue.start_queued_now(settings)
        if released:
            self.log(t('runtime_yt_queue_released').format(
                count=released,
                parallel=settings.max_parallel_uploads,
            ))
        else:
            self.log(t('runtime_yt_queue_nothing_waiting'))
        self.refresh_queue()
        self.start_uploader()

    def stop_uploader(self, wait_ms: int = 3000) -> bool:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn_stop.setEnabled(False)
            if not self.worker.wait(wait_ms):
                self.log(t('runtime_yt_stop_waiting_network'))
                return False
        self._on_worker_finished()
        return True

    def stop_account_connection(self, wait_ms: int = 3000) -> bool:
        worker = self.connect_worker
        if worker is None:
            return True
        if worker.isRunning():
            worker.requestInterruption()
            self.btn_connect.setEnabled(False)
            self.btn_check_account.setEnabled(False)
            if not worker.wait(wait_ms):
                self.log(t('runtime_yt_login_browser_open'))
                return False
        try:
            worker.blockSignals(True)
            worker.deleteLater()
        except RuntimeError:
            pass
        if worker is self.connect_worker:
            self.connect_worker = None
        self.btn_connect.setEnabled(True)
        self.btn_check_account.setEnabled(True)
        return True

    def _on_worker_finished(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.worker_state_label.setText(t('yt_uploader_stopped'))
        self.worker_state_label.setStyleSheet(f"color:{ColorsV2.TEXT_SECONDARY}; font-weight:700;")
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        self.refresh_queue()

    def _on_task_progress(self, task_id: str, percent: int) -> None:
        self._progress[task_id] = percent
        self.refresh_queue()

    def _on_worker_activity(self, event: str, payload: Dict) -> None:
        """Forward locale-neutral upload activity with a useful display title."""
        details = dict(payload or {})
        if not details.get("title"):
            task_id = str(details.get("task_id") or "")
            if task_id:
                task = next(
                    (item for item in self.queue.tasks() if str(item.get("id") or "") == task_id),
                    None,
                )
                if task:
                    details["title"] = str(
                        task.get("title")
                        or Path(str(task.get("video_path") or "")).name
                        or ""
                    )
        self.upload_activity.emit(str(event), details)

    def add_video_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            t('yt_select_upload_videos'),
            "",
            f"{t('video_files_filter')};;{t('all_files_filter')}",
        )
        if files:
            self._enqueue_video_paths(files)

    def add_video_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t('yt_select_upload_folder'))
        if not folder:
            return
        paths = sorted(
            str(path)
            for path in Path(folder).rglob("*")
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
        self._enqueue_video_paths(paths)

    def _enqueue_video_paths(self, paths: List[str]) -> None:
        settings = self._sync_settings()
        packages = [build_package_from_video(path) for path in paths]
        added = self.queue.enqueue_packages(packages, settings)
        self.log(t('runtime_yt_added_to_queue').format(count=len(added)))
        self.refresh_queue()
        if settings.auto_start:
            self.start_uploader()

    def handle_generated_package(self, package: Dict) -> None:
        settings = self._sync_settings()
        if not settings.enabled:
            return
        try:
            task = self.queue.enqueue_package(package, settings)
            self.log(t('runtime_yt_generated_added').format(title=task.get('title') or ''))
            self.refresh_queue()
            if settings.auto_start:
                self.start_uploader()
        except Exception as error:
            self.log(t('runtime_yt_generated_add_failed').format(error=error))

    def clear_completed(self) -> None:
        removed = self.queue.clear_completed()
        self.log(t('runtime_yt_completed_removed').format(count=removed))
        self.refresh_queue()

    def retry_failed(self) -> None:
        settings = self._sync_settings()
        retried = self.queue.retry_failed(settings)
        self.log(t('runtime_yt_errors_requeued').format(count=retried))
        self.refresh_queue()
        if retried and settings.auto_start:
            self.start_uploader()

    def reschedule_pending(self) -> None:
        if self.worker and self.worker.isRunning():
            show_warning(
                self,
                "YouTube",
                t('queue_stop_before_reschedule'),
            )
            return
        settings = self._sync_settings()
        moved = self.queue.reschedule_pending_publish_times(settings)
        self.log(t('runtime_yt_pending_rescheduled').format(count=moved))
        self.refresh_queue()

    def remove_selected_tasks(self) -> None:
        rows = self.queue_table.selectionModel().selectedRows()
        task_ids = []
        for index in rows:
            row = index.row()
            if 0 <= row < len(self._visible_task_ids):
                task_ids.append(self._visible_task_ids[row])
        if not task_ids:
            self.log(t('runtime_yt_none_selected_remove'))
            return
        removed = self.queue.remove_tasks(task_ids)
        self.log(t('runtime_yt_removed_tasks').format(count=removed))
        self.refresh_queue()

    def refresh_queue(self) -> None:
        self.queue.reload()
        tasks = self.queue.tasks()
        queued_tasks = [task for task in tasks if task.get("status") == "queued"]
        uploading_count = sum(1 for task in tasks if task.get("status") == "uploading")
        completed_count = sum(1 for task in tasks if task.get("status") in FINAL_STATUSES)
        failed_count = sum(1 for task in tasks if task.get("status") == "failed")
        self.queue_summary_label.setText(t('yt_queue_summary').format(
            queued=len(queued_tasks), uploading=uploading_count,
            done=completed_count, failed=failed_count,
        ))

        worker_running = bool(self.worker and self.worker.isRunning())
        if worker_running and uploading_count:
            self.worker_state_label.setText(t('yt_uploading'))
            self.worker_state_label.setStyleSheet(f"color:{ColorsV2.ACCENT_GREEN}; font-weight:700;")
        elif worker_running:
            self.worker_state_label.setText(t('yt_uploader_running'))
            self.worker_state_label.setStyleSheet(f"color:{ColorsV2.ACCENT_CYAN}; font-weight:700;")
        else:
            self.worker_state_label.setText(t('yt_uploader_stopped'))
            self.worker_state_label.setStyleSheet(f"color:{ColorsV2.TEXT_SECONDARY}; font-weight:700;")

        next_times = [
            parse_datetime(task.get("upload_after"))
            for task in queued_tasks
            if parse_datetime(task.get("upload_after"))
        ]
        if uploading_count:
            self.next_upload_label.setText(t('yt_now_uploading').format(count=uploading_count))
        elif next_times:
            next_time = min(next_times)
            if next_time <= datetime.now().astimezone():
                self.next_upload_label.setText(t('yt_next_ready'))
            else:
                self.next_upload_label.setText(t('yt_next_batch').format(time=next_time.strftime('%H:%M')))
        else:
            self.next_upload_label.setText("")

        self._visible_task_ids = [str(task.get("id", "")) for task in tasks]
        self.queue_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            video_name = Path(str(task.get("video_path", ""))).name
            upload_after = self._format_dt(task.get("upload_after"))
            publish_at = self._format_dt(task.get("publish_at"))
            status = self._status_text(task)
            raw_info = task.get("youtube_url") or task.get("error") or ""
            info = self._queue_info_text(task, raw_info)
            values = [
                video_name,
                "Shorts" if task.get("video_kind") == "short" else t('runtime_yt_kind_long'),
                _localized_content_language(task.get("language")),
                upload_after,
                publish_at or "-",
                status,
                info,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, task.get("id", ""))
                item.setToolTip(str(raw_info if column == 6 and raw_info else value))
                if task.get("status") == "failed":
                    item.setForeground(Qt.red)
                elif task.get("status") in {"uploaded", "scheduled", "completed"}:
                    item.setForeground(Qt.green)
                self.queue_table.setItem(row, column, item)

    def pending_upload_count(self) -> int:
        self.queue.reload()
        return sum(1 for task in self.queue.tasks() if task.get("status") in ACTIVE_STATUSES)

    def has_pending_uploads(self) -> bool:
        return self.pending_upload_count() > 0

    def _status_text(self, task: Dict) -> str:
        status = task.get("status", "queued")
        if status == "queued":
            return t('yt_status_queued')
        if status == "uploading":
            return t('yt_status_uploading').format(progress=self._progress.get(task.get('id', ''), 0))
        if status == "failed":
            return t('yt_status_failed')
        if status == "scheduled":
            return t('yt_status_scheduled')
        if status == "uploaded":
            return t('yt_status_uploaded')
        return str(status)

    def _format_dt(self, value) -> str:
        dt = parse_datetime(value)
        if not dt:
            return ""
        return dt.strftime("%d.%m.%Y %H:%M")

    @staticmethod
    def _queue_info_text(task: Dict, raw_info: str) -> str:
        if task.get("youtube_url"):
            return raw_info
        error_text = raw_info.casefold()
        if is_quota_exhausted_error(raw_info) or (
            "quota exceeded" in error_text and "video uploads" in error_text
        ):
            return t('runtime_yt_quota_exhausted')
        if is_upload_limit_error(raw_info):
            return t('runtime_yt_channel_limit')
        if task.get("error_code") == YOUTUBE_ERROR_RETRY_SCHEDULED or raw_info.startswith(
            ("Автоповтор после", "Automatic retry scheduled:")
        ):
            return t('runtime_yt_retry_scheduled')
        return localize_queue_error(task, raw_info)

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")
        self.log_message.emit(message)

    def closeEvent(self, event) -> None:
        if not self.stop_account_connection(10000):
            event.ignore()
            return
        if not self.stop_uploader(10000):
            event.ignore()
            return
        super().closeEvent(event)
