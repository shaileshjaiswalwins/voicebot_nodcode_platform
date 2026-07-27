import '@testing-library/jest-dom/vitest';

// Node 22+ ships a built-in global `localStorage` stub that shadows jsdom's
// fully-functional Storage implementation when no --localstorage-file path is
// configured (see Node's experimental webstorage). That stub exposes no
// setItem/getItem/removeItem/clear, which breaks any code (like src/api.ts and
// src/views/BuilderView.tsx) that relies on real localStorage semantics.
// Replace it with a small in-memory Storage-compatible polyfill before each
// test file runs, keeping `window.localStorage` and the global `localStorage`
// in sync the way real browsers do.
function createMemoryStorage(): Storage {
  let store = new Map<string, string>();
  const storage = {
    getItem(key: string) {
      return store.has(key) ? store.get(key)! : null;
    },
    setItem(key: string, value: string) {
      store.set(key, String(value));
    },
    removeItem(key: string) {
      store.delete(key);
    },
    clear() {
      store = new Map<string, string>();
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    get length() {
      return store.size;
    },
  };
  return storage as unknown as Storage;
}

if (typeof window !== 'undefined') {
  const memoryStorage = createMemoryStorage();
  Object.defineProperty(window, 'localStorage', {
    value: memoryStorage,
    writable: true,
    configurable: true,
  });
  Object.defineProperty(globalThis, 'localStorage', {
    value: memoryStorage,
    writable: true,
    configurable: true,
  });
}
