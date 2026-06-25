from pathlib import Path
from engine.scripter import SlideScript
from engine.sync import SyncEntry

def _fmt_srt_time(total_seconds: float) -> str:
    millis = round((total_seconds % 1) * 1000)
    secs   = int(total_seconds) % 60
    mins   = (int(total_seconds) // 60) % 60
    hrs    = int(total_seconds) // 3600
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

def _wrap_narration(text: str, max_chars: int = 58) -> str:
    words = text.split()
    if not words: return ""
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars: current = candidate
        else:
            if current: lines.append(current)
            current = word
    if current: lines.append(current)
    return "\n".join(lines)

def build_srt(sync_map: list[SyncEntry], script: list[SlideScript], output_dir: Path) -> Path:
    script_map = {entry["slide_number"]: entry["narration"] for entry in script}
    blocks, cursor = [], 0.0

    for index, entry in enumerate(sync_map, start=1):
        start_ts = _fmt_srt_time(cursor)
        end_ts = _fmt_srt_time(cursor + entry["duration"])
        narration = script_map.get(entry["slide_number"], "")
        wrapped = _wrap_narration(narration)
        blocks.append(f"{index}\n{start_ts} --> {end_ts}\n{wrapped}\n")
        cursor += entry["duration"]

    srt_path = output_dir / "captions.srt"
    srt_path.write_text("\n".join(blocks), encoding="utf-8")
    return srt_path