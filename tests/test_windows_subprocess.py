import os
import subprocess

import pytest

from core.windows_subprocess import hidden_subprocess_kwargs


@pytest.mark.skipif(os.name != "nt", reason="Windows-only process flags")
def test_hidden_subprocess_defaults_suppress_console_windows():
    result = hidden_subprocess_kwargs({})

    assert result["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert result["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert result["startupinfo"].wShowWindow == subprocess.SW_HIDE


@pytest.mark.skipif(os.name != "nt", reason="Windows-only process flags")
def test_explicit_new_console_is_preserved():
    result = hidden_subprocess_kwargs({"creationflags": subprocess.CREATE_NEW_CONSOLE})

    assert result["creationflags"] == subprocess.CREATE_NEW_CONSOLE
    assert "startupinfo" not in result
