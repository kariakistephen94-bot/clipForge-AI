import { useState } from 'react'

// ── Types ──────────────────────────────────────────────────────────────────
interface IdeaScore {
  relatability: number
  visualizability: number
  story_arc: number
  curiosity_gap: number
  uniqueness: number
  total: number
}

interface VideoIdea {
  id: string
  title: string
  alternative_titles: string[]
  hook: string
  premise: string
  central_character: string
  story_arc: string
  emotional_rhythm: string
  category: string
  scores: IdeaScore
  verdict: string
  thumbnail_concept: string
}

// ── Scoring helpers ────────────────────────────────────────────────────────
function total(s: IdeaScore) {
  return s.relatability + s.visualizability + s.story_arc + s.curiosity_gap + s.uniqueness
}

function verdict(t: number) {
  if (t >= 80) return { label: 'Strong Candidate', color: 'var(--ok)' }
  if (t >= 60) return { label: 'Needs Refinement', color: 'var(--warn)' }
  return { label: 'Rethink Angle', color: 'var(--bad)' }
}

// ── Idea Bank (built-in, scored ideas) ────────────────────────────────────
const IDEA_BANK: VideoIdea[] = [
  {
    id: 'i1',
    title: 'Every Level From Poverty to Riches',
    alternative_titles: [
      'The 10 Levels You Must Pass to Escape Poverty',
      'What Nobody Tells You About Building Wealth From Nothing',
    ],
    hook: 'He counted eleven dollars in his pocket. Rent was due in four days.',
    premise: 'One person follows a journey from a struggling family through conditioning, entertainment, awareness, focus, discipline, consistency, first income, growth, wealth building, and finally freedom — showing each level honestly, not as a guaranteed formula.',
    central_character: 'Young man born into a low-income family. Round white head, dark charcoal T-shirt, dark trousers — same throughout.',
    story_arc: 'Conditioning → Entertainment trap → Awareness → Focus → Discipline → Consistency → First income → Growth → Wealth building → Freedom',
    emotional_rhythm: 'Struggle → Distraction → Tension → Decision → Slow progress → Setback → Breakthrough → Satisfaction',
    category: 'Life Journey',
    scores: { relatability: 19, visualizability: 18, story_arc: 18, curiosity_gap: 17, uniqueness: 14, total: 86 },
    verdict: 'Strong Candidate',
    thumbnail_concept: 'Character at desk at night (dim) LEFT vs. standing outside modest home in morning light RIGHT. Text: "10 LEVELS" bold top center.',
  },
  {
    id: 'i2',
    title: 'What Actually Happens When You Stop Scrolling for 30 Days',
    alternative_titles: [
      'I Quit Social Media for 30 Days. Here\'s What Changed.',
      'The Real Cost of One Hour of Scrolling Every Day',
    ],
    hook: 'He deleted every app on his phone on a Tuesday morning. By Friday, he was physically restless. By day twelve, something unexpected happened.',
    premise: 'One person quits all social media and mindless scrolling cold turkey for 30 days. The story follows the withdrawal, the discomfort, the unexpected emptiness, and what slowly fills that space instead.',
    central_character: 'Young adult, round white head, simple clothing. Expression evolves from anxious to calm over the 30-day arc.',
    story_arc: 'Day 1 (deletion) → Withdrawal panic → Empty evenings → First productive habit → Setback relapse → Recommitment → Final week breakthrough',
    emotional_rhythm: 'Confidence → Anxiety → Frustration → Boredom → Discovery → Discomfort → Habit formation → Quiet satisfaction',
    category: 'Human Behavior',
    scores: { relatability: 20, visualizability: 17, story_arc: 16, curiosity_gap: 19, uniqueness: 13, total: 85 },
    verdict: 'Strong Candidate',
    thumbnail_concept: 'Character staring at blank phone screen in the dark. Cracked phone icon nearby. Text: "30 DAYS" bold red.',
  },
  {
    id: 'i3',
    title: 'The Real Reason You Can\'t Finish Anything You Start',
    alternative_titles: [
      'Why Smart People Never Follow Through',
      'What Happens in Your Brain When You Quit Before the End',
    ],
    hook: 'He had started 14 different projects in 2 years. He had finished zero of them. He wasn\'t lazy. He was something else entirely.',
    premise: 'One person discovers through personal failure that the problem isn\'t laziness or willpower — it\'s that the brain rewards starting far more than finishing. The story follows his pattern, his realization, and what finally changes.',
    central_character: 'Young man surrounded by abandoned notebooks, half-built projects, empty courses. Round white head, increasing frustration then resolution.',
    story_arc: 'First project started (excited) → Abandoned → Second project (excited again) → Pattern recognized → Shame → Research moment → New approach → First thing completed',
    emotional_rhythm: 'Excitement → Abandonment → Excitement again → Pattern recognition → Shame → Discovery → Small win → Real satisfaction',
    category: 'Human Behavior / Psychology',
    scores: { relatability: 20, visualizability: 16, story_arc: 17, curiosity_gap: 20, uniqueness: 15, total: 88 },
    verdict: 'Strong Candidate',
    thumbnail_concept: 'Stick figure surrounded by half-finished notebooks all open. Expression: defeated. Text: "WHY?" bold above head.',
  },
  {
    id: 'i4',
    title: 'How One Skill Changed Everything',
    alternative_titles: [
      'The Difference Between People Who Earn and People Who Don\'t',
      'What Happens When You Go Deep Instead of Wide',
    ],
    hook: 'He knew a little about a lot of things. None of it was paying his bills.',
    premise: 'One person spends years jumping between skills and opportunities, never gaining traction. The story follows the moment he commits to one skill deeply, the long uncomfortable period of development, and the point where depth finally creates value.',
    central_character: 'Young man, scattered piles of books representing different skills. Over the course of the story, all but one book are cleared from the desk.',
    story_arc: 'Jumping between skills → Frustration → Decision to go deep → Long uncomfortable period → First sign of competence → First paid work → Growing reputation',
    emotional_rhythm: 'Confusion → Scattered energy → Frustration → Commitment → Slow doubt → Small signal → Confidence → Momentum',
    category: 'Focus / Skill Building',
    scores: { relatability: 18, visualizability: 17, story_arc: 16, curiosity_gap: 16, uniqueness: 12, total: 79 },
    verdict: 'Needs Refinement',
    thumbnail_concept: 'Chaotic desk with 10 books LEFT vs. clean desk with one open book RIGHT. Text: "ONE SKILL" centered.',
  },
  {
    id: 'i5',
    title: 'The Hidden Cost of Trying to Please Everyone',
    alternative_titles: [
      'What Happens to People Who Never Say No',
      'Why People-Pleasers End Up Alone',
    ],
    hook: 'He was the most agreeable person anyone knew. He was also quietly exhausted, deeply unhappy, and had no idea why.',
    premise: 'One person who has built their identity around keeping everyone else happy begins to realize the cost — lost time, lost identity, suppressed goals. The story follows the painful process of setting boundaries and what is gained and lost in doing so.',
    central_character: 'Young man, always smiling in scenes with others but shown exhausted in private moments. The tension between public and private becomes visually clear.',
    story_arc: 'Habitual yes-saying → Private exhaustion → Missed personal goal → First honest no → Conflict → Loss of approval → Discovery that self-respect and genuine connection are not opposites',
    emotional_rhythm: 'Performed happiness → Private exhaustion → Resentment → Courage → Fear → Loss → Recovery → Authenticity',
    category: 'Social Dynamics / Psychology',
    scores: { relatability: 19, visualizability: 15, story_arc: 17, curiosity_gap: 17, uniqueness: 16, total: 84 },
    verdict: 'Strong Candidate',
    thumbnail_concept: 'Stick figure smiling for a group LEFT panel vs. same figure slumped alone RIGHT panel. Text: "THE COST" bold.',
  },
  {
    id: 'i6',
    title: 'What Happens When You Wake Up at 5AM Every Day for 90 Days',
    alternative_titles: [
      'I Woke Up at 5AM for 3 Months. Here\'s the Honest Truth.',
      'The 5AM Experiment Nobody Talks About Honestly',
    ],
    hook: 'The alarm went off at 5AM. He stared at the ceiling and asked himself what exactly he thought was going to happen.',
    premise: 'One person documents the honest reality of a 90-day 5AM experiment — not the productivity fantasy, but the actual experience. Days when it works, days when it backfires, what genuinely changed and what didn\'t.',
    central_character: 'Young man. Expression ranges from determined to genuinely miserable across the 90 days. Gradual, honest arc.',
    story_arc: 'Day 1 (excited, terrible) → Week 1 struggle → Week 2 habit forming → Week 3 first real benefit → Missed day → Guilt → Recommitment → Day 90 honest assessment',
    emotional_rhythm: 'Optimism → Suffering → Small gains → Setback → Reality check → Adjusted expectation → Genuine benefit identified',
    category: 'Discipline / Human Behavior',
    scores: { relatability: 17, visualizability: 16, story_arc: 15, curiosity_gap: 18, uniqueness: 11, total: 77 },
    verdict: 'Needs Refinement',
    thumbnail_concept: 'Character sitting upright in pitch-black room, alarm showing 5:00. Expression: bleary. Text: "90 DAYS" bold.',
  },
  {
    id: 'i7',
    title: 'The Psychology of Why You Stay in Bad Situations Too Long',
    alternative_titles: [
      'Why Leaving Is Always Harder Than It Looks',
      'The Real Reason People Stay Stuck',
    ],
    hook: 'He knew for two years that he needed to leave. He still hadn\'t.',
    premise: 'One person stuck in a bad situation — a job, a pattern, a circumstance — slowly understands the psychological mechanisms that make leaving feel impossible even when staying is clearly harmful. The story ends not with a fairy tale exit but with a first honest step.',
    central_character: 'Young man. The background gets progressively darker and more constraining across early scenes, then gradually opens as the story progresses.',
    story_arc: 'Comfortable enough to stay → Growing unhappiness → First attempt to leave (failed) → Psychological pull back in → Deeper understanding of the pattern → Committed decision → First real step',
    emotional_rhythm: 'Comfort → Discontent → Rationalization → Frustration → Recognition → Fear → Courage → Small hope',
    category: 'Psychology / Human Behavior',
    scores: { relatability: 20, visualizability: 14, story_arc: 18, curiosity_gap: 19, uniqueness: 17, total: 88 },
    verdict: 'Strong Candidate',
    thumbnail_concept: 'Stick figure in a box, hand pressing against the wall. Expression: conflicted. Text: "WHY STAY?" red above.',
  },
]

