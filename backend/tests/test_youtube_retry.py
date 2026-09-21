from datetime import UTC, datetime, timedelta

import pytest

from app.db import session_scope
from app.models import Project, SourceVideo
from app.pipeline import analyze, youtube_retry
from app.pipeline.progress import JobTracker
from app.services.ingest import YOUTUBE_BOT_CHECK, YouTubeBlockedError

T0 = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean_state():
    youtube_retry._state_path().unlink(missing_ok=True)
    yield
    youtube_retry._state_path().unlink(missing_ok=True)


def _project(status: str = "waiting") -> str:
    with session_scope() as s:
        p = Project(name="yt", platforms=["tiktok"], status=status)
        s.add(p)
        s.flush()
        s.add(SourceVideo(project_id=p.id, origin="youtube", source_url="https://youtu.be/abcdefghijk",
                          original_filename="YouTube video"))
        return p.id


def test_wait_doubles_on_refusals_and_is_capped():
    waits = []
    now = T0
    for _ in range(7):
        until = youtube_retry.record_block("p1", {}, attempted=True, now=now)
        waits.append(until - now)
        now = until
    assert waits[:5] == [timedelta(minutes=m) for m in (30, 60, 120, 240, 360)]
    assert waits[5:] == [timedelta(hours=6)] * 2


def test_held_back_projects_do_not_extend_the_wait():
    first = youtube_retry.record_block("p1", {"whisper_model": "base", "_scheduled": True}, attempted=True, now=T0)
    later = youtube_retry.record_block("p2", {}, attempted=False, now=T0 + timedelta(minutes=5))
    assert later == first
    assert youtube_retry.waiting_info("p2")["retry_at"] == first.isoformat()
    # internal flags are not persisted, user options are
    assert youtube_retry._load()["projects"]["p1"]["opts"] == {"whisper_model": "base"}


def test_one_probe_while_blocked_then_everyone_after_success():
    a, b = _project(), _project()
    youtube_retry.record_block(a, {}, attempted=True, now=T0)
    youtube_retry.record_block(b, {}, attempted=False, now=T0 + timedelta(seconds=1))
    started: list[str] = []
    starter = lambda pid, opts: started.append((pid, opts))  # noqa: E731

    assert youtube_retry.tick(starter, now=T0 + timedelta(minutes=29)) == []
    assert youtube_retry.tick(starter, now=T0 + timedelta(minutes=31)) == [a]  # oldest probes alone
    assert started == [(a, {"_scheduled": True})]
    assert youtube_retry.tick(starter, now=datetime.now(UTC) + timedelta(minutes=1)) == []  # b held during probe

    youtube_retry.record_success()
    assert youtube_retry.tick(starter) == [b]
    assert youtube_retry._load()["projects"] == {}


def test_projects_no_longer_waiting_are_dropped():
    pid = _project(status="ready")
    youtube_retry.record_block(pid, {}, attempted=True, now=T0)
    assert youtube_retry.tick(lambda *_: None, now=T0 + timedelta(hours=1)) == []
    assert youtube_retry.waiting_info(pid) is None


def test_gives_up_after_two_days():
    pid = _project()
    now = T0
    while now - T0 < timedelta(hours=49):
        now = youtube_retry.record_block(pid, {}, attempted=True, now=now)
    assert youtube_retry.tick(lambda *_: pytest.fail("should not start"), now=now + timedelta(seconds=1)) == []
    with session_scope() as s:
        p = s.get(Project, pid)
        assert p.status == "error" and "two days" in p.error


def test_pipeline_waits_instead_of_failing(monkeypatch):
    calls = []

    def blocked(url, dest, on_progress=None):
        calls.append(url)
        raise YouTubeBlockedError(YOUTUBE_BOT_CHECK)

    monkeypatch.setattr(analyze, "download_youtube", blocked)
    a, b = _project("created"), _project("created")

    tr = JobTracker(a, "analyze", [])
    analyze.run_analysis(a, tr, {"whisper_model": "tiny"})
    assert tr.status == "error" and "try again automatically" in tr.error
    info = youtube_retry.waiting_info(a)
    assert info and info["attempts"] == 1

    # a second project doesn't hit YouTube while the block is in effect
    analyze.run_analysis(b, JobTracker(b, "analyze", []), {})
    assert len(calls) == 1
    # ...unless the user explicitly asks to try now
    analyze.run_analysis(b, JobTracker(b, "analyze", []), {"force_youtube": True})
    assert len(calls) == 2
    with session_scope() as s:
        assert {s.get(Project, a).status, s.get(Project, b).status} == {"waiting"}
        assert s.get(Project, a).error is None
