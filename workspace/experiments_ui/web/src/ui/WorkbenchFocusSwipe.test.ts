import { describe, expect, it } from "vitest";
import {
  buildWorkbenchSwipeEntries,
  workbenchJobSwipeKey,
  workbenchSwipeSectionLabel,
} from "./WorkbenchFocusSwipe";
import type { WorkProductItem } from "./types";

function item(partial: Partial<WorkProductItem> & { job_key: string; status: string }): WorkProductItem {
  return partial as WorkProductItem;
}

describe("workbench phone swipe", () => {
  it("keys jobs by job_key", () => {
    expect(workbenchJobSwipeKey({ job_key: "abc", prompt_id: "p1" })).toBe("abc");
    expect(workbenchJobSwipeKey({ job_key: "", prompt_id: "p1" })).toBe("prompt:p1");
  });

  it("labels nav sections", () => {
    expect(workbenchSwipeSectionLabel("live")).toBe("Comfy Queue");
    expect(workbenchSwipeSectionLabel("pending")).toBe("Pending");
    expect(workbenchSwipeSectionLabel(null)).toBe("Jobs");
  });

  it("keeps swipe entries inside the opened section", () => {
    const items = [
      item({ job_key: "r1", status: "running" }),
      item({ job_key: "p1", status: "pending" }),
      item({ job_key: "e1", status: "error" }),
      item({ job_key: "d1", status: "complete" }),
    ];
    expect(buildWorkbenchSwipeEntries(items, "pending").map((e) => e.key)).toEqual(["p1"]);
    expect(buildWorkbenchSwipeEntries(items, "live").map((e) => e.key)).toEqual(["r1"]);
    expect(buildWorkbenchSwipeEntries(items).map((e) => e.key)).toEqual(["r1", "p1", "e1", "d1"]);
  });
});
