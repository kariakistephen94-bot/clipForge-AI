import type { CompiledCaptions, InputWord } from '../caption/types.ts'
import { SAMPLES } from './samples.ts'

/** Left panel: transcript input (plain text or word-timestamp JSON), then the words and beats it produced. */
export default function TranscriptPanel({ sampleId, onSample, text, setText, status, error, words, compiled, selected, onSelectWord, onSeek }: {
  sampleId: string
  onSample: (id: string) => void
  text: string
  setText: (t: string) => void
  status: string
  error?: string
  words: InputWord[]
  compiled: CompiledCaptions
  selected: number | null
  onSelectWord: (i: number) => void
  onSeek: (t: number) => void
}) {
  const asJson = () =>
    setText(
      '[\n' +
        words.map((w) => `  { "word": ${JSON.stringify(w.word)}, "start": ${w.start.toFixed(2)}, "end": ${w.end.toFixed(2)} }`).join(',\n') +
        '\n]',
    )
  return (
    <div className="transcript">
      <label className="field">
        <span className="lbl">Sample</span>
        <select value={sampleId} onChange={(e) => onSample(e.target.value)}>
          {SAMPLES.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          {sampleId === 'custom' && <option value="custom">Custom transcript</option>}
        </select>
      </label>
      <label className="field">
        <span className="lbl">Transcript — plain text or JSON [{'{'}word, start, end{'}'}]</span>
        <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} rows={9} />
      </label>
      <div className="row between">
        <span className={error ? 'error small' : 'muted small'}>{error ?? status}</span>
        {!text.trim().startsWith('[') && words.length > 0 && (
          <button className="btn sm" onClick={asJson} title="Edit the timings by hand">Edit timings as JSON</button>
        )}
      </div>

      <h3>Words <span className="muted small">click to edit</span></h3>
      <div className="chips">
        {compiled.words.map((w) => {
          const lvl = compiled.importance[w.index].level
          return (
            <button key={w.index} className={`chip lvl-bg-${lvl} ${selected === w.index ? 'sel' : ''}`}
              onClick={() => { onSelectWord(w.index); onSeek(w.start + 0.2) }} title={`${lvl} · ${w.start.toFixed(2)}s`}>
              {w.text}
            </button>
          )
        })}
      </div>

      <h3>Beats <span className="muted small">{compiled.phrases.length}</span></h3>
      <ol className="beat-list">
        {compiled.phrases.map((p) => (
          <li key={p.id} onClick={() => onSeek(Math.max(0, p.showAt - 0.05))}>
            <span className="beat-text">
              {p.lines.map((l, i) => (
                <span key={i} className="beat-line">
                  {l.map((k) => {
                    const w = p.words[k]
                    return <span key={k} className={`lvl-${w.importance.level}`}>{w.text} </span>
                  })}
                </span>
              ))}
            </span>
            <span className="muted small">{p.showAt.toFixed(2)}–{p.hideAt.toFixed(2)}s · {p.strategy}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}
