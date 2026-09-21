"""Offline, conservative campaign-rule extraction used when Gemini is off/unavailable.

It only recognises explicit, common phrasings. Anything it can't find stays unknown (None/[]).
"""

from __future__ import annotations

import re

from ..schemas.ai import CampaignRules

_NUM = r"(\d+(?:\.\d+)?)"
_UNIT = r"(seconds?|secs?|s|minutes?|mins?|m)\b"


def _to_seconds(n: str, unit: str) -> float:
    v = float(n)
    return v * 60 if unit.lower().startswith("m") else v


def parse_durations(text: str) -> tuple[float | None, float | None]:
    t = text.lower()
    mn = mx = None
    m = re.search(rf"(?:between\s+)?{_NUM}\s*(?:{_UNIT})?\s*(?:-|–|to|and)\s*{_NUM}\s*{_UNIT}", t)
    if m:
        unit2 = m.group(4)
        unit1 = m.group(2) or unit2
        a, b = _to_seconds(m.group(1), unit1), _to_seconds(m.group(3), unit2)
        if 0 < a < b <= 600:
            return a, b
    m = re.search(rf"(?:at least|minimum(?: of)?|min(?:imum)?\.?(?: length| duration)?:?|no shorter than|longer than)\s*{_NUM}\s*{_UNIT}", t)
    if m:
        mn = _to_seconds(m.group(1), m.group(2))
    m = re.search(rf"(?:at most|maximum(?: of)?|max(?:imum)?\.?(?: length| duration)?:?|no longer than|under|less than|up to|shorter than)\s*{_NUM}\s*{_UNIT}", t)
    if m:
        mx = _to_seconds(m.group(1), m.group(2))
    return mn, mx


def _tri_state(text: str, subject: str) -> bool | None:
    t = text.lower()
    neg = rf"(?:no|don't use|do not use|not allowed:?|without|avoid|prohibited:?|never use)\s+{subject}"
    neg2 = rf"{subject}\s+(?:are|is)\s+(?:not allowed|prohibited|banned|forbidden)"
    pos = rf"{subject}\s+(?:are|is)\s+(?:allowed|ok|okay|fine|permitted|welcome|encouraged|required)"
    pos2 = rf"(?:you may|you can|feel free to|allowed to|must|required to)\s+(?:use|add|include)\s+{subject}"
    if re.search(neg, t) or re.search(neg2, t):
        return False
    if re.search(pos, t) or re.search(pos2, t):
        return True
    return None


def parse_rules_heuristic(text: str) -> CampaignRules:
    text = text or ""
    low = text.lower()
    mn, mx = parse_durations(text)

    platforms = []
    if "tiktok" in low or "tik tok" in low:
        platforms.append("tiktok")
    if "instagram" in low or "reels" in low:
        platforms.append("instagram")
    if "youtube" in low or "shorts" in low:
        platforms.append("youtube_shorts")

    hashtags = re.findall(r"(?<![\w&])#([A-Za-z0-9_]{2,40})", text)
    mentions = re.findall(r"(?<![\w.])@([A-Za-z0-9_.]{2,30}[A-Za-z0-9_])", text)
    links = re.findall(r"https?://[^\s)>\]]+", text)

    cta = None
    m = re.search(r"(?:cta|call to action)\s*[:\-]\s*[\"“']?([^\n\"”']{3,140})", text, re.I)
    if m:
        cta = m.group(1).strip()
    else:
        m = re.search(r"(?:must|should)\s+(?:say|include the (?:phrase|text))\s*[:\-]?\s*[\"“']([^\"”']{3,140})[\"”']", text, re.I)
        if m:
            cta = m.group(1).strip()

    captions = _tri_state(text, r"(?:captions|subtitles)")
    captions_required = True if re.search(r"(?:captions|subtitles)\s+(?:are\s+)?(?:required|mandatory)|must (?:have|include|add) (?:captions|subtitles)", low) else None
    if captions_required:
        captions = True
    broll = _tri_state(text, r"(?:b-?roll|stock footage)")
    split = _tri_state(text, r"(?:split[- ]?screens?)")
    music = _tri_state(text, r"(?:background music|music)")

    prohibited: list[str] = []
    modification: list[str] = []
    special: list[str] = []
    for raw_line in re.split(r"[\n\r]+|(?<=[.!?])\s+", text):
        line = raw_line.strip(" -*•\t")
        if len(line) < 6:
            continue
        ll = line.lower()
        if re.match(r"^(no|don't|do not|never|prohibited|not allowed|avoid)\b", ll):
            if any(k in ll for k in ("edit", "alter", "change", "modify", "voiceover", "voice over", "watermark", "crop", "speed", "filter", "ai ")):
                modification.append(line)
            elif not any(k in ll for k in ("caption", "subtitle", "b-roll", "broll", "split", "music")):
                prohibited.append(line)
        elif re.search(r"\b(must|required|requirement|need to|have to|mandatory)\b", ll):
            if not any(k in ll for k in ("#", "@", "caption", "second", "minute", "cta", "call to action")):
                special.append(line)

    rules = CampaignRules(
        min_duration=mn,
        max_duration=mx,
        platforms=platforms,
        required_hashtags=hashtags,
        required_mentions=mentions,
        required_cta=cta,
        required_links=links,
        captions_allowed=captions,
        captions_required=captions_required,
        broll_allowed=broll,
        split_screen_allowed=split,
        music_allowed=music,
        source_modification_rules=modification[:15],
        prohibited_content=prohibited[:15],
        special_requirements=special[:15],
    )
    rules.unknown_requirements = unknown_fields(rules)
    return rules


def unknown_fields(rules: CampaignRules) -> list[str]:
    out = []
    for name in ("min_duration", "max_duration", "required_cta", "captions_allowed", "broll_allowed",
                 "split_screen_allowed", "music_allowed"):
        if getattr(rules, name) is None:
            out.append(name)
    for name in ("platforms", "required_hashtags", "required_mentions"):
        if not getattr(rules, name):
            out.append(name)
    return out
