import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api, fmtBytes, fmtDate, fmtTime, isDriveFolder, PLATFORMS, uploadProject,
  type DriveFile, type ProjectSummary, type SettingsResponse, type SystemInfo,
} from '../api'
import { Bar, StatusBadge } from './common'

const EXAMPLE_RULES = `Example (paste the real campaign brief here):
Clips must be 15-60 seconds.
Post to TikTok, Instagram Reels and YouTube Shorts.
Include #ExampleCampaign and tag @examplecreator.
Captions are allowed. No split screen.
Do not add copyrighted music.`

export default function Dashboard({ system, settings }: { system: SystemInfo | null; settings: SettingsResponse | null }) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [cleaning, setCleaning] = useState(false)

  const load = useCallback(() => {
    api<ProjectSummary[]>('/api/projects').then(setProjects).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 6000)
    return () => clearInterval(t)
  }, [load])

  const cleanup = async () => {
    const failed = projects?.filter((p) => p.status === 'error').length ?? 0
    const msg = 'Clear the project history?\n\nFinished projects are only removed from this list; their source video, renders and exports stay on disk.'
      + (failed ? `\n\n${failed} failed project(s) will be deleted along with their files.` : '')
    if (!window.confirm(msg)) return
    setCleaning(true)
    setError(null)
    try {
      await api('/api/projects/cleanup-history', { method: 'POST' })
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setCleaning(false)
    }
  }

  return (
    <div className="grid-2">
      <NewProjectForm system={system} settings={settings} />
      <div className="card" style={{ alignSelf: 'start' }}>
        <div className="card-title">
          <h2>Project history</h2>
          <span className="sub">{projects ? `${projects.length} project(s)` : ''}</span>
          {!!projects?.length && (
            <button className="btn sm ghost" style={{ marginLeft: 'auto' }} disabled={cleaning} onClick={cleanup}
              title="Hide finished projects (files are kept) and delete failed ones">
              {cleaning ? 'Cleaning…' : 'Clean up history'}
            </button>
          )}
        </div>
        {error && <p className="error-text">{error}</p>}
        {projects && projects.length === 0 && (
          <div className="empty">
            <div className="big">🎬</div>
            No projects yet. Create one to start clipping.
          </div>
        )}
        <div className="stack" style={{ gap: 8 }}>
          {projects?.map((p) => (
            <a key={p.id} className="project-row" href={`#/project/${p.id}`}>
              <div style={{ minWidth: 0 }}>
                <div className="title">{p.name}</div>
                <div className="small muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {p.campaign_name || 'No campaign'} · {p.source_name || 'source'} {p.duration ? `· ${fmtTime(p.duration)}` : ''}
                </div>
                <div className="tiny faint">
                  {fmtDate(p.created_at)} · {p.candidates} candidates{p.long_form ? ` · ${p.long_form} long-form` : ''} · {p.exports} exported
                  {p.provider === 'demo' ? ' · DEMO analysis' : ''}
                </div>
              </div>
              <StatusBadge status={p.status} />
            </a>
          ))}
        </div>
      </div>
    </div>
  )
}

