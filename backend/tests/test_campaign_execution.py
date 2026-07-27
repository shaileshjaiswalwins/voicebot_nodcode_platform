from datetime import datetime, timedelta, timezone

from bson import ObjectId

from backend import campaign_execution
from backend.db import call_jobs, campaign_leads, campaigns, transcripts


def _seed_leads(campaign_key, n=3):
    campaigns.update_one({"campaign_key": campaign_key}, {"$set": {"campaign_key": campaign_key, "name": "t"}}, upsert=True)
    for i in range(n):
        campaign_leads.insert_one(
            {"campaign_id": campaign_key, "phone_number": f"+9198765432{i:02d}", "name": "x", "vars": {}, "status": "pending"}
        )


def test_enqueue_pending_leads_creates_one_job_per_lead(client):
    _seed_leads("exec_camp1", 3)
    n = campaign_execution.enqueue_pending_leads("exec_camp1")
    assert n == 3
    assert call_jobs.count_documents({"campaign_id": "exec_camp1"}) == 3


def test_enqueue_pending_leads_is_idempotent(client):
    _seed_leads("exec_camp2", 2)
    campaign_execution.enqueue_pending_leads("exec_camp2")
    n_second = campaign_execution.enqueue_pending_leads("exec_camp2")
    assert n_second == 0
    assert call_jobs.count_documents({"campaign_id": "exec_camp2"}) == 2


def test_claim_next_job_returns_none_when_no_jobs(client):
    campaigns.update_one({"campaign_key": "empty_camp"}, {"$set": {"campaign_key": "empty_camp", "status": "active"}}, upsert=True)
    assert campaign_execution.claim_next_job("empty_camp") is None


def test_claim_next_job_returns_none_when_campaign_paused(client):
    _seed_leads("paused_camp", 1)
    campaign_execution.enqueue_pending_leads("paused_camp")
    campaigns.update_one({"campaign_key": "paused_camp"}, {"$set": {"status": "paused"}})
    assert campaign_execution.claim_next_job("paused_camp") is None


def test_claim_next_job_returns_none_when_campaign_missing(client):
    assert campaign_execution.claim_next_job("nonexistent_camp") is None


def test_two_claims_never_return_the_same_job(client):
    """Regression guard for the exact bug class the atomic find_one_and_update pattern
    (mirrored from callback_worker.worker._claim_one) exists to prevent: two dialer
    workers polling concurrently must never both grab the same lead."""
    _seed_leads("race_camp", 1)
    campaigns.update_one({"campaign_key": "race_camp"}, {"$set": {"status": "active"}})
    campaign_execution.enqueue_pending_leads("race_camp")

    first = campaign_execution.claim_next_job("race_camp")
    second = campaign_execution.claim_next_job("race_camp")
    assert first is not None
    assert second is None


def test_stale_in_progress_job_is_reclaimable_after_lease_timeout(client):
    _seed_leads("stale_camp", 1)
    campaigns.update_one({"campaign_key": "stale_camp"}, {"$set": {"status": "active"}})
    campaign_execution.enqueue_pending_leads("stale_camp")
    job = campaign_execution.claim_next_job("stale_camp")
    assert job is not None

    # Simulate a crashed worker: claimed_at far enough in the past to exceed LEASE_TIMEOUT.
    call_jobs.update_one(
        {"_id": job["_id"]},
        {"$set": {"claimed_at": datetime.now(timezone.utc) - campaign_execution.LEASE_TIMEOUT - timedelta(minutes=1)}},
    )
    reclaimed = campaign_execution.claim_next_job("stale_camp")
    assert reclaimed is not None
    assert reclaimed["_id"] == job["_id"]


def test_complete_job_rejects_non_terminal_status(client):
    _seed_leads("complete_camp", 1)
    campaigns.update_one({"campaign_key": "complete_camp"}, {"$set": {"status": "active"}})
    campaign_execution.enqueue_pending_leads("complete_camp")
    job = campaign_execution.claim_next_job("complete_camp")
    try:
        campaign_execution.complete_job(job["_id"], "queued")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_progress_counts_reflects_job_statuses(client):
    _seed_leads("progress_camp", 2)
    campaigns.update_one({"campaign_key": "progress_camp"}, {"$set": {"status": "active"}})
    campaign_execution.enqueue_pending_leads("progress_camp")
    job = campaign_execution.claim_next_job("progress_camp")
    campaign_execution.complete_job(job["_id"], "completed")

    counts = campaign_execution.progress_counts("progress_camp")
    assert counts == {"queued": 1, "in_progress": 0, "dialing": 0, "completed": 1, "failed": 0, "total": 2}


