import csv
import json

from app.ai.provider import AIProvider
from app.ai.service import cached_call
from app.db import session_scope
from app.models import Project
from app.paths import project_dir
from app.schemas.ai import CampaignRules, HookVariants
from app.services.compliance import ClipFacts, evaluate_compliance
from app.services.export import PLAN_COLUMNS, build_metadata, build_ready_to_post
from app.services.posting import assemble_posting_copy
from app.utils.hashing import cache_key, sha256_file, sha256_text

RULES = CampaignRules(min_duration=20, max_duration=60, platforms=["tiktok"], required_hashtags=["#Lessons"],
                      required_mentions=["@brand"], split_screen_allowed=False, captions_allowed=True)


def test_metadata_has_required_keys():
    m = build_metadata(candidate_id="c01", source_file="talk.mp4", start=1.23456, end=40, duration=38.7654, viral_score=87.26,
                       topic="t", hook="h", alternative_hooks=["a", "b"], campaign="camp", platforms=["tiktok"],
                       campaign_compliance="COMPLIANT", hashtags=["#x"], extra={"notes": ["n"], "hook": "ignored"})
    for k in ("candidate_id", "source_file", "start", "end", "duration", "viral_score", "topic", "hook", "alternative_hooks",
              "campaign", "platforms", "campaign_compliance", "hashtags", "created_at"):
        assert k in m
    assert m["start"] == 1.235 and m["viral_score"] == 87.3 and m["hook"] == "h" and m["notes"] == ["n"]
    json.dumps(m)


def test_compliance_statuses():
    copy = assemble_posting_copy(HookVariants(candidate_id="c", tiktok_caption="Great lesson", hashtags=["#money"]), RULES,
                                 ["tiktok"], "fallback")
    text = "\n".join(copy["full"].values())
    assert "#Lessons" in text and "@brand" in text
    ok = evaluate_compliance(RULES, ClipFacts(duration=30, platforms=["tiktok"], posting_text=text))
    assert ok.status == "COMPLIANT"
    too_long = evaluate_compliance(RULES, ClipFacts(duration=75, platforms=["tiktok"], posting_text=text))
    assert too_long.status == "FAILED" and any(c.rule == "Duration" for c in too_long.checks)
    split = evaluate_compliance(RULES, ClipFacts(duration=30, split_used=True, posting_text=text))
    assert split.status == "FAILED"
    missing = evaluate_compliance(RULES, ClipFacts(duration=30, posting_text="no tags"))
    assert missing.status == "FAILED"
    extra_platform = evaluate_compliance(RULES, ClipFacts(duration=30, platforms=["tiktok", "instagram"], posting_text=text))
    assert extra_platform.status == "WARNING"
    candidate_stage = evaluate_compliance(RULES, ClipFacts(duration=30, platforms=["tiktok"]))
    assert candidate_stage.status == "COMPLIANT" and any("#Lessons" in m for m in candidate_stage.manual_checks)
    ai_fail = evaluate_compliance(RULES, ClipFacts(duration=30, posting_text=text, ai_status="FAILED", ai_reasons=["misleading"]))
    assert ai_fail.status == "FAILED"
    assert evaluate_compliance(None, ClipFacts(duration=30), has_rules_text=False).status == "WARNING"


def test_ready_to_post_and_posting_plan(workspace):
    pid = "ptestready"
    exp = project_dir(pid, "exports")
    entries = []
    for n, (status, score) in enumerate([("COMPLIANT", 70), ("FAILED", 95), ("WARNING", 88)], 1):
        folder = exp / f"clip_{n:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"clip_{n:03d}.mp4").write_bytes(b"fake")
        (folder / "metadata.json").write_text("{}")
        copy = assemble_posting_copy(None, RULES, ["tiktok", "youtube_shorts"], f"Hook {n}")
        entries.append({"export_folder": str(folder), "topic": f"Topic {n}", "hook": f"Hook {n}", "viral_score": score,
                        "copy": copy, "compliance_status": status})
    root, count = build_ready_to_post(pid, entries, "Camp")
    assert count == 2
    dirs = sorted(d.name for d in root.iterdir() if d.is_dir())
    assert dirs == ["01_topic_3", "02_topic_1"]  # FAILED excluded, sorted by score
    rows = list(csv.DictReader((root / "posting_plan.csv").open()))
    assert list(rows[0].keys()) == PLAN_COLUMNS
    assert len(rows) == 4 and all(r["status"] == "READY" for r in rows)
    assert rows[0]["file_path"].endswith("01_topic_3/clip_003.mp4")


def test_hashing(tmp_path):
    f = tmp_path / "v.bin"
    f.write_bytes(b"abc" * 1000)
    assert sha256_file(f, chunk_size=7) == sha256_file(f)
    assert len(sha256_text("x")) == 64
    assert cache_key(a=1, b=[1, 2]) == cache_key(b=[1, 2], a=1) != cache_key(a=2, b=[1, 2])


class CountingProvider(AIProvider):
    name = "fake"
    model = "fake-1"

    def __init__(self):
        self.calls = 0

    def prompt_version(self, operation):
        return "p@1"

    def parse_campaign_rules(self, rules_text):
        self.calls += 1
        return CampaignRules(max_duration=42)

    def generate_candidates(self, ctx):
        raise NotImplementedError

    def generate_hooks(self, *a):
        raise NotImplementedError

    def check_compliance(self, *a):
        raise NotImplementedError

    def refine_candidate(self, *a):
        raise NotImplementedError


def test_ai_cache_reuses_identical_requests():
    with session_scope() as s:
        s.merge(Project(id="pcache", name="cache"))
    prov = CountingProvider()
    r1, c1 = cached_call("pcache", prov, "campaign_rules", {"rules": "h1"}, lambda: prov.parse_campaign_rules("x"), CampaignRules)
    r2, c2 = cached_call("pcache", prov, "campaign_rules", {"rules": "h1"}, lambda: prov.parse_campaign_rules("x"), CampaignRules)
    r3, c3 = cached_call("pcache", prov, "campaign_rules", {"rules": "h2"}, lambda: prov.parse_campaign_rules("x"), CampaignRules)
    assert (c1, c2, c3) == (False, True, False)
    assert prov.calls == 2 and r2.max_duration == 42
    with session_scope() as s:
        assert s.get(Project, "pcache").cached_requests == 1
