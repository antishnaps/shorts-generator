#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistent YouTube upload queue and API uploader."""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.process_registry import run_registered


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

FINAL_STATUSES = {"uploaded", "scheduled", "completed"}
ACTIVE_STATUSES = {"queued", "uploading", "failed"}
DEFAULT_QUEUE_PATH = Path("youtube_cache") / "youtube_publish_queue.json"
DEFAULT_TOKEN_PATH = Path("youtube_cache") / "youtube_oauth_token.json"
DEFAULT_ACCOUNTS_PATH = Path("youtube_cache") / "youtube_accounts.json"
# Google enforces a small daily upload allowance for unverified API projects.
# More concurrent resumable uploads only burns that allowance faster after a limit hit.
MAX_PARALLEL_UPLOADS = 3
DEFAULT_OAUTH_CLIENT_PATHS = (
    Path("assets") / "youtube_oauth_client.json",
    Path("youtube_cache") / "youtube_oauth_client.json",
    Path("youtube_cache") / "client_secret.json",
    Path("youtube_cache") / "client_secrets.json",
    Path("youtube_oauth_client.json"),
    Path("client_secret.json"),
    Path("client_secrets.json"),
)
LANGUAGE_CODES = {
    "Russian": "ru",
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Chinese": "zh",
    "Japanese": "ja",
    "Korean": "ko",
    "Portuguese": "pt",
    "Italian": "it",
    "Hindi": "hi",
    "Arabic": "ar",
}

# Stable, locale-neutral contracts used by GUI adapters. Core deliberately
# keeps no dependency on ``gui`` so command-line and background use stay safe.
OAUTH_ERROR_ACCESS_DENIED = "oauth_access_denied"
OAUTH_ERROR_REDIRECT_MISMATCH = "oauth_redirect_mismatch"
OAUTH_ERROR_INVALID_CLIENT = "oauth_invalid_client"
OAUTH_ERROR_INVALID_SCOPE = "oauth_invalid_scope"
OAUTH_ERROR_UNKNOWN = "oauth_unknown"

YOUTUBE_ERROR_DEPENDENCY_MISSING = "dependency_missing"
YOUTUBE_ERROR_AUTH_REQUIRED = "auth_required"
YOUTUBE_ERROR_OAUTH_CONFIG_REQUIRED = "oauth_config_required"
YOUTUBE_ERROR_CHANNEL_NOT_FOUND = "channel_not_found"
YOUTUBE_ERROR_VIDEO_NOT_FOUND = "video_not_found"
YOUTUBE_ERROR_UPLOAD_INTERRUPTED = "upload_interrupted"
YOUTUBE_ERROR_UPLOAD_CANCELLED = "upload_cancelled"
YOUTUBE_ERROR_RETRY_SCHEDULED = "retry_scheduled"

YOUTUBE_EVENT_RECONCILE_SKIPPED = "reconcile_skipped"
YOUTUBE_EVENT_RECONCILE_FOUND = "reconcile_found"
YOUTUBE_EVENT_RECONCILE_FAILED = "reconcile_failed"
YOUTUBE_EVENT_RECONCILE_MATCHED = "reconcile_matched"
YOUTUBE_EVENT_UPLOAD_LIMIT_RETRY = "upload_limit_retry"
YOUTUBE_EVENT_QUOTA_RETRY = "quota_retry"

YouTubeEventCallback = Callable[[str, Dict[str, Any]], None]


@dataclass
class PublishSettings:
    enabled: bool = False
    auto_start: bool = False
    client_secrets_path: str = ""
    token_path: str = str(DEFAULT_TOKEN_PATH)
    queue_path: str = str(DEFAULT_QUEUE_PATH)
    accounts_path: str = str(DEFAULT_ACCOUNTS_PATH)
    active_account_id: str = ""
    privacy_mode: str = "schedule"  # schedule, private, public
    videos_per_day: int = 3
    publish_start: str = ""
    publish_window_start: str = "10:00"
    publish_window_end: str = "22:00"
    publish_jitter_minutes: int = 20
    first_upload_delay_min_minutes: int = 15
    first_upload_delay_max_minutes: int = 15
    upload_delay_min_minutes: int = 180
    upload_delay_max_minutes: int = 180
    max_parallel_uploads: int = 1
    notify_subscribers: bool = False
    made_for_kids: bool = False
    contains_synthetic_media: bool = True
    category_id: str = "24"
    channel_title: str = ""
    channel_id: str = ""
    last_channel_check: str = ""

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "PublishSettings":
        if not isinstance(data, dict):
            return cls()
        defaults = cls()
        clean: Dict[str, Any] = {}
        for field in defaults.__dataclass_fields__:  # type: ignore[attr-defined]
            clean[field] = data.get(field, getattr(defaults, field))
        settings = cls(**clean)
        settings.videos_per_day = max(1, int(settings.videos_per_day or 1))
        settings.publish_jitter_minutes = max(0, int(settings.publish_jitter_minutes or 0))
        settings.first_upload_delay_min_minutes = max(0, int(settings.first_upload_delay_min_minutes or 0))
        settings.first_upload_delay_max_minutes = max(
            settings.first_upload_delay_min_minutes,
            int(settings.first_upload_delay_max_minutes or 0),
        )
        settings.upload_delay_min_minutes = max(0, int(settings.upload_delay_min_minutes or 0))
        settings.upload_delay_max_minutes = max(
            settings.upload_delay_min_minutes,
            int(settings.upload_delay_max_minutes or 0),
        )
        settings.max_parallel_uploads = max(
            1,
            min(MAX_PARALLEL_UPLOADS, int(settings.max_parallel_uploads or 1)),
        )
        if settings.privacy_mode not in {"schedule", "private", "public"}:
            settings.privacy_mode = "schedule"
        settings.category_id = str(settings.category_id or "24").strip() or "24"
        return settings

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OAuthClientSource:
    """Resolved OAuth material for the browser login flow."""

    source: str
    label: str
    ready: bool
    path: str = ""
    config: Optional[Dict[str, Any]] = None
    has_token: bool = False
    message: str = ""


class YouTubeAccountStore:
    """Persistent index of independently authorized Google/YouTube accounts."""

    def __init__(self, path: str | Path = DEFAULT_ACCOUNTS_PATH):
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> Dict[str, Any]:
        data = _read_json(self.path, {"version": 1, "active_account_id": "", "accounts": []})
        if not isinstance(data.get("accounts"), list):
            data["accounts"] = []
        data["version"] = 1
        data.setdefault("active_account_id", "")
        return data

    def reload(self) -> None:
        self._data = self._load()

    def save(self) -> None:
        _atomic_write_json(self.path, self._data)

    def accounts(self) -> List[Dict[str, Any]]:
        self.reload()
        return [dict(account) for account in self._data.get("accounts", [])]

    def active_account_id(self) -> str:
        self.reload()
        return str(self._data.get("active_account_id") or "")

    def get(self, account_id: str) -> Optional[Dict[str, Any]]:
        self.reload()
        for account in self._data.get("accounts", []):
            if str(account.get("id")) == str(account_id):
                return dict(account)
        return None

    def token_path_for_new_account(self) -> Path:
        token_dir = self.path.parent / "youtube_accounts"
        token_dir.mkdir(parents=True, exist_ok=True)
        return token_dir / f"account_{uuid.uuid4().hex}.json"

    def upsert_channel(self, info: Dict[str, Any], token_path: str | Path) -> Dict[str, Any]:
        self.reload()
        channel_id = str(info.get("channel_id") or "").strip()
        if not channel_id:
            raise ValueError("YouTube channel id is required")
        now = isoformat(now_local())
        account = next(
            (
                item for item in self._data.get("accounts", [])
                if str(item.get("channel_id") or "") == channel_id
            ),
            None,
        )
        if account is None:
            account = {"id": channel_id, "added_at": now}
            self._data.setdefault("accounts", []).append(account)
        old_token_path = str(account.get("token_path") or "")
        account.update({
            "id": channel_id,
            "channel_id": channel_id,
            "channel_title": str(info.get("channel_title") or "").strip(),
            "token_path": str(Path(token_path)),
            "last_used_at": now,
        })
        self._data["active_account_id"] = channel_id
        self.save()
        if old_token_path and old_token_path != account["token_path"]:
            self._delete_token_files(Path(old_token_path))
        return dict(account)

    def set_active(self, account_id: str) -> Optional[Dict[str, Any]]:
        self.reload()
        account = next(
            (item for item in self._data.get("accounts", []) if str(item.get("id")) == str(account_id)),
            None,
        )
        if account is None:
            return None
        account["last_used_at"] = isoformat(now_local())
        self._data["active_account_id"] = str(account_id)
        self.save()
        return dict(account)

    def mark_quota_exhausted(
        self,
        account_id: str,
        until: Optional[datetime] = None,
    ) -> None:
        """Mark an account unavailable until its applicable YouTube limit resets."""
        self.reload()
        reset_at = until or youtube_quota_reset_time()
        for account in self._data.get("accounts", []):
            if str(account.get("id")) == str(account_id):
                account["quota_exhausted_until"] = isoformat(reset_at)
                break
        self.save()

    def clear_quota_exhausted(self, account_id: str) -> None:
        """Clear quota-exhausted mark (e.g. after a successful upload)."""
        self.reload()
        for account in self._data.get("accounts", []):
            if str(account.get("id")) == str(account_id):
                account.pop("quota_exhausted_until", None)
                break
        self.save()

    def next_available_account(self, current_account_id: str = "") -> Optional[Dict[str, Any]]:
        """Return the next account whose quota has not been exhausted, skipping current."""
        self.reload()
        accounts = self._data.get("accounts", [])
        if len(accounts) <= 1:
            return None
        now = now_local()
        # Build ordered list starting after current
        current_idx = next(
            (i for i, a in enumerate(accounts) if str(a.get("id")) == str(current_account_id)),
            -1,
        )
        ordered = accounts[current_idx + 1:] + accounts[:current_idx + 1]
        for account in ordered:
            if str(account.get("id")) == str(current_account_id):
                continue  # skip self
            exhausted_until = parse_datetime(account.get("quota_exhausted_until"))
            if exhausted_until and now < exhausted_until:
                continue  # still exhausted
            return dict(account)
        return None

    def remove(self, account_id: str, delete_token: bool = True) -> bool:
        self.reload()
        accounts = self._data.get("accounts", [])
        removed = next(
            (item for item in accounts if str(item.get("id")) == str(account_id)),
            None,
        )
        if removed is None:
            return False
        self._data["accounts"] = [
            item for item in accounts if str(item.get("id")) != str(account_id)
        ]
        if str(self._data.get("active_account_id") or "") == str(account_id):
            remaining = self._data["accounts"]
            self._data["active_account_id"] = str(remaining[0].get("id")) if remaining else ""
        self.save()
        if delete_token:
            self._delete_token_files(Path(str(removed.get("token_path") or "")))
        return True

    @staticmethod
    def _delete_token_files(token_path: Path) -> None:
        if not str(token_path) or token_path == Path("."):
            return
        for candidate in (token_path, _backup_path(token_path)):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now_local().tzinfo)
    return dt.astimezone()


