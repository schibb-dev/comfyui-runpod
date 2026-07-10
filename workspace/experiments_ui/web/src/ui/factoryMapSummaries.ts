import { pipelineFamilySlugs } from "./factoryMapRoute";
import type { ShapeFactoryMapFamily, ShapeFactoryMapPipeline } from "./types";

/** Pipeline wired by shape_factory_hourly.sh (not april03 replay). */
export const HOURLY_PIPELINE_ID = "fb9-gex2-to-facial";

export type FactoryMapActivity = {
  active: boolean;
  label: string;
  detail?: string;
};

export type FactoryMapIndexContext = {
  jobCountsByFamily: Record<string, Record<string, number>>;
  hourlyPhase?: string | null;
};

function inflightCount(counts: Record<string, number>): number {
  return (counts.running || 0) + (counts.queued || 0);
}

function formatInflight(counts: Record<string, number>): string | null {
  const n = inflightCount(counts);
  if (n <= 0) return null;
  const parts: string[] = [];
  if (counts.running) parts.push(`${counts.running} running`);
  if (counts.queued) parts.push(`${counts.queued} queued`);
  return parts.join(", ");
}

function hourlyPhaseDetail(phase: string): string {
  if (phase === "gex2_queued") return "hourly · gex2 step";
  if (phase === "facial_queued") return "hourly · facial step";
  if (phase === "idle") return "hourly · idle";
  return `hourly · ${phase}`;
}

function familyForHourlyPhase(phase: string): string | null {
  if (phase === "gex2_queued") return "FB9_GEX2";
  if (phase === "facial_queued") return "FB9_GEX_FACIAL";
  return null;
}

export function getFamilyActivity(
  family: ShapeFactoryMapFamily,
  ctx: FactoryMapIndexContext,
): FactoryMapActivity | null {
  const slug = family.family_slug;
  if (!slug) return null;

  const counts = ctx.jobCountsByFamily[slug] || {};
  const inflight = formatInflight(counts);
  const phase = (ctx.hourlyPhase || "").trim();
  const hourlyFamily = phase ? familyForHourlyPhase(phase) : null;

  if (hourlyFamily === slug) {
    return {
      active: true,
      label: "Active",
      detail: hourlyPhaseDetail(phase),
    };
  }

  if (inflight) {
    return {
      active: true,
      label: "In flight",
      detail: inflight,
    };
  }

  return null;
}

export function getPipelineActivity(
  pipeline: ShapeFactoryMapPipeline,
  ctx: FactoryMapIndexContext,
): FactoryMapActivity | null {
  const pipelineId = pipeline.pipeline_id || "";
  const slugs = pipelineFamilySlugs(pipeline);
  const phase = (ctx.hourlyPhase || "").trim();

  let inflightTotal = 0;
  const inflightParts: string[] = [];
  for (const slug of slugs) {
    const counts = ctx.jobCountsByFamily[slug] || {};
    const n = inflightCount(counts);
    if (n > 0) {
      inflightTotal += n;
      const bit = formatInflight(counts);
      if (bit) inflightParts.push(`${slug}: ${bit}`);
    }
  }

  const hourlyActive =
    pipelineId === HOURLY_PIPELINE_ID && (phase === "gex2_queued" || phase === "facial_queued");

  if (hourlyActive) {
    return {
      active: true,
      label: "Active",
      detail: hourlyPhaseDetail(phase),
    };
  }

  if (inflightTotal > 0) {
    return {
      active: true,
      label: "In flight",
      detail: inflightParts.join(" · ") || `${inflightTotal} jobs`,
    };
  }

  return null;
}

export function summarizeFamiliesSection(
  families: ShapeFactoryMapFamily[],
  ctx: FactoryMapIndexContext,
): string {
  const parts: string[] = [`${families.length} ${families.length === 1 ? "family" : "families"}`];

  let totalJobs = 0;
  let totalInflight = 0;
  const activeNames: string[] = [];

  for (const fam of families) {
    const slug = fam.family_slug;
    if (!slug) continue;
    const counts = ctx.jobCountsByFamily[slug] || {};
    totalJobs += Object.values(counts).reduce((a, b) => a + b, 0);
    totalInflight += inflightCount(counts);
    const activity = getFamilyActivity(fam, ctx);
    if (activity?.active) activeNames.push(slug);
  }

  if (totalJobs > 0) parts.push(`${totalJobs} jobs`);
  if (totalInflight > 0) parts.push(`${totalInflight} in flight`);
  if (activeNames.length) parts.push(`${activeNames.join(", ")} active`);

  return parts.join(" · ");
}

export function summarizePipelinesSection(
  pipelines: ShapeFactoryMapPipeline[],
  ctx: FactoryMapIndexContext,
): string {
  const parts: string[] = [`${pipelines.length} ${pipelines.length === 1 ? "pipeline" : "pipelines"}`];

  const activeIds: string[] = [];
  for (const pipe of pipelines) {
    const activity = getPipelineActivity(pipe, ctx);
    if (activity?.active && pipe.pipeline_id) {
      activeIds.push(pipe.pipeline_id);
    }
  }

  if (activeIds.length) {
    parts.push(`${activeIds.join(", ")} active`);
  } else {
    parts.push("none active");
  }

  return parts.join(" · ");
}
