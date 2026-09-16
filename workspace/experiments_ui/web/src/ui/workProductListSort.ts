import type { WorkProductItem } from "./types";
import { compareQueueIndex } from "./queueMonitorSort";
import { pendingQueueIndex } from "./workProductPendingQueue";
import { recencyMs } from "./workProductRecency";

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

/** Sort live Comfy rows by queue number (next to run first). */
export function compareLiveComfyQueue(
  a: Pick<WorkProductItem, "queue_index" | "created_at" | "submitted_at" | "job_key">,
  b: Pick<WorkProductItem, "queue_index" | "created_at" | "submitted_at" | "job_key">,
): number {
  const byIndex = compareQueueIndex(a, b);
  if (byIndex !== 0) return byIndex;
  return recencyMs(a) - recencyMs(b);
}

export type WorkProductCompletedSort =
  | "created_desc"
  | "created_asc"
  | "appetite"
  | "family_asc"
  | "family_desc"
  | "status"
  | "pick_mode";

/** Running → queued (Comfy order) → pending FIFO → errors → completed (operator sort). */
export function sortWorkProductList(
  items: WorkProductItem[],
  completedSort: WorkProductCompletedSort,
  appetiteSortRank: (item: WorkProductItem) => number,
): WorkProductItem[] {
  const running: WorkProductItem[] = [];
  const queued: WorkProductItem[] = [];
  const pending: WorkProductItem[] = [];
  const errored: WorkProductItem[] = [];
  const done: WorkProductItem[] = [];
  for (const it of items) {
    const bucket = workProductListBucket(it.status);
    if (bucket === "running") running.push(it);
    else if (bucket === "queued") queued.push(it);
    else if (bucket === "pending") pending.push(it);
    else if (bucket === "error") errored.push(it);
    else done.push(it);
  }

  const byRecentFirst = (a: WorkProductItem, b: WorkProductItem) => recencyMs(b) - recencyMs(a);
  const cmp = (a: WorkProductItem, b: WorkProductItem): number => {
    switch (completedSort) {
      case "created_asc":
        return recencyMs(a) - recencyMs(b);
      case "family_asc":
        return String(a.family_slug || "").localeCompare(String(b.family_slug || "")) || byRecentFirst(a, b);
      case "family_desc":
        return String(b.family_slug || "").localeCompare(String(a.family_slug || "")) || byRecentFirst(a, b);
      case "status":
        return byRecentFirst(a, b);
      case "pick_mode":
        return (
          String(a.pick_mode || a.step || "").localeCompare(String(b.pick_mode || b.step || "")) ||
          byRecentFirst(a, b)
        );
      case "appetite":
        return appetiteSortRank(a) - appetiteSortRank(b) || byRecentFirst(a, b);
      case "created_desc":
      default:
        return byRecentFirst(a, b);
    }
  };

  running.sort(compareLiveComfyQueue);
  queued.sort(compareLiveComfyQueue);
  pending.sort((a, b) => pendingQueueIndex(a) - pendingQueueIndex(b) || recencyMs(a) - recencyMs(b));
  errored.sort(byRecentFirst);
  done.sort(cmp);
  return [...running, ...queued, ...pending, ...errored, ...done];
}
