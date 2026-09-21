"""REST + SSE API."""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sse_starlette import EventSourceResponse

from ..ai.provider import AIProviderError
from ..ai.service import get_provider
from ..candidates.scoring import final_score, local_penalties
from ..candidates.snapping import clip_text, snap_clip
from ..config import MODELS_DIR, get_settings
from ..db import get_db
from ..models import Candidate, LongFormClip, Project, SourceVideo
from ..paths import broll_dir, ensure_project_tree, is_within, music_dir, project_dir, projects_root, whisper_models_dir
from ..pipeline import youtube_retry
from ..pipeline.analyze import run_analysis
from ..pipeline.longform import run_long_form_analysis, run_long_render
from ..pipeline.progress import JobTracker, current_snapshot, is_running, start_job
from ..pipeline.render_batch import run_render
from ..schemas.ai import CampaignRules, Penalty, ViralCandidate
from ..services import broll as broll_mod
from ..services import sfx_library
from ..services.captions import PRESETS
from ..services.compliance import ClipFacts, evaluate_compliance
from ..services.ffmpeg import FFmpegMissingError, ffmpeg_bin, ffmpeg_filters, ffprobe_bin
from ..services.fonts import list_fonts, resolve_font
from ..services.ingest import LINK_LABELS, IngestError, drive_folder_id, list_drive_folder, save_upload, validate_source_link
from ..services.preferences import get_preferences, update_preferences
from ..services.probe import InvalidMediaError, probe_video
from ..services.transcribe import WHISPER_MODELS, detect_device, flatten_words
from .serializers import candidate_dict, long_form_dict, project_detail, project_summary

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

VALID_PLATFORMS = {"tiktok", "instagram", "youtube_shorts"}
LONG_FORM_MODES = ("auto", "manual", "off")


def _project(s: Session, project_id: str) -> Project:
    p = s.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "Project not found")
    return p


# --------------------------------------------------------------------------- system & settings


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@router.get("/system")
def system_check() -> dict[str, Any]:
    def version(cmd: list[str]) -> str | None:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return (out.stdout or out.stderr).splitlines()[0].strip() if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, IndexError):
            return None

    try:
        ff = ffmpeg_bin()
        ffv = version([ff, "-version"])
    except FFmpegMissingError:
        ff, ffv = None, None
    try:
        fp = ffprobe_bin()
    except FFmpegMissingError:
        fp = None
    filters = ffmpeg_filters() if ff else set()
    node = shutil.which("node")
    settings = get_settings()
    system = platform.system()
    install_hint = {"Darwin": "brew install ffmpeg", "Windows": "winget install Gyan.FFmpeg",
                    "Linux": "sudo apt install ffmpeg"}.get(system, "Install FFmpeg from https://ffmpeg.org/download.html")
    downloaded = sorted({p.name.replace("models--Systran--faster-whisper-", "") for p in whisper_models_dir().glob("models--*")})
    return {
        "python": sys.version.split()[0],
        "platform": f"{system} {platform.machine()}",
        "ffmpeg": {"path": ff, "version": ffv, "ok": bool(ff), "install_hint": install_hint,
                   "libass": "subtitles" in filters, "required_filters_ok": all(f in filters for f in ("loudnorm", "overlay", "silencedetect"))},
        "ffprobe": {"path": fp, "ok": bool(fp)},
        "node": {"path": node, "version": version([node, "--version"]) if node else None},
        "gemini": {"configured": settings.gemini_configured, "model": settings.model_name},
        "whisper": {"device": detect_device(), "downloaded_models": downloaded},
        "face_model": YUNET_OK(),
        "fonts": {"heavy": resolve_font(None, "heavy"), "bold": resolve_font(None, "bold")},
        "workspace": str(settings.workspace),
    }


def YUNET_OK() -> bool:  # noqa: N802
    return (MODELS_DIR / "face_detection_yunet_2023mar.onnx").exists()


