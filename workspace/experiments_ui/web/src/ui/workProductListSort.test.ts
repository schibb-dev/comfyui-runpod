import { describe, expect, it } from "vitest";
import {
  compareLiveComfyQueue,
  groupWorkProductsByNavSection,
  isHourlyWorkProduct,
  sortWorkProductList,
  workProductListBucket,
  workProductListSortRank,
  workProductNavSection,
  workProductNavSectionBadges,
  workProductsInOpenNavSections,
} from "./workProductListSort";

describe("workProductListSort", () => {
  it("orders running, queued, pending, errors, then complete", () => {
    expect(workProductListBucket("running")).toBe("running");
    expect(workProductListBucket("queued")).toBe("queued");
    expect(workProductListBucket("submitted")).toBe("queued");
    expect(workProductListBucket("pending")).toBe("pending");
    expect(workProductListBucket("editing")).toBe("pending");
    expect(workProductListBucket("")).toBe("pending");
    expect(workProductListBucket("error")).toBe("error");
    expect(workProductListBucket("failed")).toBe("error");
    expect(workProductListBucket("interrupted")).toBe("error");
    expect(workProductListBucket("abandoned")).toBe("error");
    expect(workProductListBucket("complete")).toBe("done");
    expect(workProductListBucket("deposited")).toBe("done");

    const ranks = ["running", "queued", "pending", "error", "interrupted", "complete"].map((status) =>
      workProductListSortRank({ status }),
    );
    expect(ranks).toEqual([0, 1, 2, 3, 3, 4]);
  });

  it("groups Comfy live/queue, pending, errors, and completed", () => {
    expect(workProductNavSection("running")).toBe("live");
    expect(workProductNavSection("queued")).toBe("live");
    expect(workProductNavSection("pending")).toBe("pending");
    expect(workProductNavSection("editing")).toBe("pending");
    expect(workProductNavSection("error")).toBe("error");
    expect(workProductNavSection("interrupted")).toBe("error");
    expect(workProductNavSection("abandoned")).toBe("error");
    expect(workProductNavSection("complete")).toBe("done");

    const grouped = groupWorkProductsByNavSection([
      { status: "complete" },
      { status: "running" },
      { status: "pending" },
      { status: "queued" },
      { status: "error" },
      { status: "editing" },
    ]);
    expect(grouped.map((sec) => [sec.id, sec.items.map((it) => it.status)])).toEqual([
      ["live", ["running", "queued"]],
      ["pending", ["pending", "editing"]],
      ["error", ["error"]],
      ["done", ["complete"]],
    ]);
  });

  it("hides empty nav sections and skips collapsed ones for keyboard order", () => {
    expect(groupWorkProductsByNavSection([{ status: "pending" }]).map((sec) => sec.id)).toEqual(["pending"]);
    const items = [{ status: "running" }, { status: "pending" }, { status: "error" }, { status: "complete" }];
    expect(
      workProductsInOpenNavSections(items, { live: true, pending: true, error: true, done: false }).map(
        (it) => it.status,
      ),
    ).toEqual(["running", "pending", "error"]);
  });

  it("builds section header badges that split mixed kinds", () => {
    expect(workProductNavSectionBadges("live", [{ status: "running" }, { status: "queued" }, { status: "submitted" }])).toEqual([
      { key: "running", count: 1, label: "live", tone: "running" },
      { key: "queued", count: 2, label: "queued", tone: "queued" },
    ]);
    expect(
      workProductNavSectionBadges("pending", [
        { status: "pending", is_hourly: true },
        { status: "pending", is_hourly: false },
        { status: "editing", job_key: "hourly__held" },
      ]),
    ).toEqual([
      { key: "hourly", count: 2, label: "hourly", tone: "hourly" },
      { key: "custom", count: 1, label: "custom", tone: "custom" },
    ]);
    expect(isHourlyWorkProduct({ is_hourly: false, job_key: "hourly__nope" })).toBe(false);
    expect(isHourlyWorkProduct({ job_key: "hourly__yes" })).toBe(true);
    expect(
      workProductNavSectionBadges("error", [{ status: "error" }, { status: "failed" }, { status: "interrupted" }]),
    ).toEqual([
      { key: "error", count: 2, label: "err", tone: "error" },
      { key: "interrupted", count: 1, label: "int", tone: "error" },
    ]);
    expect(workProductNavSectionBadges("error", [{ status: "error" }])).toEqual([
      { key: "error", count: 1, label: "", tone: "error" },
    ]);
    expect(workProductNavSectionBadges("done", [{ status: "complete" }, { status: "deposited" }])).toEqual([
      { key: "ok", count: 2, label: "", tone: "ok" },
    ]);
  });

  it("orders live Comfy rows by ascending queue_index", () => {
    expect(
      compareLiveComfyQueue({ queue_index: 5, job_key: "b" }, { queue_index: -2, job_key: "a" }),
    ).toBeGreaterThan(0);
    const sorted = sortWorkProductList(
      [
        { job_key: "q3", status: "queued", queue_index: 8 },
        { job_key: "q1", status: "queued", queue_index: -10 },
        { job_key: "p2", status: "pending", pending_index: 1 },
        { job_key: "p1", status: "pending", pending_index: 0 },
        { job_key: "done", status: "complete", created_at: "2026-01-01T00:00:00Z" },
      ],
      "created_desc",
      () => 0,
    );
    expect(sorted.map((it) => it.job_key)).toEqual(["q1", "q3", "p1", "p2", "done"]);
  });
});
