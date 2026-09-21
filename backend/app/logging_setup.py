"""Readable logging to logs/app.log with secret redaction."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from .config import get_settings

_KEY_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),  # Google API key shape
    re.compile(r"(?i)(api[_-]?key|x-goog-api-key|authorization|password|token)(\"?\s*[:=]\s*\"?)([^\s\"',&]+)"),
]


def redact(text: str) -> str:
    settings = get_settings()
    key = settings.gemini_api_key.strip()
    if key and len(key) >= 8:
        text = text.replace(key, "***REDACTED***")
    text = _KEY_PATTERNS[0].sub("***REDACTED***", text)
    text = _KEY_PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    log_dir = settings.clipforge_log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")

    root = logging.getLogger()
    root.setLevel(level)
    fh = RotatingFileHandler(log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    # Quiet noisy libraries (and keep HTTP request URLs out of the log).
    for noisy in ("httpx", "httpcore", "urllib3", "google_genai", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
