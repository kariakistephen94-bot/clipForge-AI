"""YouTube connector: OAuth (PKCE, loopback redirect) + resumable upload to the user's own channel.

Uses the documented HTTP API directly (no extra Google client libraries):
  auth    https://accounts.google.com/o/oauth2/v2/auth
  token   https://oauth2.googleapis.com/token
  upload  https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings
from .oauth import OAuthError, build_auth_url, describe_oauth_error, pkce_pair, random_state
from .store import Credentials, save

log = logging.getLogger(__name__)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"
CHANNELS_ENDPOINT = "https://www.googleapis.com/youtube/v3/channels"
SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly")
CHUNK = 8 * 1024 * 1024  # multiple of 256 KiB, as required for resumable chunks
PRIVACY = ("private", "unlisted", "public")
# YouTube treats a vertical video of this length or less as a Short.
SHORTS_MAX_SECONDS = 180


class PublishError(RuntimeError):
    """Message is safe to show in the UI."""


def redirect_uri() -> str:
    return f"{get_settings().oauth_redirect_base}/api/publish/youtube/callback"


def start_auth() -> tuple[str, str, str]:
    """Return (auth_url, state, code_verifier)."""
    s = get_settings()
    if not s.youtube_configured:
        raise PublishError("YouTube is not configured. Add YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET to .env (see PUBLISHING.md).")
    verifier, challenge = pkce_pair()
    state = random_state()
    url = build_auth_url(AUTH_ENDPOINT, {
        "client_id": s.youtube_client_id.strip(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return url, state, verifier


def _token_request(data: dict[str, str]) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=60) as client:
            r = client.post(TOKEN_ENDPOINT, data=data)
    except httpx.HTTPError as e:
        raise OAuthError(f"Could not reach Google to exchange the authorisation code: {type(e).__name__}") from e
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    if r.status_code != 200:
        raise OAuthError(f"Google rejected the authorisation: {describe_oauth_error(payload, r.text[:200])}")
    return payload


def exchange_code(code: str, code_verifier: str, redirect: str | None = None) -> Credentials:
    s = get_settings()
    payload = _token_request({
        "client_id": s.youtube_client_id.strip(),
        "client_secret": s.youtube_client_secret.strip(),
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect or redirect_uri(),
    })
    if not payload.get("refresh_token"):
        log.warning("Google returned no refresh token; the connection will expire in about an hour")
    creds = Credentials(
        platform="youtube",
        access_token=str(payload.get("access_token", "")),
        refresh_token=str(payload.get("refresh_token", "")),
        expires_at=datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 3600))),
        scopes=str(payload.get("scope", "")).split(),
        connected_at=datetime.now(UTC),
    )
    fill_account(creds)
    save(creds)
    return creds


def refresh(creds: Credentials) -> Credentials:
    if not creds.refresh_token:
        raise PublishError("The YouTube connection expired and has no refresh token. Reconnect in Settings.")
    s = get_settings()
    payload = _token_request({
        "client_id": s.youtube_client_id.strip(),
        "client_secret": s.youtube_client_secret.strip(),
        "refresh_token": creds.refresh_token,
        "grant_type": "refresh_token",
    })
    creds.access_token = str(payload.get("access_token", creds.access_token))
    creds.expires_at = datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 3600)))
    save(creds)
    return creds


def ensure_token(creds: Credentials) -> Credentials:
    return refresh(creds) if creds.expired or not creds.access_token else creds


def fill_account(creds: Credentials) -> None:
    """Best-effort channel name (1 quota unit); failure never blocks connecting."""
    try:
        with httpx.Client(timeout=30) as client:
            r = client.get(CHANNELS_ENDPOINT, params={"part": "snippet", "mine": "true"},
                           headers={"Authorization": f"Bearer {creds.access_token}"})
        if r.status_code == 200:
            items = r.json().get("items") or []
            if items:
                creds.account_id = str(items[0].get("id", ""))
                creds.account_name = str((items[0].get("snippet") or {}).get("title", ""))
    except (httpx.HTTPError, ValueError) as e:  # pragma: no cover - informational only
        log.info("Could not read the YouTube channel name: %s", type(e).__name__)


def build_metadata(title: str, description: str, tags: list[str], privacy: str, made_for_kids: bool = False,
                   category_id: str = "22") -> dict[str, Any]:
    """videos.insert body. Title/description limits are YouTube's own."""
    if privacy not in PRIVACY:
        raise PublishError(f"privacy must be one of {', '.join(PRIVACY)}")
    clean_tags: list[str] = []
    total = 0
    for t in tags:
        t = t.strip().lstrip("#")
        if not t:
            continue
        total += len(t) + 1
        if total > 480:  # YouTube caps the tags list at 500 characters
            break
        clean_tags.append(t)
    return {
        "snippet": {
            "title": (title or "Untitled clip").replace("<", "").replace(">", "")[:100],
            "description": (description or "")[:5000],
            "tags": clean_tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": bool(made_for_kids),
        },
    }


