import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication, QLabel
from PyQt5.QtGui import QFont, QFontDatabase

import gui.donations as donations
from gui.app_shell import BrandHeader
from gui.translations import UI_LANGUAGES, get_ui_language, set_ui_language

_QT_APP = None


def app():
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    if os.name == "nt" and not QFontDatabase().families():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "seguisb.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(fonts / name))
        _QT_APP.setFont(QFont("Segoe UI", 10))
    return _QT_APP


def test_public_payment_numbers_are_valid():
    details = donations.load_payment_details()
    assert donations.valid_card(details["russia_card"])
    assert donations.valid_card(details["belarus_visa"])
    assert len(details["ethereum_address"]) == 42
    assert not donations.valid_card("4111111111111112")
    assert not donations.valid_card("arbitrary-value")


def test_corrupt_payment_details_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(donations, "SUPPORT_ASSETS", tmp_path)
    assert donations.load_payment_details() == {}
    (tmp_path / "payment_methods.json").write_text(json.dumps({"russia_card": "bad", "ethereum_address": "0x" + "a" * 40, "ethereum_chain_id": 56}), encoding="utf-8")
    result = donations.load_payment_details()
    assert not result["russia_card"]
    assert not result["ethereum_address"]


def test_support_is_opt_in_for_commercial_shell():
    app()
    commercial = BrandHeader()
    free = BrandHeader(show_support=True)
    assert not hasattr(commercial, "support_button")
    assert hasattr(free, "support_button")
    assert free.support_button._dialog is None


def test_copy_uses_exact_numbers_and_address_only():
    app()
    dialog = donations.SupportDialog()
    for key, button in dialog.card_buttons.items():
        button.click()
        assert QApplication.clipboard().text() == dialog.details[key]
        assert " " not in QApplication.clipboard().text()
    dialog.crypto_copy.click()
    assert QApplication.clipboard().text() == dialog.details["ethereum_address"]
    assert "\n" not in QApplication.clipboard().text()
    # Do not leave payment data in the developer's clipboard after the test.
    QApplication.clipboard().clear()
    dialog.close()


@pytest.mark.parametrize("language", tuple(UI_LANGUAGES))
def test_donation_labels_are_localized_and_fit(language):
    qt = app()
    previous = get_ui_language()
    set_ui_language(language)
    try:
        assert len(donations._WORDS[language]) == len(donations._KEYS)
        assert all(donations._WORDS[language])
        assert "Visa" in donations.support_text("international")
        assert " · " in donations.support_text("international")
        assert " · " in donations.support_text("russia")
        dialog = donations.SupportDialog()
        dialog.show()
        qt.processEvents()
        for widget in dialog.findChildren(QLabel):
            assert widget.width() > 0
            if widget.objectName() in {"supportTitle", "supportNumber", "supportAddress"}:
                for line in widget.text().splitlines():
                    assert widget.fontMetrics().horizontalAdvance(line) <= widget.width()
        assert dialog.size().width() < 900
        dialog.close()
    finally:
        set_ui_language(previous)


def test_all_supported_ui_languages_have_complete_support_translations():
    assert set(donations._WORDS) == set(UI_LANGUAGES)
    for language, words in donations._WORDS.items():
        assert len(words) == len(donations._KEYS), language
        assert "belarus" not in donations._KEYS
        assert all(words), language


def test_qr_matches_receiving_address():
    # Dev-only decoder; the application itself uses a static PNG.
    zxingcpp = pytest.importorskip("zxingcpp")
    from PIL import Image
    results = zxingcpp.read_barcodes(Image.open(donations.SUPPORT_ASSETS / "ethereum_address.png"))
    assert len(results) == 1
    assert results[0].text == donations.load_payment_details()["ethereum_address"]
    assert donations.qr_matches_address(results[0].text)
    assert not donations.qr_matches_address("0x" + "0" * 40)


def test_copy_with_missing_value_leaves_clipboard_unchanged():
    app()
    QApplication.clipboard().setText("original")
    button = donations.CopyButton("")
    assert not button.isEnabled()
    button.copy_value()
    assert QApplication.clipboard().text() == "original"
    QApplication.clipboard().clear()


def test_qr_is_scannable_at_actual_dialog_size():
    from io import BytesIO
    from PIL import Image
    from PyQt5.QtCore import QByteArray, QBuffer, QIODevice
    zxingcpp = pytest.importorskip("zxingcpp")
    qt = app()
    dialog = donations.SupportDialog()
    dialog.show()
    qt.processEvents()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    assert dialog.grab().save(buffer, "PNG")
    results = zxingcpp.read_barcodes(Image.open(BytesIO(bytes(data))))
    assert len(results) == 1
    assert results[0].text == dialog.details["ethereum_address"]
    dialog.close()


def test_support_entry_point_only_added_to_free_export():
    root = Path(__file__).resolve().parents[1]
    source = (root / "gui/main_window_v2.py").read_text(encoding="utf-8")
    # This same test also runs inside the free release, where this is already enabled.
    if (root / "tools/prepare_free_release.py").exists():
        from tools.prepare_free_release import transform_source
        exported = transform_source("gui/main_window_v2.py", source)
        assert "BrandHeader(self, show_support=True)" in exported
        assert "BrandHeader(self, show_support=True)" not in source
    else:
        assert "BrandHeader(self, show_support=True)" in source
