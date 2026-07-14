// Typed route registry for the Experiments UI.
//
// One source of truth for top-level screens: their paths, labels, nav grouping,
// and active-state matching. `main.tsx` uses this to pick a screen; `AppShell`
// uses it to render the global nav. Deep-link helpers (factory-map family, etc.)
// live in their own modules but should build on the base paths here.

export type AppRouteId =
  | "home"
  | "queue"
  | "factory"
  | "library"
  | "rate"
  | "work-products"
  | "experiments"
  | "workflows"
  | "orchestrator";

export type AppNavGroup = "pipeline" | "tools";

export type AppRoute = {
  id: AppRouteId;
  /** Canonical path used for nav links. */
  path: string;
  label: string;
  /** Short helper text for tooltips. */
  hint?: string;
  group: AppNavGroup;
};

// Order matters for nav rendering (left → right follows the production pipeline).
export const APP_ROUTES: AppRoute[] = [
  { id: "home", path: "/", label: "Home", hint: "Resume the loop — rate, triage, generate", group: "pipeline" },
  { id: "queue", path: "/comfy-queue", label: "Queue", hint: "What's generating right now", group: "pipeline" },
  { id: "factory", path: "/discovery/factory-map", label: "Factory", hint: "Shape families · source → output · recover / replay", group: "pipeline" },
  { id: "library", path: "/discovery", label: "Library", hint: "Discover indexed outputs", group: "pipeline" },
  { id: "rate", path: "/discovery/rate", label: "Rate", hint: "Rating bootstrap queue", group: "pipeline" },
  { id: "work-products", path: "/work-products", label: "Work products", hint: "Recent outputs · how they were constructed", group: "pipeline" },
  { id: "experiments", path: "/experiments", label: "Experiments", hint: "Tune experiments & runs", group: "tools" },
  { id: "workflows", path: "/workflow-explorer", label: "Workflows", hint: "Workflow & factory-asset explorer", group: "tools" },
  { id: "orchestrator", path: "/orchestrator", label: "Orchestrator", hint: "Projects, collections, pipelines", group: "tools" },
];

const ROUTES_BY_ID: Record<AppRouteId, AppRoute> = APP_ROUTES.reduce(
  (acc, r) => {
    acc[r.id] = r;
    return acc;
  },
  {} as Record<AppRouteId, AppRoute>,
);

// Most-specific first: the first predicate that matches wins. Home is the catch-all
// landing route, so it must be matched last (any unknown path lands on Home).
const MATCHERS: { id: AppRouteId; test: (p: string) => boolean }[] = [
  { id: "queue", test: (p) => p.startsWith("/comfy-queue") },
  { id: "orchestrator", test: (p) => p.startsWith("/orchestrator") },
  { id: "workflows", test: (p) => p.startsWith("/workflow-explorer") },
  { id: "experiments", test: (p) => p.startsWith("/experiments") },
  { id: "work-products", test: (p) => p.startsWith("/work-products") },
  { id: "factory", test: (p) => p.startsWith("/discovery/factory-map") },
  { id: "rate", test: (p) => p.startsWith("/discovery/rate") },
  { id: "library", test: (p) => p.startsWith("/discovery") },
  { id: "home", test: () => true },
];

export function resolveRouteId(pathname: string): AppRouteId {
  const p = pathname || "/";
  for (const m of MATCHERS) {
    if (m.test(p)) return m.id;
  }
  return "home";
}

export function routeHref(id: AppRouteId): string {
  return ROUTES_BY_ID[id]?.path ?? "/";
}

export function routesForGroup(group: AppNavGroup): AppRoute[] {
  return APP_ROUTES.filter((r) => r.group === group);
}

// The three pipeline screens that live inside the one Workbench surface as lenses.
// They keep their own routes/paths (deep-links stay valid) but the global nav shows
// a single "Workbench" entry; the lens bar switches between them.
export const WORKBENCH_LENSES: AppRouteId[] = ["library", "factory", "rate"];

export function isWorkbenchLens(id: AppRouteId): boolean {
  return WORKBENCH_LENSES.includes(id);
}

export function routeLabel(id: AppRouteId): string {
  return ROUTES_BY_ID[id]?.label ?? id;
}

export function routeHint(id: AppRouteId): string | undefined {
  return ROUTES_BY_ID[id]?.hint;
}
