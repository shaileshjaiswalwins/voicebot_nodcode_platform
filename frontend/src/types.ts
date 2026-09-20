import type { CallEvent, Transcript, Bot, Campaign, PhraseCategory, Flow, WorkflowGraphDef } from './api';

export type View = 'dashboard' | 'bots' | 'builder' | 'flow' | 'campaigns' | 'phone_numbers' | 'number_mapping' | 'test' | 'transcripts' | 'analytics' | 'library' | 'settings' | 'audit_log' | 'admin';
export type AgentWorkspaceMode = 'list' | 'builder';
export type BuilderMode = 'pm' | 'advanced';
export type DiagnosticSeverity = 'info' | 'warning' | 'error';

export type Diagnostic = {
  id: string;
  scope: string;
  severity: DiagnosticSeverity;
  message: string;
  action?: string;
  createdAt: string;
};

/** Mirrors backend/models.py CustomFunction — an HTTP call a bot can make before / during /
 * after a conversation. `trigger` decides when it fires; during_call functions become LLM
 * tools, pre_call/post_call functions run as lifecycle hooks. */
export type FunctionParamType = 'string' | 'number' | 'boolean' | 'object' | 'array';

export type FunctionParam = {
  name: string;
  description?: string;
  type: FunctionParamType;
  required: boolean;
};

export type StoreVariable = {
  variable: string;
  json_path: string;
};

/** Mirrors backend/models.py AnalysisFieldDef (Part 2: Post-Call Analysis Revamp) — a
 * single PM-defined field the generic post-call extractor (backend/post_call_analysis.py)
 * should pull out of the transcript, in addition to (or instead of, for Workflow bots with
 * a configured schema) the legacy hardcoded lead-qualification classifier. `key` is the
 * machine-readable JSON key the extractor writes into `analysis_fields_result`; `label` is
 * the PM-facing display name. `description` feeds the generated prompt so the extractor
 * knows what the field means. `enum_options` is required and must be non-empty when
 * `type === 'enum'` — validated both here (inline UX) and server-side (source of truth).
 *
 * The transcript doc bot.py writes alongside `analysis_fields_result` also carries a
 * sibling `analysis_fields_status: 'ok' | 'skipped' | 'failed'` field (not modeled as a
 * TS type here since no frontend code reads the transcript doc's shape today — see
 * backend/post_call_analysis.py::generate_generic_analysis) so a PM viewing the dashboard
 * can tell "the model determined this value" apart from "this call was skipped or Gemini
 * errored and nothing was ever really analyzed" — both cases would otherwise persist the
 * exact same per-type-empty `analysis_fields_result` shape. */
export type AnalysisFieldType = 'boolean' | 'text' | 'number' | 'enum';

/** Discriminated union on `type` so the compiler rejects invalid combinations (e.g. a
 * 'boolean' field carrying enum_options, or an 'enum' field missing them) instead of
 * silently allowing them the way a flat type with an optional `enum_options` would. */
export type AnalysisFieldDef =
  | { key: string; label: string; description: string; type: 'boolean' | 'text' | 'number'; enum_options?: undefined }
  | { key: string; label: string; description: string; type: 'enum'; enum_options: string[] };

export type FunctionTrigger = 'pre_call' | 'during_call' | 'post_call';
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export type CustomFunction = {
  id: string;
  name: string;
  description?: string;
  url?: string;
  method: HttpMethod;
  timeout_ms: number;
  headers: Record<string, string>;
  query_params: Record<string, string>;
  body_mode: 'form' | 'json';
  parameters: FunctionParam[];
  raw_body_schema?: Record<string, unknown>;
  store_variables: StoreVariable[];
  trigger: FunctionTrigger;
  enabled: boolean;
};

/** Response shape of POST /api/bots/{id}/functions/test. */
export type FunctionTestResult = {
  ok: boolean;
  status_code: number | null;
  latency_ms: number;
  response: unknown;
  extracted_vars: Record<string, unknown>;
  error: string | null;
  request: { method: string; url: string; params: unknown; json: unknown; data: unknown };
};

