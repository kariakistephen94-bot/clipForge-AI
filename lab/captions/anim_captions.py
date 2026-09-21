"""LAB: animated "viral" captions (not used by the app until approved).

Same interface as app.services.captions.CaptionRenderer -- AnimatedCaptionRenderer(...).overlay(frame, t) --
so it can replace it with a one-line change in render.py later.

Every word is rendered once into an RGBA sprite (fill + outline + soft shadow) and then transformed per frame
(scale, offset, rotation, opacity) and alpha-blended, so animation costs little per frame.

Styles
  pop     Hormozi style: short captions, each word pops in with a springy overshoot when it is spoken; the
          current word is yellow and slightly bigger; emphasis phrases are green.
  box     Submagic / MrBeast style: the caption pops in; a coloured rounded box glides behind the word being
          spoken and that word bounces.
  single  One big word at a time, punching in with overshoot and a slight random tilt.
  rise    Clean modern: mixed case, words slide up and fade in as they are spoken; the current word is tinted.
"""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.services.captions import CaptionChunk, CaptionStyle, CaptionWord, blend_bgra, group_words

RGBA = tuple[int, int, int, int]

FONTS = {
    "arial_black": ("/System/Library/Fonts/Supplemental/Arial Black.ttf", 0),
    "impact": ("/System/Library/Fonts/Supplemental/Impact.ttf", 0),
    "coolvetica": ("/Users/mac/Library/Fonts/Coolvetica Hv Comp.otf", 0),
    "avenir_heavy": ("/System/Library/Fonts/Avenir Next.ttc", 8),  # index 8 = Heavy
}


@dataclass
class AnimStyle:
    name: str
    font: str = "arial_black"
    font_size: int = 86
    uppercase: bool = True
    words_per_caption: int = 3
    max_chars_per_line: int = 14
    position_y: float = 0.68
    text: RGBA = (255, 255, 255, 255)
    active: RGBA = (255, 224, 51, 255)
    emphasis: RGBA = (80, 230, 120, 255)
    outline: RGBA = (0, 0, 0, 255)
    outline_width: int = 8
    shadow: RGBA = (0, 0, 0, 170)
    shadow_offset: int = 7
    box: RGBA = (124, 58, 237, 255)
    line_spacing: float = 1.05
    word_gap: float = 1.05  # space between words, in space widths (room for words that grow while animating)
    extras: dict[str, Any] = field(default_factory=dict)


STYLES: dict[str, AnimStyle] = {
    "pop": AnimStyle("pop", font="arial_black", font_size=92, words_per_caption=3, max_chars_per_line=12,
                     word_gap=1.9),
    "box": AnimStyle("box", font="arial_black", font_size=80, words_per_caption=4, max_chars_per_line=14,
                     active=(255, 255, 255, 255), box=(124, 58, 237, 255), outline_width=7, word_gap=1.6),
    "single": AnimStyle("single", font="coolvetica", font_size=190, words_per_caption=1, max_chars_per_line=12,
                        position_y=0.64, outline_width=11, shadow_offset=10, active=(255, 255, 255, 255),
                        emphasis=(255, 224, 51, 255)),
    "rise": AnimStyle("rise", font="avenir_heavy", font_size=78, uppercase=False, words_per_caption=5,
                      max_chars_per_line=20, active=(94, 234, 212, 255), outline_width=0, shadow=(0, 0, 0, 200),
                      shadow_offset=4, position_y=0.7),
}


# --------------------------------------------------------------------------- easing


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out_back(x: float, s: float = 1.9) -> float:
    x = clamp01(x) - 1
    return x * x * ((s + 1) * x + s) + 1


def ease_out_cubic(x: float) -> float:
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def spring(x: float, freq: float = 3.2, damp: float = 5.0) -> float:
    """0 -> 1 with a damped wobble; x in seconds."""
    if x <= 0:
        return 0.0
    return 1 - math.exp(-damp * x) * math.cos(freq * 2 * math.pi * x)


