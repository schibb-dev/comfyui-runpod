import React from "react";

export type PipelineDensity = "comfortable" | "compact";

/**
 * Shared fill-height screen chrome for pipeline peers (Workbench, Queue, …).
 * Matches the Workbench column: header → optional filter row → scroll body.
 * `compact` is a density hook for denser packing (worktrays); styles start minimal.
 */
export function PipelineScreen({
  density = "comfortable",
  className,
  children,
}: {
  density?: PipelineDensity;
  className?: string;
  children: React.ReactNode;
}) {
  const classes = [
    "pipeline-screen",
    "layout",
    `pipeline-screen--${density}`,
    className || "",
  ]
    .filter(Boolean)
    .join(" ");
  return <div className={classes}>{children}</div>;
}

export function PipelineFilterRow({
  children,
  "aria-label": ariaLabel = "Filters",
}: {
  children: React.ReactNode;
  "aria-label"?: string;
}) {
  return (
    <div className="pipeline-filter-row" role="group" aria-label={ariaLabel}>
      {children}
    </div>
  );
}

export function PipelineScroll({ children }: { children: React.ReactNode }) {
  return <div className="pipeline-scroll">{children}</div>;
}

export function PipelineList({ children }: { children: React.ReactNode }) {
  return <div className="pipeline-list">{children}</div>;
}
