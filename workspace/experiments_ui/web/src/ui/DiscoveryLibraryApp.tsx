import React, { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  fetchDiscoveryEmbedApiPrompt,
  fetchDiscoveryExemplarSets,
  fetchDiscoveryLibrary,
  fetchDiscoveryLibraryStatus,
  fetchDiscoveryProvenanceChain,
  saveDiscoveryExemplarSets,
  submitPromptToQueue,
} from "./api";
import {
  discoveryTrimMediaRelpath,
  loadDiscoveryTrimAsync,
  persistDiscoveryTrimAsync,
  TRIM_CONTEXT_DISCOVERY_PLAYER,
} from "./discoveryTrimStorage";
import type {
  DiscoveryExemplarInputProfile,
  DiscoveryExemplarLibraryEntry,
  DiscoveryExemplarSets,
  DiscoveryLibraryItem,
  DiscoveryLibraryResponse,
  DiscoveryLibraryStatusResponse,
  DiscoveryMember,
  DiscoveryProvenanceBranchPayload,
  DiscoveryProvenanceChainLink,
  DiscoveryProvenanceChainResponse,
} from "./types";
import {
  phoneTrimBounds,
  phoneTrimLoopSeekTarget,
  phoneTrimPlaybackActive,
  TRIM_HANDLE_MIN_GAP_SEC,
} from "./phoneTrimModel";
import { DeviceProvider, useDeviceContext } from "./viewport";
import {
  DiscoveryComfyQuickEditsSection,
  findNoiseSeedQuickEdit,
  type NoiseSeedQuickEdit,
} from "./DiscoveryComfyQuickEdits";
import { useComfyPromptUndoKeyboard, usePromptDraftHistory, type PromptDraftMap } from "./usePromptDraftHistory";

const SAVED_KEY = "discovery_library_saved_v1";
const DISCOVERY_KNOWN_KEY = "discovery_library_known_v1";
const DISCOVERY_FRESH_KEY = "discovery_library_fresh_v1";
const DISCOVERY_VISITED_KEY = "discovery_library_visited_v1";
const VIDEO_AUTOPLAY_KEY = "discovery_phone_video_autoplay";
const DESKTOP_LIST_WIDTH_KEY = "discovery_desktop_list_width_v1";
const DESKTOP_LIST_WIDTH_DEFAULT = 400;
const DESKTOP_LIST_MIN = 260;
const DESKTOP_PREVIEW_MIN = 280;

const DISCOVERY_GRAPH_DRAFT_PREFIX = "discovery_comfy_graph_draft__";
const DISCOVERY_COMFY_FRONT_KEY = "discovery_comfy_front_v1";
/** Minutes between auto-refresh (0 = off). New key so legacy second-based values are not reused. */
const DISCOVERY_LIBRARY_POLL_KEY = "discovery_library_poll_min_v1";
const DISCOVERY_LIBRARY_POLL_CHOICES = [0, 1, 5, 10, 15, 30, 60] as const;
type DiscoverySortField = "mtime" | "name";
type DiscoverySortDirection = "asc" | "desc";

type DiscoverySortFieldOption = {
  label: string;
  field: DiscoverySortField;
};

const DISCOVERY_SORT_FIELDS: DiscoverySortFieldOption[] = [
  { label: "Date", field: "mtime" },
  { label: "Title", field: "name" },
];
const DISCOVERY_SORT_DEFAULT_FIELD: DiscoverySortField = "mtime";
const DISCOVERY_SORT_DEFAULT_DIRECTION: DiscoverySortDirection = "desc";

function loadDiscoveryPollMin(): (typeof DISCOVERY_LIBRARY_POLL_CHOICES)[number] {
  try {
    const n = Number(localStorage.getItem(DISCOVERY_LIBRARY_POLL_KEY));
    if (DISCOVERY_LIBRARY_POLL_CHOICES.includes(n as (typeof DISCOVERY_LIBRARY_POLL_CHOICES)[number])) {
      return n as (typeof DISCOVERY_LIBRARY_POLL_CHOICES)[number];
    }
  } catch {
    /* ignore */
  }
  return 0;
}

function persistDiscoveryPollMin(min: (typeof DISCOVERY_LIBRARY_POLL_CHOICES)[number]) {
  try {
    localStorage.setItem(DISCOVERY_LIBRARY_POLL_KEY, String(min));
  } catch {
    /* ignore */
  }
}

function discoveryCompareItems(
  a: DiscoveryLibraryItem,
  b: DiscoveryLibraryItem,
  field: DiscoverySortField,
  direction: DiscoverySortDirection
): number {
  let cmp = 0;
  if (field === "mtime") {
    cmp = a.mtime - b.mtime;
  } else {
    cmp = a.name.localeCompare(b.name, undefined, { sensitivity: "base", numeric: true });
  }
  if (cmp !== 0) return direction === "asc" ? cmp : -cmp;
  return a.relpath.localeCompare(b.relpath, undefined, { sensitivity: "base", numeric: true });
}

function discoveryDraftStorageKey(itemKey: string): string {
  const safe = itemKey.replace(/[^a-zA-Z0-9:._-]+/g, "_").slice(0, 200);
  return `${DISCOVERY_GRAPH_DRAFT_PREFIX}${safe}`;
}

function _discoverySessionGet(key: string, fallback: string): string {
  try {
    return sessionStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function _discoverySessionGetBool01(key: string): boolean {
  try {
    return sessionStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

type DiscoveryDesktopPanelTab = "viewer" | "details" | "parameters" | "assets" | "workflows";

const DISCOVERY_DESKTOP_PANEL_TABS: {
  id: DiscoveryDesktopPanelTab;
  label: string;
  mock?: boolean;
}[] = [
  { id: "viewer", label: "Viewer" },
  { id: "details", label: "Details" },
  { id: "parameters", label: "Parameters" },
  { id: "assets", label: "Assets", mock: true },
  { id: "workflows", label: "Workflows" },
];

function discoveryDesktopPanelLabelId(tab: DiscoveryDesktopPanelTab): string {
  switch (tab) {
    case "viewer":
      return "discovery-meta-tab-viewer";
    case "details":
      return "discovery-meta-tab-details";
    case "parameters":
      return "discovery-meta-tab-parameters";
    case "assets":
      return "discovery-meta-tab-assets";
    case "workflows":
      return "discovery-meta-tab-workflows";
    default:
      return "discovery-meta-tab-viewer";
  }
}

type DiscoveryRefreshMenuProps = {
  loading: boolean;
  reloading: boolean;
  rebuildRunning: boolean;
  rebuildProgressPct?: number | null;
  rebuildHeartbeatAgeMs?: number | null;
  rebuildLastError?: string | null;
  pollMin: (typeof DISCOVERY_LIBRARY_POLL_CHOICES)[number];
  onReload: () => void;
  onUpdate: () => void;
  onPollMinChange: (next: (typeof DISCOVERY_LIBRARY_POLL_CHOICES)[number]) => void;
  className?: string;
  triggerMode?: "click" | "hover";
};

function DiscoveryRefreshMenu({
  loading,
  reloading,
  rebuildRunning,
  rebuildProgressPct,
  rebuildHeartbeatAgeMs,
  rebuildLastError,
  pollMin,
  onReload,
  onUpdate,
  onPollMinChange,
  className,
  triggerMode = "click",
}: DiscoveryRefreshMenuProps) {
  const [open, setOpen] = useState(false);
  const isHover = triggerMode === "hover";
  const refreshTimeLabel = pollMin > 0 ? `${pollMin}m` : null;
  const showRefreshingIcon = reloading || rebuildRunning;
  const maybeStuck = rebuildRunning && typeof rebuildHeartbeatAgeMs === "number" && rebuildHeartbeatAgeMs > 30_000;

  return (
    <div
      className={className}
      style={{ position: "relative" }}
      onMouseEnter={isHover ? () => setOpen(true) : undefined}
      onMouseLeave={
        isHover
          ? () => {
              setOpen(false);
            }
          : undefined
      }
    >
      <button
        type="button"
        className="discovery-phone-filters-toggle"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={isHover ? undefined : () => setOpen((v) => !v)}
        style={{ position: "relative", overflow: "hidden" }}
      >
        <span>{rebuildRunning ? "Rebuilding…" : reloading ? "Refreshing…" : "Refresh"}</span>
        {showRefreshingIcon ? (
          <span
            aria-hidden="true"
            style={{
              color: "var(--muted)",
              fontSize: "0.86em",
              fontWeight: 400,
              marginLeft: 6,
              display: "inline-flex",
              alignItems: "center",
            }}
            title={maybeStuck ? "Rebuild may be stalled (no progress heartbeat recently)" : "Rebuilding"}
          >
            <svg
              viewBox="0 0 16 16"
              width="12"
              height="12"
              focusable="false"
              aria-hidden="true"
              className="discovery-refresh-rotating-icon"
            >
              <path
                d="M13.2 5.8A5.2 5.2 0 0 0 4.3 4.1"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M4.3 4.1H6.9M4.3 4.1V6.7"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M2.8 10.2A5.2 5.2 0 0 0 11.7 11.9"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M11.7 11.9H9.1M11.7 11.9V9.3"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
        ) : refreshTimeLabel ? (
          <span
            aria-hidden="true"
            style={{ color: "var(--muted)", fontSize: "0.86em", fontWeight: 400, marginLeft: 6 }}
          >
            ({refreshTimeLabel})
          </span>
        ) : null}
        {rebuildRunning && typeof rebuildProgressPct === "number" ? (
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              bottom: 0,
              height: 2,
              background: "rgba(46, 204, 113, 0.22)",
            }}
          >
            <span
              style={{
                display: "block",
                height: "100%",
                width: `${Math.max(0, Math.min(100, rebuildProgressPct))}%`,
                background: "rgb(46, 204, 113)",
                transition: "width 220ms ease-out",
              }}
            />
          </span>
        ) : null}
      </button>
      {open ? (
        <div
          role="menu"
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 6px)",
            zIndex: 20,
            minWidth: 220,
            padding: 10,
            border: "1px solid var(--border)",
            borderRadius: 8,
            background: "var(--bg)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.25)",
            display: "grid",
            gap: 8,
          }}
        >
          <button
            type="button"
            title="Re-query the saved index from the server cache (no disk rescan)"
            onClick={() => {
              onReload();
              if (!isHover) setOpen(false);
            }}
            disabled={loading || reloading || rebuildRunning}
          >
            {reloading ? "Refreshing…" : "Refresh"}
          </button>
          <button
            type="button"
            title="Rescan output folders and rebuild the list index (can take a while)"
            onClick={() => {
              onUpdate();
              if (!isHover) setOpen(false);
            }}
            disabled={loading || rebuildRunning}
          >
            {rebuildRunning
              ? `Rebuilding${typeof rebuildProgressPct === "number" ? `… ${rebuildProgressPct}%` : "…"}`
              : "Rebuild"}
          </button>
          {rebuildLastError ? (
            <div style={{ color: "var(--bad)", fontSize: 12 }} role="status">
              Last rebuild error: {rebuildLastError}
            </div>
          ) : null}
          <label style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 120 }}>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>Auto-refresh</span>
            <select
              value={pollMin}
              onChange={(e) => {
                const v = Number(e.target.value) as (typeof DISCOVERY_LIBRARY_POLL_CHOICES)[number];
                const next = DISCOVERY_LIBRARY_POLL_CHOICES.includes(v) ? v : 0;
                onPollMinChange(next);
              }}
            >
              <option value={0}>Off</option>
              <option value={1}>1 min</option>
              <option value={5}>5 min</option>
              <option value={10}>10 min</option>
              <option value={15}>15 min</option>
              <option value={30}>30 min</option>
              <option value={60}>60 min</option>
            </select>
          </label>
        </div>
      ) : null}
    </div>
  );
}

function loadVideoAutoplay(): boolean {
  try {
    return localStorage.getItem(VIDEO_AUTOPLAY_KEY) === "1";
  } catch {
    return false;
  }
}

