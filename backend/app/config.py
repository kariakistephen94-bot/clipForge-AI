"""Runtime configuration.

Secrets (GEMINI_API_KEY) come ONLY from the environment / .env file. They are never
written to SQLite, never returned by the API, and never logged.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
PROMPTS_DIR = BACKEND_DIR / "prompts"
MODELS_DIR = BACKEND_DIR / "models"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

# Sensible current Flash default (Sept 2026). Override with GEMINI_MODEL; nothing else in the
# code depends on this specific version.
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    gemini_api_key: str = ""
    gemini_model: str = ""
    gemini_timeout_s: int = 600

    clipforge_workspace: Path = ROOT_DIR / "workspace"
    clipforge_log_dir: Path = ROOT_DIR / "logs"
    clipforge_host: str = "127.0.0.1"
    clipforge_port: int = 8765

    # Publishing (optional). Register your own apps; secrets stay in .env and never reach the browser.
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""

    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    # Upload guard (bytes). Gemini's Files API accepts up to 2 GB; larger sources get a proxy.
    max_upload_bytes: int = 20 * 1024**3

    @property
    def model_name(self) -> str:
        return self.gemini_model.strip() or DEFAULT_GEMINI_MODEL

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key.strip())

    @property
    def workspace(self) -> Path:
        return Path(self.clipforge_workspace)

    @property
    def db_path(self) -> Path:
        return self.workspace / "clipforge.db"

    @property
    def youtube_configured(self) -> bool:
        return bool(self.youtube_client_id.strip() and self.youtube_client_secret.strip())

    @property
    def tiktok_configured(self) -> bool:
        return bool(self.tiktok_client_key.strip() and self.tiktok_client_secret.strip())

    @property
    def oauth_redirect_base(self) -> str:
        return f"http://127.0.0.1:{self.clipforge_port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
