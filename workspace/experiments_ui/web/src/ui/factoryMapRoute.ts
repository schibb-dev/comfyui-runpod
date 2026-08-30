export type FactoryMapRoute =
  | { view: "index" }
  | { view: "family"; familySlug: string }
  | { view: "pipeline"; pipelineId: string };

/** Hash focus on factory-map pages (#pools / #curation[=slug] / #job=key). */
export type FactoryMapFocus =
  | { kind: "pools" }
  | { kind: "curation"; familySlug?: string }
  | { kind: "job"; jobKey: string };

export type FactoryMapFamilyFocusOpts = {
  focus?: "pools" | "curation" | "job";
  jobKey?: string;
};

const PREFIX = "/discovery/factory-map";
const PIPELINE_PREFIX = `${PREFIX}/pipeline/`;

export function parseFactoryMapRoute(pathname: string = window.location.pathname): FactoryMapRoute {
  const path = (pathname || "/").replace(/\/+$/, "") || "/";
  if (path === PREFIX) return { view: "index" };
  if (path.startsWith(PIPELINE_PREFIX)) {
    const pipelineId = decodeURIComponent(path.slice(PIPELINE_PREFIX.length)).split("/")[0]?.trim();
    if (pipelineId) return { view: "pipeline", pipelineId };
  }
  if (path.startsWith(`${PREFIX}/`)) {
    const slug = decodeURIComponent(path.slice(PREFIX.length + 1)).split("/")[0]?.trim();
    if (slug && slug !== "pipeline") return { view: "family", familySlug: slug };
  }
  return { view: "index" };
}

export function parseFactoryMapFocus(hash: string = typeof window !== "undefined" ? window.location.hash : ""): FactoryMapFocus | null {
  const raw = (hash || "").replace(/^#/, "").trim();
  if (!raw) return null;
  // Ignore unrelated hashes (e.g. still=…)
  if (raw === "pools") return { kind: "pools" };
  if (raw === "curation") return { kind: "curation" };
  if (raw.startsWith("curation=")) {
    const familySlug = decodeURIComponent(raw.slice("curation=".length)).trim();
    return familySlug ? { kind: "curation", familySlug } : { kind: "curation" };
  }
  if (raw.startsWith("job=")) {
    const jobKey = decodeURIComponent(raw.slice("job=".length)).trim();
    return jobKey ? { kind: "job", jobKey } : null;
  }
  return null;
}

export function factoryMapIndexHref(opts?: { focus?: "curation"; familySlug?: string }): string {
  if (opts?.focus === "curation") {
    const slug = String(opts.familySlug || "").trim();
    return slug ? `${PREFIX}#curation=${encodeURIComponent(slug)}` : `${PREFIX}#curation`;
  }
  return PREFIX;
}

export function factoryMapFamilyHref(familySlug: string, opts?: FactoryMapFamilyFocusOpts): string {
  const base = `${PREFIX}/${encodeURIComponent(familySlug)}`;
  if (!opts?.focus) return base;
  if (opts.focus === "job") {
    const key = String(opts.jobKey || "").trim();
    return key ? `${base}#job=${encodeURIComponent(key)}` : base;
  }
  if (opts.focus === "pools") return `${base}#pools`;
  if (opts.focus === "curation") return `${base}#curation`;
  return base;
}

export function factoryMapPipelineHref(pipelineId: string): string {
  return `${PIPELINE_PREFIX}${encodeURIComponent(pipelineId)}`;
}

/** Derive family slug from a shape.yaml path when API omits family_slug. */
export function familySlugFromShapePath(shapePath?: string | null): string | null {
  if (!shapePath) return null;
  const base = shapePath.split("/").pop() || "";
  if (base.endsWith(".shape.yaml")) return base.slice(0, -".shape.yaml".length);
  if (base.endsWith(".shape.yml")) return base.slice(0, -".shape.yml".length);
  return base || null;
}

export function stepFamilySlug(step: { family_slug?: string; shape?: string }): string | null {
  return step.family_slug || familySlugFromShapePath(step.shape);
}

export function pipelineFamilySlugs(pipeline: { steps?: Array<{ family_slug?: string; shape?: string }> }): string[] {
  const slugs: string[] = [];
  for (const step of pipeline.steps || []) {
    const slug = stepFamilySlug(step);
    if (slug && !slugs.includes(slug)) slugs.push(slug);
  }
  return slugs;
}
