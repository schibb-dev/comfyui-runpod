import type { RunsItem } from "./types";

export const RUNS_CACHE_DB = "experiments_ui_cache_v1";
export const RUNS_CACHE_STORE = "runsByExpId";
export const RUNS_CACHE_TTL_MS = 15 * 60 * 1000; // 15 minutes (stale-while-revalidate)

export type RunsCacheEntry = {
  exp_id: string;
  runs: RunsItem[];
  fetchedAtMs: number;
  sizeBytes: number;
};

type CacheStats = {
  expCount: number;
  runCount: number;
  totalBytes: number;
  newestFetchedAtMs?: number;
  oldestFetchedAtMs?: number;
};

function _reqToPromise<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IndexedDB request failed"));
  });
}

function _txDone(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
  });
}

let _dbP: Promise<IDBDatabase> | null = null;

async function _openDb(): Promise<IDBDatabase> {
  if (_dbP) return _dbP;
  _dbP = new Promise((resolve, reject) => {
    const req = indexedDB.open(RUNS_CACHE_DB, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(RUNS_CACHE_STORE)) {
        db.createObjectStore(RUNS_CACHE_STORE, { keyPath: "exp_id" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("Failed to open IndexedDB"));
  });
  return _dbP;
}

function _estimateBytes(x: unknown): number {
  // local heuristic (UTF-16-ish); good enough for a UI summary.
  try {
    return JSON.stringify(x).length * 2;
  } catch {
    return 0;
  }
}

export async function runsCacheReadMany(expIds: string[], opts: { maxAgeMs?: number } = {}): Promise<{
  hits: RunsCacheEntry[];
  freshIds: string[];
  staleIds: string[];
  missingIds: string[];
}> {
  const maxAgeMs = typeof opts.maxAgeMs === "number" ? opts.maxAgeMs : RUNS_CACHE_TTL_MS;
  const now = Date.now();
  const want = Array.from(new Set(expIds.filter(Boolean)));
  if (!want.length) return { hits: [], freshIds: [], staleIds: [], missingIds: [] };

  const db = await _openDb();
  const tx = db.transaction(RUNS_CACHE_STORE, "readonly");
  const store = tx.objectStore(RUNS_CACHE_STORE);

  const hits: RunsCacheEntry[] = [];
  const freshIds: string[] = [];
  const staleIds: string[] = [];
  const missingIds: string[] = [];

  for (const id of want) {
    // eslint-disable-next-line no-await-in-loop
    const v = await _reqToPromise(store.get(id) as IDBRequest<RunsCacheEntry | undefined>);
    if (!v) {
      missingIds.push(id);
      continue;
    }
    hits.push(v);
    const age = now - (v.fetchedAtMs || 0);
    if (Number.isFinite(age) && age <= maxAgeMs) freshIds.push(id);
    else staleIds.push(id);
  }

  await _txDone(tx);
  return { hits, freshIds, staleIds, missingIds };
}

export async function runsCacheWriteFromMulti(runs: RunsItem[]): Promise<void> {
  if (!runs?.length) return;
  const db = await _openDb();
  const tx = db.transaction(RUNS_CACHE_STORE, "readwrite");
  const store = tx.objectStore(RUNS_CACHE_STORE);

  const byExp = new Map<string, RunsItem[]>();
  for (const r of runs) {
    if (!r?.exp_id) continue;
    const arr = byExp.get(r.exp_id) ?? [];
    arr.push(r);
    byExp.set(r.exp_id, arr);
  }
  const fetchedAtMs = Date.now();

  for (const [exp_id, rr] of byExp) {
    const entry: RunsCacheEntry = {
      exp_id,
      runs: rr,
      fetchedAtMs,
      sizeBytes: _estimateBytes(rr),
    };
    store.put(entry);
  }

  await _txDone(tx);
}

export async function runsCacheClear(): Promise<void> {
  const db = await _openDb();
  const tx = db.transaction(RUNS_CACHE_STORE, "readwrite");
  tx.objectStore(RUNS_CACHE_STORE).clear();
  await _txDone(tx);
}

export async function runsCacheGetStats(): Promise<CacheStats> {
  const db = await _openDb();
  const tx = db.transaction(RUNS_CACHE_STORE, "readonly");
  const store = tx.objectStore(RUNS_CACHE_STORE);
  const all = await _reqToPromise(store.getAll() as IDBRequest<RunsCacheEntry[]>);
  await _txDone(tx);

  let expCount = 0;
  let runCount = 0;
  let totalBytes = 0;
  let newest: number | undefined;
  let oldest: number | undefined;

  for (const e of all) {
    if (!e?.exp_id) continue;
    expCount += 1;
    runCount += e.runs?.length ?? 0;
    totalBytes += e.sizeBytes ?? 0;
    const t = e.fetchedAtMs;
    if (typeof t === "number" && Number.isFinite(t)) {
      newest = newest == null ? t : Math.max(newest, t);
      oldest = oldest == null ? t : Math.min(oldest, t);
    }
  }

  return { expCount, runCount, totalBytes, newestFetchedAtMs: newest, oldestFetchedAtMs: oldest };
}

