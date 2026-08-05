import React, { useEffect, useMemo, useState } from "react";
import type {
  DispositionCatalogMarker,
  DispositionPromotions,
  DispositionReasonDetail,
} from "./types";

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

/** Catalog-driven refine (or other process) reason axes + optional modifiers / Other note. */
export function DispositionReasonsPanel({
  reasons,
  activeEntries,
  markers,
  reasonDetail,
  busy,
  onToggleReason,
}: {
  reasons: DispositionCatalogMarker[];
  activeEntries: string[];
  markers: string[];
  reasonDetail: Record<string, DispositionReasonDetail>;
  busy?: boolean;
  onToggleReason: (opts: {
    markerId: string;
    on: boolean;
    modifiers?: string[];
    note?: string;
  }) => void;
}) {
  const entryIdsKey = activeEntries.join("|");
  const entrySet = useMemo(() => new Set(activeEntries), [entryIdsKey]);
  const markerSet = new Set(markers);
  const visible = useMemo(() => {
    return reasons.filter((r) => {
      const proc = String(r.process || "").trim();
      return Boolean(proc && entrySet.has(proc));
    });
  }, [reasons, entrySet]);

  const noteReasons = useMemo(() => visible.filter((r) => r.requires_note), [visible]);
  const axisReasons = useMemo(() => visible.filter((r) => !r.requires_note), [visible]);

  const [draftNotes, setDraftNotes] = useState<Record<string, string>>({});
  const noteIdsKey = noteReasons.map((r) => r.id).join("|");
  const detailKey = JSON.stringify(reasonDetail);
  useEffect(() => {
    const next: Record<string, string> = {};
    for (const r of noteReasons) {
      next[r.id] = reasonDetail[r.id]?.note || "";
    }
    setDraftNotes(next);
    // Intentionally keyed by serialized detail + note reason ids.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [markers.join("|"), detailKey, noteIdsKey]);

  if (!visible.length) return null;

  const toggleModifier = (reason: DispositionCatalogMarker, modId: string) => {
    const mode = String(reason.modifier_mode || "none").toLowerCase();
    const current = new Set(reasonDetail[reason.id]?.modifiers || []);
    if (mode === "exclusive") {
      const next = current.has(modId) ? [] : [modId];
      onToggleReason({ markerId: reason.id, on: true, modifiers: next });
      return;
    }
    if (mode === "multi") {
      if (current.has(modId)) current.delete(modId);
      else current.add(modId);
      onToggleReason({ markerId: reason.id, on: true, modifiers: [...current] });
    }
  };

  return (
    <div className="disposition-reasons" role="region" aria-label="Refine reasons">
      <p className="disposition-router__lead">Reasons</p>
      <div className="disposition-reasons__axes" role="group" aria-label="Reason axes">
        {axisReasons.map((r) => {
          const isOn = markerSet.has(r.id);
          const mods = r.modifiers || [];
          const mode = String(r.modifier_mode || "none").toLowerCase();
          const activeMods = new Set(reasonDetail[r.id]?.modifiers || []);
          return (
            <div key={r.id} className={"disposition-reason" + (isOn ? " disposition-reason--on" : "")}>
              <button
                type="button"
                className={"drt-btn disposition-reason__axis" + (isOn ? " disposition-reason__axis--on" : "")}
                disabled={busy}
                title={r.hint || r.label}
                aria-pressed={isOn}
                onClick={() => onToggleReason({ markerId: r.id, on: !isOn })}
              >
                {r.label}
              </button>
              {isOn && mods.length > 0 && mode !== "none" ? (
                <div className="disposition-reason__mods" role="group" aria-label={`${r.label} modifiers`}>
                  {mods.map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      className={
                        "drt-btn disposition-reason__mod" +
                        (activeMods.has(m.id) ? " disposition-reason__mod--on" : "")
                      }
                      disabled={busy}
                      title={m.hint || m.label}
                      aria-pressed={activeMods.has(m.id)}
                      onClick={() => toggleModifier(r, m.id)}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      {noteReasons.map((r) => {
        const isOn = markerSet.has(r.id);
        const draft = draftNotes[r.id] ?? "";
        return (
          <div key={r.id} className="disposition-reason disposition-reason--other">
            <label className="disposition-reason__other-check">
              <input
                type="checkbox"
                checked={isOn}
                disabled={busy}
                onChange={(e) => {
                  const nextOn = e.target.checked;
                  if (nextOn && !draft.trim()) {
                    return;
                  }
                  onToggleReason({
                    markerId: r.id,
                    on: nextOn,
                    note: draft.trim() || undefined,
                  });
                }}
              />
              <span>
                <strong>{r.label}</strong>
                <em className="disposition-advance__hint">{r.hint || "Short note required"}</em>
              </span>
            </label>
            <div className="disposition-reason__note-row">
              <input
                type="text"
                className="disposition-reason__note"
                value={draft}
                disabled={busy}
                placeholder="What else?"
                aria-label={`${r.label} note`}
                onChange={(e) => setDraftNotes((prev) => ({ ...prev, [r.id]: e.target.value }))}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    const text = draft.trim();
                    if (!text) return;
                    onToggleReason({ markerId: r.id, on: true, note: text });
                  }
                }}
              />
              <button
                type="button"
                className="drt-btn disposition-reason__note-save"
                disabled={busy || !draft.trim()}
                onClick={() => onToggleReason({ markerId: r.id, on: true, note: draft.trim() })}
              >
                {isOn ? "Update" : "Save"}
              </button>
            </div>
          </div>
        );
      })}
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
        <p className="disposition-router__hint factory-muted">Investigate — look closer before routing.</p>
      ) : null}
    </div>
  );
}
