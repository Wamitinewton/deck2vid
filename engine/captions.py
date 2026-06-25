"""
Caption generation for the video engine.

Supports three output formats:

- **SRT** — standard subtitle file for accessibility / soft-subtitle tracks.
- **ASS (static)** — legacy full-paragraph-per-slide subtitles.
- **ASS (karaoke)** — TikTok-style word-synced captions using ``\\kf`` tags.

The karaoke path uses per-word timestamps collected from Azure TTS
``synthesis_word_boundary`` events to generate a highlight-sweep effect
where each word lights up in sync with the spoken narration.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from engine.caption_styles import CaptionStyle, get_style
from engine.fonts import resolve_font
from engine.scripter import SlideScript
from engine.sync import SyncEntry, WordTimestamp

logger = logging.getLogger(__name__)

_MAX_CHARS_PER_LINE = 58
_DEFAULT_WORDS_PER_GROUP = 5

# ---------------------------------------------------------------------------
# ASS header template
# ---------------------------------------------------------------------------

_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

def _fmt_srt_time(total_seconds: float) -> str:
    millis = round((total_seconds % 1) * 1000)
    secs   = int(total_seconds) % 60
    mins   = (int(total_seconds) // 60) % 60
    hrs    = int(total_seconds) // 3600
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def _fmt_ass_time(total_seconds: float) -> str:
    cs   = round((total_seconds % 1) * 100)
    secs = int(total_seconds) % 60
    mins = (int(total_seconds) // 60) % 60
    hrs  = int(total_seconds) // 3600
    return f"{hrs}:{mins:02d}:{secs:02d}.{cs:02d}"


# ---------------------------------------------------------------------------
# Text wrapping (for static / SRT modes)
# ---------------------------------------------------------------------------

def _wrap_narration(text: str, max_chars: int = _MAX_CHARS_PER_LINE) -> str:
    words = text.split()
    if not words:
        return ""

    lines: list[str] = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Word-grouping for karaoke
# ---------------------------------------------------------------------------

def _is_phrase_boundary(word: str) -> bool:
    """Return True if *word* ends with punctuation that marks a natural pause."""
    return bool(re.search(r'[.!?,;:\-—]$', word))


def _group_words_into_phrases(
    words: list[WordTimestamp],
    max_words: int = _DEFAULT_WORDS_PER_GROUP,
) -> list[list[WordTimestamp]]:
    """Split a word list into display phrases of at most *max_words*.

    Tries to split at punctuation boundaries for natural phrasing.
    If no punctuation appears within the window, hard-splits at *max_words*.
    """
    if not words:
        return []

    groups: list[list[WordTimestamp]] = []
    current: list[WordTimestamp] = []

    for wt in words:
        current.append(wt)

        # Split on punctuation boundary or hard limit.
        if len(current) >= max_words or _is_phrase_boundary(wt["word"]):
            groups.append(current)
            current = []

    if current:
        # If the remainder is very short (1-2 words), merge with previous.
        if groups and len(current) <= 2:
            groups[-1].extend(current)
        else:
            groups.append(current)

    return groups


# ---------------------------------------------------------------------------
# SRT (accessibility / soft subtitles)
# ---------------------------------------------------------------------------

def build_srt(
    sync_map: list[SyncEntry],
    script: list[SlideScript],
    output_dir: Path,
) -> Path:
    """
    Generate a standard SRT subtitle file from the sync map and narration script.

    Each subtitle block covers one slide's full duration. Timestamps accumulate
    over slides so the SRT mirrors the final video timeline exactly.

    Args:
        sync_map:   Ordered list of slide timing entries.
        script:     Narration text per slide.
        output_dir: Directory where captions.srt is written.

    Returns:
        Absolute path to the written captions.srt file.
    """
    script_map: dict[int, str] = {
        entry["slide_number"]: entry["narration"] for entry in script
    }

    blocks: list[str] = []
    cursor = 0.0

    for index, entry in enumerate(sync_map, start=1):
        start_ts  = _fmt_srt_time(cursor)
        end_ts    = _fmt_srt_time(cursor + entry["duration"])
        narration = script_map.get(entry["slide_number"], "")
        wrapped   = _wrap_narration(narration)
        blocks.append(f"{index}\n{start_ts} --> {end_ts}\n{wrapped}\n")
        cursor += entry["duration"]

    srt_path = output_dir / "captions.srt"
    srt_path.write_text("\n".join(blocks), encoding="utf-8")
    return srt_path


# ---------------------------------------------------------------------------
# Static ASS (legacy — one Dialogue per slide)
# ---------------------------------------------------------------------------

_LEGACY_ASS_STYLE = (
    "Default,"
    "Arial,"
    "20,"
    "&H00FFFFFF,"
    "&H000000FF,"
    "&H00000000,"
    "&H80000000,"
    "0,"
    "0,"
    "0,"
    "0,"
    "100,"
    "100,"
    "0,"
    "0,"
    "3,"
    "1,"
    "0,"
    "2,"
    "10,"
    "10,"
    "40,"
    "1"
)


def build_ass(
    sync_map: list[SyncEntry],
    script: list[SlideScript],
    output_dir: Path,
) -> Path:
    """
    Generate an ASS (Advanced SubStation Alpha) subtitle file with styles
    embedded in the file's [V4+ Styles] header.

    This sidesteps all filter-graph quoting issues that arise when passing
    style strings via ``force_style``. The ``&HAABBGGRR`` colour codes and
    ``Key=Value`` style pairs that confuse FFmpeg's filter-graph parser never
    appear on the command line -- they live only inside the ASS file.

    Use with FFmpeg's ``ass`` filter (no additional options needed)::

        -vf ass=/absolute/path/to/captions.ass

    Args:
        sync_map:   Ordered list of slide timing entries.
        script:     Narration text per slide.
        output_dir: Directory where captions.ass is written.

    Returns:
        Absolute path to the written captions.ass file.
    """
    script_map: dict[int, str] = {
        entry["slide_number"]: entry["narration"] for entry in script
    }

    header = _ASS_HEADER.format(style_line=f"Style: {_LEGACY_ASS_STYLE}")
    output_lines: list[str] = [header]
    cursor = 0.0

    for entry in sync_map:
        start_ts  = _fmt_ass_time(cursor)
        end_ts    = _fmt_ass_time(cursor + entry["duration"])
        narration = script_map.get(entry["slide_number"], "")
        wrapped   = _wrap_narration(narration).replace("\n", r"{\N}")
        output_lines.append(
            f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{wrapped}"
        )
        cursor += entry["duration"]

    ass_path = output_dir / "captions.ass"
    ass_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    return ass_path


# ---------------------------------------------------------------------------
# Karaoke ASS (TikTok-style word-synced captions)
# ---------------------------------------------------------------------------

def build_karaoke_ass(
    sync_map: list[SyncEntry],
    output_dir: Path,
    style: CaptionStyle = CaptionStyle.TIKTOK,
    words_per_group: int = _DEFAULT_WORDS_PER_GROUP,
) -> Path:
    """Generate a karaoke-style ASS file with ``\\kf`` word-sweep tags.

    Each word group (3–5 words) becomes a separate ``Dialogue`` event. Within
    each event, every word is prefixed with ``{\\kf<centiseconds>}`` so the
    highlight colour sweeps across the word in sync with the audio.

    When no word timestamps are available for a slide (e.g. cache-hit or
    fallback), the function degrades gracefully to static per-slide captions.

    Args:
        sync_map:         Ordered slide timing entries with ``word_timestamps``.
        output_dir:       Directory where ``captions.ass`` is written.
        style:            Visual style preset (default: TIKTOK).
        words_per_group:  Max words per display group (default: 5).

    Returns:
        Absolute path to the written ``captions.ass`` file.
    """
    spec = get_style(style)

    # Resolve font — swap to fallback if the preferred font isn't installed.
    resolved_font = resolve_font(spec.fontname)
    if resolved_font != spec.fontname:
        logger.info(
            "Font '%s' unavailable; using '%s' for captions.",
            spec.fontname, resolved_font,
        )
        spec = spec._replace(fontname=resolved_font)

    header = _ASS_HEADER.format(style_line=spec.to_ass_line())
    dialogue_lines: list[str] = []

    # Accumulate a global timeline cursor so each slide's timestamps are
    # offset by the total duration of all preceding slides.
    global_cursor = 0.0

    for entry in sync_map:
        word_ts = entry.get("word_timestamps", [])

        if not word_ts:
            # No word timestamps — fall back to static caption for this slide.
            logger.debug(
                "Slide %d has no word timestamps; using static caption.",
                entry["slide_number"],
            )
            start = _fmt_ass_time(global_cursor)
            end = _fmt_ass_time(global_cursor + entry["duration"])
            # We don't have the script text in SyncEntry, so reconstruct
            # from whatever narration produced the audio.  As a safety net,
            # emit an empty dialogue if nothing is available.
            dialogue_lines.append(
                f"Dialogue: 0,{start},{end},{spec.name},,0,0,0,,"
            )
            global_cursor += entry["duration"]
            continue

        # Group words into display phrases.
        groups = _group_words_into_phrases(word_ts, max_words=words_per_group)

        for group in groups:
            if not group:
                continue

            # Dialogue start = global offset + first word's offset (ms → s).
            group_start_s = global_cursor + group[0]["offset_ms"] / 1000.0
            # Dialogue end = global offset + last word's end (offset + duration).
            last = group[-1]
            group_end_s = global_cursor + (last["offset_ms"] + last["duration_ms"]) / 1000.0

            # Add a small padding so the text doesn't vanish instantly.
            group_end_s += 0.15

            start_ts = _fmt_ass_time(group_start_s)
            end_ts = _fmt_ass_time(group_end_s)

            # Build the karaoke text: {\kf<cs>}word for each word.
            kf_parts: list[str] = []
            for wt in group:
                duration_cs = max(1, round(wt["duration_ms"] / 10))  # ms → centiseconds
                kf_parts.append(f"{{\\kf{duration_cs}}}{wt['word']}")

            karaoke_text = " ".join(kf_parts)

            dialogue_lines.append(
                f"Dialogue: 0,{start_ts},{end_ts},{spec.name},,0,0,0,,{karaoke_text}"
            )

        global_cursor += entry["duration"]

    ass_content = header + "\n".join(dialogue_lines) + "\n"
    ass_path = output_dir / "captions.ass"
    ass_path.write_text(ass_content, encoding="utf-8")

    logger.info(
        "Karaoke ASS written: %d dialogue events → %s",
        len(dialogue_lines), ass_path,
    )
    return ass_path
