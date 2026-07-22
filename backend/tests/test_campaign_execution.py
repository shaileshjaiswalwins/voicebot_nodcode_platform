from datetime import datetime, timedelta, timezone

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
    assert counts == {"queued": 1, "in_progress": 0, "completed": 1, "failed": 0, "total": 2}


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
