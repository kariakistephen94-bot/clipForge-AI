import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="clipforge-test-")
os.environ["CLIPFORGE_WORKSPACE"] = _TMP
os.environ["CLIPFORGE_LOG_DIR"] = str(Path(_TMP) / "logs")
os.environ["GEMINI_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import reload_settings  # noqa: E402
from app.db import init_db, reset_engine  # noqa: E402

reload_settings()
reset_engine(f"sqlite:///{_TMP}/test.db")
init_db()


def make_words(text: str, start: float = 0.0, word_dur: float = 0.3, gap: float = 0.05,
               pauses: dict[int, float] | None = None) -> list[dict]:
    """Build Whisper-like word dicts. pauses: {word_index: extra seconds of silence BEFORE that word}."""
    words = []
    t = start
    for i, w in enumerate(text.split()):
        t += (pauses or {}).get(i, 0.0)
        words.append({"start": round(t, 3), "end": round(t + word_dur, 3), "word": w})
        t += word_dur + gap
    return words


@pytest.fixture
def words_factory():
    return make_words


@pytest.fixture
def workspace() -> Path:
    return Path(_TMP)
