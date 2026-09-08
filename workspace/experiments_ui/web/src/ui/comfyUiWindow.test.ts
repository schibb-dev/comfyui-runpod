import { describe, expect, it } from "vitest";
import { COMFYUI_WINDOW_NAME, comfyUiHref, comfyUiHostLabel, comfyUiPort } from "./comfyUiWindow";

describe("comfyUiWindow", () => {
  it("uses a stable named target", () => {
    expect(COMFYUI_WINDOW_NAME).toBe("comfyui");
  });

  it("points at the same host on the ComfyUI port", () => {
    expect(comfyUiPort()).toMatch(/^\d{2,5}$/);
    expect(comfyUiHref({ protocol: "http:", hostname: "127.0.0.1" })).toBe(
      `http://127.0.0.1:${comfyUiPort()}/`,
    );
    expect(comfyUiHref({ protocol: "https:", hostname: "box.ts.net" })).toBe(
      `https://box.ts.net:${comfyUiPort()}/`,
    );
    expect(comfyUiHostLabel({ hostname: "127.0.0.1" })).toBe(`127.0.0.1:${comfyUiPort()}`);
  });
});
