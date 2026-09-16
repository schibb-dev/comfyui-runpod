/** Comfy Queue monitor — live (running/waiting) vs history sort modes. */

export type QueueLiveSortMode = "queue_index" | "newest" | "oldest";
export type QueueHistorySortMode = "newest" | "oldest" | "errors_first" | "queue_index";

export function isHistoryProblem(item: { status?: string | null }): boolean {
  const s = String(item.status || "").toLowerCase();
  return s === "error" || s === "failed" || s === "interrupted" || s === "no output";
}

function itemSortKeyChanged(item: {
  changed_at?: string | null;
  queued_at?: string | null;
  queue_index?: number | null;
}): number {
  const iso = item.changed_at || item.queued_at;
  if (iso) {
    const t = Date.parse(iso);
    if (!Number.isNaN(t)) return t;
  }
  if (typeof item.queue_index === "number") return item.queue_index;
  return 0;
}

/** Ascending Comfy queue number — lower / more negative runs sooner. */
export function compareQueueIndex(
  a: { queue_index?: number | null },
  b: { queue_index?: number | null },
): number {
  const ai = typeof a.queue_index === "number" ? a.queue_index : Number.POSITIVE_INFINITY;
  const bi = typeof b.queue_index === "number" ? b.queue_index : Number.POSITIVE_INFINITY;
  return ai - bi;
}

export function sortQueueLiveItems<
  T extends { changed_at?: string | null; queued_at?: string | null; queue_index?: number | null },
>(items: T[], mode: QueueLiveSortMode): T[] {
  const copy = items.slice();
  copy.sort((a, b) => {
    if (mode === "queue_index") return compareQueueIndex(a, b);
    if (mode === "oldest") return itemSortKeyChanged(a) - itemSortKeyChanged(b);
    return itemSortKeyChanged(b) - itemSortKeyChanged(a);
  });
  return copy;
}

export function sortQueueHistoryItems<
  T extends {
    changed_at?: string | null;
    queued_at?: string | null;
    queue_index?: number | null;
    status?: string;
  },
>(items: T[], mode: QueueHistorySortMode): T[] {
  const copy = items.slice();
  copy.sort((a, b) => {
    if (mode === "errors_first") {
      const ae = isHistoryProblem(a) ? 0 : 1;
      const be = isHistoryProblem(b) ? 0 : 1;
      if (ae !== be) return ae - be;
      return itemSortKeyChanged(b) - itemSortKeyChanged(a);
    }
    if (mode === "queue_index") return compareQueueIndex(a, b);
    if (mode === "oldest") return itemSortKeyChanged(a) - itemSortKeyChanged(b);
    return itemSortKeyChanged(b) - itemSortKeyChanged(a);
  });
  return copy;
}
