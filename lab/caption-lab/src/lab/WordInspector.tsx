import type { CompiledCaptions, Importance, WordOverride } from '../caption/types.ts'
import { ENTRANCES, EXITS, SPEAKS } from './CaptionControls.tsx'
import { Select, Slider } from './fields.tsx'

const LEVELS: Importance[] = ['normal', 'medium', 'strong', 'hero']

/** Manual overrides for one word. Values left on "auto" stay under the engine's control. */
export default function WordInspector({ compiled, index, override, onChange, onClose, onSeek }: {
  compiled: CompiledCaptions
  index: number
  override: WordOverride
  onChange: (o: WordOverride) => void
  onClose: () => void
  onSeek: (t: number) => void
}) {
  const w = compiled.words[index]
  const imp = compiled.importance[index]
  const phrase = compiled.phrases.find((p) => p.words.some((x) => x.index === index))
  const pw = phrase?.words.find((x) => x.index === index)
  if (!w || !pw || !phrase) return null
  const set = (patch: Partial<WordOverride>) => {
    const next: WordOverride = { ...override, ...patch }
    for (const k of Object.keys(next) as (keyof WordOverride)[]) if (next[k] === undefined) delete next[k]
    onChange(next)
  }
  const autoOr = <T extends string>(v: T | undefined) => (v ?? 'auto') as T | 'auto'
  const fromAuto = <T extends string>(v: T | 'auto') => (v === 'auto' ? undefined : (v as T))
  const overridden = Object.keys(override).length > 0

  return (
    <div className="inspector">
      <div className="inspector-head">
        <div>
          <div className="word-title">“{pw.text}”</div>
          <div className="muted small">
            word #{index} · {w.start.toFixed(2)}–{w.end.toFixed(2)}s · beat #{phrase.id} · {phrase.strategy}
          </div>
        </div>
        <button className="btn icon" onClick={onClose} title="Close">✕</button>
      </div>
      <div className="auto-box">
        <span className={`lvl lvl-${imp.level}`}>{imp.level.toUpperCase()}</span>
        <span className="muted small">score {imp.score.toFixed(2)} · {pw.entrance} · {pw.exit} · {pw.speak}</span>
        <ul className="reasons">{imp.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
      </div>
      <div className="row gap">
        <button className="btn sm" onClick={() => onSeek(Math.max(0, phrase.showAt - 0.05))}>▶ Play its beat</button>
        {overridden && <button className="btn sm" onClick={() => onChange({})}>Reset this word</button>}
      </div>
      <Select label="Importance" value={autoOr(override.importance)} options={['auto', ...LEVELS]} onChange={(v) => set({ importance: fromAuto(v) })} />
      <label className="field color">
        <span className="lbl">Colour</span>
        <span className="color-row">
          <input type="color" value={override.color ?? pw.baseColor} onChange={(e) => set({ color: e.target.value })} />
          {override.color ? <button className="btn sm" onClick={() => set({ color: undefined })}>auto</button> : <code>auto</code>}
        </span>
      </label>
      <Slider label="Size ×" value={override.sizeScale ?? 1} min={0.5} max={2.5} step={0.05} onChange={(v) => set({ sizeScale: v === 1 ? undefined : v })} />
      <Select label="Entrance (incl. direction)" value={autoOr(override.entrance)} options={['auto', ...ENTRANCES]} onChange={(v) => set({ entrance: fromAuto(v) })} />
      <Select label="Exit" value={autoOr(override.exit)} options={['auto', ...EXITS]} onChange={(v) => set({ exit: fromAuto(v) })} />
      <Select label="While spoken" value={autoOr(override.speak)} options={['auto', ...SPEAKS]} onChange={(v) => set({ speak: fromAuto(v) })} />
      <Slider label="Nudge X" value={override.dx ?? 0} min={-300} max={300} step={5} unit="px" onChange={(v) => set({ dx: v || undefined })} />
      <Slider label="Nudge Y" value={override.dy ?? 0} min={-300} max={300} step={5} unit="px" onChange={(v) => set({ dy: v || undefined })} />
    </div>
  )
}
