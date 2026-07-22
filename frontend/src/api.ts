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

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(apiUrl(path), { ...options, headers });
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
  description?: string;
  assistant_id?: string;
  status: BotStatus;
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

export type Campaign = {
  _id: string;
  campaign_key: string;
  name: string;
  bot_id?: string;
  status?: string;
  dialing_strategy?: DialingStrategy;
  lead_api?: { url?: string; endpoint?: string };
  prompt_template?: string;
};

export type CampaignLeadStatus = 'pending' | 'dialing' | 'completed' | 'failed';

export type CampaignLead = {
  _id: string;
  campaign_id: string;
  phone_number: string;
  name?: string;
  vars: Record<string, string>;
  status: CampaignLeadStatus;
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
  completed: number;
  failed: number;
  total: number;
};

export type CallDetail = {
  call_id: string;
  status: string | null;
  cost_inr: Record<string, number>;
  latency_ms: Record<string, number>;
  transcript: Array<{ role?: string; text?: string; [key: string]: unknown }>;
  call_duration_sec?: number | null;
  recording_url?: string;
  analysis?: Record<string, unknown>;
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

export type FlowNodeType = 'message' | 'condition' | 'tool_call' | 'transfer' | 'end';
export type FlowNode = {
  id: string;
  type: FlowNodeType;
  position: { x: number; y: number };
  data: Record<string, unknown>;
};
export type FlowEdge = { id: string; source: string; target: string; label?: string; condition?: string };
export type Flow = { nodes: FlowNode[]; edges: FlowEdge[] };

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
  getCampaignProgress(key: string): Promise<CampaignProgress> {
    return request(`/api/campaigns/${key}/progress`);
  },
  getCallDetail(key: string, callId: string): Promise<CallDetail> {
    return request(`/api/campaigns/${key}/calls/${callId}`);
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
