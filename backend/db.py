import os
from pathlib import Path

from dotenv import load_dotenv

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

_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")))
db = _client[PLATFORM_DB]

users = db["tbl_ai_vb_users"]
bots = db["tbl_ai_vb_bots"]
bot_versions = db["tbl_ai_vb_bot_versions"]
platform_settings = db["tbl_ai_vb_platform_settings"]
campaigns = db["tbl_ai_vb_campaigns"]
language_settings = db["tbl_ai_vb_language_settings"]
phone_numbers = db["tbl_ai_vb_phone_numbers"]
transcripts = db["tbl_ai_vb_call_transcripts"]
audit_log = db["tbl_ai_vb_audit_log"]
campaign_leads = db["tbl_ai_vb_campaign_leads"]
campaign_leads.create_index([("campaign_id", 1), ("phone_number", 1)])
call_logs = db["tbl_ai_vb_call_logs"]
call_jobs = db["tbl_ai_vb_call_jobs"]
# Matches the claim-query predicate in campaign_execution.claim_next_job (campaign_id +
# status), not just an insertion-order index — see plans/03 Day 2.5 checklist.
call_jobs.create_index([("campaign_id", 1), ("status", 1)])
