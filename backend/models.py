from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Environment = Literal["dev", "prod"]


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    email: str


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)


class UserRoleUpdate(BaseModel):
    role: Literal["admin", "user"]


class EnvironmentEndpoints(BaseModel):
    """API endpoints that differ between dev and prod, e.g. lead-qualification lookups."""

    lead_qualify_base_url: str = ""
    callback_api_url: str = ""
    callback_update_api_url: str = ""
    mis_api_base: str = ""


class PlatformSettings(BaseModel):
    active_environment: Environment = "dev"
    dev: EnvironmentEndpoints = Field(default_factory=EnvironmentEndpoints)
    prod: EnvironmentEndpoints = Field(default_factory=EnvironmentEndpoints)
    default_inactivity_phrase: str = ""
    default_close_markers: list[str] = Field(default_factory=list)


class FlowNode(BaseModel):
    id: str
    type: Literal["start", "message", "condition", "tool_call", "transfer", "global", "end"] = "message"
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})
    data: dict[str, Any] = Field(default_factory=dict)


class FlowEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str = ""
    condition: str = ""
    # Which of the source node's named outcomes (message transition, condition rule, etc.)
    # this edge is attached to — lets a node render one connector dot per outcome instead of
    # a single generic handle. Purely a rendering/authoring detail; flow_compiler.py only
    # ever reads label/condition, never this.
    source_handle: str = ""


class Flow(BaseModel):
    """The visual drag-and-drop flow graph (Phase 2b). Stored inside BotConfig so it is
    versioned/published/rolled-back together with the rest of the bot config, rather than
    as a parallel representation — per the decision to build the canvas on top of the
    existing version lifecycle instead of a separate system."""

    nodes: list[FlowNode] = Field(default_factory=list)
    edges: list[FlowEdge] = Field(default_factory=list)


class FunctionParam(BaseModel):
    """One LLM-facing argument of a custom function (the 'Request Body → Parameters'
    rows in the UI). Turned into a JSON-schema property when the function is registered
    as an LLM tool (during_call) — see bot_pipeline.py's dynamic tool builder."""

    name: str
    description: str = ""
    type: Literal["string", "number", "boolean", "object", "array"] = "string"
    required: bool = False


class StoreVariable(BaseModel):
    """'Store Fields as Variables' — extract a value from the function's JSON response
    and expose it as a dynamic variable (usable in the system prompt / greeting and by
    post-call functions). `json_path` is a dotted path into the response, e.g.
    'data.lead_name' or 'results.0.id'."""

    variable: str
    json_path: str


class CustomFunction(BaseModel):
    """A configurable HTTP call a bot can make before / during / after a conversation.

    - trigger='pre_call'    → fired in entrypoint before the greeting (lead/caller fetch).
    - trigger='during_call' → registered as an LLM tool the model can invoke mid-call.
    - trigger='post_call'   → fired during save_call_data teardown (analytics/persistence).

    `name` must be a valid identifier for during_call functions (it becomes the LLM tool
    name) and must not collide with built-ins (FetchLead, FetchCategorySchema).
    """

    id: str = ""
    name: str = ""
    description: str = ""
    url: str = ""
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    timeout_ms: int = 120000
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    body_mode: Literal["form", "json"] = "form"
    parameters: list[FunctionParam] = Field(default_factory=list)
    raw_body_schema: dict[str, Any] = Field(default_factory=dict)
    store_variables: list[StoreVariable] = Field(default_factory=list)
    trigger: Literal["pre_call", "during_call", "post_call"] = "during_call"
    enabled: bool = True
    # Back-compat: legacy stored functions used a free-form `custom_body` dict. Kept so
    # existing bots round-trip without data loss (bot.py:862 call_configured_function
    # still reads it). New functions should use body_mode + parameters instead.
    custom_body: dict[str, Any] = Field(default_factory=dict)


class WorkflowVariableSpec(BaseModel):
    """One piece of information a conversation node should extract from the caller
    during that step (workflow_engine.py's ConversationAgent turns this into part of
    the compiled instructions + reads it back via the set_variable tool)."""

    name: str
    type: Literal["string", "number", "boolean"] = "string"
    required: bool = False
    description: str = ""


class WorkflowTransitionSpec(BaseModel):
    """One outgoing branch of a `conversation` node — becomes a dynamically-built
    LiveKit function-tool in workflow_engine.py; calling it triggers an agent handoff
    to whichever node the matching edge (same id as this transition's id, via
    edge.sourceHandle) points to."""

    id: str
    key: str = ""
    label: str = ""
    condition: str = ""