function persistVideoAutoplay(on: boolean) {
  try {
    localStorage.setItem(VIDEO_AUTOPLAY_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}

/** Phone: horizontal swipe distance (viewer: left = next, right = prev). */
const PHONE_SWIPE_MIN_PX = 56;
/** Phone: long-press in viewer to open details sheet (ms). */
const PHONE_LONG_PRESS_MS = 520;
/** Phone: cancel long-press if pointer moves more than this many px. */
const PHONE_LONG_PRESS_MOVE_CANCEL_PX = 14;
/** Phone: native video controls visible after open / tap, then auto-hide (ms). */
const PHONE_VIEWER_CONTROLS_MS = 1500;

function discoveryDocumentFullscreenElement(): Element | null {
  const d = document as Document & {
    webkitFullscreenElement?: Element | null;
    mozFullScreenElement?: Element | null;
  };
  return document.fullscreenElement ?? d.webkitFullscreenElement ?? d.mozFullScreenElement ?? null;
}

/** iOS / iPadOS WebKit: element Fullscreen API is absent or ineffective; use visualViewport sizing instead. */
function discoveryPhoneLikelyIOS(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  if (/iPhone|iPod/i.test(ua)) return true;
  if (/iPad/i.test(ua)) return true;
  if (navigator.maxTouchPoints > 1 && /Macintosh/i.test(ua)) return true;
  return false;
}

/**
 * Scroll a list only if the row sits outside a “comfort” band: at least one row height
 * and DISCOVERY_LIST_SCROLL_MARGIN_FRAC of the list viewport from top/bottom (so small
 * lists do not jump every time; large lists still keep context without always centering).
 */
const DISCOVERY_LIST_SCROLL_MARGIN_FRAC = 0.14;

function scrollDiscoveryListRowIntoComfortZone(
  scrollRoot: HTMLElement | null,
  rowEl: HTMLElement | null,
  behavior: ScrollBehavior = "smooth"
): void {
  if (!scrollRoot || !rowEl) return;
  const viewH = scrollRoot.clientHeight;
  if (viewH < 4) return;

  const rootRect = scrollRoot.getBoundingClientRect();
  const elRect = rowEl.getBoundingClientRect();
  const itemH = Math.max(1, Math.round(elRect.height));
  const margin = Math.max(itemH, viewH * DISCOVERY_LIST_SCROLL_MARGIN_FRAC);

  const topLimit = rootRect.top + margin;
  const botLimit = rootRect.bottom - margin;

  const maxScroll = Math.max(0, scrollRoot.scrollHeight - viewH);

  if (elRect.height > viewH - 2 * margin) {
    const delta = elRect.top - topLimit;
    if (Math.abs(delta) < 0.5) return;
    const nextTop = Math.min(maxScroll, Math.max(0, scrollRoot.scrollTop + delta));
    scrollRoot.scrollTo({ top: nextTop, behavior });
    return;
  }

  let delta = 0;
  /* Increasing scrollTop moves content up (smaller elRect.top). */
  if (elRect.top < topLimit) delta += elRect.top - topLimit;
  if (elRect.bottom > botLimit) delta += elRect.bottom - botLimit;
  if (Math.abs(delta) < 0.5) return;

  const nextTop = Math.min(maxScroll, Math.max(0, scrollRoot.scrollTop + delta));
  scrollRoot.scrollTo({ top: nextTop, behavior });
}

function loadDesktopListWidth(): number {
  try {
    const raw = localStorage.getItem(DESKTOP_LIST_WIDTH_KEY);
    if (!raw) return DESKTOP_LIST_WIDTH_DEFAULT;
    const n = Number(raw);
    if (!Number.isFinite(n)) return DESKTOP_LIST_WIDTH_DEFAULT;
    return Math.max(DESKTOP_LIST_MIN, Math.min(2000, Math.round(n)));
  } catch {
    return DESKTOP_LIST_WIDTH_DEFAULT;
  }
}

function persistDesktopListWidth(px: number) {
  try {
    localStorage.setItem(DESKTOP_LIST_WIDTH_KEY, String(px));
  } catch {
    /* ignore */
  }
}

function loadSaved(): Set<string> {
  try {
    const raw = localStorage.getItem(SAVED_KEY);
    if (!raw) return new Set();
    const a = JSON.parse(raw) as unknown;
    if (!Array.isArray(a)) return new Set();
    return new Set(a.filter((x): x is string => typeof x === "string"));
  } catch {
    return new Set();
  }
}

function loadKeySet(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return new Set();
    const a = JSON.parse(raw) as unknown;
    if (!Array.isArray(a)) return new Set();
    return new Set(a.filter((x): x is string => typeof x === "string"));
  } catch {
    return new Set();
  }
}

function persistKeySet(key: string, s: Set<string>) {
  localStorage.setItem(key, JSON.stringify(Array.from(s)));
}

function scheduleIdle(fn: () => void): () => void {
  const w = window as Window & {
    requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
    cancelIdleCallback?: (id: number) => void;
  };
  if (typeof w.requestIdleCallback === "function") {
    const id = w.requestIdleCallback(fn, { timeout: 800 });
    return () => {
      if (typeof w.cancelIdleCallback === "function") w.cancelIdleCallback(id);
    };
  }
  const t = window.setTimeout(fn, 0);
  return () => window.clearTimeout(t);
}

function persistSaved(s: Set<string>) {
  localStorage.setItem(SAVED_KEY, JSON.stringify(Array.from(s)));
}

function fmtTime(ms: number): string {
  if (!ms) return "—";
  try {
    return new Date(ms * 1000).toLocaleString();
  } catch {
    return "—";
  }
}

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Media timeline (seconds). */
function fmtVideoSec(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const s = Math.floor(sec % 60);
  const m = Math.floor((sec / 60) % 60);
  const h = Math.floor(sec / 3600);
  const pad = (n: number) => (n < 10 ? `0${n}` : String(n));
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

type TrimDragKind = "in" | "out" | "play";

/** Behavior at the trim out point. Add modes later (e.g. bounce); cycle in `nextTrimPlaybackOutMode`. */
export type TrimPlaybackOutMode = "repeat" | "stop_at_end";

function nextTrimPlaybackOutMode(m: TrimPlaybackOutMode): TrimPlaybackOutMode {
  if (m === "repeat") return "stop_at_end";
  return "repeat";
}

function IconTrimRepeat() {
  return (
    <svg className="discovery-trim-out-toggle__svg" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="m17 2 4 4-4 4"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M3 11v-1a4 4 0 0 1 4-4h14"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="m7 22-4-4 4-4"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M21 13v1a4 4 0 0 1-4 4H3"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Play once through the trim (stop at out): solid forward arrow — not a skip-to-end bar glyph. */
function IconTrimStopAtEnd() {
  return (
    <svg className="discovery-trim-out-toggle__svg" viewBox="0 0 24 24" aria-hidden>
      <path
        d="M5 9h9V5l7 7-7 7v-4H5V9z"
        fill="currentColor"
      />
    </svg>
  );
}

function IconTransportPlay() {
  return (
    <svg className="discovery-trim-transport__svg" viewBox="0 0 24 24" aria-hidden>
      <polygon points="8,5 20,12 8,19" fill="currentColor" />
    </svg>
  );
}

function IconTransportPause() {
  return (
    <svg className="discovery-trim-transport__svg" viewBox="0 0 24 24" aria-hidden>
      <rect x="7" y="5" width="3.5" height="14" rx="0.5" fill="currentColor" />
      <rect x="13.5" y="5" width="3.5" height="14" rx="0.5" fill="currentColor" />
    </svg>
  );
}

/** Bar + play: jump to start of trimmed region (in point). */
function IconTransportToTrimStart() {
  return (
    <svg className="discovery-trim-transport__svg" viewBox="0 0 24 24" aria-hidden>
      <rect x="4" y="6" width="2.5" height="12" rx="0.5" fill="currentColor" />
      <path d="M10 12l8-5.5v11L10 12z" fill="currentColor" />
    </svg>
  );
}

/** Play + bar: jump to end of trimmed region (out point). */
function IconTransportToTrimEnd() {
  return (
    <svg className="discovery-trim-transport__svg" viewBox="0 0 24 24" aria-hidden>
      <path d="M14 12L6 6.5v11L14 12z" fill="currentColor" />
      <rect x="17.5" y="6" width="2.5" height="12" rx="0.5" fill="currentColor" />
    </svg>
  );
}

function IconTransportClearInOut() {
  return (
    <svg className="discovery-trim-transport__svg" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M5 5l14 14M19 5L5 19" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

function DiscoveryTrimTransport({
  videoRef,
  duration,
  markIn,
  markOut,
  mediaSyncKey,
  onSyncTime,
  pausedExternal,
  onTogglePlayExternal,
  size = "default",
}: {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  duration: number;
  markIn: number | null;
  markOut: number | null;
  mediaSyncKey: string | number;
  onSyncTime: (t: number) => void;
  pausedExternal?: boolean;
  onTogglePlayExternal?: () => void;
  size?: "default" | "large";
}) {
  const [, forceMediaUi] = useReducer((x: number) => x + 1, 0);

  useEffect(() => {
    if (pausedExternal !== undefined) return;
    const v = videoRef.current;
    if (!v) return;
    const fn = () => forceMediaUi();
    v.addEventListener("play", fn);
    v.addEventListener("pause", fn);
    v.addEventListener("ended", fn);
    return () => {
      v.removeEventListener("play", fn);
      v.removeEventListener("pause", fn);
      v.removeEventListener("ended", fn);
    };
  }, [pausedExternal, mediaSyncKey, videoRef]);

  const v = videoRef.current;
  const paused = pausedExternal !== undefined ? pausedExternal : (v?.paused ?? true);

  const onTogglePlay = useCallback(() => {
    if (onTogglePlayExternal) {
      onTogglePlayExternal();
      return;
    }
    const el = videoRef.current;
    if (!el) return;
    if (el.paused) void el.play().catch(() => {});
    else el.pause();
  }, [onTogglePlayExternal, videoRef]);

  const onToTrimStart = useCallback(() => {
    const el = videoRef.current;
    if (!el) return;
    const b = phoneTrimBounds(markIn, markOut, duration);
    const t = b ? b.in : 0;
    el.currentTime = t;
    onSyncTime(t);
  }, [videoRef, duration, markIn, markOut, onSyncTime]);

  const onToTrimEnd = useCallback(() => {
    const el = videoRef.current;
    if (!el) return;
    const b = phoneTrimBounds(markIn, markOut, duration);
    if (!b) return;
    const t = Math.max(0, Math.min(duration - 1e-6, b.out));
    el.currentTime = t;
    onSyncTime(t);
  }, [videoRef, duration, markIn, markOut, onSyncTime]);

  return (
    <div
      className={
        "discovery-trim-transport" +
        (size === "large" ? " discovery-trim-transport--large" : "")
      }
      role="group"
      aria-label="Trim preview playback"
    >
      <button
        type="button"
        className="discovery-trim-transport__btn"
        aria-label="Go to trim in point"
        title="Go to trim start"
        onClick={onToTrimStart}
        disabled={!Number.isFinite(duration) || duration <= 0}
      >
        <IconTransportToTrimStart />
      </button>
      <button
        type="button"
        className="discovery-trim-transport__btn"
        aria-label={paused ? "Play" : "Pause"}
        title={paused ? "Play" : "Pause"}
        onClick={onTogglePlay}
      >
        {paused ? <IconTransportPlay /> : <IconTransportPause />}
      </button>
      <button
        type="button"
        className="discovery-trim-transport__btn"
        aria-label="Go to trim out point"
        title="Go to trim end"
        onClick={onToTrimEnd}
        disabled={!Number.isFinite(duration) || duration <= 0}
      >
        <IconTransportToTrimEnd />
      </button>
    </div>
  );
}

function TrimClearInOutButton({ onClick, disabled }: { onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      className="discovery-trim-transport__btn"
      aria-label="Clear trim in and out"
      title={disabled ? "Trim range is full — nothing to reset" : "Clear in/out"}
      onClick={onClick}
      disabled={disabled}
    >
      <IconTransportClearInOut />
    </button>
  );
}

function TrimPlaybackOutIconToggle({
  mode,
  onModeChange,
}: {
  mode: TrimPlaybackOutMode;
  onModeChange: (next: TrimPlaybackOutMode) => void;
}) {
  const repeat = mode === "repeat";
  return (
    <button
      type="button"
      className={
        "discovery-trim-out-toggle" +
        (repeat ? " discovery-trim-out-toggle--repeat" : " discovery-trim-out-toggle--stop")
      }
      role="switch"
      aria-checked={repeat}
      aria-label={
        repeat
          ? "Trim playback: repeat. Switch to stop at out point."
          : "Trim playback: stop at out. Switch to repeat."
      }
      title={repeat ? "Repeat" : "Stop at out"}
      onClick={() => onModeChange(nextTrimPlaybackOutMode(mode))}
    >
      <span className="discovery-trim-out-toggle__icon">{repeat ? <IconTrimRepeat /> : <IconTrimStopAtEnd />}</span>
    </button>
  );
}

type PhoneTrimTimelineProps = {
  duration: number;
  currentTime: number;
  markIn: number | null;
  markOut: number | null;
  onSeek: (t: number) => void;
  onMarkInChange: (t: number) => void;
  onMarkOutChange: (t: number) => void;
  disabled: boolean;
};

/** iOS Photos–style trim strip: yellow excluded sides, selected middle, in/out handles + playhead. */
function PhoneTrimTimeline({
  duration,
  currentTime,
  markIn,
  markOut,
  onSeek,
  onMarkInChange,
  onMarkOutChange,
  disabled,
}: PhoneTrimTimelineProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const marksRef = useRef({ markIn, markOut, duration });
  marksRef.current = { markIn, markOut, duration };
  const [drag, setDrag] = useState<TrimDragKind | null>(null);

  const bounds = phoneTrimBounds(markIn, markOut, duration);
  const safeIn = bounds?.in ?? 0;
  const safeOut = bounds?.out ?? 0;

  const inPct = duration > 0 ? (safeIn / duration) * 100 : 0;
  const outPct = duration > 0 ? (safeOut / duration) * 100 : 0;
  const playPct = duration > 0 ? (Math.min(Math.max(0, currentTime), duration) / duration) * 100 : 0;

  const timeFromClientX = useCallback((clientX: number) => {
    const el = trackRef.current;
    if (!el || duration <= 0) return 0;
    const r = el.getBoundingClientRect();
    const w = Math.max(1, r.width);
    const x = Math.min(Math.max(clientX - r.left, 0), w);
    return (x / w) * duration;
  }, [duration]);

  useEffect(() => {
    if (!drag) return;
    const onMove = (e: PointerEvent) => {
      if ((e.buttons & 1) === 0) {
        end();
        return;
      }
      const t = timeFromClientX(e.clientX);
      const { duration: d, markIn: mi, markOut: mo } = marksRef.current;
      if (!d || d <= 0) return;
      const outV = Math.min(d, mo ?? d);
      const inV = Math.max(0, mi ?? 0);
      if (drag === "play") {
        onSeek(Math.max(0, Math.min(t, d)));
        return;
      }
      if (drag === "in") {
        onMarkInChange(Math.max(0, Math.min(t, outV - TRIM_HANDLE_MIN_GAP_SEC)));
        return;
      }
      onMarkOutChange(Math.min(d, Math.max(t, inV + TRIM_HANDLE_MIN_GAP_SEC)));
    };
    const end = () => setDrag(null);
    const onBlur = () => end();
    const onVis = () => {
      if (document.visibilityState === "hidden") end();
    };
    const onEnter = (ev: PointerEvent) => {
      if ((ev.buttons & 1) === 0) end();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", end, true);
    window.addEventListener("pointercancel", end);
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onVis);
    const track = trackRef.current;
    track?.addEventListener("pointerenter", onEnter);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", end, true);
      window.removeEventListener("pointercancel", end);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onVis);
      track?.removeEventListener("pointerenter", onEnter);
    };
  }, [drag, onSeek, onMarkInChange, onMarkOutChange, timeFromClientX]);

  const startHandleDrag = (kind: TrimDragKind) => (e: React.PointerEvent) => {
    if (disabled) return;
    e.stopPropagation();
    e.preventDefault();
    const el = e.currentTarget as HTMLElement;
    if (typeof el.setPointerCapture === "function") {
      try {
        el.setPointerCapture(e.pointerId);
      } catch {
        /* invalid state */
      }
    }
    setDrag(kind);
  };

  const onTrackPointerDown = (e: React.PointerEvent) => {
    if (disabled) return;
    const t = e.target as HTMLElement;
    if (t.closest(".discovery-phone-trim-handle, .discovery-phone-trim-playhead")) return;
    onSeek(timeFromClientX(e.clientX));
  };

  return (
    <div
      ref={trackRef}
      className={"discovery-phone-trim-track" + (disabled ? " discovery-phone-trim-track--disabled" : "")}
      onPointerDown={onTrackPointerDown}
      role="presentation"
    >
      <div className="discovery-phone-trim-excluded discovery-phone-trim-excluded--left" style={{ width: `${inPct}%` }} />
      <div className="discovery-phone-trim-selected" style={{ left: `${inPct}%`, width: `${outPct - inPct}%` }} />
      <div
        className="discovery-phone-trim-excluded discovery-phone-trim-excluded--right"
        style={{ left: `${outPct}%`, width: `${Math.max(0, 100 - outPct)}%` }}
      />
      <div
        className="discovery-phone-trim-handle discovery-phone-trim-handle--in"
        style={{ left: `${inPct}%` }}
        onPointerDown={startHandleDrag("in")}
        role="slider"
        aria-label="Trim start"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={safeIn}
        aria-disabled={disabled}
      >
        <span className="discovery-phone-trim-handle-grip" />
      </div>
      <div
        className="discovery-phone-trim-handle discovery-phone-trim-handle--out"
        style={{ left: `${outPct}%` }}
        onPointerDown={startHandleDrag("out")}
        role="slider"
        aria-label="Trim end"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={safeOut}
        aria-disabled={disabled}
      >
        <span className="discovery-phone-trim-handle-grip" />
      </div>
      <div className="discovery-phone-trim-playhead" style={{ left: `${playPct}%` }} onPointerDown={startHandleDrag("play")}>
        <span className="discovery-phone-trim-playhead-line" />
        <span className="discovery-phone-trim-playhead-knob" />
      </div>
    </div>
  );
}

function isVideo(p: string): boolean {
  const x = p.toLowerCase();
  return x.endsWith(".mp4") || x.endsWith(".webm");
}

function isRasterImage(name: string): boolean {
  return /\.(png|jpe?g|webp)$/i.test(name);
}

/** Match experiments_ui_server: quote full relpath (slashes → %2F). */
function fileUrlFromRel(relpath: string): string {
  return "/files/" + encodeURIComponent(relpath.replace(/\\/g, "/"));
}

function basenameRelPosix(rel: string): string {
  const s = rel.replace(/\\/g, "/").trim();
  const i = s.lastIndexOf("/");
  return i >= 0 ? s.slice(i + 1) : s;
}

/**
 * Workspace-relative path for “open this asset in Comfy like a workflow” — prefer PNG thumb/metadata when present.
 */
function discoveryComfyAssetRelpath(it: DiscoveryLibraryItem): string {
  const tr = it.thumb_relpath;
  if (tr && isRasterImage(tr)) return tr;
  if (isRasterImage(it.relpath)) return it.relpath;
  if (it.video_relpath) return it.video_relpath;
  return it.relpath;
}

/** POSIX path under the workspace output tree (PNG-with-metadata thumb when available, same rule as /files/). */
function discoveryWorkflowFilePathForClipboard(it: DiscoveryLibraryItem): string {
  return discoveryComfyAssetRelpath(it).replace(/\\/g, "/");
}

type DiscoveryComfyEmbedUi = { kind: "loaded"; pngRelpath: string } | { kind: "note"; text: string };

async function copyTextToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

function discoveryItemKey(it: DiscoveryLibraryItem): string {
  return it.group_id || it.relpath;
}

function inferExemplarInputProfileFromItem(it: DiscoveryLibraryItem): DiscoveryExemplarInputProfile | undefined {
  const raw = it.class_types_preview ?? [];
  if (!raw.length) return undefined;
  let uses_image_start = false;
  let uses_video_start = false;
  for (const cls of raw) {
    const c = String(cls).toLowerCase();
    if (c.includes("loadimage") || c.includes("load_image") || c.includes("imageload")) uses_image_start = true;
    if (
      c.includes("vhs_loadvideo") ||
      c.includes("loadvideo") ||
      c.includes("load_video") ||
      c.includes("loadvideoffmpeg") ||
      c.includes("videoloader") ||
      c.includes("loadvideopackage")
    ) {
      uses_video_start = true;
    }
  }
  if (!uses_image_start && !uses_video_start) return undefined;
  return { uses_image_start, uses_video_start };
}

function discoveryAssetMediaAvailability(it: DiscoveryLibraryItem): { hasImage: boolean; hasVideo: boolean } {
  const hasVideo =
    Boolean(discoveryPlayUrl(it)) ||
    isVideo(it.relpath) ||
    Boolean(it.video_relpath && isVideo(it.video_relpath)) ||
    Boolean(it.members?.some((m) => isVideo(m.relpath)));
  const hasImage =
    Boolean(discoveryThumbUrl(it)) ||
    isRasterImage(it.name) ||
    isRasterImage(it.relpath) ||
    Boolean(it.thumb_relpath && isRasterImage(it.thumb_relpath)) ||
    Boolean(it.members?.some((m) => isRasterImage(m.name)));
  return { hasImage, hasVideo };
}

function exemplarInputProfileForKey(
  key: string,
  exemplarSets: DiscoveryExemplarSets,
  itemByKey: Map<string, DiscoveryLibraryItem>,
): DiscoveryExemplarInputProfile | undefined {
  const ent = exemplarSets.library.find((e) => e.key === key);
  const fromLib = ent?.input_profile;
  if (fromLib && (fromLib.uses_image_start || fromLib.uses_video_start)) return fromLib;
  const row = itemByKey.get(key);
  if (row) return inferExemplarInputProfileFromItem(row);
  return undefined;
}

function exemplarCompatibleWithContext(
  profile: DiscoveryExemplarInputProfile | undefined,
  avail: { hasImage: boolean; hasVideo: boolean },
): boolean {
  if (!profile || (!profile.uses_image_start && !profile.uses_video_start)) return true;
  return (!profile.uses_image_start || avail.hasImage) && (!profile.uses_video_start || avail.hasVideo);
}

function discoveryAppendExemplarLibraryKey(
  doc: DiscoveryExemplarSets,
  key: string,
  sourceItem?: DiscoveryLibraryItem | null,
): DiscoveryExemplarSets {
  if (doc.library.some((e) => e.key === key)) return doc;
  const entry: DiscoveryExemplarLibraryEntry = { key, added_at: new Date().toISOString() };
  const prof = sourceItem ? inferExemplarInputProfileFromItem(sourceItem) : undefined;
  if (prof) entry.input_profile = prof;
  const nm = sourceItem?.name?.trim();
  if (nm) entry.source_name = nm;
  return { ...doc, library: [...doc.library, entry] };
}

/** Persisted menu label: custom display_name, else live row name, else key. */
function exemplarCatalogDisplayLabel(
  ent: DiscoveryExemplarLibraryEntry | undefined,
  row: DiscoveryLibraryItem | undefined,
): string {
  const custom = ent?.display_name?.trim();
  if (custom) return custom;
  const live = row?.name?.trim();
  if (live) return live;
  return (ent?.key ?? "").trim() || "—";
}

/** Original exemplar name for UI / JSON: frozen source_name, else live row name, else key. */
function exemplarCatalogSourceLabel(
  ent: DiscoveryExemplarLibraryEntry | undefined,
  row: DiscoveryLibraryItem | undefined,
): string {
  const src = ent?.source_name?.trim();
  if (src) return src;
  const live = row?.name?.trim();
  if (live) return live;
  return (ent?.key ?? "").trim() || "—";
}

function discoverySetExemplarDisplayName(doc: DiscoveryExemplarSets, key: string, displayName: string): DiscoveryExemplarSets {
  const trimmed = displayName.trim();
  return {
    ...doc,
    library: doc.library.map((e) => {
      if (e.key !== key) return e;
      if (!trimmed) {
        const next: DiscoveryExemplarLibraryEntry = { ...e };
        delete next.display_name;
        return next;
      }
      return { ...e, display_name: trimmed };
    }),
  };
}

/** Remove key from exemplar library and working set (same semantics as per-row Delete in Workflows). */
function discoveryRemoveExemplarLibraryKey(doc: DiscoveryExemplarSets, key: string): DiscoveryExemplarSets {
  return {
    ...doc,
    library: doc.library.filter((e) => e.key !== key),
    working_set: doc.working_set.filter((e) => e.key !== key),
  };
}

function discoveryPlayUrl(it: DiscoveryLibraryItem): string | null {
  if (it.video_url) return it.video_url;
  if (isVideo(it.relpath)) return it.url;
  return null;
}

function discoveryThumbUrl(it: DiscoveryLibraryItem): string | null {
  if (it.thumb_url) return it.thumb_url;
  if (isRasterImage(it.name) && it.url) return it.url;
  return null;
}

/** Preview URL for a grouped member file when it is a raster image; videos use a placeholder tile. */
function discoveryMemberThumbSrc(m: DiscoveryMember): string | null {
  if (isRasterImage(m.name)) return fileUrlFromRel(m.relpath);
  return null;
}

/** Whether this member path is the merged item’s primary video / thumb / canonical relpath. */
function discoveryMemberIsPrimaryOutput(it: DiscoveryLibraryItem, m: DiscoveryMember): boolean {
  const norm = m.relpath.replace(/\\/g, "/");
  if (norm === it.relpath.replace(/\\/g, "/")) return true;
  if (it.video_relpath && norm === it.video_relpath.replace(/\\/g, "/")) return true;
  if (it.thumb_relpath && norm === it.thumb_relpath.replace(/\\/g, "/")) return true;
  return false;
}

function discoveryThumbSrcForRelPath(relpath: string | null | undefined): string | null {
  if (!relpath || !relpath.trim()) return null;
  const base = basenameRelPosix(relpath);
  if (isRasterImage(base)) return fileUrlFromRel(relpath.trim());
  return null;
}

function libraryBadgeForProvenanceStep(it: DiscoveryLibraryItem, stepLib: string | null | undefined): string {
  if (stepLib === "og" || stepLib === "wip") return stepLib;
  return it.library;
}

function stepOutputRelPathForLink(
  it: DiscoveryLibraryItem,
  link: DiscoveryProvenanceChainLink,
  idx: number,
  links: DiscoveryProvenanceChainLink[]
): string | null {
  if (link.step_output_relpath && link.step_output_relpath.trim()) return link.step_output_relpath.trim();
  if (idx === 0) return it.relpath;
  const prev = links[idx - 1];
  return prev?.parent_resolved_relpath?.trim() || null;
}

function DiscoveryProvenanceBranchNested({
  branch,
  nestDepth,
}: {
  branch: DiscoveryProvenanceBranchPayload;
  nestDepth: number;
}) {
  if (nestDepth > 4) {
    return (
      <p className="discovery-mock-footnote" style={{ marginTop: 6 }}>
        Nested provenance depth limit reached.
      </p>
    );
  }
  const libFrom = branch.from_discovery_primary;
  return (
    <div
      style={{
        marginTop: 10,
        marginLeft: 8,
        paddingLeft: 10,
        borderLeft: "2px solid color-mix(in srgb, var(--muted) 35%, transparent)",
      }}
    >
      <p className="discovery-mock-hint" style={{ margin: "0 0 8px", fontSize: 12 }}>
        Further provenance for this source
        {libFrom ? (
          <>
            {" "}
            (indexed row <span className="mono">{basenameRelPosix(libFrom)}</span>)
          </>
        ) : null}
        {branch.nested_truncated ? <span> — list truncated in index</span> : null}
      </p>
      <div className="discovery-assets-prov-list" role="list" aria-label="Nested provenance branch">
        {branch.links.map((lnk, j) => {
          const out =
            lnk.step_output_relpath?.trim() ||
            (j === 0 ? libFrom : branch.links[j - 1]?.parent_resolved_relpath?.trim()) ||
            null;
          const lib = lnk.step_output_library === "og" || lnk.step_output_library === "wip" ? lnk.step_output_library : "og";
          const thumb = discoveryThumbSrcForRelPath(out);
          const vidPh = Boolean(out && isVideo(out) && !thumb);
          return (
            <div key={`nested-${nestDepth}-${j}-${lnk.depth}`} role="listitem">
              <DiscoveryProvenanceThumbRow
                name={out ? basenameRelPosix(out) : `Step ${lnk.depth + 1}`}
                library={lib}
                metaLine={
                  <span className="mono" style={{ fontSize: 11 }}>
                    {lnk.workflow_fingerprint?.slice(0, 12)}… · {lnk.embed_source ?? "—"}
                  </span>
                }
                thumbSrc={thumb}
                showVideoPlaceholder={vidPh}
                onActivate={() => {
                  if (out) window.open(fileUrlFromRel(out), "_blank", "noopener,noreferrer");
                }}
              />
              {lnk.branch_provenance ? (
                <DiscoveryProvenanceBranchNested branch={lnk.branch_provenance} nestDepth={nestDepth + 1} />
              ) : null}
            </div>
          );
        })}
      </div>
      {branch.terminal_source?.relpath ? (
        <div style={{ marginTop: 8 }} role="listitem">
          <DiscoveryProvenanceThumbRow
            name={basenameRelPosix(branch.terminal_source.relpath)}
            library={
              branch.terminal_source.library === "og" || branch.terminal_source.library === "wip"
                ? branch.terminal_source.library
                : "og"
            }
            metaLine={
              <span className="mono" style={{ fontSize: 11 }}>
                Original · {branch.terminal_source.chain_halted_reason ?? "—"}
              </span>
            }
            thumbSrc={discoveryThumbSrcForRelPath(branch.terminal_source.relpath)}
            showVideoPlaceholder={isVideo(branch.terminal_source.relpath) && !discoveryThumbSrcForRelPath(branch.terminal_source.relpath)}
            onActivate={() => window.open(fileUrlFromRel(branch.terminal_source!.relpath), "_blank", "noopener,noreferrer")}
          />
        </div>
      ) : null}
    </div>
  );
}

function DiscoveryProvenanceGenerationChainView({
  chain,
  it,
}: {
  chain: Extract<DiscoveryProvenanceChainResponse, { ok: true }>;
  it: DiscoveryLibraryItem;
}) {
  const { links, terminal_source: terminal, caveat } = chain;

  if (links.length === 0) {
    return (
      <>
        <p className="discovery-mock-hint" style={{ marginBottom: 10 }}>
          {caveat}
        </p>
        <p className="discovery-mock-hint">No embedded PNG prompt found for this selection.</p>
        <div className="discovery-assets-prov-list" role="list" aria-label="Selected asset only">
          <div role="listitem">
            <DiscoveryProvenanceThumbRow
              name={it.name}
              library={it.library}
              metaLine={<span className="mono">No generation metadata in index</span>}
              thumbSrc={discoveryThumbUrl(it)}
              showVideoPlaceholder={!discoveryThumbUrl(it) && Boolean(discoveryPlayUrl(it))}
              isOutput
              onActivate={() => window.open(it.url, "_blank", "noopener,noreferrer")}
            />
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <p className="discovery-mock-hint" style={{ marginBottom: 10 }}>
        {caveat}
      </p>
      <div className="discovery-assets-prov-list" role="list" aria-label="Generation chain (newest first)">
        {links.map((link, idx) => {
          const outRel = stepOutputRelPathForLink(it, link, idx, links);
          const name = outRel ? basenameRelPosix(outRel) : `Step ${link.depth + 1}`;
          const thumb = discoveryThumbSrcForRelPath(outRel);
          const vidPh = Boolean(outRel && isVideo(outRel) && !thumb);
          const lib = libraryBadgeForProvenanceStep(it, link.step_output_library ?? null);
          return (
            <div key={`prov-main-${idx}-${link.depth}`} role="listitem">
              <div>
                <DiscoveryProvenanceThumbRow
                  name={name}
                  library={lib}
                  metaLine={
                    <span className="mono" style={{ fontSize: 11 }}>
                      Step {link.depth + 1} · {link.workflow_fingerprint.slice(0, 12)}… · {link.embed_source ?? "—"}
                    </span>
                  }
                  thumbSrc={thumb}
                  showVideoPlaceholder={vidPh}
                  isOutput={idx === 0}
                  onActivate={() => {
                    if (outRel) window.open(fileUrlFromRel(outRel), "_blank", "noopener,noreferrer");
                  }}
                />
              </div>
              {link.parent_resolved_relpath ? (
                <p className="discovery-mock-footnote" style={{ margin: "4px 0 6px 44px", fontSize: 11 }}>
                  Input →{" "}
                  <a
                    href={fileUrlFromRel(link.parent_resolved_relpath)}
                    target="_blank"
                    rel="noreferrer"
                    className="mono"
                    style={{ wordBreak: "break-all" }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    {basenameRelPosix(link.parent_resolved_relpath)}
                  </a>
                  {link.input_kind ? ` · ${link.input_kind}` : ""}
                </p>
              ) : null}
              {link.branch_provenance ? (
                <DiscoveryProvenanceBranchNested branch={link.branch_provenance} nestDepth={1} />
              ) : null}
            </div>
          );
        })}
      </div>
      {terminal?.relpath ? (
        <>
          <h4 className="discovery-mock-section-title" style={{ marginTop: 14, marginBottom: 6, fontSize: 13 }}>
            Original source media
          </h4>
          <div className="discovery-assets-prov-list" role="list" aria-label="End of provenance chain">
            <div role="listitem">
              <DiscoveryProvenanceThumbRow
                name={basenameRelPosix(terminal.relpath)}
                library={
                  terminal.library === "og" || terminal.library === "wip"
                    ? terminal.library
                    : libraryBadgeForProvenanceStep(it, null)
                }
                metaLine={
                  <span className="mono" style={{ fontSize: 11 }}>
                    No further embedded workflow
                    {terminal.chain_halted_reason ? ` · ${terminal.chain_halted_reason}` : ""}
                  </span>
                }
                thumbSrc={discoveryThumbSrcForRelPath(terminal.relpath)}
                showVideoPlaceholder={isVideo(terminal.relpath) && !discoveryThumbSrcForRelPath(terminal.relpath)}
                onActivate={() => window.open(fileUrlFromRel(terminal.relpath), "_blank", "noopener,noreferrer")}
              />
            </div>
          </div>
        </>
      ) : null}
    </>
  );
}

function DiscoveryItemMetaBody({
  it,
  k,
  saved,
  onToggleSaved,
  exemplarInLibrary,
  onExemplarInLibraryChange,
}: {
  it: DiscoveryLibraryItem;
  k: string;
  saved: Set<string>;
  onToggleSaved: (key: string) => void;
  exemplarInLibrary?: boolean;
  onExemplarInLibraryChange?: (next: boolean) => void;
}) {
  const prev = it.class_types_preview ?? [];
  const play = discoveryPlayUrl(it);
  const thumb = discoveryThumbUrl(it);
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, fontSize: 15, wordBreak: "break-word", flex: "1 1 200px" }}>{it.name}</span>
        <button type="button" className="icon-btn" onClick={() => onToggleSaved(k)} style={{ fontSize: 18 }}>
          {saved.has(k) ? "★ Saved" : "☆ Save"}
        </button>
        {onExemplarInLibraryChange ? (
          <label
            className="icon-btn"
            style={{
              fontSize: 13,
              whiteSpace: "nowrap",
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              cursor: "pointer",
              userSelect: "none",
            }}
            title="Server exemplar library (Workflows tab). Uncheck to remove."
          >
            <input
              type="checkbox"
              checked={Boolean(exemplarInLibrary)}
              onChange={(e) => onExemplarInLibraryChange(e.target.checked)}
            />
            <span>Exemplars</span>
          </label>
        ) : null}
      </div>
      {it.video_relpath ? (
        <div className="mono" style={{ fontSize: 12, color: "var(--muted)", wordBreak: "break-all", marginBottom: 4 }}>
          Video: {it.video_relpath}
        </div>
      ) : null}
      {it.thumb_relpath && it.thumb_relpath !== it.video_relpath ? (
        <div className="mono" style={{ fontSize: 12, color: "var(--muted)", wordBreak: "break-all", marginBottom: 4 }}>
          Thumb: {it.thumb_relpath}
        </div>
      ) : null}
      {!it.video_relpath && !it.thumb_relpath ? (
        <div className="mono" style={{ fontSize: 12, color: "var(--muted)", wordBreak: "break-all", marginBottom: 8 }}>
          {it.relpath}
        </div>
      ) : null}
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
        {fmtTime(it.mtime)} · {fmtSize(it.size)} · <span className="mono">{it.library}</span>
      </div>
      {it.workflow_fingerprint ? (
        <div style={{ fontSize: 12, marginBottom: 8 }}>
          <span style={{ color: "var(--muted)" }}>Workflow fingerprint </span>
          <span className="mono">{it.workflow_fingerprint}</span>
          {it.has_embedded_prompt ? <span style={{ color: "var(--good)", marginLeft: 8 }}>prompt embedded</span> : null}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
          No embedded Comfy prompt in PNG (videos often have no PNG metadata here).
        </div>
      )}
      {prev.length ? (
        <div style={{ fontSize: 12, marginBottom: 10 }}>
          <div style={{ color: "var(--muted)", marginBottom: 4 }}>Node types (preview)</div>
          <div className="mono" style={{ lineHeight: 1.4 }}>
            {prev.join(" → ")}
          </div>
        </div>
      ) : null}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
        {play ? (
          <a href={play} target="_blank" rel="noreferrer">
            Open video
          </a>
        ) : null}
        {thumb && thumb !== play ? (
          <a href={thumb} target="_blank" rel="noreferrer">
            Open image
          </a>
        ) : null}
        {!play && !thumb ? (
          <a href={it.url} target="_blank" rel="noreferrer">
            Open file
          </a>
        ) : null}
        <button
          type="button"
          onClick={() => void navigator.clipboard.writeText(it.video_relpath || it.thumb_relpath || it.relpath)}
          style={{ fontSize: 13 }}
        >
          Copy primary path
        </button>
      </div>
      {it.members && it.members.length > 1 ? (
        <div style={{ marginTop: 12, fontSize: 12 }}>
          <div style={{ color: "var(--muted)", marginBottom: 6 }}>Files in this group</div>
          <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 6 }}>
            {it.members.map((m: DiscoveryMember) => (
              <li key={m.relpath}>
                <span className="mono" style={{ color: "var(--muted)" }}>
                  {m.kind}
                </span>{" "}
                <a href={fileUrlFromRel(m.relpath)} target="_blank" rel="noreferrer">
                  {m.name}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}

type ThumbRowProps = {
  it: DiscoveryLibraryItem;
  saved: boolean;
  isNew?: boolean;
  onToggleSaved: () => void;
  onActivate: () => void;
  selected?: boolean;
  /** Stable id for scroll-into-view (phone + desktop lists). */
  listRowId?: string;
  /** Desktop list is a listbox; phone rows stay focusable buttons even with listRowId. */
  desktopListboxChild?: boolean;
};

function DiscoveryListThumbRow({
  it,
  saved,
  isNew,
  onToggleSaved,
  onActivate,
  selected,
  listRowId,
  desktopListboxChild,
}: ThumbRowProps) {
  const thumb = discoveryThumbUrl(it);
  const play = discoveryPlayUrl(it);
  const isDesktopOption = Boolean(desktopListboxChild);
  return (
    <div
      id={listRowId}
      className={`discovery-phone-row${selected ? " discovery-desktop-row--selected" : ""}`}
      role={isDesktopOption ? "option" : "button"}
      tabIndex={isDesktopOption ? -1 : 0}
      aria-selected={selected ? true : undefined}
      onClick={onActivate}
      style={{ position: "relative" }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onActivate();
        }
      }}
    >
      {isNew ? (
        <span
          style={{
            position: "absolute",
            top: 6,
            right: 8,
            fontSize: 9,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: 0.2,
            padding: "0 4px",
            borderRadius: 999,
            color: "#3f2500",
            background: "rgba(255, 181, 71, 0.72)",
            border: "1px solid rgba(255, 181, 71, 0.48)",
            pointerEvents: "none",
          }}
        >
          new
        </span>
      ) : null}
      <div className="discovery-phone-thumb" aria-hidden>
        {thumb ? (
          <img src={thumb} alt="" loading="lazy" decoding="async" />
        ) : play ? (
          <span className="discovery-phone-thumb-placeholder">▶ Video</span>
        ) : (
          <span className="discovery-phone-thumb-placeholder">File</span>
        )}
      </div>
      <div style={{ flex: "1 1 auto", minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ fontWeight: 600, fontSize: 14, wordBreak: "break-word", lineHeight: 1.25 }}>{it.name}</div>
        <div style={{ fontSize: 12, color: "var(--muted)", display: "flex", flexWrap: "wrap", gap: 8 }}>
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              textTransform: "uppercase",
              padding: "1px 6px",
              borderRadius: 4,
              background: it.library === "og" ? "rgba(90,162,255,0.2)" : "rgba(70,211,154,0.18)",
            }}
          >
            {it.library}
          </span>
          <span className="mono">{fmtTime(it.mtime)}</span>
          <span>{fmtSize(it.size)}</span>
        </div>
      </div>
      <button
        type="button"
        className="icon-btn"
        title={saved ? "Remove from saved" : "Save for later"}
        aria-label={saved ? "Remove from saved" : "Save for later"}
        onClick={(e) => {
          e.stopPropagation();
          onToggleSaved();
        }}
        style={{ fontSize: 20, lineHeight: 1, flexShrink: 0 }}
      >
        {saved ? "★" : "☆"}
      </button>
    </div>
  );
}

type DiscoveryProvenanceThumbRowProps = {
  name: string;
  library: string;
  /** Short secondary line (e.g. kind label, size — members have no mtime in the index). */
  metaLine: React.ReactNode;
  thumbSrc: string | null;
  showVideoPlaceholder: boolean;
  /** Primary file for this library row (merged output). */
  isOutput?: boolean;
  showSavedButton?: boolean;
  saved?: boolean;
  onToggleSaved?: () => void;
  onActivate: () => void;
};

/** Same layout as `DiscoveryListThumbRow`, for co-located bundle files in the Assets tab. */
function DiscoveryProvenanceThumbRow({
  name,
  library,
  metaLine,
  thumbSrc,
  showVideoPlaceholder,
  isOutput,
  showSavedButton,
  saved,
  onToggleSaved,
  onActivate,
}: DiscoveryProvenanceThumbRowProps) {
  return (
    <div
      className={`discovery-phone-row discovery-assets-prov-row${isOutput ? " discovery-assets-prov-row--output" : ""}`}
      role="button"
      tabIndex={0}
      onClick={onActivate}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onActivate();
        }
      }}
    >
      <div className="discovery-phone-thumb" aria-hidden>
        {thumbSrc ? (
          <img src={thumbSrc} alt="" loading="lazy" decoding="async" />
        ) : showVideoPlaceholder ? (
          <span className="discovery-phone-thumb-placeholder">▶ Video</span>
        ) : (
          <span className="discovery-phone-thumb-placeholder">File</span>
        )}
      </div>
      <div style={{ flex: "1 1 auto", minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ fontWeight: 600, fontSize: 14, wordBreak: "break-word", lineHeight: 1.25 }}>{name}</div>
        <div style={{ fontSize: 12, color: "var(--muted)", display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              textTransform: "uppercase",
              padding: "1px 6px",
              borderRadius: 4,
              background: library === "og" ? "rgba(90,162,255,0.2)" : "rgba(70,211,154,0.18)",
            }}
          >
            {library}
          </span>
          {metaLine}
          {isOutput ? (
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                textTransform: "uppercase",
                padding: "1px 6px",
                borderRadius: 4,
                background: "rgba(120, 140, 255, 0.22)",
                color: "var(--fg)",
              }}
              title="Canonical file for this merged discovery row"
            >
              Output
            </span>
          ) : null}
        </div>
      </div>
      {showSavedButton && onToggleSaved ? (
        <button
          type="button"
          className="icon-btn"
          title={saved ? "Remove from saved" : "Save for later"}
          aria-label={saved ? "Remove from saved" : "Save for later"}
          onClick={(e) => {
            e.stopPropagation();
            onToggleSaved();
          }}
          style={{ fontSize: 20, lineHeight: 1, flexShrink: 0 }}
        >
          {saved ? "★" : "☆"}
        </button>
      ) : (
        <span style={{ width: 36, flexShrink: 0 }} aria-hidden />
      )}
    </div>
  );
}