# --------------------------------------------------------------------------- sprites


class Sprites:
    def __init__(self, st: AnimStyle, size: int | None = None):
        path, index = FONTS[st.font]
        self.st = st
        self.font = ImageFont.truetype(path, size or st.font_size, index=index)
        asc, desc = self.font.getmetrics()
        self.line_h = int((asc + desc) * st.line_spacing)
        self.pad = st.outline_width + st.shadow_offset + 12
        self._cache: dict[tuple[str, RGBA], np.ndarray] = {}

    def width(self, text: str) -> float:
        return self.font.getlength(text)

    def get(self, text: str, color: RGBA) -> np.ndarray:
        key = (text, color)
        spr = self._cache.get(key)
        if spr is not None:
            return spr
        st, p = self.st, self.pad
        w = int(self.width(text)) + 2 * p
        h = self.line_h + 2 * p
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if st.shadow[3]:
            sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(sh).text((p + st.shadow_offset * 0.4, p + st.shadow_offset), text, font=self.font,
                                    fill=st.shadow, stroke_width=st.outline_width, stroke_fill=st.shadow)
            img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(5)))
        ImageDraw.Draw(img).text((p, p), text, font=self.font, fill=color,
                                 stroke_width=st.outline_width, stroke_fill=st.outline)
        arr = np.asarray(img, dtype=np.uint8)[:, :, [2, 1, 0, 3]].copy()  # BGRA
        self._cache[key] = arr
        return arr


def transform(sprite: np.ndarray, scale: float, angle: float = 0.0, alpha: float = 1.0) -> np.ndarray:
    out = sprite
    if abs(scale - 1) > 0.01 or abs(angle) > 0.1:
        h, w = sprite.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
        nw, nh = int(w * max(scale, 1) * 1.15) + 2, int(h * max(scale, 1) * 1.15) + 2
        m[0, 2] += (nw - w) / 2
        m[1, 2] += (nh - h) / 2
        out = cv2.warpAffine(sprite, m, (nw, nh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                             borderValue=(0, 0, 0, 0))
    if alpha < 0.999:
        out = out.copy()
        out[:, :, 3] = (out[:, :, 3].astype(np.float32) * clamp01(alpha)).astype(np.uint8)
    return out


def blit_center(frame: np.ndarray, sprite: np.ndarray, cx: float, cy: float) -> None:
    h, w = sprite.shape[:2]
    blend_bgra(frame, sprite, int(round(cx - w / 2)), int(round(cy - h / 2)))


def rounded_box(w: int, h: int, color: RGBA, radius: int) -> np.ndarray:
    img = Image.new("RGBA", (w + 16, h + 16), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([8, 12, w + 8, h + 12], radius=radius, fill=(0, 0, 0, 110))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(5)))
    ImageDraw.Draw(img).rounded_rectangle([8, 8, w + 8, h + 8], radius=radius, fill=color)
    return np.asarray(img, dtype=np.uint8)[:, :, [2, 1, 0, 3]].copy()


# --------------------------------------------------------------------------- renderer


@dataclass
class _Placed:
    word: CaptionWord
    cx: float  # centre of the word at rest (frame px)
    cy: float
    w: float