class WorkflowConditionSpec(BaseModel):
    """One deterministic branch of a `condition` node — evaluated against collected
    variables (workflow_engine.py's _eval_condition), no LLM turn involved."""

    id: str
    path: str = ""
    op: Literal["eq", "ne", "gt", "lt", "contains", "exists"] = "eq"
    value: Any = None
    is_fallback: bool = False


class WorkflowFunctionSpec(BaseModel):
    """A `function` node's webhook call (workflow_engine.py's _run_function_node) —
    no LLM turn, response stored at `output_key` for later {{var}} interpolation or
    condition-node evaluation."""

    url: str = ""
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    body_format: Literal["json", "form"] = "json"
    custom_body: str = ""


class WorkflowNodeData(BaseModel):
    """Every field any workflow node kind might use — kept as one permissive shape
    (rather than a tagged union) so the frontend can freely add/remove fields per kind
    without a backend schema change; workflow_engine.py only ever reads the fields
    relevant to a given node's own `kind`, ignoring the rest.

    `kind` mirrors JD-Dashboard's workflow-canvas node types (start / conversation /
    condition / function / end_call / global) — see workflow_engine.py's module
    docstring for the full node-kind → runtime-behavior mapping this must match.
    """

    kind: Literal["start", "conversation", "condition", "function", "end_call", "global"] = "conversation"
    label: str = ""
    # start
    first_message: str = ""
    # conversation
    prompt: str = ""
    variables: list[WorkflowVariableSpec] = Field(default_factory=list)
    transitions: list[WorkflowTransitionSpec] = Field(default_factory=list)
    # condition
    conditions: list[WorkflowConditionSpec] = Field(default_factory=list)
    # function
    function: WorkflowFunctionSpec = Field(default_factory=WorkflowFunctionSpec)
    output_key: str = ""
    # end_call
    closing_message: str = ""
    # global
    trigger_description: str = ""
    action: Literal["end_call", "continue", "transfer"] = "continue"
    transfer_number: str = ""


class WorkflowNode(BaseModel):
    id: str
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})
    data: WorkflowNodeData = Field(default_factory=WorkflowNodeData)


class WorkflowEdge(BaseModel):
    id: str = ""
    source: str
    target: str
    # Which of the source node's named outcomes (transition/condition-branch id) this
    # edge is attached to — matched against WorkflowTransitionSpec.id / WorkflowConditionSpec.id
    # by workflow_engine.py's WorkflowGraph._target_of(). Empty for start/function nodes,
    # which have exactly one, unnamed, outgoing edge.
    sourceHandle: str = ""


class WorkflowGraphDef(BaseModel):
    """The visual workflow-bot graph — a real deterministic state machine at call time
    (workflow_engine.py), distinct from the prompt-compiled `Flow` above (flow_compiler.py,
    "Phase A": the model is instructed to follow it but can skip/reorder steps)."""

    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


class DynamicVariable(BaseModel):
    name: str
    default_value: str = ""


class AnalysisFieldDef(BaseModel):
    """One PM-defined post-call analysis field for a bot — the generic, per-bot-configurable
    counterpart to the legacy classifier's hardcoded qualification
    schema. See backend/post_call_analysis.py for the extractor that reads this list and
    backend/routers/bots.py for the list-level save-time validation it can't own itself
    (unique keys, field-count cap); the enum_options-required-for-type=="enum" invariant
    is enforced right here via `_check_enum_options_present`."""

    key: str
    label: str
    type: Literal["boolean", "text", "number", "enum"]
    description: str = ""
    enum_options: list[str] | None = None

    @model_validator(mode="after")
    def _check_enum_options_present(self) -> "AnalysisFieldDef":
        if self.type == "enum" and not (self.enum_options and [o for o in self.enum_options if o.strip()]):
            raise ValueError(f"analysis_fields entry {self.key!r} has type 'enum' but no enum_options")
        return self


