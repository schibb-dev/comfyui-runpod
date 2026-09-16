import { describe, expect, it } from "vitest";
import { formatCacheAgeMs, workbenchLoadingHint } from "./workbenchLoadingHint";

describe("workbenchLoadingHint", () => {
  it("prioritizes cold job list load", () => {
    expect(
      workbenchLoadingHint({
        followUpSet: false,
        followUpLoading: false,
        followUpRefreshing: false,
        jobListLoading: true,
        jobListRefreshing: false,
        hasJobList: false,
        jobHistoryLoading: false,
        mediaProbeLoading: false,
      }),
    ).toContain("Loading jobs");
  });

  it("mentions cache while refreshing with data", () => {
    expect(
      workbenchLoadingHint({
        followUpSet: false,
        followUpLoading: false,
        followUpRefreshing: false,
        jobListLoading: false,
        jobListRefreshing: true,
        hasJobList: true,
        jobHistoryLoading: false,
        mediaProbeLoading: false,
        cacheAgeLabel: "2m ago",
      }),
    ).toBe("Refreshing jobs (showing 2m ago)…");
  });

  it("formats cache age", () => {
    const now = 1_000_000;
    expect(formatCacheAgeMs(now - 12_000, now)).toBe("12s ago");
    expect(formatCacheAgeMs(now - 120_000, now)).toBe("2m ago");
  });
});
