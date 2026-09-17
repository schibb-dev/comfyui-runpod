import { describe, expect, it } from "vitest";
import { filterStillTagResults, stillTagResultTagGroups, type StillTagResultItem } from "./stillTagResults";

const SAMPLE: StillTagResultItem[] = [
  { content_id: "a", status: "done", tags: ["1girl"] },
  { content_id: "b", status: "error", error_message: "timeout" },
  { content_id: "c", status: "pending" },
  { content_id: "d", status: "missing", error_message: "missing file" },
];

describe("stillTagResults", () => {
  it("filters by status", () => {
    expect(filterStillTagResults(SAMPLE, "done").map((x) => x.content_id)).toEqual(["a"]);
    expect(filterStillTagResults(SAMPLE, "errors").map((x) => x.content_id)).toEqual(["b", "d"]);
    expect(filterStillTagResults(SAMPLE, "pending").map((x) => x.content_id)).toEqual(["c"]);
    expect(filterStillTagResults(SAMPLE, "all")).toHaveLength(4);
  });

  it("merges editorial and auto into effective tags", () => {
    const groups = stillTagResultTagGroups({
      content_id: "x",
      status: "done",
      provisional_tags: ["1girl", "solo"],
      editorial_tags: ["portrait"],
      effective_tags: ["portrait", "1girl", "solo"],
    });
    expect(groups.auto).toEqual(["1girl", "solo"]);
    expect(groups.editorial).toEqual(["portrait"]);
    expect(groups.effective).toEqual(["portrait", "1girl", "solo"]);
  });
});
