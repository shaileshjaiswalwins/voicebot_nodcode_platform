"""Tests for Part 2 of the post-call-analysis revamp: the schema-driven generic
extractor (backend/post_call_analysis.py), its router-side validation
(backend/routers/bots.py::_validate_config_analysis_fields), and the gate in
bot.py::_save_transcript_to_dashboard_db that decides which of the two analysis
systems (generic vs legacy) runs for a given call.
"""

import asyncio

import pytest

from backend.post_call_analysis import (
    _coerce_value,
    _empty_result,
    empty_analysis_result,
    generate_generic_analysis,
)


# ---------------------------------------------------------------------------
# _coerce_value / type validation
# ---------------------------------------------------------------------------

BOOL_FIELD = {"key": "resolved", "type": "boolean"}
TEXT_FIELD = {"key": "summary", "type": "text"}
NUMBER_FIELD = {"key": "score", "type": "number"}
ENUM_FIELD = {"key": "sentiment", "type": "enum", "enum_options": ["positive", "neutral", "negative"]}


class TestCoerceValueBoolean:
    def test_valid_bool_passes_through(self):
        assert _coerce_value(True, BOOL_FIELD) is True
        assert _coerce_value(False, BOOL_FIELD) is False

    def test_string_true_false_coerced(self):
        assert _coerce_value("true", BOOL_FIELD) is True
        assert _coerce_value("False", BOOL_FIELD) is False

    def test_mismatched_type_falls_back_to_false(self):
        assert _coerce_value("not-a-bool", BOOL_FIELD) is False
        assert _coerce_value(123, BOOL_FIELD) is False
        assert _coerce_value(None, BOOL_FIELD) is False


class TestCoerceValueText:
    def test_string_passes_through(self):
        assert _coerce_value("hello", TEXT_FIELD) == "hello"

    def test_none_becomes_empty_string(self):
        assert _coerce_value(None, TEXT_FIELD) == ""

    def test_non_string_coerced_to_string(self):
        assert _coerce_value(42, TEXT_FIELD) == "42"
        assert _coerce_value(True, TEXT_FIELD) == "True"


class TestCoerceValueNumber:
    def test_valid_number_passes_through(self):
        assert _coerce_value(7, NUMBER_FIELD) == 7
        assert _coerce_value(3.5, NUMBER_FIELD) == 3.5

    def test_numeric_string_coerced(self):
        assert _coerce_value("7", NUMBER_FIELD) == 7
        assert _coerce_value("3.5", NUMBER_FIELD) == 3.5

    def test_mismatched_type_falls_back_to_zero(self):
        assert _coerce_value("not-a-number", NUMBER_FIELD) == 0
        assert _coerce_value(None, NUMBER_FIELD) == 0
        # bool is a subclass of int but should not be accepted as a number here.
        assert _coerce_value(True, NUMBER_FIELD) == 0


class TestCoerceValueEnum:
    def test_value_in_options_passes_through(self):
        assert _coerce_value("positive", ENUM_FIELD) == "positive"

    def test_value_not_in_options_falls_back_to_empty_string(self):
        assert _coerce_value("furious", ENUM_FIELD) == ""
        assert _coerce_value(None, ENUM_FIELD) == ""
        assert _coerce_value(123, ENUM_FIELD) == ""


def test_empty_result_produces_per_type_defaults():
    fields = [BOOL_FIELD, TEXT_FIELD, NUMBER_FIELD, ENUM_FIELD]
    result = _empty_result(fields)
    assert result == {
        "resolved": False,
        "summary": "",
        "score": 0,
        "sentiment": "",
    }


# ---------------------------------------------------------------------------
# generate_generic_analysis — skip logic
# ---------------------------------------------------------------------------

class _SpyHttpSession:
    """Stand-in for aiohttp.ClientSession — records whether .post() was ever called,
    and (when configured) returns a canned JSON response from a fake Gemini call."""

    def __init__(self, response_json=None):
        self.post_called = False
        self._response_json = response_json if response_json is not None else {"candidates": []}

    def post(self, url, **kwargs):
        self.post_called = True
        return _FakeResponseCtx(self._response_json)


class _FakeResponseCtx:
    def __init__(self, response_json):
        self._response_json = response_json

    async def __aenter__(self):
        return _FakeResponse(self._response_json)

    async def __aexit__(self, *exc):
        return False


class _FakeResponse:
    def __init__(self, response_json, status=200):
        self._response_json = response_json
        self.status = status

    async def json(self):
        return self._response_json

    async def text(self):
        import json as _json

        return _json.dumps(self._response_json)


SAMPLE_FIELDS = [
    {"key": "resolved", "label": "Resolved", "type": "boolean"},
    {"key": "sentiment", "label": "Sentiment", "type": "enum", "enum_options": ["positive", "neutral", "negative"]},
    {"key": "summary", "label": "Summary", "type": "text"},
]

