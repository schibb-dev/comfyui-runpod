import { describe, expect, it } from "vitest";
import type { WorkProductItem } from "./types";
import {
  filterWorkProductsByMedia,
  focusedGoneMessage,
  inferJobKeyFromMediaPath,
  isMediaOutputOfJob,
  mediaFileCandidates,
  mediaFocusLabel,
  pickBestMediaMatch,
  workProductMatchesMedia,
  workProductMediaRelation,
} from "./workProductMediaFocus";

const MEDIA =
  "og/2026-09-04/hourly/hourly__pp-catalog-default__still-ff55a69e40c5268ccccfc6b3b9883b8581085bacad8e383e0e21c818aaa6b63b__00_20260904153504_f1_00001";

const PRODUCER_KEY =
  "hourly__pp-catalog-default__still-ff55a69e40c5268ccccfc6b3b9883b8581085bacad8e383e0e21c818aaa6b63b__00_20260904153504_f1";

function item(partial: Partial<WorkProductItem> & { job_key: string }): WorkProductItem {
  return partial as WorkProductItem;
}

describe("inferJobKeyFromMediaPath", () => {
  it("strips the Comfy _00001 suffix from an hourly stem", () => {
    expect(inferJobKeyFromMediaPath(MEDIA)).toBe(PRODUCER_KEY);
    expect(inferJobKeyFromMediaPath(`${MEDIA}.mp4`)).toBe(PRODUCER_KEY);
  });

  it("returns null when there is no batch suffix", () => {
    expect(inferJobKeyFromMediaPath("og/demo/plain-name.mp4")).toBeNull();
  });
});

describe("media matching", () => {
  const producer = item({
    job_key: PRODUCER_KEY,
    output_relpath: `${MEDIA}.mp4`,
    status: "complete",
  });
  const consumer = item({
    job_key: `hourly__pp-catalog-faceblast-extend__src-${PRODUCER_KEY}__00_20260907200806_f0`,
    status: "interrupted",
    bindings: {
      video: { relpath: `${MEDIA}.mp4`, basename: `${MEDIA.split("/").pop()}.mp4` },
    },
  });
  const liveUnrelated = item({
    job_key: "hourly__pp-catalog-default__still-other__00_20260908114053_f0",
    status: "running",
    live_from_comfy: true,
    prompt_id: "pid-live",
  });

  it("matches producer by output path and consumer by binding", () => {
    expect(workProductMatchesMedia(producer, MEDIA)).toBe(true);
    expect(workProductMatchesMedia(consumer, MEDIA)).toBe(true);
    expect(workProductMatchesMedia(liveUnrelated, MEDIA)).toBe(false);
    expect(isMediaOutputOfJob(producer, MEDIA)).toBe(true);
    expect(isMediaOutputOfJob(consumer, MEDIA)).toBe(false);
    expect(workProductMediaRelation(producer, MEDIA)).toBe("output");
    expect(workProductMediaRelation(consumer, MEDIA)).toBe("source");
  });

  it("matches a producer by inferred job key even when output_relpath is missing", () => {
    const orphan = item({ job_key: PRODUCER_KEY, status: "complete" });
    expect(workProductMatchesMedia(orphan, MEDIA)).toBe(true);
    expect(isMediaOutputOfJob(orphan, MEDIA)).toBe(true);
    expect(pickBestMediaMatch([consumer, orphan], MEDIA)?.job_key).toBe(PRODUCER_KEY);
  });

  it("ranks the producing job ahead of source users", () => {
    expect(pickBestMediaMatch([liveUnrelated, consumer, producer], MEDIA)?.job_key).toBe(PRODUCER_KEY);
    expect(pickBestMediaMatch([consumer], MEDIA)?.job_key).toBe(consumer.job_key);
  });

  it("filters strictly — live jobs that do not reference the media drop out", () => {
    const rows = filterWorkProductsByMedia([liveUnrelated, consumer, producer], MEDIA);
    expect(rows.map((r) => r.job_key)).toEqual([consumer.job_key, producer.job_key]);
  });

  it("can keep only the producing job (focused clip list)", () => {
    const rows = filterWorkProductsByMedia([liveUnrelated, consumer, producer], MEDIA, {
      producersOnly: true,
    });
    expect(rows.map((r) => r.job_key)).toEqual([producer.job_key]);
  });

  it("labels the focus chip with the basename", () => {
    expect(mediaFocusLabel(MEDIA)).toBe(
      "hourly__pp-catalog-default__still-ff55a69e40c5268ccccfc6b3b9883b8581085bacad8e383e0e21c818aaa6b63b__00_20260904153504_f1_00001",
    );
  });

  it("probes mp4/png when the media stem has no extension", () => {
    expect(mediaFileCandidates(MEDIA)).toEqual([MEDIA, `${MEDIA}.mp4`, `${MEDIA}.png`]);
    expect(mediaFileCandidates(`${MEDIA}.mp4`)).toEqual([`${MEDIA}.mp4`]);
  });
});

describe("focusedGoneMessage", () => {
  it("uses deleted vs does-not-exist copy", () => {
    expect(focusedGoneMessage("deleted")).toBe("Item deleted");
    expect(focusedGoneMessage("missing")).toBe("Item does not exist");
  });
});
