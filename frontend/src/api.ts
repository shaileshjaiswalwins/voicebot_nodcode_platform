const API_BASE = import.meta.env.VITE_API_BASE || '';

function withQS(path: string, qs: URLSearchParams): string {
  const q = qs.toString();
  return q ? `${path}?${q}` : path;
}
const TEST_RECORDING_API_BASE = import.meta.env.VITE_TEST_RECORDING_API_BASE
  || (import.meta.env.DEV ? `${window.location.protocol}//${window.location.hostname}:8000` : API_BASE);
const READ_TIMEOUT_MS = 8000;
const WRITE_TIMEOUT_MS = 20000;

async function request<T>(path: string, options: RequestInit = {}, baseUrl = API_BASE): Promise<T> {
  const method = (options.method || 'GET').toUpperCase();
  const timeoutMs = method === 'GET' ? READ_TIMEOUT_MS : WRITE_TIMEOUT_MS;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        'X-JD-User': 'local-dev',
        ...(options.headers || {})
      }
    });
    if (!response.ok) {
      const text = await response.text();
      let message = text || `${response.status} ${response.statusText}`;
      try {
        const parsed = JSON.parse(text) as { detail?: string };
        message = parsed.detail || message;
      } catch {
        // Keep the plain response text when the backend does not return JSON.
      }
      throw new Error(message);
    }
    return response.json();
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(`Network timeout after ${timeoutMs / 1000}s for ${path}`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function apiUrl(path?: string) {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  if (path.startsWith('/api/test-recordings/')) return `${TEST_RECORDING_API_BASE}${path}`;
  return `${API_BASE}${path}`;
}

async function uploadBlob<T>(path: string, blob: Blob, baseUrl = API_BASE): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), WRITE_TIMEOUT_MS);
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      method: 'POST',
      body: blob,
      signal: controller.signal,
      headers: {
        'Content-Type': blob.type || 'audio/webm',
        'X-JD-User': 'local-dev'
      }
    });
    if (!response.ok) {
      const text = await response.text();
      let message = text || `${response.status} ${response.statusText}`;
      try {
        const parsed = JSON.parse(text) as { detail?: string };
        message = parsed.detail || message;
      } catch {
        // Keep the plain response text when the backend does not return JSON.
      }
      throw new Error(message);
    }
    return response.json();
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(`Network timeout after ${WRITE_TIMEOUT_MS / 1000}s for ${path}`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

export type Bot = {
  _id: string;
  name: string;
  description?: string;
  assistant_id: string;
  status: string;
  owner?: string;
  active_version_id?: string;
  draft_version_id?: string;
  orchestration?: {
    mode?: string;
    flow_provider?: string | null;
    flow_id?: string | null;
  };
  updated_at?: string;
  created_at?: string;
};

export type BotVersion = {
  _id: string;
  version: number;
  state: string;
  config: Record<string, unknown>;
  notes?: string;
  published_at?: string;
  created_at?: string;
  created_by?: string;
  published_by?: string;
};

export type TranscriptTurn = {
  role: string;
  text: string;
  created_at?: string;
  interrupted?: boolean;
  event_type?: string;
};

export type Transcript = {
  _id: string;
  call_id?: string;
  room_name?: string;
  lead_id?: string;
  bot_id?: string;
  bot_version_id?: string;
  assistant_id?: string;
  campaign_id?: string;
  status?: string;
  call_duration_sec?: number;
  transcript?: TranscriptTurn[];
  live_transcript?: TranscriptTurn[];
  verified_transcript?: TranscriptTurn[];
  verified_transcript_status?: 'pending' | 'succeeded' | 'failed' | 'unavailable';
  verified_transcript_error?: string;
  analysis_transcript_source?: string;
  transcript_source?: string;
  collection_source?: string;
  transcript_quality_flags?: string[];
  latency_metrics?: Record<string, number | string>;
  transcript_count?: number;
  config_snapshot?: Record<string, unknown>;
  lead_record?: Record<string, unknown>;
  tags?: string[];
  ended_naturally?: boolean;
  callback_status?: string;
  analysis_result?: Record<string, unknown>;
  recording_url?: string;
  recording_source?: string;
  recording_saved_at?: string;
  created_at?: string;
  updated_at?: string;
};

export type TestRecordingLookup = {
  room_name: string;
  recording_url: string;
  recording_path?: string;
  recording_source?: string;
};

export type CallEvent = {
  _id: string;
  call_id?: string;
  room_name?: string;
  assistant_id?: string;
  bot_id?: string;
  bot_version_id?: string;
  campaign_id?: string;
  lead_id?: string;
  event_type: string;
  severity: 'info' | 'warning' | 'error' | 'success';
  message: string;
  details?: Record<string, unknown>;
  created_at?: string;
};

export type OutcomeRule = {
  outcome: string;
  action: 'retry' | 'dnc' | 'stop' | 'completed';
  max_attempts?: number;
  retry_after_min?: number;
  language_override?: string;
  bot_id_override?: string;
};

export type CallWindow = {
  days: string[];
  start_time: string;
  end_time: string;
  timezone: string;
};

export type AttemptStep = {
  attempt: number;
  language?: string;
  bot_id?: string;
  delay_min?: number;
};

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
  lead_api?: Record<string, unknown>;
  dialing_strategy?: DialingStrategy;
  updated_at?: string;
};

