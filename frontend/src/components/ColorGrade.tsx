import { useEffect, useState } from 'react'
import type { GradePreset } from '../api'

export type GradeOverrides = Record<string, number>

const SLIDERS: { key: keyof GradePreset & string; label: string; min: number; max: number }[] = [
  { key: 'exposure', label: 'Exposure', min: -1, max: 1 },
  { key: 'contrast', label: 'Contrast', min: -1, max: 1 },
  { key: 'saturation', label: 'Saturation', min: -1, max: 1 },
  { key: 'temperature', label: 'Temperature · cool ↔ warm', min: -1, max: 1 },
  { key: 'tint', label: 'Tint · green ↔ magenta', min: -1, max: 1 },
  { key: 'shadows', label: 'Shadows · crush ↔ lift', min: -1, max: 1 },
  { key: 'highlights', label: 'Highlights · recover ↔ brighten', min: -1, max: 1 },
  { key: 'fade', label: 'Matte (lifted blacks)', min: 0, max: 1 },
  { key: 'vignette', label: 'Vignette', min: 0, max: 1 },
  { key: 'intensity', label: 'Intensity', min: 0, max: 1 },
]

/** Query string for /api/projects/:id/grade-preview (preset + only the values the user changed). */
function gradeQuery(grade: string, overrides: GradeOverrides): string {
  const q = new URLSearchParams({ grade })
  for (const [k, v] of Object.entries(overrides)) q.set(k, String(v))
  return q.toString()
}

export function GradeSelect({ value, grades, onChange, label = 'Colour grade' }: {
  value: string; grades: Record<string, GradePreset>; onChange: (v: string) => void; label?: string
}) {
  const list = Object.values(grades)
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {list.length ? list.map((g) => <option key={g.name} value={g.name}>{g.label}</option>) : <option value="none">Off</option>}
      </select>
      {grades[value]?.description && <span className="tiny muted">{grades[value].description}</span>}
    </label>
  )
}

export function GradeSliders({ grade, overrides, grades, onChange }: {
  grade: string; overrides: GradeOverrides; grades: Record<string, GradePreset>; onChange: (o: GradeOverrides) => void
}) {
  const preset = grades[grade]
  const value = (k: keyof GradePreset & string) => overrides[k] ?? (preset ? Number(preset[k]) : k === 'intensity' ? 1 : 0)
  const changed = Object.keys(overrides).length > 0
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="settings-grid" style={{ gap: 8 }}>
        {SLIDERS.map((s) => (
          <label key={s.key} className="field">
            <span>{s.label}: {value(s.key) > 0 && s.min < 0 ? '+' : ''}{value(s.key).toFixed(2)}{overrides[s.key] !== undefined && <span className="faint"> · edited</span>}</span>
            <input type="range" min={s.min} max={s.max} step={0.05} value={value(s.key)}
              onChange={(e) => onChange({ ...overrides, [s.key]: Number(e.target.value) })} />
          </label>
        ))}
      </div>
      <div className="row between">
        <span className="tiny muted">Sliders start at the preset's values; only the ones you move are stored, so a different preset keeps your tweaks.</span>
        <button className="btn sm ghost" disabled={!changed} onClick={() => onChange({})}>Reset to preset</button>
      </div>
    </div>
  )
}

export function GradePreview({ projectId, t, grade, overrides }: {
  projectId: string; t: number; grade: string; overrides: GradeOverrides
}) {
  const base = `/api/projects/${projectId}/grade-preview?t=${t.toFixed(2)}`
  const [url, setUrl] = useState<string>(`${base}&${gradeQuery(grade, overrides)}`)
  const query = gradeQuery(grade, overrides)
  useEffect(() => {
    const id = setTimeout(() => setUrl(`${base}&${query}`), 250) // sliders fire continuously; one frame decode per pause
    return () => clearTimeout(id)
  }, [base, query])
  const off = grade === 'none' && Object.keys(overrides).length === 0
  return (
    <div className="grade-preview">
      <figure>
        <img src={`${base}&grade=none`} alt="Source frame" />
        <figcaption>Source</figcaption>
      </figure>
      <figure>
        <img src={url} alt="Graded frame" />
        <figcaption>{off ? 'Grade is off' : 'Graded'} <span className="faint">· captions and hooks are added on top, ungraded</span></figcaption>
      </figure>
    </div>
  )
}
