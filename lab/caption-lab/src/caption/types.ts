// Shared types for the kinetic caption engine.
// The engine is framework-free: no React, no DOM. Everything a renderer needs comes out of
// getCaptionFrameState(compiled, time), so Canvas, SVG, Remotion or an FFmpeg frame renderer can all use it.

export type Importance = 'normal' | 'medium' | 'strong' | 'hero'
export const IMPORTANCE_LEVELS: Importance[] = ['normal', 'medium', 'strong', 'hero']

export type LayoutStrategy =
  | 'ONE_LINE'
  | 'STACKED'
  | 'HERO_CENTER'
  | 'HERO_BOTTOM'
  | 'TWO_LINE'
  | 'THREE_LINE'
  | 'ASYMMETRIC_EDITORIAL'
  | 'WORD_FOCUS'

export type EntranceAnim =
  | 'POP'
  | 'SOFT_POP'
  | 'SLIDE_UP'
  | 'SLIDE_DOWN'
  | 'SLIDE_LEFT'
  | 'SLIDE_RIGHT'
  | 'SCALE_IN'
  | 'FADE_UP'
  | 'SPRING_IN'
  | 'BOUNCE_IN'
  | 'HERO_PUNCH'
  | 'NONE'

export type ExitAnim =
  | 'FADE_OUT'
  | 'SLIDE_UP_OUT'
  | 'SLIDE_DOWN_OUT'
  | 'SLIDE_LEFT_OUT'
  | 'SLIDE_RIGHT_OUT'
  | 'SHRINK_OUT'
  | 'QUICK_BLUR_OUT'
  | 'POP_OUT'
  | 'NONE'

export type SpeakAnim = 'PULSE' | 'LIFT' | 'PUNCH' | 'NONE'

export type HighlightMode =
  | 'ACTIVE_WORD'
  | 'ACTIVE_PHRASE'
  | 'EMPHASIS_ONLY'
  | 'KARAOKE_PROGRESS'
  | 'NO_COLOR_ANIMATION'

export type Intensity = 'low' | 'medium' | 'high'
/** When supporting words appear: all at once when the beat starts, line by line, or each as it is spoken. */
export type RevealMode = 'phrase' | 'line' | 'word'
export type ColorKey = 'primary' | 'active' | 'secondary' | 'accent'
export type TextCase = 'as-is' | 'upper' | 'lower' | 'hero-upper'
export type PlateMode = 'none' | 'word' | 'line'

/** Transcript input: one entry per spoken word. */
export interface InputWord {
  word: string
  start: number
  end: number
  speaker?: string
}

/** A normalised transcript word. `index` is its position in the whole transcript. */
export interface CaptionWord {
  index: number
  raw: string
  text: string
  start: number
  end: number
  speaker?: string
  sentenceEnd: boolean
  clauseEnd: boolean
}

export interface ImportanceResult {
  level: Importance
  score: number
  reasons: string[]
}

/** A caption beat: the words shown together. */
export interface Phrase {
  id: number
  words: number[] // global word indices
  start: number // first word starts speaking
  end: number // last word stops speaking
}

/** Manual per-word overrides from the lab (or, later, from an AI director). */
export interface WordOverride {
  importance?: Importance
  color?: string
  sizeScale?: number
  entrance?: EntranceAnim
  exit?: ExitAnim
  speak?: SpeakAnim
  dx?: number
  dy?: number
}
export type WordOverrides = Record<number, WordOverride>

