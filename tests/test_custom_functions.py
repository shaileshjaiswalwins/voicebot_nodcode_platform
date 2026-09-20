"""Tests for custom_functions.py — the pure, HTTP-free logic behind configurable
per-bot custom functions (Plan 02, Phase 2).

The network call itself lives in the pipeline (bot.py / bot_dev.py), but everything
that decides *what* request to make and *what* to extract from the response is factored
here so it can be unit-tested without a live server and reused by all three pipelines.
"""

import pytest

from custom_functions import (
    apply_store_variables,
    build_http_call,
    build_tool_schema,
    extract_json_path,
    resolve_timeout_seconds,
    selectable_during_call_functions,
)

BUILTIN_TOOL_NAMES = {"FetchLead", "FetchCategorySchema"}


# ── extract_json_path ────────────────────────────────────────────────────────

def test_extract_top_level_key():
    assert extract_json_path({"name": "Priya"}, "name") == "Priya"


def test_extract_nested_key():
    assert extract_json_path({"data": {"lead": {"name": "Priya"}}}, "data.lead.name") == "Priya"


def test_extract_list_index():
    assert extract_json_path({"results": [{"id": "a"}, {"id": "b"}]}, "results.1.id") == "b"


def test_extract_missing_key_returns_none():
    assert extract_json_path({"data": {}}, "data.name") is None


def test_extract_through_non_container_returns_none():
    assert extract_json_path({"name": "Priya"}, "name.deeper") is None


def test_extract_list_index_out_of_range_returns_none():
    assert extract_json_path({"r": [1]}, "r.5") is None


def test_extract_from_none_returns_none():
    assert extract_json_path(None, "a.b") is None


def test_extract_falsy_but_present_value_is_returned():
    # 0 / False / "" are valid extracted values, not "missing".
    assert extract_json_path({"count": 0}, "count") == 0
    assert extract_json_path({"ok": False}, "ok") is False


# ── apply_store_variables ────────────────────────────────────────────────────

def test_apply_store_variables_writes_into_target():
    target = {}
    extracted = apply_store_variables(
        [{"variable": "lead_name", "json_path": "data.name"}],
        {"data": {"name": "Priya"}},
        target,
    )
    assert target == {"lead_name": "Priya"}
    assert extracted == {"lead_name": "Priya"}


def test_apply_store_variables_skips_missing_paths():
    target = {"existing": "keep"}
    extracted = apply_store_variables(
        [{"variable": "missing", "json_path": "data.absent"}],
        {"data": {}},
        target,
    )
    assert target == {"existing": "keep"}  # untouched
    assert extracted == {}


def test_apply_store_variables_ignores_incomplete_specs():
    target = {}
    apply_store_variables(
        [{"variable": "", "json_path": "x"}, {"variable": "v", "json_path": ""}],
        {"x": 1},
        target,
    )
    assert target == {}


def test_apply_store_variables_handles_non_dict_response_gracefully():
    target = {}
    assert apply_store_variables([{"variable": "v", "json_path": "a"}], "not-json", target) == {}
    assert target == {}


# ── resolve_timeout_seconds ──────────────────────────────────────────────────

def test_timeout_converts_ms_to_seconds():
    assert resolve_timeout_seconds({"timeout_ms": 5000}) == 5.0


def test_timeout_falls_back_to_default_when_missing():
    assert resolve_timeout_seconds({}, default=8.0) == 8.0


def test_timeout_falls_back_when_non_positive_or_invalid():
    assert resolve_timeout_seconds({"timeout_ms": 0}, default=8.0) == 8.0
    assert resolve_timeout_seconds({"timeout_ms": -1}, default=8.0) == 8.0
    assert resolve_timeout_seconds({"timeout_ms": "oops"}, default=8.0) == 8.0


# ── build_http_call ──────────────────────────────────────────────────────────

def test_get_merges_query_params_and_args_into_params():
    call = build_http_call(
        {"url": "http://x/api", "method": "GET", "query_params": {"src": "dialer"}},
        {"mobile": "999"},
    )
    assert call["method"] == "GET"
    assert call["url"] == "http://x/api"
    assert call["params"] == {"src": "dialer", "mobile": "999"}
    assert call["json"] is None and call["data"] is None


def test_post_json_mode_puts_args_in_json_body():
    call = build_http_call(
        {"url": "http://x/api", "method": "POST", "body_mode": "json", "query_params": {"q": "1"}},
        {"mobile": "999"},
    )
    assert call["method"] == "POST"
    assert call["params"] == {"q": "1"}  # query params still go on the URL
    assert call["json"] == {"mobile": "999"}
    assert call["data"] is None


