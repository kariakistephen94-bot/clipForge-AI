"""Long-form pipeline: find & package segments (analysis) and render/export them (render job).

Finding runs as a step at the end of the normal analysis, and also as its own job so projects analysed
before long-form existed can get long-form clips without re-running Whisper or the video request.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete

from ..ai.demo import DemoProvider
from ..ai.provider import AIProvider, AIProviderError, LongFormContext
from ..ai.service import cached_call, get_provider
from ..candidates.longform import (
    clean_chapters,
    dedupe_long,
    long_form_target,
    select_count,
    snap_cold_open,
    snap_long,
)
from ..candidates.scoring import local_penalties
from ..db import session_scope
from ..models import Analysis, LongFormClip, Project
from ..paths import project_dir
from ..schemas.ai import CampaignRules, LongFormList
from ..services.compliance import ClipFacts, evaluate_compliance
from ..services.export import build_ready_long, export_long_clip
from ..services.longform_render import LongRenderInput, LongRenderOptions, chapters_text, render_long_clip
from ..services.preferences import Preferences, get_preferences
from ..services.thumbnail_prompts import build_prompts
from ..services.transcribe import flatten_words, transcript_for_prompt
from ..utils.hashing import sha256_text
from .progress import JobTracker

log = logging.getLogger(__name__)


def speakers_text(video_analysis: dict[str, Any]) -> str:
    sp = video_analysis.get("speakers") or []
    return "; ".join(filter(None, (s.get("description") or s.get("label") for s in sp[:3])))


def thumbnail_package(cand_data: dict[str, Any], title: str | None = None) -> list[dict[str, Any]]:
    """Assemble prompts from the stored concepts (built on read, so prompt improvements reach old projects)."""
    from ..schemas.ai import ThumbnailConcept

    out = []
    for n, c in enumerate(cand_data.get("thumbnail_concepts") or []):
        tag = chr(ord("A") + n)
        try:
            concept = ThumbnailConcept.model_validate(c)
        except Exception:  # noqa: BLE001
            continue
        item = build_prompts(concept, aspect="16:9", title=title if title is not None else cand_data.get("title", ""),
                             reference_file=f"thumbnail_frame_{tag}.jpg", speakers=cand_data.get("speakers", ""))
        item["reference_file"] = f"thumbnail_frame_{tag}.jpg"
        out.append(item)
    return out


# --------------------------------------------------------------------------- finding


def find_long_form(project_id: str, tracker: JobTracker, provider: AIProvider, *, transcript: dict[str, Any],
                   source_duration: float, sha: str, P: dict[str, Any], prefs: Preferences,
                   video_analysis: dict[str, Any], rules: CampaignRules | None) -> int:
    """Returns the number of long-form clips kept. Never raises for AI problems (shorts are already done)."""
    tracker.step("longform", "running")
    lo, hi = prefs.long_form_min_duration, prefs.long_form_max_duration
    target = long_form_target(source_duration, lo, hi, prefs.long_form_max_clips, P.get("long_form_mode", "auto"),
                              int(P.get("long_form_count") or 0))
    if target.count == 0:
        with session_scope() as s:
            s.execute(delete(LongFormClip).where(LongFormClip.project_id == project_id))
        tracker.step("longform", "skipped", detail=target.note)
        return 0
    words = flatten_words(transcript)
    ctx = LongFormContext(project_name=P["name"], campaign_name=P.get("campaign", ""), source_duration=source_duration,
                          source_sha256=sha, target_count=target.count, min_duration=lo, max_duration=hi,
                          video_analysis=video_analysis, transcript=transcript,
                          transcript_prompt=transcript_for_prompt(transcript))
    tracker.step("longform", detail=f"Looking for ~{target.count} segment(s) of {lo / 60:.0f}-{hi / 60:.0f} min "
                                    f"({'offline heuristics' if provider.is_demo else provider.model})")
    key = {"source": sha, "target": target.count, "min": round(lo), "max": round(hi),
           "analysis": sha256_text(json.dumps(video_analysis, sort_keys=True, default=str)),
           "transcript": sha256_text(ctx.transcript_prompt)}
    try:
        result, cached = cached_call(project_id, provider, "long_form", key, lambda: provider.generate_long_form(ctx),
                                     LongFormList)
        source_note = "cached" if cached else provider.name
    except AIProviderError as e:
        log.warning("Long-form AI failed: %s", e)
        result, source_note = DemoProvider().generate_long_form(ctx), f"offline heuristics (AI failed: {e})"

    sponsor_required = bool(rules and any("sponsor" in r.lower() for r in rules.special_requirements))
    items = []
    for c in result.items:
        snap = snap_long(words, c, lo, hi, source_duration)
        if snap["end"] - snap["start"] < min(lo * 0.8, 60):
            continue
        opening = " ".join(snap["text"].split()[:40])
        local = local_penalties(opening, snap["text"], snap["notes"], snap["end"] - snap["start"], lo, hi,
                                sponsor_required)
        ai_total = c.computed_score() if c.subscores.total() > 0 else c.score
        score = round(max(0.0, min(100.0, ai_total - sum(p.points for p in local))), 1)
        items.append({"cand": c, "start": snap["start"], "end": snap["end"], "notes": snap["notes"], "text": snap["text"],
                      "local": local, "score": score})
    kept, removed = dedupe_long(items)
    n_select = select_count(kept, target.count)
    with session_scope() as s:
        s.execute(delete(LongFormClip).where(LongFormClip.project_id == project_id))
        for it in kept:
            c = it["cand"]
            data = c.model_dump(mode="json")
            data["chapters_clean"] = clean_chapters(c.chapters, it["start"], it["end"])
            data["cold_open_snapped"] = snap_cold_open(words, c, it["start"], it["end"], source_duration)
            data["local_penalties"] = [p.model_dump() for p in it["local"]]
            data["ai_score"] = c.score
            data["speakers"] = speakers_text(video_analysis)
            data["demo"] = provider.is_demo or source_note.startswith("offline")
            s.add(LongFormClip(project_id=project_id, candidate_id=c.candidate_id, rank=it["rank"], ai_start=c.start_time,
                               ai_end=c.end_time, start=round(it["start"], 3), end=round(it["end"], 3), score=it["score"],
                               data=data, snap_notes=it["notes"], transcript_text=it["text"],
                               selected=it["rank"] <= n_select))
        an = s.query(Analysis).filter(Analysis.project_id == project_id).order_by(Analysis.created_at.desc()).first()
        if an is not None:
            an.video_analysis = {**(an.video_analysis or {}), "long_form": {
                "target": target.count, "ideal_seconds": round(target.ideal), "note": target.note,
                "kept": len(kept), "selected": n_select, "duplicates_removed": removed, "ai_notes": result.notes,
                "source": source_note}}
    detail = f"{len(kept)} segment(s), {n_select} recommended ({target.note})"
    tracker.step("longform", "done", detail=detail + ("" if source_note in (provider.name, "cached") else f" - {source_note}"))
    return len(kept)


def run_long_form_analysis(project_id: str, tracker: JobTracker, opts: dict[str, Any]) -> None:
    """Standalone job: find long-form segments for an already analysed project."""
    tracker.add_step("longform", "Finding long-form segments")
    prefs = get_preferences()
    with session_scope() as s:
        p = s.get(Project, project_id)
        if p is None or p.source is None or not p.source.probe:
            raise ValueError("Analyze the source first.")
        P: dict[str, Any] = {"name": p.name, "campaign": p.campaign_name, "long_form_mode": p.long_form_mode,
             "long_form_count": p.long_form_count, "use_gemini": p.use_gemini}
        duration = float(p.source.probe.get("duration") or 0)
        sha = p.source.sha256
        rules = CampaignRules.model_validate(p.campaign_rules_json) if p.campaign_rules_json else None
        an = s.query(Analysis).filter(Analysis.project_id == project_id).order_by(Analysis.created_at.desc()).first()
        va = {k: v for k, v in ((an.video_analysis or {}) if an else {}).items()
              if k in ("summary", "content_type", "speakers", "main_topics", "tone", "visual_style", "sections")}
    tpath = project_dir(project_id, "transcript") / "transcript.json"
    if not tpath.exists():
        raise ValueError("No transcript yet. Run the analysis first.")
    transcript = json.loads(tpath.read_text(encoding="utf-8"))
    provider = get_provider(project_id, P["use_gemini"])
    n = find_long_form(project_id, tracker, provider, transcript=transcript, source_duration=duration, sha=sha, P=P,
                       prefs=prefs, video_analysis=va, rules=rules)
    tracker.finish("done", message=f"{n} long-form segment(s) ready for review")


# --------------------------------------------------------------------------- rendering


def run_long_render(project_id: str, tracker: JobTracker, ids: list[str] | None, overrides: dict[str, Any]) -> None:
    prefs = get_preferences().model_dump()
    opts = LongRenderOptions.from_prefs({**prefs, **(overrides or {})})
    with session_scope() as s:
        p = s.get(Project, project_id)
        if p is None or p.source is None or not p.source.probe:
            raise ValueError("Project or source not found.")
        q = s.query(LongFormClip).filter(LongFormClip.project_id == project_id)
        q = q.filter(LongFormClip.id.in_(ids)) if ids else q.filter(LongFormClip.selected.is_(True),
                                                                   LongFormClip.rejected.is_(False))
        rows = q.order_by(LongFormClip.rank).all()
        if not rows:
            raise ValueError("No long-form clips selected.")
        C: list[dict[str, Any]] = [{"pk": r.id, "rank": r.rank, "start": r.start, "end": r.end, "data": r.data, "score": r.score,
              "title_index": r.title_index, "cid": r.candidate_id} for r in rows]
        rules = CampaignRules.model_validate(p.campaign_rules_json) if p.campaign_rules_json else None
        S: dict[str, Any] = {"path": p.source.stored_path, "probe": p.source.probe, "name": p.source.original_filename}
        P: dict[str, Any] = {"name": p.name, "campaign": p.campaign_name, "rules_text": p.campaign_rules_text or ""}
        p.status = "rendering"
    if not Path(S["path"]).exists():
        raise ValueError("Source video file is missing.")
    for n, c in enumerate(C, 1):
        tracker.add_step(f"long_{c['pk']}", f"Rendering long-form {n}/{len(C)}")
    tracker.add_step("long_export", "Exporting long-form & READY_TO_POST/LONG_FORM")
    transcript = json.loads((project_dir(project_id, "transcript") / "transcript.json").read_text(encoding="utf-8"))
    words = flatten_words(transcript)
    probe = S["probe"]
    ok = 0
    for n, c in enumerate(C, 1):
        key = f"long_{c['pk']}"
        d = c["data"]
        titles = [t for t in [d.get("title", ""), *d.get("alternative_titles", [])] if t]
        title = titles[c["title_index"]] if 0 <= c["title_index"] < len(titles) else (titles[0] if titles else d.get("topic", ""))
        tracker.step(key, "running", label=f"Rendering long-form {n}/{len(C)}: {title[:50]}")
        tracker.set_message(f"Rendering long-form {n}/{len(C)}...")
        inp = LongRenderInput(
            source_path=S["path"], src_width=int(probe["display_width"]), src_height=int(probe["display_height"]),
            src_fps=float(probe.get("fps") or 30), has_audio=bool(probe.get("has_audio")), start=c["start"], end=c["end"],
            words=words, chapters=d.get("chapters_clean") or [], cold_open=d.get("cold_open_snapped"),
            thumbnails=d.get("thumbnail_concepts") or [], rules=rules,
            workdir=project_dir(project_id, "renders") / f"long_{c['cid']}", seed=f"{project_id}:{c['cid']}")
        try:
            def on_progress(f: float, m: str, key: str = key) -> None:
                tracker.step(key, progress=f, detail=m)

            res = render_long_clip(inp, opts, progress=on_progress)
        except Exception as e:  # noqa: BLE001 - one failed clip must not stop the batch
            log.exception("Long-form render failed for %s", c["cid"])
            tracker.step(key, "error", detail=str(e)[:400])
            continue
        report = evaluate_compliance(rules, ClipFacts(duration=res.duration, platforms=[], captions_on=res.captions_on,
                                                      sfx_used=bool(res.sound_events), transcript_text=res.clip_text,
                                                      hook_text=title), has_rules_text=bool(P["rules_text"].strip()))
        # campaign duration / platform rules are written for shorts; long-form is checked for content only
        report.checks = [ch for ch in report.checks if ch.rule not in ("Duration", "Platforms")]
        report.status = ("FAILED" if any(ch.status == "FAILED" for ch in report.checks) else
                         "WARNING" if any(ch.status == "WARNING" for ch in report.checks) else "COMPLIANT")
        prompts = thumbnail_package(d, title)
        folder, files, meta = export_long_clip(
            project_id=project_id, index=c["rank"], result=res, title=title, titles=titles, data=d,
            prompts=prompts, compliance=report.model_dump(), source_file=S["name"] or Path(S["path"]).name,
            start=c["start"], end=c["end"], score=c["score"], campaign=P["campaign"],
            extra={"sound_design": {"style": opts.sound_design, "events": res.sound_events},
                   "color_grade": {"preset": opts.color_grade, "overrides": opts.grade_overrides}})
        with session_scope() as s:
            row = s.get(LongFormClip, c["pk"])
            if row:
                row.export = {"folder": str(folder), "files": files, "created_at": datetime.now(UTC).isoformat(),
                              "duration": res.duration, "notes": res.notes, "chapters": res.chapters,
                              "chapters_text": chapters_text(res.chapters) if res.youtube_chapters_ok else "",
                              "compliance_status": report.status, "title": title, "metadata": meta}
        ok += 1
        tracker.step(key, "done", detail=f"{res.duration / 60:.1f} min, {len(res.chapters)} chapters, "
                                         f"{len(res.thumbnail_drafts)} thumbnail draft(s)"
                                         + (f" - {'; '.join(res.notes)}" if res.notes else ""))
    if ok == 0:
        raise RuntimeError("All long-form renders failed. See the step details and logs/app.log.")
    tracker.step("long_export", "running")
    with session_scope() as s:
        entries = [{"folder": r.export["folder"], "title": r.export.get("title", ""), "score": r.score,
                    "duration": r.export.get("duration", 0), "status": r.export.get("compliance_status", "UNKNOWN"),
                    "rank": r.rank}
                   for r in s.query(LongFormClip).filter(LongFormClip.project_id == project_id).all()
                   if r.export and Path(r.export.get("folder", "")).exists()]
        root, ready = build_ready_long(project_id, entries)
        p = s.get(Project, project_id)
        if p:
            p.status = "rendered"
    tracker.step("long_export", "done", detail=f"{ready} long-form clip(s) in {root}")
    tracker.finish("done", message=f"Rendered {ok}/{len(C)} long-form clip(s). Ready in {root}")
