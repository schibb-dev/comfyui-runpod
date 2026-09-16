import { describe, expect, it } from "vitest";
import type { WorkProductItem } from "./types";
import {
  workProductKindId,
  workProductKindOf,
  workProductPreviewMode,
  workProductRunningLiveDespiteOutput,
  workProductShowFactoryChrome,
} from "./workProductKind";

function baseItem(overrides: Partial<WorkProductItem> = {}): WorkProductItem {
  return {
    job_key: "JOB_00001",
    family_slug: "FB9_GEX",
    status: "complete",
    ...overrides,
  } as WorkProductItem;
}

describe("workProductKind", () => {
  it("resolves factory by default", () => {
    const item = baseItem();
    expect(workProductKindId(item)).toBe("factory");
    expect(workProductShowFactoryChrome(item)).toBe(true);
    expect(workProductPreviewMode(item)).toBe("output_video");
    expect(workProductRunningLiveDespiteOutput(item)).toBe(false);
  });

  it("resolves still_tag from work_kind", () => {
    const item = baseItem({
      work_kind: "still_tag",
      still_tag_run_id: "still_tag_abc",
      display_title: "Still tag · 3/200 tagged",
      output_url: "/files/input/foo.jpeg",
      status: "running",
    });
    expect(workProductKindId(item)).toBe("still_tag");
    expect(workProductKindOf(item).displayTitle(item)).toBe("Still tag · 3/200 tagged");
    expect(workProductShowFactoryChrome(item)).toBe(false);
    expect(workProductPreviewMode(item)).toBe("still_tag");
    expect(workProductRunningLiveDespiteOutput(item)).toBe(true);
  });

  it("resolves still_tag from construction.step fallback", () => {
    const item = baseItem({
      construction: { step: "still_tag" },
    });
    expect(workProductKindId(item)).toBe("still_tag");
  });
});
