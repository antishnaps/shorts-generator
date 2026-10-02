#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Keyless Wikimedia Commons video search restricted to CC0/public domain."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

import requests


_WIKIMEDIA_DOWNLOAD_LOCK = threading.Lock()
_WIKIMEDIA_COOLDOWN_LOCK = threading.RLock()
_WIKIMEDIA_COOLDOWN_UNTIL = 0.0
_WIKIMEDIA_NEXT_DOWNLOAD_AT = 0.0


def _reset_wikimedia_rate_limit_state() -> None:
    """Reset process-wide download throttling (used by isolated tests)."""
    global _WIKIMEDIA_COOLDOWN_UNTIL, _WIKIMEDIA_NEXT_DOWNLOAD_AT
    with _WIKIMEDIA_COOLDOWN_LOCK:
        _WIKIMEDIA_COOLDOWN_UNTIL = 0.0
        _WIKIMEDIA_NEXT_DOWNLOAD_AT = 0.0


def _wikimedia_retry_after_seconds(response) -> int:
    """Return a bounded cooldown for HTTP 429 responses."""
    raw_value = str(getattr(response, "headers", {}).get("Retry-After") or "").strip()
    try:
        seconds = int(float(raw_value))
    except (TypeError, ValueError):
        seconds = 15 * 60
    return max(60, min(60 * 60, seconds))


