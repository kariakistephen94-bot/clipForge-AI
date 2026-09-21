from app.services.ffmpeg import (
    AudioOptions,
    EncodeSettings,
    build_cut_cmd,
    build_encode_cmd,
    build_extract_audio_cmd,
    build_overlay_cmd,
    build_proxy_cmd,
    parse_silencedetect,
)


def _all_str(cmd):
    return all(isinstance(x, str) for x in cmd)


def test_cut_cmd_multi_segment_with_audio():
    cmd = build_cut_cmd("/in/my video.mp4", [(10.0, 20.0), (21.0, 30.5)], "/o/cut.mp4", "/o/a.wav", fps=30,
                        out_height=1080, has_audio=True, audio=AudioOptions())
    assert _all_str(cmd)
    assert cmd[cmd.index("-ss") + 1] == "10.000" and cmd[cmd.index("-to") + 1] == "30.500"
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "trim=start=0.000:end=10.000" in fc and "trim=start=11.000:end=20.500" in fc
    assert "concat=n=2:v=1:a=1" in fc and "loudnorm=I=-14" in fc and "alimiter" in fc and "fps=30" in fc
    assert "/in/my video.mp4" in cmd  # path passed as a single argument, never shell-split
    assert cmd[-1] == "/o/a.wav" and "19.500" in cmd


def test_cut_cmd_no_audio_and_music_ducking():
    cmd = build_cut_cmd("in.mp4", [(0, 5)], "v.mp4", "a.wav", fps=30, out_height=721, has_audio=False, audio=AudioOptions())
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "anullsrc" in fc and "scale=-2:720" in fc and "concat" not in fc
    cmd = build_cut_cmd("in.mp4", [(0, 5)], "v.mp4", "a.wav", fps=30, out_height=1080, has_audio=True,
                        audio=AudioOptions(music_path="music.mp3", denoise=False))
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "-stream_loop" in cmd and "sidechaincompress" in fc and "amix" in fc and "afftdn" not in fc


def test_encode_cmd_platform_settings():
    cmd = build_encode_cmd("a.wav", "out.mp4", EncodeSettings())
    s = " ".join(cmd)
    assert "-s 1080x1920" in s and "libx264" in s and "yuv420p" in s and "-c:a aac" in s and "+faststart" in s
    assert cmd[cmd.index("-i") + 1] == "-"


def test_overlay_proxy_audio_cmds():
    ov = build_overlay_cmd("base.mp4", "hook.png", "A.mp4", fps=30, show_until=3.5)
    fc = ov[ov.index("-filter_complex") + 1]
    assert "fade=t=out:st=3.500:d=0.300:alpha=1" in fc and "-c:a" in ov and "copy" in ov
    px = build_proxy_cmd("in.mov", "p.mp4", 2160, has_audio=False)
    assert "scale=-2:540" in px and "-an" in px
    au = build_extract_audio_cmd("in.mp4", "a.wav", audio_index=1)
    assert "0:a:1" in au and "16000" in au


def test_parse_silencedetect():
    stderr = """[silencedetect @ 0x1] silence_start: 1.25
[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 1.25
[silencedetect @ 0x1] silence_start: 9.0"""
    assert parse_silencedetect(stderr, offset=100, clip_end=110) == [(101.25, 102.5), (109.0, 110)]
