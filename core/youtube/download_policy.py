"""Bounded YouTube download negotiation and failure diagnostics.

This module deliberately contains no topic-specific logic.  It translates
yt-dlp failures into stable categories, provides a small connection-scoped
failure cache, and describes the two lightweight YouTube client negotiations
used before the much slower legacy download methods are considered.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Dict, Hashable, Optional, Tuple


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_URL_CREDENTIAL_RE = re.compile(r"(?i)(\b(?:https?|socks5h?|ftp)://)[^\s/@]+:[^\s/@]+@")
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:access_token|api_key|apikey|auth|key|oauth_token|sig|signature|token)=)"
    r"[^&\s]+"
)
_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")
YOUTUBE_MIN_YTDLP_VERSION = (2026, 8, 19)


@dataclass(frozen=True)
class YouTubeDownloadFailure:
    """A safe, machine-readable yt-dlp failure description."""

    code: str
    summary: str
    retry_with_another_client: bool = False
    global_cooldown_seconds: int = 0
    cache_ttl_seconds: int = 120


@dataclass(frozen=True)
class YouTubeClientStrategy:
    """A bounded yt-dlp client negotiation attempt."""

    name: str
    player_clients: Tuple[str, ...] = ()
    use_cookies: bool = True
    required_failure_codes: Tuple[str, ...] = ()
    prefer_hls: bool = False


def compact_youtube_error(error: object, limit: int = 220) -> str:
    """Return a one-line error safe for ordinary application logs."""

    text = _ANSI_ESCAPE_RE.sub("", str(error or ""))
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _SENSITIVE_QUERY_RE.sub(r"\1<redacted>", text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def classify_youtube_download_error(error: object) -> YouTubeDownloadFailure:
    """Classify current yt-dlp/YouTube failures without topic heuristics."""

    summary = compact_youtube_error(error)
    lowered = summary.casefold()

    if (
        ("sign in to confirm you" in lowered and "not a bot" in lowered)
        or "confirm you're not a bot" in lowered
        or "confirm you’re not a bot" in lowered
    ):
        return YouTubeDownloadFailure(
            "bot_challenge", summary, global_cooldown_seconds=120, cache_ttl_seconds=120
        )

    # yt-dlp documents this exact message as a YouTube request-rate limit.
    if (
        "this content isn't available, try again later" in lowered
        or "this content isn’t available, try again later" in lowered
        or "too many requests" in lowered
        or re.search(r"(?:http error|status(?: code)?)\s*429\b", lowered)
    ):
        return YouTubeDownloadFailure(
            "rate_limited", summary, global_cooldown_seconds=60, cache_ttl_seconds=90
        )

    if any(
        marker in lowered
        for marker in (
            "sign in to confirm your age",
            "confirm your age",
            "age-restricted",
            "age restricted",
            "login_required",
            "members-only",
            "members only",
        )
    ):
        return YouTubeDownloadFailure(
            "login_required", summary, cache_ttl_seconds=900
        )

    if "requested format is not available" in lowered or "no video formats found" in lowered:
        return YouTubeDownloadFailure(
            "format_unavailable",
            summary,
            retry_with_another_client=True,
            cache_ttl_seconds=900,
        )

    if any(
        marker in lowered
        for marker in (
            "private video",
            "video unavailable",
            "has been removed",
            "copyright claim",
            "not available in your country",
            "geo-restricted",
        )
    ):
        return YouTubeDownloadFailure("unavailable", summary, cache_ttl_seconds=3600)

    if any(
        marker in lowered
        for marker in (
            "timed out",
            "timeout",
            "connection reset",
            "failed to establish a new connection",
            "network is unreachable",
            "temporary failure in name resolution",
            "proxy error",
            "winerror 10013",
        )
    ):
        return YouTubeDownloadFailure(
            "network", summary, retry_with_another_client=True, cache_ttl_seconds=45
        )

    if any(marker in lowered for marker in ("permission denied", "no space left on device")):
        return YouTubeDownloadFailure("filesystem", summary, cache_ttl_seconds=60)

    return YouTubeDownloadFailure(
        "extractor_error", summary, retry_with_another_client=True, cache_ttl_seconds=120
    )


def build_youtube_client_strategies(has_cookies: bool) -> Tuple[YouTubeClientStrategy, ...]:
    """Return a small current-client negotiation ladder.

    yt-dlp's own default client selection is intentionally first.  Forcing the
    Android client while supplying browser cookies can expose only SABR formats
    and produce a false ``Requested format is not available`` error.  The
    second attempt stays on YouTube and on the same VPN/proxy, but uses current
    tokenless clients that yt-dlp can negotiate when the default format view is
    incomplete.
    """

    strategies = [
        YouTubeClientStrategy(
            name="стандартное согласование yt-dlp без сессии",
            player_clients=(),
            use_cookies=False,
        )
    ]
    if has_cookies:
        strategies.append(
            YouTubeClientStrategy(
                name="стандартное согласование с YouTube-сессией",
                player_clients=(),
                use_cookies=True,
                required_failure_codes=("login_required",),
            )
        )
    strategies.append(
        YouTubeClientStrategy(
            name="резервное согласование YouTube",
            player_clients=("tv", "android_vr"),
            use_cookies=False,
        )
    )
    strategies.append(
        YouTubeClientStrategy(
            name="сегментированная HLS-загрузка после сетевого сбоя",
            player_clients=(),
            use_cookies=False,
            required_failure_codes=("network",),
            prefer_hls=True,
        )
    )
    return tuple(strategies)


def _parse_version(value: str) -> Tuple[int, int, int]:
    match = _VERSION_RE.search(str(value or ""))
    if not match:
        return 0, 0, 0
    return tuple(int(part or 0) for part in match.groups())


def is_supported_ytdlp_version(value: str) -> bool:
    """Return whether yt-dlp meets the extractor baseline tested by this app."""

    return _parse_version(value) >= YOUTUBE_MIN_YTDLP_VERSION


@lru_cache(maxsize=1)
def detect_youtube_js_runtimes() -> Dict[str, Dict]:
    """Return only installed runtimes compatible with current yt-dlp EJS.

    Deno is preferred. Node is offered only from major version 22 onward;
    advertising an older Node makes yt-dlp attempt a runtime that cannot solve
    current YouTube challenges. Runtime discovery is cached process-wide.
    """

    deno_candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        deno_candidates.append(Path(meipass) / "runtime" / "deno.exe")
    # PyInstaller one-dir builds expose bundled data below _internal next to
    # the executable.  Do not require Deno to be installed globally for a
    # customer build that already ships the tested runtime.
    deno_candidates.append(
        Path(sys.executable).resolve().parent
        / "_internal"
        / "runtime"
        / "deno.exe"
    )
    deno = next((path for path in deno_candidates if path.is_file()), None)
    path_deno = shutil.which("deno")
    if deno is None and path_deno:
        # ``which`` already guarantees that this is an executable candidate;
        # keeping it separate also makes runtime discovery easy to fake in
        # focused offline diagnostics.
        deno = path_deno
    if deno:
        try:
            result = subprocess.run(
                [str(deno), "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and _parse_version(result.stdout) >= (2, 3, 0):
                return {"deno": {"path": str(deno)}}
        except (OSError, subprocess.SubprocessError):
            pass

    node = shutil.which("node")
    if node:
        try:
            result = subprocess.run(
                [node, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and _parse_version(result.stdout) >= (22, 0, 0):
                return {"node": {"path": node}}
        except (OSError, subprocess.SubprocessError):
            pass
    return {}


def build_youtube_js_runtime_options() -> Dict[str, object]:
    """Build local-first yt-dlp EJS options with a source-env fallback."""

    runtimes = detect_youtube_js_runtimes()
    if not runtimes:
        return {}
    options: Dict[str, object] = {"js_runtimes": runtimes}
    try:
        local_ejs_available = importlib.util.find_spec("yt_dlp_ejs") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        local_ejs_available = False
    if not local_ejs_available:
        # Developer/source environments may opt into yt-dlp's official remote
        # EJS component. Customer builds package matching yt_dlp_ejs and stay
        # deterministic/offline here.
        options["remote_components"] = ["ejs:github"]
    return options


class YouTubeFailureRegistry:
    """Remember final failures briefly so a batch does not hammer the same ID."""

    def __init__(self, max_entries: int = 512):
        self.max_entries = max(16, int(max_entries))
        self._items = {}
        self._lock = threading.Lock()

    def remember(
        self,
        video_id: str,
        failure: YouTubeDownloadFailure,
        connection_signature: Hashable = None,
        now: Optional[float] = None,
    ) -> None:
        key = str(video_id or "").strip()
        if not key:
            return
        current = time.monotonic() if now is None else float(now)
        expires = current + max(1, int(failure.cache_ttl_seconds))
        with self._lock:
            self._items[key] = (expires, connection_signature, failure)
            if len(self._items) > self.max_entries:
                oldest = sorted(self._items.items(), key=lambda item: item[1][0])
                for stale_key, _value in oldest[: len(self._items) - self.max_entries]:
                    self._items.pop(stale_key, None)

    def lookup(
        self,
        video_id: str,
        connection_signature: Hashable = None,
        now: Optional[float] = None,
    ) -> Optional[YouTubeDownloadFailure]:
        key = str(video_id or "").strip()
        if not key:
            return None
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires, saved_signature, failure = item
            if expires <= current or saved_signature != connection_signature:
                self._items.pop(key, None)
                return None
            return failure

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


__all__ = [
    "YouTubeClientStrategy",
    "YouTubeDownloadFailure",
    "YouTubeFailureRegistry",
    "YOUTUBE_MIN_YTDLP_VERSION",
    "build_youtube_client_strategies",
    "build_youtube_js_runtime_options",
    "classify_youtube_download_error",
    "compact_youtube_error",
    "detect_youtube_js_runtimes",
    "is_supported_ytdlp_version",
]
