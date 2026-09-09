import type { ComfyHealthStatus } from "./types";

export function comfyHealthIsBackoff(health?: ComfyHealthStatus | null): boolean {
  if (!health) return false;
  return health.status === "backoff" || health.ok === false;
}

export function formatComfyRetry(sec: number | null | undefined): string {
  const n = Math.max(0, Math.round(Number(sec) || 0));
  if (n <= 0) return "now";
  if (n < 60) return `${n}s`;
  const m = Math.floor(n / 60);
  const s = n % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

export function comfyHealthRetryRemaining(health?: ComfyHealthStatus | null, nowMs = Date.now()): number {
  if (!comfyHealthIsBackoff(health)) return 0;
  const until = health?.next_probe_at ? Date.parse(health.next_probe_at) : Number.NaN;
  if (Number.isFinite(until)) {
    return Math.max(0, Math.ceil((until - nowMs) / 1000));
  }
  return Math.max(0, Math.round(Number(health?.retry_in_sec) || 0));
}

export function comfyHealthSummary(health?: ComfyHealthStatus | null, retrySec?: number): string {
  if (!comfyHealthIsBackoff(health)) return "";
  const remaining = retrySec ?? comfyHealthRetryRemaining(health);
  if (remaining > 0) {
    return `Comfy not ready — drain paused. Next health check in ${formatComfyRetry(remaining)}.`;
  }
  return "Comfy not ready — waiting for the next drain health check.";
}