SAMPLE_TRANSCRIPT = [
    {"role": "agent", "text": "Hello, how can I help?"},
    {"role": "user", "text": "My issue is resolved, thanks."},
]


def _gemini_ok_response(payload: dict) -> dict:
    import json as _json

    return {"candidates": [{"content": {"parts": [{"text": _json.dumps(payload)}]}}]}


def test_gemini_connect_failed_short_circuits_without_calling_gemini():
    spy = _SpyHttpSession()
    status, result = asyncio.run(
        generate_generic_analysis(
            SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, spy, gemini_connect_failed=True,
        )
    )
    assert spy.post_called is False
    assert status == "skipped"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}


def test_empty_transcript_short_circuits_without_calling_gemini():
    spy = _SpyHttpSession()
    status, result = asyncio.run(
        generate_generic_analysis([], SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert spy.post_called is False
    assert status == "skipped"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}


def test_transcript_with_no_text_content_short_circuits_without_calling_gemini():
    spy = _SpyHttpSession()
    blank_transcript = [{"role": "agent", "text": ""}, {"role": "user", "text": "   "}]
    status, result = asyncio.run(
        generate_generic_analysis(blank_transcript, SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert spy.post_called is False
    assert status == "skipped"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}


def test_no_fields_configured_returns_empty_dict_without_calling_gemini():
    spy = _SpyHttpSession()
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, [], spy, gemini_connect_failed=False)
    )
    assert spy.post_called is False
    assert status == "ok"
    assert result == {}


# ---------------------------------------------------------------------------
# generate_generic_analysis — happy path
# ---------------------------------------------------------------------------

def test_happy_path_returns_parsed_type_validated_dict():
    gemini_payload = {"resolved": True, "sentiment": "positive", "summary": "Issue was resolved."}
    spy = _SpyHttpSession(response_json=_gemini_ok_response(gemini_payload))
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert spy.post_called is True
    assert status == "ok"
    assert result == {"resolved": True, "sentiment": "positive", "summary": "Issue was resolved."}


def test_happy_path_coerces_bad_values_in_the_response():
    # Gemini returns a sentiment value outside enum_options and a wrong-typed boolean —
    # the function must still return a full, type-safe dict rather than raising.
    gemini_payload = {"resolved": "yes-ish", "sentiment": "furious", "summary": None}
    spy = _SpyHttpSession(response_json=_gemini_ok_response(gemini_payload))
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert status == "ok"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}


def test_gemini_error_response_falls_back_to_empty_result():
    spy = _SpyHttpSession(response_json={"error": {"message": "quota exceeded"}})
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert spy.post_called is True
    assert status == "failed"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}


def test_malformed_json_falls_back_to_empty_result():
    bad_response = {"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]}
    spy = _SpyHttpSession(response_json=bad_response)
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert status == "failed"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}


def test_non_object_json_array_falls_back_to_empty_result():
    # Gemini can legitimately return valid JSON that isn't an object — e.g. a bare
    # array — when responseMimeType=application/json is honored but the model doesn't
    # follow the "respond with an object" instruction. `parsed.get(...)` would raise
    # AttributeError on a list; that must be caught internally rather than propagating.
    bad_response = {"candidates": [{"content": {"parts": [{"text": "[1, 2, 3]"}]}}]}
    spy = _SpyHttpSession(response_json=bad_response)
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert status == "failed"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}


def test_non_object_json_string_falls_back_to_empty_result():
    # Same as above but for a bare JSON string.
    bad_response = {"candidates": [{"content": {"parts": [{"text": '"just a string"'}]}}]}
    spy = _SpyHttpSession(response_json=bad_response)
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert status == "failed"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}


def test_empty_analysis_result_matches_empty_result():
    fields = [BOOL_FIELD, TEXT_FIELD, NUMBER_FIELD, ENUM_FIELD]
    assert empty_analysis_result(fields) == _empty_result(fields)


def test_accepts_analysis_field_def_model_instances():
    from backend.models import AnalysisFieldDef

    fields = [AnalysisFieldDef(key="ok", label="OK", type="boolean")]
    gemini_payload = {"ok": True}
    spy = _SpyHttpSession(response_json=_gemini_ok_response(gemini_payload))
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, fields, spy, gemini_connect_failed=False)
    )
    assert status == "ok"
    assert result == {"ok": True}


# ---------------------------------------------------------------------------
# generate_generic_analysis — non-2xx HTTP status handling (Fix 2)
# ---------------------------------------------------------------------------

class _StatusFakeResponse:
    """Like _FakeResponse but with a configurable .status and .text() for the
    HTTP-status-check branch, which reads status/body before touching .json()."""

    def __init__(self, status, body_text="", response_json=None):
        self.status = status
        self._body_text = body_text
        self._response_json = response_json

    async def text(self):
        return self._body_text

    async def json(self):
        return self._response_json


