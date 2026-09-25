// Typed client for the local ClipForge backend. No secrets are ever handled here.

export type StepStatus = 'pending' | 'running' | 'done' | 'error' | 'skipped'

export interface JobStep {
  key: string
  label: string
  status: StepStatus
  progress: number | null
  detail: string
}

export interface Job {
  job_id: string
  project_id: string
  kind: string
  status: string
  message: string
  error: string | null
  steps: JobStep[]
  version: number
}

export interface ProjectSummary {
  id: string
  name: string
  campaign_name: string
  status: string
  error: string | null
  created_at: string
  updated_at: string
  candidates: number
  exports: number
  long_form: number
  source_name: string
  duration: number | null
  provider: string
  platforms: string[]
  youtube_retry: { retry_at: string | null; attempts: number; since: string } | null
}

export interface CampaignRules {
  min_duration: number | null
  max_duration: number | null
  platforms: string[]
  required_hashtags: string[]
  required_mentions: string[]
  required_cta: string | null
  required_links: string[]
  captions_allowed: boolean | null
  captions_required: boolean | null
  broll_allowed: boolean | null
  split_screen_allowed: boolean | null
  music_allowed: boolean | null
  source_modification_rules: string[]
  prohibited_content: string[]
  special_requirements: string[]
  unknown_requirements: string[]
}

export interface Probe {
  duration: number
  width: number
  height: number
  display_width: number
  display_height: number
  fps: number
  video_codec: string
  audio_codec: string | null
  audio_channels: number
  audio_streams: number
  has_audio: boolean
  bitrate: number
  size_bytes: number
}

export interface DriveFile {
  id: string
  name: string
  url: string
  folder: string
}

export const isDriveFolder = (url: string) => /drive\.google\.com\/.*(folders\/|folderview)/i.test(url.trim())

export interface Project extends ProjectSummary {
  campaign_rules_text: string
  campaign_rules: CampaignRules | null
  campaign_rules_source: string
  desired_clip_count: number
  duration_mode: string
  min_duration: number
  max_duration: number
  use_gemini: boolean
  long_form_mode: 'auto' | 'manual' | 'off'
  long_form_count: number
  usage: { gemini_requests: number; video_uploads: number; cached_requests: number; input_tokens: number; output_tokens: number }
  source: {
    origin: string
    url: string | null
    filename: string
    size_bytes: number
    sha256: string
    probe: Probe | null
    has_proxy: boolean
    preview_url: string | null
  } | null
  analysis: {
    provider: string
    model: string
    created_at: string
    raw_candidates: number
    kept_candidates: number
    summary?: string
    content_type?: string
    main_topics?: string[]
    tone?: string
    duplicates_removed?: { candidate_id: string; kept: string; reason: string }[]
    duration_notes?: string[]
    min_duration?: number
    max_duration?: number
    long_form?: { target: number; ideal_seconds: number; note: string; kept: number; selected: number; ai_notes: string; source: string }
  } | null
  job: Job | null
  paths: { exports: string; ready_to_post: string }
  gemini: { configured: boolean; model: string }
}

export interface ComplianceCheck {
  rule: string
  status: string
  detail: string
  source: string
}

export interface ComplianceReport {
  status: string
  checks: ComplianceCheck[]
  manual_checks: string[]
}

export interface Hook {
  text: string
  source: string
}

export interface PostingCopy {
  tiktok_caption: string
  instagram_caption: string
  youtube_title: string
  youtube_description: string
  hashtags: string[]
  required: { hashtags: string[]; mentions: string[]; cta: string | null; links: string[] }
  full: Record<string, string>
  cover_text?: string
  thumbnail_prompts?: ThumbnailPrompt[]
}

export interface CandidateExport {
  id: string
  folder: string
  compliance_status: string
  created_at: string
  videos: Record<string, string>
  thumbnail: string | null
  notes: string[]
  sound_events: SoundEvent[]
  thumbnail_prompt: string | null
}

export interface SoundEvent {
  item_id: string
  name: string
  category: string
  reason: string
  t: number
  start: number
  length: number
  gain_db: number
  hook_only: boolean
}

export interface ThumbnailPrompt {
  concept: string
  emotion: string
  text_overlay: string
  aspect: string
  size: string
  frame_timestamp: number | null
  why_it_works: string
  prompt: string
  reference_prompt: string
  midjourney: string
  negative_prompt: string
  reference_file?: string
}

