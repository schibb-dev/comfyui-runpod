import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  comfyCancel,
  comfyClear,
  fetchComfyHistory,
  fetchComfyLogs,
  fetchQueue,
  fetchQueueLedgerEvents,
  fetchQueueLedgerStatus,
  saveQueueItemForLater,
  setQueueLedgerControl,
} from "./api";
import { ComfyLiveMetricsBar, ComfyLivePreview } from "./ComfyLivePreview";
import { discoveryLibraryHref, workbenchHref } from "./discoveryDeepLink";
import { PageHeader } from "./PageHeader";
import { PipelineMediaPlayer, vhsWindowFromKeyParams } from "./PipelineMediaPlayer";
import { PipelineFilterRow, PipelineList, PipelineScreen, PipelineScroll } from "./PipelineScreen";
import type {
  ComfyHistoryItem,
  ComfyLogEntry,
  QueueComfyItem,
  QueueLedgerControlAction,
  QueueLedgerEvent,
  QueueLedgerEntry,
  QueueLedgerStatus,
  QueueResponse,
} from "./types";

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

function formatQueueWhen(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function historyStatusVisual(status?: string): string {
  const s = String(status || "").toLowerCase();
  if (s === "error" || s === "failed") return "error";
  if (s === "interrupted") return "interrupted";
  if (s === "success" || s === "complete" || s === "completed") return "ok";
  return "muted";
}

function isHistoryProblem(item: { status?: string }): boolean {
  const v = historyStatusVisual(item.status);
  return v === "error" || v === "interrupted";
}

type StatusFilter = "all" | "errors" | "ok";
type SortMode = "newest" | "oldest" | "errors_first" | "queue_index";

function itemSortKeyChanged(item: {
  changed_at?: string | null;
  queued_at?: string | null;
  queue_index?: number | null;
}): number {
  const iso = item.changed_at || item.queued_at;
  if (iso) {
    const t = Date.parse(iso);
    if (!Number.isNaN(t)) return t;
  }
  if (typeof item.queue_index === "number") return item.queue_index;
  return 0;
}

function sortQueueItems<
  T extends { changed_at?: string | null; queued_at?: string | null; queue_index?: number | null; status?: string },
>(items: T[], mode: SortMode): T[] {
  const copy = items.slice();
  copy.sort((a, b) => {
    if (mode === "errors_first") {
      const ae = isHistoryProblem(a) ? 0 : 1;
      const be = isHistoryProblem(b) ? 0 : 1;
      if (ae !== be) return ae - be;
      return itemSortKeyChanged(b) - itemSortKeyChanged(a);
    }
    if (mode === "queue_index") {
      const ai = typeof a.queue_index === "number" ? a.queue_index : -1;
      const bi = typeof b.queue_index === "number" ? b.queue_index : -1;
      return bi - ai;
    }
    if (mode === "oldest") return itemSortKeyChanged(a) - itemSortKeyChanged(b);
    return itemSortKeyChanged(b) - itemSortKeyChanged(a);
  });
  return copy;
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
  onFocusSolo,
}: {
  status: string;
  label: string;
  count: number;
  on: boolean;
  onToggle: () => void;
  /** Double-click: radio-style — only this chip on within its filter set. */
  onFocusSolo?: () => void;
}) {
  return (
    <button
      type="button"
      className={`work-products-status-toggle work-products-status-toggle--${status}${on ? " is-on" : " is-off"}`}
      aria-pressed={on}
      onClick={onToggle}
      onDoubleClick={
        onFocusSolo
          ? (e) => {
              e.preventDefault();
              onFocusSolo();
            }
          : undefined
      }
      title={
        on
          ? `Hide ${label}${onFocusSolo ? " · double-click to show only this" : ""}`
          : `Show ${label}${onFocusSolo ? " · double-click to show only this" : ""}`
      }
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
  queuedAt,
  changedAt,
  errorMessage,
  live,
  liveMetrics,
  actions,
}: {
  title: string;
  statusLabel: string;
  statusVisual: string;
  promptId?: string | null;
  media: React.ReactNode;
  detail: string;
  queuedAt?: string | null;
  changedAt?: string | null;
  errorMessage?: string | null;
  live?: boolean;
  liveMetrics?: React.ReactNode;
  actions: React.ReactNode;
}) {
  const isError = statusVisual === "error" || statusVisual === "interrupted";
  return (
    <article
      className={`pipeline-row${live ? " pipeline-row--live" : ""}${isError ? " pipeline-row--error" : ""}`}
    >
      <header className={`pipeline-row__head${liveMetrics ? " pipeline-row__head--live-metrics" : ""}`}>
        <div className="pipeline-row__head-main">
          <div className="pipeline-row__title">
            <span className="pipeline-row__badges" aria-label="Status">
              <span
                className={`work-products-status-toggle work-products-status-toggle--${statusVisual} is-on${
                  isError ? " queue-status-badge--loud" : ""
                }`}
                style={{ pointerEvents: "none" }}
              >
                <span className="work-products-status-toggle__label">{statusLabel}</span>
              </span>
            </span>
            <span className="pipeline-row__title-text">{title}</span>
          </div>
        </div>
        {liveMetrics}
      </header>
      <div className="pipeline-row__times mono" aria-label="Timestamps">
        <span title="When this job entered the queue / started">
          queued {formatQueueWhen(queuedAt)}
        </span>
        <span title="Last status change (finished, failed, or updated)">
          changed {formatQueueWhen(changedAt)}
        </span>
        <code className="pipeline-row__key" title={promptId || undefined}>
          {shortId(promptId, 14)}
        </code>
      </div>
      <div className="pipeline-row__body pipeline-row__body--player">
        <div className="pipeline-row__media pipeline-row__media--player">{media}</div>
        <div className="pipeline-row__details">
          {errorMessage ? <p className="pipeline-row__error-line">{errorMessage}</p> : null}
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
  const trim = vhsWindowFromKeyParams(item.key_params);
  const detailParts = [
    item.input_media_relpath ? basename(item.input_media_relpath) : null,
    !item.external && item.exp_id ? `${item.exp_id}/${item.run_id ?? ""}` : null,
    item.external ? "external" : null,
    formatKeyParams(item.key_params),
  ].filter(Boolean);

  const sourcePlayer = (
    <PipelineMediaPlayer
      videoUrl={videoUrl}
      thumbUrl={thumb}
      mediaKey={`queue-${kind}:${pid || item.input_media_relpath || title}`}
      alt={title}
      readOnly
      vhsWindow={trim.window}
      fpsHint={trim.fpsHint}
    />
  );

  const media =
    kind === "running" && pid ? (
      <div className="pipeline-row__media-stack">
        <ComfyLivePreview promptId={pid} className="pipeline-row__live" showMetrics={false} />
        {sourcePlayer}
      </div>
    ) : (
      sourcePlayer
    );

  return (
    <QueuePipelineRow
      title={title}
      statusLabel={kind === "running" ? "running" : item.external ? "external" : "pending"}
      statusVisual={kind === "running" ? "running" : "pending"}
      promptId={pid}
      media={media}
      detail={detailParts.join(" · ")}
      queuedAt={item.queued_at}
      changedAt={item.changed_at}
      live={kind === "running"}
      liveMetrics={kind === "running" && pid ? <ComfyLiveMetricsBar promptId={pid} /> : null}
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
  const jobKey = String(item.job_key || "").trim() || null;
  const pid = String(item.prompt_id || "").trim();
  const workbenchUrl = workbenchHref({ jobKey, promptId: pid || null });
  const detailParts = [
    item.input_media_relpath ? `in ${basename(item.input_media_relpath)}` : null,
    item.error_node ? `node ${item.error_node}` : null,
    formatKeyParams(item.key_params),
  ].filter(Boolean);
  const statusVisual = historyStatusVisual(item.status);
  const statusLabel =
    statusVisual === "error" ? "error" : statusVisual === "interrupted" ? "interrupted" : item.status || "done";
  const errLine = item.error_message
    ? String(item.error_message).trim().replace(/\s+/g, " ").slice(0, 220)
    : null;

  return (
    <QueuePipelineRow
      title={title}
      statusLabel={statusLabel}
      statusVisual={statusVisual}
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
      queuedAt={item.queued_at}
      changedAt={item.changed_at}
      errorMessage={errLine}
      actions={
        <>
          {libraryRel ? (
            <a className="pipeline-row__link" href={discoveryLibraryHref(libraryRel)} title="Open in Library">
              Open in Library
            </a>
          ) : (
            <span className="pipeline-row__meta">No library path</span>
          )}
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

function formatLedgerUpdated(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatLedgerEventTime(iso?: string): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function shortPromptId(pid: unknown): string {
  if (typeof pid !== "string" || !pid) return "";
  return pid.length > 12 ? `${pid.slice(0, 8)}…` : pid;
}

const LEDGER_EVENT_LABELS: Record<string, string> = {
  queue_enqueued: "Enqueued",
  queue_left: "Left queue",
  unexpected_queue_delta: "Queue change",
  mode_switched: "Mode",
  ledger_cleared: "Cleared",
  comfy_outage_begin: "Comfy down",
  comfy_outage_end: "Comfy back",
  outage_restored: "Outage restore",
  startup_restored: "Startup restore",
  breaker_opened: "Breaker open",
  breaker_closed_auto: "Breaker closed",
  actions_paused: "Paused",
  drain_once_ack: "Drain once",
  refill_restored: "Refill",
  spillover_removed: "Spillover",
};

function formatLedgerEventLabel(type?: string): string {
  if (!type) return "event";
  return LEDGER_EVENT_LABELS[type] || type.replace(/_/g, " ");
}

function formatLedgerEventDetail(ev: QueueLedgerEvent): string {
  const type = typeof ev.type === "string" ? ev.type : "";
  if (type === "queue_enqueued") {
    const parts: string[] = [];
    const pid = shortPromptId(ev.prompt_id);
    if (pid) parts.push(pid);
    if (ev.phase === "running") parts.push("running");
    else if (ev.phase === "pending") parts.push("waiting");
    if (typeof ev.client_id === "string" && ev.client_id) parts.push(ev.client_id);
    return parts.join(" · ");
  }
  if (type === "queue_left") {
    const parts: string[] = [];
    const pid = shortPromptId(ev.prompt_id);
    if (pid) parts.push(pid);
    if (ev.was_phase === "running") parts.push("was running");
    else if (ev.was_phase === "pending") parts.push("was waiting");
    if (typeof ev.client_id === "string" && ev.client_id) parts.push(ev.client_id);
    return parts.join(" · ");
  }
  const parts: string[] = [];
  const pid = shortPromptId(ev.prompt_id);
  if (pid) parts.push(pid);
  if (typeof ev.reason === "string" && ev.reason) parts.push(ev.reason);
  if (typeof ev.source === "string" && ev.source) parts.push(`src ${ev.source}`);
  if (typeof ev.mode === "string" && ev.mode) parts.push(`→ ${ev.mode}`);
  if (typeof ev.restored === "number") parts.push(`restored ${ev.restored}`);
  if (typeof ev.parked_backlog === "number" && ev.parked_backlog > 0) {
    parts.push(`parked ${ev.parked_backlog}`);
  }
  if (typeof ev.unrecoverable === "number" && ev.unrecoverable > 0) {
    parts.push(`unrecoverable ${ev.unrecoverable}`);
  }
  if (typeof ev.outage_s === "number") parts.push(`${ev.outage_s.toFixed(1)}s`);
  if (typeof ev.known === "number") parts.push(`known ${ev.known}`);
  if (typeof ev.backlog === "number") parts.push(`backlog ${ev.backlog}`);
  if (typeof ev.snapshot === "number") parts.push(`snapshot ${ev.snapshot}`);
  if (typeof ev.live_pending === "number") parts.push(`pending ${ev.live_pending}`);
  if (typeof ev.live_running === "number") parts.push(`running ${ev.live_running}`);
  if (Array.isArray(ev.added) && ev.added.length) {
    parts.push(`+${ev.added.map((x) => shortPromptId(x) || "?").join(",")}`);
  }
  if (Array.isArray(ev.removed) && ev.removed.length) {
    parts.push(`-${ev.removed.map((x) => shortPromptId(x) || "?").join(",")}`);
  }
  if (typeof ev.error === "string" && ev.error) parts.push(ev.error);
  if (typeof ev.failures === "number") parts.push(`failures ${ev.failures}`);
  return parts.join(" · ");
}

function formatLedgerEntryRole(role?: string): string {
  if (role === "pending") return "waiting";
  if (role === "remembered") return "remembered";
  if (role === "backlog") return "backlog";
  if (role === "running") return "running";
  return role || "entry";
}

function formatLedgerEntryLine(entry: QueueLedgerEntry): string {
  const parts: string[] = [];
  const pid = shortPromptId(entry.prompt_id) || entry.prompt_id || "?";
  parts.push(pid);
  if (entry.client_id) parts.push(entry.client_id);
  if (entry.last_seen_at) parts.push(`seen ${formatLedgerEventTime(entry.last_seen_at)}`);
  if (entry.has_prompt === false) parts.push("no prompt");
  return parts.join(" · ");
}

function QueueLedgerPanel({
  status,
  events,
  busy,
  error,
  onAction,
}: {
  status: QueueLedgerStatus | null;
  events: QueueLedgerEvent[];
  busy: boolean;
  error: string;
  onAction: (action: QueueLedgerControlAction) => void;
}) {
  const paused = Boolean(status?.paused);
  const breakerOpen = Boolean(status?.breaker?.open);
  const backlog = typeof status?.backlog_count === "number" ? status.backlog_count : 0;
  const knownCount = typeof status?.known_count === "number" ? status.known_count : 0;
  const entries = Array.isArray(status?.entries) ? status.entries : [];
  const stats = status?.stats;
  const statsParts: string[] = [];
  if (stats) {
    if (typeof stats.restored_startup === "number" && stats.restored_startup > 0) {
      statsParts.push(`startup ${stats.restored_startup}`);
    }
    if (typeof stats.restored_outage === "number" && stats.restored_outage > 0) {
      statsParts.push(`outage ${stats.restored_outage}`);
    }
    if (typeof stats.cleared === "number" && stats.cleared > 0) {
      statsParts.push(`cleared ${stats.cleared}`);
    }
  }

  return (
    <div className="queue-ledger queue-monitor-split" aria-label="Queue ledger">
      <section className="queue-monitor-log queue-ledger__controls" aria-label="Ledger status and contents">
        <div className="queue-monitor-log__head">
          <h2 className="queue-monitor-log__title">Queue ledger</h2>
          <div className="queue-monitor-log__meta">
            <span className={`queue-ledger__pill${paused ? " queue-ledger__pill--paused" : ""}`}>
              {paused ? "Paused" : "Live"}
            </span>
            <span className="mono">
              {entries.length} entries · known {knownCount}
            </span>
          </div>
        </div>
        <div className="queue-ledger__body">
          <p className="queue-ledger__lead">
            Shadows Comfy&apos;s queue and restores it after restarts. Clear ledger forgets that snapshot — it does not
            empty Comfy waiting.
          </p>
          <div className="queue-ledger__status" aria-live="polite">
            <span className="queue-ledger__meta mono">mode {status?.mode || "—"}</span>
            <span className="queue-ledger__meta mono">backlog {backlog}</span>
            {breakerOpen ? (
              <span className="queue-ledger__pill queue-ledger__pill--warn" title={status?.breaker?.reason || ""}>
                Breaker open
              </span>
            ) : (
              <span className="queue-ledger__meta">breaker ok</span>
            )}
            <span className="queue-ledger__meta">updated {formatLedgerUpdated(status?.updated_at)}</span>
            {statsParts.length ? <span className="queue-ledger__meta mono">{statsParts.join(" · ")}</span> : null}
          </div>
          <div className="queue-ledger__actions">
            {paused ? (
              <button type="button" disabled={busy} onClick={() => onAction("resume")}>
                Resume
              </button>
            ) : (
              <button type="button" disabled={busy} onClick={() => onAction("pause")}>
                Pause
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (
                  !window.confirm(
                    "Clear ledger restore state (known / backlog / snapshot)?\n\nThis does not clear Comfy’s waiting queue. After a Comfy restart the ledger will not put old jobs back.",
                  )
                ) {
                  return;
                }
                onAction("clear");
              }}
            >
              Clear ledger
            </button>
            {breakerOpen ? (
              <button type="button" disabled={busy} onClick={() => onAction("reset-breaker")}>
                Reset breaker
              </button>
            ) : null}
            <button
              type="button"
              className="queue-ledger__btn--secondary"
              disabled={busy}
              title="Refill from ledger backlog into Comfy when slots open"
              onClick={() => onAction("drain-once")}
            >
              Drain once
            </button>
          </div>
          {error ? <p className="queue-ledger__err">{error}</p> : null}
          <div className="queue-ledger__entries-head">Contents</div>
          {entries.length === 0 ? (
            <p className="queue-ledger__activity-empty">No mirrored prompts in the ledger.</p>
          ) : (
            <ul className="queue-ledger__entries-list">
              {entries.map((entry) => {
                const pid = entry.prompt_id || "";
                return (
                  <li
                    key={pid || formatLedgerEntryLine(entry)}
                    className={`queue-ledger__entry-row queue-ledger__entry-row--${entry.role || "remembered"}`}
                    title={pid}
                  >
                    <span className="queue-ledger__entry-role">{formatLedgerEntryRole(entry.role)}</span>
                    <span className="queue-ledger__entry-line mono">{formatLedgerEntryLine(entry)}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>
      <section className="queue-monitor-log" aria-label="Ledger recent activity">
        <div className="queue-monitor-log__head">
          <h2 className="queue-monitor-log__title">Ledger activity</h2>
          <div className="queue-monitor-log__meta">
            <span className="mono">{events.length} shown</span>
          </div>
        </div>
        {events.length === 0 ? (
          <p className="queue-ledger__activity-empty">No ledger events yet.</p>
        ) : (
          <ul className="queue-ledger__activity-list">
            {events.map((ev, i) => {
              const detail = formatLedgerEventDetail(ev);
              const key = `${ev.ts || ""}:${ev.type || ""}:${i}`;
              return (
                <li key={key} className="queue-ledger__activity-row">
                  <span className="queue-ledger__activity-ts mono">{formatLedgerEventTime(ev.ts)}</span>
                  <span className="queue-ledger__activity-type">{formatLedgerEventLabel(ev.type)}</span>
                  {detail ? <span className="queue-ledger__activity-detail mono">{detail}</span> : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

export function ComfyQueueMonitorApp() {
  const [data, setData] = useState<QueueResponse | null>(null);
  const [history, setHistory] = useState<ComfyHistoryItem[]>([]);
  const [ledger, setLedger] = useState<QueueLedgerStatus | null>(null);
  const [ledgerEvents, setLedgerEvents] = useState<QueueLedgerEvent[]>([]);
  const [error, setError] = useState("");
  const [ledgerErr, setLedgerErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [ledgerBusy, setLedgerBusy] = useState(false);
  const [pageTab, setPageTab] = useState<"queue" | "ledger">("queue");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sortMode, setSortMode] = useState<SortMode>("newest");
  const [show, setShow] = useState<Record<SectionKey, boolean>>({
    running: true,
    pending: true,
    history: true,
  });

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const [q, h, led, ev] = await Promise.all([
        fetchQueue(),
        fetchComfyHistory(80),
        fetchQueueLedgerStatus().catch((e) => {
          setLedgerErr(e instanceof Error ? e.message : String(e));
          return null;
        }),
        fetchQueueLedgerEvents(30).catch(() => null),
      ]);
      setData(q);
      setHistory(Array.isArray(h.items) ? h.items : []);
      if (led) {
        setLedger(led);
        setLedgerErr("");
      }
      if (ev && Array.isArray(ev.events)) {
        setLedgerEvents(ev.events);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const runLedgerAction = async (action: QueueLedgerControlAction) => {
    setLedgerBusy(true);
    setLedgerErr("");
    try {
      await setQueueLedgerControl(action);
      const [led, ev] = await Promise.all([fetchQueueLedgerStatus(), fetchQueueLedgerEvents(30)]);
      setLedger(led);
      if (Array.isArray(ev.events)) setLedgerEvents(ev.events);
    } catch (e) {
      setLedgerErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLedgerBusy(false);
    }
  };

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => {
      void refresh();
    }, 5000);
    return () => window.clearInterval(t);
  }, []);

  const runningRaw = data?.comfyui?.running ?? [];
  const pendingRaw = data?.comfyui?.pending ?? [];
  const historyErrorCount = useMemo(() => history.filter((h) => isHistoryProblem(h)).length, [history]);
  const filteredHistory = useMemo(() => {
    let rows = history;
    if (statusFilter === "errors") rows = rows.filter((h) => isHistoryProblem(h));
    else if (statusFilter === "ok") rows = rows.filter((h) => !isHistoryProblem(h));
    return sortQueueItems(rows, sortMode);
  }, [history, statusFilter, sortMode]);
  const filteredRunning = useMemo(() => {
    if (statusFilter === "errors") return [];
    return sortQueueItems(runningRaw, sortMode === "errors_first" ? "newest" : sortMode);
  }, [runningRaw, statusFilter, sortMode]);
  const filteredPending = useMemo(() => {
    if (statusFilter === "errors") return [];
    return sortQueueItems(pendingRaw, sortMode === "errors_first" ? "newest" : sortMode);
  }, [pendingRaw, statusFilter, sortMode]);

  const ledgerPaused = Boolean(ledger?.paused);
  const ledgerBacklog = typeof ledger?.backlog_count === "number" ? ledger.backlog_count : 0;
  const subtitle = useMemo(() => {
    if (pageTab === "ledger") {
      return `Ledger — ${ledgerPaused ? "paused" : "live"} · backlog ${ledgerBacklog} · ${ledgerEvents.length} recent`;
    }
    const errBit = historyErrorCount ? ` · ${historyErrorCount} errors` : "";
    return `Comfy ops — running ${runningRaw.length} · waiting ${pendingRaw.length} · history ${history.length}${errBit}`;
  }, [
    pageTab,
    ledgerPaused,
    ledgerBacklog,
    ledgerEvents.length,
    runningRaw.length,
    pendingRaw.length,
    history.length,
    historyErrorCount,
  ]);

  const toggle = (key: SectionKey) => setShow((s) => ({ ...s, [key]: !s[key] }));

  const setErrorsFilter = () => {
    setStatusFilter("errors");
    setShow((s) => ({ ...s, history: true, running: false, pending: false }));
    setSortMode("errors_first");
  };

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
            {pageTab === "queue" ? (
              <button
                type="button"
                title="Empty Comfy waiting queue only — does not clear ledger restore state"
                onClick={() => {
                  void (async () => {
                    await comfyClear();
                    await refresh();
                  })();
                }}
              >
                Clear waiting
              </button>
            ) : null}
          </>
        }
      />
      <div className="wx-screen-tabs" role="tablist" aria-label="Queue page sections">
        <button
          type="button"
          role="tab"
          id="queue-page-tab-queue"
          aria-controls="queue-page-panel-queue"
          aria-selected={pageTab === "queue"}
          className={`wx-screen-tab${pageTab === "queue" ? " wx-screen-tab--active" : ""}`}
          onClick={() => setPageTab("queue")}
        >
          Queue
        </button>
        <button
          type="button"
          role="tab"
          id="queue-page-tab-ledger"
          aria-controls="queue-page-panel-ledger"
          aria-selected={pageTab === "ledger"}
          className={`wx-screen-tab${pageTab === "ledger" ? " wx-screen-tab--active" : ""}`}
          onClick={() => setPageTab("ledger")}
        >
          Ledger
        </button>
      </div>
      {pageTab === "queue" ? (
        <div
          className="queue-page-tabpanel"
          role="tabpanel"
          id="queue-page-panel-queue"
          aria-labelledby="queue-page-tab-queue"
        >
          <p className="queue-monitor-hint">
            <strong>Clear waiting</strong> empties Comfy pending. Ledger restore state is managed on the Ledger tab.
          </p>
          <div className="queue-monitor-toolbar">
            <PipelineFilterRow aria-label="Queue sections">
              <StatusChip
                status="running"
                label="running"
                count={filteredRunning.length}
                on={show.running && statusFilter !== "errors"}
                onToggle={() => {
                  setStatusFilter("all");
                  toggle("running");
                }}
                onFocusSolo={() => {
                  setStatusFilter("all");
                  setShow({ running: true, pending: false, history: false });
                }}
              />
              <StatusChip
                status="pending"
                label="waiting"
                count={filteredPending.length}
                on={show.pending && statusFilter !== "errors"}
                onToggle={() => {
                  setStatusFilter("all");
                  toggle("pending");
                }}
                onFocusSolo={() => {
                  setStatusFilter("all");
                  setShow({ running: false, pending: true, history: false });
                }}
              />
              <StatusChip
                status="ok"
                label="history"
                count={filteredHistory.length}
                on={show.history}
                onToggle={() => toggle("history")}
                onFocusSolo={() => {
                  setStatusFilter("all");
                  setShow({ running: false, pending: false, history: true });
                }}
              />
              <button
                type="button"
                className={`work-products-status-toggle work-products-status-toggle--error queue-filter-errors${
                  statusFilter === "errors" ? " is-on" : " is-off"
                }`}
                aria-pressed={statusFilter === "errors"}
                onClick={() => {
                  if (statusFilter === "errors") {
                    setStatusFilter("all");
                    setShow((s) => ({ ...s, running: true, pending: true, history: true }));
                  } else {
                    setErrorsFilter();
                  }
                }}
                onDoubleClick={(e) => {
                  e.preventDefault();
                  setErrorsFilter();
                }}
                title="Show only failed / interrupted history · double-click to focus errors"
              >
                <span className="work-products-status-toggle__label">errors</span>
                <span className="work-products-status-toggle__count">{historyErrorCount}</span>
              </button>
            </PipelineFilterRow>
            <label className="queue-monitor-sort">
              <span className="queue-monitor-sort__label">Sort</span>
              <select
                value={sortMode}
                onChange={(e) => setSortMode(e.target.value as SortMode)}
                aria-label="Sort queue items"
              >
                <option value="newest">Newest change</option>
                <option value="oldest">Oldest change</option>
                <option value="errors_first">Errors first</option>
                <option value="queue_index">Queue index</option>
              </select>
            </label>
            {statusFilter !== "all" ? (
              <button
                type="button"
                className="queue-monitor-filter-clear"
                onClick={() => {
                  setStatusFilter("all");
                  setShow({ running: true, pending: true, history: true });
                }}
              >
                Clear filters
              </button>
            ) : null}
          </div>
          {error ? <div className="queue-monitor-error">{error}</div> : null}
          <div className="queue-monitor-split">
            <PipelineScroll>
              <PipelineList>
                {show.running && statusFilter !== "errors" ? (
                  <>
                    <div className="pipeline-section-label">Running</div>
                    {filteredRunning.length ? (
                      filteredRunning.map((item, i) => (
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
                {show.pending && statusFilter !== "errors" ? (
                  <>
                    <div className="pipeline-section-label">Waiting</div>
                    {filteredPending.length ? (
                      filteredPending.map((item, i) => (
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
                    <div className="pipeline-section-label">
                      History
                      {statusFilter === "errors" ? " · errors only" : ""}
                      {historyErrorCount ? (
                        <span className="queue-history-error-count"> {historyErrorCount} failed</span>
                      ) : null}
                    </div>
                    {filteredHistory.length ? (
                      filteredHistory.map((h) => <HistoryItemRow key={h.prompt_id} item={h} />)
                    ) : (
                      <div className="pipeline-empty">
                        {statusFilter === "errors" ? "(no errors in recent history)" : "(no history)"}
                      </div>
                    )}
                  </>
                ) : null}
              </PipelineList>
            </PipelineScroll>
            <ComfyLogPanel />
          </div>
        </div>
      ) : (
        <div
          className="queue-page-tabpanel"
          role="tabpanel"
          id="queue-page-panel-ledger"
          aria-labelledby="queue-page-tab-ledger"
        >
          <p className="queue-monitor-hint">
            <strong>Clear ledger</strong> only forgets restore state so a restart won&apos;t put old jobs back. It does
            not empty Comfy waiting — use Clear waiting on the Queue tab for that.
          </p>
          <QueueLedgerPanel
            status={ledger}
            events={ledgerEvents}
            busy={ledgerBusy || loading}
            error={ledgerErr}
            onAction={(a) => void runLedgerAction(a)}
          />
        </div>
      )}
    </PipelineScreen>
  );
}
