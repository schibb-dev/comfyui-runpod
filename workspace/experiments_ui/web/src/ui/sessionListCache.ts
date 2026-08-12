/**
 * Module-level session list cache for SPA stale-while-revalidate.
 * Survives route leave/return; cleared on full page reload (not IndexedDB).
 */

export type SessionListCacheEntry<T> = {
  value: T;
  fetchedAt: number;
};

const STORE = new Map<string, SessionListCacheEntry<unknown>>();

export function getSessionListCache<T>(key: string): SessionListCacheEntry<T> | undefined {
  const hit = STORE.get(key);
  if (!hit) return undefined;
  return hit as SessionListCacheEntry<T>;
}

export function setSessionListCache<T>(key: string, value: T, fetchedAt: number = Date.now()): void {
  STORE.set(key, { value, fetchedAt });
}

export function clearSessionListCache(key?: string): void {
  if (key == null) STORE.clear();
  else STORE.delete(key);
}
