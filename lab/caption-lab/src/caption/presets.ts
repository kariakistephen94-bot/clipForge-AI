// Caption presets: a default config plus named overlays. Each preset changes sizes, animation intensity,
// beat length, active-word behaviour, layout, colours and entrance/exit choices.

import { DEFAULT_THEME, FONT_CHOICES } from './theme.ts'
import type { CaptionConfig } from './types.ts'

export type DeepPartial<T> = { [K in keyof T]?: T[K] extends object ? DeepPartial<T[K]> : T[K] }

export const DEFAULT_CONFIG: CaptionConfig = {
  canvas: { width: 1080, height: 1920 },
  segmentation: { minWords: 2, maxWords: 6, minDuration: 0.7, maxDuration: 2.2, pauseBreak: 0.38, splitAfterHero: true },
  importance: { heroThreshold: 0.75, strongThreshold: 0.58, mediumThreshold: 0.4, maxStrongPerPhrase: 1, heroCooldown: 1.2 },
  ...DEFAULT_THEME,
  highlight: { mode: 'ACTIVE_WORD', invert: false },
  animation: {
    intensity: 'medium',
    revealMode: 'phrase',
    heroRevealOnSpeak: true,
    leadIn: 90,
    entranceDuration: 220,
    heroEntranceDuration: 320,
    exitDuration: 180,
    overlap: 110,
    stagger: 45,
    speakDuration: 150,
    speakScale: 1.08,
    heroFlashMs: 150,
    minHeroHold: 0.6,
    maxHandoverDelay: 0.2,
    supportingEntrance: 'AUTO',
    heroEntrance: 'AUTO',
    exit: 'AUTO',
    speak: 'AUTO',
    distance: 46,
  },
  layout: {
    strategy: 'AUTO',
    prefer: 'centered',
    anchorY: 0.6,
    maxWidth: 0.9,
    maxLines: 3,
    safe: { top: 260, bottom: 440, left: 70, right: 150 },
    editorialOffset: 0.08,
  },
}

export interface CaptionPreset {
  id: string
  name: string
  description: string
  patch: DeepPartial<CaptionConfig>
}

const font = (label: string) => FONT_CHOICES.find((f) => f.label === label)!.family

