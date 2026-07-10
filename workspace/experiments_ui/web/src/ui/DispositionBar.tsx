import React, { useMemo, useState } from "react";
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

const ADVANCE_EXTEND = "advance.extend";
const ADVANCE_VARY = "advance.vary";

export function DispositionRouter({
  steps,
  activeEntries,
  lastStepId,
  busy,
  onRunStep,
  onCommitAdvanceRoutes,
}: {
  steps: DispositionCatalogMarker[];
  activeEntries: string[];
  lastStepId?: string | null;
  busy?: boolean;
  onRunStep: (stepId: string) => void;
  /** Advance multi-route: create work items then run selected factory steps. */
  onCommitAdvanceRoutes?: (opts: { extend: boolean; vary: boolean; queueNow: boolean }) => void;
}) {
  const entrySet = new Set(activeEntries);
  const entryIds = activeEntries.filter((id) => !id.includes("."));
  const [extendOn, setExtendOn] = useState(true);
  const [varyOn, setVaryOn] = useState(false);
  const [queueNow, setQueueNow] = useState(false);

  const advanceActive = entryIds.includes("advance");
  const otherEntryIds = entryIds.filter((id) => id !== "advance");

  const advanceSteps = useMemo(
    () => ({
      extend: steps.find((s) => s.id === ADVANCE_EXTEND),
      vary: steps.find((s) => s.id === ADVANCE_VARY),
    }),
    [steps],
  );

  const otherSteps = useMemo(() => {
    return steps.filter((s) => {
      const proc = s.process || "";
      if (proc === "advance") return false;
      return otherEntryIds.includes(proc);
    });
  }, [steps, otherEntryIds]);

  if (!entryIds.length) return null;
  if (!advanceActive && !otherSteps.length) return null;

  const canCommit = Boolean(onCommitAdvanceRoutes) && (extendOn || varyOn);

  return (
    <div className="disposition-router" role="region" aria-label="Disposition steps">
      {advanceActive ? (
        <div className="disposition-advance">
          <p className="disposition-router__lead">Advance routes</p>
          <div className="disposition-advance__checks" role="group" aria-label="Advance pool routes">
            <label className="disposition-advance__check">
              <input
                type="checkbox"
                checked={extendOn}
                disabled={busy || !advanceSteps.extend}
                onChange={(e) => setExtendOn(e.target.checked)}
              />
              <span>
                <strong>{advanceSteps.extend?.label ?? "Extend"}</strong>
                <em className="disposition-advance__hint">
                  {advanceSteps.extend?.hint || "Chain output into video slot"}
                </em>
              </span>
            </label>
            <label className="disposition-advance__check">
              <input
                type="checkbox"
                checked={varyOn}
                disabled={busy || !advanceSteps.vary}
                onChange={(e) => setVaryOn(e.target.checked)}
              />
              <span>
                <strong>{advanceSteps.vary?.label ?? "Vary"}</strong>
                <em className="disposition-advance__hint">
                  Front-queue replay with the same bindings (not a prompt variation yet)
                </em>
              </span>
            </label>
            <label className="disposition-advance__check disposition-advance__check--priority">
              <input
                type="checkbox"
                checked={queueNow}
                disabled={busy || !(extendOn || varyOn)}
                onChange={(e) => setQueueNow(e.target.checked)}
              />
              <span>
                <strong>Queue now</strong>
                <em className="disposition-advance__hint">Priority flag for checked routes above — not a separate pool</em>
              </span>
            </label>
          </div>
          <div className="disposition-advance__actions">
            <button
              type="button"
              className="drt-btn disposition-step-btn disposition-advance__commit"
              disabled={busy || !canCommit}
              onClick={() => onCommitAdvanceRoutes?.({ extend: extendOn, vary: varyOn, queueNow })}
            >
              Commit routes
            </button>
          </div>
        </div>
      ) : null}

      {otherSteps.length ? (
        <>
          <p className="disposition-router__lead">{advanceActive ? "Other steps" : "Next step"}</p>
          <div className="disposition-router__steps">
            {otherSteps.map((s) => (
              <button
                key={s.id}
                type="button"
                className={
                  "drt-btn disposition-step-btn" + (lastStepId === s.id ? " disposition-step-btn--ran" : "")
                }
                disabled={busy}
                title={s.hint || s.label}
                aria-pressed={lastStepId === s.id}
                onClick={() => onRunStep(s.id)}
              >
                {s.label}
                {lastStepId === s.id ? (
                  <span className="disposition-step-btn__check" aria-hidden="true">
                    {" "}
                    ✓
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        </>
      ) : null}

      {entrySet.has("investigate") ? (
        <p className="disposition-router__hint factory-muted">Investigate routes to refine, extract, advance, or retire.</p>
      ) : null}
    </div>
  );
}
