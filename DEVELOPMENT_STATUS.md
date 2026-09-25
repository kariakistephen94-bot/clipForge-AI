# ClipForge AI — Development Status

_Last updated: 2026-09-19_

## DONE

### Phase 1 — working pipeline (verified end-to-end on this Mac)
- [x] Project creation (name, campaign, rules text, platforms, clip count, auto/manual duration, Gemini toggle, permission confirmation)
- [x] Upload with validation (extension, MIME, magic bytes, ffprobe) and streamed saving
- [x] Public YouTube URL ingestion via yt-dlp (no cookies/login; refuses private/restricted/live). First live test (1 h 52 min, ~1 GB) failed on a connection reset → added chunked downloads, retries with short backoff, resumable `.part` files, outer resume attempts, MB-level progress and clean error text. Further diagnosis: the resets are intermittent per connection (the 862 MB video stream completed; the English audio track then stalled). The downloader now fetches the original-language audio first, prefers H.264 video (AV1 is far slower to decode on Intel Macs), counts only no-progress attempts as failures (gives up after 6 consecutive stalls), and falls back from m4a → Opus → HLS audio when a stream keeps stalling. Live retest: the 104 MB audio completed first and the downloader resumed after a reset, but YouTube then answered "Sign in to confirm you're not a bot" (bot check on this IP after repeated test downloads). This is now detected during lookup and download and stops immediately with an "upload the file instead" message. If nothing can be downloaded and YouTube reports PO-token/SABR restrictions, the user is likewise told to upload the file; ClipForge deliberately does **not** use PO-token generators or browser cookies (that would bypass YouTube's bot protection)
- [x] ffprobe metadata (duration, size, rotation, fps, codecs, channels, audio tracks, bitrate); proxy only when needed
- [x] faster-whisper local transcription with word timestamps → transcript.json / .txt / .srt; model selection; CUDA/CPU detection; cached per source hash
- [x] Gemini provider: Files API upload + cached file refs, single multimodal analysis request, strict JSON schema, lenient parsing + repair retries, backoff — *unit-tested with a fake client; live calls await an API key*
- [x] Demo provider (offline, clearly labelled) so the full app works without Gemini
- [x] Candidate snapping to Whisper word boundaries, duplicate detection, AI Viral Potential Score with local penalties, ranking
- [x] Candidate review UI: score ring, time range, hook, "why it may work", subscores, penalties, preview, edit start/end with snapping, re-score, generate, reject, include-in-batch, sorting by score/duration/start/platform/compliance
- [x] FFmpeg cut/concat + raw-frame compositor → 1080×1920 H.264 High / AAC 48 kHz / faststart
- [x] Word-timed captions (Bold Viral / Clean / Minimal, configurable, active-word highlight, sparse emphasis) rendered with system fonts
- [x] Export folders (mp4, variants, thumbnail, transcript, captions.srt, metadata.json, posting_copy.txt, compliance.json)

### Phase 2
- [x] Face/speaker-aware framing (YuNet + tracks + mouth-motion active speaker, dead-zone camera, wide & split modes, center fallback)
- [x] Shot change detection; framing recalculated per shot; no interpolation across cuts
- [x] Subtle punch-in zooms from AI points / questions / emphasis, spaced and never across cuts
- [x] Conservative smart silence removal (confirmed by silencedetect, keeps dramatic pauses unless aggressive)
- [x] Hook overlays: 3 hook options, user selection or custom hook; variants A/B/C via lightweight overlay of the captions-only base
- [x] Campaign compliance: deterministic checks + AI judgment (1 request/batch), COMPLIANT/WARNING/FAILED with reasons + manual checks

### Phase 3
- [x] Local B-roll module (workspace/broll + broll.json tags), gated by campaign rules and user setting, ≤2.5 s, ≤2 per clip
- [x] Audio: loudness normalisation, mild denoise, limiter, optional user music with sidechain ducking
- [x] Posting copy per platform with required hashtags/mentions/CTA/links flagged; READY_TO_POST folders + posting_plan.csv (FAILED clips excluded)
- [x] Settings page (AI status, Whisper, durations, caption style & properties, reframing, split, silence, zoom, B-roll, music, output, variants) + system check
- [x] API usage panel (requests, uploads, cached reuses, tokens); response cache keyed by source hash + prompt version + model + rule hash
- [x] SSE live progress; jobs persisted; interrupted jobs flagged after restart
- [x] start.sh (checks Python/FFmpeg/Node, installs, downloads face model with checksum, builds UI, starts, opens browser); start.ps1/start.bat for Windows
- [x] Tests: 80 passing (rules parsing, timestamps, overlap/dedupe, ranking, snapping, captions, export metadata/READY_TO_POST, hashing/cache, FFmpeg commands, Pydantic validation, Gemini malformed-JSON / rate-limit / timeout recovery, silence, zoom, reframe geometry, shot cuts)

### Phase 4 — sound design, long-form, thumbnails (2026-09-19)
- [x] SFX library: scan/analyse/classify (filename rules + acoustic fallback), pack slicing, music-bed detection, cached index, per-sound overrides; verified on a real 225-file library (→ 311 usable sounds, 17 categories)
- [x] Automatic sound design for shorts (Off/Subtle/Balanced/Punchy, volume, playful opt-in): event planner, deterministic size-aware file choice, onset/peak/end alignment, per-category levelling, side-chained beds, campaign gate; Gemini `sound_cues` (viral_candidates prompt 1.1.0). Verified on real clips: output −14.1 LUFS / −1.0 dBTP
- [x] Long-form clips: right-amount target, `long_form_candidates` prompt (text-only request), snapping/dedupe/quality gate, standalone "find" job for already-analysed projects, 16:9 render with cold open, dead-air trimming, chapters (MP4 + YouTube description), optional captions, subtle sound design, READY_TO_POST/LONG_FORM + long_form_plan.csv. Verified live: 47-min source → 5 segments of 7–9 min; 7.6-min render in 112 s at 720p/ultrafast, A/V within 20 ms, −14.6 LUFS
- [x] Thumbnails: structured concepts → text-to-image / reference-frame / Midjourney prompts + negative prompt + CTR checklist; reference frames picked by face size × sharpness; text drafts on the free third. Shorts get cover text + 9:16 cover prompts (hook_generator prompt 1.1.0)
- [x] Fixed: Gemini schema converter dropped any field named `title`
- [x] UI: Shorts/Long-form tabs, long-form cards, sound options, Settings → Sound design / Long-form clips / Sound library browser (preview, re-categorise, disable)

### Phase 4.1 — sound design tuning (2026-09-19, after listening feedback "not noticeable")
- [x] Loudness is now measured perceptually (K-weighting, 100 Hz high-pass) instead of flat RMS: sub-bass booms were
      being read as loud and turned down ~10 dB, so they vanished on laptop/phone speakers
- [x] Category targets raised ~6 dB and pinned to measured speech (≈ −14 in this measure); event intensities raised;
      peak ceiling −5 → −1.5 dBFS; files needing more than +9 dB of boost are skipped
- [x] Long-form sound design was cold open + chapters only (6 effects in 10 min); Balanced/Punchy now add ~1–2
      keyword accents per minute, and Punchy is offered for long-form
- [x] Verified on a real clip: effects land on target, voice-only regions unchanged (0.00 dB → no pumping),
      output −14.5 LUFS / −0.8 dBTP

### Publishing (new)
- [x] YouTube connector: OAuth 2.0 + PKCE (loopback redirect), token refresh, channel lookup, resumable chunked upload with 308/5xx resume, quota/permission errors mapped to plain-English messages
- [x] TikTok connector: OAuth 2.0 + PKCE, creator info, upload-to-drafts (`video.upload`) and direct post (`video.publish`), chunked upload, status polling, unaudited-app handling
- [x] Tokens stored in `workspace/credentials/*.json` (0600) — never in SQLite, never returned to the browser, never logged; Disconnect deletes them
- [x] Confirm-before-publish dialog (version, title/description/tags/caption, visibility), extra tick required for public posts, FAILED-compliance clips refused
- [x] Publish job history with links; background upload with live progress
- [x] PUBLISHING.md setup guide; manual code-paste fallback for platforms that refuse loopback redirects
- [ ] **Not built on purpose:** unattended/scheduled auto-posting. Every publish is a confirmed click.
- [ ] Untested against the live APIs — needs your developer apps and account connection

### Phase 5 — colour grading (2026-09-23)
- [x] `services/color_grade.py`: 8 presets (Off, Clean, Punchy, Warm, Cool, Cinematic teal/orange, Matte film, Black & white) and 10 clamped fine-tuning fields; per-frame path is LUT + saturation matrix + luma-indexed split-tone LUTs + cached 8-bit vignette (≈4–8 ms/frame at 1080×1920)
- [x] Shorts: grade applied in the compositor after framing/punch-ins/B-roll and before captions and hook overlays, so text stays clean
- [x] Long-form: identical look baked into a 33³ `look.cube` from the same code path, applied by FFmpeg `lut3d` in the cut pass (+ `vignette` filter); thumbnail reference frames graded to match
- [x] `GET /projects/{id}/grade-preview` returns one graded source frame; UI shows source vs graded side by side with live sliders (debounced), per-render preset in Render options and the long-form panel, defaults + shared fine-tuning in Settings → Colour grade
- [x] Grade recorded in `metadata.json` (`color_grade.preset` / `overrides`) and in render notes
- [x] Tests: presets, clamping, identity/mono/exposure/white balance/intensity, vignette edges only, split-tone direction, .cube lattice order and range, FFmpeg filter quoting, cut-command placement, options/preferences validation

## CURRENT
- Quality gates: pytest 206/206, ruff clean, mypy clean, `tsc -b && vite build` clean (oxlint: 6 style warnings, all the fetch-in-effect pattern)
- API-level verification of the built dashboard (SPA serving, range requests for video seeking, export URLs, upload/URL validation)
- Interactive click-through of the dashboard in a real browser is still to do (run `./start.sh`)

## NEXT
- Optional: generate thumbnails directly with an image model from the prompts (currently prompts + real-frame drafts)
- Long-form: per-chapter punch-ins / B-roll; caption burn-in is CPU-bound (Python compositor) — consider libass builds
- Validate speaker tracking on real talking-head footage (1 and 2 speakers) and tune thresholds
- Live Gemini run: confirm schema acceptance with the chosen model, token usage, prompt quality; tune candidate prompts
- Exercise YouTube URL ingestion on a public video you have rights to
- Transcript viewer/search in the UI; per-clip caption preview before render
- Two-pass loudnorm for tighter −14 LUFS accuracy (single pass currently lands around −15 LUFS)
- Optional free stock B-roll provider (official APIs only) — **not implemented** (disabled in Settings)
- Batch operations across projects; analytics after posting (manual import) — **not implemented**
- Windows launcher testing

## BLOCKED
- **Live Gemini analysis** needs your `GEMINI_API_KEY` in `.env` (everything else runs now in Demo mode).
- **Real-footage framing validation** needs a talking-head video you have permission to use (the built-in test uses a synthetic pattern video with TTS speech, which has no faces).
