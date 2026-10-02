from core.rendering.subtitle_renderer import SubtitleRenderer
from core.subtitle_text import split_graphemes
from core.video_renderer import VideoRenderer
import pytest


def test_word_timestamps_are_preferred_and_cached():
    renderer = SubtitleRenderer()
    content = {
        "full_text": "hello world",
        "word_timestamps": [
            {"word": "hello", "start": 0.0, "end": 0.4},
            {"word": "world", "start": 0.5, "end": 0.9},
        ],
    }
    segments = renderer.build_segments(content, {"max_words_per_subtitle": 1}, 1.0)
    assert [text for _, _, text in segments] == ["hello", "world"]
    assert content["synchronized_subtitles"] == segments


def test_fallback_segments_cover_full_duration():
    renderer = SubtitleRenderer()
    segments = renderer.build_segments(
        {"text_parts": ["short", "a much longer phrase"]},
        {},
        10.0,
    )
    assert segments[0][0] == 0.0
    assert segments[-1][1] == 10.0
    assert segments[1][1] - segments[1][0] > segments[0][1] - segments[0][0]


def test_ass_contains_selected_style_and_karaoke():
    renderer = SubtitleRenderer()
    ass = renderer.build_ass(
        [(0.0, 1.0, "hello world")],
        {
            "style_preset": "boxed",
            "font_path": "Impact",
            "font_size": 60,
            "animated_subtitle": True,
            "uppercase": True,
        },
        duration=1.0,
        width=1080,
        height=1920,
    )
    assert "Style: Default,Impact,60" in ass
    assert ",3," in ass
    assert r"\fs75" in ass  # Pop size: font_size + 15
    assert r"HELLO" in ass
    assert r"WORLD" in ass


def test_static_subtitle_is_wrapped_and_spans_duration():
    renderer = SubtitleRenderer()
    content = {"full_text": "one two three four five six seven eight"}
    segments = renderer.build_segments(content, {"static_subtitle": True}, 4.0)
    ass = renderer.build_ass(
        segments,
        {"static_subtitle": True, "max_chars_per_subtitle": 8},
        4.0,
        720,
        1280,
    )
    assert r"\N" in ass
    assert "0:00:04.00" in ass


def test_position_aliases_and_legacy_position_helper():
    renderer = SubtitleRenderer()
    ass = renderer.build_ass([(0, 1, "test")], {"position": "Вверху"}, 1, 1080, 1920)
    localized_ass = renderer.build_ass([(0, 1, "test")], {"position": "Arriba"}, 1, 1080, 1920)
    assert ",8," in ass
    assert ",8," in localized_ass
    assert renderer.subtitle_position("center", 1000, 1000)[1] == "(h-text_h)/2"


def test_video_renderer_delegates_subtitles_to_component(monkeypatch):
    renderer = VideoRenderer()
    expected = object()
    monkeypatch.setattr(renderer._subtitle_renderer, "apply", lambda *args, **kwargs: expected)
    result = renderer._apply_dynamic_subtitles(
        object(), {"full_text": "test"}, {}, 1.0, 1080, 1920, lambda _message: None
    )
    assert result is expected


def test_subtitle_renderer_cleans_registered_ass_files(tmp_path):
    renderer = SubtitleRenderer()
    ass_path = tmp_path / "temporary.ass"
    ass_path.write_text("subtitle", encoding="utf-8")
    renderer._register_temp_file(str(ass_path))

    renderer.cleanup()

    assert not ass_path.exists()


@pytest.mark.parametrize("text", [
    "Это проверка динамических субтитров",
    "This checks animated subtitles",
    "Esto comprueba subtítulos animados",
    "Ceci vérifie les sous-titres animés",
    "Dies prüft animierte Untertitel",
    "这会检查动态字幕是否变化",
    "これは字幕が動くか確認します",
    "안녕하세요오늘은자막움직임을확인합니다",
    "Isto verifica legendas animadas",
    "Questo verifica i sottotitoli animati",
    "यह गतिशील उपशीर्षक जाँचता है",
    "هذا يختبر حركة الترجمة",
])
def test_supported_language_text_produces_changing_animated_events(text):
    renderer = SubtitleRenderer()
    segments = renderer.build_segments(
        {"full_text": text},
        {"max_words_per_subtitle": 2, "max_chars_per_subtitle": 18},
        6.0,
    )
    ass = renderer.build_ass(
        segments,
        {"animated_subtitle": True, "max_chars_per_subtitle": 18},
        6.0,
        1080,
        1920,
    )

    assert len(segments) >= 2
    assert segments[0][0] == 0.0
    assert segments[-1][1] == 6.0
    assert ass.count("Dialogue:") >= 2


def test_unspaced_korean_word_timestamp_expands_into_animation_units():
    renderer = SubtitleRenderer()
    segments = renderer.build_segments(
        {
            "full_text": "안녕하세요오늘입니다",
            "word_timestamps": [
                {"word": "안녕하세요오늘입니다", "start": 0.0, "end": 3.0}
            ],
        },
        {"max_words_per_subtitle": 3, "max_chars_per_subtitle": 12},
        3.0,
    )

    assert len(segments) >= 3
    assert all(" " not in text for _start, _end, text in segments)


def test_long_unspaced_static_korean_falls_back_to_changing_subtitles():
    renderer = SubtitleRenderer()
    text = "이문장은공백없이길게이어지는한국어자막을검증하기위한내용입니다" * 4

    segments = renderer.build_segments(
        {"full_text": text},
        {"static_subtitle": True, "max_chars_per_subtitle": 12},
        30.0,
    )

    assert len(segments) > 1
    assert all(len(segment_text) <= 12 for _start, _end, segment_text in segments)
    assert any(len(segment_text) > 3 for _start, _end, segment_text in segments)
    assert "".join(segment_text for _start, _end, segment_text in segments) == text


