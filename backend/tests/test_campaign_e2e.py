"""End-to-end smoke test for the outbound campaign platform (plans/03 Day 3): a 10-contact
CSV goes through upload -> start -> pause -> resume -> all jobs completed, with an audit
trail entry at every mutating step along the way."""


def _make_csv(n: int) -> str:
    lines = ["phone_number,name,reason_of_calling"]
    for i in range(n):
        lines.append(f"+9198765400{i:02d},Contact{i},payment reminder")
    return "\n".join(lines) + "\n"


def test_full_campaign_lifecycle_with_audit_trail(client, auth_headers):
    key = "e2e_campaign"

    create_resp = client.put(
        f"/api/campaigns/{key}/strategy",
        json={"name": "E2E Campaign", "strategy": {}},
        headers=auth_headers,
    )
    assert create_resp.status_code == 200

    upload_resp = client.post(
        f"/api/campaigns/{key}/leads",
        files={"file": ("leads.csv", _make_csv(10), "text/csv")},
        headers=auth_headers,
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json() == {"total": 10, "imported": 10, "skipped": 0}

    start_resp = client.post(f"/api/campaigns/{key}/start", headers=auth_headers)
    assert start_resp.status_code == 200
    assert start_resp.json()["enqueued"] == 10
    assert start_resp.json()["queued"] == 10

    # Drain half the queue before pausing.
    completed_ids = []
    for _ in range(5):
        claimed = client.post(f"/api/campaigns/{key}/claim", headers=auth_headers).json()
        assert claimed is not None
        done = client.post(
            f"/api/campaigns/{key}/jobs/{claimed['_id']}/complete",
            json={"status": "completed", "call_id": f"call_{claimed['_id']}"},
            headers=auth_headers,
        )
        assert done.status_code == 200
        completed_ids.append(claimed["_id"])

    progress = client.get(f"/api/campaigns/{key}/progress", headers=auth_headers).json()
    assert progress == {"queued": 5, "in_progress": 0, "completed": 5, "failed": 0, "total": 10}

    pause_resp = client.put(f"/api/campaigns/{key}/status", json={"status": "paused"}, headers=auth_headers)
    assert pause_resp.status_code == 200
    assert client.post(f"/api/campaigns/{key}/claim", headers=auth_headers).json() is None

    resume_resp = client.put(f"/api/campaigns/{key}/status", json={"status": "active"}, headers=auth_headers)
    assert resume_resp.status_code == 200

    # Drain the rest.
    for _ in range(5):
        claimed = client.post(f"/api/campaigns/{key}/claim", headers=auth_headers).json()
        assert claimed is not None
        done = client.post(
            f"/api/campaigns/{key}/jobs/{claimed['_id']}/complete",
            json={"status": "completed", "call_id": f"call_{claimed['_id']}"},
            headers=auth_headers,
        )
        assert done.status_code == 200

    final_progress = client.get(f"/api/campaigns/{key}/progress", headers=auth_headers).json()
    assert final_progress == {"queued": 0, "in_progress": 0, "completed": 10, "failed": 0, "total": 10}
    assert client.post(f"/api/campaigns/{key}/claim", headers=auth_headers).json() is None

    audit_resp = client.get("/api/audit-log", params={"resource_type": "campaign"}, headers=auth_headers)
    assert audit_resp.status_code == 200
    actions = {item["action"] for item in audit_resp.json()["items"] if item["resource_id"] == key}
    assert {"upload_leads", "start_campaign", "set_status", "complete_call_job"} <= actions
