import { describe, expect, it } from "vitest";
import { patchCachedAppetite } from "./assetRatingsCache";
import {
  APPETITE_FILTER_KEYS,
  appetiteSortRank,
  filterWorkProductsByAppetite,
  workProductAppetiteKey,
} from "./workProductAppetite";
import type { WorkProductItem } from "./types";

const unsetOnlyOff = new Set(APPETITE_FILTER_KEYS.filter((k) => k !== "unset"));

describe("workProductAppetite", () => {
  it("treats completed jobs with no rating as unset", () => {
    expect(workProductAppetiteKey({ status: "complete" } as WorkProductItem)).toBe("unset");
    expect(
      workProductAppetiteKey({ status: "complete", output_relpath: "og/clip.mp4" } as WorkProductItem),
    ).toBe("unset");
    expect(
      workProductAppetiteKey({ status: "deposited", output_relpath: "og/clip.mp4" } as WorkProductItem),
    ).toBe("unset");
  });

  it("does not count in-flight or failed jobs as unset", () => {
    expect(workProductAppetiteKey({} as WorkProductItem)).toBeNull();
    expect(workProductAppetiteKey({ status: "pending", output_relpath: "og/clip.mp4" } as WorkProductItem)).toBeNull();
    expect(workProductAppetiteKey({ status: "running", output_relpath: "og/clip.mp4" } as WorkProductItem)).toBeNull();
    expect(workProductAppetiteKey({ status: "queued" } as WorkProductItem)).toBeNull();
    expect(workProductAppetiteKey({ status: "interrupted" } as WorkProductItem)).toBeNull();
  });

  it("reads the assigned appetite from the ratings cache even before complete", () => {
    patchCachedAppetite("og/marked.mp4", "more", "both");
    expect(workProductAppetiteKey({ output_relpath: "og/marked.mp4" } as WorkProductItem)).toBe("more");
    expect(
      workProductAppetiteKey({ status: "running", output_relpath: "og/marked.mp4" } as WorkProductItem),
    ).toBe("more");
  });

  it("filters solo-unset to completed unmarked jobs only", () => {
    patchCachedAppetite("og/a.mp4", "fast_track", "both");
    const items = [
      { job_key: "marked", status: "complete", output_relpath: "og/a.mp4" },
      { job_key: "bare", status: "complete", output_relpath: "og/missing.mp4" },
      { job_key: "pending", status: "pending", output_relpath: "og/soon.mp4" },
    ] as WorkProductItem[];
    expect(filterWorkProductsByAppetite(items, unsetOnlyOff).map((it) => it.job_key)).toEqual(["bare"]);
  });

  it("keeps in-flight jobs when hiding unset", () => {
    const items = [
      { job_key: "bare", status: "complete", output_relpath: "og/missing.mp4" },
      { job_key: "pending", status: "pending" },
    ] as WorkProductItem[];
    expect(filterWorkProductsByAppetite(items, new Set(["unset"])).map((it) => it.job_key)).toEqual([
      "pending",
    ]);
  });

  it("treats completed remove as remove, not unset", () => {
    patchCachedAppetite("og/bin.mp4", "remove", "both");
    expect(
      workProductAppetiteKey({ status: "complete", output_relpath: "og/bin.mp4" } as WorkProductItem),
    ).toBe("remove");
  });

  it("hides remove-marked rows when the remove chip is off", () => {
    patchCachedAppetite("og/bin.mp4", "remove", "both");
    const items = [
      { job_key: "bin", status: "complete", output_relpath: "og/bin.mp4" },
      { job_key: "bare", status: "complete", output_relpath: "og/missing.mp4" },
    ] as WorkProductItem[];
    expect(filterWorkProductsByAppetite(items, new Set(["remove"])).map((it) => it.job_key)).toEqual([
      "bare",
    ]);
  });

  it("ranks completed unset before marked appetites, in-flight last", () => {
    patchCachedAppetite("og/more.mp4", "more", "both");
    const unset = { status: "complete", output_relpath: "og/none.mp4" } as WorkProductItem;
    const more = { status: "complete", output_relpath: "og/more.mp4" } as WorkProductItem;
    const pending = { status: "pending" } as WorkProductItem;
    expect(appetiteSortRank(unset)).toBeLessThan(appetiteSortRank(more));
    expect(appetiteSortRank(more)).toBeLessThan(appetiteSortRank(pending));
  });
});
