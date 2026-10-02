"""Intent-aware sound-effect planning and optional Freesound mixing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Optional

from core.freesound_client import FreesoundClient
from core.process_registry import run_registered
from core.rendering.ffmpeg_utils import FFMPEG_PATH
from core.retention_optimizer import RetentionOptimizer


@dataclass(frozen=True)
class SoundEvent:
    time: float
    query: str
    volume: float
    reason: str
    asset_path: str = ""


class SoundDesignPlanner:
    """Build restrained, meaningful SFX cues instead of random noise."""

    KEYWORDS = {
        "impact": (
            "важно", "главное", "никогда", "секрет", "шок", "взрыв", "опасн",
            "important", "secret", "never", "shock", "danger", "explosion",
        ),
        "reveal": (
            "оказалось", "вот почему", "на самом деле", "результат", "finally",
            "turns out", "actually", "result", "reveal",
        ),
        "click": (
            "выбери", "нажми", "смотри", "проверь", "select", "click", "look", "check",
        ),
        "transition": (
            "затем", "дальше", "после", "однако", "meanwhile", "next", "then", "however",
        ),
    }
    QUERIES = {
        "hook": "cinematic intro hit",
        "impact": "cinematic impact hit",
        "reveal": "soft reveal shimmer",
        "click": "clean interface click",
        "transition": "short subtle whoosh",
        "question": "soft question riser",
        "rhythm": "short subtle whoosh",
    }
    VOLUMES = {
        "hook": 0.28,
        "impact": 0.24,
        "reveal": 0.18,
        "click": 0.13,
        "transition": 0.15,
        "question": 0.15,
        "rhythm": 0.11,
    }

    @classmethod
    def plan(cls, text: str, duration: float, frequency: str = "medium") -> list[SoundEvent]:
        duration = max(0.0, float(duration or 0))
        if duration <= 0:
            return []
        text = str(text or "").strip()
        events = [cls._event(0.35, "hook", "opening hook")]

        lowered = text.lower()
        text_length = max(1, len(text))
        for kind, keywords in cls.KEYWORDS.items():
            for keyword in keywords:
                start = 0
                while True:
                    index = lowered.find(keyword, start)
                    if index < 0:
                        break
                    cue_time = max(0.6, min(duration - 0.25, duration * index / text_length))
                    events.append(cls._event(cue_time, kind, f"keyword: {keyword}"))
                    start = index + len(keyword)

        sentence_start = 0
        for question_index in (index for index, char in enumerate(text) if char == "?"):
            midpoint = (sentence_start + question_index) / 2
            cue_time = max(0.6, min(duration - 0.25, duration * midpoint / text_length))
            events.append(cls._event(cue_time, "question", "spoken question"))
            sentence_start = question_index + 1

        for cue_time in RetentionOptimizer.get_sfx_timing(frequency, int(duration)):
            events.append(cls._event(float(cue_time), "rhythm", "retention pacing"))

        limits = {"low": 4, "medium": 8, "high": 14}
        min_gap = {"low": 4.0, "medium": 2.3, "high": 1.4}.get(frequency, 2.3)
        def priority(event: SoundEvent) -> tuple[int, float]:
            if event.reason == "opening hook":
                return 0, event.time
            if event.reason.startswith("keyword:"):
                return 1, event.time
            if event.reason == "spoken question":
                return 2, event.time
            return 3, event.time

        ranked = sorted(events, key=priority)
        result: list[SoundEvent] = []
        for event in ranked:
            if any(abs(event.time - existing.time) < min_gap for existing in result):
                continue
            result.append(event)
            if len(result) >= limits.get(frequency, 8):
                break
        return sorted(result, key=lambda event: event.time)

    @classmethod
    def _event(cls, time: float, kind: str, reason: str) -> SoundEvent:
        return SoundEvent(
            time=round(float(time), 3),
            query=cls.QUERIES[kind],
            volume=cls.VOLUMES[kind],
            reason=reason,
        )


class FreesoundEffectResolver:
    """Resolve planned cues to cached Freesound previews."""

    def __init__(self, api_key: str, cache_dir: Path | str, log_callback=None):
        self.client = FreesoundClient(api_key, log_callback)
        self.cache_dir = Path(cache_dir)

    def resolve(self, events: Iterable[SoundEvent]) -> list[SoundEvent]:
        resolved: list[SoundEvent] = []
        assets: dict[str, str] = {}
        for event in events:
            path = assets.get(event.query)
            if path is None:
                results = self.client.search_sfx(event.query, duration_max=4, page_size=3)
                path = self.client.download_sound(results[0], self.cache_dir) if results else ""
                assets[event.query] = path or ""
            resolved.append(replace(event, asset_path=path or ""))
        return resolved


def mix_sound_effects(
    base_audio: Path | str,
    events: Iterable[SoundEvent],
    output_path: Path | str,
    ffmpeg_path: Optional[str] = None,
) -> Path:
    """Mix resolved SFX cues into an audio file with conservative volumes."""
    usable = [event for event in events if event.asset_path and Path(event.asset_path).exists()]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not usable:
        raise ValueError("Нет скачанных звуковых эффектов для сведения.")

    command = [ffmpeg_path or FFMPEG_PATH, "-y", "-i", str(base_audio)]
    for event in usable:
        command.extend(["-i", event.asset_path])

    filters = []
    mix_inputs = ["[0:a]"]
    for index, event in enumerate(usable, start=1):
        delay = max(0, int(event.time * 1000))
        label = f"sfx{index}"
        filters.append(
            f"[{index}:a]volume={event.volume:.3f},adelay={delay}|{delay},apad[{label}]"
        )
        mix_inputs.append(f"[{label}]")
    filters.append(
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:"
        "dropout_transition=0,alimiter=limit=0.95[out]"
    )
    command.extend(
        [
            "-filter_complex", ";".join(filters),
            "-map", "[out]",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]
    )
    run_registered(
        command,
        label="ffmpeg_sound_effects_mix",
        check=True,
        capture_output=True,
    )
    return output_path
