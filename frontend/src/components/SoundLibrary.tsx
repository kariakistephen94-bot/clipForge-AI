import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type SfxItem, type SfxResponse } from '../api'

type Cats = Record<string, { label: string; description: string; auto: boolean }>

export default function SoundLibrary({ categories, reloadKey }: { categories: Cats; reloadKey: number }) {
  const [data, setData] = useState<SfxResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [cat, setCat] = useState<string>('whoosh')
  const [query, setQuery] = useState('')
  const [playing, setPlaying] = useState<string | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await api<SfxResponse>('/api/sfx'))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => { load() }, [load, reloadKey])
  useEffect(() => {  // poll while the first scan analyses files
    if (!data?.scanning) return
    const t = setTimeout(load, 1500)
    return () => clearTimeout(t)
  }, [data, load])
  useEffect(() => () => audio.current?.pause(), [])

  const rescan = async (force = false) => {
    await api(`/api/sfx/rescan?force=${force}`, { method: 'POST' })
    setTimeout(load, 400)
  }

  const play = (it: SfxItem) => {
    audio.current?.pause()
    if (playing === it.id) {
      setPlaying(null)
      return
    }
    const a = new Audio(`/api/sfx/${it.id}/audio`)
    const stopAt = it.offset + Math.min(it.tail + 0.2, 8)
    a.addEventListener('loadedmetadata', () => {
      a.currentTime = it.offset + Math.max(0, it.lead - 0.05)
      a.play().catch(() => undefined)
    }, { once: true })
    a.addEventListener('timeupdate', () => {
      if (a.currentTime >= stopAt) {
        a.pause()
        setPlaying(null)
      }
    })
    a.addEventListener('ended', () => setPlaying(null))
    audio.current = a
    setPlaying(it.id)
  }

  const patch = async (it: SfxItem, body: { category?: string; enabled?: boolean }) => {
    try {
      const updated = await api<SfxItem>(`/api/sfx/${it.id}`, { method: 'PATCH', json: body })
      setData((d) => d && { ...d, items: d.items.map((x) => (x.id === it.id ? { ...x, ...updated } : x)) })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (error) return <div className="banner bad" style={{ margin: 0 }}>{error}</div>
  if (!data) return <p className="muted small">Loading sound library…</p>
  if (data.scanning) {
    const s = data.scanning
    return <p className="small muted">Analysing sound files… {s.done}/{s.total} (first scan only; later scans only look at new files)</p>
  }
  const counts = data.summary?.counts ?? {}
  const items = data.items.filter((i) => i.category === cat && (!query || i.label.toLowerCase().includes(query.toLowerCase())))

  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row between">
        <span className="small muted">{data.summary?.files ?? 0} files → {data.summary?.items ?? 0} usable sounds (packs are split into single hits)</span>
        <div className="row" style={{ gap: 6 }}>
          <button className="btn sm" onClick={() => rescan(false)}>Rescan</button>
          <button className="btn sm ghost" onClick={() => rescan(true)} title="Re-analyse every file">Full rescan</button>
        </div>
      </div>
      <div className="sfx-cats">
        {Object.entries(categories).map(([k, c]) => (
          <button key={k} className={cat === k ? 'on' : ''} title={c.description + (c.auto ? '' : ' (not placed automatically)')} onClick={() => setCat(k)}>
            {c.label} · {counts[k] ?? 0}
          </button>
        ))}
      </div>
      <div className="row">
        <span className="tiny muted grow">{categories[cat]?.description}{categories[cat] && !categories[cat].auto ? ' — only used when enabled/asked for' : ''}</span>
        <input type="search" placeholder="Filter by name" value={query} onChange={(e) => setQuery(e.target.value)} style={{ width: 180 }} />
      </div>
      <div className="sfx-list">
        {items.length === 0 && <div className="sfx-row"><span /><span className="muted">No sounds in this category.</span></div>}
        {items.map((it) => (
          <div key={it.id} className={`sfx-row ${it.enabled ? '' : 'off'}`}>
            <button className="btn sm" onClick={() => play(it)} title="Preview">{playing === it.id ? '■' : '▶'}</button>
            <span className="name" title={it.path}>
              {it.label}
              <span className="tiny faint"> · {(it.tail - it.lead).toFixed(2)}s{it.category_source === 'acoustic' ? ' · auto-detected' : it.category_source === 'user' ? ' · set by you' : ''}</span>
            </span>
            <select value={it.category} onChange={(e) => patch(it, { category: e.target.value })}>
              {Object.entries(categories).map(([k, c]) => <option key={k} value={k}>{c.label}</option>)}
            </select>
            <label className="check small meta"><input type="checkbox" checked={it.enabled} onChange={(e) => patch(it, { enabled: e.target.checked })} /> use</label>
          </div>
        ))}
      </div>
    </div>
  )
}
