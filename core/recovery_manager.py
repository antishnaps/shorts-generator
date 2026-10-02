"""Discovery of interrupted generation batches for startup recovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def find_recoverable_batch(output_path: str | Path) -> Optional[Dict[str, Any]]:
    manifest_path = Path(output_path or "generated") / "batch_manifest.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not data.get("batch_hash"):
        return None

    total = max(0, int(data.get("total") or len(data.get("themes") or {})))
    completed = len(data.get("completed") or {})
    failed = len(data.get("failed") or {})
    if total <= 0 or completed >= total:
        return None
    return {
        "path": str(manifest_path),
        "batch_hash": str(data.get("batch_hash")),
        "main_theme": str(data.get("main_theme") or ""),
        "total": total,
        "completed": completed,
        "failed": failed,
        "remaining": max(0, total - completed),
    }
