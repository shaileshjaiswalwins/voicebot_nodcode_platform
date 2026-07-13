def test_platform_settings_defaults_when_never_configured(client, auth_headers):
    resp = client.get("/api/settings/platform", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_environment"] == "dev"
    assert body["dev"]["lead_qualify_base_url"] == ""


def test_active_endpoints_is_unauthenticated_for_workers(client):
    resp = client.get("/api/settings/platform/active-endpoints")
    assert resp.status_code == 200
    assert resp.json()["active_environment"] == "dev"


def test_switching_active_environment_changes_resolved_endpoints(client, auth_headers):
    payload = {
        "active_environment": "dev",
        "dev": {"lead_qualify_base_url": "http://dev.internal", "callback_api_url": "", "callback_update_api_url": "", "mis_api_base": ""},
        "prod": {"lead_qualify_base_url": "http://prod.internal", "callback_api_url": "", "callback_update_api_url": "", "mis_api_base": ""},
        "default_inactivity_phrase": "",
        "default_close_markers": [],
    }
    put_resp = client.put("/api/settings/platform", json=payload, headers=auth_headers)
    assert put_resp.status_code == 200

    dev_resolved = client.get("/api/settings/platform/active-endpoints").json()
    assert dev_resolved["lead_qualify_base_url"] == "http://dev.internal"

    payload["active_environment"] = "prod"
    client.put("/api/settings/platform", json=payload, headers=auth_headers)
    prod_resolved = client.get("/api/settings/platform/active-endpoints").json()
    assert prod_resolved["lead_qualify_base_url"] == "http://prod.internal"


def test_update_platform_settings_requires_auth(client):
    resp = client.put("/api/settings/platform", json={"active_environment": "dev"})
    assert resp.status_code == 401


def test_update_platform_settings_rejects_invalid_environment(client, auth_headers):
    resp = client.put(
        "/api/settings/platform",
        json={"active_environment": "staging"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
