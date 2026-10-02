from core.subtitle_sync import SubtitleSynchronizer
import pytest


def test_word_timestamps_split_on_pause_and_limits():
    timestamps = [
        {"word": "one", "start": 0.0, "end": 0.2},
        {"word": "two", "start": 0.21, "end": 0.4},
        {"word": "three", "start": 1.0, "end": 1.2},
    ]
    result = SubtitleSynchronizer.create_subtitles_from_word_timestamps(
        timestamps, max_words_per_subtitle=3, pause_break=0.3, audio_duration=2.0
    )
    assert [item[2] for item in result] == ["one two", "three"]


def test_bad_word_timestamps_are_ignored():
    result = SubtitleSynchronizer.create_subtitles_from_word_timestamps([
        {"word": "", "start": 0, "end": 1},
        {"word": "bad", "start": 2, "end": 1},
        {"text": "good", "start_time": 0, "end_time": 0.5},
    ])
    assert len(result) == 1
    assert result[0][2] == "good"


def test_subtitles_do_not_overlap_after_lead_in_out():
    result = SubtitleSynchronizer.create_subtitles_from_word_timestamps([
        {"word": "one.", "start": 0.0, "end": 0.5},
        {"word": "two.", "start": 0.51, "end": 1.0},
    ], lead_in=0.1, lead_out=0.2)
    assert result[0][1] <= result[1][0]


def test_compact_scripts_split_without_inserting_spaces():
    phrases = SubtitleSynchronizer.split_into_short_phrases(
        "한국어자막테스트입니다",
        max_words=3,
    )

    assert len(phrases) > 1
    assert "".join(phrases) == "한국어자막테스트입니다"
    assert all(" " not in phrase for phrase in phrases)


@pytest.mark.parametrize(
    ("original", "recognized", "expected"),
    [
        ("Привет, мир!", ["привет", "мир"], ["Привет,", "мир!"]),
        ("Hello world!", ["hello", "world"], ["Hello", "world!"]),
        ("안녕하세요세계", ["안녕하세요", "세계"], list("안녕하세요세계")),
        ("日本語字幕", ["日本語", "字幕"], list("日本語字幕")),
        ("中文字幕", ["中文", "字幕"], list("中文字幕")),
        ("مَرْحَبًا بالعالم", ["مرحبا", "بالعالم"], ["مَرْحَبًا", "بالعالم"]),
        ("Hola mundo", ["hola", "mundo"], ["Hola", "mundo"]),
        ("Hallo Welt", ["hallo", "welt"], ["Hallo", "Welt"]),
        ("Bonjour le monde", ["bonjour", "le", "monde"], ["Bonjour", "le", "monde"]),
    ],
)
def test_alignment_preserves_exact_multilingual_source_writing(original, recognized, expected):
    timestamps = [
        {"word": word, "start": index * 0.4, "end": (index + 1) * 0.4}
        for index, word in enumerate(recognized)
    ]

    aligned = SubtitleSynchronizer.align_word_timestamps_to_text(timestamps, original)

    assert [item["word"] for item in aligned] == expected
    assert all(item["end"] > item["start"] for item in aligned)


def test_nonfinite_word_timestamps_are_rejected():
    result = SubtitleSynchronizer.create_subtitles_from_word_timestamps([
        {"word": "nan", "start": float("nan"), "end": 1.0},
        {"word": "infinite", "start": 0.0, "end": float("inf")},
        {"word": "valid", "start": 0.0, "end": 0.5},
    ])

    assert [item[2] for item in result] == ["valid"]


def test_forced_alignment_keeps_every_phrase_inside_short_audio(monkeypatch):
    words = [f"word{index}" for index in range(12)]
    monkeypatch.setattr(
        SubtitleSynchronizer,
        "get_audio_duration",
        staticmethod(lambda _audio_path: 2.0),
    )

    segments = SubtitleSynchronizer.synchronize_subtitles(
        " ".join(words), "voice.wav", max_words_per_subtitle=1
    )

    assert [text for _start, _end, text in segments] == words
    assert segments[0][0] == 0.0
    assert segments[-1][1] == 2.0
    assert all(0 <= start < end <= 2.0 for start, end, _text in segments)
    assert all(
        current[1] == pytest.approx(following[0])
        for current, following in zip(segments, segments[1:])
    )
