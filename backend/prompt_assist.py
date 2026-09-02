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
    "description of what they want. Given their description, produce exactly nine things:\n"
    "1. agent_name — a common Hindi/Indian first name for the bot's persona (e.g. \"Priya\", \"Rahul\", "
    "\"Meera\", \"Aman\"). Never use an English/Western name (e.g. \"Alex\", \"Sarah\", \"John\"), even "
    "if the conversation itself will be in English.\n"
    "2. description — one short sentence (no headers, no placeholders like \"[Company Name]\") "
    "summarizing what this bot does, consistent with agent_name. Never copy boilerplate structure "
    "from the user's description verbatim.\n"
    "3. persona_gender — \"male\" or \"female\", matching the gender implied by agent_name (e.g. "
    "\"Rahul\" implies \"male\", \"Priya\" implies \"female\").\n"
    "4. stt_language / tts_language — the language the caller and bot will actually speak, as a "
    "BCP-47-style code from this exact set: hi-IN (Hindi), en-IN (English/India), bn-IN (Bengali), "
    "gu-IN (Gujarati), kn-IN (Kannada), ml-IN (Malayalam), mr-IN (Marathi), od-IN (Odia), pa-IN "
    "(Punjabi), ta-IN (Tamil), te-IN (Telugu). Infer this from the description (e.g. \"for Tamil "
    "customers\" -> ta-IN); default to hi-IN if nothing in the description implies a language. Both "
    "fields must be set to the same value.\n"
    "5. initial_message — the bot's opening line when the call connects, written in the language "
    "chosen for tts_language (script and all, not transliterated English). One or two short "
    "conversational sentences, in the bot's own persona, that greet the caller and state why "
    "it's calling/what it can help with.\n"
    "6. call_end_text — the bot's closing line when it hangs up, in the same language as "
    "tts_language, consistent in tone with initial_message.\n"
    "7. system_prompt — a complete system prompt for the bot: its role, the conversation's goal, "
    "a rough step-by-step flow, tone/style guidance, and 2-3 explicit constraints.\n"
    "8. interruption_sensitivity — \"patient\" if the description implies letting callers speak at "
    "length without cutting in (e.g. elderly callers, detailed complaints), \"responsive\" if it "
    "implies a fast, no-nonsense call (e.g. quick verification, sales qualification), otherwise "
    "\"balanced\".\n\n"
    "Respond with ONLY a raw JSON object with exactly these eight keys (agent_name, description, "
    "persona_gender, stt_language, tts_language, initial_message, call_end_text, system_prompt, "
    "interruption_sensitivity), no markdown fences, no commentary before or after."
)


