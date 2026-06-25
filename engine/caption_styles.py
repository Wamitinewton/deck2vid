"""
Caption style presets for ASS subtitle rendering.

Each style defines the visual appearance of karaoke captions —
font, colors, outline, shadow, and alignment. Styles follow the
ASS V4+ format specification.

Colour format: ``&HAABBGGRR`` (Alpha, Blue, Green, Red)
"""
from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class CaptionStyle(Enum):
    TIKTOK = "tiktok"      # Yellow highlight sweep, bold, bottom-center
    MINIMAL = "minimal"    # White on dark shadow, clean sans-serif
    BOLD = "bold"          # Large white text with heavy black outline
    PODCAST = "podcast"    # Centred, softer colours, talking-head friendly
    STATIC = "static"      # Legacy full-paragraph per slide (no karaoke)


class StyleSpec(NamedTuple):
    """All parameters that define a single ASS V4+ style line."""
    name: str
    fontname: str
    fontsize: int
    primary_colour: str     # Highlighted / spoken word colour
    secondary_colour: str   # Un-spoken word colour (karaoke fill source)
    outline_colour: str
    back_colour: str
    bold: int               # 0 or 1
    italic: int
    underline: int
    strikeout: int
    scale_x: int
    scale_y: int
    spacing: float
    angle: float
    border_style: int       # 1 = outline+shadow, 3 = opaque box
    outline: float
    shadow: float
    alignment: int          # 2 = bottom-centre, 8 = top-centre
    margin_l: int
    margin_r: int
    margin_v: int
    encoding: int

    def to_ass_line(self) -> str:
        """Format as a complete ASS ``Style:`` line."""
        return (
            f"Style: {self.name},{self.fontname},{self.fontsize},"
            f"{self.primary_colour},{self.secondary_colour},"
            f"{self.outline_colour},{self.back_colour},"
            f"{self.bold},{self.italic},{self.underline},{self.strikeout},"
            f"{self.scale_x},{self.scale_y},{self.spacing},{self.angle},"
            f"{self.border_style},{self.outline},{self.shadow},"
            f"{self.alignment},{self.margin_l},{self.margin_r},"
            f"{self.margin_v},{self.encoding}"
        )


# ---------------------------------------------------------------------------
# Style registry
# ---------------------------------------------------------------------------

STYLE_REGISTRY: dict[CaptionStyle, StyleSpec] = {
    # ── TikTok ────────────────────────────────────────────────────────────
    # Yellow sweep over white text, bold Montserrat, bottom-centre.
    CaptionStyle.TIKTOK: StyleSpec(
        name="Karaoke",
        fontname="Montserrat",
        fontsize=52,
        primary_colour="&H0000FFFF",     # Yellow (spoken)
        secondary_colour="&H00FFFFFF",   # White  (un-spoken)
        outline_colour="&H00000000",     # Black outline
        back_colour="&HB4000000",        # Semi-transparent shadow
        bold=1, italic=0, underline=0, strikeout=0,
        scale_x=100, scale_y=100, spacing=0, angle=0,
        border_style=1, outline=3, shadow=1.5,
        alignment=2, margin_l=30, margin_r=30, margin_v=60,
        encoding=1,
    ),

    # ── Minimal ───────────────────────────────────────────────────────────
    # Clean white sweep, subtle shadow, smaller font.
    CaptionStyle.MINIMAL: StyleSpec(
        name="Karaoke",
        fontname="Inter",
        fontsize=40,
        primary_colour="&H00FFFFFF",     # White (spoken)
        secondary_colour="&H80FFFFFF",   # Semi-transparent white (un-spoken)
        outline_colour="&H00000000",
        back_colour="&HA0000000",
        bold=0, italic=0, underline=0, strikeout=0,
        scale_x=100, scale_y=100, spacing=0, angle=0,
        border_style=1, outline=2, shadow=1,
        alignment=2, margin_l=40, margin_r=40, margin_v=50,
        encoding=1,
    ),

    # ── Bold ──────────────────────────────────────────────────────────────
    # Large, high-contrast white with heavy outline.
    CaptionStyle.BOLD: StyleSpec(
        name="Karaoke",
        fontname="Montserrat",
        fontsize=60,
        primary_colour="&H00FFFFFF",     # White (spoken)
        secondary_colour="&H6EFFFFFF",   # Faded white (un-spoken)
        outline_colour="&H00000000",
        back_colour="&HC8000000",
        bold=1, italic=0, underline=0, strikeout=0,
        scale_x=100, scale_y=100, spacing=0, angle=0,
        border_style=1, outline=4, shadow=2,
        alignment=2, margin_l=30, margin_r=30, margin_v=55,
        encoding=1,
    ),

    # ── Podcast ───────────────────────────────────────────────────────────
    # Softer colours, centred, suitable for talking-head content.
    CaptionStyle.PODCAST: StyleSpec(
        name="Karaoke",
        fontname="Inter",
        fontsize=44,
        primary_colour="&H00F0D080",     # Warm gold (spoken)
        secondary_colour="&H00FFFFFF",   # White (un-spoken)
        outline_colour="&H00000000",
        back_colour="&HA0000000",
        bold=0, italic=0, underline=0, strikeout=0,
        scale_x=100, scale_y=100, spacing=0.5, angle=0,
        border_style=1, outline=2.5, shadow=1,
        alignment=2, margin_l=50, margin_r=50, margin_v=65,
        encoding=1,
    ),
}


def get_style(style: CaptionStyle) -> StyleSpec:
    """Return the ``StyleSpec`` for the given preset, defaulting to TIKTOK."""
    return STYLE_REGISTRY.get(style, STYLE_REGISTRY[CaptionStyle.TIKTOK])
