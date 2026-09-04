import React, { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createPortal } from "react-dom";
import { discardShapeFactoryJob, fetchShapeFactoryWorkProduct, fetchShapeFactoryWorkProducts, finishShapeFactoryEdit, claimShapeFactoryFromQueue, promoteShapeFactoryTemplate, replayShapeFactory, swapShapeFactoryFamily, unqueueShapeFactory, updatePendingShapeFactoryTrim, updateShapeFactoryOwnedLoras, updateShapeFactoryOwnedParams, updateShapeFactoryOwnedPrompt } from "./api";
import type { ShapeFactoryClip } from "./api";
import { ClipBookmarksRail, pickDefaultClip } from "./ClipBookmarksRail";
import { ComfyLiveMetricsBar, ComfyLivePreview } from "./ComfyLivePreview";
import { PageHeader } from "./PageHeader";
import { PipelineScreen } from "./PipelineScreen";
import { JsonPeekButton, PromptMarkupTable, PromptPeekButton } from "./PromptPeek";
import {
  clonePromptRows,
  encodePromptRowsClient,
  PromptChunkDiff,
  PromptChunkEditor,
  PromptSnowflakeChip,
  rowsFromRawText,
} from "./PromptChunks";
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
import { discoveryLibraryHref, extractContentIdFromName, parseWorkbenchDeepLink, stillsHref, buildSubmitDeepLink, lineageSummaryHref, workbenchHref, workbenchHrefForMedia, isLineageInputStill, type SubmitDeepLink } from "./discoveryDeepLink";
import { factoryMapFamilyHref } from "./factoryMapRoute";
import { AppetitePreviewBadge, AppetitePreviewFrame } from "./AppetitePreviewBadge";
import { WorkProductAppetiteStrip } from "./WorkProductAppetiteStrip";
import { DiscoveryAssetLineagePanel } from "./DiscoveryAssetLineagePanel";
import { SubmitComposerModal } from "./SubmitComposerModal";
import { loadClipsForMedia, rememberFamiliesFromWorkProducts } from "./shapeFactorySessionCache";
import { familySwapTargets, isStillMediaPath, pickDefaultSwapTarget } from "./submitFamily";
import { queryKeys } from "./queryKeys";
import type {
  DiscoveryAssetLineageItemSummary,
  DiscoveryLibraryItem,
  ShapeFactoryMapQueueOverrides,
  WorkItem,
  WorkProductBinding,
  WorkProductDetailRow,
  WorkProductFamilyOption,
  WorkProductItem,
  WorkProductLoraEntry,
  WorkProductParamsProfile,
  WorkProductParamsValues,
  WorkProductPromptProfile,
  WorkProductShapeProfile,
  WorkProductShapeSlot,
} from "./types";

type RowLayout = "stacked" | "split";

const LAYOUT_KEY = "work-products-row-layout";
const SORT_KEY = "work-products-sort";
const SECTION_OPEN_KEY = "work-products-section-open-v3";
const HOURLY_ONLY_KEY = "work-products-hourly-only";
const STATUS_FILTER_OFF_KEY = "work-products-status-filter-off";
const MARKER_FILTER_OFF_KEY = "work-products-marker-filter-off";
const DECODE_VAE_FILTER_KEY = "work-products-decode-vae-filter";
const CHROME_KEY = "work-products-chrome-v2";

type WorkProductSort = "created_desc" | "created_asc" | "family_asc" | "family_desc" | "status" | "pick_mode";
type DecodeVaeFilter = "all" | "tiled" | "plain";

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

