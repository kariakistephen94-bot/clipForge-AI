# ClipForge AI — Architecture

ClipForge AI is a local web app that turns long-form videos (which you have permission to clip) into ranked,
campaign-compliant, ready-to-post 9:16 shorts for TikTok, Instagram Reels and YouTube Shorts.

It runs entirely on your machine. The only network service is the **optional** Gemini API (bring your own key);
everything else — transcription, face tracking, editing, captions, encoding — is free, local and open source.
The Claude/Anthropic API is **not** used at runtime.

```
Browser (React + Vite, port 5173 dev / served by backend on 8765)
   │  REST + Server-Sent Events (/api/projects/{id}/events)
   ▼
FastAPI backend (127.0.0.1:8765) ── SQLite (workspace/clipforge.db)
   │
   ├─ pipeline/analyze.py      source → ffprobe → proxy → audio → faster-whisper → rules → Gemini → snap/dedupe/rank
   ├─ pipeline/render_batch.py hooks & copy → render clips → compliance → exports → READY_TO_POST
   ├─ pipeline/longform.py     find & package long-form segments; render/export them → READY_TO_POST/LONG_FORM
   │      (one background worker thread; progress in memory for SSE + persisted to the jobs table)
   │
   ├─ publish/     OAuth (PKCE) + upload connectors: youtube.py, tiktok.py, store.py (0600 token files), service.py
   ├─ ai/          AIProvider interface, GeminiProvider (google-genai SDK), DemoProvider (offline mock), response cache
   ├─ candidates/  word-timestamp boundary snapping, duplicate detection, scoring & ranking
   └─ services/    ffmpeg command builders, probe, transcribe, reframe (OpenCV), captions (Pillow),
                   silence, zoom, b-roll, compliance, posting copy, export, ingest, preferences,
                   sfx_library + sound_design, longform_render, thumbnail_prompts
```

## Tech

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite (WAL) |
| Transcription | faster-whisper (CTranslate2) — CUDA float16 if present, otherwise CPU int8 (Apple Silicon uses Accelerate) |
| AI | Official `google-genai` SDK, model from `GEMINI_MODEL` (default `gemini-3.8-flash`) |
| Vision | OpenCV 5 — YuNet face detector (`backend/models/`, Apache-2.0, downloaded by start.sh), HSV-histogram shot detection |
| Video | FFmpeg/ffprobe via argument-list subprocesses (never `shell=True`) |
| Captions / overlays | Pillow using system fonts (no fonts bundled) |
| Frontend | React 19 + TypeScript + Vite, no UI framework |

## Data layout

```
workspace/
  clipforge.db
  cache/<source-sha256>/        proxy_540p.mp4, audio_16k.wav, transcript_<model>.json   (shared across projects)
  models/whisper/               faster-whisper model downloads
  broll/                        your licensed B-roll (+ optional broll.json)
  music/                        your licensed music
  projects/<project-id>/
    source/ audio/ transcript/ analysis/ candidates/ renders/ exports/ cache/
    exports/clip_001/           clip_001.mp4, variants, thumbnail.jpg, transcript.txt, captions.srt,
                                metadata.json, posting_copy.txt, compliance.json
    READY_TO_POST/01_<slug>/…   hard-linked copies of non-FAILED exports, sorted by score
    READY_TO_POST/posting_plan.csv
logs/app.log                    rotating, API keys redacted
```

Database tables: `projects`, `source_videos`, `transcripts`, `gemini_files`, `ai_cache`, `analyses`, `candidates`,
`long_form_clips`, `publish_jobs`, `jobs`, `render_jobs`, `exports`, `preferences`. No API key is ever stored in SQLite.

## Analysis pipeline (`pipeline/analyze.py`)

1. **Source** — upload (extension + MIME + magic-byte check + ffprobe validation) or public YouTube URL via yt-dlp
   (no cookies/login; private, members-only, age-restricted and live videos are refused). SHA-256 of the file is the cache identity.
2. **Metadata** — ffprobe: duration, dimensions (rotation aware), fps, codecs, channels, audio track count, bitrate.
3. **Proxy** — a 540p H.264 proxy is created only when the original isn't browser/Gemini friendly or is large (>400 MB, >1080p, >1.9 GB for Gemini). It doubles as the Gemini upload, which cuts upload size.
4. **Audio** — 16 kHz mono WAV of the default audio track (files without audio continue: no captions/snapping).
5. **Transcription** — faster-whisper with `word_timestamps=True` and VAD. Writes `transcript.json/.txt/.srt`.
   Cached per (source hash, whisper model).
