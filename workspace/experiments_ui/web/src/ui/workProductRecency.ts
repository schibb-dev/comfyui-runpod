import type { WorkProductItem } from "./types";

export function isCompletedWorkProduct(item: WorkProductItem): boolean {
  const s = String(item.status || "").toLowerCase().trim();
  return s === "complete" || s === "completed" || s === "deposited";
}

function parseStampMs(raw?: string | null): number {
  if (!raw) return 0;
  const t = Date.parse(raw);
  return Number.isFinite(t) ? t : 0;
}

/** Clock for age + sort: video generated time, never deposit/heal stamps. */
export function recencyStamp(item: WorkProductItem): string | null {
  const generated = String(item.finished_at || "").trim();
  if (generated) return generated;
  if (isCompletedWorkProduct(item)) {
    return item.submitted_at || item.created_at || null;
  }
  return item.submitted_at || item.created_at || null;
}

export function recencyMs(item: WorkProductItem): number {
  return parseStampMs(recencyStamp(item));
}
