/** Human-readable Workbench load status for the page subtitle. */

export type WorkbenchLoadPhase = {
  id?: string;
  label?: string;
  ms?: number;
};

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
  enriching?: boolean;
  stillTagsLoading?: boolean;
  phaseLabel?: string | null;
  elapsedMs?: number | null;
};

export function formatElapsedMs(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
  const sec = ms / 1000;
  if (sec < 10) return `${sec.toFixed(1)}s`;
  return `${Math.round(sec)}s`;
}

export function lastLoadPhaseLabel(phases?: WorkbenchLoadPhase[] | null): string | null {
  if (!phases?.length) return null;
  const last = phases[phases.length - 1];
  const label = String(last?.label || "").trim();
  return label || null;
}

/** Phases to show on the loading screen: completed timings, else the next expected wait. */
export function activeWorkbenchPhases(opts: {
  liteFetching: boolean;
  enrichFetching: boolean;
  stillTagsFetching?: boolean;
  hasJobList: boolean;
  litePhases?: WorkbenchLoadPhase[] | null;
  enrichPhases?: WorkbenchLoadPhase[] | null;
}): WorkbenchLoadPhase[] {
  if (opts.liteFetching && !opts.hasJobList) {
    return opts.litePhases?.length
      ? opts.litePhases
      : [
          { id: "comfy_queue", label: "Comfy queue" },
          { id: "factory_jobs", label: "Factory job list" },
        ];
  }
  if (opts.enrichFetching) {
    if (opts.enrichPhases?.length) return opts.enrichPhases;
    const prior = opts.litePhases?.length ? opts.litePhases : [];
    return [...prior, { id: "history_failures", label: "Comfy history failures" }];
  }
  if (opts.stillTagsFetching) {
    const prior = opts.enrichPhases?.length
      ? opts.enrichPhases
      : opts.litePhases?.length
        ? opts.litePhases
        : [];
    return [...prior, { id: "still_tags", label: "Still-tag stubs" }];
  }
  return opts.enrichPhases?.length ? opts.enrichPhases : opts.litePhases || [];
}

export function workbenchLoadingHint(state: WorkbenchLoadingState): string | null {
  const elapsed = formatElapsedMs(state.elapsedMs);
  const time = elapsed ? ` · ${elapsed}` : "";
  const phase = String(state.phaseLabel || "").trim();

  if (state.followUpLoading) return `Loading follow-up pile${time}…`;
  if (state.jobListLoading && !state.hasJobList) {
    const core = phase || "factory ledger, Comfy queue";
    return `Loading jobs (${core})${time}…`;
  }
  if (state.enriching && state.hasJobList) {
    return `Showing jobs — loading Comfy history${time}…`;
  }
  if (state.stillTagsLoading && state.hasJobList) {
    return `Showing jobs — loading still-tag stubs${time}…`;
  }
  if (state.jobHistoryLoading) {
    const label = String(state.jobHistoryLabel || "").trim();
    return label
      ? `Loading job ${label} from history${time}…`
      : `Loading focused job from history${time}…`;
  }
  if (state.mediaProbeLoading) return `Checking media file on disk${time}…`;
  if (state.jobListRefreshing) {
    const age = String(state.cacheAgeLabel || "").trim();
    return age ? `Refreshing jobs (showing ${age})${time}…` : `Refreshing job list${time}…`;
  }
  if (state.followUpRefreshing) return `Refreshing follow-up pile${time}…`;
  if (state.jobListLoading && state.hasJobList) {
    const age = String(state.cacheAgeLabel || "").trim();
    return age ? `Updating jobs (showing cached list from ${age})${time}…` : `Updating job list${time}…`;
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
