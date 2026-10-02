#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Qt worker for reliable videos from one image and one audio file."""

import logging
import os
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from core.rendering.ffmpeg_utils import FFMPEG_PATH
from core.rendering.video_codec import VideoCodec
from core.process_registry import get_process_registry, run_registered
from gui.translations import t

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}


@dataclass
class StaticVideoSettings:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    fit_mode: str = "cover"
    animation: str = "slow_zoom"
    filter_type: str = "none"
    image_match_mode: str = "name_then_random"


def find_media_files(folder: Path, extensions: Iterable[str]) -> List[Path]:
    extensions = {ext.lower() for ext in extensions}
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in extensions)


def match_image(audio_path: Path, images: List[Path], mode: str = "name_then_random", rng=None) -> Optional[Path]:
    if not images:
        return None
    exact = [image for image in images if image.stem.casefold() == audio_path.stem.casefold()]
    if exact:
        return exact[0]
    if mode == "name_only":
        return None
    if mode == "name_then_first":
        return images[0]
    return (rng or random).choice(images)


def build_static_filter_complex(
    settings: StaticVideoSettings,
    duration: float,
    animation: str = None,
) -> str:
    width, height, fps = settings.width, settings.height, settings.fps
    animation = animation or settings.animation
    filters = []

    if settings.fit_mode == "contain":
        base = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
    else:
        base = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"

    if animation in {"slow_zoom", "zoom_in", "zoom_out"}:
        zoom = "min(zoom+0.00025,1.06)" if animation != "zoom_out" else "max(1.06-on*0.00025,1.0)"
        filters.append(
            f"[0:v]{base},zoompan=z='{zoom}':d={max(1, int(duration * fps))}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}[v1]"
        )
    else:
        filters.append(f"[0:v]{base},fps={fps}[v1]")
    last = "[v1]"

    if settings.filter_type == "old_film":
        filters.append(f"{last}vignette=PI/4,colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131,noise=alls=10:allf=t+u[v2]")
        last = "[v2]"
    elif settings.filter_type == "vhs":
        filters.append(f"{last}rgbashift=rh=3:bh=-3,noise=alls=14:allf=t+u[v2]")
        last = "[v2]"
    elif settings.filter_type == "b_w":
        filters.append(f"{last}hue=s=0,noise=alls=5:allf=t+u[v2]")
        last = "[v2]"
    elif settings.filter_type == "warm":
        filters.append(f"{last}eq=contrast=1.04:saturation=1.12:gamma_r=1.04:gamma_b=.96[v2]")
        last = "[v2]"

    filters.append(f"{last}format=yuv420p[v_out]")
    return ";".join(filters)


