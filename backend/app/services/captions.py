"""Short-form caption engine.

Groups Whisper words into 3-7 word, max-2-line captions, marks emphasis words (sparingly),
renders styled RGBA overlays with Pillow and composites them onto frames. Exports SRT.

FFmpeg's ASS/subtitles filters require libass, which many FFmpeg builds (including current
Homebrew) omit -- rendering captions ourselves works with any FFmpeg build.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..candidates.snapping import norm_token
from ..utils.timeutil import srt_timestamp
from .fonts import load_font, resolve_font

RGBA = tuple[int, int, int, int]


@dataclass
class CaptionStyle:
    name: str
    font: str | None = None
    font_group: str = "heavy"
    font_size: int = 84
    text_color: RGBA = (255, 255, 255, 255)
    highlight_color: RGBA = (255, 225, 77, 255)
    emphasis_color: RGBA = (74, 222, 128, 255)
    outline_color: RGBA = (0, 0, 0, 255)
    outline_width: int = 9
    shadow: bool = True
    shadow_offset: int = 6
    shadow_color: RGBA = (0, 0, 0, 150)
    box: bool = False
    box_color: RGBA = (0, 0, 0, 150)
    uppercase: bool = True
    position_y: float = 0.66  # centre of caption block as a fraction of frame height
    words_per_caption: int = 4
    max_chars_per_line: int = 16
    max_lines: int = 2
    highlight_mode: str = "word"  # word | fill | none
    emphasis: bool = True
    line_spacing: float = 1.12

    def to_public(self) -> dict[str, Any]:
        return {
            "name": self.name, "font": self.font, "font_size": self.font_size, "outline_width": self.outline_width,
            "shadow": self.shadow, "position_y": self.position_y, "words_per_caption": self.words_per_caption,
            "highlight_mode": self.highlight_mode, "uppercase": self.uppercase, "emphasis": self.emphasis,
            "box": self.box,
        }


PRESETS: dict[str, CaptionStyle] = {
    "bold_viral": CaptionStyle(name="bold_viral"),
    "clean": CaptionStyle(
        name="clean", font_group="bold", font_size=70, outline_width=0, shadow=True, shadow_offset=3,
        shadow_color=(0, 0, 0, 120), box=True, box_color=(0, 0, 0, 140), uppercase=False, words_per_caption=5,
        max_chars_per_line=22, highlight_color=(255, 214, 90, 255), emphasis_color=(255, 214, 90, 255), position_y=0.68,
    ),
    "minimal": CaptionStyle(
        name="minimal", font_group="bold", font_size=60, outline_width=4, shadow=False, uppercase=False,
        words_per_caption=6, max_chars_per_line=26, highlight_mode="none", emphasis=False, position_y=0.72,
    ),
}

EDITABLE_FIELDS = {"font", "font_size", "outline_width", "shadow", "position_y", "words_per_caption",
                   "highlight_mode", "uppercase", "emphasis", "box"}


def style_from(name: str, overrides: dict[str, Any] | None = None) -> CaptionStyle:
    base = PRESETS.get(name, PRESETS["bold_viral"])
    ov = {k: v for k, v in (overrides or {}).items() if k in EDITABLE_FIELDS and v is not None and v != ""}
    if "font_size" in ov:
        ov["font_size"] = int(max(32, min(140, int(ov["font_size"]))))
    if "outline_width" in ov:
        ov["outline_width"] = int(max(0, min(20, int(ov["outline_width"]))))
    if "position_y" in ov:
        ov["position_y"] = float(max(0.3, min(0.8, float(ov["position_y"]))))  # stay clear of top/bottom app UI
    if "words_per_caption" in ov:
        ov["words_per_caption"] = int(max(3, min(7, int(ov["words_per_caption"]))))
    if "highlight_mode" in ov and ov["highlight_mode"] not in ("word", "fill", "none"):
        ov.pop("highlight_mode")
    style = replace(base, **ov)
    # Line length follows font size so text never overflows 1080px.
    style.max_chars_per_line = max(10, int(base.max_chars_per_line * base.font_size / style.font_size))
    return style


# --------------------------------------------------------------------------- grouping


@dataclass
class CaptionWord:
    text: str
    start: float
    end: float
    emphasis: bool = False
    raw: str = ""


@dataclass
class CaptionChunk:
    start: float
    end: float
    words: list[CaptionWord]
    lines: list[list[int]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


_TERMINAL = re.compile(r"[.!?…]['\")\]]*$")


def clean_word(word: str, style: CaptionStyle) -> str:
    w = word.strip()
    w = re.sub(r"^[\"'“‘(\[]+|[\"'”’)\]]+$", "", w) if len(w) > 1 else w
    # Keep ? and ! (they carry meaning); drop trailing commas/periods/semicolons/colons.
    w = re.sub(r"(?<=[\w%$])[,.;:]+$", "", w)
    w = re.sub(r"\.{2,}$|…$", "...", w) if w.endswith(("..", "…")) else w
    return w.upper() if style.uppercase else w


def mark_emphasis(words: list[dict[str, Any]], phrases: list[str]) -> set[int]:
    """Indices of words that belong to an emphasis phrase."""
    toks = [norm_token(w["word"]) for w in words]
    marked: set[int] = set()
    for phrase in phrases:
        ptoks = [t for t in (norm_token(x) for x in phrase.split()) if t]
        if not ptoks or len(ptoks) > 6:
            continue
        n = len(ptoks)
        for i in range(len(toks) - n + 1):
            if toks[i : i + n] == ptoks:
                marked.update(range(i, i + n))
    return marked


def _split_lines(words: list[CaptionWord], max_chars: int) -> list[list[int]]:
    total = sum(len(w.text) for w in words) + len(words) - 1
    if total <= max_chars or len(words) < 2:
        return [list(range(len(words)))]
    best, best_cost = 1, 10**9
    for k in range(1, len(words)):
        a = sum(len(w.text) for w in words[:k]) + k - 1
        b = sum(len(w.text) for w in words[k:]) + len(words) - k - 1
        cost = max(a, b) + (0 if a <= max_chars and b <= max_chars else 100)
        if cost < best_cost:
            best, best_cost = k, cost
    return [list(range(best)), list(range(best, len(words)))]


def group_words(words: list[dict[str, Any]], style: CaptionStyle, emphasis_phrases: list[str] | None = None,
                max_gap: float = 0.45, max_chunk_dur: float = 3.2) -> list[CaptionChunk]:
    if not words:
        return []
    emph = mark_emphasis(words, emphasis_phrases or []) if style.emphasis else set()
    wpc = max(3, min(7, style.words_per_caption))
    max_chars_total = style.max_chars_per_line * style.max_lines

    chunks: list[list[int]] = []
    cur: list[int] = []
    for i, w in enumerate(words):
        if cur:
            prev = words[cur[-1]]
            chars = sum(len(words[j]["word"]) + 1 for j in cur) + len(w["word"])
            sentence_break = bool(_TERMINAL.search(prev["word"])) or bool(re.search(r"[,;:]$", prev["word"]) and len(cur) >= 3)
            if (len(cur) >= wpc or w["start"] - prev["end"] >= max_gap or sentence_break
                    or chars > max_chars_total or w["end"] - words[cur[0]]["start"] > max_chunk_dur):
                chunks.append(cur)
                cur = []
        cur.append(i)
    if cur:
        chunks.append(cur)

    # Merge 1-word orphans into the previous chunk when it keeps the chunk readable.
    merged: list[list[int]] = []
    for c in chunks:
        if (len(c) == 1 and merged and len(merged[-1]) < wpc + 1
                and words[c[0]]["start"] - words[merged[-1][-1]]["end"] < 0.3
                and not _TERMINAL.search(words[merged[-1][-1]]["word"])):
            merged[-1].extend(c)
        else:
            merged.append(c)

    out: list[CaptionChunk] = []
    for c in merged:
        cws = []
        emph_run_used = False
        in_run = False
        for j in c:
            is_e = j in emph
            if is_e and not in_run and emph_run_used:
                is_e = False  # at most one emphasised phrase per caption
            if is_e:
                in_run = True
                emph_run_used = True
            else:
                in_run = False
            cws.append(CaptionWord(text=clean_word(words[j]["word"], style), start=words[j]["start"], end=words[j]["end"],
                                   emphasis=is_e, raw=words[j]["word"]))
        cws = [w for w in cws if w.text]
        if not cws:
            continue
        out.append(CaptionChunk(start=cws[0].start, end=cws[-1].end, words=cws))

    for k, ch in enumerate(out):
        nxt = out[k + 1].start if k + 1 < len(out) else None
        end = ch.end + 0.35
        if nxt is not None:
            end = min(end, nxt) if nxt - ch.end < 0.6 else ch.end + 0.35
        ch.end = max(ch.start + 0.25, end)
        ch.lines = _split_lines(ch.words, style.max_chars_per_line)
    return out


def chunks_to_srt(chunks: list[CaptionChunk]) -> str:
    parts = []
    for i, ch in enumerate(chunks, 1):
        text = "\n".join(" ".join(ch.words[j].text for j in line) for line in ch.lines)
        parts.append(f"{i}\n{srt_timestamp(ch.start)} --> {srt_timestamp(ch.end)}\n{text}\n")
    return "\n".join(parts)


# --------------------------------------------------------------------------- rendering


class CaptionRenderer:
    """Renders caption states lazily and alpha-blends them into BGR frames."""

    def __init__(self, chunks: list[CaptionChunk], style: CaptionStyle, width: int = 1080, height: int = 1920):
        self.chunks = chunks
        self.style = style
        self.width = width
        self.height = height
        self.font_path = resolve_font(style.font, style.font_group)
        self.font = load_font(self.font_path, style.font_size, style.font_group)
        asc, desc = self.font.getmetrics() if hasattr(self.font, "getmetrics") else (style.font_size, style.font_size // 4)
        self.line_h = int((asc + desc) * style.line_spacing)
        pad = style.outline_width + style.shadow_offset + 24
        self.band_h = self.line_h * style.max_lines + pad * 2
        self.pad = pad
        self.band_y = int(height * style.position_y - self.band_h / 2)
        self.band_y = max(int(height * 0.12), min(self.band_y, int(height * 0.82) - self.band_h))
        self._cache: dict[tuple[int, int], np.ndarray] = {}
        self._starts = [c.start for c in chunks]

    def _chunk_at(self, t: float) -> int | None:
        import bisect

        i = bisect.bisect_right(self._starts, t) - 1
        if i >= 0 and self.chunks[i].start <= t < self.chunks[i].end:
            return i
        return None

    def _active_word(self, chunk: CaptionChunk, t: float) -> int:
        idx = -1
        for j, w in enumerate(chunk.words):
            if w.start <= t + 0.02:
                idx = j
        return idx

    def render_state(self, ci: int, active: int) -> np.ndarray:
        key = (ci, active if self.style.highlight_mode != "none" else -2)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        st = self.style
        chunk = self.chunks[ci]
        img = Image.new("RGBA", (self.width, self.band_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        space = draw.textlength(" ", font=self.font)
        n_lines = len(chunk.lines)
        top = self.pad + (st.max_lines - n_lines) * self.line_h // 2
        layout = []
        for li, line in enumerate(chunk.lines):
            widths = [draw.textlength(chunk.words[j].text, font=self.font) for j in line]
            total = sum(widths) + space * (len(line) - 1)
            x = (self.width - total) / 2
            y = top + li * self.line_h
            for j, wdt in zip(line, widths, strict=True):
                layout.append((j, x, y, wdt))
                x += wdt + space

        if st.box and layout:
            for li in range(n_lines):
                items = [it for it in layout if it[0] in chunk.lines[li]]
                x0 = min(it[1] for it in items) - 26
                x1 = max(it[1] + it[3] for it in items) + 26
                y0 = top + li * self.line_h - 8
                draw.rounded_rectangle([x0, y0, x1, y0 + self.line_h + 4], radius=18, fill=st.box_color)

        if st.shadow:
            sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
            sd = ImageDraw.Draw(sh)
            for j, x, y, _w in layout:
                sd.text((x + st.shadow_offset, y + st.shadow_offset), chunk.words[j].text, font=self.font,
                        fill=st.shadow_color, stroke_width=st.outline_width, stroke_fill=st.shadow_color)
            sh = sh.filter(ImageFilter.GaussianBlur(4))
            img = Image.alpha_composite(img, sh) if not st.box else Image.alpha_composite(sh, img)
            draw = ImageDraw.Draw(img)

        for j, x, y, _w in layout:
            w = chunk.words[j]
            color = st.text_color
            if w.emphasis:
                color = st.emphasis_color
            if st.highlight_mode == "word" and j == active:
                color = st.highlight_color
            elif st.highlight_mode == "fill" and j <= active:
                color = st.highlight_color
            draw.text((x, y), w.text, font=self.font, fill=color,
                      stroke_width=st.outline_width, stroke_fill=st.outline_color)

        arr = np.asarray(img, dtype=np.uint8)
        # RGBA -> BGRA (premultiplied later during blend)
        bgra = arr[:, :, [2, 1, 0, 3]].copy()
        self._cache[key] = bgra
        if len(self._cache) > 400:
            self._cache.pop(next(iter(self._cache)))
        return bgra

    def overlay(self, frame: np.ndarray, t: float) -> None:
        ci = self._chunk_at(t)
        if ci is None:
            return
        active = self._active_word(self.chunks[ci], t)
        blend_bgra(frame, self.render_state(ci, active), 0, self.band_y)


def blend_bgra(frame: np.ndarray, overlay: np.ndarray, x: int, y: int) -> None:
    """In-place alpha blend of a BGRA overlay onto a BGR frame (clipped to bounds)."""
    fh, fw = frame.shape[:2]
    oh, ow = overlay.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(fw, x + ow), min(fh, y + oh)
    if x1 <= x0 or y1 <= y0:
        return
    ov = overlay[y0 - y : y1 - y, x0 - x : x1 - x]
    alpha = ov[:, :, 3:4]
    ys, xs = np.nonzero(alpha[:, :, 0])
    if ys.size == 0:
        return
    r0, r1, c0, c1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    ov = ov[r0:r1, c0:c1]
    a = ov[:, :, 3:4].astype(np.uint16)
    roi = frame[y0 + r0 : y0 + r1, x0 + c0 : x0 + c1]
    roi[:] = ((roi.astype(np.uint16) * (255 - a) + ov[:, :, :3].astype(np.uint16) * a) // 255).astype(np.uint8)


# --------------------------------------------------------------------------- hook overlay


def wrap_text(text: str, max_chars: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def render_hook_png(text: str, out_path: str, width: int = 1080, height: int = 1920, font_name: str | None = None,
                    theme: str = "light") -> None:
    """Full-frame transparent PNG with the hook in a rounded box inside the top safe area."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    text = " ".join(text.split())[:90]
    size = 70
    font_path = resolve_font(font_name, "heavy")
    for _ in range(6):
        font = load_font(font_path, size, "heavy")
        lines = wrap_text(text, max(10, int(20 * 70 / size)))
        widest = max((draw.textlength(ln, font=font) for ln in lines), default=0)
        if widest <= width * 0.82 and len(lines) <= 3:
            break
        size -= 6
    asc, desc = font.getmetrics() if hasattr(font, "getmetrics") else (size, size // 4)
    lh = int((asc + desc) * 1.08)
    box_w = int(widest + 70)
    box_h = lh * len(lines) + 44
    x0 = (width - box_w) // 2
    y0 = int(height * 0.15)  # below platform top bars
    bg, fg = ((255, 255, 255, 245), (10, 10, 10, 255)) if theme == "light" else ((12, 12, 12, 230), (255, 255, 255, 255))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([x0 + 4, y0 + 8, x0 + box_w + 4, y0 + box_h + 8], radius=26, fill=(0, 0, 0, 90))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(8)))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=26, fill=bg)
    for i, ln in enumerate(lines):
        lw = draw.textlength(ln, font=font)
        draw.text(((width - lw) / 2, y0 + 22 + i * lh), ln, font=font, fill=fg)
    img.save(out_path)