def _extract_json_object(text: str) -> dict:
    """Gemini is instructed to return raw JSON but sometimes wraps it in a ```json fence
    anyway — strip that before parsing rather than failing on it."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(cleaned)


_VALID_SARVAM_LANGUAGES = {
    "hi-IN", "en-IN", "bn-IN", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN",
}
_VALID_INTERRUPTION_SENSITIVITIES = {"patient", "balanced", "responsive"}


def generate_agent_from_description(description: str) -> dict:
    """Create Agent > Create with AI: derives agent_name/initial_message/system_prompt (plus
    persona/language fields below) from a free-text description in one call, so a non-technical
    user never has to write a prompt by hand or hunt down the matching language/voice settings
    themselves. Everything else is left to the platform's own defaults — this only fills in the
    fields an LLM can reasonably infer from a short description."""
    prompt = f"{_CREATE_AGENT_INSTRUCTIONS}\n\nUser's description of the agent they want:\n{description}"
    client = _client()
    raw = client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text
    try:
        parsed = _extract_json_object(raw)
        persona_gender = str(parsed.get("persona_gender", "")).strip().lower()
        if persona_gender not in ("male", "female"):
            persona_gender = "female"
        tts_language = str(parsed.get("tts_language", "")).strip()
        if tts_language not in _VALID_SARVAM_LANGUAGES:
            tts_language = "hi-IN"
        interruption_sensitivity = str(parsed.get("interruption_sensitivity", "")).strip().lower()
        if interruption_sensitivity not in _VALID_INTERRUPTION_SENSITIVITIES:
            interruption_sensitivity = "balanced"
        return {
            "agent_name": str(parsed.get("agent_name", "")).strip(),
            "description": str(parsed.get("description", "")).strip(),
            "persona_gender": persona_gender,
            "stt_language": tts_language,
            "tts_language": tts_language,
            "initial_message": str(parsed.get("initial_message", "")).strip(),
            "call_end_text": str(parsed.get("call_end_text", "")).strip(),
            "system_prompt": str(parsed.get("system_prompt", "")).strip(),
            "interruption_sensitivity": interruption_sensitivity,
        }
    except (json.JSONDecodeError, AttributeError):
        # Model didn't follow the JSON format — fall back to treating the whole response as
        # the system prompt rather than losing the generation entirely; agent_name/
        # initial_message just stay blank for the user to fill in themselves.
        return {
            "agent_name": "", "description": "", "persona_gender": "", "stt_language": "",
            "tts_language": "", "initial_message": "", "call_end_text": "",
            "system_prompt": raw.strip(), "interruption_sensitivity": "",
        }


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

- "agent_name": a common Hindi/Indian first name for the bot's persona (e.g. "Priya", "Rahul", "Meera",
  "Aman"). Never use an English/Western name (e.g. "Alex", "Sarah", "John"), even if the conversation
  itself will be in English.
- "description": one short sentence (no headers, no placeholders like "[Company Name]") summarizing
  what this bot does, consistent with "agent_name" — e.g. "Priya handles HR onboarding queries and
  routes escalations to a human." Never copy boilerplate structure from the user's description verbatim.
- "persona_gender": "male" or "female", matching the gender implied by "agent_name" (e.g. "Rahul"
  implies "male", "Priya" implies "female").
- "tts_language": the language the caller and bot will actually speak, as a code from this exact set:
  hi-IN (Hindi), en-IN (English/India), bn-IN (Bengali), gu-IN (Gujarati), kn-IN (Kannada), ml-IN
  (Malayalam), mr-IN (Marathi), od-IN (Odia), pa-IN (Punjabi), ta-IN (Tamil), te-IN (Telugu). Infer
  from the description (e.g. "for Tamil customers" -> ta-IN); default to hi-IN if nothing implies a
  language. The start node's "first_message" and any "closing_message" must be written in this
  language's script, not transliterated English.
- "interruption_sensitivity": "patient" if the description implies letting callers speak at length
  without cutting in (e.g. elderly callers, detailed complaints), "responsive" if it implies a fast,
  no-nonsense call (e.g. quick verification, sales qualification), otherwise "balanced".
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


def _sanitize_graph(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
    """Defends against two ways a model's JSON graph can be silently broken rather than
    obviously wrong — a bad graph that LOOKS fine in the JSON but corrupts state at call time:

    1. An edge referencing a node id that isn't in `nodes` (a typo, or the model renaming a
       node in one place but not the other). Left alone, this ships straight into the bot's
       saved config; workflow_engine.py's WorkflowGraph would then try to follow that edge to a
       node that doesn't exist. Dropped here instead — better an edge silently missing (visible
       immediately as a validation issue: unreachable node / dead-end) than a call that breaks
       days later on whatever branch happens to hit it.
    2. Two nodes sharing an id. React keys on them collide (canvas rendering glitches), and
       whatever indexes nodes by id at runtime keeps only one — so the bot's actual behavior
       silently diverges from what the builder displays. Keeps the first occurrence, drops the
       rest (and any of their now-dangling edges, via the same pass).
    """
    seen_ids: set[str] = set()
    deduped_nodes = []
    for n in nodes:
        nid = n.get("id")
        if nid in seen_ids:
            continue
        seen_ids.add(nid)
        deduped_nodes.append(n)
    valid_edges = [e for e in edges if e.get("source") in seen_ids and e.get("target") in seen_ids]
    return deduped_nodes, valid_edges


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
    nodes, edges = _sanitize_graph(nodes, edges)
    _autolayout(nodes, edges)

    persona_gender = str(parsed.get("persona_gender", "")).strip().lower()
    if persona_gender not in ("male", "female"):
        persona_gender = "female"
    tts_language = str(parsed.get("tts_language", "")).strip()
    if tts_language not in _VALID_SARVAM_LANGUAGES:
        tts_language = "hi-IN"
    interruption_sensitivity = str(parsed.get("interruption_sensitivity", "")).strip().lower()
    if interruption_sensitivity not in _VALID_INTERRUPTION_SENSITIVITIES:
        interruption_sensitivity = "balanced"

    return {
        "agent_name": str(parsed.get("agent_name", "")).strip(),
        "description": str(parsed.get("description", "")).strip(),
        "persona_gender": persona_gender,
        "stt_language": tts_language,
        "tts_language": tts_language,
        "interruption_sensitivity": interruption_sensitivity,
        "global_prompt": str(parsed.get("global_prompt", "")).strip(),
        "workflow": {"nodes": nodes, "edges": edges},
        "functions": parsed.get("functions") or [],
    }


_GENERATE_FUNCTION_INSTRUCTIONS = """\
You are helping a non-technical user configure an HTTP API call for a voice call center bot's
Custom Function builder, from a pasted API description, curl example, or docs snippet.

