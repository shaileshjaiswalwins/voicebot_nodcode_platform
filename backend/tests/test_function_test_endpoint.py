"""Tests for POST /api/bots/{bot_id}/functions/test — server-side dry-run of a custom
function so a PM can validate it from the UI's 'Test' button without a live call
(Plan 02, Phase 5)."""

import backend.routers.bots as bots_router


def _create_bot(client, auth_headers, name="Test Bot"):
    resp = client.post(
        "/api/bots",
        json={"name": name, "description": "d", "config": {"organization_name": "AcmeCorp"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _stub_http(monkeypatch, *, status=200, body=None, raise_exc=None):
    async def fake_exec(call, timeout):
        if raise_exc:
            raise raise_exc
        return status, (body if body is not None else {})
    monkeypatch.setattr(bots_router, "_execute_test_request", fake_exec)


def test_requires_auth(client):
    resp = client.post("/api/bots/deadbeefdeadbeefdeadbeef/functions/test", json={"function": {}})
    assert resp.status_code == 401


def test_404_for_unknown_bot(client, auth_headers, monkeypatch):
    _stub_http(monkeypatch)
    resp = client.post(
        "/api/bots/deadbeefdeadbeefdeadbeef/functions/test",
        json={"function": {"url": "http://x", "method": "POST"}},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_400_when_url_missing(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    resp = client.post(
        f"/api/bots/{bot['_id']}/functions/test",
        json={"function": {"name": "n", "url": ""}},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_happy_path_returns_status_response_and_extracted_vars(client, auth_headers, monkeypatch):
    bot = _create_bot(client, auth_headers)
    _stub_http(monkeypatch, status=200, body={"data": {"name": "Priya"}})
    resp = client.post(
        f"/api/bots/{bot['_id']}/functions/test",
        json={
            "function": {
                "name": "get_lead",
                "url": "http://mis/lead",
                "method": "POST",
                "body_mode": "json",
                "store_variables": [{"variable": "lead_name", "json_path": "data.name"}],
            },
            "args": {"mobile": "999"},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["status_code"] == 200
    assert data["response"] == {"data": {"name": "Priya"}}
    assert data["extracted_vars"] == {"lead_name": "Priya"}
    assert isinstance(data["latency_ms"], int)
    assert data["error"] is None
    # The computed request is echoed back so the PM can see what was sent.
    assert data["request"]["method"] == "POST"
    assert data["request"]["json"] == {"mobile": "999"}


def test_non_2xx_is_reported_but_not_an_error_object(client, auth_headers, monkeypatch):
    bot = _create_bot(client, auth_headers)
    _stub_http(monkeypatch, status=500, body={"detail": "boom"})
    resp = client.post(
        f"/api/bots/{bot['_id']}/functions/test",
        json={"function": {"name": "n", "url": "http://x", "method": "GET"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status_code"] == 500
    assert data["ok"] is False
    assert data["error"] is None  # transport succeeded; HTTP status is separate from error


def test_transport_exception_is_captured_as_error(client, auth_headers, monkeypatch):
    bot = _create_bot(client, auth_headers)
    _stub_http(monkeypatch, raise_exc=TimeoutError("timed out"))
    resp = client.post(
        f"/api/bots/{bot['_id']}/functions/test",
        json={"function": {"name": "n", "url": "http://x", "method": "GET", "timeout_ms": 500}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["status_code"] is None
    assert "timed out" in data["error"]
