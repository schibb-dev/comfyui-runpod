import type { ComfyHistoryItem, QueueComfyItem, QueueJobGlance } from "./types";

export type QueueStillTagFields = {
  work_kind?: "still_tag" | null;
  display_title?: string | null;
  still_tag_run_id?: string | null;
  content_id?: string | null;
};

export function isQueueStillTagItem(
  item: Pick<QueueComfyItem, "work_kind" | "glance"> | Pick<ComfyHistoryItem, "work_kind" | "glance">,
): boolean {
  if (item.work_kind === "still_tag") return true;
  return String(item.glance?.workflow_kind || "").trim().toLowerCase() === "still_tag";
}

export function queueStillTagTitle(item: QueueStillTagFields & Pick<QueueComfyItem, "glance" | "input_media_relpath" | "workflow_name" | "prompt_id">): string {
  const fromApi = String(item.display_title || "").trim();
  if (fromApi) return fromApi;
  const progress = String(item.glance?.step || "").trim();
  return progress ? `Still tag · ${progress}` : "Still tag";
}

export function queueStillTagStatusLabel(kind: "running" | "waiting" | "history", status?: string | null): string {
  const s = String(status || "").toLowerCase();
  if (kind === "running") return "tagging";
  if (kind === "waiting") return "queued";
  if (s === "error" || s === "failed") return "error";
  if (s === "interrupted") return "interrupted";
  if (s === "success" || s === "complete" || s === "completed") return "done";
  return s || "still tag";
}

export function queueStillTagGlanceRows(item: {
  still_tag_run_id?: string | null;
  content_id?: string | null;
  glance?: QueueJobGlance | null;
}): Array<{ key: string; label: string; value: string; title?: string }> {
  const rows: Array<{ key: string; label: string; value: string; title?: string }> = [];
  const progress = String(item.glance?.step || "").trim();
  if (progress) rows.push({ key: "progress", label: "Batch", value: progress, title: "Still-tag batch progress" });
  const runId = String(item.still_tag_run_id || "").trim();
  if (runId) {
    const compact = runId.replace(/^still_tag_/, "");
    rows.push({
      key: "run",
      label: "Run",
      value: compact.length > 28 ? `${compact.slice(0, 28)}…` : compact,
      title: runId,
    });
  }
  const cid = String(item.content_id || "").trim();
  if (cid) {
    rows.push({
      key: "still",
      label: "Still",
      value: cid.length > 16 ? `${cid.slice(0, 16)}…` : cid,
      title: "Content id being tagged",
    });
  }
  return rows;
}
