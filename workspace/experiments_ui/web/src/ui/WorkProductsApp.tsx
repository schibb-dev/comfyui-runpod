import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { discardShapeFactoryJob, fetchShapeFactoryJsonPeek, fetchShapeFactoryWorkProducts, replayShapeFactory, unqueueShapeFactory, updatePendingShapeFactoryTrim } from "./api";
import type { ShapeFactoryClip } from "./api";
import { ClipBookmarksRail } from "./ClipBookmarksRail";
import { ComfyLiveMetricsBar, ComfyLivePreview } from "./ComfyLivePreview";
import { PageHeader } from "./PageHeader";
import { PipelineScreen } from "./PipelineScreen";
import { VideoTrimControls } from "./VideoTrimControls";
import {
  loadDiscoveryTrimAsync,
  persistDiscoveryTrimAsync,
  TRIM_CONTEXT_WORK_PRODUCTS,
} from "./discoveryTrimStorage";
import {
  familyVhsDefaults,
  marksToVhsWindow,
  originGenerationBands,
  parseFps,
  vhsDefaultsToMarks,
} from "./workProductTrim";
import { useTrimPlaybackEnforcement, type TrimPlaybackMode } from "./useTrimPlayback";
import { discoveryLibraryHref, parseWorkbenchDeepLink, submitHref } from "./discoveryDeepLink";
import type {
  ShapeFactoryMapQueueOverrides,
  WorkItem,
  WorkProductBinding,
  WorkProductDetailRow,
  WorkProductFamilyOption,
  WorkProductItem,
  WorkProductPromptProfile,
  WorkProductPromptRow,
  WorkProductShapeProfile,
  WorkProductShapeSlot,
} from "./types";

type RowLayout = "stacked" | "split";

const LAYOUT_KEY = "work-products-row-layout";
const SORT_KEY = "work-products-sort";
const SECTION_OPEN_KEY = "work-products-section-open";
const HOURLY_ONLY_KEY = "work-products-hourly-only";
const STATUS_FILTER_OFF_KEY = "work-products-status-filter-off";
const MARKER_FILTER_OFF_KEY = "work-products-marker-filter-off";
const JSON_CACHE = new Map<string, { text: string; basename?: string; truncated?: boolean; error?: string }>();

type WorkProductSort = "created_desc" | "created_asc" | "family_asc" | "family_desc" | "status" | "pick_mode";

/** Display order for status filter toggles (unknown statuses sort after these). */
const STATUS_FILTER_ORDER = [
  "running",
  "queued",
  "pending",
  "submitted",
  "complete",
  "deposited",
  "error",
  "failed",
  "interrupted",
  "abandoned",
  "unknown",
] as const;

/** Display order for pick-mode / step marker toggles. */
const MARKER_FILTER_ORDER = [
  "extend",
  "replay",
  "derive",
  "predicted_derive",
  "predicted",
  "product",
  "zip",
  "other",
] as const;

const SORT_OPTIONS: Array<{ id: WorkProductSort; label: string }> = [
  { id: "created_desc", label: "Newest" },
  { id: "created_asc", label: "Oldest" },
  { id: "family_asc", label: "Family A–Z" },
  { id: "family_desc", label: "Family Z–A" },
  { id: "status", label: "Status" },
  { id: "pick_mode", label: "Pick mode" },
];

/** Sections start open unless the operator collapsed them. */
const DEFAULT_SECTION_OPEN: Record<string, boolean> = {
  prompt: true,
  run: true,
  timing: true,
  plan: true,
  appetite: false,
  shape: true,
  bindings: false,
  other: false,
};


/** Shape-contract roles from *.shape.yaml — pipeline wiring labels. */
const SHAPE_ROLE_HELP: Record<string, string> = {
  A: "Still/image input",
  B: "Video input",
  C: "Prompt / text",
  X: "Work product (output; may feed the next stage)",
};

function loadLayout(): RowLayout {
  try {
    const v = localStorage.getItem(LAYOUT_KEY);
    if (v === "stacked" || v === "split") return v;
  } catch {
    /* ignore */
  }
  return "split";
}

function persistLayout(layout: RowLayout) {
  try {
    localStorage.setItem(LAYOUT_KEY, layout);
  } catch {
    /* ignore */
  }
}

function loadSort(): WorkProductSort {
  try {
    const v = localStorage.getItem(SORT_KEY);
    if (SORT_OPTIONS.some((o) => o.id === v)) return v as WorkProductSort;
  } catch {
    /* ignore */
  }
  return "created_desc";
}

function persistSort(sort: WorkProductSort) {
  try {
    localStorage.setItem(SORT_KEY, sort);
  } catch {
    /* ignore */
  }
}

function loadHourlyOnly(): boolean {
  try {
    const v = localStorage.getItem(HOURLY_ONLY_KEY);
    if (v === "1" || v === "true") return true;
    if (v === "0" || v === "false") return false;
  } catch {
    /* ignore */
  }
  return false;
}

function persistHourlyOnly(hourlyOnly: boolean) {
  try {
    localStorage.setItem(HOURLY_ONLY_KEY, hourlyOnly ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function loadStatusFilterOff(): Set<string> {
  return loadStringSet(STATUS_FILTER_OFF_KEY);
}

function persistStatusFilterOff(off: Set<string>) {
  persistStringSet(STATUS_FILTER_OFF_KEY, off);
}

function loadMarkerFilterOff(): Set<string> {
  return loadStringSet(MARKER_FILTER_OFF_KEY);
}

function persistMarkerFilterOff(off: Set<string>) {
  persistStringSet(MARKER_FILTER_OFF_KEY, off);
}

function loadStringSet(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.map((x) => String(x || "").toLowerCase().trim()).filter(Boolean));
  } catch {
    return new Set();
  }
}

function persistStringSet(key: string, values: Set<string>) {
  try {
    localStorage.setItem(key, JSON.stringify([...values].sort()));
  } catch {
    /* ignore */
  }
}

function workProductStatusKey(item: WorkProductItem): string {
  return String(item.status || "pending").toLowerCase().trim() || "pending";
}

/** Coarse action marker: pick_mode, else step, else other. */
function workProductMarkerKey(item: WorkProductItem): string {
  const pick = String(item.pick_mode || "").toLowerCase().trim();
  if (pick) return pick;
  const step = String(item.step || "").toLowerCase().trim();
  if (step) return step;
  return "other";
}

function markerFilterLabel(marker: string): string {
  return marker === "other" ? "other" : marker;
}

function sortFilterKeys(keys: Iterable<string>, order: readonly string[]): string[] {
  const rank = new Map(order.map((s, i) => [s, i]));
  return [...new Set(keys)].sort((a, b) => {
    const ar = rank.has(a) ? (rank.get(a) as number) : 1000;
    const br = rank.has(b) ? (rank.get(b) as number) : 1000;
    return ar - br || a.localeCompare(b);
  });
}

function collectAvailableStatuses(items: WorkProductItem[]): string[] {
  return sortFilterKeys(
    items.map(workProductStatusKey),
    STATUS_FILTER_ORDER,
  );
}

function collectAvailableMarkers(items: WorkProductItem[]): string[] {
  return sortFilterKeys(
    items.map(workProductMarkerKey),
    MARKER_FILTER_ORDER,
  );
}

function filterWorkProductsByStatus(items: WorkProductItem[], statusOff: Set<string>): WorkProductItem[] {
  if (!statusOff.size) return items;
  return items.filter((it) => !statusOff.has(workProductStatusKey(it)));
}

function filterWorkProductsByMarker(items: WorkProductItem[], markerOff: Set<string>): WorkProductItem[] {
  if (!markerOff.size) return items;
  return items.filter((it) => !markerOff.has(workProductMarkerKey(it)));
}

function loadSectionOpen(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(SECTION_OPEN_KEY);
    if (!raw) return { ...DEFAULT_SECTION_OPEN };
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object") return { ...DEFAULT_SECTION_OPEN };
    const out = { ...DEFAULT_SECTION_OPEN };
    for (const [k, v] of Object.entries(parsed)) {
      if (typeof v === "boolean") out[k] = v;
    }
    return out;
  } catch {
    return { ...DEFAULT_SECTION_OPEN };
  }
}

function persistSectionOpen(map: Record<string, boolean>) {
  try {
    localStorage.setItem(SECTION_OPEN_KEY, JSON.stringify(map));
  } catch {
    /* ignore */
  }
}

function isInFlightStatus(status?: string | null): boolean {
  const s = (status || "").toLowerCase();
  return !s || s === "queued" || s === "pending" || s === "running" || s === "submitted";
}

/**
 * Trim in/out is editable while the job is still ours (pending / not on Comfy).
 * Edits patch that job's VHS window before submit. Locked once Comfy has
 * accepted it (queued or running). Completed cards keep next-action trim
 * editable for Extend / Vary / Derive / Re-run planning (sidecar only).
 */
function isJobTrimEditable(item: WorkProductItem): boolean {
  if (item.output_url) return true;
  const s = workProductStatusKey(item);
  // Explicitly on Comfy's waiting or running list — baked prompt, no edits.
  if (s === "queued" || s === "running") return false;
  // Still pre-Comfy: pending/draft/deposited, or any status without a prompt_id.
  if (s === "pending" || s === "draft" || s === "deposited") return true;
  if (!String(item.prompt_id || "").trim()) return true;
  // Has a prompt_id under another in-flight label (e.g. submitted) — treat as locked.
  return false;
}

/** Pending factory jobs: trim edits rewrite the job workflow (not just the sidecar). */
function canUpdatePendingJobTrim(item: WorkProductItem): boolean {
  if (isNonFactoryWorkProduct(item)) return false;
  if (item.output_url) return false;
  if (!String(item.job_key || "").trim() && !String(item.job_path || "").trim()) return false;
  return isJobTrimEditable(item);
}

/** Comfy /history failure merged into Workbench with no factory job file. */
function isHistoryFailureStub(item: WorkProductItem): boolean {
  if (String(item.job_path || "").trim()) return false;
  if (item.history_from_comfy) return true;
  const construction = item.construction || {};
  if (String(construction.source || "") === "comfy_history") return true;
  const s = workProductStatusKey(item);
  if (!(s === "error" || s === "failed" || s === "interrupted" || s === "abandoned")) return false;
  return Boolean(String(item.prompt_id || "").trim() || String(item.job_key || "").trim());
}

/** Synthetic Comfy live stub — no factory .job.json to demote to pending. */
function isNonFactoryWorkProduct(item: WorkProductItem): boolean {
  if (isHistoryFailureStub(item)) return false;
  if (!String(item.job_path || "").trim()) return true;
  const key = String(item.job_key || "");
  if (key.startsWith("live__")) return true;
  const construction = item.construction || {};
  if (String(construction.source || "") === "comfy_queue") return true;
  return false;
}

function canUnqueueWorkProduct(item: WorkProductItem): boolean {
  if (workProductStatusKey(item) !== "queued") return false;
  return Boolean(String(item.prompt_id || "").trim());
}

/** Pending (pre-Comfy) factory jobs can be hard-deleted from the active set. */
function canDiscardPendingWorkProduct(item: WorkProductItem): boolean {
  if (isNonFactoryWorkProduct(item)) return false;
  if (!String(item.job_key || "").trim() && !String(item.job_path || "").trim()) return false;
  if (item.output_url) return false;
  const s = workProductStatusKey(item);
  if (s === "queued" || s === "running" || s === "submitted") return false;
  if (s === "complete" || s === "completed" || s === "error" || s === "failed" || s === "interrupted") return false;
  // pending / draft / deposited / empty — and no live prompt_id preferred
  if (String(item.prompt_id || "").trim() && (s === "queued" || s === "running")) return false;
  return s === "pending" || s === "draft" || s === "deposited" || !s;
}

/**
 * Terminal failures remain in Work Products as the failure record until archived/deleted.
 * Archive soft-renames to `.discarded` (forensics on disk; no restore UI).
 * History-only stubs (no .job.json) are dismissible the same way.
 */
function canArchiveTerminalWorkProduct(item: WorkProductItem): boolean {
  const s = workProductStatusKey(item);
  if (!(s === "error" || s === "failed" || s === "interrupted" || s === "abandoned")) return false;
  if (isHistoryFailureStub(item)) return true;
  if (isNonFactoryWorkProduct(item)) return false;
  if (!String(item.job_key || "").trim() && !String(item.job_path || "").trim()) return false;
  return true;
}

/** Pending drafts or terminal failures — hard-delete job JSON (+ sidecars). */
function canDeleteWorkProduct(item: WorkProductItem): boolean {
  return canDiscardPendingWorkProduct(item) || canArchiveTerminalWorkProduct(item);
}

function isRunningLiveItem(item: WorkProductItem): boolean {
  if (item.output_url) return false;
  if (String(item.status || "").toLowerCase() !== "running") return false;
  return Boolean(item.prompt_id) || Boolean(item.live_from_comfy);
}

/** Queued/pending/submitted — pin + quiet refresh (still expected to finish). */
function isWaitingPreviewItem(item: WorkProductItem): boolean {
  if (item.output_url || isRunningLiveItem(item)) return false;
  return isInFlightStatus(item.status);
}

/**
 * Any job with no output yet (or no recoverable output): source thumb + status badge.
 * Covers pending/queued/submitted, error/failed, interrupted, abandoned, unknown, …
 */
function isSourceThumbPreviewItem(item: WorkProductItem): boolean {
  return !item.output_url && !isRunningLiveItem(item);
}

function isLivePreviewItem(item: WorkProductItem): boolean {
  if (item.live_from_comfy && item.prompt_id && !item.output_url) return true;
  return isRunningLiveItem(item) || isWaitingPreviewItem(item);
}

type SourceThumbVisual = "queued" | "pending" | "error" | "interrupted" | "muted";

