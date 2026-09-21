"""Local transcription with faster-whisper (word-level timestamps). Nothing leaves the machine."""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..paths import whisper_models_dir
from ..utils.timeutil import srt_timestamp

log = logging.getLogger(__name__)

WHISPER_MODELS = ("tiny", "base", "small", "medium")
DEFAULT_WHISPER_MODEL = "base"


class TranscriptionError(RuntimeError):
    pass


def detect_device() -> dict[str, Any]:
    """Pick the best safe local configuration for CTranslate2."""
    info: dict[str, Any] = {"machine": platform.machine(), "system": platform.system()}
    try:
        import ctranslate2

        cuda = ctranslate2.get_cuda_device_count()
    except Exception:  # noqa: BLE001
        cuda = 0
    if cuda > 0:
        info.update(device="cuda", compute_type="float16", note=f"CUDA GPU x{cuda}")
    else:
        # CTranslate2 has no Metal backend; on Apple Silicon the arm64 build uses Apple Accelerate on CPU.
        note = "Apple Silicon CPU (Accelerate)" if info["machine"] == "arm64" and info["system"] == "Darwin" else "CPU"
        info.update(device="cpu", compute_type="int8", note=note)
    info["cpu_threads"] = max(1, (os.cpu_count() or 4))
    return info


_model_cache: dict[str, Any] = {}
_model_lock = threading.Lock()


def _load_model(size: str):
    from faster_whisper import WhisperModel

    dev = detect_device()
    key = f"{size}:{dev['device']}:{dev['compute_type']}"
    with _model_lock:
        if key not in _model_cache:
            try:
                _model_cache[key] = WhisperModel(
                    size, device=dev["device"], compute_type=dev["compute_type"],
                    cpu_threads=dev["cpu_threads"], download_root=str(whisper_models_dir()),
                )
            except Exception as e:  # noqa: BLE001
                if dev["device"] != "cpu":
                    log.warning("GPU whisper init failed (%s); falling back to CPU", e)
                    _model_cache[key] = WhisperModel(size, device="cpu", compute_type="int8",
                                                     download_root=str(whisper_models_dir()))
                else:
                    raise TranscriptionError(
                        f"Could not load Whisper model '{size}'. The first run downloads it from Hugging Face -- "
                        f"check your internet connection. ({e})"
                    ) from e
        return _model_cache[key]


def transcribe_audio(
    audio_path: str | Path,
    model_size: str = DEFAULT_WHISPER_MODEL,
    duration: float | None = None,
    language: str | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    if model_size not in WHISPER_MODELS:
        model_size = DEFAULT_WHISPER_MODEL
    model = _load_model(model_size)
    try:
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=5,
            condition_on_previous_text=False,  # reduces repetition loops on long inputs
        )
        total = duration or float(getattr(info, "duration", 0) or 0) or 1.0
        segments: list[dict[str, Any]] = []
        for seg in segments_iter:
            words = [
                {"start": round(float(w.start), 3), "end": round(float(w.end), 3), "word": w.word.strip(),
                 "probability": round(float(w.probability), 3)}
                for w in (seg.words or [])
                if w.word.strip()
            ]
            segments.append({
                "id": len(segments),
                "start": round(float(seg.start), 3),
                "end": round(float(seg.end), 3),
                "text": seg.text.strip(),
                "words": words,
            })
            if on_progress:
                on_progress(min(1.0, float(seg.end) / total))
    except TranscriptionError:
        raise
    except Exception as e:  # noqa: BLE001
        raise TranscriptionError(f"Whisper transcription failed: {e}") from e
    return {
        "language": getattr(info, "language", None),
        "language_probability": round(float(getattr(info, "language_probability", 0) or 0), 3),
        "duration": float(getattr(info, "duration", 0) or 0),
        "model": model_size,
        "segments": segments,
    }


def empty_transcript(reason: str) -> dict[str, Any]:
    return {"language": None, "duration": 0, "model": None, "segments": [], "note": reason}


_CONTINUATION = re.compile(r"^(?:[,.]\d|[%'’]|n't\b|[.,!?;:…]+$)")


def flatten_words(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten segment words, re-joining Whisper token splits such as "$40" + ",000" or "it" + "'s"."""
    words: list[dict[str, Any]] = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            if w.get("end", 0) < w.get("start", 0) or not str(w.get("word", "")).strip():
                continue
            text = str(w["word"]).strip()
            if words and _CONTINUATION.match(text) and w["start"] - words[-1]["end"] < 0.3:
                prev = words[-1]
                words[-1] = {**prev, "word": prev["word"] + text, "end": max(prev["end"], w["end"])}
                continue
            words.append({**w, "word": text})
    words.sort(key=lambda w: w["start"])
    return words


def transcript_to_srt(transcript: dict[str, Any]) -> str:
    out = []
    for i, seg in enumerate(transcript.get("segments", []), 1):
        out.append(f"{i}\n{srt_timestamp(seg['start'])} --> {srt_timestamp(seg['end'])}\n{seg['text'].strip()}\n")
    return "\n".join(out)


def transcript_to_text(transcript: dict[str, Any]) -> str:
    return "\n".join(seg["text"].strip() for seg in transcript.get("segments", []))


def transcript_for_prompt(transcript: dict[str, Any], max_chars: int = 400_000) -> str:
    """Compact timestamped transcript for Gemini: one line per segment."""
    lines = []
    for seg in transcript.get("segments", []):
        lines.append(f"[{seg['start']:.1f}-{seg['end']:.1f}] {seg['text'].strip()}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[transcript truncated]"
    return text or "[no speech detected]"


def write_transcript_files(transcript: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out_dir / "transcript.json",
        "txt": out_dir / "transcript.txt",
        "srt": out_dir / "transcript.srt",
    }
    paths["json"].write_text(json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")
    paths["txt"].write_text(transcript_to_text(transcript), encoding="utf-8")
    paths["srt"].write_text(transcript_to_srt(transcript), encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}


def words_in_range(words: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    return [w for w in words if w["end"] > start and w["start"] < end]
