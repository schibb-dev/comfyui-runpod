import React, { useEffect, useMemo, useRef, useState } from "react";
import { comfyCancel, comfyClear, fetchComfyHistory, fetchComfyLogs, fetchQueue, saveQueueItemForLater } from "./api";
import { ComfyLivePreview } from "./ComfyLivePreview";
import { discoveryLibraryHref, workbenchHref } from "./discoveryDeepLink";
import { PageHeader } from "./PageHeader";
import { PipelineMediaPlayer } from "./PipelineMediaPlayer";
import { PipelineFilterRow, PipelineList, PipelineScreen, PipelineScroll } from "./PipelineScreen";
import type { ComfyHistoryItem, ComfyLogEntry, QueueComfyItem, QueueResponse } from "./types";

function basename(rel?: string | null): string {
  const p = (rel || "").replace(/\\/g, "/");
  return p.split("/").pop() || p || "";
}

function shortId(pid?: string | null, n = 10): string {
  const s = (pid || "").trim();
  if (!s) return "(no id)";
  return s.length <= n ? s : `${s.slice(0, n)}…`;
}

function formatKeyParams(params?: Record<string, unknown> | null): string {
  if (!params || typeof params !== "object") return "";
  const order = [
    "seed",
    "noise_seed",
    "steps",
    "cfg",
    "sampler_name",
    "scheduler",
    "denoise",
    "model",
    "skip_first_frames",
    "frame_load_cap",
  ];
  const parts: string[] = [];
  const seen = new Set<string>();
  const skipRaw = params.skip_first_frames;
  const capRaw = params.frame_load_cap;
  const skipN = skipRaw == null || skipRaw === "" ? null : Number(skipRaw);
  const capN = capRaw == null || capRaw === "" ? null : Number(capRaw);
  const trimNontrivial =
    (skipN != null && Number.isFinite(skipN) && skipN > 0) ||
    (capN != null && Number.isFinite(capN) && capN > 0);
  for (const k of order) {
    if (!(k in params)) continue;
    seen.add(k);
    const v = params[k];
    if (v == null || v === "") continue;
    if (k === "skip_first_frames" || k === "frame_load_cap") {
      if (!trimNontrivial) continue;
      if (k === "skip_first_frames") {
        parts.push(`skip=${String(v)}`);
        continue;
      }
      if (capN == null || !Number.isFinite(capN) || capN <= 0) continue;
      parts.push(`cap=${String(v)}`);
      continue;
    }
    parts.push(`${k}=${String(v)}`);
  }
  for (const [k, v] of Object.entries(params)) {
    if (seen.has(k) || v == null || v === "") continue;
    parts.push(`${k}=${String(v)}`);
    if (parts.length >= 8) break;
  }
  return parts.join(" · ");
}

function queueThumb(item: QueueComfyItem): string | null {
  if (item.input_thumb_url) return item.input_thumb_url;
  if (item.input_media_kind === "image" && item.input_media_url) return item.input_media_url;
  if (item.input_media_relpath && /\.(mp4|webm|mov|mkv)$/i.test(item.input_media_relpath)) {
    return "/files/" + encodeURIComponent(item.input_media_relpath.replace(/\.(mp4|webm|mov|mkv)$/i, ".png"));
  }
  return null;
}

function historyThumb(item: ComfyHistoryItem): string | null {
  if (item.output_thumb_url) return item.output_thumb_url;
  if (item.primary_image_url) return item.primary_image_url;
  if (item.input_thumb_url) return item.input_thumb_url;
  if (item.primary_video_relpath && /\.mp4$/i.test(item.primary_video_relpath)) {
    return "/files/" + encodeURIComponent(item.primary_video_relpath.replace(/\.mp4$/i, ".png"));
  }
  return null;
}

function historyAssetRelpath(item: ComfyHistoryItem): string | null {
  for (const cand of [item.primary_video_relpath, item.primary_image_relpath, item.input_media_relpath]) {
    const rel = String(cand || "")
      .trim()
      .replace(/^\/+/, "")
      .replace(/\\/g, "/");
    if (rel) return rel;
  }
  return null;
}

