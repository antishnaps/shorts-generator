"""Offline checks for the exported edition, not the commercial installation."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_QT_APP = None

ROOT = Path(__file__).resolve().parents[1]


def test_no_activation_in_runtime():
    assert not (ROOT / "core/license_manager.py").exists()
    assert not (ROOT / "core/release_integrity.py").exists()
    for folder in ("core", "gui"):
        for path in (ROOT / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                assert not (isinstance(node, ast.Name) and node.id in {"verify_license_quick", "check_license", "LicenseManager"})


def test_clean_defaults_and_no_paid_voice_by_default():
    from core.config_manager import DEFAULT_CONFIG
    template = json.loads((ROOT / "config.json.template").read_text(encoding="utf-8"))
    assert template == DEFAULT_CONFIG
    settings = DEFAULT_CONFIG["user_settings"]
    for key, value in settings.items():
        if "api_key" in key:
            assert not value
    assert settings["audio_settings"]["provider"] == "edge"
    assert not settings["overlay_settings"]["enabled"]
    assert not settings["channel_analytics_settings"]["auto_sync"]


def test_startup_smoke_without_activation_or_network():
    result = subprocess.run([sys.executable, str(ROOT / "main.py"), "--smoke-test"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SMOKE_OK" in result.stdout


def test_manifest_files_have_not_changed():
    import hashlib
    manifest = json.loads((ROOT / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]


def test_first_launch_gui_without_keys_license_or_network(monkeypatch):
    import socket
    import time
    from PyQt5.QtWidgets import QApplication
    from gui.main_window_v2 import MainWindowV2

    def blocked(*args, **kwargs):
        raise AssertionError("First launch must not connect to external services")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.chdir(ROOT)
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    window = MainWindowV2()
    window.show()
    deadline = time.monotonic() + 15
    while window.generator is None and time.monotonic() < deadline:
        _QT_APP.processEvents()
        time.sleep(0.05)
    try:
        assert window.isVisible()
        assert window.generator is not None
        assert "Free" in window.windowTitle()
        assert not hasattr(window, "_license_timer")
    finally:
        window.close()
        _QT_APP.processEvents()