class _StatusFakeResponseCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _StatusSpyHttpSession:
    def __init__(self, status, body_text=""):
        self.post_called = False
        self._status = status
        self._body_text = body_text

    def post(self, url, **kwargs):
        self.post_called = True
        return _StatusFakeResponseCtx(_StatusFakeResponse(self._status, self._body_text))


@pytest.fixture
def captured_log_messages(monkeypatch):
    """loguru's `logger` isn't bridged into stdlib logging in this codebase (no
    conftest sink wires it up), so `caplog` can't see it — capture messages by
    monkeypatching `logger.error` directly instead, same spirit as caplog but for
    a loguru logger."""
    import backend.post_call_analysis as post_call_analysis_module

    messages = []
    monkeypatch.setattr(post_call_analysis_module.logger, "error", lambda msg: messages.append(msg))
    return messages


def test_non_2xx_http_status_logs_distinct_signature_and_falls_back(captured_log_messages):
    spy = _StatusSpyHttpSession(status=429, body_text="rate limited")
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert spy.post_called is True
    assert status == "failed"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}
    assert any(
        "error_category=http_status" in m and "429" in m for m in captured_log_messages
    )


def test_5xx_http_status_also_tagged_as_http_status_category(captured_log_messages):
    spy = _StatusSpyHttpSession(status=500, body_text="internal error")
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert status == "failed"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}
    assert any(
        "error_category=http_status" in m and "500" in m for m in captured_log_messages
    )


def test_network_exception_tagged_as_network_category(captured_log_messages):
    class _RaisingHttpSession:
        def post(self, url, **kwargs):
            raise ConnectionError("boom")

    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, _RaisingHttpSession(), gemini_connect_failed=False)
    )
    assert status == "failed"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}
    assert any("error_category=network" in m for m in captured_log_messages)


# ---------------------------------------------------------------------------
# Router validation (backend/routers/bots.py::_validate_config_analysis_fields)
# ---------------------------------------------------------------------------

def _create_bot(client, auth_headers, name="Analysis Fields Bot", config=None):
    resp = client.post(
        "/api/bots",
        json={"name": name, "description": "d", "config": config or {"organization_name": "AcmeCorp"}},
        headers=auth_headers,
    )
    return resp


def test_duplicate_analysis_field_keys_rejected(client, auth_headers):
    resp = _create_bot(
        client,
        auth_headers,
        config={
            "organization_name": "AcmeCorp",
            "analysis_fields": [
                {"key": "dup", "label": "A", "type": "text"},
                {"key": "dup", "label": "B", "type": "text"},
            ],
        },
    )
    assert resp.status_code == 400


def test_enum_field_without_enum_options_rejected(client, auth_headers):
    resp = _create_bot(
        client,
        auth_headers,
        config={
            "organization_name": "AcmeCorp",
            "analysis_fields": [{"key": "sentiment", "label": "Sentiment", "type": "enum"}],
        },
    )
    assert resp.status_code == 400


def test_enum_field_with_empty_enum_options_list_rejected(client, auth_headers):
    resp = _create_bot(
        client,
        auth_headers,
        config={
            "organization_name": "AcmeCorp",
            "analysis_fields": [
                {"key": "sentiment", "label": "Sentiment", "type": "enum", "enum_options": []}
            ],
        },
    )
    assert resp.status_code == 400


def test_exceeding_max_analysis_fields_cap_rejected(client, auth_headers):
    from backend.routers.bots import MAX_ANALYSIS_FIELDS_PER_BOT

    fields = [
        {"key": f"field_{i}", "label": f"Field {i}", "type": "text"}
        for i in range(MAX_ANALYSIS_FIELDS_PER_BOT + 1)
    ]
    resp = _create_bot(client, auth_headers, config={"organization_name": "AcmeCorp", "analysis_fields": fields})
    assert resp.status_code == 400


def test_valid_analysis_fields_schema_saves_and_round_trips(client, auth_headers):
    valid_fields = [
        {"key": "resolved", "label": "Resolved", "type": "boolean", "description": "Was the issue resolved?"},
        {
            "key": "sentiment",
            "label": "Sentiment",
            "type": "enum",
            "description": "Overall caller sentiment",
            "enum_options": ["positive", "neutral", "negative"],
        },
        {"key": "summary", "label": "Summary", "type": "text"},
        {"key": "score", "label": "Score", "type": "number"},
    ]
    resp = _create_bot(client, auth_headers, config={"organization_name": "AcmeCorp", "analysis_fields": valid_fields})
    assert resp.status_code == 200, resp.text
    bot = resp.json()

    detail = client.get(f"/api/bots/{bot['_id']}", headers=auth_headers).json()
    saved_fields = detail["versions"][0]["config"]["analysis_fields"]
    assert len(saved_fields) == len(valid_fields)
    saved_by_key = {f["key"]: f for f in saved_fields}
    for expected in valid_fields:
        saved = saved_by_key[expected["key"]]
        assert saved["type"] == expected["type"]
        assert saved["label"] == expected["label"]
        if "enum_options" in expected:
            assert saved["enum_options"] == expected["enum_options"]


