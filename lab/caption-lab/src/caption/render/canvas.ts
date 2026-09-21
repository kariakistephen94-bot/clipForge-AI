// Stage 8: rendering to a 2D canvas. Draws a CaptionFrameState; makes no decisions of its own.
// (A future FFmpeg/Remotion/SVG renderer implements the same idea against the same frame state.)

import type { CaptionConfig, CaptionFrameState, CompiledCaptions, TextMeasurer, WordFrame } from '../types.ts'

type Ctx = CanvasRenderingContext2D

const fontString = (weight: number, size: number, family: string) => `${weight} ${size}px ${family}`

function scratchContext(): Ctx {
  const c = document.createElement('canvas')
  c.width = c.height = 8
  return c.getContext('2d')!
}

/** Canvas-based TextMeasurer. Widths scale linearly with size, so each text is measured once at 100px. */
export function createCanvasMeasurer(typo: CaptionConfig['typography']): TextMeasurer & { capRatio: number } {
  const ctx = scratchContext()
  ctx.font = fontString(typo.fontWeight, 100, typo.fontFamily)
  ctx.letterSpacing = `${typo.letterSpacing * 100}px`
  const cache = new Map<string, number>()
  const capRatio = ctx.measureText('H').actualBoundingBoxAscent / 100 || 0.72
  return {
    capRatio,
    width(text: string, size: number) {
      let w = cache.get(text)
      if (w === undefined) {
        w = ctx.measureText(text).width
        cache.set(text, w)
      }
      return (w * size) / 100
    },
  }
}

