import { describe, expect, it } from "vitest";
import { destinationForWhen, isPendingQueueItem, pendingQueueIndex } from "./workProductPendingQueue";
import type { WorkProductItem } from "./types";

function item(partial: Partial<WorkProductItem>): WorkProductItem {
  return { job_key: "k", ...partial };
}

describe("workProductPendingQueue", () => {
  it("maps submit when onto destination", () => {
    expect(destinationForWhen("queue")).toEqual({
      destination: "pending",
      pending_position: "append",
      front: false,
    });
    expect(destinationForWhen("queue_next")).toEqual({
      destination: "pending",
      pending_position: "front",
      front: false,
    });
    expect(destinationForWhen("now")).toEqual({ destination: "comfy", front: true });
    expect(destinationForWhen("later")).toEqual({ destination: "comfy", front: false });
  });

  it("treats pending without prompt_id as queue inventory", () => {
    expect(isPendingQueueItem(item({ status: "pending" }))).toBe(true);
    expect(isPendingQueueItem(item({ status: "editing" }))).toBe(true);
    expect(isPendingQueueItem(item({ status: "queued", prompt_id: "p" }))).toBe(false);
    expect(isPendingQueueItem(item({ status: "error", pending_index: 3, pending_count: 13 }))).toBe(false);
    expect(isPendingQueueItem(item({ status: "failed" }))).toBe(false);
    expect(isPendingQueueItem(item({ status: "deposited" }))).toBe(false);
  });

  it("orders by pending_index then rank", () => {
    expect(pendingQueueIndex(item({ pending_index: 2, pending_rank: 9 }))).toBe(2);
    expect(pendingQueueIndex(item({ pending_rank: 4 }))).toBe(4);
  });
});
