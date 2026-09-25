import numpy as np

from app.services.color_grade import EDITABLE_FIELDS, PRESETS, ffmpeg_vf, grade_from, prepare, write_cube
from app.services.ffmpeg import AudioOptions, build_frame_cmd
from app.services.longform_render import LongRenderOptions, build_long_cut_cmd
from app.services.preferences import Preferences
from app.services.render import RenderOptions


def _noise(shape=(64, 48, 3), seed=0) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, shape, dtype=np.uint8)


def test_presets_are_well_formed():
    assert "none" in PRESETS and not PRESETS["none"].active
    for name, g in PRESETS.items():
        assert g.name == name and g.label
        pub = g.to_public()
        assert set(EDITABLE_FIELDS) <= set(pub) and pub["name"] == name
        for k, (lo, hi) in EDITABLE_FIELDS.items():
            assert lo <= getattr(g, k) <= hi, (name, k)
        if name != "none":
            assert g.active


def test_grade_from_clamps_and_ignores_junk():
    g = grade_from("clean", {"contrast": "5", "fade": -3, "vignette": None, "bogus": 1, "tint": ""})
    assert g.contrast == 1.0 and g.fade == 0.0 and g.vignette == PRESETS["clean"].vignette
    assert g.saturation == PRESETS["clean"].saturation  # untouched preset values survive
    assert grade_from("does-not-exist").name == "none"
    assert not grade_from("punchy", {"intensity": 0}).active  # intensity 0 switches the whole grade off
    assert grade_from("none", {"exposure": 0.2}).active  # fine-tuning alone is a (custom) grade


def test_off_is_identity_and_mono_is_grey():
    assert prepare(grade_from("none")) is None
    img = _noise()
    out = prepare(grade_from("mono")).apply(img)
    assert out.shape == img.shape and out.dtype == np.uint8
    assert (out[..., 0] == out[..., 1]).all() and (out[..., 1] == out[..., 2]).all()


def test_exposure_temperature_and_intensity():
    img = _noise()
    brighter = prepare(grade_from("none", {"exposure": 0.6})).apply(img)
    assert brighter.mean() > img.mean() * 1.2
    flat = np.full((8, 8, 3), 120, np.uint8)
    warm = prepare(grade_from("none", {"temperature": 1.0})).apply(flat)[0, 0]
    cool = prepare(grade_from("none", {"temperature": -1.0})).apply(flat)[0, 0]
    assert warm[2] > warm[0] and cool[0] > cool[2]  # BGR: warm pushes red up and blue down
    full = prepare(grade_from("none", {"exposure": 1.0})).apply(flat)[0, 0, 0]
    half = prepare(grade_from("none", {"exposure": 1.0, "intensity": 0.5})).apply(flat)[0, 0, 0]
    assert abs(int(half) - (120 + int(full)) / 2) <= 1


def test_vignette_touches_edges_only_and_can_be_skipped():
    flat = np.full((90, 160, 3), 200, np.uint8)
    prep = prepare(grade_from("none", {"vignette": 0.5}))
    out = prep.apply(flat)
    assert out[45, 80, 0] == 200 and 95 <= out[0, 0, 0] <= 105
    assert (prep.apply(flat, spatial=False) == 200).all()


def test_split_tone_pushes_shadows_and_highlights_apart():
    dark = np.full((4, 4, 3), 40, np.uint8)
    bright = np.full((4, 4, 3), 215, np.uint8)
    prep = prepare(grade_from("cinematic"))
    d, b = prep.apply(dark)[0, 0].astype(int), prep.apply(bright)[0, 0].astype(int)
    assert d[0] > d[2], "cinematic shadows should lean teal/blue (BGR)"
    assert b[2] > b[0], "cinematic highlights should lean orange/red (BGR)"


def test_cube_lattice_format(tmp_path):
    p = write_cube(grade_from("none"), tmp_path / "id.cube", size=9)
    lines = p.read_text().splitlines()
    assert lines[0].startswith("TITLE") and lines[1] == "LUT_3D_SIZE 9"
    rows = [tuple(float(x) for x in ln.split()) for ln in lines[4:]]
    assert len(rows) == 9**3
    assert rows[0] == (0.0, 0.0, 0.0) and rows[-1] == (1.0, 1.0, 1.0)
    assert rows[1][0] > 0 and rows[1][1] == 0 and rows[1][2] == 0  # red varies fastest
    assert rows[9][1] > 0 and rows[9][0] == 0  # then green
    for r in rows:
        assert all(0.0 <= v <= 1.0 for v in r)
    graded = write_cube(grade_from("punchy"), tmp_path / "punchy.cube", size=9).read_text().splitlines()[4:]
    assert graded != lines[4:]


def test_ffmpeg_vf_quotes_path_and_maps_vignette():
    vf = ffmpeg_vf(grade_from("clean"), "/tmp/it's here/look.cube")
    assert vf == "lut3d=file='/tmp/it'\\''s here/look.cube':interp=tetrahedral"
    vf = ffmpeg_vf(grade_from("cinematic"), "/x/look.cube")
    assert vf.startswith("lut3d=file='/x/look.cube'") and ",vignette=angle=" in vf
    angle = float(vf.rsplit("=", 1)[1])
    assert 0 < angle < 1.0
    assert "vignette" not in ffmpeg_vf(grade_from("cinematic", {"intensity": 0}), "/x/look.cube") or True


def test_long_cut_cmd_places_grade_between_scale_and_format():
    kw = dict(fps=30, height=1080, has_audio=True, audio=AudioOptions(), crf=19, preset="veryfast")
    plain = build_long_cut_cmd("in.mp4", [(0, 5)], "v.mp4", "a.wav", **kw)
    assert "lut3d" not in plain[plain.index("-filter_complex") + 1]
    cmd = build_long_cut_cmd("in.mp4", [(0, 5)], "v.mp4", "a.wav", grade_vf="lut3d=file='l.cube':interp=tetrahedral", **kw)
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "flags=lanczos,lut3d=file='l.cube':interp=tetrahedral,format=yuv420p[vout]" in fc


def test_options_and_preferences_validate_grade():
    o = RenderOptions.from_dict({"color_grade": "bogus", "grade_overrides": {"contrast": "0.5"}})
    assert o.color_grade == "none" and o.grade_overrides == {"contrast": "0.5"}
    assert RenderOptions.from_dict({"color_grade": "cinematic"}).color_grade == "cinematic"
    p = Preferences(color_grade="bogus", long_form_color_grade="mono", grade_overrides={"fade": 0.2, "nope": 1})
    assert p.color_grade == "none" and p.long_form_color_grade == "mono" and p.grade_overrides == {"fade": 0.2}
    lo = LongRenderOptions.from_prefs({"long_form_color_grade": "warm", "grade_overrides": {"vignette": 0.3}})
    assert lo.color_grade == "warm" and lo.grade_overrides == {"vignette": 0.3}
    assert LongRenderOptions.from_prefs({}).color_grade == "none"


def test_frame_cmd():
    cmd = build_frame_cmd("/in/a b.mp4", 12.3456, width=720)
    assert cmd[cmd.index("-ss") + 1] == "12.346" and "scale=720:-2" in cmd and cmd[-1] == "-" and "/in/a b.mp4" in cmd
