from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from ..auth import bot_owner_filter, owned_bot_ids, require_user
from ..db import bots, campaigns, transcripts
from ..metrics import NON_FAILURE_STATUSES, compute_metric

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

MIN_CALLS_FOR_RANKING = 5

# NON_FAILURE_STATUSES now lives in backend/metrics.py (imported above) so this dashboard
# and compute_metric()'s task_completion_rate_pct can never drift on what "completed" means.
# Free-text `analysis.call_outcome` values that represent a definitively negative/no-signal
# result. Everything else (positive or specific business outcomes) counts as "qualified" for
# the conversion-yield proxy — this errs toward inclusion since the outcome taxonomy is
# free-text and bot-specific, not a fixed enum.
NEGATIVE_OUTCOMES = {"not_interested", "unclassified", "rejected", "no_answer"}


@router.get("/summary")
def dashboard_summary(user: dict = Depends(require_user)) -> dict:
    """Landing-page dashboard: aggregate stats across all non-deleted bots the caller can
    see (every bot for an admin, only their own otherwise), computed with a handful of
    batch Mongo aggregations (not N+1 per bot) — same style as bots._list_bots_by_status()."""
    bot_docs = list(bots.find(
        {"status": {"$ne": "deleted"}, **bot_owner_filter(user)},
        {"active_version_id": 1, "draft_version_id": 1, "name": 1},
    ))
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


@router.get("/exec-metrics")
def exec_metrics(user: dict = Depends(require_user)) -> dict:
    """Executive dashboard metrics: North Star (QLCH) + 3 pillars, built only from fields the
    call pipeline actually populates today. Cost and escalation-based metrics are deliberately
    excluded — cost_inr is never written by any caller and this platform's "transfer" action
    ends the call rather than performing a real handoff, so neither has real data behind it.
    Scoped to the caller's own bots unless they're an admin, same as dashboard_summary above.
    """
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    owned = owned_bot_ids(user)
    date_match: dict = {"created_at": {"$gte": week_start}}
    if owned is not None:
        date_match["bot_id"] = {"$in": owned}

    # total_calls/task_completion/platform_error come from the shared compute_metric() so
    # this dashboard and the alert worker can never drift on what these numbers mean —
    # see backend/metrics.py. qualified_hours/conversion_yield/avg latencies aren't
    # alertable metrics (per the alerting plan's V1 metric set) and stay computed locally.
    total_calls = int(compute_metric("call_count", owned, week_start, now))
    task_completion_rate_pct = compute_metric("task_completion_rate_pct", owned, week_start, now)
    platform_error_rate_pct = compute_metric("platform_error_rate_pct", owned, week_start, now)
    p95_turn_latency_ms = compute_metric("p95_turn_latency_ms", owned, week_start, now)

    negative_outcomes = list(NEGATIVE_OUTCOMES)
    agg = list(transcripts.aggregate([
        {"$match": date_match},
        {"$group": {
            "_id": None,
            "qualified_seconds": {
                "$sum": {
                    "$cond": [
                        {"$in": ["$status", list(NON_FAILURE_STATUSES)]},
                        {"$ifNull": ["$call_duration_sec", 0]},
                        0,
                    ]
                }
            },
            "positive_outcome_calls": {
                "$sum": {
                    "$cond": [
                        {"$not": [{"$in": [{"$ifNull": ["$analysis.call_outcome", "unclassified"]}, negative_outcomes]}]},
                        1,
                        0,
                    ]
                }
            },
        }},
    ]))
    row = agg[0] if agg else {}
    qualified_hours = round((row.get("qualified_seconds", 0) or 0) / 3600, 2)
    conversion_yield_pct = (
        round(row.get("positive_outcome_calls", 0) / total_calls * 100, 1) if total_calls else 0.0
    )

    # First-turn/mid-call latency averages: needs the raw per-turn arrays, which Mongo's
    # aggregation percentile operators handle awkwardly across a ragged array-of-arrays, so
    # this pulls only the one field it needs, bounded to the last 7 days.
    first_turn_latencies: list[float] = []
    mid_call_latencies: list[float] = []
    for doc in transcripts.find(
        date_match,
        {"response_latencies_ms": 1},
    ):
        turns = doc.get("response_latencies_ms") or []
        valid = [t for t in turns if isinstance(t, (int, float))]
        if not valid:
            continue
        first_turn_latencies.append(valid[0])
        mid_call_latencies.extend(valid[1:])

    avg_first_response_latency_ms = round(sum(first_turn_latencies) / len(first_turn_latencies), 0) if first_turn_latencies else 0
    avg_mid_call_latency_ms = round(sum(mid_call_latencies) / len(mid_call_latencies), 0) if mid_call_latencies else 0

    if owned is None:
        campaign_filter: dict = {"status": "active"}
    else:
        campaign_filter = {
            "status": "active",
            "$or": [
                {"bot_id": {"$in": owned}},
                {"bot_id": {"$exists": False}, "created_by": user.get("sub")},
            ],
        }
    active_workflows = bots.count_documents(
        {"status": "active", **bot_owner_filter(user)}
    ) + campaigns.count_documents(campaign_filter)

    return {
        "window_days": 7,
        "north_star": {
            "qualified_live_call_hours": qualified_hours,
            "total_calls": total_calls,
        },
        "business_value": {
            "task_completion_rate_pct": task_completion_rate_pct,
            "conversion_yield_pct": conversion_yield_pct,
        },
        "system_quality": {
            "p95_turn_latency_ms": p95_turn_latency_ms,
            "avg_first_response_latency_ms": avg_first_response_latency_ms,
            "avg_mid_call_latency_ms": avg_mid_call_latency_ms,
            "platform_error_rate_pct": platform_error_rate_pct,
        },
        "platform_adoption": {
            "active_production_workflows": active_workflows,
        },
    }
