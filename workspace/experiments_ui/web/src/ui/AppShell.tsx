import React, { useEffect, useId, useState } from "react";
import {
  APP_ROUTES,
  phoneMoreRoutes,
  phoneTabRoutes,
  resolveRouteId,
  routesForGroup,
  type AppRoute,
  type AppRouteId,
} from "./routes";
import { useDeviceContext } from "./viewport";

function NavLink({
  route,
  active,
  className,
  onNavigate,
  children,
}: {
  route: AppRoute;
  active: boolean;
  className: string;
  onNavigate?: () => void;
  children?: React.ReactNode;
}) {
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
    if (next !== current) {
      window.history.pushState({}, "", next);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }
    onNavigate?.();
  };
  return (
    <a
      href={route.path}
      onClick={onClick}
      className={`${className}${active ? ` ${className}--active` : ""}`}
      aria-current={active ? "page" : undefined}
      title={route.hint}
    >
      {children ?? route.label}
    </a>
  );
}

function PhoneTabBar({ active }: { active: AppRouteId }) {
  const [moreOpen, setMoreOpen] = useState(false);
  const titleId = useId();
  const moreRoutes = phoneMoreRoutes();
  const moreActive = moreRoutes.some((r) => r.id === active);

  useEffect(() => {
    setMoreOpen(false);
  }, [active]);

  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMoreOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moreOpen]);

  return (
    <>
      {moreOpen ? (
        <div className="app-more">
          <button
            type="button"
            className="app-more__backdrop"
            aria-label="Close more destinations"
            onClick={() => setMoreOpen(false)}
          />
          <div className="app-more__sheet" role="dialog" aria-modal="true" aria-labelledby={titleId}>
            <div className="app-more__head">
              <h2 id={titleId} className="app-more__title">
                More
              </h2>
              <button type="button" className="app-more__close" onClick={() => setMoreOpen(false)}>
                Close
              </button>
            </div>
            <nav className="app-more__list" aria-label="More destinations">
              {moreRoutes.map((r) => (
                <NavLink
                  key={r.id}
                  route={r}
                  active={r.id === active}
                  className="app-more__link"
                  onNavigate={() => setMoreOpen(false)}
                />
              ))}
            </nav>
          </div>
        </div>
      ) : null}
      <nav className="app-tabbar" aria-label="Primary">
        {phoneTabRoutes().map((r) => (
          <NavLink key={r.id} route={r} active={r.id === active} className="app-tabbar__tab">
            {r.id === "workbench" ? "Bench" : r.label}
          </NavLink>
        ))}
        <button
          type="button"
          className={`app-tabbar__tab app-tabbar__more${moreActive || moreOpen ? " app-tabbar__tab--active" : ""}`}
          aria-expanded={moreOpen}
          aria-current={moreActive ? "page" : undefined}
          onClick={() => setMoreOpen((open) => !open)}
        >
          More
        </button>
      </nav>
    </>
  );
}

/**
 * Global application frame: desktop top nav, phone bottom tabs, plus a content
 * region the screen fills.
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
  const { device } = useDeviceContext();
  const current = active ?? resolveRouteId(typeof window !== "undefined" ? window.location.pathname : "/");
  const pipeline = routesForGroup("pipeline");
  const tools = routesForGroup("tools");
  const phone = device === "phone";

  return (
    <>
      {phone ? null : (
        <header className="app-nav" aria-label="Primary">
          <a href="/" className="app-nav__brand" title="ComfyUI Runpod — Experiments UI">
            <span className="app-nav__brand-dot" aria-hidden="true" />
            <span className="app-nav__brand-text">Factory</span>
          </a>
          <nav className="app-nav__group app-nav__group--pipeline" aria-label="Pipeline">
            {pipeline.map((r) => (
              <NavLink key={r.id} route={r} active={r.id === current} className="app-nav__link" />
            ))}
          </nav>
          <span className="app-nav__spacer" aria-hidden="true" />
          <nav className="app-nav__group app-nav__group--tools" aria-label="Tools">
            {tools.map((r) => (
              <NavLink key={r.id} route={r} active={r.id === current} className="app-nav__link" />
            ))}
          </nav>
        </header>
      )}
      <div className={`app-shell__main${phone ? " app-shell__main--phone" : ""}`}>{children}</div>
      {phone ? <PhoneTabBar active={current} /> : null}
    </>
  );
}

export { APP_ROUTES };
