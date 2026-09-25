import { useState, useRef } from 'react'
import { api } from '../api'

interface SoundFolderPickerProps {
  value: string
  onChange: (path: string) => void
}

export default function SoundFolderPicker({ value, onChange }: SoundFolderPickerProps) {
  const [showManual, setShowManual] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const [tempPath, setTempPath] = useState(value)
  const [isDragging, setIsDragging] = useState(false)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const folderName = value
    ? value.split('/').filter(Boolean).pop() || value
    : 'workspace/sfx/'

  const isCustom = Boolean(value && value.trim())

  const handleChooseFolder = async () => {
    setStatusMessage('Opening folder picker…')
    try {
      // Attempt native folder dialog on host machine
      const res = await api<{ path: string | null; canceled?: boolean; unsupported?: boolean }>('/api/utils/choose-folder', {
        method: 'POST',
      })
      if (!res.canceled && res.path) {
        onChange(res.path)
        setStatusMessage(null)
        return
      }
      if (res.canceled) {
        setStatusMessage(null)
        return
      }
      // If unsupported by host, open helper modal
      setTempPath(value)
      setShowModal(true)
      setStatusMessage(null)
    } catch {
      // Backend might not have reloaded the new route yet; open helper modal
      setTempPath(value)
      setShowModal(true)
      setStatusMessage(null)
    }
  }

  const handleRevealInFinder = async () => {
    if (!value) return
    try {
      await api('/api/utils/open-folder', {
        method: 'POST',
        json: { path: value },
      })
    } catch {
      // Fallback copy to clipboard
      navigator.clipboard?.writeText(value)
      setStatusMessage('Folder path copied to clipboard')
      setTimeout(() => setStatusMessage(null), 3000)
    }
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    // Check if path or text was dropped
    const text = e.dataTransfer.getData('text')
    if (text && (text.startsWith('/') || text.startsWith('~') || /^[A-Z]:\\/i.test(text))) {
      onChange(text.trim())
      return
    }
    // Check dropped files (e.g. from macOS Finder)
    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      // In electron or browser with file path
      const droppedFile = files[0] as unknown as { path?: string }
      if (droppedFile.path) {
        onChange(droppedFile.path)
      } else {
        // Fallback open manual
        setShowManual(true)
        setStatusMessage('Paste or type the folder path below')
        setTimeout(() => setStatusMessage(null), 3500)
      }
    }
  }

  const handleHtml5FolderSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files && files.length > 0) {
      const rel = files[0].webkitRelativePath
      const selectedFolderName = rel ? rel.split('/')[0] : files[0].name
      // Auto-suggest common user location
      const suggestedPath = `/Users/you/Downloads/${selectedFolderName}`
      setTempPath(suggestedPath)
    }
  }

  return (
    <div className="sound-folder-picker">
      <div
        className={`sound-folder-card ${isDragging ? 'drag-active' : ''}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <button
          type="button"
          className="sound-folder-img-btn"
          onClick={handleChooseFolder}
          title="Click to select folder"
        >
          <img
            src="/sound-folder.jpg"
            alt="Sound Library Folder"
            className="sound-folder-img"
          />
          <span className="sound-folder-img-badge">📁 Browse</span>
        </button>

        <div className="sound-folder-content">
          <div className="sound-folder-top">
            <span className="sound-folder-label">Sound library folder</span>
            <span className={`badge ${isCustom ? 'ok' : 'info'}`}>
              {isCustom ? '✓ Custom Folder' : 'Default (workspace/sfx/)'}
            </span>
          </div>

          <div className="sound-folder-title" title={value || 'workspace/sfx/'}>
            <span style={{ fontSize: 16 }}>📁</span>
            <span>{folderName}</span>
          </div>

          <div className="sound-folder-path" title={value || 'workspace/sfx/'}>
            {value || 'workspace/sfx/ (relative to project root)'}
          </div>

          <div className="sound-folder-actions">
            <button
              type="button"
              className="btn sm primary"
              onClick={handleChooseFolder}
            >
              📁 Choose folder…
            </button>
            {isCustom && (
              <button
                type="button"
                className="btn sm ghost"
                onClick={handleRevealInFinder}
                title="Show this folder in macOS Finder"
              >
                Reveal
              </button>
            )}
            {isCustom && (
              <button
                type="button"
                className="btn sm ghost danger"
                onClick={() => onChange('')}
                title="Revert to default workspace/sfx/ folder"
              >
                Reset
              </button>
            )}
            <button
              type="button"
              className="btn sm ghost"
              onClick={() => setShowManual((v) => !v)}
            >
              {showManual ? 'Hide manual path' : 'Edit path'}
            </button>
          </div>
        </div>
      </div>

      {statusMessage && (
        <span className="tiny" style={{ color: 'var(--accent)', fontWeight: 550 }}>
          {statusMessage}
        </span>
      )}

      {showManual && (
        <div className="sound-folder-manual">
          <div className="row between">
            <span className="small muted">Enter folder absolute path:</span>
            <span className="tiny faint">e.g. /Users/you/Downloads/sound library</span>
          </div>
          <input
            type="text"
            value={value}
            placeholder="/Users/you/Downloads/sound library"
            onChange={(e) => onChange(e.target.value)}
            autoFocus
          />
          <div className="sound-folder-presets">
            <span className="sound-folder-presets-label">Presets:</span>
            <button
              type="button"
              className="sound-folder-preset-chip"
              onClick={() => onChange('')}
            >
              workspace/sfx/ (Default)
            </button>
            <button
              type="button"
              className="sound-folder-preset-chip"
              onClick={() => onChange('/Users/kariakistephen/Downloads/sound library')}
            >
              ~/Downloads/sound library
            </button>
            <button
              type="button"
              className="sound-folder-preset-chip"
              onClick={() => onChange('/Users/kariakistephen/Music')}
            >
              ~/Music
            </button>
          </div>
        </div>
      )}

      <span className="tiny muted">
        Every audio file in this folder (and in workspace/sfx/) is analysed and sorted into categories. Use only sounds you have rights to.
      </span>

      {/* Helper Modal when native dialog is unavailable or for easy selection */}
      {showModal && (
        <div className="sound-folder-modal-backdrop" onClick={() => setShowModal(false)}>
          <div className="sound-folder-modal" onClick={(e) => e.stopPropagation()}>
            <div className="row" style={{ gap: 14, alignItems: 'center' }}>
              <img
                src="/sound-folder.jpg"
                alt="Sound Library"
                style={{ width: 64, height: 64, borderRadius: 10, objectFit: 'cover' }}
              />
              <div className="stack" style={{ gap: 2 }}>
                <h3 style={{ margin: 0 }}>Select Sound Library Folder</h3>
                <span className="tiny muted">
                  Point ClipForge to your local sound effects collection
                </span>
              </div>
            </div>

            <div className="field">
              <span>Folder path on your computer</span>
              <input
                type="text"
                value={tempPath}
                onChange={(e) => setTempPath(e.target.value)}
                placeholder="/Users/you/Downloads/sound library"
              />
            </div>

            <div className="sound-folder-presets">
              <span className="sound-folder-presets-label">Presets:</span>
              <button
                type="button"
                className="sound-folder-preset-chip"
                onClick={() => setTempPath('')}
              >
                Default (workspace/sfx/)
              </button>
              <button
                type="button"
                className="sound-folder-preset-chip"
                onClick={() => setTempPath('/Users/kariakistephen/Downloads/sound library')}
              >
                ~/Downloads/sound library
              </button>
              <button
                type="button"
                className="sound-folder-preset-chip"
                onClick={() => setTempPath('/Users/kariakistephen/Music')}
              >
                ~/Music
              </button>
            </div>

            <div className="divider" />

            <div className="row between">
              <div>
                <input
                  type="file"
                  ref={fileInputRef}
                  style={{ display: 'none' }}
                  // @ts-expect-error webkitdirectory is standard in browsers
                  webkitdirectory=""
                  directory=""
                  onChange={handleHtml5FolderSelect}
                />
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => fileInputRef.current?.click()}
                >
                  Browse Files…
                </button>
              </div>
              <div className="row" style={{ gap: 8 }}>
                <button
                  type="button"
                  className="btn ghost sm"
                  onClick={() => setShowModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn primary sm"
                  onClick={() => {
                    onChange(tempPath.trim())
                    setShowModal(false)
                  }}
                >
                  Save Folder
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
