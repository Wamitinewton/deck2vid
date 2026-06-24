"""
Two-phase parallel video composition engine.

Phase 1: Each slide is encoded into an MPEG-TS segment by its own FFmpeg
         process, all running concurrently via ProcessPoolExecutor.
Phase 2: Segments are joined losslessly with the FFmpeg concat demuxer
         (-c copy), completing in seconds regardless of total duration.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

from config import settings
from engine.sync import SyncEntry

logger = logging.getLogger(__name__)

_FFMPEG_CMD = os.environ.get("FFMPEG_BIN", "ffmpeg")


class _SegmentTask(NamedTuple):
    """Picklable descriptor for one per-slide FFmpeg encode job."""
    slide_number: int
    image_path: str
    audio_path: str
    duration: float
    output_path: str
    width: int
    height: int
    fps: int
    ffmpeg_cmd: str


def _encode_segment(task: _SegmentTask) -> tuple[int, str]:
    """
    Encode one slide into an MPEG-TS segment.

    Must be module-level so ProcessPoolExecutor can pickle it.
    Key flags: -tune stillimage skips motion search (40-70% faster for static
    content); -avoid_negative_ts make_zero aligns PTS for seamless concat;
    -shortest stops the infinite -loop 1 stream when audio ends.
    """
    cmd = [
        task.ffmpeg_cmd,
        "-y",
        "-loop", "1",
        "-i", task.image_path,
        "-i", task.audio_path,
        "-vf", (
            f"scale={task.width}:{task.height}"
            f":force_original_aspect_ratio=decrease,"
            f"pad={task.width}:{task.height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"format=yuv420p"
        ),
        "-c:v",    "libx264",
        "-tune",   "stillimage",
        "-preset", "fast",
        "-crf",    "18",
        "-r",      str(task.fps),
        "-c:a",    "aac",
        "-b:a",    "192k",
        "-shortest",
        "-t",      str(task.duration),
        "-avoid_negative_ts", "make_zero",
        "-f",      "mpegts",
        task.output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed for slide {task.slide_number} "
            f"(exit {result.returncode}):\n{result.stderr[-2000:]}"
        )
    return task.slide_number, task.output_path


def _concat_segments(segment_paths: list[str], output_path: str, ffmpeg_cmd: str) -> None:
    """
    Join MPEG-TS segments into a single MP4 via the concat demuxer (-c copy).
    No re-encoding occurs; streams are bitstream-copied directly.
    -movflags +faststart moves the moov atom to the front for web streaming.
    """
    manifest_path = Path(output_path).parent / "_concat_manifest.txt"
    with manifest_path.open("w", encoding="utf-8") as fh:
        for seg in segment_paths:
            fh.write(f"file '{seg}'\n")

    cmd = [
        ffmpeg_cmd, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(manifest_path),
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    manifest_path.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg concat failed (exit {result.returncode}):\n{result.stderr[-2000:]}"
        )


def _encode_all_segments_parallel(
    sync_map: list[SyncEntry],
    segments_dir: Path,
    width: int,
    height: int,
    max_workers: int,
) -> list[str]:
    """Submit all segment encode jobs to the pool and return paths in slide order."""
    tasks = [
        _SegmentTask(
            slide_number=entry["slide_number"],
            image_path=entry["image_path"],
            audio_path=entry["audio_path"],
            duration=entry["duration"],
            output_path=str(segments_dir / f"segment_{entry['slide_number']:04d}.ts"),
            width=width,
            height=height,
            fps=settings.VIDEO_FPS,
            ffmpeg_cmd=_FFMPEG_CMD,
        )
        for entry in sync_map
    ]

    completed: dict[int, str] = {}

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        future_to_slide = {pool.submit(_encode_segment, t): t.slide_number for t in tasks}

        for future in as_completed(future_to_slide):
            slide_num = future_to_slide[future]
            try:
                _, seg_path = future.result()
                completed[slide_num] = seg_path
                logger.info("  ✓ Slide %d encoded", slide_num)
            except Exception as exc:
                for f in future_to_slide:
                    f.cancel()
                raise RuntimeError(f"Slide {slide_num} encode failed: {exc}") from exc

    return [completed[num] for num in sorted(completed)]


def compose_video(
    sync_map: list[SyncEntry],
    output_path: str | Path,
    resolution: tuple[int, int] = settings.VIDEO_RESOLUTION,
) -> Path:
    """
    Encode all slides in parallel, then concatenate into a final MP4.
    Worker count is capped at cpu_count() — adding more than one worker per
    core adds context-switch overhead with no throughput gain.
    Segments live in a sibling directory so all I/O stays on the same
    filesystem, avoiding cross-device copy on cleanup.
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    width, height = resolution
    max_workers = min(len(sync_map), os.cpu_count() or 4)

    logger.info("Composing %d slide(s) at %dx%d – %d workers", len(sync_map), width, height, max_workers)

    segments_dir = output_path.parent / "_segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    try:
        segment_paths = _encode_all_segments_parallel(
            sync_map=sync_map,
            segments_dir=segments_dir,
            width=width,
            height=height,
            max_workers=max_workers,
        )
        _concat_segments(
            segment_paths=segment_paths,
            output_path=str(output_path),
            ffmpeg_cmd=_FFMPEG_CMD,
        )
    finally:
        shutil.rmtree(segments_dir, ignore_errors=True)

    return output_path