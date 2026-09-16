/** Human-readable Workbench load status for the page subtitle. */

export type WorkbenchLoadingState = {
  followUpSet: boolean;
  followUpLoading: boolean;
  followUpRefreshing: boolean;
  jobListLoading: boolean;
  jobListRefreshing: boolean;
  hasJobList: boolean;
  jobHistoryLoading: boolean;
  jobHistoryLabel?: string | null;
  mediaProbeLoading: boolean;
  cacheAgeLabel?: string | null;
};

export function workbenchLoadingHint(state: WorkbenchLoadingState): string | null {
  if (state.followUpLoading) return "Loading follow-up pile…";
  if (state.jobListLoading && !state.hasJobList) {
    return "Loading jobs (factory ledger, Comfy queue, still-tag batches)…";
  }
  if (state.jobHistoryLoading) {
    const label = String(state.jobHistoryLabel || "").trim();
    return label ? `Loading job ${label} from history…` : "Loading focused job from history…";
  }
  if (state.mediaProbeLoading) return "Checking media file on disk…";
  if (state.jobListRefreshing) {
    const age = String(state.cacheAgeLabel || "").trim();
    return age ? `Refreshing jobs (showing ${age})…` : "Refreshing job list…";
  }
  if (state.followUpRefreshing) return "Refreshing follow-up pile…";
  if (state.jobListLoading && state.hasJobList) {
    const age = String(state.cacheAgeLabel || "").trim();
    return age ? `Updating jobs (showing cached list from ${age})…` : "Updating job list…";
  }
  return null;
}

export function formatCacheAgeMs(fetchedAtMs: number | undefined, nowMs: number = Date.now()): string | null {
  if (!fetchedAtMs || !Number.isFinite(fetchedAtMs)) return null;
  const sec = Math.max(0, Math.round((nowMs - fetchedAtMs) / 1000));
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  return `${hr}h ago`;
}