def test_start_progress_claim_complete_endpoints(client, auth_headers):
    _seed_leads("api_camp", 2)
    resp = client.post("/api/campaigns/api_camp/start", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["enqueued"] == 2
    assert resp.json()["queued"] == 2

    progress = client.get("/api/campaigns/api_camp/progress", headers=auth_headers).json()
    assert progress["total"] == 2

    claimed = client.post("/api/campaigns/api_camp/claim", headers=auth_headers).json()
    assert claimed is not None

    done = client.post(
        f"/api/campaigns/api_camp/jobs/{claimed['_id']}/complete",
        json={"status": "completed", "call_id": "call_1"},
        headers=auth_headers,
    )
    assert done.status_code == 200
    assert done.json()["status"] == "completed"

    # pause should stop further claims
    client.put("/api/campaigns/api_camp/status", json={"status": "paused"}, headers=auth_headers)
    assert client.post("/api/campaigns/api_camp/claim", headers=auth_headers).json() is None

    # resume brings it back
    client.put("/api/campaigns/api_camp/status", json={"status": "active"}, headers=auth_headers)
    assert client.post("/api/campaigns/api_camp/claim", headers=auth_headers).json() is not None


def test_start_campaign_requires_existing_campaign(client, auth_headers):
    resp = client.post("/api/campaigns/no_such_campaign/start", headers=auth_headers)
    assert resp.status_code == 404


def test_complete_job_syncs_status_and_call_id_back_onto_the_lead(client, auth_headers):
    """CampaignLeadsPanel.tsx's table reads status/call_id straight off the CampaignLead
    doc — if this sync didn't happen, every lead would show 'pending' forever even after
    its call completed, even though the aggregate progress counters were correct."""
    _seed_leads("sync_camp", 1)
    client.post("/api/campaigns/sync_camp/start", headers=auth_headers)
    claimed = client.post("/api/campaigns/sync_camp/claim", headers=auth_headers).json()
    client.post(
        f"/api/campaigns/sync_camp/jobs/{claimed['_id']}/complete",
        json={"status": "completed", "call_id": "call_sync_1"},
        headers=auth_headers,
    )
    leads_listing = client.get("/api/campaigns/sync_camp/leads", headers=auth_headers).json()
    assert leads_listing[0]["status"] == "completed"
    assert leads_listing[0]["call_id"] == "call_sync_1"


def test_complete_job_with_call_id_writes_call_log_and_detail_is_fetchable(client, auth_headers):
    _seed_leads("detail_camp", 1)
    client.post("/api/campaigns/detail_camp/start", headers=auth_headers)
    claimed = client.post("/api/campaigns/detail_camp/claim", headers=auth_headers).json()

    resp = client.post(
        f"/api/campaigns/detail_camp/jobs/{claimed['_id']}/complete",
        json={
            "status": "completed",
            "call_id": "call_xyz",
            "cost_inr": {"stt": 0.25, "llm": 0.87, "tts": 1.60, "telephony": 0.0},
            "latency_ms": {"stt": 150, "llm_ttft": 300, "tts_first_byte": 180},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200

    detail = client.get("/api/campaigns/detail_camp/calls/call_xyz", headers=auth_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "completed"
    assert body["cost_inr"]["llm"] == 0.87
    assert body["latency_ms"]["llm_ttft"] == 300


def test_call_detail_merges_transcript_collection(client, auth_headers):
    _seed_leads("merge_camp", 1)
    client.post("/api/campaigns/merge_camp/start", headers=auth_headers)
    claimed = client.post("/api/campaigns/merge_camp/claim", headers=auth_headers).json()
    client.post(
        f"/api/campaigns/merge_camp/jobs/{claimed['_id']}/complete",
        json={"status": "completed", "call_id": "call_merge_1"},
        headers=auth_headers,
    )
    transcripts.insert_one({
        "call_id": "call_merge_1",
        "transcript": [{"role": "user", "text": "hi"}],
        "call_duration_sec": 42,
        "recording_url": "https://example.com/rec.wav",
        "analysis": {"call_outcome": "interested"},
    })

    detail = client.get("/api/campaigns/merge_camp/calls/call_merge_1", headers=auth_headers).json()
    assert detail["call_duration_sec"] == 42
    assert detail["transcript"] == [{"role": "user", "text": "hi"}]
    assert detail["analysis"]["call_outcome"] == "interested"


def test_call_detail_404_for_unknown_call_id(client, auth_headers):
    resp = client.get("/api/campaigns/x/calls/nonexistent_call", headers=auth_headers)
    assert resp.status_code == 404


def test_complete_job_endpoint_rejects_bad_status(client, auth_headers):
    _seed_leads("bad_status_camp", 1)
    client.post("/api/campaigns/bad_status_camp/start", headers=auth_headers)
    claimed = client.post("/api/campaigns/bad_status_camp/claim", headers=auth_headers).json()
    resp = client.post(
        f"/api/campaigns/bad_status_camp/jobs/{claimed['_id']}/complete",
        json={"status": "queued"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_derive_campaign_status_draft_when_no_jobs_ever_enqueued(client):
    campaigns.update_one({"campaign_key": "never_started"}, {"$set": {"campaign_key": "never_started"}}, upsert=True)
    assert campaign_execution.derive_campaign_status("never_started", None) == "draft"


def test_derive_campaign_status_completed_when_queue_empty(client):
    _seed_leads("status_completed_camp", 1)
    campaign_execution.enqueue_pending_leads("status_completed_camp")
    job = campaign_execution.claim_next_job("status_completed_camp")
    campaign_execution.complete_job(job["_id"], "completed")
    assert campaign_execution.derive_campaign_status("status_completed_camp", "active") == "completed"


def test_derive_campaign_status_leaves_active_paused_alone_while_queue_has_work(client):
    _seed_leads("status_in_progress_camp", 2)
    campaign_execution.enqueue_pending_leads("status_in_progress_camp")
    assert campaign_execution.derive_campaign_status("status_in_progress_camp", "active") == "active"
    assert campaign_execution.derive_campaign_status("status_in_progress_camp", "paused") == "paused"


def test_start_campaign_endpoint_persists_draft_status_when_no_leads(client, auth_headers):
    campaigns.update_one({"campaign_key": "empty_start_camp"}, {"$set": {"campaign_key": "empty_start_camp"}}, upsert=True)
    client.post("/api/campaigns/empty_start_camp/start", headers=auth_headers)
    assert campaigns.find_one({"campaign_key": "empty_start_camp"})["status"] == "draft"


def test_complete_job_endpoint_flips_campaign_to_completed_once_queue_empties(client, auth_headers):
    _seed_leads("auto_complete_camp", 1)
    client.post("/api/campaigns/auto_complete_camp/start", headers=auth_headers)
    claimed = client.post("/api/campaigns/auto_complete_camp/claim", headers=auth_headers).json()
    client.post(
        f"/api/campaigns/auto_complete_camp/jobs/{claimed['_id']}/complete",
        json={"status": "completed"},
        headers=auth_headers,
    )
    assert campaigns.find_one({"campaign_key": "auto_complete_camp"})["status"] == "completed"


def test_mark_job_dialing_sets_status_and_syncs_lead(client):
    _seed_leads("dialing_camp", 1)
    campaigns.update_one({"campaign_key": "dialing_camp"}, {"$set": {"status": "active"}})
    campaign_execution.enqueue_pending_leads("dialing_camp")
    job = campaign_execution.claim_next_job("dialing_camp")

    updated = campaign_execution.mark_job_dialing(job["_id"], {"status": "queued"})
    assert updated["status"] == "dialing"
    assert updated["dialer_response"] == {"status": "queued"}
    assert campaign_leads.find_one({"_id": job["lead_id"]})["status"] == "dialing"


def test_dialing_is_not_terminal_and_not_reclaimed_by_claim_next_job(client):
    """A job pushed to TSPL and awaiting their async callback must not be picked up again
    by claim_next_job — that's what reclaim_stale_dialing (a much longer, separate timeout)
    is for, not the ordinary in_progress lease."""
    _seed_leads("no_reclaim_camp", 1)
    campaigns.update_one({"campaign_key": "no_reclaim_camp"}, {"$set": {"status": "active"}})
    campaign_execution.enqueue_pending_leads("no_reclaim_camp")
    job = campaign_execution.claim_next_job("no_reclaim_camp")
    campaign_execution.mark_job_dialing(job["_id"])
    assert campaign_execution.claim_next_job("no_reclaim_camp") is None


def test_revert_to_queued_makes_job_claimable_again(client):
    _seed_leads("revert_camp", 1)
    campaigns.update_one({"campaign_key": "revert_camp"}, {"$set": {"status": "active"}})
    campaign_execution.enqueue_pending_leads("revert_camp")
    job = campaign_execution.claim_next_job("revert_camp")
    assert job is not None

    campaign_execution.revert_to_queued(job["_id"])
    reclaimed = campaign_execution.claim_next_job("revert_camp")
    assert reclaimed is not None
    assert reclaimed["_id"] == job["_id"]
    assert reclaimed["status"] == "in_progress"


def test_reclaim_stale_dialing_marks_failed_after_timeout(client):
    _seed_leads("stale_dialing_camp", 1)
    campaigns.update_one({"campaign_key": "stale_dialing_camp"}, {"$set": {"status": "active"}})
    campaign_execution.enqueue_pending_leads("stale_dialing_camp")
    job = campaign_execution.claim_next_job("stale_dialing_camp")
    campaign_execution.mark_job_dialing(job["_id"])

    # Simulate TSPL never calling back: push dialed_at into the past.
    call_jobs.update_one(
        {"_id": job["_id"]},
        {"$set": {"dialed_at": datetime.now(timezone.utc) - timedelta(minutes=100)}},
    )
    reclaimed_count = campaign_execution.reclaim_stale_dialing("stale_dialing_camp", timedelta(minutes=45))
    assert reclaimed_count == 1
    assert call_jobs.find_one({"_id": job["_id"]})["status"] == "failed"


def test_reclaim_stale_dialing_leaves_recent_dialing_jobs_alone(client):
    _seed_leads("fresh_dialing_camp", 1)
    campaigns.update_one({"campaign_key": "fresh_dialing_camp"}, {"$set": {"status": "active"}})
    campaign_execution.enqueue_pending_leads("fresh_dialing_camp")
    job = campaign_execution.claim_next_job("fresh_dialing_camp")
    campaign_execution.mark_job_dialing(job["_id"])

    reclaimed_count = campaign_execution.reclaim_stale_dialing("fresh_dialing_camp", timedelta(minutes=45))
    assert reclaimed_count == 0
    assert call_jobs.find_one({"_id": job["_id"]})["status"] == "dialing"


def test_derive_campaign_status_not_completed_while_jobs_are_dialing(client):
    """A regression guard for the exact bug this plan's model change could introduce:
    derive_campaign_status must treat 'dialing' as in-flight, same as queued/in_progress,
    or a campaign with real calls awaiting TSPL's callback would be wrongly marked done."""
    _seed_leads("dialing_not_done_camp", 1)
    campaign_execution.enqueue_pending_leads("dialing_not_done_camp")
    job = campaign_execution.claim_next_job("dialing_not_done_camp")
    campaign_execution.mark_job_dialing(job["_id"])
    assert campaign_execution.derive_campaign_status("dialing_not_done_camp", "active") == "active"


def test_find_in_progress_job_by_phone_matches_dialing_status(client):
    # A dedicated, unlikely-to-collide number — find_in_progress_job_by_phone has no
    # campaign filter by design (it mirrors TSPL's webhook, which doesn't know our
    # campaign_key either), so it must not share a number with any other test's lead.
    unique_phone = "+919999900001"
    campaigns.update_one({"campaign_key": "phone_lookup_camp"}, {"$set": {"campaign_key": "phone_lookup_camp"}}, upsert=True)
    campaign_leads.insert_one(
        {"campaign_id": "phone_lookup_camp", "phone_number": unique_phone, "name": "x", "vars": {}, "status": "pending"}
    )
    campaign_execution.enqueue_pending_leads("phone_lookup_camp")
    job = campaign_execution.claim_next_job("phone_lookup_camp")
    campaign_execution.mark_job_dialing(job["_id"])

    found = campaign_execution.find_in_progress_job_by_phone(unique_phone)
    assert found is not None
    assert found["_id"] == job["_id"]


def test_get_progress_self_heals_stale_completed_status(client, auth_headers):
    """A campaign whose queue emptied out before this feature existed (or via direct DB
    write) should catch up to 'completed' the next time anything polls its progress."""
    _seed_leads("stale_status_camp", 1)
    client.post("/api/campaigns/stale_status_camp/start", headers=auth_headers)
    claimed = client.post("/api/campaigns/stale_status_camp/claim", headers=auth_headers).json()
    campaign_execution.complete_job(ObjectId(claimed["_id"]), "completed")
    campaigns.update_one({"campaign_key": "stale_status_camp"}, {"$set": {"status": "active"}})

    client.get("/api/campaigns/stale_status_camp/progress", headers=auth_headers)

    assert campaigns.find_one({"campaign_key": "stale_status_camp"})["status"] == "completed"


def test_campaign_outcomes_empty_when_no_calls_yet(client, auth_headers):
    _seed_leads("outcomes_empty_camp", 2)
    resp = client.get("/api/campaigns/outcomes_empty_camp/outcomes", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"counts": {}, "total_analyzed": 0, "total_with_calls": 0}


def test_campaign_outcomes_groups_by_call_outcome_and_counts_pending_analysis(client, auth_headers):
    _seed_leads("outcomes_camp", 3)
    client.post("/api/campaigns/outcomes_camp/start", headers=auth_headers)

    for i, outcome in enumerate(["Approved", "Approved", None]):
        claimed = client.post("/api/campaigns/outcomes_camp/claim", headers=auth_headers).json()
        call_id = f"outcome_call_{i}"
        client.post(
            f"/api/campaigns/outcomes_camp/jobs/{claimed['_id']}/complete",
            json={"status": "completed", "call_id": call_id},
            headers=auth_headers,
        )
        if outcome:
            transcripts.insert_one({"call_id": call_id, "analysis": {"call_outcome": outcome}})

    resp = client.get("/api/campaigns/outcomes_camp/outcomes", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] == {"Approved": 2, "Pending analysis": 1}
    assert body["total_analyzed"] == 2
    assert body["total_with_calls"] == 3


def test_revert_to_queued_or_fail_retries_then_gives_up(client, auth_headers):
    """A push that keeps failing (bad dialer_config, TSPL rejecting the payload) must not
    retry forever silently — after max_attempts it should land in push_failed with the
    actual reason visible on both the job and the lead."""
    _seed_leads("push_fail_camp", 1)
    client.post("/api/campaigns/push_fail_camp/start", headers=auth_headers)
    job = campaign_execution.claim_next_job("push_fail_camp")

    for _ in range(2):
        status = campaign_execution.revert_to_queued_or_fail(job["_id"], "TSPL 400: bad channel_id", 3)
        assert status == "queued"
        assert call_jobs.find_one({"_id": job["_id"]})["status"] == "queued"

    status = campaign_execution.revert_to_queued_or_fail(job["_id"], "TSPL 400: bad channel_id", 3)
    assert status == "push_failed"
    final_job = call_jobs.find_one({"_id": job["_id"]})
    assert final_job["status"] == "push_failed"
    assert final_job["attempts"] == 3
    assert final_job["last_failure_reason"] == "TSPL 400: bad channel_id"

    lead = campaign_leads.find_one({"_id": job["lead_id"]})
    assert lead["status"] == "push_failed"
    assert lead["failure_reason"] == "TSPL 400: bad channel_id"


def test_leads_csv_export_covers_rejected_pushfailed_and_completed_rows(client, auth_headers):
    campaigns.update_one(
        {"campaign_key": "results_camp"},
        {"$set": {"campaign_key": "results_camp", "name": "t", "status": "active"}},
        upsert=True,
    )
    campaign_leads.insert_one({
        "campaign_id": "results_camp", "phone_number": "not-a-phone", "jduid": None,
        "name": "Bad Row", "vars": {}, "status": "rejected", "failure_reason": "invalid phone_number format",
    })
    campaign_leads.insert_one({
        "campaign_id": "results_camp", "phone_number": None, "jduid": "jd_push_failed",
        "name": "Stuck", "vars": {}, "status": "push_failed", "failure_reason": "TSPL 400: bad channel_id",
    })
    campaign_leads.insert_one({
        "campaign_id": "results_camp", "phone_number": "+919876500000", "jduid": None,
        "name": "Answered", "vars": {}, "status": "completed", "call_id": "results_call_1",
    })
    transcripts.insert_one({"call_id": "results_call_1", "analysis": {"call_outcome": "Approved"}})

    resp = client.get("/api/campaigns/results_camp/leads.csv", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    rows = resp.text.strip().splitlines()
    assert rows[0] == "jduid,phone_number,name,stage,status,detail"
    body = "\n".join(rows[1:])
    assert "upload,rejected,invalid phone_number format" in body
    assert "dialing,push_failed,TSPL 400: bad channel_id" in body
    assert "called,completed,Approved" in body


def test_mint_push_ref_is_unique_each_call_and_resolvable_by_the_webhook(client, auth_headers):
    """TSPL rejects a push as a Duplicate Lead if ref_obj._id repeats — mint_push_ref must
    hand out a fresh id every call, and dialer_webhooks.resolve_job must be able to find the
    job again via that id once TSPL echoes it back as job_id on the completion webhook."""
    from backend import dialer_webhooks

    _seed_leads("push_ref_camp", 1)
    client.post("/api/campaigns/push_ref_camp/start", headers=auth_headers)
    job = campaign_execution.claim_next_job("push_ref_camp")

    ref1 = campaign_execution.mint_push_ref(job["_id"])
    ref2 = campaign_execution.mint_push_ref(job["_id"])
    assert ref1 != ref2

    class _Payload:
        job_id = ref2
        call_id = None
        phone_number = None

    resolved = dialer_webhooks.resolve_job(_Payload())
    assert resolved is not None
    assert resolved["_id"] == job["_id"]