@router.get("/settings")
def get_settings_api() -> dict[str, Any]:
    s = get_settings()
    return {
        "preferences": get_preferences().model_dump(),
        "gemini": {"configured": s.gemini_configured, "model": s.model_name},
        "whisper_models": list(WHISPER_MODELS),
        "caption_styles": {k: v.to_public() for k, v in PRESETS.items()},
        "fonts": sorted(list_fonts().keys())[:400],
        "music_files": sorted(p.name for p in music_dir().iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".flac")),
        "broll_files": [Path(i.path).name for i in broll_mod.index_broll()],
        "music_beds": _music_beds(),
        "sfx_categories": {k: {"label": c.label, "description": c.description, "auto": c.auto}
                           for k, c in sfx_library.CATEGORIES.items()},
    }


def _music_beds() -> list[dict[str, str]]:
    """Music tracks found in the sound library (offered as background music)."""
    try:
        items = sfx_library.scan_library() if not sfx_library.scan_status()["running"] else []
    except Exception:  # noqa: BLE001 - settings must load even if the library is broken
        return []
    return [{"name": i.name, "path": i.path} for i in sfx_library.music_beds(items)]


@router.put("/settings")
def put_settings(body: dict[str, Any]) -> dict[str, Any]:
    body.pop("gemini_api_key", None)  # never accepted over the API
    if body.get("music_file") and not Path(str(body["music_file"])).is_absolute():
        body["music_file"] = str(music_dir() / Path(str(body["music_file"])).name)
    try:
        prefs = update_preferences(body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"preferences": prefs.model_dump()}


# --------------------------------------------------------------------------- projects


