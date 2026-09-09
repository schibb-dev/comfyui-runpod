import { describe, expect, it } from "vitest";
import {
  familySlugIsQuarantined,
  jobPromptVariantDisplayName,
  jobPromptVariantName,
  promptTextIsOverridden,
  distinctiveFamilyLabels,
} from "./submitFamily";

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
