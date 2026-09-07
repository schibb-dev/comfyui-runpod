import { describe, expect, it } from "vitest";
import { patchCachedAppetite } from "./assetRatingsCache";
import {
  appetiteSortRank,
  filterWorkProductsByAppetite,
  workProductAppetiteKey,
} from "./workProductAppetite";
import type { WorkProductItem } from "./types";

describe("workProductAppetite", () => {
  it("treats missing output or ratings as unset", () => {
    expect(workProductAppetiteKey({} as WorkProductItem)).toBe("unset");
    expect(workProductAppetiteKey({ output_relpath: "og/clip.mp4" } as WorkProductItem)).toBe("unset");
  });

  it("reads the assigned appetite from the ratings cache", () => {
    patchCachedAppetite("og/marked.mp4", "more", "both");
    expect(workProductAppetiteKey({ output_relpath: "og/marked.mp4" } as WorkProductItem)).toBe("more");
  });

  it("filters to unmarked work products", () => {
    patchCachedAppetite("og/a.mp4", "fast_track", "both");
    const items = [
      { job_key: "marked", output_relpath: "og/a.mp4" },
      { job_key: "bare", output_relpath: "og/missing.mp4" },
    ] as WorkProductItem[];
    const off = new Set(["less", "neutral", "more", "fast_track"]);
    expect(filterWorkProductsByAppetite(items, off).map((it) => it.job_key)).toEqual(["bare"]);
  });

  it("ranks unset before marked appetites", () => {
    patchCachedAppetite("og/more.mp4", "more", "both");
    const unset = { output_relpath: "og/none.mp4" } as WorkProductItem;
    const more = { output_relpath: "og/more.mp4" } as WorkProductItem;
    expect(appetiteSortRank(unset)).toBeLessThan(appetiteSortRank(more));
  });
});
