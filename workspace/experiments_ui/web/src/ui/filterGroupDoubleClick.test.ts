import { describe, expect, it } from "vitest";
import {
  nextOffSetForGroupDoubleClick,
  nextQueueSectionShowForDoubleClick,
} from "./filterGroupDoubleClick";

describe("nextOffSetForGroupDoubleClick", () => {
  const keys = ["extend", "replay", "derive"];

  it("solos the clicked key when the group is mixed or all-on", () => {
    expect([...nextOffSetForGroupDoubleClick(new Set(), keys, "extend")].sort()).toEqual([
      "derive",
      "replay",
    ]);
    expect([...nextOffSetForGroupDoubleClick(new Set(["derive"]), keys, "extend")].sort()).toEqual([
      "derive",
      "replay",
    ]);
  });

  it("restores the group when that key is already the only one on", () => {
    const solo = new Set(["replay", "derive"]);
    expect(nextOffSetForGroupDoubleClick(solo, keys, "extend").size).toBe(0);
  });

  it("solos a hidden key instead of restoring", () => {
    const soloExtend = new Set(["replay", "derive"]);
    expect([...nextOffSetForGroupDoubleClick(soloExtend, keys, "replay")].sort()).toEqual([
      "derive",
      "extend",
    ]);
  });
});

describe("nextQueueSectionShowForDoubleClick", () => {
  it("solos the section, then restores all on a second double-click", () => {
    const solo = nextQueueSectionShowForDoubleClick(
      { running: true, pending: true, history: true },
      "running",
      "all",
    );
    expect(solo).toEqual({ running: true, pending: false, history: false });
    expect(nextQueueSectionShowForDoubleClick(solo, "running", "all")).toEqual({
      running: true,
      pending: true,
      history: true,
    });
  });

  it("does not treat the errors filter as an already-solo section", () => {
    expect(
      nextQueueSectionShowForDoubleClick(
        { running: false, pending: false, history: true },
        "history",
        "errors",
      ),
    ).toEqual({ running: false, pending: false, history: true });
  });
});
