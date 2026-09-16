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

export function queueComfyItemTitle(item: Pick<QueueComfyItem, "glance" | "input_media_relpath" | "workflow_name" | "prompt_id">): string {
  const family = String(item.glance?.family_slug || "").trim();
  return (
    family ||
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
