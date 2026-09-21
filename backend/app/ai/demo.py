"""Demo provider: runs fully offline with mocked (heuristic) analysis.

It never calls a network API. Candidates are derived from the real Whisper transcript using
simple text features so the UI and render pipeline can be exercised without Gemini quota.
Everything it produces is labelled [DEMO].
"""

from __future__ import annotations

import re
from typing import Any

from ..candidates.dedupe import content_tokens
from ..candidates.snapping import sentences
from ..schemas.ai import (
    AIComplianceItem,
    AIComplianceList,
    CampaignRules,
    CandidateRefinement,
    HookOption,
    HookVariantList,
    HookVariants,
    LongFormCandidate,
    LongFormList,
    LongFormSubscores,
    Penalty,
    Subscores,
    ThumbnailConcept,
    VideoAnalysis,
    ViralCandidate,
    ViralCandidateList,
)
from ..services.transcribe import flatten_words
from .provider import AIProvider, AnalysisContext, LongFormContext
from .rules_heuristic import parse_rules_heuristic

STRONG = re.compile(
    r"\b(never|always|nobody|everyone|biggest|worst|best|mistake|secret|truth|wrong|actually|million|thousand|"
    r"crazy|insane|fail(ed|ure)?|lost|quit|fired|broke|rich|money|warning|stop|why|how|shocking|surpris\w*|lie|lied)\b",
    re.I,
)
EMOTION = re.compile(r"\b(love|hate|scared|afraid|angry|excited|cried|laugh\w*|amazing|terrible|hurt|proud|shame)\b", re.I)
NUMBER = re.compile(r"(\$\s?\d[\d,.]*[kKmM]?|\b\d[\d,.]*\s?(%|percent|k|million|billion|thousand|years?|days?|months?|hours?)?\b)")
GREETING = re.compile(r"\b(hey guys|welcome back|subscribe|hello everyone)\b", re.I)
CONNECTIVE_START = re.compile(r"^(and|but|so|because|which|that|it|this|they|he|she)\b", re.I)


def _features(text: str, first_sentence: str) -> dict[str, Any]:
    return {
        "numbers": len(NUMBER.findall(text)),
        "first_number": bool(NUMBER.search(first_sentence)),
        "first_strong": bool(STRONG.search(first_sentence)),
        "first_question": first_sentence.strip().endswith("?"),
        "questions": text.count("?"),
        "strong": len(STRONG.findall(text)),
        "emotion": len(EMOTION.findall(text)) + text.count("!"),
        "first_short": len(first_sentence.split()) <= 14,
        "connective_start": bool(CONNECTIVE_START.match(first_sentence.strip())),
        "greeting": bool(GREETING.search(text)),
    }


def _subscores(f: dict[str, Any], n_sentences: int) -> tuple[Subscores, list[Penalty]]:
    s = Subscores(
        hook=min(20, 8 + 4 * f["first_strong"] + 3 * f["first_number"] + 3 * f["first_question"] + 2 * f["first_short"]),
        clarity=min(15, 11 - 3 * f["connective_start"] + (1 if n_sentences >= 3 else 0)),
        curiosity=min(15, 7 + 3 * min(f["questions"], 2) + min(f["strong"], 3)),
        emotion=min(10, 4 + min(f["emotion"], 4)),
        specificity=min(10, 3 + 2 * min(f["numbers"], 3)),
        shareability=min(10, 4 + min(f["strong"], 3) + min(f["emotion"], 2)),
        retention=min(10, 5 + min(n_sentences, 4)),
        visual=3,
        campaign_fit=3,
    )
    pens = []
    if f["greeting"]:
        pens.append(Penalty(reason="Greeting / housekeeping language", points=6))
    return s, pens


def _hook_from(sentence: str, max_words: int = 7) -> str:
    words = sentence.strip().split()
    text = " ".join(words[:max_words])
    text = re.sub(r"[,;:]$", "", text)
    return text + ("..." if len(words) > max_words else "")


def _overlay_words(text: str) -> str:
    """2-3 punchy words for a thumbnail: a number with its noun, else the strongest word."""
    m = NUMBER.search(text)
    if m and m.group(0).strip():
        after = text[m.end():].split()[:1]
        return " ".join([m.group(0).strip(), *after]).strip(" ,.").upper()[:24]
    m = STRONG.search(text)
    return m.group(0).upper() if m else ""


