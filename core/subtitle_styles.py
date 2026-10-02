#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Small, testable helpers for ASS subtitle styling."""

import re
from typing import Any, Dict


PRESETS: Dict[str, Dict[str, Any]] = {
    "clean": {"font_color": "#FFFFFF", "highlight_color": "#FFD43B", "outline_color": "#000000", "outline_width": 3, "shadow_depth": 2, "bg_opacity": 0.0, "bold": True},
    "tiktok": {"font_color": "#FFFFFF", "highlight_color": "#00E5FF", "outline_color": "#000000", "outline_width": 7, "shadow_depth": 5, "bg_opacity": 0.0, "bold": True},
    "hormozi": {"font_color": "#FFFFFF", "highlight_color": "#FFD400", "outline_color": "#000000", "outline_width": 5, "shadow_depth": 3, "bg_opacity": 0.0, "bold": True},
    "boxed": {"font_color": "#FFFFFF", "highlight_color": "#FFD43B", "outline_color": "#000000", "outline_width": 1, "shadow_depth": 0, "bg_opacity": 0.72, "bold": True},
    "cinema": {"font_color": "#F5F1E8", "highlight_color": "#E8C66A", "outline_color": "#101010", "outline_width": 2, "shadow_depth": 3, "bg_opacity": 0.0, "bold": False},
    "minimal": {"font_color": "#FFFFFF", "highlight_color": "#FFFFFF", "outline_color": "#000000", "outline_width": 1, "shadow_depth": 1, "bg_opacity": 0.0, "bold": False},
}

NAMED_COLORS = {
    "white": "#FFFFFF", "black": "#000000", "red": "#FF0000", "green": "#00FF00",
    "blue": "#0000FF", "yellow": "#FFFF00", "cyan": "#00FFFF", "magenta": "#FF00FF",
}

POSITION_ALIASES = {
    "bottom": "bottom", "bottom center": "bottom", "bottom_center": "bottom", "bottom-center": "bottom",
    "center": "center", "centre": "center", "middle": "center",
    "top": "top", "top center": "top", "top_center": "top", "top-center": "top",
    "внизу": "bottom", "снизу": "bottom", "внизу по центру": "bottom",
    "по центру": "center", "центр": "center",
    "вверху": "top", "сверху": "top", "вверху по центру": "top",
    "abajo": "bottom", "centro": "center", "arriba": "top",
    "bas": "bottom", "haut": "top",
    "unten": "bottom", "mitte": "center", "oben": "top",
    "底部": "bottom", "下": "bottom", "中心": "center", "顶部": "top", "トップ": "top",
    "하단": "bottom", "센터": "center", "상단": "top",
    "inferior": "bottom", "topo": "top",
}


def normalize_hex_color(value: Any, fallback: str = "#FFFFFF") -> str:
    value = str(value or "").strip()
    value = NAMED_COLORS.get(value.lower(), value)
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value.upper()
    if re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return f"#{value.upper()}"
    return fallback.upper()


def normalize_subtitle_position(value: Any, fallback: str = "bottom") -> str:
    raw = str(value or "").strip().lower()
    fallback = str(fallback or "bottom").strip().lower()
    fallback = POSITION_ALIASES.get(fallback, fallback)
    if fallback not in {"bottom", "center", "top"}:
        fallback = "bottom"
    return POSITION_ALIASES.get(raw, fallback)


def color_to_ass(value: Any, alpha: int = 0, fallback: str = "#FFFFFF") -> str:
    rgb = normalize_hex_color(value, fallback).lstrip("#")
    red, green, blue = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H{max(0, min(255, int(alpha))):02X}{blue}{green}{red}"


def resolve_subtitle_style(settings: Dict[str, Any]) -> Dict[str, Any]:
    preset_name = str(settings.get("style_preset") or "tiktok").lower()
    result = dict(PRESETS.get(preset_name, PRESETS["tiktok"]))
    result["preset"] = preset_name if preset_name in PRESETS else "tiktok"
    for key in ("font_color", "highlight_color", "outline_color", "outline_width", "shadow_depth", "bg_color", "bg_opacity", "bold"):
        if key in settings:
            result[key] = settings[key]
    result["font_color"] = normalize_hex_color(result.get("font_color"), "#FFFFFF")
    result["highlight_color"] = normalize_hex_color(result.get("highlight_color"), "#FFD43B")
    result["outline_color"] = normalize_hex_color(result.get("outline_color"), "#000000")
    result["bg_color"] = normalize_hex_color(result.get("bg_color"), "#000000")
    result["outline_width"] = max(0, min(12, int(result.get("outline_width", 3))))
    result["shadow_depth"] = max(0, min(12, int(result.get("shadow_depth", 2))))
    result["bg_opacity"] = max(0.0, min(1.0, float(result.get("bg_opacity", 0.0))))
    result["bold"] = bool(result.get("bold", True))
    result["border_style"] = 3 if result["bg_opacity"] > 0 else 1
    return result


def apply_timing_offset(start: float, end: float, offset: float, duration: float) -> tuple:
    duration = max(0.05, float(duration))
    shifted_start = min(max(0.0, float(start) + float(offset)), duration - 0.05)
    shifted_end = min(float(duration), float(end) + float(offset))
    return shifted_start, max(shifted_start + 0.05, shifted_end)
