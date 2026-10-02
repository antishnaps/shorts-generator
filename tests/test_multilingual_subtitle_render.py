import shutil
import subprocess
from pathlib import Path

import pytest

from core.rendering.subtitle_renderer import SubtitleRenderer
from core.audio_processor import FFMPEG_PATH


@pytest.mark.parametrize("animation", ["word_focus", "fade", "typewriter"])
def test_ffmpeg_burns_all_supported_language_subtitles(tmp_path, animation):
    ffmpeg = FFMPEG_PATH if Path(FFMPEG_PATH).is_file() else shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")

    samples = [
        "Русские субтитры",
        "English subtitles",
        "Subtítulos españoles",
        "Sous-titres français",
        "Deutsche Untertitel",
        "中文字幕变化",
        "日本語字幕変化",
        "한국어자막변화",
        "Legendas portuguesas",
        "Sottotitoli italiani",
        "हिन्दी उपशीर्षक",
        "ترجمة عربية",
    ]
    renderer = SubtitleRenderer()
    segments = [
        (index * 0.5, (index + 1) * 0.5, text)
        for index, text in enumerate(samples)
    ]
    ass_path = tmp_path / f"multilingual_{animation}.ass"
    ass_path.write_text(
        renderer.build_ass(
            segments,
            {
                "animated_subtitle": True,
                "subtitle_animation": animation,
                "font_path": "Arial",
                "font_size": 36,
                "max_chars_per_subtitle": 24,
            },
            duration=6.0,
            width=360,
            height=640,
        ),
        encoding="utf-8",
    )
    filter_path = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
    result = subprocess.run(
        [
            ffmpeg, "-v", "warning", "-f", "lavfi",
            "-i", "color=c=black:s=360x640:r=12:d=6",
            "-vf", f"subtitles=filename='{filter_path}'",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=30,
    )

    assert result.returncode == 0, result.stderr[-1000:]
    assert "Glyph 0x" not in result.stderr, result.stderr[-1000:]
