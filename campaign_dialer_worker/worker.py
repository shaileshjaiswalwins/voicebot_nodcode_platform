"""Long-running campaign dialer worker — polls active campaigns for queued call_jobs,
pushes each lead to TSPL's outbound-dialer API, and marks the job `dialing` while TSPL's
async completion callback (backend/routers/dialer_webhooks.py) is awaited.

Mirrors callback_worker/worker.py's shape (claim/lease loop, loguru rotating file, graceful
shutdown) — adapted from "one global collection, single claim" to "one claim loop per active
campaign," since campaign_execution.claim_next_job is scoped to a single campaign_key.

⚠️ Running this against the real TSPL dev endpoint (see dialer_client.DIALER_PUSH_API_URL)
causes TSPL to actually dial a real phone number behind whatever jduid is in a queued lead.
Only run this deliberately against campaigns you intend to actually dial.
"""

import asyncio
import os
import signal
from datetime import datetime, timedelta, timezone

import aiohttp
from loguru import logger

from backend import campaign_execution
from backend.db import campaign_leads, campaigns
from dialer_client import build_dialer_payload, push_lead_to_dialer

from .config import (
    BATCH_LIMIT_PER_CAMPAIGN,
    LOG_DIR,
    MAX_PUSH_ATTEMPTS,
    POLL_INTERVAL_SEC,
    STALE_DIALING_TIMEOUT_MIN,
)

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
    logger.info("[DIALER-WORKER] Shutdown signal received — finishing current batch then exiting")
    _stop.set()


async def _push_one_job(campaign_key: str, campaign: dict, http_session: aiohttp.ClientSession) -> bool:
    """Claim one queued job for this campaign and push it to TSPL. Returns False when there
    was nothing to claim (caller should stop looping for this campaign this tick)."""
    loop = asyncio.get_running_loop()
    job = await loop.run_in_executor(None, lambda: campaign_execution.claim_next_job(campaign_key))
    if job is None:
        return False

    lead = await loop.run_in_executor(None, lambda: campaign_leads.find_one({"_id": job["lead_id"]}))
    if not lead:
        logger.warning(f"[DIALER-WORKER] job {job['_id']} has no matching lead — reverting to queued")
        await loop.run_in_executor(None, lambda: campaign_execution.revert_to_queued(job["_id"]))
        return True

    # TSPL requires ref_obj._id to be unique per push attempt — reusing the job's own _id
    # across retries gets every attempt after the first rejected as a "Duplicate Lead".
    push_ref = await loop.run_in_executor(None, lambda: campaign_execution.mint_push_ref(job["_id"]))
    payload = build_dialer_payload(lead, campaign, push_ref)
    ok, response = await push_lead_to_dialer(payload, http_session)
    if ok:
        await loop.run_in_executor(None, lambda: campaign_execution.mark_job_dialing(job["_id"], response))
        logger.info(f"[DIALER-WORKER] job {job['_id']} pushed | campaign={campaign_key} | jduid={lead.get('jduid')!r}")
    else:
        reason = str(response.get("body") or response.get("error") or "unknown push failure")
        new_status = await loop.run_in_executor(
            None, lambda: campaign_execution.revert_to_queued_or_fail(job["_id"], reason, MAX_PUSH_ATTEMPTS)
        )
        logger.warning(f"[DIALER-WORKER] job {job['_id']} push failed -> {new_status} | campaign={campaign_key} | reason={reason!r}")
    return True


async def _promote_scheduled_campaigns(loop: asyncio.AbstractEventLoop) -> int:
    """Flip any "scheduled" campaign whose scheduled_at has arrived to "active", so this
    tick's active_campaigns query below picks it up immediately — no separate scheduler
    process, this worker's own poll loop is the clock."""
    now = datetime.now(timezone.utc)

    def _promote() -> int:
        result = campaigns.update_many(
            {"status": "scheduled", "scheduled_at": {"$lte": now}},
            {"$set": {"status": "active"}},
        )
        return result.modified_count

    return await loop.run_in_executor(None, _promote)


async def _tick(http_session: aiohttp.ClientSession) -> None:
    loop = asyncio.get_running_loop()
    promoted = await _promote_scheduled_campaigns(loop)
    if promoted:
        logger.info(f"[DIALER-WORKER] promoted {promoted} scheduled campaign(s) to active")

    active_campaigns = await loop.run_in_executor(
        None, lambda: list(campaigns.find({"status": "active"}))
    )
    stale_timeout = timedelta(minutes=STALE_DIALING_TIMEOUT_MIN)
    for campaign in active_campaigns:
        if _stop.is_set():
            break
        campaign_key = campaign.get("campaign_key")
        if not campaign_key:
            continue

        reclaimed = await loop.run_in_executor(
            None, lambda: campaign_execution.reclaim_stale_dialing(campaign_key, stale_timeout)
        )
        if reclaimed:
            logger.warning(f"[DIALER-WORKER] campaign={campaign_key} reclaimed {reclaimed} stale dialing job(s) as failed")

        pushed = 0
        for _ in range(BATCH_LIMIT_PER_CAMPAIGN):
            if _stop.is_set():
                break
            claimed_and_handled = await _push_one_job(campaign_key, campaign, http_session)
            if not claimed_and_handled:
                break
            pushed += 1
        if pushed:
            logger.info(f"[DIALER-WORKER] campaign={campaign_key} — {pushed} job(s) handled this tick")


async def main() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    logger.info(
        f"[DIALER-WORKER] Starting | poll={POLL_INTERVAL_SEC}s | batch_per_campaign={BATCH_LIMIT_PER_CAMPAIGN} | "
        f"stale_dialing_timeout={STALE_DIALING_TIMEOUT_MIN}min"
    )

    async with aiohttp.ClientSession() as http_session:
        while not _stop.is_set():
            try:
                await _tick(http_session)
            except Exception as e:
                logger.exception(f"[DIALER-WORKER] Tick error: {e}")
            try:
                await asyncio.wait_for(_stop.wait(), timeout=POLL_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass

    logger.info("[DIALER-WORKER] Stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
