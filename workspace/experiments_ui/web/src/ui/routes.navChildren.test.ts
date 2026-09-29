import { describe, expect, it } from "vitest";
import { APP_ROUTES, navChildIsActive, NAV_CHILD_CANDIDATES, resolveRouteId } from "./routes";

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

  it("addresses Workbench sections and keeps them on the workbench screen", () => {
    const workbench = APP_ROUTES.find((r) => r.id === "workbench");
    expect(workbench?.children?.map((c) => c.path)).toEqual([
      "/workbench/live",
      "/workbench/pending",
      "/workbench/errors",
      "/workbench/done",
    ]);
    expect(resolveRouteId("/workbench/pending")).toBe("workbench");
    expect(resolveRouteId("/work-products/errors")).toBe("workbench");
    const errors = workbench!.children!.find((c) => c.id === "wb-errors")!;
    expect(navChildIsActive(errors, "/workbench/errors")).toBe(true);
    expect(navChildIsActive(errors, "/workbench/error")).toBe(true);
    expect(navChildIsActive(errors, "/workbench/pending")).toBe(false);
  });
});
