"""
Fetch today's connected anomaly leads for manual analysis.

Pulls all call_transcripts for today, joins with MIS, finds leads where
MIS old outcome != stored MongoDB analysis outcome, and writes to JSON
so Claude can analyze without reprocessing via Gemini.

Usage:
    .venv/bin/python fetch_anomalies_debug.py
    .venv/bin/python fetch_anomalies_debug.py --date 2026-06-24
    .venv/bin/python fetch_anomalies_debug.py --limit 20
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp
from bson import ObjectId
from dotenv import load_dotenv
from loguru import logger
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parent / ".env")

from callback_worker.config import MONGO_URI, MONGO_DB, MONGO_COLLECTION

IST = timezone(timedelta(hours=5, minutes=30))
MIS_LEADS_URL = "http://192.168.8.67:8000/leads/ai-lead-qualify/mis"
MIS_PAGE_SIZE = 100


def get_date_label(date_str):
    return date_str if date_str else datetime.now(IST).strftime("%Y-%m-%d")


def get_day_range(date_str):
    if date_str:
        day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=IST)
    else:
        day = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


async def fetch_mis_leads(date_label, session, limit=0):
    records_all = []
    total = None
    for page in range(1, 101):
        params = {
            "lead_id": "", "ref_id": "", "jduid": "", "city": "",
            "ai_partner": "inh-suny-bot", "disposition_type": "latest",
            "disposition": "", "ncatid": "", "whatsapp_flag": "", "medium": "",
            "flow": "all", "page_name": "", "fromdate": date_label,
            "todate": date_label, "page": str(page), "limit": str(MIS_PAGE_SIZE),
            "blf": "", "chk_mis": "gen",
        }
        async with session.get(MIS_LEADS_URL, params=params,
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
            data = await resp.json()
        res = data.get("results", {})
        records = res.get("data", [])
        if total is None:
            total = int(res.get("total_counts", 0) or 0)
        if not records:
            break
        records_all.extend(records)
        if limit and len(records_all) >= limit:
            records_all = records_all[:limit]
            break
        if total and len(records_all) >= total:
            break
    return records_all


def fmt_transcript(transcript, muted_transcript):
    lines = []
    for t in (transcript or []):
        role = (t.get("role") or "?").upper()[:9]
        text = (t.get("text") or "").strip()
        if text:
            lines.append(f"[{role}] {text}")
    if muted_transcript:
        muted = [m for m in muted_transcript if (m or "").strip()]
        if muted:
            lines.append("[MUTED] " + " | ".join(muted))
    return "\n".join(lines) if lines else ""


async def main(args):
    date_label = get_date_label(args.date)
    start_utc, end_utc = get_day_range(args.date)

    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    collection = mongo_client[MONGO_DB][MONGO_COLLECTION]

    # Fetch all MongoDB docs for today
    logger.info(f"Querying MongoDB for {date_label} ...")
    mongo_docs_list = list(collection.find({
        "created_at": {"$gte": start_utc, "$lt": end_utc}
    }))
    logger.info(f"  MongoDB docs: {len(mongo_docs_list)}")

    # Index by lead_id str
    mongo_by_lid = {}
    for doc in mongo_docs_list:
        lid = str(doc.get("lead_id", ""))
        if lid:
            mongo_by_lid[lid] = doc

    # Fetch MIS leads
    async with aiohttp.ClientSession() as session:
        mis_records = await fetch_mis_leads(date_label, session, limit=args.limit)
    mongo_client.close()

    logger.info(f"  MIS records: {len(mis_records)}")

    # Build anomaly list
    anomalies = []
    for rec in mis_records:
        lead_id = str(rec.get("_id") or "")
        if not lead_id or lead_id.startswith("fallback_"):
            continue

        mis_callback = rec.get("callback") or {}
        mis_old_outcome = (mis_callback.get("call_outcome") or "").strip()
        if not mis_old_outcome:
            mis_old_outcome = "No Disp"

        doc = mongo_by_lid.get(lead_id)
        if not doc:
            continue  # not connected — skip

        stored_analysis = doc.get("analysis") or {}
        stored_outcome = (stored_analysis.get("call_outcome") or "").strip()

        if mis_old_outcome == stored_outcome or not stored_outcome:
            continue  # no anomaly

        transcript = doc.get("transcript") or []
        muted_transcript = doc.get("muted_transcript") or []
        transcript_text = fmt_transcript(transcript, muted_transcript)

        anomalies.append({
            "lead_id": lead_id,
            "catname": rec.get("catname", ""),
            "mis_call_status": rec.get("call_status", ""),
            "old_outcome": mis_old_outcome,          # MIS (source of truth)
            "stored_outcome": stored_outcome,        # what Gemini said at call time
            "stored_summary": stored_analysis.get("call_summary", ""),
            "stored_description": stored_analysis.get("call_outcome_description", ""),
            "transcript": transcript_text,
            "call_duration_sec": doc.get("call_duration_sec"),
            "user_speech_ms": doc.get("user_speech_ms"),
            "greeting_done": doc.get("greeting_done"),
            "gemini_connect_failed": doc.get("gemini_connect_failed"),
            "wrong_opener_detected": doc.get("wrong_opener_detected"),
            "status": doc.get("status"),
            "qna": stored_analysis.get("qna", []),
        })

    logger.info(f"  Anomalies (MIS ≠ stored): {len(anomalies)}")

    # Sort by transition for easier grouping
    anomalies.sort(key=lambda x: (x["old_outcome"], x["stored_outcome"], x["lead_id"]))

    out_path = Path(f"anomalies_debug_{date_label}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(anomalies, f, ensure_ascii=False, indent=2, default=str)

    print(f"\nSaved {len(anomalies)} anomalies → {out_path}")

    # Print transition summary
    from collections import Counter
    ctr = Counter(f"{a['old_outcome']} → {a['stored_outcome']}" for a in anomalies)
    print("\nTransition breakdown:")
    for pair, count in ctr.most_common(20):
        print(f"  {count:>4}x  {pair}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None)
    p.add_argument("--limit", type=int, default=0)
    asyncio.run(main(p.parse_args()))
