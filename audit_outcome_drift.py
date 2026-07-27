"""
Audit outcome drift for a given date — every MIS lead included.

Flow:
  1. Paginate MIS API fully → ALL leads for the date (connected + not connected).
  2. Join each MIS lead to MongoDB call_transcripts by lead_id.
  3. Connected (has transcript):
       - Re-run generate_call_analysis N times (majority vote).
       - Old Outcome = stored analysis.call_outcome from MongoDB.
       - New Outcome = majority vote result.
       - Flagged as anomaly if they differ.
  4. Not connected (no MongoDB doc):
       - Old Outcome = MIS callback.call_outcome (or "No Disp" if blank).
       - New Outcome = same (nothing to reprocess).
       - Changed = No.
  5. Write ALL leads to CSV + summary block.
     READ-ONLY — no callbacks sent, no Mongo writes.

Usage:
    .venv/bin/python audit_outcome_drift.py
    .venv/bin/python audit_outcome_drift.py --date 2026-06-24
    .venv/bin/python audit_outcome_drift.py --limit 30        # dry run
    .venv/bin/python audit_outcome_drift.py --votes 3         # majority vote (default)
    .venv/bin/python audit_outcome_drift.py --votes 1         # fast, no voting
    .venv/bin/python audit_outcome_drift.py --concurrency 6
    .venv/bin/python audit_outcome_drift.py --out my.csv

Anomaly detection:
    LLM is non-deterministic. --votes N runs generate_call_analysis N times and
    takes the majority winner as "New Outcome". Only flags anomaly when the
    majority new outcome differs from old — filters out random single-shot variance.
    Default 3 votes (need 2-of-3). Use --votes 1 to skip (fast but noisy).

Output: outcome_drift_<DATE>_<ts>.xlsx  (3 sheets)
  Sheet 1 "All Leads"      — every MIS lead (connected + not connected)
  Sheet 2 "Anomalies"      — only leads where outcome changed (for prompt fixing)
  Sheet 3 "Metrics"        — summary, per-outcome drift table, run stats
"""

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp
from bson import ObjectId
from dotenv import load_dotenv
from loguru import logger
from pymongo import MongoClient
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

load_dotenv(Path(__file__).resolve().parent / ".env")

from callback_worker.config import MONGO_URI, MONGO_DB, MONGO_COLLECTION
from callback_worker.analysis import (
    generate_call_analysis,
    fallback_analysis,
    DISPOSITION_MAP,
)

# ── Constants ─────────────────────────────────────────────────────────────────
MIS_API_BASE = "http://192.168.8.67:8000"
MIS_LEADS_URL = f"{MIS_API_BASE}/leads/ai-lead-qualify/mis"
MIS_PAGE_SIZE = 100
MIS_MAX_PAGES = 100  # safety cap — 100 * 100 = 10 000 leads max

IST = timezone(timedelta(hours=5, minutes=30))


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Audit outcome drift — every MIS lead for the date."
    )
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: today IST)")
    p.add_argument("--limit", type=int, default=0,
                   help="Cap total MIS leads fetched (0 = no cap; use for dry runs)")
    p.add_argument("--votes", type=int, default=3,
                   help="Reprocess N times per connected lead, take majority (default 3). "
                        "Use 1 to skip voting.")
    p.add_argument("--concurrency", type=int, default=6,
                   help="Max parallel Gemini calls (default 6)")
    p.add_argument("--out", default=None, help="Output CSV path (default: auto-named)")
    return p.parse_args()


# ── Date helpers ───────────────────────────────────────────────────────────────

def get_date_label(date_str: str | None) -> str:
    return date_str if date_str else datetime.now(IST).strftime("%Y-%m-%d")


def get_day_range(date_str: str | None):
    """Return (start_utc, end_utc) for the given IST date."""
    if date_str:
        day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=IST)
    else:
        day = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


# ── MIS: full pagination ───────────────────────────────────────────────────────

