# ClipForge AI

Turn long-form videos you have permission to clip into ranked, campaign-compliant, ready-to-post vertical shorts for
**TikTok, Instagram Reels and YouTube Shorts**.

- **Gemini** watches *and* listens to the whole video and proposes 10–20 moments with an *AI Viral Potential Score* (an estimate, not a guarantee)
- **faster-whisper** transcribes locally with word timestamps; clip boundaries snap to natural speech
- **OpenCV** keeps the speaker framed in 9:16; **FFmpeg** renders 1080×1920 clips with animated captions, hooks and clean audio
- Campaign rules are parsed into structured requirements and every clip is checked: **COMPLIANT / WARNING / FAILED**
- **Sound design** from your own SFX library: whooshes on hook entrances and punch-ins, a riser into an impact on the
  payoff, cash / ding / shutter / typing sounds when the words call for it, small pops on emphasis words — levelled,
  aligned to where each sound really hits, and kept under the voice
- **Long-form clips** too: the right number of 16:9 YouTube episodes for the source length (≈1–2 for 15 min, ≈5 for an
  hour, ≈9 for three hours), each with titles, chapters, a cold open, description, tags and **high-CTR thumbnail prompts**
  plus real reference frames and text drafts
- Finished clips land in `READY_TO_POST/` with posting copy and a `posting_plan.csv` (long-form in `READY_TO_POST/LONG_FORM/`)

No botting, fake engagement or platform manipulation — this is a content-production tool.

## Quick start (macOS / Linux)

```bash
./start.sh
```

It checks Python 3.12/3.13, FFmpeg and Node, installs dependencies, downloads the free OpenCV face model, builds the
dashboard, starts the backend and opens **http://127.0.0.1:8765**.

Windows: double-click `start.bat` (runs `start.ps1`; not yet tested on Windows).

### Gemini API key (optional but recommended)

```bash
cp .env.example .env      # start.sh does this for you
# edit .env
GEMINI_API_KEY=your-key   # https://aistudio.google.com/apikey
GEMINI_MODEL=             # blank = gemini-3.8-flash; any video-capable Gemini model works
```

Restart after editing. Without a key, ClipForge runs in **Demo mode**: everything works (transcription, candidates,
rendering, exports) but candidates come from offline text heuristics and are labelled `[DEMO]`.
The key never leaves the backend.

## Requirements

| | |
|---|---|
| Python | 3.12 or 3.13 (start.sh can install 3.12 with `uv`) |
| FFmpeg + ffprobe | macOS `brew install ffmpeg` · Ubuntu `sudo apt install ffmpeg` · Windows `winget install Gyan.FFmpeg` |
| Node.js | 18+ (only to build the dashboard) |
| Disk | Whisper models 75 MB–1.5 GB, plus renders |

## Using it

1. **New project** → name, campaign, upload a video (or paste a public YouTube URL), paste the full campaign rules,
   choose platforms, clip count and duration (Auto reads it from the rules), confirm you have permission.
2. **Analyze Source** (review first) or **Generate Clips** (analyze and render the top picks automatically).
3. Watch live progress: metadata → transcription → rule parsing → Gemini analysis → ranking.
4. Review candidates: preview, read why each was chosen, adjust start/end (snaps to speech), pick or write a hook, reject weak ones, tick *Include in batch render*.
5. Choose render options (caption style, variants A/B/C, framing, silence removal, zooms) and **Generate**.
6. Open `READY_TO_POST` — each folder has the MP4(s), thumbnail, captions, transcript, metadata and posting copy.

### Output

```
workspace/projects/<id>/READY_TO_POST/
  01_<topic>/clip_003.mp4                  variant A (recommended hook)
  01_<topic>/clip_003_variant_B_alt_hook.mp4
  01_<topic>/clip_003_variant_C_no_hook.mp4
  01_<topic>/thumbnail.jpg thumbnail_prompt.txt captions.srt transcript.txt metadata.json posting_copy.txt compliance.json
  posting_plan.csv
  LONG_FORM/01_<title>/long_001.mp4             16:9, chapters embedded
  LONG_FORM/01_<title>/thumbnail_draft_A.jpg thumbnail_frame_A.jpg thumbnail_prompts.txt/.json
  LONG_FORM/01_<title>/description.txt chapters.txt captions.srt transcript.txt metadata.json
  LONG_FORM/long_form_plan.csv
```

## Publishing to YouTube / TikTok (optional)

Connect your own accounts and upload finished clips from the dashboard: press **Publish…** on a rendered clip,
check the title/caption/visibility, confirm, and the upload runs in the background.

* Register your own free developer apps and paste the keys into `.env` — step-by-step in **[PUBLISHING.md](PUBLISHING.md)**
* Nothing posts automatically: every publish is one confirmed click, and clips that FAIL compliance are refused
* Safe defaults: YouTube **private**, TikTok **drafts**; posting publicly takes one extra tick
* OAuth tokens live in `workspace/credentials/` (chmod 600) — never in the database, the browser or the logs

## Manual commands

