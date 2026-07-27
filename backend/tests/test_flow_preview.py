"""Tests for POST /api/bots/compile-flow-preview — Phase A "compiled prompt" flow preview."""


def test_compile_flow_preview_returns_step_script(client, auth_headers):
    flow = {
        "nodes": [
            {"id": "a", "type": "message", "position": {"x": 0, "y": 0}, "data": {"text": "Hi there"}},
        ],
        "edges": [],
    }
    resp = client.post("/api/bots/compile-flow-preview", json={"flow": flow}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "Step 1 [message]" in body["compiled_prompt"]
    assert "Hi there" in body["compiled_prompt"]


def test_compile_flow_preview_empty_flow_returns_empty_string(client, auth_headers):
    resp = client.post("/api/bots/compile-flow-preview", json={"flow": {}}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["compiled_prompt"] == ""


def test_compile_flow_preview_requires_auth(client):
    resp = client.post("/api/bots/compile-flow-preview", json={"flow": {}})
    assert resp.status_code == 401


def test_compile_flow_preview_rejects_malformed_nodes_with_422_not_500(client, auth_headers):
    """Regression for a fable-model audit finding: before the payload was Pydantic-validated
    (models.CompileFlowPreviewRequest), a malformed node shape (e.g. a bare string instead
    of a node object) would hit compile_flow_to_prompt's internal dict access and raise an
    unhandled 500 instead of a clean validation error."""
    resp = client.post(
        "/api/bots/compile-flow-preview",
        json={"flow": {"nodes": ["not_a_node_object"], "edges": []}},
        headers=auth_headers,
    )
    assert resp.status_code == 422