function sourceThumbPreviewMeta(item: WorkProductItem): { label: string; visual: SourceThumbVisual } {
  const s = String(item.status || "").toLowerCase().trim() || "pending";
  if (s === "queued") return { label: "queued", visual: "queued" };
  if (s === "error" || s === "failed") return { label: s, visual: "error" };
  if (s === "interrupted") return { label: "interrupted", visual: "interrupted" };
  if (s === "abandoned" || s === "unknown") return { label: s, visual: "muted" };
  // Do not relabel submitted as pending — pending means pre-Comfy / editable.
  if (s === "submitted") return { label: "submitted", visual: "queued" };
  if (s === "complete" || s === "deposited") return { label: s, visual: "muted" };
  return { label: s, visual: "pending" };
}

function createdMs(item: WorkProductItem): number {
  if (!item.created_at) return 0;
  const t = Date.parse(item.created_at);
  return Number.isFinite(t) ? t : 0;
}

function sortWorkProducts(items: WorkProductItem[], sort: WorkProductSort): WorkProductItem[] {
  const live: WorkProductItem[] = [];
  const rest: WorkProductItem[] = [];
  for (const it of items) {
    if (isLivePreviewItem(it)) live.push(it);
    else rest.push(it);
  }

  const byCreatedDesc = (a: WorkProductItem, b: WorkProductItem) => createdMs(b) - createdMs(a);
  const cmp = (a: WorkProductItem, b: WorkProductItem): number => {
    switch (sort) {
      case "created_asc":
        return createdMs(a) - createdMs(b);
      case "family_asc":
        return String(a.family_slug || "").localeCompare(String(b.family_slug || "")) || byCreatedDesc(a, b);
      case "family_desc":
        return String(b.family_slug || "").localeCompare(String(a.family_slug || "")) || byCreatedDesc(a, b);
      case "status":
        return String(a.status || "").localeCompare(String(b.status || "")) || byCreatedDesc(a, b);
      case "pick_mode":
        return (
          String(a.pick_mode || a.step || "").localeCompare(String(b.pick_mode || b.step || "")) ||
          byCreatedDesc(a, b)
        );
      case "created_desc":
      default:
        return byCreatedDesc(a, b);
    }
  };

  live.sort((a, b) => {
    const ar = String(a.status || "").toLowerCase() === "running" ? 0 : 1;
    const br = String(b.status || "").toLowerCase() === "running" ? 0 : 1;
    return ar - br || byCreatedDesc(a, b);
  });
  rest.sort(cmp);
  return [...live, ...rest];
}

function workProductNameHaystack(item: WorkProductItem): string {
  return [item.family_slug, item.job_key, item.status, item.prompt_id].filter(Boolean).join(" ").toLowerCase();
}

function filterWorkProductsByName(items: WorkProductItem[], query: string): WorkProductItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  return items.filter((it) => isLivePreviewItem(it) || workProductNameHaystack(it).includes(q));
}

function statusFilterVisual(status: string): string {
  const s = status.toLowerCase();
  if (s === "running") return "running";
  if (s === "queued") return "queued";
  if (s === "error" || s === "failed") return "error";
  if (s === "interrupted") return "interrupted";
  if (s === "complete" || s === "deposited") return "ok";
  if (s === "abandoned" || s === "unknown") return "muted";
  return "pending";
}

function statusFilterButtonClass(status: string, on: boolean): string {
  return `work-products-status-toggle work-products-status-toggle--${statusFilterVisual(status)}${
    on ? " is-on" : " is-off"
  }`;
}

function markerFilterVisual(marker: string): string {
  const s = marker.toLowerCase();
  if (s === "extend") return "extend";
  if (s === "replay") return "replay";
  if (s === "derive" || s === "predicted_derive" || s === "predicted") return "derive";
  if (s === "product") return "ok";
  if (s === "other" || s === "unset") return "muted";
  return "pending";
}

function markerFilterButtonClass(marker: string, on: boolean): string {
  return `work-products-status-toggle work-products-status-toggle--${markerFilterVisual(marker)}${
    on ? " is-on" : " is-off"
  }`;
}

