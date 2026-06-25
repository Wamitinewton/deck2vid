import logging
import multiprocessing
import os
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

from config import settings
from engine.composer import _encode_segment_with_captions, _encode_segment_static
from engine.caption_styles import CaptionStyle
from engine.scripter import SlideScript
from engine.sync import SyncEntry, WordTimestamp, get_audio_duration
from engine.tts import (
    _SynthesisTask, _TTS_MAX_WORKERS, _init_tts_worker, _run_synthesis_task,
)

# Piping raw RGB frames is RAM/CPU intensive. Limit to 4 to prevent OOM issues.
_ENCODE_WORKERS = min(os.cpu_count() or 4, 4)

logger = logging.getLogger(__name__)

def run_streaming_pipeline(
    script: list[SlideScript],
    image_map: dict[int, Path],
    output_dir: Path,
    segments_dir: Path,
    width: int,
    height: int,
    render_captions: bool,
    caption_style: CaptionStyle | None
) -> tuple[list[SyncEntry], list[str]]:
    
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)

    tts_tasks = [
        _SynthesisTask(
            slide_number=e["slide_number"], narration=e["narration"],
            output_path=str(audio_dir / f"slide_{e['slide_number']:02d}.wav"),
            speech_key=settings.AZURE_SPEECH_KEY, speech_endpoint=settings.AZURE_SPEECH_ENDPOINT,
            voice_name=settings.AZURE_SPEECH_VOICE,
        ) for e in script
    ]

    sync_entries: dict[int, SyncEntry] = {}
    encode_futures: dict[int, Future] = {}

    target_encode_fn = _encode_segment_with_captions if render_captions else _encode_segment_static

    with ProcessPoolExecutor(max_workers=_TTS_MAX_WORKERS, mp_context=multiprocessing.get_context("spawn"), initializer=_init_tts_worker) as tts_pool, \
         ProcessPoolExecutor(max_workers=_ENCODE_WORKERS) as encode_pool:
        
        tts_future_map = {tts_pool.submit(_run_synthesis_task, t): t for t in tts_tasks}

        for tts_future in as_completed(tts_future_map):
            task = tts_future_map[tts_future]
            try:
                slide_num, audio_path_str, word_timestamps = tts_future.result()
            except Exception as exc:
                for f in tts_future_map: f.cancel()
                raise RuntimeError(f"TTS failed for slide {task.slide_number}: {exc}") from exc

            duration = round(get_audio_duration(Path(audio_path_str)) + settings.TRANSITION_PAUSE_SECONDS, 3)
            
            sync_entries[slide_num] = SyncEntry(
                slide_number=slide_num, image_path=str(image_map[slide_num]),
                audio_path=audio_path_str, duration=duration, word_timestamps=word_timestamps,
            )

            encode_task = {
                'slide_number': slide_num,
                'image_path': str(image_map[slide_num]),
                'audio_path': audio_path_str,
                'duration': duration,
                'output_path': str(segments_dir / f"segment_{slide_num:04d}.ts"),
                'width': width,
                'height': height,
                'fps': settings.VIDEO_FPS,
                'word_timestamps': word_timestamps,
                'caption_style': caption_style
            }
            encode_futures[slide_num] = encode_pool.submit(target_encode_fn, encode_task)
            logger.info("  ✓ TTS slide %02d → encode submitted", slide_num)

        completed_segments = {}
        for slide_num, enc_future in encode_futures.items():
            try:
                _, seg_path = enc_future.result()
                completed_segments[slide_num] = seg_path
            except Exception as exc:
                for f in encode_futures.values(): f.cancel()
                raise RuntimeError(f"Encode failed for slide {slide_num}: {exc}") from exc

    return [sync_entries[n] for n in sorted(sync_entries)], [completed_segments[n] for n in sorted(completed_segments)]