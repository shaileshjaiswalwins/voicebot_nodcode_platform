"""Standalone alert-evaluation worker for Part 1: Custom Alerting.

Mirrors campaign_dialer_worker/worker.py's shape (claim loop, loguru rotating file,
graceful shutdown) — per the plan, this is the exact template to follow, adapted from
"claim a queued call_job" to "claim a due alert_rule."

Each tick (~60s):
  1. Atomically claim every enabled rule whose next_eval_at has arrived, stamping a fresh
     next_eval_at = now + frequency in the same find_one_and_update — the same atomic-claim
     idiom backend/campaign_execution.py and bots.py's publish
     endpoint use, so two overlapping worker instances (e.g. mid-deploy) can never both
     claim and double-evaluate the same rule.
  2. For each claimed rule: re-resolve the creator's bot scope fresh (never trust a stored
     value), compute the metric over the rule's window, compare against the threshold, and
     open/resolve an AlertIncident per the plan's lifecycle rules.
"""

import asyncio
import os
import signal
from datetime import datetime, timedelta, timezone

from loguru import logger

from pydantic import ValidationError

from backend.auth import resolve_owned_bot_ids
from backend.db import alert_incidents, alert_rules, users
from backend.metrics import compute_metric
from backend.models import AlertIncident, AlertRule

from .config import LOG_DIR, POLL_INTERVAL_SEC

# Rules with a malformed frequency/window can never be scheduled correctly, so once one
# fails to parse we fall back to this fixed interval to advance next_eval_at anyway — the
# exact value doesn't matter for correctness (the rule is disabled so it won't be claimed
# again), it just needs to be a valid duration.
_MALFORMED_RULE_FALLBACK_SEC = POLL_INTERVAL_SEC

os.makedirs(LOG_DIR, exist_ok=True)
logger.add(
    os.path.join(LOG_DIR, "{time:YYYY-MM-DD}.log"),
    rotation="00:00",
    retention="30 days",
    compression="gz",
    level="INFO",
    enqueue=True,
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {message}\n",
)

_stop = asyncio.Event()


def _handle_signal(*_):
    logger.info("[ALERT-WORKER] Shutdown signal received — finishing current tick then exiting")
    _stop.set()


# Window/frequency compatibility table (plan, "Window / frequency compatibility") stores
# both as short codes on the rule (e.g. "5m", "1h") — this worker only needs their
# second-durations, the compatibility itself is enforced by the CRUD router, not here.
_UNIT_SECONDS = {"m": 60, "h": 3600}


def _duration_to_seconds(code: str) -> int:
    """"5m" -> 300, "1h" -> 3600, etc."""
    unit = code[-1]
    value = int(code[:-1])
    return value * _UNIT_SECONDS[unit]


