"""Gemini provider recovery without network: malformed JSON, rate limits, bad keys."""

import json
from types import SimpleNamespace

import pytest
from google.genai import errors

from app.ai.gemini import GeminiProvider
from app.ai.provider import AIProviderError
from app.schemas.ai import CampaignRules

GOOD = json.dumps({"min_duration": 15, "max_duration": 60, "platforms": ["tiktok"]})


class FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        usage = SimpleNamespace(prompt_token_count=100, candidates_token_count=20)
        return SimpleNamespace(text=item, usage_metadata=usage, candidates=[])


class Usage:
    def __init__(self):
        self.requests = 0

    def request(self, input_tokens=0, output_tokens=0):
        self.requests += 1

    def upload(self):
        pass


def provider(script):
    usage = Usage()
    p = GeminiProvider(api_key="test-key", model="gemini-test", usage=usage, sleep=lambda s: None)
    p._client = SimpleNamespace(models=FakeModels(script))
    return p, usage


def test_malformed_json_is_repaired_by_retry():
    p, usage = provider(["```json\n{this is not json\n```", GOOD])
    rules = p.parse_campaign_rules("Clips 15-60 seconds on TikTok")
    assert isinstance(rules, CampaignRules) and rules.max_duration == 60
    assert usage.requests == 2
    repair_prompt = p.client.models.calls[1]["contents"][0]
    assert "not valid JSON" in repair_prompt


def test_lenient_json_needs_no_retry():
    p, usage = provider(["Here you go: " + GOOD + " cheers"])
    assert p.parse_campaign_rules("x").min_duration == 15
    assert usage.requests == 1


def test_gives_up_after_max_repairs():
    p, _ = provider(["nope", "still nope", "never"])
    with pytest.raises(AIProviderError, match="invalid structured data"):
        p.parse_campaign_rules("x")


def test_rate_limit_then_success():
    p, usage = provider([errors.APIError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}), GOOD])
    assert p.parse_campaign_rules("x").platforms == ["tiktok"]
    assert usage.requests == 1


def test_bad_key_is_not_retried():
    p, _ = provider([errors.APIError(403, {"error": {"message": "denied", "status": "PERMISSION_DENIED"}}), GOOD])
    with pytest.raises(AIProviderError, match="API key"):
        p.parse_campaign_rules("x")


def test_timeout_retried_and_reported():
    class ReadTimeout(Exception):
        pass

    p, _ = provider([ReadTimeout("t1"), ReadTimeout("t2"), ReadTimeout("t3"), ReadTimeout("t4")])
    with pytest.raises(AIProviderError, match="timed out"):
        p.parse_campaign_rules("x")


def test_missing_key_rejected():
    with pytest.raises(AIProviderError):
        GeminiProvider(api_key="", model="m")
