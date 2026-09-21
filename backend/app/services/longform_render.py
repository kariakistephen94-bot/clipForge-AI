"""Long-form (16:9) rendering: cold open + trimmed episode, chapters, captions, sound design, thumbnails.

Stages
1. keep-segments: conservative dead-air removal inside the segment (same rules as shorts)
2. the teaser (cold open) and the main body are cut and encoded by FFmpeg separately with identical settings
   (a teaser taken from the middle of the segment would otherwise force FFmpeg to buffer minutes of frames);
   inside a body, cuts use one select/aselect expression, so hundreds of cuts cost nothing extra
3. concat without re-encoding; optional burned-in lower-third captions (Pillow compositor)
4. sound design (subtle): whoosh from teaser into the episode, soft whooshes on chapter starts
5. mux with MP4 chapter markers; YouTube description chapters; thumbnail reference frames + text drafts
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..schemas.ai import CampaignRules
from ..utils.timeutil import fmt_chapter
from . import sfx_library
from .captions import PRESETS, CaptionRenderer, chunks_to_srt, group_words, wrap_text
from .ffmpeg import (
    AudioOptions,
    FFmpegError,
    build_decode_cmd,
    build_silencedetect_cmd,
    even,
    ffmpeg_bin,
    parse_silencedetect,
    run_ffmpeg,
    voice_filter_chain,
)
from .fonts import load_font, resolve_font
from .probe import probe_video
from .silence import SilenceConfig, map_time_clamped, plan_keep_segments, remap_words, total_duration
from .sound_design import (
    STYLES,
    SoundEvent,
    assign_files,
    available_categories,
    keyword_events,
    mix_sound_design,
    sfx_permitted,
)
from .transcribe import words_in_range

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]

LONG_CAPTION_STYLE = replace(
    PRESETS["clean"], name="long_form", font_size=50, words_per_caption=8, max_chars_per_line=44, max_lines=2,
    position_y=0.88, highlight_mode="none", emphasis=False, box=True,
)


@dataclass
class LongRenderOptions:
    resolution: str = "1920x1080"
    captions: bool = False
    cold_open: bool = True
    silence_removal: bool = True
    sound_design: str = "subtle"
    sfx_volume: float = 1.0
    denoise: bool = True
    normalize_audio: bool = True
    crf: int = 19
    preset: str = "veryfast"

    @classmethod
    def from_prefs(cls, p: dict[str, Any]) -> LongRenderOptions:
        return cls(resolution=p.get("long_form_resolution", "1920x1080"), captions=bool(p.get("long_form_captions")),
                   cold_open=bool(p.get("long_form_cold_open", True)),
                   silence_removal=bool(p.get("long_form_silence_removal", True)),
                   sound_design=p.get("long_form_sound_design", "subtle"), sfx_volume=float(p.get("sfx_volume", 1.0)),
                   denoise=bool(p.get("denoise", True)), normalize_audio=bool(p.get("normalize_audio", True)),
                   crf=int(max(14, min(30, int(p.get("crf", 19))))),
                   preset=p.get("encoder_preset", "veryfast") if p.get("encoder_preset") in
                   ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium") else "veryfast")

    @property
    def height(self) -> int:
        return int(self.resolution.split("x")[1]) if self.resolution in ("1920x1080", "1280x720") else 1080


@dataclass
class LongRenderInput:
    source_path: str
    src_width: int
    src_height: int
    src_fps: float
    has_audio: bool
    start: float
    end: float
    words: list[dict[str, Any]]
    chapters: list[dict[str, Any]]  # [{t (source seconds), title}]
    cold_open: dict[str, Any] | None  # {start, end}
    thumbnails: list[dict[str, Any]]  # [{text_overlay, frame_timestamp}]
    rules: CampaignRules | None
    workdir: Path
    seed: str = ""


@dataclass
class LongRenderResult:
    video: str
    duration: float
    teaser_duration: float
    keep_segments: list[tuple[float, float]]
    removed_silence: list[dict[str, Any]]
    chapters: list[dict[str, Any]]  # output timeline
    youtube_chapters_ok: bool
    srt: str
    clip_text: str
    sound_events: list[dict[str, Any]]
    thumbnail_frames: list[str]
    thumbnail_drafts: list[str]
    captions_on: bool
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- command builders (pure)


def select_expr(segments: list[tuple[float, float]], offset: float) -> str:
    return "+".join(f"between(t,{a - offset:.3f},{b - offset:.3f})" for a, b in segments)


def build_long_cut_cmd(src: str | Path, segments: list[tuple[float, float]], out_video: str | Path,
                       out_audio: str | Path, *, fps: int, height: int, has_audio: bool, audio: AudioOptions,
                       crf: int, preset: str, ffmpeg: str = "ffmpeg") -> list[str]:
    """One body (ascending segments) -> final-quality H.264 (no audio) + processed WAV."""
    if not segments:
        raise ValueError("no segments")
    win_start, win_end = segments[0][0], segments[-1][1]
    total = sum(b - a for a, b in segments)
    sel = select_expr(segments, win_start)
    f = [f"[0:v:0]fps={fps},select='{sel}',setpts=N/FRAME_RATE/TB,scale=-2:{even(height)}:flags=lanczos,"
         f"format=yuv420p[vout]"]
    if has_audio:
        f.append(f"[0:a:0]aselect='{sel}',asetpts=N/SR/TB,{voice_filter_chain(audio)},alimiter=limit=0.89:level=0[aout]")
    else:
        f.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[aout]")
    return [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{win_start:.3f}", "-to", f"{win_end:.3f}",
            "-i", str(src), "-filter_complex", ";".join(f),
            "-map", "[vout]", "-an", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-g", str(fps * 2), "-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709", "-video_track_timescale", "90000", "-t", f"{total:.3f}", str(out_video),
            "-map", "[aout]", "-vn", "-c:a", "pcm_s16le", "-ar", "48000", "-t", f"{total:.3f}", str(out_audio)]


def build_concat_audio_cmd(parts: list[Path], out: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for p in parts:
        cmd += ["-i", str(p)]
    n = len(parts)
    # both parts come from the same source through the same chain, so layouts already match
    # (forcing stereo here would apply FFmpeg's -3 dB mono upmix)
    graph = "".join(f"[{i}:a:0]aformat=sample_rates=48000[a{i}];" for i in range(n))
    graph += "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    return cmd + ["-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s16le", str(out)]


def ffmetadata(chapters: list[dict[str, Any]], duration: float, title: str = "") -> str:
    lines = [";FFMETADATA1"]
    if title:
        lines.append(f"title={_meta_escape(title)}")
    for i, ch in enumerate(chapters):
        end = chapters[i + 1]["t"] if i + 1 < len(chapters) else duration
        lines += ["", "[CHAPTER]", "TIMEBASE=1/1000", f"START={int(ch['t'] * 1000)}", f"END={int(end * 1000)}",
                  f"title={_meta_escape(ch['title'])}"]
    return "\n".join(lines) + "\n"


def _meta_escape(s: str) -> str:
    return "".join("\\" + c if c in "=;#\\\n" else c for c in s)


def build_mux_cmd(video: Path, audio: Path, meta: Path | None, out: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(audio)]
    if meta:
        cmd += ["-i", str(meta), "-map_metadata", "2", "-map_chapters", "2"]
    return cmd + ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                  "-shortest", "-movflags", "+faststart", str(out)]


# --------------------------------------------------------------------------- timeline helpers (pure)


def output_chapters(chapters: list[dict[str, Any]], main: list[tuple[float, float]], teaser: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if teaser > 0:
        out.append({"t": 0.0, "title": "Preview"})
    for ch in chapters:
        t = round(teaser + map_time_clamped(float(ch["t"]), main), 2)
        if out and t - out[-1]["t"] < 10:
            if not (teaser > 0 and len(out) == 1):
                continue
            t = max(t, out[-1]["t"] + 10)
        out.append({"t": t, "title": ch["title"]})
    if out:
        out[0]["t"] = 0.0
    return out


def youtube_chapters_valid(chapters: list[dict[str, Any]], duration: float) -> bool:
    """YouTube: first at 0:00, at least 3, each at least 10 s long."""
    if len(chapters) < 3 or chapters[0]["t"] != 0:
        return False
    ends = [c["t"] for c in chapters[1:]] + [duration]
    return all(e - c["t"] >= 10 for c, e in zip(chapters, ends, strict=True))


def chapters_text(chapters: list[dict[str, Any]]) -> str:
    return "\n".join(f"{fmt_chapter(c['t'])} {c['title']}" for c in chapters)


# --------------------------------------------------------------------------- thumbnails


def _frame_at(src: str, t: float, width: int = 1280) -> np.ndarray | None:
    try:
        out = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", src, "-frames:v", "1",
                              "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout:
        return None
    img = cv2.imdecode(np.frombuffer(out.stdout, np.uint8), cv2.IMREAD_COLOR)
    return img


def _face_score(img: np.ndarray, detector: Any) -> tuple[float, tuple[float, float] | None]:
    """Largest face area (fraction of frame) x sharpness; also returns the face centre (fractions)."""
    h, w = img.shape[:2]
    small = cv2.resize(img, (640, int(640 * h / w)))
    sh, sw = small.shape[:2]
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    face_frac, centre = 0.0, None
    if detector is not None:
        detector.setInputSize((sw, sh))
        _, faces = detector.detect(small)
        if faces is not None and len(faces):
            f = max(faces, key=lambda r: r[2] * r[3])
            face_frac = float(f[2] * f[3]) / (sw * sh)
            centre = (float(f[0] + f[2] / 2) / sw, float(f[1] + f[3] / 2) / sh)
            # sharpness of the face itself matters most
            x, y, fw, fh = (int(max(0, v)) for v in f[:4])
            roi = gray[y:y + fh, x:x + fw]
            if roi.size > 100:
                sharp = float(cv2.Laplacian(roi, cv2.CV_64F).var())
    score = (0.2 + min(face_frac, 0.25) * 8) * np.log1p(sharp)
    return score, centre


def pick_thumbnail_frame(src: str, around: float, lo: float, hi: float) -> tuple[np.ndarray | None, tuple[float, float] | None]:
    from .reframe import YUNET_PATH

    detector = None
    if YUNET_PATH.exists() and hasattr(cv2, "FaceDetectorYN"):
        try:
            detector = cv2.FaceDetectorYN.create(str(YUNET_PATH), "", (640, 360), 0.6, 0.3, 20)
        except cv2.error:
            detector = None
    best: tuple[float, np.ndarray | None, tuple[float, float] | None] = (-1.0, None, None)
    for dt in (0.0, -1.2, 1.2, -2.5, 2.5, -4.0, 4.0):
        t = min(hi - 0.5, max(lo + 0.2, around + dt))
        img = _frame_at(src, t)
        if img is None:
            continue
        sc, centre = _face_score(img, detector)
        if sc > best[0]:
            best = (sc, img, centre)
    return best[1], best[2]


def thumb_layout(focus: tuple[float, float] | None) -> tuple[float, float]:
    """(zoom, where the face should sit horizontally). With a face we punch in and put it on a third line,
    on the side it already leans towards, leaving the other two thirds for text."""
    if focus is None:
        return 1.0, 0.5
    return 1.18, (0.36 if focus[0] < 0.5 else 0.64)


def cover_crop(img: np.ndarray, w: int, h: int, focus: tuple[float, float] | None,
               zoom: float = 1.0, face_at: float = 0.5) -> tuple[np.ndarray, float | None]:
    """Crop/scale to w x h. Returns the image and the face's final x position (fraction) if known."""
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih) * zoom
    r = cv2.resize(img, (int(np.ceil(iw * s)), int(np.ceil(ih * s))),
                   interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    rh, rw = r.shape[:2]
    fx, fy = focus or (0.5, 0.4)
    x = int(min(max(fx * rw - w * face_at, 0), rw - w))
    y = int(min(max(fy * rh - h * 0.42, 0), rh - h))
    return r[y:y + h, x:x + w], ((fx * rw - x) / w if focus else None)


def enhance(img: np.ndarray) -> np.ndarray:
    """Thumbnail grade: a touch more contrast and saturation, gentle vignette."""
    out = cv2.convertScaleAbs(img, alpha=1.08, beta=-6)
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * 1.18, 0, 255)
    out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    h, w = out.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    mask = np.clip(1.0 - 0.28 * np.clip(d - 0.55, 0, None) ** 1.5, 0.6, 1.0)
    return (out.astype(np.float32) * mask[..., None]).astype(np.uint8)


