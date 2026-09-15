import { describe, expect, it } from "vitest";
import {
  FOLLOW_UP_JOB_PREFIX,
  workProductFromFocusedMedia,
  workProductIdentityLabel,
} from "./workProductWorkingSet";
import { filesUrlForRelpath, isMediaOutputOfJob } from "./workProductMediaFocus";

describe("workProductFromFocusedMedia", () => {
  const rel = "og/2026-03-10/FB9_GEX_2026-03-10_00005.mp4";

  it("builds a playable follow-up row for a media path with no producer job", () => {
    const item = workProductFromFocusedMedia(rel);
    expect(item).toEqual({
      job_key: FOLLOW_UP_JOB_PREFIX + rel,
      output_relpath: rel,
      output_url: filesUrlForRelpath(rel),
      status: "complete",
    });
    expect(isMediaOutputOfJob(item!, rel)).toBe(true);
  });

  it("returns null for an empty path", () => {
    expect(workProductFromFocusedMedia("")).toBeNull();
  });
});

describe("workProductIdentityLabel", () => {
  it("uses exp_id / run_id for experiment rows", () => {
    expect(
      workProductIdentityLabel({
        job_key: "exp__x-kneel-bra-720p-i2v__run_004",
        exp_id: "x-kneel-bra-720p-i2v",
        run_id: "run_004",
      }),
    ).toBe("x-kneel-bra-720p-i2v / run_004");
  });

  it("falls back to job_key when experiment ids are missing", () => {
    expect(workProductIdentityLabel({ job_key: "hourly__prompt_profile-abc" })).toBe(
      "hourly__prompt_profile-abc",
    );
  });
});
