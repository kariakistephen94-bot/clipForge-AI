"""ffprobe metadata extraction and media validation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..utils.timeutil import parse_fraction
from .ffmpeg import FFmpegError, build_probe_cmd, ffprobe_bin

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
ALLOWED_MIME = {"video/mp4", "video/quicktime", "video/webm", "video/x-m4v", "video/x-matroska", "application/octet-stream"}


class InvalidMediaError(ValueError):
    pass


class VideoMetadata(BaseModel):
    duration: float
    width: int
    height: int
    display_width: int
    display_height: int
    rotation: int = 0
    fps: float
    video_codec: str
    audio_codec: str | None = None
    audio_channels: int = 0
    audio_streams: int = 0
    has_audio: bool = False
    bitrate: int = 0
    format_name: str = ""
    size_bytes: int = 0

    @property
    def is_landscape(self) -> bool:
        return self.display_width > self.display_height


def sniff_container(path: str | Path) -> str | None:
    """Magic-byte check: returns 'mp4' (ISO BMFF, incl. MOV), 'webm' (EBML/Matroska) or None."""
    with Path(path).open("rb") as f:
        head = f.read(64)
    if len(head) >= 12 and head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"):
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    return None


def parse_probe_json(data: dict[str, Any], size_bytes: int = 0) -> VideoMetadata:
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = [
        s for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    if not video:
        raise InvalidMediaError("No video stream found -- is this an audio-only file or an image?")
    v = video[0]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    # Prefer the default-disposition audio track when there are several.
    audio_sorted = sorted(audio, key=lambda s: 0 if (s.get("disposition") or {}).get("default") else 1)
    a = audio_sorted[0] if audio_sorted else None

    duration = float(fmt.get("duration") or v.get("duration") or 0)
    if duration <= 0:
        raise InvalidMediaError("Could not determine video duration (file may be corrupt or still downloading).")
    width, height = int(v.get("width") or 0), int(v.get("height") or 0)
    if width <= 0 or height <= 0:
        raise InvalidMediaError("Video stream has no dimensions.")

    rotation = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = int(float(sd["rotation"])) % 360
            except (TypeError, ValueError):
                pass
    tags_rot = (v.get("tags") or {}).get("rotate")
    if tags_rot and not rotation:
        try:
            rotation = int(tags_rot) % 360
        except ValueError:
            pass
    dw, dh = (height, width) if rotation in (90, 270) else (width, height)

    fps = parse_fraction(v.get("avg_frame_rate")) or parse_fraction(v.get("r_frame_rate")) or 30.0
    if fps > 240 or fps <= 0:
        fps = 30.0

    return VideoMetadata(
        duration=round(duration, 3),
        width=width,
        height=height,
        display_width=dw,
        display_height=dh,
        rotation=rotation,
        fps=round(fps, 3),
        video_codec=str(v.get("codec_name") or "unknown"),
        audio_codec=str(a.get("codec_name")) if a else None,
        audio_channels=int(a.get("channels") or 0) if a else 0,
        audio_streams=len(audio),
        has_audio=bool(a),
        bitrate=int(fmt.get("bit_rate") or v.get("bit_rate") or 0),
        format_name=str(fmt.get("format_name") or ""),
        size_bytes=size_bytes or int(fmt.get("size") or 0),
    )


def probe_video(path: str | Path) -> VideoMetadata:
    p = Path(path)
    if not p.exists():
        raise InvalidMediaError("Source file does not exist.")
    try:
        out = subprocess.run(build_probe_cmd(p, ffprobe_bin()), capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as e:
        raise FFmpegError("ffprobe timed out") from e
    if out.returncode != 0:
        raise InvalidMediaError(f"ffprobe could not read this file: {out.stderr.strip()[:300] or 'unknown error'}")
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as e:
        raise InvalidMediaError("ffprobe returned unreadable output") from e
    return parse_probe_json(data, p.stat().st_size)


def needs_proxy(meta: VideoMetadata, path: str | Path) -> bool:
    """A browser/Gemini-friendly H.264 MP4 proxy is made when the original is heavy or not web-playable."""
    ext = Path(path).suffix.lower()
    return (
        meta.video_codec != "h264"
        or ext not in (".mp4", ".m4v", ".mov")
        or (meta.has_audio and meta.audio_codec not in ("aac", "mp3"))
        or meta.size_bytes > 400 * 1024 * 1024
        or meta.display_height > 1080
        or meta.display_width > 1920
    )