def _claim_due_rules(now: datetime) -> list[dict]:
    """Atomically claims every rule due for evaluation right now. Uses a plain loop of
    find_one_and_update calls (one per due rule) rather than update_many, because each
    rule's new next_eval_at depends on its own frequency — update_many can't compute a
    per-document $set value from that document's own fields in one operation without an
    aggregation pipeline update, and the simple per-document claim is exactly the same
    idiom bots.py's publish endpoint and campaign_execution.py's job-claim already use."""
    claimed: list[dict] = []
    while True:
        due = alert_rules.find_one(
            {"enabled": True, "next_eval_at": {"$lte": now}},
            sort=[("next_eval_at", 1)],
        )
        if not due:
            break
        try:
            frequency_sec = _duration_to_seconds(due["frequency"])
            update = {"$set": {"next_eval_at": now + timedelta(seconds=frequency_sec), "last_evaluated_at": now}}
        except Exception:
            # Malformed frequency/window on this document: it can never be scheduled
            # normally. If we let the exception escape here, this rule's next_eval_at
            # never advances — and since the due-rule query sorts by next_eval_at
            # ascending, this same broken rule would be reclaimed first on every future
            # tick forever, starving every other rule in the system. Instead: log once
            # loudly (not routine per-tick noise), disable the rule so a human has to
            # re-enable it, and still push next_eval_at forward so it drops out of the
            # due-rule query immediately.
            logger.error(
                f"[ALERT-WORKER] rule={due['_id']} has a malformed frequency "
                f"({due.get('frequency')!r}) — disabling this rule to avoid starving "
                f"all other rules; it will NOT be evaluated until fixed and re-enabled."
            )
            update = {
                "$set": {
                    "enabled": False,
                    "next_eval_at": now + timedelta(seconds=_MALFORMED_RULE_FALLBACK_SEC),
                    "last_evaluated_at": now,
                }
            }
        # Atomic claim: only the request that actually finds+advances this exact
        # next_eval_at wins — a second concurrent worker's filter no longer matches once
        # the first has advanced it, so the same rule can never be double-claimed this tick.
        result = alert_rules.find_one_and_update(
            {"_id": due["_id"], "next_eval_at": due["next_eval_at"]},
            update,
            return_document=True,
        )
        if result and result.get("enabled", True):
            claimed.append(result)
        # If the claim lost the race, loop again — some other worker got it; move on to
        # the next due rule rather than retrying the same one indefinitely.

    return claimed


def _resolve_rule_bot_ids(rule: dict) -> list[str] | None:
    """Re-resolves the creator's current role from `users` at eval time (never trust a
    stale value on the rule/token), then intersects with the rule's own bot_ids filter —
    empty filter means "all owned," per AlertRuleFilters' docstring."""
    creator_email = rule["created_by"]
    user_doc = users.find_one({"email": creator_email})
    role = (user_doc or {}).get("role", "user")
    owned = resolve_owned_bot_ids(creator_email, role)

    filter_bot_ids = (rule.get("filters") or {}).get("bot_ids") or []
    if not filter_bot_ids:
        return owned  # None (admin, unrestricted) or the creator's owned bots
    if owned is None:
        return filter_bot_ids  # admin creator, rule scoped to a specific bot subset
    return [b for b in filter_bot_ids if b in set(owned)]


_COMPARATORS = {
    "gt": lambda current, threshold: current > threshold,
    "lt": lambda current, threshold: current < threshold,
    "ge": lambda current, threshold: current >= threshold,
    "le": lambda current, threshold: current <= threshold,
}