function StatusChip({
  status,
  label,
  count,
  on,
  onToggle,
}: {
  status: string;
  label: string;
  count: number;
  on: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className={`work-products-status-toggle work-products-status-toggle--${status}${on ? " is-on" : " is-off"}`}
      aria-pressed={on}
      onClick={onToggle}
      title={on ? `Hide ${label}` : `Show ${label}`}
    >
      <span className="work-products-status-toggle__label">{label}</span>
      <span className="work-products-status-toggle__count">{count}</span>
    </button>
  );
}

function queueVideoUrl(item: QueueComfyItem): string | null {
  if (item.input_media_kind === "video" && item.input_media_url) return item.input_media_url;
  if (item.input_media_url && /\.(mp4|webm|mov|mkv)(\?|$)/i.test(item.input_media_url)) return item.input_media_url;
  if (item.input_media_relpath && /\.(mp4|webm|mov|mkv)$/i.test(item.input_media_relpath)) {
    return "/files/" + encodeURIComponent(item.input_media_relpath.replace(/\\/g, "/"));
  }
  return null;
}

function QueuePipelineRow({
  title,
  statusLabel,
  statusVisual,
  promptId,
  media,
  detail,
  live,
  actions,
}: {
  title: string;
  statusLabel: string;
  statusVisual: string;
  promptId?: string | null;
  media: React.ReactNode;
  detail: string;
  live?: boolean;
  actions: React.ReactNode;
}) {
  return (
    <article className={`pipeline-row${live ? " pipeline-row--live" : ""}`}>
      <header className="pipeline-row__head">
        <div className="pipeline-row__title">
          <span className="pipeline-row__title-text">{title}</span>
          <span
            className={`work-products-status-toggle work-products-status-toggle--${statusVisual} is-on`}
            style={{ pointerEvents: "none" }}
          >
            <span className="work-products-status-toggle__label">{statusLabel}</span>
          </span>
        </div>
        <code className="pipeline-row__key" title={promptId || undefined}>
          {shortId(promptId, 14)}
        </code>
      </header>
      <div className="pipeline-row__body pipeline-row__body--player">
        <div className="pipeline-row__media pipeline-row__media--player">{media}</div>
        <div className="pipeline-row__details">
          {detail ? <p className="pipeline-row__detail-line">{detail}</p> : null}
          <div className="pipeline-row__actions">{actions}</div>
        </div>
      </div>
    </article>
  );
}

function QueueItemRow({
  item,
  kind,
  onRefresh,
}: {
  item: QueueComfyItem;
  kind: "running" | "pending";
  onRefresh: () => void;
}) {
  const pid = item.prompt_id ?? "";
  const thumb = queueThumb(item);
  const videoUrl = queueVideoUrl(item);
  const jobKey = String(item.job_key || "").trim() || null;
  const workbenchUrl = workbenchHref({ jobKey, promptId: pid || null });
  const title = item.workflow_name || basename(item.input_media_relpath) || shortId(pid, 16);
  const detailParts = [
    item.input_media_relpath ? basename(item.input_media_relpath) : null,
    !item.external && item.exp_id ? `${item.exp_id}/${item.run_id ?? ""}` : null,
    item.external ? "external" : null,
    formatKeyParams(item.key_params),
  ].filter(Boolean);

  const media =
    kind === "running" && pid ? (
      <div className="pipeline-row__media-stack">
        <ComfyLivePreview promptId={pid} className="pipeline-row__live" />
        {videoUrl || thumb ? (
          <PipelineMediaPlayer
            videoUrl={videoUrl}
            thumbUrl={thumb}
            mediaKey={`queue-src:${pid || item.input_media_relpath || title}`}
            alt={title}
            readOnly
          />
        ) : null}
      </div>
    ) : (
      <PipelineMediaPlayer
        videoUrl={videoUrl}
        thumbUrl={thumb}
        mediaKey={`queue-${kind}:${pid || item.input_media_relpath || title}`}
        alt={title}
        readOnly
      />
    );

  return (
    <QueuePipelineRow
      title={title}
      statusLabel={kind === "running" ? "running" : item.external ? "external" : "pending"}
      statusVisual={kind === "running" ? "running" : "pending"}
      promptId={pid}
      media={media}
      detail={detailParts.join(" · ")}
      live={kind === "running"}
      actions={
        <>
          <button
            type="button"
            disabled={!pid}
            title={kind === "running" ? "Interrupt current ComfyUI execution" : "Remove from pending queue"}
            onClick={() => {
              void (async () => {
                if (!pid) return;
                await comfyCancel({ prompt_id: pid, kind });
                onRefresh();
              })();
            }}
          >
            {kind === "running" ? "Interrupt" : "Cancel"}
          </button>
          <button
            type="button"
            title="Save this queue item for later"
            onClick={() => {
              void saveQueueItemForLater({
                title: item.workflow_name || `Saved ${pid || "queue item"}`,
                prompt_id: pid || undefined,
                tags: ["comfy-queue"],
                payload: {
                  workflow_name: item.workflow_name ?? null,
                  input_media_relpath: item.input_media_relpath ?? null,
                  key_params: item.key_params ?? {},
                  source: "comfy-queue-monitor",
                },
              });
            }}
          >
            Save
          </button>
          <a
            className="pipeline-row__link"
            href={workbenchUrl}
            title={
              jobKey
                ? `Open ${jobKey} in Workbench`
                : pid
                  ? `Find prompt ${pid} in Workbench`
                  : "Open Workbench"
            }
          >
            Workbench
          </a>
        </>
      }
    />
  );
}

