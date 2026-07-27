"""Per-Service-ID webhook secrets for the TSPL dialer call-completion callback.

Avi/TSPL's reply confirmed the webhook can be "configured separately for each Service ID" —
this is that configuration: one secret per service_id, checked as a query param on the
inbound webhook (backend/routers/dialer_webhooks.py) since TSPL's server can't hold one of
our admin JWTs. Same singleton-per-key pattern as pricing_config.py / analysis_prompts.py.
"""

from . import campaign_execution
from .db import dialer_webhook_secrets

# PLACEHOLDER — TSPL's actual status vocabulary is unknown until they send a sample payload.
# Anything not recognized as a failure defaults to "completed" rather than raising, since a
# webhook 500 would make TSPL retry indefinitely for a status string we just haven't seen yet.
_FAILURE_STATUSES = {"failed", "no_answer", "busy", "rejected", "not_connected", "error"}


def map_dialer_status(raw_status: str) -> str:
    normalized = (raw_status or "").strip().lower().replace(" ", "_")
    return "failed" if normalized in _FAILURE_STATUSES else "completed"


def resolve_job(payload) -> dict | None:
    """Finds the call_job this webhook is about. Prefers job_id/call_id (the correct,
    correlation-ID-based way — TSPL should echo back whatever ID we hand them when the call
    is placed), falls back to a best-effort phone-number match. See
    campaign_execution.find_in_progress_job_by_phone for the fallback's caveat.

    `job_id` is matched against `push_ref_id` first — the value actually sent as
    `ref_obj._id` on the push (see campaign_execution.mint_push_ref; TSPL requires this to
    be unique per push attempt, so it's minted fresh each retry and is distinct from the
    job's own _id). Falls back to the job's own _id for any older in-flight job pushed
    before push_ref_id existed."""
    from bson import ObjectId

    if payload.job_id:
        job = campaign_execution.call_jobs.find_one({"push_ref_id": payload.job_id})
        if job:
            return job
        try:
            job = campaign_execution.call_jobs.find_one({"_id": ObjectId(payload.job_id)})
            if job:
                return job
        except Exception:
            pass
    if payload.call_id:
        job = campaign_execution.call_jobs.find_one({"call_id": payload.call_id})
        if job:
            return job
    if payload.phone_number:
        return campaign_execution.find_in_progress_job_by_phone(payload.phone_number)
    return None


def get_secret(service_id: str) -> str | None:
    doc = dialer_webhook_secrets.find_one({"_id": service_id})
    return doc.get("secret") if doc else None


def set_secret(service_id: str, secret: str) -> None:
    dialer_webhook_secrets.update_one(
        {"_id": service_id}, {"$set": {"secret": secret}}, upsert=True
    )


def verify_secret(service_id: str, provided: str | None) -> bool:
    expected = get_secret(service_id)
    return bool(expected) and provided == expected
