"""Credential storage for publishing connectors.

OAuth tokens are secrets, so they are kept OUT of SQLite and out of the API surface:
one 0600 JSON file per platform under workspace/credentials/. Only non-secret connection
metadata (account name, scopes, expiry) is ever returned to the browser.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..paths import workspace

log = logging.getLogger(__name__)

PLATFORMS = ("youtube", "tiktok")
SECRET_FIELDS = ("access_token", "refresh_token", "client_secret", "code", "code_verifier")


class CredentialError(RuntimeError):
    pass


def credentials_dir() -> Path:
    p = workspace() / "credentials"
    p.mkdir(parents=True, exist_ok=True)
    try:
        p.chmod(0o700)
    except OSError:  # pragma: no cover - non-POSIX filesystems
        pass
    return p


def _path(platform: str) -> Path:
    if platform not in PLATFORMS:
        raise CredentialError(f"Unknown platform '{platform}'")
    return credentials_dir() / f"{platform}.json"


@dataclass
class Credentials:
    platform: str
    access_token: str = ""
    refresh_token: str = ""
    expires_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)
    account_name: str = ""
    account_id: str = ""
    connected_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.now(UTC) >= self.expires_at - timedelta(minutes=2)

    def public(self) -> dict[str, Any]:
        """Safe for the API/browser: no tokens."""
        return {
            "platform": self.platform,
            "connected": bool(self.access_token or self.refresh_token),
            "account_name": self.account_name,
            "account_id": self.account_id[:6] + "…" if self.account_id else "",
            "scopes": self.scopes,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "needs_refresh": self.expired,
            "extra": {k: v for k, v in self.extra.items() if k not in SECRET_FIELDS},
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "scopes": self.scopes,
            "account_name": self.account_name,
            "account_id": self.account_id,
            "connected_at": (self.connected_at or datetime.now(UTC)).isoformat(),
            "extra": self.extra,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Credentials:
        def dt(value: Any) -> datetime | None:
            if not value:
                return None
            d = datetime.fromisoformat(str(value))
            return d if d.tzinfo else d.replace(tzinfo=UTC)

        return cls(
            platform=str(data.get("platform", "")),
            access_token=str(data.get("access_token", "")),
            refresh_token=str(data.get("refresh_token", "")),
            expires_at=dt(data.get("expires_at")),
            scopes=list(data.get("scopes") or []),
            account_name=str(data.get("account_name", "")),
            account_id=str(data.get("account_id", "")),
            connected_at=dt(data.get("connected_at")),
            extra=dict(data.get("extra") or {}),
        )


def save(creds: Credentials) -> None:
    path = _path(creds.platform)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(creds.to_json(), indent=1), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:  # pragma: no cover
        pass
    os.replace(tmp, path)
    log.info("Stored %s credentials for %s (tokens are not logged)", creds.platform, creds.account_name or "account")


def load(platform: str) -> Credentials | None:
    path = _path(platform)
    if not path.exists():
        return None
    try:
        return Credentials.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, ValueError) as e:
        log.warning("Could not read %s credentials: %s", platform, e)
        return None


def delete(platform: str) -> bool:
    path = _path(platform)
    if path.exists():
        path.unlink()
        log.info("Disconnected %s (credentials file removed)", platform)
        return True
    return False


def status() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for platform in PLATFORMS:
        creds = load(platform)
        out[platform] = creds.public() if creds else {"platform": platform, "connected": False}
    return out