async def fetch_mis_leads(
    date_label: str,
    session: aiohttp.ClientSession,
    limit: int = 0,
) -> list[dict]:
    """
    Paginate MIS fully and return a list of raw MIS records for the date.
    Each record contains _id, callback.call_outcome, catname, buyer_details, etc.
    """
    records_all: list[dict] = []
    total: int | None = None

    for page in range(1, MIS_MAX_PAGES + 1):
        params = {
            "lead_id": "", "ref_id": "", "jduid": "", "city": "",
            "ai_partner": "inh-suny-bot", "disposition_type": "latest",
            "disposition": "", "ncatid": "", "whatsapp_flag": "", "medium": "",
            "flow": "all", "page_name": "", "fromdate": date_label,
            "todate": date_label, "page": str(page), "limit": str(MIS_PAGE_SIZE),
            "blf": "", "chk_mis": "gen",
        }
        try:
            async with session.get(
                MIS_LEADS_URL, params=params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error(f"[MIS] Page {page} failed: {e}")
            break

        res = data.get("results", {})
        records = res.get("data", [])

        if total is None:
            total = int(res.get("total_counts", 0) or 0)
            logger.info(f"[MIS] total_counts={total} for {date_label}")

        if not records:
            logger.info(f"[MIS] Page {page}: empty — done.")
            break

        records_all.extend(records)
        logger.info(
            f"[MIS] Page {page}: +{len(records)} "
            f"(collected {len(records_all)}/{total})"
        )

        if limit and len(records_all) >= limit:
            records_all = records_all[:limit]
            logger.info(f"[MIS] Hit --limit {limit}, stopping early.")
            break

        if total and len(records_all) >= total:
            break

    return records_all


# ── MongoDB: bulk join ─────────────────────────────────────────────────────────

def fetch_mongo_docs(lead_ids: list[str], collection) -> dict[str, dict]:
    """Return {lead_id_str: mongo_doc} for every matched call transcript."""
    docs: dict[str, dict] = {}
    for lid in lead_ids:
        doc = collection.find_one({"lead_id": lid})
        if not doc:
            try:
                doc = collection.find_one({"lead_id": ObjectId(lid)})
            except Exception:
                pass
        if doc:
            docs[lid] = doc
    return docs


# ── Transcript formatter ───────────────────────────────────────────────────────

def fmt_transcript(transcript, muted_transcript) -> str:
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


# ── Reprocess: single Gemini call ─────────────────────────────────────────────

async def _single_analysis(
    transcript, status, schema, session,
    muted_transcript, gemini_connect_failed,
    duration_secs, greeting_done, user_speech_ms, wrong_opener_detected,
) -> dict:
    return await generate_call_analysis(
        transcript, status, schema, session,
        muted_transcript=muted_transcript,
        gemini_connect_failed=gemini_connect_failed,
        duration_secs=duration_secs,
        greeting_done=greeting_done,
        user_speech_ms=user_speech_ms,
        wrong_opener_detected=wrong_opener_detected,
    )


# ── Per-lead processing ────────────────────────────────────────────────────────

async def process_lead(
    mis_rec: dict,
    mongo_doc: dict | None,
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    votes: int,
) -> dict:
    """
    Process one lead. If it has a Mongo doc (connected): reprocess with majority vote.
    If not (not connected): use MIS callback.call_outcome as-is, no reprocess.
    """
    lead_id = str(mis_rec.get("_id") or "")
    catname = mis_rec.get("catname", "")
    buyer = mis_rec.get("buyer_details", {})
    buyer_name = buyer.get("buyer_name", "")
    buyer_city = mis_rec.get("buyer_city") or buyer.get("buyer_city", "")
    mobile = mis_rec.get("mobile", "")

    mis_callback = mis_rec.get("callback") or {}
    mis_disp = (mis_callback.get("call_outcome") or "").strip()
    mis_call_status = mis_rec.get("call_status", "")

    # ── Not connected: no Mongo doc ───────────────────────────────────────────
    if mongo_doc is None:
        old_outcome = mis_disp or "No Disp"
        return {
            "lead_id": lead_id,
            "catname": catname,
            "buyer_name": buyer_name,
            "buyer_city": buyer_city,
            "mobile": mobile,
            "connected": False,
            "mis_call_status": mis_call_status,
            "old_outcome": old_outcome,
            "new_outcome": old_outcome,
            "changed": False,
            "vote_breakdown": "",
            "vote_details": [],
            "transcript_text": "",
            "analysis_summary": "",
            "call_outcome_description": "",
            "error": "",
        }

    # ── Connected: reprocess with majority vote ───────────────────────────────
    async with sem:
        old_outcome = mis_disp or "No Disp"  # MIS is always the source of truth for old outcome

        schema = (mongo_doc.get("lead_record") or {}).get("qualification_schema", {}) or {}
        status = mongo_doc.get("status", "completed")
        transcript = mongo_doc.get("transcript") or []
        muted_transcript = mongo_doc.get("muted_transcript") or []
        gemini_connect_failed = bool(mongo_doc.get("gemini_connect_failed"))
        duration_secs = mongo_doc.get("call_duration_sec")
        greeting_done = mongo_doc.get("greeting_done", True)
        user_speech_ms = int(mongo_doc.get("user_speech_ms") or 0)
        wrong_opener_detected = bool(mongo_doc.get("wrong_opener_detected"))

        kwargs = dict(
            muted_transcript=muted_transcript,
            gemini_connect_failed=gemini_connect_failed,
            duration_secs=duration_secs,
            greeting_done=greeting_done,
            user_speech_ms=user_speech_ms,
            wrong_opener_detected=wrong_opener_detected,
        )

        vote_tasks = [
            _single_analysis(transcript, status, schema, session, **kwargs)
            for _ in range(votes)
        ]
        vote_results = await asyncio.gather(*vote_tasks, return_exceptions=True)

        good: list[dict] = []
        errors: list[str] = []
        for v in vote_results:
            if isinstance(v, Exception):
                errors.append(str(v))
            else:
                good.append(v)

        error_msg = "; ".join(errors) if errors else ""
        if not good:
            logger.error(f"[REPROCESS] {lead_id}: all {votes} votes failed")
            good = [fallback_analysis(status)]
            good[0]["_error"] = error_msg

        outcome_votes: Counter = Counter(g.get("call_outcome", "") for g in good)
        new_outcome, _ = outcome_votes.most_common(1)[0]
        winner = next((g for g in good if g.get("call_outcome") == new_outcome), good[0])
        vote_breakdown = ", ".join(f"{o}×{c}" for o, c in outcome_votes.most_common())
        changed = new_outcome != old_outcome

        # Store per-vote detail for debug sheet
        vote_details = [
            {
                "outcome": g.get("call_outcome", ""),
                "summary": g.get("call_summary", ""),
                "description": g.get("call_outcome_description", ""),
            }
            for g in good
        ]

        if changed:
            logger.info(
                f"[REPROCESS] {lead_id}: {old_outcome!r} → {new_outcome!r} "
                f"[{vote_breakdown}]"
            )

        return {
            "lead_id": lead_id,
            "catname": catname,
            "buyer_name": buyer_name,
            "buyer_city": buyer_city,
            "mobile": mobile,
            "connected": True,
            "mis_call_status": mis_call_status,
            "old_outcome": old_outcome,
            "new_outcome": new_outcome,
            "changed": changed,
            "vote_breakdown": vote_breakdown,
            "vote_details": vote_details,
            "transcript_text": fmt_transcript(transcript, muted_transcript),
            "analysis_summary": winner.get("call_summary", ""),
            "call_outcome_description": winner.get("call_outcome_description", ""),
            "error": error_msg,
        }


# ── Excel helpers ─────────────────────────────────────────────────────────────

LEAD_HEADERS = [
    "Lead ID", "Transcript", "Old Outcome", "New Outcome",
    "Analysis Summary", "Comment / Notes",
]

ANOMALY_HEADERS = [
    "Lead ID", "Transcript", "Old Outcome", "New Outcome",
    "Analysis Summary", "Comment / Notes",
]

_OUTCOME_ORDER = list(DISPOSITION_MAP.keys())

# Colours
_HDR_FILL  = PatternFill("solid", fgColor="1F4E79")   # dark blue
_ANOM_FILL = PatternFill("solid", fgColor="FFE699")   # yellow — anomaly rows
_NC_FILL   = PatternFill("solid", fgColor="F2F2F2")   # light grey — not connected
_MET_HDR   = PatternFill("solid", fgColor="2E75B6")   # medium blue — metric section headers
_POS_FILL  = PatternFill("solid", fgColor="E2EFDA")   # light green — positive outcomes
_NEG_FILL  = PatternFill("solid", fgColor="FCE4D6")   # light red — negative drift

_WHITE_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
_BOLD       = Font(name="Calibri", bold=True, size=10)
_NORMAL     = Font(name="Calibri", size=10)
_WRAP       = Alignment(wrap_text=True, vertical="top")
_TOP        = Alignment(vertical="top")
_THIN       = Side(style="thin", color="BFBFBF")
_BORDER     = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _hdr_row(ws, headers: list[str]) -> None:
    ws.append(headers)
    for cell in ws[ws.max_row]:
        cell.font = _WHITE_FONT
        cell.fill = _HDR_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _BORDER


def _set_col_widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _outcome_sort_key(name: str) -> int:
    try:
        return _OUTCOME_ORDER.index(name)
    except ValueError:
        return 99


def _lead_row(r: dict, connected: bool) -> list:
    if connected:
        notes = (
            f"{r['old_outcome']} → {r['new_outcome']}: {r['call_outcome_description']}"
            if r["changed"] else ""
        )
        if r["error"]:
            notes += f"  [err: {r['error']}]"
        return [
            r["lead_id"], r["transcript_text"],
            r["old_outcome"], r["new_outcome"],
            r["analysis_summary"], notes,
        ]
    else:
        # Not connected — no transcript, no new outcome
        return [
            r["lead_id"], "",
            r["old_outcome"], "",
            "", f"Not connected — MIS status: {r['mis_call_status']}",
        ]


# ── Sheet 1: All Leads ────────────────────────────────────────────────────────

def _write_all_leads(wb: Workbook, results: list[dict]) -> None:
    ws = wb.active
    ws.title = "All Leads"
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 30

    _hdr_row(ws, LEAD_HEADERS)
    # Lead ID | Transcript | Old Outcome | New Outcome | Analysis Summary | Comment
    _set_col_widths(ws, [28, 80, 24, 24, 50, 50])

    connected_rows = sorted([r for r in results if r["connected"]],
                             key=lambda x: (not x["changed"], x["lead_id"]))
    nc_rows        = [r for r in results if not r["connected"]]

    for r in connected_rows:
        ws.append(_lead_row(r, connected=True))
        excel_row = ws.max_row
        ws.row_dimensions[excel_row].height = 80
        fill = _ANOM_FILL if r["changed"] else None
        for col_idx, cell in enumerate(ws[excel_row], 1):
            cell.font      = _NORMAL
            cell.border    = _BORDER
            cell.alignment = _WRAP if col_idx in (2, 5, 6) else _TOP
            if fill:
                cell.fill = fill

    for r in nc_rows:
        ws.append(_lead_row(r, connected=False))
        excel_row = ws.max_row
        ws.row_dimensions[excel_row].height = 20
        for cell in ws[excel_row]:
            cell.font      = _NORMAL
            cell.fill      = _NC_FILL
            cell.border    = _BORDER
            cell.alignment = _TOP

    ws.auto_filter.ref = f"A1:{get_column_letter(len(LEAD_HEADERS))}1"


# ── Sheet 2: Anomalies ────────────────────────────────────────────────────────

def _write_anomalies(wb: Workbook, results: list[dict]) -> None:
    ws = wb.create_sheet("Anomalies")
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 30

    _hdr_row(ws, ANOMALY_HEADERS)
    # Lead ID | Transcript | Old Outcome | New Outcome | Analysis Summary | Comment / Notes
    _set_col_widths(ws, [28, 80, 24, 24, 50, 50])

    anomalies = [r for r in results if r["connected"] and r["changed"]]
    # Group by transition so similar bugs land together
    anomalies.sort(key=lambda x: (x["old_outcome"], x["new_outcome"], x["lead_id"]))

    for r in anomalies:
        notes = f"{r['old_outcome']} → {r['new_outcome']}: {r['call_outcome_description']}"
        ws.append([
            r["lead_id"], r["transcript_text"],
            r["old_outcome"], r["new_outcome"],
            r["analysis_summary"], notes,
        ])
        excel_row = ws.max_row
        ws.row_dimensions[excel_row].height = 80
        for col_idx, cell in enumerate(ws[excel_row], 1):
            cell.font      = _NORMAL
            cell.fill      = _ANOM_FILL
            cell.border    = _BORDER
            cell.alignment = _WRAP if col_idx in (2, 5, 6) else _TOP

    ws.auto_filter.ref = f"A1:{get_column_letter(len(ANOMALY_HEADERS))}1"


# ── Sheet 3: Metrics ──────────────────────────────────────────────────────────

def _write_metrics(
    wb: Workbook,
    results: list[dict],
    date_label: str,
    votes: int,
) -> None:
    ws = wb.create_sheet("Metrics")
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 16

    connected     = [r for r in results if r["connected"]]
    not_connected = [r for r in results if not r["connected"]]
    anomalies     = [r for r in connected if r["changed"]]

    old_counts = Counter(r["old_outcome"] for r in connected)
    new_counts = Counter(r["new_outcome"] for r in connected)
    all_outcomes = sorted(set(old_counts) | set(new_counts), key=_outcome_sort_key)

    POSITIVE = {"Interested", "Approved", "Enriched"}
    old_pos  = sum(old_counts.get(o, 0) for o in POSITIVE)
    new_pos  = sum(new_counts.get(o, 0) for o in POSITIVE)
    old_int  = old_counts.get("Interested", 0)
    new_int  = new_counts.get("Interested", 0)

    def section(title: str) -> None:
        ws.append([title])
        cell = ws.cell(ws.max_row, 1)
        cell.font  = _WHITE_FONT
        cell.fill  = _MET_HDR
        ws.merge_cells(
            start_row=ws.max_row, start_column=1,
            end_row=ws.max_row, end_column=4,
        )

    def kv(label, val, bold=False) -> None:
        ws.append([label, val])
        ws.cell(ws.max_row, 1).font = _BOLD if bold else _NORMAL
        ws.cell(ws.max_row, 2).font = _BOLD if bold else _NORMAL
        for c in [1, 2]:
            ws.cell(ws.max_row, c).border = _BORDER

    def blank() -> None:
        ws.append([])

    # ── Drift Summary ─────────────────────────────────────────────────────────
    section("Drift Summary (connected calls only)")
    kv('Old "Interested" Count',              old_int,                bold=True)
    kv('New "Interested" Count',              new_int,                bold=True)
    kv('Net Difference — Interested',         f"{new_int - old_int:+d}", bold=True)
    _colour_delta(ws, new_int - old_int)
    blank()
    kv('Old Positive (Interested+Approved+Enriched)', old_pos, bold=True)
    kv('New Positive (Interested+Approved+Enriched)', new_pos, bold=True)
    kv('Net Difference — Total Positive',     f"{new_pos - old_pos:+d}", bold=True)
    _colour_delta(ws, new_pos - old_pos)
    blank()

    # ── Per-outcome breakdown ─────────────────────────────────────────────────
    section("Per-Outcome Breakdown (connected calls, Old vs New)")
    ws.append(["Outcome", "Old Count", "New Count", "Delta"])
    for cell in ws[ws.max_row]:
        cell.font = _BOLD
        cell.border = _BORDER

    for outcome in all_outcomes:
        old_c = old_counts.get(outcome, 0)
        new_c = new_counts.get(outcome, 0)
        delta = new_c - old_c
        ws.append([outcome, old_c, new_c, f"{delta:+d}"])
        row = ws.max_row
        for c in [1, 2, 3, 4]:
            ws.cell(row, c).font   = _NORMAL
            ws.cell(row, c).border = _BORDER
        if outcome in POSITIVE:
            for c in [1, 2, 3, 4]:
                ws.cell(row, c).fill = _POS_FILL
        if delta < 0:
            ws.cell(row, 4).fill = _NEG_FILL
        elif delta > 0 and outcome in POSITIVE:
            ws.cell(row, 4).fill = _POS_FILL
    blank()

    # ── Anomaly transition summary ────────────────────────────────────────────
    section("Anomaly Transitions (top 15)")
    ws.append(["Transition", "Count"])
    for cell in ws[ws.max_row]:
        cell.font = _BOLD; cell.border = _BORDER
    ctr: Counter = Counter(
        f"{r['old_outcome']} → {r['new_outcome']}" for r in anomalies
    )
    for pair, count in ctr.most_common(15):
        ws.append([pair, count])
        for c in [1, 2]:
            ws.cell(ws.max_row, c).font   = _NORMAL
            ws.cell(ws.max_row, c).border = _BORDER
    blank()

    # ── Run stats ─────────────────────────────────────────────────────────────
    section("Run Stats")
    kv("Date audited",                        date_label)
    kv("MIS total leads (fetched)",           len(results))
    kv("Connected — has call transcript",     len(connected))
    kv("Not connected — no transcript",       len(not_connected))
    kv("Votes per lead (majority threshold)", f"{votes} (>{votes//2})")
    kv("Anomalies (outcome changed)",         len(anomalies))
    kv("Reprocess errors",                    sum(1 for r in connected if r["error"]))


def _colour_delta(ws, delta: int) -> None:
    """Colour the last written delta cell red/green."""
    cell = ws.cell(ws.max_row, 2)
    if delta > 0:
        cell.fill = _POS_FILL
    elif delta < 0:
        cell.fill = _NEG_FILL


# ── Main writer ───────────────────────────────────────────────────────────────

def write_xlsx(
    results: list[dict],
    out_path: Path,
    date_label: str,
    votes: int,
) -> None:
    wb = Workbook()
    _write_all_leads(wb, results)
    _write_anomalies(wb, results)
    _write_metrics(wb, results, date_label, votes)
    wb.save(out_path)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(args) -> None:
    date_label = get_date_label(args.date)
    ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    out_path = (
        Path(args.out) if args.out
        else Path(f"outcome_drift_{date_label}_{ts}.xlsx")
    )

    logger.info(
        f"[AUDIT] date={date_label}  limit={args.limit or 'none'}  "
        f"votes={args.votes}  concurrency={args.concurrency}"
    )

    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    collection = mongo_client[MONGO_DB][MONGO_COLLECTION]

    async with aiohttp.ClientSession() as session:

        # Step 1: fetch all MIS leads for the date
        mis_records = await fetch_mis_leads(date_label, session, limit=args.limit)
        logger.info(f"[AUDIT] Fetched {len(mis_records)} MIS records")

        if not mis_records:
            logger.warning("[AUDIT] No MIS records found. Exiting.")
            mongo_client.close()
            sys.exit(0)

        # Step 2: bulk-join to MongoDB
        lead_ids = [str(r.get("_id") or "") for r in mis_records if r.get("_id")]
        logger.info(f"[AUDIT] Joining {len(lead_ids)} lead IDs to MongoDB...")
        mongo_docs = await asyncio.to_thread(fetch_mongo_docs, lead_ids, collection)
        mongo_client.close()

        n_connected = len(mongo_docs)
        n_not_connected = len(mis_records) - n_connected
        logger.info(
            f"[AUDIT] Connected (has transcript): {n_connected} | "
            f"Not connected: {n_not_connected}"
        )

        # Step 3: process all leads
        sem = asyncio.Semaphore(args.concurrency)
        tasks = [
            process_lead(
                rec,
                mongo_docs.get(str(rec.get("_id") or "")),
                session,
                sem,
                votes=args.votes,
            )
            for rec in mis_records
        ]
        logger.info(
            f"[AUDIT] Processing {len(tasks)} leads "
            f"({n_connected} will reprocess with {args.votes} votes, "
            f"{n_not_connected} not-connected pass-through)..."
        )
        results: list[dict] = await asyncio.gather(*tasks)

    # Step 4: stats
    anomalies = [r for r in results if r["connected"] and r["changed"]]
    errors = [r for r in results if r.get("error")]
    logger.info(
        f"[AUDIT] Done — {n_connected} connected | "
        f"{n_not_connected} not-connected | "
        f"{len(anomalies)} anomalies | {len(errors)} errors"
    )

    # Step 5: write Excel (3 sheets)
    write_xlsx(results, out_path, date_label, votes=args.votes)

    connected = [r for r in results if r["connected"]]
    old_counts: Counter = Counter(r["old_outcome"] for r in connected)
    new_counts: Counter = Counter(r["new_outcome"] for r in connected)
    POSITIVE = {"Interested", "Approved", "Enriched"}

    print(f"\nSaved → {out_path}")
    print(f"  Total MIS leads in CSV          : {len(results)}")
    print(f"  Connected (reprocessed)         : {n_connected}")
    print(f"  Not connected (MIS disp only)   : {n_not_connected}")
    print(f"  Anomalies (outcome changed)     : {len(anomalies)}")
    print(f"  Reprocess errors                : {len(errors)}")

    old_int = old_counts.get("Interested", 0)
    new_int = new_counts.get("Interested", 0)
    old_pos = sum(old_counts.get(o, 0) for o in POSITIVE)
    new_pos = sum(new_counts.get(o, 0) for o in POSITIVE)
    print(f'\n  Old "Interested"              : {old_int}')
    print(f'  New "Interested"              : {new_int}')
    print(f'  Net Drift (Interested)        : {new_int - old_int:+d}')
    print(f'\n  Old Positive (I+A+E)          : {old_pos}')
    print(f'  New Positive (I+A+E)          : {new_pos}')
    print(f'  Net Drift (Total Positive)    : {new_pos - old_pos:+d}')

    if anomalies:
        print(f"\n  Anomaly transitions ({len(anomalies)} total):")
        ctr: Counter = Counter(
            f"{r['old_outcome']} → {r['new_outcome']}" for r in anomalies
        )
        for pair, count in ctr.most_common(10):
            print(f"    {count:>4}x  {pair}")


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args))
