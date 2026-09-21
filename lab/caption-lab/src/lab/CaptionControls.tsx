import type { ReactNode } from 'react'
import { PRESETS } from '../caption/presets.ts'
import { FONT_CHOICES } from '../caption/theme.ts'
import type { CaptionConfig, ColorKey, EntranceAnim, ExitAnim, SpeakAnim } from '../caption/types.ts'
import { Color, Section, Select, Slider, Toggle } from './fields.tsx'

export type Update = (path: string, value: unknown) => void

export const ENTRANCES: EntranceAnim[] = ['POP', 'SOFT_POP', 'SLIDE_UP', 'SLIDE_DOWN', 'SLIDE_LEFT', 'SLIDE_RIGHT', 'SCALE_IN', 'FADE_UP', 'SPRING_IN', 'BOUNCE_IN', 'HERO_PUNCH', 'NONE']
export const EXITS: ExitAnim[] = ['FADE_OUT', 'SLIDE_UP_OUT', 'SLIDE_DOWN_OUT', 'SLIDE_LEFT_OUT', 'SLIDE_RIGHT_OUT', 'SHRINK_OUT', 'QUICK_BLUR_OUT', 'POP_OUT', 'NONE']
export const SPEAKS: SpeakAnim[] = ['PULSE', 'LIFT', 'PUNCH', 'NONE']
const COLOR_KEYS: ColorKey[] = ['primary', 'active', 'secondary', 'accent']
const auto = <T extends string>(xs: T[]) => ['AUTO', ...xs] as ('AUTO' | T)[]