function NewProjectForm({ system, settings }: { system: SystemInfo | null; settings: SettingsResponse | null }) {
  const prefs = settings?.preferences
  const [name, setName] = useState('')
  const [campaign, setCampaign] = useState('')
  const [sourceMode, setSourceMode] = useState<'upload' | 'url'>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [url, setUrl] = useState('')
  const [rules, setRules] = useState('')
  const [platforms, setPlatforms] = useState<string[]>(['tiktok', 'instagram', 'youtube_shorts'])
  const [clipCount, setClipCount] = useState(10)
  const [durationMode, setDurationMode] = useState<'auto' | 'manual'>('auto')
  const [minD, setMinD] = useState(15)
  const [maxD, setMaxD] = useState(60)
  const [longMode, setLongMode] = useState<'auto' | 'manual' | 'off'>('auto')
  const [longCount, setLongCount] = useState(3)
  const [useGemini, setUseGemini] = useState(true)
  const [permission, setPermission] = useState(false)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [drag, setDrag] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  // Google Drive folder links: list the videos inside so the user can pick one or more.
  const folderLink = sourceMode === 'url' && isDriveFolder(url)
  const [folderFiles, setFolderFiles] = useState<DriveFile[] | null>(null)
  const [folderBusy, setFolderBusy] = useState(false)
  const [folderError, setFolderError] = useState<string | null>(null)
  const [picked, setPicked] = useState<string[]>([])

  useEffect(() => {
    setFolderFiles(null)
    setFolderError(null)
    setPicked([])
    if (!folderLink) return
    let cancelled = false
    const t = setTimeout(() => {
      setFolderBusy(true)
      api<{ files: DriveFile[] }>(`/api/drive/folder?url=${encodeURIComponent(url.trim())}`)
        .then((r) => {
          if (cancelled) return
          setFolderFiles(r.files)
          if (r.files.length === 1) setPicked([r.files[0].id])
        })
        .catch((e) => !cancelled && setFolderError((e as Error).message))
        .finally(() => !cancelled && setFolderBusy(false))
    }, 400)
    return () => { cancelled = true; clearTimeout(t) }
  }, [folderLink, url])

  useEffect(() => {
    if (prefs) {
      setMinD(prefs.default_min_duration)
      setMaxD(prefs.default_max_duration)
      setUseGemini(prefs.use_gemini)
    }
  }, [prefs])

  const geminiReady = !!system?.gemini.configured
  const hasSource = sourceMode === 'upload' ? !!file : folderLink ? picked.length > 0 : !!url.trim()
  const canSubmit = name.trim() && platforms.length > 0 && permission && hasSource && !busy

  const baseForm = (projectName: string) => {
    const fd = new FormData()
    fd.append('name', projectName)
    fd.append('campaign_name', campaign.trim())
    fd.append('campaign_rules_text', rules)
    fd.append('platforms', platforms.join(','))
    fd.append('desired_clip_count', String(clipCount))
    fd.append('duration_mode', durationMode)
    fd.append('min_duration', String(minD))
    fd.append('max_duration', String(maxD))
    fd.append('use_gemini', String(useGemini && geminiReady))
    fd.append('permission_confirmed', String(permission))
    fd.append('long_form_mode', longMode)
    fd.append('long_form_count', String(longMode === 'manual' ? longCount : 0))
    return fd
  }

  const submitFolder = async (autoRender: boolean) => {
    const chosen = (folderFiles ?? []).filter((f) => picked.includes(f.id))
    const created: string[] = []
    try {
      for (const [i, f] of chosen.entries()) {
        setProgress(i / chosen.length)
        const title = chosen.length > 1 ? `${name.trim()} · ${f.name.replace(/\.[a-z0-9]{2,4}$/i, '')}` : name.trim()
        const fd = baseForm(title.slice(0, 200))
        fd.append('source_url', f.url)
        const project = await api<{ id: string }>('/api/projects', { method: 'POST', body: fd })
        await api(`/api/projects/${project.id}/analyze`, { method: 'POST', json: { auto_render: autoRender } })
        created.push(project.id)
      }
    } catch (e) {
      setError(`${created.length ? `Created ${created.length} of ${chosen.length} projects, then: ` : ''}${(e as Error).message}`)
      setBusy(false)
      return
    }
    if (created.length === 1) {
      window.location.hash = `#/project/${created[0]}`
      return
    }
    setBusy(false)
    setPicked([])
    setNotice(`Created ${created.length} projects. They download and ${autoRender ? 'render' : 'analyze'} one after another; follow them in Project history.`)
  }

  const submit = async (autoRender: boolean) => {
    setError(null)
    setNotice(null)
    setBusy(true)
    setProgress(0)
    if (folderLink) return submitFolder(autoRender)
    const fd = new FormData()
    fd.append('name', name.trim())
    fd.append('campaign_name', campaign.trim())
    fd.append('campaign_rules_text', rules)
    fd.append('platforms', platforms.join(','))
    fd.append('desired_clip_count', String(clipCount))
    fd.append('duration_mode', durationMode)
    fd.append('min_duration', String(minD))
    fd.append('max_duration', String(maxD))
    fd.append('use_gemini', String(useGemini && geminiReady))
    fd.append('permission_confirmed', String(permission))
    fd.append('long_form_mode', longMode)
    fd.append('long_form_count', String(longMode === 'manual' ? longCount : 0))
    if (sourceMode === 'upload' && file) fd.append('file', file)
    else fd.append('source_url', url.trim())
    try {
      const project = await uploadProject(fd, setProgress)
      await api(`/api/projects/${project.id}/analyze`, { method: 'POST', json: { auto_render: autoRender } })
      window.location.hash = `#/project/${project.id}`
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  const togglePlatform = (k: string) =>
    setPlatforms((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]))

  return (
    <div className="card">
      <div className="card-title">
        <h1>New project</h1>
        <span className="sub">Long-form → ranked, ready-to-post shorts</span>
      </div>
      <div className="stack">
        <div className="row nowrap" style={{ gap: 12 }}>
          <label className="field grow">
            <span>Project name</span>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Podcast ep. 42" />
          </label>
          <label className="field grow">
            <span>Campaign</span>
            <input type="text" value={campaign} onChange={(e) => setCampaign(e.target.value)} placeholder="Content Rewards campaign name" />
          </label>
        </div>

        <div className="field">
          <div className="row between">
            <span className="small muted" style={{ fontWeight: 550 }}>Source</span>
            <div className="segmented">
              <button className={sourceMode === 'upload' ? 'on' : ''} onClick={() => setSourceMode('upload')}>Upload video</button>
              <button className={sourceMode === 'url' ? 'on' : ''} onClick={() => setSourceMode('url')}>Link (YouTube / Drive / Dropbox)</button>
            </div>
          </div>
          {sourceMode === 'upload' ? (
            <div
              className={`dropzone ${drag ? 'drag' : ''}`}
              onClick={() => fileInput.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDrag(false)
                if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0])
              }}
            >
              <input ref={fileInput} type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm,.m4v,.mkv" hidden
                onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              {file ? (
                <div><b>{file.name}</b> <span className="muted">· {fmtBytes(file.size)}</span></div>
              ) : (
                <div><b>Drop a video here</b> or click to browse<div className="small muted">MP4, MOV or WebM</div></div>
              )}
            </div>
          ) : (
            <>
              <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="YouTube link, or a Google Drive file / folder, or Dropbox link" />
              <span className="tiny muted">
                Public YouTube videos, or Drive/Dropbox files and Drive folders shared with "Anyone with the link" (e.g. a campaign's source folder).
                ClipForge never signs in or bypasses restrictions.
              </span>
              {folderLink && (
                <DriveFolderPicker files={folderFiles} busy={folderBusy} error={folderError} picked={picked} setPicked={setPicked} />
              )}
            </>
          )}
        </div>

        <label className="field">
          <span>Campaign rules <span className="faint">(paste the complete brief; AI converts it to structured rules and never invents missing ones)</span></span>
          <textarea value={rules} onChange={(e) => setRules(e.target.value)} placeholder={EXAMPLE_RULES} rows={7} />
        </label>

        <div className="field">
          <span>Target platforms</span>
          <div className="row">
            {PLATFORMS.map((p) => (
              <label key={p.key} className={`check-card ${platforms.includes(p.key) ? 'on' : ''}`}>
                <input type="checkbox" checked={platforms.includes(p.key)} onChange={() => togglePlatform(p.key)} />
                {p.label}
              </label>
            ))}
          </div>
        </div>

        <div className="row" style={{ gap: 18, alignItems: 'flex-end' }}>
          <label className="field" style={{ width: 130 }}>
            <span>Desired clip count</span>
            <input type="number" min={1} max={30} value={clipCount} onChange={(e) => setClipCount(Number(e.target.value))} />
          </label>
          <div className="field grow">
            <span>Clip duration</span>
            <div className="row">
              <div className="segmented">
                <button className={durationMode === 'auto' ? 'on' : ''} onClick={() => setDurationMode('auto')}>Auto from rules</button>
                <button className={durationMode === 'manual' ? 'on' : ''} onClick={() => setDurationMode('manual')}>Manual</button>
              </div>
              {durationMode === 'manual' && (
                <>
                  <input type="number" style={{ width: 80 }} min={3} max={600} value={minD} onChange={(e) => setMinD(Number(e.target.value))} />
                  <span className="muted">to</span>
                  <input type="number" style={{ width: 80 }} min={5} max={600} value={maxD} onChange={(e) => setMaxD(Number(e.target.value))} />
                  <span className="muted">sec</span>
                </>
              )}
            </div>
          </div>
        </div>

        <div className="field">
          <span>Long-form clips <span className="faint">(16:9 YouTube episodes of several minutes, with titles, chapters and thumbnail prompts)</span></span>
          <div className="row">
            <div className="segmented">
              <button className={longMode === 'auto' ? 'on' : ''} onClick={() => setLongMode('auto')}>Auto amount</button>
              <button className={longMode === 'manual' ? 'on' : ''} onClick={() => setLongMode('manual')}>Custom</button>
              <button className={longMode === 'off' ? 'on' : ''} onClick={() => setLongMode('off')}>Off</button>
            </div>
            {longMode === 'manual' && (
              <>
                <input type="number" style={{ width: 70 }} min={1} max={20} value={longCount} onChange={(e) => setLongCount(Number(e.target.value))} />
                <span className="muted small">clip(s)</span>
              </>
            )}
            {longMode === 'auto' && <span className="tiny muted">Scales with the source: ~1–2 for 15 min, ~5 for an hour, ~9 for three hours</span>}
          </div>
        </div>

        <div className="stack" style={{ gap: 8 }}>
          <label className="check" style={{ opacity: geminiReady ? 1 : 0.6 }}>
            <input type="checkbox" checked={useGemini && geminiReady} disabled={!geminiReady} onChange={(e) => setUseGemini(e.target.checked)} />
            <span>
              Use Gemini for video understanding
              {!geminiReady && <span className="tiny muted"> — GEMINI_API_KEY not set; analysis runs in offline Demo mode (heuristic, not AI)</span>}
            </span>
          </label>
          <label className="check">
            <input type="checkbox" checked={permission} onChange={(e) => setPermission(e.target.checked)} />
            <span>I have permission to clip and repost this content for this campaign.</span>
          </label>
        </div>

        {busy && (
          <div className="stack" style={{ gap: 6 }}>
            <span className="small muted">
              {folderLink ? `Creating projects… ${Math.round(progress * picked.length)}/${picked.length}`
                : progress < 1 ? `Uploading source… ${Math.round(progress * 100)}%` : 'Validating video…'}
            </span>
            <Bar value={progress} indeterminate={!folderLink && progress >= 1} />
          </div>
        )}
        {error && <div className="banner bad" style={{ margin: 0 }}>{error}</div>}
        {notice && <div className="banner info" style={{ margin: 0 }}>{notice}</div>}

        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn lg" disabled={!canSubmit} onClick={() => submit(false)} title="Transcribe and find ranked candidates for review">
            Analyze Source
          </button>
          <button className="btn primary lg" disabled={!canSubmit} onClick={() => submit(true)} title="Analyze, then automatically render the top clips">
            Generate Clips
          </button>
        </div>
      </div>
    </div>
  )
}

