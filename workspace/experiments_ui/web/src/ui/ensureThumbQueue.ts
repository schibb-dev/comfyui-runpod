/**
 * Concurrency-limited queue for POST /api/discovery/ensure-thumb.
 * Dedupes in-flight requests by relpath and caches resolved URLs.
 */
import { ensureDiscoveryThumb, type DiscoveryEnsureThumbResponse } from "./api";

const MAX_CONCURRENT = 3;

type Job = {
  relpath: string;
  resolve: (v: DiscoveryEnsureThumbResponse) => void;
  reject: (e: unknown) => void;
};

const waitQueue: Job[] = [];
let activeCount = 0;
const inFlight = new Map<string, Promise<DiscoveryEnsureThumbResponse>>();
/** relpath → thumb URL (or null when generation failed). */
const resolvedCache = new Map<string, string | null>();

function thumbUrlFromResponse(res: DiscoveryEnsureThumbResponse): string | null {
  if (!res.ok) return null;
  if (res.thumb_url) return res.thumb_url;
  if (res.thumb_relpath) {
    return "/files/" + encodeURIComponent(res.thumb_relpath.replace(/\\/g, "/"));
  }
  return null;
}

function pump(): void {
  while (activeCount < MAX_CONCURRENT && waitQueue.length > 0) {
    const job = waitQueue.shift();
    if (!job) break;
    activeCount += 1;
    void ensureDiscoveryThumb({ relpath: job.relpath })
      .then((res) => {
        const url = thumbUrlFromResponse(res);
        resolvedCache.set(job.relpath, url);
        job.resolve(res);
      })
      .catch((err) => {
        resolvedCache.set(job.relpath, null);
        job.reject(err);
      })
      .finally(() => {
        activeCount -= 1;
        inFlight.delete(job.relpath);
        pump();
      });
  }
}

export function cachedEnsureThumbUrl(relpath: string): string | null | undefined {
  if (!resolvedCache.has(relpath)) return undefined;
  return resolvedCache.get(relpath);
}

export function enqueueEnsureThumb(relpath: string): Promise<DiscoveryEnsureThumbResponse> {
  const key = relpath.replace(/\\/g, "/").trim();
  if (!key) {
    return Promise.resolve({ ok: false, error: "empty_relpath" });
  }
  if (resolvedCache.has(key)) {
    const url = resolvedCache.get(key);
    return Promise.resolve({
      ok: url != null,
      relpath: key,
      thumb_url: url ?? undefined,
      reason: url != null ? "cache_hit" : "cache_miss_failed",
    });
  }
  const existing = inFlight.get(key);
  if (existing) return existing;

  const promise = new Promise<DiscoveryEnsureThumbResponse>((resolve, reject) => {
    waitQueue.push({ relpath: key, resolve, reject });
    pump();
  });
  inFlight.set(key, promise);
  return promise;
}

export function ensureThumbQueueStats(): { active: number; waiting: number; max: number } {
  return { active: activeCount, waiting: waitQueue.length, max: MAX_CONCURRENT };
}
