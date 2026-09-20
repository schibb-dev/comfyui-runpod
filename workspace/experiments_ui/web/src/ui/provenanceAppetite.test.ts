import { describe, expect, it } from "vitest";
import {
  layersFromFactoryPair,
  layersFromLineage,
  layersFromWorkProduct,
  mergeAppetiteLayers,
} from "./provenanceAppetite";
import type { WorkProductItem } from "./types";

describe("provenanceAppetite layers", () => {
  it("collects source bindings and output from a work product", () => {
    const layers = layersFromWorkProduct({
      job_key: "j1",
      output_relpath: "og/2026-09-20/out.mp4",
      output_thumb_url: "/files/og/2026-09-20/out.png",
      parent_output_relpath: "og/2026-09-19/parent.mp4",
      bindings: {
        prompt_profile: { relpath: "prompts/default.json" },
        source_still: { relpath: "input/face.jpg", thumb_url: "/files/input/face.jpg" },
      },
    } as WorkProductItem);
    expect(layers.map((l) => l.role)).toEqual(["source", "parent", "output"]);
    expect(layers.map((l) => l.relpath)).toEqual([
      "input/face.jpg",
      "og/2026-09-19/parent.mp4",
      "og/2026-09-20/out.mp4",
    ]);
    expect(layers.find((l) => l.slot === "source_still")?.label).toBe("Source still");
  });

  it("skips prompt_profile and duplicate parent/source paths", () => {
    const layers = layersFromWorkProduct({
      job_key: "j2",
      output_relpath: "og/out.mp4",
      parent_output_relpath: "og/parent.mp4",
      bindings: {
        source_video: { relpath: "og/parent.mp4" },
      },
    } as WorkProductItem);
    expect(layers.map((l) => l.relpath)).toEqual(["og/parent.mp4", "og/out.mp4"]);
  });

  it("builds factory pair layers from bindings plus output", () => {
    const layers = layersFromFactoryPair({
      source: { relpath: "input/a.jpeg", basename: "a.jpeg" },
      output: { relpath: "og/b.mp4", basename: "b.mp4" },
      bindings: {
        source_still: { relpath: "input/a.jpeg" },
        identity_still: { relpath: "input/id.png" },
      },
    });
    expect(layers.map((l) => l.relpath)).toEqual(["input/a.jpeg", "input/id.png", "og/b.mp4"]);
    expect(layers[layers.length - 1]?.role).toBe("output");
  });

  it("merges lineage ancestors in front and keeps this-output last", () => {
    const lineage = layersFromLineage({
      ok: true,
      provenance_chain: [
        { depth: 2, role: "source", item: { relpath: "input/root.jpeg", name: "root.jpeg" } },
        { depth: 1, role: "ancestor", item: { relpath: "og/mid.mp4", name: "mid.mp4" } },
        { depth: 0, role: "seed", item: { relpath: "og/out.mp4", name: "out.mp4" } },
      ],
    });
    const job = layersFromWorkProduct({
      job_key: "j3",
      output_relpath: "og/out.mp4",
      bindings: { source_still: { relpath: "input/root.jpeg" } },
    } as WorkProductItem);
    const merged = mergeAppetiteLayers(lineage, job);
    expect(merged.map((l) => l.relpath)).toEqual(["input/root.jpeg", "og/mid.mp4", "og/out.mp4"]);
    expect(merged[2]?.label).toBe("This output");
    expect(merged[0]?.role).toBe("source");
  });
});
