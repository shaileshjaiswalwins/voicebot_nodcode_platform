import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root (parent of this package)
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

MONGO_URI = os.environ["MONGO_URI"]  # no prod fallback — must be set explicitly, even for local/dev
MONGO_DB = "ai_lead_qualify"
MONGO_COLLECTION = "call_transcripts"

CALLBACK_API_URL = os.environ["CALLBACK_API_URL"]  # no prod fallback — must be set explicitly
CALLBACK_UPDATE_API_URL = os.environ["CALLBACK_UPDATE_API_URL"]  # no prod fallback — must be set explicitly

GEMINI_API_KEY = os.getenv("GEMINI_ANALYSIS_API_KEY")

# JIRA-AIP-799: hot lead flow (business leads pitch + b2b follow-up).
HOT_LEAD_FLOW_ENABLED = True

POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "60"))
BATCH_LIMIT = int(os.getenv("BATCH_LIMIT", "50"))

LOG_DIR = os.path.join(
    os.environ.get("BOT_LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")),
    "analysis_logs",
)

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
