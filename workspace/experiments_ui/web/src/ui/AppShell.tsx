import React from "react";
import {
  APP_ROUTES,
  isWorkbenchLens,
  resolveRouteId,
  routeHref,
  routesForGroup,
  type AppRoute,
  type AppRouteId,
} from "./routes";

function NavLink({ route, active }: { route: AppRoute; active: boolean }) {
  return (
    <a
      href={route.path}
      className={`app-nav__link${active ? " app-nav__link--active" : ""}`}
      aria-current={active ? "page" : undefined}
      title={route.hint}
    >
      {route.label}
    </a>
  );
}

/** Render pipeline nav, collapsing the Library/Factory/Rate lenses into one Workbench entry. */
function pipelineNavItems(pipeline: AppRoute[], current: AppRouteId): React.ReactNode[] {
  const items: React.ReactNode[] = [];
  let workbenchInserted = false;
  for (const r of pipeline) {
    if (isWorkbenchLens(r.id)) {
      if (!workbenchInserted) {
        workbenchInserted = true;
        const active = isWorkbenchLens(current);
        items.push(
          <a
            key="workbench"
            href={routeHref("library")}
            className={`app-nav__link${active ? " app-nav__link--active" : ""}`}
            aria-current={active ? "page" : undefined}
            title="Library · Factory · Rate"
          >
            Workbench
          </a>,
        );
      }
      continue;
    }
    items.push(<NavLink key={r.id} route={r} active={r.id === current} />);
  }
  return items;
}

/**
 * Global application frame: a single top navigation bar shared by every screen,
 * plus a content region the screen fills. Nav order follows the production
 * pipeline (Queue → Factory → Library → Rate) with tools grouped to the right.
 *
 * The active screen is derived from the current path unless `active` is passed.
 */
export function AppShell({
  active,
  children,
}: {
  active?: AppRouteId;
  children: React.ReactNode;
}) {
  const current = active ?? resolveRouteId(typeof window !== "undefined" ? window.location.pathname : "/");
  const pipeline = routesForGroup("pipeline");
  const tools = routesForGroup("tools");
  return (
    <>
      <header className="app-nav" aria-label="Primary">
        <a href="/" className="app-nav__brand" title="ComfyUI Runpod — Experiments UI">
          <span className="app-nav__brand-dot" aria-hidden="true" />
          <span className="app-nav__brand-text">Factory</span>
        </a>
        <nav className="app-nav__group app-nav__group--pipeline" aria-label="Pipeline">
          {pipelineNavItems(pipeline, current)}
        </nav>
        <span className="app-nav__spacer" aria-hidden="true" />
        <nav className="app-nav__group app-nav__group--tools" aria-label="Tools">
          {tools.map((r) => (
            <NavLink key={r.id} route={r} active={r.id === current} />
          ))}
        </nav>
      </header>
      <div className="app-shell__main">{children}</div>
    </>
  );
}

export { APP_ROUTES };
