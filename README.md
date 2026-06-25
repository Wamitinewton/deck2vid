# PPT Video Engine

A high-performance CLI pipeline that converts PowerPoint presentations into narrated MP4 videos with optional soft subtitle tracks. Slides are rendered to PNG via LibreOffice, narrated by GPT-4.1 (with vision support for image-only slides), voiced by Azure Neural TTS, and assembled in parallel by FFmpeg.

---

## Requirements

### System dependencies

| Tool | Purpose |
|---|---|
| `libreoffice` | Converts `.pptx` → PDF |
| `pdftoppm` | Converts PDF → per-slide PNG images |
| `ffmpeg` | Encodes and concatenates video segments |
| Python ≥ 3.10 | Runtime |

```bash
# Ubuntu / Debian
sudo apt install libreoffice poppler-utils ffmpeg
```

### Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment variables

Create a `.env` file in the project root:

```env
AZURE_OPENAI_API_KEY=your_key_here
AZURE_OPENAI_ENDPOINT=https://your-resource.services.ai.azure.com/openai/v1

AZURE_SPEECH_KEY=your_key_here
AZURE_SPEECH_ENDPOINT=https://your-resource.cognitiveservices.azure.com/
```

---

## Project layout

```
ppt-video-engine/
├── workspace/          ← Drop your .pptx files here
├── output/             ← Generated files written here (per presentation)
│   └── <deck-name>/
│       ├── slides/         slide_01.png, slide_02.png, ...
│       ├── audio/          slide_01.wav, slide_02.wav, ...
│       ├── script.json     GPT-generated narration (JSON)
│       ├── script.txt      GPT-generated narration (plain text)
│       ├── captions.srt    Subtitle file (only with --captions)
│       └── video.mp4       Final output video
├── engine/
│   ├── captions.py     SRT subtitle generator
│   ├── composer.py     Parallel FFmpeg segment encoder + concat
│   ├── extractor.py    PPTX text/notes extractor
│   ├── pipeline.py     Streaming TTS→encode orchestrator
│   ├── renderer.py     LibreOffice + pdftoppm renderer
│   ├── scripter.py     GPT-4.1 narration generator (text + vision)
│   ├── sync.py         Sync map builder (slide timing)
│   └── tts.py          Azure TTS audio synthesiser
├── config/
│   └── settings.py     Defaults and env var loading
├── cli.py              CLI entry point
└── requirements.txt
```

---

## Commands

All commands are run from the project root with the virtual environment active:

```bash
source .venv/bin/activate
```

---

### `generate` — Full pipeline

Runs the complete PPTX → video pipeline.

```bash
python cli.py generate --file <filename.pptx> [OPTIONS]
```

#### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--file` | `TEXT` | *(required)* | Filename of the `.pptx` in `workspace/` |
| `--voice` | `TEXT` | `en-US-AriaNeural` | Azure Neural TTS voice name |
| `--resolution` | `TEXT` | `1920x1080` | Output video resolution as `WIDTHxHEIGHT` |
| `--skip-render` | flag | off | Skip LibreOffice rendering and reuse cached PNGs from a previous run |
| `--script-only` | flag | off | Stop after narration script generation — no audio or video produced |
| `--captions` | flag | off | Embed a soft subtitle (SRT) track into the output MP4 |

#### Examples

```bash
# Standard run — full pipeline
python cli.py generate --file pitch.pptx

# Custom voice and 4K resolution
python cli.py generate --file pitch.pptx \
  --voice en-US-GuyNeural \
  --resolution 3840x2160

# Generate with toggleable captions embedded in the video
python cli.py generate --file pitch.pptx --captions

# Skip slide rendering (reuse existing PNGs from output/pitch/slides/)
python cli.py generate --file pitch.pptx --skip-render

# Re-generate only the narration script, no audio or video
python cli.py generate --file pitch.pptx --script-only

# Combine: skip render + captions + custom voice
python cli.py generate --file pitch.pptx \
  --skip-render \
  --captions \
  --voice en-US-JennyNeural
```

#### Pipeline stages (in order)