Produce ONLY a raw JSON object (no markdown fences, no commentary) with exactly these keys:

- "name": short snake_case identifier for the function (e.g. "get_order_status").
- "description": one sentence, what this call does and when it should run — shown to the LLM
  for during-call tools.
- "url": the endpoint URL. If genuinely not present anywhere in the input, use the literal
  string "TODO_SET_URL" — never invent one.
- "method": one of "GET", "POST", "PUT", "PATCH", "DELETE".
- "headers": object of header name -> value. Use a placeholder like "YOUR_API_KEY" for any
  secret/token the input references but doesn't give the real value for — never invent a
  plausible-looking real key.
- "query_params": object of query string key -> value (or "" if the value is dynamic/unknown).
- "body_mode": "json" or "form" — which the request body uses.
- "parameters": array of {"name","description","type": "string"|"number"|"boolean"|"object"|"array",
  "required": bool} — each dynamic input the call needs (path/query/body params, whatever the
  input implies the LLM or caller must supply).
- "store_variables": array of {"variable","json_path"} — sensible fields to extract from an
  example/typical JSON response into named variables (dot path, e.g. "data.status"). Empty
  array if the input gives no clue about the response shape.

Base every field only on what the input actually states or strongly implies — leave a field at
its empty/placeholder default rather than guessing when genuinely unclear.
"""


def generate_function_from_description(description: str) -> dict:
    """Functions tab AI-assist button: turns a pasted curl command / API description / docs
    snippet into a draft CustomFunction the user still reviews before saving — mirrors
    generate_agent_from_description's one-shot-JSON pattern. Any field the input doesn't
    support is left at a safe default (empty/placeholder) rather than guessed."""
    prompt = f"{_GENERATE_FUNCTION_INSTRUCTIONS}\n\nInput:\n{description}"
    client = _client()
    raw = client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text
    parsed = _extract_json_object(raw)  # let JSONDecodeError propagate — no half-built function to fall back to
    return {
        "name": str(parsed.get("name", "")).strip(),
        "description": str(parsed.get("description", "")).strip(),
        "url": str(parsed.get("url", "")).strip() or PLACEHOLDER_URL,
        "method": str(parsed.get("method", "POST")).strip().upper() or "POST",
        "headers": {str(k): str(v) for k, v in (parsed.get("headers") or {}).items()},
        "query_params": {str(k): str(v) for k, v in (parsed.get("query_params") or {}).items()},
        "body_mode": parsed.get("body_mode") if parsed.get("body_mode") in ("json", "form") else "json",
        "parameters": [
            {
                "name": str(p.get("name", "")).strip(),
                "description": str(p.get("description", "")).strip(),
                "type": p.get("type") if p.get("type") in ("string", "number", "boolean", "object", "array") else "string",
                "required": bool(p.get("required", False)),
            }
            for p in (parsed.get("parameters") or [])
            if str(p.get("name", "")).strip()
        ],
        "store_variables": [
            {"variable": str(sv.get("variable", "")).strip(), "json_path": str(sv.get("json_path", "")).strip()}
            for sv in (parsed.get("store_variables") or [])
            if str(sv.get("variable", "")).strip()
        ],
    }


_SUMMARIZE_VERSION_DIFF_INSTRUCTIONS = """\
You are summarizing a config diff between two versions of a voice call center bot, for a PM
who is about to publish and wants a quick sanity check of what actually changed and why it
likely matters — they are not a developer and won't read raw JSON.

