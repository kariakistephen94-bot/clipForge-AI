from pathlib import Path

import numpy as np

from app.ai.demo import DemoProvider
from app.ai.jsonrepair import gemini_json_schema
from app.ai.provider import LongFormContext
from app.candidates.longform import clean_chapters, dedupe_long, long_form_target, select_count
from app.schemas.ai import Chapter, HookVariants, LongFormCandidate, LongFormList, ThumbnailConcept
from app.services.ffmpeg import AudioOptions
from app.services.longform_render import (
    build_long_cut_cmd,
    draw_thumbnail_text,
    ffmetadata,
    output_chapters,
    select_expr,
    youtube_chapters_valid,
)
from app.services.thumbnail_prompts import build_prompts, prompts_text
from app.utils.timeutil import fmt_chapter

# --------------------------------------------------------------------------- how many


def test_target_scales_with_source_length():
    counts = {m: long_form_target(m * 60, 300, 1200, 10).count for m in (5, 10, 15, 19, 47, 58, 85, 175)}
    assert counts[5] == 0  # too short: the source already is the long-form video
    assert counts[10] == 1
    assert 1 <= counts[15] <= 2 and 1 <= counts[19] <= 2
    assert 4 <= counts[47] <= 6 and 4 <= counts[58] <= 6
    assert counts[175] >= 8
    assert all(counts[a] <= counts[b] for a, b in zip(sorted(counts), sorted(counts)[1:], strict=False))


def test_target_respects_cap_modes_and_minimum_length():
    assert long_form_target(3 * 3600, 300, 1200, 4).count == 4
    assert long_form_target(3600, 300, 1200, 10, mode="off").count == 0
    assert long_form_target(3600, 300, 1200, 10, mode="manual", manual_count=2).count == 2
    assert long_form_target(3600, 900, 1200, 10, mode="manual", manual_count=20).count <= 3  # can't fit 20 x 15 min
    t = long_form_target(20 * 60, 900, 1800, 10)  # 20 min source, 15 min minimum
    assert t.count == 0 and "too short" in t.note


def test_quality_gate_selection():
    kept = [{"score": s} for s in (88, 71, 52, 41)]
    assert select_count(kept, 5) == 3
    assert select_count([{"score": 44}], 3) == 1  # never zero when something exists
    assert select_count([], 3) == 0


# --------------------------------------------------------------------------- dedupe & chapters


def _cand(cid: str, s: float, e: float) -> LongFormCandidate:
    return LongFormCandidate(candidate_id=cid, start_time=s, end_time=e)


def test_dedupe_keeps_stronger_overlapping_segment():
    items = [{"cand": _cand("L01", 0, 600), "start": 0, "end": 600, "score": 70},
             {"cand": _cand("L02", 300, 900), "start": 300, "end": 900, "score": 80},
             {"cand": _cand("L03", 900, 1500), "start": 900, "end": 1500, "score": 60}]
    kept, removed = dedupe_long(items)
    assert [k["cand"].candidate_id for k in kept] == ["L02", "L03"]
    assert [k["rank"] for k in kept] == [1, 2]
    assert removed[0]["candidate_id"] == "L01" and removed[0]["kept"] == "L02"


def test_clean_chapters_clamps_spaces_and_starts_at_zero():
    chs = [Chapter(timestamp=110, title="Intro"), Chapter(timestamp=90, title="Early"), Chapter(timestamp=120, title="Too close"),
           Chapter(timestamp=300, title="Middle"), Chapter(timestamp=695, title="Tail end")]
    out = clean_chapters(chs, 100, 700)
    assert out[0]["t"] == 100 and [c["title"] for c in out] == ["Early", "Middle"]


