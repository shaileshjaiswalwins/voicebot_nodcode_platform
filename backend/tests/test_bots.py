def _create_bot(client, auth_headers, name="Test Bot"):
    resp = client.post(
        "/api/bots",
        json={"name": name, "description": "d", "config": {"organization_name": "Acmecorp"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_create_bot_rejects_analysis_prompt_missing_required_placeholder(client, auth_headers):
    resp = client.post(
        "/api/bots",
        json={
            "name": "Bad Analysis Prompt Bot",
            "description": "d",
            "config": {"organization_name": "Acmecorp", "analysis_prompt": "No placeholders here."},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_update_version_rejects_invalid_analysis_prompt_override(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    resp = client.put(
        f"/api/bots/{bot['_id']}/versions/{bot['draft_version_id']}",
        json={"config": {"organization_name": "Acmecorp", "analysis_prompt": "still missing placeholders"}},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_update_version_accepts_empty_analysis_prompt_as_use_global_default(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    resp = client.put(
        f"/api/bots/{bot['_id']}/versions/{bot['draft_version_id']}",
        json={"config": {"organization_name": "Acmecorp", "analysis_prompt": ""}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


def test_update_version_accepts_valid_analysis_prompt_override_with_all_required_placeholders(client, auth_headers):
    from backend.analysis_prompts import CALL_ANALYSIS_KEY, DEFAULT_PROMPTS

    bot = _create_bot(client, auth_headers)
    valid_override = DEFAULT_PROMPTS[CALL_ANALYSIS_KEY].replace(
        "strict call-analysis engine", "custom per-bot call-analysis engine"
    )
    resp = client.put(
        f"/api/bots/{bot['_id']}/versions/{bot['draft_version_id']}",
        json={"config": {"organization_name": "Acmecorp", "analysis_prompt": valid_override}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


def test_legacy_function_dict_with_custom_body_round_trips(client, auth_headers):
    """Existing bots stored functions as loose dicts (name/url/method/headers/query_params/
    custom_body). Migrating `functions` to the typed CustomFunction model must not drop those
    fields — FetchLead/FetchCategorySchema entries and their custom_body must survive a save."""
    bot = _create_bot(client, auth_headers)
    legacy_fn = {
        "name": "FetchLead",
        "url": "http://mis.internal/lead",
        "method": "POST",
        "headers": {"Authorization": "Bearer x"},
        "query_params": {"src": "dialer"},
        "custom_body": {"lead_id": "{{lead_id}}"},
    }
    resp = client.put(
        f"/api/bots/{bot['_id']}/draft",
        json={"config": {"function_calling": True, "functions": [legacy_fn]}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/api/bots/{bot['_id']}", headers=auth_headers).json()
    saved = detail["versions"][0]["config"]["functions"][0]
    for k, v in legacy_fn.items():
        assert saved.get(k) == v, f"legacy function field {k} dropped: got {saved.get(k)!r}"
    # New typed defaults are applied so the pipeline/UI can rely on them.
    assert saved["trigger"] == "during_call"
    assert saved["enabled"] is True
    assert saved["timeout_ms"] == 120000


def test_empty_bot_list_returns_empty_array_not_error(client, auth_headers):
    resp = client.get("/api/bots", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_bot_creates_initial_draft_version(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    assert bot["name"] == "Test Bot"
    assert bot["draft_version_id"]

    detail = client.get(f"/api/bots/{bot['_id']}", headers=auth_headers).json()
    assert len(detail["versions"]) == 1
    assert detail["versions"][0]["state"] == "draft"
    assert detail["versions"][0]["version"] == 1


def test_get_unknown_bot_returns_404_not_500(client, auth_headers):
    resp = client.get("/api/bots/000000000000000000000000", headers=auth_headers)
    assert resp.status_code == 404


def test_create_bot_requires_non_empty_name(client, auth_headers):
    resp = client.post(
        "/api/bots",
        json={"name": "   ", "description": "d", "config": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "Name is required" in resp.json()["detail"]


def test_get_bot_with_malformed_id_returns_404_not_500(client, auth_headers):
    resp = client.get("/api/bots/not-an-object-id", headers=auth_headers)
    assert resp.status_code == 404


def test_publish_draft_then_active_version_set_and_draft_cleared(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    resp = client.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)
    assert resp.status_code == 200
    active_version_id = resp.json()["active_version_id"]

    detail = client.get(f"/api/bots/{bot['_id']}", headers=auth_headers).json()
    assert detail["bot"]["active_version_id"] == active_version_id
    assert detail["bot"]["draft_version_id"] is None
    published = next(v for v in detail["versions"] if v["_id"] == active_version_id)
    assert published["state"] == "published"


def test_publish_without_draft_returns_helpful_400(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    client.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)
    resp = client.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)
    assert resp.status_code == 400
    assert "No draft to publish" in resp.json()["detail"]


def test_update_version_rejects_editing_published_version(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    publish_resp = client.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)
    active_id = publish_resp.json()["active_version_id"]

    resp = client.put(
        f"/api/bots/{bot['_id']}/versions/{active_id}",
        json={"config": {"organization_name": "Changed"}},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "immutable" in resp.json()["detail"] or "Cannot edit" in resp.json()["detail"]


def test_publishing_new_version_does_not_mutate_previous_published_snapshot(client, auth_headers):
    """Acceptance criterion: in-flight calls on a published version keep their original
    config snapshot even after a newer version is published."""
    bot = _create_bot(client, auth_headers)
    v1_publish = client.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)
    v1_id = v1_publish.json()["active_version_id"]

    client.put(
        f"/api/bots/{bot['_id']}/draft",
        json={"config": {"organization_name": "Changed Org"}},
        headers=auth_headers,
    )
    client.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)

    detail = client.get(f"/api/bots/{bot['_id']}", headers=auth_headers).json()
    v1_snapshot = next(v for v in detail["versions"] if v["_id"] == v1_id)
    assert v1_snapshot["config"]["organization_name"] == "Acmecorp"
    assert v1_snapshot["state"] == "published"


def test_rollback_forks_new_draft_instead_of_mutating_history(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    v1_publish = client.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)
    v1_id = v1_publish.json()["active_version_id"]

    resp = client.post(f"/api/bots/{bot['_id']}/rollback/{v1_id}", headers=auth_headers)
    assert resp.status_code == 200
    new_draft_id = resp.json()["draft_version_id"]
    assert new_draft_id != v1_id

    detail = client.get(f"/api/bots/{bot['_id']}", headers=auth_headers).json()
    original = next(v for v in detail["versions"] if v["_id"] == v1_id)
    assert original["state"] == "published"


def test_rename_bot_requires_non_empty_name(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    resp = client.put(f"/api/bots/{bot['_id']}", json={"name": "  "}, headers=auth_headers)
    assert resp.status_code == 400
    assert "Name is required" in resp.json()["detail"]


def test_delete_bot_is_soft_delete_and_hides_from_list(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    resp = client.delete(f"/api/bots/{bot['_id']}", headers=auth_headers)
    assert resp.status_code == 200

    listing = client.get("/api/bots", headers=auth_headers).json()
    assert all(b["_id"] != bot["_id"] for b in listing)


def test_delete_unknown_bot_returns_404(client, auth_headers):
    resp = client.delete("/api/bots/000000000000000000000000", headers=auth_headers)
    assert resp.status_code == 404


def test_unpublish_moves_active_version_back_to_draft(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    publish_resp = client.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)
    active_id = publish_resp.json()["active_version_id"]

    resp = client.post(f"/api/bots/{bot['_id']}/unpublish", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["draft_version_id"] == active_id


def test_unpublish_with_existing_draft_returns_helpful_400(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    resp = client.post(f"/api/bots/{bot['_id']}/unpublish", headers=auth_headers)
    assert resp.status_code == 400
    assert "draft already exists" in resp.json()["detail"]


def test_unpublish_without_any_published_version_returns_helpful_400(client, auth_headers):
    from bson import ObjectId

    from backend import db as db_module

    bot = _create_bot(client, auth_headers)
    db_module.bots.update_one({"_id": ObjectId(bot["_id"])}, {"$set": {"draft_version_id": None}})
    resp = client.post(f"/api/bots/{bot['_id']}/unpublish", headers=auth_headers)
    assert resp.status_code == 400
    assert "No published version" in resp.json()["detail"]


def test_saving_a_draft_persists_every_field_the_pipeline_actually_reads(client, auth_headers):
    """Regression test: BotConfig previously only declared a subset of fields, so
    Pydantic silently dropped anything else (agent_name, temperature, function_calling,
    functions, post_speech_hold_ms, silero_*, inactivity_*_secs, api_urls, recording,
    prompt_config) on every save — bot_pipeline.py never actually received PM-configured
    values for these, always falling back to hardcoded defaults."""
    bot = _create_bot(client, auth_headers)
    full_config = {
        "organization_name": "Acmecorp",
        "agent_name": "Priya",
        "temperature": 0.85,
        "function_calling": True,
        "functions": [
            {
                "id": "fn1",
                "name": "fetch_lead",
                "description": "Fetch lead details",
                "url": "http://mis.internal/lead",
                "method": "POST",
                "timeout_ms": 8000,
                "trigger": "pre_call",
                "parameters": [
                    {"name": "mobile", "description": "caller mobile", "type": "string", "required": True}
                ],
                "store_variables": [{"variable": "lead_name", "json_path": "data.name"}],
            }
        ],
        "post_speech_hold_ms": 650,
        "silero_threshold": 0.72,
        "silero_min_speech_ms": 1200,
        "inactivity_first_rescue_secs": 3.5,
        "inactivity_first_nudge_gap_secs": 5.0,
        "inactivity_nudge_secs": 12.0,
        "inactivity_close_secs": 6.5,
        "api_urls": {"mis_api_base": "http://mis.internal"},
        "recording": {"service_id": 293, "dialer_city": "bangalore"},
        "prompt_config": {"timeout_message": "Thanks, ending the call now."},
        "stt_provider": "deepgram",
        "stt_model": "nova-2",
        "stt_language": "hi",
        "tts_provider": "elevenlabs",
        "tts_voice": "some-voice-id",
        "tts_language": "hi-IN",
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "stt_options": {"mode": "translate", "sample_rate": 16000},
        "tts_options": {"pace": 1.2, "output_audio_codec": "mp3"},
        "llm_options": {"top_p": 0.9, "max_output_tokens": 512},
    }
    resp = client.put(f"/api/bots/{bot['_id']}/draft", json={"config": full_config}, headers=auth_headers)
    assert resp.status_code == 200, resp.text

    detail = client.get(f"/api/bots/{bot['_id']}", headers=auth_headers).json()
    saved_config = detail["versions"][0]["config"]
    for key, value in full_config.items():
        if key == "functions":
            # CustomFunction fills schema defaults on save, so assert the fields we set
            # survived rather than exact equality against the sparse input.
            saved_fn = saved_config["functions"][0]
            expected_fn = value[0]
            for fk, fv in expected_fn.items():
                assert saved_fn.get(fk) == fv, f"functions[0].{fk} dropped: got {saved_fn.get(fk)!r}"
            continue
        assert saved_config.get(key) == value, f"{key} was dropped on save: got {saved_config.get(key)!r}"


def test_bot_list_includes_server_aggregated_call_count(client, auth_headers):
    """Regression test: the frontend used to compute "Calls" per bot by filtering
    whatever page of transcripts the Transcripts view happened to have loaded client-side
    (capped at its own fetch limit) — silently wrong for any bot with more calls than that.
    GET /api/bots now aggregates the real count server-side over the whole collection."""
    from backend import db as db_module

    bot_a = _create_bot(client, auth_headers, name="Bot A")
    bot_b = _create_bot(client, auth_headers, name="Bot B")

    db_module.transcripts.insert_many([
        {"bot_id": bot_a["_id"], "call_id": "c1"},
        {"bot_id": bot_a["_id"], "call_id": "c2"},
        {"bot_id": bot_a["_id"], "call_id": "c3"},
        {"bot_id": bot_b["_id"], "call_id": "c4"},
    ])

    listed = client.get("/api/bots", headers=auth_headers).json()
    by_id = {b["_id"]: b for b in listed}
    assert by_id[bot_a["_id"]]["call_count"] == 3
    assert by_id[bot_b["_id"]]["call_count"] == 1


def test_bot_list_call_count_is_zero_for_a_bot_with_no_calls(client, auth_headers):
    bot = _create_bot(client, auth_headers)
    listed = client.get("/api/bots", headers=auth_headers).json()
    assert listed[0]["call_count"] == 0
