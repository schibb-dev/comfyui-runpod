/**
 * Session caches for Submit / Clips / Workbench handoff.
 * Survives SPA navigations; cleared on full reload.
 */
import {
  fetchIdentityStillCandidates,
  fetchShapeFactoryFamilies,
  listShapeFactoryClips,
  type IdentityStillCandidatesResponse,
  type ShapeFactoryClipsListResponse,
} from "./api";
import { clearSessionListCache, getSessionListCache, setSessionListCache } from "./sessionListCache";
import { fetchShapeFactoryWorkProducts } from "./api";
import type { GenerationStackOption, WorkProductFamilyOption, WorkProductsResponse } from "./types";

export type FamiliesBootstrap = {
  families: WorkProductFamilyOption[];
  extend_families: WorkProductFamilyOption[];
  vary_families: WorkProductFamilyOption[];
  derive_families: WorkProductFamilyOption[];
  extend_family_defaults: Record<string, string>;
  stacks: GenerationStackOption[];
  fingerprint?: string;
};

const FAMILIES_KEY = "sf:families-bootstrap-v4";
const WORK_PRODUCTS_KEY_PREFIX = "sf:work-products-v1";
/** Config-only endpoint — long TTL; fingerprint still refreshes on soft reload. */
const FAMILIES_TTL_MS = 60 * 60 * 1000;
/** Stale-while-revalidate for Workbench job list between navigations. */
const WORK_PRODUCTS_TTL_MS = 5 * 60 * 1000;
const CLIPS_TTL_MS = 5 * 60 * 1000;
const IDENTITY_TTL_MS = 5 * 60 * 1000;

