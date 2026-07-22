from fastapi import APIRouter, Depends, HTTPException

from .. import campaign_execution, dialer_webhooks
from ..audit import log_audit
from ..auth import require_user
from ..db import campaigns
from ..models import DialerCallStatusWebhook, DialerWebhookSecretUpdate

router = APIRouter(prefix="/api/dialer-webhooks", tags=["dialer-webhooks"])


@router.post("/{service_id}/call-complete")
def call_complete(service_id: str, secret: str, payload: DialerCallStatusWebhook) -> dict:
    """TSPL's call-completion webhook (per Avi's confirmation this is "feasible... configured
    separately for each Service ID"). PLACEHOLDER: exact field names/status vocabulary will
    need adjusting once TSPL sends their real sample payload — the shape of this endpoint
    (secret-gated, resolves a call_job, calls complete_job) should not need to change."""
    if not dialer_webhooks.verify_secret(service_id, secret):
        raise HTTPException(401, "Invalid or missing webhook secret for this service_id")

    job = dialer_webhooks.resolve_job(payload)
    if not job:
        raise HTTPException(404, "No matching in-flight call found for this webhook")

    mapped_status = dialer_webhooks.map_dialer_status(payload.status)
    campaign_execution.complete_job(
        job["_id"],
        mapped_status,
        call_id=payload.call_id or str(job.get("call_id") or job["_id"]),
        recording_url=payload.recording_url,
    )

    campaign_key = job["campaign_id"]
    campaign = campaigns.find_one({"campaign_key": campaign_key})
    if campaign:
        new_status = campaign_execution.derive_campaign_status(campaign_key, campaign.get("status"))
        if campaign.get("status") != new_status:
            campaigns.update_one({"campaign_key": campaign_key}, {"$set": {"status": new_status}})

    log_audit(
        {"sub": f"dialer-webhook:{service_id}", "role": "system"},
        "dialer_call_complete",
        "campaign",
        campaign_key,
        {"job_id": str(job["_id"]), "status": mapped_status, "raw_status": payload.status},
    )
    return {"ok": True, "job_id": str(job["_id"]), "status": mapped_status}


@router.get("/{service_id}/secret")
def get_webhook_secret(service_id: str, _: dict = Depends(require_user)) -> dict:
    secret = dialer_webhooks.get_secret(service_id)
    return {"service_id": service_id, "configured": bool(secret)}


@router.put("/{service_id}/secret")
def set_webhook_secret(service_id: str, payload: DialerWebhookSecretUpdate, user: dict = Depends(require_user)) -> dict:
    dialer_webhooks.set_secret(service_id, payload.secret)
    log_audit(user, "update", "dialer_webhook_secret", service_id)
    return {"service_id": service_id, "configured": True}