@router.get("/projects")
def list_projects(s: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = s.query(Project).filter(Project.hidden.is_(False)).order_by(Project.created_at.desc()).limit(200).all()
    return [project_summary(p, s) for p in rows]


@router.get("/drive/folder")
async def drive_folder(url: str) -> dict[str, Any]:
    """List the videos in a public Google Drive folder so the user can pick which ones to clip."""
    if not drive_folder_id(url):
        raise HTTPException(422, "That isn't a Google Drive folder link.")
    try:
        files = await asyncio.to_thread(list_drive_folder, url)
    except IngestError as e:
        raise HTTPException(400, str(e)) from e
    return {"files": files}


@router.post("/projects")
async def create_project(
    name: str = Form(..., min_length=1, max_length=200),
    campaign_name: str = Form("", max_length=200),
    campaign_rules_text: str = Form("", max_length=50000),
    platforms: str = Form("tiktok,instagram,youtube_shorts"),
    desired_clip_count: int = Form(10, ge=1, le=30),
    duration_mode: str = Form("auto"),
    min_duration: float = Form(15, ge=3, le=600),
    max_duration: float = Form(60, ge=5, le=600),
    use_gemini: bool = Form(True),
    long_form_mode: str = Form("auto"),
    long_form_count: int = Form(0, ge=0, le=20),
    source_url: str = Form(""),
    youtube_url: str = Form(""),  # older clients
    permission_confirmed: bool = Form(False),
    file: UploadFile | None = File(None),
    s: Session = Depends(get_db),
) -> dict[str, Any]:
    if not permission_confirmed:
        raise HTTPException(422, "Confirm that you have permission to clip this content.")
    plats = [x.strip() for x in platforms.split(",") if x.strip() in VALID_PLATFORMS]
    if not plats:
        raise HTTPException(422, "Choose at least one target platform.")
    if duration_mode not in ("auto", "manual"):
        raise HTTPException(422, "duration_mode must be auto or manual")
    if min_duration > max_duration:
        raise HTTPException(422, "Minimum duration must not exceed maximum.")
    if long_form_mode not in LONG_FORM_MODES:
        raise HTTPException(422, "long_form_mode must be auto, manual or off")
    has_file = file is not None and bool(file.filename)
    link = (source_url or youtube_url).strip()
    if has_file == bool(link):
        raise HTTPException(422, "Provide either an uploaded video or a link (exactly one).")
    origin = "upload"
    if link:
        try:
            origin, link = validate_source_link(link)
        except IngestError as e:
            raise HTTPException(422, str(e)) from e

    p = Project(name=name.strip(), campaign_name=campaign_name.strip(), campaign_rules_text=campaign_rules_text,
                platforms=plats, desired_clip_count=desired_clip_count, duration_mode=duration_mode,
                min_duration=min_duration, max_duration=max_duration, use_gemini=use_gemini,
                long_form_mode=long_form_mode, long_form_count=long_form_count)
    s.add(p)
    s.flush()
    ensure_project_tree(p.id)
    try:
        if has_file:
            assert file is not None
            path, size, original = await save_upload(file, project_dir(p.id, "source"), get_settings().max_upload_bytes)
            meta = await asyncio.to_thread(probe_video, path)
            s.add(SourceVideo(project_id=p.id, origin="upload", original_filename=original[:300], stored_path=str(path),
                              size_bytes=size, probe=meta.model_dump(), permission_confirmed=True))
        else:
            s.add(SourceVideo(project_id=p.id, origin=origin, source_url=link, permission_confirmed=True,
                              original_filename=LINK_LABELS[origin]))
        s.commit()
    except (IngestError, InvalidMediaError) as e:
        s.rollback()
        shutil.rmtree(projects_root() / p.id, ignore_errors=True)
        raise HTTPException(400, str(e)) from e
    except Exception:
        s.rollback()
        shutil.rmtree(projects_root() / p.id, ignore_errors=True)
        raise
    s.refresh(p)
    return project_detail(p, s)


@router.post("/projects/{project_id}/source")
async def replace_source(
    project_id: str,
    file: UploadFile | None = File(None),
    source_url: str = Form(""),
    permission_confirmed: bool = Form(False),
    s: Session = Depends(get_db),
) -> dict[str, Any]:
    """Give a project whose source was never obtained (e.g. YouTube refused the download) a file or another link."""
    p = _project(s, project_id)
    if is_running(project_id):
        raise HTTPException(409, "A job is running for this project.")
    if not permission_confirmed:
        raise HTTPException(422, "Confirm that you have permission to clip this content.")
    src = p.source
    if src is not None and src.stored_path and Path(src.stored_path).exists():
        raise HTTPException(409, "This project already has a source video. Create a new project to use a different one.")
    has_file = file is not None and bool(file.filename)
    if has_file == bool(source_url.strip()):
        raise HTTPException(422, "Provide either an uploaded video or a link (exactly one).")
    youtube_retry.cancel(p.id)
    source_dir = project_dir(p.id, "source")
    if not has_file:
        try:
            origin, link = validate_source_link(source_url)
        except IngestError as e:
            raise HTTPException(422, str(e)) from e
        if src is not None and (origin, link) != (src.origin, src.source_url):
            for f in source_dir.glob("original*"):  # partial data belongs to the old link
                if f.is_file():
                    f.unlink(missing_ok=True)
        if src is None:
            src = SourceVideo(project_id=p.id)
            s.add(src)
        src.origin, src.source_url, src.original_filename = origin, link, LINK_LABELS[origin]
        src.stored_path, src.size_bytes, src.probe, src.sha256, src.proxy_path, src.audio_path = "", 0, None, "", None, None
        src.permission_confirmed = True
        p.status, p.error = "created", None
        s.commit()
        s.refresh(p)
        return project_detail(p, s)
    assert file is not None
    staging = source_dir / "_incoming"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        path, size, original = await save_upload(file, staging, get_settings().max_upload_bytes)
        meta = await asyncio.to_thread(probe_video, path)
    except (IngestError, InvalidMediaError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise HTTPException(400, str(e)) from e
    # Drop leftovers of the failed download (partial files) before moving the upload into place.
    for f in source_dir.glob("original*"):
        if f.is_file():
            f.unlink(missing_ok=True)
    final = source_dir / path.name
    path.replace(final)
    shutil.rmtree(staging, ignore_errors=True)
    if src is None:
        src = SourceVideo(project_id=p.id, source_url=None)
        s.add(src)
    src.origin, src.original_filename, src.stored_path, src.size_bytes = "upload", original[:300], str(final), size
    src.probe, src.sha256, src.proxy_path, src.audio_path = meta.model_dump(), "", None, None
    src.permission_confirmed = True
    p.status, p.error = "created", None
    s.commit()
    s.refresh(p)
    return project_detail(p, s)


@router.get("/projects/{project_id}")
def get_project(project_id: str, s: Session = Depends(get_db)) -> dict[str, Any]:
    return project_detail(_project(s, project_id), s)


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    campaign_name: str | None = Field(None, max_length=200)
    campaign_rules_text: str | None = Field(None, max_length=50000)
    platforms: list[str] | None = None
    desired_clip_count: int | None = Field(None, ge=1, le=30)
    duration_mode: str | None = None
    min_duration: float | None = Field(None, ge=3, le=600)
    max_duration: float | None = Field(None, ge=5, le=600)
    use_gemini: bool | None = None
    long_form_mode: str | None = None
    long_form_count: int | None = Field(None, ge=0, le=20)
    campaign_rules: dict[str, Any] | None = None  # manual correction of parsed rules


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, s: Session = Depends(get_db)) -> dict[str, Any]:
    p = _project(s, project_id)
    if is_running(project_id):
        raise HTTPException(409, "A job is running for this project.")
    data = body.model_dump(exclude_unset=True)
    if "platforms" in data:
        data["platforms"] = [x for x in data["platforms"] or [] if x in VALID_PLATFORMS]
    if "duration_mode" in data and data["duration_mode"] not in ("auto", "manual"):
        raise HTTPException(422, "duration_mode must be auto or manual")
    if "long_form_mode" in data and data["long_form_mode"] not in LONG_FORM_MODES:
        raise HTTPException(422, "long_form_mode must be auto, manual or off")
    if "campaign_rules" in data:
        rules = CampaignRules.model_validate(data.pop("campaign_rules") or {})
        p.campaign_rules_json = rules.model_dump()
        p.campaign_rules_source = "manual"
    if "campaign_rules_text" in data and data["campaign_rules_text"] != p.campaign_rules_text:
        p.campaign_rules_json = None
        p.campaign_rules_source = ""
    for k, v in data.items():
        setattr(p, k, v)
    s.commit()
    return project_detail(p, s)


def _remove_project(s: Session, p: Project) -> None:
    project_id = p.id
    s.delete(p)
    s.commit()
    youtube_retry.cancel(project_id)
    folder = projects_root() / project_id
    if is_within(folder, projects_root()):
        shutil.rmtree(folder, ignore_errors=True)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, s: Session = Depends(get_db)) -> dict[str, Any]:
    p = _project(s, project_id)
    if is_running(project_id):
        raise HTTPException(409, "A job is running for this project.")
    _remove_project(s, p)
    return {"deleted": project_id}


