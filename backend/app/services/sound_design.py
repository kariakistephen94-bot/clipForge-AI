"""Automatic sound design: decide where effects go, pick files, align and level them, mix under the voice.

Planning is a pure function of the edit (all times on the OUTPUT timeline, after silence removal):

* hook overlay entrance        -> whoosh peaking as the hook lands (variants A/B only)
* AI sound cues (Gemini)       -> the category it asked for at that moment
* payoff / reveal punch-in     -> riser building into it + impact on it
* other punch-ins              -> soft whoosh (statements, questions) or low impact (punchlines)
* B-roll entrances             -> whoosh
* meaning in the words         -> cash on money, ding on ideas, shutter on photos, typing on writing ...
                                  (only on AI emphasis words or unmistakable cases such as "$40k")
* caption emphasis words       -> small pops (balanced / punchy)
* jump cuts                    -> faint swish (punchy only)

Candidates are ranked by priority and accepted greedily under a style's spacing and density limits, so a
clip never turns into a sound-effect soundboard. Files are chosen deterministically per clip (same input ->
same mix), rotating so the same sample isn't repeated inside one clip, preferring files whose names told us
what they are. Each file is trimmed to where it really starts, aligned by its onset / peak / end, levelled to
a per-category loudness target and faded; risers, beds and tension are ducked under speech.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .sfx_library import CATEGORIES, SfxItem

# --------------------------------------------------------------------------- styles


@dataclass(frozen=True)
class Style:
    gain_db: float
    spacing: float  # minimum seconds between any two effects
    per10s: float  # density cap (effects per 10 s, hook excluded)
    pops_per_s: float
    pop_spacing: float
    keywords: int
    ai: int
    zoom: bool
    jump_cuts: bool
    riser: bool


STYLES: dict[str, Style] = {
    "subtle": Style(gain_db=-4.0, spacing=2.5, per10s=1.0, pops_per_s=0.0, pop_spacing=99, keywords=1, ai=2, zoom=False,
                    jump_cuts=False, riser=False),
    "balanced": Style(gain_db=0.0, spacing=1.4, per10s=2.0, pops_per_s=1 / 14, pop_spacing=4.5, keywords=2, ai=4,
                      zoom=True, jump_cuts=False, riser=True),
    "punchy": Style(gain_db=2.0, spacing=0.9, per10s=3.2, pops_per_s=1 / 8, pop_spacing=2.8, keywords=3, ai=6,
                    zoom=True, jump_cuts=True, riser=True),
}

CATEGORY_ALIASES = {
    "swoosh": "whoosh", "swish": "whoosh", "transition": "whoosh", "boom": "impact", "hit": "impact", "bass_drop": "impact",
    "drop": "impact", "build": "riser", "buildup": "riser", "build_up": "riser", "money": "cash", "coin": "cash",
    "notification": "ding", "idea": "ding", "success": "ding", "correct": "ding", "shutter": "camera", "photo": "camera",
    "keyboard": "typing", "writing": "typing", "glitch": "tech", "digital": "tech", "error": "wrong", "fail": "wrong",
    "clock": "tick", "suspense": "tension", "crowd": "reaction", "applause": "reaction", "funny": "comedic",
    "meme": "comedic", "cartoon": "comedic", "ui": "click",
}


def normalize_category(c: str) -> str | None:
    key = re.sub(r"[^a-z_]", "", str(c or "").lower().replace(" ", "_").replace("-", "_"))
    key = CATEGORY_ALIASES.get(key, key)
    return key if key in CATEGORIES and key not in ("music", "other") else None


# Meaning -> sound. Fires on AI emphasis words, or anywhere for the unmistakable "strong" patterns.
KEYWORD_CUES: list[tuple[str, re.Pattern[str], re.Pattern[str] | None]] = [
    ("cash", re.compile(r"^(\$[\d,.]+[kmb]?|money|cash|dollars?|paid|salary|revenue|profits?|rich|millions?|billions?|"
                        r"bucks|price|expensive|sold|earn(ed|ing)?|income|millionaire|billionaire)$", re.I),
     re.compile(r"^(\$[\d,.]+[kmb]?|millionaire|billionaire)$", re.I)),
    ("ding", re.compile(r"^(idea|realized|realised|figured|lesson|tip|secret|answer|solution|insight|trick|genius|"
                        r"breakthrough|epiphany)$", re.I), None),
    ("camera", re.compile(r"^(photos?|pictures?|camera|screenshot|snapshot|selfie|photographed|filmed)$", re.I), None),
    ("typing", re.compile(r"^(typed|typing|wrote|emailed|email|texted|tweeted|coding|coded|script)$", re.I), None),
    ("wrong", re.compile(r"^(wrong|mistakes?|failed|failure|rejected|fired|broke|bankrupt)$", re.I), None),
    ("tech", re.compile(r"^(ai|a\.i|computer|software|algorithm|robots?|hacked|hacker|data|chatgpt|code)$", re.I), None),
    ("impact", re.compile(r"^(boom|crashed|exploded|destroyed|massive|insane)$", re.I), None),
    ("tick", re.compile(r"^(deadline|countdown|clock|tick)$", re.I), None),
]

HOOK_ROOM = 1.8  # the hook entrance owns the first moments of the clip
IMPACT_ROOM = 1.6  # seconds of air after an impact
PEAK_CEILING_DB = -1.5  # keep a single effect off full scale; the final limiter catches the rest
STRONG_ZOOM_KINDS = {"punchline", "revelation", "reveal", "emotional", "emotion", "surprise"}


# --------------------------------------------------------------------------- planning


@dataclass
class SoundEvent:
    t: float  # anchor time on the output timeline
    category: str
    reason: str
    priority: int
    intensity: float = 1.0  # 0.4 .. 1.2 multiplier on the category loudness target
    hook_only: bool = False
    group: str = ""  # events in the same group (riser + impact) are accepted together
    max_active: float | None = None  # prefer samples no longer than this (a jump cut wants a tiny swish)


@dataclass
class SoundContext:
    duration: float
    words: list[dict[str, Any]] = field(default_factory=list)  # output timeline
    emphasis: list[str] = field(default_factory=list)
    zooms: list[tuple[float, str]] = field(default_factory=list)  # (start, kind)
    broll_starts: list[float] = field(default_factory=list)
    jump_cuts: list[float] = field(default_factory=list)
    ai_cues: list[dict[str, Any]] = field(default_factory=list)  # {t, category, reason, intensity}
    hook: bool = False
    humorous: bool = False
    playful: bool = False
    style: str = "balanced"
    available: set[str] | None = None  # categories present in the library


def _clean(w: str) -> str:
    return re.sub(r"^[^\w$]+|[^\w.]+$", "", w).rstrip(".")


def emphasis_indices(words: Sequence[dict[str, Any]], phrases: Sequence[str]) -> set[int]:
    from .captions import mark_emphasis

    return set(mark_emphasis(list(words), list(phrases))) if phrases else set()


def candidate_events(ctx: SoundContext) -> list[SoundEvent]:
    st = STYLES.get(ctx.style, STYLES["balanced"])
    ev: list[SoundEvent] = []
    if ctx.hook:
        ev.append(SoundEvent(0.12, "whoosh", "Hook overlay entrance", 10, 1.0, hook_only=True, max_active=1.2))

    # AI cues (from the multimodal analysis)
    for n, cue in enumerate(ctx.ai_cues):
        cat = normalize_category(cue.get("category", ""))
        if cat is None:
            continue
        inten = max(0.5, min(1.2, float(cue.get("intensity", 1.0) or 1.0)))
        reason = f"AI: {cue.get('reason') or cat}"
        if cat == "riser":
            ev.append(SoundEvent(cue["t"], "riser", reason, 8, inten, group=f"ai{n}"))
            ev.append(SoundEvent(cue["t"], "impact", reason + " (payoff)", 8, 0.8 * inten, group=f"ai{n}"))
        else:
            ev.append(SoundEvent(cue["t"], cat, reason, 8, inten))

    # payoff: riser into the strongest punch-in, impact on it
    strong = [(t, k) for t, k in ctx.zooms if k.lower() in STRONG_ZOOM_KINDS and t >= 6.0]
    if st.riser and strong and ctx.duration >= 20 and not any(normalize_category(c.get("category", "")) == "riser"
                                                               for c in ctx.ai_cues):
        t, k = strong[0]
        ev.append(SoundEvent(t, "riser", f"Build into the {k}", 6, 0.95, group="payoff"))
        ev.append(SoundEvent(t, "impact", f"Land the {k}", 6, 1.0, group="payoff"))

    if st.zoom:
        for t, k in ctx.zooms:
            if k.lower() in STRONG_ZOOM_KINDS:
                ev.append(SoundEvent(t, "impact", f"Punch-in on {k}", 5, 0.8))
            else:
                ev.append(SoundEvent(t, "whoosh", f"Punch-in ({k})", 4, 0.85, max_active=1.3))
    for t in ctx.broll_starts:
        ev.append(SoundEvent(t, "whoosh", "B-roll cut-in", 5, 0.85, max_active=1.3))

    # meaning in the words
    emph = emphasis_indices(ctx.words, ctx.emphasis)
    seen: set[str] = set()
    for i, w in enumerate(ctx.words):
        tok = _clean(w.get("word", ""))
        if not tok:
            continue
        for cat, rx, strong_rx in KEYWORD_CUES:
            if cat in seen or not rx.match(tok):
                continue
            if i in emph or (strong_rx is not None and strong_rx.match(tok)):
                ev.append(SoundEvent(w["start"], cat, f'"{tok}"', 5, 1.0))
                seen.add(cat)
            break

    # small pops on emphasised words (first word of each emphasised run)
    if st.pops_per_s > 0:
        runs = sorted(i for i in emph if i - 1 not in emph)
        for i in runs:
            ev.append(SoundEvent(ctx.words[i]["start"], "pop", f'Emphasis "{_clean(ctx.words[i]["word"])}"', 2, 0.9,
                                 max_active=0.5))
    if st.jump_cuts:
        for t in ctx.jump_cuts:
            ev.append(SoundEvent(t, "whoosh", "Jump cut", 1, 0.55, max_active=0.6))
    return ev


def keyword_events(words: Sequence[dict[str, Any]], *, limit: int, min_gap: float = 25.0,
                   per_category: int = 2, start_after: float = 0.0) -> list[SoundEvent]:
    """Money / idea / mistake moments spread through a long clip (strong matches such as "$40k" win)."""
    found: list[tuple[int, SoundEvent]] = []
    for w in words:
        tok = _clean(w.get("word", ""))
        if not tok or w["start"] < start_after:
            continue
        for cat, rx, strong_rx in KEYWORD_CUES:
            if not rx.match(tok):
                continue
            strong = strong_rx is not None and strong_rx.match(tok)
            found.append((5 if strong else 4, SoundEvent(w["start"], cat, f'"{tok}"', 5 if strong else 4, 1.0)))
            break
    chosen: list[SoundEvent] = []
    counts: dict[str, int] = {}
    for _prio, e in sorted(found, key=lambda x: (-x[0], x[1].t)):
        if len(chosen) >= limit or counts.get(e.category, 0) >= per_category:
            continue
        if any(abs(o.t - e.t) < min_gap for o in chosen):
            continue
        chosen.append(e)
        counts[e.category] = counts.get(e.category, 0) + 1
    chosen.sort(key=lambda e: e.t)
    return chosen


def plan_events(ctx: SoundContext) -> list[SoundEvent]:
    """Choose which candidate events survive the style's spacing and density limits."""
    if ctx.style == "off" or ctx.duration < 3:
        return []
    st = STYLES.get(ctx.style, STYLES["balanced"])
    cands = candidate_events(ctx)
    allowed_playful = ctx.playful and ctx.humorous
    budget = max(1, math.ceil(ctx.duration / 10.0 * st.per10s))
    caps = {"ai": st.ai, "pop": max(0, int(ctx.duration * st.pops_per_s)), "kw": st.keywords}
    used = {"ai": 0, "pop": 0, "kw": 0}
    chosen: list[SoundEvent] = []
    groups: dict[str, list[SoundEvent]] = {}
    for e in cands:
        if e.group:
            groups.setdefault(e.group, []).append(e)

    def ok(e: SoundEvent, others: list[SoundEvent]) -> bool:
        cat = CATEGORIES[e.category]
        if ctx.available is not None and e.category not in ctx.available:
            return False
        if not cat.auto and not (allowed_playful and e.category in ("comedic", "reaction")):
            return False
        if e.t < 0 or e.t > ctx.duration - 0.25:
            return False
        if e.category == "riser" and e.t < 2.0:
            return False
        for o in chosen:
            if o.group and o.group == e.group:
                continue
            gap = abs(o.t - e.t)
            if o.hook_only or e.hook_only:
                if gap < HOOK_ROOM:
                    return False
                continue
            if gap < st.spacing:
                return False
            if o.category == "impact" and 0 < e.t - o.t < IMPACT_ROOM:
                return False  # let a hit ring out before the next sound
            if e.category == "impact" and 0 < o.t - e.t < IMPACT_ROOM:
                return False
            if o.category == e.category and e.category == "pop" and gap < st.pop_spacing:
                return False
            if e.category == "riser" and o.category not in ("riser",) and e.t - 3.0 < o.t < e.t:
                return False  # something already happens during the build
        return True

    order = sorted(cands, key=lambda e: (-e.priority, e.t))
    done_groups: set[str] = set()
    for e in order:
        if e.group:
            if e.group in done_groups:
                continue
            done_groups.add(e.group)
            members = groups[e.group]
            if all(ok(m, chosen) for m in members) and _count(chosen) + len(members) <= budget + 1:
                chosen.extend(members)
            else:
                # the pair didn't fit: try just the impact / main hit
                hit = next((m for m in members if m.category != "riser"), None)
                if hit and ok(hit, chosen) and _count(chosen) < budget:
                    chosen.append(SoundEvent(hit.t, hit.category, hit.reason, hit.priority, hit.intensity))
            continue
        kind = "ai" if e.reason.startswith("AI:") else "pop" if e.category == "pop" else \
            "kw" if e.reason.startswith('"') else ""
        if kind and used[kind] >= caps[kind]:
            continue
        if not e.hook_only and _count(chosen) >= budget:
            continue
        if not ok(e, chosen):
            continue
        chosen.append(e)
        if kind:
            used[kind] += 1
    chosen.sort(key=lambda e: (e.t, e.category != "riser"))
    return chosen


