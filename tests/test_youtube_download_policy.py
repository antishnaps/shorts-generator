import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.youtube_mixer as mixer_module
import core.youtube.download_policy as policy_module
from core.youtube.download_policy import (
    YouTubeDownloadFailure,
    YouTubeFailureRegistry,
    build_youtube_client_strategies,
    classify_youtube_download_error,
    compact_youtube_error,
    detect_youtube_js_runtimes,
    build_youtube_js_runtime_options,
    is_supported_ytdlp_version,
)
from core.youtube_mixer import YouTubeMixer, reset_youtube_access_state


@pytest.fixture(autouse=True)
def reset_shared_youtube_state():
    reset_youtube_access_state()
    yield
    reset_youtube_access_state()


def test_download_failures_are_classified_for_bounded_retry_and_circuit_breaking():
    missing_format = classify_youtube_download_error("Requested format is not available")
    throttled = classify_youtube_download_error(
        "This content isn't available, try again later"
    )

    assert missing_format.code == "format_unavailable"
    assert missing_format.retry_with_another_client
    assert missing_format.global_cooldown_seconds == 0
    assert throttled.code == "rate_limited"
    assert throttled.global_cooldown_seconds > 0
    assert not throttled.retry_with_another_client


def test_error_compaction_redacts_proxy_credentials_and_tokens():
    message = compact_youtube_error(
        "\x1b[31mGET https://user:password@proxy.local/file?access_token=secret&x=1\x1b[0m"
    )

    assert "password" not in message
    assert "secret" not in message
    assert "user:" not in message
    assert "<redacted>" in message


def test_failure_registry_is_scoped_to_connection_and_ttl():
    registry = YouTubeFailureRegistry(max_entries=16)
    failure = YouTubeDownloadFailure(
        "format_unavailable", "missing", cache_ttl_seconds=10
    )
    registry.remember("id", failure, connection_signature=("cookies-a", "proxy-a"), now=5)

    assert registry.lookup(
        "id", connection_signature=("cookies-a", "proxy-a"), now=14
    ) is failure
    assert registry.lookup(
        "id", connection_signature=("cookies-b", "proxy-a"), now=14
    ) is None

    registry.remember("id", failure, connection_signature="same", now=5)
    assert registry.lookup("id", connection_signature="same", now=15) is None


def test_client_ladder_uses_tokenless_default_before_session_and_fallback_clients():
    strategies = build_youtube_client_strategies(has_cookies=True)

    assert len(strategies) == 4
    assert strategies[0].player_clients == ()
    assert not strategies[0].use_cookies
    assert strategies[1].player_clients == ()
    assert strategies[1].use_cookies
    assert strategies[1].required_failure_codes == ("login_required",)
    assert strategies[2].player_clients == ("tv", "android_vr")
    assert not strategies[2].use_cookies
    assert strategies[3].player_clients == ()
    assert not strategies[3].use_cookies
    assert strategies[3].required_failure_codes == ("network",)
    assert strategies[3].prefer_hls


def test_dependency_baseline_requires_current_ytdlp_with_default_ejs_extra():
    requirement = Path("requirements.txt").read_text(encoding="utf-8")

    assert "yt-dlp[default]>=2026.8.19" in requirement
    assert is_supported_ytdlp_version("2026.08.19")
    assert is_supported_ytdlp_version("2026.12.31")
    assert not is_supported_ytdlp_version("2026.06.09")


def test_runtime_detection_prefers_valid_deno_and_never_advertises_node_20(
    monkeypatch
):
    paths = {"deno": "C:/runtime/deno.exe", "node": "C:/runtime/node.exe"}
    monkeypatch.setattr(policy_module.shutil, "which", lambda name: paths.get(name))

    def fake_run(command, **_kwargs):
        output = "deno 2.6.2\n" if "deno" in command[0] else "v20.18.2\n"
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(policy_module.subprocess, "run", fake_run)
    detect_youtube_js_runtimes.cache_clear()
    try:
        assert detect_youtube_js_runtimes() == {
            "deno": {"path": "C:/runtime/deno.exe"}
        }
    finally:
        detect_youtube_js_runtimes.cache_clear()


def test_runtime_detection_accepts_node_22_only_when_deno_is_missing(monkeypatch):
    monkeypatch.setattr(
        policy_module.shutil,
        "which",
        lambda name: "C:/runtime/node.exe" if name == "node" else None,
    )
    monkeypatch.setattr(
        policy_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v22.11.0\n"),
    )
    detect_youtube_js_runtimes.cache_clear()
    try:
        assert detect_youtube_js_runtimes() == {
            "node": {"path": "C:/runtime/node.exe"}
        }
    finally:
        detect_youtube_js_runtimes.cache_clear()


