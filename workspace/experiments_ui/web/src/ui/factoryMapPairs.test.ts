import { describe, expect, it } from "vitest";
import { shortMediaLabel, shortPairLabel } from "../ui/factoryMapPairs";

describe("shortPairLabel", () => {
  it("does not lead with catalog-default from job_key", () => {
    const label = shortPairLabel({
      pairKey: "k",
      jobKey: "hourly__pp-catalog-default__still-abc123def456__000",
      gap: "none",
      source: { basename: "SSSabc123def4567890.jpeg" },
    });
    expect(label.toLowerCase()).not.toContain("catalog-default");
    expect(label.length).toBeGreaterThan(0);
  });

  it("prefers source identity over placeholder prompt", () => {
    const label = shortPairLabel({
      pairKey: "k",
      gap: "none",
      bindings: {
        prompt_profile: { basename: "catalog-default.json" },
        source_still: { basename: "X-Kneel-FB9-2026-03-24-142815_OG_00001.png" },
      },
      source: { basename: "X-Kneel-FB9-2026-03-24-142815_OG_00001.png" },
    });
    expect(label.toLowerCase()).not.toContain("catalog");
    expect(label).toMatch(/Kneel|142815|00001/i);
  });

  it("keeps distinctive prompt labels beside source", () => {
    const label = shortPairLabel({
      pairKey: "k",
      gap: "none",
      bindings: {
        prompt_profile: { basename: "overhead-soft.json" },
        source_video: { basename: "clip_A.mp4" },
      },
      source: { basename: "clip_A.mp4" },
    });
    expect(label).toContain("clip_A");
    expect(label).toContain("overhead-soft");
  });
});

describe("shortMediaLabel", () => {
  it("compacts long hex stems", () => {
    const h = "a".repeat(64);
    const label = shortMediaLabel(`SSS${h}`);
    expect(label.includes("…")).toBe(true);
    expect(label.length).toBeLessThan(20);
  });
});
