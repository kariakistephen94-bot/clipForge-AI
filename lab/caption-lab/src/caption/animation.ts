// Stage 6: animation -- reusable primitives (pure functions of elapsed time) and the director that picks
// which primitive each word uses.
//
// Rules the director follows (controlled, never random per word):
//   * all supporting words of a beat share ONE entrance; the hero gets its own, stronger one
//   * the choice rotates between beats deterministically (same input -> same video), so scenes differ
//     but stay coherent
//   * a word gets at most one entrance + one "being spoken" reaction; a hero that enters exactly when it is
//     spoken gets no extra reaction on top
//   * intensity (low / medium / high) decides the pools

import type { CaptionConfig, EntranceAnim, ExitAnim, Importance, Intensity, SpeakAnim } from './types.ts'

export interface Transform {
  opacity: number
  scale: number
  dx: number
  dy: number
  rotation: number
  blur: number
}

export const IDENTITY: Transform = { opacity: 1, scale: 1, dx: 0, dy: 0, rotation: 0, blur: 0 }

// --------------------------------------------------------------------------- easing

export const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x)
export const easeOutCubic = (x: number) => 1 - (1 - clamp01(x)) ** 3
export const easeInCubic = (x: number) => clamp01(x) ** 3
export const easeInOutCubic = (x: number) => {
  x = clamp01(x)
  return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2
}
export function easeOutBounce(x: number): number {
  x = clamp01(x)
  const n1 = 7.5625
  const d1 = 2.75
  if (x < 1 / d1) return n1 * x * x
  if (x < 2 / d1) return n1 * (x -= 1.5 / d1) * x + 0.75
  if (x < 2.5 / d1) return n1 * (x -= 2.25 / d1) * x + 0.9375
  return n1 * (x -= 2.625 / d1) * x + 0.984375
}

/** Underdamped spring from `from` to 1; t in seconds. Settles in roughly `settle` seconds. */
export function spring(t: number, from: number, settle = 0.35, bounciness = 0.35): number {
  if (t <= 0) return from
  const zeta = 1 - bounciness // damping ratio (<1 bounces)
  const omega = 4.6 / (zeta * settle) // natural frequency so the envelope decays to ~1% by `settle`
  const wd = omega * Math.sqrt(Math.max(1e-6, 1 - zeta * zeta))
  const env = Math.exp(-zeta * omega * t)
  return 1 - (1 - from) * env * (Math.cos(wd * t) + ((zeta * omega) / wd) * Math.sin(wd * t))
}

/** Piecewise keyframes [progress, value], smoothly eased between keys. */
export function keyframes(p: number, keys: [number, number][]): number {
  if (p <= keys[0][0]) return keys[0][1]
  for (let i = 1; i < keys.length; i++) {
    const [p1, v1] = keys[i]
    const [p0, v0] = keys[i - 1]
    if (p <= p1) return v0 + (v1 - v0) * easeInOutCubic((p - p0) / (p1 - p0))
  }
  return keys[keys.length - 1][1]
}

// --------------------------------------------------------------------------- primitives

