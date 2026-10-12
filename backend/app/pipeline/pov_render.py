"""POV video render pipeline.

Orchestrates the full AI -> render flow:
  1. Generate script (GPT-4o)
  2. Generate narration + images concurrently (TTS-1 + DALL-E 3)
  3. Probe each audio file for actual duration
  4. Build per-scene video segments with FFmpeg (Ken Burns zoom + captions)
  5. Concatenate segments into a single 1080x1920 MP4
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# In-memory job store: job_id -> status dict
_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}

POV_JOBS_DIR_NAME = "pov_jobs"


# ---------------------------------------------------------------------------
# Job helpers
# ---------------------------------------------------------------------------

def pov_jobs_root() -> Path:
    from ..config import get_settings
    root = get_settings().workspace / POV_JOBS_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_job() -> tuple[str, Path]:
    """Create a fresh job directory and return (job_id, job_dir)."""
    job_id = uuid.uuid4().hex
    job_dir = pov_jobs_root() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "",
            "progress": 0.0,
            "error": None,
            "output": None,
            "title": "",
            "scenes": [],
        }
    return job_id, job_dir


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = dict(_jobs[job_id]) if job_id in _jobs else None

    job_dir = pov_jobs_root() / job_id
    if not job_dir.exists():
        return job

    if job is None:
        # Recover job information from disk (e.g. after server restart)
        script_file = job_dir / "script.json"
        output_file = job_dir / "output.mp4"
        title = ""
        scenes: list[dict[str, Any]] = []
        if script_file.exists():
            try:
                sdata = json.loads(script_file.read_text(encoding="utf-8"))
                title = sdata.get("title", "")
                scenes = [
                    {
                        "index": s["index"],
                        "narration": s["narration"],
                        "image_prompt": s["image_prompt"],
                        "has_image": (job_dir / f"scene_{s['index']}.png").exists(),
                        "image_url": f"/api/pov/{job_id}/image/{s['index']}",
                    }
                    for s in sdata.get("scenes", [])
                ]
            except Exception:
                pass

        status = "done" if output_file.exists() else "unknown"
        job = {
            "job_id": job_id,
            "status": status,
            "stage": "done" if status == "done" else "",
            "progress": 1.0 if status == "done" else 0.0,
            "error": None,
            "output": str(output_file) if output_file.exists() else None,
            "title": title,
            "scenes": scenes,
        }
    else:
        # Check image availability on disk for in-memory jobs
        scenes = job.get("scenes") or []
        for s in scenes:
            s["has_image"] = (job_dir / f"scene_{s['index']}.png").exists()
            s["image_url"] = f"/api/pov/{job_id}/image/{s['index']}"
        job["scenes"] = scenes

    return job


def _update(job_id: str, **kwargs: Any) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------

def _ffmpeg() -> str:
    from ..services.ffmpeg import ffmpeg_bin
    return ffmpeg_bin()


def _probe_audio_duration(mp3_path: Path) -> float:
    """Return duration of an MP3 file in seconds via ffprobe."""
    from ..services.ffmpeg import ffprobe_bin
    result = subprocess.run(
        [ffprobe_bin(), "-v", "quiet", "-print_format", "json", "-show_streams", str(mp3_path)],
        capture_output=True, text=True, timeout=30,
    )
    data = json.loads(result.stdout or "{}")
    for stream in data.get("streams", []):
        dur = stream.get("duration")
        if dur:
            return float(dur)
    return 5.0  # fallback


def _render_scene_segment(
    scene_idx: int,
    image_path: Path,
    audio_path: Path,
    duration_s: float,
    narration: str,
    job_dir: Path,
) -> Path:
    """Render one scene into a vertical MP4 segment with Ken Burns + caption."""
    ff = _ffmpeg()
    out = job_dir / f"seg_{scene_idx}.mp4"

    # Wrap narration text (max 40 chars per line) for drawtext
    words = narration.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 <= 40:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    caption_text = "\n".join(lines)

    # Write caption text to a file to prevent FFmpeg parameter escaping bugs
    caption_file = job_dir / f"caption_{scene_idx}.txt"
    caption_file.write_text(caption_text, encoding="utf-8")
    safe_caption_path = (
        str(caption_file).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    )

    # Ken Burns: slow zoom from 1.0x to 1.05x
    fps = 25
    total_frames = int(duration_s * fps) + 1
    zoom_expr = f"zoom='min(zoom+0.0002,1.05)':d={total_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"

    cmd = [
        ff, "-y",
        "-loop", "1", "-i", str(image_path),          # input image (looped)
        "-i", str(audio_path),                          # input audio
        "-filter_complex",
        (
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,"
            f"zoompan={zoom_expr},"
            f"fps={fps},"
            f"drawtext=textfile='{safe_caption_path}'"
            f":fontsize=38:fontcolor=white:borderw=2:bordercolor=black@0.7"
            f":x=(w-text_w)/2:y=h-text_h-120"
            f":font=Arial"
            f":line_spacing=8 [v]"
        ),
        "-map", "[v]",
        "-map", "1:a",
        "-t", str(duration_s),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log.error("FFmpeg seg_%d failed:\n%s", scene_idx, result.stderr[-2000:])
        raise RuntimeError(f"FFmpeg failed for scene {scene_idx}: {result.stderr[-400:]}")
    return out


def _concat_segments(segments: list[Path], output: Path) -> None:
    """Concatenate multiple MP4 segments into the final output file."""
    ff = _ffmpeg()
    # Build filter_complex concat
    n = len(segments)
    inputs: list[str] = []
    for seg in segments:
        inputs += ["-i", str(seg)]
    filter_inputs = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    filter_complex = f"{filter_inputs}concat=n={n}:v=1:a=1[vout][aout]"
    cmd = [
        ff, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        log.error("FFmpeg concat failed:\n%s", result.stderr[-2000:])
        raise RuntimeError(f"FFmpeg concat failed: {result.stderr[-400:]}")


# ---------------------------------------------------------------------------
# Main async pipeline runner
# ---------------------------------------------------------------------------

async def _run_pipeline(
    job_id: str,
    job_dir: Path,
    topic: str,
    style: str,
    duration_s: int,
    voice: str,
    api_key: str,
) -> None:
    from ..services.openai_pov import POVScript, Scene, generate_all_scenes, generate_script

    try:
        # ── Stage 1: Script ──────────────────────────────────────────────
        _update(job_id, status="running", stage="scripting", progress=0.05)
        script = await generate_script(topic, style, duration_s, api_key)
        # Save script for inspection
        script_data = {
            "title": script.title, "topic": topic, "style": style,
            "scenes": [{"index": s.index, "narration": s.narration, "image_prompt": s.image_prompt}
                       for s in script.scenes],
        }
        (job_dir / "script.json").write_text(json.dumps(script_data, indent=2, ensure_ascii=False), encoding="utf-8")
        initial_scenes = [
            {
                "index": s.index,
                "narration": s.narration,
                "image_prompt": s.image_prompt,
                "has_image": False,
                "image_url": f"/api/pov/{job_id}/image/{s.index}",
            }
            for s in script.scenes
        ]
        _update(job_id, progress=0.15, title=script.title, scenes=initial_scenes)

        # ── Stage 2: Generate narration + images ─────────────────────────
        _update(job_id, stage="generating", progress=0.20)
        total_scenes = len(script.scenes)
        generated = 0

        def _on_scene_done(_key: str, _frac: float) -> None:
            nonlocal generated
            generated += 1
            updated_scenes = [
                {
                    "index": s.index,
                    "narration": s.narration,
                    "image_prompt": s.image_prompt,
                    "has_image": (job_dir / f"scene_{s.index}.png").exists(),
                    "image_url": f"/api/pov/{job_id}/image/{s.index}",
                }
                for s in script.scenes
            ]
            _update(job_id, progress=0.20 + 0.50 * (generated / total_scenes), scenes=updated_scenes)

        await generate_all_scenes(script, job_dir, api_key, voice, on_progress=_on_scene_done)

        # ── Stage 3: Probe audio durations ───────────────────────────────
        _update(job_id, stage="rendering", progress=0.72)
        for scene in script.scenes:
            mp3 = job_dir / f"scene_{scene.index}.mp3"
            scene.duration_s = _probe_audio_duration(mp3)

        # ── Stage 4: Render each scene segment (run in thread pool) ──────
        loop = asyncio.get_event_loop()
        segments: list[Path] = []
        for i, scene in enumerate(script.scenes):
            seg = await loop.run_in_executor(
                None,
                _render_scene_segment,
                scene.index,
                job_dir / f"scene_{scene.index}.png",
                job_dir / f"scene_{scene.index}.mp3",
                scene.duration_s,
                scene.narration,
                job_dir,
            )
            segments.append(seg)
            _update(job_id, progress=0.72 + 0.22 * ((i + 1) / total_scenes))

        # ── Stage 5: Concatenate ─────────────────────────────────────────
        _update(job_id, stage="finalizing", progress=0.95)
        output = job_dir / "output.mp4"
        await loop.run_in_executor(None, _concat_segments, segments, output)

        _update(job_id, status="done", stage="done", progress=1.0, output=str(output))
        log.info("POV job %s complete → %s", job_id, output)

    except Exception as exc:  # noqa: BLE001
        log.exception("POV job %s failed", job_id)
        _update(job_id, status="error", error=str(exc))


# ---------------------------------------------------------------------------
# Public entry point (called from API route in a background thread)
# ---------------------------------------------------------------------------

def launch_pov_job(
    job_id: str,
    job_dir: Path,
    topic: str,
    style: str,
    duration_s: int,
    voice: str,
    api_key: str,
) -> None:
    """Run the full async pipeline synchronously (called from a background thread)."""
    asyncio.run(
        _run_pipeline(job_id, job_dir, topic, style, duration_s, voice, api_key)
    )
