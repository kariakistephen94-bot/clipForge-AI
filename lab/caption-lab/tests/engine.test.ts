// Engine tests -- run with:  node --experimental-strip-types --test lab/caption-lab/tests/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { entrance } from '../src/caption/animation.ts'
import { compileCaptions, frameTime, getCaptionFrameState } from '../src/caption/engine.ts'
import { presetConfig, PRESETS } from '../src/caption/presets.ts'
import { autoTime, parseTranscript } from '../src/caption/timeline.ts'
import type { TextMeasurer } from '../src/caption/types.ts'

const SAMPLE = 'Words can mean different things. Do not judge too quickly. Be more tolerant.'
// Deterministic stand-in for canvas measureText: 0.56 em per character.
const measure: TextMeasurer = { width: (t, size) => t.length * size * 0.56 }
const compile = (preset = 'editorial-hero', text = SAMPLE) => compileCaptions(autoTime(text), presetConfig(preset), { measure })
const textOf = (c: ReturnType<typeof compile>, ids: number[]) => ids.map((i) => c.words[i].text).join(' ')

test('segments the sample into editorial beats', () => {
  const c = compile()
  const beats = c.phrases.map((p) => p.words.map((w) => c.words[w.index].text).join(' '))
  assert.deepEqual(beats, ['Words can mean different things', 'Do not judge', 'too quickly', 'Be more tolerant'])
})

test('finds the hero words and keeps supporting words small', () => {
  const c = compile()
  const level = (w: string) => c.importance[c.words.findIndex((x) => x.text === w)].level
  assert.equal(level('different'), 'hero')
  assert.equal(level('judge'), 'hero')
  assert.equal(level('tolerant'), 'hero')
  assert.equal(level('not'), 'medium')
  assert.equal(level('things'), 'normal')
  assert.notEqual(level('quickly'), 'hero') // hero cooldown right after JUDGE
})

test('hero words get their own line and dominate the frame', () => {
  const c = compile()
  const [b0, b1, , b3] = c.phrases
  const lineTexts = (p: typeof b0) => p.lines.map((l) => l.map((k) => p.words[k].text).join(' '))
  assert.deepEqual(lineTexts(b0), ['Words can mean', 'DIFFERENT', 'things'])
  assert.deepEqual(lineTexts(b1), ['Do not', 'JUDGE'])
  assert.deepEqual(lineTexts(b3), ['Be more', 'TOLERANT'])
  for (const p of [b0, b1, b3]) {
    const hero = p.words.find((w) => w.importance.level === 'hero')!
    const others = p.words.filter((w) => w !== hero)
    assert.ok(hero.fontSize >= 1.8 * Math.max(...others.map((w) => w.fontSize)), `${hero.text} is not dominant`)
  }
  assert.equal(textOf(c, b1.words.map((w) => w.index)), 'Do not judge')
})

test('words never overlap on a line and stay inside the safe zone', () => {
  for (const preset of PRESETS) {
    const c = compile(preset.id, SAMPLE + ' Nobody expected a three hundred million dollar mistake like that one!')
    const cfg = c.config
    for (const p of c.phrases) {
      for (const w of p.words) {
        assert.ok(w.x - w.width / 2 >= cfg.layout.safe.left - 0.5, `${preset.id}: ${w.text} crosses the left safe edge`)
        assert.ok(w.x + w.width / 2 <= cfg.canvas.width - cfg.layout.safe.right + 0.5, `${preset.id}: ${w.text} crosses the right safe edge`)
        assert.ok(w.y - w.fontSize / 2 >= cfg.layout.safe.top - 1, `${preset.id}: ${w.text} above the safe zone`)
        assert.ok(w.y + w.fontSize / 2 <= cfg.canvas.height - cfg.layout.safe.bottom + 1, `${preset.id}: ${w.text} below the safe zone`)
      }
      for (const line of p.lines) {
        for (let j = 1; j < line.length; j++) {
          const a = p.words[line[j - 1]]
          const b = p.words[line[j]]
          assert.ok(a.x + a.width / 2 <= b.x - b.width / 2 + 0.5, `${preset.id}: "${a.text}" overlaps "${b.text}"`)
        }
      }
    }
  }
})

test('frame state is a pure function of time (same frame at any fps)', () => {
  const c = compile()
  for (const t of [0, 0.5, 1.234, 2.5, 3.9, 5.0]) {
    assert.deepEqual(getCaptionFrameState(c, t), getCaptionFrameState(c, t))
  }
  // frame 30 at 30 fps is the same instant as frame 60 at 60 fps
  assert.deepEqual(getCaptionFrameState(c, frameTime(30, 30)), getCaptionFrameState(c, frameTime(60, 60)))
})

test('the hero enters when it is spoken, not before', () => {
  const c = compile()
  const judge = c.words.find((w) => w.text === 'judge')!
  const before = getCaptionFrameState(c, judge.start - 0.08)
  const after = getCaptionFrameState(c, judge.start + 0.36)
  assert.ok(!before.words.some((w) => w.index === judge.index), 'JUDGE visible before it is spoken')
  assert.ok(before.words.some((w) => w.text === 'Do'), '"Do not" should already be on screen')
  const j = after.words.find((w) => w.index === judge.index)!
  assert.ok(j && Math.abs(j.scale - 1) < 0.02 && j.opacity > 0.99, 'JUDGE settled after its entrance')
})

test('beats hand over with a short overlap and stay readable', () => {
  const c = compile()
  for (let i = 0; i < c.phrases.length - 1; i++) {
    const a = c.phrases[i]
    const b = c.phrases[i + 1]
    assert.ok(a.hideAt - b.showAt <= c.config.animation.overlap / 1000 + 1e-9, 'overlap too long')
    assert.ok(a.hideAt - a.showAt >= 0.5, `beat ${i} flashes by`)
    const hero = a.words.find((w) => w.importance.level === 'hero')
    if (hero) assert.ok(a.hideAt - hero.revealAt >= 0.55, `hero "${hero.text}" gone too soon`)
  }
})

test('POP follows the specified keyframes', () => {
  const at = (ms: number) => entrance('POP', ms, 180, 40)
  assert.ok(Math.abs(at(0).scale - 0.82) < 1e-6 && at(0).opacity === 0)
  assert.ok(Math.abs(at(70).scale - 1.1) < 0.01 && at(70).opacity > 0.99)
  assert.ok(Math.abs(at(130).scale - 0.97) < 0.01)
  assert.equal(at(180).scale, 1)
})

test('accepts word-timestamp JSON (also Whisper segments)', () => {
  const json = '[{"word":"Words","start":0,"end":0.31},{"word":"can","start":0.31,"end":0.47}]'
  assert.equal(parseTranscript(json).words.length, 2)
  const whisper = JSON.stringify({ segments: [{ words: [{ word: ' Hi', start: 0, end: 0.2 }] }] })
  assert.equal(parseTranscript(whisper).words[0].word, ' Hi')
  assert.ok(parseTranscript('[{"oops"').error)
  assert.equal(parseTranscript('plain text here').timed, false)
})
