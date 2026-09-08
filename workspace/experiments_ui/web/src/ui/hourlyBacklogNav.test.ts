import { describe, expect, it } from "vitest";
import { backlogNavItems, backlogNextPicks, stepBacklogNav } from "./hourlyBacklogNav";
import type { HourlyChainBacklog, HourlyChainBacklogItem } from "./types";

function item(job_key: string, producer_family = "FB9-FaceBlast"): HourlyChainBacklogItem {
  return { job_key, producer_family, video_name: `${job_key}.mp4` };
}

const chain: HourlyChainBacklog = {
  id: "i2v_to_gex",
  next: item("a", "X-KNEEL-FB9"),
  next_picks: [item("a", "X-KNEEL-FB9"), item("b", "FB9-FaceBlast"), item("c", "BounceDanceA")],
  items: [
    item("a", "X-KNEEL-FB9"),
    item("b", "FB9-FaceBlast"),
    item("c", "BounceDanceA"),
    item("d", "FB9-FaceBlast"),
  ],
};

describe("hourlyBacklogNav", () => {
  it("prefers next_picks over the single next field", () => {
    expect(backlogNextPicks(chain).map((it) => it.job_key)).toEqual(["a", "b", "c"]);
    expect(backlogNextPicks({ next: item("z") }).map((it) => it.job_key)).toEqual(["z"]);
  });

  it("navigates next picks first, then the rest of the visible list", () => {
    expect(backlogNavItems(chain, "all").map((it) => it.job_key)).toEqual(["a", "b", "c", "d"]);
    expect(backlogNavItems(chain, "FB9-FaceBlast").map((it) => it.job_key)).toEqual([
      "a",
      "b",
      "c",
      "d",
    ]);
  });

  it("steps and wraps with arrow deltas", () => {
    const nav = backlogNavItems(chain, "all");
    expect(stepBacklogNav(nav, "a", 1)?.job_key).toBe("b");
    expect(stepBacklogNav(nav, "d", 1)?.job_key).toBe("a");
    expect(stepBacklogNav(nav, "a", -1)?.job_key).toBe("d");
    expect(stepBacklogNav(nav, null, 1)?.job_key).toBe("a");
    expect(stepBacklogNav(nav, null, -1)?.job_key).toBe("a");
  });
});
