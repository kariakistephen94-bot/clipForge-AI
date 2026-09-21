"""Workspace folder layout helpers."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .config import get_settings

PROJECT_SUBDIRS = (
    "source",
    "audio",
    "transcript",
    "analysis",
    "candidates",
    "renders",
    "exports",
    "cache",
    "READY_TO_POST",
)


def workspace() -> Path:
    ws = get_settings().workspace
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def projects_root() -> Path:
    p = workspace() / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_dir(project_id: str, sub: str | None = None) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", project_id):
        raise ValueError("invalid project id")
    base = projects_root() / project_id
    if sub is not None:
        if sub not in PROJECT_SUBDIRS:
            raise ValueError(f"unknown project subdir {sub}")
        base = base / sub
    base.mkdir(parents=True, exist_ok=True)
    return base


def ensure_project_tree(project_id: str) -> Path:
    for sub in PROJECT_SUBDIRS:
        project_dir(project_id, sub)
    return project_dir(project_id)


def source_cache_dir(sha256: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("invalid sha256")
    p = workspace() / "cache" / sha256
    p.mkdir(parents=True, exist_ok=True)
    return p


def broll_dir() -> Path:
    p = workspace() / "broll"
    p.mkdir(parents=True, exist_ok=True)
    return p


def music_dir() -> Path:
    p = workspace() / "music"
    p.mkdir(parents=True, exist_ok=True)
    return p


def whisper_models_dir() -> Path:
    p = workspace() / "models" / "whisper"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sanitize_filename(name: str, max_len: int = 80, default: str = "file") -> str:
    """Return a safe single path component (no separators, no leading dots)."""
    name = unicodedata.normalize("NFKD", name or "")
    name = name.encode("ascii", "ignore").decode("ascii")
    name = Path(name).name  # drop any directory parts
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    if not name:
        name = default
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 5:
            name = stem[: max_len - len(ext) - 1] + "." + ext
        else:
            name = name[:max_len]
    return name


def slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return (text[:max_len].rstrip("_")) or "clip"


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
