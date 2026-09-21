# Local B-roll

Put **licensed** B-roll clips here (your own footage, or footage whose license allows this use).
ClipForge AI never downloads or scrapes third-party footage.

B-roll is only used when **all** of these are true:
1. B-roll is set to `Local` in Settings or Render options
2. the campaign rules allow B-roll (or you enabled "allow when rules don't mention it")
3. a file here matches an AI-suggested concept

Optional metadata file `broll.json`:

```json
[
  {"file": "nyc_skyline_night.mp4", "tags": ["new york", "skyline", "night", "city"], "license": "own footage"}
]
```

Without metadata, words in the file name are used as tags (`nyc_skyline_night.mp4` → nyc, skyline, night).
Inserts are capped at 2.5 seconds, never cover the hook or the ending, and there are at most 2 per clip.
