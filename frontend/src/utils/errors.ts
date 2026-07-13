import type { Diagnostic, DiagnosticSeverity } from '../types';

function classifyNetworkError(raw: string, context: 'api' | 'test'): string | null {
  const lower = raw.toLowerCase();
  if (lower.includes('network timeout')) {
    return context === 'test'
      ? `${raw}. The backend is reachable but too slow; check Mongo/LiveKit connectivity and backend logs before retrying.`
      : `${raw}. The backend may be slow, down, or blocked by Mongo/LiveKit connectivity.`;
  }
  if (lower.includes('failed to fetch')) {
    return context === 'test'
      ? `Cannot reach the FastAPI backend at ${import.meta.env.VITE_API_BASE || 'the Vite /api proxy'}. Start the local API on port 8010 or open the SSH tunnel, then refresh and retry.`
      : 'Cannot reach the FastAPI backend. Start it with ./start_api.sh, then refresh this page.';
  }
  return null;
}

export function friendlyApiError(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error);
  return classifyNetworkError(raw, 'api') ?? raw ?? 'API request failed.';
}

export function friendlyTestError(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error);
  const networkMsg = classifyNetworkError(raw, 'test');
  if (networkMsg) return networkMsg;
  if (raw.includes('LiveKit is not configured')) {
    return 'LiveKit is not configured on the backend. Ask backend/infra to set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env, then restart ./start_api.sh.';
  }
  if (raw.includes('Could not create LiveKit test room')) {
    return 'Backend could not create the LiveKit room. Check LiveKit server URL, API credentials, and whether the LiveKit server is reachable from this machine.';
  }
  if (raw.toLowerCase().includes('permission') || raw.toLowerCase().includes('microphone')) {
    return 'Browser microphone access failed. Allow microphone permission, check the selected input device, then try again.';
  }
  if (raw.toLowerCase().includes('websocket') || raw.toLowerCase().includes('network')) {
    return 'Browser could not connect to LiveKit. Check LIVEKIT_URL is reachable from your browser and uses ws/wss correctly.';
  }
  return raw || 'Unknown test call error. Check backend logs and LiveKit server status.';
}

export function summarizeDetails(details: Record<string, unknown>) {
  return Object.entries(details)
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
    .join(' | ');
}

export function average(values: number[]) {
  if (!values.length) return 0;
  return Math.round(values.reduce((sum, item) => sum + item, 0) / values.length);
}

export function buildDiagnostic(scope: string, error: unknown, action?: string, severity: DiagnosticSeverity = 'error'): Diagnostic {
  const message = friendlyApiError(error);
  const normalized = message.length > 220 ? `${message.slice(0, 220)}...` : message;
  return {
    id: `${scope}-${Date.now()}`,
    scope,
    severity,
    message: normalized,
    action,
    createdAt: new Date().toISOString()
  };
}
