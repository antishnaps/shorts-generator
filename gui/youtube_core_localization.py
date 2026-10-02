"""Locale-aware GUI boundary for :mod:`core.youtube_publisher`.

The backend exposes stable error and event codes and remains GUI-independent.
This module is the only place that turns those contracts into customer-facing
text in the currently selected interface language.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional

from core.youtube_publisher import (
    OAUTH_ERROR_ACCESS_DENIED,
    OAUTH_ERROR_INVALID_CLIENT,
    OAUTH_ERROR_INVALID_SCOPE,
    OAUTH_ERROR_REDIRECT_MISMATCH,
    OAUTH_ERROR_UNKNOWN,
    YOUTUBE_ERROR_AUTH_REQUIRED,
    YOUTUBE_ERROR_CHANNEL_NOT_FOUND,
    YOUTUBE_ERROR_DEPENDENCY_MISSING,
    YOUTUBE_ERROR_OAUTH_CONFIG_REQUIRED,
    YOUTUBE_ERROR_RETRY_SCHEDULED,
    YOUTUBE_ERROR_UPLOAD_CANCELLED,
    YOUTUBE_ERROR_UPLOAD_INTERRUPTED,
    YOUTUBE_ERROR_VIDEO_NOT_FOUND,
    YOUTUBE_EVENT_QUOTA_RETRY,
    YOUTUBE_EVENT_RECONCILE_FAILED,
    YOUTUBE_EVENT_RECONCILE_FOUND,
    YOUTUBE_EVENT_RECONCILE_MATCHED,
    YOUTUBE_EVENT_RECONCILE_SKIPPED,
    YOUTUBE_EVENT_UPLOAD_LIMIT_RETRY,
    OAuthClientSource,
    classify_oauth_error,
    classify_youtube_core_error,
    is_quota_exhausted_error,
    is_upload_limit_error,
)
from gui.locales.runtime_youtube_core import TRANSLATIONS
from gui.translations import get_ui_language


_OAUTH_ERROR_KEYS = {
    OAUTH_ERROR_ACCESS_DENIED: "youtube_core_oauth_access_denied",
    OAUTH_ERROR_REDIRECT_MISMATCH: "youtube_core_oauth_redirect_mismatch",
    OAUTH_ERROR_INVALID_CLIENT: "youtube_core_oauth_invalid_client",
    OAUTH_ERROR_INVALID_SCOPE: "youtube_core_oauth_invalid_scope",
}

_CORE_ERROR_KEYS = {
    YOUTUBE_ERROR_DEPENDENCY_MISSING: "youtube_core_dependency_missing",
    YOUTUBE_ERROR_AUTH_REQUIRED: "youtube_core_auth_required",
    YOUTUBE_ERROR_OAUTH_CONFIG_REQUIRED: "youtube_core_oauth_config_required",
    YOUTUBE_ERROR_CHANNEL_NOT_FOUND: "youtube_core_channel_not_found",
    YOUTUBE_ERROR_UPLOAD_INTERRUPTED: "youtube_core_queue_interrupted",
    YOUTUBE_ERROR_UPLOAD_CANCELLED: "youtube_core_queue_cancelled",
    YOUTUBE_ERROR_RETRY_SCHEDULED: "youtube_core_queue_retry",
}

_EVENT_KEYS = {
    YOUTUBE_EVENT_RECONCILE_SKIPPED: "youtube_core_log_reconcile_skipped",
    YOUTUBE_EVENT_RECONCILE_FOUND: "youtube_core_log_reconcile_found",
    YOUTUBE_EVENT_RECONCILE_FAILED: "youtube_core_log_reconcile_failed",
    YOUTUBE_EVENT_RECONCILE_MATCHED: "youtube_core_log_reconcile_matched",
    YOUTUBE_EVENT_UPLOAD_LIMIT_RETRY: "youtube_core_log_upload_limit_retry",
    YOUTUBE_EVENT_QUOTA_RETRY: "youtube_core_log_quota_retry",
}

_SOURCE_LABEL_KEYS = {
    "token": "youtube_core_oauth_source_token_label",
    "file": "youtube_core_oauth_source_file_label",
    "invalid_file": "youtube_core_oauth_source_invalid_file_label",
    "env": "youtube_core_oauth_source_env_label",
    "auto_file": "youtube_core_oauth_source_auto_file_label",
    "builtin": "youtube_core_oauth_source_builtin_label",
    "missing": "youtube_core_oauth_source_missing_label",
}


def _translate(key: str, language: Optional[str] = None) -> str:
    locale = language or get_ui_language()
    catalog = TRANSLATIONS.get(locale, TRANSLATIONS["English"])
    return catalog.get(key, TRANSLATIONS["English"].get(key, key))


def localize_oauth_error(error: Any, language: Optional[str] = None) -> str:
    """Explain a Google OAuth failure in the active interface language."""
    raw = str(error or "").strip()
    code = classify_oauth_error(error)
    key = _OAUTH_ERROR_KEYS.get(code)
    if key:
        return _translate(key, language)
    return raw or _translate("youtube_core_oauth_unknown", language)


def localize_youtube_error(
    error: Any,
    language: Optional[str] = None,
    *,
    code: str = "",
) -> str:
    """Localize a known core error and preserve unknown provider details."""
    raw = str(error or "").strip()
    resolved_code = code or classify_youtube_core_error(error)
    if resolved_code in _OAUTH_ERROR_KEYS:
        return _translate(_OAUTH_ERROR_KEYS[resolved_code], language)
    if resolved_code == YOUTUBE_ERROR_VIDEO_NOT_FOUND:
        path = raw.split(":", 1)[1].strip() if ":" in raw else raw
        return _translate("youtube_core_video_not_found", language).format(path=path)
    key = _CORE_ERROR_KEYS.get(resolved_code)
    if key:
        return _translate(key, language)
    return raw or _translate("youtube_core_oauth_unknown", language)


def localize_oauth_source(
    source: OAuthClientSource,
    language: Optional[str] = None,
) -> OAuthClientSource:
    """Return a copy of an auth-state record with localized display fields."""
    label_key = _SOURCE_LABEL_KEYS.get(source.source)
    label = _translate(label_key, language) if label_key else source.label

    if source.source == "token":
        is_backup = Path(source.path).name.endswith(".backup")
        message_key = (
            "youtube_core_oauth_source_token_backup"
            if is_backup
            else "youtube_core_oauth_source_token_current"
        )
    elif source.source == "invalid_file":
        message_key = "youtube_core_oauth_source_invalid_file"
    elif source.source == "missing":
        message_key = "youtube_core_oauth_source_missing"
    elif source.ready:
        message_key = "youtube_core_oauth_source_ready"
    else:
        message_key = ""
    message = _translate(message_key, language) if message_key else source.message
    return replace(source, label=label, message=message)


def oauth_browser_messages(language: Optional[str] = None) -> dict[str, str]:
    """Messages shown by the temporary local OAuth browser callback page."""
    return {
        "authorization_prompt_message": _translate(
            "youtube_core_browser_opening", language
        ),
        "success_message": _translate("youtube_core_browser_success", language),
    }


def localize_core_event(
    event: str,
    details: Optional[Mapping[str, Any]] = None,
    language: Optional[str] = None,
) -> str:
    """Format a stable YouTube diagnostic event for the activity log."""
    values = dict(details or {})
    key = _EVENT_KEYS.get(str(event or ""))
    if not key:
        return str(values.get("message") or event or "")
    return _translate(key, language).format(**values)


def localize_queue_error(
    task_or_outcome: Mapping[str, Any],
    raw_error: Any = "",
    language: Optional[str] = None,
) -> str:
    """Turn persisted queue error metadata into current-locale display text."""
    raw = str(raw_error or task_or_outcome.get("error") or "").strip()
    if is_quota_exhausted_error(raw):
        return _translate("youtube_core_queue_quota", language)
    if is_upload_limit_error(raw):
        return _translate("youtube_core_queue_upload_limit", language)

    code = str(task_or_outcome.get("error_code") or "")
    if code:
        return localize_youtube_error(raw, language, code=code)
    if raw.startswith("Автоповтор после") or raw.startswith(
        "Automatic retry scheduled:"
    ):
        return _translate("youtube_core_queue_retry", language)
    return localize_youtube_error(raw, language)


__all__ = [
    "localize_core_event",
    "localize_oauth_error",
    "localize_oauth_source",
    "localize_queue_error",
    "localize_youtube_error",
    "oauth_browser_messages",
]