function formatWhen(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatDurationSec(sec?: number | null): string {
  if (sec == null || !Number.isFinite(sec) || sec < 0) return "";
  if (sec < 90) return `${Math.round(sec)}s`;
  if (sec < 3600) {
    const m = sec / 60;
    return `${m < 10 ? m.toFixed(1) : Math.round(m)}m`;
  }
  return `${(sec / 3600).toFixed(1)}h`;
}

function timingHeadline(item: WorkProductItem): { text: string; title: string; bad: boolean } | null {
  const t = item.timing;
  if (!t) return null;
  const bad = Boolean(t.error) || String(item.status || "").toLowerCase() === "error";
  const parts: string[] = [];
  const exec = formatDurationSec(t.exec_sec);
  if (exec) parts.push(`${exec} exec`);
  const wait = formatDurationSec(t.wait_sec);
  if (wait && (t.wait_sec || 0) >= 1) parts.push(`${wait} queue`);
  const load = formatDurationSec(t.load_sec);
  if (load && (t.load_sec || 0) >= 0.5) parts.push(`${load} load`);
  const unload = formatDurationSec(t.unload_to_reload_sec);
  if (unload && (t.unload_to_reload_sec || 0) >= 0.5) parts.push(`${unload} unload→reload`);
  const spf = t.sec_per_frame;
  if (spf != null && Number.isFinite(spf) && spf > 0.05 && !bad) {
    parts.push(`${spf < 10 ? spf.toFixed(1) : Math.round(spf)}s/f`);
  }
  if (t.frames != null && Number.isFinite(t.frames)) parts.push(`${t.frames}f`);
  const text = parts.length ? parts.join(" · ") : String(t.label || "").trim();
  if (!text) return null;
  const titleBits = [
    t.exec_sec != null ? `exec ${formatDurationSec(t.exec_sec)}` : null,
    t.wait_sec != null ? `queue wait ${formatDurationSec(t.wait_sec)}` : null,
    t.load_sec != null ? `model load ${formatDurationSec(t.load_sec)}` : null,
    t.unload_to_reload_sec != null
      ? `unload→reload ${formatDurationSec(t.unload_to_reload_sec)}`
      : null,
    t.load_models?.length ? `models ${t.load_models.join(", ")}` : null,
    t.wall_sec != null ? `submit→done ${formatDurationSec(t.wall_sec)}` : null,
    t.terminal ? `terminal=${t.terminal}` : null,
    t.source ? `source=${t.source}` : null,
  ].filter(Boolean);
  return { text, title: titleBits.join(" · ") || text, bad };
}

function badgeClass(kind?: string | null): string {
  const k = (kind || "").toLowerCase();
  if (k === "derive" || k === "predicted_derive" || k === "predicted") return "work-product-badge--derive";
  if (k === "extend") return "work-product-badge--extend";
  if (k === "replay") return "work-product-badge--replay";
  if (k === "front" || k === "now") return "work-product-badge--front";
  if (k === "complete" || k === "deposited") return "work-product-badge--ok";
  if (k === "queued" || k === "pending" || k === "normal" || k === "later") return "work-product-badge--pending";
  if (k === "interrupted" || k === "unknown" || k === "abandoned") return "work-product-badge--pending";
  if (k === "failed" || k === "error") return "work-product-badge--bad";
  return "";
}

function sourcePreviewUrls(item: WorkProductItem): { thumb: string | null; video: string | null; label: string } {
  const source = item.bindings?.source_video || item.bindings?.source_image || item.bindings?.start_image;
  const thumb =
    source?.thumb_url ||
    item.parent_output_thumb_url ||
    (source?.url && /\.(png|jpe?g|webp)(\?|$)/i.test(source.url) ? source.url : null) ||
    null;
  const video = source?.url || item.parent_output_url || null;
  return {
    thumb,
    video,
    label: source?.basename || item.parent_output || "source",
  };
}

function WorkProductSourceThumbPreview({ item }: { item: WorkProductItem }) {
  const { thumb, video, label } = sourcePreviewUrls(item);
  const { label: kind, visual } = sourceThumbPreviewMeta(item);
  const badgeMod = `work-product-live__badge--${visual}`;
  const frameMod =
    visual === "error"
      ? " work-product-live__frame--error"
      : visual === "interrupted"
        ? " work-product-live__frame--interrupted"
        : visual === "muted"
          ? " work-product-live__frame--muted"
          : "";
  const imgMod =
    visual === "error"
      ? " work-product-live__img--error"
      : visual === "interrupted" || visual === "muted"
        ? " work-product-live__img--dim"
        : "";
  const emptyMod =
    visual === "error"
      ? " work-product-live__waiting--error"
      : visual === "interrupted"
        ? " work-product-live__waiting--interrupted"
        : visual === "muted"
          ? " work-product-live__waiting--muted"
          : "";
  const emptyTitle = kind.charAt(0).toUpperCase() + kind.slice(1);
  return (
    <div className="work-product-live">
      <div className={`work-product-live__frame work-product-live__frame--queued${frameMod}`}>
        {thumb ? (
          <img
            className={`work-product-live__img work-product-live__img--queued${imgMod}`}
            src={thumb}
            alt={label}
          />
        ) : video ? (
          <video
            className={`work-product-live__img work-product-live__img--queued${imgMod}`}
            src={video}
            muted
            playsInline
            preload="metadata"
          />
        ) : (
          <div
            className={`work-product-viewer__empty work-product-live__waiting work-product-live__waiting--queued${emptyMod}`}
          >
            <span className="work-product-live__queue-icon" aria-hidden>
              {visual === "error" ? "✕" : visual === "interrupted" ? "⊘" : "▣"}
            </span>
            <span>{emptyTitle} — source preview unavailable</span>
            {item.error ? (
              <span className="work-product-live__error-snip" title={item.error}>
                {String(item.error).trim()}
              </span>
            ) : null}
          </div>
        )}
        <span
          className={`work-product-live__badge ${badgeMod}`}
          title={item.error || item.prompt_id || item.job_key}
        >
          {kind}
        </span>
      </div>
    </div>
  );
}


type InputTrimState = {
  markIn: number | null;
  markOut: number | null;
  dirty: boolean;
  duration: number;
  fps: number;
  warning: string | null;
  clampedDefault: boolean;
};

function emptyTrimState(fps = 18): InputTrimState {
  return {
    markIn: null,
    markOut: null,
    dirty: false,
    duration: 0,
    fps,
    warning: null,
    clampedDefault: false,
  };
}

function trimOverridesFromState(
  state: InputTrimState,
  frameCountHint?: number | null,
  sourceClipId?: string | null,
): {
  overrides?: ShapeFactoryMapQueueOverrides;
  warning: string | null;
} {
  const clipId = String(sourceClipId || "").trim();
  const hasClip = Boolean(clipId);
  if (!state.dirty && !state.clampedDefault && !hasClip) {
    return { warning: state.warning };
  }
  const win = marksToVhsWindow(state.markIn, state.markOut, state.duration, state.fps, frameCountHint);
  const overrides: ShapeFactoryMapQueueOverrides = {};
  if (state.dirty || state.clampedDefault || hasClip) {
    overrides.parameters = {
      // Seconds are authoritative on the backend when present.
      ...(state.markIn != null ? { mark_in: state.markIn } : {}),
      ...(state.markOut != null ? { mark_out: state.markOut } : {}),
      skip_first_frames: win.skip_first_frames,
      frame_load_cap: win.frame_load_cap,
    };
  }
  if (hasClip) overrides.source_clip_id = clipId;
  return {
    overrides: Object.keys(overrides).length ? overrides : undefined,
    warning: win.warning || state.warning,
  };
}

function WorkProductViewer({
  item,
  outputTrim,
  sourceTrim,
  onOutputTrimChange,
  onSourceTrimChange,
  outputDefaults,
  sourceDefaults,
  selectedClipId,
  onSelectClip,
  onUseForExtend,
}: {
  item: WorkProductItem;
  outputTrim: InputTrimState;
  sourceTrim: InputTrimState;
  onOutputTrimChange: (next: InputTrimState) => void;
  onSourceTrimChange: (next: InputTrimState) => void;
  outputDefaults: { skip_first_frames: number; frame_load_cap: number };
  selectedClipId?: string | null;
  onSelectClip?: (clip: ShapeFactoryClip | null) => void;
  onUseForExtend?: (clip: ShapeFactoryClip) => void;
  sourceDefaults: { skip_first_frames: number; frame_load_cap: number };
}) {
  const videoUrl = item.output_url || null;
  const thumbUrl = item.output_thumb_url || null;
  const source = item.bindings?.source_video;
  const sourceUrl = source?.url || null;
  const sourceThumb = source?.thumb_url || null;
  const sourceRel = String(source?.relpath || "").trim() || null;
  const outputRel = String(item.output_relpath || "").trim() || null;
  const promptId = String(item.prompt_id || "").trim();
  const showRunningLive = isRunningLiveItem(item);
  const showSourceThumb = isSourceThumbPreviewItem(item);
  const previewUrls = sourcePreviewUrls(item);
  const queuedSourcePlayUrl =
    !videoUrl && !showRunningLive && previewUrls.video && /\.(mp4|webm)(\?|$)/i.test(previewUrls.video)
      ? previewUrls.video
      : null;
  const queuedSourceThumb = queuedSourcePlayUrl ? previewUrls.thumb : null;
  const queuedSourceRel =
    queuedSourcePlayUrl
      ? String(source?.relpath || item.parent_output_relpath || "").trim() || null
      : sourceRel;
  const queuedStatusMeta = queuedSourcePlayUrl ? sourceThumbPreviewMeta(item) : null;
  const outputVideoRef = useRef<HTMLVideoElement | null>(null);
  const sourceVideoRef = useRef<HTMLVideoElement | null>(null);
  const [outputTime, setOutputTime] = useState(0);
  const [sourceTime, setSourceTime] = useState(0);
  const [outputMode, setOutputMode] = useState<TrimPlaybackMode>("repeat");
  const [sourceMode, setSourceMode] = useState<TrimPlaybackMode>("repeat");
  const fps = parseFps(item.media_meta?.fps, 18);
  const construction = item.construction && typeof item.construction === "object" ? item.construction : {};
  const generationBands = originGenerationBands({
    duration: outputTrim.duration,
    fps,
    framesBefore: Number(construction.frames_before),
    generationFrames: Number(item.timing?.frames ?? construction.frames_after),
    outputFrameCount: Number(item.media_meta?.frame_count),
    overlapFrames: Number(item.timing?.overlap ?? construction.overlap),
  });
  const trimEditable = isJobTrimEditable(item);
  const pendingJobTrim = canUpdatePendingJobTrim(item);
  const pendingTrimTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pendingTrimTimerRef.current != null) {
        window.clearTimeout(pendingTrimTimerRef.current);
        pendingTrimTimerRef.current = null;
      }
    };
  }, []);

  const schedulePendingJobTrimUpdate = (state: InputTrimState, defaults: { skip_first_frames: number; frame_load_cap: number }) => {
    if (!pendingJobTrim) return;
    if (!(state.duration > 0)) return;
    const win =
      state.markIn == null && state.markOut == null && !state.dirty
        ? { skip_first_frames: defaults.skip_first_frames, frame_load_cap: defaults.frame_load_cap, warning: null as string | null }
        : marksToVhsWindow(state.markIn, state.markOut, state.duration, state.fps || fps, null);
    if (pendingTrimTimerRef.current != null) window.clearTimeout(pendingTrimTimerRef.current);
    pendingTrimTimerRef.current = window.setTimeout(() => {
      pendingTrimTimerRef.current = null;
      void updatePendingShapeFactoryTrim({
        job_key: String(item.job_key || "").trim() || undefined,
        job_path: String(item.job_path || "").trim() || undefined,
        skip_first_frames: win.skip_first_frames,
        frame_load_cap: win.frame_load_cap,
        mark_in: state.markIn,
        mark_out: state.markOut,
      }).catch((err) => {
        console.warn("update-pending-trim failed", err);
      });
    }, 450);
  };

  useTrimPlaybackEnforcement(outputVideoRef, {
    mediaKey: outputRel || item.job_key,
    markIn: outputTrim.markIn,
    markOut: outputTrim.markOut,
    mode: outputMode,
    enabled: Boolean(videoUrl),
  });
  useTrimPlaybackEnforcement(sourceVideoRef, {
    mediaKey: queuedSourceRel || `src:${item.job_key}`,
    markIn: sourceTrim.markIn,
    markOut: sourceTrim.markOut,
    mode: sourceMode,
    enabled: Boolean(sourceUrl || queuedSourcePlayUrl) && (!showSourceThumb || Boolean(queuedSourcePlayUrl)),
  });

  useEffect(() => {
    let cancelled = false;
    const boot = async (
      rel: string | null,
      defaults: { skip_first_frames: number; frame_load_cap: number },
      apply: (s: InputTrimState) => void,
      key: string,
      durationHint: number,
    ) => {
      let markIn: number | null = null;
      let markOut: number | null = null;
      let dirty = false;
      if (rel) {
        const saved = await loadDiscoveryTrimAsync(TRIM_CONTEXT_WORK_PRODUCTS, rel, key);
        if (saved) {
          markIn = saved.in;
          markOut = saved.out;
          dirty = true;
        }
      }
      if (cancelled) return;
      if (!dirty) {
        const seeded = vhsDefaultsToMarks(defaults, durationHint || 1, fps);
        markIn = durationHint > 0 ? seeded.markIn : null;
        markOut = durationHint > 0 ? seeded.markOut : null;
        apply({
          markIn,
          markOut,
          dirty: false,
          duration: durationHint,
          fps,
          warning: durationHint > 0 ? seeded.warning : null,
          clampedDefault: durationHint > 0 ? seeded.clamped : false,
        });
      } else {
        apply({
          markIn,
          markOut,
          dirty: true,
          duration: durationHint,
          fps,
          warning: null,
          clampedDefault: false,
        });
      }
    };
    // Output trim seeds from target-family defaults (next Extend input).
    void boot(outputRel, outputDefaults, onOutputTrimChange, `wp-out:${item.job_key}`, Number(item.media_meta?.duration) || 0);
    // Source trim seeds from what THIS job actually applied (else family defaults).
    void boot(
      queuedSourceRel || sourceRel,
      sourceDefaults,
      onSourceTrimChange,
      `wp-src:${item.job_key}`,
      0,
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    item.job_key,
    outputRel,
    sourceRel,
    queuedSourceRel,
    outputDefaults.skip_first_frames,
    outputDefaults.frame_load_cap,
    sourceDefaults.skip_first_frames,
    sourceDefaults.frame_load_cap,
    fps,
  ]);

  const onMeta = (
    el: HTMLVideoElement,
    defaults: { skip_first_frames: number; frame_load_cap: number },
    current: InputTrimState,
    apply: (s: InputTrimState) => void,
    rel: string | null,
    legacyKey: string,
  ) => {
    const duration = Number.isFinite(el.duration) && el.duration > 0 ? el.duration : current.duration;
    if (!(duration > 0)) return;
    if (current.dirty) {
      if (Math.abs(current.duration - duration) > 0.05) apply({ ...current, duration });
      return;
    }
    const seeded = vhsDefaultsToMarks(defaults, duration, fps);
    apply({
      markIn: seeded.markIn,
      markOut: seeded.markOut,
      dirty: false,
      duration,
      fps,
      warning: seeded.warning,
      clampedDefault: seeded.clamped,
    });
    void rel;
    void legacyKey;
  };

  const persistTrim = (
    next: InputTrimState,
    rel: string | null,
    legacyKey: string,
    opts?: {
      updatePendingJob?: boolean;
      defaults?: { skip_first_frames: number; frame_load_cap: number };
    },
  ) => {
    if (!(next.duration > 0)) return;
    if (next.dirty) {
      void persistDiscoveryTrimAsync({
        context: TRIM_CONTEXT_WORK_PRODUCTS,
        mediaRelpath: rel,
        legacyAssetKey: legacyKey,
        markIn: next.markIn,
        markOut: next.markOut,
        duration: next.duration,
      });
    }
    if (opts?.updatePendingJob && opts.defaults) schedulePendingJobTrimUpdate(next, opts.defaults);
  };

  return (
    <div className="work-product-viewer">
      <div className="work-product-viewer__main">
        {videoUrl ? (
          <>
            <video
              ref={outputVideoRef}
              className="work-product-viewer__video"
              src={videoUrl}
              poster={thumbUrl || undefined}
              controls
              playsInline
              preload="metadata"
              onTimeUpdate={(e) => setOutputTime(e.currentTarget.currentTime || 0)}
              onLoadedMetadata={(e) =>
                onMeta(e.currentTarget, outputDefaults, outputTrim, onOutputTrimChange, outputRel, `wp-out:${item.job_key}`)
              }
            />
            <VideoTrimControls
              className="work-product-viewer__trim"
              videoRef={outputVideoRef}
              duration={outputTrim.duration}
              currentTime={outputTime}
              markIn={outputTrim.markIn}
              markOut={outputTrim.markOut}
              mode={outputMode}
              mediaSyncKey={outputRel || item.job_key}
              size="default"
              seamMark={generationBands?.seamSec ?? null}
              blendEndMark={generationBands?.blendEndSec ?? null}
              onSeek={setOutputTime}
              onSyncTime={setOutputTime}
              onMarkInChange={(v) => {
                const next = { ...outputTrim, markIn: v, dirty: true, warning: null, clampedDefault: false };
                onOutputTrimChange(next);
                persistTrim(next, outputRel, `wp-out:${item.job_key}`);
              }}
              onMarkOutChange={(v) => {
                const next = { ...outputTrim, markOut: v, dirty: true, warning: null, clampedDefault: false };
                onOutputTrimChange(next);
                persistTrim(next, outputRel, `wp-out:${item.job_key}`);
              }}
              onModeChange={setOutputMode}
              onClear={() => {
                const seeded = vhsDefaultsToMarks(outputDefaults, outputTrim.duration || 1, fps);
                const next: InputTrimState = {
                  markIn: outputTrim.duration > 0 ? seeded.markIn : null,
                  markOut: outputTrim.duration > 0 ? seeded.markOut : null,
                  dirty: false,
                  duration: outputTrim.duration,
                  fps,
                  warning: outputTrim.duration > 0 ? seeded.warning : null,
                  clampedDefault: outputTrim.duration > 0 ? seeded.clamped : false,
                };
                onOutputTrimChange(next);
                void persistDiscoveryTrimAsync({
                  context: TRIM_CONTEXT_WORK_PRODUCTS,
                  mediaRelpath: outputRel,
                  legacyAssetKey: `wp-out:${item.job_key}`,
                  markIn: null,
                  markOut: null,
                  duration: outputTrim.duration || 1,
                });
              }}
            />
            {outputTrim.warning ? (
              <p className="work-product-viewer__trim-warn" title={outputTrim.warning}>
                {outputTrim.warning}
              </p>
            ) : null}
          </>
        ) : showRunningLive ? (
          <ComfyLivePreview
            promptId={promptId}
            submittedAt={item.submitted_at || item.created_at}
            showMetrics={false}
          />
        ) : queuedSourcePlayUrl ? (
          <div className="work-product-viewer__queued-source">
            <div className="work-product-viewer__queued-source-frame">
              <video
                ref={sourceVideoRef}
                className="work-product-viewer__video"
                src={queuedSourcePlayUrl}
                poster={queuedSourceThumb || undefined}
                controls
                playsInline
                muted
                preload="metadata"
                onTimeUpdate={(e) => setSourceTime(e.currentTarget.currentTime || 0)}
                onLoadedMetadata={(e) =>
                  onMeta(
                    e.currentTarget,
                    sourceDefaults,
                    sourceTrim,
                    onSourceTrimChange,
                    queuedSourceRel,
                    `wp-src:${item.job_key}`,
                  )
                }
              />
              {queuedStatusMeta ? (
                <span
                  className={`work-product-live__badge work-product-live__badge--${queuedStatusMeta.visual}`}
                  title={item.error || item.prompt_id || item.job_key}
                >
                  {queuedStatusMeta.label}
                </span>
              ) : null}
            </div>
            <VideoTrimControls
              className="work-product-viewer__trim"
              videoRef={sourceVideoRef}
              duration={sourceTrim.duration}
              currentTime={sourceTime}
              markIn={sourceTrim.markIn}
              markOut={sourceTrim.markOut}
              mode={sourceMode}
              mediaSyncKey={queuedSourceRel || `src:${item.job_key}`}
              size="default"
              readOnly={!trimEditable}
              onSeek={setSourceTime}
              onSyncTime={setSourceTime}
              onMarkInChange={(v) => {
                if (!trimEditable) return;
                const next = { ...sourceTrim, markIn: v, dirty: true, warning: null, clampedDefault: false };
                onSourceTrimChange(next);
                persistTrim(next, queuedSourceRel, `wp-src:${item.job_key}`, {
                  updatePendingJob: true,
                  defaults: sourceDefaults,
                });
              }}
              onMarkOutChange={(v) => {
                if (!trimEditable) return;
                const next = { ...sourceTrim, markOut: v, dirty: true, warning: null, clampedDefault: false };
                onSourceTrimChange(next);
                persistTrim(next, queuedSourceRel, `wp-src:${item.job_key}`, {
                  updatePendingJob: true,
                  defaults: sourceDefaults,
                });
              }}
              onModeChange={setSourceMode}
              onClear={() => {
                if (!trimEditable) return;
                const seeded = vhsDefaultsToMarks(sourceDefaults, sourceTrim.duration || 1, fps);
                const next: InputTrimState = {
                  markIn: sourceTrim.duration > 0 ? seeded.markIn : null,
                  markOut: sourceTrim.duration > 0 ? seeded.markOut : null,
                  dirty: false,
                  duration: sourceTrim.duration,
                  fps,
                  warning: sourceTrim.duration > 0 ? seeded.warning : null,
                  clampedDefault: sourceTrim.duration > 0 ? seeded.clamped : false,
                };
                onSourceTrimChange(next);
                void persistDiscoveryTrimAsync({
                  context: TRIM_CONTEXT_WORK_PRODUCTS,
                  mediaRelpath: queuedSourceRel,
                  legacyAssetKey: `wp-src:${item.job_key}`,
                  markIn: null,
                  markOut: null,
                  duration: sourceTrim.duration || 1,
                });
                schedulePendingJobTrimUpdate(next, sourceDefaults);
              }}
            />
            {sourceTrim.warning ? (
              <p className="work-product-viewer__trim-warn" title={sourceTrim.warning}>
                {sourceTrim.warning}
              </p>
            ) : null}
            <ClipBookmarksRail
              mediaRelpath={queuedSourceRel}
              duration={sourceTrim.duration}
              markIn={sourceTrim.markIn}
              markOut={sourceTrim.markOut}
              trimEditable={trimEditable}
              origin="workbench"
              selectedClipId={selectedClipId}
              onSelectClip={onSelectClip}
              onUseForExtend={onUseForExtend}
              onApplyClip={(mi, mo) => {
                if (!trimEditable) return;
                const next = {
                  ...sourceTrim,
                  markIn: mi,
                  markOut: mo,
                  dirty: true,
                  warning: null,
                  clampedDefault: false,
                };
                onSourceTrimChange(next);
                persistTrim(next, queuedSourceRel, `wp-src:${item.job_key}`, {
                  updatePendingJob: true,
                  defaults: sourceDefaults,
                });
              }}
            />
          </div>
        ) : showSourceThumb ? (
          <WorkProductSourceThumbPreview item={item} />
        ) : thumbUrl ? (
          <img className="work-product-viewer__img" src={thumbUrl} alt={item.job_key} />
        ) : (
          <div className="work-product-viewer__empty">No output yet ({item.status || "pending"})</div>
        )}
      </div>
      {(sourceUrl || sourceThumb) && !showSourceThumb && !queuedSourcePlayUrl && (
        <div className="work-product-viewer__source" title={source?.basename || "source"}>
          {sourceUrl ? (
            <>
              <video
                ref={sourceVideoRef}
                className="work-product-viewer__source-video"
                src={sourceUrl}
                poster={sourceThumb || undefined}
                controls
                playsInline
                muted
                preload="metadata"
                onTimeUpdate={(e) => setSourceTime(e.currentTarget.currentTime || 0)}
                onLoadedMetadata={(e) =>
                  onMeta(e.currentTarget, sourceDefaults, sourceTrim, onSourceTrimChange, sourceRel, `wp-src:${item.job_key}`)
                }
              />
              <VideoTrimControls
                className="work-product-viewer__trim"
                videoRef={sourceVideoRef}
                duration={sourceTrim.duration}
                currentTime={sourceTime}
                markIn={sourceTrim.markIn}
                markOut={sourceTrim.markOut}
                mode={sourceMode}
                mediaSyncKey={sourceRel || `src:${item.job_key}`}
                size="default"
                readOnly={!trimEditable}
                onSeek={setSourceTime}
                onSyncTime={setSourceTime}
                onMarkInChange={(v) => {
                  if (!trimEditable) return;
                  const next = { ...sourceTrim, markIn: v, dirty: true, warning: null, clampedDefault: false };
                  onSourceTrimChange(next);
                  persistTrim(next, sourceRel, `wp-src:${item.job_key}`, {
                    updatePendingJob: true,
                    defaults: sourceDefaults,
                  });
                }}
                onMarkOutChange={(v) => {
                  if (!trimEditable) return;
                  const next = { ...sourceTrim, markOut: v, dirty: true, warning: null, clampedDefault: false };
                  onSourceTrimChange(next);
                  persistTrim(next, sourceRel, `wp-src:${item.job_key}`, {
                    updatePendingJob: true,
                    defaults: sourceDefaults,
                  });
                }}
                onModeChange={setSourceMode}
                onClear={() => {
                  if (!trimEditable) return;
                  const seeded = vhsDefaultsToMarks(sourceDefaults, sourceTrim.duration || 1, fps);
                  const next: InputTrimState = {
                    markIn: sourceTrim.duration > 0 ? seeded.markIn : null,
                    markOut: sourceTrim.duration > 0 ? seeded.markOut : null,
                    dirty: false,
                    duration: sourceTrim.duration,
                    fps,
                    warning: sourceTrim.duration > 0 ? seeded.warning : null,
                    clampedDefault: sourceTrim.duration > 0 ? seeded.clamped : false,
                  };
                  onSourceTrimChange(next);
                  void persistDiscoveryTrimAsync({
                    context: TRIM_CONTEXT_WORK_PRODUCTS,
                    mediaRelpath: sourceRel,
                    legacyAssetKey: `wp-src:${item.job_key}`,
                    markIn: null,
                    markOut: null,
                    duration: sourceTrim.duration || 1,
                  });
                  schedulePendingJobTrimUpdate(next, sourceDefaults);
                }}
              />
              {sourceTrim.warning ? (
                <p className="work-product-viewer__trim-warn" title={sourceTrim.warning}>
                  {sourceTrim.warning}
                </p>
              ) : null}
              <ClipBookmarksRail
                mediaRelpath={sourceRel}
                duration={sourceTrim.duration}
                markIn={sourceTrim.markIn}
                markOut={sourceTrim.markOut}
                trimEditable={trimEditable}
                origin="workbench"
                selectedClipId={selectedClipId}
                onSelectClip={onSelectClip}
                onUseForExtend={onUseForExtend}
                onApplyClip={(mi, mo) => {
                  if (!trimEditable) return;
                  const next = {
                    ...sourceTrim,
                    markIn: mi,
                    markOut: mo,
                    dirty: true,
                    warning: null,
                    clampedDefault: false,
                  };
                  onSourceTrimChange(next);
                  persistTrim(next, sourceRel, `wp-src:${item.job_key}`, {
                    updatePendingJob: true,
                    defaults: sourceDefaults,
                  });
                }}
              />
            </>
          ) : sourceThumb ? (
            <img className="work-product-viewer__source-img" src={sourceThumb} alt={source?.basename || "source"} />
          ) : null}
        </div>
      )}
    </div>
  );
}