def test_output_chapters_with_cold_open_and_silence_cuts():
    main = [(100.0, 200.0), (210.0, 400.0)]  # 10 s of dead air removed at 200-210
    out = output_chapters([{"t": 100, "title": "Start"}, {"t": 250, "title": "Next"}], main, teaser=12.0)
    assert out[0] == {"t": 0.0, "title": "Preview"}
    assert out[1]["t"] == 12.0 and out[2]["t"] == 12.0 + 100 + 40
    assert youtube_chapters_valid(out, 12 + 290)
    assert not youtube_chapters_valid(out[:2], 300)
    assert not youtube_chapters_valid([{"t": 0, "title": "a"}, {"t": 5, "title": "b"}, {"t": 50, "title": "c"}], 100)


def test_chapter_formatting_and_ffmetadata():
    assert fmt_chapter(0) == "0:00" and fmt_chapter(245) == "4:05" and fmt_chapter(3723) == "1:02:03"
    meta = ffmetadata([{"t": 0, "title": "A=B"}, {"t": 30.5, "title": "Next"}], 90)
    assert meta.startswith(";FFMETADATA1") and "START=30500" in meta and "END=90000" in meta and "title=A\\=B" in meta


# --------------------------------------------------------------------------- render commands


def test_long_cut_uses_one_select_expression():
    segs = [(100.0, 150.0), (151.0, 220.0), (221.5, 400.0)]
    cmd = build_long_cut_cmd("src.mp4", segs, "v.mp4", "a.wav", fps=30, height=1080, has_audio=True,
                             audio=AudioOptions(), crf=19, preset="veryfast")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert cmd[cmd.index("-ss") + 1] == "100.000" and cmd[cmd.index("-to") + 1] == "400.000"
    assert graph.count("between(t,") == 6  # 3 in select + 3 in aselect
    assert "between(t,0.000,50.000)" in graph and "setpts=N/FRAME_RATE/TB" in graph and "asetpts=N/SR/TB" in graph
    assert "loudnorm" in graph and "scale=-2:1080" in graph
    assert select_expr([(5, 6)], 5) == "between(t,0.000,1.000)"


# --------------------------------------------------------------------------- schemas & prompts


def test_long_form_schema_is_forgiving():
    lst = LongFormList.model_validate({"items": [
        {"candidate_id": "L01", "start_time": "10:00", "end_time": "18:30", "title": "  A   title ",
         "cold_open": {"start_time": 700, "end_time": 690}, "chapters": [{"timestamp": "10:00", "title": "x"}, {"bad": 1}],
         "thumbnail_concepts": [{"text_overlay": "one two three four five six", "frame_timestamp": "12:00"}] * 5},
        {"candidate_id": "", "start_time": 0, "end_time": 5},
    ]})
    c = lst.items[0]
    assert len(lst.items) == 1 and c.title == "A title" and c.cold_open is None and len(c.chapters) == 1
    assert len(c.thumbnail_concepts) == 3 and c.thumbnail_concepts[0].text_overlay == "one two three four five"
    assert c.thumbnail_concepts[0].frame_timestamp == 720


def test_gemini_schema_keeps_fields_named_title():
    props = gemini_json_schema(LongFormList)["properties"]["items"]["items"]["properties"]
    assert "title" in props and "title" in props["chapters"]["items"]["properties"]


def test_hook_variants_accept_cover_and_thumbnail():
    hv = HookVariants.model_validate({"candidate_id": "c01", "cover_text": "WAIT WHAT", "thumbnail": {"emotion": "shock"}})
    assert hv.thumbnail is not None and hv.thumbnail.emotion == "shock"
    assert HookVariants.model_validate({"candidate_id": "c01", "thumbnail": "junk"}).thumbnail is None


