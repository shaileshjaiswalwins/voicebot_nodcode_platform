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


def _upload_csv(client, auth_headers, campaign_key, csv_text):
    return client.post(
        f"/api/campaigns/{campaign_key}/leads",
        files={"file": ("leads.csv", csv_text, "text/csv")},
        headers=auth_headers,
    )


def test_upload_leads_imports_valid_rows_and_skips_bad_phone_numbers(client, auth_headers):
    csv_text = (
        "phone_number,name,reason_of_calling\n"
        "+919876543210,Asha,payment reminder\n"
        "not-a-phone,Bad Row,x\n"
        "+919876543211,,follow up\n"
    )
    resp = _upload_csv(client, auth_headers, "csv_camp", csv_text)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"total": 3, "imported": 2, "skipped": 1}

    listing = client.get("/api/campaigns/csv_camp/leads", headers=auth_headers).json()
    assert len(listing) == 2
    by_phone = {lead["phone_number"]: lead for lead in listing}
    assert by_phone["+919876543210"]["name"] == "Asha"
    assert by_phone["+919876543210"]["vars"] == {"reason_of_calling": "payment reminder"}
    # blank name falls back to "Customer" per spec, not left empty
    assert by_phone["+919876543211"]["name"] == "Customer"
    assert by_phone["+919876543211"]["status"] == "pending"


def test_upload_leads_rejects_csv_without_phone_number_column(client, auth_headers):
    resp = _upload_csv(client, auth_headers, "csv_camp2", "name,foo\nAsha,bar\n")
    assert resp.status_code == 400


def test_list_leads_filters_by_status(client, auth_headers):
    _upload_csv(client, auth_headers, "csv_camp3", "phone_number,name\n+919876543212,Ravi\n")
    resp = client.get("/api/campaigns/csv_camp3/leads?status=pending", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    resp = client.get("/api/campaigns/csv_camp3/leads?status=completed", headers=auth_headers)
    assert resp.json() == []


def test_upload_leads_requires_auth(client):
    resp = client.post(
        "/api/campaigns/x/leads",
        files={"file": ("leads.csv", "phone_number\n+919876543210\n", "text/csv")},
    )
    assert resp.status_code == 401


def test_save_and_validate_prompt_template_flags_unknown_vars(client, auth_headers):
    _upload_csv(
        client,
        auth_headers,
        "prompt_camp",
        "phone_number,name,reason_of_calling\n+919876543213,Asha,payment reminder\n",
    )
    save_resp = client.put(
        "/api/campaigns/prompt_camp/prompt",
        json={"prompt_template": "Hello {{name}}, {{reason_of_calling}}. {{unknown_col}}?"},
        headers=auth_headers,
    )
    assert save_resp.status_code == 200
    assert save_resp.json()["prompt_template"] == "Hello {{name}}, {{reason_of_calling}}. {{unknown_col}}?"

    validate_resp = client.post(
        "/api/campaigns/prompt_camp/prompt/validate",
        json={"prompt_template": "Hello {{name}}, {{reason_of_calling}}. {{unknown_col}}?"},
        headers=auth_headers,
    )
    assert validate_resp.status_code == 200
    assert validate_resp.json()["unknown_vars"] == ["unknown_col"]


def test_validate_prompt_template_with_only_known_vars_returns_empty(client, auth_headers):
    resp = client.post(
        "/api/campaigns/no_leads_camp/prompt/validate",
        json={"prompt_template": "Hi {{name}}, calling about {{phone_number}}"},
        headers=auth_headers,
    )
    assert resp.json()["unknown_vars"] == []
