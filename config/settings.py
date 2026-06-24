import os
from dotenv import load_dotenv

load_dotenv()

AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "https://muriithinewton2023-9408-resource.services.ai.azure.com/openai/v1")
AZURE_OPENAI_DEPLOYMENT: str = "gpt-4.1"

AZURE_SPEECH_KEY: str = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_ENDPOINT: str = os.getenv("AZURE_SPEECH_ENDPOINT", "")
AZURE_SPEECH_VOICE: str = "en-US-AriaNeural"

VIDEO_RESOLUTION: tuple[int, int] = (1920, 1080)
VIDEO_FPS: int = 24

TRANSITION_PAUSE_SECONDS: float = 0.8
SLIDE_RENDER_DPI: int = 150

WORKSPACE_DIR: str = "workspace"
OUTPUT_DIR: str = "output"