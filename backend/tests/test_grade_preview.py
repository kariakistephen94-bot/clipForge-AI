"""The grade-preview endpoint decodes a real frame, so these tests need FFmpeg and are skipped without it."""

import subprocess

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.main import app
from app.models import Project, SourceVideo
from app.paths import ensure_project_tree, project_dir
from app.services.ffmpeg import FFmpegMissingError, ffmpeg_bin

try:
    FFMPEG: str | None = ffmpeg_bin()
except FFmpegMissingError:
    FFMPEG = None

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="FFmpeg not installed")


def _project(with_video: bool) -> str:
    with session_scope() as s:
        p = Project(name="grade preview", status="ready", platforms=["tiktok"])
        s.add(p)
        s.flush()
        ensure_project_tree(p.id)
        if with_video:
            src = project_dir(p.id, "source") / "original.mp4"
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "smptebars=size=320x180:rate=30", "-t", "2",
                            "-pix_fmt", "yuv420p", str(src)], check=True, timeout=60)
            s.add(SourceVideo(project_id=p.id, origin="upload", stored_path=str(src),
                              probe={"duration": 2.0, "display_width": 320, "display_height": 180, "has_audio": False}))
        return p.id


def _decode(body: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    return img


def test_grade_preview_returns_graded_jpeg():
    client = TestClient(app)
    pid = _project(with_video=True)
    try:
        url = f"/api/projects/{pid}/grade-preview"
        plain = client.get(url, params={"t": 1.0, "grade": "none"})
        mono = client.get(url, params={"t": 1.0, "grade": "mono"})
        # t beyond the end is clamped; unknown query keys are ignored; overrides apply on top of a preset
        bright = client.get(url, params={"t": 99, "grade": "none", "exposure": "0.8", "bogus": "1"})
        for r in (plain, mono, bright):
            assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
            assert r.headers["cache-control"] == "no-store"
        p, m, b = _decode(plain.content), _decode(mono.content), _decode(bright.content)
        assert p.shape[1] == 720 and p.shape == m.shape == b.shape
        assert np.abs(m[..., 0].astype(int) - m[..., 2].astype(int)).max() <= 2, "mono preview should be grey"
        assert np.abs(p[..., 0].astype(int) - p[..., 2].astype(int)).max() > 50, "colour bars are not grey"
        assert b.mean() > p.mean() * 1.15
        assert client.get(url, params={"grade": "no-such-preset"}).status_code == 200  # falls back to Off
    finally:
        client.delete(f"/api/projects/{pid}")


def test_grade_preview_without_source_is_404():
    client = TestClient(app)
    pid = _project(with_video=False)
    try:
        assert client.get(f"/api/projects/{pid}/grade-preview").status_code == 404
    finally:
        client.delete(f"/api/projects/{pid}")
    assert client.get("/api/projects/nope/grade-preview").status_code == 404