export type VoiceOption = {
  id: string;
  label: string;
  provider?: string;
  gender?: string;
};

export type LanguageOption = {
  id: string;
  label: string;
  livekit_code?: string;
  sarvam_code?: string;
};

export type WebRtcTestSession = {
  room_name: string;
  livekit_url: string;
  token: string;
  metadata: Record<string, unknown>;
  agent_name: string;
  expires_in_sec: number;
  next_steps: string[];
};

export type LangfuseSettings = {
  _id?: string;
  key?: string;
  enabled: boolean;
  environment: 'local' | 'staging' | 'prod';
  base_url?: string;
  credentials_configured: boolean;
  send_transcripts: boolean;
  send_prompts: boolean;
  updated_by?: string;
  updated_at?: string;
  runtime_status?: {
    enabled: boolean;
    credentials_configured: boolean;
    base_url?: string;
    environment: string;
    send_transcripts: boolean;
    send_prompts: boolean;
    last_error?: string | null;
  };
};

export type RuntimeSettings = {
  _id?: string;
  key?: string;
  livekit_api_url: string;
  livekit_browser_url: string;
  livekit_agent_name: string;
  livekit_credentials_configured: boolean;
  updated_by?: string;
  updated_at?: string;
};

export type PhraseCategory = 'voicemail' | 'hold_music' | 'dnc_trigger';

export type LibraryPhrase = {
  _id: string;
  category: PhraseCategory;
  text: string;
  language?: string;
  notes?: string;
  created_by?: string;
  created_at?: string;
  updated_by?: string;
  updated_at?: string;
};

export type OutcomeEntry = {
  _id?: string;
  key: string;
  description: string;
  display_label?: string;
  order?: number;
  updated_by?: string;
  updated_at?: string;
};

export type OutcomeAnalytics = {
  total: number;
  by_status: Record<string, number>;
  by_outcome: Record<string, number>;
  ended_naturally: number;
  avg_duration_sec: number;
};

export type QualityAlert = {
  hours: number;
  total_calls: number;
  bad_calls: number;
  bad_pct: number;
  threshold_pct: number;
  alert: boolean;
  message: string | null;
};

export type LanguageSettings = {
  _id?: string;
  id: string;
  name: string;
  timeout_message?: string;
  inactivity_nudge?: string;
  lang_notes?: string;
  updated_by?: string;
  updated_at?: string;
};

