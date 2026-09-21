import numpy as np

from app.schemas.ai import CampaignRules, SoundCue, ViralCandidate
from app.services.ffmpeg import build_overlay_cmd
from app.services.longform_render import long_form_events
from app.services.sfx_library import CATEGORIES, SfxItem, classify_acoustic, classify_name, is_pack, measure
from app.services.sound_design import (
    PEAK_CEILING_DB,
    PlacedSound,
    SoundContext,
    SoundEvent,
    assign_files,
    build_mix_cmd,
    keyword_events,
    normalize_category,
    place_sound,
    plan_events,
    sfx_permitted,
)

RATE = 16000


def _item(cat: str, n: int = 0, *, lead=0.05, peak=0.1, tail=0.5, loud=-20.0, source="name", channels=2, **kw) -> SfxItem:
    return SfxItem(id=f"{cat}{n}", path=f"/lib/{cat}{n}.wav", name=f"{cat}{n}.wav", category=cat, auto_category=cat,
                   category_source=source, duration=tail + 0.2, lead=lead, peak=peak, tail=tail, loud_db=loud,
                   peak_db=-3.0, channels=channels, **kw)


def _library() -> list[SfxItem]:
    return [_item(c, n) for c in CATEGORIES if c not in ("music", "other") for n in range(3)]


# --------------------------------------------------------------------------- library classification


def test_classify_by_filename():
    cases = {
        "Fast Whoosh Sound Effect.mp3": "whoosh", "swipes.mp3": "whoosh", "Paper Slide 03.wav": "paper",
        "ES_Riser Suction 5 - SFX Producer.mp3": "riser", "ES_Suction Pop 5 - SFX Producer.mp3": "pop",
        "Cash Register (Kaching) - Sound Effect (HD).mp3": "cash", "camera-shutter-6305.mp3": "camera",
        "11 Access Granted.wav": "ding", "10 Access Denied.wav": "wrong", "03 Grand Hit A.wav": "impact",
        "Applause.wav": "reaction", "NO COPYRIGHT BOXING BELL SOUND EFFECT.mp3": "reaction", "Ding.mp3": "ding",
        "Sci Fi UI Sounds.mp3": "tech", "18 Termainal Glitch.wav": "tech", "mixkit-trap-305.mp3": "music",
        "Podcast Background Music While Talking.mp3": "music", "CARTOON FART.wav": "comedic", "Clock Tick.mp3": "tick",
        "Writing on keyboard Sound Effect.mp3": "typing", "Spooky Wind.wav": "tension", "Mouse Click.mp3": "click",
    }
    for name, cat in cases.items():
        assert classify_name(name) == cat, name
    assert classify_name("SOUND 3.mp3") is None
    assert classify_name("007.wav") is None


def test_classify_acoustic_shapes():
    assert classify_acoustic(0.1, 0.1, 4000) == "click"
    assert classify_acoustic(0.1, 0.1, 900) == "pop"
    assert classify_acoustic(1.5, 0.9, 2000) == "riser"
    assert classify_acoustic(2.0, 0.1, 300) == "impact"
    assert classify_acoustic(1.0, 0.5, 1500) == "whoosh"


def _tone(seconds: float, amp: float = 0.5, freq: float = 800) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_measure_finds_real_start_peak_and_end():
    sig = np.concatenate([np.zeros(int(0.5 * RATE), np.float32), _tone(0.3, 0.2), _tone(0.1, 0.9),
                          np.zeros(int(0.4 * RATE), np.float32)])
    env = measure(sig)
    assert env is not None
    assert abs(env.lead - 0.5) < 0.03
    assert 0.78 <= env.peak <= 0.9
    assert abs(env.tail - 0.9) < 0.03
    assert env.loud_db > -12