@router.post("/projects/cleanup-history")
def cleanup_history(s: Session = Depends(get_db)) -> dict[str, Any]:
    """Clear the dashboard history: failed projects are deleted with their files; the rest are only hidden,
    so their source video, renders and exports stay on disk. Running or auto-retrying projects are left alone."""
    deleted = hidden = 0
    for p in s.query(Project).filter(Project.hidden.is_(False)).all():
        if is_running(p.id) or p.status == "waiting":
            continue
        if p.status == "error":
            _remove_project(s, p)
            deleted += 1
        else:
            p.hidden = True
            hidden += 1
    s.commit()
    return {"deleted": deleted, "hidden": hidden}


class AnalyzeBody(BaseModel):
    use_gemini: bool | None = None
    whisper_model: str | None = None
    auto_render: bool = False
    force_youtube: bool = False  # try YouTube now even though it is currently blocking this connection


@router.post("/projects/{project_id}/analyze")
def analyze(project_id: str, body: AnalyzeBody, s: Session = Depends(get_db)) -> dict[str, Any]:
    _project(s, project_id)
    if body.whisper_model and body.whisper_model not in WHISPER_MODELS:
        raise HTTPException(422, "Unknown Whisper model")
    if is_running(project_id):
        raise HTTPException(409, "A job is already running for this project.")
    youtube_retry.cancel(project_id)  # a manual run replaces any scheduled retry
    tracker = JobTracker(project_id, "analyze", [])
    try:
        start_job(project_id, tracker, run_analysis, body.model_dump())
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return tracker.snapshot()