export interface CaptionConfig {
  canvas: { width: number; height: number }
  segmentation: {
    minWords: number
    maxWords: number
    minDuration: number // s a beat stays on screen at least (if the next beat allows it)
    maxDuration: number // s of speech in one beat
    pauseBreak: number // s of silence that always ends a beat
    splitAfterHero: boolean // end the beat on the hero word when 2+ words of the sentence follow it
  }
  importance: {
    heroThreshold: number
    strongThreshold: number
    mediumThreshold: number
    maxStrongPerPhrase: number
    heroCooldown: number // s: no second hero this soon after the previous one
  }
  typography: {
    fontFamily: string
    fontWeight: number
    baseSize: number // px of a NORMAL word on a 1080x1920 canvas
    scales: Record<Importance, number>
    lineHeight: number // multiple of the line's largest font size
    letterSpacing: number // em
    textCase: TextCase
  }
  style: {
    strokeWidth: number // px at base size, scales with the word
    strokeColor: string
    shadowColor: string
    shadowBlur: number
    shadowOpacity: number
    shadowOffsetY: number
    plate: PlateMode
    plateColor: string
    plateOpacity: number
    plateRadius: number
    platePadding: number
  }
  colors: {
    primary: string
    active: string
    secondary: string
    accent: string
    hero: ColorKey
    strong: ColorKey
    supportingOpacity: number // 0..1: supporting (normal) words can sit back a little
  }
  highlight: {
    mode: HighlightMode
    invert: boolean // active word turns primary while the rest of the beat uses the active colour
  }
  animation: {
    intensity: Intensity
    revealMode: RevealMode
    heroRevealOnSpeak: boolean // hero word enters when it is spoken, not with the beat
    leadIn: number // ms a beat appears before its first word is spoken
    entranceDuration: number // ms
    heroEntranceDuration: number // ms
    exitDuration: number // ms
    overlap: number // ms the old beat's exit overlaps the new beat's entrance
    stagger: number // ms between supporting words entering together
    speakDuration: number // ms of the "being spoken" reaction
    speakScale: number // peak scale of that reaction
    heroFlashMs: number // ms a hero word shows the accent colour after entering
    minHeroHold: number // s a hero word stays on screen at least...
    maxHandoverDelay: number // s ...even if that delays the next beat by up to this much
    supportingEntrance: EntranceAnim | 'AUTO'
    heroEntrance: EntranceAnim | 'AUTO'
    exit: ExitAnim | 'AUTO'
    speak: SpeakAnim | 'AUTO'
    distance: number // px travelled by slide / fade-up animations
  }
  layout: {
    strategy: LayoutStrategy | 'AUTO'
    prefer: 'centered' | 'editorial' | 'stacked' // what AUTO leans towards
    anchorY: number // 0..1: centre of the caption block
    maxWidth: number // 0..1 of the safe-zone width
    maxLines: number
    safe: { top: number; bottom: number; left: number; right: number } // px
    editorialOffset: number // 0..1 of safe width: indent step for ASYMMETRIC_EDITORIAL
  }
}

/** Measures text width for a given font size; supplied by the renderer environment. */
export interface TextMeasurer {
  width(text: string, fontSize: number): number
}

export interface Box {
  x: number
  y: number
  w: number
  h: number
}

/** A word after layout and animation planning (static for the whole beat). */
export interface PlannedWord {
  index: number
  text: string
  importance: ImportanceResult
  fontSize: number
  width: number
  height: number
  x: number // centre, canvas px
  y: number // centre, canvas px
  line: number
  revealAt: number // s
  entrance: EntranceAnim
  exit: ExitAnim
  speak: SpeakAnim
  entranceMs: number
  baseColor: string
  customColor?: string
  opacityBase: number
}

export interface PlannedPhrase {
  id: number
  words: PlannedWord[]
  lines: number[][] // indices into words
  strategy: LayoutStrategy
  box: Box
  spokenStart: number
  spokenEnd: number
  showAt: number // first entrance starts
  exitStart: number
  hideAt: number
  exitMs: number
}

export interface CompiledCaptions {
  config: CaptionConfig
  words: CaptionWord[]
  importance: ImportanceResult[]
  phrases: PlannedPhrase[]
  duration: number
}

/** Everything needed to draw one word on one frame. */
export interface WordFrame {
  index: number
  phraseId: number
  line: number
  text: string
  x: number
  y: number
  width: number
  height: number
  fontSize: number
  scale: number
  rotation: number // degrees
  opacity: number
  blur: number // px
  color: string
  /** KARAOKE_PROGRESS: 0..1 of the word filled with `color`, the rest drawn in `base`. */
  fill?: { progress: number; base: string }
  active: boolean
  spoken: boolean
  importance: Importance
  entrance: EntranceAnim
  exit: ExitAnim
  speak: SpeakAnim
  entranceProgress: number
  exitProgress: number
  speakProgress: number
}

export interface PhraseFrame {
  id: number
  strategy: LayoutStrategy
  box: Box
  exiting: boolean
}

export interface CaptionFrameState {
  time: number
  phrases: PhraseFrame[]
  words: WordFrame[]
  activeWord: number | null
}