export const api = {
  bots: () => request<Bot[]>('/api/bots'),
  bot: (id: string) => request<{ bot: Bot; versions: BotVersion[] }>(`/api/bots/${id}`),
  createBot: (payload: unknown) =>
    request<Bot>('/api/bots', { method: 'POST', body: JSON.stringify(payload) }),
  deleteBot: (botId: string) =>
    request<{ status: string; bot_id: string }>(`/api/bots/${botId}`, { method: 'DELETE' }),
  updateBotMeta: (botId: string, payload: { name?: string; description?: string }) =>
    request<Bot>(`/api/bots/${botId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  saveDraft: (botId: string, payload: unknown) =>
    request<BotVersion>(`/api/bots/${botId}/draft`, { method: 'POST', body: JSON.stringify(payload) }),
  updateVersion: (botId: string, versionId: string, payload: unknown) =>
    request<BotVersion>(`/api/bots/${botId}/versions/${encodeURIComponent(versionId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload)
    }),
  publish: (botId: string, versionId?: string) =>
    request<BotVersion>(`/api/bots/${botId}/publish`, {
      method: 'POST',
      body: JSON.stringify({ version_id: versionId })
    }),
  unpublish: (botId: string) =>
    request<BotVersion>(`/api/bots/${botId}/unpublish`, { method: 'POST' }),
  rollbackVersion: (botId: string, versionId: string) =>
    request<{ bot: Bot; active_version: BotVersion }>(`/api/bots/${botId}/rollback`, {
      method: 'POST',
      body: JSON.stringify({ version_id: versionId })
    }),
  createWebRtcTestSession: (botId: string, payload: unknown) =>
    request<WebRtcTestSession>(`/api/bots/${botId}/webrtc-test-session`, {
      method: 'POST',
      body: JSON.stringify(payload)
    }),
  closeWebRtcTestSession: (roomName: string) =>
    request<{ room_name: string; status: string }>(
      `/api/webrtc-test-sessions/${encodeURIComponent(roomName)}/close`,
      { method: 'POST' }
    ),
  uploadTestRecording: (roomName: string, blob: Blob) =>
    uploadBlob<{
      room_name: string;
      recording_url: string;
      recording_path: string;
      transcripts_updated: number;
    }>(`/api/test-recordings/${encodeURIComponent(roomName)}`, blob, TEST_RECORDING_API_BASE),
  testRecording: (roomName: string) =>
    request<TestRecordingLookup>(
      `/api/test-recordings/by-room/${encodeURIComponent(roomName)}`,
      {},
      TEST_RECORDING_API_BASE
    ),
  langfuseSettings: () => request<LangfuseSettings>('/api/observability/langfuse'),
  updateLangfuseSettings: (payload: Partial<LangfuseSettings>) =>
    request<LangfuseSettings>('/api/observability/langfuse', {
      method: 'PUT',
      body: JSON.stringify(payload)
    }),
  runtimeSettings: () => request<RuntimeSettings>('/api/settings/runtime'),
  updateRuntimeSettings: (payload: Partial<RuntimeSettings>) =>
    request<RuntimeSettings>('/api/settings/runtime', {
      method: 'PUT',
      body: JSON.stringify(payload)
    }),
  transcripts: () => request<Transcript[]>('/api/transcripts'),
  transcript: (transcriptId: string) =>
    request<Transcript>(`/api/transcripts/${encodeURIComponent(transcriptId)}`),
  callEvents: (query = '') => request<CallEvent[]>(`/api/call-events${query}`),
  transcriptEvents: (transcriptId: string) =>
    request<CallEvent[]>(`/api/transcripts/${encodeURIComponent(transcriptId)}/events`),
  campaigns: () => request<Campaign[]>('/api/campaigns'),
  getCampaign: (campaignKey: string) => request<Campaign>(`/api/campaigns/${encodeURIComponent(campaignKey)}`),
  upsertCampaign: (payload: Partial<Campaign> & { dialing_strategy?: DialingStrategy }) =>
    request<Campaign>('/api/campaigns', { method: 'POST', body: JSON.stringify(payload) }),
  setCampaignStatus: (campaignKey: string, status: string) =>
    request<Campaign>(`/api/campaigns/${encodeURIComponent(campaignKey)}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status })
    }),
  outcomeAnalytics: (params?: { bot_id?: string; campaign_id?: string; hours?: number; start_date?: string; end_date?: string }) => {
    const qs = new URLSearchParams();
    if (params?.bot_id) qs.set('bot_id', params.bot_id);
    if (params?.campaign_id) qs.set('campaign_id', params.campaign_id);
    if (params?.hours != null) qs.set('hours', String(params.hours));
    if (params?.start_date) qs.set('start_date', params.start_date);
    if (params?.end_date) qs.set('end_date', params.end_date);
    return request<OutcomeAnalytics>(withQS('/api/analytics/outcomes', qs));
  },
  qualityAlerts: (hours = 1, threshold = 30) =>
    request<QualityAlert>(`/api/analytics/quality-alerts?hours=${hours}&threshold_pct=${threshold}`),
  transcriptsFiltered: (params: {
    bot_id?: string; campaign_id?: string; status?: string; outcome?: string;
    start_date?: string; end_date?: string; text?: string; limit?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params.bot_id) qs.set('bot_id', params.bot_id);
    if (params.campaign_id) qs.set('campaign_id', params.campaign_id);
    if (params.status) qs.set('status', params.status);
    if (params.outcome) qs.set('outcome', params.outcome);
    if (params.start_date) qs.set('start_date', params.start_date);
    if (params.end_date) qs.set('end_date', params.end_date);
    if (params.text) qs.set('text', params.text);
    if (params.limit) qs.set('limit', String(params.limit));
    return request<Transcript[]>(withQS('/api/transcripts', qs));
  },
  exportCsvUrl: (params: {
    bot_id?: string; bot_version_id?: string; campaign_id?: string;
    status?: string; outcome?: string; start_date?: string; end_date?: string; text?: string;
  }) => {
    const base = (import.meta.env.VITE_API_BASE || '');
    const qs = new URLSearchParams();
    if (params.bot_id) qs.set('bot_id', params.bot_id);
    if (params.bot_version_id) qs.set('bot_version_id', params.bot_version_id);
    if (params.campaign_id) qs.set('campaign_id', params.campaign_id);
    if (params.status) qs.set('status', params.status);
    if (params.outcome) qs.set('outcome', params.outcome);
    if (params.start_date) qs.set('start_date', params.start_date);
    if (params.end_date) qs.set('end_date', params.end_date);
    if (params.text) qs.set('text', params.text);
    return withQS(`${base}/api/transcripts/export.csv`, qs);
  },
  voices: () => request<VoiceOption[]>('/api/options/voices'),
  languages: () => request<LanguageOption[]>('/api/options/languages'),
  phrases: (category?: PhraseCategory) =>
    request<LibraryPhrase[]>(`/api/library/phrases${category ? `?category=${category}` : ''}`),
  createPhrase: (payload: Partial<LibraryPhrase>) =>
    request<LibraryPhrase>('/api/library/phrases', { method: 'POST', body: JSON.stringify(payload) }),
  updatePhrase: (id: string, payload: Partial<LibraryPhrase>) =>
    request<LibraryPhrase>(`/api/library/phrases/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deletePhrase: (id: string) =>
    request<{ status: string }>(`/api/library/phrases/${id}`, { method: 'DELETE' }),
  outcomes: () => request<OutcomeEntry[]>('/api/library/outcomes'),
  updateOutcome: (key: string, payload: Partial<OutcomeEntry>) =>
    request<OutcomeEntry>(`/api/library/outcomes/${encodeURIComponent(key)}`, {
      method: 'PUT',
      body: JSON.stringify(payload)
    }),
  languageSettings: () => request<LanguageSettings[]>('/api/library/language-settings'),
  upsertLanguageSettings: (payload: Partial<LanguageSettings>) =>
    request<LanguageSettings>('/api/library/language-settings', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
};
