"""Windows process defaults for a GUI application without console flashes."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Optional


def hidden_subprocess_kwargs(
    kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return Popen kwargs that keep console programs hidden on Windows."""
    result = dict(kwargs or {})
    if os.name != "nt":
        return result

    flags = int(result.get("creationflags") or 0)
    visible_flags = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0)) | int(
        getattr(subprocess, "DETACHED_PROCESS", 0)
    )
    if flags & visible_flags:
        return result

    result["creationflags"] = flags | int(
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    )
    if result.get("startupinfo") is None:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        result["startupinfo"] = startupinfo
    return result


def configure_hidden_subprocesses() -> bool:
    """Apply hidden-window defaults to all subprocess calls in this process."""
    if os.name != "nt" or getattr(subprocess, "_contentbot_hidden_processes", False):
        return False

    original_init = subprocess.Popen.__init__

    def hidden_init(self, *args, **kwargs):
        return original_init(self, *args, **hidden_subprocess_kwargs(kwargs))

    subprocess.Popen.__init__ = hidden_init
    subprocess._contentbot_hidden_processes = True
    subprocess._contentbot_original_popen_init = original_init
    return True
