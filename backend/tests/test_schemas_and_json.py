import json

import pytest
from pydantic import ValidationError

from app.ai.jsonrepair import JSONRepairError, gemini_json_schema, loads_lenient, validate_output
from app.schemas.ai import ViralCandidate, ViralCandidateList


def cand(**kw):
    base = {
        "candidate_id": "c01", "start_time": "00:14:31", "end_time": 909.0, "topic": "t",
        "subscores": {"hook": 25, "clarity": 14, "curiosity": -2, "emotion": 8, "specificity": 9,
                      "shareability": 9, "retention": 9, "visual": 5, "campaign_fit": 4},
        "penalties": [{"reason": "slow intro", "points": -3}],
        "campaign_compliance": {"status": "pass", "reasons": "ok"},
    }
    base.update(kw)
    return base


def test_candidate_validation_clamps_and_parses():
    c = ViralCandidate.model_validate(cand())
    assert c.start_time == 871.0 and c.duration == 38.0
    assert c.subscores.hook == 20  # clamped to max
    assert c.subscores.curiosity == 0
    assert c.penalties[0].points == 3
    assert c.campaign_compliance.status == "COMPLIANT"
    assert c.computed_score() == pytest.approx(20 + 14 + 0 + 8 + 9 + 9 + 9 + 5 + 4 - 3)


def test_candidate_rejects_inverted_times():
    with pytest.raises(ValidationError):
        ViralCandidate.model_validate(cand(start_time=100, end_time=90))


def test_candidate_list_drops_only_bad_items():
    lst = ViralCandidateList.model_validate({"candidates": [cand(), cand(candidate_id="c02", start_time=5, end_time=1), cand(candidate_id="c03")]})
    assert [c.candidate_id for c in lst.candidates] == ["c01", "c03"]


def test_candidate_list_all_bad_raises():
    with pytest.raises(ValidationError):
        ViralCandidateList.model_validate({"candidates": [cand(start_time=5, end_time=1)]})


@pytest.mark.parametrize("raw", [
    '```json\n{"a": 1, "b": [1,2,],}\n```',
    'Sure! Here is the JSON:\n{"a": 1, "b": [1, 2]}\nHope that helps.',
    "{'a': 1}".replace("'", '"'),
    '{"a": 1, "b": [1, 2',  # truncated
    '{"a": True, "b": None}',
])
def test_loads_lenient_repairs(raw):
    data = loads_lenient(raw)
    assert data["a"] in (1, True)


def test_loads_lenient_gives_up():
    with pytest.raises(JSONRepairError):
        loads_lenient("no json here at all")


def test_validate_output_returns_error_text():
    obj, err = validate_output(ViralCandidateList, json.dumps({"candidates": [{"candidate_id": "x"}]}))
    assert obj is None and err


def test_gemini_schema_inlines_refs():
    schema = gemini_json_schema(ViralCandidateList)
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    assert "candidates" in schema["properties"]