/** Entrance at `ms` into an animation lasting `dur` ms. `dist` = travel in px. */
export function entrance(kind: EntranceAnim, ms: number, dur: number, dist: number): Transform {
  if (kind === 'NONE' || ms >= dur) return IDENTITY
  const p = clamp01(ms / Math.max(1, dur))
  const t = ms / 1000
  const fadeIn = (share: number) => clamp01(p / share)
  switch (kind) {
    case 'POP': // 0ms .82/0 -> 70ms 1.10/1 -> 130ms .97 -> 180ms 1.00 (at 180 ms; stretches with duration)
      return { ...IDENTITY, scale: keyframes(p, [[0, 0.82], [0.39, 1.1], [0.72, 0.97], [1, 1]]), opacity: fadeIn(0.39) }
    case 'SOFT_POP':
      return { ...IDENTITY, scale: keyframes(p, [[0, 0.9], [0.5, 1.04], [1, 1]]), opacity: fadeIn(0.45) }
    case 'SLIDE_UP':
      return { ...IDENTITY, dy: dist * (1 - easeOutCubic(p)), opacity: fadeIn(0.55) }
    case 'SLIDE_DOWN':
      return { ...IDENTITY, dy: -dist * (1 - easeOutCubic(p)), opacity: fadeIn(0.55) }
    case 'SLIDE_LEFT':
      return { ...IDENTITY, dx: dist * 1.4 * (1 - easeOutCubic(p)), opacity: fadeIn(0.55) }
    case 'SLIDE_RIGHT':
      return { ...IDENTITY, dx: -dist * 1.4 * (1 - easeOutCubic(p)), opacity: fadeIn(0.55) }
    case 'SCALE_IN':
      return { ...IDENTITY, scale: 0.6 + 0.4 * easeOutCubic(p), opacity: fadeIn(0.6) }
    case 'FADE_UP':
      return { ...IDENTITY, dy: dist * 0.5 * (1 - easeOutCubic(p)), opacity: easeOutCubic(p) }
    case 'SPRING_IN':
      return { ...IDENTITY, scale: spring(t, 0.55, dur / 1000), opacity: fadeIn(0.3) }
    case 'BOUNCE_IN':
      return { ...IDENTITY, dy: -dist * 1.6 * (1 - easeOutBounce(p)), opacity: fadeIn(0.25) }
    case 'HERO_PUNCH': // .72 -> 1.12 -> .98 -> 1, rising slightly into place
      return {
        ...IDENTITY,
        scale: keyframes(p, [[0, 0.72], [0.35, 1.12], [0.65, 0.98], [1, 1]]),
        dy: keyframes(p, [[0, dist * 0.45], [0.35, 0], [1, 0]]),
        opacity: fadeIn(0.25),
      }
  }
  return IDENTITY
}

export function exit(kind: ExitAnim, ms: number, dur: number, dist: number): Transform {
  if (kind === 'NONE' || ms <= 0) return IDENTITY
  const p = clamp01(ms / Math.max(1, dur))
  const fade = 1 - easeInCubic(p) * 0.3 - p * 0.7
  switch (kind) {
    case 'FADE_OUT':
      return { ...IDENTITY, opacity: 1 - p }
    case 'SLIDE_UP_OUT':
      return { ...IDENTITY, dy: -dist * 0.6 * easeInCubic(p) - dist * 0.25 * p, opacity: fade }
    case 'SLIDE_DOWN_OUT':
      return { ...IDENTITY, dy: dist * 0.6 * easeInCubic(p) + dist * 0.25 * p, opacity: fade }
    case 'SLIDE_LEFT_OUT':
      return { ...IDENTITY, dx: -dist * 1.4 * easeInCubic(p), opacity: fade }
    case 'SLIDE_RIGHT_OUT':
      return { ...IDENTITY, dx: dist * 1.4 * easeInCubic(p), opacity: fade }
    case 'SHRINK_OUT':
      return { ...IDENTITY, scale: 1 - 0.45 * easeInCubic(p), opacity: fade }
    case 'QUICK_BLUR_OUT':
      return { ...IDENTITY, blur: 16 * p, scale: 1 + 0.06 * p, opacity: 1 - p }
    case 'POP_OUT':
      return { ...IDENTITY, scale: keyframes(p, [[0, 1], [0.35, 1.08], [1, 0.3]]), opacity: keyframes(p, [[0, 1], [0.35, 1], [1, 0]]) }
  }
  return IDENTITY
}

/** Reaction while a word is spoken: `ms` since the word started. */
export function speak(kind: SpeakAnim, ms: number, dur: number, peak: number): Transform {
  if (kind === 'NONE' || ms < 0 || ms >= dur) return IDENTITY
  const p = ms / dur
  const bell = Math.sin(Math.PI * p)
  switch (kind) {
    case 'PULSE': // 1 -> peak -> 1
      return { ...IDENTITY, scale: 1 + (peak - 1) * bell }
    case 'LIFT':
      return { ...IDENTITY, scale: 1 + (peak - 1) * 0.5 * bell, dy: -12 * bell }
    case 'PUNCH':
      return { ...IDENTITY, scale: keyframes(p, [[0, 1], [0.3, 1 + (peak - 1) * 1.4], [0.6, 0.99], [1, 1]]) }
  }
  return IDENTITY
}