def test_runtime_detection_finds_packaged_deno_in_meipass(monkeypatch, tmp_path):
    packaged_deno = tmp_path / "runtime" / "deno.exe"
    packaged_deno.parent.mkdir(parents=True)
    packaged_deno.touch()
    monkeypatch.setattr(policy_module.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(policy_module.shutil, "which", lambda _name: None)
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="deno 2.3.0\n")

    monkeypatch.setattr(policy_module.subprocess, "run", fake_run)
    detect_youtube_js_runtimes.cache_clear()
    try:
        assert detect_youtube_js_runtimes() == {
            "deno": {"path": str(packaged_deno)}
        }
        assert commands == [[str(packaged_deno), "--version"]]
    finally:
        detect_youtube_js_runtimes.cache_clear()


def test_runtime_detection_rejects_deno_older_than_ejs_minimum(
    monkeypatch, tmp_path
):
    packaged_deno = tmp_path / "runtime" / "deno.exe"
    packaged_deno.parent.mkdir(parents=True)
    packaged_deno.touch()
    monkeypatch.setattr(policy_module.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(policy_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        policy_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="deno 2.2.9\n"
        ),
    )
    detect_youtube_js_runtimes.cache_clear()
    try:
        assert detect_youtube_js_runtimes() == {}
    finally:
        detect_youtube_js_runtimes.cache_clear()


def test_js_options_prefer_packaged_ejs_over_remote_component(monkeypatch):
    monkeypatch.setattr(
        policy_module,
        "detect_youtube_js_runtimes",
        lambda: {"deno": {"path": "runtime/deno.exe"}},
    )
    monkeypatch.setattr(
        policy_module.importlib.util,
        "find_spec",
        lambda name: object() if name == "yt_dlp_ejs" else None,
    )

    assert build_youtube_js_runtime_options() == {
        "js_runtimes": {"deno": {"path": "runtime/deno.exe"}}
    }


def test_js_options_allow_official_remote_ejs_only_when_local_package_missing(
    monkeypatch,
):
    monkeypatch.setattr(
        policy_module,
        "detect_youtube_js_runtimes",
        lambda: {"deno": {"path": "deno"}},
    )
    monkeypatch.setattr(policy_module.importlib.util, "find_spec", lambda _name: None)

    assert build_youtube_js_runtime_options()["remote_components"] == ["ejs:github"]


def _install_fake_ytdlp(monkeypatch, factory):
    class DownloadError(Exception):
        pass

    fake_module = SimpleNamespace(
        YoutubeDL=factory(DownloadError),
        utils=SimpleNamespace(DownloadError=DownloadError),
    )
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)
    return DownloadError


def _patch_offline_download_dependencies(monkeypatch, dimensions=(1920, 1080)):
    monkeypatch.setattr(mixer_module, "ensure_youtube_cookies", lambda *_a, **_k: None)
    monkeypatch.setattr(mixer_module, "_get_auto_proxy", lambda *_a, **_k: None)
    monkeypatch.setattr(
        mixer_module,
        "build_youtube_js_runtime_options",
        lambda: {
            "js_runtimes": {"deno": {}},
            "remote_components": ["ejs:github"],
        },
    )
    monkeypatch.setattr(mixer_module, "validate_video_file", lambda *_a, **_k: True)
    monkeypatch.setattr(mixer_module, "probe_video_dimensions", lambda _path: dimensions)


def test_download_negotiates_once_per_client_and_recovers_from_missing_format(
    tmp_path, monkeypatch
):
    options_seen = []
    calls = []

    def factory(DownloadError):
        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options
                options_seen.append(options)
                self.instance_index = len(options_seen)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, download=False):
                calls.append((self.instance_index, download))
                assert download is True
                if self.instance_index == 1:
                    raise DownloadError("Requested format is not available")
                output = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
                output.write_bytes(b"video" * 1_000)
                return {
                    "duration": 300,
                    "format_id": "137+251",
                    "requested_formats": [{"height": 1080}],
                }

            def download(self, _urls):
                pytest.fail("A second ydl.download extraction must not occur")

        return FakeYoutubeDL

    _install_fake_ytdlp(monkeypatch, factory)
    _patch_offline_download_dependencies(monkeypatch)
    mixer = YouTubeMixer(cache_dir=str(tmp_path / "cache"), log_callback=lambda _msg: None)

    result = mixer.download_video(
        "https://www.youtube.com/watch?v=negotiation",
        tmp_path,
        video_id="negotiation",
        min_resolution=720,
        allow_fallbacks=False,
    )

    assert result == str(tmp_path / "negotiation.mp4")
    assert calls == [(1, True), (2, True)]
    assert "extractor_args" not in options_seen[0]
    assert options_seen[0]["js_runtimes"] == {"deno": {}}
    assert options_seen[1]["extractor_args"]["youtube"]["player_client"] == [
        "tv",
        "android_vr",
    ]


