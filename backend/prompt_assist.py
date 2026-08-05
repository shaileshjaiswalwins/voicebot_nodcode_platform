import json
import re

from .analysis_prompts import CALL_ANALYSIS_KEY, DEFAULT_PROMPTS, REQUIRED_PLACEHOLDERS
from .evals import _client, _GEMINI_MODEL

# Per-target instruction pairs for the builder's "Generate/Refine with AI" (Sparkles) button —
# same widget reused across System prompt, Closing line, and Analysis prompt override, each
# with its own generate/refine framing since they're very different kinds of text.
_GENERATE_INSTRUCTIONS = {
    "system_prompt": (
        "You are writing a system prompt for a voice call center agent bot. Write only the system "
        "prompt text itself — no preamble, no markdown, no explanation of what you wrote."
    ),
    "global_prompt": (
        "You are writing the GLOBAL prompt for a voice call center WORKFLOW bot — a deterministic "
        "node graph where every node compiles its own separate LLM instructions. This text is "
        "prepended to every single node's instructions, so it must only contain shared persona/tone/"
        "context that applies across the ENTIRE call (e.g. who the bot is, the organization, overall "
        "tone) — never step-specific instructions, since those belong on individual nodes. Keep it to "
        "1-4 sentences. Write only the prompt text itself — no preamble, no markdown, no explanation."
    ),
    "closing_line": (
        "You are writing the closing line a voice call center agent bot says right after it has "
        "collected everything it needs from the caller and is ending the call. Write ONE short, warm, "
        "natural spoken line — no preamble, no markdown, no explanation, no quotation marks around it. "
        "Match the language/script implied by the instruction (e.g. Hindi-in-Devanagari, Hinglish, "
        "English) — do not silently translate to English."
    ),
}

_REFINE_INSTRUCTIONS = {
    "system_prompt": (
        "You are editing an existing system prompt for a voice call center agent bot. Apply ONLY the "
        "requested change below — leave every other part of the prompt exactly as it is. Output the "
        "full updated system prompt text, nothing else: no preamble, no markdown, no explanation."
    ),
    "global_prompt": (
        "You are editing the GLOBAL prompt for a voice call center WORKFLOW bot — shared persona/tone/"
        "context text prepended to every node's own separate instructions (never step-specific "
        "instructions, those belong on individual nodes). Apply ONLY the requested change below — "
        "leave every other part exactly as it is. Output the full updated global prompt text, nothing "
        "else: no preamble, no markdown, no explanation."
    ),
    "closing_line": (
        "You are editing the closing line a voice call center agent bot says at the end of a call. "
        "Apply ONLY the requested change below. Output just the updated line, nothing else: no "
        "preamble, no markdown, no explanation, no quotation marks around it."
    ),
    "analysis_prompt": (
        "You are editing the post-call ANALYSIS prompt for a voice AI platform — this is not spoken to "
        "the caller; it's fed to a separate LLM after the call ends, with the transcript, to produce a "
        "structured JSON classification. Apply ONLY the requested change below (e.g. add/adjust a "
        "classification rule or disposition option) — leave everything else exactly as it is, "
        "including formatting and section structure.\n\n"
        "CRITICAL — this template is substituted with runtime values via Python str.format(), so it "
        "MUST keep every one of these placeholders present, spelled exactly as shown, wrapped in a "
        "single pair of curly braces, and MUST NOT introduce any other {{...}} placeholder: "
        f"{', '.join('{' + p + '}' for p in sorted(REQUIRED_PLACEHOLDERS[CALL_ANALYSIS_KEY]))}\n\n"
        "Output the full updated analysis prompt text, nothing else: no preamble, no markdown fences, "
        "no explanation."
    ),
}


