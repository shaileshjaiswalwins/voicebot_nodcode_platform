import csv
import io
import re
from datetime import timezone
from zoneinfo import ZoneInfo

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from .. import campaign_execution
from ..audit import log_audit
from ..auth import require_user
from ..db import bots, campaign_leads, campaigns, phone_numbers
from ..models import (
    AssignBotRequest,
    CampaignLeadUploadResult,
    PromptTemplateRequest,
    PromptValidationResult,
    ScheduleCampaignRequest,
    SaveDialerConfigRequest,
    SaveStrategyRequest,
    SetStatusRequest,
)

# TSPL is India-only today (DialerConfig.country defaults to "IN") — scheduled_at is always
# interpreted as this wall-clock timezone, never anything user-selectable.
IST = ZoneInfo("Asia/Kolkata")

# Real columns build_dialer_payload (dialer_client.py) reads off a lead: jduid/phone_number/
# name are top-level CampaignLead fields, the rest come out of CampaignLead.vars verbatim.
# Keep this in sync with dialer_client.py::build_dialer_payload — the whole point of the
# downloadable template is that it matches what actually reaches TSPL.
LEADS_TEMPLATE_COLUMNS = [
    "jduid",
    "phone_number",
    "name",
    "buyer_city",
    "buyer_area",
    "searched_keyword",
    "ncatid",
    "jdmart_id",
    "product_id",
    "flow",
]
LEADS_TEMPLATE_EXAMPLE_ROW = [
    "JD123456789",  # jduid — the real campaign case: TSPL resolves this to a number itself
    "",  # phone_number left blank on purpose — not required when jduid is present
    "Customer",
    "Mumbai",
    "Andheri",
    "packers and movers",
    # ncatid/jdmart_id/product_id/flow: confirmed-working values (verified 2026-07-24 via a
    # direct push through dialer_client.push_lead_to_dialer using these exact values, TSPL
    # returned {"code": 201, "msg": "success"}). Earlier placeholder values here (ncatid
    # 12345, jdmart_id "JM7890", product_id "PRD001") got rejected by TSPL as
    # {"code": 202, "msg": "config validation failed"} — TSPL validates ncatid/jdmart_id/
    # product_id against real category/product records, not just the channel config, so a
    # made-up ncatid is enough to fail the whole push. 10551087 is a real ncatid; leaving
    # jdmart_id/product_id/flow blank is the safe default unless you have real values for
    # your specific leads.
    "10551087",
    "",
    "",
    "",
]

# DialerConfig fields with no default that build_dialer_payload reads unconditionally —
# saving a campaign without these produces a payload TSPL will likely reject.
REQUIRED_DIALER_FIELDS = ("channel_name", "channel_id", "bd", "service_source")

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


