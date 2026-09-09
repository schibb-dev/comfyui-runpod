import type { WorkProductItem } from "./types";

export type SubmitWhen = "queue" | "queue_next" | "now" | "later";

export function isPendingQueueItem(item: WorkProductItem): boolean {
  const pid = String(item.prompt_id || "").trim();
  if (pid) return false;
  const s = String(item.status || "").toLowerCase().trim();
  if (s === "queued" || s === "running" || s === "submitted" || s === "complete" || s === "completed" || s === "abandoned") {
    return false;
  }
  // Reorder is scheduling-only. Error/failed/deposited stay out of the operator FIFO
  // even when the factory still stamps a held pending_rank on those files.
  return s === "pending" || s === "editing" || s === "draft" || !s;
}

export function pendingQueueIndex(item: WorkProductItem): number {
  if (typeof item.pending_index === "number" && Number.isFinite(item.pending_index)) return item.pending_index;
  if (typeof item.pending_rank === "number" && Number.isFinite(item.pending_rank)) return item.pending_rank;
  return Number.POSITIVE_INFINITY;
}

export function destinationForWhen(when: SubmitWhen): {
  destination: "pending" | "comfy";
  pending_position?: "append" | "front";
  front: boolean;
} {
  if (when === "queue") return { destination: "pending", pending_position: "append", front: false };
  if (when === "queue_next") return { destination: "pending", pending_position: "front", front: false };
  return { destination: "comfy", front: when === "now" };
}

export function submitWhenLabel(when: SubmitWhen): string {
  if (when === "queue") return "Queue";
  if (when === "queue_next") return "Next";
  if (when === "now") return "Now";
  return "Later";
}