export default function CaptionControls({ config, presetId, onPreset, update, onReset, extra }: {
  config: CaptionConfig
  presetId: string
  onPreset: (id: string) => void
  update: Update
  onReset: () => void
  extra?: ReactNode
}) {
  const t = config.typography
  const s = config.style
  const c = config.colors
  const a = config.animation
  const g = config.segmentation
  const im = config.importance
  const l = config.layout
  const fontLabel = FONT_CHOICES.find((f) => f.family === t.fontFamily)?.label ?? 'Custom'

  return (
    <div className="controls">
      <div className="presets">
        {PRESETS.map((p) => (
          <button key={p.id} className={`preset ${p.id === presetId ? 'on' : ''}`} onClick={() => onPreset(p.id)} title={p.description}>
            <b>{p.name}</b>
            <small>{p.description}</small>
          </button>
        ))}
      </div>
      <div className="row gap">
        <button className="btn sm" onClick={onReset}>Reset to preset</button>
        <button className="btn sm" onClick={() => void navigator.clipboard?.writeText(JSON.stringify(config, null, 2))}>Copy config JSON</button>
      </div>
      {extra}

      <Section title="Behaviour" open>
        <Select label="Animation intensity" value={a.intensity} options={['low', 'medium', 'high'] as const} onChange={(v) => update('animation.intensity', v)} />
        <Select label="Active-word colouring" value={config.highlight.mode}
          options={['ACTIVE_WORD', 'ACTIVE_PHRASE', 'EMPHASIS_ONLY', 'KARAOKE_PROGRESS', 'NO_COLOR_ANIMATION'] as const}
          onChange={(v) => update('highlight.mode', v)} />
        <Toggle label="Invert (accent text, active word turns primary)" value={config.highlight.invert} onChange={(v) => update('highlight.invert', v)} />
        <Select label="Supporting words appear" value={a.revealMode}
          options={[{ value: 'phrase', label: 'with the beat' }, { value: 'line', label: 'line by line' }, { value: 'word', label: 'as each is spoken' }] as const}
          onChange={(v) => update('animation.revealMode', v)} />
        <Toggle label="Hero word enters when spoken" value={a.heroRevealOnSpeak} onChange={(v) => update('animation.heroRevealOnSpeak', v)} />
        <Select label="Layout" value={l.strategy}
          options={['AUTO', 'ONE_LINE', 'STACKED', 'TWO_LINE', 'THREE_LINE', 'HERO_CENTER', 'HERO_BOTTOM', 'ASYMMETRIC_EDITORIAL', 'WORD_FOCUS'] as const}
          onChange={(v) => update('layout.strategy', v)} />
        <Select label="Auto layout leans" value={l.prefer} options={['centered', 'editorial', 'stacked'] as const} onChange={(v) => update('layout.prefer', v)} />
      </Section>

      <Section title="Typography">
        <Select label="Font" value={fontLabel} options={[...FONT_CHOICES.map((f) => f.label), ...(fontLabel === 'Custom' ? ['Custom'] : [])]}
          onChange={(v) => {
            const f = FONT_CHOICES.find((x) => x.label === v)
            if (!f) return
            update('typography.fontFamily', f.family)
            if (!f.weights.includes(t.fontWeight)) update('typography.fontWeight', f.weights[f.weights.length - 1])
          }} />
        <Select label="Weight" value={t.fontWeight} options={[400, 500, 600, 700, 800, 900]} onChange={(v) => update('typography.fontWeight', v)} />
        <Select label="Letter case" value={t.textCase}
          options={[{ value: 'hero-upper', label: 'Key words in CAPS' }, { value: 'upper', label: 'ALL CAPS' }, { value: 'as-is', label: 'As spoken' }, { value: 'lower', label: 'lowercase' }] as const}
          onChange={(v) => update('typography.textCase', v)} />
        <Slider label="Normal size" value={t.baseSize} min={36} max={140} unit="px" onChange={(v) => update('typography.baseSize', v)} />
        <Slider label="Medium ×" value={t.scales.medium} min={1} max={1.6} step={0.05} onChange={(v) => update('typography.scales.medium', v)} />
        <Slider label="Strong ×" value={t.scales.strong} min={1} max={2.2} step={0.05} onChange={(v) => update('typography.scales.strong', v)} />
        <Slider label="Hero ×" value={t.scales.hero} min={1} max={3.2} step={0.05} onChange={(v) => update('typography.scales.hero', v)} />
        <Slider label="Line height" value={t.lineHeight} min={0.8} max={1.5} step={0.02} onChange={(v) => update('typography.lineHeight', v)} />
        <Slider label="Letter spacing" value={t.letterSpacing} min={-0.08} max={0.1} step={0.005} unit="em" onChange={(v) => update('typography.letterSpacing', v)} />
      </Section>

      <Section title="Colours">
        <Color label="Primary text" value={c.primary} onChange={(v) => update('colors.primary', v)} />
        <Color label="Active word" value={c.active} onChange={(v) => update('colors.active', v)} />
        <Color label="Secondary accent" value={c.secondary} onChange={(v) => update('colors.secondary', v)} />
        <Color label="Accent" value={c.accent} onChange={(v) => update('colors.accent', v)} />
        <Select label="Hero words use" value={c.hero} options={COLOR_KEYS} onChange={(v) => update('colors.hero', v)} />
        <Select label="Strong words use" value={c.strong} options={COLOR_KEYS} onChange={(v) => update('colors.strong', v)} />
        <Slider label="Supporting word opacity" value={c.supportingOpacity} min={0.4} max={1} step={0.05} onChange={(v) => update('colors.supportingOpacity', v)} />
      </Section>

      <Section title="Stroke, shadow, plate">
        <Slider label="Stroke width" value={s.strokeWidth} min={0} max={20} unit="px" onChange={(v) => update('style.strokeWidth', v)} />
        <Color label="Stroke colour" value={s.strokeColor} onChange={(v) => update('style.strokeColor', v)} />
        <Slider label="Shadow blur" value={s.shadowBlur} min={0} max={60} onChange={(v) => update('style.shadowBlur', v)} />
        <Slider label="Shadow opacity" value={s.shadowOpacity} min={0} max={1} step={0.05} onChange={(v) => update('style.shadowOpacity', v)} />
        <Slider label="Shadow offset Y" value={s.shadowOffsetY} min={0} max={24} onChange={(v) => update('style.shadowOffsetY', v)} />
        <Color label="Shadow colour" value={s.shadowColor} onChange={(v) => update('style.shadowColor', v)} />
        <Select label="Background plate" value={s.plate} options={['none', 'word', 'line'] as const} onChange={(v) => update('style.plate', v)} />
        <Color label="Plate colour" value={s.plateColor} onChange={(v) => update('style.plateColor', v)} />
        <Slider label="Plate opacity" value={s.plateOpacity} min={0} max={1} step={0.05} onChange={(v) => update('style.plateOpacity', v)} />
        <Slider label="Plate radius" value={s.plateRadius} min={0} max={48} onChange={(v) => update('style.plateRadius', v)} />
      </Section>

      <Section title="Animation">
        <Select label="Supporting entrance" value={a.supportingEntrance} options={auto(ENTRANCES)} onChange={(v) => update('animation.supportingEntrance', v)} />
        <Select label="Hero entrance" value={a.heroEntrance} options={auto(ENTRANCES)} onChange={(v) => update('animation.heroEntrance', v)} />
        <Select label="Exit" value={a.exit} options={auto(EXITS)} onChange={(v) => update('animation.exit', v)} />
        <Select label="While spoken" value={a.speak} options={auto(SPEAKS)} onChange={(v) => update('animation.speak', v)} />
        <Slider label="Entrance" value={a.entranceDuration} min={80} max={500} step={10} unit="ms" onChange={(v) => update('animation.entranceDuration', v)} />
        <Slider label="Hero entrance" value={a.heroEntranceDuration} min={120} max={700} step={10} unit="ms" onChange={(v) => update('animation.heroEntranceDuration', v)} />
        <Slider label="Exit" value={a.exitDuration} min={60} max={400} step={10} unit="ms" onChange={(v) => update('animation.exitDuration', v)} />
        <Slider label="Beat overlap" value={a.overlap} min={0} max={250} step={10} unit="ms" onChange={(v) => update('animation.overlap', v)} />
        <Slider label="Stagger" value={a.stagger} min={0} max={150} step={5} unit="ms" onChange={(v) => update('animation.stagger', v)} />
        <Slider label="Lead-in" value={a.leadIn} min={0} max={300} step={10} unit="ms" onChange={(v) => update('animation.leadIn', v)} />
        <Slider label="Spoken reaction" value={a.speakDuration} min={60} max={300} step={10} unit="ms" onChange={(v) => update('animation.speakDuration', v)} />
        <Slider label="Spoken reaction scale" value={a.speakScale} min={1} max={1.3} step={0.01} onChange={(v) => update('animation.speakScale', v)} />
        <Slider label="Hero colour flash" value={a.heroFlashMs} min={0} max={400} step={10} unit="ms" onChange={(v) => update('animation.heroFlashMs', v)} />
        <Slider label="Min hero hold" value={a.minHeroHold} min={0} max={1.2} step={0.05} unit="s" onChange={(v) => update('animation.minHeroHold', v)} />
        <Slider label="Motion distance" value={a.distance} min={0} max={120} unit="px" onChange={(v) => update('animation.distance', v)} />
      </Section>

      <Section title="Phrase segmentation">
        <Slider label="Min words" value={g.minWords} min={1} max={4} onChange={(v) => update('segmentation.minWords', v)} />
        <Slider label="Max words" value={g.maxWords} min={1} max={10} onChange={(v) => update('segmentation.maxWords', v)} />
        <Slider label="Min on screen" value={g.minDuration} min={0.3} max={1.5} step={0.05} unit="s" onChange={(v) => update('segmentation.minDuration', v)} />
        <Slider label="Max speech per beat" value={g.maxDuration} min={0.6} max={4} step={0.1} unit="s" onChange={(v) => update('segmentation.maxDuration', v)} />
        <Slider label="Pause that splits" value={g.pauseBreak} min={0.15} max={1} step={0.01} unit="s" onChange={(v) => update('segmentation.pauseBreak', v)} />
        <Toggle label="End the beat on its hero word" value={g.splitAfterHero} onChange={(v) => update('segmentation.splitAfterHero', v)} />
      </Section>

      <Section title="Importance">
        <Slider label="Hero threshold" value={im.heroThreshold} min={0.3} max={1} step={0.01} onChange={(v) => update('importance.heroThreshold', v)} />
        <Slider label="Strong threshold" value={im.strongThreshold} min={0.2} max={1} step={0.01} onChange={(v) => update('importance.strongThreshold', v)} />
        <Slider label="Medium threshold" value={im.mediumThreshold} min={0.1} max={1} step={0.01} onChange={(v) => update('importance.mediumThreshold', v)} />
        <Slider label="Max strong per beat" value={im.maxStrongPerPhrase} min={0} max={4} onChange={(v) => update('importance.maxStrongPerPhrase', v)} />
        <Slider label="Hero cooldown" value={im.heroCooldown} min={0} max={4} step={0.1} unit="s" onChange={(v) => update('importance.heroCooldown', v)} />
      </Section>

      <Section title="Layout & safe zones">
        <Slider label="Vertical position" value={l.anchorY} min={0.3} max={0.8} step={0.01} onChange={(v) => update('layout.anchorY', v)} />
        <Slider label="Max width" value={l.maxWidth} min={0.5} max={1} step={0.01} onChange={(v) => update('layout.maxWidth', v)} />
        <Slider label="Max lines" value={l.maxLines} min={1} max={4} onChange={(v) => update('layout.maxLines', v)} />
        <Slider label="Editorial indent" value={l.editorialOffset} min={0} max={0.25} step={0.01} onChange={(v) => update('layout.editorialOffset', v)} />
        <Slider label="Safe top" value={l.safe.top} min={0} max={600} step={10} unit="px" onChange={(v) => update('layout.safe.top', v)} />
        <Slider label="Safe bottom" value={l.safe.bottom} min={0} max={800} step={10} unit="px" onChange={(v) => update('layout.safe.bottom', v)} />
        <Slider label="Safe left" value={l.safe.left} min={0} max={300} step={5} unit="px" onChange={(v) => update('layout.safe.left', v)} />
        <Slider label="Safe right" value={l.safe.right} min={0} max={300} step={5} unit="px" onChange={(v) => update('layout.safe.right', v)} />
      </Section>
    </div>
  )
}
