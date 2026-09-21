from app.candidates.snapping import find_phrase, is_sentence_end, sentences, snap_clip

TEXT = ("Hey guys welcome back. The biggest mistake I made was trusting a plan. "
        "I lost forty thousand dollars in three months. That really hurt. Anyway let's move on.")


def idx(words, token):
    return next(i for i, w in enumerate(words) if w["word"].strip(".,") == token)


def test_sentences(words_factory):
    words = words_factory(TEXT)
    assert len(sentences(words)) == 5
    assert is_sentence_end(words, idx(words, "back"))


def test_find_phrase_fuzzy(words_factory):
    words = words_factory(TEXT)
    i = find_phrase(words, "the biggest mistake i made", near=words[4]["start"] + 3)
    assert i == idx(words, "The")
    j = find_phrase(words, "forty thousand dollars in three months", near=10, from_end=True)
    assert words[j]["word"] == "months."


def test_snap_anchors_opening_and_extends_to_sentence_end(words_factory):
    words = words_factory(TEXT)
    the, mistake, months = idx(words, "The"), idx(words, "mistake"), idx(words, "months")
    # AI boundaries are sloppy: start mid-word region, end mid-sentence
    r = snap_clip(words, words[mistake]["start"] - 0.1, words[idx(words, "thousand")]["end"],
                  opening_words="The biggest mistake I made", min_duration=1, max_duration=60)
    assert words[the - 1]["end"] < r.start <= words[the]["start"]  # starts at sentence, no chopped word
    assert r.end >= words[months]["end"]  # sentence completed
    assert r.end < words[months + 1]["start"]  # but does not bleed into next word
    assert r.anchored_start and not r.notes


def test_snap_padding_is_bounded(words_factory):
    words = words_factory(TEXT)
    the = idx(words, "The")
    r = snap_clip(words, words[the]["start"], words[idx(words, "plan")]["end"], min_duration=1, max_duration=60)
    assert words[the]["start"] - r.start <= 0.15 + 1e-6
    assert r.end - words[idx(words, "plan")]["end"] <= 0.25


def test_snap_respects_max_duration_by_dropping_sentences(words_factory):
    words = words_factory(TEXT)
    the = idx(words, "The")
    r = snap_clip(words, words[the]["start"], words[-1]["end"], min_duration=2, max_duration=6.5)
    assert r.duration <= 6.5
    assert words[r.end_idx]["word"].endswith(".")


def test_snap_extends_to_min_duration(words_factory):
    words = words_factory(TEXT)
    that = idx(words, "That")
    r = snap_clip(words, words[that]["start"], words[idx(words, "hurt")]["end"], min_duration=5, max_duration=30)
    assert r.duration >= 5


def test_snap_without_words_uses_ai_times():
    r = snap_clip([], 10, 40, media_duration=100)
    assert (r.start, r.end) == (10, 40) and r.notes


def test_snap_skips_long_pause_boundary(words_factory):
    words = words_factory("so yeah this is the part where it gets interesting okay", pauses={4: 1.0})
    # pause before "the" is a sentence boundary even without punctuation
    r = snap_clip(words, words[5]["start"] + 0.1, words[-1]["end"], min_duration=1, max_duration=30)
    assert r.start_idx == 4