class RenderBody(BaseModel):
    candidate_ids: list[str] | None = None
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("/projects/{project_id}/render")
def render(project_id: str, body: RenderBody, s: Session = Depends(get_db)) -> dict[str, Any]:
    p = _project(s, project_id)
    if p.status in ("created", "analyzing", "waiting"):
        raise HTTPException(409, "Analyze the source before rendering.")
    tracker = JobTracker(project_id, "render", [])
    try:
        start_job(project_id, tracker, run_render, body.candidate_ids, body.options)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return tracker.snapshot()


@router.delete("/projects/{project_id}/youtube-retry")
def cancel_youtube_retry(project_id: str, s: Session = Depends(get_db)) -> dict[str, Any]:
    p = _project(s, project_id)
    youtube_retry.cancel(project_id)
    if p.status == "waiting":
        p.status = "error"
        p.error = ("Automatic YouTube retry cancelled. Click Analyze to try again, "
                   "or upload the file / paste a Drive or Dropbox link instead.")
        s.commit()
    return project_detail(p, s)


@router.get("/projects/{project_id}/job")
def job(project_id: str) -> dict[str, Any] | None:
    return current_snapshot(project_id)


@router.get("/projects/{project_id}/events")
async def events(project_id: str, request: Request):
    async def gen():
        last = None
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            snap = await asyncio.to_thread(current_snapshot, project_id)
            key = json.dumps(snap, sort_keys=True, default=str) if snap else None
            if key != last:
                last = key
                idle = 0
                yield {"event": "job", "data": key or "null"}
            else:
                idle += 1
                if idle % 30 == 0:
                    yield {"event": "ping", "data": "{}"}
            await asyncio.sleep(0.5)

    return EventSourceResponse(gen())


@router.get("/projects/{project_id}/candidates")
def candidates(project_id: str, s: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _project(s, project_id)
    rows = s.query(Candidate).filter(Candidate.project_id == project_id).order_by(Candidate.rank).all()
    return [candidate_dict(c) for c in rows]


@router.get("/projects/{project_id}/transcript")
def transcript(project_id: str, s: Session = Depends(get_db)) -> dict[str, Any]:
    _project(s, project_id)
    p = project_dir(project_id, "transcript") / "transcript.json"
    if not p.exists():
        raise HTTPException(404, "No transcript yet")
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- candidates


class CandidatePatch(BaseModel):
    selected: bool | None = None
    rejected: bool | None = None
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, ge=0)
    snap: bool = True
    hook_index: int | None = Field(None, ge=0, le=10)
    custom_hook: str | None = Field(None, max_length=120)


def _recheck(c: Candidate, p: Project) -> None:
    rules = CampaignRules.model_validate(p.campaign_rules_json) if p.campaign_rules_json else None
    d = c.data or {}
    ai = d.get("campaign_compliance") or {}
    report = evaluate_compliance(rules, ClipFacts(
        duration=c.end - c.start, platforms=p.platforms, transcript_text=c.transcript_text,
        hook_text=" | ".join(h["text"] for h in c.hooks or []), ai_status=ai.get("status"), ai_reasons=ai.get("reasons", [])),
        has_rules_text=bool((p.campaign_rules_text or "").strip()))
    c.compliance_status, c.compliance = report.status, report.model_dump()


