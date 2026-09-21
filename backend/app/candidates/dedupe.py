"""Duplicate detection: timestamp overlap + transcript/idea similarity."""

from __future__ import annotations

import re
from dataclasses import dataclass

STOPWORDS = set("""
a about above after again against all am an and any are as at be because been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers herself him
himself his how i if in into is it its itself just me more most my myself no nor not now of off on once only or other
our ours ourselves out over own same she should so some such than that the their theirs them themselves then there
these they this those through to too under until up very was we were what when where which while who whom why will
with would you your yours yourself yourselves um uh like yeah okay ok really actually gonna wanna kind sort thing
things know mean going get got say said just right well also
""".split())


def content_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9$%']+", (text or "").lower()) if t not in STOPWORDS and len(t) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def time_overlap_ratio(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Intersection length divided by the shorter clip's length."""
    inter = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    shorter = min(a_end - a_start, b_end - b_start)
    return inter / shorter if shorter > 0 else 0.0


@dataclass
class DedupeItem:
    key: str
    start: float
    end: float
    score: float
    text: str = ""
    hook: str = ""
    topic: str = ""
    summary: str = ""


@dataclass
class DuplicateInfo:
    removed: DedupeItem
    kept: DedupeItem
    reason: str


def is_duplicate(a: DedupeItem, b: DedupeItem) -> str | None:
    """Return a reason string if b duplicates a, else None."""
    ov = time_overlap_ratio(a.start, a.end, b.start, b.end)
    text_sim = jaccard(content_tokens(a.text), content_tokens(b.text))
    idea_sim = jaccard(content_tokens(f"{a.topic} {a.summary}"), content_tokens(f"{b.topic} {b.summary}"))
    hook_sim = jaccard(content_tokens(f"{a.hook} {a.topic}"), content_tokens(f"{b.hook} {b.topic}"))

    if ov >= 0.8:
        return f"overlaps {ov:.0%} in time"
    if ov >= 0.5 and not (hook_sim < 0.2 and idea_sim < 0.3):
        return f"overlaps {ov:.0%} with a similar hook/idea"
    if text_sim >= 0.6:
        return f"repeats {text_sim:.0%} of the same spoken content"
    if idea_sim >= 0.65:
        return "communicates essentially the same idea"
    return None


def dedupe(items: list[DedupeItem]) -> tuple[list[DedupeItem], list[DuplicateInfo]]:
    kept: list[DedupeItem] = []
    removed: list[DuplicateInfo] = []
    for item in sorted(items, key=lambda x: (-x.score, x.start)):
        dup_of = None
        reason = None
        for k in kept:
            reason = is_duplicate(k, item)
            if reason:
                dup_of = k
                break
        if dup_of is not None and reason is not None:
            removed.append(DuplicateInfo(removed=item, kept=dup_of, reason=reason))
        else:
            kept.append(item)
    return kept, removed
