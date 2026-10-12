"""REST API for the POV video generator.

Routes:
  POST /api/pov/generate          – kick off the pipeline, returns job_id immediately
  GET  /api/pov/{job_id}/status   – poll job status + progress
  GET  /api/pov/{job_id}/download – stream the finished MP4
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..pipeline.pov_render import get_job, launch_pov_job, new_job
from ..services.openai_pov import VALID_STYLES, VALID_VOICES

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pov", tags=["pov"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=500)
    style: str = Field("cinematic")
    duration_s: int = Field(30, ge=10, le=120)
    voice: str = Field("onyx")


class GenerateResponse(BaseModel):
    job_id: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=GenerateResponse)
def generate(body: GenerateRequest) -> dict[str, Any]:
    """Start the POV pipeline in a background thread and return the job_id."""
    settings = get_settings()
    if not settings.openai_configured:
        raise HTTPException(503, "OPEN_AI_LISBON_API_KEY is not set in .env")

    if body.style not in VALID_STYLES:
        raise HTTPException(422, f"style must be one of: {', '.join(VALID_STYLES)}")
    if body.voice not in VALID_VOICES:
        raise HTTPException(422, f"voice must be one of: {', '.join(VALID_VOICES)}")

    job_id, job_dir = new_job()
    api_key = settings.open_ai_lisbon_api_key

    def _run() -> None:
        launch_pov_job(
            job_id=job_id,
            job_dir=job_dir,
            topic=body.topic,
            style=body.style,
            duration_s=body.duration_s,
            voice=body.voice,
            api_key=api_key,
        )

    t = threading.Thread(target=_run, daemon=True, name=f"pov-{job_id[:8]}")
    t.start()
    log.info("POV job %s started (topic=%r)", job_id, body.topic)
    return {"job_id": job_id}


@router.get("/{job_id}/status")
def status(job_id: str) -> dict[str, Any]:
    """Return the current job status, stage, and progress (0.0–1.0)."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/{job_id}/download")
def download(job_id: str):
    """Stream the finished output.mp4 as a file download."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job["status"] == "error":
        raise HTTPException(500, job.get("error") or "Job failed")
    if job["status"] != "done":
        raise HTTPException(409, "Video is not ready yet")
    output_path = Path(job["output"])
    if not output_path.exists():
        raise HTTPException(404, "Output file missing")
    filename = f"pov_video_{job_id[:8]}.mp4"
    return FileResponse(
        path=str(output_path),
        media_type="video/mp4",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{job_id}/image/{scene_idx}")
def get_scene_image(job_id: str, scene_idx: int):
    """Serve the generated PNG image for a specific scene."""
    from ..pipeline.pov_render import pov_jobs_root
    img_path = pov_jobs_root() / job_id / f"scene_{scene_idx}.png"
    if not img_path.exists():
        raise HTTPException(404, f"Image for scene {scene_idx} not found")
    return FileResponse(path=str(img_path), media_type="image/png")