class BotConfig(BaseModel):
    """Mirrors RuntimeConfig in frontend/src/types.ts — the fields a PM can edit.

    IMPORTANT: every field bot_pipeline.py actually reads from a bot's config (see
    `_bot_config.get(...)` calls there) MUST be declared here. Pydantic silently drops
    any field not declared on this model when `.model_dump()` is called in
    backend/routers/bots.py's create/save_draft/update_version handlers — a field missing
    here means the pipeline permanently sees only its hardcoded fallback, never whatever
    a PM configures in the UI. This bit us once already (agent_name, temperature,
    function_calling, post_speech_hold_ms, silero_*, inactivity_*_secs were all silently
    stripped on every save) — see test_saving_a_draft_persists_every_field_the_pipeline_actually_reads.
    """

    organization_name: str = ""
    agent_name: str = ""
    # Agents-page filter/organization only — never read by the call pipeline.
    tags: list[str] = Field(default_factory=list)
    # Persona gender. Hindi conjugates first-person verbs by speaker gender, so this drives
    # both the opening line's verb (बोल रही हूँ / बोल रहा हूँ) and the prompt's gender rule.
    persona_gender: Literal["female", "male"] = "female"
    ai_partner: str = ""
    language: str = "hi"
    # Conversational tone preset — key into bot.py's TONE_CONFIGS (e.g. "casual", "formal").
    # Lets otherwise-identical bots sound more/less formal without a separate prompt rewrite.
    tone: Literal["casual", "formal"] = "casual"
    voice: str = ""
    system_prompt: str = ""
    initial_message: str = ""
    # PM-declared placeholders usable as {{var_name}} in system_prompt/initial_message.
    # default_value is what a real call falls back to when the caller (test-call payload
    # or, in future, lead data) doesn't supply that variable — keeps {{var_name}} from
    # ever reaching the caller verbatim if it's left unfilled.
    dynamic_variables: list[DynamicVariable] = Field(default_factory=list)
    call_end_text: str = ""
    inactivity_end_text: str = ""
    close_markers: list[str] = Field(default_factory=list)
    max_call_duration: int = 600
    flow: Flow = Field(default_factory=Flow)

    # "standard" = the existing fixed-assistant pipeline (system_prompt/flow above,
    # compiled/instructed but not deterministically enforced). "workflow" = a real
    # state-machine bot (workflow_engine.py) driven entirely by the `workflow` graph
    # below — bot_dev_param.py's entrypoint dispatches to run_workflow_call() for these,
    # bypassing the rest of the fixed-assistant flow. Additive: every existing bot
    # defaults to "standard" and is completely unaffected.
    bot_type: Literal["standard", "workflow"] = "standard"
    workflow: WorkflowGraphDef = Field(default_factory=WorkflowGraphDef)
    # Prepended to every conversation node's compiled instructions (workflow_engine.py's
    # WorkflowGraph.compile_instructions) — shared context/persona across the whole graph,
    # since each node is otherwise its own independent LiveKit Agent.
    global_prompt: str = ""

    # Optional per-bot override of the global post-call analysis prompt
    # (backend/analysis_prompts.py's CALL_ANALYSIS_KEY template, edited by default on the
    # Library page). Empty string (the default) means "use the global prompt" — this is
    # NOT a supplementary note, it fully replaces the template for this bot's calls, so it
    # must still satisfy every REQUIRED_PLACEHOLDERS[CALL_ANALYSIS_KEY] placeholder and
    # emit the same JSON schema the callback worker parses. Validated with the exact same
    # rules as the global editor at save time (backend/routers/bots.py's update_version) —
    # never validate this yourself elsewhere, call analysis_prompts.validate_prompt_template.
    analysis_prompt: str = ""

    # PM-defined generic post-call analysis schema, for any bot type. When non-empty,
    # bot.py::_save_transcript_to_dashboard_db runs the generic schema-driven extractor
    # (backend/post_call_analysis.py) instead of the legacy qualification-schema classifier
    # (callback_worker/analysis.py), and persists its output to a separate field so the two
    # systems never collide. Empty (the default) means "use the legacy classifier" — zero
    # behavior change for every existing bot. Not gated on bot_type=="workflow": that was
    # tried first, but "standard" bot_type is also used for plenty of non-qualification
    # bots (support, HR, appointment) with no qualification_schema — presence of a
    # configured schema is a better signal than the bot's structural type.
    analysis_fields: list[AnalysisFieldDef] = Field(default_factory=list)

    # Real bot_pipeline.py runtime knobs (backend/evals.py:307-323).
    temperature: float = 0.4
    function_calling: bool = False
    functions: list[CustomFunction] = Field(default_factory=list)
    post_speech_hold_ms: int = 400
    silero_threshold: float = 0.6
    silero_min_speech_ms: int = 1000
    inactivity_first_rescue_secs: float = 4.0
    inactivity_first_nudge_gap_secs: float = 4.0
    inactivity_nudge_secs: float = 10.0
    inactivity_close_secs: float = 5.0
    api_urls: dict[str, str] = Field(default_factory=dict)
    prompt_config: dict[str, Any] = Field(default_factory=dict)
    recording: dict[str, Any] = Field(default_factory=dict)

    # Per-agent STT/TTS/LLM provider selection — see pipeline_providers.py at the repo
    # root (used by bot_pipeline.py). Empty string on each *_provider field means "use the
    # pipeline's hardcoded default", preserving exact prior behavior for existing bots.
    stt_provider: Literal["", "sarvam", "deepgram"] = ""
    stt_model: str = ""
    stt_language: str = ""
    # "acmecorp" = our own in-house IndicF5 TTS (livekit_indic5_tts.py); only relevant to
    # bot_type="workflow" today (workflow_engine.py's run_workflow_call), which treats
    # anything other than "sarvam" as the IndicF5 path — added here so a workflow bot's
    # saved config round-trips through BotConfig without this field being silently dropped.
    tts_provider: Literal["", "sarvam", "elevenlabs", "acmecorp"] = ""
    tts_model: str = ""
    tts_voice: str = ""
    tts_language: str = ""
    llm_provider: Literal["", "gemini", "openai"] = ""
    llm_model: str = ""
    # PM-facing "how easily can a caller interrupt the bot" knob — see
    # interruption_presets.py for the concrete parameters each preset resolves to.
    # "" (default) resolves to "balanced", matching every pre-existing bot's actual
    # behavior (see resolve_interruption_preset).
    interruption_sensitivity: Literal["", "patient", "balanced", "responsive"] = ""

    # Full per-provider parameter surface — see provider_params.py for the assembled kwargs
    # and the exact keys each accepts. These free-form dicts let a PM tune every relevant
    # Sarvam STT / Sarvam TTS / Gemini knob (VAD sensitivity, pace/pitch/loudness, top_p,
    # thinking_config, …) without a schema change per parameter. Empty = pipeline defaults.
    stt_options: dict[str, Any] = Field(default_factory=dict)
    tts_options: dict[str, Any] = Field(default_factory=dict)
    llm_options: dict[str, Any] = Field(default_factory=dict)

    # Conversational-polish intent (Vapi/Bland benchmark, Phase 2a). Wired into
    # bot_pipeline.py: noise_filter_sensitivity selects a threshold, backchanneling_enabled
    # gates a hold-message playback during tool calls (see bot_pipeline.py's
    # _play_hold_message / _NOISE_SENSITIVITY_THRESHOLDS usage).
    backchanneling_enabled: bool = False
    noise_filter_sensitivity: Literal["low", "medium", "high"] = "medium"
    extra: dict[str, Any] = Field(default_factory=dict)


