import { useEffect, useState } from 'react'
import type { CaptionFrameState, CompiledCaptions } from '../caption/types.ts'

/** Text readout of the current frame (the canvas overlay shows the same data spatially). */
export default function CaptionDebugPanel({ compiled, frameRef }: {
  compiled: CompiledCaptions
  frameRef: React.MutableRefObject<CaptionFrameState | null>
}) {
  const [frame, setFrame] = useState<CaptionFrameState | null>(null)
  useEffect(() => {
    const id = setInterval(() => setFrame(frameRef.current), 100)
    return () => clearInterval(id)
  }, [frameRef])
  if (!frame) return null
  const active = frame.activeWord != null ? compiled.words[frame.activeWord] : null
  const activeFrame = frame.words.find((w) => w.index === frame.activeWord)
  return (
    <div className="debug-panel">
      <div className="debug-row"><b>t</b> {frame.time.toFixed(3)}s</div>
      {frame.phrases.map((p) => {
        const ph = compiled.phrases.find((x) => x.id === p.id)!
        return (
          <div key={p.id} className="debug-row">
            <b>beat #{p.id}</b> {p.strategy} · show {ph.showAt.toFixed(2)} · exit {ph.exitStart.toFixed(2)} · hide {ph.hideAt.toFixed(2)}
            {p.exiting ? ' · exiting' : ''}
          </div>
        )
      })}
      {active ? (
        <div className="debug-row">
          <b>active</b> “{active.text}” {compiled.importance[active.index].level.toUpperCase()} · {active.start.toFixed(2)}–{active.end.toFixed(2)}
          {activeFrame && ` · ${activeFrame.entrance} (${Math.round(activeFrame.entranceProgress * 100)}%) · speak ${activeFrame.speak} · scale ${activeFrame.scale.toFixed(2)}`}
        </div>
      ) : (
        <div className="debug-row muted"><b>active</b> — (between words)</div>
      )}
      <table className="debug-table">
        <thead>
          <tr><th>word</th><th>level</th><th>start</th><th>end</th><th>entrance</th><th>exit</th><th>scale</th><th>opacity</th></tr>
        </thead>
        <tbody>
          {frame.words.map((w) => {
            const cw = compiled.words[w.index]
            return (
              <tr key={w.index} className={w.active ? 'active' : ''}>
                <td>{w.text}</td>
                <td className={`lvl-${w.importance}`}>{w.importance}</td>
                <td>{cw.start.toFixed(2)}</td>
                <td>{cw.end.toFixed(2)}</td>
                <td>{w.entrance}</td>
                <td>{w.exitProgress > 0 ? `${w.exit} ${Math.round(w.exitProgress * 100)}%` : w.exit}</td>
                <td>{w.scale.toFixed(2)}</td>
                <td>{w.opacity.toFixed(2)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
