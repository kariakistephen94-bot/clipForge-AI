// Playback clock for the lab preview. Lives outside React: the render loop reads clock.tick() every
// animation frame, and React only hears about time a few times per second (for the timeline readout).
// When a video is attached it is the master clock, so captions stay locked to its audio.

type Listener = () => void

export class PlaybackClock {
  time = 0
  playing = false
  speed = 1
  duration = 10
  loop = true
  private video: HTMLVideoElement | null = null
  private last = 0
  private listeners = new Set<Listener>()

  attachVideo(video: HTMLVideoElement | null) {
    this.video = video
    if (video) {
      video.playbackRate = this.speed
      video.currentTime = Math.min(this.time, video.duration || this.time)
      if (this.playing) void video.play().catch(() => this.pause())
    }
  }

  /** Advance and return the current time. Call once per animation frame. */
  tick(now = performance.now()): number {
    if (this.video) {
      this.time = this.video.currentTime
      if (this.playing && (this.video.ended || this.time >= this.duration)) this.atEnd()
    } else if (this.playing) {
      this.time += ((now - this.last) / 1000) * this.speed
      if (this.time >= this.duration) this.atEnd()
    }
    this.last = now
    return this.time
  }

  private atEnd() {
    if (this.loop) this.seek(0)
    else {
      this.time = this.duration
      this.pause()
    }
  }

  play() {
    if (this.time >= this.duration - 0.01) this.seek(0)
    this.playing = true
    this.last = performance.now()
    if (this.video) void this.video.play().catch(() => this.pause())
    this.emit()
  }

  pause() {
    this.playing = false
    this.video?.pause()
    this.emit()
  }

  toggle() {
    if (this.playing) this.pause()
    else this.play()
  }

  seek(t: number) {
    this.time = Math.max(0, Math.min(this.duration, t))
    if (this.video) this.video.currentTime = this.time
    this.last = performance.now()
    this.emit()
  }

  setSpeed(s: number) {
    this.speed = s
    if (this.video) this.video.playbackRate = s
    this.emit()
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  private emit() {
    for (const fn of this.listeners) fn()
  }
}