class BotCreate(BaseModel):
    name: str
    description: str = ""
    config: BotConfig = Field(default_factory=BotConfig)


class BotUpdateConfig(BaseModel):
    config: BotConfig


class CompileFlowPreviewRequest(BaseModel):
    flow: Flow


class FunctionTestRequest(BaseModel):
    """Dry-run a custom function from the builder's 'Test' button. The function need not be
    saved yet — its full config is sent inline along with sample args."""

    function: CustomFunction
    args: dict[str, Any] = Field(default_factory=dict)


class ResponseMapping(BaseModel):
    """Extracts one value from a function's JSON response into a {{variable}}.

    `path` is a dotted path with numeric indices, e.g. "results.data.0.buyer_details.buyer_name".
    Allowed for any HTTP method — a POST search can produce variables just like a GET.
    """

    variable: str
    path: str


class CustomFunctionBase(BaseModel):
    """A user-configured API call attached to a bot.

    timing="pre_call": runs automatically before the call connects (while ringing); its
    response_mappings populate {{variables}} substituted anywhere in the system prompt.
    timing="in_call": registered as an LLM tool the bot may invoke mid-conversation;
    name/description/parameters are what the model sees.
    """

    name: str
    description: str = ""
    timing: Literal["pre_call", "in_call"]  # required — a deliberate authoring choice
    enabled: bool = True

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    # Raw request-body template stored as a string so {{tokens}} survive verbatim; parsed
    # per body_format at execution time. None means no body.
    body: str | None = None
    body_format: Literal["json", "form"] = "json"
    timeout_ms: int = 8000

    # in_call only: JSON schema of the arguments the LLM supplies when calling the tool.
    parameters: dict[str, Any] = Field(default_factory=dict)
    response_mappings: list[ResponseMapping] = Field(default_factory=list)


class CustomFunctionCreate(CustomFunctionBase):
    pass


class CustomFunctionUpdate(CustomFunctionBase):
    pass


class CustomFunctionTestRequest(CustomFunctionBase):
    """A function definition plus sample context values, used by the Test button to execute
    the request server-side and show the raw response + which variables it resolved."""

    sample_context: dict[str, str] = Field(default_factory=dict)