class WikimediaCommonsClient:
    """Search and download reusable Commons videos without an API key.

    Commons contains several attribution and share-alike licences.  ContentBot
    deliberately accepts only files whose API metadata explicitly says CC0 or
    public domain so a generated short does not silently acquire attribution or
    share-alike obligations.
    """

    API_URL = "https://commons.wikimedia.org/w/api.php"
    USER_AGENT = "ContentBotPro/1.0 (Wikimedia Commons video client)"
    CACHE_TTL_SECONDS = 24 * 60 * 60
    MAX_DOWNLOAD_BYTES = 120 * 1024 * 1024
    ALLOWED_MIME_TYPES = {
        "video/webm",
        "video/ogg",
        "application/ogg",
        "video/mp4",
    }
    _cache_lock = threading.RLock()

    def __init__(
        self,
        log_callback: Callable = None,
        cache_dir: Path | str = None,
        cache_ttl_seconds: int = CACHE_TTL_SECONDS,
        max_download_bytes: int = MAX_DOWNLOAD_BYTES,
        session: requests.Session = None,
    ):
        self.log = log_callback or (lambda _message: None)
        self.cache_dir = Path(cache_dir or Path("cache") / "wikimedia_search")
        self.cache_ttl_seconds = max(60, int(cache_ttl_seconds))
        self.max_download_bytes = max(1024 * 1024, int(max_download_bytes))
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})

    @staticmethod
    def _clean_query(query: str) -> str:
        value = re.sub(r"[^\w\s'\-]", " ", str(query or ""), flags=re.UNICODE)
        return re.sub(r"\s+", " ", value).strip()[:160]

    @staticmethod
    def _metadata_value(metadata: Dict, key: str) -> str:
        item = metadata.get(key) or {}
        return str(item.get("value") or "").strip()

    @classmethod
    def has_safe_license(cls, image_info: Dict) -> bool:
        metadata = image_info.get("extmetadata") or {}
        short_name = cls._metadata_value(metadata, "LicenseShortName").lower()
        usage_terms = cls._metadata_value(metadata, "UsageTerms").lower()
        license_url = cls._metadata_value(metadata, "LicenseUrl").lower()
        combined = f"{short_name} {usage_terms}"
        return bool(
            "cc0" in combined
            or "cc zero" in combined
            or "public domain" in combined
            or "/publicdomain/zero/" in license_url
            or "/publicdomain/mark/" in license_url
        )

    def _cache_path(self, query: str, per_page: int, min_height: int) -> Path:
        payload = json.dumps(
            {
                "query": query,
                "per_page": per_page,
                "min_height": min_height,
                "max_download_bytes": self.max_download_bytes,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path) -> Optional[List[Dict]]:
        try:
            if not path.is_file() or time.time() - path.stat().st_mtime > self.cache_ttl_seconds:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            results = payload.get("results")
            return results if isinstance(results, list) else None
        except (OSError, ValueError, TypeError):
            return None

    def _write_cache(self, path: Path, results: List[Dict]) -> None:
        try:
            with self._cache_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps({"saved_at": time.time(), "results": results}, ensure_ascii=False),
                    encoding="utf-8",
                )
                temporary.replace(path)
        except OSError:
            # Search still succeeded; a read-only cache directory must not stop generation.
            return

    def _request_json(self, params: Dict) -> Optional[Dict]:
        for attempt in range(3):
            try:
                response = self.session.get(self.API_URL, params=params, timeout=30)
                response.raise_for_status()
                payload = response.json()
                if payload.get("error"):
                    raise requests.RequestException(str(payload["error"]))
                return payload
            except (requests.RequestException, ValueError, TypeError) as exc:
                if attempt >= 2:
                    self.log(f"Wikimedia Commons: ошибка поиска: {exc}")
                    return None
                time.sleep(min(2 ** attempt, 2))
        return None

    def search_videos(
        self,
        query: str,
        per_page: int = 15,
        min_height: int = 720,
        target_orientation: str = "vertical",
    ) -> List[Dict]:
        clean_query = self._clean_query(query)
        if not clean_query:
            return []
        per_page = max(1, min(30, int(per_page)))
        min_height = max(480, int(min_height))
        cache_path = self._cache_path(clean_query, per_page, min_height)
        cached = self._read_cache(cache_path)
        if cached is not None:
            self.log(f"Wikimedia Commons: результаты из кэша для '{clean_query}'")
            return cached

        search_limit = min(50, max(20, per_page * 4))
        params = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "generator": "search",
            "gsrsearch": f"{clean_query} filetype:video -nude -nudity -pornographic -genitals",
            "gsrnamespace": 6,
            "gsrlimit": search_limit,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|mediatype|extmetadata",
            "iiextmetadatafilter": "LicenseShortName|UsageTerms|LicenseUrl",
            "maxlag": 5,
        }
        self.log(f"Wikimedia Commons: поиск CC0/Public Domain видео '{clean_query}'...")
        payload = self._request_json(params)
        if not payload:
            return []

        results = []
        pages = payload.get("query", {}).get("pages", [])
        for page in sorted(pages, key=lambda item: int(item.get("index") or 10**9)):
            image_info = next(iter(page.get("imageinfo") or []), None)
            if not image_info:
                continue
            mime = str(image_info.get("mime") or "").lower()
            if (
                str(image_info.get("mediatype") or "").upper() != "VIDEO"
                or mime not in self.ALLOWED_MIME_TYPES
                or int(image_info.get("height") or 0) < min_height
                or int(image_info.get("size") or 0) <= 0
                or int(image_info.get("size") or 0) > self.max_download_bytes
                or not image_info.get("url")
                or not self.has_safe_license(image_info)
            ):
                continue
            metadata = image_info.get("extmetadata") or {}
            results.append(
                {
                    "id": int(page.get("pageid") or 0),
                    "title": str(page.get("title") or ""),
                    "url": str(image_info["url"]),
                    "page_url": str(image_info.get("descriptionurl") or ""),
                    "width": int(image_info.get("width") or 0),
                    "height": int(image_info.get("height") or 0),
                    "duration": float(image_info.get("duration") or 0.0),
                    "size": int(image_info.get("size") or 0),
                    "mime": mime,
                    "license": self._metadata_value(metadata, "LicenseShortName"),
                    "license_url": self._metadata_value(metadata, "LicenseUrl"),
                }
            )

        want_vertical = target_orientation == "vertical"
        results.sort(
            key=lambda item: (
                (item["height"] >= item["width"]) != want_vertical,
                -min(item["width"], item["height"]),
            )
        )
        results = results[:per_page]
        self._write_cache(cache_path, results)
        self.log(f"Wikimedia Commons: найдено {len(results)} безопасных HD-видео")
        return results

    @staticmethod
    def output_path_for(video: Dict, output_dir: Path) -> Path:
        parsed = urlparse(str(video.get("url") or ""))
        suffix = Path(parsed.path).suffix.lower()
        if suffix not in {".webm", ".ogv", ".ogg", ".mp4"}:
            suffix = ".webm"
        identity = str(video.get("id") or "unknown")
        return Path(output_dir) / f"wikimedia_video_{identity}{suffix}"

    def download_video(self, video: Dict, output_dir: Path) -> Optional[str]:
        url = str(video.get("url") or "")
        if not url or not self.has_safe_license(
            {
                "extmetadata": {
                    "LicenseShortName": {"value": video.get("license") or ""},
                    "LicenseUrl": {"value": video.get("license_url") or ""},
                }
            }
        ):
            return None
        declared_size = int(video.get("size") or 0)
        if declared_size <= 0 or declared_size > self.max_download_bytes:
            return None

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_path_for(video, output_dir)
        if output_path.is_file() and 0 < output_path.stat().st_size <= self.max_download_bytes:
            return str(output_path)

        temporary = output_path.with_suffix(output_path.suffix + ".part")
        try:
            # Commons explicitly asks automated clients to avoid bursts. A
            # process-wide gate prevents parallel video workers from hammering
            # upload.wikimedia.org and turns the first 429 into one bounded
            # cooldown instead of dozens of guaranteed failures.
            global _WIKIMEDIA_COOLDOWN_UNTIL, _WIKIMEDIA_NEXT_DOWNLOAD_AT
            with _WIKIMEDIA_DOWNLOAD_LOCK:
                with _WIKIMEDIA_COOLDOWN_LOCK:
                    now = time.monotonic()
                    if now < _WIKIMEDIA_COOLDOWN_UNTIL:
                        remaining = max(1, int(_WIKIMEDIA_COOLDOWN_UNTIL - now))
                        self.log(
                            f"Wikimedia Commons: загрузки на паузе после 429 "
                            f"(ещё ~{remaining}с)"
                        )
                        return None
                    wait_seconds = max(0.0, _WIKIMEDIA_NEXT_DOWNLOAD_AT - now)
                if wait_seconds:
                    time.sleep(min(wait_seconds, 1.0))

                response = self.session.get(url, timeout=120, stream=True)
                with _WIKIMEDIA_COOLDOWN_LOCK:
                    _WIKIMEDIA_NEXT_DOWNLOAD_AT = time.monotonic() + 0.75
                if int(getattr(response, "status_code", 0) or 0) == 429:
                    retry_after = _wikimedia_retry_after_seconds(response)
                    with _WIKIMEDIA_COOLDOWN_LOCK:
                        _WIKIMEDIA_COOLDOWN_UNTIL = time.monotonic() + retry_after
                    self.log(
                        "Wikimedia Commons: лимит запросов 429; "
                        f"останавливаю новые загрузки на {retry_after}с"
                    )
                    return None

                response.raise_for_status()
                content_length = int(response.headers.get("Content-Length") or 0)
                if content_length > self.max_download_bytes:
                    return None
                downloaded = 0
                with open(temporary, "wb") as output:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > self.max_download_bytes:
                            raise ValueError("файл превышает безопасный лимит размера")
                        output.write(chunk)
            if downloaded <= 0:
                raise ValueError("получен пустой файл")
            temporary.replace(output_path)
            self.log(f"Wikimedia Commons: скачано {output_path.name}")
            return str(output_path)
        except (OSError, ValueError, requests.RequestException) as exc:
            self.log(f"Wikimedia Commons: ошибка скачивания: {exc}")
            temporary.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            return None
