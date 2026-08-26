import React, { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  comfyCancel,
  comfyClear,
  fetchComfyHistory,
  fetchComfyLogs,
  fetchQueue,
  fetchQueueLedgerEvents,
  fetchQueueLedgerStatus,
  moveQueuePrompt,
  saveQueueItemForLater,
  setQueueLedgerControl,
} from "./api";
import { ComfyLiveMetricsBar, ComfyLivePreview } from "./ComfyLivePreview";
import { discoveryLibraryHref, submitHref, workbenchHref } from "./discoveryDeepLink";
import { PageHeader } from "./PageHeader";
import { PipelineMediaPlayer, vhsWindowFromKeyParams } from "./PipelineMediaPlayer";
import { PipelineFilterRow, PipelineList, PipelineScreen, PipelineScroll } from "./PipelineScreen";
import { PromptPeekButton } from "./PromptPeek";
import { queryKeys } from "./queryKeys";
import type {
  ComfyHistoryItem,
  ComfyLogEntry,
  QueueComfyItem,
  QueueJobGlance,
  QueueLedgerControlAction,
  QueueLedgerEvent,
  QueueLedgerEntry,
  QueueLedgerStatus,
  WorkProductPromptProfile,
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

function formatVideoClock(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function queueTrimBadge(item: {
  key_params?: Record<string, unknown> | null;
  vhs_window?: {
    skip_first_frames?: number;
    frame_load_cap?: number;
    mark_in?: number;
    mark_out?: number;
  } | null;
}): { text: string; title: string } | null {
  const win = item.vhs_window || {};
  const kp = item.key_params || {};
  const skip = Number(win.skip_first_frames ?? kp.skip_first_frames ?? 0) || 0;
  const cap = Number(win.frame_load_cap ?? kp.frame_load_cap ?? 0) || 0;
  const markIn = win.mark_in != null ? Number(win.mark_in) : null;
  const markOut = win.mark_out != null ? Number(win.mark_out) : null;
  if (skip > 0 || cap > 0) {
    const text = cap > 0 ? `skip ${skip} · cap ${cap}` : `skip ${skip}`;
    return { text, title: "VHS loader window applied on this job (source input)" };
  }
  if (
    markIn != null &&
    markOut != null &&
    Number.isFinite(markIn) &&
    Number.isFinite(markOut) &&
    markOut > markIn + 0.05
  ) {
    return {
      text: `${formatVideoClock(markIn)}–${formatVideoClock(markOut)}`,
      title: "Use window (mark in/out) from factory job",
    };
  }
  return null;
}

type QueueGlanceRow = {
  key: string;
  label: string;
  value: string;
  title?: string;
  prompt?: WorkProductPromptProfile | null;
};

function queueGlanceRows(
  item: {
    glance?: QueueJobGlance | null;
    input_media_relpath?: string | null;
    job_key?: string | null;
    external?: boolean;
    prompt_profile?: WorkProductPromptProfile | null;
  },
  opts?: { trimBadge?: { text: string; title: string } | null },
): QueueGlanceRow[] {
  const g = item.glance || {};
  const rows: QueueGlanceRow[] = [];
  const push = (key: string, label: string, value: string | null | undefined, title?: string) => {
    const v = String(value || "").trim();
    if (!v) return;
    rows.push({ key, label, value: v, title });
  };

  if (g.is_hourly) push("hourly", "Kind", "Hourly", "Hourly planner job");
  push("family", "Family", g.family_slug || null, g.shape_id ? `shape ${g.shape_id}` : "Family");
  if (g.pick_mode || g.step) {
    const bits = [g.pick_mode, g.step && g.step !== g.pick_mode ? g.step : null].filter(Boolean);
    push("mode", "Mode", bits.join(" · "), g.step ? `step ${g.step}` : "Pick mode");
  }
  if (g.noise_seed != null && Number.isFinite(Number(g.noise_seed))) {
    push(
      "seed",
      "Seed",
      g.seed_mode ? `${Number(g.noise_seed)} · ${g.seed_mode}` : String(Number(g.noise_seed)),
      g.seed_mode ? `Noise seed · mode ${g.seed_mode}` : "Noise seed",
    );
  }
  if (opts?.trimBadge) {
    push("trim", "Trim", opts.trimBadge.text, opts.trimBadge.title);
  }
  {
    const samplerBits = [g.sampler_name, g.scheduler].filter(Boolean).map(String);
    const extras = [
      g.cfg != null && String(g.cfg).trim() !== "" ? `cfg ${g.cfg}` : null,
      g.steps != null && String(g.steps).trim() !== "" ? `steps ${g.steps}` : null,
      g.denoise != null && String(g.denoise).trim() !== "" && Number(g.denoise) !== 1
        ? `denoise ${g.denoise}`
        : null,
    ].filter(Boolean);
    if (samplerBits.length || extras.length) {
      push("sampler", "Sampler", [...samplerBits, ...extras].join(" · "));
    }
  }
  {
    const promptLabel =
      String(g.prompt_profile || item.prompt_profile?.basename || item.prompt_profile?.label || "").trim() ||
      null;
    const profile = item.prompt_profile;
    if (promptLabel && profile && !profile.missing) {
      rows.push({
        key: "prompt",
        label: "Prompt",
        value: promptLabel,
        title: profile.path || "Prompt profile",
        prompt: profile,
      });
    } else {
      push("prompt", "Prompt", promptLabel, "Prompt profile");
    }
  }
  const source = g.source_name || (item.input_media_relpath ? basename(item.input_media_relpath) : "");
  push("source", "Source", source || null);
  push("identity", "Identity", g.identity_name || null);
  if (item.external) push("origin", "Origin", "external", "Not mapped to an experiments run");
  const jobKey = String(item.job_key || "").trim();
  if (jobKey) push("job", "Job", jobKey);

  return rows;
}

function queueWorkflowKindBadge(item: {
  glance?: QueueJobGlance | null;
  input_media_kind?: string | null;
  input_media_relpath?: string | null;
}): { label: "Image" | "Extend"; title: string; className: string } | null {
  const fromGlance = String(item.glance?.workflow_kind || "").trim().toLowerCase();
  if (fromGlance === "image") {
    return { label: "Image", title: "Still-source / image workflow", className: "pipeline-row__kind-badge--image" };
  }
  if (fromGlance === "extend") {
    return { label: "Extend", title: "Video-source extend workflow", className: "pipeline-row__kind-badge--extend" };
  }
  const kind = String(item.input_media_kind || "").toLowerCase();
  if (kind === "image") {
    return { label: "Image", title: "Still-source / image workflow", className: "pipeline-row__kind-badge--image" };
  }
  if (kind === "video") {
    return { label: "Extend", title: "Video-source extend workflow", className: "pipeline-row__kind-badge--extend" };
  }
  const rel = String(item.input_media_relpath || item.glance?.source_name || "").toLowerCase();
  if (/\.(png|jpe?g|webp|gif)(\?|$)/i.test(rel)) {
    return { label: "Image", title: "Still-source / image workflow", className: "pipeline-row__kind-badge--image" };
  }
  if (/\.(mp4|webm|mov|mkv)(\?|$)/i.test(rel)) {
    return { label: "Extend", title: "Video-source extend workflow", className: "pipeline-row__kind-badge--extend" };
  }
  return null;
}

function queueTrimFromItem(item: {
  key_params?: Record<string, unknown> | null;
  vhs_window?: {
    skip_first_frames?: number;
    frame_load_cap?: number;
    mark_in?: number;
    mark_out?: number;
  } | null;
}) {
  const merged: Record<string, unknown> = { ...(item.key_params || {}) };
  const win = item.vhs_window;
  if (win) {
    if (win.skip_first_frames != null) merged.skip_first_frames = win.skip_first_frames;
    if (win.frame_load_cap != null) merged.frame_load_cap = win.frame_load_cap;
    if (win.mark_in != null) merged.mark_in = win.mark_in;
    if (win.mark_out != null) merged.mark_out = win.mark_out;
  }
  return vhsWindowFromKeyParams(merged);
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
  kindBadge,
  glanceRows,
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
  kindBadge?: { label: string; title: string; className: string } | null;
  glanceRows?: QueueGlanceRow[];
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
      className={`pipeline-row pipeline-row--status-${statusVisual}${
        live ? " pipeline-row--live" : ""
      }${isError ? " pipeline-row--error" : ""}`}
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
            <span className="pipeline-row__title-text" title={title}>
              {title}
            </span>
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
          {kindBadge ? (
            <div className="pipeline-row__kind">
              <span className={`pipeline-row__kind-badge ${kindBadge.className}`} title={kindBadge.title}>
                {kindBadge.label}
              </span>
            </div>
          ) : null}
          {errorMessage ? <p className="pipeline-row__error-line">{errorMessage}</p> : null}
          {glanceRows && glanceRows.length ? (
            <dl className="pipeline-row__glance" aria-label="Job summary">
              {glanceRows.map((row) => (
                <div key={row.key} className="pipeline-row__glance-row">
                  <dt>{row.label}</dt>
                  <dd
                    className={row.prompt ? "pipeline-row__glance-value--prompt" : "mono"}
                    title={row.prompt ? undefined : row.title || row.value}
                  >
                    {row.prompt ? <PromptPeekButton prompt={row.prompt} label={row.value} /> : row.value}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}
          <div className="pipeline-row__actions">{actions}</div>
        </div>
      </div>
    </article>
  );
}

/** Live Comfy rows: running or waiting. API cancel still uses Comfy's "pending" for waiting. */
type QueueLiveKind = "running" | "waiting";

function QueueItemRow({
  item,
  kind,
  movingPromptId,
  onMovePrompt,
  onRefresh,
}: {
  item: QueueComfyItem;
  kind: QueueLiveKind;
  movingPromptId?: string | null;
  onMovePrompt?: (item: QueueComfyItem, to: "front" | "back") => Promise<void>;
  onRefresh: () => void;
}) {
  const pid = item.prompt_id ?? "";
  const moveBusy = Boolean(pid) && movingPromptId === pid;
  const thumb = queueThumb(item);
  const videoUrl = queueVideoUrl(item);
  const jobKey = String(item.job_key || "").trim() || null;
  const workbenchUrl = workbenchHref({ jobKey, promptId: pid || null });
  // Waiting on Comfy (== queued factory jobs), not factory "pending" (not submitted yet).
  const editUrl =
    kind === "waiting" && jobKey
      ? submitHref({ editJob: jobKey, origin: "queue" })
      : null;
  const cancelKind = kind === "waiting" ? "pending" : "running";
  const family = String(item.glance?.family_slug || "").trim();
  const title =
    family ||
    basename(item.input_media_relpath) ||
    item.workflow_name ||
    shortId(pid, 16);
  const trim = queueTrimFromItem(item);
  const trimBadge = queueTrimBadge(item);
  const glanceRows = queueGlanceRows(
    {
      glance: item.glance,
      input_media_relpath: item.input_media_relpath,
      job_key: jobKey,
      external: item.external,
      prompt_profile: item.prompt_profile,
    },
    { trimBadge },
  );

  const sourcePlayer = (
    <PipelineMediaPlayer
      videoUrl={videoUrl}
      thumbUrl={thumb}
      mediaKey={`queue-${kind}:${pid || item.input_media_relpath || title}`}
      alt={title}
      readOnly
      vhsWindow={trim.window}
      fpsHint={trim.fpsHint}
      markIn={trim.markIn}
      markOut={trim.markOut}
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
      statusLabel={kind === "running" ? "running" : item.external ? "external" : "queued"}
      statusVisual={kind === "running" ? "running" : "queued"}
      promptId={pid}
      media={media}
      kindBadge={queueWorkflowKindBadge(item)}
      glanceRows={glanceRows}
      queuedAt={item.queued_at}
      changedAt={item.changed_at}
      live={kind === "running"}
      liveMetrics={kind === "running" && pid ? <ComfyLiveMetricsBar promptId={pid} /> : null}
      actions={
        <>
          <button
            type="button"
            disabled={!pid || moveBusy}
            title={kind === "running" ? "Interrupt current ComfyUI execution" : "Remove from Comfy waiting queue"}
            onClick={() => {
              void (async () => {
                if (!pid) return;
                await comfyCancel({ prompt_id: pid, kind: cancelKind });
                onRefresh();
              })();
            }}
          >
            {kind === "running" ? "Interrupt" : "Cancel"}
          </button>
          <button
            type="button"
            disabled={moveBusy}
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
          {kind === "waiting" && pid ? (
            <>
              <button
                type="button"
                disabled={moveBusy}
                title="Move this waiting prompt to the front of the queue"
                onClick={() => {
                  void onMovePrompt?.(item, "front");
                }}
              >
                {moveBusy ? "Moving…" : "Move to top"}
              </button>
              <button
                type="button"
                disabled={moveBusy}
                title="Move this waiting prompt to the back of the queue"
                onClick={() => {
                  void onMovePrompt?.(item, "back");
                }}
              >
                {moveBusy ? "Moving…" : "Move to bottom"}
              </button>
            </>
          ) : null}
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
          {editUrl ? (
            <a
              className="drt-btn"
              href={editUrl}
              title="Edit this run in Submit (unqueues if waiting on Comfy; holds pending-drain)"
            >
              Edit
            </a>
          ) : null}
        </>
      }
    />
  );
}

function HistoryItemRow({ item }: { item: ComfyHistoryItem }) {
  const thumb = historyThumb(item);
  const videoUrl = item.primary_video_url || null;
  const jobKey = String(item.job_key || "").trim() || null;
  const pid = String(item.prompt_id || "").trim();
  const family = String(item.glance?.family_slug || "").trim();
  const title =
    family ||
    item.workflow_name ||
    basename(item.primary_video_relpath) ||
    basename(item.primary_image_relpath) ||
    shortId(item.prompt_id, 16);
  const libraryRel = historyAssetRelpath(item);
  const workbenchUrl = workbenchHref({ jobKey, promptId: pid || null });
  // Done plays the *output* clip — full timeline (0→end). Source Use-window marks
  // belong on queued/running input preview only; still show Trim in glance for history.
  const trimBadge = queueTrimBadge(item);
  const glanceRows = queueGlanceRows(
    {
      glance: item.glance,
      input_media_relpath: item.input_media_relpath,
      job_key: jobKey,
      prompt_profile: item.prompt_profile,
    },
    { trimBadge },
  );
  const statusVisual = historyStatusVisual(item.status);
  const statusLabel =
    statusVisual === "error"
      ? item.hollow_success
        ? "no output"
        : "error"
      : statusVisual === "interrupted"
        ? "interrupted"
        : item.status || "done";
  const errLine = item.error_message ? String(item.error_message).trim() : null;

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
      kindBadge={queueWorkflowKindBadge(item)}
      glanceRows={glanceRows}
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
  queue_parked: "Parked queue",
  refill_restored: "Refill",
  spillover_removed: "Spillover",
  actions_paused: "Paused",
  drain_once_ack: "Drain once",
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
  if (typeof ev.added === "number") parts.push(`+${ev.added}`);
  if (typeof ev.skipped === "number" && ev.skipped > 0) parts.push(`skipped ${ev.skipped}`);
  if (typeof ev.no_prompt === "number" && ev.no_prompt > 0) parts.push(`no prompt ${ev.no_prompt}`);
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

function FeederPill({
  on,
  onLabel,
  offLabel,
  unknownLabel,
}: {
  on: boolean | null | undefined;
  onLabel: string;
  offLabel: string;
  unknownLabel: string;
}) {
  const cls = on === true ? " queue-ledger__pill--on" : on === false ? " queue-ledger__pill--off" : "";
  const label = on === true ? onLabel : on === false ? offLabel : unknownLabel;
  return <span className={`queue-ledger__pill${cls}`}>{label}</span>;
}

function QueueLedgerPanel({
  status,
  events,
  busy,
  error,
  notice,
  onAction,
}: {
  status: QueueLedgerStatus | null;
  events: QueueLedgerEvent[];
  busy: boolean;
  error: string;
  notice: string;
  onAction: (action: QueueLedgerControlAction) => void;
}) {
  const paused = Boolean(status?.paused || status?.ops?.ledger?.paused);
  const breakerOpen = Boolean(status?.breaker?.open);
  const backlog = typeof status?.backlog_count === "number" ? status.backlog_count : 0;
  const knownCount = typeof status?.known_count === "number" ? status.known_count : 0;
  const entries = Array.isArray(status?.entries) ? status.entries : [];
  const ops = status?.ops;
  const hourlyOn = ops?.hourly?.enabled;
  const drainOn = ops?.drain?.active;
  const watchOn = ops?.watch_queue?.running;
  const comfyRun = ops?.comfy?.running;
  const comfyPend = ops?.comfy?.pending;
  const lastParkAt = ops?.ledger?.last_park_at;
  const lastParkAdded = ops?.ledger?.last_park?.added;
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
            Shadows Comfy&apos;s queue and can restore it after restarts. Suspend parks live jobs into the backlog,
            empties Comfy, and stops drain + watch-queue. Clear ledger forgets that snapshot — it does not empty Comfy
            waiting.
          </p>
          <div className="queue-ledger__ops" aria-label="Comfy feeders">
            <div className="queue-ledger__status" aria-live="polite">
              <FeederPill
                on={typeof comfyRun === "number" || typeof comfyPend === "number" ? (comfyRun || 0) + (comfyPend || 0) === 0 : null}
                onLabel={`Comfy idle`}
                offLabel={`Comfy ${typeof comfyRun === "number" ? comfyRun : "—"} run · ${typeof comfyPend === "number" ? comfyPend : "—"} wait`}
                unknownLabel="Comfy —"
              />
              <FeederPill on={hourlyOn} onLabel="Hourlies on" offLabel="Hourlies off" unknownLabel="Hourlies —" />
              <FeederPill on={drainOn} onLabel="Drain on" offLabel="Drain off" unknownLabel="Drain —" />
              <FeederPill on={watchOn} onLabel="Watch-queue on" offLabel="Watch-queue off" unknownLabel="Watch-queue —" />
            </div>
            <div className="queue-ledger__actions">
              <button
                type="button"
                disabled={busy}
                title="Park live jobs, empty Comfy, stop drain and watch-queue"
                onClick={() => {
                  if (
                    !window.confirm(
                      "Suspend Comfy?\n\nThis parks the live queue into the ledger backlog, interrupts/clears Comfy, and stops pending-drain + watch-queue. Jobs are kept for later. Takes ~30 seconds.",
                    )
                  ) {
                    return;
                  }
                  onAction("suspend");
                }}
              >
                {busy ? "Working…" : "Suspend Comfy"}
              </button>
              <button
                type="button"
                disabled={busy}
                title="Unpause ledger restore and restart drain + watch-queue"
                onClick={() => {
                  if (
                    !window.confirm(
                      "Resume Comfy?\n\nThis unpauses the ledger and restarts pending-drain + watch-queue. Parked jobs will refill toward 2 waiting slots.",
                    )
                  ) {
                    return;
                  }
                  onAction("resume-ops");
                }}
              >
                Resume Comfy
              </button>
            </div>
            <div className="queue-ledger__actions">
              {hourlyOn ? (
                <button type="button" disabled={busy} onClick={() => onAction("hourlies-off")}>
                  Disable hourlies
                </button>
              ) : (
                <button type="button" disabled={busy} onClick={() => onAction("hourlies-on")}>
                  Enable hourlies
                </button>
              )}
              {drainOn ? (
                <button type="button" disabled={busy} onClick={() => onAction("drain-off")}>
                  Stop drain
                </button>
              ) : (
                <button type="button" disabled={busy} onClick={() => onAction("drain-on")}>
                  Start drain
                </button>
              )}
              {watchOn ? (
                <button type="button" disabled={busy} onClick={() => onAction("watch-off")}>
                  Stop watch-queue
                </button>
              ) : (
                <button type="button" disabled={busy} onClick={() => onAction("watch-on")}>
                  Start watch-queue
                </button>
              )}
            </div>
            {lastParkAt ? (
              <p className="queue-ledger__meta">
                Last park {formatLedgerUpdated(lastParkAt)}
                {typeof lastParkAdded === "number" ? ` · ${lastParkAdded} jobs` : ""}
              </p>
            ) : null}
          </div>
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
              <button type="button" disabled={busy} title="Allow ledger restore/refill" onClick={() => onAction("resume")}>
                Resume restore
              </button>
            ) : (
              <button type="button" disabled={busy} title="Stop ledger restore/refill (does not empty Comfy)" onClick={() => onAction("pause")}>
                Pause restore
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
          {notice ? <p className="queue-ledger__notice">{notice}</p> : null}
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

const QUEUE_HISTORY_LIMIT = 80;
const LEDGER_EVENTS_LIMIT = 30;
const QUEUE_POLL_MS = 5000;

export function ComfyQueueMonitorApp() {
  const queryClient = useQueryClient();
  const [ledgerNotice, setLedgerNotice] = useState("");
  const [ledgerActionErr, setLedgerActionErr] = useState("");
  const [queueActionMsg, setQueueActionMsg] = useState("");
  const [movingPromptId, setMovingPromptId] = useState<string | null>(null);
  const [pageTab, setPageTab] = useState<"queue" | "ledger">("queue");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sortMode, setSortMode] = useState<SortMode>("newest");
  const [show, setShow] = useState<Record<SectionKey, boolean>>({
    running: true,
    pending: true,
    history: true,
  });
  const snapshotQuery = useQuery({
    queryKey: queryKeys.queue.snapshot,
    queryFn: () => fetchQueue(),
    staleTime: 5_000,
    refetchInterval: QUEUE_POLL_MS,
    refetchIntervalInBackground: false,
    placeholderData: (prev) => prev,
  });
  const historyQuery = useQuery({
    queryKey: queryKeys.queue.history,
    queryFn: () => fetchComfyHistory(QUEUE_HISTORY_LIMIT),
    staleTime: 5_000,
    refetchInterval: QUEUE_POLL_MS,
    refetchIntervalInBackground: false,
    placeholderData: (prev) => prev,
  });
  const ledgerStatusQuery = useQuery({
    queryKey: queryKeys.queue.ledgerStatus,
    queryFn: () => fetchQueueLedgerStatus(),
    staleTime: 5_000,
    refetchInterval: QUEUE_POLL_MS,
    refetchIntervalInBackground: false,
    placeholderData: (prev) => prev,
  });
  const ledgerEventsQuery = useQuery({
    queryKey: queryKeys.queue.ledgerEvents(LEDGER_EVENTS_LIMIT),
    queryFn: () => fetchQueueLedgerEvents(LEDGER_EVENTS_LIMIT),
    staleTime: 5_000,
    refetchInterval: QUEUE_POLL_MS,
    refetchIntervalInBackground: false,
    placeholderData: (prev) => prev,
  });
  const ledgerControlMutation = useMutation({
    mutationFn: (action: QueueLedgerControlAction) => setQueueLedgerControl(action),
  });
  const movePromptMutation = useMutation({
    mutationFn: moveQueuePrompt,
  });

  const data = snapshotQuery.data ?? null;
  const history = Array.isArray(historyQuery.data?.items) ? historyQuery.data.items : [];
  const ledger = ledgerStatusQuery.data ?? null;
  const ledgerEvents = Array.isArray(ledgerEventsQuery.data?.events) ? ledgerEventsQuery.data.events : [];
  const hasData = Boolean(data || history.length || ledger || ledgerEvents.length);
  const loading = !hasData && (snapshotQuery.isLoading || historyQuery.isLoading);
  const refreshing =
    hasData &&
    (snapshotQuery.isFetching || historyQuery.isFetching || ledgerStatusQuery.isFetching || ledgerEventsQuery.isFetching);
  const errorSource = snapshotQuery.error ?? historyQuery.error;
  const error = errorSource instanceof Error ? errorSource.message : "";
  const ledgerErrSource = ledgerActionErr || (ledgerStatusQuery.error instanceof Error ? ledgerStatusQuery.error.message : "");
  const ledgerBusy = ledgerControlMutation.isPending;

  const refresh = async () => {
    setLedgerActionErr("");
    await Promise.all([
      snapshotQuery.refetch(),
      historyQuery.refetch(),
      ledgerStatusQuery.refetch(),
      ledgerEventsQuery.refetch(),
    ]);
  };

  const invalidateQueue = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.queue.snapshot }),
      queryClient.invalidateQueries({ queryKey: queryKeys.queue.history }),
      queryClient.invalidateQueries({ queryKey: queryKeys.queue.ledgerRoot }),
    ]);

  const runLedgerAction = async (action: QueueLedgerControlAction) => {
    setLedgerActionErr("");
    setLedgerNotice("");
    try {
      const res = await ledgerControlMutation.mutateAsync(action);
      if (res.note) setLedgerNotice(res.note);
      await invalidateQueue();
    } catch (e) {
      setLedgerActionErr(e instanceof Error ? e.message : String(e));
    }
  };

  const movePendingPrompt = async (item: QueueComfyItem, to: "front" | "back") => {
    const pid = String(item.prompt_id || "").trim();
    if (!pid) return;
    setMovingPromptId(pid);
    setQueueActionMsg("");
    try {
      await movePromptMutation.mutateAsync({
        prompt_id: pid,
        to,
        client_id: "experiments-ui.queue-reorder",
      });
      setQueueActionMsg(to === "front" ? `Moved ${shortId(pid, 14)} to top` : `Moved ${shortId(pid, 14)} to bottom`);
      await invalidateQueue();
    } catch (e) {
      setQueueActionMsg(e instanceof Error ? e.message : String(e));
      await invalidateQueue();
    } finally {
      setMovingPromptId(null);
    }
  };

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

  const ledgerPaused = Boolean(ledger?.paused || ledger?.ops?.ledger?.paused);
  const ledgerBacklog = typeof ledger?.backlog_count === "number" ? ledger.backlog_count : 0;
  const hourlyOn = ledger?.ops?.hourly?.enabled;
  const drainOn = ledger?.ops?.drain?.active;
  const watchOn = ledger?.ops?.watch_queue?.running;
  const subtitle = useMemo(() => {
    if (pageTab === "ledger") {
      const bits = [
        ledgerPaused ? "paused" : "live",
        `backlog ${ledgerBacklog}`,
        hourlyOn === false ? "hourlies off" : hourlyOn === true ? "hourlies on" : null,
        drainOn === false ? "drain off" : drainOn === true ? "drain on" : null,
        watchOn === false ? "watch off" : watchOn === true ? "watch on" : null,
      ].filter(Boolean);
      return `Ledger — ${bits.join(" · ")}`;
    }
    const errBit = historyErrorCount ? ` · ${historyErrorCount} errors` : "";
    return `Comfy ops — running ${runningRaw.length} · waiting ${pendingRaw.length} · history ${history.length}${errBit}`;
  }, [
    pageTab,
    ledgerPaused,
    ledgerBacklog,
    hourlyOn,
    drainOn,
    watchOn,
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
            {refreshing ? (
              <span className="page-header__updating" aria-live="polite">
                updating…
              </span>
            ) : null}
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading && !hasData}
            >
              {refreshing ? "Updating…" : "Refresh"}
            </button>
            {pageTab === "queue" ? (
              <button
                type="button"
                title="Empty Comfy waiting queue only — does not clear ledger restore state"
                onClick={() => {
                  void (async () => {
                    await comfyClear();
                    await invalidateQueue();
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
          {queueActionMsg ? <div className="queue-monitor-error">{queueActionMsg}</div> : null}
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
                          movingPromptId={movingPromptId}
                          onRefresh={() => void invalidateQueue()}
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
                          kind="waiting"
                          movingPromptId={movingPromptId}
                          onMovePrompt={movePendingPrompt}
                          onRefresh={() => void invalidateQueue()}
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
            <strong>Suspend Comfy</strong> parks live jobs and empties the GPU queue.{" "}
            <strong>Clear ledger</strong> only forgets restore state so a restart won&apos;t put old jobs back — it does
            not empty Comfy waiting.
          </p>
          <QueueLedgerPanel
            status={ledger}
            events={ledgerEvents}
            busy={ledgerBusy}
            error={ledgerErrSource}
            notice={ledgerNotice}
            onAction={(a) => void runLedgerAction(a)}
          />
        </div>
      )}
    </PipelineScreen>
  );
}