class AttemptStep(BaseModel):
    attempt: int
    language: str = ""
    bot_id: str = ""


class CallWindow(BaseModel):
    days: list[str] = Field(default_factory=list)
    start_time: str = "09:00"
    end_time: str = "20:00"
    timezone: str = "Asia/Kolkata"


class OutcomeRule(BaseModel):
    outcome: str
    action: Literal["retry", "stop", "dnc", "completed"]
    max_attempts: int | None = None
    retry_after_min: int | None = None
    language_override: str = ""


class DialingStrategy(BaseModel):
    enabled: bool = True
    outcome_rules: list[OutcomeRule] = Field(default_factory=list)
    call_windows: list[CallWindow] = Field(default_factory=list)
    attempt_sequence: list[AttemptStep] = Field(default_factory=list)
    max_attempts_total: int = 5
    max_attempts_per_day: int = 2
    lead_expiry_days: int = 30
    priority: Literal["low", "normal", "high", "urgent"] = "normal"


class SaveStrategyRequest(BaseModel):
    name: str
    strategy: DialingStrategy


class AssignBotRequest(BaseModel):
    bot_id: str


class SetStatusRequest(BaseModel):
    status: str


class ScheduleCampaignRequest(BaseModel):
    """Body for POST /{campaign_key}/schedule. `scheduled_at`, when given, is a naive
    Asia/Kolkata wall-clock datetime (no timezone field) — this platform is India-only
    today (DialerConfig.country defaults to "IN"), so a timezone picker would just be
    a control that always has to be set the same way."""

    send_now: bool
    scheduled_at: datetime | None = None


class CampaignLead(BaseModel):
    """A single contact/row imported from a campaign's CSV. `vars` holds every CSV
    column beyond phone_number/name/jduid verbatim, so the prompt injector (`{{col}}`)
    can reference any of them without the schema knowing column names in advance.

    `jduid` (AcmeCorp's internal per-user ID, which TSPL's dialer resolves to a real
    phone number on their side) is the primary identifier for TSPL-pushed campaigns —
    we never see the real number. `phone_number` is kept for direct-dial leads and is
    now optional; a row needs at least one of the two (enforced at CSV-upload time,
    not here, so existing direct-dial flows are unaffected)."""

    id: str = Field(alias="_id")
    campaign_id: str
    phone_number: str | None = None
    jduid: str | None = None
    name: str | None = None
    vars: dict[str, str] = Field(default_factory=dict)
    status: Literal["pending", "dialing", "completed", "failed", "rejected", "push_failed"] = "pending"
    failure_reason: str | None = None
    call_id: str | None = None
    estimated_cost: float | None = None

    model_config = {"populate_by_name": True}


class DialerConfig(BaseModel):
    """Campaign-level fields for TSPL's outbound-dialer push payload that don't vary
    per lead (unlike jduid/buyer_city/searched_keyword, which live on CampaignLead).
    Set once per campaign via PUT /{campaign_key}/dialer-config."""

    channel_name: str = ""
    channel_id: int | None = None
    bd: int | None = None
    service_id: str = ""
    service_source: str = ""
    page_type: str = "gallery_image"
    country: str = "IN"
    language: str = "en"


class SaveDialerConfigRequest(BaseModel):
    dialer_config: DialerConfig


class CampaignLeadUploadResult(BaseModel):
    total: int
    imported: int
    skipped: int


class PromptTemplateRequest(BaseModel):
    prompt_template: str


class PricingModelEntry(BaseModel):
    key: str
    label: str
    cost_inr_per_min: float
    company: str = ""
    latency_ms_min: int | None = None
    latency_ms_max: int | None = None
    tokens_min: int | None = None
    tokens_max: int | None = None


class PricingConfig(BaseModel):
    stt: list[PricingModelEntry] = Field(default_factory=list)
    llm: list[PricingModelEntry] = Field(default_factory=list)
    tts: list[PricingModelEntry] = Field(default_factory=list)
    telephony: list[PricingModelEntry] = Field(default_factory=list)


class PromptValidationResult(BaseModel):
    unknown_vars: list[str]


class LibraryPhraseCreate(BaseModel):
    category: Literal["voicemail", "hold_music", "dnc_trigger"]
    text: str
    language: str = ""
    notes: str = ""


class OutcomeEntryUpdate(BaseModel):
    display_label: str = ""
    description: str = ""


class LanguageSettingsUpsert(BaseModel):
    id: str
    name: str
    timeout_message: str = ""
    inactivity_nudge: str = ""
    lang_notes: str = ""


class AnalysisPromptUpdate(BaseModel):
    prompt_template: str


