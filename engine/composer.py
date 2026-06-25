import os
import shutil
import subprocess
import logging
from pathlib import Path
from PIL import Image
from concurrent.futures import ProcessPoolExecutor, as_completed

from config import settings
from engine.caption_styles import CaptionStyle, get_style
from engine.fonts import resolve_pil_font
from engine.custom_renderer import FrameRenderer

logger = logging.getLogger(__name__)

# Dynamically locate FFmpeg in the system path to prevent hardcoded failures
_FFMPEG_CMD = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"

def _resize_and_pad(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Letterbox fit an image to exact dimensions required by rawvideo."""
    img_ratio = img.width / img.height
    target_ratio = target_w / target_h
    
    if img_ratio > target_ratio:
        new_w, new_h = target_w, int(target_w / img_ratio)
    else:
        new_w, new_h = int(target_h * img_ratio), target_h
        
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    new_img = Image.new("RGB", (target_w, target_h), (0, 0, 0))
    new_img.paste(img, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return new_img

def _encode_segment_with_captions(task_data: dict) -> tuple[int, str]:
    slide_num, width, height = task_data['slide_number'], task_data['width'], task_data['height']
    fps, duration = task_data['fps'], task_data['duration']
    
    cmd = [
        _FFMPEG_CMD, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{width}x{height}", "-pix_fmt", "rgb24", "-r", str(fps),
        "-i", "-", 
        "-i", task_data['audio_path'],
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p",
        "-shortest", "-t", str(duration), "-avoid_negative_ts", "make_zero",
        "-f", "mpegts", task_data['output_path']
    ]
    
    style = get_style(task_data['caption_style'])
    font = resolve_pil_font(style.fontname, style.fontsize)
    
    base_img = Image.open(task_data['image_path']).convert("RGB")
    base_img = _resize_and_pad(base_img, width, height)
    renderer = FrameRenderer(base_img, task_data['word_timestamps'], style, font)
    
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    total_frames = int(duration * fps)
    
    try:
        for frame_idx in range(total_frames):
            current_time_ms = (frame_idx / fps) * 1000.0
            frame_bytes = renderer.render(current_time_ms).tobytes()
            proc.stdin.write(frame_bytes)
    except BrokenPipeError:
        pass 
    finally:
        if proc.stdin:
            proc.stdin.close()
        _, stderr_out = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg failed for slide {slide_num}:\n{stderr_out.decode('utf-8')[-1000:]}")
            
    return slide_num, task_data['output_path']

def _encode_segment_static(task_data: dict) -> tuple[int, str]:
    """Fast-path when captions are disabled."""
    cmd = [
        _FFMPEG_CMD, "-y", "-loop", "1",
        "-i", task_data['image_path'], "-i", task_data['audio_path'],
        "-vf", f"scale={task_data['width']}:{task_data['height']}:force_original_aspect_ratio=decrease,pad={task_data['width']}:{task_data['height']}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
        "-c:v", "libx264", "-tune", "stillimage", "-preset", "fast", "-crf", "18",
        "-r", str(task_data['fps']), "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-t", str(task_data['duration']), "-avoid_negative_ts", "make_zero",
        "-f", "mpegts", task_data['output_path']
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Static render failed:\n{res.stderr[-1000:]}")
    return task_data['slide_number'], task_data['output_path']

def _concat_segments(segment_paths: list[str], output_path: str, srt_path: str = None):
    manifest_path = Path(output_path).parent / "_concat_manifest.txt"
    with manifest_path.open("w", encoding="utf-8") as fh:
        for seg in segment_paths:
            fh.write(f"file '{Path(seg).resolve().as_posix()}'\n")

    base_cmd = [_FFMPEG_CMD, "-y", "-f", "concat", "-safe", "0", "-i", str(manifest_path)]
    
    if srt_path:
        abs_srt = Path(srt_path).resolve().as_posix()
        cmd = base_cmd + [
            "-i", abs_srt,
            "-map", "0:v", "-map", "0:a", "-map", "1:s",
            "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
            "-disposition:s:0", "default",
            "-movflags", "+faststart", output_path
        ]
    else:
        cmd = base_cmd + ["-c", "copy", "-movflags", "+faststart", output_path]

    res = subprocess.run(cmd, capture_output=True, text=True)
    manifest_path.unlink(missing_ok=True)
    if res.returncode != 0:
        raise RuntimeError(f"Concat failed:\n{res.stderr[-1000:]}")