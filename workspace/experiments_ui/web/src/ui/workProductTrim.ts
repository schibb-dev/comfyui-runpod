/** VHS input-window helpers for Work Products trim UI. */

import { phoneTrimBounds } from "./phoneTrimModel";

export type VhsDefaults = {
  skip_first_frames: number;
  frame_load_cap: number;
};

export type VhsWindow = VhsDefaults & {
  clamped: boolean;
  warning: string | null;
  frame_count: number;
};

export function parseFps(raw: unknown, fallback = 18): number {
  if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) return raw;
  const text = String(raw ?? "").trim();
  if (!text) return fallback;
  if (text.includes("/")) {
    const [a, b] = text.split("/", 2);
    const num = Number(a);
    const den = Number(b);
    if (Number.isFinite(num) && Number.isFinite(den) && den !== 0 && num / den > 0) return num / den;
    return fallback;
  }
  const n = Number(text);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

export function clampVhsWindow(
  skipFirstFrames: number,
  frameLoadCap: number,
  frameCount: number,
): VhsWindow {
  const fc = Math.max(0, Math.floor(frameCount));
  const reqSkip = Math.max(0, Math.floor(skipFirstFrames));
  const reqCap = Math.max(0, Math.floor(frameLoadCap));
  if (fc <= 0) {
    // Keep the requested window. Zeroing here used to make Re-run/Submit send
    // skip=0,cap=0 while marks were still set (source duration not loaded yet),
    // which overrode the work-products trim sidecar on the backend.
    return {
      skip_first_frames: reqSkip,
      frame_load_cap: reqCap,
      clamped: false,
      warning: reqSkip || reqCap ? "clip length unknown — skip/cap not clamped to media" : null,
      frame_count: 0,
    };
  }
  const skip = Math.min(reqSkip, Math.max(0, fc - 1));
  const remaining = fc - skip;
  let cap = 0;
  if (reqCap > 0) {
    cap = Math.min(reqCap, remaining);
  }
  const clamped = skip !== reqSkip || cap !== reqCap;
  const warning = clamped
    ? `template skip ${reqSkip} → ${skip} for this clip (${fc} frames)`
    : null;
  return {
    skip_first_frames: skip,
    frame_load_cap: cap,
    clamped,
    warning,
    frame_count: fc,
  };
}

export function vhsDefaultsToMarks(
  defaults: VhsDefaults,
  duration: number,
  fps: number,
): { markIn: number | null; markOut: number | null; warning: string | null; clamped: boolean } {
  if (!(duration > 0) || !(fps > 0)) {
    return { markIn: null, markOut: null, warning: null, clamped: false };
  }
  const frameCount = Math.max(1, Math.round(duration * fps));
  const win = clampVhsWindow(defaults.skip_first_frames, defaults.frame_load_cap, frameCount);
  const markIn = win.skip_first_frames / fps;
  const markOut =
    win.frame_load_cap > 0
      ? Math.min(duration, (win.skip_first_frames + win.frame_load_cap) / fps)
      : duration;
  const bounds = phoneTrimBounds(markIn, markOut, duration);
  if (!bounds) return { markIn: null, markOut: null, warning: win.warning, clamped: win.clamped };
  return {
    markIn: bounds.in,
    markOut: bounds.out,
    warning: win.warning,
    clamped: win.clamped,
  };
}

