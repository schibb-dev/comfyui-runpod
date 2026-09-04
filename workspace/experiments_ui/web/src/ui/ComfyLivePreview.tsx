import React, { useEffect, useRef, useState } from "react";
import { AppetitePreviewBadge } from "./AppetitePreviewBadge";
import { comfyLivePreviewUrl, fetchComfyLiveStatus } from "./api";
import type { ComfyLiveStatusItem } from "./types";

const LIVE_STATUS_POLL_MS = 2000;

type LiveStatusListener = (item: ComfyLiveStatusItem | null) => void;
const liveStatusListeners = new Map<string, Set<LiveStatusListener>>();
let liveStatusTimer: number | null = null;

function liveStatusIds(): string[] {
  return [...liveStatusListeners.keys()].filter((id) => (liveStatusListeners.get(id)?.size || 0) > 0);
}

async function tickSharedLiveStatus() {
  if (typeof document !== "undefined" && document.hidden) return;
  const ids = liveStatusIds();
  if (!ids.length) return;
  try {
    const res = await fetchComfyLiveStatus(ids);
    const byId = new Map((res.items || []).map((it) => [String(it.prompt_id || ""), it]));
    for (const id of ids) {
      const item = byId.get(id) || null;
      for (const cb of liveStatusListeners.get(id) || []) cb(item);
    }
  } catch {
    /* ignore transient bridge errors while polling */
  }
}

function ensureLiveStatusTimer() {
  if (liveStatusTimer != null) return;
  void tickSharedLiveStatus();
  liveStatusTimer = window.setInterval(() => void tickSharedLiveStatus(), LIVE_STATUS_POLL_MS);
}

function stopLiveStatusTimerIfIdle() {
  if (liveStatusIds().length) return;
  if (liveStatusTimer != null) {
    window.clearInterval(liveStatusTimer);
    liveStatusTimer = null;
  }
}

function subscribeLiveStatus(promptId: string, cb: LiveStatusListener): () => void {
  const id = String(promptId || "").trim();
  if (!id) return () => undefined;
  let set = liveStatusListeners.get(id);
  if (!set) {
    set = new Set();
    liveStatusListeners.set(id, set);
  }
  set.add(cb);
  ensureLiveStatusTimer();
  return () => {
    set?.delete(cb);
    if (set && set.size === 0) liveStatusListeners.delete(id);
    stopLiveStatusTimerIfIdle();
  };
}

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

  useEffect(() => subscribeLiveStatus(promptId, setStatus), [promptId]);

  useEffect(() => {
    const onVis = () => {
      if (!document.hidden) setNowTick(Date.now());
    };
    document.addEventListener("visibilitychange", onVis);
    const clock = window.setInterval(() => {
      if (!document.hidden) setNowTick(Date.now());
    }, 1000);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      window.clearInterval(clock);
    };
  }, []);

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
  appetiteRelpath,
}: {
  promptId: string;
  submittedAt?: string | null;
  className?: string;
  showMetrics?: boolean;
  appetiteRelpath?: string | null;
}) {
  const [bust, setBust] = useState(() => Date.now());
  const [hasFrame, setHasFrame] = useState(false);
  const [imgFailed, setImgFailed] = useState(false);
  const { status, nowTick } = useComfyLiveStatus(promptId);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameUrlsRef = useRef<Map<number, string>>(new Map());
  const frameImgsRef = useRef<Map<number, HTMLImageElement>>(new Map());
  const animRef = useRef<number | null>(null);
  const lastFetchRef = useRef(0);

  useEffect(() => {
    if (status?.has_preview) {
      setHasFrame(true);
      setImgFailed(false);
      setBust(Date.now());
    }
  }, [status?.has_preview, status?.updated_at]);

  // While running, periodically re-bust the still preview so a 204/race doesn't stick forever.
  useEffect(() => {
    const running = status?.status === "running" || (status?.value != null && status?.finished_at == null);
    if (!running) return;
    const id = window.setInterval(() => {
      if (document.hidden) return;
      setBust(Date.now());
    }, hasFrame && !imgFailed ? 2500 : 900);
    return () => window.clearInterval(id);
  }, [status?.status, status?.value, status?.finished_at, hasFrame, imgFailed, promptId]);

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
        setImgFailed(false);
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
      if (cancelled || document.hidden) return;
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
    const startLoop = () => {
      if (cancelled || document.hidden) return;
      if (animRef.current != null) cancelAnimationFrame(animRef.current);
      animRef.current = requestAnimationFrame(draw);
    };
    const onVis = () => {
      if (document.hidden) {
        if (animRef.current != null) cancelAnimationFrame(animRef.current);
        animRef.current = null;
        return;
      }
      startLoop();
    };
    document.addEventListener("visibilitychange", onVis);
    startLoop();
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVis);
      if (animRef.current != null) cancelAnimationFrame(animRef.current);
      animRef.current = null;
    };
  }, [status?.vhs_rate, status?.vhs_length, status?.frames_count]);

  const { value, max, pct, elapsedClient, eta } = liveTimingParts(status, nowTick, submittedAt);
  const animate = (status?.frames_count || 0) >= 2;
  const showStill = !animate && (hasFrame || Boolean(status?.has_preview));

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
        ) : showStill ? (
          <img
            className="work-product-live__img"
            src={comfyLivePreviewUrl(promptId, bust)}
            alt={`Live preview ${promptId}`}
            onLoad={() => {
              setHasFrame(true);
              setImgFailed(false);
            }}
            onError={() => {
              setImgFailed(true);
              setHasFrame(false);
            }}
          />
        ) : (
          <div className="work-product-viewer__empty work-product-live__waiting">Waiting for latent preview…</div>
        )}
        <span className="work-product-live__badge" title={promptId}>
          live
        </span>
        <AppetitePreviewBadge relpath={appetiteRelpath} />
      </div>
    </div>
  );
}
