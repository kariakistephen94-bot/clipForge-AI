"""Conservative dead-air removal and timeline remapping.

Gaps between Whisper words are cut only when (a) they exceed a threshold, (b) FFmpeg's
silencedetect confirms the audio is actually quiet there, and (c) the pause is not a likely
dramatic pause (after a question / before an emphasised word) unless aggressive mode is on.
A natural pause is always left in place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..candidates.snapping import norm_token


@dataclass
class SilenceConfig:
    enabled: bool = True
    aggressive: bool = False

    @property
    def min_gap(self) -> float:
        return 0.45 if self.aggressive else 0.85

    @property
    def keep_pause(self) -> float:
        return 0.22 if self.aggressive else 0.38


def _intersect(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float] | None:
    s, e = max(a[0], b[0]), min(a[1], b[1])
    return (s, e) if e - s > 1e-6 else None


def plan_keep_segments(
    words: list[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    cfg: SilenceConfig,
    confirmed_silences: list[tuple[float, float]] | None = None,
    emphasis_phrases: list[str] | None = None,
) -> tuple[list[tuple[float, float]], list[dict[str, Any]]]:
    """Return (keep segments in source time, list of removed regions with reasons)."""
    if not cfg.enabled:
        return [(clip_start, clip_end)], []
    ws = [w for w in words if w["start"] >= clip_start - 0.05 and w["end"] <= clip_end + 0.05]
    emph_first_tokens = {norm_token(p.split()[0]) for p in (emphasis_phrases or []) if p.split()}
    cuts: list[tuple[float, float]] = []
    removed: list[dict[str, Any]] = []
    half = cfg.keep_pause / 2
    for w1, w2 in zip(ws, ws[1:], strict=False):
        gap = w2["start"] - w1["end"]
        if gap < cfg.min_gap:
            continue
        if not cfg.aggressive:
            dramatic = bool(re.search(r"(\?|\.\.\.|…|—|-|:)$", w1["word"])) or norm_token(w2["word"]) in emph_first_tokens
            if dramatic:
                continue
        region = (w1["end"] + half, w2["start"] - half)
        if region[1] - region[0] < 0.2:
            continue
        if confirmed_silences is not None:
            best = None
            for sil in confirmed_silences:
                inter = _intersect(region, sil)
                if inter and (best is None or inter[1] - inter[0] > best[1] - best[0]):
                    best = inter
            if best is None or best[1] - best[0] < 0.2:
                continue
            region = best
        cuts.append(region)
        removed.append({"start": round(region[0], 3), "end": round(region[1], 3), "seconds": round(region[1] - region[0], 3)})

    keep: list[tuple[float, float]] = []
    cursor = clip_start
    for s, e in cuts:
        if s - cursor > 0.08:
            keep.append((round(cursor, 3), round(s, 3)))
        cursor = e
    if clip_end - cursor > 0.08:
        keep.append((round(cursor, 3), round(clip_end, 3)))
    if not keep:
        keep = [(clip_start, clip_end)]
    return keep, removed


def total_duration(segments: list[tuple[float, float]]) -> float:
    return round(sum(e - s for s, e in segments), 3)


def map_time(t: float, segments: list[tuple[float, float]]) -> float | None:
    """Source time -> output time. None when t falls in removed material."""
    acc = 0.0
    for s, e in segments:
        if t < s:
            return None
        if t <= e:
            return round(acc + (t - s), 3)
        acc += e - s
    return None


def map_time_clamped(t: float, segments: list[tuple[float, float]]) -> float:
    acc = 0.0
    for s, e in segments:
        if t < s:
            return round(acc, 3)
        if t <= e:
            return round(acc + (t - s), 3)
        acc += e - s
    return round(acc, 3)


def remap_words(words: list[dict[str, Any]], segments: list[tuple[float, float]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not segments:
        return out
    lo, hi = segments[0][0], segments[-1][1]
    for w in words:
        if w["end"] <= lo or w["start"] >= hi:
            continue
        s = map_time_clamped(max(w["start"], lo), segments)
        e = map_time_clamped(min(w["end"], hi), segments)
        if e - s < 0.02:
            e = s + 0.05
        out.append({**w, "start": s, "end": e})
    return out
