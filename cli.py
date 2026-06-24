from __future__ import annotations

import sys
import time
import threading
from contextlib import contextmanager
from pathlib import Path

import click

from config import settings
from engine.composer import compose_video
from engine.extractor import extract_slides
from engine.renderer import render_slides
from engine.scripter import generate_script
from engine.sync import build_sync_map
from engine.tts import generate_audio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_pptx(filename: str) -> Path:
    path = Path(settings.WORKSPACE_DIR) / filename
    if not path.exists():
        click.echo(f"Error: '{path}' not found. Drop your .pptx into the workspace/ folder.", err=True)
        sys.exit(1)
    return path


def _output_dir(pptx_path: Path) -> Path:
    return Path(settings.OUTPUT_DIR) / pptx_path.stem


def _parse_resolution(resolution: str) -> tuple[int, int]:
    try:
        width, height = resolution.lower().split("x")
        return int(width), int(height)
    except ValueError:
        click.echo(f"Error: Invalid resolution '{resolution}'. Use format WIDTHxHEIGHT (e.g. 1920x1080).", err=True)
        sys.exit(1)


def _fmt_duration(seconds: float) -> str:
    """Format elapsed seconds as m:ss or s."""
    if seconds >= 60:
        m = int(seconds) // 60
        s = int(seconds) % 60
        return f"{m}m {s:02d}s"
    return f"{seconds:.1f}s"


@contextmanager
def _step(label: str):
    """
    Context manager that prints a live-updating elapsed timer for a pipeline step.
    A background thread increments the counter every second on the same line;
    on exit it overwrites the line with the final elapsed time.
    """
    start = time.perf_counter()
    stop_event = threading.Event()

    def _ticker():
        while not stop_event.wait(timeout=1.0):
            elapsed = time.perf_counter() - start
            # \r returns to the start of the line without a newline so the
            # counter updates in-place rather than scrolling the terminal.
            click.echo(f"\r  ... {label}  [{_fmt_duration(elapsed)}]", nl=False)

    ticker = threading.Thread(target=_ticker, daemon=True)
    ticker.start()

    try:
        yield
    finally:
        stop_event.set()
        ticker.join()
        elapsed = time.perf_counter() - start
        # Overwrite the spinner line with the final ✓ result.
        click.echo(f"\r  done  {label}  [{_fmt_duration(elapsed)}]")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@click.group()
def cli() -> None:
    pass


@cli.command()
@click.option("--file", "filename", required=True, help="PPTX filename in workspace/")
@click.option("--voice", default=settings.AZURE_SPEECH_VOICE, show_default=True, help="Azure TTS voice name")
@click.option("--resolution", default="1920x1080", show_default=True, help="Output video resolution (WxH)")
@click.option("--skip-render", is_flag=True, default=False, help="Skip LibreOffice slide render and use cached PNGs")
@click.option("--script-only", is_flag=True, default=False, help="Generate narration script only — no audio or video")
def generate(
    filename: str,
    voice: str,
    resolution: str,
    skip_render: bool,
    script_only: bool,
) -> None:
    """Full pipeline: PPTX → render → script (text + vision) → audio → video."""
    settings.AZURE_SPEECH_VOICE = voice

    pptx_path = _resolve_pptx(filename)
    output_dir = _output_dir(pptx_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    pipeline_start = time.perf_counter()
    click.echo(f"\n>> {pptx_path.name}\n")

    with _step("Extracting slide content"):
        slides = extract_slides(pptx_path)
    click.echo(f"     {len(slides)} slides found")

    # Render must happen before scripting so that visual-only slides have a PNG
    # available for GPT-4.1 vision narration.
    slides_dir = output_dir / "slides"
    if skip_render and slides_dir.exists():
        image_paths = sorted(slides_dir.glob("slide_*.png"))
        click.echo(f"  ✓  Using {len(image_paths)} cached slide images  [0.0s]")
    else:
        with _step("Rendering slides to PNG  (LibreOffice)"):
            image_paths = render_slides(pptx_path, output_dir)
        click.echo(f"     {len(image_paths)} images rendered")

    # Build a slide_number → Path lookup consumed by the vision narration path.
    # Filenames follow the pattern slide_01.png produced by _rename_images().
    image_map: dict[int, Path] = {
        int(p.stem.split("_")[1]): p for p in image_paths
    }

    with _step("Generating narration script  (GPT-4.1)"):
        script = generate_script(slides, output_dir, image_paths=image_map)
    click.echo(f"     {len(script)} slides scripted → {output_dir / 'script.json'}")

    if script_only:
        total = _fmt_duration(time.perf_counter() - pipeline_start)
        click.echo(f"\nScript-only complete  [total {total}]\n")
        return

    with _step("Synthesising audio  (Azure TTS)"):
        audio_paths = generate_audio(script, output_dir)
    click.echo(f"     {len(audio_paths)} audio files")

    with _step("Building sync map"):
        sync_map = build_sync_map(image_paths, audio_paths)
    total_duration = sum(e["duration"] for e in sync_map)
    click.echo(f"     Total video duration: {total_duration:.1f}s")

    video_path = output_dir / "video.mp4"
    with _step("Composing final video"):
        compose_video(sync_map, video_path, resolution=_parse_resolution(resolution))

    total = _fmt_duration(time.perf_counter() - pipeline_start)
    click.echo(f"\nDone!  {video_path}  [total {total}]\n")


@cli.command(name="list")
def list_workspace() -> None:
    """List PPTX files available in workspace/."""
    workspace = Path(settings.WORKSPACE_DIR)
    if not workspace.exists():
        click.echo("workspace/ directory does not exist.")
        return

    files = sorted(workspace.glob("*.pptx"))
    if not files:
        click.echo("No .pptx files found in workspace/.")
        return

    click.echo(f"Found {len(files)} file(s) in workspace/:\n")
    for f in files:
        size_kb = f.stat().st_size // 1024
        click.echo(f"  {f.name}  ({size_kb} KB)")


@cli.command()
@click.option("--file", "filename", required=True, help="PPTX filename in workspace/")
def preview(filename: str) -> None:
    """Extract and display slide content without generating audio or video."""
    pptx_path = _resolve_pptx(filename)

    click.echo(f"Extracting content from {pptx_path.name}...\n")
    slides = extract_slides(pptx_path)

    for slide in slides:
        click.echo(f"{'─' * 50}")
        click.echo(f"Slide {slide['slide_number']}: {slide['title'] or '(no title)'}")
        for line in slide["body_text"]:
            click.echo(f"  • {line}")
        if slide["notes"]:
            click.echo(f"  [Notes] {slide['notes']}")

    click.echo(f"\n{'─' * 50}")
    click.echo(f"Total: {len(slides)} slides")


if __name__ == "__main__":
    cli()