import { useEffect, useRef, type RefObject } from "react";
import {
  phoneTrimBounds,
  phoneTrimLoopSeekTarget,
  phoneTrimPlaybackActive,
} from "./phoneTrimModel";

/** Repeat trim: `timeupdate` is sparse; treat as past-out slightly before `out`. */
const TRIM_REPEAT_TIMEUPDATE_OUT_EPS_SEC = 0.048;
/**
 * Stop-at-out: when resuming play, `currentTime` can sit slightly inside `out` while the
 * next decoded frame jumps past — widen "on or past out" so `play` seeks back first.
 */
const TRIM_STOP_PLAY_RESUME_NEAR_OUT_SEC = 0.055;

export type TrimPlaybackMode = "repeat" | "stop_at_end";

/**
 * Enforce in/out window on a `<video>`: clamp seeks, stop-at-out or loop (Discovery parity).
 */
export function useTrimPlaybackEnforcement(
  videoRef: RefObject<HTMLVideoElement | null>,
  opts: {
    mediaKey: string | number;
    markIn: number | null;
    markOut: number | null;
    mode: TrimPlaybackMode;
    enabled?: boolean;
  },
): void {
  const { mediaKey, markIn, markOut, mode, enabled = true } = opts;
  const loop = mode === "repeat";
  const rewindPendingRef = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    const v = videoRef.current;
    if (!v) return;

    rewindPendingRef.current = false;
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
      rewindPendingRef.current = true;
      const resume = opts?.resumeAfterSeek ?? !v.paused;
      v.currentTime = phoneTrimLoopSeekTarget(b);
      if (resume) void v.play().catch(() => {});
      rewindSafetyTimer = setTimeout(() => {
        rewindPendingRef.current = false;
        rewindSafetyTimer = null;
      }, 400);
    };

    const onTimeUpdate = () => {
      if (v.seeking) return;
      if (loop && rewindPendingRef.current) return;
      const ctx = applyTrimPlayback();
      if (!ctx?.trimActive) return;
      const { b, duration } = ctx;
      const t = v.currentTime;

      if (t < b.in - 1e-3) {
        if (loop) {
          if (!v.paused) rewindLoop(b);
          else v.currentTime = b.in;
        } else {
          v.currentTime = b.in;
        }
        return;
      }

      if (!loop) {
        const pastOutPlaying = !v.paused && (v.ended || t + TRIM_STOP_PLAY_RESUME_NEAR_OUT_SEC >= b.out);
        if (pastOutPlaying) {
          v.pause();
          v.currentTime = Math.max(b.in, Math.min(b.out - 1 / 120, Math.max(0, duration - 1e-6)));
        }
        return;
      }

      const pastOutWhilePlaying = !v.paused && t + TRIM_REPEAT_TIMEUPDATE_OUT_EPS_SEC >= b.out;
      if (pastOutWhilePlaying) rewindLoop(b);
    };

    const onEnded = () => {
      if (!loop) {
        v.pause();
        return;
      }
      if (v.seeking) return;
      const ctx = applyTrimPlayback();
      if (!ctx?.trimActive) return;
      rewindLoop(ctx.b, { resumeAfterSeek: true });
    };

    const onPlay = () => {
      const ctx = applyTrimPlayback();
      if (!ctx?.trimActive) return;
      const { b } = ctx;
      const t = v.currentTime;
      if (t < b.in - 1e-4) {
        v.currentTime = loop ? phoneTrimLoopSeekTarget(b) : b.in;
        return;
      }
      if (t + TRIM_STOP_PLAY_RESUME_NEAR_OUT_SEC >= b.out) {
        v.currentTime = loop ? phoneTrimLoopSeekTarget(b) : b.in;
      }
    };

    const onSeeked = () => {
      if (loop) {
        clearRewindSafety();
        rewindPendingRef.current = false;
        return;
      }
      const ctx = applyTrimPlayback();
      if (!ctx?.trimActive) return;
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
  }, [videoRef, mediaKey, markIn, markOut, loop, enabled]);
}
