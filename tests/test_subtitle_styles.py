from core.subtitle_styles import (
    apply_timing_offset,
    color_to_ass,
    normalize_hex_color,
    resolve_subtitle_style,
)


def test_color_to_ass_converts_rgb_to_ass_bgr():
    assert color_to_ass("#FF0000") == "&H000000FF"
    assert color_to_ass("#112233", 128) == "&H80332211"


def test_named_and_invalid_colors_are_normalized():
    assert normalize_hex_color("yellow") == "#FFFF00"
    assert normalize_hex_color("not-a-color", "#123456") == "#123456"


def test_boxed_preset_enables_opaque_border_style():
    style = resolve_subtitle_style({"style_preset": "boxed"})
    assert style["border_style"] == 3
    assert style["bg_opacity"] > 0


def test_tiktok_preset_is_high_retention_and_readable():
    style = resolve_subtitle_style({"style_preset": "tiktok"})
    assert style["preset"] == "tiktok"
    assert style["highlight_color"] == "#00E5FF"
    assert style["outline_width"] >= 7
    assert style["shadow_depth"] >= 5


def test_explicit_style_values_override_preset():
    style = resolve_subtitle_style({"style_preset": "minimal", "outline_width": 7, "font_color": "#ABCDEF"})
    assert style["outline_width"] == 7
    assert style["font_color"] == "#ABCDEF"


def test_timing_offset_clamps_to_video_duration():
    assert apply_timing_offset(0.1, 0.5, -1.0, 3.0)[0] == 0.0
    start, end = apply_timing_offset(2.8, 3.2, 1.0, 3.0)
    assert start < 3.0
    assert end == 3.0