class AnimatedCaptionRenderer:
    def __init__(self, words: list[dict[str, Any]], style: str | AnimStyle = "pop", width: int = 1080,
                 height: int = 1920, emphasis_phrases: list[str] | None = None, seed: int = 3):
        self.st = STYLES[style] if isinstance(style, str) else style
        self.width, self.height = width, height
        self.sprites = Sprites(self.st)
        # the word sitting on the highlight box is drawn clean (no outline / shadow)
        self.clean = Sprites(AnimStyle(**{**self.st.__dict__, "outline_width": 0, "shadow": (0, 0, 0, 0),
                                          "shadow_offset": 0}))
        self.chunks = self._chunks(words, emphasis_phrases or [])
        self._starts = [c.start for c in self.chunks]
        self._layouts = [self._layout(c) for c in self.chunks]
        self._rng = random.Random(seed)
        self._tilts = [self._rng.uniform(-5, 5) for _ in self.chunks]
        self._box_cache: dict[tuple[int, int], np.ndarray] = {}

    # ---- grouping / layout
    def _chunks(self, words: list[dict[str, Any]], emphasis: list[str]) -> list[CaptionChunk]:
        base = CaptionStyle(name=self.st.name, uppercase=self.st.uppercase,
                            words_per_caption=max(3, self.st.words_per_caption),
                            max_chars_per_line=self.st.max_chars_per_line, max_lines=2)
        chunks = group_words(words, base, emphasis)
        if self.st.words_per_caption >= 3:
            return chunks
        # one word per caption: split, each word shown until the next one starts
        singles: list[CaptionChunk] = []
        for ch in chunks:
            for i, w in enumerate(ch.words):
                end = ch.words[i + 1].start if i + 1 < len(ch.words) else ch.end
                singles.append(CaptionChunk(start=w.start, end=max(end, w.start + 0.15), words=[w], lines=[[0]]))
        return singles

    def _layout(self, ch: CaptionChunk) -> list[_Placed]:
        sp = self.sprites
        space = sp.width(" ") * self.st.word_gap
        n = len(ch.lines)
        cy0 = self.height * self.st.position_y - (n - 1) * sp.line_h / 2
        placed = []
        for li, line in enumerate(ch.lines):
            widths = [sp.width(ch.words[j].text) for j in line]
            total = sum(widths) + space * (len(line) - 1)
            x = (self.width - total) / 2
            for j, w in zip(line, widths, strict=True):
                placed.append(_Placed(ch.words[j], x + w / 2, cy0 + li * sp.line_h, w))
                x += w + space
        # shrink-to-fit safety: never wider than 90% of the frame
        return placed

    def _chunk_at(self, t: float) -> int | None:
        i = bisect.bisect_right(self._starts, t + 0.001) - 1
        if i >= 0 and t < self.chunks[i].end:
            return i
        return None

    # ---- per-frame
    def overlay(self, frame: np.ndarray, t: float) -> None:
        ci = self._chunk_at(t)
        if ci is None:
            return
        getattr(self, f"_draw_{self.st.name}")(frame, ci, t)

    def _color(self, w: CaptionWord, active: bool) -> RGBA:
        if w.emphasis:
            return self.st.emphasis
        return self.st.active if active else self.st.text

    @staticmethod
    def _active(words: list[CaptionWord], t: float) -> int:
        idx = -1
        for j, w in enumerate(words):
            if w.start <= t + 0.03:
                idx = j
        return idx

    def _exit(self, ci: int, t: float, dur: float = 0.09) -> float:
        """1 -> 0 over the last `dur` s of a caption, unless the next caption follows immediately."""
        ch = self.chunks[ci]
        nxt = self.chunks[ci + 1].start if ci + 1 < len(self.chunks) else None
        if nxt is not None and nxt - ch.end < 0.05:
            return 1.0
        return clamp01((ch.end - t) / dur)

    def _draw_pop(self, frame: np.ndarray, ci: int, t: float) -> None:
        ch, lay = self.chunks[ci], self._layouts[ci]
        act = self._active(ch.words, t)
        out = self._exit(ci, t)
        for j, p in enumerate(lay):
            w = p.word
            since = t - w.start
            if since < -0.02:
                continue  # words appear only when they are spoken
            grow = ease_out_back(since / 0.2, 1.7)
            scale = 0.5 + 0.5 * grow
            if j == act:
                scale *= 1.0 + 0.07 * ease_out_cubic(since / 0.12)
            if w.emphasis:
                scale *= 1.08
            scale *= 0.85 + 0.15 * out
            alpha = clamp01(since / 0.06 + 0.3) * out
            spr = transform(self.sprites.get(w.text, self._color(w, j == act)), scale, 0, alpha)
            blit_center(frame, spr, p.cx, p.cy)

    def _draw_box(self, frame: np.ndarray, ci: int, t: float) -> None:
        ch, lay = self.chunks[ci], self._layouts[ci]
        act = self._active(ch.words, t)
        since_chunk = t - ch.start
        enter = ease_out_back(since_chunk / 0.18, 1.6)
        chunk_scale = (0.8 + 0.2 * enter) * (0.9 + 0.1 * self._exit(ci, t))
        alpha = clamp01(since_chunk / 0.08) * self._exit(ci, t)
        cx_mid = self.width / 2
        cy_mid = sum(p.cy for p in lay) / len(lay)

        def at(x: float, y: float) -> tuple[float, float]:
            return cx_mid + (x - cx_mid) * chunk_scale, cy_mid + (y - cy_mid) * chunk_scale

        if act >= 0:
            cur = lay[act]
            prev = lay[act - 1] if act > 0 and lay[act - 1].cy == cur.cy else None
            k = ease_out_cubic((t - cur.word.start) / 0.12)
            bx = cur.cx if prev is None else prev.cx + (cur.cx - prev.cx) * k
            bw = cur.w if prev is None else prev.w + (cur.w - prev.w) * k
            bx, by = at(bx, cur.cy)
            pop = 1 + 0.12 * (1 - ease_out_cubic((t - cur.word.start) / 0.18))
            bw_px = int((bw + 44) * chunk_scale * pop)
            bh_px = int(self.sprites.line_h * 1.02 * chunk_scale * pop)
            key = (bw_px // 4, bh_px // 4)
            box = self._box_cache.get(key)
            if box is None:
                box = rounded_box(bw_px, bh_px, self.st.box, radius=max(10, bh_px // 5))
                self._box_cache[key] = box
                if len(self._box_cache) > 300:
                    self._box_cache.pop(next(iter(self._box_cache)))
            blit_center(frame, transform(box, 1.0, 0, alpha), bx, by + self.sprites.line_h * 0.02)
        for j, p in enumerate(lay):
            w = p.word
            s = chunk_scale
            if j == act:
                s *= 1 + 0.08 * (1 - ease_out_cubic((t - w.start) / 0.2))
            x, y = at(p.cx, p.cy)
            sprites = self.clean if j == act else self.sprites
            spr = transform(sprites.get(w.text, self._color(w, j == act)), s, 0, alpha)
            blit_center(frame, spr, x, y)

    def _draw_single(self, frame: np.ndarray, ci: int, t: float) -> None:
        ch, lay = self.chunks[ci], self._layouts[ci]
        p = lay[0]
        since = t - ch.start
        scale = 0.3 + 0.7 * ease_out_back(since / 0.16, 2.6)
        max_w = self.width * 0.9
        if p.w * scale > max_w:
            scale = max_w / p.w
        tilt = self._tilts[ci] * (1 - ease_out_cubic(since / 0.25)) + self._tilts[ci] * 0.25
        alpha = clamp01(since / 0.05 + 0.2)
        spr = transform(self.sprites.get(p.word.text, self._color(p.word, False)), scale, tilt, alpha)
        blit_center(frame, spr, p.cx, p.cy)

    def _draw_rise(self, frame: np.ndarray, ci: int, t: float) -> None:
        ch, lay = self.chunks[ci], self._layouts[ci]
        act = self._active(ch.words, t)
        out = self._exit(ci, t, 0.12)
        for j, p in enumerate(lay):
            w = p.word
            since = t - (w.start - 0.08)
            if since < 0:
                continue
            k = ease_out_cubic(since / 0.22)
            dy = (1 - k) * 34 - (1 - out) * 18
            alpha = k * out
            spr = transform(self.sprites.get(w.text, self._color(w, j == act)), 1.0, 0, alpha)
            blit_center(frame, spr, p.cx, p.cy + dy)