class RuntimeSettingsPayload(BaseModel):
    livekit_api_url: str = ""
    livekit_browser_url: str = ""
    livekit_agent_name: str = ""


class LangfuseSettingsPayload(BaseModel):
    enabled: bool = False
    environment: Literal["local", "staging", "prod"] = "local"
    base_url: str = ""
    send_transcripts: bool = True
    send_prompts: bool = True


class TestCallStartRequest(BaseModel):
    bot_id: str
    test_bot_version_id: str = ""
    campaign_id: str = ""
    lead_id: str = ""
    call_id: str = ""
    mobile: str = ""
    srchterm: str = ""
    buyer_name: str = ""
    city: str = ""
    test_worker_agent_name: str = ""
    custom_lead_json: str = ""
    # Tester-seeded overrides for the bot's own pre_call functions' query_params, keyed by
    # param name (flat across all pre_call functions — matches how bot.py's _pre_call_params
    # already merges lead_id/mobile/call_id into every pre_call function's request args).
    pre_call_params: dict[str, str] = Field(default_factory=dict)
    # Tester-supplied values for the bot's declared dynamic_variables (BotConfig above),
    # keyed by variable name. Falls back to each variable's own default_value in bot.py
    # when a name here is missing/blank.
    dynamic_variables: dict[str, str] = Field(default_factory=dict)


class TestCallStopRequest(BaseModel):
    room_name: str


class EvalScenario(BaseModel):
    """A scripted caller persona to simulate against a draft, plus pass/fail checks —
    Vapi/Bland-style pre-publish evals (Phase 2a from the competitive benchmark). Runs as an
    LLM-vs-LLM text simulation (no live telephony needed), so it works pre-publish without
    burning a real call."""

    name: str
    caller_persona: str
    max_turns: int = 4
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)


class EvalRunRequest(BaseModel):
    version_id: str = ""  # empty = current draft
    scenarios: list[EvalScenario] = Field(default_factory=list)  # empty = built-in defaults


class GeneratePromptRequest(BaseModel):
    mode: Literal["generate", "refine"]
    instruction: str
    current_prompt: str = ""  # only used/required when mode == "refine"
    # Which field this powers — system_prompt (default, back-compat), closing_line, or
    # analysis_prompt. See prompt_assist.generate_prompt for the per-target framing.
    target: Literal["system_prompt", "closing_line", "analysis_prompt", "global_prompt"] = "system_prompt"


class GenerateAgentRequest(BaseModel):
    """Create Agent > Create with AI: a free-text description of the agent to build."""

    description: str


class GenerateFunctionRequest(BaseModel):
    """Functions tab AI-assist: paste an API description / curl example / docs snippet and
    have the method/url/headers/query_params/parameters/store_variables filled in."""

    description: str


class GenerateEvalScenariosRequest(BaseModel):
    """Pre-publish evals panel's "Generate with AI" button: derives scenario personas + pass/
    fail checks tailored to the bot's own system_prompt instead of the generic built-in
    defaults."""

    system_prompt: str
    count: int = 3


class SummarizeVersionDiffRequest(BaseModel):
    """VersionDiffModal's AI summary: the already-computed {field: {old, new}} diff, keyed by
    top-level config field, for the PM-friendly plain-English summary."""

    diffs: dict[str, Any]


class TriageTestCallRequest(BaseModel):
    """Test panel's "What went wrong?" button: identifies the test call by room name (its
    call_id in the transcripts collection) so the transcript can be looked up server-side,
    plus whatever client-side error/close note the test session already surfaced."""

    room_name: str
    status: str = ""
    error: str = ""
    close_note: str = ""


class GenerateWorkflowRequest(BaseModel):
    """Create Agent > Create workflow with AI (mode='generate', the default — no bot exists
    yet) AND the Workflow tab's "Refine with AI" button (mode='refine' — edits an existing
    bot's graph in place). `description` doubles as the free-text instruction in refine mode."""

    description: str
    mode: Literal["generate", "refine"] = "generate"
    # refine mode only — the graph/functions/global_prompt to edit; ignored for mode='generate'.
    current_workflow: WorkflowGraphDef = Field(default_factory=WorkflowGraphDef)
    current_functions: list[CustomFunction] = Field(default_factory=list)
    current_global_prompt: str = ""


class ChatTurn(BaseModel):
    role: Literal["user", "bot"]
    text: str


