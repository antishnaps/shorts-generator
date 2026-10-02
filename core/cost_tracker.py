#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API cost tracking for the current generation session."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Callable, ClassVar, Dict, Tuple


@dataclass
class CostTracker:
    """Tracks estimated API spend for one app generation run."""

    text_input_tokens: int = 0
    text_output_tokens: int = 0
    image_count: int = 0
    tts_characters: int = 0
    tts_input_tokens: int = 0
    tts_audio_tokens: int = 0
    tts_seconds: float = 0.0
    text_usage_by_model: Dict[str, Dict[str, int]] = field(default_factory=dict)
    image_usage_by_model: Dict[str, int] = field(default_factory=dict)
    tts_usage_by_model: Dict[str, Dict[str, float]] = field(default_factory=dict)

    PRICING_VERSION: ClassVar[str] = "google-pricing-2026-07-03"
    GEMINI_PRICING_URL: ClassVar[str] = "https://ai.google.dev/gemini-api/docs/pricing"
    CLOUD_TTS_PRICING_URL: ClassVar[str] = "https://cloud.google.com/text-to-speech/pricing"
    AUDIO_TOKENS_PER_SECOND: ClassVar[int] = 25

    # Backward-compatible defaults. These are per-token / per-image values.
    GEMINI_FLASH_INPUT_PRICE: ClassVar[float] = 0.30 / 1_000_000
    GEMINI_FLASH_OUTPUT_PRICE: ClassVar[float] = 2.50 / 1_000_000
    IMAGE_PRICE: ClassVar[float] = 0.067
    TTS_PRICE: ClassVar[float] = 16.00 / 1_000_000

    TEXT_PRICES_PER_M_TOKEN: ClassVar[Dict[str, Tuple[float, float]]] = {
        "gemini-3.5-flash": (1.50, 9.00),
        "gemini-3.1-flash-lite": (0.25, 1.50),
        "gemini-3.1-pro-preview": (2.00, 12.00),
        "gemini-2.5-flash-lite": (0.10, 0.40),
        "gemini-2.5-flash": (0.30, 2.50),
        "gemini-2.5-pro": (1.25, 10.00),
    }
    IMAGE_PRICE_PER_IMAGE: ClassVar[Dict[str, float]] = {
        # Default app model. 1K output tier; actual billing can vary by selected resolution.
        "gemini-3.1-flash-image": 0.067,
        "gemini-3.1-flash-lite-image": 0.0336,
        "gemini-3-pro-image": 0.134,
        "gemini-2.5-flash-image": 0.039,
    }
    TTS_PRICES_PER_M_TOKEN: ClassVar[Dict[str, Tuple[float, float]]] = {
        "gemini-3.1-flash-tts-preview": (1.00, 20.00),
        "gemini-2.5-flash-preview-tts": (0.50, 10.00),
        "gemini-2.5-flash-lite-preview-tts": (0.50, 10.00),
        "gemini-2.5-pro-preview-tts": (1.00, 20.00),
    }

    _lock: threading.RLock = field(default_factory=threading.RLock)

    @staticmethod
    def _clean_model(model: str | None, fallback: str) -> str:
        value = str(model or "").strip().lower()
        if not value:
            return fallback
        if "/" in value:
            value = value.rsplit("/", 1)[-1]
        return value

    @classmethod
    def _matching_price(
        cls,
        model: str,
        prices: Dict[str, Tuple[float, float]],
        fallback_model: str,
    ) -> Tuple[float, float]:
        model = cls._clean_model(model, fallback_model)
        if model in prices:
            return prices[model]
        for known_model, price in prices.items():
            if known_model in model:
                return price
        return prices[fallback_model]

    @classmethod
    def _matching_image_price(cls, model: str) -> float:
        model = cls._clean_model(model, "gemini-3.1-flash-image")
        if model in cls.IMAGE_PRICE_PER_IMAGE:
            return cls.IMAGE_PRICE_PER_IMAGE[model]
        for known_model, price in cls.IMAGE_PRICE_PER_IMAGE.items():
            if known_model in model:
                return price
        return cls.IMAGE_PRICE_PER_IMAGE["gemini-3.1-flash-image"]

    def add_text_generation(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str | None = None,
    ) -> None:
        """Add text-generation token usage."""
        input_tokens = max(0, int(input_tokens or 0))
        output_tokens = max(0, int(output_tokens or 0))
        if input_tokens <= 0 and output_tokens <= 0:
            return

        model_key = self._clean_model(model, "gemini-3.1-flash-lite")
        with self._lock:
            self.text_input_tokens += input_tokens
            self.text_output_tokens += output_tokens
            usage = self.text_usage_by_model.setdefault(
                model_key, {"input_tokens": 0, "output_tokens": 0}
            )
            usage["input_tokens"] += input_tokens
            usage["output_tokens"] += output_tokens

    def add_image(
        self,
        count: int = 1,
        model: str | None = None,
        resolution: str | None = None,
    ) -> None:
        """Add image-generation usage."""
        count = max(0, int(count or 0))
        if count <= 0:
            return
        model_key = self._clean_model(model, "gemini-3.1-flash-image")
        with self._lock:
            self.image_count += count
            self.image_usage_by_model[model_key] = (
                self.image_usage_by_model.get(model_key, 0) + count
            )

    def add_tts(self, characters: int) -> None:
        """Add legacy / character-billed TTS usage."""
        characters = max(0, int(characters or 0))
        if characters <= 0:
            return
        with self._lock:
            self.tts_characters += characters

    def add_gemini_tts(
        self,
        input_tokens: int = 0,
        audio_tokens: int = 0,
        duration_seconds: float | None = None,
        model: str | None = None,
    ) -> None:
        """Add Gemini TTS usage, billed as text input tokens + audio output tokens."""
        input_tokens = max(0, int(input_tokens or 0))
        audio_tokens = max(0, int(audio_tokens or 0))
        seconds = max(0.0, float(duration_seconds or 0.0))
        if audio_tokens <= 0 and seconds > 0:
            audio_tokens = int(round(seconds * self.AUDIO_TOKENS_PER_SECOND))
        if seconds <= 0 and audio_tokens > 0:
            seconds = audio_tokens / self.AUDIO_TOKENS_PER_SECOND
        if input_tokens <= 0 and audio_tokens <= 0 and seconds <= 0:
            return

        model_key = self._clean_model(model, "gemini-3.1-flash-tts-preview")
        with self._lock:
            self.tts_input_tokens += input_tokens
            self.tts_audio_tokens += audio_tokens
            self.tts_seconds += seconds
            usage = self.tts_usage_by_model.setdefault(
                model_key,
                {"input_tokens": 0, "audio_tokens": 0, "seconds": 0.0},
            )
            usage["input_tokens"] += input_tokens
            usage["audio_tokens"] += audio_tokens
            usage["seconds"] += seconds

    def _text_cost_unlocked(self) -> float:
        if not self.text_usage_by_model:
            return (
                self.text_input_tokens * self.GEMINI_FLASH_INPUT_PRICE
                + self.text_output_tokens * self.GEMINI_FLASH_OUTPUT_PRICE
            )
        total = 0.0
        for model, usage in self.text_usage_by_model.items():
            input_price, output_price = self._matching_price(
                model, self.TEXT_PRICES_PER_M_TOKEN, "gemini-3.1-flash-lite"
            )
            total += (usage.get("input_tokens", 0) / 1_000_000) * input_price
            total += (usage.get("output_tokens", 0) / 1_000_000) * output_price
        return total

    def _image_cost_unlocked(self) -> float:
        if not self.image_usage_by_model:
            return self.image_count * self.IMAGE_PRICE
        return sum(
            count * self._matching_image_price(model)
            for model, count in self.image_usage_by_model.items()
        )

    def _gemini_tts_cost_unlocked(self) -> float:
        total = 0.0
        for model, usage in self.tts_usage_by_model.items():
            input_price, output_price = self._matching_price(
                model, self.TTS_PRICES_PER_M_TOKEN, "gemini-3.1-flash-tts-preview"
            )
            total += (usage.get("input_tokens", 0) / 1_000_000) * input_price
            total += (usage.get("audio_tokens", 0) / 1_000_000) * output_price
        return total

    def _legacy_tts_cost_unlocked(self) -> float:
        return self.tts_characters * self.TTS_PRICE

    def get_text_cost(self) -> float:
        with self._lock:
            return self._text_cost_unlocked()

    def get_image_cost(self) -> float:
        with self._lock:
            return self._image_cost_unlocked()

    def get_gemini_tts_cost(self) -> float:
        with self._lock:
            return self._gemini_tts_cost_unlocked()

    def get_legacy_tts_cost(self) -> float:
        with self._lock:
            return self._legacy_tts_cost_unlocked()

    def get_tts_cost(self) -> float:
        with self._lock:
            return self._gemini_tts_cost_unlocked() + self._legacy_tts_cost_unlocked()

    def get_total_cost(self) -> float:
        with self._lock:
            return (
                self._text_cost_unlocked()
                + self._image_cost_unlocked()
                + self._gemini_tts_cost_unlocked()
                + self._legacy_tts_cost_unlocked()
            )

    def get_summary(self) -> str:
        """Return a clear, log-friendly API spend summary."""
        data = self.to_dict()
        lines = [
            "",
            "API spend estimate",
            "=" * 56,
            f"TOTAL FOR THIS RUN: ${data['estimated_total_cost_usd']:.6f}",
            "",
            f"Gemini text:  ${data['estimated_text_cost_usd']:.6f}",
            f"  input tokens:  {data['text_input_tokens']:,}",
            f"  output tokens: {data['text_output_tokens']:,}",
            "",
            f"Gemini images: ${data['estimated_image_cost_usd']:.6f}",
            f"  images:        {data['image_count']}",
            "",
            f"Gemini TTS:    ${data['estimated_gemini_tts_cost_usd']:.6f}",
            f"  text tokens:   {data['tts_input_tokens']:,}",
            f"  audio tokens:  {data['tts_audio_tokens']:,}",
            f"  audio seconds: {data['tts_seconds']:.1f}",
        ]
        if data["tts_characters"] > 0:
            lines.extend(
                [
                    "",
                    f"Legacy/Cloud TTS: ${data['estimated_legacy_tts_cost_usd']:.6f}",
                    f"  characters:      {data['tts_characters']:,}",
                ]
            )
        lines.extend(
            [
                "=" * 56,
                "Estimate only: excludes free tier, taxes, cached/batch discounts, and provider-side rounding.",
                f"Pricing version: {self.PRICING_VERSION}",
            ]
        )
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        with self._lock:
            text_cost = self._text_cost_unlocked()
            image_cost = self._image_cost_unlocked()
            gemini_tts_cost = self._gemini_tts_cost_unlocked()
            legacy_tts_cost = self._legacy_tts_cost_unlocked()
            tts_cost = gemini_tts_cost + legacy_tts_cost
            total_cost = text_cost + image_cost + tts_cost
            return {
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "pricing_version": self.PRICING_VERSION,
                "pricing_sources": {
                    "gemini": self.GEMINI_PRICING_URL,
                    "cloud_tts": self.CLOUD_TTS_PRICING_URL,
                },
                "text_input_tokens": self.text_input_tokens,
                "text_output_tokens": self.text_output_tokens,
                "text_tokens_total": self.text_input_tokens + self.text_output_tokens,
                "text_usage_by_model": self.text_usage_by_model,
                "image_count": self.image_count,
                "image_usage_by_model": self.image_usage_by_model,
                "tts_characters": self.tts_characters,
                "tts_input_tokens": self.tts_input_tokens,
                "tts_audio_tokens": self.tts_audio_tokens,
                "tts_seconds": round(self.tts_seconds, 3),
                "tts_audio_tokens_per_second": self.AUDIO_TOKENS_PER_SECOND,
                "tts_usage_by_model": self.tts_usage_by_model,
                "estimated_text_cost_usd": round(text_cost, 6),
                "estimated_image_cost_usd": round(image_cost, 6),
                "estimated_gemini_tts_cost_usd": round(gemini_tts_cost, 6),
                "estimated_legacy_tts_cost_usd": round(legacy_tts_cost, 6),
                "estimated_tts_cost_usd": round(tts_cost, 6),
                "estimated_total_cost_usd": round(total_cost, 6),
            }

    def save_report(
        self,
        output_dir: str | Path = Path(__file__).resolve().parent.parent / "logs",
    ) -> Path:
        """Persist the latest usage snapshot plus an append-only session history."""
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        latest_path = directory / "api_usage_latest.json"
        history_path = directory / "api_usage_history.jsonl"
        snapshot = self.to_dict()
        with self._lock:
            fd, temp_name = tempfile.mkstemp(
                prefix=".api_usage_", suffix=".tmp", dir=str(directory)
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(snapshot, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                temp_path.replace(latest_path)
                with open(history_path, "a", encoding="utf-8") as history:
                    history.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
                    history.flush()
            finally:
                temp_path.unlink(missing_ok=True)
        return latest_path

    def reset(self) -> None:
        with self._lock:
            self.text_input_tokens = 0
            self.text_output_tokens = 0
            self.image_count = 0
            self.tts_characters = 0
            self.tts_input_tokens = 0
            self.tts_audio_tokens = 0
            self.tts_seconds = 0.0
            self.text_usage_by_model.clear()
            self.image_usage_by_model.clear()
            self.tts_usage_by_model.clear()


_current_tracker: CostTracker | None = None
_tracker_lock = threading.Lock()


def get_tracker() -> CostTracker:
    """Return the current cost tracker, creating one if needed."""
    global _current_tracker
    with _tracker_lock:
        if _current_tracker is None:
            _current_tracker = CostTracker()
        return _current_tracker


def reset_tracker() -> None:
    """Reset tracker for a new generation run."""
    global _current_tracker
    with _tracker_lock:
        _current_tracker = CostTracker()


def log_cost_summary(log_callback: Callable[[str], None]) -> None:
    """Write the cost summary and persisted report path to the app log."""
    tracker = get_tracker()
    for line in tracker.get_summary().split("\n"):
        log_callback(line)
    try:
        report_path = tracker.save_report()
        log_callback(f"API usage report saved: {report_path}")
    except Exception as error:
        log_callback(f"Could not save API usage report: {error}")