def test_at_max_cap_boundary_is_accepted(client, auth_headers):
    from backend.routers.bots import MAX_ANALYSIS_FIELDS_PER_BOT

    fields = [
        {"key": f"field_{i}", "label": f"Field {i}", "type": "text"}
        for i in range(MAX_ANALYSIS_FIELDS_PER_BOT)
    ]
    resp = _create_bot(client, auth_headers, config={"organization_name": "AcmeCorp", "analysis_fields": fields})
    assert resp.status_code == 200, resp.text


def test_save_draft_also_validates_analysis_fields(client, auth_headers):
    resp = _create_bot(client, auth_headers)
    assert resp.status_code == 200, resp.text
    bot = resp.json()
    put_resp = client.put(
        f"/api/bots/{bot['_id']}/draft",
        json={
            "config": {
                "analysis_fields": [
                    {"key": "dup", "label": "A", "type": "text"},
                    {"key": "dup", "label": "B", "type": "text"},
                ]
            }
        },
        headers=auth_headers,
    )
    assert put_resp.status_code == 400


def test_update_version_also_validates_analysis_fields(client, auth_headers):
    resp = _create_bot(client, auth_headers)
    assert resp.status_code == 200, resp.text
    bot = resp.json()
    put_resp = client.put(
        f"/api/bots/{bot['_id']}/versions/{bot['draft_version_id']}",
        json={
            "config": {
                "analysis_fields": [
                    {"key": "sentiment", "label": "Sentiment", "type": "enum", "enum_options": []}
                ]
            }
        },
        headers=auth_headers,
    )
    assert put_resp.status_code == 400


# ---------------------------------------------------------------------------
# bot.py::_save_transcript_to_dashboard_db — the gate between the two analysis systems
#
# Tested directly (not through the full LiveKit call pipeline): the function's
# Mongo writes and telemetry call are monkeypatched to no-ops that capture the saved
# doc, and both Gemini-backed classifiers (the new generic extractor and the legacy
# callback_worker.analysis functions) are monkeypatched to spies so we can assert
# which path actually ran, without hitting a real Gemini endpoint. This is the direct
# route the task asked to attempt first, and it works cleanly because the function
# takes bot_config as a plain dict parameter and the two systems are imported at
# call-time (easy monkeypatch targets) rather than baked in at module import.
# ---------------------------------------------------------------------------

@pytest.fixture
def bot_module(monkeypatch):
    import bot as bot_module

    saved_docs = []

    def _fake_insert_one(doc):
        saved_docs.append(doc)

    class _FakeCollection:
        def insert_one(self, doc):
            _fake_insert_one(doc)

    monkeypatch.setattr(bot_module, "_get_platform_transcripts_collection", lambda: _FakeCollection())
    monkeypatch.setattr(bot_module, "trace_completed_call", lambda *a, **kw: None)
    monkeypatch.setattr(bot_module, "_get_http_session", lambda: object())
    bot_module._test_saved_docs = saved_docs
    return bot_module


def _base_mongo_doc():
    return {
        "room_name": "test-abc123",
        "transcript": [{"role": "agent", "text": "hi"}, {"role": "user", "text": "hello"}],
        "status": "completed",
        "gemini_connect_failed": False,
    }


def test_workflow_bot_with_analysis_fields_writes_result_and_skips_legacy_classifier(bot_module, monkeypatch):
    import backend.post_call_analysis as post_call_analysis_module
    import callback_worker.analysis as legacy_analysis_module

    generic_called = {"count": 0}
    legacy_called = {"count": 0}

    async def _fake_generic(*args, **kwargs):
        generic_called["count"] += 1
        return "ok", {"resolved": True}

    async def _fake_legacy_call_analysis(*args, **kwargs):
        legacy_called["count"] += 1
        return {"call_outcome": "Approved"}

    async def _fake_b2b_score(*args, **kwargs):
        legacy_called["count"] += 1
        return {"deal_value": "100"}

    monkeypatch.setattr(post_call_analysis_module, "generate_generic_analysis", _fake_generic)
    monkeypatch.setattr(legacy_analysis_module, "generate_call_analysis", _fake_legacy_call_analysis)
    monkeypatch.setattr(legacy_analysis_module, "generate_b2b_score", _fake_b2b_score)

    bot_config = {
        "bot_type": "workflow",
        "analysis_fields": [{"key": "resolved", "label": "Resolved", "type": "boolean"}],
    }

    asyncio.run(
        bot_module._save_transcript_to_dashboard_db(
            _base_mongo_doc(), bot_id="bot-1", bot_config=bot_config,
        )
    )

    assert generic_called["count"] == 1
    assert legacy_called["count"] == 0
    saved = bot_module._test_saved_docs[0]
    assert saved["analysis_fields_result"] == {"resolved": True}
    assert saved["analysis_fields_status"] == "ok"
    # No legacy `analysis` object at all for a schema-driven Workflow bot — writing a
    # fabricated fallback value (via status_to_outcome's 2-value guess) would be worse
    # than omitting the field, since it looks real but isn't (see AlertCallOutcome's
    # docstring in backend/models.py for the same gap on the alerting side).
    assert "analysis" not in saved


