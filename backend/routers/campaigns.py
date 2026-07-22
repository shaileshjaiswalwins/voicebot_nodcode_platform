import csv
import io
import re

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import campaign_execution
from ..audit import log_audit
from ..auth import require_user
from ..db import bots, campaign_leads, campaigns
from ..models import (
    AssignBotRequest,
    CampaignLeadUploadResult,
    PromptTemplateRequest,
    PromptValidationResult,
    SaveStrategyRequest,
    SetStatusRequest,
)

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
VAR_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

BUILTIN_VARS = {"name", "phone_number"}


def _known_vars_for_campaign(campaign_key: str) -> set[str]:
    known = set(BUILTIN_VARS)
    for lead in campaign_leads.find({"campaign_id": campaign_key}, {"vars": 1}):
        known.update(lead.get("vars", {}).keys())
    return known


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    if "lead_id" in doc:
        doc["lead_id"] = str(doc["lead_id"])
    return doc


@router.get("")
def list_campaigns(_: dict = Depends(require_user)) -> list[dict]:
    return [_serialize(c) for c in campaigns.find({})]


@router.put("/{campaign_key}/strategy")
def save_strategy(campaign_key: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    body = SaveStrategyRequest(**payload)
    campaigns.update_one(
        {"campaign_key": campaign_key},
        {
            "$set": {
                "campaign_key": campaign_key,
                "name": body.name,
                "dialing_strategy": body.strategy.model_dump(),
            }
        },
        upsert=True,
    )
    doc = campaigns.find_one({"campaign_key": campaign_key})
    log_audit(user, "update_strategy", "campaign", campaign_key)
    return _serialize(doc)


@router.put("/{campaign_key}/bot")
def assign_bot(campaign_key: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    body = AssignBotRequest(**payload)
    try:
        bot_oid = ObjectId(body.bot_id)
    except Exception as exc:
        raise HTTPException(404, "Bot not found") from exc
    if not bots.find_one({"_id": bot_oid}):
        raise HTTPException(404, "Bot not found")

    campaigns.update_one({"campaign_key": campaign_key}, {"$set": {"bot_id": body.bot_id}}, upsert=True)
    log_audit(user, "assign_bot", "campaign", campaign_key, {"bot_id": body.bot_id})
    return _serialize(campaigns.find_one({"campaign_key": campaign_key}))


@router.put("/{campaign_key}/status")
def set_status(campaign_key: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    body = SetStatusRequest(**payload)
    result = campaigns.update_one({"campaign_key": campaign_key}, {"$set": {"status": body.status}})
    if result.matched_count == 0:
        raise HTTPException(404, "Campaign not found")
    log_audit(user, "set_status", "campaign", campaign_key, {"status": body.status})
    return _serialize(campaigns.find_one({"campaign_key": campaign_key}))


@router.post("/{campaign_key}/leads")
async def upload_leads(
    campaign_key: str, file: UploadFile = File(...), user: dict = Depends(require_user)
) -> CampaignLeadUploadResult:
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or "phone_number" not in reader.fieldnames:
        raise HTTPException(400, "CSV must have a phone_number column")

    total = 0
    imported = 0
    docs = []
    for row in reader:
        total += 1
        phone = (row.get("phone_number") or "").strip()
        if not E164_RE.match(phone):
            continue
        vars_ = {
            k: v for k, v in row.items() if k not in ("phone_number", "name") and v is not None
        }
        docs.append(
            {
                "campaign_id": campaign_key,
                "phone_number": phone,
                "name": (row.get("name") or "").strip() or "Customer",
                "vars": vars_,
                "status": "pending",
            }
        )
        imported += 1

    if docs:
        campaign_leads.insert_many(docs)

    log_audit(user, "upload_leads", "campaign", campaign_key, {"total": total, "imported": imported})
    return CampaignLeadUploadResult(total=total, imported=imported, skipped=total - imported)


@router.get("/{campaign_key}/leads")
def list_leads(
    campaign_key: str, status: str | None = None, _: dict = Depends(require_user)
) -> list[dict]:
    query: dict = {"campaign_id": campaign_key}
    if status:
        query["status"] = status
    return [_serialize(d) for d in campaign_leads.find(query)]


@router.put("/{campaign_key}/prompt")
def save_prompt_template(
    campaign_key: str, payload: dict, user: dict = Depends(require_user)
) -> dict:
    body = PromptTemplateRequest(**payload)
    campaigns.update_one(
        {"campaign_key": campaign_key},
        {"$set": {"campaign_key": campaign_key, "prompt_template": body.prompt_template}},
        upsert=True,
    )
    log_audit(user, "update_prompt_template", "campaign", campaign_key)
    return _serialize(campaigns.find_one({"campaign_key": campaign_key}))


@router.post("/{campaign_key}/prompt/validate")
def validate_prompt_template(
    campaign_key: str, payload: dict, _: dict = Depends(require_user)
) -> PromptValidationResult:
    body = PromptTemplateRequest(**payload)
    used = set(VAR_TOKEN_RE.findall(body.prompt_template))
    known = _known_vars_for_campaign(campaign_key)
    return PromptValidationResult(unknown_vars=sorted(used - known))


@router.post("/{campaign_key}/start")
def start_campaign(campaign_key: str, user: dict = Depends(require_user)) -> dict:
    """Enqueue call_jobs for any pending leads and mark the campaign active. Idempotent —
    calling this again after uploading more leads just enqueues the new ones."""
    if not campaigns.find_one({"campaign_key": campaign_key}):
        raise HTTPException(404, "Campaign not found")
    enqueued = campaign_execution.enqueue_pending_leads(campaign_key)
    status = campaign_execution.derive_campaign_status(campaign_key, "active")
    campaigns.update_one({"campaign_key": campaign_key}, {"$set": {"status": status}})
    log_audit(user, "start_campaign", "campaign", campaign_key, {"enqueued": enqueued})
    return {"enqueued": enqueued, **campaign_execution.progress_counts(campaign_key)}


@router.get("/{campaign_key}/progress")
def get_progress(campaign_key: str, _: dict = Depends(require_user)) -> dict:
    counts = campaign_execution.progress_counts(campaign_key)
    campaign = campaigns.find_one({"campaign_key": campaign_key})
    if campaign:
        new_status = campaign_execution.derive_campaign_status(campaign_key, campaign.get("status"))
        if campaign.get("status") != new_status:
            campaigns.update_one({"campaign_key": campaign_key}, {"$set": {"status": new_status}})
    return counts


@router.post("/{campaign_key}/claim")
def claim_job(campaign_key: str, _: dict = Depends(require_user)) -> dict | None:
    """Claim the next queued job for a dialer to place a call for. Returns null when the
    campaign is paused, exhausted, or doesn't exist — callers should treat null as
    'nothing to do right now', not an error."""
    job = campaign_execution.claim_next_job(campaign_key)
    return _serialize(job) if job else None


@router.post("/{campaign_key}/jobs/{job_id}/complete")
def complete_job(campaign_key: str, job_id: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    status = payload.get("status")
    if status not in campaign_execution.TERMINAL_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(campaign_execution.TERMINAL_STATUSES)}")
    try:
        job_oid = ObjectId(job_id)
    except Exception as exc:
        raise HTTPException(404, "Job not found") from exc
    job = campaign_execution.complete_job(
        job_oid, status, payload.get("call_id"), payload.get("cost_inr"), payload.get("latency_ms")
    )
    if not job:
        raise HTTPException(404, "Job not found")
    campaign = campaigns.find_one({"campaign_key": campaign_key})
    new_status = campaign_execution.derive_campaign_status(campaign_key, campaign.get("status") if campaign else None)
    if campaign and campaign.get("status") != new_status:
        campaigns.update_one({"campaign_key": campaign_key}, {"$set": {"status": new_status}})
    log_audit(user, "complete_call_job", "campaign", campaign_key, {"job_id": job_id, "status": status})
    return _serialize(job)


@router.get("/{campaign_key}/calls/{call_id}")
def get_call_detail(campaign_key: str, call_id: str, _: dict = Depends(require_user)) -> dict:
    detail = campaign_execution.get_call_detail(call_id)
    if not detail:
        raise HTTPException(404, "Call not found")
    return detail


@router.delete("/{campaign_key}")
def delete_campaign(campaign_key: str, user: dict = Depends(require_user)) -> dict:
    result = campaigns.delete_one({"campaign_key": campaign_key})
    if result.deleted_count == 0:
        raise HTTPException(404, "Campaign not found")
    log_audit(user, "delete", "campaign", campaign_key)
    return {"ok": True}
