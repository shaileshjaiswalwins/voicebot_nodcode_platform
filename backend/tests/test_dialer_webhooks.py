from backend import campaign_execution, dialer_webhooks
from backend.db import call_jobs, call_logs, campaign_leads, campaigns


def _seed_and_claim(campaign_key: str, phone_number: str = "+919876543210") -> dict:
    campaigns.update_one({"campaign_key": campaign_key}, {"$set": {"campaign_key": campaign_key}}, upsert=True)
    campaign_leads.insert_one(
        {"campaign_id": campaign_key, "phone_number": phone_number, "name": "x", "vars": {}, "status": "pending"}
    )
    campaign_execution.enqueue_pending_leads(campaign_key)
    return campaign_execution.claim_next_job(campaign_key)


def test_map_dialer_status_recognizes_known_failure_strings():
    assert dialer_webhooks.map_dialer_status("no_answer") == "failed"
    assert dialer_webhooks.map_dialer_status("Busy") == "failed"
    assert dialer_webhooks.map_dialer_status("NOT CONNECTED") == "failed"


def test_map_dialer_status_defaults_unknown_strings_to_completed():
    # Deliberate: an unrecognized status must not 500 the webhook (TSPL would retry forever).
    assert dialer_webhooks.map_dialer_status("answered_by_customer") == "completed"
    assert dialer_webhooks.map_dialer_status("") == "completed"


def test_secret_roundtrip(client):
    assert dialer_webhooks.get_secret("svc-1") is None
    dialer_webhooks.set_secret("svc-1", "topsecret")
    assert dialer_webhooks.get_secret("svc-1") == "topsecret"
    assert dialer_webhooks.verify_secret("svc-1", "topsecret") is True
    assert dialer_webhooks.verify_secret("svc-1", "wrong") is False
    assert dialer_webhooks.verify_secret("svc-1", None) is False


def test_call_complete_endpoint_rejects_missing_secret(client):
    resp = client.post("/api/dialer-webhooks/svc-2/call-complete?secret=whatever", json={"status": "completed"})
    assert resp.status_code == 401


def test_call_complete_endpoint_resolves_job_by_phone_number_and_completes_it(client):
    dialer_webhooks.set_secret("svc-3", "s3cret")
    job = _seed_and_claim("dialer_webhook_camp", "+919876543211")

    resp = client.post(
        "/api/dialer-webhooks/svc-3/call-complete?secret=s3cret",
        json={"phone_number": "+919876543211", "status": "answered", "recording_url": "https://rec/x.wav", "call_id": "tspl-call-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["job_id"] == str(job["_id"])

    updated_job = call_jobs.find_one({"_id": job["_id"]})
    assert updated_job["status"] == "completed"
    log = call_logs.find_one({"call_id": "tspl-call-1"})
    assert log["recording_url"] == "https://rec/x.wav"

    updated_lead = campaign_leads.find_one({"_id": job["lead_id"]})
    assert updated_lead["status"] == "completed"


def test_call_complete_endpoint_maps_failure_status_and_flips_campaign_to_completed(client):
    dialer_webhooks.set_secret("svc-4", "s3cret")
    job = _seed_and_claim("dialer_webhook_fail_camp", "+919876543212")

    resp = client.post(
        "/api/dialer-webhooks/svc-4/call-complete?secret=s3cret",
        json={"job_id": str(job["_id"]), "status": "no_answer"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    assert campaigns.find_one({"campaign_key": "dialer_webhook_fail_camp"})["status"] == "completed"


def test_call_complete_endpoint_404_when_nothing_matches(client):
    dialer_webhooks.set_secret("svc-5", "s3cret")
    resp = client.post(
        "/api/dialer-webhooks/svc-5/call-complete?secret=s3cret",
        json={"phone_number": "+910000000000", "status": "completed"},
    )
    assert resp.status_code == 404


def test_secret_admin_endpoints_require_auth(client):
    resp = client.get("/api/dialer-webhooks/svc-6/secret")
    assert resp.status_code == 401


def test_secret_admin_endpoints_set_and_check(client, auth_headers):
    get_before = client.get("/api/dialer-webhooks/svc-7/secret", headers=auth_headers).json()
    assert get_before["configured"] is False

    put_resp = client.put("/api/dialer-webhooks/svc-7/secret", json={"secret": "hunter2"}, headers=auth_headers)
    assert put_resp.status_code == 200

    get_after = client.get("/api/dialer-webhooks/svc-7/secret", headers=auth_headers).json()
    assert get_after["configured"] is True
    # Never echoes the actual secret back, same write-only convention as sip_password.
    assert "secret" not in get_after or get_after.get("secret") != "hunter2"
