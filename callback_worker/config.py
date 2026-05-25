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
CALLBACK_UPDATE_API_URL = os.getenv("CALLBACK_UPDATE_API_URL", CALLBACK_API_URL)

GEMINI_API_KEY = os.getenv("GEMINI_LIVE_API_KEY", "")

POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "60"))
BATCH_LIMIT = int(os.getenv("BATCH_LIMIT", "50"))

VERIFY_TRANSCRIPTS_FROM_RECORDING = os.getenv(
    "VERIFY_TRANSCRIPTS_FROM_RECORDING", "true"
).lower() in {"1", "true", "yes", "on"}
DIALER_RECORDING_API_URL = os.getenv(
    "DIALER_RECORDING_API_URL",
    "http://192.168.8.121:8082/jdboxNode/dashboard/fetchFilteredCallData",
)
DIALER_RECORDING_CITY = os.getenv("DIALER_RECORDING_CITY", "bangalore")
DIALER_RECORDING_SERVICE_ID = os.getenv("DIALER_RECORDING_SERVICE_ID", "")
DIALER_RECORDING_LOOKBACK_HOURS = int(os.getenv("DIALER_RECORDING_LOOKBACK_HOURS", "12"))
DIALER_RECORDING_LOOKAHEAD_HOURS = int(os.getenv("DIALER_RECORDING_LOOKAHEAD_HOURS", "12"))
RECORDING_FETCH_TIMEOUT_SEC = int(os.getenv("RECORDING_FETCH_TIMEOUT_SEC", "12"))
RECORDING_TRANSCRIBE_TIMEOUT_SEC = int(os.getenv("RECORDING_TRANSCRIBE_TIMEOUT_SEC", "30"))

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
SARVAM_STT_URL = os.getenv("SARVAM_STT_URL", "https://api.sarvam.ai/speech-to-text")
