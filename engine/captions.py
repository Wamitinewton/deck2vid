from __future__ import annotations

from pathlib import Path

from engine.scripter import SlideScript
from engine.sync import SyncEntry

_MAX_CHARS_PER_LINE = 58

_ASS_STYLE = (
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

_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: {style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


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

    output_lines: list[str] = [_ASS_HEADER.format(style=_ASS_STYLE)]
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
