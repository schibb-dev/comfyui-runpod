import React, { useMemo } from "react";
import { formatIsoDateTime } from "./locale";
import type { DispositionCatalogMarker, DispositionOutcome } from "./types";

function labelForId(id: string, catalog: DispositionCatalogMarker[]): string {
  return catalog.find((m) => m.id === id)?.label || id;
}

function formatOutcome(outcome: DispositionOutcome | null | undefined): string | null {
  if (!outcome?.action) return null;
  const action = outcome.action;
  const detail = outcome.detail;
  if (action === "toggle" && detail && typeof detail === "object") {
    const d = detail as { marker?: string; on?: boolean };
    return d.on ? `Set marker: ${d.marker}` : `Cleared: ${d.marker}`;
  }
  if (action.startsWith("step:")) {
    const step = action.slice("step:".length);
    if (detail && typeof detail === "object") {
      const r = detail as Record<string, unknown>;
      const inner = (r.result as Record<string, unknown> | undefined) || r;
      if (inner.moved) return `${step} → moved to trash`;
      if (inner.archived) return `${step} → archived`;
      if (inner.trim_ui) return `${step} → open trim in library`;
      if (inner.ok && (inner.extend || inner.replay_of_job_key)) {
        return inner.extend_fallback === "replay"
          ? `${step} → replay queued`
          : `${step} → extend/replay queued`;
      }
      if (inner.reason) return `${step} → ${String(inner.reason)}`;
      if (inner.error) return `${step} → ${String(inner.error)}`;
      if (inner.toggled) return `${step} → routed`;
    }
    return `Ran step: ${step}`;
  }
  return action;
}

const ENTRY_NEXT: Record<string, string> = {
  refine: "Pick Aspect, Quality, or Edit above — or clear Refine when this clip is handled.",
  investigate: "Route to salvage, pipeline, fix, or retire — then run that entry’s steps.",
  extract: "Run a salvage step (frame / clip / reference).",
  advance: "Run Extend, Vary, or Queue now — then watch for a new factory output.",
  retire: "Run Trash or Archive to remove this from active work.",
  park: "No action required now — revisit later or clear Park.",
};

function formatTime(iso: string): string {
  return formatIsoDateTime(iso);
}

export function DispositionStatusPanel({
  markers,
  catalog,
  updatedAt,
  lastOutcome,
  lastActionMessage,
  lastTriagedAt,
  triagePassCount = 0,
}: {
  markers: string[];
  catalog: DispositionCatalogMarker[];
  updatedAt?: string | null;
  lastOutcome?: DispositionOutcome | null;
  lastActionMessage?: string;
  saved?: boolean;
  lastTriagedAt?: string | null;
  triagePassCount?: number;
}) {
  const entryIds = markers.filter((m) => catalog.some((c) => c.id === m && c.kind === "entry"));
  const stepIds = markers.filter((m) => catalog.some((c) => m === c.id && c.kind === "step"));
  const primaryEntry = entryIds[0] ?? null;

  const outcomeLine = useMemo(() => {
    if (lastActionMessage) return lastActionMessage;
    return formatOutcome(lastOutcome);
  }, [lastActionMessage, lastOutcome]);

  const nextHint = primaryEntry ? ENTRY_NEXT[primaryEntry] ?? "Complete a step or clear the entry marker." : null;

  return (
    <div className="disposition-status" aria-live="polite">
      <div className="disposition-status__triage">
        <span className="disposition-status__triage-label">Triage</span>
        {lastTriagedAt ? (
          <span className="disposition-status__triage-detail factory-muted">
            Last pass #{triagePassCount} ·{" "}
            <time className="mono" dateTime={lastTriagedAt} title={lastTriagedAt}>
              {formatTime(lastTriagedAt)}
            </time>
          </span>
        ) : (
          <span className="disposition-status__triage-detail factory-muted">In batch — use Dismiss batch when finished</span>
        )}
      </div>

      {!markers.length ? (
        <div className="disposition-status disposition-status--empty disposition-status--nested">
          <p className="disposition-status__lead">No disposition</p>
          <p className="disposition-status__detail factory-muted">
            Optional — set an entry above to commit editing work, or leave empty and advance.
          </p>
        </div>
      ) : (
        <>
          <div className="disposition-status__head">
            <span className="disposition-status__saved-badge">Disposition</span>
            {updatedAt ? (
              <time className="disposition-status__time mono" dateTime={updatedAt} title={updatedAt}>
                {formatTime(updatedAt)}
              </time>
            ) : null}
          </div>
          <div className="disposition-status__markers">
            {entryIds.map((id) => (
              <span key={id} className="disposition-status__pill disposition-status__pill--entry">
                {labelForId(id, catalog)}
              </span>
            ))}
            {stepIds.map((id) => (
              <span key={id} className="disposition-status__pill disposition-status__pill--step">
                {labelForId(id, catalog)}
              </span>
            ))}
          </div>
          {outcomeLine ? (
            <p className="disposition-status__outcome">
              <span className="disposition-status__outcome-label">Last action</span>
              {outcomeLine}
            </p>
          ) : null}
          {nextHint ? (
            <p className="disposition-status__next">
              <span className="disposition-status__next-label">Next</span>
              {nextHint}
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}