export const PRESETS: CaptionPreset[] = [
  {
    id: 'editorial-hero',
    name: 'Editorial Hero',
    description: 'Clean white editorial type. Supporting words small, the key word huge. Calm, confident motion.',
    patch: {
      typography: { fontFamily: font('Inter'), fontWeight: 800, baseSize: 74, textCase: 'hero-upper', letterSpacing: -0.035,
        scales: { normal: 1, medium: 1.1, strong: 1.45, hero: 2.45 }, lineHeight: 1 },
      style: { strokeWidth: 0, shadowBlur: 22, shadowOpacity: 0.6, shadowOffsetY: 6 },
      colors: { hero: 'primary', strong: 'primary' },
      highlight: { mode: 'EMPHASIS_ONLY' },
      animation: { intensity: 'medium', revealMode: 'phrase', heroEntranceDuration: 360 },
      layout: { prefer: 'editorial', anchorY: 0.58 },
    },
  },
  {
    id: 'viral-bold',
    name: 'Viral Bold',
    description: 'Heavy all-caps with an outline, yellow active word, punchy pops.',
    patch: {
      typography: { fontFamily: font('Montserrat'), fontWeight: 900, baseSize: 78, textCase: 'upper', letterSpacing: -0.02,
        scales: { normal: 1, medium: 1.15, strong: 1.4, hero: 2.0 } },
      style: { strokeWidth: 9, shadowBlur: 14, shadowOpacity: 0.5 },
      colors: { hero: 'active', strong: 'primary' },
      highlight: { mode: 'ACTIVE_WORD' },
      animation: { intensity: 'medium', revealMode: 'phrase' },
      layout: { prefer: 'centered', anchorY: 0.62 },
      segmentation: { maxWords: 4 },
    },
  },
  {
    id: 'podcast-punch',
    name: 'Podcast Punch',
    description: 'Words appear as they are spoken; hero words land big in orange. Built for talking heads.',
    patch: {
      typography: { fontFamily: font('Poppins'), fontWeight: 800, baseSize: 70, textCase: 'hero-upper',
        scales: { normal: 1, medium: 1.1, strong: 1.35, hero: 2.2 } },
      style: { strokeWidth: 0, shadowBlur: 20, shadowOpacity: 0.7 },
      colors: { hero: 'secondary', strong: 'primary' },
      highlight: { mode: 'ACTIVE_WORD' },
      animation: { intensity: 'medium', revealMode: 'word', supportingEntrance: 'SOFT_POP', exit: 'FADE_OUT' },
      layout: { prefer: 'centered', anchorY: 0.66 },
      segmentation: { maxWords: 5 },
    },
  },
  {
    id: 'fast-tiktok',
    name: 'Fast TikTok',
    description: 'Short, stacked, high-energy beats. Green active word, punch on every spoken word.',
    patch: {
      typography: { fontFamily: font('Anton'), fontWeight: 400, baseSize: 96, textCase: 'upper', letterSpacing: 0,
        scales: { normal: 1, medium: 1.12, strong: 1.35, hero: 1.9 }, lineHeight: 0.98 },
      style: { strokeWidth: 10, shadowBlur: 10, shadowOpacity: 0.55 },
      colors: { active: '#3DDC84', hero: 'active', strong: 'primary' },
      highlight: { mode: 'ACTIVE_WORD' },
      animation: { intensity: 'high', revealMode: 'word', leadIn: 40, entranceDuration: 170, exitDuration: 120, speakScale: 1.12 },
      layout: { prefer: 'stacked', anchorY: 0.6 },
      segmentation: { minWords: 1, maxWords: 3, maxDuration: 1.4, minDuration: 0.5 },
    },
  },
  {
    id: 'minimal-clean',
    name: 'Minimal Clean',
    description: 'Sentence case on a soft dark plate, gentle fades. Readable anywhere, never shouty.',
    patch: {
      typography: { fontFamily: font('Inter'), fontWeight: 700, baseSize: 60, textCase: 'as-is', letterSpacing: -0.01,
        scales: { normal: 1, medium: 1.05, strong: 1.15, hero: 1.35 }, lineHeight: 1.18 },
      style: { strokeWidth: 0, shadowBlur: 0, shadowOpacity: 0, plate: 'line', plateOpacity: 0.62, plateRadius: 16, platePadding: 18 },
      colors: { active: '#FFE27A', hero: 'primary', strong: 'primary' },
      highlight: { mode: 'ACTIVE_WORD' },
      animation: { intensity: 'low', revealMode: 'phrase', heroRevealOnSpeak: false, speak: 'NONE', exit: 'FADE_OUT' },
      layout: { prefer: 'centered', anchorY: 0.72, maxWidth: 0.86 },
      segmentation: { maxWords: 7, maxDuration: 2.6 },
    },
  },
  {
    id: 'word-explosion',
    name: 'Word Explosion',
    description: 'One or two words at a time, huge and colourful, with punches and blur-outs.',
    patch: {
      typography: { fontFamily: font('Archivo Black'), fontWeight: 400, baseSize: 104, textCase: 'upper', letterSpacing: -0.02,
        scales: { normal: 1, medium: 1.2, strong: 1.5, hero: 2.3 } },
      style: { strokeWidth: 11, shadowBlur: 16, shadowOpacity: 0.6 },
      colors: { hero: 'accent', strong: 'secondary' },
      highlight: { mode: 'ACTIVE_WORD' },
      animation: { intensity: 'high', revealMode: 'word', heroEntrance: 'HERO_PUNCH', exit: 'QUICK_BLUR_OUT', leadIn: 30 },
      layout: { prefer: 'stacked', anchorY: 0.58 },
      segmentation: { minWords: 1, maxWords: 2, maxDuration: 1.0, minDuration: 0.45, splitAfterHero: true },
      importance: { maxStrongPerPhrase: 1 },
    },
  },
]

export function deepMerge<T>(base: T, patch: DeepPartial<T> | undefined): T {
  if (!patch) return structuredClone(base)
  const out = structuredClone(base) as Record<string, unknown>
  for (const [k, v] of Object.entries(patch as Record<string, unknown>)) {
    const cur = out[k]
    if (v && typeof v === 'object' && !Array.isArray(v) && cur && typeof cur === 'object' && !Array.isArray(cur)) {
      out[k] = deepMerge(cur, v as DeepPartial<typeof cur>)
    } else if (v !== undefined) out[k] = v
  }
  return out as T
}

export function presetConfig(id: string): CaptionConfig {
  const p = PRESETS.find((x) => x.id === id) ?? PRESETS[0]
  return deepMerge(DEFAULT_CONFIG, p.patch)
}
