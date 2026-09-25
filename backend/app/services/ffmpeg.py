"""FFmpeg / ffprobe execution and command builders.

All commands are argument lists (never shell strings). Builders are pure functions so they
can be unit-tested without FFmpeg installed.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr_tail: str = ""):
        super().__init__(message)
        self.stderr_tail = stderr_tail


class FFmpegMissingError(FFmpegError):
    pass


def ffmpeg_bin() -> str:
    path = shutil.which(get_settings().ffmpeg_bin)
    if not path:
        raise FFmpegMissingError(
            "FFmpeg was not found. Install it (macOS: `brew install ffmpeg`, Windows: `winget install Gyan.FFmpeg`, "
            "Ubuntu/Debian: `sudo apt install ffmpeg`) and restart ClipForge AI."
        )
    return path


def ffprobe_bin() -> str:
    path = shutil.which(get_settings().ffprobe_bin)
    if not path:
        raise FFmpegMissingError("ffprobe was not found. It ships with FFmpeg -- install FFmpeg and restart.")
    return path


def ffmpeg_filters() -> set[str]:
    try:
        out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20)
    except (FFmpegMissingError, subprocess.SubprocessError, OSError):
        return set()
    names = set()
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch(r"[TSC.]{2,3}", parts[0]):
            names.add(parts[1])
    return names


def _drain(stream, sink: deque[str]) -> None:
    for line in iter(stream.readline, ""):
        sink.append(line.rstrip())
    stream.close()


def run_ffmpeg(
    args: Sequence[str],
    *,
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    timeout: float | None = None,
    capture_stderr: bool = False,
) -> str:
    """Run an ffmpeg/ffprobe command list. Returns stderr text if capture_stderr else ''."""
    cmd = list(args)
    is_ffmpeg = Path(cmd[0]).name.startswith("ffmpeg")
    if is_ffmpeg and on_progress and duration:
        cmd[1:1] = ["-progress", "pipe:1", "-nostats"]
    log.debug("exec: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    except FileNotFoundError as e:
        raise FFmpegMissingError(f"Executable not found: {cmd[0]}") from e

    err_lines: deque[str] = deque(maxlen=4000 if capture_stderr else 60)
    t = threading.Thread(target=_drain, args=(proc.stderr, err_lines), daemon=True)
    t.start()
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if on_progress and duration and line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                    on_progress(max(0.0, min(1.0, us / 1e6 / duration)))
                except ValueError:
                    pass
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        proc.kill()
        raise FFmpegError("FFmpeg timed out") from e
    t.join(timeout=5)
    stderr = "\n".join(err_lines)
    if proc.returncode != 0:
        tail = "\n".join(list(err_lines)[-15:])
        raise FFmpegError(f"FFmpeg failed (exit {proc.returncode}): {_summarize(tail)}", tail)
    return stderr if capture_stderr else ""


def _summarize(stderr_tail: str) -> str:
    lines = [ln for ln in stderr_tail.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if any(w in ln.lower() for w in ("error", "invalid", "no such", "not found", "unsupported", "failed")):
            return ln.strip()[:300]
    return (lines[-1].strip() if lines else "unknown error")[:300]


# --------------------------------------------------------------------------- builders


def build_probe_cmd(path: str | Path, ffprobe: str = "ffprobe") -> list[str]:
    return [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]


def build_extract_audio_cmd(src: str | Path, out_wav: str | Path, audio_index: int = 0, ffmpeg: str = "ffmpeg") -> list[str]:
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-map", f"0:a:{audio_index}", "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav),
    ]


def even(n: float) -> int:
    v = int(round(n))
    return v - (v % 2)


def build_proxy_cmd(src: str | Path, out: str | Path, src_height: int, has_audio: bool, target_height: int = 540,
                    ffmpeg: str = "ffmpeg") -> list[str]:
    h = even(min(target_height, src_height or target_height))
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "0:a:0"]
    cmd += [
        "-vf", f"scale=-2:{h}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-pix_fmt", "yuv420p",
    ]
    cmd += ["-c:a", "aac", "-b:a", "96k", "-ac", "1"] if has_audio else ["-an"]
    cmd += ["-movflags", "+faststart", str(out)]
    return cmd


@dataclass
class AudioOptions:
    normalize: bool = True
    denoise: bool = True
    limiter: bool = True
    music_path: str | None = None
    music_volume: float = 0.18


def voice_filter_chain(opts: AudioOptions) -> str:
    parts = ["highpass=f=70"]
    if opts.denoise:
        parts.append("afftdn=nr=8:nf=-40")  # mild; strong settings make speech watery
    if opts.normalize:
        parts.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    parts.append("aresample=48000")
    return ",".join(parts)


def limiter_filter() -> str:
    return "alimiter=limit=0.89:level=0"


def build_cut_cmd(
    src: str | Path,
    segments: Sequence[tuple[float, float]],
    out_video: str | Path,
    out_audio: str | Path,
    *,
    fps: int,
    out_height: int,
    has_audio: bool,
    audio: AudioOptions,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Cut [segments] (absolute source seconds) and join them.

    Produces a high-quality intermediate video (no audio) and a processed WAV.
    """
    if not segments:
        raise ValueError("no segments")
    win_start = segments[0][0]
    win_end = segments[-1][1]
    rel = [(max(0.0, a - win_start), b - win_start) for a, b in segments]
    total = sum(b - a for a, b in rel)

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{win_start:.3f}", "-to", f"{win_end:.3f}", "-i", str(src)]
    inputs = 1
    music_idx = None
    if audio.music_path:
        cmd += ["-stream_loop", "-1", "-i", str(audio.music_path)]
        music_idx = inputs
        inputs += 1

    f: list[str] = []
    n = len(rel)
    for i, (a, b) in enumerate(rel):
        f.append(f"[0:v:0]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            f.append(f"[0:a:0]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
    if n > 1:
        if has_audio:
            f.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")
        else:
            f.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vc]")
    else:
        f.append("[v0]null[vc]")
        if has_audio:
            f.append("[a0]anull[ac]")
    f.append(f"[vc]fps={fps},scale=-2:{even(out_height)}:flags=lanczos,format=yuv420p[vout]")

    if has_audio:
        voice = f"[ac]{voice_filter_chain(audio)}"
    else:
        voice = f"anullsrc=r=48000:cl=mono,atrim=0:{total:.3f}"
    if music_idx is not None:
        f.append(f"{voice},asplit=2[vo][sc]")
        f.append(f"[{music_idx}:a:0]aresample=48000,volume={audio.music_volume:.3f},atrim=0:{total:.3f}[mus]")
        f.append("[mus][sc]sidechaincompress=threshold=0.03:ratio=10:attack=15:release=350[duck]")
        tail = "[vo][duck]amix=inputs=2:duration=first:normalize=0"
        f.append(f"{tail},{limiter_filter()}[aout]" if audio.limiter else f"{tail}[aout]")
    else:
        f.append(f"{voice},{limiter_filter()}[aout]" if audio.limiter else f"{voice}[aout]")

    cmd += ["-filter_complex", ";".join(f)]
    cmd += ["-map", "[vout]", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14", str(out_video)]
    cmd += ["-map", "[aout]", "-vn", "-c:a", "pcm_s16le", "-ar", "48000", "-t", f"{total:.3f}", str(out_audio)]
    return cmd


def build_decode_cmd(src: str | Path, width: int, height: int, fps: float | None = None, ffmpeg: str = "ffmpeg") -> list[str]:
    vf = [f"scale={even(width)}:{even(height)}"]
    if fps:
        vf.insert(0, f"fps={fps}")
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", ",".join(vf),
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]


