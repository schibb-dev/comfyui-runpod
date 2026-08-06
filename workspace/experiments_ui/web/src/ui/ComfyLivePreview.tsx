import React, { useEffect, useRef, useState } from "react";
import { comfyLivePreviewUrl, fetchComfyLiveStatus } from "./api";
import type { ComfyLiveStatusItem } from "./types";

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const s = Math.floor(seconds);
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m <= 0) return `${r}s`;
  return `${m}m ${r.toString().padStart(2, "0")}s`;
}

function useComfyLiveStatus(promptId: string) {
  const [status, setStatus] = useState<ComfyLiveStatusItem | null>(null);
  const [nowTick, setNowTick] = useState(() => Date.now());

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      if (cancelled) return;
      void fetchComfyLiveStatus([promptId])
        .then((res) => {
          if (cancelled) return;
          const item = (res.items || []).find((x) => x.prompt_id === promptId) || null;
          setStatus(item);
        })
        .catch(() => {
          /* ignore transient bridge errors while polling */
        });
    };
    tick();
    const id = window.setInterval(tick, 750);
    const clock = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      window.clearInterval(clock);
    };
  }, [promptId]);

  return { status, nowTick };
}

function liveTimingParts(
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

  return { value, max, pct, elapsedClient, eta };
}

/** Elapsed / ETA / progress — for the bottom of a row header. */
export function ComfyLiveMetricsBar({
  promptId,
  submittedAt,
  className,
}: {
  promptId: string;
  submittedAt?: string | null;
  className?: string;
}) {
  const { status, nowTick } = useComfyLiveStatus(promptId);
  const { value, max, pct, elapsedClient, eta } = liveTimingParts(status, nowTick, submittedAt);

  return (
    <div className={["work-product-live__metrics", className].filter(Boolean).join(" ")}>
      <div className="work-product-live__timing" title={status?.node ? `node ${status.node}` : undefined}>
        <span>Elapsed {formatDuration(elapsedClient)}</span>
        <span>ETA {eta != null ? `~${formatDuration(eta)}` : "—"}</span>
        {pct != null ? (
          <span>
            {value}/{max}
          </span>
        ) : status?.status ? (
          <span>{status.status}</span>
        ) : null}
      </div>
      {pct != null ? (
        <div
          className="work-product-live__progress"
          title={`${value}/${max}${status?.node ? ` · node ${status.node}` : ""}`}
        >
          <div className="work-product-live__bar" style={{ width: `${pct}%` }} />
          <span className="work-product-live__prog-label">
            {value}/{max}
            {status?.node ? ` · ${status.node}` : ""}
          </span>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Live Comfy latent/VHS preview for a running prompt_id — shared by Workbench and Queue.
 * Pass showMetrics={false} when ComfyLiveMetricsBar is rendered in the row header.
 */
export function ComfyLivePreview({
  promptId,
  submittedAt,
  className,
  showMetrics = true,
}: {
  promptId: string;
  submittedAt?: string | null;
  className?: string;
  showMetrics?: boolean;
}) {
  const [bust, setBust] = useState(() => Date.now());
  const [hasFrame, setHasFrame] = useState(false);
  const { status, nowTick } = useComfyLiveStatus(promptId);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameUrlsRef = useRef<Map<number, string>>(new Map());
  const frameImgsRef = useRef<Map<number, HTMLImageElement>>(new Map());
  const animRef = useRef<number | null>(null);
  const lastFetchRef = useRef(0);

  useEffect(() => {
    if (status?.has_preview) {
      setHasFrame(true);
      setBust(Date.now());
    }
  }, [status?.has_preview, status?.updated_at]);

  useEffect(() => {
    let cancelled = false;
    const ready = status?.frames_ready || [];
    const updatedAt = status?.updated_at || 0;
    if (!ready.length) return;

    const fetchFrame = async (idx: number) => {
      try {
        const r = await fetch(comfyLivePreviewUrl(promptId, `${updatedAt}-${idx}`, idx));
        if (!r.ok || cancelled) return;
        const blob = await r.blob();
        if (cancelled || blob.size < 1) return;
        const prev = frameUrlsRef.current.get(idx);
        if (prev) URL.revokeObjectURL(prev);
        const url = URL.createObjectURL(blob);
        frameUrlsRef.current.set(idx, url);
        const img = new Image();
        img.src = url;
        frameImgsRef.current.set(idx, img);
        setHasFrame(true);
      } catch {
        /* ignore */
      }
    };

    if (updatedAt && updatedAt === lastFetchRef.current) {
      for (const idx of ready) {
        if (!frameUrlsRef.current.has(idx)) void fetchFrame(idx);
      }
    } else {
      lastFetchRef.current = updatedAt || Date.now() / 1000;
      for (const idx of ready) void fetchFrame(idx);
    }

    return () => {
      cancelled = true;
    };
  }, [promptId, status?.updated_at, status?.frames_count, (status?.frames_ready || []).join(",")]);

  useEffect(() => {
    return () => {
      for (const url of frameUrlsRef.current.values()) URL.revokeObjectURL(url);
      frameUrlsRef.current.clear();
      frameImgsRef.current.clear();
      if (animRef.current != null) cancelAnimationFrame(animRef.current);
    };
  }, [promptId]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rate = status?.vhs_rate && status.vhs_rate > 0 ? status.vhs_rate : 8;
    const length = Math.max(status?.vhs_length || 0, status?.frames_count || 0);
    if (length < 2 && (status?.frames_count || 0) < 2) {
      if (animRef.current != null) {
        cancelAnimationFrame(animRef.current);
        animRef.current = null;
      }
      return;
    }

    let cancelled = false;
    const start = performance.now();
    const draw = (t: number) => {
      if (cancelled) return;
      const imgs = frameImgsRef.current;
      const keys = [...imgs.keys()].sort((a, b) => a - b);
      if (keys.length) {
        const span = Math.max(length, keys[keys.length - 1] + 1);
        const idx = Math.floor(((t - start) / 1000) * rate) % span;
        let img = imgs.get(idx);
        if (!img) {
          for (let i = keys.length - 1; i >= 0; i--) {
            if (keys[i] <= idx) {
              img = imgs.get(keys[i]);
              break;
            }
          }
          img = img || imgs.get(keys[0]);
        }
        if (img && img.complete && img.naturalWidth > 0) {
          if (canvas.width !== img.naturalWidth || canvas.height !== img.naturalHeight) {
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
          }
          const ctx = canvas.getContext("2d");
          if (ctx) {
            ctx.drawImage(img, 0, 0);
          }
        }
      }
      animRef.current = requestAnimationFrame(draw);
    };
    animRef.current = requestAnimationFrame(draw);
    return () => {
      cancelled = true;
      if (animRef.current != null) cancelAnimationFrame(animRef.current);
      animRef.current = null;
    };
  }, [status?.vhs_rate, status?.vhs_length, status?.frames_count]);

  const { value, max, pct, elapsedClient, eta } = liveTimingParts(status, nowTick, submittedAt);
  const animate = (status?.frames_count || 0) >= 2;

  return (
    <div className={["work-product-live", className].filter(Boolean).join(" ")}>
      {showMetrics ? (
        <div className="work-product-live__metrics">
          <div className="work-product-live__timing" title={status?.node ? `node ${status.node}` : undefined}>
            <span>Elapsed {formatDuration(elapsedClient)}</span>
            <span>ETA {eta != null ? `~${formatDuration(eta)}` : "—"}</span>
            {pct != null ? (
              <span>
                {value}/{max}
              </span>
            ) : status?.status ? (
              <span>{status.status}</span>
            ) : null}
          </div>
          {pct != null ? (
            <div
              className="work-product-live__progress"
              title={`${value}/${max}${status?.node ? ` · node ${status.node}` : ""}`}
            >
              <div className="work-product-live__bar" style={{ width: `${pct}%` }} />
              <span className="work-product-live__prog-label">
                {value}/{max}
                {status?.node ? ` · ${status.node}` : ""}
              </span>
            </div>
          ) : null}
        </div>
      ) : null}
      <div className="work-product-live__frame">
        {animate ? (
          <canvas className="work-product-live__img work-product-live__canvas" ref={canvasRef} />
        ) : hasFrame || status?.has_preview ? (
          <img
            className="work-product-live__img"
            src={comfyLivePreviewUrl(promptId, bust)}
            alt={`Live preview ${promptId}`}
            onLoad={() => setHasFrame(true)}
            onError={() => {
              /* 204 / missing — keep waiting state */
            }}
          />
        ) : (
          <div className="work-product-viewer__empty work-product-live__waiting">Waiting for latent preview…</div>
        )}
        <span className="work-product-live__badge" title={promptId}>
          live
        </span>
      </div>
    </div>
  );
}
