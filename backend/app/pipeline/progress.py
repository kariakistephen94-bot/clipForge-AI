"""Live job progress (in-memory for SSE, persisted to SQLite for history/restarts)."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from ..db import session_scope
from ..models import Job

log = logging.getLogger(__name__)

_lock = threading.Lock()
_trackers: dict[str, JobTracker] = {}
_running: set[str] = set()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-job")


class JobTracker:
    def __init__(self, project_id: str, kind: str, steps: list[tuple[str, str]]):
        self.project_id = project_id
        self.kind = kind
        self.steps: list[dict[str, Any]] = [
            {"key": k, "label": label, "status": "pending", "progress": None, "detail": ""} for k, label in steps
        ]
        self.status = "queued"
        self.message = ""
        self.error: str | None = None
        self.version = 0
        self._last_persist = 0.0
        with session_scope() as s:
            job = Job(project_id=project_id, kind=kind, status="queued", steps=self.steps)
            s.add(job)
            s.flush()
            self.job_id = job.id

    def _find(self, key: str) -> dict[str, Any]:
        for st in self.steps:
            if st["key"] == key:
                return st
        st = {"key": key, "label": key, "status": "pending", "progress": None, "detail": ""}
        self.steps.append(st)
        return st

    def add_step(self, key: str, label: str) -> None:
        with _lock:
            st = self._find(key)
            st["label"] = label
            self.version += 1

    def step(self, key: str, status: str | None = None, progress: float | None = None, detail: str | None = None,
             label: str | None = None) -> None:
        with _lock:
            st = self._find(key)
            changed_status = status is not None and status != st["status"]
            if status is not None:
                st["status"] = status
                if status == "done":
                    st["progress"] = 1.0
            if progress is not None:
                st["progress"] = round(max(0.0, min(1.0, progress)), 3)
            if detail is not None:
                st["detail"] = detail
            if label is not None:
                st["label"] = label
            if status == "running":
                self.status = "running"
            self.version += 1
        self._persist(force=changed_status)

    def set_message(self, text: str) -> None:
        with _lock:
            self.message = text
            self.version += 1
        self._persist()

    def finish(self, status: str, error: str | None = None, message: str | None = None) -> None:
        with _lock:
            self.status = status
            self.error = error
            if message is not None:
                self.message = message
            for st in self.steps:
                if st["status"] == "running":
                    st["status"] = "error" if status == "error" else "done"
            self.version += 1
        self._persist(force=True, finished=True)

    def snapshot(self) -> dict[str, Any]:
        with _lock:
            return {"job_id": self.job_id, "project_id": self.project_id, "kind": self.kind, "status": self.status,
                    "message": self.message, "error": self.error, "steps": [dict(s) for s in self.steps],
                    "version": self.version}

    def _persist(self, force: bool = False, finished: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_persist < 1.5:
            return
        self._last_persist = now
        snap = self.snapshot()
        try:
            with session_scope() as s:
                job = s.get(Job, self.job_id)
                if job:
                    job.status, job.steps, job.message, job.error = snap["status"], snap["steps"], snap["message"], snap["error"]
                    if finished:
                        job.finished_at = datetime.now(UTC)
        except Exception as e:  # noqa: BLE001 - progress persistence must never kill a job
            log.warning("Could not persist job progress: %s", e)


def current_snapshot(project_id: str) -> dict[str, Any] | None:
    with _lock:
        tr = _trackers.get(project_id)
    if tr is not None:
        return tr.snapshot()
    with session_scope() as s:
        job = s.query(Job).filter(Job.project_id == project_id).order_by(Job.created_at.desc()).first()
        if not job:
            return None
        return {"job_id": job.id, "project_id": project_id, "kind": job.kind, "status": job.status, "message": job.message,
                "error": job.error, "steps": job.steps, "version": 0}


def is_running(project_id: str) -> bool:
    with _lock:
        return project_id in _running


def start_job(project_id: str, tracker: JobTracker, fn, *args: Any) -> None:
    with _lock:
        if project_id in _running:
            raise RuntimeError("A job is already running for this project.")
        _running.add(project_id)
        _trackers[project_id] = tracker

    def runner() -> None:
        try:
            fn(project_id, tracker, *args)
            if tracker.status not in ("done", "error"):
                tracker.finish("done")
        except Exception as e:  # noqa: BLE001 - convert every failure into a visible job error
            log.exception("Job %s failed", tracker.job_id)
            tracker.finish("error", error=str(e) or type(e).__name__)
        finally:
            with _lock:
                _running.discard(project_id)

    _executor.submit(runner)


def mark_interrupted_jobs() -> None:
    from ..models import Project

    msg = "Interrupted: the server stopped while this job was running. Start it again."
    with session_scope() as s:
        for job in s.query(Job).filter(Job.status.in_(["queued", "running"])).all():
            job.status = "error"
            job.error = msg
        for p in s.query(Project).filter(Project.status.in_(["analyzing", "rendering"])).all():
            p.status, p.error = "error", msg
