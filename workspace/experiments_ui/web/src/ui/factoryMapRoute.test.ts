import { describe, expect, it } from "vitest";
import {
  factoryMapHourliesCurateHref,
  factoryMapHourliesHref,
  hourlySteerBinIdForFamily,
  parseFactoryMapRoute,
} from "./factoryMapRoute";

describe("factoryMapRoute hourlies", () => {
  it("reserves /hourlies instead of treating it as a family slug", () => {
    expect(parseFactoryMapRoute("/discovery/factory-map")).toEqual({ view: "index" });
    expect(parseFactoryMapRoute("/discovery/factory-map/hourlies")).toEqual({ view: "hourlies" });
    expect(parseFactoryMapRoute("/discovery/factory-map/hourly")).toEqual({ view: "hourlies" });
    expect(parseFactoryMapRoute("/discovery/factory-map/hourlies/curate")).toEqual({
      view: "hourlies_curate",
    });
    expect(parseFactoryMapRoute("/discovery/factory-map/FB9_GEX")).toEqual({
      view: "family",
      familySlug: "FB9_GEX",
    });
    expect(factoryMapHourliesHref()).toBe("/discovery/factory-map/hourlies");
    expect(factoryMapHourliesCurateHref()).toBe("/discovery/factory-map/hourlies/curate");
    expect(factoryMapHourliesCurateHref({ binId: "hourly-seed-stills" })).toBe(
      "/discovery/factory-map/hourlies/curate",
    );
    expect(factoryMapHourliesCurateHref({ binId: "steer-still-BounceDanceA" })).toBe(
      "/discovery/factory-map/hourlies/curate?bin=steer-still-BounceDanceA",
    );
    expect(hourlySteerBinIdForFamily("X-KNEEL-FB9-bare")).toBe("hourly-seed-stills");
    expect(hourlySteerBinIdForFamily("BounceDanceA")).toBe("steer-still-BounceDanceA");
  });
});
