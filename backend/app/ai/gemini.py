"""Gemini provider (official google-genai SDK).

Cost controls:
- the source video is uploaded once per content hash and the file reference is cached (files expire ~48h)
- whole-video understanding and candidate selection share ONE multimodal request
- hooks / compliance / refinement are text-only requests
- long videos use low media resolution and reduced frame sampling
- every response is cached by the caller (source hash + prompt version + model + rules hash)
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from pydantic import BaseModel

from ..schemas.ai import (
    AIComplianceList,
    CampaignRules,
    CandidateRefinement,
    HookVariantList,
    LongFormList,
    ViralCandidateList,
)
from .jsonrepair import gemini_json_schema, validate_output
from .prompts import load_prompt
from .provider import AIProvider, AIProviderError, AnalysisContext, LongFormContext, NullUsage, UsageRecorder, VideoRef

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

MIME_BY_EXT = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
               ".mkv": "video/x-matroska"}


class FileRefStore:
    """Persistence hooks for Gemini file references (implemented by the service layer)."""

    def __init__(self, get: Callable[[str], VideoRef | None], put: Callable[[str, VideoRef, datetime | None], None],
                 on_cached: Callable[[], None] | None = None):
        self.get = get
        self.put = put
        self.on_cached = on_cached or (lambda: None)


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout_s: int = 600, usage: UsageRecorder | None = None,
                 file_store: FileRefStore | None = None, sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise AIProviderError("GEMINI_API_KEY is not configured.")
        self._api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.usage: UsageRecorder = usage or NullUsage()
        self.file_store = file_store
        self._client = None
        self._sleep = sleep

    # ------------------------------------------------------------------ plumbing

    @property
    def client(self):
        if self._client is None:
            from google import genai
            from google.genai import types

            self._client = genai.Client(api_key=self._api_key,
                                        http_options=types.HttpOptions(timeout=self.timeout_s * 1000))
        return self._client

    def prompt_version(self, operation: str) -> str:
        names = {
            "campaign_rules": ["campaign_parser"],
            "candidates": ["video_analysis", "viral_candidates"],
            "hooks": ["hook_generator"],
            "compliance": ["compliance_checker"],
            "refine": ["candidate_refinement"],
            "long_form": ["long_form_candidates"],
        }[operation]
        return "+".join(load_prompt(n).version for n in names)

    def _translate_error(self, e: Exception) -> AIProviderError:
        from google.genai import errors

        if isinstance(e, errors.APIError):
            code = getattr(e, "code", 0) or 0
            msg = (getattr(e, "message", "") or str(e))[:300]
            if code in (401, 403):
                return AIProviderError(f"Gemini rejected the API key or permissions ({code}). Check GEMINI_API_KEY.")
            if code == 404:
                return AIProviderError(f"Gemini model '{self.model}' was not found. Set GEMINI_MODEL to an available model.")
            if code == 429:
                return AIProviderError("Gemini rate limit / quota exceeded. Wait a bit and retry (results so far are cached).", True)
            if code >= 500:
                return AIProviderError(f"Gemini service error ({code}). Retry shortly.", True)
            return AIProviderError(f"Gemini request failed ({code}): {msg}")
        name = type(e).__name__.lower()
        if "timeout" in name:
            return AIProviderError("Gemini request timed out. Retry; very long videos may need a smaller Whisper transcript or a proxy.", True)
        if any(k in name for k in ("connect", "network", "remoteprotocol", "readerror")):
            return AIProviderError("Network error while contacting Gemini. Check your internet connection.", True)
        return AIProviderError(f"Unexpected Gemini error: {type(e).__name__}: {str(e)[:200]}")

    def _raw_generate(self, contents: list[Any], config: Any) -> Any:
        delays = [4, 15, 40]
        for attempt in range(len(delays) + 1):
            try:
                resp = self.client.models.generate_content(model=self.model, contents=contents, config=config)
                um = getattr(resp, "usage_metadata", None)
                self.usage.request(int(getattr(um, "prompt_token_count", 0) or 0),
                                   int(getattr(um, "candidates_token_count", 0) or 0))
                return resp
            except Exception as e:  # noqa: BLE001
                err = self._translate_error(e)
                if err.retryable and attempt < len(delays):
                    log.warning("Gemini call failed (%s); retrying in %ss", err, delays[attempt])
                    self._sleep(delays[attempt])
                    continue
                raise err from e
        raise AIProviderError("Gemini request failed after retries.", True)

    def _generate_structured(self, *, contents: list[Any], schema: type[T], system: str | None = None,
                             media_resolution: str | None = None, max_output_tokens: int = 16384,
                             temperature: float = 0.4, max_repairs: int = 2) -> T:
        from google.genai import errors, types

        use_schema = True

        def make_config(with_schema: bool, sys_instr: str | None) -> Any:
            kwargs: dict[str, Any] = dict(
                response_mime_type="application/json",
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            if sys_instr:
                kwargs["system_instruction"] = sys_instr
            if with_schema:
                kwargs["response_json_schema"] = gemini_json_schema(schema)
            if media_resolution:
                kwargs["media_resolution"] = media_resolution
            return types.GenerateContentConfig(**kwargs)

        try:
            resp = self._raw_generate(contents, make_config(True, system))
        except AIProviderError as e:
            cause = e.__cause__
            if isinstance(cause, errors.APIError) and (cause.code == 400) and "schema" in str(cause).lower():
                log.warning("Gemini rejected the JSON schema; retrying with prompt-only JSON instructions")
                use_schema = False
                resp = self._raw_generate(contents, make_config(False, system))
            else:
                raise

        raw = self._response_text(resp)
        obj, err = validate_output(schema, raw)
        repairs = 0
        while obj is None and repairs < max_repairs:
            repairs += 1
            log.warning("Gemini JSON invalid (%s); repair attempt %d", (err or "")[:200], repairs)
            prompt = load_prompt("json_repair").render(errors=err or "unknown", previous=(raw or "")[:60000])
            resp = self._raw_generate([prompt], make_config(use_schema, None))
            raw = self._response_text(resp)
            obj, err = validate_output(schema, raw)
        if obj is None:
            raise AIProviderError(f"Gemini returned invalid structured data after {max_repairs} repair attempts: {err}", True)
        return obj

    @staticmethod
    def _response_text(resp: Any) -> str:
        try:
            text = resp.text
        except Exception:  # noqa: BLE001 - blocked/empty candidates raise in some SDK versions
            text = None
        if text:
            return text
        reason = ""
        try:
            reason = str(resp.candidates[0].finish_reason)
        except Exception:  # noqa: BLE001
            pass
        return f"<<empty response {reason}>>"

    # ------------------------------------------------------------------ video upload

    def prepare_video(self, path: str, sha256: str, mime_type: str | None = None) -> VideoRef:
        from pathlib import Path

        mime = mime_type or MIME_BY_EXT.get(Path(path).suffix.lower(), "video/mp4")
        if self.file_store:
            cached = self.file_store.get(sha256)
            if cached:
                try:
                    f = self.client.files.get(name=cached.name)
                    if f.state and f.state.name == "ACTIVE":
                        self.file_store.on_cached()
                        log.info("Reusing cached Gemini file %s", cached.name)
                        return VideoRef(uri=f.uri or cached.uri, mime_type=f.mime_type or cached.mime_type, name=f.name or cached.name)
                except Exception as e:  # noqa: BLE001 - expired/deleted -> re-upload
                    log.info("Cached Gemini file unavailable (%s); uploading again", type(e).__name__)

        from google.genai import types

        try:
            f = self.client.files.upload(file=path, config=types.UploadFileConfig(mime_type=mime,
                                                                                  display_name=f"clipforge-{sha256[:16]}"))
        except Exception as e:  # noqa: BLE001
            raise self._translate_error(e) from e
        self.usage.upload()
        deadline = time.monotonic() + 45 * 60
        while not f.state or f.state.name == "PROCESSING":
            if time.monotonic() > deadline:
                raise AIProviderError("Gemini is still processing the uploaded video after 45 minutes.", True)
            self._sleep(5)
            try:
                f = self.client.files.get(name=f.name)
            except Exception as e:  # noqa: BLE001
                raise self._translate_error(e) from e
        if f.state.name != "ACTIVE":
            detail = getattr(f, "error", None)
            raise AIProviderError(f"Gemini could not process the video (state {f.state.name}). {detail or ''}".strip())
        ref = VideoRef(uri=f.uri, mime_type=f.mime_type or mime, name=f.name)
        expires = getattr(f, "expiration_time", None) or (datetime.now(UTC) + timedelta(hours=47))
        if self.file_store:
            self.file_store.put(sha256, ref, expires)
        return ref

    # ------------------------------------------------------------------ operations

    def parse_campaign_rules(self, rules_text: str) -> CampaignRules:
        prompt = load_prompt("campaign_parser").render(rules_text=rules_text[:30000])
        rules = self._generate_structured(contents=[prompt], schema=CampaignRules, temperature=0.0, max_output_tokens=4096)
        return rules

    def generate_candidates(self, ctx: AnalysisContext) -> ViralCandidateList:
        from google.genai import types

        if ctx.video_ref is None:
            ctx.video_ref = self.prepare_video(ctx.video_path, ctx.source_sha256)
        system = load_prompt("video_analysis").render()
        user = load_prompt("viral_candidates").render(
            project_name=ctx.project_name,
            campaign_name=ctx.campaign_name or "(none)",
            platforms=", ".join(ctx.platforms) or "tiktok, instagram, youtube_shorts",
            clip_count=ctx.clip_count,
            candidate_target=ctx.candidate_target,
            source_duration=round(ctx.source_duration, 1),
            min_duration=round(ctx.min_duration),
            max_duration=round(ctx.max_duration),
            rules_json=json.dumps(ctx.rules.model_dump(), indent=1),
            rules_text=(ctx.rules_text or "(no campaign rules supplied)")[:20000],
            transcript=ctx.transcript_prompt,
        )
        video_part = types.Part(file_data=types.FileData(file_uri=ctx.video_ref.uri, mime_type=ctx.video_ref.mime_type))
        if ctx.source_duration > 45 * 60:
            video_part.video_metadata = types.VideoMetadata(fps=0.5)
        media_res = "MEDIA_RESOLUTION_LOW" if ctx.source_duration > 15 * 60 else None
        return self._generate_structured(contents=[video_part, user], schema=ViralCandidateList, system=system,
                                         media_resolution=media_res, max_output_tokens=32768, temperature=0.5)

    def generate_long_form(self, ctx: LongFormContext) -> LongFormList:
        from ..candidates.longform import ceil_half

        prompt = load_prompt("long_form_candidates").render(
            project_name=ctx.project_name,
            campaign_name=ctx.campaign_name or "(none)",
            source_duration=round(ctx.source_duration, 1),
            min_duration=round(ctx.min_duration),
            max_duration=round(ctx.max_duration),
            target_count=ctx.target_count,
            min_count=ceil_half(ctx.target_count),
            max_count=ctx.target_count + 2,
            video_analysis=json.dumps(ctx.video_analysis, ensure_ascii=False, indent=1)[:20000],
            transcript=ctx.transcript_prompt,
        )
        return self._generate_structured(contents=[prompt], schema=LongFormList, max_output_tokens=32768,
                                         temperature=0.5)

    def generate_hooks(self, clips: list[dict[str, Any]], rules: CampaignRules, campaign_name: str,
                       platforms: list[str]) -> HookVariantList:
        prompt = load_prompt("hook_generator").render(
            campaign_name=campaign_name or "(none)",
            required_hashtags=", ".join(rules.required_hashtags) or "none",
            required_mentions=", ".join(rules.required_mentions) or "none",
            required_cta=rules.required_cta or "none",
            prohibited_content="; ".join(rules.prohibited_content) or "none stated",
            platforms=", ".join(platforms) or "tiktok, instagram, youtube_shorts",
            clips_json=json.dumps(clips, ensure_ascii=False, indent=1),
        )
        return self._generate_structured(contents=[prompt], schema=HookVariantList, temperature=0.7)

    def check_compliance(self, clips: list[dict[str, Any]], rules: CampaignRules, rules_text: str) -> AIComplianceList:
        prompt = load_prompt("compliance_checker").render(
            rules_json=json.dumps(rules.model_dump(), indent=1),
            rules_text=(rules_text or "(none)")[:20000],
            clips_json=json.dumps(clips, ensure_ascii=False, indent=1),
        )
        return self._generate_structured(contents=[prompt], schema=AIComplianceList, temperature=0.0)

    def refine_candidate(self, clip: dict[str, Any], rules: CampaignRules, platforms: list[str],
                         min_duration: float, max_duration: float) -> CandidateRefinement:
        prompt = load_prompt("candidate_refinement").render(
            rules_json=json.dumps(rules.model_dump(), indent=1),
            platforms=", ".join(platforms),
            min_duration=round(min_duration),
            max_duration=round(max_duration),
            previous_json=json.dumps(clip.get("previous", {}), ensure_ascii=False)[:20000],
            start=round(clip["start"], 2),
            end=round(clip["end"], 2),
            duration=round(clip["end"] - clip["start"], 1),
            clip_transcript=clip.get("transcript", "")[:20000],
            candidate_id=clip["candidate_id"],
        )
        return self._generate_structured(contents=[prompt], schema=CandidateRefinement, temperature=0.3)
