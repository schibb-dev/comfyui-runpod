import { describe, expect, it } from "vitest";
import { parseStillTagFilter, serializeStillTagFilter, stillTagFilterHas, toggleStillTagFilter } from "./stillTagFilter";

describe("stillTagFilter", () => {
  it("parses comma lists and lowercases", () => {
    expect(parseStillTagFilter("1girl, Sitting; 1girl")).toEqual(["1girl", "sitting"]);
  });

  it("toggles add then remove", () => {
    const added = toggleStillTagFilter([], "Portrait");
    expect(added).toEqual(["portrait"]);
    expect(stillTagFilterHas(added, "portrait")).toBe(true);
    expect(toggleStillTagFilter(added, "portrait")).toEqual([]);
  });

  it("serializes for the query string", () => {
    expect(serializeStillTagFilter(["Sitting", "1girl"])).toBe("sitting,1girl");
  });
});
