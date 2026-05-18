"""Long-running callback worker — polls MongoDB for untagged transcripts, runs analysis, sends callback."""

import asyncio
import signal
from datetime import datetime

import aiohttp
from loguru import logger
from pymongo import ASCENDING, MongoClient

from .analysis import fallback_analysis, generate_b2b_score, generate_call_analysis
from .callback import CALLBACK_API_URL, build_callback_payload, send_callback
from .config import BATCH_LIMIT, MONGO_COLLECTION, MONGO_DB, MONGO_URI, POLL_INTERVAL_SEC

_stop = asyncio.Event()


def _handle_signal(*_):
    logger.info("[WORKER] Shutdown signal received — finishing current batch then exiting")
    _stop.set()


async def _process_doc(doc: dict, collection, http_session: aiohttp.ClientSession) -> None:
    lead_id = doc.get("lead_id")
    doc_id = doc["_id"]
    loop = asyncio.get_running_loop()

    if not lead_id:
        logger.warning(f"[WORKER] Skipping doc {doc_id} — no lead_id")
        await loop.run_in_executor(None, lambda: collection.update_one(
            {"_id": doc_id},
            {"$set": {"tagged": True, "tagged_at": datetime.utcnow(), "skipped_reason": "no_lead_id"}},
        ))
        return

    if str(lead_id).startswith("fallback_"):
        logger.warning(f"[WORKER] Skipping doc {doc_id} — fallback lead_id={lead_id!r}")
        await loop.run_in_executor(None, lambda: collection.update_one(
            {"_id": doc_id},
            {"$set": {"tagged": True, "tagged_at": datetime.utcnow(), "skipped_reason": "fallback_lead_id"}},
        ))
        return

    schema = (doc.get("lead_record") or {}).get("qualification_schema", {}) or {}
    status = doc.get("status", "completed")

    transcript = doc.get("transcript") or []
    muted_transcript = doc.get("muted_transcript") or []
    gemini_connect_failed = bool(doc.get("gemini_connect_failed"))
    try:
        analysis, b2b_score = await asyncio.gather(
            generate_call_analysis(transcript, status, schema, http_session, muted_transcript=muted_transcript, gemini_connect_failed=gemini_connect_failed),
            generate_b2b_score(transcript, http_session),
        )
    except Exception as e:
        logger.warning(f"[WORKER] Analysis failed for doc {doc_id}: {e} — using fallback")
        analysis = fallback_analysis(status)
        b2b_score = {"deal_value": "", "lead_intent_score": "", "urgency_flag": "no"}

    payload = build_callback_payload(doc, analysis, b2b_score)
    ok = await send_callback(payload, http_session, CALLBACK_API_URL)

    if ok:
        await loop.run_in_executor(None, lambda: collection.update_one(
            {"_id": doc_id},
            {"$set": {"tagged": True, "tagged_at": datetime.utcnow()}},
        ))
        logger.info(f"[WORKER] Tagged doc {doc_id} | lead_id={lead_id!r}")
    else:
        logger.warning(f"[WORKER] Callback failed for doc {doc_id} | lead_id={lead_id!r} — will retry next tick")


async def _tick(collection, http_session: aiohttp.ClientSession) -> None:
    loop = asyncio.get_running_loop()
    docs = await loop.run_in_executor(None, lambda: list(collection.find({"tagged": False}).limit(BATCH_LIMIT)))
    if not docs:
        return
    logger.info(f"[WORKER] Processing {len(docs)} untagged doc(s)")
    for doc in docs:
        if _stop.is_set():
            break
        await _process_doc(doc, collection, http_session)


async def main() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    logger.info(f"[WORKER] Starting | mongo={MONGO_URI} | db={MONGO_DB} | collection={MONGO_COLLECTION} | poll={POLL_INTERVAL_SEC}s | batch={BATCH_LIMIT}")

    client = MongoClient(MONGO_URI)
    collection = client[MONGO_DB][MONGO_COLLECTION]

    await loop.run_in_executor(None, lambda: collection.create_index(
        [("tagged", ASCENDING), ("created_at", ASCENDING)],
        background=True,
    ))

    async with aiohttp.ClientSession() as http_session:
        while not _stop.is_set():
            try:
                await _tick(collection, http_session)
            except Exception as e:
                logger.exception(f"[WORKER] Tick error: {e}")
            try:
                await asyncio.wait_for(_stop.wait(), timeout=POLL_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass

    client.close()
    logger.info("[WORKER] Stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
