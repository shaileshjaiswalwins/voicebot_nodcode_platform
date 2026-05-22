"""Callback payload builder and HTTP sender — mirrors the bot's original send_callback."""

import asyncio
import json

import aiohttp
from loguru import logger

from .analysis import DISPOSITION_MAP, fuzzy_match_opt_id, status_to_outcome
from .config import CALLBACK_API_URL, CALLBACK_UPDATE_API_URL


async def send_callback(
    payload: dict,
    http_session: aiohttp.ClientSession,
    callback_api_url: str = CALLBACK_API_URL,
) -> bool:
    """Send callback with up to 3 attempts (2s, 4s backoff). Returns True on success."""
    delays = [0, 2, 4]
    for attempt, delay in enumerate(delays, 1):
        if delay:
            await asyncio.sleep(delay)
        try:
            logger.info(
                f"[CALLBACK] Sending to {callback_api_url} (attempt {attempt}/3) | "
                f"payload={json.dumps(payload, ensure_ascii=False)}"
            )
            async with http_session.post(
                callback_api_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                body = await resp.text()
                if resp.status not in (200, 201):
                    logger.warning(f"[CALLBACK] attempt {attempt} — {resp.status}: {body[:300]}")
                else:
                    logger.info(f"[CALLBACK] attempt {attempt} — {resp.status} OK: {body[:300]}")
                    return True
        except Exception as e:
            logger.error(f"[CALLBACK] attempt {attempt} failed: {type(e).__name__}: {e}")
    logger.error("[CALLBACK] All 3 attempts failed — callback not delivered")
    return False


async def send_callback_update(
    call_id: str,
    lead_id: str,
    updates: dict,
    http_session: aiohttp.ClientSession,
    callback_update_api_url: str = CALLBACK_UPDATE_API_URL,
) -> bool:
    """Patch specific fields on an already-sent callback. Only pass fields you want to change —
    the API overwrites any field you include, so never spread the full payload here."""
    payload = {
        "call_id": call_id,
        "lead_id": str(lead_id),
        "ai_partner": "inh-suny-bot",
        **updates,
    }
    delays = [0, 2, 4]
    for attempt, delay in enumerate(delays, 1):
        if delay:
            await asyncio.sleep(delay)
        try:
            logger.info(
                f"[CALLBACK-UPDATE] Sending to {callback_update_api_url} (attempt {attempt}/3) | "
                f"payload={json.dumps(payload, ensure_ascii=False)}"
            )
            async with http_session.post(
                callback_update_api_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                body = await resp.text()
                if resp.status not in (200, 201):
                    logger.warning(f"[CALLBACK-UPDATE] attempt {attempt} — {resp.status}: {body[:300]}")
                    continue
                # API returns 200 even on failure — check the body
                try:
                    parsed = json.loads(body)
                    if parsed.get("error", {}).get("code", 0) != 0:
                        logger.warning(f"[CALLBACK-UPDATE] attempt {attempt} — API error: {body[:300]}")
                        continue
                except Exception:
                    pass
                logger.info(f"[CALLBACK-UPDATE] attempt {attempt} — {resp.status} OK: {body[:300]}")
                return True
        except Exception as e:
            logger.error(f"[CALLBACK-UPDATE] attempt {attempt} failed: {type(e).__name__}: {e}")
    logger.error("[CALLBACK-UPDATE] All 3 attempts failed — update not delivered")
    return False


def build_callback_payload(doc: dict, analysis: dict, b2b_score: dict | None = None) -> dict:
    """Build the exact callback payload shape the original bot produced."""
    status = doc.get("status", "completed")
    outcome = analysis.get("call_outcome", status_to_outcome(status))

    # If the call was flagged disconnected but analysis resolved a positive outcome, treat as completed
    if status == "disconnected" and outcome in ("Approved", "Enriched"):
        status = "completed"

    _b2b = b2b_score or {}
    payload: dict = {
        "call_id": str(doc.get("_id", "")),
        "lead_id": doc.get("lead_id"),
        "is_business": analysis.get("is_business", ""),
        "business_name": analysis.get("business_name", ""),
        "business_city": analysis.get("business_city", ""),
        "deal_value": _b2b.get("deal_value", ""),
        "lead_intent_score": _b2b.get("lead_intent_score", ""),
        "urgency_flag": _b2b.get("urgency_flag", "no"),
        "ai_partner": "inh-suny-bot",
        "call_outcome": outcome,
        "call_outcome_desc": analysis.get("call_outcome_description", DISPOSITION_MAP.get(outcome, "")),
        "call_summary": analysis.get("call_summary", ""),
        "product_change": doc.get("product_change") or analysis.get("product_change") or {},
        "call_duration": doc.get("call_duration_sec", 0),
    }
    payload["rescheduled_to"] = analysis.get("rescheduled_to", "") or ""

    schema = (doc.get("lead_record") or {}).get("qualification_schema", {}) or {}
    schema_qs = schema.get("question", []) if schema else []
    qna_by_id = {qa.get("id"): qa for qa in (analysis.get("qna") or []) if qa.get("id")}
    quantity_units_by_id = {
        q.get("id"): q.get("quantity_unit") or []
        for q in schema_qs
        if q.get("type") == "quantity"
    }
    options_by_qid = {q.get("id"): q.get("option") or [] for q in schema_qs}

    # Patch null opt_ids via fuzzy matching (STT digit-drops like "40 GSM" → "140 GSM")
    for qa in qna_by_id.values():
        if not qa.get("opt_id"):
            opts = options_by_qid.get(qa.get("id")) or []
            if opts:
                matched = fuzzy_match_opt_id(str(qa.get("answ", "")), opts)
                if matched:
                    qa["opt_id"] = matched
                    opt_text = next((o.get("text", "") for o in opts if o.get("id") == matched), "")
                    if opt_text:
                        qa["answ"] = opt_text

    ordered_entries: list[dict] = []
    quantity_entries: list[dict] = []
    for q in schema_qs:
        qid = q.get("id")
        qa = qna_by_id.get(qid)
        if not qa or not qa.get("answ"):
            continue
        answ = str(qa.get("answ", "")).strip()
        if q.get("type") == "quantity":
            units = quantity_units_by_id.get(qid) or []
            unit = units[0] if units else None
            if unit and answ and answ.lower() not in {"not sure", "पता नहीं"}:
                if not any(u and u.lower() in answ.lower() for u in units):
                    answ = f"{answ} {unit}".strip()
        entry = {
            "Qid": qid or "",
            "Quest": qa.get("quest", "") or q.get("text", ""),
            "Answ": answ,
            "OptId": qa.get("opt_id"),
        }
        if q.get("type") == "quantity":
            quantity_entries.append(entry)
        else:
            ordered_entries.append(entry)

    MAX_SPEC = 4
    if quantity_entries:
        final_entries = ordered_entries[: MAX_SPEC - 1] + [quantity_entries[0]]
    else:
        final_entries = ordered_entries[:MAX_SPEC]

    spec_ques: dict = {}
    for i, entry in enumerate(final_entries, 1):
        spec_ques[f"spec_ques_{i}"] = entry
    payload.update(spec_ques)

    return payload
