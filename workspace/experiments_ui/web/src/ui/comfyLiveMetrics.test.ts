import { describe, expect, it } from "vitest";
import { formatDurationMmSs, liveStepLabel, liveTimingParts } from "./comfyLiveMetrics";
import type { ComfyLiveStatusItem } from "./types";

function item(partial: Partial<ComfyLiveStatusItem>): ComfyLiveStatusItem {
  return {
    prompt_id: "p1",
    has_preview: false,
    ...partial,
  };
}

describe("liveStepLabel", () => {
  it("prefers node_title over node id", () => {
    expect(liveStepLabel({ prompt_id: "p1", has_preview: false, node: "462", node_title: "KSampler" })).toBe(
      "KSampler",
    );
    expect(liveStepLabel({ prompt_id: "p1", has_preview: false, node: "462" })).toBe("node 462");
  });
});

describe("formatDurationMmSs", () => {
  it("formats sub-hour durations as m:ss", () => {
    expect(formatDurationMmSs(45)).toBe("0:45");
    expect(formatDurationMmSs(725)).toBe("12:05");
  });

  it("formats hour-plus as h:mm:ss", () => {
    expect(formatDurationMmSs(3665)).toBe("1:01:05");
  });
});

describe("liveTimingParts", () => {
  it("computes determinate percent from value/max", () => {
    const out = liveTimingParts(item({ value: 2, max: 14, status: "running" }), Date.now());
    expect(out.pct).toBe(14);
    expect(out.running).toBe(true);
  });

  it("treats a running node without sampler ticks as running", () => {
    const out = liveTimingParts(item({ status: "running", node: "136" }), Date.now());
    expect(out.pct).toBeNull();
    expect(out.running).toBe(true);
  });

  it("uses submittedAt for elapsed when started_at is missing", () => {
    const now = Date.parse("2026-09-15T16:00:00Z");
    const out = liveTimingParts(item({ status: "running" }), now, "2026-09-15T15:59:00Z");
    expect(out.elapsedClient).toBe(60);
  });
});
