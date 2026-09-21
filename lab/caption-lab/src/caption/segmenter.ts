// Stage 3: phrase segmentation -- speech -> short caption beats (typically 2-6 words).
//
// A beat ends at: a sentence end, a pause, a comma once the beat has enough words, the word/duration limit,
// or right after a hero word when the sentence goes on for 2+ more words ("Do not JUDGE" | "too quickly").
// Clean-up passes then keep beats readable: no beat ends on a dangling "the / of / to ...", and a lone
// orphan word joins the beat before it.

import type { WordScore } from './importance.ts'
import type { CaptionConfig, CaptionWord, Phrase } from './types.ts'

const LEADING = new Set(
  'a an the to of in on at for with from by my your his her our their its this that these those and or but if as than into'.split(' '),
)
const CLAUSE_STARTERS = new Set('and but because so which while when where although though unless until'.split(' '))

const lower = (w: CaptionWord) => w.text.toLowerCase()

export function segmentPhrases(words: CaptionWord[], scores: WordScore[], config: CaptionConfig): Phrase[] {
  const cfg = config.segmentation
  const heroish = (i: number) => (scores[i]?.level ?? null) === 'hero' || (scores[i]?.score ?? 0) >= config.importance.heroThreshold
  const sentenceRemaining = (i: number) => {
    let n = 0
    for (let j = i; j < words.length; j++) {
      n++
      if (words[j].sentenceEnd) break
    }
    return n
  }

  const groups: number[][] = []
  let cur: number[] = []
  for (let i = 0; i < words.length; i++) {
    const w = words[i]
    if (cur.length) {
      const prev = words[cur[cur.length - 1]]
      const first = words[cur[0]]
      const gap = w.start - prev.end
      const breakHere =
        prev.sentenceEnd ||
        gap >= cfg.pauseBreak ||
        cur.length >= cfg.maxWords ||
        (w.end - first.start > cfg.maxDuration && cur.length >= cfg.minWords) ||
        (prev.clauseEnd && cur.length >= cfg.minWords) ||
        (cfg.splitAfterHero && heroish(prev.index) && sentenceRemaining(i) >= 2) ||
        (CLAUSE_STARTERS.has(lower(w)) && cur.length >= cfg.minWords + 1 && gap > 0.08)
      if (breakHere) {
        groups.push(cur)
        cur = []
      }
    }
    cur.push(i)
  }
  if (cur.length) groups.push(cur)

  // A beat should not end on a word that leads into the next one ("... the | problem").
  for (let g = 0; g < groups.length - 1; g++) {
    const a = groups[g]
    const b = groups[g + 1]
    const last = words[a[a.length - 1]]
    if (a.length > 1 && LEADING.has(lower(last)) && !last.sentenceEnd && !last.clauseEnd
        && words[b[0]].start - last.end < cfg.pauseBreak && b.length < cfg.maxWords) {
      b.unshift(a.pop()!)
    }
  }

  // Orphans: a single non-hero word joins the previous beat when it follows closely in the same sentence.
  const merged: number[][] = []
  for (const g of groups) {
    const prevG = merged[merged.length - 1]
    if (g.length === 1 && prevG && !heroish(g[0])) {
      const prevLast = words[prevG[prevG.length - 1]]
      if (!prevLast.sentenceEnd && words[g[0]].start - prevLast.end < cfg.pauseBreak && prevG.length < cfg.maxWords + 1) {
        prevG.push(g[0])
        continue
      }
    }
    merged.push(g)
  }

  return merged
    .filter((g) => g.length)
    .map((g, id) => ({ id, words: g, start: words[g[0]].start, end: words[g[g.length - 1]].end }))
}
