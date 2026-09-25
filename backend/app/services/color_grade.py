"""Colour grading: presets plus per-clip fine-tuning, applied identically to shorts and long-form.

Shorts are composited frame by frame in NumPy, so the grade is applied there -- after framing, punch-ins and
B-roll, before captions and hook overlays, which stay clean. Long-form is cut and encoded by FFmpeg in a single
pass, so the same maths is baked into a 3D LUT (.cube) and applied with FFmpeg's ``lut3d`` filter; the vignette
is spatial and cannot live in a LUT, so FFmpeg's ``vignette`` filter stands in for it there.

All tone maths runs on 8-bit values through 256-entry lookup tables and 3x3 matrices, so a 1080x1920 frame costs a
few milliseconds. The .cube lattice is produced by the very same code path, which is what keeps the two outputs alike.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np

RGB = tuple[float, float, float]

# BT.709 luma weights in the BGR channel order OpenCV uses
_LUMA_BGR = np.array([[0.0722, 0.7152, 0.2126]], dtype=np.float32)


@dataclass
class Grade:
    name: str
    label: str = ""
    description: str = ""
    exposure: float = 0.0  # EV, -1..1
    contrast: float = 0.0  # -1..1, S-curve around mid grey
    saturation: float = 0.0  # -1 (monochrome) .. 1
    temperature: float = 0.0  # -1 cool .. 1 warm
    tint: float = 0.0  # -1 green .. 1 magenta
    shadows: float = 0.0  # -1 crush .. 1 lift
    highlights: float = 0.0  # -1 recover .. 1 brighten
    fade: float = 0.0  # 0..1 matte (lifted black point)
    vignette: float = 0.0  # 0..1 edge darkening
    intensity: float = 1.0  # 0..1 blend with the ungraded frame
    shadow_tint: RGB = (0.0, 0.0, 0.0)  # split toning: RGB offsets that fade in towards black
    highlight_tint: RGB = (0.0, 0.0, 0.0)  # split toning: RGB offsets that fade in towards white

    @property
    def active(self) -> bool:
        if self.intensity <= 0:
            return False
        tonal = any(abs(getattr(self, k)) > 1e-6 for k in EDITABLE_FIELDS if k != "intensity")
        return tonal or any(abs(v) > 1e-6 for v in (*self.shadow_tint, *self.highlight_tint))

    def to_public(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "description": self.description,
                **{k: getattr(self, k) for k in EDITABLE_FIELDS}}

    def describe(self, overrides: dict[str, Any] | None = None) -> str:
        """Human label for render notes: 'Cinematic', 'Cinematic (custom)', or 'Custom' for sliders alone."""
        if self.name == "none":
            return "Custom" if overrides else self.label
        return f"{self.label} (custom)" if overrides else self.label


# (min, max) for every value the user may override on top of a preset
EDITABLE_FIELDS: dict[str, tuple[float, float]] = {
    "exposure": (-1.0, 1.0), "contrast": (-1.0, 1.0), "saturation": (-1.0, 1.0), "temperature": (-1.0, 1.0),
    "tint": (-1.0, 1.0), "shadows": (-1.0, 1.0), "highlights": (-1.0, 1.0), "fade": (0.0, 1.0),
    "vignette": (0.0, 1.0), "intensity": (0.0, 1.0),
}

PRESETS: dict[str, Grade] = {
    "none": Grade("none", "Off", "Source colours untouched."),
    "clean": Grade("clean", "Clean", "A touch more contrast and colour. Safe on any footage.",
                   contrast=0.08, saturation=0.10, highlights=0.03),
    "punchy": Grade("punchy", "Punchy", "High contrast, vivid colour, slight vignette: the feed look.",
                    contrast=0.20, saturation=0.25, highlights=0.05, shadows=-0.05, vignette=0.15),
    "warm": Grade("warm", "Warm", "Golden, friendly skin tones.",
                  temperature=0.40, tint=0.05, contrast=0.08, saturation=0.10),
    "cool": Grade("cool", "Cool", "Crisp and blue-leaning; tech and office footage.",
                  temperature=-0.40, contrast=0.10, saturation=0.05),
    "cinematic": Grade("cinematic", "Cinematic", "Teal shadows, orange highlights, soft blacks, vignette.",
                       contrast=0.12, saturation=-0.05, fade=0.10, vignette=0.25,
                       shadow_tint=(-0.04, 0.03, 0.07), highlight_tint=(0.06, 0.02, -0.04)),
    "matte": Grade("matte", "Matte film", "Lifted blacks, muted colour, a little warmth.",
                   fade=0.50, contrast=0.06, saturation=-0.15, temperature=0.12, vignette=0.12),
    "mono": Grade("mono", "Black & white", "Punchy monochrome with a vignette.",
                  saturation=-1.0, contrast=0.18, vignette=0.20),
}


def grade_from(name: str, overrides: dict[str, Any] | None = None) -> Grade:
    base = PRESETS.get(name, PRESETS["none"])
    ov: dict[str, Any] = {}
    for k, v in (overrides or {}).items():
        if k not in EDITABLE_FIELDS or v is None or v == "":
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        lo, hi = EDITABLE_FIELDS[k]
        ov[k] = max(lo, min(hi, f))
    return replace(base, **ov)


# --------------------------------------------------------------------------- tone maths (pure NumPy)


def tone_curve(g: Grade, x: np.ndarray) -> np.ndarray:
    """Per-channel tone response for values in 0..1 (white balance is applied by the caller)."""
    y = x * (2.0 ** g.exposure)
    # shadows lift/crush and highlight brighten/recover: quadratic weights so each only touches its own end
    y = y + g.shadows * 0.12 * np.clip(1.0 - y, 0, 1) ** 2
    y = y + g.highlights * 0.12 * np.clip(y, 0, 1) ** 2
    # contrast: a sine S-curve keeps black and white in place and never clips on its own
    if g.contrast:
        y = y - 0.75 * g.contrast * np.sin(2 * np.pi * np.clip(y, 0, 1)) / (2 * np.pi)
    # matte: lift the black point, whites stay white
    if g.fade:
        y = g.fade * 0.14 + y * (1.0 - g.fade * 0.14)
    return np.clip(y, 0.0, 1.0)


def white_balance_gains(g: Grade) -> tuple[float, float, float]:
    """(B, G, R) channel gains for temperature and tint."""
    return 1.0 - 0.10 * g.temperature, 1.0 - 0.06 * g.tint, 1.0 + 0.10 * g.temperature


def tone_luts(g: Grade) -> np.ndarray:
    """(256, 1, 3) uint8 lookup table in BGR order, ready for cv2.LUT."""
    x = np.linspace(0.0, 1.0, 256)
    lut = np.zeros((256, 1, 3), np.uint8)
    for c, gain in enumerate(white_balance_gains(g)):
        lut[:, 0, c] = np.round(tone_curve(g, x * gain) * 255).astype(np.uint8)
    return lut


def saturation_matrix(g: Grade) -> np.ndarray | None:
    """Luma-preserving saturation as a 3x3 float32 matrix for cv2.transform (BGR)."""
    if abs(g.saturation) < 1e-6:
        return None
    k = 1.0 + g.saturation
    return (k * np.eye(3, dtype=np.float32) + (1.0 - k) * np.repeat(_LUMA_BGR, 3, axis=0)).astype(np.float32)


def split_tone_luts(g: Grade) -> tuple[np.ndarray, np.ndarray] | None:
    """Tints driven by luma: returns (add, subtract) 8-bit LUTs indexed by luma, or None when unused."""
    if not any(abs(v) > 1e-6 for v in (*g.shadow_tint, *g.highlight_tint)):
        return None
    lum = np.linspace(0.0, 1.0, 256)[:, None]
    sh = np.array(g.shadow_tint[::-1], dtype=np.float64)[None, :]  # RGB -> BGR
    hi = np.array(g.highlight_tint[::-1], dtype=np.float64)[None, :]
    add = (sh * (1.0 - lum) ** 2 + hi * lum**2) * 255.0
    pos = np.clip(np.round(add), 0, 255).astype(np.uint8).reshape(256, 1, 3)
    neg = np.clip(np.round(-add), 0, 255).astype(np.uint8).reshape(256, 1, 3)
    return pos, neg


def vignette_mask(w: int, h: int, strength: float) -> np.ndarray:
    """(h, w, 3) uint8 multiplier (255 = untouched); corners end up at 1 - strength."""
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2.0)
    m = 1.0 - strength * np.clip((d - 0.3) / 0.7, 0.0, 1.0) ** 1.8
    m8 = np.round(m * 255).astype(np.uint8)
    return np.repeat(m8[:, :, None], 3, axis=2)


# --------------------------------------------------------------------------- per-frame application


@dataclass
class PreparedGrade:
    grade: Grade
    tone: np.ndarray
    sat: np.ndarray | None
    split: tuple[np.ndarray, np.ndarray] | None
    _masks: dict[tuple[int, int], np.ndarray] = field(default_factory=dict)

    def apply(self, frame: np.ndarray, *, spatial: bool = True) -> np.ndarray:
        """Grade a BGR uint8 image. ``spatial=False`` skips the vignette (used to build the LUT lattice)."""
        g = self.grade
        out = cv2.LUT(frame, self.tone)
        if self.sat is not None:
            out = cv2.transform(out, self.sat)
        if self.split is not None:
            luma = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)  # ~10x faster than a 1x3 transform; weights are close enough
            luma3 = cv2.merge((luma, luma, luma))
            out = cv2.add(out, cv2.LUT(luma3, self.split[0]))
            out = cv2.subtract(out, cv2.LUT(luma3, self.split[1]))
        if spatial and g.vignette > 0:
            h, w = out.shape[:2]
            mask = self._masks.get((w, h))
            if mask is None:
                mask = self._masks[(w, h)] = vignette_mask(w, h, g.vignette)
            out = cv2.multiply(out, mask, scale=1.0 / 255.0)
        if g.intensity < 1.0:
            out = cv2.addWeighted(out, g.intensity, frame, 1.0 - g.intensity, 0.0)
        return out


def prepare(g: Grade) -> PreparedGrade | None:
    if not g.active:
        return None
    return PreparedGrade(grade=g, tone=tone_luts(g), sat=saturation_matrix(g), split=split_tone_luts(g))


# --------------------------------------------------------------------------- FFmpeg side (long-form)


def write_cube(g: Grade, path: str | Path, size: int = 33) -> Path:
    """Bake the grade (everything but the vignette) into an Adobe/Resolve-style .cube 3D LUT."""
    prep = prepare(g)
    n = size
    idx = np.arange(n**3)
    r, gg, b = idx % n, (idx // n) % n, idx // (n * n)  # red varies fastest, as the format requires
    lattice = np.stack([b, gg, r], axis=1).astype(np.float64) * (255.0 / (n - 1))
    img = np.round(lattice).astype(np.uint8).reshape(-1, 1, 3)
    out = prep.apply(img, spatial=False) if prep else img
    rgb = out.reshape(-1, 3)[:, ::-1].astype(np.float64) / 255.0
    p = Path(path)
    header = f"TITLE \"ClipForge {g.label or g.name}\"\nLUT_3D_SIZE {n}\nDOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0"
    np.savetxt(p, rgb, fmt="%.6f", header=header, comments="")
    return p


def _quote(path: str) -> str:
    # single quotes make a filter argument literal; the quote itself has to be closed, escaped and reopened
    return "'" + path.replace("'", "'\\''") + "'"


def ffmpeg_vf(g: Grade, cube_path: str | Path) -> str:
    """Video filter chain that reproduces the grade in FFmpeg (lut3d + vignette)."""
    parts = [f"lut3d=file={_quote(str(cube_path))}:interp=tetrahedral"]
    v = g.vignette * g.intensity
    if v > 0:
        # ffmpeg's vignette darkens by cos(angle * distance)^4 with distance 1 at the corners
        angle = math.acos(max(0.0, 1.0 - v) ** 0.25)
        parts.append(f"vignette=angle={angle:.4f}")
    return ",".join(parts)