def _count(events: list[SoundEvent]) -> int:
    return sum(1 for e in events if not e.hook_only)


# --------------------------------------------------------------------------- file assignment


@dataclass
class PlacedSound:
    item_id: str
    path: str
    name: str
    category: str
    reason: str
    t: float  # the event time the sound is aligned to
    start: float  # output time where playback begins
    src_in: float  # seconds into the file
    length: float
    gain_db: float
    fade_in: float
    fade_out: float
    duck: bool
    hook_only: bool
    channels: int = 2

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("path")
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items()}


MAX_BOOST_DB = 9.0  # a file needing more than this is too quiet to use cleanly (its noise floor comes up too)


def _pool(items: Sequence[SfxItem], category: str) -> list[SfxItem]:
    pool = [i for i in items if i.usable and i.category == category]
    target = CATEGORIES[category].target_db if category in CATEGORIES else -20.0
    loud_enough = [i for i in pool if target - i.loud_db <= MAX_BOOST_DB]
    pool = loud_enough or pool
    # prefer files whose names told us what they are, then the ones with a clean, short active part
    pool.sort(key=lambda i: (i.category_source == "acoustic", i.slice_index is not None, i.id))
    named = [i for i in pool if i.category_source != "acoustic"]
    return named if len(named) >= 2 else pool


