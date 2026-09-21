"""Timestamp parsing/formatting."""

from __future__ import annotations

import math
import re

_TS_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:[.,]\d+)?)\s*$")


def parse_timestamp(value: object) -> float:
    """Accept seconds (int/float/"12.5"/"12.5s") or clock strings ("MM:SS", "HH:MM:SS.mmm")."""
    if value is None:
        raise ValueError("timestamp is None")
    if isinstance(value, bool):
        raise ValueError("timestamp cannot be bool")
    if isinstance(value, (int, float)):
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            raise ValueError("timestamp is not finite")
        return max(0.0, f)
    s = str(value).strip().lower()
    if not s:
        raise ValueError("empty timestamp")
    m = _TS_RE.match(s)
    if m:
        h = int(m.group(1) or 0)
        mnt = int(m.group(2))
        sec = float(m.group(3).replace(",", "."))
        return h * 3600 + mnt * 60 + sec
    s = s.removesuffix("seconds").removesuffix("secs").removesuffix("sec").removesuffix("s").strip()
    return max(0.0, float(s))


def format_timestamp(seconds: float, millis: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    base = f"{h:02d}:{m:02d}:{s:02d}"
    return f"{base}.{ms:03d}" if millis else base


def srt_timestamp(seconds: float) -> str:
    return format_timestamp(seconds, millis=True).replace(".", ",")


def parse_fraction(value: str | None) -> float:
    """ffprobe style '30000/1001' -> 29.97."""
    if not value:
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            d = float(den)
            return float(num) / d if d else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def fmt_chapter(seconds: float) -> str:
    """YouTube chapter timestamp: 0:00, 4:05, 1:02:03."""
    s = int(max(0.0, seconds))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
