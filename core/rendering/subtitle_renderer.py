#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ASS subtitle construction and ffmpeg stream integration."""

import tempfile
import textwrap
import threading
import traceback
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from core.subtitle_styles import (
    apply_timing_offset,
    color_to_ass,
    normalize_subtitle_position,
    resolve_subtitle_style,
)
from core.subtitle_sync import SubtitleSynchronizer
from core.subtitle_text import join_subtitle_units, split_graphemes, split_subtitle_units


SubtitleSegment = Tuple[float, float, str]


class SubtitleRenderer:
    """Create readable ASS subtitles independently from the video renderer."""

    ALIGNMENTS = {"bottom": 2, "center": 5, "top": 8}

    def __init__(self):
        self._tl = threading.local()

    def _register_temp_file(self, path: str) -> None:
        files = getattr(self._tl, "temp_files", None)
        if files is None:
            files = []
            self._tl.temp_files = files
        files.append(str(path))

    def cleanup(self) -> None:
        """Remove ASS files after the owning FFmpeg command has completed."""
        files = getattr(self._tl, "temp_files", [])
        for filename in files:
            try:
                Path(filename).unlink(missing_ok=True)
            except OSError:
                pass
        self._tl.temp_files = []

    def apply(
        self,
        video_stream,
        text_content: Dict,
        settings: Dict,
        duration: float,
        width: int,
        height: int,
        log_callback,
        audio_path: str = None,
    ):
        """Write a temporary ASS file and attach it to an ffmpeg stream."""
        log = log_callback or (lambda _message: None)
        try:
            # The owning video renderer already announces the subtitle stage.
            # Keep this line specific so logs do not report the same stage twice.
            log("   🧩 Подготовка ASS-субтитров...")
            segments = self.build_segments(text_content, settings, duration, audio_path, log)
            if not segments:
                log("   ⚠️ Нет текста для субтитров")
                return video_stream

            ass_content = self.build_ass(segments, settings, duration, width, height)
            ass_file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".ass", delete=False)
            try:
                ass_file.write(ass_content)
                ass_file.close()
                ass_path = str(Path(ass_file.name).resolve()).replace("\\", "/")
                self._register_temp_file(ass_file.name)
                result = video_stream.filter("subtitles", filename=ass_path)
            finally:
                if not ass_file.closed:
                    ass_file.close()

            log(f"   ✅ Добавлено {len(segments)} субтитров через ASS")
            return result
        except Exception as exc:
            log(f"   ❌ Ошибка динамических субтитров: {exc}")
            log(f"   📋 Traceback: {traceback.format_exc()}")
            return video_stream

    def build_segments(
        self,
        text_content: Dict,
        settings: Dict,
        duration: float,
        audio_path: str = None,
        log_callback=None,
    ) -> List[SubtitleSegment]:
        """Choose the best available timing source and return subtitle segments."""
        log = log_callback or (lambda _message: None)
        duration = max(0.05, float(duration))
        max_words = max(1, int(settings.get("max_words_per_subtitle", 3)))
        max_chars = max(8, int(settings.get("max_chars_per_subtitle", 34)))

        if settings.get("static_subtitle"):
            full_text = self._clean_text(text_content.get("full_text", ""))
            static_lines = self._wrap_text_lines(full_text, max_chars)
            if duration <= 60 and len(full_text) <= 500 and len(static_lines) <= 6:
                return [(0.0, duration, full_text)] if full_text else []
            log(
                "   ⚠️ Статичный режим небезопасен для длинного или непереносимого текста; "
                "автоматически используем сменяемые субтитры"
            )

        timestamps = text_content.get("word_timestamps") or []
        if timestamps:
            segments = SubtitleSynchronizer.create_subtitles_from_word_timestamps(
                timestamps,
                max_words_per_subtitle=max_words,
                max_chars_per_subtitle=max_chars,
                audio_duration=duration,
            )
            if segments:
                text_content["synchronized_subtitles"] = segments
                log(f"   🎯 Используем точные word timestamps: {len(segments)} сегментов")
                return segments

        existing = self._normalize_segments(text_content.get("synchronized_subtitles") or [], duration)
        if existing:
            return existing

        full_text = self._clean_text(text_content.get("full_text", ""))
        if audio_path and Path(audio_path).exists() and full_text:
            segments = SubtitleSynchronizer.synchronize_subtitles(full_text, audio_path, max_words)
            segments = self._normalize_segments(segments, duration)
            if segments:
                text_content["synchronized_subtitles"] = segments
                log(f"   🎯 Используем синхронизацию по длительности аудио: {len(segments)} сегментов")
                return segments

        parts = [self._clean_text(part) for part in text_content.get("text_parts", [])]
        parts = [part for part in parts if part]
        if not parts and full_text:
            parts = self._split_by_words(full_text, max_words, max_chars)
        return self._proportional_segments(parts, duration)

    def build_ass(
        self,
        segments: Sequence[SubtitleSegment],
        settings: Dict,
        duration: float,
        width: int,
        height: int,
    ) -> str:
        """Build a complete ASS document from normalized subtitle segments."""
        style = resolve_subtitle_style(settings)
        position = normalize_subtitle_position(settings.get("position"), "bottom")
        alignment = self.ALIGNMENTS.get(position, 2)
        font_name = Path(str(settings.get("font_path", "Arial"))).stem or "Arial"
        font_size = max(8, int(settings.get("font_size", 48)))
        line_spacing = int(settings.get("line_spacing", 0))
        margin_h = max(10, int(width * 0.02))
        margin_v = max(10, int(height * 0.10))
        background_alpha = int((1.0 - style["bg_opacity"]) * 255)
        bold = -1 if style["bold"] else 0

        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            (
                f"Style: Default,{font_name},{font_size},{color_to_ass(style['font_color'])},"
                f"{color_to_ass(style['highlight_color'])},{color_to_ass(style['outline_color'])},"
                f"{color_to_ass(style['bg_color'], background_alpha)},{bold},0,0,0,100,100,"
                f"{line_spacing},0,{style['border_style']},{style['outline_width']},"
                f"{style['shadow_depth']},{alignment},{margin_h},{margin_h},{margin_v},1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        offset = float(settings.get("timing_offset", 0.0))
        uppercase = bool(settings.get("uppercase", False))
        max_chars = max(8, int(settings.get("max_chars_per_subtitle", 34)))
        animation = self._resolve_animation(settings, segments)
        static_mode = bool(settings.get("static_subtitle", False))

        for start, end, text in segments:
            start, end = apply_timing_offset(start, end, offset, duration)
            clean_text = self._clean_text(text)
            clean_text = clean_text.upper() if uppercase else clean_text
            if static_mode:
                clean_text = "\\N".join(
                    self._escape_ass(line)
                    for line in self._wrap_text_lines(clean_text, max_chars)
                )
                prefix = self._animation_prefix(animation, end - start, settings)
                lines.append(
                    f"Dialogue: 0,{self.format_ass_time(start)},{self.format_ass_time(end)},"
                    f"Default,,0,0,0,,{prefix}{clean_text}"
                )
            elif animation == "word_focus":
                highlighted_lines = self._create_active_word_highlighting(
                    self._escape_ass(clean_text), start, end, style, font_size
                )
                for w_start, w_end, w_text in highlighted_lines:
                    lines.append(
                        f"Dialogue: 0,{self.format_ass_time(w_start)},{self.format_ass_time(w_end)},"
                        f"Default,,0,0,0,,{w_text}"
                    )
            elif animation == "typewriter":
                for t_start, t_end, t_text in self._create_typewriter_events(
                    clean_text, start, end, settings
                ):
                    lines.append(
                        f"Dialogue: 0,{self.format_ass_time(t_start)},{self.format_ass_time(t_end)},"
                        f"Default,,0,0,0,,{t_text}"
                    )
            else:
                clean_text = self._escape_ass(clean_text)
                prefix = self._animation_prefix(animation, end - start, settings)
                lines.append(
                    f"Dialogue: 0,{self.format_ass_time(start)},{self.format_ass_time(end)},"
                    f"Default,,0,0,0,,{prefix}{clean_text}"
                )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _resolve_animation(settings: Dict, segments: Sequence[SubtitleSegment]) -> str:
        raw = settings.get("subtitle_animation")
        if raw is None:
            raw = "word_focus" if settings.get("animated_subtitle", False) else "none"
        animation = str(raw or "none").strip().lower()
        aliases = {
            "animated": "word_focus",
            "highlight": "word_focus",
            "word": "word_focus",
            "typing": "typewriter",
            "type": "typewriter",
            "off": "none",
            "static": "none",
        }
        animation = aliases.get(animation, animation)
        if animation not in {"auto", "word_focus", "fade", "typewriter", "none"}:
            animation = "word_focus"
        if animation != "auto":
            return animation

        # Content-derived choice is stable on retries, but different scripts in
        # a batch naturally receive different motion treatments.
        import hashlib

        seed = str(settings.get("subtitle_animation_seed") or "")
        sample = "|".join(str(item[2]) for item in segments[:8])
        digest = hashlib.sha256(f"{seed}|{sample}".encode("utf-8")).digest()
        roll = digest[0] % 10
        if roll < 5:
            return "word_focus"
        if roll < 8:
            return "fade"
        return "typewriter"

    @staticmethod
    def _fade_values(segment_duration: float, settings: Dict) -> Tuple[int, int]:
        duration_ms = max(50, int(float(segment_duration) * 1000))
        fade_in = max(0, min(1200, int(settings.get("subtitle_fade_in_ms", 180))))
        fade_out = max(0, min(1200, int(settings.get("subtitle_fade_out_ms", 220))))
        total = fade_in + fade_out
        budget = max(0, int(duration_ms * 0.80))
        if total > budget and total > 0:
            scale = budget / total
            fade_in = int(fade_in * scale)
            fade_out = int(fade_out * scale)
        return fade_in, fade_out

    @classmethod
    def _animation_prefix(cls, animation: str, segment_duration: float, settings: Dict) -> str:
        if animation != "fade":
            return ""
        fade_in, fade_out = cls._fade_values(segment_duration, settings)
        return f"{{\\fad({fade_in},{fade_out})}}"

    def _create_typewriter_events(
        self,
        text: str,
        start: float,
        end: float,
        settings: Dict,
    ) -> List[SubtitleSegment]:
        units = split_graphemes(text)
        if not units:
            return []

        duration = max(0.05, end - start)
        cps = max(4.0, min(60.0, float(settings.get("subtitle_typewriter_cps", 18.0))))
        reveal_duration = min(duration * 0.72, len(units) / cps)
        reveal_duration = max(min(duration * 0.28, 0.18), reveal_duration)
        reveal_duration = min(reveal_duration, max(0.03, duration - 0.05))

        # Keep ASS files compact for long words/URLs while retaining visibly
        # progressive typing. Normal two-word subtitle groups stay one glyph per
        # event; only unusually long groups are coalesced.
        # ASS stores centiseconds. Emitting more reveal events than there are
        # distinct centiseconds creates zero-duration duplicate timestamps that
        # libass drops, which made very short CJK/Korean groups look static.
        max_steps = min(48, max(1, int(reveal_duration * 100)))
        units_per_step = max(1, (len(units) + max_steps - 1) // max_steps)
        prefixes = [
            "".join(units[:index])
            for index in range(units_per_step, len(units) + 1, units_per_step)
        ]
        full_text = "".join(units)
        if not prefixes or prefixes[-1] != full_text:
            prefixes.append(full_text)

        step_duration = reveal_duration / max(1, len(prefixes))
        result: List[SubtitleSegment] = []
        for index, prefix in enumerate(prefixes):
            event_start = start + index * step_duration
            event_end = start + (index + 1) * step_duration
            result.append((event_start, min(end, event_end), self._escape_ass(prefix)))

        final_start = min(end, start + reveal_duration)
        if end - final_start >= 0.02:
            _fade_in, fade_out = self._fade_values(end - final_start, settings)
            result.append(
                (final_start, end, f"{{\\fad(0,{fade_out})}}{self._escape_ass(full_text)}")
            )
        elif result:
            fade_in, fade_out = self._fade_values(duration, settings)
            last_start, last_end, last_text = result[-1]
            result[-1] = (last_start, last_end, f"{{\\fad({min(60, fade_in)},{fade_out})}}{last_text}")
        return result

    @staticmethod
    def subtitle_position(position: str, width: int, height: int) -> Tuple[str, str]:
        position = normalize_subtitle_position(position, "bottom")
        margin_x = int(width * 0.05)
        margin_y = int(height * 0.12)
        positions = {
            "bottom": (f"{margin_x}", f"(h-text_h)-{margin_y}"),
            "top": (f"{margin_x}", f"{margin_y}"),
            "center": (f"{margin_x}", "(h-text_h)/2"),
            "bottom-left": (f"{margin_x}", f"(h-text_h)-{margin_y}"),
            "bottom-right": (f"{width - margin_x - int(width * 0.8)}", f"(h-text_h)-{margin_y}"),
            "top-left": (f"{margin_x}", f"{margin_y}"),
            "top-right": (f"{width - margin_x - int(width * 0.8)}", f"{margin_y}"),
        }
        return positions.get(position, positions["bottom"])

    @staticmethod
    def format_ass_time(seconds: float) -> str:
        # ASS timestamps have centisecond precision. Round once as an integer
        # so carries at minute/hour boundaries are handled correctly.
        total_centiseconds = max(0, int(round(float(seconds) * 100)))
        hours, remainder = divmod(total_centiseconds, 360_000)
        minutes, remainder = divmod(remainder, 6_000)
        whole_seconds, centiseconds = divmod(remainder, 100)
        return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"

    @staticmethod
    def _normalize_segments(segments: Iterable, duration: float) -> List[SubtitleSegment]:
        duration = max(0.05, float(duration))
        candidates = []
        for segment in segments:
            try:
                start, end, text = float(segment[0]), float(segment[1]), str(segment[2]).strip()
                start = max(0.0, min(start, duration))
                end = max(0.0, min(end, duration))
                if start >= duration:
                    continue
                if end - start < 0.05:
                    end = min(duration, start + 0.05)
                if text and end > start:
                    candidates.append((start, end, text))
            except (TypeError, ValueError, IndexError):
                continue

        result = []
        for start, end, text in sorted(candidates, key=lambda item: (item[0], item[1])):
            if result and start < result[-1][1]:
                start = result[-1][1]
            if end - start < 0.01:
                continue
            result.append((start, end, text))
        return result

    @staticmethod
    def _proportional_segments(parts: Sequence[str], duration: float) -> List[SubtitleSegment]:
        if not parts:
            return []
        # Combining accents and Arabic diacritics must not receive extra screen
        # time, and a joined emoji should behave as one visible symbol.
        weights = [
            max(1, len(split_graphemes(part, preserve_whitespace=False)))
            for part in parts
        ]
        total_weight = sum(weights)
        result = []
        current = 0.0
        for index, (part, weight) in enumerate(zip(parts, weights)):
            end = duration if index == len(parts) - 1 else current + duration * weight / total_weight
            result.append((current, end, part))
            current = end
        return result

    @staticmethod
    def _split_by_words(text: str, max_words: int, max_chars: int) -> List[str]:
        result, current = [], []
        words, separator = split_subtitle_units(text)
        # In CJK and unspaced Hangul each animation unit is a character, not a
        # linguistic word. Applying max_words=2/3 there creates unreadably
        # rapid two- or three-character flashes; the character-width limit is
        # the meaningful constraint for those scripts.
        max_units = min(max_chars, max(6, max_words * 3)) if separator == "" else max_words
        for word in words:
            candidate = join_subtitle_units(current + [word], separator)
            if current and (len(current) >= max_units or len(candidate) > max_chars):
                result.append(join_subtitle_units(current, separator))
                current = [word]
            else:
                current.append(word)
        if current:
            result.append(join_subtitle_units(current, separator))
        return result

    @staticmethod
    def _karaoke_text(text: str, duration: float) -> str:
        words, separator = split_subtitle_units(text)
        centiseconds = max(1, int(max(0.05, duration) * 100 / max(1, len(words))))
        return separator.join(f"{{\\k{centiseconds}}}{word}" for word in words)

    @staticmethod
    def _wrap_ass(text: str, max_chars: int, max_lines: int) -> str:
        # build_segments prevents unsafe static blocks from reaching this path.
        # Do not silently truncate content if build_ass is called directly.
        return "\\N".join(SubtitleRenderer._wrap_text_lines(text, max_chars))

    @staticmethod
    def _wrap_text_lines(text: str, max_chars: int) -> list[str]:
        units, separator = split_subtitle_units(text)
        if not units:
            return []
        if separator == '':
            return [
                ''.join(units[index:index + max_chars])
                for index in range(0, len(units), max_chars)
            ]
        return textwrap.wrap(
            text,
            width=max_chars,
            break_long_words=True,
            break_on_hyphens=False,
        )

    @staticmethod
    def _clean_text(text) -> str:
        return " ".join(str(text or "").split())

    def _create_active_word_highlighting(
        self,
        text: str,
        start: float,
        end: float,
        style: dict,
        font_size: int,
    ) -> list:
        words, separator = split_subtitle_units(text)
        if not words:
            return []
        
        word_dur = (end - start) / len(words)
        highlight_color_ass = color_to_ass(style['highlight_color'])
        font_color_ass = color_to_ass(style['font_color'])
        pop_style = style.get("preset") == "tiktok"
        contains_rtl = any(
            0x0590 <= ord(character) <= 0x08FF
            for character in text
        )
        
        dialogue_lines = []
        for idx in range(len(words)):
            w_start = start + idx * word_dur
            w_end = w_start + word_dur
            
            parts = []
            for i, word in enumerate(words):
                if i == idx:
                    # Active word: color = highlight, size = larger. TikTok Punch adds a quick scale pop.
                    if contains_rtl:
                        # Size changes can reorder/reflow bidirectional text on
                        # every frame. Preserve layout and animate by color.
                        parts.append(
                            f"{{\\c{highlight_color_ass}}}{word}"
                            f"{{\\c{font_color_ass}}}"
                        )
                    elif pop_style:
                        parts.append(
                            f"{{\\c{highlight_color_ass}\\fs{font_size + 18}\\bord{max(1, int(style.get('outline_width', 7)) + 2)}"
                            f"\\shad{max(0, int(style.get('shadow_depth', 5)) + 1)}\\fscx122\\fscy122"
                            f"\\t(0,90,\\fscx100\\fscy100)}}{word}"
                            f"{{\\c{font_color_ass}\\fs{font_size}\\bord{style.get('outline_width', 7)}\\shad{style.get('shadow_depth', 5)}}}"
                        )
                    else:
                        parts.append(f"{{\\c{highlight_color_ass}\\fs{font_size + 15}}}{word}{{\\c{font_color_ass}\\fs{font_size}}}")
                else:
                    parts.append(word)
            
            w_text = separator.join(parts)
            dialogue_lines.append((w_start, w_end, w_text))
            
        return dialogue_lines

    @staticmethod
    def _escape_ass(text: str) -> str:
        return text.replace("\\", "\\\\").replace("\n", "\\N").replace("{", r"\{").replace("}", r"\}")