def place_sound(item: SfxItem, e: SoundEvent, duration: float, *, style_gain: float = 0.0,
                volume: float = 1.0) -> PlacedSound | None:
    cat = CATEGORIES[e.category]
    base = item.offset + max(0.0, item.lead - 0.01)
    natural_end = item.offset + item.tail + 0.06
    if cat.anchor == "end":  # risers: the build finishes exactly on the event
        src_out = item.offset + item.tail
        src_in = max(base, src_out - cat.max_len)
        start = e.t - (src_out - src_in)
    elif cat.anchor == "peak":  # whooshes / impacts: the loudest moment lands on the event
        peak_abs = item.offset + max(item.peak, item.lead)
        src_in = max(base, peak_abs - cat.max_len * 0.6)
        src_out = min(natural_end, src_in + cat.max_len)
        start = e.t - max(0.0, min(peak_abs - src_in, src_out - src_in))
    else:  # onset: starts a hair before the word
        src_in = base
        src_out = min(natural_end, src_in + cat.max_len)
        start = e.t - 0.02
    fade_in = 0.005
    if start < 0:
        src_in += -start
        start = 0.0
        fade_in = 0.02
    length = src_out - src_in
    truncated = src_out < natural_end - 0.05
    if start + length > duration:
        length = duration - start
        truncated = True
    if length < 0.05:
        return None
    fade_out = min(0.3, length * 0.35) if truncated else min(0.04, length * 0.2)
    gain = cat.target_db - item.loud_db + style_gain + 20 * math.log10(max(0.05, e.intensity))
    gain += 20 * math.log10(max(0.01, volume))
    # a spiky file (bells, register dings) must not hit the limiter and pump the voice
    gain = min(gain, PEAK_CEILING_DB - item.peak_db)
    gain = max(-40.0, min(12.0, gain))
    return PlacedSound(item_id=item.id, path=item.path, name=item.name if item.slice_index is None else
                       f"{item.name} #{item.slice_index + 1}", category=e.category, reason=e.reason, t=round(e.t, 3),
                       start=round(start, 3), src_in=round(src_in, 3), length=round(length, 3), gain_db=round(gain, 2),
                       fade_in=fade_in, fade_out=round(fade_out, 3), duck=cat.duck, hook_only=e.hook_only,
                       channels=item.channels)


