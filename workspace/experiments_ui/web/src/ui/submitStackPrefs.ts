import type { GenerationStackOption } from "./types";

/** Submit surface: still seed (I2V) vs video Use advance (V2V extend/vary/derive). */
export type SubmitRouteKind = "still" | "video";

export const SUBMIT_STACK_COOKIE = "submit_stack.v2";
const SUBMIT_STACK_COOKIE_V1 = "submit_stack.v1";
const MAX_RECENT = 8;
const MAX_AGE_DAYS = 365;

export type SubmitStackPrefs = {
  lastByRoute: Partial<Record<SubmitRouteKind, string>>;
  /** `${route}:${familySlug}` — workflow family is route-specific; do not bleed across still/video. */
  byFamilyRoute: Record<string, string>;
  recentByRoute: Partial<Record<SubmitRouteKind, string[]>>;
  /** Cross-route model preference (`720p:Q5`) — stacks share UNet/resolution, not workflow topology. */
  modelSignature: string;
};

export type RememberSubmitStackOpts = {
  familySlug?: string | null;
  routeKind: SubmitRouteKind;
  stacks?: GenerationStackOption[] | null;
};

function emptyPrefs(): SubmitStackPrefs {
  return { lastByRoute: {}, byFamilyRoute: {}, recentByRoute: {}, modelSignature: "" };
}

type CookieStore = {
  read: (name: string) => string;
  write: (name: string, value: string) => void;
};

let cookieStoreOverride: CookieStore | null = null;

function browserCookieStore(): CookieStore {
  return {
    read(name: string): string {
      try {
        const parts = String(document.cookie || "")
          .split(";")
          .map((p) => p.trim());
        for (const part of parts) {
          if (!part) continue;
          const eq = part.indexOf("=");
          const key = eq >= 0 ? part.slice(0, eq) : part;
          if (key === name) return eq >= 0 ? part.slice(eq + 1) : "";
        }
      } catch {
        /* ignore */
      }
      return "";
    },
    write(name: string, value: string): void {
      try {
        const maxAgeSec = Math.max(60, Math.round(MAX_AGE_DAYS * 86400));
        document.cookie = `${name}=${value}; Path=/; Max-Age=${maxAgeSec}; SameSite=Lax`;
      } catch {
        /* ignore */
      }
    },
  };
}

function cookieStore(): CookieStore {
  return cookieStoreOverride || browserCookieStore();
}

/** Test hook — inject an in-memory cookie store. */
export function setSubmitStackCookieStoreForTests(store: CookieStore | null): void {
  cookieStoreOverride = store;
}

function stackCatalogIds(stacks: GenerationStackOption[] | null | undefined): Set<string> {
  return new Set(
    (Array.isArray(stacks) ? stacks : [])
      .map((s) => String(s.stack_id || "").trim())
      .filter(Boolean),
  );
}

function stackCatalogMap(stacks: GenerationStackOption[] | null | undefined): Map<string, GenerationStackOption> {
  const out = new Map<string, GenerationStackOption>();
  for (const row of Array.isArray(stacks) ? stacks : []) {
    const id = String(row.stack_id || "").trim();
    if (id) out.set(id, row);
  }
  return out;
}

export function familyRouteKey(routeKind: SubmitRouteKind, familySlug?: string | null): string {
  const family = String(familySlug || "").trim();
  return family ? `${routeKind}:${family}` : "";
}

/** Model/resolution identity shared across still and video routes (not workflow/family). */
export function stackModelSignature(
  stack: GenerationStackOption | null | undefined,
  stackId?: string | null,
): string {
  const size = String(stack?.training_size || "").trim();
  const quant = String(stack?.quant || "").trim();
  if (size && quant) return `${size}:${quant}`.toLowerCase();
  const id = String(stack?.stack_id || stackId || "").trim();
  const m = id.match(/(\d+p)-([^/]+)$/i);
  if (m) return `${m[1]}:${m[2]}`.toLowerCase();
  const unet = String(stack?.unet_name || "").trim();
  if (unet) return unet.toLowerCase();
  return id.toLowerCase();
}

export function findStackIdByModelSignature(
  stacks: GenerationStackOption[] | null | undefined,
  signature: string | null | undefined,
): string | null {
  const sig = String(signature || "").trim().toLowerCase();
  if (!sig) return null;
  for (const row of Array.isArray(stacks) ? stacks : []) {
    if (stackModelSignature(row) === sig) {
      const id = String(row.stack_id || "").trim();
      if (id) return id;
    }
  }
  return null;
}

function normalizePrefs(raw: unknown): SubmitStackPrefs {
  if (!raw || typeof raw !== "object") return emptyPrefs();
  const doc = raw as Partial<SubmitStackPrefs> & {
    last?: string;
    byFamily?: Record<string, string>;
    recent?: string[];
  };

  if (doc.last != null || doc.byFamily || doc.recent) {
    return migrateV1Prefs(doc);
  }

  const lastByRoute: Partial<Record<SubmitRouteKind, string>> = {};
  if (doc.lastByRoute && typeof doc.lastByRoute === "object") {
    for (const route of ["still", "video"] as const) {
      const sid = String(doc.lastByRoute[route] || "").trim();
      if (sid) lastByRoute[route] = sid;
    }
  }

  const byFamilyRoute: Record<string, string> = {};
  if (doc.byFamilyRoute && typeof doc.byFamilyRoute === "object") {
    for (const [key, val] of Object.entries(doc.byFamilyRoute)) {
      const k = String(key || "").trim();
      const sid = String(val || "").trim();
      if (k && sid) byFamilyRoute[k] = sid;
    }
  }

  const recentByRoute: Partial<Record<SubmitRouteKind, string[]>> = {};
  if (doc.recentByRoute && typeof doc.recentByRoute === "object") {
    for (const route of ["still", "video"] as const) {
      const rows = doc.recentByRoute[route];
      if (Array.isArray(rows)) {
        recentByRoute[route] = rows.map((id) => String(id || "").trim()).filter(Boolean).slice(0, MAX_RECENT);
      }
    }
  }

  return {
    lastByRoute,
    byFamilyRoute,
    recentByRoute,
    modelSignature: String(doc.modelSignature || "").trim().toLowerCase(),
  };
}

