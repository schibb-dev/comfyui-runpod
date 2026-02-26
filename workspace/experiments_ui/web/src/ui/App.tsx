import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { comfyClear, createNextExperiment, fetchExperimentRuns, fetchExperiments, fetchQueue, fetchRunsMulti } from "./api";
import type { ExperimentSummary, ExperimentsRelations, QueueResponse, RunStatus, RunsItem } from "./types";
import { cmp, fmt, uniq } from "./utils";
import { HintCallout, useHintCallout } from "./hintCallout";
import { ListHeaderControls } from "./ListHeaderControls";
import { FilterBox } from "./FilterBox";
import { RUNS_CACHE_TTL_MS, runsCacheClear, runsCacheGetStats, runsCacheReadMany, runsCacheWriteFromMulti } from "./idbRunsCache";
import { QueueViewer } from "./QueueViewer";
import {
  DEFAULT_WIP_PARAMS,
  WipMainContent,
  WipSidebarContent,
  nextId,
  paramsFromPlanned,
  paramsFromRun,
  sweepFromParamStrings,
} from "./WipTuneLauncher";
import { ExperimentList } from "./ExperimentList";
import type { ExperimentListRunsEntry } from "./ExperimentList";
import { ExperimentDetailPanel } from "./ExperimentDetailPanel";
import type { WipFormParams } from "./WipTuneLauncher";
import type { CreateSource, WipPlannedExperiment } from "./types";
import { createExperimentFromWip, fetchWip } from "./api";
import { DeviceProvider } from "./viewport";

type ExpandedMedia =
  | { kind: "video"; title: string; url: string }
  | { kind: "image"; title: string; url: string };

type Axis = {
  key: string;
  label: string;
  get: (r: RunsItem) => unknown;
  virtual?: boolean;
};

function buildAxes(runs: RunsItem[]): Axis[] {
  const paramKeys = uniq(
    runs.flatMap((r) => Object.keys(r.params ?? {})).sort((a, b) => a.localeCompare(b))
  );

  const axes: Axis[] = [
    { key: "exp_id", label: "exp_id", get: (r) => r.exp_id },
    { key: "run_id", label: "run_id", get: (r) => r.run_id },
    { key: "run_key", label: "run_key", get: (r) => `${r.exp_id}::${r.run_id}` },
    { key: "status", label: "status", get: (r) => r.status },
    { key: "status_str", label: "status_str", get: (r) => r.status_str ?? "" },
    { key: "prompt_id", label: "prompt_id", get: (r) => r.prompt_id ?? "" },
    {
      key: "metrics.generation_time_sec",
      label: "gen_time_sec",
      get: (r) => (r.metrics ?? ({} as Record<string, unknown>))["generation_time_sec"],
    },
    {
      key: "metrics.wait_history_sec",
      label: "wait_sec",
      get: (r) => (r.metrics ?? ({} as Record<string, unknown>))["wait_history_sec"],
    },
    {
      key: "metrics.submit_http_sec",
      label: "submit_sec",
      get: (r) => (r.metrics ?? ({} as Record<string, unknown>))["submit_http_sec"],
    },
    {
      key: "metrics.submitted_at",
      label: "submitted_at",
      get: (r) => (r.metrics ?? ({} as Record<string, unknown>))["submitted_at"],
    },
    {
      key: "metrics.history_collected_at",
      label: "done_at",
      get: (r) => (r.metrics ?? ({} as Record<string, unknown>))["history_collected_at"],
    },
    {
      key: "primary_video",
      label: "primary_video",
      get: (r) => r.primary_video?.relpath ?? "",
    },
    {
      key: "primary_image",
      label: "primary_image",
      get: (r) => r.primary_image?.relpath ?? "",
    },
  ];

  for (const k of paramKeys) {
    axes.push({
      key: `params.${k}`,
      label: k,
      get: (r) => (r.params ?? {})[k],
    });
  }

  return axes;
}

type FacetSectionProps = {
  title: React.ReactNode;
  open: boolean;
  onToggle: () => void;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
};

function FacetSection({ title, open, onToggle, meta, actions, children }: FacetSectionProps) {
  const contentId = useId();
  return (
    <div className="facet">
      <div className="facet-header">
        <button type="button" className="facet-toggle" onClick={onToggle} aria-expanded={open} aria-controls={contentId}>
          <span className={`facet-caret ${open ? "open" : ""}`} aria-hidden="true">
            ▸
          </span>
          <span className="facet-title">{title}</span>
        </button>
        {meta ? <div className="facet-meta">{meta}</div> : null}
        {actions ? <div className="facet-actions">{actions}</div> : null}
      </div>
      <div id={contentId} className={`facet-body ${open ? "open" : ""}`}>
        <div className="facet-body-inner">{children}</div>
      </div>
    </div>
  );
}

type PagerState = { page: number; pageSize: number };

function clampInt(n: number, min: number, max: number): number {
  const nn = Math.trunc(n);
  if (!Number.isFinite(nn)) return min;
  return Math.max(min, Math.min(max, nn));
}

function pageSlice<T>(
  items: T[],
  page: number,
  pageSize: number
): { pageItems: T[]; pageCount: number; total: number; page: number } {
  const total = items.length;
  const ps = Math.max(1, Math.trunc(pageSize) || 1);
  const pageCount = Math.max(1, Math.ceil(total / ps));
  const p = clampInt(page, 1, pageCount);
  const start = (p - 1) * ps;
  const end = start + ps;
  return { pageItems: items.slice(start, end), pageCount, total, page: p };
}

type PagerProps = {
  state: PagerState;
  pageCount: number;
  total: number;
  onChange: (next: PagerState) => void;
  pageSizeOptions?: number[];
};

function Pager({ state, pageCount, total, onChange, pageSizeOptions }: PagerProps) {
  const opts = pageSizeOptions?.length ? pageSizeOptions : [7, 15, 21, 50, 100];
  const page = clampInt(state.page, 1, Math.max(1, pageCount));
  const canPrev = page > 1;
  const canNext = page < pageCount;
  const showNav = pageCount > 1;
  return (
    <div className="pager" role="navigation" aria-label="Pagination">
      {showNav ? (
        <>
          <button
            type="button"
            className="pager-nav"
            onClick={() => onChange({ ...state, page: Math.max(1, page - 1) })}
            disabled={!canPrev}
            aria-label="Previous page"
            title="Previous page"
          >
            ‹
          </button>
          <span className="pager-text">
            <span className="mono">{page}</span> / <span className="mono">{pageCount}</span>{" "}
            <span className="pager-total" style={{ color: "var(--muted)" }}>
              ({total} total)
            </span>
          </span>
          <button
            type="button"
            className="pager-nav"
            onClick={() => onChange({ ...state, page: Math.min(pageCount, page + 1) })}
            disabled={!canNext}
            aria-label="Next page"
            title="Next page"
          >
            ›
          </button>
          <span style={{ flex: "1 1 auto" }} />
        </>
      ) : (
        <span style={{ flex: "1 1 auto" }} />
      )}
      <label className="pager-label">
        <span className="pager-size-text pager-size-long">Page Size</span>
        <span className="pager-size-text pager-size-short">#</span>
        <select
          value={String(state.pageSize)}
          onChange={(e) => onChange({ page: 1, pageSize: Number(e.target.value) || opts[0] })}
          className="pager-size-select"
          aria-label="Page size"
          title="Page size"
        >
          {opts.map((n) => (
            <option key={n} value={String(n)}>
              {n}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

type ZoomPanSize = { w: number; h: number };

type UseZoomPanOpts = {
  onTwoFingerSwipe?: (dx: number, dy: number) => void;
};

type ZoomPanTransform = { zoom: number; pan: { x: number; y: number } };

function useZoomPan(opts: UseZoomPanOpts = {}) {
  const [zoom, setZoom] = useState<number>(1);
  const [pan, setPan] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [stageSize, setStageSize] = useState<ZoomPanSize>({ w: 0, h: 0 });
  const [mediaSize, setMediaSize] = useState<ZoomPanSize>({ w: 0, h: 0 });
  const didInitialFitRef = useRef<boolean>(false);
  const initialFitTimerRef = useRef<number | null>(null);
  const stageSizeRef = useRef<ZoomPanSize>({ w: 0, h: 0 });
  const mediaSizeRef = useRef<ZoomPanSize>({ w: 0, h: 0 });

  const panDrag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const pinch = useRef<{ d: number; z: number; cx: number; cy: number; px: number; py: number } | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const navSwipe = useRef<{ cx: number; cy: number; d: number; didZoom: boolean; lastCx: number; lastCy: number } | null>(null);

  const roRef = useRef<ResizeObserver | null>(null);
  const stageElRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useCallback((el: HTMLDivElement | null) => {
    if (stageElRef.current === el) return;
    if (roRef.current && stageElRef.current) roRef.current.disconnect();
    stageElRef.current = el;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect();
      setStageSize({ w: Math.max(0, r.width), h: Math.max(0, r.height) });
    });
    ro.observe(el);
    roRef.current = ro;
  }, []);

  useEffect(() => {
    return () => {
      if (roRef.current) roRef.current.disconnect();
      roRef.current = null;
    };
  }, []);

  const mediaStyle = useMemo(() => {
    return {
      width: mediaSize.w ? `${mediaSize.w}px` : undefined,
      height: mediaSize.h ? `${mediaSize.h}px` : undefined,
      // Keep pan in screen px (not magnified by zoom).
      transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
    } as React.CSSProperties;
  }, [mediaSize.w, mediaSize.h, pan.x, pan.y, zoom]);

  function resetGestures() {
    panDrag.current = null;
    pinch.current = null;
    navSwipe.current = null;
    pointers.current.clear();
  }

  function fitZoom(): number {
    const sw = stageSizeRef.current.w;
    const sh = stageSizeRef.current.h;
    const mw = mediaSizeRef.current.w;
    const mh = mediaSizeRef.current.h;
    if (!sw || !sh || !mw || !mh) return 1;
    const z = Math.min(sw / mw, sh / mh);
    return Number.isFinite(z) && z > 0 ? z : 1;
  }

  function fitToViewport() {
    resetGestures();
    didInitialFitRef.current = true;
    if (initialFitTimerRef.current != null) {
      window.clearTimeout(initialFitTimerRef.current);
      initialFitTimerRef.current = null;
    }
    setZoom(fitZoom());
    // With zoompan media aligned to stage top-left, pan=(0,0) is always true origin.
    setPan({ x: 0, y: 0 });
  }

  function actualSize() {
    resetGestures();
    didInitialFitRef.current = true;
    if (initialFitTimerRef.current != null) {
      window.clearTimeout(initialFitTimerRef.current);
      initialFitTimerRef.current = null;
    }
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }

  function clampZoom(z: number): number {
    const minZ = Math.min(1, fitZoom());
    return Math.max(minZ, Math.min(6, z));
  }

  function setTransform(t: ZoomPanTransform) {
    resetGestures();
    setZoom(clampZoom(Number(t.zoom) || 1));
    setPan({ x: Number(t.pan?.x) || 0, y: Number(t.pan?.y) || 0 });
  }

  function onWheelZoom(e: React.WheelEvent) {
    e.preventDefault();
    const dy = e.deltaY;
    const factor = Math.exp(-dy * 0.0012);
    setZoom((z) => clampZoom(z * factor));
  }

  // Initial auto-fit when stage+media sizes settle (first open only).
  useEffect(() => {
    stageSizeRef.current = stageSize;
    mediaSizeRef.current = mediaSize;
    if (didInitialFitRef.current) return;
    if (!stageSize.w || !stageSize.h || !mediaSize.w || !mediaSize.h) return;

    // Debounce to allow layout to settle (e.g. stage aspect-ratio changes after media metadata).
    if (initialFitTimerRef.current != null) window.clearTimeout(initialFitTimerRef.current);
    initialFitTimerRef.current = window.setTimeout(() => {
      initialFitTimerRef.current = null;
      if (didInitialFitRef.current) return;
      fitToViewport();
    }, 80);
  }, [stageSize.w, stageSize.h, mediaSize.w, mediaSize.h]);

  useEffect(() => {
    return () => {
      if (initialFitTimerRef.current != null) {
        window.clearTimeout(initialFitTimerRef.current);
        initialFitTimerRef.current = null;
      }
    };
  }, []);

  function onPointerDown(e: React.PointerEvent) {
    // Don't start gestures when interacting with UI controls inside the stage.
    const t = e.target as HTMLElement | null;
    const tag = (t?.tagName ?? "").toLowerCase();
    if (t?.closest("[data-zp-ui]") || ["button", "a", "input", "select", "textarea"].includes(tag)) return;
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 1) {
      panDrag.current = { x: pan.x, y: pan.y, px: e.clientX, py: e.clientY };
    }
    if (pointers.current.size === 2) {
      const pts = [...pointers.current.values()];
      const a = pts[0];
      const b = pts[1];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy);
      const cx = (a.x + b.x) / 2;
      const cy = (a.y + b.y) / 2;
      pinch.current = { d, z: zoom, cx, cy, px: pan.x, py: pan.y };
      navSwipe.current = { cx, cy, d, didZoom: false, lastCx: cx, lastCy: cy };
    }
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 1 && panDrag.current) {
      const dx = e.clientX - panDrag.current.px;
      const dy = e.clientY - panDrag.current.py;
      setPan({ x: panDrag.current.x + dx, y: panDrag.current.y + dy });
      return;
    }
    if (pointers.current.size === 2) {
      const pts = [...pointers.current.values()];
      const a = pts[0];
      const b = pts[1];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy);
      const cx = (a.x + b.x) / 2;
      const cy = (a.y + b.y) / 2;
      if (navSwipe.current) {
        navSwipe.current.lastCx = cx;
        navSwipe.current.lastCy = cy;
      }
      if (!pinch.current) {
        pinch.current = { d, z: zoom, cx, cy, px: pan.x, py: pan.y };
        return;
      }
      const base = pinch.current;
      const ratio = d / Math.max(1, base.d);
      // If the two-finger gesture is mostly translation (ratio ~ 1), treat as swipe-nav candidate.
      if (Math.abs(ratio - 1) < 0.06) return;
      const nextZ = clampZoom(base.z * ratio);
      setZoom(nextZ);
      // keep it simple: keep pan from the start of pinch (no fancy focal-point correction yet)
      setPan({ x: base.px, y: base.py });
      if (navSwipe.current) navSwipe.current.didZoom = true;
    }
  }

  function onPointerUp(e: React.PointerEvent) {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    if (pointers.current.size === 0) panDrag.current = null;

    // Interpret two-finger swipe navigation when the last pointer lifts.
    if (pointers.current.size === 0 && navSwipe.current) {
      const base = navSwipe.current;
      navSwipe.current = null;
      if (!base.didZoom && opts.onTwoFingerSwipe) {
        const dx = base.lastCx - base.cx;
        const dy = base.lastCy - base.cy;
        opts.onTwoFingerSwipe(dx, dy);
      }
      return;
    }
  }

  function onPointerCancel(e: React.PointerEvent) {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    if (pointers.current.size === 0) panDrag.current = null;
    navSwipe.current = null;
  }

  return {
    stageRef,
    stageSize,
    mediaSize,
    setMediaSize,
    zoom,
    pan,
    fitToViewport,
    actualSize,
    mediaStyle,
    setTransform,
    stageProps: {
      onWheel: onWheelZoom,
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel,
    },
  };
}

type PairZoomPaneProps = {
  run: RunsItem | null;
  axes: Axis[];
  pool: RunsItem[];
  autoPlay: boolean;
  loopPlayback: boolean;
  onOpenExpanded: (r: RunsItem) => void;
  getOtherTransform?: () => ZoomPanTransform | null;
  onTransformChange?: (t: ZoomPanTransform) => void;
  /** When set (e.g. Match sync on), apply this transform and do not echo back to parent. */
  externalTransform?: ZoomPanTransform | null;
  onVideoEl?: (el: HTMLVideoElement | null) => void;
  getOtherVideoEl?: () => HTMLVideoElement | null;
  matchOn?: boolean;
  syncOn?: boolean;
  onMatchToggle?: () => void;
  onSyncToggle?: () => void;
};

