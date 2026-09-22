import React, { useEffect, useState } from "react";
import {
  navGroups,
  resolveRouteId,
  routeLabel,
  type AppRoute,
  type AppRouteId,
} from "./routes";
import { usePhoneOverflowItems } from "./phoneChrome";
import { useDeviceContext } from "./viewport";

const SIDEBAR_KEY = "app-sidebar-collapsed-v1";

function loadSidebarCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}

function persistSidebarCollapsed(collapsed: boolean) {
  try {
    localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function NavLink({
  route,
  active,
  className,
  collapsed,
  onNavigate,
  children,
}: {
  route: AppRoute;
  active: boolean;
  className: string;
  collapsed?: boolean;
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
      title={route.hint || route.label}
    >
      {children ?? (
        <>
          {collapsed ? <span className="app-sidebar__mark">{route.mark}</span> : route.label}
        </>
      )}
    </a>
  );
}

function NavSections({
  active,
  collapsed,
  linkClass,
  onNavigate,
}: {
  active: AppRouteId;
  collapsed?: boolean;
  linkClass: string;
  onNavigate?: () => void;
}) {
  return (
    <>
      {navGroups().map((group) => (
        <section key={group.id} className="app-nav-section" aria-label={group.label}>
          {collapsed ? null : <p className="app-nav-section__label">{group.label}</p>}
          <nav className="app-nav-section__links">
            {group.routes.map((r) => (
              <NavLink
                key={r.id}
                route={r}
                active={r.id === active}
                className={linkClass}
                collapsed={collapsed}
                onNavigate={onNavigate}
              />
            ))}
          </nav>
        </section>
      ))}
    </>
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
            <NavSections
              active={active}
              linkClass="app-drawer__link"
              onNavigate={() => setOpen(false)}
            />
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

function DesktopSidebar({ active }: { active: AppRouteId }) {
  const [collapsed, setCollapsed] = useState(() => loadSidebarCollapsed());
  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      persistSidebarCollapsed(next);
      return next;
    });
  };
  return (
    <aside className={`app-sidebar${collapsed ? " app-sidebar--collapsed" : ""}`} aria-label="Primary">
      <div className="app-sidebar__brand">
        <a href="/" className="app-sidebar__brand-link" title="ComfyUI Runpod — Experiments UI">
          <span className="app-nav__brand-dot" aria-hidden="true" />
          {collapsed ? null : <span className="app-sidebar__brand-text">Factory</span>}
        </a>
        <button
          type="button"
          className="app-sidebar__collapse"
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand menu" : "Collapse menu"}
          title={collapsed ? "Expand menu" : "Collapse menu"}
          onClick={toggle}
        >
          {collapsed ? "›" : "‹"}
        </button>
      </div>
      <div className="app-sidebar__nav">
        <NavSections active={active} collapsed={collapsed} linkClass="app-sidebar__link" />
      </div>
    </aside>
  );
}

/**
 * Global application frame: desktop collapsible sidebar, phone hamburger,
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
  const phone = device === "phone";

  return (
    <div className={`app-shell${phone ? " app-shell--phone" : " app-shell--desktop"}`}>
      {phone ? <PhoneMenu active={current} /> : <DesktopSidebar active={current} />}
      <div className={`app-shell__main${phone ? " app-shell__main--phone" : ""}`}>{children}</div>
    </div>
  );
}
