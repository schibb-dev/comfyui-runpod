import React, { useEffect, useMemo, useState } from "react";
import {
  navChildIsActive,
  navGroups,
  resolveRouteId,
  routeLabel,
  type AppNavChild,
  type AppNavGroup,
  type AppRoute,
  type AppRouteId,
} from "./routes";
import { usePhoneOverflowItems } from "./phoneChrome";
import { useDeviceContext } from "./viewport";

const SIDEBAR_KEY = "app-sidebar-collapsed-v1";
const NAV_OPEN_KEY = "app-nav-open-v1";

type NavOpenState = {
  groups: Partial<Record<AppNavGroup, boolean>>;
  parents: Partial<Record<AppRouteId, boolean>>;
};

const DEFAULT_GROUP_OPEN: Record<AppNavGroup, boolean> = {
  now: true,
  browse: true,
  produce: true,
  care: true,
  labs: true,
};

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

function loadNavOpen(): NavOpenState {
  try {
    const raw = localStorage.getItem(NAV_OPEN_KEY);
    if (!raw) return { groups: {}, parents: {} };
    const parsed = JSON.parse(raw) as NavOpenState;
    return {
      groups: parsed?.groups && typeof parsed.groups === "object" ? parsed.groups : {},
      parents: parsed?.parents && typeof parsed.parents === "object" ? parsed.parents : {},
    };
  } catch {
    return { groups: {}, parents: {} };
  }
}

