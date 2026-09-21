"""Analysis pipeline: source -> metadata -> proxy -> audio -> Whisper -> rules -> Gemini -> snap/dedupe/rank."""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete

from ..ai.provider import AIProviderError, AnalysisContext
from ..ai.rules_heuristic import parse_rules_heuristic, unknown_fields
from ..ai.service import cached_call, get_provider
from ..candidates.dedupe import DedupeItem, dedupe
from ..candidates.scoring import final_score, local_penalties, rank
from ..candidates.snapping import clip_text, snap_clip
from ..config import get_settings
from ..db import session_scope
from ..models import Analysis, Candidate, GeminiFileRef, Project, TranscriptRecord
from ..paths import project_dir, source_cache_dir
from ..schemas.ai import CampaignRules, ViralCandidateList
from ..services.compliance import ClipFacts, evaluate_compliance
from ..services.ffmpeg import FFmpegError, build_extract_audio_cmd, build_proxy_cmd, ffmpeg_bin, run_ffmpeg
from ..services.ingest import YouTubeBlockedError, download_shared_file, download_youtube
from ..services.preferences import get_preferences
from ..services.probe import needs_proxy, probe_video
from ..services.transcribe import (
    empty_transcript,
    flatten_words,
    transcribe_audio,
    transcript_for_prompt,
    write_transcript_files,
)
from ..utils.hashing import sha256_file, sha256_text
from . import youtube_retry
from .progress import JobTracker

log = logging.getLogger(__name__)

ANALYSIS_STEPS = [
    ("source", "Preparing source"),
    ("metadata", "Extracting metadata"),
    ("proxy", "Preview / AI proxy"),
    ("audio", "Extracting audio"),
    ("transcribe", "Transcribing locally (Whisper)"),
    ("rules", "Parsing campaign rules"),
    ("upload", "Sending video to Gemini"),
    ("analyze", "Understanding video & finding moments"),
    ("rank", "Snapping, de-duplicating & ranking"),
    ("longform", "Finding long-form segments"),
    ("ready", "Ready for review"),
]