def test_public_download_stays_tokenless_and_cookies_are_used_only_after_gate(
    tmp_path, monkeypatch
):
    options_seen = []

    def factory(DownloadError):
        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options
                options_seen.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, download=False):
                assert download is True
                if len(options_seen) == 1:
                    raise DownloadError("Sign in to confirm your age")
                output = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
                output.write_bytes(b"video" * 1_000)
                return {
                    "duration": 120,
                    "format_id": "398+140",
                    "requested_formats": [{"height": 720}],
                }

        return FakeYoutubeDL

    _install_fake_ytdlp(monkeypatch, factory)
    _patch_offline_download_dependencies(monkeypatch)
    monkeypatch.setattr(
        mixer_module,
        "ensure_youtube_cookies",
        lambda *_args, **_kwargs: tmp_path / "source-cookies.txt",
    )
    monkeypatch.setattr(
        mixer_module,
        "create_isolated_cookie_snapshot",
        lambda *_args, **_kwargs: tmp_path / "isolated-cookies.txt",
    )
    mixer = YouTubeMixer(cache_dir=str(tmp_path / "cache"), log_callback=lambda _msg: None)

    result = mixer.download_video(
        "https://www.youtube.com/watch?v=age-gated",
        tmp_path,
        video_id="age-gated",
        min_resolution=720,
        allow_fallbacks=False,
    )

    assert result == str(tmp_path / "age-gated.mp4")
    assert len(options_seen) == 2
    assert "cookiefile" not in options_seen[0]
    assert options_seen[1]["cookiefile"] == str(tmp_path / "isolated-cookies.txt")
    assert "extractor_args" not in options_seen[1]


def test_network_failure_gets_one_fresh_default_negotiation_after_client_fallback(
    tmp_path, monkeypatch
):
    options_seen = []

    def factory(DownloadError):
        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options
                options_seen.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, download=False):
                assert download is True
                attempt = len(options_seen)
                if attempt == 1:
                    raise DownloadError("HTTPS read timed out")
                if attempt == 2:
                    raise DownloadError("Requested format is not available")
                output = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
                output.write_bytes(b"video" * 1_000)
                return {
                    "duration": 120,
                    "format_id": "298",
                    "requested_formats": [{"height": 720}],
                }

        return FakeYoutubeDL

    _install_fake_ytdlp(monkeypatch, factory)
    _patch_offline_download_dependencies(monkeypatch)
    mixer = YouTubeMixer(cache_dir=str(tmp_path / "cache"), log_callback=lambda _msg: None)

    result = mixer.download_video(
        "https://www.youtube.com/watch?v=network-refresh",
        tmp_path,
        video_id="network-refresh",
        min_resolution=720,
        max_download_seconds=60,
        allow_fallbacks=False,
    )

    assert result == str(tmp_path / "network-refresh.mp4")
    assert len(options_seen) == 3
    assert "extractor_args" not in options_seen[0]
    assert options_seen[1]["extractor_args"]["youtube"]["player_client"] == [
        "tv",
        "android_vr",
    ]
    assert "extractor_args" not in options_seen[2]
    assert "[protocol^=m3u8]" in options_seen[2]["format"]


def test_final_format_failure_is_not_retried_for_same_connection(tmp_path, monkeypatch):
    instances = []

    def factory(DownloadError):
        class FakeYoutubeDL:
            def __init__(self, options):
                instances.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, download=False):
                raise DownloadError("Requested format is not available")

        return FakeYoutubeDL

    _install_fake_ytdlp(monkeypatch, factory)
    _patch_offline_download_dependencies(monkeypatch)
    mixer = YouTubeMixer(cache_dir=str(tmp_path / "cache"), log_callback=lambda _msg: None)
    kwargs = dict(
        video_url="https://www.youtube.com/watch?v=known-bad",
        output_dir=tmp_path,
        video_id="known-bad",
        min_resolution=720,
        allow_fallbacks=False,
    )

    assert mixer.download_video(**kwargs) is None
    assert len(instances) == 2
    assert mixer.download_video(**kwargs) is None
    assert len(instances) == 2


def test_documented_rate_limit_opens_shared_circuit_breaker(tmp_path, monkeypatch):
    instances = []

    def factory(DownloadError):
        class FakeYoutubeDL:
            def __init__(self, options):
                instances.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, download=False):
                raise DownloadError("This content isn't available, try again later")

        return FakeYoutubeDL

    _install_fake_ytdlp(monkeypatch, factory)
    _patch_offline_download_dependencies(monkeypatch)
    first = YouTubeMixer(cache_dir=str(tmp_path / "one"), log_callback=lambda _msg: None)
    second = YouTubeMixer(cache_dir=str(tmp_path / "two"), log_callback=lambda _msg: None)

    assert first.download_video(
        "https://www.youtube.com/watch?v=limited-a",
        tmp_path,
        video_id="limited-a",
        allow_fallbacks=False,
    ) is None
    assert len(instances) == 1
    assert second.download_video(
        "https://www.youtube.com/watch?v=limited-b",
        tmp_path,
        video_id="limited-b",
        allow_fallbacks=False,
    ) is None
    assert len(instances) == 1
