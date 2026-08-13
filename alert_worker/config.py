import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root (parent of this package) — same pattern as
# campaign_dialer_worker/config.py. This worker imports backend.db / backend.auth /
# backend.metrics directly (they build their own MongoClient against PLATFORM_DB), so
# there's a single source of truth for how alert_rules/alert_incidents/transcripts/users
# are reached.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

# Matches Retell's own "checks for due rules every minute" FAQ answer — see the plan.
POLL_INTERVAL_SEC = int(os.getenv("ALERT_WORKER_POLL_INTERVAL_SEC", "60"))

LOG_DIR = os.path.join(
    os.environ.get("BOT_LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")),
    "alert_worker_logs",
)
