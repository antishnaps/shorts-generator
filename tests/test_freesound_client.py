from core.freesound_client import FreesoundClient


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "results": [
                {
                    "id": 42,
                    "name": "Safe whoosh",
                    "license": "Creative Commons 0",
                    "previews": {"preview-hq-mp3": "https://example.invalid/safe.mp3"},
                }
            ]
        }


class _Session:
    def __init__(self):
        self.params = None

    def get(self, _url, params=None, timeout=None):
        self.params = params
        return _Response()


def test_freesound_search_is_limited_to_cc0_and_requests_license_metadata():
    FreesoundClient._search_cache.clear()
    client = FreesoundClient("test-key", log_callback=lambda _message: None)
    session = _Session()
    client.session = session

    results = client.search_sounds("whoosh", filter_params="duration:[0 TO 10]")

    assert results[0]["license"] == "Creative Commons 0"
    assert 'license:"Creative Commons 0"' in session.params["filter"]
    assert "license" in session.params["fields"].split(",")


def test_freesound_rejects_non_cc0_downloads(tmp_path):
    client = FreesoundClient("test-key", log_callback=lambda _message: None)

    assert client.download_sound(
        {"id": 7, "license": "Attribution NonCommercial"}, tmp_path,
    ) is None


def test_freesound_accepts_canonical_cc0_license_url():
    assert FreesoundClient.is_cc0_sound({
        "license": "http://creativecommons.org/publicdomain/zero/1.0/",
    })