function HistoryItemRow({ item }: { item: ComfyHistoryItem }) {
  const thumb = historyThumb(item);
  const videoUrl =
    item.primary_video_url ||
    (item.primary_video_relpath
      ? "/files/" + encodeURIComponent(item.primary_video_relpath.replace(/\\/g, "/"))
      : null);
  const title =
    item.workflow_name ||
    basename(item.primary_video_relpath) ||
    basename(item.primary_image_relpath) ||
    shortId(item.prompt_id, 16);
  const libraryRel = historyAssetRelpath(item);
  const detailParts = [
    item.input_media_relpath ? `in ${basename(item.input_media_relpath)}` : null,
    formatKeyParams(item.key_params),
  ].filter(Boolean);

  return (
    <QueuePipelineRow
      title={title}
      statusLabel={item.status || "done"}
      statusVisual="ok"
      promptId={item.prompt_id}
      media={
        <PipelineMediaPlayer
          videoUrl={videoUrl}
          thumbUrl={thumb}
          mediaKey={`queue-hist:${item.prompt_id || libraryRel || title}`}
          alt={title}
          readOnly
        />
      }
      detail={detailParts.join(" · ")}
      actions={
        libraryRel ? (
          <a className="pipeline-row__link" href={discoveryLibraryHref(libraryRel)} title="Open in Library">
            Open in Library
          </a>
        ) : (
          <span className="pipeline-row__meta">No library path</span>
        )
      }
    />
  );
}

type SectionKey = "running" | "pending" | "history";