def test_workflow_bot_generic_extraction_exception_falls_back_to_typed_empty_result(bot_module, monkeypatch):
    # generate_generic_analysis is documented to never raise, but bot.py's outer
    # except block exists as a defense-in-depth for genuine bugs in the calling code
    # (bad config shape, missing session, etc). Its fallback must match the SAME
    # per-type-correct shape as post_call_analysis._empty_result, not untyped None.
    import backend.post_call_analysis as post_call_analysis_module
    import callback_worker.analysis as legacy_analysis_module

    async def _raising_generic(*args, **kwargs):
        raise RuntimeError("boom")

    async def _fake_legacy_call_analysis(*args, **kwargs):
        return {"call_outcome": "Approved"}

    async def _fake_b2b_score(*args, **kwargs):
        return {"deal_value": "100"}

    monkeypatch.setattr(post_call_analysis_module, "generate_generic_analysis", _raising_generic)
    monkeypatch.setattr(legacy_analysis_module, "generate_call_analysis", _fake_legacy_call_analysis)
    monkeypatch.setattr(legacy_analysis_module, "generate_b2b_score", _fake_b2b_score)

    analysis_fields = [
        {"key": "resolved", "label": "Resolved", "type": "boolean"},
        {"key": "sentiment", "label": "Sentiment", "type": "enum", "enum_options": ["positive", "negative"]},
        {"key": "summary", "label": "Summary", "type": "text"},
        {"key": "score", "label": "Score", "type": "number"},
    ]
    bot_config = {"bot_type": "workflow", "analysis_fields": analysis_fields}

    asyncio.run(
        bot_module._save_transcript_to_dashboard_db(
            _base_mongo_doc(), bot_id="bot-1", bot_config=bot_config,
        )
    )

    saved = bot_module._test_saved_docs[0]
    expected = post_call_analysis_module.empty_analysis_result(analysis_fields)
    assert saved["analysis_fields_result"] == expected
    assert saved["analysis_fields_result"] == {
        "resolved": False,
        "sentiment": "",
        "summary": "",
        "score": 0,
    }
    # bot.py's own outer except block (defense-in-depth around a raising
    # generate_generic_analysis) must record status="failed" too, so this doesn't
    # read as a genuine "ok" extraction that happened to come back all-empty.
    assert saved["analysis_fields_status"] == "failed"


def test_workflow_bot_without_analysis_fields_falls_through_to_legacy_path(bot_module, monkeypatch):
    import backend.post_call_analysis as post_call_analysis_module
    import callback_worker.analysis as legacy_analysis_module

    generic_called = {"count": 0}
    legacy_called = {"count": 0}

    async def _fake_generic(*args, **kwargs):
        generic_called["count"] += 1
        return "ok", {}

    async def _fake_legacy_call_analysis(*args, **kwargs):
        legacy_called["count"] += 1
        return {"call_outcome": "Approved", "call_summary": "s"}

    async def _fake_b2b_score(*args, **kwargs):
        return {"deal_value": "100"}

    monkeypatch.setattr(post_call_analysis_module, "generate_generic_analysis", _fake_generic)
    monkeypatch.setattr(legacy_analysis_module, "generate_call_analysis", _fake_legacy_call_analysis)
    monkeypatch.setattr(legacy_analysis_module, "generate_b2b_score", _fake_b2b_score)

    bot_config = {"bot_type": "workflow", "analysis_fields": []}

    asyncio.run(
        bot_module._save_transcript_to_dashboard_db(
            _base_mongo_doc(), bot_id="bot-2", bot_config=bot_config,
        )
    )

    assert generic_called["count"] == 0
    assert legacy_called["count"] == 1
    saved = bot_module._test_saved_docs[0]
    assert "analysis_fields_result" not in saved
    assert "analysis_fields_status" not in saved
    assert saved["analysis"]["call_outcome"] == "Approved"


