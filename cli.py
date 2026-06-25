import shutil
import sys
import time
import threading
from contextlib import contextmanager
from pathlib import Path
import click

from config import settings
from engine.caption_styles import CaptionStyle
from engine.captions import build_srt
from engine.composer import _concat_segments
from engine.extractor import extract_slides
from engine.pipeline import run_streaming_pipeline
from engine.renderer import render_slides
from engine.scripter import generate_script

def _resolve_pptx(filename: str) -> Path:
    path = Path(settings.WORKSPACE_DIR) / filename
    if not path.exists():
        click.echo(f"Error: '{path}' not found.", err=True)
        sys.exit(1)
    return path

def _output_dir(pptx_path: Path) -> Path:
    return Path(settings.OUTPUT_DIR) / pptx_path.stem

def _parse_resolution(resolution: str) -> tuple[int, int]:
    width, height = resolution.lower().split("x")
    return int(width), int(height)

def _fmt_duration(seconds: float) -> str:
    if seconds >= 60: return f"{int(seconds) // 60}m {int(seconds) % 60:02d}s"
    return f"{seconds:.1f}s"

@contextmanager
def _step(label: str):
    start = time.perf_counter()
    stop_event = threading.Event()
    def _ticker():
        while not stop_event.wait(timeout=1.0):
            click.echo(f"\r  ... {label}  [{_fmt_duration(time.perf_counter() - start)}]", nl=False)
    ticker = threading.Thread(target=_ticker, daemon=True)
    ticker.start()
    try: yield
    finally:
        stop_event.set()
        ticker.join()
        click.echo(f"\r  done  {label}  [{_fmt_duration(time.perf_counter() - start)}]")

@click.group()
def cli(): pass

@cli.command()
@click.option("--file", "filename", required=True)
@click.option("--voice", default=settings.AZURE_SPEECH_VOICE)
@click.option("--resolution", default="1920x1080")
@click.option("--skip-render", is_flag=True, default=False)
@click.option("--script-only", is_flag=True, default=False)
@click.option("--captions", is_flag=True, default=False)
@click.option("--caption-style", type=click.Choice([s.value for s in CaptionStyle], case_sensitive=False), default=CaptionStyle.TIKTOK.value)
def generate(filename, voice, resolution, skip_render, script_only, captions, caption_style):
    settings.AZURE_SPEECH_VOICE = voice
    pptx_path, output_dir = _resolve_pptx(filename), _output_dir(_resolve_pptx(filename))
    output_dir.mkdir(parents=True, exist_ok=True)
    pipeline_start = time.perf_counter()
    click.echo(f"\n>> {pptx_path.name}\n")

    with _step("Extracting slide content"): slides = extract_slides(pptx_path)
    slides_dir = output_dir / "slides"
    
    if skip_render and slides_dir.exists():
        image_paths = sorted(slides_dir.glob("slide_*.png"))
    else:
        with _step("Rendering slides to PNG"): image_paths = render_slides(pptx_path, output_dir)

    image_map = {int(p.stem.split("_")[1]): p for p in image_paths}

    with _step("Generating narration script"): script = generate_script(slides, output_dir, image_map)
    if script_only: return click.echo(f"\nScript-only complete\n")

    width, height = _parse_resolution(resolution)
    segments_dir = output_dir / "_segments"
    selected_style = CaptionStyle(caption_style) if captions else None

    with _step(f"Synthesising + encoding segments {'(Python rendering)' if captions else ''}"):
        sync_map, segment_paths = run_streaming_pipeline(
            script=script, image_map=image_map, output_dir=output_dir,
            segments_dir=segments_dir, width=width, height=height,
            render_captions=captions, caption_style=selected_style
        )

    srt_path = build_srt(sync_map, script, output_dir) if captions else None
    video_path = output_dir / "video.mp4"

    with _step("Concatenating segments"):
        _concat_segments(segment_paths, str(video_path), str(srt_path) if srt_path else None)

    shutil.rmtree(segments_dir, ignore_errors=True)
    click.echo(f"\nDone! {video_path} [{_fmt_duration(time.perf_counter() - pipeline_start)}]\n")

if __name__ == "__main__":
    cli()