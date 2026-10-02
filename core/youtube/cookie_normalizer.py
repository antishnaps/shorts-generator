"""Normalize browser-exported cookies for yt-dlp."""

import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .utils import _dummy_log
from .constants import AUTH_COOKIES


_normalization_lock = threading.Lock()


def looks_like_youtube_auth_cookie_names(cookie_names) -> bool:
    """Return True when cookie names look like a usable YouTube login session."""
    auth_names = {name for name in cookie_names if name in AUTH_COOKIES}
    has_primary_session = bool(
        auth_names.intersection({'SID', 'SAPISID', 'LOGIN_INFO'})
    )
    has_secure_psid = bool(
        auth_names.intersection({'__Secure-1PSID', '__Secure-3PSID'})
    )
    has_secure_papisid = bool(
        auth_names.intersection({'__Secure-1PAPISID', '__Secure-3PAPISID'})
    )

    # Modern Chrome/Yandex exports can omit legacy SID/SAPISID rows while still
    # carrying the secure Google session pair that yt-dlp can use.
    return (has_primary_session and has_secure_psid) or (
        has_secure_psid and has_secure_papisid
    )


def is_valid_netscape_cookie_file(cookies_path: str) -> bool:
    """Return True only for a complete Netscape cookie file with usable rows."""
    cookies_file = Path(cookies_path)
    try:
        content = cookies_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    lines = content.splitlines()
    has_header = any("Netscape HTTP Cookie File" in line for line in lines[:5])
    cookie_rows = [
        line for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return has_header and any(len(row.split("\t")) >= 7 for row in cookie_rows)


def has_current_youtube_auth_cookies(cookies_path: str) -> bool:
    """Return True when the file contains a non-expired YouTube login cookie."""
    cookies_file = Path(cookies_path)
    try:
        lines = cookies_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False

    now = int(time.time())
    auth_names = set()
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        domain, _flag, _path, _secure, expires, name, value = fields[:7]
        if "youtube.com" not in domain and "google.com" not in domain:
            continue
        if name not in AUTH_COOKIES or not value:
            continue
        try:
            expiry = int(expires or 0)
        except ValueError:
            expiry = 0
        if expiry == 0 or expiry > now:
            auth_names.add(name)

    return looks_like_youtube_auth_cookie_names(auth_names)


def normalize_cookies_file(
    cookies_path: str, log_callback: Optional[Callable] = None
) -> bool:
    """Normalize a cookies file to UTF-8 Netscape format."""
    log = log_callback or _dummy_log
    cookies_file = Path(cookies_path)
    if not cookies_file.exists():
        return False

    with _normalization_lock:
        try:
            original_content = cookies_file.read_bytes()
            raw_content = original_content
            has_bom = raw_content.startswith(b"\xef\xbb\xbf")
            if has_bom:
                raw_content = raw_content[3:]

            content = None
            used_encoding = "utf-8"
            for encoding in ("utf-8", "cp1251", "latin-1", "cp1252"):
                try:
                    content = raw_content.decode(encoding)
                    used_encoding = encoding
                    break
                except UnicodeDecodeError:
                    continue
            if content is None:
                return False

            had_windows_endings = "\r" in content
            content = content.replace("\r\n", "\n").replace("\r", "\n")
            lines = content.split("\n")
            has_header = any(
                "Netscape HTTP Cookie File" in line for line in lines[:5]
            )

            normalized_lines = []
            if not has_header:
                normalized_lines.extend(
                    [
                        "# Netscape HTTP Cookie File",
                        "# https://curl.haxx.se/rfc/cookie_spec.html",
                        "# This is a generated file! Do not edit.",
                        "",
                    ]
                )

            converted_spaces = False
            for line in lines:
                if line.strip() and not line.strip().startswith("#") and "\t" not in line:
                    parts = [
                        part.strip()
                        for part in re.split(r"\s{2,}", line)
                        if part.strip()
                    ]
                    if len(parts) >= 7:
                        line = "\t".join(parts)
                        converted_spaces = True
                normalized_lines.append(line)

            needs_normalization = (
                has_bom
                or used_encoding != "utf-8"
                or had_windows_endings
                or not has_header
                or converted_spaces
            )
            if not needs_normalization:
                return True

            backup_path = cookies_file.with_suffix(".txt.backup")
            backup_path.write_bytes(original_content)
            temp_path = cookies_file.with_name(
                f".{cookies_file.name}.{threading.get_ident()}.tmp"
            )
            try:
                temp_path.write_text(
                    "\n".join(normalized_lines), encoding="utf-8", newline="\n"
                )
                temp_path.replace(cookies_file)
            finally:
                temp_path.unlink(missing_ok=True)
            log("Cookies file normalized")
            return True
        except Exception as exc:
            log(f"Cookies normalization failed: {str(exc)[:100]}")
            return False


def create_isolated_cookie_snapshot(
    cookies_path: str,
    output_dir: str,
    prefix: str = "youtube",
) -> Optional[Path]:
    """Create a private cookie copy for one yt-dlp instance."""
    source = Path(cookies_path)
    if not source.exists():
        return None

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    fd, snapshot_name = tempfile.mkstemp(
        prefix=f".{prefix}_",
        suffix=".cookies.txt",
        dir=str(output),
    )
    os.close(fd)
    snapshot = Path(snapshot_name)
    try:
        shutil.copy2(source, snapshot)
        return snapshot
    except Exception:
        snapshot.unlink(missing_ok=True)
        return None


__all__ = [
    "normalize_cookies_file",
    "create_isolated_cookie_snapshot",
    "is_valid_netscape_cookie_file",
    "has_current_youtube_auth_cookies",
    "looks_like_youtube_auth_cookie_names",
]
