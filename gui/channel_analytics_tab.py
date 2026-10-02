#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional channel analytics UI."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.channel_analytics import (
    ChannelAnalyticsAccountStore,
    ChannelAnalyticsSettings,
    YouTubeAnalyticsClient,
    analytics_token_is_valid,
    channel_snapshot_path,
    import_analytics_csv,
    load_snapshot,
    save_snapshot,
)
from core.youtube_publisher import (
    PublishSettings,
    resolve_oauth_client_source,
)
from gui.styles_v2 import ColorsV2, get_group_box_style, get_sizes
from gui.localized_dialogs import ask_yes_no, show_warning
from gui.translations import t
from gui.youtube_core_localization import (
    localize_youtube_error,
)


class AnalyticsConnectWorker(QThread):
    result = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, settings: ChannelAnalyticsSettings, parent=None):
        super().__init__(parent)
        self.settings = settings

    def run(self) -> None:
        try:
            info = YouTubeAnalyticsClient(self.settings).channel_info(
                allow_interactive=True,
                force_account_selection=True,
            )
            self.result.emit(info)
        except Exception as error:
            self.error.emit(localize_youtube_error(error))


class AnalyticsSyncWorker(QThread):
    result = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, settings: ChannelAnalyticsSettings, parent=None):
        super().__init__(parent)
        self.settings = settings

    def run(self) -> None:
        try:
            self.result.emit(YouTubeAnalyticsClient(self.settings).fetch_snapshot())
        except Exception as error:
            self.error.emit(localize_youtube_error(error))


