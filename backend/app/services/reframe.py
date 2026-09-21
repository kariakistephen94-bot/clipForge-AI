"""Speaker-aware 9:16 reframing: audio-visual active speaker detection, fully local (OpenCV + the clip's audio).

Analysis pass (~12 fps, 768 px):
  * shot cuts: colour/brightness histogram jumps plus spikes in frame difference, so cuts in dark,
    single-palette studio footage are found too
  * faces: OpenCV YuNet with 5 landmarks, linked one-to-one into tracks across frames
  * lip activity per face: motion of a landmark-aligned mouth patch minus the motion of the eye region,
    so head turns and nods don't count as talking
Active speaker detection:
  * speech turns come from the word timings (or the loudness envelope when there is no transcript)
  * every visible face is scored per turn: lip activity above that face's own resting level, plus how well
    its lip motion follows the loudness of the voice
  * a Viterbi pass over the turns picks one speaker per turn with a switching cost, so the camera cuts to
    whoever is talking at the start of their turn and ignores short backchannels ("yeah", "right")
Per shot the mode is:
  track  - follow one subject, cutting between speakers (or keep a group that fits in the frame together)
  split  - top/bottom arrangement (ONLY when the campaign explicitly allows split screen)
  wide   - fit the full frame over a blurred fill (several people and nobody identifiable as the speaker)
  center - fallback when no faces are found
Camera motion uses a dead zone + eased moves, and never interpolates across a shot cut or a speaker switch.
"""

from __future__ import annotations

import logging
import subprocess
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import MODELS_DIR
from .ffmpeg import build_decode_cmd, even, ffmpeg_bin

log = logging.getLogger(__name__)

YUNET_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
OUT_ASPECT = 9 / 16
ANALYSIS_LONG_SIDE = 768
SAMPLE_FPS = 12.0


@dataclass
class Keyframe:
    t: float
    cx: float
    cy: float
    face_h: float = 0.0


@dataclass
class ShotPlan:
    start: float
    end: float
    mode: str  # track | wide | split | center
    keyframes: list[Keyframe] = field(default_factory=list)
    split_targets: list[Keyframe] = field(default_factory=list)
    note: str = ""
    speakers: list[tuple[float, int]] = field(default_factory=list)  # (from time, track number) when cutting


@dataclass
class FramingPlan:
    src_w: int
    src_h: int
    shots: list[ShotPlan]
    cuts: list[float]
    face_ratio: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def modes(self) -> set[str]:
        return {s.mode for s in self.shots}

    def to_json(self) -> dict[str, Any]:
        return {
            "src": [self.src_w, self.src_h],
            "cuts": [round(c, 2) for c in self.cuts],
            "face_ratio": round(self.face_ratio, 2),
            "notes": self.notes,
            "shots": [{"start": round(s.start, 2), "end": round(s.end, 2), "mode": s.mode, "note": s.note,
                       "keyframes": len(s.keyframes),
                       "speakers": [[round(t, 2), k] for t, k in s.speakers]} for s in self.shots],
        }


class FaceDetector:
    def __init__(self, input_size: tuple[int, int]):
        self.ok = False
        self.detector = None
        if YUNET_PATH.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.detector = cv2.FaceDetectorYN.create(str(YUNET_PATH), "", input_size, 0.6, 0.3, 50)
                self.ok = True
            except cv2.error as e:  # pragma: no cover
                log.warning("YuNet init failed: %s", e)

    def detect(self, frame: np.ndarray) -> np.ndarray:
        if not self.ok or self.detector is None:
            return np.zeros((0, 15), dtype=np.float32)
        _, faces = self.detector.detect(frame)
        return faces if faces is not None else np.zeros((0, 15), dtype=np.float32)


# --------------------------------------------------------------------------- speech (audio side)


@dataclass
class SpeechInfo:
    """When someone is talking, on the clip's own timeline."""

    turns: list[tuple[float, float]] = field(default_factory=list)
    env_t: np.ndarray | None = None  # loudness envelope sample times (s)
    env: np.ndarray | None = None  # loudness 0..1
    voices: list[int] = field(default_factory=list)  # which voice (speaker cluster) each turn is; -1 = unknown

    def loudness(self, times: np.ndarray) -> np.ndarray | None:
        if self.env is None or self.env_t is None or len(self.env) < 2:
            return None
        return np.interp(times, self.env_t, self.env)


def speech_turns(words: Sequence[dict[str, Any]], max_gap: float = 0.3, target: float = 3.0,
                 max_len: float = 6.0) -> list[tuple[float, float]]:
    """Group words into short speech turns: split at pauses, and split long stretches at word gaps so a
    speaker change without a pause is still caught within a few seconds."""
    turns: list[tuple[float, float]] = []
    cur: list[float] | None = None
    for w in sorted(words, key=lambda w: float(w["start"])):
        s, e = float(w["start"]), float(w["end"])
        if e <= s:
            continue
        if cur is None:
            cur = [s, e]
            continue
        gap, length = s - cur[1], cur[1] - cur[0]
        if gap >= max_gap or (length >= target and gap >= 0.08) or length >= max_len:
            turns.append((cur[0], cur[1]))
            cur = [s, e]
        else:
            cur[1] = max(cur[1], e)
    if cur is not None:
        turns.append((cur[0], cur[1]))
    return turns


