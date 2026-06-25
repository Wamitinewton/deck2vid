"""
Font availability probing and fallback resolution.

Uses ``fc-list`` (fontconfig) to check whether a font family is installed.
If the preferred font is missing, returns the first available font from a
prioritised fallback chain.
"""
from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

# Ordered fallback chain used when the requested font is not installed.
_FALLBACK_CHAIN = [
    "Montserrat",
    "Arial",
    "Liberation Sans",
    "DejaVu Sans",
    "Noto Sans",
    "sans-serif",
]


def _is_font_available(family: str) -> bool:
    """Check if *family* is known to fontconfig on this system."""
    try:
        result = subprocess.run(
            ["fc-list", f":family={family}", "family"],
            capture_output=True, text=True, timeout=5,
        )
        # fc-list returns matching lines; empty output = not found.
        return bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # fc-list not installed or timed out — assume unavailable.
        return False


def resolve_font(preferred: str) -> str:
    """Return *preferred* if installed, otherwise the first available fallback.

    Always returns a value — worst case ``"sans-serif"`` which fontconfig
    will resolve at render time.
    """
    if _is_font_available(preferred):
        return preferred

    logger.warning(
        "Font '%s' not found; searching fallback chain.", preferred,
    )

    for candidate in _FALLBACK_CHAIN:
        if candidate == preferred:
            continue
        if candidate == "sans-serif" or _is_font_available(candidate):
            logger.info("Using fallback font: %s", candidate)
            return candidate

    return "sans-serif"


def ensure_fonts(*families: str) -> dict[str, str]:
    """Probe and resolve multiple font families at once.

    Returns a mapping of ``{requested: resolved}`` so callers can see
    which fonts were substituted.
    """
    return {family: resolve_font(family) for family in families}
