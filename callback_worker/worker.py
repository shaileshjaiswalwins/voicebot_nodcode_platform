"""Long-running callback worker — polls MongoDB for untagged transcripts, runs analysis, sends callback."""

import asyncio
import signal
from datetime import datetime

import aiohttp
from loguru import logger
from pymongo import ASCENDING

from voicebot_platform.mongo import get_client
from voicebot_platform.observability import recorder as _observability
from voicebot_platform.call_events import record_call_event

from .analysis import fallback_analysis, generate_b2b_score, generate_call_analysis
from .callback import CALLBACK_API_URL, build_callback_payload, send_callback
from .config import BATCH_LIMIT, MONGO_COLLECTION, MONGO_DB, POLL_INTERVAL_SEC
from .recording import quality_flags, verify_transcript_from_recording

_stop = asyncio.Event()


def _handle_signal(*_):
    logger.info("[WORKER] Shutdown signal received — finishing current batch then exiting")
    _stop.set()


async def _process_doc(doc: dict, collection, http_session: aiohttp.ClientSession) -> None:
    lead_id = doc.get("lead_id")
    doc_id = doc["_id"]
    loop = asyncio.get_running_loop()
    event_context = {
        "call_id": doc.get("call_id", ""),
        "room_name": doc.get("room_name", ""),
        "assistant_id": doc.get("assistant_id", ""),
        "bot_id": doc.get("bot_id", ""),
        "bot_version_id": doc.get("bot_version_id", ""),
        "campaign_id": doc.get("campaign_id", ""),
        "lead_id": lead_id,
    }

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

    doc = await verify_transcript_from_recording(doc, collection, http_session, event_context)
    transcript = doc.get("verified_transcript") or doc.get("transcript") or []
    analysis_transcript_source = doc.get("analysis_transcript_source") or (
        "recording_verified" if doc.get("verified_transcript") else "gemini_live"
    )
    muted_transcript = doc.get("muted_transcript") or []
    duration_secs = doc.get("call_duration_sec")
    try:
        record_call_event(
            "analysis_started",
            "info",
            "Post-call analysis started",
            event_context,
            {
                "doc_id": str(doc_id),
                "transcript_count": len(transcript),
                "analysis_transcript_source": analysis_transcript_source,
            },
        )
        analysis, b2b_score = await asyncio.gather(
            generate_call_analysis(
                transcript,
                status,
                schema,
                http_session,
                muted_transcript=muted_transcript,
                gemini_connect_failed=bool(doc.get("gemini_connect_failed")),
                duration_secs=duration_secs,
                transcript_source=analysis_transcript_source,
            ),
            generate_b2b_score(transcript, http_session),
        )
        analysis["analysis_transcript_source"] = analysis_transcript_source
        flags = quality_flags(doc, transcript)
        if flags:
            analysis["quality_flags"] = flags
        record_call_event(
            "analysis_succeeded",
            "success",
            "Post-call analysis succeeded",
            event_context,
            {
                "doc_id": str(doc_id),
                "outcome": analysis.get("call_outcome", ""),
                "confidence": analysis.get("confidence", ""),
                "quality_flags": analysis.get("quality_flags", []),
                "analysis_transcript_source": analysis_transcript_source,
            },
        )
    except Exception as e:
        logger.warning(f"[WORKER] Analysis failed for doc {doc_id}: {e} — using fallback")
        record_call_event(
            "analysis_failed",
            "error",
            "Post-call analysis failed; fallback analysis used",
            event_context,
            {"doc_id": str(doc_id), "error_type": type(e).__name__, "error": str(e)},
        )
        analysis = fallback_analysis(status)
        b2b_score = {"deal_value": "", "lead_intent_score": "", "urgency_flag": "no"}

    payload = build_callback_payload(doc, analysis, b2b_score)
    ok = await send_callback(payload, http_session, CALLBACK_API_URL, event_context=event_context)

    if ok:
        await loop.run_in_executor(None, lambda: collection.update_one(
            {"_id": doc_id},
            {"$set": {
                "tagged": True,
                "tagged_at": datetime.utcnow(),
                "analysis_result": analysis,
                "b2b_score": b2b_score,
                "transcript_quality_flags": analysis.get("quality_flags", quality_flags(doc, transcript)),
                "analysis_transcript_source": analysis_transcript_source,
            }},
        ))
        logger.info(f"[WORKER] Tagged doc {doc_id} | lead_id={lead_id!r}")
        record_call_event(
            "transcript_tagged",
            "success",
            "Transcript tagged after callback delivery",
            event_context,
            {"doc_id": str(doc_id)},
        )
        _observability.event(
            "callback_sent",
            {
                "doc_id": str(doc_id),
                "lead_id": lead_id,
                "call_id": doc.get("call_id", ""),
                "bot_id": doc.get("bot_id", ""),
                "bot_version_id": doc.get("bot_version_id", ""),
                "campaign_id": doc.get("campaign_id", ""),
            },
        )
    else:
        logger.warning(f"[WORKER] Callback failed for doc {doc_id} | lead_id={lead_id!r} — will retry next tick")
        _observability.event(
            "callback_failed",
            {
                "doc_id": str(doc_id),
                "lead_id": lead_id,
                "call_id": doc.get("call_id", ""),
                "bot_id": doc.get("bot_id", ""),
                "bot_version_id": doc.get("bot_version_id", ""),
                "campaign_id": doc.get("campaign_id", ""),
            },
        )


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

    logger.info(
        f"[WORKER] Starting | mongo=<configured> | db={MONGO_DB} | "
        f"collection={MONGO_COLLECTION} | poll={POLL_INTERVAL_SEC}s | batch={BATCH_LIMIT}"
    )

    client = get_client()
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

    # Shared client is owned by voicebot_platform.mongo — leave it open for any
    # in-process consumers; PyMongo cleans up on interpreter shutdown.
    logger.info("[WORKER] Stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
