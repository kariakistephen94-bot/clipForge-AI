// Stage 5: caption layout -- where every word of a beat sits, at what size.
//
// A strategy decides how the beat's words are split into lines (a hero word usually gets its own line);
// then each line is sized from word importance, shrunk if it would exceed the safe width, and the block is
// placed around layout.anchorY inside the safe zones. Nothing here knows about animation or rendering.

import type {
  Box,
  CaptionConfig,
  CaptionWord,
  Importance,
  ImportanceResult,
  LayoutStrategy,
  TextMeasurer,
  WordOverrides,
} from './types.ts'

export interface LaidOutWord {
  index: number
  text: string
  fontSize: number
  width: number
  height: number
  x: number
  y: number
  line: number
}

export interface PhraseLayout {
  strategy: LayoutStrategy
  words: LaidOutWord[] // same order as the phrase
  lines: number[][] // positions into `words`
  box: Box
}

/** Letter case for display. 'hero-upper' capitalises only strong/hero words, like editorial captions do. */
export function applyCase(text: string, level: Importance, textCase: CaptionConfig['typography']['textCase']): string {
  if (textCase === 'upper') return text.toUpperCase()
  if (textCase === 'lower') return text.toLowerCase()
  if (textCase === 'hero-upper' && (level === 'hero' || level === 'strong')) return text.toUpperCase()
  return text
}

export function safeArea(config: CaptionConfig): Box {
  const { width, height } = config.canvas
  const s = config.layout.safe
  return { x: s.left, y: s.top, w: width - s.left - s.right, h: height - s.top - s.bottom }
}

/** Split `items` (widths) into `n` contiguous lines with the smallest widest line. */
function balancedBreaks(widths: number[], gaps: number[], n: number): number[][] {
  const count = widths.length
  n = Math.max(1, Math.min(n, count))
  let best: number[][] = [widths.map((_, i) => i)]
  let bestCost = Infinity
  const lineW = (a: number, b: number) => {
    let w = 0
    for (let i = a; i < b; i++) w += widths[i] + (i > a ? gaps[i] : 0)
    return w
  }
  const recurse = (start: number, left: number, acc: number[][]) => {
    if (left === 1) {
      const lines = [...acc, range(start, count)]
      const ws = lines.map((l) => lineW(l[0], l[l.length - 1] + 1))
      // widest line dominates; slight preference for a shorter first line (reads like a lead-in)
      const cost = Math.max(...ws) + 0.02 * (ws[0] - ws[ws.length - 1])
      if (cost < bestCost) {
        bestCost = cost
        best = lines
      }
      return
    }
    for (let end = start + 1; end <= count - left + 1; end++) recurse(end, left - 1, [...acc, range(start, end)])
  }
  recurse(0, n, [])
  return best
}

const range = (a: number, b: number) => Array.from({ length: b - a }, (_, i) => a + i)

export function chooseStrategy(
  levels: Importance[],
  widths: number[],
  gaps: number[],
  config: CaptionConfig,
  beat = 0,
): LayoutStrategy {
  if (config.layout.strategy !== 'AUTO') return config.layout.strategy
  const n = levels.length
  if (n === 1) return 'WORD_FOCUS'
  const hero = levels.indexOf('hero')
  const maxW = safeArea(config).w * config.layout.maxWidth
  const oneLine = widths.reduce((a, b) => a + b, 0) + gaps.slice(1).reduce((a, b) => a + b, 0)
  if (hero >= 0) {
    // editorial: alternate staggered and centred hero beats so consecutive scenes don't look identical
    if (config.layout.prefer === 'editorial' && beat % 2 === 1) return 'ASYMMETRIC_EDITORIAL'
    return hero === n - 1 ? 'HERO_BOTTOM' : 'HERO_CENTER'
  }
  if (config.layout.prefer === 'stacked' && n <= 4) return 'STACKED'
  if (oneLine <= maxW * 0.92 && n <= 3) return 'ONE_LINE'
  if (oneLine <= maxW * 1.8 || config.layout.maxLines < 3 || n <= 4) return 'TWO_LINE'
  return 'THREE_LINE'
}

