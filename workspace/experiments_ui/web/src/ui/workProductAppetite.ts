import { peekAssetRatings } from "./assetRatingsCache";
import type { Appetite, WorkProductItem } from "./types";
import { isCompletedWorkProduct } from "./workProductRecency";

export function normalizeAppetiteRelpath(raw: string | null | undefined): string {
  return String(raw || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
}

export const APPETITE_FILTER_KEYS = ["unset", "less", "neutral", "more", "fast_track", "remove"] as const;
export type AppetiteFilterKey = (typeof APPETITE_FILTER_KEYS)[number];

export const APPETITE_FILTER_LABEL: Record<AppetiteFilterKey, string> = {
  unset: "unset",
  less: "less",
  neutral: "neutral",
  more: "more",
  fast_track: "fast-track",
  remove: "remove",
};

/** Unset first — the usual hunt is “what still needs a mark”. */
const APPETITE_SORT_RANK: Record<AppetiteFilterKey, number> = {
  unset: 0,
  less: 1,
  neutral: 2,
  more: 3,
  fast_track: 4,
  remove: 5,
};

const ASSIGNED_APPETITE_KEYS = APPETITE_FILTER_KEYS.filter((k) => k !== "unset");

/** In-flight / failed rows sort after the appetite-review queue. */
const APPETITE_SORT_RANK_NOT_REVIEWABLE = 6;

export function isAppetiteValue(raw: string): raw is Appetite {
  return raw === "less" || raw === "neutral" || raw === "more" || raw === "fast_track" || raw === "remove";
}

export function workProductAppetiteRelpath(item: WorkProductItem): string {
  return normalizeAppetiteRelpath(item.output_relpath);
}

export function workProductAssignedAppetite(item: WorkProductItem): Appetite | null {
  const rel = workProductAppetiteRelpath(item);
  if (!rel) return null;
  const raw = String(peekAssetRatings(rel)?.appetite || "");
  return isAppetiteValue(raw) ? raw : null;
}

/**
 * Chip bucket for a row. ``unset`` is completed-and-unmarked only — ready for
 * appetite review. In-flight jobs are ``null`` so they do not inflate that count.
 */
export function workProductAppetiteKey(item: WorkProductItem): AppetiteFilterKey | null {
  const assigned = workProductAssignedAppetite(item);
  if (assigned) return assigned;
  if (isCompletedWorkProduct(item)) return "unset";
  return null;
}

export function appetiteSortRank(item: WorkProductItem): number {
  const key = workProductAppetiteKey(item);
  if (key == null) return APPETITE_SORT_RANK_NOT_REVIEWABLE;
  return APPETITE_SORT_RANK[key];
}

/** True when the only appetite chip still on is ``unset`` (solo / review queue). */
function appetiteFilterIsUnsetOnly(appetiteOff: ReadonlySet<string>): boolean {
  return !appetiteOff.has("unset") && ASSIGNED_APPETITE_KEYS.every((k) => appetiteOff.has(k));
}

export function filterWorkProductsByAppetite(
  items: WorkProductItem[],
  appetiteOff: Set<string>,
): WorkProductItem[] {
  if (!appetiteOff.size) return items;
  const unsetOnly = appetiteFilterIsUnsetOnly(appetiteOff);
  return items.filter((it) => {
    const key = workProductAppetiteKey(it);
    if (key != null) return !appetiteOff.has(key);
    return !unsetOnly;
  });
}
