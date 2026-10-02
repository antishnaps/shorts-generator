"""
Compatibility wrapper for YouTube cookies validation and browser export.

The active YouTube pipeline lives in ``core.youtube.cookies``. This class keeps
the older startup-validator API while delegating validation and browser export
to the shared implementation.
"""

from importlib.util import find_spec
from pathlib import Path
from typing import Dict, Tuple

from core.system_profiler import SystemProfiler
from core.youtube import (
    default_youtube_cookies_path,
    ensure_youtube_cookies,
    has_current_youtube_auth_cookies,
    is_valid_netscape_cookie_file,
    normalize_cookies_file,
)


class CookiesManager:
    """Manages YouTube cookies validation and auto-export."""

    def __init__(self, cookies_path: str = None, verbose: bool = True):
        self.cookies_path = (
            Path(cookies_path)
            if cookies_path is not None
            else default_youtube_cookies_path()
        )
        self.verbose = verbose
        self.profiler = SystemProfiler()

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    def validate_cookies(self) -> Tuple[bool, str]:
        """Validate the current cookies file using the shared YouTube rules."""
        if not self.cookies_path.exists():
            return False, "Cookies file not found"

        try:
            preview = self.cookies_path.read_text(
                encoding="utf-8", errors="ignore"
            ).lstrip()
        except OSError as exc:
            return False, f"Error reading cookies: {exc}"

        if preview.startswith("{") or preview.startswith("["):
            return False, "Cookies file is in JSON format (must be Netscape format)"

        normalize_cookies_file(str(self.cookies_path), self._log)

        if not is_valid_netscape_cookie_file(str(self.cookies_path)):
            return False, "Cookies file is not a valid Netscape cookies file"

        if not has_current_youtube_auth_cookies(str(self.cookies_path)):
            return False, "Cookies file does not contain a current YouTube login session"

        return True, "Cookies valid"

    def auto_export_cookies(self) -> bool:
        """Try to create or refresh cookies from the local browser profile."""
        return ensure_youtube_cookies(
            str(self.cookies_path),
            self._log,
            auto_export=True,
        ) is not None

    def get_cookies_status(self) -> Dict:
        """Get detailed cookies status and persist it to the system profile."""
        is_valid, error = self.validate_cookies()
        status = {
            "exists": self.cookies_path.exists(),
            "valid": is_valid,
            "size": self.cookies_path.stat().st_size if self.cookies_path.exists() else 0,
            "error": None if is_valid else error,
            "can_auto_export": find_spec("browser_cookie3") is not None,
        }

        self.profiler.update_profile(
            {
                "youtube": {
                    "cookies_valid": status["valid"],
                    "cookies_path": str(self.cookies_path),
                }
            }
        )
        return status

    def show_manual_export_instructions(self) -> None:
        """Print fallback instructions for environments where browser export fails."""
        print("\n" + "=" * 60)
        print("YOUTUBE COOKIES FALLBACK")
        print("=" * 60)
        print("1. Log in to YouTube in your browser.")
        print("2. In the app, open Settings -> YouTube Cookies -> From browser.")
        print("3. If browser export fails, export cookies.txt manually with")
        print("   'Get cookies.txt LOCALLY' and import it in the same settings row.")
        print("=" * 60 + "\n")

    def check_and_fix(self) -> bool:
        """Check cookies and try browser export when the file is missing/invalid."""
        status = self.get_cookies_status()

        if status["valid"]:
            self._log("✅ YouTube cookies are valid")
            return True

        if status["exists"]:
            self._log(f"⚠️ Cookies file exists but invalid: {status['error']}")
        else:
            self._log("⚠️ Cookies file not found")

        if self.auto_export_cookies():
            is_valid, _ = self.validate_cookies()
            if is_valid:
                self._log("✅ Cookies exported and validated successfully")
                return True

        self._log("❌ Could not auto-export cookies")
        if self.verbose:
            self.show_manual_export_instructions()
        return False