def _evaluate_rule(rule: dict, now: datetime) -> None:
    rule_id = str(rule["_id"])

    # The write side (CRUD router) validates every rule through AlertRuleCreate/Update
    # before it's persisted, but nothing guarantees a document read back here still
    # conforms — it may have been written by an older version of the code, hand-edited,
    # or corrupted. Re-validate through the same AlertRule model here, at the point where
    # we move from "I have a claimed rule dict" to "I'm about to actually evaluate it," so
    # a malformed document produces one clear log line and a skipped tick instead of a
    # KeyError/TypeError surfacing deep inside metric computation. _id is an ObjectId on
    # the raw Mongo doc, so it's stringified before parsing to satisfy AlertRule.id: str.
    try:
        AlertRule.model_validate({**rule, "_id": str(rule["_id"])})
    except ValidationError as e:
        logger.error(
            f"[ALERT-WORKER] rule={rule_id} failed schema validation — skipping evaluation "
            f"this tick (document does not conform to the current AlertRule model): {e}"
        )
        return

    bot_ids = _resolve_rule_bot_ids(rule)
    if bot_ids is not None and not bot_ids:
        # Non-admin creator who currently owns no matching bots (e.g. bots reassigned
        # since the rule was created) — nothing to evaluate, and definitely nothing to
        # breach against zero calls counted as zero, which would be misleading.
        logger.info(f"[ALERT-WORKER] rule={rule_id} — no bots in scope, skipping this tick")
        return

    window_sec = _duration_to_seconds(rule["window"])
    window_start = now - timedelta(seconds=window_sec)

    filters = rule.get("filters") or {}
    current_value = compute_metric(
        rule["metric"],
        bot_ids,
        window_start,
        now,
        status=filters.get("status"),
        call_outcome=filters.get("call_outcome"),
    )

    breached = _COMPARATORS[rule["comparator"]](current_value, rule["threshold_value"])
    open_incident = alert_incidents.find_one({"rule_id": rule_id, "status": "open"})

    if breached and not open_incident:
        new_incident = {
            "rule_id": rule_id,
            "rule_name": rule.get("name", ""),
            "bot_ids": bot_ids or [],
            "metric": rule["metric"],
            "current_value": current_value,
            "threshold_value": rule["threshold_value"],
            "status": "open",
            "triggered_at": now,
            "resolved_at": None,
        }
        # Actually construct + validate the model before writing — a placeholder _id is
        # substituted purely to satisfy AlertIncident.id (Mongo assigns the real one on
        # insert), so this exercises _check_status_resolved_at_pairing for real instead of
        # only declaring the invariant. model_dump(by_alias=True) is then stripped of the
        # placeholder id so insert_one still lets Mongo generate the real _id.
        validated = AlertIncident.model_validate({**new_incident, "_id": "pending"})
        to_insert = validated.model_dump(by_alias=True, exclude={"id"})
        alert_incidents.insert_one(to_insert)
        logger.info(
            f"[ALERT-WORKER] rule={rule_id} name={rule.get('name')!r} BREACH opened | "
            f"metric={rule['metric']} value={current_value} comparator={rule['comparator']} "
            f"threshold={rule['threshold_value']}"
        )
    elif not breached and open_incident:
        resolve_set = {"status": "resolved", "resolved_at": now, "current_value": current_value}
        # Validate the document as it will look POST-update — triggers
        # _check_status_resolved_at_pairing against the real resulting shape without
        # changing the atomicity of the actual write (we still issue the same
        # optimistic-filter update_one below, keyed on {"_id": ..., "status": "open"},
        # as before).
        AlertIncident.model_validate({**open_incident, **resolve_set, "_id": str(open_incident["_id"])})
        alert_incidents.update_one(
            {"_id": open_incident["_id"], "status": "open"},
            {"$set": resolve_set},
        )
        logger.info(f"[ALERT-WORKER] rule={rule_id} name={rule.get('name')!r} incident RESOLVED | value={current_value}")
    elif breached and open_incident:
        # Still breached — no new incident, no repeat notification, per the plan's "fires
        # once on open, once on resolve." Keep current_value fresh so the History view
        # reflects the latest reading while the incident stays open.
        alert_incidents.update_one(
            {"_id": open_incident["_id"], "status": "open"},
            {"$set": {"current_value": current_value}},
        )
    # else: not breached, no open incident — nothing to do.


async def _tick() -> None:
    loop = asyncio.get_running_loop()
    now = datetime.now(timezone.utc)
    due_rules = await loop.run_in_executor(None, lambda: _claim_due_rules(now))
    if not due_rules:
        return
    logger.info(f"[ALERT-WORKER] claimed {len(due_rules)} due rule(s)")
    for rule in due_rules:
        if _stop.is_set():
            break
        try:
            await loop.run_in_executor(None, lambda r=rule: _evaluate_rule(r, now))
        except Exception as e:
            logger.exception(f"[ALERT-WORKER] rule={rule.get('_id')} evaluation error: {e}")


async def main() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    logger.info(f"[ALERT-WORKER] Starting | poll={POLL_INTERVAL_SEC}s")

    while not _stop.is_set():
        try:
            await _tick()
        except Exception as e:
            logger.exception(f"[ALERT-WORKER] Tick error: {e}")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=POLL_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass

    logger.info("[ALERT-WORKER] Stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
