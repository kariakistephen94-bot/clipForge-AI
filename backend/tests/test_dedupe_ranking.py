from app.candidates.dedupe import DedupeItem, dedupe, time_overlap_ratio
from app.candidates.scoring import final_score, local_penalties, rank
from app.schemas.ai import ViralCandidate


def test_overlap_ratio():
    assert time_overlap_ratio(0, 10, 5, 15) == 0.5
    assert time_overlap_ratio(0, 10, 2, 6) == 1.0
    assert time_overlap_ratio(0, 10, 10, 20) == 0.0


def test_dedupe_keeps_higher_score_on_heavy_overlap():
    a = DedupeItem("a", 100, 140, 70, text="I lost forty thousand dollars", hook="Lost 40k", topic="money mistake")
    b = DedupeItem("b", 102, 138, 85, text="I lost forty thousand dollars fast", hook="The 40k mistake", topic="money mistake")
    kept, removed = dedupe([a, b])
    assert [k.key for k in kept] == ["b"]
    assert removed[0].removed.key == "a" and "overlap" in removed[0].reason


def test_dedupe_allows_partial_overlap_with_different_narratives():
    a = DedupeItem("a", 100, 140, 80, text="packaging costs twelve thousand", hook="12k on boxes", topic="packaging waste",
                   summary="spent on packaging before orders")
    b = DedupeItem("b", 120, 170, 75, text="angry customers became loyal buyers", hook="Complainers are gold",
                   topic="customer complaints", summary="complaining customers turned into best customers")
    kept, _ = dedupe([a, b])
    assert len(kept) == 2


def test_dedupe_same_idea_different_time():
    a = DedupeItem("a", 10, 40, 60, text="sell before you build validate demand with preorders first",
                   topic="validate before building", summary="sell before you build using preorders")
    b = DedupeItem("b", 500, 530, 90, text="validate demand with preorders sell before you build it",
                   topic="validate before building", summary="use preorders to sell before you build")
    kept, removed = dedupe([a, b])
    assert [k.key for k in kept] == ["b"] and removed


def test_rank_orders_by_score_then_hook():
    items = [
        {"viral_score": 70, "subscores": {"hook": 10}, "start": 5},
        {"viral_score": 90, "subscores": {"hook": 12}, "start": 50},
        {"viral_score": 90, "subscores": {"hook": 18}, "start": 80},
    ]
    ranked = rank(items)
    assert [i["start"] for i in ranked] == [80, 50, 5]
    assert [i["rank"] for i in ranked] == [1, 2, 3]


def test_local_penalties_and_final_score():
    pens = local_penalties("Hey guys, welcome back to the channel", "sponsored by acme, use code SAVE", ["Starts mid-sentence (x)"],
                           duration=70, min_duration=15, max_duration=60)
    reasons = " ".join(p.reason for p in pens)
    assert "greeting" in reasons and "sponsor" in reasons and "mid-thought" in reasons and "duration" in reasons
    c = ViralCandidate.model_validate({"candidate_id": "c1", "start_time": 0, "end_time": 30,
                                       "subscores": {"hook": 18, "clarity": 12, "curiosity": 12, "emotion": 7, "specificity": 8,
                                                     "shareability": 7, "retention": 8, "visual": 4, "campaign_fit": 4},
                                       "penalties": [{"reason": "x", "points": 2}]})
    total = 18 + 12 + 12 + 7 + 8 + 7 + 8 + 4 + 4 - 2 - sum(p.points for p in pens)
    assert final_score(c, pens) == round(total, 1)


def test_final_score_falls_back_to_model_score_without_subscores():
    c = ViralCandidate.model_validate({"candidate_id": "c1", "start_time": 0, "end_time": 30, "viral_score": 77})
    assert final_score(c, []) == 77
