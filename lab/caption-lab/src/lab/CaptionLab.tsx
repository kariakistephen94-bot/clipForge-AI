import { useEffect, useMemo, useRef, useState } from 'react'
import { compileCaptions } from '../caption/engine.ts'
import { presetConfig, PRESETS } from '../caption/presets.ts'
import { createCanvasMeasurer } from '../caption/render/canvas.ts'
import { parseTranscript } from '../caption/timeline.ts'
import type { CaptionConfig, CaptionFrameState, WordOverride, WordOverrides } from '../caption/types.ts'
import CaptionControls from './CaptionControls.tsx'
import CaptionDebugPanel from './CaptionDebugPanel.tsx'
import CaptionPreview, { type Backdrop } from './CaptionPreview.tsx'
import { PlaybackClock } from './playback.ts'
import { DEFAULT_SAMPLE, SAMPLES } from './samples.ts'
import TimelineBar from './TimelineBar.tsx'
import TranscriptPanel from './TranscriptPanel.tsx'
import WordInspector from './WordInspector.tsx'

function setPath<T>(obj: T, path: string, value: unknown): T {
  const out = structuredClone(obj) as Record<string, unknown>
  const keys = path.split('.')
  let cur = out
  for (const k of keys.slice(0, -1)) cur = cur[k] as Record<string, unknown>
  cur[keys[keys.length - 1]] = value
  return out as T
}

export default function CaptionLab() {
  const clock = useRef(new PlaybackClock()).current
  const frameRef = useRef<CaptionFrameState | null>(null)
  const [sampleId, setSampleId] = useState(DEFAULT_SAMPLE.id)
  const [text, setText] = useState(DEFAULT_SAMPLE.text ?? '')
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const [backdrop, setBackdrop] = useState<Backdrop>('dark')
  const [presetId, setPresetId] = useState(PRESETS[0].id)
  const [config, setConfig] = useState<CaptionConfig>(() => presetConfig(PRESETS[0].id))
  const [overrides, setOverrides] = useState<WordOverrides>({})
  const [selected, setSelected] = useState<number | null>(null)
  const [debug, setDebug] = useState(false)
  const [fps, setFps] = useState(30)
  const [fontVersion, setFontVersion] = useState(0)

  // Load a sample: plain text, or Whisper-style timestamps (+ matching video).
  const loadSample = async (id: string) => {
    const s = SAMPLES.find((x) => x.id === id)
    if (!s) return
    setSampleId(id)
    setOverrides({})
    setSelected(null)
    clock.pause()
    clock.seek(0)
    if (s.wordsUrl) {
      const words = await fetch(s.wordsUrl).then((r) => r.json())
      setText(JSON.stringify(words))
    } else setText(s.text ?? '')
    setVideoUrl(s.videoUrl ?? null)
    setBackdrop(s.videoUrl ? 'video' : 'dark')
  }

  // Measure with the real font: wait for it to load, then re-measure.
  const { fontFamily, fontWeight, letterSpacing } = config.typography
  useEffect(() => {
    let cancelled = false
    document.fonts.load(`${fontWeight} 100px ${fontFamily}`, 'AaWwDIFFERENT').then(() => {
      if (!cancelled) setFontVersion((v) => v + 1)
    })
    return () => { cancelled = true }
  }, [fontFamily, fontWeight])
  const measurer = useMemo(
    () => createCanvasMeasurer({ ...config.typography, fontFamily, fontWeight, letterSpacing }),
    [fontFamily, fontWeight, letterSpacing, fontVersion], // eslint-disable-line react-hooks/exhaustive-deps
  )

  const parsed = useMemo(() => parseTranscript(text), [text])
  const compiled = useMemo(
    () => compileCaptions(parsed.words, config, { measure: measurer, overrides }),
    [parsed, config, measurer, overrides],
  )
  clock.duration = Math.max(compiled.duration, 1)

  const update = (path: string, value: unknown) => setConfig((c) => setPath(c, path, value))
  const choosePreset = (id: string) => {
    setPresetId(id)
    setConfig(presetConfig(id))
  }
  const setOverride = (i: number, o: WordOverride) =>
    setOverrides((prev) => {
      const next = { ...prev }
      if (Object.keys(o).length) next[i] = o
      else delete next[i]
      return next
    })
  const onText = (t: string) => {
    setText(t)
    if (sampleId !== 'custom') setSampleId('custom')
    setOverrides({})
  }

  const status = parsed.words.length
    ? `${compiled.words.length} words · ${compiled.phrases.length} beats · ${parsed.timed ? 'timestamps from JSON' : 'timing estimated from text'}`
    : 'Paste a transcript'
  const overrideCount = Object.keys(overrides).length

  return (
    <div className="lab">
      <header className="topbar">
        <div className="brand">
          <span className="logo">Aa</span>
          <b>Caption Lab</b>
          <span className="badge">isolated test environment · not connected to ClipForge</span>
        </div>
        <div className="row gap">
          <div className="seg">
            {(['dark', 'bright', 'busy', 'video'] as Backdrop[]).map((b) => (
              <button key={b} className={backdrop === b ? 'on' : ''} disabled={b === 'video' && !videoUrl}
                onClick={() => setBackdrop(b)} title={b === 'video' && !videoUrl ? 'Load the podcast sample for video' : `${b} background`}>
                {b}
              </button>
            ))}
          </div>
          <label className="toggle">
            <input type="checkbox" checked={debug} onChange={(e) => setDebug(e.target.checked)} /> Debug
          </label>
        </div>
      </header>

      <aside className="panel left">
        <TranscriptPanel
          sampleId={sampleId}
          onSample={(id) => void loadSample(id)}
          text={text}
          setText={onText}
          status={status}
          error={parsed.error}
          words={parsed.words}
          compiled={compiled}
          selected={selected}
          onSelectWord={setSelected}
          onSeek={(t) => { clock.pause(); clock.seek(t) }}
        />
      </aside>

      <main className="center">
        <CaptionPreview
          compiled={compiled}
          capRatio={measurer.capRatio}
          clock={clock}
          backdrop={backdrop}
          videoUrl={videoUrl}
          debug={debug}
          selected={selected}
          onSelect={setSelected}
          frameRef={frameRef}
        />
        {debug && <CaptionDebugPanel compiled={compiled} frameRef={frameRef} />}
      </main>

      <aside className="panel right">
        {selected != null && compiled.words[selected] && (
          <WordInspector
            compiled={compiled}
            index={selected}
            override={overrides[selected] ?? {}}
            onChange={(o) => setOverride(selected, o)}
            onClose={() => setSelected(null)}
            onSeek={(t) => { clock.seek(t); clock.play() }}
          />
        )}
        <CaptionControls
          config={config}
          presetId={presetId}
          onPreset={choosePreset}
          update={update}
          onReset={() => setConfig(presetConfig(presetId))}
          extra={overrideCount > 0 && (
            <div className="row between note">
              <span className="small">{overrideCount} word override(s)</span>
              <button className="btn sm" onClick={() => setOverrides({})}>Clear</button>
            </div>
          )}
        />
      </aside>

      <footer className="bottom">
        <TimelineBar clock={clock} compiled={compiled} fps={fps} setFps={setFps} />
      </footer>
    </div>
  )
}