@router.patch("/candidates/{pk}")
def patch_candidate(pk: str, body: CandidatePatch, s: Session = Depends(get_db)) -> dict[str, Any]:
    c = s.get(Candidate, pk)
    if c is None:
        raise HTTPException(404, "Candidate not found")
    p = _project(s, c.project_id)
    if body.selected is not None:
        c.selected = body.selected
        if body.selected:
            c.rejected = False
    if body.rejected is not None:
        c.rejected = body.rejected
        if body.rejected:
            c.selected = False
    if body.custom_hook and body.custom_hook.strip():
        hooks = list(c.hooks or [])
        hooks.append({"text": body.custom_hook.strip(), "source": "user"})
        c.hooks = hooks
        c.hook_index = len(hooks) - 1
    elif body.hook_index is not None:
        if body.hook_index >= len(c.hooks or []):
            raise HTTPException(422, "hook_index out of range")
        c.hook_index = body.hook_index
    if body.start is not None or body.end is not None:
        start = body.start if body.start is not None else c.start
        end = body.end if body.end is not None else c.end
        duration = float(((p.source.probe if p.source else None) or {}).get("duration") or end)
        if not (0 <= start < end <= duration + 0.01):
            raise HTTPException(422, "Invalid start/end")
        tpath = project_dir(p.id, "transcript") / "transcript.json"
        words = flatten_words(json.loads(tpath.read_text(encoding="utf-8"))) if tpath.exists() else []
        notes: list[str] = []
        if body.snap and words:
            snap = snap_clip(words, start, end, min_duration=0.5, max_duration=10**6, media_duration=duration)
            start, end, notes = snap.start, snap.end, snap.notes
        c.start, c.end = round(start, 3), round(end, 3)
        c.snap_notes = notes + ["Boundaries edited by user."]
        c.transcript_text = clip_text(words, c.start, c.end)
        c.user_edited = True
        analysis_mm = (p.campaign_rules_json or {})
        lo = analysis_mm.get("min_duration") or p.min_duration
        hi = analysis_mm.get("max_duration") or p.max_duration
        local = local_penalties(clip_text(words, c.start, c.start + 3.5), c.transcript_text, notes, c.end - c.start, lo, hi)
        data = dict(c.data)
        data["local_penalties"] = [pn.model_dump() for pn in local]
        c.data = data
        c.viral_score = final_score(ViralCandidate.model_validate(data), local)
    _recheck(c, p)
    s.commit()
    return candidate_dict(c)


@router.post("/candidates/{pk}/refine")
def refine_candidate(pk: str, s: Session = Depends(get_db)) -> dict[str, Any]:
    """Text-only AI re-score after boundary edits (1 cheap Gemini request, no video)."""
    c = s.get(Candidate, pk)
    if c is None:
        raise HTTPException(404, "Candidate not found")
    p = _project(s, c.project_id)
    provider = get_provider(p.id, p.use_gemini)
    rules = CampaignRules.model_validate(p.campaign_rules_json) if p.campaign_rules_json else CampaignRules()
    prev = {k: (c.data or {}).get(k) for k in ("topic", "summary", "subscores", "reason_it_works", "suggested_hook_text")}
    try:
        ref = provider.refine_candidate({"candidate_id": c.candidate_id, "start": c.start, "end": c.end,
                                         "transcript": c.transcript_text, "previous": prev},
                                        rules, p.platforms, p.min_duration, p.max_duration)
    except AIProviderError as e:
        raise HTTPException(502, str(e)) from e
    data = dict(c.data)
    data["subscores"] = ref.subscores.model_dump()
    data["penalties"] = [pn.model_dump() for pn in ref.penalties]
    if ref.reason_it_works:
        data["reason_it_works"] = ref.reason_it_works
    data["boundary_feedback"] = ref.boundary_feedback
    data["refined"] = True
    c.data = data
    user_hooks = [h for h in (c.hooks or []) if h.get("source") == "user"]
    new_hooks = [{"text": h, "source": "ai"} for h in [ref.suggested_hook_text, *ref.alternative_hooks] if h][:3]
    if new_hooks:
        c.hooks = new_hooks + user_hooks
        c.hook_index = 0
    local = [Penalty.model_validate(x) for x in data.get("local_penalties", [])]
    c.viral_score = final_score(ViralCandidate.model_validate(data), local)
    _recheck(c, p)
    s.commit()
    return candidate_dict(c)


