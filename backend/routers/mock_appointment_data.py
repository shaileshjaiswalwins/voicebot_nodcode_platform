"""Mock endpoints backing the appointment-flow demo bot
(backend/seed_appointment_workflow_bot.py) — THROWAWAY TEST DATA, not real
integrations. Swap both for real MIS/CRM + calendar APIs before going live.

- GET  /api/mock/vendor-lookup   — same fixed sample-vendor shape used by the
  reference platform's mock_vendor.py, so the copied Ishita script's
  {{mis.business_name}}-style tokens resolve to something real during testing.
- POST /api/mock/calendar/book   — NEW: an actual calendar-booking call the bot
  makes mid-call via a `function` node (fn-book-appointment), rather than just
  having the LLM record a free-text appointment_time string. Neither this
  platform nor the reference one previously did more than the latter.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

_SAMPLE_VENDOR = {
    "business_name": "Sunrise Electronics",
    "owner_name": "Rajesh Kumar",
    "business_category": "electronics store",
    "category_searches": "40",
    "competitor_name_1": "Bright Electricals",
    "competitor_name_2": "City Electronics",
}


@router.get("/api/mock/vendor-lookup")
async def mock_vendor_lookup(lead_id: str = "", mobile: str = "") -> dict:
    """Returns a fixed sample vendor record regardless of lead_id/mobile —
    test/demo data only. `lead_id`/`mobile` are accepted (unused) so this has
    the same call shape a real lookup endpoint would."""
    return _SAMPLE_VENDOR


@router.post("/api/mock/calendar/book")
async def mock_book_appointment(payload: dict) -> dict:
    """Fake-but-real calendar booking: always confirms the exact requested slot
    (demo-reliability over realism — a real integration would check actual
    availability and sometimes return status="unavailable" with no confirmed_slot,
    which is exactly the shape the workflow's booking-check condition node and
    end-booking-issue fallback branch are built to handle)."""
    requested_time = (payload or {}).get("requested_time", "").strip()
    business_name = (payload or {}).get("business_name", "").strip()
    if not requested_time:
        return {"status": "unavailable", "confirmed_slot": None}
    return {
        "status": "confirmed",
        "confirmed_slot": requested_time,
        "business_name": business_name,
        "booking_ref": f"APT-{abs(hash((business_name, requested_time))) % 100000:05d}",
    }
