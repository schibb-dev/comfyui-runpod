import { describe, expect, it } from "vitest";
import type { WorkProductItem } from "./types";
import {
  filterWorkProductsByFamily,
  filterWorkProductsByOverride,
  formatOverrideLoraLine,
  formatOverrideParamLine,
  jobHasAnyOverride,
  jobMatchesOverrideNeed,
  jobOverrideFlags,
  jobOverrideKinds,
  jobOverrideSearchLabel,
} from "./jobOverrides";
import { workProductMediaHaystack } from "./workProductMediaFocus";

function item(partial: Partial<WorkProductItem> & { job_key: string }): WorkProductItem {
  return partial as WorkProductItem;
}

describe("jobOverrideFlags", () => {
  it("uses stamped overrides when present", () => {
    const flags = jobOverrideFlags(item({ job_key: "a", overrides: { loras: true, stack: true } }));
    expect(flags).toEqual({ prompt: false, loras: true, params: false, stack: true });
    expect(jobOverrideKinds(item({ job_key: "a", overrides: { loras: true } }))).toEqual(["loras"]);
  });

  it("treats prompt snowflake / glance as an override", () => {
    expect(jobOverrideFlags(item({ job_key: "a", prompt_profile: { snowflake: true } })).prompt).toBe(true);
    expect(jobOverrideFlags({ glance: { prompt_snowflake: true } }).prompt).toBe(true);
  });

  it("ignores seed-only param diffs", () => {
    const flags = jobOverrideFlags(
      item({
        job_key: "a",
        params_profile: { snowflake: false, diffs: { seed: { job: 1, seed: 2 } } },
      }),
    );
    expect(flags.params).toBe(false);
    expect(jobHasAnyOverride(item({ job_key: "a", params_profile: { snowflake: false, diffs: { seed: { job: 1, seed: 2 } } } }))).toBe(
      false,
    );
  });

  it("counts non-seed param snowflakes", () => {
    expect(
      jobOverrideFlags(
        item({
          job_key: "a",
          params_profile: { snowflake: true, diffs: { frames: { job: 81, seed: 65 } } },
        }),
      ).params,
    ).toBe(true);
  });
});

describe("filterWorkProductsByOverride", () => {
  const rows = [
    item({ job_key: "plain" }),
    item({ job_key: "lora", overrides: { loras: true } }),
    item({ job_key: "prompt", overrides: { prompt: true } }),
    item({ job_key: "both", overrides: { loras: true, stack: true } }),
  ];

  it("returns all when no need is set", () => {
    expect(filterWorkProductsByOverride(rows, []).map((r) => r.job_key)).toEqual(["plain", "lora", "prompt", "both"]);
  });

  it("filters any / specific kinds", () => {
    expect(filterWorkProductsByOverride(rows, ["any"]).map((r) => r.job_key)).toEqual(["lora", "prompt", "both"]);
    expect(filterWorkProductsByOverride(rows, ["loras"]).map((r) => r.job_key)).toEqual(["lora", "both"]);
    expect(jobMatchesOverrideNeed(rows[3], ["stack"])).toBe(true);
  });
});

describe("Find haystack + override chips", () => {
  const rows = [
    item({ job_key: "plain", family_slug: "FB8VA5-ZOOMOUT" }),
    item({
      job_key: "lora-job",
      family_slug: "FB9-FaceBlast",
      stack_id: "i2v-720p-Q5",
      overrides: { loras: true },
    }),
    item({ job_key: "prompt-job", family_slug: "FB9-FaceBlast", overrides: { prompt: true } }),
  ];

  it("matches override kind words and stack id in the loaded-list haystack", () => {
    const hay = workProductMediaHaystack(rows[1]);
    expect(hay).toContain("loras");
    expect(hay).toContain("ovr");
    expect(hay).toContain("i2v-720p-q5");
    expect(jobOverrideSearchLabel(rows[1]).toLowerCase()).toContain("loras");
    expect(rows.filter((it) => workProductMediaHaystack(it).includes("loras")).map((r) => r.job_key)).toEqual([
      "lora-job",
    ]);
  });

  it("composes free-text Find with override chips and family", () => {
    const named = rows.filter((it) => workProductMediaHaystack(it).includes("faceblast"));
    expect(filterWorkProductsByOverride(named, ["loras"]).map((r) => r.job_key)).toEqual(["lora-job"]);
    expect(filterWorkProductsByFamily(filterWorkProductsByOverride(rows, ["any"]), "FB9-FaceBlast").map((r) => r.job_key)).toEqual([
      "lora-job",
      "prompt-job",
    ]);
  });
});

describe("override detail lines", () => {
  it("formats LoRA and param diffs", () => {
    expect(
      formatOverrideLoraLine({
        lora: "dicks_epoch_100",
        on: true,
        strength: 0.7,
        seed_on: false,
        seed_strength: 0.1,
      }),
    ).toBe("dicks_epoch_100 · on · 0.7 · was off 0.1");
    expect(formatOverrideParamLine("frames", { job: 81, seed: 65 })).toBe("frames 65 → 81");
  });
});

describe("filterWorkProductsByFamily", () => {
  it("matches family slug case-insensitively", () => {
    const rows = [
      item({ job_key: "a", family_slug: "FB9-FaceBlast" }),
      item({ job_key: "b", family_slug: "FB8VA5-ZOOMOUT" }),
    ];
    expect(filterWorkProductsByFamily(rows, "fb9-faceblast").map((r) => r.job_key)).toEqual(["a"]);
    expect(filterWorkProductsByFamily(rows, "").map((r) => r.job_key)).toEqual(["a", "b"]);
  });
});