def assign_files(events: Sequence[SoundEvent], items: Sequence[SfxItem], duration: float, seed: str, *,
                 style: str = "balanced", volume: float = 1.0) -> list[PlacedSound]:
    st = STYLES.get(style, STYLES["balanced"])
    rotations: dict[str, list[SfxItem]] = {}
    counters: dict[str, int] = {}
    placed: list[PlacedSound] = []
    for e in events:
        key = f"{e.category}<{e.max_active}"
        if key not in rotations:
            pool = _pool(items, e.category)
            if e.max_active is not None:
                pool = [i for i in pool if i.active <= e.max_active] or pool
            rnd = random.Random(int(hashlib.sha1(f"{seed}:{key}".encode()).hexdigest()[:12], 16))
            rnd.shuffle(pool)
            rotations[key] = pool
        pool = rotations[key]
        if not pool:
            continue
        k = counters.get(key, 0)
        counters[key] = k + 1
        p = place_sound(pool[k % len(pool)], e, duration, style_gain=st.gain_db, volume=volume)
        if p is not None:
            placed.append(p)
    return placed


def available_categories(items: Sequence[SfxItem]) -> set[str]:
    return {i.category for i in items if i.usable}


# --------------------------------------------------------------------------- mixing


def _to_layout(channels: int, stereo: bool) -> str:
    """Channel conversion without FFmpeg's -3 dB mono->stereo upmix (a mono voice must keep its loudness)."""
    if stereo:
        return "pan=stereo|c0=c0|c1=c0" if channels == 1 else "aformat=channel_layouts=stereo"
    return "aformat=channel_layouts=mono"