function migrateV1Prefs(doc: {
  last?: string;
  byFamily?: Record<string, string>;
  recent?: string[];
}): SubmitStackPrefs {
  const prefs = emptyPrefs();
  const last = String(doc.last || "").trim();
  if (last) {
    prefs.lastByRoute.video = last;
    prefs.modelSignature = stackModelSignature(null, last);
  }
  if (doc.byFamily && typeof doc.byFamily === "object") {
    for (const [family, val] of Object.entries(doc.byFamily)) {
      const slug = String(family || "").trim();
      const sid = String(val || "").trim();
      if (slug && sid) prefs.byFamilyRoute[`video:${slug}`] = sid;
    }
  }
  if (Array.isArray(doc.recent)) {
    prefs.recentByRoute.video = doc.recent.map((id) => String(id || "").trim()).filter(Boolean).slice(0, MAX_RECENT);
  }
  return prefs;
}

function readCookieJson(name: string): unknown {
  const raw = cookieStore().read(name);
  if (!raw) return null;
  try {
    return JSON.parse(decodeURIComponent(raw));
  } catch {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }
}

export function readSubmitStackPrefs(): SubmitStackPrefs {
  const v2 = readCookieJson(SUBMIT_STACK_COOKIE);
  if (v2) return normalizePrefs(v2);
  const v1 = readCookieJson(SUBMIT_STACK_COOKIE_V1);
  if (v1) return normalizePrefs(v1);
  return emptyPrefs();
}

export function writeSubmitStackPrefs(prefs: SubmitStackPrefs): void {
  cookieStore().write(SUBMIT_STACK_COOKIE, encodeURIComponent(JSON.stringify(normalizePrefs(prefs))));
}

/** Remember a stack the operator picked or successfully submitted. */
export function rememberSubmitStack(stackId: string, opts: RememberSubmitStackOpts): void {
  const sid = String(stackId || "").trim();
  if (!sid) return;
  const routeKind = opts.routeKind;
  const prefs = readSubmitStackPrefs();
  prefs.lastByRoute[routeKind] = sid;

  const catalog = stackCatalogMap(opts.stacks);
  const stack = catalog.get(sid);
  const sig = stackModelSignature(stack, sid);
  if (sig) prefs.modelSignature = sig;

  const familyKey = familyRouteKey(routeKind, opts.familySlug);
  if (familyKey) prefs.byFamilyRoute[familyKey] = sid;

  const recent = [sid, ...(prefs.recentByRoute[routeKind] || []).filter((id) => id !== sid)].slice(0, MAX_RECENT);
  prefs.recentByRoute[routeKind] = recent;
  writeSubmitStackPrefs(prefs);
}

function firstValid(ids: string[], catalog: Set<string>): string | null {
  for (const id of ids) {
    const sid = String(id || "").trim();
    if (sid && catalog.has(sid)) return sid;
  }
  return null;
}

/**
 * Pick a stack for Submit.
 *
 * Order: source job → route+family cookie → route last → cross-route model →
 * family catalog default → route recent → catalog first.
 */
export function pickSubmitStack(
  stacks: GenerationStackOption[] | null | undefined,
  prefer?: {
    routeKind?: SubmitRouteKind | null;
    jobStackId?: string | null;
    familyStackId?: string | null;
    familySlug?: string | null;
    prefs?: SubmitStackPrefs | null;
  },
): string {
  const rows = Array.isArray(stacks) ? stacks : [];
  const catalog = stackCatalogIds(rows);
  const routeKind = prefer?.routeKind === "still" || prefer?.routeKind === "video" ? prefer.routeKind : "video";

  const job = String(prefer?.jobStackId || "").trim();
  if (job && catalog.has(job)) return job;

  const prefs = prefer?.prefs ?? readSubmitStackPrefs();
  const familyKey = familyRouteKey(routeKind, prefer?.familySlug);
  const byFamilyRoute = familyKey ? String(prefs.byFamilyRoute?.[familyKey] || "").trim() : "";
  if (byFamilyRoute && catalog.has(byFamilyRoute)) return byFamilyRoute;

  const routeLast = String(prefs.lastByRoute?.[routeKind] || "").trim();
  if (routeLast && catalog.has(routeLast)) return routeLast;

  const modelMatch = findStackIdByModelSignature(rows, prefs.modelSignature);
  if (modelMatch && catalog.has(modelMatch)) return modelMatch;

  const familyDefault = String(prefer?.familyStackId || "").trim();
  if (familyDefault && catalog.has(familyDefault)) return familyDefault;

  const recentHit = firstValid(prefs.recentByRoute?.[routeKind] || [], catalog);
  if (recentHit) return recentHit;

  return String(rows[0]?.stack_id || "").trim();
}

export function resolveSubmitRouteKind(isStill: boolean): SubmitRouteKind {
  return isStill ? "still" : "video";
}
