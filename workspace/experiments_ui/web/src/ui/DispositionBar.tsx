import React from "react";
import type { DispositionCatalogMarker, DispositionPromotions } from "./types";

export function DispositionBar({
  entries,
  markers,
  promotions,
  busy,
  embedded = false,
  onToggle,
  onEditCatalog,
}: {
  entries: DispositionCatalogMarker[];
  markers: string[];
  promotions?: DispositionPromotions | null;
  busy?: boolean;
  embedded?: boolean;
  onToggle: (markerId: string, on: boolean) => void;
  onEditCatalog?: () => void;
}) {
  const promote = new Set(promotions?.promote ?? []);
  const secondary = new Set(promotions?.secondary ?? []);
  const active = new Set(markers);

  return (
    <div className={"disposition-bar" + (embedded ? " disposition-bar--embedded" : "")} role="group" aria-label="Disposition — what to do next">
      <div className={embedded ? "drq-rate-bar drq-rate-bar--disposition" : "disposition-btns"}>
        {entries.map((e) => {
          const isOn = active.has(e.id);
          const isPromote = promote.has(e.id);
          const isSecondary = secondary.has(e.id);
          return (
            <button
              key={e.id}
              type="button"
              className={
                (embedded ? "drq-star-btn drq-disposition-tile " : "disposition-btn ") +
                (isOn ? " disposition-btn--on" : "") +
                (isPromote ? " disposition-btn--promote" : "") +
                (isSecondary && !isPromote ? " disposition-btn--secondary" : "")
              }
              disabled={busy}
              title={e.hint || e.label}
              aria-pressed={isOn}
              onClick={() => onToggle(e.id, !isOn)}
            >
              <span className="drq-disposition-tile__label">{e.label}</span>
            </button>
          );
        })}
      </div>
      {onEditCatalog ? (
        <button type="button" className="drt-btn disposition-edit-btn" disabled={busy} onClick={onEditCatalog}>
          Edit markers…
        </button>
      ) : null}
    </div>
  );
}

export function DispositionRouter({
  steps,
  activeEntries,
  lastStepId,
  busy,
  onRunStep,
}: {
  steps: DispositionCatalogMarker[];
  activeEntries: string[];
  lastStepId?: string | null;
  busy?: boolean;
  onRunStep: (stepId: string) => void;
}) {
  const entrySet = new Set(activeEntries);
  const entryIds = activeEntries.filter((id) => !id.includes("."));
  if (!entryIds.length) return null;

  const visibleSteps = steps.filter((s) => {
    const proc = s.process || "";
    return entryIds.includes(proc);
  });
  if (!visibleSteps.length) return null;

  return (
    <div className="disposition-router" role="region" aria-label="Disposition steps">
      <p className="disposition-router__lead">Next step</p>
      <div className="disposition-router__steps">
        {visibleSteps.map((s) => (
          <button
            key={s.id}
            type="button"
            className={
              "drt-btn disposition-step-btn" +
              (lastStepId === s.id ? " disposition-step-btn--ran" : "")
            }
            disabled={busy}
            title={s.hint || s.label}
            aria-pressed={lastStepId === s.id}
            onClick={() => onRunStep(s.id)}
          >
            {s.label}
            {lastStepId === s.id ? <span className="disposition-step-btn__check" aria-hidden="true"> ✓</span> : null}
          </button>
        ))}
      </div>
      {entrySet.has("investigate") ? (
        <p className="disposition-router__hint factory-muted">Investigate routes to refine, extract, advance, or retire.</p>
      ) : null}
    </div>
  );
}