# --------------------------------------------------------------------------- media & files


@router.get("/projects/{project_id}/media/source")
def media_source(project_id: str, s: Session = Depends(get_db)):
    p = _project(s, project_id)
    if p.source is None:
        raise HTTPException(404, "No source")
    for cand in (p.source.proxy_path, p.source.stored_path):
        if cand and Path(cand).exists():
            ext = Path(cand).suffix.lower()
            mt = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}.get(ext, "video/mp4")
            return FileResponse(cand, media_type=mt)
    raise HTTPException(404, "Source file not available yet")


@router.get("/projects/{project_id}/files/{rel_path:path}")
def project_file(project_id: str, rel_path: str, s: Session = Depends(get_db)):
    _project(s, project_id)
    base = projects_root() / project_id
    target = (base / rel_path).resolve()
    if not is_within(target, base) or not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(target)


class OpenFolderBody(BaseModel):
    target: str = "ready_to_post"


@router.post("/projects/{project_id}/open-folder")
def open_folder(project_id: str, body: OpenFolderBody, s: Session = Depends(get_db)) -> dict[str, Any]:
    _project(s, project_id)
    sub = {"ready_to_post": "READY_TO_POST", "exports": "exports"}.get(body.target)
    if sub is None:
        raise HTTPException(422, "Unknown folder")
    folder = project_dir(project_id, sub)
    system = platform.system()
    cmd = ["open", str(folder)] if system == "Darwin" else ["explorer", str(folder)] if system == "Windows" else ["xdg-open", str(folder)]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise HTTPException(500, f"Could not open folder: {e}") from e
    return {"path": str(folder)}


@router.get("/broll")
def broll_list() -> dict[str, Any]:
    return {"folder": str(broll_dir()), "items": [{"file": Path(i.path).name, "tags": sorted(i.tags), "license": i.license}
                                                 for i in broll_mod.index_broll()]}


# --------------------------------------------------------------------------- long-form


@router.get("/projects/{project_id}/long-form")
def long_form_list(project_id: str, s: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _project(s, project_id)
    rows = s.query(LongFormClip).filter(LongFormClip.project_id == project_id).order_by(LongFormClip.rank).all()
    return [long_form_dict(r) for r in rows]


@router.post("/projects/{project_id}/long-form/analyze")
def long_form_analyze(project_id: str, s: Session = Depends(get_db)) -> dict[str, Any]:
    p = _project(s, project_id)
    if p.status in ("created", "analyzing", "waiting") or not (p.source and p.source.probe):
        raise HTTPException(409, "Analyze the source first.")
    tracker = JobTracker(project_id, "longform", [])
    try:
        start_job(project_id, tracker, run_long_form_analysis, {})
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return tracker.snapshot()


@router.post("/projects/{project_id}/long-form/render")
def long_form_render(project_id: str, body: RenderBody, s: Session = Depends(get_db)) -> dict[str, Any]:
    _project(s, project_id)
    tracker = JobTracker(project_id, "render_long", [])
    try:
        start_job(project_id, tracker, run_long_render, body.candidate_ids, body.options)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return tracker.snapshot()


class LongFormPatch(BaseModel):
    selected: bool | None = None
    rejected: bool | None = None
    title_index: int | None = Field(None, ge=0, le=10)
    custom_title: str | None = Field(None, max_length=100)
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, ge=0)


