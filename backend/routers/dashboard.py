from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from ..auth import require_user
from ..db import bots, transcripts

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

MIN_CALLS_FOR_RANKING = 5


@router.get("/summary")
def dashboard_summary(_: dict = Depends(require_user)) -> dict:
    """Global landing-page dashboard: aggregate stats across all non-deleted bots, computed
    with a handful of batch Mongo aggregations (not N+1 per bot) — same style as
    bots._list_bots_by_status()."""
    bot_docs = list(bots.find({"status": {"$ne": "deleted"}}, {"active_version_id": 1, "draft_version_id": 1, "name": 1}))
    bot_ids = [str(b["_id"]) for b in bot_docs]
    total_agents = len(bot_docs)

    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    today_end = today_start + timedelta(days=1)

    total_calls_all_time = 0
    total_seconds_all_time = 0.0
    calls_today = 0
    if bot_ids:
        for row in transcripts.aggregate([
            {"$match": {"bot_id": {"$in": bot_ids}}},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "total_duration_sec": {"$sum": {"$ifNull": ["$call_duration_sec", 0]}},
                "calls_today": {
                    "$sum": {
                        "$cond": [
                            {"$and": [
                                {"$gte": ["$created_at", today_start]},
                                {"$lt": ["$created_at", today_end]},
                            ]},
                            1,
                            0,
                        ]
                    }
                },
            }},
        ]):
            total_calls_all_time = row.get("total", 0)
            total_seconds_all_time = row.get("total_duration_sec", 0) or 0
            calls_today = row.get("calls_today", 0)

    # Per-bot call_count + success_rate_pct (same "status == 'completed'" definition used by
    # bots.bot_metrics), so best/least performing can be ranked among bots with >= 5 calls.
    per_bot_stats: dict[str, dict] = {}
    if bot_ids:
        for row in transcripts.aggregate([
            {"$match": {"bot_id": {"$in": bot_ids}}},
            {"$group": {
                "_id": "$bot_id",
                "count": {"$sum": 1},
                "completed": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
            }},
        ]):
            count = row["count"]
            completed = row.get("completed", 0)
            per_bot_stats[row["_id"]] = {
                "call_count": count,
                "success_rate_pct": round((completed / count) * 100, 1) if count else 0.0,
            }

    name_by_bot_id = {str(b["_id"]): b.get("name", "") for b in bot_docs}
    qualifying = [
        {"bot_id": bid, "name": name_by_bot_id.get(bid, ""), **stats}
        for bid, stats in per_bot_stats.items()
        if stats["call_count"] >= MIN_CALLS_FOR_RANKING
    ]
    best_performing_bot = None
    least_performing_bot = None
    if len(qualifying) >= 2:
        ranked = sorted(qualifying, key=lambda b: b["success_rate_pct"], reverse=True)
        best_performing_bot = ranked[0]
        least_performing_bot = ranked[-1]

    # Daily volume across all bots, last 14 days (inclusive of today).
    window_start = today_start - timedelta(days=13)
    daily_by_date: dict[str, int] = {}
    if bot_ids:
        for row in transcripts.aggregate([
            {"$match": {"bot_id": {"$in": bot_ids}, "created_at": {"$gte": window_start}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "count": {"$sum": 1},
            }},
        ]):
            daily_by_date[row["_id"]] = row["count"]

    daily_volume = []
    for i in range(14):
        d = (window_start + timedelta(days=i)).date().isoformat()
        daily_volume.append({"date": d, "count": daily_by_date.get(d, 0)})

    return {
        "total_agents": total_agents,
        "total_calls_all_time": total_calls_all_time,
        "total_minutes_all_time": round(total_seconds_all_time / 60, 1),
        "calls_today": calls_today,
        "best_performing_bot": best_performing_bot,
        "least_performing_bot": least_performing_bot,
        "daily_volume": daily_volume,
    }
