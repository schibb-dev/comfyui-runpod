import { describe, expect, it } from "vitest";
import { factoryMapHourliesHref, parseFactoryMapRoute } from "./factoryMapRoute";

describe("factoryMapRoute hourlies", () => {
  it("reserves /hourlies instead of treating it as a family slug", () => {
    expect(parseFactoryMapRoute("/discovery/factory-map")).toEqual({ view: "index" });
    expect(parseFactoryMapRoute("/discovery/factory-map/hourlies")).toEqual({ view: "hourlies" });
    expect(parseFactoryMapRoute("/discovery/factory-map/hourly")).toEqual({ view: "hourlies" });
    expect(parseFactoryMapRoute("/discovery/factory-map/FB9_GEX")).toEqual({
      view: "family",
      familySlug: "FB9_GEX",
    });
    expect(factoryMapHourliesHref()).toBe("/discovery/factory-map/hourlies");
  });
});
