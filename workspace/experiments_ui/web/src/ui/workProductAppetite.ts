import { peekAssetRatings } from "./assetRatingsCache";
import type { Appetite, WorkProductItem } from "./types";

export function normalizeAppetiteRelpath(raw: string | null | undefined): string {
  return String(raw || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
}

export const APPETITE_FILTER_KEYS = ["unset", "less", "neutral", "more", "fast_track"] as const;
export type AppetiteFilterKey = (typeof APPETITE_FILTER_KEYS)[number];

export const APPETITE_FILTER_LABEL: Record<AppetiteFilterKey, string> = {
  unset: "unset",
  less: "less",
  neutral: "neutral",
  more: "more",
  fast_track: "fast-track",
};

/** Unset first — the usual hunt is “what still needs a mark”. */
const APPETITE_SORT_RANK: Record<AppetiteFilterKey, number> = {
  unset: 0,
  less: 1,
  neutral: 2,
  more: 3,
  fast_track: 4,
};

export function workProductAppetiteRelpath(item: WorkProductItem): string {
  return normalizeAppetiteRelpath(item.output_relpath);
}

export function workProductAppetiteKey(item: WorkProductItem): AppetiteFilterKey {
  const rel = workProductAppetiteRelpath(item);
  if (!rel) return "unset";
  const raw = peekAssetRatings(rel)?.appetite;
  if (raw === "less" || raw === "neutral" || raw === "more" || raw === "fast_track") return raw;
  return "unset";
}

export function appetiteSortRank(item: WorkProductItem): number {
  return APPETITE_SORT_RANK[workProductAppetiteKey(item)];
}

export function filterWorkProductsByAppetite(
  items: WorkProductItem[],
  appetiteOff: Set<string>,
): WorkProductItem[] {
  if (!appetiteOff.size) return items;
  return items.filter((it) => !appetiteOff.has(workProductAppetiteKey(it)));
}

export function isAppetiteValue(raw: string): raw is Appetite {
  return raw === "less" || raw === "neutral" || raw === "more" || raw === "fast_track";
}
