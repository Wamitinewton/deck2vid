from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

_SOFFICE_CMD  = os.environ.get("LIBREOFFICE_BIN", "libreoffice")
_PDFTOPPM_CMD = os.environ.get("PDFTOPPM_BIN",   "pdftoppm")

_IN_FLATPAK = Path("/.flatpak-info").exists()


def _host_cmd(cmd: list[str]) -> list[str]:
    if _IN_FLATPAK:
        return ["flatpak-spawn", "--host"] + cmd
    return cmd


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("HOME", str(Path.home()))
    env.setdefault("TMPDIR", "/tmp")
    return env


def _convert_pptx_to_pdf(pptx_path: Path, tmp_dir: Path) -> Path:
    result = subprocess.run(
        _host_cmd([
            _SOFFICE_CMD,
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "-env:UserInstallation=file:///tmp/libreoffice-ppt-engine",
            "--convert-to", "pdf",
            str(pptx_path),
            "--outdir", str(tmp_dir),
        ]),
        check=False,
        capture_output=True,
        text=True,
        env=_subprocess_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"LibreOffice conversion failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    pdf_path = tmp_dir / (pptx_path.stem + ".pdf")
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"LibreOffice did not produce a PDF at {pdf_path}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return pdf_path


def _convert_pdf_to_images(pdf_path: Path, slides_dir: Path) -> list[Path]:
    output_prefix = str(slides_dir / "slide")
    result = subprocess.run(
        _host_cmd([
            _PDFTOPPM_CMD,
            "-png",
            "-r", str(settings.SLIDE_RENDER_DPI),
            str(pdf_path),
            output_prefix,
        ]),
        check=False,
        capture_output=True,
        text=True,
        env=_subprocess_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pdftoppm failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    images = sorted(slides_dir.glob("slide-*.png"))
    if not images:
        raise FileNotFoundError(f"pdftoppm produced no images in {slides_dir}")
    return images


def _rename_images(images: list[Path]) -> list[Path]:
    renamed: list[Path] = []
    for index, image in enumerate(images, start=1):
        new_path = image.parent / f"slide_{index:02d}.png"
        image.rename(new_path)
        renamed.append(new_path)
    return renamed


def render_slides(pptx_path: str | Path, output_dir: str | Path) -> list[Path]:
    pptx_path = Path(pptx_path)
    output_dir = Path(output_dir)
    slides_dir = output_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ppt-engine-", dir=output_dir) as tmp_str:
        tmp_dir = Path(tmp_str)
        pdf_path = _convert_pptx_to_pdf(pptx_path, tmp_dir)
        raw_images = _convert_pdf_to_images(pdf_path, slides_dir)

    return _rename_images(raw_images)