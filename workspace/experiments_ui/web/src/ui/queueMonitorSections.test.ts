import { describe, expect, it } from "vitest";
import {
  buildQueueSwipeEntries,
  filterQueueSwipeEntries,
  queueComfyItemTitle,
  queueFocusDetailMode,
  queueHistorySectionHint,
  queueItemFocusKey,
  queueRunningSectionHint,
  queueSwipeCommitIndex,
  queueSwipePageIndex,
  queueSwipeSectionLabel,
  queueWaitingSectionHint,
} from "./queueMonitorSections";

describe("queueItemFocusKey", () => {
  it("joins prompt id and job key", () => {
    expect(queueItemFocusKey({ prompt_id: "p1", job_key: "JOB" })).toBe("p1|JOB");
    expect(queueItemFocusKey({ prompt_id: "p1" })).toBe("p1|");
  });
});

describe("buildQueueSwipeEntries", () => {
  it("orders running, then waiting, then history and drops dup keys", () => {
    const keys = buildQueueSwipeEntries(
      [{ prompt_id: "r1", external: false }],
      [{ prompt_id: "w1", external: false }, { prompt_id: "r1", external: false }],
      [{ prompt_id: "h1", status: "success", outputs: {} }],
    ).map((e) => e.key);
    expect(keys).toEqual(["r1|", "w1|", "h1|"]);
  });

  it("keeps vertical swipe inside the opened section", () => {
    const built = buildQueueSwipeEntries(
      [{ prompt_id: "r1", external: false }],
      [{ prompt_id: "w1", external: false }],
      [{ prompt_id: "h1", status: "success", outputs: {} }],
      "waiting",
    );
    expect(built.map((e) => e.section)).toEqual(["waiting"]);
    expect(filterQueueSwipeEntries(built, null)).toEqual(built);
    expect(queueSwipeSectionLabel("waiting")).toBe("Waiting");
  });
});

describe("queueSwipeCommitIndex", () => {
  it("snaps a short swipe-up to the next job", () => {
    expect(queueSwipeCommitIndex(0, 9, -40, 721)).toBe(1);
    expect(queueSwipeCommitIndex(1, 9, 40, 721)).toBe(0);
    expect(queueSwipeCommitIndex(0, 9, -10, 721)).toBe(0);
    expect(queueSwipeCommitIndex(8, 9, -80, 721)).toBe(8);
  });

  it("reads the page from scroll position after a flick", () => {
    expect(queueSwipePageIndex(0, 721, 10)).toBe(0);
    expect(queueSwipePageIndex(721, 721, 10)).toBe(1);
    expect(queueSwipePageIndex(721 * 4 + 20, 721, 10)).toBe(4);
    expect(queueSwipePageIndex(721 * 20, 721, 10)).toBe(9);
  });
});

describe("queueFocusDetailMode", () => {
  it("loads the focused job and neighbors", () => {
    expect(queueFocusDetailMode(3, 3)).toBe("full");
    expect(queueFocusDetailMode(2, 3)).toBe("full");
    expect(queueFocusDetailMode(4, 3)).toBe("full");
    expect(queueFocusDetailMode(0, 3)).toBe("off");
  });
});

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
