from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from typing import Any

from loguru import logger

from .config import CALL_EVENT_COLLECTION
from .mongo import get_db
from .serialization import serialize_doc


VALID_SEVERITIES = {"info", "warning", "error", "success"}
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="call-events")


def _clean_context(context: dict[str, Any] | None) -> dict[str, Any]:
    context = context or {}
    return {
        "call_id": context.get("call_id") or "",
        "room_name": context.get("room_name") or "",
        "assistant_id": context.get("assistant_id") or "",
        "bot_id": context.get("bot_id") or "",
        "bot_version_id": context.get("bot_version_id") or "",
        "campaign_id": context.get("campaign_id") or "",
        "lead_id": context.get("lead_id") or "",
    }


def build_call_event_doc(
    event_type: str,
    severity: str,
    message: str,
    context: dict[str, Any] | None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_type = (event_type or "").strip()
    if not event_type:
        raise ValueError("event_type is required")
    severity = (severity or "info").strip().lower()
    if severity not in VALID_SEVERITIES:
        severity = "info"
    return {
        **_clean_context(context),
        "event_type": event_type,
        "severity": severity,
        "message": (message or event_type).strip() or event_type,
        "details": deepcopy(details or {}),
        "created_at": datetime.utcnow(),
    }


def insert_call_event(
    event_type: str,
    severity: str,
    message: str,
    context: dict[str, Any] | None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Synchronous insert used by tests and the background writer."""
    doc = build_call_event_doc(event_type, severity, message, context, details)
    try:
        inserted_id = get_db()[CALL_EVENT_COLLECTION].insert_one(doc).inserted_id
        doc["_id"] = inserted_id
        return serialize_doc(doc)
    except Exception as exc:
        logger.warning(f"[CALL_EVENT] insert failed for {event_type!r}: {exc}")
        return None


def record_call_event(
    event_type: str,
    severity: str,
    message: str,
    context: dict[str, Any] | None,
    details: dict[str, Any] | None = None,
) -> Future:
    """Fire-and-forget call timeline event.

    The returned Future is useful in tests, but production callers should not
    wait on it. Mongo failures are swallowed inside insert_call_event.
    """
    return _EXECUTOR.submit(insert_call_event, event_type, severity, message, context, details)


def _date_filter(start_date: str | None, end_date: str | None) -> dict[str, datetime]:
    query: dict[str, datetime] = {}
    if start_date:
        query["$gte"] = datetime.fromisoformat(start_date.replace("Z", "+00:00")).replace(tzinfo=None)
    if end_date:
        query["$lte"] = datetime.fromisoformat(end_date.replace("Z", "+00:00")).replace(tzinfo=None)
    return query


def call_event_query(filters: dict[str, Any]) -> dict[str, Any]:
    query: dict[str, Any] = {}
    for key in ("call_id", "room_name", "bot_id", "campaign_id", "lead_id", "event_type", "severity"):
        value = filters.get(key)
        if value:
            query[key] = value
    created_filter = _date_filter(filters.get("start_date"), filters.get("end_date"))
    if created_filter:
        query["created_at"] = created_filter
    return query


def search_call_events(
    filters: dict[str, Any],
    *,
    limit: int = 100,
    skip: int = 0,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    skip = max(0, skip)
    cursor = (
        get_db()[CALL_EVENT_COLLECTION]
        .find(call_event_query(filters))
        .sort("created_at", 1)
        .skip(skip)
        .limit(limit)
    )
    return serialize_doc(list(cursor))


def events_for_transcript(transcript: dict[str, Any], *, limit: int = 200) -> list[dict[str, Any]]:
    call_id = transcript.get("call_id") or ""
    room_name = transcript.get("room_name") or ""
    if not call_id and not room_name:
        return []
    query: dict[str, Any]
    if call_id and room_name:
        query = {"$or": [{"call_id": call_id}, {"room_name": room_name}]}
    elif call_id:
        query = {"call_id": call_id}
    else:
        query = {"room_name": room_name}
    cursor = get_db()[CALL_EVENT_COLLECTION].find(query).sort("created_at", 1).limit(max(1, min(limit, 500)))
    return serialize_doc(list(cursor))
