import { describe, expect, it } from "vitest";
import { recencyStamp } from "./workProductRecency";
import type { WorkProductItem } from "./types";

describe("recencyStamp", () => {
  it("uses finished_at (video generated) over deposit and submit", () => {
    const item = {
      status: "complete",
      finished_at: "2026-09-06T17:13:05+00:00",
      deposited_at: "2026-09-06T20:58:24+00:00",
      submitted_at: "2026-09-06T16:15:29+00:00",
      created_at: "2026-09-06T16:15:28+00:00",
    } as WorkProductItem;
    expect(recencyStamp(item)).toBe("2026-09-06T17:13:05+00:00");
  });

  it("does not use deposited_at when the video time is missing", () => {
    const item = {
      status: "complete",
      deposited_at: "2026-09-06T20:58:24+00:00",
      submitted_at: "2026-09-06T16:15:29+00:00",
    } as WorkProductItem;
    expect(recencyStamp(item)).toBe("2026-09-06T16:15:29+00:00");
  });
});
