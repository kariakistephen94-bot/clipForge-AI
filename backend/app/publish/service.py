"""Publishing orchestration: defaults for the confirm dialog, the upload job, and history.

Nothing here runs on its own. A publish always starts from an explicit, confirmed request
made by the user in the dashboard, and clips that FAIL campaign compliance are refused.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..db import session_scope
from ..models import Candidate, ExportRecord, Project, PublishJob
from ..pipeline.progress import JobTracker
from ..services.posting import full_caption
from . import store, tiktok, youtube
from .youtube import PublishError

log = logging.getLogger(__name__)

PLATFORM_LABEL = {"youtube": "YouTube", "tiktok": "TikTok"}
VARIANT_LABEL = {"A": "A · hook overlay", "B": "B · alternative hook", "C": "C · captions only"}


def _export_for(s: Any, candidate_pk: str) -> tuple[Candidate, ExportRecord]:
    cand = s.get(Candidate, candidate_pk)
    if cand is None:
        raise PublishError("Clip not found.")
    export = (s.query(ExportRecord).filter(ExportRecord.candidate_pk == candidate_pk)
              .order_by(ExportRecord.created_at.desc()).first())
    if export is None:
        raise PublishError("This clip has not been rendered yet. Render it first, then publish.")
    return cand, export


def resolve_file(export: ExportRecord, variant: str | None) -> Path:
    files = export.files or {}
    name = files.get(variant or "", "") or files.get("primary", "")
    if not name:
        raise PublishError("No rendered video file found for this clip.")
    path = Path(export.folder) / name
    if not path.is_file():
        raise PublishError(f"Rendered file is missing on disk: {name}")
    return path


def clip_options(candidate_pk: str) -> dict[str, Any]:
    """Everything the confirm dialog needs: suggested copy, files, compliance and connection state."""
    with session_scope() as s:
        cand, export = _export_for(s, candidate_pk)
        project = s.get(Project, cand.project_id)
        copy = cand.posting_copy or {}
        meta = export.metadata_json or {}
        hooks = cand.hooks or []
        hook = hooks[cand.hook_index]["text"] if 0 <= cand.hook_index < len(hooks) else (hooks[0]["text"] if hooks else "")
        variants = {v: n for v, n in (export.files or {}).items() if v in ("A", "B", "C")}
        tags = [t.lstrip("#") for t in (copy.get("hashtags") or [])]
        yt_title = (copy.get("youtube_title") or hook or cand.data.get("topic", "") or "Clip")[:100]
        yt_desc = full_caption("youtube_shorts", copy) if copy else (cand.data.get("summary") or "")
        tt_caption = full_caption("tiktok", copy) if copy else hook
        return {
            "candidate_pk": candidate_pk,
            "project_id": cand.project_id,
            "project_name": project.name if project else "",
            "campaign": project.campaign_name if project else "",
            "topic": cand.data.get("topic", ""),
            "hook": hook,
            "duration": round(float(meta.get("duration") or (cand.end - cand.start)), 1),
            "viral_score": cand.viral_score,
            "compliance_status": export.compliance_status or cand.compliance_status,
            "compliance_blocks_publishing": (export.compliance_status or "") == "FAILED",
            "variants": {v: VARIANT_LABEL.get(v, v) for v in sorted(variants)},
            "default_variant": "A" if "A" in variants else next(iter(sorted(variants)), ""),
            "youtube": {"title": yt_title, "description": yt_desc, "tags": tags[:10], "privacy": "private",
                        "made_for_kids": False},
            "tiktok": {"caption": tt_caption[:2200], "privacy": "SELF_ONLY", "mode": "inbox",
                       "disable_comment": False, "disable_duet": False, "disable_stitch": False},
            "connections": store.status(),
        }


def _record(project_id: str, candidate_pk: str, export_id: str, target: dict[str, Any], path: Path,
            account: str) -> str:
    with session_scope() as s:
        job = PublishJob(
            project_id=project_id, candidate_pk=candidate_pk, export_id=export_id,
            platform=target["platform"], mode=target.get("mode", ""), status="uploading",
            privacy=target.get("privacy", ""), title=target.get("title", "")[:300], file_path=str(path),
            account_name=account,
        )
        s.add(job)
        s.flush()
        return job.id


def _finish(job_id: str, *, status: str, remote: dict[str, Any] | None = None, error: str | None = None) -> None:
    with session_scope() as s:
        job = s.get(PublishJob, job_id)
        if job is None:
            return
        job.status = status
        job.error = error
        job.finished_at = datetime.now(UTC)
        if remote:
            job.remote_id = str(remote.get("id", "")) or None
            job.remote_url = str(remote.get("url", "")) or None
            job.detail = remote


def publish_steps(targets: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(f"pub_{i}_{t['platform']}", f"Publishing to {PLATFORM_LABEL.get(t['platform'], t['platform'])}")
            for i, t in enumerate(targets)]


def run_publish(project_id: str, tracker: JobTracker, payload: dict[str, Any]) -> None:
    """Upload one rendered clip to the requested platforms. Called only from a confirmed UI request."""
    candidate_pk = payload["candidate_pk"]
    variant = payload.get("variant") or None
    targets: list[dict[str, Any]] = payload["targets"]

    with session_scope() as s:
        cand, export = _export_for(s, candidate_pk)
        if (export.compliance_status or "") == "FAILED":
            raise PublishError("This clip FAILED the campaign compliance check, so ClipForge will not publish it. "
                               "Fix the issues (or re-render) first.")
        export_id, folder, files = export.id, export.folder, dict(export.files or {})
        topic = cand.data.get("topic", "")
    path = resolve_file(ExportRecord(folder=folder, files=files), variant)

    results: list[dict[str, Any]] = []
    for i, target in enumerate(targets):
        platform = target["platform"]
        key = f"pub_{i}_{platform}"
        label = PLATFORM_LABEL.get(platform, platform)
        tracker.step(key, "running", detail=f"Preparing {path.name}")
        creds = store.load(platform)
        if creds is None or not (creds.access_token or creds.refresh_token):
            tracker.step(key, "error", detail=f"{label} is not connected. Connect it in Settings.")
            results.append({"platform": platform, "status": "error", "error": "not connected"})
            continue
        job_id = _record(project_id, candidate_pk, export_id, target, path, creds.account_name)
        try:
            def progress(f: float, detail: str, key: str = key) -> None:
                tracker.step(key, progress=max(0.0, min(1.0, f)), detail=detail)

            if platform == "youtube":
                meta = youtube.build_metadata(
                    title=target.get("title") or topic, description=target.get("description", ""),
                    tags=target.get("tags") or [], privacy=target.get("privacy", "private"),
                    made_for_kids=bool(target.get("made_for_kids")))
                remote = youtube.upload_video(creds, path, meta, on_progress=progress)
                detail = f"Uploaded as {remote.get('privacy', '')} → {remote.get('url', '')}"
            elif platform == "tiktok":
                mode = target.get("mode", "inbox")
                post_info = None
                if mode == "direct":
                    post_info = tiktok.build_post_info(
                        title=target.get("title", ""), privacy=target.get("privacy", "SELF_ONLY"),
                        disable_comment=bool(target.get("disable_comment")),
                        disable_duet=bool(target.get("disable_duet")),
                        disable_stitch=bool(target.get("disable_stitch")))
                remote = tiktok.publish_video(creds, path, mode=mode, post_info=post_info, on_progress=progress)
                detail = ("Sent to your TikTok inbox — open TikTok to finish posting"
                          if remote.get("needs_user_action") else f"Posted ({remote.get('status', '')})")
            else:
                raise PublishError(f"Unknown platform '{platform}'")
            _finish(job_id, status="done", remote=remote)
            tracker.step(key, "done", detail=detail)
            results.append({"platform": platform, "status": "done", **remote})
        except Exception as e:  # noqa: BLE001 - one platform failing must not stop the other
            msg = str(e) or type(e).__name__
            log.exception("Publishing to %s failed", platform)
            _finish(job_id, status="error", error=msg)
            tracker.step(key, "error", detail=msg[:400])
            results.append({"platform": platform, "status": "error", "error": msg})

    ok = [r for r in results if r["status"] == "done"]
    failed = [r for r in results if r["status"] != "done"]
    tracker.set_message(f"Published to {len(ok)}/{len(results)} platform(s)"
                        + (f"; {len(failed)} failed" if failed else ""))
    if not ok:
        raise PublishError(failed[0].get("error", "Publishing failed") if failed else "Publishing failed")


def history(project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = s.query(PublishJob)
        if project_id:
            q = q.filter(PublishJob.project_id == project_id)
        rows = q.order_by(PublishJob.created_at.desc()).limit(limit).all()
        return [{
            "id": r.id, "project_id": r.project_id, "candidate_pk": r.candidate_pk, "platform": r.platform,
            "mode": r.mode, "status": r.status, "privacy": r.privacy, "title": r.title,
            "remote_url": r.remote_url, "remote_id": r.remote_id, "account_name": r.account_name,
            "error": r.error, "created_at": r.created_at.isoformat() if r.created_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "needs_user_action": bool((r.detail or {}).get("needs_user_action")),
        } for r in rows]
