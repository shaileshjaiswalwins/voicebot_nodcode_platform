"""Tests for custom_function_tools.build_during_call_tools — turns during_call custom
functions into LiveKit RawFunctionTool objects (Plan 02, Phase 3).

Requires the livekit-agents package (already a runtime dependency)."""

import pytest

pytest.importorskip("livekit.agents")

from custom_function_tools import BUILTIN_TOOL_NAMES, build_during_call_tools


def _names(tools):
    return [t.info.name for t in tools]


def test_builds_one_tool_per_selectable_function():
    calls = []

    async def fake_execute(name, args, functions, call_state):
        calls.append((name, args))
        return {"ok": True}

    functions = [
        {"name": "get_lead", "trigger": "during_call", "enabled": True,
         "parameters": [{"name": "mobile", "type": "string", "required": True}]},
        {"name": "disabled_fn", "trigger": "during_call", "enabled": False},
        {"name": "pre_fn", "trigger": "pre_call", "enabled": True},
        {"name": "FetchLead", "trigger": "during_call", "enabled": True},  # reserved builtin
    ]
    tools = build_during_call_tools(functions, fake_execute, call_state={})
    assert _names(tools) == ["get_lead"]


def test_tool_carries_the_generated_raw_schema():
    async def fake_execute(name, args, functions, call_state):
        return {}

    functions = [{
        "name": "get_lead", "description": "Fetch a lead", "trigger": "during_call", "enabled": True,
        "parameters": [{"name": "mobile", "type": "string", "required": True}],
    }]
    tool = build_during_call_tools(functions, fake_execute, call_state={})[0]
    schema = tool.info.raw_schema
    assert schema["name"] == "get_lead"
    assert schema["description"] == "Fetch a lead"
    assert schema["parameters"]["properties"]["mobile"]["type"] == "string"
    assert schema["parameters"]["required"] == ["mobile"]


@pytest.mark.asyncio
async def test_invoking_a_tool_dispatches_to_execute_with_its_name_and_args():
    calls = []

    async def fake_execute(name, args, functions, call_state):
        calls.append((name, dict(args), call_state is state))
        return {"result": name}

    state = {"vars": {}}
    functions = [
        {"name": "alpha", "trigger": "during_call", "enabled": True,
         "parameters": [{"name": "x", "type": "string"}]},
        {"name": "beta", "trigger": "during_call", "enabled": True,
         "parameters": [{"name": "y", "type": "string"}]},
    ]
    tools = build_during_call_tools(functions, fake_execute, call_state=state)
    # Each tool must dispatch under its OWN name (guards against late-binding closure bugs).
    out_a = await tools[0](None, {"x": "1"})
    out_b = await tools[1](None, {"y": "2"})
    assert out_a == {"result": "alpha"}
    assert out_b == {"result": "beta"}
    assert calls == [("alpha", {"x": "1"}, True), ("beta", {"y": "2"}, True)]


def test_builtin_names_constant_matches_the_pipeline_builtins():
    assert BUILTIN_TOOL_NAMES == {"FetchLead", "FetchCategorySchema"}
