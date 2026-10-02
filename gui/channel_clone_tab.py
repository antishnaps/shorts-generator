"""GUI for analyzing a YouTube channel and generating finished remakes."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.channel_clone import ChannelAnalyzer, ChannelClonePipeline, ChannelVideo
from core.config_manager import ConfigManager
from gui.icons import interface_icon
from gui.localized_dialogs import show_critical, show_information, show_warning
from gui.translations import t


class ChannelCloneWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    analyzed = pyqtSignal(list)
    completed = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, mode: str, payload: dict, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.payload = payload

    def run(self):
        try:
            if self.mode == "analyze":
                videos = ChannelAnalyzer(self.log.emit).list_videos(
                    self.payload["url"], self.payload["limit"]
                )
                self.analyzed.emit([asdict(video) for video in videos])
                return
            pipeline = ChannelClonePipeline(
                output_root=self.payload["output"],
                api_key=self.payload.get("api_key", ""),
                language=self.payload.get("language", "Russian"),
                visual_sources=self.payload.get("sources", []),
                log_callback=self.log.emit,
            )
            results = pipeline.generate_remakes(
                (ChannelVideo(**video) for video in self.payload["videos"]),
                self.payload.get("generation_settings") or {},
                self.progress.emit,
            )
            self.completed.emit([str(path) for path in results])
        except Exception as exc:
            self.failed.emit(str(exc))


class ChannelCloneTab(QWidget):
    """One-screen workflow: channel link, selection and finished remake generation."""

    KIND_LABEL_KEYS = {
        "video": "channel_kind_video",
        "short": "channel_kind_short",
        "live": "channel_kind_live",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self._videos: list[dict] = []
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        root = QVBoxLayout(content)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        title = QLabel(t("channel_clone_title"))
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        root.addWidget(title)
        subtitle = QLabel(t("channel_clone_description"))
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)
        workflow = QLabel(t("channel_clone_workflow"))
        workflow.setWordWrap(True)
        workflow.setObjectName("channelCloneWorkflow")
        workflow.setStyleSheet(
            "padding: 10px 12px; border: 1px solid #223244; border-radius: 8px; "
            "background: #0D1721; color: #A9B7C6;"
        )
        root.addWidget(workflow)

        analyze_card = QGroupBox(t("channel_clone_source"))
        analyze_layout = QGridLayout(analyze_card)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://www.youtube.com/@channel")
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 1000)
        self.limit_spin.setValue(100)
        self.limit_spin.setToolTip(t("channel_clone_limit_hint"))
        self.analyze_button = QPushButton(t("channel_clone_analyze"))
        self.analyze_button.setIcon(interface_icon("monitor"))
        self.analyze_button.clicked.connect(self._analyze)
        analyze_layout.addWidget(QLabel(t("channel_clone_url")), 0, 0)
        analyze_layout.addWidget(self.url_input, 0, 1)
        analyze_layout.addWidget(QLabel(t("channel_clone_limit")), 0, 2)
        analyze_layout.addWidget(self.limit_spin, 0, 3)
        analyze_layout.addWidget(self.analyze_button, 0, 4)
        root.addWidget(analyze_card)

        self.summary_label = QLabel(t("channel_clone_no_results"))
        self.summary_label.setStyleSheet("color: #A9B7C6; font-weight: 600;")
        root.addWidget(self.summary_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [
                t("channel_clone_select"),
                t("channel_clone_type"),
                t("channel_clone_video"),
                t("duration"),
                t("channel_clone_views"),
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemChanged.connect(self._update_selection_summary)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(300)
        root.addWidget(self.table, 1)

        selection_row = QHBoxLayout()
        select_all = QPushButton(t("channel_clone_select_all"))
        clear_all = QPushButton(t("channel_clone_clear"))
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        clear_all.clicked.connect(lambda: self._set_all_checked(False))
        selection_row.addWidget(select_all)
        selection_row.addWidget(clear_all)
        selection_row.addStretch(1)
        root.addLayout(selection_row)

        remake_card = QGroupBox(t("channel_clone_remake"))
        remake_layout = QGridLayout(remake_card)
        self.output_input = QLineEdit(str(Path("generated") / "channel_remakes"))
        browse = QPushButton(t("select_output"))
        browse.clicked.connect(self._browse_output)
        remake_layout.addWidget(QLabel(t("channel_clone_output_folder")), 0, 0)
        remake_layout.addWidget(self.output_input, 0, 1, 1, 3)
        remake_layout.addWidget(browse, 0, 4)
        hint = QLabel(t("channel_clone_output_hint"))
        hint.setWordWrap(True)
        remake_layout.addWidget(hint, 1, 0, 1, 5)

        self.youtube_cb = QCheckBox("YouTube")
        self.youtube_cb.setChecked(True)
        self.local_cb = QCheckBox(t("channel_clone_local"))
        self.local_cb.setChecked(True)
        self.pexels_cb = QCheckBox("Pexels")
        self.pexels_cb.setChecked(True)
        for column, checkbox in enumerate(
            (self.youtube_cb, self.local_cb, self.pexels_cb), start=1
        ):
            remake_layout.addWidget(checkbox, 2, column)
        remake_layout.addWidget(QLabel(t("channel_clone_sources")), 2, 0)

        self.source_remix_cb = QCheckBox(t("channel_clone_source_remix"))
        self.source_remix_cb.setChecked(False)
        self.source_remix_cb.setToolTip(t("channel_clone_source_remix_hint"))
        remake_layout.addWidget(self.source_remix_cb, 3, 0, 1, 5)

        self.first_shots_cb = QCheckBox(t("channel_clone_first_shots"))
        self.first_shots_cb.setChecked(False)
        self.first_shots_cb.toggled.connect(self._update_remake_controls)
        self.first_shots_input = QLineEdit()
        self.first_shots_input.setPlaceholderText(t("channel_clone_first_shots_placeholder"))
        first_shots_browse = QPushButton(t("browse"))
        first_shots_browse.clicked.connect(self._browse_first_shots)
        remake_layout.addWidget(self.first_shots_cb, 4, 0)
        remake_layout.addWidget(self.first_shots_input, 4, 1, 1, 3)
        remake_layout.addWidget(first_shots_browse, 4, 4)
        self.first_shots_browse = first_shots_browse
        self._update_remake_controls()

        self.generate_button = QPushButton(t("channel_clone_generate"))
        self.generate_button.setIcon(interface_icon("generation"))
        self.generate_button.clicked.connect(self._generate)
        self.prepare_button = self.generate_button
        remake_layout.addWidget(self.generate_button, 5, 0, 1, 5)
        root.addWidget(remake_card)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(140)
        root.addWidget(self.log_output)

    def _set_busy(self, busy: bool):
        self.analyze_button.setEnabled(not busy)
        self.generate_button.setEnabled(not busy)
        if busy:
            self.progress.setValue(0)

    def _start_worker(self, mode: str, payload: dict):
        if self._worker is not None and self._worker.isRunning():
            return
        self._set_busy(True)
        self._worker = ChannelCloneWorker(mode, payload, self)
        self._worker.log.connect(self.log_output.append)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.failed.connect(self._failed)
        self._worker.finished.connect(self._on_worker_finished)
        if mode == "analyze":
            self._worker.analyzed.connect(self._show_videos)
        else:
            self._worker.completed.connect(self._generated)
        self._worker.start()

    def _on_worker_finished(self):
        self._set_busy(False)
        worker = self.sender()
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
        if worker is self._worker:
            self._worker = None

    def stop_background_threads(self, wait_ms: int = 3000) -> bool:
        worker = self._worker
        if worker is None:
            return True
        if worker.isRunning() and not worker.wait(wait_ms):
            return False
        try:
            worker.blockSignals(True)
            worker.deleteLater()
        except RuntimeError:
            pass
        if self._worker is worker:
            self._worker = None
        self._set_busy(False)
        return True

    def _analyze(self):
        if not self.url_input.text().strip():
            show_warning(self, t("error"), t("channel_clone_url_required"))
            return
        self.log_output.clear()
        self._start_worker(
            "analyze", {"url": self.url_input.text().strip(), "limit": self.limit_spin.value()}
        )

    def _show_videos(self, videos: list):
        self._videos = videos
        self.table.blockSignals(True)
        self.table.setRowCount(len(videos))
        for row, video in enumerate(videos):
            selected = QTableWidgetItem()
            selected.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            selected.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, selected)
            self.table.setItem(
                row,
                1,
                QTableWidgetItem(
                    t(self.KIND_LABEL_KEYS.get(video.get("kind"), "channel_kind_video"))
                ),
            )
            title_item = QTableWidgetItem(video["title"])
            title_item.setToolTip(video.get("url", ""))
            self.table.setItem(row, 2, title_item)
            duration = video.get("duration") or 0
            self.table.setItem(row, 3, QTableWidgetItem(self._format_duration(duration)))
            views = video.get("view_count") or 0
            self.table.setItem(row, 4, QTableWidgetItem(f"{views:,}" if views else "—"))
        self.table.blockSignals(False)
        self.progress.setValue(100)
        self._update_selection_summary()

    def _set_all_checked(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(state)
        self.table.blockSignals(False)
        self._update_selection_summary()

    def _selected_videos(self) -> list[dict]:
        return [
            video
            for row, video in enumerate(self._videos)
            if self.table.item(row, 0) and self.table.item(row, 0).checkState() == Qt.Checked
        ]

    def _update_selection_summary(self, *_args):
        self.summary_label.setText(
            t("channel_clone_summary").format(
                total=len(self._videos), selected=len(self._selected_videos())
            )
        )

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, t("select_output"), self.output_input.text())
        if path:
            self.output_input.setText(path)

    def _browse_first_shots(self):
        path = QFileDialog.getExistingDirectory(
            self,
            t("select_images"),
            self.first_shots_input.text() or str(Path.cwd()),
        )
        if path:
            self.first_shots_input.setText(path)

    def _update_remake_controls(self):
        enabled = self.first_shots_cb.isChecked()
        self.first_shots_input.setEnabled(enabled)
        self.first_shots_browse.setEnabled(enabled)

    def _current_generation_settings(self) -> dict:
        if self.parent_window and hasattr(self.parent_window, "get_all_settings"):
            settings = dict(self.parent_window.get_all_settings())
        else:
            settings = {}
        config = ConfigManager()
        keys = config.get_api_keys()
        settings.setdefault("api_key", keys[0] if keys else "")
        settings.setdefault("google_ai_api_key", settings.get("api_key", ""))
        settings.setdefault("source_data", {})
        settings["source_data"] = dict(settings["source_data"])
        settings["source_data"]["language"] = config.get_user_setting("language", "Russian")
        avatar = dict(settings.get("avatar_settings") or {})
        if (
            avatar.get("enabled")
            and self.parent_window
            and hasattr(self.parent_window, "settings_tab")
            and hasattr(self.parent_window.settings_tab, "get_heygen_api_key")
        ):
            avatar["api_key"] = self.parent_window.settings_tab.get_heygen_api_key()
        settings["avatar_settings"] = avatar
        return settings

    def _generate(self):
        selected = self._selected_videos()
        if not selected:
            show_warning(self, t("error"), t("channel_clone_nothing_selected"))
            return
        sources = [
            name
            for name, checkbox in (
                ("youtube", self.youtube_cb),
                ("local", self.local_cb),
                ("pexels", self.pexels_cb),
            )
            if checkbox.isChecked()
        ]
        settings = self._current_generation_settings()
        if self.first_shots_cb.isChecked():
            first_shots_folder = Path(self.first_shots_input.text().strip())
            if not self.first_shots_input.text().strip() or not first_shots_folder.is_dir():
                show_warning(
                    self,
                    t("error"),
                    t("channel_clone_first_shots_missing"),
                )
                return
        settings["channel_remake_settings"] = {
            "use_source_video": self.source_remix_cb.isChecked(),
            "first_shots_enabled": self.first_shots_cb.isChecked(),
            "first_shots_folder": self.first_shots_input.text().strip(),
        }
        self.log_output.clear()
        self._start_worker(
            "generate",
            {
                "videos": selected,
                "output": self.output_input.text().strip() or "generated/channel_remakes",
                "api_key": settings.get("api_key", ""),
                "language": settings.get("source_data", {}).get("language", "Russian"),
                "sources": sources,
                "generation_settings": settings,
            },
        )

    def _generated(self, paths: list):
        self.progress.setValue(100)
        show_information(
            self, t("channel_clone_title"), t("channel_clone_ready").format(count=len(paths))
        )
        output = Path(self.output_input.text().strip() or "generated/channel_remakes").resolve()
        if output.exists():
            os.startfile(str(output))

    def _failed(self, message: str):
        localized_message = t("runtime_error_detail").format(error=message)
        self.log_output.append(localized_message)
        show_critical(self, t("error"), localized_message)

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if not seconds:
            return "—"
        minutes, seconds = divmod(max(0, int(seconds)), 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
