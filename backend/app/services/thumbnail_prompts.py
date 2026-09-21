"""Turn a structured thumbnail concept into ready-to-paste image-generation prompts.

Three flavours per concept:
* ``prompt``            -- a full text-to-image prompt (Imagen / GPT-image / Flux / Ideogram style prose)
* ``reference_prompt``  -- for models that take a reference image (Nano Banana, GPT-image edit, Flux Kontext):
                           keeps the REAL speaker from ``thumbnail_frame_X.jpg`` -- the honest and most accurate option
* ``midjourney``        -- the same idea with Midjourney parameters
plus a negative prompt and a CTR checklist. Everything is plain text, no API calls.
"""

from __future__ import annotations

from typing import Any

from ..schemas.ai import ThumbnailConcept

NEGATIVE = ("blurry, low contrast, cluttered background, multiple focal points, small or unreadable text, extra words, "
            "misspelled text, watermark, logos, borders, distorted face, extra fingers, deformed hands, plastic skin, "
            "uncanny eyes, jpeg artifacts, low resolution, text in the bottom-right corner")

CHECKLIST = [
    "Readable at phone size (~160 px wide): one focal point, face and text instantly clear.",
    "Text is 2-4 words and adds to the title instead of repeating it.",
    "The expression and objects are true to the video - curiosity, not deception.",
    "Strong contrast between subject and background; the subject is separated by light or colour.",
    "Bottom-right corner kept clear (YouTube shows the timestamp there).",
    "Test 2-3 variants with YouTube's Test & Compare and keep the winner.",
]

FORMATS = {
    "16:9": {"label": "YouTube thumbnail", "size": "1280x720", "mj": "--ar 16:9",
             "clear": "keep the bottom-right corner clear"},  # YouTube's timestamp badge
    "9:16": {"label": "vertical Shorts / Reels cover", "size": "1080x1920", "mj": "--ar 9:16",
             "clear": "keep the bottom 20% and right edge clear"},  # captions, buttons and profile UI
}


def _c(s: str | None) -> str:
    """Model fields often end with a period; strip it so pieces can be joined into one sentence."""
    return (s or "").strip().rstrip(".;, ").strip()


def _join(parts: list[str], sep: str = ", ") -> str:
    return sep.join(_c(p) for p in parts if p and _c(p))


def build_prompts(c: ThumbnailConcept, *, aspect: str = "16:9", title: str = "", reference_file: str = "",
                  speakers: str = "") -> dict[str, Any]:
    fmt = FORMATS.get(aspect, FORMATS["16:9"])
    c = c.model_copy(update={k: _c(getattr(c, k)) for k in ("subject", "scene", "emotion", "composition", "lighting",
                                                            "color_palette", "text_style")})
    subject = c.subject or (f"{speakers}, close-up" if speakers else "the speaker from the video, close-up")
    text = c.text_overlay.strip()
    text_part = (f'large bold text "{text}"' + (f" ({c.text_style})" if c.text_style else
                                                         ", heavy white sans-serif with a thick black outline")
                 if text else "no text")
    elements = _join(c.visual_elements, " and ")
    scene = _join([
        f"{fmt['label']}, {aspect}",
        f"{subject}" + (f", {c.emotion} expression" if c.emotion and c.emotion.lower() not in subject.lower() else ""),
        elements and f"featuring {elements}",
        c.scene and f"background: {c.scene}",
        c.composition and f"composition: {c.composition}",
        c.lighting and f"lighting: {c.lighting}",
        c.color_palette and f"colour palette: {c.color_palette}",
        text_part,
    ])
    style = ("ultra-sharp focus on the eyes, high contrast, vivid but natural colour, shallow depth of field, "
             "professional photography, clean and uncluttered, instantly readable at small size")
    prompt = f"{scene}. {style}."
    ref = reference_file or "the attached frame"
    reference_prompt = (
        f"Using {ref} as the reference, keep the person's face, identity, hairstyle and clothing exactly as they are "
        f"(do not beautify or change them). Turn it into a {fmt['label']} ({aspect}, {fmt['size']}): "
        + _join([
            c.emotion and f"emphasise the real {c.emotion} expression",
            "crop so the face fills roughly 40% of the frame",
            c.scene and f"replace the background with {c.scene}, slightly blurred",
            elements and f"add {elements}",
            c.lighting and f"relight: {c.lighting}",
            c.color_palette and f"grade to {c.color_palette}",
            text and f'add the text "{text}"' + (f" ({c.text_style})" if c.text_style else ""),
            fmt["clear"],
        ]) + "."
    )
    mj = f"{scene}, {style} {fmt['mj']} --style raw --no text artifacts, watermark"
    return {
        "concept": c.concept, "emotion": c.emotion, "text_overlay": text, "aspect": aspect, "size": fmt["size"],
        "title": title, "frame_timestamp": c.frame_timestamp, "why_it_works": c.why_it_works,
        "prompt": prompt, "reference_prompt": reference_prompt, "midjourney": mj, "negative_prompt": NEGATIVE,
        "checklist": CHECKLIST,
    }


def prompts_text(title: str, items: list[dict[str, Any]], alt_titles: list[str] | None = None) -> str:
    """Human-readable thumbnail_prompts.txt."""
    lines = [f"TITLE: {title}"]
    if alt_titles:
        lines.append("ALTERNATIVE TITLES: " + " | ".join(alt_titles))
    for n, it in enumerate(items):
        tag = chr(ord("A") + n)
        lines += ["", f"=== THUMBNAIL {tag}: {it['concept'] or 'concept'} ({it['aspect']}, {it['size']}) ===",
                  f"Emotion: {it['emotion']}   Text: {it['text_overlay'] or '(none)'}",
                  f"Why: {it['why_it_works']}", "",
                  "PROMPT (text-to-image):", it["prompt"], "",
                  f"PROMPT WITH REFERENCE IMAGE (attach {it.get('reference_file') or 'the frame'}; keeps the real speaker):",
                  it["reference_prompt"], "", "MIDJOURNEY:", it["midjourney"], "",
                  "NEGATIVE PROMPT:", it["negative_prompt"]]
    lines += ["", "=== CTR CHECKLIST ==="] + [f"- {c}" for c in CHECKLIST]
    return "\n".join(lines) + "\n"