class LlmChatReplyRequest(BaseModel):
    """Manual Chat: tester talks to the bot's LLM directly (no LiveKit/voice). Stateless —
    the full turn history is resent by the client every call, so nothing is persisted
    server-side."""

    system_prompt: str
    history: list[ChatTurn] = Field(default_factory=list)
    dynamic_variables: dict[str, str] = Field(default_factory=dict)
    function_mocks: dict[str, str] = Field(default_factory=dict)


class LlmChatSimulateRequest(BaseModel):
    """AI Simulated Chat: one call advances the LLM-vs-LLM simulation by exactly one
    caller-then-bot turn, so the frontend can render each pair as it arrives instead of
    waiting for the whole conversation to finish (see evals.run_scenario, which only
    returns a full transcript at the end — this is the turn-by-turn sibling of that)."""

    system_prompt: str
    caller_persona: str
    history: list[ChatTurn] = Field(default_factory=list)
    dynamic_variables: dict[str, str] = Field(default_factory=dict)
    function_mocks: dict[str, str] = Field(default_factory=dict)


PhoneEnvironment = Literal["dev", "preprod", "prod"]


class PhoneNumberCreate(BaseModel):
    """Fields mirror AcmeCorp's SIP trunk provisioning sheet (service_id/aod_ports/name/ip/
    dni/sip_trunk/username/password per trunk line) — `number` is the DNI (Dialed Number
    Identification), the actual number that gets dialed/answered. `sip_password` is
    write-only: accepted here but never echoed back by GET/list — see PhoneNumberOut."""

    number: str
    environment: PhoneEnvironment
    assigned_bot_id: str | None = None
    status: str = "active"
    service_id: str = ""
    aod_ports: int = 1
    name: str = ""
    ip: str = ""
    sip_trunk: str = ""
    sip_username: str = ""
    sip_password: str = ""


class ReassignPhoneNumberRequest(BaseModel):
    bot_id: str


class PhoneNumberUpdate(BaseModel):
    """Editable subset of PhoneNumberCreate for updating an existing phone number's
    details after creation. `sip_password` is only applied if non-empty (blank means
    'leave unchanged'), matching the write-only-never-echoed convention in PhoneNumberCreate."""

    status: str = "active"
    service_id: str = ""
    aod_ports: int = 1
    name: str = ""
    ip: str = ""
    sip_trunk: str = ""
    sip_username: str = ""
    sip_password: str = ""


class DialerCallStatusWebhook(BaseModel):
    """PLACEHOLDER shape for TSPL's per-Service-ID call-completion webhook (see
    plans/03-outbound-campaign-platform.md). Field names/mapping are our best guess pending
    TSPL's actual sample payload — job_id/call_id/phone_number/status are the fields we
    actually need to resolve which call_job this is about and what happened to it; `raw`
    keeps everything else TSPL sends so nothing is silently dropped before we've mapped it."""

    job_id: str | None = None
    call_id: str | None = None
    phone_number: str | None = None
    status: str
    recording_url: str | None = None
    duration_sec: int | None = None
    raw: dict = Field(default_factory=dict)


class DialerWebhookSecretUpdate(BaseModel):
    secret: str


AlertMetric = Literal[
    "call_count",
    "task_completion_rate_pct",
    "session_error_count",
    "concurrency_used",
    "p95_turn_latency_ms",
    "platform_error_rate_pct",
    "analysis_field_rate_pct",
]
AlertComparator = Literal["gt", "lt", "ge", "le"]
AlertWindow = Literal["5m", "30m", "1h", "12h", "24h"]
AlertFrequency = Literal["1m", "5m", "30m", "1h", "12h"]

# Single source of truth for window/frequency compatibility (mirrors Retell's own table —
# see the alerting plan): a window can only pair with a frequency at least as coarse, so the
# worker never re-evaluates a shorter window than it was actually validated against. Enforced
# below as a model invariant (AlertRuleBase._check_window_frequency_compat) rather than only
# in backend/routers/alerts.py, so any code path that constructs an AlertRule directly (a
# script, a different endpoint, a migration) can't bypass the check.
WINDOW_FREQUENCY_COMPAT: dict[str, list[str]] = {
    "5m": ["1m", "5m"],
    "30m": ["5m", "30m"],
    "1h": ["5m", "30m", "1h"],
    "12h": ["30m", "1h", "12h"],
    "24h": ["1h", "12h"],
}

# Call-status values a transcript's `status` field actually takes (see
# backend/metrics.py's NON_FAILURE_STATUSES, its "disconnected" usage, and the "abusive"
# close status set by bot.py/bot_dev.py/bot_dev_param.py/bot_pipeline.py) — narrows
# AlertRuleFilters.status from a free-form string so a typo can't silently create a rule
# that never matches anything and never fires.
AlertCallStatus = Literal["completed", "disconnected", "not_interested", "abusive"]

