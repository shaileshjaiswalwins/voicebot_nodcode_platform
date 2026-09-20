def _create_bot(client, auth_headers, name="Test Bot"):
    resp = client.post(
        "/api/bots",
        json={"name": name, "description": "d", "config": {"organization_name": "AcmeCorp"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create_and_publish_bot(client, auth_headers, name="Published Bot"):
    bot = _create_bot(client, auth_headers, name)
    resp = client.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    bot["active_version_id"] = resp.json()["active_version_id"]
    return bot


def test_phone_number_list_empty_by_default(client, auth_headers):
    resp = client.get("/api/phone-numbers", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_phone_number_without_assignment(client, auth_headers):
    resp = client.post(
        "/api/phone-numbers",
        json={"number": "+911234567890", "environment": "dev"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["number"] == "+911234567890"
    assert body["environment"] == "dev"
    assert body["assigned_bot_id"] is None
    assert body["assigned_bot_version_id"] is None
    assert body["status"] == "active"


def test_create_phone_number_with_assignment_to_published_bot(client, auth_headers):
    bot = _create_and_publish_bot(client, auth_headers)
    resp = client.post(
        "/api/phone-numbers",
        json={"number": "+911111111111", "environment": "prod", "assigned_bot_id": bot["_id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assigned_bot_id"] == bot["_id"]
    assert body["assigned_bot_version_id"] == bot["active_version_id"]


def test_create_phone_number_with_assignment_to_unpublished_bot_errors(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    resp = client.post(
        "/api/phone-numbers",
        json={"number": "+922222222222", "environment": "preprod", "assigned_bot_id": bot["_id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_list_phone_numbers_returns_created(client, auth_headers):
    client.post("/api/phone-numbers", json={"number": "+933333333333", "environment": "dev"}, headers=auth_headers)
    resp = client.get("/api/phone-numbers", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_reassign_phone_number_to_published_bot(client, auth_headers):
    phone = client.post(
        "/api/phone-numbers", json={"number": "+944444444444", "environment": "prod"}, headers=auth_headers
    ).json()
    bot = _create_and_publish_bot(client, auth_headers)

    resp = client.put(
        f"/api/phone-numbers/{phone['_id']}/reassign",
        json={"bot_id": bot["_id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assigned_bot_id"] == bot["_id"]
    assert body["assigned_bot_version_id"] == bot["active_version_id"]


def test_reassign_to_bot_with_no_active_version_errors_clearly(client, auth_headers):
    phone = client.post(
        "/api/phone-numbers", json={"number": "+955555555555", "environment": "dev"}, headers=auth_headers
    ).json()
    bot = _create_bot(client, auth_headers)  # never published, no active_version_id

    resp = client.put(
        f"/api/phone-numbers/{phone['_id']}/reassign",
        json={"bot_id": bot["_id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "published" in resp.json()["detail"].lower()


def test_reassign_unknown_phone_number_returns_404(client, auth_headers):
    bot = _create_and_publish_bot(client, auth_headers)
    resp = client.put(
        "/api/phone-numbers/000000000000000000000000/reassign",
        json={"bot_id": bot["_id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_delete_phone_number(client, auth_headers):
    phone = client.post(
        "/api/phone-numbers", json={"number": "+966666666666", "environment": "dev"}, headers=auth_headers
    ).json()
    resp = client.delete(f"/api/phone-numbers/{phone['_id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    resp = client.get("/api/phone-numbers", headers=auth_headers)
    assert resp.json() == []


def test_delete_unknown_phone_number_returns_404(client, auth_headers):
    resp = client.delete("/api/phone-numbers/000000000000000000000000", headers=auth_headers)
    assert resp.status_code == 404


def test_phone_numbers_requires_auth(client):
    resp = client.get("/api/phone-numbers")
    assert resp.status_code == 401


def test_create_phone_number_persists_sip_trunk_fields_but_never_echoes_password(client, auth_headers):
    resp = client.post(
        "/api/phone-numbers",
        json={
            "number": "08069625582",
            "environment": "prod",
            "service_id": "300",
            "aod_ports": 1,
            "name": "AI_Boot_15",
            "ip": "192.168.29.196",
            "sip_trunk": "9017",
            "sip_username": "voice_bot_nocode",
            "sip_password": "acmecorp",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["service_id"] == "300"
    assert body["aod_ports"] == 1
    assert body["name"] == "AI_Boot_15"
    assert body["ip"] == "192.168.29.196"
    assert body["sip_trunk"] == "9017"
    assert body["sip_username"] == "voice_bot_nocode"
    assert "sip_password" not in body

    listed = client.get("/api/phone-numbers", headers=auth_headers).json()
    assert all("sip_password" not in p for p in listed)


def test_create_phone_number_rejects_duplicate_number(client, auth_headers):
    client.post("/api/phone-numbers", json={"number": "+911111100000", "environment": "dev"}, headers=auth_headers)
    resp = client.post("/api/phone-numbers", json={"number": "+911111100000", "environment": "prod"}, headers=auth_headers)
    assert resp.status_code == 409


def test_reassign_with_malformed_bot_id_reports_bot_not_phone(client, auth_headers):
    """Regression: _oid() used to be hardcoded to always say 'Phone number not found', even
    when parsing payload.bot_id — misleading a PM debugging a bad bot id into thinking their
    phone number id was wrong instead."""
    phone = client.post(
        "/api/phone-numbers", json={"number": "+911111100001", "environment": "dev"}, headers=auth_headers
    ).json()
    resp = client.put(
        f"/api/phone-numbers/{phone['_id']}/reassign",
        json={"bot_id": "not-a-valid-objectid"},
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Bot not found"


def test_update_phone_number_details(client, auth_headers):
    phone = client.post(
        "/api/phone-numbers", json={"number": "+911111100002", "environment": "dev"}, headers=auth_headers
    ).json()

    resp = client.put(
        f"/api/phone-numbers/{phone['_id']}",
        json={"name": "Renamed", "ip": "10.0.0.5", "sip_trunk": "9017", "service_id": "301", "aod_ports": 2},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["ip"] == "10.0.0.5"
    assert body["sip_trunk"] == "9017"
    assert body["service_id"] == "301"
    assert body["aod_ports"] == 2
    assert "sip_password" not in body


def test_update_phone_number_leaves_password_unchanged_when_blank(client, auth_headers):
    phone = client.post(
        "/api/phone-numbers",
        json={"number": "+911111100003", "environment": "dev", "sip_password": "secret123"},
        headers=auth_headers,
    ).json()

    resp = client.put(f"/api/phone-numbers/{phone['_id']}", json={"name": "Updated"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text

    from bson import ObjectId

    from backend.db import phone_numbers as phone_numbers_collection

    stored = phone_numbers_collection.find_one({"_id": ObjectId(phone["_id"])})
    assert stored["sip_password"] == "secret123"


def test_update_unknown_phone_number_returns_404(client, auth_headers):
    resp = client.put("/api/phone-numbers/000000000000000000000000", json={"name": "x"}, headers=auth_headers)
    assert resp.status_code == 404