def build_static_video_command(
    ffmpeg_path: str,
    image_path: str,
    audio_path: str,
    output_path: str,
    duration: float,
    settings: StaticVideoSettings,
    vcodec: str = "libx264",
    animation: str = None,
) -> List[str]:
    command = [
        ffmpeg_path, "-y", "-loop", "1", "-t", str(duration), "-i", image_path,
        "-i", audio_path, "-filter_complex",
        build_static_filter_complex(settings, duration, animation),
        "-map", "[v_out]", "-map", "1:a", "-c:v", vcodec,
    ]
    if vcodec == "h264_nvenc":
        command += ["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "8M", "-maxrate", "12M"]
    else:
        command += ["-preset", "fast", "-crf", "21"]
    return command + ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", output_path]


class StaticVideoWorker(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, images_dir: str, audio_dir: str, output_dir: str, settings: StaticVideoSettings = None, **legacy):
        super().__init__()
        self.images_dir = Path(images_dir)
        self.audio_dir = Path(audio_dir)
        self.output_dir = Path(output_dir)
        self.settings = settings or StaticVideoSettings(
            width=int(legacy.get("width", 1080)),
            height=int(legacy.get("height", 1920)),
            animation="slow_zoom" if legacy.get("use_animation", True) else "none",
            filter_type=legacy.get("filter_type", "none"),
        )
        self._is_cancelled = False
        self._active_process = None
        self.logger = logging.getLogger("StaticVideoWorker")
        self.codec_manager = VideoCodec(ffmpeg_path=FFMPEG_PATH)

    def cancel(self):
        self._is_cancelled = True
        process = self._active_process
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

    def run(self):
        try:

            self.log.emit(t("static_video_starting"))
            audio_files = find_media_files(self.audio_dir, AUDIO_EXTENSIONS)
            image_files = find_media_files(self.images_dir, IMAGE_EXTENSIONS)
            if not audio_files or not image_files:
                self.finished_signal.emit(False, t("static_video_media_missing"))
                return
            self.output_dir.mkdir(parents=True, exist_ok=True)
            success_count = 0
            for index, audio_path in enumerate(audio_files):
                if self._is_cancelled:
                    break
                image_path = match_image(audio_path, image_files, self.settings.image_match_mode)
                if not image_path:
                    self.log.emit(
                        t("static_video_skip_no_matching_image").format(
                            audio=audio_path.name
                        )
                    )
                    continue
                output_path = self.output_dir / f"{audio_path.stem}_video.mp4"
                self.log.emit(
                    t("static_video_pair_progress").format(
                        current=index + 1,
                        total=len(audio_files),
                        audio=audio_path.name,
                        image=image_path.name,
                    )
                )
                if self._create_static_video(str(image_path), str(audio_path), str(output_path)):
                    success_count += 1
                    self.log.emit(t("static_video_ready").format(file=output_path.name))
                else:
                    self.log.emit(t("static_video_failed").format(file=audio_path.name))
                self.progress.emit(int(((index + 1) / len(audio_files)) * 100))
            message = t("static_video_summary").format(
                created=success_count, total=len(audio_files)
            )
            if self._is_cancelled:
                message = t("static_video_stopped").format(
                    created=success_count, total=len(audio_files)
                )
            self.finished_signal.emit(success_count > 0 and not self._is_cancelled, message)
        except Exception as exc:
            self.logger.error("Static video worker failed", exc_info=True)
            self.finished_signal.emit(
                False, t("static_video_error").format(error=exc)
            )

    def _get_ffprobe_path(self) -> str:
        executable = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        local = Path(FFMPEG_PATH).parent / executable
        return str(local) if local.exists() else executable

    def _get_audio_duration(self, audio_path: str) -> float:
        try:
            result = run_registered(
                [self._get_ffprobe_path(), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
                label="ffprobe_static_video_audio",
                capture_output=True, text=True, timeout=15,
            )
            return float(result.stdout.strip()) if result.returncode == 0 else 0.0
        except Exception:
            return 0.0

    def _create_static_video(self, image_path: str, audio_path: str, output_path: str) -> bool:
        duration = self._get_audio_duration(audio_path)
        if duration <= 0:
            return False
        animation = self.settings.animation
        if animation == "random":
            animation = random.choice(["none", "zoom_in", "zoom_out"])
        preferred = self.codec_manager.get_video_codec_settings(use_nvenc=True)["vcodec"]
        codecs = [preferred] if preferred == "libx264" else [preferred, "libx264"]
        registry = get_process_registry()
        for codec in codecs:
            command = build_static_video_command(
                FFMPEG_PATH, image_path, audio_path, output_path, duration,
                self.settings, codec, animation,
            )
            process = None
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                self._active_process = process
                registry.register(process, f"ffmpeg_static_video_{codec}")
                _, stderr = process.communicate(timeout=duration * 6 + 300)
                returncode = process.returncode
                if returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 1024:
                    return True
                self.logger.error("FFmpeg failed with %s: %s", codec, (stderr or "")[-2000:])
            except subprocess.TimeoutExpired:
                if process and process.poll() is None:
                    try:
                        process.terminate()
                        process.communicate(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    except Exception:
                        try:
                            if process.poll() is None:
                                process.kill()
                            process.communicate()
                        except Exception:
                            pass
                self.logger.error("FFmpeg timed out with %s", codec)
            except Exception as exc:
                self.logger.error("FFmpeg failed with %s: %s", codec, exc)
            finally:
                if process is not None:
                    registry.unregister(process)
                if self._active_process is process:
                    self._active_process = None
            if self._is_cancelled:
                break
        return False
