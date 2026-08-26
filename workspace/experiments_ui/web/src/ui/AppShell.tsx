import React from "react";
import {
  APP_ROUTES,
  resolveRouteId,
  routesForGroup,
  type AppRoute,
  type AppRouteId,
} from "./routes";

function NavLink({ route, active }: { route: AppRoute; active: boolean }) {
  const onClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (
      e.defaultPrevented ||
      e.button !== 0 ||
      e.metaKey ||
      e.altKey ||
      e.ctrlKey ||
      e.shiftKey
    ) {
      return;
    }
    const url = new URL(route.path, window.location.origin);
    if (url.origin !== window.location.origin) return;
    e.preventDefault();
    const next = `${url.pathname}${url.search}${url.hash}`;
    const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (next === current) return;
    window.history.pushState({}, "", next);
    window.dispatchEvent(new PopStateEvent("popstate"));
  };
  return (
    <a
      href={route.path}
      onClick={onClick}
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
 * Pipeline peers (Library · Clips · Factory · Rating · Workbench · Queue) are flat —
 * Submit is intent-modal (doors only, not in nav); Workbench tracks job status; Queue monitors Comfy.
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