def demo_thumbnail(text: str, speaker: str, frame_t: float | None, aspect_note: str = "") -> ThumbnailConcept:
    f = _features(text, text.split(".")[0])
    emotion = "curiosity" if f["first_question"] or f["questions"] else "shock" if f["strong"] else \
        "disbelief" if f["numbers"] else "determination"
    return ThumbnailConcept(
        concept=f"[DEMO] {emotion.title()} close-up",
        emotion=emotion,
        subject=f"{speaker or 'the speaker'}, tight close-up, {emotion} expression, eyes to camera",
        scene="clean dark gradient background related to the topic, softly blurred",
        visual_elements=[],
        text_overlay=_overlay_words(text),
        text_style="heavy white sans-serif, thick black outline, key word in yellow, upper third",
        color_palette="warm skin tones against deep blue",
        composition="face on the right third, text on the left, bottom-right corner clear" + aspect_note,
        lighting="bright key light on the face, cool rim light",
        frame_timestamp=frame_t,
        why_it_works="[DEMO - heuristic] expression + short text create a question the video answers",
    )


class DemoProvider(AIProvider):
    name = "demo"
    model = "demo-heuristic"

    @property
    def is_demo(self) -> bool:
        return True

    def prompt_version(self, operation: str) -> str:
        return f"demo-{operation}@1"

    def parse_campaign_rules(self, rules_text: str) -> CampaignRules:
        return parse_rules_heuristic(rules_text)

    # ------------------------------------------------------------------

    def _window_candidate(self, words: list[dict[str, Any]], sents: list[tuple[int, int]], a: int, b: int,
                          cid: str) -> ViralCandidate:
        si, ei = sents[a][0], sents[b][1]
        sent_texts = [" ".join(w["word"] for w in words[x:y + 1]) for x, y in sents[a:b + 1]]
        text = " ".join(sent_texts)
        first = sent_texts[0]
        f = _features(text, first)
        subs, pens = _subscores(f, len(sent_texts))
        question = next((s for s in sent_texts if s.strip().endswith("?")), None)
        strongest = max(sent_texts, key=lambda s: len(STRONG.findall(s)) + len(NUMBER.findall(s)))
        hooks = [_hook_from(first)]
        for h in (question, strongest, sent_texts[-1]):
            if h and _hook_from(h) not in hooks:
                hooks.append(_hook_from(h))
        emphasis = list(dict.fromkeys([m.group(0).strip() for m in NUMBER.finditer(text)][:3] +
                                      [m.group(0) for m in STRONG.finditer(text)][:4]))
        zooms = []
        last_t = -99.0
        for x, y in sents[a:b + 1]:
            s_text = " ".join(w["word"] for w in words[x:y + 1])
            t = words[x]["start"]
            if (s_text.endswith("?") or STRONG.search(s_text)) and t - last_t >= 6 and t > words[si]["start"] + 1:
                zooms.append({"timestamp": t, "reason": "[DEMO] emphatic or question sentence", "kind": "statement"})
                last_t = t
        topic_tokens = sorted((t for t in content_tokens(text) if not re.search(r"\d", t)),
                              key=lambda tok: -text.lower().count(tok))[:4]
        reasons = []
        if f["first_strong"] or f["first_question"]:
            reasons.append("strong/question opening")
        if f["numbers"]:
            reasons.append("specific numbers")
        if f["emotion"]:
            reasons.append("emotional language")
        if f["questions"]:
            reasons.append("open question")
        return ViralCandidate(
            candidate_id=cid,
            start_time=words[si]["start"],
            end_time=words[ei]["end"],
            exact_opening_words=" ".join(w["word"] for w in words[si:si + 8]),
            exact_closing_words=" ".join(w["word"] for w in words[max(si, ei - 7):ei + 1]),
            topic=" / ".join(topic_tokens) or "segment",
            summary=_hook_from(text, 30),
            reason_it_works="[DEMO - heuristic, not AI] " + (", ".join(reasons) or "self-contained sentences"),
            target_audience="[DEMO] general",
            hook_type="question" if f["first_question"] else ("number" if f["first_number"] else "other"),
            suggested_hook_text=hooks[0],
            alternative_hooks=hooks[1:3],
            editing_strategy="[DEMO] Keep pacing tight; punch in on emphatic sentences.",
            caption_emphasis_words=emphasis[:6],
            suggested_zoom_points=zooms[:3],
            platform_fit={"tiktok": 6, "instagram": 6, "youtube_shorts": 6, "notes": "[DEMO]"},
            campaign_compliance={"status": "UNKNOWN", "reasons": ["Demo mode: campaign content not judged by AI."]},
            subscores=subs,
            penalties=pens,
        )

    def generate_candidates(self, ctx: AnalysisContext) -> ViralCandidateList:
        words = flatten_words(ctx.transcript)
        target = max(1, ctx.candidate_target)
        lo, hi = ctx.min_duration, ctx.max_duration
        ideal = lo + (hi - lo) * 0.45
        cands: list[ViralCandidate] = []

        if not words:
            dur = min(hi, max(lo, 30.0))
            step = max(dur, ctx.source_duration / max(target, 1))
            t = 0.0
            i = 1
            while t + lo <= ctx.source_duration and len(cands) < target:
                end = min(ctx.source_duration, t + dur)
                cands.append(ViralCandidate(
                    candidate_id=f"c{i:02d}", start_time=t, end_time=end, topic="[DEMO] visual segment",
                    summary="No speech detected; evenly spaced demo segment.",
                    reason_it_works="[DEMO - heuristic, not AI] no transcript available",
                    subscores=Subscores(hook=6, clarity=6, curiosity=5, emotion=3, specificity=2, shareability=3,
                                        retention=4, visual=3, campaign_fit=2),
                ))
                t += step
                i += 1
        else:
            sents = sentences(words)
            windows = []
            for a in range(len(sents)):
                b = a
                while b < len(sents) - 1 and words[sents[b][1]]["end"] - words[sents[a][0]]["start"] < ideal:
                    b += 1
                d = words[sents[b][1]]["end"] - words[sents[a][0]]["start"]
                if lo - 2 <= d <= hi:
                    windows.append((a, b))
            scored = []
            for a, b in windows:
                c = self._window_candidate(words, sents, a, b, "tmp")
                scored.append((c.computed_score(), a, b, c))
            scored.sort(key=lambda x: -x[0])
            used: list[tuple[float, float]] = []
            for _score, _a, _b, c in scored:
                if any(not (c.end_time <= s or c.start_time >= e) for s, e in used):
                    continue
                used.append((c.start_time, c.end_time))
                cands.append(c)
                if len(cands) >= target:
                    break
            for i, c in enumerate(cands, 1):
                c.candidate_id = f"c{i:02d}"

        topics = sorted(content_tokens(" ".join(w["word"] for w in words)),
                        key=lambda tok: -sum(1 for w in words if tok in w["word"].lower()))[:6] if words else []
        analysis = VideoAnalysis(
            summary="[DEMO] Offline heuristic analysis of the transcript. Connect Gemini for real multimodal understanding.",
            content_type="unknown (demo)",
            main_topics=topics,
        )
        return ViralCandidateList(video_analysis=analysis, candidates=cands)

    def generate_long_form(self, ctx: LongFormContext) -> LongFormList:
        words = flatten_words(ctx.transcript)
        if not words or ctx.target_count <= 0:
            return LongFormList(items=[], notes="[DEMO] no transcript")
        sents = sentences(words)
        lo, hi = ctx.min_duration, ctx.max_duration
        ideal = max(lo, min(hi, ctx.source_duration / 7.5, 720.0))
        speakers = ", ".join(s.get("description") or s.get("label", "") for s in ctx.video_analysis.get("speakers", [])[:2])
        scored = []
        last_start = -1e9
        for a in range(len(sents)):
            t0 = words[sents[a][0]]["start"]
            if t0 - last_start < 30:  # candidate starts every ~30 s keep long sources fast
                continue
            gap = t0 - words[sents[a][0] - 1]["end"] if sents[a][0] > 0 else 9
            if gap < 0.6 and a > 0:
                continue
            last_start = t0
            b = a
            while b < len(sents) - 1 and words[sents[b][1]]["end"] - t0 < ideal:
                b += 1
            d = words[sents[b][1]]["end"] - t0
            if d < lo * 0.9:
                continue
            first = " ".join(w["word"] for w in words[sents[a][0]:sents[a][1] + 1])
            text = " ".join(w["word"] for w in words[sents[a][0]:sents[b][1] + 1])
            f = _features(text[:3000], first)
            density = (f["strong"] + f["numbers"] + f["emotion"] + f["questions"]) / max(1.0, d / 60)
            subs = LongFormSubscores(opening=min(20, 8 + 4 * f["first_strong"] + 4 * f["first_question"] + 3 * f["first_number"]),
                                     arc=12, value=min(20, 6 + 2 * density), retention=min(15, 6 + density),
                                     emotion=min(10, 3 + f["emotion"] / 4), packaging=min(15, 6 + 2 * min(f["numbers"], 3)))
            pens = [Penalty(reason="Greeting / housekeeping language", points=6)] if f["greeting"] else []
            scored.append((subs.total() - sum(p.points for p in pens), a, b, subs, pens, text))
        scored.sort(key=lambda x: -x[0])
        used: list[tuple[float, float]] = []
        items: list[LongFormCandidate] = []
        for _sc, a, b, subs, pens, text in scored:
            s0, e0 = words[sents[a][0]]["start"], words[sents[b][1]]["end"]
            if any(not (e0 <= u0 or s0 >= u1) for u0, u1 in used):
                continue
            used.append((s0, e0))
            chapters = []
            step = max(60.0, (e0 - s0) / 5)
            nxt = s0
            for x, y in sents[a:b + 1]:
                if words[x]["start"] >= nxt:
                    chapters.append({"timestamp": words[x]["start"], "title": _hook_from(" ".join(w["word"] for w in words[x:y + 1]), 5)})
                    nxt = words[x]["start"] + step
            strongest = max((" ".join(w["word"] for w in words[x:y + 1]) for x, y in sents[a:b + 1]),
                            key=lambda t: len(STRONG.findall(t)) + len(NUMBER.findall(t)))
            topic_tokens = sorted((t for t in content_tokens(text) if not re.search(r"\d", t)),
                                  key=lambda tok: -text.lower().count(tok))[:4]
            title = _hook_from(strongest, 9).rstrip(".")
            items.append(LongFormCandidate(
                candidate_id=f"L{len(items) + 1:02d}", start_time=s0, end_time=e0,
                exact_opening_words=" ".join(w["word"] for w in words[sents[a][0]:sents[a][0] + 8]),
                exact_closing_words=" ".join(w["word"] for w in words[max(sents[a][0], sents[b][1] - 7):sents[b][1] + 1]),
                title=f"[DEMO] {title}", alternative_titles=[_hook_from(text, 8)],
                topic=" / ".join(topic_tokens) or "segment", summary=_hook_from(text, 40),
                reason_it_works="[DEMO - heuristic, not AI] dense with strong statements, numbers and questions",
                chapters=chapters, description=_hook_from(text, 35), tags=topic_tokens,
                subscores=subs, penalties=pens,
                thumbnail_concepts=[demo_thumbnail(strongest, speakers, words[sents[a][0]]["start"] + 5)],
            ))
            if len(items) >= ctx.target_count:
                break
        return LongFormList(items=items, notes="[DEMO] offline heuristic segments")

    def generate_hooks(self, clips: list[dict[str, Any]], rules: CampaignRules, campaign_name: str,
                       platforms: list[str]) -> HookVariantList:
        items = []
        for clip in clips:
            existing = [h for h in [clip.get("suggested_hook_text", "")] + list(clip.get("alternative_hooks", [])) if h]
            if len(existing) < 3:
                sents = re.split(r"(?<=[.!?])\s+", clip.get("transcript", ""))
                for s in sents:
                    h = _hook_from(s)
                    if h and h not in existing:
                        existing.append(h)
                    if len(existing) >= 3:
                        break
            hooks = [HookOption(text=h, rating=max(1, 7 - i)) for i, h in enumerate(existing[:3])]
            topic = clip.get("topic", "")
            items.append(HookVariants(
                candidate_id=clip["candidate_id"],
                hooks=hooks,
                tiktok_caption=f"{hooks[0].text if hooks else topic}",
                instagram_caption=f"{clip.get('summary', topic)}",
                youtube_title=(hooks[0].text if hooks else topic)[:70],
                youtube_description=clip.get("summary", "")[:200],
                hashtags=[],
                cover_text=_overlay_words(clip.get("transcript", "")) or (hooks[0].text if hooks else topic)[:24],
                thumbnail=demo_thumbnail(clip.get("transcript", ""), "", None, "; text in the upper third"),
            ))
        return HookVariantList(items=items)

    def check_compliance(self, clips: list[dict[str, Any]], rules: CampaignRules, rules_text: str) -> AIComplianceList:
        items = []
        for clip in clips:
            items.append(AIComplianceItem(
                candidate_id=clip["candidate_id"], status="UNKNOWN",
                reasons=["Demo mode: judgment-based compliance (prohibited content, misleading hooks) was not AI-checked."],
            ))
        return AIComplianceList(items=items)

    def refine_candidate(self, clip: dict[str, Any], rules: CampaignRules, platforms: list[str],
                         min_duration: float, max_duration: float) -> CandidateRefinement:
        text = clip.get("transcript", "")
        sents = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        f = _features(text, sents[0] if sents else "")
        subs, pens = _subscores(f, len(sents))
        return CandidateRefinement(
            candidate_id=clip["candidate_id"], subscores=subs, penalties=pens,
            reason_it_works="[DEMO - heuristic, not AI] re-scored from edited transcript",
            suggested_hook_text=_hook_from(sents[0]) if sents else "",
            alternative_hooks=[_hook_from(s) for s in sents[1:3]],
            boundary_feedback="[DEMO] Boundaries not evaluated by AI.",
        )
