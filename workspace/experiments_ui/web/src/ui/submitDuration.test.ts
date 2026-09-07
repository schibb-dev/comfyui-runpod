import { describe, expect, it } from "vitest";
import { familyDefaultFrames } from "./submitFamily";
import {
  SUBMIT_GEN_FPS,
  clampSubmitFrames,
  formatSubmitDuration,
  framesToSubmitSeconds,
  secondsToSubmitFrames,
} from "./submitDuration";
import type { WorkProductFamilyOption } from "./types";

describe("submitDuration", () => {
  it("clamps and rounds frames", () => {
    expect(clampSubmitFrames(81.4)).toBe(81);
    expect(clampSubmitFrames(0)).toBe(1);
    expect(clampSubmitFrames(9999)).toBe(321);
  });

  it("converts 81 frames to ~5s at 16fps", () => {
    expect(framesToSubmitSeconds(81)).toBeCloseTo(81 / SUBMIT_GEN_FPS, 5);
    expect(secondsToSubmitFrames(5.0625)).toBe(81);
  });

  it("formats a label or null for empty", () => {
    expect(formatSubmitDuration(81)).toBe("81f · 5.06s");
    expect(formatSubmitDuration(null)).toBeNull();
  });
});

describe("familyDefaultFrames", () => {
  const families = [
    { slug: "Demo", params_defaults: { frames: 121 } },
    { slug: "Empty" },
  ] as WorkProductFamilyOption[];

  it("reads template frames for a family slug", () => {
    expect(familyDefaultFrames(families, "Demo")).toBe(121);
  });

  it("returns null when the family has no default", () => {
    expect(familyDefaultFrames(families, "Empty")).toBeNull();
    expect(familyDefaultFrames(families, "Missing")).toBeNull();
  });
});