def test_thumbnail_prompts_are_complete_and_honest():
    c = ThumbnailConcept(concept="Shock", emotion="shock", subject="The host, wide eyes, hand on mouth.",
                         scene="Dark studio.", visual_elements=["a stack of cash"], text_overlay="HE SAID NO",
                         composition="face right third", frame_timestamp=42.0)
    p = build_prompts(c, title="Why He Turned Down $1B", reference_file="thumbnail_frame_A.jpg")
    assert "16:9" in p["prompt"] and '"HE SAID NO"' in p["prompt"] and "stack of cash" in p["prompt"]
    assert ".," not in p["prompt"] and ".," not in p["reference_prompt"]
    assert p["reference_prompt"].startswith("Using thumbnail_frame_A.jpg") and "identity" in p["reference_prompt"]
    assert "--ar 16:9" in p["midjourney"] and "watermark" in p["negative_prompt"]
    vertical = build_prompts(ThumbnailConcept(), aspect="9:16", speakers="a woman in a red jacket")
    assert "9:16" in vertical["prompt"] and "red jacket" in vertical["prompt"] and "no text" in vertical["prompt"]
    assert "bottom 20%" in vertical["reference_prompt"] and "bottom-right" not in vertical["reference_prompt"]
    txt = prompts_text("T", [{**p, "reference_file": "thumbnail_frame_A.jpg"}], ["Alt"])
    assert "THUMBNAIL A" in txt and "CTR CHECKLIST" in txt and "ALTERNATIVE TITLES: Alt" in txt


def test_draw_thumbnail_text_puts_text_away_from_face():
    img = np.full((720, 1280, 3), 60, np.uint8)
    left = draw_thumbnail_text(img, "BIG NEWS", face_x=0.8)
    right = draw_thumbnail_text(img, "BIG NEWS", face_x=0.2)
    assert (left[:, :640] != 60).sum() > (left[:, 640:] != 60).sum()
    assert (right[:, 640:] != 60).sum() > (right[:, :640] != 60).sum()
    assert (draw_thumbnail_text(img, "", None) == img).all()


# --------------------------------------------------------------------------- demo provider


def test_demo_long_form_from_transcript(words_factory):
    text = " ".join(f"Sentence number {i} is about money and a big mistake." for i in range(400))
    words = words_factory(text, word_dur=0.35, gap=0.05)
    transcript = {"segments": [{"start": words[0]["start"], "end": words[-1]["end"], "text": text,
                                "words": [{"start": w["start"], "end": w["end"], "word": w["word"]} for w in words]}]}
    dur = words[-1]["end"]
    ctx = LongFormContext(project_name="p", campaign_name="", source_duration=dur, source_sha256="x" * 64,
                          target_count=2, min_duration=300, max_duration=900,
                          video_analysis={"speakers": [{"description": "a man in a grey hoodie"}]},
                          transcript=transcript, transcript_prompt="")
    out = DemoProvider().generate_long_form(ctx)
    assert 1 <= len(out.items) <= 2
    it = out.items[0]
    assert it.title.startswith("[DEMO]") and it.end_time - it.start_time >= 300 * 0.9
    assert it.thumbnail_concepts and "grey hoodie" in it.thumbnail_concepts[0].subject
    assert it.chapters and it.chapters[0].timestamp == it.start_time
    if len(out.items) == 2:
        a, b = out.items
        assert a.end_time <= b.start_time or b.end_time <= a.start_time


def test_ready_long_excludes_failed(tmp_path: Path, workspace):
    from app.services.export import build_ready_long

    good = tmp_path / "long_001"
    bad = tmp_path / "long_002"
    for f in (good, bad):
        f.mkdir()
        (f / f"{f.name}.mp4").write_bytes(b"x")
        (f / "thumbnail_draft_A.jpg").write_bytes(b"x")
        (f / "description.txt").write_text("d")
    root, n = build_ready_long("ptestlong", [
        {"folder": str(good), "title": "Great one", "score": 80, "duration": 600, "status": "COMPLIANT", "rank": 1},
        {"folder": str(bad), "title": "Bad", "score": 90, "duration": 600, "status": "FAILED", "rank": 2}])
    assert n == 1
    assert (root / "01_great_one" / "long_001.mp4").exists()
    plan = (root / "long_form_plan.csv").read_text()
    assert "Great one" in plan and "Bad" not in plan and "thumbnail_draft_A.jpg" in plan
