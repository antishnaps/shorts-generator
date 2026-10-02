import requests

from core.wikimedia_commons_client import (
    WikimediaCommonsClient,
    _reset_wikimedia_rate_limit_state,
)


class FakeResponse:
    def __init__(self, payload=None, chunks=(), headers=None, error=None, status_code=200):
        self._payload = payload or {}
        self._chunks = list(chunks)
        self.headers = headers or {}
        self._error = error
        self.status_code = status_code

    def raise_for_status(self):
        if self._error:
            raise requests.RequestException(self._error)

    def json(self):
        return self._payload

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self._chunks


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _page(page_id, license_name, width=1920, height=1080, mime="video/webm"):
    return {
        "pageid": page_id,
        "index": page_id,
        "title": f"File:Video {page_id}.webm",
        "imageinfo": [{
            "size": 4096,
            "width": width,
            "height": height,
            "duration": 9.5,
            "url": f"https://upload.wikimedia.org/video-{page_id}.webm",
            "descriptionurl": f"https://commons.wikimedia.org/wiki/File:Video_{page_id}.webm",
            "mime": mime,
            "mediatype": "VIDEO",
            "extmetadata": {
                "LicenseShortName": {"value": license_name},
                "UsageTerms": {"value": license_name},
            },
        }],
    }


def test_search_keeps_only_explicit_safe_hd_video_and_uses_cache(tmp_path):
    payload = {"query": {"pages": [
        _page(1, "CC BY-SA 4.0"),
        _page(2, "Public domain", height=480),
        _page(3, "CC0", width=720, height=1280),
    ]}}
    session = FakeSession([FakeResponse(payload=payload)])
    client = WikimediaCommonsClient(session=session, cache_dir=tmp_path)

    first = client.search_videos("cats", per_page=5)
    second = client.search_videos("cats", per_page=5)

    assert [item["id"] for item in first] == [3]
    assert second == first
    assert len(session.calls) == 1
    params = session.calls[0][1]["params"]
    assert "filetype:video" in params["gsrsearch"]
    assert "LicenseShortName" in params["iiextmetadatafilter"]
    assert session.headers["User-Agent"].startswith("ContentBotPro/")


def test_download_is_atomic_and_rechecks_license(tmp_path):
    session = FakeSession([
        FakeResponse(chunks=[b"video", b"-bytes"], headers={"Content-Length": "11"})
    ])
    client = WikimediaCommonsClient(session=session, cache_dir=tmp_path / "cache")
    video = {
        "id": 42,
        "url": "https://upload.wikimedia.org/example.webm",
        "size": 11,
        "license": "Public domain",
        "license_url": "",
    }

    result = client.download_video(video, tmp_path / "clips")

    assert result
    assert (tmp_path / "clips" / "wikimedia_video_42.webm").read_bytes() == b"video-bytes"
    assert not list((tmp_path / "clips").glob("*.part"))

    tampered = dict(video, id=43, license="CC BY-SA 4.0")
    assert client.download_video(tampered, tmp_path / "clips") is None


def test_download_rejects_declared_oversize_without_network(tmp_path):
    session = FakeSession([])
    client = WikimediaCommonsClient(
        session=session,
        cache_dir=tmp_path / "cache",
        max_download_bytes=1024 * 1024,
    )
    video = {
        "id": 99,
        "url": "https://upload.wikimedia.org/huge.webm",
        "size": 2 * 1024 * 1024,
        "license": "CC0",
        "license_url": "",
    }
    assert client.download_video(video, tmp_path / "clips") is None
    assert session.calls == []


def test_first_429_stops_followup_downloads_process_wide(tmp_path):
    _reset_wikimedia_rate_limit_state()
    logs = []
    session = FakeSession([
        FakeResponse(status_code=429, headers={"Retry-After": "120"}),
    ])
    client = WikimediaCommonsClient(
        session=session,
        cache_dir=tmp_path / "cache",
        log_callback=logs.append,
    )
    video = {
        "id": 1,
        "url": "https://upload.wikimedia.org/limited.webm",
        "size": 11,
        "license": "Public domain",
        "license_url": "",
    }

    try:
        assert client.download_video(video, tmp_path / "clips") is None
        assert client.download_video(dict(video, id=2), tmp_path / "clips") is None
        assert len(session.calls) == 1
        assert any("лимит запросов 429" in message for message in logs)
        assert any("загрузки на паузе" in message for message in logs)
    finally:
        _reset_wikimedia_rate_limit_state()
