"""HeyGen API v3 client and final-video integration."""

from __future__ import annotations

import re
import random
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import requests

from core.process_registry import probe_registered, run_registered


LogCallback = Callable[[str], None]


class HeyGenError(RuntimeError):
    pass


class HeyGenClient:
    BASE_URL = "https://api.heygen.com/v3"

    def __init__(
        self,
        api_key: str,
        log_callback: Optional[LogCallback] = None,
        session: Optional[requests.Session] = None,
    ):
        self.api_key = str(api_key or "").strip()
        self.log = log_callback or (lambda _message: None)
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "ContentBotPro/1.0",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.api_key:
            raise HeyGenError("Добавьте HeyGen API key в Настройках.")
        response = self.session.request(
            method, f"{self.BASE_URL}{path}", timeout=kwargs.pop("timeout", 60), **kwargs
        )
        if response.status_code == 429:
            raise HeyGenError("HeyGen вернул 429: исчерпан лимит или баланс API.")
        if response.status_code in (401, 403):
            raise HeyGenError("HeyGen отклонил API key. Проверьте ключ и баланс API.")
        if response.status_code >= 400:
            raise HeyGenError(f"HeyGen API {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise HeyGenError("HeyGen вернул некорректный ответ.") from exc

    def validate_key(self) -> bool:
        self._request("GET", "/avatars", params={"limit": 1})
        return True

    def create_avatar_video(
        self,
        script: str,
        avatar_id: str,
        title: str,
        aspect_ratio: str = "9:16",
        resolution: str = "1080p",
        voice_id: str = "",
        motion_prompt: str = "",
    ) -> str:
        if not str(script or "").strip():
            raise HeyGenError("Сценарий для аватара пуст.")
        if not str(avatar_id or "").strip():
            raise HeyGenError("Укажите Avatar ID на вкладке AI-аватар.")
        payload = {
            "type": "avatar",
            "avatar_id": avatar_id.strip(),
            "title": title[:120],
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "output_format": "mp4",
            "script": script.strip(),
        }
        if voice_id.strip():
            payload["voice_id"] = voice_id.strip()
        if motion_prompt.strip():
            payload["motion_prompt"] = motion_prompt.strip()
        result = self._request(
            "POST",
            "/videos",
            json=payload,
            headers={"Idempotency-Key": str(uuid.uuid4())},
            timeout=90,
        )
        video_id = str((result.get("data") or {}).get("video_id") or "").strip()
        if not video_id:
            raise HeyGenError("HeyGen не вернул ID созданного видео.")
        return video_id

    def wait_for_video(
        self,
        video_id: str,
        timeout: int = 3600,
        poll_interval: int = 10,
    ) -> str:
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            data = (self._request("GET", f"/videos/{video_id}") or {}).get("data") or {}
            video_url = str(data.get("video_url") or "").strip()
            if video_url:
                return video_url
            failure = str(data.get("failure_message") or data.get("failure_code") or "").strip()
            if failure:
                raise HeyGenError(f"HeyGen не создал видео: {failure}")
            time.sleep(max(1, poll_interval))
        raise HeyGenError("Истекло время ожидания рендера HeyGen.")

    def download_video(self, url: str, output_path: Path | str) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Do not forward the HeyGen API key to the signed file-host URL.
        with requests.get(url, stream=True, timeout=(20, 180)) as response:
            response.raise_for_status()
            with output.open("wb") as target:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        target.write(chunk)
        if not output.is_file() or output.stat().st_size < 1024:
            raise HeyGenError("HeyGen-видео скачалось пустым.")
        return output

    def generate_avatar_video(self, output_path: Path | str, **kwargs) -> Path:
        video_id = self.create_avatar_video(**kwargs)
        self.log(f"HeyGen: рендер запущен ({video_id}).")
        url = self.wait_for_video(video_id)
        result = self.download_video(url, output_path)
        self.log(f"HeyGen: аватар-видео готово ({result.name}).")
        return result


def _first_sentence(text: str, limit: int = 500) -> str:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]
    return (parts[0] if parts else text.strip())[:limit]


