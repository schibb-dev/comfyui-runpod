import React from "react";

/**
 * Shared screen header: a consistent title + optional subtitle and right-aligned
 * actions row. Screens should render this at the top of their content region so
 * the whole app reads as one product rather than a set of bespoke pages.
 */
export function PageHeader({
  title,
  subtitle,
  actions,
  children,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header__titles">
        <h1 className="page-header__title">{title}</h1>
        {subtitle ? <p className="page-header__subtitle">{subtitle}</p> : null}
      </div>
      {actions ? <div className="page-header__actions">{actions}</div> : null}
      {children}
    </header>
  );
}
