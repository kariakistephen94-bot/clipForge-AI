"""Caption animation lab: render test clips in every animated style and build a comparison page.

    backend/.venv/bin/python lab/captions/run_lab.py            # all clips, all styles
    backend/.venv/bin/python lab/captions/run_lab.py pop box    # only some styles

Output goes to lab/captions/out/ (index.html compares the styles side by side). The main app is not touched:
the base clips are rendered by the real pipeline with captions switched off, then each style is burned on.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from anim_captions import STYLES, AnimatedCaptionRenderer  # noqa: E402

from app.services.ffmpeg import EncodeSettings, build_decode_cmd, build_encode_cmd, ffmpeg_bin  # noqa: E402
from app.services.render import RenderInput, RenderOptions, render_clip  # noqa: E402
from app.services.silence import remap_words  # noqa: E402
from app.services.transcribe import words_in_range  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
PODCAST = ROOT / "workspace/projects/pb448c32519e4/source/original.mp4"
TRANSCRIPT = ROOT / "workspace/cache/fb25a6f7fa10aadb46c1eef73cfbe369b3457384615c44b2d27214a21d85f764/transcript_base.json"

# name, source, start, end, emphasis phrases (as the AI would suggest them)
CLIPS = [
    ("handover", PODCAST, 9968.0, 9998.0, ["long form content", "the doom"]),
    ("daydream", PODCAST, 10221.5, 10248.0, ["real for you", "powerless", "the most powerful person"]),
]


def load_words() -> list[dict]:
    data = json.loads(TRANSCRIPT.read_text())
    return [w for sg in data["segments"] for w in sg["words"]]


def base_clip(name: str, src: Path, start: float, end: float, words: list[dict]) -> tuple[Path, list[dict]]:
    """Vertical clip without captions (real pipeline: silence cuts, speaker framing, zooms) + its word timings."""
    wd = OUT / f"_base_{name}"
    meta = wd / "meta.json"
    if meta.exists():
        m = json.loads(meta.read_text())
        return Path(m["video"]), m["words"]
    inp = RenderInput(source_path=str(src), src_width=1920, src_height=1080, has_audio=True, start=start, end=end,
                      words=words, emphasis=[], zoom_points=[], broll_points=[], hooks=[], rules=None, workdir=wd)
    res = render_clip(inp, RenderOptions(captions=False, variants=["C"]))
    clip_words = remap_words(words_in_range(words, start, end), res.keep_segments)
    meta.write_text(json.dumps({"video": res.base_path, "words": clip_words}))
    return Path(res.base_path), clip_words


def burn(video: Path, renderer, out: Path) -> float:
    """Burn captions from any renderer with an overlay(frame, t) method onto `video`."""
    ff = ffmpeg_bin()
    W, H, fps = 1080, 1920, 30
    audio = out.with_suffix(".wav")
    subprocess.run([ff, "-v", "error", "-y", "-i", str(video), "-vn", "-c:a", "pcm_s16le", str(audio)], check=True)
    dec = subprocess.Popen(build_decode_cmd(video, W, H, ffmpeg=ff), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    enc = subprocess.Popen(build_encode_cmd(audio, out, EncodeSettings(width=W, height=H, fps=fps, crf=20), ffmpeg=ff),
                           stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert dec.stdout and enc.stdin
    t0, i, overlay_time = time.time(), 0, 0.0
    while True:
        buf = dec.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
        s = time.time()
        renderer.overlay(frame, i / fps)
        overlay_time += time.time() - s
        enc.stdin.write(frame.tobytes())
        i += 1
    enc.stdin.close()
    dec.wait()
    enc.wait()
    audio.unlink(missing_ok=True)
    print(f"  {out.name}: {i} frames in {time.time() - t0:.1f}s (captions {1000 * overlay_time / max(1, i):.1f} ms/frame)")
    return overlay_time / max(1, i)


def strip(video: Path, out: Path, start: float = 1.0, count: int = 8, step: float = 0.25) -> None:
    """A row of frames `step` s apart, cropped to the caption area, to see the animation at a glance."""
    vf = f"fps={1 / step},crop=1080:700:0:980,scale=300:-1,tile={count}x1"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-ss", str(start), "-i", str(video), "-vf", vf,
                    "-frames:v", "1", str(out)], check=True)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Caption Lab</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{margin:0;background:#0b0d12;color:#e7e9ee;font:15px/1.5 -apple-system,system-ui,sans-serif}
header{padding:20px 24px 4px}h1{margin:0;font-size:22px}p{margin:4px 0;color:#9aa3b2}
h2{margin:22px 24px 8px;font-size:17px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px;padding:0 24px}
.card{background:#141824;border:1px solid #252d40;border-radius:12px;padding:10px}
.card b{display:block;margin-bottom:2px}.card small{color:#9aa3b2;display:block;min-height:40px}
video{width:100%;border-radius:8px;background:#000;aspect-ratio:9/16;margin-top:6px}
.strips img{display:block;max-width:100%;margin:6px 24px;border-radius:8px}
.row{display:flex;gap:8px;padding:0 24px}button{background:#252d40;color:#fff;border:0;border-radius:8px;padding:8px 14px;cursor:pointer}
</style></head><body>
<header><h1>Caption animation lab</h1>
<p>Test renders only &mdash; the main app still uses the current static captions until a style is approved.</p></header>
<div class="row"><button onclick="document.querySelectorAll('video').forEach(v=>{v.currentTime=0;v.play()})">Play all from start</button>
<button onclick="document.querySelectorAll('video').forEach(v=>v.muted=!v.muted)">Toggle sound</button></div>
BODY</body></html>"""

