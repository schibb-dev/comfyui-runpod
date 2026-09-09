import { describe, expect, it } from "vitest";
import { trimStopAtEndSeekedClamp } from "./phoneTrimModel";

describe("trimStopAtEndSeekedClamp", () => {
  const b = { in: 1, out: 4 };
  const duration = 8;

  it("leaves a paused playhead on the out mark for OUT-handle preview", () => {
    expect(trimStopAtEndSeekedClamp(4, b, duration, true)).toBeNull();
    expect(trimStopAtEndSeekedClamp(4 - 5e-4, b, duration, true)).toBeNull();
  });

  it("snaps a playing playhead on or past out to the last in-window frame", () => {
    expect(trimStopAtEndSeekedClamp(4, b, duration, false)).toBeCloseTo(4 - 1 / 120, 6);
    expect(trimStopAtEndSeekedClamp(4.4, b, duration, false)).toBeCloseTo(4 - 1 / 120, 6);
  });

  it("still clamps a paused seek well past out", () => {
    expect(trimStopAtEndSeekedClamp(5.2, b, duration, true)).toBeCloseTo(4 - 1 / 120, 6);
  });

  it("does not clamp seeks still inside the window", () => {
    expect(trimStopAtEndSeekedClamp(3.2, b, duration, false)).toBeNull();
    expect(trimStopAtEndSeekedClamp(3.2, b, duration, true)).toBeNull();
  });
});
