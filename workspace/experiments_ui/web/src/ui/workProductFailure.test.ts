import { describe, expect, it } from "vitest";
import type { WorkProductItem } from "./types";
import { failurePrimaryLabel, workProductFailure, workProductFlowEvents } from "./workProductFailure";

function item(partial: Partial<WorkProductItem>): WorkProductItem {
  return { job_key: "job-1", ...partial } as WorkProductItem;
}

describe("workProductFailure", () => {
  it("classifies submit-missed NameError as return-to-pending", () => {
    const fail = workProductFailure(
      item({
        status: "error",
        error: "name 'ensure_comfy_submit_ready' is not defined",
        job_path: "/jobs/a.job.json",
      }),
    );
    expect(fail?.kind).toBe("submit_missed");
    expect(fail?.primary).toBe("retry_same");
    expect(failurePrimaryLabel(fail!)).toBe("Return to pending");
  });

  it("classifies interrupted Comfy runs as replay", () => {
    const fail = workProductFailure(
      item({
        status: "error",
        prompt_id: "pid-1",
        error: "SamplerCustomAdvanced · #150: Interrupted",
        job_path: "/jobs/a.job.json",
      }),
    );
    expect(fail?.kind).toBe("interrupted");
    expect(fail?.primary).toBe("replay");
    expect(failurePrimaryLabel(fail!)).toBe("Replay to pending");
  });

  it("trusts the server payload when present", () => {
    const fail = workProductFailure(
      item({
        status: "error",
        prompt_id: "pid-1",
        error: "CUDA OOM",
        job_path: "/jobs/a.job.json",
        failure: {
          kind: "comfy_failed",
          headline: "Failed during the Comfy run",
          primary: "replay",
          actions: ["replay", "edit", "discard"],
        },
      }),
    );
    expect(fail?.kind).toBe("comfy_failed");
  });

  it("history stubs can only archive", () => {
    const fail = workProductFailure(
      item({
        status: "error",
        prompt_id: "pid-hist",
        history_from_comfy: true,
        error: "Interrupted",
      }),
    );
    expect(fail?.primary).toBe("discard");
    expect(fail?.actions).toEqual(["discard"]);
    expect(fail?.can_retry_same).toBe(false);
  });

  it("returns null for complete jobs", () => {
    expect(workProductFailure(item({ status: "complete" }))).toBeNull();
  });

  it("synthesizes a failure timeline event for error jobs with no flow_events", () => {
    const events = workProductFlowEvents(
      item({
        status: "error",
        error: "name 'ensure_comfy_submit_ready' is not defined",
        job_path: "/jobs/a.job.json",
      }),
    );
    expect(events[0]?.action).toBe("submit_failed");
    expect(events[0]?.ok).toBe(false);
  });
});
