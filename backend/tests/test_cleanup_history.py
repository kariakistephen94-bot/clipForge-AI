from fastapi.testclient import TestClient

from app.db import session_scope
from app.main import app
from app.models import Project, SourceVideo
from app.paths import ensure_project_tree, project_dir, projects_root


def _make(name: str, status: str) -> str:
    with session_scope() as s:
        p = Project(name=name, status=status, platforms=["tiktok"])
        s.add(p)
        s.flush()
        ensure_project_tree(p.id)
        src = project_dir(p.id, "source") / "original.mp4"
        src.write_bytes(b"video")
        s.add(SourceVideo(project_id=p.id, origin="upload", stored_path=str(src)))
        return p.id


def test_cleanup_hides_finished_and_deletes_failed():
    client = TestClient(app)
    for p in client.get("/api/projects").json():  # start from an empty history
        client.delete(f"/api/projects/{p['id']}")
    done, failed, waiting = _make("done", "rendered"), _make("broken", "error"), _make("retrying", "waiting")

    r = client.post("/api/projects/cleanup-history")
    assert r.status_code == 200
    assert r.json() == {"deleted": 1, "hidden": 1}

    listed = {p["id"] for p in client.get("/api/projects").json()}
    assert listed == {waiting}

    # Hidden project keeps its record and its source file.
    assert client.get(f"/api/projects/{done}").status_code == 200
    assert (project_dir(done, "source") / "original.mp4").exists()

    # Failed project is gone entirely.
    assert client.get(f"/api/projects/{failed}").status_code == 404
    assert not (projects_root() / failed).exists()