def generate_prompt(mode: str, instruction: str, current_prompt: str, target: str = "system_prompt") -> str:
    """Powers the builder's per-field "Generate/Refine with AI" button.

    `target` selects which framing to use (system_prompt / closing_line / analysis_prompt) —
    see the instruction dicts above. analysis_prompt has no from-scratch "generate" mode: with
    18 mandatory runtime placeholders (see analysis_prompts.REQUIRED_PLACEHOLDERS) a from-nothing
    rewrite is too likely to drop one and fail the save-time validator
    (bots._validate_config_analysis_prompt), so it's always treated as a refine of the current
    text — or the shared default template (analysis_prompts.DEFAULT_PROMPTS) when the bot has no
    override yet, which the frontend passes as current_prompt in that case anyway.
    """
    if target == "analysis_prompt":
        base_text = current_prompt or DEFAULT_PROMPTS[CALL_ANALYSIS_KEY]
        prompt = (
            f"{_REFINE_INSTRUCTIONS['analysis_prompt']}\n\n"
            f"Current analysis prompt:\n{base_text}\n\n"
            f"Requested change:\n{instruction}"
        )
    elif mode == "refine":
        prompt = (
            f"{_REFINE_INSTRUCTIONS.get(target, _REFINE_INSTRUCTIONS['system_prompt'])}\n\n"
            f"Current text:\n{current_prompt}\n\n"
            f"Requested change:\n{instruction}"
        )
    else:
        prompt = (
            f"{_GENERATE_INSTRUCTIONS.get(target, _GENERATE_INSTRUCTIONS['system_prompt'])}\n\n"
            f"What it should do:\n{instruction}"
        )

    client = _client()
    return client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text.strip()


_CREATE_AGENT_INSTRUCTIONS = (
    "You are helping a non-technical user create a voice call center agent from a plain-English "
    "description of what they want. Given their description, produce exactly three things:\n"
    "1. agent_name — a short, plausible spoken persona first name for the bot (e.g. \"Priya\", \"Rahul\").\n"
    "2. initial_message — the bot's opening line when the call connects. One or two short "
    "conversational sentences, in the bot's own persona, that greet the caller and state why "
    "it's calling/what it can help with.\n"
    "3. system_prompt — a complete system prompt for the bot: its role, the conversation's goal, "
    "a rough step-by-step flow, tone/style guidance, and 2-3 explicit constraints.\n\n"
    "Respond with ONLY a raw JSON object with exactly these three keys (agent_name, "
    "initial_message, system_prompt), no markdown fences, no commentary before or after."
)


def _extract_json_object(text: str) -> dict:
    """Gemini is instructed to return raw JSON but sometimes wraps it in a ```json fence
    anyway — strip that before parsing rather than failing on it."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(cleaned)


def generate_agent_from_description(description: str) -> dict:
    """Create Agent > Create with AI: derives agent_name/initial_message/system_prompt from a
    free-text description in one call, so a non-technical user never has to write a prompt by
    hand. Everything else (TTS/STT/language/etc.) is left to the platform's own defaults —
    this only fills in the fields an LLM can reasonably infer from a short description."""
    prompt = f"{_CREATE_AGENT_INSTRUCTIONS}\n\nUser's description of the agent they want:\n{description}"
    client = _client()
    raw = client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text
    try:
        parsed = _extract_json_object(raw)
        return {
            "agent_name": str(parsed.get("agent_name", "")).strip(),
            "initial_message": str(parsed.get("initial_message", "")).strip(),
            "system_prompt": str(parsed.get("system_prompt", "")).strip(),
        }
    except (json.JSONDecodeError, AttributeError):
        # Model didn't follow the JSON format — fall back to treating the whole response as
        # the system prompt rather than losing the generation entirely; agent_name/
        # initial_message just stay blank for the user to fill in themselves.
        return {"agent_name": "", "initial_message": "", "system_prompt": raw.strip()}


# Sentinel written into any URL field the model would otherwise have to invent (function-node
# webhooks, during_call custom functions). The frontend detects this exact string and renders
# a "needs attention" badge on the node/function until the user replaces it with a real
# endpoint — deliberately unsubtle rather than a guessed-but-wrong URL that looks legitimate.
PLACEHOLDER_URL = "TODO_SET_URL"

_GENERATE_WORKFLOW_INSTRUCTIONS = f"""\
You are helping a non-technical user build a voice call center WORKFLOW bot from a plain-English
description. A workflow bot is a deterministic state machine: a graph of nodes connected by edges,
executed in order at call time (no free-form LLM wandering between steps).

