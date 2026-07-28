"""Read-only surface for bot.record_fallback_event()'s Mongo trail — every call that ran on
_HARDCODED_BOT_CONFIG instead of the dashboard-configured bot, with the specific reason
(bot.py's FALLBACK_REASON_* constants), which worker handled it, and when.

Existed only as scattered log lines across whichever worker process happened to answer a
given call until this was added — impossible to tell "did this happen once tonight or is it
a recurring pattern" without SSHing into every worker and grepping. This makes it one
queryable list."""
from fastapi import APIRouter, Depends, Query

from ..auth import require_user
from ..db import db

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

fallback_events = db["tbl_ai_vb_bot_config_fallback_events"]


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
