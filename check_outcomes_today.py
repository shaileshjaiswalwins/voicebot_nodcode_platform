"""
Check all Interested / Approved / Enriched outcomes from MongoDB for today.

Usage:
    .venv/bin/python check_outcomes_today.py
    .venv/bin/python check_outcomes_today.py --date 2026-06-01   # specific date
    .venv/bin/python check_outcomes_today.py --json              # also save JSON
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parent / ".env")

from callback_worker.config import MONGO_URI, MONGO_DB, MONGO_COLLECTION

TARGET_OUTCOMES = ["Interested", "Approved", "Enriched"]

IST = timezone(timedelta(hours=5, minutes=30))


def parse_args():
    p = argparse.ArgumentParser(description="Check positive outcomes from MongoDB for a given date.")
    p.add_argument("--date", help="Date in YYYY-MM-DD (default: today IST)", default=None)
    p.add_argument("--json", action="store_true", help="Save results to JSON file")
    return p.parse_args()


def get_day_range(date_str: str | None):
    if date_str:
        day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=IST)
    else:
        day = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)

    start = day.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    end = (start + timedelta(days=1))
    return start, end


def fetch(date_str: str | None) -> list[dict]:
    start, end = get_day_range(date_str)
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    col = client[MONGO_DB][MONGO_COLLECTION]

    cursor = col.find(
        {
            "created_at": {"$gte": start, "$lt": end},
            "analysis.call_outcome": {"$in": TARGET_OUTCOMES},
        },
        {
            "lead_id": 1,
            "created_at": 1,
            "call_duration_sec": 1,
            "status": 1,
            "analysis.call_outcome": 1,
            "analysis.call_summary": 1,
            "analysis.lead_intent_score": 1,
            "analysis.urgency_flag": 1,
            "analysis.deal_value": 1,
            "lead_record.catname": 1,
            "lead_record.buyer_details.buyer_name": 1,
            "lead_record.buyer_details.buyer_number": 1,
            "lead_record.buyer_details.buyer_city": 1,
            "tagged": 1,
        },
    ).sort("created_at", 1)

    rows = []
    for doc in cursor:
        analysis = doc.get("analysis", {})
        lead_record = doc.get("lead_record", {})
        buyer = lead_record.get("buyer_details", {})
        created_ist = doc["created_at"].replace(tzinfo=timezone.utc).astimezone(IST)
        rows.append(
            {
                "lead_id": doc.get("lead_id", ""),
                "call_outcome": analysis.get("call_outcome", ""),
                "created_at": created_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                "call_duration_sec": round(doc.get("call_duration_sec", 0)),
                "status": doc.get("status", ""),
                "tagged": doc.get("tagged", False),
                "catname": lead_record.get("catname", ""),
                "buyer_name": buyer.get("buyer_name", ""),
                "buyer_number": buyer.get("buyer_number", ""),
                "buyer_city": buyer.get("buyer_city", ""),
                "lead_intent_score": analysis.get("lead_intent_score", ""),
                "urgency_flag": analysis.get("urgency_flag", ""),
                "deal_value": analysis.get("deal_value", ""),
                "call_summary": analysis.get("call_summary", ""),
            }
        )
    client.close()
    return rows


def print_table(rows: list[dict], outcome: str):
    subset = [r for r in rows if r["call_outcome"] == outcome]
    print(f"\n{'='*80}")
    print(f"  {outcome.upper()}  ({len(subset)} leads)")
    print(f"{'='*80}")
    if not subset:
        print("  (none)")
        return

    col_w = {"lead_id": 26, "created_at": 22, "dur": 6, "cat": 22, "city": 14, "score": 7, "urgent": 7, "tagged": 7}
    header = (
        f"{'Lead ID':<{col_w['lead_id']}} "
        f"{'Time (IST)':<{col_w['created_at']}} "
        f"{'Dur':>{col_w['dur']}} "
        f"{'Category':<{col_w['cat']}} "
        f"{'City':<{col_w['city']}} "
        f"{'Score':>{col_w['score']}} "
        f"{'Urgent':<{col_w['urgent']}} "
        f"{'Tagged':<{col_w['tagged']}}"
    )
    print(header)
    print("-" * len(header))
    for r in subset:
        print(
            f"{r['lead_id']:<{col_w['lead_id']}} "
            f"{r['created_at']:<{col_w['created_at']}} "
            f"{str(r['call_duration_sec'])+'s':>{col_w['dur']}} "
            f"{r['catname'][:col_w['cat']]:<{col_w['cat']}} "
            f"{r['buyer_city'][:col_w['city']]:<{col_w['city']}} "
            f"{r['lead_intent_score']:>{col_w['score']}} "
            f"{r['urgency_flag']:<{col_w['urgent']}} "
            f"{'Y' if r['tagged'] else 'N':<{col_w['tagged']}}"
        )


def main():
    args = parse_args()

    date_label = args.date or datetime.now(IST).strftime("%Y-%m-%d")
    print(f"\nFetching outcomes for date: {date_label}")
    print(f"Outcomes: {', '.join(TARGET_OUTCOMES)}")

    try:
        rows = fetch(args.date)
    except Exception as e:
        print(f"\nERROR connecting to MongoDB: {e}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print("\nNo records found.")
        return

    # Summary
    counts = {o: sum(1 for r in rows if r["call_outcome"] == o) for o in TARGET_OUTCOMES}
    print(f"\nSUMMARY  (total {len(rows)} leads)")
    for o, c in counts.items():
        print(f"  {o:<12} {c}")

    for outcome in TARGET_OUTCOMES:
        print_table(rows, outcome)

    print()

    if args.json:
        ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
        out_file = Path(f"outcomes_{date_label}_{ts}.json")
        out_file.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        print(f"Saved to {out_file}")


if __name__ == "__main__":
    main()