function PairZoomPane({
  run,
  axes,
  pool,
  autoPlay,
  loopPlayback,
  onOpenExpanded,
  getOtherTransform,
  onTransformChange,
  externalTransform,
  onVideoEl,
  getOtherVideoEl,
  matchOn = false,
  syncOn = false,
  onMatchToggle,
  onSyncToggle,
}: PairZoomPaneProps) {
  function eqLocal(a: unknown, b: unknown): boolean {
    if (a === b) return true;
    if (a == null || b == null) return false;
    const na = typeof a === "number" ? a : Number(a);
    const nb = typeof b === "number" ? b : Number(b);
    if (Number.isFinite(na) && Number.isFinite(nb)) return na === nb;
    try {
      return JSON.stringify(a) === JSON.stringify(b);
    } catch {
      return String(a) === String(b);
    }
  }

  const [activeAxis, setActiveAxis] = useState<number>(0);
  const [cursor, setCursor] = useState<Record<string, unknown>>({});
  const [curRunKey, setCurRunKey] = useState<string>("");

  const axesKey = useMemo(() => axes.map((a) => a.label).join("|"), [axes]);
  const anchorKey = useMemo(() => (run ? `${run.exp_id}::${run.run_id}` : ""), [run?.exp_id, run?.run_id]);

  function getAxisValue(r: RunsItem, axis: Axis): unknown {
    return axis.get(r);
  }

  const lockedParams = useMemo(() => {
    const a = run;
    if (!a) return {};
    const lock: Record<string, unknown> = { ...(a.params ?? {}) };
    for (const axis of axes) {
      if (axis.key.startsWith("params.")) delete lock[axis.label];
    }
    for (const [k, v] of Object.entries(lock)) {
      if (v == null) delete lock[k];
    }
    return lock;
  }, [anchorKey, axesKey]);

  // Init cursor from anchor.
  useEffect(() => {
    if (!run) return;
    setActiveAxis(0);
    setCursor((prev) => {
      const next: Record<string, unknown> = { ...prev };
      for (const axis of axes) next[axis.label] = getAxisValue(run, axis);
      return next;
    });
    setCurRunKey(anchorKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorKey, axesKey]);

  function candidatesForAxis(axis: Axis): RunsItem[] {
    return pool.filter((r) => {
      const p = r.params ?? {};
      for (const [k, v] of Object.entries(lockedParams)) {
        if (!eqLocal(p[k], v)) return false;
      }
      for (const ax of axes) {
        if (ax === axis) continue;
        if (!eqLocal(getAxisValue(r, ax), cursor[ax.label])) return false;
      }
      return true;
    });
  }

  function valuesForAxis(axis: Axis): unknown[] {
    const vals = uniq(candidatesForAxis(axis).map((r) => getAxisValue(r, axis))).filter((v) => v != null);
    return vals.sort(cmp);
  }

  function pickRunForCursor(): RunsItem | null {
    const rr = pool.filter((r) => {
      const p = r.params ?? {};
      for (const [k, v] of Object.entries(lockedParams)) {
        if (!eqLocal(p[k], v)) return false;
      }
      for (const axis of axes) {
        if (!eqLocal(getAxisValue(r, axis), cursor[axis.label])) return false;
      }
      return true;
    });
    return rr[0] ?? null;
  }

  function nearestRunForCursor(): RunsItem | null {
    if (!pool.length) return null;
    let best: RunsItem | null = null;
    let bestScore = -1;
    const activeAxisObj = axes[activeAxis];
    for (const r of pool) {
      const p = r.params ?? {};
      let ok = true;
      for (const [k, v] of Object.entries(lockedParams)) {
        if (!eqLocal(p[k], v)) {
          ok = false;
          break;
        }
      }
      if (!ok) continue;
      let score = 0;
      for (const axis of axes) {
        if (eqLocal(getAxisValue(r, axis), cursor[axis.label])) score += 1;
      }
      if (activeAxisObj && eqLocal(getAxisValue(r, activeAxisObj), cursor[activeAxisObj.label])) score += 0.25;
      if (score > bestScore) {
        bestScore = score;
        best = r;
      }
    }
    return best;
  }

  // Keep current run in sync with cursor; snap if no match.
  useEffect(() => {
    if (!run || !axes.length) return;
    const exact = pickRunForCursor();
    const next = exact ?? nearestRunForCursor();
    if (next) {
      const key = `${next.exp_id}::${next.run_id}`;
      setCurRunKey(key);
      if (!exact) {
        setCursor((prev) => {
          const out = { ...prev };
          for (const axis of axes) out[axis.label] = getAxisValue(next, axis);
          return out;
        });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorKey, axesKey, activeAxis, JSON.stringify(cursor), JSON.stringify(lockedParams), pool.length]);

  const activeAxisObj = axes[activeAxis] ?? null;
  const activeAxisName = activeAxisObj?.label ?? "";
  const activeVals = useMemo(
    () => (activeAxisObj ? valuesForAxis(activeAxisObj) : []),
    [activeAxisObj?.label, axesKey, JSON.stringify(cursor), JSON.stringify(lockedParams), pool.length]
  );
  const activeValue = activeAxisName ? cursor[activeAxisName] : "";

  function moveAxis(delta: number) {
    if (!axes.length) return;
    setActiveAxis((i) => {
      const n = axes.length;
      return (i + delta + n) % n;
    });
  }

  function moveValue(delta: number) {
    if (!activeAxisObj) return;
    if (!activeVals.length) return;
    const cur = cursor[activeAxisName];
    let idx = activeVals.findIndex((v) => eqLocal(v, cur));
    if (idx < 0) idx = 0;
    const next = activeVals[(idx + delta + activeVals.length) % activeVals.length];
    setCursor((prev) => ({ ...prev, [activeAxisName]: next }));
  }

  function moveValueForAxis(axis: Axis, axisIndex: number, delta: number) {
    const vals = valuesForAxis(axis);
    if (vals.length <= 1) return;
    setActiveAxis(axisIndex);
    setCursor((prev) => {
      const cur = prev[axis.label];
      let idx = vals.findIndex((v) => eqLocal(v, cur));
      if (idx < 0) idx = 0;
      const next = vals[(idx + delta + vals.length) % vals.length];
      return { ...prev, [axis.label]: next };
    });
  }

  const zp = useZoomPan({
    onTwoFingerSwipe: (dx, dy) => {
      const adx = Math.abs(dx);
      const ady = Math.abs(dy);
      const th = 52;
      if (adx < th && ady < th) return;
      if (ady > adx) moveAxis(dy < 0 ? -1 : 1);
      else moveValue(dx < 0 ? -1 : 1);
    },
  });

  const ignoreNextTransformEchoRef = useRef<boolean>(false);

  useEffect(() => {
    if (externalTransform == null) return;
    const { zoom, pan } = zp;
    const same =
      Math.abs((zoom ?? 0) - (externalTransform.zoom ?? 0)) < 1e-6 &&
      Math.abs((pan?.x ?? 0) - (externalTransform.pan?.x ?? 0)) < 1e-6 &&
      Math.abs((pan?.y ?? 0) - (externalTransform.pan?.y ?? 0)) < 1e-6;
    if (same) return;
    ignoreNextTransformEchoRef.current = true;
    zp.setTransform(externalTransform);
  }, [externalTransform]);

  useEffect(() => {
    if (ignoreNextTransformEchoRef.current) {
      ignoreNextTransformEchoRef.current = false;
      return;
    }
    onTransformChange?.({ zoom: zp.zoom, pan: zp.pan });
  }, [onTransformChange, zp.zoom, zp.pan]);

  const [showAxisHud, setShowAxisHud] = useState<boolean>(false);
  const axisHudRef = useRef<HTMLDivElement | null>(null);
  const pairScoreboardRef = useRef<HTMLDivElement | null>(null);
  const [pairScoreboxW, setPairScoreboxW] = useState<number>(0);
  const myVideoRef = useRef<HTMLVideoElement | null>(null);
  const syncRafRef = useRef<number>(0);
  const setMyVideoEl = useCallback(
    (el: HTMLVideoElement | null) => {
      myVideoRef.current = el;
      onVideoEl?.(el);
    },
    [onVideoEl],
  );

  const syncPlaybackFromOther = useCallback(
    (opts: { chase?: boolean } = {}) => {
      const other = getOtherVideoEl?.();
      const me = myVideoRef.current;
      if (!other || !me) return;

      if (syncRafRef.current) {
        cancelAnimationFrame(syncRafRef.current);
        syncRafRef.current = 0;
      }

      const tgt = Number(other.currentTime) || 0;
      try {
        if (Number.isFinite(tgt)) me.currentTime = tgt;
      } catch {
        // ignore (may fail if not seekable yet)
      }

      const baseRate = Number(other.playbackRate) || 1;
      const otherPlaying = !other.paused && !other.ended;

      if (!otherPlaying) {
        me.playbackRate = baseRate;
        me.pause();
        return;
      }

      // If other is playing, optionally chase it briefly so we converge.
      me.playbackRate = baseRate;
      void me.play().catch(() => {});
      if (!opts.chase) return;

      const t0 = performance.now();
      const maxMs = 1500;
      const hardSeekS = 0.6;
      const epsS = 0.03;

      const tick = () => {
        const now = performance.now();
        const ms = now - t0;
        if (ms > maxMs) {
          me.playbackRate = baseRate;
          syncRafRef.current = 0;
          return;
        }
        if (other.paused || other.ended || me.paused) {
          me.playbackRate = baseRate;
          syncRafRef.current = 0;
          return;
        }

        const diff = other.currentTime - me.currentTime; // + => me behind
        if (Math.abs(diff) > hardSeekS) {
          try {
            me.currentTime = other.currentTime;
          } catch {
            // ignore
          }
        }

        if (diff > 0.15) me.playbackRate = Math.min(1.35, baseRate * 1.25);
        else if (diff > 0.06) me.playbackRate = Math.min(1.2, baseRate * 1.1);
        else if (diff < -0.15) me.playbackRate = Math.max(0.65, baseRate * 0.75);
        else if (diff < -0.06) me.playbackRate = Math.max(0.8, baseRate * 0.9);
        else if (Math.abs(diff) < epsS) me.playbackRate = baseRate;

        syncRafRef.current = requestAnimationFrame(tick);
      };
      syncRafRef.current = requestAnimationFrame(tick);
    },
    [getOtherVideoEl],
  );

  useEffect(() => {
    return () => {
      if (syncRafRef.current) {
        cancelAnimationFrame(syncRafRef.current);
        syncRafRef.current = 0;
      }
    };
  }, []);

  const currentRun = useMemo(() => {
    if (!curRunKey) return run;
    const [exp_id, run_id] = curRunKey.split("::");
    return pool.find((r) => r.exp_id === exp_id && r.run_id === run_id) ?? run;
  }, [curRunKey, pool, anchorKey]);

  const title = currentRun
    ? `${currentRun.exp_id}::${currentRun.run_id}${currentRun.status ? ` (${currentRun.status})` : ""}`
    : "Select a run";

  const mediaAR = zp.mediaSize.w && zp.mediaSize.h ? `${Math.round(zp.mediaSize.w)} / ${Math.round(zp.mediaSize.h)}` : "";

  // Equalize scorebox widths (use widest box), but clamp so HUD stays narrow.
  useEffect(() => {
    const el = pairScoreboardRef.current;
    if (!el) return;
    const raf = requestAnimationFrame(() => {
      const boxes = Array.from(el.querySelectorAll<HTMLElement>(".scorebox"));
      if (!boxes.length) return;
      let max = 0;
      for (const b of boxes) max = Math.max(max, b.scrollWidth);
      const clamped = Math.max(72, Math.min(120, Math.ceil(max + 2)));
      setPairScoreboxW((prev) => (prev === clamped ? prev : clamped));
    });
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [axesKey, activeAxis, JSON.stringify(cursor), curRunKey]);

  // Pair view uses a compact stage aligned toward the seam; ensure we re-fit once
  // after the layout/aspect-ratio has settled so we don't leave gutters or clip.
  const didPairRefitRef = useRef<string>("");
  useEffect(() => {
    if (!currentRun) return;
    if (!zp.stageSize.w || !zp.stageSize.h || !zp.mediaSize.w || !zp.mediaSize.h) return;
    // Only if user hasn't panned/zoomed away.
    if (Math.abs(zp.pan.x) > 0.5 || Math.abs(zp.pan.y) > 0.5) return;
    const key = `${currentRun.exp_id}::${currentRun.run_id}`;
    if (didPairRefitRef.current === key) return;
    const t = window.setTimeout(() => {
      if (Math.abs(zp.pan.x) > 0.5 || Math.abs(zp.pan.y) > 0.5) return;
      didPairRefitRef.current = key;
      zp.fitToViewport();
    }, 180);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRun?.exp_id, currentRun?.run_id, zp.stageSize.w, zp.stageSize.h, zp.mediaSize.w, zp.mediaSize.h]);

  return (
    <div className="pair-card">
      <div className="pair-title">{title}</div>
      {currentRun ? (
        <div
          className="slide-stage compact pair-zoom-stage"
          ref={zp.stageRef}
          style={mediaAR ? ({ ["--media-ar" as any]: mediaAR } as any) : undefined}
          {...zp.stageProps}
          onMouseEnter={(e) => {
            const r = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
            const x = e.clientX - r.left;
            const hudW = axisHudRef.current?.getBoundingClientRect().width ?? 0;
            const th = Math.min(r.width, Math.max(44, Math.min(220, hudW + 20)));
            setShowAxisHud(x < th);
          }}
          onMouseMove={(e) => {
            const r = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
            const x = e.clientX - r.left;
            const hudW = axisHudRef.current?.getBoundingClientRect().width ?? 0;
            const th = Math.min(r.width, Math.max(44, Math.min(220, hudW + 20)));
            setShowAxisHud(x < th);
          }}
          onMouseLeave={() => setShowAxisHud(false)}
        >
          <div className="overlay-hotzone" data-zp-ui aria-hidden="true">
            <button
              type="button"
              className="overlay-icon overlay-expand"
              data-zp-ui
              onClick={(e) => {
                e.stopPropagation();
                onOpenExpanded(currentRun);
              }}
              title="Expand"
              aria-label="Expand"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
                <path
                  d="M9 5H5v4M15 5h4v4M9 19H5v-4M15 19h4v-4"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </div>
          {axes.length ? (
            <div ref={axisHudRef} className={`slide-hud ${showAxisHud ? "show" : ""}`} data-zp-ui>
              <div className="hud-row">
                <div className="hud-left">
                  <div
                    className="scoreboard"
                    ref={pairScoreboardRef}
                    style={{ ["--scorebox-w" as any]: pairScoreboxW ? `${pairScoreboxW}px` : undefined }}
                  >
                    {axes.map((a, i) => (
                      <div
                        key={a.label}
                        className={`scorebox ${i === activeAxis ? "active" : ""}`}
                        onClick={() => setActiveAxis(i)}
                        role="button"
                        tabIndex={0}
                        title="Select axis"
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            setActiveAxis(i);
                          }
                        }}
                      >
                        <div className="score-label">{a.label}</div>
                        <div className="score-value">{fmt(cursor[a.label])}</div>
                        {valuesForAxis(a).length > 1 ? (
                          <div className="score-arrows" aria-label={`Change ${a.label}`}>
                            <button
                              type="button"
                              title={`Previous ${a.label}`}
                              aria-label={`Previous ${a.label}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                moveValueForAxis(a, i, -1);
                              }}
                            >
                              ←
                            </button>
                            <button
                              type="button"
                              title={`Next ${a.label}`}
                              aria-label={`Next ${a.label}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                moveValueForAxis(a, i, 1);
                              }}
                            >
                              →
                            </button>
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {currentRun.primary_video?.url ? (
            <video
              className="slide-media zoompan"
              ref={setMyVideoEl}
              controls
              loop={loopPlayback}
              autoPlay={autoPlay}
              muted={autoPlay}
              playsInline
              preload="metadata"
              src={currentRun.primary_video.url ?? undefined}
              style={{ ...zp.mediaStyle }}
              onLoadedMetadata={(e) => {
                const v = e.currentTarget;
                zp.setMediaSize({ w: v.videoWidth, h: v.videoHeight });
                // On navigation, auto-sync playback state to the other pane (preserve pan/zoom).
                syncPlaybackFromOther({ chase: true });
                if (autoPlay) void v.play().catch(() => {});
              }}
            />
          ) : currentRun.primary_image?.url ? (
            <img
              className="slide-media zoompan"
              alt={currentRun.run_id}
              src={currentRun.primary_image.url ?? undefined}
              style={{ ...zp.mediaStyle }}
              onLoad={(e) => {
                const img = e.currentTarget;
                zp.setMediaSize({ w: img.naturalWidth, h: img.naturalHeight });
              }}
            />
          ) : (
            <div style={{ color: "var(--muted)", fontSize: 12, padding: 10 }}>No media yet.</div>
          )}
          <div className="nav-fab" data-zp-ui role="navigation" aria-label="Pair navigation">
            <div className="nav-status mono" aria-live="polite">
              {activeAxisName ? `${activeAxisName}  ${fmt(activeValue ?? "")}` : ""}
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              <button onClick={() => zp.fitToViewport()} title="Fit to viewport">
                Fit
              </button>
              <button onClick={() => zp.actualSize()} title="Actual size (1:1)">
                1:1
              </button>
              <button
                type="button"
                className={matchOn ? "active" : ""}
                onClick={() => {
                  if (matchOn) onMatchToggle?.();
                  else {
                    const t = getOtherTransform?.();
                    if (t) zp.setTransform(t);
                    onMatchToggle?.();
                  }
                }}
                title={matchOn ? "Unlink pan/zoom (sync off)" : "Match and keep pan/zoom synced with other pane"}
                aria-label={matchOn ? "Pan/zoom sync on (click to turn off)" : "Match and sync pan/zoom with other pane"}
                aria-pressed={matchOn}
              >
                Match
              </button>
              <button
                type="button"
                className={syncOn ? "active" : ""}
                onClick={() => {
                  if (!syncOn) syncPlaybackFromOther({ chase: true });
                  onSyncToggle?.();
                }}
                title={syncOn ? "Unlink playback (sync off)" : "Sync and keep playback synced with other pane"}
                aria-label={syncOn ? "Playback sync on (click to turn off)" : "Sync playback with other pane"}
                aria-pressed={syncOn}
              >
                Sync
              </button>
            </div>
            <button onClick={() => moveAxis(-1)} title="Axis up" aria-label="Axis up">
              ↑
            </button>
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={() => moveValue(-1)} title="Value left" aria-label="Value left">
                ←
              </button>
              <button onClick={() => moveValue(1)} title="Value right" aria-label="Value right">
                →
              </button>
            </div>
            <button onClick={() => moveAxis(1)} title="Axis down" aria-label="Axis down">
              ↓
            </button>
          </div>
        </div>
      ) : (
        <div style={{ color: "var(--muted)", fontSize: 12, padding: 10 }}>{title}</div>
      )}
    </div>
  );
}

export function App() {
  const DEFAULT_PINNED_AXES = ["cfg", "steps", "denoise", "speed"];
  const SIDEBAR_COLLAPSED_W = 56;
  const SIDEBAR_MIN_W = 280;
  const SIDEBAR_MAX_W = 560;
  const DEFAULT_PAGE_SIZES = [7, 15, 21, 50, 100];

  type DrawerCookie = { open?: boolean; width?: number };
  const DRAWER_COOKIE = "ui.drawer";

  type SidebarLocalStateV1 = {
    v: 1;
    sidebar?: {
      pinned_open?: boolean;
      collapsed?: boolean;
      facets?: {
        viewer?: boolean;
        experiments?: boolean;
        runs?: boolean;
        queue?: boolean;
        axes?: boolean;
        cache?: boolean;
      };
    };
    axes?: {
      /** Table column selection (by label). */
      columns?: string[];
      /** Pinned param ordering (by label). */
      pinned?: string[];
      /** Slide axis selection (params only, by label). */
      slide_params?: string[];
    };
  };

  const SIDEBAR_LOCAL_KEY = "ui.sidebar_state.v1";

  function _getCookie(name: string): string {
    try {
      const parts = String(document.cookie || "").split(";").map((p) => p.trim());
      for (const p of parts) {
        if (!p) continue;
        const eq = p.indexOf("=");
        const k = eq >= 0 ? p.slice(0, eq) : p;
        if (k === name) return eq >= 0 ? p.slice(eq + 1) : "";
      }
    } catch {
      // ignore
    }
    return "";
  }

  function _setCookie(name: string, value: string, opts: { maxAgeDays?: number } = {}): void {
    try {
      const maxAgeDays = opts.maxAgeDays ?? 365;
      const maxAgeSec = Math.max(60, Math.round(maxAgeDays * 86400));
      document.cookie = `${name}=${value}; Path=/; Max-Age=${maxAgeSec}; SameSite=Lax`;
    } catch {
      // ignore
    }
  }

  function _deleteCookie(name: string): void {
    try {
      document.cookie = `${name}=; Path=/; Max-Age=0; SameSite=Lax`;
    } catch {
      // ignore
    }
  }

  function _decodeDrawerCookie(raw: string): { raw: string; decoded?: string; params?: Record<string, string> } {
    try {
      const decoded = decodeURIComponent(raw);
      const p = new URLSearchParams(decoded);
      const params: Record<string, string> = {};
      for (const [k, v] of p.entries()) params[k] = v;
      return { raw, decoded, params };
    } catch {
      return { raw };
    }
  }

  function _readDrawerCookie(): DrawerCookie {
    const raw = _getCookie(DRAWER_COOKIE);
    if (!raw) return {};
    try {
      const decoded = decodeURIComponent(raw);
      const params = new URLSearchParams(decoded);
      const o = params.get("o");
      const w = params.get("w");
      const out: DrawerCookie = {};
      if (o === "1") out.open = true;
      if (o === "0") out.open = false;
      const wn = w != null ? Number(w) : NaN;
      if (Number.isFinite(wn) && wn > 0) out.width = wn;
      return out;
    } catch {
      return {};
    }
  }

  function _writeDrawerCookie(state: { open: boolean; width: number }) {
    const p = new URLSearchParams();
    p.set("v", "1");
    p.set("o", state.open ? "1" : "0");
    p.set("w", String(Math.round(state.width)));
    _setCookie(DRAWER_COOKIE, encodeURIComponent(p.toString()), { maxAgeDays: 365 });
  }

  function _readSidebarLocalState(): SidebarLocalStateV1 {
    try {
      const raw = localStorage.getItem(SIDEBAR_LOCAL_KEY);
      if (!raw) return { v: 1 };
      const obj = JSON.parse(raw) as unknown;
      if (!obj || typeof obj !== "object") return { v: 1 };
      const v = (obj as any).v;
      if (v !== 1) return { v: 1 };
      return obj as SidebarLocalStateV1;
    } catch {
      return { v: 1 };
    }
  }

  function _writeSidebarLocalState(state: SidebarLocalStateV1) {
    try {
      localStorage.setItem(SIDEBAR_LOCAL_KEY, JSON.stringify(state));
    } catch {
      // ignore
    }
  }

  function _uniqStrArray(x: unknown): string[] | undefined {
    if (!Array.isArray(x)) return undefined;
    const out: string[] = [];
    const seen = new Set<string>();
    for (const v of x) {
      if (typeof v !== "string") continue;
      if (seen.has(v)) continue;
      seen.add(v);
      out.push(v);
    }
    return out;
  }

  const EXPERIMENTS_SELECTED_KEY = "ui.experiments.selected.v1";
  const EXPERIMENTS_FILTER_KEY = "ui.experiments.filter.v1";

  function _readSelectedExpIds(): { ids: string[]; hydrated: boolean } {
    try {
      const raw = localStorage.getItem(EXPERIMENTS_SELECTED_KEY);
      if (!raw) return { ids: [], hydrated: false };
      const arr = JSON.parse(raw) as unknown;
      return { ids: _uniqStrArray(arr) ?? [], hydrated: true };
    } catch {
      // If the key exists but is corrupted, treat it as "hydrated" to avoid
      // surprising auto-selection on reload.
      return { ids: [], hydrated: true };
    }
  }

  const sidebarLocal0 = useRef<SidebarLocalStateV1>(_readSidebarLocalState());
  const didHydrateSelectedAxesFromLsRef = useRef<boolean>(Boolean(_uniqStrArray(sidebarLocal0.current.axes?.columns)?.length));

  function loadPager(id: "experiments" | "runs" | "axes" | "gallery", defaultPageSize: number): PagerState {
    try {
      const pRaw = localStorage.getItem(`ui.pager.${id}.page`);
      const psRaw = localStorage.getItem(`ui.pager.${id}.pageSize`);
      const page = pRaw ? Number(pRaw) : 1;
      const pageSizeRaw = psRaw ? Number(psRaw) : defaultPageSize;
      const pickNearest = (n: number) => {
        const nn = Number.isFinite(n) && n > 0 ? Math.trunc(n) : defaultPageSize;
        let best = DEFAULT_PAGE_SIZES[0];
        let bestD = Infinity;
        for (const s of DEFAULT_PAGE_SIZES) {
          const d = Math.abs(s - nn);
          if (d < bestD) {
            best = s;
            bestD = d;
          }
        }
        return best;
      };
      return {
        page: Number.isFinite(page) && page > 0 ? Math.trunc(page) : 1,
        pageSize: pickNearest(pageSizeRaw),
      };
    } catch {
      return { page: 1, pageSize: defaultPageSize };
    }
  }

  const expSel0 = _readSelectedExpIds();
  const didHydrateSelectedExpIdsFromLsRef = useRef<boolean>(expSel0.hydrated);

  const [experiments, setExperiments] = useState<ExperimentSummary[]>([]);
  const [relations, setRelations] = useState<ExperimentsRelations | null>(null);
  const [expFilter, setExpFilter] = useState<string>(() => {
    try {
      return localStorage.getItem(EXPERIMENTS_FILTER_KEY) ?? "";
    } catch {
      return "";
    }
  });
  const [selectedExpId, setSelectedExpId] = useState<string | null>(() => expSel0.ids[0] ?? null);
  const [expandedExpIds, setExpandedExpIds] = useState<string[]>([]);
  const [runsByExpId, setRunsByExpId] = useState<Record<string, ExperimentListRunsEntry>>({});
  const [loadingRunsExpId, setLoadingRunsExpId] = useState<string | null>(null);
  const runs = useMemo(() => Object.values(runsByExpId).flatMap((e) => e.runs), [runsByExpId]);
  const experimentsByExpId = useMemo(() => Object.fromEntries(experiments.map((e) => [e.exp_id, e])), [experiments]);

  const [runsCacheStats, setRunsCacheStats] = useState<{
    expCount: number;
    runCount: number;
    totalBytes: number;
    newestFetchedAtMs?: number;
    oldestFetchedAtMs?: number;
    lastError?: string;
  }>({ expCount: 0, runCount: 0, totalBytes: 0 });
  const [cacheClearing, setCacheClearing] = useState<boolean>(false);
  const [cookiesClearing, setCookiesClearing] = useState<boolean>(false);
  const [localStorageClearing, setLocalStorageClearing] = useState<boolean>(false);
  const [storageSnapNonce, setStorageSnapNonce] = useState<number>(0);
  const [storageSnapOpen, setStorageSnapOpen] = useState<boolean>(false);

  const [queueData, setQueueData] = useState<QueueResponse | null>(null);
  const [queueLoading, setQueueLoading] = useState<boolean>(false);
  const [queueError, setQueueError] = useState<string>("");

  const [error, setError] = useState<string>("");
  const [filterText, setFilterText] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<Record<RunStatus, boolean>>({
    complete: true,
    submitted: false,
    not_submitted: false,
  });
  const [runsPager, setRunsPager] = useState<PagerState>(() => loadPager("runs", 21));
  const [axesPager, setAxesPager] = useState<PagerState>(() => loadPager("axes", 21));
  const [galleryPager, setGalleryPager] = useState<PagerState>(() => loadPager("gallery", 24));
  const didAutoSelectInitialExpRef = useRef<boolean>(false);

  const [selectedAxes, setSelectedAxes] = useState<string[]>(() => {
    const fromLs = _uniqStrArray(sidebarLocal0.current.axes?.columns);
    if (fromLs?.length) return fromLs;
    return ["run_key", "status", "gen_time_sec", "cfg", "steps", "denoise", "speed"];
  });
  const [sortKey, setSortKey] = useState<string>("run_id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [selectedRuns, setSelectedRuns] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem("ui.selected_runs.v1");
      const arr = raw ? (JSON.parse(raw) as unknown) : null;
      const ids = _uniqStrArray(arr) ?? [];
      return ids.slice(0, 2);
    } catch {
      return [];
    }
  }); // runKey(exp_id::run_id) ordered slots: [A, B]
  const [autoRefresh, setAutoRefresh] = useState<boolean>(false);
  const [expanded, setExpanded] = useState<ExpandedMedia | null>(null);
  const [loopPlayback, setLoopPlayback] = useState<boolean>(true);
  const [autoPlay, setAutoPlay] = useState<boolean>(true);
  const [pinnedAxes, setPinnedAxes] = useState<string[]>(() => {
    const ls = sidebarLocal0.current;
    const fromLs = _uniqStrArray(ls.axes?.pinned);
    if (fromLs?.length) return fromLs;
    try {
      const raw = localStorage.getItem("ui.pinned_axes");
      const arr = raw ? (JSON.parse(raw) as unknown) : null;
      if (Array.isArray(arr) && arr.every((x) => typeof x === "string")) return arr as string[];
    } catch {
      // ignore
    }
    return DEFAULT_PINNED_AXES;
  });
  const [sidebarPinnedOpen, setSidebarPinnedOpen] = useState<boolean>(() => {
    const ls = sidebarLocal0.current;
    const v = ls.sidebar?.pinned_open;
    if (typeof v === "boolean") return v;
    try {
      const raw = localStorage.getItem("ui.sidebar_pinned_open");
      if (raw === "1") return true;
      if (raw === "0") return false;
    } catch {
      // ignore
    }
    return false;
  });
  const [showSidebar, setShowSidebar] = useState<boolean>(() => {
    const c = _readDrawerCookie();
    if (typeof c.open === "boolean") return c.open;
    return true;
  });
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    const ls = sidebarLocal0.current;
    const v = ls.sidebar?.collapsed;
    if (typeof v === "boolean") return v;
    try {
      const raw = localStorage.getItem("ui.sidebar_collapsed");
      if (raw === "1") return true;
      if (raw === "0") return false;
    } catch {
      // ignore
    }
    return false;
  });
  const [sidebarWidth, setSidebarWidth] = useState<number>(() => {
    const c = _readDrawerCookie();
    if (typeof c.width === "number" && Number.isFinite(c.width) && c.width > 0) return c.width;
    try {
      const raw = localStorage.getItem("ui.sidebar_width");
      const n = raw ? Number(raw) : NaN;
      if (Number.isFinite(n) && n > 0) return n;
    } catch {
      // ignore
    }
    return 340;
  });
  const [sidebarViewerOpen, setSidebarViewerOpen] = useState<boolean>(() => {
    const v = sidebarLocal0.current.sidebar?.facets?.viewer;
    return typeof v === "boolean" ? v : true;
  });
  const [sidebarExperimentsOpen, setSidebarExperimentsOpen] = useState<boolean>(() => {
    const v = sidebarLocal0.current.sidebar?.facets?.experiments;
    return typeof v === "boolean" ? v : true;
  });
  const [sidebarQueueOpen, setSidebarQueueOpen] = useState<boolean>(() => {
    const v = sidebarLocal0.current.sidebar?.facets?.queue;
    return typeof v === "boolean" ? v : true;
  });
  const [sidebarWipOpen, setSidebarWipOpen] = useState<boolean>(() => false);
  const [sidebarCacheOpen, setSidebarCacheOpen] = useState<boolean>(() => {
    const v = sidebarLocal0.current.sidebar?.facets?.cache;
    return typeof v === "boolean" ? v : false;
  });
  const [showAxes, setShowAxes] = useState<boolean>(() => {
    const v = sidebarLocal0.current.sidebar?.facets?.axes;
    return typeof v === "boolean" ? v : true;
  });
  const [focusPreview, setFocusPreview] = useState<boolean>(false);
  const [createSource, setCreateSource] = useState<CreateSource | null>(null);
  const [mainMode, setMainMode] = useState<"runs" | "queue" | "wip">(() => {
    try {
      const raw = localStorage.getItem("ui.main_mode.v1");
      if (raw === "queue") return "queue";
    } catch {
      // ignore
    }
    return "runs";
  });
  const [wipPlanned, setWipPlanned] = useState<WipPlannedExperiment[]>([]);
  const [wipSelectedRelpath, setWipSelectedRelpath] = useState<string | null>(null);
  const [wipEditingId, setWipEditingId] = useState<string | null>(null);
  const [wipParams, setWipParams] = useState<WipFormParams>(() => DEFAULT_WIP_PARAMS);
  const [wipDates, setWipDates] = useState<{ name: string; path: string; date: string }[]>([]);
  const [wipMedia, setWipMedia] = useState<{ name: string; path: string; relpath: string; size: number; mtime: number }[]>([]);
  const [wipCurrentDir, setWipCurrentDir] = useState("");
  const [wipLoading, setWipLoading] = useState(false);
  const [wipError, setWipError] = useState("");
  const [wipCreating, setWipCreating] = useState(false);
  const [wipCreateLog, setWipCreateLog] = useState<string | null>(null);
  const [viewerKind, setViewerKind] = useState<"pair" | "slide" | "select">(() => {
    try {
      const raw = localStorage.getItem("ui.viewer_kind.v1");
      if (raw === "pair" || raw === "slide" || raw === "select") return raw;
    } catch {
      // ignore
    }
    return "slide";
  });
  const [experimentDetailPanelOpen, setExperimentDetailPanelOpen] = useState<boolean>(false);
  const [selectSubmode, setSelectSubmode] = useState<"list" | "gallery">(() => {
    try {
      const raw = localStorage.getItem("ui.select_submode");
      if (raw === "gallery") return "gallery";
      if (raw === "list") return "list";
    } catch {
      // ignore
    }
    return "list";
  });
  const [galleryThumbAspect, setGalleryThumbAspect] = useState<string>(() => {
    try {
      const raw = localStorage.getItem("ui.gallery_thumb_aspect");
      if (raw && typeof raw === "string") return raw;
    } catch {
      // ignore
    }
    return "";
  });
  const galleryThumbAspectKindRef = useRef<"image" | "video" | "">("");
  const [slideParamAxesSelected, setSlideParamAxesSelected] = useState<string[]>(() => {
    const fromLs = _uniqStrArray(sidebarLocal0.current.axes?.slide_params);
    if (fromLs?.length) return fromLs;
    return DEFAULT_PINNED_AXES;
  });

  const [slideAxes, setSlideAxes] = useState<string[]>(DEFAULT_PINNED_AXES);
  const [slideActiveAxis, setSlideActiveAxis] = useState<number>(0);
  const [slideGroupKey, setSlideGroupKey] = useState<string>("");
  const [slideAnchorKey, setSlideAnchorKey] = useState<string>("");
  const [slideAB, setSlideAB] = useState<"A" | "B">(() => {
    try {
      const raw = localStorage.getItem("ui.slide.ab.v1");
      if (raw === "B") return "B";
    } catch {
      // ignore
    }
    return "A";
  });
  const [slideCursor, setSlideCursor] = useState<Record<string, unknown>>({});
  const [slideCursorB, setSlideCursorB] = useState<Record<string, unknown>>({});
  const [slideShowHud, setSlideShowHud] = useState<boolean>(false);
  const [slideMeta, setSlideMeta] = useState<{ w?: number; h?: number; duration?: number } | null>(null);
  const [slideHint, setSlideHint] = useState<string>("");
  const [slideMediaError, setSlideMediaError] = useState<string>("");
  const [recentGood, setRecentGood] = useState<
    Array<{ sig: string; values: Record<string, unknown>; label: string; run_key: string }>
  >([]);
  const [nextOpen, setNextOpen] = useState<boolean>(false);
  const [nextAxes, setNextAxes] = useState<string[]>(DEFAULT_PINNED_AXES);
  const [nextValues, setNextValues] = useState<Record<string, string>>({});
  const [nextMaxRuns, setNextMaxRuns] = useState<number>(200);
  const [nextBaselineFirst, setNextBaselineFirst] = useState<boolean>(true);
  const [nextNoWait, setNextNoWait] = useState<boolean>(true);
  const swipeStart = useRef<{ x: number; y: number } | null>(null);
  const pairTransformRef = useRef<[ZoomPanTransform | null, ZoomPanTransform | null]>([null, null]);
  const pairVideoRef = useRef<[HTMLVideoElement | null, HTMLVideoElement | null]>([null, null]);
  const [pairMatchOn, setPairMatchOn] = useState<boolean>(false);
  const [pairSyncVideosOn, setPairSyncVideosOn] = useState<boolean>(false);
  const [pairSyncTransforms, setPairSyncTransforms] = useState<{
    from0: ZoomPanTransform | null;
    from1: ZoomPanTransform | null;
  }>({ from0: null, from1: null });

  // When Sync toggle is on, keep the two Pair videos in sync (playback state + time).
  useEffect(() => {
    if (!pairSyncVideosOn) return;
    const interval = window.setInterval(() => {
      const a = pairVideoRef.current[0];
      const b = pairVideoRef.current[1];
      if (!a || !b) return;
      const aPlaying = !a.paused && !a.ended;
      const bPlaying = !b.paused && !b.ended;
      const aTime = Number(a.currentTime) || 0;
      const bTime = Number(b.currentTime) || 0;
      // Pick source: playing wins; if both playing, use the one ahead in time; if both paused, use pane 0.
      const useA =
        aPlaying && !bPlaying
          ? true
          : !aPlaying && bPlaying
            ? false
            : aPlaying && bPlaying
              ? aTime >= bTime
              : true;
      const src = useA ? a : b;
      const dst = useA ? b : a;
      const tgtTime = Number(src.currentTime) || 0;
      const tgtRate = Number(src.playbackRate) || 1;
      const dstTime = Number(dst.currentTime) || 0;
      if (Math.abs(tgtTime - dstTime) > 0.5) {
        try {
          dst.currentTime = tgtTime;
        } catch {
          // ignore
        }
      }
      dst.playbackRate = tgtRate;
      if (src.paused || src.ended) {
        dst.pause();
      } else {
        void dst.play().catch(() => {});
      }
    }, 220);
    return () => window.clearInterval(interval);
  }, [pairSyncVideosOn]);

  function runKey(r: Pick<RunsItem, "exp_id" | "run_id">): string {
    return `${r.exp_id}::${r.run_id}`;
  }

  function clampSidebarW(w: number): number {
    return Math.max(SIDEBAR_MIN_W, Math.min(SIDEBAR_MAX_W, Math.round(w)));
  }

  function fmtBytes(n: number): string {
    const nn = Number(n);
    if (!Number.isFinite(nn) || nn <= 0) return "0 B";
    const kb = 1024;
    const mb = kb * 1024;
    const gb = mb * 1024;
    if (nn >= gb) return `${(nn / gb).toFixed(1)} GB`;
    if (nn >= mb) return `${(nn / mb).toFixed(1)} MB`;
    if (nn >= kb) return `${(nn / kb).toFixed(1)} KB`;
    return `${Math.round(nn)} B`;
  }

  const resizeRef = useRef<{ startX: number; startW: number } | null>(null);
  const sidebarWidthRef = useRef<number>(sidebarWidth);
  const showSidebarRef = useRef<boolean>(showSidebar);

  useEffect(() => {
    sidebarWidthRef.current = sidebarWidth;
  }, [sidebarWidth]);

  useEffect(() => {
    showSidebarRef.current = showSidebar;
  }, [showSidebar]);

  // Persist drawer state (open/close + width) in one cookie.
  useEffect(() => {
    _writeDrawerCookie({ open: showSidebar, width: clampSidebarW(sidebarWidth) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showSidebar]);

  function beginSidebarResize(e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();
    if (sidebarCollapsed) setSidebarCollapsed(false);
    const startW = clampSidebarW(sidebarWidth);
    resizeRef.current = { startX: e.clientX, startW };

    document.documentElement.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const onMove = (ev: PointerEvent) => {
      const st = resizeRef.current;
      if (!st) return;
      const dx = ev.clientX - st.startX;
      setSidebarWidth(clampSidebarW(st.startW + dx));
    };
    const onUp = () => {
      resizeRef.current = null;
      document.documentElement.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("pointermove", onMove);
      // Write width at end of drag to avoid spamming cookies.
      _writeDrawerCookie({ open: showSidebarRef.current, width: clampSidebarW(sidebarWidthRef.current) });
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerup", onUp, { passive: true, once: true });
  }

  function renderRunsHeader(opts: { variant: "sidebar" | "main"; showSelectSubmode?: boolean }) {
    function toggleStatus(s: RunStatus) {
      setStatusFilter((prev) => ({ ...prev, [s]: !prev[s] }));
    }

    return (
      <>
        <FilterBox
          left={<label style={{ width: 80 }}>Filter</label>}
          value={filterText}
          onChange={setFilterText}
          placeholder="exp_id::run_…"
          ariaLabel="Filter runs"
          showClear={true}
          onClear={() => setFilterText("")}
          clearTitle="Clear runs filter"
          clearAriaLabel="Clear runs filter"
        />

        <div className="row" style={{ flexWrap: "wrap" }}>
          <label style={{ width: 80 }}>Status</label>
          <div className="segmented" role="group" aria-label="Run status filter">
            <button
              type="button"
              className={`seg-btn ${statusFilter.complete ? "active" : ""}`}
              onClick={() => toggleStatus("complete")}
              aria-pressed={statusFilter.complete}
              title="Show completed runs"
            >
              complete
            </button>
            <button
              type="button"
              className={`seg-btn ${statusFilter.submitted ? "active" : ""}`}
              onClick={() => toggleStatus("submitted")}
              aria-pressed={statusFilter.submitted}
              title="Show submitted (in progress) runs"
            >
              submitted
            </button>
            <button
              type="button"
              className={`seg-btn ${statusFilter.not_submitted ? "active" : ""}`}
              onClick={() => toggleStatus("not_submitted")}
              aria-pressed={statusFilter.not_submitted}
              title="Show not_submitted runs"
            >
              not_submitted
            </button>
          </div>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            (default: complete only)
          </span>
        </div>

        <div className="row" style={{ flexWrap: "wrap" }}>
          <label style={{ width: 120 }}>Auto-refresh</label>
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          <span style={{ color: "var(--muted)", fontSize: 12 }}>3s</span>
          {opts.showSelectSubmode ? (
            <div style={{ marginLeft: "auto" }}>
              <div className="segmented" role="radiogroup" aria-label="Select view">
                <button
                  type="button"
                  className={`seg-btn ${selectSubmode === "list" ? "active" : ""}`}
                  onClick={() => setSelectSubmode("list")}
                  role="radio"
                  aria-checked={selectSubmode === "list"}
                  title="Table list"
                >
                  List
                </button>
                <button
                  type="button"
                  className={`seg-btn ${selectSubmode === "gallery" ? "active" : ""}`}
                  onClick={() => setSelectSubmode("gallery")}
                  role="radio"
                  aria-checked={selectSubmode === "gallery"}
                  title="Gallery grid"
                >
                  Gallery
                </button>
              </div>
            </div>
          ) : null}
        </div>

        <div style={{ color: "var(--muted)", fontSize: 12, marginBottom: 8 }}>
          Only <span className="mono">complete</span> runs are selectable.
        </div>
      </>
    );
  }

  function renderRunsTableBody(opts: { variant: "sidebar" | "main" }) {
    const isSidebar = opts.variant === "sidebar";
    const rows = isSidebar ? pagedRuns.pageItems : filteredRuns;
    return (
      <>
        {renderRunsHeader({ variant: opts.variant, showSelectSubmode: opts.variant === "main" && viewerKind === "select" })}

        {isSidebar ? (
          <Pager state={runsPager} pageCount={pagedRuns.pageCount} total={pagedRuns.total} onChange={setRunsPager} />
        ) : null}

        <div
          className={`table-wrap ${isSidebar ? "paged paged-fixed" : ""}`}
          style={
            isSidebar
              ? ({
                  // +1 for the header row height-ish so the table wrap stays stable.
                  ["--paged-rows" as any]: runsPager.pageSize + 1,
                  ["--paged-row-h" as any]: "30px",
                  ["--paged-extra" as any]: "0px",
                } as React.CSSProperties)
              : undefined
          }
        >
          <table>
            <thead>
              <tr>
                <th style={{ width: 56, textAlign: "center" }} title="Set run as A or B view">
                  A / B
                </th>
                {visibleAxes.map((a) => (
                  <th key={a.label} onClick={() => onSort(a.label)} title="Click to sort">
                    {a.label}
                    {sortKey === a.label ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const key = runKey(r);
                const sel = selectedRuns.includes(key);
                const selectable = r.status === "complete";
                const isA = selectedRuns[0] === key;
                const isB = selectedRuns[1] === key;
                return (
                  <tr
                    key={key}
                    className={`${sel ? "selected" : ""} ${selectable ? "" : "disabled"}`}
                    style={{ cursor: "default" }}
                    title={selectable ? "Use A / B buttons to set view" : "Only completed runs are selectable"}
                  >
                    <td style={{ textAlign: "center", verticalAlign: "middle" }} onClick={(e) => e.stopPropagation()}>
                      <span style={{ display: "inline-flex", gap: 2 }} role="radiogroup" aria-label="Set as A or B view">
                        <button
                          type="button"
                          role="radio"
                          aria-checked={isA}
                          className={`icon-btn slot-btn ${isA ? "slot-btn-selected" : ""}`}
                          style={{ fontSize: 10 }}
                          onClick={() => selectable && setSelectedSlot("A", r)}
                          disabled={!selectable}
                          title={selectable ? "Set as A view" : "Only finished runs can be set as A"}
                          aria-label="Set as A"
                        >
                          A
                        </button>
                        <button
                          type="button"
                          role="radio"
                          aria-checked={isB}
                          className={`icon-btn slot-btn ${isB ? "slot-btn-selected" : ""}`}
                          style={{ fontSize: 10 }}
                          onClick={() => selectable && setSelectedSlot("B", r)}
                          disabled={!selectable}
                          title={selectable ? "Set as B view" : "Only finished runs can be set as B"}
                          aria-label="Set as B"
                        >
                          B
                        </button>
                      </span>
                    </td>
                    {visibleAxes.map((a) => {
                      const v = a.get(r);
                      if (a.label === "status") {
                        return (
                          <td key={a.label} className={`status ${String(v)}`}>
                            {String(v)}
                          </td>
                        );
                      }
                      if (a.label === "primary_video") {
                        const url = r.primary_video?.url;
                        return (
                          <td key={a.label}>
                            {url ? (
                              <a href={url} target="_blank" rel="noreferrer">
                                mp4
                              </a>
                            ) : (
                              ""
                            )}
                          </td>
                        );
                      }
                      if (a.label === "primary_image") {
                        const url = r.primary_image?.url;
                        return (
                          <td key={a.label}>
                            {url ? (
                              <a href={url} target="_blank" rel="noreferrer">
                                png
                              </a>
                            ) : (
                              ""
                            )}
                          </td>
                        );
                      }
                      return <td key={a.label}>{fmt(v)}</td>;
                    })}
                  </tr>
                );
              })}
              {isSidebar
                ? Array.from({ length: Math.max(0, runsPager.pageSize - rows.length) }).map((_, i) => (
                    <tr key={`ph-run-${i}`} className="placeholder-row">
                      <td colSpan={Math.max(1, visibleAxes.length) + 1}>placeholder</td>
                    </tr>
                  ))
                : null}
            </tbody>
          </table>
        </div>
      </>
    );
  }

  function renderRunsGallery() {
    function maybeSetAspect(kind: "image" | "video", w: number, h: number) {
      if (!w || !h) return;
      if (!Number.isFinite(w) || !Number.isFinite(h)) return;
      const prevKind = galleryThumbAspectKindRef.current;
      // Prefer video metadata over image if both appear.
      if (galleryThumbAspect && prevKind === "video") return;
      if (galleryThumbAspect && prevKind === "image" && kind === "image") return;
      galleryThumbAspectKindRef.current = kind;
      setGalleryThumbAspect(`${Math.round(w)} / ${Math.round(h)}`);
    }

    return (
      <div className="gallery-wrap">
        <Pager
          state={galleryPager}
          pageCount={pagedGalleryRuns.pageCount}
          total={pagedGalleryRuns.total}
          onChange={setGalleryPager}
          pageSizeOptions={[12, 24, 48, 96]}
        />
        <div className="gallery-pane" style={galleryThumbAspect ? ({ ["--thumb-ar" as any]: galleryThumbAspect } as any) : undefined}>
          <div className="gallery-grid">
            {pagedGalleryRuns.pageItems.map((r) => {
            const key = runKey(r);
            const sel = selectedRuns.includes(key);
            const selectable = r.status === "complete";
            const isA = selectedRuns[0] === key;
            const isB = selectedRuns[1] === key;
            const img = r.primary_image?.url;
            const vid = r.primary_video?.url;
            return (
              <div
                key={key}
                className={`gallery-card ${sel ? "selected" : ""} ${selectable ? "" : "disabled"}`}
                title={selectable ? "Use A / B buttons to set view" : "Only completed runs are selectable"}
              >
                <div className="overlay-hotzone" aria-hidden="true">
                  <button
                    type="button"
                    className="overlay-icon overlay-expand"
                    onClick={(e) => {
                      e.stopPropagation();
                      openExpanded(r);
                    }}
                    title="Expand"
                    aria-label="Expand"
                  >
                    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
                      <path
                        d="M9 5H5v4M15 5h4v4M9 19H5v-4M15 19h4v-4"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>
                </div>
                <div className="gallery-thumb">
                  {img ? (
                    <img
                      className="gallery-media"
                      src={img}
                      alt={key}
                      loading="lazy"
                      onLoad={(e) => {
                        const el = e.currentTarget;
                        maybeSetAspect("image", el.naturalWidth, el.naturalHeight);
                      }}
                    />
                  ) : vid ? (
                    <video
                      className="gallery-media"
                      src={vid}
                      muted
                      playsInline
                      preload="metadata"
                      onLoadedMetadata={(e) => {
                        const el = e.currentTarget;
                        maybeSetAspect("video", el.videoWidth, el.videoHeight);
                      }}
                    />
                  ) : (
                    <div className="gallery-empty">no media</div>
                  )}
                </div>
                <div className="gallery-meta">
                  <div className="mono gallery-title">{key}</div>
                  <div className={`mono gallery-status status ${String(r.status ?? "")}`}>{String(r.status ?? "")}</div>
                  <span style={{ display: "inline-flex", gap: 4, marginTop: 4 }} role="radiogroup" aria-label="Set as A or B view">
                    <button
                      type="button"
                      role="radio"
                      aria-checked={isA}
                      className={`icon-btn slot-btn ${isA ? "slot-btn-selected" : ""}`}
                      style={{ fontSize: 10 }}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (selectable) setSelectedSlot("A", r);
                      }}
                      disabled={!selectable}
                      title={selectable ? "Set as A view" : "Only finished runs can be set as A"}
                      aria-label="Set as A"
                    >
                      A
                    </button>
                    <button
                      type="button"
                      role="radio"
                      aria-checked={isB}
                      className={`icon-btn slot-btn ${isB ? "slot-btn-selected" : ""}`}
                      style={{ fontSize: 10 }}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (selectable) setSelectedSlot("B", r);
                      }}
                      disabled={!selectable}
                      title={selectable ? "Set as B view" : "Only finished runs can be set as B"}
                      aria-label="Set as B"
                    >
                      B
                    </button>
                  </span>
                </div>
              </div>
            );
            })}
          </div>
        </div>
      </div>
    );
  }

  function desiredSlideAxes(selAxes: string[]): string[] {
    const uniqSelParams: string[] = [];
    const seen = new Set<string>();
    for (const a of selAxes) {
      if (!paramAxisLabels.includes(a)) continue;
      if (seen.has(a)) continue;
      seen.add(a);
      uniqSelParams.push(a);
    }
    const out = uniqSelParams.slice(0, 6);
    if (out.length) return out;
    const def = DEFAULT_PINNED_AXES.filter((x) => paramAxisLabels.includes(x)).slice(0, 6);
    if (def.length) return def;
    return ["cfg"];
  }

  function toggleSlideParamAxis(label: string) {
    if (!paramAxisLabels.includes(label)) return;
    setSlideParamAxesSelected((prev) => (prev.includes(label) ? prev.filter((x) => x !== label) : [...prev, label]));
  }

  async function refreshExperiments() {
    setError("");
    try {
      const res = await fetchExperiments();
      setExperiments(res.experiments ?? []);
      setRelations(res.relations ?? null);
      if (
        !didAutoSelectInitialExpRef.current &&
        !didHydrateSelectedExpIdsFromLsRef.current &&
        selectedExpId == null &&
        res.experiments?.length
      ) {
        setSelectedExpId(res.experiments[res.experiments.length - 1].exp_id);
        didAutoSelectInitialExpRef.current = true;
      }
    } catch (e) {
      setError(String(e));
    }
  }

  async function refreshQueue() {
    setQueueError("");
    setQueueLoading(true);
    try {
      const res = await fetchQueue();
      setQueueData(res);
    } catch (e) {
      setQueueError(String(e));
    } finally {
      setQueueLoading(false);
    }
  }

  useEffect(() => {
    if (mainMode !== "queue") return;
    if (queueLoading) return;
    if (queueData) return;
    void refreshQueue();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mainMode]);

  async function loadWip(dir?: string) {
    setWipLoading(true);
    setWipError("");
    try {
      const res = await fetchWip(dir);
      setWipDates(res.dates);
      setWipMedia(res.media);
      setWipCurrentDir(res.dir);
    } catch (e) {
      setWipError(String(e));
      setWipDates([]);
      setWipMedia([]);
      setWipCurrentDir("");
    } finally {
      setWipLoading(false);
    }
  }

  useEffect(() => {
    if (sidebarWipOpen) {
      setMainMode("wip");
      void loadWip();
    } else {
      setMainMode((m) => (m === "wip" ? "runs" : m));
    }
  }, [sidebarWipOpen]);

  useEffect(() => {
    if (!wipEditingId) return;
    const item = wipPlanned.find((p) => p.id === wipEditingId);
    if (item) setWipParams(paramsFromPlanned(item));
  }, [wipEditingId, wipPlanned]);

  function openCreateFromRun(run: RunsItem) {
    const relpath = run.primary_video?.relpath ?? null;
    if (!relpath) return;
    setCreateSource({ type: "run", run, relpath, videoName: relpath.replace(/\\/g, "/").split("/").pop() ?? "video" });
    setWipSelectedRelpath(relpath);
    setWipParams(paramsFromRun(run));
    setMainMode("wip");
    setSidebarWipOpen(true);
  }

  function wipAddExperiment() {
    if (!wipSelectedRelpath) return;
    const name = wipMedia.find((m) => m.relpath === wipSelectedRelpath)?.name ?? wipSelectedRelpath.split("/").pop() ?? "video";
    const sweep = sweepFromParamStrings(wipParams.cfgStr, wipParams.denoiseStr, wipParams.stepsStr);
    setWipPlanned((prev) => [
      ...prev,
      {
        id: nextId(),
        base_mp4_relpath: wipSelectedRelpath,
        videoName: name,
        seed: wipParams.seed,
        duration_sec: wipParams.duration_sec,
        baseline_first: wipParams.baseline_first,
        max_runs: wipParams.max_runs,
        sweep,
      },
    ]);
    setWipSelectedRelpath(null);
  }

  function wipUpdateExperiment() {
    if (!wipEditingId) return;
    const sweep = sweepFromParamStrings(wipParams.cfgStr, wipParams.denoiseStr, wipParams.stepsStr);
    setWipPlanned((prev) =>
      prev.map((p) =>
        p.id === wipEditingId
          ? {
              ...p,
              seed: wipParams.seed,
              duration_sec: wipParams.duration_sec,
              baseline_first: wipParams.baseline_first,
              max_runs: wipParams.max_runs,
              sweep,
            }
          : p
      )
    );
    setWipEditingId(null);
  }

  async function wipCreateAll() {
    setWipCreating(true);
    setWipCreateLog(null);
    const logs: string[] = [];
    try {
      for (const item of wipPlanned) {
        try {
          const res = await createExperimentFromWip({
            base_mp4_relpath: item.base_mp4_relpath,
            seed: item.seed,
            duration_sec: item.duration_sec,
            baseline_first: item.baseline_first,
            max_runs: item.max_runs,
            sweep: item.sweep,
          });
          logs.push(`Created ${res.exp_id}`);
          setSelectedExpId(res.exp_id);
          setExpandedExpIds((prev) => (prev.includes(res.exp_id) ? prev : [...prev, res.exp_id]));
          loadRunsForExp(res.exp_id);
        } catch (e) {
          logs.push(`Failed ${item.videoName}: ${e}`);
        }
      }
      setWipCreateLog(logs.join("\n"));
      if (logs.some((l) => l.startsWith("Created"))) {
        setWipPlanned([]);
        setWipEditingId(null);
        await refreshExperiments();
      }
    } finally {
      setWipCreating(false);
    }
  }

  async function loadRunsForExp(expId: string) {
    if (runsByExpId[expId]) return;
    setLoadingRunsExpId(expId);
    try {
      const res = await fetchExperimentRuns(expId);
      setRunsByExpId((prev) => ({ ...prev, [expId]: { runs: res.runs, manifest: res.manifest ?? undefined } }));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingRunsExpId(null);
    }
  }

  function toggleExpandedExp(expId: string) {
    setExpandedExpIds((prev) => (prev.includes(expId) ? prev.filter((id) => id !== expId) : [...prev, expId]));
  }

  useEffect(() => {
    refreshExperiments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist run selection slots (A/B).
  useEffect(() => {
    try {
      localStorage.setItem("ui.selected_runs.v1", JSON.stringify(selectedRuns.slice(0, 2)));
    } catch {
      // ignore
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRuns.join("|")]);

  useEffect(() => {
    try {
      localStorage.setItem("ui.viewer_kind.v1", viewerKind);
    } catch {
      // ignore
    }
  }, [viewerKind]);

  useEffect(() => {
    try {
      localStorage.setItem("ui.main_mode.v1", mainMode);
    } catch {
      // ignore
    }
  }, [mainMode]);

  useEffect(() => {
    try {
      localStorage.setItem("ui.slide.ab.v1", slideAB);
    } catch {
      // ignore
    }
  }, [slideAB]);

  // Persist selected experiment for detail panel.
  useEffect(() => {
    try {
      localStorage.setItem(EXPERIMENTS_SELECTED_KEY, JSON.stringify(selectedExpId != null ? [selectedExpId] : []));
    } catch {
      // ignore
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedExpId]);

  useEffect(() => {
    try {
      localStorage.setItem(EXPERIMENTS_FILTER_KEY, expFilter);
    } catch {
      // ignore
    }
  }, [expFilter]);

  // When experiments load, drop any persisted selections that no longer exist.
  useEffect(() => {
    if (!experiments.length) return;
    const ids = new Set(experiments.map((e) => e.exp_id));
    setSelectedExpId((prev) => (prev && ids.has(prev) ? prev : null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experiments]);

  // Runs are now loaded per-expand via loadRunsForExp; no bulk refresh on selection.

  // On load: ensure runs for selected A/B are fetched so the viewers populate (selections are restored from localStorage).
  const loadRunsForExpRef = useRef(loadRunsForExp);
  loadRunsForExpRef.current = loadRunsForExp;
  useEffect(() => {
    const keys = selectedRuns.slice(0, 2);
    for (const k of keys) {
      if (!k || !k.includes("::")) continue;
      const exp_id = k.split("::")[0];
      if (!exp_id) continue;
      if (runsByExpId[exp_id]) continue;
      if (loadingRunsExpId === exp_id) continue;
      loadRunsForExpRef.current(exp_id);
    }
  }, [selectedRuns, runsByExpId, loadingRunsExpId]);

  // IndexedDB cache stats (best-effort).
  useEffect(() => {
    void (async () => {
      try {
        const st = await runsCacheGetStats();
        setRunsCacheStats((prev) => ({ ...prev, ...st, lastError: "" }));
      } catch (e) {
        setRunsCacheStats((prev) => ({ ...prev, lastError: String(e) }));
      }
    })();
  }, []);

  const storageSnapshot = useMemo(() => {
    // Best-effort diagnostic snapshot for the Cache panel.
    // Avoid huge dumps by filtering keys to app-related entries.
    const out: {
      localStorage: Array<{ key: string; value: string }>;
      cookies: Array<{ name: string; value: string }>;
      drawer?: { raw: string; decoded?: string; params?: Record<string, string> };
    } = { localStorage: [], cookies: [] };

    try {
      const keys: string[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k) keys.push(k);
      }
      keys.sort((a, b) => a.localeCompare(b));
      const allow = (k: string) =>
        k.startsWith("ui.") ||
        k === "ui.sidebar_width" ||
        k === "ui.sidebar_collapsed" ||
        k === "ui.sidebar_pinned_open" ||
        k === "ui.pinned_axes";
      for (const k of keys) {
        if (!allow(k)) continue;
        const v = localStorage.getItem(k);
        if (v == null) continue;
        out.localStorage.push({ key: k, value: v.length > 2000 ? `${v.slice(0, 2000)}… (${v.length} chars)` : v });
      }
    } catch {
      // ignore
    }

    try {
      const parts = String(document.cookie || "")
        .split(";")
        .map((p) => p.trim())
        .filter(Boolean);
      for (const p of parts) {
        const eq = p.indexOf("=");
        const name = eq >= 0 ? p.slice(0, eq) : p;
        const value = eq >= 0 ? p.slice(eq + 1) : "";
        out.cookies.push({ name, value });
        if (name === DRAWER_COOKIE) out.drawer = _decodeDrawerCookie(value);
      }
      out.cookies.sort((a, b) => a.name.localeCompare(b.name));
    } catch {
      // ignore
    }

    return out;
  }, [storageSnapNonce]);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  useEffect(() => {
    // Escape exits focus-preview mode.
    if (!focusPreview) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFocusPreview(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focusPreview]);

  const axesRaw = useMemo(() => buildAxes(runs), [runs]);

  // Stabilize the Axes UI on reload: include persisted axis labels as "virtual" rows
  // until runs arrive and the real axes list is known.
  const axes = useMemo(() => {
    const base = axesRaw;
    const byLabel = new Map(base.map((a) => [a.label, a] as const));
    const want = new Set<string>();
    for (const x of selectedAxes) want.add(x);
    for (const x of pinnedAxes) want.add(x);
    for (const x of slideParamAxesSelected) want.add(x);
    want.add("run_key");
    want.add("status");

    const out: Axis[] = [...base];
    for (const label of want) {
      if (!label) continue;
      if (byLabel.has(label)) continue;
      // Treat unknown labels as param axes (matches buildAxes behavior).
      out.push({
        key: `params.${label}`,
        label,
        get: (r) => (r.params ?? {})[label],
        virtual: true,
      });
    }
    return out;
  }, [axesRaw, pinnedAxes.join("|"), selectedAxes.join("|"), slideParamAxesSelected.join("|")]);

  const filteredExperiments = useMemo(() => {
    const f = expFilter.trim().toLowerCase();
    if (!f) return experiments;
    return experiments.filter(
      (e) =>
        e.exp_id.toLowerCase().includes(f) ||
        String(e.base_mp4 ?? "").toLowerCase().includes(f) ||
        String(e.source_image ?? "").toLowerCase().includes(f)
    );
  }, [experiments, expFilter]);

  // Ensure key sweep axes are visible by default (HMR may preserve older state).
  useEffect(() => {
    // If the user has a persisted selection, don't force defaults back in.
    if (didHydrateSelectedAxesFromLsRef.current) return;
    const must = DEFAULT_PINNED_AXES;
    setSelectedAxes((prev) => {
      const s = new Set(prev);
      for (const m of must) s.add(m);
      // Always keep identifiers visible too.
      s.add("run_key");
      s.add("status");
      return [...s];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep selected axes as the user's persisted preference.
  // Axes can temporarily be missing while runs are still loading; don't drop selections.

  const visibleAxes = useMemo(() => {
    const byLabel = new Map(axes.map((a) => [a.label, a] as const));
    const chosen = selectedAxes.map((k) => byLabel.get(k)).filter(Boolean) as Axis[];
    // Always keep identifiers/status pinned.
    const required = ["run_key", "status"];
    for (const r of required) {
      if (!chosen.find((a) => a.label === r)) {
        const ax = byLabel.get(r);
        if (ax) chosen.unshift(ax);
      }
    }
    return chosen;
  }, [axes, selectedAxes]);

  const filteredRuns = useMemo(() => {
    const f = filterText.trim().toLowerCase();
    let rr = runs;
    if (f) rr = rr.filter((r) => `${r.exp_id}::${r.run_id}`.toLowerCase().includes(f));
    rr = rr.filter((r) => statusFilter[r.status]);
    const ax = visibleAxes.find((a) => a.label === sortKey) ?? visibleAxes[0];
    rr = [...rr].sort((a, b) => {
      const c = cmp(ax?.get(a), ax?.get(b));
      return sortDir === "asc" ? c : -c;
    });
    return rr;
  }, [runs, filterText, statusFilter, visibleAxes, sortKey, sortDir]);

  // Paging: Runs (sidebar)
  useEffect(() => {
    setRunsPager((prev) => ({ ...prev, page: 1 }));
  }, [filterText, statusFilter, sortKey, sortDir]);

  const pagedRuns = useMemo(() => {
    return pageSlice(filteredRuns, runsPager.page, runsPager.pageSize);
  }, [filteredRuns, runsPager.page, runsPager.pageSize]);

  useEffect(() => {
    if (runsPager.page !== pagedRuns.page) setRunsPager((prev) => ({ ...prev, page: pagedRuns.page }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pagedRuns.page]);

  // Paging: Gallery (main view)
  const galleryPagingKey = useMemo(() => {
    return [
      filterText,
      sortKey,
      sortDir,
      statusFilter.complete ? "1" : "0",
      statusFilter.submitted ? "1" : "0",
      statusFilter.not_submitted ? "1" : "0",
      viewerKind,
      selectSubmode,
    ].join("|");
  }, [filterText, sortKey, sortDir, statusFilter.complete, statusFilter.submitted, statusFilter.not_submitted, viewerKind, selectSubmode]);

  useEffect(() => {
    // Only reset paging when we're actually looking at the gallery.
    if (viewerKind !== "select" || selectSubmode !== "gallery") return;
    setGalleryPager((prev) => ({ ...prev, page: 1 }));
  }, [galleryPagingKey, viewerKind, selectSubmode]);

  const pagedGalleryRuns = useMemo(() => {
    return pageSlice(filteredRuns, galleryPager.page, galleryPager.pageSize);
  }, [filteredRuns, galleryPager.page, galleryPager.pageSize]);

  useEffect(() => {
    if (galleryPager.page !== pagedGalleryRuns.page) setGalleryPager((prev) => ({ ...prev, page: pagedGalleryRuns.page }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pagedGalleryRuns.page]);

  function toggleAxis(label: string) {
    setSelectedAxes((prev) => (prev.includes(label) ? prev.filter((x) => x !== label) : [...prev, label]));
  }

  function onSort(label: string) {
    if (sortKey === label) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(label);
      setSortDir("asc");
    }
  }

  // Ensure selection never includes non-completed runs (e.g. after refresh).
  useEffect(() => {
    // On reload, runs may be empty while we hydrate from cache / fetch from server.
    // Don't wipe persisted A/B selection during that transient state.
    if (!runs.length) return;
    const byKey = new Map(runs.map((r) => [runKey(r), r] as const));
    setSelectedRuns((prev) => prev.filter((k) => (byKey.has(k) ? byKey.get(k)?.status === "complete" : true)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs]);

  // Preserve A/B slot indices: [0]=A run, [1]=B run (undefined if key not in runs).
  const selectedRunObjs: (RunsItem | undefined)[] = selectedRuns
    .slice(0, 2)
    .map((k) => {
      if (!k || !k.includes("::")) return undefined;
      const [exp_id, run_id] = k.split("::");
      return runs.find((r) => r.exp_id === exp_id && r.run_id === run_id) ?? undefined;
    });
  while (selectedRunObjs.length < 2) selectedRunObjs.push(undefined);

  function openExpanded(r: RunsItem) {
    const v = r.primary_video?.url;
    const i = r.primary_image?.url;
    const title = `${r.exp_id}::${r.run_id} (${r.status})`;
    if (v) setExpanded({ kind: "video", title, url: v });
    else if (i) setExpanded({ kind: "image", title, url: i });
  }

  const paramAxisLabels = useMemo(() => {
    return axes.filter((a) => a.key.startsWith("params.")).map((a) => a.label);
  }, [axes]);

  function orderAxes(all: string[], pinned: string[]): string[] {
    const norm = (s: string) => s.trim().toLowerCase();
    const byNorm = new Map(all.map((a) => [norm(a), a] as const));
    const pinnedOut: string[] = [];
    for (const p of pinned) {
      const hit = byNorm.get(norm(p));
      if (hit && !pinnedOut.includes(hit)) pinnedOut.push(hit);
    }
    const rest = all.filter((a) => !pinnedOut.includes(a)).sort((a, b) => a.localeCompare(b));
    return [...pinnedOut, ...rest];
  }

  const orderedParamAxes = useMemo(() => orderAxes(paramAxisLabels, pinnedAxes), [paramAxisLabels, pinnedAxes]);

  // Keep slide-axes selection valid as available params change.
  useEffect(() => {
    setSlideParamAxesSelected((prev) => {
      const next = prev.filter((x) => paramAxisLabels.includes(x));
      if (next.length) return next;
      const def = DEFAULT_PINNED_AXES.filter((x) => paramAxisLabels.includes(x));
      return def.length ? def : ["cfg"];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramAxisLabels.join("|")]);

  function reorderSelectedAxes(prev: string[]): string[] {
    const seen = new Set<string>();
    const out: string[] = [];
    const push = (x: string) => {
      if (!x) return;
      if (seen.has(x)) return;
      seen.add(x);
      out.push(x);
    };
    // Always first
    push("run_key");
    push("status");
    // Pinned params next (only if the user already selected them; don't force-add).
    const prevLower = new Set(prev.map((x) => String(x).toLowerCase()));
    const byLower = new Map(axes.map((a) => [a.label.toLowerCase(), a.label] as const));
    for (const p of pinnedAxes) {
      const hit = byLower.get(String(p).toLowerCase()) ?? String(p);
      if (prevLower.has(String(hit).toLowerCase())) push(hit);
    }
    // Then whatever the user already had, preserving their order
    for (const x of prev) push(x);
    return out;
  }

  const orderedAxesForSidebar = useMemo(() => {
    // Keep these at the very top.
    // NOTE: `run_key` already contains exp_id + run_id, so keep identifiers *after* pinned params.
    const top = ["run_key", "status"];
    const identifiers = ["exp_id", "run_id"];
    const byLabel = new Map(axes.map((a) => [a.label, a] as const));
    const out: Axis[] = [];
    for (const k of top) {
      const ax = byLabel.get(k);
      if (ax) out.push(ax);
    }
    // Param axes (pinned-first ordering).
    for (const k of orderedParamAxes) {
      const ax = byLabel.get(k);
      if (ax) out.push(ax);
    }
    // Identifiers after pinned params.
    for (const k of identifiers) {
      const ax = byLabel.get(k);
      if (ax) out.push(ax);
    }
    // Everything else.
    for (const ax of axes) {
      if (out.find((x) => x.label === ax.label)) continue;
      out.push(ax);
    }
    return out;
  }, [axes, orderedParamAxes]);

  // Paging: Axes
  const axesPagingKey = useMemo(() => orderedAxesForSidebar.map((a) => a.label).join("|"), [orderedAxesForSidebar]);

  useEffect(() => {
    setAxesPager((prev) => ({ ...prev, page: 1 }));
  }, [axesPagingKey]);

  const pagedAxes = useMemo(() => {
    return pageSlice(orderedAxesForSidebar, axesPager.page, axesPager.pageSize);
  }, [orderedAxesForSidebar, axesPager.page, axesPager.pageSize]);

  useEffect(() => {
    if (axesPager.page !== pagedAxes.page) setAxesPager((prev) => ({ ...prev, page: pagedAxes.page }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pagedAxes.page]);

  // Keep table column order aligned with pinned-first config.
  useEffect(() => {
    setSelectedAxes((prev) => reorderSelectedAxes(prev));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinnedAxes.join("|"), axes.length]);

  // Persist sidebar accordion state + axes selections in one localStorage blob.
  useEffect(() => {
    _writeSidebarLocalState({
      v: 1,
      sidebar: {
        pinned_open: sidebarPinnedOpen,
        collapsed: sidebarCollapsed,
        facets: {
          viewer: sidebarViewerOpen,
          experiments: sidebarExperimentsOpen,
          queue: sidebarQueueOpen,
          axes: showAxes,
          cache: sidebarCacheOpen,
        },
      },
      axes: {
        columns: selectedAxes,
        pinned: pinnedAxes,
        slide_params: slideParamAxesSelected,
      },
    });

    // Legacy keys (keep for now to avoid surprising users during transition).
    try {
      localStorage.setItem("ui.pinned_axes", JSON.stringify(pinnedAxes));
      localStorage.setItem("ui.sidebar_pinned_open", sidebarPinnedOpen ? "1" : "0");
      localStorage.setItem("ui.sidebar_collapsed", sidebarCollapsed ? "1" : "0");
    } catch {
      // ignore
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    sidebarPinnedOpen,
    sidebarCollapsed,
    sidebarViewerOpen,
    sidebarExperimentsOpen,
    sidebarQueueOpen,
    showAxes,
    sidebarCacheOpen,
    pinnedAxes.join("|"),
    selectedAxes.join("|"),
    slideParamAxesSelected.join("|"),
  ]);

  useEffect(() => {
    try {
      // Drawer width is now stored in a cookie; keep localStorage write for backwards compat.
      localStorage.setItem("ui.sidebar_width", String(sidebarWidth));
    } catch {
      // ignore
    }
  }, [sidebarWidth]);

  useEffect(() => {
    try {
      localStorage.setItem("ui.select_submode", selectSubmode);
    } catch {
      // ignore
    }
  }, [selectSubmode]);

  useEffect(() => {
    try {
      localStorage.setItem("ui.pager.runs.page", String(runsPager.page));
      localStorage.setItem("ui.pager.runs.pageSize", String(runsPager.pageSize));
    } catch {
      // ignore
    }
  }, [runsPager.page, runsPager.pageSize]);

  useEffect(() => {
    try {
      localStorage.setItem("ui.pager.axes.page", String(axesPager.page));
      localStorage.setItem("ui.pager.axes.pageSize", String(axesPager.pageSize));
    } catch {
      // ignore
    }
  }, [axesPager.page, axesPager.pageSize]);

  useEffect(() => {
    try {
      localStorage.setItem("ui.pager.gallery.page", String(galleryPager.page));
      localStorage.setItem("ui.pager.gallery.pageSize", String(galleryPager.pageSize));
    } catch {
      // ignore
    }
  }, [galleryPager.page, galleryPager.pageSize]);

  useEffect(() => {
    try {
      if (galleryThumbAspect) localStorage.setItem("ui.gallery_thumb_aspect", galleryThumbAspect);
    } catch {
      // ignore
    }
  }, [galleryThumbAspect]);

  // Reset derived aspect when entering gallery so it adapts to the current media set.
  useEffect(() => {
    if (viewerKind !== "select" || selectSubmode !== "gallery") return;
    galleryThumbAspectKindRef.current = "";
    setGalleryThumbAspect("");
  }, [viewerKind, selectSubmode]);

  type SlideGroup = {
    key: string;
    label: string;
    base_mp4: string;
    fixed_seed?: number;
    fixed_duration_sec?: number;
    run_count: number;
  };

  const slideGroups = useMemo(() => {
    const m = new Map<string, SlideGroup>();
    for (const r of runs) {
      const e = experimentsByExpId[r.exp_id] ?? r.experiment;
      const base = String(e?.base_mp4 ?? "");
      const seed = typeof e?.fixed_seed === "number" ? e.fixed_seed : undefined;
      const dur = typeof e?.fixed_duration_sec === "number" ? e.fixed_duration_sec : undefined;
      const k = `${base}||${seed ?? ""}||${dur ?? ""}`;
      const prev = m.get(k);
      const baseName = base ? base.replace(/\\/g, "/").split("/").slice(-1)[0] : "(unknown)";
      if (!prev) {
        m.set(k, {
          key: k,
          label: `${baseName}  seed=${seed ?? "?"}  dur=${dur ?? "?"}s`,
          base_mp4: base,
          fixed_seed: seed,
          fixed_duration_sec: dur,
          run_count: 1,
        });
      } else {
        prev.run_count += 1;
      }
    }
    return [...m.values()].sort((a, b) => a.label.localeCompare(b.label));
  }, [runs, experimentsByExpId]);

  const slideGroup = useMemo(() => slideGroups.find((g) => g.key === slideGroupKey) ?? slideGroups[0], [slideGroups, slideGroupKey]);

  const slideA = selectedRunObjs[0] ?? null;
  const slideB = selectedRunObjs[1] ?? null;
  const slideABEnabled = Boolean(slideA && slideB);
  // In Slide mode, A is the "primary" anchor for navigation/slicing.
  // B is an alternate view (same slice cursor) into a different locked-params family.
  const slideSelected = slideAB === "B" && slideB ? slideB : slideA;
  const slidePrimaryAnchor = slideA ?? slideSelected ?? null;

  // If B disappears (selection cleared), fall back to A.
  useEffect(() => {
    if (slideAB !== "B") return;
    if (slideB) return;
    setSlideAB("A");
  }, [slideAB, slideB?.exp_id, slideB?.run_id]);

  // If the user selects a run, auto-switch Slide group to match it.
  // Otherwise selection can appear to "do nothing" when the selected run is outside the current group.
  useEffect(() => {
    if (viewerKind !== "slide") return;
    const sel = slidePrimaryAnchor;
    if (!sel) return;
    const e = experimentsByExpId[sel.exp_id] ?? sel.experiment;
    const base = String(e?.base_mp4 ?? "");
    const seed = typeof e?.fixed_seed === "number" ? e.fixed_seed : undefined;
    const dur = typeof e?.fixed_duration_sec === "number" ? e.fixed_duration_sec : undefined;
    const k = `${base}||${seed ?? ""}||${dur ?? ""}`;
    if (!k) return;
    if (!slideGroups.some((g) => g.key === k)) return;
    if (k === slideGroupKey) return;
    setSlideGroupKey(k);
    setSlideHint("Switched Slide group to match selected run");
    window.setTimeout(() => setSlideHint(""), 1200);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerKind, slidePrimaryAnchor?.exp_id, slidePrimaryAnchor?.run_id, slideGroups.length, experimentsByExpId]);

  const slideRuns = useMemo(() => {
    if (!slideGroup) return [];
    const base = slideGroup.base_mp4;
    const seed = slideGroup.fixed_seed;
    const dur = slideGroup.fixed_duration_sec;
    return runs.filter((r) => {
      const e = experimentsByExpId[r.exp_id] ?? r.experiment;
      if (String(e?.base_mp4 ?? "") !== base) return false;
      if ((typeof seed === "number" ? seed : undefined) !== (typeof e?.fixed_seed === "number" ? e.fixed_seed : undefined)) return false;
      if ((typeof dur === "number" ? dur : undefined) !== (typeof e?.fixed_duration_sec === "number" ? e.fixed_duration_sec : undefined)) return false;
      return true;
    });
  }, [runs, slideGroup, experimentsByExpId]);

  // For B, use the B anchor's group pool (Pair's right pane equivalent).
  const slideRunsB = useMemo(() => {
    if (!slideB) return [];
    const e0 = experimentsByExpId[slideB.exp_id] ?? slideB.experiment;
    const base = String(e0?.base_mp4 ?? "");
    const seed = typeof e0?.fixed_seed === "number" ? e0.fixed_seed : undefined;
    const dur = typeof e0?.fixed_duration_sec === "number" ? e0.fixed_duration_sec : undefined;
    const filtered = runs.filter((r) => {
      const e = experimentsByExpId[r.exp_id] ?? r.experiment;
      if (String(e?.base_mp4 ?? "") !== base) return false;
      if ((typeof seed === "number" ? seed : undefined) !== (typeof e?.fixed_seed === "number" ? e.fixed_seed : undefined)) return false;
      if ((typeof dur === "number" ? dur : undefined) !== (typeof e?.fixed_duration_sec === "number" ? e.fixed_duration_sec : undefined)) return false;
      return true;
    });
    // Ensure B run is always in the pool (e.g. when group metadata doesn't match or B is not in runs yet).
    const hasB = filtered.some((r) => r.exp_id === slideB.exp_id && r.run_id === slideB.run_id);
    return hasB ? filtered : [slideB];
  }, [runs, slideB, experimentsByExpId]);

  // Clear history when switching groups.
  useEffect(() => {
    setRecentGood([]);
  }, [slideGroup?.key]);

  function hasMedia(r: RunsItem): boolean {
    return Boolean(r.primary_video?.url || r.primary_image?.url);
  }

  // Use media-only runs for navigation so we avoid "No media found for this slice" in normal use.
  const slideRunsWithMedia = useMemo(() => slideRuns.filter(hasMedia), [slideRuns]);
  const slideRunsBWithMedia = useMemo(() => slideRunsB.filter(hasMedia), [slideRunsB]);

  const sortedSlideRuns = useMemo(() => {
    const rr = [...slideRuns];
    rr.sort((a, b) => {
      const c = String(a.run_id ?? "").localeCompare(String(b.run_id ?? ""), undefined, { numeric: true, sensitivity: "base" });
      if (c) return c;
      return runKey(a).localeCompare(runKey(b));
    });
    return rr;
  }, [slideRuns]);

  const sortedSlideRunsWithMedia = useMemo(() => {
    const rr = [...slideRunsWithMedia];
    rr.sort((a, b) => {
      const c = String(a.run_id ?? "").localeCompare(String(b.run_id ?? ""), undefined, { numeric: true, sensitivity: "base" });
      if (c) return c;
      return runKey(a).localeCompare(runKey(b));
    });
    return rr;
  }, [slideRunsWithMedia]);

  // Deterministic "re-entry" run when we need to recover from an impossible slice.
  const slideEntryRun = useMemo(() => sortedSlideRunsWithMedia[0] ?? sortedSlideRuns[0] ?? null, [sortedSlideRuns, sortedSlideRunsWithMedia]);

  function cursorSig(cur: Record<string, unknown>, axes: string[]): string {
    try {
      return axes.map((a) => `${a}=${JSON.stringify(cur[a])}`).join("|");
    } catch {
      return axes.map((a) => `${a}=${String(cur[a])}`).join("|");
    }
  }

  function nearestRunForCursor(): { run: RunsItem; matched: number } | null {
    const src = slideActiveRunsWithMedia.length ? slideActiveRunsWithMedia : slideActiveRuns;
    if (!src.length) return null;

    const lockedFiltered = src.filter((r) => {
      const p = r.params ?? {};
      for (const [k, v] of Object.entries(slideActiveLockedParams)) {
        if (!eq(p[k], v)) return false;
      }
      return true;
    });
    const pool = lockedFiltered.length ? lockedFiltered : src;

    let best: RunsItem | null = null;
    let bestScore = -1;
    for (const r of pool) {
      const p = r.params ?? {};
      let score = 0;
      for (const a of slideAxes) {
        const curV = slideActiveCursor[a];
        if (isWildcard(curV)) continue;
        if (eq(p[a], curV)) score += 1;
      }
      const active = slideAxes[slideActiveAxis];
      if (active) {
        const curV = slideActiveCursor[active];
        if (!isWildcard(curV) && eq(p[active], curV)) score += 0.25;
      }
      if (score > bestScore) {
        bestScore = score;
        best = r;
      }
    }
    if (!best) return null;
    return { run: best, matched: Math.floor(bestScore) };
  }

  function eq(a: unknown, b: unknown): boolean {
    if (a === b) return true;
    if (a == null || b == null) return false;
    const na = typeof a === "number" ? a : Number(a);
    const nb = typeof b === "number" ? b : Number(b);
    if (Number.isFinite(na) && Number.isFinite(nb)) return na === nb;
    try {
      return JSON.stringify(a) === JSON.stringify(b);
    } catch {
      return String(a) === String(b);
    }
  }

  // In Slide mode, allow unset cursor values to act as wildcards.
  // This prevents missing/unknown axes from forcing "No exact match".
  function isWildcard(v: unknown): boolean {
    return v == null || v === "";
  }

  function initSlideFromAnchor(anchor: RunsItem) {
    const key = runKey(anchor);
    const useAxes = desiredSlideAxes(slideParamAxesSelected);
    setSlideAnchorKey(key);
    setSlideAxes(useAxes);
    setSlideActiveAxis(0);
    setSlideCursor((prev) => {
      const next: Record<string, unknown> = { ...prev };
      for (const a of useAxes) next[a] = (anchor.params ?? ({} as Record<string, unknown>))[a];
      return next;
    });
    setNextAxes(useAxes);
  }

  useEffect(() => {
    if (viewerKind !== "slide") return;
    if (!slideGroup && slideGroups.length) setSlideGroupKey(slideGroups[0].key);
    // Prefer explicitly selected run as anchor, else keep existing anchor, else first run with media.
    const sel = slidePrimaryAnchor;
    const anchor =
      sel && (!slideGroup || slideRuns.some((r) => runKey(r) === runKey(sel)))
        ? sel
        : slideAnchorKey
          ? slideRuns.find((r) => runKey(r) === slideAnchorKey)
          : undefined;
    const fallback = anchor ?? slideRunsWithMedia[0] ?? slideRuns.find((r) => r.primary_video?.url) ?? slideRuns[0];
    if (fallback) initSlideFromAnchor(fallback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerKind, slidePrimaryAnchor?.exp_id, slidePrimaryAnchor?.run_id, slideGroupKey, slideGroups.length, slideRuns.length, slideRunsWithMedia.length]);

  useEffect(() => {
    // Slide uses the same *param axes* as the columns selection.
    setSlideAxes(desiredSlideAxes(slideParamAxesSelected));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramAxisLabels.join("|"), slideParamAxesSelected.join("|")]);

  // Ensure slide cursor always has values for all slide axes, otherwise the grid can become empty
  // and navigation will appear "stuck".
  useEffect(() => {
    if (viewerKind !== "slide") return;
    const base = slideAnchor ?? slideSelected ?? slideRuns[0];
    if (!base) return;
    setSlideCursor((prev) => {
      const next: Record<string, unknown> = { ...prev };
      const baseParams = base.params ?? {};
      for (const a of slideAxes) {
        if (next[a] != null) continue;
        const v = (baseParams as Record<string, unknown>)[a];
        if (v != null) next[a] = v;
        else {
          const src = slideRunsWithMedia.length ? slideRunsWithMedia : slideRuns;
          const vals = uniq(src.map((r) => (r.params ?? {})[a])).filter((x) => x != null);
          vals.sort(cmp);
          next[a] = vals[0] ?? "";
        }
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    viewerKind,
    slideAxes.join("|"),
    slideAnchorKey,
    slideSelected?.exp_id,
    slideSelected?.run_id,
    slideRuns.length,
    slideRunsWithMedia.length,
  ]);

  // Ensure B cursor always has values for all slide axes (so switching to B doesn't dead-end).
  useEffect(() => {
    if (viewerKind !== "slide") return;
    if (!slideB) return;
    const base = slideB ?? slideRunsB[0];
    if (!base) return;
    setSlideCursorB((prev) => {
      const next: Record<string, unknown> = { ...prev };
      const baseParams = base.params ?? {};
      for (const a of slideAxes) {
        if (next[a] != null) continue;
        const v = (baseParams as Record<string, unknown>)[a];
        if (v != null) next[a] = v;
        else {
          const src = slideRunsBWithMedia.length ? slideRunsBWithMedia : slideRunsB;
          const vals = uniq(src.map((r) => (r.params ?? {})[a])).filter((x) => x != null);
          vals.sort(cmp);
          next[a] = vals[0] ?? "";
        }
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerKind, slideAxes.join("|"), slideB?.exp_id, slideB?.run_id, slideRunsB.length, slideRunsBWithMedia.length]);

  // Equalize scorebox widths (use widest box), but clamp so HUD stays narrow.
  useEffect(() => {
    if (viewerKind !== "slide") return;
    const el = scoreboardRef.current;
    if (!el) return;
    const raf = requestAnimationFrame(() => {
      const boxes = Array.from(el.querySelectorAll<HTMLElement>(".scorebox"));
      if (!boxes.length) return;
      let max = 0;
      for (const b of boxes) max = Math.max(max, b.scrollWidth);
      // scrollWidth excludes borders; add a tiny buffer so we don't clip.
      const clamped = Math.max(88, Math.min(131, Math.ceil(max + 2)));
      setScoreboxW((prev) => (prev === clamped ? prev : clamped));
    });
    return () => cancelAnimationFrame(raf);
  }, [viewerKind, slideAxes.join("|"), slideCursor]);

  useEffect(() => {
    setNextAxes((prev) => {
      const next = prev.filter((x) => paramAxisLabels.includes(x));
      if (next.length) return next;
      const def = DEFAULT_PINNED_AXES.filter((x) => paramAxisLabels.includes(x));
      return def.length ? def : ["cfg"];
    });
  }, [paramAxisLabels]);

  useEffect(() => {
    // Keep active axis in range.
    setSlideActiveAxis((i) => {
      if (i < 0) return 0;
      if (i >= slideAxes.length) return Math.max(0, slideAxes.length - 1);
      return i;
    });
  }, [slideAxes.length]);

  const slideAnchor = useMemo(() => {
    if (!slideAnchorKey) return null;
    const [exp_id, run_id] = slideAnchorKey.split("::");
    return slideRuns.find((r) => r.exp_id === exp_id && r.run_id === run_id) ?? null;
  }, [slideAnchorKey, slideRuns]);

  function lockedParamsForAnchor(a: RunsItem | null): Record<string, unknown> {
    if (!a) return {};
    const lock: Record<string, unknown> = { ...(a.params ?? {}) };
    for (const k of slideAxes) delete lock[k];
    for (const [k, v] of Object.entries(lock)) {
      if (v == null) delete lock[k];
    }
    return lock;
  }

  const slideLockedParams = useMemo(() => {
    return lockedParamsForAnchor(slideAnchor);
  }, [slideAnchor, slideAxes]);

  const slideLockedParamsB = useMemo(() => {
    return lockedParamsForAnchor(slideB);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slideB?.exp_id, slideB?.run_id, slideAxes.join("|")]);

  const slideAxesForPane = useMemo(
    () => slideAxes.map((label) => axes.find((ax) => ax.label === label)).filter((ax): ax is Axis => ax != null),
    [axes, slideAxes]
  );

  function findRunForCursor(
    srcAll: RunsItem[],
    srcWithMedia: RunsItem[],
    lockedParams: Record<string, unknown>,
    cursor: Record<string, unknown>
  ): {
    exact: RunsItem | null;
    nearest: RunsItem | null;
    matched: number;
  } {
    const src = srcWithMedia.length ? srcWithMedia : srcAll;
    if (!src.length) return { exact: null, nearest: null, matched: 0 };

    const exact = src.find((r) => {
      const p = r.params ?? {};
      for (const [k, v] of Object.entries(lockedParams)) {
        if (!eq(p[k], v)) return false;
      }
      for (const a of slideAxes) {
        const curV = cursor[a];
        if (isWildcard(curV)) continue;
        if (!eq(p[a], curV)) return false;
      }
      return true;
    });
    if (exact) return { exact, nearest: exact, matched: slideAxes.length };

    const lockedFiltered = src.filter((r) => {
      const p = r.params ?? {};
      for (const [k, v] of Object.entries(lockedParams)) {
        if (!eq(p[k], v)) return false;
      }
      return true;
    });
    const pool = lockedFiltered.length ? lockedFiltered : src;

    let best: RunsItem | null = null;
    let bestScore = -1;
    for (const r of pool) {
      const p = r.params ?? {};
      let score = 0;
      for (const a of slideAxes) {
        const curV = cursor[a];
        if (isWildcard(curV)) continue;
        if (eq(p[a], curV)) score += 1;
      }
      const active = slideAxes[slideActiveAxis];
      if (active) {
        const curV = cursor[active];
        if (!isWildcard(curV) && eq(p[active], curV)) score += 0.25;
      }
      if (score > bestScore) {
        bestScore = score;
        best = r;
      }
    }
    return { exact: null, nearest: best, matched: Math.max(0, Math.floor(bestScore)) };
  }

  function candidatesForAxis(axis: string): RunsItem[] {
    const src = slideActiveRunsWithMedia.length ? slideActiveRunsWithMedia : slideActiveRuns;
    return src.filter((r) => {
      const p = r.params ?? {};
      for (const [k, v] of Object.entries(slideActiveLockedParams)) {
        if (!eq(p[k], v)) return false;
      }
      for (const a of slideAxes) {
        if (a === axis) continue;
        const curV = slideActiveCursor[a];
        if (isWildcard(curV)) continue;
        if (!eq(p[a], curV)) return false;
      }
      return true;
    });
  }

  function valuesForAxis(axis: string): unknown[] {
    const vals = uniq(candidatesForAxis(axis).map((r) => (r.params ?? {})[axis])).filter((v) => v != null);
    return vals.sort(cmp);
  }

  const slideInfoA = useMemo(() => {
    return findRunForCursor(slideRuns, slideRunsWithMedia, slideLockedParams, slideCursor);
  }, [slideRuns, slideRunsWithMedia, slideAxes, slideCursor, slideLockedParams, slideActiveAxis]);
  const slideInfoB = useMemo(
    () =>
      slideB
        ? findRunForCursor(slideRunsB, slideRunsBWithMedia, slideLockedParamsB, slideCursorB)
        : { exact: null, nearest: null, matched: 0 },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [slideB?.exp_id, slideB?.run_id, slideRunsB, slideRunsBWithMedia, slideAxes, slideCursorB, slideLockedParamsB, slideActiveAxis]
  );

  const slideRunA = slideInfoA.nearest ?? null;
  // B in Slide view is always the selected B run (same as Pair view), not cursor-based.
  const slideRunB = slideB ?? null;
  const activeSlot: "A" | "B" = slideAB === "B" && slideABEnabled ? "B" : "A";
  const slideRun = activeSlot === "B" ? slideRunB : slideRunA;
  const slideRunExact = activeSlot === "B" ? true : Boolean(slideInfoA.exact);

  const slideActiveCursor = activeSlot === "B" ? slideCursorB : slideCursor;
  const setSlideActiveCursor = activeSlot === "B" ? setSlideCursorB : setSlideCursor;
  const slideActiveLockedParams = activeSlot === "B" ? slideLockedParamsB : slideLockedParams;
  const slideActiveRuns = activeSlot === "B" ? slideRunsB : slideRuns;
  const slideActiveRunsWithMedia = activeSlot === "B" ? slideRunsBWithMedia : slideRunsWithMedia;

  // When viewing B, keep B cursor in sync with the selected B run (same run as Pair view).
  useEffect(() => {
    if (viewerKind !== "slide") return;
    if (activeSlot !== "B") return;
    if (!slideB) return;
    if (slideInfoB.exact) return;
    setSlideCursorB((prev) => {
      const next = { ...prev };
      const p = slideB.params ?? {};
      for (const a of slideAxes) next[a] = p[a];
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerKind, activeSlot, slideB?.exp_id, slideB?.run_id, slideAxes.join("|")]);

  const lastSnapSig = useRef<string>("");

  // Clear any previous media error when changing the active slide run.
  useEffect(() => {
    setSlideMediaError("");
  }, [viewerKind, slideRun?.exp_id, slideRun?.run_id]);

  // Debug: log Slide B state when in Slide view (helps diagnose B not showing).
  useEffect(() => {
    if (viewerKind !== "slide") return;
    const bKey = selectedRuns[1];
    const bInRuns = bKey ? runs.some((r) => runKey(r) === bKey) : false;
    const debug = {
      activeSlot,
      slideABEnabled,
      selectedRuns: selectedRuns.slice(0, 2),
      bKey,
      bInRuns,
      slideB: slideB
        ? {
            exp_id: slideB.exp_id,
            run_id: slideB.run_id,
            hasPrimaryVideo: Boolean(slideB.primary_video?.url),
            hasPrimaryImage: Boolean(slideB.primary_image?.url),
          }
        : null,
      slideRunB: slideRunB
        ? {
            exp_id: slideRunB.exp_id,
            run_id: slideRunB.run_id,
            hasPrimaryVideo: Boolean(slideRunB.primary_video?.url),
            hasPrimaryImage: Boolean(slideRunB.primary_image?.url),
          }
        : null,
      slideRun: slideRun
        ? {
            exp_id: slideRun.exp_id,
            run_id: slideRun.run_id,
            hasPrimaryVideo: Boolean(slideRun.primary_video?.url),
            hasPrimaryImage: Boolean(slideRun.primary_image?.url),
          }
        : null,
    };
    console.log("[Slide B debug]", debug);
  }, [
    viewerKind,
    activeSlot,
    slideABEnabled,
    selectedRuns,
    slideB,
    slideRunB,
    slideRun,
    runs.length,
  ]);

  const slideHasRenderableMedia = Boolean(slideRun?.primary_video?.url || slideRun?.primary_image?.url);

  const slideDebug = typeof window !== "undefined" && window.location.search.includes("slideDebug");

  // Track a small history of "good" (media) axis combinations for quick recovery when a slice is missing.
  useEffect(() => {
    if (viewerKind !== "slide") return;
    if (!slideRun) return;
    if (!hasMedia(slideRun)) return;
    const p = slideRun.params ?? {};
    const values: Record<string, unknown> = {};
    for (const a of slideAxes) values[a] = p[a];
    const sig = cursorSig(values, slideAxes);
    const label = slideAxes.map((a) => `${a}=${fmt(values[a])}`).join("  ");
    const rk = runKey(slideRun);
    setRecentGood((prev) => {
      const next = [{ sig, values, label, run_key: rk }, ...prev.filter((x) => x.sig !== sig)];
      return next.slice(0, 6);
    });
  }, [viewerKind, slideRun?.exp_id, slideRun?.run_id, slideAxes.join("|")]);

  // If cursor lands on an impossible slice (no media run), snap to the nearest available run
  // to keep navigation from dead-ending.
  useEffect(() => {
    if (viewerKind !== "slide") return;
    if (slideRun) return;
    const src = slideRunsWithMedia.length ? slideRunsWithMedia : slideRuns;
    if (!src.length) return;
    const sig = cursorSig(slideActiveCursor, slideAxes);
    if (sig && sig === lastSnapSig.current) return;
    const near = nearestRunForCursor();
    const entry = near?.run ?? slideEntryRun;
    if (!entry) return;
    lastSnapSig.current = sig;
    setSlideActiveCursor((prev) => {
      const next = { ...prev };
      const p = entry.params ?? {};
      for (const a of slideAxes) next[a] = p[a];
      return next;
    });
    const hint =
      near && near.matched > 0
        ? `Snapped to nearest slice (${near.matched}/${slideAxes.length} axes matched)`
        : `Re-entered at lowest run (${entry.run_id})`;
    setSlideHint(hint);
    const t = window.setTimeout(() => setSlideHint(""), 1800);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerKind, slideRun, slideEntryRun?.exp_id, slideEntryRun?.run_id, slideRuns.length, slideRunsWithMedia.length, slideAxes.join("|"), slideCursor, slideLockedParams, slideActiveAxis]);

  const slideZP = useZoomPan({
    onTwoFingerSwipe: (dx, dy) => {
      const adx = Math.abs(dx);
      const ady = Math.abs(dy);
      const th = 52;
      if (adx < th && ady < th) return;
      if (ady > adx) slideMoveAxis(dy < 0 ? -1 : 1);
      else slideMoveValue(dx < 0 ? -1 : 1);
    },
  });
  const slideVideoARef = useRef<HTMLVideoElement | null>(null);
  const slideVideoBRef = useRef<HTMLVideoElement | null>(null);
  const slideMetaARef = useRef<{ w?: number; h?: number; duration?: number } | null>(null);
  const slideMetaBRef = useRef<{ w?: number; h?: number; duration?: number } | null>(null);

  // When toggling A/B, reuse cached metadata to keep stage sizing stable.
  useEffect(() => {
    if (viewerKind !== "slide") return;
    const m = activeSlot === "B" ? slideMetaBRef.current : slideMetaARef.current;
    if (!m?.w || !m?.h) return;
    setSlideMeta(m);
    slideZP.setMediaSize({ w: m.w, h: m.h });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerKind, activeSlot]);

  // In Slide A/B, always sync playback between the two videos.
  useEffect(() => {
    if (viewerKind !== "slide") return;
    const a = slideVideoARef.current;
    const b = slideVideoBRef.current;
    if (!a || !b) return;

    const tick = () => {
      const master = activeSlot === "B" ? b : a;
      const other = activeSlot === "B" ? a : b;
      if (!master || !other) return;

      // Sync play/pause + rate.
      const masterPlaying = !master.paused && !master.ended;
      other.playbackRate = Number(master.playbackRate) || 1;
      if (!masterPlaying) {
        other.pause();
      } else {
        void other.play().catch(() => {});
      }

      // Sync time (soft chase + occasional hard seek).
      const diff = (Number(other.currentTime) || 0) - (Number(master.currentTime) || 0); // + => other ahead
      const ad = Math.abs(diff);
      if (ad > 0.6) {
        try {
          other.currentTime = master.currentTime;
        } catch {
          // ignore
        }
      } else if (ad > 0.06) {
        // Nudge via rate for a moment.
        if (diff > 0) other.playbackRate = Math.max(0.8, other.playbackRate * 0.92);
        else other.playbackRate = Math.min(1.25, other.playbackRate * 1.08);
      }
    };

    const t = window.setInterval(tick, 220);
    // Run once immediately.
    tick();
    return () => window.clearInterval(t);
  }, [viewerKind, activeSlot, slideRunA?.primary_video?.url, slideRunB?.primary_video?.url]);
  const scoreboardRef = useRef<HTMLDivElement | null>(null);
  const [scoreboxW, setScoreboxW] = useState<number>(0);
  const slideHudRef = useRef<HTMLDivElement | null>(null);
  const [hudHotZoneW, setHudHotZoneW] = useState<number>(180);
  const hudHotRef = useRef<boolean>(false);
  const hudHideTimerRef = useRef<number | null>(null);

  // (zoom/pan handlers now live in useZoomPan)

  const slideCriteriaKey = viewerKind === "slide" ? String(slideGroup?.key ?? "") : "";
  const slideIsLoading =
    viewerKind === "slide" && selectedExpId != null && loadingRunsExpId === selectedExpId && slideRuns.length === 0;
  const slideNavEnabled =
    viewerKind === "slide" && !slideIsLoading && (slideRunsWithMedia.length > 0 || slideRunsBWithMedia.length > 0);

  // Prevent selection feedback loops: only write Slide's current run back into A/B selection
  // when the user explicitly navigated values (left/right), not when Slide changes due to
  // loading, snapping, anchoring, or group switches.
  const slideNavWritebackRef = useRef<{ slot: "A" | "B"; ts: number } | null>(null);
  function markSlideValueNav() {
    const slot: "A" | "B" = slideAB === "B" && slideABEnabled ? "B" : "A";
    slideNavWritebackRef.current = { slot, ts: Date.now() };
  }

  useEffect(() => {
    if (viewerKind !== "slide") return;
    if (!slideNavEnabled) return;
    if (!slideRun) return;
    const st = slideNavWritebackRef.current;
    if (!st) return;
    // Only accept very recent marks (avoid applying after unrelated rerenders).
    if (Date.now() - st.ts > 1200) {
      slideNavWritebackRef.current = null;
      return;
    }
    slideNavWritebackRef.current = null;
    setSelectedSlot(st.slot, slideRun);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerKind, slideNavEnabled, slideRun?.exp_id, slideRun?.run_id]);

  function setSelectedSlot(slot: "A" | "B", r: RunsItem) {
    const key = runKey(r);
    setSelectedRuns((prev) => {
      const a0 = prev[0] ?? "";
      const b0 = prev[1] ?? "";
      let a = a0;
      let b = b0;
      if (slot === "A") a = key;
      else b = key;

      // Keep unique + compact.
      if (a && b && a === b) {
        if (slot === "A") b = "";
        else a = "";
      }
      const next = [a, b].filter(Boolean);
      if (next[0] === a0 && next[1] === b0 && next.length === prev.length) return prev;
      return next;
    });
  }


  // Keep HUD hover hot-zone aligned with actual HUD width.
  useEffect(() => {
    if (viewerKind !== "slide") return;
    const el = slideHudRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const w = Math.ceil(el.getBoundingClientRect().width);
      if (w > 0) setHudHotZoneW(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [viewerKind]);

  // Cleanup any pending HUD hide timer.
  useEffect(() => {
    return () => {
      if (hudHideTimerRef.current != null) {
        window.clearTimeout(hudHideTimerRef.current);
        hudHideTimerRef.current = null;
      }
    };
  }, []);

  function setHudHot(next: boolean) {
    // Don't show HUD in "no slices" states where the viewer is showing buttons/messages.
    if (!slideNavEnabled) return;
    const HIDE_DELAY_MS = 400;
    if (next) {
      if (hudHideTimerRef.current != null) {
        window.clearTimeout(hudHideTimerRef.current);
        hudHideTimerRef.current = null;
      }
      if (next !== hudHotRef.current) {
        hudHotRef.current = true;
        setSlideShowHud(true);
      }
      return;
    }
    // Delay hiding to avoid flicker on brief lane departures.
    if (hudHideTimerRef.current != null) return;
    hudHideTimerRef.current = window.setTimeout(() => {
      hudHideTimerRef.current = null;
      hudHotRef.current = false;
      setSlideShowHud(false);
    }, HIDE_DELAY_MS);
  }

  function slideMoveAxis(delta: number) {
    if (!slideAxes.length) return;
    setSlideActiveAxis((i) => {
      const n = slideAxes.length;
      const next = (i + delta + n) % n;
      return next;
    });
  }

  function slideMoveValue(delta: number) {
    const axis = slideAxes[slideActiveAxis];
    if (!axis) return;
    const vals = valuesForAxis(axis);
    if (!vals.length) return;
    const cur = slideActiveCursor[axis];
    let idx = vals.findIndex((v) => eq(v, cur));
    if (idx < 0) idx = 0;
    const next = vals[(idx + delta + vals.length) % vals.length];
    markSlideValueNav();
    setSlideActiveCursor((prev) => ({ ...prev, [axis]: next }));
  }

  function slideMoveValueForAxis(axis: string, axisIndex: number, delta: number) {
    if (!axis) return;
    const vals = valuesForAxis(axis);
    if (vals.length <= 1) return;
    setSlideActiveAxis(axisIndex);
    markSlideValueNav();
    setSlideActiveCursor((prev) => {
      const cur = prev[axis];
      let idx = vals.findIndex((v) => eq(v, cur));
      if (idx < 0) idx = 0;
      const next = vals[(idx + delta + vals.length) % vals.length];
      return { ...prev, [axis]: next };
    });
  }

  function resetSlideSlice() {
    const base = slideEntryRun;
    if (!base) return;
    initSlideFromAnchor(base);
    setSlideHint("Reset slice");
    window.setTimeout(() => setSlideHint(""), 1200);
  }

  useEffect(() => {
    if (viewerKind !== "slide") return;
    const onKey = (e: KeyboardEvent) => {
      if (nextOpen) return;
      // If a video player is present/targeted, don't steal arrow/space keys from it.
      // This preserves native video controls (seek w/ arrows, pause/play w/ space).
      const isArrow =
        e.key === "ArrowUp" || e.key === "ArrowDown" || e.key === "ArrowLeft" || e.key === "ArrowRight";
      const isSpace = e.code === "Space" || e.key === " " || e.key === "Spacebar";
      if (isArrow || isSpace) {
        const t = e.target as HTMLElement | null;
        const ae = (document.activeElement as HTMLElement | null) ?? null;
        const inVideo = Boolean(t?.closest?.("video") || ae?.closest?.("video"));
        const hasSlideVideo = Boolean(slideRun?.primary_video?.url);
        if (inVideo || hasSlideVideo) return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        slideMoveAxis(-1);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        slideMoveAxis(1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        slideMoveValue(-1);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        slideMoveValue(1);
      } else if (e.key === "Escape") {
        setNextOpen(false);
      }
    };
    window.addEventListener("keydown", onKey, { passive: false });
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerKind, slideAxes, slideActiveAxis, slideCursor, slideLockedParams, nextOpen, slideRun?.primary_video?.url]);

  // Quick toggle between pair/slide viewer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (nextOpen) return;
      if (e.key !== "v" && e.key !== "V") return;
      const ae = document.activeElement as HTMLElement | null;
      const tag = (ae?.tagName ?? "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      e.preventDefault();
      setViewerKind((prev) => {
        const next = prev === "pair" ? "slide" : "pair";
        if (next === "slide") {
          const anchor = selectedRunObjs[0];
          if (anchor) initSlideFromAnchor(anchor);
        }
        return next;
      });
    };
    window.addEventListener("keydown", onKey, { passive: false });
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nextOpen, selectedRunObjs]);

  // Prime "next experiment" defaults from current slide cursor.
  useEffect(() => {
    if (!slideRun) return;
    setNextAxes((prev) => (prev.length ? prev : slideAxes));
    setNextValues((prev) => {
      const out: Record<string, string> = { ...prev };
      for (const a of nextAxes.length ? nextAxes : slideAxes) {
        if (out[a]) continue;
        const vals = valuesForAxis(a);
        const cur = (slideRun.params ?? {})[a];
        const idx = vals.findIndex((v) => eq(v, cur));
        const pick = [];
        if (idx > 0) pick.push(vals[idx - 1]);
        pick.push(cur);
        if (idx >= 0 && idx < vals.length - 1) pick.push(vals[idx + 1]);
        out[a] = pick.map((x) => String(x)).join(", ");
      }
      return out;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slideRun?.exp_id, slideRun?.run_id]);

  function parseList(s: string): string[] {
    return s
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
  }

  const nextEstRuns = useMemo(() => {
    let est = 1;
    for (const a of nextAxes) {
      const n = parseList(nextValues[a] ?? "").length;
      est *= Math.max(1, n);
    }
    return est;
  }, [nextAxes, nextValues]);

  async function submitNextExperiment() {
    if (!slideRun) return;
    const sweep: Record<string, unknown> = {};
    for (const a of nextAxes) {
      const parts = parseList(nextValues[a] ?? "");
      sweep[a] = parts.map((p) => {
        const n = Number(p);
        return Number.isFinite(n) ? n : p;
      });
    }
    setError("");
    try {
      const res = await createNextExperiment({
        anchor: { exp_id: slideRun.exp_id, run_id: slideRun.run_id },
        sweep,
        baseline_first: nextBaselineFirst,
        max_runs: nextMaxRuns,
        no_wait: nextNoWait,
        submit_all: true,
      });
      setNextOpen(false);
      await refreshExperiments();
      setSelectedExpId(res.exp_id);
      setExpandedExpIds((prev) => (prev.includes(res.exp_id) ? prev : [...prev, res.exp_id]));
      await loadRunsForExp(res.exp_id);
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <DeviceProvider>
    <div
      className={`app ${focusPreview ? "focus-preview" : ""}`}
      style={
        !focusPreview
          ? ({
              ["--sidebar-w" as any]: `${showSidebar ? (sidebarCollapsed ? SIDEBAR_COLLAPSED_W : clampSidebarW(sidebarWidth)) : 0}px`,
              ["--app-gap" as any]: showSidebar ? "12px" : "0px",
            } as any)
          : undefined
      }
    >
      <div
        className={`panel sidebar-panel ${focusPreview ? "hidden" : ""} ${showSidebar ? "" : "drawer-closed"} ${
          sidebarCollapsed ? "collapsed" : ""
        }`}
        aria-hidden={!showSidebar && !focusPreview ? true : undefined}
      >
        <div className="preview-toolbar" style={{ marginBottom: 10 }}>
          {!sidebarCollapsed ? (
            <h2 className="title" style={{ margin: 0 }}>
              Experiments
            </h2>
          ) : null}
          <div className="right">
            <button
              type="button"
              className="icon-btn"
              onClick={() => setSidebarCollapsed((v) => !v)}
              title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {sidebarCollapsed ? (
                <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
                  <path d="M10 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
                  <path d="M14 6l-6 6 6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </button>
          </div>
        </div>

        {!sidebarCollapsed ? <div className="sidebar-resize-handle" onPointerDown={beginSidebarResize} title="Drag to resize" /> : null}

        <div className={`sidebar-scroll ${sidebarCollapsed ? "hidden" : ""}`}>
          <div className="sidebar-group-title">Data</div>

          <ExperimentList
            experiments={filteredExperiments}
            filterValue={expFilter}
            onFilterChange={setExpFilter}
            expandedExpIds={expandedExpIds}
            onToggleExpand={toggleExpandedExp}
            runsByExpId={runsByExpId}
            loadingRunsExpId={loadingRunsExpId}
            onLoadRuns={loadRunsForExp}
            queue={queueData}
            selectedExpId={selectedExpId}
            onSelectExperiment={(expId) => {
              if (expId == null) return;
              setSelectedExpId(expId);
              setExpandedExpIds((prev) => (prev.includes(expId) ? prev : [...prev, expId]));
              if (!runsByExpId[expId]) loadRunsForExp(expId);
            }}
            selectedRunKeys={selectedRuns}
            onSetSlot={setSelectedSlot}
            onRefresh={() => void refreshExperiments()}
            open={sidebarExperimentsOpen}
            onToggleOpen={() => setSidebarExperimentsOpen((v) => !v)}
          />

          <FacetSection
            title="Queue"
            open={sidebarQueueOpen}
            onToggle={() =>
              setSidebarQueueOpen((v) => {
                const next = !v;
                if (next) void refreshQueue();
                return next;
              })
            }
            meta={
              <span style={{ color: "var(--muted)", fontSize: 12 }}>
                exp <span className="mono">{queueData?.experiments?.length ?? 0}</span> · comfy{" "}
                <span className="mono">
                  {queueData?.comfyui?.running?.length ?? 0}/{queueData?.comfyui?.pending?.length ?? 0}
                </span>
              </span>
            }
            actions={
              <button
                type="button"
                className="icon-btn"
                onClick={() => refreshQueue()}
                title="Refresh queue"
                aria-label="Refresh queue"
              >
                <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
                  <path
                    d="M20 12a8 8 0 1 1-2.34-5.66"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  />
                  <path
                    d="M20 4v6h-6"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            }
          >
            <div style={{ display: "grid", gap: 8 }}>
              {queueError ? <div style={{ color: "var(--bad)", fontFamily: "var(--mono)", fontSize: 12 }}>{queueError}</div> : null}
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <button
                  type="button"
                  onClick={() => {
                    void refreshQueue();
                    setMainMode("queue");
                  }}
                  title="Open the Queue viewer on the right"
                >
                  Open Queue Viewer
                </button>
                <button type="button" onClick={() => setMainMode("runs")} disabled={mainMode === "runs"} title="Return to Viewer">
                  Back to Viewer
                </button>
                <span style={{ color: "var(--muted)", fontSize: 12 }}>
                  ComfyUI: <span className="mono">{queueData?.comfyui?.running?.length ?? 0}</span> running ·{" "}
                  <span className="mono">{queueData?.comfyui?.pending?.length ?? 0}</span> pending
                </span>
              </div>
            </div>
          </FacetSection>

          <FacetSection
            title="Create from WIP"
            open={sidebarWipOpen}
            onToggle={() => setSidebarWipOpen((v) => !v)}
          >
            <WipSidebarContent
              planned={wipPlanned}
              onPlannedChange={setWipPlanned}
              selectedWipRelpath={wipSelectedRelpath}
              onSelectWip={(relpath) => {
                setWipSelectedRelpath(relpath);
                setCreateSource(relpath ? { type: "wip", relpath, videoName: relpath.replace(/\\/g, "/").split("/").pop() ?? "video" } : null);
              }}
              editingId={wipEditingId}
              onEdit={setWipEditingId}
              dates={wipDates}
              media={wipMedia}
              currentDir={wipCurrentDir}
              loading={wipLoading}
              error={wipError}
              onLoadWip={loadWip}
            />
          </FacetSection>

          <div className="sidebar-group-title">View</div>

          <FacetSection title="Viewer" open={sidebarViewerOpen} onToggle={() => setSidebarViewerOpen((v) => !v)}>
            <div className="row" style={{ margin: "0 0 6px 0" }}>
              <label style={{ width: 120 }}>Pin sidebar</label>
              <input
                type="checkbox"
                checked={sidebarPinnedOpen}
                onChange={(e) => {
                  const v = e.target.checked;
                  setSidebarPinnedOpen(v);
                  if (v) setShowSidebar(true);
                }}
              />
              <span style={{ color: "var(--muted)", fontSize: 12 }}>holds sidebar open</span>
            </div>
            <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
              <div className="segmented" role="radiogroup" aria-label="Viewer mode">
                <button
                  type="button"
                  className={`seg-btn ${viewerKind === "pair" ? "active" : ""}`}
                  onClick={() => setViewerKind("pair")}
                  role="radio"
                  aria-checked={viewerKind === "pair"}
                  title="Side-by-side viewer"
                >
                  Pair
                </button>
                <button
                  type="button"
                  className={`seg-btn ${viewerKind === "slide" ? "active" : ""}`}
                  onClick={() => {
                    setViewerKind("slide");
                    const anchor = slideSelected;
                    if (anchor) initSlideFromAnchor(anchor);
                  }}
                  role="radio"
                  aria-checked={viewerKind === "slide"}
                  title="Sliding comparison viewer (hotkey: V)"
                >
                  Slide
                </button>
                <button
                  type="button"
                  className={`seg-btn ${viewerKind === "select" ? "active" : ""}`}
                  onClick={() => setViewerKind("select")}
                  role="radio"
                  aria-checked={viewerKind === "select"}
                  title="Table-based selection view"
                >
                  Select
                </button>
              </div>
              <button
                onClick={() => {
                  setFocusPreview((v) => !v);
                  if (!focusPreview) {
                    if (!sidebarPinnedOpen) setShowSidebar(false);
                  }
                }}
                disabled={sidebarPinnedOpen}
                title={sidebarPinnedOpen ? "Disable Pin sidebar to focus viewer" : "Hide sidebar for maximum viewer space"}
              >
                {focusPreview ? "Exit focus" : "Focus viewer"}
              </button>
            </div>
            <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <label style={{ color: "var(--muted)", fontSize: 12 }}>Auto-play</label>
                <input type="checkbox" checked={autoPlay} onChange={(e) => setAutoPlay(e.target.checked)} />
                <label style={{ color: "var(--muted)", fontSize: 12 }}>Loop</label>
                <input type="checkbox" checked={loopPlayback} onChange={(e) => setLoopPlayback(e.target.checked)} />
              </div>
            </div>

            {viewerKind === "slide" ? (
              <>
                <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", marginTop: 6 }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                    <label style={{ color: "var(--muted)", fontSize: 12 }}>Group</label>
                    <select
                      value={slideGroup?.key ?? ""}
                      onChange={(e) => setSlideGroupKey(e.target.value)}
                      style={{ maxWidth: 520 }}
                    >
                      {slideGroups.map((g) => (
                        <option key={g.key} value={g.key}>
                          {g.label} ({g.run_count})
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", marginTop: 0 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <button
                      onClick={() => {
                        const anchor = slideSelected;
                        if (anchor) initSlideFromAnchor(anchor);
                      }}
                      disabled={!slideSelected}
                    >
                      Anchor from selected
                    </button>
                    <button onClick={() => setNextOpen(true)} disabled={!slideRun}>
                      Next experiment…
                    </button>
                    <button
                      onClick={() => slideRun && openCreateFromRun(slideRun)}
                      disabled={!slideRun?.primary_video?.relpath}
                      title="Same setup, refine sweeps (opens create-experiment flow with this run's params)"
                    >
                      Branch experiment
                    </button>
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ color: "var(--muted)", fontSize: 12 }}>
                      Active axis: <span className="mono">{slideAxes[slideActiveAxis] ?? ""}</span>
                    </span>
                  </div>
                </div>

                <div style={{ marginTop: 8, color: "var(--muted)", fontSize: 12 }}>
                  Tips: <span className="mono">↑/↓</span> pick axis, <span className="mono">←/→</span> change value, or swipe. Configure
                  slide axes in <span className="mono">Axes</span>.
                </div>
              </>
            ) : null}
          </FacetSection>

          <FacetSection
            title="Axes"
            open={showAxes}
            onToggle={() => setShowAxes((v) => !v)}
            meta={
              <span style={{ color: "var(--muted)", fontSize: 12 }}>
                cols <span className="mono">{visibleAxes.length}</span> · slide <span className="mono">{slideAxes.length}</span>
              </span>
            }
          >
            <div className="axes-matrix-head">
              <div />
              <div title="Show as a table column">Col</div>
              <div title="Use as a Slide axis (params only)">Slide</div>
            </div>
            <div
              className="axis-list axes-matrix paged paged-fixed"
              style={
                {
                  ["--paged-rows" as any]: axesPager.pageSize,
                  ["--paged-row-h" as any]: "30px",
                  ["--paged-extra" as any]: "0px",
                } as React.CSSProperties
              }
            >
              {pagedAxes.pageItems.map((a) => {
                const label = a.label;
                const required = label === "run_key" || label === "status";
                const isParam = paramAxisLabels.includes(label);
                const isVirtual = Boolean(a.virtual);
                return (
                  <div className="axes-row" key={label}>
                    <code title={isVirtual ? `${label} (pending)` : label} className={isVirtual ? "axis-virtual" : ""}>
                      {label}
                    </code>
                    <input
                      type="checkbox"
                      checked={required ? true : selectedAxes.includes(label)}
                      disabled={required}
                      onChange={() => toggleAxis(label)}
                      aria-label={`Toggle column ${label}`}
                      title={required ? "Always shown" : "Toggle table column"}
                    />
                    <input
                      type="checkbox"
                      checked={isParam ? slideParamAxesSelected.includes(label) : false}
                      disabled={!isParam}
                      onChange={() => toggleSlideParamAxis(label)}
                      aria-label={`Toggle slide axis ${label}`}
                      title={isParam ? "Toggle slide axis" : "Slide uses params.* axes only"}
                    />
                  </div>
                );
              })}
              {Array.from({ length: Math.max(0, axesPager.pageSize - pagedAxes.pageItems.length) }).map((_, i) => (
                <div className="axes-row placeholder" key={`ph-ax-${i}`}>
                  <code>placeholder</code>
                  <input type="checkbox" checked={false} readOnly />
                  <input type="checkbox" checked={false} readOnly />
                </div>
              ))}
            </div>
            <Pager
              state={axesPager}
              pageCount={pagedAxes.pageCount}
              total={pagedAxes.total}
              onChange={setAxesPager}
            />
          </FacetSection>

          <div className="sidebar-group-title">Config</div>

          <FacetSection title="Cache" open={sidebarCacheOpen} onToggle={() => setSidebarCacheOpen((v) => !v)}>
            <div style={{ display: "grid", gap: 8 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "space-between", minWidth: 0 }}>
                <div style={{ color: "var(--muted)", fontSize: 12, minWidth: 0 }}>
                  Runs cache:{" "}
                  <span className="mono">
                    {runsCacheStats.expCount} exp · {runsCacheStats.runCount} runs · {fmtBytes(runsCacheStats.totalBytes)}
                  </span>
                </div>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => {
                    void (async () => {
                      try {
                        const st = await runsCacheGetStats();
                        setRunsCacheStats((prev) => ({ ...prev, ...st, lastError: "" }));
                      } catch (e) {
                        setRunsCacheStats((prev) => ({ ...prev, lastError: String(e) }));
                      }
                    })();
                  }}
                  title="Refresh cache status"
                  aria-label="Refresh cache status"
                >
                  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
                    <path
                      d="M20 12a8 8 0 1 1-2.34-5.66"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                    />
                    <path
                      d="M20 4v6h-6"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </button>
              </div>

              <div style={{ color: "var(--muted)", fontSize: 12 }}>
                TTL: <span className="mono">{Math.round(RUNS_CACHE_TTL_MS / 60000)}m</span> (stale-while-revalidate)
              </div>

              {runsCacheStats.newestFetchedAtMs ? (
                <div style={{ color: "var(--muted)", fontSize: 12 }}>
                  Last write: <span className="mono">{new Date(runsCacheStats.newestFetchedAtMs).toLocaleString()}</span>
                </div>
              ) : null}

              {runsCacheStats.lastError ? (
                <div style={{ color: "var(--warn)", fontSize: 12, fontFamily: "var(--mono)" }}>{runsCacheStats.lastError}</div>
              ) : null}

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  type="button"
                  onClick={() => {
                    setLocalStorageClearing(true);
                    try {
                      // Clear app keys only (avoid nuking unrelated site storage).
                      const keys: string[] = [];
                      for (let i = 0; i < localStorage.length; i++) {
                        const k = localStorage.key(i);
                        if (k) keys.push(k);
                      }
                      for (const k of keys) {
                        if (k.startsWith("ui.") || k === "ui.sidebar_width" || k === "ui.sidebar_collapsed" || k === "ui.sidebar_pinned_open" || k === "ui.pinned_axes") {
                          localStorage.removeItem(k);
                        }
                      }
                      setStorageSnapNonce((n) => n + 1);
                      setSlideHint("Cleared localStorage");
                      window.setTimeout(() => setSlideHint(""), 1200);
                    } catch {
                      // ignore
                    } finally {
                      setLocalStorageClearing(false);
                    }
                  }}
                  disabled={localStorageClearing}
                  aria-disabled={localStorageClearing}
                  title="Clears app localStorage keys (ui.*). IndexedDB/cookies are unchanged."
                >
                  {localStorageClearing ? "Clearing localStorage…" : "Clear localStorage"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setCookiesClearing(true);
                    try {
                      _deleteCookie(DRAWER_COOKIE);
                      setStorageSnapNonce((n) => n + 1);
                      setSlideHint("Cleared cookies");
                      window.setTimeout(() => setSlideHint(""), 1200);
                    } finally {
                      setCookiesClearing(false);
                    }
                  }}
                  disabled={cookiesClearing}
                  aria-disabled={cookiesClearing}
                  title="Clears UI cookies (e.g. drawer state). LocalStorage/IndexedDB are unchanged."
                >
                  {cookiesClearing ? "Clearing cookies…" : "Clear cookies"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    void (async () => {
                      setCacheClearing(true);
                      try {
                        await runsCacheClear();
                        const st = await runsCacheGetStats();
                        setRunsCacheStats((prev) => ({ ...prev, ...st, lastError: "" }));
                      } catch (e) {
                        setRunsCacheStats((prev) => ({ ...prev, lastError: String(e) }));
                      } finally {
                        setCacheClearing(false);
                      }
                    })();
                  }}
                  disabled={cacheClearing || runsCacheStats.expCount <= 0}
                  aria-disabled={cacheClearing || runsCacheStats.expCount <= 0}
                >
                  {cacheClearing ? "Clearing…" : "Clear runs cache"}
                </button>
              </div>

              <div style={{ display: "grid", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                  <button
                    type="button"
                    className="facet-toggle"
                    onClick={() => setStorageSnapOpen((v) => !v)}
                    aria-expanded={storageSnapOpen}
                    style={{ padding: 0, flex: "1 1 auto", width: "auto", minWidth: 0 }}
                  >
                    <span className={`facet-caret ${storageSnapOpen ? "open" : ""}`} aria-hidden="true">
                      ▸
                    </span>
                    <span style={{ color: "var(--muted)", fontSize: 12 }}>Storage snapshot</span>
                  </button>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => setStorageSnapNonce((n) => n + 1)}
                    title="Refresh storage view"
                    aria-label="Refresh storage view"
                  >
                    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
                      <path
                        d="M20 12a8 8 0 1 1-2.34-5.66"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                      />
                      <path
                        d="M20 4v6h-6"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>
                </div>

                {storageSnapOpen ? (
                  <div style={{ display: "grid", gap: 10 }}>
                  <div>
                    <div style={{ color: "var(--muted)", fontSize: 12, marginBottom: 6 }}>Cookies</div>
                    <pre style={{ margin: 0, whiteSpace: "pre-wrap", maxWidth: "100%", overflowWrap: "anywhere", wordBreak: "break-word" }} className="mono">
                      {storageSnapshot.cookies.length
                        ? storageSnapshot.cookies.map((c) => `${c.name}=${c.value}`).join("\n")
                        : "(none)"}
                    </pre>
                    {storageSnapshot.drawer ? (
                      <div style={{ marginTop: 8 }}>
                        <div style={{ color: "var(--muted)", fontSize: 12, marginBottom: 6 }}>Decoded `ui.drawer`</div>
                        <pre style={{ margin: 0, whiteSpace: "pre-wrap", maxWidth: "100%", overflowWrap: "anywhere", wordBreak: "break-word" }} className="mono">
                          {JSON.stringify(storageSnapshot.drawer, null, 2)}
                        </pre>
                      </div>
                    ) : null}
                  </div>
                  <div>
                    <div style={{ color: "var(--muted)", fontSize: 12, marginBottom: 6 }}>LocalStorage (filtered)</div>
                    <pre style={{ margin: 0, whiteSpace: "pre-wrap", maxWidth: "100%", overflowWrap: "anywhere", wordBreak: "break-word" }} className="mono">
                      {storageSnapshot.localStorage.length
                        ? storageSnapshot.localStorage.map((e) => `${e.key} = ${e.value}`).join("\n")
                        : "(no matching keys)"}
                    </pre>
                  </div>
                  </div>
                ) : null}
              </div>
            </div>
          </FacetSection>

          {error ? (
            <div style={{ marginTop: 10, color: "var(--bad)", fontFamily: "var(--mono)", fontSize: 12 }}>{error}</div>
          ) : null}
        </div>
      </div>

      <div className={`panel main-panel ${focusPreview ? "flush" : ""}`}>
        {mainMode === "wip" ? (
          <div className="preview-toolbar">
            <h2 className="title" style={{ margin: 0 }}>
              Create from WIP
            </h2>
            <div className="right">
              <button type="button" onClick={() => setMainMode("runs")} title="Back to Viewer">
                Back to Viewer
              </button>
            </div>
          </div>
        ) : null}
        {mainMode === "queue" ? (
          <div className="preview-toolbar">
            <h2 className="title" style={{ margin: 0 }}>
              Queue
            </h2>
            <div className="right">
              <button type="button" onClick={() => setMainMode("runs")} title="Back to Viewer">
                Back to Viewer
              </button>
              <button type="button" onClick={() => refreshQueue()} disabled={queueLoading} title="Refresh queue">
                Refresh
              </button>
              <button
                type="button"
                onClick={() => {
                  void (async () => {
                    await comfyClear();
                    await refreshQueue();
                  })();
                }}
                title="Clear ComfyUI pending queue"
              >
                Clear pending
              </button>
              {!showSidebar && !focusPreview ? <button onClick={() => setShowSidebar(true)}>Show sidebar</button> : null}
            </div>
          </div>
        ) : null}

        {mainMode === "wip" ? (
          <div className="viewer-pane" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
            <WipMainContent
              planned={wipPlanned}
              selectedWipRelpath={wipSelectedRelpath}
              editingId={wipEditingId}
              params={wipParams}
              onParamsChange={setWipParams}
              onAddExperiment={wipAddExperiment}
              onUpdateExperiment={wipUpdateExperiment}
              onCreateAll={wipCreateAll}
              creating={wipCreating}
              createLog={wipCreateLog}
            />
          </div>
        ) : null}
        {mainMode === "runs" ? (
          <div className="preview-toolbar">
            <h2 className="title" style={{ margin: 0 }}>
              Viewer
            </h2>
            <div className="right">
              {sidebarWipOpen ? (
                <button
                  type="button"
                  onClick={() => setMainMode("wip")}
                  title="Return to Create from WIP (video preview and parameters)"
                  style={{ marginRight: 8 }}
                >
                  Create from WIP
                </button>
              ) : null}
              <div className="segmented" role="radiogroup" aria-label="Viewer mode">
                <button
                  type="button"
                  className={`seg-btn ${viewerKind === "pair" ? "active" : ""}`}
                  onClick={() => setViewerKind("pair")}
                  role="radio"
                  aria-checked={viewerKind === "pair"}
                  title="Side-by-side viewer"
                >
                  Pair
                </button>
                <button
                  type="button"
                  className={`seg-btn ${viewerKind === "slide" ? "active" : ""}`}
                  onClick={() => {
                    setViewerKind("slide");
                    const anchor = selectedRunObjs[0];
                    if (anchor) initSlideFromAnchor(anchor);
                  }}
                  role="radio"
                  aria-checked={viewerKind === "slide"}
                  title="Sliding comparison viewer (hotkey: V)"
                >
                  Slide
                </button>
                <button
                  type="button"
                  className={`seg-btn ${viewerKind === "select" ? "active" : ""}`}
                  onClick={() => setViewerKind("select")}
                  role="radio"
                  aria-checked={viewerKind === "select"}
                  title="Table-based selection view"
                >
                  Select
                </button>
              </div>
              {!showSidebar && !focusPreview ? <button onClick={() => setShowSidebar(true)}>Show sidebar</button> : null}
            </div>
          </div>
        ) : null}

        {mainMode === "queue" ? (
          <div className="viewer-pane" style={{ padding: 12, overflow: "auto" }}>
            <QueueViewer data={queueData} loading={queueLoading} error={queueError} onRefresh={() => void refreshQueue()} />
          </div>
        ) : null}

        {mainMode === "runs" ? (
        <div className="runs-viewer-wrap" style={{ display: "flex", flex: 1, minWidth: 0, minHeight: 0 }}>
          <div
            className={`viewer-pane ${viewerKind === "slide" || viewerKind === "pair" ? "viewer-pane-stage" : ""}`}
            style={{ flex: 1, minWidth: 0, minHeight: 0 }}
          >
          {viewerKind === "slide" ? (
            <div
              className="slide-stage compact"
              ref={slideZP.stageRef}
              style={
                slideMeta?.w && slideMeta?.h ? ({ ["--media-ar" as any]: `${Math.round(slideMeta.w)} / ${Math.round(slideMeta.h)}` } as any) : undefined
              }
              onMouseEnter={(e) => {
                const r = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
                const x = e.clientX - r.left;
                // Only show HUD when hovering the left-side zone (match actual HUD width).
                const HUD_LEFT = 12;
                const pad = 8;
                const th = Math.min(r.width, HUD_LEFT + Math.max(80, hudHotZoneW) + pad);
                setHudHot(x < th);
              }}
              onMouseMove={(e) => {
                const r = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
                const x = e.clientX - r.left;
                // Only show HUD when hovering the left-side zone (match actual HUD width).
                const HUD_LEFT = 12;
                const pad = 8;
                const th = Math.min(r.width, HUD_LEFT + Math.max(80, hudHotZoneW) + pad);
                setHudHot(x < th);
              }}
              onMouseLeave={() => setHudHot(false)}
              {...slideZP.stageProps}
            >
                {slideIsLoading ? (
                  <div className="slide-empty">
                    <div className="spinner" role="status" aria-live="polite" aria-label="Loading slide data">
                      <span className="spinner-ring" aria-hidden="true" />
                      <div style={{ display: "grid", gap: 4 }}>
                        <span className="mono" style={{ color: "var(--muted)" }}>
                          Loading runs for this experiment…
                        </span>
                      </div>
                    </div>
                  </div>
                ) : !slideRuns.length ? (
                  <div className="slide-empty">
                    <div className="mono" style={{ color: "var(--muted)" }}>
                      No runs found for the current Slide group.
                    </div>
                    <div style={{ marginTop: 8, color: "var(--muted)", fontSize: 12 }}>
                      Re-enter by selecting a run (switch to <span className="mono">Select</span> view), or change the Slide group dropdown.
                    </div>
                    <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button type="button" onClick={() => setViewerKind("select")}>
                        Go to Select view
                      </button>
                      {!showSidebar && !focusPreview ? (
                        <button type="button" onClick={() => setShowSidebar(true)}>
                          Show sidebar
                        </button>
                      ) : null}
                    </div>
                  </div>
                ) : !slideRunsWithMedia.length ? (
                  <div className="slide-empty">
                    <div className="mono" style={{ color: "var(--muted)" }}>
                      This Slide group has no media yet.
                    </div>
                    <div style={{ marginTop: 8, color: "var(--muted)", fontSize: 12 }}>
                      Runs may still be in-progress, or outputs haven’t been detected. Try enabling Auto-refresh in the Runs pane.
                    </div>
                    <div style={{ marginTop: 10 }}>
                      <button type="button" onClick={resetSlideSlice}>
                        Reset slice
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    {/* Render both A and B viewers; A/B switch just changes visibility. */}
                    {slideRunA?.primary_video?.url ? (
                      <video
                        key={`A:${slideRunA.primary_video.url}`}
                        ref={(el) => {
                          slideVideoARef.current = el;
                        }}
                        className={`slide-media zoompan ${activeSlot === "A" ? "ab-active" : "ab-inactive"}`}
                        controls={activeSlot === "A"}
                        loop={loopPlayback}
                        autoPlay={activeSlot === "A" ? autoPlay : false}
                        muted={activeSlot === "A" ? autoPlay : true}
                        playsInline
                        preload="metadata"
                        src={slideRunA.primary_video.url ?? undefined}
                        style={{
                          ...slideZP.mediaStyle,
                        }}
                        onLoadedMetadata={(e) => {
                          const v = e.currentTarget;
                          const m = { w: v.videoWidth, h: v.videoHeight, duration: v.duration };
                          slideMetaARef.current = m;
                          if (activeSlot === "A") {
                            setSlideMediaError("");
                            setSlideMeta(m);
                            slideZP.setMediaSize({ w: v.videoWidth, h: v.videoHeight });
                            if (autoPlay) void v.play().catch(() => {});
                          }
                        }}
                        onError={() => {
                          if (activeSlot === "A") setSlideMediaError("Video failed to load.");
                        }}
                      />
                    ) : slideRunA?.primary_image?.url ? (
                      <img
                        key={`A:${slideRunA.primary_image.url ?? `${slideRunA.exp_id}::${slideRunA.run_id}`}`}
                        className={`slide-media zoompan ${activeSlot === "A" ? "ab-active" : "ab-inactive"}`}
                        alt={`${slideRunA.exp_id} ${slideRunA.run_id}`}
                        src={slideRunA.primary_image.url ?? undefined}
                        style={{
                          ...slideZP.mediaStyle,
                        }}
                        onLoad={(e) => {
                          const img = e.currentTarget;
                          slideMetaARef.current = { w: img.naturalWidth, h: img.naturalHeight };
                          if (activeSlot === "A") {
                            setSlideMediaError("");
                            slideZP.setMediaSize({ w: img.naturalWidth, h: img.naturalHeight });
                          }
                        }}
                        onError={() => {
                          if (activeSlot === "A") setSlideMediaError("Image failed to load.");
                        }}
                      />
                    ) : null}

                    {slideRunB?.primary_video?.url ? (
                      <video
                        key={`B:${slideRunB.primary_video.url}`}
                        ref={(el) => {
                          slideVideoBRef.current = el;
                        }}
                        className={`slide-media zoompan ${activeSlot === "B" ? "ab-active" : "ab-inactive"}`}
                        controls={activeSlot === "B"}
                        loop={loopPlayback}
                        autoPlay={activeSlot === "B" ? autoPlay : false}
                        muted={activeSlot === "B" ? autoPlay : true}
                        playsInline
                        preload="metadata"
                        src={slideRunB.primary_video.url ?? undefined}
                        style={{
                          ...slideZP.mediaStyle,
                        }}
                        onLoadedMetadata={(e) => {
                          const v = e.currentTarget;
                          const m = { w: v.videoWidth, h: v.videoHeight, duration: v.duration };
                          slideMetaBRef.current = m;
                          if (activeSlot === "B") {
                            setSlideMediaError("");
                            setSlideMeta(m);
                            slideZP.setMediaSize({ w: v.videoWidth, h: v.videoHeight });
                            if (autoPlay) void v.play().catch(() => {});
                          }
                        }}
                        onError={() => {
                          if (activeSlot === "B") setSlideMediaError("Video failed to load.");
                        }}
                      />
                    ) : slideRunB?.primary_image?.url ? (
                      <img
                        key={`B:${slideRunB.primary_image.url ?? `${slideRunB.exp_id}::${slideRunB.run_id}`}`}
                        className={`slide-media zoompan ${activeSlot === "B" ? "ab-active" : "ab-inactive"}`}
                        alt={`${slideRunB.exp_id} ${slideRunB.run_id}`}
                        src={slideRunB.primary_image.url ?? undefined}
                        style={{
                          ...slideZP.mediaStyle,
                        }}
                        onLoad={(e) => {
                          const img = e.currentTarget;
                          slideMetaBRef.current = { w: img.naturalWidth, h: img.naturalHeight };
                          if (activeSlot === "B") {
                            setSlideMediaError("");
                            slideZP.setMediaSize({ w: img.naturalWidth, h: img.naturalHeight });
                          }
                        }}
                        onError={() => {
                          if (activeSlot === "B") setSlideMediaError("Image failed to load.");
                        }}
                      />
                    ) : null}

                    {/* If there is no exact match for this slice, still show nearest but explain. */}
                    {!slideRunExact && slideRun ? (
                      <div className="slide-no-match" style={{ top: 64 }}>
                        <div className="mono" style={{ color: "var(--muted)" }}>
                          No exact match for this slice — showing nearest ({activeSlot}).
                        </div>
                        <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
                          <button type="button" onClick={resetSlideSlice}>
                            Reset slice
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </>
                )}

                <div className="slide-titlebar show">
                  <div className="slide-titlebar-inner">
                    <div className="mono slide-title-text">
                      {slideRun ? `${slideABEnabled ? `${slideAB} · ` : ""}${slideRun.exp_id} :: ${slideRun.run_id}` : ""}
                      {slideRun?.status ? `  (${slideRun.status})` : ""}
                    </div>
                    {slideHint ? <div className="slide-title-hint">{slideHint}</div> : null}
                  </div>
                </div>

                {slideDebug ? (
                  <div
                    className="slide-no-match"
                    style={{
                      top: 40,
                      left: 8,
                      right: 8,
                      padding: 8,
                      fontSize: 11,
                      fontFamily: "monospace",
                      background: "rgba(0,0,0,0.85)",
                      color: "#ccc",
                      borderRadius: 4,
                      maxHeight: 200,
                      overflow: "auto",
                    }}
                  >
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>Slide B debug (?slideDebug)</div>
                    <div>activeSlot: {activeSlot}</div>
                    <div>slideABEnabled: {String(slideABEnabled)}</div>
                    <div>selectedRuns[1]: {selectedRuns[1] ?? "(none)"}</div>
                    <div>
                      slideB: {slideB ? `${slideB.exp_id}::${slideB.run_id}` : "null"}
                      {slideB
                        ? ` · video=${slideB.primary_video?.url ? "yes" : "no"} image=${slideB.primary_image?.url ? "yes" : "no"}`
                        : ""}
                    </div>
                    <div>
                      slideRunB: {slideRunB ? `${slideRunB.exp_id}::${slideRunB.run_id}` : "null"}
                      {slideRunB
                        ? ` · video=${slideRunB.primary_video?.url ? "yes" : "no"} image=${slideRunB.primary_image?.url ? "yes" : "no"}`
                        : ""}
                    </div>
                    <div>
                      slideRun: {slideRun ? `${slideRun.exp_id}::${slideRun.run_id}` : "null"}
                      {slideRun
                        ? ` · video=${slideRun.primary_video?.url ? "yes" : "no"} image=${slideRun.primary_image?.url ? "yes" : "no"}`
                        : ""}
                    </div>
                    <div>
                      B in runs: {selectedRuns[1] ? (runs.some((r) => runKey(r) === selectedRuns[1]) ? "yes" : "NO") : "n/a"}
                    </div>
                    <div>runs.length: {runs.length}</div>
                  </div>
                ) : null}

                {slideMediaError ? (
                  <div className="slide-no-match" style={{ top: 64 }}>
                    <div className="mono" style={{ color: "var(--bad)" }}>
                      {slideMediaError}
                    </div>
                    <div style={{ marginTop: 8, color: "var(--muted)", fontSize: 12 }}>
                      {slideRun?.primary_video?.url ? (
                        <>
                          Try opening the file directly:{" "}
                          <a href={slideRun.primary_video.url} target="_blank" rel="noreferrer">
                            video
                          </a>
                        </>
                      ) : slideRun?.primary_image?.url ? (
                        <>
                          Try opening the file directly:{" "}
                          <a href={slideRun.primary_image.url} target="_blank" rel="noreferrer">
                            image
                          </a>
                        </>
                      ) : null}
                    </div>
                  </div>
                ) : null}

                {slideNavEnabled ? (
                  <div className={`slide-hud ${slideShowHud ? "show" : ""}`} ref={slideHudRef}>
                    <div className="hud-row">
                      <div className="hud-left">
                        <div
                          className="scoreboard"
                          ref={scoreboardRef}
                          style={{ ["--scorebox-w" as any]: scoreboxW ? `${scoreboxW}px` : undefined }}
                        >
                      {slideAxes.map((a, i) => (
                            <div
                              key={a}
                              className={`scorebox ${i === slideActiveAxis ? "active" : ""}`}
                              onClick={() => setSlideActiveAxis(i)}
                              role="button"
                              tabIndex={0}
                              title="Select axis"
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault();
                                  setSlideActiveAxis(i);
                                }
                              }}
                            >
                              <div className="score-label">{a}</div>
                              <div className="score-value">{fmt(slideActiveCursor[a])}</div>
                              {valuesForAxis(a).length > 1 ? (
                                <div className="score-arrows" aria-label={`Change ${a}`}>
                                  <button
                                    type="button"
                                    title={`Previous ${a}`}
                                    aria-label={`Previous ${a}`}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      slideMoveValueForAxis(a, i, -1);
                                    }}
                                  >
                                    ←
                                  </button>
                                  <button
                                    type="button"
                                    title={`Next ${a}`}
                                    aria-label={`Next ${a}`}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      slideMoveValueForAxis(a, i, 1);
                                    }}
                                  >
                                    →
                                  </button>
                                </div>
                              ) : null}
                            </div>
                          ))}
                        </div>
                        <div className="hud-meta">
                          {slideMeta?.w && slideMeta?.h ? <div className="mono hud-meta-line">{`${slideMeta.w}x${slideMeta.h}`}</div> : null}
                          {slideMeta?.duration ? (
                            <div className="mono hud-meta-line">{`dur=${slideMeta.duration.toFixed(2)}s`}</div>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : null}

                {slideNavEnabled ? (
                  <div className="nav-fab" data-zp-ui role="navigation" aria-label="Slide navigation">
                    {slideABEnabled ? (
                      <div className="segmented" role="radiogroup" aria-label="A/B view">
                        <button
                          type="button"
                          className={`seg-btn ${slideAB === "A" ? "active" : ""}`}
                          onClick={() => setSlideAB("A")}
                          role="radio"
                          aria-checked={slideAB === "A"}
                          title={`View A (${slideA?.run_id ?? ""})`}
                        >
                          A
                        </button>
                        <button
                          type="button"
                          className={`seg-btn ${slideAB === "B" ? "active" : ""}`}
                          onClick={() => setSlideAB("B")}
                          role="radio"
                          aria-checked={slideAB === "B"}
                          title={`View B (${slideB?.run_id ?? ""})`}
                        >
                          B
                        </button>
                      </div>
                    ) : null}
                    <div className="nav-status mono" aria-live="polite">
                      {slideAxes[slideActiveAxis]
                        ? `${slideAxes[slideActiveAxis]}  ${fmt(slideActiveCursor[slideAxes[slideActiveAxis]] ?? "")}`
                        : ""}
                    </div>
                    <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
                      <button onClick={() => slideZP.fitToViewport()} title="Fit to viewport">
                        Fit
                      </button>
                      <button onClick={() => slideZP.actualSize()} title="Actual size (1:1)">
                        1:1
                      </button>
                    </div>
                    <button onClick={() => slideMoveAxis(-1)} title="Axis up">
                      ↑
                    </button>
                    <div style={{ display: "flex", gap: 8 }}>
                      <button onClick={() => slideMoveValue(-1)} title="Value left">
                        ←
                      </button>
                      <button onClick={() => slideMoveValue(1)} title="Value right">
                        →
                      </button>
                    </div>
                    <button onClick={() => slideMoveAxis(1)} title="Axis down">
                      ↓
                    </button>
                  </div>
                ) : null}
              </div>
          ) : viewerKind === "select" ? (
            <div className="table-pane">
              {selectSubmode === "list" ? (
                renderRunsTableBody({ variant: "main" })
              ) : (
                <>
                  {renderRunsHeader({ variant: "main", showSelectSubmode: true })}
                  {renderRunsGallery()}
                </>
              )}
            </div>
          ) : (
            <div className="pair-stage">
              {[0, 1].map((i) => {
                const r = selectedRunObjs[i];
                const title = r ? `${r.exp_id}::${r.run_id} (${r.status})` : i === 0 ? "Select a run" : "Select another run";
                return (
                  <PairZoomPane
                    key={i}
                    run={r ?? null}
                    axes={slideAxesForPane}
                    pool={runs.filter((x) => Boolean(x.primary_video?.url || x.primary_image?.url))}
                    autoPlay={autoPlay}
                    loopPlayback={loopPlayback}
                    onOpenExpanded={openExpanded}
                    onTransformChange={(t) => {
                      pairTransformRef.current[i] = t;
                      if (pairMatchOn) {
                        setPairSyncTransforms((prev) => ({
                          ...prev,
                          [i === 0 ? "from0" : "from1"]: t,
                        }));
                      }
                    }}
                    getOtherTransform={() => pairTransformRef.current[i === 0 ? 1 : 0]}
                    externalTransform={
                      pairMatchOn ? (i === 0 ? pairSyncTransforms.from1 : pairSyncTransforms.from0) : null
                    }
                    matchOn={pairMatchOn}
                    syncOn={pairSyncVideosOn}
                    onMatchToggle={() => setPairMatchOn((v) => !v)}
                    onSyncToggle={() => setPairSyncVideosOn((v) => !v)}
                    onVideoEl={(el) => {
                      pairVideoRef.current[i] = el;
                    }}
                    getOtherVideoEl={() => pairVideoRef.current[i === 0 ? 1 : 0]}
                  />
                );
              })}
            </div>
          )}
          </div>
          <div
            style={{
              width: experimentDetailPanelOpen ? 280 : 32,
              flexShrink: 0,
              borderLeft: "1px solid var(--border)",
              background: "var(--bg-1)",
              display: "flex",
              flexDirection: "column",
              minWidth: 0,
            }}
          >
            {experimentDetailPanelOpen ? (
              <>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "6px 8px",
                    borderBottom: "1px solid var(--border)",
                    flexShrink: 0,
                  }}
                >
                  <span style={{ fontSize: 12, fontWeight: 600 }}>Experiment metadata</span>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => setExperimentDetailPanelOpen(false)}
                    title="Collapse panel"
                    aria-label="Collapse experiment metadata panel"
                  >
                    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false">
                      <path d="M14 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>
                </div>
                <div style={{ overflow: "auto", flex: 1, minHeight: 0 }}>
                  <ExperimentDetailPanel
                    experiment={selectedExpId ? experiments.find((e) => e.exp_id === selectedExpId) ?? null : null}
                    manifest={selectedExpId ? (runsByExpId[selectedExpId]?.manifest ?? null) : null}
                    relations={relations}
                    onSelectExperiment={(expId) => {
                      setSelectedExpId(expId);
                      setExpandedExpIds((prev) => (prev.includes(expId) ? prev : [...prev, expId]));
                      if (!runsByExpId[expId]) loadRunsForExp(expId);
                    }}
                  />
                </div>
              </>
            ) : (
              <button
                type="button"
                className="icon-btn"
                onClick={() => setExperimentDetailPanelOpen(true)}
                title="Show experiment metadata"
                aria-label="Show experiment metadata panel"
                style={{
                  width: "100%",
                  height: "100%",
                  minHeight: 80,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexDirection: "column",
                  gap: 4,
                }}
              >
                <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false" style={{ transform: "rotate(90deg)" }}>
                  <path d="M14 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span style={{ fontSize: 10, writingMode: "vertical-rl", textOrientation: "mixed" }}>Details</span>
              </button>
            )}
          </div>
        </div>
        ) : null}
      </div>

      {expanded ? (
        <div className="modal-overlay" onClick={() => setExpanded(null)} role="dialog" aria-modal="true">
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">{expanded.title}</div>
              <div className="modal-actions">
                <a href={expanded.url} target="_blank" rel="noreferrer">
                  <button>Open</button>
                </a>
                <button
                  onClick={() => {
                    const el = document.getElementById("expanded-media") as
                      | (HTMLVideoElement & { requestFullscreen?: () => Promise<void> })
                      | null;
                    if (el?.requestFullscreen) void el.requestFullscreen();
                  }}
                >
                  Fullscreen
                </button>
                <button onClick={() => setExpanded(null)}>Close</button>
              </div>
            </div>
            <div className="modal-body">
              {expanded.kind === "video" ? (
                <video
                  id="expanded-media"
                  className={`modal-media fit-contain`}
                  controls
                  loop={loopPlayback}
                  autoPlay={autoPlay}
                  muted={autoPlay}
                  playsInline
                  preload="metadata"
                  src={expanded.url}
                />
              ) : (
                <img id="expanded-media" className={`modal-media fit-contain`} alt={expanded.title} src={expanded.url} />
              )}
            </div>
          </div>
        </div>
      ) : null}

      {nextOpen ? (
        <div className="modal-overlay" onClick={() => setNextOpen(false)} role="dialog" aria-modal="true">
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">Next experiment (from current run)</div>
              <div className="modal-actions">
                <button onClick={() => setNextOpen(false)}>Close</button>
              </div>
            </div>
            <div className="modal-body" style={{ placeItems: "stretch" }}>
              <div style={{ display: "grid", gap: 10 }}>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>
                  Anchor:{" "}
                  <span className="mono">
                    {slideRun ? `${slideRun.exp_id} :: ${slideRun.run_id}` : "(none)"}
                  </span>
                </div>

                <div className="row" style={{ flexWrap: "wrap" }}>
                  <label style={{ width: 140 }}>Baseline first</label>
                  <input type="checkbox" checked={nextBaselineFirst} onChange={(e) => setNextBaselineFirst(e.target.checked)} />
                  <label style={{ width: 140, marginLeft: 12 }}>Queue only (--no-wait)</label>
                  <input type="checkbox" checked={nextNoWait} onChange={(e) => setNextNoWait(e.target.checked)} />
                </div>

                <div className="row" style={{ flexWrap: "wrap" }}>
                  <label style={{ width: 140 }}>Max runs</label>
                  <input
                    type="text"
                    value={String(nextMaxRuns)}
                    onChange={(e) => setNextMaxRuns(Number(e.target.value) || 0)}
                    style={{ maxWidth: 120 }}
                  />
                  <div style={{ color: nextEstRuns > nextMaxRuns ? "var(--bad)" : "var(--muted)", fontSize: 12, marginLeft: 12 }}>
                    Estimated runs: <span className="mono">{nextEstRuns}</span>
                  </div>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 12, alignItems: "start" }}>
                  <div className="axis-list" style={{ maxHeight: 320 }}>
                    {orderedParamAxes.map((a) => (
                      <div className="axis-item" key={a}>
                        <input
                          type="checkbox"
                          checked={nextAxes.includes(a)}
                          onChange={() =>
                            setNextAxes((prev) => (prev.includes(a) ? prev.filter((x) => x !== a) : [...prev, a]).slice(0, 6))
                          }
                        />
                        <code>{a}</code>
                      </div>
                    ))}
                  </div>
                  <div style={{ display: "grid", gap: 10 }}>
                    {nextAxes.length ? (
                      nextAxes.map((a) => (
                        <div key={a} style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 10, alignItems: "center" }}>
                          <div className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>
                            {a}
                          </div>
                          <input
                            type="text"
                            value={nextValues[a] ?? ""}
                            onChange={(e) => setNextValues((prev) => ({ ...prev, [a]: e.target.value }))}
                            placeholder="comma-separated values"
                          />
                        </div>
                      ))
                    ) : (
                      <div style={{ color: "var(--muted)", fontSize: 12 }}>Select at least one axis.</div>
                    )}
                  </div>
                </div>

                <div className="row" style={{ justifyContent: "flex-end" }}>
                  <button onClick={() => void submitNextExperiment()} disabled={!slideRun || !nextAxes.length || nextEstRuns > nextMaxRuns}>
                    Create & queue
                  </button>
                </div>
                {nextEstRuns > nextMaxRuns ? (
                  <div style={{ color: "var(--bad)", fontFamily: "var(--mono)", fontSize: 12 }}>
                    Selection expands to too many runs. Either reduce values or increase Max runs.
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
    </DeviceProvider>
  );
}