export interface LongFormClip {
  id: string
  candidate_id: string
  rank: number
  start: number
  end: number
  duration: number
  score: number
  ai_score: number | null
  subscores: { key: string; value: number; max: number }[]
  penalties: { reason: string; points: number; source: string }[]
  titles: string[]
  title_index: number
  topic: string
  summary: string
  reason_it_works: string
  target_audience: string
  description: string
  tags: string[]
  chapters: { t: number; title: string }[]
  cold_open: { start: number; end: number; reason: string } | null
  thumbnail_prompts: ThumbnailPrompt[]
  demo: boolean
  selected: boolean
  rejected: boolean
  snap_notes: string[]
  transcript_preview: string
  export: {
    folder: string
    created_at: string
    duration: number
    video: string | null
    thumbnail_drafts: string[]
    thumbnail_frames: string[]
    chapters: { t: number; title: string }[]
    chapters_text: string
    notes: string[]
    compliance_status: string
    title: string
    sound_events: SoundEvent[]
  } | null
}

export interface SfxItem {
  id: string
  label: string
  name: string
  path: string
  folder: string
  category: string
  auto_category: string
  category_source: string
  duration: number
  offset: number
  lead: number
  tail: number
  loud_db: number
  slice_index: number | null
  enabled: boolean
}

export interface SfxResponse {
  scanning: { running: boolean; done: number; total: number } | null
  dirs: string[]
  summary: { counts: Record<string, number>; files: number; items: number } | null
  items: SfxItem[]
}

export interface Candidate {
  id: string
  candidate_id: string
  rank: number
  start: number
  end: number
  duration: number
  ai_start: number
  ai_end: number
  viral_score: number
  ai_viral_score: number | null
  subscores: { key: string; value: number; max: number }[]
  penalties: { reason: string; points: number; source: string }[]
  topic: string
  summary: string
  reason_it_works: string
  target_audience: string
  hook_type: string
  story_structure: Record<string, string>
  editing_strategy: string
  caption_emphasis_words: string[]
  suggested_zoom_points: { timestamp: number; reason: string; kind: string }[]
  suggested_broll_points: { timestamp: number; duration: number; query: string; reason: string }[]
  platform_fit: { tiktok?: number; instagram?: number; youtube_shorts?: number; notes?: string }
  exact_opening_words: string
  exact_closing_words: string
  hooks: Hook[]
  hook_index: number
  posting_copy: PostingCopy | null
  compliance_status: string
  compliance: ComplianceReport | null
  selected: boolean
  rejected: boolean
  user_edited: boolean
  snap_notes: string[]
  transcript_text: string
  refined: boolean
  boundary_feedback: string | null
  exports: CandidateExport[]
}

export interface Preferences {
  use_gemini: boolean
  whisper_model: string
  default_min_duration: number
  default_max_duration: number
  max_candidates: number
  caption_style: string
  caption_overrides: Record<string, string | number | boolean>
  captions: boolean
  smart_reframe: boolean
  split_screen: boolean
  silence_removal: boolean
  aggressive_silence: boolean
  auto_zoom: boolean
  broll_mode: string
  broll_allow_unstated: boolean
  music_file: string | null
  music_volume: number
  denoise: boolean
  normalize_audio: boolean
  output_resolution: string
  output_fps: number
  crf: number
  encoder_preset: string
  variants: string[]
  hook_seconds: number
  sound_design: 'off' | 'subtle' | 'balanced' | 'punchy'
  sfx_volume: number
  sfx_playful: boolean
  sfx_library_path: string
  color_grade: string
  grade_overrides: Record<string, number>
  long_form_color_grade: string
  long_form_min_duration: number
  long_form_max_duration: number
  long_form_max_clips: number
  long_form_resolution: string
  long_form_captions: boolean
  long_form_cold_open: boolean
  long_form_silence_removal: boolean
  long_form_sound_design: 'off' | 'subtle' | 'balanced' | 'punchy'
}

export interface GradePreset {
  name: string
  label: string
  description: string
  exposure: number
  contrast: number
  saturation: number
  temperature: number
  tint: number
  shadows: number
  highlights: number
  fade: number
  vignette: number
  intensity: number
}

export interface SettingsResponse {
  preferences: Preferences
  gemini: { configured: boolean; model: string }
  whisper_models: string[]
  caption_styles: Record<string, Record<string, unknown>>
  color_grades: Record<string, GradePreset>
  fonts: string[]
  music_files: string[]
  broll_files: string[]
  music_beds: { name: string; path: string }[]
  sfx_categories: Record<string, { label: string; description: string; auto: boolean }>
}

