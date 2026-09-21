import { useEffect, useState } from 'react'
import { frameAt } from '../caption/engine.ts'
import type { CompiledCaptions } from '../caption/types.ts'
import type { PlaybackClock } from './playback.ts'

const SPEEDS = [0.5, 1, 1.5, 2]
const FPS = [24, 25, 30, 60]

function fmt(t: number) {
  const m = Math.floor(t / 60)
  const s = t - m * 60
  return `${m}:${s.toFixed(3).padStart(6, '0')}`
}

export default function TimelineBar({ clock, compiled, fps, setFps }: {
  clock: PlaybackClock
  compiled: CompiledCaptions
  fps: number
  setFps: (f: number) => void
}) {
  const [, force] = useState(0)
  useEffect(() => {
    const id = setInterval(() => force((n) => n + 1), 66) // readout only; the canvas runs on its own loop
    const off = clock.subscribe(() => force((n) => n + 1))
    return () => {
      clearInterval(id)
      off()
    }
  }, [clock])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.code === 'Space') {
        e.preventDefault()
        clock.toggle()
      } else if (e.code === 'ArrowRight') {
        clock.pause()
        clock.seek(clock.time + (e.shiftKey ? 1 : 1 / fps))
      } else if (e.code === 'ArrowLeft') {
        clock.pause()
        clock.seek(clock.time - (e.shiftKey ? 1 : 1 / fps))
      } else if (e.key === 'Home' || e.key === '0') clock.seek(0)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [clock, fps])

  const d = clock.duration
  const step = (n: number) => {
    clock.pause()
    clock.seek((frameAt(clock.time, fps) + n) / fps)
  }
  return (
    <div className="timeline">
      <div className="transport">
        <button className="btn icon" onClick={() => clock.seek(0)} title="Restart (0)">⏮</button>
        <button className="btn icon" onClick={() => step(-1)} title="Previous frame (←)">◀︎</button>
        <button className="btn primary play" onClick={() => clock.toggle()} title="Play / pause (space)">
          {clock.playing ? '❚❚ Pause' : '▶ Play'}
        </button>
        <button className="btn icon" onClick={() => step(1)} title="Next frame (→)">▶︎</button>
        <div className="readout">
          <b>{fmt(clock.time)}</b>
          <span>frame {frameAt(clock.time, fps)} @ {fps} fps</span>
        </div>
        <div className="seg">
          {SPEEDS.map((s) => (
            <button key={s} className={clock.speed === s ? 'on' : ''} onClick={() => clock.setSpeed(s)}>{s}x</button>
          ))}
        </div>
        <select value={fps} onChange={(e) => setFps(Number(e.target.value))} title="Frame grid for stepping">
          {FPS.map((f) => <option key={f} value={f}>{f} fps</option>)}
        </select>
        <label className="toggle">
          <input type="checkbox" checked={clock.loop} onChange={(e) => { clock.loop = e.target.checked; force((n) => n + 1) }} />
          Loop
        </label>
      </div>
      <div className="scrub">
        <div className="beats">
          {compiled.phrases.map((p) => (
            <div
              key={p.id}
              className="beat"
              style={{ left: `${(p.showAt / d) * 100}%`, width: `${((p.hideAt - p.showAt) / d) * 100}%` }}
              title={`beat #${p.id} · ${p.strategy}`}
              onClick={() => clock.seek(p.spokenStart)}
            >
              {p.words.map((w) => w.text).join(' ')}
            </div>
          ))}
        </div>
        <input
          type="range"
          min={0}
          max={d}
          step={1 / fps}
          value={Math.min(clock.time, d)}
          onChange={(e) => clock.seek(Number(e.target.value))}
          onMouseDown={() => clock.pause()}
        />
        <span className="dur">{fmt(d)}</span>
      </div>
    </div>
  )
}
