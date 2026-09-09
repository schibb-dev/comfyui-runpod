import type { WorkProductItem } from "./types";

/** Workbench list buckets. Failures are their own scheduling surface. */
export type WorkProductListBucket = "running" | "queued" | "pending" | "error" | "done";

/** Jobs-index sections. Queue/live stays lean; pending is the scheduling surface. */
export type WorkProductNavSectionId = "live" | "pending" | "error" | "done";

export type WorkProductNavSectionDef = {
  id: WorkProductNavSectionId;
  label: string;
  title: string;
};

const BUCKET_RANK: Record<WorkProductListBucket, number> = {
  running: 0,
  queued: 1,
  pending: 2,
  error: 3,
  done: 4,
};

export const WORK_PRODUCT_NAV_SECTIONS: readonly WorkProductNavSectionDef[] = [
  {
    id: "live",
    label: "Comfy Queue",
    title: "Jobs currently on Comfy (running or waiting). Keep this short — schedule from Pending.",
  },
  {
    id: "pending",
    label: "Pending",
    title: "Factory pending FIFO. Hourly planner vs custom (Workbench/Submit). Reorder here; drain submits overflow to Comfy.",
  },
  {
    id: "error",
    label: "Errors",
    title: "Failed, interrupted, or abandoned jobs. Fix here — retry the same job or replay a successor.",
  },
  {
    id: "done",
    label: "Completed",
    title: "Finished renders.",
  },
];

export function workProductListBucket(status?: string | null): WorkProductListBucket {
  const s = String(status || "").toLowerCase().trim();
  if (s === "running") return "running";
  if (s === "queued" || s === "submitted") return "queued";
  if (!s || s === "pending" || s === "editing" || s === "draft") return "pending";
  if (s === "error" || s === "failed" || s === "interrupted" || s === "abandoned") return "error";
  return "done";
}

export function workProductListSortRank(item: Pick<WorkProductItem, "status">): number {
  return BUCKET_RANK[workProductListBucket(item.status)];
}

export function workProductNavSection(status?: string | null): WorkProductNavSectionId {
  const bucket = workProductListBucket(status);
  if (bucket === "running" || bucket === "queued") return "live";
  if (bucket === "pending") return "pending";
  if (bucket === "error") return "error";
  return "done";
}

export function groupWorkProductsByNavSection<T extends Pick<WorkProductItem, "status">>(
  items: T[],
): Array<WorkProductNavSectionDef & { items: T[] }> {
  const buckets: Record<WorkProductNavSectionId, T[]> = { live: [], pending: [], error: [], done: [] };
  for (const it of items) {
    buckets[workProductNavSection(it.status)].push(it);
  }
  return WORK_PRODUCT_NAV_SECTIONS.map((sec) => ({
    ...sec,
    items: buckets[sec.id],
  })).filter((sec) => sec.items.length > 0);
}

export function workProductsInOpenNavSections<T extends Pick<WorkProductItem, "status">>(
  items: T[],
  open: Record<WorkProductNavSectionId, boolean>,
): T[] {
  return items.filter((it) => open[workProductNavSection(it.status)]);
}

export type WorkProductNavBadge = {
  key: string;
  count: number;
  label: string;
  tone: "running" | "queued" | "pending" | "hourly" | "custom" | "editing" | "ok" | "error";
};

type NavBadgeItem = Pick<WorkProductItem, "status"> & {
  is_hourly?: boolean;
  job_key?: string;
};

export function isHourlyWorkProduct(item: { is_hourly?: boolean; job_key?: string | null }): boolean {
  if (item.is_hourly === true) return true;
  if (item.is_hourly === false) return false;
  return String(item.job_key || "").startsWith("hourly__");
}

function statusKey(status?: string | null): string {
  return String(status || "").toLowerCase().trim();
}

/** Header chips: omit empty parts; labels only when a section mixes kinds. */
export function workProductNavSectionBadges(
  id: WorkProductNavSectionId,
  items: NavBadgeItem[],
): WorkProductNavBadge[] {
  if (id === "live") {
    let running = 0;
    let queued = 0;
    for (const it of items) {
      if (workProductListBucket(it.status) === "running") running += 1;
      else queued += 1;
    }
    const badges: WorkProductNavBadge[] = [];
    if (running) badges.push({ key: "running", count: running, label: "live", tone: "running" });
    if (queued) badges.push({ key: "queued", count: queued, label: "queued", tone: "queued" });
    return badges;
  }
  if (id === "pending") {
    let hourly = 0;
    let custom = 0;
    for (const it of items) {
      if (isHourlyWorkProduct(it)) hourly += 1;
      else custom += 1;
    }
    const badges: WorkProductNavBadge[] = [];
    if (hourly) badges.push({ key: "hourly", count: hourly, label: "hourly", tone: "hourly" });
    if (custom) badges.push({ key: "custom", count: custom, label: "custom", tone: "custom" });
    return badges;
  }
  if (id === "error") {
    let interrupted = 0;
    let failed = 0;
    for (const it of items) {
      const s = statusKey(it.status);
      if (s === "interrupted") interrupted += 1;
      else failed += 1;
    }
    const badges: WorkProductNavBadge[] = [];
    if (failed) badges.push({ key: "error", count: failed, label: interrupted ? "err" : "", tone: "error" });
    if (interrupted) {
      badges.push({ key: "interrupted", count: interrupted, label: "int", tone: "error" });
    }
    return badges;
  }
  const badges: WorkProductNavBadge[] = [];
  if (items.length) badges.push({ key: "ok", count: items.length, label: "", tone: "ok" });
  return badges;
}
