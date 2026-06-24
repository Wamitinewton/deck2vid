from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import TypedDict

from openai import OpenAI

from config import settings
from engine.extractor import SlideContent

logger = logging.getLogger(__name__)


class SlideScript(TypedDict):
    slide_number: int
    narration: str


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_TEXT_SYSTEM_PROMPT = """\
You are a professional presenter delivering a slide deck to an audience.
Generate a natural, engaging spoken narration script for each slide.

Rules:
- Write as if you are SPEAKING, not reading bullet points
- Transitions between slides must feel natural — never say "Slide 2" or "Next slide"
- Each slide narration should be 20–40 seconds when spoken at a normal pace
- Use connecting phrases like "Building on that...", "What this means is...", "Here's where it gets interesting..."
- The tone should be confident but conversational
- Do NOT start every slide with "In this slide..."
- Output ONLY a raw JSON array — no markdown fences, no keys wrapping the array, no explanation

Output format:
[
  {
    "slide_number": 1,
    "narration": "..."
  }
]
"""

# Used for slides that contain only images, screenshots, or diagrams.
# Instructs the model to describe the visual content in the same presenter voice.
_VISION_SYSTEM_PROMPT = """\
You are a professional presenter delivering a pitch deck to investors.
The current slide contains only images, screenshots, or diagrams — no text was extracted from it.

Look at the slide carefully and narrate it naturally, as if you are walking the audience through it.
Rules:
- Speak confidently and directly — do not say "this slide shows" or "I can see"
- Match the tone of the rest of the presentation: confident, clear, and engaging
- Keep the narration to 20–40 seconds of spoken word
- Output ONLY the narration text, no JSON, no formatting
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_text_user_message(slides: list[SlideContent]) -> str:
    return f"Slide content:\n{json.dumps(slides, indent=2)}"


def _parse_text_response(raw: str) -> list[SlideScript]:
    """Parse the GPT JSON array response, handling common formatting deviations."""
    cleaned = raw.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()

    parsed = json.loads(cleaned)

    if isinstance(parsed, dict):
        for value in parsed.values():
            if isinstance(value, list):
                return value
        raise ValueError(f"Unexpected dict structure from model: {list(parsed.keys())}")

    if isinstance(parsed, list):
        return parsed

    raise ValueError(f"Unexpected response type: {type(parsed)}")


def _save_script(script: list[SlideScript], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "script.json"
    json_path.write_text(json.dumps(script, indent=2, ensure_ascii=False))

    txt_path = output_dir / "script.txt"
    lines = [
        f"=== Slide {entry['slide_number']} ===\n{entry['narration']}\n"
        for entry in script
    ]
    txt_path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Narration paths
# ---------------------------------------------------------------------------

def _narrate_text_slides(
    slides: list[SlideContent],
    client: OpenAI,
) -> list[SlideScript]:
    """Send all text-bearing slides to GPT-4.1 in a single batched call."""
    if not slides:
        return []

    response = client.chat.completions.create(
        model=settings.AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _TEXT_SYSTEM_PROMPT},
            {"role": "user", "content": _build_text_user_message(slides)},
        ],
    )

    raw = response.choices[0].message.content or ""
    return _parse_text_response(raw)


def _narrate_visual_slides(
    slides: list[SlideContent],
    image_paths: dict[int, Path],
    client: OpenAI,
) -> list[SlideScript]:
    """
    Narrate image-only slides using GPT-4.1 vision.

    Each slide is sent as a separate API call with its rendered PNG base64-encoded
    in the message payload. Calls are sequential to keep memory usage predictable
    and avoid hitting the 10-image-per-request API limit for decks with many visuals.
    """
    if not slides:
        return []

    scripts: list[SlideScript] = []

    for slide in slides:
        slide_num = slide["slide_number"]
        png_path = image_paths.get(slide_num)

        if png_path is None or not png_path.exists():
            # Fallback narration when the rendered PNG is unavailable (e.g. hidden slide).
            logger.warning("No PNG found for visual-only slide %d — using fallback narration.", slide_num)
            scripts.append(SlideScript(slide_number=slide_num, narration="Let's take a moment to look at this visual."))
            continue

        logger.info("Generating vision narration for slide %d (%s).", slide_num, png_path.name)

        b64_image = base64.b64encode(png_path.read_bytes()).decode()

        response = client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64_image}"},
                        },
                        {
                            "type": "text",
                            "text": "Narrate this slide for the presentation.",
                        },
                    ],
                },
            ],
        )

        narration = (response.choices[0].message.content or "").strip()
        scripts.append(SlideScript(slide_number=slide_num, narration=narration))

    return scripts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_script(
    slides: list[SlideContent],
    output_dir: str | Path,
    image_paths: dict[int, Path] | None = None,
) -> list[SlideScript]:
    """
    Generate narration for all slides and return them ordered by slide number.

    Text-bearing slides are handled in a single batched GPT-4.1 call.
    Visual-only slides (no extractable text) are narrated individually via
    GPT-4.1 vision using their rendered PNG images.

    Args:
        slides:      Extracted slide content from the PPTX.
        output_dir:  Directory where script.json and script.txt are written.
        image_paths: Mapping of slide_number → rendered PNG path, required for
                     vision narration of image-only slides.
    """
    client = OpenAI(
        base_url=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY,
    )

    text_slides   = [s for s in slides if not s["is_visual_only"]]
    visual_slides = [s for s in slides if s["is_visual_only"]]

    logger.info(
        "Scripting %d text slide(s) and %d visual-only slide(s).",
        len(text_slides),
        len(visual_slides),
    )

    text_scripts   = _narrate_text_slides(text_slides, client)
    vision_scripts = _narrate_visual_slides(visual_slides, image_paths or {}, client)

    # Merge both batches and restore slide order before saving.
    all_scripts = sorted(text_scripts + vision_scripts, key=lambda e: e["slide_number"])
    _save_script(all_scripts, Path(output_dir))
    return all_scripts