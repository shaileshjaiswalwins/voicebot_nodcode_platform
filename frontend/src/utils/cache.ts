export const CACHE_VERSION = 'v2';
export const CACHE_PREFIX = `jd-vb:${CACHE_VERSION}:`;

export function cacheSet<T>(key: string, value: T) {
  try {
    window.localStorage.setItem(`${CACHE_PREFIX}${key}`, JSON.stringify(value));
  } catch {
    // Cache is best-effort only.
  }
}

export function cacheGet<T>(key: string, fallback: T): T {
  try {
    const value = window.localStorage.getItem(`${CACHE_PREFIX}${key}`);
    return value ? JSON.parse(value) as T : fallback;
  } catch {
    return fallback;
  }
}

export function pruneOldCache() {
  try {
    const drop: string[] = [];
    for (let i = 0; i < window.localStorage.length; i++) {
      const key = window.localStorage.key(i);
      if (key && key.startsWith('jd-vb:') && !key.startsWith(CACHE_PREFIX)) drop.push(key);
    }
    drop.forEach((key) => window.localStorage.removeItem(key));
  } catch {
    // ignore
  }
}

pruneOldCache();
