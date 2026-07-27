"""Tests for flow_compiler.py — Phase A "compiled prompt" flow (turns BotConfig.flow into
step-script text appended to the LLM system prompt)."""

from flow_compiler import compile_flow_to_prompt


def test_empty_flow_returns_empty_string():
    assert compile_flow_to_prompt({}) == ""
    assert compile_flow_to_prompt({"nodes": [], "edges": []}) == ""


def test_single_message_node_with_no_edges():
    flow = {
        "nodes": [{"id": "n1", "type": "message", "position": {"x": 0, "y": 0}, "data": {"text": "Hello there"}}],
        "edges": [],
    }
    out = compile_flow_to_prompt(flow)
    assert "Step 1 [message]" in out
    assert 'Say: "Hello there"' in out
    assert "No further steps defined" in out


def test_linear_flow_orders_steps_by_traversal_from_entry():
    flow = {
        "nodes": [
            {"id": "a", "type": "message", "position": {"x": 0, "y": 0}, "data": {"text": "Ask budget"}},
            {"id": "b", "type": "condition", "position": {"x": 0, "y": 0}, "data": {"expression": "budget > 5000"}},
            {"id": "c", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "a", "target": "b", "label": "", "condition": ""},
            {"id": "e2", "source": "b", "target": "c", "label": "", "condition": "budget confirmed"},
        ],
    }
    out = compile_flow_to_prompt(flow)
    step1_idx = out.index("Step 1 [message]")
    step2_idx = out.index("Step 2 [condition]")
    step3_idx = out.index("Step 3 [end]")
    assert step1_idx < step2_idx < step3_idx
    assert "go to step 2" in out
    assert "if budget confirmed: go to step 3" in out


def test_disconnected_node_still_appears():
    flow = {
        "nodes": [
            {"id": "a", "type": "message", "position": {"x": 0, "y": 0}, "data": {"text": "Start"}},
            {"id": "orphan", "type": "message", "position": {"x": 0, "y": 0}, "data": {"text": "Never linked"}},
        ],
        "edges": [],
    }
    out = compile_flow_to_prompt(flow)
    assert "Never linked" in out


def test_cyclic_graph_does_not_hang():
    flow = {
        "nodes": [
            {"id": "a", "type": "message", "position": {"x": 0, "y": 0}, "data": {"text": "A"}},
            {"id": "b", "type": "message", "position": {"x": 0, "y": 0}, "data": {"text": "B"}},
        ],
        "edges": [
            {"id": "e1", "source": "a", "target": "b", "label": "", "condition": ""},
            {"id": "e2", "source": "b", "target": "a", "label": "", "condition": ""},
        ],
    }
    out = compile_flow_to_prompt(flow)
    assert "Step 1" in out and "Step 2" in out
    assert out.count("Step 3") == 0


def test_edge_pointing_to_unknown_node_is_ignored():
    flow = {
        "nodes": [{"id": "a", "type": "message", "position": {"x": 0, "y": 0}, "data": {"text": "A"}}],
        "edges": [{"id": "e1", "source": "a", "target": "missing", "label": "", "condition": ""}],
    }
    out = compile_flow_to_prompt(flow)
    assert "Step 1" in out
    assert "missing" not in out
