import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root (parent of this package) — same pattern as callback_worker.
# Mongo connectivity itself is NOT duplicated here: this worker imports backend.campaign_execution
# / backend.db directly (they build their own MongoClient against PLATFORM_DB), so there's a
# single source of truth for how campaigns/call_jobs are reached — unlike callback_worker,
# which talks to a different, separate DB (ai_lead_qualify) and rightly owns its own client.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

DIALER_PUSH_API_URL = os.getenv(
    "DIALER_PUSH_API_URL", "http://mis-dev.internal:3006/leads/ai-lead-qualify/save"
)

POLL_INTERVAL_SEC = int(os.getenv("CAMPAIGN_DIALER_POLL_INTERVAL_SEC", "30"))
BATCH_LIMIT_PER_CAMPAIGN = int(os.getenv("CAMPAIGN_DIALER_BATCH_LIMIT", "10"))
STALE_DIALING_TIMEOUT_MIN = int(os.getenv("CAMPAIGN_DIALER_STALE_DIALING_TIMEOUT_MIN", "45"))
# A push that keeps failing (bad dialer_config, TSPL rejecting the payload, etc.) would
# otherwise retry every tick forever with the failure invisible to a PM — cap it so the job
# lands in a visible "push_failed" state instead.
MAX_PUSH_ATTEMPTS = int(os.getenv("CAMPAIGN_DIALER_MAX_PUSH_ATTEMPTS", "5"))

LOG_DIR = os.path.join(
    os.environ.get("BOT_LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")),
    "campaign_dialer_logs",
)
