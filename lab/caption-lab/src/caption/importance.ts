// Stage 4: word importance.
//
// Two steps, kept apart so the scorer can be replaced by an AI/LLM later:
//   1. an ImportanceAnalyzer scores every word 0..1 (with human-readable reasons)
//   2. assignLevels() turns scores into NORMAL / MEDIUM / STRONG / HERO per caption beat, applying editorial
//      limits (one hero per beat, a cap on strong words, no hero right after another hero) and manual overrides.

import type { CaptionConfig, CaptionWord, Importance, ImportanceResult, Phrase, WordOverrides } from './types.ts'

export interface WordScore {
  score: number
  reasons: string[]
  /** An analyzer (e.g. an LLM) may decide the level outright; limits and overrides still apply. */
  level?: Importance
}

export interface ImportanceAnalyzer {
  readonly name: string
  score(words: CaptionWord[]): WordScore[]
}

const FUNCTION_WORDS = new Set(
  ('a an the and or of to in on at by for with from as is are was were be been being am it its it\'s this that these those ' +
    'i you he she we they me him her us them my your his our their mine yours ours theirs there here then than so if ' +
    'do does did done have has had having will would can could should shall may might must just also too very really ' +
    'about into over under up down out off like what which who whom whose when where why how all any some such ' +
    'i\'m you\'re we\'re they\'re that\'s what\'s there\'s i\'ve you\'ve we\'ve i\'ll you\'ll i\'d you\'d gonna wanna ' +
    'okay ok yeah yes um uh oh well right')
    .split(' '),
)
const NEGATIONS = new Set(
  "not no never nothing nobody none nowhere neither nor don't doesn't didn't can't cannot won't wouldn't shouldn't isn't aren't wasn't weren't haven't hasn't stop without".split(
    ' ',
  ),
)
const CONTRAST = new Set('but however instead yet although though actually except unless whereas'.split(' '))
const INTENSIFIERS = new Set('more most less least much so too very extremely super completely totally only'.split(' '))
const GENERIC = new Set('thing things stuff something anything everything way ways lot lots kind sort bit people person'.split(' '))
const POWER = new Set(
  ('different secret truth free money rich poor dead death kill love hate fear crazy insane impossible huge massive ' +
    'best worst never always everyone nobody wrong right real fake problem mistake mistakes failure fail success win lose ' +
    'judge tolerant powerful powerless dream dreams brain mind focus memory dopamine addiction habit habits stress ' +
    'broke million billion first last only warning danger shocking stupid genius hard easy fast slow now today forever ' +
    'why stop quit change changed life lives health happy sad angry alone lonely empathy empathetic dumb smart')
    .split(' '),
)
const EMOTION_SUFFIX = /(ful|less|ous|ive|ant|ent|able|ible|est|ism|ness|tion|sion)$/