function withAlpha(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return hex
  const n = parseInt(m[1], 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${Math.max(0, Math.min(1, alpha))})`
}

function drawLinePlates(ctx: Ctx, frame: CaptionFrameState, config: CaptionConfig) {
  const st = config.style
  const groups = new Map<string, WordFrame[]>()
  for (const w of frame.words) {
    const k = `${w.phraseId}:${w.line}`
    if (!groups.has(k)) groups.set(k, [])
    groups.get(k)!.push(w)
  }
  for (const ws of groups.values()) {
    const pad = st.platePadding
    const x0 = Math.min(...ws.map((w) => w.x - (w.width * w.scale) / 2)) - pad * 1.4
    const x1 = Math.max(...ws.map((w) => w.x + (w.width * w.scale) / 2)) + pad * 1.4
    const h = Math.max(...ws.map((w) => w.fontSize * w.scale)) * 1.05 + pad
    const cy = ws.reduce((s, w) => s + w.y, 0) / ws.length
    const op = Math.max(...ws.map((w) => w.opacity))
    ctx.fillStyle = withAlpha(st.plateColor, st.plateOpacity * op)
    ctx.beginPath()
    ctx.roundRect(x0, cy - h / 2, x1 - x0, h, st.plateRadius)
    ctx.fill()
  }
}

function drawWord(ctx: Ctx, w: WordFrame, config: CaptionConfig, capRatio: number) {
  if (w.opacity <= 0.001 || w.scale <= 0.001) return
  const typo = config.typography
  const st = config.style
  ctx.save()
  ctx.globalAlpha = w.opacity
  ctx.translate(w.x, w.y)
  if (w.rotation) ctx.rotate((w.rotation * Math.PI) / 180)
  if (w.scale !== 1) ctx.scale(w.scale, w.scale)
  if (w.blur > 0.3) ctx.filter = `blur(${w.blur.toFixed(1)}px)`
  ctx.font = fontString(typo.fontWeight, w.fontSize, typo.fontFamily)
  ctx.letterSpacing = `${typo.letterSpacing * w.fontSize}px`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'alphabetic'
  const by = (capRatio * w.fontSize) / 2 // baseline that centres cap height on w.y

  if (st.plate === 'word') {
    const pad = st.platePadding
    ctx.fillStyle = withAlpha(st.plateColor, st.plateOpacity)
    ctx.beginPath()
    ctx.roundRect(-w.width / 2 - pad, -w.fontSize * 0.55 - pad / 2, w.width + pad * 2, w.fontSize * 1.1 + pad, st.plateRadius)
    ctx.fill()
  }

  const stroke = st.strokeWidth > 0 ? st.strokeWidth * (w.fontSize / typo.baseSize) : 0
  if (st.shadowOpacity > 0 && st.shadowBlur >= 0) {
    ctx.shadowColor = withAlpha(st.shadowColor, st.shadowOpacity)
    ctx.shadowBlur = st.shadowBlur * (w.fontSize / typo.baseSize) ** 0.5
    ctx.shadowOffsetY = st.shadowOffsetY * (w.fontSize / typo.baseSize) ** 0.5
  }
  if (stroke > 0) {
    ctx.lineJoin = 'round'
    ctx.miterLimit = 2
    ctx.lineWidth = stroke * 2 // half of it sits under the fill
    ctx.strokeStyle = st.strokeColor
    ctx.strokeText(w.text, 0, by)
    ctx.shadowColor = 'transparent' // the outline already carries the shadow
  }
  if (w.fill) {
    ctx.fillStyle = w.fill.base
    ctx.fillText(w.text, 0, by)
    ctx.shadowColor = 'transparent'
    ctx.save()
    ctx.beginPath()
    ctx.rect(-w.width / 2 - 2, -w.fontSize, (w.width + 4) * w.fill.progress, w.fontSize * 2)
    ctx.clip()
    ctx.fillStyle = w.color
    ctx.fillText(w.text, 0, by)
    ctx.restore()
  } else {
    ctx.fillStyle = w.color
    ctx.fillText(w.text, 0, by)
  }
  ctx.restore()
}

export function drawCaptions(ctx: Ctx, frame: CaptionFrameState, config: CaptionConfig, capRatio: number) {
  if (config.style.plate === 'line') drawLinePlates(ctx, frame, config)
  for (const w of frame.words) drawWord(ctx, w, config, capRatio)
}

/** Word under a canvas point (in 1080x1920 canvas units), for click-to-edit. */
export function hitTest(frame: CaptionFrameState, x: number, y: number): number | null {
  for (let i = frame.words.length - 1; i >= 0; i--) {
    const w = frame.words[i]
    const hw = (w.width * w.scale) / 2 + 10
    const hh = (w.fontSize * w.scale) / 2 + 10
    if (Math.abs(x - w.x) <= hw && Math.abs(y - w.y) <= hh) return w.index
  }
  return null
}

// --------------------------------------------------------------------------- debug overlay

const LEVEL_COLORS = { normal: '#94a3b8', medium: '#38bdf8', strong: '#f59e0b', hero: '#f43f5e' }

export function drawDebug(ctx: Ctx, frame: CaptionFrameState, compiled: CompiledCaptions, selected: number | null) {
  const { config } = compiled
  const { width, height } = config.canvas
  const s = config.layout.safe
  ctx.save()
  // safe zones
  ctx.fillStyle = 'rgba(244, 63, 94, 0.13)'
  ctx.fillRect(0, 0, width, s.top)
  ctx.fillRect(0, height - s.bottom, width, s.bottom)
  ctx.fillRect(0, s.top, s.left, height - s.top - s.bottom)
  ctx.fillRect(width - s.right, s.top, s.right, height - s.top - s.bottom)
  ctx.setLineDash([14, 10])
  ctx.lineWidth = 3
  ctx.strokeStyle = 'rgba(244, 63, 94, 0.7)'
  ctx.strokeRect(s.left, s.top, width - s.left - s.right, height - s.top - s.bottom)
  // anchor line
  ctx.strokeStyle = 'rgba(255,255,255,0.25)'
  ctx.beginPath()
  ctx.moveTo(s.left, height * config.layout.anchorY)
  ctx.lineTo(width - s.right, height * config.layout.anchorY)
  ctx.stroke()
  ctx.font = '600 24px ui-monospace, Menlo, monospace'
  ctx.textBaseline = 'top'
  ctx.fillStyle = 'rgba(244, 63, 94, 0.9)'
  ctx.fillText('SAFE ZONE', s.left + 8, s.top + 8)

  // beat boxes
  for (const p of frame.phrases) {
    ctx.setLineDash([10, 8])
    ctx.strokeStyle = p.exiting ? 'rgba(148,163,184,0.6)' : 'rgba(45, 212, 191, 0.9)'
    ctx.strokeRect(p.box.x - 12, p.box.y - 12, p.box.w + 24, p.box.h + 24)
    ctx.setLineDash([])
    ctx.fillStyle = 'rgba(45, 212, 191, 0.95)'
    ctx.fillText(`beat #${p.id} · ${p.strategy}${p.exiting ? ' · exiting' : ''}`, p.box.x - 12, p.box.y - 44)
  }
  // word boxes + labels
  ctx.setLineDash([])
  for (const w of frame.words) {
    const bw = w.width * w.scale
    const bh = w.fontSize * w.scale * 0.9
    ctx.strokeStyle = w.index === selected ? '#e879f9' : LEVEL_COLORS[w.importance]
    ctx.lineWidth = w.index === selected ? 6 : w.active ? 4 : 2
    ctx.strokeRect(w.x - bw / 2, w.y - bh / 2, bw, bh)
    const cw = compiled.words[w.index]
    const label = `${w.importance.toUpperCase()} ${cw.start.toFixed(2)}–${cw.end.toFixed(2)} ${w.entrance}${w.active ? ' ●' : ''}`
    ctx.font = '600 19px ui-monospace, Menlo, monospace'
    const lw = ctx.measureText(label).width
    ctx.fillStyle = 'rgba(0,0,0,0.72)'
    ctx.fillRect(w.x - lw / 2 - 5, w.y + bh / 2 + 4, lw + 10, 26)
    ctx.fillStyle = LEVEL_COLORS[w.importance]
    ctx.fillText(label, w.x - lw / 2, w.y + bh / 2 + 8)
  }
  // clock
  ctx.font = '700 30px ui-monospace, Menlo, monospace'
  ctx.fillStyle = 'rgba(0,0,0,0.6)'
  ctx.fillRect(width - 260, 24, 236, 48)
  ctx.fillStyle = '#fff'
  ctx.fillText(`t = ${frame.time.toFixed(3)}s`, width - 248, 34)
  ctx.restore()
}
