import { describe, it, expect } from 'vitest';
import { friendlyApiError, friendlyTestError, summarizeDetails, average, buildDiagnostic } from './errors';

describe('friendlyApiError', () => {
  it('passes through an unrecognized error message unchanged', () => {
    expect(friendlyApiError(new Error('boom'))).toBe('boom');
  });

  it('stringifies a non-Error value', () => {
    expect(friendlyApiError('plain string')).toBe('plain string');
  });

  it('adds guidance for a "network timeout" message', () => {
    const msg = friendlyApiError(new Error('network timeout after 8000ms'));
    expect(msg).toContain('Mongo/LiveKit connectivity');
  });

  it('adds guidance for a "failed to fetch" message', () => {
    const msg = friendlyApiError(new Error('Failed to fetch'));
    expect(msg).toContain('start_api.sh');
  });

  it('prioritizes "network timeout" over "failed to fetch" when both substrings are present', () => {
    const msg = friendlyApiError(new Error('network timeout: failed to fetch'));
    expect(msg).toContain('Mongo/LiveKit connectivity');
    expect(msg).not.toContain('start_api.sh');
  });
});

describe('friendlyTestError', () => {
  it('uses the test-context network timeout message', () => {
    const msg = friendlyTestError(new Error('network timeout'));
    expect(msg).toContain('backend logs before retrying');
  });

  it('uses the test-context failed-to-fetch message with the API base', () => {
    const msg = friendlyTestError(new Error('failed to fetch'));
    expect(msg).toContain('port 8010');
  });

  it('flags missing LiveKit configuration', () => {
    const msg = friendlyTestError(new Error('LiveKit is not configured on backend'));
    expect(msg).toContain('LIVEKIT_URL');
  });

  it('flags LiveKit room creation failure', () => {
    const msg = friendlyTestError(new Error('Could not create LiveKit test room'));
    expect(msg).toContain('LiveKit server');
  });

  it('flags microphone permission errors case-insensitively', () => {
    expect(friendlyTestError(new Error('Permission denied'))).toContain('microphone');
    expect(friendlyTestError(new Error('microphone not found'))).toContain('microphone');
  });

  it('flags websocket/network errors distinct from the generic network-timeout branch', () => {
    const msg = friendlyTestError(new Error('websocket closed unexpectedly'));
    expect(msg).toContain('ws/wss');
  });

  it('falls back to the raw message when nothing matches', () => {
    expect(friendlyTestError(new Error('totally unrelated error'))).toBe('totally unrelated error');
  });

  it('falls back to a generic message for an empty error', () => {
    expect(friendlyTestError(new Error(''))).toBe('Unknown test call error. Check backend logs and LiveKit server status.');
  });
});

describe('summarizeDetails', () => {
  it('joins up to 4 entries as key: value pairs', () => {
    const out = summarizeDetails({ a: 1, b: 'x', c: true, d: 'y', e: 'ignored' });
    expect(out).toBe('a: 1 | b: x | c: true | d: y');
  });

  it('JSON-stringifies object values', () => {
    expect(summarizeDetails({ obj: { nested: 1 } })).toBe('obj: {"nested":1}');
  });

  it('returns an empty string for an empty object', () => {
    expect(summarizeDetails({})).toBe('');
  });
});

describe('average', () => {
  it('rounds the mean of a list of numbers', () => {
    expect(average([1, 2, 4])).toBe(2);
  });

  it('returns 0 for an empty list', () => {
    expect(average([])).toBe(0);
  });
});

describe('buildDiagnostic', () => {
  it('builds a diagnostic with default severity "error"', () => {
    const d = buildDiagnostic('scope1', new Error('failure'));
    expect(d.scope).toBe('scope1');
    expect(d.severity).toBe('error');
    expect(d.message).toBe('failure');
    expect(d.id).toContain('scope1-');
  });

  it('truncates messages longer than 220 characters with an ellipsis', () => {
    const longMsg = 'x'.repeat(300);
    const d = buildDiagnostic('scope2', new Error(longMsg));
    expect(d.message.length).toBe(223);
    expect(d.message.endsWith('...')).toBe(true);
  });

  it('accepts a custom severity and optional action', () => {
    const d = buildDiagnostic('scope3', new Error('warn'), 'Retry', 'warning');
    expect(d.severity).toBe('warning');
    expect(d.action).toBe('Retry');
  });
});
