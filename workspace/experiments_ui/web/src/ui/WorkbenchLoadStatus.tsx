import React from "react";
import type { WorkbenchLoadPhase } from "./workbenchLoadingHint";

export function WorkbenchLoadStatus({
  hint,
  phases,
  fetching,
  compact,
}: {
  hint: string | null;
  phases?: WorkbenchLoadPhase[] | null;
  fetching?: boolean;
  compact?: boolean;
}) {
  if (!hint) return null;
  if (compact) {
    return (
      <span className="work-products-load-status work-products-load-status--compact" aria-live="polite">
        {hint}
      </span>
    );
  }
  return (
    <div
      className="work-products-empty work-products-empty--loading"
      aria-live="polite"
      aria-busy={Boolean(fetching)}
    >
      <p className="work-products-empty__title">{hint}</p>
      {phases?.length ? (
        <ol className="work-products-load-phases">
          {phases.map((phase, i) => (
            <li key={phase.id || `${phase.label || "phase"}-${i}`}>
              {phase.label || phase.id || "Working"}
              {phase.ms != null ? (
                <span className="work-products-load-phases__ms">{phase.ms}ms</span>
              ) : fetching && i === phases.length - 1 ? (
                <span className="work-products-load-phases__ms">in progress</span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
