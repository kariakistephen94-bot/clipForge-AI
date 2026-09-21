// Small form controls for the lab panels.
import type { ReactNode } from 'react'

export function Section({ title, children, open = false }: { title: string; children: ReactNode; open?: boolean }) {
  return (
    <details className="section" open={open}>
      <summary>{title}</summary>
      <div className="section-body">{children}</div>
    </details>
  )
}

export function Slider({ label, value, min, max, step = 1, unit = '', onChange }: {
  label: string
  value: number
  min: number
  max: number
  step?: number
  unit?: string
  onChange: (v: number) => void
}) {
  return (
    <label className="field slider">
      <span className="lbl">{label}<b>{Number.isInteger(step) ? value : value.toFixed(step < 0.1 ? 2 : 1)}{unit}</b></span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  )
}

export function Select<T extends string | number>({ label, value, options, onChange }: {
  label: string
  value: T
  options: readonly (T | { value: T; label: string })[]
  onChange: (v: T) => void
}) {
  return (
    <label className="field">
      <span className="lbl">{label}</span>
      <select
        value={String(value)}
        onChange={(e) => {
          const opt = options.map((o) => (typeof o === 'object' ? o.value : o)).find((o) => String(o) === e.target.value)
          if (opt !== undefined) onChange(opt)
        }}
      >
        {options.map((o) => {
          const v = typeof o === 'object' ? o.value : o
          const l = typeof o === 'object' ? o.label : String(o)
          return <option key={String(v)} value={String(v)}>{l}</option>
        })}
      </select>
    </label>
  )
}

export function Color({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="field color">
      <span className="lbl">{label}</span>
      <span className="color-row">
        <input type="color" value={value} onChange={(e) => onChange(e.target.value)} />
        <code>{value.toUpperCase()}</code>
      </span>
    </label>
  )
}

export function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="field toggle">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  )
}
