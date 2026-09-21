import { useCallback, useEffect, useState } from 'react'
import {
  api, fmtDate, TT_PRIVACY, YT_PRIVACY,
  type ClipPublishOptions, type Job, type PublishJobRow, type PublishStatus,
} from '../api'
import { ComplianceBadge, Toast } from './common'

const LABEL: Record<string, string> = { youtube: 'YouTube', tiktok: 'TikTok' }

function openAuthWindow(url: string) {
  window.open(url, 'clipforge-auth', 'width=560,height=760')
}

/** Settings card: connect / disconnect the user's own accounts. */
export function PublishConnections() {
  const [status, setStatus] = useState<PublishStatus | null>(null)
  const [history, setHistory] = useState<PublishJobRow[]>([])
  const [busy, setBusy] = useState('')
  const [manual, setManual] = useState<{ platform: string; code: string } | null>(null)
  const [tiktokMode, setTiktokMode] = useState('inbox')
  const [toast, setToast] = useState<{ msg: string; kind: 'info' | 'bad' } | null>(null)

  const load = useCallback(() => {
    api<PublishStatus>('/api/publish').then(setStatus).catch((e) => setToast({ msg: e.message, kind: 'bad' }))
    api<PublishJobRow[]>('/api/publish/history?limit=8').then(setHistory).catch(() => undefined)
  }, [])

  useEffect(() => {
    load()
    const onFocus = () => load()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [load])

  const connect = async (platform: string) => {
    setBusy(platform)
    try {
      const res = await api<{ auth_url: string }>(`/api/publish/${platform}/connect`, {
        method: 'POST', json: { mode: platform === 'tiktok' ? tiktokMode : undefined },
      })
      openAuthWindow(res.auth_url)
      setManual({ platform, code: '' })
    } catch (e) {
      setToast({ msg: (e as Error).message, kind: 'bad' })
    } finally {
      setBusy('')
    }
  }

  const submitCode = async () => {
    if (!manual?.code.trim()) return
    setBusy(manual.platform)
    try {
      await api(`/api/publish/${manual.platform}/code`, { method: 'POST', json: { code: manual.code.trim() } })
      setManual(null)
      setToast({ msg: `${LABEL[manual.platform]} connected`, kind: 'info' })
      load()
    } catch (e) {
      setToast({ msg: (e as Error).message, kind: 'bad' })
    } finally {
      setBusy('')
    }
  }

  const disconnect = async (platform: string) => {
    if (!window.confirm(`Disconnect ${LABEL[platform]}? ClipForge will no longer be able to post to that account.`)) return
    await api(`/api/publish/${platform}/disconnect`, { method: 'POST' })
    load()
  }

  if (!status) return null

  return (
    <div className="card">
      <div className="card-title">
        <h2>Connected accounts</h2>
        <span className="sub">Publishing goes to your own accounts, one clip at a time, after you confirm</span>
      </div>
      <div className="stack">
        {(['youtube', 'tiktok'] as const).map((p) => {
          const conn = status.connections[p]
          const configured = status.configured[p]
          return (
            <div key={p} className="row between" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 10 }}>
              <div style={{ minWidth: 0 }}>
                <div className="row" style={{ gap: 8 }}>
                  <b>{LABEL[p]}</b>
                  {conn?.connected
                    ? <span className="badge ok">Connected{conn.account_name ? `: ${conn.account_name}` : ''}</span>
                    : <span className={`badge ${configured ? 'neutral' : 'warn'}`}>{configured ? 'Not connected' : 'No API keys'}</span>}
                  {conn?.needs_refresh && <span className="badge warn">token expired — will refresh</span>}
                </div>
                {!configured && (
                  <div className="tiny muted">
                    Add {p === 'youtube' ? 'YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET' : 'TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET'} to
                    <code> .env</code> and restart — see <code>PUBLISHING.md</code>
                  </div>
                )}
                {configured && p === 'tiktok' && !conn?.connected && (
                  <label className="tiny muted row" style={{ gap: 6, marginTop: 4 }}>
                    Permission:
                    <select value={tiktokMode} onChange={(e) => setTiktokMode(e.target.value)} style={{ width: 'auto' }}>
                      <option value="inbox">Send to TikTok drafts (works without app review)</option>
                      <option value="direct">Post directly (needs an approved TikTok app)</option>
                    </select>
                  </label>
                )}
                {conn?.connected && conn.scopes?.length ? <div className="tiny faint">scopes: {conn.scopes.join(', ')}</div> : null}
              </div>
              <div className="row">
                {conn?.connected
                  ? <button className="btn sm ghost danger" onClick={() => disconnect(p)}>Disconnect</button>
                  : <button className="btn sm" disabled={!configured || busy === p} onClick={() => connect(p)}>Connect</button>}
              </div>
            </div>
          )
        })}

        {manual && (
          <div className="edit-box stack" style={{ gap: 8 }}>
            <div className="small">
              A browser window opened for {LABEL[manual.platform]}. If it redirected to a page ClipForge could not read,
              copy the <code>code</code> value from that page's address bar and paste it here.
            </div>
            <div className="row nowrap">
              <input type="text" placeholder="Paste code or full redirect URL" value={manual.code}
                onChange={(e) => setManual({ ...manual, code: e.target.value })} />
              <button className="btn sm primary" disabled={!manual.code.trim()} onClick={submitCode}>Finish</button>
              <button className="btn sm ghost" onClick={() => { setManual(null); load() }}>Done / cancel</button>
            </div>
          </div>
        )}

        {history.length > 0 && (
          <details>
            <summary>Recent publishes ({history.length})</summary>
            <div className="stack small" style={{ gap: 6, marginTop: 8 }}>
              {history.map((h) => (
                <div key={h.id} className="row between">
                  <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    <span className={`badge ${h.status === 'done' ? 'ok' : h.status === 'error' ? 'bad' : 'info'}`}>{h.status}</span>{' '}
                    {LABEL[h.platform] ?? h.platform} · {h.title || '(no title)'}
                    {h.needs_user_action && <span className="tiny warn"> · finish in the TikTok app</span>}
                  </span>
                  <span className="row" style={{ gap: 8 }}>
                    {h.remote_url && <a href={h.remote_url} target="_blank" rel="noreferrer">open</a>}
                    <span className="tiny faint">{fmtDate(h.created_at)}</span>
                  </span>
                </div>
              ))}
              {history.some((h) => h.error) && (
                <div className="tiny muted">{history.find((h) => h.error)?.error}</div>
              )}
            </div>
          </details>
        )}
      </div>
      {toast && <Toast message={toast.msg} kind={toast.kind} onDone={() => setToast(null)} />}
    </div>
  )
}

