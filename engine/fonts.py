import logging
from PIL import ImageFont

logger = logging.getLogger(__name__)

def resolve_pil_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    """Attempts to load a font across OS environments, falling back gracefully."""
    candidates = [
        f"{font_name}.ttf", 
        "arial.ttf", 
        "DejaVuSans-Bold.ttf", 
        "LiberationSans-Bold.ttf"
    ]
    
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
            
    logger.warning("Could not load requested font or OS fallbacks. Using PIL default.")
    return ImageFont.load_default()