/** Property panels start collapsed; summary line stays visible on the header. */
const DEFAULT_SECTION_OPEN: Record<string, boolean> = {
  prompt: false,
  run: false,
  timing: false,
  plan: false,
  appetite: false,
  lineage: false,
  shape: false,
  bindings: false,
  trim: false,
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

function loadChrome(): { tools: boolean; filters: boolean } {
  try {
    const raw = localStorage.getItem(CHROME_KEY);
    if (!raw) return { tools: false, filters: false };
    const parsed = JSON.parse(raw) as { tools?: unknown; filters?: unknown };
    return {
      tools: parsed.tools === true,
      filters: parsed.filters === true,
    };
  } catch {
    return { tools: false, filters: false };
  }
}

function persistChrome(next: { tools: boolean; filters: boolean }) {
  try {
    localStorage.setItem(CHROME_KEY, JSON.stringify(next));
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

function loadDecodeVaeFilter(): DecodeVaeFilter {
  try {
    const v = localStorage.getItem(DECODE_VAE_FILTER_KEY);
    if (v === "all" || v === "tiled" || v === "plain") return v;
  } catch {
    /* ignore */
  }
  return "all";
}

function persistDecodeVaeFilter(v: DecodeVaeFilter) {
  try {
    localStorage.setItem(DECODE_VAE_FILTER_KEY, v);
  } catch {
    /* ignore */
  }
}

function workProductDecodeVae(item: WorkProductItem): string | null {
  const raw = item.markers?.["decode.vae"];
  if (raw == null) return null;
  const v = String(raw).toLowerCase().trim();
  return v || null;
}

function filterWorkProductsByDecodeVae(
  items: WorkProductItem[],
  filter: DecodeVaeFilter,
): WorkProductItem[] {
  if (filter === "all") return items;
  return items.filter((it) => workProductDecodeVae(it) === filter);
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
  return !s || s === "queued" || s === "pending" || s === "editing" || s === "running" || s === "submitted";
}

/**
 * Trim in/out is editable while the job is still ours (pending / editing / not on Comfy).
 * Edits patch that job's VHS window before submit. Locked once Comfy has
 * accepted it (queued or running). Completed cards keep next-action trim
 * editable for Extend / Vary / Derive / Re-run planning (sidecar only).
 */
function isJobTrimEditable(item: WorkProductItem): boolean {
  if (item.output_url) return true;
  const s = workProductStatusKey(item);
  // Explicitly on Comfy's waiting or running list — baked prompt, no edits.
  if (s === "queued" || s === "running") return false;
  // Still pre-Comfy: pending/editing/draft/deposited, or any status without a prompt_id.
  if (s === "pending" || s === "editing" || s === "draft" || s === "deposited") return true;
  if (!String(item.prompt_id || "").trim()) return true;
  // Has a prompt_id under another in-flight label (e.g. submitted) — treat as locked.
  return false;
}

/** Open Submit edit-in-place for a pre-run factory job. */
function canEditJobViaSubmit(item: WorkProductItem): boolean {
  if (isNonFactoryWorkProduct(item)) return false;
  if (!String(item.job_key || "").trim()) return false;
  if (item.output_url) return false;
  const s = workProductStatusKey(item);
  if (s === "running" || s === "complete" || s === "completed") return false;
  if (s === "error" || s === "failed" || s === "interrupted" || s === "abandoned") return false;
  return s === "pending" || s === "editing" || s === "queued" || s === "submitted" || !String(item.prompt_id || "").trim();
}

/** Pending factory jobs: trim edits rewrite the job workflow (not just the sidecar). */
function canUpdatePendingJobTrim(item: WorkProductItem): boolean {
  if (isNonFactoryWorkProduct(item)) return false;
  if (item.output_url) return false;
  if (!String(item.job_key || "").trim() && !String(item.job_path || "").trim()) return false;
  return isJobTrimEditable(item);
}

/** Promote writes the job's prompt into the family library — only after results exist. */
function canPromoteJobPrompt(item: WorkProductItem): boolean {
  if (isNonFactoryWorkProduct(item)) return false;
  if (!String(item.job_key || "").trim() && !String(item.job_path || "").trim()) return false;
  return Boolean(item.output_url || String(item.output_relpath || "").trim());
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

/** Queued / pending jobs can be retargeted: replay as another family, then retire the old one. */
function canSwapFamilyWorkProduct(item: WorkProductItem): boolean {
  if (!String(item.job_key || "").trim()) return false;
  if (isNonFactoryWorkProduct(item)) return false;
  const s = workProductStatusKey(item);
  return s === "queued" || s === "pending" || s === "editing" || s === "submitted";
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
  const parts: string[] = [
    item.family_slug || "",
    item.job_key || "",
    item.status || "",
    item.prompt_id || "",
    item.output_relpath || "",
    item.parent_output_relpath || "",
    item.parent_output || "",
  ];
  const bindings = item.bindings;
  if (bindings && typeof bindings === "object") {
    for (const b of Object.values(bindings)) {
      if (!b || typeof b !== "object") continue;
      parts.push(b.relpath || "", b.basename || "", b.path || "");
    }
  }
  return parts.filter(Boolean).join(" ").toLowerCase();
}

function filterWorkProductsByName(items: WorkProductItem[], query: string): WorkProductItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  return items.filter((it) => isLivePreviewItem(it) || workProductNameHaystack(it).includes(q));
}

function workProductMatchesMedia(item: WorkProductItem, media: string): boolean {
  const needle = media.trim().toLowerCase().replace(/\\/g, "/").replace(/^\/+/, "");
  if (!needle) return false;
  const hay = workProductNameHaystack(item);
  if (hay.includes(needle)) return true;
  const base = needle.split("/").pop() || needle;
  if (base && base !== needle && hay.includes(base.toLowerCase())) return true;
  const stem = base.includes(".") ? base.replace(/\.[^.]+$/, "") : "";
  if (stem && hay.includes(stem.toLowerCase())) return true;
  return false;
}

function filterWorkProductsByMedia(items: WorkProductItem[], media: string | null): WorkProductItem[] {
  const m = String(media || "").trim();
  if (!m) return items;
  return items.filter((it) => isLivePreviewItem(it) || workProductMatchesMedia(it, m));
}

function statusFilterVisual(status: string): string {
  const s = status.toLowerCase();
  if (s === "running") return "running";
  if (s === "queued" || s === "submitted") return "queued";
  if (s === "editing") return "editing";
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

function formatRelativeAge(iso?: string | null): string {
  if (!iso) return "unknown";
  try {
    const t = new Date(iso).getTime();
    if (!Number.isFinite(t)) return "unknown";
    const delta = Date.now() - t;
    if (!Number.isFinite(delta)) return "unknown";
    const absMs = Math.abs(delta);
    const dir = delta >= 0 ? "ago" : "from now";
    const sec = Math.round(absMs / 1000);
    if (sec < 60) return `${sec}s ${dir}`;
    const min = Math.round(sec / 60);
    if (min < 60) return `${min}m ${dir}`;
    const hr = Math.round(min / 60);
    if (hr < 48) return `${hr}h ${dir}`;
    const day = Math.round(hr / 24);
    return `${day}d ${dir}`;
  } catch {
    return "unknown";
  }
}

function FlowEventTimeline({ item }: { item: WorkProductItem }) {
  const events = Array.isArray(item.flow_events) ? item.flow_events : [];
  if (!events.length) return null;
  const recent = events.slice(-6).reverse();
  return (
    <section className="work-product-flow" aria-label="Remediation timeline">
      <header className="work-product-flow__head">
        <span className="work-product-quick-queue__label">Remediation timeline</span>
        <span className="work-product-flow__count">{events.length} event{events.length === 1 ? "" : "s"}</span>
      </header>
      <ul className="work-product-flow__list">
        {recent.map((ev, idx) => {
          const action = String(ev?.action || "action").trim();
          const actor = String(ev?.actor || "operator").trim();
          const source = String(ev?.source_surface || "api").trim();
          const reason = String(ev?.reason || "").trim();
          const at = String(ev?.at || "").trim();
          const ok = ev?.ok === false ? "failed" : "ok";
          const when = formatWhen(at || null);
          const age = formatRelativeAge(at || null);
          return (
            <li key={`${at}:${action}:${idx}`} className="work-product-flow__item">
              <div className="work-product-flow__title">
                <span className={`work-product-badge ${ok === "failed" ? "work-product-badge--bad" : "work-product-badge--ok"}`}>
                  {ok}
                </span>
                <span className="work-product-flow__action">{action}</span>
                <span className="work-product-flow__meta">
                  by {actor} @ {source}
                </span>
              </div>
              <div className="work-product-flow__time" title={when}>
                {age}
              </div>
              {reason ? <div className="work-product-flow__reason">{reason}</div> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
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
  if (k === "running") return "work-product-badge--running";
  if (k === "queued" || k === "submitted") return "work-product-badge--queued";
  if (k === "editing") return "work-product-badge--editing";
  if (k === "complete" || k === "deposited") return "work-product-badge--ok";
  if (k === "pending" || k === "draft" || k === "normal" || k === "later") return "work-product-badge--pending";
  if (k === "interrupted") return "work-product-badge--interrupted";
  if (k === "unknown" || k === "abandoned") return "work-product-badge--muted";
  if (k === "failed" || k === "error") return "work-product-badge--bad";
  return "";
}

function workbenchSourceBinding(item: WorkProductItem): WorkProductBinding | null {
  // Still-source families (e.g. BounceDanceA) bind `source_still`, not `source_image`.
  return (
    item.bindings?.source_video ||
    item.bindings?.source_image ||
    item.bindings?.source_still ||
    item.bindings?.identity_still ||
    item.bindings?.identity_anchor ||
    item.bindings?.start_image ||
    null
  );
}

/** Job output the Workbench strip and list badge both rate. Never the source still. */
function workbenchJobAppetiteRelpath(item: WorkProductItem): string | null {
  const out = String(item.output_relpath || "").trim();
  return out || null;
}

/** Relpath for advancing / submitting from this job's input (not its output). */
function workbenchSourceMediaRelpath(item: WorkProductItem): string | null {
  const source = workbenchSourceBinding(item);
  let rel = String(source?.relpath || item.parent_output_relpath || "").trim().replace(/\\/g, "/");
  if (!rel) return null;
  if (isStillMediaPath(rel) && !rel.includes("/") && !rel.toLowerCase().startsWith("input/")) {
    rel = `input/${rel}`;
  }
  return rel;
}

function sourcePreviewUrls(item: WorkProductItem): { thumb: string | null; video: string | null; label: string } {
  const source = workbenchSourceBinding(item);
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
            preload="none"
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
        <AppetitePreviewBadge relpath={workbenchSourceMediaRelpath(item) || item.output_relpath} />
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

function workbenchJobDomId(jobKey: string | null | undefined): string {
  return `workbench-job-${String(jobKey || "").replace(/[^\w.-]+/g, "_")}`;
}

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

function trimHasMarks(state: Pick<InputTrimState, "markIn" | "markOut">): boolean {
  return state.markIn != null || state.markOut != null;
}

function readMediaDuration(el: HTMLVideoElement | null | undefined, fallback = 0): number {
  const d = el?.duration;
  if (Number.isFinite(d) && (d as number) > 0) return d as number;
  return fallback > 0 ? fallback : 0;
}

async function preferredWorkbenchTrim(opts: {
  rel: string | null;
  legacyKey: string;
  defaults: { skip_first_frames: number; frame_load_cap: number };
  durationHint: number;
  fps: number;
  seedFromAppliedVhs: boolean;
}): Promise<{
  markIn: number | null;
  markOut: number | null;
  dirty: boolean;
  warning: string | null;
  clampedDefault: boolean;
  clip: ShapeFactoryClip | null;
}> {
  if (opts.rel) {
    const saved = await loadDiscoveryTrimAsync(TRIM_CONTEXT_WORK_PRODUCTS, opts.rel, opts.legacyKey);
    if (saved) {
      return {
        markIn: saved.in,
        markOut: saved.out,
        dirty: true,
        warning: null,
        clampedDefault: false,
        clip: null,
      };
    }
    try {
      const def = pickDefaultClip(await loadClipsForMedia(opts.rel));
      if (def) {
        return {
          markIn: def.mark_in_s,
          markOut: def.mark_out_s,
          dirty: false,
          warning: null,
          clampedDefault: false,
          clip: def,
        };
      }
    } catch {
      /* clips are optional — fall through */
    }
  }
  const nontrivial =
    Number(opts.defaults.skip_first_frames || 0) > 0 || Number(opts.defaults.frame_load_cap || 0) > 0;
  if (opts.seedFromAppliedVhs && nontrivial && opts.durationHint > 0) {
    const seeded = vhsDefaultsToMarks(opts.defaults, opts.durationHint, opts.fps);
    return {
      markIn: seeded.markIn,
      markOut: seeded.markOut,
      dirty: false,
      warning: seeded.warning,
      clampedDefault: seeded.clamped,
      clip: null,
    };
  }
  return {
    markIn: null,
    markOut: null,
    dirty: false,
    warning: null,
    clampedDefault: false,
    clip: null,
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
  onOutputTrimChange: React.Dispatch<React.SetStateAction<InputTrimState>>;
  onSourceTrimChange: React.Dispatch<React.SetStateAction<InputTrimState>>;
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
  const [outputClipId, setOutputClipId] = useState<string | null>(null);
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
  const pendingJobTrim = canUpdatePendingJobTrim(item);
  const pendingTrimTimerRef = useRef<number | null>(null);
  const queryClient = useQueryClient();
  const pendingTrimMutation = useMutation({
    mutationFn: updatePendingShapeFactoryTrim,
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.shapeFactory.workProductsRoot,
      }),
  });

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
      void pendingTrimMutation.mutateAsync({
        job_key: String(item.job_key || "").trim() || undefined,
        job_path: String(item.job_path || "").trim() || undefined,
        skip_first_frames: win.skip_first_frames,
        frame_load_cap: win.frame_load_cap,
        mark_in: state.markIn,
        mark_out: state.markOut,
        actor: "operator",
        reason: "trim_adjustment",
        source_surface: "workbench",
      }).catch((err) => {
        console.warn("update-pending-trim failed", err);
      });
    }, 450);
  };

  useEffect(() => {
    const outDur = readMediaDuration(outputVideoRef.current, 0);
    if (outDur > 0) {
      onOutputTrimChange((prev) =>
        Math.abs(prev.duration - outDur) > 0.05 ? { ...prev, duration: outDur } : prev,
      );
    }
    const srcDur = readMediaDuration(sourceVideoRef.current, 0);
    if (srcDur > 0) {
      onSourceTrimChange((prev) =>
        Math.abs(prev.duration - srcDur) > 0.05 ? { ...prev, duration: srcDur } : prev,
      );
    }
  }, [videoUrl, sourceUrl, queuedSourcePlayUrl, onOutputTrimChange, onSourceTrimChange]);

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
      apply: React.Dispatch<React.SetStateAction<InputTrimState>>,
      key: string,
      durationHint: number,
      seedFromAppliedVhs: boolean,
      onClip: (clip: ShapeFactoryClip | null) => void,
    ) => {
      const pref = await preferredWorkbenchTrim({
        rel,
        legacyKey: key,
        defaults,
        durationHint,
        fps,
        seedFromAppliedVhs,
      });
      if (cancelled) return;
      apply((prev) => {
        if (prev.dirty || trimHasMarks(prev)) {
          const d = durationHint > 0 ? durationHint : prev.duration;
          return d > 0 && Math.abs(prev.duration - d) > 0.05 ? { ...prev, duration: d } : prev;
        }
        return {
          markIn: pref.markIn,
          markOut: pref.markOut,
          dirty: pref.dirty,
          duration: durationHint,
          fps,
          warning: pref.warning,
          clampedDefault: pref.clampedDefault,
        };
      });
      if (pref.clip) onClip(pref.clip);
    };
    void boot(
      outputRel,
      outputDefaults,
      onOutputTrimChange,
      `wp-out:${item.job_key}`,
      Number(item.media_meta?.duration) || 0,
      false,
      (clip) => setOutputClipId(clip.clip_id),
    );
    void boot(
      queuedSourceRel || sourceRel,
      sourceDefaults,
      onSourceTrimChange,
      `wp-src:${item.job_key}`,
      0,
      true,
      (clip) => onSelectClip?.(clip),
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
    apply: React.Dispatch<React.SetStateAction<InputTrimState>>,
    rel: string | null,
    legacyKey: string,
    seedFromAppliedVhs: boolean,
    onClip?: (clip: ShapeFactoryClip) => void,
  ) => {
    const duration = readMediaDuration(el, 0);
    if (!(duration > 0)) return;
    apply((current) => {
      if (current.dirty || trimHasMarks(current)) {
        return Math.abs(current.duration - duration) > 0.05 ? { ...current, duration } : current;
      }
      return current.duration > 0 && Math.abs(current.duration - duration) <= 0.05
        ? current
        : { ...current, duration };
    });
    void preferredWorkbenchTrim({
      rel,
      legacyKey,
      defaults,
      durationHint: duration,
      fps,
      seedFromAppliedVhs,
    }).then((pref) => {
      apply((current) => {
        if (current.dirty || trimHasMarks(current)) {
          return Math.abs(current.duration - duration) > 0.05 ? { ...current, duration } : current;
        }
        return {
          markIn: pref.markIn,
          markOut: pref.markOut,
          dirty: pref.dirty,
          duration,
          fps,
          warning: pref.warning,
          clampedDefault: pref.clampedDefault,
        };
      });
      if (pref.clip) onClip?.(pref.clip);
    });
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
    if (next.dirty || trimHasMarks(next)) {
      void persistDiscoveryTrimAsync({
        context: TRIM_CONTEXT_WORK_PRODUCTS,
        mediaRelpath: rel,
        legacyAssetKey: legacyKey,
        markIn: next.markIn,
        markOut: next.markOut,
        duration: next.duration,
      });
    }
    if (opts?.updatePendingJob && pendingJobTrim && opts.defaults) {
      schedulePendingJobTrimUpdate(next, opts.defaults);
    }
  };

  const applyDefaultSourceClip = (clip: ShapeFactoryClip) => {
    if (!selectedClipId) onSelectClip?.(clip);
    onSourceTrimChange((prev) => {
      if (prev.dirty || trimHasMarks(prev)) return prev;
      return {
        ...prev,
        markIn: clip.mark_in_s,
        markOut: clip.mark_out_s,
        dirty: false,
        warning: null,
        clampedDefault: false,
      };
    });
  };

  return (
    <div className="work-product-viewer">
      <div className="work-product-viewer__main">
        {videoUrl ? (
          <>
            <AppetitePreviewFrame relpath={outputRel}>
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
                  onMeta(
                    e.currentTarget,
                    outputDefaults,
                    onOutputTrimChange,
                    outputRel,
                    `wp-out:${item.job_key}`,
                    false,
                    (clip) => setOutputClipId(clip.clip_id),
                  )
                }
                onLoadedData={(e) =>
                  onMeta(
                    e.currentTarget,
                    outputDefaults,
                    onOutputTrimChange,
                    outputRel,
                    `wp-out:${item.job_key}`,
                    false,
                    (clip) => setOutputClipId(clip.clip_id),
                  )
                }
              />
            </AppetitePreviewFrame>
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
                onOutputTrimChange((prev) => {
                  const next = {
                    ...prev,
                    markIn: v,
                    dirty: true,
                    warning: null,
                    clampedDefault: false,
                    duration: readMediaDuration(outputVideoRef.current, prev.duration),
                  };
                  persistTrim(next, outputRel, `wp-out:${item.job_key}`);
                  return next;
                });
              }}
              onMarkOutChange={(v) => {
                onOutputTrimChange((prev) => {
                  const next = {
                    ...prev,
                    markOut: v,
                    dirty: true,
                    warning: null,
                    clampedDefault: false,
                    duration: readMediaDuration(outputVideoRef.current, prev.duration),
                  };
                  persistTrim(next, outputRel, `wp-out:${item.job_key}`);
                  return next;
                });
              }}
              onModeChange={setOutputMode}
              onClear={() => {
                const next: InputTrimState = {
                  markIn: null,
                  markOut: null,
                  dirty: false,
                  duration: outputTrim.duration,
                  fps,
                  warning: null,
                  clampedDefault: false,
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
            <ClipBookmarksRail
              mediaRelpath={outputRel}
              duration={outputTrim.duration}
              markIn={outputTrim.markIn}
              markOut={outputTrim.markOut}
              trimEditable
              origin="workbench"
              selectedClipId={outputClipId}
              onSelectClip={(clip) => setOutputClipId(clip?.clip_id || null)}
              onDefaultClip={(clip) => {
                setOutputClipId((id) => id || clip.clip_id);
                onOutputTrimChange((prev) => {
                  if (prev.dirty || trimHasMarks(prev)) return prev;
                  return {
                    ...prev,
                    markIn: clip.mark_in_s,
                    markOut: clip.mark_out_s,
                    dirty: false,
                    warning: null,
                    clampedDefault: false,
                  };
                });
              }}
              onApplyClip={(mi, mo, clip) => {
                onOutputTrimChange((prev) => {
                  const next = {
                    ...prev,
                    markIn: mi,
                    markOut: mo,
                    dirty: true,
                    warning: null,
                    clampedDefault: false,
                    duration: readMediaDuration(outputVideoRef.current, prev.duration),
                  };
                  persistTrim(next, outputRel, `wp-out:${item.job_key}`);
                  return next;
                });
                setOutputClipId(clip?.clip_id || null);
              }}
            />
          </>
        ) : showRunningLive ? (
          <div className="work-product-viewer__live-plus-source">
            <ComfyLivePreview
              className="work-product-viewer__live-primary"
              promptId={promptId}
              submittedAt={item.submitted_at || item.created_at}
              showMetrics={false}
              appetiteRelpath={outputRel || workbenchSourceMediaRelpath(item)}
            />
            {previewUrls.thumb ? (
              <div className="work-product-viewer__live-source" title={previewUrls.label}>
                <img
                  className="work-product-viewer__live-source-img"
                  src={previewUrls.thumb}
                  alt={previewUrls.label || "source"}
                />
                <span className="work-product-live__badge work-product-live__badge--queued">source</span>
                <AppetitePreviewBadge relpath={workbenchSourceMediaRelpath(item)} />
              </div>
            ) : null}
          </div>
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
                    onSourceTrimChange,
                    queuedSourceRel,
                    `wp-src:${item.job_key}`,
                    true,
                    (clip) => onSelectClip?.(clip),
                  )
                }
                onLoadedData={(e) =>
                  onMeta(
                    e.currentTarget,
                    sourceDefaults,
                    onSourceTrimChange,
                    queuedSourceRel,
                    `wp-src:${item.job_key}`,
                    true,
                    (clip) => onSelectClip?.(clip),
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
              <AppetitePreviewBadge relpath={queuedSourceRel} />
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
              onSeek={setSourceTime}
              onSyncTime={setSourceTime}
              onMarkInChange={(v) => {
                onSourceTrimChange((prev) => {
                  const next = {
                    ...prev,
                    markIn: v,
                    dirty: true,
                    warning: null,
                    clampedDefault: false,
                    duration: readMediaDuration(sourceVideoRef.current, prev.duration),
                  };
                  persistTrim(next, queuedSourceRel, `wp-src:${item.job_key}`, {
                    updatePendingJob: pendingJobTrim,
                    defaults: sourceDefaults,
                  });
                  return next;
                });
              }}
              onMarkOutChange={(v) => {
                onSourceTrimChange((prev) => {
                  const next = {
                    ...prev,
                    markOut: v,
                    dirty: true,
                    warning: null,
                    clampedDefault: false,
                    duration: readMediaDuration(sourceVideoRef.current, prev.duration),
                  };
                  persistTrim(next, queuedSourceRel, `wp-src:${item.job_key}`, {
                    updatePendingJob: pendingJobTrim,
                    defaults: sourceDefaults,
                  });
                  return next;
                });
              }}
              onModeChange={setSourceMode}
              onClear={() => {
                const next: InputTrimState = {
                  markIn: null,
                  markOut: null,
                  dirty: false,
                  duration: sourceTrim.duration,
                  fps,
                  warning: null,
                  clampedDefault: false,
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
              trimEditable
              origin="workbench"
              selectedClipId={selectedClipId}
              onSelectClip={onSelectClip}
              onUseForExtend={onUseForExtend}
              onDefaultClip={applyDefaultSourceClip}
              onApplyClip={(mi, mo, clip) => {
                onSourceTrimChange((prev) => {
                  const next = {
                    ...prev,
                    markIn: mi,
                    markOut: mo,
                    dirty: true,
                    warning: null,
                    clampedDefault: false,
                    duration: readMediaDuration(sourceVideoRef.current, prev.duration),
                  };
                  persistTrim(next, queuedSourceRel, `wp-src:${item.job_key}`, {
                    updatePendingJob: pendingJobTrim,
                    defaults: sourceDefaults,
                  });
                  return next;
                });
                onSelectClip?.(clip || null);
              }}
            />
          </div>
        ) : showSourceThumb ? (
          <WorkProductSourceThumbPreview item={item} />
        ) : thumbUrl ? (
          <AppetitePreviewFrame relpath={outputRel}>
            <img className="work-product-viewer__img" src={thumbUrl} alt={item.job_key} />
          </AppetitePreviewFrame>
        ) : (
          <div className="work-product-viewer__empty">No output yet ({item.status || "pending"})</div>
        )}
      </div>
      {(sourceUrl || sourceThumb) && !showSourceThumb && !queuedSourcePlayUrl && !showRunningLive && (
        <div className="work-product-viewer__source" title={source?.basename || "source"}>
          {sourceUrl ? (
            <>
              <AppetitePreviewFrame relpath={sourceRel}>
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
                  onMeta(
                    e.currentTarget,
                    sourceDefaults,
                    onSourceTrimChange,
                    sourceRel,
                    `wp-src:${item.job_key}`,
                    true,
                    (clip) => onSelectClip?.(clip),
                  )
                }
                onLoadedData={(e) =>
                  onMeta(
                    e.currentTarget,
                    sourceDefaults,
                    onSourceTrimChange,
                    sourceRel,
                    `wp-src:${item.job_key}`,
                    true,
                    (clip) => onSelectClip?.(clip),
                  )
                }
                />
              </AppetitePreviewFrame>
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
                onSeek={setSourceTime}
                onSyncTime={setSourceTime}
                onMarkInChange={(v) => {
                  onSourceTrimChange((prev) => {
                    const next = {
                      ...prev,
                      markIn: v,
                      dirty: true,
                      warning: null,
                      clampedDefault: false,
                      duration: readMediaDuration(sourceVideoRef.current, prev.duration),
                    };
                    persistTrim(next, sourceRel, `wp-src:${item.job_key}`, {
                      updatePendingJob: pendingJobTrim,
                      defaults: sourceDefaults,
                    });
                    return next;
                  });
                }}
                onMarkOutChange={(v) => {
                  onSourceTrimChange((prev) => {
                    const next = {
                      ...prev,
                      markOut: v,
                      dirty: true,
                      warning: null,
                      clampedDefault: false,
                      duration: readMediaDuration(sourceVideoRef.current, prev.duration),
                    };
                    persistTrim(next, sourceRel, `wp-src:${item.job_key}`, {
                      updatePendingJob: pendingJobTrim,
                      defaults: sourceDefaults,
                    });
                    return next;
                  });
                }}
                onModeChange={setSourceMode}
                onClear={() => {
                  const next: InputTrimState = {
                    markIn: null,
                    markOut: null,
                    dirty: false,
                    duration: sourceTrim.duration,
                    fps,
                    warning: null,
                    clampedDefault: false,
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
                trimEditable
                origin="workbench"
                selectedClipId={selectedClipId}
                onSelectClip={onSelectClip}
                onUseForExtend={onUseForExtend}
                onDefaultClip={applyDefaultSourceClip}
                onApplyClip={(mi, mo, clip) => {
                  onSourceTrimChange((prev) => {
                    const next = {
                      ...prev,
                      markIn: mi,
                      markOut: mo,
                      dirty: true,
                      warning: null,
                      clampedDefault: false,
                      duration: readMediaDuration(sourceVideoRef.current, prev.duration),
                    };
                    persistTrim(next, sourceRel, `wp-src:${item.job_key}`, {
                      updatePendingJob: pendingJobTrim,
                      defaults: sourceDefaults,
                    });
                    return next;
                  });
                  onSelectClip?.(clip || null);
                }}
              />
            </>
          ) : sourceThumb ? (
            <AppetitePreviewFrame relpath={sourceRel}>
              <img className="work-product-viewer__source-img" src={sourceThumb} alt={source?.basename || "source"} />
            </AppetitePreviewFrame>
          ) : null}
        </div>
      )}
    </div>
  );
}

type PeekPos = { top: number; left: number; maxHeight: number };


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
  const vocabBits = [shape.io_class, shape.chain_role].filter(Boolean).join(" · ");
  const metaBits = [
    shape.family_slug ? `family ${shape.family_slug}` : "",
    vocabBits ? `vocab ${vocabBits}` : "",
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

function filesHrefForRelpath(relpath: string): string {
  const norm = relpath.replace(/^\/+/, "").replace(/\\/g, "/");
  return "/files/" + norm.split("/").map(encodeURIComponent).join("/");
}

/** Deep link for a binding asset: Discovery / Stills gallery / raw file. */
function bindingAssetHref(row: WorkProductDetailRow): string | null {
  const rel = String(row.relpath || "").trim().replace(/^\/+/, "").replace(/\\/g, "/");
  const asset = String(row.asset_url || "").trim();
  if (rel) {
    if (/^(og|wip)\//i.test(rel)) {
      return discoveryLibraryHref(rel);
    }
    if (/^output\//i.test(rel) || /\.mp4($|\?)/i.test(rel)) {
      return discoveryLibraryHref(rel);
    }
    if (/^input\//i.test(rel) || /\.(jpe?g|png|webp|gif)($|\?)/i.test(rel)) {
      const base = rel.split("/").pop() || rel;
      return stillsHref({
        contentId: extractContentIdFromName(base),
        relpath: rel.startsWith("input/") ? rel : `input/${base}`,
        q: base,
      });
    }
    return filesHrefForRelpath(rel);
  }
  return asset || null;
}

function bindingOpenLabel(href: string | null): string {
  if (!href) return "Open asset";
  if (href.startsWith("/discovery/stills")) return "Open in Stills";
  if (href.startsWith("/discovery")) return "Open in Library";
  if (href.startsWith("/files/")) return "Open file";
  return "Open asset";
}

function BindingDetailValue({ row }: { row: WorkProductDetailRow }) {
  const thumb = String(row.thumb_url || "").trim() || null;
  const href = bindingAssetHref(row);
  const label = enrichRoleMentions(row.value);
  const openLabel = bindingOpenLabel(href);
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
            {openLabel}
            {row.relpath ? ` · ${row.relpath}` : ""}
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
  "Seed",
  "Seed mode",
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
  // Prompt positive/negative live in WorkProductPromptEditor — do not duplicate here.
  {
    id: "run",
    title: "Run",
    kv: true,
    labels: [
      "Created",
      "Seed",
      "Seed mode",
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

function rowVal(
  rows: WorkProductDetailRow[],
  ...labels: string[]
): string | null {
  for (const label of labels) {
    const hit = rows.find((r) => r.label === label);
    if (hit && String(hit.value || "").trim()) return String(hit.value).trim();
  }
  return null;
}

function truncateSummary(text: string, max = 48): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

function BindingGroupSummaryLinks({
  rows,
  bindings,
}: {
  rows: WorkProductDetailRow[];
  bindings?: Record<string, WorkProductBinding> | null;
}) {
  const items = rows
    .filter((r) => r.label.startsWith("Binding · "))
    .map((r) => {
      const enriched = enrichBindingDetailRow(r, bindings);
      const slot = r.label.slice("Binding · ".length).trim() || "binding";
      const href = bindingAssetHref(enriched);
      const base =
        String(enriched.relpath || "")
          .split("/")
          .pop() || truncateSummary(enriched.value, 28);
      return { slot, href, base, key: `${slot}:${enriched.relpath || enriched.value}` };
    });
  if (!items.length) return <span className="work-product-details__group-summary-muted">none</span>;
  return (
    <span className="work-product-details__group-summary-bindings">
      {items.map((it) =>
        it.href ? (
          <a
            key={it.key}
            className="work-product-details__group-summary-link"
            href={it.href}
            title={bindingOpenLabel(it.href)}
            onClick={(e) => e.stopPropagation()}
          >
            {it.slot}
          </a>
        ) : (
          <span key={it.key} className="work-product-details__group-summary-muted" title={it.base}>
            {it.slot}
          </span>
        ),
      )}
    </span>
  );
}

function detailGroupSummary(
  group: {
    id: string;
    rows: WorkProductDetailRow[];
    compact?: { rows: WorkProductDetailRow[] };
  },
  item: WorkProductItem,
): React.ReactNode {
  const rows = [...group.rows, ...(group.compact?.rows || [])];
  if (group.id === "bindings") {
    return <BindingGroupSummaryLinks rows={group.rows} bindings={item.bindings} />;
  }
  if (group.id === "run") {
    const bits = [
      rowVal(rows, "Seed") ? `seed ${rowVal(rows, "Seed")}` : null,
      rowVal(rows, "Job key") ? truncateSummary(String(rowVal(rows, "Job key")), 28) : null,
      rowVal(rows, "Created"),
    ].filter(Boolean);
    return bits.length ? bits.join(" · ") : `${rows.length} field${rows.length === 1 ? "" : "s"}`;
  }
  if (group.id === "timing") {
    const exec = rowVal(rows, "Exec", "Exec sec");
    const wall = rowVal(rows, "Wall sec");
    const bits = [exec ? `exec ${exec}` : null, wall ? `wall ${wall}` : null].filter(Boolean);
    return bits.length ? bits.join(" · ") : `${rows.length} field${rows.length === 1 ? "" : "s"}`;
  }
  if (group.id === "shape") {
    const bits = [
      rowVal(rows, "Template"),
      rowVal(rows, "Shape", "Shape path"),
    ].filter(Boolean);
    return bits.length ? bits.map((b) => truncateSummary(String(b), 36)).join(" · ") : `${rows.length} field${rows.length === 1 ? "" : "s"}`;
  }
  if (group.id === "plan") {
    const bits = [
      rowVal(rows, "Derive action"),
      rowVal(rows, "Plan source tag"),
      rowVal(rows, "Parent output") ? `parent ${truncateSummary(String(rowVal(rows, "Parent output")), 24)}` : null,
    ].filter(Boolean);
    return bits.length ? bits.join(" · ") : `${rows.length} field${rows.length === 1 ? "" : "s"}`;
  }
  if (group.id === "trim") {
    const skip = rowVal(rows, "VHS skip_first_frames", "skip_first_frames");
    const cap = rowVal(rows, "VHS frame_load_cap", "frame_load_cap");
    const bits = [skip != null ? `skip ${skip}` : null, cap != null ? `cap ${cap}` : null].filter(Boolean);
    return bits.length ? bits.join(" · ") : `${rows.length} field${rows.length === 1 ? "" : "s"}`;
  }
  if (group.id === "appetite") {
    const bits = [
      rowVal(rows, "Appetite", "Appetite value"),
      rowVal(rows, "Appetite facet"),
      rowVal(rows, "Hold axis"),
    ].filter(Boolean);
    return bits.length ? bits.join(" · ") : `${rows.length} field${rows.length === 1 ? "" : "s"}`;
  }
  if (!rows.length) return null;
  const first = rows[0];
  return truncateSummary(`${first.label}: ${first.value}`, 56);
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
  Seed: "Seed",
  "Seed mode": "Seed mode",
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

function shortContentHash(hash?: string | null): string {
  const h = String(hash || "").trim();
  return h ? h.slice(0, 10) : "";
}

function WorkProductPromptEditor({
  item,
  onCommitted,
}: {
  item: WorkProductItem;
  onCommitted?: () => void;
}) {
  const prompt = item.prompt_profile;
  const queryClient = useQueryClient();
  const editable = isJobTrimEditable(item) && !Boolean(prompt?.frozen);
  const [editing, setEditing] = useState(false);
  const [rawMode, setRawMode] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [positive, setPositive] = useState(() => String(prompt?.positive || ""));
  const [negative, setNegative] = useState(() => String(prompt?.negative || ""));
  const [posChunks, setPosChunks] = useState(() =>
    clonePromptRows(prompt?.positive_rows, prompt?.positive || undefined),
  );
  const [negChunks, setNegChunks] = useState(() =>
    clonePromptRows(prompt?.negative_rows, prompt?.negative || undefined),
  );
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promoteMode, setPromoteMode] = useState<"fork" | "overwrite">("fork");
  const [promoteLabel, setPromoteLabel] = useState("");
  const [promoteNote, setPromoteNote] = useState("");

  useEffect(() => {
    setPositive(String(prompt?.positive || ""));
    setNegative(String(prompt?.negative || ""));
    setPosChunks(clonePromptRows(prompt?.positive_rows, prompt?.positive || undefined));
    setNegChunks(clonePromptRows(prompt?.negative_rows, prompt?.negative || undefined));
    setDirty(false);
    setMsg(null);
    setEditing(false);
    setRawMode(false);
    setShowDiff(false);
  }, [item.job_key, prompt?.content_hash, prompt?.positive, prompt?.negative]);

  const saveMut = useMutation({
    mutationFn: updateShapeFactoryOwnedPrompt,
    onSuccess: async () => {
      setDirty(false);
      setEditing(false);
      setRawMode(false);
      setMsg("Saved to job");
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductsRoot });
      onCommitted?.();
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : String(e)),
  });
  const promoteMut = useMutation({
    mutationFn: promoteShapeFactoryTemplate,
    onSuccess: async (res) => {
      setPromoteOpen(false);
      setMsg(
        res.mode === "overwrite"
          ? `Overwrote catalog-default${res.bak_path ? " (bak kept)" : ""}`
          : `Forked library variant${res.path ? ` · ${String(res.path).split("/").pop()}` : ""}`,
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductsRoot });
      onCommitted?.();
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : String(e)),
  });

  if (!prompt && !editable) return null;

  const hash = shortContentHash(prompt?.content_hash);
  const sourceLabel = prompt?.label || prompt?.basename || "owned prompt";
  const busy = saveMut.isPending || promoteMut.isPending;
  const canPromote = canPromoteJobPrompt(item) && !editing;
  const canDiff = Boolean(prompt?.snowflake && prompt?.seed) && !editing;
  const posRows = prompt?.positive_rows || [];
  const negRows = prompt?.negative_rows || [];
  const posText = String(prompt?.positive || positive || "");
  const negText = String(prompt?.negative || negative || "");
  const hasNeg = negRows.length > 0 || Boolean(negText.trim()) || editing;
  const posSummary =
    (editing ? posChunks : posRows).length > 0
      ? `${(editing ? posChunks : posRows).length} part${(editing ? posChunks : posRows).length === 1 ? "" : "s"}`
      : posText.trim()
        ? `${posText.trim().length} chars`
        : "empty";
  const negSummary =
    (editing ? negChunks : negRows).length > 0
      ? `${(editing ? negChunks : negRows).length} part${(editing ? negChunks : negRows).length === 1 ? "" : "s"}`
      : negText.trim()
        ? `${negText.trim().length} chars`
        : "empty";

  const resetFromPrompt = () => {
    setPositive(String(prompt?.positive || ""));
    setNegative(String(prompt?.negative || ""));
    setPosChunks(clonePromptRows(prompt?.positive_rows, prompt?.positive || undefined));
    setNegChunks(clonePromptRows(prompt?.negative_rows, prompt?.negative || undefined));
    setDirty(false);
  };

  const save = () => {
    if (rawMode) {
      void saveMut.mutateAsync({
        job_key: item.job_key,
        job_path: item.job_path || undefined,
        positive,
        negative,
      });
      return;
    }
    void saveMut.mutateAsync({
      job_key: item.job_key,
      job_path: item.job_path || undefined,
      positive_rows: posChunks,
      negative_rows: negChunks,
    });
  };

  return (
    <div className="work-product-prompt-editor">
      <div className="work-product-prompt-editor__head">
        <span className="work-product-prompt-editor__title">Prompt</span>
        <span className="work-product-prompt-editor__name" title={prompt?.path || undefined}>
          {sourceLabel}
        </span>
        <span className="factory-muted work-product-prompt-editor__meta">
          {[
            hash || null,
            prompt?.frozen ? "frozen" : editable ? "editable" : null,
            prompt?.snowflake && prompt.seed
              ? `from ${prompt.seed.label || prompt.seed.basename || "seed"}`
              : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
        <PromptSnowflakeChip prompt={prompt} />
        <div className="work-product-prompt-editor__actions">
          {prompt?.path ? <JsonPeekButton path={prompt.path} label="raw json" /> : null}
          {canDiff ? (
            <button type="button" className="drt-btn" disabled={busy} onClick={() => setShowDiff((v) => !v)}>
              {showDiff ? "Hide diff" : "Show diff"}
            </button>
          ) : null}
          {editable ? (
            <button
              type="button"
              className="drt-btn"
              disabled={busy}
              onClick={() => {
                if (editing) resetFromPrompt();
                setEditing((v) => !v);
                setRawMode(false);
                setShowDiff(false);
                setPromoteOpen(false);
                setMsg(null);
              }}
            >
              {editing ? "Cancel edit" : "Edit"}
            </button>
          ) : null}
          {editing ? (
            <button
              type="button"
              className="drt-btn"
              disabled={busy}
              onClick={() => {
                if (!rawMode) {
                  setPositive(encodePromptRowsClient(posChunks));
                  setNegative(encodePromptRowsClient(negChunks));
                } else {
                  setPosChunks(rowsFromRawText(positive));
                  setNegChunks(rowsFromRawText(negative));
                }
                setRawMode((v) => !v);
              }}
            >
              {rawMode ? "Edit chunks" : "Edit raw"}
            </button>
          ) : null}
          {editing ? (
            <button type="button" className="drt-btn" disabled={!dirty || busy} onClick={() => save()}>
              Save to job
            </button>
          ) : null}
          {canPromote ? (
            <button
              type="button"
              className="drt-btn"
              disabled={busy || !(positive || negative || prompt)}
              title="Copy this job's prompt into the family library after judging the output"
              onClick={() => {
                setPromoteMode("fork");
                setPromoteLabel(
                  String(prompt?.label || item.family_slug || "variant").replace(/catalog-default/i, "variant"),
                );
                setPromoteNote("");
                setPromoteOpen(true);
              }}
            >
              Promote…
            </button>
          ) : null}
        </div>
      </div>

      <details className="work-product-prompt-editor__section">
        <summary className="work-product-prompt-editor__section-summary">
          <span>Positive</span>
          <span className="factory-muted">{posSummary}</span>
        </summary>
        <div className="work-product-prompt-editor__section-body">
          {editing && rawMode ? (
            <textarea
              className="work-product-prompt-editor__textarea"
              value={positive}
              disabled={busy}
              rows={6}
              onChange={(e) => {
                setPositive(e.target.value);
                setDirty(true);
              }}
            />
          ) : editing ? (
            <PromptChunkEditor
              rows={posChunks}
              disabled={busy}
              onChange={(next) => {
                setPosChunks(next);
                setDirty(true);
              }}
            />
          ) : prompt?.missing ? (
            <div className="work-product-prompt-table__empty">Prompt file missing</div>
          ) : prompt?.error ? (
            <div className="work-product-prompt-table__empty">{prompt.error}</div>
          ) : showDiff && canDiff ? (
            <PromptChunkDiff
              title=""
              seedRows={prompt?.seed?.positive_rows || []}
              jobRows={posRows}
            />
          ) : (
            <PromptMarkupTable title="" rows={posRows} fallbackText={posText} />
          )}
        </div>
      </details>

      {hasNeg ? (
        <details className="work-product-prompt-editor__section">
          <summary className="work-product-prompt-editor__section-summary">
            <span>Negative</span>
            <span className="factory-muted">{negSummary}</span>
          </summary>
          <div className="work-product-prompt-editor__section-body">
            {editing && rawMode ? (
              <textarea
                className="work-product-prompt-editor__textarea"
                value={negative}
                disabled={busy}
                rows={4}
                onChange={(e) => {
                  setNegative(e.target.value);
                  setDirty(true);
                }}
              />
            ) : editing ? (
              <PromptChunkEditor
                rows={negChunks}
                disabled={busy}
                onChange={(next) => {
                  setNegChunks(next);
                  setDirty(true);
                }}
              />
            ) : showDiff && canDiff ? (
              <PromptChunkDiff
                title=""
                seedRows={prompt?.seed?.negative_rows || []}
                jobRows={negRows}
              />
            ) : (
              <PromptMarkupTable title="" rows={negRows} fallbackText={negText} />
            )}
          </div>
        </details>
      ) : null}

      {msg ? <p className="work-product-prompt-editor__msg factory-muted">{msg}</p> : null}
      {promoteOpen && canPromote ? (
        <div className="work-product-prompt-editor__promote" role="dialog" aria-label="Promote prompt to library">
          <p className="factory-muted">
            After judging this output, write its prompt into the family library. Default is a new variant file
            (git-friendly); overwrite replaces catalog-default.json with a .bak.
          </p>
          <div className="work-product-prompt-editor__promote-modes" role="radiogroup" aria-label="Promote mode">
            <label>
              <input
                type="radio"
                name={`promote-mode-${item.job_key}`}
                checked={promoteMode === "fork"}
                onChange={() => setPromoteMode("fork")}
              />{" "}
              Save as new variant
            </label>
            <label>
              <input
                type="radio"
                name={`promote-mode-${item.job_key}`}
                checked={promoteMode === "overwrite"}
                onChange={() => setPromoteMode("overwrite")}
              />{" "}
              Overwrite family default
            </label>
          </div>
          {promoteMode === "fork" ? (
            <label className="work-product-prompt-editor__field">
              <span>Variant label</span>
              <input value={promoteLabel} onChange={(e) => setPromoteLabel(e.target.value)} disabled={busy} />
            </label>
          ) : null}
          <label className="work-product-prompt-editor__field">
            <span>Note (optional)</span>
            <input value={promoteNote} onChange={(e) => setPromoteNote(e.target.value)} disabled={busy} />
          </label>
          <div className="work-product-prompt-editor__actions work-product-prompt-editor__actions--promote">
            <button
              type="button"
              className="drt-btn"
              disabled={busy}
              onClick={() =>
                void promoteMut.mutateAsync({
                  job_key: item.job_key,
                  job_path: item.job_path || undefined,
                  fields: ["prompt"],
                  mode: promoteMode,
                  label: promoteMode === "fork" ? promoteLabel || undefined : "catalog-default",
                  note: promoteNote || undefined,
                  positive: String(prompt?.positive || positive || ""),
                  negative: String(prompt?.negative || negative || ""),
                })
              }
            >
              {promoteMode === "overwrite" ? "Overwrite default" : "Save variant"}
            </button>
            <button type="button" className="drt-btn" disabled={busy} onClick={() => setPromoteOpen(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

const PARAM_FIELD_DEFS: Array<{ key: keyof WorkProductParamsValues; label: string }> = [
  { key: "frames", label: "Frames" },
  { key: "steps", label: "Steps" },
  { key: "overlap", label: "Overlap" },
  { key: "seed", label: "Seed" },
];

function WorkProductParamsEditor({
  item,
  onCommitted,
}: {
  item: WorkProductItem;
  onCommitted?: () => void;
}) {
  const queryClient = useQueryClient();
  const profile = item.params_profile;
  const editable = isJobTrimEditable(item);
  const [editing, setEditing] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [draft, setDraft] = useState<WorkProductParamsValues>(() => ({ ...(profile?.current || {}) }));
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promoteMode, setPromoteMode] = useState<"fork" | "overwrite">("overwrite");

  useEffect(() => {
    setDraft({ ...(profile?.current || {}) });
    setDirty(false);
    setMsg(null);
    setEditing(false);
    setShowDiff(false);
  }, [item.job_key, profile?.snowflake, profile?.current?.frames, profile?.current?.steps, profile?.current?.overlap, profile?.current?.seed]);

  const saveMut = useMutation({
    mutationFn: updateShapeFactoryOwnedParams,
    onSuccess: async () => {
      setDirty(false);
      setEditing(false);
      setMsg("Saved to job");
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductsRoot });
      onCommitted?.();
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : String(e)),
  });
  const promoteMut = useMutation({
    mutationFn: promoteShapeFactoryTemplate,
    onSuccess: async (res) => {
      setPromoteOpen(false);
      setMsg(
        res.mode === "fork"
          ? `Forked catalog readable${res.path ? ` · ${String(res.path).split("/").pop()}` : ""}`
          : `Overwrote catalog template${res.bak_path ? " (bak kept)" : ""}`,
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductsRoot });
      onCommitted?.();
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : String(e)),
  });

  const current = profile?.current || {};
  const seed = profile?.seed || {};
  const hasAny = PARAM_FIELD_DEFS.some(({ key }) => current[key] != null || seed[key] != null);
  if (!hasAny && !editable) return null;

  const busy = saveMut.isPending || promoteMut.isPending;
  const canPromote =
    Boolean(profile?.snowflake && (isJobTrimEditable(item) || canPromoteJobPrompt(item))) && !editing;
  // Any template delta (incl. RNG seed) can be inspected; promote stays snowflake-only.
  const canDiff = Boolean(profile?.diffs && Object.keys(profile.diffs).length) && !editing;

  const setField = (key: keyof WorkProductParamsValues, raw: string) => {
    const next = { ...draft };
    if (!raw.trim()) {
      delete next[key];
    } else {
      const n = Number(raw);
      if (!Number.isFinite(n)) return;
      next[key] = Math.trunc(n);
    }
    setDraft(next);
    setDirty(true);
  };

  return (
    <div className="work-product-prompt-editor work-product-params-editor">
      <details className="work-product-prompt-editor__section" open={Boolean(profile?.snowflake)}>
        <summary className="work-product-prompt-editor__section-summary">
          <span>Params</span>
          <span className="factory-muted">
            {[
              current.frames != null ? `frames ${current.frames}` : null,
              current.steps != null ? `steps ${current.steps}` : null,
              current.overlap != null ? `overlap ${current.overlap}` : null,
              current.seed != null ? `seed ${current.seed}` : null,
            ]
              .filter(Boolean)
              .join(" · ") || "template defaults"}
          </span>
          <PromptSnowflakeChip params={profile} />
        </summary>
        <div className="work-product-prompt-editor__section-body">
          <div className="work-product-prompt-editor__actions">
            {editable ? (
              <button
                type="button"
                className="drt-btn"
                disabled={busy}
                onClick={() => {
                  if (editing) {
                    setDraft({ ...(profile?.current || {}) });
                    setDirty(false);
                    setEditing(false);
                  } else setEditing(true);
                }}
              >
                {editing ? "Cancel" : "Edit"}
              </button>
            ) : null}
            {editing ? (
              <button
                type="button"
                className="drt-btn"
                disabled={busy || !dirty}
                onClick={() =>
                  void saveMut.mutateAsync({
                    job_key: item.job_key,
                    job_path: item.job_path || undefined,
                    parameters: draft,
                  })
                }
              >
                Save to job
              </button>
            ) : null}
            {canDiff ? (
              <button type="button" className="drt-btn" disabled={busy} onClick={() => setShowDiff((v) => !v)}>
                {showDiff ? "Hide diff" : "Show diff"}
              </button>
            ) : null}
            {canPromote ? (
              <button type="button" className="drt-btn" disabled={busy} onClick={() => setPromoteOpen(true)}>
                Promote…
              </button>
            ) : null}
          </div>
          <div className="work-product-params-editor__grid">
            {PARAM_FIELD_DEFS.map(({ key, label }) => {
              const jobVal = editing ? draft[key] : current[key];
              const seedVal = seed[key];
              const changed = jobVal != null && seedVal != null && jobVal !== seedVal;
              return (
                <label key={key} className={"work-product-params-editor__field" + (changed ? " is-diff" : "")}>
                  <span className="work-product-params-editor__label">{label}</span>
                  {editing ? (
                    <input
                      type="number"
                      className="work-product-params-editor__input"
                      value={jobVal ?? ""}
                      onChange={(e) => setField(key, e.target.value)}
                      disabled={busy}
                    />
                  ) : (
                    <span className="work-product-params-editor__value mono">
                      {jobVal != null ? jobVal : "—"}
                      {showDiff && seedVal != null && changed ? (
                        <span className="factory-muted"> ← {seedVal}</span>
                      ) : null}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
          {msg ? <p className="factory-muted work-product-prompt-editor__msg">{msg}</p> : null}
          {promoteOpen && canPromote ? (
            <div className="work-product-prompt-editor__promote" role="dialog" aria-label="Promote params to catalog">
              <p className="factory-muted">
                Write these knobs into the catalog readable. Overwrite keeps a .bak; fork writes a sibling file.
              </p>
              <div className="work-product-prompt-editor__promote-modes" role="radiogroup" aria-label="Promote mode">
                <label>
                  <input
                    type="radio"
                    name={`promote-params-${item.job_key}`}
                    checked={promoteMode === "overwrite"}
                    onChange={() => setPromoteMode("overwrite")}
                    disabled={busy}
                  />{" "}
                  Overwrite catalog template
                </label>
                <label>
                  <input
                    type="radio"
                    name={`promote-params-${item.job_key}`}
                    checked={promoteMode === "fork"}
                    onChange={() => setPromoteMode("fork")}
                    disabled={busy}
                  />{" "}
                  Save as new readable fork
                </label>
              </div>
              <div className="work-product-prompt-editor__actions work-product-prompt-editor__actions--promote">
                <button type="button" className="drt-btn" disabled={busy} onClick={() => setPromoteOpen(false)}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="drt-btn"
                  disabled={busy}
                  onClick={() =>
                    void promoteMut.mutateAsync({
                      job_key: item.job_key,
                      job_path: item.job_path || undefined,
                      fields: ["params"],
                      mode: promoteMode,
                      parameters: current,
                    })
                  }
                >
                  {promoteMode === "fork" ? "Fork readable" : "Overwrite template"}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </details>
    </div>
  );
}

function loraBasename(name: string): string {
  const base = String(name || "").split(/[/\\]/).pop() || name;
  return base.replace(/\.safetensors$/i, "");
}

function WorkProductLorasEditor({
  item,
  onCommitted,
}: {
  item: WorkProductItem;
  onCommitted?: () => void;
}) {
  const queryClient = useQueryClient();
  const profile = item.loras_profile;
  const editable = isJobTrimEditable(item);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<WorkProductLoraEntry[]>(() => [...(profile?.current || [])]);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promoteMode, setPromoteMode] = useState<"fork" | "overwrite">("overwrite");

  useEffect(() => {
    setDraft([...(profile?.current || [])]);
    setDirty(false);
    setMsg(null);
    setEditing(false);
  }, [item.job_key, profile?.snowflake, profile?.content_hash]);

  const saveMut = useMutation({
    mutationFn: updateShapeFactoryOwnedLoras,
    onSuccess: async () => {
      setDirty(false);
      setEditing(false);
      setMsg("Saved to job");
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductsRoot });
      onCommitted?.();
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : String(e)),
  });
  const promoteMut = useMutation({
    mutationFn: promoteShapeFactoryTemplate,
    onSuccess: async (res) => {
      setPromoteOpen(false);
      setMsg(
        res.mode === "fork"
          ? `Forked readable · ${String(res.path || "").split(/[/\\]/).pop() || "ok"}`
          : `Overwrote template${res.bak_path ? " (+.bak)" : ""}`,
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductsRoot });
      onCommitted?.();
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : String(e)),
  });

  const current = profile?.current || [];
  const seed = profile?.seed || [];
  if (!current.length && !seed.length && !editable) return null;

  const busy = saveMut.isPending || promoteMut.isPending;
  const canPromote =
    Boolean(profile?.snowflake && (isJobTrimEditable(item) || canPromoteJobPrompt(item))) && !editing;
  const onCount = (editing ? draft : current).filter((e) => e.on !== false).length;
  const hint =
    (editing ? draft : current).length > 0
      ? `${onCount}/${(editing ? draft : current).length} on`
      : "no loras";

  const setEntry = (idx: number, patch: Partial<WorkProductLoraEntry>) => {
    setDraft((prev) => prev.map((row, i) => (i === idx ? { ...row, ...patch } : row)));
    setDirty(true);
  };

  return (
    <div className="work-product-prompt-editor work-product-loras-editor">
      <details className="work-product-prompt-editor__section" open={Boolean(profile?.snowflake)}>
        <summary className="work-product-prompt-editor__section-summary">
          <span>LoRAs</span>
          <span className="factory-muted">{hint}</span>
          <PromptSnowflakeChip loras={profile} />
        </summary>
        <div className="work-product-prompt-editor__section-body">
          <div className="work-product-prompt-editor__actions">
            {editable ? (
              <button
                type="button"
                className="drt-btn"
                disabled={busy}
                onClick={() => {
                  if (editing) {
                    setDraft([...(profile?.current || [])]);
                    setDirty(false);
                    setEditing(false);
                  } else setEditing(true);
                }}
              >
                {editing ? "Cancel" : "Edit"}
              </button>
            ) : null}
            {editing ? (
              <button
                type="button"
                className="drt-btn"
                disabled={busy || !dirty}
                onClick={() =>
                  void saveMut.mutateAsync({
                    job_key: item.job_key,
                    job_path: item.job_path || undefined,
                    entries: draft,
                  })
                }
              >
                Save to job
              </button>
            ) : null}
            {canPromote ? (
              <button type="button" className="drt-btn" disabled={busy} onClick={() => setPromoteOpen(true)}>
                Promote…
              </button>
            ) : null}
          </div>
          <div className="work-product-loras-editor__list">
            {(editing ? draft : current).map((row, idx) => {
              const seedRow = seed[idx];
              const changed =
                Boolean(seedRow) &&
                (Boolean(row.on) !== Boolean(seedRow?.on) ||
                  Number(row.strength ?? NaN) !== Number(seedRow?.strength ?? NaN) ||
                  String(row.lora || "") !== String(seedRow?.lora || ""));
              return (
                <div
                  key={`${row.lora}-${idx}`}
                  className={"work-product-loras-editor__row" + (changed ? " is-diff" : "")}
                >
                  <label className="work-product-loras-editor__on">
                    <input
                      type="checkbox"
                      checked={row.on !== false}
                      disabled={!editing || busy}
                      onChange={(e) => setEntry(idx, { on: e.target.checked })}
                    />
                    <span className="work-product-loras-editor__name mono" title={row.lora}>
                      {loraBasename(row.lora)}
                    </span>
                  </label>
                  <label className="work-product-loras-editor__strength">
                    <span className="work-product-params-editor__label">Str</span>
                    {editing ? (
                      <input
                        type="number"
                        step="0.05"
                        className="work-product-params-editor__input"
                        value={row.strength ?? ""}
                        disabled={busy || row.on === false}
                        onChange={(e) => {
                          const n = Number(e.target.value);
                          setEntry(idx, { strength: Number.isFinite(n) ? n : undefined });
                        }}
                      />
                    ) : (
                      <span className="work-product-params-editor__value mono">
                        {row.strength != null ? row.strength : "—"}
                        {changed && seedRow?.strength != null ? (
                          <span className="factory-muted"> ← {seedRow.strength}</span>
                        ) : null}
                      </span>
                    )}
                  </label>
                </div>
              );
            })}
            {!current.length && !editing ? (
              <p className="factory-muted">No Power LoRA slots on this job’s workflow.</p>
            ) : null}
          </div>
          {msg ? <p className="factory-muted work-product-prompt-editor__msg">{msg}</p> : null}
          {promoteOpen && canPromote ? (
            <div className="work-product-prompt-editor__promote" role="dialog" aria-label="Promote LoRAs to catalog">
              <p className="factory-muted">
                Write this LoRA stack into the catalog readable. Overwrite keeps a .bak; fork writes a sibling file.
              </p>
              <div className="work-product-prompt-editor__promote-modes" role="radiogroup" aria-label="Promote mode">
                <label>
                  <input
                    type="radio"
                    name={`promote-loras-${item.job_key}`}
                    checked={promoteMode === "overwrite"}
                    onChange={() => setPromoteMode("overwrite")}
                    disabled={busy}
                  />{" "}
                  Overwrite catalog template
                </label>
                <label>
                  <input
                    type="radio"
                    name={`promote-loras-${item.job_key}`}
                    checked={promoteMode === "fork"}
                    onChange={() => setPromoteMode("fork")}
                    disabled={busy}
                  />{" "}
                  Save as new readable fork
                </label>
              </div>
              <div className="work-product-prompt-editor__actions work-product-prompt-editor__actions--promote">
                <button type="button" className="drt-btn" disabled={busy} onClick={() => setPromoteOpen(false)}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="drt-btn"
                  disabled={busy}
                  onClick={() =>
                    void promoteMut.mutateAsync({
                      job_key: item.job_key,
                      job_path: item.job_path || undefined,
                      fields: ["loras"],
                      mode: promoteMode,
                      entries: current,
                    })
                  }
                >
                  {promoteMode === "fork" ? "Fork readable" : "Overwrite template"}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </details>
    </div>
  );
}

function workbenchAdvanceSubmitIntent(
  item: WorkProductItem,
  opts: {
    outputTrim: InputTrimState;
    sourceClipId?: string | null;
    extendFamilyDefaults?: Record<string, string>;
    step?: string | null;
    clip?: ShapeFactoryClip | null;
  },
): SubmitDeepLink {
  const media = String(item.output_relpath || "").trim();
  const successors = opts.extendFamilyDefaults || EMPTY_EXTEND_DEFAULTS;
  const family = smartExtendFamily(item, successors);
  const clip = opts.clip || null;
  return buildSubmitDeepLink({
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

/** Open Submit with this job's *input* as the subject (Extend/Vary for video, I2V for still). */
function workbenchSourceSubmitIntent(
  item: WorkProductItem,
  opts: {
    sourceTrim: InputTrimState;
    sourceClipId?: string | null;
    step?: string | null;
    clip?: ShapeFactoryClip | null;
  },
): SubmitDeepLink | null {
  const media = workbenchSourceMediaRelpath(item);
  if (!media) return null;
  const still = isStillMediaPath(media);
  const clip = opts.clip || null;
  const windowOk =
    !still &&
    opts.sourceTrim.markIn != null &&
    opts.sourceTrim.markOut != null &&
    Number.isFinite(opts.sourceTrim.markIn) &&
    Number.isFinite(opts.sourceTrim.markOut) &&
    opts.sourceTrim.markOut > opts.sourceTrim.markIn + 0.05;
  return buildSubmitDeepLink({
    mediaRelpath: media,
    // Do not pass from_job — this media is the input, not this job's product.
    family: String(item.family_slug || "").trim() || null,
    markIn: still ? null : clip ? clip.mark_in_s : windowOk ? opts.sourceTrim.markIn : null,
    markOut: still ? null : clip ? clip.mark_out_s : windowOk ? opts.sourceTrim.markOut : null,
    clipId: still ? null : clip?.clip_id || opts.sourceClipId || null,
    // Stills go through I2V on Submit; videos open the Advance (extend) compose.
    step: still ? null : opts.step || "advance.extend",
    origin: "workbench",
  });
}

function WorkProductQuickQueue({
  item,
  families,
  extendFamilyDefaults,
  outputTrim,
  sourceTrim,
  sourceClipId,
  onCommitted,
  onOpenSubmit,
  onFocusJobKey,
}: {
  item: WorkProductItem;
  families?: WorkProductFamilyOption[];
  extendFamilyDefaults?: Record<string, string>;
  outputTrim: InputTrimState;
  sourceTrim: InputTrimState;
  sourceClipId?: string | null;
  onCommitted?: () => void;
  onOpenSubmit?: (intent: SubmitDeepLink) => void;
  onFocusJobKey?: (jobKey: string) => void;
}) {
  const open = item.work_items_open || [];
  const extendOpen = openPoolItem(open, "extend");
  const varyOpen = openPoolItem(open, "vary");
  const deriveOpen = openPoolItem(open, "derive");
  const relpath = String(item.output_relpath || "").trim();
  const jobKey = String(item.job_key || "").trim();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const invalidateWorkbench = () =>
    queryClient.invalidateQueries({
      queryKey: queryKeys.shapeFactory.workProductsRoot,
    });
  const invalidateQueue = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.queue.snapshot }),
      queryClient.invalidateQueries({ queryKey: queryKeys.queue.history }),
      queryClient.invalidateQueries({ queryKey: queryKeys.queue.ledgerRoot }),
    ]);
  const unqueueMutation = useMutation({ mutationFn: unqueueShapeFactory });
  const claimMutation = useMutation({ mutationFn: claimShapeFactoryFromQueue });
  const discardMutation = useMutation({ mutationFn: discardShapeFactoryJob });
  const finishEditMutation = useMutation({ mutationFn: finishShapeFactoryEdit });
  const replayMutation = useMutation({ mutationFn: replayShapeFactory });
  const swapMutation = useMutation({ mutationFn: swapShapeFactoryFamily });
  const currentFamily = String(item.family_slug || "").trim();
  const swapTargets = useMemo(
    () => familySwapTargets(families || [], currentFamily),
    [families, currentFamily],
  );
  const [rerunFamily, setRerunFamily] = useState(currentFamily);
  const [rerunTrimMode, setRerunTrimMode] = useState<"job" | "edited">(() =>
    sourceTrim.dirty || sourceTrim.clampedDefault ? "edited" : "job",
  );
  const [rerunSeedMode, setRerunSeedMode] = useState<"same" | "new">("new");

  useEffect(() => {
    setRerunFamily(currentFamily);
  }, [item.job_key, currentFamily]);

  useEffect(() => {
    if (sourceTrim.dirty || sourceTrim.clampedDefault) setRerunTrimMode("edited");
  }, [sourceTrim.dirty, sourceTrim.clampedDefault]);

  const familyChanged = Boolean(rerunFamily && currentFamily && rerunFamily !== currentFamily);
  const swapInstead = familyChanged && canSwapFamilyWorkProduct(item);

  const mutationsBusy =
    unqueueMutation.isPending ||
    claimMutation.isPending ||
    discardMutation.isPending ||
    finishEditMutation.isPending ||
    replayMutation.isPending ||
    swapMutation.isPending;
  const isBusy = busy || mutationsBusy;
  const canRerun = Boolean(jobKey) && !isBusy;
  const canUnqueue = canUnqueueWorkProduct(item) && !isBusy;
  const canClaim =
    Boolean(item.live_from_comfy || isNonFactoryWorkProduct(item)) &&
    Boolean(String(item.prompt_id || "").trim()) &&
    !String(item.job_path || "").trim() &&
    !isBusy;
  const canEditSubmit = canEditJobViaSubmit(item) && !isBusy;
  const isEditing = workProductStatusKey(item) === "editing";
  const editSubmitIntent = canEditSubmit
    ? buildSubmitDeepLink({
        editJob: jobKey,
        origin: "workbench",
        family: item.family_slug || null,
        mediaRelpath: String(item.bindings?.source_video?.relpath || item.bindings?.source_still?.relpath || "").trim() || null,
      })
    : null;
  const canArchive = canArchiveTerminalWorkProduct(item) && !isBusy;
  const canDelete = canDeleteWorkProduct(item) && !isBusy;
  const deleteIsPendingOnly = canDiscardPendingWorkProduct(item);
  const nonFactory = isNonFactoryWorkProduct(item);
  const submitIntent = workbenchAdvanceSubmitIntent(item, {
    outputTrim,
    sourceClipId,
    extendFamilyDefaults,
  });
  const sourceSubmitIntent = workbenchSourceSubmitIntent(item, {
    sourceTrim,
    sourceClipId,
  });
  const openSubmit = (intent: SubmitDeepLink | null) => {
    if (!intent) return;
    if (onOpenSubmit) onOpenSubmit(intent);
  };
  const unqueue = async () => {
    const pid = String(item.prompt_id || "").trim();
    if (!pid || isBusy) return;
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
      const res = await unqueueMutation.mutateAsync({
        prompt_id: pid,
        job_key: nonFactory ? undefined : jobKey || undefined,
        job_path: nonFactory ? undefined : String(item.job_path || "").trim() || undefined,
        actor: "operator",
        reason: nonFactory ? "user_unqueue_non_factory" : "user_unqueue",
        source_surface: "workbench",
      });
      if (res.factory_job) {
        setMsg(`Unqueued → pending${res.job_key ? ` · ${res.job_key}` : ""}`);
      } else {
        setMsg("Removed from Comfy queue (no factory job)");
      }
      await invalidateWorkbench();
      await invalidateQueue();
      onCommitted?.();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const claimToWorkbench = async () => {
    const pid = String(item.prompt_id || "").trim();
    if (!pid || isBusy) return;
    const familyGuess = String(item.family_slug || "").trim();
    let family = familyGuess;
    if (!family) {
      const typed = window.prompt(
        "Claim as Workbench job — enter family slug (e.g. FB8VA5-ZOOMOUT):",
        "",
      );
      family = String(typed || "").trim();
      if (!family) return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const res = await claimMutation.mutateAsync({
        prompt_id: pid,
        family_slug: family,
      });
      setMsg(
        res.already_indexed
          ? `Already a factory job · ${res.job_key || ""}`
          : `Claimed · ${res.job_key || ""}${res.recipe?.loras ? " · loras recovered" : ""}`,
      );
      if (res.workbench_href) {
        window.history.replaceState(null, "", res.workbench_href);
      }
      await invalidateWorkbench();
      onCommitted?.();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const discard = async () => {
    if (!canDelete || isBusy) return;
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
      const res = await discardMutation.mutateAsync({
        job_key: jobKey || undefined,
        job_path: String(item.job_path || "").trim() || undefined,
        prompt_id: String(item.prompt_id || "").trim() || undefined,
        history_from_comfy: isHistoryFailureStub(item) || Boolean(item.history_from_comfy),
        reason: deleteIsPendingOnly ? "user_expunged" : "user_expunged_failure",
        expunge: true,
        actor: "operator",
        source_surface: "workbench",
      });
      setMsg(
        res.history_stub || res.dismissed
          ? `Dismissed history failure${res.job_key ? ` · ${res.job_key}` : ""}`
          : `Deleted${res.job_key ? ` · ${res.job_key}` : ""}`,
      );
      await invalidateWorkbench();
      onCommitted?.();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const archive = async () => {
    if (!canArchive || isBusy) return;
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
      const res = await discardMutation.mutateAsync({
        job_key: jobKey || undefined,
        job_path: String(item.job_path || "").trim() || undefined,
        prompt_id: String(item.prompt_id || "").trim() || undefined,
        history_from_comfy: isHistoryFailureStub(item) || Boolean(item.history_from_comfy),
        reason: "user_archived_failure",
        expunge: false,
        actor: "operator",
        source_surface: "workbench",
      });
      setMsg(
        res.history_stub || res.dismissed
          ? `Dismissed history failure${res.job_key ? ` · ${res.job_key}` : ""}`
          : `Archived failure${res.job_key ? ` · ${res.job_key}` : ""}`,
      );
      await invalidateWorkbench();
      onCommitted?.();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const rerun = async (when: "now" | "later") => {
    if (!jobKey || isBusy) return;
    const targetFamily = String(rerunFamily || currentFamily || "").trim();
    if (swapInstead) {
      const ok = window.confirm(
        `Swap this ${workProductStatusKey(item)} job to ${targetFamily}?\n\n` +
          `A new ${targetFamily} job is queued (trim ${rerunTrimMode} · seed ${rerunSeedMode}). ` +
          `The current ${currentFamily || "family"} job is unqueued and removed so it cannot start.`,
      );
      if (!ok) return;
    }
    setBusy(true);
    setMsg("");
    try {
      let overrides: ShapeFactoryMapQueueOverrides | undefined;
      let warning: string | null = null;
      if (rerunTrimMode === "edited") {
        const fromTrim = trimOverridesFromState(
          { ...sourceTrim, dirty: true },
          null,
          sourceClipId,
        );
        overrides = fromTrim.overrides;
        warning = fromTrim.warning;
      }
      if (swapInstead) {
        const res = await swapMutation.mutateAsync({
          job_key: jobKey,
          family_slug: targetFamily,
          replace: true,
          front: when === "now",
          seed_mode: rerunSeedMode,
          overrides,
        });
        const row = (res.items || []).find((it) => it.ok) || (res.items || [])[0];
        const nextKey = String(row?.replay?.job_key || "").trim();
        const pid = String(row?.replay?.prompt_id || "").trim();
        const fail = !res.ok
          ? row?.detail || row?.error || res.detail || res.error || "swap failed"
          : null;
        setMsg(
          [
            fail
              ? `Swap ${when} failed${fail ? ` · ${fail}` : ""}`
              : nextKey
                ? `Swapped ${when}→${targetFamily} · ${nextKey}${pid ? ` · ${pid}` : ""}`
                : `Swapped ${when}→${targetFamily}`,
            `trim ${rerunTrimMode}`,
            warning,
          ]
            .filter(Boolean)
            .join(" · "),
        );
        await invalidateWorkbench();
        await invalidateQueue();
        onCommitted?.();
        if (nextKey) onFocusJobKey?.(nextKey);
        return;
      }
      const res = await replayMutation.mutateAsync({
        job_key: jobKey,
        family_slug: targetFamily || undefined,
        extend: false,
        front: when === "now",
        seed_mode: rerunSeedMode,
        overrides,
      });
      const nextKey = String(res.job_key || "").trim();
      const pid = String(res.prompt_id || "").trim();
      const clampMsg = res.trim_clamped?.message || warning;
      const seedLabel =
        res.seed_mode === "new"
          ? `seed new${res.noise_seed != null ? ` ${res.noise_seed}` : ""}`
          : res.seed_mode === "same" || res.seed_mode === "explicit"
            ? `seed same${res.noise_seed != null ? ` ${res.noise_seed}` : ""}`
            : res.seed_mode === "same_missing"
              ? "seed same (missing — template)"
              : null;
      const familyLabel = familyChanged ? `as ${targetFamily}` : null;
      setMsg(
        [
          nextKey
            ? `Re-run ${when}→${nextKey}${pid ? ` · ${pid}` : ""}`
            : pid
              ? `Re-run ${when} queued · ${pid}`
              : `Re-run ${when} queued`,
          familyLabel,
          `trim ${rerunTrimMode}`,
          seedLabel,
          clampMsg,
        ]
          .filter(Boolean)
          .join(" · "),
      );
      await invalidateWorkbench();
      await invalidateQueue();
      onCommitted?.();
      if (nextKey) onFocusJobKey?.(nextKey);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const openBadge = (wi: WorkItem | null | undefined, label: string) =>
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
          <button
            type="button"
            className="drt-btn work-product-quick-queue__now"
            title="Open Submit with Extend / Vary / Derive for this output"
            onClick={() => openSubmit(submitIntent)}
          >
            Open output in Submit
          </button>
        ) : (
          <span className="work-product-quick-queue__hint">No output</span>
        )}
        {sourceSubmitIntent ? (
          <button
            type="button"
            className="drt-btn work-product-quick-queue__now"
            title={
              isStillMediaPath(workbenchSourceMediaRelpath(item) || "")
                ? "Open Submit with this job's input still (I2V)"
                : "Open Submit with Extend / Vary for this job's input video"
            }
            onClick={() => openSubmit(sourceSubmitIntent)}
          >
            Open input in Submit
          </button>
        ) : (
          <span className="work-product-quick-queue__hint">No input</span>
        )}
        {openBadge(extendOpen, "Extend")}
        {openBadge(varyOpen, "Vary")}
        {openBadge(deriveOpen, "Derive")}
        <span className="work-product-quick-queue__sep" aria-hidden="true" />
        <span
          className="work-product-quick-queue__label"
          title={
            swapInstead
              ? "Retarget this waiting job to another family, then retire the old one"
              : "New job from this recipe — trim, seed, and family are independent"
          }
        >
          {swapInstead ? "Swap" : "Re-run"}
        </span>
        {currentFamily || swapTargets.length ? (
          <label className="work-product-rerun-opts work-product-rerun-opts--family">
            <span className="work-product-rerun-opts__label">Family</span>
            <select
              className="work-product-family-select"
              value={rerunFamily || currentFamily}
              disabled={isBusy || (!swapTargets.length && !currentFamily)}
              aria-label="Family for re-run or swap"
              title={
                swapInstead
                  ? `Swap queued ${currentFamily} → ${rerunFamily}`
                  : familyChanged
                    ? `Re-run as ${rerunFamily} (keeps this job)`
                    : "Same family, or pick a compatible one"
              }
              onChange={(e) => setRerunFamily(e.target.value)}
            >
              {currentFamily ? <option value={currentFamily}>{currentFamily}</option> : null}
              {swapTargets.map((f) => (
                <option key={f.slug} value={f.slug}>
                  {f.slug}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <div className="work-product-rerun-opts" role="group" aria-label="Re-run trim">
          <span className="work-product-rerun-opts__label">Trim</span>
          <div className="segmented work-product-rerun-opts__seg">
            <button
              type="button"
              className={rerunTrimMode === "job" ? "seg-btn active" : "seg-btn"}
              disabled={isBusy}
              title="Keep the Use window baked into this job"
              onClick={() => setRerunTrimMode("job")}
            >
              As job
            </button>
            <button
              type="button"
              className={rerunTrimMode === "edited" ? "seg-btn active" : "seg-btn"}
              disabled={isBusy}
              title="Use the source marks currently on this card"
              onClick={() => setRerunTrimMode("edited")}
            >
              As edited
            </button>
          </div>
        </div>
        <div className="work-product-rerun-opts" role="group" aria-label="Re-run seed">
          <span className="work-product-rerun-opts__label">Seed</span>
          <div className="segmented work-product-rerun-opts__seg">
            <button
              type="button"
              className={rerunSeedMode === "same" ? "seg-btn active" : "seg-btn"}
              disabled={isBusy}
              title="Hold this job’s noise seed (exact retry when trim also matches)"
              onClick={() => setRerunSeedMode("same")}
            >
              Same
            </button>
            <button
              type="button"
              className={rerunSeedMode === "new" ? "seg-btn active" : "seg-btn"}
              disabled={isBusy}
              title="Draw a new noise seed; keep other bindings"
              onClick={() => setRerunSeedMode("new")}
            >
              New
            </button>
          </div>
        </div>
        <button
          type="button"
          className="drt-btn work-product-quick-queue__rerun"
          disabled={!canRerun || (familyChanged && !rerunFamily)}
          title={
            swapInstead
              ? `Swap to ${rerunFamily} · trim ${rerunTrimMode} · seed ${rerunSeedMode} · front of queue · retire this job`
              : `New job${familyChanged ? ` as ${rerunFamily}` : ""} · trim ${rerunTrimMode} · seed ${rerunSeedMode} · front of queue`
          }
          onClick={() => void rerun("now")}
        >
          {swapInstead ? "Swap now" : "Now"}
        </button>
        <button
          type="button"
          className="drt-btn work-product-quick-queue__rerun"
          disabled={!canRerun || (familyChanged && !rerunFamily)}
          title={
            swapInstead
              ? `Swap to ${rerunFamily} · trim ${rerunTrimMode} · seed ${rerunSeedMode} · normal priority · retire this job`
              : `New job${familyChanged ? ` as ${rerunFamily}` : ""} · trim ${rerunTrimMode} · seed ${rerunSeedMode} · normal priority`
          }
          onClick={() => void rerun("later")}
        >
          {swapInstead ? "Swap later" : "Later"}
        </button>
        {canEditSubmit && editSubmitIntent ? (
          <>
            <span className="work-product-quick-queue__sep" aria-hidden="true" />
            <button
              type="button"
              className="drt-btn work-product-quick-queue__edit"
              title="Edit this run in Submit (unqueues if waiting on Comfy; holds pending-drain)"
              onClick={() => openSubmit(editSubmitIntent)}
            >
              Edit
            </button>
          </>
        ) : null}
        {isEditing ? (
          <>
            <span className="work-product-quick-queue__sep" aria-hidden="true" />
            <button
              type="button"
              className="drt-btn work-product-quick-queue__release"
              disabled={isBusy}
              title="Release editing lock back to pending so drain can pick it up"
              onClick={() => {
                void (async () => {
                  setBusy(true);
                  setMsg(null);
                  try {
                    await finishEditMutation.mutateAsync({
                      job_key: jobKey,
                      action: "later",
                      actor: "operator",
                      reason: "finish_edit:later",
                      source_surface: "workbench",
                    });
                    setMsg("Released → pending");
                    await invalidateWorkbench();
                    onCommitted?.();
                  } catch (e) {
                    setMsg(e instanceof Error ? e.message : String(e));
                  } finally {
                    setBusy(false);
                  }
                })();
              }}
            >
              Release
            </button>
          </>
        ) : null}
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
        {canClaim ? (
          <>
            <span className="work-product-quick-queue__sep" aria-hidden="true" />
            <button
              type="button"
              className="drt-btn"
              disabled={!canClaim}
              title="Mint a durable Workbench job from this Comfy prompt (prompt/params/LoRAs on disk)"
              onClick={() => void claimToWorkbench()}
            >
              Claim
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


function lineageLibraryForRelpath(relpath: string): string {
  const norm = relpath.replace(/\\/g, "/").replace(/^\/+/, "").toLowerCase();
  if (norm.startsWith("input/") || isStillMediaPath(norm)) return "input";
  if (norm.startsWith("wip/")) return "wip";
  return "og";
}

/**
 * Seed Discovery lineage from this job's output when present; otherwise from the
 * known parent/source binding so pending/queued jobs still show ancestry.
 */
function workProductLineageSeed(item: WorkProductItem): {
  seed: DiscoveryLibraryItem;
  via: "output" | "source";
} | null {
  const outRel = String(item.output_relpath || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  if (outRel) {
    const name = outRel.split("/").pop() || outRel;
    return {
      via: "output",
      seed: {
        group_id: undefined,
        relpath: outRel,
        library: lineageLibraryForRelpath(outRel),
        name,
        mtime: 0,
        size: 0,
        sha256: "",
        url: item.output_url || "",
        thumb_url: item.output_thumb_url || undefined,
      } as DiscoveryLibraryItem,
    };
  }

  const source = workbenchSourceBinding(item);
  const srcRel = workbenchSourceMediaRelpath(item);
  if (!srcRel) return null;
  const name = srcRel.split("/").pop() || srcRel;
  return {
    via: "source",
    seed: {
      group_id: undefined,
      relpath: srcRel,
      library: lineageLibraryForRelpath(srcRel),
      name,
      mtime: 0,
      size: 0,
      sha256: "",
      url: source?.url || item.parent_output_url || "",
      thumb_url: source?.thumb_url || item.parent_output_thumb_url || undefined,
    } as DiscoveryLibraryItem,
  };
}

function openLineageSummaryInWorkbench(s: DiscoveryAssetLineageItemSummary) {
  // Input stills → Stills viewer; og/wip ancestors → focused Workbench media view.
  if (isLineageInputStill(s) || s.external === true) {
    window.location.assign(lineageSummaryHref(s));
    return;
  }
  window.location.assign(
    workbenchHrefForMedia({
      relpath: s.relpath || s.workspace_relpath || s.video_relpath || null,
      name: s.name || null,
      groupId: s.group_id || null,
    }),
  );
}

function WorkProductLineageSection({
  item,
  open,
  onOpenChange,
}: {
  item: WorkProductItem;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const seeded = useMemo(() => workProductLineageSeed(item), [item]);
  const seed = seeded?.seed ?? null;
  const summary = seed
    ? seeded?.via === "source"
      ? `via source · ${String(seed.name || seed.relpath || "source")}`
      : String(seed.name || seed.relpath || "output")
    : "no source or output yet";
  const panelRef = useRef<HTMLDivElement | null>(null);

  return (
    <details
      className="work-product-details__group work-product-details__group--wide work-product-details__lineage"
      open={open}
      onToggle={(e) => onOpenChange(e.currentTarget.open)}
    >
      <summary className="work-product-details__group-title">
        <span className="work-product-details__group-title-text">Lineage</span>
        <span className="work-product-details__group-summary" title={summary}>
          {summary}
        </span>
      </summary>
      {open ? (
        seed ? (
          <div className="work-product-lineage-panel" ref={panelRef}>
            {seeded?.via === "source" ? (
              <p className="factory-muted work-product-lineage-empty" style={{ marginBottom: 8 }}>
                No output yet — showing ancestry from this job’s source / parent.
              </p>
            ) : null}
            <DiscoveryAssetLineagePanel
              seedItem={seed}
              onOpenSummary={openLineageSummaryInWorkbench}
              secondaryLink="library"
              onGraphExpanded={() => {
                // After backfill adds nodes, bring the grown panel into view.
                window.requestAnimationFrame(() => {
                  panelRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
                });
              }}
            />
          </div>
        ) : (
          <p className="factory-muted work-product-lineage-empty">
            Lineage needs an output or a recoverable source/parent binding.
          </p>
        )
      ) : null}
    </details>
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
  onOpenSubmit,
  onFocusJobKey,
}: {
  item: WorkProductItem;
  families?: WorkProductFamilyOption[];
  extendFamilyDefaults?: Record<string, string>;
  outputTrim: InputTrimState;
  sourceTrim: InputTrimState;
  sourceClipId?: string | null;
  onCommitted?: () => void;
  onOpenSubmit?: (intent: SubmitDeepLink) => void;
  onFocusJobKey?: (jobKey: string) => void;
}) {
  const prompt = item.prompt_profile;
  const groups = useMemo(() => {
    // Dedicated Positive/Negative editor owns prompt display — drop duplicate
    // detail rows (legacy positive/negative, profile peek, binding slot).
    const filtered = (item.details || []).filter((r) => {
      if (
        r.label === "Prompt positive" ||
        r.label === "Prompt negative" ||
        r.label === "Prompt name" ||
        r.label === "Prompt profile" ||
        r.label === "Prompt label" ||
        r.label === "Prompt file"
      ) {
        return false;
      }
      if (prompt && r.label === "Binding · prompt_profile") return false;
      return true;
    });
    return groupDetailRows(filtered);
  }, [item.details, prompt]);
  const [sectionOpen, setSectionOpen] = useState<Record<string, boolean>>(() => loadSectionOpen());
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
        {item.family_slug ? (
          <a
            className="work-product-badge work-product-badge--link"
            href={factoryMapFamilyHref(item.family_slug, { focus: "pools" })}
            title="Open family pools on Factory Map"
          >
            {item.family_slug}
          </a>
        ) : null}
        {shape?.io_class ? (
          <span className="work-product-badge" title="IO class (station process)">
            {String(shape.io_class)}
          </span>
        ) : null}
        {shape?.chain_role ? (
          <span className="work-product-badge" title="Chain role in a pipeline">
            {String(shape.chain_role)}
          </span>
        ) : null}
        {item.noise_seed != null && Number.isFinite(Number(item.noise_seed)) ? (
          <span
            className="work-product-badge work-product-badge--seed"
            title={
              item.seed_mode
                ? `Noise seed · mode ${item.seed_mode}`
                : "Noise seed (RandomNoise / KSampler)"
            }
          >
            seed {Number(item.noise_seed)}
          </span>
        ) : null}
        {item.pick_mode ? (
          <span className={`work-product-badge ${badgeClass(item.pick_mode)}`}>{item.pick_mode}</span>
        ) : null}
        {item.step ? <span className={`work-product-badge ${badgeClass(item.step)}`}>{item.step}</span> : null}
        {item.rating_kind ? (
          <span className={`work-product-badge ${badgeClass(item.rating_kind)}`}>{item.rating_kind}</span>
        ) : null}
        {item.disposition_entry ? <span className="work-product-badge">{item.disposition_entry}</span> : null}
        {(() => {
          const markers = item.markers || {};
          const keys = Object.keys(markers).sort();
          if (!keys.length) return null;
          return keys.map((k) => (
            <span
              key={`wp-marker-${k}`}
              className={`work-product-badge${
                k === "decode.vae" ? " work-product-badge--decode-vae" : ""
              }`}
              title={`Marker ${k}=${markers[k]}`}
            >
              {k === "decode.vae" ? `vae:${markers[k]}` : `${k}:${markers[k]}`}
            </span>
          ));
        })()}
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
        {item.flow_phase && item.flow_phase !== item.status ? (
          <span className="work-product-badge" title="Normalized flow phase">
            phase:{item.flow_phase}
          </span>
        ) : null}
      </div>
      {item.error ? (
        <div className="work-product-details__error" title={item.error}>
          {item.error}
        </div>
      ) : null}
      <FlowEventTimeline item={item} />
      <WorkProductAppetiteStrip
        relpath={workbenchJobAppetiteRelpath(item)}
        jobKey={item.job_key}
        familySlug={item.family_slug}
        disabledHint={
          isLivePreviewItem(item) || String(item.status || "") === "pending"
            ? "Appetite available once this job has an output"
            : "Appetite needs an output path"
        }
      />
      <WorkProductQuickQueue
        item={item}
        families={families}
        extendFamilyDefaults={extendFamilyDefaults}
        outputTrim={outputTrim}
        sourceTrim={sourceTrim}
        sourceClipId={sourceClipId}
        onCommitted={onCommitted}
        onOpenSubmit={onOpenSubmit}
        onFocusJobKey={onFocusJobKey}
      />
      <WorkProductPromptEditor item={item} onCommitted={onCommitted} />
      <WorkProductParamsEditor item={item} onCommitted={onCommitted} />
      <WorkProductLorasEditor item={item} onCommitted={onCommitted} />
      <div className="work-product-details__groups">
        <WorkProductLineageSection
          item={item}
          open={sectionOpen.lineage ?? DEFAULT_SECTION_OPEN.lineage ?? false}
          onOpenChange={(next) => setGroupOpen("lineage", next)}
        />
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
              open={sectionOpen[group.id] ?? DEFAULT_SECTION_OPEN[group.id] ?? false}
              onToggle={(e) => {
                const el = e.currentTarget;
                setGroupOpen(group.id, el.open);
              }}
            >
              <summary className="work-product-details__group-title">
                <span className="work-product-details__group-title-text">{group.title}</span>
                <span className="work-product-details__group-summary">{detailGroupSummary(group, item)}</span>
              </summary>
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

function workProductMatchesFocus(
  item: WorkProductItem,
  jobKey: string | null,
  promptId: string | null,
): boolean {
  if (jobKey && String(item.job_key || "").trim() === jobKey) return true;
  if (promptId && String(item.prompt_id || "").trim() === promptId) return true;
  return false;
}

function WorkProductIndexRow({
  item,
  selected,
  onSelect,
}: {
  item: WorkProductItem;
  selected: boolean;
  onSelect: (item: WorkProductItem) => void;
}) {
  const thumb = item.output_thumb_url || sourcePreviewUrls(item).thumb;
  const status = statusFilterVisual(item.status || "pending");
  const timing = timingHeadline(item);
  const thumbMeta = isSourceThumbPreviewItem(item) ? sourceThumbPreviewMeta(item) : null;
  return (
    <button
      type="button"
      id={workbenchJobDomId(item.job_key)}
      role="option"
      aria-selected={selected}
      data-job-key={item.job_key || undefined}
      data-prompt-id={item.prompt_id || undefined}
      className={`work-product-index-row work-product-index-row--status-${status}${
        selected ? " is-selected" : ""
      }${isLivePreviewItem(item) ? " is-live" : ""}`}
      onClick={() => onSelect(item)}
    >
      <span className="work-product-index-row__thumb">
        {thumb ? (
          <img src={thumb} alt="" />
        ) : (
          <span className="work-product-index-row__thumb-empty" aria-hidden>
            {isRunningLiveItem(item) ? "live" : "—"}
          </span>
        )}
      </span>
      <span className="work-product-index-row__meta">
        <span className="work-product-index-row__title">
          <strong>{item.family_slug || "job"}</strong>
          {item.is_hourly ? (
            <span className="work-product-badge work-product-badge--hourly" title="Produced by the hourly planner">
              Hourly
            </span>
          ) : null}
          {isRunningLiveItem(item) ? (
            <span className="work-product-badge work-product-badge--live-run">live</span>
          ) : thumbMeta ? (
            <span className={`work-product-badge work-product-badge--live-${thumbMeta.visual}`}>{thumbMeta.label}</span>
          ) : null}
          <span className={`work-product-index-row__status work-product-index-row__status--${status}`}>
            {item.status || "pending"}
          </span>
        </span>
        <span className="work-product-index-row__sub">
          {formatRelativeAge(item.created_at)}
          {timing ? ` · ${timing.text}` : ""}
        </span>
        <code className="work-product-index-row__key" title={item.job_key}>
          {item.job_key}
        </code>
      </span>
      <AppetitePreviewBadge
        relpath={workbenchJobAppetiteRelpath(item)}
        size="sm"
        jobKey={item.job_key}
        familySlug={item.family_slug}
      />
    </button>
  );
}

function WorkProductRowInner({
  item,
  layout,
  families,
  extendFamilyDefaults,
  onCommitted,
  onOpenSubmit,
  onFocusJobKey,
}: {
  item: WorkProductItem;
  layout: RowLayout;
  families?: WorkProductFamilyOption[];
  extendFamilyDefaults?: Record<string, string>;
  onCommitted?: () => void;
  onOpenSubmit?: (intent: SubmitDeepLink) => void;
  onFocusJobKey?: (jobKey: string) => void;
}) {
  const thumbMeta = isSourceThumbPreviewItem(item) ? sourceThumbPreviewMeta(item) : null;
  const thumbBadgeClass = thumbMeta ? `work-product-badge--live-${thumbMeta.visual}` : "";
  const successors = extendFamilyDefaults || {};
  const extendFamily = smartExtendFamily(item, successors);
  const outputDefaults = familyVhsDefaults(families, extendFamily || String(item.family_slug || ""));
  const applied = item.applied_vhs;
  // Display/source seeding uses what THIS job applied — never catalog template fossils
  // (FB9_GEX skip=85 etc.) as a stand-in for "unset".
  const sourceDefaults =
    applied && (applied.skip_first_frames != null || applied.frame_load_cap != null)
      ? {
          skip_first_frames: Math.max(0, Math.floor(Number(applied.skip_first_frames ?? 0) || 0)),
          frame_load_cap: Math.max(0, Math.floor(Number(applied.frame_load_cap ?? 0) || 0)),
        }
      : { skip_first_frames: 0, frame_load_cap: 0 };
  const [outputTrim, setOutputTrim] = useState<InputTrimState>(() => emptyTrimState(parseFps(item.media_meta?.fps)));
  const [sourceTrim, setSourceTrim] = useState<InputTrimState>(() => emptyTrimState(parseFps(item.media_meta?.fps)));
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);

  return (
    <article
      data-job-key={item.job_key || undefined}
      data-prompt-id={item.prompt_id || undefined}
      className={`work-product-row work-product-row--${layout} work-product-row--status-${statusFilterVisual(
        item.status || "pending",
      )}${isLivePreviewItem(item) ? " work-product-row--live" : ""}`}
    >
      <header
        className={`work-product-row__head${
          isRunningLiveItem(item) && item.prompt_id ? " work-product-row__head--live-metrics" : ""
        }`}
      >
        <div className="work-product-row__head-main">
          <div className="work-product-row__title">
            {item.family_slug ? (
              <strong>
                <a
                  className="work-product-family-link"
                  href={factoryMapFamilyHref(item.family_slug, { focus: "pools" })}
                  title="Open family on Factory Map"
                >
                  {item.family_slug}
                </a>
              </strong>
            ) : (
              <strong>job</strong>
            )}
            {item.job_key && item.family_slug ? (
              <a
                className="work-product-badge work-product-badge--link"
                href={factoryMapFamilyHref(item.family_slug, { focus: "job", jobKey: item.job_key })}
                title="Open this job on Factory Map"
              >
                On map
              </a>
            ) : null}
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
            // Clip rail lives on the *input* pane — advance that media, not the output.
            const intent = workbenchSourceSubmitIntent(item, {
              sourceTrim: {
                markIn: clip.mark_in_s,
                markOut: clip.mark_out_s,
                dirty: true,
                duration: sourceTrim.duration,
                fps: sourceTrim.fps,
                warning: null,
                clampedDefault: false,
              },
              sourceClipId: clip.clip_id,
              clip,
              step: "advance.extend",
            });
            if (intent && onOpenSubmit) onOpenSubmit(intent);
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
          onOpenSubmit={onOpenSubmit}
          onFocusJobKey={onFocusJobKey}
        />
      </div>
    </article>
  );
}

function workProductRowPropsEqual(
  prev: Readonly<React.ComponentProps<typeof WorkProductRowInner>>,
  next: Readonly<React.ComponentProps<typeof WorkProductRowInner>>,
): boolean {
  if (prev.layout !== next.layout) return false;
  if (
    prev.onCommitted !== next.onCommitted ||
    prev.onOpenSubmit !== next.onOpenSubmit ||
    prev.onFocusJobKey !== next.onFocusJobKey
  ) {
    return false;
  }
  const a = prev.item;
  const b = next.item;
  return (
    a.job_key === b.job_key &&
    a.status === b.status &&
    a.output_url === b.output_url &&
    a.output_relpath === b.output_relpath &&
    a.prompt_id === b.prompt_id &&
    a.error === b.error &&
    a.created_at === b.created_at &&
    a.live_from_comfy === b.live_from_comfy
  );
}

const WorkProductRow = React.memo(WorkProductRowInner, workProductRowPropsEqual);

function WorkbenchIndexFamilySwap({
  families,
  items,
  disabled,
  onSwapped,
}: {
  families: WorkProductFamilyOption[];
  items: WorkProductItem[];
  disabled?: boolean;
  onSwapped?: (nextJobKey: string | null, summary: string) => void;
}) {
  const swappable = useMemo(
    () => items.filter((it) => canSwapFamilyWorkProduct(it) && String(it.family_slug || "").trim()),
    [items],
  );
  const fromSlugs = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const it of swappable) {
      const slug = String(it.family_slug || "").trim();
      if (!slug || seen.has(slug)) continue;
      if (!familySwapTargets(families, slug).length) continue;
      seen.add(slug);
      out.push(slug);
    }
    return out;
  }, [swappable, families]);
  const [fromSlug, setFromSlug] = useState("");
  const [toSlug, setToSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const resolvedFrom = fromSlugs.includes(fromSlug) ? fromSlug : fromSlugs[0] || "";
  const toOptions = useMemo(
    () => familySwapTargets(families, resolvedFrom),
    [families, resolvedFrom],
  );
  const preferredTo = pickDefaultSwapTarget(families, resolvedFrom);
  const resolvedTo = toOptions.some((f) => f.slug === toSlug)
    ? toSlug
    : preferredTo || toOptions[0]?.slug || "";

  useEffect(() => {
    if (fromSlug !== resolvedFrom) setFromSlug(resolvedFrom);
  }, [fromSlug, resolvedFrom]);
  useEffect(() => {
    if (toSlug !== resolvedTo) setToSlug(resolvedTo);
  }, [toSlug, resolvedTo]);

  const targets = swappable.filter((it) => String(it.family_slug || "").trim() === resolvedFrom);
  if (!fromSlugs.length || !resolvedFrom || !resolvedTo) return null;

  const swapBulk = async (when: "now" | "later") => {
    const keys = targets.map((it) => String(it.job_key || "").trim()).filter(Boolean);
    if (!keys.length || busy || disabled) return;
    const ok = window.confirm(
      `Swap ${keys.length} ${resolvedFrom} job${keys.length === 1 ? "" : "s"} to ${resolvedTo}?\n\n` +
        `Each is replayed as ${resolvedTo} (same seed), then the old job is unqueued and removed. ` +
        `Running jobs are skipped.`,
    );
    if (!ok) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await swapShapeFactoryFamily({
        job_keys: keys,
        family_slug: resolvedTo,
        replace: true,
        front: when === "now",
        seed_mode: "same",
      });
      const firstNew = (res.items || [])
        .map((it) => String(it.replay?.job_key || "").trim())
        .find(Boolean) || null;
      const summary = res.failed
        ? `Swapped ${res.swapped || 0}/${keys.length} → ${resolvedTo} · ${res.failed} failed`
        : `Swapped ${res.swapped || 0} → ${resolvedTo}`;
      setMsg(summary);
      onSwapped?.(firstNew, summary);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="work-products-index__swap" role="group" aria-label="Swap family for queued jobs">
      <span className="work-products-index__swap-label">Swap</span>
      <select
        className="work-product-family-select"
        value={resolvedFrom}
        disabled={busy || disabled}
        aria-label="Family to swap from"
        onChange={(e) => setFromSlug(e.target.value)}
      >
        {fromSlugs.map((slug) => (
          <option key={slug} value={slug}>
            {slug} ({swappable.filter((it) => it.family_slug === slug).length})
          </option>
        ))}
      </select>
      <span className="work-products-index__swap-arrow" aria-hidden>
        →
      </span>
      <select
        className="work-product-family-select"
        value={resolvedTo}
        disabled={busy || disabled}
        aria-label="Family to swap to"
        onChange={(e) => setToSlug(e.target.value)}
      >
        {toOptions.map((f) => (
          <option key={f.slug} value={f.slug}>
            {f.slug}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="drt-btn"
        disabled={busy || disabled || !targets.length}
        title={`Replay ${targets.length} as ${resolvedTo} now, then retire the old jobs`}
        onClick={() => void swapBulk("now")}
      >
        {busy ? "…" : `Now (${targets.length})`}
      </button>
      <button
        type="button"
        className="drt-btn"
        disabled={busy || disabled || !targets.length}
        title={`Replay ${targets.length} as ${resolvedTo} later, then retire the old jobs`}
        onClick={() => void swapBulk("later")}
      >
        Later
      </button>
      {msg ? (
        <span className="work-products-index__swap-msg" title={msg}>
          {msg}
        </span>
      ) : null}
    </div>
  );
}

export function WorkProductsApp() {
  const deepLink = useMemo(() => parseWorkbenchDeepLink(), []);
  const queryClient = useQueryClient();
  const [layout, setLayout] = useState<RowLayout>(() => loadLayout());
  const [sort, setSort] = useState<WorkProductSort>(() => loadSort());
  // Search box is only seeded from explicit ?q= — never from job/media identity.
  const [nameQuery, setNameQuery] = useState(() => deepLink.q || "");
  const hasResourceDeepLink = Boolean(deepLink.job || deepLink.promptId || deepLink.media);
  const initialLimit = hasResourceDeepLink || deepLink.q ? 40 : 24;
  const initialHourlyOnly = hasResourceDeepLink || deepLink.q ? false : loadHourlyOnly();
  const [limit, setLimit] = useState(() => initialLimit);
  const [hourlyOnly, setHourlyOnly] = useState(() => initialHourlyOnly);
  const [statusOff, setStatusOff] = useState<Set<string>>(() => loadStatusFilterOff());
  const [markerOff, setMarkerOff] = useState<Set<string>>(() => loadMarkerFilterOff());
  const [decodeVaeFilter, setDecodeVaeFilter] = useState<DecodeVaeFilter>(() => loadDecodeVaeFilter());
  const [clearFailedBusy, setClearFailedBusy] = useState(false);
  const [clearFailedMsg, setClearFailedMsg] = useState<string | null>(null);
  const [submitModalIntent, setSubmitModalIntent] = useState<SubmitDeepLink | null>(null);
  /** First-class focused resource (job / prompt / media) — not a search string. */
  const [focusJob, setFocusJob] = useState<string | null>(() => deepLink.job);
  const [focusPromptId, setFocusPromptId] = useState<string | null>(() =>
    deepLink.job ? null : deepLink.promptId,
  );
  const [focusMedia, setFocusMedia] = useState<string | null>(() => deepLink.media);
  /** Job/prompt deep-links start with the index collapsed; browse starts open. */
  const arrivedViaItemDeepLink = Boolean(deepLink.job || deepLink.promptId);
  const [listOpen, setListOpen] = useState(() => !arrivedViaItemDeepLink);
  const [toolsOpen, setToolsOpen] = useState(() => Boolean(deepLink.q) || loadChrome().tools);
  const [filtersOpen, setFiltersOpen] = useState(() => loadChrome().filters);
  const deepLinkScrolled = useRef(false);
  const toggleJobList = useCallback(() => setListOpen((open) => !open), []);
  const showJobList = useCallback(() => setListOpen(true), []);
  const hideJobList = useCallback(() => setListOpen(false), []);
  const bulkDiscardMutation = useMutation({ mutationFn: discardShapeFactoryJob });
  const onRowCommitted = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductsRoot });
    void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductRoot });
  }, [queryClient]);
  const focusJobKey = useCallback((jobKey: string) => {
    const key = String(jobKey || "").trim();
    if (!key) return;
    setFocusJob(key);
    setFocusPromptId(null);
    window.history.replaceState(null, "", workbenchHref({ jobKey: key }));
  }, []);
  const queryState = useQuery({
    queryKey: queryKeys.shapeFactory.workProducts({ limit, hourlyOnly, family: null }),
    queryFn: () => fetchShapeFactoryWorkProducts({ limit, hourlyOnly }),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
    refetchInterval: (query) => {
      if (typeof document !== "undefined" && document.hidden) return false;
      const rows = (query.state.data?.items || []) as WorkProductItem[];
      // Live frames already poll /api/comfy/live-status. This only picks up completed outputs.
      return rows.some((it) => isLivePreviewItem(it)) ? 30_000 : false;
    },
    refetchIntervalInBackground: false,
  });
  const items = queryState.data?.items || [];
  const recentHit = useMemo(
    () => items.find((it) => workProductMatchesFocus(it, focusJob, focusPromptId)) || null,
    [items, focusJob, focusPromptId],
  );
  const listSettled = !queryState.isLoading || Boolean(queryState.error);
  const historyQuery = useQuery({
    queryKey: queryKeys.shapeFactory.workProduct({
      jobKey: focusJob,
      promptId: focusJob ? null : focusPromptId,
    }),
    queryFn: () =>
      fetchShapeFactoryWorkProduct({
        jobKey: focusJob,
        promptId: focusJob ? null : focusPromptId,
      }),
    enabled:
      arrivedViaItemDeepLink &&
      Boolean(focusJob || focusPromptId) &&
      !recentHit &&
      listSettled,
    staleTime: 30_000,
  });
  const historyItem = historyQuery.data?.ok ? historyQuery.data.item || null : null;
  const focusedItem = recentHit || historyItem || null;
  const families = queryState.data?.families || historyQuery.data?.families || [];
  const extendFamilyDefaults =
    queryState.data?.extend_family_defaults || historyQuery.data?.extend_family_defaults || {};
  const loading = queryState.isLoading;
  const refreshing = queryState.isFetching && !queryState.isLoading;
  const error = queryState.error instanceof Error ? queryState.error.message : null;
  const historyResolved =
    Boolean(recentHit) ||
    !arrivedViaItemDeepLink ||
    historyQuery.isFetched ||
    historyQuery.isError;
  const focusedLoading =
    !listOpen &&
    Boolean(focusJob || focusPromptId) &&
    !focusedItem &&
    (!listSettled || !historyResolved);
  const focusedMissing =
    !listOpen &&
    Boolean(focusJob || focusPromptId) &&
    !focusedItem &&
    listSettled &&
    historyResolved &&
    (historyQuery.data?.error === "not_found" || historyQuery.isError);

  useEffect(() => {
    const fams = queryState.data?.families || historyQuery.data?.families;
    const defaults =
      queryState.data?.extend_family_defaults || historyQuery.data?.extend_family_defaults;
    if (!fams && !defaults) return;
    rememberFamiliesFromWorkProducts({
      families: fams || [],
      extend_family_defaults: defaults || {},
    });
  }, [queryState.data, historyQuery.data]);

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

  const visibleItems = useMemo(() => {
    const rows = sortWorkProducts(
      filterWorkProductsByDecodeVae(
        filterWorkProductsByMarker(
          filterWorkProductsByStatus(
            filterWorkProductsByMedia(filterWorkProductsByName(items, nameQuery), focusMedia),
            statusOff,
          ),
          markerOff,
        ),
        decodeVaeFilter,
      ),
      sort,
    );
    if (!focusedItem) return rows;
    if (rows.some((it) => workProductMatchesFocus(it, focusedItem.job_key || null, focusedItem.prompt_id || null))) {
      return rows;
    }
    return [focusedItem, ...rows];
  }, [items, nameQuery, focusMedia, sort, statusOff, markerOff, decodeVaeFilter, focusedItem]);

  const failedVisible = useMemo(
    () => visibleItems.filter((it) => canArchiveTerminalWorkProduct(it)),
    [visibleItems],
  );

  const selectedItem = useMemo(() => {
    if (!listOpen) return focusedItem;
    const hit = visibleItems.find((it) => workProductMatchesFocus(it, focusJob, focusPromptId));
    if (hit) return hit;
    return visibleItems[0] || focusedItem || null;
  }, [visibleItems, focusJob, focusPromptId, listOpen, focusedItem]);

  const selectItem = useCallback((item: WorkProductItem) => {
    const key = String(item.job_key || "").trim();
    if (key) setFocusJob(key);
    setFocusPromptId(null);
    deepLinkScrolled.current = true;
  }, []);

  useEffect(() => {
    const key = String(selectedItem?.job_key || "").trim();
    if (!key || key === focusJob) return;
    const focusedVisible = Boolean(
      focusJob && visibleItems.some((it) => String(it.job_key || "").trim() === focusJob),
    );
    if (focusedVisible) return;
    const focusedLoaded = Boolean(
      focusJob && items.some((it) => String(it.job_key || "").trim() === focusJob),
    );
    if (focusJob && !focusedLoaded) return;
    setFocusJob(key);
    setFocusPromptId(null);
  }, [selectedItem, focusJob, items, visibleItems]);

  useEffect(() => {
    if (!listOpen) return;
    const key = String(selectedItem?.job_key || "").trim();
    if (!key) return;
    const el = document.getElementById(workbenchJobDomId(key));
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedItem?.job_key, listOpen]);

  useEffect(() => {
    if (listOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (submitModalIntent) return;
      e.preventDefault();
      showJobList();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [listOpen, showJobList, submitModalIntent]);

  const onIndexKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
    if (!visibleItems.length) return;
    e.preventDefault();
    const idx = selectedItem
      ? visibleItems.findIndex((it) => it.job_key && it.job_key === selectedItem.job_key)
      : -1;
    let next = 0;
    if (e.key === "ArrowDown") next = Math.min(visibleItems.length - 1, Math.max(0, idx) + 1);
    else if (e.key === "ArrowUp") next = Math.max(0, idx < 0 ? 0 : idx - 1);
    else if (e.key === "End") next = visibleItems.length - 1;
    const hit = visibleItems[next];
    if (hit) selectItem(hit);
  };

  // Keep the URL named after the focused resource (job / media), not the search box.
  useEffect(() => {
    const next = workbenchHref({
      jobKey: listOpen && !arrivedViaItemDeepLink ? null : focusJob,
      promptId: listOpen && !arrivedViaItemDeepLink ? null : focusJob ? null : focusPromptId,
      media: focusMedia,
      q: nameQuery.trim() || null,
    });
    if (`${window.location.pathname}${window.location.search}` === next) return;
    window.history.replaceState(null, "", next);
  }, [arrivedViaItemDeepLink, focusJob, focusPromptId, focusMedia, listOpen, nameQuery]);

  useEffect(() => {
    if (deepLinkScrolled.current || loading) return;
    const needleJob = String(focusJob || "").trim();
    const needlePid = String(focusPromptId || "").trim();
    const needleMedia = String(focusMedia || "").trim().toLowerCase();
    if (!needleJob && !needlePid && !needleMedia) return;

    let match =
      (needleJob
        ? items.find((it) => String(it.job_key || "").trim() === needleJob)
        : undefined) ||
      (needlePid
        ? items.find((it) => String(it.prompt_id || "").trim() === needlePid)
        : undefined);
    if (!match && needleMedia) {
      match = visibleItems.find((it) => workProductMatchesMedia(it, needleMedia));
    }
    if (!match) return;

    const inVisible = visibleItems.some(
      (it) =>
        (match!.job_key && it.job_key === match!.job_key) ||
        (match!.prompt_id && it.prompt_id === match!.prompt_id),
    );
    // Status/marker toggles may hide the target — clear them for exact job/prompt focus.
    if (!inVisible && (needleJob || needlePid)) {
      if (statusOff.size) {
        persistStatusFilterOff(new Set());
        setStatusOff(new Set());
      }
      if (markerOff.size) {
        persistMarkerFilterOff(new Set());
        setMarkerOff(new Set());
      }
      return;
    }
    if (!inVisible) return;

    deepLinkScrolled.current = true;
    if (!focusJob && match.job_key) setFocusJob(String(match.job_key));
    if (!listOpen) return;
    window.requestAnimationFrame(() => {
      const el = document.getElementById(workbenchJobDomId(match.job_key));
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
      el.classList.add("work-product-index-row--deep-link");
      window.setTimeout(() => el.classList.remove("work-product-index-row--deep-link"), 2400);
    });
  }, [
    focusJob,
    focusPromptId,
    focusMedia,
    items,
    listOpen,
    loading,
    markerOff.size,
    statusOff.size,
    visibleItems,
  ]);

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

  const refresh = () => queryState.refetch();

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
        await bulkDiscardMutation.mutateAsync({
          job_key: String(it.job_key || "").trim() || undefined,
          job_path: String(it.job_path || "").trim() || undefined,
          prompt_id: String(it.prompt_id || "").trim() || undefined,
          history_from_comfy: isHistoryFailureStub(it) || Boolean(it.history_from_comfy),
          reason: "user_bulk_expunged_failure",
          expunge: true,
          actor: "operator",
          source_surface: "workbench",
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
    await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductsRoot });
  };

  return (
    <PipelineScreen className="work-products">
      <PageHeader
        title="Workbench"
        actions={
          <>
            <button
              type="button"
              className={`work-products-chrome-toggle${toolsOpen || nameQuery.trim() ? " is-on" : ""}`}
              aria-expanded={toolsOpen}
              aria-controls="workbench-tools"
              title={toolsOpen ? "Hide working set, search, and layout" : "Show working set, search, and layout"}
              onClick={() => {
                setToolsOpen((open) => {
                  const next = !open;
                  persistChrome({ tools: next, filters: filtersOpen });
                  return next;
                });
              }}
            >
              Tools
            </button>
            <button
              type="button"
              className={`work-products-chrome-toggle${
                filtersOpen || hourlyOnly || statusOff.size || markerOff.size || decodeVaeFilter !== "all"
                  ? " is-on"
                  : ""
              }`}
              aria-expanded={filtersOpen}
              aria-controls="workbench-filters"
              title={filtersOpen ? "Hide filter chips" : "Show filter chips"}
              onClick={() => {
                setFiltersOpen((open) => {
                  const next = !open;
                  persistChrome({ tools: toolsOpen, filters: next });
                  return next;
                });
              }}
            >
              Filters
            </button>
            <button
              type="button"
              className="page-header__refresh"
              onClick={toggleJobList}
              disabled={listOpen && !selectedItem}
              aria-expanded={listOpen}
              aria-controls="workbench-jobs-index"
              title={listOpen ? "Collapse the job list" : "Expand the job list (Esc)"}
            >
              {listOpen ? "Hide jobs" : "Show jobs"}
            </button>
            <button
              type="button"
              className="page-header__refresh"
              onClick={() => void refresh()}
              disabled={loading && !items.length}
              aria-busy={refreshing}
            >
              <span
                className={`page-header__spinner${refreshing ? " page-header__spinner--active" : ""}`}
                aria-hidden="true"
              />
              Refresh
              {refreshing ? <span className="page-header__sr-only">Updating</span> : null}
            </button>
            {listOpen && failedVisible.length ? (
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
      {toolsOpen ? (
        <div id="workbench-tools" className="work-products-tools" role="group" aria-label="Workbench tools">
          <label className="pipeline-tray-switch" title="Worktrays coming soon — Recent is the default working set">
            <span>Working set</span>
            <select value="recent" aria-label="Workbench working set" disabled>
              <option value="recent">Recent</option>
            </select>
          </label>
          <label className="work-products-search" id="workbench-search">
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
          <div className="discovery-preview-layout-switch" role="group" aria-label="Detail layout">
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
        </div>
      ) : null}
      {listOpen && filtersOpen ? (
      <div
        id="workbench-filters"
        className="work-products-status-filters pipeline-filter-row"
        role="group"
        aria-label="Work product filters"
      >
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
        <span className="work-products-status-filters__sep" aria-hidden="true" />
        <div className="work-products-status-filters__group" role="group" aria-label="Filter by VAE decode">
          {(["all", "tiled", "plain"] as DecodeVaeFilter[]).map((opt) => {
            const on = decodeVaeFilter === opt;
            const count =
              opt === "all"
                ? items.length
                : items.filter((it) => workProductDecodeVae(it) === opt).length;
            return (
              <button
                key={`decode-vae-${opt}`}
                type="button"
                className={`work-products-status-toggle work-products-status-toggle--decode-vae${
                  on ? " is-on" : " is-off"
                }`}
                aria-pressed={on}
                title={
                  opt === "all"
                    ? "Show all decode modes"
                    : `Show only decode.vae=${opt} (${count})`
                }
                onClick={() => {
                  setDecodeVaeFilter(opt);
                  persistDecodeVaeFilter(opt);
                }}
              >
                <span className="work-products-status-toggle__label">
                  {opt === "all" ? "vae:all" : `vae:${opt}`}
                </span>
                <span className="work-products-status-toggle__count">{count}</span>
              </button>
            );
          })}
        </div>
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
      ) : null}

      <div className={`work-products-shell${listOpen ? "" : " work-products-shell--list-collapsed"}`}>
        {error && listOpen ? <div className="work-products-error">{error}</div> : null}
        {focusedLoading ? <div className="work-products-empty">Loading…</div> : null}
        {loading && !items.length && listOpen ? <div className="work-products-empty">Loading…</div> : null}
        {!loading && !error && !items.length && listOpen && !focusedItem ? (
          <div className="work-products-empty">
            {hourlyOnly ? "No hourly work products found." : "No work products found."}
          </div>
        ) : null}
        {!loading && !error && items.length && !visibleItems.length && listOpen ? (
          <div className="work-products-empty">
            {focusMedia
              ? `No loaded work products reference “${focusMedia}”.`
              : nameQuery.trim()
                ? `No work products match “${nameQuery.trim()}”.`
                : "No work products match the selected filters."}
          </div>
        ) : null}

        {listOpen && visibleItems.length ? (
          <nav
            id="workbench-jobs-index"
            className="work-products-index"
            aria-label="Workbench jobs"
            onKeyDown={onIndexKeyDown}
          >
            <div className="work-products-index__toolbar">
              <button
                type="button"
                className="work-products-index__collapse"
                onClick={hideJobList}
                disabled={!selectedItem}
                title="Collapse the job list"
                aria-expanded={true}
                aria-controls="workbench-jobs-index"
              >
                ‹
              </button>
              <span className="work-products-index__toolbar-label">Jobs</span>
              <label className="work-products-limit work-products-limit--index">
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
              <WorkbenchIndexFamilySwap
                families={families}
                items={visibleItems}
                onSwapped={(nextKey) => {
                  void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductsRoot });
                  void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductRoot });
                  void queryClient.invalidateQueries({ queryKey: queryKeys.queue.snapshot });
                  void queryClient.invalidateQueries({ queryKey: queryKeys.queue.ledgerRoot });
                  if (nextKey) focusJobKey(nextKey);
                }}
              />
            </div>
            <div className="work-products-index__list" role="listbox" aria-label="Recent jobs">
              {visibleItems.map((item) => (
                <WorkProductIndexRow
                  key={item.job_key}
                  item={item}
                  selected={Boolean(selectedItem && item.job_key === selectedItem.job_key)}
                  onSelect={selectItem}
                />
              ))}
            </div>
          </nav>
        ) : !listOpen ? (
          <button
            type="button"
            id="workbench-jobs-index"
            className="work-products-index-rail"
            onClick={showJobList}
            title="Expand the job list (Esc)"
            aria-expanded={false}
          >
            <span className="work-products-index-rail__chevron" aria-hidden>
              ›
            </span>
            <span className="work-products-index-rail__label">Jobs</span>
            {visibleItems.length || items.length ? (
              <span className="work-products-index-rail__count">
                {visibleItems.length || items.length}
              </span>
            ) : null}
          </button>
        ) : null}
        {selectedItem ? (
          <div className="work-products-detail">
            <WorkProductRow
              key={selectedItem.job_key}
              item={selectedItem}
              layout={layout}
              families={families}
              extendFamilyDefaults={extendFamilyDefaults}
              onOpenSubmit={setSubmitModalIntent}
              onCommitted={onRowCommitted}
              onFocusJobKey={focusJobKey}
            />
          </div>
        ) : focusedMissing ? (
          <div className="work-products-empty">
            This job isn’t in factory history.
          </div>
        ) : listOpen && visibleItems.length ? (
          <div className="work-products-detail">
            <div className="work-products-empty">Select a job from the list.</div>
          </div>
        ) : null}
      </div>
      <SubmitComposerModal
        intent={submitModalIntent}
        onClose={() => setSubmitModalIntent(null)}
        onSubmitted={() => {
          void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductsRoot });
          void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductRoot });
          void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.submitAttemptsRoot });
        }}
      />
    </PipelineScreen>
  );
}