```
1. Extract slide content      (python-pptx)
2. Render slides to PNG       (LibreOffice → pdftoppm)   [skipped with --skip-render]
3. Generate narration script  (GPT-4.1 text + vision)    [stops here with --script-only]
4. Synthesise audio + encode  (Azure TTS ∥ FFmpeg)        ← streaming, both run at once
5. Generate SRT captions      (O(n) from sync map)        [only with --captions]
6. Concat segments → MP4      (FFmpeg -c copy)            [SRT embedded with --captions]
```

---

### `list` — List workspace files

Lists all `.pptx` files available in the `workspace/` directory.

```bash
python cli.py list
```

#### Example output

```
Found 3 file(s) in workspace/:

  pitch.pptx       (2048 KB)
  quarterly.pptx   (512 KB)
  onboarding.pptx  (1024 KB)
```

---

### `preview` — Inspect slide content

Extracts and prints slide titles, bullet points, and speaker notes without rendering or generating anything. Useful for verifying what the engine will see before a full run.

```bash
python cli.py preview --file <filename.pptx>
```

#### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--file` | `TEXT` | *(required)* | Filename of the `.pptx` in `workspace/` |

#### Example

```bash
python cli.py preview --file pitch.pptx
```

#### Example output

```
Extracting content from pitch.pptx...

──────────────────────────────────────────────────
Slide 1: Introduction
  • Founded in 2021
  • Serving 50+ enterprise clients
──────────────────────────────────────────────────
Slide 2: The Problem
  • $3.2T lost annually to inefficiency
  • Current tools are fragmented
  [Notes] Pause here and ask the audience about their experience.
──────────────────────────────────────────────────
Total: 12 slides
```

---

## Environment variables (advanced)

These can be set in `.env` or exported in your shell to override defaults without touching code.

| Variable | Default | Description |
|---|---|---|
| `AZURE_OPENAI_API_KEY` | — | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | — | Azure OpenAI endpoint URL |
| `AZURE_SPEECH_KEY` | — | Azure Speech Services key |
| `AZURE_SPEECH_ENDPOINT` | — | Azure Speech Services endpoint URL |
| `TTS_MAX_WORKERS` | `4` | Number of parallel TTS synthesis processes |
| `FFMPEG_BIN` | `ffmpeg` | Path to FFmpeg binary (if not on `$PATH`) |
| `LIBREOFFICE_BIN` | `libreoffice` | Path to LibreOffice binary |
| `PDFTOPPM_BIN` | `pdftoppm` | Path to pdftoppm binary |

---

## Available Azure Neural voices (examples)

Pass any valid Azure Neural voice name to `--voice`.

| Voice | Style |
|---|---|
| `en-US-AriaNeural` *(default)* | Conversational, warm |
| `en-US-GuyNeural` | Confident, professional (male) |
| `en-US-JennyNeural` | Friendly, clear (female) |
| `en-US-DavisNeural` | Deep, authoritative (male) |
| `en-GB-RyanNeural` | British English (male) |
| `en-GB-SoniaNeural` | British English (female) |

Full list: [Azure Neural voices](https://learn.microsoft.com/azure/ai-services/speech-service/language-support)

---

## Output files reference

| File | Created when | Description |
|---|---|---|
| `output/<name>/slides/` | Always | Rendered PNG images per slide |
| `output/<name>/audio/` | Not `--script-only` | WAV audio files per slide |
| `output/<name>/script.json` | Always | Narration script (machine-readable) |
| `output/<name>/script.txt` | Always | Narration script (human-readable) |
| `output/<name>/captions.srt` | `--captions` only | Standalone SRT subtitle file |
| `output/<name>/video.mp4` | Not `--script-only` | Final video (with subtitle track if `--captions`) |

---

## Quick start

```bash
# 1. Clone and set up
git clone <repo-url>
cd ppt-video-engine
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env   # then fill in your Azure keys

# 3. Add your presentation
cp /path/to/deck.pptx workspace/

# 4. Generate video
python cli.py generate --file deck.pptx --captions
```

Output: `output/deck/video.mp4` with embedded subtitle track.
