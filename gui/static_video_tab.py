#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.static_video_worker import StaticVideoSettings, StaticVideoWorker
from gui.icons import interface_icon, navigation_icon_size
from gui.styles_v2 import ColorsV2
from gui.translations import t


class StaticVideoTab(QWidget):
    """Batch creation of one-image videos with audio-driven duration."""

    settings_changed = pyqtSignal()

    RESOLUTIONS = (
        ("1080x1920 - Full HD (9:16)", 1080, 1920),
        ("1440x2560 - 2K (9:16)", 1440, 2560),
        ("720x1280 - HD (9:16)", 720, 1280),
        ("1920x1080 - Full HD (16:9)", 1920, 1080),
        ("2560x1440 - 2K (16:9)", 2560, 1440),
        ("1280x720 - HD (16:9)", 1280, 720),
    )

    def __init__(self):
        super().__init__()
        self.worker = None
        self.images_dir = ""
        self.audio_dir = ""
        self.output_dir = ""
        self.init_ui()

    def init_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        title = QLabel(t("static_title"))
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        root.addWidget(title)

        description = QLabel(t("static_description"))
        description.setWordWrap(True)
        description.setStyleSheet(f"color:{ColorsV2.TEXT_MUTED};")
        root.addWidget(description)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(scroll, 2)

        content = QWidget()
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        content_layout = QVBoxLayout(content)
        content_layout.setSizeConstraint(QLayout.SetMinimumSize)
        content_layout.setContentsMargins(2, 2, 2, 2)
        content_layout.setSpacing(10)
        scroll.setWidget(content)

        folders = QGroupBox(t("folders"))
        folders_layout = QGridLayout(folders)
        self.img_lbl = self._path_label()
        self.audio_lbl = self._path_label()
        self.out_lbl = self._path_label()
        for index, (title_text, handler, label, icon_name) in enumerate((
            (t("images"), self.select_images_dir, self.img_lbl, "static"),
            (t("audio_folder"), self.select_audio_dir, self.audio_lbl, "audio"),
            (t("ready_videos"), self.select_output_dir, self.out_lbl, "settings"),
        )):
            if index < 2:
                folders_layout.addWidget(
                    self._folder_row(title_text, handler, label, icon_name), 0, index
                )
            else:
                folders_layout.addWidget(
                    self._folder_row(title_text, handler, label, icon_name), 1, 0, 1, 2
                )
        content_layout.addWidget(folders)

        settings_group = QGroupBox(t("static_settings"))
        form = QGridLayout(settings_group)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)

        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems([item[0] for item in self.RESOLUTIONS])
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["30", "60", "25"])
        self.fit_combo = QComboBox()
        self.fit_combo.addItems([t("fill_frame"), t("fit_whole")])
        self.fit_combo.setToolTip(t("fit_tooltip"))
        self.animation_combo = QComboBox()
        self.animation_combo.addItems(
            [t("slow_zoom"), t("zoom_in"), t("zoom_out"), t("no_movement"), t("random")]
        )
        self.filter_combo = QComboBox()
        self.filter_combo.addItems([t("no_filter"), t("warm"), t("old_film"), "VHS", t("black_white")])
        self.match_combo = QComboBox()
        self.match_combo.addItems([t("match_auto"), t("match_strict")])
        self.match_combo.setToolTip(t("match_explanation"))
        for index, (label, widget) in enumerate((
            (t("resolution").rstrip(":"), self.resolution_combo),
            ("FPS", self.fps_combo),
            (t("cropping"), self.fit_combo),
            (t("movement"), self.animation_combo),
            (t("filter"), self.filter_combo),
            (t("matching"), self.match_combo),
        )):
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            widget.setMinimumContentsLength(12)
            widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            row = index % 3
            column = (index // 3) * 2
            form.addWidget(QLabel(label + ":"), row, column)
            form.addWidget(widget, row, column + 1)
        match_hint = QLabel(t("match_explanation"))
        match_hint.setWordWrap(True)
        match_hint.setStyleSheet(f"color:{ColorsV2.TEXT_MUTED}; font-size:11px;")
        form.addWidget(match_hint, 3, 0, 1, 4)
        content_layout.addWidget(settings_group)

        self.btn_start = QPushButton(t("create_static"))
        self.btn_start.setMinimumHeight(48)
        self.btn_start.clicked.connect(self.toggle_generation)
        content_layout.addWidget(self.btn_start)

        self.progress_bar = QProgressBar()
        content_layout.addWidget(self.progress_bar)

        self.btn_open_output = QPushButton(t("open_output"))
        self.btn_open_output.setVisible(False)
        self.btn_open_output.clicked.connect(self._open_output_dir)
        content_layout.addWidget(self.btn_open_output)
        content_layout.addStretch()

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(80)
        self.log_output.setMaximumHeight(110)
        self.log_output.setStyleSheet(f"background:{ColorsV2.BG_DARKEST}; color:#3fb950;")
        root.addWidget(self.log_output)
        for combo in (
            self.resolution_combo, self.fps_combo, self.fit_combo,
            self.animation_combo, self.filter_combo, self.match_combo,
        ):
            combo.currentIndexChanged.connect(self.settings_changed)

    def _folder_row(self, title, handler, label, icon_name):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton(title)
        button.setIcon(interface_icon(icon_name))
        button.setIconSize(navigation_icon_size())
        button.setMinimumWidth(130)
        button.clicked.connect(handler)
        row_layout.addWidget(button)
        row_layout.addWidget(label, 1)
        return row

    @staticmethod
    def _path_label():
        label = QLabel(t("not_selected"))
        label.setStyleSheet(f"color:{ColorsV2.TEXT_MUTED};")
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def _set_path(self, label, path):
        label.setText(path)
        label.setToolTip(path)

    def select_images_dir(self):
        path = QFileDialog.getExistingDirectory(self, t("select_images"))
        if path:
            self.images_dir = path
            self._set_path(self.img_lbl, path)
            self.settings_changed.emit()

    def select_audio_dir(self):
        path = QFileDialog.getExistingDirectory(self, t("select_audio"))
        if path:
            self.audio_dir = path
            self._set_path(self.audio_lbl, path)
            self.settings_changed.emit()

    def select_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, t("select_output"))
        if path:
            self.output_dir = path
            self._set_path(self.out_lbl, path)
            self.settings_changed.emit()

    def get_saved_state(self):
        return {
            "images_dir": self.images_dir,
            "audio_dir": self.audio_dir,
            "output_dir": self.output_dir,
            "resolution_index": self.resolution_combo.currentIndex(),
            "fps_index": self.fps_combo.currentIndex(),
            "fit_index": self.fit_combo.currentIndex(),
            "animation_index": self.animation_combo.currentIndex(),
            "filter_index": self.filter_combo.currentIndex(),
            "match_index": self.match_combo.currentIndex(),
        }

    def apply_saved_state(self, state):
        if not isinstance(state, dict):
            return
        for key, label in (
            ("images_dir", self.img_lbl),
            ("audio_dir", self.audio_lbl),
            ("output_dir", self.out_lbl),
        ):
            path = state.get(key, "") or ""
            setattr(self, key, path)
            if path:
                self._set_path(label, path)
        for key, combo in (
            ("resolution_index", self.resolution_combo),
            ("fps_index", self.fps_combo),
            ("fit_index", self.fit_combo),
            ("animation_index", self.animation_combo),
            ("filter_index", self.filter_combo),
            ("match_index", self.match_combo),
        ):
            index = int(state.get(key, combo.currentIndex()))
            combo.setCurrentIndex(max(0, min(index, combo.count() - 1)))

    def _settings(self):
        _, width, height = self.RESOLUTIONS[self.resolution_combo.currentIndex()]
        return StaticVideoSettings(
            width=width,
            height=height,
            fps=int(self.fps_combo.currentText()),
            fit_mode=("cover", "contain")[self.fit_combo.currentIndex()],
            animation=("slow_zoom", "zoom_in", "zoom_out", "none", "random")[
                self.animation_combo.currentIndex()
            ],
            filter_type=("none", "warm", "old_film", "vhs", "b_w")[
                self.filter_combo.currentIndex()
            ],
            image_match_mode=("name_then_random", "name_only")[
                self.match_combo.currentIndex()
            ],
        )

    def toggle_generation(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.btn_start.setEnabled(False)
            return
        if not self.images_dir or not self.audio_dir or not self.output_dir:
            self.log(t("choose_three"))
            return
        self.log_output.clear()
        self.progress_bar.setValue(0)
        self.btn_open_output.setVisible(False)
        self.btn_start.setText(t("stop"))
        self.worker = StaticVideoWorker(
            self.images_dir,
            self.audio_dir,
            self.output_dir,
            settings=self._settings(),
        )
        self.worker.log.connect(self.log)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.finished.connect(self._cleanup_worker)
        self.worker.start()

    def log(self, text):
        self.log_output.append(text)

    def on_finished(self, success, message):
        self.log(message)
        self.btn_start.setText(t("create_static"))
        self.btn_start.setEnabled(True)
        self.btn_open_output.setVisible(bool(self.output_dir and os.path.isdir(self.output_dir)))
        self.worker = None

    def _cleanup_worker(self):
        worker = self.sender()
        if worker is None:
            return
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        if self.worker is worker:
            self.worker = None

    def stop_background_threads(self, wait_ms: int = 3000) -> bool:
        worker = self.worker
        if worker is None:
            return True
        if worker.isRunning():
            worker.cancel()
            if not worker.wait(wait_ms):
                return False
        try:
            worker.blockSignals(True)
            worker.deleteLater()
        except RuntimeError:
            pass
        if self.worker is worker:
            self.worker = None
        return True

    def _open_output_dir(self):
        if self.output_dir and os.path.isdir(self.output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_dir))
