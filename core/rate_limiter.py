#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Rate limiting utilities to prevent API quota exhaustion
"""

import time
import threading
from typing import Dict, Callable, Optional
from collections import deque


# Global lock for thread-safe rate limiting
_global_lock = threading.Lock()
_global_throttler = None


def get_global_throttler(log_callback: Optional[Callable] = None) -> 'APICallThrottler':
    """Get or create global throttler instance (singleton pattern)"""
    global _global_throttler
    with _global_lock:
        if _global_throttler is None:
            _global_throttler = APICallThrottler(log_callback=log_callback)
        return _global_throttler


class RateLimiter:
    """Rate limiter for API calls with sliding window (thread-safe)"""

    def __init__(self, calls_per_minute: int = 60, log_callback: Optional[Callable] = None):
        """
        Initialize rate limiter.
        
        Args:
            calls_per_minute: Maximum API calls allowed per minute
            log_callback: Optional logging function
        """
        self.calls_per_minute = calls_per_minute
        self.calls_per_second = calls_per_minute / 60.0
        self.min_interval = 1.0 / self.calls_per_second if self.calls_per_second > 0 else 0
        self.call_times = deque()
        self.log_callback = log_callback or (lambda x: print(x))
        self._lock = threading.Lock()  # Thread-safe lock

    def wait_if_needed(self, api_name: str = "API"):
        """Wait if necessary to maintain rate limit (thread-safe)."""
        with self._lock:
            now = time.time()
            minute_ago = now - 60

            # Remove calls older than 1 minute
            while self.call_times and self.call_times[0] < minute_ago:
                self.call_times.popleft()

            # If we've exceeded the limit, wait
            if len(self.call_times) >= self.calls_per_minute:
                oldest_call = self.call_times[0]
                sleep_time = oldest_call + 60 - now
                if sleep_time > 0:
                    self.log_callback(f"⏱️ {api_name} rate limit: ожидание {sleep_time:.1f}s...")
                    # Release lock while sleeping so other threads can check
                    self._lock.release()
                    try:
                        time.sleep(sleep_time + 0.1)
                    finally:
                        self._lock.acquire()
                    now = time.time()

            self.call_times.append(now)

    def get_wait_time(self) -> float:
        """Get recommended wait time before next call (in seconds)."""
        return max(0, self.min_interval)


class APICallThrottler:
    """Throttle different API calls with individual rate limits"""

    def __init__(self, log_callback: Optional[Callable] = None):
        self.limiters: Dict[str, RateLimiter] = {}
        self.log_callback = log_callback or (lambda x: print(x))

        # 🚀 ОБНОВЛЕННЫЕ ЛИМИТЫ под реальные квоты Gemini API
        # Источник: https://ai.google.dev/gemini-api/docs/models/gemini
        
        # Gemini Text Models (gemini-2.5-flash, gemini-2.5-flash)
        # Реальный лимит: 1500 RPM (requests per minute)
        # Используем 1000 для безопасности с учетом других API calls
        self.limiters['gemini_text'] = RateLimiter(calls_per_minute=1000, log_callback=self.log_callback)
        
        # Imagen / Image Generation (gemini-2.5-flash-image)
        # Реальный лимит: 60 RPM
        # Используем 50 для безопасности
        self.limiters['imagen'] = RateLimiter(calls_per_minute=50, log_callback=self.log_callback)
        
        # Veo 3 Video Generation
        # Реальный лимит: 5 RPM (очень строгий!)
        # Используем 4 для безопасности
        self.limiters['veo3'] = RateLimiter(calls_per_minute=4, log_callback=self.log_callback)
        
        # Translation API (если используется)
        # Увеличен с 40 до 100 RPM
        self.limiters['translate'] = RateLimiter(calls_per_minute=100, log_callback=self.log_callback)
        
        # Gemini TTS (Text-to-Speech)
        # 🔧 VPN FIX: Снижен лимит 10 → 8 RPM для стабильности через VPN
        self.limiters['gemini_tts'] = RateLimiter(calls_per_minute=8, log_callback=self.log_callback)

    def throttle(self, api_name: str):
        """Apply throttling for the specified API."""
        if api_name not in self.limiters:
            self.limiters[api_name] = RateLimiter(calls_per_minute=30, log_callback=self.log_callback)
        
        self.limiters[api_name].wait_if_needed(api_name)

    def get_current_stats(self) -> Dict[str, int]:
        """Get current call counts for all APIs."""
        stats = {}
        for name, limiter in self.limiters.items():
            minute_ago = time.time() - 60
            current_calls = sum(1 for t in limiter.call_times if t >= minute_ago)
            stats[name] = current_calls
        return stats