DESCRIPTIONS = {
    "current": "What the app renders today: static words, current word turns yellow.",
    "pop": "Hormozi style. Words pop in with a springy overshoot as they're spoken; current word yellow and bigger, key phrases green.",
    "box": "Submagic / MrBeast style. The caption pops in and a purple box glides behind the word being spoken.",
    "single": "One big word at a time (Coolvetica), punching in with overshoot and a slight tilt.",
    "rise": "Clean modern. Mixed case, words slide up and fade in as spoken; current word teal.",
}


def current_renderer(words: list[dict], emphasis: list[str]):
    """The app's existing captions, for comparison on the same base clip."""
    from app.services.captions import CaptionRenderer, group_words, style_from

    style = style_from("bold_viral")
    return CaptionRenderer(group_words(words, style, emphasis), style, 1080, 1920)


def main() -> None:
    only = [s for s in sys.argv[1:] if s in STYLES]  # re-render just these; the page always lists every style
    OUT.mkdir(parents=True, exist_ok=True)
    words = load_words()
    body = []
    for name, src, start, end, emphasis in CLIPS:
        print(f"== {name}")
        video, clip_words = base_clip(name, src, start, end, words)
        cards, strips = [], []
        for style in ["current", *STYLES]:
            out = OUT / f"{name}_{style}.mp4"
            if style == "current":
                if not out.exists():
                    burn(video, current_renderer(clip_words, emphasis), out)
            elif not out.exists() or not only or style in only:
                burn(video, AnimatedCaptionRenderer(clip_words, style, 1080, 1920, emphasis_phrases=emphasis), out)
            strip(out, OUT / f"{name}_{style}.jpg")
            cards.append(f'<div class="card"><b>{style}</b><small>{DESCRIPTIONS[style]}</small>'
                         f'<video src="{out.name}#t=1.2" controls muted loop playsinline preload="metadata"></video></div>')
            strips.append(f'<p style="margin:10px 24px 0">{style} &mdash; frames 0.25 s apart</p>'
                          f'<img src="{name}_{style}.jpg" alt="{style} frame strip">')
        body.append(f'<h2>{name}</h2><div class="grid">{"".join(cards)}</div><div class="strips">{"".join(strips)}</div>')
    (OUT / "index.html").write_text(PAGE.replace("BODY", "".join(body)))
    print(f"open {OUT / 'index.html'}")


if __name__ == "__main__":
    main()
