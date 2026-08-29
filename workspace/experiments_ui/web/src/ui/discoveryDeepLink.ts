/** Extract a 64-hex content_id from a path or basename (content-addressed stills). */
export function extractContentIdFromName(name?: string | null): string | null {
  const m = /([0-9a-f]{64})/i.exec(String(name || "").trim());
  return m ? m[1].toLowerCase() : null;
}

/** Build a Discovery library URL that opens a specific indexed asset. */
export function discoveryLibraryHref(relpath?: string | null): string {
  const norm = (relpath || "").trim().replace(/^\/+/, "").replace(/\\/g, "/");
  if (!norm) return "/discovery";
  return `/discovery?relpath=${encodeURIComponent(norm)}`;
}

/** Still gallery deep-link: prefer content_id, else relpath, else free-text q. */
export function stillsHref(opts?: {
  contentId?: string | null;
  relpath?: string | null;
  q?: string | null;
}): string {
  const sp = new URLSearchParams();
  const contentId = String(opts?.contentId || "").trim().toLowerCase();
  const rel = String(opts?.relpath || "")
    .trim()
    .replace(/^\/+/, "")
    .replace(/\\/g, "/");
  const q = String(opts?.q || "").trim();
  if (contentId) sp.set("content_id", contentId);
  if (rel) sp.set("relpath", rel);
  if (q) sp.set("q", q);
  else if (!contentId && rel) {
    const base = rel.split("/").pop() || rel;
    if (base) sp.set("q", base);
  } else if (contentId && !q) {
    sp.set("q", contentId);
  }
  const qs = sp.toString();
  return qs ? `/discovery/stills?${qs}` : "/discovery/stills";
}

export function parseStillDeepLink(search: string = window.location.search): {
  contentId: string | null;
  relpath: string | null;
  q: string | null;
} {
  const sp = new URLSearchParams(search);
  const contentId = (sp.get("content_id") || "").trim().toLowerCase() || null;
  const relRaw = (sp.get("relpath") || "").trim().replace(/\\/g, "/").replace(/^\/+/, "");
  const relpath = relRaw || null;
  const q = (sp.get("q") || "").trim() || null;
  return { contentId, relpath, q };
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
  editJob?: string | null;
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
  if (opts?.editJob) {
    const editJob = String(opts.editJob).trim();
    if (editJob) sp.set("edit_job", editJob);
  }
  if (step) sp.set("step", step);
  if (origin) sp.set("origin", origin);
  const qs = sp.toString();
  // Bare /submit is the intent-modal empty state — doors should always pass intent.
  return qs ? `/submit?${qs}` : "/submit";
}

export type SubmitDeepLink = {
  mediaRelpath: string | null;
  clipId: string | null;
  markIn: number | null;
  markOut: number | null;
  family: string | null;
  identity: string | null;
  when: "now" | "later" | null;
  fromJob: string | null;
  /** Edit an existing pending/queued job in place (not advance). */
  editJob: string | null;
  step: string | null;
  origin: string | null;
};

export function parseSubmitDeepLink(search: string = window.location.search): SubmitDeepLink {
  const sp = new URLSearchParams(search);
  const mediaRaw = (sp.get("media") || sp.get("media_relpath") || "").trim().replace(/\\/g, "/");
  const clipId = (sp.get("clip_id") || "").trim() || null;
  const family = (sp.get("family") || "").trim() || null;
  const identity = (sp.get("identity") || "").trim() || null;
  const fromJob = (sp.get("from_job") || "").trim() || null;
  const editJob = (sp.get("edit_job") || "").trim() || null;
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
    editJob,
    step,
    origin,
  };
}

/** Intent-modal: compose only when a door handed off a subject. */
export function hasSubmitIntent(
  intent: Pick<SubmitDeepLink, "mediaRelpath" | "clipId" | "fromJob" | "editJob">,
): boolean {
  return Boolean(
    String(intent.mediaRelpath || "").trim() ||
      String(intent.clipId || "").trim() ||
      String(intent.fromJob || "").trim() ||
      String(intent.editJob || "").trim(),
  );
}

