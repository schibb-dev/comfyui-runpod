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

  it("does not call a pending job's source still This output", () => {
    const job = layersFromWorkProduct({
      job_key: "X-KNEEL-FB9__pp-catalog-default__still-3928097__00_ui1790016840",
      status: "running",
      parent_output_relpath:
        "input/_factory/3928097ab7b04c98055e5957b18be598aa2fa6600f19f8e528918fd9d4c33a07.jpg",
      bindings: {
        source_still: {
          relpath:
            "input/_factory/3928097ab7b04c98055e5957b18be598aa2fa6600f19f8e528918fd9d4c33a07.jpg",
          thumb_url:
            "/files/input%2F_factory%2F3928097ab7b04c98055e5957b18be598aa2fa6600f19f8e528918fd9d4c33a07.jpg",
        },
      },
    } as WorkProductItem);
    expect(job).toHaveLength(1);
    expect(job[0]?.label).toBe("Source still");
    expect(job[0]?.role).toBe("source");
    expect(job[0]?.relpath).toBe(
      "input/3928097ab7b04c98055e5957b18be598aa2fa6600f19f8e528918fd9d4c33a07.jpg",
    );
    expect(job[0]?.thumbUrl).toBe(
      "/files/input%2F3928097ab7b04c98055e5957b18be598aa2fa6600f19f8e528918fd9d4c33a07.jpg",
    );

    const lineage = layersFromLineage({
      ok: true,
      provenance_chain: [
        {
          depth: 0,
          role: "seed",
          item: {
            relpath:
              "input/_factory/3928097ab7b04c98055e5957b18be598aa2fa6600f19f8e528918fd9d4c33a07.jpg",
            name: "3928097ab7b04c98055e5957b18be598aa2fa6600f19f8e528918fd9d4c33a07.jpg",
          },
        },
      ],
    });
    const merged = mergeAppetiteLayers(lineage, job);
    expect(merged).toHaveLength(1);
    expect(merged[0]?.label).toBe("Source still");
    expect(merged[0]?.role).toBe("source");
    expect(merged[0]?.relpath).toBe(
      "input/3928097ab7b04c98055e5957b18be598aa2fa6600f19f8e528918fd9d4c33a07.jpg",
    );
  });
});
