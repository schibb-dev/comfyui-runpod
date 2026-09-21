import { describe, expect, it } from "vitest";
import {
  activeWorkbenchPhases,
  formatCacheAgeMs,
  formatElapsedMs,
  lastLoadPhaseLabel,
  workbenchLoadingHint,
} from "./workbenchLoadingHint";

const idle = {
  followUpSet: false,
  followUpLoading: false,
  followUpRefreshing: false,
  jobListLoading: false,
  jobListRefreshing: false,
  hasJobList: false,
  jobHistoryLoading: false,
  mediaProbeLoading: false,
};

describe("workbenchLoadingHint", () => {
  it("prioritizes cold job list load with live phase and elapsed time", () => {
    expect(
      workbenchLoadingHint({
        ...idle,
        jobListLoading: true,
        phaseLabel: "Comfy queue",
        elapsedMs: 1200,
      }),
    ).toBe("Loading jobs (Comfy queue) · 1.2s…");
  });

  it("mentions cache while refreshing with data", () => {
    expect(
      workbenchLoadingHint({
        ...idle,
        jobListRefreshing: true,
        hasJobList: true,
        cacheAgeLabel: "2m ago",
      }),
    ).toBe("Refreshing jobs (showing 2m ago)…");
  });

  it("says the list is up while history enrich is still running", () => {
    expect(
      workbenchLoadingHint({
        ...idle,
        hasJobList: true,
        enriching: true,
        elapsedMs: 4300,
      }),
    ).toBe("Showing jobs — loading Comfy history · 4.3s…");
  });

  it("formats cache age", () => {
    const now = 1_000_000;
    expect(formatCacheAgeMs(now - 12_000, now)).toBe("12s ago");
    expect(formatCacheAgeMs(now - 120_000, now)).toBe("2m ago");
  });

  it("formats elapsed seconds", () => {
    expect(formatElapsedMs(250)).toBe("0.3s");
    expect(formatElapsedMs(12_400)).toBe("12s");
  });

  it("picks expected phases before timings arrive", () => {
    expect(lastLoadPhaseLabel(activeWorkbenchPhases({
      liteFetching: true,
      enrichFetching: false,
      hasJobList: false,
    }))).toBe("Factory job list");
    expect(lastLoadPhaseLabel(activeWorkbenchPhases({
      liteFetching: false,
      enrichFetching: true,
      hasJobList: true,
      litePhases: [{ id: "factory_jobs", label: "Factory job list", ms: 80 }],
    }))).toBe("Comfy history failures");
    expect(lastLoadPhaseLabel(activeWorkbenchPhases({
      liteFetching: false,
      enrichFetching: false,
      stillTagsFetching: true,
      hasJobList: true,
    }))).toBe("Still-tag stubs");
  });

  it("mentions still-tag stubs only after the job list is up", () => {
    expect(
      workbenchLoadingHint({
        ...idle,
        hasJobList: true,
        stillTagsLoading: true,
        elapsedMs: 800,
      }),
    ).toBe("Showing jobs — loading still-tag stubs · 0.8s…");
  });
});
