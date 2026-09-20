def _create_bot(client, auth_headers, name="Audit Test Bot"):
    resp = client.post(
        "/api/bots",
        json={"name": name, "description": "d", "config": {"organization_name": "Acmecorp"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_audit_log_empty_by_default(client, auth_headers):
    resp = client.get("/api/audit-log", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


def test_creating_a_bot_records_an_audit_entry(client, auth_headers):
    bot = _create_bot(client, auth_headers)

    resp = client.get("/api/audit-log", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    entry = body["items"][0]
    assert entry["action"] == "create"
    assert entry["resource_type"] == "bot"
    assert entry["resource_id"] == bot["_id"]
    assert entry["actor"]  # populated from the JWT's `sub` claim
    assert entry["created_at"]


def test_publish_and_rollback_record_audit_entries(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    publish_resp = client.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)
    assert publish_resp.status_code == 200, publish_resp.text
    version_id = publish_resp.json()["active_version_id"]

    rollback_resp = client.post(f"/api/bots/{bot['_id']}/rollback/{version_id}", headers=auth_headers)
    assert rollback_resp.status_code == 200, rollback_resp.text

    resp = client.get("/api/audit-log?resource_type=bot", headers=auth_headers)
    actions = [e["action"] for e in resp.json()["items"]]
    assert "create" in actions
    assert "publish" in actions
    assert "rollback" in actions


def test_audit_log_filters_by_resource_type(client, auth_headers):
    _create_bot(client, auth_headers)
    client.post(
        "/api/phone-numbers",
        json={"number": "+911234500000", "environment": "dev"},
        headers=auth_headers,
    )

    resp = client.get("/api/audit-log?resource_type=phone_number", headers=auth_headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["resource_type"] == "phone_number"


def test_audit_log_requires_auth(client):
    resp = client.get("/api/audit-log")
    assert resp.status_code == 401
