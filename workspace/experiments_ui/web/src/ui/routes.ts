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

/** Nested destinations under a primary nav route (e.g. Factory → Families / Hourlies). */
export type AppNavChild = {
  id: string;
  label: string;
  path: string;
  hint?: string;
  /** Override active detection; default is exact path or path + "/" prefix. */
  match?: (pathname: string) => boolean;
};

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
  /** Optional nested links shown under this item when the sidebar/drawer is expanded. */
  children?: AppNavChild[];
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
  {
    id: "factory",
    path: "/discovery/factory-map",
    label: "Factory",
    mark: "Fc",
    hint: "Families, hourlies, recover / replay",
    group: "produce",
    children: [
      {
        id: "factory-families",
        label: "Families",
        path: "/discovery/factory-map",
        hint: "Family map — shapes, pools, recover / replay",
        match: (p) => {
          const path = (p || "/").replace(/\/+$/, "") || "/";
          if (!path.startsWith("/discovery/factory-map")) return false;
          if (path === "/discovery/factory-map/hourlies" || path === "/discovery/factory-map/hourly") {
            return false;
          }
          if (
            path === "/discovery/factory-map/hourlies/curate" ||
            path === "/discovery/factory-map/hourly/curate"
          ) {
            return false;
          }
          return true;
        },
      },
      {
        id: "factory-hourlies",
        label: "Hourlies",
        path: "/discovery/factory-map/hourlies",
        hint: "Cadence, pending floor, and chain backlogs",
        match: (p) => {
          const path = (p || "/").replace(/\/+$/, "") || "/";
          return path === "/discovery/factory-map/hourlies" || path === "/discovery/factory-map/hourly";
        },
      },
      {
        id: "factory-steering",
        label: "Steering",
        path: "/discovery/factory-map/hourlies/curate",
        hint: "Sort seed stills into the hourly feed bin",
        match: (p) => {
          const path = (p || "/").replace(/\/+$/, "") || "/";
          return (
            path === "/discovery/factory-map/hourlies/curate" ||
            path === "/discovery/factory-map/hourly/curate"
          );
        },
      },
    ],
  },
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

export function navChildIsActive(child: AppNavChild, pathname: string): boolean {
  const p = (pathname || "/").replace(/\/+$/, "") || "/";
  if (child.match) return child.match(p);
  const base = (child.path || "/").replace(/\/+$/, "") || "/";
  return p === base || p.startsWith(`${base}/`);
}

/**
 * Candidates for the same parent→child nav pattern (not all wired yet).
 * Factory Families/Hourlies are live; the rest are the natural next nests.
 */
export const NAV_CHILD_CANDIDATES: Array<{
  parentId: AppRouteId;
  children: Array<{ id: string; label: string; note: string }>;
}> = [
  {
    parentId: "factory",
    children: [
      { id: "factory-families", label: "Families", note: "wired — factory-map index + family/pipeline detail" },
      { id: "factory-hourlies", label: "Hourlies", note: "wired — /discovery/factory-map/hourlies" },
      { id: "factory-steering", label: "Steering", note: "wired — /discovery/factory-map/hourlies/curate" },
      { id: "factory-pipelines", label: "Pipelines", note: "candidate — pipeline detail already exists under factory-map" },
    ],
  },
  {
    parentId: "workbench",
    children: [
      { id: "wb-live", label: "Comfy Queue", note: "candidate — Workbench nav section" },
      { id: "wb-pending", label: "Pending", note: "candidate — factory FIFO" },
      { id: "wb-errors", label: "Errors", note: "candidate — failed / interrupted" },
      { id: "wb-done", label: "Completed", note: "candidate — finished renders" },
    ],
  },
  {
    parentId: "queue",
    children: [
      { id: "queue-live", label: "Queue", note: "candidate — running/waiting tab" },
      { id: "queue-ledger", label: "Ledger", note: "candidate — park/resume/ops tab" },
    ],
  },
  {
    parentId: "stills",
    children: [
      { id: "stills-gallery", label: "Gallery", note: "candidate — still grid/focus" },
      { id: "stills-tagging", label: "Tagging", note: "candidate — Florence index-hour backlog" },
    ],
  },
  {
    parentId: "library",
    children: [
      { id: "library-search", label: "Search", note: "candidate — indexed media find" },
      { id: "library-rate-doors", label: "Rate doors", note: "candidate — if split from Rating route" },
    ],
  },
  {
    parentId: "workflows",
    children: [
      { id: "wf-browse", label: "Browse", note: "candidate — workflow explorer tabs" },
      { id: "wf-factory-assets", label: "Factory assets", note: "candidate — explorer tab" },
    ],
  },
];

export function canHandleClientPath(pathname: string): boolean {
  const p = pathname || "/";
  if (p === "/") return true;
  return MATCHERS.some((m) => m.id !== "home" && m.test(p));
}
