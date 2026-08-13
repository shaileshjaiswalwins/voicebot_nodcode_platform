from datetime import datetime, timedelta

from .db import transcripts

# Calls in these states did not fail/drop — same definition used by dashboard.py's
# task-completion rate and by bots.bot_metrics.
NON_FAILURE_STATUSES = {"completed", "not_interested"}


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(pct / 100 * (len(sorted_values) - 1))))
    return sorted_values[idx]


def _base_match(
    bot_ids: list[str] | None,
    window_start: datetime,
    window_end: datetime,
    status: str | None = None,
    call_outcome: str | None = None,
) -> dict:
    match: dict = {"created_at": {"$gte": window_start, "$lt": window_end}}
    if bot_ids is not None:
        match["bot_id"] = {"$in": bot_ids}
    # AlertRuleFilters.status/call_outcome (backend/models.py) — a rule-level narrowing of
    # which calls count toward the metric at all, applied uniformly ahead of whatever the
    # metric itself already computes (e.g. task_completion_rate_pct's own status-based
    # numerator is unaffected; this just shrinks the population both numerator and
    # denominator are drawn from). Query-level filter, not a change to any metric's math.
    if status:
        match["status"] = status
    if call_outcome:
        match["analysis.call_outcome"] = call_outcome
    return match


def compute_metric(
    metric: str,
    bot_ids: list[str] | None,
    window_start: datetime,
    window_end: datetime,
    error_type: str | None = None,
    status: str | None = None,
    call_outcome: str | None = None,
) -> float:
    """Computes one alertable metric over [window_start, window_end) for the given bots
    (None = no bot restriction, i.e. admin/platform-wide). This is the shared aggregation
    logic behind both `dashboard.py::exec_metrics()`'s per-pillar numbers and the alert
    worker's rule evaluation — see the plan's explicit note that this is a real refactor
    of exec_metrics(), not a lift-and-shift: exec_metrics() used one combined aggregation
    for several pillars at once; this deliberately re-runs a scoped query per metric so
    the same function serves both a fixed 7-day dashboard window and an arbitrary
    rule-defined window without carrying dashboard-only fields (active_workflows etc)."""
    match = _base_match(bot_ids, window_start, window_end, status, call_outcome)

    if metric == "call_count":
        return float(transcripts.count_documents(match))

    if metric == "task_completion_rate_pct":
        total = transcripts.count_documents(match)
        if not total:
            return 0.0
        completed = transcripts.count_documents({**match, "status": {"$in": list(NON_FAILURE_STATUSES)}})
        return round(completed / total * 100, 1)

    if metric == "platform_error_rate_pct":
        total = transcripts.count_documents(match)
        if not total:
            return 0.0
        dropped = transcripts.count_documents({
            **match,
            "$or": [
                {"status": "disconnected"},
                {"session_errors.0": {"$exists": True}},
            ],
        })
        return round(dropped / total * 100, 1)

    if metric == "session_error_count":
        m = dict(match)
        if error_type:
            m["session_errors"] = {"$elemMatch": {"type": error_type}}
        else:
            m["session_errors.0"] = {"$exists": True}
        return float(transcripts.count_documents(m))

    if metric == "p95_turn_latency_ms":
        all_latencies: list[float] = []
        for doc in transcripts.find(match, {"response_latencies_ms": 1}):
            turns = doc.get("response_latencies_ms") or []
            all_latencies.extend(t for t in turns if isinstance(t, (int, float)))
        all_latencies.sort()
        return round(_percentile(all_latencies, 95), 0)

    if metric == "concurrency_used":
        # `created_at` is end-time in most entrypoints (see the plan's note on
        # workflow_engine.py/bot.py/bot_pipeline.py/bot_dev_param.py save sites), so the
        # correct call interval is [created_at - call_duration_sec, created_at], not
        # [created_at, created_at + duration] — an approximation, not exact concurrency,
        # since a minority of entrypoints (bot.py:2160, bot_dev_param.py:791) instead use
        # the real call-start timestamp when available.
        events: list[tuple[datetime, int]] = []
        for doc in transcripts.find(match, {"created_at": 1, "call_duration_sec": 1}):
            end = doc.get("created_at")
            if end is None:
                continue
            duration = doc.get("call_duration_sec") or 0
            start = end - timedelta(seconds=duration)
            events.append((start, 1))
            events.append((end, -1))
        # Process interval-closes at a given instant before interval-opens at that same
        # instant, so a call ending exactly when another begins doesn't get double-counted.
        events.sort(key=lambda e: (e[0], e[1]))
        current = 0
        peak = 0
        for _, delta in events:
            current += delta
            peak = max(peak, current)
        return float(peak)

    raise ValueError(f"Unknown or unsupported alert metric: {metric!r}")
