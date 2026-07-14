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