export function marksToVhsWindow(
  markIn: number | null,
  markOut: number | null,
  duration: number,
  fps: number,
  frameCountHint?: number | null,
): VhsWindow {
  const fpsN = fps > 0 ? fps : 18;
  const dur = duration > 0 ? duration : 0;
  const fc =
    frameCountHint && frameCountHint > 0
      ? Math.floor(frameCountHint)
      : dur > 0
        ? Math.max(1, Math.round(dur * fpsN))
        : 0;
  const bounds = dur > 0 ? phoneTrimBounds(markIn, markOut, dur) : null;
  const inSec = bounds ? bounds.in : Math.max(0, markIn ?? 0);
  const outSec = bounds
    ? bounds.out
    : markOut != null && Number.isFinite(markOut)
      ? Math.max(inSec, Number(markOut))
      : dur > 0
        ? dur
        : inSec;
  const reqSkip = Math.max(0, Math.round(inSec * fpsN));
  // Only "load to EOF" when we know media duration and the out mark is at/near it.
  // Missing duration must NOT collapse to cap=0 — that ignores the end of the Use window.
  const toEnd = dur > 0 && outSec >= dur - 1e-3;
  const reqCap = toEnd ? 0 : Math.max(0, Math.round((outSec - inSec) * fpsN));
  return clampVhsWindow(reqSkip, reqCap, fc);
}

export function familyVhsDefaults(
  families: Array<{ slug: string; vhs_defaults?: { skip_first_frames?: number; frame_load_cap?: number } }> | undefined,
  slug: string,
): VhsDefaults {
  const row = (families || []).find((f) => f.slug === slug);
  return {
    skip_first_frames: Math.max(0, Math.floor(Number(row?.vhs_defaults?.skip_first_frames ?? 0) || 0)),
    frame_load_cap: Math.max(0, Math.floor(Number(row?.vhs_defaults?.frame_load_cap ?? 0) || 0)),
  };
}

/**
 * Seconds into an output clip where prior/origin material ends and this pass's
 * generated frames begin. Prefer construction.frames_before; else
 * output_frame_count − workload.frames (or duration − gen/fps).
 */
export function originSeamSeconds(opts: {
  duration: number;
  fps: number;
  framesBefore?: number | null;
  generationFrames?: number | null;
  outputFrameCount?: number | null;
}): number | null {
  const duration = Number(opts.duration) || 0;
  const fps = Number(opts.fps) || 0;
  if (!(duration > 0.1) || !(fps > 0)) return null;

  const before = Math.floor(Number(opts.framesBefore));
  if (Number.isFinite(before) && before > 0) {
    const t = before / fps;
    if (t > 0.04 && t < duration - 0.04) return t;
  }

  const gen = Math.floor(Number(opts.generationFrames));
  if (!(Number.isFinite(gen) && gen > 0)) return null;

  const fc = Math.floor(Number(opts.outputFrameCount));
  let t: number | null = null;
  if (Number.isFinite(fc) && fc > gen) {
    t = (fc - gen) / fps;
  } else {
    t = duration - gen / fps;
  }
  if (t == null || !(t > 0.04) || !(t < duration - 0.04)) return null;
  return t;
}

/** Origin / overlap-blend / generated bands for the output trim track. */
export type OriginGenerationBands = {
  /** Start of this pass's generation window (origin ends here). */
  seamSec: number;
  /** End of overlap blend (= seam + overlap/fps), or null when overlap unknown. */
  blendEndSec: number | null;
  overlapFrames: number | null;
};

export function originGenerationBands(opts: {
  duration: number;
  fps: number;
  framesBefore?: number | null;
  generationFrames?: number | null;
  outputFrameCount?: number | null;
  overlapFrames?: number | null;
}): OriginGenerationBands | null {
  const duration = Number(opts.duration) || 0;
  const fps = Number(opts.fps) || 0;
  const seamSec = originSeamSeconds(opts);
  if (seamSec == null || !(duration > 0) || !(fps > 0)) return null;

  const ov = Math.floor(Number(opts.overlapFrames));
  let blendEndSec: number | null = null;
  let overlapFrames: number | null = null;
  if (Number.isFinite(ov) && ov > 0) {
    const end = Math.min(duration, seamSec + ov / fps);
    if (end > seamSec + 0.02) {
      blendEndSec = end;
      overlapFrames = ov;
    }
  }
  return { seamSec, blendEndSec, overlapFrames };
}

