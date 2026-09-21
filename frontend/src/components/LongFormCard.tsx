import { useState } from 'react'
import { api, fmtTime, type LongFormClip, type ThumbnailPrompt } from '../api'
import { Bar, ComplianceBadge, CopyButton, ScoreRing } from './common'

const SUB_LABEL: Record<string, string> = {
  opening: 'Opening', arc: 'Complete arc', value: 'Value density', retention: 'Retention', emotion: 'Emotion', packaging: 'Title & thumbnail',
}

interface Props {
  clip: LongFormClip
  disabled: boolean
  onChange: (c: LongFormClip) => void
  onPreview: (start: number, end: number, title: string) => void
  onRender: () => void
  onError: (msg: string) => void
}

export function ThumbnailPrompts({ prompts, drafts = [], frames = [] }: { prompts: ThumbnailPrompt[]; drafts?: string[]; frames?: string[] }) {
  const [tab, setTab] = useState<'prompt' | 'reference_prompt' | 'midjourney'>('reference_prompt')
  if (!prompts.length) return <p className="tiny muted">No thumbnail concepts.</p>
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="segmented" style={{ alignSelf: 'flex-start' }}>
        <button className={tab === 'reference_prompt' ? 'on' : ''} onClick={() => setTab('reference_prompt')} title="Attach the reference frame; keeps the real speaker">With reference frame</button>
        <button className={tab === 'prompt' ? 'on' : ''} onClick={() => setTab('prompt')}>Text-to-image</button>
        <button className={tab === 'midjourney' ? 'on' : ''} onClick={() => setTab('midjourney')}>Midjourney</button>
      </div>
      {prompts.map((p, i) => {
        const tag = String.fromCharCode(65 + i)
        const draft = drafts[i]
        return (
          <div key={i} className="thumb-concept">
            {draft && <a href={draft} target="_blank" rel="noreferrer"><img src={draft} alt={`Thumbnail draft ${tag}`} /></a>}
            <div className="stack" style={{ gap: 6, minWidth: 0 }}>
              <div className="row" style={{ gap: 6 }}>
                <b>{tag}. {p.concept || 'Concept'}</b>
                {p.emotion && <span className="badge info">{p.emotion}</span>}
                {p.text_overlay && <span className="chip">“{p.text_overlay}”</span>}
                {p.frame_timestamp != null && <span className="tiny faint">frame @ {fmtTime(p.frame_timestamp)}</span>}
              </div>
              {p.why_it_works && <div className="tiny muted">{p.why_it_works}</div>}
              <div className="copy-block small">{p[tab]}<CopyButton text={p[tab]} /></div>
              <div className="row tiny muted" style={{ gap: 10 }}>
                <span>Negative: {p.negative_prompt.slice(0, 60)}…</span>
                <CopyButton text={p.negative_prompt} />
                {frames[i] && <a href={frames[i]} target="_blank" rel="noreferrer">Reference frame {tag}</a>}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function LongFormCard({ clip: c, disabled, onChange, onPreview, onRender, onError }: Props) {
  const [busy, setBusy] = useState(false)
  const [customTitle, setCustomTitle] = useState('')
  const [editing, setEditing] = useState(false)
  const [start, setStart] = useState(c.start)
  const [end, setEnd] = useState(c.end)

  const patch = async (body: Record<string, unknown>) => {
    setBusy(true)
    try {
      onChange(await api<LongFormClip>(`/api/long-form/${c.id}`, { method: 'PATCH', json: body }))
      return true
    } catch (e) {
      onError((e as Error).message)
      return false
    } finally {
      setBusy(false)
    }
  }

  const title = c.titles[c.title_index] ?? c.titles[0] ?? c.topic
  const ex = c.export

  return (
    <div className={`cand ${c.selected ? 'selected' : ''} ${c.rejected ? 'rejected' : ''}`}>
      <div className="stack" style={{ alignItems: 'center', gap: 6 }}>
        <div className="cand-rank">L{c.rank}</div>
        <ScoreRing score={c.score} />
        <div className="score-caption">Long-form<br />potential</div>
        {c.demo && <span className="badge warn">DEMO</span>}
      </div>

      <div className="stack" style={{ gap: 12, minWidth: 0 }}>
        <div className="row between">
          <div className="row">
            <span className="timecode">{fmtTime(c.start)} → {fmtTime(c.end)}</span>
            <span className="badge neutral">{(c.duration / 60).toFixed(1)} min</span>
            {c.cold_open && <span className="badge info" title={c.cold_open.reason}>cold open {(c.cold_open.end - c.cold_open.start).toFixed(0)}s</span>}
            <span className="badge neutral">{c.chapters.length} chapters</span>
          </div>
          <label className="check small">
            <input type="checkbox" checked={c.selected} disabled={busy || c.rejected} onChange={(e) => patch({ selected: e.target.checked })} />
            Include in long-form render
          </label>
        </div>

        <div>
          <div className="label">Title</div>
          <div className="hook-quote">{title}<CopyButton text={title} /></div>
          <div className="small muted" style={{ marginTop: 2 }}>{c.topic}</div>
        </div>

        <div className="why">
          <div className="label" style={{ marginBottom: 2 }}>Why it works</div>
          <div className="small">{c.reason_it_works || '—'}</div>
          {c.summary && <div className="small muted" style={{ marginTop: 4 }}>{c.summary}</div>}
        </div>

        <div className="subscores">
          {c.subscores.map((s) => (
            <div key={s.key} className="subscore">
              <span className="muted">{SUB_LABEL[s.key] ?? s.key}</span>
              <Bar value={s.max ? s.value / s.max : 0} />
              <span className="num">{Math.round(s.value)}/{s.max}</span>
            </div>
          ))}
        </div>
        {c.penalties.length > 0 && (
          <div className="small">
            <span className="label">Penalties </span>
            {c.penalties.map((p, i) => <span key={i} className="chip">−{p.points} {p.reason}</span>)}
          </div>
        )}

        <div className="row" style={{ gap: 8 }}>
          <button className="btn sm" onClick={() => onPreview(c.start, c.end, `L${c.rank} ${title}`)}>▶ Preview</button>
          {c.cold_open && <button className="btn sm" onClick={() => onPreview(c.cold_open!.start, c.cold_open!.end, `Cold open · L${c.rank}`)}>▶ Cold open</button>}
          <button className="btn sm" onClick={() => { setEditing(!editing); setStart(c.start); setEnd(c.end) }}>Edit Start/End</button>
          <button className="btn sm primary" disabled={disabled || busy || c.rejected} onClick={onRender}>Render</button>
          {c.rejected ? (
            <button className="btn sm ghost" disabled={busy} onClick={() => patch({ rejected: false })}>Restore</button>
          ) : (
            <button className="btn sm ghost danger" disabled={busy} onClick={() => patch({ rejected: true })}>Reject</button>
          )}
        </div>

        {editing && (
          <div className="edit-box row">
            <span className="small muted">Start</span>
            <input type="number" step={1} style={{ width: 110 }} value={start} onChange={(e) => setStart(Number(e.target.value))} />
            <span className="mono muted small">{fmtTime(start)}</span>
            <span className="small muted">End</span>
            <input type="number" step={1} style={{ width: 110 }} value={end} onChange={(e) => setEnd(Number(e.target.value))} />
            <span className="mono muted small">{fmtTime(end)} · {((end - start) / 60).toFixed(1)} min</span>
            <span className="grow" />
            <button className="btn sm primary" disabled={busy || end - start < 30} onClick={async () => {
              if (await patch({ start, end })) setEditing(false)
            }}>Save (snaps to sentences)</button>
          </div>
        )}

        <details>
          <summary>Titles ({c.titles.length})</summary>
          <div className="hooks" style={{ marginTop: 8 }}>
            {c.titles.map((t, i) => (
              <label key={i} className={`hook-opt ${i === c.title_index ? 'on' : ''}`}>
                <input type="radio" name={`title-${c.id}`} checked={i === c.title_index} disabled={busy} onChange={() => patch({ title_index: i })} />
                <span className="grow">{t} <span className="tiny faint">· {t.length} chars{i === 0 ? ' · AI recommended' : ''}</span></span>
              </label>
            ))}
            <div className="row nowrap">
              <input type="text" maxLength={100} placeholder="Write your own title (keep it accurate)" value={customTitle} onChange={(e) => setCustomTitle(e.target.value)} />
              <button className="btn sm" disabled={!customTitle.trim() || busy} onClick={async () => {
                if (await patch({ custom_title: customTitle })) setCustomTitle('')
              }}>Add</button>
            </div>
          </div>
        </details>

        <details open={!ex}>
          <summary>Thumbnail concepts & prompts ({c.thumbnail_prompts.length})</summary>
          <div style={{ marginTop: 8 }}>
            <ThumbnailPrompts prompts={c.thumbnail_prompts} drafts={ex?.thumbnail_drafts} frames={ex?.thumbnail_frames} />
            {!ex && <p className="tiny muted">Rendering adds real reference frames (best face + sharpness near each concept's moment) and text drafts.</p>}
          </div>
        </details>

        <details>
          <summary>Chapters, description & tags</summary>
          <div className="stack small" style={{ gap: 8, marginTop: 8 }}>
            {c.cold_open && <div><span className="label">Cold open </span>{fmtTime(c.cold_open.start)}–{fmtTime(c.cold_open.end)} <span className="muted">{c.cold_open.reason}</span></div>}
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {c.chapters.map((ch, i) => <li key={i}><span className="mono">{fmtTime(ch.t)}</span> {ch.title}</li>)}
            </ul>
            {c.description && <div className="copy-block">{c.description}<CopyButton text={c.description} /></div>}
            {c.tags.length > 0 && <div>{c.tags.map((t) => <span key={t} className="chip">{t}</span>)}</div>}
            <div className="tiny muted">{c.snap_notes.join(' ')}</div>
          </div>
        </details>

        {ex && (
          <div className="stack" style={{ gap: 10 }}>
            <div className="divider" />
            <div className="row between">
              <h3>Rendered long-form · {(ex.duration / 60).toFixed(1)} min</h3>
              <ComplianceBadge status={ex.compliance_status} />
            </div>
            {ex.video && <video className="long-video" src={ex.video} controls preload="metadata" poster={ex.thumbnail_drafts[0] ?? undefined} />}
            {ex.chapters_text && (
              <div>
                <div className="label">YouTube chapters (paste into the description)</div>
                <div className="copy-block mono small">{ex.chapters_text}<CopyButton text={ex.chapters_text} /></div>
              </div>
            )}
            {ex.sound_events.length > 0 && (
              <div className="small"><span className="label">Sound design </span>
                {ex.sound_events.map((e, i) => <span key={i} className="chip" title={`${e.name} · ${e.gain_db} dB`}>{fmtTime(e.t)} {e.category}</span>)}
              </div>
            )}
            {ex.notes.length > 0 && <div className="tiny muted">{ex.notes.join(' · ')}</div>}
            <div className="tiny muted">Folder: <code>{ex.folder}</code></div>
          </div>
        )}
      </div>
    </div>
  )
}