function normRel(rel: string): string {
  return String(rel || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
}

export function clipsForMediaCacheKey(mediaRelpath: string): string {
  return `sf:clips:${normRel(mediaRelpath)}`;
}

export function identityStillCacheKey(opts: {
  relpath: string;
  family_slug?: string;
  job_key?: string;
}): string {
  return `sf:identity:${normRel(opts.relpath)}|${String(opts.family_slug || "").trim()}|${String(opts.job_key || "").trim()}`;
}

function fresh(fetchedAt: number, ttlMs: number): boolean {
  return Date.now() - fetchedAt < ttlMs;
}

function normalizeFamiliesBoot(input: {
  families?: WorkProductFamilyOption[] | null;
  extend_families?: WorkProductFamilyOption[] | null;
  vary_families?: WorkProductFamilyOption[] | null;
  derive_families?: WorkProductFamilyOption[] | null;
  stacks?: GenerationStackOption[] | null;
  sets?: {
    extend?: WorkProductFamilyOption[] | null;
    vary?: WorkProductFamilyOption[] | null;
    derive?: WorkProductFamilyOption[] | null;
  } | null;
  extend_family_defaults?: Record<string, string> | null;
  fingerprint?: string | null;
}): FamiliesBootstrap {
  const families = input.families || [];
  const extend = input.extend_families || input.sets?.extend || families;
  const vary = input.vary_families || input.sets?.vary || families;
  const derive = input.derive_families || input.sets?.derive || families;
  return {
    families,
    extend_families: extend,
    vary_families: vary,
    derive_families: derive,
    extend_family_defaults: input.extend_family_defaults || {},
    stacks: input.stacks || [],
    fingerprint: input.fingerprint || undefined,
  };
}

export function peekFamiliesBootstrap(): FamiliesBootstrap | null {
  const hit = getSessionListCache<FamiliesBootstrap>(FAMILIES_KEY);
  return hit?.value || null;
}

export function putFamiliesBootstrap(value: FamiliesBootstrap): void {
  setSessionListCache(FAMILIES_KEY, normalizeFamiliesBoot(value));
}

/** Drop cached families so the next load (or force) hits the server. */
export function clearFamiliesBootstrap(): void {
  clearSessionListCache(FAMILIES_KEY);
}

/** Write families extracted from any work-products response (partial; no discrete sets). */
export function rememberFamiliesFromWorkProducts(res: {
  families?: WorkProductFamilyOption[] | null;
  stacks?: GenerationStackOption[] | null;
  extend_family_defaults?: Record<string, string> | null;
}): void {
  const prev = peekFamiliesBootstrap();
  putFamiliesBootstrap(
    normalizeFamiliesBoot({
      families: res.families || prev?.families || [],
      extend_families: prev?.extend_families,
      vary_families: prev?.vary_families,
      derive_families: prev?.derive_families,
      stacks: res.stacks || prev?.stacks || [],
      extend_family_defaults: res.extend_family_defaults || prev?.extend_family_defaults || {},
      fingerprint: prev?.fingerprint,
    }),
  );
}

export async function loadFamiliesBootstrap(opts?: {
  force?: boolean;
}): Promise<FamiliesBootstrap> {
  if (opts?.force) {
    clearFamiliesBootstrap();
  }
  const hit = getSessionListCache<FamiliesBootstrap>(FAMILIES_KEY);
  if (!opts?.force && hit && fresh(hit.fetchedAt, FAMILIES_TTL_MS) && hit.value.extend_families?.length) {
    const cachedHasProfiles = (hit.value.families || []).some((f) => (f.prompt_profiles || []).length);
    const cachedHasFrameDefaults = (hit.value.families || []).some((f) => {
      const n = Number(f.params_defaults?.frames);
      return Number.isFinite(n) && n > 0;
    });
    if (cachedHasProfiles && cachedHasFrameDefaults && (hit.value.stacks || []).length) {
      // Refresh when the server fingerprint moved (new catalog / shape).
      try {
        const res = await fetchShapeFactoryFamilies();
        if (!res.fingerprint || res.fingerprint === hit.value.fingerprint) {
          return hit.value;
        }
        const payload = normalizeFamiliesBoot(res);
        putFamiliesBootstrap(payload);
        return payload;
      } catch {
        return hit.value;
      }
    }
  }
  const res = await fetchShapeFactoryFamilies();
  const payload = normalizeFamiliesBoot(res);
  putFamiliesBootstrap(payload);
  return payload;
}

/** Fire-and-forget warm for routes that often precede Submit. */
export function prefetchFamiliesBootstrap(): void {
  void loadFamiliesBootstrap().catch(() => {
    /* ignore */
  });
}

export function workProductsListCacheKey(opts: { limit: number; hourlyOnly: boolean }): string {
  return `${WORK_PRODUCTS_KEY_PREFIX}:${Math.max(1, opts.limit)}:${opts.hourlyOnly ? 1 : 0}`;
}

export function peekWorkProductsListEntry(
  key: string,
): { value: WorkProductsResponse; fetchedAt: number } | null {
  const hit = getSessionListCache<WorkProductsResponse>(key);
  if (!hit?.value) return null;
  return { value: hit.value, fetchedAt: hit.fetchedAt };
}

export function peekWorkProductsList(key: string): WorkProductsResponse | null {
  const hit = peekWorkProductsListEntry(key);
  if (!hit) return null;
  if (!fresh(hit.fetchedAt, WORK_PRODUCTS_TTL_MS)) return null;
  return hit.value;
}

export function putWorkProductsList(key: string, value: WorkProductsResponse): void {
  setSessionListCache(key, value);
}

/** Fetch work-products; reuse session cache when fresh unless force=true. */
export async function loadWorkProductsList(opts: {
  limit: number;
  hourlyOnly: boolean;
  force?: boolean;
}): Promise<WorkProductsResponse> {
  const key = workProductsListCacheKey(opts);
  const hit = peekWorkProductsListEntry(key);
  if (!opts.force && hit && fresh(hit.fetchedAt, WORK_PRODUCTS_TTL_MS)) {
    return hit.value;
  }
  const res = await fetchShapeFactoryWorkProducts({
    limit: opts.limit,
    hourlyOnly: opts.hourlyOnly,
    lite: true,
  });
  putWorkProductsList(key, res);
  rememberFamiliesFromWorkProducts(res);
  return res;
}

/** Warm Workbench list in the background (e.g. from Home). */
export function prefetchWorkProductsList(opts?: { limit?: number; hourlyOnly?: boolean }): void {
  void loadWorkProductsList({
    limit: opts?.limit ?? 40,
    hourlyOnly: opts?.hourlyOnly ?? false,
  }).catch(() => {
    /* ignore */
  });
}

export function peekClipsForMedia(mediaRelpath: string): ShapeFactoryClipsListResponse | null {
  const key = clipsForMediaCacheKey(mediaRelpath);
  const hit = getSessionListCache<ShapeFactoryClipsListResponse>(key);
  return hit?.value || null;
}

export function putClipsForMedia(mediaRelpath: string, res: ShapeFactoryClipsListResponse): void {
  setSessionListCache(clipsForMediaCacheKey(mediaRelpath), res);
}

export function invalidateClipsForMedia(mediaRelpath: string): void {
  clearSessionListCache(clipsForMediaCacheKey(mediaRelpath));
}

export async function loadClipsForMedia(
  mediaRelpath: string,
  opts?: { force?: boolean },
): Promise<ShapeFactoryClipsListResponse> {
  const key = clipsForMediaCacheKey(mediaRelpath);
  const hit = getSessionListCache<ShapeFactoryClipsListResponse>(key);
  if (!opts?.force && hit && fresh(hit.fetchedAt, CLIPS_TTL_MS)) {
    return hit.value;
  }
  const still = /\.(jpe?g|png|webp|gif|avif|bmp)(\?|$)/i.test(mediaRelpath);
  if (still) {
    const empty: ShapeFactoryClipsListResponse = { ok: true, clips: [] };
    putClipsForMedia(mediaRelpath, empty);
    return empty;
  }
  const res = await listShapeFactoryClips({ mediaRelpath });
  putClipsForMedia(mediaRelpath, res);
  return res;
}

export function peekIdentityStill(opts: {
  relpath: string;
  family_slug?: string;
  job_key?: string;
}): IdentityStillCandidatesResponse | null {
  const hit = getSessionListCache<IdentityStillCandidatesResponse>(identityStillCacheKey(opts));
  return hit?.value || null;
}

export function putIdentityStill(
  opts: { relpath: string; family_slug?: string; job_key?: string },
  res: IdentityStillCandidatesResponse,
): void {
  setSessionListCache(identityStillCacheKey(opts), res);
}

export function invalidateIdentityStill(opts: {
  relpath: string;
  family_slug?: string;
  job_key?: string;
}): void {
  clearSessionListCache(identityStillCacheKey(opts));
}

export async function loadIdentityStillCandidates(
  opts: { relpath: string; family_slug?: string; job_key?: string },
  fetchOpts?: { force?: boolean },
): Promise<IdentityStillCandidatesResponse> {
  const key = identityStillCacheKey(opts);
  const hit = getSessionListCache<IdentityStillCandidatesResponse>(key);
  if (!fetchOpts?.force && hit && fresh(hit.fetchedAt, IDENTITY_TTL_MS)) {
    return hit.value;
  }
  const res = await fetchIdentityStillCandidates(opts);
  putIdentityStill(opts, res);
  return res;
}
