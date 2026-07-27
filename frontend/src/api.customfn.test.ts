import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import type { CustomFunction } from './types';

describe('api.testCustomFunction()', () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('POSTs the function config and args to the per-bot test endpoint', async () => {
    let captured: { url: string; init?: RequestInit } | null = null;
    globalThis.fetch = vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
      captured = { url: String(url), init };
      return Promise.resolve(
        new Response(
          JSON.stringify({
            ok: true, status_code: 200, latency_ms: 12,
            response: { data: { name: 'Priya' } },
            extracted_vars: { lead_name: 'Priya' }, error: null,
            request: { method: 'POST', url: 'http://x', params: null, json: { mobile: '999' }, data: null },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      );
    }) as unknown as typeof fetch;

    const fn: CustomFunction = {
      id: 'f1', name: 'get_lead', method: 'POST', timeout_ms: 8000,
      headers: {}, query_params: {}, body_mode: 'json', parameters: [],
      store_variables: [{ variable: 'lead_name', json_path: 'data.name' }],
      trigger: 'during_call', enabled: true, url: 'http://x',
    };
    const result = await api.testCustomFunction('bot123', fn, { mobile: '999' });

    expect(captured!.url).toContain('/api/bots/bot123/functions/test');
    expect(captured!.init?.method).toBe('POST');
    const body = JSON.parse(String(captured!.init?.body));
    expect(body.function.name).toBe('get_lead');
    expect(body.args).toEqual({ mobile: '999' });
    expect(result.ok).toBe(true);
    expect(result.extracted_vars).toEqual({ lead_name: 'Priya' });
  });
});
