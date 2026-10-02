import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication, QDialog, QLineEdit

import gui.color_picker as color_picker_module
from gui.color_picker import ColorPickerLineEdit
from gui.generation_tab import GenerationTab


_QT_APP = None


def _app():
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


def test_color_fields_use_editable_palette_controls():
    _app()
    tab = GenerationTab()

    assert isinstance(tab.subtitle_color_input, ColorPickerLineEdit)
    assert isinstance(tab.subtitle_highlight_input, ColorPickerLineEdit)
    assert isinstance(tab.subtitle_color_input, QLineEdit)
    assert tab.subtitle_color_input.swatch_rect().width() >= 18
    assert tab.subtitle_highlight_input.swatch_rect().width() >= 18


def test_color_picker_accepts_manual_names_and_normalizes_to_hex():
    _app()
    picker = ColorPickerLineEdit("#FFFFFF")

    picker.setText("yellow")
    assert picker.current_color().name(QColor.HexRgb) == "#ffff00"
    picker.editingFinished.emit()

    assert picker.text() == "#FFFF00"


def test_color_picker_applies_palette_selection(monkeypatch):
    _app()

    class FakeColorDialog:
        DontUseNativeDialog = 1
        ShowAlphaChannel = 2

        def __init__(self, initial, parent):
            self.initial = initial
            self.parent = parent
            self.options = []
            self.title = ""

        def setOption(self, option, enabled):
            self.options.append((option, enabled))

        def setWindowTitle(self, title):
            self.title = title

        def exec_(self):
            return QDialog.Accepted

        @staticmethod
        def currentColor():
            return QColor("#3DBB7F")

    monkeypatch.setattr(color_picker_module, "QColorDialog", FakeColorDialog)
    picker = ColorPickerLineEdit("#FFFFFF")
    changed = []
    picker.textChanged.connect(changed.append)

    picker.open_color_picker()

    assert picker.text() == "#3DBB7F"
    assert changed[-1] == "#3DBB7F"
