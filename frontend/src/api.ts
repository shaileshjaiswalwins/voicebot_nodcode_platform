// Typed client for the backend/ FastAPI admin service (Phase 1a).
// Contract reconstructed from usage across frontend/src — see nocode_platform_alignment.md
// Phase 1a for the audit that produced this. Types intentionally match what every view
// already destructures; extend fields here rather than casting in views.

// Type-only import (erased at build) — safe despite types.ts importing from api.ts.
import type { CustomFunction, FunctionTestResult } from './types';

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

const READ_TIMEOUT_MS = 8000;

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), READ_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(apiUrl(path), { ...options, headers, signal: controller.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(0, `Request timed out after ${READ_TIMEOUT_MS}ms: ${path}`);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
  if (response.status === 401) {
    clearToken();
  }
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new ApiError(response.status, body || response.statusText);
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
    throw new ApiError(response.status, body || response.statusText);
  }
  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

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
  logout(): void {
    clearToken();
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
  runEvals(id: string, versionId?: string, scenarios?: EvalScenario[]): Promise<EvalRun> {
    return request(`/api/bots/${id}/evals/run`, {
      method: 'POST',
      body: JSON.stringify({ version_id: versionId || '', scenarios: scenarios || [] }),
    });
  },
  listEvals(id: string): Promise<EvalRun[]> {
    return request(`/api/bots/${id}/evals`);
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

  // audit log
  auditLog(params: { resource_type?: string; action?: string; actor?: string; limit?: number; offset?: number } = {}): Promise<{ items: AuditLogEntry[]; total: number }> {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)]));
    return request(`/api/audit-log?${qs.toString()}`);
  },

  // transcripts
  transcripts(params: { bot_id?: string; campaign_id?: string; status?: string; text?: string; limit?: number } = {}): Promise<Transcript[]> {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '') as [string, string][]);
    return request(`/api/transcripts?${qs.toString()}`);
  },
  callEvents(transcriptId: string): Promise<CallEvent[]> {
    return request(`/api/transcripts/${transcriptId}/events`);
  },
  testRecordingLookup(callId: string): Promise<TestRecordingLookup> {
    return request(`/api/transcripts/recording-lookup/${callId}`);
  },
  exportCsvUrl(params: { bot_id?: string; campaign_id?: string; status?: string; outcome?: string; start_date?: string; end_date?: string; text?: string }): string {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '') as [string, string][]);
    return apiUrl(`/api/transcripts/export.csv?${qs.toString()}`);
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
  }): Promise<{ room_name: string; livekit_token: string; livekit_url: string }> {
    return request('/api/testcall/start', { method: 'POST', body: JSON.stringify(payload) });
  },
  stopTestCall(roomName: string): Promise<{ ok: boolean }> {
    return request('/api/testcall/stop', { method: 'POST', body: JSON.stringify({ room_name: roomName }) });
  },
};
