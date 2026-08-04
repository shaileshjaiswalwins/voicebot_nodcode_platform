from backend.analysis_prompts import (
    CALL_ANALYSIS_KEY,
    DEFAULT_PROMPTS,
    get_analysis_prompt_for_runtime,
    seed_default_analysis_prompts,
)


def test_seed_is_idempotent(client, auth_headers):
    seed_default_analysis_prompts()
    seed_default_analysis_prompts()
    resp = client.get("/api/library/analysis-prompts", headers=auth_headers)
    assert resp.status_code == 200
    keys = [p["key"] for p in resp.json()]
    assert sorted(keys) == sorted(DEFAULT_PROMPTS.keys())


def test_update_analysis_prompt_roundtrip(client, auth_headers):
    seed_default_analysis_prompts()
    new_template = DEFAULT_PROMPTS[CALL_ANALYSIS_KEY].replace("strict call-analysis engine", "lenient call-analysis engine")
    resp = client.put(
        f"/api/library/analysis-prompts/{CALL_ANALYSIS_KEY}",
        json={"prompt_template": new_template},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["prompt_template"] == new_template

    listing = client.get("/api/library/analysis-prompts", headers=auth_headers).json()
    updated = next(p for p in listing if p["key"] == CALL_ANALYSIS_KEY)
    assert updated["prompt_template"] == new_template


def test_update_unknown_key_returns_404(client, auth_headers):
    resp = client.put(
        "/api/library/analysis-prompts/nonexistent_key",
        json={"prompt_template": "anything"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_update_rejects_empty_template(client, auth_headers):
    seed_default_analysis_prompts()
    resp = client.put(
        f"/api/library/analysis-prompts/{CALL_ANALYSIS_KEY}",
        json={"prompt_template": "   "},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_update_rejects_missing_required_placeholder(client, auth_headers):
    seed_default_analysis_prompts()
    resp = client.put(
        f"/api/library/analysis-prompts/{CALL_ANALYSIS_KEY}",
        json={"prompt_template": "A prompt with no placeholders at all."},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_get_analysis_prompt_for_runtime_falls_back_to_default_when_mongo_empty():
    # No seed call — collection is empty (wiped by the autouse _clean_db fixture).
    template = get_analysis_prompt_for_runtime(CALL_ANALYSIS_KEY)
    assert template == DEFAULT_PROMPTS[CALL_ANALYSIS_KEY]


def test_get_analysis_prompt_for_runtime_reflects_update(client, auth_headers):
    seed_default_analysis_prompts()
    new_template = DEFAULT_PROMPTS[CALL_ANALYSIS_KEY] + " EXTRA_MARKER"
    client.put(
        f"/api/library/analysis-prompts/{CALL_ANALYSIS_KEY}",
        json={"prompt_template": new_template},
        headers=auth_headers,
    )
    # update_analysis_prompt evicts the cache entry, so the next read is fresh.
    assert get_analysis_prompt_for_runtime(CALL_ANALYSIS_KEY) == new_template


def test_get_analysis_prompt_for_runtime_prefers_bot_level_override(client, auth_headers):
    """A per-bot analysis_prompt (BotConfig.analysis_prompt) must win over both the global
    Mongo-backed template AND the hardcoded default — override bypasses the lookup entirely."""
    seed_default_analysis_prompts()
    client.put(
        f"/api/library/analysis-prompts/{CALL_ANALYSIS_KEY}",
        json={"prompt_template": DEFAULT_PROMPTS[CALL_ANALYSIS_KEY] + " GLOBAL_MARKER"},
        headers=auth_headers,
    )
    bot_override = DEFAULT_PROMPTS[CALL_ANALYSIS_KEY] + " BOT_OVERRIDE_MARKER"
    assert get_analysis_prompt_for_runtime(CALL_ANALYSIS_KEY, override=bot_override) == bot_override


def test_get_analysis_prompt_for_runtime_falls_back_to_global_when_override_blank():
    template = get_analysis_prompt_for_runtime(CALL_ANALYSIS_KEY, override="")
    assert template == DEFAULT_PROMPTS[CALL_ANALYSIS_KEY]
    whitespace_only = get_analysis_prompt_for_runtime(CALL_ANALYSIS_KEY, override="   ")
    assert whitespace_only == DEFAULT_PROMPTS[CALL_ANALYSIS_KEY]


def test_validate_prompt_template_reusable_by_bot_level_save_path():
    """The bots router (backend/routers/bots.py) validates a per-bot analysis_prompt override
    with this exact function — assert it rejects/accepts the same way the global editor does."""
    from backend.analysis_prompts import validate_prompt_template
    import pytest

    with pytest.raises(ValueError):
        validate_prompt_template(CALL_ANALYSIS_KEY, "no placeholders at all")
    with pytest.raises(KeyError):
        validate_prompt_template("nonexistent_key", DEFAULT_PROMPTS[CALL_ANALYSIS_KEY])
    validate_prompt_template(CALL_ANALYSIS_KEY, DEFAULT_PROMPTS[CALL_ANALYSIS_KEY])  # no raise
