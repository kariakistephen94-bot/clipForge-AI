import { useState } from 'react'
import { api, fmtTime, type Candidate } from '../api'
import { Bar, ComplianceBadge, CopyButton, ScoreRing } from './common'
import { PublishModal } from './Publish'
import { ThumbnailPrompts } from './LongFormCard'

const SUB_LABEL: Record<string, string> = {
  hook: 'Hook', clarity: 'Clarity', curiosity: 'Curiosity', emotion: 'Emotion', specificity: 'Specificity',
  shareability: 'Shareability', retention: 'Retention', visual: 'Visual', campaign_fit: 'Campaign',
}
const VARIANT_LABEL: Record<string, string> = { A: 'A · hook overlay', B: 'B · alt hook', C: 'C · captions only' }
const PLATFORM_LABEL: Record<string, string> = { tiktok: 'TikTok', instagram: 'Instagram', youtube_shorts: 'YouTube Shorts' }

interface Props {
  candidate: Candidate
  disabled: boolean
  onChange: (c: Candidate) => void
  onPreview: (start: number, end: number, title: string) => void
  onRender: () => void
  onError: (msg: string) => void
}

export default function CandidateCard({ candidate: c, disabled, onChange, onPreview, onRender, onError }: Props) {
  const [editing, setEditing] = useState(false)
  const [start, setStart] = useState(c.start)
  const [end, setEnd] = useState(c.end)
  const [snap, setSnap] = useState(true)
  const [customHook, setCustomHook] = useState('')
  const [busy, setBusy] = useState(false)
  const [publishing, setPublishing] = useState(false)

  const patch = async (body: Record<string, unknown>) => {
    setBusy(true)
    try {
      onChange(await api<Candidate>(`/api/candidates/${c.id}`, { method: 'PATCH', json: body }))
      return true
    } catch (e) {
      onError((e as Error).message)
      return false
    } finally {
      setBusy(false)
    }
  }

  const refine = async () => {
    setBusy(true)
    try {
      onChange(await api<Candidate>(`/api/candidates/${c.id}/refine`, { method: 'POST' }))
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const hook = c.hooks[c.hook_index]?.text || c.topic
  const latestExport = c.exports[0]
  const pf = c.platform_fit as Record<string, number | string | undefined>

  return (
    <div className={`cand ${c.selected ? 'selected' : ''} ${c.rejected ? 'rejected' : ''}`}>
      <div className="stack" style={{ alignItems: 'center', gap: 6 }}>
        <div className="cand-rank">#{c.rank}</div>
        <ScoreRing score={c.viral_score} />
        <div className="score-caption">AI Viral<br />Potential Score</div>
        {c.user_edited && <span className="badge neutral" title="Boundaries edited; score not re-evaluated unless you re-score">edited</span>}
        {c.refined && <span className="badge info">re-scored</span>}
      </div>

      <div className="stack" style={{ gap: 12, minWidth: 0 }}>
        <div className="row between">
          <div className="row">
            <span className="timecode">{fmtTime(c.start)} → {fmtTime(c.end)}</span>
            <span className="badge neutral">{c.duration.toFixed(1)} sec</span>
            <ComplianceBadge status={c.compliance_status} />
            {c.hook_type && <span className="badge info">{c.hook_type.replace(/_/g, ' ')}</span>}
          </div>
          <label className="check small">
            <input type="checkbox" checked={c.selected} disabled={busy || c.rejected} onChange={(e) => patch({ selected: e.target.checked })} />
            Include in batch render
          </label>
        </div>

        <div>
          <div className="label">Hook</div>
          <div className="hook-quote">“{hook}”</div>
          <div className="small muted" style={{ marginTop: 2 }}>{c.topic}</div>
        </div>

        <div className="why">
          <div className="label" style={{ marginBottom: 2 }}>Why it may work</div>
          <div className="small">{c.reason_it_works || '—'}</div>
          {c.summary && <div className="small muted" style={{ marginTop: 4 }}>{c.summary}</div>}
          {c.boundary_feedback && <div className="tiny muted" style={{ marginTop: 4 }}>Boundary feedback: {c.boundary_feedback}</div>}
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
            {c.penalties.map((p, i) => (
              <span key={i} className="chip" title={p.source === 'local' ? 'Detected locally' : 'From AI analysis'}>
                −{p.points} {p.reason}
              </span>
            ))}
          </div>
        )}

        <div className="row" style={{ gap: 8 }}>
          <button className="btn sm" onClick={() => onPreview(c.start, c.end, `#${c.rank} ${c.topic}`)}>▶ Preview</button>
          <button className="btn sm" onClick={() => { setEditing(!editing); setStart(c.start); setEnd(c.end) }}>Edit Start/End</button>
          <button className="btn sm primary" disabled={disabled || busy || c.rejected} onClick={onRender}>Generate</button>
          {c.rejected ? (
            <button className="btn sm ghost" disabled={busy} onClick={() => patch({ rejected: false })}>Restore</button>
          ) : (
            <button className="btn sm ghost danger" disabled={busy} onClick={() => patch({ rejected: true })}>Reject</button>
          )}
          <span className="grow" />
          <span className="tiny faint">
            {['tiktok', 'instagram', 'youtube_shorts'].map((k) => pf[k] != null ? `${PLATFORM_LABEL[k]} ${pf[k]}/10` : null).filter(Boolean).join(' · ')}
          </span>
        </div>

        {editing && (
          <div className="edit-box stack" style={{ gap: 10 }}>
            <div className="row">
              <span className="small muted" style={{ width: 40 }}>Start</span>
              <button className="btn sm" onClick={() => setStart(Math.max(0, +(start - 1).toFixed(2)))}>−1s</button>
              <button className="btn sm" onClick={() => setStart(Math.max(0, +(start - 0.25).toFixed(2)))}>−¼</button>
              <input type="number" step={0.1} value={start} onChange={(e) => setStart(Number(e.target.value))} />
              <button className="btn sm" onClick={() => setStart(+(start + 0.25).toFixed(2))}>+¼</button>
              <button className="btn sm" onClick={() => setStart(+(start + 1).toFixed(2))}>+1s</button>
              <span className="mono muted">{fmtTime(start, true)}</span>
            </div>
            <div className="row">
              <span className="small muted" style={{ width: 40 }}>End</span>
              <button className="btn sm" onClick={() => setEnd(+(end - 1).toFixed(2))}>−1s</button>
              <button className="btn sm" onClick={() => setEnd(+(end - 0.25).toFixed(2))}>−¼</button>
              <input type="number" step={0.1} value={end} onChange={(e) => setEnd(Number(e.target.value))} />
              <button className="btn sm" onClick={() => setEnd(+(end + 0.25).toFixed(2))}>+¼</button>
              <button className="btn sm" onClick={() => setEnd(+(end + 1).toFixed(2))}>+1s</button>
              <span className="mono muted">{fmtTime(end, true)} · {(end - start).toFixed(1)}s</span>
            </div>
            <div className="row">
              <label className="check small"><input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} /> Snap to natural speech boundaries</label>
              <span className="grow" />
              <button className="btn sm" onClick={() => onPreview(start, end, `Edit preview #${c.rank}`)}>▶ Preview edit</button>
              <button className="btn sm primary" disabled={busy || end <= start} onClick={async () => {
                if (await patch({ start, end, snap })) setEditing(false)
              }}>Save boundaries</button>
              <button className="btn sm" disabled={busy} onClick={refine} title="Text-only re-evaluation (1 small Gemini request, or offline heuristic in demo mode)">Re-score with AI</button>
            </div>
          </div>
        )}

        <details>
          <summary>Hook options ({c.hooks.length})</summary>
          <div className="hooks" style={{ marginTop: 8 }}>
            {c.hooks.map((h, i) => (
              <label key={i} className={`hook-opt ${i === c.hook_index ? 'on' : ''}`}>
                <input type="radio" name={`hook-${c.id}`} checked={i === c.hook_index} disabled={busy} onChange={() => patch({ hook_index: i })} />
                <span className="grow">
                  {h.text}
                  <span className="tiny faint"> · {i === 0 && h.source === 'ai' ? 'AI recommended' : h.source}</span>
                </span>
              </label>
            ))}
            <div className="row nowrap">
              <input type="text" maxLength={120} placeholder="Write your own hook (keep it accurate to the clip)" value={customHook} onChange={(e) => setCustomHook(e.target.value)} />
              <button className="btn sm" disabled={!customHook.trim() || busy} onClick={async () => {
                if (await patch({ custom_hook: customHook })) setCustomHook('')
              }}>Add</button>
            </div>
          </div>
        </details>

        <details>
          <summary>Compliance details</summary>
          <div className="checks" style={{ marginTop: 8 }}>
            <ul>
              {c.compliance?.checks.map((ch, i) => (
                <li key={i}><ComplianceBadge status={ch.status} /> <b>{ch.rule}</b> <span className="muted">{ch.detail}</span>{ch.source === 'ai' && <span className="tiny faint">(AI)</span>}</li>
              ))}
            </ul>
            {c.compliance?.manual_checks.length ? (
              <>
                <div className="label" style={{ marginTop: 6 }}>Check manually</div>
                <ul>{c.compliance.manual_checks.map((m, i) => <li key={i} className="muted">• {m}</li>)}</ul>
              </>
            ) : null}
          </div>
        </details>

        <details>
          <summary>Transcript, editing plan & boundaries</summary>
          <div className="stack small" style={{ gap: 8, marginTop: 8 }}>
            <div className="copy-block">{c.transcript_text || '(no speech)'}</div>
            {c.editing_strategy && <div><span className="label">Editing strategy </span>{c.editing_strategy}</div>}
            {c.target_audience && <div><span className="label">Audience </span>{c.target_audience}</div>}
            {c.caption_emphasis_words.length > 0 && <div><span className="label">Caption emphasis </span>{c.caption_emphasis_words.map((w) => <span key={w} className="chip">{w}</span>)}</div>}
            {c.suggested_zoom_points.length > 0 && <div><span className="label">Punch-ins </span>{c.suggested_zoom_points.map((z, i) => <span key={i} className="chip" title={z.reason}>{fmtTime(z.timestamp)} {z.kind}</span>)}</div>}
            {c.suggested_broll_points.length > 0 && <div><span className="label">B-roll ideas </span>{c.suggested_broll_points.map((b, i) => <span key={i} className="chip" title={b.reason}>{fmtTime(b.timestamp)} “{b.query}”</span>)}</div>}
            <div className="tiny muted">AI region {fmtTime(c.ai_start, true)} → {fmtTime(c.ai_end, true)}, snapped to {fmtTime(c.start, true)} → {fmtTime(c.end, true)}.{' '}
              {c.snap_notes.join(' ')}</div>
          </div>
        </details>

        {latestExport && (
          <div className="stack" style={{ gap: 10 }}>
            <div className="divider" />
            <div className="row between">
              <h3>Rendered clip</h3>
              <span className="grow" />
              <button className="btn sm" disabled={latestExport.compliance_status === 'FAILED'}
                title={latestExport.compliance_status === 'FAILED' ? 'Blocked: this clip failed campaign compliance' : 'Publish to your connected accounts'}
                onClick={() => setPublishing(true)}>Publish…</button>
              <ComplianceBadge status={latestExport.compliance_status} />
            </div>
            {publishing && <PublishModal candidatePk={c.id} onClose={() => setPublishing(false)} />}
            <div className="exports-strip">
              {Object.entries(latestExport.videos).sort().map(([v, url]) => (
                <div key={v} className="export-video">
                  <video src={url} controls preload="metadata" poster={latestExport.thumbnail ?? undefined} />
                  <span className="tiny muted">{VARIANT_LABEL[v] ?? v}</span>
                </div>
              ))}
            </div>
            {latestExport.sound_events.length > 0 && (
              <div className="small"><span className="label">Sound design </span>
                {latestExport.sound_events.map((e, i) => (
                  <span key={i} className="chip" title={`${e.name} · ${e.reason} · ${e.gain_db} dB${e.hook_only ? ' · hook variants only' : ''}`}>
                    {e.t.toFixed(1)}s {e.category}
                  </span>
                ))}
              </div>
            )}
            {latestExport.notes.length > 0 && <div className="tiny muted">{latestExport.notes.join(' · ')}</div>}
            {c.posting_copy?.thumbnail_prompts?.length ? (
              <details>
                <summary>Cover / thumbnail prompt{c.posting_copy.cover_text ? ` · “${c.posting_copy.cover_text}”` : ''}</summary>
                <div style={{ marginTop: 8 }}>
                  <ThumbnailPrompts prompts={c.posting_copy.thumbnail_prompts} frames={latestExport.thumbnail ? [latestExport.thumbnail] : []} />
                </div>
              </details>
            ) : null}
            {c.posting_copy && (
              <details>
                <summary>Posting copy</summary>
                <div className="stack" style={{ gap: 8, marginTop: 8 }}>
                  {(c.posting_copy.required.hashtags.length > 0 || c.posting_copy.required.mentions.length > 0 || c.posting_copy.required.cta) && (
                    <div className="small">
                      <span className="badge warn">REQUIRED BY CAMPAIGN</span>{' '}
                      {[...c.posting_copy.required.hashtags, ...c.posting_copy.required.mentions, c.posting_copy.required.cta ?? '', ...c.posting_copy.required.links]
                        .filter(Boolean).map((x) => <span key={x} className="chip">{x}</span>)}
                    </div>
                  )}
                  {Object.entries(c.posting_copy.full).map(([platform, text]) => (
                    <div key={platform}>
                      <div className="label">{PLATFORM_LABEL[platform] ?? platform}</div>
                      <div className="copy-block">{text}<CopyButton text={text} /></div>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
