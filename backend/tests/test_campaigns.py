def test_campaign_list_empty_by_default(client, auth_headers):
    resp = client.get("/api/campaigns", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_save_strategy_creates_campaign_via_upsert(client, auth_headers):
    resp = client.put(
        "/api/campaigns/leads_q1/strategy",
        json={"name": "Q1 Leads", "strategy": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_key"] == "leads_q1"
    assert body["dialing_strategy"]["max_attempts_total"] == 5


def _create_bot(client, auth_headers, name="Campaign Test Bot"):
    resp = client.post(
        "/api/bots",
        json={"name": name, "description": "d", "config": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_assign_bot_to_unknown_campaign_upserts_it(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    resp = client.put(
        "/api/campaigns/new_campaign/bot",
        json={"bot_id": bot["_id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["bot_id"] == bot["_id"]


def test_assign_bot_rejects_nonexistent_bot_id(client, auth_headers):
    """Regression: assign-bot previously accepted any bot_id string with zero validation
    (even a garbage string like 'abc123'), unlike phone-numbers' reassign endpoint which
    correctly checks the bot exists — a campaign could silently point at a bot that was
    never created or has since been deleted."""
    resp = client.put(
        "/api/campaigns/ghost_campaign/bot",
        json={"bot_id": "abc123"},
        headers=auth_headers,
    )
    assert resp.status_code == 404

    resp = client.put(
        "/api/campaigns/ghost_campaign/bot",
        json={"bot_id": "000000000000000000000000"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_set_status_on_unknown_campaign_returns_404_not_500(client, auth_headers):
    resp = client.put(
        "/api/campaigns/nonexistent/status",
        json={"status": "paused"},
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Campaign not found"


def test_set_status_requires_auth(client):
    resp = client.put("/api/campaigns/x/status", json={"status": "paused"})
    assert resp.status_code == 401


def test_delete_campaign(client, auth_headers):
    client.put(
        "/api/campaigns/to_delete/strategy",
        json={"name": "To Delete", "strategy": {}},
        headers=auth_headers,
    )
    resp = client.delete("/api/campaigns/to_delete", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    listing = client.get("/api/campaigns", headers=auth_headers).json()
    assert not any(c["campaign_key"] == "to_delete" for c in listing)


def test_delete_unknown_campaign_returns_404(client, auth_headers):
    resp = client.delete("/api/campaigns/nonexistent", headers=auth_headers)
    assert resp.status_code == 404


def test_delete_campaign_requires_auth(client):
    resp = client.delete("/api/campaigns/x")
    assert resp.status_code == 401
