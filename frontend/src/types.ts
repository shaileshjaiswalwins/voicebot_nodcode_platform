import type { CallEvent, Transcript, Bot, Campaign, PhraseCategory, Flow } from './api';

export type View = 'bots' | 'builder' | 'flow' | 'campaigns' | 'phone_numbers' | 'test' | 'transcripts' | 'analytics' | 'observability' | 'library' | 'settings' | 'audit_log';
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
  ai_partner?: string;
  /** This field is used by other language-scoped features (phrase library, campaign
   * attempt-sequence overrides) but has no effect on this bot's own STT/TTS — use
   * stt_language/tts_language below to actually configure the pipeline's language. */
  language?: string;
  temperature?: number;
  /** Per-agent STT/TTS/LLM provider selection — empty string means "use the pipeline's
   * hardcoded default" (Sarvam STT/TTS + Gemini), preserving prior behavior exactly for
   * bots that don't set these. See pipeline_providers.py at the repo root. */
  stt_provider?: '' | 'sarvam' | 'deepgram';
  stt_model?: string;
  stt_language?: string;
  tts_provider?: '' | 'sarvam' | 'elevenlabs';
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
  /** Config-surface only — see backend/models.py BotConfig for pipeline-wiring status. */
  backchanneling_enabled?: boolean;
  noise_filter_sensitivity?: 'low' | 'medium' | 'high';
  [key: string]: unknown;
};

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
