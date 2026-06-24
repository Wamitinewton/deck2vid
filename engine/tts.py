from __future__ import annotations

import io
import multiprocessing
import os
import re
import struct
import time
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import azure.cognitiveservices.speech as speechsdk

from config import settings
from engine.scripter import SlideScript

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0
_MAX_CHUNK_CHARS = 800

# Number of slides synthesised in parallel. Each worker is a fully isolated
# OS process, so the Azure Speech SDK's native library has no shared state
# between them. Safe to run 3–5 concurrently on standard Azure Speech tiers.
_TTS_MAX_WORKERS = int(os.environ.get("TTS_MAX_WORKERS", "4"))


# ---------------------------------------------------------------------------
# Picklable task descriptor
# ---------------------------------------------------------------------------

class _SynthesisTask(NamedTuple):
    """
    Plain-data descriptor passed to each worker process.

    SpeechConfig holds a native C++ handle and cannot be pickled, so we pass
    the raw credentials instead and reconstruct the config inside the worker.
    """
    slide_number: int
    narration: str
    output_path: str       # str, not Path — NamedTuple must be picklable
    speech_key: str
    speech_endpoint: str
    voice_name: str


# ---------------------------------------------------------------------------
# Internal helpers (all run inside worker processes)
# ---------------------------------------------------------------------------

def _build_speech_config(key: str, endpoint: str, voice: str) -> speechsdk.SpeechConfig:
    parsed = urlparse(endpoint)
    base_endpoint = f"{parsed.scheme}://{parsed.netloc}"
    config = speechsdk.SpeechConfig(subscription=key, endpoint=base_endpoint)
    config.speech_synthesis_voice_name = voice
    return config


def _split_into_sentences(text: str) -> list[str]:
    raw = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences: list[str] = []
    for s in raw:
        s = s.strip()
        if not s:
            continue
        if len(s) <= _MAX_CHUNK_CHARS:
            sentences.append(s)
        else:
            clauses = re.split(r'(?<=[,;])\s+', s)
            sentences.extend(c.strip() for c in clauses if c.strip())
    return sentences or [text.strip()]


def _synthesise_chunk(
    text: str,
    config: speechsdk.SpeechConfig,
    chunk_label: str,
) -> bytes:
    delay = _RETRY_BASE_DELAY
    last_error: str = ""

    for attempt in range(1, _MAX_RETRIES + 1):
        stream = speechsdk.audio.PullAudioOutputStream()
        audio_config = speechsdk.audio.AudioOutputConfig(stream=stream)
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=config,
            audio_config=audio_config,
        )

        result = synthesizer.speak_text_async(text).get()

        # Explicit deletion ensures the SDK releases its internal event loop
        # handle before the next attempt, preventing handle accumulation.
        del synthesizer

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            return result.audio_data

        if result.reason == speechsdk.ResultReason.Canceled:
            details = result.cancellation_details
            last_error = (
                details.error_details
                if details.reason == speechsdk.CancellationReason.Error
                else str(details.reason)
            )

            is_transient = any(
                kw in last_error
                for kw in ("Timeout", "timeout", "Connection", "connection", "reset")
            )
            if is_transient and attempt < _MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
                continue

            raise RuntimeError(
                f"[{chunk_label}] TTS synthesis failed after {attempt} attempt(s): {last_error}"
            )

        raise RuntimeError(f"[{chunk_label}] TTS returned unknown result reason.")

    raise RuntimeError(
        f"[{chunk_label}] TTS synthesis failed after {_MAX_RETRIES} attempts: {last_error}"
    )


def _concat_wavs(wav_chunks: list[bytes]) -> bytes:
    if len(wav_chunks) == 1:
        return wav_chunks[0]

    first = io.BytesIO(wav_chunks[0])
    first.seek(22)
    num_channels    = struct.unpack('<H', first.read(2))[0]
    sample_rate     = struct.unpack('<I', first.read(4))[0]
    byte_rate       = struct.unpack('<I', first.read(4))[0]
    block_align     = struct.unpack('<H', first.read(2))[0]
    bits_per_sample = struct.unpack('<H', first.read(2))[0]

    pcm_data = b"".join(chunk[44:] for chunk in wav_chunks)
    pcm_size = len(pcm_data)

    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + pcm_size,
        b'WAVE',
        b'fmt ',
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b'data',
        pcm_size,
    )
    return header + pcm_data


# ---------------------------------------------------------------------------
# Worker entry point (module-level so ProcessPoolExecutor can pickle it)
# ---------------------------------------------------------------------------

def _run_synthesis_task(task: _SynthesisTask) -> tuple[int, str]:
    """
    Process-worker entry point: synthesise one slide and write the WAV file.

    Building SpeechConfig here (rather than in the parent) ensures the native
    SDK handle is created and destroyed entirely within this process — avoiding
    the cross-thread handle sharing that causes segfaults in the C++ layer.
    """
    output_path = Path(task.output_path)

    if output_path.exists() and output_path.stat().st_size > 0:
        return task.slide_number, task.output_path

    config = _build_speech_config(task.speech_key, task.speech_endpoint, task.voice_name)
    sentences = _split_into_sentences(task.narration)
    wav_chunks: list[bytes] = []

    for idx, sentence in enumerate(sentences, start=1):
        label = f"slide-{task.slide_number:02d}/chunk-{idx}"
        wav_chunks.append(_synthesise_chunk(sentence, config, label))

    output_path.write_bytes(_concat_wavs(wav_chunks))
    return task.slide_number, task.output_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_audio(
    script: list[SlideScript], output_dir: str | Path
) -> list[Path]:
    """
    Synthesise audio for all slides in parallel using process isolation.

    The Azure Speech SDK wraps a native C++ library whose internal event loop
    is not safe to share across OS threads. Using ProcessPoolExecutor with the
    'spawn' start method gives each worker a clean address space — the SDK is
    freshly initialised per process and never shares handles with siblings.
    """
    audio_dir = Path(output_dir) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    tasks = [
        _SynthesisTask(
            slide_number=entry["slide_number"],
            narration=entry["narration"],
            output_path=str(audio_dir / f"slide_{entry['slide_number']:02d}.wav"),
            speech_key=settings.AZURE_SPEECH_KEY,
            speech_endpoint=settings.AZURE_SPEECH_ENDPOINT,
            voice_name=settings.AZURE_SPEECH_VOICE,
        )
        for entry in script
    ]

    results: dict[int, Path] = {}

    # 'spawn' creates a fresh interpreter for each worker — required on Linux
    # to prevent the SDK's native library from inheriting file descriptors and
    # internal state from the parent process (which 'fork' would copy).
    mp_context = multiprocessing.get_context("spawn")

    with ProcessPoolExecutor(max_workers=_TTS_MAX_WORKERS, mp_context=mp_context) as pool:
        future_to_slide = {
            pool.submit(_run_synthesis_task, task): task.slide_number
            for task in tasks
        }

        for future in as_completed(future_to_slide):
            slide_num = future_to_slide[future]
            try:
                num, path_str = future.result()
                results[num] = Path(path_str)
                logger.info("Audio ready: slide %02d", num)
            except Exception as exc:
                for f in future_to_slide:
                    f.cancel()
                raise RuntimeError(f"Audio synthesis failed for slide {slide_num}: {exc}") from exc

    return [results[num] for num in sorted(results)]