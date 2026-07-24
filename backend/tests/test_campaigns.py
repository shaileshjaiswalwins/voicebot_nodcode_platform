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


def test_save_dialer_config_creates_campaign_via_upsert(client, auth_headers):
    client.post(
        "/api/phone-numbers",
        json={"number": "+911111100300", "environment": "dev", "service_id": "300"},
        headers=auth_headers,
    )
    resp = client.put(
        "/api/campaigns/dialer_q1/dialer-config",
        json={
            "dialer_config": {
                "channel_name": "DVN Missed Call",
                "channel_id": 43,
                "bd": 0,
                "service_id": "300",
                "service_source": "lq_staging",
            }
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["campaign_key"] == "dialer_q1"
    assert body["dialer_config"]["channel_id"] == 43
    assert body["dialer_config"]["service_id"] == "300"
    # unset fields fall back to their DialerConfig defaults, not silently dropped
    assert body["dialer_config"]["country"] == "IN"
    assert body["dialer_config"]["page_type"] == "gallery_image"


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
    # The rejected row is persisted too (status="rejected" + a reason), not silently
    # dropped — so a PM can see exactly which rows failed and why.
    assert len(listing) == 3
    imported = [lead for lead in listing if lead["status"] != "rejected"]
    rejected = [lead for lead in listing if lead["status"] == "rejected"]
    assert len(imported) == 2
    assert len(rejected) == 1
    assert "not-a-phone" in rejected[0]["failure_reason"]

    by_phone = {lead["phone_number"]: lead for lead in imported}
    assert by_phone["+919876543210"]["name"] == "Asha"
    assert by_phone["+919876543210"]["vars"] == {"reason_of_calling": "payment reminder"}
    # blank name falls back to "Customer" per spec, not left empty
    assert by_phone["+919876543211"]["name"] == "Customer"
    assert by_phone["+919876543211"]["status"] == "pending"


def test_upload_leads_rejects_csv_without_phone_number_column(client, auth_headers):
    resp = _upload_csv(client, auth_headers, "csv_camp2", "name,foo\nAsha,bar\n")
    assert resp.status_code == 400


def test_upload_leads_accepts_jduid_only_rows(client, auth_headers):
    """TSPL-pushed campaigns never see a real phone number — jduid is the primary
    identifier there, so a CSV with only a jduid column (no phone_number at all) must be
    accepted, not rejected."""
    csv_text = (
        "jduid,name,buyer_city,searched_keyword\n"
        "9092012171200001572,Asha,kolkata,biscuit Dealer\n"
        ",Bad Row,delhi,widgets\n"
    )
    resp = _upload_csv(client, auth_headers, "jduid_camp", csv_text)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"total": 2, "imported": 1, "skipped": 1}

    listing = client.get("/api/campaigns/jduid_camp/leads", headers=auth_headers).json()
    assert len(listing) == 2
    imported = [lead for lead in listing if lead["status"] != "rejected"]
    rejected = [lead for lead in listing if lead["status"] == "rejected"]
    assert len(imported) == 1
    assert len(rejected) == 1
    assert imported[0]["jduid"] == "9092012171200001572"
    assert imported[0]["phone_number"] is None
    assert imported[0]["vars"] == {"buyer_city": "kolkata", "searched_keyword": "biscuit Dealer"}
    assert rejected[0]["failure_reason"] == "missing phone_number and jduid"


def test_upload_leads_row_with_neither_phone_nor_jduid_is_skipped(client, auth_headers):
    csv_text = "jduid,phone_number,name\n,,Nobody\n9092012171200001573,,Someone\n"
    resp = _upload_csv(client, auth_headers, "mixed_camp", csv_text)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"total": 2, "imported": 1, "skipped": 1}


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


def _base_dialer_config(**overrides):
    cfg = {
        "channel_name": "DVN Missed Call",
        "channel_id": 43,
        "bd": 0,
        "service_id": "300",
        "service_source": "lq_staging",
    }
    cfg.update(overrides)
    return cfg