# The fixed set of post-call dispositions a call_outcome can take (see
# callback_worker/analysis.py's DISPOSITION_MAP) — same rationale as AlertCallStatus above.
AlertCallOutcome = Literal[
    "Short Hangup",
    "Voicemail",
    "Wrong Number",
    "Approved",
    "Enriched",
    "Interested",
    "Not Interested",
    "Could Not Confirm",
    "Alternate Number",
    "Already Spoken",
    "Will do it Myself",
    "Call Rescheduled",
    "Seller Intent",
    "Job Seeker",
    "Abusive Lead",
    "DNC Client : Don't Call Further",
    "Other Cases",
    "Technical Issue - Call Connected",
    "Language Issue",
]


class AlertRuleFilters(BaseModel):
    """Scoping/filter fields on a rule, per the plan's V1 filter set. `bot_ids` empty means
    all bots owned by the rule's creator (or literally every bot, for an admin) — evaluated
    fresh at eval time via resolve_owned_bot_ids, not frozen at create time."""

    bot_ids: list[str] = Field(default_factory=list)
    status: AlertCallStatus | None = None
    call_outcome: AlertCallOutcome | None = None
    # Only meaningful for metric == "analysis_field_rate_pct" — which PM-defined
    # analysis_fields key to check, and which value (stringified; a boolean field's value
    # is "true"/"false") counts as a match. Both required together — enforced below,
    # alongside the "exactly one bot" requirement, since a field's schema is per-bot(-version)
    # and there's no cross-bot notion of "the same field" to alert on across several bots at
    # once the way call_outcome/status can. See backend/metrics.py's analysis_field_rate_pct
    # branch for how the match is actually computed.
    analysis_field_key: str | None = None
    analysis_field_value: str | None = None


class AlertRuleBase(BaseModel):
    name: str
    metric: AlertMetric
    threshold_type: Literal["absolute"] = "absolute"
    comparator: AlertComparator
    threshold_value: float
    window: AlertWindow
    frequency: AlertFrequency
    filters: AlertRuleFilters = Field(default_factory=AlertRuleFilters)
    # V1 ships with exactly one notification channel; email/webhook are Phase 2.
    notify_via: Literal["in_app"] = "in_app"
    enabled: bool = True

    @model_validator(mode="after")
    def _check_window_frequency_compat(self) -> "AlertRuleBase":
        allowed = WINDOW_FREQUENCY_COMPAT.get(self.window)
        if allowed is None or self.frequency not in allowed:
            raise ValueError(f"frequency {self.frequency!r} is not compatible with window {self.window!r}")
        return self

    @model_validator(mode="after")
    def _check_analysis_field_rate_requirements(self) -> "AlertRuleBase":
        if self.metric != "analysis_field_rate_pct":
            return self
        if len(self.filters.bot_ids) != 1:
            raise ValueError(
                "analysis_field_rate_pct requires exactly one bot in filters.bot_ids — a PM-defined "
                "field's schema is per-bot, there's no cross-bot notion of 'the same field' to alert on"
            )
        if not self.filters.analysis_field_key or not self.filters.analysis_field_value:
            raise ValueError("analysis_field_rate_pct requires both filters.analysis_field_key and filters.analysis_field_value")
        return self


class AlertRuleCreate(AlertRuleBase):
    pass


class AlertRuleUpdate(AlertRuleBase):
    pass


class AlertRule(AlertRuleBase):
    id: str = Field(alias="_id")
    created_by: str
    next_eval_at: datetime
    last_evaluated_at: datetime | None = None
    created_at: datetime

    model_config = {"populate_by_name": True}


class AlertIncident(BaseModel):
    id: str = Field(alias="_id")
    rule_id: str
    rule_name: str
    bot_ids: list[str] = Field(default_factory=list)
    metric: AlertMetric
    current_value: float
    threshold_value: float
    status: Literal["open", "resolved"] = "open"
    triggered_at: datetime
    resolved_at: datetime | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _check_status_resolved_at_pairing(self) -> "AlertIncident":
        if (self.status == "resolved") != (self.resolved_at is not None):
            raise ValueError("resolved_at must be set if and only if status is 'resolved'")
        return self


class MapNumberToAgentRequest(BaseModel):
    """Maps one agent to one inbound number. phone_number=None clears the agent's mapping.

    A number belongs to at most one agent (unique index on phone_number), and this endpoint
    also drops any number the agent already held — so the relationship is 1:1 both ways.
    """

    phone_number: str | None = None
