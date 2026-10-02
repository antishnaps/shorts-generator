from pathlib import Path

from PIL import Image

from tools.generate_brand_assets import ICO_PATH, PNG_PATH, render_mark


def test_brand_mark_is_scalable_and_has_transparent_corners():
    mark = render_mark(32)

    assert mark.mode == "RGBA"
    assert mark.size == (32, 32)
    assert mark.getpixel((0, 0))[3] == 0
    assert mark.getbbox() is not None


def test_checked_in_brand_assets_are_valid():
    assert PNG_PATH.is_file()
    assert ICO_PATH.is_file()

    with Image.open(PNG_PATH) as mark:
        assert mark.size == (1024, 1024)
        assert mark.mode == "RGBA"

    with Image.open(ICO_PATH) as icon:
        assert icon.format == "ICO"
        assert icon.size[0] >= 128
        assert icon.size[1] >= 128

    assert Path(PNG_PATH).stat().st_size < 500_000