@pytest.mark.parametrize("bot_type", ["standard", "campaign"])
def test_non_workflow_bot_with_analysis_fields_still_uses_generic_extractor(bot_module, monkeypatch, bot_type):
    """The gate used to also require bot_type == "workflow", on the assumption that
    "standard" bots are all outbound lead-qualification/campaign bots the legacy
    classifier is tuned for. That assumption didn't hold — "standard" is also used for
    non-qualification bots (support, HR, appointment) with no qualification_schema at
    all, which is exactly the "legacy classifier degenerates into meaninglessness"
    problem this feature was built to fix. Presence of a configured schema is now the
    only signal that matters, regardless of bot_type."""
    import backend.post_call_analysis as post_call_analysis_module
    import callback_worker.analysis as legacy_analysis_module

    generic_called = {"count": 0}
    legacy_called = {"count": 0}

    async def _fake_generic(*args, **kwargs):
        generic_called["count"] += 1
        return "ok", {"resolved": True}

    async def _fake_legacy_call_analysis(*args, **kwargs):
        legacy_called["count"] += 1
        return {"call_outcome": "Approved"}

    async def _fake_b2b_score(*args, **kwargs):
        return {"deal_value": "100"}

    monkeypatch.setattr(post_call_analysis_module, "generate_generic_analysis", _fake_generic)
    monkeypatch.setattr(legacy_analysis_module, "generate_call_analysis", _fake_legacy_call_analysis)
    monkeypatch.setattr(legacy_analysis_module, "generate_b2b_score", _fake_b2b_score)

    bot_config = {
        "bot_type": bot_type,
        "analysis_fields": [{"key": "resolved", "label": "Resolved", "type": "boolean"}],
    }

    asyncio.run(
        bot_module._save_transcript_to_dashboard_db(
            _base_mongo_doc(), bot_id="bot-3", bot_config=bot_config,
        )
    )

    assert generic_called["count"] == 1
    assert legacy_called["count"] == 0
    saved = bot_module._test_saved_docs[0]
    assert saved["analysis_fields_result"] == {"resolved": True}
    assert saved["analysis_fields_status"] == "ok"
    assert "analysis" not in saved


@pytest.mark.parametrize("bot_type", ["standard", "campaign", "workflow"])
def test_bot_without_analysis_fields_always_takes_legacy_path_regardless_of_bot_type(bot_module, monkeypatch, bot_type):
    import backend.post_call_analysis as post_call_analysis_module
    import callback_worker.analysis as legacy_analysis_module

    generic_called = {"count": 0}
    legacy_called = {"count": 0}

    async def _fake_generic(*args, **kwargs):
        generic_called["count"] += 1
        return "ok", {"resolved": True}

    async def _fake_legacy_call_analysis(*args, **kwargs):
        legacy_called["count"] += 1
        return {"call_outcome": "Approved"}

    async def _fake_b2b_score(*args, **kwargs):
        return {"deal_value": "100"}

    monkeypatch.setattr(post_call_analysis_module, "generate_generic_analysis", _fake_generic)
    monkeypatch.setattr(legacy_analysis_module, "generate_call_analysis", _fake_legacy_call_analysis)
    monkeypatch.setattr(legacy_analysis_module, "generate_b2b_score", _fake_b2b_score)

    bot_config = {"bot_type": bot_type, "analysis_fields": []}

    asyncio.run(
        bot_module._save_transcript_to_dashboard_db(
            _base_mongo_doc(), bot_id="bot-4", bot_config=bot_config,
        )
    )

    assert generic_called["count"] == 0
    assert legacy_called["count"] == 1
    saved = bot_module._test_saved_docs[0]
    assert "analysis_fields_result" not in saved
    assert "analysis_fields_status" not in saved


# ---------------------------------------------------------------------------
# AnalysisFieldDef model-level invariant (backend/models.py)
# ---------------------------------------------------------------------------


def test_analysis_field_def_enum_without_enum_options_raises_at_construction():
    """The enum_options-required-for-type=='enum' invariant must be enforced by
    AnalysisFieldDef itself (a model_validator), not only by the router's
    _validate_config_analysis_fields — so constructing the model directly, anywhere,
    with an invalid combination is impossible."""
    import pydantic

    from backend.models import AnalysisFieldDef

    with pytest.raises(pydantic.ValidationError):
        AnalysisFieldDef(key="sentiment", label="Sentiment", type="enum")

    with pytest.raises(pydantic.ValidationError):
        AnalysisFieldDef(key="sentiment", label="Sentiment", type="enum", enum_options=[])

    with pytest.raises(pydantic.ValidationError):
        AnalysisFieldDef(key="sentiment", label="Sentiment", type="enum", enum_options=["  ", ""])

    # Sanity check: a valid enum field still constructs fine.
    field = AnalysisFieldDef(key="sentiment", label="Sentiment", type="enum", enum_options=["a", "b"])
    assert field.enum_options == ["a", "b"]


