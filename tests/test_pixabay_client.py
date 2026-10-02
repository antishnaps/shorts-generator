from core.pixabay_client import PixabayClient


def test_pixabay_prefers_full_hd_profile_over_large_4k_file():
    video = {
        "videos": {
            "large": {"url": "https://example.invalid/4k.mp4", "width": 2160, "height": 3840},
            "medium": {"url": "https://example.invalid/2k.mp4", "width": 1440, "height": 2560},
            "small": {"url": "https://example.invalid/fhd.mp4", "width": 1080, "height": 1920},
            "tiny": {"url": "https://example.invalid/hd.mp4", "width": 720, "height": 1280},
        }
    }

    selected = PixabayClient.choose_video_file(video)

    assert selected["width"] == 1080
    assert selected["height"] == 1920


def test_pixabay_rejects_sub_hd_profiles():
    selected = PixabayClient.choose_video_file({
        "videos": {"small": {"url": "https://example.invalid/low.mp4", "height": 480}}
    })

    assert selected is None
