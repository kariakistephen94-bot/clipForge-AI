"""Locate fonts that are already installed on this machine. No font files are bundled."""

from __future__ import annotations

import logging
import os
import platform
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

log = logging.getLogger(__name__)

FONT_EXT = {".ttf", ".otf", ".ttc"}

PREFERRED: dict[str, list[str]] = {
    # heavy display faces for "Bold Viral"
    "heavy": ["Arial Black", "ariblk", "Impact", "impact", "Montserrat-Black", "Montserrat-ExtraBold", "Anton-Regular",
              "DejaVuSans-Bold", "LiberationSans-Bold", "Arial Bold", "arialbd"],
    # clean bold sans
    "bold": ["Avenir Next", "HelveticaNeue", "Arial Bold", "arialbd", "segoeuib", "Verdana Bold", "verdanab",
             "DejaVuSans-Bold", "LiberationSans-Bold", "NotoSans-Bold"],
    "regular": ["Avenir Next", "HelveticaNeue", "Arial", "arial", "segoeui", "Verdana", "DejaVuSans", "LiberationSans-Regular"],
}


def font_dirs() -> list[Path]:
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        dirs = ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts", str(home / "Library/Fonts")]
    elif system == "Windows":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs = [os.path.join(windir, "Fonts"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    else:
        dirs = ["/usr/share/fonts", "/usr/local/share/fonts", str(home / ".fonts"), str(home / ".local/share/fonts")]
    return [Path(d) for d in dirs if d and Path(d).is_dir()]


@lru_cache
def list_fonts() -> dict[str, str]:
    """Map lower-case file stem -> path."""
    found: dict[str, str] = {}
    for d in font_dirs():
        for root, _dirs, files in os.walk(d):
            if root.count(os.sep) - str(d).count(os.sep) > 3:
                continue
            for f in files:
                p = Path(root) / f
                if p.suffix.lower() in FONT_EXT:
                    found.setdefault(p.stem.lower(), str(p))
    return found


def resolve_font(name: str | None, group: str = "heavy") -> str | None:
    fonts = list_fonts()
    if name:
        p = Path(name)
        if p.is_file() and p.suffix.lower() in FONT_EXT and any(str(p.resolve()).startswith(str(d.resolve())) for d in font_dirs()):
            return str(p)
        hit = fonts.get(Path(name).stem.lower())
        if hit:
            return hit
    for cand in PREFERRED.get(group, []) + PREFERRED["bold"]:
        hit = fonts.get(cand.lower())
        if hit:
            return hit
    return None


_WEIGHT_WORDS = {"heavy": ("black", "heavy", "extrabold", "bold"), "bold": ("demi bold", "bold", "semibold", "medium"),
                 "regular": ("regular", "medium", "book")}


@lru_cache(maxsize=64)
def load_font(path: str | None, size: int, group: str = "heavy") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path:
        try:
            if path.lower().endswith(".ttc"):
                best_idx, best_rank = 0, 99
                wants = _WEIGHT_WORDS.get(group, ("bold",))
                for idx in range(0, 24):
                    try:
                        f = ImageFont.truetype(path, size, index=idx)
                    except OSError:
                        break
                    style = (f.getname()[1] or "").lower()
                    if "italic" in style or "oblique" in style or "condensed" in style:
                        continue
                    for rank, w in enumerate(wants):
                        if w in style and rank < best_rank:
                            best_idx, best_rank = idx, rank
                return ImageFont.truetype(path, size, index=best_idx)
            return ImageFont.truetype(path, size)
        except OSError as e:
            log.warning("Could not load font %s (%s); using fallback", path, e)
    log.warning("No suitable system font found; using Pillow's built-in font")
    return ImageFont.load_default(size)
