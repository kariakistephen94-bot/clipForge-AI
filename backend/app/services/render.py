"""Clip rendering engine.

Stage 1 (FFmpeg): cut + join keep-segments, constant fps, processed audio (normalise/denoise/limit/music duck).
Stage 2 (OpenCV/Pillow compositor -> FFmpeg encoder): smart 9:16 framing, punch-ins, B-roll, captions.
Stage 3 (FFmpeg): hook-overlay variants from the captions-only base (no re-compositing), thumbnail.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..schemas.ai import CampaignRules
from . import broll as broll_mod
from . import sfx_library
from .captions import CaptionRenderer, chunks_to_srt, group_words, render_hook_png, style_from
from .ffmpeg import (
    AudioOptions,
    EncodeSettings,
    FFmpegError,
    build_cut_cmd,
    build_decode_cmd,
    build_encode_cmd,
    build_overlay_cmd,
    build_silencedetect_cmd,
    build_thumbnail_cmd,
    even,
    ffmpeg_bin,
    parse_silencedetect,
    run_ffmpeg,
)
from .probe import probe_video
from .reframe import FramingPlan, Keyframe, analyze_framing, build_speech_info, camera_at, crop_rect, shot_at
from .silence import SilenceConfig, map_time_clamped, plan_keep_segments, remap_words, total_duration
from .sound_design import STYLES as SFX_STYLES
from .sound_design import SoundContext, assign_files, available_categories, mix_sound_design, plan_events, sfx_permitted
from .transcribe import words_in_range
from .zoom import emphasis_word_times, plan_zooms, zoom_at

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]


@dataclass
class RenderOptions:
    captions: bool = True
    caption_style: str = "bold_viral"
    caption_overrides: dict[str, Any] = field(default_factory=dict)
    smart_reframe: bool = True
    split_screen: bool = False  # honoured only when the campaign explicitly allows it
    silence_removal: bool = True
    aggressive_silence: bool = False
    auto_zoom: bool = True
    broll_mode: str = "off"  # off | local
    broll_allow_unstated: bool = False
    music_file: str | None = None
    music_volume: float = 0.15
    denoise: bool = True
    normalize_audio: bool = True
    output_resolution: str = "1080x1920"
    output_fps: int = 30
    crf: int = 18
    encoder_preset: str = "veryfast"
    variants: list[str] = field(default_factory=lambda: ["A", "B", "C"])
    hook_seconds: float = 3.5
    sound_design: str = "balanced"  # off | subtle | balanced | punchy
    sfx_volume: float = 1.0
    sfx_playful: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RenderOptions:
        names = {f.name for f in fields(cls)}
        opts = cls(**{k: v for k, v in (d or {}).items() if k in names})
        opts.variants = [v for v in opts.variants if v in ("A", "B", "C")] or ["A"]
        opts.output_fps = 60 if int(opts.output_fps) >= 50 else 30
        if opts.output_resolution not in ("1080x1920", "720x1280"):
            opts.output_resolution = "1080x1920"
        opts.hook_seconds = max(0.0, min(15.0, float(opts.hook_seconds)))
        opts.crf = int(max(14, min(30, int(opts.crf))))
        if opts.encoder_preset not in ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium"):
            opts.encoder_preset = "veryfast"
        if opts.sound_design not in SFX_STYLES:
            opts.sound_design = "off"
        opts.sfx_volume = max(0.0, min(2.0, float(opts.sfx_volume)))
        return opts

    @property
    def size(self) -> tuple[int, int]:
        w, h = self.output_resolution.split("x")
        return int(w), int(h)


@dataclass
class RenderInput:
    source_path: str
    src_width: int
    src_height: int
    has_audio: bool
    start: float
    end: float
    words: list[dict[str, Any]]
    emphasis: list[str]
    zoom_points: list[tuple[float, str]]
    broll_points: list[dict[str, Any]]
    hooks: list[str]  # ordered: [variant A hook, variant B hook, ...]
    rules: CampaignRules | None
    workdir: Path
    sound_cues: list[dict[str, Any]] = field(default_factory=list)  # AI cues, absolute source seconds
    humorous: bool = False
    seed: str = ""


@dataclass
class RenderResult:
    variants: dict[str, str]
    base_path: str
    duration: float
    keep_segments: list[tuple[float, float]]
    removed_silence: list[dict[str, Any]]
    framing: dict[str, Any]
    split_used: bool
    broll_used: list[dict[str, Any]]
    music_used: bool
    captions_on: bool
    srt: str
    clip_text: str
    zoom_events: list[dict[str, Any]]
    thumbnail: str
    notes: list[str]
    sound_events: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- compositing helpers


def compose_wide(frame: np.ndarray, out_w: int, out_h: int, zoom: float) -> np.ndarray:
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (max(16, w // 10), max(16, h // 10)), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    cw = max(4, int(sh * out_w / out_h))
    x0 = max(0, (sw - cw) // 2)
    bg = cv2.GaussianBlur(small[:, x0 : x0 + cw], (0, 0), 2.5)
    bg = cv2.resize(bg, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    out = cv2.convertScaleAbs(bg, alpha=0.5)
    fw = int(out_w * zoom)
    fh = int(h * fw / w)
    fg = cv2.resize(frame, (fw, fh), interpolation=cv2.INTER_AREA if fw < w else cv2.INTER_LINEAR)
    if fw > out_w:
        xo = (fw - out_w) // 2
        fg = fg[:, xo : xo + out_w]
    y = int(out_h * 0.42 - fh / 2)
    y = max(0, min(out_h - fh, y))
    out[y : y + fg.shape[0], : fg.shape[1]] = fg
    return out


def compose_split(frame: np.ndarray, targets: list[Keyframe], out_w: int, out_h: int, zoom: float) -> np.ndarray:
    h, w = frame.shape[:2]
    half = out_h // 2
    parts = []
    for tgt in targets[:2]:
        ch = min(h, max(tgt.face_h * 3.2, h * 0.45)) / max(1.0, zoom)
        cw = ch * out_w / half
        if cw > w:
            cw = w
            ch = cw * half / out_w
        x = int(min(max(tgt.cx - cw / 2, 0), w - cw))
        y = int(min(max(tgt.cy + ch * 0.1 - ch / 2, 0), h - ch))
        region = frame[y : y + int(ch), x : x + int(cw)]
        parts.append(cv2.resize(region, (out_w, half), interpolation=cv2.INTER_LINEAR))
    out = np.vstack(parts)
    if out.shape[0] < out_h:
        out = np.vstack([out, np.zeros((out_h - out.shape[0], out_w, 3), np.uint8)])
    cv2.line(out, (0, half), (out_w, half), (0, 0, 0), 4)
    return out


def cover_resize(frame: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    h, w = frame.shape[:2]
    s = max(out_w / w, out_h / h)
    nw, nh = int(np.ceil(w * s)), int(np.ceil(h * s))
    r = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    x, y = (nw - out_w) // 2, (nh - out_h) // 2
    return r[y : y + out_h, x : x + out_w]


class BrollPlayer:
    def __init__(self, events: list[dict[str, Any]]):
        self.events = events
        self.cap: cv2.VideoCapture | None = None
        self.cur: dict[str, Any] | None = None
        self.last: np.ndarray | None = None

    def frame_at(self, t: float, out_w: int, out_h: int) -> np.ndarray | None:
        ev = next((e for e in self.events if e["start"] <= t < e["end"]), None)
        if ev is None:
            if self.cap is not None:
                self.cap.release()
                self.cap, self.cur = None, None
            return None
        if ev is not self.cur:
            if self.cap is not None:
                self.cap.release()
            self.cap = cv2.VideoCapture(ev["path"])
            self.cur = ev
            self.last = None
        assert self.cap is not None
        rel_ms = (t - ev["start"]) * 1000
        while self.cap.isOpened() and (self.last is None or self.cap.get(cv2.CAP_PROP_POS_MSEC) < rel_ms):
            ok, fr = self.cap.read()
            if not ok:
                break
            self.last = fr
        if self.last is None:
            return None
        return cover_resize(self.last, out_w, out_h)

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()


# --------------------------------------------------------------------------- main entry


def render_clip(inp: RenderInput, opts: RenderOptions, progress: ProgressFn | None = None) -> RenderResult:
    prog = progress or (lambda f, m: None)
    wd = inp.workdir
    wd.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    rules = inp.rules
    out_w, out_h = opts.size
    ff = ffmpeg_bin()

    # ---- 1. plan segments (silence) --------------------------------------------------
    clip_words = words_in_range(inp.words, inp.start, inp.end)
    scfg = SilenceConfig(enabled=opts.silence_removal and inp.has_audio and bool(clip_words),
                         aggressive=opts.aggressive_silence)
    confirmed = None
    if scfg.enabled:
        try:
            stderr = run_ffmpeg(build_silencedetect_cmd(inp.source_path, inp.start, inp.end, -35, 0.3, ffmpeg=ff),
                                capture_stderr=True, timeout=300)
            confirmed = parse_silencedetect(stderr, offset=inp.start, clip_end=inp.end)
        except FFmpegError as e:
            notes.append(f"Silence detection skipped: {e}")
            scfg.enabled = False
    segments, removed = plan_keep_segments(clip_words, inp.start, inp.end, scfg, confirmed, inp.emphasis)
    duration = total_duration(segments)
    if removed:
        notes.append(f"Removed {sum(r['seconds'] for r in removed):.1f}s of dead air in {len(removed)} place(s).")

    # ---- 2. cut + audio ---------------------------------------------------------------
    music_used = False
    music_path = None
    if opts.music_file:
        if rules is not None and rules.music_allowed is False:
            notes.append("Background music skipped: campaign prohibits music.")
        elif Path(opts.music_file).is_file():
            music_path = opts.music_file
            music_used = True
        else:
            notes.append("Background music file not found; skipped.")
    landscape = inp.src_width > inp.src_height
    inter_h = min(inp.src_height, 1440 if landscape else 1920)
    cut_video, cut_audio = wd / "cut.mp4", wd / "audio.wav"
    prog(0.02, "Cutting source")
    run_ffmpeg(
        build_cut_cmd(inp.source_path, segments, cut_video, cut_audio, fps=opts.output_fps, out_height=inter_h,
                      has_audio=inp.has_audio,
                      audio=AudioOptions(normalize=opts.normalize_audio, denoise=opts.denoise, music_path=music_path,
                                         music_volume=opts.music_volume), ffmpeg=ff),
        duration=duration, on_progress=lambda f: prog(0.02 + 0.16 * f, "Cutting source"),
    )
    meta = probe_video(cut_video)
    W, H = meta.width, meta.height

    # ---- 3. framing -------------------------------------------------------------------
    split_ok = bool(opts.split_screen and rules is not None and rules.split_screen_allowed is True)
    if opts.split_screen and not split_ok:
        notes.append("Split screen not used: campaign rules don't explicitly allow it.")
    prog(0.2, "Analyzing framing")
    # Who is talking when: the clip's word timings on the cut timeline, plus its audio (voice identity).
    speech = build_speech_info(remap_words(clip_words, segments), cut_audio if inp.has_audio else None, duration,
                               identify_voices=not music_used)
    plan: FramingPlan = analyze_framing(cut_video, W, H, duration, smart=opts.smart_reframe, split_allowed=split_ok,
                                        speech=speech,
                                        on_progress=lambda f: prog(0.2 + 0.12 * f, "Analyzing framing"))
    notes += plan.notes

    # ---- 4. captions / zoom / b-roll plans ------------------------------------------------
    words_out = remap_words(clip_words, segments)
    style = style_from(opts.caption_style, opts.caption_overrides)
    captions_on = opts.captions
    if captions_on and rules is not None and rules.captions_allowed is False:
        captions_on = False
        notes.append("Captions not burned in: campaign prohibits captions.")
    chunks = group_words(words_out, style, inp.emphasis)
    renderer = CaptionRenderer(chunks, style, out_w, out_h) if captions_on and chunks else None
    if captions_on and not chunks:
        notes.append("No speech in this clip: no captions.")

    ai_points = [(map_time_clamped(t, segments), k) for t, k in inp.zoom_points if inp.start <= t <= inp.end]
    zooms = plan_zooms(duration, words_out, ai_points, emphasis_word_times(words_out, inp.emphasis), plan.cuts,
                       enabled=opts.auto_zoom)

    broll_events: list[dict[str, Any]] = []
    allowed, why = broll_mod.broll_permitted(rules, opts.broll_mode, opts.broll_allow_unstated)
    if opts.broll_mode == "local":
        if not allowed:
            notes.append(why)
        else:
            items = broll_mod.index_broll()
            pts = [{**p, "timestamp": map_time_clamped(p["timestamp"], segments)} for p in inp.broll_points
                   if inp.start <= p["timestamp"] <= inp.end]
            broll_events = broll_mod.plan_broll(pts, duration, items)
            if not items:
                notes.append("B-roll enabled but workspace/broll/ has no video files.")
            elif pts and not broll_events:
                notes.append("No local B-roll matched the AI suggestions.")

    # ---- 4b. sound design ------------------------------------------------------------------
    hooks = [h for h in inp.hooks if h and h.strip()]
    hook_variants = bool(hooks) and any(v in opts.variants for v in ("A", "B"))
    prog(0.325, "Sound design")
    base_audio, hook_audio, sound_events = design_sound(
        inp, opts, cut_audio, wd, duration, segments, words_out,
        zooms=[(z.start, z.kind) for z in zooms], broll_starts=[e["start"] for e in broll_events],
        hook=hook_variants, notes=notes)

    # ---- 5. composite + encode base (captions only = variant C) ----------------------------
    base = wd / "base.mp4"
    enc = EncodeSettings(width=out_w, height=out_h, fps=opts.output_fps, crf=opts.crf, preset=opts.encoder_preset)
    dec = subprocess.Popen(build_decode_cmd(cut_video, W, H, ffmpeg=ff), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with tempfile.TemporaryFile() as enc_err:
        encp = subprocess.Popen(build_encode_cmd(base_audio, base, enc, ffmpeg=ff), stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=enc_err)
        player = BrollPlayer(broll_events)
        total_frames = max(1, int(round(duration * opts.output_fps)))
        frame_bytes = W * H * 3
        i = 0
        assert dec.stdout is not None and encp.stdin is not None
        try:
            while True:
                buf = dec.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    break
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3)
                t = i / opts.output_fps
                z = zoom_at(t, zooms)
                shot = shot_at(plan, t)
                if shot.mode == "wide":
                    out = compose_wide(frame, out_w, out_h, z)
                elif shot.mode == "split" and len(shot.split_targets) >= 2:
                    out = compose_split(frame, shot.split_targets, out_w, out_h, z)
                else:
                    cam = camera_at(shot, t)
                    x, y, cw, ch = crop_rect(W, H, cam, z, out_w / out_h, has_face=shot.mode == "track")
                    region = frame[y : y + ch, x : x + cw]
                    interp = cv2.INTER_AREA if cw > out_w else cv2.INTER_LINEAR
                    out = cv2.resize(region, (out_w, out_h), interpolation=interp)
                b = player.frame_at(t, out_w, out_h) if broll_events else None
                if b is not None:
                    out = np.ascontiguousarray(b)
                if renderer is not None:
                    renderer.overlay(out, t)
                encp.stdin.write(out.tobytes())
                i += 1
                if i % 15 == 0:
                    prog(0.33 + 0.55 * min(1.0, i / total_frames), "Compositing & encoding")
        except BrokenPipeError:
            pass
        finally:
            player.close()
            try:
                encp.stdin.close()
            except BrokenPipeError:
                pass
            dec.stdout.close()
            dec.wait(timeout=30)
            rc = encp.wait(timeout=600)
        if rc != 0 or i == 0:
            enc_err.seek(0)
            tail = enc_err.read().decode("utf-8", "replace")[-1500:]
            raise FFmpegError(f"Encoding failed (exit {rc}, frames {i}): {tail.strip().splitlines()[-1] if tail.strip() else 'no output'}", tail)

    # ---- 6. variants ------------------------------------------------------------------------
    variants: dict[str, str] = {}
    show_until = duration if opts.hook_seconds <= 0 else min(opts.hook_seconds, max(0.5, duration - 0.3))
    for vi, v in enumerate(sorted(set(opts.variants))):
        prog(0.9 + 0.08 * vi / 3, f"Creating variant {v}")
        if v == "C":
            variants["C"] = str(base)
            continue
        hook = hooks[0] if v == "A" else (hooks[1] if len(hooks) > 1 else None)
        if not hook:
            notes.append(f"Variant {v} skipped: no {'hook' if v == 'A' else 'alternative hook'} text.")
            continue
        png = wd / f"hook_{v}.png"
        render_hook_png(hook, str(png), out_w, out_h)
        vout = wd / f"variant_{v}.mp4"
        run_ffmpeg(build_overlay_cmd(base, png, vout, fps=opts.output_fps, show_until=show_until, crf=opts.crf,
                                     preset=opts.encoder_preset, audio=hook_audio, ffmpeg=ff), timeout=1800)
        variants[v] = str(vout)
    if not variants:
        variants["C"] = str(base)

    primary = variants.get("A") or variants.get("B") or variants["C"]
    thumb = wd / "thumbnail.jpg"
    try:
        run_ffmpeg(build_thumbnail_cmd(primary, min(1.2, duration * 0.3), thumb, ffmpeg=ff), timeout=120)
    except FFmpegError as e:
        notes.append(f"Thumbnail failed: {e}")
    prog(1.0, "Rendered")

    return RenderResult(
        variants=variants, base_path=str(base), duration=duration, keep_segments=segments, removed_silence=removed,
        framing=plan.to_json(), split_used="split" in plan.modes, broll_used=broll_events, music_used=music_used,
        captions_on=captions_on and renderer is not None, srt=chunks_to_srt(chunks),
        clip_text=" ".join(w["word"] for w in clip_words),
        zoom_events=[{"start": z.start, "end": z.end, "scale": z.scale, "kind": z.kind} for z in zooms],
        thumbnail=str(thumb) if thumb.exists() else "", notes=notes, sound_events=sound_events,
    )


def jump_cut_times(segments: list[tuple[float, float]]) -> list[float]:
    """Output-timeline times where two kept segments are joined."""
    out, acc = [], 0.0
    for s, e in segments[:-1]:
        acc += e - s
        out.append(round(acc, 3))
    return out


def design_sound(inp: RenderInput, opts: RenderOptions, voice: Path, wd: Path, duration: float,
                 segments: list[tuple[float, float]], words_out: list[dict[str, Any]], *, zooms: list[tuple[float, str]],
                 broll_starts: list[float], hook: bool, notes: list[str]) -> tuple[Path, Path | None, list[dict[str, Any]]]:
    """Plan + mix effects. Returns (audio for the no-hook base, audio for hook variants or None, events)."""
    if opts.sound_design == "off":
        return voice, None, []
    ok, why = sfx_permitted(inp.rules)
    if not ok:
        notes.append(why)
        return voice, None, []
    try:
        items = sfx_library.scan_library()
    except Exception as e:  # noqa: BLE001 - a broken library must never stop a render
        log.warning("SFX library unavailable: %s", e)
        notes.append(f"Sound design skipped: library unavailable ({e}).")
        return voice, None, []
    avail = available_categories(items)
    if not avail:
        notes.append("Sound design skipped: no sound effects found (Settings -> Sound design -> library folder).")
        return voice, None, []
    cues = []
    for c in inp.sound_cues:
        ts = float(c.get("timestamp", -1))
        if inp.start <= ts <= inp.end:
            cues.append({**c, "t": map_time_clamped(ts, segments)})
    ctx = SoundContext(duration=duration, words=words_out, emphasis=inp.emphasis, zooms=zooms, broll_starts=broll_starts,
                       jump_cuts=jump_cut_times(segments), ai_cues=cues, hook=hook, humorous=inp.humorous,
                       playful=opts.sfx_playful, style=opts.sound_design, available=avail)
    placed = assign_files(plan_events(ctx), items, duration, seed=inp.seed or f"{inp.start:.2f}",
                          style=opts.sound_design, volume=opts.sfx_volume)
    if not placed:
        return voice, None, []
    base_audio, hook_audio = voice, None
    try:
        main = [p for p in placed if not p.hook_only]
        if main:
            base_audio = wd / "audio_sfx.wav"
            mix_sound_design(voice, main, base_audio, duration)
        if any(p.hook_only for p in placed):
            hook_audio = wd / "audio_sfx_hook.wav"
            mix_sound_design(voice, placed, hook_audio, duration)
    except FFmpegError as e:
        notes.append(f"Sound design skipped: mixing failed ({e}).")
        return voice, None, []
    notes.append(f"Sound design ({opts.sound_design}): {len(placed)} effect(s).")
    return base_audio, hook_audio, [p.public() for p in placed]


def even_size(w: int, h: int) -> tuple[int, int]:
    return even(w), even(h)