export interface SystemInfo {
  python: string
  platform: string
  ffmpeg: { path: string | null; version: string | null; ok: boolean; install_hint: string; libass: boolean; required_filters_ok: boolean }
  ffprobe: { path: string | null; ok: boolean }
  node: { path: string | null; version: string | null }
  gemini: { configured: boolean; model: string }
  whisper: { device: { device: string; compute_type: string; note: string; cpu_threads: number }; downloaded_models: string[] }
  face_model: boolean
  fonts: { heavy: string | null; bold: string | null }
  workspace: string
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) return body.detail.map((d: { msg: string }) => d.msg).join('; ')
  } catch {
    /* not JSON */
  }
  return `${res.status} ${res.statusText}`
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const opts: RequestInit = { ...init }
  if (init?.json !== undefined) {
    opts.body = JSON.stringify(init.json)
    opts.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) }
  }
  let res: Response
  try {
    res = await fetch(path, opts)
  } catch {
    throw new Error('Cannot reach the ClipForge backend. Is it running (./start.sh)?')
  }
  if (!res.ok) throw new Error(await parseError(res))
  return res.json() as Promise<T>
}

export function uploadProject(form: FormData, onProgress: (f: number) => void): Promise<Project> {
  return uploadForm('/api/projects', form, onProgress)
}

export function uploadForm(path: string, form: FormData, onProgress: (f: number) => void): Promise<Project> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      let body: { detail?: unknown } = {}
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Project)
      else {
        const d = body.detail
        reject(new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map((x: { msg: string }) => x.msg).join('; ') : `Upload failed (${xhr.status})`))
      }
    }
    xhr.onerror = () => reject(new Error('Network error while uploading. Is the backend running?'))
    xhr.send(form)
  })
}

export function subscribeJob(projectId: string, onJob: (job: Job | null) => void): () => void {
  const es = new EventSource(`/api/projects/${projectId}/events`)
  es.addEventListener('job', (e) => {
    try {
      onJob(JSON.parse((e as MessageEvent).data))
    } catch {
      /* ignore malformed event */
    }
  })
  return () => es.close()
}

export const PLATFORMS: { key: string; label: string }[] = [
  { key: 'tiktok', label: 'TikTok' },
  { key: 'instagram', label: 'Instagram Reels' },
  { key: 'youtube_shorts', label: 'YouTube Shorts' },
]

export function fmtTime(sec: number | null | undefined, ms = false): string {
  if (sec == null || Number.isNaN(sec)) return '--:--'
  const s = Math.max(0, sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  const secStr = ms ? r.toFixed(1).padStart(4, '0') : String(Math.floor(r)).padStart(2, '0')
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${secStr}`
}

export function fmtBytes(n: number | null | undefined): string {
  if (!n) return '-'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

// ---------------------------------------------------------------- publishing

export interface PublishConnection {
  platform: string
  connected: boolean
  account_name?: string
  account_id?: string
  scopes?: string[]
  connected_at?: string | null
  expires_at?: string | null
  needs_refresh?: boolean
}

export interface PublishStatus {
  configured: { youtube: boolean; tiktok: boolean }
  connections: Record<string, PublishConnection>
  redirect_uris: Record<string, string>
  setup_doc: string
}

export interface ClipPublishOptions {
  candidate_pk: string
  project_id: string
  project_name: string
  campaign: string
  topic: string
  hook: string
  duration: number
  viral_score: number
  compliance_status: string
  compliance_blocks_publishing: boolean
  variants: Record<string, string>
  default_variant: string
  youtube: { title: string; description: string; tags: string[]; privacy: string; made_for_kids: boolean }
  tiktok: { caption: string; privacy: string; mode: string; disable_comment: boolean; disable_duet: boolean; disable_stitch: boolean }
  connections: Record<string, PublishConnection>
}

export interface PublishJobRow {
  id: string
  project_id: string
  candidate_pk: string | null
  platform: string
  mode: string
  status: string
  privacy: string
  title: string
  remote_url: string | null
  account_name: string
  error: string | null
  created_at: string | null
  finished_at: string | null
  needs_user_action: boolean
}

export const YT_PRIVACY = [
  { key: 'private', label: 'Private (only you)' },
  { key: 'unlisted', label: 'Unlisted (link only)' },
  { key: 'public', label: 'Public' },
]

export const TT_PRIVACY = [
  { key: 'SELF_ONLY', label: 'Private (only you)' },
  { key: 'MUTUAL_FOLLOW_FRIENDS', label: 'Friends' },
  { key: 'FOLLOWER_OF_CREATOR', label: 'Followers' },
  { key: 'PUBLIC_TO_EVERYONE', label: 'Public' },
]