@router.patch("/long-form/{pk}")
def long_form_patch(pk: str, body: LongFormPatch, s: Session = Depends(get_db)) -> dict[str, Any]:
    c = s.get(LongFormClip, pk)
    if c is None:
        raise HTTPException(404, "Long-form clip not found")
    p = _project(s, c.project_id)
    if body.selected is not None:
        c.selected = body.selected
        if body.selected:
            c.rejected = False
    if body.rejected is not None:
        c.rejected = body.rejected
        if body.rejected:
            c.selected = False
    data = dict(c.data or {})
    if body.custom_title and body.custom_title.strip():
        alts = list(data.get("alternative_titles", []))
        alts.append(" ".join(body.custom_title.split()))
        data["alternative_titles"] = alts
        c.title_index = len(alts)
    elif body.title_index is not None:
        if body.title_index > len(data.get("alternative_titles", [])):
            raise HTTPException(422, "title_index out of range")
        c.title_index = body.title_index
    if body.start is not None or body.end is not None:
        start = body.start if body.start is not None else c.start
        end = body.end if body.end is not None else c.end
        duration = float(((p.source.probe if p.source else None) or {}).get("duration") or end)
        if not (0 <= start < end <= duration + 0.01) or end - start < 30:
            raise HTTPException(422, "Invalid start/end (long-form clips must be at least 30 s)")
        tpath = project_dir(p.id, "transcript") / "transcript.json"
        words = flatten_words(json.loads(tpath.read_text(encoding="utf-8"))) if tpath.exists() else []
        notes: list[str] = []
        if words:
            snap = snap_clip(words, start, end, min_duration=30, max_duration=10**6, media_duration=duration)
            start, end, notes = snap.start, snap.end, snap.notes
        c.start, c.end = round(start, 3), round(end, 3)
        c.snap_notes = notes + ["Boundaries edited by user."]
        c.transcript_text = clip_text(words, c.start, c.end)
        from ..candidates.longform import clean_chapters
        from ..schemas.ai import Chapter

        data["chapters_clean"] = clean_chapters([Chapter.model_validate(x) for x in data.get("chapters", [])], c.start, c.end)
        co = data.get("cold_open_snapped")
        if co and not (c.start <= co["start"] < co["end"] <= c.end):
            data["cold_open_snapped"] = None
    c.data = data
    s.commit()
    return long_form_dict(c)


# --------------------------------------------------------------------------- sound library


def _sfx_items() -> list[sfx_library.SfxItem]:
    if sfx_library.scan_status()["running"]:
        raise HTTPException(409, "The sound library is being scanned; try again in a moment.")
    return sfx_library.scan_library()


@router.get("/sfx")
async def sfx_list() -> dict[str, Any]:
    status = sfx_library.scan_status()
    dirs = [str(d) for d in sfx_library.library_dirs()]
    if status["running"]:
        return {"scanning": status, "dirs": dirs, "summary": None, "items": []}
    items = await asyncio.to_thread(sfx_library.scan_library)
    return {"scanning": None, "dirs": dirs, "summary": sfx_library.library_summary(items),
            "items": [i.public() for i in items if not i.pack]}


@router.post("/sfx/rescan")
def sfx_rescan(force: bool = False) -> dict[str, Any]:
    if sfx_library.scan_status()["running"]:
        return {"started": False, "scanning": sfx_library.scan_status()}
    import threading

    threading.Thread(target=lambda: sfx_library.scan_library(force=force), daemon=True, name="sfx-scan").start()
    return {"started": True}


class SfxPatch(BaseModel):
    category: str | None = None
    enabled: bool | None = None


@router.patch("/sfx/{sid}")
def sfx_patch(sid: str, body: SfxPatch) -> dict[str, Any]:
    items = _sfx_items()
    if sfx_library.find_item(items, sid) is None:
        raise HTTPException(404, "Sound not found")
    try:
        sfx_library.set_override(sid, body.category, body.enabled)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    item = sfx_library.find_item(sfx_library.scan_library(), sid)
    assert item is not None
    return item.public()


@router.get("/sfx/{sid}/audio")
def sfx_audio(sid: str):
    """Serve an indexed library file for preview (only files the index knows about)."""
    item = sfx_library.find_item(_sfx_items(), sid)
    if item is None or not Path(item.path).is_file():
        raise HTTPException(404, "Sound not found")
    ext = Path(item.path).suffix.lower()
    mt = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg",
          ".flac": "audio/flac", ".aif": "audio/aiff", ".aiff": "audio/aiff", ".mp4": "video/mp4",
          ".mov": "video/quicktime"}.get(ext, "application/octet-stream")
    return FileResponse(item.path, media_type=mt)
