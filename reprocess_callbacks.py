"""One-time script: re-analyse misclassified leads and send corrected callbacks."""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bson import ObjectId
from pymongo import MongoClient
import aiohttp
from loguru import logger
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from callback_worker.config import MONGO_URI, MONGO_DB, MONGO_COLLECTION
from callback_worker.analysis import (
    generate_call_analysis,
    generate_b2b_score,
    fallback_analysis,
    DISPOSITION_MAP,
    status_to_outcome,
)
from callback_worker.callback import send_callback_update

# All leads that were incorrectly classified (these are lead_ids, not _ids)
LEAD_IDS: list[str] = [
    "6a1eda7a4866a90ff9285624",  # Office Container — analysis was null (N/A); job seeker "जॉब से रिलेटेड" → Not Interested
]


async def reprocess_doc(doc: dict, http_session: aiohttp.ClientSession, collection) -> None:
    doc_id = str(doc["_id"])
    lead_id = doc.get("lead_id")

    if not lead_id or str(lead_id).startswith("fallback_"):
        logger.warning(f"[REPROCESS] Skipping {doc_id} — no usable lead_id")
        return

    schema = (doc.get("lead_record") or {}).get("qualification_schema", {}) or {}
    status = doc.get("status", "completed")
    transcript = doc.get("transcript") or []
    muted_transcript = doc.get("muted_transcript") or []
    gemini_connect_failed = bool(doc.get("gemini_connect_failed"))
    duration_secs = doc.get("call_duration_sec")
    greeting_done = doc.get("greeting_done", True)
    user_speech_ms = int(doc.get("user_speech_ms") or 0)
    wrong_opener_detected = bool(doc.get("wrong_opener_detected"))

    try:
        analysis, b2b_score = await asyncio.gather(
            generate_call_analysis(
                transcript,
                status,
                schema,
                http_session,
                muted_transcript=muted_transcript,
                gemini_connect_failed=gemini_connect_failed,
                duration_secs=duration_secs,
                greeting_done=greeting_done,
                user_speech_ms=user_speech_ms,
                wrong_opener_detected=wrong_opener_detected,
            ),
            generate_b2b_score(transcript, http_session),
        )
    except Exception as e:
        logger.error(f"[REPROCESS] Analysis failed for {doc_id}: {e}")
        analysis = fallback_analysis(status)
        b2b_score = {"deal_value": "", "lead_intent_score": "", "urgency_flag": "no"}

    new_outcome = analysis.get("call_outcome", status_to_outcome(status))
    logger.info(
        f"[REPROCESS] {doc_id} | lead_id={lead_id} | "
        f"dur={duration_secs}s | new_outcome={new_outcome!r}"
    )

    updates = {
        "call_outcome": new_outcome,
        "call_outcome_desc": analysis.get(
            "call_outcome_description", DISPOSITION_MAP.get(new_outcome, "")
        ),
        "call_summary": analysis.get("call_summary", ""),
        "is_business": analysis.get("is_business", ""),
        "business_name": analysis.get("business_name", ""),
        "business_city": analysis.get("business_city", ""),
        "deal_value": (b2b_score or {}).get("deal_value", ""),
        "lead_intent_score": (b2b_score or {}).get("lead_intent_score", ""),
        "urgency_flag": (b2b_score or {}).get("urgency_flag", "no"),
    }

    ok = await send_callback_update(doc_id, str(lead_id), updates, http_session)
    if ok:
        logger.info(f"[REPROCESS] ✓ Corrected callback sent for {doc_id}")
        saved_analysis = {
            "call_outcome": analysis.get("call_outcome", ""),
            "call_outcome_description": analysis.get("call_outcome_description", ""),
            "call_summary": analysis.get("call_summary", ""),
            "is_business": analysis.get("is_business", ""),
            "business_name": analysis.get("business_name", ""),
            "business_city": analysis.get("business_city", ""),
            "qna": analysis.get("qna") or [],
            "product_change": analysis.get("product_change") or {},
            "rescheduled_to": analysis.get("rescheduled_to", "") or "",
            "deal_value": (b2b_score or {}).get("deal_value", ""),
            "lead_intent_score": (b2b_score or {}).get("lead_intent_score", ""),
            "urgency_flag": (b2b_score or {}).get("urgency_flag", "no"),
        }
        collection.update_one(
            {"_id": doc["_id"]},
            {"$set": {"analysis": saved_analysis, "reprocessed_at": datetime.now(timezone.utc)}},
        )
        logger.info(f"[REPROCESS] ✓ MongoDB analysis field updated for {doc_id}")
    else:
        logger.error(f"[REPROCESS] ✗ Failed to send update for {doc_id}")


async def main() -> None:
    client = MongoClient(MONGO_URI)
    collection = client[MONGO_DB][MONGO_COLLECTION]

    async with aiohttp.ClientSession() as http_session:
        for lead_id in LEAD_IDS:
            doc = collection.find_one({"lead_id": lead_id}) or \
                  collection.find_one({"lead_id": ObjectId(lead_id)})
            if not doc:
                logger.warning(f"[REPROCESS] No doc found for lead_id={lead_id}")
                continue
            await reprocess_doc(doc, http_session, collection)

    client.close()
    logger.info("[REPROCESS] Done")


if __name__ == "__main__":
    asyncio.run(main())
