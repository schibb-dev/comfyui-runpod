import React from "react";
import type { WorkProductItem } from "./types";
import { StillTagResultsGallery } from "./StillTagResultsGallery";
import { StillTagTagsPanel } from "./StillTagResultTags";
import { stillTagCurrentContentId, stillTagProgressLabel, stillTagStatusLabel } from "./stillTagWorkProduct";
import {
  workProductDisplayTitle,
  workProductIdentityLabel,
  workProductKindStatusLabel,
  workProductPreviewUrl,
} from "./workProductKind";
import { AppetitePreviewFrame } from "./AppetitePreviewBadge";
import { discoveryLibraryHref, queueHref } from "./discoveryDeepLink";

function formatWhen(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function StillTagWorkbenchPanel({ item }: { item: WorkProductItem }) {
  const runId = String(item.still_tag_run_id || item.job_key || "").trim();
  const status = workProductKindStatusLabel(item) || stillTagStatusLabel(item);
  const running = String(item.status || "").toLowerCase() === "running";
  const progress = stillTagProgressLabel(item);
  const construction = item.construction && typeof item.construction === "object" ? item.construction : {};
  const provider = String(construction.provider || "comfy");
  const modelPin = String(construction.model_pin || item.spec_title || "").replace(/^Florence auto-tag · /, "");
  const pinPolicy = String(construction.pin_policy || "—");
  const liveUrl = workProductPreviewUrl(item);
  const liveRel =
    item.still_tag_output?.current_relpath || item.output_relpath || item.parent_output_relpath || null;
  const liveTags = item.still_tag_output?.tags || [];
  const batchError = String(item.error || "").trim() || null;

  return (
    <div className="still-tag-workbench">
      <header className="still-tag-workbench__head">
        <div>
          <h2 className="still-tag-workbench__title">{workProductDisplayTitle(item)}</h2>
          <p className="still-tag-workbench__sub factory-muted">
            <span className={`still-tag-workbench__status still-tag-workbench__status--${status}`}>{status}</span>
            {progress ? ` · ${progress}` : null}
            {" · "}
            <code title={runId}>{workProductIdentityLabel(item)}</code>
          </p>
        </div>
        <div className="still-tag-workbench__links">
          <a className="drt-btn" href="/discovery/stills">
            Stills gallery
          </a>
          <a className="drt-btn" href={queueHref()}>
            Queue
          </a>
          {liveRel ? (
            <a className="drt-btn" href={discoveryLibraryHref(liveRel)}>
              Open still
            </a>
          ) : null}
        </div>
      </header>

      <dl className="still-tag-workbench__meta">
        <div>
          <dt>Provider</dt>
          <dd>{provider}</dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>{modelPin || "—"}</dd>
        </div>
        <div>
          <dt>Pin policy</dt>
          <dd>{pinPolicy}</dd>
        </div>
        <div>
          <dt>Enqueued</dt>
          <dd>{formatWhen(item.created_at)}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>{formatWhen(item.started_at)}</dd>
        </div>
        <div>
          <dt>Finished</dt>
          <dd>{formatWhen(item.finished_at)}</dd>
        </div>
      </dl>

      {batchError ? (
        <div className="still-tag-workbench__batch-error" role="alert">
          {batchError}
        </div>
      ) : null}

      {(running || liveUrl) && (
        <section className="still-tag-workbench__live" aria-label="Live tagging preview">
          <h3 className="still-tag-workbench__section-title">
            {running ? "Tagging now" : "Latest still"}
          </h3>
          <div className={`still-tag-workbench__live-frame${running ? " still-tag-workbench__live-frame--on" : ""}`}>
            {liveUrl ? (
              <AppetitePreviewFrame relpath={liveRel}>
                <img className="still-tag-workbench__live-img" src={liveUrl} alt="Still being tagged" />
              </AppetitePreviewFrame>
            ) : (
              <div className="still-tag-workbench__live-empty factory-muted">Waiting for next still…</div>
            )}
            {running ? <span className="still-tag-workbench__live-badge">Florence on GPU</span> : null}
          </div>
          {liveTags.length ? (
            <div className="still-tag-workbench__live-tags">
              <StillTagTagsPanel
                item={{
                  content_id: String(item.still_tag_output?.current_content_id || stillTagCurrentContentId(item) || ""),
                  status: "done",
                  tags: liveTags,
                  provisional_tags: liveTags,
                  effective_tags: liveTags,
                }}
                layout="effective_only"
              />
            </div>
          ) : null}
        </section>
      )}

      {runId ? (
        <StillTagResultsGallery runId={runId} live={running} className="still-tag-workbench__gallery" />
      ) : (
        <p className="factory-error">Missing still-tag run id — cannot load batch results.</p>
      )}
    </div>
  );
}