# ---------------------------------------------------------------------------
# _build_prompt — robustness against prompt-breaking key characters
#
# `_build_prompt` interpolates `f['key']` directly into the extraction prompt with no
# escaping, and neither AnalysisFieldDef nor _validate_config_analysis_fields previously
# constrained which characters a key can contain (only the frontend's slugifyKey did,
# client-side, bypassable via a direct API call). _validate_config_analysis_fields now
# also enforces `^[a-zA-Z0-9_]+$` server-side (see backend/routers/bots.py), matching
# what slugifyKey already produces — so a malicious/malformed key can no longer reach
# _build_prompt via the router. _build_prompt itself is still exercised directly here for
# robustness: it must never crash regardless of what key string it's handed.
# ---------------------------------------------------------------------------

class TestBuildPromptKeyCharacterRobustness:
    def test_key_with_double_quote_does_not_crash_and_is_included(self):
        from backend.post_call_analysis import _build_prompt

        fields = [{"key": 'bad"key', "label": "Bad", "type": "text"}]
        prompt = _build_prompt(fields, "AGENT: hi\nUSER: hello")
        assert isinstance(prompt, str)
        assert 'bad"key' in prompt

    def test_key_with_newlines_does_not_crash_and_is_included(self):
        from backend.post_call_analysis import _build_prompt

        fields = [{"key": "key\nwith\nnewlines", "label": "Bad", "type": "text"}]
        prompt = _build_prompt(fields, "AGENT: hi\nUSER: hello")
        assert isinstance(prompt, str)
        assert "key\nwith\nnewlines" in prompt

    def test_generate_generic_analysis_with_prompt_breaking_key_end_to_end(self):
        # Confirm the whole pipeline (prompt build -> mocked Gemini call -> coercion)
        # doesn't crash when a field key contains characters that could break a naively
        # quoted prompt fragment, even though the router should reject such a key before
        # it ever reaches here in practice.
        fields = [{"key": 'weird"key\nvalue', "label": "Weird", "type": "text"}]
        gemini_payload = {'weird"key\nvalue': "some text"}
        spy = _SpyHttpSession(response_json=_gemini_ok_response(gemini_payload))
        status, result = asyncio.run(
            generate_generic_analysis(SAMPLE_TRANSCRIPT, fields, spy, gemini_connect_failed=False)
        )
        assert spy.post_called is True
        assert status == "ok"
        assert result == {'weird"key\nvalue': "some text"}


# ---------------------------------------------------------------------------
# Router validation — key character restriction (backend/routers/bots.py)
# ---------------------------------------------------------------------------

def test_analysis_field_key_with_double_quote_rejected(client, auth_headers):
    resp = _create_bot(
        client,
        auth_headers,
        config={
            "organization_name": "AcmeCorp",
            "analysis_fields": [{"key": 'bad"key', "label": "Bad", "type": "text"}],
        },
    )
    assert resp.status_code == 400


def test_analysis_field_key_with_newlines_rejected(client, auth_headers):
    resp = _create_bot(
        client,
        auth_headers,
        config={
            "organization_name": "AcmeCorp",
            "analysis_fields": [{"key": "key\nwith\nnewlines", "label": "Bad", "type": "text"}],
        },
    )
    assert resp.status_code == 400


def test_analysis_field_key_alphanumeric_underscore_still_accepted(client, auth_headers):
    # Sanity check that the new character-class restriction doesn't regress normal keys.
    resp = _create_bot(
        client,
        auth_headers,
        config={
            "organization_name": "AcmeCorp",
            "analysis_fields": [{"key": "sentiment_v2", "label": "Sentiment", "type": "text"}],
        },
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Router validation — whitespace-normalized duplicate/empty keys
# ---------------------------------------------------------------------------

def test_whitespace_variant_duplicate_keys_rejected(client, auth_headers):
    # " dup" and "dup " normalize to the same stripped key "dup" — must be rejected as a
    # duplicate, not silently accepted and persisted as two colliding output keys.
    resp = _create_bot(
        client,
        auth_headers,
        config={
            "organization_name": "AcmeCorp",
            "analysis_fields": [
                {"key": " dup", "label": "A", "type": "text"},
                {"key": "dup ", "label": "B", "type": "text"},
            ],
        },
    )
    assert resp.status_code == 400


def test_whitespace_only_key_rejected_as_empty(client, auth_headers):
    resp = _create_bot(
        client,
        auth_headers,
        config={
            "organization_name": "AcmeCorp",
            "analysis_fields": [{"key": "   ", "label": "A", "type": "text"}],
        },
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# generate_generic_analysis — the HTTP call itself raising (not just returning a bad
# response). Existing tests only cover a mocked session whose .post() always succeeds in
# returning a response object, even for malformed/error-shaped payloads; none simulate
# the network call raising before any response is received.
# ---------------------------------------------------------------------------

class _RaisingHttpSession:
    """Spy whose .post() raises immediately, simulating a connection failure or timeout
    before any response is ever received (as opposed to a response object carrying an
    error, which the other tests in this file already cover)."""

    def __init__(self, exc):
        self._exc = exc
        self.post_called = False

    def post(self, url, **kwargs):
        self.post_called = True
        raise self._exc


def test_connection_error_during_post_falls_back_cleanly():
    spy = _RaisingHttpSession(ConnectionError("connection refused"))
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert spy.post_called is True
    assert status == "failed"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}


def test_timeout_error_during_post_falls_back_cleanly():
    status, result = asyncio.run(
        generate_generic_analysis(
            SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, _RaisingHttpSession(asyncio.TimeoutError()),
            gemini_connect_failed=False,
        )
    )
    assert status == "failed"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}


