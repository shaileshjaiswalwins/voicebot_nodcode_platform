"""Workflow-bot execution engine — runs a visual ReactFlow graph as a live call.

Consumed by bot_dev.py's entrypoint: when the resolved bot_config's
``bot_type == "workflow"`` (see backend/routers/bot_config.py, the unified
resolver `bot.py`'s `fetch_bot_config` now calls), bot_dev.py hands off to
`run_workflow_call()` here instead of running its normal fixed-assistant flow.

--- How this maps onto the graph schema (backend/schemas.py: Workflow,
    WorkflowNodeData; JD-Dashboard/src/types/workflow.ts is the ground truth
    for what the frontend actually persists) ---

- Exactly one ``start`` node. Its ``first_message`` seeds the very first
  conversation node's opening line; the node itself has no dialogue of its
  own, so it's treated like a transparent pass-through (same as function/
  condition nodes below).
- ``conversation`` nodes are the only nodes with a real LLM turn. Each one
  becomes its own LiveKit ``Agent`` instance. Its outgoing branches
  (``data.transitions: [{id, key, label, condition}]``) each become a
  dynamically-built ``@function_tool`` — calling one triggers a LiveKit
  "agent handoff": the tool returns a new ``Agent`` instance, and the
  framework (confirmed by reading
  livekit/agents/voice/generation.py::make_tool_output +
  livekit/agents/voice/agent_activity.py + AgentSession.update_agent) swaps
  the live session onto it, invoking the old agent's ``on_exit`` and the new
  one's ``on_enter`` automatically. This is exactly the (until now
  commented-out, never-activated) pattern sketched in bot_new.py's
  "Handoff stub" — this file is that pattern made real and generic.
- An outgoing edge's ``sourceHandle`` equals the transition's (or condition
  branch's) ``id`` — confirmed directly against
  JD-Dashboard/src/components/workflow-bots/builder/workflow-canvas.tsx
  (`onConnect`) and .../nodes/condition-node.tsx (`<Handle id={branch.id}>`).
  So resolving "where does branch X lead" means matching
  `edge.source == node.id and edge.sourceHandle == branch.id`, NOT reading
  `edge.data.kind/condition/key` — those legacy WorkflowEdgeData fields are
  never populated by the current frontend (confirmed: `onConnect` only ever
  sets `data: {label}`).
- ``condition`` nodes have no LLM turn: `data.conditions: [{id, path, op,
  value, is_fallback}]` are evaluated deterministically against collected
  variables, then execution silently continues to whichever node the
  matching branch's handle points to (recursing through `resolve()` below,
  same as a conversation node's transition — just without a spoken turn).
- ``function`` nodes have no LLM turn either: `data.function` (webhook
  config) is executed synchronously, the response is stored at
  `data.output_key` in the shared variables dict, then execution
  auto-continues to the single outgoing edge (function nodes have exactly
  one, unnamed, output handle — confirmed against
  .../nodes/function-node.tsx).
- ``end_call`` nodes become a terminal Agent that speaks `closing_message`
  then ends the call.
- ``global`` nodes are NOT part of the graph traversal at all (confirmed: no
  incoming edges from the main flow, per .../nodes/global-node.tsx comment
  "no incoming edges from the main flow — always reachable via tool").
  Instead, one always-available `@function_tool` is added to EVERY
  conversation-node Agent, named/described from the global node's
  `trigger_description`, so the LLM can call it from any step. Its `action`
  determines what happens:
    - "end_call": speak the global node's own `prompt`, then end the call.
    - "continue": speak `prompt` (if any) and return — no agent switch, the
      current conversation just carries on. (The `resume` flag is therefore
      a no-op for "continue" in this implementation, since "continue" never
      diverts away from the current agent to begin with; there's nothing to
      resume *to*.)
    - "transfer": **NOT a real SIP/telephony transfer.** `transfer_number`
      is accepted by the schema but wiring an actual call transfer is a
      separate, larger feature (LiveKit SIP transfer API) that wasn't part
      of this pass. This action logs a warning, speaks an apology, and ends
      the call gracefully rather than silently pretending to transfer.

Reused from bot_new.py's (never-activated) design rather than reinvented:
the "state travels via a plain object passed into every Agent's constructor,
and a handoff-created Agent reads it back in its own __init__/on_enter"
pattern from its commented SpecialistAgent stub.

Two corrections versus that stub, both confirmed by reading the installed
livekit-agents source rather than assumed from the (never-executed) example:
  1. STT/LLM/TTS are configured once on the AgentSession, not per-Agent.
     `Agent.stt/llm/tts` are typed `NotGivenOr[...]` specifically so an Agent
     that doesn't set its own falls back to the session's — every Agent class
     below intentionally omits them.
  2. The stub's `chat_ctx=ctx.session.chat_ctx` doesn't exist on this version
     (verified: no such property; the real one is `session.history`). More
     importantly, generation reads from `self._agent._chat_ctx` (the
     *Agent instance's own* copy, seeded at construction time from whatever
     `chat_ctx=` was passed in — see livekit/agents/voice/agent_activity.py
     lines ~1215/1467), not `session._chat_ctx` directly. So every handoff
     below explicitly passes `chat_ctx=ctx.session.history` into the next
     Agent's constructor — omitting it would silently reset the LLM's view
     of the conversation to empty on every transition.

Not modeled (kept intentionally out of scope for this pass, unlike the
Justdial-specific bots): muted-window noise filtering, per-call Mongo
transcript schema, keyboard/ambience background audio, abusive-language
detection. A generic engine has no per-bot business logic to hang those on.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import aiohttp
from loguru import logger

from livekit.agents import Agent, AgentSession, RunContext, StopResponse, function_tool
from livekit.api import DeleteRoomRequest, LiveKitAPI
from livekit.plugins import google, sarvam

from bot import _get_http_session, build_transcript_from_session, _save_transcript_to_dashboard_db
from call_metrics import CallMetricsCollector
from livekit_indic5_tts import IndicF5TTS
from interruption_presets import resolve_interruption_preset

INDIC_TTS_WS_URL = os.getenv("INDIC_TTS_WS_URL", "ws://10.10.0.14:8404/ws")

# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _slugify(text: str, fallback: str = "step") -> str:
    s = _SLUG_RE.sub("_", (text or "").strip().lower()).strip("_")
    return s or fallback


_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _resolve_path(variables: dict, path: str) -> Any:
    cur: Any = variables
    for part in (path or "").split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _interpolate(template: str, variables: dict) -> str:
    """Replace {{name}} / {{a.b}} placeholders with collected variable values."""
    if not template:
        return template

    def _sub(m: re.Match) -> str:
        val = _resolve_path(variables, m.group(1))
        return "" if val is None else str(val)

    return _VAR_RE.sub(_sub, template)


def _to_num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


_RULE_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: str(a) == str(b),
    "ne": lambda a, b: str(a) != str(b),
    "gt": lambda a, b: _to_num(a) > _to_num(b),
    "lt": lambda a, b: _to_num(a) < _to_num(b),
    "contains": lambda a, b: str(b) in str(a),
    "exists": lambda a, b: a is not None and a != "",
}

# Verbatim copy of bot_dev.py's _TRANSLITERATION_HINT (minus its leading
# "\n\n" — WorkflowGraph.compile_instructions() joins parts with "\n\n"
# itself). Appended only when the workflow's TTS is our own IndicF5 (see
# WorkflowGraph.__init__'s tts_provider param) — Sarvam pronounces Latin/
# Hinglish natively and doesn't need this.
_TRANSLITERATION_HINT = (
    "SCRIPT OVERRIDE FOR THIS TTS (mandatory — takes priority over any earlier "
    "instruction to keep English/API-provided words as-is in Latin script): This "
    "bot's TTS voice cannot pronounce Latin letters at all. Every English word — "
    "including product/category/option names from the API (catname, question.text, "
    "option.text) and ordinary Hinglish words — must be transliterated phonetically "
    "into Devanagari before you speak it. Transliterate the SOUND, don't translate "
    "the meaning, and never leave Latin letters in your reply.\n"
    "  Examples: chocolate → चॉकलेट, cookie → कुकी, cream biscuit → क्रीम बिस्किट, "
    "requirement → रिक्वायरमेंट, dealer → डीलर, sellers → सेलर्स, connect → कनेक्ट, "
    "details → डिटेल्स.\n"
    "These are examples, not a fixed list — apply the same transliteration to every "
    "other English word you encounter, including ones not shown here."
)


# ---------------------------------------------------------------------------
# Per-call state — passed explicitly into every Agent's constructor (simpler
# and more explicit than relying on AgentSession.userdata timing, though we
# also set session.userdata = state for parity with the handoff pattern
# bot_new.py's stub sketches).
# ---------------------------------------------------------------------------

@dataclass
class WorkflowState:
    variables: dict = field(default_factory=dict)
    room_name: str = ""
    organization_id: str = ""
    workflow_bot_id: str = ""
    lead_record: dict = field(default_factory=dict)
    ended_naturally: bool = False
    # Set by run_workflow_call() once the session/room are known.
    end_call: Optional[Callable[[], Awaitable[None]]] = None


# ---------------------------------------------------------------------------
# Graph compiler — resolves node ids/handles to a runtime Agent, transparently
# walking through function/condition nodes (which have no LLM turn of their
# own) until it lands on a conversation or end_call node.
# ---------------------------------------------------------------------------

class WorkflowGraph:
    _MAX_HOPS = 25  # guard against a malformed graph looping forever

    def __init__(self, workflow: dict, global_prompt: str = "", tts_provider: str = "justdial"):
        nodes = workflow.get("nodes") or []
        self.nodes_by_id: dict[str, dict] = {n["id"]: n for n in nodes}
        self.edges: list[dict] = workflow.get("edges") or []
        self.global_prompt = global_prompt or ""
        # "justdial" (our own IndicF5) can only pronounce Devanagari — see the
        # transliteration hint appended in compile_instructions() below.
        # "sarvam" (bulbul:v3) is a hosted TTS that pronounces Latin/Hinglish
        # natively, so it skips the hint (mirrors bot_dev.py's identical
        # tts_provider != "sarvam" gate).
        self.tts_provider = tts_provider or "justdial"
        self.global_nodes = [n for n in nodes if (n.get("data") or {}).get("kind") == "global"]
        starts = [n for n in nodes if (n.get("data") or {}).get("kind") == "start"]
        self.start_node: dict | None = starts[0] if starts else None

    def _target_of(self, source_id: str, handle_id: str | None) -> dict | None:
        outs = [e for e in self.edges if e.get("source") == source_id]
        if handle_id is not None:
            for e in outs:
                if e.get("sourceHandle") == handle_id:
                    return self.nodes_by_id.get(e.get("target"))
            # Defensive fallback: a single outgoing edge with no/blank
            # sourceHandle is unambiguous even if the handle id didn't match
            # (guards against minor graph-serialization drift).
            if len(outs) == 1:
                return self.nodes_by_id.get(outs[0].get("target"))
            return None
        # No handle constraint (start/function nodes have exactly one,
        # unnamed, outgoing edge).
        for e in outs:
            if not e.get("sourceHandle"):
                return self.nodes_by_id.get(e.get("target"))
        return self.nodes_by_id.get(outs[0].get("target")) if outs else None

    async def _run_function_node(self, node: dict, state: WorkflowState) -> None:
        data = node.get("data") or {}
        fn = data.get("function") or {}
        output_key = data.get("output_key") or node["id"]
        url = _interpolate(fn.get("url", ""), state.variables)
        if not url:
            logger.warning(f"[Workflow] function node {node['id']} has no URL — skipping")
            state.variables[output_key] = {"error": "no url configured"}
            return
        method = (fn.get("method") or "GET").upper()
        headers = {k: _interpolate(v, state.variables) for k, v in (fn.get("headers") or {}).items()}
        query_params = {k: _interpolate(v, state.variables) for k, v in (fn.get("query_params") or {}).items()}
        body = None
        if fn.get("body_format") == "json" and fn.get("custom_body"):
            try:
                body = json.loads(_interpolate(fn["custom_body"], state.variables))
            except (json.JSONDecodeError, TypeError):
                body = None
        try:
            session_http = _get_http_session()
            async with session_http.request(
                method, url, params=query_params, headers=headers, json=body,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                try:
                    data_out = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, json.JSONDecodeError, ValueError):
                    data_out = {"status_code": resp.status, "text": await resp.text()}
            state.variables[output_key] = data_out
        except Exception as e:
            logger.warning(f"[Workflow] function node {node['id']} request failed: {e}")
            state.variables[output_key] = {"error": str(e)}

    def _eval_condition(self, node: dict, state: WorkflowState) -> dict | None:
        branches = (node.get("data") or {}).get("conditions") or []
        fallback = None
        for b in branches:
            if b.get("is_fallback"):
                fallback = b
                continue
            op = _RULE_OPS.get(b.get("op", "eq"), _RULE_OPS["eq"])
            val = _resolve_path(state.variables, b.get("path", ""))
            try:
                if op(val, b.get("value")):
                    return b
            except Exception:
                continue
        return fallback

    def compile_instructions(self, node: dict, state: WorkflowState) -> str:
        data = node.get("data") or {}
        parts: list[str] = []
        if self.global_prompt:
            parts.append(self.global_prompt)
        prompt = _interpolate(data.get("prompt", ""), state.variables)
        if prompt:
            parts.append(prompt)
        # IndicF5 (our own TTS) can only pronounce Devanagari — Sarvam handles
        # Latin/Hinglish natively. Same gate bot_dev.py uses for regular
        # assistants (see module docstring for _TRANSLITERATION_HINT origin).
        if self.tts_provider != "sarvam":
            parts.append(_TRANSLITERATION_HINT)
        variables = data.get("variables") or []
        if variables:
            lines = "\n".join(
                f"- {v.get('name')} ({v.get('type', 'string')}"
                f"{', required' if v.get('required') else ''}): {v.get('description', '')}"
                for v in variables
            )
            parts.append(
                "Collect the following information from the caller during this step, and "
                "call the set_variable tool for each one as soon as you have it:\n" + lines
            )
        trig_lines = "\n".join(
            f"- {(g.get('data') or {}).get('trigger_description', '')}"
            for g in self.global_nodes
            if (g.get("data") or {}).get("trigger_description")
        )
        if trig_lines:
            parts.append(
                "At any point in this step, if one of these situations comes up, call the "
                "matching tool immediately:\n" + trig_lines
            )
        return "\n\n".join(p for p in parts if p)

    async def resolve(
        self,
        source_id: str,
        handle_id: str | None,
        state: WorkflowState,
        *,
        chat_ctx=None,
        opening_line: str | None = None,
        _hops: int = 0,
    ) -> Agent:
        """Walk from (source_id, handle_id) to the next real (conversation/
        end_call) node, executing any function/condition nodes in between.

        chat_ctx: the calling agent's current `ctx.session.history` (None for
        the very first call, before any session exists) — threaded through so
        the new Agent's own generation history isn't silently reset to empty
        (see module docstring point 2). STT/LLM/TTS are deliberately NOT
        threaded here — every Agent below omits them so they fall back to the
        session-level ones (see module docstring point 1).
        """
        if _hops > self._MAX_HOPS:
            logger.error(f"[Workflow] graph resolution exceeded {self._MAX_HOPS} hops — likely a cycle")
            return LostAgent(state=state, chat_ctx=chat_ctx)

        node = self._target_of(source_id, handle_id)
        if node is None:
            logger.error(f"[Workflow] no edge from node={source_id!r} handle={handle_id!r} — dead end")
            return LostAgent(state=state, chat_ctx=chat_ctx)

        kind = (node.get("data") or {}).get("kind")
        if kind == "function":
            await self._run_function_node(node, state)
            # Forward opening_line — a function node sitting between Start and the
            # first conversation node was silently dropping the greeting before,
            # since this recursive call never passed it through.
            return await self.resolve(
                node["id"], None, state, chat_ctx=chat_ctx, opening_line=opening_line, _hops=_hops + 1
            )
        if kind == "condition":
            branch = self._eval_condition(node, state)
            if branch is None:
                logger.warning(f"[Workflow] condition node {node['id']} matched no branch and has no fallback")
                return LostAgent(state=state, chat_ctx=chat_ctx)
            return await self.resolve(
                node["id"], branch.get("id"), state, chat_ctx=chat_ctx, opening_line=opening_line, _hops=_hops + 1
            )
        if kind == "conversation":
            # Interpolated HERE rather than by the caller — a function node between Start
            # and this conversation node (e.g. a vendor/lead lookup feeding the Start node's
            # own first_message) only populates state.variables during the walk through
            # resolve() above; interpolating any earlier would silently render those tokens
            # empty since the lookup hadn't run yet.
            resolved_opening = _interpolate(opening_line, state.variables) if opening_line else None
            return ConversationAgent(node=node, graph=self, state=state, opening_line=resolved_opening, chat_ctx=chat_ctx)
        if kind == "end_call":
            return EndCallAgent(node=node, state=state, chat_ctx=chat_ctx)

        logger.error(f"[Workflow] reached unexpected node kind {kind!r} (id={node.get('id')})")
        return LostAgent(state=state, chat_ctx=chat_ctx)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class _BaseWorkflowAgent(Agent):
    """Shared guard: never generate once the call is already ending."""

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        # Diagnostic: this file previously had no per-turn logging at all (unlike bot.py's
        # extensive instrumentation), which made a "bot only speaks its first line" report
        # impossible to diagnose without a live reproduction — this confirms whether STT/turn
        # detection ever hands the user's speech to the agent in the first place.
        logger.info(f"[Workflow] user turn completed on node={getattr(self, '_node', {}).get('id', '?')!r}: {getattr(new_message, 'text_content', None) or new_message!r}")
        state = getattr(self, "_state", None)
        if state is not None and state.ended_naturally:
            raise StopResponse()


def _build_set_variable_tool(state: WorkflowState):
    async def set_variable(ctx: RunContext, name: str, value: str) -> str:
        """Record a piece of information the caller has told you (e.g. their name, a
        chosen product, or the answer to a yes/no question), keyed by a short
        variable name, so it can be reused later in the conversation."""
        state.variables[name] = value
        return f"Recorded {name} = {value!r}."

    return function_tool(
        set_variable,
        name="set_variable",
        description=(
            "Record a piece of information the caller has told you, keyed by a short "
            "variable name (e.g. name='buyer_name', value='Rahul'), so it can be reused "
            "later in the conversation and in any {{variable}} placeholders."
        ),
    )


class ConversationAgent(_BaseWorkflowAgent):
    """One `conversation` node — the only node kind with a real LLM turn."""

    def __init__(self, *, node: dict, graph: WorkflowGraph, state: WorkflowState, opening_line: str | None, chat_ctx=None, **kwargs):
        self._node = node
        self._graph = graph
        self._state = state
        self._opening_line = opening_line

        instructions = graph.compile_instructions(node, state)
        tools = [_build_set_variable_tool(state)]
        for transition in (node.get("data") or {}).get("transitions", []) or []:
            tools.append(self._build_transition_tool(transition))
        for global_node in graph.global_nodes:
            tools.append(self._build_global_tool(global_node))

        super().__init__(
            instructions=instructions or "Continue the conversation naturally.",
            tools=tools, chat_ctx=chat_ctx, **kwargs,
        )

    def _build_transition_tool(self, transition: dict):
        node, graph, state = self._node, self._graph, self._state
        transition_id = transition.get("id")

        async def go(ctx: RunContext) -> Agent:
            logger.info(f"[Workflow] transition tool fired: node={node['id']!r} transition={transition_id!r} key={transition.get('key')!r}")
            next_agent = await graph.resolve(node["id"], transition_id, state, chat_ctx=ctx.session.history)
            logger.info(f"[Workflow] resolved to next agent: {type(next_agent).__name__} node={getattr(next_agent, '_node', {}).get('id', '<terminal>')!r}")
            return next_agent

        return function_tool(
            go,
            name=_slugify(transition.get("key") or transition.get("label") or transition_id, "next"),
            description=transition.get("condition") or transition.get("label") or "Move to the next step.",
        )

    def _build_global_tool(self, global_node: dict):
        state = self._state
        data = global_node.get("data") or {}
        action = data.get("action", "continue")
        prompt_line_template = data.get("prompt", "")
        transfer_number = data.get("transfer_number", "")
        global_id = global_node.get("id", "")

        async def trigger(ctx: RunContext):
            # NOTE: like EndCallAgent, ctx.session.say() speaks this directly,
            # bypassing the LLM — so the transliteration hint can't reach it.
            # Interpolated at least, so {{var}} tokens aren't spoken literally.
            prompt_line = _interpolate(prompt_line_template, state.variables)
            if prompt_line:
                await ctx.session.say(prompt_line, allow_interruptions=False)

            if action == "end_call":
                if state.end_call:
                    await state.end_call()
                return "Call ending."

            if action == "transfer":
                # NOT a real SIP/telephony transfer — see module docstring.
                logger.warning(
                    f"[Workflow] global node {global_id!r} requested transfer to "
                    f"{transfer_number!r} — real call transfer isn't implemented; ending call."
                )
                await ctx.session.say(
                    "माफ़ कीजिए, अभी मैं आपको transfer नहीं कर पा रही — मैं call यहीं समाप्त कर रही हूँ।",
                    allow_interruptions=False,
                )
                if state.end_call:
                    await state.end_call()
                return "Transfer unavailable; call ended."

            # action == "continue": no agent switch — the current conversation just
            # carries on (the `resume` flag is a no-op here; see module docstring).
            return "Acknowledged."

        return function_tool(
            trigger,
            name=_slugify(data.get("label") or global_id, "global_event"),
            description=data.get("trigger_description") or "Handle a special situation that can occur at any point in the call.",
        )

    async def on_enter(self) -> None:
        logger.info(f"[Workflow] entered conversation node={self._node['id']!r} opening_line={bool(self._opening_line)}")
        if self._opening_line:
            # "exactly this greeting" alone fights the transliteration hint already
            # present in `instructions` (compile_instructions, set at construction) —
            # an LLM reading "exactly" tends to recite Latin letters verbatim instead
            # of rendering them phonetically. Spell out that script rules still apply.
            self.session.generate_reply(
                instructions=(
                    f"Begin the call with this greeting — keep the words the same, but follow "
                    f'the script/pronunciation rules above (e.g. transliterate if needed): "{self._opening_line}". '
                    "Then continue naturally with your role below."
                )
            )
        else:
            self.session.generate_reply()


class EndCallAgent(_BaseWorkflowAgent):
    """Terminal node — speaks a closing line, then ends the call.

    NOTE — known transliteration gap: unlike ConversationAgent (whose reply is
    LLM-generated and thus follows compile_instructions()'s transliteration
    hint) and the Pipecat/WebRTC end-call path (pipecat_workflow_engine.py's
    _speak_and_end(), which also goes through the LLM), this agent's on_enter()
    speaks `closing_message` directly via session.say() — bypassing the LLM
    entirely. A closing_message authored in Latin-script Hinglish (the norm in
    this codebase's seed data) will NOT be transliterated on this LiveKit/SIP
    path, even though it now is on the WebRTC path. Fixing this properly means
    routing end-call speech through session.generate_reply() for non-Sarvam
    TTS, which changes the say-then-hangup sequencing (generate_reply() isn't
    awaited elsewhere in this file — on_enter() would need an explicit
    "speech finished" callback before calling state.end_call(), instead of
    today's simple await-then-hangup). Left as a follow-up rather than risking
    the call-termination sequencing in this pass; interpolation is fixed below.
    """

    _DEFAULT_CLOSING = "धन्यवाद, आपका दिन शुभ हो। अलविदा।"

    def __init__(self, *, node: dict, state: WorkflowState, **kwargs):
        self._state = state
        closing_template = (node.get("data") or {}).get("closing_message") or self._DEFAULT_CLOSING
        self._closing = _interpolate(closing_template, state.variables)
        super().__init__(instructions=f'The call is ending. Say goodbye warmly: "{self._closing}"', **kwargs)

    async def on_enter(self) -> None:
        await self.session.say(self._closing, allow_interruptions=False)
        if self._state.end_call:
            await self._state.end_call()


class LostAgent(_BaseWorkflowAgent):
    """Fallback when the graph is malformed / a dead end is hit at runtime.

    Ends the call gracefully instead of leaving the caller in silence.
    """

    def __init__(self, *, state: WorkflowState, **kwargs):
        self._state = state
        super().__init__(instructions="Apologize briefly that something went wrong and end the call.", **kwargs)

    async def on_enter(self) -> None:
        await self.session.say(
            "माफ़ कीजिए, कुछ technical दिक्कत आ गई। मैं call समाप्त कर रही हूँ।",
            allow_interruptions=False,
        )
        if self._state.end_call:
            await self._state.end_call()


# ---------------------------------------------------------------------------
# Entrypoint — called from bot_dev.py's entrypoint() when bot_config["bot_type"] == "workflow"
# ---------------------------------------------------------------------------

async def run_workflow_call(
    ctx,
    bot_config: dict,
    lead_record: dict | None,
    *,
    room_name: str,
    bot_id: str = "",
) -> None:
    """Compile bot_config["workflow"] and run it as a live LiveKit call.

    `ctx` must already be connected (ctx.connect() / wait_for_participant()
    called by bot_dev.py before handing off here — this function only builds
    the session/agent and starts it).
    """
    _call_start = time.time()
    workflow = bot_config.get("workflow") or {"nodes": [], "edges": []}
    global_prompt = bot_config.get("global_prompt", "")
    temperature = float(bot_config.get("temperature") or 0.7)
    max_call_duration = int(bot_config.get("max_call_duration") or 300)
    post_speech_hold_ms = int(bot_config.get("post_speech_hold_ms") or 300)

    # TTS provider/voice — "justdial" (our own IndicF5) or "sarvam" (bulbul:v3),
    # resolved server-side from voice_id via backend/voice_catalog.py (see
    # backend/routers/workflow_bots.py's get_workflow_bot_config). Same
    # selection bot_dev.py makes for regular assistants.
    tts_provider = bot_config.get("tts_provider") or "justdial"
    tts_voice = bot_config.get("tts_voice") or "simran"
    # How easily a caller can interrupt this bot — see interruption_presets.py.
    interruption_preset = resolve_interruption_preset(bot_config.get("interruption_sensitivity"))

    graph = WorkflowGraph(workflow, global_prompt=global_prompt, tts_provider=tts_provider)
    if graph.start_node is None:
        logger.error(f"[Workflow] workflow_bot_id={bot_config.get('workflow_bot_id')!r} has no start node — aborting")
        return

    state = WorkflowState(
        room_name=room_name,
        organization_id=bot_config.get("organization_id", ""),
        workflow_bot_id=bot_config.get("workflow_bot_id", ""),
        lead_record=lead_record or {},
    )
    # Seed a few obvious variables from the lead record so {{buyer_name}} etc.
    # work out of the box without every workflow author wiring a function node
    # just to fetch what bot_dev.py's assistant flow already fetches for free.
    if lead_record:
        buyer = lead_record.get("buyer_details") or {}
        search = lead_record.get("search_context") or {}
        state.variables.setdefault("buyer_name", buyer.get("buyer_name", ""))
        state.variables.setdefault("buyer_city", buyer.get("buyer_city", ""))
        state.variables.setdefault(
            "product", search.get("searched_keyword", "") or lead_record.get("catname", "")
        )

    gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    sarvam_api_key = os.getenv("SARVAM_API_KEY", "")

    stt = sarvam.STT(
        language="hi-IN", model="saaras:v3", mode="transcribe",
        api_key=sarvam_api_key or None, flush_signal=True,
    )
    llm_plugin = google.LLM(
        model="gemini-3.1-flash-lite", api_key=gemini_api_key or None, temperature=temperature,
    )
    if tts_provider == "sarvam":
        tts = sarvam.TTS(
            target_language_code="hi-IN", model="bulbul:v3", speaker=tts_voice,
            api_key=sarvam_api_key or None, speech_sample_rate=24000,
            output_audio_codec="linear16",
        )
    else:
        tts = IndicF5TTS(
            ws_url=INDIC_TTS_WS_URL, sample_rate=24000, nfe_step=16, speaker=tts_voice,
        )

    # Workflow bots don't go through pipeline_providers.py at all (STT/LLM are hardcoded
    # above, only TTS is selectable) — build the summary from what's actually instantiated
    # here, not from pipeline_providers.resolve_provider_summary's defaults.
    _provider_summary = {
        "stt_provider": "sarvam", "stt_model": "saaras:v3", "stt_language": "hi-IN",
        "tts_provider": tts_provider,
        "tts_model": "bulbul:v3" if tts_provider == "sarvam" else "indicf5",
        "tts_voice": tts_voice, "tts_language": "hi-IN",
        "llm_provider": "gemini", "llm_model": "gemini-3.1-flash-lite", "llm_temperature": temperature,
    }

    session: AgentSession = AgentSession(
        stt=stt, llm=llm_plugin, tts=tts,
        turn_handling={
            "turn_detection": "stt",
            "endpointing": {"min_delay": post_speech_hold_ms / 1000},
            # Previously just {"enabled": True} — no actual sensitivity tuning,
            # meaning workflow bots got only the SDK's implicit defaults with
            # no way to configure them per bot. Now driven by the same named
            # preset regular assistants use (see interruption_presets.py);
            # "balanced" reproduces those same implicit defaults exactly.
            "interruption": {
                "enabled": True,
                "min_duration": interruption_preset["livekit_min_duration"],
                "min_words": interruption_preset["livekit_min_words"],
                "backchannel_boundary": interruption_preset["backchannel_boundary"],
            },
        },
        userdata=state,
    )

    _metrics = CallMetricsCollector()
    session.on("metrics_collected", _metrics.on_metrics_collected)
    session.on("function_tools_executed", _metrics.on_function_tools_executed)

    @session.on("error")
    def _on_session_error(event) -> None:
        err = event.error
        logger.error(f"[Workflow] session error: {getattr(err, 'error', err)}")
        _metrics.on_session_error(err)

    _call_finished = asyncio.Event()

    async def _end_call() -> None:
        if state.ended_naturally:
            return
        state.ended_naturally = True
        try:
            await _finish_call()
        finally:
            _call_finished.set()

    async def _finish_call() -> None:
        # Workflow bots never persisted a transcript at all — the fixed-assistant
        # entrypoint's end-of-call save (bot_dev_param.py's _mongo_doc block) only runs
        # for bot_type != "workflow", since run_workflow_call takes over the whole call
        # and returns without going back through that code. build_transcript_from_session
        # is the same helper bot_dev.py/bot_pipeline.py use, so this lands in the same
        # tbl_ai_vb_call_transcripts collection the dashboard's Transcripts view reads.
        try:
            transcript = build_transcript_from_session(session)
            mongo_doc = {
                "call_id": room_name,
                "room_name": room_name,
                "status": "completed",
                "transcript": transcript,
                "call_start_time": _call_start,
                "call_end_time": time.time(),
                "call_duration_sec": round(time.time() - _call_start),
                "turn_count": len(transcript),
                "turn_metrics": _metrics.as_list(),
                "tool_calls": _metrics.tool_calls_as_list(),
                "session_errors": _metrics.errors_as_list(),
                "created_at": datetime.now(timezone.utc),
            }
            await _save_transcript_to_dashboard_db(mongo_doc, bot_id, "", bot_config=bot_config, provider_config=_provider_summary)
        except Exception as e:
            logger.error(f"[Workflow] transcript save failed: {e}")
        try:
            await session.aclose()
        except Exception as e:
            logger.warning(f"[Workflow] session.aclose() failed: {e}")
        try:
            await LiveKitAPI().room.delete_room(DeleteRoomRequest(room=room_name))
        except Exception as e:
            logger.warning(f"[Workflow] delete_room failed: {e}")

    state.end_call = _end_call

    # Passed through RAW (not interpolated here) — resolve() interpolates it itself at
    # the point a ConversationAgent is actually constructed, after any function/condition
    # nodes between Start and there (e.g. a vendor/lead lookup) have already run and
    # populated state.variables. Interpolating this early would silently render tokens
    # fed by such a lookup as empty — see resolve()'s "conversation" branch.
    _raw_first_message = (graph.start_node.get("data") or {}).get("first_message") or ""
    first_agent = await graph.resolve(
        graph.start_node["id"], None, state,
        opening_line=_raw_first_message or None,
    )

    if max_call_duration > 0:
        async def _timeout_call() -> None:
            await asyncio.sleep(max_call_duration)
            if not state.ended_naturally:
                logger.info(f"[Workflow] {max_call_duration}s max_call_duration reached — ending call")
                await _end_call()
        asyncio.create_task(_timeout_call())

    ctx.room.on(
        "participant_disconnected",
        lambda *_args: asyncio.create_task(_end_call()),
    )

    await session.start(room=ctx.room, agent=first_agent)

    # Block here until the call is actually over (participant disconnect, timeout, or an
    # explicit end-call action all funnel through _end_call). Previously this function
    # returned right after session.start(), so bot_dev_param.py's entrypoint returned too
    # — and the LiveKit job runtime tore the whole process down as soon as the room
    # disconnected, often faster than the fire-and-forget _end_call() task could finish
    # its Mongo write. That's why no workflow-bot call ever saved a transcript, even after
    # the save logic itself was added above.
    await _call_finished.wait()
