import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';

describe('request() timeout', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('aborts a hung request and throws a distinguishable ApiError instead of hanging forever', async () => {
    globalThis.fetch = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        const signal = init?.signal;
        signal?.addEventListener('abort', () => {
          const err = new DOMException('The operation was aborted.', 'AbortError');
          reject(err);
        });
      });
    }) as unknown as typeof fetch;

    const loginPromise = api.login('pm@example.com', 'password');
    const assertion = expect(loginPromise).rejects.toMatchObject({
      status: 0,
      message: expect.stringContaining('timed out'),
    });

    await vi.advanceTimersByTimeAsync(15000);
    await assertion;
  });

  it('rejected ApiError from a timeout is an instance of ApiError', async () => {
    globalThis.fetch = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          reject(new DOMException('aborted', 'AbortError'));
        });
      });
    }) as unknown as typeof fetch;

    const loginPromise = api.login('pm@example.com', 'password');
    const check = loginPromise.catch((err) => {
      expect(err).toBeInstanceOf(ApiError);
    });
    await vi.advanceTimersByTimeAsync(15000);
    await check;
  });
});