```bash
# backend
uv venv --python 3.12 backend/.venv            # or: python3.12 -m venv backend/.venv
uv pip install --python backend/.venv/bin/python -r backend/requirements.txt
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765

# frontend (dev server with hot reload on :5173, proxies /api to :8765)
npm --prefix frontend install
npm --prefix frontend run dev
# or build once and let the backend serve it on :8765
npm --prefix frontend run build
```

`./start.sh --dev` runs both with hot reload; `./start.sh --check` only verifies dependencies.

## Tests & checks

```bash
cd backend && .venv/bin/python -m pytest          # 193 tests
cd backend && .venv/bin/ruff check app tests
cd frontend && npm run build && npm run lint
```

## Costs

Everything except Gemini is free and local. To keep Gemini costs down, ClipForge:
- uploads each source once (cached file reference) and sends a lightweight proxy when the original is heavy
- analyses the whole video in a **single** multimodal request, using low media resolution for long videos
- caches every AI response by source hash + prompt version + model + campaign-rule hash
- uses small text-only requests for hooks/copy and compliance (one per render batch)

The project page shows Gemini requests, video uploads, cached reuses and token counts.

## Sound design

Settings → **Sound design** → *Sound library folder*: point it at your SFX folder (any mix of WAV/MP3/AIFF/M4A…;
`workspace/sfx/` is always included). Every file is analysed once (cached; rescans only look at new files):
sorted into categories by name — or by its sound when the name says nothing ("SOUND 3.mp3") — packs of several
sounds are split into single hits, and long music tracks are offered as background music instead. In Settings →
*Sound library* you can preview any sound, move it to another category or switch it off.

Levels are set by a perceptual (broadcast-style) loudness measure with a 100 Hz high-pass, so a sub-bass boom is
judged by the part a phone speaker actually reproduces and is turned up rather than down. Impacts land at about
speech level, whooshes ~3 dB under, pops ~4 dB under; the voice is never ducked by them.

Per render choose **Off / Subtle / Balanced / Punchy**, the effects volume, and *Playful sounds* (comedic/crowd effects,
only on clips the AI reads as humorous). Gemini suggests a few sound cues per clip; ClipForge adds its own from the
edit (hook, punch-ins, payoff, B-roll, jump cuts) and from the words (money → cash register, idea → ding…), then keeps
them spaced so a clip never turns into a soundboard. Final audio stays at −14 LUFS / −1 dBTP. Sound effects are skipped
when campaign rules forbid added sound, and flagged when the campaign prohibits music. Use only sounds you have rights to.

## Long-form clips

New projects default to **Auto amount** (or pick a custom count / off). After analysis, the *Long-form* tab lists the
segments (5–20 min by default; Settings → Long-form clips) with a score, 3 titles, chapters, cold open, description,
tags and 2–3 thumbnail concepts. Each concept comes with three prompts: text-to-image, **with reference frame**
(attach `thumbnail_frame_X.jpg` so the real speaker is kept — the honest, most accurate option) and Midjourney, plus a
negative prompt and a CTR checklist. Projects analysed earlier: open the tab and click *Find long-form clips*
(one text-only Gemini request, no re-upload).

Rendering a long-form clip gives a 16:9 MP4 with dead air trimmed, the cold open in front, embedded chapters, subtle
sound design, SRT captions (burn-in optional) and thumbnail drafts made from the sharpest real frame with a face.
Long-form sound design marks the cold open and chapter turns (*Subtle*) and adds roughly one accent per minute where
the words earn one -- money, an idea, a mistake (*Balanced*) or two per minute (*Punchy*).

## Folders you can use

- `workspace/broll/` — your licensed B-roll (see README inside); used only when enabled and allowed by the campaign
- `workspace/music/` — your licensed background music; ducked under speech
- `workspace/sfx/` — sound effects (in addition to the library folder chosen in Settings)
- `logs/app.log` — readable logs (API keys redacted)

## Troubleshooting

| Problem | Fix |
|---|---|
| "FFmpeg was not found" | Install FFmpeg (see above) and restart |
| Whisper model download fails | First use downloads from Hugging Face; check your connection or pick a smaller model |
| Gemini 403 / "model not found" | Check `GEMINI_API_KEY` / `GEMINI_MODEL` in `.env`, restart |
| Rate limit (429) | Wait and re-run; completed steps are cached |
| Framing falls back to center | Face model missing (re-run start.sh) or no faces detected |
| Slow renders | Settings → 720×1280 output, faster encoder preset, or smaller Whisper model |
| YouTube URL: "asking this connection to sign in to prove it isn't a bot" | YouTube's bot check has flagged your network (often after many downloads). ClipForge never signs in or uses browser cookies to get past it. Upload the file instead, or try the link again later |
| YouTube URL: "YouTube refused to send this video's stream" / connection reset | YouTube requires a proof-of-origin (PO) token for some videos. ClipForge doesn't bypass that protection — obtain the file through a permitted route (campaign source files, YouTube Studio for your own channel) and upload it |

See [ARCHITECTURE.md](ARCHITECTURE.md) for how it works and [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md) for what's done and what's next.
