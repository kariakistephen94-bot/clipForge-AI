import numpy as np

from app.services.reframe import Keyframe, ShotPlan, camera_at, crop_rect, detect_shot_cuts, smooth_path
from app.services.silence import SilenceConfig, map_time, plan_keep_segments, remap_words, total_duration
from app.services.zoom import plan_zooms, zoom_at


def test_silence_removes_confirmed_long_gap_only(words_factory):
    words = words_factory("this is a normal sentence and then a long gap happens here", pauses={6: 1.5, 3: 0.5})
    gap_start, gap_end = words[5]["end"], words[6]["start"]
    segs, removed = plan_keep_segments(words, 0, words[-1]["end"], SilenceConfig(), confirmed_silences=[(gap_start, gap_end)])
    assert len(removed) == 1 and len(segs) == 2
    # a natural pause is left in place
    assert segs[1][0] - segs[0][1] < gap_end - gap_start
    assert abs((gap_end - gap_start) - removed[0]["seconds"] - SilenceConfig().keep_pause) < 0.01
    # without confirmation from silencedetect (e.g. music under speech) nothing is cut
    segs2, removed2 = plan_keep_segments(words, 0, words[-1]["end"], SilenceConfig(), confirmed_silences=[])
    assert removed2 == [] and len(segs2) == 1


def test_silence_keeps_dramatic_pause_unless_aggressive(words_factory):
    words = words_factory("do you know why? because nobody tells you", pauses={4: 1.2})
    confirmed = [(0, 100)]
    _, removed = plan_keep_segments(words, 0, 10, SilenceConfig(), confirmed)
    assert removed == []
    _, removed = plan_keep_segments(words, 0, 10, SilenceConfig(aggressive=True), confirmed)
    assert len(removed) == 1


def test_timeline_mapping():
    segs = [(10.0, 20.0), (25.0, 30.0)]
    assert total_duration(segs) == 15
    assert map_time(12, segs) == 2 and map_time(26, segs) == 11 and map_time(22, segs) is None
    words = [{"start": 19.5, "end": 19.9, "word": "a"}, {"start": 25.2, "end": 25.6, "word": "b"}]
    out = remap_words(words, segs)
    assert out[0]["start"] == 9.5 and out[1]["start"] == 10.2


def test_zoom_plan_is_sparse_and_subtle(words_factory):
    words = words_factory(" ".join(["word."] * 120))
    dur = words[-1]["end"]
    pts = [(t, "punchline") for t in np.arange(1, dur, 2.0)]
    events = plan_zooms(dur, words, pts, [], shot_cuts=[])
    assert 1 <= len(events) <= dur // 8
    starts = [e.start for e in events]
    assert all(b - a >= 6 for a, b in zip(starts, starts[1:], strict=False))
    assert all(1.0 < e.scale <= 1.12 for e in events)
    e = events[0]
    assert zoom_at(e.start - 0.5, events) == 1.0 and zoom_at((e.start + e.end) / 2, events) == e.scale
    assert plan_zooms(dur, words, pts, [], enabled=False) == []


def test_zoom_never_crosses_shot_cut(words_factory):
    words = words_factory("this is a long sentence that keeps going and going for a while.")
    ev = plan_zooms(30, words, [(1.0, "statement")], [], shot_cuts=[2.5])
    assert ev and ev[0].end <= 2.5


def test_smooth_path_deadzone_ignores_jitter():
    samples = [(i / 6, 960 + (10 if i % 2 else -10), 540, 200) for i in range(60)]
    kfs = smooth_path(samples, crop_w=607, shot_start=0)
    assert len(kfs) == 1


def test_smooth_path_moves_on_sustained_shift():
    samples = [(i / 6, 700 if i < 30 else 1200, 540, 200) for i in range(60)]
    kfs = smooth_path(samples, crop_w=607, shot_start=0)
    assert len(kfs) == 3 and kfs[-1].cx == 1200 and kfs[-1].t > kfs[-2].t


def test_camera_hard_cut_and_crop_bounds():
    shot = ShotPlan(0, 10, "track", [Keyframe(0, 500, 540, 200), Keyframe(4.999, 500, 540, 200), Keyframe(5, 1500, 540, 200)])
    assert camera_at(shot, 5.2).cx == 1500
    x, y, w, h = crop_rect(1920, 1080, Keyframe(0, 1900, 540, 200), 1.0)
    assert (w, h) == (608, 1080) and x + w <= 1920 and y == 0
    x, y, w, h = crop_rect(1920, 1080, Keyframe(0, 960, 100, 200), 1.12)
    assert h < 1080 and y >= 0 and w / h == 9 / 16 or abs(w / h - 9 / 16) < 0.01


def test_shot_cut_detection():
    import cv2

    def hist(color):
        img = np.full((90, 160, 3), color, np.uint8)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0, 1], None, [24, 12], [0, 180, 0, 256])
        cv2.normalize(h, h)
        return h

    hists = [hist((200, 30, 30))] * 10 + [hist((30, 200, 30))] * 10
    times = [i / 6 for i in range(20)]
    assert detect_shot_cuts(hists, times) == [times[10]]
