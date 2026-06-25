from __future__ import annotations

import logging
import multiprocessing
import os
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

from config import settings
from engine.composer import _FFMPEG_CMD, _SegmentTask, _encode_segment
from engine.scripter import SlideScript
from engine.sync import SyncEntry, get_audio_duration
from engine.tts import (
    _SynthesisTask,
    _TTS_MAX_WORKERS,
    _init_tts_worker,
    _run_synthesis_task,
)

_ENCODE_THREAD_WORKERS = max(4, os.cpu_count() or 4)

logger = logging.getLogger(__name__)


def _make_tts_task(entry: SlideScript, audio_dir: Path) -> _SynthesisTask:
    return _SynthesisTask(
        slide_number=entry["slide_number"],
        narration=entry["narration"],
        output_path=str(audio_dir / f"slide_{entry['slide_number']:02d}.wav"),
        speech_key=settings.AZURE_SPEECH_KEY,
        speech_endpoint=settings.AZURE_SPEECH_ENDPOINT,
        voice_name=settings.AZURE_SPEECH_VOICE,
    )


def _make_encode_task(
    slide_number: int,
    image_path: Path,
    audio_path: str,
    duration: float,
    segments_dir: Path,
    width: int,
    height: int,
) -> _SegmentTask:
    return _SegmentTask(
        slide_number=slide_number,
        image_path=str(image_path),
        audio_path=audio_path,
        duration=duration,
        output_path=str(segments_dir / f"segment_{slide_number:04d}.ts"),
        width=width,
        height=height,
        fps=settings.VIDEO_FPS,
        ffmpeg_cmd=_FFMPEG_CMD,
    )


def run_streaming_pipeline(
    script: list[SlideScript],
    image_map: dict[int, Path],
    output_dir: Path,
    segments_dir: Path,
    width: int,
    height: int,
) -> tuple[list[SyncEntry], list[str]]:
    """
    Synthesise audio and encode video segments in an overlapping pipeline.

    The TTS process pool and FFmpeg thread pool run simultaneously. As each
    slide's WAV completes, its encode job is submitted immediately — no phase
    barrier between synthesis and encoding.
    """
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)

    tts_tasks = [_make_tts_task(entry, audio_dir) for entry in script]
    spawn_ctx = multiprocessing.get_context("spawn")

    sync_entries: dict[int, SyncEntry] = {}
    encode_futures: dict[int, Future[tuple[int, str]]] = {}

    logger.info(
        "Streaming pipeline: %d slides | TTS workers=%d | encode threads=%d",
        len(script),
        _TTS_MAX_WORKERS,
        _ENCODE_THREAD_WORKERS,
    )

    with (
        ProcessPoolExecutor(
            max_workers=_TTS_MAX_WORKERS,
            mp_context=spawn_ctx,
            initializer=_init_tts_worker,
        ) as tts_pool,
        ThreadPoolExecutor(max_workers=_ENCODE_THREAD_WORKERS) as encode_pool,
    ):
        tts_future_map: dict[Future[tuple[int, str]], _SynthesisTask] = {
            tts_pool.submit(_run_synthesis_task, task): task
            for task in tts_tasks
        }

        for tts_future in as_completed(tts_future_map):
            task = tts_future_map[tts_future]
            try:
                slide_num, audio_path_str = tts_future.result()
            except Exception as exc:
                for f in tts_future_map:
                    f.cancel()
                raise RuntimeError(
                    f"TTS failed for slide {task.slide_number}: {exc}"
                ) from exc

            audio_path = Path(audio_path_str)
            duration = round(
                get_audio_duration(audio_path) + settings.TRANSITION_PAUSE_SECONDS, 3
            )
            image_path = image_map[slide_num]

            sync_entries[slide_num] = SyncEntry(
                slide_number=slide_num,
                image_path=str(image_path),
                audio_path=audio_path_str,
                duration=duration,
            )

            encode_task = _make_encode_task(
                slide_number=slide_num,
                image_path=image_path,
                audio_path=audio_path_str,
                duration=duration,
                segments_dir=segments_dir,
                width=width,
                height=height,
            )
            encode_futures[slide_num] = encode_pool.submit(_encode_segment, encode_task)
            logger.info("  ✓ TTS slide %02d → encode submitted", slide_num)

        completed_segments: dict[int, str] = {}
        for slide_num, enc_future in encode_futures.items():
            try:
                _, seg_path = enc_future.result()
                completed_segments[slide_num] = seg_path
                logger.info("  ✓ Encoded slide %02d", slide_num)
            except Exception as exc:
                for f in encode_futures.values():
                    f.cancel()
                raise RuntimeError(
                    f"Encode failed for slide {slide_num}: {exc}"
                ) from exc

    sync_map = [sync_entries[n] for n in sorted(sync_entries)]
    segment_paths = [completed_segments[n] for n in sorted(completed_segments)]
    return sync_map, segment_paths
