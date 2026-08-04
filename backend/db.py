import logging
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

_log = logging.getLogger("voicebot_admin.db")

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
PLATFORM_DB = os.getenv("VOICEBOT_PLATFORM_DB", "ai_voice_bot_management")
# NOTE: ai_lead_qualify is a separate live production DB — do not read/write it from this
# codebase. Call transcripts live in tbl_ai_vb_call_transcripts in PLATFORM_DB instead.

# Opt-in local demo mode: the real Mongo (MONGO_URI) lives on Justdial's internal network
# and is unreachable off-VPN. Set USE_INMEMORY_DB=true to run the admin API against an
# in-process mongomock instance instead, so the UI is fully clickable without VPN access.
# Off by default — production/dev-on-VPN behavior is completely unchanged.
if os.getenv("USE_INMEMORY_DB", "").lower() == "true":
    import mongomock

    MongoClient = mongomock.MongoClient
else:
    from pymongo import MongoClient

_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")),
    # A VPN drop/reconnect leaves pooled sockets half-open — without these, pymongo can
    # sit on a dead socket well past what serverSelectionTimeoutMS implies (that timeout
    # only bounds picking a server, not a stuck read/write on an already-selected one).
    connectTimeoutMS=int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "5000")),
    socketTimeoutMS=int(os.getenv("MONGO_SOCKET_TIMEOUT_MS", "10000")),
    heartbeatFrequencyMS=int(os.getenv("MONGO_HEARTBEAT_FREQUENCY_MS", "5000")),
)
db = _client[PLATFORM_DB]

users = db["tbl_ai_vb_users"]
bots = db["tbl_ai_vb_bots"]
bot_versions = db["tbl_ai_vb_bot_versions"]
platform_settings = db["tbl_ai_vb_platform_settings"]
campaigns = db["tbl_ai_vb_campaigns"]
language_settings = db["tbl_ai_vb_language_settings"]
phone_numbers = db["tbl_ai_vb_phone_numbers"]
# The inbound numbers available to map, seeded from the SIP trunk sheet. Deliberately
# separate from phone_numbers above.
sip_dispatch_rules = db["tbl_ai_vb_sip_dispatch_rules"]
# number -> agent mapping. Unique index on phone_number enforces one-number-one-agent.
agent_number_mapping = db["tbl_ai_vb_agent_number_mapping"]
transcripts = db["tbl_ai_vb_call_transcripts"]
audit_log = db["tbl_ai_vb_audit_log"]
campaign_leads = db["tbl_ai_vb_campaign_leads"]
campaign_leads.create_index([("campaign_id", 1), ("phone_number", 1)])
call_logs = db["tbl_ai_vb_call_logs"]
call_jobs = db["tbl_ai_vb_call_jobs"]
# Matches the claim-query predicate in campaign_execution.claim_next_job (campaign_id +
# status), not just an insertion-order index — see plans/03 Day 2.5 checklist.
call_jobs.create_index([("campaign_id", 1), ("status", 1)])
call_jobs.create_index([("phone_number", 1), ("status", 1)])
dialer_webhook_secrets = db["tbl_ai_vb_dialer_webhook_secrets"]
# Per-bot user-configured API calls (pre-call data fetches + in-call LLM tools). Read at
# call time by the runtime (agent_resolver.py) directly from this collection.
custom_functions = db["tbl_ai_vb_custom_functions"]


def _watchdog_loop(interval_s: float) -> None:
    # A VPN drop/reconnect can leave the pool's sockets half-open without pymongo
    # noticing on its own — every collection above shares the one `_client` instance, so
    # closing it here (not replacing it) is enough: `_client.close()` just drops the
    # pooled sockets, and the next query anywhere in the app transparently reconnects
    # through the same client/db/collection objects. This replaces the manual
    # "kill the backend process and restart it" step we were doing by hand.
    consecutive_failures = 0
    while True:
        time.sleep(interval_s)
        try:
            _client.admin.command("ping")
            if consecutive_failures:
                _log.info("[DB WATCHDOG] Mongo ping recovered after %d failed check(s).", consecutive_failures)
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            _log.warning("[DB WATCHDOG] Mongo ping failed (%d in a row): %s — recycling connection pool.", consecutive_failures, exc)
            try:
                _client.close()
            except Exception as close_exc:
                _log.warning("[DB WATCHDOG] _client.close() raised during recycle (non-fatal): %s", close_exc)


def start_db_watchdog(interval_s: float = None) -> None:
    """Starts a background thread that periodically pings Mongo and recycles the
    connection pool on failure, so a VPN blip self-heals instead of needing a manual
    backend restart. Safe to call once at app startup; a bare `import backend.db` (e.g.
    from tests or scripts) does not start it."""
    interval = interval_s if interval_s is not None else float(os.getenv("MONGO_WATCHDOG_INTERVAL_S", "15"))
    t = threading.Thread(target=_watchdog_loop, args=(interval,), daemon=True, name="mongo-watchdog")
    t.start()