6. **Campaign rules** — `campaign_parser` prompt → `CampaignRules` (unknown = `null`, listed in `unknown_requirements`).
   Falls back to a conservative offline regex parser if Gemini is off or fails. Original text and parsed JSON are both stored.
7. **Gemini upload** — Files API upload once per source hash; the file reference and expiry (~48 h) are cached and verified (`ACTIVE`) before reuse.
8. **Understanding + candidates** — ONE multimodal request (video part + system prompt `video_analysis` + user prompt
   `viral_candidates` with transcript, rules, platforms, clip count, duration limits). Structured output via
   `response_json_schema` from the Pydantic models. Long videos (>15 min) use `MEDIA_RESOLUTION_LOW`; >45 min also sample at 0.5 fps.
9. **Snap / dedupe / rank** — see below. Candidates are saved with local compliance status; the top N non-FAILED are pre-selected.

## Candidate logic (`candidates/`)

* **Snapping** (`snapping.py`): Gemini supplies the idea and an approximate region plus verbatim opening/closing words.
  The snapper fuzzy-locates those words in the Whisper word list near the timestamps, moves the start to a sentence
  start (punctuation or ≥0.65 s pause) and the end to the end of the sentence containing the payoff, then enforces
  min/max duration by adding/removing whole sentences. 150 ms / 220 ms padding never overlaps a neighbouring word.
  Notes such as "starts mid-sentence" are stored and penalised.
* **Scoring** (`scoring.py`): AI Viral Potential Score = rubric subscores (hook 20, clarity 15, curiosity 15, emotion 10,
  specificity 10, shareability 10, retention 10, visual 5, campaign 5) − AI penalties − local penalties
  (greetings, housekeeping, sponsor reads, filler openings, mid-thought boundaries, duration violations). It is labelled as an estimate everywhere.
* **Dedupe** (`dedupe.py`): higher score wins when time overlap ≥80 %, or ≥50 % with similar hook/idea, or spoken-content
  Jaccard ≥0.6, or topic+summary similarity ≥0.65. Partially overlapping clips with different narratives are kept.

## Render engine (`services/render.py`)

1. **Cut** (FFmpeg): keep-segments (after silence planning) are trimmed and concatenated in one filter graph; video is
   normalised to constant 30/60 fps; audio goes through high-pass → mild `afftdn` → `loudnorm` (−14 LUFS) → `alimiter`,
   optionally mixed with user music ducked by `sidechaincompress`.
2. **Framing analysis** (OpenCV, ~6 fps, 480 px): shot cuts from HSV histogram distance; YuNet faces; greedy tracks;
   mouth-region motion energy as the active-speaker signal. Per shot: `track` (one subject, or two that fit),
   active-speaker `track` with hard cuts between speakers (min 2.5 s hold), `split` (only when rules explicitly allow it
   and the user enabled it), `wide` (fit + blurred fill), or `center` fallback. Dead-zone camera (11 % of crop width,
   0.5 s persistence) with eased 0.35–0.9 s moves; never interpolates across a cut.
3. **Composite & encode**: frames are decoded by FFmpeg, cropped/zoomed/composited in NumPy/OpenCV, B-roll is swapped
   in, the colour grade is applied, captions are drawn on top, and raw frames are piped into FFmpeg (libx264 High,
   yuv420p, BT.709, AAC 48 kHz, faststart) → 1080×1920.
4. **Variants**: the captions-only render is Variant C. Variants A (recommended hook) and B (alternative hook) are made by
   overlaying a transparent hook PNG on C with FFmpeg — no second compositing pass.
5. **Thumbnail** from the primary variant.

Captions (`services/captions.py`) group words into 3–7-word, ≤2-line chunks broken at sentence ends, pauses and a 3.2 s cap;
the active word is highlighted; at most one AI emphasis phrase per caption; punctuation is cleaned (keeps ? and !).
Styles: Bold Viral, Clean, Minimal, each configurable (font, size, outline, shadow, position, words per caption, highlight mode).
Why not ASS/libass? Many FFmpeg builds (including current Homebrew) ship without libass, so ClipForge renders captions itself;
SRT is still exported.

