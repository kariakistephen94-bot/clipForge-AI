"""Sound-effect library: scan, analyse and classify the user's own SFX files.

Sources: ``workspace/sfx/`` plus an optional external folder (Settings -> Sound design -> library folder).
Every file is decoded once (16 kHz mono) and measured:

* ``lead`` / ``peak`` / ``tail`` -- where the sound actually starts, hits hardest and fades out, so a pop can
  land exactly on a word and a whoosh can peak exactly on a cut even when the file has silence padding;
* ``loud_db`` -- loudness of the loudest 300 ms, measured with a perceptual (BS.1770-style K) weighting so a
  sub-bass boom is not mistaken for a loud sound: it rolls off the sub-bass nobody hears on a phone and lifts the
  presence range. Every file is levelled to a per-category target of this measure before mixing;
* ``centroid`` -- spectral brightness, used to classify files whose names say nothing ("SOUND 3.mp3").

Files that are really packs (several separated sounds in one file, e.g. "swipes.mp3") are split into
individual one-shots. Long continuous files become music beds and are never used as effects.
Results are cached in ``workspace/cache/sfx_index.json`` keyed by path + size + mtime, so rescans only
analyse new or changed files. User corrections (category / enabled) live in the preferences table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..db import session_scope
from ..models import Preference
from ..paths import workspace

log = logging.getLogger(__name__)

AUDIO_EXT = {".wav", ".mp3", ".aif", ".aiff", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".mp4", ".mov"}
INDEX_VERSION = 6
ANALYSIS_RATE = 16000
MAX_DECODE_S = 120.0
OVERRIDES_KEY = "sfx_overrides"


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    anchor: str  # onset | peak | end  -- which point of the sound lands on the event time
    max_len: float  # longest excerpt used, seconds
    target_db: float  # perceived level of the loudest 300 ms at default volume; speech sits around -14 here
    duck: bool  # soften under speech (beds, risers)
    auto: bool  # may be placed automatically (others only when the AI explicitly asks / playful mode)
    description: str


CATEGORIES: dict[str, Category] = {c.key: c for c in (
    Category("whoosh", "Whoosh / transition", "peak", 1.6, -17, False, True, "Hook entrances, punch-ins, B-roll and cut transitions"),
    Category("impact", "Impact / boom", "peak", 2.6, -13, False, True, "Punchlines, reveals and big statements"),
    Category("riser", "Riser / build-up", "end", 3.5, -19, True, True, "Builds tension into a payoff"),
    Category("pop", "Pop", "onset", 0.6, -18, False, True, "Caption emphasis and text pop-ins"),
    Category("click", "Click / UI", "onset", 0.5, -19, False, True, "Lists, steps and UI moments"),
    Category("ding", "Ding / notification", "onset", 1.4, -17, False, True, "Ideas, lessons, correct answers"),
    Category("cash", "Cash / money", "onset", 1.6, -17, False, True, "Money, prices, revenue"),
    Category("camera", "Camera shutter", "onset", 0.8, -18, False, True, "Photos, screenshots, freeze moments"),
    Category("typing", "Typing / writing", "onset", 1.8, -23, True, True, "Writing, emails, texting, coding"),
    Category("paper", "Paper", "onset", 1.2, -21, False, True, "Notes, contracts, pages"),
    Category("tech", "Glitch / tech", "onset", 1.5, -19, False, True, "AI, software, data, hacking"),
    Category("wrong", "Wrong / error", "onset", 1.2, -17, False, True, "Mistakes, failures, a hard no"),
    Category("tick", "Clock tick", "onset", 2.0, -24, True, True, "Time pressure, deadlines"),
    Category("tension", "Tension / suspense", "onset", 3.0, -22, True, True, "Suspense before a reveal"),
    Category("reaction", "Crowd / reaction", "onset", 1.8, -18, True, False, "Audience reactions (playful mode)"),
    Category("comedic", "Comedic", "onset", 1.2, -16, False, False, "Cartoon / meme sounds (playful mode, humour only)"),
    Category("music", "Music bed", "onset", 0.0, -26, True, False, "Background music -- offered as a music bed, never as an effect"),
    Category("other", "Unsorted", "onset", 1.5, -19, False, False, "Not used automatically until you assign a category"),
)}

# Filename keywords, first match wins -- order matters ("Paper Slide" is paper, not a slide whoosh).
NAME_RULES: list[tuple[str, re.Pattern[str]]] = [(k, re.compile(p, re.I)) for k, p in (
    ("music", r"\b(music|background|instrumental|lo-?fi|beat|ambition|roll-out|zay-zay|purple-js|puddle)\b|mixkit-21-|trap-\d+"),
    ("paper", r"paper|crumpl|page flip|\bflip\b"),
    ("camera", r"camera|shutter|photo|\bflash\b"),
    ("cash", r"cash|register|ka-?ching|\bcoin|money|\bracks\b|purchase|\bting\b"),
    ("riser", r"riser|build-?up|ascending|swell|rising|uplift"),
    ("tick", r"clock|\btick"),
    ("typing", r"typing|keyboard|typewriter|writing|pencil|\bkeys\b"),
    ("comedic", r"fart|splat|goofy|whistle|\bdog\b|barking|cartoon|boing|bone|animal crossing|censor|minecraft"),
    ("pop", r"\bpop|bloop|bubble|cork"),
    ("reaction", r"crowd|shocked|applause|\ba+w+\b|\bye+y+\b|\byay\b|cheer|party horn|laugh|gasp|\bhmm+|boxing bell|drum ?roll"),
    ("wrong", r"wrong|error|\bfail|denied|disconnect|buzz|discord_leave"),
    ("whoosh", r"whoo?sh|woosh|swoosh|swish|swipe|swing|transition|warp|arrow|fast.?forward|rewind|zoom|portal|lens flare|\bslide"),
    ("tension", r"suspense|spooky|\bwind\b|tension|heart ?beat|beating|pulses|eerie"),
    ("impact", r"boom|impact|\bhit\b|slam|\bdrop|thud|punch|crash|struck|subsonic|\bdeep\b|bass|brass|shatter|low quick|shot light"),
    ("tech", r"glitch|hack|\bdata\b|digital|digits|processing|loading|download|network|dial-?up|disc read|erased|rf switch|"
             r"intermodulation|line break|terminal|termainal|sci[- ]?fi|electric|static|power-?up|restart|futuristic|computer|gear"),
    ("ding", r"ding|notification|correct|good.?idea|new idea|quick-?win|access granted|message|iphone|chime|success|discord_join|\bbell\b"),
    ("click", r"click|\bpress\b|select|\btap\b"),
)]

BED_CATEGORIES = {"music", "tick", "typing", "tension", "reaction"}  # continuous by nature: never sliced


@dataclass
class SfxItem:
    id: str
    path: str
    name: str
    category: str
    auto_category: str
    category_source: str  # name | acoustic | user
    duration: float  # of the file, or of the slice
    offset: float = 0.0  # slice start inside the file
    lead: float = 0.0  # relative to offset
    peak: float = 0.0
    tail: float = 0.0
    loud_db: float = -30.0
    peak_db: float = 0.0
    centroid: float = 0.0
    slice_index: int | None = None
    enabled: bool = True
    pack: bool = False  # parent file of slices (itself not used)
    channels: int = 2

    @property
    def active(self) -> float:
        return max(0.0, self.tail - self.lead)

    @property
    def usable(self) -> bool:
        return (self.enabled and not self.pack and self.category not in ("music",) and self.active >= 0.04
                and self.loud_db > -50.0)  # near-silent files would only add boosted noise

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["label"] = self.name if self.slice_index is None else f"{self.name} #{self.slice_index + 1}"
        d["folder"] = str(Path(self.path).parent)
        return d


def item_id(path: str, slice_index: int | None = None) -> str:
    return hashlib.sha1(f"{path}#{slice_index}".encode()).hexdigest()[:12]


# --------------------------------------------------------------------------- classification


def classify_name(name: str) -> str | None:
    stem = re.sub(r"[_]+", " ", Path(name).stem)
    for key, rx in NAME_RULES:
        if rx.search(stem) or rx.search(Path(name).stem):
            return key
    return None


def classify_acoustic(active: float, rel_peak: float, centroid: float) -> str:
    """Best guess from the envelope when the filename is uninformative."""
    if active < 0.25:
        return "click" if centroid > 2500 else "pop"
    if active >= 0.9 and rel_peak > 0.72:
        return "riser"  # swells into the end (also reverse whooshes)
    if centroid < 750 and rel_peak < 0.35:
        return "impact"
    if active <= 2.4:
        return "whoosh"
    return "tension"


# --------------------------------------------------------------------------- measurement (pure numpy)


# Perceptual weighting (BS.1770 "K" shape, applied in the frequency domain -- no scipy needed):
# a 2nd-order high-pass and a high-frequency shelf. Sub-bass counts for little, presence counts for more.
# The high-pass sits at 100 Hz rather than BS.1770's 38 Hz: clips are watched on phones and laptops, where
# sub-bass is barely reproduced, so a 40 Hz boom should be judged (and levelled) by its audible part.
K_SHELF_HZ, K_SHELF_DB, K_HIGHPASS_HZ = 1681.0, 4.0, 100.0


def k_weights(freqs: np.ndarray) -> np.ndarray:
    """Power (not amplitude) weighting for each frequency bin."""
    g = 10 ** (K_SHELF_DB / 20)
    shelf = (1 + (g * freqs / K_SHELF_HZ) ** 2) / (1 + (freqs / K_SHELF_HZ) ** 2)
    r = (freqs / K_HIGHPASS_HZ) ** 4
    return shelf * (r / (1 + r))


def weighted_power(a: np.ndarray, rate: int, hop: int, win: int = 512) -> np.ndarray:
    """Perceptually weighted mean power per frame (hop-spaced), comparable to a squared RMS."""
    n = max(1, (a.size - win) // hop + 1)
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = a[np.clip(idx, 0, a.size - 1)].astype(np.float64)
    w = np.hanning(win)
    spec = np.abs(np.fft.rfft(frames * w, axis=1)) ** 2
    weights = k_weights(np.fft.rfftfreq(win, 1 / rate))
    scale = 2.0 / (win * (w ** 2).sum() / win) / win  # Parseval: unweighted sum == mean(x^2)
    return (spec * weights).sum(axis=1) * scale


@dataclass
class Envelope:
    lead: float
    peak: float
    tail: float
    loud_db: float
    peak_db: float
    centroid: float
    regions: list[tuple[float, float]] = field(default_factory=list)


def measure(a: np.ndarray, rate: int = ANALYSIS_RATE, hop_s: float = 0.01) -> Envelope | None:
    """Envelope features of a mono float signal. None when the signal is silent."""
    if a.size < int(rate * 0.02):
        return None
    hop = max(1, int(rate * hop_s))
    n = a.size // hop
    frames = a[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    top = float(db.max())
    if top < -70:
        return None
    thr = max(top - 30.0, -65.0)
    active = np.where(db > thr)[0]
    lead, tail = active[0] * hop_s, (active[-1] + 1) * hop_s
    peak = float(np.argmax(db)) * hop_s
    # perceived loudness of the loudest 300 ms (what we level against)
    wp = weighted_power(a, rate, hop)
    win = max(1, int(0.3 / hop_s))
    if wp.size >= win:
        csum = np.cumsum(np.concatenate([[0.0], wp]))
        loud_db = float(10 * np.log10(((csum[win:] - csum[:-win]) / win).max() + 1e-12))
    else:
        loud_db = float(10 * np.log10(wp.mean() + 1e-12))
    seg = a[int(lead * rate): int(min(tail, lead + 3.0) * rate)]
    if seg.size >= 256:
        spec = np.abs(np.fft.rfft(seg * np.hanning(seg.size)))
        freqs = np.fft.rfftfreq(seg.size, 1 / rate)
        centroid = float((spec * freqs).sum() / (spec.sum() + 1e-9))
    else:
        centroid = 0.0
    # separated sounds (for pack detection): regions above a stricter gate, merging gaps < 0.25 s
    gate = db > max(top - 35.0, -60.0)
    regions: list[tuple[float, float]] = []
    start = None
    for i, on in enumerate(gate):
        if on and start is None:
            start = i
        elif not on and start is not None:
            regions.append((start * hop_s, i * hop_s))
            start = None
    if start is not None:
        regions.append((start * hop_s, n * hop_s))
    merged: list[list[float]] = []
    for s, e in regions:
        if merged and s - merged[-1][1] < 0.25:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    peak_db = float(20 * np.log10(np.abs(a).max() + 1e-9))
    return Envelope(lead=round(lead, 3), peak=round(peak, 3), tail=round(tail, 3), loud_db=round(loud_db, 2),
                    peak_db=round(peak_db, 2), centroid=round(centroid, 1),
                    regions=[(round(s, 3), round(e, 3)) for s, e in merged if e - s >= 0.08])


def is_pack(duration: float, env: Envelope, category: str | None) -> bool:
    if category in BED_CATEGORIES or duration <= 6.0 or len(env.regions) < 3:
        return False
    lengths = sorted(e - s for s, e in env.regions)
    return lengths[len(lengths) // 2] <= 3.0


def _decode(path: Path) -> np.ndarray | None:
    from .ffmpeg import ffmpeg_bin

    try:
        out = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", "0:a:0", "-t", str(MAX_DECODE_S),
                              "-ac", "1", "-ar", str(ANALYSIS_RATE), "-f", "f32le", "-"],
                             capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("SFX decode failed for %s: %s", path.name, e)
        return None
    if out.returncode != 0 or not out.stdout:
        return None
    return np.frombuffer(out.stdout, dtype=np.float32)


def probe_audio(path: Path) -> tuple[float | None, int]:
    """(duration, channels of the first audio stream)."""
    from .ffmpeg import ffprobe_bin

    try:
        out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries",
                              "format=duration:stream=channels", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout)
        dur = float(data["format"]["duration"])
        ch = int((data.get("streams") or [{}])[0].get("channels") or 2)
        return dur, ch
    except (OSError, subprocess.SubprocessError, KeyError, ValueError, TypeError, IndexError):
        return None, 2


def analyze_file(path: Path) -> list[SfxItem]:
    """Measure one file. Packs return the parent (pack=True) followed by one item per slice."""
    samples = _decode(path)
    if samples is None or samples.size == 0:
        return []
    probed, channels = probe_audio(path)
    duration = probed or samples.size / ANALYSIS_RATE
    env = measure(samples)
    if env is None:
        return []
    by_name = classify_name(path.name)
    spath = str(path)

    def build(e: Envelope, dur: float, offset: float, idx: int | None, name_cat: str | None) -> SfxItem:
        active = max(0.01, e.tail - e.lead)
        acoustic = classify_acoustic(active, (e.peak - e.lead) / active, e.centroid)
        if name_cat:
            cat, src = name_cat, "name"
        elif idx is None and dur >= 25.0:
            cat, src = "music", "acoustic"  # long and continuous
        else:
            cat, src = acoustic, "acoustic"
        return SfxItem(id=item_id(spath, idx), path=spath, name=path.name, category=cat, auto_category=cat,
                       category_source=src, duration=round(dur, 3), offset=round(offset, 3), lead=e.lead, peak=e.peak,
                       tail=e.tail, loud_db=e.loud_db, peak_db=e.peak_db, centroid=e.centroid, slice_index=idx,
                       channels=channels)

    if not is_pack(duration, env, by_name):
        return [build(env, duration, 0.0, None, by_name)]
    parent = build(env, duration, 0.0, None, by_name)
    parent.pack = True
    items = [parent]
    for i, (s, e) in enumerate(env.regions[:24]):
        s0 = max(0.0, s - 0.03)
        e0 = min(samples.size / ANALYSIS_RATE, e + 0.08)
        if e0 - s0 > 4.5 or e0 - s0 < 0.1:
            continue
        sub = measure(samples[int(s0 * ANALYSIS_RATE): int(e0 * ANALYSIS_RATE)])
        if sub is None or sub.loud_db < env.loud_db - 20.0:
            continue  # a quiet tail or background rumble between the real sounds
        items.append(build(sub, e0 - s0, s0, i, by_name if by_name != "music" else None))
    return items


# --------------------------------------------------------------------------- library


def workspace_sfx_dir() -> Path:
    p = workspace() / "sfx"
    p.mkdir(parents=True, exist_ok=True)
    return p


def library_dirs(external: str | None = None) -> list[Path]:
    dirs = [workspace_sfx_dir()]
    if external is None:
        from .preferences import get_preferences

        external = get_preferences().sfx_library_path
    if external:
        p = Path(external).expanduser()
        if p.is_dir() and p.resolve() not in {d.resolve() for d in dirs}:
            dirs.append(p)
    return dirs


def _index_path() -> Path:
    p = workspace() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p / "sfx_index.json"


def _audio_files(dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for d in dirs:
        for f in sorted(d.rglob("*")):
            if f.is_file() and f.suffix.lower() in AUDIO_EXT and not f.name.startswith("."):
                files.append(f)
    return files


def get_overrides() -> dict[str, dict[str, Any]]:
    with session_scope() as s:
        row = s.get(Preference, OVERRIDES_KEY)
        return dict(row.value) if row and isinstance(row.value, dict) else {}


def set_override(sid: str, category: str | None = None, enabled: bool | None = None) -> dict[str, Any]:
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"Unknown category {category}")
    with session_scope() as s:
        row = s.get(Preference, OVERRIDES_KEY)
        data = dict(row.value) if row and isinstance(row.value, dict) else {}
        cur = dict(data.get(sid, {}))
        if category is not None:
            cur["category"] = category
        if enabled is not None:
            cur["enabled"] = enabled
        data[sid] = cur
        s.merge(Preference(key=OVERRIDES_KEY, value=data))
    return cur


_scan_lock = threading.Lock()
_scan_state: dict[str, Any] = {"running": False, "done": 0, "total": 0}


def scan_status() -> dict[str, Any]:
    return dict(_scan_state)


def scan_library(dirs: list[Path] | None = None, force: bool = False) -> list[SfxItem]:
    """Incremental scan; returns every item (packs included) with user overrides applied."""
    dirs = dirs if dirs is not None else library_dirs()
    with _scan_lock:
        idx_path = _index_path()
        cache: dict[str, Any] = {}
        if idx_path.exists() and not force:
            try:
                raw = json.loads(idx_path.read_text(encoding="utf-8"))
                if raw.get("version") == INDEX_VERSION:
                    cache = raw.get("files", {})
            except (OSError, ValueError):
                cache = {}
        files = _audio_files(dirs)
        fresh: dict[str, Any] = {}
        todo = []
        for f in files:
            st = f.stat()
            key = str(f)
            hit = cache.get(key)
            if hit and hit.get("size") == st.st_size and abs(hit.get("mtime", 0) - st.st_mtime) < 1e-3:
                fresh[key] = hit
            else:
                todo.append((f, st))
        _scan_state.update(running=bool(todo), done=0, total=len(todo))
        try:
            for n, (f, st) in enumerate(todo, 1):
                items = analyze_file(f)
                fresh[str(f)] = {"size": st.st_size, "mtime": st.st_mtime, "items": [asdict(i) for i in items]}
                _scan_state["done"] = n
        finally:
            _scan_state["running"] = False
        if todo or set(cache) != set(fresh):
            tmp = idx_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": INDEX_VERSION, "files": fresh}), encoding="utf-8")
            tmp.replace(idx_path)
            if todo:
                log.info("SFX library: analysed %d new/changed file(s), %d total", len(todo), len(files))
    overrides = get_overrides()
    out: list[SfxItem] = []
    for entry in fresh.values():
        for d in entry["items"]:
            item = SfxItem(**d)
            ov = overrides.get(item.id) or {}
            if ov.get("category") in CATEGORIES:
                item.category, item.category_source = ov["category"], "user"
            if isinstance(ov.get("enabled"), bool):
                item.enabled = ov["enabled"]
            out.append(item)
    return out


def library_summary(items: list[SfxItem]) -> dict[str, Any]:
    counts: dict[str, int] = {k: 0 for k in CATEGORIES}
    for i in items:
        if i.usable or (i.category == "music" and i.enabled and not i.pack):
            counts[i.category] = counts.get(i.category, 0) + 1
    return {"counts": counts, "files": len({i.path for i in items}), "items": len([i for i in items if not i.pack])}


def find_item(items: list[SfxItem], sid: str) -> SfxItem | None:
    return next((i for i in items if i.id == sid), None)


def music_beds(items: list[SfxItem]) -> list[SfxItem]:
    return [i for i in items if i.category == "music" and i.enabled and not i.pack and i.slice_index is None]
