"""FastAPI application entrypoint.

Run:  backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8765
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .ai.provider import AIProviderError
from .api.publish_routes import router as publish_router
from .api.routes import router
from .config import FRONTEND_DIST, get_settings
from .db import init_db
from .logging_setup import setup_logging
from .paths import broll_dir, music_dir, projects_root
from .pipeline.progress import mark_interrupted_jobs
from .pipeline.youtube_retry import start_scheduler
from .services.ffmpeg import FFmpegError, FFmpegMissingError
from .services.ingest import IngestError
from .services.probe import InvalidMediaError
from .services.transcribe import TranscriptionError

log = logging.getLogger("clipforge")


def _warm_sfx_index() -> None:
    """Analyse new sound-library files in the background so the first render doesn't wait for it."""
    import threading

    from .services.sfx_library import scan_library, workspace_sfx_dir

    workspace_sfx_dir()

    def run() -> None:
        try:
            scan_library()
        except Exception as e:  # noqa: BLE001 - never block startup
            logging.getLogger(__name__).warning("Sound library scan failed: %s", e)

    threading.Thread(target=run, daemon=True, name="sfx-scan").start()


def create_app() -> FastAPI:
    setup_logging()
    init_db()
    projects_root()
    broll_dir()
    music_dir()
    mark_interrupted_jobs()

    app = FastAPI(title="ClipForge AI", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(publish_router)
    app.router.on_startup.append(start_scheduler)
    app.router.on_startup.append(_warm_sfx_index)

    def err(status: int, message: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": message})

    @app.exception_handler(FFmpegMissingError)
    async def _ffmissing(_: Request, e: FFmpegMissingError):
        return err(503, str(e))

    @app.exception_handler(FFmpegError)
    async def _fferr(_: Request, e: FFmpegError):
        log.error("FFmpeg error: %s\n%s", e, e.stderr_tail)
        return err(500, str(e))

    @app.exception_handler(AIProviderError)
    async def _aierr(_: Request, e: AIProviderError):
        return err(502, str(e))

    @app.exception_handler(InvalidMediaError)
    async def _media(_: Request, e: InvalidMediaError):
        return err(400, str(e))

    @app.exception_handler(IngestError)
    async def _ingest(_: Request, e: IngestError):
        return err(400, str(e))

    @app.exception_handler(TranscriptionError)
    async def _tx(_: Request, e: TranscriptionError):
        return err(500, str(e))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception):
        log.exception("Unhandled error")
        return err(500, f"Internal error: {type(e).__name__}. See logs/app.log for details.")

    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return err(404, "Not found")
            candidate = (FRONTEND_DIST / full_path).resolve()
            if full_path and candidate.is_file() and str(candidate).startswith(str(FRONTEND_DIST.resolve())):
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    s = get_settings()
    log.info("ClipForge AI %s ready. Gemini configured: %s (model %s)", __version__, s.gemini_configured, s.model_name)
    return app


app = create_app()
