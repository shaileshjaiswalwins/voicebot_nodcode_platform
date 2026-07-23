from backend.routers import custom_functions as cf_module


def _make_bot(client, auth_headers) -> str:
    resp = client.post("/api/bots", json={"name": "CF Bot"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["_id"]


def _fn_payload(**overrides) -> dict:
    payload = {
        "name": "fetch_customer",
        "description": "Fetch customer details by phone",
        "timing": "pre_call",
        "method": "GET",
        "url": "https://crm.example.com/lookup",
        "query_params": {"mobile": "{{mobile}}"},
        "response_mappings": [
            {"variable": "customer_name", "path": "results.0.name"},
        ],
    }
    payload.update(overrides)
    return payload


def test_crud_round_trip(client, auth_headers):
    bot_id = _make_bot(client, auth_headers)

    # create
    resp = client.post(
        f"/api/bots/{bot_id}/custom-functions", json=_fn_payload(), headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    fn = resp.json()
    fn_id = fn["id"]
    assert fn["bot_id"] == bot_id
    assert fn["timing"] == "pre_call"
    assert fn["enabled"] is True  # defaulted
    assert fn["method"] == "GET"  # defaulted

    # list
    resp = client.get(f"/api/bots/{bot_id}/custom-functions", headers=auth_headers)
    assert resp.status_code == 200
    assert [f["id"] for f in resp.json()] == [fn_id]

    # get
    resp = client.get(f"/api/bots/{bot_id}/custom-functions/{fn_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "fetch_customer"

    # update
    resp = client.put(
        f"/api/bots/{bot_id}/custom-functions/{fn_id}",
        json=_fn_payload(name="fetch_customer_v2", enabled=False),
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "fetch_customer_v2"
    assert resp.json()["enabled"] is False

    # delete
    resp = client.delete(f"/api/bots/{bot_id}/custom-functions/{fn_id}", headers=auth_headers)
    assert resp.status_code == 200
    resp = client.get(f"/api/bots/{bot_id}/custom-functions", headers=auth_headers)
    assert resp.json() == []


def test_timing_is_required(client, auth_headers):
    bot_id = _make_bot(client, auth_headers)
    payload = _fn_payload()
    del payload["timing"]
    resp = client.post(f"/api/bots/{bot_id}/custom-functions", json=payload, headers=auth_headers)
    assert resp.status_code == 422


def test_unknown_bot_404(client, auth_headers):
    resp = client.get("/api/bots/64b0c0000000000000000000/custom-functions", headers=auth_headers)
    assert resp.status_code == 404


def test_test_endpoint_extracts_variables(client, auth_headers, monkeypatch):
    bot_id = _make_bot(client, auth_headers)

    captured = {}

    async def _fake_request(method, url, headers, params, body, body_format, timeout_ms):
        captured["url"] = url
        captured["params"] = params
        return 200, 12.3, {"results": [{"name": "Evie Wang"}]}, None

    monkeypatch.setattr(cf_module, "_perform_request", _fake_request)

    resp = client.post(
        f"/api/bots/{bot_id}/custom-functions/test",
        json=_fn_payload(sample_context={"mobile": "9999999999"}),
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == 200
    assert body["extracted"] == {"customer_name": "Evie Wang"}
    assert body["error"] is None
    # sample_context was substituted into the outgoing request
    assert captured["params"] == {"mobile": "9999999999"}


def test_test_endpoint_reports_error(client, auth_headers, monkeypatch):
    bot_id = _make_bot(client, auth_headers)

    async def _fake_request(*args, **kwargs):
        return None, 5.0, None, "Cannot connect to host"

    monkeypatch.setattr(cf_module, "_perform_request", _fake_request)

    resp = client.post(
        f"/api/bots/{bot_id}/custom-functions/test",
        json=_fn_payload(),
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["error"] == "Cannot connect to host"
    assert resp.json()["extracted"] == {}
