/** Build a Discovery library URL that opens a specific indexed asset. */
export function discoveryLibraryHref(relpath?: string | null): string {
  const norm = (relpath || "").trim().replace(/^\/+/, "").replace(/\\/g, "/");
  if (!norm) return "/discovery";
  return `/discovery?relpath=${encodeURIComponent(norm)}`;
}

/** Build a Clips library URL, optionally selecting a clip / source video. */
export function clipsLibraryHref(opts?: {
  clipId?: string | null;
  q?: string | null;
  mediaRelpath?: string | null;
  view?: "all" | "by_source" | "derived" | null;
}): string {
  const sp = new URLSearchParams();
  const clipId = String(opts?.clipId || "").trim();
  const q = String(opts?.q || "").trim();
  const media = String(opts?.mediaRelpath || "").trim().replace(/\\/g, "/");
  const view =
    opts?.view === "by_source" || opts?.view === "all" || opts?.view === "derived" ? opts.view : null;
  if (clipId) sp.set("clip_id", clipId);
  if (q) sp.set("q", q);
  if (media) sp.set("media", media);
  if (view) sp.set("view", view);
  const qs = sp.toString();
  return qs ? `/discovery/clips?${qs}` : "/discovery/clips";
}

export function parseClipsDeepLink(search: string = window.location.search): {
  clipId: string | null;
  q: string | null;
  origin: string | null;
  mediaRelpath: string | null;
  view: "all" | "by_source" | "derived" | null;
} {
  const sp = new URLSearchParams(search);
  const clipId = (sp.get("clip_id") || "").trim() || null;
  const q = (sp.get("q") || "").trim() || null;
  const origin = (sp.get("origin") || "").trim() || null;
  const mediaRaw = (sp.get("media") || sp.get("media_relpath") || "").trim().replace(/\\/g, "/");
  const mediaRelpath = mediaRaw || null;
  const viewRaw = (sp.get("view") || "").trim().toLowerCase();
  const view =
    viewRaw === "by_source" || viewRaw === "source"
      ? "by_source"
      : viewRaw === "derived"
        ? "derived"
        : viewRaw === "all"
          ? "all"
          : null;
  return { clipId, q, origin, mediaRelpath, view };
}

/** Read `?relpath=` from a search string (defaults to current location). */
export function parseDiscoveryDeepLinkRelpath(search: string = window.location.search): string | null {
  const sp = new URLSearchParams(search);
  const rel = sp.get("relpath");
  if (!rel || !rel.trim()) return null;
  return rel.trim().replace(/^\/+/, "").replace(/\\/g, "/");
}

/** Submit compose deep-link: hand off intent from Library / Clips / etc. */
export function submitHref(opts?: {
  mediaRelpath?: string | null;
  clipId?: string | null;
  markIn?: number | null;
  markOut?: number | null;
  family?: string | null;
  identity?: string | null;
  when?: "now" | "later" | null;
  fromJob?: string | null;
  step?: string | null;
  origin?: string | null;
}): string {
  const sp = new URLSearchParams();
  const media = String(opts?.mediaRelpath || "").trim().replace(/\\/g, "/");
  const clipId = String(opts?.clipId || "").trim();
  const family = String(opts?.family || "").trim();
  const identity = String(opts?.identity || "").trim();
  const fromJob = String(opts?.fromJob || "").trim();
  const step = String(opts?.step || "").trim();
  const origin = String(opts?.origin || "").trim();
  if (media) sp.set("media", media);
  if (clipId) sp.set("clip_id", clipId);
  if (opts?.markIn != null && Number.isFinite(opts.markIn)) sp.set("mark_in", String(opts.markIn));
  if (opts?.markOut != null && Number.isFinite(opts.markOut)) sp.set("mark_out", String(opts.markOut));
  if (family) sp.set("family", family);
  if (identity) sp.set("identity", identity);
  if (opts?.when === "now" || opts?.when === "later") sp.set("when", opts.when);
  if (fromJob) sp.set("from_job", fromJob);
  if (step) sp.set("step", step);
  if (origin) sp.set("origin", origin);
  const qs = sp.toString();
  return qs ? `/submit?${qs}` : "/submit";
}

export function parseSubmitDeepLink(search: string = window.location.search): {
  mediaRelpath: string | null;
  clipId: string | null;
  markIn: number | null;
  markOut: number | null;
  family: string | null;
  identity: string | null;
  when: "now" | "later" | null;
  fromJob: string | null;
  step: string | null;
  origin: string | null;
} {
  const sp = new URLSearchParams(search);
  const mediaRaw = (sp.get("media") || sp.get("media_relpath") || "").trim().replace(/\\/g, "/");
  const clipId = (sp.get("clip_id") || "").trim() || null;
  const family = (sp.get("family") || "").trim() || null;
  const identity = (sp.get("identity") || "").trim() || null;
  const fromJob = (sp.get("from_job") || "").trim() || null;
  const step = (sp.get("step") || "").trim() || null;
  const origin = (sp.get("origin") || "").trim() || null;
  const whenRaw = (sp.get("when") || "").trim().toLowerCase();
  const when = whenRaw === "now" || whenRaw === "later" ? whenRaw : null;
  const parseMark = (key: string): number | null => {
    const raw = sp.get(key);
    if (raw == null || !String(raw).trim()) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  };
  return {
    mediaRelpath: mediaRaw || null,
    clipId,
    markIn: parseMark("mark_in"),
    markOut: parseMark("mark_out"),
    family,
    identity,
    when,
    fromJob,
    step,
    origin,
  };
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