def isoformat(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now_local().tzinfo)
    return dt.astimezone().isoformat(timespec="seconds")


def youtube_quota_reset_time(moment: Optional[datetime] = None) -> datetime:
    """Return the next YouTube daily quota reset time, in local timezone."""
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=now_local().tzinfo)
    try:
        pacific = ZoneInfo("America/Los_Angeles")
    except ZoneInfoNotFoundError:
        pacific = _fallback_pacific_timezone(current)
    current_pacific = current.astimezone(pacific)
    reset_at = current_pacific.replace(hour=0, minute=0, second=0, microsecond=0)
    if current_pacific >= reset_at:
        reset_at += timedelta(days=1)
    return reset_at.astimezone()


def _fallback_pacific_timezone(moment: datetime) -> timezone:
    """Approximate current US Pacific offset when Windows has no IANA tzdata."""
    current_utc = moment.astimezone(timezone.utc)
    year = current_utc.year

    march_first = datetime(year, 3, 1, tzinfo=timezone.utc)
    first_sunday_offset = (6 - march_first.weekday()) % 7
    second_sunday = 1 + first_sunday_offset + 7
    dst_start_utc = datetime(year, 3, second_sunday, 10, tzinfo=timezone.utc)

    november_first = datetime(year, 11, 1, tzinfo=timezone.utc)
    first_sunday = 1 + ((6 - november_first.weekday()) % 7)
    dst_end_utc = datetime(year, 11, first_sunday, 9, tzinfo=timezone.utc)

    offset_hours = -7 if dst_start_utc <= current_utc < dst_end_utc else -8
    return timezone(timedelta(hours=offset_hours))


def youtube_quota_retry_delay_seconds(moment: Optional[datetime] = None) -> int:
    reset_at = youtube_quota_reset_time(moment)
    seconds = int((reset_at - now_local()).total_seconds())
    return max(300, seconds + random.randint(300, 1800))


def youtube_upload_limit_retry_delay_seconds() -> int:
    """YouTube's channel upload cap is a rolling 24-hour limit, not an API quota day."""
    return (24 * 60 * 60) + random.randint(300, 1800)


def _parse_clock(value: str, fallback: time) -> time:
    try:
        hours, minutes = (value or "").split(":", 1)
        return time(hour=max(0, min(23, int(hours))), minute=max(0, min(59, int(minutes))))
    except Exception:
        return fallback


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    _atomic_write_json_with_backup(path, data)


def _backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".backup")


def _atomic_write_json_with_backup(
    path: Path,
    data: Dict[str, Any],
    *,
    backup_existing: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        suffix=".tmp",
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    if backup_existing and path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as current:
                existing = json.load(current)
            if isinstance(existing, dict):
                shutil.copy2(path, _backup_path(path))
        except Exception:
            pass
    # On Windows the target file may be briefly locked by another reader
    # (e.g. the GUI or an antivirus), causing os.replace / Path.replace to
    # raise PermissionError (WinError 5). Retry a few times before giving up.
    _last_exc: Exception | None = None
    for _attempt in range(6):
        try:
            tmp_path.replace(path)
            return
        except PermissionError as exc:
            _last_exc = exc
            import time as _time
            _time.sleep(0.05 * (2 ** _attempt))  # 50ms, 100ms, 200ms, 400ms…
    # All retries exhausted — write directly so data is not lost entirely.
    try:
        with open(path, "w", encoding="utf-8") as _f:
            json.dump(data, _f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    try:
        tmp_path.unlink(missing_ok=True)
    except Exception:
        pass


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    for candidate in (path, _backup_path(path)):
        if not candidate.exists():
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                continue
            if candidate != path:
                _atomic_write_json_with_backup(path, data, backup_existing=False)
            return data
        except Exception:
            continue
    return default


def normalize_hashtags(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.findall(r"#[\w\u0400-\u04ff\u0590-\u06ff\u0900-\u097f]+", value, flags=re.UNICODE)
        if not parts:
            parts = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value]
    else:
        return []

    result: List[str] = []
    seen = set()
    for raw in parts:
        tag = str(raw).strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = f"#{tag}"
        tag = re.sub(r"\s+", "_", tag)
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            result.append(tag)
    return result


def tags_from_hashtags(hashtags: Iterable[str], limit: int = 30) -> List[str]:
    tags: List[str] = []
    total_chars = 0
    seen = set()
    for hashtag in hashtags:
        tag = re.sub(r"^#+", "", str(hashtag)).strip().replace("_", " ")
        tag = re.sub(r"\s+", " ", tag)
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        if total_chars + len(tag) > 450:
            break
        seen.add(key)
        tags.append(tag)
        total_chars += len(tag) + 1
        if len(tags) >= limit:
            break
    return tags


def clean_title(value: Any, fallback: str) -> str:
    title = str(value or "").strip() or fallback
    title = re.sub(r"\s+", " ", title)
    return title[:100].strip() or fallback[:100]


def clean_description(value: Any) -> str:
    """Turn generated prose into a readable YouTube description."""
    description = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not description:
        return ""

    # Description files may also contain an editor-only A/B title panel.
    description = re.sub(
        r"\A[\s\S]*?ВАРИАНТЫ\s+ЗАГОЛОВКОВ[\s\S]*?─{5,}\s*",
        "",
        description,
        count=1,
        flags=re.IGNORECASE,
    )
    description = re.sub(r"(?m)^.*ХЕШТЕГИ.*$", "", description, flags=re.IGNORECASE)
    description = re.sub(r"(?m)^\s*[╔╗╚╝═║┌─└┘│]+\s*$", "", description)

    hashtags = []
    seen_hashtags = set()
    for match in re.findall(r"(?<!\w)#[^\s#]+", description, flags=re.UNICODE):
        tag = match.rstrip(".,;:!?)]}»\"")
        key = tag.casefold()
        if len(tag) > 1 and key not in seen_hashtags:
            seen_hashtags.add(key)
            hashtags.append(tag)

    body = re.sub(r"(?<!\w)#[^\s#]+", " ", description, flags=re.UNICODE)
    body = re.sub(r"(?:^|\s)(?:RU|EN|ES|DE|FR|IT|PT)(?:\s|$)", " ", body, flags=re.IGNORECASE)
    body = re.sub(r"\s*(?:🔹|▪️?|◾|◆|•)\s*", "\n• ", body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r" *\n *", "\n", body).strip()

    elements: List[Tuple[str, str]] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("• "):
            bullet_content = line[2:].strip()
            sentence_end = re.search(r'[.!?…](?:[»"”])?(?:\s|$)', bullet_content)
            if sentence_end:
                end = sentence_end.end()
                elements.append(("bullet", bullet_content[:end].strip()))
                remainder = bullet_content[end:].strip()
                if remainder:
                    elements.append(("prose", remainder))
            else:
                elements.append(("bullet", bullet_content))
        else:
            elements.append(("prose", line))

    blocks: List[str] = []
    bullet_group: List[str] = []

    def flush_bullets() -> None:
        if bullet_group:
            blocks.append("\n".join(f"• {item}" for item in bullet_group))
            bullet_group.clear()

    def append_prose(text: str) -> None:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?…])\s+", text) if part.strip()]
        paragraph: List[str] = []
        paragraph_length = 0
        for sentence in sentences or [text]:
            next_length = paragraph_length + len(sentence) + (1 if paragraph else 0)
            if paragraph and (len(paragraph) >= 2 or next_length > 420):
                blocks.append(" ".join(paragraph))
                paragraph = []
                paragraph_length = 0
            paragraph.append(sentence)
            paragraph_length += len(sentence) + (1 if paragraph_length else 0)
        if paragraph:
            blocks.append(" ".join(paragraph))

    for element_type, text in elements:
        if element_type == "bullet":
            bullet_group.append(text)
        else:
            flush_bullets()
            append_prose(text)
    flush_bullets()

    hashtag_blocks: List[str] = []
    regular_tags = [tag for tag in hashtags if tag.casefold() != "#shorts"]
    for index in range(0, len(regular_tags), 6):
        hashtag_blocks.append(" ".join(regular_tags[index:index + 6]))
    if any(tag.casefold() == "#shorts" for tag in hashtags):
        hashtag_blocks.append("#Shorts")

    suffix = "\n\n".join(hashtag_blocks)
    body_text = "\n\n".join(blocks).strip()
    reserved = len(suffix) + (2 if suffix and body_text else 0)
    if len(body_text) + reserved > 5000:
        body_text = body_text[:max(0, 4997 - reserved)].rstrip() + "..."
    return (body_text + ("\n\n" if body_text and suffix else "") + suffix).strip()