def build_mix_cmd(voice_wav: str | Path, sounds: Sequence[PlacedSound], out_wav: str | Path, duration: float,
                  ffmpeg: str = "ffmpeg", voice_channels: int = 2) -> list[str]:
    """voice + placed effects -> 48 kHz WAV in the voice's own layout (mono stays mono).

    Ducked effects are side-chained under the voice."""
    stereo = voice_channels != 1
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(voice_wav)]
    for s in sounds:
        cmd += ["-vn", "-ss", f"{s.src_in:.3f}", "-t", f"{s.length:.3f}", "-i", s.path]
    fmt = "aformat=sample_fmts=fltp:sample_rates=48000"
    f: list[str] = []
    plain: list[str] = []
    ducked: list[str] = []
    for n, s in enumerate(sounds, 1):
        delay = int(round(s.start * 1000))
        chain = [_to_layout(s.channels, stereo), fmt, "asetpts=PTS-STARTPTS", f"afade=t=in:st=0:d={s.fade_in:.3f}",
                 f"afade=t=out:st={max(0.0, s.length - s.fade_out):.3f}:d={s.fade_out:.3f}",
                 f"volume={s.gain_db:.2f}dB"]
        if delay > 0:
            chain.append(f"adelay={delay}:all=1")
        f.append(f"[{n}:a:0]{','.join(chain)}[s{n}]")
        (ducked if s.duck else plain).append(f"[s{n}]")
    need_key = bool(ducked)
    f.append(f"[0:a:0]{_to_layout(voice_channels, stereo)},{fmt}" + (",asplit=2[voice][key]" if need_key else "[voice]"))
    mix_inputs = ["[voice]"]
    if plain:
        f.append("".join(plain) + (f"amix=inputs={len(plain)}:normalize=0:duration=longest[fx]" if len(plain) > 1 else "anull[fx]"))
        mix_inputs.append("[fx]")
    if ducked:
        f.append("".join(ducked) + (f"amix=inputs={len(ducked)}:normalize=0:duration=longest[fxd0]" if len(ducked) > 1 else "anull[fxd0]"))
        f.append("[fxd0][key]sidechaincompress=threshold=0.04:ratio=5:attack=15:release=300[fxd]")
        mix_inputs.append("[fxd]")
    f.append("".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:normalize=0:duration=first,"
             "alimiter=limit=0.89:level=0:attack=4:release=60[out]")
    cmd += ["-filter_complex", ";".join(f), "-map", "[out]", "-c:a", "pcm_s16le", "-ar", "48000",
            "-t", f"{duration:.3f}", str(out_wav)]
    return cmd


