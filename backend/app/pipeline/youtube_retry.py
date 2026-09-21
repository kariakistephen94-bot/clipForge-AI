"""Automatic, polite retries for YouTube downloads blocked by YouTube's "confirm you're not a bot" check.

The check applies to the whole network, so the state is network-wide: while YouTube is blocking this
connection, new YouTube downloads wait instead of adding more requests (which tends to prolong the block).
When the wait is over, ONE waiting project tries again. If that works, the others follow; if not, the wait
doubles (30 min, 1 h, 2 h, ... capped at 6 h). Nothing here signs in, uses cookies or changes the network.
State is a small JSON file in the workspace so waiting projects survive restarts.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..db import session_scope
from ..models import Project
from .progress import JobTracker, is_running, start_job

log = logging.getLogger(__name__)

FIRST_WAIT = timedelta(minutes=30)
MAX_WAIT = timedelta(hours=6)
GIVE_UP_AFTER = timedelta(hours=48)
CHECK_EVERY_S = 30

_lock = threading.RLock()


def _now() -> datetime:
    return datetime.now(UTC)


def _state_path() -> Path:
    return get_settings().workspace / "youtube_retry.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("blocked_until", None)
    data.setdefault("wait_s", 0)
    data.setdefault("attempts", 0)
    data.setdefault("blocked_since", None)
    data.setdefault("projects", {})
    return data


def _save(data: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def blocked_until(now: datetime | None = None) -> datetime | None:
    """When the next YouTube attempt is allowed, or None if YouTube isn't known to be blocking us."""
    with _lock:
        until = _parse(_load()["blocked_until"])
    return until if until and until > (now or _now()) else None


def record_block(project_id: str, opts: dict[str, Any], attempted: bool, now: datetime | None = None) -> datetime:
    """Queue a project whose YouTube download can't happen yet. Returns the next attempt time.

    attempted=True: YouTube just refused a real request, so the network-wide wait grows.
    attempted=False: the download was held back because a wait is already in effect.
    """
    now = now or _now()
    with _lock:
        data = _load()
        until = _parse(data["blocked_until"])
        entry = data["projects"].setdefault(project_id, {"since": now.isoformat()})
        entry["opts"] = {k: v for k, v in opts.items() if not k.startswith("_") and k != "force_youtube"}
        if attempted or not (until and until > now):
            wait = timedelta(seconds=data["wait_s"]) * 2 if data["wait_s"] else FIRST_WAIT
            wait = min(wait, MAX_WAIT)
            data["wait_s"] = int(wait.total_seconds())
            data["attempts"] += 1
            data["blocked_since"] = data["blocked_since"] or now.isoformat()
            until = now + wait
            data["blocked_until"] = until.isoformat()
        _save(data)
        assert until is not None
        return until


def record_success() -> list[str]:
    """A YouTube download worked: clear the block. Returns the projects that were waiting."""
    with _lock:
        data = _load()
        waiting = list(data["projects"])
        if data["blocked_until"] or data["attempts"]:
            log.info("YouTube downloads work again; %d waiting project(s) will start", len(waiting))
        data.update(blocked_until=None, wait_s=0, attempts=0, blocked_since=None)
        _save(data)
    return waiting


def cancel(project_id: str) -> bool:
    with _lock:
        data = _load()
        found = data["projects"].pop(project_id, None) is not None
        if not data["projects"] and not blocked_until():
            data.update(wait_s=0, attempts=0, blocked_since=None)
        _save(data)
    return found


def waiting_info(project_id: str) -> dict[str, Any] | None:
    with _lock:
        data = _load()
    entry = data["projects"].get(project_id)
    if entry is None:
        return None
    return {"retry_at": data["blocked_until"], "attempts": data["attempts"], "since": entry["since"]}


def due_projects(now: datetime | None = None) -> tuple[list[tuple[str, dict[str, Any]]], bool]:
    """(projects to start now, whether this is a single probe while still blocked)."""
    now = now or _now()
    with _lock:
        data = _load()
        if not data["projects"]:
            return [], False
        until = _parse(data["blocked_until"])
        ordered = sorted(data["projects"].items(), key=lambda kv: kv[1]["since"])
        if until and until > now:
            return [], False
        since = _parse(data["blocked_since"])
        if since and now - since > GIVE_UP_AFTER:
            return [(pid, {"_give_up": True}) for pid, _ in ordered], False
        probing = until is not None
        picked = ordered[:1] if probing else ordered
        return [(pid, dict(e.get("opts") or {})) for pid, e in picked], probing


GAVE_UP = ("YouTube kept blocking this connection for two days, so ClipForge stopped retrying automatically. "
           "Click Analyze to try again, or upload the file / paste a Drive or Dropbox link instead.")


def tick(starter: Callable[[str, dict[str, Any]], None] | None = None, now: datetime | None = None) -> list[str]:
    """Start whatever is due. Returns the started project ids."""
    started: list[str] = []
    due, probing = due_projects(now)
    for pid, opts in due:
        if is_running(pid):
            continue
        if opts.pop("_give_up", False):
            cancel(pid)
            with session_scope() as s:
                p = s.get(Project, pid)
                if p and p.status == "waiting":
                    p.status, p.error = "error", GAVE_UP
            continue
        with session_scope() as s:
            p = s.get(Project, pid)
            if p is None or p.status != "waiting":
                cancel(pid)
                continue
        with _lock:  # the attempt is now in flight; it re-registers itself if YouTube still refuses
            data = _load()
            data["projects"].pop(pid, None)
            if probing:
                # hold everyone else back until this probe reports back
                data["blocked_until"] = (_now() + timedelta(minutes=10)).isoformat()
            _save(data)
        opts["_scheduled"] = True
        (starter or _start_analysis)(pid, opts)
        started.append(pid)
    return started


def _start_analysis(project_id: str, opts: dict[str, Any]) -> None:
    from .analyze import run_analysis

    log.info("Retrying YouTube download for project %s", project_id)
    try:
        start_job(project_id, JobTracker(project_id, "analyze", []), run_analysis, opts)
    except RuntimeError:  # something is already running for this project; it reports its own outcome
        log.info("Project %s is busy; skipping its scheduled YouTube retry", project_id)


_thread: threading.Thread | None = None


def start_scheduler() -> None:
    global _thread
    if _thread is not None:
        return
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(CHECK_EVERY_S):
            try:
                tick()
            except Exception:  # noqa: BLE001 - the scheduler must keep running
                log.exception("YouTube retry scheduler error")

    _thread = threading.Thread(target=loop, name="clipforge-youtube-retry", daemon=True)
    _thread.start()