export function layoutPhrase(
  wordIdx: number[],
  words: CaptionWord[],
  importance: ImportanceResult[],
  config: CaptionConfig,
  measure: TextMeasurer,
  overrides: WordOverrides = {},
  beat = 0,
): PhraseLayout {
  const typo = config.typography
  const area = safeArea(config)
  const maxW = area.w * config.layout.maxWidth
  const levels = wordIdx.map((i) => importance[i].level)
  const texts = wordIdx.map((i, k) => applyCase(words[i].text, levels[k], typo.textCase))
  const sizes = wordIdx.map((i, k) => typo.baseSize * typo.scales[levels[k]] * (overrides[i]?.sizeScale ?? 1))
  const widthOf = (k: number, size = sizes[k]) => measure.width(texts[k], size)
  const gapBefore = (k: number) => (k === 0 ? 0 : measure.width(' ', Math.min(sizes[k - 1], sizes[k])))
  const widths = texts.map((_, k) => widthOf(k))
  const gaps = texts.map((_, k) => gapBefore(k))

  const strategy = chooseStrategy(levels, widths, gaps, config, beat)
  const hero = levels.indexOf('hero')
  const wrap = (from: number, to: number, maxLines = 2): number[][] => {
    if (to <= from) return []
    const idx = range(from, to)
    const total = idx.reduce((s, k) => s + widths[k] + (k > from ? gaps[k] : 0), 0)
    const n = Math.min(maxLines, idx.length, Math.max(1, Math.ceil(total / maxW)))
    return balancedBreaks(idx.map((k) => widths[k]), idx.map((k) => (k > from ? gaps[k] : 0)), n).map((l) => l.map((j) => j + from))
  }

  let lines: number[][]
  const n = texts.length
  switch (strategy) {
    case 'WORD_FOCUS':
      lines = n === 1 ? [[0]] : [range(0, n)]
      break
    case 'ONE_LINE':
      lines = [range(0, n)]
      break
    case 'STACKED':
      lines = range(0, n).reduce<number[][]>((acc, k) => {
        const last = acc[acc.length - 1]
        // stack short words two per line, longer words alone
        if (last && last.length < 2 && widths[last[0]] + gaps[k] + widths[k] <= maxW * 0.6) last.push(k)
        else acc.push([k])
        return acc
      }, [])
      break
    case 'TWO_LINE':
      lines = balancedBreaks(widths, gaps, 2)
      break
    case 'THREE_LINE':
      lines = balancedBreaks(widths, gaps, 3)
      break
    case 'HERO_BOTTOM': {
      const h = hero >= 0 ? hero : n - 1
      lines = [...wrap(0, h), range(h, n)]
      break
    }
    case 'HERO_CENTER':
    case 'ASYMMETRIC_EDITORIAL':
    default: {
      const h = hero >= 0 ? hero : widths.indexOf(Math.max(...widths))
      lines = [...wrap(0, h), [h], ...wrap(h + 1, n)]
    }
  }
  lines = lines.filter((l) => l.length)
  if (lines.length > config.layout.maxLines && strategy !== 'WORD_FOCUS') {
    // too many lines: merge the shortest neighbours until it fits
    while (lines.length > config.layout.maxLines) {
      let bi = 0
      let bw = Infinity
      for (let i = 0; i < lines.length - 1; i++) {
        const w = [...lines[i], ...lines[i + 1]].reduce((s, k) => s + widths[k], 0)
        if (w < bw && !lines[i].includes(hero) && !lines[i + 1].includes(hero)) {
          bw = w
          bi = i
        }
      }
      if (bw === Infinity) break
      lines.splice(bi, 2, [...lines[bi], ...lines[bi + 1]])
    }
  }

  // Fit each line into the safe width by shrinking that line (never clip, never overflow).
  const finalSizes = [...sizes]
  const lineWidth = (line: number[]) =>
    line.reduce((s, k, j) => s + measure.width(texts[k], finalSizes[k]) + (j > 0 ? measure.width(' ', Math.min(finalSizes[line[j - 1]], finalSizes[k])) : 0), 0)
  for (const line of lines) {
    const w = lineWidth(line)
    if (w > maxW) for (const k of line) finalSizes[k] *= maxW / w
  }
  // Fit the block into the safe height.
  const lineHeights = () => lines.map((l) => Math.max(...l.map((k) => finalSizes[k])) * typo.lineHeight)
  let totalH = lineHeights().reduce((a, b) => a + b, 0)
  if (totalH > area.h * 0.9) {
    const f = (area.h * 0.9) / totalH
    for (let k = 0; k < n; k++) finalSizes[k] *= f
    totalH *= f
  }

  const lh = lineHeights()
  const centerY = Math.min(
    Math.max(config.canvas.height * config.layout.anchorY, area.y + totalH / 2),
    area.y + area.h - totalH / 2,
  )
  const cx = area.x + area.w / 2
  const laid: LaidOutWord[] = texts.map((t, k) => ({ index: wordIdx[k], text: t, fontSize: finalSizes[k], width: 0, height: 0, x: 0, y: 0, line: 0 }))
  const lineWs = lines.map(lineWidth)
  let y = centerY - totalH / 2
  lines.forEach((line, li) => {
    const lw = lineWs[li]
    let x: number
    if (strategy === 'ASYMMETRIC_EDITORIAL') {
      // staggered left edges: each line steps right, the whole block stays centred
      const step = area.w * config.layout.editorialOffset
      const indents = lines.map((_, i) => i * step)
      const extent = Math.max(...lineWs.map((w, i) => indents[i] + w))
      x = cx - extent / 2 + indents[li]
    } else x = cx - lw / 2
    const rowCenter = y + lh[li] / 2
    line.forEach((k, j) => {
      if (j > 0) x += measure.width(' ', Math.min(finalSizes[line[j - 1]], finalSizes[k]))
      const w = measure.width(texts[k], finalSizes[k])
      const o = overrides[wordIdx[k]]
      laid[k].width = w
      laid[k].height = finalSizes[k] * 0.86
      laid[k].x = x + w / 2 + (o?.dx ?? 0)
      laid[k].y = rowCenter + (o?.dy ?? 0)
      laid[k].line = li
      x += w
    })
    y += lh[li]
  })
  const left = Math.min(...laid.map((w) => w.x - w.width / 2))
  const right = Math.max(...laid.map((w) => w.x + w.width / 2))
  const box: Box = { x: left, y: centerY - totalH / 2, w: right - left, h: totalH }
  return { strategy, words: laid, lines, box }
}
