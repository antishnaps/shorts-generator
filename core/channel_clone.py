"""YouTube channel analysis and end-to-end channel remake generation."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

from core.gemini_client import GeminiClient
from core.sound_design import SoundDesignPlanner
from core.text_loader import TextFile, TextLoader
from core.utils import cleanup_generated_folder, get_enabled_metadata_dirs, normalize_language
from core.vosk_stt import get_vosk_word_timestamps


LogCallback = Callable[[str], None]


@dataclass(frozen=True)
class ChannelVideo:
    video_id: str
    title: str
    url: str
    duration: int = 0
    view_count: int = 0
    description: str = ""
    kind: str = "video"
    upload_date: str = ""


def _safe_name(value: str, fallback: str = "video") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned[:100] or fallback).strip()


class ChannelAnalyzer:
    """Read real channel uploads without mistaking channel tabs for videos."""

    def __init__(self, log_callback: Optional[LogCallback] = None):
        self.log = log_callback or (lambda _message: None)

    @staticmethod
    def _channel_sections(channel_url: str) -> list[tuple[str, str]]:
        parts = urlsplit(channel_url.strip())
        path = parts.path.rstrip("/")
        markers = ("/@", "/channel/", "/c/", "/user/")
        if "youtube.com" not in parts.netloc.lower() or not any(marker in path for marker in markers):
            return [("video", channel_url.strip())]

        for suffix in ("/videos", "/shorts", "/streams", "/live", "/featured"):
            if path.endswith(suffix):
                path = path[: -len(suffix)]
                break
        base = urlunsplit((parts.scheme or "https", parts.netloc, path, "", ""))
        return [
            ("video", f"{base}/videos"),
            ("short", f"{base}/shorts"),
            ("live", f"{base}/streams"),
        ]

    @staticmethod
    def _is_container(entry: dict) -> bool:
        title = str(entry.get("title") or "").strip().lower()
        url = str(entry.get("url") or entry.get("webpage_url") or "").strip().lower()
        return (
            entry.get("_type") in {"playlist", "multi_video"}
            or title.endswith((" - videos", " - shorts", " - live", " - streams"))
            or url.endswith(("/videos", "/shorts", "/streams", "/live"))
        )

    @staticmethod
    def _cookie_file() -> Optional[str]:
        from core.youtube import has_current_youtube_auth_cookies

        path = Path(__file__).resolve().parents[1] / "youtube_cookies.txt"
        return str(path) if has_current_youtube_auth_cookies(str(path)) else None

    def _extract_section(self, yt_dlp, url: str, limit: int) -> list[dict]:
        from core.youtube_mixer import youtube_access_temporarily_blocked

        if youtube_access_temporarily_blocked():
            raise RuntimeError(
                "YouTube временно остановлен после антибот-проверки. "
                "Импортируйте свежие cookies и повторите анализ."
            )
        errors: list[str] = []

        class QuietLogger:
            def debug(self, _message):
                return None

            def warning(self, _message):
                return None

            def error(self, message):
                errors.append(str(message))

        options = {
            "extract_flat": True,
            "playlistend": limit,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "skip_download": True,
            "socket_timeout": 30,
            "retries": 1,
            "logger": QuietLogger(),
        }
        cookie_file = self._cookie_file()
        if cookie_file:
            options["cookiefile"] = cookie_file
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            from core.youtube_mixer import is_youtube_bot_challenge, mark_youtube_bot_block

            message = str(exc)
            if is_youtube_bot_challenge(message):
                mark_youtube_bot_block()
            if is_youtube_bot_challenge(message) or "cookies" in message.lower():
                raise RuntimeError(
                    "YouTube запросил подтверждение входа. Добавьте актуальный "
                    "youtube_cookies.txt в папку программы и повторите анализ."
                ) from exc
            raise
        auth_error = next(
            (
                message
                for message in errors
                if "Sign in to confirm" in message or "cookies" in message.lower()
            ),
            None,
        )
        if not info and auth_error:
            from core.youtube_mixer import is_youtube_bot_challenge, mark_youtube_bot_block

            if is_youtube_bot_challenge(auth_error):
                mark_youtube_bot_block()
            raise RuntimeError(
                "YouTube запросил подтверждение входа. Добавьте актуальный "
                "youtube_cookies.txt в папку программы и повторите анализ."
            )
        entries = list((info or {}).get("entries") or [])
        missing_duration = [entry for entry in entries if entry and not entry.get("duration")]
        if missing_duration:
            def enrich(entry):
                video_id = str(entry.get("id") or "").strip()
                video_url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
                if video_url and not video_url.startswith(("http://", "https://")) and video_id:
                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                detail_options = {
                    "quiet": True,
                    "no_warnings": True,
                    "ignoreerrors": True,
                    "skip_download": True,
                    "noplaylist": True,
                    "socket_timeout": 20,
                    "retries": 1,
                }
                if cookie_file:
                    detail_options["cookiefile"] = cookie_file
                try:
                    with yt_dlp.YoutubeDL(detail_options) as detail_ydl:
                        details = detail_ydl.extract_info(video_url, download=False) or {}
                    merged = dict(entry)
                    for key in ("duration", "view_count", "description", "upload_date", "webpage_url"):
                        if details.get(key) is not None:
                            merged[key] = details[key]
                    return merged
                except Exception:
                    return entry

            with ThreadPoolExecutor(max_workers=min(2, len(missing_duration))) as executor:
                enriched = iter(executor.map(enrich, missing_duration))
                entries = [next(enriched) if entry in missing_duration else entry for entry in entries]
        return entries

    def list_videos(self, channel_url: str, limit: int = 50) -> list[ChannelVideo]:
        if not str(channel_url or "").strip():
            raise ValueError("Укажите ссылку на YouTube-канал или плейлист.")
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("Для анализа каналов требуется yt-dlp.") from exc

        limit = max(1, min(int(limit), 1000))
        self.log("Анализируем обычные видео, Shorts и стримы...")
        videos: list[ChannelVideo] = []
        seen: set[str] = set()
        auth_error: Optional[RuntimeError] = None

        for kind, section_url in self._channel_sections(channel_url):
            try:
                entries = self._extract_section(yt_dlp, section_url, limit)
            except RuntimeError as exc:
                auth_error = exc
                self.log(str(exc))
                break
            except Exception as exc:
                self.log(f"Раздел {kind} недоступен: {str(exc)[:160]}")
                continue

            found = 0
            for entry in entries:
                if not entry or self._is_container(entry):
                    continue
                video_id = str(entry.get("id") or "").strip()
                raw_url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
                if raw_url and not raw_url.startswith(("http://", "https://")) and video_id:
                    raw_url = f"https://www.youtube.com/watch?v={video_id}"
                identity = video_id or raw_url
                if not raw_url or not identity or identity in seen:
                    continue
                seen.add(identity)
                videos.append(
                    ChannelVideo(
                        video_id=video_id or raw_url.rsplit("/", 1)[-1],
                        title=str(entry.get("title") or "Без названия"),
                        url=raw_url,
                        duration=int(entry.get("duration") or 0),
                        view_count=int(entry.get("view_count") or 0),
                        description=str(entry.get("description") or ""),
                        kind=kind,
                        upload_date=str(entry.get("upload_date") or ""),
                    )
                )
                found += 1
            if found:
                self.log(f"Раздел {kind}: {found}")

        if not videos:
            if auth_error:
                raise auth_error
            raise RuntimeError(
                "YouTube не вернул ни одного ролика. Проверьте ссылку и доступность канала."
            )
        self.log(f"Всего найдено реальных роликов: {len(videos)}")
        return videos


class ChannelClonePipeline:
    """Download, transcribe, rewrite, plan and render selected channel videos."""

    VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi"}
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

    def __init__(
        self,
        output_root: Path | str,
        api_key: str = "",
        language: str = "Russian",
        visual_sources: Optional[Iterable[str]] = None,
        log_callback: Optional[LogCallback] = None,
    ):
        self.output_root = Path(output_root)
        self.api_key = str(api_key or "").strip()
        self.language = normalize_language(language)
        self.visual_sources = list(visual_sources or ["youtube", "pexels", "local"])
        self.log = log_callback or (lambda _message: None)

    def prepare_video(self, video: ChannelVideo) -> Path:
        package_dir = self.output_root / _safe_name(f"{video.video_id} {video.title}")
        package_dir.mkdir(parents=True, exist_ok=True)
        ready_texts = self.output_root / "ready_texts"
        ready_texts.mkdir(parents=True, exist_ok=True)

        self.log(f"Скачиваем оригинал: {video.title}")
        original_path = self._download_video(video.url, package_dir)
        self.log("Создаём транскрипцию...")
        raw_transcript, timestamps = self._transcribe(original_path)
        self.log("Исправляем транскрипцию и строим план нового ролика...")
        polished_script = (
            self._polish_transcript(raw_transcript, video)
            if raw_transcript
            else self._create_script_without_transcript(video)
        )
        visual_plan = self._build_visual_plan(polished_script, video)
        sound_plan = [
            asdict(event)
            for event in SoundDesignPlanner.plan(
                polished_script, video.duration or max(15, len(polished_script) / 14), "medium"
            )
        ]

        (package_dir / "transcript_raw.txt").write_text(raw_transcript, encoding="utf-8")
        (package_dir / "script_polished.txt").write_text(polished_script, encoding="utf-8")
        ready_path = ready_texts / f"{_safe_name(video.video_id)}.txt"
        ready_path.write_text(polished_script, encoding="utf-8")
        (package_dir / "word_timestamps.json").write_text(
            json.dumps(timestamps, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (package_dir / "visual_plan.json").write_text(
            json.dumps(visual_plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (package_dir / "sound_design_plan.json").write_text(
            json.dumps(sound_plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest = {
            "version": 2,
            "source": asdict(video),
            "original_path": str(original_path),
            "language": self.language,
            "visual_sources": self.visual_sources,
            "ready_text_path": str(ready_path),
            "generation_settings": {
                "custom_text_mode": True,
                "visual_source_mode": "smart_mix",
                "youtube_enabled": "youtube" in self.visual_sources,
                "local_enabled": "local" in self.visual_sources,
                "pexels_enabled": "pexels" in self.visual_sources,
                "pixabay_enabled": False,
                "wikimedia_enabled": "wikimedia" in self.visual_sources,
            },
        }
        (package_dir / "remake_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.log(f"Анализ ролика готов: {package_dir.name}")
        return package_dir

    def prepare_many(self, videos: Iterable[ChannelVideo]) -> list[Path]:
        return [self.prepare_video(video) for video in videos]

    @classmethod
    def _package_source_video(cls, package_dir: Path) -> Optional[Path]:
        manifest_path = package_dir / "remake_manifest.json"
        if manifest_path.is_file():
            try:
                value = json.loads(manifest_path.read_text(encoding="utf-8")).get("original_path")
                path = Path(str(value or ""))
                if path.is_file():
                    return path
            except Exception:
                pass
        candidates = [
            path for path in (package_dir / "source").glob("*")
            if path.is_file() and path.suffix.lower() in cls.VIDEO_EXTENSIONS
        ]
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None

    @classmethod
    def _assign_first_shot_images(
        cls,
        videos: list[ChannelVideo],
        folder: Path | str | None,
    ) -> list[Optional[Path]]:
        if not str(folder or "").strip():
            return [None] * len(videos)
        root = Path(str(folder or ""))
        if not root.is_dir():
            return [None] * len(videos)
        images = sorted(
            (
                path for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in cls.IMAGE_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
        if not images:
            return [None] * len(videos)

        assigned: list[Optional[Path]] = [None] * len(videos)
        unused = list(images)
        # A file containing a YouTube video id wins. Everything else is mapped
        # alphabetically to the remaining selected videos (01.png, 02.png...).
        for index, video in enumerate(videos):
            video_id = str(video.video_id or "").casefold()
            match = next(
                (path for path in unused if video_id and video_id in path.stem.casefold()),
                None,
            )
            if match:
                assigned[index] = match
                unused.remove(match)
        for index in range(len(assigned)):
            if assigned[index] is None and unused:
                assigned[index] = unused.pop(0)
        return assigned

    def generate_remakes(
        self,
        videos: Iterable[ChannelVideo],
        generation_settings: dict,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> list[Path]:
        selected = list(videos)
        if not selected:
            return []

        settings = dict(generation_settings or {})
        remake_options = dict(settings.get("channel_remake_settings") or {})
        use_source_video = bool(remake_options.get("use_source_video", False))
        first_shots_enabled = bool(remake_options.get("first_shots_enabled", False))
        first_shot_images = self._assign_first_shot_images(
            selected,
            remake_options.get("first_shots_folder") if first_shots_enabled else None,
        )
        if first_shots_enabled:
            mapped_count = sum(path is not None for path in first_shot_images)
            self.log(
                f"🖼️ Пользовательские первые кадры: {mapped_count}/{len(selected)}"
            )

        self.output_root.mkdir(parents=True, exist_ok=True)
        analysis_pipeline = ChannelClonePipeline(
            self.output_root / "_remake_analysis",
            api_key=self.api_key,
            language=self.language,
            visual_sources=self.visual_sources,
            log_callback=self.log,
        )
        loader = TextLoader()
        texts: list[TextFile] = []
        for index, video in enumerate(selected, 1):
            self.log(f"Подготавливаем {index}/{len(selected)}: {video.title}")
            package = analysis_pipeline.prepare_video(video)
            script_path = package / "script_polished.txt"
            text = script_path.read_text(encoding="utf-8").strip()
            valid, error = loader.validate_text(text)
            if not valid:
                raise RuntimeError(f"Сценарий для «{video.title}» не готов: {error}")
            source_video = self._package_source_video(package) if use_source_video else None
            if use_source_video and source_video is None:
                self.log(
                    f"   ⚠️ Исходный видеоряд для «{video.title}» не найден; "
                    "будут использованы выбранные резервные источники."
                )
            texts.append(
                TextFile(
                    filename=script_path.name,
                    filepath=script_path,
                    title=video.title,
                    text=text,
                    duration=loader.calculate_duration(text),
                    valid=True,
                    remake_source_video=str(source_video) if source_video else None,
                    first_shot_image_path=(
                        str(first_shot_images[index - 1])
                        if first_shot_images[index - 1]
                        else None
                    ),
                )
            )
            if progress_callback:
                progress_callback(int(index / len(selected) * 45))

        from core.generator import ShortsGenerator

        source_data = dict(settings.get("source_data") or {})
        source_data.update(
            {
                "theme": source_data.get("theme") or "Channel remakes",
                "language": self.language,
                "custom_texts": texts,
                "strict_text_theme": True,
            }
        )
        video_settings = dict(settings.get("video_settings") or {})
        video_settings["duration"] = max(text.duration for text in texts)
        if first_shots_enabled and any(first_shot_images):
            video_settings["first_shot_from_pool"] = True
        youtube_settings = dict(settings.get("youtube_mixer_settings") or {})
        youtube_settings.update(
            {
                "enabled": bool(self.visual_sources) or use_source_video,
                "source_mode": "smart_mix",
                "enable_youtube": "youtube" in self.visual_sources,
                "enable_local_videos": "local" in self.visual_sources,
                "enable_pexels": "pexels" in self.visual_sources,
                "enable_pixabay_videos": False,
                "enable_wikimedia_videos": "wikimedia" in self.visual_sources,
                "remake_source_video_enabled": use_source_video,
                "remake_first_shot_only": bool(first_shots_enabled and any(first_shot_images)),
            }
        )
        if use_source_video or (first_shots_enabled and any(first_shot_images)):
            youtube_settings["video_ratio"] = 1.0
        if use_source_video:
            youtube_settings["smart_clip_matching"] = False
        before = {path.resolve() for path in self.output_root.glob("*.mp4")}
        common = {
            "source_data": source_data,
            "num_videos": len(texts),
            "music_path": settings.get("music_path"),
            "api_key": settings.get("api_key") or self.api_key,
            "video_settings": video_settings,
            "output_path": str(self.output_root),
            "progress_callback": (
                (lambda value: progress_callback(45 + int(value * 0.55)))
                if progress_callback
                else None
            ),
            "log_callback": self.log,
            "subtitle_settings": settings.get("subtitle_settings") or {},
            "media_path": settings.get("media_path") or None,
            "use_ai_image_generation": bool(
                settings.get("use_ai_image_generation", settings.get("use_ai_images", True))
            ),
            "strict_theme_following": bool(settings.get("strict_theme_following", True)),
            "google_ai_api_key": settings.get("google_ai_api_key") or self.api_key,
            "overlay_settings": settings.get("overlay_settings") or {},
            "use_triple_template": False,
            "audio_settings": settings.get("audio_settings") or {},
            "unlimited_images": bool(settings.get("unlimited_images", False)),
            "num_unique_images": int(settings.get("num_unique_images", 5)),
            "enable_scene_variety": bool(settings.get("enable_scene_variety", True)),
            "image_model": settings.get("image_model", "gemini-3.1-flash-image"),
            "veo3_settings": settings.get("veo3_settings") or {"enabled": False},
            "youtube_mixer_settings": youtube_settings,
            "custom_images_folder": settings.get("custom_images_folder") or None,
            "use_only_custom_images": bool(settings.get("use_only_custom_images", False)),
            "use_image_cache": bool(settings.get("use_image_cache", False)),
            "save_to_image_cache": bool(settings.get("save_to_image_cache", False)),
            "image_pool_settings": settings.get("image_pool_settings") or {},
            "avatar_settings": settings.get("avatar_settings") or {"enabled": False},
            "final_output_settings": settings,
        }
        generator = ShortsGenerator()
        if settings.get("enable_parallel") and len(texts) > 1:
            generator.generate_shorts_parallel(
                **common,
                num_workers=min(int(settings.get("num_workers", 2)), 2),
                force_parallel=True,
            )
        else:
            generator.generate_shorts(**common)
        if progress_callback:
            progress_callback(100)
        after = {path.resolve() for path in self.output_root.glob("*.mp4")}
        cleanup_generated_folder(
            str(self.output_root),
            self.log,
            keep_images=False,
            skip_youtube_clips=False,
            keep_metadata=True,
            metadata_dirs_to_keep=get_enabled_metadata_dirs(settings),
        )
        return sorted(after - before)

    def _download_video(self, url: str, package_dir: Path) -> Path:
        from core.youtube_mixer import (
            YOUTUBE_DEFAULT_MIN_HEIGHT,
            build_youtube_format_selector,
            is_youtube_bot_challenge,
            mark_youtube_bot_block,
            probe_video_dimensions,
            youtube_access_temporarily_blocked,
        )

        if youtube_access_temporarily_blocked():
            raise RuntimeError(
                "YouTube временно остановлен после антибот-проверки. "
                "Импортируйте свежие cookies и повторите ремейк."
            )
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("Для скачивания роликов требуется yt-dlp.") from exc

        source_dir = package_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        options = {
            "format": build_youtube_format_selector(YOUTUBE_DEFAULT_MIN_HEIGHT),
            "outtmpl": str(source_dir / "original.%(ext)s"),
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitlesformat": "vtt",
            "subtitleslangs": ["en", "ru", "es", "de", "fr", "pt", "ja", "ko"],
            "socket_timeout": 20,
            "retries": 1,
            "fragment_retries": 1,
            "extractor_retries": 1,
            "concurrent_fragment_downloads": 2,
            "cachedir": False,
        }
        cookie_file = ChannelAnalyzer._cookie_file()
        if cookie_file:
            options["cookiefile"] = cookie_file
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([url])
        except Exception as exc:
            if is_youtube_bot_challenge(str(exc)):
                mark_youtube_bot_block()
                raise RuntimeError(
                    "YouTube запросил антибот-проверку. Импортируйте свежие cookies "
                    "в настройках и повторите ремейк."
                ) from exc
            raise
        files = [
            path
            for path in source_dir.iterdir()
            if path.is_file() and path.suffix.lower() in self.VIDEO_EXTENSIONS
        ]
        if not files:
            raise RuntimeError("yt-dlp завершил работу, но видеофайл не найден.")
        downloaded = max(files, key=lambda path: path.stat().st_mtime)
        dimensions = probe_video_dimensions(downloaded)
        if not dimensions or dimensions[1] < YOUTUBE_DEFAULT_MIN_HEIGHT:
            downloaded.unlink(missing_ok=True)
            actual = f"{dimensions[1]}p" if dimensions else "неизвестно"
            raise RuntimeError(
                "YouTube вернул слишком низкое качество "
                f"({actual}); требуется не ниже {YOUTUBE_DEFAULT_MIN_HEIGHT}p."
            )
        return downloaded

    def _transcribe(self, original_path: Path) -> tuple[str, list[dict]]:
        caption_text = self._read_downloaded_captions(original_path.parent)
        if caption_text:
            self.log("Используем субтитры YouTube как наиболее точную транскрипцию.")
            return caption_text, []
        timestamps = get_vosk_word_timestamps(
            str(original_path), self.language, self.log, original_text=""
        ) or []
        transcript = " ".join(str(item.get("word", "")).strip() for item in timestamps).strip()
        if not transcript:
            self.log("Речь и субтитры не найдены; создаём новый сценарий по теме ролика.")
        return transcript, timestamps

    @staticmethod
    def _read_downloaded_captions(source_dir: Path) -> str:
        lines: list[str] = []
        previous = ""
        for caption in sorted(source_dir.glob("*.vtt")):
            for raw_line in caption.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw_line.strip()
                if (
                    not line
                    or line == "WEBVTT"
                    or "-->" in line
                    or line.isdigit()
                    or line.startswith(("Kind:", "Language:"))
                ):
                    continue
                line = re.sub(r"<[^>]+>", "", line)
                line = re.sub(r"\s+", " ", line).strip()
                if line and line != previous:
                    lines.append(line)
                    previous = line
            if lines:
                break
        return " ".join(lines).strip()

    def _create_script_without_transcript(self, video: ChannelVideo) -> str:
        if not self.api_key:
            raise RuntimeError(
                f"В ролике «{video.title}» не найдена речь. Для создания нового сценария "
                "нужен Gemini API key."
            )
        prompt = (
            "Создай самостоятельный оригинальный сценарий для нового видео по теме исходного "
            "ролика. Не утверждай, что видел исходные кадры, не упоминай канал и не копируй "
            "название дословно. Сценарий должен быть пригоден для озвучки и занимать примерно "
            f"{max(30, video.duration or 60)} секунд.\n\n"
            f"Тема исходного ролика: {video.title}\nОписание: {video.description}"
        )
        result = GeminiClient(self.api_key, self.log).generate_text(prompt, temperature=0.7)
        if not result.success or not str(result.raw_text or "").strip():
            raise RuntimeError(f"Не удалось создать сценарий для «{video.title}»: {result.error}")
        return result.raw_text.strip()

    @staticmethod
    def _rewrite_similarity(source: str, candidate: str, ngram_size: int = 4) -> float:
        def ngrams(value: str) -> set[tuple[str, ...]]:
            words = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", str(value or "").casefold())
            if len(words) < ngram_size:
                return {tuple(words)} if words else set()
            return {
                tuple(words[index:index + ngram_size])
                for index in range(len(words) - ngram_size + 1)
            }

        source_ngrams = ngrams(source)
        candidate_ngrams = ngrams(candidate)
        if not source_ngrams or not candidate_ngrams:
            return 0.0
        return len(source_ngrams & candidate_ngrams) / max(
            1, min(len(source_ngrams), len(candidate_ngrams))
        )

    def _polish_transcript(self, transcript: str, video: ChannelVideo) -> str:
        source = transcript.strip()
        if not source:
            return ""
        if not self.api_key:
            raise RuntimeError(
                f"Для полноценного рерайта «{video.title}» нужен Gemini API key."
            )

        base_prompt = (
            "Сделай ПОЛНОЦЕННЫЙ РЕРАЙТ транскрипции как самостоятельный сценарий нового видео. "
            "Сохрани проверяемые факты, имена и примерно ту же длительность, но заново построй "
            "вступление, порядок смысловых блоков, переходы и формулировки. Не делай поверхностную "
            "замену синонимов и не копируй длинные фразы из транскрипции. Удали повторы, мусор "
            "распознавания, обращения к аудитории исходного канала и фирменные фразы автора. "
            f"Пиши только готовый текст для озвучки на языке {self.language}, без пояснений редактора.\n\n"
            f"Исходное название: {video.title}\n\nТранскрипция:\n{source}"
        )
        client = GeminiClient(self.api_key, self.log)
        result = client.generate_text(base_prompt, temperature=0.65)
        if not result.success or not str(result.raw_text or "").strip():
            raise RuntimeError(f"Не удалось переписать «{video.title}»: {result.error}")

        rewritten = result.raw_text.strip()
        similarity = self._rewrite_similarity(source, rewritten)
        length_ratio = len(rewritten) / max(1, len(source))
        if similarity > 0.42 or not 0.55 <= length_ratio <= 1.55:
            self.log(
                f"   🔄 Первый рерайт слишком близкий/короткий "
                f"(совпадение={similarity:.0%}, длина={length_ratio:.0%}); повторяем."
            )
            retry_prompt = (
                base_prompt
                + "\n\nПредыдущая попытка получилась слишком близкой к исходной или нарушила длину. "
                "Пересобери сценарий заметно глубже, сохранив только факты и смысл."
            )
            retry = client.generate_text(retry_prompt, temperature=0.8)
            if retry.success and str(retry.raw_text or "").strip():
                rewritten = retry.raw_text.strip()
                similarity = self._rewrite_similarity(source, rewritten)
        self.log(f"✅ Полный рерайт готов; совпадение фраз: {similarity:.0%}")
        return rewritten

    def _build_visual_plan(self, script: str, video: ChannelVideo) -> dict:
        fallback = {
            "summary": video.title,
            "style": "dynamic documentary short",
            "search_queries": [video.title],
            "sources": self.visual_sources,
            "notes": "План создан локально; уточните запросы перед генерацией.",
        }
        if not self.api_key:
            return fallback
        schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "style": {"type": "string"},
                "search_queries": {"type": "array", "items": {"type": "string"}},
                "shot_ideas": {"type": "array", "items": {"type": "string"}},
                "avoid": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "style", "search_queries", "shot_ideas"],
        }
        prompt = (
            "Создай компактный план нового видеоряда по сценарию. Он должен передавать тот же смысл, "
            "но не копировать исходные кадры. Дай конкретные поисковые запросы для YouTube, Pexels "
            "и локальной библиотеки, а также идеи смены кадров.\n\n"
            f"Исходное название: {video.title}\nСценарий:\n{script}"
        )
        result = GeminiClient(self.api_key, self.log).generate_json(
            prompt, custom_schema=schema, temperature=0.35
        )
        if not result.success or not isinstance(result.data, dict):
            return fallback
        plan = dict(result.data)
        plan["sources"] = self.visual_sources
        return plan
