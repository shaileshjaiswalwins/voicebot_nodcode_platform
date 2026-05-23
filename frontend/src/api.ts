const API_BASE = import.meta.env.VITE_API_BASE || '';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
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
};

export type Transcript = {
  _id: string;
  call_id?: string;
  lead_id?: string;
  bot_id?: string;
  bot_version_id?: string;
  assistant_id?: string;
  campaign_id?: string;
  status?: string;
  call_duration_sec?: number;
  transcript?: TranscriptTurn[];
  config_snapshot?: Record<string, unknown>;
  lead_record?: Record<string, unknown>;
  callback_status?: string;
  analysis_result?: Record<string, unknown>;
  recording_url?: string;
  created_at?: string;
  updated_at?: string;
};

export type Campaign = {
  _id: string;
  campaign_key: string;
  name: string;
  bot_id?: string;
  status?: string;
  lead_api?: Record<string, unknown>;
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

export const api = {
  bots: () => request<Bot[]>('/api/bots'),
  bot: (id: string) => request<{ bot: Bot; versions: BotVersion[] }>(`/api/bots/${id}`),
  createBot: (payload: unknown) =>
    request<Bot>('/api/bots', { method: 'POST', body: JSON.stringify(payload) }),
  saveDraft: (botId: string, payload: unknown) =>
    request<BotVersion>(`/api/bots/${botId}/draft`, { method: 'POST', body: JSON.stringify(payload) }),
  publish: (botId: string, versionId?: string) =>
    request<BotVersion>(`/api/bots/${botId}/publish`, {
      method: 'POST',
      body: JSON.stringify({ version_id: versionId })
    }),
  createWebRtcTestSession: (botId: string, payload: unknown) =>
    request<WebRtcTestSession>(`/api/bots/${botId}/webrtc-test-session`, {
      method: 'POST',
      body: JSON.stringify(payload)
    }),
  langfuseSettings: () => request<LangfuseSettings>('/api/observability/langfuse'),
  updateLangfuseSettings: (payload: Partial<LangfuseSettings>) =>
    request<LangfuseSettings>('/api/observability/langfuse', {
      method: 'PUT',
      body: JSON.stringify(payload)
    }),
  transcripts: () => request<Transcript[]>('/api/transcripts'),
  campaigns: () => request<Campaign[]>('/api/campaigns'),
  voices: () => request<VoiceOption[]>('/api/options/voices'),
  languages: () => request<LanguageOption[]>('/api/options/languages')
};