You are given, per changed top-level config field, its old and new value as JSON. Write a
PLAIN-ENGLISH summary: what changed, field by field, in the fewest words that stay clear (short
phrases/bullets are fine, e.g. "System prompt: now asks for a callback number before ending the
call."). Skip fields whose change is purely cosmetic/formatting with no behavioral effect. If
literally nothing meaningfully changed, say so in one line. No markdown headers, no preamble —
just the summary itself, plain text (bullet lines using "- " are fine).
"""


def summarize_version_diff(diffs: dict) -> str:
    """VersionDiffModal's AI summary line: given the {field: {old, new}} diff already computed
    client-side (same top-level-key comparison the modal itself does), produce a one-glance
    plain-English summary for a PM reviewing before publish — no separate diff computation here,
    just narration of a diff that already exists."""
    prompt = f"{_SUMMARIZE_VERSION_DIFF_INSTRUCTIONS}\n\nDiff:\n{json.dumps(diffs, indent=2)}"
    client = _client()
    return client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text.strip()


_TRIAGE_TEST_CALL_INSTRUCTIONS = """\
You are helping a non-technical user debug a bad test call with their voice call center bot in
a no-code builder. You are given the bot's current instructions (system prompt, or workflow
graph summary), the test call's transcript, and how the call ended.

Diagnose what went wrong and tell them exactly how to fix it. Respond in plain English, no
markdown headers, structured as:
1. What went wrong — one or two sentences, the concrete moment/behavior that was the problem
   (e.g. "The bot never asked for a callback number even though the caller offered one").
2. Likely cause — is it the prompt wording, a missing workflow node/transition, a missing
   fallback branch, a function/webhook issue, or something outside the bot's control (caller
   hung up, audio issue)?
3. Suggested fix — a specific, concrete edit: what to change in the system prompt / global
   prompt / which node to add or edit, worded so they could paste it straight into the
   "Refine with AI" box or type it into the prompt field themselves.

If the transcript shows nothing actually wrong (e.g. the caller just hung up, or the call
completed as intended), say so plainly rather than inventing a problem.

CRITICAL — if the transcript is empty (no turns were captured), you have NO evidence of what
happened on the call. Do not invent a specific technical cause (phone number config, SIP trunk,
webhook, telephony provider, or any other guessed root cause) — you cannot see any of that from
a transcript alone. Instead say plainly that no conversation was captured, so this can't be
diagnosed as a prompt/graph issue from the transcript, and suggest simply retrying the test call
and checking the connection/mic before diagnosing further.
"""


def triage_test_call(transcript: list, status: str, error: str, close_note: str, instructions: str) -> str:
    """Test panel's "What went wrong?" button: feeds the failed test call's transcript plus the
    bot's current prompt/graph instructions to Gemini and gets back a plain-English diagnosis +
    concrete fix, so a non-technical user doesn't have to reverse-engineer the transcript
    themselves. `instructions` is whatever text best represents the bot's current behavior
    (system_prompt for conversational bots, a workflow summary for workflow bots) — the caller
    decides which, this function is agnostic to bot_type.

    Short-circuits before calling the model when there's truly nothing to go on (empty
    transcript, no error, no close note) — with zero signal the model has nothing to diagnose
    but tends to invent a specific, confident-sounding technical cause anyway (observed:
    fabricated SIP-trunk/phone-number-config narratives), which is worse than admitting there's
    no evidence."""
    if not transcript and not error.strip() and not close_note.strip():
        return (
            "No conversation was captured for this call, and no error or close note came "
            "through either — there isn't enough information here to diagnose a prompt or "
            "workflow problem. This usually means the call never actually connected (mic/"
            "network/room setup) rather than something wrong with the bot's instructions. "
            "Try the test call again and check the connection before assuming it's the "
            "prompt or graph."
        )
    transcript_text = "\n".join(
        f"{turn.get('role', '?')}: {turn.get('text', '')}" for turn in (transcript or []) if turn.get("text")
    ) or "(empty — no turns were captured)"
    prompt = (
        f"{_TRIAGE_TEST_CALL_INSTRUCTIONS}\n\n"
        f"Bot's current instructions:\n{instructions or '(none set)'}\n\n"
        f"Call ended with status: {status or 'unknown'}\n"
        f"Client-side error (if any): {error or 'none'}\n"
        f"Session close note (if any): {close_note or 'none'}\n\n"
        f"Transcript:\n{transcript_text}"
    )
    client = _client()
    return client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text.strip()


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
    nodes, edges = _sanitize_graph(nodes, edges)
    _autolayout(nodes, edges)

    return {
        "global_prompt": str(parsed.get("global_prompt", current_global_prompt)).strip(),
        "workflow": {"nodes": nodes, "edges": edges},
        "functions": parsed.get("functions") or [],
    }


_GENERATE_EVAL_SCENARIOS_INSTRUCTIONS = """\
You are designing pre-publish regression test scenarios for a voice call center bot, given its
system prompt below. Each scenario scripts a simulated CALLER persona that will be played by
another LLM against this bot's real prompt — no live call, just an LLM-vs-LLM text simulation —
then auto-scored pass/fail against phrase checks you also define.

