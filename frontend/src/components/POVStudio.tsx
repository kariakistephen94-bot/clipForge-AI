import { useEffect, useRef, useState } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface POVScene {
  index: number
  narration: string
  image_prompt: string
  has_image?: boolean
  image_url?: string
}

interface JobStatus {
  job_id: string
  status: 'queued' | 'running' | 'done' | 'error'
  stage: string
  progress: number
  error: string | null
  output: string | null
  title?: string
  scenes?: POVScene[]
}

type Style = 'cinematic' | 'vlog' | 'documentary'
type Voice = 'onyx' | 'alloy' | 'echo' | 'nova' | 'shimmer' | 'fable'

const STYLES: { id: Style; label: string; icon: string; desc: string }[] = [
  { id: 'cinematic', label: 'Cinematic', icon: '🎬', desc: 'Dramatic, moody storytelling with rich visuals' },
  { id: 'vlog',      label: 'Vlog',      icon: '📱', desc: 'Casual, authentic first-person perspective' },
  { id: 'documentary', label: 'Documentary', icon: '🎥', desc: 'Factual, informative and compelling narrative' },
]

const VOICES: { id: Voice; label: string; desc: string }[] = [
  { id: 'onyx',    label: 'Onyx',    desc: 'Deep & cinematic' },
  { id: 'alloy',   label: 'Alloy',   desc: 'Neutral & clear' },
  { id: 'echo',    label: 'Echo',    desc: 'Warm & smooth' },
  { id: 'nova',    label: 'Nova',    desc: 'Bright & energetic' },
  { id: 'shimmer', label: 'Shimmer', desc: 'Soft & gentle' },
  { id: 'fable',   label: 'Fable',   desc: 'Expressive & rich' },
]

const STAGE_LABELS: Record<string, { label: string; icon: string }> = {
  scripting:   { label: 'Writing script with GPT-4o…', icon: '✍️' },
  generating:  { label: 'Generating narration & images…', icon: '🎨' },
  rendering:   { label: 'Rendering video scenes…', icon: '🎬' },
  finalizing:  { label: 'Combining final video…', icon: '⚙️' },
  done:        { label: 'Video ready!', icon: '✅' },
  queued:      { label: 'Queued…', icon: '⏳' },
}

const DURATIONS = [15, 30, 60]

const BASE = import.meta.env.DEV ? 'http://127.0.0.1:8765' : ''

