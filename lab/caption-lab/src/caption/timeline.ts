// Stage 1-2: transcript -> normalised, timestamped words, plus time lookups used by the frame engine.

import type { CaptionWord, InputWord } from './types.ts'

const SENTENCE_END = /[.!?…]["')\]]*$/
const CLAUSE_END = /[,;:—–-]["')\]]*$/

/** Display text: strip surrounding quotes/brackets and trailing , . ; : (keep ? and ! which carry tone). */
export function cleanWord(raw: string): string {
  let w = raw.trim()
  if (w.length > 1) w = w.replace(/^["'“‘([]+|["'”’)\]]+$/g, '')
  w = w.replace(/(?<=[\p{L}\p{N}%$])[,.;:]+$/u, '')
  return w
}

export function normalizeWords(input: InputWord[]): CaptionWord[] {
  const sorted = input
    .filter((w) => w && typeof w.word === 'string' && w.word.trim() && Number.isFinite(w.start) && Number.isFinite(w.end))
    .map((w) => ({ ...w, start: Number(w.start), end: Math.max(Number(w.end), Number(w.start) + 0.04) }))
    .sort((a, b) => a.start - b.start)
  const out: CaptionWord[] = []
  for (const w of sorted) {
    const text = cleanWord(w.word)
    if (!text) continue
    out.push({
      index: out.length,
      raw: w.word.trim(),
      text,
      start: w.start,
      end: w.end,
      speaker: w.speaker,
      sentenceEnd: SENTENCE_END.test(w.word.trim()),
      clauseEnd: CLAUSE_END.test(w.word.trim()),
    })
  }
  return out
}

/** Rough spoken duration of a word from its letters (used only when plain text has no timestamps). */
function spokenSeconds(word: string): number {
  const letters = word.replace(/[^\p{L}\p{N}]/gu, '').length
  const syllables = Math.max(1, (word.toLowerCase().match(/[aeiouy]+/g) || []).length)
  return Math.min(0.9, 0.1 + 0.06 * letters + 0.04 * syllables)
}

/** Plain text -> evenly paced timestamps (~2.8 words/s, pauses at punctuation). */
export function autoTime(text: string, startAt = 0.4): InputWord[] {
  const tokens = text.split(/\s+/).filter(Boolean)
  const out: InputWord[] = []
  let t = startAt
  for (const tok of tokens) {
    const d = spokenSeconds(tok)
    out.push({ word: tok, start: +t.toFixed(3), end: +(t + d).toFixed(3) })
    t += d + 0.04
    if (SENTENCE_END.test(tok)) t += 0.45
    else if (CLAUSE_END.test(tok)) t += 0.2
  }
  return out
}

/** Accepts a JSON array of {word,start,end} (also {text,...} or Whisper segments), else treats input as text. */
export function parseTranscript(input: string): { words: InputWord[]; timed: boolean; error?: string } {
  const trimmed = input.trim()
  if (!trimmed) return { words: [], timed: false }
  if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
    try {
      const data = JSON.parse(trimmed)
      const list: unknown[] = Array.isArray(data)
        ? data
        : Array.isArray(data?.segments)
          ? data.segments.flatMap((s: { words?: unknown[] }) => s.words ?? [])
          : Array.isArray(data?.words)
            ? data.words
            : []
      const words = list
        .map((x) => x as Record<string, unknown>)
        .map((x) => ({
          word: String(x.word ?? x.text ?? ''),
          start: Number(x.start),
          end: Number(x.end),
          speaker: x.speaker != null ? String(x.speaker) : undefined,
        }))
        .filter((w) => w.word.trim() && Number.isFinite(w.start) && Number.isFinite(w.end))
      if (!words.length) return { words: [], timed: true, error: 'No {word, start, end} entries found in the JSON.' }
      return { words, timed: true }
    } catch (e) {
      return { words: [], timed: true, error: `Invalid JSON: ${(e as Error).message}` }
    }
  }
  return { words: autoTime(trimmed), timed: false }
}

/** Index of the word being spoken at time t (the last word that has started, while it or its gap lasts). */
export function activeWordAt(words: CaptionWord[], t: number, holdGap = 0.25): number | null {
  let lo = 0
  let hi = words.length - 1
  let found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (words[mid].start <= t) {
      found = mid
      lo = mid + 1
    } else hi = mid - 1
  }
  if (found < 0) return null
  const w = words[found]
  const next = words[found + 1]
  const until = next ? Math.min(next.start, w.end + holdGap) : w.end + holdGap
  return t < until ? found : null
}
