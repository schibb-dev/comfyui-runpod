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
  | "pools"
  | "remove"
  | "workbench"
  | "family-ab"
  | "submit"
  | "vision-slices"
  | "experiments"
  | "workflows"
  | "orchestrator";

export type AppNavGroup = "now" | "browse" | "produce" | "care" | "labs";

export type AppRoute = {
  id: AppRouteId;
  /** Canonical path used for nav links. */
  path: string;
  label: string;
  /** Short glyph shown when the desktop sidebar is collapsed. */
  mark: string;
  /** Short helper text for tooltips. */
  hint?: string;
  group: AppNavGroup;
  /**
   * When false, the route still resolves (deep links / AppShell active state)
   * but is omitted from the primary nav. Default true.
   */
  nav?: boolean;
};

export const NAV_GROUP_ORDER: AppNavGroup[] = ["now", "browse", "produce", "care", "labs"];

export const NAV_GROUP_LABEL: Record<AppNavGroup, string> = {
  now: "Now",
  browse: "Browse",
  produce: "Produce",
  care: "Care",
  labs: "Labs",
};

// Submit is intent-modal only (doors → /submit?…); not a nav destination.
export const APP_ROUTES: AppRoute[] = [
  { id: "home", path: "/", label: "Home", mark: "Ho", hint: "Resume the loop — rate, triage, generate", group: "now" },
  {
    id: "workbench",
    path: "/workbench",
    label: "Workbench",
    mark: "Wb",
    hint: "Job status — pending trim, retry, bindings, discard",
    group: "now",
  },
  { id: "queue", path: "/comfy-queue", label: "Queue", mark: "Q", hint: "What's generating on Comfy right now", group: "now" },
  { id: "library", path: "/discovery", label: "Library", mark: "Lb", hint: "Search and find indexed media", group: "browse" },
  { id: "stills", path: "/discovery/stills", label: "Stills", mark: "St", hint: "Input still gallery · collections · I2V launch", group: "browse" },
  { id: "clips", path: "/discovery/clips", label: "Clips", mark: "Cl", hint: "Browse clip bookmarks across parents", group: "browse" },
  { id: "factory", path: "/discovery/factory-map", label: "Factory", mark: "Fc", hint: "Families, hourlies, recover / replay", group: "produce" },
  {
    id: "pools",
    path: "/discovery/pools",
    label: "Follow-up",
    mark: "Fu",
    hint: "Videos marked to fix, look at, or vary later",
    group: "produce",
  },
  { id: "rate", path: "/discovery/rate", label: "Rating", mark: "Rt", hint: "Rating bootstrap queue", group: "produce" },
  {
    id: "remove",
    path: "/discovery/remove",
    label: "Remove",
    mark: "Rm",
    hint: "Review and permanently delete Remove-marked outputs",
    group: "care",
  },
  {
    id: "submit",
    path: "/submit",
    label: "Submit",
    mark: "Sb",
    hint: "Intent-only compose — open from Library, Clips, or Workbench",
    group: "produce",
    nav: false,
  },
  {
    id: "family-ab",
    path: "/family-ab",
    label: "Family A/B",
    mark: "AB",
    hint: "Exemplar-locked family compare · catalog distinction",
    group: "labs",
  },
  { id: "vision-slices", path: "/vision/slices", label: "Vision", mark: "Vs", hint: "V1 time-slice captions vs video", group: "labs" },
  { id: "experiments", path: "/experiments", label: "Experiments", mark: "Ex", hint: "Tune experiments & runs", group: "labs" },
  { id: "workflows", path: "/workflow-explorer", label: "Workflows", mark: "Wf", hint: "Workflow & factory-asset explorer", group: "labs" },
  { id: "orchestrator", path: "/orchestrator", label: "Orchestrator", mark: "Or", hint: "Projects, collections, pipelines", group: "labs" },
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
  { id: "workbench", test: (p) => p.startsWith("/workbench") || p.startsWith("/work-products") },
  { id: "vision-slices", test: (p) => p.startsWith("/vision") },
  { id: "factory", test: (p) => p.startsWith("/discovery/factory-map") },
  { id: "rate", test: (p) => p.startsWith("/discovery/rate") },
  { id: "pools", test: (p) => p.startsWith("/discovery/pools") || p.startsWith("/discovery/follow-up") },
  { id: "remove", test: (p) => p.startsWith("/discovery/remove") },
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

export function navGroups(): Array<{ id: AppNavGroup; label: string; routes: AppRoute[] }> {
  return NAV_GROUP_ORDER.map((id) => ({
    id,
    label: NAV_GROUP_LABEL[id],
    routes: routesForGroup(id),
  })).filter((g) => g.routes.length);
}

/** Destinations listed in the phone hamburger (every nav route). */
export function phoneTabRoutes(): AppRoute[] {
  return APP_ROUTES.filter((r) => r.nav !== false);
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