Produce ONLY a raw JSON object (no markdown fences, no commentary) with exactly these top-level keys:

- "agent_name": short spoken persona first name (e.g. "Priya", "Rahul").
- "global_prompt": 1-3 sentences of shared persona/context prepended to every node's instructions.
- "nodes": array of node objects, each: {{"id": string, "data": {{...}}}}. Every "id" must be unique
  and referenced consistently by edges. Node "data.kind" must be one of:
    - "start": {{"kind":"start", "first_message": "<opening line the bot says when the call connects>"}}
      Exactly one start node, and it must be the graph's entry point.
    - "conversation": {{"kind":"conversation", "prompt": "<what this step should accomplish/ask>",
      "variables": [{{"name","type" (string|number|boolean),"required" (bool),"description"}}],
      "transitions": [{{"id","label","condition":"<when the model should take this branch, plain English>"}}]}}
      A conversation node collects info from the caller via natural dialogue. Give it one transition
      per distinct outcome it can lead to (at least one; use one transition with id "default" if there
      is only one way forward).
    - "condition": {{"kind":"condition", "conditions": [{{"id","path":"<variable name to check>",
      "op": one of eq|ne|gt|lt|contains|exists, "value": <literal>, "is_fallback": bool}}]}}
      Deterministic branch on a previously-collected variable, no LLM turn. Always include exactly one
      condition with "is_fallback": true (its "path"/"op"/"value" ignored) as the catch-all branch.
    - "function": {{"kind":"function", "function": {{"url": "{PLACEHOLDER_URL}", "method": "GET"|"POST",
      "headers": {{}}, "query_params": {{}}, "body_format": "json"}}, "output_key": "<variable name the
      response is stored under>"}}
      Represents calling an external API/webhook (e.g. "look up the order status", "check availability").
      ALWAYS set "function.url" to exactly the literal string "{PLACEHOLDER_URL}" — never invent a real
      URL, you do not know the user's actual endpoints. Has exactly one outgoing edge (no sourceHandle).
    - "end_call": {{"kind":"end_call", "closing_message": "<final line the bot says before hanging up>"}}
      Terminal node, no outgoing edges. Include at least one.
    - "global": {{"kind":"global", "trigger_description": "<plain-English condition that can fire from any
      node, e.g. 'caller asks to speak to a human'>", "action": "end_call"|"continue"|"transfer",
      "transfer_number": ""}}
      Optional — only include if the description implies an interrupt-style behavior (e.g. "let them
      transfer to a human agent at any point"). Omit entirely otherwise.
- "edges": array of {{"source": "<node id>", "target": "<node id>", "sourceHandle": "<outcome id>"}}.
  "sourceHandle" must match: the transition id for a conversation node's edge, the condition id for a
  condition node's edge, "" for start/function/end_call/global (function/start have exactly one
  outgoing edge; end_call has none; global's edge is only present when action is "continue").
- "functions": array of during_call custom functions the bot's LLM can call mid-conversation for
  anything NOT already modeled as a function node (leave empty array if not needed). Each:
  {{"name": "<snake_case tool name>", "description": "<when the LLM should call this>",
  "url": "{PLACEHOLDER_URL}", "method": "GET"|"POST", "trigger": "during_call",
  "parameters": [{{"name","description","type":"string"|"number"|"boolean","required":bool}}]}}
  ALWAYS set "url" to exactly "{PLACEHOLDER_URL}" here too.

Design the graph to actually reflect the steps, branches, and API calls implied by the user's
description — use condition nodes for deterministic branching on collected data, function nodes for
each distinct external lookup/action mentioned, and enough conversation nodes to cover every distinct
piece of information that needs collecting. Keep it as small as correctly models the description —
do not pad with unnecessary nodes.
"""


def _autolayout(nodes: list[dict], edges: list[dict]) -> None:
    """The model doesn't reliably produce sane x/y positions, and a bad layout (nodes stacked
    on top of each other, or edges strung between far-apart rows) makes the generated graph
    unreadable before the user has even looked at it. Good enough for a first-draft canvas the
    user is expected to rearrange anyway — but it has to avoid two specific failure modes seen
    in practice:

    1. Plain BFS from the start node assigns each node the depth of whichever incoming edge is
       walked *first*. A node reachable by both a short path and a longer one then sits too
       close to the top, so the edge from its actually-deep parent has to stretch across many
       rows — this is what produced the edges arcing off the top of the canvas. Longest-path
       layering (a node's depth = 1 + max depth of ALL its parents, computed in topological
       order) fixes this: every incoming edge is exactly one row.
    2. `global` nodes have no incoming edge by construction (they fire from anywhere) — a
       depth-from-start walk never reaches them, so naively they'd all get shoved into one
       far-below row, again stretching their outgoing edges across the whole canvas. Instead,
       place each unreached node one row above whichever of its targets sits highest, so its
       one short outgoing edge stays local; only fall back to stacking below everything if it
       has no placed target either.
    """
    by_id = {n["id"]: n for n in nodes}
    incoming: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    outgoing: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        src, tgt = e.get("source"), e.get("target")
        if src in outgoing and tgt in by_id:
            outgoing[src].append(tgt)
        if tgt in incoming and src in by_id:
            incoming[tgt].append(src)

    start_id = next((n["id"] for n in nodes if n.get("data", {}).get("kind") == "start"), None)
    depth: dict[str, int] = {}
    if start_id:
        # Kahn's algorithm restricted to the subgraph reachable from start, so a node's depth
        # is only finalized once every one of its (reachable) parents already has one —
        # guarantees the longest-path property without recursion.
        indegree = {nid: 0 for nid in by_id}
        reachable = {start_id}
        frontier = [start_id]
        while frontier:
            nxt = []
            for nid in frontier:
                for child in outgoing.get(nid, []):
                    if child not in reachable:
                        reachable.add(child)
                        nxt.append(child)
            frontier = nxt
        for nid in reachable:
            indegree[nid] = sum(1 for p in incoming[nid] if p in reachable)
        depth[start_id] = 0
        queue = [start_id]
        while queue:
            nid = queue.pop(0)
            for child in outgoing.get(nid, []):
                if child not in reachable:
                    continue
                depth[child] = max(depth.get(child, 0), depth[nid] + 1)
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)

    # Anything not reachable from start (global-trigger nodes, mainly) — pull each one up next
    # to its nearest placed target rather than stacking them all in one row far below.
    max_depth = max(depth.values(), default=-1)
    remaining = [n["id"] for n in nodes if n["id"] not in depth]
    progress = True
    while remaining and progress:
        progress = False
        still_remaining = []
        for nid in remaining:
            target_depths = [depth[t] for t in outgoing.get(nid, []) if t in depth]
            if target_depths:
                depth[nid] = max(0, min(target_depths) - 1)
                progress = True
            else:
                still_remaining.append(nid)
        remaining = still_remaining
    for nid in remaining:
        max_depth += 1
        depth[nid] = max_depth

    per_depth_count: dict[int, int] = {}
    for n in nodes:
        d = depth[n["id"]]
        col = per_depth_count.get(d, 0)
        per_depth_count[d] = col + 1
        n["position"] = {"x": col * 320, "y": d * 220}


def generate_workflow_from_description(description: str) -> dict:
    """Create Agent > Create workflow with AI: derives a full WorkflowGraphDef (nodes/edges),
    during_call functions, agent_name, and global_prompt from a free-text description in one
    call — the workflow-bot counterpart to generate_agent_from_description above. Any HTTP
    endpoint the description implies is left as PLACEHOLDER_URL rather than invented, since the
    model has no way to know the user's real API surface; the frontend flags those for the user
    to fill in before the bot can actually call them."""
    prompt = f"{_GENERATE_WORKFLOW_INSTRUCTIONS}\n\nUser's description of the workflow bot they want:\n{description}"
    client = _client()
    raw = client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text
    parsed = _extract_json_object(raw)  # let JSONDecodeError propagate — no half-built graph to fall back to

    nodes = parsed.get("nodes") or []
    edges = parsed.get("edges") or []
    for n in nodes:
        n.setdefault("data", {})
    _autolayout(nodes, edges)

    return {
        "agent_name": str(parsed.get("agent_name", "")).strip(),
        "global_prompt": str(parsed.get("global_prompt", "")).strip(),
        "workflow": {"nodes": nodes, "edges": edges},
        "functions": parsed.get("functions") or [],
    }


_REFINE_WORKFLOW_INSTRUCTIONS = f"""\
You are editing an EXISTING voice call center WORKFLOW bot's graph — the same node/edge/function
schema described above (kind: start/conversation/condition/function/end_call/global, with
transitions/conditions/function/output_key per kind, edges keyed by sourceHandle).

You are given the bot's current graph as JSON below, plus a plain-English instruction describing a
change to make. Apply ONLY the requested change — add/remove/rewire/edit the specific nodes and
edges the instruction calls for, and leave every other node, edge, id, and piece of text EXACTLY as
it is. Do not rename existing node ids; new nodes need new unique ids not already used in the
current graph. Do not touch any node's "position" field — layout is recomputed separately.

CRITICAL — do not touch any existing "url" field (in a function node's function.url, or in a
top-level function's "url") that is not exactly the literal string "{PLACEHOLDER_URL}" — those are
real, already-configured endpoints; changing or removing them would break the bot. Only ever WRITE
"{PLACEHOLDER_URL}" into url fields for brand-new function nodes/functions this instruction
introduces — never invent a real URL.

Respond with ONLY a raw JSON object (no markdown fences, no commentary) with exactly these
top-level keys, same shape as a from-scratch generation: "global_prompt" (string — the current
value below unless the instruction specifically asks to change it), "nodes" (the FULL updated node
list, not just the changed ones), "edges" (the FULL updated edge list), "functions" (the FULL
updated during_call functions list).
"""


def refine_workflow_from_instruction(
    current_workflow: dict, current_functions: list, current_global_prompt: str, instruction: str
) -> dict:
    """Workflow tab's "Refine with AI" button: edits an existing bot's graph in place per a
    free-text instruction, rather than generating a brand new one — the workflow-bot
    counterpart to generate_prompt's mode='refine'. Sends the current graph as context so the
    model edits it rather than starting over, and is explicitly told never to touch an
    already-configured (non-placeholder) URL."""
    current_state = json.dumps(
        {"global_prompt": current_global_prompt, "nodes": current_workflow.get("nodes") or [],
         "edges": current_workflow.get("edges") or [], "functions": current_functions or []},
        indent=2,
    )
    prompt = (
        f"{_GENERATE_WORKFLOW_INSTRUCTIONS}\n\n{_REFINE_WORKFLOW_INSTRUCTIONS}\n\n"
        f"Current graph:\n{current_state}\n\nRequested change:\n{instruction}"
    )
    client = _client()
    raw = client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text
    parsed = _extract_json_object(raw)  # let JSONDecodeError propagate — no half-applied edit to fall back to

    nodes = parsed.get("nodes") or []
    edges = parsed.get("edges") or []
    for n in nodes:
        n.setdefault("data", {})
    _autolayout(nodes, edges)

    return {
        "global_prompt": str(parsed.get("global_prompt", current_global_prompt)).strip(),
        "workflow": {"nodes": nodes, "edges": edges},
        "functions": parsed.get("functions") or [],
    }
