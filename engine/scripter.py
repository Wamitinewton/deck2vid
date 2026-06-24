from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from openai import OpenAI

from config import settings
from engine.extractor import SlideContent


class SlideScript(TypedDict):
    slide_number: int
    narration: str


_SYSTEM_PROMPT = """\
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


def _build_user_message(slides: list[SlideContent]) -> str:
    return f"Slide content:\n{json.dumps(slides, indent=2)}"


def _parse_response(raw: str) -> list[SlideScript]:
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


def generate_script(
    slides: list[SlideContent], output_dir: str | Path
) -> list[SlideScript]:
    client = OpenAI(
        base_url=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY,
    )

    response = client.chat.completions.create(
        model=settings.AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_message(slides)},
        ],
    )

    raw = response.choices[0].message.content or ""
    script = _parse_response(raw)
    _save_script(script, Path(output_dir))
    return script