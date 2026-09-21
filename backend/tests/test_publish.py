import base64
import hashlib
import json
import os
import stat
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.db import session_scope
from app.models import Candidate, ExportRecord, Project
from app.publish import service, store, tiktok, youtube
from app.publish.oauth import PendingAuth, PendingRegistry, build_auth_url, pkce_pair
from app.publish.youtube import PublishError

# --------------------------------------------------------------------------- oauth helpers

def test_pkce_pair_is_valid_s256():
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected and "=" not in challenge
    assert pkce_pair()[0] != verifier  # fresh each time


def test_build_auth_url_drops_empty_params():
    url = build_auth_url("https://x.test/auth", {"a": "1", "b": None, "c": "", "d": "x y"})
    assert url.startswith("https://x.test/auth?") and "b=" not in url and "c=" not in url and "d=x+y" in url


def test_pending_registry_roundtrip_and_expiry():
    reg = PendingRegistry()
    item = PendingAuth(platform="tiktok", state="s1", code_verifier="v", redirect_uri="r")
    reg.add(item)
    assert reg.latest("tiktok") is item and reg.latest("youtube") is None
    assert reg.pop("s1") is item and reg.pop("s1") is None
    old = PendingAuth(platform="youtube", state="s2", code_verifier="v", redirect_uri="r", created_at=-10_000)
    reg.add(old)
    assert reg.pop("s2") is None  # pruned as too old


# --------------------------------------------------------------------------- credential storage

def test_credentials_saved_privately_and_never_exposed():
    creds = store.Credentials(platform="youtube", access_token="AT-secret", refresh_token="RT-secret",
                              expires_at=datetime.now(UTC) + timedelta(hours=1), scopes=["s"],
                              account_name="My Channel", account_id="UC123456789")
    store.save(creds)
    path = store.credentials_dir() / "youtube.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    public = json.dumps(store.status())
    assert "AT-secret" not in public and "RT-secret" not in public
    assert "My Channel" in public
    loaded = store.load("youtube")
    assert loaded and loaded.access_token == "AT-secret" and not loaded.expired
    assert store.delete("youtube") and store.load("youtube") is None


def test_expiry_detection():
    c = store.Credentials(platform="tiktok", access_token="a", expires_at=datetime.now(UTC) + timedelta(seconds=30))
    assert c.expired  # inside the 2-minute safety margin
    c.expires_at = datetime.now(UTC) + timedelta(hours=2)
    assert not c.expired


# --------------------------------------------------------------------------- youtube

def test_build_metadata_limits_and_validation():
    meta = youtube.build_metadata("T" * 300, "D" * 6000, ["#one", "two", "", "x" * 600], "unlisted", made_for_kids=True)
    assert len(meta["snippet"]["title"]) == 100 and len(meta["snippet"]["description"]) == 5000
    assert meta["snippet"]["tags"][:2] == ["one", "two"]
    assert sum(len(t) + 1 for t in meta["snippet"]["tags"]) <= 500
    assert meta["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": True}
    with pytest.raises(PublishError):
        youtube.build_metadata("t", "d", [], "everyone")


def test_api_error_messages_are_actionable():
    err = youtube._api_error(403, json.dumps({"error": {"errors": [{"reason": "quotaExceeded"}], "message": "x"}}))
    assert "quota" in str(err).lower()
    err = youtube._api_error(403, json.dumps({"error": {"errors": [{"reason": "youtubeSignupRequired"}]}}))
    assert "channel" in str(err).lower()
    assert "500" in str(youtube._api_error(500, "boom"))


def test_shorts_eligibility():
    assert youtube.is_shorts_eligible(45, 1080, 1920)
    assert not youtube.is_shorts_eligible(600, 1080, 1920)
    assert not youtube.is_shorts_eligible(45, 1920, 1080)


def _mock_client(monkeypatch, handler, module):
    real_client = httpx.Client  # bind before patching: module.httpx is the shared httpx module

    def factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(module.httpx, "Client", factory)


