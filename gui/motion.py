"""Small, consistent motion primitives for the desktop interface.

The effects deliberately animate only paint/opacity/scroll values. They never
move form controls inside layouts, so enabling motion cannot change geometry or
the saved application state.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from PyQt5.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QSettings,
    QTimer,
    Qt,
)
from PyQt5.QtWidgets import (
    QAbstractButton,
    QApplication,
    QGraphicsOpacityEffect,
    QPushButton,
    QTabWidget,
    QToolButton,
    QWidget,
)


MOTION_FAST_MS = 110
MOTION_STANDARD_MS = 180
MOTION_SCROLL_MS = 220


def system_prefers_reduced_motion() -> bool:
    """Honor an explicit override and the Windows animation preference."""
    override = os.environ.get("CONTENTBOT_REDUCE_MOTION", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    if sys.platform != "win32":
        return False
    try:
        settings = QSettings(
            r"HKEY_CURRENT_USER\Control Panel\Desktop\WindowMetrics",
            QSettings.NativeFormat,
        )
        value = str(settings.value("MinAnimate", "1") or "1").strip().lower()
        return value in {"0", "false", "no", "off"}
    except Exception:
        return False


def set_motion_enabled(enabled: bool) -> None:
    app = QApplication.instance()
    if app is not None:
        app.setProperty("contentbotMotionEnabled", bool(enabled))


def motion_enabled() -> bool:
    if system_prefers_reduced_motion():
        return False
    app = QApplication.instance()
    if app is None:
        return True
    value = app.property("contentbotMotionEnabled")
    return True if value is None else bool(value)


class _TabFadeController(QObject):
    def __init__(self, tabs: QTabWidget):
        super().__init__(tabs)
        self.tabs = tabs
        self.animation: Optional[QPropertyAnimation] = None
        self.effect: Optional[QGraphicsOpacityEffect] = None
        self.widget: Optional[QWidget] = None
        tabs.currentChanged.connect(self.animate_current)

    def animate_current(self, _index: int) -> None:
        if not motion_enabled():
            self._clear()
            return
        widget = self.tabs.currentWidget()
        if widget is None:
            return
        self._clear()
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.58)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setStartValue(0.58)
        animation.setEndValue(1.0)
        animation.setDuration(MOTION_STANDARD_MS)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(self._clear)
        self.widget = widget
        self.effect = effect
        self.animation = animation
        animation.start()

    def _clear(self) -> None:
        animation = self.animation
        self.animation = None
        if animation is not None and animation.state() != QPropertyAnimation.Stopped:
            animation.stop()
        widget = self.widget
        effect = self.effect
        self.widget = None
        self.effect = None
        if widget is not None and widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)


class MotionController(QObject):
    """Installs motion on existing and subsequently added buttons/tabs."""

    def __init__(self, root: QWidget):
        super().__init__(root)
        self.root = root
        self._buttons = []
        self._tab_controllers = []
        root.installEventFilter(self)
        self.scan()

    def scan(self) -> None:
        for button in self.root.findChildren(QAbstractButton):
            if not isinstance(button, (QPushButton, QToolButton)):
                continue
            if button.property("contentbotMotionInstalled"):
                continue
            if button.graphicsEffect() is not None:
                continue
            button.setProperty("contentbotMotionInstalled", True)
            effect = QGraphicsOpacityEffect(button)
            effect.setOpacity(1.0)
            button.setGraphicsEffect(effect)
            button._contentbot_motion_effect = effect
            button._contentbot_motion_animation = None
            button.installEventFilter(self)
            self._buttons.append(button)
        for tabs in self.root.findChildren(QTabWidget):
            if tabs.property("contentbotMotionInstalled"):
                continue
            tabs.setProperty("contentbotMotionInstalled", True)
            controller = _TabFadeController(tabs)
            tabs._contentbot_fade_controller = controller
            self._tab_controllers.append(controller)

    def eventFilter(self, watched: QObject, event) -> bool:
        if watched is self.root and event.type() == QEvent.ChildAdded:
            QTimer.singleShot(0, self.scan)
            return False
        if not isinstance(watched, (QPushButton, QToolButton)):
            return False
        effect = getattr(watched, "_contentbot_motion_effect", None)
        if effect is None:
            return False
        event_type = event.type()
        if event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._press(watched, effect)
        elif event_type == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            self._release(watched, effect)
        elif event_type == QEvent.KeyPress and event.key() in {Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter}:
            self._press(watched, effect)
        elif event_type == QEvent.KeyRelease and event.key() in {Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter}:
            self._release(watched, effect)
        elif event_type in {QEvent.Leave, QEvent.EnabledChange, QEvent.Hide}:
            self._release(watched, effect)
        return False

    @staticmethod
    def _press(button: QAbstractButton, effect: QGraphicsOpacityEffect) -> None:
        animation = getattr(button, "_contentbot_motion_animation", None)
        if animation is not None:
            animation.stop()
        effect.setOpacity(0.84 if motion_enabled() and button.isEnabled() else 1.0)

    @staticmethod
    def _release(button: QAbstractButton, effect: QGraphicsOpacityEffect) -> None:
        animation = getattr(button, "_contentbot_motion_animation", None)
        if animation is not None:
            animation.stop()
        if not motion_enabled() or not button.isEnabled():
            effect.setOpacity(1.0)
            return
        animation = QPropertyAnimation(effect, b"opacity", button)
        animation.setStartValue(effect.opacity())
        animation.setEndValue(1.0)
        animation.setDuration(MOTION_STANDARD_MS)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        button._contentbot_motion_animation = animation
        animation.start()


def install_motion(root: QWidget) -> MotionController:
    controller = getattr(root, "_contentbot_motion_controller", None)
    if controller is None:
        controller = MotionController(root)
        root._contentbot_motion_controller = controller
    else:
        controller.scan()
    return controller
