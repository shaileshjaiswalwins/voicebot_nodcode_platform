// Typed client for the backend/ FastAPI admin service (Phase 1a).
// Contract reconstructed from usage across frontend/src — see nocode_platform_alignment.md
// Phase 1a for the audit that produced this. Types intentionally match what every view
// already destructures; extend fields here rather than casting in views.

// Type-only import (erased at build) — safe despite types.ts importing from api.ts.
import type { AlertRule, AlertRuleInput, AlertIncident, CustomFunction, FunctionTestResult } from './types';

export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) || 'http://localhost:8000';

const TOKEN_STORAGE_KEY = 'nocode_platform_token';

export function getToken(): string {
  return localStorage.getItem(TOKEN_STORAGE_KEY) || '';
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

export function apiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// FastAPI error bodies are JSON like {"detail": "..."}; fall back to the raw text
// (e.g. a plain-text 500 from a proxy) when the body isn't that shape.
function parseErrorDetail(body: string): string {
  try {
    const parsed = JSON.parse(body);
    if (parsed && typeof parsed.detail === 'string') return parsed.detail;
  } catch {
    // not JSON — use the raw body as-is
  }
  return body;
}

const READ_TIMEOUT_MS = 8000;

async function request<T>(path: string, options: RequestInit = {}, timeoutMs: number = READ_TIMEOUT_MS, externalSignal?: AbortSignal): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  // externalSignal lets a caller cancel client-side waiting early (e.g. a "Stop" button) —
  // separate from the timeout abort above so we can tell the two apart in the catch below.
  const onExternalAbort = () => controller.abort();
  externalSignal?.addEventListener('abort', onExternalAbort);
  let response: Response;
  try {
    response = await fetch(apiUrl(path), { ...options, headers, signal: controller.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (externalSignal?.aborted) {
        throw new ApiError(0, `Request cancelled: ${path}`);
      }
      throw new ApiError(0, `Request timed out after ${timeoutMs}ms: ${path}`);
    }
    throw error;
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener('abort', onExternalAbort);
  }
  if (response.status === 401) {
    clearToken();
  }
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new ApiError(response.status, parseErrorDetail(body) || response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

// Like request(), but for multipart/form-data bodies — must not set Content-Type
// (the browser sets it with the correct boundary).
async function requestForm<T>(path: string, formData: FormData): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(apiUrl(path), { method: 'POST', headers, body: formData });
  if (response.status === 401) {
    clearToken();
  }
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new ApiError(response.status, parseErrorDetail(body) || response.statusText);
  }
  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ChatTurn = { role: 'user' | 'bot'; text: string };

export type BotStatus = 'active' | 'paused' | 'deleted';

export type Bot = {
  _id: string;
  name: string;
  /** The agent's spoken persona name (config.agent_name), resolved from the published or
   * draft version — distinct from `name`, which is the internal display label. */
  agent_name?: string;
  description?: string;
  assistant_id?: string;
  status: BotStatus;
  /** "standard" = fixed-assistant pipeline, "workflow" = visual-graph state machine — read
   * off the bot's active (or draft) version config by GET /api/bots. */
  bot_type?: 'standard' | 'workflow';
  /** Whether this bot has ever been published (active_version_id set) — more informative
   * than `status`, which reads "active" for every non-deleted bot regardless of lifecycle. */
  published?: boolean;
  updated_at: string;
  created_at?: string;
  active_version_id?: string | null;
  draft_version_id?: string | null;
  owner?: string;
  /** Total calls against this bot, aggregated server-side over the full
   * tbl_ai_vb_call_transcripts collection — not just whatever page of transcripts the
   * Transcripts view happens to have loaded client-side. */
  call_count?: number;
  calls_today?: number;
  avg_duration_sec?: number;
  tags?: string[];
  /** Set only while status='deleted' (backend/routers/bots.py delete_bot); cleared on
   * restore. Absent for every active bot. */
  deleted_at?: string;
};

export type BotVersion = {
  _id: string;
  bot_id: string;
  version: number;
  state: 'draft' | 'published';
  config: Record<string, unknown>;
  notes?: string;
  published_at?: string;
  created_at: string;
};

export type AttemptStep = { attempt: number; language?: string; bot_id?: string };
export type CallWindow = { days: string[]; start_time: string; end_time: string; timezone: string };
export type DialingStrategy = {
  enabled: boolean;
  outcome_rules: OutcomeRule[];
  call_windows: CallWindow[];
  attempt_sequence: AttemptStep[];
  max_attempts_total: number;
  max_attempts_per_day: number;
  lead_expiry_days: number;
  priority: 'low' | 'normal' | 'high' | 'urgent';
};

/** Campaign-level fields for TSPL's outbound-dialer push payload that don't vary per
 * lead (unlike jduid/buyer_city/searched_keyword, which live on CampaignLead.vars). */
export type DialerConfig = {
  channel_name: string;
  channel_id: number | null;
  bd: number | null;
  service_id: string;
  service_source: string;
  page_type: string;
  country: string;
  language: string;
};

export type Campaign = {
  _id: string;
  campaign_key: string;
  name: string;
  bot_id?: string;
  status?: string;
  dialing_strategy?: DialingStrategy;
  dialer_config?: DialerConfig;
  lead_api?: { url?: string; endpoint?: string };
  prompt_template?: string;
};

export type CampaignLeadStatus = 'pending' | 'dialing' | 'completed' | 'failed' | 'rejected' | 'push_failed';

export type CampaignLead = {
  _id: string;
  campaign_id: string;
  /** Direct-dial leads only. TSPL-pushed leads use jduid instead — a row has one or the other. */
  phone_number?: string | null;
  /** Justdial's internal per-user ID — the primary identifier for TSPL-pushed campaigns,
   * which never see the real phone number. */
  jduid?: string | null;
  name?: string;
  vars: Record<string, string>;
  status: CampaignLeadStatus;
  failure_reason?: string | null;
  call_id?: string;
  estimated_cost?: number;
};

export type CampaignLeadUploadResult = {
  total: number;
  imported: number;
  skipped: number;
};

export type CampaignProgress = {
  queued: number;
  in_progress: number;
  /** Pushed to TSPL's outbound-dialer API, awaiting their async completion callback. */
  dialing: number;
  completed: number;
  failed: number;
  total: number;
};

export type PricingModelEntry = {
  key: string;
  label: string;
  cost_inr_per_min: number;
  company?: string;
  latency_ms_min?: number | null;
  latency_ms_max?: number | null;
  tokens_min?: number | null;
  tokens_max?: number | null;
};

export type PricingConfig = {
  stt: PricingModelEntry[];
  llm: PricingModelEntry[];
  tts: PricingModelEntry[];
  telephony: PricingModelEntry[];
};

export type PricingTier = {
  name: string;
  stt: string;
  llm: string;
  tts: string;
  telephony: string;
  cost_per_min: number;
};

export type CallDetail = {
  call_id: string;
  status: string | null;
  cost_inr: Record<string, number>;
  latency_ms: Record<string, number>;
  transcript: Array<{ role?: string; text?: string; [key: string]: unknown }>;
  call_duration_sec?: number | null;
  recording_url?: string;
  analysis?: {
    call_outcome?: string;
    call_outcome_description?: string;
    call_summary?: string;
    [key: string]: unknown;
  };
};

export type CampaignOutcomes = {
  counts: Record<string, number>;
  total_analyzed: number;
  total_with_calls: number;
};

export type PhoneNumberEnvironment = 'dev' | 'preprod' | 'prod';

export type PhoneNumber = {
  _id: string;
  number: string; // DNI — the actual dialed number
  environment: PhoneNumberEnvironment;
  assigned_bot_id?: string | null;
  assigned_bot_version_id?: string | null;
  status: string;
  service_id?: string;
  aod_ports?: number;
  name?: string;
  ip?: string;
  sip_trunk?: string;
  sip_username?: string;
  // sip_password is write-only — never present on anything the API returns
  created_at: string;
  updated_at: string;
};

export type NumberMapping = {
  phone_number: string;
  /** LiveKit worker pool the number's calls arrive on — NOT the persona name. */
  livekit_agent_name: string;
  environment: string;
  bot_id?: string | null;
  /** The agent's display name. */
  bot_name?: string | null;
  /** The name the caller actually hears (config.agent_name on the published version). */
  persona_name?: string | null;
};

export type CallEvent = {
  _id: string;
  event_type: string;
  message?: string;
  created_at: string;
  severity?: 'info' | 'warning' | 'error';
  details?: Record<string, unknown>;
};

export type CurrentUser = {
  email: string;
  role: string;
};

export type AccountEntry = {
  email: string;
  role: string;
  is_sso: boolean;
};

export type AuditLogEntry = {
  _id: string;
  actor: string;
  actor_role?: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  details?: Record<string, unknown>;
  created_at: string;
};

export type FallbackEvent = {
  _id: string;
  room_name: string;
  bot_id: string;
  test_bot_version_id: string;
  reason: string;
  worker: string;
  created_at: string;
};

export type WorkerHealth = {
  agent_name: string;
  pid: number | null;
  host: string;
  last_seen: string;
  started_at: string;
  age_seconds: number | null;
  stale: boolean;
};

export type DispatchFailure = {
  _id: string;
  room_name: string;
  agent_name: string;
  bot_id: string;
  timeout_seconds: number;
  created_at: string;
};

export type TranscriptTurn = { role: string; text?: string; created_at?: string; interrupted?: boolean; event_type?: string };

export type Transcript = {
  _id: string;
  call_id?: string;
  lead_id?: string;
  status?: string;
  campaign_id?: string;
  bot_id?: string;
  call_duration_sec?: number;
  transcript_count?: number;
  transcript?: TranscriptTurn[];
  live_transcript?: TranscriptTurn[];
  verified_transcript?: TranscriptTurn[];
  verified_transcript_status?: 'pending' | 'succeeded' | 'unavailable' | 'failed';
  verified_transcript_error?: string;
  transcript_quality_flags?: string[];
  created_at: string;
  bot_version_id?: string;
  callback_status?: string;
  analysis_transcript_source?: string;
  transcript_source?: string;
  latency_metrics?: { max_response_delay_ms?: number; first_response_delay_ms?: number };
  recording_url?: string;
  recording_source?: string;
  room_name?: string;
  /** Call origin: "web_test" (dashboard Test Call) vs "batch" (campaign/SIP-dialed).
   * Absent on transcripts saved before this field existed — treat as "batch". */
  source?: 'web_test' | 'batch';
  /** Post-call analysis computed inline (bot.py `_save_transcript_to_dashboard_db`) or
   * by callback_worker/worker.py for legacy production calls. Absent when analysis
   * failed silently and no fallback was persisted, or for older transcripts saved
   * before this field existed. */
  analysis?: {
    call_outcome?: string;
    call_outcome_description?: string;
    call_summary?: string;
    is_business?: string;
    business_intent?: string;
    b2b_user?: string;
    business_name?: string;
    business_city?: string;
    qna?: Array<{ id?: string; question?: string; answer?: string; [key: string]: unknown }>;
    product_change?: Record<string, unknown>;
    rescheduled_to?: string;
    deal_value?: string;
    lead_intent_score?: string;
    urgency_flag?: string;
    [key: string]: unknown;
  };
  /** PM-defined schema-driven analysis for Workflow Builder bots (bot.py
   * `_save_transcript_to_dashboard_db`), kept separate from the legacy `analysis`
   * object above. `analysis_fields_status` distinguishes "the model genuinely
   * determined false/0/empty" ('ok') from "nothing was ever actually analyzed"
   * ('skipped' — no transcript content, or 'failed' — e.g. Gemini was down). */
  analysis_fields_status?: 'ok' | 'skipped' | 'failed';
  analysis_fields_result?: Record<string, unknown>;
};

export type PhraseCategory = 'voicemail' | 'hold_music' | 'dnc_trigger';

export type OutcomeRule = {
  outcome: string;
  action: 'retry' | 'stop' | 'dnc' | 'completed';
  max_attempts?: number;
  retry_after_min?: number;
  language_override?: string;
};

export type LangfuseSettings = {
  enabled?: boolean;
  credentials_configured?: boolean;
  environment?: 'local' | 'staging' | 'prod';
  base_url?: string;
  send_transcripts?: boolean;
  send_prompts?: boolean;
  runtime_status?: { last_error?: string };
};

export type TestRecordingLookup = { recording_url?: string; recording_source?: string };

export type OutcomeAnalytics = {
  by_status: Record<string, number>;
  by_outcome: Record<string, number>;
  total: number;
  ended_naturally: number;
  avg_duration_sec: number;
};

export type QualityAlert = {
  alert: boolean;
  message: string;
  hours: number;
  total_calls: number;
  bad_calls: number;
  bad_pct: number;
};

export type RuntimeSettings = {
  _id?: string;
  livekit_api_url?: string;
  livekit_browser_url?: string;
  livekit_agent_name?: string;
  /** Raw LIVEKIT_AGENT_NAME from the backend's env — the name the real worker process
   * (bot_dev_param.py) actually registers under. Never shadowed by a saved override, so the
   * UI can detect/offer to reset a stale livekit_agent_name that no longer matches it. */
  livekit_agent_name_env_default?: string;
  livekit_credentials_configured?: boolean;
};

export type LanguageSettings = {
  id: string;
  name: string;
  timeout_message?: string;
  inactivity_nudge?: string;
  lang_notes?: string;
  updated_at?: string;
};

export type FlowNodeType = 'start' | 'message' | 'condition' | 'tool_call' | 'transfer' | 'global' | 'end';
export type FlowNode = {
  id: string;
  type: FlowNodeType;
  position: { x: number; y: number };
  data: Record<string, unknown>;
};
export type FlowEdge = {
  id: string;
  source: string;
  target: string;
  label?: string;
  condition?: string;
  source_handle?: string;
};
export type Flow = { nodes: FlowNode[]; edges: FlowEdge[] };

/** Mirrors backend/models.py's Workflow* models exactly — the real state-machine graph
 * consumed by workflow_engine.py's WorkflowGraph (distinct from Flow/FlowNode above,
 * which is prompt-compiled by flow_compiler.py and only instructs, not enforces). */
export type WorkflowNodeKind = 'start' | 'conversation' | 'condition' | 'function' | 'end_call' | 'global';

export type WorkflowVariableSpec = {
  name: string;
  type?: 'string' | 'number' | 'boolean';
  required?: boolean;
  description?: string;
};

export type WorkflowTransitionSpec = {
  id: string;
  key?: string;
  label?: string;
  condition?: string;
};

export type WorkflowConditionSpec = {
  id: string;
  path?: string;
  op?: 'eq' | 'ne' | 'gt' | 'lt' | 'contains' | 'exists';
  value?: unknown;
  is_fallback?: boolean;
};

export type WorkflowFunctionSpec = {
  url?: string;
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  headers?: Record<string, string>;
  query_params?: Record<string, string>;
  body_format?: 'json' | 'form';
  custom_body?: string;
};

export type WorkflowNodeData = {
  kind: WorkflowNodeKind;
  label?: string;
  first_message?: string;
  prompt?: string;
  variables?: WorkflowVariableSpec[];
  transitions?: WorkflowTransitionSpec[];
  conditions?: WorkflowConditionSpec[];
  function?: WorkflowFunctionSpec;
  output_key?: string;
  closing_message?: string;
  trigger_description?: string;
  action?: 'end_call' | 'continue' | 'transfer';
  transfer_number?: string;
};

export type WorkflowNode = {
  id: string;
  position?: { x: number; y: number };
  data: WorkflowNodeData;
};

export type WorkflowEdge = {
  id?: string;
  source: string;
  target: string;
  sourceHandle?: string;
};

export type WorkflowGraphDef = { nodes: WorkflowNode[]; edges: WorkflowEdge[] };

export type LanguageOption = { id: string; label: string };
export type VoiceOption = { id: string; label: string; gender?: string };

export type LibraryPhrase = {
  _id: string;
  category: PhraseCategory;
  text: string;
  language?: string;
  notes?: string;
  created_by?: string;
  updated_at: string;
};

export type OutcomeEntry = { key: string; display_label?: string; description: string; updated_at?: string };

export type AnalysisPromptKey = 'call_analysis' | 'b2b_score';

export type AnalysisPromptEntry = {
  _id: string;
  key: AnalysisPromptKey;
  prompt_template: string;
  updated_by?: string;
  updated_at?: string;
};

export type EvalScenario = {
  name: string;
  caller_persona: string;
  max_turns?: number;
  must_contain?: string[];
  must_not_contain?: string[];
};
export type EvalTurn = { role: 'caller' | 'bot'; text: string };
export type EvalScenarioResult = {
  scenario: string;
  passed: boolean;
  transcript: EvalTurn[];
  missing_required_phrases: string[];
  forbidden_phrases_found: string[];
};
export type EvalRun = {
  _id: string;
  bot_id: string;
  version_id: string;
  run_by?: string;
  created_at: string;
  results: EvalScenarioResult[];
  total: number;
  passed: number;
  failed: number;
};

export type Environment = 'dev' | 'prod';
export type EnvironmentEndpoints = {
  lead_qualify_base_url?: string;
  callback_api_url?: string;
  callback_update_api_url?: string;
  mis_api_base?: string;
};
export type PlatformSettings = {
  active_environment: Environment;
  dev: EnvironmentEndpoints;
  prod: EnvironmentEndpoints;
  default_inactivity_phrase?: string;
  default_close_markers?: string[];
};

export type DailyCount = { date: string; count: number };
export type DailyDuration = { date: string; avg_duration_sec: number };
export type OutcomeCount = { outcome: string; count: number };

export type BotMetrics = {
  total_calls: number;
  calls_today: number;
  calls_this_week: number;
  calls_this_month: number;
  avg_duration_sec: number;
  success_rate_pct: number;
  trend_vs_previous_pct: { total_calls: number | null; success_rate: number | null };
  daily_volume: DailyCount[];
  daily_avg_duration: DailyDuration[];
  outcome_breakdown: OutcomeCount[];
};

export type BotRankingEntry = { bot_id: string; name: string; success_rate_pct: number; call_count: number };

export type DashboardSummary = {
  total_agents: number;
  total_calls_all_time: number;
  total_minutes_all_time: number;
  calls_today: number;
  best_performing_bot: BotRankingEntry | null;
  least_performing_bot: BotRankingEntry | null;
  daily_volume: DailyCount[];
};

export type ExecMetrics = {
  window_days: number;
  north_star: { qualified_live_call_hours: number; total_calls: number };
  business_value: { task_completion_rate_pct: number; conversion_yield_pct: number };
  system_quality: {
    p95_turn_latency_ms: number;
    avg_first_response_latency_ms: number;
    avg_mid_call_latency_ms: number;
    platform_error_rate_pct: number;
  };
  platform_adoption: { active_production_workflows: number };
};

// ---------------------------------------------------------------------------
// api client
// ---------------------------------------------------------------------------

export const api = {
  // auth
  async login(email: string, password: string): Promise<{ token: string; email: string }> {
    const result = await request<{ token: string; email: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    setToken(result.token);
    return result;
  },
  async signup(email: string, password: string): Promise<{ token: string; email: string }> {
    const result = await request<{ token: string; email: string }>('/api/auth/signup', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    setToken(result.token);
    return result;
  },
  logout(): void {
    clearToken();
  },
  me(): Promise<CurrentUser> {
    return request('/api/auth/me');
  },
  listUsers(): Promise<AccountEntry[]> {
    return request('/api/auth/users');
  },
  updateUserRole(email: string, role: string): Promise<AccountEntry> {
    return request(`/api/auth/users/${encodeURIComponent(email)}/role`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    });
  },
  deleteUser(email: string): Promise<void> {
    return request(`/api/auth/users/${encodeURIComponent(email)}`, { method: 'DELETE' });
  },

  // bots + version lifecycle
  bots(): Promise<Bot[]> {
    return request('/api/bots');
  },
  createBot(payload: { name: string; description?: string; config: Record<string, unknown> }): Promise<Bot> {
    return request('/api/bots', { method: 'POST', body: JSON.stringify(payload) });
  },
  bot(id: string): Promise<{ bot: Bot; versions: BotVersion[] }> {
    return request(`/api/bots/${id}`);
  },
  /** Custom functions saved against a bot (read from the live version's config.functions,
   * or a specific version if versionId is given). */
  listBotFunctions(id: string, versionId?: string): Promise<{
    bot_id: string; version_id: string; version: number; state: string;
    count: number; functions: CustomFunction[];
  }> {
    return request(`/api/bots/${id}/functions${versionId ? `?version_id=${versionId}` : ''}`);
  },
  saveDraft(id: string, config: Record<string, unknown>): Promise<{ draft_version_id: string }> {
    return request(`/api/bots/${id}/draft`, { method: 'PUT', body: JSON.stringify({ config }) });
  },
  updateVersion(id: string, versionId: string, config: Record<string, unknown>): Promise<{ draft_version_id: string }> {
    return request(`/api/bots/${id}/versions/${versionId}`, { method: 'PUT', body: JSON.stringify({ config }) });
  },
  unpublish(id: string): Promise<{ draft_version_id: string }> {
    return request(`/api/bots/${id}/unpublish`, { method: 'POST' });
  },
  renameBot(id: string, name: string, description: string): Promise<Bot> {
    return request(`/api/bots/${id}`, { method: 'PUT', body: JSON.stringify({ name, description }) });
  },
  publish(id: string): Promise<{ active_version_id: string }> {
    return request(`/api/bots/${id}/publish`, { method: 'POST' });
  },
  rollback(id: string, versionId: string): Promise<{ draft_version_id: string }> {
    return request(`/api/bots/${id}/rollback/${versionId}`, { method: 'POST' });
  },
  deleteBot(id: string): Promise<{ ok: boolean }> {
    return request(`/api/bots/${id}`, { method: 'DELETE' });
  },
  deletedBots(): Promise<Bot[]> {
    return request('/api/bots/deleted');
  },
  restoreBot(id: string): Promise<{ ok: boolean }> {
    return request(`/api/bots/${id}/restore`, { method: 'POST' });
  },
  botMetrics(id: string, days: 7 | 30 = 7): Promise<BotMetrics> {
    return request(`/api/bots/${id}/metrics?days=${days}`);
  },
  dashboardSummary(): Promise<DashboardSummary> {
    return request('/api/dashboard/summary');
  },
  execMetrics(): Promise<ExecMetrics> {
    return request('/api/dashboard/exec-metrics');
  },
  /** Triggers a browser download of the CSV — can't use request()'s json() parsing, and a
   * plain <a href> can't carry the Bearer auth header, so this fetches as a blob directly. */
  async exportBots(): Promise<void> {
    const token = getToken();
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(apiUrl('/api/bots/export'), { headers });
    if (!response.ok) throw new ApiError(response.status, await response.text().catch(() => response.statusText));
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'agents.csv';
    a.click();
    URL.revokeObjectURL(url);
  },
  compileFlowPreview(flow: Flow): Promise<{ compiled_prompt: string }> {
    return request('/api/bots/compile-flow-preview', { method: 'POST', body: JSON.stringify({ flow }) });
  },
  testCustomFunction(
    id: string,
    fn: CustomFunction,
    args: Record<string, unknown> = {},
  ): Promise<FunctionTestResult> {
    return request(`/api/bots/${id}/functions/test`, {
      method: 'POST',
      body: JSON.stringify({ function: fn, args }),
    });
  },

  // pre-publish evals/simulations
  runEvals(id: string, versionId?: string, scenarios?: EvalScenario[], signal?: AbortSignal): Promise<EvalRun> {
    // Longer timeout than the default 8s: this simulates each scenario as a multi-turn
    // LLM-vs-LLM conversation (backend/evals.py's run_scenario), which easily takes tens
    // of seconds across the default scenario set. `signal` lets the UI's Stop button cancel
    // client-side waiting early — note the backend has no cancellation hook of its own, so
    // the simulation keeps running server-side until it finishes; this only stops the wait.
    return request(`/api/bots/${id}/evals/run`, {
      method: 'POST',
      body: JSON.stringify({ version_id: versionId || '', scenarios: scenarios || [] }),
    }, 120000, signal);
  },
  // Evals panel's "Generate with AI" button — derives scenarios tailored to the bot's own
  // system_prompt instead of the two generic built-in defaults. No bot exists lookup needed,
  // same free-standing shape as generateFunction.
  generateEvalScenarios(systemPrompt: string, count = 3): Promise<EvalScenario[]> {
    return request<{ scenarios: EvalScenario[] }>(
      '/api/bots/generate-eval-scenarios',
      { method: 'POST', body: JSON.stringify({ system_prompt: systemPrompt, count }) },
      30000,
    ).then((r) => r.scenarios);
  },
  listEvals(id: string): Promise<EvalRun[]> {
    return request(`/api/bots/${id}/evals`);
  },

  // Test LLM: text-only chat against the bot's LLM, no LiveKit/voice involved
  llmChatReply(
    id: string,
    systemPrompt: string,
    history: ChatTurn[],
    dynamicVariables: Record<string, string>,
    functionMocks: Record<string, string>,
  ): Promise<{ text: string }> {
    return request(
      `/api/bots/${id}/llm-chat/reply`,
      {
        method: 'POST',
        body: JSON.stringify({
          system_prompt: systemPrompt,
          history,
          dynamic_variables: dynamicVariables,
          function_mocks: functionMocks,
        }),
      },
      30000,
    );
  },
  llmChatSimulateTurn(
    id: string,
    systemPrompt: string,
    callerPersona: string,
    history: ChatTurn[],
    dynamicVariables: Record<string, string>,
    functionMocks: Record<string, string>,
  ): Promise<{ ended: boolean; caller_text: string | null; bot_text: string | null }> {
    return request(
      `/api/bots/${id}/llm-chat/simulate-turn`,
      {
        method: 'POST',
        body: JSON.stringify({
          system_prompt: systemPrompt,
          caller_persona: callerPersona,
          history,
          dynamic_variables: dynamicVariables,
          function_mocks: functionMocks,
        }),
      },
      30000,
    );
  },

  // AI-assisted text generation/refinement — shared by System prompt, Closing line, and
  // Analysis prompt override in BotConfigTabs (each with its own server-side framing, see
  // backend/prompt_assist.py). `target` defaults to 'system_prompt' for back-compat.
  generatePrompt(
    id: string,
    mode: 'generate' | 'refine',
    instruction: string,
    currentPrompt: string,
    target: 'system_prompt' | 'closing_line' | 'analysis_prompt' | 'global_prompt' = 'system_prompt',
  ): Promise<{ text: string }> {
    return request(
      `/api/bots/${id}/generate-prompt`,
      { method: 'POST', body: JSON.stringify({ mode, instruction, current_prompt: currentPrompt, target }) },
      30000,
    );
  },
  // Create Agent > Create with AI — no bot exists yet, so this is bot-less.
  generateAgent(description: string): Promise<{
    agent_name: string;
    description: string;
    persona_gender: string;
    stt_language: string;
    tts_language: string;
    initial_message: string;
    call_end_text: string;
    system_prompt: string;
    interruption_sensitivity: string;
  }> {
    return request('/api/bots/generate-agent', { method: 'POST', body: JSON.stringify({ description }) }, 30000);
  },
  // Functions tab AI-assist — paste a curl example/API description, get a draft CustomFunction
  // (minus id) to review before saving. No bot exists lookup needed.
  generateFunction(description: string): Promise<Omit<CustomFunction, 'id' | 'enabled' | 'timeout_ms' | 'trigger' | 'raw_body_schema'>> {
    return request('/api/bots/generate-function', { method: 'POST', body: JSON.stringify({ description }) }, 30000);
  },
  // VersionDiffModal AI summary — diffs is {field: {old, new}} for every changed top-level key,
  // already computed client-side; this just narrates it in plain English.
  summarizeVersionDiff(diffs: Record<string, { old: unknown; new: unknown }>): Promise<{ summary: string }> {
    return request('/api/bots/summarize-version-diff', { method: 'POST', body: JSON.stringify({ diffs }) }, 30000);
  },
  // Test panel's "What went wrong?" button — server looks up the transcript by room name and
  // pairs it with the bot's current instructions before asking for a diagnosis.
  triageTestCall(
    botId: string,
    roomName: string,
    status: string,
    error: string,
    closeNote: string,
  ): Promise<{ diagnosis: string; transcript_found: boolean }> {
    return request(
      `/api/bots/${botId}/triage-test-call`,
      { method: 'POST', body: JSON.stringify({ room_name: roomName, status, error, close_note: closeNote }) },
      30000,
    );
  },
  // Create Agent > Create workflow with AI — same idea, but generates a full WorkflowGraphDef
  // (nodes/edges/conditions/function-calls) instead of just prompt fields. Any HTTP endpoint the
  // description implies comes back as WORKFLOW_URL_PLACEHOLDER — see workflowPlaceholders.ts.
  generateWorkflow(description: string): Promise<{
    agent_name: string;
    description: string;
    persona_gender: string;
    stt_language: string;
    tts_language: string;
    interruption_sensitivity: string;
    global_prompt: string;
    workflow: WorkflowGraphDef;
    functions: CustomFunction[];
  }> {
    return request('/api/bots/generate-workflow', { method: 'POST', body: JSON.stringify({ description }) }, 45000);
  },
  // Workflow tab's "Refine with AI" — edits an existing bot's graph in place per a free-text
  // instruction. No bot_id needed: the graph to edit travels in the body (matches generatePrompt).
  refineWorkflow(
    instruction: string,
    currentWorkflow: WorkflowGraphDef,
    currentFunctions: CustomFunction[],
    currentGlobalPrompt: string,
  ): Promise<{ global_prompt: string; workflow: WorkflowGraphDef; functions: CustomFunction[] }> {
    return request(
      '/api/bots/generate-workflow',
      {
        method: 'POST',
        body: JSON.stringify({
          description: instruction,
          mode: 'refine',
          current_workflow: currentWorkflow,
          current_functions: currentFunctions,
          current_global_prompt: currentGlobalPrompt,
        }),
      },
      45000,
    );
  },

  // platform (dev/prod) settings
  platformSettings(): Promise<PlatformSettings> {
    return request('/api/settings/platform');
  },
  updatePlatformSettings(payload: PlatformSettings): Promise<PlatformSettings> {
    return request('/api/settings/platform', { method: 'PUT', body: JSON.stringify(payload) });
  },

  // runtime (LiveKit) settings
  runtimeSettings(): Promise<RuntimeSettings> {
    return request('/api/settings/runtime');
  },
  updateRuntimeSettings(payload: Partial<RuntimeSettings>): Promise<RuntimeSettings> {
    return request('/api/settings/runtime', { method: 'PUT', body: JSON.stringify(payload) });
  },

  // langfuse settings
  langfuseSettings(): Promise<LangfuseSettings> {
    return request('/api/settings/langfuse');
  },
  updateLangfuseSettings(payload: Partial<LangfuseSettings>): Promise<LangfuseSettings> {
    return request('/api/settings/langfuse', { method: 'PUT', body: JSON.stringify(payload) });
  },

  // campaigns
  campaigns(): Promise<Campaign[]> {
    return request('/api/campaigns');
  },
  saveCampaignStrategy(key: string, name: string, strategy: DialingStrategy): Promise<Campaign> {
    return request(`/api/campaigns/${key}/strategy`, { method: 'PUT', body: JSON.stringify({ name, strategy }) });
  },
  assignCampaignBot(key: string, botId: string): Promise<Campaign> {
    return request(`/api/campaigns/${key}/bot`, { method: 'PUT', body: JSON.stringify({ bot_id: botId }) });
  },
  saveDialerConfig(key: string, dialerConfig: DialerConfig): Promise<Campaign> {
    return request(`/api/campaigns/${key}/dialer-config`, { method: 'PUT', body: JSON.stringify({ dialer_config: dialerConfig }) });
  },
  setCampaignStatus(key: string, status: string): Promise<Campaign> {
    return request(`/api/campaigns/${key}/status`, { method: 'PUT', body: JSON.stringify({ status }) });
  },
  deleteCampaign(key: string): Promise<{ ok: boolean }> {
    return request(`/api/campaigns/${key}`, { method: 'DELETE' });
  },
  uploadCampaignLeads(key: string, file: File): Promise<CampaignLeadUploadResult> {
    const formData = new FormData();
    formData.append('file', file);
    return requestForm(`/api/campaigns/${key}/leads`, formData);
  },
  getCampaignLeads(key: string, status?: CampaignLeadStatus): Promise<CampaignLead[]> {
    const qs = status ? `?status=${status}` : '';
    return request(`/api/campaigns/${key}/leads${qs}`);
  },
  startCampaign(key: string): Promise<CampaignProgress & { enqueued: number }> {
    return request(`/api/campaigns/${key}/start`, { method: 'POST' });
  },
  scheduleCampaign(
    key: string,
    schedule: { send_now: true } | { send_now: false; scheduled_at: string },
  ): Promise<CampaignProgress & { enqueued: number }> {
    return request(`/api/campaigns/${key}/schedule`, { method: 'POST', body: JSON.stringify(schedule) });
  },
  // GET /leads-template.csv requires auth (Depends(require_user)) — a plain <a href> can't
  // carry the bearer token, so fetch it with the token and trigger the download via a blob.
  async downloadLeadsTemplate(): Promise<void> {
    const token = getToken();
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(apiUrl('/api/campaigns/leads-template.csv'), { headers });
    if (!response.ok) throw new ApiError(response.status, await response.text().catch(() => ''));
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'leads-template.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
  // Same auth-carrying blob-download pattern as downloadLeadsTemplate above — combines
  // upload rejections, TSPL push failures, and completed-call outcomes into one CSV.
  async downloadCampaignResults(key: string): Promise<void> {
    const token = getToken();
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(apiUrl(`/api/campaigns/${key}/leads.csv`), { headers });
    if (!response.ok) throw new ApiError(response.status, await response.text().catch(() => ''));
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${key}-results.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
  getCampaignProgress(key: string): Promise<CampaignProgress> {
    return request(`/api/campaigns/${key}/progress`);
  },
  getCallDetail(key: string, callId: string): Promise<CallDetail> {
    return request(`/api/campaigns/${key}/calls/${callId}`);
  },
  getCampaignOutcomes(key: string): Promise<CampaignOutcomes> {
    return request(`/api/campaigns/${key}/outcomes`);
  },
  saveCampaignPromptTemplate(key: string, promptTemplate: string): Promise<Campaign> {
    return request(`/api/campaigns/${key}/prompt`, {
      method: 'PUT',
      body: JSON.stringify({ prompt_template: promptTemplate }),
    });
  },
  validateCampaignPromptTemplate(key: string, promptTemplate: string): Promise<{ unknown_vars: string[] }> {
    return request(`/api/campaigns/${key}/prompt/validate`, {
      method: 'POST',
      body: JSON.stringify({ prompt_template: promptTemplate }),
    });
  },

  pricingMatrix(): Promise<{ stt: Record<string, number>; llm: Record<string, number>; tts: Record<string, number>; telephony: Record<string, number> }> {
    return request('/api/pricing/matrix');
  },
  pricingTiers(): Promise<PricingTier[]> {
    return request('/api/pricing/tiers');
  },
  pricingBudgetRoute(maxInrPerMin: number): Promise<{ stt: string; llm: string; tts: string; telephony: string; estimated_cost_per_min: number }> {
    return request(`/api/pricing/budget-route?max_inr_per_min=${maxInrPerMin}`);
  },
  getPricingAdminConfig(): Promise<PricingConfig> {
    return request('/api/pricing/admin-config');
  },
  updatePricingAdminConfig(config: PricingConfig): Promise<PricingConfig> {
    return request('/api/pricing/admin-config', { method: 'PUT', body: JSON.stringify(config) });
  },

  // phone numbers
  phoneNumbers(): Promise<PhoneNumber[]> {
    return request('/api/phone-numbers');
  },
  createPhoneNumber(payload: {
    number: string;
    environment: PhoneNumberEnvironment;
    assigned_bot_id?: string;
    service_id?: string;
    aod_ports?: number;
    name?: string;
    ip?: string;
    sip_trunk?: string;
    sip_username?: string;
    sip_password?: string;
  }): Promise<PhoneNumber> {
    return request('/api/phone-numbers', { method: 'POST', body: JSON.stringify(payload) });
  },
  reassignPhoneNumber(id: string, botId: string): Promise<PhoneNumber> {
    return request(`/api/phone-numbers/${id}/reassign`, { method: 'PUT', body: JSON.stringify({ bot_id: botId }) });
  },
  updatePhoneNumber(id: string, payload: {
    status?: string;
    service_id?: string;
    aod_ports?: number;
    name?: string;
    ip?: string;
    sip_trunk?: string;
    sip_username?: string;
    sip_password?: string;
  }): Promise<PhoneNumber> {
    return request(`/api/phone-numbers/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
  },
  deletePhoneNumber(id: string): Promise<{ ok: boolean }> {
    return request(`/api/phone-numbers/${id}`, { method: 'DELETE' });
  },

  // number → agent mapping
  numberMapping(): Promise<NumberMapping[]> {
    return request('/api/number-mapping');
  },
  mapNumberToAgent(botId: string, phoneNumber: string | null): Promise<{ bot_id: string; phone_number: string | null }> {
    return request(`/api/number-mapping/agent/${botId}`, { method: 'PUT', body: JSON.stringify({ phone_number: phoneNumber }) });
  },

  // library — phrases, outcomes, language settings
  phrases(): Promise<LibraryPhrase[]> {
    return request('/api/library/phrases');
  },
  createPhrase(payload: Partial<LibraryPhrase>): Promise<LibraryPhrase> {
    return request('/api/library/phrases', { method: 'POST', body: JSON.stringify(payload) });
  },
  updatePhrase(id: string, payload: Partial<LibraryPhrase>): Promise<LibraryPhrase> {
    return request(`/api/library/phrases/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
  },
  deletePhrase(id: string): Promise<{ ok: boolean }> {
    return request(`/api/library/phrases/${id}`, { method: 'DELETE' });
  },
  outcomes(): Promise<OutcomeEntry[]> {
    return request('/api/library/outcomes');
  },
  updateOutcome(key: string, payload: Partial<OutcomeEntry>): Promise<OutcomeEntry> {
    return request(`/api/library/outcomes/${encodeURIComponent(key)}`, { method: 'PUT', body: JSON.stringify(payload) });
  },
  languageSettings(): Promise<LanguageSettings[]> {
    return request('/api/library/languages');
  },
  upsertLanguageSettings(payload: Partial<LanguageSettings>): Promise<LanguageSettings> {
    return request('/api/library/languages', { method: 'PUT', body: JSON.stringify(payload) });
  },
  deleteLanguageSetting(id: string): Promise<{ ok: boolean }> {
    return request(`/api/library/languages/${encodeURIComponent(id)}`, { method: 'DELETE' });
  },
  analysisPrompts(): Promise<AnalysisPromptEntry[]> {
    return request('/api/library/analysis-prompts');
  },
  updateAnalysisPrompt(key: AnalysisPromptKey, promptTemplate: string): Promise<AnalysisPromptEntry> {
    return request(`/api/library/analysis-prompts/${encodeURIComponent(key)}`, {
      method: 'PUT',
      body: JSON.stringify({ prompt_template: promptTemplate }),
    });
  },

  // alerts — custom threshold rules + fired incidents (Part 1: Custom Alerting)
  listAlertRules(): Promise<AlertRule[]> {
    return request('/api/alerts/rules');
  },
  createAlertRule(payload: AlertRuleInput): Promise<AlertRule> {
    return request('/api/alerts/rules', { method: 'POST', body: JSON.stringify(payload) });
  },
  updateAlertRule(id: string, payload: AlertRuleInput): Promise<AlertRule> {
    return request(`/api/alerts/rules/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
  },
  deleteAlertRule(id: string): Promise<{ ok: boolean }> {
    return request(`/api/alerts/rules/${id}`, { method: 'DELETE' });
  },
  listAlertIncidents(): Promise<AlertIncident[]> {
    return request('/api/alerts/incidents');
  },

  // audit log
  auditLog(params: { resource_type?: string; action?: string; actor?: string; limit?: number; offset?: number } = {}): Promise<{ items: AuditLogEntry[]; total: number }> {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)]));
    return request(`/api/audit-log?${qs.toString()}`);
  },

  // bot-config fallback events — every call that ran on the hardcoded default assistant
  // instead of the dashboard-configured bot, with why (see bot.py's record_fallback_event)
  fallbackEvents(params: { limit?: number; offset?: number } = {}): Promise<{ items: FallbackEvent[]; total: number }> {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]));
    return request(`/api/diagnostics/fallback-events?${qs.toString()}`);
  },
  workerHealth(): Promise<WorkerHealth[]> {
    return request('/api/diagnostics/worker-health');
  },
  dispatchFailures(params: { limit?: number; offset?: number } = {}): Promise<{ items: DispatchFailure[]; total: number }> {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]));
    return request(`/api/diagnostics/dispatch-failures?${qs.toString()}`);
  },

  // transcripts
  transcripts(params: { bot_id?: string; campaign_id?: string; status?: string; text?: string; source?: string; before?: string; limit?: number } = {}): Promise<Transcript[]> {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '') as [string, string][]);
    return request(`/api/transcripts?${qs.toString()}`);
  },
  transcriptDetail(transcriptId: string): Promise<Transcript> {
    // Full document (transcript turns, muted_transcript, analysis) — the list endpoint
    // above omits those to keep list payload light; fetched here once a row is selected.
    return request(`/api/transcripts/${transcriptId}`);
  },
  callEvents(transcriptId: string): Promise<CallEvent[]> {
    return request(`/api/transcripts/${transcriptId}/events`);
  },
  testRecordingLookup(callId: string): Promise<TestRecordingLookup> {
    return request(`/api/transcripts/recording-lookup/${callId}`);
  },
  testRecordingByRoom(roomName: string): Promise<TestRecordingLookup> {
    return request(`/api/transcripts/recordings/by-room/${roomName}`);
  },
  uploadTestRecording(roomName: string, blob: Blob): Promise<{ room_name: string; recording_url: string; transcripts_updated: boolean }> {
    // Longer timeout than the default 8s: the backend retries attaching the
    // recording to the transcript doc for up to ~8s to cover the race with the
    // bot worker's own save, on top of the upload itself.
    return request(`/api/transcripts/recordings/${roomName}`, {
      method: 'POST',
      headers: { 'Content-Type': blob.type || 'audio/webm' },
      body: blob,
    }, 15000);
  },
  async exportTranscriptsCsv(params: { bot_id?: string; campaign_id?: string; status?: string; outcome?: string; start_date?: string; end_date?: string; text?: string; source?: string }): Promise<Blob> {
    // A plain <a href> to this endpoint sends no Authorization header — the backend
    // requires one (Depends(require_user)), so that always 401ed. Fetch it ourselves
    // with the header and hand the caller a Blob to trigger the download from.
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '') as [string, string][]);
    const res = await fetch(apiUrl(`/api/transcripts/export.csv?${qs.toString()}`), {
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new ApiError(res.status, await res.text().catch(() => res.statusText));
    return res.blob();
  },

  // analytics
  outcomeAnalytics(params: { bot_id?: string; campaign_id?: string; hours?: number; start_date?: string; end_date?: string } = {}): Promise<OutcomeAnalytics> {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '') as [string, string][]);
    return request(`/api/analytics/outcomes?${qs.toString()}`);
  },
  qualityAlerts(hours: number, thresholdPct: number): Promise<QualityAlert> {
    return request(`/api/analytics/quality-alerts?hours=${hours}&threshold_pct=${thresholdPct}`);
  },

  // test-call room orchestration (LiveKit)
  startTestCall(payload: {
    bot_id: string;
    test_bot_version_id?: string;
    campaign_id?: string;
    lead_id?: string;
    call_id?: string;
    mobile?: string;
    srchterm?: string;
    buyer_name?: string;
    city?: string;
    test_worker_agent_name?: string;
    custom_lead_json?: string;
    pre_call_params?: Record<string, string>;
    dynamic_variables?: Record<string, string>;
  }): Promise<{ room_name: string; livekit_token: string; livekit_url: string }> {
    return request('/api/testcall/start', { method: 'POST', body: JSON.stringify(payload) });
  },
  stopTestCall(roomName: string): Promise<{ ok: boolean }> {
    return request('/api/testcall/stop', { method: 'POST', body: JSON.stringify({ room_name: roomName }) });
  },
};
