// The caption engine: runs the pipeline once (compile), then answers "what does frame t look like?"
//
//   transcript -> words (timeline) -> scores (importance analyzer) -> beats (segmenter)
//   -> levels (importance) -> layout -> animation plan            = compileCaptions()
//   time -> visible beats, visible words, active word, transforms, colours = getCaptionFrameState()
//
// getCaptionFrameState is a pure function of (compiled, time): no clocks, timers or DOM. The same call
// renders a 60 fps browser preview or frame N of a 24/25/30/60 fps export identically.

import { combine, entrance, exit, planAnimations, speak } from './animation.ts'
import { HeuristicImportanceAnalyzer, assignLevels, type ImportanceAnalyzer } from './importance.ts'
import { layoutPhrase } from './layout.ts'
import { segmentPhrases } from './segmenter.ts'
import { activeWordAt, normalizeWords } from './timeline.ts'
import type {
  CaptionConfig,
  CaptionFrameState,
  CompiledCaptions,
  InputWord,
  PhraseFrame,
  PlannedPhrase,
  PlannedWord,
  TextMeasurer,
  WordFrame,
  WordOverrides,
} from './types.ts'

const HOLD_AFTER_SPEECH = 0.35 // s a beat lingers after its last word when nothing follows immediately

export interface CompileOptions {
  measure: TextMeasurer
  overrides?: WordOverrides
  analyzer?: ImportanceAnalyzer
}

export function compileCaptions(input: InputWord[], config: CaptionConfig, opts: CompileOptions): CompiledCaptions {
  const overrides = opts.overrides ?? {}
  const analyzer = opts.analyzer ?? new HeuristicImportanceAnalyzer()
  const words = normalizeWords(input)
  const scores = analyzer.score(words)
  const phrases = segmentPhrases(words, scores, config)
  const importance = assignLevels(words, scores, phrases, config, overrides)
  const a = config.animation
  const lead = a.leadIn / 1000

  const planned: PlannedPhrase[] = phrases.map((ph, beat) => {
    const layout = layoutPhrase(ph.words, words, importance, config, opts.measure, overrides, beat)
    const showAt = Math.max(0, ph.start - lead)
    let supportingOrder = 0
    const pw: PlannedWord[] = layout.words.map((lw) => {
      const w = words[lw.index]
      const imp = importance[lw.index]
      const isHero = imp.level === 'hero'
      let revealAt: number
      if (isHero && a.heroRevealOnSpeak) revealAt = w.start - 0.03
      else if (a.revealMode === 'word') revealAt = w.start - lead * 0.5
      else if (a.revealMode === 'line') {
        const firstOfLine = layout.words.find((x) => x.line === lw.line)!
        revealAt = Math.max(showAt, words[firstOfLine.index].start - lead) + (a.stagger / 1000) * supportingOrder
      } else revealAt = showAt + (a.stagger / 1000) * supportingOrder
      if (!isHero) supportingOrder++
      revealAt = Math.max(showAt, revealAt)
      const plan = planAnimations(beat, imp.level, Math.abs(revealAt - w.start) < 0.06, config)
      const o = overrides[lw.index] ?? {}
      const colorKey = imp.level === 'hero' ? config.colors.hero : imp.level === 'strong' ? config.colors.strong : 'primary'
      return {
        index: lw.index,
        text: lw.text,
        importance: imp,
        fontSize: lw.fontSize,
        width: lw.width,
        height: lw.height,
        x: lw.x,
        y: lw.y,
        line: lw.line,
        revealAt,
        entrance: o.entrance ?? plan.entrance,
        exit: o.exit ?? plan.exit,
        speak: o.speak ?? plan.speak,
        entranceMs: plan.entranceMs,
        baseColor: config.colors[colorKey],
        customColor: o.color,
        opacityBase: imp.level === 'normal' ? config.colors.supportingOpacity : 1,
      }
    })
    return {
      id: ph.id,
      words: pw,
      lines: layout.lines,
      strategy: layout.strategy,
      box: layout.box,
      spokenStart: ph.start,
      spokenEnd: ph.end,
      showAt,
      exitStart: 0,
      hideAt: 0,
      exitMs: a.exitDuration,
    }
  })

  // Beat timing: stay at least minDuration, linger briefly after speech, and hand over to the next beat with
  // a short overlap (old beat exits while the new one enters).
  const overlap = a.overlap / 1000
  planned.forEach((ph, i) => {
    const next = planned[i + 1]
    // A hero word needs a moment to land: hold it, delaying the next beat a little if necessary.
    const heroes = ph.words.filter((w) => w.importance.level === 'hero')
    if (next && heroes.length) {
      const heroIn = Math.max(...heroes.map((w) => w.revealAt))
      const need = heroIn + a.minHeroHold - (next.showAt + overlap)
      if (need > 0) {
        next.showAt += Math.min(need, a.maxHandoverDelay)
        for (const w of next.words) w.revealAt = Math.max(w.revealAt, next.showAt)
      }
    }
    const lastReveal = Math.max(...ph.words.map((w) => w.revealAt + w.entranceMs / 1000))
    let hide = Math.max(ph.spokenEnd + HOLD_AFTER_SPEECH, ph.showAt + config.segmentation.minDuration, lastReveal + 0.15)
    if (next) hide = Math.min(hide, next.showAt + overlap)
    hide = Math.max(hide, ph.showAt + 0.2)
    ph.hideAt = hide
    ph.exitMs = Math.min(a.exitDuration, (hide - ph.showAt) * 500)
    ph.exitStart = hide - ph.exitMs / 1000
  })

  const last = planned[planned.length - 1]
  const duration = Math.max(last ? last.hideAt : 0, words.length ? words[words.length - 1].end : 0) + 0.6
  return { config, words, importance, phrases: planned, duration }
}