def _last_sentence(text: str, limit: int = 500) -> str:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]
    return (parts[-1] if parts else text.strip())[:limit]


def _middle_avatar_scripts(text: str, count: int, seed: str, limit: int = 500) -> list[str]:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", str(text or "").strip()) if part.strip()]
    if len(parts) > 2:
        candidates = parts[1:-1]
    else:
        candidates = parts or [str(text or "").strip()]
    candidates = [candidate[:limit] for candidate in candidates if candidate]
    rng = random.Random(seed)
    rng.shuffle(candidates)
    return candidates[: max(1, min(3, int(count or 1)))]


def _video_duration(path: Path) -> float:
    try:
        return max(
            0.0,
            float(
                probe_registered(str(path), label="ffprobe_heygen_video")["format"][
                    "duration"
                ]
            ),
        )
    except Exception:
        return 0.0


def _split_video_at(input_path: Path, before_path: Path, after_path: Path, split_at: float) -> bool:
    from core.rendering.ffmpeg_utils import FFMPEG_PATH

    split_at = max(0.5, float(split_at))
    flags = 0x08000000 if __import__("sys").platform == "win32" else 0
    common = [
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
    ]
    commands = [
        [str(FFMPEG_PATH), "-y", "-i", str(input_path), "-t", f"{split_at:.3f}", *common, str(before_path)],
        [str(FFMPEG_PATH), "-y", "-ss", f"{split_at:.3f}", "-i", str(input_path), *common, str(after_path)],
    ]
    for command in commands:
        result = run_registered(
            command,
            label="ffmpeg_heygen_split",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=7200,
            creationflags=flags,
        )
        if result.returncode != 0:
            return False
    return before_path.is_file() and before_path.stat().st_size > 1024 and after_path.is_file() and after_path.stat().st_size > 1024


def _concat_videos(
    inputs: list[Path],
    output_path: Path,
    width: int,
    height: int,
    fps: int,
) -> None:
    from core.rendering.ffmpeg_utils import FFMPEG_PATH

    temp_output = output_path.with_name(f"{output_path.stem}_heygen_merge.mp4")
    command = [str(FFMPEG_PATH), "-y"]
    for path in inputs:
        command.extend(["-i", str(path)])
    filters = []
    pairs = []
    for index in range(len(inputs)):
        filters.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps}[v{index}]"
        )
        filters.append(f"[{index}:a]aresample=48000[a{index}]")
        pairs.append(f"[v{index}][a{index}]")
    filters.append("".join(pairs) + f"concat=n={len(inputs)}:v=1:a=1[v][a]")
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(temp_output),
        ]
    )
    flags = 0x08000000 if __import__("sys").platform == "win32" else 0
    result = run_registered(
        command,
        label="ffmpeg_heygen_merge",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=7200,
        creationflags=flags,
    )
    if result.returncode != 0 or not temp_output.exists():
        raise HeyGenError(f"Не удалось встроить HeyGen-видео: {result.stderr[-500:]}")
    temp_output.replace(output_path)