export type RuntimeConfig = {
  assistant_id?: string;
  agent_name?: string;
  organization_name?: string;
  /** Hindi conjugates first-person verbs by speaker gender — drives the opening line's
   * verb (बोल रही हूँ / बोल रहा हूँ) and the prompt's gender rule. */
  persona_gender?: 'female' | 'male';
  ai_partner?: string;
  /** This field is used by other language-scoped features (phrase library, campaign
   * attempt-sequence overrides). Changing it also auto-fills sensible stt_language/
   * tts_language defaults (see UI's language dropdown handler) if those are still unset —
   * but stt_language/tts_language below remain the source of truth for the pipeline and
   * can still be overridden independently of this field. */
  language?: string;
  /** Conversational tone the bot's prompt/opening line should adopt. Consumed by
   * bot.py's TONE_CONFIGS registry (keyed "casual"/"formal"). */
  tone?: 'casual' | 'formal';
  temperature?: number;
  /** Per-agent STT/TTS/LLM provider selection — empty string means "use the pipeline's
   * hardcoded default" (Sarvam STT/TTS + Gemini), preserving prior behavior exactly for
   * bots that don't set these. See pipeline_providers.py at the repo root. */
  stt_provider?: '' | 'sarvam' | 'deepgram';
  stt_model?: string;
  stt_language?: string;
  tts_provider?: '' | 'sarvam' | 'elevenlabs' | 'acmecorp';
  tts_model?: string;
  tts_voice?: string;
  tts_language?: string;
  llm_provider?: '' | 'gemini' | 'openai';
  llm_model?: string;
  /** Full per-provider parameter surface (see provider_params.py). Free-form so every
   * Sarvam STT / Sarvam TTS / Gemini knob is tunable without a schema change per param. */
  stt_options?: Record<string, unknown>;
  tts_options?: Record<string, unknown>;
  llm_options?: Record<string, unknown>;
  max_call_duration?: number;
  system_prompt?: string;
  initial_message?: string;
  /** PM-declared {{var_name}} placeholders usable in system_prompt/initial_message —
   * see backend/models.py DynamicVariable. default_value is what a real call falls back to
   * when the caller doesn't supply that variable. */
  dynamic_variables?: { name: string; default_value?: string }[];
  call_end_text?: string;
  inactivity_end_text?: string;
  function_calling?: boolean;
  post_speech_hold_ms?: number;
  /** Voice-activity gating used by bot_pipeline.py's muted-window capture logic. */
  silero_threshold?: number;
  silero_min_speech_ms?: number;
  /** Inactivity timeout sequence: first_rescue -> first_nudge_gap -> nudge (repeating) -> close. */
  inactivity_first_rescue_secs?: number;
  inactivity_first_nudge_gap_secs?: number;
  inactivity_nudge_secs?: number;
  inactivity_close_secs?: number;
  api_urls?: Record<string, string>;
  recording?: {
    service_id?: number | string;
    dialer_city?: string;
  };
  prompt_config?: Record<string, unknown>;
  functions?: CustomFunction[];
  close_markers?: string[];
  flow?: Flow;
  /** "standard" = this fixed-assistant pipeline (system_prompt/flow above). "workflow" =
   * a real state-machine bot (workflow_engine.py) driven by the `workflow` graph below,
   * bypassing the rest of this config's fields entirely at call time. Defaults to
   * "standard" — every existing bot is unaffected. */
  bot_type?: 'standard' | 'workflow';
  workflow?: WorkflowGraphDef;
  /** Prepended to every conversation node's compiled instructions — shared context/persona
   * across the whole graph, since each node is otherwise its own independent LiveKit Agent. */
  global_prompt?: string;
  /** Optional per-bot override of the global post-call analysis prompt (edited by default
   * on the Library page — backend/analysis_prompts.py's CALL_ANALYSIS_KEY template).
   * Empty (the default) means "use the global prompt". When set, this FULLY REPLACES the
   * template for this bot's calls, so it must still satisfy every required placeholder and
   * emit the exact same JSON schema the callback worker parses — validated server-side on
   * save with the same rules as the global editor. */
  analysis_prompt?: string;
  /** PM-defined structured fields for the generic post-call extractor (Part 2 of the
   * Post-Call Analysis Revamp) — travels inside this same draft/version save payload, no
   * separate endpoint. Only consumed by `_save_transcript_to_dashboard_db`'s new gate when
   * this bot is a Workflow Builder bot; legacy/campaign bots without a configured schema
   * keep going through the existing hardcoded `generate_call_analysis()` path unchanged. */
  analysis_fields?: AnalysisFieldDef[];
  /** "How easily can a caller interrupt the bot" — see interruption_presets.py. */
  interruption_sensitivity?: '' | 'patient' | 'balanced' | 'responsive';
  /** Config-surface only — see backend/models.py BotConfig for pipeline-wiring status. */
  backchanneling_enabled?: boolean;
  noise_filter_sensitivity?: 'low' | 'medium' | 'high';
  [key: string]: unknown;
};

export type TestCallStatus = 'Idle' | 'Creating room…' | 'Connecting to LiveKit…' | 'Waiting for bot to join…' | 'In call' | 'Failed';