type PeekPos = { top: number; left: number; maxHeight: number };

function JsonPeekButton({ path, label }: { path: string; label: string }) {
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const hoverTimer = useRef<number | null>(null);
  const leaveTimer = useRef<number | null>(null);
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [pos, setPos] = useState<PeekPos>({ top: 0, left: 0, maxHeight: 360 });
  const [loading, setLoading] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ basename?: string; truncated?: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const clearTimers = () => {
    if (hoverTimer.current != null) window.clearTimeout(hoverTimer.current);
    if (leaveTimer.current != null) window.clearTimeout(leaveTimer.current);
    hoverTimer.current = null;
    leaveTimer.current = null;
  };

  const place = () => {
    const btn = btnRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const pad = 8;
    const width = Math.min(520, window.innerWidth - pad * 2);
    let left = r.left;
    if (left + width > window.innerWidth - pad) left = Math.max(pad, window.innerWidth - pad - width);
    const spaceBelow = window.innerHeight - r.bottom - pad;
    const spaceAbove = r.top - pad;
    const preferBelow = spaceBelow >= 180 || spaceBelow >= spaceAbove;
    const maxHeight = Math.max(160, Math.min(480, preferBelow ? spaceBelow : spaceAbove));
    const top = preferBelow ? r.bottom + 6 : Math.max(pad, r.top - 6 - maxHeight);
    setPos({ top, left, maxHeight });
  };

  const load = async () => {
    const cached = JSON_CACHE.get(path);
    if (cached) {
      setText(cached.text);
      setMeta({ basename: cached.basename, truncated: cached.truncated });
      setError(cached.error || null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetchShapeFactoryJsonPeek(path);
      const body = res.text || "";
      JSON_CACHE.set(path, { text: body, basename: res.basename, truncated: res.truncated });
      setText(body);
      setMeta({ basename: res.basename, truncated: res.truncated });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      JSON_CACHE.set(path, { text: "", error: msg });
      setError(msg);
      setText(null);
    } finally {
      setLoading(false);
    }
  };

  const openPeek = (pin: boolean) => {
    clearTimers();
    setPinned(pin);
    setOpen(true);
    place();
    void load();
  };

  const closePeek = () => {
    clearTimers();
    setPinned(false);
    setOpen(false);
  };

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const onScroll = () => {
      if (!pinned) closePeek();
      else place();
    };
    const onResize = () => place();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open, pinned]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePeek();
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (btnRef.current?.contains(t)) return;
      if (popRef.current?.contains(t)) return;
      closePeek();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  useEffect(() => () => clearTimers(), []);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="work-product-json-link"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title={`${path}\nHover to peek · click to pin`}
        onMouseEnter={() => {
          clearTimers();
          hoverTimer.current = window.setTimeout(() => openPeek(false), 180);
        }}
        onMouseLeave={() => {
          clearTimers();
          if (!pinned) {
            leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
          }
        }}
        onFocus={() => openPeek(false)}
        onClick={(e) => {
          e.preventDefault();
          if (open && pinned) closePeek();
          else openPeek(true);
        }}
      >
        {label}
        <span className="work-product-json-link__tag">json</span>
      </button>
      {open
        ? createPortal(
            <div
              ref={popRef}
              id={panelId}
              role="dialog"
              aria-label={`JSON: ${meta?.basename || label}`}
              className={`work-product-json-pop${pinned ? " work-product-json-pop--pinned" : ""}`}
              style={{ top: pos.top, left: pos.left, maxHeight: pos.maxHeight }}
              onMouseEnter={() => clearTimers()}
              onMouseLeave={() => {
                if (!pinned) {
                  leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
                }
              }}
            >
              <div className="work-product-json-pop__head">
                <strong className="work-product-json-pop__title">{meta?.basename || label}</strong>
                <div className="work-product-json-pop__actions">
                  {meta?.truncated ? <span className="work-product-json-pop__note">truncated</span> : null}
                  {pinned ? <span className="work-product-json-pop__note">pinned</span> : null}
                  <button type="button" className="work-product-json-pop__close" onClick={closePeek} aria-label="Close">
                    ×
                  </button>
                </div>
              </div>
              <div className="work-product-json-pop__path" title={path}>
                {path}
              </div>
              <pre className="work-product-json-pop__body">
                {loading ? "Loading…" : error ? error : text || "(empty)"}
              </pre>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

function PromptMarkupTable({
  title,
  rows,
}: {
  title: string;
  rows: WorkProductPromptRow[];
}) {
  if (!rows.length) {
    return (
      <div className="work-product-prompt-table-wrap">
        <div className="work-product-prompt-table__title">{title}</div>
        <div className="work-product-prompt-table__empty">—</div>
      </div>
    );
  }
  return (
    <div className="work-product-prompt-table-wrap">
      <div className="work-product-prompt-table__title">{title}</div>
      <table className="work-product-prompt-table">
        <thead>
          <tr>
            <th scope="col" className="work-product-prompt-table__w">
              Weight
            </th>
            <th scope="col">Text</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const w = Number(row.weight);
            const emphasis = !Number.isFinite(w) ? 0 : Math.max(0, Math.min(1, (w - 1) / 1.2));
            return (
              <tr key={`${i}:${row.text.slice(0, 24)}`} title={row.raw || undefined}>
                <td className="work-product-prompt-table__w">
                  <span
                    className="work-product-prompt-weight"
                    style={{ ["--wp-emphasis" as string]: String(emphasis) }}
                  >
                    {Number.isFinite(w) ? (Math.round(w * 100) / 100).toString() : "—"}
                  </span>
                </td>
                <td className="work-product-prompt-table__text">{row.text}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PromptPeekButton({ prompt, label }: { prompt: WorkProductPromptProfile; label: string }) {
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const hoverTimer = useRef<number | null>(null);
  const leaveTimer = useRef<number | null>(null);
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [pos, setPos] = useState<PeekPos>({ top: 0, left: 0, maxHeight: 360 });

  const clearTimers = () => {
    if (hoverTimer.current != null) window.clearTimeout(hoverTimer.current);
    if (leaveTimer.current != null) window.clearTimeout(leaveTimer.current);
    hoverTimer.current = null;
    leaveTimer.current = null;
  };

  const place = () => {
    const btn = btnRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const pad = 8;
    const width = Math.min(560, window.innerWidth - pad * 2);
    let left = r.left;
    if (left + width > window.innerWidth - pad) left = Math.max(pad, window.innerWidth - pad - width);
    const spaceBelow = window.innerHeight - r.bottom - pad;
    const spaceAbove = r.top - pad;
    const preferBelow = spaceBelow >= 180 || spaceBelow >= spaceAbove;
    const maxHeight = Math.max(160, Math.min(520, preferBelow ? spaceBelow : spaceAbove));
    const top = preferBelow ? r.bottom + 6 : Math.max(pad, r.top - 6 - maxHeight);
    setPos({ top, left, maxHeight });
  };

  const openPeek = (pin: boolean) => {
    clearTimers();
    setPinned(pin);
    setOpen(true);
    place();
  };

  const closePeek = () => {
    clearTimers();
    setPinned(false);
    setOpen(false);
  };

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const onScroll = () => {
      if (!pinned) closePeek();
      else place();
    };
    const onResize = () => place();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open, pinned]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePeek();
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (btnRef.current?.contains(t)) return;
      if (popRef.current?.contains(t)) return;
      closePeek();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  useEffect(() => () => clearTimers(), []);

  const title = prompt.label || prompt.basename || label;
  const posRows = prompt.positive_rows || [];
  const negRows = prompt.negative_rows || [];

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="work-product-json-link"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title={`${prompt.path || label}\nHover to peek decoded prompt · click to pin`}
        onMouseEnter={() => {
          clearTimers();
          hoverTimer.current = window.setTimeout(() => openPeek(false), 180);
        }}
        onMouseLeave={() => {
          clearTimers();
          if (!pinned) {
            leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
          }
        }}
        onFocus={() => openPeek(false)}
        onClick={(e) => {
          e.preventDefault();
          if (open && pinned) closePeek();
          else openPeek(true);
        }}
      >
        {label}
        <span className="work-product-json-link__tag">prompt</span>
      </button>
      {open
        ? createPortal(
            <div
              ref={popRef}
              id={panelId}
              role="dialog"
              aria-label={`Prompt: ${title}`}
              className={`work-product-json-pop work-product-json-pop--prompt${pinned ? " work-product-json-pop--pinned" : ""}`}
              style={{ top: pos.top, left: pos.left, maxHeight: pos.maxHeight, width: Math.min(560, window.innerWidth - 16) }}
              onMouseEnter={() => clearTimers()}
              onMouseLeave={() => {
                if (!pinned) {
                  leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
                }
              }}
            >
              <div className="work-product-json-pop__head">
                <strong className="work-product-json-pop__title">{title}</strong>
                <div className="work-product-json-pop__actions">
                  {pinned ? <span className="work-product-json-pop__note">pinned</span> : null}
                  {prompt.path ? <JsonPeekButton path={prompt.path} label="raw json" /> : null}
                  <button type="button" className="work-product-json-pop__close" onClick={closePeek} aria-label="Close">
                    ×
                  </button>
                </div>
              </div>
              {prompt.path ? (
                <div className="work-product-json-pop__path" title={prompt.path}>
                  {prompt.path}
                </div>
              ) : null}
              <div className="work-product-json-pop__body work-product-json-pop__body--prompt">
                {prompt.missing ? (
                  <div className="work-product-prompt-table__empty">Prompt file missing</div>
                ) : prompt.error ? (
                  <div className="work-product-prompt-table__empty">{prompt.error}</div>
                ) : (
                  <>
                    <PromptMarkupTable title="Positive" rows={posRows} />
                    {negRows.length > 0 || (prompt.negative && prompt.negative.trim()) ? (
                      <PromptMarkupTable title="Negative" rows={negRows} />
                    ) : null}
                  </>
                )}
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

function ShapeSlotTable({
  title,
  rows,
}: {
  title: string;
  rows: WorkProductShapeSlot[];
}) {
  if (!rows.length) {
    return (
      <div className="work-product-prompt-table-wrap">
        <div className="work-product-prompt-table__title">{title}</div>
        <div className="work-product-prompt-table__empty">—</div>
      </div>
    );
  }
  return (
    <div className="work-product-prompt-table-wrap">
      <div className="work-product-prompt-table__title">{title}</div>
      <table className="work-product-prompt-table work-product-shape-table">
        <thead>
          <tr>
            <th scope="col">Slot</th>
            <th
              scope="col"
              title={Object.entries(SHAPE_ROLE_HELP)
                .map(([letter, help]) => `${letter}: ${help}`)
                .join("\n")}
            >
              Role
            </th>
            <th scope="col">Media</th>
            <th scope="col">Binding</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const role = String(row.role || "").toUpperCase();
            const gloss = row.role_gloss || SHAPE_ROLE_HELP[role];
            const binding = [row.binding_type, row.node_id != null ? `node ${row.node_id}` : ""]
              .filter(Boolean)
              .join(" · ");
            return (
              <tr key={`${title}:${row.slot || i}`}>
                <td>
                  <code>{row.slot || "—"}</code>
                </td>
                <td>
                  {role ? (
                    <abbr className="work-product-role" title={gloss ? `${role}: ${gloss}` : role}>
                      {role}
                    </abbr>
                  ) : (
                    "—"
                  )}
                </td>
                <td>{row.media || "—"}</td>
                <td className="work-product-shape-table__binding">{binding || "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ShapePeekButton({ shape, label }: { shape: WorkProductShapeProfile; label: string }) {
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const hoverTimer = useRef<number | null>(null);
  const leaveTimer = useRef<number | null>(null);
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const [pos, setPos] = useState<PeekPos>({ top: 0, left: 0, maxHeight: 360 });

  const clearTimers = () => {
    if (hoverTimer.current != null) window.clearTimeout(hoverTimer.current);
    if (leaveTimer.current != null) window.clearTimeout(leaveTimer.current);
    hoverTimer.current = null;
    leaveTimer.current = null;
  };

  const place = () => {
    const btn = btnRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const pad = 8;
    const width = Math.min(640, window.innerWidth - pad * 2);
    let left = r.left;
    if (left + width > window.innerWidth - pad) left = Math.max(pad, window.innerWidth - pad - width);
    const spaceBelow = window.innerHeight - r.bottom - pad;
    const spaceAbove = r.top - pad;
    const preferBelow = spaceBelow >= 180 || spaceBelow >= spaceAbove;
    const maxHeight = Math.max(160, Math.min(560, preferBelow ? spaceBelow : spaceAbove));
    const top = preferBelow ? r.bottom + 6 : Math.max(pad, r.top - 6 - maxHeight);
    setPos({ top, left, maxHeight });
  };

  const openPeek = (pin: boolean) => {
    clearTimers();
    setPinned(pin);
    setOpen(true);
    place();
  };

  const closePeek = () => {
    clearTimers();
    setPinned(false);
    setShowRaw(false);
    setOpen(false);
  };

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const onScroll = () => {
      if (!pinned) closePeek();
      else place();
    };
    const onResize = () => place();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open, pinned]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePeek();
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (btnRef.current?.contains(t)) return;
      if (popRef.current?.contains(t)) return;
      closePeek();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  useEffect(() => () => clearTimers(), []);

  const title = shape.shape_id || shape.basename || label;
  const metaBits = [
    shape.family_slug ? `family ${shape.family_slug}` : "",
    shape.graph_hash ? `graph ${String(shape.graph_hash).slice(0, 12)}…` : "",
    shape.output_prefix_root ? `prefix ${shape.output_prefix_root}` : "",
  ].filter(Boolean);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="work-product-json-link"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title={`${shape.path || label}\nHover to peek shape contract · click to pin`}
        onMouseEnter={() => {
          clearTimers();
          hoverTimer.current = window.setTimeout(() => openPeek(false), 180);
        }}
        onMouseLeave={() => {
          clearTimers();
          if (!pinned) {
            leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
          }
        }}
        onFocus={() => openPeek(false)}
        onClick={(e) => {
          e.preventDefault();
          if (open && pinned) closePeek();
          else openPeek(true);
        }}
      >
        {label}
        <span className="work-product-json-link__tag">shape</span>
      </button>
      {open
        ? createPortal(
            <div
              ref={popRef}
              id={panelId}
              role="dialog"
              aria-label={`Shape: ${title}`}
              className={`work-product-json-pop work-product-json-pop--shape${pinned ? " work-product-json-pop--pinned" : ""}`}
              style={{ top: pos.top, left: pos.left, maxHeight: pos.maxHeight, width: Math.min(640, window.innerWidth - 16) }}
              onMouseEnter={() => clearTimers()}
              onMouseLeave={() => {
                if (!pinned) {
                  leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
                }
              }}
            >
              <div className="work-product-json-pop__head">
                <strong className="work-product-json-pop__title">{title}</strong>
                <div className="work-product-json-pop__actions">
                  {pinned ? <span className="work-product-json-pop__note">pinned</span> : null}
                  {shape.text ? (
                    <button
                      type="button"
                      className="work-product-json-link"
                      onClick={() => setShowRaw((v) => !v)}
                    >
                      {showRaw ? "contract" : "raw yaml"}
                    </button>
                  ) : null}
                  <button type="button" className="work-product-json-pop__close" onClick={closePeek} aria-label="Close">
                    ×
                  </button>
                </div>
              </div>
              {shape.path ? (
                <div className="work-product-json-pop__path" title={shape.path}>
                  {shape.path}
                </div>
              ) : null}
              <div className="work-product-json-pop__body work-product-json-pop__body--shape">
                {shape.missing ? (
                  <div className="work-product-prompt-table__empty">Shape file missing</div>
                ) : shape.error ? (
                  <div className="work-product-prompt-table__empty">{shape.error}</div>
                ) : showRaw ? (
                  <pre className="work-product-shape-raw">{shape.text || "(empty)"}</pre>
                ) : (
                  <>
                    {metaBits.length ? (
                      <div className="work-product-shape-meta">{metaBits.join(" · ")}</div>
                    ) : null}
                    {shape.template_basename || shape.template ? (
                      <div className="work-product-shape-meta" title={shape.template || undefined}>
                        template {shape.template_basename || shape.template}
                      </div>
                    ) : null}
                    <ShapeSlotTable title="Requires" rows={shape.requires || []} />
                    <ShapeSlotTable title="Produces" rows={shape.produces || []} />
                    {(shape.deposits || []).length > 0 ? (
                      <div className="work-product-prompt-table-wrap">
                        <div className="work-product-prompt-table__title">Deposits</div>
                        <table className="work-product-prompt-table work-product-shape-table">
                          <thead>
                            <tr>
                              <th scope="col">Slot</th>
                              <th scope="col">Pool</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(shape.deposits || []).map((d, i) => (
                              <tr key={`dep:${d.slot || i}`}>
                                <td>
                                  <code>{d.slot || "—"}</code>
                                </td>
                                <td>
                                  <code>{d.to_pool || "—"}</code>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : null}
                  </>
                )}
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

function bindingAssetHref(row: WorkProductDetailRow): string | null {
  const rel = String(row.relpath || "").trim().replace(/^\/+/, "").replace(/\\/g, "/");
  const asset = String(row.asset_url || "").trim();
  if (rel) {
    // Indexed outputs → Discovery; input stills / other files → /files/.
    if (/^(og|wip|output)\//i.test(rel) || /\.mp4($|\?)/i.test(rel)) {
      return discoveryLibraryHref(rel);
    }
    return "/files/" + encodeURIComponent(rel);
  }
  return asset || null;
}

function BindingDetailValue({ row }: { row: WorkProductDetailRow }) {
  const thumb = String(row.thumb_url || "").trim() || null;
  const href = bindingAssetHref(row);
  const label = enrichRoleMentions(row.value);
  const openLabel = href?.startsWith("/discovery") ? "Open in Discovery" : "Open asset";
  return (
    <div className="work-product-binding-media">
      {thumb ? (
        href ? (
          <a className="work-product-binding-media__thumb" href={href} title={openLabel}>
            <img src={thumb} alt="" loading="lazy" />
          </a>
        ) : (
          <span className="work-product-binding-media__thumb">
            <img src={thumb} alt="" loading="lazy" />
          </span>
        )
      ) : null}
      <div className="work-product-binding-media__meta">
        <div className="work-product-binding-media__value" title={row.value}>
          {label}
        </div>
        {href ? (
          <a className="work-product-binding-media__link" href={href}>
            {row.relpath || openLabel}
          </a>
        ) : row.relpath ? (
          <span className="work-product-binding-media__link work-product-binding-media__link--muted">
            {row.relpath}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** Merge media fields from item.bindings when detail rows lack thumb/url (stale API process). */
function enrichBindingDetailRow(
  row: WorkProductDetailRow,
  bindings?: Record<string, WorkProductBinding> | null,
): WorkProductDetailRow {
  if (!row.label.startsWith("Binding · ")) return row;
  const slot = row.label.slice("Binding · ".length).trim();
  const meta = bindings?.[slot];
  if (!meta) return row;
  return {
    ...row,
    thumb_url: row.thumb_url || meta.thumb_url || null,
    asset_url: row.asset_url || meta.url || null,
    relpath: row.relpath || meta.relpath || null,
  };
}

function DetailValue({
  row,
  prompt,
  shape,
  bindings,
}: {
  row: WorkProductDetailRow;
  prompt?: WorkProductPromptProfile | null;
  shape?: WorkProductShapeProfile | null;
  bindings?: Record<string, WorkProductBinding> | null;
}) {
  const bindingRow = row.label.startsWith("Binding · ") ? enrichBindingDetailRow(row, bindings) : row;
  if (
    bindingRow.label.startsWith("Binding · ") &&
    (bindingRow.thumb_url || bindingRow.asset_url || bindingRow.relpath)
  ) {
    return <BindingDetailValue row={bindingRow} />;
  }
  if (row.peek === "shape" && shape && !shape.missing) {
    return <ShapePeekButton shape={shape} label={row.value} />;
  }
  if ((row.label === "Prompt profile" || row.label === "Prompt file" || row.peek === "prompt") && prompt) {
    return <PromptPeekButton prompt={prompt} label={row.value} />;
  }
  if (row.json_path) {
    // Binding rows may include role=… — keep letter, explain via tooltip.
    const hasRole = /role=[A-Za-z]/.test(row.value);
    if (hasRole) {
      return (
        <span className="work-product-detail-with-json">
          <span>{enrichRoleMentions(row.value)}</span>
          <JsonPeekButton path={row.json_path} label="view json" />
        </span>
      );
    }
    return <JsonPeekButton path={row.json_path} label={row.value} />;
  }
  return <span title={row.value}>{enrichRoleMentions(row.value)}</span>;
}

function enrichRoleMentions(text: string): React.ReactNode {
  // Highlight role=A|B|C|X; gloss lives in the tooltip, not inline.
  const re = /role=([A-Za-z])(?:\s*\([^)]*\))?/g;
  const parts: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const letter = m[1].toUpperCase();
    const help = SHAPE_ROLE_HELP[letter];
    parts.push(
      <abbr key={`role-${i++}`} className="work-product-role" title={help ? `${letter}: ${help}` : `role=${letter}`}>
        {`role=${letter}`}
      </abbr>,
    );
    last = m.index + m[0].length;
  }
  if (last === 0) return text;
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function openPoolItem(items: WorkItem[] | undefined, pool: string): WorkItem | undefined {
  return (items || []).find((w) => w.pool === pool && (w.status === "draft" || w.status === "queued" || w.status === "running"));
}

/** Labels already shown as chips — omit from the property grid. */
const DETAIL_CHIP_LABELS = new Set([
  "Family",
  "Status",
  "Pick mode",
  "Step",
  "Rating kind",
  "Disposition",
]);

type DetailGroupDef = {
  id: string;
  title: string;
  /** Prefer exact labels in this order; unmatched go to later groups / Other. */
  labels?: string[];
  match?: (label: string) => boolean;
  /** Full-width rows (bindings, long notes). */
  wide?: boolean;
  /** Stacked label-above-value layout (better for long IDs/paths). */
  kv?: boolean;
  /** Small scalar fields rendered in a nested metrics box. */
  compact?: { title?: string; labels: string[] };
};

const DETAIL_GROUPS: DetailGroupDef[] = [
  {
    id: "prompt",
    title: "Positive Prompt",
    kv: true,
    labels: [
      "Prompt name",
      "Prompt profile",
      // Legacy labels (older API).
      "Prompt label",
      "Prompt file",
    ],
  },
  {
    id: "run",
    title: "Run",
    kv: true,
    labels: [
      "Created",
      "Job key",
      "Job file",
      "Comfy prompt ID",
      "Comfy submit JSON",
      "Output prefix",
      // Legacy labels (older API).
      "Prompt id",
      "Submit prompt JSON",
    ],
  },
  {
    id: "timing",
    title: "Timing",
    kv: true,
    labels: [
      "Exec",
      "Exec sec",
      "Queue wait sec",
      "Wall sec",
      "Model load sec",
      "Unload→reload sec",
      "Models loaded",
      "Model load count",
      "Unload events",
      "Sec per frame",
      "Workload frames",
      "Exec terminal",
      "Frames before→after",
    ],
  },
  {
    id: "plan",
    title: "Selection",
    kv: true,
    labels: [
      "Derive action",
      "Plan source tag",
      "Combo key",
      "Upgraded from",
      "Parent output",
      "Disposition note",
    ],
    compact: {
      labels: [
        "Cursor",
        "Fast track",
        "Selection weight",
        "Recipe pool size",
        "Seed count",
        "Derive attempts",
        "Used recent fallback",
      ],
    },
  },
  {
    id: "appetite",
    title: "Appetite & hold",
    labels: [
      "Appetite",
      "Appetite facet",
      "Appetite value",
      "Appetite evidence",
      "Tag affinity",
      "Hold axis",
      "Hold values",
      "Hold candidates",
      "Hold facet constrained",
      "Hold fallback",
    ],
    match: (label) =>
      label.startsWith("Appetite") || label.startsWith("Hold ") || label === "Tag affinity",
  },
  {
    id: "shape",
    title: "Shape & template",
    kv: true,
    labels: ["Template", "Generated workflow", "Shape path"],
    compact: {
      labels: ["Shape", "Graph hash"],
    },
  },
  {
    id: "bindings",
    title: "Bindings",
    match: (label) => label.startsWith("Binding · "),
    wide: true,
  },
  {
    id: "trim",
    title: "Trim",
    kv: true,
    labels: ["VHS skip_first_frames", "VHS frame_load_cap"],
    match: (label) =>
      label.startsWith("VHS ") ||
      label.toLowerCase().startsWith("trim ") ||
      label === "skip_first_frames" ||
      label === "frame_load_cap",
  },
];

function groupDetailRows(rows: WorkProductDetailRow[]): Array<{
  id: string;
  title: string;
  wide?: boolean;
  kv?: boolean;
  rows: WorkProductDetailRow[];
  compact?: { title?: string; rows: WorkProductDetailRow[] };
}> {
  const remaining = rows.filter((r) => !DETAIL_CHIP_LABELS.has(r.label));
  const used = new Set<WorkProductDetailRow>();
  const groups: Array<{
    id: string;
    title: string;
    wide?: boolean;
    kv?: boolean;
    rows: WorkProductDetailRow[];
    compact?: { title?: string; rows: WorkProductDetailRow[] };
  }> = [];

  const takeExact = (labels: string[]): WorkProductDetailRow[] => {
    const picked: WorkProductDetailRow[] = [];
    for (const label of labels) {
      const row = remaining.find((r) => r.label === label && !used.has(r));
      if (row) {
        picked.push(row);
        used.add(row);
      }
    }
    return picked;
  };

  for (const def of DETAIL_GROUPS) {
    const picked = def.labels ? takeExact(def.labels) : [];
    if (def.match) {
      for (const row of remaining) {
        if (used.has(row)) continue;
        if (def.match(row.label)) {
          picked.push(row);
          used.add(row);
        }
      }
    }
    const compactRows = def.compact ? takeExact(def.compact.labels) : [];
    if (!picked.length && !compactRows.length) continue;
    groups.push({
      id: def.id,
      title: def.title,
      wide: def.wide,
      kv: def.kv,
      rows: picked,
      compact: compactRows.length
        ? { title: def.compact?.title, rows: compactRows }
        : undefined,
    });
  }

  const leftover = remaining.filter((r) => !used.has(r));
  if (leftover.length) {
    // Prefer Trim over a catch-all Other when leftovers are only VHS/trim rows
    // (defensive if match rules drift). Otherwise keep Other for true unknowns.
    const allTrim = leftover.every(
      (r) =>
        r.label.startsWith("VHS ") ||
        r.label.toLowerCase().startsWith("trim ") ||
        r.label === "skip_first_frames" ||
        r.label === "frame_load_cap",
    );
    groups.push({
      id: allTrim ? "trim" : "other",
      title: allTrim ? "Trim" : "Other",
      rows: leftover,
    });
  }
  return groups;
}

/** Friendlier display labels for the Run / Selection collections. */
const DETAIL_LABELS: Record<string, string> = {
  Created: "Created",
  "Job key": "Job key",
  "Job file": "Job file",
  "Comfy prompt ID": "Comfy prompt ID",
  "Comfy submit JSON": "Comfy submit JSON",
  // Legacy API labels (in case an older server is still running).
  "Prompt id": "Comfy prompt ID",
  "Submit prompt JSON": "Comfy submit JSON",
  "Output prefix": "Output prefix",
  "Plan source tag": "Source",
  "Derive action": "Derive",
  "Combo key": "Combo",
  "Parent output": "Parent",
  "Disposition note": "Note",
  "Upgraded from": "Upgraded from",
  Cursor: "Cursor",
  "Fast track": "Fast track",
  "Selection weight": "Weight",
  "Recipe pool size": "Recipes",
  "Seed count": "Seeds",
  "Derive attempts": "Attempts",
  "Used recent fallback": "Recent fallback",
  Shape: "Shape ID",
  "Shape path": "Shape file",
  Template: "Template",
  "Generated workflow": "Workflow",
  "Graph hash": "Graph hash",
  "Prompt name": "Name",
  "Prompt profile": "Profile",
  "Prompt label": "Name",
  "Prompt file": "Profile",
  "VHS skip_first_frames": "skip_first_frames",
  "VHS frame_load_cap": "frame_load_cap",
  Exec: "Exec",
  "Exec sec": "Exec (s)",
  "Queue wait sec": "Queue wait (s)",
  "Wall sec": "Submit→done (s)",
  "Model load sec": "Model load (s)",
  "Unload→reload sec": "Unload→reload (s)",
  "Models loaded": "Models",
  "Model load count": "Load count",
  "Unload events": "Unload events",
  "Sec per frame": "Sec / frame",
  "Workload frames": "Frames",
  "Exec terminal": "Terminal",
  "Frames before→after": "Frames before→after",
};

function displayDetailLabel(_groupId: string, label: string): string {
  return DETAIL_LABELS[label] || label;
}

function displayDetailValue(row: WorkProductDetailRow): string {
  if (row.label === "Created") return formatWhen(row.value);
  if (
    row.label === "Exec sec" ||
    row.label === "Queue wait sec" ||
    row.label === "Wall sec" ||
    row.label === "Model load sec" ||
    row.label === "Unload→reload sec"
  ) {
    const n = Number(row.value);
    if (Number.isFinite(n)) {
      const pretty = formatDurationSec(n);
      return pretty ? `${pretty} (${n.toFixed(n < 10 ? 2 : 0)}s)` : String(row.value);
    }
  }
  if (row.label === "Sec per frame") {
    const n = Number(row.value);
    if (Number.isFinite(n)) return n < 10 ? n.toFixed(2) : String(Math.round(n));
  }
  return row.value;
}

const EMPTY_EXTEND_DEFAULTS: Record<string, string> = {};

function smartVaryFamily(item: WorkProductItem): string {
  const fromWi = String(openPoolItem(item.work_items_open, "vary")?.factory_family || "").trim();
  if (fromWi) return fromWi;
  return String(item.family_slug || "").trim();
}

function smartDeriveFamily(item: WorkProductItem): string {
  const fromWi = String(openPoolItem(item.work_items_open, "derive")?.factory_family || "").trim();
  if (fromWi) return fromWi;
  return String(item.family_slug || "").trim();
}

function smartExtendFamily(item: WorkProductItem, successors: Record<string, string>): string {
  const fromWi = String(openPoolItem(item.work_items_open, "extend")?.factory_family || "").trim();
  if (fromWi) return fromWi;
  const src = String(item.family_slug || "").trim();
  if (src && successors[src]) return successors[src];
  return src;
}



function badgePriorityClass(priority: string | undefined): string {
  return priority === "front" ? "work-product-badge--front" : "work-product-badge--pending";
}

function workbenchAdvanceSubmitHref(
  item: WorkProductItem,
  opts: {
    outputTrim: InputTrimState;
    sourceClipId?: string | null;
    extendFamilyDefaults?: Record<string, string>;
    step?: string | null;
    clip?: ShapeFactoryClip | null;
  },
): string {
  const media = String(item.output_relpath || "").trim();
  const successors = opts.extendFamilyDefaults || EMPTY_EXTEND_DEFAULTS;
  const family = smartExtendFamily(item, successors);
  const clip = opts.clip || null;
  return submitHref({
    mediaRelpath: media || null,
    fromJob: item.job_key || null,
    family: family || item.family_slug || null,
    markIn: clip ? clip.mark_in_s : opts.outputTrim.markIn,
    markOut: clip ? clip.mark_out_s : opts.outputTrim.markOut,
    clipId: clip?.clip_id || opts.sourceClipId || null,
    step: opts.step || "advance.extend",
    origin: "workbench",
  });
}

function WorkProductQuickQueue({
  item,
  extendFamilyDefaults,
  outputTrim,
  sourceTrim,
  sourceClipId,
  onCommitted,
}: {
  item: WorkProductItem;
  families?: WorkProductFamilyOption[];
  extendFamilyDefaults?: Record<string, string>;
  outputTrim: InputTrimState;
  sourceTrim: InputTrimState;
  sourceClipId?: string | null;
  onCommitted?: () => void;
}) {
  const open = item.work_items_open || [];
  const extendOpen = openPoolItem(open, "extend");
  const varyOpen = openPoolItem(open, "vary");
  const deriveOpen = openPoolItem(open, "derive");
  const relpath = String(item.output_relpath || "").trim();
  const jobKey = String(item.job_key || "").trim();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const canRerun = Boolean(jobKey) && !busy;
  const canUnqueue = canUnqueueWorkProduct(item) && !busy;
  const canArchive = canArchiveTerminalWorkProduct(item) && !busy;
  const canDelete = canDeleteWorkProduct(item) && !busy;
  const deleteIsPendingOnly = canDiscardPendingWorkProduct(item);
  const nonFactory = isNonFactoryWorkProduct(item);
  const submitUrl = workbenchAdvanceSubmitHref(item, {
    outputTrim,
    sourceClipId,
    extendFamilyDefaults,
  });

  const unqueue = async () => {
    const pid = String(item.prompt_id || "").trim();
    if (!pid || busy) return;
    if (nonFactory) {
      const ok = window.confirm(
        "Remove from Comfy queue?\n\n" +
          "This prompt is not a shape-factory job. Removing it will not create an editable pending job — " +
          "it only deletes it from Comfy’s waiting queue.",
      );
      if (!ok) return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const res = await unqueueShapeFactory({
        prompt_id: pid,
        job_key: nonFactory ? undefined : jobKey || undefined,
        job_path: nonFactory ? undefined : String(item.job_path || "").trim() || undefined,
      });
      if (res.factory_job) {
        setMsg(`Unqueued → pending${res.job_key ? ` · ${res.job_key}` : ""}`);
      } else {
        setMsg("Removed from Comfy queue (no factory job)");
      }
      onCommitted?.();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const discard = async () => {
    if (!canDelete || busy) return;
    const historyStub = isHistoryFailureStub(item);
    const kind = historyStub ? "history failure" : deleteIsPendingOnly ? "pending job" : "failed job";
    const ok = window.confirm(
      historyStub
        ? `Dismiss this ${kind} from Workbench?\n\n` +
            "There is no factory .job.json on disk — this only hides the Comfy history stub. " +
            "It will not reappear after refresh. Media under output/ is not deleted."
        : `Permanently delete this ${kind}?\n\n` +
            "This expunges the .job.json and related sidecars (prompt/submit/timings/workflow) from disk. " +
            "It cannot be undone. Media outputs under output/ are not deleted.",
    );
    if (!ok) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await discardShapeFactoryJob({
        job_key: jobKey || undefined,
        job_path: String(item.job_path || "").trim() || undefined,
        prompt_id: String(item.prompt_id || "").trim() || undefined,
        history_from_comfy: isHistoryFailureStub(item) || Boolean(item.history_from_comfy),
        reason: deleteIsPendingOnly ? "user_expunged" : "user_expunged_failure",
        expunge: true,
      });
      setMsg(
        res.history_stub || res.dismissed
          ? `Dismissed history failure${res.job_key ? ` · ${res.job_key}` : ""}`
          : `Deleted${res.job_key ? ` · ${res.job_key}` : ""}`,
      );
      onCommitted?.();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const archive = async () => {
    if (!canArchive || busy) return;
    const ok = window.confirm(
      "Archive this failed job?\n\n" +
        "Removes it from Workbench. The job + sidecars are renamed to .discarded on disk " +
        "(kept for forensics; no restore UI). Prefer Delete if you want them gone for good. " +
        "Media outputs are not deleted.",
    );
    if (!ok) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await discardShapeFactoryJob({
        job_key: jobKey || undefined,
        job_path: String(item.job_path || "").trim() || undefined,
        prompt_id: String(item.prompt_id || "").trim() || undefined,
        history_from_comfy: isHistoryFailureStub(item) || Boolean(item.history_from_comfy),
        reason: "user_archived_failure",
        expunge: false,
      });
      setMsg(
        res.history_stub || res.dismissed
          ? `Dismissed history failure${res.job_key ? ` · ${res.job_key}` : ""}`
          : `Archived failure${res.job_key ? ` · ${res.job_key}` : ""}`,
      );
      onCommitted?.();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const rerun = async (when: "now" | "later") => {
    if (!jobKey || busy) return;
    setBusy(true);
    setMsg("");
    try {
      const { overrides, warning } = trimOverridesFromState(sourceTrim, null);
      const res = await replayShapeFactory({
        job_key: jobKey,
        family_slug: String(item.family_slug || "").trim() || undefined,
        extend: false,
        front: when === "now",
        overrides,
      });
      const nextKey = String(res.job_key || "").trim();
      const pid = String(res.prompt_id || "").trim();
      const clampMsg = res.trim_clamped?.message || warning;
      setMsg(
        [
          nextKey
            ? `Re-run ${when}→${nextKey}${pid ? ` · ${pid}` : ""}`
            : pid
              ? `Re-run ${when} queued · ${pid}`
              : `Re-run ${when} queued`,
          clampMsg,
        ]
          .filter(Boolean)
          .join(" · "),
      );
      onCommitted?.();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const openBadge = (wi: WorkItem | null, label: string) =>
    wi ? (
      <span
        className={`work-product-badge ${badgePriorityClass(wi.priority)}`}
        title={`${label} · ${wi.status}`}
      >
        {label}:{wi.priority === "front" ? "now" : "later"}
        {wi.status === "running" ? "·run" : ""}
      </span>
    ) : null;

  return (
    <div className="work-product-quick-queue" role="group" aria-label="Job actions">
      <div className="work-product-quick-queue__row">
        <span className="work-product-quick-queue__label" title="Compose the next Advance on Submit">
          Advance
        </span>
        {relpath ? (
          <a
            className="drt-btn work-product-quick-queue__now"
            href={submitUrl}
            title="Open Submit with Extend / Vary / Derive for this output"
          >
            Open in Submit
          </a>
        ) : (
          <span className="work-product-quick-queue__hint">No output</span>
        )}
        {openBadge(extendOpen, "Extend")}
        {openBadge(varyOpen, "Vary")}
        {openBadge(deriveOpen, "Derive")}
        <span className="work-product-quick-queue__sep" aria-hidden="true" />
        <span className="work-product-quick-queue__label">Re-run</span>
        <button
          type="button"
          className="drt-btn work-product-quick-queue__rerun"
          disabled={!canRerun}
          title="Submit a new identical job at the front of the queue"
          onClick={() => void rerun("now")}
        >
          Now
        </button>
        <button
          type="button"
          className="drt-btn work-product-quick-queue__rerun"
          disabled={!canRerun}
          title="Submit a new identical job at normal queue priority"
          onClick={() => void rerun("later")}
        >
          Later
        </button>
        {canUnqueue ? (
          <>
            <span className="work-product-quick-queue__sep" aria-hidden="true" />
            <button
              type="button"
              className="drt-btn work-product-quick-queue__unqueue"
              disabled={!canUnqueue}
              title={
                nonFactory
                  ? "Remove this non-factory prompt from Comfy’s waiting queue"
                  : "Remove from Comfy waiting queue and return this job to editable pending"
              }
              onClick={() => void unqueue()}
            >
              Unqueue
            </button>
          </>
        ) : null}
        {canArchive ? (
          <>
            <span className="work-product-quick-queue__sep" aria-hidden="true" />
            <button
              type="button"
              className="drt-btn work-product-quick-queue__discard"
              disabled={!canArchive}
              title="Remove from Workbench; soft-archive job + sidecars as .discarded (no restore UI)"
              onClick={() => void archive()}
            >
              Archive
            </button>
          </>
        ) : null}
        {canDelete ? (
          <>
            <span className="work-product-quick-queue__sep" aria-hidden="true" />
            <button
              type="button"
              className="drt-btn work-product-quick-queue__discard"
              disabled={!canDelete}
              title={
                deleteIsPendingOnly
                  ? "Permanently delete this pending job and its sidecars from disk"
                  : "Permanently delete this failed job and its sidecars from disk"
              }
              onClick={() => void discard()}
            >
              Delete
            </button>
          </>
        ) : null}
      </div>
      {msg ? <p className="work-product-quick-queue__msg" title={msg}>{msg}</p> : null}
    </div>
  );
}


function WorkProductDetails({
  item,
  families,
  extendFamilyDefaults,
  outputTrim,
  sourceTrim,
  sourceClipId,
  onCommitted,
}: {
  item: WorkProductItem;
  families?: WorkProductFamilyOption[];
  extendFamilyDefaults?: Record<string, string>;
  outputTrim: InputTrimState;
  sourceTrim: InputTrimState;
  sourceClipId?: string | null;
  onCommitted?: () => void;
}) {
  const groups = useMemo(() => {
    const filtered = (item.details || []).filter(
      (r) => r.label !== "Prompt positive" && r.label !== "Prompt negative",
    );
    return groupDetailRows(filtered);
  }, [item.details]);
  const [sectionOpen, setSectionOpen] = useState<Record<string, boolean>>(() => loadSectionOpen());
  const prompt = item.prompt_profile;
  const shape = item.shape_profile;

  const setGroupOpen = (id: string, open: boolean) => {
    setSectionOpen((prev) => {
      const next = { ...prev, [id]: open };
      persistSectionOpen(next);
      return next;
    });
  };
  return (
    <div className="work-product-details">
      <div className="work-product-details__chips">
        {item.is_hourly ? (
          <span className="work-product-badge work-product-badge--hourly" title="Produced by the hourly planner">
            Hourly
          </span>
        ) : null}
        {item.family_slug ? <span className="work-product-badge">{item.family_slug}</span> : null}
        {item.pick_mode ? (
          <span className={`work-product-badge ${badgeClass(item.pick_mode)}`}>{item.pick_mode}</span>
        ) : null}
        {item.step ? <span className={`work-product-badge ${badgeClass(item.step)}`}>{item.step}</span> : null}
        {item.rating_kind ? (
          <span className={`work-product-badge ${badgeClass(item.rating_kind)}`}>{item.rating_kind}</span>
        ) : null}
        {item.disposition_entry ? <span className="work-product-badge">{item.disposition_entry}</span> : null}
        {(() => {
          const timing = timingHeadline(item);
          if (!timing) return null;
          return (
            <span
              className={`work-product-badge work-product-badge--timing${
                timing.bad ? " work-product-badge--timing-bad" : ""
              }`}
              title={timing.title}
            >
              {timing.text}
            </span>
          );
        })()}
        {item.applied_vhs &&
        (Number(item.applied_vhs.skip_first_frames ?? 0) > 0 ||
          Number(item.applied_vhs.frame_load_cap ?? 0) > 0) ? (
          <span
            className="work-product-badge"
            title="VHS loader window applied on this job (source input)"
          >
            skip {Number(item.applied_vhs.skip_first_frames ?? 0)}
            {Number(item.applied_vhs.frame_load_cap ?? 0) > 0
              ? ` · cap ${Number(item.applied_vhs.frame_load_cap)}`
              : ""}
          </span>
        ) : null}
        {item.status ? (
          <span
            className={`work-product-badge ${badgeClass(item.status)}`}
            title={item.error || item.status}
          >
            {item.status}
          </span>
        ) : null}
      </div>
      {item.error ? (
        <div className="work-product-details__error" title={item.error}>
          {item.error}
        </div>
      ) : null}
      <WorkProductQuickQueue
        item={item}
        families={families}
        extendFamilyDefaults={extendFamilyDefaults}
        outputTrim={outputTrim}
        sourceTrim={sourceTrim}
        sourceClipId={sourceClipId}
        onCommitted={onCommitted}
      />
      <div className="work-product-details__groups">
        {groups.map((group) => {
          const renderRow = (row: WorkProductDetailRow, opts?: { kv?: boolean; compact?: boolean }) => {
            const long =
              !opts?.kv &&
              !opts?.compact &&
              (row.value.length > 72 ||
                Boolean(row.json_path) ||
                row.label === "Disposition note" ||
                row.label === "Parent output" ||
                row.label === "Combo key" ||
                row.label.startsWith("Binding · "));
            const compactWide = Boolean(opts?.compact && (row.label === "Graph hash" || row.value.length > 40));
            const displayValue = displayDetailValue(row);
            const valueRow: WorkProductDetailRow =
              displayValue === row.value ? row : { ...row, value: displayValue };
            return (
              <div
                key={`${row.label}:${row.value.slice(0, 40)}`}
                className={`work-product-details__row${long ? " work-product-details__row--span" : ""}${
                  opts?.kv ? " work-product-details__row--kv" : ""
                }${opts?.compact ? " work-product-details__row--compact" : ""}${
                  compactWide ? " work-product-details__row--compact-wide" : ""
                }`}
              >
                <dt>{displayDetailLabel(group.id, row.label)}</dt>
                <dd>
                  <DetailValue
                    row={valueRow}
                    prompt={prompt}
                    shape={shape}
                    bindings={item.bindings}
                  />
                </dd>
              </div>
            );
          };

          return (
            <details
              key={group.id}
              className={`work-product-details__group${group.wide ? " work-product-details__group--wide" : ""}${
                group.kv ? " work-product-details__group--kv" : ""
              }`}
              open={sectionOpen[group.id] ?? DEFAULT_SECTION_OPEN[group.id] ?? true}
              onToggle={(e) => {
                const el = e.currentTarget;
                setGroupOpen(group.id, el.open);
              }}
            >
              <summary className="work-product-details__group-title">{group.title}</summary>
              {group.rows.length ? (
                <dl
                  className={`work-product-details__list${group.wide ? " work-product-details__list--wide" : ""}${
                    group.kv ? " work-product-details__list--kv" : ""
                  }`}
                >
                  {group.rows.map((row) => renderRow(row, { kv: group.kv }))}
                </dl>
              ) : null}
              {group.compact ? (
                <div className="work-product-details__compact">
                  {group.compact.title ? (
                    <div className="work-product-details__compact-title">{group.compact.title}</div>
                  ) : null}
                  <dl className="work-product-details__list work-product-details__list--compact">
                    {group.compact.rows.map((row) => renderRow(row, { compact: true }))}
                  </dl>
                </div>
              ) : null}
            </details>
          );
        })}
      </div>
    </div>
  );
}

function WorkProductRow({
  item,
  layout,
  families,
  extendFamilyDefaults,
  onCommitted,
}: {
  item: WorkProductItem;
  layout: RowLayout;
  families?: WorkProductFamilyOption[];
  extendFamilyDefaults?: Record<string, string>;
  onCommitted?: () => void;
}) {
  const thumbMeta = isSourceThumbPreviewItem(item) ? sourceThumbPreviewMeta(item) : null;
  const thumbBadgeClass = thumbMeta ? `work-product-badge--live-${thumbMeta.visual}` : "";
  const successors = extendFamilyDefaults || {};
  const extendFamily = smartExtendFamily(item, successors);
  const varyFamily = smartVaryFamily(item);
  const outputDefaults = familyVhsDefaults(families, extendFamily || String(item.family_slug || ""));
  const applied = item.applied_vhs;
  const sourceDefaults =
    applied && (applied.skip_first_frames != null || applied.frame_load_cap != null)
      ? {
          skip_first_frames: Math.max(0, Math.floor(Number(applied.skip_first_frames ?? 0) || 0)),
          frame_load_cap: Math.max(0, Math.floor(Number(applied.frame_load_cap ?? 0) || 0)),
        }
      : familyVhsDefaults(families, varyFamily || String(item.family_slug || ""));
  const [outputTrim, setOutputTrim] = useState<InputTrimState>(() => emptyTrimState(parseFps(item.media_meta?.fps)));
  const [sourceTrim, setSourceTrim] = useState<InputTrimState>(() => emptyTrimState(parseFps(item.media_meta?.fps)));
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);

  return (
    <article
      id={`workbench-job-${String(item.job_key || "").replace(/[^\w.-]+/g, "_")}`}
      data-job-key={item.job_key || undefined}
      data-prompt-id={item.prompt_id || undefined}
      className={`work-product-row work-product-row--${layout}${
        isLivePreviewItem(item) ? " work-product-row--live" : ""
      }`}
    >
      <header
        className={`work-product-row__head${
          isRunningLiveItem(item) && item.prompt_id ? " work-product-row__head--live-metrics" : ""
        }`}
      >
        <div className="work-product-row__head-main">
          <div className="work-product-row__title">
            <strong>{item.family_slug || "job"}</strong>
            {item.is_hourly ? (
              <span className="work-product-badge work-product-badge--hourly" title="Produced by the hourly planner">
                Hourly
              </span>
            ) : null}
            {isRunningLiveItem(item) ? (
              <span className="work-product-badge work-product-badge--live-run">live</span>
            ) : thumbMeta ? (
              <span className={`work-product-badge ${thumbBadgeClass}`}>{thumbMeta.label}</span>
            ) : null}
            <span className="work-product-row__when">{formatWhen(item.created_at)}</span>
            {(() => {
              const timing = timingHeadline(item);
              if (!timing) return null;
              return (
                <span
                  className={`work-product-row__timing${timing.bad ? " work-product-row__timing--bad" : ""}`}
                  title={timing.title}
                >
                  {timing.text}
                </span>
              );
            })()}
          </div>
          <code className="work-product-row__key" title={item.job_key}>
            {item.job_key}
          </code>
        </div>
        {isRunningLiveItem(item) && item.prompt_id ? (
          <ComfyLiveMetricsBar
            promptId={String(item.prompt_id)}
            submittedAt={item.submitted_at || item.created_at}
          />
        ) : null}
      </header>
      <div className="work-product-row__body">
        <WorkProductViewer
          item={item}
          outputTrim={outputTrim}
          sourceTrim={sourceTrim}
          onOutputTrimChange={setOutputTrim}
          onSourceTrimChange={setSourceTrim}
          outputDefaults={outputDefaults}
          sourceDefaults={sourceDefaults}
          selectedClipId={selectedClipId}
          onSelectClip={(c) => setSelectedClipId(c?.clip_id || null)}
          onUseForExtend={(clip) => {
            setSelectedClipId(clip.clip_id);
            setSourceTrim((prev) => ({
              ...prev,
              markIn: clip.mark_in_s,
              markOut: clip.mark_out_s,
              dirty: true,
              warning: null,
              clampedDefault: false,
            }));
            const href = workbenchAdvanceSubmitHref(item, {
              outputTrim,
              sourceClipId: clip.clip_id,
              extendFamilyDefaults,
              clip,
              step: "advance.extend",
            });
            window.location.assign(href);
          }}
        />
        <WorkProductDetails
          item={item}
          families={families}
          extendFamilyDefaults={extendFamilyDefaults}
          outputTrim={outputTrim}
          sourceTrim={sourceTrim}
          sourceClipId={selectedClipId}
          onCommitted={onCommitted}
        />
      </div>
    </article>
  );
}

export function WorkProductsApp() {
  const deepLink = useMemo(() => parseWorkbenchDeepLink(), []);
  const [layout, setLayout] = useState<RowLayout>(() => loadLayout());
  const [sort, setSort] = useState<WorkProductSort>(() => loadSort());
  const [nameQuery, setNameQuery] = useState(() => deepLink.filter || "");
  const [items, setItems] = useState<WorkProductItem[]>([]);
  const [families, setFamilies] = useState<WorkProductFamilyOption[]>([]);
  const [extendFamilyDefaults, setExtendFamilyDefaults] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [limit, setLimit] = useState(() => (deepLink.filter ? 80 : 50));
  const [hourlyOnly, setHourlyOnly] = useState(() => (deepLink.filter ? false : loadHourlyOnly()));
  const [statusOff, setStatusOff] = useState<Set<string>>(() => loadStatusFilterOff());
  const [markerOff, setMarkerOff] = useState<Set<string>>(() => loadMarkerFilterOff());
  const [clearFailedBusy, setClearFailedBusy] = useState(false);
  const [clearFailedMsg, setClearFailedMsg] = useState<string | null>(null);
  const deepLinkScrolled = useRef(false);

  const statusCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const it of items) {
      const key = workProductStatusKey(it);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return counts;
  }, [items]);

  const markerCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const it of items) {
      const key = workProductMarkerKey(it);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return counts;
  }, [items]);

  const availableStatuses = useMemo(() => {
    const keys = new Set(statusCounts.keys());
    for (const s of statusOff) keys.add(s);
    return collectAvailableStatuses([...keys].map((status) => ({ status }) as WorkProductItem));
  }, [statusCounts, statusOff]);

  const availableMarkers = useMemo(() => {
    const keys = new Set(markerCounts.keys());
    for (const s of markerOff) keys.add(s);
    return collectAvailableMarkers([...keys].map((pick_mode) => ({ pick_mode }) as WorkProductItem));
  }, [markerCounts, markerOff]);

  const visibleItems = useMemo(
    () =>
      sortWorkProducts(
        filterWorkProductsByMarker(
          filterWorkProductsByStatus(filterWorkProductsByName(items, nameQuery), statusOff),
          markerOff,
        ),
        sort,
      ),
    [items, nameQuery, sort, statusOff, markerOff],
  );

  const failedVisible = useMemo(
    () => visibleItems.filter((it) => canArchiveTerminalWorkProduct(it)),
    [visibleItems],
  );

  useEffect(() => {
    if (deepLinkScrolled.current || loading || !deepLink.filter || !visibleItems.length) return;
    const needleJob = (deepLink.job || "").toLowerCase();
    const needlePid = (deepLink.promptId || "").toLowerCase();
    const match =
      visibleItems.find((it) => needleJob && String(it.job_key || "").toLowerCase() === needleJob) ||
      visibleItems.find((it) => needlePid && String(it.prompt_id || "").toLowerCase() === needlePid) ||
      visibleItems[0];
    if (!match) return;
    const id = `workbench-job-${String(match.job_key || "").replace(/[^\w.-]+/g, "_")}`;
    const el = document.getElementById(id);
    if (!el) return;
    deepLinkScrolled.current = true;
    window.requestAnimationFrame(() => {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      el.classList.add("work-product-row--deep-link");
      window.setTimeout(() => el.classList.remove("work-product-row--deep-link"), 2400);
    });
  }, [deepLink.filter, deepLink.job, deepLink.promptId, loading, visibleItems]);

  const toggleStatusFilter = (status: string) => {
    setStatusOff((prev) => {
      const next = new Set(prev);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      persistStatusFilterOff(next);
      return next;
    });
  };

  /** Double-click: radio-style focus — only this status on within the status set. */
  const focusStatusFilter = (status: string) => {
    const next = new Set(availableStatuses.filter((s) => s !== status));
    persistStatusFilterOff(next);
    setStatusOff(next);
  };

  const toggleMarkerFilter = (marker: string) => {
    setMarkerOff((prev) => {
      const next = new Set(prev);
      if (next.has(marker)) next.delete(marker);
      else next.add(marker);
      persistMarkerFilterOff(next);
      return next;
    });
  };

  /** Double-click: radio-style focus — only this pick-mode on within the marker set. */
  const focusMarkerFilter = (marker: string) => {
    const next = new Set(availableMarkers.filter((m) => m !== marker));
    persistMarkerFilterOff(next);
    setMarkerOff(next);
  };

  const refresh = (opts?: { quiet?: boolean }) => {
    if (!opts?.quiet) setLoading(true);
    void fetchShapeFactoryWorkProducts({ limit, hourlyOnly })
      .then((res) => {
        setItems(res.items || []);
        setFamilies(res.families || []);
        setExtendFamilyDefaults((prev) => {
          const next = res.extend_family_defaults || {};
          try {
            return JSON.stringify(prev) === JSON.stringify(next) ? prev : next;
          } catch {
            return next;
          }
        });
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => {
        if (!opts?.quiet) setLoading(false);
      });
  };

  const clearFailedVisible = async () => {
    const targets = failedVisible;
    if (!targets.length || clearFailedBusy) return;
    const ok = window.confirm(
      `Permanently delete ${targets.length} failed job${targets.length === 1 ? "" : "s"} ` +
        `from the current Workbench list?\n\n` +
        "Statuses: error, failed, interrupted, abandoned.\n" +
        "Deletes .job.json + sidecars. Media under output/ is kept.\n" +
        "Only the currently loaded/filtered rows are affected.",
    );
    if (!ok) return;
    setClearFailedBusy(true);
    setClearFailedMsg(null);
    let deleted = 0;
    const errors: string[] = [];
    for (const it of targets) {
      try {
        await discardShapeFactoryJob({
          job_key: String(it.job_key || "").trim() || undefined,
          job_path: String(it.job_path || "").trim() || undefined,
          prompt_id: String(it.prompt_id || "").trim() || undefined,
          history_from_comfy: isHistoryFailureStub(it) || Boolean(it.history_from_comfy),
          reason: "user_bulk_expunged_failure",
          expunge: true,
        });
        deleted += 1;
      } catch (e) {
        errors.push(
          `${it.job_key || "?"}: ${e instanceof Error ? e.message : String(e)}`,
        );
      }
    }
    setClearFailedBusy(false);
    setClearFailedMsg(
      errors.length
        ? `Deleted ${deleted}/${targets.length} · ${errors.length} failed`
        : `Deleted ${deleted} failed job${deleted === 1 ? "" : "s"}`,
    );
    refresh({ quiet: true });
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const res = await fetchShapeFactoryWorkProducts({ limit, hourlyOnly });
        if (cancelled) return;
        setItems(res.items || []);
        setFamilies(res.families || []);
        setExtendFamilyDefaults((prev) => {
          const next = res.extend_family_defaults || {};
          try {
            return JSON.stringify(prev) === JSON.stringify(next) ? prev : next;
          } catch {
            return next;
          }
        });
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [limit, hourlyOnly]);

  // Quiet refresh so in-flight jobs swap to the finished file when ready.
  useEffect(() => {
    const needs = items.some((it) => isLivePreviewItem(it));
    if (!needs) return;
    const id = window.setInterval(() => refresh({ quiet: true }), 8000);
    return () => window.clearInterval(id);
  }, [items, limit, hourlyOnly]);

  return (
    <PipelineScreen className="work-products">
      <PageHeader
        title="Workbench"
        subtitle="Set up jobs that seed factories — recent outputs, construction, trim, and queue"
        actions={
          <>
            <label className="pipeline-tray-switch" title="Worktrays coming soon — Recent is the default working set">
              <span>Working set</span>
              <select value="recent" aria-label="Workbench working set" disabled>
                <option value="recent">Recent</option>
              </select>
            </label>
            <label className="work-products-search">
              <span className="work-products-search__label">Search</span>
              <input
                type="search"
                value={nameQuery}
                onChange={(e) => setNameQuery(e.target.value)}
                placeholder="Family or job key…"
                aria-label="Filter work products by name"
              />
            </label>
            <label className="work-products-limit">
              Show
              <select
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
                aria-label="How many recent work products to load"
              >
                {[20, 30, 50, 80, 120].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <label className="work-products-limit">
              Sort
              <select
                value={sort}
                onChange={(e) => {
                  const next = e.target.value as WorkProductSort;
                  setSort(next);
                  persistSort(next);
                }}
                aria-label="Sort work products"
                title="Live previews always stay on top"
              >
                {SORT_OPTIONS.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <div className="discovery-preview-layout-switch" role="group" aria-label="Row layout">
              <span className="discovery-preview-layout-switch__label">Layout</span>
              <div className="segmented">
                <button
                  type="button"
                  className={layout === "split" ? "is-active" : undefined}
                  onClick={() => {
                    setLayout("split");
                    persistLayout("split");
                  }}
                >
                  Side by side
                </button>
                <button
                  type="button"
                  className={layout === "stacked" ? "is-active" : undefined}
                  onClick={() => {
                    setLayout("stacked");
                    persistLayout("stacked");
                  }}
                >
                  Stacked
                </button>
              </div>
            </div>
            <button type="button" onClick={() => refresh()}>
              Refresh
            </button>
            {failedVisible.length ? (
              <button
                type="button"
                className="work-products-clear-failed"
                disabled={clearFailedBusy}
                title="Permanently delete error/failed/interrupted/abandoned jobs in the current list (filtered view)"
                onClick={() => void clearFailedVisible()}
              >
                {clearFailedBusy
                  ? "Deleting…"
                  : `Delete failed (${failedVisible.length})`}
              </button>
            ) : null}
            {clearFailedMsg ? (
              <span className="work-products-clear-failed__msg" title={clearFailedMsg}>
                {clearFailedMsg}
              </span>
            ) : null}
          </>
        }
      />
      <div className="work-products-status-filters pipeline-filter-row" role="group" aria-label="Work product filters">
        <button
          type="button"
          className={`work-products-status-toggle work-products-status-toggle--hourly${
            hourlyOnly ? " is-on" : " is-off"
          }`}
          aria-pressed={hourlyOnly}
          title={
            hourlyOnly
              ? "Hourly only — click to show all jobs"
              : "Showing all jobs — click for hourly only"
          }
          onClick={() => {
            const next = !hourlyOnly;
            setHourlyOnly(next);
            persistHourlyOnly(next);
          }}
        >
          <span className="work-products-status-toggle__label">hourly only</span>
        </button>
        {availableMarkers.length ? (
          <>
            <span className="work-products-status-filters__sep" aria-hidden="true" />
            <div className="work-products-status-filters__group" role="group" aria-label="Filter by pick mode">
              {availableMarkers.map((marker) => {
                const on = !markerOff.has(marker);
                const count = markerCounts.get(marker) || 0;
                const label = markerFilterLabel(marker);
                return (
                  <button
                    key={`marker-${marker}`}
                    type="button"
                    className={markerFilterButtonClass(marker, on)}
                    aria-pressed={on}
                    title={
                      on
                        ? `Showing ${label} (${count}) — click to hide · double-click to show only this`
                        : `Hidden ${label} (${count}) — click to show · double-click to show only this`
                    }
                    onClick={() => toggleMarkerFilter(marker)}
                    onDoubleClick={(e) => {
                      e.preventDefault();
                      focusMarkerFilter(marker);
                    }}
                  >
                    <span className="work-products-status-toggle__label">{label}</span>
                    <span className="work-products-status-toggle__count">{count}</span>
                  </button>
                );
              })}
            </div>
          </>
        ) : null}
        {availableStatuses.length ? (
          <>
            <span className="work-products-status-filters__sep" aria-hidden="true" />
            <div className="work-products-status-filters__group" role="group" aria-label="Filter by job status">
              {availableStatuses.map((status) => {
                const on = !statusOff.has(status);
                const count = statusCounts.get(status) || 0;
                return (
                  <button
                    key={`status-${status}`}
                    type="button"
                    className={statusFilterButtonClass(status, on)}
                    aria-pressed={on}
                    title={
                      on
                        ? `Showing ${status} (${count}) — click to hide · double-click to show only this`
                        : `Hidden ${status} (${count}) — click to show · double-click to show only this`
                    }
                    onClick={() => toggleStatusFilter(status)}
                    onDoubleClick={(e) => {
                      e.preventDefault();
                      focusStatusFilter(status);
                    }}
                  >
                    <span className="work-products-status-toggle__label">{status}</span>
                    <span className="work-products-status-toggle__count">{count}</span>
                  </button>
                );
              })}
            </div>
          </>
        ) : null}
      </div>

      <div className="work-products-scroll">
        {error ? <div className="work-products-error">{error}</div> : null}
        {loading && !items.length ? <div className="work-products-empty">Loading…</div> : null}
        {!loading && !error && !items.length ? (
          <div className="work-products-empty">
            {hourlyOnly ? "No hourly work products found." : "No work products found."}
          </div>
        ) : null}
        {!loading && !error && items.length && !visibleItems.length ? (
          <div className="work-products-empty">
            {nameQuery.trim()
              ? `No work products match “${nameQuery.trim()}”.`
              : "No work products match the selected filters."}
          </div>
        ) : null}

        <div className="work-products-list">
          {visibleItems.map((item) => (
            <WorkProductRow
              key={item.job_key}
              item={item}
              layout={layout}
              families={families}
              extendFamilyDefaults={extendFamilyDefaults}
              onCommitted={() => refresh({ quiet: true })}
            />
          ))}
        </div>
      </div>
    </PipelineScreen>
  );
}