def test_safety_blocked_candidate_with_no_content_parts_tagged_as_parse_not_network(
    captured_log_messages,
):
    # A safety-blocked (or otherwise refused) Gemini response has a candidate but no
    # `content.parts` — extracting `data["candidates"][0]["content"]["parts"][0]["text"]`
    # raises KeyError. This is a response-shape/parse problem, not a network problem, and
    # must be tagged error_category=parse, not error_category=network.
    bad_response = {"candidates": [{"finishReason": "SAFETY"}]}
    spy = _SpyHttpSession(response_json=bad_response)
    status, result = asyncio.run(
        generate_generic_analysis(SAMPLE_TRANSCRIPT, SAMPLE_FIELDS, spy, gemini_connect_failed=False)
    )
    assert spy.post_called is True
    assert status == "failed"
    assert result == {"resolved": False, "sentiment": "", "summary": ""}
    assert any("error_category=parse" in m for m in captured_log_messages)
    assert not any("error_category=network" in m for m in captured_log_messages)


# ---------------------------------------------------------------------------
# Router-level: a non-workflow bot with analysis_fields configured still saves (200) —
# the fields are accepted-and-ignored by design (only bot.py's workflow-bot gate ever
# consumes them at runtime), not silently rejected as an oversight. Complements
# test_non_workflow_bot_always_takes_legacy_path_regardless_of_analysis_fields in the
# gate-logic section, which covers the runtime behavior; this covers the save itself.
# ---------------------------------------------------------------------------

def test_save_draft_rejects_enum_field_without_enum_options(client, auth_headers):
    resp = _create_bot(client, auth_headers)
    assert resp.status_code == 200, resp.text
    bot = resp.json()
    put_resp = client.put(
        f"/api/bots/{bot['_id']}/draft",
        json={
            "config": {
                "analysis_fields": [{"key": "sentiment", "label": "Sentiment", "type": "enum"}]
            }
        },
        headers=auth_headers,
    )
    assert put_resp.status_code == 400


def test_update_version_rejects_enum_field_without_enum_options(client, auth_headers):
    resp = _create_bot(client, auth_headers)
    assert resp.status_code == 200, resp.text
    bot = resp.json()
    put_resp = client.put(
        f"/api/bots/{bot['_id']}/versions/{bot['draft_version_id']}",
        json={
            "config": {
                "analysis_fields": [{"key": "sentiment", "label": "Sentiment", "type": "enum"}]
            }
        },
        headers=auth_headers,
    )
    assert put_resp.status_code == 400


def test_save_draft_rejects_analysis_field_key_with_bad_characters(client, auth_headers):
    resp = _create_bot(client, auth_headers)
    assert resp.status_code == 200, resp.text
    bot = resp.json()
    put_resp = client.put(
        f"/api/bots/{bot['_id']}/draft",
        json={
            "config": {
                "analysis_fields": [{"key": 'bad"key', "label": "Bad", "type": "text"}]
            }
        },
        headers=auth_headers,
    )
    assert put_resp.status_code == 400


def test_update_version_rejects_analysis_field_key_with_bad_characters(client, auth_headers):
    resp = _create_bot(client, auth_headers)
    assert resp.status_code == 200, resp.text
    bot = resp.json()
    put_resp = client.put(
        f"/api/bots/{bot['_id']}/versions/{bot['draft_version_id']}",
        json={
            "config": {
                "analysis_fields": [{"key": "key\nwith\nnewlines", "label": "Bad", "type": "text"}]
            }
        },
        headers=auth_headers,
    )
    assert put_resp.status_code == 400


def test_non_workflow_bot_with_analysis_fields_saves_successfully(client, auth_headers):
    resp = _create_bot(
        client,
        auth_headers,
        config={
            "organization_name": "AcmeCorp",
            "bot_type": "standard",
            "analysis_fields": [
                {"key": "resolved", "label": "Resolved", "type": "boolean"},
                {
                    "key": "sentiment",
                    "label": "Sentiment",
                    "type": "enum",
                    "enum_options": ["positive", "neutral", "negative"],
                },
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    bot = resp.json()
    detail = client.get(f"/api/bots/{bot['_id']}", headers=auth_headers).json()
    saved_fields = detail["versions"][0]["config"]["analysis_fields"]
    assert len(saved_fields) == 2
    assert detail["versions"][0]["config"].get("bot_type", "standard") == "standard"
