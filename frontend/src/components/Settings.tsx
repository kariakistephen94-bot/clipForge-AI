import { useEffect, useState } from 'react'
import { api, type Preferences, type SettingsResponse, type SystemInfo } from '../api'
import { GradeSelect, GradeSliders } from './ColorGrade'
import { Toast, Toggle } from './common'
import SoundLibrary from './SoundLibrary'
import { PublishConnections } from './Publish'
import SoundFolderPicker from './SoundFolderPicker'

export default function SettingsPage({ settings, system, onSaved }: {
  settings: SettingsResponse | null; system: SystemInfo | null; onSaved: () => void
}) {
  const [prefs, setPrefs] = useState<Preferences | null>(null)
  const [toast, setToast] = useState<{ msg: string; kind: 'info' | 'bad' } | null>(null)
  const [libKey, setLibKey] = useState(0)

  useEffect(() => {
    if (settings) setPrefs(settings.preferences)
  }, [settings])

  if (!settings || !prefs) return <div className="empty">Loading settings…</div>

  const set = <K extends keyof Preferences>(k: K, v: Preferences[K]) => setPrefs({ ...prefs, [k]: v })
  const setOverride = (k: string, v: string | number | boolean) => set('caption_overrides', { ...prefs.caption_overrides, [k]: v })
  const styleDefaults = settings.caption_styles[prefs.caption_style] ?? {}
  const ov = (k: string) => (prefs.caption_overrides[k] ?? styleDefaults[k] ?? '') as string | number | boolean

  const save = async () => {
    try {
      await api('/api/settings', { method: 'PUT', json: prefs })
      setToast({ msg: 'Settings saved', kind: 'info' })
      onSaved()
      setLibKey((k) => k + 1)
    } catch (e) {
      setToast({ msg: (e as Error).message, kind: 'bad' })
    }
  }

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="row between">
        <h1>Settings</h1>
        <button className="btn primary" onClick={save}>Save settings</button>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-title"><h2>AI</h2></div>
          <div className="stack">
            <div className="row between">
              <span>Gemini API key</span>
              <span className={`badge ${settings.gemini.configured ? 'ok' : 'warn'}`}>{settings.gemini.configured ? 'Configured' : 'Not configured'}</span>
            </div>
            {!settings.gemini.configured && (
              <div className="banner warn" style={{ margin: 0 }}>
                <span className="small">
                  Add <code>GEMINI_API_KEY=...</code> to the <code>.env</code> file in the project root and restart ClipForge AI.
                  The key stays on the backend: it is never sent to the browser or stored in the database.
                  Without it, analysis runs in <b>Demo mode</b> (offline heuristics, not real AI).
                </span>
              </div>
            )}
            <div className="row between"><span>Gemini model</span><code>{settings.gemini.model}</code></div>
            <div className="tiny muted">Change with <code>GEMINI_MODEL</code> in <code>.env</code>.</div>
            <Toggle label="Use Gemini by default for new projects" checked={prefs.use_gemini} onChange={(v) => set('use_gemini', v)} />
            <div className="settings-grid">
              <label className="field">
                <span>Whisper model (local)</span>
                <select value={prefs.whisper_model} onChange={(e) => set('whisper_model', e.target.value)}>
                  {settings.whisper_models.map((m) => <option key={m} value={m}>{m}{m === 'base' ? ' (default, efficient)' : ''}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Maximum candidates</span>
                <input type="number" min={3} max={30} value={prefs.max_candidates} onChange={(e) => set('max_candidates', Number(e.target.value))} />
              </label>
              <label className="field">
                <span>Default min clip duration (s)</span>
                <input type="number" min={3} max={600} value={prefs.default_min_duration} onChange={(e) => set('default_min_duration', Number(e.target.value))} />
              </label>
              <label className="field">
                <span>Default max clip duration (s)</span>
                <input type="number" min={5} max={600} value={prefs.default_max_duration} onChange={(e) => set('default_max_duration', Number(e.target.value))} />
              </label>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-title"><h2>System check</h2></div>
          {!system ? <p className="muted">Checking…</p> : (
            <dl className="kv">
              <dt>FFmpeg</dt><dd>{system.ffmpeg.ok ? <span className="badge ok">OK</span> : <span className="badge bad">Missing — {system.ffmpeg.install_hint}</span>} <span className="tiny muted">{system.ffmpeg.version}</span></dd>
              <dt>ffprobe</dt><dd>{system.ffprobe.ok ? <span className="badge ok">OK</span> : <span className="badge bad">Missing</span>}</dd>
              <dt>libass</dt><dd>{system.ffmpeg.libass ? 'available' : 'not in this FFmpeg build — captions are rendered by ClipForge instead (no impact)'}</dd>
              <dt>Python</dt><dd>{system.python} · {system.platform}</dd>
              <dt>Node</dt><dd>{system.node.version ?? 'not found (only needed to build the UI)'}</dd>
              <dt>Whisper</dt><dd>{system.whisper.device.note} · {system.whisper.device.compute_type} · {system.whisper.device.cpu_threads} threads<br />
                <span className="tiny muted">Downloaded: {system.whisper.downloaded_models.join(', ') || 'none yet (downloads on first use)'}</span></dd>
              <dt>Face model</dt><dd>{system.face_model ? <span className="badge ok">YuNet ready</span> : <span className="badge warn">Missing — run start.sh (center crop fallback)</span>}</dd>
              <dt>Fonts</dt><dd className="tiny">{system.fonts.heavy ?? 'fallback'}<br />{system.fonts.bold ?? ''}</dd>
              <dt>Workspace</dt><dd className="mono tiny">{system.workspace}</dd>
            </dl>
          )}
        </div>
      </div>

      <PublishConnections />

      <div className="card">
        <div className="card-title"><h2>Captions</h2><span className="sub">Fonts are read from your system; none are bundled</span></div>
        <div className="settings-grid">
          <Toggle label="Burn-in captions by default" checked={prefs.captions} onChange={(v) => set('captions', v)} />
          <label className="field">
            <span>Caption style</span>
            <select value={prefs.caption_style} onChange={(e) => setPrefs({ ...prefs, caption_style: e.target.value, caption_overrides: {} })}>
              <option value="bold_viral">Bold Viral</option>
              <option value="clean">Clean</option>
              <option value="minimal">Minimal</option>
            </select>
          </label>
          <label className="field">
            <span>Font (blank = style default)</span>
            <select value={String(prefs.caption_overrides.font ?? '')} onChange={(e) => setOverride('font', e.target.value)}>
              <option value="">Style default</option>
              {settings.fonts.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Font size: {String(ov('font_size'))}</span>
            <input type="range" min={32} max={140} value={Number(ov('font_size'))} onChange={(e) => setOverride('font_size', Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Outline width: {String(ov('outline_width'))}</span>
            <input type="range" min={0} max={20} value={Number(ov('outline_width'))} onChange={(e) => setOverride('outline_width', Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Vertical position: {Math.round(Number(ov('position_y')) * 100)}% from top</span>
            <input type="range" min={0.3} max={0.8} step={0.01} value={Number(ov('position_y'))} onChange={(e) => setOverride('position_y', Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Words per caption: {String(ov('words_per_caption'))}</span>
            <input type="range" min={3} max={7} value={Number(ov('words_per_caption'))} onChange={(e) => setOverride('words_per_caption', Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Highlight mode</span>
            <select value={String(ov('highlight_mode'))} onChange={(e) => setOverride('highlight_mode', e.target.value)}>
              <option value="word">Active word</option>
              <option value="fill">Karaoke fill</option>
              <option value="none">None</option>
            </select>
          </label>
          <Toggle label="Shadow" checked={Boolean(ov('shadow'))} onChange={(v) => setOverride('shadow', v)} />
          <Toggle label="UPPERCASE" checked={Boolean(ov('uppercase'))} onChange={(v) => setOverride('uppercase', v)} />
          <Toggle label="Emphasis words (from AI)" checked={Boolean(ov('emphasis'))} onChange={(v) => setOverride('emphasis', v)} />
        </div>
      </div>

      <div className="card">
        <div className="card-title"><h2>Colour grade</h2><span className="sub">Applied to the picture only; captions and hook text stay clean</span></div>
        <div className="stack">
          <div className="settings-grid">
            <GradeSelect label="Default for shorts" value={prefs.color_grade} grades={settings.color_grades} onChange={(v) => set('color_grade', v)} />
            <GradeSelect label="Default for long-form" value={prefs.long_form_color_grade} grades={settings.color_grades} onChange={(v) => set('long_form_color_grade', v)} />
          </div>
          <GradeSliders grade={prefs.color_grade} overrides={prefs.grade_overrides} grades={settings.color_grades} onChange={(o) => set('grade_overrides', o)} />
          <div className="tiny muted">
            Fine-tuning sits on top of whichever preset a render uses (shorts and long-form share it). Open a project's
            <i> Render options → Look</i> to compare source and graded frames before rendering; the grade used is written to each clip's metadata.json.
          </div>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-title"><h2>Editing</h2></div>
          <div className="stack">
            <Toggle label="Smart reframing (face / speaker tracking)" checked={prefs.smart_reframe} onChange={(v) => set('smart_reframe', v)} />
            <Toggle label="Split screen for two speakers" hint="Only ever used when the campaign rules explicitly allow split screen" checked={prefs.split_screen} onChange={(v) => set('split_screen', v)} />
            <Toggle label="Smart silence removal" hint="Conservative: only confirmed dead air, natural pauses kept" checked={prefs.silence_removal} onChange={(v) => set('silence_removal', v)} />
            <Toggle label="Aggressive editing" hint="Also trims dramatic pauses" checked={prefs.aggressive_silence} onChange={(v) => set('aggressive_silence', v)} />
            <Toggle label="Auto punch-in zooms (108–112%)" checked={prefs.auto_zoom} onChange={(v) => set('auto_zoom', v)} />
            <label className="field">
              <span>Hook overlay duration (seconds, 0 = whole clip)</span>
              <input type="number" min={0} max={15} step={0.5} value={prefs.hook_seconds} onChange={(e) => set('hook_seconds', Number(e.target.value))} />
            </label>
            <div className="field">
              <span>Variants</span>
              <div className="row">
                {[['A', 'Recommended hook'], ['B', 'Alternative hook'], ['C', 'Captions only']].map(([v, l]) => (
                  <label key={v} className="check small">
                    <input type="checkbox" checked={prefs.variants.includes(v)} onChange={(e) => set('variants', e.target.checked ? [...prefs.variants, v] : prefs.variants.filter((x) => x !== v))} />
                    {v}: {l}
                  </label>
                ))}
              </div>
            </div>
            <label className="field">
              <span>B-roll</span>
              <select value={prefs.broll_mode} onChange={(e) => set('broll_mode', e.target.value)}>
                <option value="off">Off</option>
                <option value="local">Local (workspace/broll/)</option>
                <option value="stock" disabled>Free stock provider APIs — not implemented yet</option>
              </select>
              <span className="tiny muted">{settings.broll_files.length} local B-roll file(s). Only files you place (and have licensed) are used.</span>
            </label>
            <Toggle label="Allow local B-roll when campaign rules don't mention it" checked={prefs.broll_allow_unstated} onChange={(v) => set('broll_allow_unstated', v)} />
          </div>
        </div>

        <div className="card">
          <div className="card-title"><h2>Audio & output</h2></div>
          <div className="stack">
            <Toggle label="Loudness normalization (-14 LUFS) + limiter" checked={prefs.normalize_audio} onChange={(v) => set('normalize_audio', v)} />
            <Toggle label="Mild noise cleanup" checked={prefs.denoise} onChange={(v) => set('denoise', v)} />
            <label className="field">
              <span>Background music (from workspace/music/, ducked under speech)</span>
              <select value={prefs.music_file && prefs.music_file.startsWith(prefs.sfx_library_path || '\u0000') ? prefs.music_file : (prefs.music_file ? prefs.music_file.split('/').pop() : '')}
                onChange={(e) => set('music_file', e.target.value || null)}>
                <option value="">None</option>
                {settings.music_files.map((m) => <option key={m} value={m}>{m}</option>)}
                {settings.music_beds.length > 0 && (
                  <optgroup label="From your sound library">
                    {settings.music_beds.map((m) => <option key={m.path} value={m.path}>{m.name}</option>)}
                  </optgroup>
                )}
              </select>
              <span className="tiny muted">Never added automatically. Use only music you have rights to; skipped if the campaign prohibits music.</span>
            </label>
            <label className="field">
              <span>Music volume: {Math.round(prefs.music_volume * 100)}%</span>
              <input type="range" min={0} max={1} step={0.01} value={prefs.music_volume} onChange={(e) => set('music_volume', Number(e.target.value))} />
            </label>
            <div className="settings-grid">
              <label className="field">
                <span>Output resolution</span>
                <select value={prefs.output_resolution} onChange={(e) => set('output_resolution', e.target.value)}>
                  <option value="1080x1920">1080×1920 (recommended)</option>
                  <option value="720x1280">720×1280 (faster)</option>
                </select>
              </label>
              <label className="field">
                <span>Frame rate</span>
                <select value={prefs.output_fps} onChange={(e) => set('output_fps', Number(e.target.value))}>
                  <option value={30}>30 fps</option>
                  <option value={60}>60 fps</option>
                </select>
              </label>
              <label className="field">
                <span>Quality (CRF, lower = better)</span>
                <input type="number" min={14} max={30} value={prefs.crf} onChange={(e) => set('crf', Number(e.target.value))} />
              </label>
              <label className="field">
                <span>Encoder speed</span>
                <select value={prefs.encoder_preset} onChange={(e) => set('encoder_preset', e.target.value)}>
                  {['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium'].map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </label>
            </div>
          </div>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-title"><h2>Sound design</h2><span className="sub">Effects from your own sound library</span></div>
          <div className="stack">
            <SoundFolderPicker
              value={prefs.sfx_library_path}
              onChange={(val) => set('sfx_library_path', val)}
            />
            <label className="field">
              <span>Default style for shorts</span>
              <select value={prefs.sound_design} onChange={(e) => set('sound_design', e.target.value as Preferences['sound_design'])}>
                <option value="off">Off</option>
                <option value="subtle">Subtle — hook entrance + a couple of key moments</option>
                <option value="balanced">Balanced — + punch-ins, payoff riser/impact, emphasis pops</option>
                <option value="punchy">Punchy — + jump-cut swishes, denser</option>
              </select>
            </label>
            <label className="field">
              <span>Effects volume: {Math.round(prefs.sfx_volume * 100)}%</span>
              <input type="range" min={0} max={2} step={0.05} value={prefs.sfx_volume} onChange={(e) => set('sfx_volume', Number(e.target.value))} />
            </label>
            <Toggle label="Playful sounds" hint="Comedic and crowd effects, only on clips the AI reads as humorous" checked={prefs.sfx_playful} onChange={(v) => set('sfx_playful', v)} />
            <div className="tiny muted">Every effect is levelled to its category, aligned to where the sound really starts or peaks, kept under the voice, and skipped if the campaign forbids added sound.</div>
          </div>
        </div>

        <div className="card">
          <div className="card-title"><h2>Long-form clips</h2><span className="sub">16:9 episodes for YouTube</span></div>
          <div className="settings-grid">
            <label className="field">
              <span>Minimum length (minutes)</span>
              <input type="number" min={1} max={60} step={0.5} value={prefs.long_form_min_duration / 60} onChange={(e) => set('long_form_min_duration', Number(e.target.value) * 60)} />
            </label>
            <label className="field">
              <span>Maximum length (minutes)</span>
              <input type="number" min={2} max={90} step={0.5} value={prefs.long_form_max_duration / 60} onChange={(e) => set('long_form_max_duration', Number(e.target.value) * 60)} />
            </label>
            <label className="field">
              <span>Most clips per video (Auto)</span>
              <input type="number" min={1} max={20} value={prefs.long_form_max_clips} onChange={(e) => set('long_form_max_clips', Number(e.target.value))} />
            </label>
            <label className="field">
              <span>Resolution</span>
              <select value={prefs.long_form_resolution} onChange={(e) => set('long_form_resolution', e.target.value)}>
                <option value="1920x1080">1920×1080</option>
                <option value="1280x720">1280×720 (faster)</option>
              </select>
            </label>
            <label className="field">
              <span>Sound design</span>
              <select value={prefs.long_form_sound_design} onChange={(e) => set('long_form_sound_design', e.target.value as Preferences['long_form_sound_design'])}>
                <option value="off">Off</option>
                <option value="subtle">Subtle — cold open + chapter turns</option>
                <option value="balanced">Balanced — + ~1 accent per minute</option>
                <option value="punchy">Punchy — + ~2 accents per minute</option>
              </select>
            </label>
            <Toggle label="Cold open teaser" checked={prefs.long_form_cold_open} onChange={(v) => set('long_form_cold_open', v)} />
            <Toggle label="Remove dead air" checked={prefs.long_form_silence_removal} onChange={(v) => set('long_form_silence_removal', v)} />
            <Toggle label="Burn-in captions" checked={prefs.long_form_captions} onChange={(v) => set('long_form_captions', v)} />
          </div>
          <p className="tiny muted">Auto amount aims to cover the best ~60% of a source: ~1–2 clips for 15 minutes, ~5 for an hour, ~9 for three hours. Only segments scoring 50+ are pre-selected.</p>
        </div>
      </div>

      <div className="card">
        <div className="card-title"><h2>Sound library</h2><span className="sub">Preview, re-categorise or switch off any sound</span></div>
        <SoundLibrary categories={settings.sfx_categories} reloadKey={libKey} />
      </div>

      {toast && <Toast message={toast.msg} kind={toast.kind} onDone={() => setToast(null)} />}
    </div>
  )
}
