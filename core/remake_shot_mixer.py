#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Cut one remake source into shots and return them in a mixed order."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import random
from typing import Callable, Iterable, List

from core.rendering.ffmpeg_utils import FFMPEG_PATH
from core.process_registry import run_registered


@dataclass(frozen=True)
class SourceShot:
    index: int
    start: float
    duration: float


def _stable_seed(value: str) -> int:
    digest = hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:16], 16)


def plan_source_shots(
    source_duration: float,
    min_duration: float = 2.5,
    max_duration: float = 5.0,
    seed: int | str = 0,
) -> List[SourceShot]:
    """Split the complete source timeline into slightly varied consecutive shots."""
    duration = max(0.0, float(source_duration or 0.0))
    minimum = max(0.8, float(min_duration or 2.5))
    maximum = max(minimum, float(max_duration or minimum))
    if duration <= 0.2:
        return []

    rng = random.Random(_stable_seed(str(seed)))
    shots: List[SourceShot] = []
    cursor = 0.0
    index = 0
    while cursor < duration - 0.2:
        remaining = duration - cursor
        shot_duration = min(remaining, rng.uniform(minimum, maximum))
        if shot_duration < 0.8 and shots:
            previous = shots[-1]
            shots[-1] = SourceShot(previous.index, previous.start, previous.duration + shot_duration)
            break
        shots.append(SourceShot(index, round(cursor, 3), round(shot_duration, 3)))
        cursor += shot_duration
        index += 1
    return shots


def shuffled_shot_order(shots: Iterable[SourceShot], seed: int | str = 0) -> List[SourceShot]:
    """Shuffle shots while avoiding neighbouring original segments when possible."""
    remaining = list(shots or [])
    if len(remaining) <= 2:
        return list(reversed(remaining))

    original_indices = [shot.index for shot in remaining]
    rng = random.Random(_stable_seed(str(seed)))
    result: List[SourceShot] = []
    while remaining:
        candidates = [
            shot for shot in remaining
            if not result or abs(shot.index - result[-1].index) > 1
        ]
        chosen = rng.choice(candidates or remaining)
        result.append(chosen)
        remaining.remove(chosen)

    if [shot.index for shot in result] == original_indices:
        result = result[1:] + result[:1]
    return result


class RemakeShotMixer:
    """FFmpeg-backed source shot extraction with a small reusable cache."""

    def __init__(self, log_callback: Callable[[str], None] = None):
        self.log = log_callback or (lambda _message: None)

    @staticmethod
    def _ffprobe_path() -> str:
        ffmpeg = Path(str(FFMPEG_PATH))
        sibling = ffmpeg.with_name("ffprobe.exe" if ffmpeg.suffix.lower() == ".exe" else "ffprobe")
        return str(sibling) if sibling.is_file() else "ffprobe"

    def probe_duration(self, source_path: Path | str) -> float:
        result = run_registered(
            [
                self._ffprobe_path(),
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(source_path),
            ],
            label="ffprobe_remake_source",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "ffprobe не определил длительность")[:300])
        return float(str(result.stdout or "0").strip())

    @staticmethod
    def _cache_key(
        source_path: Path,
        target_duration: float,
        min_duration: float,
        max_duration: float,
        seed: int | str,
    ) -> str:
        stat = source_path.stat()
        payload = "|".join(
            map(str, (
                source_path.resolve(), stat.st_size, stat.st_mtime_ns,
                round(target_duration, 2), round(min_duration, 2),
                round(max_duration, 2), seed,
            ))
        )
        return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _extract_one(self, source_path: Path, shot: SourceShot, output_path: Path) -> Path:
        command = [
            str(FFMPEG_PATH), "-y",
            "-ss", f"{shot.start:.3f}",
            "-i", str(source_path),
            "-t", f"{shot.duration:.3f}",
            "-map", "0:v:0", "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(output_path),
        ]
        result = run_registered(
            command,
            label=f"ffmpeg_remake_shot_{shot.index}",
            capture_output=True,
            text=True,
            timeout=max(90, int(shot.duration * 12)),
            check=False,
        )
        if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size < 1024:
            output_path.unlink(missing_ok=True)
            raise RuntimeError((result.stderr or "ffmpeg не создал шот")[-400:])
        return output_path

    def prepare_shots(
        self,
        source_path: Path | str,
        output_dir: Path | str,
        target_duration: float,
        min_duration: float = 2.5,
        max_duration: float = 5.0,
        target_count: int | None = None,
        seed: int | str = 0,
    ) -> List[str]:
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(f"Исходный видеоряд не найден: {source}")
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)

        source_duration = self.probe_duration(source)
        shots = plan_source_shots(source_duration, min_duration, max_duration, seed)
        if not shots:
            raise RuntimeError("Исходный ролик слишком короткий для нарезки на шоты.")
        mixed = shuffled_shot_order(shots, seed)
        desired_count = max(1, int(target_count or len(mixed)))
        unique_needed = mixed[: min(desired_count, len(mixed))]

        cache_key = self._cache_key(
            source, target_duration, min_duration, max_duration, seed
        )
        cache_dir = destination / f"remake_{cache_key}"
        cache_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = cache_dir / "shot_manifest.json"

        output_by_index = {
            shot.index: cache_dir / f"shot_{shot.index:04d}_{shot.start:.3f}.mp4"
            for shot in unique_needed
        }
        pending = [
            shot for shot in unique_needed
            if not output_by_index[shot.index].is_file()
            or output_by_index[shot.index].stat().st_size < 1024
        ]
        if pending:
            self.log(
                f"🎬 Нарезаем исходный ролик: {len(pending)} новых шотов "
                f"из {len(shots)}"
            )
            failures = []
            with ThreadPoolExecutor(max_workers=min(3, len(pending))) as executor:
                futures = {
                    executor.submit(
                        self._extract_one,
                        source,
                        shot,
                        output_by_index[shot.index],
                    ): shot
                    for shot in pending
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        failures.append((futures[future], str(exc)))
            if failures:
                self.log(f"   ⚠️ Не извлечено шотов: {len(failures)}")

        available = [
            shot for shot in unique_needed
            if output_by_index[shot.index].is_file()
            and output_by_index[shot.index].stat().st_size >= 1024
        ]
        if not available:
            raise RuntimeError("Не удалось извлечь ни одного шота из исходного ролика.")

        ordered_paths = [str(output_by_index[shot.index]) for shot in available]
        while len(ordered_paths) < desired_count:
            ordered_paths.extend(ordered_paths[: desired_count - len(ordered_paths)])
        ordered_paths = ordered_paths[:desired_count]
        manifest_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "source": str(source.resolve()),
                    "source_duration": source_duration,
                    "target_duration": target_duration,
                    "seed": str(seed),
                    "shots": [asdict(shot) for shot in mixed],
                    "selected_paths": ordered_paths,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.log(f"✅ Видеоряд ремейка перемешан: {len(ordered_paths)} шотов")
        return ordered_paths