def test_post_form_mode_puts_args_in_data_body():
    call = build_http_call(
        {"url": "http://x/api", "method": "POST", "body_mode": "form"},
        {"mobile": "999"},
    )
    assert call["data"] == {"mobile": "999"}
    assert call["json"] is None


def test_legacy_body_format_form_is_honored():
    # Old configs used body_format='form' instead of body_mode.
    call = build_http_call(
        {"url": "http://x/api", "method": "POST", "body_format": "form"},
        {"a": "1"},
    )
    assert call["data"] == {"a": "1"}


def test_custom_body_is_merged_and_overridden_by_args():
    call = build_http_call(
        {"url": "http://x/api", "method": "POST", "body_mode": "json",
         "custom_body": {"static": "s", "mobile": "old"}},
        {"mobile": "new"},
    )
    assert call["json"] == {"static": "s", "mobile": "new"}


def test_headers_passed_through():
    call = build_http_call(
        {"url": "http://x/api", "method": "POST", "headers": {"Authorization": "Bearer t"}},
        {},
    )
    assert call["headers"] == {"Authorization": "Bearer t"}


def test_method_defaults_to_post_and_uppercases():
    assert build_http_call({"url": "http://x", "method": "get"}, {})["method"] == "GET"
    assert build_http_call({"url": "http://x"}, {})["method"] == "POST"


# ── build_tool_schema ────────────────────────────────────────────────────────

def test_tool_schema_from_parameters():
    schema = build_tool_schema({
        "name": "get_lead",
        "description": "Fetch lead",
        "parameters": [
            {"name": "mobile", "description": "caller number", "type": "string", "required": True},
            {"name": "verbose", "type": "boolean", "required": False},
        ],
    })
    assert schema["name"] == "get_lead"
    assert schema["description"] == "Fetch lead"
    props = schema["parameters"]["properties"]
    assert props["mobile"] == {"type": "string", "description": "caller number"}
    assert props["verbose"] == {"type": "boolean"}
    assert schema["parameters"]["required"] == ["mobile"]
    assert schema["parameters"]["type"] == "object"


def test_tool_schema_no_required_omits_required_key():
    schema = build_tool_schema({"name": "n", "parameters": [{"name": "x", "type": "string"}]})
    assert "required" not in schema["parameters"]


def test_tool_schema_empty_parameters_is_valid_object():
    schema = build_tool_schema({"name": "n"})
    assert schema["parameters"] == {"type": "object", "properties": {}}


def test_tool_schema_skips_unnamed_params():
    schema = build_tool_schema({"name": "n", "parameters": [{"name": "", "type": "string"}]})
    assert schema["parameters"]["properties"] == {}


def test_tool_schema_json_mode_uses_raw_body_schema_verbatim():
    raw = {"type": "object", "properties": {"payload": {"type": "object"}}, "required": ["payload"]}
    schema = build_tool_schema({"name": "n", "raw_body_schema": raw})
    assert schema["parameters"] == raw


# ── selectable_during_call_functions ─────────────────────────────────────────

def test_selects_only_enabled_during_call_functions_with_a_name():
    fns = [
        {"name": "a", "trigger": "during_call", "enabled": True},
        {"name": "b", "trigger": "pre_call", "enabled": True},      # wrong trigger
        {"name": "c", "trigger": "during_call", "enabled": False},  # disabled
        {"name": "", "trigger": "during_call", "enabled": True},    # no name
        {"trigger": "during_call", "enabled": True},                 # legacy: trigger defaults ok but no name
    ]
    out = selectable_during_call_functions(fns, reserved=BUILTIN_TOOL_NAMES)
    assert [f["name"] for f in out] == ["a"]


def test_missing_trigger_defaults_to_during_call():
    fns = [{"name": "a"}]  # legacy entry with no trigger field
    out = selectable_during_call_functions(fns, reserved=BUILTIN_TOOL_NAMES)
    assert [f["name"] for f in out] == ["a"]


def test_builtins_are_excluded_so_they_are_not_double_registered():
    fns = [
        {"name": "FetchLead", "trigger": "during_call", "enabled": True},
        {"name": "FetchCategorySchema", "trigger": "during_call", "enabled": True},
        {"name": "custom", "trigger": "during_call", "enabled": True},
    ]
    out = selectable_during_call_functions(fns, reserved=BUILTIN_TOOL_NAMES)
    assert [f["name"] for f in out] == ["custom"]


def test_duplicate_names_are_deduped_keeping_first():
    fns = [
        {"name": "dup", "trigger": "during_call", "enabled": True, "url": "first"},
        {"name": "dup", "trigger": "during_call", "enabled": True, "url": "second"},
    ]
    out = selectable_during_call_functions(fns, reserved=BUILTIN_TOOL_NAMES)
    assert len(out) == 1 and out[0]["url"] == "first"


