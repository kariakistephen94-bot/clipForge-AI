import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Job, JobStep } from '../api'

export function ComplianceBadge({ status }: { status: string }) {
  const cls = status === 'COMPLIANT' ? 'ok' : status === 'WARNING' ? 'warn' : status === 'FAILED' ? 'bad' : 'neutral'
  const icon = status === 'COMPLIANT' ? '✓' : status === 'WARNING' ? '!' : status === 'FAILED' ? '✕' : '?'
  return (
    <span className={`badge ${cls}`}>
      {icon} {status}
    </span>
  )
}

const STATUS_CLASS: Record<string, string> = {
  created: 'neutral', waiting: 'warn', analyzing: 'info', ready: 'ok', rendering: 'info', rendered: 'ok', error: 'bad',
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${STATUS_CLASS[status] || 'neutral'}`}>{status}</span>
}

export function ScoreRing({ score, size = 96 }: { score: number; size?: number }) {
  const r = size / 2 - 7
  const c = 2 * Math.PI * r
  const pct = Math.max(0, Math.min(100, score)) / 100
  const color = score >= 80 ? 'var(--ok)' : score >= 60 ? 'var(--accent)' : score >= 40 ? 'var(--warn)' : 'var(--bad)'
  return (
    <div className="score-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--panel-2)" strokeWidth="7" fill="none" />
        <circle cx={size / 2} cy={size / 2} r={r} stroke={color} strokeWidth="7" fill="none" strokeLinecap="round"
          strokeDasharray={`${c * pct} ${c}`} />
      </svg>
      <div className="val">
        <div>
          <b>{Math.round(score)}</b>
          <small>/100</small>
        </div>
      </div>
    </div>
  )
}

export function Bar({ value, indeterminate = false }: { value: number | null; indeterminate?: boolean }) {
  return (
    <div className={`bar ${indeterminate ? 'indeterminate' : ''}`}>
      <i style={{ width: `${Math.round((value ?? 0) * 100)}%` }} />
    </div>
  )
}

function StepRow({ step }: { step: JobStep }) {
  const icon = step.status === 'done' ? '✓' : step.status === 'error' ? '!' : step.status === 'skipped' ? '–' : ''
  const showPct = step.status === 'running' && step.progress != null && step.progress > 0 && step.progress < 1
  return (
    <div className={`step ${step.status}`}>
      <div className="icon">{icon}</div>
      <div className="stack" style={{ gap: 3 }}>
        <div className="step-label">{step.label}</div>
        {step.status === 'running' && <Bar value={step.progress} indeterminate={!step.progress} />}
        {step.detail && <div className="step-detail">{step.detail}</div>}
      </div>
      <div className="pct">{showPct ? `${Math.round((step.progress ?? 0) * 100)}%` : ''}</div>
    </div>
  )
}

export function JobPanel({ job }: { job: Job | null }) {
  if (!job) return null
  const running = job.status === 'running' || job.status === 'queued'
  const title = ({ render: 'Render batch', render_long: 'Long-form render', longform: 'Long-form finder' } as Record<string, string>)[job.kind] ?? 'Analysis pipeline'
  return (
    <div className="card">
      <div className="card-title">
        <h2>{title}</h2>
        <span className={`badge ${running ? 'info' : job.status === 'error' ? 'bad' : 'ok'}`}>
          {running ? 'running' : job.status}
        </span>
      </div>
      <div className="steps">
        {job.steps.map((s) => (
          <StepRow key={s.key} step={s} />
        ))}
      </div>
      {job.message && <p className="small" style={{ marginBottom: 0 }}>{job.message}</p>}
      {job.error && (
        <div className="banner bad" style={{ marginTop: 12, marginBottom: 0 }}>
          <b>Error</b>
          <span className="grow">{job.error}</span>
        </div>
      )}
    </div>
  )
}

export function Toggle({ label, checked, onChange, hint, disabled }: {
  label: ReactNode; checked: boolean; onChange: (v: boolean) => void; hint?: ReactNode; disabled?: boolean
}) {
  return (
    <label className="check" style={{ alignItems: 'flex-start', opacity: disabled ? 0.5 : 1 }}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} style={{ marginTop: 2 }} />
      <span>
        {label}
        {hint && <div className="tiny muted">{hint}</div>}
      </span>
    </label>
  )
}

export function TriState({ value }: { value: boolean | null | undefined }) {
  if (value === true) return <span className="badge ok">Allowed</span>
  if (value === false) return <span className="badge bad">Prohibited</span>
  return <span className="badge neutral">Unknown</span>
}

export function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false)
  return (
    <button className="btn sm" onClick={() => {
      navigator.clipboard?.writeText(text).then(() => {
        setDone(true)
        setTimeout(() => setDone(false), 1200)
      })
    }}>
      {done ? 'Copied' : 'Copy'}
    </button>
  )
}

export function Toast({ message, kind = 'info', onDone }: { message: string; kind?: 'info' | 'bad'; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, kind === 'bad' ? 7000 : 3500)
    return () => clearTimeout(t)
  }, [message, kind, onDone])
  return <div className={`toast ${kind}`}>{message}</div>
}

export function SegmentPreview({ src, start, end, title, onClose }: {
  src: string; start: number; end: number; title: string; onClose: () => void
}) {
  const ref = useRef<HTMLVideoElement>(null)
  const [ended, setEnded] = useState(false)
  useEffect(() => {
    const v = ref.current
    if (!v) return
    const seek = () => {
      v.currentTime = start
      v.play().catch(() => undefined)
    }
    if (v.readyState >= 1) seek()
    else v.addEventListener('loadedmetadata', seek, { once: true })
    const onTime = () => {
      if (v.currentTime >= end) {
        v.pause()
        setEnded(true)
      }
    }
    v.addEventListener('timeupdate', onTime)
    return () => v.removeEventListener('timeupdate', onTime)
  }, [start, end])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row between" style={{ marginBottom: 10 }}>
          <h2 className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</h2>
          <button className="btn sm" onClick={onClose}>Close</button>
        </div>
        <video ref={ref} src={src} controls preload="metadata" />
        <div className="row between" style={{ marginTop: 10 }}>
          <span className="muted small">Source preview of the (landscape) original. Rendering applies 9:16 framing, captions and edits.</span>
          <button className="btn sm" onClick={() => {
            const v = ref.current
            if (v) {
              v.currentTime = start
              v.play().catch(() => undefined)
              setEnded(false)
            }
          }}>{ended ? 'Replay segment' : 'Restart'}</button>
        </div>
      </div>
    </div>
  )
}