// ── Sub-components ─────────────────────────────────────────────────────────
function ScoreBar({ label, value, max = 20 }: { label: string; value: number; max?: number }) {
  const pct = (value / max) * 100
  const color = pct >= 75 ? 'var(--ok)' : pct >= 50 ? 'var(--warn)' : 'var(--bad)'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ fontSize: 11.5, color: 'var(--muted)', width: 120, flexShrink: 0 }}>{label}</span>
      <div style={{ flex: 1, height: 6, background: 'var(--border)', borderRadius: 99, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 99, transition: 'width 0.5s ease' }} />
      </div>
      <span style={{ fontSize: 11.5, color, width: 30, textAlign: 'right', fontWeight: 650 }}>{value}/{max}</span>
    </div>
  )
}

function IdeaCard({ idea, onSelect, selected }: { idea: VideoIdea; onSelect: (id: string) => void; selected: boolean }) {
  const t = total(idea.scores)
  const v = verdict(t)
  const [expanded, setExpanded] = useState(false)

  return (
    <div
      className={`idea-card ${selected ? 'idea-card--selected' : ''}`}
      onClick={() => onSelect(idea.id)}
    >
      <div className="idea-card__header">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className="idea-tag">{idea.category}</span>
            <span style={{ fontSize: 11, color: v.color, fontWeight: 650, padding: '2px 8px', borderRadius: 99, background: `${v.color}22` }}>
              {v.label}
            </span>
          </div>
          <h3 style={{ marginTop: 6, fontSize: 15, fontWeight: 700, lineHeight: 1.3 }}>{idea.title}</h3>
          <p style={{ margin: '6px 0 0', fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.5 }}>
            <span style={{ color: 'var(--accent-2)', fontWeight: 600 }}>Hook: </span>{idea.hook}
          </p>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, flexShrink: 0 }}>
          <div className={`idea-score ${t >= 80 ? 'idea-score--good' : t >= 60 ? 'idea-score--mid' : 'idea-score--bad'}`}>
            {t}
          </div>
          <span style={{ fontSize: 10.5, color: 'var(--faint)' }}>/ 100</span>
        </div>
      </div>

      <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 5 }}>
        <ScoreBar label="Relatability" value={idea.scores.relatability} />
        <ScoreBar label="Visualizability" value={idea.scores.visualizability} />
        <ScoreBar label="Story Arc" value={idea.scores.story_arc} />
        <ScoreBar label="Curiosity Gap" value={idea.scores.curiosity_gap} />
        <ScoreBar label="Uniqueness" value={idea.scores.uniqueness} />
      </div>

      <button
        className="idea-expand-btn"
        onClick={e => { e.stopPropagation(); setExpanded(!expanded) }}
      >
        {expanded ? '▲ Hide details' : '▼ Full breakdown'}
      </button>

      {expanded && (
        <div className="idea-detail">
          <div className="idea-detail__section">
            <span className="label">Premise</span>
            <p>{idea.premise}</p>
          </div>
          <div className="idea-detail__section">
            <span className="label">Central Character</span>
            <p>{idea.central_character}</p>
          </div>
          <div className="idea-detail__section">
            <span className="label">Story Arc</span>
            <p style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12, color: 'var(--muted)' }}>{idea.story_arc}</p>
          </div>
          <div className="idea-detail__section">
            <span className="label">Emotional Rhythm</span>
            <p style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12, color: 'var(--muted)' }}>{idea.emotional_rhythm}</p>
          </div>
          <div className="idea-detail__section">
            <span className="label">Thumbnail Concept</span>
            <p>{idea.thumbnail_concept}</p>
          </div>
          <div className="idea-detail__section">
            <span className="label">Alternative Titles</span>
            <ul style={{ margin: 0, paddingLeft: 16 }}>
              {idea.alternative_titles.map((t, i) => <li key={i} style={{ fontSize: 12.5, color: 'var(--muted)' }}>{t}</li>)}
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Custom Idea Generator ──────────────────────────────────────────────────
const CATEGORIES = [
  'Life Journey', 'Human Behavior', 'Psychology', 'Focus / Skill Building',
  'Social Dynamics', 'Discipline', 'Historical / Biographical', 'Science Explained',
]

