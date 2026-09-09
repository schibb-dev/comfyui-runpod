import { describe, expect, it } from "vitest";
import {
  jobPromptVariantDisplayName,
  jobPromptVariantName,
  promptTextIsOverridden,
} from "./submitFamily";

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
