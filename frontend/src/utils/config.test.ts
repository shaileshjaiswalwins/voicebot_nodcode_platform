import { describe, it, expect } from 'vitest';
import { parseConfig, deriveAgentState } from './config';

describe('parseConfig', () => {
  it('parses valid JSON into an ok result', () => {
    const result = parseConfig('{"agent_name": "Priya"}');
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.value.agent_name).toBe('Priya');
  });

  it('returns an error result for invalid JSON', () => {
    const result = parseConfig('{not valid json');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBeTruthy();
  });
});

describe('deriveAgentState', () => {
  it('returns "idle" when there is no room regardless of status', () => {
    expect(deriveAgentState('speaking', true, false)).toBe('idle');
  });

  it('returns "idle" for an explicit idle status', () => {
    expect(deriveAgentState('idle', false, true)).toBe('idle');
  });

  it('returns "idle" for any status containing "disconnected"', () => {
    expect(deriveAgentState('Disconnected: room closed', false, true)).toBe('idle');
  });

  it('returns "connecting" for creating/connecting/requesting/reconnecting statuses', () => {
    expect(deriveAgentState('Creating room', false, true)).toBe('connecting');
    expect(deriveAgentState('Connecting to LiveKit', false, true)).toBe('connecting');
    expect(deriveAgentState('Requesting mic', false, true)).toBe('connecting');
    expect(deriveAgentState('Reconnecting', false, true)).toBe('connecting');
  });

  it('returns "speaking" when remoteAudioReady is true even if the status text does not say so', () => {
    expect(deriveAgentState('participant joined', true, true)).toBe('speaking');
  });

  it('returns "speaking" for a status containing "speaking" or "bot audio connected"', () => {
    expect(deriveAgentState('Bot is speaking', false, true)).toBe('speaking');
    expect(deriveAgentState('Bot audio connected', false, true)).toBe('speaking');
  });

  it('returns "thinking" for thinking/waiting-for-bot/participant-joined statuses (checked before default)', () => {
    expect(deriveAgentState('Thinking...', false, true)).toBe('thinking');
    expect(deriveAgentState('Waiting for bot', false, true)).toBe('thinking');
    expect(deriveAgentState('Participant joined', false, true)).toBe('thinking');
  });

  it('falls back to "listening" for an unrecognized in-room status', () => {
    expect(deriveAgentState('some unknown state', false, true)).toBe('listening');
  });

  it('is case-insensitive via lowercasing the status', () => {
    expect(deriveAgentState('CONNECTING', false, true)).toBe('connecting');
  });

  it('prioritizes the connecting branch over thinking when both substrings could apply', () => {
    // "Reconnecting" also isn't a thinking match, but this documents branch order/precedence
    // for statuses that could plausibly match multiple checks.
    expect(deriveAgentState('reconnecting after waiting for bot', false, true)).toBe('connecting');
  });
});
