import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root (parent of this package)
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://192.168.13.65:27017")
MONGO_DB = "ai_lead_qualify"
MONGO_COLLECTION = "call_transcripts"

CALLBACK_API_URL = os.getenv(
    "CALLBACK_API_URL",
    "http://192.168.8.67:8000/leads/ai-lead-qualify/callback",
)

CALLBACK_UPDATE_API_URL = os.getenv(
    "CALLBACK_UPDATE_API_URL",
    "http://192.168.8.67:8000/leads/ai-lead-qualify/callback-update",
)

GEMINI_API_KEY = os.getenv("GEMINI_ANALYSIS_API_KEY")

# JIRA-AIP-799: hot lead flow (business leads pitch + b2b follow-up) is
# temporarily disabled. Flip to True to re-enable it — the gated code paths
# in analysis.py (and bot.py's HOT_LEAD_FLOW_ENABLED) already contain the
# full flow.
HOT_LEAD_FLOW_ENABLED = False

POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "60"))
BATCH_LIMIT = int(os.getenv("BATCH_LIMIT", "50"))

LOG_DIR = os.path.join(
    os.environ.get("BOT_LOG_DIR", "/home/yogeshv_10011835/voicebot_nodcode_platform/logs/"),
    "analysis_logs",
)
