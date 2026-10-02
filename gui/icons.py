"""Small, dependency-free icon system for the desktop interface."""

from pathlib import Path

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import QAbstractButton, QGroupBox, QLabel, QPushButton, QTabWidget


_ACCENT = QColor("#49B9D6")
_SECONDARY = QColor("#2F7DD3")


def brand_asset_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "branding" / "contentbot_mark.png"


def brand_icon() -> QIcon:
    path = brand_asset_path()
    return QIcon(str(path)) if path.exists() else QIcon()


def clean_navigation_text(text: str) -> str:
    """Remove a leading emoji/symbol while preserving every written language."""
    value = str(text or "").strip()
    if value.startswith("<"):
        return value
    while value and not value[0].isalnum():
        value = value[1:].lstrip()
    return value


def _pen(color=_ACCENT, width=1.8) -> QPen:
    pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    return pen


def interface_icon(name: str, size: int = 22) -> QIcon:
    """Render a crisp two-color icon without relying on emoji fonts."""
    scale = 3
    canvas = QPixmap(size * scale, size * scale)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.scale(scale, scale)
    painter.setPen(_pen())
    painter.setBrush(Qt.NoBrush)

    if name == "generation":
        painter.drawRoundedRect(QRectF(2.5, 4, 17, 14), 3, 3)
        path = QPainterPath()
        path.moveTo(9, 8)
        path.lineTo(15, 11)
        path.lineTo(9, 14)
        path.closeSubpath()
        painter.setBrush(_SECONDARY)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)
    elif name == "series":
        painter.drawRoundedRect(QRectF(5, 3, 14, 11), 2, 2)
        painter.setPen(_pen(_SECONDARY))
        painter.drawRoundedRect(QRectF(3, 6, 14, 11), 2, 2)
        painter.setPen(_pen())
        painter.drawRoundedRect(QRectF(1, 9, 14, 11), 2, 2)
    elif name == "queue":
        for y in (5, 11, 17):
            painter.setPen(_pen(_SECONDARY))
            painter.drawEllipse(QPointF(4, y), 1.2, 1.2)
            painter.setPen(_pen())
            painter.drawLine(QPointF(8, y), QPointF(19, y))
    elif name == "monitor":
        painter.drawRoundedRect(QRectF(2, 3, 18, 15), 2, 2)
        painter.drawLine(QPointF(7, 21), QPointF(15, 21))
        painter.drawLine(QPointF(11, 18), QPointF(11, 21))
        painter.setPen(_pen(_SECONDARY))
        painter.drawLine(QPointF(5, 14), QPointF(8.5, 10))
        painter.drawLine(QPointF(8.5, 10), QPointF(12, 12))
        painter.drawLine(QPointF(12, 12), QPointF(17, 7))
    elif name == "analytics":
        painter.drawLine(QPointF(3, 19), QPointF(20, 19))
        painter.setBrush(_SECONDARY)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(4, 12, 3.5, 7), 1, 1)
        painter.drawRoundedRect(QRectF(9.3, 8, 3.5, 11), 1, 1)
        painter.drawRoundedRect(QRectF(14.6, 3, 3.5, 16), 1, 1)
    elif name == "audio":
        painter.drawRoundedRect(QRectF(8, 2, 6, 12), 3, 3)
        painter.drawLine(QPointF(5, 10), QPointF(5, 11))
        painter.drawArc(QRectF(4, 7, 14, 10), 180 * 16, 180 * 16)
        painter.drawLine(QPointF(11, 17), QPointF(11, 21))
        painter.setPen(_pen(_SECONDARY))
        painter.drawLine(QPointF(7, 21), QPointF(15, 21))
    elif name == "text":
        painter.drawRoundedRect(QRectF(3, 2, 16, 19), 2, 2)
        painter.setPen(_pen(_SECONDARY))
        for y, end in ((7, 16), (11, 16), (15, 13)):
            painter.drawLine(QPointF(7, y), QPointF(end, y))
    elif name == "static":
        painter.drawRoundedRect(QRectF(2, 3, 18, 16), 2, 2)
        painter.setPen(_pen(_SECONDARY))
        painter.drawEllipse(QPointF(15.5, 7.5), 1.6, 1.6)
        path = QPainterPath()
        path.moveTo(4.5, 16)
        path.lineTo(9, 11)
        path.lineTo(12, 14)
        path.lineTo(14, 12)
        path.lineTo(18, 16)
        painter.drawPath(path)
    elif name == "clone":
        painter.drawRoundedRect(QRectF(2, 4, 12, 10), 2, 2)
        painter.setPen(_pen(_SECONDARY))
        painter.drawRoundedRect(QRectF(8, 9, 12, 10), 2, 2)
        painter.drawLine(QPointF(5, 17), QPointF(5, 20))
        painter.drawLine(QPointF(5, 20), QPointF(9, 20))
        painter.drawLine(QPointF(17, 3), QPointF(17, 6))
        painter.drawLine(QPointF(17, 3), QPointF(13, 3))
    elif name == "youtube":
        painter.drawRoundedRect(QRectF(2, 5, 18, 12), 4, 4)
        path = QPainterPath()
        path.moveTo(9, 8)
        path.lineTo(15, 11)
        path.lineTo(9, 14)
        path.closeSubpath()
        painter.setBrush(_SECONDARY)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)
    elif name == "avatar":
        painter.drawEllipse(QPointF(11, 7), 4, 4)
        painter.drawArc(QRectF(4, 11, 14, 10), 0, 180 * 16)
        painter.setPen(_pen(_SECONDARY))
        painter.drawLine(QPointF(3, 4), QPointF(6, 4))
        painter.drawLine(QPointF(4.5, 2.5), QPointF(4.5, 5.5))
        painter.drawLine(QPointF(16, 4), QPointF(19, 4))
        painter.drawLine(QPointF(17.5, 2.5), QPointF(17.5, 5.5))
    elif name == "advanced":
        path = QPainterPath()
        path.moveTo(12, 1.5)
        path.lineTo(6.5, 11)
        path.lineTo(11, 11)
        path.lineTo(8.5, 20.5)
        path.lineTo(17.5, 8.5)
        path.lineTo(13, 8.5)
        path.closeSubpath()
        painter.setBrush(_SECONDARY)
        painter.setPen(_pen())
        painter.drawPath(path)
    elif name == "settings":
        painter.drawEllipse(QPointF(11, 11), 4.2, 4.2)
        painter.setPen(_pen(_SECONDARY))
        for x1, y1, x2, y2 in (
            (11, 2, 11, 5), (11, 17, 11, 20), (2, 11, 5, 11), (17, 11, 20, 11),
            (4.5, 4.5, 6.5, 6.5), (15.5, 15.5, 17.5, 17.5),
            (17.5, 4.5, 15.5, 6.5), (6.5, 15.5, 4.5, 17.5),
        ):
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    elif name in {"previous", "next"}:
        path = QPainterPath()
        if name == "previous":
            path.moveTo(14.5, 4)
            path.lineTo(7.5, 11)
            path.lineTo(14.5, 18)
        else:
            path.moveTo(7.5, 4)
            path.lineTo(14.5, 11)
            path.lineTo(7.5, 18)
        painter.setPen(_pen(_ACCENT, 2.4))
        painter.drawPath(path)
    else:
        painter.drawEllipse(QPointF(11, 11), 7, 7)

    painter.end()
    canvas.setDevicePixelRatio(scale)
    return QIcon(canvas)


