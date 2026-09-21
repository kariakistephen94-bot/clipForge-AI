import { useEffect, useRef } from 'react'
import { getCaptionFrameState } from '../caption/engine.ts'
import { drawCaptions, drawDebug, hitTest } from '../caption/render/canvas.ts'
import type { CaptionFrameState, CompiledCaptions } from '../caption/types.ts'
import type { PlaybackClock } from './playback.ts'

export type Backdrop = 'dark' | 'bright' | 'busy' | 'video'

interface Props {
  compiled: CompiledCaptions
  capRatio: number
  clock: PlaybackClock
  backdrop: Backdrop
  videoUrl: string | null
  debug: boolean
  selected: number | null
  onSelect: (index: number | null) => void
  frameRef: React.MutableRefObject<CaptionFrameState | null>
}

/** 9:16 preview. Draws every animation frame from the clock; React props are read through a ref. */
export default function CaptionPreview(props: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const live = useRef(props)
  live.current = props

  // attach / detach the background video as the master clock
  useEffect(() => {
    const v = videoRef.current
    if (props.backdrop === 'video' && props.videoUrl && v) {
      const onReady = () => props.clock.attachVideo(v)
      if (v.readyState >= 1) onReady()
      else v.addEventListener('loadedmetadata', onReady, { once: true })
      return () => {
        v.removeEventListener('loadedmetadata', onReady)
        props.clock.attachVideo(null)
      }
    }
    props.clock.attachVideo(null)
  }, [props.backdrop, props.videoUrl, props.clock])

  // keep the canvas backing store at screen resolution
  useEffect(() => {
    const wrap = wrapRef.current!
    const canvas = canvasRef.current!
    const fit = () => {
      const r = wrap.getBoundingClientRect()
      const h = Math.min(r.height, (r.width * 16) / 9)
      const w = (h * 9) / 16
      canvas.style.width = `${w}px`
      canvas.style.height = `${h}px`
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(wrap)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    let raf = 0
    const ctx = canvasRef.current!.getContext('2d')!
    const loop = () => {
      const p = live.current
      const { width: W, height: H } = p.compiled.config.canvas
      const k = ctx.canvas.width / W
      const t = p.clock.tick()
      const frame = getCaptionFrameState(p.compiled, t)
      p.frameRef.current = frame
      ctx.setTransform(k, 0, 0, k, 0, 0)
      drawBackdrop(ctx, p.backdrop, videoRef.current, t, W, H)
      drawCaptions(ctx, frame, p.compiled.config, p.capRatio)
      if (p.debug) drawDebug(ctx, frame, p.compiled, p.selected)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [])

  const onClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const c = e.currentTarget
    const r = c.getBoundingClientRect()
    const W = props.compiled.config.canvas.width
    const scale = W / r.width
    const frame = props.frameRef.current
    if (!frame) return
    props.onSelect(hitTest(frame, (e.clientX - r.left) * scale, (e.clientY - r.top) * scale))
  }

  return (
    <div className="preview-wrap" ref={wrapRef}>
      <canvas ref={canvasRef} className="preview-canvas" onClick={onClick} title="Click a word to edit it" />
      {props.videoUrl && (
        <video ref={videoRef} src={props.videoUrl} preload="auto" playsInline style={{ display: 'none' }} />
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- backdrops

function drawBackdrop(ctx: CanvasRenderingContext2D, kind: Backdrop, video: HTMLVideoElement | null, t: number, W: number, H: number) {
  if (kind === 'video' && video && video.readyState >= 2) {
    const s = Math.max(W / video.videoWidth, H / video.videoHeight)
    const w = video.videoWidth * s
    const h = video.videoHeight * s
    ctx.drawImage(video, (W - w) / 2, (H - h) / 2, w, h)
    return
  }
  const palettes: Record<Exclude<Backdrop, 'video'>, [string, string, string[]]> = {
    dark: ['#1d2436', '#07090e', ['rgba(91,140,255,0.22)', 'rgba(139,108,255,0.18)', 'rgba(45,212,191,0.12)']],
    bright: ['#fbf6ec', '#dfe8f3', ['rgba(255,196,120,0.55)', 'rgba(140,190,255,0.5)', 'rgba(255,255,255,0.8)']],
    busy: ['#ff5f6d', '#2c3e8f', ['rgba(255,230,0,0.65)', 'rgba(0,255,200,0.5)', 'rgba(255,255,255,0.6)']],
  }
  const [top, bottom, blobs] = palettes[kind === 'video' ? 'dark' : kind]
  const g = ctx.createLinearGradient(0, 0, 0, H)
  g.addColorStop(0, top)
  g.addColorStop(1, bottom)
  ctx.fillStyle = g
  ctx.fillRect(0, 0, W, H)
  // slow drifting light, so the backdrop feels like footage (deterministic in t)
  blobs.forEach((c, i) => {
    const x = W * (0.5 + 0.35 * Math.sin(t * 0.35 + i * 2.1))
    const y = H * (0.45 + 0.3 * Math.cos(t * 0.27 + i * 1.7))
    const r = W * (0.55 + 0.1 * i)
    const rg = ctx.createRadialGradient(x, y, 0, x, y, r)
    rg.addColorStop(0, c)
    rg.addColorStop(1, 'rgba(0,0,0,0)')
    ctx.fillStyle = rg
    ctx.fillRect(0, 0, W, H)
  })
  if (kind === 'busy') {
    ctx.fillStyle = 'rgba(255,255,255,0.18)'
    for (let i = 0; i < 14; i++) ctx.fillRect(((i * 97 + t * 40) % (W + 200)) - 100, 0, 38, H)
  }
}