def test_loudness_is_perceptual_not_flat():
    """Equal-RMS tones: sub-bass must measure much quieter than presence (it is, on a phone speaker)."""
    levels = {f: measure(_tone(1.0, 0.1, f)).loud_db for f in (40, 60, 200, 1000, 4000)}  # type: ignore[union-attr]
    assert levels[40] < levels[60] < levels[200] < levels[1000] < levels[4000]
    assert levels[1000] - levels[40] > 10  # a 40 Hz boom is not "loud" just because its RMS is high
    assert abs(levels[4000] - levels[1000]) < 5


def test_bass_heavy_impact_gets_boosted_not_cut():
    """A sub-bass boom and a bright hit at the same flat RMS should end up comparably audible."""
    boom = _item("impact", loud=measure(_tone(1.0, 0.3, 50)).loud_db)  # type: ignore[union-attr]
    bright = _item("impact", n=1, loud=measure(_tone(1.0, 0.3, 2000)).loud_db)  # type: ignore[union-attr]
    boom.peak_db = bright.peak_db = -20.0  # well below the peak ceiling, so only loudness decides
    g_boom = place_sound(boom, SoundEvent(5, "impact", "t", 5), 30).gain_db  # type: ignore[union-attr]
    g_bright = place_sound(bright, SoundEvent(5, "impact", "t", 5), 30).gain_db  # type: ignore[union-attr]
    assert g_boom > g_bright + 10


def test_measure_silence_is_none():
    assert measure(np.zeros(RATE, np.float32)) is None


def test_pack_detection_splits_separated_sounds():
    gap = np.zeros(int(0.6 * RATE), np.float32)
    sig = np.concatenate([gap, *[np.concatenate([_tone(0.3), gap]) for _ in range(8)]])
    env = measure(sig)
    assert env is not None and len(env.regions) == 8
    assert is_pack(len(sig) / RATE, env, "whoosh")
    assert not is_pack(len(sig) / RATE, env, "typing")  # beds are never sliced
    assert not is_pack(3.0, env, None)


def test_quiet_items_are_not_usable():
    assert _item("pop").usable
    assert not _item("pop", loud=-58).usable
    assert not _item("music").usable
    assert not _item("pop", pack=True).usable


# --------------------------------------------------------------------------- planning


def _words(text: str, start: float = 0.5, step: float = 0.4) -> list[dict]:
    return [{"word": w, "start": round(start + i * step, 3), "end": round(start + i * step + 0.3, 3)}
            for i, w in enumerate(text.split())]


def test_hook_entrance_gets_room():
    ev = plan_events(SoundContext(duration=30, hook=True, zooms=[(1.1, "statement"), (6.0, "statement")],
                                  style="balanced", available=set(CATEGORIES)))
    assert [e.t for e in ev] == [0.12, 6.0]


def test_hook_whoosh_only_for_hook_variants():
    base = SoundContext(duration=30, style="balanced", available=set(CATEGORIES))
    assert not any(e.hook_only for e in plan_events(base))
    ev = plan_events(SoundContext(duration=30, hook=True, style="balanced", available=set(CATEGORIES)))
    assert ev and ev[0].hook_only and ev[0].category == "whoosh" and ev[0].t < 0.3


def test_off_style_and_tiny_clips_have_no_sound():
    assert plan_events(SoundContext(duration=30, hook=True, style="off")) == []
    assert plan_events(SoundContext(duration=2, hook=True)) == []


def test_spacing_and_density_limits():
    words = _words(" ".join(f"w{i}" for i in range(140)))
    emph = [f"w{i}" for i in range(0, 140, 3)]
    ctx = SoundContext(duration=60, words=words, emphasis=emph, zooms=[(t, "statement") for t in (5, 6, 7, 20, 40)],
                       style="balanced", available=set(CATEGORIES))
    ev = plan_events(ctx)
    times = sorted(e.t for e in ev)
    assert all(b - a >= 1.4 - 1e-6 for a, b in zip(times, times[1:], strict=False))
    assert len(ev) <= 12  # 2 per 10 s
    subtle = plan_events(SoundContext(**{**ctx.__dict__, "style": "subtle"}))
    punchy = plan_events(SoundContext(**{**ctx.__dict__, "style": "punchy"}))
    assert len(subtle) < len(ev) <= len(punchy)
    assert not any(e.category == "pop" for e in subtle)