const DESKTOP_VIDEO_SEEK_SEC = 2;

/** Desktop preview registers these so parent can handle i / o / Backspace / Delete in the split pane. */
type DiscoveryTrimKeyboardApi = {
  setInAtPlayhead: () => void;
  setOutAtPlayhead: () => void;
  clearTrim: () => void;
};

/** Set trim **in** to the current video time (clamped vs out and min gap). */
function discoveryTrimKeyboardSetIn(
  video: HTMLVideoElement | null,
  durationHint: number,
  markOut: number | null,
  setMarkIn: (v: number) => void
): void {
  if (!video) return;
  const d = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : durationHint;
  if (!(d > 0)) return;
  const t = Math.max(0, Math.min(video.currentTime, d));
  const outV = Math.min(d, markOut ?? d);
  setMarkIn(Math.max(0, Math.min(t, outV - TRIM_HANDLE_MIN_GAP_SEC)));
}

/** Set trim **out** to the current video time (clamped vs in and min gap). */
function discoveryTrimKeyboardSetOut(
  video: HTMLVideoElement | null,
  durationHint: number,
  markIn: number | null,
  setMarkOut: (v: number) => void
): void {
  if (!video) return;
  const d = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : durationHint;
  if (!(d > 0)) return;
  const t = Math.max(0, Math.min(video.currentTime, d));
  const inV = Math.max(0, markIn ?? 0);
  setMarkOut(Math.min(d, Math.max(t, inV + TRIM_HANDLE_MIN_GAP_SEC)));
}

/** Playhead within this (seconds) of a resolved in/out counts as "on" that handle for toggle / clear. */
const TRIM_IO_HANDLE_SNAP_SEC = 0.1;

/** Repeat trim: `timeupdate` is sparse; treat as past-out slightly before `b.out` (mux / keyframe jitter). */
const TRIM_REPEAT_TIMEUPDATE_OUT_EPS_SEC = 0.048;

/**
 * Stop-at-out + WebKit (iOS): when resuming play, `currentTime` can sit slightly inside `out` while the
 * next decoded frame jumps past `out`. Widen "on or past out" so `play` seeks back into the trim window first.
 */
const TRIM_STOP_PLAY_RESUME_NEAR_OUT_SEC = 0.055;

function discoveryTrimPlayheadAtExplicitIn(
  duration: number,
  markIn: number | null,
  markOut: number | null,
  playheadSec: number
): boolean {
  if (markIn == null) return false;
  const b = phoneTrimBounds(markIn, markOut, duration);
  if (!b) return false;
  return Math.abs(playheadSec - b.in) <= TRIM_IO_HANDLE_SNAP_SEC;
}

function discoveryTrimPlayheadAtExplicitOut(
  duration: number,
  markIn: number | null,
  markOut: number | null,
  playheadSec: number
): boolean {
  if (markOut == null) return false;
  const b = phoneTrimBounds(markIn, markOut, duration);
  if (!b) return false;
  return Math.abs(playheadSec - b.out) <= TRIM_IO_HANDLE_SNAP_SEC;
}

function discoveryTrimApplyInAtPlayhead(params: {
  video: HTMLVideoElement | null;
  duration: number;
  markIn: number | null;
  markOut: number | null;
  playheadSec: number;
  playing: boolean;
  setMarkIn: (v: number | null) => void;
}): void {
  const { video, duration, markIn, markOut, playheadSec, playing, setMarkIn } = params;
  if (!Number.isFinite(duration) || duration <= 0) return;
  const atIn = discoveryTrimPlayheadAtExplicitIn(duration, markIn, markOut, playheadSec);
  if (!playing && atIn) {
    setMarkIn(null);
    return;
  }
  discoveryTrimKeyboardSetIn(video, duration, markOut, (v) => setMarkIn(v));
}

function discoveryTrimApplyOutAtPlayhead(params: {
  video: HTMLVideoElement | null;
  duration: number;
  markIn: number | null;
  markOut: number | null;
  playheadSec: number;
  playing: boolean;
  setMarkOut: (v: number | null) => void;
}): void {
  const { video, duration, markIn, markOut, playheadSec, playing, setMarkOut } = params;
  if (!Number.isFinite(duration) || duration <= 0) return;
  const atOut = discoveryTrimPlayheadAtExplicitOut(duration, markIn, markOut, playheadSec);
  if (!playing && atOut) {
    setMarkOut(null);
    return;
  }
  discoveryTrimKeyboardSetOut(video, duration, markIn, (v) => setMarkOut(v));
}

/** In / out at playhead: set when elsewhere; when paused on an explicit handle, click clears that handle. */
function TrimInOutAtPlayheadButtons({
  duration,
  markIn,
  markOut,
  setMarkIn,
  setMarkOut,
  getVideo,
  playheadSec,
  paused,
  onAfterMarkEdit,
}: {
  duration: number;
  markIn: number | null;
  markOut: number | null;
  setMarkIn: (v: number | null) => void;
  setMarkOut: (v: number | null) => void;
  getVideo: () => HTMLVideoElement | null;
  playheadSec: number;
  paused: boolean;
  onAfterMarkEdit?: () => void;
}) {
  const disabled = !Number.isFinite(duration) || duration <= 0;
  const playing = !paused;
  const atIn = discoveryTrimPlayheadAtExplicitIn(duration, markIn, markOut, playheadSec);
  const atOut = discoveryTrimPlayheadAtExplicitOut(duration, markIn, markOut, playheadSec);
  const inActivated = atIn && markIn != null;
  const outActivated = atOut && markOut != null;

  const onIn = () => {
    discoveryTrimApplyInAtPlayhead({
      video: getVideo(),
      duration,
      markIn,
      markOut,
      playheadSec,
      playing,
      setMarkIn,
    });
    onAfterMarkEdit?.();
  };
  const onOut = () => {
    discoveryTrimApplyOutAtPlayhead({
      video: getVideo(),
      duration,
      markIn,
      markOut,
      playheadSec,
      playing,
      setMarkOut,
    });
    onAfterMarkEdit?.();
  };

  const inTitle = playing
    ? "Set in at playhead (pause on the in point to clear it)"
    : inActivated
      ? "Clear trim in (playhead is on in point)"
      : "Set in at playhead";
  const outTitle = playing
    ? "Set out at playhead (pause on the out point to clear it)"
    : outActivated
      ? "Clear trim out (playhead is on out point)"
      : "Set out at playhead";

  return (
    <div className="discovery-trim-io-btns" role="group" aria-label="Trim in and out at playhead">
      <button
        type="button"
        className={"discovery-trim-io-btn" + (inActivated ? " discovery-trim-io-btn--activated" : "")}
        onClick={onIn}
        disabled={disabled}
        title={inTitle}
        aria-label={inActivated && !playing ? "Clear trim in" : "Set trim in at playhead"}
        aria-pressed={inActivated}
      >
        I
      </button>
      <button
        type="button"
        className={"discovery-trim-io-btn" + (outActivated ? " discovery-trim-io-btn--activated" : "")}
        onClick={onOut}
        disabled={disabled}
        title={outTitle}
        aria-label={outActivated && !playing ? "Clear trim out" : "Set trim out at playhead"}
        aria-pressed={outActivated}
      >
        O
      </button>
    </div>
  );
}

/** Latest desktop preview trim bounds for global arrow-key seek (clamped when trim is active). */
type DiscoveryDesktopTrimSeekRef = React.MutableRefObject<{
  markIn: number | null;
  markOut: number | null;
  duration: number;
}>;

function PhoneAutoplayToggle({
  videoAutoplay,
  onVideoAutoplayChange,
  variant,
}: {
  videoAutoplay: boolean;
  onVideoAutoplayChange: (on: boolean) => void;
  variant: "list" | "overlay";
}) {
  return (
    <label
      className={variant === "list" ? "discovery-phone-autoplay-row" : "discovery-phone-detail-autoplay"}
    >
      <input
        type="checkbox"
        checked={videoAutoplay}
        onChange={(e) => onVideoAutoplayChange(e.target.checked)}
      />
      <span>
        {variant === "overlay"
          ? "Autoplay (muted)"
          : "Autoplay when opening a video (muted until you unmute)"}
      </span>
    </label>
  );
}

function _discoveryCloneJson<T>(x: T): T {
  return JSON.parse(JSON.stringify(x)) as T;
}

function _isComfyEdgeRef(v: unknown): boolean {
  if (!Array.isArray(v) || v.length !== 2) return false;
  const [, b] = v;
  if (typeof b !== "number" || !Number.isInteger(b)) return false;
  return true;
}

function _isScalarEditable(v: unknown): v is string | number | boolean {
  if (typeof v === "boolean") return true;
  if (typeof v === "string") return true;
  if (typeof v === "number" && Number.isFinite(v)) return true;
  return false;
}

function _sortNodeIds(ids: string[]): string[] {
  return [...ids].sort((a, b) => {
    const na = Number.parseInt(a, 10);
    const nb = Number.parseInt(b, 10);
    if (String(na) === a && String(nb) === b && !Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
    return a.localeCompare(b);
  });
}

/** Friendly labels for CLIPTextEncode `text` by node order (two nodes → positive / negative). */
function _clipTextLabelsByNodeId(prompt: Record<string, unknown>): Map<string, Partial<Record<string, string>>> {
  const clipIds = _sortNodeIds(
    Object.keys(prompt).filter((nid) => {
      const n = prompt[nid];
      if (typeof n !== "object" || n === null) return false;
      const ct = (n as { class_type?: unknown }).class_type;
      return typeof ct === "string" && ct.includes("CLIPTextEncode");
    })
  );
  const out = new Map<string, Partial<Record<string, string>>>();
  clipIds.forEach((nid, i) => {
    let label: string;
    if (clipIds.length === 2) label = i === 0 ? "Positive prompt" : "Negative prompt";
    else if (clipIds.length === 1) label = "Prompt text";
    else label = `CLIP text (node ${nid})`;
    out.set(nid, { text: label });
  });
  return out;
}

type DiscoveryComfyFieldKind = "string" | "textarea" | "number" | "bool" | "json";

type DiscoveryComfyEditableRow = {
  nodeId: string;
  classType: string;
  inputKey: string;
  value: string | number | boolean;
  kind: DiscoveryComfyFieldKind;
  displayLabel: string;
};

function _buildComfyEditableRows(prompt: Record<string, unknown>): DiscoveryComfyEditableRow[] {
  const clipLabels = _clipTextLabelsByNodeId(prompt);
  const rows: DiscoveryComfyEditableRow[] = [];
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const node = prompt[nodeId];
    if (typeof node !== "object" || node === null) continue;
    const nrec = node as Record<string, unknown>;
    const classType = String(nrec.class_type ?? "Node");
    const inputs = nrec.inputs;
    if (typeof inputs !== "object" || inputs === null) continue;
    const inRec = inputs as Record<string, unknown>;
    for (const inputKey of Object.keys(inRec).sort((a, b) => a.localeCompare(b))) {
      const v = inRec[inputKey];
      if (_isComfyEdgeRef(v)) continue;
      if (_isScalarEditable(v)) {
        const clipOverride = clipLabels.get(nodeId)?.[inputKey];
        const displayLabel = clipOverride ?? `${inputKey} · ${classType}`;
        let kind: DiscoveryComfyFieldKind =
          typeof v === "boolean" ? "bool" : typeof v === "number" ? "number" : "string";
        if (typeof v === "string" && (v.includes("\n") || v.length > 160 || inputKey === "text")) {
          kind = "textarea";
        }
        rows.push({ nodeId, classType, inputKey, value: v, kind, displayLabel });
        continue;
      }
      if (Array.isArray(v) && !_isComfyEdgeRef(v)) {
        rows.push({
          nodeId,
          classType,
          inputKey,
          value: JSON.stringify(v),
          kind: "json",
          displayLabel: `${inputKey} · ${classType} (JSON)`,
        });
        continue;
      }
      if (v !== null && typeof v === "object") {
        rows.push({
          nodeId,
          classType,
          inputKey,
          value: JSON.stringify(v),
          kind: "json",
          displayLabel: `${inputKey} · ${classType} (JSON)`,
        });
      }
    }
  }
  return rows;
}

function _parsePromptDraft(raw: string): Record<string, unknown> | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const v = JSON.parse(trimmed) as unknown;
    if (typeof v !== "object" || v === null || Array.isArray(v)) return null;
    return v as Record<string, unknown>;
  } catch {
    return null;
  }
}

function DiscoveryComfyJsonInput({
  fieldKey,
  valueObj,
  displayLabel,
  onCommit,
  onParseState,
}: {
  fieldKey: string;
  valueObj: unknown;
  displayLabel: string;
  onCommit: (parsed: unknown) => void;
  onParseState: (msg: string | null) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(valueObj, null, 2));
  useEffect(() => {
    setText(JSON.stringify(valueObj, null, 2));
  }, [valueObj, fieldKey]);
  return (
    <textarea
      id={`dcf-json-${fieldKey}`}
      className="discovery-comfy-field-textarea discovery-comfy-field-textarea--json mono"
      spellCheck={false}
      rows={4}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onParseState(null);
      }}
      onBlur={() => {
        const t = text.trim();
        if (!t) {
          onParseState(`Empty JSON (${displayLabel})`);
          return;
        }
        try {
          onCommit(JSON.parse(t));
          onParseState(null);
        } catch {
          onParseState(`Invalid JSON (${displayLabel})`);
        }
      }}
    />
  );
}

function _discoveryClonePromptDraft(p: PromptDraftMap): PromptDraftMap {
  return JSON.parse(JSON.stringify(p)) as PromptDraftMap;
}

function _discoveryRandomSeedInt(): number {
  try {
    const buf = new Uint32Array(2);
    crypto.getRandomValues(buf);
    return (buf[0]! >>> 0) * 0x100000000 + (buf[1]! >>> 0);
  } catch {
    return Math.floor(Math.random() * Number.MAX_SAFE_INTEGER);
  }
}

/**
 * Clone-on-submit: patch only the literal seed in memory (leave `control_after_generate` as embedded in the draft).
 * Queued payload matches `resetPromptDraft(next)` (avoids stale React state vs `setPromptInput` + immediate send).
 */
function buildPromptForSeedPresetSubmit(
  draft: PromptDraftMap,
  target: NoiseSeedQuickEdit,
  mode: "replay" | "new" | "increment",
): PromptDraftMap {
  const next = _discoveryClonePromptDraft(draft);
  const nodeRaw = next[target.nodeId];
  if (typeof nodeRaw !== "object" || nodeRaw === null) return next;
  const node = nodeRaw as Record<string, unknown>;
  const insRaw = node.inputs;
  if (typeof insRaw !== "object" || insRaw === null) return next;
  const inputs = { ...(insRaw as Record<string, unknown>) };
  const curSeed =
    typeof inputs[target.intKey] === "number" && Number.isFinite(inputs[target.intKey] as number)
      ? Math.round(inputs[target.intKey] as number)
      : target.seedValue;

  if (mode === "replay") {
    inputs[target.intKey] = curSeed;
  } else if (mode === "new") {
    let n = Math.round(_discoveryRandomSeedInt() % Number.MAX_SAFE_INTEGER);
    if (!Number.isFinite(n)) n = target.seedValue;
    inputs[target.intKey] = n;
  } else {
    inputs[target.intKey] = Math.min(Number.MAX_SAFE_INTEGER - 1, curSeed + 1);
  }
  next[target.nodeId] = { ...node, inputs };
  return next;
}

