import React, { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import { fetchDiscoveryEmbedApiPrompt, fetchDiscoveryLibrary, submitPromptToQueue } from "./api";
import {
  discoveryTrimMediaRelpath,
  loadDiscoveryTrimAsync,
  persistDiscoveryTrimAsync,
  TRIM_CONTEXT_DISCOVERY_PLAYER,
} from "./discoveryTrimStorage";
import type { DiscoveryLibraryItem, DiscoveryLibraryResponse, DiscoveryMember } from "./types";
import {
  phoneTrimBounds,
  phoneTrimLoopSeekTarget,
  phoneTrimPlaybackActive,
  TRIM_HANDLE_MIN_GAP_SEC,
} from "./phoneTrimModel";
import { DeviceProvider, useDeviceContext } from "./viewport";

const SAVED_KEY = "discovery_library_saved_v1";
const VIDEO_AUTOPLAY_KEY = "discovery_phone_video_autoplay";
const DESKTOP_LIST_WIDTH_KEY = "discovery_desktop_list_width_v1";
const DESKTOP_LIST_WIDTH_DEFAULT = 400;
const DESKTOP_LIST_MIN = 260;
const DESKTOP_PREVIEW_MIN = 280;

const DESKTOP_META_DRAWER_WIDTH_KEY = "discovery_desktop_meta_drawer_width_v1";
const DESKTOP_META_DRAWER_WIDTH_DEFAULT = 360;
const DESKTOP_META_DRAWER_MIN = 220;
const DESKTOP_META_DRAWER_MAX = 640;

/** Initial open state for the desktop discovery details drawer. Toggle placement / persisted pref TBD. */
const DESKTOP_DETAILS_DRAWER_DEFAULT_OPEN = false;

const DISCOVERY_GRAPH_DRAFT_PREFIX = "discovery_comfy_graph_draft__";
const DISCOVERY_COMFY_FRONT_KEY = "discovery_comfy_front_v1";

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

type DiscoveryMetaDrawerTab = "details" | "comfy";

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

function loadDesktopMetaDrawerWidth(): number {
  try {
    const raw = localStorage.getItem(DESKTOP_META_DRAWER_WIDTH_KEY);
    if (!raw) return DESKTOP_META_DRAWER_WIDTH_DEFAULT;
    const n = Number(raw);
    if (!Number.isFinite(n)) return DESKTOP_META_DRAWER_WIDTH_DEFAULT;
    return Math.max(DESKTOP_META_DRAWER_MIN, Math.min(DESKTOP_META_DRAWER_MAX, Math.round(n)));
  } catch {
    return DESKTOP_META_DRAWER_WIDTH_DEFAULT;
  }
}

function persistDesktopMetaDrawerWidth(px: number) {
  try {
    localStorage.setItem(DESKTOP_META_DRAWER_WIDTH_KEY, String(px));
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

function discoveryItemKey(it: DiscoveryLibraryItem): string {
  return it.group_id || it.relpath;
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

function DiscoveryItemMetaBody({
  it,
  k,
  saved,
  onToggleSaved,
}: {
  it: DiscoveryLibraryItem;
  k: string;
  saved: Set<string>;
  onToggleSaved: (key: string) => void;
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
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onActivate();
        }
      }}
    >
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

function DiscoveryComfyQueuePanel({ it }: { it: DiscoveryLibraryItem }) {
  const itemKey = discoveryItemKey(it);
  const draftKey = discoveryDraftStorageKey(itemKey);
  const [graphJson, setGraphJson] = useState("");
  const [frontOfQueue, setFrontOfQueue] = useState(() => _discoverySessionGetBool01(DISCOVERY_COMFY_FRONT_KEY));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resultLine, setResultLine] = useState<string | null>(null);
  const [embedLoading, setEmbedLoading] = useState(true);
  const [embedMeta, setEmbedMeta] = useState<string | null>(null);

  const loadEmbedFromServer = useCallback(async () => {
    setEmbedLoading(true);
    setGraphJson("");
    setEmbedMeta(null);
    setError(null);
    setResultLine(null);
    try {
      const j = await fetchDiscoveryEmbedApiPrompt(it);
      if (j.ok) {
        setGraphJson(JSON.stringify(j.prompt, null, 2));
        setEmbedMeta(`${j.source} · ${j.png_relpath}`);
        return;
      }
      const detail = [j.detail, j.hint].filter(Boolean).join(" ");
      const fallback = _discoverySessionGet(draftKey, "");
      if (fallback.trim()) {
        setGraphJson(fallback);
        setEmbedMeta(`Saved draft · ${j.error}${detail ? ` — ${detail}` : ""}`);
        setError(null);
      } else {
        setGraphJson("");
        setEmbedMeta(null);
        setError(detail || j.error || "Could not load embedded workflow.");
      }
    } catch (e) {
      const fallback = _discoverySessionGet(draftKey, "");
      if (fallback.trim()) {
        setGraphJson(fallback);
        setEmbedMeta(`Saved draft · ${e instanceof Error ? e.message : String(e)}`);
        setError(null);
      } else {
        setGraphJson("");
        setEmbedMeta(null);
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setEmbedLoading(false);
    }
  }, [it, draftKey]);

  useEffect(() => {
    void loadEmbedFromServer();
  }, [loadEmbedFromServer]);

  useEffect(() => {
    if (embedLoading) return;
    try {
      sessionStorage.setItem(draftKey, graphJson);
    } catch {
      /* ignore */
    }
  }, [draftKey, graphJson, embedLoading]);

  useEffect(() => {
    try {
      sessionStorage.setItem(DISCOVERY_COMFY_FRONT_KEY, frontOfQueue ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [frontOfQueue]);

  const onSend = useCallback(async () => {
    setError(null);
    setResultLine(null);
    const trimmed = graphJson.trim();
    if (!trimmed) {
      setError("Paste a workflow JSON object first.");
      return;
    }
    let prompt: Record<string, unknown>;
    try {
      const v = JSON.parse(trimmed) as unknown;
      if (typeof v !== "object" || v === null || Array.isArray(v)) {
        setError("The workflow must be a JSON object (Comfy API “prompt”, keyed by node id).");
        return;
      }
      prompt = v as Record<string, unknown>;
    } catch {
      setError("Could not parse JSON. Check brackets and commas.");
      return;
    }
    setBusy(true);
    try {
      const res = await submitPromptToQueue({
        prompt,
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
    }
  }, [graphJson, frontOfQueue]);

  const ctxTitle = it.video_relpath ?? it.relpath;

  return (
    <div className="discovery-comfy-queue-panel">
      <p className="discovery-comfy-queue-lead">
        Workflow is loaded from the output PNG metadata when available. If the file only has the UI workflow (nodes
        + links), the server asks Comfy at <span className="mono">POST /workflow/convert</span> (e.g. workflow-to-api
        converter custom node) to produce API <strong>prompt</strong> JSON. You can still edit before sending.
      </p>
      <div className="discovery-comfy-queue-context mono" title={ctxTitle}>
        <span className="discovery-comfy-queue-context-label">Now viewing</span> {it.name}
      </div>
      {embedMeta ? <p className="discovery-comfy-queue-embedmeta">{embedMeta}</p> : null}
      {embedLoading ? <p className="discovery-comfy-queue-embedloading">Loading embedded workflow…</p> : null}
      <label className="discovery-comfy-queue-check">
        <input type="checkbox" checked={frontOfQueue} onChange={(e) => setFrontOfQueue(e.target.checked)} />
        Send to front of queue
      </label>
      <label className="discovery-comfy-queue-json-label" htmlFor="discovery-comfy-graph-json">
        API prompt (editable)
      </label>
      <textarea
        id="discovery-comfy-graph-json"
        className="discovery-comfy-queue-textarea mono"
        spellCheck={false}
        autoComplete="off"
        value={graphJson}
        onChange={(e) => setGraphJson(e.target.value)}
        placeholder={`{\n  "3": { "class_type": "...", "inputs": { }\n}`}
      />
      <div className="discovery-comfy-queue-actions">
        <button
          type="button"
          className="discovery-comfy-queue-reload"
          disabled={busy || embedLoading}
          onClick={() => void loadEmbedFromServer()}
        >
          Reload from file
        </button>
        <button type="button" className="discovery-comfy-queue-send" disabled={busy || embedLoading} onClick={() => void onSend()}>
          {busy ? "Sending…" : "Send to Comfy"}
        </button>
      </div>
      {error ? <p className="discovery-comfy-queue-msg discovery-comfy-queue-msg--error">{error}</p> : null}
      {resultLine ? <p className="discovery-comfy-queue-msg discovery-comfy-queue-msg--ok">{resultLine}</p> : null}
    </div>
  );
}

function DiscoveryDesktopPreview({
  it,
  saved,
  onToggleSaved,
  videoAutoplay,
  previewVideoRef,
  trimSeekBoundsRef,
  trimKeyboardRef,
}: {
  it: DiscoveryLibraryItem | null;
  saved: Set<string>;
  onToggleSaved: (key: string) => void;
  videoAutoplay: boolean;
  previewVideoRef: React.MutableRefObject<HTMLVideoElement | null>;
  trimSeekBoundsRef: DiscoveryDesktopTrimSeekRef;
  trimKeyboardRef: React.MutableRefObject<DiscoveryTrimKeyboardApi | null>;
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
  const [detailsOpen, setDetailsOpen] = useState(DESKTOP_DETAILS_DRAWER_DEFAULT_OPEN);
  const [metaDrawerTab, setMetaDrawerTab] = useState<DiscoveryMetaDrawerTab>("details");
  const [drawerWidth, setDrawerWidth] = useState<number>(() => loadDesktopMetaDrawerWidth());
  const drawerWidthRef = useRef(drawerWidth);
  drawerWidthRef.current = drawerWidth;

  const clampDrawerWidthPx = useCallback((w: number, containerW: number) => {
    const cap = Math.min(
      DESKTOP_META_DRAWER_MAX,
      Math.max(DESKTOP_META_DRAWER_MIN, containerW - 96)
    );
    return Math.max(DESKTOP_META_DRAWER_MIN, Math.min(cap, Math.round(w)));
  }, []);

  useEffect(() => {
    const root = previewRootRef.current;
    if (!root || typeof ResizeObserver === "undefined") return;
    const obs = new ResizeObserver(() => {
      const cw = root.clientWidth;
      if (cw < 40) return;
      setDrawerWidth((dw) => {
        const next = clampDrawerWidthPx(dw, cw);
        if (next !== dw) persistDesktopMetaDrawerWidth(next);
        drawerWidthRef.current = next;
        return next;
      });
    });
    obs.observe(root);
    return () => obs.disconnect();
  }, [clampDrawerWidthPx]);

  useEffect(() => {
    if (!detailsOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setDetailsOpen(false);
      e.preventDefault();
      e.stopPropagation();
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [detailsOpen]);

  const onMetaDrawerResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      let ended = false;
      const root = previewRootRef.current;
      const startW = drawerWidthRef.current;
      const startX = e.clientX;
      const onMove = (ev: MouseEvent) => {
        if (ended) return;
        if ((ev.buttons & 1) === 0) {
          cleanup();
          return;
        }
        const cw = root?.clientWidth ?? 800;
        const delta = startX - ev.clientX;
        const next = clampDrawerWidthPx(startW + delta, cw);
        drawerWidthRef.current = next;
        setDrawerWidth(next);
      };
      const cleanup = () => {
        if (ended) return;
        ended = true;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", cleanup, true);
        window.removeEventListener("blur", cleanup);
        document.removeEventListener("visibilitychange", onVis);
        root?.removeEventListener("mouseenter", onEnter);
        persistDesktopMetaDrawerWidth(drawerWidthRef.current);
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
      root?.addEventListener("mouseenter", onEnter);
    },
    [clampDrawerWidthPx]
  );

  useEffect(() => {
    if (!playUrl) previewVideoRef.current = null;
  }, [playUrl, previewVideoRef]);

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

  if (!it) {
    return (
      <div className="discovery-desktop-preview discovery-desktop-preview--empty">
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 14 }}>Select an item from the list.</p>
      </div>
    );
  }

  return (
    <div ref={previewRootRef} className="discovery-desktop-preview">
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

      <aside
        className={"discovery-desktop-meta-drawer" + (detailsOpen ? " discovery-desktop-meta-drawer--open" : "")}
        style={{ width: drawerWidth }}
        aria-hidden={!detailsOpen}
      >
        <div
          className="discovery-desktop-meta-resize-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize side panel"
          tabIndex={detailsOpen ? 0 : -1}
          onMouseDown={detailsOpen ? onMetaDrawerResizeStart : undefined}
          onKeyDown={
            detailsOpen
              ? (e) => {
                  const root = previewRootRef.current;
                  const cw = root?.clientWidth ?? 800;
                  if (e.key === "ArrowLeft") {
                    e.preventDefault();
                    setDrawerWidth((w) => {
                      const next = clampDrawerWidthPx(w + 12, cw);
                      drawerWidthRef.current = next;
                      persistDesktopMetaDrawerWidth(next);
                      return next;
                    });
                  } else if (e.key === "ArrowRight") {
                    e.preventDefault();
                    setDrawerWidth((w) => {
                      const next = clampDrawerWidthPx(w - 12, cw);
                      drawerWidthRef.current = next;
                      persistDesktopMetaDrawerWidth(next);
                      return next;
                    });
                  }
                }
              : undefined
          }
        />
        <div className="discovery-desktop-meta-drawer-column">
          <div className="discovery-desktop-meta-drawer-head">
            <div className="discovery-desktop-meta-drawer-tablist" role="tablist" aria-label="Side panel">
              <button
                type="button"
                role="tab"
                id="discovery-meta-tab-details"
                aria-controls="discovery-meta-panel-body"
                aria-selected={metaDrawerTab === "details"}
                tabIndex={detailsOpen ? (metaDrawerTab === "details" ? 0 : -1) : -1}
                className={
                  "discovery-desktop-meta-drawer-tab" +
                  (metaDrawerTab === "details" ? " discovery-desktop-meta-drawer-tab--active" : "")
                }
                onClick={() => setMetaDrawerTab("details")}
              >
                Details
              </button>
              <button
                type="button"
                role="tab"
                id="discovery-meta-tab-comfy"
                aria-controls="discovery-meta-panel-body"
                aria-selected={metaDrawerTab === "comfy"}
                tabIndex={detailsOpen ? (metaDrawerTab === "comfy" ? 0 : -1) : -1}
                className={
                  "discovery-desktop-meta-drawer-tab" +
                  (metaDrawerTab === "comfy" ? " discovery-desktop-meta-drawer-tab--active" : "")
                }
                onClick={() => setMetaDrawerTab("comfy")}
              >
                Comfy
              </button>
            </div>
            <button
              type="button"
              className="discovery-desktop-meta-drawer-close"
              aria-label="Close side panel"
              onClick={() => setDetailsOpen(false)}
            >
              ×
            </button>
          </div>
          <div
            className="discovery-desktop-preview-meta"
            role="tabpanel"
            id="discovery-meta-panel-body"
            aria-labelledby={metaDrawerTab === "details" ? "discovery-meta-tab-details" : "discovery-meta-tab-comfy"}
          >
            {metaDrawerTab === "details" ? (
              <DiscoveryItemMetaBody it={it} k={k} saved={saved} onToggleSaved={onToggleSaved} />
            ) : (
              <DiscoveryComfyQueuePanel it={it} />
            )}
          </div>
        </div>
      </aside>

      {!detailsOpen ? (
        <div className="discovery-desktop-drawer-tab-stack" aria-label="Open side panel">
          <button
            type="button"
            className="discovery-desktop-drawer-tab"
            onClick={() => {
              setMetaDrawerTab("details");
              setDetailsOpen(true);
            }}
            aria-label="Open details"
          >
            Details
          </button>
          <button
            type="button"
            className="discovery-desktop-drawer-tab"
            onClick={() => {
              setMetaDrawerTab("comfy");
              setDetailsOpen(true);
            }}
            aria-label="Open Comfy queue"
          >
            Comfy
          </button>
        </div>
      ) : null}
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
  const [qInput, setQInput] = useState("");
  const [qApplied, setQApplied] = useState("");
  const [sinceDays, setSinceDays] = useState(0);
  const [library, setLibrary] = useState<"all" | "og" | "wip">("all");
  const [savedOnly, setSavedOnly] = useState(false);
  const [data, setData] = useState<DiscoveryLibraryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
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
  /** Phone: highlighted list row (kept after closing viewer). */
  const [phoneFocusIndex, setPhoneFocusIndex] = useState<number | null>(null);
  /** Phone: detail overlay open (list highlight can remain when false). */
  const [phoneViewerOpen, setPhoneViewerOpen] = useState(false);
  const phoneListScrollRef = useRef<HTMLDivElement | null>(null);
  const [videoAutoplay, setVideoAutoplay] = useState<boolean>(() => loadVideoAutoplay());

  const setVideoAutoplayFromUser = useCallback((on: boolean) => {
    setVideoAutoplay(on);
    persistVideoAutoplay(on);
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setQApplied(qInput.trim()), 400);
    return () => clearTimeout(t);
  }, [qInput]);

  const load = useCallback(
    async (refresh: boolean) => {
      setLoading(true);
      setErr("");
      try {
        const res = await fetchDiscoveryLibrary({
          refresh,
          q: qApplied || undefined,
          since_days: sinceDays > 0 ? sinceDays : undefined,
          library,
          limit: 1200,
        });
        setData(res);
      } catch (e) {
        setErr(String(e));
      } finally {
        setLoading(false);
      }
    },
    [qApplied, sinceDays, library]
  );

  useEffect(() => {
    void load(false);
  }, [load]);

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
  const displayed = useMemo(() => {
    if (!savedOnly) return items;
    return items.filter((it) => saved.has(discoveryItemKey(it)));
  }, [items, savedOnly, saved]);

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
      if (t.closest(".discovery-desktop-meta-resize-handle")) return;
      if (t.closest(".discovery-desktop-meta-drawer")) return;
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
      <button type="button" onClick={() => void load(false)} disabled={loading}>
        Refresh
      </button>
      <button type="button" onClick={() => void load(true)} disabled={loading}>
        Rebuild index
      </button>
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
                {" · "}
                <span style={{ color: "var(--text)" }}>
                  Tap a row to open the viewer · after {(PHONE_VIEWER_CONTROLS_MS / 1000).toFixed(1)}s only the video
                  shows · tap for HUD · long-press on video for actions (details, trim)
                </span>
              </>
            ) : null}
          </div>

          <div ref={phoneListScrollRef} className="discovery-list-scroll">
            {displayed.map((it, idx) => (
              <DiscoveryListThumbRow
                key={discoveryItemKey(it)}
                it={it}
                saved={saved.has(discoveryItemKey(it))}
                onToggleSaved={() => toggleSaved(discoveryItemKey(it))}
                onActivate={() => {
                  setPhoneFocusIndex(idx);
                  setPhoneViewerOpen(true);
                }}
                selected={phoneFocusIndex === idx}
                listRowId={`discovery-phone-list-row-${idx}`}
              />
            ))}
            {!loading && displayed.length === 0 ? (
              <div style={{ padding: 16, color: "var(--muted)" }}>No items. Open filters → Rebuild index.</div>
            ) : null}
          </div>
        </div>

        {phoneViewerOpen && phoneFocusIndex !== null && displayed[phoneFocusIndex] ? (
          <DiscoveryPhoneDetailOverlay
            items={displayed}
            index={phoneFocusIndex}
            onClose={() => setPhoneViewerOpen(false)}
            onIndexChange={setPhoneFocusIndex}
            saved={saved}
            onToggleSaved={toggleSaved}
            videoAutoplay={videoAutoplay}
            onVideoAutoplayChange={setVideoAutoplayFromUser}
          />
        ) : null}
      </div>
    );
  }

  /* Desktop + tablet: list + resizable preview */
  const desktopSelectedItem =
    desktopSelectedKey == null ? null : displayed.find((it) => discoveryItemKey(it) === desktopSelectedKey) ?? null;

  return (
    <div className="discovery-screen">
      <div className="panel discovery-panel discovery-desktop-root" style={{ gap: 10 }}>
        <header style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, flexShrink: 0 }}>
          <a href="/" style={{ fontWeight: 600 }}>
            ← Experiments
          </a>
          <h1 className="title" style={{ margin: 0, fontSize: "1.15rem" }}>
            Og / Wip library
          </h1>
          <span className="discovery-subtitle" style={{ color: "var(--muted)", fontSize: 13 }}>
            Indexed discovery (persistent scan)
          </span>
        </header>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", flexShrink: 0 }}>{filtersBlock}</div>

        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontSize: 14,
            flexShrink: 0,
            userSelect: "none",
          }}
        >
          <input type="checkbox" checked={videoAutoplay} onChange={(e) => setVideoAutoplayFromUser(e.target.checked)} />
          <span>Autoplay video in preview (muted; same setting as phone viewer)</span>
        </label>

        {err ? (
          <div style={{ color: "var(--bad)", fontSize: 14, flexShrink: 0 }} role="alert">
            {err}
          </div>
        ) : null}

        <div style={{ fontSize: 13, color: "var(--muted)", flexShrink: 0 }}>
          {loading ? (
            "Loading…"
          ) : data ? (
            <>
              Index <span className="mono">{data.updated_at ?? "—"}</span>
              {data.from_cache ? " (cached)" : " (just scanned)"}
              {data.scan_ms != null ? ` · ${data.scan_ms} ms scan` : ""}
              {" · "}
              <span className="mono">{data.item_count_filtered}</span> matches
              {data.truncated ? " (truncated)" : ""}
            </>
          ) : null}
        </div>

        <div ref={desktopSplitRef} className="discovery-desktop-split">
          <div className="discovery-desktop-list-pane" style={{ flex: `0 0 ${listPaneWidth}px` }}>
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
                  onToggleSaved={() => toggleSaved(discoveryItemKey(it))}
                  onActivate={() => {
                    setDesktopSelectedKey(discoveryItemKey(it));
                    desktopListScrollRef.current?.focus();
                  }}
                  selected={desktopSelectedKey === discoveryItemKey(it)}
                  listRowId={`discovery-desktop-row-${idx}`}
                  desktopListboxChild
                />
              ))}
              {!loading && displayed.length === 0 ? (
                <div style={{ padding: 16, color: "var(--muted)" }}>No items. Try Rebuild index or relax filters.</div>
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
            videoAutoplay={videoAutoplay}
            previewVideoRef={desktopPreviewVideoRef}
            trimSeekBoundsRef={desktopTrimSeekRef}
            trimKeyboardRef={desktopTrimKeyboardRef}
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
  saved: Set<string>;
  onToggleSaved: (relpath: string) => void;
  videoAutoplay: boolean;
  onVideoAutoplayChange: (on: boolean) => void;
};

function DiscoveryPhoneDetailOverlay({
  items,
  index,
  onClose,
  onIndexChange,
  saved,
  onToggleSaved,
  videoAutoplay,
  onVideoAutoplayChange,
}: PhoneDetailProps) {
  const it = items[index];
  const play = discoveryPlayUrl(it);
  const thumb = discoveryThumbUrl(it);
  const k = discoveryItemKey(it);
  const trimMedia = discoveryTrimMediaRelpath(it);
  const phoneVideoRef = useRef<HTMLVideoElement | null>(null);
  const trimLoopRewindPendingRef = useRef(false);
  const [infoOverlayOpen, setInfoOverlayOpen] = useState(false);
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
  const skipIntroTransientAfterInfoDismiss = useRef(false);
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

  const scheduleHideViewerChrome = useCallback(() => {
    if (infoOverlayOpen) return;
    clearViewerUiTimer();
    viewerUiTimerRef.current = setTimeout(() => {
      setViewerChromeVisible(false);
      setShowVideoControls(false);
      setHideHudChromeForNativeVideo(false);
      viewerUiTimerRef.current = null;
    }, PHONE_VIEWER_CONTROLS_MS);
  }, [infoOverlayOpen, clearViewerUiTimer]);

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
    if (infoOverlayOpen) return;
    setViewerActionMenuOpen(false);
    setViewerActionMenuPos(null);
    setScrubSheetOpen(false);
    clearViewerUiTimer();
    setViewerChromeVisible(true);
    setShowVideoControls(!!play);
    setHideHudChromeForNativeVideo(false);
    scheduleHideViewerChrome();
  }, [play, infoOverlayOpen, clearViewerUiTimer, scheduleHideViewerChrome]);

  useEffect(() => {
    if (infoOverlayOpen) {
      clearViewerUiTimer();
      setViewerChromeVisible(false);
      setShowVideoControls(false);
      setHideHudChromeForNativeVideo(false);
      setViewerActionMenuOpen(false);
      setViewerActionMenuPos(null);
      setScrubSheetOpen(false);
      return;
    }
    if (skipIntroTransientAfterInfoDismiss.current) {
      skipIntroTransientAfterInfoDismiss.current = false;
      return;
    }
    if (skipIndexEffectTransientOnce.current) {
      skipIndexEffectTransientOnce.current = false;
      flashFilenameStatus();
      return;
    }
    showTransientViewerUi();
    return () => clearViewerUiTimer();
  }, [index, k, infoOverlayOpen, showTransientViewerUi, clearViewerUiTimer, flashFilenameStatus]);

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

  const dismissInfoOverlay = useCallback(() => {
    skipIntroTransientAfterInfoDismiss.current = true;
    setInfoOverlayOpen(false);
  }, []);

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
    if (!play || infoOverlayOpen) return;
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
    infoOverlayOpen,
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
    if (!play || !videoAutoplay || infoOverlayOpen || scrubSheetOpen) return;
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
  }, [play, videoAutoplay, k, index, infoOverlayOpen, scrubSheetOpen]);

  const goNext = useCallback(() => {
    onIndexChange(Math.min(index + 1, items.length - 1));
  }, [index, items.length, onIndexChange]);

  const goPrev = useCallback(() => {
    onIndexChange(Math.max(index - 1, 0));
  }, [index, onIndexChange]);

  const swipeTouchStart = useRef<{ y: number; x: number } | null>(null);
  const stageMenuAnchorRef = useRef<{ x: number; y: number } | null>(null);

  const onSwipeTouchStart = useCallback((e: React.TouchEvent) => {
    if (infoOverlayOpen || viewerActionMenuOpen || scrubSheetOpen) return;
    if (e.touches.length !== 1) return;
    swipeTouchStart.current = { y: e.touches[0].clientY, x: e.touches[0].clientX };
  }, [infoOverlayOpen, viewerActionMenuOpen, scrubSheetOpen]);

  const onSwipeTouchEnd = useCallback(
    (e: React.TouchEvent) => {
      if (infoOverlayOpen || viewerActionMenuOpen || scrubSheetOpen) return;
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
    [goNext, goPrev, infoOverlayOpen, viewerActionMenuOpen, scrubSheetOpen, index, items.length]
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
      if (infoOverlayOpen || viewerActionMenuOpen || scrubSheetOpen) return;
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
    [infoOverlayOpen, viewerActionMenuOpen, scrubSheetOpen, clearStageLp]
  );

  const onStagePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (infoOverlayOpen || viewerActionMenuOpen || scrubSheetOpen || !stageLpDown.current || !stageLpTimer.current)
        return;
      const dx = e.clientX - stageLpDown.current.x;
      const dy = e.clientY - stageLpDown.current.y;
      const lim = PHONE_LONG_PRESS_MOVE_CANCEL_PX;
      if (dx * dx + dy * dy > lim * lim) {
        clearStageLp();
        stageLpDown.current = null;
      }
    },
    [infoOverlayOpen, viewerActionMenuOpen, scrubSheetOpen, clearStageLp]
  );

  const onStagePointerUp = useCallback(
    (e: React.PointerEvent) => {
      if (infoOverlayOpen || viewerActionMenuOpen || scrubSheetOpen) return;
      clearStageLp();
      stageLpDown.current = null;
      if (stageLpSuppressClick.current) {
        stageLpSuppressClick.current = false;
        e.preventDefault();
        e.stopPropagation();
      }
    },
    [infoOverlayOpen, viewerActionMenuOpen, scrubSheetOpen, clearStageLp]
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
    if (infoOverlayOpen) return;
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
  }, [infoOverlayOpen, viewerActionMenuOpen, scrubSheetOpen, closeScrubSheet, showTransientViewerUi]);

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

      {viewerChromeVisible && !infoOverlayOpen && !hideHudChromeForNativeVideo ? (
        <div className="discovery-phone-viewer-chrome">
          <div className="discovery-phone-viewer-chrome-filename" title={it.name}>
            <span className="mono discovery-phone-viewer-chrome-filename-text">{it.name}</span>
          </div>
          <div className="discovery-phone-viewer-chrome-top">
            <button type="button" className="discovery-phone-viewer-chrome-btn" onClick={onClose} aria-label="Return to list">
              ← List
            </button>
            <span className="mono discovery-phone-viewer-chrome-counter">{index + 1} / {items.length}</span>
            <div className="discovery-phone-viewer-chrome-top-actions">
              <button type="button" className="discovery-phone-viewer-chrome-btn" onClick={() => setInfoOverlayOpen(true)} aria-label="Details">
                Info
              </button>
              <button
                type="button"
                className="discovery-phone-viewer-chrome-btn"
                onClick={() => void toggleViewerFullscreen()}
                aria-label={inViewerFullscreen ? "Exit fullscreen" : "Fullscreen viewer"}
              >
                {inViewerFullscreen ? "Exit FS" : "Full"}
              </button>
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
            Swipe for next / prev · long-press video for actions menu · Full uses the visible viewport on phones (Safari
            bars may still show)
          </p>
        </div>
      ) : null}

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
                setInfoOverlayOpen(true);
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

      {infoOverlayOpen ? (
        <div className="discovery-phone-info-layer">
          <button type="button" className="discovery-phone-info-backdrop" aria-label="Dismiss details" onClick={dismissInfoOverlay} />
          <div className="discovery-phone-info-sheet" onClick={(e) => e.stopPropagation()}>
            <div className="discovery-phone-info-sheet-filename" title={it.name}>
              <span className="mono discovery-phone-info-sheet-filename-text">{it.name}</span>
            </div>
            <button type="button" className="discovery-phone-info-dismiss" onClick={dismissInfoOverlay}>
              Dismiss
            </button>
            <div className="discovery-phone-info-sheet-body">
              <DiscoveryItemMetaBody it={it} k={k} saved={saved} onToggleSaved={onToggleSaved} />
            </div>
          </div>
        </div>
      ) : null}

      {filenameLinePhase !== "hidden" && !infoOverlayOpen && !viewerChromeVisible ? (
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
