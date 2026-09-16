import { describe, expect, it } from "vitest";
import { compareQueueIndex, sortQueueHistoryItems, sortQueueLiveItems } from "./queueMonitorSort";

describe("queueMonitorSort", () => {
  it("sorts live queue ascending by queue_index (next to run first)", () => {
    const items = [
      { prompt_id: "c", queue_index: 8 },
      { prompt_id: "a", queue_index: -12 },
      { prompt_id: "b", queue_index: 2 },
    ];
    expect(sortQueueLiveItems(items, "queue_index").map((it) => it.prompt_id)).toEqual(["a", "b", "c"]);
  });

  it("compareQueueIndex treats missing index as last", () => {
    expect(compareQueueIndex({ queue_index: 3 }, { queue_index: null })).toBeLessThan(0);
    expect(compareQueueIndex({ queue_index: null }, { queue_index: 3 })).toBeGreaterThan(0);
  });

  it("defaults history to newest change", () => {
    const items = [
      { prompt_id: "old", changed_at: "2026-01-01T00:00:00Z" },
      { prompt_id: "new", changed_at: "2026-01-02T00:00:00Z" },
    ];
    expect(sortQueueHistoryItems(items, "newest").map((it) => it.prompt_id)).toEqual(["new", "old"]);
  });
});