def test_payoff_gets_riser_and_impact_with_room_after():
    words = _words("this is the moment everything changed and nobody saw it coming at all")
    ctx = SoundContext(duration=40, words=words, zooms=[(18.0, "punchline")], emphasis=["everything changed"],
                       style="balanced", available=set(CATEGORIES))
    ev = plan_events(ctx)
    cats = {(e.category, e.t) for e in ev}
    assert ("riser", 18.0) in cats and ("impact", 18.0) in cats
    assert not any(18.0 < e.t < 19.6 for e in ev)


def test_keywords_fire_on_emphasis_or_unmistakable_amounts():
    words = _words("I made $40k from one simple idea but the money was not the point")
    ev = plan_events(SoundContext(duration=20, words=words, style="balanced", available=set(CATEGORIES)))
    assert any(e.category == "cash" and "$40k" in e.reason for e in ev)
    assert not any(e.category == "ding" for e in ev)  # "idea" only fires when it is an emphasis word
    ev2 = plan_events(SoundContext(duration=20, words=words, emphasis=["simple idea"], style="balanced",
                                   available=set(CATEGORIES)))
    assert any(e.category == "ding" for e in ev2)


def test_playful_sounds_need_opt_in_and_humour():
    cue = [{"t": 5.0, "category": "comedic", "reason": "joke lands"}]
    for playful, humorous, expect in ((False, True, False), (True, False, False), (True, True, True)):
        ev = plan_events(SoundContext(duration=20, ai_cues=cue, playful=playful, humorous=humorous,
                                      style="balanced", available=set(CATEGORIES)))
        assert any(e.category == "comedic" for e in ev) is expect


def test_ai_cues_are_normalised_and_unknown_dropped():
    assert normalize_category("Swoosh") == "whoosh"
    assert normalize_category("bass drop") == "impact"
    assert normalize_category("music") is None
    assert normalize_category("laser") is None
    ev = plan_events(SoundContext(duration=20, ai_cues=[{"t": 8, "category": "money", "reason": "price"},
                                                        {"t": 14, "category": "laser"}],
                                  style="balanced", available=set(CATEGORIES)))
    assert [e.category for e in ev] == ["cash"]


def test_missing_categories_are_skipped():
    ev = plan_events(SoundContext(duration=30, hook=True, style="balanced", available={"pop"}))
    assert ev == []


# --------------------------------------------------------------------------- placement & mixing


def test_peak_anchor_lands_on_event():
    it = _item("whoosh", lead=0.2, peak=0.6, tail=1.0)
    p = place_sound(it, SoundEvent(5.0, "whoosh", "t", 5), 30)
    assert p is not None
    assert abs((p.start + (0.6 - p.src_in)) - 5.0) < 1e-6


def test_onset_anchor_skips_leading_silence():
    it = _item("pop", lead=1.98, peak=1.99, tail=2.05)  # "Pop up Sound effect" has ~2 s of silence first
    p = place_sound(it, SoundEvent(3.0, "pop", "t", 2), 30)
    assert p is not None and abs(p.src_in - 1.97) < 1e-6 and abs(p.start - 2.98) < 1e-6


def test_riser_ends_on_event_and_is_clipped_at_zero():
    it = _item("riser", lead=0.0, peak=2.9, tail=3.0)
    p = place_sound(it, SoundEvent(10.0, "riser", "t", 6), 30)
    assert p is not None and abs(p.start + p.length - 10.0) < 1e-6
    early = place_sound(it, SoundEvent(1.0, "riser", "t", 6), 30)
    assert early is not None and early.start == 0 and early.fade_in > 0.01