export type TestForm = {
  campaign_id: string;
  lead_id: string;
  call_id: string;
  mobile: string;
  srchterm: string;
  buyer_name: string;
  city: string;
  test_worker_agent_name: string;
  /** Pinned version id — empty string means "use active published version" */
  test_bot_version_id: string;
  custom_lead_json: string;
  /** Tester-seeded overrides for this bot's own pre_call functions' query_params, flat
   * across all pre_call functions (matches bot.py's _pre_call_params merge). */
  pre_call_params: Record<string, string>;
  /** Tester-supplied values for the bot's declared dynamic_variables, keyed by name. */
  dynamic_variables: Record<string, string>;
};

export type ConversationItem =
  | {
      kind: 'turn';
      id: string;
      role: string;
      text: string;
      time: string;
      sortAt: number;
      interrupted: boolean;
    }
  | {
      kind: 'event';
      id: string;
      title: string;
      text: string;
      time: string;
      sortAt: number;
      severity: CallEvent['severity'];
    };

export type LibrarySection = PhraseCategory | 'outcomes' | 'languages';

export type CmdKResult =
  | { kind: 'view';       view: View;       label: string; icon: React.ReactNode; description?: string }
  | { kind: 'bot';        bot: Bot }
  | { kind: 'campaign';   campaign: Campaign }
  | { kind: 'transcript'; transcript: Transcript };

export type CmdKExtra = { botId?: string; transcriptId?: string; campaignKey?: string };

export type AgentUiState = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking';

// ---------------------------------------------------------------------------
// Custom Alerting (Part 1) — mirrors backend/models.py AlertRule / AlertIncident.
// ---------------------------------------------------------------------------

/** V1 metric set (6, all real today, zero new instrumentation) — grouped in the UI as
 * Call / Latency / System. See plan doc "Part 1: Custom Alerting". */
export type AlertMetric =
  | 'call_count'
  | 'task_completion_rate_pct'
  | 'session_error_count'
  | 'concurrency_used'
  | 'p95_turn_latency_ms'
  | 'platform_error_rate_pct';

export type AlertComparator = 'gt' | 'lt' | 'ge' | 'le';

/** V1 only supports 'absolute' — 'relative' (compare to last cycle) is specced for Phase 2. */
export type AlertThresholdType = 'absolute';

export type AlertWindow = '5m' | '30m' | '1h' | '12h' | '24h';
export type AlertFrequency = '1m' | '5m' | '30m' | '1h' | '12h';

/** Call-status values a transcript's `status` field actually takes — mirrors
 * backend/models.py AlertCallStatus. */
export type AlertCallStatus = 'completed' | 'disconnected' | 'not_interested' | 'abusive';

/** The fixed set of post-call dispositions a call_outcome can take — mirrors
 * backend/models.py AlertCallOutcome. */
export type AlertCallOutcome =
  | 'Short Hangup'
  | 'Voicemail'
  | 'Wrong Number'
  | 'Approved'
  | 'Enriched'
  | 'Interested'
  | 'Not Interested'
  | 'Could Not Confirm'
  | 'Alternate Number'
  | 'Already Spoken'
  | 'Will do it Myself'
  | 'Call Rescheduled'
  | 'Seller Intent'
  | 'Job Seeker'
  | 'Abusive Lead'
  | "DNC Client : Don't Call Further"
  | 'Other Cases'
  | 'Technical Issue - Call Connected'
  | 'Language Issue';

export type AlertRuleFilters = {
  bot_ids: string[];
  status?: AlertCallStatus;
  call_outcome?: AlertCallOutcome;
};

/** V1 ships with exactly one delivery channel; email/webhook are Phase 2 (disabled
 * "Coming soon" rows in the Create Alert dialog). */
export type AlertNotifyVia = 'in_app';

export type AlertRule = {
  _id: string;
  name: string;
  metric: AlertMetric;
  threshold_type: AlertThresholdType;
  comparator: AlertComparator;
  threshold_value: number;
  window: AlertWindow;
  frequency: AlertFrequency;
  filters: AlertRuleFilters;
  notify_via: AlertNotifyVia;
  enabled: boolean;
  created_by: string;
  next_eval_at?: string | null;
  last_evaluated_at?: string | null;
  created_at: string;
};

/** Payload shape for POST/PUT — no server-assigned fields. */
export type AlertRuleInput = {
  name: string;
  metric: AlertMetric;
  comparator: AlertComparator;
  threshold_value: number;
  window: AlertWindow;
  frequency: AlertFrequency;
  filters: AlertRuleFilters;
  enabled: boolean;
};

export type AlertIncidentStatus = 'open' | 'resolved';

export type AlertIncident = {
  _id: string;
  rule_id: string;
  rule_name: string;
  bot_ids: string[];
  metric: AlertMetric;
  current_value: number;
  threshold_value: number;
  status: AlertIncidentStatus;
  triggered_at: string;
  resolved_at?: string | null;
};
