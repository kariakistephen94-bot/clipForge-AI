"""Subtle, strategic punch-ins (100% -> 108/112%), never every two seconds."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import Any

from ..candidates.snapping import is_sentence_end

STRONG_KINDS = {"punchline", "revelation", "reveal", "emotional", "emotion", "surprise"}


@dataclass
class ZoomEvent:
    start: float
    end: float
    scale: float
    kind: str = "statement"


def plan_zooms(
    duration: float,
    words: list[dict[str, Any]],
    ai_points: list[tuple[float, str]],
    emphasis_times: list[float],
    shot_cuts: list[float] | None = None,
    *,
    enabled: bool = True,
    min_spacing: float = 6.0,
) -> list[ZoomEvent]:
    if not enabled or duration < 6:
        return []
    shot_cuts = sorted(shot_cuts or [])
    cands: list[tuple[int, float, str]] = [(2, t, k) for t, k in ai_points]
    cands += [(1, t, "emphasis") for t in emphasis_times]
    for i, w in enumerate(words):
        if w["word"].endswith("?") and is_sentence_end(words, i):
            # punch in at the start of the question sentence
            j = i
            while j > 0 and not is_sentence_end(words, j - 1) and w["end"] - words[j - 1]["start"] < 6:
                j -= 1
            cands.append((1, words[j]["start"], "question"))
    cands.sort(key=lambda c: (-c[0], c[1]))

    max_events = max(1, int(duration // 8))
    starts = [w["start"] for w in words]
    chosen: list[ZoomEvent] = []
    for _prio, t, kind in cands:
        if len(chosen) >= max_events:
            break
        if t < 0.6 or t > duration - 2.0:
            continue
        # snap to the start of the nearest word at/after t
        if words:
            k = bisect.bisect_left(starts, t - 0.15)
            if k < len(words):
                t = words[k]["start"]
        if any(abs(t - e.start) < min_spacing for e in chosen):
            continue
        # hold until the end of the sentence (1.5 - 4 s), and never across a shot cut
        end = t + 2.5
        if words:
            k = bisect.bisect_left(starts, t)
            for m in range(k, len(words)):
                if is_sentence_end(words, m):
                    end = words[m]["end"] + 0.1
                    break
        end = min(max(end, t + 1.5), t + 4.0, duration)
        nxt_cut = next((c for c in shot_cuts if t < c < end), None)
        if nxt_cut is not None:
            end = nxt_cut
        if end - t < 0.8:
            continue
        scale = 1.12 if kind.lower() in STRONG_KINDS else 1.08
        chosen.append(ZoomEvent(start=round(t, 3), end=round(end, 3), scale=scale, kind=kind))
    chosen.sort(key=lambda e: e.start)
    return chosen


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def zoom_at(t: float, events: list[ZoomEvent], ease_in: float = 0.12, ease_out: float = 0.18) -> float:
    for e in events:
        if e.start <= t < e.end:
            into = t - e.start
            left = e.end - t
            f = min(_ease(into / ease_in) if ease_in else 1.0, _ease(left / ease_out) if ease_out else 1.0)
            return 1.0 + (e.scale - 1.0) * f
    return 1.0


def emphasis_word_times(words: list[dict[str, Any]], phrases: list[str]) -> list[float]:
    from .captions import mark_emphasis

    idx = sorted(mark_emphasis(words, phrases))
    times = []
    last = -99
    for i in idx:
        if i != last + 1:
            times.append(words[i]["start"])
        last = i
    return times


def normalize_kind(reason: str, kind: str) -> str:
    text = f"{kind} {reason}".lower()
    for k in ("punchline", "revelation", "emotional", "question", "transition"):
        if re.search(k[:6], text):
            return k
    return kind or "statement"