/** Map `origin` query to a Back link (door surface). */
export function submitOriginHref(
  origin: string | null | undefined,
  opts?: { mediaRelpath?: string | null; clipId?: string | null; fromJob?: string | null; editJob?: string | null },
): { href: string; label: string } | null {
  const o = String(origin || "")
    .trim()
    .toLowerCase();
  if (!o) return null;
  const media = String(opts?.mediaRelpath || "").trim() || null;
  const clipId = String(opts?.clipId || "").trim() || null;
  const fromJob = String(opts?.fromJob || opts?.editJob || "").trim() || null;
  if (o === "library" || o === "discovery") {
    return { href: discoveryLibraryHref(media), label: "Back to Library" };
  }
  if (o === "clips" || o === "clip") {
    return {
      href: clipsLibraryHref({ mediaRelpath: media, clipId, view: media ? "by_source" : "all" }),
      label: "Back to Clips",
    };
  }
  if (o === "workbench" || o === "work-products" || o === "work_products") {
    return { href: workbenchHref({ jobKey: fromJob }), label: "Back to Workbench" };
  }
  if (o === "queue" || o === "comfy-queue" || o === "comfy_queue") {
    return { href: queueHref({ jobKey: fromJob }), label: "Back to Queue" };
  }
  if (o === "factory" || o === "factory-map" || o === "factory_map") {
    return { href: "/discovery/factory-map", label: "Back to Factory" };
  }
  if (o === "gallery" || o === "stills" || o === "still") {
    const stillRel = media && /^(input\/|\S+\.(jpe?g|png|webp|gif)$)/i.test(media) ? media : null;
    return {
      href: stillsHref({
        relpath: stillRel,
        contentId: extractContentIdFromName(stillRel || media),
        q: stillRel ? null : media,
      }),
      label: "Back to Stills",
    };
  }
  if (o === "rate" || o === "rating") {
    return { href: "/discovery/rate", label: "Back to Rating" };
  }
  return null;
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
  if (promptId) sp.set("prompt_id", promptId);
  if (!job && !promptId && q) sp.set("q", q);
  const qs = sp.toString();
  return qs ? `/workbench?${qs}` : "/workbench";
}

/** Seed Workbench search from a library / lineage media path or basename. */
export function workbenchHrefForMedia(opts: {
  relpath?: string | null;
  name?: string | null;
  groupId?: string | null;
}): string {
  const rel = String(opts.relpath || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  const name = String(opts.name || "").trim();
  const base = (rel.split("/").pop() || name || "").trim();
  let q = base;
  if (q.includes(".")) {
    const stem = q.replace(/\.[^.]+$/, "");
    if (stem) q = stem;
  }
  if (!q) {
    const gid = String(opts.groupId || "").trim();
    const m = /^og:stem:(.+)$/i.exec(gid) || /^wip:stem:(.+)$/i.exec(gid);
    if (m) q = m[1];
  }
  return workbenchHref({ q: q || null });
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

/** Queue deep-link: Comfy prompt_id and/or factory job_key. */
export function queueHref(opts?: {
  promptId?: string | null;
  jobKey?: string | null;
}): string {
  const sp = new URLSearchParams();
  const promptId = String(opts?.promptId || "").trim();
  const job = String(opts?.jobKey || "").trim();
  if (promptId) sp.set("prompt_id", promptId);
  if (job) sp.set("job", job);
  const qs = sp.toString();
  return qs ? `/comfy-queue?${qs}` : "/comfy-queue";
}

export function parseQueueDeepLink(search: string = window.location.search): {
  promptId: string | null;
  job: string | null;
} {
  const sp = new URLSearchParams(search);
  const promptId = (sp.get("prompt_id") || "").trim() || null;
  const job = (sp.get("job") || "").trim() || null;
  return { promptId, job };
}
