/**
 * Session cache for GET /api/discovery/asset-ratings — stale-while-revalidate for rating UI navigation.
 */
import { fetchDiscoveryAssetRatings } from "./api";
import type {
  DiscoveryAssetRatingsResponse,
  DiscoveryRatingSamplerCandidate,
  QualityAxesMap,
} from "./types";

const cache = new Map<string, DiscoveryAssetRatingsResponse>();
const inflight = new Map<string, Promise<DiscoveryAssetRatingsResponse>>();

export function peekAssetRatings(relpath: string): DiscoveryAssetRatingsResponse | undefined {
  const key = relpath.trim();
  if (!key) return undefined;
  return cache.get(key);
}

export function rememberAssetRatings(relpath: string, ratings: DiscoveryAssetRatingsResponse): void {
  const key = relpath.trim();
  if (!key) return;
  cache.set(key, ratings);
}

export function patchAssetRatingsCache(
  relpath: string,
  patch: Partial<DiscoveryAssetRatingsResponse>,
): DiscoveryAssetRatingsResponse | undefined {
  const key = relpath.trim();
  if (!key) return undefined;
  const prev = cache.get(key) ?? ({ ok: true, query_relpath: key } as DiscoveryAssetRatingsResponse);
  const next = { ...prev, ...patch, query_relpath: key };
  cache.set(key, next);
  return next;
}

export function patchCachedQuality(
  relpath: string,
  axes: QualityAxesMap,
  explicit: number | null | undefined,
): void {
  patchAssetRatingsCache(relpath, {
    axes,
    explicit: {
      rating: explicit ?? undefined,
      axes,
    },
  });
}

export function patchCachedDisposition(
  relpath: string,
  markers: string[],
  reasonDetail?: Record<string, { modifiers?: string[]; note?: string }>,
  updatedAt?: string | null,
): void {
  patchAssetRatingsCache(relpath, {
    disposition_markers: markers,
    disposition_reason_detail: reasonDetail,
    disposition_updated_at: updatedAt ?? undefined,
  });
}

export function patchCachedAppetite(
  relpath: string,
  appetite: DiscoveryAssetRatingsResponse["appetite"],
  facet: DiscoveryAssetRatingsResponse["appetite_facet"],
): void {
  patchAssetRatingsCache(relpath, { appetite, appetite_facet: facet });
}

/** Partial ratings stub from sampler row fields (no quality axes). */
export function ratingsSeedFromCandidate(c: DiscoveryRatingSamplerCandidate): DiscoveryAssetRatingsResponse {
  return {
    ok: true,
    query_relpath: c.relpath,
    appetite: c.appetite ?? null,
    appetite_facet: c.appetite_facet ?? null,
    disposition_markers: c.disposition_markers ?? [],
    last_triaged_at: c.last_triaged_at ?? null,
    triage_pass_count: c.triage_pass_count ?? 0,
  };
}

export async function loadAssetRatings(relpath: string): Promise<DiscoveryAssetRatingsResponse> {
  const key = relpath.trim();
  if (!key) throw new Error("missing relpath");

  const hit = cache.get(key);
  if (hit) return hit;

  const pending = inflight.get(key);
  if (pending) return pending;

  const p = fetchDiscoveryAssetRatings(key)
    .then((r) => {
      cache.set(key, r);
      return r;
    })
    .finally(() => {
      inflight.delete(key);
    });
  inflight.set(key, p);
  return p;
}

/** Always fetch; update cache and return fresh ratings. */
export async function revalidateAssetRatings(relpath: string): Promise<DiscoveryAssetRatingsResponse> {
  const key = relpath.trim();
  if (!key) throw new Error("missing relpath");

  const pending = inflight.get(key);
  if (pending) return pending;

  const p = fetchDiscoveryAssetRatings(key)
    .then((r) => {
      cache.set(key, r);
      return r;
    })
    .finally(() => {
      inflight.delete(key);
    });
  inflight.set(key, p);
  return p;
}

export function prefetchAssetRatings(relpaths: string[]): void {
  for (const rel of relpaths) {
    const key = String(rel || "").trim();
    if (!key || cache.has(key) || inflight.has(key)) continue;
    void loadAssetRatings(key).catch(() => {});
  }
}
