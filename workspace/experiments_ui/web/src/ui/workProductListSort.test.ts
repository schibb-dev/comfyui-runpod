import { describe, expect, it } from "vitest";
import { workProductListBucket, workProductListSortRank } from "./workProductListSort";

describe("workProductListSort", () => {
  it("orders running, queued, pending, then complete and errors together", () => {
    expect(workProductListBucket("running")).toBe("running");
    expect(workProductListBucket("queued")).toBe("queued");
    expect(workProductListBucket("submitted")).toBe("queued");
    expect(workProductListBucket("pending")).toBe("pending");
    expect(workProductListBucket("editing")).toBe("pending");
    expect(workProductListBucket("")).toBe("pending");

    const done = [
      "complete",
      "completed",
      "deposited",
      "error",
      "failed",
      "interrupted",
      "abandoned",
      "unknown",
    ];
    expect(new Set(done.map(workProductListBucket))).toEqual(new Set(["done"]));

    const ranks = ["running", "queued", "pending", "complete", "error", "abandoned", "interrupted"].map(
      (status) => workProductListSortRank({ status }),
    );
    expect(ranks).toEqual([0, 1, 2, 3, 3, 3, 3]);
  });
});