def test_save_dialer_config_rejects_missing_required_field(client, auth_headers):
    client.post(
        "/api/phone-numbers",
        json={"number": "+911111100301", "environment": "dev", "service_id": "300"},
        headers=auth_headers,
    )
    resp = client.put(
        "/api/campaigns/dialer_missing/dialer-config",
        json={"dialer_config": _base_dialer_config(service_source="")},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "service_source" in resp.text


def test_save_dialer_config_accepts_zero_as_a_valid_bd_or_channel_id(client, auth_headers):
    """bd=0 / channel_id=0 are real values, not "missing" — must not be treated as falsy."""
    client.post(
        "/api/phone-numbers",
        json={"number": "+911111100302", "environment": "dev", "service_id": "300"},
        headers=auth_headers,
    )
    resp = client.put(
        "/api/campaigns/dialer_zero/dialer-config",
        json={"dialer_config": _base_dialer_config(channel_id=0, bd=0)},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


def test_save_dialer_config_rejects_unknown_service_id(client, auth_headers):
    resp = client.put(
        "/api/campaigns/dialer_unbound/dialer-config",
        json={"dialer_config": _base_dialer_config(service_id="does-not-exist")},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_download_leads_template_matches_upload_and_dialer_payload_fields(client, auth_headers):
    """The concrete guarantee: the downloadable template's columns are exactly what
    upload_leads accepts and what dialer_client.build_dialer_payload reads — filling in
    the template and uploading it produces a real, working push payload."""
    from dialer_client import build_dialer_payload

    resp = client.get("/api/campaigns/leads-template.csv", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    header = lines[0].split(",")
    assert header == [
        "jduid", "phone_number", "name", "buyer_city", "buyer_area",
        "searched_keyword", "ncatid", "jdmart_id", "product_id", "flow",
    ]

    upload_resp = _upload_csv(client, auth_headers, "template_camp", resp.text)
    assert upload_resp.status_code == 200, upload_resp.text
    assert upload_resp.json()["imported"] == 1

    leads = client.get("/api/campaigns/template_camp/leads", headers=auth_headers).json()
    assert len(leads) == 1
    lead = leads[0]
    assert lead["jduid"]
    assert lead["phone_number"] is None  # template's example row is jduid-only, on purpose

    payload = build_dialer_payload(lead, {"dialer_config": {}}, job_id="job123")
    assert payload["jduid"] == lead["jduid"]
    assert payload["buyer_details"]["buyer_city"] == lead["vars"]["buyer_city"] != ""
    assert payload["buyer_details"]["buyer_area"] == lead["vars"]["buyer_area"] != ""
    assert payload["search_context"]["searched_keyword"] == lead["vars"]["searched_keyword"] != ""
    # ncatid is a real, TSPL-confirmed-valid category id (see LEADS_TEMPLATE_EXAMPLE_ROW's
    # comment) — TSPL validates ncatid/jdmart_id/product_id against real records, so a
    # placeholder ncatid alone is enough to get the whole push rejected as
    # "config validation failed". jdmart_id/product_id/flow are left blank in the template
    # since that's the confirmed-working combination, not a gap in the template.
    assert payload["ncatid"] == int(lead["vars"]["ncatid"]) != 0
    assert payload["jdmart_id"] == lead["vars"]["jdmart_id"] == ""
    assert payload["product_id"] == lead["vars"]["product_id"] == ""
    assert payload["flow"] == lead["vars"]["flow"] == ""


def test_schedule_campaign_send_now_mirrors_start(client, auth_headers):
    client.put(
        "/api/campaigns/sched_now/strategy", json={"name": "Sched Now", "strategy": {}}, headers=auth_headers
    )
    _upload_csv(client, auth_headers, "sched_now", "phone_number,name\n+919876543299,Asha\n")

    resp = client.post(
        "/api/campaigns/sched_now/schedule", json={"send_now": True}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["enqueued"] == 1

    campaign = client.get("/api/campaigns", headers=auth_headers).json()
    doc = next(c for c in campaign if c["campaign_key"] == "sched_now")
    assert doc["status"] == "active"
    assert "scheduled_at" not in doc or doc.get("scheduled_at") is None


def test_schedule_campaign_with_future_datetime_sets_scheduled_status(client, auth_headers):
    client.put(
        "/api/campaigns/sched_later/strategy", json={"name": "Sched Later", "strategy": {}}, headers=auth_headers
    )
    _upload_csv(client, auth_headers, "sched_later", "phone_number,name\n+919876543298,Asha\n")

    resp = client.post(
        "/api/campaigns/sched_later/schedule",
        json={"send_now": False, "scheduled_at": "2099-01-01T09:00:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    campaign = client.get("/api/campaigns", headers=auth_headers).json()
    doc = next(c for c in campaign if c["campaign_key"] == "sched_later")
    assert doc["status"] == "scheduled"
    assert doc["scheduled_at"] is not None


def test_schedule_campaign_without_datetime_requires_scheduled_at(client, auth_headers):
    client.put(
        "/api/campaigns/sched_bad/strategy", json={"name": "Sched Bad", "strategy": {}}, headers=auth_headers
    )
    resp = client.post(
        "/api/campaigns/sched_bad/schedule", json={"send_now": False}, headers=auth_headers
    )
    assert resp.status_code == 400


def test_schedule_campaign_requires_existing_campaign(client, auth_headers):
    resp = client.post(
        "/api/campaigns/does_not_exist/schedule", json={"send_now": True}, headers=auth_headers
    )
    assert resp.status_code == 404
