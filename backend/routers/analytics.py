from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from ..auth import require_user
from ..db import transcripts

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _date_filter(bot_id: str, campaign_id: str, hours: float, start_date: str, end_date: str) -> dict:
    query: dict = {}
    if bot_id:
        query["bot_id"] = bot_id
    if campaign_id:
        query["campaign_id"] = campaign_id
    if hours:
        query["created_at"] = {"$gte": datetime.now(timezone.utc) - timedelta(hours=hours)}
    elif start_date or end_date:
        date_filter = {}
        if start_date:
            date_filter["$gte"] = datetime.fromisoformat(start_date)
        if end_date:
            date_filter["$lte"] = datetime.fromisoformat(end_date) + timedelta(days=1)
        query["created_at"] = date_filter
    return query


@router.get("/outcomes")
def outcome_analytics(
    bot_id: str = Query(""),
    campaign_id: str = Query(""),
    hours: float = Query(0),
    start_date: str = Query(""),
    end_date: str = Query(""),
    _: dict = Depends(require_user),
) -> dict:
    query = _date_filter(bot_id, campaign_id, hours, start_date, end_date)
    # Project only the fields this endpoint actually reads — the full transcript/call_events
    # arrays on each doc are the bulk of a call record and aren't needed for these aggregates.
    docs = list(
        transcripts.find(
            query,
            {"status": 1, "analysis": 1, "call_end_reason": 1, "call_duration_sec": 1},
        )
    )

    by_status: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    ended_naturally = 0
    total_duration = 0
    duration_count = 0

    for doc in docs:
        status = doc.get("status") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1

        outcome = (doc.get("analysis") or {}).get("call_outcome") or "unclassified"
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1

        if doc.get("call_end_reason") == "natural" or status == "completed":
            ended_naturally += 1

        duration = doc.get("call_duration_sec")
        if isinstance(duration, (int, float)):
            total_duration += duration
            duration_count += 1

    return {
        "by_status": by_status,
        "by_outcome": by_outcome,
        "total": len(docs),
        "ended_naturally": ended_naturally,
        "avg_duration_sec": round(total_duration / duration_count, 1) if duration_count else 0,
    }


@router.get("/quality-alerts")
def quality_alerts(hours: float = Query(1), threshold_pct: float = Query(30), _: dict = Depends(require_user)) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    docs = list(
        transcripts.find(
            {"created_at": {"$gte": since}},
            {"transcript_quality_flags": 1, "status": 1},
        )
    )
    total = len(docs)
    bad = sum(1 for d in docs if d.get("transcript_quality_flags") or d.get("status") not in (None, "completed"))
    bad_pct = round((bad / total) * 100, 1) if total else 0
    alert = total > 0 and bad_pct >= threshold_pct
    message = (
        f"{bad_pct}% of the last {hours}h of calls flagged for quality issues"
        if alert
        else "Call quality within normal range"
    )
    return {
        "alert": alert,
        "message": message,
        "hours": hours,
        "total_calls": total,
        "bad_calls": bad,
        "bad_pct": bad_pct,
    }
