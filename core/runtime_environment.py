"""Runtime detection shared by PyInstaller and Nuitka customer builds."""

from __future__ import annotations

import sys


def is_packaged_runtime() -> bool:
    """Return whether this module runs from a supported packaged build."""
    return bool(
        getattr(sys, "frozen", False)
        or globals().get("__compiled__") is not None
    )
