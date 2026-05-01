/** Minimum span between trim in / out (seconds). */
export const TRIM_HANDLE_MIN_GAP_SEC = 0.12;

/** Resolved trim window [in, out] aligned with UI handles; null if duration invalid. */
export function phoneTrimBounds(
  markIn: number | null,
  markOut: number | null,
  duration: number
): { in: number; out: number } | null {
  if (!Number.isFinite(duration) || duration <= 0) return null;
  const rawIn = Math.max(0, markIn ?? 0);
  const rawOut = Math.min(duration, markOut ?? duration);
  const gap = TRIM_HANDLE_MIN_GAP_SEC;
  const safeIn = Math.min(rawIn, Math.max(0, rawOut - gap));
  const safeOut = Math.max(rawOut, safeIn + gap);
  return { in: safeIn, out: safeOut };
}

/** True when trim is narrower than the full timeline (playback should enforce the window). */
export function phoneTrimPlaybackActive(bounds: { in: number; out: number } | null, duration: number): boolean {
  if (!bounds || duration <= 0) return false;
  return bounds.in > 0.008 || bounds.out < duration - 0.008;
}

/**
 * Seek target when looping [in, out): jump back to just after `in` so the full trim span
 * plays again. Nudge is a bit larger when `in > 0` so decoders that snap to the previous
 * keyframe are less likely to land before `in` and fight the trim clamp. Always stay
 * comfortably below `out` so the next frame does not immediately satisfy `t >= out`.
 */
export function phoneTrimLoopSeekTarget(b: { in: number; out: number }): number {
  const span = Math.max(0, b.out - b.in);
  const gap = TRIM_HANDLE_MIN_GAP_SEC;
  let nudge = Math.max(gap * 0.35, Math.min(0.12, span * 0.035));
  if (b.in > 0.02) nudge = Math.max(nudge, 1 / 9);
  let target = b.in + nudge;
  /* Keep a solid margin before `out` so keyframes / float time do not immediately re-trigger past-out. */
  const headroomSoft = Math.max(1 / 40, Math.min(0.05, span * 0.02));
  const headroom = Math.max(headroomSoft, Math.min(0.1, span * 0.15), 3 / 60);
  target = Math.min(b.out - headroom, target);
  target = Math.max(b.in + gap * 0.15, target);
  const ceiling = b.out - Math.max(gap * 0.25, Math.min(1 / 45, Math.max(gap, span * 0.35)));
  if (target > ceiling) target = ceiling;
  if (!(target > b.in + gap * 0.1)) target = Math.min(ceiling, b.in + gap * 0.35);
  return target;
}
