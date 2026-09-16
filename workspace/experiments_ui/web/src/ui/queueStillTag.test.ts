import { describe, expect, it } from "vitest";
import {
  isQueueStillTagItem,
  queueStillTagGlanceRows,
  queueStillTagStatusLabel,
  queueStillTagTitle,
} from "./queueStillTag";

describe("queueStillTag", () => {
  it("detects still-tag queue rows", () => {
    expect(isQueueStillTagItem({ work_kind: "still_tag", glance: null })).toBe(true);
    expect(isQueueStillTagItem({ work_kind: null, glance: { workflow_kind: "still_tag" } })).toBe(true);
    expect(isQueueStillTagItem({ work_kind: null, glance: { workflow_kind: "image" } })).toBe(false);
  });

  it("prefers API display_title", () => {
    expect(
      queueStillTagTitle({
        display_title: "Still tag · 3/40 tagged",
        glance: { step: "ignored" },
        prompt_id: "p1",
      }),
    ).toBe("Still tag · 3/40 tagged");
  });

  it("builds glance rows for batch + still", () => {
    const rows = queueStillTagGlanceRows({
      still_tag_run_id: "still_tag_abc123",
      content_id: "deadbeef",
      glance: { step: "2/10 tagged" },
    });
    expect(rows.map((r) => r.label)).toEqual(["Batch", "Run", "Still"]);
  });

  it("uses tagging status labels", () => {
    expect(queueStillTagStatusLabel("running")).toBe("tagging");
    expect(queueStillTagStatusLabel("waiting")).toBe("queued");
    expect(queueStillTagStatusLabel("history", "complete")).toBe("done");
  });
});
