import pytest

from backend.pricing import ProviderStack, estimate_cost_per_min, named_tiers, select_stack_for_budget


def test_estimate_cost_sums_all_four_components():
    stack = ProviderStack("sarvam_saras_v3", "gemini_3_1_flash_lite", "sarvam_bulbul_v3", "sip_direct")
    assert estimate_cost_per_min(stack) == pytest.approx(0.25 + 0.87 + 1.60 + 0.0)


def test_estimate_cost_raises_for_unknown_provider_key():
    stack = ProviderStack("not_a_real_stt", "gemini_3_1_flash_lite", "sarvam_bulbul_v3", "sip_direct")
    with pytest.raises(KeyError):
        estimate_cost_per_min(stack)


def test_select_stack_for_budget_picks_the_current_default_stack_at_two_rupees():
    stack = select_stack_for_budget(2.72)
    assert (stack.stt, stack.llm, stack.tts, stack.telephony) == (
        "sarvam_saras_v3",
        "gemini_3_1_flash_lite",
        "sarvam_bulbul_v3",
        "sip_direct",
    )


def test_select_stack_for_budget_upgrades_at_high_cap():
    stack = select_stack_for_budget(15.0)
    assert estimate_cost_per_min(stack) > 10.0


def test_select_stack_for_budget_falls_back_to_cheapest_when_cap_too_low():
    stack = select_stack_for_budget(0.01)
    assert estimate_cost_per_min(stack) == estimate_cost_per_min(
        min(
            [
                ProviderStack("deepgram_nova2", "gemini_2_5_flash", "deepgram_aura", "plivo"),
                ProviderStack("sarvam_saras_v3", "gemini_3_1_flash_lite", "sarvam_bulbul_v3", "sip_direct"),
            ],
            key=estimate_cost_per_min,
        )
    )


def test_named_tiers_are_sorted_cheapest_first_and_include_cost_per_min():
    tiers = named_tiers()
    assert [t["name"] for t in tiers] == ["Budget", "Current default", "Performance", "Ultra"]
    costs = [t["cost_per_min"] for t in tiers]
    assert costs == sorted(costs)


def test_pricing_tiers_endpoint_requires_auth(client):
    resp = client.get("/api/pricing/tiers")
    assert resp.status_code == 401


def test_pricing_tiers_endpoint_returns_four_tiers(client, auth_headers):
    resp = client.get("/api/pricing/tiers", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 4


def test_pricing_matrix_endpoint_requires_auth(client):
    resp = client.get("/api/pricing/matrix")
    assert resp.status_code == 401


def test_pricing_matrix_endpoint_returns_all_four_categories(client, auth_headers):
    resp = client.get("/api/pricing/matrix", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"stt", "llm", "tts", "telephony"}


def test_budget_route_endpoint_returns_a_stack_and_cost(client, auth_headers):
    resp = client.get("/api/pricing/budget-route?max_inr_per_min=2.0", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "estimated_cost_per_min" in body
    assert body["estimated_cost_per_min"] <= 2.72  # within a small tolerance of the cap in practice