function persistNavOpen(state: NavOpenState) {
  try {
    localStorage.setItem(NAV_OPEN_KEY, JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

function usePathname(): string {
  const [pathname, setPathname] = useState(
    () => (typeof window !== "undefined" ? window.location.pathname : "/"),
  );
  useEffect(() => {
    const sync = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  return pathname;
}

function useNavOpenState(active: AppRouteId, pathname: string) {
  const [open, setOpen] = useState<NavOpenState>(() => loadNavOpen());

  // Keep the active trail expanded so the current page stays findable.
  useEffect(() => {
    setOpen((prev) => {
      let changed = false;
      const groups = { ...prev.groups };
      const parents = { ...prev.parents };
      for (const group of navGroups()) {
        const hasActive = group.routes.some(
          (r) =>
            r.id === active || (r.children || []).some((c) => navChildIsActive(c, pathname)),
        );
        if (hasActive && groups[group.id] === false) {
          groups[group.id] = true;
          changed = true;
        }
        for (const r of group.routes) {
          if (!r.children?.length) continue;
          const childActive = r.children.some((c) => navChildIsActive(c, pathname));
          if ((r.id === active || childActive) && parents[r.id] === false) {
            parents[r.id] = true;
            changed = true;
          }
        }
      }
      if (!changed) return prev;
      const next = { groups, parents };
      persistNavOpen(next);
      return next;
    });
  }, [active, pathname]);

  const isGroupOpen = (id: AppNavGroup) => open.groups[id] ?? DEFAULT_GROUP_OPEN[id];
  const isParentOpen = (id: AppRouteId) => open.parents[id] ?? true;

  const toggleGroup = (id: AppNavGroup) => {
    setOpen((prev) => {
      const nextVal = !(prev.groups[id] ?? DEFAULT_GROUP_OPEN[id]);
      const next = { ...prev, groups: { ...prev.groups, [id]: nextVal } };
      persistNavOpen(next);
      return next;
    });
  };

  const toggleParent = (id: AppRouteId) => {
    setOpen((prev) => {
      const nextVal = !(prev.parents[id] ?? true);
      const next = { ...prev, parents: { ...prev.parents, [id]: nextVal } };
      persistNavOpen(next);
      return next;
    });
  };

  return { isGroupOpen, isParentOpen, toggleGroup, toggleParent };
}

function navigateTo(href: string, onNavigate?: () => void) {
  const url = new URL(href, window.location.origin);
  if (url.origin !== window.location.origin) {
    window.location.assign(href);
    return;
  }
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next !== current) {
    window.history.pushState({}, "", next);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }
  onNavigate?.();
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
    e.preventDefault();
    navigateTo(route.path, onNavigate);
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

function NavChildLink({
  child,
  active,
  className,
  onNavigate,
}: {
  child: AppNavChild;
  active: boolean;
  className: string;
  onNavigate?: () => void;
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
    e.preventDefault();
    navigateTo(child.path, onNavigate);
  };
  return (
    <a
      href={child.path}
      onClick={onClick}
      className={`${className}${active ? ` ${className}--active` : ""}`}
      aria-current={active ? "page" : undefined}
      title={child.hint || child.label}
    >
      {child.label}
    </a>
  );
}

function NavSections({
  active,
  pathname,
  collapsed,
  linkClass,
  childLinkClass,
  onNavigate,
}: {
  active: AppRouteId;
  pathname: string;
  collapsed?: boolean;
  linkClass: string;
  childLinkClass: string;
  onNavigate?: () => void;
}) {
  const { isGroupOpen, isParentOpen, toggleGroup, toggleParent } = useNavOpenState(active, pathname);
  const groups = useMemo(() => navGroups(), []);

  return (
    <>
      {groups.map((group) => {
        const groupOpen = collapsed ? true : isGroupOpen(group.id);
        const groupHasActive = group.routes.some(
          (r) =>
            r.id === active || (r.children || []).some((c) => navChildIsActive(c, pathname)),
        );
        return (
          <section
            key={group.id}
            className={`app-nav-section${groupOpen ? "" : " app-nav-section--collapsed"}${
              groupHasActive ? " app-nav-section--has-active" : ""
            }`}
            aria-label={group.label}
          >
            {collapsed ? null : (
              <button
                type="button"
                className="app-nav-section__toggle"
                aria-expanded={groupOpen}
                onClick={() => toggleGroup(group.id)}
              >
                <span className="app-nav-section__label">{group.label}</span>
                <span className="app-nav-section__chevron" aria-hidden="true">
                  {groupOpen ? "▾" : "▸"}
                </span>
              </button>
            )}
            {groupOpen ? (
              <nav className="app-nav-section__links">
                {group.routes.map((r) => {
                  const hasKids = Boolean(r.children?.length);
                  const parentOpen = collapsed || !hasKids ? true : isParentOpen(r.id);
                  const kids = !collapsed && hasKids && parentOpen ? r.children! : null;
                  const childActive = (r.children || []).some((c) => navChildIsActive(c, pathname));
                  return (
                    <div
                      key={r.id}
                      className={`app-nav-item${hasKids ? " app-nav-item--branch" : ""}${
                        parentOpen ? "" : " app-nav-item--collapsed"
                      }`}
                    >
                      <div className="app-nav-item__row">
                        <NavLink
                          route={r}
                          active={r.id === active && !childActive}
                          className={linkClass}
                          collapsed={collapsed}
                          onNavigate={onNavigate}
                        />
                        {!collapsed && hasKids ? (
                          <button
                            type="button"
                            className="app-nav-item__toggle"
                            aria-label={parentOpen ? `Collapse ${r.label}` : `Expand ${r.label}`}
                            aria-expanded={parentOpen}
                            title={parentOpen ? `Collapse ${r.label}` : `Expand ${r.label}`}
                            onClick={() => toggleParent(r.id)}
                          >
                            {parentOpen ? "▾" : "▸"}
                          </button>
                        ) : null}
                      </div>
                      {kids ? (
                        <div className="app-nav-item__children" role="group" aria-label={`${r.label} sections`}>
                          {kids.map((child) => (
                            <NavChildLink
                              key={child.id}
                              child={child}
                              active={navChildIsActive(child, pathname)}
                              className={childLinkClass}
                              onNavigate={onNavigate}
                            />
                          ))}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </nav>
            ) : null}
          </section>
        );
      })}
    </>
  );
}

function PhoneMenu({ active, pathname }: { active: AppRouteId; pathname: string }) {
  const [open, setOpen] = useState(false);
  const overflow = usePhoneOverflowItems();

  useEffect(() => {
    setOpen(false);
  }, [active, pathname]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const activeChildLabel = (() => {
    for (const group of navGroups()) {
      for (const r of group.routes) {
        for (const child of r.children || []) {
          if (navChildIsActive(child, pathname)) return `${r.label} · ${child.label}`;
        }
      }
    }
    return null;
  })();

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
        <h1 className="app-phonebar__title">{activeChildLabel || routeLabel(active)}</h1>
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
              pathname={pathname}
              linkClass="app-drawer__link"
              childLinkClass="app-drawer__child-link"
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

function DesktopSidebar({ active, pathname }: { active: AppRouteId; pathname: string }) {
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
        <NavSections
          active={active}
          pathname={pathname}
          collapsed={collapsed}
          linkClass="app-sidebar__link"
          childLinkClass="app-sidebar__child-link"
        />
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
  const pathname = usePathname();
  const current = active ?? resolveRouteId(pathname);
  const phone = device === "phone";

  return (
    <div className={`app-shell${phone ? " app-shell--phone" : " app-shell--desktop"}`}>
      {phone ? <PhoneMenu active={current} pathname={pathname} /> : <DesktopSidebar active={current} pathname={pathname} />}
      <div className={`app-shell__main${phone ? " app-shell__main--phone" : ""}`}>{children}</div>
    </div>
  );
}
