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

from fastapi import APIRouter, Request

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
async def mock_vendor_lookup(
    lead_id: str = "",
    mobile: str = "",
    owner_name: str = "",
    business_name: str = "",
    business_category: str = "",
) -> dict:
    """Returns a fixed sample vendor record — test/demo data only. `lead_id`/`mobile`
    are accepted (unused) so this has the same call shape a real lookup endpoint would.
    `owner_name`/`business_name`/`business_category` let a tester override the demo
    persona from the dashboard's Test Inputs "Pre-call Parameters" panel, without a
    code change, by passing them as this pre_call function's query_params."""
    vendor = dict(_SAMPLE_VENDOR)
    if owner_name:
        vendor["owner_name"] = owner_name
    if business_name:
        vendor["business_name"] = business_name
    if business_category:
        vendor["business_category"] = business_category
    return vendor


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


@router.post("/api/mock/crm/category-change")
async def mock_category_change(payload: dict) -> dict:
    """Zero-code action webhook demo target — a CRM/category-change call a bot can make
    mid-call via a during_call custom function (backend/seed_demo_showcase_bots.py's
    "Zero-Code Action Webhooks" bot), rather than the LLM just claiming it did something.
    THROWAWAY TEST DATA — always succeeds; a real CRM integration would validate the
    requested category against the vendor's actual listing and could reject it."""
    lead_id = (payload or {}).get("lead_id", "").strip()
    new_category = (payload or {}).get("new_category", "").strip()
    if not new_category:
        return {"status": "rejected", "reason": "new_category is required"}
    return {
        "status": "updated",
        "lead_id": lead_id or "unknown",
        "new_category": new_category,
        "change_ref": f"CAT-{abs(hash((lead_id, new_category))) % 100000:05d}",
    }


@router.get("/api/mock/echo")
async def mock_echo(request: Request) -> dict:
    """Query-parameters showcase demo target (backend/seed_demo_showcase_bots.py's
    "Query Parameters Showcase" bot) — echoes back whatever query params it received so a
    PM can see, live, that each function's fixed query_params (and any LLM-filled params)
    actually reached the URL. THROWAWAY TEST DATA, not a real integration."""
    return {"received": dict(request.query_params)}
