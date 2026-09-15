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
