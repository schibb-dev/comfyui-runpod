import { describe, expect, it } from "vitest";
import { mergeStillTagWorkProducts } from "./stillTagWorkProduct";
import type { WorkProductItem } from "./types";

function job(partial: Partial<WorkProductItem> & { job_key: string }): WorkProductItem {
  return partial as WorkProductItem;
}

describe("mergeStillTagWorkProducts", () => {
  it("strips still-tag rows from the factory list until stubs arrive", () => {
    const jobs = [
      job({ job_key: "tag-old", work_kind: "still_tag" }),
      job({ job_key: "hourly__a", work_kind: "factory" }),
    ];
    expect(mergeStillTagWorkProducts(jobs, null).map((it) => it.job_key)).toEqual(["hourly__a"]);
  });

  it("prepends stub still-tag rows when the lazy query returns", () => {
    const jobs = [job({ job_key: "hourly__a" })];
    const tags = [job({ job_key: "still_tag_1", work_kind: "still_tag", still_tag_stub: true, status: "running" })];
    expect(mergeStillTagWorkProducts(jobs, tags).map((it) => it.job_key)).toEqual([
      "still_tag_1",
      "hourly__a",
    ]);
  });

  it("does not register queued SLA / queue-test batches as jobs", () => {
    const jobs = [job({ job_key: "hourly__a" })];
    const tags = [
      job({ job_key: "still_tag_queued", work_kind: "still_tag", status: "pending" }),
      job({ job_key: "still_tag_run", work_kind: "still_tag", status: "running" }),
    ];
    expect(mergeStillTagWorkProducts(jobs, tags).map((it) => it.job_key)).toEqual([
      "still_tag_run",
      "hourly__a",
    ]);
  });
});
