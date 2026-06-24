from __future__ import annotations

import io
import os
import re
import struct
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import azure.cognitiveservices.speech as speechsdk

from config import settings
from engine.scripter import SlideScript

logger = logging.getLogger(__name__)

# Azure Speech standard tier supports ~20 concurrent requests; 5 is a safe default.
_TTS_MAX_WORKERS = int(os.environ.get("TTS_MAX_WORKERS", "5"))

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0
_MAX_CHUNK_CHARS = 800


def _build_speech_config(voice_name: str) -> speechsdk.SpeechConfig:
    parsed = urlparse(settings.AZURE_SPEECH_ENDPOINT)
    base_endpoint = f"{parsed.scheme}://{parsed.netloc}"

    config = speechsdk.SpeechConfig(
        subscription=settings.AZURE_SPEECH_KEY,
        endpoint=base_endpoint,
    )
    config.speech_synthesis_voice_name = voice_name
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
                logger.warning(
                    "[%s] Transient TTS error on attempt %d/%d – retrying in %.0fs: %s",
                    chunk_label, attempt, _MAX_RETRIES, delay, last_error,
                )
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


def synthesise_slide(
    text: str,
    config: speechsdk.SpeechConfig,
    output_path: Path,
    slide_label: str = "slide",
) -> None:
    sentences = _split_into_sentences(text)
    wav_chunks: list[bytes] = []
    for idx, sentence in enumerate(sentences, start=1):
        wav_bytes = _synthesise_chunk(sentence, config, f"{slide_label}/chunk-{idx}")
        wav_chunks.append(wav_bytes)

    output_path.write_bytes(_concat_wavs(wav_chunks))


def _synthesise_entry(
    entry: SlideScript,
    audio_dir: Path,
    config: speechsdk.SpeechConfig,
) -> tuple[int, Path]:
    """Thread-worker target: synthesise one slide and return (slide_number, path)."""
    slide_num = entry["slide_number"]
    output_path = audio_dir / f"slide_{slide_num:02d}.wav"

    if output_path.exists() and output_path.stat().st_size > 0:
        logger.info("Skipping slide %02d – audio already exists.", slide_num)
        return slide_num, output_path

    synthesise_slide(
        text=entry["narration"],
        config=config,
        output_path=output_path,
        slide_label=f"slide-{slide_num:02d}",
    )
    return slide_num, output_path


def generate_audio(
    script: list[SlideScript], output_dir: str | Path
) -> list[Path]:
    """
    Synthesise audio for all slides concurrently.
    TTS is network I/O so ThreadPoolExecutor gives real parallelism without
    the pickling overhead of multiprocessing. Results are returned in slide order.
    """
    audio_dir = Path(output_dir) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    config = _build_speech_config(settings.AZURE_SPEECH_VOICE)
    results: dict[int, Path] = {}

    with ThreadPoolExecutor(max_workers=_TTS_MAX_WORKERS) as pool:
        future_to_slide = {
            pool.submit(_synthesise_entry, entry, audio_dir, config): entry["slide_number"]
            for entry in script
        }

        for future in as_completed(future_to_slide):
            slide_num = future_to_slide[future]
            try:
                _, path = future.result()
                results[slide_num] = path
                logger.info("   Audio ready for slide %02d", slide_num)
            except Exception as exc:
                for f in future_to_slide:
                    f.cancel()
                raise RuntimeError(f"Audio synthesis failed for slide {slide_num}: {exc}") from exc

    return [results[num] for num in sorted(results)]