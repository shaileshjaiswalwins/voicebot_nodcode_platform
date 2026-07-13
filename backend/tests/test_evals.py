def _create_bot(client, auth_headers, system_prompt=""):
    resp = client.post(
        "/api/bots",
        json={"name": "Eval Bot", "config": {"system_prompt": system_prompt}},
        headers=auth_headers,
    )
    return resp.json()


def test_eval_run_without_system_prompt_returns_helpful_400(client, auth_headers):
    bot = _create_bot(client, auth_headers, system_prompt="")
    resp = client.post(f"/api/bots/{bot['_id']}/evals/run", json={}, headers=auth_headers)
    assert resp.status_code == 400
    assert "system_prompt" in resp.json()["detail"]


def test_eval_run_on_unknown_bot_returns_404(client, auth_headers):
    resp = client.post("/api/bots/000000000000000000000000/evals/run", json={}, headers=auth_headers)
    assert resp.status_code == 404


def test_eval_list_is_empty_for_new_bot(client, auth_headers):
    bot = _create_bot(client, auth_headers, system_prompt="You are a helpful assistant.")
    resp = client.get(f"/api/bots/{bot['_id']}/evals", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_eval_run_persists_and_shows_up_in_list(client, auth_headers, monkeypatch):
    from backend.routers import evals as evals_router

    def fake_run_evals(system_prompt, scenarios):
        return {"passed": True, "scenario_results": [{"name": s.name, "passed": True} for s in scenarios]}

    monkeypatch.setattr(evals_router, "run_evals", fake_run_evals)

    bot = _create_bot(client, auth_headers, system_prompt="You are a helpful assistant.")
    run_resp = client.post(f"/api/bots/{bot['_id']}/evals/run", json={}, headers=auth_headers)
    assert run_resp.status_code == 200
    assert run_resp.json()["passed"] is True

    listing = client.get(f"/api/bots/{bot['_id']}/evals", headers=auth_headers).json()
    assert len(listing) == 1


def test_eval_run_wraps_upstream_failure_as_502_not_500(client, auth_headers, monkeypatch):
    from backend.routers import evals as evals_router

    def failing_run_evals(system_prompt, scenarios):
        raise RuntimeError("Gemini API unreachable")

    monkeypatch.setattr(evals_router, "run_evals", failing_run_evals)

    bot = _create_bot(client, auth_headers, system_prompt="You are a helpful assistant.")
    resp = client.post(f"/api/bots/{bot['_id']}/evals/run", json={}, headers=auth_headers)
    assert resp.status_code == 502
    assert "Eval run failed" in resp.json()["detail"]