def _read_wav(audio_path: str | Path, max_seconds: float = 1200) -> tuple[np.ndarray, int] | None:
    try:
        with wave.open(str(audio_path), "rb") as wf:
            rate, ch, width, n = wf.getframerate(), wf.getnchannels(), wf.getsampwidth(), wf.getnframes()
            raw = wf.readframes(min(n, int(rate * max_seconds)))
    except (OSError, wave.Error, EOFError) as e:
        log.info("Clip audio unreadable (%s)", e)
        return None
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None or not raw:
        return None
    a = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if width == 1:
        a -= 128
    if ch > 1:
        a = a[: len(a) // ch * ch].reshape(-1, ch).mean(axis=1)
    return a, rate


def _envelope(a: np.ndarray, rate: int, hop: float = 0.02) -> tuple[np.ndarray, np.ndarray] | None:
    step = max(1, int(rate * hop))
    frames = len(a) // step
    if frames < 3:
        return None
    rms = np.sqrt(np.mean(a[: frames * step].reshape(frames, step) ** 2, axis=1) + 1e-9)
    db = 20 * np.log10(rms + 1e-6)
    lo, hi = np.percentile(db, 10), np.percentile(db, 97)
    env = np.clip((db - lo) / max(6.0, hi - lo), 0, 1)
    return (np.arange(frames) + 0.5) * hop, env


def _mel_filters(rate: int, n_fft: int, n_mels: int = 32, fmin: float = 80.0, fmax: float = 7600.0) -> np.ndarray:
    def mel(f: np.ndarray | float) -> np.ndarray:
        return 2595 * np.log10(1 + np.asarray(f) / 700)

    fmax = min(fmax, rate / 2 - 100)
    pts = 700 * (10 ** (np.linspace(mel(fmin), mel(fmax), n_mels + 2) / 2595) - 1)
    bins = np.floor((n_fft + 1) * pts / rate).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(1, n_mels + 1):
        lo, c, hi = bins[m - 1], bins[m], bins[m + 1]
        if c > lo:
            fb[m - 1, lo:c] = (np.arange(lo, c) - lo) / (c - lo)
        if hi > c:
            fb[m - 1, c:hi] = (hi - np.arange(c, hi)) / (hi - c)
    return fb


def _pitch(seg: np.ndarray, rate: int) -> float | None:
    """Median fundamental frequency (Hz) of the voiced frames, by normalised autocorrelation."""
    n = int(0.04 * rate)
    hop = int(0.01 * rate)
    if len(seg) < n + hop * 10:
        return None
    idx = np.arange(n)[None, :] + hop * np.arange((len(seg) - n) // hop)[:, None]
    frames = seg[idx] - seg[idx].mean(axis=1, keepdims=True)
    energy = (frames ** 2).sum(axis=1)
    frames = frames[energy >= np.percentile(energy, 50)] * np.hanning(n)
    spec = np.fft.rfft(frames, 2 * n, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :n]
    ac /= ac[:, :1] + 1e-9
    lo, hi = int(rate / 400), int(rate / 70)
    lags = lo + np.argmax(ac[:, lo:hi], axis=1)
    peaks = ac[np.arange(len(ac)), lags]
    good = lags[peaks > 0.45]
    if len(good) < 5:
        return None
    return float(np.median(rate / good))


def voice_features(a: np.ndarray, rate: int, turns: list[tuple[float, float]]) -> list[np.ndarray | None]:
    """A fingerprint of the voice in each turn: its pitch (the strongest cue for telling two people apart) and
    the shape of its spectrum (loudness removed). None when the turn has no usable voiced audio."""
    n_fft = 1 << int(np.ceil(np.log2(0.025 * rate)))
    hop = int(0.01 * rate)
    fb = _mel_filters(rate, n_fft)
    win = np.hanning(n_fft).astype(np.float32)
    out: list[np.ndarray | None] = []
    for a0, b0 in turns:
        seg = a[int(a0 * rate) : int(b0 * rate)]
        f0 = _pitch(seg, rate) if len(seg) else None
        if f0 is None or len(seg) < n_fft + hop * 20:
            out.append(None)
            continue
        idx = np.arange(n_fft)[None, :] + hop * np.arange((len(seg) - n_fft) // hop)[:, None]
        spec = np.abs(np.fft.rfft(seg[idx] * win, axis=1)) ** 2
        logmel = np.log(spec @ fb.T + 1e-3)
        energy = logmel.mean(axis=1)
        shape = logmel[energy >= np.percentile(energy, 40)].mean(axis=0)
        shape -= shape.mean()
        out.append(np.concatenate([[np.log2(f0) / PITCH_STEP], shape * SHAPE_WEIGHT / np.sqrt(len(shape))]))
    return out


# Voice fingerprint scale: a pitch difference of PITCH_STEP octaves counts as one unit of distance; turns more
# than VOICE_THRESHOLD units apart (on average) are different people.
PITCH_STEP = 0.35
SHAPE_WEIGHT = 0.3
VOICE_THRESHOLD = 1.0


def cluster_voices(feats: list[np.ndarray | None], durations: Sequence[float], threshold: float | None = None,
                   max_voices: int = 4) -> list[int]:
    """Group turns by voice: average-linkage clustering of the fingerprints of the longer turns; short turns and
    tiny clusters (a laugh, one word) then join the nearest real voice. Returns a voice label per turn, -1 when
    the turn had no usable audio."""
    threshold = VOICE_THRESHOLD if threshold is None else threshold
    usable = [i for i, f in enumerate(feats) if f is not None]
    if len(usable) < 2:
        return [0 if f is not None else -1 for f in feats]
    X = np.array([feats[i] for i in usable])
    long_ = [j for j, i in enumerate(usable) if durations[i] >= 0.8] or list(range(len(usable)))
    clusters = [[j] for j in long_]

    def dist(c1: list[int], c2: list[int]) -> float:
        return float(np.mean([np.linalg.norm(X[p] - X[q]) for p in c1 for q in c2]))

    while len(clusters) > 1:
        best, pair = np.inf, (0, 0)
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                d = dist(clusters[x], clusters[y])
                if d < best:
                    best, pair = d, (x, y)
        if best > threshold and len(clusters) <= max_voices:
            break
        x, y = pair
        clusters[x] += clusters.pop(y)
    clusters.sort(key=lambda c: -sum(durations[usable[j]] for j in c))
    big = [c for c in clusters if sum(durations[usable[j]] for j in c) >= 2.0] or clusters[:1]
    labels = [-1] * len(feats)
    for j, i in enumerate(usable):
        labels[i] = int(np.argmin([np.mean([np.linalg.norm(X[j] - X[q]) for q in c]) for c in big]))
    return labels


def _turns_from_envelope(env_t: np.ndarray, env: np.ndarray, duration: float) -> list[tuple[float, float]]:
    voiced = env > 0.45
    turns: list[tuple[float, float]] = []
    start = None
    for t, v in zip(env_t, voiced, strict=False):
        if v and start is None:
            start = float(t)
        elif not v and start is not None:
            if turns and start - turns[-1][1] < 0.3:
                turns[-1] = (turns[-1][0], float(t))
            else:
                turns.append((start, float(t)))
            start = None
    if start is not None:
        turns.append((start, duration))
    out: list[tuple[float, float]] = []
    for s, e in turns:
        if e - s < 0.2:
            continue
        while e - s > 4.0:  # no word gaps to split on: cut long stretches into ~3 s pieces
            out.append((s, s + 3.0))
            s += 3.0
        out.append((s, e))
    return out


def build_speech_info(words: Sequence[dict[str, Any]] | None, audio_path: str | Path | None,
                      duration: float, identify_voices: bool = True) -> SpeechInfo:
    """Speech turns (from word timings, else the audio), the loudness envelope, and which voice speaks each turn.
    identify_voices=False when the audio has music mixed in, which would blur the voice fingerprints."""
    info = SpeechInfo(turns=speech_turns(words or []))
    audio = _read_wav(audio_path) if audio_path else None
    env = _envelope(*audio) if audio is not None else None
    if env is not None:
        info.env_t, info.env = env
        if not info.turns:
            info.turns = _turns_from_envelope(env[0], env[1], duration)
    info.voices = [-1] * len(info.turns)
    if audio is not None and info.turns and identify_voices:
        feats = voice_features(audio[0], audio[1], info.turns)
        info.voices = cluster_voices(feats, [b - a for a, b in info.turns])
    return info


# --------------------------------------------------------------------------- faces (video side)


@dataclass
class _Obs:
    t: float
    x: float
    y: float
    w: float
    h: float
    mouth: np.ndarray | None
    ref: np.ndarray | None = None
    energy: float = float("nan")


@dataclass
class _Track:
    obs: list[_Obs] = field(default_factory=list)

    @property
    def last(self) -> _Obs:
        return self.obs[-1]

    def median_box(self) -> tuple[float, float, float, float]:
        arr = np.array([[o.x, o.y, o.w, o.h] for o in self.obs])
        return tuple(np.median(arr, axis=0))  # type: ignore[return-value]

    def near(self, t: float) -> _Obs:
        return min(self.obs, key=lambda o: abs(o.t - t))


def _hist(frame: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [24, 12], [0, 180, 0, 256])
    cv2.normalize(h, h)
    return h


def _vhist(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h = cv2.calcHist([gray], [0], None, [32], [0, 256])
    cv2.normalize(h, h)
    return h


def _thumb(frame: np.ndarray) -> np.ndarray:
    return cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)


def _norm_patch(gray: np.ndarray, cx: float, cy: float, w: float, h: float, size: tuple[int, int]) -> np.ndarray | None:
    x0, y0, x1, y1 = int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2)
    H, W = gray.shape[:2]
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H or x1 - x0 < 6 or y1 - y0 < 4:
        return None
    p = cv2.resize(gray[y0:y1, x0:x1], size, interpolation=cv2.INTER_AREA).astype(np.float32)
    return (p - p.mean()) / (p.std() + 8.0)  # contrast-normalised: lighting changes don't look like motion


def _face_patches(gray: np.ndarray, face: np.ndarray) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Landmark-aligned mouth patch (lips + jaw) and an eye-region reference patch."""
    w, h = float(face[2]), float(face[3])
    rex, rey, lex, ley = (float(v) for v in face[4:8])
    rmx, rmy, lmx, lmy = (float(v) for v in face[10:14])
    mw = max(abs(lmx - rmx) * 1.8, w * 0.45)
    mouth = _norm_patch(gray, (rmx + lmx) / 2, (rmy + lmy) / 2 + h * 0.05, mw, mw * 0.6, (32, 18))
    ew = max(abs(lex - rex) * 1.8, w * 0.6)
    ref = _norm_patch(gray, (rex + lex) / 2, (rey + ley) / 2 + h * 0.05, ew, ew * 0.45, (32, 14))
    return mouth, ref


def detect_shot_cuts(hists: list[np.ndarray], times: list[float], threshold: float = 0.45, min_shot: float = 0.6,
                     vhists: list[np.ndarray] | None = None, thumbs: list[np.ndarray] | None = None) -> list[float]:
    """Hard cuts: a big colour/brightness histogram jump, or a one-frame spike in pixel difference that stands
    out from the motion around it (camera switches in dark studios barely change the histograms)."""
    n = len(hists)
    dh = np.zeros(n)
    dpix = np.zeros(n)
    for i in range(1, n):
        d = cv2.compareHist(hists[i - 1], hists[i], cv2.HISTCMP_BHATTACHARYYA)
        if vhists is not None:
            d = max(d, cv2.compareHist(vhists[i - 1], vhists[i], cv2.HISTCMP_BHATTACHARYYA))
        dh[i] = d
        if thumbs is not None:
            dpix[i] = float(np.mean(np.abs(thumbs[i] - thumbs[i - 1]))) / 255.0
    cuts: list[float] = []
    for i in range(1, n):
        is_cut = dh[i] > threshold
        if not is_cut and thumbs is not None and dpix[i] > 0.035:
            around = np.concatenate([dpix[max(1, i - 6) : i], dpix[i + 1 : i + 7]])
            local = float(np.median(around)) if len(around) else 0.0
            is_cut = dpix[i] > max(0.035, 4.0 * local) and dh[i] > 0.08
        if is_cut and (not cuts or times[i] - cuts[-1] >= min_shot) and times[i] >= min_shot:
            cuts.append(times[i])
    return cuts


def smooth_path(samples: list[tuple[float, float, float, float]], crop_w: float, shot_start: float,
                deadzone_frac: float = 0.11, hold: float = 0.5) -> list[Keyframe]:
    """samples: (t, cx, cy, face_h). Dead-zone camera with eased moves; returns keyframes.

    The analysis is offline, so a move starts when the subject started drifting (not `hold` seconds later)."""
    if not samples:
        return []
    xs = np.array([s[1] for s in samples])
    k = 5 if len(xs) >= 5 else 1
    if k > 1:
        pad = np.pad(xs, (k // 2, k // 2), mode="edge")
        xs = np.array([np.median(pad[i : i + k]) for i in range(len(samples))])
    cam_x = float(xs[0])
    cam_y, cam_fh = samples[0][2], samples[0][3]
    kfs = [Keyframe(shot_start, cam_x, cam_y, cam_fh)]
    dz = crop_w * deadzone_frac
    pending_since: float | None = None
    for i, (t, _x, y, fh) in enumerate(samples):
        x = float(xs[i])
        if abs(x - cam_x) > dz:
            if pending_since is None:
                pending_since = t
            elif t - pending_since >= hold:
                move = float(np.clip(abs(x - cam_x) / crop_w * 1.2, 0.35, 0.9))
                t0 = max(pending_since, kfs[-1].t + 0.05)
                kfs.append(Keyframe(t0, cam_x, cam_y, cam_fh))
                cam_x, cam_y, cam_fh = x, y, fh
                kfs.append(Keyframe(t0 + move, cam_x, cam_y, cam_fh))
                pending_since = None
        else:
            pending_since = None
    return kfs


def analyze_framing(
    video_path: str | Path,
    src_w: int,
    src_h: int,
    duration: float,
    *,
    smart: bool = True,
    split_allowed: bool = False,
    speech: SpeechInfo | None = None,
    sample_fps: float = SAMPLE_FPS,
    on_progress: Callable[[float], None] | None = None,
) -> FramingPlan:
    if src_w / src_h <= OUT_ASPECT + 0.02:
        return FramingPlan(src_w, src_h, [ShotPlan(0, duration, "center", [Keyframe(0, src_w / 2, src_h / 2)],
                                                   note="source already vertical")], [], notes=["Vertical source: no reframing needed."])
    if not smart:
        return FramingPlan(src_w, src_h, [ShotPlan(0, duration, "center", [Keyframe(0, src_w / 2, src_h / 2)],
                                                   note="smart reframing off")], [], notes=["Smart reframing disabled: center crop."])

    scale = min(1.0, ANALYSIS_LONG_SIDE / max(src_w, src_h))
    aw, ah = even(src_w * scale), even(src_h * scale)
    sx, sy = src_w / aw, src_h / ah
    detector = FaceDetector((aw, ah))
    notes: list[str] = []
    if not detector.ok:
        notes.append("Face model unavailable: using center crop. Run start.sh to download it.")

    proc = subprocess.Popen(build_decode_cmd(video_path, aw, ah, fps=sample_fps, ffmpeg=ffmpeg_bin()),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = aw * ah * 3
    times: list[float] = []
    hists: list[np.ndarray] = []
    vhists: list[np.ndarray] = []
    thumbs: list[np.ndarray] = []
    frame_faces: list[list[_Obs]] = []
    edges: list[np.ndarray] = []
    motion: list[np.ndarray] = []
    prev_small: np.ndarray | None = None
    idx = 0
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(ah, aw, 3)
            t = idx / sample_fps
            times.append(t)
            hists.append(_hist(frame))
            vhists.append(_vhist(frame))
            thumbs.append(_thumb(frame))
            small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (PROFILE_BINS * 2, 108),
                               interpolation=cv2.INTER_AREA).astype(np.float32)
            edges.append(_column_profile(np.abs(cv2.Sobel(small, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(small, cv2.CV_32F, 0, 1))))
            motion.append(_column_profile(np.abs(small - prev_small)) if prev_small is not None else np.zeros(PROFILE_BINS))
            prev_small = small
            faces = detector.detect(frame)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(faces) else None
            obs = []
            for f in faces:
                x, y, w, h = (float(v) for v in f[:4])
                if w * sx < src_w * 0.03:
                    continue  # tiny background faces
                mouth, ref = _face_patches(gray, f) if gray is not None else (None, None)
                obs.append(_Obs(t, (x + w / 2) * sx, (y + h / 2) * sy, w * sx, h * sy, mouth, ref))
            frame_faces.append(obs)
            idx += 1
            if on_progress and duration > 0 and idx % 10 == 0:
                on_progress(min(1.0, t / duration))
    finally:
        proc.stdout.close()
        proc.wait(timeout=30)

    if not times:
        return FramingPlan(src_w, src_h, [ShotPlan(0, duration, "center", [Keyframe(0, src_w / 2, src_h / 2)],
                                                   note="no frames decoded")], [], notes=notes + ["Could not decode frames: center crop."])

    cuts = detect_shot_cuts(hists, times, vhists=vhists, thumbs=thumbs)
    bounds = [0.0] + cuts + [duration]
    crop_w = src_h * OUT_ASPECT
    speech = speech or SpeechInfo()
    shots: list[ShotPlan] = []
    with_face = sum(1 for f in frame_faces if f)
    for si in range(len(bounds) - 1):
        s0, s1 = bounds[si], bounds[si + 1]
        idxs = [i for i, t in enumerate(times) if s0 <= t < s1]
        shot = _plan_shot(s0, s1, idxs, times, frame_faces, src_w, src_h, crop_w, split_allowed, speech, sample_fps)
        if shot.mode == "center" and idxs:
            shot = plan_content_shot(s0, s1, [times[i] for i in idxs], [edges[i] for i in idxs],
                                     [motion[i] for i in idxs], src_w, src_h, crop_w)
        shots.append(shot)

    switches = sum(max(0, len(s.speakers) - 1) for s in shots)
    if switches:
        notes.append(f"Active speaker detection: {switches} speaker switch(es).")
    return FramingPlan(src_w, src_h, shots, cuts, face_ratio=with_face / len(times), notes=notes)


# --------------------------------------------------------------------------- shots without faces

PROFILE_BINS = 96


def _column_profile(img: np.ndarray) -> np.ndarray:
    col = img.sum(axis=0)
    return col.reshape(PROFILE_BINS, -1).sum(axis=1) if len(col) % PROFILE_BINS == 0 else \
        np.interp(np.linspace(0, len(col) - 1, PROFILE_BINS), np.arange(len(col)), col)


def _unit(v: np.ndarray) -> np.ndarray:
    total = float(v.sum())
    return v / total if total > 1e-6 else np.zeros_like(v)


def plan_content_shot(s0: float, s1: float, times: list[float], edges: list[np.ndarray], motion: list[np.ndarray],
                      src_w: int, src_h: int, crop_w: float, min_coverage: float = 0.6) -> ShotPlan:
    """Frame a shot with no faces by its content: follow where the detail and the movement are (a hand drawing,
    a product, on-screen text) when that fits in the vertical crop; otherwise show the whole frame."""
    win = max(1, int(round(crop_w / src_w * PROFILE_BINS)))
    scores = [_unit(e) + 2.0 * _unit(m) for e, m in zip(edges, motion, strict=False)]
    avg = _unit(np.sum(scores, axis=0)) if scores else np.zeros(PROFILE_BINS)
    if avg.sum() <= 0:
        return ShotPlan(s0, s1, "center", [Keyframe(s0, src_w / 2, src_h / 2)], note="no faces found")
    sums = np.convolve(avg, np.ones(win), "valid")
    best = int(np.argmax(sums))
    if float(sums[best]) < min_coverage:
        return ShotPlan(s0, s1, "wide", [Keyframe(s0, src_w / 2, src_h / 2)], note="no faces, content spread out: full frame")
    samples = []
    for t, sc in zip(times, scores, strict=False):
        # stay near the shot's main content area; let movement pull the camera within it
        local = np.convolve(0.5 * sc + avg, np.ones(win), "valid")
        lo, hi = max(0, best - win // 2), min(len(local), best + win // 2 + 1)
        pos = lo + int(np.argmax(local[lo:hi]))
        samples.append((t, (pos + win / 2) / PROFILE_BINS * src_w, src_h / 2, 0.0))
    kfs = smooth_path(samples, crop_w, s0, deadzone_frac=0.18, hold=0.8)
    return ShotPlan(s0, s1, "track", kfs, note="no faces: content-aware")


# --------------------------------------------------------------------------- tracking + active speaker


def link_tracks(idxs: list[int], frame_faces: list[list[_Obs]], sample_fps: float = SAMPLE_FPS) -> list[_Track]:
    """One-to-one greedy linking of face detections into tracks, then lip activity per observation."""
    tracks: list[_Track] = []
    for i in idxs:
        obs = frame_faces[i]
        pairs: list[tuple[float, int, int]] = []
        for ti, tr in enumerate(tracks):
            lo = tr.last
            for oi, o in enumerate(obs):
                if o.t - lo.t > 1.5 or o.t <= lo.t:
                    continue
                d = float(np.hypot(o.x - lo.x, o.y - lo.y))
                ratio = o.w / max(1.0, lo.w)
                if d < max(lo.w, o.w) * 0.9 and 0.6 < ratio < 1.65:
                    pairs.append((d / max(lo.w, o.w) + abs(np.log(ratio)), ti, oi))
        pairs.sort()
        used_t: set[int] = set()
        used_o: set[int] = set()
        for _cost, ti, oi in pairs:
            if ti in used_t or oi in used_o:
                continue
            used_t.add(ti)
            used_o.add(oi)
            tracks[ti].obs.append(obs[oi])
        for oi, o in enumerate(obs):
            if oi not in used_o:
                tracks.append(_Track([o]))
    tracks = _merge_fragments(tracks)
    for tr in tracks:
        _lip_activity(tr)
    return tracks


def _lip_activity(tr: _Track) -> None:
    """Lip activity per observation.

    Speech opens and closes the mouth several times a second, while head turns and nods are smooth. So the
    signal is the mouth patch's deviation from the average of its neighbouring samples (a second difference),
    minus the same measure for the eye region; head motion is capped at the face's usual level so a hand or
    glasses crossing the eyes doesn't cancel real lip movement."""
    obs = tr.obs
    m2 = np.full(len(obs), np.nan)
    r2 = np.full(len(obs), np.nan)
    for i in range(1, len(obs) - 1):
        a, o, b = obs[i - 1], obs[i], obs[i + 1]
        if b.t - a.t > 3.5 / SAMPLE_FPS or o.mouth is None or a.mouth is None or b.mouth is None:
            continue
        m2[i] = float(np.mean(np.abs(o.mouth - 0.5 * (a.mouth + b.mouth))))
        if o.ref is not None and a.ref is not None and b.ref is not None:
            r2[i] = float(np.mean(np.abs(o.ref - 0.5 * (a.ref + b.ref))))
    fin = r2[np.isfinite(r2)]
    cap = float(np.percentile(fin, 60)) if len(fin) >= 5 else float("inf")
    for i, o in enumerate(obs):
        if np.isfinite(m2[i]):
            head = min(r2[i], cap) if np.isfinite(r2[i]) else 0.0
            o.energy = max(0.0, float(m2[i]) - HEAD_WEIGHT * head)
        else:
            o.energy = float("nan")

HEAD_WEIGHT = 0.6
# Active-speaker score weights: lip activity compared with the other faces, compared with this face's own
# resting level, and correlation of lip motion with the loudness of the voice.
W_REL, W_OWN, W_SYNC = 1.0, 0.3, 0.8


def _sync(es: np.ndarray, loud: np.ndarray, width: int = 5) -> float:
    if len(es) < 8 or np.std(loud) < 1e-3 or np.std(es) < 1e-6:
        return 0.0
    kern = np.ones(width) / width
    a, b = np.convolve(es, kern, "same"), np.convolve(loud, kern, "same")
    if np.std(a) < 1e-6 or np.std(b) < 1e-6:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _merge_fragments(tracks: list[_Track]) -> list[_Track]:
    """Join a face that was lost for a while (turned away, occluded) with its continuation at the same spot."""
    tracks = sorted(tracks, key=lambda tr: tr.obs[0].t)
    merged: list[_Track] = []
    for tr in tracks:
        first = tr.obs[0]
        target = None
        for m in merged:
            lo = m.last
            if lo.t < first.t and np.hypot(first.x - lo.x, first.y - lo.y) < max(lo.w, first.w) * 0.8:
                target = m
                break
        if target is None:
            merged.append(tr)
        else:
            target.obs.extend(tr.obs)
    return merged


def _energy_series(tr: _Track) -> tuple[np.ndarray, np.ndarray]:
    ts = np.array([o.t for o in tr.obs if np.isfinite(o.energy)])
    es = np.array([o.energy for o in tr.obs if np.isfinite(o.energy)])
    return ts, es


def _voice_owners(emissions: np.ndarray, voices: list[int], durs: list[float], k: int) -> dict[int, int]:
    """voice -> track, one-to-one, only where the lip evidence clearly points at one face (an off-screen voice,
    e.g. an interviewer behind the camera, gets no face)."""
    ids = sorted({v for v in voices if v >= 0})
    if not ids or k == 0:
        return {}
    score = np.zeros((len(ids), k))
    seen = np.zeros((len(ids), k))
    for j, v in enumerate(voices):
        if v < 0:
            continue
        vi = ids.index(v)
        for ti in range(k):
            if emissions[j, ti] > -3:
                score[vi, ti] += durs[j] * float(np.clip(emissions[j, ti], -1.5, 1.5))
                seen[vi, ti] += durs[j]
    owners: dict[int, int] = {}
    pairs = sorted(((score[vi, ti], vi, ti) for vi in range(len(ids)) for ti in range(k)), reverse=True)
    taken: set[int] = set()
    for sc, vi, ti in pairs:
        if ids[vi] in owners or ti in taken or seen[vi, ti] < 1.0:
            continue
        others = [score[vi, t] for t in range(k) if t != ti and seen[vi, t] >= 1.0]
        margin = sc - max(others) if others else sc
        if sc > 0 and margin >= VOICE_MARGIN * max(1.0, seen[vi, ti] ** 0.5):
            owners[ids[vi]] = ti
            taken.add(ti)
    return owners


W_VOICE = 1.5
VOICE_MARGIN = 0.5


def speaker_timeline(tracks: list[_Track], s0: float, s1: float, speech: SpeechInfo
                     ) -> tuple[list[tuple[float, int]], float, list[float]]:
    """Pick who is talking in each speech turn of [s0, s1).

    Returns ([(from_time, track_index)], decisiveness 0..1, talk share per track)."""
    turns = [(max(s0, a), min(s1, b)) for a, b in speech.turns if b > s0 + 0.05 and a < s1 - 0.05]
    k = len(tracks)
    if not turns or k == 0:
        return [], 0.0, [0.0] * k
    series = [_energy_series(tr) for tr in tracks]
    pooled = np.concatenate([es for _, es in series if len(es)]) if any(len(es) for _, es in series) else np.zeros(0)
    if len(pooled) < 6:
        return [], 0.0, [0.0] * k
    spread = float(np.std(pooled)) + 1e-3

    def in_speech(t: np.ndarray) -> np.ndarray:
        m = np.zeros(len(t), bool)
        for a, b in turns:
            m |= (t >= a - 0.1) & (t < b + 0.1)
        return m

    baselines = []
    for ts, es in series:
        if not len(es):
            baselines.append(0.0)
            continue
        quiet = es[~in_speech(ts)]
        baselines.append(float(np.median(quiet)) if len(quiet) >= 5 else float(np.percentile(es, 25)))

    emissions = np.full((len(turns), k), -3.0)
    for j, (a, b) in enumerate(turns):
        levels: dict[int, float] = {}
        syncs: dict[int, float] = {}
        for ti, (ts, es) in enumerate(series):
            present = [o for o in tracks[ti].obs if a <= o.t < b]
            if len(present) < 0.4 * max(1.0, (b - a) * SAMPLE_FPS):
                continue  # not on screen for most of this turn
            sel = (ts >= a - 0.1) & (ts < b + 0.1)
            if sel.sum() < 2:
                continue
            levels[ti] = float(np.mean(es[sel]))
            # Does this face's lip motion follow the rhythm of the voice? (smoothed, over the turn +- 1.5 s)
            win = (ts >= a - 1.5) & (ts < b + 1.5)
            loud = speech.loudness(ts[win])
            syncs[ti] = _sync(es[win], loud) if loud is not None else 0.0
        if not levels:
            continue
        mean_level = float(np.mean(list(levels.values())))
        for ti, lv in levels.items():
            emissions[j, ti] = (W_REL * (lv - mean_level) + W_OWN * (lv - baselines[ti])) / spread + W_SYNC * syncs[ti]

    # Voice identity: link each voice to the face whose lips move most across ALL of that voice's turns, then
    # favour that face whenever the voice speaks. Per-turn lip evidence is noisy (nods, gestures, a hand over
    # the mouth); summed over a whole conversation it is reliable.
    voices = [speech.voices[i] if i < len(speech.voices) else -1
              for i, (a, b) in enumerate(speech.turns) if b > s0 + 0.05 and a < s1 - 0.05]
    owner = _voice_owners(emissions, voices, [b - a for a, b in turns], k)
    for j, v in enumerate(voices):
        face = owner.get(v)
        if face is not None and emissions[j, face] > -3:
            emissions[j, face] += W_VOICE

    # Viterbi: stay on a speaker unless the evidence for someone else beats the switching cost.
    durs = np.array([b - a for a, b in turns])
    cost = np.zeros((len(turns), k))
    back = np.zeros((len(turns), k), dtype=int)
    cost[0] = emissions[0]
    for j in range(1, len(turns)):
        penalty = 0.9 + max(0.0, 1.2 - durs[j]) * 1.2
        for s in range(k):
            stay = cost[j - 1, s]
            move = cost[j - 1] - penalty
            move[s] = -np.inf
            best_other = int(np.argmax(move))
            if move[best_other] > stay:
                cost[j, s], back[j, s] = move[best_other] + emissions[j, s], best_other
            else:
                cost[j, s], back[j, s] = stay + emissions[j, s], s
    path = [int(np.argmax(cost[-1]))]
    for j in range(len(turns) - 1, 0, -1):
        path.append(int(back[j, path[-1]]))
    path.reverse()
    if log.isEnabledFor(logging.DEBUG):
        for j, (a, b) in enumerate(turns):
            log.debug("turn %5.2f-%5.2f  %s  -> %d", a, b, " ".join(f"{e:+.2f}" for e in emissions[j]), path[j])

    decisive = 0.0
    share = [0.0] * k
    for j, s in enumerate(path):
        ranked = np.sort(emissions[j])[::-1]
        margin = ranked[0] - ranked[1] if k > 1 else ranked[0]
        if emissions[j, s] > -3 and (k == 1 or margin >= 0.35):
            decisive += durs[j]
        share[s] += durs[j]
    total = float(durs.sum()) or 1.0

    timeline: list[tuple[float, int]] = []
    for j, s in enumerate(path):
        if timeline and timeline[-1][1] == s:
            continue
        a = turns[j][0]
        prev_end = turns[j - 1][1] if j else s0
        switch = s0 if not timeline else max(prev_end, a - 0.15, timeline[-1][0] + 0.8)
        timeline.append((switch, s))
    return timeline, decisive / total, [x / total for x in share]


def _plan_shot(s0: float, s1: float, idxs: list[int], times: list[float], frame_faces: list[list[_Obs]],
               src_w: int, src_h: int, crop_w: float, split_allowed: bool,
               speech: SpeechInfo | None = None, sample_fps: float = SAMPLE_FPS) -> ShotPlan:
    center = ShotPlan(s0, s1, "center", [Keyframe(s0, src_w / 2, src_h / 2)], note="no faces found")
    if not idxs:
        return center
    speech = speech or SpeechInfo()
    tracks = link_tracks(idxs, frame_faces, sample_fps)

    n = len(idxs)
    present = [tr for tr in tracks if len({o.t for o in tr.obs}) / n >= 0.2]
    if not present:
        return center
    biggest = max(tr.median_box()[2] for tr in present)
    prominent = [tr for tr in present if tr.median_box()[2] >= biggest * 0.45]  # skip small background people
    prominent.sort(key=lambda tr: -(tr.median_box()[2] * len(tr.obs)))

    def samples_for(tr: _Track, a: float = -1.0, b: float = 1e9) -> list[tuple[float, float, float, float]]:
        seg = [(o.t, o.x, o.y, o.h) for o in tr.obs if a <= o.t < b]
        if not seg:
            o = tr.near((a + b) / 2 if b < 1e8 else a)
            seg = [(max(a, 0.0), o.x, o.y, o.h)]
        return seg

    if len(prominent) == 1:
        return ShotPlan(s0, s1, "track", smooth_path(samples_for(prominent[0]), crop_w, s0), note="single speaker")

    boxes = [tr.median_box() for tr in prominent]
    left = min(x - w * 0.9 for x, _y, w, _h in boxes)
    right = max(x + w * 0.9 for x, _y, w, _h in boxes)
    if right - left <= crop_w * 0.95:
        mx, my = (left + right) / 2, float(np.mean([b[1] for b in boxes]))
        fh = max(b[3] for b in boxes)
        return ShotPlan(s0, s1, "track", [Keyframe(s0, mx, my, fh)], note=f"{len(prominent)} subjects fit in frame")

    timeline, decisive, share = speaker_timeline(prominent, s0, s1, speech)
    if timeline and decisive >= 0.3:
        kfs: list[Keyframe] = []
        for ci, (ct, ti) in enumerate(timeline):
            c_end = timeline[ci + 1][0] if ci + 1 < len(timeline) else s1
            seg_kfs = smooth_path(samples_for(prominent[ti], ct, c_end), crop_w, ct if ci else s0)
            if kfs and seg_kfs:
                # hard cut to the new speaker (no pan across the room)
                kfs.append(Keyframe(ct - 1e-3, kfs[-1].cx, kfs[-1].cy, kfs[-1].face_h))
                seg_kfs = [k for k in seg_kfs if k.t >= ct]
                seg_kfs[0:1] = [Keyframe(ct, seg_kfs[0].cx, seg_kfs[0].cy, seg_kfs[0].face_h)] if seg_kfs else []
            kfs.extend(seg_kfs)
        note = "active speaker" + (f", {len(timeline) - 1} switch(es)" if len(timeline) > 1 else "")
        return ShotPlan(s0, s1, "track", kfs, note=note, speakers=[(t, ti) for t, ti in timeline])

    # Nobody identifiable as the speaker.
    order = sorted(range(len(prominent)), key=lambda i: -(share[i] + 1e-6 * len(prominent[i].obs)))
    if split_allowed:
        a, b = prominent[order[0]], prominent[order[1]]
        ab, bb = a.median_box(), b.median_box()
        top, bottom = (ab, bb) if ab[0] <= bb[0] else (bb, ab)
        return ShotPlan(s0, s1, "split", [Keyframe(s0, src_w / 2, src_h / 2)],
                        split_targets=[Keyframe(s0, top[0], top[1], top[3]), Keyframe(s0, bottom[0], bottom[1], bottom[3])],
                        note="several speakers, split allowed")
    weights = sorted((tr.median_box()[2] ** 2 * len(tr.obs) for tr in prominent), reverse=True)
    if weights[0] >= 2.0 * weights[1]:
        main = max(prominent, key=lambda tr: tr.median_box()[2] ** 2 * len(tr.obs))
        return ShotPlan(s0, s1, "track", smooth_path(samples_for(main), crop_w, s0), note="main subject")
    return ShotPlan(s0, s1, "wide", [Keyframe(s0, src_w / 2, src_h / 2)], note="several speakers, wide frame")


# --------------------------------------------------------------------------- per-frame geometry


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def shot_at(plan: FramingPlan, t: float) -> ShotPlan:
    for s in plan.shots:
        if s.start <= t < s.end:
            return s
    return plan.shots[-1]


def camera_at(shot: ShotPlan, t: float) -> Keyframe:
    kfs = shot.keyframes
    if not kfs:
        return Keyframe(t, 0, 0)
    if t <= kfs[0].t:
        return kfs[0]
    for k0, k1 in zip(kfs, kfs[1:], strict=False):
        if k0.t <= t < k1.t:
            span = k1.t - k0.t
            if span < 0.01:
                return k1
            f = _ease((t - k0.t) / span)
            return Keyframe(t, k0.cx + (k1.cx - k0.cx) * f, k0.cy + (k1.cy - k0.cy) * f,
                            k0.face_h + (k1.face_h - k0.face_h) * f)
    return kfs[-1]


def crop_rect(src_w: int, src_h: int, cam: Keyframe, zoom: float, aspect: float = OUT_ASPECT,
              has_face: bool = True) -> tuple[int, int, int, int]:
    """Crop (x, y, w, h) in source pixels keeping the subject's face in the upper-middle of the frame."""
    zoom = max(1.0, zoom)
    ch = src_h / zoom
    cw = ch * aspect
    if cw > src_w:
        cw = src_w / zoom
        ch = cw / aspect
    cx = cam.cx if cam.cx > 0 else src_w / 2
    if has_face and cam.face_h > 0 and zoom > 1.0:
        cy = cam.cy + ch * 0.12  # face above centre, shoulders visible
    else:
        cy = src_h / 2 if not has_face or cam.cy <= 0 else max(ch / 2, min(src_h - ch / 2, cam.cy + ch * 0.12))
    x = int(round(min(max(cx - cw / 2, 0), src_w - cw)))
    y = int(round(min(max(cy - ch / 2, 0), src_h - ch)))
    return x, y, int(round(cw)), int(round(ch))
