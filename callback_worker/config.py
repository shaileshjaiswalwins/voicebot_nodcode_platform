import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root while allowing launch-time overrides.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://192.168.13.65:27017")
MONGO_DB = os.getenv("VOICEBOT_PLATFORM_DB") or os.getenv("MONGO_DB", "ai_voice_bot_management")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "tbl_ai_vb_call_transcripts")

CALLBACK_API_URL = os.getenv(
    "CALLBACK_API_URL",
    "http://192.168.8.67:8000/leads/ai-lead-qualify/callback",
)

GEMINI_API_KEY = os.getenv("GEMINI_LIVE_API_KEY", "")

POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "60"))
BATCH_LIMIT = int(os.getenv("BATCH_LIMIT", "50"))
