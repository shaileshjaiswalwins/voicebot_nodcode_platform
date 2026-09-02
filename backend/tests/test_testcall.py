def test_start_test_call_without_livekit_credentials_returns_helpful_500(client, auth_headers, monkeypatch):
    monkeypatch.delenv("LIVEKIT_API_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)

    bot = client.post(
        "/api/bots",
        json={"name": "Test Bot", "description": "d", "config": {"organization_name": "Justdial"}},
        headers=auth_headers,
    ).json()

    resp = client.post(
        "/api/testcall/start",
        json={"bot_id": bot["_id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 500
    assert "not configured" in resp.json()["detail"]


def test_stop_test_call_without_livekit_credentials_returns_helpful_500(client, auth_headers, monkeypatch):
    monkeypatch.delenv("LIVEKIT_API_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)

    resp = client.post(
        "/api/testcall/stop",
        json={"room_name": "test-abc"},
        headers=auth_headers,
    )
    assert resp.status_code == 500
    assert "not configured" in resp.json()["detail"]


def test_start_test_call_requires_auth(client):
    resp = client.post("/api/testcall/start", json={"bot_id": "abc"})
    assert resp.status_code == 401