def apply_heygen_avatar(
    final_video_path: Path | str,
    full_text: str,
    title: str,
    avatar_settings: dict,
    video_settings: dict,
    log_callback: Optional[LogCallback] = None,
) -> Path:
    """Generate requested avatar segments and merge them into the finished video."""
    settings = dict(avatar_settings or {})
    output = Path(final_video_path)
    if not settings.get("enabled"):
        return output
    log = log_callback or (lambda _message: None)
    api_key = str(settings.get("api_key") or "").strip()
    if not api_key:
        from core.config_manager import ConfigManager

        api_key = str(ConfigManager().get_user_setting("heygen_api_key", "") or "").strip()
    avatar_id = str(settings.get("avatar_id") or "").strip()
    if not api_key:
        log("HeyGen: skipped because API key is not configured.")
        return output
    if not avatar_id:
        log("HeyGen: skipped because Avatar ID is empty.")
        return output
    if not any(settings.get(key) for key in ("full_video", "start_hook", "random_spots", "end_call")):
        log("HeyGen: skipped because no avatar segment is selected.")
        return output
    client = HeyGenClient(api_key, log)
    voice_id = str(settings.get("voice_id") or "").strip()
    width = int(video_settings.get("width", 1080))
    height = int(video_settings.get("height", 1920))
    fps = int(video_settings.get("fps", 30))
    aspect = "9:16" if height > width else "16:9"
    resolution = "720p" if settings.get("test_mode", False) else "1080p"
    temp_dir = output.parent / "_heygen"
    temp_dir.mkdir(parents=True, exist_ok=True)
    common = {
        "avatar_id": avatar_id,
        "title": title,
        "aspect_ratio": aspect,
        "resolution": resolution,
        "voice_id": voice_id,
    }

    def safe_generate(output_path: Path, **kwargs) -> Optional[Path]:
        try:
            return client.generate_avatar_video(output_path, **kwargs)
        except Exception as exc:
            log(f"HeyGen: skipped after API/render error: {str(exc)[:200]}")
            if settings.get("fail_on_error"):
                raise
            return None

    def safe_concat(inputs: list[Path]) -> bool:
        try:
            _concat_videos(inputs, output, width, height, fps)
            return True
        except Exception as exc:
            log(f"HeyGen: merge skipped after error: {str(exc)[:200]}")
            if settings.get("fail_on_error"):
                raise
            return False

    if settings.get("full_video"):
        full = safe_generate(temp_dir / f"{output.stem}_full.mp4", script=full_text, **common)
        if not full:
            return output
        shutil.copy2(full, output)
        log("HeyGen: итоговый ролик заменён полноэкранным аватаром.")
        return output

    intro_inputs: list[Path] = []
    if settings.get("start_hook"):
        hook_clip = safe_generate(
            temp_dir / f"{output.stem}_hook.mp4", script=_first_sentence(full_text), **common
        )
        if hook_clip:
            intro_inputs.append(hook_clip)

    body_inputs: list[Path] = [output]
    if settings.get("random_spots"):
        scripts = _middle_avatar_scripts(
            full_text,
            int(settings.get("random_count") or 1),
            str(settings.get("random_seed") or f"{title}|{output.name}"),
        )
        random_clips = [
            clip
            for idx, script in enumerate(scripts)
            for clip in [
                safe_generate(
                    temp_dir / f"{output.stem}_random_{idx + 1}.mp4",
                    script=script,
                    **common,
                )
            ]
            if clip
        ]
        duration = _video_duration(output)
        if duration >= 8 and random_clips:
            rng = random.Random(str(settings.get("random_seed") or f"{title}|{output.name}|insert"))
            split_at = duration * rng.uniform(0.35, 0.70)
            before = temp_dir / f"{output.stem}_before_avatar.mp4"
            after = temp_dir / f"{output.stem}_after_avatar.mp4"
            if _split_video_at(output, before, after, split_at):
                body_inputs = [before, random_clips[0], after]
            else:
                body_inputs = [output, *random_clips]
        elif random_clips:
            body_inputs = [output, *random_clips]

    inputs: list[Path] = [*intro_inputs, *body_inputs]
    if settings.get("end_call"):
        outro_clip = safe_generate(
            temp_dir / f"{output.stem}_outro.mp4", script=_last_sentence(full_text), **common
        )
        if outro_clip:
            inputs.append(outro_clip)
    if len(inputs) > 1:
        if not safe_concat(inputs):
            return output
        log("HeyGen: аватар встроен в готовый ролик.")
    return output