Punch-ins (`services/zoom.py`): AI zoom points > questions > emphasis words; at most one per ~8 s, ≥6 s apart, 108 %
(112 % for punchlines/revelations), held to the sentence end, never across a shot cut.

Silence (`services/silence.py`): gaps between words ≥0.85 s (0.45 s aggressive) are shortened to a natural 0.38 s pause,
only where FFmpeg `silencedetect` confirms silence, and pauses after questions/before emphasised words are kept unless aggressive.

Colour grade (`services/color_grade.py`): a `Grade` (preset + clamped overrides: exposure, contrast, saturation,
temperature, tint, shadows, highlights, fade, vignette, intensity; presets may add luma-driven split toning) is compiled
once per render into a 256-entry per-channel LUT (`cv2.LUT`), a luma-preserving 3×3 saturation matrix (`cv2.transform`),
split-tone add/subtract LUTs indexed by luma, and a cached 8-bit vignette mask — roughly 4–8 ms per 1080×1920 frame.
It runs on the composed frame *before* captions so text and the vignette never interact. Long-form is graded by FFmpeg:
the same `apply()` is run over an identity lattice to write a 33³ `.cube` LUT (`lut3d=…:interp=tetrahedral`) and the
vignette maps to FFmpeg's `vignette=angle=acos((1-v)^¼)`; long-form thumbnail frames are graded with the NumPy path so
they match. `GET /api/projects/{id}/grade-preview?t=&grade=&<field>=` returns one graded source frame for the UI.

## Sound design (`services/sfx_library.py`, `services/sound_design.py`)

* **Library**: `workspace/sfx/` + the folder set in Settings. Each file is decoded once (16 kHz mono) and measured:
  onset (`lead`), loudest point (`peak`), end (`tail`), **perceived** loudness of the loudest 300 ms, spectral
  centroid, channels. Loudness uses a BS.1770-style K weighting applied in the frequency domain (no scipy), with the
  high-pass moved to 100 Hz: clips are watched on phones, so sub-bass must not be counted as loudness -- otherwise
  cinematic booms get attenuated and disappear.
  Category from filename keywords (ordered rules), else from the envelope (short+bright → click, swells to the end →
  riser, dark+front-loaded → impact, …). Files with ≥3 separated sounds become *packs* split into slices; long
  continuous files become music beds. Index cached in `workspace/cache/sfx_index.json` (path+size+mtime); user
  category/enabled overrides in the `preferences` table (`sfx_overrides`).
* **Planning** (pure, output timeline): candidates from the hook entrance (A/B variants only), Gemini `sound_cues`,
  riser+impact into the strongest punchline/revelation punch-in, other punch-ins, B-roll entrances, keywords on
  emphasis words (money → cash …), emphasis pops, jump cuts (punchy). Accepted greedily by priority under per-style
  spacing, density, pop and keyword caps; 1.8 s around the hook and 1.6 s after an impact stay clear.
* **Placement**: deterministic rotation per clip (named files preferred over auto-detected, size-matched: a jump cut
  gets a short swish, files needing more than +9 dB of boost are skipped); aligned by onset / peak / end; levelled to a
  per-category target relative to speech (which sits at ≈ −14 in this measure: impacts level with it, whooshes −3,
  pops −4, beds further down), capped at −1.5 dBFS peak so one effect cannot drive the limiter.
* **Mix**: one FFmpeg graph — effects delayed into place, beds/risers side-chained under the voice, `alimiter`; the
  voice's channel layout is kept (mono sources are not attenuated by FFmpeg's −3 dB upmix). Variant C uses the mix
  without the hook whoosh; A/B get their own mix swapped in during the overlay step.

## Long-form (`candidates/longform.py`, `pipeline/longform.py`, `services/longform_render.py`)

* **How many**: `long_form_target` — none for sources under ~8 min; otherwise ideal length
  `clamp(source/7.5, min, min(max, 12 min))`, count `round(0.6 × source / ideal)` capped by settings. The AI returns
  between half and target+2; segments scoring ≥ 50 are pre-selected.
* **Finding**: one text-only request (`long_form_candidates` prompt: transcript + the earlier video understanding),
  cached like every AI call; demo heuristics otherwise. Segments are snapped to sentences, de-duplicated (≥35 %
  overlap), chapters cleaned, cold open snapped. Runs as an analysis step and as its own job for older projects.
