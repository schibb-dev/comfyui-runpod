import { describe, expect, it } from "vitest";
import {
  SHOW_LIBRARY_WORKPRODUCT_ACTION,
  submitInputHref,
  submitOutputHref,
  workProductOutputRelpath,
} from "./workProductSubmitLinks";

describe("workProductSubmitLinks", () => {
  it("keeps Library workproduct action stubbed off", () => {
    expect(SHOW_LIBRARY_WORKPRODUCT_ACTION).toBe(false);
  });

  it("prefers video then image for output relpath (no input fallback)", () => {
    expect(
      workProductOutputRelpath({
        primaryVideoRelpath: "og/out.mp4",
        primaryImageRelpath: "og/out.png",
      }),
    ).toBe("og/out.mp4");
    expect(
      workProductOutputRelpath({
        primaryImageRelpath: "og/out.png",
      }),
    ).toBe("og/out.png");
    expect(workProductOutputRelpath({})).toBeNull();
  });

  it("builds Submit output with from_job and advance.extend", () => {
    const href = submitOutputHref({
      mediaRelpath: "og/a.mp4",
      fromJob: "job-1",
      origin: "queue",
    });
    expect(href).toContain("/submit?");
    expect(href).toContain("media=og%2Fa.mp4");
    expect(href).toContain("from_job=job-1");
    expect(href).toContain("step=advance.extend");
    expect(href).toContain("origin=queue");
  });

  it("builds Submit input without from_job; stills omit step", () => {
    const video = submitInputHref({
      mediaRelpath: "og/src.mp4",
      origin: "queue",
    });
    expect(video).toContain("media=og%2Fsrc.mp4");
    expect(video).not.toContain("from_job=");
    expect(video).toContain("step=advance.extend");

    const still = submitInputHref({
      mediaRelpath: "input/SSS123.jpeg",
      origin: "queue",
    });
    expect(still).toContain("media=input%2FSSS123.jpeg");
    expect(still).not.toContain("from_job=");
    expect(still).not.toContain("step=");
  });

  it("returns null when media is missing", () => {
    expect(submitOutputHref({ mediaRelpath: null })).toBeNull();
    expect(submitInputHref({ mediaRelpath: "  " })).toBeNull();
  });
});
