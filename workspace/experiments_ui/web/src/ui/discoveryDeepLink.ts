/** Build a Discovery library URL that opens a specific indexed asset. */
export function discoveryLibraryHref(relpath?: string | null): string {
  const norm = (relpath || "").trim().replace(/^\/+/, "").replace(/\\/g, "/");
  if (!norm) return "/discovery";
  return `/discovery?relpath=${encodeURIComponent(norm)}`;
}

/** Build a Clips library URL, optionally selecting a clip. */
export function clipsLibraryHref(opts?: { clipId?: string | null; q?: string | null }): string {
  const sp = new URLSearchParams();
  const clipId = String(opts?.clipId || "").trim();
  const q = String(opts?.q || "").trim();
  if (clipId) sp.set("clip_id", clipId);
  if (q) sp.set("q", q);
  const qs = sp.toString();
  return qs ? `/discovery/clips?${qs}` : "/discovery/clips";
}

export function parseClipsDeepLink(search: string = window.location.search): {
  clipId: string | null;
  q: string | null;
  origin: string | null;
} {
  const sp = new URLSearchParams(search);
  const clipId = (sp.get("clip_id") || "").trim() || null;
  const q = (sp.get("q") || "").trim() || null;
  const origin = (sp.get("origin") || "").trim() || null;
  return { clipId, q, origin };
}

/** Read `?relpath=` from a search string (defaults to current location). */
export function parseDiscoveryDeepLinkRelpath(search: string = window.location.search): string | null {
  const sp = new URLSearchParams(search);
  const rel = sp.get("relpath");
  if (!rel || !rel.trim()) return null;
  return rel.trim().replace(/^\/+/, "").replace(/\\/g, "/");
}

/** Workbench deep-link: prefer factory job_key, else prompt_id / free-text q. */
export function workbenchHref(opts?: {
  jobKey?: string | null;
  promptId?: string | null;
  q?: string | null;
}): string {
  const sp = new URLSearchParams();
  const job = String(opts?.jobKey || "").trim();
  const promptId = String(opts?.promptId || "").trim();
  const q = String(opts?.q || "").trim();
  if (job) sp.set("job", job);
  else if (promptId) sp.set("prompt_id", promptId);
  else if (q) sp.set("q", q);
  const qs = sp.toString();
  return qs ? `/workbench?${qs}` : "/workbench";
}

export function parseWorkbenchDeepLink(search: string = window.location.search): {
  job: string | null;
  promptId: string | null;
  q: string | null;
  /** Value to seed the Workbench search box. */
  filter: string | null;
} {
  const sp = new URLSearchParams(search);
  const job = (sp.get("job") || "").trim() || null;
  const promptId = (sp.get("prompt_id") || "").trim() || null;
  const q = (sp.get("q") || "").trim() || null;
  return { job, promptId, q, filter: job || promptId || q };
}