def ensure_shorts_hashtag(description: str, video_kind: str) -> str:
    if video_kind != "short":
        return description
    if re.search(r"#shorts\b", description, flags=re.IGNORECASE):
        return description
    return (description.rstrip() + "\n\n#Shorts").strip()


def detect_video_kind(duration_seconds: Optional[float], width: Optional[int], height: Optional[int]) -> str:
    duration_ok = duration_seconds is None or duration_seconds <= 180.5
    vertical_or_square = width is None or height is None or height >= width
    return "short" if duration_ok and vertical_or_square else "long"


def _ffprobe_path() -> Optional[str]:
    bundled = Path("tools") / "ffmpeg" / "ffprobe.exe"
    if bundled.exists():
        return str(bundled)
    return shutil.which("ffprobe")


def probe_video(video_path: str) -> Dict[str, Any]:
    path = Path(video_path)
    probe = _ffprobe_path()
    if not probe or not path.exists():
        return {}

    command = [
        probe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = run_registered(
            command,
            label="ffprobe_youtube_publish_video",
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        fmt = data.get("format") or {}
        duration_raw = stream.get("duration") or fmt.get("duration")
        duration = float(duration_raw) if duration_raw not in (None, "N/A") else None
        width = int(stream["width"]) if stream.get("width") else None
        height = int(stream["height"]) if stream.get("height") else None
        return {"duration_seconds": duration, "width": width, "height": height}
    except Exception:
        return {}


def _read_text_if_exists(path: Any) -> str:
    if not path:
        return ""
    try:
        candidate = Path(path)
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""
    return ""


def _metadata_from_package(package: Dict[str, Any]) -> Tuple[str, str, List[str]]:
    video_path = Path(str(package.get("video_path") or "video.mp4"))
    metadata = package.get("metadata") or package.get("description_data") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    variants = metadata.get("title_variants") or package.get("title_variants") or []
    if isinstance(variants, str):
        variants = [variants]
    title_source = package.get("title") or metadata.get("title") or (variants[0] if variants else "")
    title = clean_title(title_source, video_path.stem)

    description = (
        package.get("description_text")
        or package.get("description")
        or metadata.get("youtube_description")
        or metadata.get("description")
        or _read_text_if_exists(package.get("description_file"))
    )

    hashtags = normalize_hashtags(package.get("hashtags") or metadata.get("hashtags") or description)
    if hashtags and isinstance(description, str):
        joined_hashtags = " ".join(hashtags)
        if not any(tag.lower() in description.lower() for tag in hashtags[:3]):
            description = f"{description.strip()}\n\n{joined_hashtags}".strip()

    return title, clean_description(description), hashtags


def build_package_from_video(video_path: str) -> Dict[str, Any]:
    path = Path(video_path)
    probed = probe_video(str(path))
    description_file = path.parent / "descriptions" / f"{path.stem}.txt"
    description_text = _read_text_if_exists(description_file)
    hashtags = normalize_hashtags(description_text)
    return {
        "video_path": str(path),
        "title": path.stem,
        "description_text": description_text,
        "hashtags": hashtags,
        "duration_seconds": probed.get("duration_seconds"),
        "width": probed.get("width"),
        "height": probed.get("height"),
        "language": "",
        "description_file": str(description_file) if description_file.exists() else "",
    }


_QUEUE_TRANSACTION_LOCK = threading.RLock()
_OAUTH_TOKEN_LOCK = threading.RLock()


def _queue_transaction(method):
    """Reload and mutate the shared disk queue as one in-process transaction."""
    def wrapped(self, *args, **kwargs):
        with _QUEUE_TRANSACTION_LOCK:
            self._data = self._load()
            return method(self, *args, **kwargs)

    wrapped.__name__ = method.__name__
    wrapped.__doc__ = method.__doc__
    return wrapped


class YouTubePublishQueue:
    """Disk-backed queue of videos waiting for YouTube upload."""

    def __init__(self, path: str | Path = DEFAULT_QUEUE_PATH):
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> Dict[str, Any]:
        data = _read_json(self.path, {"version": 1, "tasks": []})
        tasks = data.get("tasks")
        if not isinstance(tasks, list):
            data["tasks"] = []
        data["version"] = 1
        data.setdefault("upload_parallelism", 1)
        return data

    def reload(self) -> None:
        self._data = self._load()

    def save(self) -> None:
        _atomic_write_json(self.path, self._data)

    @_queue_transaction
    def recover_interrupted_uploads(self) -> int:
        """Recover uploads only when a new worker starts after the old worker has stopped."""
        recovered = 0
        for task in self._data.get("tasks", []):
            if task.get("status") != "uploading":
                continue
            task["status"] = "queued"
            task["error"] = "Загрузка была прервана, задача возвращена в очередь"
            task["error_code"] = YOUTUBE_ERROR_UPLOAD_INTERRUPTED
            task["updated_at"] = isoformat(now_local())
            recovered += 1
        if recovered:
            self.save()
        return recovered

    def reconcile_with_youtube(
        self,
        uploader: "YouTubeUploader",
        log_callback: Optional[Callable[[str], None]] = None,
        event_callback: Optional[YouTubeEventCallback] = None,
        max_results: int = 50,
    ) -> int:
        """Cross-check the queue against videos actually on the channel.

        For every queued/failed/interrupted task that has no youtube_video_id,
        fetch the channel's recent uploads via the API and try to match by
        title.  Matched tasks are immediately marked as *uploaded* so they
        won't be sent again (preventing duplicate uploads after a crash or a
        manual upload through the browser).

        Returns the number of tasks reconciled.
        """
        def _diagnostic(event: str, details: Dict[str, Any], legacy_message: str) -> None:
            if event_callback:
                event_callback(event, details)
            if log_callback:
                log_callback(legacy_message)

        try:
            _, _, _, build, _ = uploader._load_google_modules()
            service = uploader.build_service(allow_interactive=False)
        except Exception as exc:
            _diagnostic(
                YOUTUBE_EVENT_RECONCILE_SKIPPED,
                {"error": str(exc)},
                f"⚠️ Сверка с YouTube пропущена (нет доступа): {exc}",
            )
            return 0

        try:
            # 1. Fetch the channel's uploads playlist id
            ch_resp = service.channels().list(part="contentDetails", mine=True).execute()
            items = (ch_resp.get("items") or [])
            if not items:
                return 0
            uploads_playlist = (
                items[0].get("contentDetails", {})
                .get("relatedPlaylists", {})
                .get("uploads", "")
            )
            if not uploads_playlist:
                return 0

            # 2. Fetch recent uploads (up to max_results)
            yt_videos: Dict[str, str] = {}  # title_lower -> video_id
            yt_video_statuses: Dict[str, str] = {}  # video_id -> privacyStatus
            page_token = ""
            fetched = 0
            while fetched < max_results:
                batch = min(50, max_results - fetched)
                kwargs: Dict[str, Any] = dict(
                    playlistId=uploads_playlist,
                    part="snippet",
                    maxResults=batch,
                )
                if page_token:
                    kwargs["pageToken"] = page_token
                pl_resp = service.playlistItems().list(**kwargs).execute()
                for item in (pl_resp.get("items") or []):
                    snippet = item.get("snippet") or {}
                    vid_title = (snippet.get("title") or "").strip()
                    vid_id = (
                        (snippet.get("resourceId") or {}).get("videoId") or ""
                    )
                    if vid_title and vid_id:
                        yt_videos[vid_title.lower()] = vid_id
                fetched += batch
                page_token = pl_resp.get("nextPageToken", "")
                if not page_token:
                    break

            if not yt_videos:
                return 0

            _diagnostic(
                YOUTUBE_EVENT_RECONCILE_FOUND,
                {"count": len(yt_videos)},
                f"🔍 Сверка с YouTube: найдено {len(yt_videos)} видео на канале",
            )

        except Exception as exc:
            _diagnostic(
                YOUTUBE_EVENT_RECONCILE_FAILED,
                {"error": str(exc)},
                f"⚠️ Ошибка при получении списка видео с канала: {exc}",
            )
            return 0

        # 3. Match queue tasks
        reconciled = 0
        with _QUEUE_TRANSACTION_LOCK:
            self._data = self._load()
            for task in self._data.get("tasks", []):
                # Only consider tasks that don't already have a youtube_video_id
                if task.get("youtube_video_id"):
                    continue
                if task.get("status") in {"uploaded", "scheduled"}:
                    continue
                task_title = (task.get("title") or "").strip().lower()
                if not task_title:
                    continue
                # Try exact match first, then substring match
                matched_id = yt_videos.get(task_title)
                if not matched_id:
                    # Partial match: task title is contained in a YT title or vice versa
                    for yt_title, vid_id in yt_videos.items():
                        if task_title in yt_title or yt_title in task_title:
                            matched_id = vid_id
                            break
                if matched_id:
                    prev_status = task.get("status", "")
                    task["status"] = "uploaded"
                    task["youtube_video_id"] = matched_id
                    task["youtube_url"] = f"https://www.youtube.com/watch?v={matched_id}"
                    task["error"] = ""
                    task["updated_at"] = isoformat(now_local())
                    reconciled += 1
                    title = str(task.get("title") or "")
                    _diagnostic(
                        YOUTUBE_EVENT_RECONCILE_MATCHED,
                        {
                            "title": title,
                            "video_id": matched_id,
                            "status": str(prev_status),
                        },
                        f"   ✅ Сверка: '{title}' уже на YouTube ({matched_id}), "
                        f"статус {prev_status} → uploaded",
                    )
            if reconciled:
                self.save()

        return reconciled

    @_queue_transaction
    def tasks(self) -> List[Dict[str, Any]]:
        return list(self._data.get("tasks", []))

    def enqueue_package(self, package: Dict[str, Any], settings: PublishSettings) -> Dict[str, Any]:
        return self.enqueue_packages([package], settings)[0]

    @_queue_transaction
    def enqueue_packages(
        self,
        packages: Iterable[Dict[str, Any]],
        settings: PublishSettings,
    ) -> List[Dict[str, Any]]:
        self._rebalance_upload_waves_locked(settings)
        added: List[Dict[str, Any]] = []
        for package in packages:
            task = self._task_from_package(package, settings)
            duplicate = self._find_by_path(task["video_path"])
            if duplicate:
                duplicate.update(
                    {
                        "title": task["title"],
                        "description": task["description"],
                        "hashtags": task["hashtags"],
                        "tags": task["tags"],
                        "language": task["language"],
                        "video_kind": task["video_kind"],
                        "duration_seconds": task.get("duration_seconds"),
                        "width": task.get("width"),
                        "height": task.get("height"),
                        "privacy_mode": settings.privacy_mode,
                        "updated_at": isoformat(now_local()),
                    }
                )
                if duplicate.get("status") == "failed":
                    duplicate["status"] = "queued"
                    duplicate["error"] = ""
                    duplicate["upload_after"] = isoformat(self._next_upload_time(settings))
                    duplicate["publish_at"] = ""
                    publish_at = self._next_publish_time(settings)
                    duplicate["publish_at"] = isoformat(publish_at) if publish_at else ""
                added.append(duplicate)
                continue
            task["upload_after"] = isoformat(self._next_upload_time(settings))
            publish_at = self._next_publish_time(settings)
            task["publish_at"] = isoformat(publish_at) if publish_at else ""
            self._data.setdefault("tasks", []).append(task)
            added.append(task)
        self.save()
        return added

    def _rebalance_upload_waves_locked(self, settings: PublishSettings) -> int:
        parallelism = max(1, min(MAX_PARALLEL_UPLOADS, int(settings.max_parallel_uploads or 1)))
        previous = int(self._data.get("upload_parallelism") or 1)
        if previous == parallelism:
            return 0

        queued = [task for task in self._data.get("tasks", []) if task.get("status") == "queued"]
        queued.sort(key=lambda task: task.get("upload_after") or "")
        self._data["upload_parallelism"] = parallelism
        if not queued:
            return 0

        parsed_times = [parse_datetime(task.get("upload_after")) for task in queued]
        anchor = next((value for value in parsed_times if value is not None), now_local())
        changed = 0
        for index, task in enumerate(queued):
            if index and index % parallelism == 0:
                anchor = max(anchor, now_local()) + timedelta(
                    minutes=random.randint(
                        settings.upload_delay_min_minutes,
                        settings.upload_delay_max_minutes,
                    )
                )
            planned = isoformat(anchor)
            if task.get("upload_after") != planned:
                task["upload_after"] = planned
                task["updated_at"] = isoformat(now_local())
                changed += 1
        return changed

    @_queue_transaction
    def rebalance_upload_waves(self, settings: PublishSettings) -> int:
        """Migrate an existing queue when its parallelism setting changes."""
        changed = self._rebalance_upload_waves_locked(settings)
        if changed or int(self._data.get("upload_parallelism") or 1) == settings.max_parallel_uploads:
            self.save()
        return changed

    @_queue_transaction
    def start_queued_now(self, settings: PublishSettings) -> int:
        """Schedule the first queued wave now and keep simple pauses between waves."""
        queued = [task for task in self._data.get("tasks", []) if task.get("status") == "queued"]
        queued.sort(key=lambda task: task.get("upload_after") or "")
        if not queued:
            return 0

        parallelism = max(1, min(MAX_PARALLEL_UPLOADS, int(settings.max_parallel_uploads or 1)))
        anchor = now_local()
        for index, task in enumerate(queued):
            if index and index % parallelism == 0:
                anchor += timedelta(
                    minutes=random.randint(
                        settings.upload_delay_min_minutes,
                        settings.upload_delay_max_minutes,
                    )
                )
            task["upload_after"] = isoformat(anchor)
            task["updated_at"] = isoformat(now_local())
        self._data["upload_parallelism"] = parallelism
        self.save()
        return len(queued)

    def _find_by_path(self, video_path: str) -> Optional[Dict[str, Any]]:
        normalized = str(Path(video_path))
        for task in self._data.get("tasks", []):
            if str(Path(str(task.get("video_path", "")))) == normalized:
                return task
        return None

    def _task_from_package(self, package: Dict[str, Any], settings: PublishSettings) -> Dict[str, Any]:
        video_path = str(package.get("video_path") or "").strip()
        if not video_path:
            raise ValueError("video_path is required")
        probed = {}
        if not all(package.get(key) for key in ("duration_seconds", "width", "height")):
            probed = probe_video(video_path)
        duration = package.get("duration_seconds", probed.get("duration_seconds"))
        width = package.get("width", probed.get("width"))
        height = package.get("height", probed.get("height"))
        try:
            duration = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        try:
            width = int(width) if width is not None else None
            height = int(height) if height is not None else None
        except (TypeError, ValueError):
            width = height = None

        title, description, hashtags = _metadata_from_package(package)
        video_kind = package.get("video_kind") or detect_video_kind(duration, width, height)
        description = clean_description(ensure_shorts_hashtag(description, video_kind))
        created = isoformat(now_local())
        return {
            "id": uuid.uuid4().hex,
            "video_path": video_path,
            "title": title,
            "description": description,
            "hashtags": hashtags,
            "tags": tags_from_hashtags(hashtags),
            "language": str(package.get("language") or "").strip(),
            "video_kind": video_kind,
            "duration_seconds": duration,
            "width": width,
            "height": height,
            "description_file": str(package.get("description_file") or ""),
            "batch_output_dir": str(package.get("batch_output_dir") or ""),
            "batch_manifest": str(package.get("batch_manifest") or ""),
            "source_video_num": package.get("video_num"),
            "theme": str(package.get("theme") or ""),
            "opening_hook_family": str(package.get("opening_hook_family") or ""),
            "first_shot_hook": str(package.get("first_shot_hook") or ""),
            "status": "queued",
            "upload_after": "",
            "publish_at": "",
            "youtube_video_id": "",
            "youtube_url": "",
            "error": "",
            "error_code": "",
            "attempts": 0,
            "privacy_mode": settings.privacy_mode,
            "created_at": created,
            "updated_at": created,
        }

    def _next_upload_time(self, settings: PublishSettings) -> datetime:
        existing_times: List[datetime] = []
        for task in self._data.get("tasks", []):
            if task.get("status") in {"queued", "uploading"}:
                parsed = parse_datetime(task.get("upload_after"))
                if parsed:
                    existing_times.append(parsed)

        if not existing_times:
            base = now_local()
            delay = random.randint(
                settings.first_upload_delay_min_minutes,
                settings.first_upload_delay_max_minutes,
            )
            return base + timedelta(minutes=delay)

        latest = max(existing_times)
        parallelism = max(1, min(MAX_PARALLEL_UPLOADS, int(settings.max_parallel_uploads or 1)))
        tasks_in_latest_wave = sum(
            1 for scheduled in existing_times
            if abs((scheduled - latest).total_seconds()) < 1
        )
        if tasks_in_latest_wave < parallelism:
            return latest

        base = max(latest, now_local())
        delay = random.randint(settings.upload_delay_min_minutes, settings.upload_delay_max_minutes)
        return base + timedelta(minutes=delay)

    def _next_publish_time(
        self,
        settings: PublishSettings,
        not_before: Optional[datetime] = None,
    ) -> Optional[datetime]:
        if settings.privacy_mode != "schedule":
            return None

        occupied_slots = set()
        for task in self._data.get("tasks", []):
            publish_at = parse_datetime(task.get("publish_at"))
            if publish_at:
                occupied_slots.add(publish_at.replace(second=0, microsecond=0).isoformat())

        now = now_local()
        min_publish_at = now + timedelta(minutes=10)
        configured_start = parse_datetime(settings.publish_start)
        if configured_start is not None:
            min_publish_at = max(min_publish_at, configured_start)
        if not_before is not None:
            min_publish_at = max(min_publish_at, parse_datetime(not_before) or min_publish_at)
        for index in range(20000):
            slot = self._publish_slot(index, settings)
            slot_key = slot.replace(second=0, microsecond=0).isoformat()
            # publish_start is an inclusive boundary. Rejecting an equal first
            # slot left day one under-filled and spilled the last item to day N+1.
            if slot >= min_publish_at and slot_key not in occupied_slots:
                return slot
        return now + timedelta(hours=1)

    def _publish_slot(self, index: int, settings: PublishSettings) -> datetime:
        start_dt = parse_datetime(settings.publish_start)
        if start_dt is None:
            tomorrow = now_local().date() + timedelta(days=1)
            start_dt = datetime.combine(tomorrow, _parse_clock(settings.publish_window_start, time(10, 0)))
            start_dt = start_dt.replace(tzinfo=now_local().tzinfo)

        per_day = max(1, settings.videos_per_day)
        day_index = index // per_day
        position = index % per_day
        day = (start_dt + timedelta(days=day_index)).date()

        window_start = _parse_clock(settings.publish_window_start, time(10, 0))
        window_end = _parse_clock(settings.publish_window_end, time(22, 0))
        start_minutes = window_start.hour * 60 + window_start.minute
        end_minutes = window_end.hour * 60 + window_end.minute
        if end_minutes <= start_minutes:
            end_minutes += 24 * 60

        if per_day <= 1:
            minute = start_minutes
        else:
            minute = int(round(start_minutes + ((end_minutes - start_minutes) * position / (per_day - 1))))

        jitter = settings.publish_jitter_minutes
        if jitter:
            # A schedule position must always resolve to the same moment.
            # Otherwise each retry sees a new random minute as a free slot and
            # can pack dozens of videos into a single calendar day.
            jitter_seed = (
                f"{settings.publish_start}|{settings.publish_window_start}|"
                f"{settings.publish_window_end}|{per_day}|{index}"
            )
            minute += random.Random(jitter_seed).randint(-jitter, jitter)
        minute = max(start_minutes, min(end_minutes, minute))

        day_offset, minute_of_day = divmod(minute, 24 * 60)
        hour, minute_part = divmod(minute_of_day, 60)
        slot = datetime.combine(day + timedelta(days=day_offset), time(hour=hour, minute=minute_part))
        return slot.replace(tzinfo=start_dt.tzinfo).astimezone()

    @_queue_transaction
    def reschedule_pending_publish_times(self, settings: PublishSettings) -> int:
        """Rebuild publication slots for videos that have not reached YouTube yet."""
        pending = [
            task
            for task in self._data.get("tasks", [])
            if task.get("status") in {"queued", "failed"}
        ]
        pending.sort(key=lambda task: (task.get("created_at") or "", task.get("id") or ""))
        if not pending:
            return 0

        for task in pending:
            task["publish_at"] = ""

        for task in pending:
            task["privacy_mode"] = settings.privacy_mode
            publish_at = self._next_publish_time(settings)
            task["publish_at"] = isoformat(publish_at) if publish_at else ""
            task["updated_at"] = isoformat(now_local())

        self.save()
        return len(pending)

    @_queue_transaction
    def due_tasks(self, moment: Optional[datetime] = None) -> List[Dict[str, Any]]:
        check_time = moment or now_local()
        due: List[Dict[str, Any]] = []
        for task in self._data.get("tasks", []):
            if task.get("status") != "queued":
                continue
            upload_after = parse_datetime(task.get("upload_after")) or now_local()
            if upload_after <= check_time:
                due.append(task)
        due.sort(key=lambda item: item.get("upload_after") or "")
        return due

    @_queue_transaction
    def claim_due_tasks(
        self,
        limit: int,
        moment: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Atomically reserve a bounded group of due tasks for upload."""
        check_time = moment or now_local()
        candidates: List[Dict[str, Any]] = []
        for task in self._data.get("tasks", []):
            if task.get("status") != "queued":
                continue
            upload_after = parse_datetime(task.get("upload_after")) or check_time
            if upload_after <= check_time:
                candidates.append(task)
        candidates.sort(key=lambda item: item.get("upload_after") or "")

        claimed: List[Dict[str, Any]] = []
        for task in candidates[:max(0, int(limit))]:
            task["status"] = "uploading"
            task["attempts"] = int(task.get("attempts") or 0) + 1
            task["error"] = ""
            task["error_code"] = ""
            task["updated_at"] = isoformat(now_local())
            claimed.append(dict(task))
        if claimed:
            self.save()
        return claimed

    @_queue_transaction
    def mark_status(self, task_id: str, status: str, **fields: Any) -> Optional[Dict[str, Any]]:
        for task in self._data.get("tasks", []):
            if task.get("id") == task_id:
                task.update(fields)
                task["status"] = status
                task["updated_at"] = isoformat(now_local())
                self.save()
                return task
        return None

    @_queue_transaction
    def clear_completed(self) -> int:
        tasks = self._data.get("tasks", [])
        kept = [task for task in tasks if task.get("status") not in FINAL_STATUSES]
        removed = len(tasks) - len(kept)
        self._data["tasks"] = kept
        if removed:
            self.save()
        return removed

    @_queue_transaction
    def remove_tasks(self, task_ids: Iterable[str]) -> int:
        task_set = set(task_ids)
        tasks = self._data.get("tasks", [])
        kept = [task for task in tasks if task.get("id") not in task_set]
        removed = len(tasks) - len(kept)
        self._data["tasks"] = kept
        if removed:
            self.save()
        return removed

    @_queue_transaction
    def retry_failed(self, settings: PublishSettings) -> int:
        failed_tasks = [task for task in self._data.get("tasks", []) if task.get("status") == "failed"]
        for task in failed_tasks:
            task["publish_at"] = ""
        retried = 0
        for task in failed_tasks:
            task["status"] = "queued"
            task["error"] = ""
            task["error_code"] = ""
            task["privacy_mode"] = settings.privacy_mode
            task["upload_after"] = isoformat(self._next_upload_time(settings))
            publish_at = self._next_publish_time(settings)
            task["publish_at"] = isoformat(publish_at) if publish_at else ""
            task["updated_at"] = isoformat(now_local())
            retried += 1
        if retried:
            self.save()
        return retried

    def _move_stale_publish_slot(
        self,
        task: Dict[str, Any],
        settings: Optional[PublishSettings],
        upload_after: datetime,
    ) -> None:
        if settings is None or settings.privacy_mode != "schedule":
            return
        publish_at = parse_datetime(task.get("publish_at"))
        earliest_publish = upload_after + timedelta(minutes=15)
        if publish_at is not None and publish_at > earliest_publish:
            return

        task["publish_at"] = ""
        replacement = self._next_publish_time(settings, not_before=earliest_publish)
        task["publish_at"] = isoformat(replacement) if replacement else ""

    @_queue_transaction
    def retry_transient_failures(
        self,
        max_attempts: int = 5,
        settings: Optional[PublishSettings] = None,
    ) -> int:
        """Return interrupted network uploads to the queue without changing publication slots."""
        retried = 0
        for task in self._data.get("tasks", []):
            if task.get("status") != "failed":
                continue
            attempts = int(task.get("attempts") or 0)
            error = task.get("error", "")
            upload_limit = is_upload_limit_error(error)
            quota_exhausted = is_quota_exhausted_error(error)
            allowed_attempts = max(10, max_attempts) if upload_limit or quota_exhausted else max_attempts
            if attempts >= allowed_attempts:
                continue
            if upload_limit:
                delay_seconds = youtube_upload_limit_retry_delay_seconds()
            elif quota_exhausted:
                delay_seconds = youtube_quota_retry_delay_seconds()
            elif is_transient_upload_error(error):
                delay_seconds = min(3600, 60 * (2 ** max(0, attempts - 1)))
            else:
                continue
            upload_after = now_local() + timedelta(seconds=delay_seconds)
            task["status"] = "queued"
            task["upload_after"] = isoformat(upload_after)
            if upload_limit or quota_exhausted:
                self._move_stale_publish_slot(task, settings, upload_after)
            reason = "лимита YouTube" if upload_limit else "квоты API" if quota_exhausted else "временной ошибки"
            task["error"] = f"Автоповтор после {reason}: {error}"
            task["error_code"] = YOUTUBE_ERROR_RETRY_SCHEDULED
            task["updated_at"] = isoformat(now_local())
            retried += 1
        if retried:
            self.save()
        return retried

    @_queue_transaction
    def retry_transient_failure(
        self,
        task_id: str,
        max_attempts: int = 5,
        settings: Optional[PublishSettings] = None,
    ) -> bool:
        """Retry one failed task without touching failures from other workers."""
        for task in self._data.get("tasks", []):
            if task.get("id") != task_id or task.get("status") != "failed":
                continue
            attempts = int(task.get("attempts") or 0)
            error = task.get("error", "")
            upload_limit = is_upload_limit_error(error)
            quota_exhausted = is_quota_exhausted_error(error)
            allowed_attempts = max(10, max_attempts) if upload_limit or quota_exhausted else max_attempts
            if attempts >= allowed_attempts:
                return False
            if upload_limit:
                delay_seconds = youtube_upload_limit_retry_delay_seconds()
            elif quota_exhausted:
                delay_seconds = youtube_quota_retry_delay_seconds()
            elif is_transient_upload_error(error):
                delay_seconds = min(3600, 60 * (2 ** max(0, attempts - 1)))
            else:
                return False
            upload_after = now_local() + timedelta(seconds=delay_seconds)
            task["status"] = "queued"
            task["upload_after"] = isoformat(upload_after)
            if upload_limit or quota_exhausted:
                self._move_stale_publish_slot(task, settings, upload_after)
            if upload_limit:
                retry_message = "Автоповтор после сброса лимита канала"
            elif quota_exhausted:
                retry_message = "Автоповтор после сброса квоты API YouTube"
            else:
                retry_message = "Автоповтор после временной ошибки"
            task["error"] = f"{retry_message}: {error}"
            task["error_code"] = YOUTUBE_ERROR_RETRY_SCHEDULED
            task["updated_at"] = isoformat(now_local())
            self.save()
            return True
        return False


def is_transient_upload_error(error: Any) -> bool:
    text = str(error or "").lower()
    markers = (
        "getaddrinfo failed",
        "name resolution",
        "timed out",
        "timeout",
        "connection reset",
        "connection aborted",
        "connection refused",
        "remote disconnected",
        "temporarily unavailable",
        "temporary failure",
        "network is unreachable",
        "ssl error",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "ratelimitexceeded",
        "userratelimitexceeded",
        "backenderror",
        "internalerror",
    )
    return any(marker in text for marker in markers)


def is_upload_limit_error(error: Any) -> bool:
    text = str(error or "").lower()
    return "uploadlimitexceeded" in text or "exceeded the number of videos" in text


def is_quota_exhausted_error(error: Any) -> bool:
    text = str(error or "").lower()
    if "quotaexceeded" in text or "dailylimitexceeded" in text:
        return True
    # The Videos endpoint reports its per-day quota as rateLimitExceeded rather
    # than quotaExceeded. Keep other rate-limit responses transient, but defer
    # this specific daily limit until the Pacific quota reset.
    return "ratelimitexceeded" in text and (
        "video uploads" in text or "uploads per day" in text
    )


def classify_oauth_error(error: Any) -> str:
    """Return a stable OAuth error code without choosing a display language."""
    explicit = str(getattr(error, "code", "") or "")
    if explicit in {
        OAUTH_ERROR_ACCESS_DENIED,
        OAUTH_ERROR_REDIRECT_MISMATCH,
        OAUTH_ERROR_INVALID_CLIENT,
        OAUTH_ERROR_INVALID_SCOPE,
    }:
        return explicit

    raw = str(error or "").strip()
    text = raw.casefold()
    if "access_denied" in text or (
        "403" in text and ("oauth" in text or "verification" in text or "провер" in text)
    ):
        return OAUTH_ERROR_ACCESS_DENIED
    if "redirect_uri_mismatch" in text:
        return OAUTH_ERROR_REDIRECT_MISMATCH
    if "invalid_client" in text or "unauthorized_client" in text:
        return OAUTH_ERROR_INVALID_CLIENT
    if "scope" in text and ("invalid" in text or "insufficient" in text):
        return OAUTH_ERROR_INVALID_SCOPE
    return OAUTH_ERROR_UNKNOWN


def classify_youtube_core_error(error: Any) -> str:
    """Classify application errors while leaving raw provider details intact."""
    explicit = str(getattr(error, "code", "") or "")
    if explicit:
        return explicit

    oauth_code = classify_oauth_error(error)
    if oauth_code != OAUTH_ERROR_UNKNOWN:
        return oauth_code

    text = str(error or "").casefold()
    if "google-auth-oauthlib" in text or (
        "google oauth/youtube client libraries" in text and "unavailable" in text
    ):
        return YOUTUBE_ERROR_DEPENDENCY_MISSING
    if "youtube" in text and (
        "не сохранён" in text
        or "не сохранен" in text
        or "authorization is missing or expired" in text
    ):
        return YOUTUBE_ERROR_AUTH_REQUIRED
    if "oauth" in text and (
        "не настроен" in text
        or "not configured" in text
        or "client_id/client_secret" in text
    ):
        return YOUTUBE_ERROR_OAUTH_CONFIG_REQUIRED
    if "канал не найден" in text or "channel not found" in text:
        return YOUTUBE_ERROR_CHANNEL_NOT_FOUND
    if "видео не найдено" in text or "video not found" in text:
        return YOUTUBE_ERROR_VIDEO_NOT_FOUND
    if "upload stopped" in text or "загрузка остановлена" in text:
        return YOUTUBE_ERROR_UPLOAD_CANCELLED
    return ""


def friendly_oauth_error(error: Any) -> str:
    """Return the legacy Russian OAuth explanation used by the core API.

    GUI code should use its locale-aware adapter and ``classify_oauth_error``.
    """
    raw = str(error or "").strip()
    code = classify_oauth_error(error)
    if code == OAUTH_ERROR_ACCESS_DENIED:
        return (
            "Google заблокировал вход: OAuth-приложение ContentBot Pro находится "
            "в тестовом режиме, а этот аккаунт не добавлен в Test users. "
            "В Google Cloud Console добавьте аккаунт в Audience -> Test users "
            "либо опубликуйте приложение в Production."
        )
    if code == OAUTH_ERROR_REDIRECT_MISMATCH:
        return (
            "Google отклонил адрес возврата OAuth. Для клиента ContentBot Pro "
            "нужно использовать тип Desktop app с localhost redirect URI."
        )
    if code == OAUTH_ERROR_INVALID_CLIENT:
        return (
            "OAuth-клиент ContentBot Pro недействителен. Пересоздайте его в Google Cloud "
            "как Desktop app и обновите youtube_oauth_client.json в сборке."
        )
    if code == OAUTH_ERROR_INVALID_SCOPE:
        return (
            "Google не выдал необходимые разрешения YouTube. Проверьте OAuth scopes "
            "и повторите вход через браузер."
        )
    return raw or "Неизвестная ошибка входа Google"


# Developer OAuth client. Desktop clients cannot keep a secret, but Google still
# requires app credentials to identify the application during browser consent.
BUILTIN_CLIENT_CONFIG = {
    "installed": {
        "client_id": "YOUR_BUILTIN_CLIENT_ID.apps.googleusercontent.com",
        "client_secret": "YOUR_BUILTIN_CLIENT_SECRET",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "redirect_uris": ["http://localhost"]
    }
}


def _installed_oauth_config(client_id: str, client_secret: str) -> Dict[str, Any]:
    return {
        "installed": {
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost", "http://127.0.0.1"],
        }
    }


def _oauth_section(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for key in ("installed", "web"):
        section = config.get(key)
        if isinstance(section, dict):
            return section
    return None


def normalize_oauth_client_config(data: Any) -> Optional[Dict[str, Any]]:
    """Accept Google client JSON or a small {client_id, client_secret} file."""
    if not isinstance(data, dict):
        return None

    section = _oauth_section(data)
    if section:
        client_id = str(section.get("client_id") or "").strip()
        client_secret = str(section.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            return None
        section.setdefault("auth_uri", "https://accounts.google.com/o/oauth2/auth")
        section.setdefault("token_uri", "https://oauth2.googleapis.com/token")
        section.setdefault("auth_provider_x509_cert_url", "https://www.googleapis.com/oauth2/v1/certs")
        section.setdefault("redirect_uris", ["http://localhost", "http://127.0.0.1"])
        return data

    client_id = str(data.get("client_id") or data.get("oauth_client_id") or "").strip()
    client_secret = str(data.get("client_secret") or data.get("oauth_client_secret") or "").strip()
    if client_id and client_secret:
        return _installed_oauth_config(client_id, client_secret)
    return None


def load_oauth_client_config_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return normalize_oauth_client_config(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _read_dotenv_value(name: str) -> str:
    for env_path in (Path(".env"), Path("youtube_cache") / ".env"):
        if not env_path.is_file():
            continue
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            if key.strip() != name:
                continue
            return value.strip().strip('"').strip("'")
    return ""


def _oauth_env_value(name: str) -> str:
    return os.getenv(name, "").strip() or _read_dotenv_value(name)


def _looks_like_oauth_token(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    return bool(data.get("refresh_token") or data.get("token"))


def is_builtin_config_valid() -> bool:
    config = normalize_oauth_client_config(BUILTIN_CLIENT_CONFIG)
    if not config:
        return False
    section = _oauth_section(config) or {}
    client_id = str(section.get("client_id") or "")
    client_secret = str(section.get("client_secret") or "")
    return bool(
        client_id
        and client_secret
        and not client_id.startswith("YOUR_BUILTIN_CLIENT_ID")
        and not client_secret.startswith("YOUR_BUILTIN_CLIENT_SECRET")
    )


def default_oauth_client_paths() -> List[Path]:
    paths: List[Path] = []
    seen = set()

    def add(path: Path) -> None:
        text = str(path)
        if text not in seen:
            seen.add(text)
            paths.append(path)

    for path in DEFAULT_OAUTH_CLIENT_PATHS:
        add(path)

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        for path in DEFAULT_OAUTH_CLIENT_PATHS:
            add(exe_dir / path)

    bundle_dir = getattr(sys, "_MEIPASS", "")
    if bundle_dir:
        root = Path(bundle_dir)
        for path in DEFAULT_OAUTH_CLIENT_PATHS:
            add(root / path)

    repo_root = Path(__file__).resolve().parents[1]
    for path in DEFAULT_OAUTH_CLIENT_PATHS:
        add(repo_root / path)

    return paths


def resolve_oauth_client_source(
    settings: Optional[PublishSettings] = None,
    include_token: bool = True,
) -> OAuthClientSource:
    settings = settings or PublishSettings()
    token_path = Path(settings.token_path or DEFAULT_TOKEN_PATH)
    saved_token_path = next(
        (
            candidate
            for candidate in (token_path, _backup_path(token_path))
            if candidate.is_file() and _looks_like_oauth_token(candidate)
        ),
        None,
    )
    if include_token and saved_token_path:
        return OAuthClientSource(
            source="token",
            label="Сохраненный вход",
            ready=True,
            path=str(saved_token_path),
            has_token=True,
            message=(
                "YouTube уже авторизован. При необходимости токен обновится автоматически."
                if saved_token_path == token_path
                else "YouTube-вход восстановлен из резервной копии."
            ),
        )

    manual_path = Path(settings.client_secrets_path) if settings.client_secrets_path else None
    if manual_path and manual_path.is_file():
        config = load_oauth_client_config_file(manual_path)
        if config:
            return OAuthClientSource(
                source="file",
                label="OAuth-файл",
                ready=True,
                path=str(manual_path),
                config=config,
                message="Нажмите вход, браузер откроется автоматически.",
            )
        return OAuthClientSource(
            source="invalid_file",
            label="OAuth-файл не читается",
            ready=False,
            path=str(manual_path),
            message="Файл OAuth найден, но в нем нет client_id/client_secret.",
        )

    env_client_id = _oauth_env_value("YOUTUBE_OAUTH_CLIENT_ID")
    env_client_secret = _oauth_env_value("YOUTUBE_OAUTH_CLIENT_SECRET")
    if env_client_id and env_client_secret:
        return OAuthClientSource(
            source="env",
            label="OAuth из окружения",
            ready=True,
            config=_installed_oauth_config(env_client_id, env_client_secret),
            message="Нажмите вход, браузер откроется автоматически.",
        )

    for path in default_oauth_client_paths():
        if path.is_file():
            config = load_oauth_client_config_file(path)
            if config:
                return OAuthClientSource(
                    source="auto_file",
                    label="OAuth найден автоматически",
                    ready=True,
                    path=str(path),
                    config=config,
                    message="Нажмите вход, браузер откроется автоматически.",
                )

    if is_builtin_config_valid():
        return OAuthClientSource(
            source="builtin",
            label="Встроенный OAuth-клиент",
            ready=True,
            config=normalize_oauth_client_config(BUILTIN_CLIENT_CONFIG),
            message="Нажмите вход, браузер откроется автоматически.",
        )

    return OAuthClientSource(
        source="missing",
        label="OAuth не настроен",
        ready=False,
        message=(
            "Нужен OAuth-клиент приложения: встроенный в сборку, переменные окружения "
            "YOUTUBE_OAUTH_CLIENT_ID/YOUTUBE_OAUTH_CLIENT_SECRET или файл youtube_oauth_client.json."
        ),
    )


def has_oauth_material(settings: Optional[PublishSettings] = None) -> bool:
    return resolve_oauth_client_source(settings).ready


def has_saved_youtube_token(settings: Optional[PublishSettings] = None) -> bool:
    return resolve_oauth_client_source(settings).has_token


class UploadCancelled(RuntimeError):
    """Raised between resumable chunks when the in-app worker is stopped."""

    code = YOUTUBE_ERROR_UPLOAD_CANCELLED


class YouTubeAuthRequired(RuntimeError):
    """Raised when background upload cannot continue without browser login."""

    code = YOUTUBE_ERROR_AUTH_REQUIRED


class YouTubeDependencyError(RuntimeError):
    """Raised when optional Google upload dependencies are unavailable."""

    code = YOUTUBE_ERROR_DEPENDENCY_MISSING


class YouTubeOAuthConfigRequired(FileNotFoundError):
    """Raised when an interactive login has no usable OAuth client config."""

    code = YOUTUBE_ERROR_OAUTH_CONFIG_REQUIRED


class YouTubeVideoNotFound(FileNotFoundError):
    """Raised when a queued upload no longer has its source video."""

    code = YOUTUBE_ERROR_VIDEO_NOT_FOUND


class YouTubeUploader:
    """Small wrapper over YouTube Data API v3 uploads."""

    def __init__(
        self,
        settings: PublishSettings,
        oauth_browser_messages: Optional[Mapping[str, str]] = None,
    ):
        self.settings = settings
        self.service = None
        self.oauth_browser_messages = dict(oauth_browser_messages or {})

    def _load_google_modules(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError as error:
            raise YouTubeDependencyError(
                "Не хватает пакета google-auth-oauthlib. Установите зависимости из requirements.txt."
            ) from error
        return Request, Credentials, InstalledAppFlow, build, MediaFileUpload

    def get_credentials(
        self,
        force_account_selection: bool = False,
        allow_interactive: bool = True,
    ):
        with _OAUTH_TOKEN_LOCK:
            return self._get_credentials_unlocked(force_account_selection, allow_interactive)

    def _get_credentials_unlocked(
        self,
        force_account_selection: bool = False,
        allow_interactive: bool = True,
    ):
        Request, Credentials, InstalledAppFlow, _, _ = self._load_google_modules()
        token_path = Path(self.settings.token_path or DEFAULT_TOKEN_PATH)
        credentials = None
        loaded_token_path = None

        for candidate in (() if force_account_selection else (token_path, _backup_path(token_path))):
            if not candidate.exists():
                continue
            try:
                credentials = Credentials.from_authorized_user_file(str(candidate), SCOPES)
                if not credentials.has_scopes(SCOPES):
                    credentials = None
                if credentials:
                    loaded_token_path = candidate
                    break
            except Exception:
                credentials = None

        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except Exception:
                credentials = None

        if (not credentials or not credentials.valid) and not allow_interactive:
            raise YouTubeAuthRequired(
                "Вход в YouTube не сохранён или устарел. Подключите канал через браузер."
            )

        if not credentials or not credentials.valid:
            oauth_source = resolve_oauth_client_source(self.settings, include_token=False)
            if not oauth_source.ready or not oauth_source.config:
                raise YouTubeOAuthConfigRequired(
                    "OAuth для YouTube не настроен. Добавьте youtube_oauth_client.json в папку проекта "
                    "или настройте OAuth-клиент приложения в сборке."
                )
            flow = InstalledAppFlow.from_client_config(oauth_source.config, SCOPES)
            credentials = flow.run_local_server(
                host="127.0.0.1",
                port=0,
                open_browser=True,
                prompt="select_account consent" if force_account_selection else "consent",
                access_type="offline",
                authorization_prompt_message=self.oauth_browser_messages.get(
                    "authorization_prompt_message",
                    "Открываю браузер для входа в YouTube.",
                ),
                success_message=self.oauth_browser_messages.get(
                    "success_message",
                    "Готово. YouTube подключён, эту вкладку можно закрыть.",
                ),
            )

        token_data = json.loads(credentials.to_json())
        _atomic_write_json_with_backup(
            token_path,
            token_data,
            backup_existing=loaded_token_path == token_path,
        )
        # A fresh OAuth login must also be recoverable if the primary token is
        # interrupted or removed before the next application start.
        shutil.copy2(token_path, _backup_path(token_path))
        return credentials

    def build_service(
        self,
        force_account_selection: bool = False,
        allow_interactive: bool = True,
    ):
        if self.service is None or force_account_selection:
            _, _, _, build, _ = self._load_google_modules()
            credentials = self.get_credentials(
                force_account_selection=force_account_selection,
                allow_interactive=allow_interactive,
            )
            self.service = build("youtube", "v3", credentials=credentials)
        return self.service

    def channel_info(
        self,
        force_account_selection: bool = False,
        allow_interactive: bool = True,
    ) -> Dict[str, Any]:
        service = self.build_service(
            force_account_selection=force_account_selection,
            allow_interactive=allow_interactive,
        )
        response = service.channels().list(part="snippet,id,status", mine=True).execute()
        items = response.get("items") or []
        if not items:
            return {
                "connected": False,
                "channel_title": "",
                "channel_id": "",
                "error": "Канал не найден",
                "error_code": YOUTUBE_ERROR_CHANNEL_NOT_FOUND,
            }
        channel = items[0]
        snippet = channel.get("snippet") or {}
        return {
            "connected": True,
            "channel_title": snippet.get("title", ""),
            "channel_id": channel.get("id", ""),
            "description": snippet.get("description", ""),
            "privacy_status": (channel.get("status") or {}).get("privacyStatus", ""),
        }

    def upload_task(
        self,
        task: Dict[str, Any],
        progress_callback: Optional[Callable[[int], None]] = None,
        stop_callback: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        if not Path(str(task.get("video_path", ""))).exists():
            raise YouTubeVideoNotFound(f"Видео не найдено: {task.get('video_path')}")

        _, _, _, _, MediaFileUpload = self._load_google_modules()
        service = self.build_service(allow_interactive=False)
        body = self._body_for_task(task)
        media = MediaFileUpload(str(task["video_path"]), chunksize=8 * 1024 * 1024, resumable=True)
        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
            notifySubscribers=bool(self.settings.notify_subscribers),
        )

        response = None
        while response is None:
            if stop_callback and stop_callback():
                raise UploadCancelled("Upload stopped by user")
            # Queue-level retries use an exponential delay and can stop at the
            # daily quota reset. Keeping this low avoids each worker making five
            # immediate duplicate requests after Google has already rejected it.
            status, response = request.next_chunk(num_retries=1)
            if status and progress_callback:
                progress_callback(int(status.progress() * 100))

        video_id = response.get("id", "")
        return {
            "youtube_video_id": video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
            "raw_response": response,
            "scheduled": bool(task.get("publish_at")),
        }

    def _body_for_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        publish_at = parse_datetime(task.get("publish_at"))
        privacy_mode = task.get("privacy_mode") or self.settings.privacy_mode
        privacy_status = "private" if privacy_mode in {"schedule", "private"} else "public"
        language_code = LANGUAGE_CODES.get(str(task.get("language") or ""), None)
        tags = list(task.get("tags") or tags_from_hashtags(task.get("hashtags") or []))

        snippet: Dict[str, Any] = {
            "title": clean_title(task.get("title"), Path(str(task.get("video_path"))).stem),
            "description": clean_description(task.get("description")),
            "tags": tags,
            "categoryId": str(self.settings.category_id or "24"),
        }
        if language_code:
            snippet["defaultLanguage"] = language_code
            snippet["defaultAudioLanguage"] = language_code

        status: Dict[str, Any] = {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": bool(self.settings.made_for_kids),
            "containsSyntheticMedia": bool(self.settings.contains_synthetic_media),
        }
        if privacy_mode == "schedule" and publish_at:
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

        return {"snippet": snippet, "status": status}


def upload_due_batch(
    settings: PublishSettings,
    queue: YouTubePublishQueue,
    *,
    stop_requested: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[Callable[[str, int], None]] = None,
    queue_changed_callback: Optional[Callable[[], None]] = None,
    uploader_factory: Optional[Callable[[PublishSettings], Any]] = None,
    account_store: Optional["YouTubeAccountStore"] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    event_callback: Optional[YouTubeEventCallback] = None,
) -> List[Dict[str, Any]]:
    """Claim and upload one due wave without switching the selected channel.

    Quota and upload-limit errors are deferred until the next daily YouTube
    quota reset so a queued video is not silently published to another saved
    account.
    """
    should_stop = stop_requested or (lambda: False)
    if should_stop():
        return []

    parallelism = max(1, min(MAX_PARALLEL_UPLOADS, int(settings.max_parallel_uploads or 1)))
    tasks = queue.claim_due_tasks(parallelism)
    if not tasks:
        return []
    if queue_changed_callback:
        queue_changed_callback()

    make_uploader = uploader_factory or YouTubeUploader
    def _diagnostic(event: str, details: Dict[str, Any], legacy_message: str) -> None:
        if event_callback:
            event_callback(event, details)
        if log_callback:
            log_callback(legacy_message)

    def _active_account_id(s: PublishSettings) -> str:
        if s.active_account_id:
            return s.active_account_id
        if account_store:
            for acc in account_store.accounts():
                if str(acc.get("token_path")) == str(s.token_path):
                    return str(acc.get("id", ""))
        return ""

    def upload_one(task: Dict[str, Any]) -> Dict[str, Any]:
        task_id = str(task.get("id") or "")
        title = str(task.get("title") or Path(str(task.get("video_path") or "")).name)
        if should_stop():
            queue.mark_status(
                task_id,
                "queued",
                upload_after=isoformat(now_local()),
                error="Upload stopped before transfer started",
                error_code=YOUTUBE_ERROR_UPLOAD_CANCELLED,
            )
            return {
                "task_id": task_id,
                "title": title,
                "status": "cancelled",
                "error_code": YOUTUBE_ERROR_UPLOAD_CANCELLED,
            }

        acct_id = _active_account_id(settings)

        try:
            uploader = make_uploader(settings)
            result = uploader.upload_task(
                task,
                progress_callback=(
                    (lambda percent: progress_callback(task_id, percent))
                    if progress_callback else None
                ),
                stop_callback=should_stop,
            )
            if account_store and acct_id:
                try:
                    account_store.clear_quota_exhausted(acct_id)
                except Exception:
                    pass
            final_status = "scheduled" if task.get("publish_at") else "uploaded"
            queue.mark_status(
                task_id,
                final_status,
                youtube_video_id=result.get("youtube_video_id", ""),
                youtube_url=result.get("youtube_url", ""),
                error="",
                error_code="",
            )
            return {
                "task_id": task_id,
                "title": title,
                "status": final_status,
                **result,
            }

        except UploadCancelled as error:
            queue.mark_status(
                task_id,
                "queued",
                upload_after=isoformat(now_local()),
                error=str(error),
                error_code=YOUTUBE_ERROR_UPLOAD_CANCELLED,
            )
            return {
                "task_id": task_id,
                "title": title,
                "status": "cancelled",
                "error": str(error),
                "error_code": YOUTUBE_ERROR_UPLOAD_CANCELLED,
            }

        except Exception as error:
            error_str = str(error)
            limit_hit = is_upload_limit_error(error) or is_quota_exhausted_error(error)
            error_code = classify_youtube_core_error(error)
            queue.mark_status(task_id, "failed", error=error_str, error_code=error_code)
            retrying = queue.retry_transient_failure(task_id, settings=settings)
            if limit_hit and retrying:
                queued_task = next(
                    (item for item in queue.tasks() if item.get("id") == task_id),
                    {},
                )
                retry_at = parse_datetime(queued_task.get("upload_after"))
                if retry_at is not None:
                    if account_store and acct_id:
                        try:
                            account_store.mark_quota_exhausted(acct_id, until=retry_at)
                        except Exception:
                            pass
                    event = (
                        YOUTUBE_EVENT_UPLOAD_LIMIT_RETRY
                        if is_upload_limit_error(error)
                        else YOUTUBE_EVENT_QUOTA_RETRY
                    )
                    reason = (
                        "channel's rolling 24-hour upload limit"
                        if is_upload_limit_error(error)
                        else "API quota reset"
                    )
                    _diagnostic(
                        event,
                        {"time": retry_at.strftime("%Y-%m-%d %H:%M")},
                        f"YouTube limit reached. Retry after {reason}: "
                        f"{retry_at:%Y-%m-%d %H:%M}.",
                    )
            return {
                "task_id": task_id,
                "title": title,
                "status": "retrying" if retrying else "failed",
                "error": error_str,
                "error_code": (
                    YOUTUBE_ERROR_RETRY_SCHEDULED if retrying else error_code
                ),
            }
        finally:
            if queue_changed_callback:
                queue_changed_callback()

    outcomes: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(
        max_workers=min(parallelism, len(tasks)),
        thread_name_prefix="youtube-upload",
    ) as executor:
        futures = [executor.submit(upload_one, task) for task in tasks]
        for future in as_completed(futures):
            outcomes.append(future.result())
    return outcomes
