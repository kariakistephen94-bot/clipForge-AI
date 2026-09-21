"""OAuth 2.0 helpers (PKCE, state, pending-authorisation registry).

Both connectors use the authorization-code flow with PKCE. The browser is sent to the
platform; the platform redirects back to the local backend (or, when a platform refuses
loopback redirect URIs, the user pastes the code manually).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode


def random_state(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_auth_url(endpoint: str, params: dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return f"{endpoint}?{urlencode(clean)}"


@dataclass
class PendingAuth:
    platform: str
    state: str
    code_verifier: str
    redirect_uri: str
    created_at: float = field(default_factory=time.monotonic)
    result: dict[str, Any] | None = None

    @property
    def age(self) -> float:
        return time.monotonic() - self.created_at


class PendingRegistry:
    """Short-lived in-memory store for in-flight authorisations (never persisted)."""

    TTL = 15 * 60

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, PendingAuth] = {}

    def add(self, pending: PendingAuth) -> None:
        with self._lock:
            self._prune()
            self._items[pending.state] = pending

    def pop(self, state: str) -> PendingAuth | None:
        with self._lock:
            self._prune()
            return self._items.pop(state, None)

    def latest(self, platform: str) -> PendingAuth | None:
        """Used by the manual code-paste flow, where the state isn't echoed back to us."""
        with self._lock:
            self._prune()
            items = [p for p in self._items.values() if p.platform == platform]
            return max(items, key=lambda p: p.created_at) if items else None

    def clear(self, platform: str) -> None:
        with self._lock:
            for state in [s for s, p in self._items.items() if p.platform == platform]:
                del self._items[state]

    def _prune(self) -> None:
        for state in [s for s, p in self._items.items() if p.age > self.TTL]:
            del self._items[state]


pending = PendingRegistry()


class OAuthError(RuntimeError):
    """Raised with a message safe to show in the UI (never contains tokens)."""


def describe_oauth_error(payload: dict[str, Any], fallback: str = "") -> str:
    for key in ("error_description", "error_summary", "message", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:300]
        if isinstance(value, dict):
            inner = value.get("message") or value.get("code")
            if isinstance(inner, str) and inner:
                return inner[:300]
    return fallback or "authorisation failed"
