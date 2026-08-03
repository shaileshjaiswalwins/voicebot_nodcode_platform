import { describe, it, expect, beforeEach } from 'vitest';
import { cacheSet, cacheGet, pruneOldCache, CACHE_PREFIX } from './cache';

describe('cacheSet / cacheGet', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('round-trips a stored value', () => {
    cacheSet('foo', { a: 1 });
    expect(cacheGet('foo', null)).toEqual({ a: 1 });
  });

  it('returns the fallback when the key is missing', () => {
    expect(cacheGet('missing', 'fallback')).toBe('fallback');
  });

  it('returns the fallback when the stored value is corrupt JSON', () => {
    window.localStorage.setItem(`${CACHE_PREFIX}bad`, '{not json');
    expect(cacheGet('bad', 'fallback')).toBe('fallback');
  });

  it('cacheSet swallows a quota/storage error instead of throwing', () => {
    const original = window.localStorage.setItem;
    window.localStorage.setItem = () => {
      throw new DOMException('QuotaExceededError');
    };
    expect(() => cacheSet('x', 1)).not.toThrow();
    window.localStorage.setItem = original;
  });

  it('cacheGet swallows a storage-read error and returns the fallback', () => {
    const original = window.localStorage.getItem;
    window.localStorage.getItem = () => {
      throw new Error('boom');
    };
    expect(cacheGet('x', 'fallback')).toBe('fallback');
    window.localStorage.getItem = original;
  });
});

describe('pruneOldCache', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('removes keys under the old "jd-vb:" prefix that are not the current version', () => {
    window.localStorage.setItem('jd-vb:v1:old-key', '"stale"');
    window.localStorage.setItem(`${CACHE_PREFIX}current-key`, '"fresh"');
    pruneOldCache();
    expect(window.localStorage.getItem('jd-vb:v1:old-key')).toBeNull();
    expect(window.localStorage.getItem(`${CACHE_PREFIX}current-key`)).toBe('"fresh"');
  });

  it('leaves unrelated keys untouched', () => {
    window.localStorage.setItem('unrelated_key', 'value');
    pruneOldCache();
    expect(window.localStorage.getItem('unrelated_key')).toBe('value');
  });

  it('does not throw when localStorage access fails', () => {
    const original = window.localStorage.key;
    window.localStorage.key = () => {
      throw new Error('boom');
    };
    expect(() => pruneOldCache()).not.toThrow();
    window.localStorage.key = original;
  });
});
