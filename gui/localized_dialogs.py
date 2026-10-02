"""QMessageBox helpers whose buttons follow the application's UI language.

Qt normally translates standard buttons through an operating-system
``QTranslator``.  ContentBot changes language at runtime and intentionally does
not depend on an OS translation pack, so every standard button is labelled
explicitly from the application catalog instead.
"""

from __future__ import annotations

from PyQt5.QtWidgets import QDialogButtonBox, QMessageBox, QWidget

from gui.translations import t


_BUTTON_TRANSLATION_KEYS = {
    QMessageBox.Yes: "dialog_yes",
    QMessageBox.No: "dialog_no",
    QMessageBox.Ok: "dialog_ok",
    QMessageBox.Cancel: "dialog_cancel",
}

_DIALOG_BUTTON_TRANSLATION_KEYS = {
    QDialogButtonBox.Yes: "dialog_yes",
    QDialogButtonBox.No: "dialog_no",
    QDialogButtonBox.Ok: "dialog_ok",
    QDialogButtonBox.Cancel: "dialog_cancel",
}


def _localize_standard_buttons(box: QMessageBox) -> None:
    """Apply application-language labels to buttons present in ``box``."""
    for standard_button, translation_key in _BUTTON_TRANSLATION_KEYS.items():
        button = box.button(standard_button)
        if button is not None:
            button.setText(t(translation_key))


def localize_dialog_button_box(button_box: QDialogButtonBox) -> None:
    """Apply the selected UI language to standard QDialogButtonBox buttons."""
    for standard_button, translation_key in _DIALOG_BUTTON_TRANSLATION_KEYS.items():
        button = button_box.button(standard_button)
        if button is not None:
            button.setText(t(translation_key))


def exec_localized_message(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    icon: QMessageBox.Icon = QMessageBox.Information,
    buttons: QMessageBox.StandardButtons = QMessageBox.Ok,
    default_button: QMessageBox.StandardButton | None = QMessageBox.Ok,
) -> int:
    """Show a modal message box with explicitly localized standard buttons."""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(str(title))
    box.setText(str(text))
    box.setStandardButtons(buttons)
    if default_button is not None and box.button(default_button) is not None:
        box.setDefaultButton(default_button)
    _localize_standard_buttons(box)
    return box.exec_()


def show_information(parent: QWidget | None, title: str, text: str) -> int:
    return exec_localized_message(
        parent, title, text, icon=QMessageBox.Information
    )


def show_warning(parent: QWidget | None, title: str, text: str) -> int:
    return exec_localized_message(parent, title, text, icon=QMessageBox.Warning)


def show_critical(parent: QWidget | None, title: str, text: str) -> int:
    return exec_localized_message(parent, title, text, icon=QMessageBox.Critical)


def ask_yes_no(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    default_yes: bool | None = None,
) -> bool:
    """Ask a localized yes/no question and return ``True`` only for Yes."""
    default = (
        None
        if default_yes is None
        else QMessageBox.Yes if default_yes else QMessageBox.No
    )
    result = exec_localized_message(
        parent,
        title,
        text,
        icon=QMessageBox.Question,
        buttons=QMessageBox.Yes | QMessageBox.No,
        default_button=default,
    )
    return result == QMessageBox.Yes


__all__ = [
    "ask_yes_no",
    "exec_localized_message",
    "localize_dialog_button_box",
    "show_critical",
    "show_information",
    "show_warning",
]
