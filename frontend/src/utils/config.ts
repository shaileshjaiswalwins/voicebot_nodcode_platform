import type { RuntimeConfig, AgentUiState } from '../types';

export function parseConfig(value: string): { ok: true; value: RuntimeConfig } | { ok: false; error: string } {
  try {
    return { ok: true, value: JSON.parse(value) as RuntimeConfig };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

export function deriveAgentState(status: string, remoteAudioReady: boolean, hasRoom: boolean): AgentUiState {
  const value = status.toLowerCase();
  if (!hasRoom || value === 'idle' || value.includes('disconnected')) return 'idle';
  if (value.includes('creating') || value.includes('connecting') || value.includes('requesting') || value.includes('reconnecting')) return 'connecting';
  if (value.includes('speaking') || value.includes('bot audio connected') || remoteAudioReady) return 'speaking';
  if (value.includes('thinking') || value.includes('waiting for bot') || value.includes('participant joined')) return 'thinking';
  return 'listening';
}