/** Confirm dialog: nothing is posted until the user presses Publish here. */
export function PublishModal({ candidatePk, onClose, onStarted }: {
  candidatePk: string; onClose: () => void; onStarted?: (job: Job) => void
}) {
  const [opts, setOpts] = useState<ClipPublishOptions | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [variant, setVariant] = useState('')
  const [useYouTube, setUseYouTube] = useState(false)
  const [useTikTok, setUseTikTok] = useState(false)
  const [yt, setYt] = useState({ title: '', description: '', tags: '', privacy: 'private', made_for_kids: false })
  const [tt, setTt] = useState({ caption: '', privacy: 'SELF_ONLY', mode: 'inbox', disable_comment: false, disable_duet: false, disable_stitch: false })
  const [publicOk, setPublicOk] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api<ClipPublishOptions>(`/api/publish/options/${candidatePk}`)
      .then((o) => {
        setOpts(o)
        setVariant(o.default_variant)
        setYt({ ...o.youtube, tags: o.youtube.tags.join(', ') })
        setTt({ ...o.tiktok })
        setUseYouTube(!!o.connections.youtube?.connected)
        setUseTikTok(!!o.connections.tiktok?.connected)
      })
      .catch((e) => setError(e.message))
  }, [candidatePk])

  const goesPublic = (useYouTube && yt.privacy === 'public') || (useTikTok && tt.mode === 'direct' && tt.privacy !== 'SELF_ONLY')
  const blocked = opts?.compliance_blocks_publishing
  const canPublish = !!opts && !blocked && (useYouTube || useTikTok) && (!goesPublic || publicOk) && !busy

  const publish = async () => {
    if (!opts) return
    setBusy(true)
    setError(null)
    const targets = []
    if (useYouTube) {
      targets.push({
        platform: 'youtube', title: yt.title, description: yt.description, privacy: yt.privacy,
        made_for_kids: yt.made_for_kids,
        tags: yt.tags.split(',').map((t) => t.trim()).filter(Boolean),
      })
    }
    if (useTikTok) {
      targets.push({
        platform: 'tiktok', mode: tt.mode, title: tt.caption, privacy: tt.privacy,
        disable_comment: tt.disable_comment, disable_duet: tt.disable_duet, disable_stitch: tt.disable_stitch,
      })
    }
    try {
      const job = await api<Job>('/api/publish/clip', {
        method: 'POST', json: { candidate_pk: candidatePk, variant, targets, confirm: true },
      })
      onStarted?.(job)
      onClose()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal stack" onClick={(e) => e.stopPropagation()} style={{ gap: 14, maxHeight: '88vh', overflowY: 'auto' }}>
        <div className="row between">
          <h2>Publish clip</h2>
          <button className="btn sm" onClick={onClose}>Close</button>
        </div>

        {error && <div className="banner bad" style={{ margin: 0 }}>{error}</div>}
        {!opts && !error && <p className="muted">Loading…</p>}

        {opts && (
          <>
            <div className="row" style={{ gap: 10 }}>
              <ComplianceBadge status={opts.compliance_status} />
              <span className="small muted">{opts.topic} · {opts.duration}s · score {opts.viral_score}</span>
              {Object.keys(opts.variants).length > 1 && (
                <label className="row small" style={{ gap: 6 }}>
                  Version:
                  <select value={variant} onChange={(e) => setVariant(e.target.value)} style={{ width: 'auto' }}>
                    {Object.entries(opts.variants).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                  </select>
                </label>
              )}
            </div>

            {blocked && (
              <div className="banner bad" style={{ margin: 0 }}>
                This clip FAILED the campaign compliance check, so ClipForge will not publish it.
              </div>
            )}

            {/* YouTube */}
            <div className="card tight stack" style={{ gap: 10 }}>
              <label className="check">
                <input type="checkbox" checked={useYouTube} disabled={!opts.connections.youtube?.connected}
                  onChange={(e) => setUseYouTube(e.target.checked)} />
                <b>YouTube Shorts</b>
                {opts.connections.youtube?.connected
                  ? <span className="tiny muted"> → {opts.connections.youtube.account_name || 'your channel'}</span>
                  : <span className="tiny warn"> — not connected (Settings → Connected accounts)</span>}
              </label>
              {useYouTube && (
                <>
                  <label className="field"><span>Title</span>
                    <input type="text" maxLength={100} value={yt.title} onChange={(e) => setYt({ ...yt, title: e.target.value })} />
                    <span className="tiny faint">{yt.title.length}/100</span>
                  </label>
                  <label className="field"><span>Description</span>
                    <textarea rows={4} value={yt.description} onChange={(e) => setYt({ ...yt, description: e.target.value })} />
                  </label>
                  <div className="row" style={{ gap: 12 }}>
                    <label className="field grow"><span>Tags (comma separated)</span>
                      <input type="text" value={yt.tags} onChange={(e) => setYt({ ...yt, tags: e.target.value })} />
                    </label>
                    <label className="field"><span>Visibility</span>
                      <select value={yt.privacy} onChange={(e) => setYt({ ...yt, privacy: e.target.value })}>
                        {YT_PRIVACY.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                      </select>
                    </label>
                  </div>
                  <label className="check small">
                    <input type="checkbox" checked={yt.made_for_kids} onChange={(e) => setYt({ ...yt, made_for_kids: e.target.checked })} />
                    Made for kids
                  </label>
                </>
              )}
            </div>

            {/* TikTok */}
            <div className="card tight stack" style={{ gap: 10 }}>
              <label className="check">
                <input type="checkbox" checked={useTikTok} disabled={!opts.connections.tiktok?.connected}
                  onChange={(e) => setUseTikTok(e.target.checked)} />
                <b>TikTok</b>
                {opts.connections.tiktok?.connected
                  ? <span className="tiny muted"> → {opts.connections.tiktok.account_name || 'your account'}</span>
                  : <span className="tiny warn"> — not connected (Settings → Connected accounts)</span>}
              </label>
              {useTikTok && (
                <>
                  <label className="field"><span>How to post</span>
                    <select value={tt.mode} onChange={(e) => setTt({ ...tt, mode: e.target.value })}>
                      <option value="inbox">Send to TikTok drafts — you finish the post in the app</option>
                      <option value="direct">Post directly (needs an approved TikTok app)</option>
                    </select>
                  </label>
                  <label className="field"><span>Caption</span>
                    <textarea rows={3} maxLength={2200} value={tt.caption} onChange={(e) => setTt({ ...tt, caption: e.target.value })} />
                  </label>
                  {tt.mode === 'direct' && (
                    <>
                      <label className="field"><span>Visibility</span>
                        <select value={tt.privacy} onChange={(e) => setTt({ ...tt, privacy: e.target.value })}>
                          {TT_PRIVACY.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                        </select>
                        <span className="tiny faint">Unapproved TikTok apps can only post privately (SELF_ONLY).</span>
                      </label>
                      <div className="row" style={{ gap: 14 }}>
                        <label className="check small"><input type="checkbox" checked={tt.disable_comment} onChange={(e) => setTt({ ...tt, disable_comment: e.target.checked })} /> Turn off comments</label>
                        <label className="check small"><input type="checkbox" checked={tt.disable_duet} onChange={(e) => setTt({ ...tt, disable_duet: e.target.checked })} /> No duet</label>
                        <label className="check small"><input type="checkbox" checked={tt.disable_stitch} onChange={(e) => setTt({ ...tt, disable_stitch: e.target.checked })} /> No stitch</label>
                      </div>
                    </>
                  )}
                  {tt.mode === 'inbox' && (
                    <div className="tiny muted">The clip appears in your TikTok inbox/drafts. Open TikTok to add sounds/effects and post.</div>
                  )}
                </>
              )}
            </div>

            {goesPublic && (
              <label className="check banner warn" style={{ margin: 0, alignItems: 'flex-start' }}>
                <input type="checkbox" checked={publicOk} onChange={(e) => setPublicOk(e.target.checked)} />
                <span>I understand this will be posted <b>publicly</b> to my account(s) and is visible to everyone.</span>
              </label>
            )}

            <div className="row between">
              <span className="tiny muted">Uploads run in the background; progress shows on the project page.</span>
              <div className="row">
                <button className="btn" onClick={onClose}>Cancel</button>
                <button className="btn primary" disabled={!canPublish} onClick={publish}>
                  {busy ? 'Starting…' : `Publish to ${[useYouTube && 'YouTube', useTikTok && 'TikTok'].filter(Boolean).join(' + ') || '…'}`}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
