"""Long-form (16:9, minutes-long) segment logic: how many, snapping, chapters, de-duplication, ranking.

"The right amount": a long-form clip should be a complete episode, so the count follows the source length.
We aim to cover roughly 60% of the source (the best material, not intros or ads) with segments whose ideal
length grows with the source -- a 15-minute video yields 1-2 segments, an hour-long podcast ~5, a
three-hour one ~9 -- capped by settings. The AI may return fewer when the material doesn't support them,
and only segments scoring >= QUALITY_GATE are pre-selected.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..schemas.ai import Chapter, LongFormCandidate
from .dedupe import time_overlap_ratio
from .snapping import clip_text, snap_clip

COVERAGE = 0.6
QUALITY_GATE = 50.0
MIN_SOURCE_FACTOR = 1.6  # source must be at least this many times the minimum segment length


@dataclass
class LongFormTarget:
    count: int
    ideal: float
    note: str


def long_form_target(source_duration: float, lo: float, hi: float, max_clips: int, mode: str = "auto",
                     manual_count: int = 0) -> LongFormTarget:
    if mode == "off":
        return LongFormTarget(0, 0, "Long-form clips are turned off for this project.")
    if source_duration < max(lo * MIN_SOURCE_FACTOR, 480):
        return LongFormTarget(0, 0, f"Source is {source_duration / 60:.0f} min: too short to cut long-form clips "
                                    f"(needs at least {max(lo * MIN_SOURCE_FACTOR, 480) / 60:.0f} min).")
    ideal = max(lo, min(hi, 720.0, source_duration / 7.5))
    possible = max(1, int(source_duration * 0.9 // lo))
    if mode == "manual" and manual_count > 0:
        n = min(manual_count, possible, 20)
        return LongFormTarget(n, ideal, f"{n} long-form clip(s) requested.")
    n = int(source_duration * COVERAGE / ideal + 0.5)
    n = max(1, min(n, max_clips, possible))
    return LongFormTarget(n, ideal, f"{n} long-form clip(s) of ~{ideal / 60:.0f} min for a "
                                    f"{source_duration / 60:.0f}-minute source.")


def clean_chapters(chapters: list[Chapter], start: float, end: float, min_gap: float = 30.0) -> list[dict[str, Any]]:
    """Chapters inside [start, end], sorted, spaced, the first one forced to the start."""
    out: list[dict[str, Any]] = []
    for ch in sorted(chapters, key=lambda c: c.timestamp):
        t = max(start, min(end, ch.timestamp))
        if end - t < 20:
            continue
        if out and t - out[-1]["t"] < min_gap:
            continue
        out.append({"t": round(t, 2), "title": ch.title.strip() or f"Part {len(out) + 1}"})
    if out:
        out[0]["t"] = round(start, 2)
    elif end - start > 0:
        out.append({"t": round(start, 2), "title": "Start"})
    return out


def snap_cold_open(words: list[dict[str, Any]], c: LongFormCandidate, start: float, end: float,
                   media_duration: float) -> dict[str, Any] | None:
    co = c.cold_open
    if co is None:
        return None
    s, e = max(start + 20, co.start_time), min(end, co.end_time)
    if e - s < 3:
        return None
    snap = snap_clip(words, s, e, min_duration=4, max_duration=18, media_duration=media_duration) if words else None
    if snap is not None:
        s, e = snap.start, snap.end
    if not (start <= s < e <= end) or e - s > 20:
        return None
    return {"start": round(s, 3), "end": round(e, 3), "reason": co.reason}


def snap_long(words: list[dict[str, Any]], c: LongFormCandidate, lo: float, hi: float,
              media_duration: float) -> dict[str, Any]:
    s = max(0.0, min(c.start_time, media_duration))
    e = max(0.0, min(c.end_time, media_duration))
    notes: list[str] = []
    if words:
        snap = snap_clip(words, s, e, opening_words=c.exact_opening_words, closing_words=c.exact_closing_words,
                         min_duration=lo, max_duration=hi, media_duration=media_duration)
        s, e, notes = snap.start, snap.end, list(snap.notes)
    return {"start": s, "end": e, "notes": notes, "text": clip_text(words, s, e) if words else ""}


def dedupe_long(items: list[dict[str, Any]], threshold: float = 0.35) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the higher-scored segment when two overlap by >= threshold of the shorter one."""
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for it in sorted(items, key=lambda x: -x["score"]):
        clash = next((k for k in kept if time_overlap_ratio(it["start"], it["end"], k["start"], k["end"]) >= threshold),
                     None)
        if clash:
            removed.append({"candidate_id": it["cand"].candidate_id, "kept": clash["cand"].candidate_id,
                            "reason": "overlaps a stronger long-form segment"})
        else:
            kept.append(it)
    kept.sort(key=lambda x: -x["score"])
    for n, it in enumerate(kept, 1):
        it["rank"] = n
    return kept, removed


def select_count(kept: list[dict[str, Any]], target: int) -> int:
    good = sum(1 for k in kept if k["score"] >= QUALITY_GATE)
    return min(target, good if good else min(1, len(kept)))


def ceil_half(n: int) -> int:
    return max(1, math.ceil(n * 0.5))
