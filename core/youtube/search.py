#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Search Functions
Handles video search via YouTube Data API and smart query generation.
"""

import json
import requests
import time
from typing import List, Dict, Callable, Optional

from .utils import _dummy_log, _parse_duration


def _api_error_reason(response) -> str:
    """Return a stable YouTube Data API reason without logging request params."""

    try:
        error = response.json().get("error") or {}
        reasons = error.get("errors") or []
        if reasons and reasons[0].get("reason"):
            return str(reasons[0]["reason"])
        if error.get("status"):
            return str(error["status"])
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return f"http_{getattr(response, 'status_code', 'unknown')}"


def _request_youtube_json(
    url: str,
    params: Dict,
    *,
    timeout: int,
    operation: str,
    log_callback: Callable,
) -> Optional[Dict]:
    """Perform one request plus one transient retry, never an unbounded loop."""

    for attempt in range(2):
        try:
            response = requests.get(url, params=params, timeout=timeout, stream=False)
        except requests.RequestException as exc:
            if attempt == 0:
                time.sleep(0.5)
                continue
            log_callback(f"⚠️ YouTube Data API [{operation}/network]: {str(exc)[:100]}")
            return None

        if response.status_code == 200:
            try:
                payload = response.json()
                return payload if isinstance(payload, dict) else None
            except (TypeError, ValueError, json.JSONDecodeError):
                log_callback(f"⚠️ YouTube Data API [{operation}/invalid_json]")
                return None

        reason = _api_error_reason(response)
        transient = response.status_code == 429 or 500 <= response.status_code < 600
        if transient and attempt == 0:
            time.sleep(0.5)
            continue
        log_callback(
            f"⚠️ YouTube Data API [{operation}/{reason}] HTTP {response.status_code}"
        )
        return None
    return None


def search_youtube_api(
    query: str,
    api_key: str,
    max_results: int = 25,
    video_duration: str = "medium",
    video_definition: str = "high",
    order: str = "relevance",
    log_callback: Optional[Callable] = None
) -> List[Dict]:
    """
    Поиск видео через YouTube Data API v3.
    Быстрее и стабильнее чем yt-dlp search.
    """
    if log_callback is None:
        log_callback = _dummy_log
    
    url = "https://www.googleapis.com/youtube/v3/search"
    
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": min(max_results, 50),
        "videoDuration": video_duration,
        "videoDefinition": video_definition,
        "order": order,
        "key": api_key
    }
    
    try:
        data = _request_youtube_json(
            url,
            params,
            timeout=15,
            operation="search",
            log_callback=log_callback,
        )
        if not data:
            return []
        items = data.get("items", [])
        
        if not items:
            return []
        
        video_ids = [item["id"]["videoId"] for item in items]
        videos_details = _get_video_details(video_ids, api_key, log_callback)
        
        results = []
        for item in items:
            video_id = item["id"]["videoId"]
            snippet = item["snippet"]
            details = videos_details.get(video_id, {})
            
            duration = _parse_duration(details.get("duration", "PT0S"))
            
            results.append({
                "id": video_id,
                "title": snippet.get("title", "Unknown"),
                "channel": snippet.get("channelTitle", ""),
                "channel_id": snippet.get("channelId", ""),
                "description": snippet.get("description", ""),
                "published_at": snippet.get("publishedAt", ""),
                "duration": duration,
                "view_count": details.get("viewCount", 0),
                "definition": details.get("definition", ""),
                "url": f"https://www.youtube.com/watch?v={video_id}"
            })
        
        return results
        
    except Exception as e:
        log_callback(f"❌ Ошибка поиска: {str(e)[:100]}")
        return []


def _get_video_details(
    video_ids: List[str],
    api_key: str,
    log_callback: Optional[Callable] = None,
) -> Dict:
    """Получает детали видео (длительность, просмотры) через YouTube Data API."""
    if not video_ids:
        return {}
    
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "contentDetails,statistics",
        "id": ",".join(video_ids),
        "key": api_key
    }
    
    log_callback = log_callback or _dummy_log
    try:
        data = _request_youtube_json(
            url,
            params,
            timeout=10,
            operation="details",
            log_callback=log_callback,
        )
        if not data:
            return {}
        results = {}
        
        for item in data.get("items", []):
            video_id = item["id"]
            content = item.get("contentDetails", {})
            stats = item.get("statistics", {})
            
            results[video_id] = {
                "duration": content.get("duration", "PT0S"),
                "viewCount": int(stats.get("viewCount", 0)),
                "definition": content.get("definition", ""),
            }
        
        return results
    except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError):
        return {}


# Compatibility exports historically lived in this module.  Keep the public
# import path while using one implementation and one cache.
from .smart_search import (  # noqa: E402
    generate_semantic_fallback_queries,
    generate_smart_queries,
)


__all__ = [
    "_get_video_details",
    "generate_semantic_fallback_queries",
    "generate_smart_queries",
    "search_youtube_api",
]