class ChannelAnalyticsTab(QWidget):
    settings_changed = pyqtSignal()
    log_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = ChannelAnalyticsSettings()
        self.account_store = ChannelAnalyticsAccountStore(self.settings.accounts_path)
        self.connect_worker: AnalyticsConnectWorker | None = None
        self.sync_worker: AnalyticsSyncWorker | None = None
        self._loading = False
        self._pending_token_path = ""
        self._build_ui()
        self._load_snapshot_if_present()
        self._update_controls()

    def _build_ui(self) -> None:
        sizes = get_sizes()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(sizes.SPACING_XL, sizes.SPACING_LG, sizes.SPACING_XL, sizes.SPACING_XL)
        layout.setSpacing(sizes.SPACING_MD)

        title = QLabel(t('analytics_title'))
        title.setStyleSheet(f"font-size:22px; font-weight:700; color:{ColorsV2.TEXT_PRIMARY};")
        layout.addWidget(title)
        description = QLabel(t('analytics_description'))
        description.setWordWrap(True)
        description.setStyleSheet(f"color:{ColorsV2.TEXT_SECONDARY};")
        layout.addWidget(description)

        consent = QGroupBox(t('analytics_privacy_title'))
        consent.setStyleSheet(get_group_box_style())
        consent_layout = QVBoxLayout(consent)
        privacy = QLabel(t('analytics_privacy'))
        privacy.setWordWrap(True)
        privacy.setStyleSheet(f"color:{ColorsV2.TEXT_SECONDARY};")
        consent_layout.addWidget(privacy)
        self.enable_cb = QCheckBox(t('analytics_enable'))
        self.use_generation_cb = QCheckBox(t('analytics_use_generation'))
        self.use_generation_cb.setToolTip(t('analytics_use_generation_hint'))
        self.auto_sync_cb = QCheckBox(t('analytics_auto_sync'))
        consent_layout.addWidget(self.enable_cb)
        consent_layout.addWidget(self.use_generation_cb)
        consent_layout.addWidget(self.auto_sync_cb)
        layout.addWidget(consent)

        connection = QGroupBox(t('analytics_account'))
        connection.setStyleSheet(get_group_box_style())
        grid = QGridLayout(connection)
        self.account_combo = QComboBox()
        self.btn_connect = QPushButton(t('analytics_add_account'))
        self.btn_disconnect = QPushButton(t('analytics_disconnect'))
        self.days_spin = QSpinBox()
        self.days_spin.setRange(7, 365)
        self.days_spin.setValue(90)
        self.days_spin.setSuffix(t('analytics_days_suffix'))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(10, 200)
        self.limit_spin.setValue(100)
        grid.addWidget(QLabel(t('analytics_account')), 0, 0)
        grid.addWidget(self.account_combo, 0, 1, 1, 3)
        grid.addWidget(self.btn_connect, 1, 0, 1, 2)
        grid.addWidget(self.btn_disconnect, 1, 2, 1, 2)
        grid.addWidget(QLabel(t('analytics_period')), 2, 0)
        grid.addWidget(self.days_spin, 2, 1)
        grid.addWidget(QLabel(t('analytics_video_limit')), 2, 2)
        grid.addWidget(self.limit_spin, 2, 3)
        action_row = QHBoxLayout()
        self.btn_sync = QPushButton(t('analytics_sync'))
        self.btn_import = QPushButton(t('analytics_import_csv'))
        action_row.addWidget(self.btn_sync)
        action_row.addWidget(self.btn_import)
        grid.addLayout(action_row, 3, 0, 1, 4)
        self.status_label = QLabel(t('analytics_status_disabled'))
        self.status_label.setWordWrap(True)
        grid.addWidget(self.status_label, 4, 0, 1, 4)
        layout.addWidget(connection)

        results = QGroupBox(t('analytics_insights'))
        results.setStyleSheet(get_group_box_style())
        results_layout = QVBoxLayout(results)
        self.summary_label = QLabel(t('analytics_no_data'))
        self.summary_label.setWordWrap(True)
        results_layout.addWidget(self.summary_label)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            t('analytics_table_title'),
            t('analytics_table_views'),
            t('analytics_table_engaged'),
            t('analytics_table_retention'),
            t('analytics_table_likes'),
            t('analytics_table_shares'),
            t('analytics_table_subs'),
            t('analytics_table_score'),
        ])
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(sizes.scale(300))
        results_layout.addWidget(self.table)
        self.insights = QTextEdit()
        self.insights.setReadOnly(True)
        self.insights.setMinimumHeight(sizes.scale(115))
        results_layout.addWidget(self.insights)
        layout.addWidget(results)
        layout.addStretch(1)

        scroll.setWidget(body)
        root.addWidget(scroll)

        self.enable_cb.toggled.connect(self._on_enabled_changed)
        self.use_generation_cb.toggled.connect(self._emit_changed)
        self.auto_sync_cb.toggled.connect(self._emit_changed)
        self.days_spin.valueChanged.connect(self._emit_changed)
        self.limit_spin.valueChanged.connect(self._emit_changed)
        self.account_combo.currentIndexChanged.connect(self._on_account_selected)
        self.btn_connect.clicked.connect(self.connect_account)
        self.btn_disconnect.clicked.connect(self.disconnect_account)
        self.btn_sync.clicked.connect(self.sync_now)
        self.btn_import.clicked.connect(self.import_csv)

    def _emit_changed(self, *_) -> None:
        if not self._loading:
            self.settings_changed.emit()

    def _on_enabled_changed(self, _enabled: bool) -> None:
        if self._loading:
            return
        self._update_controls()
        self._emit_changed()

    def _settings_from_ui(self) -> ChannelAnalyticsSettings:
        self.settings.enabled = self.enable_cb.isChecked()
        self.settings.use_for_generation = self.use_generation_cb.isChecked()
        self.settings.auto_sync = self.auto_sync_cb.isChecked()
        self.settings.sync_days = self.days_spin.value()
        self.settings.max_videos = self.limit_spin.value()
        return ChannelAnalyticsSettings.from_dict(self.settings.to_dict())

    def get_saved_state(self) -> Dict:
        return self._settings_from_ui().to_dict()

    def apply_saved_state(self, state: Dict) -> None:
        self._loading = True
        try:
            self.settings = ChannelAnalyticsSettings.from_dict(state)
            self.account_store = ChannelAnalyticsAccountStore(self.settings.accounts_path)
            self.enable_cb.setChecked(self.settings.enabled)
            self.use_generation_cb.setChecked(self.settings.use_for_generation)
            self.auto_sync_cb.setChecked(self.settings.auto_sync)
            self.days_spin.setValue(self.settings.sync_days)
            self.limit_spin.setValue(self.settings.max_videos)
            self._refresh_accounts()
            self._load_snapshot_if_present()
        finally:
            self._loading = False
        self._update_controls()
        if self.settings.enabled and self.settings.auto_sync and analytics_token_is_valid(self.settings):
            QTimer.singleShot(1500, self.sync_now)

    def _refresh_accounts(self) -> None:
        active = self.settings.active_account_id or self.account_store.active_account_id()
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        for account in self.account_store.accounts():
            self.account_combo.addItem(
                str(account.get('channel_title') or account.get('channel_id') or '-'),
                str(account.get('id') or ''),
            )
        index = self.account_combo.findData(active)
        if index < 0 and self.account_combo.count():
            index = 0
        self.account_combo.setCurrentIndex(index)
        self.account_combo.blockSignals(False)
        if index >= 0:
            account = self.account_store.set_active(str(self.account_combo.itemData(index) or ''))
            if account:
                self._apply_account(account)
                self._load_active_channel_snapshot()
        elif not self.account_store.accounts():
            self._clear_snapshot_view()

    def _apply_account(self, account: Dict) -> None:
        self.settings.active_account_id = str(account.get('id') or '')
        self.settings.channel_id = str(account.get('channel_id') or '')
        self.settings.channel_title = str(account.get('channel_title') or '')
        self.settings.token_path = str(account.get('token_path') or self.settings.token_path)

    def _load_active_channel_snapshot(self) -> None:
        if not self.settings.channel_id:
            return
        archived = load_snapshot(channel_snapshot_path(
            self.settings.snapshot_path,
            self.settings.channel_id,
        ))
        if archived:
            save_snapshot(archived, self.settings.snapshot_path)
            self.render_snapshot(archived)
        else:
            self._clear_snapshot_view()

    def _on_account_selected(self, _index: int) -> None:
        if self._loading:
            return
        account = self.account_store.set_active(str(self.account_combo.currentData() or ''))
        if account:
            self._apply_account(account)
            self._load_active_channel_snapshot()
            self._update_controls()
            self.settings_changed.emit()

    def _update_controls(self) -> None:
        enabled = self.enable_cb.isChecked()
        token_ready = analytics_token_is_valid(self._settings_from_ui())
        busy = bool(
            (self.connect_worker and self.connect_worker.isRunning())
            or (self.sync_worker and self.sync_worker.isRunning())
        )
        self.use_generation_cb.setEnabled(enabled)
        self.auto_sync_cb.setEnabled(enabled)
        self.account_combo.setEnabled(enabled and not busy)
        self.btn_connect.setEnabled(enabled and not busy and self._oauth_ready())
        self.btn_disconnect.setEnabled(enabled and not busy and self.account_combo.count() > 0)
        self.btn_sync.setEnabled(enabled and token_ready and not busy)
        self.btn_import.setEnabled(enabled and not busy)
        self.days_spin.setEnabled(enabled and not busy)
        self.limit_spin.setEnabled(enabled and not busy)
        if busy:
            self.status_label.setText(t('analytics_status_working'))
            self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_CYAN};")
        elif not enabled:
            self.status_label.setText(t('analytics_status_disabled'))
            self.status_label.setStyleSheet(f"color:{ColorsV2.TEXT_SECONDARY};")
        elif self.settings.last_error:
            self.status_label.setText(t('analytics_error').format(error=self.settings.last_error))
            self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_RED};")
        elif token_ready:
            self.status_label.setText(t('analytics_status_ready'))
            self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_GREEN};")
        else:
            self.status_label.setText(t('analytics_status_missing'))
            self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_ORANGE};")

    def _oauth_ready(self) -> bool:
        publish = PublishSettings(client_secrets_path=self.settings.client_secrets_path)
        return resolve_oauth_client_source(publish, include_token=False).ready

    def connect_account(self) -> None:
        if self.connect_worker and self.connect_worker.isRunning():
            self.log_message.emit(t('analytics_login_running'))
            return
        if not self._oauth_ready():
            show_warning(self, t('tab_analytics'), t('analytics_oauth_missing'))
            return
        self._pending_token_path = str(self.account_store.token_path_for_new_account())
        self.settings.last_error = ''
        settings = self._settings_from_ui()
        settings.token_path = self._pending_token_path
        self.connect_worker = AnalyticsConnectWorker(settings, self)
        self.connect_worker.result.connect(self._on_connected)
        self.connect_worker.error.connect(self._on_error)
        self.connect_worker.finished.connect(self._on_connect_finished)
        self.connect_worker.start()
        self._update_controls()

    def _on_connected(self, info: Dict) -> None:
        if not info.get('connected'):
            self._on_error(t('analytics_oauth_missing'))
            return
        account = self.account_store.upsert_channel(info, self._pending_token_path)
        self._pending_token_path = ''
        self._apply_account(account)
        self._refresh_accounts()
        self.status_label.setText(t('analytics_status_connected').format(
            channel=self.settings.channel_title or self.settings.channel_id
        ))
        self.settings_changed.emit()

    def _on_connect_finished(self) -> None:
        worker = self.sender()
        if worker is self.connect_worker:
            self.connect_worker = None
        if worker is not None:
            worker.deleteLater()
        self._update_controls()

    def disconnect_account(self) -> None:
        account_id = str(self.account_combo.currentData() or self.settings.active_account_id or '')
        if not account_id:
            return
        if not ask_yes_no(
            self,
            t('tab_analytics'),
            t('analytics_remove_confirm'),
            default_yes=False,
        ):
            return
        removed = self.settings.channel_title or self.settings.channel_id
        if self.account_store.remove(account_id, delete_token=True):
            self.settings = ChannelAnalyticsSettings.from_dict({
                **self.settings.to_dict(),
                'active_account_id': '',
                'channel_id': '',
                'channel_title': '',
                'token_path': str(ChannelAnalyticsSettings().token_path),
            })
            self._refresh_accounts()
            self.log_message.emit(t('analytics_account_removed').format(channel=removed))
            self.settings_changed.emit()
        self._update_controls()

    def sync_now(self) -> None:
        if not self.enable_cb.isChecked() or (self.sync_worker and self.sync_worker.isRunning()):
            return
        settings = self._settings_from_ui()
        if not analytics_token_is_valid(settings):
            self._update_controls()
            return
        self.settings.last_error = ''
        self.sync_worker = AnalyticsSyncWorker(settings, self)
        self.sync_worker.result.connect(self._on_snapshot)
        self.sync_worker.error.connect(self._on_error)
        self.sync_worker.finished.connect(self._on_sync_finished)
        self.sync_worker.start()
        self._update_controls()

    def _on_sync_finished(self) -> None:
        worker = self.sender()
        if worker is self.sync_worker:
            self.sync_worker = None
        if worker is not None:
            worker.deleteLater()
        self._update_controls()

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            t('analytics_csv_title'),
            '',
            t('analytics_csv_filter'),
        )
        if not path:
            return
        try:
            snapshot = import_analytics_csv(path, snapshot_path=self.settings.snapshot_path)
        except Exception as error:
            self._on_error(str(error))
            return
        self._on_snapshot(snapshot, imported=True)

    def _on_snapshot(self, snapshot: Dict, imported: bool = False) -> None:
        videos = snapshot.get('videos') or []
        self.settings.last_sync_at = str(snapshot.get('synced_at') or '')
        self.settings.last_error = ''
        self.render_snapshot(snapshot)
        message = (
            t('analytics_status_imported') if imported else t('analytics_status_synced')
        ).format(count=len(videos))
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_GREEN};")
        self.log_message.emit(message)
        self.settings_changed.emit()

    def _on_error(self, error: str) -> None:
        self.settings.last_error = str(error)
        message = t('analytics_error').format(error=error)
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color:{ColorsV2.ACCENT_RED};")
        self.log_message.emit(message)
        self._update_controls()

    def _load_snapshot_if_present(self) -> None:
        snapshot = load_snapshot(self.settings.snapshot_path)
        if snapshot:
            self.render_snapshot(snapshot)

    def render_snapshot(self, snapshot: Dict) -> None:
        videos = list(snapshot.get('videos') or [])
        if not videos:
            self._clear_snapshot_view()
            return
        totals = snapshot.get('totals') or {}
        views = int(float(totals.get('views') or 0))
        retention = float(totals.get('median_average_view_percentage') or 0)
        self.summary_label.setText(t('analytics_summary').format(
            views=f"{views:,}".replace(',', ' '),
            videos=len(videos),
            retention=f"{retention:.1f}",
        ))
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(videos))
        for row_index, video in enumerate(videos):
            values = [
                str(video.get('title') or video.get('video_id') or '-'),
                int(float(video.get('views') or 0)),
                float(video.get('engaged_rate') or 0),
                float(video.get('averageViewPercentage') or 0),
                float(video.get('like_rate_per_1000') or 0),
                float(video.get('share_rate_per_1000') or 0),
                float(video.get('subscriber_rate_per_1000') or 0),
                float(video.get('relative_score') or 0),
            ]
            for column, value in enumerate(values):
                text = str(value) if column == 0 else (f"{value:.1f}" if isinstance(value, float) else str(value))
                item = QTableWidgetItem(text)
                if column:
                    item.setData(Qt.UserRole, float(value))
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)
        profile = snapshot.get('generation_profile') or {}
        lines = [t('analytics_insight_sample').format(count=int(profile.get('sample_size') or 0))]
        duration = float(profile.get('top_median_duration_seconds') or 0)
        retention_top = float(profile.get('top_median_average_view_percentage') or 0)
        if duration:
            lines.append(t('analytics_insight_duration').format(value=f"{duration:.0f}"))
        if retention_top:
            lines.append(t('analytics_insight_retention').format(value=f"{retention_top:.1f}"))
        lines.append(t('analytics_insight_caution'))
        self.insights.setPlainText('\n'.join(f"• {line}" for line in lines))

    def _clear_snapshot_view(self) -> None:
        self.summary_label.setText(t('analytics_no_data'))
        self.table.setRowCount(0)
        self.insights.clear()

    def stop_workers(self, timeout_ms: int = 10000) -> bool:
        ok = True
        for worker in (self.connect_worker, self.sync_worker):
            if worker and worker.isRunning():
                worker.requestInterruption()
                ok = worker.wait(timeout_ms) and ok
        return ok
