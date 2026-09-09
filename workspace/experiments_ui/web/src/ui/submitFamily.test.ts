import { describe, expect, it } from "vitest";
import {
  familySlugIsQuarantined,
  jobPromptVariantDisplayName,
  jobPromptVariantName,
  pickQuickExtendFamily,
  pickRerunPromptPreset,
  promptTextIsOverridden,
  distinctiveFamilyLabels,
  rerunPromptPresetDiffers,
  workProductCanQuickExtend,
  workProductHasExtendableOutput,
} from "./submitFamily";
import type { WorkProductFamilyOption } from "./types";

describe("familySlugIsQuarantined", () => {
  const entries = [
    { status: "quarantined", workflow_name: "FB8VB2_2026-01-06_234750_EXT_00001-readable.json" },
    { status: "quarantined", workflow_name: "X-KNEEL-FB9-readable.json" },
    { status: "released", workflow_name: "BounceDanceA-readable.json" },
  ];

  it("matches a family-prefixed instance workflow and catalog readable", () => {
    expect(familySlugIsQuarantined("FB8VB2", entries)).toBe(true);
    expect(familySlugIsQuarantined("X-KNEEL-FB9", entries)).toBe(true);
  });

  it("does not treat a short slug as a prefix of a longer family", () => {
    expect(familySlugIsQuarantined("FB9", [{ status: "quarantined", workflow_name: "FB9_GEX2_2026-04-04_00019-readable.json" }])).toBe(
      false,
    );
    expect(familySlugIsQuarantined("FB9_GEX", [{ status: "quarantined", workflow_name: "FB9_GEX2_foo-readable.json" }])).toBe(
      false,
    );
  });

  it("ignores released entries and empty input", () => {
    expect(familySlugIsQuarantined("BounceDanceA", entries)).toBe(false);
    expect(familySlugIsQuarantined("FB8VB2", [])).toBe(false);
    expect(familySlugIsQuarantined("", entries)).toBe(false);
  });
});

describe("distinctiveFamilyLabels", () => {
  it("keeps short slugs and unique token spans", () => {
    const labels = distinctiveFamilyLabels([
      "X-KNEEL-FB9-bare",
      "BounceDanceA",
      "Breast-shake-FB8VA5",
      "FB8VA4",
      "FB8VA5-ZOOMOUT",
      "FB8VB2",
      "FB9-FaceBlast",
      "X-KNEEL-FB9",
    ]);
    expect(labels.get("X-KNEEL-FB9")).toBe("X-KNEEL-FB9");
    expect(labels.get("X-KNEEL-FB9-bare")).toBe("X-KNEEL-FB9-bare");
    expect(labels.get("Breast-shake-FB8VA5")).toBe("Breast-shake");
    expect(labels.get("BounceDanceA")).toBe("BounceDanceA");
    expect(labels.get("FB8VA5-ZOOMOUT")).toBe("FB8VA5-ZOOMOUT");
    expect(new Set(labels.values()).size).toBe(labels.size);
  });

  it("picks distinctive tokens among GEX siblings", () => {
    const labels = distinctiveFamilyLabels([
      "FB9_GEX",
      "FB9_GEX2",
      "FB9_GEX2_identity_anchor",
      "FB9_GEX_FACIAL",
      "ASTONISH_FB9_GEX",
    ]);
    expect(labels.get("FB9_GEX")).toBe("FB9_GEX");
    expect(labels.get("FB9_GEX2")).toBe("FB9_GEX2");
    expect(labels.get("FB9_GEX2_identity_anchor")).toBe("identity_anchor");
    expect(labels.get("FB9_GEX_FACIAL")).toBe("FB9_GEX_FACIAL");
    expect(labels.get("ASTONISH_FB9_GEX")).toBe("ASTONISH_FB9_GEX");
    expect(new Set(labels.values()).size).toBe(labels.size);
  });
});

describe("jobPromptVariantDisplayName", () => {
  it("keeps the catalog name when the job matches the seed", () => {
    expect(
      jobPromptVariantDisplayName({
        prompt_profile: { name: "Default", slug: "default", snowflake: false },
      }),
    ).toBe("Default");
    expect(
      jobPromptVariantDisplayName({
        prompt_profile: { name: "FaceBlast extend", slug: "faceblast-extend", snowflake: false },
      }),
    ).toBe("FaceBlast extend");
  });

  it("marks Default and FaceBlast extend as edited when snowflake", () => {
    expect(
      jobPromptVariantDisplayName({
        prompt_profile: { name: "Default", slug: "default", snowflake: true },
      }),
    ).toBe("Default · edited");
    expect(
      jobPromptVariantDisplayName({
        prompt_profile: { name: "FaceBlast extend", slug: "faceblast-extend", snowflake: true },
      }),
    ).toBe("FaceBlast extend · edited");
  });

  it("treats compose scratch drafts as edited even without snowflake", () => {
    const item = {
      job_key:
        "FB8VA5-ZOOMOUT__pp-catalog-default__draft_1788888144__still-e4e35ed9cdf5fb3eb2981b8a9d788668c19b15947843c2__ui1788888144",
      prompt_profile: {
        name: null,
        slug: "default",
        path: "/workspace/.data/shape_factory/jobs/_scratch/FB8VA5-ZOOMOUT/catalog-default__draft_1788888144.json",
        basename: "catalog-default__draft_1788888144.json",
        snowflake: false,
      },
    };
    expect(jobPromptVariantName(item)).toBe("Default");
    expect(promptTextIsOverridden(item.prompt_profile)).toBe(true);
    expect(jobPromptVariantDisplayName(item)).toBe("Default · edited");
  });
});

