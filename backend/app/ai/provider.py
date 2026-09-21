"""Provider-agnostic AI interface.

Swap in another provider by implementing :class:`AIProvider`. Only Gemini (runtime) and a
local Demo provider (no network, mocked analysis) are implemented.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..schemas.ai import (
    AIComplianceList,
    CampaignRules,
    CandidateRefinement,
    HookVariantList,
    LongFormList,
    VideoAnalysis,
    ViralCandidateList,
)


class AIProviderError(RuntimeError):
    """Raised with a message that is safe and useful to show in the UI."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class UsageRecorder(Protocol):
    def request(self, input_tokens: int = 0, output_tokens: int = 0) -> None: ...
    def upload(self) -> None: ...


class NullUsage:
    def request(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        pass

    def upload(self) -> None:
        pass


@dataclass
class VideoRef:
    uri: str
    mime_type: str
    name: str = ""


@dataclass
class AnalysisContext:
    project_name: str
    campaign_name: str
    rules: CampaignRules
    rules_text: str
    platforms: list[str]
    clip_count: int
    candidate_target: int
    min_duration: float
    max_duration: float
    source_duration: float
    source_sha256: str
    video_path: str
    transcript: dict[str, Any]
    transcript_prompt: str
    video_ref: VideoRef | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LongFormContext:
    project_name: str
    campaign_name: str
    source_duration: float
    source_sha256: str
    target_count: int
    min_duration: float
    max_duration: float
    video_analysis: dict[str, Any]
    transcript: dict[str, Any]
    transcript_prompt: str


class AIProvider(ABC):
    name: str = "base"
    model: str = ""

    @property
    def is_demo(self) -> bool:
        return False

    @abstractmethod
    def prompt_version(self, operation: str) -> str: ...

    @abstractmethod
    def parse_campaign_rules(self, rules_text: str) -> CampaignRules: ...

    @abstractmethod
    def generate_candidates(self, ctx: AnalysisContext) -> ViralCandidateList:
        """Understand the whole video and return analysis + ranked candidates."""

    def analyze_video(self, ctx: AnalysisContext) -> VideoAnalysis:
        """Whole-video understanding. Providers may share the request with generate_candidates."""
        return self.generate_candidates(ctx).video_analysis

    @abstractmethod
    def generate_hooks(self, clips: list[dict[str, Any]], rules: CampaignRules, campaign_name: str,
                       platforms: list[str]) -> HookVariantList: ...

    @abstractmethod
    def check_compliance(self, clips: list[dict[str, Any]], rules: CampaignRules, rules_text: str) -> AIComplianceList: ...

    @abstractmethod
    def refine_candidate(self, clip: dict[str, Any], rules: CampaignRules, platforms: list[str],
                         min_duration: float, max_duration: float) -> CandidateRefinement: ...

    def generate_long_form(self, ctx: LongFormContext) -> LongFormList:
        """Pick and package long-form segments (text-only: transcript + earlier video understanding)."""
        raise NotImplementedError

    def prepare_video(self, path: str, sha256: str, mime_type: str | None = None) -> VideoRef | None:
        """Upload/reference the video if the provider needs it. Default: not needed."""
        return None
