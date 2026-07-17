# Plan 02 — Custom Functions (before/during/after call) + Tabbed Settings

**JIRA:** TBD  ·  **Author:** shailesh.jaiswal  ·  **Date:** 2026-07-17

## Goal

Let a PM configure, **per bot (board)**, arbitrary HTTP "custom functions" that the
voicebot can call:

- **before** the conversation (pre-call: fetch lead / caller details),
- **during** the conversation (LLM tool calls: product change, lookups),
- **after** the conversation (post-call: analytics, persistence),

and reorganize the bot builder settings into **tabs** (Speed · STT · TTS · LLM ·
Functions · Advanced), retell.ai-style.

## Current state (what already exists — do not rebuild)

| Piece | Location | Status |
|---|---|---|
| `functions: list[dict]` on bot config, versioned + persisted | `backend/models.py:89` | ✅ exists (loose dict) |
| Generic in-call HTTP dispatcher | `bot.py:791 _execute_function_call` | ⚠️ special-cases FetchLead/FetchCategorySchema; timeout hardcoded 10s |
| Lifecycle HTTP caller | `bot.py:862 call_configured_function` | ⚠️ exists, imported in bot_pipeline/bot_dev, not config-driven |
| Tool registration | `bot_pipeline.py:1237` | ❌ hardcoded `tools = [FetchCategorySchema, FetchLead]` |
| Settings UI | `frontend/src/views/BuilderView.tsx` | ⚠️ flat form + `isAdvanced` toggle + raw Developer JSON; no tabs, no fn editor |
| `entrypoint` / `save_call_data` (lifecycle seams) | `bot.py:1603` / `bot.py:1751` (also `bot_dev.py`) | pre/post hook seams |

LiveKit confirmed capabilities: `function_tool(raw_schema=...)` builds a tool from a
JSON schema; `agent.update_tools()` swaps tools at runtime.

---

## Phase 1 — Data model (`backend/models.py`)

Add typed models; keep `functions: list[dict]` readable for back-compat by parsing loosely.

```python
class FunctionParam(BaseModel):
    name: str
    description: str = ""
    type: Literal["string","number","boolean","object","array"] = "string"
    required: bool = False

class StoreVariable(BaseModel):
    variable: str          # dynamic var name to store into call_state / prompt vars
    json_path: str         # dotted/JSONPath into response, e.g. data.lead_name

class CustomFunction(BaseModel):
    id: str
    name: str                                   # LLM tool name (during_call) — must be a valid identifier
    description: str = ""
    url: str = ""
    method: Literal["GET","POST","PUT","PATCH","DELETE"] = "POST"
    timeout_ms: int = 120000
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    body_mode: Literal["form","json"] = "form"
    parameters: list[FunctionParam] = Field(default_factory=list)
    raw_body_schema: dict[str, Any] = Field(default_factory=dict)   # JSON mode
    store_variables: list[StoreVariable] = Field(default_factory=list)
    trigger: Literal["pre_call","during_call","post_call"] = "during_call"
    enabled: bool = True
```

- `BotConfig.functions` becomes `list[CustomFunction]` (Pydantic parses existing dicts;
  legacy entries without `id`/`trigger` default to `during_call`).
- **Critical** (per the model's own docstring at `models.py:60`): confirm the new field
  survives `.model_dump()` in `backend/routers/bots.py` create/save_draft/update_version,
  and extend `test_saving_a_draft_persists_every_field_the_pipeline_actually_reads`.

## Phase 2 — Generic dispatcher (`bot.py`)

- Generalize `_execute_function_call`:
  - use per-function `timeout_ms` instead of hardcoded 10s;
  - honor `body_mode` (form → data/query, json → json body);
  - after a successful response, apply `store_variables` → write extracted values into
    `call_state["vars"]` (new namespace) for prompt interpolation + post-call use.
  - Keep FetchLead / FetchCategorySchema special-case branches intact (regression guard).
- Keep `call_configured_function` for pre/post; route it through the same timeout/body logic.

## Phase 3 — Dynamic tool registration (`bot_pipeline.py:1237`, mirror in `bot.py`/`bot_dev.py`)

- Build one tool per `during_call` function via `function_tool(raw_schema=...)`, where the
  schema is generated from `CustomFunction.parameters` (or `raw_body_schema` in JSON mode).
  The callable invokes `_execute_function_call(fn.name, args, functions, call_state)`.
- `tools = [FetchCategorySchema, FetchLead] + dynamic_tools` when `_function_calling`.
- Built-ins remain default so existing bots are unchanged.

## Phase 4 — Lifecycle hooks

- **pre_call** — in `entrypoint` (`bot.py:1603`, `bot_dev.py:249`), after config load /
  before greeting: run all `enabled` `pre_call` functions via `call_configured_function`,
  merge results + `store_variables` into `call_state["vars"]`. System prompt / greeting can
  reference `{{var}}` (extend `build_system_prompt` interpolation, `bot.py:1034`).
- **post_call** — in `save_call_data` (`bot.py:1751`, `bot_dev.py:449`): fire `post_call`
  functions with `{transcript, outcome, vars, lead_record}` payload. Fire-and-log; failures
  never block teardown. Analytics counters logged per call.

## Phase 5 — Test endpoint (`backend/routers/bots.py`)

`POST /bots/{bot_id}/functions/{function_id}/test` with sample args → runs dispatcher
server-side, returns `{status_code, latency_ms, response, extracted_vars, error}`. Powers
the UI **Test** button without a live call.

## Phase 6 — Tabbed settings UI (`frontend/src/views/BuilderView.tsx` + `types.ts`)

- Introduce a tab bar; relocate existing controls (no behavior change, just grouping):
  - **Speed** — silero_*, inactivity_*, post_speech_hold_ms, max_call_duration
  - **STT** — stt_provider/model/language
  - **TTS** — tts_provider/model/voice/language
  - **LLM** — llm_provider/model, temperature
  - **Functions** — new (Phase 6b)
  - **Advanced** — Developer JSON editor, ai_partner, close_markers
- Persona/system-prompt/opening/closing stay on a primary "Agent" tab (always visible).
- Keep `isAdvanced` gating within tabs where present.

### Phase 6b — Custom Function editor (Functions tab)

Matches the requested layout: Name, Description, API Endpoint (method dropdown + URL),
Timeout (ms), Headers (KV rows), Query Parameters (KV rows), Request Body (Form/JSON toggle
→ param rows: Name/Detail/Type/Required + Add), Store Fields as Variables (KV rows),
**Trigger** selector (pre/during/post-call), Test / Cancel / Save. List of functions with
enable toggles. New shared components: `KeyValueRows`, `ParamSchemaBuilder`.

## Phase 7 — Tests

- Backend: model round-trip persistence; dispatcher timeout + store_variables extraction;
  pre/post firing; test endpoint happy + error path.
- Frontend: tab switching, function add/edit/delete, KV row editing, Test button wiring.

## Rollout / safety

- Fully back-compatible: empty `functions` and built-in defaults preserve current behavior.
- Ship behind existing `function_calling` flag for during-call; pre/post gated by
  `enabled` + presence.
- Per-bot config is versioned/published like all other bot settings — no global state.

## Open questions

1. Variable interpolation syntax — reuse an existing `{{var}}` convention if one exists in
   prompts, else introduce `{{vars.x}}`.
2. Secrets in headers (API keys) — store plaintext in bot config for now, or add a secret
   ref? Recommend a follow-up for a secrets vault; plaintext acceptable for v1 internal use.
3. `during_call` tool name collisions with built-ins (FetchLead/FetchCategorySchema) —
   validate & reject on save.
