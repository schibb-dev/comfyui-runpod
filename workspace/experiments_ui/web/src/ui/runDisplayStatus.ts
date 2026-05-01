import type { QueueResponse, RunDisplayStatus, RunsItem } from "./types";

/**
 * Derive display status for a run using queue data (running vs pending).
 * - finished: status === "complete"
 * - waiting: status === "not_submitted"
 * - queued: submitted and prompt_id in comfyui.pending
 * - in_process: submitted and prompt_id in comfyui.running
 */
export function getRunDisplayStatus(run: RunsItem, queue: QueueResponse | null): RunDisplayStatus {
  if (run.status === "complete") return "finished";
  if (run.status === "not_submitted") return "waiting";
  const promptId = run.prompt_id ?? null;
  if (!promptId || !queue?.comfyui) return "waiting"; // submitted but not in queue
  const running = queue.comfyui.running ?? [];
  const pending = queue.comfyui.pending ?? [];
  if (running.some((x) => x.prompt_id === promptId)) return "in_process";
  if (pending.some((x) => x.prompt_id === promptId)) return "queued";
  return "waiting"; // submitted, queue may have been cleared
}

export const RUN_DISPLAY_LABELS: Record<RunDisplayStatus, string> = {
  finished: "Finished",
  waiting: "Waiting",
  queued: "Queued (ComfyUI)",
  in_process: "In process (ComfyUI)",
};
