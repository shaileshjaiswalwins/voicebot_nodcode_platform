"""Compiles a BotConfig.flow graph (backend/models.py Flow/FlowNode/FlowEdge) into a
structured text block appended to the LLM system prompt.

Phase A of the flow-builder wiring (Acme Vani PRD gap-analysis plan): the visual flow graph
previously had zero runtime effect — nothing in bot.py/bot_pipeline.py/bot_dev.py ever read
it. This makes the graph *do something* by turning it into step-by-step guidance the LLM is
told to follow. It is intentionally NOT a deterministic interpreter — the model can still
skip/reorder steps — that is Phase B (the pipeline tracking current_node_id as real call
state and using code, not the LLM, to decide transitions). Ship this first because it's a
same-day change with no pipeline architecture risk; only invest in Phase B once real usage
shows the LLM doesn't follow this reliably past a handful of nodes.
"""

from __future__ import annotations

_MAX_NODES = 200  # guards against pathological/cyclic graphs blowing up the prompt


def _node_content(node: dict) -> str:
    ntype = node.get("type", "message")
    data = node.get("data") or {}
    if ntype == "start":
        greeting = (data.get("first_message") or "").strip()
        return f'Say (opening greeting): "{greeting}"' if greeting else "Greet the caller (no greeting configured)."
    if ntype == "message":
        # `prompt` is the current field name (a narrow, single-purpose instruction for this
        # step); `text` is kept for flows saved before that rename.
        text = (data.get("prompt") or data.get("text") or "").strip()
        return f'Say: "{text}"' if text else "Say something appropriate here (no text configured)."
    if ntype == "condition":
        rules = data.get("rules") or []
        if rules:
            parts = [
                f"{r.get('variable', '?')} {r.get('operator', 'equals')} {r.get('value', '')}".strip()
                for r in rules
                if isinstance(r, dict)
            ]
            return "Evaluate: " + "; ".join(parts) + " — otherwise fall through to Else."
        expr = (data.get("expression") or "").strip()
        return f"Evaluate: {expr}" if expr else "Evaluate the caller's most recent response."
    if ntype == "tool_call":
        url = (data.get("url") or "").strip()
        tool = (data.get("tool_name") or "").strip()
        output_key = (data.get("output_key") or "").strip()
        if url:
            suffix = f" Store the response as {output_key}." if output_key else ""
            return f"Call the API at {url}.{suffix}"
        return f"Call the {tool} tool." if tool else "Call the configured tool."
    if ntype == "transfer":
        target = (data.get("target_bot_id") or "").strip()
        return f"Transfer this call to bot {target}." if target else "Transfer this call."
    if ntype == "global":
        trigger = (data.get("trigger_description") or "").strip()
        action = data.get("action") or "end_call"
        action_text = {
            "end_call": "end the call",
            "transfer": f"transfer to {(data.get('transfer_number') or '').strip() or 'the configured number'}",
            "continue": "handle it and then resume the flow exactly where the caller left off" if data.get("resume") else "handle it and continue",
        }.get(action, "handle it")
        return (
            f'This step can fire at any point, whenever it matches: "{trigger}". When it does, {action_text}.'
            if trigger
            else "This step can fire at any point (no trigger configured)."
        )
    if ntype == "end":
        closing = (data.get("closing_message") or "").strip()
        return f'Say (closing message): "{closing}", then end the call.' if closing else "End the call."
    return "Continue the conversation."


def compile_flow_to_prompt(flow: dict) -> str:
    """Returns "" for an empty/missing flow (no-op — callers should skip appending it)."""
    nodes = flow.get("nodes") or []
    edges = flow.get("edges") or []
    if not nodes:
        return ""

    nodes_by_id = {n["id"]: n for n in nodes if n.get("id")}
    outgoing: dict[str, list[dict]] = {}
    incoming_count: dict[str, int] = {nid: 0 for nid in nodes_by_id}
    for e in edges:
        src, tgt = e.get("source"), e.get("target")
        if src not in nodes_by_id or tgt not in nodes_by_id:
            continue
        outgoing.setdefault(src, []).append(e)
        incoming_count[tgt] = incoming_count.get(tgt, 0) + 1

    # Entry point(s): explicit Start node(s) win when present (a flow should have exactly
    # one, but traversal doesn't hard-require that). Otherwise fall back to nodes nothing
    # points to, or the first declared node for a fully-cyclic graph with no Start node.
    start_ids = [nid for nid, n in nodes_by_id.items() if n.get("type") == "start"]
    entry_ids = start_ids or [nid for nid, count in incoming_count.items() if count == 0] or [nodes[0]["id"]]

    order: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited or len(order) >= _MAX_NODES:
            return
        visited.add(node_id)
        order.append(node_id)
        for edge in outgoing.get(node_id, []):
            visit(edge["target"])

    for entry_id in entry_ids:
        visit(entry_id)
    # Anything unreachable from an entry node still gets documented, at the end, so a PM
    # never loses a node silently just because it's disconnected from the main graph.
    for nid in nodes_by_id:
        visit(nid)

    step_number = {nid: i + 1 for i, nid in enumerate(order)}

    lines = [
        "━━━ CONVERSATION FLOW (author-defined step script) ━━━",
        "The PM who built this bot laid out the conversation as the numbered steps below.",
        "Follow them in order unless a step's transition rule sends you elsewhere. Do not "
        "skip a step's required action (message/tool call/transfer/end) even if you could "
        "improvise something similar — say or do exactly what the step specifies.",
        "If this prompt also contains a MANDATORY OPENING or QUALIFICATION QUESTIONS "
        "section elsewhere, those sections' exact wording takes precedence over a step's "
        "wording — treat these steps as the call's overall structure, not a wording override.",
        "",
    ]
    for nid in order:
        node = nodes_by_id[nid]
        n = step_number[nid]
        ntype = node.get("type", "message")
        lines.append(f"Step {n} [{ntype}]: {_node_content(node)}")
        out_edges = outgoing.get(nid, [])
        if not out_edges:
            if ntype != "end":
                lines.append("  -> No further steps defined — use judgement, or close the call.")
        else:
            for edge in out_edges:
                target_n = step_number.get(edge.get("target"))
                if target_n is None:
                    continue
                condition = (edge.get("condition") or edge.get("label") or "").strip()
                if condition:
                    lines.append(f"  -> if {condition}: go to step {target_n}")
                else:
                    lines.append(f"  -> go to step {target_n}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines).rstrip()
