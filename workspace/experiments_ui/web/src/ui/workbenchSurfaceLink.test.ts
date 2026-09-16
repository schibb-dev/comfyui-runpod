import { describe, expect, it } from "vitest";
import { resolveWorkbenchSurfaceHref } from "./workbenchSurfaceLink";

describe("resolveWorkbenchSurfaceHref", () => {
  it("prefers job key over media", () => {
    expect(
      resolveWorkbenchSurfaceHref({
        jobKey: "JOB_00001",
        relpath: "output/foo.mp4",
      }),
    ).toBe("/workbench?job=JOB_00001&media=output%2Ffoo.mp4");
  });

  it("falls back to media focus", () => {
    expect(resolveWorkbenchSurfaceHref({ relpath: "input/abc.jpeg" })).toBe(
      "/workbench?media=input%2Fabc.jpeg",
    );
  });

  it("returns null without identity", () => {
    expect(resolveWorkbenchSurfaceHref({})).toBeNull();
  });
});
