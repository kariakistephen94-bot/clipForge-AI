import numpy as np

from app.services.captions import (
    CaptionRenderer,
    blend_bgra,
    chunks_to_srt,
    clean_word,
    group_words,
    mark_emphasis,
    style_from,
)

TEXT = ("Here is the truth. I lost $40,000 in three months, and nobody warned me. "
        "Why did it happen? Because I trusted a plan that sounded perfect on paper.")


def test_grouping_limits(words_factory):
    words = words_factory(TEXT)
    style = style_from("bold_viral", {"words_per_caption": 4})
    chunks = group_words(words, style, ["$40,000", "nobody warned me"])
    assert chunks
    for ch in chunks:
        assert 1 <= len(ch.words) <= 5  # wpc (+1 orphan merge)
        assert len(ch.lines) <= 2
        assert ch.end > ch.start
    # sentence boundary respected: "truth." ends a caption
    assert any(ch.words[-1].raw == "truth." for ch in chunks)
    # captions never overlap in time
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.end <= b.start + 1e-6


def test_emphasis_is_sparse(words_factory):
    words = words_factory(TEXT)
    chunks = group_words(words, style_from("bold_viral"), ["$40,000", "nobody warned me", "the", "I"])
    total = sum(len(c.words) for c in chunks)
    emphasised = sum(w.emphasis for c in chunks for w in c.words)
    assert emphasised < total * 0.5
    for c in chunks:
        runs = sum(1 for i, w in enumerate(c.words) if w.emphasis and (i == 0 or not c.words[i - 1].emphasis))
        assert runs <= 1


def test_mark_emphasis_matches_phrases(words_factory):
    words = words_factory("nobody tells you this about money")
    assert mark_emphasis(words, ["nobody tells you this"]) == {0, 1, 2, 3}


def test_clean_word_punctuation():
    viral, clean = style_from("bold_viral"), style_from("clean")
    assert clean_word("months,", viral) == "MONTHS"
    assert clean_word("happen?", viral) == "HAPPEN?"
    assert clean_word("paper.", clean) == "paper"
    assert clean_word("$40,000", clean) == "$40,000"


def test_style_overrides_are_clamped():
    s = style_from("clean", {"font_size": 999, "position_y": 0.99, "words_per_caption": 12, "highlight_mode": "rainbow"})
    assert s.font_size == 140 and s.position_y == 0.8 and s.words_per_caption == 7 and s.highlight_mode == "word"


def test_srt_and_render(words_factory):
    words = words_factory(TEXT)
    style = style_from("bold_viral")
    chunks = group_words(words, style, [])
    srt = chunks_to_srt(chunks)
    assert srt.startswith("1\n00:00:00,000 --> ")
    r = CaptionRenderer(chunks, style, 1080, 1920)
    frame = np.zeros((1920, 1080, 3), np.uint8)
    r.overlay(frame, chunks[0].start + 0.05)
    assert frame.sum() > 0  # something was drawn
    assert int(1920 * 0.12) <= r.band_y <= int(1920 * 0.82)


def test_blend_clips_to_bounds():
    frame = np.zeros((10, 10, 3), np.uint8)
    ov = np.full((6, 6, 4), 255, np.uint8)
    blend_bgra(frame, ov, 7, 7)
    assert frame[9, 9].tolist() == [255, 255, 255] and frame[0, 0].tolist() == [0, 0, 0]
