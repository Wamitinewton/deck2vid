from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from pptx import Presentation
from pptx.shapes.base import BaseShape


class SlideContent(TypedDict):
    slide_number: int
    title: str
    body_text: list[str]
    notes: str


def _extract_text_from_shape(shape: BaseShape) -> list[str]:
    texts: list[str] = []

    if shape.shape_type == 6:
        for s in shape.shapes:
            texts.extend(_extract_text_from_shape(s))
        return texts

    if not shape.has_text_frame:
        return texts

    for paragraph in shape.text_frame.paragraphs:
        line = paragraph.text.strip()
        if line:
            texts.append(line)

    return texts


def _extract_title(slide) -> str:
    if slide.shapes.title and slide.shapes.title.has_text_frame:
        return slide.shapes.title.text.strip()
    return ""


def _extract_notes(slide) -> str:
    try:
        notes_frame = slide.notes_slide.notes_text_frame
        return notes_frame.text.strip()
    except Exception:
        return ""


def extract_slides(pptx_path: str | Path) -> list[SlideContent]:
    prs = Presentation(str(pptx_path))
    slides: list[SlideContent] = []

    for index, slide in enumerate(prs.slides, start=1):
        title = _extract_title(slide)
        body_text: list[str] = []

        for shape in slide.shapes:
            if shape == slide.shapes.title:
                continue
            body_text.extend(_extract_text_from_shape(shape))

        notes = _extract_notes(slide)

        slides.append(
            SlideContent(
                slide_number=index,
                title=title,
                body_text=body_text,
                notes=notes,
            )
        )

    return slides