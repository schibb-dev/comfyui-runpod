import { describe, expect, it, beforeEach, afterEach } from "vitest";
import {
  familyRouteKey,
  findStackIdByModelSignature,
  lastSubmitFamily,
  pickSubmitStack,
  pickSubmitStackForOpen,
  readSubmitStackPrefs,
  rememberSubmitFamily,
  rememberSubmitStack,
  setSubmitStackCookieStoreForTests,
  stackModelSignature,
  writeSubmitStackPrefs,
  type SubmitStackPrefs,
} from "./submitStackPrefs";
import type { GenerationStackOption } from "./types";

const stacks: GenerationStackOption[] = [
  { stack_id: "i2v-720p-Q5", label: "720p-Q5 virt4.0", training_size: "720p", quant: "Q5_K_M" },
  { stack_id: "i2v-480p-Q8", label: "480p-Q8 virt6.0", training_size: "480p", quant: "Q8" },
  { stack_id: "i2v-480p-Q5", label: "480p-Q5 virt4.0", training_size: "480p", quant: "Q5_K_M" },
];

function memoryCookieStore(): { read: (name: string) => string; write: (name: string, value: string) => void } {
  const jar = new Map<string, string>();
  return {
    read: (name) => jar.get(name) || "",
    write: (name, value) => {
      jar.set(name, value);
    },
  };
}

