from __future__ import annotations

import wave
from pathlib import Path
from typing import TypedDict

from config import settings


class WordTimestamp(TypedDict):
    """Timing information for a single spoken word within a slide's audio."""
    word: str
    offset_ms: float    # milliseconds from start of this slide's audio
    duration_ms: float  # how long this word takes to speak


class SyncEntry(TypedDict):
    slide_number: int
    image_path: str
    audio_path: str
    duration: float
    word_timestamps: list[WordTimestamp]


def get_audio_duration(audio_path: Path) -> float:
    """Return the duration of a WAV file in seconds."""
    with wave.open(str(audio_path), "rb") as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
        return frames / float(rate)


# Kept for backward compatibility within the package.
_get_audio_duration = get_audio_duration


def build_sync_map(
    image_paths: list[Path],
    audio_paths: list[Path],
) -> list[SyncEntry]:
    if len(image_paths) != len(audio_paths):
        raise ValueError(
            f"Slide count mismatch: {len(image_paths)} images vs {len(audio_paths)} audio files.\n"
            f"Ensure generate_script returned an entry for every slide, including visual-only slides. "
            f"Try re-running without --skip-render."
        )

    sync_map: list[SyncEntry] = []
    for index, (image, audio) in enumerate(zip(image_paths, audio_paths), start=1):
        duration = get_audio_duration(audio) + settings.TRANSITION_PAUSE_SECONDS
        sync_map.append(
            SyncEntry(
                slide_number=index,
                image_path=str(image),
                audio_path=str(audio),
                duration=round(duration, 3),
                word_timestamps=[],
            )
        )

    return sync_map