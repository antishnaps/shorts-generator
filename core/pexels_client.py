#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Minimal Pexels video API client with local download caching."""

import os
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests


class PexelsClient:
    SEARCH_URL = "https://api.pexels.com/videos/search"

    def __init__(self, api_key: str = None, log_callback: Callable = None):
        self.api_key = api_key or os.environ.get("PEXELS_API_KEY", "")
        self.log = log_callback or (lambda _message: None)
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({"Authorization": self.api_key})
        self.session.headers.update({"User-Agent": "ContentBotPro/1.0"})

    def search_videos(self, query: str, per_page: int = 15, orientation: str = "portrait", size: str = "medium") -> List[Dict]:
        if not self.api_key:
            self.log("Pexels: API ключ не задан")
            return []
        params = {"query": query, "per_page": max(1, min(80, int(per_page))), "orientation": orientation, "size": size}
        try:
            response = self.session.get(self.SEARCH_URL, params=params, timeout=30)
            response.raise_for_status()
            return response.json().get("videos", [])
        except requests.RequestException as exc:
            self.log(f"Pexels: ошибка поиска: {exc}")
            return []

    @staticmethod
    def choose_video_file(video: Dict, target_orientation: str = "vertical") -> Optional[Dict]:
        files = [
            item for item in video.get("video_files", [])
            if item.get("link") and int(item.get("height") or 0) >= 720
        ]
        if not files:
            return None
        want_vertical = target_orientation == "vertical"
        matching = [item for item in files if (int(item.get("height") or 0) >= int(item.get("width") or 0)) == want_vertical]
        candidates = matching or files
        return min(candidates, key=lambda item: abs(int(item.get("height") or 0) - 1920) + abs(int(item.get("width") or 0) - 1080))

    def download_video(self, video: Dict, output_dir: Path, target_orientation: str = "vertical") -> Optional[str]:
        selected = self.choose_video_file(video, target_orientation)
        if not selected:
            return None
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"pexels_video_{video.get('id', 'unknown')}.mp4"
        if output_path.exists() and output_path.stat().st_size > 0:
            return str(output_path)
        try:
            response = self.session.get(selected["link"], timeout=120, stream=True)
            response.raise_for_status()
            with open(output_path, "wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        output.write(chunk)
            return str(output_path)
        except Exception as exc:
            self.log(f"Pexels: ошибка скачивания: {exc}")
            output_path.unlink(missing_ok=True)
            return None