@router.put("/{campaign_key}/dialer-config")
def save_dialer_config(campaign_key: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    body = SaveDialerConfigRequest(**payload)
    cfg = body.dialer_config

    missing = [f for f in REQUIRED_DIALER_FIELDS if getattr(cfg, f) in (None, "")]
    if missing:
        raise HTTPException(
            400,
            f"dialer_config is missing required field(s): {', '.join(missing)} — "
            "TSPL's push payload reads these unconditionally, with no default.",
        )
    if cfg.service_id and not phone_numbers.find_one({"service_id": cfg.service_id}):
        raise HTTPException(404, f"No phone number is bound to service_id {cfg.service_id!r}")

    campaigns.update_one(
        {"campaign_key": campaign_key},
        {"$set": {"campaign_key": campaign_key, "dialer_config": cfg.model_dump()}},
        upsert=True,
    )
    log_audit(user, "update_dialer_config", "campaign", campaign_key)
    return _serialize(campaigns.find_one({"campaign_key": campaign_key}))


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


@router.get("/leads-template.csv")
def download_leads_template(_: dict = Depends(require_user)) -> PlainTextResponse:
    """A CSV a PM can fill in and re-upload as-is. Columns match exactly what upload_leads
    accepts and what dialer_client.py::build_dialer_payload reads — not a simplified guess.
    The example row is jduid-only (no phone_number) since that's the real campaign case:
    TSPL resolves jduid to a number on its side, we never see the number itself."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(LEADS_TEMPLATE_COLUMNS)
    writer.writerow(LEADS_TEMPLATE_EXAMPLE_ROW)
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads-template.csv"},
    )


@router.post("/{campaign_key}/leads")
async def upload_leads(
    campaign_key: str, file: UploadFile = File(...), user: dict = Depends(require_user)
) -> CampaignLeadUploadResult:
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    fieldnames = reader.fieldnames or []
    if "phone_number" not in fieldnames and "jduid" not in fieldnames:
        raise HTTPException(400, "CSV must have a phone_number or jduid column")

    total = 0
    imported = 0
    docs = []
    for row in reader:
        total += 1
        phone = (row.get("phone_number") or "").strip()
        jduid = (row.get("jduid") or "").strip()
        vars_ = {
            k: v for k, v in row.items()
            if k not in ("phone_number", "jduid", "name") and v is not None
        }
        # A row needs a valid phone_number OR a jduid (TSPL-pushed campaigns never see the
        # real number — jduid is opaque, no format to validate beyond "non-empty"). Rejected
        # rows are still persisted (status="rejected" + failure_reason) rather than dropped,
        # so a PM can see exactly which rows failed and why via the leads table/export
        # instead of just a bare "skipped" count.
        if phone and not E164_RE.match(phone):
            failure_reason = f"invalid phone_number format: {phone!r}"
        elif not phone and not jduid:
            failure_reason = "missing phone_number and jduid"
        else:
            failure_reason = None

        docs.append(
            {
                "campaign_id": campaign_key,
                "phone_number": phone or None,
                "jduid": jduid or None,
                "name": (row.get("name") or "").strip() or "Customer",
                "vars": vars_,
                "status": "pending" if failure_reason is None else "rejected",
                "failure_reason": failure_reason,
            }
        )
        if failure_reason is None:
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


@router.post("/{campaign_key}/schedule")
def schedule_campaign(campaign_key: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    """Send Now vs Schedule from the batch-call modal. send_now mirrors start_campaign
    exactly (enqueue + go active immediately). Otherwise leads are enqueued now (so they're
    ready the moment the schedule fires) but the campaign is parked in "scheduled" status —
    campaign_dialer_worker's _tick promotes it to "active" once scheduled_at arrives, and
    claim_next_job already refuses to hand out jobs for any non-active status implicitly via
    campaigns not being queried until they're "active" (see worker.py)."""
    if not campaigns.find_one({"campaign_key": campaign_key}):
        raise HTTPException(404, "Campaign not found")
    body = ScheduleCampaignRequest(**payload)

    enqueued = campaign_execution.enqueue_pending_leads(campaign_key)
    if body.send_now:
        status = campaign_execution.derive_campaign_status(campaign_key, "active")
        campaigns.update_one(
            {"campaign_key": campaign_key},
            {"$set": {"status": status}, "$unset": {"scheduled_at": ""}},
        )
        log_audit(user, "start_campaign", "campaign", campaign_key, {"enqueued": enqueued})
    else:
        if body.scheduled_at is None:
            raise HTTPException(400, "scheduled_at is required when send_now is false")
        scheduled_at = body.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=IST)
        campaigns.update_one(
            {"campaign_key": campaign_key},
            {"$set": {"status": "scheduled", "scheduled_at": scheduled_at.astimezone(timezone.utc)}},
        )
        log_audit(
            user, "schedule_campaign", "campaign", campaign_key,
            {"enqueued": enqueued, "scheduled_at": scheduled_at.isoformat()},
        )
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


@router.get("/{campaign_key}/outcomes")
def get_campaign_outcomes(campaign_key: str, _: dict = Depends(require_user)) -> dict:
    return campaign_execution.get_campaign_outcomes(campaign_key)


_STAGE_BY_STATUS = {
    "rejected": "upload",
    "pending": "not dialed yet",
    "queued": "dialing",
    "in_progress": "dialing",
    "dialing": "dialing",
    "push_failed": "dialing",
    "completed": "called",
    "failed": "called",
}


@router.get("/{campaign_key}/leads.csv")
def download_campaign_results(campaign_key: str, _: dict = Depends(require_user)) -> PlainTextResponse:
    """One combined results export: every lead in this campaign with which stage it reached
    and why, so a PM doesn't have to click through leads one at a time to find out what
    happened to a batch. Covers all three failure classes: upload-time rejects
    (failure_reason from upload_leads), TSPL push failures (failure_reason from
    revert_to_queued_or_fail), and completed-call outcomes (call_outcome from the same
    transcripts join get_campaign_outcomes uses)."""
    leads = list(campaign_leads.find({"campaign_id": campaign_key}))
    call_ids = [lead["call_id"] for lead in leads if lead.get("call_id")]
    outcome_by_call_id = campaign_execution.get_lead_outcome_map(call_ids)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["jduid", "phone_number", "name", "stage", "status", "detail"])
    for lead in leads:
        status = lead.get("status", "pending")
        stage = _STAGE_BY_STATUS.get(status, status)
        if status in ("rejected", "push_failed"):
            detail = lead.get("failure_reason") or ""
        elif status in ("completed", "failed") and lead.get("call_id"):
            detail = outcome_by_call_id.get(lead["call_id"]) or "Pending analysis"
        else:
            detail = ""
        writer.writerow([
            lead.get("jduid") or "",
            lead.get("phone_number") or "",
            lead.get("name") or "",
            stage,
            status,
            detail,
        ])

    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={campaign_key}-results.csv"},
    )


@router.delete("/{campaign_key}")
def delete_campaign(campaign_key: str, user: dict = Depends(require_user)) -> dict:
    result = campaigns.delete_one({"campaign_key": campaign_key})
    if result.deleted_count == 0:
        raise HTTPException(404, "Campaign not found")
    log_audit(user, "delete", "campaign", campaign_key)
    return {"ok": True}