# ── functions_for_trigger ────────────────────────────────────────────────────

def test_functions_for_trigger_filters_by_trigger_and_enabled_and_url():
    from custom_functions import functions_for_trigger
    fns = [
        {"name": "a", "trigger": "pre_call", "enabled": True, "url": "http://x"},
        {"name": "b", "trigger": "pre_call", "enabled": False, "url": "http://x"},  # disabled
        {"name": "c", "trigger": "pre_call", "enabled": True, "url": ""},           # no url
        {"name": "d", "trigger": "post_call", "enabled": True, "url": "http://x"},  # other trigger
        {"name": "e", "trigger": "during_call", "enabled": True, "url": "http://x"},
    ]
    assert [f["name"] for f in functions_for_trigger(fns, "pre_call")] == ["a"]
    assert [f["name"] for f in functions_for_trigger(fns, "post_call")] == ["d"]


def test_functions_for_trigger_ignores_default_during_call_for_lifecycle():
    # legacy entries (no trigger) default to during_call, so they never fire as pre/post.
    from custom_functions import functions_for_trigger
    fns = [{"name": "legacy", "enabled": True, "url": "http://x"}]
    assert functions_for_trigger(fns, "pre_call") == []


# ── run_lifecycle_functions ──────────────────────────────────────────────────

def test_run_lifecycle_calls_each_and_stores_variables():
    import asyncio
    from custom_functions import run_lifecycle_functions

    seen = []

    async def fake_call(fn, params):
        seen.append((fn["name"], params))
        return {"data": {"name": "Priya"}}

    fns = [
        {"name": "get_lead", "trigger": "pre_call", "enabled": True, "url": "http://x",
         "store_variables": [{"variable": "lead_name", "json_path": "data.name"}]},
    ]
    target = {}
    results = asyncio.run(
        run_lifecycle_functions(fns, "pre_call", {"mobile": "999"}, fake_call, target)
    )
    assert seen == [("get_lead", {"mobile": "999"})]
    assert target == {"lead_name": "Priya"}
    assert results[0]["name"] == "get_lead" and results[0]["ok"] is True


def test_run_lifecycle_one_failure_does_not_stop_the_rest():
    import asyncio
    from custom_functions import run_lifecycle_functions

    async def flaky_call(fn, params):
        if fn["name"] == "boom":
            raise RuntimeError("network down")
        return {"v": 1}

    fns = [
        {"name": "boom", "trigger": "post_call", "enabled": True, "url": "http://x"},
        {"name": "ok", "trigger": "post_call", "enabled": True, "url": "http://x"},
    ]
    target = {}
    results = asyncio.run(run_lifecycle_functions(fns, "post_call", {}, flaky_call, target))
    assert [r["name"] for r in results] == ["boom", "ok"]
    assert results[0]["ok"] is False and results[1]["ok"] is True


def test_run_lifecycle_none_response_is_not_stored():
    import asyncio
    from custom_functions import run_lifecycle_functions

    async def none_call(fn, params):
        return None

    fns = [{"name": "n", "trigger": "pre_call", "enabled": True, "url": "http://x",
            "store_variables": [{"variable": "v", "json_path": "a"}]}]
    target = {}
    results = asyncio.run(run_lifecycle_functions(fns, "pre_call", {}, none_call, target))
    assert target == {}
    assert results[0]["ok"] is False


# ── interpolate_vars ─────────────────────────────────────────────────────────

def test_interpolate_replaces_known_vars():
    from custom_functions import interpolate_vars
    out = interpolate_vars("Hello {{lead_name}} from {{org}}", {"lead_name": "Priya", "org": "Acme"})
    assert out == "Hello Priya from Acme"


def test_interpolate_supports_vars_prefix():
    from custom_functions import interpolate_vars
    assert interpolate_vars("Hi {{vars.name}}", {"name": "Priya"}) == "Hi Priya"


def test_interpolate_leaves_unknown_placeholders_untouched():
    from custom_functions import interpolate_vars
    assert interpolate_vars("Hi {{missing}}", {"name": "Priya"}) == "Hi {{missing}}"


def test_interpolate_handles_whitespace_and_non_string_values():
    from custom_functions import interpolate_vars
    assert interpolate_vars("n={{ count }}", {"count": 5}) == "n=5"


def test_interpolate_noop_on_empty_or_no_vars():
    from custom_functions import interpolate_vars
    assert interpolate_vars("plain", {}) == "plain"
    assert interpolate_vars("", {"a": "b"}) == ""
