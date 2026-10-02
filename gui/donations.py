"""Optional, offline donation UI for the free edition.

Receiving details are intentionally public. No checkout, telemetry, browser
navigation, contacts or automatic transfers are performed by this widget.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout,
)

from gui.styles_v2 import ColorsV2, get_sizes
from gui.translations import get_ui_language

SUPPORT_ASSETS = Path(__file__).resolve().parents[1] / "assets" / "support"

_KEYS = ("button", "title", "optional", "russia", "international", "copy", "copied",
         "pending", "network", "network_warning", "close", "address", "unavailable")
_WORDS = {
    "Russian": ("Поддержать", "Поддержать проект", "Добровольно. Программа остаётся бесплатной.",
                "Россия · МИР", "Международные · Visa", "Копировать", "Скопировано", "Номер уточняется",
                "Сеть Ethereum", "Только сеть Ethereum", "Закрыть", "Адрес кошелька", "Реквизиты недоступны"),
    "English": ("Support", "Support the project", "Optional. The app stays free.",
                "Russia · MIR", "International · Visa", "Copy", "Copied", "Number pending",
                "Ethereum network", "Ethereum network only", "Close", "Wallet address", "Details unavailable"),
    "Spanish": ("Apoyar", "Apoyar el proyecto", "Voluntario. La aplicación sigue siendo gratuita.",
                "Rusia · MIR", "Internacional · Visa", "Copiar", "Copiado", "Número pendiente",
                "Red Ethereum", "Solo la red Ethereum", "Cerrar", "Dirección de la cartera", "Datos no disponibles"),
    "French": ("Soutenir", "Soutenir le projet", "Facultatif. Le logiciel reste gratuit.",
               "Russie · MIR", "International · Visa", "Copier", "Copié", "Numéro à confirmer",
               "Réseau Ethereum", "Réseau Ethereum uniquement", "Fermer", "Adresse du portefeuille", "Données indisponibles"),
    "German": ("Unterstützen", "Projekt unterstützen", "Freiwillig. Die App bleibt kostenlos.",
               "Russland · MIR", "International · Visa", "Kopieren", "Kopiert", "Nummer ausstehend",
               "Ethereum-Netzwerk", "Nur Ethereum-Netzwerk", "Schließen", "Wallet-Adresse", "Daten nicht verfügbar"),
    "Chinese": ("支持", "支持本项目", "自愿支持，软件永久免费。",
                "俄罗斯 · MIR", "国际转账 · Visa", "复制", "已复制", "号码待确认",
                "Ethereum 网络", "仅限 Ethereum 网络", "关闭", "钱包地址", "收款信息不可用"),
    "Japanese": ("支援", "プロジェクトを支援", "支援は任意です。アプリは無料のままです。",
                 "ロシア · MIR", "国際送金 · Visa", "コピー", "コピーしました", "番号を確認中",
                 "Ethereum ネットワーク", "Ethereum ネットワークのみ", "閉じる", "ウォレットアドレス", "情報を取得できません"),
    "Korean": ("후원", "프로젝트 후원", "후원은 선택 사항이며 앱은 무료로 유지됩니다.",
               "러시아 · MIR", "해외 송금 · Visa", "복사", "복사됨", "번호 확인 중",
               "Ethereum 네트워크", "Ethereum 네트워크만 사용", "닫기", "지갑 주소", "정보를 사용할 수 없음"),
    "Portuguese": ("Apoiar", "Apoiar o projeto", "Opcional. O aplicativo continua gratuito.",
                   "Rússia · MIR", "Internacional · Visa", "Copiar", "Copiado", "Número a confirmar",
                   "Rede Ethereum", "Somente a rede Ethereum", "Fechar", "Endereço da carteira", "Dados indisponíveis"),
}


def support_text(key: str) -> str:
    return dict(zip(_KEYS, _WORDS.get(get_ui_language(), _WORDS["English"])))[key]


def valid_card(number: str) -> bool:
    """Reject transcription errors; do not guess or repair a payment number."""
    if not re.fullmatch(r"[0-9]{16}", number or ""):
        return False
    digits = [int(c) for c in number]
    total = sum((d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 == 0 else d
                for i, d in enumerate(digits))
    return total % 10 == 0


def load_payment_details() -> dict:
    try:
        data = json.loads((SUPPORT_ASSETS / "payment_methods.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    for key in ("russia_card", "belarus_visa"):
        number = data.get(key, "")
        result[key] = number if isinstance(number, str) and valid_card(number) else ""
    address = data.get("ethereum_address", "")
    result["ethereum_address"] = address if (
        isinstance(address, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", address)
        and data.get("ethereum_chain_id") == 1
    ) else ""
    return result


def qr_matches_address(address: str) -> bool:
    """Hide an outdated QR after receiving details change; no decoder at runtime."""
    from PIL import Image
    try:
        with Image.open(SUPPORT_ASSETS / "ethereum_address.png") as image:
            return bool(address) and image.info.get("qr_payload") == address
    except (OSError, ValueError):
        return False


def _heart_icon() -> QIcon:
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(ColorsV2.ACCENT_CYAN), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(24, 38)
    path.cubicTo(5, 26, 4, 12, 15, 11)
    path.cubicTo(21, 10, 24, 16, 24, 16)
    path.cubicTo(24, 16, 27, 10, 33, 11)
    path.cubicTo(44, 12, 43, 26, 24, 38)
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


class CopyButton(QPushButton):
    def __init__(self, value: str, parent=None):
        super().__init__(support_text("copy"), parent)
        self.value = value
        self.setObjectName("supportCopy")
        self.setEnabled(bool(value))
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self.copy_value)
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.timeout.connect(lambda: self.setText(support_text("copy")))

    def copy_value(self):
        if not self.value:
            return
        QApplication.clipboard().setText(self.value)
        self.setText(support_text("copied"))
        self._reset_timer.start(1800)


class SupportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("supportDialog")
        self.setWindowTitle(support_text("title"))
        self.setModal(True)
        self.setMinimumWidth(640)
        self.setStyleSheet(_dialog_style())
        self.details = load_payment_details()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 22)
        layout.setSpacing(16)

        heading = QLabel(support_text("title"))
        heading.setObjectName("supportTitle")
        layout.addWidget(heading)
        subtitle = QLabel(support_text("optional"))
        subtitle.setObjectName("supportMuted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.card_buttons = {}
        for key, label in (("russia_card", "russia"), ("belarus_visa", "international")):
            frame = QFrame()
            frame.setObjectName("supportCard")
            row = QHBoxLayout(frame)
            row.setContentsMargins(18, 15, 18, 15)
            texts = QVBoxLayout()
            name = QLabel(support_text(label))
            name.setObjectName("supportMethod")
            texts.addWidget(name)
            value = self.details.get(key, "")
            number = QLabel(" ".join(value[i:i + 4] for i in range(0, len(value), 4)) if value else support_text("pending"))
            number.setObjectName("supportNumber" if value else "supportMuted")
            number.setTextInteractionFlags(Qt.TextSelectableByMouse)
            texts.addWidget(number)
            row.addLayout(texts, 1)
            button = CopyButton(value)
            button.setAccessibleName(support_text("copy") + " — " + support_text(label))
            self.card_buttons[key] = button
            row.addWidget(button)
            layout.addWidget(frame)

        crypto = QFrame()
        crypto.setObjectName("supportCard")
        crypto_row = QHBoxLayout(crypto)
        crypto_row.setContentsMargins(18, 18, 18, 18)
        crypto_row.setSpacing(22)
        address = self.details.get("ethereum_address", "")
        qr = QLabel()
        qr.setObjectName("supportQr")
        qr.setFixedSize(180, 180)
        qr.setAlignment(Qt.AlignCenter)
        image = QPixmap(str(SUPPORT_ASSETS / "ethereum_address.png")) if qr_matches_address(address) else QPixmap()
        if not image.isNull():
            qr.setPixmap(image.scaled(180, 180, Qt.KeepAspectRatio, Qt.FastTransformation))
        else:
            qr.setText(support_text("unavailable"))
            qr.setWordWrap(True)
        qr.setAccessibleName(support_text("address"))
        crypto_row.addWidget(qr)
        details = QVBoxLayout()
        details.setSpacing(10)
        currencies = QLabel("USDT / ETH")
        currencies.setObjectName("supportCryptoTitle")
        details.addWidget(currencies)
        badge = QLabel(support_text("network") + " · USDT ERC-20")
        badge.setObjectName("supportNetwork")
        badge.setWordWrap(True)
        details.addWidget(badge)
        wallet = QLabel(address[:22] + "\n" + address[22:] if address else support_text("unavailable"))
        wallet.setObjectName("supportAddress")
        wallet.setTextInteractionFlags(Qt.TextSelectableByMouse)
        wallet.setAccessibleName(support_text("address"))
        details.addWidget(wallet)
        self.crypto_copy = CopyButton(address)
        self.crypto_copy.setAccessibleName(support_text("copy") + " — " + support_text("address"))
        details.addWidget(self.crypto_copy, 0, Qt.AlignLeft)
        warning = QLabel(support_text("network_warning"))
        warning.setObjectName("supportWarning")
        warning.setWordWrap(True)
        details.addWidget(warning)
        crypto_row.addLayout(details, 1)
        layout.addWidget(crypto)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton(support_text("close"))
        close.setObjectName("supportClose")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)


class SupportButton(QPushButton):
    """Always-visible entry point; the dialog opens only on an explicit click."""
    def __init__(self, parent=None):
        super().__init__(support_text("button"), parent)
        sizes = get_sizes()
        self.setObjectName("supportButton")
        self.setIcon(_heart_icon())
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(support_text("title"))
        self.setToolTip(support_text("title"))
        self.setMinimumHeight(sizes.scale(36))
        self.setStyleSheet(f"""
            QPushButton#supportButton {{ background: {ColorsV2.BG_ELEVATED}; color: {ColorsV2.TEXT_PRIMARY};
                border: 1px solid {ColorsV2.BORDER_DEFAULT}; border-radius: 8px; padding: 7px 15px; }}
            QPushButton#supportButton:hover {{ background: {ColorsV2.BG_HOVER}; border-color: {ColorsV2.ACCENT_CYAN}; }}
            QPushButton#supportButton:pressed {{ background: {ColorsV2.BG_INPUT}; }}
        """)
        self._dialog = None
        self.clicked.connect(self.open_dialog)

    def open_dialog(self):
        if self._dialog is None:
            self._dialog = SupportDialog(self.window())
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()


def _dialog_style() -> str:
    return f"""
        QDialog#supportDialog {{ background: {ColorsV2.BG_DARK}; }}
        QLabel {{ color: {ColorsV2.TEXT_PRIMARY}; border: none; background: transparent; font: 13px 'Segoe UI'; }}
        QLabel#supportTitle {{ font-size: 25px; font-weight: 600; }}
        QLabel#supportMuted {{ color: {ColorsV2.TEXT_SECONDARY}; font-size: 13px; }}
        QFrame#supportCard {{ background: {ColorsV2.BG_CARD}; border: 1px solid {ColorsV2.BORDER_DEFAULT}; border-radius: 12px; }}
        QLabel#supportMethod {{ color: {ColorsV2.TEXT_SECONDARY}; font-size: 13px; }}
        QLabel#supportNumber {{ font: 20px 'Consolas'; letter-spacing: 1px; }}
        QLabel#supportCryptoTitle {{ font-size: 22px; font-weight: 600; }}
        QLabel#supportNetwork {{ color: {ColorsV2.ACCENT_CYAN}; font-size: 12px; }}
        QLabel#supportAddress {{ font: 13px 'Consolas'; }}
        QLabel#supportWarning {{ color: {ColorsV2.ACCENT_ORANGE}; font-size: 12px; }}
        QLabel#supportQr {{ background: white; color: {ColorsV2.BG_DARK}; border-radius: 6px; }}
        QPushButton {{ font: 13px 'Segoe UI'; min-height: 22px; padding: 7px 13px;
            border-radius: 7px; border: 1px solid {ColorsV2.BORDER_DEFAULT}; color: {ColorsV2.TEXT_PRIMARY}; }}
        QPushButton#supportCopy {{ background: {ColorsV2.BG_ELEVATED}; min-width: 88px; }}
        QPushButton:hover {{ border-color: {ColorsV2.ACCENT_CYAN}; background: {ColorsV2.BG_HOVER}; }}
        QPushButton:pressed {{ background: {ColorsV2.BG_INPUT}; }}
        QPushButton:disabled {{ color: {ColorsV2.TEXT_MUTED}; background: {ColorsV2.BG_DARK}; border-color: {ColorsV2.BORDER_MUTED}; }}
        QPushButton#supportClose {{ background: transparent; }}
    """