def test_gain_levels_to_target_and_respects_peak_ceiling():
    loud = _item("pop", loud=-6.0)
    quiet = _item("pop", n=1, loud=-34.0)
    quiet.peak_db = -24.0
    g_loud = place_sound(loud, SoundEvent(5, "pop", "t", 2), 30).gain_db  # type: ignore[union-attr]
    g_quiet = place_sound(quiet, SoundEvent(5, "pop", "t", 2), 30).gain_db  # type: ignore[union-attr]
    assert g_loud < 0 < g_quiet
    spiky = _item("cash", loud=-30.0)  # quiet on average but peaks hard: the ceiling keeps it off the limiter
    spiky.peak_db = -1.0
    assert place_sound(spiky, SoundEvent(5, "cash", "t", 5), 30).gain_db <= PEAK_CEILING_DB - spiky.peak_db + 1e-6  # type: ignore[union-attr]


def test_assignment_is_deterministic_and_rotates():
    lib = _library()
    events = [SoundEvent(float(t), "whoosh", "t", 4) for t in (2, 6, 10)]
    a = assign_files(events, lib, 30, seed="p:c01")
    b = assign_files(events, lib, 30, seed="p:c01")
    assert [p.item_id for p in a] == [p.item_id for p in b]
    assert len({p.item_id for p in a}) == 3


def test_pool_skips_files_that_would_need_extreme_boost():
    usable = _item("whoosh", 0, loud=-20.0)
    too_quiet = _item("whoosh", 1, loud=-45.0)  # usable, but would need ~+28 dB
    picked = assign_files([SoundEvent(5, "whoosh", "t", 4)], [usable, too_quiet], 30, seed="x")
    assert picked[0].item_id == "whoosh0"
    only_quiet = assign_files([SoundEvent(5, "whoosh", "t", 4)], [too_quiet], 30, seed="x")
    assert only_quiet and only_quiet[0].item_id == "whoosh1"  # unless it is all we have


def test_keyword_events_are_spread_and_capped():
    words = _words(" ".join(["money"] * 400), step=1.0)
    ev = keyword_events(words, limit=5, min_gap=25, per_category=2)
    assert len(ev) == 2  # per-category cap
    mixed = _words("money idea photo typed wrong computer massive deadline " * 20, step=2.0)
    ev2 = keyword_events(mixed, limit=6, min_gap=10, per_category=2)
    assert len(ev2) == 6 and len({e.category for e in ev2}) >= 3
    times = [e.t for e in ev2]
    assert times == sorted(times) and all(b - a >= 10 for a, b in zip(times, times[1:], strict=False))
    assert not keyword_events(mixed, limit=3, min_gap=10, per_category=2, start_after=1e6)


def test_assignment_prefers_short_samples_for_small_events():
    lib = [_item("whoosh", 0, lead=0, peak=1, tail=2.5), _item("whoosh", 1, lead=0, peak=0.1, tail=0.3)]
    p = assign_files([SoundEvent(5, "whoosh", "Jump cut", 1, max_active=0.6)], lib, 30, seed="x")
    assert p[0].item_id == "whoosh1"


def _placed(duck: bool, channels: int = 2, n: int = 0) -> PlacedSound:
    return PlacedSound(item_id=f"i{n}", path=f"/lib/{n}.wav", name="x", category="whoosh", reason="r", t=1, start=1.0,
                       src_in=0.1, length=0.5, gain_db=-6, fade_in=0.005, fade_out=0.04, duck=duck, hook_only=False,
                       channels=channels)


def test_mix_cmd_structure():
    cmd = build_mix_cmd("voice.wav", [_placed(False), _placed(True, n=1)], "out.wav", 12.0)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert cmd.count("-i") == 3 and "-ss" in cmd and "-vn" in cmd
    assert "sidechaincompress" in graph and "[key]" in graph
    assert "adelay=1000:all=1" in graph
    assert "alimiter" in graph and graph.endswith("[out]")
    assert cmd[-3:] == ["-t", "12.000", "out.wav"]


