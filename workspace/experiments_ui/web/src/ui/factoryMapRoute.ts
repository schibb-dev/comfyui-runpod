export type FactoryMapRoute =
  | { view: "index" }
  | { view: "family"; familySlug: string }
  | { view: "pipeline"; pipelineId: string };

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

export function factoryMapIndexHref(): string {
  return PREFIX;
}

export function factoryMapFamilyHref(familySlug: string): string {
  return `${PREFIX}/${encodeURIComponent(familySlug)}`;
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