// ── Helpers ───────────────────────────────────────────────────────────────────

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error((err as { detail?: string }).detail || res.statusText)
  }
  return res.json()
}

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error((err as { detail?: string }).detail || res.statusText)
  }
  return res.json()
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function POVStudio() {
  const [topic, setTopic] = useState('')
  const [style, setStyle] = useState<Style>('cinematic')
  const [duration, setDuration] = useState(30)
  const [voice, setVoice] = useState<Voice>('onyx')
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const [selectedImage, setSelectedImage] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const videoRef = useRef<HTMLVideoElement>(null)

  // ── Polling ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!jobId) return
    const poll = async () => {
      try {
        const status = await apiGet<JobStatus>(`/api/pov/${jobId}/status`)
        setJobStatus(status)
        if (status.status === 'done') {
          clearInterval(pollRef.current!)
          setVideoUrl(`${BASE}/api/pov/${jobId}/download`)
          setGenerating(false)
        } else if (status.status === 'error') {
          clearInterval(pollRef.current!)
          setError(status.error || 'An unknown error occurred')
          setGenerating(false)
        }
      } catch (e: unknown) {
        setError((e as Error).message)
        clearInterval(pollRef.current!)
        setGenerating(false)
      }
    }
    pollRef.current = setInterval(poll, 1500)
    return () => clearInterval(pollRef.current!)
  }, [jobId])

  // Auto-load video preview when ready
  useEffect(() => {
    if (videoUrl && videoRef.current) {
      videoRef.current.load()
    }
  }, [videoUrl])

  // ── Handlers ─────────────────────────────────────────────────────────────────
  const handleGenerate = async () => {
    if (!topic.trim()) return
    setError(null)
    setVideoUrl(null)
    setJobStatus(null)
    setGenerating(true)
    try {
      const res = await apiPost<{ job_id: string }>('/api/pov/generate', {
        topic: topic.trim(),
        style,
        duration_s: duration,
        voice,
      })
      setJobId(res.job_id)
    } catch (e: unknown) {
      setError((e as Error).message)
      setGenerating(false)
    }
  }

  const handleDownload = () => {
    if (!videoUrl || !jobId) return
    const a = document.createElement('a')
    a.href = videoUrl
    a.download = `pov_video_${jobId.slice(0, 8)}.mp4`
    a.click()
  }

  const handleReset = () => {
    clearInterval(pollRef.current!)
    setJobId(null)
    setJobStatus(null)
    setVideoUrl(null)
    setError(null)
    setGenerating(false)
  }

  const stageInfo = jobStatus ? (STAGE_LABELS[jobStatus.stage] ?? { label: 'Processing…', icon: '⚙️' }) : null
  const progressPct = jobStatus ? Math.round((jobStatus.progress ?? 0) * 100) : 0

  return (
    <div className="pov-studio">
      {/* ── Header ── */}
      <div className="pov-header">
        <div className="pov-header-icon">🎬</div>
        <div>
          <h1>POV Video Studio</h1>
          <p className="pov-header-sub">
            Describe a scene — AI writes the script, records narration, generates images &amp; renders a vertical MP4 ready to download.
          </p>
        </div>
      </div>

      <div className="pov-layout">
        {/* ── Left panel: controls ── */}
        <div className="pov-controls card">
          {/* Topic */}
          <div className="pov-section">
            <label className="pov-label" htmlFor="pov-topic">
              <span>📝</span> What's the POV about?
            </label>
            <textarea
              id="pov-topic"
              className="pov-textarea"
              placeholder="e.g. Exploring an abandoned lighthouse at midnight…"
              value={topic}
              onChange={e => setTopic(e.target.value)}
              rows={3}
              maxLength={500}
              disabled={generating}
            />
            <div className="pov-char-count">{topic.length}/500</div>
          </div>

          {/* Style */}
          <div className="pov-section">
            <div className="pov-label"><span>🎭</span> Style</div>
            <div className="pov-style-grid">
              {STYLES.map(s => (
                <button
                  key={s.id}
                  className={`pov-style-card ${style === s.id ? 'active' : ''}`}
                  onClick={() => setStyle(s.id)}
                  disabled={generating}
                >
                  <span className="pov-style-icon">{s.icon}</span>
                  <span className="pov-style-label">{s.label}</span>
                  <span className="pov-style-desc">{s.desc}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Duration */}
          <div className="pov-section">
            <div className="pov-label"><span>⏱️</span> Duration</div>
            <div className="pov-duration-row">
              {DURATIONS.map(d => (
                <button
                  key={d}
                  className={`pov-duration-btn ${duration === d ? 'active' : ''}`}
                  onClick={() => setDuration(d)}
                  disabled={generating}
                >
                  {d}s
                </button>
              ))}
            </div>
          </div>

          {/* Voice */}
          <div className="pov-section">
            <div className="pov-label"><span>🔊</span> Narrator Voice</div>
            <div className="pov-voice-grid">
              {VOICES.map(v => (
                <button
                  key={v.id}
                  className={`pov-voice-card ${voice === v.id ? 'active' : ''}`}
                  onClick={() => setVoice(v.id)}
                  disabled={generating}
                >
                  <span className="pov-voice-name">{v.label}</span>
                  <span className="pov-voice-desc">{v.desc}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Generate button */}
          <button
            id="pov-generate-btn"
            className={`pov-generate-btn ${generating ? 'loading' : ''}`}
            onClick={handleGenerate}
            disabled={generating || !topic.trim()}
          >
            {generating ? (
              <>
                <span className="pov-spinner" />
                Generating…
              </>
            ) : (
              <>✨ Generate POV Video</>
            )}
          </button>
        </div>

        {/* ── Right panel: progress + output ── */}
        <div className="pov-output-panel">
          {/* Idle state */}
          {!generating && !videoUrl && !error && (
            <div className="pov-idle card">
              <div className="pov-idle-graphic">
                <div className="pov-idle-ring ring-1" />
                <div className="pov-idle-ring ring-2" />
                <div className="pov-idle-ring ring-3" />
                <span className="pov-idle-icon">🎬</span>
              </div>
              <h3>Your video will appear here</h3>
              <p>Fill in the form and click <strong>Generate POV Video</strong> to start. The full pipeline takes 1–3 minutes depending on video length.</p>
              <div className="pov-idle-steps">
                <div className="pov-idle-step"><span>✍️</span> GPT-4o writes your script</div>
                <div className="pov-idle-step"><span>🎨</span> AI generates vertical scene images</div>
                <div className="pov-idle-step"><span>🔊</span> OpenAI TTS records the narration</div>
                <div className="pov-idle-step"><span>🎬</span> FFmpeg renders the final MP4</div>
              </div>
            </div>
          )}

          {/* Progress card */}
          {generating && jobStatus && (
            <div className="pov-progress-card card">
              <div className="pov-progress-header">
                <span className="pov-progress-stage-icon">{stageInfo?.icon}</span>
                <span className="pov-progress-stage-label">{stageInfo?.label}</span>
              </div>
              <div className="pov-progress-bar-track">
                <div
                  className="pov-progress-bar-fill"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
              <div className="pov-progress-pct">{progressPct}%</div>

              <div className="pov-pipeline-steps">
                {[
                  { key: 'scripting',  icon: '✍️', label: 'Script' },
                  { key: 'generating', icon: '🎨', label: 'Assets' },
                  { key: 'rendering',  icon: '🎬', label: 'Render' },
                  { key: 'finalizing', icon: '⚙️', label: 'Finalize' },
                ].map((step, i, arr) => {
                  const stageOrder = ['scripting', 'generating', 'rendering', 'finalizing', 'done']
                  const currentIdx = stageOrder.indexOf(jobStatus.stage)
                  const stepIdx = stageOrder.indexOf(step.key)
                  const isDone = currentIdx > stepIdx
                  const isActive = currentIdx === stepIdx
                  return (
                    <div key={step.key} className="pov-pipe-step-wrap">
                      <div className={`pov-pipe-step ${isDone ? 'done' : isActive ? 'active' : 'pending'}`}>
                        <span className="pov-pipe-icon">{isDone ? '✅' : step.icon}</span>
                        <span className="pov-pipe-label">{step.label}</span>
                      </div>
                      {i < arr.length - 1 && <div className={`pov-pipe-arrow ${isDone ? 'done' : ''}`}>→</div>}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Queued (before first status response) */}
          {generating && !jobStatus && (
            <div className="pov-progress-card card">
              <div className="pov-progress-header">
                <span className="pov-spinner" />
                <span className="pov-progress-stage-label">Starting pipeline…</span>
              </div>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="pov-error card">
              <div className="pov-error-icon">❌</div>
              <h3>Generation Failed</h3>
              <p className="pov-error-msg">{error}</p>
              <button className="btn-sm" onClick={handleReset}>Try Again</button>
            </div>
          )}

          {/* Video output */}
          {videoUrl && (
            <div className="pov-video-card card">
              <div className="pov-video-header">
                <span className="pov-done-badge">✅ Ready</span>
                <h3>Your POV Video</h3>
              </div>
              <div className="pov-video-player-wrap">
                <video
                  ref={videoRef}
                  className="pov-video-player"
                  controls
                  src={videoUrl}
                  playsInline
                />
              </div>
              <div className="pov-video-actions">
                <button id="pov-download-btn" className="pov-download-btn" onClick={handleDownload}>
                  ⬇️ Download MP4
                </button>
                <button className="btn-sm" onClick={handleReset}>
                  ✨ Create Another
                </button>
              </div>
            </div>
          )}

          {/* Generated Scenes Storyboard Gallery */}
          {jobStatus?.scenes && jobStatus.scenes.length > 0 && (
            <div className="pov-storyboard card">
              <div className="pov-storyboard-header">
                <div className="pov-storyboard-title">
                  <span>🎬</span> {jobStatus.title || 'Scene Storyboard'} ({jobStatus.scenes.length} scenes)
                </div>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>Click image to enlarge</span>
              </div>
              <div className="pov-storyboard-grid">
                {jobStatus.scenes.map(s => (
                  <div
                    key={s.index}
                    className="pov-scene-card"
                    onClick={() => s.has_image && setSelectedImage(`${BASE}${s.image_url}`)}
                  >
                    <div className="pov-scene-img-wrap">
                      {s.has_image ? (
                        <img
                          src={`${BASE}${s.image_url}`}
                          alt={`Scene ${s.index + 1}`}
                          className="pov-scene-img"
                          loading="lazy"
                        />
                      ) : (
                        <div className="pov-scene-skeleton">
                          <span>🎨</span>
                          <span>Generating…</span>
                        </div>
                      )}
                      <span className="pov-scene-badge">Scene {s.index + 1}</span>
                    </div>
                    <div className="pov-scene-info">
                      <p className="pov-scene-narration" title={s.narration}>
                        "{s.narration}"
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Image Lightbox Modal */}
      {selectedImage && (
        <div className="pov-modal-backdrop" onClick={() => setSelectedImage(null)}>
          <div className="pov-modal-content" onClick={e => e.stopPropagation()}>
            <button className="pov-modal-close" onClick={() => setSelectedImage(null)}>✕</button>
            <img src={selectedImage} alt="POV Scene Preview" className="pov-modal-img" />
          </div>
        </div>
      )}
    </div>
  )
}
