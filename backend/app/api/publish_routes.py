"""Publishing API: connect accounts, then publish a rendered clip on explicit confirmation."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..pipeline.progress import JobTracker, start_job
from ..publish import service, store, tiktok, youtube
from ..publish.oauth import OAuthError, PendingAuth, pending
from ..publish.youtube import PublishError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/publish", tags=["publish"])

CONNECTORS = {"youtube": youtube, "tiktok": tiktok}


def _connector(platform: str):
    if platform not in CONNECTORS:
        raise HTTPException(404, "Unknown platform")
    return CONNECTORS[platform]


def _page(title: str, body: str, ok: bool = True) -> HTMLResponse:
    colour = "#22c55e" if ok else "#f04f5f"
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8"><title>{title}</title>
        <body style="font:16px system-ui;background:#0a0c11;color:#e8ebf4;display:grid;place-items:center;height:100vh;margin:0">
        <div style="max-width:560px;text-align:center;padding:28px;border:1px solid #252d40;border-radius:14px;background:#141925">
        <h2 style="color:{colour};margin:0 0 10px">{title}</h2><p style="color:#8d95aa;line-height:1.5">{body}</p>
        <p style="color:#5d657a;font-size:13px">You can close this tab and return to ClipForge AI.</p></div></body>"""
    )


@router.get("")
def publish_status() -> dict[str, Any]:
    s = get_settings()
    return {
        "configured": {"youtube": s.youtube_configured, "tiktok": s.tiktok_configured},
        "connections": store.status(),
        "redirect_uris": {"youtube": youtube.redirect_uri(), "tiktok": tiktok.redirect_uri()},
        "setup_doc": "PUBLISHING.md",
    }


class ConnectBody(BaseModel):
    mode: str = "inbox"  # tiktok: inbox (drafts, video.upload) | direct (video.publish)
    redirect_uri: str | None = None  # for platforms that refuse loopback redirects


@router.post("/{platform}/connect")
def connect(platform: str, body: ConnectBody) -> dict[str, Any]:
    connector = _connector(platform)
    try:
        if platform == "tiktok":
            url, state, verifier = connector.start_auth(mode=body.mode, redirect=body.redirect_uri)
            redirect = body.redirect_uri or connector.redirect_uri()
        else:
            url, state, verifier = connector.start_auth()
            redirect = connector.redirect_uri()
    except PublishError as e:
        raise HTTPException(400, str(e)) from e
    pending.add(PendingAuth(platform=platform, state=state, code_verifier=verifier, redirect_uri=redirect))
    return {"auth_url": url, "state": state, "redirect_uri": redirect,
            "manual_fallback": f"/api/publish/{platform}/code"}


@router.get("/{platform}/callback")
def callback(platform: str, code: str | None = None, state: str | None = None, error: str | None = None,
             error_description: str | None = None) -> HTMLResponse:
    label = service.PLATFORM_LABEL.get(platform, platform)
    if error:
        return _page(f"{label} connection cancelled", error_description or error, ok=False)
    if not code or not state:
        return _page(f"{label} connection failed", "The response was missing the authorisation code.", ok=False)
    item = pending.pop(state)
    if item is None or item.platform != platform:
        return _page(f"{label} connection failed",
                     "This authorisation expired or was not started here. Try connecting again.", ok=False)
    try:
        creds = _connector(platform).exchange_code(code, item.code_verifier, item.redirect_uri)
    except (OAuthError, PublishError) as e:
        return _page(f"{label} connection failed", str(e), ok=False)
    return _page(f"{label} connected", f"ClipForge AI can now publish to <b>{creds.account_name or 'your account'}</b>.")


class CodeBody(BaseModel):
    code: str = Field(..., min_length=4, max_length=2048)
    state: str | None = None


@router.post("/{platform}/code")
def manual_code(platform: str, body: CodeBody) -> dict[str, Any]:
    """Fallback for platforms that will not redirect to 127.0.0.1: paste the code from the URL."""
    item = pending.pop(body.state) if body.state else pending.latest(platform)
    if item is None or item.platform != platform:
        raise HTTPException(400, "Start the connection from Settings first, then paste the code within 15 minutes.")
    code = body.code.strip()
    if "code=" in code:  # tolerate a pasted full redirect URL
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(code).query).get("code", [code])[0]
    try:
        creds = _connector(platform).exchange_code(code, item.code_verifier, item.redirect_uri)
    except (OAuthError, PublishError) as e:
        raise HTTPException(400, str(e)) from e
    return {"connected": True, "account": creds.public()}


@router.post("/{platform}/disconnect")
def disconnect(platform: str) -> dict[str, Any]:
    _connector(platform)
    pending.clear(platform)
    return {"disconnected": store.delete(platform)}


@router.get("/tiktok/creator-info")
def tiktok_creator_info() -> dict[str, Any]:
    creds = store.load("tiktok")
    if creds is None:
        raise HTTPException(400, "TikTok is not connected.")
    try:
        return tiktok.creator_info(creds)
    except PublishError as e:
        raise HTTPException(502, str(e)) from e


@router.get("/options/{candidate_pk}")
def options(candidate_pk: str) -> dict[str, Any]:
    try:
        return service.clip_options(candidate_pk)
    except PublishError as e:
        raise HTTPException(400, str(e)) from e


class Target(BaseModel):
    platform: str
    mode: str = ""
    privacy: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    made_for_kids: bool = False
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False


class PublishBody(BaseModel):
    candidate_pk: str
    variant: str | None = None
    targets: list[Target] = Field(..., min_length=1)
    confirm: bool = False


@router.post("/clip")
def publish_clip(body: PublishBody) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(400, "Publishing must be confirmed explicitly.")
    for t in body.targets:
        if t.platform not in CONNECTORS:
            raise HTTPException(422, f"Unknown platform '{t.platform}'")
    try:
        opts = service.clip_options(body.candidate_pk)
    except PublishError as e:
        raise HTTPException(400, str(e)) from e
    if opts["compliance_blocks_publishing"]:
        raise HTTPException(409, "This clip FAILED campaign compliance, so it cannot be published.")
    project_id = opts["project_id"]
    targets = [t.model_dump() for t in body.targets]
    tracker = JobTracker(project_id, "publish", service.publish_steps(targets))
    payload = {"candidate_pk": body.candidate_pk, "variant": body.variant, "targets": targets}
    try:
        start_job(project_id, tracker, service.run_publish, payload)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return tracker.snapshot()


@router.get("/history")
def publish_history(project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return service.history(project_id, max(1, min(200, limit)))
