"""Small, dependency-free helpers for keeping credentials out of logs."""

from __future__ import annotations

import re
from typing import Any


REDACTED = "[REDACTED]"

_SENSITIVE_FIELD_PARTS = (
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "authorization",
    "cookie",
    "private_key",
    "card_number",
)

_TEXT_PATTERNS = (
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+\-/=]{8,}"),
    re.compile(
        r"(?i)(\b(?:x-goog-api-key|api[_-]?key|access[_-]?token|refresh[_-]?token|"
        r"token|password|passwd|secret|key)"
        r"\b\s*[=:]\s*[\"']?)[^\s,;\"'&}\]]{4,}"
    ),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
)


def is_sensitive_field(name: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    return any(part in normalized for part in _SENSITIVE_FIELD_PARTS)


def redact_text(value: Any) -> str:
    text = str(value)
    for pattern in _TEXT_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


def redact_setting(name: Any, value: Any) -> str:
    if is_sensitive_field(name):
        if isinstance(value, (list, tuple, set)):
            return f"{REDACTED} ({len(value)} value(s))"
        if isinstance(value, dict):
            return f"{REDACTED} ({len(value)} field(s))"
        return REDACTED
    return redact_text(_redact_nested(value))


def _redact_nested(value: Any) -> Any:
    """Return a log-safe representation of nested settings containers."""

    if isinstance(value, dict):
        return {
            str(key): REDACTED if is_sensitive_field(key) else _redact_nested(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_nested(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_nested(item) for item in value)
    if isinstance(value, set):
        return {_redact_nested(item) for item in value}
    return redact_text(value)