def test_short_static_korean_wraps_without_dropping_characters():
    renderer = SubtitleRenderer()
    text = "공백없는한국어자막줄바꿈"
    ass = renderer.build_ass(
        [(0.0, 4.0, text)],
        {"static_subtitle": True, "max_chars_per_subtitle": 6},
        4.0,
        720,
        1280,
    )
    dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
    rendered_text = dialogue.split(",", 9)[-1].replace(r"\N", "")
    rendered_text = rendered_text.removeprefix(r"{\fad(120,120)}")

    assert r"\N" in dialogue
    assert rendered_text == text


def test_arabic_animation_does_not_resize_individual_words():
    renderer = SubtitleRenderer()
    ass = renderer.build_ass(
        [(0.0, 2.0, "هذا اختبار حركة الترجمة")],
        {"animated_subtitle": True, "font_size": 60},
        2.0,
        1080,
        1920,
    )

    assert r"\fs75" not in ass
    assert r"\c&H" in ass


def test_fade_animation_uses_configured_smooth_entrance_and_exit():
    renderer = SubtitleRenderer()
    ass = renderer.build_ass(
        [(0.0, 2.0, "Smooth subtitle")],
        {
            "subtitle_animation": "fade",
            "subtitle_fade_in_ms": 240,
            "subtitle_fade_out_ms": 360,
            "uppercase": False,
        },
        2.0,
        1080,
        1920,
    )

    assert ass.count("Dialogue:") == 1
    assert r"{\fad(240,360)}Smooth subtitle" in ass


def test_typewriter_animation_reveals_korean_graphemes_then_fades_out():
    renderer = SubtitleRenderer()
    ass = renderer.build_ass(
        [(0.0, 2.0, "안녕하세요")],
        {
            "subtitle_animation": "typewriter",
            "subtitle_typewriter_cps": 8,
            "subtitle_fade_out_ms": 300,
            "uppercase": False,
        },
        2.0,
        1080,
        1920,
    )
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    texts = [line.split(",", 9)[-1] for line in dialogues]

    assert len(dialogues) >= 6
    assert texts[0] == "안"
    assert "안녕" in texts
    assert any(text.startswith(r"{\fad(0,300)}") and text.endswith("안녕하세요") for text in texts)


def test_auto_subtitle_animation_is_stable_for_the_same_content():
    renderer = SubtitleRenderer()
    segments = [(0.0, 1.5, "A deterministic subtitle treatment")]
    first = renderer.build_ass(
        segments, {"subtitle_animation": "auto"}, 1.5, 720, 1280
    )
    second = renderer.build_ass(
        segments, {"subtitle_animation": "auto"}, 1.5, 720, 1280
    )

    assert first == second


def test_existing_segments_are_sorted_clamped_and_never_overlap():
    renderer = SubtitleRenderer()
    segments = renderer.build_segments(
        {
            "synchronized_subtitles": [
                (1.8, 4.0, "outside"),
                (0.8, 1.4, "second"),
                (0.0, 1.0, "first"),
                (2.0, 2.5, "past end"),
            ]
        },
        {},
        2.0,
    )

    assert segments == [
        (0.0, 1.0, "first"),
        (1.0, 1.4, "second"),
        (1.8, 2.0, "outside"),
    ]
    assert all(0 <= start < end <= 2.0 for start, end, _text in segments)


def test_ass_time_rounds_and_carries_centiseconds():
    assert SubtitleRenderer.format_ass_time(59.999) == "0:01:00.00"
    assert SubtitleRenderer.format_ass_time(3600.006) == "1:00:00.01"


def test_short_typewriter_events_have_distinct_ass_intervals():
    renderer = SubtitleRenderer()
    ass = renderer.build_ass(
        [(0.0, 0.05, "한국어자막")],
        {"subtitle_animation": "typewriter", "subtitle_typewriter_cps": 60},
        0.05,
        720,
        1280,
    )
    intervals = [
        tuple(line.split(",")[1:3])
        for line in ass.splitlines()
        if line.startswith("Dialogue:")
    ]

    assert intervals
    assert all(start != end for start, end in intervals)
    assert len(intervals) == len(set(intervals))


def test_diacritics_do_not_distort_proportional_fallback_timing():
    renderer = SubtitleRenderer()
    plain = renderer._proportional_segments(["مرحبا", "العالم"], 4.0)
    vocalized = renderer._proportional_segments(["مَرْحَبًا", "العالم"], 4.0)

    assert plain[0][1] == pytest.approx(vocalized[0][1])


def test_typewriter_units_keep_flags_keycaps_and_indic_conjuncts_intact():
    assert split_graphemes("🇰🇷") == ["🇰🇷"]
    assert split_graphemes("1️⃣") == ["1️⃣"]
    assert split_graphemes("क्ष") == ["क्ष"]


def test_component_log_does_not_repeat_parent_subtitle_stage_message():
    renderer = SubtitleRenderer()
    logs = []

    class FakeStream:
        def filter(self, *_args, **_kwargs):
            return self

    result = renderer.apply(
        FakeStream(),
        {"full_text": "subtitle"},
        {"max_words_per_subtitle": 2},
        1.0,
        720,
        1280,
        logs.append,
    )
    renderer.cleanup()

    assert isinstance(result, FakeStream)
    assert "📝 Наложение динамических субтитров..." not in logs
    assert "   🧩 Подготовка ASS-субтитров..." in logs
