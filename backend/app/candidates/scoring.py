"""AI Viral Potential Score: rubric total minus AI + local penalties. A heuristic, not a guarantee."""

from __future__ import annotations

import re
from typing import Any

from ..schemas.ai import SUBSCORE_MAX, Penalty, ViralCandidate

GREETING_RE = re.compile(
    r"\b((hey|hi|hello|what'?s up)\s+(guys|everyone|everybody|folks|y'?all)|welcome (back|to (the|my|our))"
    r"|before we (get started|begin|dive in)|let'?s get (started|into it))\b",
    re.I,
)
HOUSEKEEPING_RE = re.compile(r"\b(like and subscribe|hit (the|that) (subscribe|like|bell)|link in (the )?(description|bio)|patreon)\b", re.I)
SPONSOR_RE = re.compile(r"\b(sponsor(ed)?( by)?|brought to you by|promo code|use code|discount code)\b", re.I)
FILLER_START = {"so", "um", "uh", "and", "but", "like", "yeah", "okay", "ok", "right", "anyway", "also"}


def local_penalties(
    opening_text: str,
    full_text: str,
    snap_notes: list[str],
    duration: float,
    min_duration: float,
    max_duration: float,
    sponsor_required: bool = False,
) -> list[Penalty]:
    pens: list[Penalty] = []
    if GREETING_RE.search(opening_text):
        pens.append(Penalty(reason="Opens with a greeting / intro", points=6))
    if HOUSEKEEPING_RE.search(full_text):
        pens.append(Penalty(reason="Contains channel housekeeping", points=4))
    if SPONSOR_RE.search(full_text) and not sponsor_required:
        pens.append(Penalty(reason="Contains a sponsor read", points=5))
    first = re.findall(r"[a-z']+", opening_text.lower())[:1]
    if first and first[0] in FILLER_START:
        pens.append(Penalty(reason=f"Starts with filler word '{first[0]}'", points=1.5))
    for note in snap_notes:
        low = note.lower()
        if "starts mid-sentence" in low:
            pens.append(Penalty(reason="Begins mid-thought", points=4))
        elif "ends mid-sentence" in low or "trimmed to maximum" in low:
            pens.append(Penalty(reason="May end before the payoff", points=4))
        elif "shorter than the minimum" in low:
            pens.append(Penalty(reason="Below campaign minimum duration", points=5))
    if duration > max_duration + 0.5 or duration < min_duration - 0.5:
        if not any("minimum duration" in p.reason for p in pens):
            pens.append(Penalty(reason="Outside allowed duration", points=5))
    return pens


def final_score(candidate: ViralCandidate, extra_penalties: list[Penalty]) -> float:
    rubric = candidate.subscores.total()
    if rubric <= 0 and candidate.viral_score > 0:
        # Model omitted subscores: fall back to its overall score.
        base = candidate.viral_score
    else:
        base = rubric - sum(p.points for p in candidate.penalties)
    total = base - sum(p.points for p in extra_penalties)
    return round(max(0.0, min(100.0, total)), 1)


def rank_key(item: dict[str, Any]) -> tuple:
    subs = item.get("subscores") or {}
    return (-float(item.get("viral_score", 0)), -float(subs.get("hook", 0)), -float(subs.get("clarity", 0)),
            float(item.get("start", 0)))


def rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(items, key=rank_key)
    for i, it in enumerate(ranked, 1):
        it["rank"] = i
    return ranked


def subscore_breakdown(subscores: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": k, "value": float(subscores.get(k, 0)), "max": m} for k, m in SUBSCORE_MAX.items()]