def mix_sound_design(voice_wav: str | Path, sounds: Sequence[PlacedSound], out_wav: str | Path, duration: float) -> None:
    from .ffmpeg import ffmpeg_bin, run_ffmpeg
    from .sfx_library import probe_audio

    _dur, channels = probe_audio(Path(voice_wav))
    run_ffmpeg(build_mix_cmd(voice_wav, sounds, out_wav, duration, ffmpeg=ffmpeg_bin(), voice_channels=channels),
               timeout=900)


# --------------------------------------------------------------------------- campaign gate

_NO_SFX = re.compile(r"\b(no|without|don'?t (add|use)|not allowed|prohibit\w*|avoid)\b[^.]{0,40}"
                     r"\b(sound ?effects?|sfx|added (audio|sounds?)|extra (audio|sounds?)|additional (audio|sounds?))", re.I)
_KEEP_AUDIO = re.compile(r"\b(original|unaltered|unmodified)\s+audio\b|\bdo not (alter|modify|change) the audio\b", re.I)


def sfx_permitted(rules: Any) -> tuple[bool, str]:
    """Campaign rules can forbid added sound effects even when they say nothing about music."""
    if rules is None:
        return True, ""
    texts = [*getattr(rules, "source_modification_rules", []), *getattr(rules, "special_requirements", []),
             *getattr(rules, "prohibited_content", [])]
    for t in texts:
        if _NO_SFX.search(t) or _KEEP_AUDIO.search(t):
            return False, f"Sound effects skipped: campaign rule “{t[:120]}”."
    return True, ""
