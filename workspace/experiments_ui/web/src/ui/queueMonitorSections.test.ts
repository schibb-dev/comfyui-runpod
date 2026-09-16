import { describe, expect, it } from "vitest";
import {
  queueComfyItemTitle,
  queueHistorySectionHint,
  queueRunningSectionHint,
  queueWaitingSectionHint,
} from "./queueMonitorSections";

describe("queueMonitorSections", () => {
  it("summarizes running section", () => {
    expect(queueRunningSectionHint([])).toMatch(/idle/i);
    expect(
      queueRunningSectionHint([
        { glance: { family_slug: "FB9-FaceBlast" }, prompt_id: "p1", external: false },
      ]),
    ).toBe("Active now · FB9-FaceBlast");
  });

  it("summarizes waiting section with next job", () => {
    expect(
      queueWaitingSectionHint([
        { glance: { family_slug: "X-KNEEL-FB9" }, prompt_id: "a", external: false },
        { workflow_name: "other", prompt_id: "b", external: true },
      ]),
    ).toBe("2 waiting · next: X-KNEEL-FB9 · 1 non-factory");
  });

  it("titles still-tag rows from display_title", () => {
    expect(
      queueComfyItemTitle({
        work_kind: "still_tag",
        display_title: "Still tag · 3/40 tagged",
        glance: { workflow_kind: "still_tag", family_slug: "still-tag" },
        prompt_id: "p1",
      }),
    ).toBe("Still tag · 3/40 tagged");
  });

  it("summarizes history with error count", () => {
    expect(queueHistorySectionHint([], 0, false)).toMatch(/no recent/i);
    expect(
      queueHistorySectionHint(
        [{ glance: { family_slug: "FB9" }, prompt_id: "h1", status: "success", outputs: {} }],
        2,
        false,
      ),
    ).toBe("1 recent · 2 failed");
    expect(queueHistorySectionHint([], 0, true)).toMatch(/no errors/i);
  });
});
