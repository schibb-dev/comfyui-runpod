import React, { useEffect, useState } from "react";
import { comfyHealthIsBackoff, comfyHealthRetryRemaining, comfyHealthSummary } from "./comfyHealth";
import type { ComfyHealthStatus } from "./types";

export function useComfyHealthRetrySec(health?: ComfyHealthStatus | null): number {
  const [sec, setSec] = useState(() => comfyHealthRetryRemaining(health));
  useEffect(() => {
    const tick = () => setSec(comfyHealthRetryRemaining(health));
    tick();
    if (!comfyHealthIsBackoff(health)) return;
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [health, health?.next_probe_at, health?.retry_in_sec, health?.status, health?.ok]);
  return sec;
}

export function ComfyHealthBanner({
  health,
  className,
}: {
  health?: ComfyHealthStatus | null;
  className?: string;
}) {
  const retrySec = useComfyHealthRetrySec(health);
  if (!comfyHealthIsBackoff(health)) return null;
  const summary = comfyHealthSummary(health, retrySec);
  const detail = String(health?.error || "").trim();
  return (
    <div
      className={`comfy-health-banner${className ? ` ${className}` : ""}`}
      role="status"
      title={detail || summary}
    >
      <span className="comfy-health-banner__label">Comfy backoff</span>
      <span className="comfy-health-banner__text">{summary}</span>
      {detail ? <span className="comfy-health-banner__detail">{detail}</span> : null}
    </div>
  );
}