Read the system prompt and infer: what is this bot actually for, what are the realistic caller
intents/personas someone would bring to it, and what would a broken response look like for this
specific bot (not a generic bot). Favor scenarios that exercise the SPECIFIC things this prompt
promises — its stated flow, constraints, escalation rules, required questions — over generic
"happy path" / "angry caller" filler that would apply to any bot.

For each scenario produce:
- "name" — a short label (e.g. "Wants human escalation mid-call").
- "caller_persona" — one paragraph of instructions for the LLM playing the caller: their
  situation, what they say/ask, how they react. Written in second person ("You are..."),
  matching the language/tone the system prompt implies the bot's callers would use.
- "max_turns" — integer 2-6, however many turns this scenario realistically needs to play out.
- "must_contain" — phrases/substrings (lowercase-insensitive match) the bot's replies across the
  conversation SHOULD include if it's behaving correctly per its own prompt (e.g. a required
  disclosure, a specific question it must ask). Empty list if nothing specific applies — do not
  invent a requirement the prompt doesn't actually state.
- "must_not_contain" — phrases that would indicate a broken response (leaked placeholders like
  "{agent_name}", the literal word "error"/"undefined", or something the prompt explicitly says
  never to do/say). Always include "error" and "undefined" plus anything specific to this prompt.

Respond with ONLY a raw JSON object: {"scenarios": [...]}, no markdown fences, no commentary.
Produce exactly the requested number of scenarios.
"""


def generate_eval_scenarios(system_prompt: str, count: int = 3) -> list[dict]:
    """Pre-publish evals panel's "Generate with AI" button: derives scenario personas + pass/fail
    checks tailored to this bot's actual system_prompt, rather than the two generic built-in
    defaults (backend/evals.py's DEFAULT_SCENARIOS) — same one-shot-JSON pattern as
    generate_function_from_description. The user reviews/edits the result before running it,
    same as every other AI-assist button in this module."""
    prompt = (
        f"{_GENERATE_EVAL_SCENARIOS_INSTRUCTIONS}\n\n"
        f"Number of scenarios to produce: {count}\n\n"
        f"Bot's system prompt:\n{system_prompt}"
    )
    client = _client()
    raw = client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text
    parsed = _extract_json_object(raw)  # let JSONDecodeError propagate — no half-built scenario list to fall back to
    scenarios = parsed.get("scenarios") or []
    return [
        {
            "name": str(s.get("name", "")).strip(),
            "caller_persona": str(s.get("caller_persona", "")).strip(),
            "max_turns": max(1, min(20, int(s.get("max_turns", 4) or 4))),
            "must_contain": [str(p).strip() for p in (s.get("must_contain") or []) if str(p).strip()],
            "must_not_contain": [str(p).strip() for p in (s.get("must_not_contain") or []) if str(p).strip()],
        }
        for s in scenarios
        if str(s.get("name", "")).strip() and str(s.get("caller_persona", "")).strip()
    ]
