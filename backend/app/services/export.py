"""Export folders, metadata.json and READY_TO_POST with posting_plan.csv."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..paths import project_dir, slugify
from .posting import PLATFORM_LABEL

VARIANT_SUFFIX = {"A": "", "B": "_variant_B_alt_hook", "C": "_variant_C_no_hook"}


def build_metadata(
    *, candidate_id: str, source_file: str, start: float, end: float, duration: float, viral_score: float,
    topic: str, hook: str, alternative_hooks: list[str], campaign: str, platforms: list[str],
    campaign_compliance: str, hashtags: list[str], created_at: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "candidate_id": candidate_id,
        "source_file": source_file,
        "start": round(float(start), 3),
        "end": round(float(end), 3),
        "duration": round(float(duration), 3),
        "viral_score": round(float(viral_score), 1),
        "topic": topic,
        "hook": hook,
        "alternative_hooks": list(alternative_hooks),
        "campaign": campaign,
        "platforms": list(platforms),
        "campaign_compliance": campaign_compliance,
        "hashtags": list(hashtags),
        "created_at": created_at or datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if extra:
        meta.update({k: v for k, v in extra.items() if k not in meta})
    return meta


def _place(src: str, dst: Path, move: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    if move:
        shutil.move(src, dst)
    else:
        shutil.copy2(src, dst)


def export_clip(
    *, project_id: str, index: int, variants: dict[str, str], thumbnail: str, clip_text: str, srt: str,
    metadata: dict[str, Any], posting_copy_txt: str, compliance: dict[str, Any],
) -> tuple[Path, dict[str, str]]:
    name = f"clip_{index:03d}"
    folder = project_dir(project_id, "exports") / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    files: dict[str, str] = {}
    primary = "A" if "A" in variants else ("B" if "B" in variants else "C")
    for v, path in variants.items():
        fname = f"{name}.mp4" if v == primary else f"{name}{VARIANT_SUFFIX[v]}.mp4"
        # variant C is the base and may be referenced by nothing else: move all render outputs.
        _place(path, folder / fname, move=True)
        files[v] = fname
    if thumbnail and Path(thumbnail).exists():
        _place(thumbnail, folder / "thumbnail.jpg", move=False)
        files["thumbnail"] = "thumbnail.jpg"
    (folder / "transcript.txt").write_text(clip_text + "\n", encoding="utf-8")
    (folder / "captions.srt").write_text(srt, encoding="utf-8")
    (folder / "metadata.json").write_text(json.dumps({**metadata, "files": files}, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "posting_copy.txt").write_text(posting_copy_txt, encoding="utf-8")
    (folder / "compliance.json").write_text(json.dumps(compliance, indent=2), encoding="utf-8")
    files.update(primary=f"{name}.mp4", folder=name)
    return folder, files


PLAN_COLUMNS = ["clip", "viral_score", "platform", "hook", "caption", "hashtags", "campaign", "status", "file_path"]


def posting_plan_rows(entries: list[dict[str, Any]], campaign: str) -> list[dict[str, Any]]:
    rows = []
    for e in entries:
        for platform, caption in e["copy"]["full"].items():
            rows.append({
                "clip": e["folder_name"],
                "viral_score": round(float(e["viral_score"]), 1),
                "platform": PLATFORM_LABEL.get(platform, platform),
                "hook": e["hook"],
                "caption": caption,
                "hashtags": " ".join(e["copy"].get("hashtags", [])),
                "campaign": campaign,
                "status": "READY",
                "file_path": e["file_path"],
            })
    return rows


def write_posting_plan(rows: list[dict[str, Any]], path: Path) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=PLAN_COLUMNS)
    w.writeheader()
    w.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def build_ready_to_post(project_id: str, entries: list[dict[str, Any]], campaign: str) -> tuple[Path, int]:
    """entries: {export_folder, topic, viral_score, hook, copy, compliance_status}. FAILED clips are excluded."""
    root = project_dir(project_id, "READY_TO_POST")
    for child in root.iterdir():
        if child.is_dir() and re.match(r"^\d{2,3}_", child.name):
            shutil.rmtree(child)
        elif child.name == "posting_plan.csv":
            child.unlink()
    ready = [e for e in entries if e["compliance_status"] != "FAILED"]
    ready.sort(key=lambda e: -float(e["viral_score"]))
    plan_entries = []
    for n, e in enumerate(ready, 1):
        src_folder = Path(e["export_folder"])
        name = f"{n:02d}_{slugify(e.get('topic') or e.get('hook') or src_folder.name)}"
        dst = root / name
        dst.mkdir(parents=True)
        for f in src_folder.iterdir():
            if f.is_file():
                _link_or_copy(f, dst / f.name)
        primary = dst / f"{src_folder.name}.mp4"
        plan_entries.append({**e, "folder_name": name, "file_path": str(primary)})
    write_posting_plan(posting_plan_rows(plan_entries, campaign), root / "posting_plan.csv")
    return root, len(ready)


# --------------------------------------------------------------------------- long-form


def long_description(title: str, data: dict[str, Any], chapters_txt: str, campaign: str = "") -> str:
    parts = [data.get("description") or data.get("summary") or ""]
    if chapters_txt:
        parts += ["", chapters_txt]
    tags = data.get("tags") or []
    if tags:
        parts += ["", "Tags: " + ", ".join(tags)]
    return "\n".join(p for p in parts if p is not None).strip() + "\n"


def export_long_clip(*, project_id: str, index: int, result: Any, title: str, titles: list[str], data: dict[str, Any],
                     prompts: list[dict[str, Any]], compliance: dict[str, Any], source_file: str, start: float,
                     end: float, score: float, campaign: str, extra: dict[str, Any] | None = None,
                     ) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    from .longform_render import chapters_text
    from .thumbnail_prompts import prompts_text

    name = f"long_{index:03d}"
    folder = project_dir(project_id, "exports") / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    files: dict[str, Any] = {"video": f"{name}.mp4", "folder": name, "thumbnail_frames": [], "thumbnail_drafts": []}
    _place(result.video, folder / f"{name}.mp4", move=True)
    for p in result.thumbnail_frames:
        _place(p, folder / Path(p).name, move=False)
        files["thumbnail_frames"].append(Path(p).name)
    for p in result.thumbnail_drafts:
        _place(p, folder / Path(p).name, move=False)
        files["thumbnail_drafts"].append(Path(p).name)
    ch_txt = chapters_text(result.chapters) if result.youtube_chapters_ok else ""
    alt = [t for t in titles if t != title]
    desc = long_description(title, data, ch_txt, campaign)
    (folder / "description.txt").write_text(
        f"TITLE: {title}\n" + ("ALTERNATIVE TITLES:\n" + "\n".join(f"- {t}" for t in alt) + "\n" if alt else "")
        + "\nDESCRIPTION:\n" + desc, encoding="utf-8")
    (folder / "chapters.txt").write_text(chapters_text(result.chapters) + "\n", encoding="utf-8")
    (folder / "thumbnail_prompts.txt").write_text(prompts_text(title, prompts, alt), encoding="utf-8")
    (folder / "thumbnail_prompts.json").write_text(json.dumps(prompts, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "transcript.txt").write_text(result.clip_text + "\n", encoding="utf-8")
    (folder / "captions.srt").write_text(result.srt, encoding="utf-8")
    (folder / "compliance.json").write_text(json.dumps(compliance, indent=2), encoding="utf-8")
    meta = {
        "type": "long_form", "candidate_id": data.get("candidate_id"), "source_file": source_file,
        "start": round(float(start), 3), "end": round(float(end), 3), "duration": round(float(result.duration), 3),
        "score": round(float(score), 1), "title": title, "alternative_titles": alt, "topic": data.get("topic", ""),
        "summary": data.get("summary", ""), "tags": data.get("tags", []), "campaign": campaign,
        "chapters": result.chapters, "youtube_chapters_valid": result.youtube_chapters_ok,
        "cold_open_seconds": result.teaser_duration, "keep_segments": result.keep_segments,
        "removed_silence": result.removed_silence, "captions_burned_in": result.captions_on, "notes": result.notes,
        "campaign_compliance": compliance.get("status"),
        "score_label": "Long-form potential score (heuristic estimate, not a guarantee)",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"), **(extra or {}),
    }
    (folder / "metadata.json").write_text(json.dumps({**meta, "files": files}, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    return folder, files, meta


LONG_PLAN_COLUMNS = ["clip", "score", "title", "duration_min", "status", "video", "thumbnail_draft", "description_file"]


def build_ready_long(project_id: str, entries: list[dict[str, Any]]) -> tuple[Path, int]:
    """entries: {folder, title, score, duration, status, rank}. FAILED clips are excluded."""
    root = project_dir(project_id, "READY_TO_POST") / "LONG_FORM"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    ready = sorted((e for e in entries if e["status"] != "FAILED"), key=lambda e: -float(e["score"]))
    rows = []
    for n, e in enumerate(ready, 1):
        src = Path(e["folder"])
        dst = root / f"{n:02d}_{slugify(e.get('title') or src.name)}"
        dst.mkdir(parents=True)
        for f in src.iterdir():
            if f.is_file():
                _link_or_copy(f, dst / f.name)
        draft = next(iter(sorted(dst.glob("thumbnail_draft_*.jpg"))), None)
        rows.append({"clip": dst.name, "score": round(float(e["score"]), 1), "title": e.get("title", ""),
                     "duration_min": round(float(e.get("duration", 0)) / 60, 1), "status": "READY",
                     "video": str(dst / f"{src.name}.mp4"), "thumbnail_draft": str(draft) if draft else "",
                     "description_file": str(dst / "description.txt")})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=LONG_PLAN_COLUMNS)
    w.writeheader()
    w.writerows(rows)
    (root / "long_form_plan.csv").write_text(buf.getvalue(), encoding="utf-8")
    return root, len(ready)