@dataclass
class EncodeSettings:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 18
    preset: str = "veryfast"
    audio_bitrate: str = "192k"


def build_encode_cmd(audio_path: str | Path, out: str | Path, s: EncodeSettings, ffmpeg: str = "ffmpeg") -> list[str]:
    """Final encode: raw BGR frames on stdin + processed audio -> H.264/AAC MP4 for Shorts/Reels/TikTok."""
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{s.width}x{s.height}", "-r", str(s.fps), "-i", "-",
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", s.preset, "-crf", str(s.crf), "-profile:v", "high", "-level:v", "4.2",
        "-pix_fmt", "yuv420p", "-g", str(s.fps * 2), "-bf", "2",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-c:a", "aac", "-b:a", s.audio_bitrate, "-ar", "48000",
        "-shortest", "-movflags", "+faststart", str(out),
    ]


def build_overlay_cmd(base: str | Path, png: str | Path, out: str | Path, *, fps: int, show_until: float,
                      fade: float = 0.3, crf: int = 18, preset: str = "veryfast", audio: str | Path | None = None,
                      ffmpeg: str = "ffmpeg") -> list[str]:
    """Lightweight variant creation: overlay a transparent hook PNG on an already-rendered clip.

    ``audio`` replaces the base clip's soundtrack (hook variants carry an extra hook-entrance effect)."""
    total = show_until + fade
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(base),
        "-loop", "1", "-framerate", str(fps), "-t", f"{total:.3f}", "-i", str(png),
    ]
    if audio:
        cmd += ["-i", str(audio)]
    cmd += [
        "-filter_complex",
        f"[1:v]format=rgba,fade=t=out:st={show_until:.3f}:d={fade:.3f}:alpha=1[ov];"
        f"[0:v][ov]overlay=0:0:eof_action=pass:format=auto[v]",
        "-map", "[v]", "-map", "2:a:0" if audio else "0:a?", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-profile:v", "high", "-pix_fmt", "yuv420p",
    ]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"] if audio else ["-c:a", "copy"]
    cmd += ["-movflags", "+faststart", str(out)]
    return cmd


def build_thumbnail_cmd(video: str | Path, t: float, out: str | Path, ffmpeg: str = "ffmpeg") -> list[str]:
    return [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
            "-frames:v", "1", "-q:v", "3", str(out)]


def build_frame_cmd(video: str | Path, t: float, width: int = 1280, ffmpeg: str = "ffmpeg") -> list[str]:
    """One frame at ``t`` as PNG on stdout (thumbnail picking, grade previews)."""
    return [ffmpeg, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1",
            "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"]


def build_silencedetect_cmd(src: str | Path, start: float, end: float, noise_db: float = -35, min_d: float = 0.3,
                            ffmpeg: str = "ffmpeg") -> list[str]:
    return [ffmpeg, "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
            "-map", "0:a:0", "-af", f"silencedetect=noise={noise_db}dB:d={min_d}", "-f", "null", "-"]


_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def parse_silencedetect(stderr: str, offset: float = 0.0, clip_end: float | None = None) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    cur: float | None = None
    for line in stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            cur = max(0.0, float(m.group(1))) + offset
            continue
        m = _SIL_END.search(line)
        if m and cur is not None:
            out.append((cur, float(m.group(1)) + offset))
            cur = None
    if cur is not None and clip_end is not None:
        out.append((cur, clip_end))
    return out
