"""OpenAI services for the POV video generator.

Handles three OpenAI API calls:
  1. GPT-4o  – generates the scene-by-scene script
  2. TTS-1   – converts each scene narration to MP3
  3. DALL-E 3 – generates a 9:16 image for each scene
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

VALID_STYLES = ("cinematic", "vlog", "documentary")
VALID_VOICES = ("onyx", "alloy", "echo", "nova", "shimmer", "fable")


@dataclass
class Scene:
    index: int
    narration: str          # spoken text (fed to TTS)
    image_prompt: str       # DALL-E 3 prompt
    duration_s: float = 0.0 # filled in after TTS audio is probed


@dataclass
class POVScript:
    title: str
    topic: str
    style: str
    scenes: list[Scene] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Script generation  (GPT-4o)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a viral short-form video director who specialises in immersive POV storytelling.
Create a tightly paced, emotionally engaging script for a vertical (9:16) video.

Output ONLY a single valid JSON object with this exact schema:
{
  "title": "<catchy video title>",
  "scenes": [
    {
      "narration": "<spoken voiceover text for this scene (1-3 sentences)>",
      "image_prompt": "<vivid DALL-E prompt describing the scene visually in POV style, include lighting, mood, camera angle>"
    }
  ]
}

Rules:
- 4-8 scenes per video
- Each narration chunk should take roughly (total_duration / num_scenes) seconds to read aloud
- image_prompt must be in FIRST-PERSON POV camera angle (e.g. "First-person POV of hands ...")
- Keep total narration word-count proportional to the requested duration (~130 words/min)
- Do NOT include any markdown, code fences, or prose outside the JSON
"""


async def generate_script(
    topic: str,
    style: str,
    duration_s: int,
    api_key: str,
) -> POVScript:
    """Call GPT-4o to produce a scene-by-scene script."""
    import openai  # imported lazily

    client = openai.AsyncOpenAI(api_key=api_key)
    user_msg = (
        f"Topic: {topic}\n"
        f"Style: {style}\n"
        f"Target duration: {duration_s} seconds\n"
        "Generate the POV video script now."
    )
    log.info("POV: requesting script from GPT-4o (topic=%r, style=%r, duration=%ds)", topic, style, duration_s)
    response = await client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.85,
        max_tokens=1800,
    )
    raw = (response.choices[0].message.content or "{}").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()
    data = json.loads(raw)
    scenes = [
        Scene(index=i, narration=s["narration"], image_prompt=s["image_prompt"])
        for i, s in enumerate(data.get("scenes", []))
    ]
    script = POVScript(title=data.get("title", topic), topic=topic, style=style, scenes=scenes)
    log.info("POV: script generated — %d scenes, title=%r", len(scenes), script.title)
    return script


# ---------------------------------------------------------------------------
# Narration  (OpenAI TTS-1)
# ---------------------------------------------------------------------------

async def generate_narration(
    text: str,
    scene_idx: int,
    out_dir: Path,
    api_key: str,
    voice: str = "onyx",
) -> Path:
    """Generate a TTS MP3 for one scene and save it to *out_dir*."""
    import openai

    client = openai.AsyncOpenAI(api_key=api_key)
    out_path = out_dir / f"scene_{scene_idx}.mp3"
    log.info("POV: TTS scene %d (voice=%s, %d chars)", scene_idx, voice, len(text))
    response = await client.audio.speech.create(
        model="tts-1",
        voice=voice,  # type: ignore[arg-type]
        input=text,
        response_format="mp3",
    )
    out_path.write_bytes(response.content)
    log.info("POV: TTS scene %d saved -> %s (%d bytes)", scene_idx, out_path.name, out_path.stat().st_size)
    return out_path


# ---------------------------------------------------------------------------
# Image generation  (GPT Image / DALL-E)
# ---------------------------------------------------------------------------

async def generate_image(
    prompt: str,
    scene_idx: int,
    out_dir: Path,
    api_key: str,
) -> Path:
    """Generate a vertical scene image and save it to *out_dir*.

    Supports modern OpenAI image models (gpt-image-1, gpt-image-1-mini) which return
    base64-encoded images and reject the legacy `response_format` parameter, with
    graceful fallback to DALL-E 3.
    """
    import base64
    import httpx
    import openai

    client = openai.AsyncOpenAI(api_key=api_key)
    out_path = out_dir / f"scene_{scene_idx}.png"

    # Candidate models to try in order.
    # gpt-image models support 1024x1536 (2:3 vertical), do not accept `response_format`,
    # and return b64_json.
    # dall-e-3 supports 1024x1792 (9:16) and returns url by default.
    models_to_try = [
        {"model": "gpt-image-1", "size": "1024x1536"},
        {"model": "gpt-image-1-mini", "size": "1024x1536"},
        {"model": "dall-e-3", "size": "1024x1792"},
    ]

    last_exc: Exception | None = None
    for cfg in models_to_try:
        model_name = cfg["model"]
        size = cfg["size"]
        log.info("POV: requesting image for scene %d using %s (%s)", scene_idx, model_name, size)
        try:
            kwargs: dict[str, Any] = {
                "model": model_name,
                "prompt": prompt,
                "n": 1,
                "size": size,
            }
            if not model_name.startswith("gpt-image"):
                kwargs["quality"] = "standard"

            # Notice: response_format is intentionally omitted because gpt-image models
            # raise a 400 'Unknown parameter' error if passed.
            response = await client.images.generate(**kwargs)
            item = response.data[0]

            if getattr(item, "b64_json", None):
                img_bytes = base64.b64decode(item.b64_json)
                out_path.write_bytes(img_bytes)
            elif getattr(item, "url", None):
                async with httpx.AsyncClient(timeout=60) as http:
                    r = await http.get(item.url)
                    r.raise_for_status()
                    out_path.write_bytes(r.content)
            else:
                raise RuntimeError(f"No image data returned for scene {scene_idx}")

            log.info(
                "POV: image scene %d saved -> %s (%d bytes, model=%s)",
                scene_idx,
                out_path.name,
                out_path.stat().st_size,
                model_name,
            )
            return out_path
        except Exception as exc:
            last_exc = exc
            log.warning("POV: failed to generate scene %d with %s: %s", scene_idx, model_name, exc)
            continue

    raise RuntimeError(
        f"Failed to generate image for scene {scene_idx}: {last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Concurrent scene generation
# ---------------------------------------------------------------------------

async def generate_all_scenes(
    script: POVScript,
    out_dir: Path,
    api_key: str,
    voice: str = "onyx",
    on_progress: Callable[[str, float], None] | None = None,
) -> None:
    """Generate TTS audio and DALL-E images for all scenes concurrently."""
    total = len(script.scenes)

    async def _process_scene(scene: Scene) -> None:
        tts_task = asyncio.create_task(
            generate_narration(scene.narration, scene.index, out_dir, api_key, voice)
        )
        img_task = asyncio.create_task(
            generate_image(scene.image_prompt, scene.index, out_dir, api_key)
        )
        await asyncio.gather(tts_task, img_task)
        if on_progress:
            on_progress("scenes", (scene.index + 1) / total)

    await asyncio.gather(*[_process_scene(s) for s in script.scenes])
