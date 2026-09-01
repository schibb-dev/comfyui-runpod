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
  | "stills"
  | "clips"
  | "rate"
  | "workbench"
  | "family-ab"
  | "submit"
  | "vision-slices"
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
  /**
   * When false, the route still resolves (deep links / AppShell active state)
   * but is omitted from the primary nav. Default true.
   */
  nav?: boolean;
};

// Order matters for nav rendering (left → right).
// Pipeline: Library · Clips · Factory · Rating · Workbench · Queue.
// Submit is intent-modal only (doors → /submit?…); not a nav destination.
export const APP_ROUTES: AppRoute[] = [
  { id: "home", path: "/", label: "Home", hint: "Resume the loop — rate, triage, generate", group: "pipeline" },
  { id: "library", path: "/discovery", label: "Library", hint: "Search and find indexed media", group: "pipeline" },
  { id: "stills", path: "/discovery/stills", label: "Stills", hint: "Input still gallery · collections · I2V launch", group: "pipeline" },
  { id: "clips", path: "/discovery/clips", label: "Clips", hint: "Browse clip bookmarks across parents", group: "pipeline" },
  { id: "factory", path: "/discovery/factory-map", label: "Factory", hint: "Manage shape families · recover / replay", group: "pipeline" },
  { id: "rate", path: "/discovery/rate", label: "Rating", hint: "Rating bootstrap queue", group: "pipeline" },
  {
    id: "submit",
    path: "/submit",
    label: "Submit",
    hint: "Intent-only compose — open from Library, Clips, or Workbench",
    group: "pipeline",
    nav: false,
  },
  {
    id: "workbench",
    path: "/workbench",
    label: "Workbench",
    hint: "Job status — pending trim, retry, bindings, discard",
    group: "pipeline",
  },
  {
    id: "family-ab",
    path: "/family-ab",
    label: "Family A/B",
    hint: "Exemplar-locked family compare · catalog distinction",
    group: "tools",
  },
  { id: "queue", path: "/comfy-queue", label: "Queue", hint: "What's generating on Comfy right now", group: "pipeline" },
  { id: "vision-slices", path: "/vision/slices", label: "Vision slices", hint: "V1 time-slice captions vs video", group: "tools" },
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
  { id: "family-ab", test: (p) => p.startsWith("/family-ab") },
  { id: "submit", test: (p) => p.startsWith("/submit") },
  // Canonical /workbench; keep /work-products as a deep-link alias.
  { id: "workbench", test: (p) => p.startsWith("/workbench") || p.startsWith("/work-products") },
  { id: "vision-slices", test: (p) => p.startsWith("/vision") },
  { id: "factory", test: (p) => p.startsWith("/discovery/factory-map") },
  { id: "rate", test: (p) => p.startsWith("/discovery/rate") },
  { id: "stills", test: (p) => p.startsWith("/discovery/stills") },
  { id: "clips", test: (p) => p.startsWith("/discovery/clips") },
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
  return APP_ROUTES.filter((r) => r.group === group && r.nav !== false);
}

export function routeLabel(id: AppRouteId): string {
  return ROUTES_BY_ID[id]?.label ?? id;
}

export function routeHint(id: AppRouteId): string | undefined {
  return ROUTES_BY_ID[id]?.hint;
}

export function canHandleClientPath(pathname: string): boolean {
  const p = pathname || "/";
  if (p === "/") return true;
  return MATCHERS.some((m) => m.id !== "home" && m.test(p));
}