export function combine(...ts: Transform[]): Transform {
  return ts.reduce(
    (a, b) => ({
      opacity: a.opacity * b.opacity,
      scale: a.scale * b.scale,
      dx: a.dx + b.dx,
      dy: a.dy + b.dy,
      rotation: a.rotation + b.rotation,
      blur: a.blur + b.blur,
    }),
    IDENTITY,
  )
}

// --------------------------------------------------------------------------- director

interface Pools {
  supporting: EntranceAnim[]
  strong: EntranceAnim
  hero: EntranceAnim[]
  exit: ExitAnim[]
  speak: SpeakAnim
}

export const INTENSITY_POOLS: Record<Intensity, Pools> = {
  low: { supporting: ['FADE_UP'], strong: 'SOFT_POP', hero: ['SCALE_IN', 'SOFT_POP'], exit: ['FADE_OUT'], speak: 'PULSE' },
  medium: {
    supporting: ['SOFT_POP', 'SLIDE_UP', 'FADE_UP'],
    strong: 'POP',
    hero: ['POP', 'SPRING_IN', 'HERO_PUNCH'],
    exit: ['SLIDE_UP_OUT', 'FADE_OUT', 'SHRINK_OUT'],
    speak: 'PULSE',
  },
  high: {
    supporting: ['POP', 'SLIDE_UP', 'SLIDE_LEFT', 'SLIDE_RIGHT'],
    strong: 'POP',
    hero: ['HERO_PUNCH', 'BOUNCE_IN', 'SPRING_IN'],
    exit: ['POP_OUT', 'QUICK_BLUR_OUT', 'SLIDE_UP_OUT'],
    speak: 'PUNCH',
  },
}

/** Deterministic pick that changes from beat to beat without repeating the previous beat's choice. */
export function rotate<T>(pool: T[], beat: number, salt = 0): T {
  if (pool.length === 1) return pool[0]
  const h = (beat * 2654435761 + salt * 40503) >>> 0
  const i = h % pool.length
  const prev = beat > 0 ? ((beat - 1) * 2654435761 + salt * 40503) >>> 0 : -1
  return prev >= 0 && prev % pool.length === i ? pool[(i + 1) % pool.length] : pool[i]
}

export interface WordAnimationPlan {
  entrance: EntranceAnim
  exit: ExitAnim
  speak: SpeakAnim
  entranceMs: number
}

export function planAnimations(
  beat: number,
  level: Importance,
  entersWhenSpoken: boolean,
  config: CaptionConfig,
): WordAnimationPlan {
  const a = config.animation
  const pools = INTENSITY_POOLS[a.intensity]
  const isHero = level === 'hero'
  let ent: EntranceAnim
  if (isHero) ent = a.heroEntrance !== 'AUTO' ? a.heroEntrance : rotate(pools.hero, beat, 1)
  else if (level === 'strong') ent = a.supportingEntrance !== 'AUTO' ? a.supportingEntrance : pools.strong
  else ent = a.supportingEntrance !== 'AUTO' ? a.supportingEntrance : rotate(pools.supporting, beat, 2)
  const ex = a.exit !== 'AUTO' ? a.exit : rotate(pools.exit, beat, 3)
  let sp: SpeakAnim = a.speak !== 'AUTO' ? a.speak : pools.speak
  if (entersWhenSpoken && (isHero || config.animation.revealMode === 'word')) sp = 'NONE' // one animation at a time
  return { entrance: ent, exit: ex, speak: sp, entranceMs: isHero ? a.heroEntranceDuration : a.entranceDuration }
}
