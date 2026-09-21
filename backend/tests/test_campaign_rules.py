from app.ai.rules_heuristic import parse_durations, parse_rules_heuristic
from app.schemas.ai import CampaignRules

RULES = """Clips must be between 20 and 60 seconds.
Post on TikTok and YouTube Shorts.
Include #MoneyLessons and tag @founderstories in every post.
CTA: Follow for part 2
Captions are allowed.
No split screen.
Do not use AI voiceovers.
No misleading titles or fake claims.
Accounts must be public."""


def test_parse_durations_variants():
    assert parse_durations("between 20 and 60 seconds") == (20, 60)
    assert parse_durations("15-45s clips") == (15, 45)
    assert parse_durations("at least 15 seconds, no longer than 1 minute") == (15, 60)
    assert parse_durations("under 90 sec") == (None, 90)
    assert parse_durations("make great clips") == (None, None)


def test_heuristic_full_rules():
    r = parse_rules_heuristic(RULES)
    assert (r.min_duration, r.max_duration) == (20, 60)
    assert r.platforms == ["tiktok", "youtube_shorts"]
    assert r.required_hashtags == ["#MoneyLessons"]
    assert r.required_mentions == ["@founderstories"]
    assert r.required_cta == "Follow for part 2"
    assert r.captions_allowed is True
    assert r.split_screen_allowed is False
    assert any("voiceover" in x.lower() for x in r.source_modification_rules)
    assert any("misleading" in x.lower() for x in r.prohibited_content)
    assert any("public" in x.lower() for x in r.special_requirements)


def test_heuristic_does_not_invent_rules():
    r = parse_rules_heuristic("Clip the best moments from the podcast.")
    assert r.min_duration is None and r.max_duration is None
    assert r.captions_allowed is None and r.broll_allowed is None and r.split_screen_allowed is None
    assert r.required_hashtags == [] and r.required_cta is None
    for f in ("min_duration", "captions_allowed", "broll_allowed", "required_hashtags"):
        assert f in r.unknown_requirements


def test_campaign_rules_validation_normalises():
    r = CampaignRules.model_validate({
        "min_duration": "1:00", "max_duration": 30, "platforms": ["TikTok", "Instagram Reels", "YouTube Shorts"],
        "required_hashtags": ["ad", "#Ad", "sponsored"], "required_mentions": "brand", "required_cta": "unknown",
        "captions_allowed": None,
    })
    assert (r.min_duration, r.max_duration) == (30, 60)  # swapped into order
    assert r.platforms == ["tiktok", "instagram", "youtube_shorts"]
    assert r.required_hashtags == ["#ad", "#sponsored"]
    assert r.required_mentions == ["@brand"]
    assert r.required_cta is None
    assert r.captions_allowed is None
