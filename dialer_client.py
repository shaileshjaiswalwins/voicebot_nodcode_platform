"""Push a queued campaign lead to TSPL's outbound-dialer API — the piece that actually
causes a real phone call to happen. TSPL resolves the lead's `jduid` (AcmeCorp's internal
per-user ID) to a real phone number on their side; we never see the number itself.

`campaign_type`/`trigger_reason` are static per Avi's dev sample. Everything else is either
per-lead (jduid, buyer_city, buyer_area, searched_keyword, ncatid, jdmart_id, product_id,
flow — CampaignLead.vars) or per-campaign (channel_name, channel_id, bd, service_id,
service_source, page_type/country/language — Campaign.dialer_config, see backend/models.py's
DialerConfig).

`ref_obj._id` is set to our own call_job's _id so dialer_webhooks.py's existing job_id-first
resolution has something to match against once TSPL's completion-callback payload shape is
known — this is our own design choice for correlation, not a confirmed TSPL contract.
"""

import json
import os

import aiohttp
from loguru import logger

DIALER_PUSH_API_URL = os.getenv(
    "DIALER_PUSH_API_URL", "http://192.168.14.101:3006/leads/ai-lead-qualify/save"
)

STATIC_CAMPAIGN_TYPE = "TARGET_AI_CALL"
STATIC_TRIGGER_REASON = "15_MIN_INACTIVITY"


def build_dialer_payload(lead: dict, campaign: dict, job_id: str) -> dict:
    """Assemble the exact JSON shape TSPL's dev endpoint expects. `lead` is a CampaignLead
    doc (dict, as stored in Mongo — not the Pydantic model), `campaign` is a Campaign doc,
    `job_id` is the call_jobs `_id` (stringified) for this dial attempt."""
    dialer_cfg = campaign.get("dialer_config") or {}
    lead_vars = lead.get("vars") or {}
    jduid = lead.get("jduid") or ""
    buyer_city = lead_vars.get("buyer_city", "")
    buyer_area = lead_vars.get("buyer_area", "")
    searched_keyword = lead_vars.get("searched_keyword", "")

    ncatid_raw = lead_vars.get("ncatid", "")
    try:
        ncatid: int | str = int(ncatid_raw) if ncatid_raw else ""
    except (TypeError, ValueError):
        ncatid = ncatid_raw

    page_type = dialer_cfg.get("page_type") or "gallery_image"

    return {
        "campaign_type": STATIC_CAMPAIGN_TYPE,
        "trigger_reason": STATIC_TRIGGER_REASON,
        "buyer_details": {
            "jduid": jduid,
            "buyer_city": buyer_city,
            "buyer_area": buyer_area,
        },
        "search_context": {
            "searched_keyword": searched_keyword,
        },
        "meta": {
            "page_type": page_type,
            "country": dialer_cfg.get("country") or "IN",
            "language": dialer_cfg.get("language") or "en",
        },
        "ncatid": ncatid,
        "jdmart_id": lead_vars.get("jdmart_id", ""),
        "product_id": lead_vars.get("product_id", ""),
        "flow": lead_vars.get("flow", ""),
        "jduid": jduid,
        "buyer_city": buyer_city,
        "page_type": page_type,
        "ref_obj": {"_id": job_id},
        "channel_name": dialer_cfg.get("channel_name") or "",
        "channel_id": dialer_cfg.get("channel_id"),
        "bd": dialer_cfg.get("bd"),
        "service_id": dialer_cfg.get("service_id") or "",
        "service_source": dialer_cfg.get("service_source") or "",
    }


async def push_lead_to_dialer(
    payload: dict,
    http_session: aiohttp.ClientSession,
    dialer_push_api_url: str = DIALER_PUSH_API_URL,
) -> tuple[bool, dict]:
    """POST a single lead push to TSPL. No internal retry loop — the campaign_dialer_worker
    tick loop already retries failed jobs (they go back to `queued` via revert_to_queued and
    get picked up next tick), so retrying here too would double up backoff behavior."""
    try:
        async with http_session.post(
            dialer_push_api_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            body_text = await resp.text()
            try:
                body = json.loads(body_text) if body_text else {}
            except ValueError:
                body = {"raw": body_text}
            if resp.status in (200, 201):
                logger.info(
                    f"[DIALER] Pushed lead OK | status={resp.status} | jduid={payload.get('jduid')!r}"
                )
                return True, body
            logger.warning(
                f"[DIALER] Push failed | status={resp.status} | body={body_text[:300]!r} | "
                f"jduid={payload.get('jduid')!r}"
            )
            return False, {"status_code": resp.status, "body": body}
    except Exception as e:
        logger.error(
            f"[DIALER] Push errored: {type(e).__name__}: {e} | jduid={payload.get('jduid')!r}"
        )
        return False, {"error": str(e)}
