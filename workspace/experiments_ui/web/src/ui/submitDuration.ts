/**
 * First Submit tunable: Wan generation length (`parameters.frames`).
 * Distinct from VHS trim fps. Empty means “use the family / variant template.”
 * Same key as queue overrides and owned-params — later variants can capture it.
 */
export const SUBMIT_GEN_FPS = 16;
export const SUBMIT_FRAMES_MIN = 1;
export const SUBMIT_FRAMES_MAX = 321;

export function clampSubmitFrames(n: number): number {
  if (!Number.isFinite(n)) return SUBMIT_FRAMES_MIN;
  return Math.min(SUBMIT_FRAMES_MAX, Math.max(SUBMIT_FRAMES_MIN, Math.round(n)));
}

export function framesToSubmitSeconds(frames: number, fps = SUBMIT_GEN_FPS): number {
  if (!(fps > 0)) return NaN;
  return clampSubmitFrames(frames) / fps;
}

export function secondsToSubmitFrames(seconds: number, fps = SUBMIT_GEN_FPS): number {
  if (!(fps > 0) || !Number.isFinite(seconds)) return SUBMIT_FRAMES_MIN;
  return clampSubmitFrames(seconds * fps);
}

export function formatSubmitDuration(frames: number | null | undefined, fps = SUBMIT_GEN_FPS): string | null {
  if (frames == null || !Number.isFinite(frames) || frames <= 0) return null;
  const f = clampSubmitFrames(frames);
  const s = framesToSubmitSeconds(f, fps);
  return `${f}f · ${s.toFixed(2)}s`;
}
