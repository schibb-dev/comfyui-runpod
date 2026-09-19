import type { ComfyHistoryItem, QueueComfyItem } from "./types";

export type QueueMonitorSectionId = "running" | "pending" | "history";

function basename(path: string | null | undefined): string {
  const raw = String(path || "")
    .trim()
    .replace(/\\/g, "/");
  if (!raw) return "";
  const parts = raw.split("/");
  return parts[parts.length - 1] || raw;
}

function shortId(value: string | null | undefined, max = 14): string {
  const s = String(value || "").trim();
  if (!s) return "—";
  return s.length <= max ? s : `${s.slice(0, max)}…`;
}

export function queueItemFocusKey(item: { prompt_id?: string | null; job_key?: string | null }): string {
  return `${String(item.prompt_id || "").trim()}|${String(item.job_key || "").trim()}`;
}

export type QueueSwipeSection = "running" | "waiting" | "history";

export type QueueSwipeEntry =
  | { key: string; section: "running" | "waiting"; item: QueueComfyItem }
  | { key: string; section: "history"; item: ComfyHistoryItem };

export function queueSwipeSectionLabel(section: QueueSwipeSection | null | undefined): string {
  if (section === "running") return "Running";
  if (section === "waiting") return "Waiting";
  if (section === "history") return "History";
  return "Queue";
}

/** Keep vertical swipe inside the section the user opened from. */
export function filterQueueSwipeEntries(
  entries: QueueSwipeEntry[],
  section: QueueSwipeSection | null | undefined,
): QueueSwipeEntry[] {
  if (!section) return entries;
  return entries.filter((entry) => entry.section === section);
}

/** Running → waiting → history, then optionally one section. */
export function buildQueueSwipeEntries(
  running: QueueComfyItem[],
  waiting: QueueComfyItem[],
  history: ComfyHistoryItem[],
  section?: QueueSwipeSection | null,
): QueueSwipeEntry[] {
  const out: QueueSwipeEntry[] = [];
  const seen = new Set<string>();
  const push = (entry: QueueSwipeEntry) => {
    if (!entry.key || seen.has(entry.key)) return;
    seen.add(entry.key);
    out.push(entry);
  };
  for (const item of running) push({ key: queueItemFocusKey(item), section: "running", item });
  for (const item of waiting) push({ key: queueItemFocusKey(item), section: "waiting", item });
  for (const item of history) push({ key: queueItemFocusKey(item), section: "history", item });
  return filterQueueSwipeEntries(out, section);
}

/** Page under the current scroll position. */
export function queueSwipePageIndex(scrollTop: number, pageHeight: number, count: number): number {
  if (count <= 0) return 0;
  const h = pageHeight > 0 ? pageHeight : 1;
  return Math.max(0, Math.min(count - 1, Math.round(scrollTop / h)));
}

/** Modest swipe-up commits to the next page; never leave a half-step. */
export function queueSwipeCommitIndex(
  currentIndex: number,
  count: number,
  dy: number,
  pageHeight: number,
): number {
  if (count <= 0) return 0;
  const current = Math.max(0, Math.min(count - 1, currentIndex));
  const commit = Math.min(48, Math.max(28, pageHeight * 0.08));
  if (dy <= -commit) return Math.min(count - 1, current + 1);
  if (dy >= commit) return Math.max(0, current - 1);
  return current;
}

/** Full decode for the focused job and neighbors; skip the rest. */
export function queueFocusDetailMode(index: number, focusIndex: number): "full" | "off" {
  const focus = focusIndex >= 0 ? focusIndex : 0;
  return Math.abs(index - focus) <= 1 ? "full" : "off";
}

export function queueComfyItemTitle(
  item: Pick<
    QueueComfyItem,
    "glance" | "input_media_relpath" | "workflow_name" | "prompt_id" | "work_kind" | "display_title"
  >,
): string {
  const displayTitle = String(item.display_title || "").trim();
  if (displayTitle) return displayTitle;
  const family = String(item.glance?.family_slug || "").trim();
  if (family && family !== "still-tag") return family;
  if (String(item.glance?.workflow_kind || "").trim().toLowerCase() === "still_tag" || item.work_kind === "still_tag") {
    const progress = String(item.glance?.step || "").trim();
    return progress ? `Still tag · ${progress}` : "Still tag";
  }
  return (
    basename(item.input_media_relpath) ||
    String(item.workflow_name || "").trim() ||
    shortId(item.prompt_id, 16)
  );
}

export function queueRunningSectionHint(items: QueueComfyItem[]): string {
  if (!items.length) return "Idle — nothing executing on GPU";
  const title = queueComfyItemTitle(items[0]);
  return `Active now · ${title}`;
}

export function queueWaitingSectionHint(items: QueueComfyItem[]): string {
  if (!items.length) return "Empty — submit or drain factory pending to fill";
  const title = queueComfyItemTitle(items[0]);
  const external = items.filter((it) => it.external).length;
  const externalBit = external ? ` · ${external} non-factory` : "";
  return `${items.length} waiting · next: ${title}${externalBit}`;
}

export function queueHistorySectionHint(
  items: ComfyHistoryItem[],
  errorCount: number,
  errorsOnly: boolean,
): string {
  if (errorsOnly) {
    return items.length
      ? `${items.length} failed or interrupted in recent history`
      : "No errors in recent history";
  }
  if (!items.length) return "No recent Comfy finishes yet";
  if (errorCount > 0) return `${items.length} recent · ${errorCount} failed`;
  const title =
    queueComfyItemTitle(items[0]) ||
    basename(items[0].primary_video_relpath || items[0].primary_image_relpath) ||
    shortId(items[0].prompt_id, 12);
  return `${items.length} recent · newest: ${title}`;
}
