#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI Styles V2 - Modern UI Design
Enhanced visual design with better organization and modern look
"""

import logging

# =============================================================================
# ЦВЕТОВАЯ ПАЛИТРА V2 - Более современная
# =============================================================================

class ColorsV2:
    """Cold industrial dark palette for the ContentBot Pro workspace."""
    
    # Backgrounds - более глубокие тона
    BG_DARKEST = "#060A0F"
    BG_DARK = "#0A1118"
    BG_MEDIUM = "#101923"
    BG_LIGHT = "#172331"
    BG_HOVER = "#203044"
    BG_INPUT = "#071019"
    BG_CARD = "#0D1721"
    BG_ELEVATED = "#13202D"
    
    # Accent colors - яркие акценты
    ACCENT_BLUE = "#2F7DD3"
    ACCENT_BLUE_HOVER = "#4C9BEE"
    ACCENT_BLUE_ACTIVE = "#1E5F9E"
    ACCENT_GREEN = "#3DBB7F"
    ACCENT_GREEN_LIGHT = "#5ED79A"
    ACCENT_PURPLE = "#2F7DD3"  # compatibility alias; intentionally no purple hue
    ACCENT_ORANGE = "#D79A35"
    ACCENT_RED = "#E15B64"
    ACCENT_PINK = "#3A8CCB"  # compatibility alias; intentionally cooled down
    ACCENT_CYAN = "#49B9D6"
    
    # Text
    TEXT_PRIMARY = "#EAF2F8"
    TEXT_SECONDARY = "#A9B7C6"
    TEXT_MUTED = "#6F8092"
    TEXT_LINK = "#69AEEB"
    
    # Borders
    BORDER_DEFAULT = "#223244"
    BORDER_MUTED = "#162230"
    BORDER_FOCUS = "#4C9BEE"
    BORDER_SUCCESS = "#3DBB7F"
    
    # Gradients (for buttons)
    GRADIENT_BLUE = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1F5F99, stop:1 #49B9D6)"
    GRADIENT_BLUE_HOVER = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2877BE, stop:1 #61CBE3)"
    GRADIENT_PURPLE = GRADIENT_BLUE  # compatibility alias; intentionally no purple hue


class SizesV2:
    """Modern sizing constants - DPI and screen aware with resolution adaptation"""
    
    # Cache scale factor to avoid repeated calculations
    _scale_factor_cache = None
    
    @staticmethod
    def get_screen_scale_factor():
        """Get screen scale factor based on DPI and resolution (cached)"""
        # Return cached value if available
        if SizesV2._scale_factor_cache is not None:
            return SizesV2._scale_factor_cache
            
        try:
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                screen = app.primaryScreen()
                if screen:
                    # Get DPI
                    dpi = screen.logicalDotsPerInch()
                    dpi_scale = max(1.0, dpi / 96.0)
                    
                    # Get screen resolution
                    geometry = screen.geometry()
                    width = geometry.width()
                    height = geometry.height()
                    
                    # Calculate resolution scale factor
                    # Base resolution: 1920x1080 (Full HD)
                    base_width = 1920
                    base_height = 1080
                    
                    # Use the smaller dimension to determine scale
                    width_scale = width / base_width
                    height_scale = height / base_height
                    resolution_scale = min(width_scale, height_scale)
                    
                    # Combine DPI and resolution scaling
                    # Weight: 50% DPI, 50% resolution
                    combined_scale = (dpi_scale * 0.5) + (resolution_scale * 0.5)
                    
                    # Clamp between 0.8 and 2.5
                    final_scale = max(0.8, min(2.5, combined_scale))
                    
                    # Cache the result
                    SizesV2._scale_factor_cache = final_scale
                    
                    # Log only once when caching
                    logging.info(f"Screen scale cached: {width}x{height}, DPI: {dpi}, Scale: {final_scale:.2f}")
                    return final_scale
            
            # Cache default value
            SizesV2._scale_factor_cache = 1.0
            return 1.0
        except Exception as e:
            logging.debug(f"Failed to get screen scale factor: {e}")
            SizesV2._scale_factor_cache = 1.0
            return 1.0
    
    def scale(self, value):
        """Scale a value based on DPI"""
        return int(value * self.get_screen_scale_factor())
    
    # Window - now DPI aware
    @property
    def WINDOW_MIN_WIDTH(self):
        return self.scale(1000)
    
    @property 
    def WINDOW_MIN_HEIGHT(self):
        return self.scale(700)
        
    @property
    def WINDOW_DEFAULT_WIDTH(self):
        return self.scale(1200)
        
    @property
    def WINDOW_DEFAULT_HEIGHT(self):
        return self.scale(800)
    
    # Spacing - DPI aware
    @property
    def SPACING_XS(self):
        return self.scale(4)
    
    @property
    def SPACING_SM(self):
        return self.scale(8)
    
    @property
    def SPACING_MD(self):
        return self.scale(12)
    
    @property
    def SPACING_LG(self):
        return self.scale(16)
    
    @property
    def SPACING_XL(self):
        return self.scale(24)
    
    @property
    def SPACING_XXL(self):
        return self.scale(32)
    
    # Border radius - DPI aware
    @property
    def RADIUS_SM(self):
        return self.scale(4)
    
    @property
    def RADIUS_MD(self):
        return self.scale(6)
    
    @property
    def RADIUS_LG(self):
        return self.scale(8)
    
    @property
    def RADIUS_XL(self):
        return self.scale(12)
    
    RADIUS_FULL = 9999
    
    # Elements - DPI aware
    @property
    def INPUT_HEIGHT(self):
        return self.scale(36)
    
    @property
    def BUTTON_HEIGHT(self):
        return self.scale(36)
    
    @property
    def BUTTON_HEIGHT_LG(self):
        return self.scale(44)
    
    @property
    def TAB_HEIGHT(self):
        return self.scale(40)
    
    @property
    def ICON_SIZE(self):
        return self.scale(20)
    
    @property
    def ICON_BUTTON_SIZE(self):
        return self.scale(36)
    
    # Cards - DPI aware
    @property
    def CARD_PADDING(self):
        return self.scale(16)
    
    @property
    def CARD_RADIUS(self):
        return self.scale(12)
    
    # Font sizes - DPI and resolution aware
    @property
    def FONT_XS(self):
        return self.scale(11)
    
    @property
    def FONT_SM(self):
        return self.scale(12)
    
    @property
    def FONT_MD(self):
        return self.scale(13)
    
    @property
    def FONT_LG(self):
        return self.scale(14)
    
    @property
    def FONT_XL(self):
        return self.scale(16)
    
    @property
    def FONT_XXL(self):
        return self.scale(18)


def get_modern_style():
    """Main application style - modern dark theme with DPI awareness"""
    sizes = get_sizes()
    return f"""
        /* === GLOBAL === */
        QMainWindow, QWidget {{
            background-color: {ColorsV2.BG_DARKEST};
            color: {ColorsV2.TEXT_PRIMARY};
            font-family: 'Segoe UI', 'SF Pro Display', -apple-system, sans-serif;
            font-size: {sizes.FONT_MD}px;
        }}

        QFrame#brandHeader {{
            background-color: {ColorsV2.BG_DARK};
            border-bottom: 1px solid {ColorsV2.BORDER_MUTED};
        }}
        QLabel#brandMark {{
            background: transparent;
            border: none;
            border-radius: {sizes.RADIUS_LG}px;
        }}
        QLabel#brandTitle {{
            color: {ColorsV2.TEXT_PRIMARY};
            font-size: {sizes.FONT_XL}px;
            font-weight: 700;
        }}
        QLabel#brandSubtitle, QLabel#shellStatusDetail {{
            color: {ColorsV2.TEXT_MUTED};
            font-size: {sizes.FONT_XS}px;
        }}
        QLabel#shellStatus {{
            color: {ColorsV2.ACCENT_GREEN};
            font-size: {sizes.FONT_XS}px;
            font-weight: 700;
            letter-spacing: 1px;
        }}
        QFrame#mainNavigation {{
            background-color: {ColorsV2.BG_DARK};
            border-bottom: 1px solid {ColorsV2.BORDER_MUTED};
        }}
        QPushButton#mainNavButton {{
            background-color: transparent;
            color: {ColorsV2.TEXT_SECONDARY};
            border: 1px solid transparent;
            border-radius: {sizes.RADIUS_LG}px;
            padding: 7px 14px;
            min-height: 24px;
            font-size: {sizes.FONT_SM}px;
            font-weight: 600;
        }}
        QPushButton#mainNavButton:hover {{
            background-color: {ColorsV2.BG_LIGHT};
            color: {ColorsV2.TEXT_PRIMARY};
            border-color: {ColorsV2.BORDER_DEFAULT};
        }}
        QPushButton#mainNavButton:checked {{
            background-color: {ColorsV2.BG_ELEVATED};
            color: {ColorsV2.ACCENT_CYAN};
            border-color: #2B4055;
        }}
        QToolButton#mainNavScrollButton {{
            background-color: {ColorsV2.BG_ELEVATED};
            color: {ColorsV2.ACCENT_CYAN};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_MD}px;
            min-width: 28px;
            max-width: 28px;
            min-height: 28px;
            margin: 7px 4px;
            font-size: {sizes.FONT_XL}px;
            font-weight: 700;
        }}
        QToolButton#mainNavScrollButton:hover {{
            background-color: {ColorsV2.BG_LIGHT};
            border-color: {ColorsV2.ACCENT_CYAN};
        }}
        QToolButton#mainNavScrollButton:disabled {{
            color: {ColorsV2.TEXT_MUTED};
            border-color: {ColorsV2.BORDER_MUTED};
        }}
        QFrame#commandDock {{
            background-color: {ColorsV2.BG_DARK};
            border-top: 1px solid {ColorsV2.BORDER_MUTED};
        }}
        
        /* === SCROLL AREA === */
        QScrollArea {{
            border: none;
            background-color: transparent;
        }}
        QScrollArea > QWidget > QWidget {{
            background-color: transparent;
        }}
        
        /* === GROUP BOX (Cards) === */
        QGroupBox {{
            background-color: {ColorsV2.BG_CARD};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_XL}px;
            margin-top: 16px;
            padding: 16px;
            padding-top: 24px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 16px;
            top: 4px;
            padding: 0 8px;
            color: {ColorsV2.TEXT_PRIMARY};
            font-size: {sizes.FONT_LG}px;
        }}
        
        /* === BUTTONS === */
        QPushButton {{
            background-color: {ColorsV2.BG_ELEVATED};
            color: {ColorsV2.TEXT_PRIMARY};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_LG}px;
            padding: 8px 16px;
            font-weight: 500;
            min-height: {sizes.BUTTON_HEIGHT}px;
        }}
        QPushButton:hover {{
            background-color: {ColorsV2.BG_HOVER};
            border-color: {ColorsV2.BORDER_FOCUS};
        }}
        QPushButton:pressed {{
            background-color: {ColorsV2.BG_MEDIUM};
            border-color: {ColorsV2.ACCENT_CYAN};
        }}
        QPushButton:focus, QToolButton:focus {{
            border: 2px solid {ColorsV2.ACCENT_CYAN};
        }}
        QPushButton:disabled {{
            background-color: {ColorsV2.BG_MEDIUM};
            color: {ColorsV2.TEXT_MUTED};
            border-color: {ColorsV2.BORDER_MUTED};
        }}
        
        /* Primary button */
        QPushButton[class="primary"] {{
            background-color: {ColorsV2.ACCENT_GREEN};
            border: none;
            color: white;
        }}
        QPushButton[class="primary"]:hover {{
            background-color: {ColorsV2.ACCENT_GREEN_LIGHT};
        }}
        
        /* Danger button */
        QPushButton[class="danger"] {{
            background-color: {ColorsV2.ACCENT_RED};
            border: none;
            color: white;
        }}
        
        /* === INPUTS === */
        QLineEdit, QSpinBox, QDoubleSpinBox {{
            background-color: {ColorsV2.BG_INPUT};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_LG}px;
            padding: 8px 12px;
            color: {ColorsV2.TEXT_PRIMARY};
            min-height: {sizes.INPUT_HEIGHT}px;
            selection-background-color: {ColorsV2.ACCENT_BLUE};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
        QSpinBox:focus, QDoubleSpinBox:focus {{
            border-color: {ColorsV2.BORDER_FOCUS};
            background-color: {ColorsV2.BG_DARK};
        }}
        QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
            background-color: {ColorsV2.BG_MEDIUM};
            color: {ColorsV2.TEXT_MUTED};
        }}
        
        /* === COMBOBOX === */
        QComboBox {{
            background-color: {ColorsV2.BG_INPUT};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_LG}px;
            padding: 8px 12px;
            padding-right: 30px;
            color: {ColorsV2.TEXT_PRIMARY};
            min-height: {sizes.INPUT_HEIGHT}px;
        }}
        QComboBox:hover {{
            border-color: {ColorsV2.BORDER_FOCUS};
        }}
        QComboBox:focus {{
            border: 2px solid {ColorsV2.ACCENT_CYAN};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 30px;
        }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid {ColorsV2.TEXT_SECONDARY};
            margin-right: 10px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {ColorsV2.BG_DARK};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_MD}px;
            selection-background-color: {ColorsV2.BG_HOVER};
            padding: 4px;
        }}

        QHeaderView::section {{
            background-color: {ColorsV2.BG_ELEVATED};
            color: {ColorsV2.TEXT_SECONDARY};
            border: none;
            border-right: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-bottom: 1px solid {ColorsV2.BORDER_DEFAULT};
            padding: 8px 10px;
            font-weight: 600;
        }}
        
        /* === CHECKBOX === */
        QCheckBox {{
            spacing: 8px;
            color: {ColorsV2.TEXT_PRIMARY};
        }}
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border-radius: 4px;
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            background-color: {ColorsV2.BG_INPUT};
        }}
        QCheckBox::indicator:hover {{
            border-color: {ColorsV2.BORDER_FOCUS};
        }}
        QCheckBox::indicator:checked {{
            background-color: {ColorsV2.ACCENT_BLUE};
            border-color: {ColorsV2.ACCENT_BLUE};
        }}
        QCheckBox::indicator:checked:hover {{
            background-color: {ColorsV2.ACCENT_BLUE_HOVER};
        }}
        QCheckBox:focus {{
            color: {ColorsV2.TEXT_PRIMARY};
        }}
        QCheckBox::indicator:focus {{
            border: 2px solid {ColorsV2.ACCENT_CYAN};
        }}
        
        /* === SLIDER === */
        QSlider::groove:horizontal {{
            height: 6px;
            background-color: {ColorsV2.BG_LIGHT};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            width: 16px;
            height: 16px;
            margin: -5px 0;
            background-color: {ColorsV2.ACCENT_BLUE};
            border-radius: 8px;
        }}
        QSlider::handle:horizontal:hover {{
            background-color: {ColorsV2.ACCENT_BLUE_HOVER};
        }}
        QSlider::sub-page:horizontal {{
            background-color: {ColorsV2.ACCENT_BLUE};
            border-radius: 3px;
        }}
        
        /* === PROGRESS BAR === */
        QProgressBar {{
            background-color: {ColorsV2.BG_LIGHT};
            border: none;
            border-radius: {sizes.RADIUS_MD}px;
            text-align: center;
            color: {ColorsV2.TEXT_PRIMARY};
            font-weight: 600;
            min-height: 24px;
        }}
        QProgressBar::chunk {{
            background-color: {ColorsV2.ACCENT_GREEN};
            border-radius: {sizes.RADIUS_MD}px;
        }}
        
        /* === TEXT EDIT (Logs) === */
        QTextEdit {{
            background-color: {ColorsV2.BG_INPUT};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_MD}px;
            padding: 12px;
            color: {ColorsV2.TEXT_PRIMARY};
            font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
            font-size: {sizes.FONT_SM}px;
        }}
        
        /* === LABELS === */
        QLabel {{
            background-color: transparent;
            color: {ColorsV2.TEXT_PRIMARY};
        }}
        QCheckBox, QRadioButton {{
            background-color: transparent;
        }}
        QLabel[class="muted"] {{
            color: {ColorsV2.TEXT_MUTED};
            font-size: {sizes.FONT_SM}px;
        }}
        QLabel[class="header"] {{
            font-size: {sizes.FONT_XXL}px;
            font-weight: 600;
            color: {ColorsV2.TEXT_PRIMARY};
        }}
        
        /* === SCROLLBAR === */
        QScrollBar:vertical {{
            background-color: transparent;
            width: 8px;
            margin: 0;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background-color: {ColorsV2.BG_HOVER};
            border-radius: 4px;
            min-height: 40px;
            margin: 1px;
        }}
        QScrollBar::handle:vertical:hover {{
            background-color: {ColorsV2.ACCENT_BLUE_HOVER};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: none;
        }}
        
        /* === SPLITTER === */
        QSplitter::handle {{
            background-color: {ColorsV2.BORDER_DEFAULT};
        }}
        QSplitter::handle:horizontal {{
            width: 2px;
        }}
        QSplitter::handle:vertical {{
            height: 2px;
        }}
        
        /* === STATUS BAR === */
        QStatusBar {{
            background-color: {ColorsV2.BG_DARK};
            color: {ColorsV2.TEXT_SECONDARY};
            border-top: 1px solid {ColorsV2.BORDER_DEFAULT};
            padding: 4px 12px;
        }}
        
        /* === TOOLTIP === */
        QToolTip {{
            background-color: {ColorsV2.BG_DARK};
            color: {ColorsV2.TEXT_PRIMARY};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_SM}px;
            padding: 8px 12px;
            font-size: {sizes.FONT_SM}px;
        }}
    """


def get_tab_style_v2():
    """Borderless style for the main content stack."""
    sizes = get_sizes()
    return f"""
        QTabWidget::pane {{
            border: none;
            background-color: {ColorsV2.BG_DARKEST};
        }}
        
        QTabBar {{
            background-color: transparent;
        }}
        
        QTabBar::tab {{
            background-color: {ColorsV2.BG_MEDIUM};
            color: {ColorsV2.TEXT_SECONDARY};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-bottom: none;
            border-top-left-radius: {sizes.RADIUS_LG}px;
            border-top-right-radius: {sizes.RADIUS_LG}px;
            padding: 10px 20px;
            margin-right: 2px;
            font-weight: 500;
            font-size: {sizes.FONT_MD}px;
            min-width: 120px;
        }}
        
        QTabBar::tab:selected {{
            background-color: {ColorsV2.BG_DARKEST};
            color: {ColorsV2.ACCENT_CYAN};
            border-color: {ColorsV2.ACCENT_CYAN};
            border-bottom: 1px solid {ColorsV2.BG_DARKEST};
        }}
        
        QTabBar::tab:hover:!selected {{
            background-color: {ColorsV2.BG_HOVER};
            color: {ColorsV2.TEXT_PRIMARY};
        }}
    """


def get_sub_tab_style():
    """Compact pill navigation for nested tabs."""
    sizes = get_sizes()
    return f"""
        QTabWidget::pane {{
            border: none;
            background-color: transparent;
            padding-top: 8px;
        }}
        
        QTabBar::tab {{
            background-color: {ColorsV2.BG_DARK};
            color: {ColorsV2.TEXT_SECONDARY};
            border: 1px solid {ColorsV2.BORDER_MUTED};
            border-radius: {sizes.RADIUS_LG}px;
            padding: 8px 14px;
            margin-right: 6px;
            font-weight: 600;
            font-size: {sizes.FONT_MD}px;
        }}
        
        QTabBar::tab:selected {{
            color: {ColorsV2.ACCENT_CYAN};
            background-color: {ColorsV2.BG_ELEVATED};
            border: 1px solid #2B4055;
        }}
        
        QTabBar::tab:hover:!selected {{
            color: {ColorsV2.TEXT_PRIMARY};
            background-color: {ColorsV2.BG_LIGHT};
            border-color: {ColorsV2.BORDER_DEFAULT};
        }}
    """


def get_card_style():
    """Style for card-like containers"""
    sizes = get_sizes()
    return f"""
        background-color: {ColorsV2.BG_CARD};
        border: 1px solid {ColorsV2.BORDER_DEFAULT};
        border-radius: {sizes.RADIUS_LG}px;
        padding: {sizes.CARD_PADDING}px;
    """


def get_primary_button_style():
    """High-contrast primary action button."""
    sizes = get_sizes()
    return f"""
        QPushButton {{
            background: {ColorsV2.GRADIENT_BLUE};
            color: {ColorsV2.TEXT_PRIMARY};
            border: none;
            border-radius: {sizes.RADIUS_LG}px;
            padding: 12px 32px;
            font-weight: 600;
            font-size: {sizes.FONT_LG}px;
            min-height: {sizes.BUTTON_HEIGHT_LG}px;
        }}
        QPushButton:hover {{
            background: {ColorsV2.GRADIENT_BLUE_HOVER};
        }}
        QPushButton:pressed {{
            background-color: {ColorsV2.ACCENT_BLUE_ACTIVE};
        }}
        QPushButton:disabled {{
            background-color: {ColorsV2.BG_LIGHT};
            color: {ColorsV2.TEXT_MUTED};
        }}
    """


def get_danger_button_style():
    """Red danger/stop button with DPI awareness"""
    sizes = get_sizes()
    return f"""
        QPushButton {{
            background-color: transparent;
            color: {ColorsV2.ACCENT_RED};
            border: 1px solid #5B3135;
            border-radius: {sizes.RADIUS_LG}px;
            padding: 12px 24px;
            font-weight: 600;
            font-size: {sizes.FONT_LG}px;
            min-height: {sizes.BUTTON_HEIGHT_LG}px;
        }}
        QPushButton:hover {{
            background-color: #261418;
            border-color: {ColorsV2.ACCENT_RED};
        }}
        QPushButton:disabled {{
            background-color: {ColorsV2.BG_LIGHT};
            color: {ColorsV2.TEXT_MUTED};
        }}
    """


def get_icon_button_style():
    """Small icon button style"""
    sizes = get_sizes()
    return f"""
        QPushButton {{
            background-color: transparent;
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_SM}px;
            padding: 6px;
            min-width: 32px;
            max-width: 32px;
            min-height: 32px;
            max-height: 32px;
        }}
        QPushButton:hover {{
            background-color: {ColorsV2.BG_HOVER};
            border-color: {ColorsV2.BORDER_FOCUS};
        }}
    """


def get_ghost_button_style():
    """Subtle action button used inside cards and toolbars."""
    sizes = get_sizes()
    return f"""
        QPushButton {{
            background-color: {ColorsV2.BG_ELEVATED};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_LG}px;
            padding: 6px 12px;
            color: {ColorsV2.TEXT_SECONDARY};
            font-size: {sizes.FONT_SM}px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: {ColorsV2.BG_HOVER};
            border-color: {ColorsV2.BORDER_FOCUS};
            color: {ColorsV2.TEXT_PRIMARY};
        }}
    """


def get_section_header_style():
    """Style for section headers"""
    sizes = get_sizes()
    return f"""
        QLabel {{
            color: {ColorsV2.TEXT_PRIMARY};
            font-size: {sizes.FONT_XL}px;
            font-weight: 600;
            padding: 8px 0;
        }}
    """


# Create singleton instance for DPI-aware sizing
_sizes_instance = SizesV2()

def get_sizes():
    """Get the DPI-aware sizes instance"""
    return _sizes_instance

# =============================================================================
# COMPATIBILITY ALIASES - для series_tab_qt.py и других модулей
# =============================================================================

# Алиасы классов
Colors = ColorsV2
Sizes = SizesV2


def get_group_box_style():
    """GroupBox style - alias for compatibility"""
    sizes = get_sizes()
    return f"""
        QGroupBox {{
            background-color: {ColorsV2.BG_CARD};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_LG}px;
            margin-top: 16px;
            padding: 16px;
            padding-top: 28px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 16px;
            top: 4px;
            padding: 0 8px;
            color: {ColorsV2.TEXT_PRIMARY};
            font-size: {sizes.FONT_LG}px;
        }}
    """


def get_input_style():
    """Input field style"""
    sizes = get_sizes()
    return f"""
        QLineEdit {{
            background-color: {ColorsV2.BG_INPUT};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_MD}px;
            padding: 8px 12px;
            color: {ColorsV2.TEXT_PRIMARY};
            min-height: {sizes.INPUT_HEIGHT}px;
        }}
        QLineEdit:focus {{
            border-color: {ColorsV2.BORDER_FOCUS};
        }}
    """


def get_spin_box_style():
    """SpinBox style"""
    sizes = get_sizes()
    return f"""
        QSpinBox {{
            background-color: {ColorsV2.BG_INPUT};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_MD}px;
            padding: 6px 10px;
            color: {ColorsV2.TEXT_PRIMARY};
            min-height: 32px;
        }}
        QSpinBox:focus {{
            border-color: {ColorsV2.BORDER_FOCUS};
        }}
    """


def get_text_edit_style():
    """TextEdit style"""
    sizes = get_sizes()
    return f"""
        QTextEdit {{
            background-color: {ColorsV2.BG_INPUT};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_MD}px;
            padding: 10px;
            color: {ColorsV2.TEXT_PRIMARY};
        }}
        QTextEdit:focus {{
            border-color: {ColorsV2.BORDER_FOCUS};
        }}
    """


def get_log_text_style():
    """Log text area style (monospace)"""
    sizes = get_sizes()
    return f"""
        QTextEdit {{
            background-color: {ColorsV2.BG_DARKEST};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: {sizes.RADIUS_MD}px;
            padding: 10px;
            color: {ColorsV2.TEXT_PRIMARY};
            font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
            font-size: {sizes.FONT_SM}px;
        }}
    """


def get_progress_bar_style():
    """Progress bar style"""
    sizes = get_sizes()
    return f"""
        QProgressBar {{
            background-color: {ColorsV2.BG_LIGHT};
            border: none;
            border-radius: {sizes.RADIUS_MD}px;
            text-align: center;
            color: {ColorsV2.TEXT_PRIMARY};
            font-weight: 600;
            min-height: 24px;
        }}
        QProgressBar::chunk {{
            background-color: {ColorsV2.ACCENT_GREEN};
            border-radius: {sizes.RADIUS_MD}px;
        }}
    """


def get_success_button_style():
    """Green success button - alias for get_primary_button_style"""
    return get_primary_button_style()


def get_suggestion_button_style():
    """Small suggestion/tag button style"""
    sizes = get_sizes()
    return f"""
        QPushButton {{
            background-color: {ColorsV2.BG_LIGHT};
            border: 1px solid {ColorsV2.BORDER_DEFAULT};
            border-radius: 16px;
            padding: 6px 12px;
            font-size: {sizes.FONT_SM}px;
            color: {ColorsV2.TEXT_SECONDARY};
        }}
        QPushButton:hover {{
            background-color: {ColorsV2.BG_HOVER};
            color: {ColorsV2.TEXT_PRIMARY};
        }}
    """


def get_ai_button_style():
    """AI/special action button style."""
    sizes = get_sizes()
    return f"""
        QPushButton {{
            background-color: {ColorsV2.ACCENT_BLUE};
            color: white;
            border: none;
            border-radius: {sizes.RADIUS_MD}px;
            padding: 10px 20px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: {ColorsV2.ACCENT_BLUE_HOVER};
        }}
        QPushButton:disabled {{
            background-color: {ColorsV2.BG_LIGHT};
            color: {ColorsV2.TEXT_MUTED};
        }}
    """