function DiscoveryComfyQueuePanel({ it }: { it: DiscoveryLibraryItem }) {
  const itemKey = discoveryItemKey(it);
  const draftKey = discoveryDraftStorageKey(itemKey);
  const {
    promptDraft,
    setPromptInput,
    resetPromptDraft,
    undo,
    redo,
    endSliderBurst,
    canUndo,
    canRedo,
  } = usePromptDraftHistory();
  const [frontOfQueue, setFrontOfQueue] = useState(() => _discoverySessionGetBool01(DISCOVERY_COMFY_FRONT_KEY));
  const [busy, setBusy] = useState(false);
  const [submitKind, setSubmitKind] = useState<null | "replay" | "new" | "increment" | "queue">(null);
  const [error, setError] = useState<string | null>(null);
  const [resultLine, setResultLine] = useState<string | null>(null);
  const [embedLoading, setEmbedLoading] = useState(true);
  const [embedUi, setEmbedUi] = useState<DiscoveryComfyEmbedUi | null>(null);
  const [jsonFieldError, setJsonFieldError] = useState<string | null>(null);
  const [workflowPathCopied, setWorkflowPathCopied] = useState(false);

  const loadEmbedFromServer = useCallback(async () => {
    setEmbedLoading(true);
    resetPromptDraft(null);
    setEmbedUi(null);
    setError(null);
    setJsonFieldError(null);
    setResultLine(null);
    try {
      const j = await fetchDiscoveryEmbedApiPrompt(it);
      if (j.ok) {
        resetPromptDraft(_discoveryCloneJson(j.prompt));
        setEmbedUi({ kind: "loaded", pngRelpath: j.png_relpath });
        return;
      }
      const detail = [j.detail, j.hint].filter(Boolean).join(" ");
      const fallback = _discoverySessionGet(draftKey, "");
      const parsed = _parsePromptDraft(fallback);
      if (parsed) {
        resetPromptDraft(parsed);
        setEmbedUi({ kind: "note", text: `Saved draft · ${j.error}${detail ? ` — ${detail}` : ""}` });
        setError(null);
      } else {
        resetPromptDraft(null);
        setEmbedUi(null);
        setError(detail || j.error || "Could not load embedded workflow.");
      }
    } catch (e) {
      const fallback = _discoverySessionGet(draftKey, "");
      const parsed = _parsePromptDraft(fallback);
      if (parsed) {
        resetPromptDraft(parsed);
        setEmbedUi({ kind: "note", text: `Saved draft · ${e instanceof Error ? e.message : String(e)}` });
        setError(null);
      } else {
        resetPromptDraft(null);
        setEmbedUi(null);
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setEmbedLoading(false);
    }
  }, [it, draftKey, resetPromptDraft]);

  useEffect(() => {
    void loadEmbedFromServer();
  }, [loadEmbedFromServer]);

  useComfyPromptUndoKeyboard({
    active: Boolean(!embedLoading && promptDraft),
    canUndo,
    canRedo,
    undo,
    redo,
  });

  useEffect(() => {
    setWorkflowPathCopied(false);
  }, [itemKey]);

  useEffect(() => {
    if (embedLoading || !promptDraft) return;
    try {
      sessionStorage.setItem(draftKey, JSON.stringify(promptDraft));
    } catch {
      /* ignore */
    }
  }, [draftKey, promptDraft, embedLoading]);

  useEffect(() => {
    try {
      sessionStorage.setItem(DISCOVERY_COMFY_FRONT_KEY, frontOfQueue ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [frontOfQueue]);

  const editableRows = useMemo(() => _buildComfyEditableRows(promptDraft ?? {}), [promptDraft]);
  const rowsByNode = useMemo(() => {
    const m = new Map<string, DiscoveryComfyEditableRow[]>();
    for (const row of editableRows) {
      const arr = m.get(row.nodeId) ?? [];
      arr.push(row);
      m.set(row.nodeId, arr);
    }
    return m;
  }, [editableRows]);

  const onSend = useCallback(async () => {
    setError(null);
    setResultLine(null);
    if (jsonFieldError) {
      setError(jsonFieldError);
      return;
    }
    if (!promptDraft || Object.keys(promptDraft).length === 0) {
      setError("No workflow loaded to send.");
      return;
    }
    setSubmitKind("queue");
    setBusy(true);
    try {
      const res = await submitPromptToQueue({
        prompt: promptDraft,
        front: frontOfQueue,
        client_id: "discovery-ui",
      });
      const sub = res.submit;
      let line = "Sent to the ComfyUI queue.";
      if (sub && typeof sub === "object" && sub !== null && "prompt_id" in sub) {
        line = `Queued. prompt_id: ${String((sub as { prompt_id?: unknown }).prompt_id)}`;
      }
      setResultLine(line);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setSubmitKind(null);
    }
  }, [promptDraft, frontOfQueue, jsonFieldError]);

  const runSeedPresetSubmit = useCallback(
    async (mode: "replay" | "new" | "increment") => {
      setError(null);
      setResultLine(null);
      if (jsonFieldError) {
        setError(jsonFieldError);
        return;
      }
      if (!promptDraft) {
        setError("No workflow loaded to send.");
        return;
      }
      const t = findNoiseSeedQuickEdit(promptDraft);
      if (!t) {
        setError("No literal seed widget found in this prompt.");
        return;
      }
      const next = buildPromptForSeedPresetSubmit(promptDraft, t, mode);
      setSubmitKind(mode);
      setBusy(true);
      try {
        const res = await submitPromptToQueue({
          prompt: next,
          front: frontOfQueue,
          client_id: "discovery-ui",
        });
        const sub = res.submit;
        let line = "Sent to the ComfyUI queue.";
        if (sub && typeof sub === "object" && sub !== null && "prompt_id" in sub) {
          line = `Queued. prompt_id: ${String((sub as { prompt_id?: unknown }).prompt_id)}`;
        }
        setResultLine(line);
        resetPromptDraft(next);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
        setSubmitKind(null);
      }
    },
    [promptDraft, jsonFieldError, frontOfQueue, resetPromptDraft],
  );

  const viewingPathHint = it.video_relpath ?? it.relpath;
  const viewingTitleTooltip = viewingPathHint && viewingPathHint !== it.name ? `${it.name}\n${viewingPathHint}` : it.name;
  const jsonMirror = useMemo(
    () => (promptDraft ? JSON.stringify(promptDraft, null, 2) : ""),
    [promptDraft]
  );

  const seedQuickTarget = useMemo(
    () => (promptDraft ? findNoiseSeedQuickEdit(promptDraft) : null),
    [promptDraft],
  );

  const workflowFilePathForClipboard = useMemo(() => discoveryWorkflowFilePathForClipboard(it), [it]);

  const onCopyWorkflowPath = useCallback(async () => {
    const text = workflowFilePathForClipboard;
    const ok = await copyTextToClipboard(text);
    if (ok) {
      setWorkflowPathCopied(true);
      window.setTimeout(() => setWorkflowPathCopied(false), 2500);
      return;
    }
    window.prompt("Copy this path manually (Ctrl+C):", text);
  }, [workflowFilePathForClipboard]);

  const loadWorkflowHint = !embedLoading && !promptDraft;
  const loadWorkflowMsg = error ?? "No workflow loaded. Try Reload from file after selecting an item with PNG metadata.";
  const showQueueStatusStrip =
    Boolean(jsonFieldError) ||
    Boolean(error && promptDraft) ||
    Boolean(resultLine) ||
    loadWorkflowHint;

  return (
    <div className="discovery-comfy-queue-panel">
      {showQueueStatusStrip ? (
        <div className="discovery-comfy-queue-status-strip" aria-live="polite">
          {jsonFieldError ? (
            <p className="discovery-comfy-queue-msg discovery-comfy-queue-msg--error">{jsonFieldError}</p>
          ) : null}
          {error && promptDraft ? (
            <p className="discovery-comfy-queue-msg discovery-comfy-queue-msg--error">{error}</p>
          ) : null}
          {loadWorkflowHint ? (
            <p className="discovery-comfy-queue-msg discovery-comfy-queue-msg--error">{loadWorkflowMsg}</p>
          ) : null}
          {resultLine ? (
            <p className="discovery-comfy-queue-msg discovery-comfy-queue-msg--ok mono">{resultLine}</p>
          ) : null}
        </div>
      ) : null}
      <div className="discovery-comfy-queue-meta">
        <div className="discovery-comfy-queue-section-head">Now viewing</div>
        <div className="discovery-comfy-queue-viewing-title" title={viewingTitleTooltip}>
          {it.name}
        </div>
        {embedLoading ? <p className="discovery-comfy-queue-embedloading">Loading embedded workflow…</p> : null}
        {!embedLoading && embedUi?.kind === "note" ? (
          <p className="discovery-comfy-queue-embedmeta">{embedUi.text}</p>
        ) : null}
        <details className="discovery-comfy-queue-meta-paths-details">
          <summary className="discovery-comfy-queue-meta-paths-summary">
            <span className="discovery-comfy-queue-meta-paths-caret" aria-hidden="true" />
            Details
          </summary>
          <div className="discovery-comfy-queue-meta-paths-body">
            {!embedLoading && embedUi?.kind === "loaded" ? (
              <div className="discovery-comfy-queue-embed-loaded">
                <div className="discovery-comfy-queue-section-head">Prompt source</div>
                <div className="discovery-comfy-queue-path-copy">
                  <span className="discovery-comfy-queue-path-clip" title={embedUi.pngRelpath}>
                    <span className="discovery-comfy-queue-path-text mono">{embedUi.pngRelpath}</span>
                  </span>
                </div>
              </div>
            ) : null}
            <div className="discovery-comfy-queue-path-row">
              <span className="discovery-comfy-queue-path-label">Workflow file</span>
              <div className="discovery-comfy-queue-path-copy">
                <span className="discovery-comfy-queue-path-clip" title={workflowFilePathForClipboard}>
                  <span className="discovery-comfy-queue-path-text mono">{workflowFilePathForClipboard}</span>
                </span>
                <button
                  type="button"
                  className="discovery-comfy-queue-copypath"
                  title={`Copy workspace-relative path (POSIX).\n${workflowFilePathForClipboard}`}
                  onClick={() => void onCopyWorkflowPath()}
                >
                  {workflowPathCopied ? "Copied" : "Copy"}
                </button>
              </div>
            </div>
          </div>
        </details>
      </div>

      {promptDraft && !embedLoading ? (
        <>
          <DiscoveryComfyQuickEditsSection
            promptDraft={promptDraft}
            setPromptInput={setPromptInput}
            onSliderBurstEnd={endSliderBurst}
            disabled={busy}
            omitSeed
          />
          <details className="discovery-comfy-advanced-details">
            <summary>All node fields (advanced)</summary>
            <div className="discovery-comfy-fields">
              {Array.from(rowsByNode.entries()).map(([nodeId, rows]) => (
                <fieldset key={nodeId} className="discovery-comfy-node-fieldset">
                  <legend className="discovery-comfy-node-legend mono">
                    Node {nodeId} — {rows[0]?.classType ?? "?"}
                  </legend>
                  <div className="discovery-comfy-field-stack">
                    {rows.map((row) => {
                      const valueObj =
                        typeof promptDraft[row.nodeId] === "object" &&
                        promptDraft[row.nodeId] !== null &&
                        typeof (promptDraft[row.nodeId] as Record<string, unknown>).inputs === "object" &&
                        (promptDraft[row.nodeId] as Record<string, unknown>).inputs !== null
                          ? ((promptDraft[row.nodeId] as Record<string, unknown>).inputs as Record<string, unknown>)[
                              row.inputKey
                            ]
                          : row.value;
                      return (
                        <div key={`${row.nodeId}:${row.inputKey}`} className="discovery-comfy-field-row">
                          <label className="discovery-comfy-field-label" htmlFor={`dcf-${row.nodeId}-${row.inputKey}`}>
                            {row.displayLabel}
                          </label>
                          {row.kind === "bool" ? (
                            <input
                              id={`dcf-${row.nodeId}-${row.inputKey}`}
                              type="checkbox"
                              className="discovery-comfy-field-check"
                              checked={Boolean(row.value)}
                              onChange={(e) => setPromptInput(row.nodeId, row.inputKey, e.target.checked)}
                            />
                          ) : row.kind === "number" ? (
                            <input
                              id={`dcf-${row.nodeId}-${row.inputKey}`}
                              type="number"
                              className="discovery-comfy-field-input mono"
                              step="any"
                              value={typeof row.value === "number" && Number.isFinite(row.value) ? row.value : 0}
                              onChange={(e) => {
                                const n = Number.parseFloat(e.target.value);
                                setPromptInput(row.nodeId, row.inputKey, Number.isFinite(n) ? n : 0);
                              }}
                            />
                          ) : row.kind === "textarea" ? (
                            <textarea
                              id={`dcf-${row.nodeId}-${row.inputKey}`}
                              className="discovery-comfy-field-textarea mono"
                              spellCheck={false}
                              rows={6}
                              value={typeof row.value === "string" ? row.value : String(row.value)}
                              onChange={(e) => setPromptInput(row.nodeId, row.inputKey, e.target.value)}
                            />
                          ) : row.kind === "json" ? (
                            <DiscoveryComfyJsonInput
                              fieldKey={`${row.nodeId}::${row.inputKey}`}
                              valueObj={valueObj}
                              displayLabel={row.displayLabel}
                              onCommit={(parsed) => setPromptInput(row.nodeId, row.inputKey, parsed)}
                              onParseState={setJsonFieldError}
                            />
                          ) : (
                            <input
                              id={`dcf-${row.nodeId}-${row.inputKey}`}
                              type="text"
                              className="discovery-comfy-field-input mono"
                              value={typeof row.value === "string" ? row.value : String(row.value)}
                              onChange={(e) => setPromptInput(row.nodeId, row.inputKey, e.target.value)}
                            />
                          )}
                        </div>
                      );
                    })}
                  </div>
                </fieldset>
              ))}
              {editableRows.length === 0 ? (
                <p className="discovery-comfy-queue-msg">No scalar or JSON widget fields found (graph may be links-only).</p>
              ) : null}
            </div>
          </details>
        </>
      ) : null}

      {promptDraft && !embedLoading ? (
        <details className="discovery-comfy-json-details">
          <summary>Advanced: full prompt JSON</summary>
          <pre className="discovery-comfy-json-pre mono">{jsonMirror}</pre>
        </details>
      ) : null}

      {promptDraft && !embedLoading ? (
        <div className="discovery-comfy-queue-submit-block" aria-busy={busy}>
          <div className="discovery-comfy-queue-submit-head">Submit</div>
          <label className="discovery-comfy-queue-check discovery-comfy-queue-check--submit-block">
            <input
              type="checkbox"
              checked={frontOfQueue}
              disabled={busy}
              onChange={(e) => setFrontOfQueue(e.target.checked)}
            />
            <span>Send to front of queue</span>
          </label>
          {seedQuickTarget ? (
            <div className="discovery-comfy-queue-submit-presets" role="group" aria-label="Queue with seed preset">
              <button
                type="button"
                className="discovery-comfy-queue-submit-preset"
                disabled={busy || Boolean(jsonFieldError)}
                title="Same seed as in the draft, then queue"
                onClick={() => void runSeedPresetSubmit("replay")}
              >
                {busy && submitKind === "replay" ? "Sending…" : "Replay"}
              </button>
              <button
                type="button"
                className="discovery-comfy-queue-submit-preset"
                disabled={busy || Boolean(jsonFieldError)}
                title="New random seed, then queue"
                onClick={() => void runSeedPresetSubmit("new")}
              >
                {busy && submitKind === "new" ? "Sending…" : "New seed"}
              </button>
              <button
                type="button"
                className="discovery-comfy-queue-submit-preset"
                disabled={busy || Boolean(jsonFieldError)}
                title="Seed +1, then queue"
                onClick={() => void runSeedPresetSubmit("increment")}
              >
                {busy && submitKind === "increment" ? "Sending…" : "Increment"}
              </button>
            </div>
          ) : (
            <div className="discovery-comfy-queue-submit-presets" role="group" aria-label="Queue current prompt">
              <button
                type="button"
                className="discovery-comfy-queue-send"
                disabled={busy || Boolean(jsonFieldError)}
                title="Queue the draft as shown (no literal seed widget found for presets)"
                onClick={() => void onSend()}
              >
                {busy && submitKind === "queue" ? "Sending…" : "Queue draft"}
              </button>
            </div>
          )}
        </div>
      ) : null}

      <div className="discovery-comfy-queue-actions">
        <div className="discovery-comfy-queue-history" role="group" aria-label="Quick edit undo and redo">
          <button
            type="button"
            className="discovery-comfy-queue-undo"
            disabled={busy || embedLoading || !canUndo}
            title="Undo quick edit (Ctrl+Z)"
            onClick={() => undo()}
          >
            Undo
          </button>
          <button
            type="button"
            className="discovery-comfy-queue-redo"
            disabled={busy || embedLoading || !canRedo}
            title="Redo quick edit (Ctrl+Shift+Z or Ctrl+Y)"
            onClick={() => redo()}
          >
            Redo
          </button>
        </div>
        <button
          type="button"
          className="discovery-comfy-queue-reload"
          disabled={busy || embedLoading}
          onClick={() => void loadEmbedFromServer()}
        >
          Reload from file
        </button>
      </div>
    </div>
  );
}

function DiscoveryMockBadge() {
  return (
    <span className="discovery-mock-badge" title="Placeholder UI — not a product contract">
      Mock
    </span>
  );
}

function DiscoveryMockAssetsPanel({
  it,
  saved,
  onToggleSaved,
}: {
  it: DiscoveryLibraryItem | null;
  saved?: Set<string>;
  onToggleSaved?: (key: string) => void;
}) {
  const [advOpen, setAdvOpen] = useState(false);
  const [chainRes, setChainRes] = useState<DiscoveryProvenanceChainResponse | null>(null);
  const [chainErr, setChainErr] = useState("");
  const [chainLoading, setChainLoading] = useState(false);

  useEffect(() => {
    if (!it) {
      setChainRes(null);
      setChainErr("");
      setChainLoading(false);
      return;
    }
    if (it.provenance?.ok === true) {
      setChainRes(it.provenance);
      setChainErr("");
      setChainLoading(false);
      return;
    }
    let cancelled = false;
    setChainLoading(true);
    setChainErr("");
    setChainRes(null);
    void fetchDiscoveryProvenanceChain(it)
      .then((r) => {
        if (!cancelled) setChainRes(r);
      })
      .catch((e: unknown) => {
        if (!cancelled) setChainErr(String(e));
      })
      .finally(() => {
        if (!cancelled) setChainLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [it]);

  if (!it) {
    return (
      <div className="discovery-mock-panel">
        <div className="discovery-mock-banner">
          <DiscoveryMockBadge />
          <span>Select a library item for mock Assets tools.</span>
        </div>
      </div>
    );
  }
  const key = discoveryItemKey(it);
  const showSaved = Boolean(saved && onToggleSaved);
  const savedSelected = Boolean(saved?.has(key));
  const members = it.members;

  const bundleRows =
    members && members.length > 0 ? (
      <div className="discovery-assets-prov-list" role="list" aria-label="Co-located outputs in this discovery group">
        {members.map((m) => {
          const thumbSrc = discoveryMemberThumbSrc(m);
          const vidPh = !thumbSrc && isVideo(m.name);
          const primary = discoveryMemberIsPrimaryOutput(it, m);
          return (
            <div key={m.relpath} role="listitem">
              <DiscoveryProvenanceThumbRow
                name={m.name}
                library={it.library}
                metaLine={
                  <span className="mono" style={{ fontSize: 11 }}>
                    {m.kind}
                  </span>
                }
                thumbSrc={thumbSrc}
                showVideoPlaceholder={vidPh}
                isOutput={primary}
                showSavedButton={Boolean(showSaved && primary)}
                saved={Boolean(showSaved && primary && savedSelected)}
                onToggleSaved={
                  showSaved && primary && onToggleSaved ? () => onToggleSaved(key) : undefined
                }
                onActivate={() => {
                  window.open(fileUrlFromRel(m.relpath), "_blank", "noopener,noreferrer");
                }}
              />
            </div>
          );
        })}
      </div>
    ) : (
      <div className="discovery-assets-prov-list" role="list" aria-label="Single primary file">
        <div role="listitem">
          <DiscoveryProvenanceThumbRow
            name={it.name}
            library={it.library}
            metaLine={
              <>
                <span className="mono">{fmtTime(it.mtime)}</span>
                <span>{fmtSize(it.size)}</span>
              </>
            }
            thumbSrc={discoveryThumbUrl(it)}
            showVideoPlaceholder={!discoveryThumbUrl(it) && Boolean(discoveryPlayUrl(it))}
            isOutput
            showSavedButton={showSaved}
            saved={showSaved ? savedSelected : false}
            onToggleSaved={showSaved && onToggleSaved ? () => onToggleSaved(key) : undefined}
            onActivate={() => {
              window.open(it.url, "_blank", "noopener,noreferrer");
            }}
          />
        </div>
      </div>
    );

  let generationChainBlock: React.ReactNode = null;
  if (chainLoading) {
    generationChainBlock = <p className="discovery-mock-hint">Loading generation chain…</p>;
  } else if (chainErr) {
    generationChainBlock = (
      <p style={{ margin: 0, fontSize: 13, color: "var(--bad)" }} role="alert">
        {chainErr}
      </p>
    );
  } else if (chainRes && chainRes.ok === false) {
    generationChainBlock = (
      <p style={{ margin: 0, fontSize: 13, color: "var(--bad)" }} role="alert">
        {chainRes.detail ?? chainRes.error ?? "Could not load provenance chain."}
      </p>
    );
  } else if (chainRes && chainRes.ok === true) {
    generationChainBlock = (
      <>
        <DiscoveryProvenanceGenerationChainView chain={chainRes} it={it} />
        {chainRes.stops.length > 0 ? (
          <p className="discovery-mock-footnote" style={{ marginTop: 10 }}>
            Chain stopped:{" "}
            {chainRes.stops.map((s, i) => (
              <span key={i} className="mono" style={{ fontSize: 11 }}>
                {typeof s === "object" && s !== null && "reason" in s
                  ? String((s as { reason?: string }).reason ?? JSON.stringify(s))
                  : JSON.stringify(s)}
                {i < chainRes.stops.length - 1 ? "; " : ""}
              </span>
            ))}
          </p>
        ) : null}
      </>
    );
  }

  return (
    <div className="discovery-mock-panel" aria-label="Assets mock">
      <div className="discovery-mock-banner">
        <DiscoveryMockBadge />
        <span>Ideas only — not a contract.</span>
      </div>
      <p className="discovery-mock-lead">
        Tools to find, filter, and organize <strong>images and video</strong> (selection-aware).
      </p>
      <div className="discovery-mock-search-row">
        <input
          type="search"
          className="discovery-mock-input"
          placeholder="Search assets…"
          readOnly
          aria-readonly="true"
        />
        <button type="button" className="discovery-mock-button-ghost" onClick={() => setAdvOpen((o) => !o)}>
          {advOpen ? "Hide advanced" : "Advanced search…"}
        </button>
      </div>
      {advOpen ? (
        <div className="discovery-mock-adv">
          <p className="discovery-mock-hint">
            Mock filters: media kind, library, date range, size, has embedded workflow, path glob…
          </p>
        </div>
      ) : null}

      <h3 className="discovery-mock-section-title">Provenance (generation chain)</h3>
      <p className="discovery-mock-hint">
        Each step reads the Comfy API prompt embedded in a PNG next to the output, fingerprints that graph, and follows
        the first LoadImage / VHS_LoadVideo path that resolves under this workspace. The discovery index (v6+) stores
        this chain per row when you rebuild the index; otherwise it loads live from the API. When another indexed row is
        the source file for a step, an indented branch shows that row&apos;s chain. This is not the same as
        co-located outputs below.
      </p>
      {generationChainBlock}

      <h3 className="discovery-mock-section-title" style={{ marginTop: 18 }}>
        Co-located outputs
      </h3>
      <p className="discovery-mock-hint">
        Files in the same discovery group (usually same filename stem: e.g. PNG + MP4). This is not provenance; it is
        how the library merges companions.
      </p>
      {bundleRows}
    </div>
  );
}

function DiscoveryWorkflowsPanel({
  it,
  libraryItems,
  onSelectItem,
  onOpenParameters,
  itemByKey,
  exemplarSets,
  onExemplarPatch,
  exemplarReady,
  exemplarLoadError,
  exemplarSaveError,
}: {
  it: DiscoveryLibraryItem | null;
  libraryItems: DiscoveryLibraryItem[];
  onSelectItem: (item: DiscoveryLibraryItem) => void;
  onOpenParameters: () => void;
  itemByKey: Map<string, DiscoveryLibraryItem>;
  exemplarSets: DiscoveryExemplarSets;
  onExemplarPatch: (upd: (prev: DiscoveryExemplarSets) => DiscoveryExemplarSets) => void;
  exemplarReady: boolean;
  exemplarLoadError: string;
  exemplarSaveError: string;
}) {
  const sameFingerprintPeers = useMemo(() => {
    if (!it?.workflow_fingerprint) return [];
    const fp = it.workflow_fingerprint;
    const selfKey = discoveryItemKey(it);
    return libraryItems
      .filter((x) => x.workflow_fingerprint === fp && discoveryItemKey(x) !== selfKey)
      .slice()
      .sort((a, b) => b.mtime - a.mtime);
  }, [it, libraryItems]);

  const curKey = it ? discoveryItemKey(it) : null;
  const inLibrary = curKey ? exemplarSets.library.some((e) => e.key === curKey) : false;
  const inWorking = curKey ? exemplarSets.working_set.some((e) => e.key === curKey) : false;

  const prov = it?.provenance;
  const provOk = prov != null && typeof prov === "object" && "ok" in prov && prov.ok === true;
  const classPreview = (it?.class_types_preview ?? []).slice(0, 16);

  const mediaAvail = useMemo(
    () => (it ? discoveryAssetMediaAvailability(it) : { hasImage: true, hasVideo: true }),
    [it],
  );

  const workingVisibleRealIdx = useMemo(() => {
    const out: number[] = [];
    exemplarSets.working_set.forEach((ws, idx) => {
      const p = exemplarInputProfileForKey(ws.key, exemplarSets, itemByKey);
      if (exemplarCompatibleWithContext(p, mediaAvail)) out.push(idx);
    });
    return out;
  }, [exemplarSets, itemByKey, mediaAvail]);

  const libraryFiltered = useMemo(() => {
    return exemplarSets.library.filter((ent) =>
      exemplarCompatibleWithContext(exemplarInputProfileForKey(ent.key, exemplarSets, itemByKey), mediaAvail),
    );
  }, [exemplarSets, itemByKey, mediaAvail]);

  const hiddenWorkingCount = exemplarSets.working_set.length - workingVisibleRealIdx.length;
  const hiddenLibraryCount = exemplarSets.library.length - libraryFiltered.length;

  return (
    <div className="discovery-mock-panel" aria-label="Workflows">
      <div className="discovery-mock-banner">
        <span className="discovery-mock-badge" style={{ opacity: 0.85 }}>
          v1
        </span>
        <span>Server-synced exemplar sets — keys match Discovery rows (desktop and phone).</span>
      </div>

      {!exemplarReady ? (
        <p className="discovery-mock-hint" style={{ marginTop: 0 }}>
          Loading exemplar sets from server…
        </p>
      ) : null}
      {exemplarLoadError ? (
        <p style={{ margin: "8px 0", color: "var(--bad)", fontSize: 13 }} role="alert">
          Could not load exemplar sets: {exemplarLoadError}
        </p>
      ) : null}
      {exemplarSaveError ? (
        <p style={{ margin: "8px 0", color: "var(--bad)", fontSize: 13 }} role="alert">
          Save failed: {exemplarSaveError}
        </p>
      ) : null}

      <h3 className="discovery-mock-section-title">Working set</h3>
      <p className="discovery-mock-hint" style={{ marginTop: 0 }}>
        Ordered queue of exemplars for this session. Rows are filtered by the{" "}
        <strong>current asset’s</strong> available image/video inputs vs each exemplar’s inferred workflow loaders.
        Reorder with ↑↓ (desktop); tap a resolved row to select it in the list.
      </p>
      {it && (hiddenWorkingCount > 0 || hiddenLibraryCount > 0) ? (
        <p className="discovery-mock-hint" style={{ marginTop: 0, fontSize: 12 }}>
          {hiddenWorkingCount > 0 ? (
            <>
              {hiddenWorkingCount} working-set entr{hiddenWorkingCount === 1 ? "y" : "ies"} hidden for this asset.
            </>
          ) : null}
          {hiddenWorkingCount > 0 && hiddenLibraryCount > 0 ? " " : null}
          {hiddenLibraryCount > 0 ? (
            <>
              {hiddenLibraryCount} librar{hiddenLibraryCount === 1 ? "y" : "ies"} entr{hiddenLibraryCount === 1 ? "y" : "ies"} hidden.
            </>
          ) : null}
        </p>
      ) : null}
      {exemplarSets.working_set.length === 0 ? (
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>Empty — add from the library or “Add current” below.</p>
      ) : workingVisibleRealIdx.length === 0 ? (
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
          No working-set entries match this asset’s media (switch selection or clear filters on the list).
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {workingVisibleRealIdx.map((realIdx, visPos) => {
            const ws = exemplarSets.working_set[realIdx];
            const row = itemByKey.get(ws.key);
            const thumb = row ? discoveryThumbUrl(row) : null;
            return (
              <div
                key={`ws-${ws.key}-${realIdx}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  flexWrap: "wrap",
                  padding: "6px 8px",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  fontSize: 13,
                }}
              >
                {thumb ? (
                  <img src={thumb} alt="" width={40} height={40} style={{ objectFit: "cover", borderRadius: 4 }} />
                ) : (
                  <div
                    style={{
                      width: 40,
                      height: 40,
                      background: "var(--border)",
                      borderRadius: 4,
                      flexShrink: 0,
                    }}
                    title="No thumbnail in index"
                  />
                )}
                <div style={{ flex: "1 1 120px", minWidth: 0 }}>
                  {(() => {
                    const libEnt = exemplarSets.library.find((e) => e.key === ws.key);
                    const menuLabel = exemplarCatalogDisplayLabel(libEnt, row);
                    const sourceLabel = exemplarCatalogSourceLabel(libEnt, row);
                    return (
                      <>
                        <button
                          type="button"
                          className="discovery-mock-library-row-title"
                          style={{
                            background: "none",
                            border: "none",
                            padding: 0,
                            cursor: row ? "pointer" : "default",
                            textAlign: "left",
                            color: "inherit",
                            font: "inherit",
                            width: "100%",
                          }}
                          disabled={!row}
                          onClick={() => row && onSelectItem(row)}
                        >
                          {menuLabel}
                        </button>
                        {row && libEnt?.display_name?.trim() ? (
                          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }} className="mono">
                            Original exemplar: {sourceLabel}
                          </div>
                        ) : null}
                        {!row ? (
                          <span style={{ fontSize: 11, color: "var(--muted)" }} className="mono">
                            Not in current index — widen filters or rebuild.
                          </span>
                        ) : null}
                      </>
                    );
                  })()}
                </div>
                <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                  <button
                    type="button"
                    className="discovery-mock-button-ghost"
                    disabled={visPos <= 0}
                    title="Move up"
                    onClick={() =>
                      onExemplarPatch((d) => {
                        const prevReal = workingVisibleRealIdx[visPos - 1];
                        const ws2 = d.working_set.slice();
                        const t = ws2[prevReal];
                        ws2[prevReal] = ws2[realIdx];
                        ws2[realIdx] = t;
                        return { ...d, working_set: ws2 };
                      })
                    }
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="discovery-mock-button-ghost"
                    disabled={visPos >= workingVisibleRealIdx.length - 1}
                    title="Move down"
                    onClick={() =>
                      onExemplarPatch((d) => {
                        const nextReal = workingVisibleRealIdx[visPos + 1];
                        const ws2 = d.working_set.slice();
                        const t = ws2[nextReal];
                        ws2[nextReal] = ws2[realIdx];
                        ws2[realIdx] = t;
                        return { ...d, working_set: ws2 };
                      })
                    }
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="discovery-mock-button-ghost"
                    title="Remove from working set"
                    onClick={() =>
                      onExemplarPatch((d) => ({
                        ...d,
                        working_set: d.working_set.filter((_, i) => i !== realIdx),
                      }))
                    }
                  >
                    Remove
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <h3 className="discovery-mock-section-title" style={{ marginTop: 18 }}>
        Exemplar library
      </h3>
      <p className="discovery-mock-hint" style={{ marginTop: 0 }}>
        Curated reference outputs (embedded workflows). Toggle <strong>Exemplar library</strong> for the current selection,
        then promote rows to the working set. Optional <strong>custom menu label</strong> per row; the saved document keeps{" "}
        <span className="mono">source_name</span> (original name when added) separate from <span className="mono">display_name</span>.
      </p>
      <div className="discovery-mock-chip-row" style={{ marginBottom: 10 }}>
        <label
          className="discovery-mock-chip"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            cursor: !it ? "not-allowed" : "pointer",
            opacity: !it ? 0.55 : 1,
          }}
          title={
            !it
              ? "Select a list item first"
              : "Curated exemplar library. Uncheck to remove this asset from the library and working set."
          }
        >
          <input
            type="checkbox"
            checked={Boolean(it && inLibrary)}
            disabled={!it}
            onChange={(e) => {
              if (!curKey || !it) return;
              const next = e.target.checked;
              onExemplarPatch((d) =>
                next ? discoveryAppendExemplarLibraryKey(d, curKey, it) : discoveryRemoveExemplarLibraryKey(d, curKey),
              );
            }}
          />
          <span>Exemplar library</span>
        </label>
        <button
          type="button"
          className="discovery-mock-chip"
          disabled={!it || inWorking}
          title={!it ? "Select a list item first" : inWorking ? "Already in working set" : "Add to working set"}
          onClick={() => {
            if (!curKey || inWorking) return;
            onExemplarPatch((d) => {
              if (d.working_set.some((e) => e.key === curKey)) return d;
              return { ...d, working_set: [...d.working_set, { key: curKey }] };
            });
          }}
        >
          Add current to working set
        </button>
      </div>
      {exemplarSets.library.length === 0 ? (
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>Library is empty.</p>
      ) : libraryFiltered.length === 0 ? (
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
          No library entries match this asset’s available media (image vs video). Select a different asset or add new exemplars from a matching output.
        </p>
      ) : (
        <div className="discovery-mock-library">
          <div className="discovery-mock-library-cat">
            {libraryFiltered.map((ent) => {
              const row = itemByKey.get(ent.key);
              const wsin = exemplarSets.working_set.some((e) => e.key === ent.key);
              const prof = ent.input_profile ?? (row ? inferExemplarInputProfileFromItem(row) : undefined);
              const profHint =
                prof && (prof.uses_image_start || prof.uses_video_start)
                  ? ` · loaders: ${prof.uses_image_start ? "image" : ""}${prof.uses_image_start && prof.uses_video_start ? "+" : ""}${prof.uses_video_start ? "video" : ""}`
                  : "";
              const menuTitle = exemplarCatalogDisplayLabel(ent, row);
              const sourceTitle = exemplarCatalogSourceLabel(ent, row);
              return (
                <div
                  key={ent.key}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 8,
                    flexWrap: "wrap",
                    padding: "6px 0",
                    borderBottom: "1px solid var(--border)",
                  }}
                >
                  <div style={{ flex: "1 1 200px", minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
                    <button
                      type="button"
                      className="discovery-mock-library-row"
                      style={{ justifyContent: "flex-start", textAlign: "left", width: "100%" }}
                      onClick={() => row && onSelectItem(row)}
                      disabled={!row}
                    >
                      <span className="discovery-mock-library-row-title">{menuTitle}</span>
                      <span className="discovery-mock-library-row-tags mono" style={{ fontSize: 11 }}>
                        {row ? row.library : "—"}
                        {profHint ? <span style={{ color: "var(--muted)" }}>{profHint}</span> : null}
                      </span>
                    </button>
                    <input
                      type="text"
                      className="mono"
                      style={{
                        width: "100%",
                        boxSizing: "border-box",
                        fontSize: 12,
                        padding: "4px 8px",
                        borderRadius: 4,
                        border: "1px solid var(--border)",
                        background: "var(--panel)",
                        color: "var(--text)",
                      }}
                      placeholder="Custom menu label (optional)"
                      value={ent.display_name ?? ""}
                      onChange={(e) =>
                        onExemplarPatch((d) => discoverySetExemplarDisplayName(d, ent.key, e.target.value))
                      }
                      onClick={(e) => e.stopPropagation()}
                      aria-label={`Custom menu label; original exemplar: ${sourceTitle}`}
                    />
                    <div style={{ fontSize: 11, color: "var(--muted)" }}>
                      Original exemplar: <span className="mono">{sourceTitle}</span>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 4, flexShrink: 0, alignSelf: "flex-start" }}>
                    <button
                      type="button"
                      className="discovery-mock-button-ghost"
                      disabled={wsin}
                      title={wsin ? "Already in working set" : "Add to working set"}
                      onClick={() =>
                        onExemplarPatch((d) => {
                          if (d.working_set.some((e) => e.key === ent.key)) return d;
                          return { ...d, working_set: [...d.working_set, { key: ent.key }] };
                        })
                      }
                    >
                      → Set
                    </button>
                    <button
                      type="button"
                      className="discovery-mock-button-ghost"
                      onClick={() =>
                        onExemplarPatch((d) => ({
                          ...d,
                          library: d.library.filter((e) => e.key !== ent.key),
                          working_set: d.working_set.filter((e) => e.key !== ent.key),
                        }))
                      }
                    >
                      Delete
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <h3 className="discovery-mock-section-title" style={{ marginTop: 22 }}>
        Graph identity
      </h3>
      {!it ? (
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 14 }}>Select a library item for fingerprint and class preview.</p>
      ) : (
      <>
      <div style={{ display: "grid", gap: 8, fontSize: 13 }}>
        <div>
          <span style={{ color: "var(--muted)" }}>Workflow fingerprint </span>
          {it.workflow_fingerprint ? (
            <span className="mono" title={it.workflow_fingerprint}>
              {it.workflow_fingerprint}
            </span>
          ) : (
            <span style={{ color: "var(--muted)" }}>— not indexed for this row</span>
          )}
        </div>
        <div>
          <span style={{ color: "var(--muted)" }}>Embedded prompt in metadata </span>
          <span>{it.has_embedded_prompt ? "yes" : "no"}</span>
        </div>
        {classPreview.length ? (
          <div>
            <span style={{ color: "var(--muted)", display: "block", marginBottom: 4 }}>Node classes (preview)</span>
            <span style={{ lineHeight: 1.45 }}>{classPreview.join(" · ")}</span>
          </div>
        ) : (
          <p style={{ margin: 0, color: "var(--muted)" }}>No class preview on index row.</p>
        )}
      </div>

      <h3 className="discovery-mock-section-title" style={{ marginTop: 18 }}>
        Provenance chain
      </h3>
      {provOk ? (
        <div style={{ display: "grid", gap: 8, fontSize: 13 }}>
          <div>
            <span style={{ color: "var(--muted)" }}>Steps in chain </span>
            <span>{prov.links?.length ?? 0}</span>
          </div>
          {prov.terminal_source?.relpath ? (
            <div>
              <span style={{ color: "var(--muted)" }}>Terminal source </span>
              <span className="mono" style={{ fontSize: 12 }}>
                {prov.terminal_source.relpath}
              </span>
            </div>
          ) : null}
          {prov.terminal_source?.chain_halted_reason ? (
            <div style={{ color: "var(--muted)", fontSize: 12 }}>{prov.terminal_source.chain_halted_reason}</div>
          ) : null}
          {prov.caveat ? (
            <p className="discovery-mock-hint" style={{ margin: 0 }}>
              {prov.caveat}
            </p>
          ) : null}
        </div>
      ) : (
        <p className="discovery-mock-hint" style={{ margin: 0 }}>
          {prov != null && typeof prov === "object" && "ok" in prov && prov.ok === false
            ? prov.detail || prov.error || "Provenance unavailable for this row."
            : "No provenance on this index row — try rebuilding the discovery index, or use Details / Assets."}
        </p>
      )}

      <h3 className="discovery-mock-section-title" style={{ marginTop: 18 }}>
        Same fingerprint in this list
      </h3>
      <p className="discovery-mock-hint" style={{ marginTop: 0 }}>
        Other rows sharing this fingerprint (current filter). Useful for finding exemplar siblings.
      </p>
      {!it.workflow_fingerprint ? (
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>No fingerprint — cannot match peers.</p>
      ) : sameFingerprintPeers.length === 0 ? (
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>No other items with this fingerprint in the list.</p>
      ) : (
        <div className="discovery-mock-library">
          <div className="discovery-mock-library-cat">
            {sameFingerprintPeers.slice(0, 40).map((peer) => (
              <button
                key={discoveryItemKey(peer)}
                type="button"
                className="discovery-mock-library-row"
                onClick={() => onSelectItem(peer)}
              >
                <span className="discovery-mock-library-row-title">{peer.name}</span>
                <span className="discovery-mock-library-row-tags mono" style={{ fontSize: 11 }}>
                  {peer.library}
                </span>
              </button>
            ))}
            {sameFingerprintPeers.length > 40 ? (
              <p className="discovery-mock-hint" style={{ margin: "8px 0 0" }}>
                … and {sameFingerprintPeers.length - 40} more (narrow filters to browse)
              </p>
            ) : null}
          </div>
        </div>
      )}

      <h3 className="discovery-mock-section-title" style={{ marginTop: 18 }}>
        Actions
      </h3>
      <div className="discovery-mock-chip-row">
        <button type="button" className="discovery-mock-chip" onClick={onOpenParameters}>
          Open Parameters (embed / queue)
        </button>
      </div>
      <p className="discovery-mock-footnote" style={{ marginBottom: 0 }}>
        Selection: <span className="mono">{it.relpath}</span>
      </p>
      </>
      )}
    </div>
  );
}

function DiscoveryDesktopPreview({
  it,
  saved,
  onToggleSaved,
  onVisitImage,
  onVisitVideoPlay,
  videoAutoplay,
  onVideoAutoplayChange,
  previewVideoRef,
  trimSeekBoundsRef,
  trimKeyboardRef,
  libraryItems,
  onSelectLibraryItem,
  itemByKey,
  exemplarSets,
  onExemplarPatch,
  exemplarReady,
  exemplarLoadError,
  exemplarSaveError,
}: {
  it: DiscoveryLibraryItem | null;
  saved: Set<string>;
  onToggleSaved: (key: string) => void;
  onVisitImage: (it: DiscoveryLibraryItem) => void;
  onVisitVideoPlay: (it: DiscoveryLibraryItem) => void;
  videoAutoplay: boolean;
  onVideoAutoplayChange: (on: boolean) => void;
  previewVideoRef: React.MutableRefObject<HTMLVideoElement | null>;
  trimSeekBoundsRef: DiscoveryDesktopTrimSeekRef;
  trimKeyboardRef: React.MutableRefObject<DiscoveryTrimKeyboardApi | null>;
  libraryItems: DiscoveryLibraryItem[];
  onSelectLibraryItem: (item: DiscoveryLibraryItem) => void;
  itemByKey: Map<string, DiscoveryLibraryItem>;
  exemplarSets: DiscoveryExemplarSets;
  onExemplarPatch: (upd: (prev: DiscoveryExemplarSets) => DiscoveryExemplarSets) => void;
  exemplarReady: boolean;
  exemplarLoadError: string;
  exemplarSaveError: string;
}) {
  const playUrl = it ? discoveryPlayUrl(it) : null;
  const k = it ? discoveryItemKey(it) : "";
  const trimMedia = it ? discoveryTrimMediaRelpath(it) : null;
  const thumb = it ? discoveryThumbUrl(it) : null;

  const [markIn, setMarkIn] = useState<number | null>(null);
  const [markOut, setMarkOut] = useState<number | null>(null);
  const [previewDuration, setPreviewDuration] = useState(0);
  const [trimUiCurrentTime, setTrimUiCurrentTime] = useState(0);
  const [previewPlayEpoch, bumpPreviewPlayState] = useReducer((n: number) => n + 1, 0);
  const [trimActivePresetId, setTrimActivePresetId] = useState<string | null>(null);
  const [trimPlaybackLoop, setTrimPlaybackLoop] = useState(true);
  const skipTrimPersistRef = useRef(false);
  /** Ignore `timeupdate` past-out until the rewind seek finishes (`seeked` clears this). */
  const trimLoopRewindPendingRef = useRef(false);

  const previewRootRef = useRef<HTMLDivElement | null>(null);
  const [panelTab, setPanelTab] = useState<DiscoveryDesktopPanelTab>("viewer");

  useEffect(() => {
    if (!playUrl) previewVideoRef.current = null;
  }, [playUrl, previewVideoRef]);

  useEffect(() => {
    if (!it) return;
    if (!playUrl) onVisitImage(it);
  }, [it, playUrl, onVisitImage]);

  useEffect(() => {
    trimSeekBoundsRef.current = { markIn, markOut, duration: previewDuration };
  }, [markIn, markOut, previewDuration, trimSeekBoundsRef]);

  const trimKbSetIn = useCallback(() => {
    const v = previewVideoRef.current;
    discoveryTrimApplyInAtPlayhead({
      video: v,
      duration: previewDuration,
      markIn,
      markOut,
      playheadSec: trimUiCurrentTime,
      playing: !!(v && !v.paused),
      setMarkIn,
    });
  }, [previewDuration, markOut, markIn, previewVideoRef, trimUiCurrentTime]);

  const trimKbSetOut = useCallback(() => {
    const v = previewVideoRef.current;
    discoveryTrimApplyOutAtPlayhead({
      video: v,
      duration: previewDuration,
      markIn,
      markOut,
      playheadSec: trimUiCurrentTime,
      playing: !!(v && !v.paused),
      setMarkOut,
    });
  }, [previewDuration, markIn, markOut, previewVideoRef, trimUiCurrentTime]);

  const trimKbClear = useCallback(() => {
    setMarkIn(null);
    setMarkOut(null);
  }, []);

  useLayoutEffect(() => {
    if (!playUrl) {
      trimKeyboardRef.current = null;
      return;
    }
    trimKeyboardRef.current = {
      setInAtPlayhead: trimKbSetIn,
      setOutAtPlayhead: trimKbSetOut,
      clearTrim: trimKbClear,
    };
    return () => {
      trimKeyboardRef.current = null;
    };
  }, [playUrl, trimKeyboardRef, trimKbSetIn, trimKbSetOut, trimKbClear]);

  useEffect(() => {
    if (!playUrl) {
      setPreviewDuration(0);
      setTrimUiCurrentTime(0);
    }
  }, [playUrl]);

  useEffect(() => {
    skipTrimPersistRef.current = true;
    setTrimActivePresetId(null);
    let cancelled = false;
    if (!it || !playUrl) {
      setMarkIn(null);
      setMarkOut(null);
      queueMicrotask(() => {
        if (!cancelled) skipTrimPersistRef.current = false;
      });
      return () => {
        cancelled = true;
        skipTrimPersistRef.current = false;
      };
    }
    (async () => {
      const loaded = await loadDiscoveryTrimAsync(TRIM_CONTEXT_DISCOVERY_PLAYER, trimMedia, k);
      if (cancelled) return;
      if (loaded) {
        setMarkIn(loaded.in);
        setMarkOut(loaded.out);
        setTrimActivePresetId(loaded.activePresetId);
      } else {
        setMarkIn(null);
        setMarkOut(null);
      }
      queueMicrotask(() => {
        if (!cancelled) skipTrimPersistRef.current = false;
      });
    })();
    return () => {
      cancelled = true;
      skipTrimPersistRef.current = false;
    };
  }, [it, k, playUrl, trimMedia]);

  useEffect(() => {
    if (markIn == null && markOut == null) setTrimActivePresetId(null);
  }, [markIn, markOut]);

  useEffect(() => {
    if (!playUrl) return;
    const v = previewVideoRef.current;
    if (!v) return;
    const syncMeta = () => {
      const d = v.duration;
      const dur = Number.isFinite(d) && d > 0 ? d : 0;
      setPreviewDuration(dur);
    };
    const onTime = () => setTrimUiCurrentTime(v.currentTime);
    const onPlayState = () => bumpPreviewPlayState();
    v.addEventListener("loadedmetadata", syncMeta);
    v.addEventListener("durationchange", syncMeta);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("play", onPlayState);
    v.addEventListener("pause", onPlayState);
    v.addEventListener("ended", onPlayState);
    syncMeta();
    onPlayState();
    return () => {
      v.removeEventListener("loadedmetadata", syncMeta);
      v.removeEventListener("durationchange", syncMeta);
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("play", onPlayState);
      v.removeEventListener("pause", onPlayState);
      v.removeEventListener("ended", onPlayState);
    };
  }, [playUrl, k, previewVideoRef]);

  useEffect(() => {
    if (!playUrl) return;
    const v = previewVideoRef.current;
    if (!v) return;

    trimLoopRewindPendingRef.current = false;
    let rewindSafetyTimer: ReturnType<typeof setTimeout> | null = null;
    const clearRewindSafety = () => {
      if (rewindSafetyTimer) {
        clearTimeout(rewindSafetyTimer);
        rewindSafetyTimer = null;
      }
    };

    const readDuration = () => {
      const d = v.duration;
      return Number.isFinite(d) && d > 0 ? d : 0;
    };

    const applyTrimPlayback = () => {
      const duration = readDuration();
      const b = phoneTrimBounds(markIn, markOut, duration);
      if (!b) return null;
      const trimActive = phoneTrimPlaybackActive(b, duration);
      return { b, duration, trimActive };
    };

    const rewindLoop = (b: { in: number; out: number }, opts?: { resumeAfterSeek?: boolean }) => {
      clearRewindSafety();
      trimLoopRewindPendingRef.current = true;
      const resume = opts?.resumeAfterSeek ?? !v.paused;
      v.currentTime = phoneTrimLoopSeekTarget(b);
      if (resume) void v.play().catch(() => {});
      rewindSafetyTimer = setTimeout(() => {
        trimLoopRewindPendingRef.current = false;
        rewindSafetyTimer = null;
      }, 400);
    };

    const onTimeUpdate = () => {
      if (v.seeking) return;
      if (trimPlaybackLoop && trimLoopRewindPendingRef.current) return;
      const ctx = applyTrimPlayback();
      if (!ctx) return;
      const { b, duration } = ctx;
      const t = v.currentTime;

      if (t < b.in - 1e-3) {
        if (trimPlaybackLoop) {
          if (!v.paused) {
            rewindLoop(b);
          } else {
            v.currentTime = b.in;
          }
        } else {
          v.currentTime = b.in;
        }
        return;
      }

      if (!trimPlaybackLoop) {
        const pastOutPlaying =
          !v.paused && (v.ended || t + TRIM_STOP_PLAY_RESUME_NEAR_OUT_SEC >= b.out);
        if (pastOutPlaying) {
          v.pause();
          v.currentTime = Math.max(b.in, Math.min(b.out - 1 / 120, Math.max(0, duration - 1e-6)));
        }
        return;
      }

      const pastOutWhilePlaying =
        !v.paused && t + TRIM_REPEAT_TIMEUPDATE_OUT_EPS_SEC >= b.out;
      if (pastOutWhilePlaying) rewindLoop(b);
    };

    const onEnded = () => {
      if (!trimPlaybackLoop) {
        v.pause();
        return;
      }
      if (v.seeking) return;
      const ctx = applyTrimPlayback();
      if (!ctx) return;
      const { b } = ctx;
      rewindLoop(b, { resumeAfterSeek: true });
    };

    const onPlay = () => {
      const ctx = applyTrimPlayback();
      if (!ctx) return;
      const { b } = ctx;
      const t = v.currentTime;
      if (t < b.in - 1e-4) {
        v.currentTime = trimPlaybackLoop ? phoneTrimLoopSeekTarget(b) : b.in;
        return;
      }
      if (t + TRIM_STOP_PLAY_RESUME_NEAR_OUT_SEC >= b.out) {
        v.currentTime = trimPlaybackLoop ? phoneTrimLoopSeekTarget(b) : b.in;
      }
    };

    const onSeeked = () => {
      if (trimPlaybackLoop) {
        clearRewindSafety();
        trimLoopRewindPendingRef.current = false;
        return;
      }
      const ctx = applyTrimPlayback();
      if (!ctx) return;
      const { b, duration } = ctx;
      const t = v.currentTime;
      if (t >= b.out - 1e-3) {
        v.pause();
        v.currentTime = Math.max(b.in, Math.min(b.out - 1 / 120, Math.max(0, duration - 1e-6)));
      }
    };

    v.addEventListener("timeupdate", onTimeUpdate);
    v.addEventListener("ended", onEnded);
    v.addEventListener("play", onPlay);
    v.addEventListener("seeked", onSeeked);
    return () => {
      clearRewindSafety();
      v.removeEventListener("timeupdate", onTimeUpdate);
      v.removeEventListener("ended", onEnded);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("seeked", onSeeked);
    };
  }, [playUrl, markIn, markOut, trimPlaybackLoop, k, previewVideoRef]);

  useEffect(() => {
    if (!playUrl || skipTrimPersistRef.current) return;
    const t = window.setTimeout(() => {
      void persistDiscoveryTrimAsync({
        context: TRIM_CONTEXT_DISCOVERY_PLAYER,
        mediaRelpath: trimMedia,
        legacyAssetKey: k,
        markIn,
        markOut,
        duration: previewDuration,
        presetId: trimActivePresetId,
      });
    }, 220);
    return () => window.clearTimeout(t);
  }, [playUrl, k, trimMedia, markIn, markOut, previewDuration, trimActivePresetId]);

  const trimEnforcesPlayback = useMemo(() => {
    const b = phoneTrimBounds(markIn, markOut, previewDuration);
    return phoneTrimPlaybackActive(b, previewDuration);
  }, [markIn, markOut, previewDuration]);

  const previewPaused = useMemo(() => {
    void previewPlayEpoch;
    if (!playUrl) return true;
    return previewVideoRef.current?.paused ?? true;
  }, [playUrl, previewPlayEpoch, previewVideoRef]);

  const exemplarHasCurrent = it ? exemplarSets.library.some((e) => e.key === discoveryItemKey(it)) : false;

  const autoplayStrip = (
    <div className="discovery-desktop-preview-topbar">
      <label className="discovery-desktop-preview-autoplay">
        <input type="checkbox" checked={videoAutoplay} onChange={(e) => onVideoAutoplayChange(e.target.checked)} />
        <span>Autoplay (muted)</span>
      </label>
      {it ? (
        <label
          className="discovery-desktop-preview-autoplay"
          style={{ marginLeft: "auto" }}
          title="Server exemplar library. Uncheck to remove from library and working set."
        >
          <input
            type="checkbox"
            checked={exemplarHasCurrent}
            onChange={(e) => {
              const next = e.target.checked;
              const key = discoveryItemKey(it);
              onExemplarPatch((d) =>
                next ? discoveryAppendExemplarLibraryKey(d, key, it) : discoveryRemoveExemplarLibraryKey(d, key),
              );
            }}
          />
          <span>Exemplars</span>
        </label>
      ) : null}
    </div>
  );

  const tablist = (
    <div
      className="discovery-desktop-panel-tablist discovery-desktop-panel-tablist--wrap"
      role="tablist"
      aria-label="Right panel"
    >
      {DISCOVERY_DESKTOP_PANEL_TABS.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          id={discoveryDesktopPanelLabelId(t.id)}
          aria-controls="discovery-desktop-panel-tabpanel"
          aria-selected={panelTab === t.id}
          tabIndex={panelTab === t.id ? 0 : -1}
          className={
            "discovery-desktop-panel-tab" + (panelTab === t.id ? " discovery-desktop-panel-tab--active" : "")
          }
          onClick={() => setPanelTab(t.id)}
        >
          {t.label}
          {t.mock ? (
            <>
              {" "}
              <span className="discovery-mock-tab-hint">Mock</span>
            </>
          ) : null}
        </button>
      ))}
    </div>
  );

  const viewerStage =
    it && panelTab === "viewer" ? (
      <>
        {autoplayStrip}
        <div className="discovery-desktop-preview-main">
          <div className="discovery-desktop-preview-stage">
            <div className="discovery-desktop-preview-video-slot">
              {playUrl ? (
                <video
                  ref={(el) => {
                    previewVideoRef.current = el;
                  }}
                  key={k}
                  src={playUrl}
                  onPlay={() => onVisitVideoPlay(it)}
                  controls
                  playsInline
                  loop={videoAutoplay && !trimEnforcesPlayback && trimPlaybackLoop}
                  autoPlay={videoAutoplay}
                  muted={videoAutoplay}
                />
              ) : thumb ? (
                <img key={k} src={thumb} alt="" decoding="async" />
              ) : (
                <div style={{ color: "var(--muted)", textAlign: "center", padding: 16 }}>No preview for this type</div>
              )}
            </div>
            {playUrl ? (
              <div className="discovery-desktop-preview-trim">
                <div className="discovery-trim-primary-row">
                  <div className="discovery-trim-primary-row__time mono">
                    <span className="discovery-trim-time-readout">
                      {fmtVideoSec(trimUiCurrentTime)}{" "}
                      <span className="discovery-trim-range-readout-sep">/</span> {fmtVideoSec(previewDuration)}
                    </span>
                  </div>
                  <div className="discovery-trim-primary-row__center">
                    <DiscoveryTrimTransport
                      videoRef={previewVideoRef}
                      duration={previewDuration}
                      markIn={markIn}
                      markOut={markOut}
                      mediaSyncKey={k}
                      size="large"
                      onSyncTime={(t) => {
                        setTrimUiCurrentTime(t);
                      }}
                    />
                  </div>
                  <div className="discovery-trim-primary-row__io">
                    <TrimInOutAtPlayheadButtons
                      duration={previewDuration}
                      markIn={markIn}
                      markOut={markOut}
                      setMarkIn={setMarkIn}
                      setMarkOut={setMarkOut}
                      getVideo={() => previewVideoRef.current}
                      playheadSec={trimUiCurrentTime}
                      paused={previewPaused}
                    />
                  </div>
                </div>
                <div className="discovery-trim-timeline-row">
                  <div className="discovery-trim-timeline-row__track">
                    <PhoneTrimTimeline
                      duration={previewDuration}
                      currentTime={trimUiCurrentTime}
                      markIn={markIn}
                      markOut={markOut}
                      disabled={previewDuration <= 0}
                      onSeek={(t) => {
                        const v = previewVideoRef.current;
                        if (!v) return;
                        v.currentTime = t;
                        setTrimUiCurrentTime(t);
                      }}
                      onMarkInChange={setMarkIn}
                      onMarkOutChange={setMarkOut}
                    />
                  </div>
                  <div className="discovery-trim-timeline-row__actions" role="group" aria-label="Trim range options">
                    <TrimClearInOutButton
                      onClick={() => {
                        setMarkIn(null);
                        setMarkOut(null);
                      }}
                      disabled={!trimEnforcesPlayback}
                    />
                    <TrimPlaybackOutIconToggle
                      mode={trimPlaybackLoop ? "repeat" : "stop_at_end"}
                      onModeChange={(m) => setTrimPlaybackLoop(m === "repeat")}
                    />
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </>
    ) : (
      <>
        {autoplayStrip}
        <div className="discovery-desktop-preview-main discovery-desktop-preview-main--empty">
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 14, textAlign: "center", padding: "24px 16px" }}>
            Select an item from the list.
          </p>
        </div>
      </>
    );

  const metaPanelBody =
    panelTab !== "viewer" ? (
      panelTab === "workflows" ? (
        <DiscoveryWorkflowsPanel
          it={it}
          libraryItems={libraryItems}
          onSelectItem={onSelectLibraryItem}
          onOpenParameters={() => setPanelTab("parameters")}
          itemByKey={itemByKey}
          exemplarSets={exemplarSets}
          onExemplarPatch={onExemplarPatch}
          exemplarReady={exemplarReady}
          exemplarLoadError={exemplarLoadError}
          exemplarSaveError={exemplarSaveError}
        />
      ) : !it ? (
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 14 }}>Select an item from the list.</p>
      ) : panelTab === "details" ? (
        <DiscoveryItemMetaBody
          it={it}
          k={k}
          saved={saved}
          onToggleSaved={onToggleSaved}
          exemplarInLibrary={exemplarSets.library.some((e) => e.key === k)}
          onExemplarInLibraryChange={(next) => {
            const key = discoveryItemKey(it);
            onExemplarPatch((d) =>
              next ? discoveryAppendExemplarLibraryKey(d, key, it) : discoveryRemoveExemplarLibraryKey(d, key),
            );
          }}
        />
      ) : panelTab === "parameters" ? (
        <DiscoveryComfyQueuePanel it={it} />
      ) : (
        <DiscoveryMockAssetsPanel it={it} saved={saved} onToggleSaved={onToggleSaved} />
      )
    ) : (
      <p style={{ margin: 0, color: "var(--muted)", fontSize: 14 }}>Select an item from the list.</p>
    );

  return (
    <div ref={previewRootRef} className="discovery-desktop-preview discovery-desktop-preview--stacked">
      {tablist}
      <div className="discovery-desktop-panel-body">
        {panelTab === "viewer" ? (
          <div
            role="tabpanel"
            id="discovery-desktop-panel-tabpanel"
            aria-labelledby={discoveryDesktopPanelLabelId("viewer")}
            className={"discovery-desktop-viewer-pane" + (!it ? " discovery-desktop-viewer-pane--empty" : "")}
          >
            {viewerStage}
          </div>
        ) : (
          <div
            role="tabpanel"
            id="discovery-desktop-panel-tabpanel"
            className="discovery-desktop-preview-meta"
            aria-labelledby={discoveryDesktopPanelLabelId(panelTab)}
          >
            {metaPanelBody}
          </div>
        )}
      </div>
    </div>
  );
}

export function DiscoveryLibraryApp() {
  return (
    <DeviceProvider>
      <DiscoveryLibraryInner />
    </DeviceProvider>
  );
}

function DiscoveryLibraryInner() {
  const { device } = useDeviceContext();
  const isPhone = device === "phone";

  const [saved, setSaved] = useState<Set<string>>(() => loadSaved());
  const [knownKeys, setKnownKeys] = useState<Set<string>>(() => loadKeySet(DISCOVERY_KNOWN_KEY));
  const [freshKeys, setFreshKeys] = useState<Set<string>>(() => loadKeySet(DISCOVERY_FRESH_KEY));
  const [visitedKeys, setVisitedKeys] = useState<Set<string>>(() => loadKeySet(DISCOVERY_VISITED_KEY));
  const [qInput, setQInput] = useState("");
  const [qApplied, setQApplied] = useState("");
  const [sinceDays, setSinceDays] = useState(0);
  const [library, setLibrary] = useState<"all" | "og" | "wip">("all");
  const [savedOnly, setSavedOnly] = useState(false);
  const [sortField, setSortField] = useState<DiscoverySortField>(DISCOVERY_SORT_DEFAULT_FIELD);
  const [sortDirection, setSortDirection] = useState<DiscoverySortDirection>(DISCOVERY_SORT_DEFAULT_DIRECTION);
  const [data, setData] = useState<DiscoveryLibraryResponse | null>(null);
  const [discoveryStatus, setDiscoveryStatus] = useState<DiscoveryLibraryStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [listRefreshingCount, setListRefreshingCount] = useState(0);
  const [pollMin, setPollMin] = useState<(typeof DISCOVERY_LIBRARY_POLL_CHOICES)[number]>(() => loadDiscoveryPollMin());
  const [err, setErr] = useState("");
  const [refreshAck, setRefreshAck] = useState("");
  const rebuildRunning = discoveryStatus?.running === true;
  const listRefreshing = listRefreshingCount > 0;
  const reloadRunning = listRefreshing && !rebuildRunning;
  const [desktopSelectedKey, setDesktopSelectedKey] = useState<string | null>(null);
  const [listPaneWidth, setListPaneWidth] = useState<number>(() => loadDesktopListWidth());
  const listPaneWidthRef = useRef(listPaneWidth);
  listPaneWidthRef.current = listPaneWidth;
  const desktopListScrollRef = useRef<HTMLDivElement | null>(null);
  const desktopPreviewVideoRef = useRef<HTMLVideoElement | null>(null);
  const desktopTrimSeekRef = useRef<{ markIn: number | null; markOut: number | null; duration: number }>({
    markIn: null,
    markOut: null,
    duration: 0,
  });
  const desktopTrimKeyboardRef = useRef<DiscoveryTrimKeyboardApi | null>(null);
  const desktopSplitRef = useRef<HTMLDivElement | null>(null);
  const [phoneFiltersOpen, setPhoneFiltersOpen] = useState(false);
  const [desktopFiltersOpen, setDesktopFiltersOpen] = useState(true);
  /** Phone: highlighted list row (kept after closing viewer). */
  const [phoneFocusIndex, setPhoneFocusIndex] = useState<number | null>(null);
  /** Phone: detail overlay open (list highlight can remain when false). */
  const [phoneViewerOpen, setPhoneViewerOpen] = useState(false);
  const phoneListScrollRef = useRef<HTMLDivElement | null>(null);
  const [videoAutoplay, setVideoAutoplay] = useState<boolean>(() => loadVideoAutoplay());
  const [exemplarSets, setExemplarSets] = useState<DiscoveryExemplarSets>({ version: 1, library: [], working_set: [] });
  const [exemplarReady, setExemplarReady] = useState(false);
  const [exemplarLoadErr, setExemplarLoadErr] = useState("");
  const [exemplarSaveErr, setExemplarSaveErr] = useState("");
  const exemplarSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingExemplarRef = useRef<DiscoveryExemplarSets | null>(null);

  const scheduleSaveExemplars = useCallback((next: DiscoveryExemplarSets) => {
    pendingExemplarRef.current = next;
    setExemplarSets(next);
    setExemplarSaveErr("");
    if (exemplarSaveTimerRef.current) clearTimeout(exemplarSaveTimerRef.current);
    exemplarSaveTimerRef.current = setTimeout(() => {
      exemplarSaveTimerRef.current = null;
      const doc = pendingExemplarRef.current;
      if (!doc) return;
      void saveDiscoveryExemplarSets(doc)
        .then((saved) => {
          pendingExemplarRef.current = saved;
          setExemplarSets(saved);
          setExemplarSaveErr("");
        })
        .catch((e) => {
          setExemplarSaveErr(e instanceof Error ? e.message : String(e));
        });
    }, 400);
  }, []);

  const exemplarSetsRef = useRef(exemplarSets);
  exemplarSetsRef.current = exemplarSets;
  const patchExemplar = useCallback(
    (upd: (prev: DiscoveryExemplarSets) => DiscoveryExemplarSets) => {
      scheduleSaveExemplars(upd(exemplarSetsRef.current));
    },
    [scheduleSaveExemplars],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const d = await fetchDiscoveryExemplarSets();
        if (!cancelled) {
          setExemplarSets(d);
          pendingExemplarRef.current = d;
          setExemplarLoadErr("");
        }
      } catch (e) {
        if (!cancelled) {
          setExemplarLoadErr(e instanceof Error ? e.message : String(e));
        }
      } finally {
        if (!cancelled) setExemplarReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return () => {
      if (exemplarSaveTimerRef.current) clearTimeout(exemplarSaveTimerRef.current);
    };
  }, []);

  const discoveryItemByKey = useMemo(() => {
    const m = new Map<string, DiscoveryLibraryItem>();
    for (const row of data?.items ?? []) {
      m.set(discoveryItemKey(row), row);
    }
    return m;
  }, [data?.items]);

  const persistKnownCancelRef = useRef<(() => void) | null>(null);
  const persistFreshCancelRef = useRef<(() => void) | null>(null);
  const persistVisitedCancelRef = useRef<(() => void) | null>(null);

  const setVideoAutoplayFromUser = useCallback((on: boolean) => {
    setVideoAutoplay(on);
    persistVideoAutoplay(on);
  }, []);
  const seededKnownOnceRef = useRef(false);

  useEffect(() => {
    const t = setTimeout(() => setQApplied(qInput.trim()), 400);
    return () => clearTimeout(t);
  }, [qInput]);

  useEffect(() => {
    persistKnownCancelRef.current?.();
    persistKnownCancelRef.current = scheduleIdle(() => {
      persistKeySet(DISCOVERY_KNOWN_KEY, knownKeys);
    });
    return () => {
      persistKnownCancelRef.current?.();
      persistKnownCancelRef.current = null;
    };
  }, [knownKeys]);

  useEffect(() => {
    persistFreshCancelRef.current?.();
    persistFreshCancelRef.current = scheduleIdle(() => {
      persistKeySet(DISCOVERY_FRESH_KEY, freshKeys);
    });
    return () => {
      persistFreshCancelRef.current?.();
      persistFreshCancelRef.current = null;
    };
  }, [freshKeys]);

  useEffect(() => {
    persistVisitedCancelRef.current?.();
    persistVisitedCancelRef.current = scheduleIdle(() => {
      persistKeySet(DISCOVERY_VISITED_KEY, visitedKeys);
    });
    return () => {
      persistVisitedCancelRef.current?.();
      persistVisitedCancelRef.current = null;
    };
  }, [visitedKeys]);

  const load = useCallback(
    async (refresh: boolean, opts?: { soft?: boolean; incremental?: boolean }) => {
      const soft = opts?.soft === true;
      const incremental = opts?.incremental === true;
      if (soft) setListRefreshingCount((n) => n + 1);
      else setLoading(true);
      setErr("");
      try {
        const res = await fetchDiscoveryLibrary({
          refresh,
          incremental,
          q: qApplied || undefined,
          since_days: sinceDays > 0 ? sinceDays : undefined,
          library,
          limit: 1200,
        });
        setData(res);
        if (refresh) {
          setRefreshAck("Rebuild requested. Server is processing.");
        } else if (soft) {
          setRefreshAck("Refresh complete.");
        }
      } catch (e) {
        const msg = String(e);
        if (/already in progress/i.test(msg)) {
          setErr("");
          setRefreshAck("Rebuild already in progress.");
        } else {
          setErr(msg);
          setRefreshAck("");
        }
      } finally {
        if (soft) setListRefreshingCount((n) => (n > 0 ? n - 1 : 0));
        else setLoading(false);
      }
    },
    [qApplied, sinceDays, library]
  );

  const requestRebuild = useCallback(() => {
    if (rebuildRunning) {
      setRefreshAck("Rebuild already in progress.");
      return;
    }
    if (listRefreshing) {
      setRefreshAck("Please wait for refresh to finish.");
      return;
    }
    const ok = window.confirm(
      "Rebuild discovery index now?\n\nThis can take a while because it rescans output folders."
    );
    if (!ok) {
      setRefreshAck("Rebuild canceled.");
      return;
    }
    setRefreshAck("Rebuild requested…");
    void load(true, { soft: true });
  }, [rebuildRunning, listRefreshing, load]);

  const requestReload = useCallback(() => {
    if (rebuildRunning) {
      setRefreshAck("Rebuild is in progress. Refresh is disabled until it finishes.");
      return;
    }
    if (listRefreshing) {
      setRefreshAck("Refresh already in progress.");
      return;
    }
    setRefreshAck("Refresh requested…");
    void load(false, { soft: true, incremental: true });
  }, [rebuildRunning, listRefreshing, load]);

  useEffect(() => {
    void load(false);
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    const pullStatus = async () => {
      try {
        const st = await fetchDiscoveryLibraryStatus();
        if (!cancelled) setDiscoveryStatus(st);
      } catch {
        /* ignore status poll errors */
      }
    };
    void pullStatus();
    const id = window.setInterval(() => {
      void pullStatus();
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (pollMin <= 0) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      // Auto-refresh should be lightweight + timely: incremental pickup of new files.
      void load(false, { soft: true, incremental: true });
    }, pollMin * 60_000);
    return () => window.clearInterval(id);
  }, [pollMin, load]);

  const toggleSaved = useCallback((relpath: string) => {
    setSaved((prev) => {
      const n = new Set(prev);
      if (n.has(relpath)) n.delete(relpath);
      else n.add(relpath);
      persistSaved(n);
      return n;
    });
  }, []);

  const items = data?.items ?? [];
  useEffect(() => {
    if (items.length === 0) return;
    const cancel = scheduleIdle(() => {
      const keys = items.map((it) => discoveryItemKey(it));
      setKnownKeys((prevKnown) => {
        const nextKnown = new Set(prevKnown);
        const added: string[] = [];
        for (const k of keys) {
          if (!nextKnown.has(k)) {
            nextKnown.add(k);
            added.push(k);
          }
        }
        if (added.length === 0) return prevKnown;
        if (prevKnown.size === 0 && !seededKnownOnceRef.current) {
          seededKnownOnceRef.current = true;
          return nextKnown;
        }
        setFreshKeys((prevFresh) => {
          const nextFresh = new Set(prevFresh);
          let changed = false;
          for (const k of added) {
            if (visitedKeys.has(k)) continue;
            if (!nextFresh.has(k)) {
              nextFresh.add(k);
              changed = true;
            }
          }
          return changed ? nextFresh : prevFresh;
        });
        return nextKnown;
      });
    });
    return () => cancel();
  }, [items, visitedKeys]);

  const markVisited = useCallback((key: string) => {
    setVisitedKeys((prev) => {
      if (prev.has(key)) return prev;
      const next = new Set(prev);
      next.add(key);
      return next;
    });
    setFreshKeys((prev) => {
      if (!prev.has(key)) return prev;
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
  }, []);
  const markVisitedForImageView = useCallback(
    (it: DiscoveryLibraryItem) => {
      if (discoveryPlayUrl(it)) return;
      markVisited(discoveryItemKey(it));
    },
    [markVisited]
  );
  const markVisitedForVideoPlay = useCallback(
    (it: DiscoveryLibraryItem) => {
      markVisited(discoveryItemKey(it));
    },
    [markVisited]
  );
  const rebuildHeartbeatAgeMs = discoveryStatus?.heartbeat_age_ms ?? null;
  const rebuildLastError = discoveryStatus?.last_error ?? null;
  const rebuildScannedFiles = discoveryStatus?.scanned_files ?? null;
  const lastCompletedScanFiles = discoveryStatus?.last_index_timing?.files_scanned ?? data?.item_count_total ?? null;
  const rebuildProgressPct =
    rebuildRunning &&
    typeof rebuildScannedFiles === "number" &&
    Number.isFinite(rebuildScannedFiles) &&
    rebuildScannedFiles >= 0 &&
    typeof lastCompletedScanFiles === "number" &&
    Number.isFinite(lastCompletedScanFiles) &&
    lastCompletedScanFiles > 0
      ? Math.max(0, Math.min(99, Math.round((rebuildScannedFiles / lastCompletedScanFiles) * 100)))
      : null;
  const sortedItems = useMemo(() => {
    const out = [...items];
    out.sort((a, b) => discoveryCompareItems(a, b, sortField, sortDirection));
    return out;
  }, [items, sortField, sortDirection]);
  const displayed = useMemo(() => {
    if (!savedOnly) return sortedItems;
    return sortedItems.filter((it) => saved.has(discoveryItemKey(it)));
  }, [sortedItems, savedOnly, saved]);

  useEffect(() => {
    if (!isPhone || phoneFocusIndex === null) return;
    if (displayed.length === 0) {
      setPhoneFocusIndex(null);
      return;
    }
    if (phoneFocusIndex >= displayed.length) {
      setPhoneFocusIndex(displayed.length - 1);
    }
  }, [isPhone, displayed.length, phoneFocusIndex]);

  useEffect(() => {
    if (isPhone) return;
    if (displayed.length === 0) {
      setDesktopSelectedKey(null);
      return;
    }
    setDesktopSelectedKey((cur) => {
      if (cur != null && displayed.some((it) => discoveryItemKey(it) === cur)) return cur;
      return discoveryItemKey(displayed[0]);
    });
  }, [isPhone, displayed]);

  useEffect(() => {
    if (isPhone || desktopSelectedKey == null) return;
    const idx = displayed.findIndex((it) => discoveryItemKey(it) === desktopSelectedKey);
    if (idx < 0) return;
    const el = document.getElementById(`discovery-desktop-row-${idx}`);
    const root = desktopListScrollRef.current;
    const raf = requestAnimationFrame(() => {
      scrollDiscoveryListRowIntoComfortZone(root, el, "smooth");
    });
    return () => cancelAnimationFrame(raf);
  }, [isPhone, desktopSelectedKey, displayed]);

  useEffect(() => {
    if (!isPhone || phoneViewerOpen) return;
    if (phoneFocusIndex == null || phoneFocusIndex < 0 || phoneFocusIndex >= displayed.length) return;
    const el = document.getElementById(`discovery-phone-list-row-${phoneFocusIndex}`);
    const scrollRoot = phoneListScrollRef.current;
    if (!el || !scrollRoot) return;
    const raf = requestAnimationFrame(() => {
      scrollDiscoveryListRowIntoComfortZone(scrollRoot, el, "smooth");
    });
    return () => cancelAnimationFrame(raf);
  }, [isPhone, phoneViewerOpen, phoneFocusIndex, displayed]);

  useEffect(() => {
    if (isPhone) return;

    const moveSelection = (delta: number) => {
      setDesktopSelectedKey((cur) => {
        const i = cur == null ? -1 : displayed.findIndex((it) => discoveryItemKey(it) === cur);
        if (i < 0) {
          const ni = delta > 0 ? 0 : displayed.length - 1;
          return discoveryItemKey(displayed[ni]);
        }
        const ni = Math.min(displayed.length - 1, Math.max(0, i + delta));
        return discoveryItemKey(displayed[ni]);
      });
    };

    const onKeyDown = (e: KeyboardEvent) => {
      const t = e.target;
      if (!(t instanceof HTMLElement)) return;

      if (t.closest(".discovery-desktop-resize-handle")) return;
      if (t.closest(".discovery-desktop-preview-meta")) return;
      if (
        t.closest(
          'input, textarea, select, [contenteditable="true"], [contenteditable="plaintext-only"]'
        )
      ) {
        return;
      }
      if (t.closest("button")) return;
      if (t.closest("a[href]")) return;
      if (!t.closest(".discovery-desktop-split")) return;

      const k = e.key;
      const trimKb = desktopTrimKeyboardRef.current;
      const isTrimIn = k.length === 1 && k.toLowerCase() === "i";
      const isTrimOut = k.length === 1 && k.toLowerCase() === "o";
      const isTrimClear = k === "Delete" || k === "Backspace";
      if (trimKb && (isTrimIn || isTrimOut || isTrimClear)) {
        if (isTrimIn) trimKb.setInAtPlayhead();
        else if (isTrimOut) trimKb.setOutAtPlayhead();
        else trimKb.clearTrim();
        e.preventDefault();
        e.stopPropagation();
        return;
      }

      if (
        k !== "ArrowUp" &&
        k !== "ArrowDown" &&
        k !== "ArrowLeft" &&
        k !== "ArrowRight" &&
        k !== "Home" &&
        k !== "End"
      ) {
        return;
      }

      if (k === "ArrowLeft" || k === "ArrowRight") {
        const v = desktopPreviewVideoRef.current;
        if (!v) return;
        e.preventDefault();
        e.stopPropagation();
        const wasPlaying = !v.paused;
        const dur =
          Number.isFinite(v.duration) && v.duration > 0 ? v.duration : Number.POSITIVE_INFINITY;
        const step = DESKTOP_VIDEO_SEEK_SEC;
        const snap = desktopTrimSeekRef.current;
        const b = phoneTrimBounds(snap.markIn, snap.markOut, snap.duration);
        if (b && phoneTrimPlaybackActive(b, snap.duration)) {
          if (k === "ArrowLeft") {
            v.currentTime = Math.max(b.in, v.currentTime - step);
          } else {
            v.currentTime = Math.min(b.out, v.currentTime + step);
          }
        } else {
          if (k === "ArrowLeft") {
            v.currentTime = Math.max(0, v.currentTime - step);
          } else {
            v.currentTime = Math.min(dur, v.currentTime + step);
          }
        }
        if (wasPlaying) {
          queueMicrotask(() => {
            const el = desktopPreviewVideoRef.current;
            if (!el || !el.paused) return;
            void el.play().catch(() => {});
          });
        }
        return;
      }

      if (displayed.length === 0) return;
      e.preventDefault();
      e.stopPropagation();
      if (k === "ArrowDown") {
        moveSelection(1);
      } else if (k === "ArrowUp") {
        moveSelection(-1);
      } else if (k === "Home") {
        setDesktopSelectedKey(discoveryItemKey(displayed[0]));
      } else if (k === "End") {
        setDesktopSelectedKey(discoveryItemKey(displayed[displayed.length - 1]));
      }
    };

    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [isPhone, displayed]);

  const onDesktopResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    let ended = false;
    const startX = e.clientX;
    const startW = listPaneWidthRef.current;
    const split = desktopSplitRef.current;
    const onMove = (ev: MouseEvent) => {
      if (ended) return;
      if ((ev.buttons & 1) === 0) {
        cleanup();
        return;
      }
      const maxW = Math.max(
        DESKTOP_LIST_MIN,
        window.innerWidth - DESKTOP_PREVIEW_MIN - 24
      );
      const delta = ev.clientX - startX;
      const next = Math.min(maxW, Math.max(DESKTOP_LIST_MIN, startW + delta));
      setListPaneWidth(next);
    };
    const cleanup = () => {
      if (ended) return;
      ended = true;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", cleanup, true);
      window.removeEventListener("blur", cleanup);
      document.removeEventListener("visibilitychange", onVis);
      split?.removeEventListener("mouseenter", onEnter);
      persistDesktopListWidth(listPaneWidthRef.current);
    };
    const onVis = () => {
      if (document.visibilityState === "hidden") cleanup();
    };
    const onEnter = (ev: MouseEvent) => {
      if ((ev.buttons & 1) === 0) cleanup();
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", cleanup, true);
    window.addEventListener("blur", cleanup);
    document.addEventListener("visibilitychange", onVis);
    split?.addEventListener("mouseenter", onEnter);
  }, []);

  const filtersBlock = (
    <>
      <label style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 160, flex: "1 1 160px" }}>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>Search path / name</span>
        <input type="text" value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder="Substring…" autoComplete="off" />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 4, width: 120 }}>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>Last N days</span>
        <input
          type="number"
          min={0}
          step={1}
          value={sinceDays || ""}
          onChange={(e) => setSinceDays(Math.max(0, Number(e.target.value) || 0))}
          placeholder="0 = all"
        />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 100 }}>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>Tree</span>
        <select value={library} onChange={(e) => setLibrary(e.target.value as "all" | "og" | "wip")}>
          <option value="all">og + wip</option>
          <option value="og">og</option>
          <option value="wip">wip</option>
        </select>
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: isPhone ? 0 : 18 }}>
        <input type="checkbox" checked={savedOnly} onChange={(e) => setSavedOnly(e.target.checked)} />
        <span style={{ fontSize: 14 }}>Saved only</span>
      </label>
      <span style={{ flex: "1 1 20px" }} />
    </>
  );

  if (isPhone) {
    return (
      <div className="discovery-screen">
        <div className="panel discovery-panel" style={{ gap: 8 }}>
          <div className="discovery-phone-topbar">
            <a href="/" style={{ fontWeight: 600 }}>
              ← Experiments
            </a>
            <h1 className="title" style={{ margin: 0, fontSize: "1.05rem", flex: "1 1 auto" }}>
              Library
            </h1>
            <button
              type="button"
              className="discovery-phone-filters-toggle"
              aria-expanded={phoneFiltersOpen}
              onClick={() => setPhoneFiltersOpen((v) => !v)}
            >
              {phoneFiltersOpen ? "Hide filters" : "Filters"}
            </button>
            <DiscoveryRefreshMenu
              loading={loading}
              reloading={reloadRunning}
              rebuildRunning={rebuildRunning}
              rebuildProgressPct={rebuildProgressPct}
              rebuildHeartbeatAgeMs={rebuildHeartbeatAgeMs}
              rebuildLastError={rebuildLastError}
              pollMin={pollMin}
              onReload={requestReload}
              onUpdate={requestRebuild}
              onPollMinChange={(next) => {
                setPollMin(next);
                persistDiscoveryPollMin(next);
              }}
              triggerMode="click"
            />
          </div>

          <PhoneAutoplayToggle
            variant="list"
            videoAutoplay={videoAutoplay}
            onVideoAutoplayChange={setVideoAutoplayFromUser}
          />

          <div className="discovery-phone-filters-panel" hidden={!phoneFiltersOpen}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>{filtersBlock}</div>
          </div>

          {err ? (
            <div style={{ color: "var(--bad)", fontSize: 14, flexShrink: 0 }} role="alert">
              {err}
            </div>
          ) : null}

          <div style={{ fontSize: 12, color: "var(--muted)", flexShrink: 0, lineHeight: 1.35 }}>
            {loading ? (
              "Loading…"
            ) : data ? (
              <>
                <span className="mono">{data.item_count_filtered}</span> matches
                {data.truncated ? " · truncated" : ""}
                {data.from_cache ? " · cached" : ""}
                {rebuildRunning
                  ? ` · rebuilding${typeof rebuildProgressPct === "number" ? `… ~${rebuildProgressPct}%` : "…"}`
                  : reloadRunning
                    ? " · refreshing…"
                    : ""}
                {" · "}
                <span style={{ color: "var(--text)" }}>
                  Tap a row to open the viewer · after {(PHONE_VIEWER_CONTROLS_MS / 1000).toFixed(1)}s only the video
                  shows · tap for HUD · long-press on video for actions (details, trim)
                </span>
              </>
            ) : null}
          </div>
          {refreshAck ? (
            <div style={{ fontSize: 12, color: "var(--muted)", flexShrink: 0 }} role="status" aria-live="polite">
              {refreshAck}
            </div>
          ) : null}

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              flexShrink: 0,
              padding: "4px 0",
              borderTop: "1px solid var(--border)",
              borderBottom: "1px solid var(--border)",
            }}
          >
            <span style={{ fontSize: 12, color: "var(--muted)" }}>Sort</span>
            <select value={sortField} onChange={(e) => setSortField(e.target.value as DiscoverySortField)} style={{ minWidth: 120 }}>
              {DISCOVERY_SORT_FIELDS.map((opt) => (
                <option key={opt.field} value={opt.field}>
                  {opt.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="icon-btn"
              onClick={() => setSortDirection((v) => (v === "asc" ? "desc" : "asc"))}
              title={sortDirection === "asc" ? "Sort ascending (click for descending)" : "Sort descending (click for ascending)"}
              aria-label={sortDirection === "asc" ? "Sort ascending" : "Sort descending"}
              aria-pressed={sortDirection === "desc"}
            >
              <svg
                viewBox="0 0 16 16"
                width="14"
                height="14"
                aria-hidden="true"
                focusable="false"
              >
                <rect x="2" y="3" width={sortDirection === "asc" ? 6 : 12} height="2.2" rx="0.8" fill="currentColor" />
                <rect x="2" y="6.9" width="9" height="2.2" rx="0.8" fill="currentColor" />
                <rect x="2" y="10.8" width={sortDirection === "asc" ? 12 : 6} height="2.2" rx="0.8" fill="currentColor" />
              </svg>
            </button>
          </div>

          <div ref={phoneListScrollRef} className="discovery-list-scroll">
            {displayed.map((it, idx) => (
              <DiscoveryListThumbRow
                key={discoveryItemKey(it)}
                it={it}
                saved={saved.has(discoveryItemKey(it))}
                isNew={freshKeys.has(discoveryItemKey(it)) && !visitedKeys.has(discoveryItemKey(it))}
                onToggleSaved={() => toggleSaved(discoveryItemKey(it))}
                onActivate={() => {
                  markVisitedForImageView(it);
                  setPhoneFocusIndex(idx);
                  setPhoneViewerOpen(true);
                }}
                selected={phoneFocusIndex === idx}
                listRowId={`discovery-phone-list-row-${idx}`}
              />
            ))}
            {!loading && displayed.length === 0 ? (
              <div style={{ padding: 16, color: "var(--muted)" }}>No items. Open filters → Update.</div>
            ) : null}
          </div>
        </div>

        {phoneViewerOpen && phoneFocusIndex !== null && displayed[phoneFocusIndex] ? (
          <DiscoveryPhoneDetailOverlay
            items={displayed}
            index={phoneFocusIndex}
            onClose={() => setPhoneViewerOpen(false)}
            onIndexChange={setPhoneFocusIndex}
            onVisitImage={markVisitedForImageView}
            onVisitVideoPlay={markVisitedForVideoPlay}
            saved={saved}
            onToggleSaved={toggleSaved}
            videoAutoplay={videoAutoplay}
            onVideoAutoplayChange={setVideoAutoplayFromUser}
            itemByKey={discoveryItemByKey}
            exemplarSets={exemplarSets}
            onExemplarPatch={patchExemplar}
            exemplarReady={exemplarReady}
            exemplarLoadError={exemplarLoadErr}
            exemplarSaveError={exemplarSaveErr}
          />
        ) : null}
      </div>
    );
  }

  /* Desktop + tablet: list + resizable preview */
  const desktopSelectedItem =
    desktopSelectedKey == null ? null : displayed.find((it) => discoveryItemKey(it) === desktopSelectedKey) ?? null;

  useEffect(() => {
    if (isPhone || !desktopSelectedItem) return;
    markVisitedForImageView(desktopSelectedItem);
  }, [isPhone, desktopSelectedItem, markVisitedForImageView]);

  return (
    <div className="discovery-screen">
      <div className="panel discovery-panel discovery-desktop-root" style={{ gap: 0 }}>
        <div ref={desktopSplitRef} className="discovery-desktop-split">
          <div className="discovery-desktop-list-pane" style={{ flex: `0 0 ${listPaneWidth}px` }}>
            <details className="discovery-desktop-nav-details" open>
              <summary className="discovery-desktop-nav-summary">
                <span className="discovery-desktop-nav-summary-label">Navigation & filters</span>
              </summary>
              <div className="discovery-desktop-nav-details-inner">
                <header style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, flexShrink: 0 }}>
                  <a href="/" style={{ fontWeight: 600 }}>
                    ← Experiments
                  </a>
                  <h1 className="title" style={{ margin: 0, fontSize: "1.05rem" }}>
                    Og / Wip library
                  </h1>
                  <span className="discovery-subtitle" style={{ color: "var(--muted)", fontSize: 12 }}>
                    Indexed discovery (persistent scan)
                  </span>
                </header>

                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexShrink: 0, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="discovery-phone-filters-toggle"
                    aria-expanded={desktopFiltersOpen}
                    onClick={() => setDesktopFiltersOpen((v) => !v)}
                    style={{ marginLeft: 0 }}
                  >
                    {desktopFiltersOpen ? "Hide filters" : "Filters"}
                  </button>
                  <DiscoveryRefreshMenu
                    loading={loading}
                    reloading={reloadRunning}
                    rebuildRunning={rebuildRunning}
                    rebuildProgressPct={rebuildProgressPct}
                    rebuildHeartbeatAgeMs={rebuildHeartbeatAgeMs}
                    rebuildLastError={rebuildLastError}
                    pollMin={pollMin}
                    onReload={requestReload}
                    onUpdate={requestRebuild}
                    onPollMinChange={(next) => {
                      setPollMin(next);
                      persistDiscoveryPollMin(next);
                    }}
                    triggerMode="hover"
                  />
                </div>
                {desktopFiltersOpen ? (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", flexShrink: 0 }}>{filtersBlock}</div>
                ) : null}

                {err ? (
                  <div style={{ color: "var(--bad)", fontSize: 13, flexShrink: 0 }} role="alert">
                    {err}
                  </div>
                ) : null}

                <div style={{ fontSize: 12, color: "var(--muted)", flexShrink: 0, lineHeight: 1.35 }}>
                  {loading ? (
                    "Loading…"
                  ) : data ? (
                    <>
                      Index <span className="mono">{data.updated_at ?? "—"}</span>
                      {data.from_cache ? " (cached)" : " (just scanned)"}
                      {data.scan_ms != null ? ` · ${data.scan_ms} ms scan` : ""}
                      {rebuildRunning
                        ? ` · rebuilding${typeof rebuildProgressPct === "number" ? `… ~${rebuildProgressPct}%` : "…"}`
                        : reloadRunning
                          ? " · refreshing…"
                          : ""}
                      {" · "}
                      <span className="mono">{data.item_count_filtered}</span> matches
                      {data.truncated ? " (truncated)" : ""}
                    </>
                  ) : null}
                </div>
                {refreshAck ? (
                  <div style={{ fontSize: 12, color: "var(--muted)", flexShrink: 0 }} role="status" aria-live="polite">
                    {refreshAck}
                  </div>
                ) : null}
              </div>
            </details>

            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "6px 8px",
                borderBottom: "1px solid var(--border)",
                flexShrink: 0,
              }}
            >
              <span style={{ fontSize: 12, color: "var(--muted)" }}>Sort</span>
              <select value={sortField} onChange={(e) => setSortField(e.target.value as DiscoverySortField)} style={{ minWidth: 120 }}>
                {DISCOVERY_SORT_FIELDS.map((opt) => (
                  <option key={opt.field} value={opt.field}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="icon-btn"
                onClick={() => setSortDirection((v) => (v === "asc" ? "desc" : "asc"))}
                title={sortDirection === "asc" ? "Sort ascending (click for descending)" : "Sort descending (click for ascending)"}
                aria-label={sortDirection === "asc" ? "Sort ascending" : "Sort descending"}
                aria-pressed={sortDirection === "desc"}
              >
                <svg
                  viewBox="0 0 16 16"
                  width="14"
                  height="14"
                  aria-hidden="true"
                  focusable="false"
                >
                  <rect x="2" y="3" width={sortDirection === "asc" ? 6 : 12} height="2.2" rx="0.8" fill="currentColor" />
                  <rect x="2" y="6.9" width="9" height="2.2" rx="0.8" fill="currentColor" />
                  <rect x="2" y="10.8" width={sortDirection === "asc" ? 12 : 6} height="2.2" rx="0.8" fill="currentColor" />
                </svg>
              </button>
            </div>
            <div
              ref={desktopListScrollRef}
              className="discovery-list-scroll"
              tabIndex={0}
              role="listbox"
              aria-label="Library items — arrow keys: up/down change item, left/right seek video (inside trim when trim is on). Trim: i / o set in/out at playhead, Backspace or Delete clears trim."
              aria-activedescendant={
                desktopSelectedKey == null
                  ? undefined
                  : (() => {
                      const i = displayed.findIndex((x) => discoveryItemKey(x) === desktopSelectedKey);
                      return i >= 0 ? `discovery-desktop-row-${i}` : undefined;
                    })()
              }
            >
              {displayed.map((it, idx) => (
                <DiscoveryListThumbRow
                  key={discoveryItemKey(it)}
                  it={it}
                  saved={saved.has(discoveryItemKey(it))}
                  isNew={freshKeys.has(discoveryItemKey(it)) && !visitedKeys.has(discoveryItemKey(it))}
                  onToggleSaved={() => toggleSaved(discoveryItemKey(it))}
                  onActivate={() => {
                    markVisitedForImageView(it);
                    setDesktopSelectedKey(discoveryItemKey(it));
                    desktopListScrollRef.current?.focus();
                  }}
                  selected={desktopSelectedKey === discoveryItemKey(it)}
                  listRowId={`discovery-desktop-row-${idx}`}
                  desktopListboxChild
                />
              ))}
              {!loading && displayed.length === 0 ? (
                <div style={{ padding: 16, color: "var(--muted)" }}>No items. Try Update or relax filters.</div>
              ) : null}
            </div>
          </div>

          <div
            className="discovery-desktop-resize-handle"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize list and preview"
            tabIndex={0}
            onMouseDown={onDesktopResizeStart}
            onKeyDown={(e) => {
              if (e.key === "ArrowLeft") {
                e.preventDefault();
                setListPaneWidth((w) => {
                  const next = Math.max(DESKTOP_LIST_MIN, w - 16);
                  persistDesktopListWidth(next);
                  return next;
                });
              } else if (e.key === "ArrowRight") {
                e.preventDefault();
                setListPaneWidth((w) => {
                  const maxW = Math.max(
                    DESKTOP_LIST_MIN,
                    window.innerWidth - DESKTOP_PREVIEW_MIN - 24
                  );
                  const next = Math.min(maxW, w + 16);
                  persistDesktopListWidth(next);
                  return next;
                });
              }
            }}
          />
          <DiscoveryDesktopPreview
            it={desktopSelectedItem}
            saved={saved}
            onToggleSaved={toggleSaved}
            onVisitImage={markVisitedForImageView}
            onVisitVideoPlay={markVisitedForVideoPlay}
            videoAutoplay={videoAutoplay}
            onVideoAutoplayChange={setVideoAutoplayFromUser}
            previewVideoRef={desktopPreviewVideoRef}
            trimSeekBoundsRef={desktopTrimSeekRef}
            trimKeyboardRef={desktopTrimKeyboardRef}
            libraryItems={displayed}
            onSelectLibraryItem={(item) => {
              markVisitedForImageView(item);
              setDesktopSelectedKey(discoveryItemKey(item));
            }}
            itemByKey={discoveryItemByKey}
            exemplarSets={exemplarSets}
            onExemplarPatch={patchExemplar}
            exemplarReady={exemplarReady}
            exemplarLoadError={exemplarLoadErr}
            exemplarSaveError={exemplarSaveErr}
          />
        </div>
      </div>
    </div>
  );
}

type PhoneDetailProps = {
  items: DiscoveryLibraryItem[];
  index: number;
  onClose: () => void;
  onIndexChange: (i: number) => void;
  onVisitImage: (it: DiscoveryLibraryItem) => void;
  onVisitVideoPlay: (it: DiscoveryLibraryItem) => void;
  saved: Set<string>;
  onToggleSaved: (relpath: string) => void;
  videoAutoplay: boolean;
  onVideoAutoplayChange: (on: boolean) => void;
  itemByKey: Map<string, DiscoveryLibraryItem>;
  exemplarSets: DiscoveryExemplarSets;
  onExemplarPatch: (upd: (prev: DiscoveryExemplarSets) => DiscoveryExemplarSets) => void;
  exemplarReady: boolean;
  exemplarLoadError: string;
  exemplarSaveError: string;
};

function DiscoveryPhoneDetailOverlay({
  items,
  index,
  onClose,
  onIndexChange,
  onVisitImage,
  onVisitVideoPlay,
  saved,
  onToggleSaved,
  videoAutoplay,
  onVideoAutoplayChange,
  itemByKey,
  exemplarSets,
  onExemplarPatch,
  exemplarReady,
  exemplarLoadError,
  exemplarSaveError,
}: PhoneDetailProps) {
  const it = items[index];
  const play = discoveryPlayUrl(it);
  const thumb = discoveryThumbUrl(it);
  const k = discoveryItemKey(it);
  const exemplarHasThis = exemplarSets.library.some((e) => e.key === k);
  const trimMedia = discoveryTrimMediaRelpath(it);
  const phoneVideoRef = useRef<HTMLVideoElement | null>(null);
  const trimLoopRewindPendingRef = useRef(false);
  const stackRef = useRef<HTMLDivElement | null>(null);
  const detailsMetaScrollRef = useRef<HTMLDivElement | null>(null);
  const [stackActive, setStackActive] = useState<"details" | "assets" | "parameters" | "workflows">("details");
  const detailsPageActive = stackActive === "details";
  const [viewerActionMenuOpen, setViewerActionMenuOpen] = useState(false);
  const [viewerActionMenuPos, setViewerActionMenuPos] = useState<{ x: number; y: number } | null>(null);
  const [scrubSheetOpen, setScrubSheetOpen] = useState(false);
  const [markIn, setMarkIn] = useState<number | null>(null);
  const [markOut, setMarkOut] = useState<number | null>(null);
  const [phoneVideoDuration, setPhoneVideoDuration] = useState(0);
  const [scrubUiDuration, setScrubUiDuration] = useState(0);
  const [scrubUiPos, setScrubUiPos] = useState(0);
  /** Active preset in the sidecar (`*.trims.json`); drives which row POST updates when `preset_id` is sent. */
  const [trimActivePresetId, setTrimActivePresetId] = useState<string | null>(null);
  /** When trim is narrower than full clip: loop in↔out (default) vs pause at out. */
  const [trimPlaybackLoop, setTrimPlaybackLoop] = useState(true);
  const [scrubVideoPaused, setScrubVideoPaused] = useState(true);
  const [viewerChromeVisible, setViewerChromeVisible] = useState(true);
  const [showVideoControls, setShowVideoControls] = useState(true);
  /** Hide custom HUD chrome while the user scrubs / touches native video controls (more picture area). */
  const [hideHudChromeForNativeVideo, setHideHudChromeForNativeVideo] = useState(false);
  /** Thin top filename strip when HUD is hidden (e.g. swipe nav); fades after PHONE_VIEWER_CONTROLS_MS. HUD uses in-chrome strip. */
  const [filenameLinePhase, setFilenameLinePhase] = useState<"hidden" | "visible" | "fade">("hidden");
  /** Swipe next/prev: skip the index-change chrome flash and the follow-up synthetic click. */
  const skipIndexEffectTransientOnce = useRef(false);
  const suppressStageClickOnce = useRef(false);
  /** Skip debounced persist while applying storage for a new `k` (layout effect + microtask). */
  const skipTrimPersistRef = useRef(false);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  /** Browser Fullscreen API on the viewer overlay (gestures stay on our DOM). */
  const [browserViewerFullscreen, setBrowserViewerFullscreen] = useState(false);
  /** Layout fullscreen when the Fullscreen API is missing, fails, or on iOS where it does not expand the viewer. */
  const [visualViewerFullscreen, setVisualViewerFullscreen] = useState(false);
  const inViewerFullscreen = browserViewerFullscreen || visualViewerFullscreen;

  useEffect(() => {
    if (!it) return;
    if (!play) onVisitImage(it);
  }, [it, play, onVisitImage]);

  const viewerUiTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const filenameStatusTimersRef = useRef<[ReturnType<typeof setTimeout> | null, ReturnType<typeof setTimeout> | null]>([
    null,
    null,
  ]);

  const clearViewerUiTimer = useCallback(() => {
    if (viewerUiTimerRef.current) {
      clearTimeout(viewerUiTimerRef.current);
      viewerUiTimerRef.current = null;
    }
  }, []);

  const scrollStackTo = useCallback((page: "details" | "assets" | "parameters" | "workflows") => {
    const el = typeof document !== "undefined" ? document.getElementById(`discovery-phone-page-${page}`) : null;
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const onStackScroll = useCallback(() => {
    const root = stackRef.current;
    if (!root) return;
    const rootRect = root.getBoundingClientRect();
    const midY = rootRect.top + rootRect.height * 0.35;
    const pages = ["details", "assets", "parameters", "workflows"] as const;
    for (const p of pages) {
      const el = document.getElementById(`discovery-phone-page-${p}`);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      if (midY >= r.top && midY <= r.bottom) {
        setStackActive((prev) => (prev === p ? prev : p));
        break;
      }
    }
  }, []);

  useLayoutEffect(() => {
    const root = stackRef.current;
    if (!root) return;
    root.scrollTop = 0;
    setStackActive("details");
  }, [index, k]);

  useEffect(() => {
    const root = stackRef.current;
    if (!root) return;
    root.addEventListener("scroll", onStackScroll, { passive: true });
    onStackScroll();
    return () => root.removeEventListener("scroll", onStackScroll);
  }, [onStackScroll, index, k]);

  const scheduleHideViewerChrome = useCallback(() => {
    if (!detailsPageActive) return;
    clearViewerUiTimer();
    viewerUiTimerRef.current = setTimeout(() => {
      setViewerChromeVisible(false);
      setShowVideoControls(false);
      setHideHudChromeForNativeVideo(false);
      viewerUiTimerRef.current = null;
    }, PHONE_VIEWER_CONTROLS_MS);
  }, [detailsPageActive, clearViewerUiTimer]);

  const clearFilenameStatusTimers = useCallback(() => {
    const [a, b] = filenameStatusTimersRef.current;
    if (a) clearTimeout(a);
    if (b) clearTimeout(b);
    filenameStatusTimersRef.current = [null, null];
  }, []);

  const flashFilenameStatus = useCallback(() => {
    clearFilenameStatusTimers();
    setFilenameLinePhase("visible");
    const t1 = setTimeout(() => {
      setFilenameLinePhase("fade");
    }, PHONE_VIEWER_CONTROLS_MS);
    const t2 = setTimeout(() => {
      setFilenameLinePhase("hidden");
    }, PHONE_VIEWER_CONTROLS_MS + 380);
    filenameStatusTimersRef.current = [t1, t2];
  }, [clearFilenameStatusTimers]);

  useEffect(() => () => clearFilenameStatusTimers(), [clearFilenameStatusTimers]);

  /** Toolbar + native video controls (when applicable); auto-hide after PHONE_VIEWER_CONTROLS_MS. */
  const showTransientViewerUi = useCallback(() => {
    if (!detailsPageActive) return;
    setViewerActionMenuOpen(false);
    setViewerActionMenuPos(null);
    setScrubSheetOpen(false);
    clearViewerUiTimer();
    setViewerChromeVisible(true);
    setShowVideoControls(!!play);
    setHideHudChromeForNativeVideo(false);
    scheduleHideViewerChrome();
  }, [play, detailsPageActive, clearViewerUiTimer, scheduleHideViewerChrome]);

  useEffect(() => {
    if (!detailsPageActive) {
      clearViewerUiTimer();
      setViewerChromeVisible(false);
      setShowVideoControls(false);
      setHideHudChromeForNativeVideo(false);
      setViewerActionMenuOpen(false);
      setViewerActionMenuPos(null);
      setScrubSheetOpen(false);
      return;
    }
    if (skipIndexEffectTransientOnce.current) {
      skipIndexEffectTransientOnce.current = false;
      flashFilenameStatus();
      return;
    }
    showTransientViewerUi();
    return () => clearViewerUiTimer();
  }, [index, k, detailsPageActive, showTransientViewerUi, clearViewerUiTimer, flashFilenameStatus]);

  useEffect(() => {
    if (!showVideoControls || !play) {
      setHideHudChromeForNativeVideo(false);
      return;
    }
    const v = phoneVideoRef.current;
    if (!v) return;

    let fingerOnVideo = false;

    const onSeeking = () => {
      clearViewerUiTimer();
      setHideHudChromeForNativeVideo(true);
    };
    const onSeeked = () => {
      setHideHudChromeForNativeVideo(false);
      scheduleHideViewerChrome();
    };
    const onVideoTouchStart = () => {
      fingerOnVideo = true;
    };
    const onVideoTouchMove = () => {
      clearViewerUiTimer();
      setHideHudChromeForNativeVideo(true);
    };
    const endTouchHud = () => {
      if (!fingerOnVideo) return;
      fingerOnVideo = false;
      setHideHudChromeForNativeVideo(false);
      scheduleHideViewerChrome();
    };

    v.addEventListener("seeking", onSeeking);
    v.addEventListener("seeked", onSeeked);
    v.addEventListener("touchstart", onVideoTouchStart, { passive: true });
    v.addEventListener("touchmove", onVideoTouchMove, { passive: true });
    window.addEventListener("touchend", endTouchHud, { capture: true });
    window.addEventListener("touchcancel", endTouchHud, { capture: true });
    return () => {
      v.removeEventListener("seeking", onSeeking);
      v.removeEventListener("seeked", onSeeked);
      v.removeEventListener("touchstart", onVideoTouchStart);
      v.removeEventListener("touchmove", onVideoTouchMove);
      window.removeEventListener("touchend", endTouchHud, { capture: true });
      window.removeEventListener("touchcancel", endTouchHud, { capture: true });
    };
  }, [showVideoControls, play, k, index, clearViewerUiTimer, scheduleHideViewerChrome]);

  useEffect(() => {
    skipTrimPersistRef.current = true;
    setTrimActivePresetId(null);
    setScrubSheetOpen(false);
    setViewerActionMenuOpen(false);
    setViewerActionMenuPos(null);
    let cancelled = false;
    (async () => {
      if (!play) {
        if (!cancelled) {
          setMarkIn(null);
          setMarkOut(null);
        }
      } else {
        const loaded = await loadDiscoveryTrimAsync(TRIM_CONTEXT_DISCOVERY_PLAYER, trimMedia, k);
        if (cancelled) return;
        if (loaded) {
          setMarkIn(loaded.in);
          setMarkOut(loaded.out);
          setTrimActivePresetId(loaded.activePresetId);
        } else {
          setMarkIn(null);
          setMarkOut(null);
        }
      }
      queueMicrotask(() => {
        if (!cancelled) skipTrimPersistRef.current = false;
      });
    })();
    return () => {
      cancelled = true;
      skipTrimPersistRef.current = false;
    };
  }, [index, k, play, trimMedia]);

  useEffect(() => {
    if (markIn == null && markOut == null) setTrimActivePresetId(null);
  }, [markIn, markOut]);

  useEffect(() => {
    if (!play || skipTrimPersistRef.current) return;
    const t = window.setTimeout(() => {
      void persistDiscoveryTrimAsync({
        context: TRIM_CONTEXT_DISCOVERY_PLAYER,
        mediaRelpath: trimMedia,
        legacyAssetKey: k,
        markIn,
        markOut,
        duration: phoneVideoDuration,
        presetId: trimActivePresetId,
      });
    }, 220);
    return () => window.clearTimeout(t);
  }, [play, k, trimMedia, markIn, markOut, phoneVideoDuration, trimActivePresetId]);

  useEffect(() => {
    if (!viewerActionMenuOpen && !scrubSheetOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setViewerActionMenuOpen(false);
        setViewerActionMenuPos(null);
        setScrubSheetOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [viewerActionMenuOpen, scrubSheetOpen]);

  useEffect(() => {
    if (!play || !detailsPageActive) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target;
      if (!(t instanceof HTMLElement)) return;
      if (!t.closest(".discovery-phone-detail-overlay")) return;
      if (
        t.closest(
          'input, textarea, select, [contenteditable="true"], [contenteditable="plaintext-only"]'
        )
      ) {
        return;
      }
      if (t.closest("button") || t.closest("a[href]")) return;
      const key = e.key;
      const isTrimIn = key.length === 1 && key.toLowerCase() === "i";
      const isTrimOut = key.length === 1 && key.toLowerCase() === "o";
      const isTrimClear = key === "Delete" || key === "Backspace";
      if (!isTrimIn && !isTrimOut && !isTrimClear) return;
      const durHint = phoneVideoDuration || scrubUiDuration;
      const v = phoneVideoRef.current;
      const playheadSec = scrubSheetOpen ? scrubUiPos : (v?.currentTime ?? 0);
      const playing = !!(v && !v.paused);
      clearViewerUiTimer();
      if (isTrimIn) {
        discoveryTrimApplyInAtPlayhead({
          video: v,
          duration: durHint,
          markIn,
          markOut,
          playheadSec,
          playing,
          setMarkIn,
        });
      } else if (isTrimOut) {
        discoveryTrimApplyOutAtPlayhead({
          video: v,
          duration: durHint,
          markIn,
          markOut,
          playheadSec,
          playing,
          setMarkOut,
        });
      }
      else {
        setMarkIn(null);
        setMarkOut(null);
      }
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [
    play,
    detailsPageActive,
    phoneVideoDuration,
    scrubUiDuration,
    markIn,
    markOut,
    clearViewerUiTimer,
    scrubSheetOpen,
    scrubUiPos,
  ]);

  useEffect(() => {
    if (!play) {
      setPhoneVideoDuration(0);
      setScrubUiDuration(0);
      return;
    }
    const v = phoneVideoRef.current;
    if (!v) return;
    const syncMeta = () => {
      const d = v.duration;
      const dur = Number.isFinite(d) && d > 0 ? d : 0;
      setPhoneVideoDuration(dur);
      if (scrubSheetOpen) {
        setScrubUiDuration(dur);
        setScrubUiPos(v.currentTime);
      }
    };
    const onTime = () => {
      if (scrubSheetOpen) setScrubUiPos(v.currentTime);
    };
    v.addEventListener("loadedmetadata", syncMeta);
    v.addEventListener("durationchange", syncMeta);
    v.addEventListener("timeupdate", onTime);
    syncMeta();
    return () => {
      v.removeEventListener("loadedmetadata", syncMeta);
      v.removeEventListener("durationchange", syncMeta);
      v.removeEventListener("timeupdate", onTime);
    };
  }, [play, scrubSheetOpen, k, index]);

  const trimEnforcesPlayback = useMemo(() => {
    const b = phoneTrimBounds(markIn, markOut, phoneVideoDuration);
    return phoneTrimPlaybackActive(b, phoneVideoDuration);
  }, [markIn, markOut, phoneVideoDuration]);

  const phoneScrubSheetTrimNarrows = useMemo(() => {
    const d = phoneVideoDuration || scrubUiDuration;
    const b = phoneTrimBounds(markIn, markOut, d);
    return Boolean(b && phoneTrimPlaybackActive(b, d));
  }, [markIn, markOut, phoneVideoDuration, scrubUiDuration]);

  useEffect(() => {
    const v = phoneVideoRef.current;
    if (!v || !play) return;

    trimLoopRewindPendingRef.current = false;
    let rewindSafetyTimer: ReturnType<typeof setTimeout> | null = null;
    const clearRewindSafety = () => {
      if (rewindSafetyTimer) {
        clearTimeout(rewindSafetyTimer);
        rewindSafetyTimer = null;
      }
    };

    const readDuration = () => {
      const d = v.duration;
      return Number.isFinite(d) && d > 0 ? d : 0;
    };

    const applyTrimPlayback = () => {
      const duration = readDuration();
      const b = phoneTrimBounds(markIn, markOut, duration);
      if (!b) return null;
      const trimActive = phoneTrimPlaybackActive(b, duration);
      return { b, duration, trimActive };
    };

    const rewindLoop = (b: { in: number; out: number }, opts?: { resumeAfterSeek?: boolean }) => {
      clearRewindSafety();
      trimLoopRewindPendingRef.current = true;
      const resume = opts?.resumeAfterSeek ?? !v.paused;
      v.currentTime = phoneTrimLoopSeekTarget(b);
      if (resume) void v.play().catch(() => {});
      rewindSafetyTimer = setTimeout(() => {
        trimLoopRewindPendingRef.current = false;
        rewindSafetyTimer = null;
      }, 400);
    };

    const onTimeUpdate = () => {
      if (v.seeking) return;
      if (trimPlaybackLoop && trimLoopRewindPendingRef.current) return;
      const ctx = applyTrimPlayback();
      if (!ctx) return;
      const { b, duration } = ctx;
      const t = v.currentTime;

      /* Small margin avoids fighting float / mux jitter just inside `in`. */
      if (t < b.in - 1e-3) {
        if (trimPlaybackLoop) {
          if (!v.paused) {
            rewindLoop(b);
          } else {
            v.currentTime = b.in;
          }
        } else {
          v.currentTime = b.in;
        }
        return;
      }

      if (!trimPlaybackLoop) {
        const pastOutPlaying =
          !v.paused && (v.ended || t + TRIM_STOP_PLAY_RESUME_NEAR_OUT_SEC >= b.out);
        if (pastOutPlaying) {
          v.pause();
          v.currentTime = Math.max(b.in, Math.min(b.out - 1 / 120, Math.max(0, duration - 1e-6)));
        }
        return;
      }

      const pastOutWhilePlaying =
        !v.paused && t + TRIM_REPEAT_TIMEUPDATE_OUT_EPS_SEC >= b.out;
      if (pastOutWhilePlaying) rewindLoop(b);
    };

    const onEnded = () => {
      if (!trimPlaybackLoop) {
        v.pause();
        return;
      }
      if (v.seeking) return;
      const ctx = applyTrimPlayback();
      if (!ctx) return;
      const { b } = ctx;
      rewindLoop(b, { resumeAfterSeek: true });
    };

    const onPlay = () => {
      const ctx = applyTrimPlayback();
      if (!ctx) return;
      const { b } = ctx;
      const t = v.currentTime;
      if (t < b.in - 1e-4) {
        v.currentTime = trimPlaybackLoop ? phoneTrimLoopSeekTarget(b) : b.in;
        return;
      }
      if (t + TRIM_STOP_PLAY_RESUME_NEAR_OUT_SEC >= b.out) {
        v.currentTime = trimPlaybackLoop ? phoneTrimLoopSeekTarget(b) : b.in;
      }
    };

    /** Only "Once" mode needs seeked: pause after user scrubs past `out` (loop is handled in timeupdate). */
    const onSeeked = () => {
      if (trimPlaybackLoop) {
        clearRewindSafety();
        trimLoopRewindPendingRef.current = false;
        return;
      }
      const ctx = applyTrimPlayback();
      if (!ctx) return;
      const { b, duration } = ctx;
      const t = v.currentTime;
      if (t >= b.out - 1e-3) {
        v.pause();
        v.currentTime = Math.max(b.in, Math.min(b.out - 1 / 120, Math.max(0, duration - 1e-6)));
      }
    };

    v.addEventListener("timeupdate", onTimeUpdate);
    v.addEventListener("ended", onEnded);
    v.addEventListener("play", onPlay);
    v.addEventListener("seeked", onSeeked);
    return () => {
      clearRewindSafety();
      v.removeEventListener("timeupdate", onTimeUpdate);
      v.removeEventListener("ended", onEnded);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("seeked", onSeeked);
    };
  }, [play, markIn, markOut, trimPlaybackLoop, k, index]);

  useEffect(() => {
    if (!scrubSheetOpen || !play) return;
    const v = phoneVideoRef.current;
    if (!v) return;
    const sync = () => setScrubVideoPaused(v.paused);
    sync();
    v.addEventListener("play", sync);
    v.addEventListener("pause", sync);
    return () => {
      v.removeEventListener("play", sync);
      v.removeEventListener("pause", sync);
    };
  }, [scrubSheetOpen, play, k, index]);

  const toggleScrubPlay = useCallback(() => {
    const v = phoneVideoRef.current;
    if (!v) return;
    clearViewerUiTimer();
    if (v.paused) {
      void v.play().catch(() => {});
    } else {
      v.pause();
    }
  }, [clearViewerUiTimer]);

  /** Muted inline + play() after load is required for iOS. */
  useEffect(() => {
    if (!play || !videoAutoplay || !detailsPageActive || scrubSheetOpen) return;
    const v = phoneVideoRef.current;
    if (!v) return;
    v.setAttribute("playsinline", "");
    v.setAttribute("webkit-playsinline", "");
    v.muted = true;
    const tryPlay = () => {
      void v.play().catch(() => {});
    };
    tryPlay();
    if (v.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
      v.addEventListener("loadeddata", tryPlay, { once: true });
      v.addEventListener("canplay", tryPlay, { once: true });
    }
    return () => {
      v.removeEventListener("loadeddata", tryPlay);
      v.removeEventListener("canplay", tryPlay);
    };
  }, [play, videoAutoplay, k, index, detailsPageActive, scrubSheetOpen]);

  const goNext = useCallback(() => {
    onIndexChange(Math.min(index + 1, items.length - 1));
  }, [index, items.length, onIndexChange]);

  const goPrev = useCallback(() => {
    onIndexChange(Math.max(index - 1, 0));
  }, [index, onIndexChange]);

  const swipeTouchStart = useRef<{ y: number; x: number } | null>(null);
  const stageMenuAnchorRef = useRef<{ x: number; y: number } | null>(null);

  const onSwipeTouchStart = useCallback((e: React.TouchEvent) => {
    if (!detailsPageActive || viewerActionMenuOpen || scrubSheetOpen) return;
    if (e.touches.length !== 1) return;
    swipeTouchStart.current = { y: e.touches[0].clientY, x: e.touches[0].clientX };
  }, [detailsPageActive, viewerActionMenuOpen, scrubSheetOpen]);

  const onSwipeTouchEnd = useCallback(
    (e: React.TouchEvent) => {
      if (!detailsPageActive || viewerActionMenuOpen || scrubSheetOpen) return;
      const start = swipeTouchStart.current;
      swipeTouchStart.current = null;
      if (!start || e.changedTouches.length < 1) return;
      const t = e.changedTouches[0];
      const dy = t.clientY - start.y;
      const dx = t.clientX - start.x;
      const absDx = Math.abs(dx);
      const absDy = Math.abs(dy);

      if (absDx < PHONE_SWIPE_MIN_PX) return;
      if (absDx < absDy * 1.15) return;
      if (dx < 0) {
        if (index >= items.length - 1) return;
        skipIndexEffectTransientOnce.current = true;
        suppressStageClickOnce.current = true;
        goNext();
      } else {
        if (index <= 0) return;
        skipIndexEffectTransientOnce.current = true;
        suppressStageClickOnce.current = true;
        goPrev();
      }
    },
    [goNext, goPrev, detailsPageActive, viewerActionMenuOpen, scrubSheetOpen, index, items.length]
  );

  const stageLpTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stageLpDown = useRef<{ x: number; y: number } | null>(null);
  const stageLpSuppressClick = useRef(false);

  const clearStageLp = useCallback(() => {
    if (stageLpTimer.current) {
      clearTimeout(stageLpTimer.current);
      stageLpTimer.current = null;
    }
  }, []);

  const onStagePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (!detailsPageActive || viewerActionMenuOpen || scrubSheetOpen) return;
      if (e.pointerType === "mouse" && e.button !== 0) return;
      stageLpDown.current = { x: e.clientX, y: e.clientY };
      stageMenuAnchorRef.current = { x: e.clientX, y: e.clientY };
      clearStageLp();
      stageLpTimer.current = setTimeout(() => {
        stageLpTimer.current = null;
        stageLpDown.current = null;
        stageLpSuppressClick.current = true;
        const anchor = stageMenuAnchorRef.current;
        stageMenuAnchorRef.current = null;
        const ax = anchor?.x ?? (typeof window !== "undefined" ? window.innerWidth / 2 : 0);
        const ay = anchor?.y ?? (typeof window !== "undefined" ? window.innerHeight / 2 : 0);
        setViewerActionMenuPos({ x: ax, y: ay });
        setViewerActionMenuOpen(true);
      }, PHONE_LONG_PRESS_MS);
    },
    [detailsPageActive, viewerActionMenuOpen, scrubSheetOpen, clearStageLp]
  );

  const onStagePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!detailsPageActive || viewerActionMenuOpen || scrubSheetOpen || !stageLpDown.current || !stageLpTimer.current)
        return;
      const dx = e.clientX - stageLpDown.current.x;
      const dy = e.clientY - stageLpDown.current.y;
      const lim = PHONE_LONG_PRESS_MOVE_CANCEL_PX;
      if (dx * dx + dy * dy > lim * lim) {
        clearStageLp();
        stageLpDown.current = null;
      }
    },
    [detailsPageActive, viewerActionMenuOpen, scrubSheetOpen, clearStageLp]
  );

  const onStagePointerUp = useCallback(
    (e: React.PointerEvent) => {
      if (!detailsPageActive || viewerActionMenuOpen || scrubSheetOpen) return;
      clearStageLp();
      stageLpDown.current = null;
      if (stageLpSuppressClick.current) {
        stageLpSuppressClick.current = false;
        e.preventDefault();
        e.stopPropagation();
      }
    },
    [detailsPageActive, viewerActionMenuOpen, scrubSheetOpen, clearStageLp]
  );

  const onStagePointerCancel = useCallback(() => {
    clearStageLp();
    stageLpDown.current = null;
  }, [clearStageLp]);

  const closeScrubSheet = useCallback(() => {
    setScrubSheetOpen(false);
    scheduleHideViewerChrome();
  }, [scheduleHideViewerChrome]);

  const onStageClick = useCallback(() => {
    if (!detailsPageActive) return;
    if (viewerActionMenuOpen) {
      setViewerActionMenuOpen(false);
      setViewerActionMenuPos(null);
      return;
    }
    if (scrubSheetOpen) {
      closeScrubSheet();
      return;
    }
    if (suppressStageClickOnce.current) {
      suppressStageClickOnce.current = false;
      return;
    }
    if (stageLpSuppressClick.current) {
      stageLpSuppressClick.current = false;
      return;
    }
    showTransientViewerUi();
  }, [detailsPageActive, viewerActionMenuOpen, scrubSheetOpen, closeScrubSheet, showTransientViewerUi]);

  useEffect(() => {
    const sync = () => {
      const fs = discoveryDocumentFullscreenElement();
      const el = overlayRef.current;
      setBrowserViewerFullscreen(!!(el && fs === el));
    };
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
    sync();
    return () => {
      document.removeEventListener("fullscreenchange", sync);
      document.removeEventListener("webkitfullscreenchange", sync);
    };
  }, []);

  useEffect(() => {
    return () => {
      setVisualViewerFullscreen(false);
      const fs = discoveryDocumentFullscreenElement();
      const el = overlayRef.current;
      if (!el || fs !== el) return;
      try {
        void document.exitFullscreen?.();
      } catch {
        /* ignore */
      }
      const d = document as Document & { webkitExitFullscreen?: () => void };
      try {
        d.webkitExitFullscreen?.();
      } catch {
        /* ignore */
      }
    };
  }, []);

  const toggleViewerFullscreen = useCallback(async () => {
    const el = overlayRef.current;
    if (!el) return;

    if (browserViewerFullscreen || visualViewerFullscreen) {
      if (visualViewerFullscreen) {
        setVisualViewerFullscreen(false);
        return;
      }
      const fs = discoveryDocumentFullscreenElement();
      if (fs === el) {
        try {
          await document.exitFullscreen?.();
        } catch {
          /* ignore */
        }
        const d = document as Document & { webkitExitFullscreen?: () => void };
        try {
          d.webkitExitFullscreen?.();
        } catch {
          /* ignore */
        }
      }
      return;
    }

    setVisualViewerFullscreen(false);
    if (discoveryPhoneLikelyIOS()) {
      setVisualViewerFullscreen(true);
      return;
    }
    const anyEl = el as HTMLElement & {
      webkitRequestFullscreen?: () => void | Promise<void>;
      mozRequestFullScreen?: () => void | Promise<void>;
    };
    try {
      if (el.requestFullscreen) {
        await el.requestFullscreen();
        return;
      }
      if (anyEl.webkitRequestFullscreen) {
        await Promise.resolve(anyEl.webkitRequestFullscreen());
        return;
      }
      if (anyEl.mozRequestFullScreen) {
        await Promise.resolve(anyEl.mozRequestFullScreen());
        return;
      }
    } catch {
      /* fall through to visual fallback */
    }
    setVisualViewerFullscreen(true);
  }, [browserViewerFullscreen, visualViewerFullscreen]);

  useLayoutEffect(() => {
    if (!inViewerFullscreen) return;
    const el = overlayRef.current;
    if (!el) return;

    const vv = window.visualViewport;
    const apply = () => {
      if (!vv) {
        el.style.position = "fixed";
        el.style.left = "0";
        el.style.top = "0";
        el.style.width = "100%";
        el.style.height = "100dvh";
        el.style.minHeight = "-webkit-fill-available";
        return;
      }
      el.style.position = "fixed";
      el.style.left = `${vv.offsetLeft}px`;
      el.style.top = `${vv.offsetTop}px`;
      el.style.width = `${vv.width}px`;
      el.style.height = `${vv.height}px`;
      el.style.right = "auto";
      el.style.bottom = "auto";
      el.style.maxWidth = "none";
      el.style.maxHeight = "none";
      el.style.minHeight = "";
    };

    apply();
    if (vv) {
      vv.addEventListener("resize", apply);
      vv.addEventListener("scroll", apply);
    }
    return () => {
      if (vv) {
        vv.removeEventListener("resize", apply);
        vv.removeEventListener("scroll", apply);
      }
      for (const prop of [
        "position",
        "left",
        "top",
        "right",
        "bottom",
        "width",
        "height",
        "max-width",
        "max-height",
        "min-height",
      ]) {
        el.style.removeProperty(prop);
      }
    };
  }, [inViewerFullscreen]);

  useEffect(() => {
    if (!inViewerFullscreen) return;
    const prevHtml = document.documentElement.style.overflow;
    const prevBody = document.body.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    return () => {
      document.documentElement.style.overflow = prevHtml;
      document.body.style.overflow = prevBody;
    };
  }, [inViewerFullscreen]);

  const posterUrl = thumb && thumb !== play ? thumb : undefined;

  const actionMenuPopoverStyle = useMemo((): React.CSSProperties => {
    if (!viewerActionMenuOpen) return {};
    const menuW = 200;
    const menuH = 156;
    const pos = viewerActionMenuPos;
    if (!pos || typeof window === "undefined") {
      return {
        position: "fixed",
        left: "50%",
        top: "max(12px, env(safe-area-inset-top, 0px))",
        transform: "translateX(-50%)",
        width: menuW,
        minHeight: menuH,
      };
    }
    return {
      position: "fixed",
      left: Math.min(window.innerWidth - menuW - 8, Math.max(8, pos.x - menuW / 2)),
      top: Math.min(window.innerHeight - menuH - 8, Math.max(8, pos.y - menuH - 10)),
      width: menuW,
      minHeight: menuH,
    };
  }, [viewerActionMenuOpen, viewerActionMenuPos]);

  const stageClass =
    "discovery-phone-detail-stage discovery-phone-detail-stage--viewer discovery-phone-detail-stage--immersive" +
    (play && showVideoControls ? " discovery-phone-detail-stage--video-controls" : "");

  return (
    <div
      ref={overlayRef}
      className={
        "discovery-phone-detail-overlay" +
        (inViewerFullscreen ? " discovery-phone-detail-overlay--viewer-fs-lock" : "")
      }
      role="dialog"
      aria-modal="true"
      aria-label={it.name}
    >
      <div ref={stackRef} className="discovery-phone-stack">
        <section
          id="discovery-phone-page-details"
          className="discovery-phone-stack__page discovery-phone-stack__page--details"
          aria-label="Details"
        >
          <div className="discovery-phone-stack__details-video">
            <div className={stageClass} onContextMenu={(e) => e.preventDefault()}>
              <div
                className="discovery-phone-detail-stage-inner"
                onClick={onStageClick}
                onPointerDown={onStagePointerDown}
                onPointerMove={onStagePointerMove}
                onPointerUp={onStagePointerUp}
                onPointerCancel={onStagePointerCancel}
                onTouchStart={onSwipeTouchStart}
                onTouchEnd={onSwipeTouchEnd}
              >
                {play ? (
                  <video
                    ref={phoneVideoRef}
                    key={k}
                    src={play}
                    onPlay={() => onVisitVideoPlay(it)}
                    controls={showVideoControls && !scrubSheetOpen}
                    controlsList="nofullscreen"
                    playsInline
                    poster={posterUrl}
                    preload={videoAutoplay ? "auto" : "metadata"}
                    loop={videoAutoplay && !trimEnforcesPlayback && trimPlaybackLoop}
                    muted={videoAutoplay}
                    autoPlay={videoAutoplay}
                  />
                ) : thumb ? (
                  <img key={k} src={thumb} alt="" decoding="async" />
                ) : (
                  <div style={{ color: "var(--muted)", padding: 24, textAlign: "center" }}>No preview for this type</div>
                )}
              </div>
            </div>

            {viewerChromeVisible && detailsPageActive && !hideHudChromeForNativeVideo ? (
              <div className="discovery-phone-viewer-chrome">
                <div className="discovery-phone-viewer-chrome-filename" title={it.name}>
                  <span className="mono discovery-phone-viewer-chrome-filename-text">{it.name}</span>
                </div>
                <div className="discovery-phone-viewer-chrome-top">
                  <button type="button" className="discovery-phone-viewer-chrome-btn" onClick={onClose} aria-label="Return to list">
                    ← List
                  </button>
                  <span className="mono discovery-phone-viewer-chrome-counter">
                    {index + 1} / {items.length}
                  </span>
                  <div className="discovery-phone-viewer-chrome-top-actions">
                    <button
                      type="button"
                      className="discovery-phone-viewer-chrome-btn"
                      onClick={() => detailsMetaScrollRef.current?.scrollTo({ top: 0, behavior: "smooth" })}
                      aria-label="Scroll to item fields"
                    >
                      Meta
                    </button>
                    <button
                      type="button"
                      className="discovery-phone-viewer-chrome-btn"
                      onClick={() => void toggleViewerFullscreen()}
                      aria-label={inViewerFullscreen ? "Exit fullscreen" : "Fullscreen viewer"}
                    >
                      {inViewerFullscreen ? "Exit FS" : "Full"}
                    </button>
                    <label
                      className="discovery-phone-viewer-chrome-btn"
                      style={{ display: "inline-flex", alignItems: "center", gap: 4, cursor: "pointer" }}
                      title="Exemplar library (server). Uncheck to remove."
                    >
                      <input
                        type="checkbox"
                        checked={exemplarHasThis}
                        aria-label="Exemplar library"
                        onChange={(e) => {
                          const next = e.target.checked;
                          const key = discoveryItemKey(it);
                          onExemplarPatch((d) =>
                            next ? discoveryAppendExemplarLibraryKey(d, key, it) : discoveryRemoveExemplarLibraryKey(d, key),
                          );
                        }}
                      />
                      <span>Exp</span>
                    </label>
                  </div>
                </div>
                <PhoneAutoplayToggle variant="overlay" videoAutoplay={videoAutoplay} onVideoAutoplayChange={onVideoAutoplayChange} />
                <div className="discovery-phone-viewer-chrome-nav">
                  <button type="button" className="discovery-phone-detail-nav-btn" disabled={index <= 0} onClick={goPrev} aria-label="Previous item">
                    ← Prev
                  </button>
                  <button
                    type="button"
                    className="discovery-phone-detail-nav-btn"
                    disabled={index >= items.length - 1}
                    onClick={goNext}
                    aria-label="Next item"
                  >
                    Next →
                  </button>
                </div>
                <p className="discovery-phone-viewer-chrome-hint">
                  Swipe video for next / prev · scroll vertically for Assets, Parameters, and Workflows · long-press for actions
                  · Full uses the visible viewport on phones (Safari bars may still show)
                </p>
              </div>
            ) : null}
          </div>
          <div ref={detailsMetaScrollRef} className="discovery-phone-stack__details-meta">
            <DiscoveryItemMetaBody
              it={it}
              k={k}
              saved={saved}
              onToggleSaved={onToggleSaved}
              exemplarInLibrary={exemplarHasThis}
              onExemplarInLibraryChange={(next) => {
                const key = discoveryItemKey(it);
                onExemplarPatch((d) =>
                  next ? discoveryAppendExemplarLibraryKey(d, key, it) : discoveryRemoveExemplarLibraryKey(d, key),
                );
              }}
            />
          </div>
        </section>

        <section
          id="discovery-phone-page-assets"
          className="discovery-phone-stack__page discovery-phone-stack__page--panel"
          aria-label="Assets mock"
        >
          <div className="discovery-phone-stack__page-head">
            <span>Assets</span>
            <span className="discovery-mock-tab-hint">Mock</span>
          </div>
          <div className="discovery-phone-stack__page-body">
            <DiscoveryMockAssetsPanel it={it} saved={saved} onToggleSaved={onToggleSaved} />
          </div>
        </section>

        <section id="discovery-phone-page-parameters" className="discovery-phone-stack__page discovery-phone-stack__page--panel" aria-label="Parameters">
          <div className="discovery-phone-stack__page-head">
            <span>Parameters</span>
          </div>
          <div className="discovery-phone-stack__page-body">
            <DiscoveryComfyQueuePanel it={it} />
          </div>
        </section>

        <section
          id="discovery-phone-page-workflows"
          className="discovery-phone-stack__page discovery-phone-stack__page--panel"
          aria-label="Workflows"
        >
          <div className="discovery-phone-stack__page-head">
            <span>Workflows</span>
          </div>
          <div className="discovery-phone-stack__page-body">
            <DiscoveryWorkflowsPanel
              it={it}
              libraryItems={items}
              onSelectItem={(peer) => {
                const idx = items.findIndex((x) => discoveryItemKey(x) === discoveryItemKey(peer));
                if (idx >= 0) {
                  onVisitImage(peer);
                  onIndexChange(idx);
                }
              }}
              onOpenParameters={() => scrollStackTo("parameters")}
              itemByKey={itemByKey}
              exemplarSets={exemplarSets}
              onExemplarPatch={onExemplarPatch}
              exemplarReady={exemplarReady}
              exemplarLoadError={exemplarLoadError}
              exemplarSaveError={exemplarSaveError}
            />
          </div>
        </section>
      </div>

      <nav className="discovery-phone-stack-nav" aria-label="Discovery panels">
        <button
          type="button"
          className={stackActive === "details" ? "discovery-phone-stack-nav--active" : undefined}
          onClick={() => scrollStackTo("details")}
        >
          Details
        </button>
        <button
          type="button"
          className={stackActive === "assets" ? "discovery-phone-stack-nav--active" : undefined}
          onClick={() => scrollStackTo("assets")}
        >
          Assets
        </button>
        <button
          type="button"
          className={stackActive === "parameters" ? "discovery-phone-stack-nav--active" : undefined}
          onClick={() => scrollStackTo("parameters")}
        >
          Parameters
        </button>
        <button
          type="button"
          className={stackActive === "workflows" ? "discovery-phone-stack-nav--active" : undefined}
          onClick={() => scrollStackTo("workflows")}
        >
          Workflows
        </button>
      </nav>

      {viewerActionMenuOpen ? (
        <div className="discovery-phone-viewer-action-layer" role="presentation">
          <button
            type="button"
            className="discovery-phone-viewer-action-backdrop"
            aria-label="Dismiss menu"
            onClick={() => {
              setViewerActionMenuOpen(false);
              setViewerActionMenuPos(null);
            }}
          />
          <div className="discovery-phone-viewer-action-popover" role="menu" style={actionMenuPopoverStyle}>
            <button
              type="button"
              className="discovery-phone-viewer-action-item"
              role="menuitem"
              onClick={() => {
                setViewerActionMenuOpen(false);
                setViewerActionMenuPos(null);
                scrollStackTo("details");
              }}
            >
              Details…
            </button>
            <button
              type="button"
              className="discovery-phone-viewer-action-item"
              role="menuitem"
              disabled={!play}
              title={!play ? "Video items only" : undefined}
              onClick={() => {
                if (!play) return;
                setViewerActionMenuOpen(false);
                setViewerActionMenuPos(null);
                setHideHudChromeForNativeVideo(false);
                setShowVideoControls(true);
                clearViewerUiTimer();
                setScrubSheetOpen(true);
              }}
            >
              Trim…
            </button>
            <button
              type="button"
              className="discovery-phone-viewer-action-cancel"
              onClick={() => {
                setViewerActionMenuOpen(false);
                setViewerActionMenuPos(null);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {scrubSheetOpen ? (
        <div className="discovery-phone-scrub-layer" role="presentation">
          <div className="discovery-phone-scrub-sheet" role="dialog" aria-modal="true" aria-label="Trim" onClick={(e) => e.stopPropagation()}>
            <div className="discovery-phone-scrub-sheet-head discovery-phone-scrub-sheet-head--compact">
              <div className="discovery-trim-head-main">
                {play ? (
                  <div className="discovery-trim-primary-row discovery-trim-primary-row--sheet">
                    <div className="discovery-trim-primary-row__time mono">
                      <span className="discovery-trim-time-readout discovery-trim-time-readout--sheet">
                        {fmtVideoSec(scrubUiPos)}{" "}
                        <span className="discovery-trim-range-readout-sep">/</span>{" "}
                        {fmtVideoSec(phoneVideoDuration || scrubUiDuration)}
                      </span>
                    </div>
                    <div className="discovery-trim-primary-row__center">
                      <DiscoveryTrimTransport
                        videoRef={phoneVideoRef}
                        duration={phoneVideoDuration || scrubUiDuration}
                        markIn={markIn}
                        markOut={markOut}
                        mediaSyncKey={`${k}-${index}`}
                        size="large"
                        onSyncTime={(t) => {
                          clearViewerUiTimer();
                          setScrubUiPos(t);
                        }}
                        pausedExternal={scrubVideoPaused}
                        onTogglePlayExternal={toggleScrubPlay}
                      />
                    </div>
                    <div className="discovery-trim-primary-row__io">
                      <TrimInOutAtPlayheadButtons
                        duration={phoneVideoDuration || scrubUiDuration}
                        markIn={markIn}
                        markOut={markOut}
                        setMarkIn={setMarkIn}
                        setMarkOut={setMarkOut}
                        getVideo={() => phoneVideoRef.current}
                        playheadSec={scrubUiPos}
                        paused={scrubVideoPaused}
                        onAfterMarkEdit={clearViewerUiTimer}
                      />
                    </div>
                  </div>
                ) : (
                  <span className="mono discovery-phone-scrub-compact-time discovery-phone-scrub-compact-time--solo">—</span>
                )}
              </div>
              <button type="button" className="discovery-phone-scrub-close" onClick={closeScrubSheet} aria-label="Close">
                ×
              </button>
            </div>
            <div className="discovery-phone-scrub-sheet-body discovery-phone-scrub-sheet-body--compact">
              {!play ? (
                <p className="discovery-phone-scrub-footnote" style={{ margin: 0 }}>
                  Video items only.
                </p>
              ) : (
                <>
                  <div className="discovery-trim-timeline-row">
                    <div className="discovery-trim-timeline-row__track">
                      <PhoneTrimTimeline
                        duration={phoneVideoDuration || scrubUiDuration}
                        currentTime={scrubUiPos}
                        markIn={markIn}
                        markOut={markOut}
                        disabled={(phoneVideoDuration || scrubUiDuration) <= 0}
                        onSeek={(t) => {
                          clearViewerUiTimer();
                          const v = phoneVideoRef.current;
                          if (!v) return;
                          v.currentTime = t;
                          setScrubUiPos(t);
                        }}
                        onMarkInChange={(t) => {
                          clearViewerUiTimer();
                          setMarkIn(t);
                        }}
                        onMarkOutChange={(t) => {
                          clearViewerUiTimer();
                          setMarkOut(t);
                        }}
                      />
                    </div>
                    <div className="discovery-trim-timeline-row__actions" role="group" aria-label="Trim range options">
                      <TrimClearInOutButton
                        onClick={() => {
                          clearViewerUiTimer();
                          setMarkIn(null);
                          setMarkOut(null);
                        }}
                        disabled={!phoneScrubSheetTrimNarrows}
                      />
                      <TrimPlaybackOutIconToggle
                        mode={trimPlaybackLoop ? "repeat" : "stop_at_end"}
                        onModeChange={(m) => setTrimPlaybackLoop(m === "repeat")}
                      />
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {filenameLinePhase !== "hidden" && detailsPageActive && !viewerChromeVisible ? (
        <div
          className={
            "discovery-phone-filename-status" +
            (filenameLinePhase === "fade" ? " discovery-phone-filename-status--fade" : "")
          }
          aria-hidden="true"
        >
          <span className="mono discovery-phone-filename-status-text">{it.name}</span>
        </div>
      ) : null}
    </div>
  );
}
