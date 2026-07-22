"""Campaign dialer queue: one call_job per lead, claimed atomically so two dialer workers
(or a worker restart) never double-dial the same contact. Deliberately reuses the exact
find_one_and_update + lease-timeout pattern from callback_worker/worker.py's _claim_one
instead of introducing Redis/Celery/BullMQ — this platform has no such infra today, and a
single Mongo collection is enough for a single-process campaign dialer.

NOTE (see plans/03-outbound-campaign-platform.md "Known cross-cutting gap"): this module
only manages job *state* (queued/in_progress/completed/failed/paused). It does not yet spawn
the actual LiveKit call — a real dialer process would call claim_next_job(), then dispatch a
room with bot_id + resolved provider stack in room metadata (the piece phone_numbers.py still
doesn't do), then call complete_job() with the outcome. Wiring that dispatch call is follow-up
work, not done here.
"""

from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument

from .db import call_jobs, call_logs, campaign_leads, campaigns, transcripts

LEASE_TIMEOUT = timedelta(minutes=10)

TERMINAL_STATUSES = {"completed", "failed"}


def enqueue_pending_leads(campaign_key: str) -> int:
    """Create a call_job for every lead that doesn't have one yet. Idempotent — safe to
    call every time a campaign is (re)started, e.g. after new leads are uploaded mid-run."""
    existing_lead_ids = {
        j["lead_id"] for j in call_jobs.find({"campaign_id": campaign_key}, {"lead_id": 1})
    }
    to_insert = [
        {
            "campaign_id": campaign_key,
            "lead_id": lead["_id"],
            "phone_number": lead["phone_number"],
            "status": "queued",
            "created_at": datetime.now(timezone.utc),
        }
        for lead in campaign_leads.find({"campaign_id": campaign_key, "status": "pending"})
        if lead["_id"] not in existing_lead_ids
    ]
    if to_insert:
        call_jobs.insert_many(to_insert)
    return len(to_insert)


def claim_next_job(campaign_key: str) -> dict | None:
    """Atomically claim one queued (or stale in_progress) job for this campaign — but only
    if the campaign itself isn't paused. This is what makes Pause actually stop new calls
    from being placed, rather than just being a UI label (the pre-existing PUT .../status
    endpoint only ever set a free-text field with no enforcement)."""
    campaign = campaigns.find_one({"campaign_key": campaign_key})
    if not campaign or campaign.get("status") == "paused":
        return None

    now = datetime.now(timezone.utc)
    stale_cutoff = now - LEASE_TIMEOUT
    return call_jobs.find_one_and_update(
        {
            "campaign_id": campaign_key,
            "$or": [
                {"status": "queued"},
                {"status": "in_progress", "claimed_at": {"$lt": stale_cutoff}},
            ],
        },
        {"$set": {"status": "in_progress", "claimed_at": now}},
        return_document=ReturnDocument.AFTER,
    )


def complete_job(
    job_id,
    status: str,
    call_id: str | None = None,
    cost_inr: dict | None = None,
    latency_ms: dict | None = None,
    recording_url: str | None = None,
) -> dict | None:
    """Record a job's outcome (called by the dialer once a call finishes). `status` must be
    a terminal status — in_progress/queued transitions only ever happen via claim_next_job.
    When a call_id is given, also upserts a call_logs doc (cost/latency breakdown, plus the
    recording path once the dialer webhook starts sending one) so the call detail drawer has
    something to join against the existing `transcripts` collection."""
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"complete_job status must be one of {TERMINAL_STATUSES}, got {status!r}")
    update: dict = {"status": status, "completed_at": datetime.now(timezone.utc)}
    if call_id:
        update["call_id"] = call_id

    job = call_jobs.find_one_and_update(
        {"_id": job_id}, {"$set": update}, return_document=ReturnDocument.AFTER
    )
    if job:
        # Keep the lead's own status/call_id in sync — CampaignLeadsPanel.tsx's table reads
        # status straight off the CampaignLead doc, not the call_jobs collection.
        lead_update: dict = {"status": status}
        if call_id:
            lead_update["call_id"] = call_id
        campaign_leads.update_one({"_id": job["lead_id"]}, {"$set": lead_update})
    if job and call_id:
        log_set: dict = {
            "call_id": call_id,
            "campaign_id": job["campaign_id"],
            "job_id": job["_id"],
            "status": status,
            "cost_inr": cost_inr or {},
            "latency_ms": latency_ms or {},
            "updated_at": datetime.now(timezone.utc),
        }
        if recording_url:
            log_set["recording_url"] = recording_url
        call_logs.update_one({"call_id": call_id}, {"$set": log_set}, upsert=True)
    return job


def find_in_progress_job_by_phone(phone_number: str) -> dict | None:
    """Best-effort job lookup for the dialer webhook when the payload doesn't (yet) echo
    back our own job_id/call_id — assumes at most one call in flight per phone number at a
    time, which holds as long as claim_next_job's lease/atomicity guarantees do. Prefer
    job_id/call_id once TSPL's real payload confirms which one they round-trip."""
    return call_jobs.find_one({"phone_number": phone_number, "status": "in_progress"})


def get_call_detail(call_id: str) -> dict | None:
    """Merge the campaign-side cost/latency breakdown (call_logs) with the existing
    transcripts collection's transcript/duration/recording_url, for the call detail drawer.
    Returns None if neither collection has anything for this call_id."""
    log = call_logs.find_one({"call_id": call_id}) or {}
    transcript_doc = transcripts.find_one({"call_id": call_id}) or {}
    if not log and not transcript_doc:
        return None
    return {
        "call_id": call_id,
        "status": log.get("status"),
        "cost_inr": log.get("cost_inr", {}),
        "latency_ms": log.get("latency_ms", {}),
        "transcript": transcript_doc.get("transcript", []),
        "call_duration_sec": transcript_doc.get("call_duration_sec"),
        "recording_url": transcript_doc.get("recording_url", ""),
        "analysis": transcript_doc.get("analysis", {}),
    }


def progress_counts(campaign_key: str) -> dict:
    counts = {"queued": 0, "in_progress": 0, "completed": 0, "failed": 0, "total": 0}
    for job in call_jobs.find({"campaign_id": campaign_key}, {"status": 1}):
        counts["total"] += 1
        counts[job["status"]] = counts.get(job["status"], 0) + 1
    return counts


DRAFT_STATUS = "draft"
COMPLETED_STATUS = "completed"


def derive_campaign_status(campaign_key: str, current_status: str | None) -> str:
    """Auto-derives a campaign's status from its call_jobs queue state, per plans/03's
    definition: draft = never started (no jobs ever enqueued), completed = every enqueued
    job has left the queue (nothing queued or in_progress — "in-queue" is zero). Otherwise
    the operator's own active/paused choice wins unchanged — this only ever moves a
    campaign INTO draft or completed, never overrides active/paused while dialing is still
    in flight, so it can't fight with claim_next_job's pause enforcement."""
    counts = progress_counts(campaign_key)
    if counts["total"] == 0:
        return DRAFT_STATUS
    if counts["queued"] == 0 and counts["in_progress"] == 0:
        return COMPLETED_STATUS
    return current_status or "active"
