"""Snap AI-suggested clip regions to natural speech boundaries using Whisper word timestamps.

Gemini identifies the IDEA and approximate region; this module decides the exact cut points:
- anchor on the verbatim opening/closing words when they can be found nearby
- start at a sentence start (or right at the hook), end after the sentence containing the payoff
- respect min/max duration by moving whole sentences
- pad 100-250ms without ever chopping into a neighbouring word
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

TERMINAL_RE = re.compile(r"[.!?…]+['\")\]]*$")
SENTENCE_GAP = 0.65  # a pause this long also counts as a sentence boundary


def norm_token(w: str) -> str:
    return re.sub(r"[^\w$%]+", "", w.lower())


def is_sentence_end(words: list[dict[str, Any]], i: int) -> bool:
    if i >= len(words) - 1:
        return True
    if TERMINAL_RE.search(words[i]["word"]):
        return True
    return words[i + 1]["start"] - words[i]["end"] >= SENTENCE_GAP


def is_sentence_start(words: list[dict[str, Any]], i: int) -> bool:
    return i <= 0 or is_sentence_end(words, i - 1)


def sentences(words: list[dict[str, Any]]) -> list[tuple[int, int]]:
    out = []
    s = 0
    for i in range(len(words)):
        if is_sentence_end(words, i):
            out.append((s, i))
            s = i + 1
    return out


def find_phrase(words: list[dict[str, Any]], phrase: str, near: float, window: float = 25.0,
                from_end: bool = False, threshold: float = 0.72) -> int | None:
    """Fuzzy-locate a verbatim phrase near a timestamp. Returns first word index (or last if from_end)."""
    tokens = [t for t in (norm_token(x) for x in (phrase or "").split()) if t]
    if not tokens or not words:
        return None
    tokens = tokens[-10:] if from_end else tokens[:10]
    n = len(tokens)
    target = " ".join(tokens)
    best: tuple[float, float, int] | None = None
    for i in range(0, max(1, len(words) - n + 1)):
        t0 = words[i]["start"]
        if abs(t0 - near) > window + 10:
            continue
        seq = " ".join(norm_token(w["word"]) for w in words[i : i + n])
        ratio = SequenceMatcher(None, seq, target).ratio()
        if ratio < threshold:
            continue
        idx = i + n - 1 if from_end else i
        dist = abs(words[min(idx, len(words) - 1)]["start"] - near)
        if dist > window:
            continue
        key = (ratio, -dist, idx)
        if best is None or key[:2] > best[:2]:
            best = key
    return None if best is None else min(best[2], len(words) - 1)


@dataclass
class SnapResult:
    start: float
    end: float
    start_idx: int | None = None
    end_idx: int | None = None
    anchored_start: bool = False
    anchored_end: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


def _first_word_at_or_after(words: list[dict[str, Any]], t: float) -> int:
    for i, w in enumerate(words):
        if w["end"] > t:
            return i
    return len(words) - 1


def _last_word_before(words: list[dict[str, Any]], t: float) -> int:
    idx = 0
    for i, w in enumerate(words):
        if w["start"] < t:
            idx = i
        else:
            break
    return idx


def snap_clip(
    words: list[dict[str, Any]],
    ai_start: float,
    ai_end: float,
    *,
    opening_words: str = "",
    closing_words: str = "",
    min_duration: float = 15.0,
    max_duration: float = 60.0,
    media_duration: float | None = None,
    pad_start: float = 0.15,
    pad_end: float = 0.22,
) -> SnapResult:
    media_duration = media_duration if media_duration and media_duration > 0 else max(ai_end, words[-1]["end"] if words else ai_end)
    ai_start = max(0.0, min(ai_start, media_duration))
    ai_end = max(ai_start + 0.5, min(ai_end, media_duration))

    if not words:
        return SnapResult(start=ai_start, end=ai_end, notes=["No transcript words; used AI timestamps as-is."])

    notes: list[str] = []

    # ---- start
    si = find_phrase(words, opening_words, ai_start)
    anchored_start = si is not None
    if si is None:
        si = _first_word_at_or_after(words, ai_start)
    if not is_sentence_start(words, si):
        back_limit = 1.6 if anchored_start else 4.0
        found = None
        j = si
        while j > 0 and words[si]["start"] - words[j - 1]["start"] <= back_limit:
            j -= 1
            if is_sentence_start(words, j):
                found = j
                break
        if found is None and not anchored_start:
            k = si
            while k < len(words) - 1 and words[k + 1]["start"] - words[si]["start"] <= 2.5:
                k += 1
                if is_sentence_start(words, k):
                    found = k
                    break
        if found is not None:
            si = found
        elif not anchored_start:
            notes.append("Starts mid-sentence (no nearby sentence start).")

    # ---- end
    ei = find_phrase(words, closing_words, ai_end, from_end=True)
    anchored_end = ei is not None
    if ei is None:
        ei = _last_word_before(words, ai_end)
    ei = max(ei, si)
    if not is_sentence_end(words, ei):
        found = None
        k = ei
        while k < len(words) - 1 and words[k + 1]["end"] - words[ei]["end"] <= 6.0:
            k += 1
            if is_sentence_end(words, k):
                found = k
                break
        if found is None and not anchored_end:
            j = ei
            while j > si and words[ei]["end"] - words[j - 1]["end"] <= 3.0:
                j -= 1
                if is_sentence_end(words, j):
                    found = j
                    break
        if found is not None:
            ei = found
        else:
            notes.append("Ends mid-sentence (no nearby sentence end).")

    def dur(a: int, b: int) -> float:
        return words[b]["end"] - words[a]["start"] + pad_start + pad_end

    # ---- max duration: drop whole trailing sentences first
    if dur(si, ei) > max_duration:
        fitted = None
        for j in range(ei - 1, si - 1, -1):
            if is_sentence_end(words, j) and dur(si, j) <= max_duration and dur(si, j) >= min(min_duration, max_duration):
                fitted = j
                break
        if fitted is None:
            for j in range(ei - 1, si - 1, -1):
                if dur(si, j) <= max_duration:
                    fitted = j
                    break
            notes.append("Trimmed to maximum duration mid-sentence.")
        ei = fitted if fitted is not None else si

    # ---- min duration: extend forward by sentences, then backward
    if dur(si, ei) < min_duration:
        k = ei
        while k < len(words) - 1 and dur(si, k) < min_duration:
            k += 1
            while k < len(words) - 1 and not is_sentence_end(words, k):
                k += 1
            if dur(si, k) > max_duration:
                break
            ei = k
        j = si
        while dur(si, ei) < min_duration and j > 0:
            j -= 1
            while j > 0 and not is_sentence_start(words, j):
                j -= 1
            if dur(j, ei) > max_duration:
                break
            si = j
        if dur(si, ei) < min_duration:
            notes.append("Shorter than the minimum duration (not enough speech nearby).")

    # ---- padding without chopping neighbouring words
    prev_end = words[si - 1]["end"] if si > 0 else 0.0
    w_start = words[si]["start"]
    start = max(prev_end + 0.01, w_start - pad_start) if si > 0 else max(0.0, w_start - pad_start)
    start = min(start, w_start)
    next_start = words[ei + 1]["start"] if ei + 1 < len(words) else media_duration
    w_end = words[ei]["end"]
    end = max(w_end, min(next_start - 0.01, w_end + pad_end))
    end = min(end, media_duration)
    start = max(0.0, start)

    return SnapResult(start=round(start, 3), end=round(end, 3), start_idx=si, end_idx=ei,
                      anchored_start=anchored_start, anchored_end=anchored_end, notes=notes)


def clip_text(words: list[dict[str, Any]], start: float, end: float) -> str:
    return " ".join(w["word"] for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.05)
