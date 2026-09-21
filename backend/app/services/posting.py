"""Posting copy assembly. Campaign-required elements are appended verbatim and clearly flagged."""

from __future__ import annotations

from typing import Any

from ..schemas.ai import CampaignRules, HookVariants

PLATFORM_LABEL = {"tiktok": "TikTok", "instagram": "Instagram Reels", "youtube_shorts": "YouTube Shorts"}


def assemble_posting_copy(hv: HookVariants | None, rules: CampaignRules | None, platforms: list[str],
                          fallback_title: str, fallback_summary: str = "") -> dict[str, Any]:
    rules = rules or CampaignRules()
    req_tags = list(rules.required_hashtags)
    ai_tags = [t for t in (hv.hashtags if hv else []) if t.lower() not in {r.lower() for r in req_tags}]
    hashtags = req_tags + ai_tags[: max(0, 5 - len(req_tags))]
    title = (hv.youtube_title if hv and hv.youtube_title else fallback_title)[:100]
    copy = {
        "tiktok_caption": (hv.tiktok_caption if hv and hv.tiktok_caption else fallback_title).strip(),
        "instagram_caption": (hv.instagram_caption if hv and hv.instagram_caption else (fallback_summary or fallback_title)).strip(),
        "youtube_title": title.strip(),
        "youtube_description": (hv.youtube_description if hv and hv.youtube_description else fallback_summary).strip(),
        "hashtags": hashtags,
        "required": {
            "hashtags": req_tags,
            "mentions": list(rules.required_mentions),
            "cta": rules.required_cta,
            "links": list(rules.required_links),
        },
    }
    copy["full"] = {p: full_caption(p, copy) for p in (platforms or ["tiktok", "instagram", "youtube_shorts"])}
    return copy


def _tail(copy: dict[str, Any]) -> list[str]:
    req = copy["required"]
    parts = []
    if req.get("cta"):
        parts.append(req["cta"])
    if req.get("links"):
        parts.append(" ".join(req["links"]))
    if req.get("mentions"):
        parts.append(" ".join(req["mentions"]))
    if copy.get("hashtags"):
        parts.append(" ".join(copy["hashtags"]))
    return parts


def full_caption(platform: str, copy: dict[str, Any]) -> str:
    if platform == "youtube_shorts":
        body = [copy["youtube_title"], "", copy["youtube_description"]]
    elif platform == "instagram":
        body = [copy["instagram_caption"]]
    else:
        body = [copy["tiktok_caption"]]
    return "\n".join([*body, "", *_tail(copy)]).strip()


def posting_copy_text(copy: dict[str, Any], hook: str, alt_hooks: list[str], campaign: str) -> str:
    req = copy["required"]
    lines = [f"CAMPAIGN: {campaign or '(none)'}", f"HOOK OVERLAY: {hook}"]
    if alt_hooks:
        lines.append("ALTERNATIVE HOOKS: " + " | ".join(alt_hooks))
    lines += ["", "=== REQUIRED BY CAMPAIGN (verify before posting) ==="]
    any_req = False
    for label, value in (("Hashtags", " ".join(req["hashtags"])), ("Mentions", " ".join(req["mentions"])),
                         ("CTA", req["cta"] or ""), ("Links", " ".join(req["links"]))):
        if value:
            lines.append(f"[REQUIRED] {label}: {value}")
            any_req = True
    if not any_req:
        lines.append("(no required hashtags/mentions/CTA/links found in the campaign rules)")
    for p, text in copy["full"].items():
        lines += ["", f"=== {PLATFORM_LABEL.get(p, p)} ===", text]
    if copy.get("cover_text") or copy.get("thumbnail_prompts"):
        lines += ["", "=== COVER / THUMBNAIL ==="]
        if copy.get("cover_text"):
            lines.append(f"Cover text: {copy['cover_text']}")
        for t in copy.get("thumbnail_prompts") or []:
            lines += ["Image prompt (9:16):", t["prompt"], "With the clip's thumbnail.jpg as reference:", t["reference_prompt"]]
    return "\n".join(lines) + "\n"
