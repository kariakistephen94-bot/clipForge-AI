"""Batch render: hooks & posting copy -> render clips -> compliance -> exports -> READY_TO_POST."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete

from ..ai.demo import DemoProvider
from ..ai.provider import AIProviderError
from ..ai.service import cached_call, get_provider
from ..candidates.scoring import local_penalties
from ..db import session_scope
from ..models import Analysis, Candidate, ExportRecord, Project, RenderJob
from ..paths import project_dir
from ..schemas.ai import AIComplianceList, CampaignRules, HookVariantList, ViralCandidate
from ..services.compliance import ClipFacts, evaluate_compliance
from ..services.export import build_metadata, build_ready_to_post, export_clip
from ..services.posting import assemble_posting_copy, posting_copy_text
from ..services.preferences import get_preferences
from ..services.render import RenderInput, RenderOptions, RenderResult, render_clip
from ..services.thumbnail_prompts import build_prompts, prompts_text
from ..services.transcribe import flatten_words
from ..services.zoom import normalize_kind
from ..utils.hashing import sha256_text
from .progress import JobTracker

log = logging.getLogger(__name__)


def _load_transcript(project_id: str) -> dict[str, Any]:
    p = project_dir(project_id, "transcript") / "transcript.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"segments": []}


def run_render(project_id: str, tracker: JobTracker, candidate_pks: list[str] | None, overrides: dict[str, Any]) -> None:
    prefs = get_preferences()
    opts = RenderOptions.from_dict({**prefs.model_dump(), **(overrides or {})})

    with session_scope() as s:
        p = s.get(Project, project_id)
        if p is None or p.source is None:
            raise ValueError("Project or source not found.")
        q = s.query(Candidate).filter(Candidate.project_id == project_id)
        if candidate_pks:
            q = q.filter(Candidate.id.in_(candidate_pks))
        else:
            q = q.filter(Candidate.selected.is_(True), Candidate.rejected.is_(False))
        cands = q.order_by(Candidate.rank).all()
        if not cands:
            raise ValueError("No clips selected. Tick 'Include in batch render' on at least one candidate.")
        P: dict[str, Any] = {"name": p.name, "campaign": p.campaign_name, "platforms": list(p.platforms or []), "use_gemini": p.use_gemini,
             "rules_text": p.campaign_rules_text or "", "rules_hash": p.campaign_rules_hash}
        rules = CampaignRules.model_validate(p.campaign_rules_json) if p.campaign_rules_json else None
        src = p.source
        S: dict[str, Any] = {"path": src.stored_path, "probe": src.probe or {}, "sha": src.sha256, "name": src.original_filename}
        an = s.query(Analysis).filter(Analysis.project_id == project_id).order_by(Analysis.created_at.desc()).first()
        tone = str(((an.video_analysis or {}) if an else {}).get("tone", "")).lower()
        P["speakers"] = "; ".join(filter(None, (sp.get("description") or sp.get("label")
                                                for sp in (((an.video_analysis or {}) if an else {}).get("speakers") or [])[:2])))
        C: list[dict[str, Any]] = [{"pk": c.id, "candidate_id": c.candidate_id, "rank": c.rank, "start": c.start, "end": c.end, "data": c.data,
              "hooks": list(c.hooks or []), "hook_index": c.hook_index, "text": c.transcript_text, "score": c.viral_score}
             for c in cands]
        p.status = "rendering"
        for c in C:
            s.add(RenderJob(project_id=project_id, candidate_pk=c["pk"], job_id=tracker.job_id, status="queued",
                            options=opts.__dict__))

    if not S["probe"]:
        raise ValueError("Source metadata missing. Run analysis first.")
    if not Path(S["path"]).exists():
        raise ValueError("Source video file is missing.")

    tracker.add_step("copy", "Hooks & posting copy")
    for n, c in enumerate(C, 1):
        tracker.add_step(f"clip_{c['pk']}", f"Rendering clip {n}/{len(C)}")
    tracker.add_step("compliance", "Campaign compliance check")
    tracker.add_step("export", "Exporting & building READY_TO_POST")

    provider = get_provider(project_id, P["use_gemini"])
    rules_eff = rules or CampaignRules()

    # ---- hooks & posting copy ------------------------------------------------------------------
    tracker.step("copy", "running")
    clips_for_ai = [{"candidate_id": c["candidate_id"], "topic": c["data"].get("topic", ""), "summary": c["data"].get("summary", ""),
                     "transcript": c["text"][:4000], "suggested_hook_text": c["data"].get("suggested_hook_text", ""),
                     "alternative_hooks": c["data"].get("alternative_hooks", [])} for c in C]
    copy_note = ""
    try:
        hv_list, cached = cached_call(project_id, provider, "hooks",
                                      {"source": S["sha"], "rules": P["rules_hash"], "clips": sha256_text(json.dumps(clips_for_ai, sort_keys=True))},
                                      lambda: provider.generate_hooks(clips_for_ai, rules_eff, P["campaign"], P["platforms"]),
                                      HookVariantList)
        copy_note = "cached" if cached else provider.name
    except AIProviderError as e:
        hv_list = DemoProvider().generate_hooks(clips_for_ai, rules_eff, P["campaign"], P["platforms"])
        copy_note = f"template copy (AI unavailable: {e})"
    hv_by_id = {h.candidate_id: h for h in hv_list.items}
    copies: dict[str, dict[str, Any]] = {}
    speakers = P.get("speakers", "")
    with session_scope() as s:
        for c in C:
            hv = hv_by_id.get(c["candidate_id"])
            hooks = list(c["hooks"])
            existing = {h["text"].strip().lower() for h in hooks}
            for opt in (sorted(hv.hooks, key=lambda h: -h.rating) if hv else []):
                if len(hooks) >= 3:
                    break
                if opt.text.strip().lower() not in existing:
                    hooks.append({"text": opt.text, "source": "ai"})
            c["hooks"] = hooks
            hook = hooks[c["hook_index"]]["text"] if 0 <= c["hook_index"] < len(hooks) else (hooks[0]["text"] if hooks else "")
            copy = assemble_posting_copy(hv, rules_eff, P["platforms"], fallback_title=hook or c["data"].get("topic", ""),
                                         fallback_summary=c["data"].get("summary", ""))
            if hv is not None:
                copy["cover_text"] = hv.cover_text
                if hv.thumbnail is not None:
                    copy["thumbnail_prompts"] = [{**build_prompts(hv.thumbnail, aspect="9:16", title=copy["youtube_title"],
                                                                  reference_file="thumbnail.jpg", speakers=speakers),
                                                  "reference_file": "thumbnail.jpg"}]
            copies[c["pk"]] = copy
            row = s.get(Candidate, c["pk"])
            if row:
                row.hooks, row.posting_copy = hooks, copy
    tracker.step("copy", "done", detail=f"Generated for {len(C)} clip(s) ({copy_note})")

    # ---- render ---------------------------------------------------------------------------------
    transcript = _load_transcript(project_id)
    words = flatten_words(transcript)
    probe = S["probe"]
    results: dict[str, RenderResult] = {}
    for n, c in enumerate(C, 1):
        key = f"clip_{c['pk']}"
        topic = c["data"].get("topic", "")[:40]
        tracker.step(key, "running", label=f"Rendering clip {n}/{len(C)}: {topic}")
        tracker.set_message(f"Rendering clip {n}/{len(C)}...")
        _set_render_job(c["pk"], tracker.job_id, "running")
        cand = ViralCandidate.model_validate(c["data"])
        idx = c["hook_index"] if 0 <= c["hook_index"] < len(c["hooks"]) else 0
        ordered = [c["hooks"][idx]["text"]] + [h["text"] for i, h in enumerate(c["hooks"]) if i != idx] if c["hooks"] else []
        inp = RenderInput(
            source_path=S["path"], src_width=int(probe["display_width"]), src_height=int(probe["display_height"]),
            has_audio=bool(probe.get("has_audio")), start=c["start"], end=c["end"], words=words,
            emphasis=cand.caption_emphasis_words,
            zoom_points=[(z.timestamp, normalize_kind(z.reason, z.kind)) for z in cand.suggested_zoom_points],
            broll_points=[b.model_dump() for b in cand.suggested_broll_points], hooks=ordered, rules=rules,
            workdir=project_dir(project_id, "renders") / c["candidate_id"],
            sound_cues=[sc.model_dump() for sc in cand.sound_cues],
            humorous=is_humorous(cand.hook_type, tone), seed=f"{project_id}:{c['candidate_id']}",
        )
        try:
            def on_progress(f: float, m: str, key: str = key) -> None:
                tracker.step(key, progress=f, detail=m)

            res = render_clip(inp, opts, progress=on_progress)
            results[c["pk"]] = res
            extra = f" - {'; '.join(res.notes)}" if res.notes else ""
            tracker.step(key, "done", detail=f"{res.duration:.1f}s, variants {', '.join(sorted(res.variants))}{extra}")
            _set_render_job(c["pk"], tracker.job_id, "done")
        except Exception as e:  # noqa: BLE001 - one failed clip must not stop the batch
            log.exception("Render failed for %s", c["candidate_id"])
            tracker.step(key, "error", detail=str(e)[:400])
            _set_render_job(c["pk"], tracker.job_id, "error", str(e))

    if not results:
        raise RuntimeError("All clip renders failed. See the step details and logs/app.log.")

    # ---- compliance -----------------------------------------------------------------------------
    tracker.step("compliance", "running")
    by_pk = {c["pk"]: c for c in C}
    ai_items: dict[str, Any] = {}
    comp_note = ""
    if P["rules_text"].strip():
        clips_c = [{"candidate_id": by_pk[pk]["candidate_id"], "duration": round(r.duration, 1),
                    "hook_overlay": (by_pk[pk]["hooks"][0]["text"] if by_pk[pk]["hooks"] else ""),
                    "transcript": r.clip_text[:4000], "posting_copy": next(iter(copies[pk]["full"].values()), "")}
                   for pk, r in results.items()]
        try:
            ai_list, cached = cached_call(project_id, provider, "compliance",
                                          {"rules": P["rules_hash"], "clips": sha256_text(json.dumps(clips_c, sort_keys=True))},
                                          lambda: provider.check_compliance(clips_c, rules_eff, P["rules_text"]), AIComplianceList)
            ai_items = {i.candidate_id: i for i in ai_list.items}
            comp_note = " (AI review cached)" if cached else ("" if provider.is_demo else " (AI reviewed)")
        except AIProviderError as e:
            comp_note = f" (AI review unavailable: {e})"
    reports: dict[str, dict[str, Any]] = {}
    counts = {"COMPLIANT": 0, "WARNING": 0, "FAILED": 0}
    for pk, r in results.items():
        c = by_pk[pk]
        ai = ai_items.get(c["candidate_id"])
        report = evaluate_compliance(rules, ClipFacts(
            duration=r.duration, platforms=P["platforms"], captions_on=r.captions_on, split_used=r.split_used,
            broll_used=bool(r.broll_used), music_used=r.music_used, sfx_used=bool(r.sound_events), posting_text="\n".join(copies[pk]["full"].values()),
            transcript_text=r.clip_text, hook_text=" | ".join(h["text"] for h in c["hooks"]),
            ai_status=ai.status if ai else None, ai_reasons=ai.reasons if ai else []), has_rules_text=bool(P["rules_text"].strip()))
        reports[pk] = report.model_dump()
        counts[report.status] = counts.get(report.status, 0) + 1
    tracker.step("compliance", "done", detail=", ".join(f"{v} {k}" for k, v in counts.items() if v) + comp_note)

    # ---- export ---------------------------------------------------------------------------------
    tracker.step("export", "running")
    with session_scope() as s:
        for pk, r in results.items():
            c = by_pk[pk]
            row = s.get(Candidate, pk)
            hooks = c["hooks"]
            idx = c["hook_index"] if 0 <= c["hook_index"] < len(hooks) else 0
            hook = hooks[idx]["text"] if hooks else ""
            alts = [h["text"] for i, h in enumerate(hooks) if i != idx]
            copy = copies[pk]
            status = reports[pk]["status"]
            metadata = build_metadata(
                candidate_id=c["candidate_id"], source_file=S["name"] or Path(S["path"]).name, start=c["start"], end=c["end"],
                duration=r.duration, viral_score=c["score"], topic=c["data"].get("topic", ""), hook=hook,
                alternative_hooks=alts, campaign=P["campaign"], platforms=P["platforms"], campaign_compliance=status,
                hashtags=copy["hashtags"],
                extra={"project": P["name"], "ai_provider": provider.name, "ai_model": provider.model,
                       "score_label": "AI Viral Potential Score (heuristic estimate, not a guarantee)",
                       "variants": sorted(r.variants), "keep_segments": r.keep_segments, "removed_silence": r.removed_silence,
                       "framing": r.framing, "zoom_events": r.zoom_events, "broll_used": r.broll_used,
                       "sound_design": {"style": opts.sound_design, "events": r.sound_events},
                       "captions_burned_in": r.captions_on, "notes": r.notes, "compliance_report": reports[pk]},
            )
            txt = posting_copy_text(copy, hook, alts, P["campaign"])
            folder, files = export_clip(project_id=project_id, index=c["rank"], variants=r.variants, thumbnail=r.thumbnail,
                                        clip_text=r.clip_text, srt=r.srt, metadata=metadata, posting_copy_txt=txt,
                                        compliance=reports[pk])
            if copy.get("thumbnail_prompts"):
                (folder / "thumbnail_prompt.txt").write_text(prompts_text(copy["youtube_title"], copy["thumbnail_prompts"]),
                                                             encoding="utf-8")
                files["thumbnail_prompt"] = "thumbnail_prompt.txt"
            s.execute(delete(ExportRecord).where(ExportRecord.candidate_pk == pk))
            s.add(ExportRecord(project_id=project_id, candidate_pk=pk, folder=str(folder), files=files, metadata_json=metadata,
                               compliance_status=status))
            if row:
                row.compliance_status, row.compliance = status, reports[pk]
                # keep local penalty info fresh for the rendered duration
                data = dict(row.data)
                data["render_notes"] = r.notes
                data["local_penalties_render"] = [pn.model_dump() for pn in local_penalties(
                    r.clip_text[:200], r.clip_text, [], r.duration, 0, 10**6)]
                row.data = data

    with session_scope() as s:
        entries = []
        for rec in s.query(ExportRecord).filter(ExportRecord.project_id == project_id).all():
            crow = s.get(Candidate, rec.candidate_pk)
            if crow is None or not Path(rec.folder).exists():
                continue
            meta = rec.metadata_json or {}
            entries.append({"export_folder": rec.folder, "topic": meta.get("topic", ""), "hook": meta.get("hook", ""),
                            "viral_score": crow.viral_score,
                            "copy": crow.posting_copy or assemble_posting_copy(None, rules_eff, P["platforms"], meta.get("hook", "")),
                            "compliance_status": rec.compliance_status})
        root, ready = build_ready_to_post(project_id, entries, P["campaign"])
        p = s.get(Project, project_id)
        p.status = "rendered"  # type: ignore[union-attr]
    skipped = len(entries) - ready
    tracker.step("export", "done", detail=f"{ready} clip(s) in READY_TO_POST" + (f"; {skipped} FAILED compliance (kept in exports only)" if skipped else ""))
    tracker.finish("done", message=f"Rendered {len(results)}/{len(C)} clip(s). {ready} ready to post in {root}")


def is_humorous(hook_type: str, tone: str) -> bool:
    return hook_type == "humor" or any(k in tone for k in ("humor", "humour", "funny", "comed", "playful", "banter"))


def _set_render_job(candidate_pk: str, job_id: str, status: str, error: str | None = None) -> None:
    with session_scope() as s:
        rj = (s.query(RenderJob).filter(RenderJob.candidate_pk == candidate_pk, RenderJob.job_id == job_id)
              .order_by(RenderJob.created_at.desc()).first())
        if rj:
            rj.status, rj.error = status, error
            if status in ("done", "error"):
                rj.finished_at = datetime.now(UTC)
