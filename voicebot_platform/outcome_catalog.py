"""Editable call-outcome catalog.

The 17 disposition labels (Approved, Enriched, Wrong Number, etc.) used to live
as a Python dict (DISPOSITION_MAP) inside callback_worker/analysis.py. The keys
are referenced by other callback logic (callback.py:48 upgrade rules), so the
keys themselves stay immutable here — PMs can only edit the descriptions that
the LLM rubric sees and the display labels shown in the dashboard.

A small TTL cache keeps per-call reads cheap.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from .config import DEFAULT_USER
from .mongo import get_db
from .serialization import serialize_doc

OUTCOME_COLLECTION = "tbl_ai_vb_outcome_catalog"

# Source of truth for the 17 outcome keys. PMs may not delete/add keys (they're
# wired into downstream callback logic) but may edit descriptions and display
# labels. Order here is the canonical ordering used in the prompt rubric.
DEFAULT_OUTCOMES: list[dict[str, str]] = [
    {"key": "Short Hangup", "description": "The call ended with no product discussion — the customer said nothing at all, OR gave only a bare call-acknowledgment (e.g. hello, haan, hold on, ek second) and disconnected before any product topic was raised."},
    {"key": "Voicemail", "description": "The call went to the recipient's voicemail instead of connecting directly."},
    {"key": "Wrong Number", "description": "The number dialed does not belong to the intended customer."},
    {"key": "Approved", "description": "The customer confirmed the product and answered ALL specification questions."},
    {"key": "Enriched", "description": "The customer confirmed the product and answered at least one (but not all) specification questions."},
    {"key": "Interested", "description": "The customer confirmed they need the product but answered ZERO specification questions, OR showed clear positive interest (engaged meaningfully, asked follow-up questions, showed enthusiasm) without answering any spec questions. Covers both explicit product confirmation with zero specs and positive-but-unconfirmed engagement."},
    {"key": "Not Interested", "description": "The customer clearly stated they are not interested or do not need the product."},
    {"key": "Could Not Confirm", "description": "The customer was uncertain or did not confirm whether they still need the product — includes vague/non-committal responses, mid-conversation disconnections where no product confirmation was obtained, and cases where the call dropped before any meaningful product exchange."},
    {"key": "Alternate Number", "description": "The customer provided a different or alternate contact number."},
    {"key": "Already Spoken", "description": "The customer has already discussed or interacted about the requirement with JD or the seller, OR the customer's requirement has already been fulfilled."},
    {"key": "Will do it Myself", "description": "The customer still has the requirement but will source/handle it themselves without JD's help — they explicitly declined seller connections (e.g. 'मैं खुद देख लूँगा', 'I'll manage it myself'). The need exists; only JD's assistance is rejected. Distinct from Not Interested."},
    {"key": "Call Rescheduled", "description": "The customer asked to call at a specific date and time."},
    {"key": "Seller Intent", "description": "The caller is a seller or vendor trying to offer their own products/services — they are NOT a buyer with a requirement. They may want to list on JustDial or pitch their business. This is the opposite of a buyer lead."},
    {"key": "Abusive Lead", "description": "The recipient exhibited abusive or inappropriate behavior during the call."},
    {"key": "DNC Client : Don't Call Further", "description": "The customer explicitly requested not to be contacted again."},
    {"key": "Other Cases", "description": "The call outcome does not fit into any predefined categories."},
    {"key": "Technical Issue - Call Connected", "description": "The call connected but was disrupted by technical issues."},
    {"key": "Language Issue", "description": "Communication was not possible due to a language mismatch."},
]

_CACHE_TTL_SEC = 60
_cache_lock = threading.Lock()
_cached: tuple[float, dict[str, str], list[dict[str, Any]]] | None = None


def _now() -> datetime:
    return datetime.utcnow()


def _invalidate() -> None:
    global _cached
    with _cache_lock:
        _cached = None


def seed_default_outcomes(user: str = DEFAULT_USER) -> None:
    """Idempotent — inserts any outcome keys that are missing."""
    db = get_db()
    coll = db[OUTCOME_COLLECTION]
    now = _now()
    for idx, entry in enumerate(DEFAULT_OUTCOMES):
        coll.update_one(
            {"key": entry["key"]},
            {
                "$setOnInsert": {
                    "key": entry["key"],
                    "description": entry["description"],
                    "display_label": entry["key"],
                    "order": idx,
                    "created_at": now,
                    "created_by": user,
                    "updated_at": now,
                    "updated_by": user,
                }
            },
            upsert=True,
        )
    _invalidate()


def list_outcomes() -> list[dict[str, Any]]:
    docs = list(get_db()[OUTCOME_COLLECTION].find().sort("order", 1))
    return serialize_doc(docs)


def update_outcome(key: str, payload: dict[str, Any], user: str) -> dict[str, Any]:
    db = get_db()
    coll = db[OUTCOME_COLLECTION]
    existing = coll.find_one({"key": key})
    if not existing:
        raise KeyError("outcome_not_found")
    update: dict[str, Any] = {"updated_by": user, "updated_at": _now()}
    if "description" in payload:
        description = (payload.get("description") or "").strip()
        if not description:
            raise ValueError("description cannot be empty")
        if len(description) > 2000:
            raise ValueError("description must be 2000 characters or fewer")
        update["description"] = description
    if "display_label" in payload:
        display = (payload.get("display_label") or "").strip()
        if not display:
            raise ValueError("display_label cannot be empty")
        if len(display) > 200:
            raise ValueError("display_label must be 200 characters or fewer")
        update["display_label"] = display
    coll.update_one({"key": key}, {"$set": update})
    _invalidate()
    return serialize_doc(coll.find_one({"key": key}))


def get_disposition_map() -> dict[str, str]:
    """Cached read used by the LLM prompt builder. Returns the same shape as
    the old hardcoded DISPOSITION_MAP — a dict of key -> description.
    Falls back to defaults on Mongo failure so analysis never crashes.
    """
    global _cached
    with _cache_lock:
        if _cached and (time.monotonic() - _cached[0]) < _CACHE_TTL_SEC:
            return _cached[1]
    try:
        docs = list(get_db()[OUTCOME_COLLECTION].find().sort("order", 1))
    except Exception:
        docs = []
    if not docs:
        mapping = {entry["key"]: entry["description"] for entry in DEFAULT_OUTCOMES}
        raw = list(DEFAULT_OUTCOMES)
    else:
        mapping = {d["key"]: d.get("description", "") for d in docs if d.get("key")}
        raw = serialize_doc(docs)
    with _cache_lock:
        _cached = (time.monotonic(), mapping, raw)
    return mapping
