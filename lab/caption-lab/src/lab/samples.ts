// Built-in lab samples.

export interface LabSample {
  id: string
  name: string
  text?: string // plain text (auto-timed)
  wordsUrl?: string // JSON word timestamps
  videoUrl?: string // background video that matches the timestamps
}

export const SAMPLES: LabSample[] = [
  {
    id: 'kinetic-demo',
    name: 'Kinetic demo (Words can mean DIFFERENT things)',
    text: 'Words can mean different things. Do not judge too quickly. Be more tolerant.',
  },
  {
    id: 'hook',
    name: 'Hook with numbers and contrast',
    text:
      'Nobody tells you this. I lost three hundred thousand dollars in one week. ' +
      'But that mistake changed everything. Stop chasing money and start building skills.',
  },
  {
    id: 'podcast',
    name: 'Real podcast clip (speech + video, Whisper timestamps)',
    wordsUrl: '/samples/podcast-words.json',
    videoUrl: '/samples/podcast.mp4',
  },
]

export const DEFAULT_SAMPLE = SAMPLES[0]
