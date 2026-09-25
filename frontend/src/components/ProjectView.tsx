import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  api, fmtBytes, fmtTime, PLATFORMS, subscribeJob, uploadForm,
  type Candidate, type GradePreset, type Job, type LongFormClip, type Preferences, type Project, type SettingsResponse,
} from '../api'
import CandidateCard from './CandidateCard'
import { GradePreview, GradeSelect, GradeSliders } from './ColorGrade'
import LongFormCard from './LongFormCard'
import { JobPanel, SegmentPreview, StatusBadge, Toast, Toggle, TriState } from './common'

type SortKey = 'score' | 'duration' | 'start' | 'platform' | 'compliance'
const COMPLIANCE_ORDER: Record<string, number> = { COMPLIANT: 0, WARNING: 1, UNKNOWN: 2, FAILED: 3 }

export default function ProjectView({ projectId, settings }: { projectId: string; settings: SettingsResponse | null }) {
  const [project, setProject] = useState<Project | null>(null)
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [longClips, setLongClips] = useState<LongFormClip[]>([])
  const [tab, setTab] = useState<'shorts' | 'long'>('shorts')
  const [longOpts, setLongOpts] = useState<Partial<Preferences>>({})
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<{ msg: string; kind: 'info' | 'bad' } | null>(null)
  const [sort, setSort] = useState<SortKey>('score')
  const [sortPlatform, setSortPlatform] = useState('tiktok')
  const [showRejected, setShowRejected] = useState(false)
  const [preview, setPreview] = useState<{ start: number; end: number; title: string } | null>(null)
  const [opts, setOpts] = useState<Partial<Preferences>>({})
  const [whisperModel, setWhisperModel] = useState('')
  const lastStatus = useRef<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [drag, setDrag] = useState(false)
  const [permission, setPermission] = useState(false)
  const [link, setLink] = useState('')
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const [p, c, l] = await Promise.all([
        api<Project>(`/api/projects/${projectId}`),
        api<Candidate[]>(`/api/projects/${projectId}/candidates`),
        api<LongFormClip[]>(`/api/projects/${projectId}/long-form`),
      ])
      setProject(p)
      setCandidates(c)
      setLongClips(l)
      setJob((j) => j ?? p.job)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [projectId])

  useEffect(() => {
    load()
    return subscribeJob(projectId, (j) => {
      setJob(j)
      const st = j?.status ?? null
      if (lastStatus.current && lastStatus.current !== st && (st === 'done' || st === 'error')) load()
      lastStatus.current = st
    })
  }, [projectId, load])

  useEffect(() => {
    if (settings) {
      const p = settings.preferences
      setOpts({
        captions: p.captions, caption_style: p.caption_style, smart_reframe: p.smart_reframe, split_screen: p.split_screen,
        silence_removal: p.silence_removal, aggressive_silence: p.aggressive_silence, auto_zoom: p.auto_zoom,
        broll_mode: p.broll_mode, variants: p.variants, hook_seconds: p.hook_seconds,
        sound_design: p.sound_design, sfx_volume: p.sfx_volume, sfx_playful: p.sfx_playful,
        color_grade: p.color_grade, grade_overrides: p.grade_overrides,
      })
      setLongOpts({
        long_form_resolution: p.long_form_resolution, long_form_captions: p.long_form_captions,
        long_form_cold_open: p.long_form_cold_open, long_form_silence_removal: p.long_form_silence_removal,
        long_form_sound_design: p.long_form_sound_design, long_form_color_grade: p.long_form_color_grade,
      })
      setWhisperModel(p.whisper_model)
    }
  }, [settings])

  const running = job?.status === 'running' || job?.status === 'queued'

  const sorted = useMemo(() => {
    const list = candidates.filter((c) => showRejected || !c.rejected)
    const by: Record<SortKey, (a: Candidate, b: Candidate) => number> = {
      score: (a, b) => b.viral_score - a.viral_score,
      duration: (a, b) => a.duration - b.duration,
      start: (a, b) => a.start - b.start,
      platform: (a, b) =>
        ((b.platform_fit as Record<string, number>)[sortPlatform] ?? 0) - ((a.platform_fit as Record<string, number>)[sortPlatform] ?? 0),
      compliance: (a, b) => (COMPLIANCE_ORDER[a.compliance_status] ?? 9) - (COMPLIANCE_ORDER[b.compliance_status] ?? 9) || b.viral_score - a.viral_score,
    }
    return [...list].sort(by[sort])
  }, [candidates, sort, sortPlatform, showRejected])

  const selectedCount = candidates.filter((c) => c.selected && !c.rejected).length
  const rendered = candidates.filter((c) => c.exports.length > 0)

  const updateCandidate = (c: Candidate) => setCandidates((list) => list.map((x) => (x.id === c.id ? c : x)))

  const startAnalysis = async (forceYoutube = false) => {
    try {
      const j = await api<Job>(`/api/projects/${projectId}/analyze`, {
        method: 'POST', json: { whisper_model: whisperModel, force_youtube: forceYoutube },
      })
      setJob(j)
      lastStatus.current = j.status
    } catch (e) {
      setToast({ msg: (e as Error).message, kind: 'bad' })
    }
  }

  const startRender = async (ids: string[] | null) => {
    try {
      const j = await api<Job>(`/api/projects/${projectId}/render`, { method: 'POST', json: { candidate_ids: ids, options: opts } })
      setJob(j)
      lastStatus.current = j.status
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (e) {
      setToast({ msg: (e as Error).message, kind: 'bad' })
    }
  }

  const updateLong = (c: LongFormClip) => setLongClips((list) => list.map((x) => (x.id === c.id ? c : x)))

  const findLongForm = async () => {
    try {
      const j = await api<Job>(`/api/projects/${projectId}/long-form/analyze`, { method: 'POST' })
      setJob(j)
      lastStatus.current = j.status
    } catch (e) {
      setToast({ msg: (e as Error).message, kind: 'bad' })
    }
  }

  const renderLong = async (ids: string[] | null) => {
    try {
      const j = await api<Job>(`/api/projects/${projectId}/long-form/render`, { method: 'POST', json: { candidate_ids: ids, options: longOpts } })
      setJob(j)
      lastStatus.current = j.status
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (e) {
      setToast({ msg: (e as Error).message, kind: 'bad' })
    }
  }

  const uploadSource = async () => {
    if (!file && !link.trim()) return
    const fd = new FormData()
    if (file) fd.append('file', file)
    else fd.append('source_url', link.trim())
    fd.append('permission_confirmed', String(permission))
    setUploadProgress(0)
    try {
      setProject(await uploadForm(`/api/projects/${projectId}/source`, fd, setUploadProgress))
      setFile(null)
      setLink('')
      await startAnalysis()
    } catch (e) {
      setToast({ msg: (e as Error).message, kind: 'bad' })
    } finally {
      setUploadProgress(null)
    }
  }

  const setAllSelected = async (value: boolean) => {
    const targets = candidates.filter((c) => !c.rejected && c.selected !== value)
    for (const c of targets) {
      const updated = await api<Candidate>(`/api/candidates/${c.id}`, { method: 'PATCH', json: { selected: value } })
      updateCandidate(updated)
    }
  }

  const cancelRetry = async () => {
    try {
      setProject(await api<Project>(`/api/projects/${projectId}/youtube-retry`, { method: 'DELETE' }))
    } catch (e) {
      setToast({ msg: (e as Error).message, kind: 'bad' })
    }
  }

  const openFolder = (target: string) =>
    api(`/api/projects/${projectId}/open-folder`, { method: 'POST', json: { target } }).catch((e) =>
      setToast({ msg: (e as Error).message, kind: 'bad' }))

  const deleteProject = async () => {
    if (!window.confirm('Delete this project and all of its files (source copy, renders, exports)? This cannot be undone.')) return
    await api(`/api/projects/${projectId}`, { method: 'DELETE' })
    window.location.hash = '#/'
  }

  if (error) return <div className="banner bad">{error}</div>
  if (!project) return <div className="empty">Loading project…</div>

  const rules = project.campaign_rules
  const probe = project.source?.probe
  const isDemo = project.analysis?.provider === 'demo'
  const grades = settings?.color_grades ?? {}
  // frame for the look preview: just into the first clip that will be rendered, else 30 % into the source
  const previewCand = candidates.find((c) => c.selected && !c.rejected) ?? candidates.find((c) => !c.rejected)
  const previewT = previewCand ? previewCand.start + Math.min(1.5, previewCand.duration / 3) : (probe?.duration ?? 0) * 0.3

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="row between">
        <div>
          <div className="row">
            <h1>{project.name}</h1>
            <StatusBadge status={project.status} />
            {isDemo && <span className="badge warn" title="Offline heuristic analysis, not AI">DEMO ANALYSIS</span>}
          </div>
          <div className="muted small">{project.campaign_name || 'No campaign'} · {project.platforms.map((p) => PLATFORMS.find((x) => x.key === p)?.label ?? p).join(', ')}</div>
        </div>
        <div className="row">
          <select value={whisperModel} onChange={(e) => setWhisperModel(e.target.value)} style={{ width: 'auto' }} title="Whisper model">
            {(settings?.whisper_models ?? ['tiny', 'base', 'small', 'medium']).map((m) => <option key={m} value={m}>Whisper {m}</option>)}
          </select>
          <button className="btn" disabled={running} onClick={() => startAnalysis()}>{candidates.length ? 'Re-analyze' : 'Analyze Source'}</button>
          <button className="btn" onClick={() => openFolder('ready_to_post')} title={project.paths.ready_to_post}>Open READY_TO_POST</button>
          <button className="btn ghost danger" disabled={running} onClick={deleteProject}>Delete</button>
        </div>
      </div>

      {project.error && !running && <div className="banner bad" style={{ margin: 0 }}><b>Last error:</b> {project.error}</div>}
      {project.status === 'waiting' && project.youtube_retry && !running && (
        <div className="banner warn" style={{ margin: 0, flexDirection: 'column' }}>
          <span>
            <b>Waiting for YouTube.</b> YouTube is blocking downloads from this connection for now. ClipForge will try again
            automatically{project.youtube_retry.retry_at ? ` at ${new Date(project.youtube_retry.retry_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ' shortly'}
            {project.youtube_retry.attempts > 1 ? ` (blocked ${project.youtube_retry.attempts} times so far)` : ''} and
            start the analysis as soon as the download works. Keep ClipForge running. You can also upload the file or paste a
            Drive/Dropbox link below.
          </span>
          <div className="row">
            <button className="btn sm" onClick={() => startAnalysis(true)} title="Sends one request to YouTube now">Try now</button>
            <button className="btn sm ghost" onClick={cancelRetry}>Cancel auto-retry</button>
          </div>
        </div>
      )}

      <div className="grid-side">
        <div className="stack">
          {job && <JobPanel job={job} />}

          <div className="card">
            <div className="card-title"><h2>Source</h2><span className="sub">{project.source?.origin}</span></div>
            {project.source?.preview_url && probe && (
              <video src={project.source.preview_url} controls preload="metadata" style={{ width: '100%', borderRadius: 10, background: '#000', marginBottom: 12 }} />
            )}
            {!project.source?.preview_url && !running && (
              <div className="stack" style={{ gap: 10, marginBottom: 12 }}>
                <p className="small muted" style={{ margin: 0 }}>
                  No video file yet. Upload it, or paste a Google Drive / Dropbox link shared with "Anyone with the link",
                  to analyze this project with its current name, rules and settings.
                </p>
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
                <input type="url" value={link} disabled={!!file} placeholder="…or a Google Drive / Dropbox link"
                  onChange={(e) => setLink(e.target.value)} />
                <label className="check small">
                  <input type="checkbox" checked={permission} onChange={(e) => setPermission(e.target.checked)} />
                  I have permission to clip and repost this content for this campaign.
                </label>
                <button className="btn primary" disabled={(!file && !link.trim()) || !permission || uploadProgress !== null} onClick={uploadSource}>
                  {uploadProgress !== null ? (file ? `Uploading ${Math.round(uploadProgress * 100)}%` : 'Saving link…') : file ? 'Upload & analyze' : 'Import & analyze'}
                </button>
              </div>
            )}
            <dl className="kv">
              <dt>File</dt><dd>{project.source?.filename}</dd>
              {probe && <>
                <dt>Duration</dt><dd>{fmtTime(probe.duration)}</dd>
                <dt>Resolution</dt><dd>{probe.display_width}×{probe.display_height} @ {probe.fps}fps</dd>
                <dt>Codecs</dt><dd>{probe.video_codec} / {probe.audio_codec ?? 'no audio'}{probe.audio_streams > 1 ? ` (${probe.audio_streams} tracks)` : ''}</dd>
                <dt>Size</dt><dd>{fmtBytes(probe.size_bytes)} · {Math.round(probe.bitrate / 1000)} kbps</dd>
              </>}
              {project.source?.sha256 && <><dt>SHA-256</dt><dd className="mono">{project.source.sha256.slice(0, 16)}…</dd></>}
            </dl>
          </div>

          <div className="card">
            <div className="card-title"><h2>API usage</h2><span className="sub">{project.gemini.configured ? project.gemini.model : 'not configured'}</span></div>
            <div className="stats">
              <div className="stat"><b>{project.usage.gemini_requests}</b><span>Gemini requests</span></div>
              <div className="stat"><b>{project.usage.video_uploads}</b><span>Video uploads</span></div>
              <div className="stat"><b>{project.usage.cached_requests}</b><span>Cached (free) reuses</span></div>
              <div className="stat"><b>{(project.usage.input_tokens / 1000).toFixed(1)}k</b><span>Input tokens · {(project.usage.output_tokens / 1000).toFixed(1)}k out</span></div>
            </div>
            <label className="check small" style={{ marginTop: 12 }}>
              <input type="checkbox" checked={project.use_gemini} disabled={running || !project.gemini.configured}
                onChange={async (e) => setProject(await api<Project>(`/api/projects/${projectId}`, { method: 'PATCH', json: { use_gemini: e.target.checked } }))} />
              Use Gemini for this project {!project.gemini.configured && <span className="muted">(set GEMINI_API_KEY in .env)</span>}
            </label>
          </div>

          <div className="card">
            <div className="card-title">
              <h2>Campaign rules</h2>
              <span className="sub">{project.campaign_rules_source ? `parsed: ${project.campaign_rules_source}` : 'not parsed yet'}</span>
            </div>
            {!rules ? (
              <p className="muted small">Rules are parsed during analysis.</p>
            ) : (
              <div className="stack" style={{ gap: 10 }}>
                <dl className="kv">
                  <dt>Duration</dt><dd>{rules.min_duration ?? '?'}s – {rules.max_duration ?? '?'}s</dd>
                  <dt>Platforms</dt><dd>{rules.platforms.length ? rules.platforms.join(', ') : <span className="faint">unknown</span>}</dd>
                  <dt>Hashtags</dt><dd>{rules.required_hashtags.map((h) => <span key={h} className="chip">{h}</span>)}{!rules.required_hashtags.length && <span className="faint">none stated</span>}</dd>
                  <dt>Mentions</dt><dd>{rules.required_mentions.map((h) => <span key={h} className="chip">{h}</span>)}{!rules.required_mentions.length && <span className="faint">none stated</span>}</dd>
                  <dt>CTA</dt><dd>{rules.required_cta ?? <span className="faint">none stated</span>}</dd>
                  <dt>Captions</dt><dd><TriState value={rules.captions_allowed} /></dd>
                  <dt>B-roll</dt><dd><TriState value={rules.broll_allowed} /></dd>
                  <dt>Split screen</dt><dd><TriState value={rules.split_screen_allowed} /></dd>
                  <dt>Music</dt><dd><TriState value={rules.music_allowed} /></dd>
                </dl>
                {[['Prohibited', rules.prohibited_content], ['Source edits', rules.source_modification_rules], ['Special', rules.special_requirements]].map(([label, items]) =>
                  (items as string[]).length ? (
                    <div key={label as string}>
                      <div className="label">{label as string}</div>
                      <ul className="small" style={{ margin: '4px 0 0', paddingLeft: 18 }}>{(items as string[]).map((x) => <li key={x}>{x}</li>)}</ul>
                    </div>
                  ) : null)}
                <details>
                  <summary>Original rules text & JSON</summary>
                  <pre className="copy-block" style={{ marginTop: 8 }}>{project.campaign_rules_text || '(none)'}</pre>
                  <pre className="copy-block mono" style={{ marginTop: 8, maxHeight: 260, overflow: 'auto' }}>{JSON.stringify(rules, null, 2)}</pre>
                </details>
              </div>
            )}
          </div>

          {project.analysis && (
            <div className="card">
              <div className="card-title"><h2>Video understanding</h2><span className="sub">{project.analysis.provider}</span></div>
              <p className="small" style={{ marginTop: 0 }}>{project.analysis.summary}</p>
              {project.analysis.main_topics?.length ? <div>{project.analysis.main_topics.map((t) => <span key={t} className="chip">{t}</span>)}</div> : null}
              <p className="tiny muted">
                {project.analysis.raw_candidates} moments proposed · {project.analysis.kept_candidates} kept
                {project.analysis.duplicates_removed?.length ? ` · ${project.analysis.duplicates_removed.length} duplicate(s) removed` : ''}
              </p>
              {project.analysis.duration_notes?.map((n) => <p key={n} className="tiny muted">{n}</p>)}
            </div>
          )}
        </div>

        <div>
          <div className="tabs">
            <button className={tab === 'shorts' ? 'on' : ''} onClick={() => setTab('shorts')}>Shorts <span className="count">{candidates.filter((c) => !c.rejected).length}</span></button>
            <button className={tab === 'long' ? 'on' : ''} onClick={() => setTab('long')}>Long-form <span className="count">{longClips.filter((c) => !c.rejected).length}</span></button>
          </div>
          {tab === 'long' ? (
            <LongFormPanel project={project} setProject={setProject} clips={longClips} running={running} opts={longOpts} setOpts={setLongOpts}
              grades={grades} onFind={findLongForm} onRender={renderLong} onChange={updateLong}
              onPreview={(start, end, title) => setPreview({ start, end, title })}
              onError={(msg) => setToast({ msg, kind: 'bad' })} />
          ) : candidates.length === 0 ? (
            <div className="card empty">
              <div className="big">🔎</div>
              {running ? 'Analysis in progress — candidates will appear here.' : 'No candidates yet. Run "Analyze Source".'}
            </div>
          ) : (
            <>
              <div className="card tight" style={{ marginBottom: 14 }}>
                <div className="row between" style={{ marginBottom: 10 }}>
                  <h2>Render options</h2>
                  <span className="small muted">Defaults come from Settings</span>
                </div>
                <div className="settings-grid" style={{ gap: 10 }}>
                  <label className="field">
                    <span>Caption style</span>
                    <select value={opts.caption_style} onChange={(e) => setOpts({ ...opts, caption_style: e.target.value })}>
                      <option value="bold_viral">Bold Viral</option>
                      <option value="clean">Clean</option>
                      <option value="minimal">Minimal</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Hook overlay seconds (0 = whole clip)</span>
                    <input type="number" min={0} max={15} step={0.5} value={opts.hook_seconds ?? 3.5} onChange={(e) => setOpts({ ...opts, hook_seconds: Number(e.target.value) })} />
                  </label>
                  <div className="field">
                    <span>Variants</span>
                    <div className="row" style={{ gap: 12 }}>
                      {[['A', 'Hook'], ['B', 'Alt hook'], ['C', 'Captions only']].map(([v, l]) => (
                        <label key={v} className="check small">
                          <input type="checkbox" checked={opts.variants?.includes(v) ?? false}
                            onChange={(e) => setOpts({ ...opts, variants: e.target.checked ? [...(opts.variants ?? []), v] : (opts.variants ?? []).filter((x) => x !== v) })} />
                          {v}: {l}
                        </label>
                      ))}
                    </div>
                  </div>
                  <Toggle label="Burn-in captions" checked={!!opts.captions} onChange={(v) => setOpts({ ...opts, captions: v })} />
                  <Toggle label="Smart speaker framing" checked={!!opts.smart_reframe} onChange={(v) => setOpts({ ...opts, smart_reframe: v })} />
                  <Toggle label="Smart silence removal" checked={!!opts.silence_removal} onChange={(v) => setOpts({ ...opts, silence_removal: v })} />
                  <Toggle label="Aggressive editing" hint="Also trims dramatic pauses" checked={!!opts.aggressive_silence} onChange={(v) => setOpts({ ...opts, aggressive_silence: v })} />
                  <Toggle label="Subtle auto punch-ins" checked={!!opts.auto_zoom} onChange={(v) => setOpts({ ...opts, auto_zoom: v })} />
                  <Toggle label="Split screen (only if campaign allows)" checked={!!opts.split_screen} onChange={(v) => setOpts({ ...opts, split_screen: v })} />
                  <Toggle label="Local B-roll" hint="From workspace/broll/ only" checked={opts.broll_mode === 'local'} onChange={(v) => setOpts({ ...opts, broll_mode: v ? 'local' : 'off' })} />
                  <label className="field">
                    <span>Sound design</span>
                    <select value={opts.sound_design ?? 'balanced'} onChange={(e) => setOpts({ ...opts, sound_design: e.target.value as Preferences['sound_design'] })}>
                      <option value="off">Off</option>
                      <option value="subtle">Subtle (hook + key moments)</option>
                      <option value="balanced">Balanced (+ punch-ins, emphasis pops)</option>
                      <option value="punchy">Punchy (+ jump-cut swishes)</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Effects volume: {Math.round((opts.sfx_volume ?? 1) * 100)}%</span>
                    <input type="range" min={0} max={2} step={0.05} value={opts.sfx_volume ?? 1} disabled={opts.sound_design === 'off'}
                      onChange={(e) => setOpts({ ...opts, sfx_volume: Number(e.target.value) })} />
                  </label>
                  <Toggle label="Playful sounds" hint="Comedic / crowd effects, only on clips the AI reads as funny" checked={!!opts.sfx_playful}
                    disabled={opts.sound_design === 'off'} onChange={(v) => setOpts({ ...opts, sfx_playful: v })} />
                  <GradeSelect value={opts.color_grade ?? 'none'} grades={grades} onChange={(v) => setOpts({ ...opts, color_grade: v })} />
                </div>
                <details style={{ marginTop: 10 }}>
                  <summary>Look: preview &amp; fine-tune the colour grade{Object.keys(opts.grade_overrides ?? {}).length ? ' (edited)' : ''}</summary>
                  <div className="stack" style={{ gap: 12, marginTop: 10 }}>
                    {project.source?.preview_url && (
                      <GradePreview projectId={projectId} t={previewT} grade={opts.color_grade ?? 'none'} overrides={opts.grade_overrides ?? {}} />
                    )}
                    <GradeSliders grade={opts.color_grade ?? 'none'} overrides={opts.grade_overrides ?? {}} grades={grades}
                      onChange={(o) => setOpts({ ...opts, grade_overrides: o })} />
                  </div>
                </details>
              </div>

              <div className="card toolbar">
                <span className="label">Sort</span>
                <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
                  <option value="score">Viral score</option>
                  <option value="duration">Duration</option>
                  <option value="start">Start time</option>
                  <option value="platform">Platform fit</option>
                  <option value="compliance">Campaign compliance</option>
                </select>
                {sort === 'platform' && (
                  <select value={sortPlatform} onChange={(e) => setSortPlatform(e.target.value)}>
                    {PLATFORMS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                  </select>
                )}
                <label className="check small"><input type="checkbox" checked={showRejected} onChange={(e) => setShowRejected(e.target.checked)} /> Show rejected</label>
                <button className="btn sm ghost" onClick={() => setAllSelected(true)}>Select all</button>
                <button className="btn sm ghost" onClick={() => setAllSelected(false)}>Clear</button>
                <div className="spacer grow" />
                <span className="small muted">{selectedCount} selected</span>
                <button className="btn primary" disabled={running || selectedCount === 0} onClick={() => startRender(null)}>
                  Generate {selectedCount} clip{selectedCount === 1 ? '' : 's'}
                </button>
              </div>

              {rendered.length > 0 && (
                <div className="banner info">
                  <b>{rendered.length} clip(s) rendered.</b>
                  <span className="grow small">Exports: <code>{project.paths.exports}</code><br />Ready to post: <code>{project.paths.ready_to_post}</code></span>
                  <button className="btn sm" onClick={() => openFolder('ready_to_post')}>Open folder</button>
                </div>
              )}

              {sorted.map((c) => (
                <CandidateCard key={c.id} candidate={c} disabled={running} onChange={updateCandidate}
                  onPreview={(start, end, title) => setPreview({ start, end, title })}
                  onRender={() => startRender([c.id])}
                  onError={(msg) => setToast({ msg, kind: 'bad' })} />
              ))}
            </>
          )}
        </div>
      </div>

      {preview && project.source?.preview_url && (
        <SegmentPreview src={project.source.preview_url} start={preview.start} end={preview.end} title={preview.title} onClose={() => setPreview(null)} />
      )}
      {toast && <Toast message={toast.msg} kind={toast.kind} onDone={() => setToast(null)} />}
    </div>
  )
}


function LongFormPanel({ project, setProject, clips, running, opts, setOpts, grades, onFind, onRender, onChange, onPreview, onError }: {
  project: Project
  setProject: (p: Project) => void
  clips: LongFormClip[]
  running: boolean
  opts: Partial<Preferences>
  setOpts: (o: Partial<Preferences>) => void
  grades: Record<string, GradePreset>
  onFind: () => void
  onRender: (ids: string[] | null) => void
  onChange: (c: LongFormClip) => void
  onPreview: (start: number, end: number, title: string) => void
  onError: (msg: string) => void
}) {
  const info = project.analysis?.long_form
  const visible = clips.filter((c) => !c.rejected)
  const selected = visible.filter((c) => c.selected).length
  const analysed = !!project.analysis
  const updateMode = async (mode: string, count?: number) => {
    try {
      setProject(await api<Project>(`/api/projects/${project.id}`, {
        method: 'PATCH', json: { long_form_mode: mode, long_form_count: count ?? project.long_form_count },
      }))
      if (mode !== 'off') onFind()
    } catch (e) {
      onError((e as Error).message)
    }
  }
  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="card tight">
        <div className="row between" style={{ marginBottom: 8 }}>
          <h2>Long-form clips</h2>
          <span className="small muted">16:9 YouTube episodes with titles, chapters and thumbnail prompts</span>
        </div>
        {info && <p className="small" style={{ marginTop: 0 }}><b>Right amount for this source:</b> {info.note} {info.kept ? `${info.kept} found, ${info.selected} recommended (score ≥ 50).` : ''}</p>}
        <div className="row" style={{ gap: 10, marginBottom: 10 }}>
          <div className="segmented">
            {(['auto', 'manual', 'off'] as const).map((m) => (
              <button key={m} className={project.long_form_mode === m ? 'on' : ''} disabled={running || !analysed}
                onClick={() => updateMode(m, m === 'manual' ? Math.max(1, project.long_form_count || info?.target || 3) : undefined)}>
                {m === 'auto' ? 'Auto amount' : m === 'manual' ? 'Custom' : 'Off'}
              </button>
            ))}
          </div>
          {project.long_form_mode === 'manual' && (
            <input type="number" min={1} max={20} style={{ width: 70 }} defaultValue={project.long_form_count || 3}
              onBlur={(e) => Number(e.target.value) !== project.long_form_count && updateMode('manual', Number(e.target.value))} />
          )}
          <button className="btn sm" disabled={running || !analysed} onClick={onFind}
            title={project.use_gemini ? 'One text-only Gemini request (transcript + earlier video understanding)' : 'Offline heuristics'}>
            {clips.length ? 'Re-find long-form clips' : 'Find long-form clips'}
          </button>
        </div>
        <div className="settings-grid" style={{ gap: 10 }}>
          <label className="field">
            <span>Resolution</span>
            <select value={opts.long_form_resolution} onChange={(e) => setOpts({ ...opts, long_form_resolution: e.target.value })}>
              <option value="1920x1080">1920×1080</option>
              <option value="1280x720">1280×720 (faster)</option>
            </select>
          </label>
          <label className="field">
            <span>Sound design</span>
            <select value={opts.long_form_sound_design} onChange={(e) => setOpts({ ...opts, long_form_sound_design: e.target.value as Preferences['long_form_sound_design'] })}>
              <option value="off">Off</option>
              <option value="subtle">Subtle (cold open + chapters)</option>
              <option value="balanced">Balanced (+ ~1 accent/min)</option>
              <option value="punchy">Punchy (+ ~2 accents/min)</option>
            </select>
          </label>
          <GradeSelect value={opts.long_form_color_grade ?? 'none'} grades={grades} onChange={(v) => setOpts({ ...opts, long_form_color_grade: v })} />
          <Toggle label="Cold open" hint="Start with the strongest 5–15 s line from inside the clip" checked={!!opts.long_form_cold_open} onChange={(v) => setOpts({ ...opts, long_form_cold_open: v })} />
          <Toggle label="Remove dead air" checked={!!opts.long_form_silence_removal} onChange={(v) => setOpts({ ...opts, long_form_silence_removal: v })} />
          <Toggle label="Burn-in captions" hint="Lower-third subtitles (slower); an SRT is always exported" checked={!!opts.long_form_captions} onChange={(v) => setOpts({ ...opts, long_form_captions: v })} />
        </div>
      </div>

      {clips.length === 0 ? (
        <div className="card empty">
          <div className="big">🎬</div>
          {!analysed ? 'Analyze the source first.' : project.long_form_mode === 'off' ? 'Long-form clips are off for this project.'
            : info?.note && !info.kept ? info.note : 'No long-form clips yet. Click "Find long-form clips".'}
        </div>
      ) : (
        <>
          <div className="card toolbar">
            <span className="small muted">{selected} selected</span>
            <div className="spacer grow" />
            <button className="btn primary" disabled={running || selected === 0} onClick={() => onRender(null)}>
              Render {selected} long-form clip{selected === 1 ? '' : 's'}
            </button>
          </div>
          {visible.concat(clips.filter((c) => c.rejected)).map((c) => (
            <LongFormCard key={c.id} clip={c} disabled={running} onChange={onChange} onPreview={onPreview}
              onRender={() => onRender([c.id])} onError={onError} />
          ))}
        </>
      )}
    </div>
  )
}
