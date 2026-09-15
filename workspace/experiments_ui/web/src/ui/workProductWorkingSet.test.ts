import { describe, expect, it } from "vitest";
import { workProductIdentityLabel } from "./workProductWorkingSet";

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