function DriveFolderPicker({ files, busy, error, picked, setPicked }: {
  files: DriveFile[] | null
  busy: boolean
  error: string | null
  picked: string[]
  setPicked: (ids: string[]) => void
}) {
  if (busy) return <div className="small muted">Reading the Google Drive folder…</div>
  if (error) return <div className="banner bad" style={{ margin: 0 }}>{error}</div>
  if (!files) return null
  if (!files.length) {
    return <div className="banner warn" style={{ margin: 0 }}>No videos found in this folder (subfolders are included, two levels deep).</div>
  }
  const all = picked.length === files.length
  const toggle = (id: string) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id])
  return (
    <div className="drive-picker">
      <div className="row between">
        <span className="small muted">{files.length} video(s) in this folder · {picked.length} selected (one project each)</span>
        <button className="btn sm ghost" onClick={() => setPicked(all ? [] : files.map((f) => f.id))}>
          {all ? 'Select none' : 'Select all'}
        </button>
      </div>
      <div className="drive-list">
        {files.map((f) => (
          <label key={f.id} className={`check drive-file ${picked.includes(f.id) ? 'on' : ''}`}>
            <input type="checkbox" checked={picked.includes(f.id)} onChange={() => toggle(f.id)} />
            <span className="drive-name" title={f.name}>
              {f.folder && <span className="faint">{f.folder} / </span>}{f.name}
            </span>
          </label>
        ))}
      </div>
    </div>
  )
}
