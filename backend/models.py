from typing import Any, Literal

from pydantic import BaseModel, Field

Environment = Literal["dev", "prod"]


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    email: str


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
    type: Literal["message", "condition", "tool_call", "transfer", "end"] = "message"
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})
    data: dict[str, Any] = Field(default_factory=dict)


class FlowEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str = ""
    condition: str = ""


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
    ai_partner: str = ""
    language: str = "hi"
    voice: str = ""
    system_prompt: str = ""
    initial_message: str = ""
    call_end_text: str = ""
    inactivity_end_text: str = ""
    close_markers: list[str] = Field(default_factory=list)
    max_call_duration: int = 600
    flow: Flow = Field(default_factory=Flow)

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
    tts_provider: Literal["", "sarvam", "elevenlabs"] = ""
    tts_model: str = ""
    tts_voice: str = ""
    tts_language: str = ""
    llm_provider: Literal["", "gemini", "openai"] = ""
    llm_model: str = ""

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


class CampaignLead(BaseModel):
    """A single contact/row imported from a campaign's CSV. `vars` holds every CSV
    column beyond phone_number/name verbatim, so the prompt injector (`{{col}}`) can
    reference any of them without the schema knowing column names in advance."""

    id: str = Field(alias="_id")
    campaign_id: str
    phone_number: str
    name: str | None = None
    vars: dict[str, str] = Field(default_factory=dict)
    status: Literal["pending", "dialing", "completed", "failed"] = "pending"
    call_id: str | None = None
    estimated_cost: float | None = None

    model_config = {"populate_by_name": True}


class CampaignLeadUploadResult(BaseModel):
    total: int
    imported: int
    skipped: int


class PromptTemplateRequest(BaseModel):
    prompt_template: str


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


PhoneEnvironment = Literal["dev", "preprod", "prod"]


class PhoneNumberCreate(BaseModel):
    """Fields mirror Justdial's SIP trunk provisioning sheet (service_id/aod_ports/name/ip/
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
