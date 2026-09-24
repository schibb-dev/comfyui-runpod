import { describe, expect, it } from "vitest";
import { APP_ROUTES, navChildIsActive, NAV_CHILD_CANDIDATES } from "./routes";

describe("factory nav children", () => {
  const factory = APP_ROUTES.find((r) => r.id === "factory");

  it("exposes Families and Hourlies under Factory", () => {
    expect(factory?.children?.map((c) => c.id)).toEqual(["factory-families", "factory-hourlies"]);
  });

  it("marks Families active on index and family detail, not hourlies", () => {
    const families = factory!.children!.find((c) => c.id === "factory-families")!;
    expect(navChildIsActive(families, "/discovery/factory-map")).toBe(true);
    expect(navChildIsActive(families, "/discovery/factory-map/FB9_GEX")).toBe(true);
    expect(navChildIsActive(families, "/discovery/factory-map/pipeline/foo")).toBe(true);
    expect(navChildIsActive(families, "/discovery/factory-map/hourlies")).toBe(false);
  });

  it("marks Hourlies active only on hourlies paths", () => {
    const hourlies = factory!.children!.find((c) => c.id === "factory-hourlies")!;
    expect(navChildIsActive(hourlies, "/discovery/factory-map/hourlies")).toBe(true);
    expect(navChildIsActive(hourlies, "/discovery/factory-map/hourly")).toBe(true);
    expect(navChildIsActive(hourlies, "/discovery/factory-map")).toBe(false);
    expect(navChildIsActive(hourlies, "/discovery/factory-map/FB9_GEX")).toBe(false);
  });

  it("lists candidate nests for other parents", () => {
    const parents = NAV_CHILD_CANDIDATES.map((c) => c.parentId);
    expect(parents).toEqual(
      expect.arrayContaining(["factory", "workbench", "queue", "stills", "library", "workflows"]),
    );
  });
});
