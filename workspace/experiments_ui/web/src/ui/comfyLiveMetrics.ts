import type { ComfyLiveStatusItem } from "./types";

export function liveTimingParts(
  status: ComfyLiveStatusItem | null,
  nowTick: number,
  submittedAt?: string | null,
) {
  const value = status?.value;
  const max = status?.max;
  const pct =
    typeof value === "number" && typeof max === "number" && max > 0
      ? Math.max(0, Math.min(100, Math.round((value / max) * 100)))
      : null;

  const elapsedClient =
    status?.started_at != null
      ? Math.max(0, nowTick / 1000 - status.started_at)
      : submittedAt
        ? Math.max(0, (nowTick - Date.parse(submittedAt)) / 1000)
        : status?.elapsed_s ?? null;
  const eta =
    status?.eta_s != null
      ? status.eta_s
      : typeof value === "number" && typeof max === "number" && value > 0 && max > value && elapsedClient != null
        ? (elapsedClient * (max - value)) / value
        : null;

  const running =
    status?.status === "running" ||
    (status?.finished_at == null && (status?.value != null || Boolean(status?.node)));

  return { value, max, pct, elapsedClient, eta, running };
}

/** Human step label for live progress (prefer workflow title over raw node id). */
export function liveStepLabel(status: ComfyLiveStatusItem | null | undefined): string | null {
  const title = String(status?.node_title || "").trim();
  if (title) return title;
  const node = String(status?.node || "").trim();
  if (node) return `node ${node}`;
  return null;
}

/** Clock-style duration: `m:ss`, or `h:mm:ss` when over an hour. */
export function formatDurationMmSs(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}