function formatLogStamp(t?: string | null): string {
  if (!t) return "";
  // Comfy stamps look like 2026-08-04T14:27:23.018433 — show local-ish HH:MM:SS
  const m = String(t).match(/T(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : String(t).slice(11, 19);
}

function ComfyLogPanel() {
  const [entries, setEntries] = useState<ComfyLogEntry[]>([]);
  const [error, setError] = useState("");
  const [follow, setFollow] = useState(true);
  const [size, setSize] = useState<number | null>(null);
  const preRef = useRef<HTMLPreElement | null>(null);
  const stickRef = useRef(true);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await fetchComfyLogs({ tail: 400 });
        if (cancelled) return;
        setEntries(Array.isArray(res.entries) ? res.entries : []);
        setSize(typeof res.size === "number" ? res.size : null);
        setError("");
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    stickRef.current = follow;
  }, [follow]);

  useEffect(() => {
    const el = preRef.current;
    if (!el || !stickRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [entries]);

  const onScroll = () => {
    const el = preRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (nearBottom !== follow) setFollow(nearBottom);
  };

  const text = useMemo(() => {
    return entries
      .map((row) => {
        const stamp = formatLogStamp(row.t);
        const msg = String(row.m ?? "").replace(/\r/g, "");
        return stamp ? `${stamp} ${msg}` : msg;
      })
      .join("")
      .replace(/\n{3,}/g, "\n\n");
  }, [entries]);

  return (
    <section className="queue-monitor-log" aria-label="ComfyUI logs">
      <div className="queue-monitor-log__head">
        <h2 className="queue-monitor-log__title">ComfyUI logs</h2>
        <div className="queue-monitor-log__meta">
          {size != null ? <span className="mono">{size} buffered</span> : null}
          <button
            type="button"
            className={follow ? "is-on" : ""}
            aria-pressed={follow}
            onClick={() => setFollow((v) => !v)}
            title={follow ? "Following new lines" : "Scroll paused — click to follow"}
          >
            {follow ? "Follow" : "Paused"}
          </button>
        </div>
      </div>
      {error ? <div className="queue-monitor-error">{error}</div> : null}
      <pre ref={preRef} className="queue-monitor-log__pre mono" onScroll={onScroll}>
        {text || (error ? "" : "Waiting for log lines…")}
      </pre>
    </section>
  );
}

export function ComfyQueueMonitorApp() {
  const [data, setData] = useState<QueueResponse | null>(null);
  const [history, setHistory] = useState<ComfyHistoryItem[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [show, setShow] = useState<Record<SectionKey, boolean>>({
    running: true,
    pending: true,
    history: true,
  });

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const [q, h] = await Promise.all([fetchQueue(), fetchComfyHistory(40)]);
      setData(q);
      setHistory(Array.isArray(h.items) ? h.items : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => {
      void refresh();
    }, 5000);
    return () => window.clearInterval(t);
  }, []);

  const running = data?.comfyui?.running ?? [];
  const pending = data?.comfyui?.pending ?? [];
  const subtitle = useMemo(
    () => `Comfy ops — running ${running.length} · waiting ${pending.length} · history ${history.length}`,
    [running.length, pending.length, history.length],
  );

  const toggle = (key: SectionKey) => setShow((s) => ({ ...s, [key]: !s[key] }));

  return (
    <PipelineScreen className="queue-monitor">
      <PageHeader
        title="Queue"
        subtitle={subtitle}
        actions={
          <>
            <button type="button" onClick={() => void refresh()} disabled={loading}>
              Refresh
            </button>
            <button
              type="button"
              onClick={() => {
                void (async () => {
                  await comfyClear();
                  await refresh();
                })();
              }}
            >
              Clear waiting
            </button>
          </>
        }
      />
      <PipelineFilterRow aria-label="Queue sections">
        <StatusChip
          status="running"
          label="running"
          count={running.length}
          on={show.running}
          onToggle={() => toggle("running")}
        />
        <StatusChip
          status="pending"
          label="waiting"
          count={pending.length}
          on={show.pending}
          onToggle={() => toggle("pending")}
        />
        <StatusChip
          status="ok"
          label="history"
          count={history.length}
          on={show.history}
          onToggle={() => toggle("history")}
        />
      </PipelineFilterRow>
      {error ? <div className="queue-monitor-error">{error}</div> : null}
      <div className="queue-monitor-split">
        <PipelineScroll>
          <PipelineList>
            {show.running ? (
              <>
                <div className="pipeline-section-label">Running</div>
                {running.length ? (
                  running.map((item, i) => (
                    <QueueItemRow
                      key={`${item.prompt_id ?? "run"}:${i}`}
                      item={item}
                      kind="running"
                      onRefresh={() => void refresh()}
                    />
                  ))
                ) : (
                  <div className="pipeline-empty">(idle)</div>
                )}
              </>
            ) : null}
            {show.pending ? (
              <>
                <div className="pipeline-section-label">Waiting</div>
                {pending.length ? (
                  pending.map((item, i) => (
                    <QueueItemRow
                      key={`${item.prompt_id ?? "pend"}:${i}`}
                      item={item}
                      kind="pending"
                      onRefresh={() => void refresh()}
                    />
                  ))
                ) : (
                  <div className="pipeline-empty">(none)</div>
                )}
              </>
            ) : null}
            {show.history ? (
              <>
                <div className="pipeline-section-label">History</div>
                {history.length ? (
                  history.map((h) => <HistoryItemRow key={h.prompt_id} item={h} />)
                ) : (
                  <div className="pipeline-empty">(no history)</div>
                )}
              </>
            ) : null}
          </PipelineList>
        </PipelineScroll>
        <ComfyLogPanel />
      </div>
    </PipelineScreen>
  );
}