/** Beats visible at time t (at most two while one hands over to the next). */
export function visiblePhrases(compiled: CompiledCaptions, t: number): PlannedPhrase[] {
  return compiled.phrases.filter((p) => p.showAt <= t && t < p.hideAt)
}

export function getCaptionFrameState(compiled: CompiledCaptions, time: number): CaptionFrameState {
  const { config, words } = compiled
  const a = config.animation
  const c = config.colors
  const hl = config.highlight
  const active = activeWordAt(words, time)
  const activePhrase = active == null ? -1 : compiled.phrases.findIndex((p) => p.words.some((w) => w.index === active))
  const phrases: PhraseFrame[] = []
  const out: WordFrame[] = []

  for (const ph of visiblePhrases(compiled, time)) {
    const exiting = time >= ph.exitStart
    phrases.push({ id: ph.id, strategy: ph.strategy, box: ph.box, exiting })
    for (const pw of ph.words) {
      if (time < pw.revealAt) continue
      const w = words[pw.index]
      const inMs = (time - pw.revealAt) * 1000
      const outMs = exiting ? (time - ph.exitStart) * 1000 : 0
      const spMs = (time - w.start) * 1000
      const tIn = entrance(pw.entrance, inMs, pw.entranceMs, a.distance)
      const tOut = exiting ? exit(pw.exit, outMs, ph.exitMs, a.distance) : undefined
      const tSp = speak(pw.speak, spMs, a.speakDuration, a.speakScale)
      const tr = tOut ? combine(tIn, tOut, tSp) : combine(tIn, tSp)

      const isActive = pw.index === active
      const spoken = time >= w.start
      const isHero = pw.importance.level === 'hero'
      const base = pw.customColor ?? pw.baseColor
      const hierarchy = pw.customColor ?? (pw.importance.level === 'normal' || pw.importance.level === 'medium' ? c.primary : base)
      let color = base
      let fill: WordFrame['fill']
      switch (hl.mode) {
        case 'NO_COLOR_ANIMATION':
          color = pw.customColor ?? c.primary
          break
        case 'EMPHASIS_ONLY':
          color = hierarchy
          break
        case 'ACTIVE_WORD':
          if (hl.invert) color = isActive ? c.primary : isHero ? base : c.active
          else color = isActive && !isHero ? c.active : hierarchy
          break
        case 'ACTIVE_PHRASE':
          color = ph.id === compiled.phrases[activePhrase]?.id ? (isHero ? base : hl.invert ? c.primary : c.active) : hierarchy
          break
        case 'KARAOKE_PROGRESS':
          if (isActive) {
            color = isHero ? base : c.active
            fill = { progress: Math.min(1, Math.max(0, (time - w.start) / Math.max(0.05, w.end - w.start))), base: hierarchy }
          } else color = spoken ? (isHero ? base : c.active) : hierarchy
          break
      }
      // Hero words flash the secondary accent briefly as they land, then settle on their own colour.
      if (isHero && !pw.customColor && config.animation.heroFlashMs > 0 && hl.mode !== 'NO_COLOR_ANIMATION') {
        const since = inMs - pw.entranceMs * 0.25
        if (since >= 0 && since < config.animation.heroFlashMs) color = base === c.secondary ? c.active : c.secondary
      }

      out.push({
        index: pw.index,
        phraseId: ph.id,
        line: pw.line,
        text: pw.text,
        x: pw.x + tr.dx,
        y: pw.y + tr.dy,
        width: pw.width,
        height: pw.height,
        fontSize: pw.fontSize,
        scale: tr.scale,
        rotation: tr.rotation,
        opacity: Math.max(0, Math.min(1, tr.opacity * pw.opacityBase)),
        blur: tr.blur,
        color,
        fill,
        active: isActive,
        spoken,
        importance: pw.importance.level,
        entrance: pw.entrance,
        exit: pw.exit,
        speak: pw.speak,
        entranceProgress: Math.min(1, inMs / Math.max(1, pw.entranceMs)),
        exitProgress: exiting ? Math.min(1, outMs / Math.max(1, ph.exitMs)) : 0,
        speakProgress: spMs >= 0 ? Math.min(1, spMs / a.speakDuration) : 0,
      })
    }
  }
  // Draw order: supporting words first, then strong, then hero on top.
  const rank = { normal: 0, medium: 1, strong: 2, hero: 3 }
  out.sort((x, y) => rank[x.importance] - rank[y.importance])
  return { time, phrases, words: out, activeWord: active }
}

/** Snap a time to the frame grid of a given fps (for frame-accurate scrubbing and export). */
export const frameTime = (frame: number, fps: number) => frame / fps
export const frameAt = (time: number, fps: number) => Math.floor(time * fps + 1e-6)