def test_resumable_upload_resumes_after_308(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0123456789")
    monkeypatch.setattr(youtube, "CHUNK", 4)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.headers["X-Upload-Content-Length"] == "10"
            return httpx.Response(200, headers={"Location": "https://upload.test/session"})
        seen.append(request.headers.get("Content-Range", ""))
        if len(seen) == 1:
            return httpx.Response(308, headers={"Range": "bytes=0-3"})
        if len(seen) == 2:
            return httpx.Response(503)  # transient failure -> status probe -> resume
        if request.headers.get("Content-Length") == "0":
            return httpx.Response(308, headers={"Range": "bytes=0-7"})
        return httpx.Response(201, json={"id": "vid123", "status": {"privacyStatus": "private"}})

    _mock_client(monkeypatch, handler, youtube)
    creds = store.Credentials(platform="youtube", access_token="a", expires_at=datetime.now(UTC) + timedelta(hours=1))
    out = youtube.upload_video(creds, video, youtube.build_metadata("t", "d", [], "private"), sleep=lambda s: None)
    assert out["id"] == "vid123" and out["url"] == "https://www.youtube.com/watch?v=vid123"
    assert seen[0] == "bytes 0-3/10" and seen[1] == "bytes 4-7/10"
    assert seen[-1] == "bytes 8-9/10"  # resumed exactly where the server said


def test_upload_missing_file():
    creds = store.Credentials(platform="youtube", access_token="a")
    with pytest.raises(PublishError, match="not found"):
        youtube.upload_video(creds, "/nope/missing.mp4", {})


# --------------------------------------------------------------------------- tiktok

@pytest.mark.parametrize("size", [1, 4 * 1024**2, 5 * 1024**2, 40 * 1024**2, 700 * 1024**2, 3 * 1024**3])
def test_chunk_plan_covers_file_and_respects_limits(size):
    chunk, count, ranges = tiktok.plan_chunks(size)
    assert count == len(ranges) <= tiktok.MAX_CHUNKS
    assert ranges[0][0] == 0 and ranges[-1][1] == size - 1
    for a, b in zip(ranges, ranges[1:], strict=False):
        assert b[0] == a[1] + 1  # contiguous
    if size > tiktok.MIN_CHUNK:
        assert tiktok.MIN_CHUNK <= chunk <= tiktok.MAX_CHUNK
        assert (ranges[-1][1] - ranges[-1][0] + 1) >= chunk  # final chunk absorbs the remainder
    else:
        assert count == 1


def test_post_info_validation():
    info = tiktok.build_post_info("hello", "PUBLIC_TO_EVERYONE", disable_comment=True, cover_ms=-5)
    assert info["privacy_level"] == "PUBLIC_TO_EVERYONE" and info["disable_comment"] is True
    assert info["video_cover_timestamp_ms"] == 0
    with pytest.raises(PublishError):
        tiktok.build_post_info("t", "EVERYONE")


def test_tiktok_error_mapping():
    err = tiktok._api_error(403, "unaudited_client_can_only_post_to_private_accounts", "x")
    assert "not audited" in str(err)
    assert "rate-limit" in str(tiktok._api_error(429, "spam_risk_too_many_posts", ""))


def test_inbox_upload_flow(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 1000)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(f"{request.method} {url.rsplit('/v2', 1)[-1] or url}")
        if url.endswith("/inbox/video/init/"):
            body = json.loads(request.content)
            assert body["source_info"] == {"source": "FILE_UPLOAD", "video_size": 1000, "chunk_size": 1000,
                                           "total_chunk_count": 1}
            return httpx.Response(200, json={"data": {"publish_id": "pub1", "upload_url": "https://up.test/x"}})
        if url.startswith("https://up.test"):
            assert request.headers["Content-Range"] == "bytes 0-999/1000"
            return httpx.Response(201)
        return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}})

    _mock_client(monkeypatch, handler, tiktok)
    creds = store.Credentials(platform="tiktok", access_token="a", expires_at=datetime.now(UTC) + timedelta(hours=5))
    out = tiktok.publish_video(creds, video, mode="inbox", sleep=lambda s: None, poll_seconds=0)
    assert out["status"] == "SEND_TO_USER_INBOX" and out["needs_user_action"] is True
    assert any("inbox/video/init" in c for c in calls) and any("status/fetch" in c for c in calls)


# --------------------------------------------------------------------------- service / compliance gate

def _seed(compliance: str, tmp_path) -> str:
    folder = tmp_path / "clip_001"
    folder.mkdir(exist_ok=True)
    (folder / "clip_001.mp4").write_bytes(b"video")
    with session_scope() as s:
        s.merge(Project(id="ppub", name="Pub test", campaign_name="Camp", platforms=["tiktok", "youtube_shorts"]))
        cand = Candidate(id="cpub" + compliance[:2], project_id="ppub", candidate_id="c01", rank=1, ai_start=0, ai_end=30,
                         start=0, end=30, viral_score=80, data={"topic": "Money", "summary": "s"},
                         hooks=[{"text": "Hook line", "source": "ai"}], hook_index=0,
                         posting_copy={"tiktok_caption": "Cap", "youtube_title": "YT title",
                                       "youtube_description": "Desc", "hashtags": ["#Money"],
                                       "required": {"hashtags": ["#Money"], "mentions": [], "cta": None, "links": []},
                                       "full": {"tiktok": "Cap #Money", "youtube_shorts": "YT title\n\nDesc #Money"}},
                         compliance_status=compliance)
        s.merge(cand)
        s.merge(ExportRecord(id="epub" + compliance[:2], project_id="ppub", candidate_pk=cand.id, folder=str(folder),
                             files={"A": "clip_001.mp4", "primary": "clip_001.mp4", "folder": "clip_001"},
                             metadata_json={"duration": 30.0}, compliance_status=compliance))
        return cand.id


def test_clip_options_defaults(tmp_path):
    pk = _seed("COMPLIANT", tmp_path)
    opts = service.clip_options(pk)
    assert opts["youtube"]["title"] == "YT title" and opts["youtube"]["privacy"] == "private"
    assert opts["tiktok"]["privacy"] == "SELF_ONLY" and opts["tiktok"]["mode"] == "inbox"
    assert "#Money" in opts["tiktok"]["caption"]
    assert opts["default_variant"] == "A" and opts["compliance_blocks_publishing"] is False
    assert "connections" in opts


def test_failed_compliance_blocks_publishing(tmp_path):
    pk = _seed("FAILED", tmp_path)
    opts = service.clip_options(pk)
    assert opts["compliance_blocks_publishing"] is True

    class Tracker:
        def step(self, *a, **k): pass
        def set_message(self, *a, **k): pass

    with pytest.raises(PublishError, match="FAILED"):
        service.run_publish("ppub", Tracker(), {"candidate_pk": pk, "variant": "A",
                                                "targets": [{"platform": "youtube"}]})


def test_publish_requires_connection(tmp_path):
    pk = _seed("WARNING", tmp_path)
    store.delete("youtube")
    steps: list[tuple] = []

    class Tracker:
        def step(self, key, status=None, **k): steps.append((key, status, k.get("detail", "")))
        def set_message(self, *a, **k): pass

    with pytest.raises(PublishError):
        service.run_publish("ppub", Tracker(), {"candidate_pk": pk, "variant": "A",
                                                "targets": [{"platform": "youtube"}]})
    assert any("not connected" in (d or "") for _, _, d in steps)
