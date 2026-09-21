"""TikTok connector: OAuth (PKCE) + Content Posting API.

Two posting modes:
  * inbox  (scope video.upload)  -> the clip lands in the creator's TikTok inbox/drafts and
                                    the user finishes the post in the TikTok app. Works for
                                    unaudited apps.
  * direct (scope video.publish) -> posts straight to the account. Requires an approved app;
                                    unaudited apps can only post SELF_ONLY (private).

  auth    https://www.tiktok.com/v2/auth/authorize/
  token   https://open.tiktokapis.com/v2/oauth/token/
  api     https://open.tiktokapis.com/v2/post/publish/...
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings
from .oauth import OAuthError, build_auth_url, describe_oauth_error, pkce_pair, random_state
from .store import Credentials, save
from .youtube import PublishError

log = logging.getLogger(__name__)

AUTH_ENDPOINT = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_ENDPOINT = "https://open.tiktokapis.com/v2/oauth/token/"
API = "https://open.tiktokapis.com/v2"
CREATOR_INFO = f"{API}/post/publish/creator_info/query/"
INBOX_INIT = f"{API}/post/publish/inbox/video/init/"
DIRECT_INIT = f"{API}/post/publish/video/init/"
STATUS_FETCH = f"{API}/post/publish/status/fetch/"
USER_INFO = f"{API}/user/info/"

SCOPES_INBOX = ("user.info.basic", "video.upload")
SCOPES_DIRECT = ("user.info.basic", "video.publish")
PRIVACY_LEVELS = ("SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE")

# Chunk rules from the Content Posting API: chunks are 5 MB-64 MB, at most 1000 of them, and a
# file smaller than the minimum must be sent whole. The final chunk carries the remainder.
MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
MAX_CHUNKS = 1000
TARGET_CHUNK = 10 * 1024 * 1024


def plan_chunks(size: int, target: int = TARGET_CHUNK) -> tuple[int, int, list[tuple[int, int]]]:
    """Return (chunk_size, total_chunk_count, [(start, end_inclusive), ...])."""
    if size <= 0:
        raise PublishError("The clip file is empty.")
    if size <= MIN_CHUNK:
        return size, 1, [(0, size - 1)]
    chunk = max(MIN_CHUNK, min(target, MAX_CHUNK))
    count = size // chunk
    if count > MAX_CHUNKS:
        chunk = min(MAX_CHUNK, math.ceil(size / MAX_CHUNKS))
        count = max(1, size // chunk)
    ranges: list[tuple[int, int]] = []
    for i in range(count):
        start = i * chunk
        end = size - 1 if i == count - 1 else start + chunk - 1  # last chunk absorbs the remainder
        ranges.append((start, end))
    return chunk, count, ranges


def redirect_uri() -> str:
    return f"{get_settings().oauth_redirect_base}/api/publish/tiktok/callback"


def start_auth(mode: str = "inbox", redirect: str | None = None) -> tuple[str, str, str]:
    s = get_settings()
    if not s.tiktok_configured:
        raise PublishError("TikTok is not configured. Add TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET to .env (see PUBLISHING.md).")
    scopes = SCOPES_DIRECT if mode == "direct" else SCOPES_INBOX
    verifier, challenge = pkce_pair()
    state = random_state()
    url = build_auth_url(AUTH_ENDPOINT, {
        "client_key": s.tiktok_client_key.strip(),
        "scope": ",".join(scopes),
        "response_type": "code",
        "redirect_uri": redirect or redirect_uri(),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return url, state, verifier


def _token_request(data: dict[str, str]) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=60) as client:
            r = client.post(TOKEN_ENDPOINT, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    except httpx.HTTPError as e:
        raise OAuthError(f"Could not reach TikTok to exchange the authorisation code: {type(e).__name__}") from e
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    if r.status_code != 200 or payload.get("error"):
        raise OAuthError(f"TikTok rejected the authorisation: {describe_oauth_error(payload, r.text[:200])}")
    return payload


def exchange_code(code: str, code_verifier: str, redirect: str | None = None) -> Credentials:
    s = get_settings()
    payload = _token_request({
        "client_key": s.tiktok_client_key.strip(),
        "client_secret": s.tiktok_client_secret.strip(),
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect or redirect_uri(),
        "code_verifier": code_verifier,
    })
    creds = Credentials(
        platform="tiktok",
        access_token=str(payload.get("access_token", "")),
        refresh_token=str(payload.get("refresh_token", "")),
        expires_at=datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 86400))),
        scopes=str(payload.get("scope", "")).replace(" ", ",").split(","),
        account_id=str(payload.get("open_id", "")),
        connected_at=datetime.now(UTC),
    )
    fill_account(creds)
    save(creds)
    return creds


def refresh(creds: Credentials) -> Credentials:
    if not creds.refresh_token:
        raise PublishError("The TikTok connection expired. Reconnect in Settings.")
    s = get_settings()
    payload = _token_request({
        "client_key": s.tiktok_client_key.strip(),
        "client_secret": s.tiktok_client_secret.strip(),
        "grant_type": "refresh_token",
        "refresh_token": creds.refresh_token,
    })
    creds.access_token = str(payload.get("access_token", creds.access_token))
    creds.refresh_token = str(payload.get("refresh_token", creds.refresh_token))
    creds.expires_at = datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 86400)))
    save(creds)
    return creds


def ensure_token(creds: Credentials) -> Credentials:
    return refresh(creds) if creds.expired or not creds.access_token else creds


def fill_account(creds: Credentials) -> None:
    try:
        with httpx.Client(timeout=30) as client:
            r = client.get(USER_INFO, params={"fields": "open_id,display_name"},
                           headers={"Authorization": f"Bearer {creds.access_token}"})
        if r.status_code == 200:
            user = (r.json().get("data") or {}).get("user") or {}
            creds.account_name = str(user.get("display_name", ""))
            creds.account_id = str(user.get("open_id", creds.account_id))
    except (httpx.HTTPError, ValueError) as e:  # pragma: no cover - informational only
        log.info("Could not read the TikTok account name: %s", type(e).__name__)


def _post(client: httpx.Client, url: str, creds: Credentials, payload: dict[str, Any]) -> dict[str, Any]:
    r = client.post(url, json=payload, headers={
        "Authorization": f"Bearer {creds.access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    })
    try:
        data = r.json()
    except ValueError:
        data = {}
    err = (data.get("error") or {}) if isinstance(data, dict) else {}
    code = str(err.get("code", "")).lower()
    if r.status_code != 200 or (code and code != "ok"):
        raise _api_error(r.status_code, code, str(err.get("message", ""))[:300])
    return data.get("data") or {}


def _api_error(status: int, code: str, message: str) -> PublishError:
    friendly = {
        "spam_risk_too_many_posts": "TikTok is rate-limiting this account (too many recent posts). Try again later.",
        "spam_risk_user_banned_from_posting": "This TikTok account is currently blocked from posting.",
        "reached_active_user_cap": "TikTok's daily posting cap for this app was reached. Try again tomorrow.",
        "unaudited_client_can_only_post_to_private_accounts": (
            "Your TikTok app is not audited yet, so it can only post privately (SELF_ONLY) or to drafts."),
        "access_token_invalid": "The TikTok connection is no longer valid. Reconnect in Settings.",
        "scope_not_authorized": "This TikTok connection lacks the posting permission. Reconnect and approve it.",
        "file_format_check_failed": "TikTok rejected the video format. Re-render the clip as H.264 MP4.",
        "video_pull_failed": "TikTok could not read the uploaded file. Retry the publish.",
    }.get(code)
    return PublishError(friendly or f"TikTok API error ({status}{'/' + code if code else ''}): {message or 'unknown error'}")


def creator_info(creds: Credentials) -> dict[str, Any]:
    """Required before a direct post: returns nickname and the privacy levels this creator allows."""
    creds = ensure_token(creds)
    with httpx.Client(timeout=60) as client:
        return _post(client, CREATOR_INFO, creds, {})


def build_post_info(title: str, privacy: str, *, disable_comment: bool = False, disable_duet: bool = False,
                    disable_stitch: bool = False, cover_ms: int = 1000) -> dict[str, Any]:
    if privacy not in PRIVACY_LEVELS:
        raise PublishError(f"privacy_level must be one of {', '.join(PRIVACY_LEVELS)}")
    return {
        "title": (title or "")[:2200],
        "privacy_level": privacy,
        "disable_comment": bool(disable_comment),
        "disable_duet": bool(disable_duet),
        "disable_stitch": bool(disable_stitch),
        "video_cover_timestamp_ms": max(0, int(cover_ms)),
    }


def publish_video(creds: Credentials, file_path: str | Path, *, mode: str = "inbox",
                  post_info: dict[str, Any] | None = None,
                  on_progress: Callable[[float, str], None] | None = None,
                  sleep: Callable[[float], None] = time.sleep, poll_seconds: int = 5,
                  max_polls: int = 60) -> dict[str, Any]:
    """Upload a clip. mode='inbox' -> drafts; mode='direct' -> posts (needs video.publish)."""
    path = Path(file_path)
    if not path.is_file():
        raise PublishError(f"Clip file not found: {path.name}")
    size = path.stat().st_size
    chunk_size, count, ranges = plan_chunks(size)
    creds = ensure_token(creds)
    source_info = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": count}
    payload: dict[str, Any] = {"source_info": source_info}
    if mode == "direct":
        payload["post_info"] = post_info or build_post_info("", "SELF_ONLY")

    with httpx.Client(timeout=httpx.Timeout(60, read=300, write=300)) as client:
        data = _post(client, DIRECT_INIT if mode == "direct" else INBOX_INIT, creds, payload)
        publish_id = str(data.get("publish_id", ""))
        upload_url = str(data.get("upload_url", ""))
        if not upload_url or not publish_id:
            raise PublishError("TikTok did not return an upload URL.")

        with path.open("rb") as f:
            for i, (start, end) in enumerate(ranges, 1):
                f.seek(start)
                blob = f.read(end - start + 1)
                attempts = 0
                while True:
                    try:
                        r = client.put(upload_url, content=blob, headers={
                            "Content-Type": "video/mp4",
                            "Content-Length": str(len(blob)),
                            "Content-Range": f"bytes {start}-{end}/{size}",
                        })
                    except httpx.HTTPError as e:
                        attempts += 1
                        if attempts > 5:
                            raise PublishError(f"Upload to TikTok was interrupted ({type(e).__name__}). Retry later.") from e
                        sleep(min(30, 2 ** attempts))
                        continue
                    if r.status_code in (200, 201, 206, 308):
                        break
                    attempts += 1
                    if r.status_code in (500, 502, 503, 504) and attempts <= 5:
                        sleep(min(30, 2 ** attempts))
                        continue
                    raise PublishError(f"TikTok rejected the upload ({r.status_code}): {r.text[:160]}")
                if on_progress:
                    on_progress(min(0.95, (end + 1) / size), f"Uploading chunk {i}/{count}")

        status: dict[str, Any] = {}
        for _ in range(max_polls):
            sleep(poll_seconds)
            status = _post(client, STATUS_FETCH, creds, {"publish_id": publish_id})
            state = str(status.get("status", "")).upper()
            if state in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                break
            if state == "FAILED":
                reason = str(status.get("fail_reason", "")) or "unknown reason"
                raise PublishError(f"TikTok could not process the video: {reason}")
            if on_progress:
                on_progress(0.97, f"TikTok is processing the video ({state.lower() or 'pending'})")

    state = str(status.get("status", "")).upper()
    public_ids = status.get("publicaly_available_post_id") or status.get("publicly_available_post_id") or []
    url = f"https://www.tiktok.com/video/{public_ids[0]}" if public_ids else ""
    if on_progress:
        on_progress(1.0, "Sent to TikTok drafts" if mode == "inbox" else "Posted to TikTok")
    return {"id": publish_id, "url": url, "status": state or "UNKNOWN", "mode": mode,
            "needs_user_action": mode == "inbox"}
