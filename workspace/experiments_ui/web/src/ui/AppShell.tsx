import React from "react";
import {
  APP_ROUTES,
  resolveRouteId,
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

/**
 * Global application frame: a single top navigation bar shared by every screen,
 * plus a content region the screen fills.
 *
 * Pipeline peers (Library · Factory · Rating · Workbench · Queue) are flat —
 * Workbench is the job-setup activity lens, not a parent chrome for the others.
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
          {pipeline.map((r) => (
            <NavLink key={r.id} route={r} active={r.id === current} />
          ))}
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
