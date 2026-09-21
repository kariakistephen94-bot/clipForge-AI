"""DB row -> JSON for the dashboard."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Analysis, Candidate, ExportRecord, LongFormClip, Project
from ..paths import project_dir
from ..pipeline import youtube_retry
from ..pipeline.progress import current_snapshot
from ..schemas.ai import LONG_SUBSCORE_MAX, SUBSCORE_MAX


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def file_url(project_id: str, rel: str) -> str:
    return f"/api/projects/{project_id}/files/{rel}"


def project_summary(p: Project, s: Session) -> dict[str, Any]:
    n_cand = s.query(func.count(Candidate.id)).filter(Candidate.project_id == p.id).scalar() or 0
    n_exp = s.query(func.count(ExportRecord.id)).filter(ExportRecord.project_id == p.id).scalar() or 0
    n_long = s.query(func.count(LongFormClip.id)).filter(LongFormClip.project_id == p.id).scalar() or 0
    probe = (p.source.probe if p.source else None) or {}
    return {
        "id": p.id, "name": p.name, "campaign_name": p.campaign_name, "status": p.status, "error": p.error,
        "created_at": iso(p.created_at), "updated_at": iso(p.updated_at), "candidates": n_cand, "exports": n_exp, "long_form": n_long,
        "source_name": p.source.original_filename if p.source else "", "duration": probe.get("duration"),
        "provider": p.analysis_provider, "platforms": p.platforms,
        "youtube_retry": youtube_retry.waiting_info(p.id) if p.status == "waiting" else None,
    }


def project_detail(p: Project, s: Session) -> dict[str, Any]:
    src = p.source
    analysis = s.query(Analysis).filter(Analysis.project_id == p.id).order_by(Analysis.created_at.desc()).first()
    ready_dir = project_dir(p.id, "READY_TO_POST")
    return {
        **project_summary(p, s),
        "campaign_rules_text": p.campaign_rules_text,
        "campaign_rules": p.campaign_rules_json,
        "campaign_rules_source": p.campaign_rules_source,
        "desired_clip_count": p.desired_clip_count,
        "duration_mode": p.duration_mode,
        "min_duration": p.min_duration,
        "max_duration": p.max_duration,
        "use_gemini": p.use_gemini,
        "long_form_mode": p.long_form_mode,
        "long_form_count": p.long_form_count,
        "usage": {
            "gemini_requests": p.gemini_requests, "video_uploads": p.gemini_uploads, "cached_requests": p.cached_requests,
            "input_tokens": p.input_tokens, "output_tokens": p.output_tokens,
        },
        "source": None if src is None else {
            "origin": src.origin, "url": src.source_url, "filename": src.original_filename, "size_bytes": src.size_bytes,
            "sha256": src.sha256, "probe": src.probe, "has_proxy": bool(src.proxy_path),
            "preview_url": f"/api/projects/{p.id}/media/source" if src.stored_path else None,
        },
        "analysis": None if analysis is None else {
            "provider": analysis.provider, "model": analysis.model, "created_at": iso(analysis.created_at),
            "raw_candidates": analysis.raw_candidate_count, "kept_candidates": analysis.kept_candidate_count,
            **(analysis.video_analysis or {}),
        },
        "job": current_snapshot(p.id),
        "paths": {"exports": str(project_dir(p.id, "exports")), "ready_to_post": str(ready_dir)},
        "gemini": {"configured": get_settings().gemini_configured, "model": get_settings().model_name},
    }


def candidate_dict(c: Candidate) -> dict[str, Any]:
    d = c.data or {}
    subs = d.get("subscores") or {}
    penalties = [{**pn, "source": "ai"} for pn in d.get("penalties", [])] + \
                [{**pn, "source": "local"} for pn in d.get("local_penalties", [])]
    exports = []
    for e in c.exports:
        files = e.files or {}
        folder = files.get("folder", Path(e.folder).name)
        exports.append({
            "id": e.id, "folder": e.folder, "compliance_status": e.compliance_status, "created_at": iso(e.created_at),
            "videos": {v: file_url(c.project_id, f"exports/{folder}/{fn}") for v, fn in files.items() if v in ("A", "B", "C")},
            "thumbnail": file_url(c.project_id, f"exports/{folder}/thumbnail.jpg") if files.get("thumbnail") else None,
            "notes": (e.metadata_json or {}).get("notes", []),
            "sound_events": ((e.metadata_json or {}).get("sound_design") or {}).get("events", []),
            "thumbnail_prompt": file_url(c.project_id, f"exports/{folder}/thumbnail_prompt.txt")
            if files.get("thumbnail_prompt") else None,
        })
    return {
        "id": c.id, "candidate_id": c.candidate_id, "rank": c.rank, "start": c.start, "end": c.end,
        "duration": round(c.end - c.start, 2), "ai_start": c.ai_start, "ai_end": c.ai_end, "viral_score": c.viral_score,
        "ai_viral_score": d.get("ai_viral_score"),
        "subscores": [{"key": k, "value": float(subs.get(k, 0)), "max": m} for k, m in SUBSCORE_MAX.items()],
        "penalties": penalties, "topic": d.get("topic", ""), "summary": d.get("summary", ""),
        "reason_it_works": d.get("reason_it_works", ""), "target_audience": d.get("target_audience", ""),
        "hook_type": d.get("hook_type", ""), "story_structure": d.get("story_structure", {}),
        "editing_strategy": d.get("editing_strategy", ""), "caption_emphasis_words": d.get("caption_emphasis_words", []),
        "suggested_zoom_points": d.get("suggested_zoom_points", []), "suggested_broll_points": d.get("suggested_broll_points", []),
        "platform_fit": d.get("platform_fit", {}), "exact_opening_words": d.get("exact_opening_words", ""),
        "exact_closing_words": d.get("exact_closing_words", ""), "hooks": c.hooks or [], "hook_index": c.hook_index,
        "posting_copy": c.posting_copy, "compliance_status": c.compliance_status, "compliance": c.compliance,
        "selected": c.selected, "rejected": c.rejected, "user_edited": c.user_edited, "snap_notes": c.snap_notes or [],
        "transcript_text": c.transcript_text, "refined": bool(d.get("refined")), "boundary_feedback": d.get("boundary_feedback"),
        "exports": exports,
    }


def long_form_dict(c: LongFormClip) -> dict[str, Any]:
    from ..pipeline.longform import thumbnail_package

    d = c.data or {}
    subs = d.get("subscores") or {}
    penalties = [{**pn, "source": "ai"} for pn in d.get("penalties", [])] + \
                [{**pn, "source": "local"} for pn in d.get("local_penalties", [])]
    titles = [t for t in [d.get("title", ""), *d.get("alternative_titles", [])] if t]
    export = None
    if c.export:
        e = c.export
        files = e.get("files") or {}
        folder = files.get("folder", Path(e.get("folder", "")).name)

        def url(name: str) -> str:
            return file_url(c.project_id, f"exports/{folder}/{name}")

        export = {
            "folder": e.get("folder"), "created_at": e.get("created_at"), "duration": e.get("duration"),
            "video": url(files["video"]) if files.get("video") else None,
            "thumbnail_drafts": [url(f) for f in files.get("thumbnail_drafts", [])],
            "thumbnail_frames": [url(f) for f in files.get("thumbnail_frames", [])],
            "chapters": e.get("chapters", []), "chapters_text": e.get("chapters_text", ""), "notes": e.get("notes", []),
            "compliance_status": e.get("compliance_status", "UNKNOWN"), "title": e.get("title", ""),
            "sound_events": ((e.get("metadata") or {}).get("sound_design") or {}).get("events", []),
        }
    return {
        "id": c.id, "candidate_id": c.candidate_id, "rank": c.rank, "start": c.start, "end": c.end,
        "duration": round(c.end - c.start, 2), "ai_start": c.ai_start, "ai_end": c.ai_end, "score": c.score,
        "ai_score": d.get("ai_score"),
        "subscores": [{"key": k, "value": float(subs.get(k, 0)), "max": m} for k, m in LONG_SUBSCORE_MAX.items()],
        "penalties": penalties, "titles": titles, "title_index": c.title_index, "topic": d.get("topic", ""),
        "summary": d.get("summary", ""), "reason_it_works": d.get("reason_it_works", ""),
        "target_audience": d.get("target_audience", ""), "description": d.get("description", ""),
        "tags": d.get("tags", []), "chapters": d.get("chapters_clean", []), "cold_open": d.get("cold_open_snapped"),
        "thumbnail_prompts": thumbnail_package(d, titles[c.title_index] if 0 <= c.title_index < len(titles) else None),
        "demo": bool(d.get("demo")),
        "selected": c.selected, "rejected": c.rejected, "snap_notes": c.snap_notes or [],
        "transcript_preview": (c.transcript_text or "")[:1200], "export": export,
    }