def _api_error(status: int, body: str) -> PublishError:
    reason = ""
    try:
        err = json.loads(body).get("error", {})
        reason = (err.get("errors") or [{}])[0].get("reason", "") or err.get("status", "")
        message = err.get("message", "")
    except (ValueError, AttributeError, IndexError):
        message = body[:200]
    friendly = {
        "quotaExceeded": "YouTube API quota for today is used up. Uploads reset at midnight Pacific time.",
        "uploadLimitExceeded": "This channel has reached its daily upload limit. Try again tomorrow.",
        "youtubeSignupRequired": "This Google account has no YouTube channel yet. Create one, then reconnect.",
        "forbidden": "YouTube refused the upload for this account (check the channel is in good standing).",
        "authError": "The YouTube connection is no longer valid. Reconnect in Settings.",
        "invalidVideoMetadata": "YouTube rejected the title/description/tags. Shorten them and retry.",
    }.get(reason)
    return PublishError(friendly or f"YouTube upload failed ({status}): {reason or message or 'unknown error'}")


def upload_video(creds: Credentials, file_path: str | Path, metadata: dict[str, Any],
                 on_progress: Callable[[float, str], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Resumable upload. Returns {'id', 'url', 'privacy'}. Retries 5xx/308 gaps, resumes from the server offset."""
    path = Path(file_path)
    if not path.is_file():
        raise PublishError(f"Clip file not found: {path.name}")
    size = path.stat().st_size
    creds = ensure_token(creds)
    headers = {
        "Authorization": f"Bearer {creds.access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(size),
        "X-Upload-Content-Type": "video/mp4",
    }
    with httpx.Client(timeout=httpx.Timeout(60, read=300, write=300)) as client:
        r = client.post(UPLOAD_ENDPOINT, params={"uploadType": "resumable", "part": "snippet,status"},
                        headers=headers, json=metadata)
        if r.status_code == 401:
            creds = refresh(creds)
            headers["Authorization"] = f"Bearer {creds.access_token}"
            r = client.post(UPLOAD_ENDPOINT, params={"uploadType": "resumable", "part": "snippet,status"},
                            headers=headers, json=metadata)
        if r.status_code not in (200, 201):
            raise _api_error(r.status_code, r.text)
        session_url = r.headers.get("Location")
        if not session_url:
            raise PublishError("YouTube did not return an upload session URL.")

        offset = 0
        attempts = 0
        with path.open("rb") as f:
            while offset < size:
                f.seek(offset)
                chunk = f.read(CHUNK)
                end = offset + len(chunk) - 1
                try:
                    resp = client.put(session_url, content=chunk, headers={
                        "Authorization": f"Bearer {creds.access_token}",
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {offset}-{end}/{size}",
                    })
                except httpx.HTTPError as e:
                    attempts += 1
                    if attempts > 6:
                        raise PublishError(f"Upload interrupted repeatedly ({type(e).__name__}). Retry later.") from e
                    sleep(min(30, 2 ** attempts))
                    offset = _server_offset(client, session_url, creds, size, offset)
                    continue
                if resp.status_code in (200, 201):
                    data = resp.json()
                    vid = str(data.get("id", ""))
                    if on_progress:
                        on_progress(1.0, "Upload complete")
                    return {"id": vid, "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
                            "privacy": (data.get("status") or {}).get("privacyStatus", "")}
                if resp.status_code == 308:
                    attempts = 0
                    rng = resp.headers.get("Range")
                    offset = int(rng.split("-")[-1]) + 1 if rng else offset + len(chunk)
                    if on_progress:
                        on_progress(offset / size, f"Uploading {offset / 1024**2:.0f}/{size / 1024**2:.0f} MB")
                    continue
                if resp.status_code == 404:
                    raise PublishError("The upload session expired. Start the publish again.")
                if resp.status_code in (500, 502, 503, 504):
                    attempts += 1
                    if attempts > 6:
                        raise _api_error(resp.status_code, resp.text)
                    sleep(min(30, 2 ** attempts))
                    offset = _server_offset(client, session_url, creds, size, offset)
                    continue
                if resp.status_code == 401:
                    creds = refresh(creds)
                    continue
                raise _api_error(resp.status_code, resp.text)
    raise PublishError("Upload ended unexpectedly without confirmation from YouTube.")


def _server_offset(client: httpx.Client, session_url: str, creds: Credentials, size: int, fallback: int) -> int:
    """Ask YouTube how many bytes it already has (empty PUT with Content-Range: bytes */SIZE)."""
    try:
        r = client.put(session_url, headers={
            "Authorization": f"Bearer {creds.access_token}",
            "Content-Length": "0",
            "Content-Range": f"bytes */{size}",
        })
    except httpx.HTTPError:
        return fallback
    if r.status_code == 308:
        rng = r.headers.get("Range")
        return int(rng.split("-")[-1]) + 1 if rng else 0
    if r.status_code in (200, 201):
        return size
    return fallback


def is_shorts_eligible(duration: float, width: int, height: int) -> bool:
    return duration <= SHORTS_MAX_SECONDS and height >= width
