"""
Fetch logs + MongoDB transcript for a list of lead IDs and save to JSON.

Usage:
    .venv/bin/python fetch_lead_debug.py <lead_id1> <lead_id2> ...
    .venv/bin/python fetch_lead_debug.py                          # reads LEAD_IDS list below

Output: lead_debug_<timestamp>.json
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parent / ".env")

from callback_worker.config import MONGO_URI, MONGO_DB, MONGO_COLLECTION

# ── Paste lead IDs here when not passing via CLI ──────────────────────────────
LEAD_IDS = [
    "6a16a0def600428ced529fe9",
    "6a1691663f7f4722262800c0",
    "6a16962a3c4cc88d7a5ee09c",
    "6a168c7156c59f86fbd5bd3b",
    "6a16cf987141898702b46a46",
    "6a169941c7a89e8cd31a71f7",
    "6a16899eb9a2b38dfe2ea10f",
    "6a16b69632bc0285fd4f959a",
    "6a16d33bbdf65621e0e6b629",
]

LOG_API = "http://192.168.41.116:9090/search?q={lead_id}"


async def fetch_logs(session: aiohttp.ClientSession, lead_id: str) -> dict:
    url = LOG_API.format(lead_id=lead_id)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            return await r.json()
    except Exception as e:
        return {"error": str(e)}


def fetch_mongo(collection, lead_id: str) -> dict | None:
    doc = collection.find_one({"lead_id": lead_id}) or \
          collection.find_one({"lead_id": ObjectId(lead_id)})
    if not doc:
        return None
    # Convert ObjectId/non-serialisable fields
    doc["_id"] = str(doc["_id"])
    if "lead_id" in doc and isinstance(doc["lead_id"], ObjectId):
        doc["lead_id"] = str(doc["lead_id"])
    return doc


async def process_lead(
    lead_id: str,
    session: aiohttp.ClientSession,
    collection,
) -> dict:
    logs_raw, mongo_doc = await asyncio.gather(
        fetch_logs(session, lead_id),
        asyncio.to_thread(fetch_mongo, collection, lead_id),
    )

    # Flatten log lines into a single list
    log_lines: list[str] = []
    for _file, lines in (logs_raw.get("results") or {}).items():
        log_lines.extend(lines)

    result = {
        "lead_id": lead_id,
        "mobile": (logs_raw.get("mobiles_found") or [None])[0],
        # ── Key fields from MongoDB ───────────────────────────────────────────
        "call_outcome": (mongo_doc or {}).get("call_outcome"),
        "call_summary": (mongo_doc or {}).get("call_summary"),
        "call_duration_sec": (mongo_doc or {}).get("call_duration_sec"),
        "status": (mongo_doc or {}).get("status"),
        "category": ((mongo_doc or {}).get("lead_record") or {}).get("catname"),
        # ── Transcript ───────────────────────────────────────────────────────
        "transcript": (mongo_doc or {}).get("transcript", []),
        "muted_transcript": (mongo_doc or {}).get("muted_transcript", []),
        # ── Raw logs ─────────────────────────────────────────────────────────
        "logs": log_lines,
        # ── Full mongo doc (for deeper inspection) ───────────────────────────
        "mongo_doc": mongo_doc,
    }
    return result


async def main(lead_ids: list[str]) -> None:
    client = MongoClient(MONGO_URI)
    collection = client[MONGO_DB][MONGO_COLLECTION]

    async with aiohttp.ClientSession() as session:
        tasks = [process_lead(lid, session, collection) for lid in lead_ids]
        results = await asyncio.gather(*tasks)

    client.close()

    out = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(results),
        "leads": results,
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(__file__).parent / f"lead_debug_{ts}.json"
    def _default(o):
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)

    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=_default))
    print(f"Saved → {out_path}  ({len(results)} leads)")

    # Quick summary table
    print(f"\n{'Lead ID':<28} {'Mobile':<14} {'Outcome':<22} {'Duration':>8}  Category")
    print("-" * 100)
    for r in results:
        print(
            f"{r['lead_id']:<28} "
            f"{str(r['mobile'] or ''):<14} "
            f"{str(r['call_outcome'] or 'N/A'):<22} "
            f"{str(r['call_duration_sec'] or '')!s:>8}s "
            f" {r['category'] or ''}"
        )


if __name__ == "__main__":
    ids = sys.argv[1:] if len(sys.argv) > 1 else LEAD_IDS
    if not ids:
        print("No lead IDs provided. Edit LEAD_IDS in the script or pass them as CLI args.")
        sys.exit(1)
    asyncio.run(main(ids))