* **Thumbnails**: the AI returns structured concepts (emotion, subject, scene, props, 2–4 word text, palette,
  composition, frame timestamp); `thumbnail_prompts.py` assembles text-to-image / reference-image / Midjourney
  prompts + negative prompt at read time. Rendering picks the best frame near each concept (YuNet face size ×
  face sharpness), punches in and puts the face on a third, grades it and draws outlined text on the free side.
* **Sound**: `long_form_events` marks the cold open and chapter turns, and at Balanced/Punchy adds ~1–2 keyword
  accents per minute (money, idea, mistake …), spaced and capped per category.
* **Render**: teaser and body are cut separately (`select`/`aselect` expressions, so hundreds of silence cuts need no
  buffering) with identical encoder settings and the colour grade applied in the same pass (`lut3d` from `look.cube`
  + `vignette`), concatenated without re-encoding, optional caption burn-in, subtle sound design, then muxed with
  ffmetadata chapters. Duration/platform campaign rules (written for shorts) are not
  applied to long-form; content rules are.

## Campaign compliance (`services/compliance.py`)

Deterministic checks: duration, platforms, captions allowed/required, split screen, B-roll, music, required hashtags /
mentions / CTA / links in the posting copy, plus a conservative prohibited-content keyword signal. The judgment-based
part (prohibited content, misleading hooks) comes from the `compliance_checker` prompt (one text-only request per batch).
Result: COMPLIANT / WARNING / FAILED with reasons, plus "check manually" items. FAILED clips stay in `exports/` but are
excluded from `READY_TO_POST/`.

## AI provider interface (`ai/provider.py`)

```python
class AIProvider:
    parse_campaign_rules(text) -> CampaignRules
    analyze_video(ctx) -> VideoAnalysis           # shares the request with generate_candidates
    generate_candidates(ctx) -> ViralCandidateList
    generate_hooks(clips, rules, ...) -> HookVariantList
    check_compliance(clips, rules, text) -> AIComplianceList
    refine_candidate(clip, ...) -> CandidateRefinement
    prepare_video(path, sha, mime) -> VideoRef | None
```

Implementations: `GeminiProvider` and `DemoProvider` (offline; derives candidates from the real transcript with text
heuristics and labels everything `[DEMO]`). A new provider only needs this class; nothing else changes.

## Cost controls & caching

* `ai_cache` key = sha256(operation, provider, model, **prompt version**, source hash, campaign-rule hash, parameters, transcript hash).
  Identical operations never call Gemini twice; cache hits are counted per project.
* Gemini Files API references are cached per source hash and validated before reuse.
* Transcripts, proxies and audio are cached per source hash across projects.
* Hooks + posting copy: one text-only request per render batch; compliance: one per batch; refinement: on demand only.
* Per-project counters: Gemini requests, video uploads, cached reuses, input/output tokens (from `usage_metadata`).

## Prompts (`backend/prompts/*.txt`)

`campaign_parser`, `video_analysis` (system), `viral_candidates`, `candidate_refinement`, `hook_generator`,
`compliance_checker`, `long_form_candidates`, `json_repair`. First line `VERSION: x.y.z`; placeholders are `{{name}}`. The version is part of every cache key.

## Structured output & recovery

Pydantic schemas (`schemas/ai.py`) → JSON Schema with `$ref`s inlined for Gemini. Responses are parsed leniently
(code fences, trailing commas, truncated output), validated, and on failure a text-only `json_repair` request is sent
(max 2). Individually malformed candidates are dropped instead of failing the whole batch. If Gemini rejects the schema,
the request is retried with prompt-only JSON instructions. 429/5xx/timeouts retry with backoff (4 s, 15 s, 40 s);
401/403/404 fail fast with actionable messages.

## Security

* `GEMINI_API_KEY` is read from `.env`/environment by the backend only; never returned by the API, never stored, redacted from logs.
* Backend binds to 127.0.0.1. Uploaded filenames are sanitised; files are served only from inside the project directory.
* All subprocesses use argument arrays. No user input is ever interpreted by a shell.
* YouTube ingestion never uses cookies or credentials and refuses non-public videos.

## Error handling

Each pipeline step reports status/progress/detail over SSE. Failures (bad media, no audio, extra audio tracks,
Whisper/model download errors, Gemini timeouts/rate limits/invalid JSON, FFmpeg errors, missing fonts/FFmpeg, network loss)
become visible step/job errors; one failed clip does not stop a batch; jobs interrupted by a restart are marked as such.