describe("pickRerunPromptPreset", () => {
  const profiles = [
    { slug: "default", name: "Default", path: "/pools/FB9_GEX/prompts/catalog-default.json", basename: "catalog-default.json" },
    {
      slug: "faceblast-extend",
      name: "FaceBlast extend",
      path: "/pools/FB9_GEX/prompts/catalog-faceblast-extend.json",
      basename: "catalog-faceblast-extend.json",
    },
  ];

  it("matches the job variant slug to a catalog preset", () => {
    expect(pickRerunPromptPreset(profiles, { slug: "faceblast-extend", snowflake: true } as never)).toBe(
      "/pools/FB9_GEX/prompts/catalog-faceblast-extend.json",
    );
    expect(pickRerunPromptPreset(profiles, "default")).toBe("/pools/FB9_GEX/prompts/catalog-default.json");
  });

  it("falls back to catalog-default, never a scratch path", () => {
    expect(pickRerunPromptPreset(profiles, { slug: "unknown", path: "/scratch/draft.json" })).toBe(
      "/pools/FB9_GEX/prompts/catalog-default.json",
    );
    expect(pickRerunPromptPreset(profiles)).toBe("/pools/FB9_GEX/prompts/catalog-default.json");
  });

  it("only treats a different catalog path as a change", () => {
    expect(
      rerunPromptPresetDiffers("/pools/FB9_GEX/prompts/catalog-default.json", profiles, { slug: "default" }),
    ).toBe(false);
    expect(
      rerunPromptPresetDiffers("/pools/FB9_GEX/prompts/catalog-faceblast-extend.json", profiles, { slug: "default" }),
    ).toBe(true);
  });
});

const extendFamily = (slug: string, extra: Partial<WorkProductFamilyOption> = {}): WorkProductFamilyOption => ({
  slug,
  chain_role: "extend",
  io_class: "V2V",
  ...extra,
});
const i2vFamily = (slug: string): WorkProductFamilyOption => ({
  slug,
  chain_role: "origin",
  io_class: "I2V",
});

describe("workProductCanQuickExtend", () => {
  const families = [i2vFamily("X-KNEEL-FB9"), extendFamily("FB9_GEX"), extendFamily("FB9_GEX2")];

  it("requires a factory job with a video output", () => {
    expect(workProductHasExtendableOutput({ output_relpath: "og/clip.mp4" })).toBe(true);
    expect(workProductHasExtendableOutput({ output_relpath: "input/still.jpeg" })).toBe(false);
    expect(
      workProductCanQuickExtend({ job_key: "j1", output_relpath: "og/clip.mp4" }, families),
    ).toBe(true);
    expect(
      workProductCanQuickExtend({ job_key: "j1", output_relpath: null }, families),
    ).toBe(false);
    expect(
      workProductCanQuickExtend({ job_key: "", output_relpath: "og/clip.mp4" }, families),
    ).toBe(false);
  });

  it("hides Extend when no V2V/VI2V family exists", () => {
    expect(
      workProductCanQuickExtend({ job_key: "j1", output_relpath: "og/clip.mp4" }, [i2vFamily("X-KNEEL-FB9")]),
    ).toBe(false);
  });
});

describe("pickQuickExtendFamily", () => {
  const families = [i2vFamily("X-KNEEL-FB9"), extendFamily("FB9_GEX"), extendFamily("FB9_GEX2")];

  it("keeps an operator-picked extend family", () => {
    expect(
      pickQuickExtendFamily(families, { "X-KNEEL-FB9": "FB9_GEX" }, { family_slug: "X-KNEEL-FB9" }, "FB9_GEX2"),
    ).toBe("FB9_GEX2");
  });

  it("ignores an I2V picker value and uses the successor default", () => {
    expect(
      pickQuickExtendFamily(
        families,
        { "X-KNEEL-FB9": "FB9_GEX" },
        { family_slug: "X-KNEEL-FB9", output_relpath: "og/kneel.mp4" },
        "X-KNEEL-FB9",
      ),
    ).toBe("FB9_GEX");
  });
});
