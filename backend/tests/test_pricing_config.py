from backend import pricing, pricing_config
from backend.models import PricingConfig, PricingModelEntry


def test_get_config_seeds_defaults_matching_hardcoded_pricing_when_no_doc_exists(client):
    config = pricing_config.get_config()
    assert {e.key for e in config.llm} == set(pricing.LLM_RATES.keys())
    assert next(e for e in config.llm if e.key == "gemini_3_1_flash_lite").cost_inr_per_min == pricing.LLM_RATES["gemini_3_1_flash_lite"]


def test_default_llm_entries_include_latency_and_token_estimates(client):
    config = pricing_config.get_config()
    entry = next(e for e in config.llm if e.key == "gemini_3_1_flash_lite")
    assert entry.latency_ms_min == 970
    assert entry.latency_ms_max == 1450
    assert entry.tokens_min == 597
    assert entry.tokens_max == 1000


def test_update_config_persists_and_get_config_reflects_it(client):
    new_config = pricing_config.get_config()
    new_config.llm.append(PricingModelEntry(key="gpt_5_1", label="gpt-5.1", cost_inr_per_min=2.5))
    pricing_config.update_config(new_config)

    reloaded = pricing_config.get_config()
    assert any(e.key == "gpt_5_1" and e.cost_inr_per_min == 2.5 for e in reloaded.llm)


def test_get_effective_rates_reflects_updated_config(client):
    config = pricing_config.get_config()
    for entry in config.llm:
        if entry.key == "gemini_3_1_flash_lite":
            entry.cost_inr_per_min = 99.0
    pricing_config.update_config(config)

    rates = pricing_config.get_effective_rates()
    assert rates["llm"]["gemini_3_1_flash_lite"] == 99.0


def test_admin_config_endpoint_requires_auth(client):
    resp = client.get("/api/pricing/admin-config")
    assert resp.status_code == 401


def test_admin_config_get_returns_full_shape(client, auth_headers):
    resp = client.get("/api/pricing/admin-config", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"stt", "llm", "tts", "telephony"}
    assert len(body["llm"]) == len(pricing.LLM_RATES)


def test_admin_config_put_round_trips(client, auth_headers):
    get_resp = client.get("/api/pricing/admin-config", headers=auth_headers)
    config = get_resp.json()
    config["llm"][0]["cost_inr_per_min"] = 42.0

    put_resp = client.put("/api/pricing/admin-config", json=config, headers=auth_headers)
    assert put_resp.status_code == 200

    reget = client.get("/api/pricing/admin-config", headers=auth_headers).json()
    assert reget["llm"][0]["cost_inr_per_min"] == 42.0


def test_matrix_endpoint_reflects_admin_config_changes(client, auth_headers):
    config = client.get("/api/pricing/admin-config", headers=auth_headers).json()
    for entry in config["llm"]:
        if entry["key"] == "gemini_3_1_flash_lite":
            entry["cost_inr_per_min"] = 7.77
    client.put("/api/pricing/admin-config", json=config, headers=auth_headers)

    matrix = client.get("/api/pricing/matrix", headers=auth_headers).json()
    assert matrix["llm"]["gemini_3_1_flash_lite"] == 7.77


def test_tiers_endpoint_reflects_admin_config_changes(client, auth_headers):
    config = client.get("/api/pricing/admin-config", headers=auth_headers).json()
    # Push every stt/llm/tts/telephony rate to 0 so every tier costs 0 and order is stable.
    for category in ("stt", "llm", "tts", "telephony"):
        for entry in config[category]:
            entry["cost_inr_per_min"] = 0.0
    for category in ("llm",):
        for entry in config[category]:
            if entry["key"] == "gpt_4o":
                entry["cost_inr_per_min"] = 999.0
    client.put("/api/pricing/admin-config", json=config, headers=auth_headers)

    tiers = client.get("/api/pricing/tiers", headers=auth_headers).json()
    ultra = next(t for t in tiers if t["name"] == "Ultra")
    assert ultra["cost_per_min"] > 900


def test_budget_route_endpoint_reflects_admin_config_changes(client, auth_headers):
    config = client.get("/api/pricing/admin-config", headers=auth_headers).json()
    for category in ("stt", "llm", "tts", "telephony"):
        for entry in config[category]:
            entry["cost_inr_per_min"] = 0.0
    client.put("/api/pricing/admin-config", json=config, headers=auth_headers)

    resp = client.get("/api/pricing/budget-route?max_inr_per_min=2.0", headers=auth_headers)
    assert resp.json()["estimated_cost_per_min"] == 0.0