def navigation_icon_size() -> QSize:
    return QSize(20, 20)


def _guess_icon_name(text: str) -> str:
    value = clean_navigation_text(text).lower()
    groups = (
        ("avatar", ("аватар", "avatar", "heygen")),
        ("analytics", ("аналит", "analytics", "metrics")),
        ("advanced", ("продвинут", "advanced", "performance", "эксперимент")),
        ("clone", ("канал", "channel", "копир", "clone", "remake", "ремейк")),
        ("static", ("изображ", "image", "photo", "картин", "статич", "визуал", "visual")),
        ("series", ("сери", "series")),
        ("queue", ("очеред", "queue", "список", "добав", "add", "import", "export")),
        ("monitor", ("монитор", "monitor", "log", "журнал", "preview", "тест", "test")),
        ("audio", ("аудио", "audio", "звук", "sound", "voice", "голос", "music", "музык")),
        ("text", ("текст", "text", "script", "сценар", "тема", "theme")),
        ("settings", ("настро", "settings", "папк", "folder", "api", "ключ", "key", "open", "откры")),
        ("generation", ("генера", "generate", "create", "созда", "start", "запуск", "video", "видео")),
    )
    for name, tokens in groups:
        if any(token in value for token in tokens):
            return name
    return ""


def modernize_widget_tree(root) -> None:
    """Remove font-dependent emoji and add consistent picture icons where useful."""
    for button in root.findChildren(QAbstractButton):
        button.setText(clean_navigation_text(button.text()))
        if isinstance(button, QPushButton) and button.icon().isNull():
            icon_name = _guess_icon_name(button.text())
            if icon_name:
                button.setIcon(interface_icon(icon_name))
                button.setIconSize(navigation_icon_size())
    for label in root.findChildren(QLabel):
        label.setText(clean_navigation_text(label.text()))
    for group in root.findChildren(QGroupBox):
        group.setTitle(clean_navigation_text(group.title()))
    for tabs in root.findChildren(QTabWidget):
        for index in range(tabs.count()):
            text = clean_navigation_text(tabs.tabText(index))
            tabs.setTabText(index, text)
            icon_name = _guess_icon_name(text)
            if icon_name:
                tabs.setTabIcon(index, interface_icon(icon_name))
