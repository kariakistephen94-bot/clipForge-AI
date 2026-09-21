import { useCallback, useEffect, useState } from 'react'
import { api, type SettingsResponse, type SystemInfo } from './api'
import Dashboard from './components/Dashboard'
import ProjectView from './components/ProjectView'
import SettingsPage from './components/Settings'

function useHashRoute(): string {
  const [hash, setHash] = useState(() => window.location.hash || '#/')
  useEffect(() => {
    const on = () => setHash(window.location.hash || '#/')
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return hash
}

export default function App() {
  const route = useHashRoute()
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [settings, setSettings] = useState<SettingsResponse | null>(null)
  const [backendError, setBackendError] = useState<string | null>(null)

  const loadSettings = useCallback(() => {
    api<SettingsResponse>('/api/settings').then(setSettings).catch((e) => setBackendError(e.message))
  }, [])

  useEffect(() => {
    api<SystemInfo>('/api/system').then(setSystem).catch((e) => setBackendError(e.message))
    loadSettings()
  }, [loadSettings])

  const projectMatch = route.match(/^#\/project\/([a-zA-Z0-9_-]+)/)
  const page = projectMatch ? 'project' : route.startsWith('#/settings') ? 'settings' : 'dashboard'

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#/">
          <span className="brand-mark">✂</span> ClipForge AI
        </a>
        <nav className="nav">
          <a href="#/" className={page === 'dashboard' ? 'active' : ''}>Dashboard</a>
          <a href="#/settings" className={page === 'settings' ? 'active' : ''}>Settings</a>
        </nav>
        <div className="spacer" />
        {system && (
          <span className={`badge ${system.gemini.configured ? 'ok' : 'warn'}`} title={system.gemini.model}>
            {system.gemini.configured ? `Gemini: ${system.gemini.model}` : 'Gemini not configured - Demo mode'}
          </span>
        )}
      </header>
      <main className="page">
        {backendError && (
          <div className="banner bad">
            <b>Backend unavailable.</b>
            <span>{backendError}</span>
          </div>
        )}
        {system && !system.ffmpeg.ok && (
          <div className="banner bad">
            <b>FFmpeg missing.</b>
            <span>
              Rendering and analysis need FFmpeg. Install it with <code>{system.ffmpeg.install_hint}</code> and restart ClipForge AI.
            </span>
          </div>
        )}
        {page === 'dashboard' && <Dashboard system={system} settings={settings} />}
        {page === 'project' && projectMatch && <ProjectView key={projectMatch[1]} projectId={projectMatch[1]} settings={settings} />}
        {page === 'settings' && <SettingsPage settings={settings} system={system} onSaved={loadSettings} />}
      </main>
    </>
  )
}
