"""Read-only surface for bot.record_fallback_event()'s Mongo trail — every call that ran on
_HARDCODED_BOT_CONFIG instead of the dashboard-configured bot, with the specific reason
(bot.py's FALLBACK_REASON_* constants), which worker handled it, and when.

Existed only as scattered log lines across whichever worker process happened to answer a
given call until this was added — impossible to tell "did this happen once tonight or is it
a recurring pattern" without SSHing into every worker and grepping. This makes it one
queryable list."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from ..auth import require_user
from ..db import db

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

fallback_events = db["tbl_ai_vb_bot_config_fallback_events"]
worker_heartbeats = db["tbl_ai_vb_worker_heartbeats"]
dispatch_failures = db["tbl_ai_vb_dispatch_failures"]

# A worker upserts its heartbeat every WORKER_HEARTBEAT_INTERVAL_S (bot.py, default 10s) —
# 3x that interval gives one missed beat of slack before flagging "stale" (crashed /
# network-partitioned) rather than reacting to a single slow tick.
_STALE_AFTER_S = 30


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    created = doc.get("created_at")
    if hasattr(created, "isoformat"):
        doc["created_at"] = created.isoformat()
    return doc


@router.get("/fallback-events")
def list_fallback_events(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    _: dict = Depends(require_user),
) -> dict:
    total = fallback_events.count_documents({})
    docs = fallback_events.find({}).sort("created_at", -1).skip(offset).limit(limit)
    return {"items": [_serialize(d) for d in docs], "total": total}


@router.get("/worker-health")
def list_worker_health(_: dict = Depends(require_user)) -> list[dict]:
    """One row per agent_name that has ever registered a heartbeat (bot.py's
    start_worker_heartbeat), so a crashed/partitioned worker shows up here — red, with how
    long it's been silent — before anyone has to place a real call to discover it."""
    now = datetime.now(timezone.utc)
    out = []
    for doc in worker_heartbeats.find({}).sort("agent_name", 1):
        last_seen = doc.get("last_seen")
        # pymongo returns naive UTC datetimes by default (no tzinfo attached), while `now`
        # is tz-aware — subtracting them directly raises TypeError. bot.py writes last_seen
        # with datetime.now(timezone.utc), so it's always actually UTC; just attach the tz.
        if last_seen is not None and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        age_s = (now - last_seen).total_seconds() if last_seen is not None else None
        out.append({
            "agent_name": doc.get("agent_name", ""),
            "pid": doc.get("pid"),
            "host": doc.get("host", ""),
            "last_seen": last_seen.isoformat() if hasattr(last_seen, "isoformat") else last_seen,
            "started_at": doc["started_at"].isoformat() if hasattr(doc.get("started_at"), "isoformat") else doc.get("started_at"),
            "age_seconds": round(age_s) if age_s is not None else None,
            "stale": age_s is None or age_s > _STALE_AFTER_S,
        })
    return out


@router.get("/dispatch-failures")
def list_dispatch_failures(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    _: dict = Depends(require_user),
) -> dict:
    """Every test-call dispatch LiveKit accepted but never actually handed to any
    registered worker within backend/routers/testcall.py's _DISPATCH_ASSIGN_TIMEOUT_S —
    distinct from fallback-events (a call ran, but on the wrong config) and worker-health
    (a worker process is down): this is "a worker was up and reachable, and the call still
    never got assigned to it," the exact blind spot from the Jul 29 incident."""
    total = dispatch_failures.count_documents({})
    docs = dispatch_failures.find({}).sort("created_at", -1).skip(offset).limit(limit)
    return {"items": [_serialize(d) for d in docs], "total": total}
