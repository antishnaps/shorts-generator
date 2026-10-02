"""Reusable widgets for the ContentBot Pro application shell."""

from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.styles_v2 import ColorsV2, get_sizes
from gui.translations import t
from gui.icons import (
    brand_asset_path,
    clean_navigation_text,
    interface_icon,
    navigation_icon_size,
)
from gui.motion import MOTION_SCROLL_MS, motion_enabled


class BrandHeader(QFrame):
    """Compact branded header with a live application status."""

    def __init__(self, parent=None, *, show_support=False):
        super().__init__(parent)
        sizes = get_sizes()
        self.setObjectName("brandHeader")
        self.setFixedHeight(sizes.scale(76))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            sizes.SPACING_XL, sizes.SPACING_MD, sizes.SPACING_XL, sizes.SPACING_MD
        )
        layout.setSpacing(sizes.SPACING_MD)

        mark = QLabel()
        mark.setObjectName("brandMark")
        mark.setAccessibleName("ContentBot Pro")
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(sizes.scale(46), sizes.scale(46))
        brand_path = brand_asset_path()
        if brand_path.exists():
            mark.setPixmap(
                QPixmap(str(brand_path)).scaled(
                    mark.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        else:
            mark.setText("CB")
        layout.addWidget(mark)

        brand = QVBoxLayout()
        brand.setSpacing(1)
        title = QLabel("ContentBot Pro")
        title.setObjectName("brandTitle")
        subtitle = QLabel(t('shell_subtitle'))
        subtitle.setObjectName("brandSubtitle")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        layout.addLayout(brand)
        layout.addStretch(1)

        self.status_dot = QLabel()
        self.status_dot.setObjectName("statusDot")
        self.status_dot.setFixedSize(sizes.scale(8), sizes.scale(8))
        layout.addWidget(self.status_dot)

        status = QVBoxLayout()
        status.setSpacing(0)
        self.status_label = QLabel(t('shell_ready'))
        self.status_label.setObjectName("shellStatus")
        self.status_detail = QLabel(t('shell_workspace_ready'))
        self.status_detail.setObjectName("shellStatusDetail")
        status.addWidget(self.status_label, 0, Qt.AlignRight)
        status.addWidget(self.status_detail, 0, Qt.AlignRight)
        layout.addLayout(status)

        if show_support:
            from gui.donations import SupportButton

            self.support_button = SupportButton(self)
            layout.addWidget(self.support_button)

        self.set_status(t('shell_ready'), t('shell_workspace_ready'), "success")

    def set_status(self, label, detail="", tone="success"):
        tones = {
            "success": ColorsV2.ACCENT_GREEN,
            "busy": ColorsV2.ACCENT_CYAN,
            "warning": ColorsV2.ACCENT_ORANGE,
            "danger": ColorsV2.ACCENT_RED,
        }
        color = tones.get(tone, ColorsV2.TEXT_SECONDARY)
        self.status_label.setText(str(label).upper())
        self.status_detail.setText(detail)
        self.status_dot.setStyleSheet(
            f"background-color: {color}; border: none; border-radius: 4px;"
        )
        self.status_label.setStyleSheet(f"color: {color};")


class MainNavigation(QFrame):
    """Pill navigation that mirrors the hidden main QTabWidget.
    
    Wraps buttons in a horizontal scroll area so they don't overlap
    when the window is narrower than the total button width.
    """

    tab_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        sizes = get_sizes()
        self.setObjectName("mainNavigation")
        self._buttons = []
        self._scroll_animation = None

        # Outer layout holds the scroll area
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._previous_button = QToolButton()
        self._previous_button.setObjectName("mainNavScrollButton")
        self._previous_button.setIcon(interface_icon("previous"))
        self._previous_button.setIconSize(navigation_icon_size())
        self._previous_button.setToolTip(t("nav_previous"))
        self._previous_button.setAccessibleName(t("nav_previous"))
        self._previous_button.setCursor(Qt.PointingHandCursor)
        self._previous_button.clicked.connect(lambda: self._scroll_by(-1))
        outer.addWidget(self._previous_button)

        # Scrollable container for nav buttons
        self._scroll = QScrollArea()
        self._scroll.setObjectName("navScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )

        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._layout = QHBoxLayout(self._inner)
        self._layout.setContentsMargins(
            sizes.SPACING_XL, sizes.SPACING_SM, sizes.SPACING_XL, sizes.SPACING_SM
        )
        self._layout.setSpacing(sizes.SPACING_XS)
        self._layout.addStretch(1)

        self._scroll.setWidget(self._inner)
        outer.addWidget(self._scroll)

        self._next_button = QToolButton()
        self._next_button.setObjectName("mainNavScrollButton")
        self._next_button.setIcon(interface_icon("next"))
        self._next_button.setIconSize(navigation_icon_size())
        self._next_button.setToolTip(t("nav_next"))
        self._next_button.setAccessibleName(t("nav_next"))
        self._next_button.setCursor(Qt.PointingHandCursor)
        self._next_button.clicked.connect(lambda: self._scroll_by(1))
        outer.addWidget(self._next_button)

        bar = self._scroll.horizontalScrollBar()
        bar.rangeChanged.connect(lambda *_: self._update_scroll_buttons())
        bar.valueChanged.connect(lambda *_: self._update_scroll_buttons())
        self._update_scroll_buttons()

    def set_items(self, items):
        while self._buttons:
            self._buttons.pop().deleteLater()

        for index, item in enumerate(items):
            if isinstance(item, (tuple, list)):
                label, icon_name = item
            else:
                label, icon_name = item, ""
            button = QPushButton(clean_navigation_text(label))
            button.setObjectName("mainNavButton")
            button.setIcon(interface_icon(icon_name))
            button.setIconSize(navigation_icon_size())
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            button.clicked.connect(
                lambda checked=False, tab_index=index: self.tab_selected.emit(tab_index)
            )
            self._layout.insertWidget(self._layout.count() - 1, button)
            self._buttons.append(button)

        # QScrollArea may otherwise squeeze the inner widget below its layout
        # size hint, leaving the last navigation entries clipped with no
        # visible way to reach them on laptop-sized windows.
        self._inner.setMinimumWidth(self._inner.sizeHint().width())
        self.set_current(0)
        QTimer.singleShot(0, self._update_scroll_buttons)

    def set_current(self, index):
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)
            # Scroll to make selected button visible
            btn = self._buttons[index]
            self._scroll.ensureWidgetVisible(btn, 50, 0)
            QTimer.singleShot(0, self._update_scroll_buttons)

    def _scroll_by(self, direction):
        bar = self._scroll.horizontalScrollBar()
        step = max(160, int(self._scroll.viewport().width() * 0.65))
        target = max(bar.minimum(), min(bar.maximum(), bar.value() + int(direction) * step))
        if not motion_enabled():
            bar.setValue(target)
            return
        if self._scroll_animation is not None:
            self._scroll_animation.stop()
        animation = QPropertyAnimation(bar, b"value", self)
        animation.setStartValue(bar.value())
        animation.setEndValue(target)
        animation.setDuration(MOTION_SCROLL_MS)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._scroll_animation = animation
        animation.start()

    def _update_scroll_buttons(self):
        bar = self._scroll.horizontalScrollBar()
        overflow = bar.maximum() > bar.minimum()
        self._previous_button.setVisible(overflow)
        self._next_button.setVisible(overflow)
        self._previous_button.setEnabled(overflow and bar.value() > bar.minimum())
        self._next_button.setEnabled(overflow and bar.value() < bar.maximum())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_scroll_buttons)

    @property
    def buttons(self):
        return tuple(self._buttons)
