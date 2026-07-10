import React, { useEffect } from "react";
import { routeHref, routeHint, routeLabel, WORKBENCH_LENSES, type AppRouteId } from "./routes";

/**
 * Workbench frame: the one surface that hosts Library / Factory / Rate as lenses.
 * A shared lens bar switches between them (each keeps its own route + deep-links),
 * so the three read as a single stage of the pipeline rather than three screens.
 *
 * The active lens screen fills `.workbench__content`, a positioned region the
 * absolute `.discovery-screen` roots anchor to.
 */
export function AssetWorkbench({
  active,
  children,
}: {
  active: AppRouteId;
  children: React.ReactNode;
}) {
  // Unified lens navigation: `[` / `]` cycle between lenses. Digits/arrows are left
  // to the lens screens (e.g. Rate uses 1-5 to rate, arrows to move), so we only
  // claim bracket keys to avoid clobbering per-lens shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target;
      if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) return;
      if (t instanceof HTMLElement && t.isContentEditable) return;
      if (e.key !== "[" && e.key !== "]") return;
      const i = WORKBENCH_LENSES.indexOf(active);
      if (i < 0) return;
      e.preventDefault();
      const delta = e.key === "]" ? 1 : -1;
      const next = WORKBENCH_LENSES[(i + delta + WORKBENCH_LENSES.length) % WORKBENCH_LENSES.length];
      window.location.assign(routeHref(next));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);

  return (
    <div className="workbench">
      <div className="workbench__lensbar" role="tablist" aria-label="Workbench lenses">
        <span className="workbench__label">Workbench</span>
        {WORKBENCH_LENSES.map((id) => {
          const isActive = id === active;
          return (
            <a
              key={id}
              href={routeHref(id)}
              role="tab"
              aria-selected={isActive}
              aria-current={isActive ? "page" : undefined}
              className={"workbench__lens" + (isActive ? " workbench__lens--active" : "")}
              title={routeHint(id)}
            >
              {routeLabel(id)}
            </a>
          );
        })}
        <span className="workbench__kbd factory-muted" aria-hidden="true">
          [ ] to switch
        </span>
      </div>
      <div className="workbench__content">{children}</div>
    </div>
  );
}