def candidate_target(clip_count: int, max_candidates: int, source_duration: float, min_duration: float) -> int:
    target = min(max_candidates, max(10, math.ceil(clip_count * 1.5)))
    possible = max(3, int(source_duration // max(min_duration, 8.0)))
    return max(1, min(target, possible))


def resolve_durations(mode: str, rules: CampaignRules, manual_min: float, manual_max: float,
                      default_min: float, default_max: float, source_duration: float) -> tuple[float, float, list[str]]:
    notes = []
    if mode == "auto":
        lo = rules.min_duration if rules.min_duration is not None else default_min
        hi = rules.max_duration if rules.max_duration is not None else default_max
        if rules.min_duration is None or rules.max_duration is None:
            notes.append("Some duration limits not stated in rules; using defaults for the rest.")
    else:
        lo, hi = manual_min, manual_max
        if rules.max_duration is not None and hi > rules.max_duration:
            notes.append(f"Manual maximum {hi:g}s exceeds campaign maximum {rules.max_duration:g}s.")
        if rules.min_duration is not None and lo < rules.min_duration:
            notes.append(f"Manual minimum {lo:g}s is below campaign minimum {rules.min_duration:g}s.")
    if lo > hi:
        lo, hi = hi, lo
    hi = min(hi, source_duration)
    lo = min(lo, hi)
    return float(lo), float(hi), notes


def run_analysis(project_id: str, tracker: JobTracker, opts: dict[str, Any]) -> None:
    for key, label in ANALYSIS_STEPS:
        tracker.add_step(key, label)
    try:
        _run(project_id, tracker, opts)
    except YouTubeBlockedError as e:
        retry_at = youtube_retry.record_block(project_id, opts, attempted=not e.held_back)
        with session_scope() as s:
            p = s.get(Project, project_id)
            if p:
                p.status, p.error = "waiting", None
        local = retry_at.astimezone().strftime("%H:%M")
        tracker.step("source", "error", detail=f"YouTube is blocking this connection; retrying automatically at {local}")
        tracker.finish("error", error=f"{e} ClipForge AI will try again automatically at {local}.")
        return
    except Exception as e:
        with session_scope() as s:
            p = s.get(Project, project_id)
            if p:
                p.status = "error"
                p.error = str(e)
        raise
    if opts.get("auto_render"):
        from ..models import LongFormClip
        from .longform import run_long_render
        from .render_batch import run_render

        run_render(project_id, tracker, None, {})
        with session_scope() as s:
            has_long = s.query(LongFormClip).filter(LongFormClip.project_id == project_id,
                                                    LongFormClip.selected.is_(True)).count() > 0
        if has_long:
            tracker.status = "running"
            run_long_render(project_id, tracker, None, {})


def _run(project_id: str, tracker: JobTracker, opts: dict[str, Any]) -> None:
    prefs = get_preferences()
    whisper_model = opts.get("whisper_model") or prefs.whisper_model

    with session_scope() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise ValueError("Project not found.")
        if p.source is None:
            raise ValueError("Project has no source video.")
        p.status, p.error = "analyzing", None
        if "use_gemini" in opts and opts["use_gemini"] is not None:
            p.use_gemini = bool(opts["use_gemini"])
        P: dict[str, Any] = {"name": p.name, "campaign": p.campaign_name, "rules_text": p.campaign_rules_text or "", "platforms": list(p.platforms or []),
             "clip_count": p.desired_clip_count, "mode": p.duration_mode, "min": p.min_duration, "max": p.max_duration,
             "use_gemini": p.use_gemini, "long_form_mode": p.long_form_mode, "long_form_count": p.long_form_count}
        src = p.source
        S: dict[str, Any] = {"origin": src.origin, "url": src.source_url, "path": src.stored_path, "sha": src.sha256}

    # ---- source --------------------------------------------------------------------------
    tracker.step("source", "running")
    path = Path(S["path"]) if S["path"] else None
    if S["origin"] in ("youtube", "drive", "dropbox") and (path is None or not path.exists()):
        tracker.step("source", detail="Downloading video...")
        def dl_progress(frac: float, detail: str) -> None:
            tracker.step("source", progress=frac * 0.8 if frac >= 0 else None, detail=detail)

        if S["origin"] == "youtube":
            until = youtube_retry.blocked_until()
            if until and not (opts.get("_scheduled") or opts.get("force_youtube")):
                raise YouTubeBlockedError(
                    "YouTube is currently blocking downloads from this connection, so ClipForge AI is waiting "
                    "instead of sending more requests (more requests tend to make the block last longer).",
                    held_back=True)
            path, info = download_youtube(S["url"], project_dir(project_id, "source"), on_progress=dl_progress)
            title = info.get("title") or "youtube"
        else:
            path, title = download_shared_file(S["url"], project_dir(project_id, "source"),
                                               get_settings().max_upload_bytes, on_progress=dl_progress)
        with session_scope() as s:
            src_row = s.get(Project, project_id).source  # type: ignore[union-attr]
            src_row.stored_path, src_row.size_bytes = str(path), path.stat().st_size  # type: ignore[union-attr]
            src_row.original_filename = title[:300]  # type: ignore[union-attr]
        if S["origin"] == "youtube":
            youtube_retry.record_success()  # waiting projects start on the scheduler's next tick
    if path is None or not path.exists():
        raise ValueError("Source video file is missing. Upload it again.")
    sha = S["sha"]
    if not sha:
        tracker.step("source", detail="Hashing file for cache...")
        sha = sha256_file(path, progress=lambda f: tracker.step("source", progress=0.8 + 0.2 * f))
        with session_scope() as s:
            s.get(Project, project_id).source.sha256 = sha  # type: ignore[union-attr]
    tracker.step("source", "done", detail=f"{path.name} - sha256 {sha[:12]}")

    # ---- metadata ------------------------------------------------------------------------
    tracker.step("metadata", "running")
    meta = probe_video(path)
    with session_scope() as s:
        s.get(Project, project_id).source.probe = meta.model_dump()  # type: ignore[union-attr]
    audio_note = "" if meta.has_audio else " - no audio"
    if meta.audio_streams > 1:
        audio_note = f" - {meta.audio_streams} audio tracks (using default)"
    tracker.step("metadata", "done", detail=f"{meta.display_width}x{meta.display_height} {meta.fps:g}fps {meta.duration:.0f}s "
                                           f"{meta.video_codec}/{meta.audio_codec or '-'}{audio_note}")

    cache = source_cache_dir(sha)
    ff = ffmpeg_bin()

    # ---- proxy ---------------------------------------------------------------------------
    proxy_path: Path | None = None
    too_big_for_gemini = meta.size_bytes > int(1.9 * 1024**3)
    if needs_proxy(meta, path) or too_big_for_gemini:
        proxy_path = cache / "proxy_540p.mp4"
        if proxy_path.exists() and proxy_path.stat().st_size > 0:
            tracker.step("proxy", "done", detail="Reused cached proxy")
        else:
            tracker.step("proxy", "running", detail="Encoding lightweight 540p proxy...")
            tmp = cache / "proxy_540p.tmp.mp4"
            run_ffmpeg(build_proxy_cmd(path, tmp, meta.display_height, meta.has_audio, ffmpeg=ff), duration=meta.duration,
                       on_progress=lambda f: tracker.step("proxy", progress=f))
            tmp.replace(proxy_path)
            tracker.step("proxy", "done", detail="540p proxy created")
    else:
        tracker.step("proxy", "skipped", detail="Original is already web/AI friendly")
    with session_scope() as s:
        s.get(Project, project_id).source.proxy_path = str(proxy_path) if proxy_path else None  # type: ignore[union-attr]

    # ---- audio ---------------------------------------------------------------------------
    audio_path: Path | None = None
    if meta.has_audio:
        audio_path = cache / "audio_16k.wav"
        if audio_path.exists() and audio_path.stat().st_size > 1000:
            tracker.step("audio", "done", detail="Reused cached audio")
        else:
            tracker.step("audio", "running")
            try:
                run_ffmpeg(build_extract_audio_cmd(path, audio_path, ffmpeg=ff), duration=meta.duration,
                           on_progress=lambda f: tracker.step("audio", progress=f))
                tracker.step("audio", "done", detail="16 kHz mono WAV")
            except FFmpegError as e:
                audio_path = None
                tracker.step("audio", "error", detail=f"Audio unreadable, continuing without speech: {e}")
    else:
        tracker.step("audio", "skipped", detail="Source has no audio track")
    with session_scope() as s:
        s.get(Project, project_id).source.audio_path = str(audio_path) if audio_path else None  # type: ignore[union-attr]

    # ---- transcription -------------------------------------------------------------------
    tdir = project_dir(project_id, "transcript")
    transcript: dict[str, Any] | None = None
    if audio_path is None:
        transcript = empty_transcript("no audio")
        tracker.step("transcribe", "skipped", detail="No audio: captions and word snapping unavailable")
    else:
        with session_scope() as s:
            rec = (s.query(TranscriptRecord).filter_by(source_sha256=sha, whisper_model=whisper_model)
                   .order_by(TranscriptRecord.created_at.desc()).first())
            rec_path = rec.json_path if rec else None
        if rec_path and Path(rec_path).exists():
            transcript = json.loads(Path(rec_path).read_text(encoding="utf-8"))
            tracker.step("transcribe", "done", detail=f"Reused cached transcript ({whisper_model})")
        else:
            from ..services.transcribe import detect_device

            dev = detect_device()
            tracker.step("transcribe", "running", detail=f"Whisper '{whisper_model}' on {dev['note']} ({dev['compute_type']})")
            transcript = transcribe_audio(audio_path, whisper_model, meta.duration,
                                          on_progress=lambda f: tracker.step("transcribe", progress=f))
            cpath = cache / f"transcript_{whisper_model}.json"
            cpath.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
            with session_scope() as s:
                s.add(TranscriptRecord(source_sha256=sha, whisper_model=whisper_model, language=transcript.get("language"),
                                       json_path=str(cpath), word_count=len(flatten_words(transcript))))
            tracker.step("transcribe", "done", detail=f"{len(flatten_words(transcript))} words, language {transcript.get('language')}")
    write_transcript_files(transcript, tdir)
    words = flatten_words(transcript)

    # ---- campaign rules ------------------------------------------------------------------
    provider = get_provider(project_id, P["use_gemini"])
    rules_text = P["rules_text"].strip()
    rules_hash = sha256_text(rules_text)
    tracker.step("rules", "running")
    if not rules_text:
        rules = CampaignRules()
        rules.unknown_requirements = unknown_fields(rules)
        rules_source = "none"
        tracker.step("rules", "done", detail="No campaign rules supplied: everything is unknown")
    else:
        try:
            rules, cached = cached_call(project_id, provider, "campaign_rules", {"rules": rules_hash},
                                        lambda: provider.parse_campaign_rules(rules_text), CampaignRules)
            rules_source = "heuristic" if provider.is_demo else provider.name
            tracker.step("rules", "done", detail=("Offline rule parser (demo)" if provider.is_demo else
                                                  f"Parsed by {provider.model}" + (" (cached)" if cached else "")))
        except AIProviderError as e:
            rules = parse_rules_heuristic(rules_text)
            rules_source = "heuristic"
            tracker.step("rules", "error", detail=f"Gemini failed ({e}); used offline parser instead")
    with session_scope() as s:
        p = s.get(Project, project_id)
        p.campaign_rules_json = rules.model_dump()  # type: ignore[union-attr]
        p.campaign_rules_hash = rules_hash  # type: ignore[union-attr]
        p.campaign_rules_source = rules_source  # type: ignore[union-attr]

    min_d, max_d, dnotes = resolve_durations(P["mode"], rules, P["min"], P["max"], prefs.default_min_duration,
                                             prefs.default_max_duration, meta.duration)
    target = candidate_target(P["clip_count"], prefs.max_candidates, meta.duration, min_d)

    # ---- upload --------------------------------------------------------------------------
    ctx = AnalysisContext(
        project_name=P["name"], campaign_name=P["campaign"], rules=rules, rules_text=rules_text,
        platforms=P["platforms"], clip_count=P["clip_count"], candidate_target=target, min_duration=min_d,
        max_duration=max_d, source_duration=meta.duration, source_sha256=sha,
        video_path=str(proxy_path or path), transcript=transcript, transcript_prompt=transcript_for_prompt(transcript),
    )
    if provider.is_demo:
        why = "Gemini disabled for this project" if not P["use_gemini"] else "GEMINI_API_KEY not configured"
        tracker.step("upload", "skipped", detail=f"Demo mode ({why})")
        tracker.step("analyze", label="Demo analysis (offline heuristics, no AI)")
    else:
        with session_scope() as s:
            ref = s.get(GeminiFileRef, sha)
            reuse = bool(ref and ref.expires_at and (ref.expires_at if ref.expires_at.tzinfo else ref.expires_at.replace(tzinfo=UTC))
                         - datetime.now(UTC) > timedelta(hours=1))
        tracker.step("upload", "running", detail="Checking cached Gemini file..." if reuse else
                     f"Uploading {Path(ctx.video_path).stat().st_size / 1024**2:.0f} MB (processed by Gemini before analysis)...")
        ctx.video_ref = provider.prepare_video(ctx.video_path, sha, None)
        tracker.step("upload", "done", detail="Reused Gemini file reference" if reuse else "Uploaded and processed")
        tracker.step("analyze", label=f"Analyzing video with Gemini ({provider.model})")

    tracker.step("analyze", "running", detail=f"Looking for ~{target} moments of {min_d:g}-{max_d:g}s")
    key_parts = {"source": sha, "rules": rules_hash, "rules_json": sha256_text(json.dumps(rules.model_dump(), sort_keys=True)),
                 "platforms": sorted(P["platforms"]), "clip_count": P["clip_count"], "target": target,
                 "min": round(min_d, 1), "max": round(max_d, 1), "transcript": sha256_text(ctx.transcript_prompt)}
    result, cached = cached_call(project_id, provider, "candidates", key_parts, lambda: provider.generate_candidates(ctx),
                                 ViralCandidateList)
    tracker.step("analyze", "done", detail=f"Found {len(result.candidates)} candidate moments" + (" (cached response)" if cached else ""))

    # ---- snap / dedupe / rank ------------------------------------------------------------
    tracker.step("rank", "running")
    sponsor_required = any("sponsor" in r.lower() for r in rules.special_requirements)
    items: list[dict[str, Any]] = []
    for c in result.candidates:
        ai_s = max(0.0, min(c.start_time, meta.duration))
        ai_e = max(0.0, min(c.end_time, meta.duration))
        if ai_e - ai_s < 1.0:
            continue
        snap = snap_clip(words, ai_s, ai_e, opening_words=c.exact_opening_words, closing_words=c.exact_closing_words,
                         min_duration=min_d, max_duration=max_d, media_duration=meta.duration)
        text = clip_text(words, snap.start, snap.end)
        opening = clip_text(words, snap.start, snap.start + 3.5)
        local = local_penalties(opening, text, snap.notes, snap.duration, min_d, max_d, sponsor_required)
        items.append({"cand": c, "start": snap.start, "end": snap.end, "snap_notes": snap.notes, "text": text,
                      "local": local, "viral_score": final_score(c, local), "subscores": c.subscores.model_dump()})

    kept, removed = dedupe([DedupeItem(key=str(i), start=it["start"], end=it["end"], score=it["viral_score"], text=it["text"],
                                       hook=it["cand"].suggested_hook_text, topic=it["cand"].topic, summary=it["cand"].summary)
                            for i, it in enumerate(items)])
    kept_items = rank([items[int(k.key)] for k in kept])
    removed_info = [{"candidate_id": items[int(d.removed.key)]["cand"].candidate_id,
                     "kept": items[int(d.kept.key)]["cand"].candidate_id, "reason": d.reason} for d in removed]

    with session_scope() as s:
        s.execute(delete(Candidate).where(Candidate.project_id == project_id))
        selected_left = P["clip_count"]
        for it in kept_items:
            c = it["cand"]
            report = evaluate_compliance(rules, ClipFacts(
                duration=it["end"] - it["start"], platforms=P["platforms"], captions_on=prefs.captions,
                transcript_text=it["text"], hook_text=c.suggested_hook_text, ai_status=c.campaign_compliance.status,
                ai_reasons=c.campaign_compliance.reasons), has_rules_text=bool(rules_text))
            hooks = [{"text": h, "source": "ai"} for h in [c.suggested_hook_text, *c.alternative_hooks] if h][:3]
            select = selected_left > 0 and report.status != "FAILED"
            if select:
                selected_left -= 1
            data = c.model_dump(mode="json")
            data["local_penalties"] = [pn.model_dump() for pn in it["local"]]
            data["ai_viral_score"] = c.viral_score
            s.add(Candidate(
                project_id=project_id, candidate_id=c.candidate_id, rank=it["rank"], ai_start=c.start_time, ai_end=c.end_time,
                start=it["start"], end=it["end"], viral_score=it["viral_score"], data=data, snap_notes=it["snap_notes"],
                transcript_text=it["text"], hooks=hooks, hook_index=0, compliance_status=report.status,
                compliance=report.model_dump(), selected=select,
            ))
        s.add(Analysis(project_id=project_id, provider=provider.name, model=provider.model, cache_key=sha256_text(json.dumps(key_parts, sort_keys=True)),
                       video_analysis={**result.video_analysis.model_dump(mode="json"), "duplicates_removed": removed_info,
                                       "duration_notes": dnotes, "min_duration": min_d, "max_duration": max_d},
                       raw_candidate_count=len(result.candidates), kept_candidate_count=len(kept_items)))
        p = s.get(Project, project_id)
        p.status = "ready"  # type: ignore[union-attr]
        p.analysis_provider = provider.name  # type: ignore[union-attr]

    adir = project_dir(project_id, "analysis")
    (adir / "analysis.json").write_text(json.dumps({"provider": provider.name, "model": provider.model,
                                                    "video_analysis": result.video_analysis.model_dump(mode="json"),
                                                    "duplicates_removed": removed_info}, indent=2), encoding="utf-8")
    (project_dir(project_id, "candidates") / "candidates.json").write_text(
        json.dumps([{**it["cand"].model_dump(mode="json"), "snapped_start": it["start"], "snapped_end": it["end"],
                     "final_score": it["viral_score"], "rank": it["rank"]} for it in kept_items], indent=2), encoding="utf-8")

    detail = f"{len(kept_items)} ranked candidates"
    if removed_info:
        detail += f", {len(removed_info)} duplicate(s) removed"
    tracker.step("rank", "done", detail=detail)

    from .longform import find_long_form

    va = result.video_analysis.model_dump(mode="json")
    try:
        n_long = find_long_form(project_id, tracker, provider, transcript=transcript, source_duration=meta.duration,
                                sha=sha, P=P, prefs=prefs, video_analysis=va, rules=rules)
    except Exception as e:  # noqa: BLE001 - shorts are ready; long-form can be retried on its own
        log.exception("Long-form step failed")
        tracker.step("longform", "error", detail=f"{e} (shorts are ready; retry with 'Find long-form clips')")
        n_long = 0
    if n_long:
        detail += f"; {n_long} long-form segment(s)"
    tracker.step("ready", "done", detail="Review candidates below")
    tracker.set_message(f"Ready for review: {len(kept_items)} candidates" + (" [DEMO MODE]" if provider.is_demo else ""))
