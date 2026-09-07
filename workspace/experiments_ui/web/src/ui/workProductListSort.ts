import type { WorkProductItem } from "./types";

/** Workbench list buckets. Complete and all failure states share ``done``. */
export type WorkProductListBucket = "running" | "queued" | "pending" | "done";

const BUCKET_RANK: Record<WorkProductListBucket, number> = {
  running: 0,
  queued: 1,
  pending: 2,
  done: 3,
};

export function workProductListBucket(status?: string | null): WorkProductListBucket {
  const s = String(status || "").toLowerCase().trim();
  if (s === "running") return "running";
  if (s === "queued" || s === "submitted") return "queued";
  if (!s || s === "pending" || s === "editing" || s === "draft") return "pending";
  return "done";
}

export function workProductListSortRank(item: Pick<WorkProductItem, "status">): number {
  return BUCKET_RANK[workProductListBucket(item.status)];
}
