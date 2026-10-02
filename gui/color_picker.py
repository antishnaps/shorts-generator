#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Editable HEX fields with a compact visual color picker."""

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QColorDialog, QDialog, QLineEdit

from gui.styles_v2 import ColorsV2
from gui.translations import t


class ColorPickerLineEdit(QLineEdit):
    """A normal editable color field with a synchronized palette button.

    Keeping this as a ``QLineEdit`` subclass preserves the existing settings
    API while letting users pick a precise color visually or type a HEX value.
    """

    _SWATCH_SIZE = 26

    def __init__(self, color: str = "#FFFFFF", parent=None):
        super().__init__(color, parent)
        self.setTextMargins(0, 0, self._SWATCH_SIZE + 18, 0)
        self.setMouseTracking(True)
        self.textChanged.connect(self._refresh_swatch)
        self.editingFinished.connect(self._normalize_hex_text)
        self._refresh_swatch(color)

    @staticmethod
    def _parse_color(value: str) -> QColor:
        return QColor(str(value or '').strip())

    def current_color(self) -> QColor:
        """Return the current field value as ``QColor`` (possibly invalid)."""
        return self._parse_color(self.text())

    def swatch_rect(self) -> QRect:
        side = max(18, min(self._SWATCH_SIZE, self.height() - 14))
        return QRect(
            self.width() - side - 9,
            (self.height() - side) // 2,
            side,
            side,
        )

    def _refresh_swatch(self, value: str):
        color = self._parse_color(value)
        self.setToolTip(t('color_picker_choose') if color.isValid() else t('color_picker_invalid'))
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        color = self.current_color()
        rect = self.swatch_rect()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(color if color.isValid() else QColor(ColorsV2.BG_HOVER))
        painter.setPen(QColor(ColorsV2.BORDER_DEFAULT if color.isValid() else ColorsV2.ACCENT_RED))
        painter.drawRoundedRect(rect, 5, 5)
        if not color.isValid():
            painter.drawLine(rect.topLeft(), rect.bottomRight())
        painter.end()

    def mouseMoveEvent(self, event):
        self.setCursor(Qt.PointingHandCursor if self.swatch_rect().contains(event.pos()) else Qt.IBeamCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.setCursor(Qt.IBeamCursor)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.swatch_rect().contains(event.pos()):
            self.open_color_picker()
            event.accept()
            return
        super().mousePressEvent(event)

    def _normalize_hex_text(self):
        color = self.current_color()
        if color.isValid():
            normalized = color.name(QColor.HexRgb).upper()
            if self.text() != normalized:
                self.setText(normalized)

    def open_color_picker(self):
        """Open Qt's full palette dialog; it also exposes a manual HTML/HEX field."""
        initial = self.current_color()
        if not initial.isValid():
            initial = QColor('#FFFFFF')

        dialog = QColorDialog(initial, self)
        # The Qt dialog consistently includes the palette and manual HTML/HEX
        # field, unlike platform-native dialogs that vary by Windows version.
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)
        dialog.setOption(QColorDialog.ShowAlphaChannel, False)
        dialog.setWindowTitle(t('color_picker_title'))
        if dialog.exec_() == QDialog.Accepted:
            self.setText(dialog.currentColor().name(QColor.HexRgb).upper())
            self.setFocus(Qt.OtherFocusReason)
