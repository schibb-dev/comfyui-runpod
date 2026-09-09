import { describe, expect, it } from "vitest";
import {
  composeRuntimeOverrides,
  lorasOverrideFromDraft,
  paramsOverrideFromDraft,
} from "./submitRuntime";

describe("composeRuntimeOverrides", () => {
  it("omits template-matching knobs and LoRAs", () => {
    const seedParams = { frames: 81, steps: 8, overlap: 4, seed: 1 };
    const seedLoras = [{ lora: "a.safetensors", on: true, strength: 0.8 }];
    expect(
      composeRuntimeOverrides(null, {}, seedParams, seedLoras, seedLoras),
    ).toEqual({});
  });

  it("sends only changed params and LoRA stack", () => {
    const seedParams = { frames: 81, steps: 8, overlap: 4 };
    const seedLoras = [{ lora: "a.safetensors", on: true, strength: 0.8 }];
    const draftLoras = [{ lora: "a.safetensors", on: false, strength: 0.8 }];
    expect(paramsOverrideFromDraft({ steps: 12 }, seedParams)).toEqual({ steps: 12 });
    expect(lorasOverrideFromDraft(draftLoras, seedLoras)).toEqual({ entries: draftLoras });
    expect(
      composeRuntimeOverrides(97, { steps: 12 }, seedParams, draftLoras, seedLoras),
    ).toEqual({
      parameters: { frames: 97, steps: 12 },
      loras: { entries: draftLoras },
    });
  });
});
