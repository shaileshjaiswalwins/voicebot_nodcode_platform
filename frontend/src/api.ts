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
    throw new Error(await response.text());
  }
  return response.json();
}

export type Bot = {
  _id: string;
  name: string;
  assistant_id: string;
  status: string;
  owner?: string;
  active_version_id?: string;
  updated_at?: string;
};

export type BotVersion = {
  _id: string;
  version: number;
  state: string;
  config: Record<string, unknown>;
  published_at?: string;
};

export type Transcript = {
  _id: string;
  call_id?: string;
  lead_id?: string;
  bot_id?: string;
  campaign_id?: string;
  status?: string;
  call_duration_sec?: number;
  transcript?: { role: string; text: string }[];
  created_at?: string;
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
  transcripts: () => request<Transcript[]>('/api/transcripts'),
  campaigns: () => request<unknown[]>('/api/campaigns'),
  voices: () => request<unknown[]>('/api/options/voices'),
  languages: () => request<unknown[]>('/api/options/languages')
};
