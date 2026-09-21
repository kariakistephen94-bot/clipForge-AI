"""Campaign compliance checks: deterministic local rules + optional AI judgment."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..candidates.dedupe import content_tokens
from ..schemas.ai import CampaignRules, ComplianceCheck, ComplianceReport

GENERIC_RULE_WORDS = {"content", "allowed", "clips", "clip", "videos", "video", "posts", "post", "please", "must",
                      "cannot", "should", "anything", "any", "don't", "dont", "prohibited", "including", "include",
                      "use", "using", "make", "made", "creator", "creators", "campaign", "account", "accounts"}


@dataclass
class ClipFacts:
    duration: float
    platforms: list[str] = field(default_factory=list)
    captions_on: bool = True
    split_used: bool = False
    broll_used: bool = False
    music_used: bool = False
    sfx_used: bool = False
    posting_text: str | None = None  # None = copy not generated yet
    transcript_text: str = ""
    hook_text: str = ""
    ai_status: str | None = None
    ai_reasons: list[str] = field(default_factory=list)


def _fmt(v: float | None) -> str:
    return "?" if v is None else f"{v:g}s"


def evaluate_compliance(rules: CampaignRules | None, facts: ClipFacts, has_rules_text: bool = True) -> ComplianceReport:
    checks: list[ComplianceCheck] = []
    manual: list[str] = []

    if rules is None or not has_rules_text:
        checks.append(ComplianceCheck(rule="Campaign rules", status="WARNING",
                                      detail="No campaign rules were supplied, so nothing could be verified."))
        return ComplianceReport(status="WARNING", checks=checks, manual_checks=manual)

    # Duration
    lo, hi = rules.min_duration, rules.max_duration
    if lo is None and hi is None:
        manual.append("Duration limits are not stated in the campaign rules.")
    else:
        tol = 0.25
        if (lo is not None and facts.duration < lo - tol) or (hi is not None and facts.duration > hi + tol):
            checks.append(ComplianceCheck(rule="Duration", status="FAILED",
                                          detail=f"{facts.duration:.1f}s is outside {_fmt(lo)}-{_fmt(hi)}"))
        else:
            checks.append(ComplianceCheck(rule="Duration", status="COMPLIANT",
                                          detail=f"{facts.duration:.1f}s within {_fmt(lo)}-{_fmt(hi)}"))

    # Platforms
    if rules.platforms and facts.platforms:
        extra = [p for p in facts.platforms if p not in rules.platforms]
        if extra:
            checks.append(ComplianceCheck(rule="Platforms", status="WARNING",
                                          detail=f"Campaign lists {', '.join(rules.platforms)}; you also target {', '.join(extra)}"))
        else:
            checks.append(ComplianceCheck(rule="Platforms", status="COMPLIANT", detail=", ".join(facts.platforms)))

    # Captions
    if facts.captions_on and rules.captions_allowed is False:
        checks.append(ComplianceCheck(rule="Captions", status="FAILED", detail="Campaign does not allow captions"))
    elif not facts.captions_on and rules.captions_required:
        checks.append(ComplianceCheck(rule="Captions", status="FAILED", detail="Campaign requires captions"))
    elif rules.captions_allowed is not None or rules.captions_required is not None:
        checks.append(ComplianceCheck(rule="Captions", status="COMPLIANT",
                                      detail="Captions on" if facts.captions_on else "Captions off"))

    # Toggles that can make a clip non-compliant
    for used, allowed, label in (
        (facts.split_used, rules.split_screen_allowed, "Split screen"),
        (facts.broll_used, rules.broll_allowed, "B-roll"),
        (facts.music_used, rules.music_allowed, "Background music"),
    ):
        if not used:
            continue
        if allowed is False:
            checks.append(ComplianceCheck(rule=label, status="FAILED", detail=f"{label} used but the campaign prohibits it"))
        elif allowed is None:
            checks.append(ComplianceCheck(rule=label, status="WARNING", detail=f"{label} used; the rules don't say whether it's allowed"))
        else:
            checks.append(ComplianceCheck(rule=label, status="COMPLIANT", detail=f"{label} allowed"))

    if facts.sfx_used and rules.music_allowed is False:
        checks.append(ComplianceCheck(rule="Sound effects", status="WARNING",
                                      detail="Sound effects were added and the campaign prohibits music; confirm added "
                                             "sound effects are acceptable (or render with sound design off)"))

    # Required post elements
    required = [("Hashtag", h) for h in rules.required_hashtags] + [("Mention", m) for m in rules.required_mentions]
    if rules.required_cta:
        required.append(("CTA", rules.required_cta))
    required += [("Link", link) for link in rules.required_links]
    for kind, value in required:
        if facts.posting_text is None:
            # Candidate stage: posting copy doesn't exist yet; it's generated (with these included) at render time.
            manual.append(f"Posting copy must include required {kind.lower()} {value} (added automatically at render).")
        elif value.lower() in facts.posting_text.lower():
            checks.append(ComplianceCheck(rule=f"Required {kind.lower()}", status="COMPLIANT", detail=f"{value} is in the posting copy"))
        else:
            checks.append(ComplianceCheck(rule=f"Required {kind.lower()}", status="FAILED", detail=f"{value} missing from posting copy"))

    # Prohibited content: conservative keyword signal only; AI does the judgment.
    haystack = content_tokens(f"{facts.transcript_text} {facts.hook_text}")
    for rule in rules.prohibited_content:
        terms = {t for t in content_tokens(rule) if t not in GENERIC_RULE_WORDS and len(t) > 3}
        hits = terms & haystack
        if terms and len(hits) >= min(2, len(terms)):
            checks.append(ComplianceCheck(rule="Prohibited content", status="WARNING",
                                          detail=f"May relate to '{rule}' (mentions: {', '.join(sorted(hits))})"))

    # AI judgment
    if facts.ai_status in ("COMPLIANT", "WARNING", "FAILED"):
        checks.append(ComplianceCheck(rule="AI content review", status=facts.ai_status,  # type: ignore[arg-type]
                                      detail="; ".join(facts.ai_reasons) or "No issues found", source="ai"))
    elif rules.prohibited_content or rules.source_modification_rules:
        manual.append("Judgment-based rules were not AI-reviewed; check prohibited content manually.")

    manual += [f"Source modification: {r}" for r in rules.source_modification_rules]
    manual += [f"Special requirement: {r}" for r in rules.special_requirements]

    statuses = {c.status for c in checks}
    status = "FAILED" if "FAILED" in statuses else "WARNING" if "WARNING" in statuses else "COMPLIANT"
    return ComplianceReport(status=status, checks=checks, manual_checks=manual)
