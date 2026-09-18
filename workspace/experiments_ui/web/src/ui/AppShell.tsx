import React, { useEffect, useState } from "react";
import {
  APP_ROUTES,
  resolveRouteId,
  routeLabel,
  routesForGroup,
  type AppRoute,
  type AppRouteId,
} from "./routes";
import { usePhoneOverflowItems } from "./phoneChrome";
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

function PhoneMenu({ active }: { active: AppRouteId }) {
  const [open, setOpen] = useState(false);
  const overflow = usePhoneOverflowItems();

  useEffect(() => {
    setOpen(false);
  }, [active]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <header className="app-phonebar">
        <button
          type="button"
          className="app-phonebar__burger"
          aria-label="Menu"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="app-phonebar__burger-lines" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        </button>
        <h1 className="app-phonebar__title">{routeLabel(active)}</h1>
      </header>
      {open ? (
        <div className="app-drawer">
          <div className="app-drawer__panel" role="dialog" aria-modal="true" aria-label="Menu">
            {overflow.length ? (
              <>
                <p className="app-drawer__section-label">This screen</p>
                <div className="app-drawer__nav">
                  {overflow.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className="app-drawer__link"
                      title={item.hint}
                      onClick={() => {
                        setOpen(false);
                        item.onSelect();
                      }}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </>
            ) : null}
            <p className="app-drawer__section-label">Pipeline</p>
            <nav className="app-drawer__nav" aria-label="Pipeline">
              {routesForGroup("pipeline").map((r) => (
                <NavLink
                  key={r.id}
                  route={r}
                  active={r.id === active}
                  className="app-drawer__link"
                  onNavigate={() => setOpen(false)}
                />
              ))}
            </nav>
            <p className="app-drawer__section-label">Tools</p>
            <nav className="app-drawer__nav" aria-label="Tools">
              {routesForGroup("tools").map((r) => (
                <NavLink
                  key={r.id}
                  route={r}
                  active={r.id === active}
                  className="app-drawer__link"
                  onNavigate={() => setOpen(false)}
                />
              ))}
            </nav>
          </div>
          <button
            type="button"
            className="app-drawer__backdrop"
            aria-label="Close menu"
            onClick={() => setOpen(false)}
          />
        </div>
      ) : null}
    </>
  );
}

/**
 * Global application frame: desktop top nav, phone hamburger + focused screen,
 * plus a content region the screen fills.
 *
 * Submit is intent-modal (doors only, not in nav).
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
      {phone ? (
        <PhoneMenu active={current} />
      ) : (
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
    </>
  );
}

export { APP_ROUTES };