def test_mix_keeps_mono_voice_mono_and_upmixes_mono_sfx_at_full_level():
    mono = build_mix_cmd("v.wav", [_placed(False, channels=1)], "o.wav", 5, voice_channels=1)
    g = mono[mono.index("-filter_complex") + 1]
    assert "channel_layouts=mono" in g and "stereo" not in g
    st = build_mix_cmd("v.wav", [_placed(False, channels=1)], "o.wav", 5, voice_channels=2)
    g2 = st[st.index("-filter_complex") + 1]
    assert "pan=stereo|c0=c0|c1=c0" in g2  # not FFmpeg's -3 dB upmix
    assert "sidechaincompress" not in g2


def test_overlay_cmd_can_swap_in_hook_audio():
    plain = build_overlay_cmd("b.mp4", "h.png", "o.mp4", fps=30, show_until=3)
    assert plain[plain.index("-c:a") + 1] == "copy"
    swapped = build_overlay_cmd("b.mp4", "h.png", "o.mp4", fps=30, show_until=3, audio="hook.wav")
    assert "hook.wav" in swapped and "2:a:0" in swapped and swapped[swapped.index("-c:a") + 1] == "aac"


# --------------------------------------------------------------------------- rules & schema


def test_campaign_can_forbid_sound_effects():
    assert sfx_permitted(None)[0]
    assert sfx_permitted(CampaignRules(music_allowed=False))[0]  # music rules alone don't forbid SFX
    ok, why = sfx_permitted(CampaignRules(source_modification_rules=["No added sound effects or music"]))
    assert not ok and "campaign rule" in why
    assert not sfx_permitted(CampaignRules(special_requirements=["Keep the original audio unaltered"]))[0]


def test_sound_cue_schema_is_forgiving():
    c = ViralCandidate(candidate_id="c01", start_time="1:00", end_time="1:30",
                       sound_cues=[{"timestamp": "1:05.5", "category": "Impact", "intensity": "strong"},
                                   {"timestamp": 70, "category": "pop", "intensity": 2}])
    assert c.sound_cues[0].timestamp == 65.5 and c.sound_cues[0].category == "impact"
    assert c.sound_cues[0].intensity == 1.15
    assert 0.7 < c.sound_cues[1].intensity < 1.15
    assert SoundCue(timestamp=1, category="x").intensity == 1.0


# --------------------------------------------------------------------------- long-form density


def _long_words() -> list[dict]:
    return _words("we spent money on the idea and it was a massive mistake we typed it wrong " * 30, step=1.4)


def test_long_form_density_by_style():
    chapters = [{"t": 0.0, "title": "Preview"}, {"t": 12.0, "title": "One"}, {"t": 200.0, "title": "Two"},
                {"t": 400.0, "title": "Three"}]
    words = _long_words()
    avail = set(CATEGORIES)
    counts = {st: len(long_form_events(st, 600, 12.0, chapters, words, avail)) for st in ("subtle", "balanced", "punchy")}
    # cold open (whoosh + impact) + the 2 chapter turns far enough past it (the 12 s one is skipped)
    assert counts["subtle"] == 4
    assert counts["balanced"] >= counts["subtle"] + 5
    assert counts["punchy"] >= counts["balanced"]
    ev = long_form_events("balanced", 600, 12.0, chapters, words, avail)
    assert ev[0].t == 12.0 and ev[0].category == "whoosh" and ev[1].category == "impact"
    times = [e.t for e in ev]
    assert times == sorted(times) and all(t <= 600 - 0.3 for t in times)


def test_long_form_respects_missing_categories_and_no_teaser():
    chapters = [{"t": 0.0, "title": "One"}, {"t": 120.0, "title": "Two"}]
    ev = long_form_events("balanced", 300, 0.0, chapters, _long_words(), {"whoosh"})
    assert all(e.category == "whoosh" for e in ev)
    assert not any(e.reason.startswith("Cold open") for e in ev)
