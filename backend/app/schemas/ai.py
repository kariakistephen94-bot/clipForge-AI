"""Structured-output schemas for AI responses.

Validators are deliberately forgiving about *shape* (clock-string timestamps, out-of-range
scores are clamped, hashtags normalised) but strict about *meaning* (required fields, types).
Unknown campaign requirements stay ``None`` -- we never invent rules.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..utils.timeutil import parse_timestamp

ComplianceStatus = Literal["COMPLIANT", "WARNING", "FAILED", "UNKNOWN"]


def _clamp(v: Any, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, f))


def _as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return list(v)


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# --------------------------------------------------------------------------- campaign rules


class CampaignRules(_Base):
    min_duration: float | None = Field(None, description="Minimum clip length in seconds, null if not stated")
    max_duration: float | None = Field(None, description="Maximum clip length in seconds, null if not stated")
    platforms: list[str] = Field(default_factory=list, description="Allowed/required platforms, empty if not stated")
    required_hashtags: list[str] = Field(default_factory=list)
    required_mentions: list[str] = Field(default_factory=list)
    required_cta: str | None = None
    required_links: list[str] = Field(default_factory=list)
    captions_allowed: bool | None = None
    captions_required: bool | None = None
    broll_allowed: bool | None = None
    split_screen_allowed: bool | None = None
    music_allowed: bool | None = None
    source_modification_rules: list[str] = Field(default_factory=list)
    prohibited_content: list[str] = Field(default_factory=list)
    special_requirements: list[str] = Field(default_factory=list)
    unknown_requirements: list[str] = Field(
        default_factory=list, description="Names of the fields above that the rules did not state"
    )

    @field_validator("min_duration", "max_duration", mode="before")
    @classmethod
    def _dur(cls, v: Any) -> float | None:
        if v in (None, "", "null", "unknown"):
            return None
        try:
            return parse_timestamp(v)
        except (ValueError, TypeError):
            return None

    @field_validator(
        "platforms", "required_links", "source_modification_rules", "prohibited_content",
        "special_requirements", "unknown_requirements", mode="before",
    )
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return [str(x).strip() for x in _as_list(v) if str(x).strip()]

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for p in v:
            key = normalize_platform(p)
            if key and key not in out:
                out.append(key)
        return out

    @field_validator("required_hashtags", mode="before")
    @classmethod
    def _hashtags(cls, v: Any) -> list[str]:
        out: list[str] = []
        for x in _as_list(v):
            tag = "#" + str(x).strip().lstrip("#").replace(" ", "")
            if len(tag) > 1 and tag.lower() not in [o.lower() for o in out]:
                out.append(tag)
        return out

    @field_validator("required_mentions", mode="before")
    @classmethod
    def _mentions(cls, v: Any) -> list[str]:
        out: list[str] = []
        for x in _as_list(v):
            m = "@" + str(x).strip().lstrip("@").replace(" ", "")
            if len(m) > 1 and m.lower() not in [o.lower() for o in out]:
                out.append(m)
        return out

    @field_validator("required_cta", mode="before")
    @classmethod
    def _cta(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return None if s.lower() in ("", "null", "none", "unknown", "n/a") else s

    @model_validator(mode="after")
    def _order(self) -> CampaignRules:
        if self.min_duration is not None and self.max_duration is not None and self.min_duration > self.max_duration:
            self.min_duration, self.max_duration = self.max_duration, self.min_duration
        return self


PLATFORM_ALIASES = {
    "tiktok": "tiktok",
    "tik tok": "tiktok",
    "instagram": "instagram",
    "instagram reels": "instagram",
    "reels": "instagram",
    "ig": "instagram",
    "youtube": "youtube_shorts",
    "youtube shorts": "youtube_shorts",
    "youtube_shorts": "youtube_shorts",
    "shorts": "youtube_shorts",
    "yt shorts": "youtube_shorts",
}


def normalize_platform(p: str) -> str | None:
    key = re.sub(r"[^a-z_ ]", "", str(p).lower()).strip()
    return PLATFORM_ALIASES.get(key, key.replace(" ", "_") or None)


# --------------------------------------------------------------------------- video analysis


class Speaker(_Base):
    label: str = ""
    description: str = ""
    visual_position: str = ""


class Section(_Base):
    start: float = 0.0
    end: float = 0.0
    title: str = ""
    summary: str = ""

    @field_validator("start", "end", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> float:
        return parse_timestamp(v)


class VideoAnalysis(_Base):
    summary: str = ""
    content_type: str = ""
    speakers: list[Speaker] = Field(default_factory=list)
    main_topics: list[str] = Field(default_factory=list)
    tone: str = ""
    visual_style: str = ""
    audio_quality_notes: str = ""
    sections: list[Section] = Field(default_factory=list)


# --------------------------------------------------------------------------- candidates

SUBSCORE_MAX: dict[str, float] = {
    "hook": 20,
    "clarity": 15,
    "curiosity": 15,
    "emotion": 10,
    "specificity": 10,
    "shareability": 10,
    "retention": 10,
    "visual": 5,
    "campaign_fit": 5,
}


class Subscores(_Base):
    hook: float = Field(0, description="Hook strength 0-20")
    clarity: float = Field(0, description="Standalone clarity 0-15")
    curiosity: float = Field(0, description="Curiosity / open loop 0-15")
    emotion: float = Field(0, description="Emotion / tension 0-10")
    specificity: float = Field(0, description="Specificity 0-10")
    shareability: float = Field(0, description="Shareability 0-10")
    retention: float = Field(0, description="Retention potential 0-10")
    visual: float = Field(0, description="Visual potential 0-5")
    campaign_fit: float = Field(0, description="Campaign fit 0-5")

    @model_validator(mode="before")
    @classmethod
    def _clamp_all(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            aliases = {"standalone_clarity": "clarity", "open_loop": "curiosity", "campaign": "campaign_fit",
                       "hook_strength": "hook", "retention_potential": "retention", "visual_potential": "visual"}
            for a, k in aliases.items():
                if a in data and k not in data:
                    data[k] = data.pop(a)
            for k, hi in SUBSCORE_MAX.items():
                if k in data:
                    data[k] = round(_clamp(data[k], 0, hi), 1)
        return data

    def total(self) -> float:
        return round(sum(getattr(self, k) for k in SUBSCORE_MAX), 1)


class Penalty(_Base):
    reason: str
    points: float = 0

    @field_validator("points", mode="before")
    @classmethod
    def _pts(cls, v: Any) -> float:
        return round(abs(_clamp(v, -30, 30)), 1)


class ZoomPoint(_Base):
    timestamp: float
    reason: str = ""
    kind: str = "statement"

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> float:
        return parse_timestamp(v)


class BrollPoint(_Base):
    timestamp: float
    duration: float = 2.0
    query: str = ""
    reason: str = ""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> float:
        return parse_timestamp(v)

    @field_validator("duration", mode="before")
    @classmethod
    def _d(cls, v: Any) -> float:
        return _clamp(v, 0.5, 4.0)


SOUND_CUE_CATEGORIES = ("whoosh", "impact", "riser", "pop", "click", "ding", "cash", "camera", "typing", "paper", "tech",
                        "wrong", "tick", "tension", "reaction", "comedic")


def _intensity(v: Any) -> float:
    words = {"subtle": 0.7, "soft": 0.7, "low": 0.7, "light": 0.7, "medium": 1.0, "normal": 1.0, "strong": 1.15,
             "high": 1.15, "hard": 1.15}
    if isinstance(v, str) and v.strip().lower() in words:
        return words[v.strip().lower()]
    f = _clamp(v, 0, 10) if v is not None else 1.0
    if f > 1.5:  # a 1-3 (or 1-10) scale
        f = 0.7 + 0.45 * min(1.0, (f - 1) / (2 if f <= 3 else 9))
    return round(max(0.5, min(1.2, f or 1.0)), 2)


class SoundCue(_Base):
    timestamp: float = Field(description="Absolute seconds in the source video where the sound should land")
    category: str = Field(description="One of: " + ", ".join(SOUND_CUE_CATEGORIES))
    reason: str = ""
    intensity: float = Field(1.0, description="subtle 0.7, medium 1.0, strong 1.15")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> float:
        return parse_timestamp(v)

    @field_validator("category", mode="before")
    @classmethod
    def _cat(cls, v: Any) -> str:
        return str(v or "").strip().lower()

    @field_validator("intensity", mode="before")
    @classmethod
    def _int(cls, v: Any) -> float:
        return _intensity(v)


class PlatformFit(_Base):
    tiktok: float = 0
    instagram: float = 0
    youtube_shorts: float = 0
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _clamp_all(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            for k in ("tiktok", "instagram", "youtube_shorts"):
                if k in data:
                    data[k] = _clamp(data[k], 0, 10)
        return data


class CandidateCompliance(_Base):
    status: ComplianceStatus = "UNKNOWN"
    reasons: list[str] = Field(default_factory=list)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, v: Any) -> str:
        s = str(v or "UNKNOWN").upper().strip()
        return {"PASS": "COMPLIANT", "OK": "COMPLIANT", "FAIL": "FAILED", "WARN": "WARNING"}.get(
            s, s if s in ("COMPLIANT", "WARNING", "FAILED", "UNKNOWN") else "UNKNOWN"
        )

    @field_validator("reasons", mode="before")
    @classmethod
    def _r(cls, v: Any) -> list[str]:
        return [str(x) for x in _as_list(v)]


class StoryStructure(_Base):
    hook: str = ""
    context: str = ""
    tension: str = ""
    payoff: str = ""


class ViralCandidate(_Base):
    candidate_id: str
    start_time: float
    end_time: float
    duration: float | None = None
    exact_opening_words: str = ""
    exact_closing_words: str = ""
    topic: str = ""
    summary: str = ""
    reason_it_works: str = ""
    target_audience: str = ""
    hook_type: str = ""
    story_structure: StoryStructure = Field(default_factory=StoryStructure)
    suggested_hook_text: str = ""
    alternative_hooks: list[str] = Field(default_factory=list)
    editing_strategy: str = ""
    caption_emphasis_words: list[str] = Field(default_factory=list)
    suggested_zoom_points: list[ZoomPoint] = Field(default_factory=list)
    suggested_broll_points: list[BrollPoint] = Field(default_factory=list)
    sound_cues: list[SoundCue] = Field(default_factory=list)
    platform_fit: PlatformFit = Field(default_factory=PlatformFit)
    campaign_compliance: CandidateCompliance = Field(default_factory=CandidateCompliance)
    viral_score: float = 0
    subscores: Subscores = Field(default_factory=Subscores)
    penalties: list[Penalty] = Field(default_factory=list)

    @field_validator("candidate_id", mode="before")
    @classmethod
    def _cid(cls, v: Any) -> str:
        s = re.sub(r"[^A-Za-z0-9_-]", "", str(v or ""))[:40]
        if not s:
            raise ValueError("candidate_id is required")
        return s

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> float:
        return parse_timestamp(v)

    @field_validator("alternative_hooks", "caption_emphasis_words", mode="before")
    @classmethod
    def _strs(cls, v: Any) -> list[str]:
        return [str(x).strip() for x in _as_list(v) if str(x).strip()]

    @field_validator("viral_score", mode="before")
    @classmethod
    def _score(cls, v: Any) -> float:
        return _clamp(v, 0, 100)

    @model_validator(mode="after")
    def _check(self) -> ViralCandidate:
        if self.end_time <= self.start_time:
            raise ValueError(f"end_time must be after start_time for {self.candidate_id}")
        self.duration = round(self.end_time - self.start_time, 3)
        return self

    def computed_score(self) -> float:
        """Rubric total minus penalties -- the number we actually display."""
        pen = sum(p.points for p in self.penalties)
        return round(max(0.0, min(100.0, self.subscores.total() - pen)), 1)


class ViralCandidateList(_Base):
    video_analysis: VideoAnalysis = Field(default_factory=VideoAnalysis)
    candidates: list[ViralCandidate] = Field(default_factory=list)

    @field_validator("candidates", mode="before")
    @classmethod
    def _drop_invalid(cls, v: Any) -> Any:
        """Keep valid candidates even if a few items are malformed."""
        if not isinstance(v, list):
            return v
        good = []
        for item in v:
            try:
                good.append(ViralCandidate.model_validate(item))
            except Exception:  # noqa: BLE001 - one bad item must not sink the batch
                continue
        if v and not good:
            raise ValueError("no valid candidates in response")
        return good


# --------------------------------------------------------------------------- thumbnails


class ThumbnailConcept(_Base):
    """One click-worthy thumbnail idea, described as parts so prompts can be assembled per image model."""

    concept: str = Field("", description="Short name of the idea, e.g. 'Shocked face + wall of GPUs'")
    emotion: str = Field("", description="The single emotion the face/scene sells (shock, curiosity, fear, joy...)")
    subject: str = Field("", description="Who is shown and exactly how: the real speaker's look, expression, pose, gaze")
    scene: str = Field("", description="Background / setting, simplified")
    visual_elements: list[str] = Field(default_factory=list, description="0-2 props or visual metaphors")
    text_overlay: str = Field("", description="2-4 words, complements (never repeats) the title")
    text_style: str = Field("", description="Font weight, colour, highlight word, placement")
    color_palette: str = ""
    composition: str = Field("", description="Where the face, text and object sit (rule of thirds)")
    lighting: str = ""
    frame_timestamp: float | None = Field(None, description="Absolute seconds of a source frame with this expression")
    why_it_works: str = ""

    @field_validator("text_overlay", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        words = str(v or "").replace("\n", " ").split()
        return " ".join(words[:5])

    @field_validator("visual_elements", mode="before")
    @classmethod
    def _elements(cls, v: Any) -> list[str]:
        return [str(x).strip() for x in _as_list(v) if str(x).strip()][:3]

    @field_validator("frame_timestamp", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> float | None:
        if v in (None, "", "null"):
            return None
        try:
            return parse_timestamp(v)
        except (ValueError, TypeError):
            return None


# --------------------------------------------------------------------------- long-form

LONG_SUBSCORE_MAX: dict[str, float] = {
    "opening": 20,
    "arc": 20,
    "value": 20,
    "retention": 15,
    "emotion": 10,
    "packaging": 15,
}


class LongFormSubscores(_Base):
    opening: float = Field(0, description="First 30 seconds hook / promise 0-20")
    arc: float = Field(0, description="Complete, self-contained arc with a real ending 0-20")
    value: float = Field(0, description="Density of insight, story or entertainment 0-20")
    retention: float = Field(0, description="Keeps unfolding; few slow stretches 0-15")
    emotion: float = Field(0, description="Tension, humour, stakes 0-10")
    packaging: float = Field(0, description="Title + thumbnail click potential without lying 0-15")

    @model_validator(mode="before")
    @classmethod
    def _clamp_all(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            for k, hi in LONG_SUBSCORE_MAX.items():
                if k in data:
                    data[k] = round(_clamp(data[k], 0, hi), 1)
        return data

    def total(self) -> float:
        return round(sum(getattr(self, k) for k in LONG_SUBSCORE_MAX), 1)


class Chapter(_Base):
    timestamp: float
    title: str = ""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> float:
        return parse_timestamp(v)


class ColdOpen(_Base):
    start_time: float
    end_time: float
    reason: str = ""

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> float:
        return parse_timestamp(v)


class LongFormCandidate(_Base):
    candidate_id: str
    start_time: float
    end_time: float
    exact_opening_words: str = ""
    exact_closing_words: str = ""
    title: str = ""
    alternative_titles: list[str] = Field(default_factory=list)
    topic: str = ""
    summary: str = ""
    reason_it_works: str = ""
    target_audience: str = ""
    chapters: list[Chapter] = Field(default_factory=list)
    cold_open: ColdOpen | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    subscores: LongFormSubscores = Field(default_factory=LongFormSubscores)
    penalties: list[Penalty] = Field(default_factory=list)
    score: float = 0
    thumbnail_concepts: list[ThumbnailConcept] = Field(default_factory=list)

    @field_validator("candidate_id", mode="before")
    @classmethod
    def _cid(cls, v: Any) -> str:
        s = re.sub(r"[^A-Za-z0-9_-]", "", str(v or ""))[:40]
        if not s:
            raise ValueError("candidate_id is required")
        return s

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _ts(cls, v: Any) -> float:
        return parse_timestamp(v)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, v: Any) -> str:
        return " ".join(str(v or "").split())[:100]

    @field_validator("alternative_titles", "tags", mode="before")
    @classmethod
    def _strs(cls, v: Any) -> list[str]:
        return [" ".join(str(x).split())[:100] for x in _as_list(v) if str(x).strip()][:12]

    @field_validator("cold_open", mode="before")
    @classmethod
    def _cold(cls, v: Any) -> Any:
        if not v:
            return None
        try:
            c = ColdOpen.model_validate(v)
        except Exception:  # noqa: BLE001 - a bad teaser must not drop the whole candidate
            return None
        return c if c.end_time > c.start_time else None

    @field_validator("chapters", mode="before")
    @classmethod
    def _chapters(cls, v: Any) -> list[Any]:
        out = []
        for x in _as_list(v):
            try:
                out.append(Chapter.model_validate(x))
            except Exception:  # noqa: BLE001
                continue
        return out

    @field_validator("thumbnail_concepts", mode="before")
    @classmethod
    def _thumbs(cls, v: Any) -> list[Any]:
        out = []
        for x in _as_list(v):
            try:
                out.append(ThumbnailConcept.model_validate(x))
            except Exception:  # noqa: BLE001
                continue
        return out[:3]

    @field_validator("score", mode="before")
    @classmethod
    def _score(cls, v: Any) -> float:
        return _clamp(v, 0, 100)

    @model_validator(mode="after")
    def _check(self) -> LongFormCandidate:
        if self.end_time <= self.start_time:
            raise ValueError(f"end_time must be after start_time for {self.candidate_id}")
        return self

    def computed_score(self) -> float:
        pen = sum(p.points for p in self.penalties)
        return round(max(0.0, min(100.0, self.subscores.total() - pen)), 1)


class LongFormList(_Base):
    items: list[LongFormCandidate] = Field(default_factory=list)
    notes: str = ""

    @field_validator("items", mode="before")
    @classmethod
    def _drop_invalid(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        good = []
        for item in v:
            try:
                good.append(LongFormCandidate.model_validate(item))
            except Exception:  # noqa: BLE001
                continue
        if v and not good:
            raise ValueError("no valid long-form candidates in response")
        return good


# --------------------------------------------------------------------------- hooks & copy


class HookOption(_Base):
    text: str
    rating: float = 0

    @field_validator("rating", mode="before")
    @classmethod
    def _r(cls, v: Any) -> float:
        return _clamp(v, 0, 10)


class HookVariants(_Base):
    candidate_id: str
    hooks: list[HookOption] = Field(default_factory=list)
    tiktok_caption: str = ""
    instagram_caption: str = ""
    youtube_title: str = ""
    youtube_description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    cover_text: str = Field("", description="2-4 word cover/thumbnail text for Shorts & Reels")
    thumbnail: ThumbnailConcept | None = None

    @field_validator("thumbnail", mode="before")
    @classmethod
    def _thumb(cls, v: Any) -> Any:
        if not v:
            return None
        try:
            return ThumbnailConcept.model_validate(v)
        except Exception:  # noqa: BLE001
            return None

    @field_validator("hashtags", mode="before")
    @classmethod
    def _tags(cls, v: Any) -> list[str]:
        out = []
        for x in _as_list(v):
            t = "#" + str(x).strip().lstrip("#").replace(" ", "")
            if len(t) > 1 and t not in out:
                out.append(t)
        return out[:8]


class HookVariantList(_Base):
    items: list[HookVariants] = Field(default_factory=list)


class CandidateRefinement(_Base):
    candidate_id: str
    subscores: Subscores = Field(default_factory=Subscores)
    penalties: list[Penalty] = Field(default_factory=list)
    reason_it_works: str = ""
    suggested_hook_text: str = ""
    alternative_hooks: list[str] = Field(default_factory=list)
    boundary_feedback: str = ""


# --------------------------------------------------------------------------- compliance


class ComplianceCheck(_Base):
    rule: str
    status: ComplianceStatus
    detail: str = ""
    source: Literal["local", "ai"] = "local"


class ComplianceReport(_Base):
    status: ComplianceStatus = "UNKNOWN"
    checks: list[ComplianceCheck] = Field(default_factory=list)
    manual_checks: list[str] = Field(default_factory=list)


class AIComplianceItem(_Base):
    candidate_id: str
    status: ComplianceStatus = "UNKNOWN"
    reasons: list[str] = Field(default_factory=list)

    @field_validator("status", mode="before")
    @classmethod
    def _s(cls, v: Any) -> str:
        return CandidateCompliance._status(v)  # type: ignore[call-arg]


class AIComplianceList(_Base):
    items: list[AIComplianceItem] = Field(default_factory=list)


# --------------------------------------------------------------------------- edit plan (internal)


class EditPlan(_Base):
    candidate_id: str
    source_start: float
    source_end: float
    keep_segments: list[tuple[float, float]]
    hook_text: str = ""
    caption_style: str = "bold_viral"
    framing: str = "auto"
    zoom_events: list[dict[str, Any]] = Field(default_factory=list)
    broll_events: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
