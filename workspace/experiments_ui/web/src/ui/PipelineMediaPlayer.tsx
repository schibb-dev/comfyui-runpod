import React, { useEffect, useMemo, useRef, useState } from "react";
import { AppetitePreviewFrame } from "./AppetitePreviewBadge";
import { VideoTrimControls, type VideoTrimPlaybackMode } from "./VideoTrimControls";
import { useTrimPlaybackEnforcement } from "./useTrimPlayback";
import { parseFps, vhsDefaultsToMarks, type VhsDefaults } from "./workProductTrim";

/**
 * Workbench-style media preview for pipeline lists (Queue, etc.).
 * When VHS skip/cap are provided, playback is clamped to that window and a
 * readonly trim scrubber is shown (marks are not editable). Explicit markIn/markOut
 * (from factory vhs_window) are used when skip/cap are unset/zero.
 */
export function PipelineMediaPlayer({
  videoUrl,
  thumbUrl,
  mediaKey,
  alt = "",
  className,
  vhsWindow,
  fpsHint,
  markIn: markInProp,
  markOut: markOutProp,
  appetiteRelpath,
}: {
  videoUrl?: string | null;
  thumbUrl?: string | null;
  mediaKey?: string;
  alt?: string;
  appetiteRelpath?: string | null;
  /** Kept for call-site compatibility; queue viewers are always non-editing. */
  readOnly?: boolean;
  className?: string;
  /** Applied VHS loader window (skip_first_frames / frame_load_cap). */
  vhsWindow?: VhsDefaults | null;
  /** Optional fps override (e.g. force_rate from the prompt). */
  fpsHint?: number | null;
  /** Optional Use marks in seconds (factory vhs_window). */
  markIn?: number | null;
  markOut?: number | null;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [mode, setMode] = useState<VideoTrimPlaybackMode>("repeat");
  const fps = parseFps(fpsHint, 18);
  const syncKey = mediaKey || videoUrl || thumbUrl || "pipeline-media";

  const skip = Math.max(0, Math.floor(Number(vhsWindow?.skip_first_frames ?? 0) || 0));
  const cap = Math.max(0, Math.floor(Number(vhsWindow?.frame_load_cap ?? 0) || 0));
  const hasVhsIntent = skip > 0 || cap > 0;
  const explicitMarkIn =
    markInProp != null && Number.isFinite(markInProp) ? Math.max(0, markInProp) : null;
  const explicitMarkOut =
    markOutProp != null && Number.isFinite(markOutProp) ? Math.max(0, markOutProp) : null;
  const hasExplicitMarks =
    explicitMarkIn != null && explicitMarkOut != null && explicitMarkOut > explicitMarkIn + 0.05;
  const hasTrimIntent = hasVhsIntent || hasExplicitMarks;

  const marks = useMemo(() => {
    if (!hasTrimIntent || !(duration > 0)) {
      return { markIn: null as number | null, markOut: null as number | null, warning: null as string | null };
    }
    if (hasVhsIntent) {
      return vhsDefaultsToMarks({ skip_first_frames: skip, frame_load_cap: cap }, duration, fps);
    }
    return {
      markIn: explicitMarkIn,
      markOut: Math.min(duration, explicitMarkOut!),
      warning: null as string | null,
    };
  }, [hasTrimIntent, hasVhsIntent, skip, cap, duration, fps, explicitMarkIn, explicitMarkOut]);

  useTrimPlaybackEnforcement(videoRef, {
    mediaKey: syncKey,
    markIn: marks.markIn,
    markOut: marks.markOut,
    mode,
    enabled: Boolean(videoUrl) && hasTrimIntent,
  });

  useEffect(() => {
    setDuration(0);
    setCurrentTime(0);
  }, [syncKey, videoUrl]);

  if (videoUrl) {
    return (
      <div className={["work-product-viewer", "pipeline-media-player", className].filter(Boolean).join(" ")}>
        <div className="work-product-viewer__main">
          <AppetitePreviewFrame relpath={appetiteRelpath}>
            <video
              ref={videoRef}
              className="work-product-viewer__video"
              src={videoUrl}
              poster={thumbUrl || undefined}
            controls={!hasTrimIntent}
            playsInline
            muted
            preload="metadata"
            onLoadedMetadata={(e) => {
              const d = e.currentTarget.duration;
              if (Number.isFinite(d) && d > 0) setDuration(d);
              setCurrentTime(e.currentTarget.currentTime || 0);
            }}
            onDurationChange={(e) => {
              const d = e.currentTarget.duration;
              if (Number.isFinite(d) && d > 0) setDuration(d);
            }}
            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime || 0)}
            onSeeked={(e) => setCurrentTime(e.currentTarget.currentTime || 0)}
            />
          </AppetitePreviewFrame>
        </div>
        {hasTrimIntent ? (
          <>
            <VideoTrimControls
              className="work-product-viewer__trim"
              videoRef={videoRef}
              duration={duration}
              currentTime={currentTime}
              markIn={marks.markIn}
              markOut={marks.markOut}
              mode={mode}
              mediaSyncKey={syncKey}
              readOnly
              onSeek={setCurrentTime}
              onSyncTime={setCurrentTime}
              onMarkInChange={() => {}}
              onMarkOutChange={() => {}}
              onClear={() => {}}
              onModeChange={setMode}
            />
            {marks.warning ? (
              <p className="work-product-viewer__trim-warn" title={marks.warning}>
                {marks.warning}
              </p>
            ) : null}
          </>
        ) : null}
      </div>
    );
  }

  if (thumbUrl) {
    return (
      <div className={["work-product-viewer", "pipeline-media-player", className].filter(Boolean).join(" ")}>
        <div className="work-product-viewer__main">
          <AppetitePreviewFrame relpath={appetiteRelpath}>
            <img className="work-product-viewer__img" src={thumbUrl} alt={alt} />
          </AppetitePreviewFrame>
        </div>
      </div>
    );
  }

  return (
    <div className={["work-product-viewer", "pipeline-media-player", className].filter(Boolean).join(" ")}>
      <div className="work-product-viewer__empty">No preview</div>
    </div>
  );
}

/** Pull VHS window (+ optional force_rate / Use marks) from queue/history key_params or vhs_window. */
export function vhsWindowFromKeyParams(
  params?: Record<string, unknown> | null,
): {
  window: VhsDefaults | null;
  fpsHint: number | null;
  markIn: number | null;
  markOut: number | null;
} {
  if (!params || typeof params !== "object") {
    return { window: null, fpsHint: null, markIn: null, markOut: null };
  }
  const skipRaw = params.skip_first_frames;
  const capRaw = params.frame_load_cap;
  const skip = skipRaw == null || skipRaw === "" ? 0 : Math.max(0, Math.floor(Number(skipRaw) || 0));
  const cap = capRaw == null || capRaw === "" ? 0 : Math.max(0, Math.floor(Number(capRaw) || 0));
  const force = params.force_rate;
  const fpsHint =
    force == null || force === "" || Number(force) <= 0 ? null : parseFps(force, 18);
  const miRaw = params.mark_in;
  const moRaw = params.mark_out;
  const markIn =
    miRaw == null || miRaw === "" || !Number.isFinite(Number(miRaw)) ? null : Math.max(0, Number(miRaw));
  const markOut =
    moRaw == null || moRaw === "" || !Number.isFinite(Number(moRaw)) ? null : Math.max(0, Number(moRaw));
  const window = skip <= 0 && cap <= 0 ? null : { skip_first_frames: skip, frame_load_cap: cap };
  return { window, fpsHint, markIn, markOut };
}