function CustomIdeaForm({ onAdd }: { onAdd: (idea: VideoIdea) => void }) {
  const [topic, setTopic] = useState('')
  const [character, setCharacter] = useState('')
  const [cat, setCat] = useState(CATEGORIES[0])
  const [generating, setGenerating] = useState(false)

  const generate = async () => {
    if (!topic.trim()) return
    setGenerating(true)
    // Client-side idea generation based on GEMINI.md scoring rubric
    await new Promise(r => setTimeout(r, 1200))

    // Score heuristically based on input keywords
    const lc = topic.toLowerCase()
    const hasJourney = /level|journey|from|to|escape|build|become/.test(lc)
    const hasBehavior = /why|how|what|stop|start|quit|habit/.test(lc)
    const hasEmotion = /fear|love|alone|struggle|fail|win|broke|rich|poor/.test(lc)

    const scores: IdeaScore = {
      relatability: hasEmotion ? 17 : 14,
      visualizability: hasJourney ? 16 : 13,
      story_arc: hasJourney ? 17 : hasBehavior ? 15 : 12,
      curiosity_gap: hasBehavior ? 18 : 14,
      uniqueness: 13,
      total: 0,
    }
    scores.total = scores.relatability + scores.visualizability + scores.story_arc + scores.curiosity_gap + scores.uniqueness

    const idea: VideoIdea = {
      id: `custom-${Date.now()}`,
      title: topic.trim(),
      alternative_titles: [`What Actually Happens When You ${topic.trim()}`, `The Real Truth About ${topic.trim()}`],
      hook: character.trim()
        ? `${character.trim()} faced a decision that would define the next ten years of their life.`
        : 'At the moment everything was supposed to get easier, it all fell apart instead.',
      premise: `A cartoon documentary following one character through the real journey of "${topic.trim()}" — showing the obstacles, setbacks, and turning points honestly.`,
      central_character: character.trim() || 'Young adult, round white head, dark charcoal T-shirt, dark trousers. Expression evolves across the story.',
      story_arc: `Opening conflict → First attempt → Setback → Discovery → Deeper effort → Turning point → Resolution`,
      emotional_rhythm: 'Tension → Doubt → Small progress → Setback → Breakthrough → Satisfaction',
      category: cat,
      scores,
      verdict: scores.total >= 80 ? 'Strong Candidate' : scores.total >= 60 ? 'Needs Refinement' : 'Rethink Angle',
      thumbnail_concept: `Character facing the central challenge. Bold 2–4 word text describing the core tension. Clean white background.`,
    }

    onAdd(idea)
    setTopic('')
    setCharacter('')
    setGenerating(false)
  }

  return (
    <div className="card" style={{ alignSelf: 'start' }}>
      <div className="card-title">
        <h2>✏️ Generate Custom Idea</h2>
      </div>
      <div className="stack">
        <label className="field">
          <span>Video topic or question</span>
          <input
            type="text"
            value={topic}
            onChange={e => setTopic(e.target.value)}
            placeholder="e.g. What happens when you stop caring what people think"
            onKeyDown={e => e.key === 'Enter' && generate()}
          />
        </label>
        <label className="field">
          <span>Central character (optional)</span>
          <input
            type="text"
            value={character}
            onChange={e => setCharacter(e.target.value)}
            placeholder="e.g. A young man from a poor family with big ambitions"
          />
        </label>
        <label className="field">
          <span>Category</span>
          <select value={cat} onChange={e => setCat(e.target.value)}>
            {CATEGORIES.map(c => <option key={c}>{c}</option>)}
          </select>
        </label>
        <div style={{ padding: '10px 14px', borderRadius: 'var(--radius-sm)', background: 'var(--accent-soft)', fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.6 }}>
          <strong style={{ color: 'var(--accent)' }}>Scoring rubric:</strong> Relatability · Visualizability · Story Arc · Curiosity Gap · Uniqueness (20pts each)
          <br />80+ = Strong · 60–79 = Needs refinement · Below 60 = Rethink
        </div>
        <button
          className="btn primary"
          disabled={!topic.trim() || generating}
          onClick={generate}
          id="generate-idea-btn"
        >
          {generating ? '⏳ Scoring idea…' : '🎬 Score This Idea'}
        </button>
      </div>
    </div>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────
type SortKey = 'score' | 'category' | 'title'
type FilterKey = 'all' | 'strong' | 'needs-work'

export default function IdeaGenerator() {
  const [ideas, setIdeas] = useState<VideoIdea[]>(IDEA_BANK)
  const [selected, setSelected] = useState<string | null>(null)
  const [sort, setSort] = useState<SortKey>('score')
  const [filter, setFilter] = useState<FilterKey>('all')
  const [search, setSearch] = useState('')

  const addIdea = (idea: VideoIdea) => {
    setIdeas(prev => [idea, ...prev])
    setSelected(idea.id)
  }

  const filtered = ideas
    .filter(i => {
      if (filter === 'strong') return i.scores.total >= 80
      if (filter === 'needs-work') return i.scores.total < 80
      return true
    })
    .filter(i => {
      if (!search.trim()) return true
      const q = search.toLowerCase()
      return i.title.toLowerCase().includes(q) || i.category.toLowerCase().includes(q) || i.premise.toLowerCase().includes(q)
    })
    .sort((a, b) => {
      if (sort === 'score') return b.scores.total - a.scores.total
      if (sort === 'category') return a.category.localeCompare(b.category)
      return a.title.localeCompare(b.title)
    })

  return (
    <div>
      {/* Page header */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10,
            background: 'linear-gradient(135deg, var(--accent), var(--accent-2))',
            display: 'grid', placeItems: 'center', fontSize: 18,
            boxShadow: '0 4px 14px rgba(139,108,255,0.35)'
          }}>💡</div>
          <h1 style={{ fontSize: 24 }}>Idea Generator</h1>
        </div>
        <p style={{ color: 'var(--muted)', fontSize: 13.5, margin: 0, maxWidth: 600 }}>
          Cartoon documentary ideas scored against the GEMINI.md rubric — relatability, visualizability, story arc, curiosity gap, and uniqueness. 8–15 min · 16:9 landscape · one character.
        </p>
      </div>

      <div className="grid-side" style={{ alignItems: 'start' }}>
        {/* Left: custom generator */}
        <CustomIdeaForm onAdd={addIdea} />

        {/* Right: idea list */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Controls */}
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search ideas…"
              style={{ flex: 1, minWidth: 180 }}
              id="idea-search"
            />
            <div className="segmented">
              <button className={filter === 'all' ? 'on' : ''} onClick={() => setFilter('all')}>All ({ideas.length})</button>
              <button className={filter === 'strong' ? 'on' : ''} onClick={() => setFilter('strong')}>
                Strong ({ideas.filter(i => i.scores.total >= 80).length})
              </button>
              <button className={filter === 'needs-work' ? 'on' : ''} onClick={() => setFilter('needs-work')}>
                Needs Work ({ideas.filter(i => i.scores.total < 80).length})
              </button>
            </div>
            <div className="segmented">
              <button className={sort === 'score' ? 'on' : ''} onClick={() => setSort('score')}>By Score</button>
              <button className={sort === 'category' ? 'on' : ''} onClick={() => setSort('category')}>By Category</button>
              <button className={sort === 'title' ? 'on' : ''} onClick={() => setSort('title')}>A–Z</button>
            </div>
          </div>

          {/* Idea cards */}
          {filtered.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
              <div style={{ fontSize: 36, marginBottom: 12 }}>🎬</div>
              <div style={{ color: 'var(--muted)' }}>No ideas match your filter. Try generating one on the left.</div>
            </div>
          ) : (
            filtered.map(idea => (
              <IdeaCard
                key={idea.id}
                idea={idea}
                selected={selected === idea.id}
                onSelect={id => setSelected(prev => prev === id ? null : id)}
              />
            ))
          )}
        </div>
      </div>
    </div>
  )
}
