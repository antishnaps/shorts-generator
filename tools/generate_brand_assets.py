#!/usr/bin/env python3
"""Generate the deterministic ContentBot Pro application mark and Windows icon.

The mark deliberately uses a small number of flat geometric shapes so it stays
legible in the 16 px taskbar slot as well as in the desktop application's header.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent.parent
BRANDING_DIR = ROOT / "assets" / "branding"
PNG_PATH = BRANDING_DIR / "contentbot_mark.png"
ICO_PATH = BRANDING_DIR / "contentbot.ico"

CANVAS = 1024
BACKGROUND = "#07121E"
BORDER = "#163044"
CYAN = "#35C9F0"
COBALT = "#4D6FF2"
FOREGROUND = "#F5FAFF"


def _round_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    width: int,
    fill: str,
) -> None:
    """Draw a line with reliable round caps across Pillow versions."""
    radius = width // 2
    draw.line((start, end), fill=fill, width=width)
    for x, y in (start, end):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def render_mark(size: int = CANVAS) -> Image.Image:
    """Render the flat C + production-flow mark at ``size`` square pixels."""
    if size < 16:
        raise ValueError("brand mark size must be at least 16 pixels")

    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (54, 54, 970, 970), radius=218, fill=BACKGROUND, outline=BORDER, width=22
    )

    # The open ring is a recognizable C at small sizes; the cobalt segment adds
    # movement without using a gradient or decorative lighting effects.
    ring_box = (192, 184, 828, 840)
    draw.arc(ring_box, start=42, end=318, fill=CYAN, width=148)
    draw.arc(ring_box, start=108, end=202, fill=COBALT, width=148)

    # Three increasingly long tracks express batch production. Their angled
    # ends create a forward cue without falling back to a generic play button.
    tracks = (
        ((486, 390), (650, 390), 44),
        ((450, 512), (716, 512), 56),
        ((486, 634), (782, 634), 44),
    )
    for index, (start, end, width) in enumerate(tracks):
        fill = FOREGROUND if index == 1 else CYAN
        _round_line(draw, start, end, width=width, fill=fill)
        tip = width // 2
        draw.polygon(
            ((end[0], end[1] - tip), (end[0] + tip, end[1]), (end[0], end[1] + tip)),
            fill=fill,
        )

    if size != CANVAS:
        image = image.resize((size, size), Image.Resampling.LANCZOS)
    return image


def write_assets() -> tuple[Path, Path]:
    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    mark = render_mark()
    mark.save(PNG_PATH, format="PNG", optimize=True)
    mark.save(
        ICO_PATH,
        format="ICO",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
               (64, 64), (128, 128), (256, 256)],
    )
    return PNG_PATH, ICO_PATH


def main() -> int:
    png_path, ico_path = write_assets()
    print(f"Generated {png_path.relative_to(ROOT)}")
    print(f"Generated {ico_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
