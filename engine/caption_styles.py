from enum import Enum
from typing import NamedTuple, Tuple

class CaptionStyle(Enum):
    TIKTOK = "tiktok"      
    MINIMAL = "minimal"    
    BOLD = "bold"          

class StyleSpec(NamedTuple):
    fontname: str
    fontsize: int
    primary_color: Tuple[int, int, int]
    secondary_color: Tuple[int, int, int]
    outline_color: Tuple[int, int, int]
    outline_width: int
    bg_color: Tuple[int, int, int, int] # RGBA format
    margin_bottom: int

STYLE_REGISTRY: dict[CaptionStyle, StyleSpec] = {
    CaptionStyle.TIKTOK: StyleSpec(
        fontname="Montserrat-Bold", fontsize=46,
        primary_color=(255, 255, 0), secondary_color=(255, 255, 255),
        outline_color=(0, 0, 0), outline_width=3,
        bg_color=(0, 0, 0, 160), margin_bottom=100
    ),
    CaptionStyle.MINIMAL: StyleSpec(
        fontname="Inter-Regular", fontsize=40,
        primary_color=(255, 255, 255), secondary_color=(200, 200, 200),
        outline_color=(0, 0, 0), outline_width=2,
        bg_color=(0, 0, 0, 120), margin_bottom=80
    ),
    CaptionStyle.BOLD: StyleSpec(
        fontname="Montserrat-Black", fontsize=52,
        primary_color=(255, 255, 255), secondary_color=(180, 180, 180),
        outline_color=(0, 0, 0), outline_width=4,
        bg_color=(0, 0, 0, 200), margin_bottom=100
    ),
}

def get_style(style: CaptionStyle) -> StyleSpec:
    return STYLE_REGISTRY.get(style, STYLE_REGISTRY[CaptionStyle.TIKTOK])