describe("submitStackPrefs", () => {
  beforeEach(() => {
    setSubmitStackCookieStoreForTests(memoryCookieStore());
  });

  afterEach(() => {
    setSubmitStackCookieStoreForTests(null);
  });

  it("derives model signatures from stack metadata or ids", () => {
    expect(stackModelSignature(stacks[0])).toBe("720p:q5_k_m");
    expect(stackModelSignature(null, "i2v-480p-Q8")).toBe("480p:q8");
    expect(findStackIdByModelSignature(stacks, "480p:q8")).toBe("i2v-480p-Q8");
  });

  it("round-trips v2 prefs through the cookie", () => {
    const prefs: SubmitStackPrefs = {
      lastByRoute: { still: "i2v-720p-Q5", video: "i2v-480p-Q8" },
      lastFamilyByRoute: { still: "FB9-FaceBlast", video: "FB9_GEX2" },
      byFamilyRoute: { "video:FB9": "i2v-480p-Q5" },
      recentByRoute: { video: ["i2v-480p-Q8", "i2v-720p-Q5"] },
      modelSignature: "480p:q8",
    };
    writeSubmitStackPrefs(prefs);
    expect(readSubmitStackPrefs()).toEqual(prefs);
  });

  it("does not apply video family stack to still route but can reuse the model", () => {
    writeSubmitStackPrefs({
      lastByRoute: { video: "i2v-480p-Q8" },
      lastFamilyByRoute: {},
      byFamilyRoute: { "video:FB9-FaceBlast": "i2v-480p-Q5" },
      recentByRoute: {},
      modelSignature: "480p:q8",
    });
    const picked = pickSubmitStack(stacks, {
      routeKind: "still",
      familySlug: "FB9-FaceBlast",
      familyStackId: "i2v-720p-Q5",
    });
    expect(picked).toBe("i2v-480p-Q8");
    expect(picked).not.toBe("i2v-480p-Q5");
  });

  it("applies cross-route model preference when family default differs", () => {
    writeSubmitStackPrefs({
      lastByRoute: { video: "i2v-480p-Q8" },
      lastFamilyByRoute: {},
      byFamilyRoute: {},
      recentByRoute: {},
      modelSignature: "480p:q8",
    });
    expect(
      pickSubmitStack(stacks, {
        routeKind: "still",
        familySlug: "FB9-FaceBlast",
        familyStackId: "i2v-720p-Q5",
      }),
    ).toBe("i2v-480p-Q8");
  });

  it("prefers job stack, then route+family cookie, then family default", () => {
    writeSubmitStackPrefs({
      lastByRoute: { still: "i2v-480p-Q8" },
      lastFamilyByRoute: {},
      byFamilyRoute: { "still:FB9": "i2v-480p-Q5" },
      recentByRoute: {},
      modelSignature: "",
    });
    expect(
      pickSubmitStack(stacks, {
        routeKind: "still",
        jobStackId: "i2v-720p-Q5",
        familySlug: "FB9",
        familyStackId: "i2v-480p-Q8",
      }),
    ).toBe("i2v-720p-Q5");
    expect(
      pickSubmitStack(stacks, {
        routeKind: "still",
        familySlug: "FB9",
        familyStackId: "i2v-480p-Q8",
      }),
    ).toBe("i2v-480p-Q5");
    expect(
      pickSubmitStack(stacks, {
        routeKind: "still",
        familySlug: "Kneel",
        familyStackId: "i2v-480p-Q8",
      }),
    ).toBe("i2v-480p-Q8");
  });

  it("rememberSubmitStack stores route-scoped family keys and model signature", () => {
    rememberSubmitStack("i2v-720p-Q5", { routeKind: "still", familySlug: "FB9", stacks });
    rememberSubmitStack("i2v-480p-Q8", { routeKind: "video", familySlug: "GEX2", stacks });
    expect(readSubmitStackPrefs()).toEqual({
      lastByRoute: { still: "i2v-720p-Q5", video: "i2v-480p-Q8" },
      lastFamilyByRoute: { still: "FB9", video: "GEX2" },
      byFamilyRoute: {
        [familyRouteKey("still", "FB9")]: "i2v-720p-Q5",
        [familyRouteKey("video", "GEX2")]: "i2v-480p-Q8",
      },
      recentByRoute: {
        still: ["i2v-720p-Q5"],
        video: ["i2v-480p-Q8"],
      },
      modelSignature: "480p:q8",
    });
  });

  it("remembers I2V / extend family independently of stack and restores it next open", () => {
    rememberSubmitFamily("BounceDanceA", "still");
    rememberSubmitFamily("FB9_GEX2", "video");
    expect(lastSubmitFamily("still")).toBe("BounceDanceA");
    expect(lastSubmitFamily("video")).toBe("FB9_GEX2");
    rememberSubmitStack("i2v-720p-Q5", { routeKind: "still", familySlug: "FB9-FaceBlast", stacks });
    expect(lastSubmitFamily("still")).toBe("FB9-FaceBlast");
    expect(lastSubmitFamily("video")).toBe("FB9_GEX2");
  });

  it("pickSubmitStackForOpen uses still route and saved model signature", () => {
    writeSubmitStackPrefs({
      lastByRoute: { video: "i2v-720p-Q5" },
      lastFamilyByRoute: {},
      byFamilyRoute: {},
      recentByRoute: {},
      modelSignature: "480p:q8",
    });
    expect(
      pickSubmitStackForOpen(stacks, {
        isStill: true,
        familySlug: "FB9-FaceBlast",
        familyStackId: "i2v-720p-Q5",
      }),
    ).toBe("i2v-480p-Q8");
  });

  it("migrates v1 cookie shape: video byFamily only, model carries to still", () => {
    writeSubmitStackPrefs({
      last: "i2v-480p-Q8",
      byFamily: { FB9: "i2v-480p-Q5" },
      recent: ["i2v-480p-Q8"],
    } as unknown as SubmitStackPrefs);
    expect(
      pickSubmitStack(stacks, {
        routeKind: "still",
        familySlug: "FB9",
        familyStackId: "i2v-720p-Q5",
      }),
    ).toBe("i2v-480p-Q8");
    expect(
      pickSubmitStack(stacks, {
        routeKind: "video",
        familySlug: "FB9",
        familyStackId: "i2v-720p-Q5",
      }),
    ).toBe("i2v-480p-Q5");
  });
});
