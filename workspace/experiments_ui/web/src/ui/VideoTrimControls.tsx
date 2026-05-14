import React, { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { phoneTrimBounds, phoneTrimPlaybackActive, TRIM_HANDLE_MIN_GAP_SEC } from "./phoneTrimModel";

type TrimDragKind = "in" | "out" | "play";

export type VideoTrimPlaybackMode = "repeat" | "stop_at_end";

export function formatVideoSeconds(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const s = Math.floor(sec % 60);
  const m = Math.floor((sec / 60) % 60);
  const h = Math.floor(sec / 3600);
  const pad = (n: number) => (n < 10 ? `0${n}` : String(n));
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function IconPlay() {
  return (
    <svg className="video-trim-controls__svg" viewBox="0 0 24 24" aria-hidden>
      <polygon points="8,5 20,12 8,19" fill="currentColor" />
    </svg>
  );
}

function IconPause() {
  return (
    <svg className="video-trim-controls__svg" viewBox="0 0 24 24" aria-hidden>
      <rect x="7" y="5" width="3.5" height="14" rx="0.5" fill="currentColor" />
      <rect x="13.5" y="5" width="3.5" height="14" rx="0.5" fill="currentColor" />
    </svg>
  );
}

function IconToStart() {
  return (
    <svg className="video-trim-controls__svg" viewBox="0 0 24 24" aria-hidden>
      <rect x="4" y="6" width="2.5" height="12" rx="0.5" fill="currentColor" />
      <path d="M10 12l8-5.5v11L10 12z" fill="currentColor" />
    </svg>
  );
}

function IconToEnd() {
  return (
    <svg className="video-trim-controls__svg" viewBox="0 0 24 24" aria-hidden>
      <path d="M14 12L6 6.5v11L14 12z" fill="currentColor" />
      <rect x="17.5" y="6" width="2.5" height="12" rx="0.5" fill="currentColor" />
    </svg>
  );
}

function IconClear() {
  return (
    <svg className="video-trim-controls__svg" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M5 5l14 14M19 5L5 19" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

function IconRepeat() {
  return (
    <svg className="video-trim-controls__svg" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="m17 2 4 4-4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 11v-1a4 4 0 0 1 4-4h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="m7 22-4-4 4-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M21 13v1a4 4 0 0 1-4 4H3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconStopAtEnd() {
  return (
    <svg className="video-trim-controls__svg" viewBox="0 0 24 24" aria-hidden>
      <path d="M5 9h9V5l7 7-7 7v-4H5V9z" fill="currentColor" />
    </svg>
  );
}

function nextMode(mode: VideoTrimPlaybackMode): VideoTrimPlaybackMode {
  return mode === "repeat" ? "stop_at_end" : "repeat";
}

type VideoTrimTimelineProps = {
  duration: number;
  currentTime: number;
  markIn: number | null;
  markOut: number | null;
  disabled: boolean;
  onSeek: (t: number) => void;
  onMarkInChange: (t: number) => void;
  onMarkOutChange: (t: number) => void;
};

function VideoTrimTimeline({
  duration,
  currentTime,
  markIn,
  markOut,
  disabled,
  onSeek,
  onMarkInChange,
  onMarkOutChange,
}: VideoTrimTimelineProps) {
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

  const timeFromClientX = useCallback(
    (clientX: number) => {
      const el = trackRef.current;
      if (!el || duration <= 0) return 0;
      const r = el.getBoundingClientRect();
      const w = Math.max(1, r.width);
      const x = Math.min(Math.max(clientX - r.left, 0), w);
      return (x / w) * duration;
    },
    [duration],
  );

  useEffect(() => {
    if (!drag) return;
    const end = () => setDrag(null);
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
      } else if (drag === "in") {
        onMarkInChange(Math.max(0, Math.min(t, outV - TRIM_HANDLE_MIN_GAP_SEC)));
      } else {
        onMarkOutChange(Math.min(d, Math.max(t, inV + TRIM_HANDLE_MIN_GAP_SEC)));
      }
    };
    const onBlur = () => end();
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", end, true);
    window.addEventListener("pointercancel", end);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", end, true);
      window.removeEventListener("pointercancel", end);
      window.removeEventListener("blur", onBlur);
    };
  }, [drag, onSeek, onMarkInChange, onMarkOutChange, timeFromClientX]);

  const startDrag = (kind: TrimDragKind) => (e: React.PointerEvent) => {
    if (disabled) return;
    e.stopPropagation();
    e.preventDefault();
    setDrag(kind);
  };

  const onTrackPointerDown = (e: React.PointerEvent) => {
    if (disabled) return;
    const target = e.target as HTMLElement;
    if (target.closest(".video-trim-controls__handle, .video-trim-controls__playhead")) return;
    onSeek(timeFromClientX(e.clientX));
  };

  return (
    <div
      ref={trackRef}
      className={"video-trim-controls__timeline" + (disabled ? " video-trim-controls__timeline--disabled" : "")}
      onPointerDown={onTrackPointerDown}
      role="presentation"
    >
      <div className="video-trim-controls__timeline-track" />
      <div className="video-trim-controls__timeline-selection" style={{ left: `${inPct}%`, width: `${outPct - inPct}%` }} />
      <div
        className="video-trim-controls__handle video-trim-controls__handle--in"
        style={{ left: `${inPct}%` }}
        onPointerDown={startDrag("in")}
        role="slider"
        aria-label="Trim start"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={safeIn}
        aria-disabled={disabled}
      >
        <span />
      </div>
      <div
        className="video-trim-controls__handle video-trim-controls__handle--out"
        style={{ left: `${outPct}%` }}
        onPointerDown={startDrag("out")}
        role="slider"
        aria-label="Trim end"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={safeOut}
        aria-disabled={disabled}
      >
        <span />
      </div>
      <div
        className="video-trim-controls__playhead"
        style={{ left: `${playPct}%` }}
        onPointerDown={startDrag("play")}
        role="slider"
        aria-label="Playhead"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={Math.min(Math.max(0, currentTime), duration)}
        aria-disabled={disabled}
      >
        <span className="video-trim-controls__playhead-line" />
        <span className="video-trim-controls__playhead-knob" />
      </div>
    </div>
  );
}

export function VideoTrimControls({
  videoRef,
  duration,
  currentTime,
  markIn,
  markOut,
  mode,
  mediaSyncKey,
  onSeek,
  onMarkInChange,
  onMarkOutChange,
  onClear,
  onModeChange,
  onSyncTime,
  className,
  size = "default",
}: {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  duration: number;
  currentTime: number;
  markIn: number | null;
  markOut: number | null;
  mode: VideoTrimPlaybackMode;
  mediaSyncKey: string | number;
  onSeek: (t: number) => void;
  onMarkInChange: (t: number) => void;
  onMarkOutChange: (t: number) => void;
  onClear: () => void;
  onModeChange: (mode: VideoTrimPlaybackMode) => void;
  onSyncTime?: (t: number) => void;
  className?: string;
  size?: "default" | "large";
}) {
  const [, forceMediaUi] = useReducer((x: number) => x + 1, 0);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onMediaState = () => forceMediaUi();
    v.addEventListener("play", onMediaState);
    v.addEventListener("pause", onMediaState);
    v.addEventListener("ended", onMediaState);
    return () => {
      v.removeEventListener("play", onMediaState);
      v.removeEventListener("pause", onMediaState);
      v.removeEventListener("ended", onMediaState);
    };
  }, [mediaSyncKey, videoRef]);

  const paused = videoRef.current?.paused ?? true;
  const disabled = !Number.isFinite(duration) || duration <= 0;
  const bounds = phoneTrimBounds(markIn, markOut, duration);
  const trimActive = phoneTrimPlaybackActive(bounds, duration);

  const syncSeek = (t: number) => {
    const next = Math.max(0, Math.min(duration || 0, t));
    const v = videoRef.current;
    if (v) v.currentTime = next;
    onSyncTime?.(next);
    onSeek(next);
  };

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) void v.play().catch(() => {});
    else v.pause();
  };

  const setInAtPlayhead = () => {
    if (disabled) return;
    const out = Math.min(duration, markOut ?? duration);
    onMarkInChange(Math.max(0, Math.min(currentTime, out - TRIM_HANDLE_MIN_GAP_SEC)));
  };

  const setOutAtPlayhead = () => {
    if (disabled) return;
    const inn = Math.max(0, markIn ?? 0);
    onMarkOutChange(Math.min(duration, Math.max(currentTime, inn + TRIM_HANDLE_MIN_GAP_SEC)));
  };

  return (
    <div
      className={
        "video-trim-controls" +
        (size === "large" ? " video-trim-controls--large" : "") +
        (className ? ` ${className}` : "")
      }
    >
      <div className="video-trim-controls__primary">
        <div className="video-trim-controls__time mono">
          {formatVideoSeconds(currentTime)} <span>/</span> {formatVideoSeconds(duration)}
        </div>
        <div className="video-trim-controls__transport" role="group" aria-label="Video playback">
          <button type="button" aria-label="Go to trim start" title="Go to trim start" disabled={disabled} onClick={() => syncSeek(bounds?.in ?? 0)}>
            <IconToStart />
          </button>
          <button type="button" aria-label={paused ? "Play" : "Pause"} title={paused ? "Play" : "Pause"} onClick={togglePlay}>
            {paused ? <IconPlay /> : <IconPause />}
          </button>
          <button type="button" aria-label="Go to trim end" title="Go to trim end" disabled={disabled} onClick={() => syncSeek(bounds ? bounds.out : duration)}>
            <IconToEnd />
          </button>
        </div>
        <div className="video-trim-controls__io" role="group" aria-label="Set trim in and out">
          <button type="button" disabled={disabled} onClick={setInAtPlayhead} title="Set in at playhead">
            I
          </button>
          <button type="button" disabled={disabled} onClick={setOutAtPlayhead} title="Set out at playhead">
            O
          </button>
        </div>
      </div>
      <div className="video-trim-controls__timeline-row">
        <VideoTrimTimeline
          duration={duration}
          currentTime={currentTime}
          markIn={markIn}
          markOut={markOut}
          disabled={disabled}
          onSeek={syncSeek}
          onMarkInChange={onMarkInChange}
          onMarkOutChange={onMarkOutChange}
        />
        <div className="video-trim-controls__actions" role="group" aria-label="Trim range options">
          <button type="button" aria-label="Clear trim in and out" title="Clear in/out" disabled={!trimActive} onClick={onClear}>
            <IconClear />
          </button>
          <button
            type="button"
            className={"video-trim-controls__mode" + (mode === "repeat" ? " video-trim-controls__mode--repeat" : "")}
            role="switch"
            aria-checked={mode === "repeat"}
            aria-label={mode === "repeat" ? "Trim playback: repeat. Switch to stop at out." : "Trim playback: stop at out. Switch to repeat."}
            title={mode === "repeat" ? "Repeat" : "Stop at out"}
            onClick={() => onModeChange(nextMode(mode))}
          >
            {mode === "repeat" ? <IconRepeat /> : <IconStopAtEnd />}
          </button>
        </div>
      </div>
    </div>
  );
}