def draw_thumbnail_text(img_bgr: np.ndarray, text: str, face_x: float | None) -> np.ndarray:
    """Big outlined text on the side away from the face, upper area, last word highlighted."""
    text = " ".join(text.split())
    if not text:
        return img_bgr
    h, w = img_bgr.shape[:2]
    pil = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    left_side = face_x is None or face_x >= 0.5
    box_w = int(w * 0.52)
    font_path = resolve_font(None, "heavy")
    size = int(h * 0.19)
    lines: list[str] = [text]
    for _ in range(12):
        font = load_font(font_path, size, "heavy")
        lines = wrap_text(text, max(6, len(text) // 2 + 1)) if len(text) > 10 else [text]
        widest = max(draw.textlength(ln, font=font) for ln in lines)
        if widest <= box_w and len(lines) <= 2:
            break
        size = int(size * 0.9)
    stroke = max(4, size // 11)
    asc, desc = font.getmetrics() if hasattr(font, "getmetrics") else (size, size // 4)
    lh = int((asc + desc) * 0.98)
    y = int(h * 0.08)
    words_total = text.split()
    highlight = words_total[-1] if len(words_total) > 1 else ""
    for i, ln in enumerate(lines):
        lw = draw.textlength(ln, font=font)
        x = int(w * 0.05) if left_side else int(w * 0.95 - lw)
        yy = y + i * lh
        parts = ln.split(" ")
        cx = x
        for j, word in enumerate(parts):
            is_hl = bool(highlight) and i == len(lines) - 1 and j == len(parts) - 1
            fill = (255, 214, 0) if is_hl else (255, 255, 255)
            draw.text((cx, yy), word, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))
            cx += int(draw.textlength(word + " ", font=font))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def make_thumbnails(src: str, lo: float, hi: float, concepts: list[dict[str, Any]], wd: Path) -> tuple[list[str], list[str]]:
    frames, drafts = [], []
    concepts = concepts or [{"text_overlay": "", "frame_timestamp": None}]
    span = hi - lo
    for n, c in enumerate(concepts[:3]):
        tag = chr(ord("A") + n)
        ts = c.get("frame_timestamp")
        around = float(ts) if isinstance(ts, (int, float)) and lo <= ts <= hi else lo + span * (0.2 + 0.25 * n)
        img, centre = pick_thumbnail_frame(src, around, lo, hi)
        if img is None:
            continue
        zoom, face_at = thumb_layout(centre)
        cropped, fx = cover_crop(img, 1280, 720, centre, zoom, face_at)
        base = enhance(cropped)
        fpath = wd / f"thumbnail_frame_{tag}.jpg"
        cv2.imwrite(str(fpath), base, [cv2.IMWRITE_JPEG_QUALITY, 92])
        frames.append(str(fpath))
        draft = draw_thumbnail_text(base, str(c.get("text_overlay") or ""), fx)
        dpath = wd / f"thumbnail_draft_{tag}.jpg"
        cv2.imwrite(str(dpath), draft, [cv2.IMWRITE_JPEG_QUALITY, 92])
        drafts.append(str(dpath))
    return frames, drafts


# --------------------------------------------------------------------------- captions


def burn_captions(video: Path, words_out: list[dict[str, Any]], out: Path, duration: float, fps: int,
                  preset: str, crf: int, progress: ProgressFn) -> bool:
    meta = probe_video(video)
    W, H = meta.width, meta.height
    style = replace(LONG_CAPTION_STYLE, font_size=max(28, int(LONG_CAPTION_STYLE.font_size * H / 1080)))
    chunks = group_words(words_out, style, [])
    if not chunks:
        return False
    renderer = CaptionRenderer(chunks, style, W, H)
    ff = ffmpeg_bin()
    dec = subprocess.Popen(build_decode_cmd(video, W, H, ffmpeg=ff), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    enc_cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
               "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high",
               "-pix_fmt", "yuv420p", "-g", str(fps * 2), "-color_primaries", "bt709", "-color_trc", "bt709",
               "-colorspace", "bt709", str(out)]
    total = max(1, int(duration * fps))
    with tempfile.TemporaryFile() as err:
        enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        assert dec.stdout is not None and enc.stdin is not None
        n = 0
        size = W * H * 3
        try:
            while True:
                buf = dec.stdout.read(size)
                if len(buf) < size:
                    break
                frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
                renderer.overlay(frame, n / fps)
                enc.stdin.write(frame.tobytes())
                n += 1
                if n % 60 == 0:
                    progress(min(1.0, n / total), "Burning in captions")
        except BrokenPipeError:
            pass
        finally:
            try:
                enc.stdin.close()
            except BrokenPipeError:
                pass
            dec.stdout.close()
            dec.wait(timeout=60)
            rc = enc.wait(timeout=3600)
        if rc != 0 or n == 0:
            err.seek(0)
            raise FFmpegError(f"Caption encode failed (exit {rc})", err.read().decode("utf-8", "replace")[-800:])
    return True


# --------------------------------------------------------------------------- main entry


def render_long_clip(inp: LongRenderInput, opts: LongRenderOptions, progress: ProgressFn | None = None) -> LongRenderResult:
    prog = progress or (lambda f, m: None)
    wd = inp.workdir
    wd.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    ff = ffmpeg_bin()
    fps = 60 if inp.src_fps >= 50 else 30
    height = min(opts.height, even(inp.src_height)) if inp.src_height else opts.height

    # ---- 1. segments -----------------------------------------------------------------------
    clip_words = words_in_range(inp.words, inp.start, inp.end)
    cfg = SilenceConfig(enabled=opts.silence_removal and inp.has_audio and bool(clip_words))
    confirmed = None
    if cfg.enabled:
        prog(0.01, "Detecting silence")
        try:
            stderr = run_ffmpeg(build_silencedetect_cmd(inp.source_path, inp.start, inp.end, -35, 0.3, ffmpeg=ff),
                                capture_stderr=True, timeout=1800)
            confirmed = parse_silencedetect(stderr, offset=inp.start, clip_end=inp.end)
        except FFmpegError as e:
            notes.append(f"Silence detection skipped: {e}")
            cfg.enabled = False
    main, removed = plan_keep_segments(clip_words, inp.start, inp.end, cfg, confirmed, [])
    if removed:
        notes.append(f"Removed {sum(r['seconds'] for r in removed):.1f}s of dead air in {len(removed)} place(s).")
    teaser_seg: tuple[float, float] | None = None
    if opts.cold_open and inp.cold_open:
        a, b = float(inp.cold_open["start"]), float(inp.cold_open["end"])
        if inp.start <= a < b <= inp.end and b - a <= 20:
            teaser_seg = (a, b)
    main_dur = total_duration(main)
    teaser_dur = round(teaser_seg[1] - teaser_seg[0], 3) if teaser_seg else 0.0
    duration = round(main_dur + teaser_dur, 3)

    # ---- 2. cut + encode ----------------------------------------------------------------------
    aopts = AudioOptions(normalize=opts.normalize_audio, denoise=opts.denoise)
    parts_v: list[Path] = []
    parts_a: list[Path] = []
    bodies = ([("teaser", [teaser_seg])] if teaser_seg else []) + [("main", main)]
    done = 0.0
    for name, segs in bodies:
        v, a_ = wd / f"{name}.mp4", wd / f"{name}.wav"
        seg_dur = total_duration(segs)
        base_f = 0.03 + 0.6 * done / max(duration, 1)
        run_ffmpeg(build_long_cut_cmd(inp.source_path, segs, v, a_, fps=fps, height=height, has_audio=inp.has_audio,
                                      audio=aopts, crf=opts.crf, preset=opts.preset, ffmpeg=ff),
                   duration=seg_dur, timeout=6 * 3600, on_progress=_scaled(prog, base_f, 0.6 * seg_dur / max(duration, 1)))
        done += seg_dur
        parts_v.append(v)
        parts_a.append(a_)
    video = wd / "video.mp4"
    if len(parts_v) > 1:
        lst = wd / "concat.txt"
        lst.write_text("".join(f"file '{p.name}'\n" for p in parts_v), encoding="utf-8")
        run_ffmpeg([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(video)], timeout=3600)
        voice = wd / "voice.wav"
        run_ffmpeg(build_concat_audio_cmd(parts_a, voice, ffmpeg=ff), timeout=3600)
    else:
        parts_v[0].replace(video)
        voice = parts_a[0]

    # ---- 3. words on the output timeline, captions --------------------------------------------
    words_out: list[dict[str, Any]] = []
    if teaser_seg:
        words_out += remap_words(words_in_range(clip_words, *teaser_seg), [teaser_seg])
    words_out += [{**w, "start": round(w["start"] + teaser_dur, 3), "end": round(w["end"] + teaser_dur, 3)}
                  for w in remap_words(clip_words, main)]
    srt = chunks_to_srt(group_words(words_out, LONG_CAPTION_STYLE, []))
    captions_on = False
    if opts.captions:
        if inp.rules is not None and inp.rules.captions_allowed is False:
            notes.append("Captions not burned in: campaign prohibits captions.")
        else:
            capv = wd / "video_captions.mp4"
            try:
                captions_on = burn_captions(video, words_out, capv, duration, fps, opts.preset, opts.crf,
                                            lambda f, m: prog(0.64 + 0.2 * f, m))
                if captions_on:
                    capv.replace(video)
            except FFmpegError as e:
                notes.append(f"Captions skipped: {e}")

    # ---- 4. chapters + sound design --------------------------------------------------------------
    chapters = output_chapters(inp.chapters, main, teaser_dur)
    yt_ok = youtube_chapters_valid(chapters, duration)
    if not yt_ok and chapters:
        notes.append("Chapters kept in the file, but YouTube needs >= 3 chapters of >= 10 s to show them.")
    prog(0.86, "Sound design")
    audio, sound_events = _long_sound(inp, opts, voice, wd, duration, teaser_dur, chapters, words_out, notes)

    # ---- 5. mux ------------------------------------------------------------------------------------
    meta_path = wd / "chapters.ffmeta"
    meta_path.write_text(ffmetadata(chapters, duration), encoding="utf-8")
    final = wd / "long.mp4"
    prog(0.9, "Muxing")
    run_ffmpeg(build_mux_cmd(video, audio, meta_path if chapters else None, final, ffmpeg=ff), timeout=3600)

    prog(0.93, "Thumbnails")
    frames, drafts = make_thumbnails(inp.source_path, inp.start, inp.end, inp.thumbnails, wd)
    if not frames:
        notes.append("Could not extract thumbnail frames.")
    prog(1.0, "Rendered")
    return LongRenderResult(
        video=str(final), duration=duration, teaser_duration=teaser_dur, keep_segments=main, removed_silence=removed,
        chapters=chapters, youtube_chapters_ok=yt_ok, srt=srt, clip_text=" ".join(w["word"] for w in clip_words),
        sound_events=sound_events, thumbnail_frames=frames, thumbnail_drafts=drafts, captions_on=captions_on, notes=notes,
    )


def _scaled(prog: ProgressFn, base: float, span: float) -> Callable[[float], None]:
    return lambda f: prog(base + span * f, "Cutting & encoding")


def long_form_events(style: str, duration: float, teaser: float, chapters: list[dict[str, Any]],
                     words: list[dict[str, Any]], available: set[str]) -> list[SoundEvent]:
    """Long-form is watched, not scrolled: the cold open and chapter turns get marked, plus a few moments
    where the words earn a sound. Roughly one accent per minute at Balanced, two at Punchy."""
    events: list[SoundEvent] = []
    if teaser > 0:
        events.append(SoundEvent(teaser, "whoosh", "Cold open into the episode", 9, 1.0, max_active=1.4))
        events.append(SoundEvent(teaser, "impact", "Episode starts", 8, 0.9))
    for ch in chapters:
        if ch["t"] > teaser + 20 and duration - ch["t"] > 10:
            events.append(SoundEvent(ch["t"], "whoosh", f"Chapter: {ch['title']}", 5,
                                     0.7 if style == "subtle" else 0.85, max_active=1.1))
    if style in ("balanced", "punchy"):
        per_minute = 1.0 if style == "balanced" else 2.0
        limit = max(1, int(duration / 60 * per_minute))
        gap = 25.0 if style == "balanced" else 15.0
        for e in keyword_events(words, limit=limit, min_gap=gap, per_category=2 if style == "balanced" else 3,
                                start_after=teaser + 5):
            if all(abs(o.t - e.t) >= 6.0 for o in events):
                events.append(e)
    events = [e for e in events if e.category in available and 0 <= e.t <= duration - 0.3]
    events.sort(key=lambda e: e.t)
    return events


def _long_sound(inp: LongRenderInput, opts: LongRenderOptions, voice: Path, wd: Path, duration: float, teaser: float,
                chapters: list[dict[str, Any]], words: list[dict[str, Any]],
                notes: list[str]) -> tuple[Path, list[dict[str, Any]]]:
    if opts.sound_design == "off":
        return voice, []
    ok, why = sfx_permitted(inp.rules)
    if not ok:
        notes.append(why)
        return voice, []
    try:
        items = sfx_library.scan_library()
    except Exception as e:  # noqa: BLE001
        notes.append(f"Sound design skipped: library unavailable ({e}).")
        return voice, []
    avail = available_categories(items)
    events = long_form_events(opts.sound_design, duration, teaser, chapters, words, avail)
    if not events:
        return voice, []
    style = opts.sound_design if opts.sound_design in STYLES else "subtle"
    placed = assign_files(events, items, duration, seed=inp.seed or "long", style=style, volume=opts.sfx_volume)
    if not placed:
        return voice, []
    out = wd / "audio_sfx.wav"
    try:
        mix_sound_design(voice, placed, out, duration)
    except FFmpegError as e:
        notes.append(f"Sound design skipped: mixing failed ({e}).")
        return voice, []
    notes.append(f"Sound design ({opts.sound_design}): {len(placed)} effect(s).")
    return out, [p.public() for p in placed]
