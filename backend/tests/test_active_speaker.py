"""Audio-visual active speaker detection and content-aware framing (synthetic signals)."""

import wave

import numpy as np

from app.services.reframe import (
    SAMPLE_FPS,
    SpeechInfo,
    _Obs,
    _read_wav,
    build_speech_info,
    cluster_voices,
    link_tracks,
    plan_content_shot,
    speaker_timeline,
    speech_turns,
    voice_features,
)

RNG = np.random.default_rng(7)


def _frames(duration: float, faces: list[tuple[float, float]], talking) -> list[list[_Obs]]:
    """faces: (x, y) per person; talking(t) -> index of the person whose mouth moves at time t (or None)."""
    out = []
    still = [RNG.normal(size=(18, 32)).astype(np.float32) for _ in faces]
    ref = [RNG.normal(size=(14, 32)).astype(np.float32) for _ in faces]
    for i in range(int(duration * SAMPLE_FPS)):
        t = i / SAMPLE_FPS
        row = []
        for k, (x, y) in enumerate(faces):
            mouth = RNG.normal(size=(18, 32)).astype(np.float32) if talking(t) == k else \
                still[k] + 0.05 * RNG.normal(size=(18, 32)).astype(np.float32)
            row.append(_Obs(t, x, y, 220, 260, mouth, ref[k] + 0.05 * RNG.normal(size=(14, 32)).astype(np.float32)))
        out.append(row)
    return out


def test_speech_turns_split_at_pauses_and_long_stretches():
    words = [{"start": i * 0.4, "end": i * 0.4 + 0.3, "word": "w"} for i in range(20)]  # 8 s, 0.1 s gaps
    words += [{"start": 10.0, "end": 10.4, "word": "yes"}]
    turns = speech_turns(words)
    assert turns[-1] == (10.0, 10.4)
    assert len(turns) >= 3 and all(b - a <= 6.01 for a, b in turns)


def test_speaker_timeline_cuts_to_whoever_talks_at_the_turn():
    # Left person talks 0-6 s, right person 6.5-12 s.
    frames = _frames(12, [(500, 400), (1400, 400)], lambda t: 0 if t < 6.2 else 1)
    tracks = link_tracks(list(range(len(frames))), frames)
    assert len(tracks) == 2
    speech = SpeechInfo(turns=[(0.2, 3.0), (3.2, 6.0), (6.5, 9.0), (9.2, 11.8)])
    timeline, decisive, _share = speaker_timeline(tracks, 0, 12, speech)
    xs = [(round(t, 1), tracks[k].median_box()[0]) for t, k in timeline]
    assert [x for _, x in xs] == [500, 1400]
    assert 6.0 <= xs[1][0] <= 6.5  # the cut lands in the pause before the new speaker's turn
    assert decisive > 0.5


def test_short_backchannel_does_not_trigger_a_cut():
    # Right person talks throughout except a 0.4 s "yeah" from the left person.
    frames = _frames(12, [(500, 400), (1400, 400)], lambda t: 0 if 5.0 <= t < 5.4 else 1)
    tracks = link_tracks(list(range(len(frames))), frames)
    speech = SpeechInfo(turns=[(0.2, 4.8), (5.0, 5.4), (5.6, 11.8)])
    timeline, _, _ = speaker_timeline(tracks, 0, 12, speech)
    assert len(timeline) == 1 and tracks[timeline[0][1]].median_box()[0] == 1400


def test_voice_identity_keeps_camera_on_the_speaker_when_lips_are_ambiguous():
    # The listener (left) nods and moves a lot in the second half; the right person's voice keeps talking.
    frames = _frames(12, [(500, 400), (1400, 400)], lambda t: 1 if t < 6 else (0 if int(t * 4) % 2 else 1))
    tracks = link_tracks(list(range(len(frames))), frames)
    turns = [(0.2, 3.0), (3.2, 6.0), (6.2, 9.0), (9.2, 11.8)]
    no_voice, _, _ = speaker_timeline(tracks, 0, 12, SpeechInfo(turns=turns))
    with_voice, _, _ = speaker_timeline(tracks, 0, 12, SpeechInfo(turns=turns, voices=[0, 0, 0, 0]))
    assert [tracks[k].median_box()[0] for _, k in with_voice] == [1400]
    assert len(with_voice) <= len(no_voice)


def _tone_wav(path, segments, rate=16000):
    """segments: (seconds, pitch Hz or 0 for silence); a buzzy harmonic tone approximates a voiced sound."""
    parts = []
    for dur, f0 in segments:
        t = np.arange(int(dur * rate)) / rate
        sig = sum(np.sin(2 * np.pi * f0 * h * t) / h for h in range(1, 8)) if f0 else np.zeros_like(t)
        parts.append(sig * (1 + 0.3 * np.sin(2 * np.pi * 4 * t)))
    x = (np.concatenate(parts) / 8 * 20000).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(x.tobytes())


def test_voices_are_told_apart_by_pitch(tmp_path):
    wav = tmp_path / "a.wav"
    plan = [(2, 120), (0.3, 0), (2, 205), (0.3, 0), (1.5, 125), (0.3, 0), (2.5, 190)]
    _tone_wav(wav, plan)
    turns, t = [], 0.0
    for dur, f0 in plan:
        if f0:
            turns.append((t + 0.05, t + dur - 0.05))
        t += dur
    info = build_speech_info([{"start": a, "end": b, "word": "x"} for a, b in turns], wav, t)
    assert info.voices[0] == info.voices[2] != info.voices[1] == info.voices[3]
    feats = voice_features(*_read_wav(wav), turns)
    assert cluster_voices(feats, [b - a for a, b in turns]) == info.voices


def test_content_aware_framing_without_faces():
    bins = 96
    edges, motion = [], []
    for _ in range(30):  # detail concentrated on the left third (e.g. a product shot)
        e = np.full(bins, 0.2)
        e[10:30] = 5.0
        edges.append(e)
        motion.append(np.zeros(bins))
    shot = plan_content_shot(0, 3, [i / 10 for i in range(30)], edges, motion, 1920, 1080, 607)
    assert shot.mode == "track" and shot.keyframes[0].cx < 700
    flat = [np.ones(bins)] * 30  # detail everywhere (a whiteboard, a landscape): show the whole frame
    assert plan_content_shot(0, 3, [i / 10 for i in range(30)], flat, [np.zeros(bins)] * 30, 1920, 1080, 607).mode == "wide"
