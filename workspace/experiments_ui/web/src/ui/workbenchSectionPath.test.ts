import { describe, expect, it, afterEach } from "vitest";
import { workbenchHref } from "./discoveryDeepLink";
import {
  parseWorkbenchSection,
  sectionAfterToggle,
  workbenchSectionHref,
  workbenchSectionIsActive,
  workbenchSectionPath,
} from "./workbenchSectionPath";

describe("workbenchSectionPath", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("parses section slugs and the bare workbench index", () => {
    expect(parseWorkbenchSection("/workbench")).toBeNull();
    expect(parseWorkbenchSection("/workbench/")).toBeNull();
    expect(parseWorkbenchSection("/workbench/live")).toBe("live");
    expect(parseWorkbenchSection("/workbench/pending")).toBe("pending");
    expect(parseWorkbenchSection("/workbench/errors")).toBe("error");
    expect(parseWorkbenchSection("/workbench/error")).toBe("error");
    expect(parseWorkbenchSection("/workbench/done")).toBe("done");
    expect(parseWorkbenchSection("/work-products/pending")).toBe("pending");
    expect(parseWorkbenchSection("/workbench/nope")).toBeNull();
  });

  it("builds canonical paths and preserves search", () => {
    expect(workbenchSectionPath("error")).toBe("/workbench/errors");
    expect(workbenchSectionPath(null)).toBe("/workbench");
    expect(workbenchSectionHref("pending", "?job=abc", "")).toBe("/workbench/pending?job=abc");
    expect(workbenchSectionIsActive("error", "/workbench/error")).toBe(true);
    expect(workbenchSectionIsActive("error", "/work-products/errors")).toBe(true);
    expect(workbenchSectionIsActive("pending", "/workbench/live")).toBe(false);
  });

  it("names the section just opened and clears it when that section closes", () => {
    expect(sectionAfterToggle(null, "pending", true)).toBe("pending");
    expect(sectionAfterToggle("live", "pending", true)).toBe("pending");
    expect(sectionAfterToggle("pending", "pending", false)).toBeNull();
    expect(sectionAfterToggle("live", "pending", false)).toBe("live");
  });

  it("keeps the current section when workbench query links are rebuilt", () => {
    window.history.replaceState(null, "", "/workbench/errors?set=advance");
    expect(workbenchHref({ jobKey: "abc" })).toBe("/workbench/errors?job=abc");
    expect(workbenchHref({ jobKey: "abc", section: null })).toBe("/workbench?job=abc");
  });
});