function norm(word: string): string {
  return word.toLowerCase().replace(/[^\p{L}\p{N}'$%]/gu, '')
}

/** Heuristic analyzer: lexical cues + position in the sentence. Replace with an AI analyzer later. */
export class HeuristicImportanceAnalyzer implements ImportanceAnalyzer {
  readonly name = 'heuristic-v1'

  score(words: CaptionWord[]): WordScore[] {
    const counts = new Map<string, number>()
    for (const w of words) counts.set(norm(w.text), (counts.get(norm(w.text)) ?? 0) + 1)
    // last content word of each sentence = punchline candidate
    const punchline = new Set<number>()
    let sentenceStart = 0
    for (let i = 0; i < words.length; i++) {
      if (words[i].sentenceEnd || i === words.length - 1) {
        for (let j = i; j >= sentenceStart; j--) {
          const n = norm(words[j].text)
          if (!FUNCTION_WORDS.has(n) && !GENERIC.has(n)) {
            punchline.add(j)
            break
          }
        }
        sentenceStart = i + 1
      }
    }

    return words.map((w, i) => {
      const n = norm(w.text)
      const reasons: string[] = []
      let s = 0.1
      const add = (v: number, why: string) => {
        s += v
        reasons.push(`${v >= 0 ? '+' : ''}${v.toFixed(2)} ${why}`)
      }
      const isFunction = FUNCTION_WORDS.has(n)
      if (NEGATIONS.has(n)) add(0.35, 'negation')
      else if (CONTRAST.has(n)) add(0.3, 'contrast word')
      else if (INTENSIFIERS.has(n)) add(0.25, 'intensifier')
      else if (isFunction) add(-0.05, 'function word')
      else add(0.25, 'content word')
      if (/^[$€£]?\d[\d,.]*[%kKmMbB]?$/.test(n) || /^\d/.test(n)) add(0.45, 'number')
      if (POWER.has(n)) add(0.3, 'power / emotional word')
      if (!isFunction && n.length >= 5) add(0.1, 'long content word')
      if (!isFunction && EMOTION_SUFFIX.test(n) && n.length >= 6) add(0.1, 'descriptive suffix')
      if (GENERIC.has(n)) add(-0.2, 'generic word')
      const prev = i > 0 ? norm(words[i - 1].text) : ''
      if (!isFunction && (NEGATIONS.has(prev) || NEGATIONS.has(i > 1 ? norm(words[i - 2].text) : ''))) add(0.3, 'follows a negation')
      if (!isFunction && INTENSIFIERS.has(prev)) add(0.2, 'follows an intensifier')
      if (punchline.has(i)) add(0.2, 'punchline position')
      if (w.raw.endsWith('!')) add(0.15, 'exclamation')
      if (w.text.length > 1 && w.text === w.text.toUpperCase() && /\p{L}/u.test(w.text)) add(0.2, 'written in capitals')
      if (!isFunction && (counts.get(n) ?? 0) > 1) add(0.05, 'repeated')
      const dur = w.end - w.start
      if (!isFunction && dur > 0.45) add(0.1, 'drawn out when spoken')
      return { score: Math.max(0, Math.min(1, s)), reasons }
    })
  }
}

function levelFor(score: number, cfg: CaptionConfig['importance']): Importance {
  if (score >= cfg.heroThreshold) return 'hero'
  if (score >= cfg.strongThreshold) return 'strong'
  if (score >= cfg.mediumThreshold) return 'medium'
  return 'normal'
}

/** Word levels per beat: at most one hero, at most N strong, hero cooldown, then manual overrides. */
export function assignLevels(
  words: CaptionWord[],
  scores: WordScore[],
  phrases: Phrase[],
  config: CaptionConfig,
  overrides: WordOverrides = {},
): ImportanceResult[] {
  const cfg = config.importance
  const out: ImportanceResult[] = words.map((_, i) => ({
    level: scores[i].level ?? levelFor(scores[i].score, cfg),
    score: scores[i].score,
    reasons: [...scores[i].reasons],
  }))
  let lastHeroAt = -Infinity
  for (const ph of phrases) {
    const idx = [...ph.words].sort((a, b) => out[b].score - out[a].score)
    let heroTaken = false
    let strongCount = 0
    for (const i of idx) {
      const r = out[i]
      if (r.level === 'hero') {
        if (heroTaken) {
          r.level = 'strong'
          r.reasons.push('demoted: one hero per beat')
        } else if (words[i].start - lastHeroAt < cfg.heroCooldown && scores[i].level !== 'hero') {
          r.level = 'strong'
          r.reasons.push('demoted: hero cooldown')
        } else {
          heroTaken = true
          lastHeroAt = words[i].start
        }
      }
      if (r.level === 'strong') {
        strongCount++
        if (strongCount > cfg.maxStrongPerPhrase) {
          r.level = 'medium'
          r.reasons.push('demoted: too many strong words')
        }
      }
    }
  }
  for (const [k, o] of Object.entries(overrides)) {
    const i = Number(k)
    if (o.importance && out[i]) {
      out[i].level = o.importance
      out[i].reasons.push(`manual override: ${o.importance}`)
    }
  }
  return out
}
