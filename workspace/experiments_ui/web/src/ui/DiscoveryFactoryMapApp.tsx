import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useIsFetching, useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createPortal } from "react-dom";
import {
  fetchShapeFactoryMap,
  fetchShapeFactoryInputCurationEffectiveSources,
  fetchShapeFactoryInputCurationState,
  fetchShapeFactoryInputCurationStills,
  fetchShapeFactoryQuarantine,
  fetchShapeFactoryTemplatePromotions,
  mutateShapeFactoryInputBindings,
  mutateShapeFactoryInputCollection,
  queueShapeFactoryCombo,
  recoverAssets,
  releaseShapeFactoryQuarantine,
  replayShapeFactory,
  setShapeFactoryTemplatePromotion,
} from "./api";
import { SubmitQueueErrorPanel } from "./SubmitAttemptError";
import { AssetInspector, type InspectorAsset } from "./AssetInspector";
import { buildQueueOverrides, FutureRunEditor } from "./factoryMapFutureRunEditor";
import { discoveryLibraryHref } from "./discoveryDeepLink";
import { MediaFullscreenModal, type MediaFullscreenPayload } from "./MediaFullscreenModal";
import { formatIsoDateTime } from "./locale";
import {
  buildSourceOutputPairs,
  countPairPhases,
  primarySourceBinding,
  shortPairLabel,
  summarizePairGaps,
  type SourceOutputPair,
} from "./factoryMapPairs";
import {
  factoryMapFamilyHref,
  factoryMapIndexHref,
  factoryMapPipelineHref,
  familySlugFromShapePath,
  parseFactoryMapRoute,
  pipelineFamilySlugs,
  stepFamilySlug,
  type FactoryMapRoute,
} from "./factoryMapRoute";
import {
  getFamilyActivity,
  getPipelineActivity,
  summarizeFamiliesSection,
  summarizePipelinesSection,
  type FactoryMapActivity,
  type FactoryMapIndexContext,
} from "./factoryMapSummaries";
import { queryKeys } from "./queryKeys";
import type {
  ShapeFactoryMapFamily,
  ShapeFactoryMapJob,
  ShapeFactoryMapMediaRef,
  ShapeFactoryMapPipeline,
  ShapeFactoryMapPipelineStep,
  ShapeFactoryMapResponse,
  ShapeFactoryQuarantineEntry,
  FutureRunDraft,
  InputCurationCollection,
} from "./types";

const POLL_MS = 30_000;

function shortHash(value?: string | null): string {
  return value ? value.slice(0, 12) : "—";
}

function statusClass(status?: string): string {
  const s = (status || "").toLowerCase();
  if (s === "complete") return "sfmap-status sfmap-status--complete";
  if (s === "running") return "sfmap-status sfmap-status--running";
  if (s === "queued") return "sfmap-status sfmap-status--queued";
  if (s === "pending") return "sfmap-status sfmap-status--pending";
  if (s === "future") return "sfmap-status sfmap-status--future";
  if (s === "error") return "sfmap-status sfmap-status--error";
  return "sfmap-status";
}

function formatStatusTimestamp(ms?: number): string {
  if (!ms || !Number.isFinite(ms)) return "—";
  try {
    return new Date(ms).toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "—";
  }
}

function formatPromotionCountdown(expiresAt?: string | null): string {
  const iso = String(expiresAt || "").trim();
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const ms = t - Date.now();
  if (ms <= 0) return "expired";
  const totalMins = Math.round(ms / 60000);
  if (totalMins < 60) return `${totalMins}m left`;
  const h = Math.floor(totalMins / 60);
  const m = totalMins % 60;
  return m > 0 ? `${h}h ${m}m left` : `${h}h left`;
}

function jobCountsForFamily(jobs: ShapeFactoryMapJob[], familySlug: string): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const j of jobs) {
    if (j.family_slug !== familySlug) continue;
    const st = (j.status || "unknown").toLowerCase();
    counts[st] = (counts[st] || 0) + 1;
  }
  return counts;
}

function FactoryMapShell({
  route,
  loading,
  refreshing,
  statusLine,
  onRefresh,
  children,
}: {
  route: FactoryMapRoute;
  loading: boolean;
  refreshing: boolean;
  statusLine: string;
  onRefresh: () => void;
  children: React.ReactNode;
}) {
  const isFamily = route.view === "family";
  const isPipeline = route.view === "pipeline";
  return (
    <div className="discovery-screen">
      <div className="panel discovery-panel discovery-factory-map-root">
        <header className="discovery-factory-map-header">
          <div>
            <h1 className="title" style={{ margin: 0, fontSize: "1.15rem" }}>
              Factory map
            </h1>
            <p className="factory-muted" style={{ margin: "4px 0 0" }}>
              Shape families — pools, jobs, and queue observation
            </p>
            {isFamily ? (
              <nav className="sfmap-breadcrumb" aria-label="Factory map breadcrumb">
                <a href={factoryMapIndexHref()}>All families</a>
                <span aria-hidden="true">/</span>
                <span className="sfmap-breadcrumb__current">{route.familySlug}</span>
              </nav>
            ) : isPipeline ? (
              <nav className="sfmap-breadcrumb" aria-label="Factory map breadcrumb">
                <a href={factoryMapIndexHref()}>All families</a>
                <span aria-hidden="true">/</span>
                <span className="sfmap-breadcrumb__current">{route.pipelineId}</span>
              </nav>
            ) : null}
          </div>
          <button type="button" disabled={loading || refreshing} onClick={onRefresh}>
            {loading ? "Loading…" : refreshing ? "Updating…" : "Refresh"}
          </button>
        </header>
        <div className="sfmap-cache-status" role="status" aria-live="polite">
          {statusLine}
        </div>
        {children}
      </div>
    </div>
  );
}

function FactoryMapFamilyNav({
  families,
  activeSlug,
}: {
  families: ShapeFactoryMapFamily[];
  activeSlug?: string | null;
}) {
  if (!families.length) return null;
  return (
    <nav className="sfmap-family-nav" aria-label="Shape families">
      <a
        href={factoryMapIndexHref()}
        className={`sfmap-family-nav__link${!activeSlug ? " sfmap-family-nav__link--active" : ""}`}
        aria-current={!activeSlug ? "page" : undefined}
      >
        All
      </a>
      {families.map((fam) => {
        const slug = fam.family_slug;
        const active = activeSlug === slug;
        const io = String(fam.shape?.io_class || "").trim();
        const role = String(fam.shape?.chain_role || "").trim();
        return (
          <a
            key={slug}
            href={factoryMapFamilyHref(slug)}
            className={`sfmap-family-nav__link${active ? " sfmap-family-nav__link--active" : ""}`}
            aria-current={active ? "page" : undefined}
            title={[io, role].filter(Boolean).join(" · ") || undefined}
          >
            {slug}
            {io || role ? (
              <span className="sfmap-family-nav__vocab">
                {io || role}
                {io && role ? ` · ${role}` : ""}
              </span>
            ) : null}
          </a>
        );
      })}
    </nav>
  );
}

function FactoryMapAccordionSection({
  sectionId,
  title,
  summaryLine,
  activities,
  defaultOpen,
  hint,
  children,
}: {
  sectionId: string;
  title: string;
  summaryLine: string;
  activities?: FactoryMapActivity[];
  defaultOpen?: boolean;
  hint?: string;
  children: React.ReactNode;
}) {
  const activeItems = (activities || []).filter((a) => a.active);
  return (
    <details className="sfmap-accordion" id={sectionId} open={defaultOpen}>
      <summary className="sfmap-accordion__summary">
        <span className="sfmap-accordion__summary-start">
          <span className="sfmap-accordion__caret" aria-hidden="true">
            ▶
          </span>
          <span className="sfmap-accordion__title">{title}</span>
        </span>
        <span className="sfmap-accordion__summary-line factory-muted">{summaryLine}</span>
        {activeItems.length > 0 ? (
          <span className="sfmap-accordion__summary-badges">
            {activeItems.map((activity, idx) => (
              <span key={idx} className="sfmap-activity-badge sfmap-activity-badge--compact">
                {activity.label}
              </span>
            ))}
          </span>
        ) : null}
      </summary>
      <div className="sfmap-accordion__body">
        {hint ? <p className="sfmap-index-section__hint factory-muted">{hint}</p> : null}
        {children}
      </div>
    </details>
  );
}

function FactoryMapPipelineNav({
  pipelines,
  activeId,
}: {
  pipelines: ShapeFactoryMapPipeline[];
  activeId?: string | null;
}) {
  if (!pipelines.length) return null;
  return (
    <nav className="sfmap-pipeline-nav" aria-label="Pipelines">
      <span className="sfmap-pipeline-nav__label">Pipelines</span>
      {!activeId ? (
        <span className="sfmap-pipeline-nav__link sfmap-pipeline-nav__link--active" aria-current="page">
          All
        </span>
      ) : (
        <a href={factoryMapIndexHref()} className="sfmap-pipeline-nav__link">
          All
        </a>
      )}
      {pipelines.map((pipe) => {
        const id = pipe.pipeline_id || "";
        if (!id) return null;
        const active = activeId === id;
        return (
          <a
            key={id}
            href={factoryMapPipelineHref(id)}
            className={`sfmap-pipeline-nav__link${active ? " sfmap-pipeline-nav__link--active" : ""}`}
            aria-current={active ? "page" : undefined}
          >
            {id}
          </a>
        );
      })}
    </nav>
  );
}

function ArrowColumn({ label }: { label: string }) {
  return (
    <div className="factory-arrow-column" aria-hidden="true">
      <div className="factory-arrow-label">{label}</div>
      <div className="factory-arrow-line">→</div>
    </div>
  );
}

function mediaLooksLikeVideo(media: ShapeFactoryMapMediaRef): boolean {
  const hint = media.relpath || media.path || media.url || media.basename || "";
  return /\.mp4($|\?)/i.test(hint);
}

function mediaHasInspector(media: ShapeFactoryMapMediaRef): boolean {
  return Boolean(media.url || media.thumb_url || media.relpath);
}

function MediaAssetInspector({
  title,
  subtitle,
  media,
  meta,
  onOpenMedia,
  compact,
}: {
  title?: string;
  subtitle?: string;
  media: ShapeFactoryMapMediaRef;
  meta?: string[];
  onOpenMedia: (media: MediaFullscreenPayload) => void;
  compact?: boolean;
}) {
  const displayTitle = title || media.basename || "asset";
  const discoveryRel = media.relpath || media.thumb_relpath;

  return (
    <div className={"sfmap-media-inspector" + (compact ? " sfmap-media-inspector--compact" : "")}>
      {media.thumb_url ? (
        <button
          type="button"
          className={"sfmap-detail-preview" + (compact ? " sfmap-detail-preview--compact" : "")}
          onClick={() =>
            onOpenMedia({
              kind: mediaLooksLikeVideo(media) && media.url ? "video" : "image",
              url: media.url || media.thumb_url!,
              title: media.basename || displayTitle,
            })
          }
        >
          <img src={media.thumb_url} alt="" loading="lazy" />
        </button>
      ) : null}
      <div className="sfmap-detail-kv mono">{displayTitle}</div>
      {subtitle ? <div className="sfmap-detail-meta">{subtitle}</div> : null}
      {meta?.length ? <div className="sfmap-detail-meta">{meta.join(" · ")}</div> : null}
      {media.relpath ? (
        media.url || media.thumb_url ? (
          <a
            className="sfmap-detail-kv mono sfmap-detail-path sfmap-detail-path--link"
            href={
              /^(og|wip|output)\//i.test(media.relpath) || /\.mp4($|\?)/i.test(media.relpath)
                ? `/discovery?relpath=${encodeURIComponent(media.relpath)}`
                : media.url || media.thumb_url || "#"
            }
          >
            {media.relpath}
          </a>
        ) : (
          <div className="sfmap-detail-kv mono sfmap-detail-path">{media.relpath}</div>
        )
      ) : media.path ? (
        <div className="sfmap-detail-kv mono sfmap-detail-path">{media.path}</div>
      ) : null}
      <div className="sfmap-detail-actions">
        {media.url ? (
          <button
            type="button"
            onClick={() =>
              onOpenMedia({
                kind: mediaLooksLikeVideo(media) ? "video" : "image",
                url: media.url!,
                title: media.basename || displayTitle,
              })
            }
          >
            Open video
          </button>
        ) : null}
        {media.thumb_url && media.thumb_url !== media.url ? (
          <button
            type="button"
            onClick={() =>
              onOpenMedia({
                kind: "image",
                url: media.thumb_url!,
                title: media.basename || displayTitle,
              })
            }
          >
            Open thumb
          </button>
        ) : null}
        {discoveryRel ? (
          <a href={discoveryLibraryHref(discoveryRel)} title="Open in discovery library">
            Discovery library
          </a>
        ) : null}
      </div>
    </div>
  );
}

function JobRow({
  job,
  selected,
  onSelect,
}: {
  job: ShapeFactoryMapJob;
  selected?: boolean;
  onSelect: (anchor: HTMLElement) => void;
}) {
  return (
    <button
      type="button"
      className={"sfmap-job-row" + (selected ? " sfmap-job-row--selected" : "")}
      onClick={(e) => onSelect(e.currentTarget)}
    >
      <span className={statusClass(job.status)}>{job.status || "?"}</span>
      <span className="sfmap-job-row__key mono">{job.job_key || "—"}</span>
      {job.exec_sec != null ? (
        <span className="sfmap-job-row__meta">{Math.round(Number(job.exec_sec))}s</span>
      ) : null}
    </button>
  );
}

function DetailPanel({
  familySlug,
  selectedPair,
  selectedJob,
  jobForMember,
  onOpenMedia,
  onQueued,
}: {
  familySlug: string;
  selectedPair: SourceOutputPair | null;
  selectedJob: ShapeFactoryMapJob | null;
  jobForMember: ShapeFactoryMapJob | null;
  onOpenMedia: (media: MediaFullscreenPayload) => void;
  onQueued?: () => void;
}) {
  const job = selectedJob || jobForMember;
  const bindings = selectedPair?.bindings || job?.bindings;
  const source =
    selectedPair?.source || primarySourceBinding(bindings, selectedPair?.source);
  const output = selectedPair?.output;
  const [queueBusy, setQueueBusy] = useState(false);
  const [queueError, setQueueError] = useState<Error | string>("");
  const [queueOk, setQueueOk] = useState("");
  const [runDraft, setRunDraft] = useState<FutureRunDraft | null>(null);
  const [runBaseline, setRunBaseline] = useState<FutureRunDraft | null>(null);
  const [recoverBusy, setRecoverBusy] = useState(false);
  const [recoverMsg, setRecoverMsg] = useState("");
  const queryClient = useQueryClient();
  const queueMutation = useMutation({
    mutationFn: queueShapeFactoryCombo,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.mapRoot });
    },
  });
  const replayMutation = useMutation({
    mutationFn: replayShapeFactory,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.mapRoot });
    },
  });
  const recoverMutation = useMutation({
    mutationFn: recoverAssets,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.mapRoot });
    },
  });

  const missingSourceName = useMemo(() => {
    if (selectedPair?.gap !== "source" || selectedPair?.phase === "future") return "";
    const b = bindings || {};
    const row =
      b.identity_anchor ||
      b.source_still ||
      b.source_video ||
      Object.values(b).find((r) => r?.binding_type === "load_image");
    const raw = row?.basename || row?.path || "";
    return raw ? raw.split(/[\\/]/).pop() || "" : "";
  }, [bindings, selectedPair?.gap, selectedPair?.phase]);

  const handleRecover = useCallback(async () => {
    if (!missingSourceName) return;
    setRecoverBusy(true);
    setRecoverMsg("");
    try {
      const res = await recoverMutation.mutateAsync({ names: [missingSourceName] });
      const r = res.results?.[0];
      if (r?.ok) {
        setRecoverMsg(`Recovered (${r.method})`);
        onQueued?.();
      } else {
        setRecoverMsg(`Failed: ${r?.error || "not_found"}`);
      }
    } catch (e) {
      setRecoverMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setRecoverBusy(false);
    }
  }, [missingSourceName, onQueued, recoverMutation]);

  const canQueue =
    selectedPair?.phase === "future" &&
    Boolean(selectedPair.bindings && Object.keys(selectedPair.bindings).length);

  const replayJobKey = selectedPair?.jobKey || selectedJob?.job_key || jobForMember?.job_key || "";
  const canReplay = Boolean(replayJobKey) && selectedPair?.phase !== "future";
  const hasVideoSource = useMemo(() => {
    const b = bindings || {};
    return Object.entries(b).some(([slot, ref]) => slot.toLowerCase().includes("video") && Boolean(ref?.path));
  }, [bindings]);
  const [replayBusy, setReplayBusy] = useState("");
  const [replayMsg, setReplayMsg] = useState("");

  const handleReplay = useCallback(
    async (extend: boolean) => {
      if (!replayJobKey) return;
      setReplayBusy(extend ? "extend" : "replay");
      setReplayMsg("");
      try {
        const res = await replayMutation.mutateAsync({ job_key: replayJobKey, extend });
        setReplayMsg(res.prompt_id ? `Queued · prompt ${res.prompt_id}` : res.job_key ? `Job ${res.job_key}` : "Queued");
        onQueued?.();
      } catch (e) {
        setReplayMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setReplayBusy("");
      }
    },
    [replayJobKey, onQueued, replayMutation],
  );

  useEffect(() => {
    setQueueError("");
    setQueueOk("");
    setRunDraft(null);
    setRunBaseline(null);
    setRecoverMsg("");
    setReplayMsg("");
  }, [selectedPair?.pairKey]);

  const handleDraftChange = useCallback((draft: FutureRunDraft, baseline: FutureRunDraft) => {
    setRunDraft(draft);
    setRunBaseline(baseline);
  }, []);

  const handleQueue = useCallback(async () => {
    if (!canQueue || !selectedPair?.bindings) return;
    setQueueBusy(true);
    setQueueError("");
    setQueueOk("");
    try {
      const bindingPaths: Record<string, string> = {};
      for (const [slot, b] of Object.entries(selectedPair.bindings)) {
        if (b.path) bindingPaths[slot] = b.path;
      }
      const overrides =
        runDraft && runBaseline ? buildQueueOverrides(runDraft, runBaseline) : undefined;
      const res = await queueMutation.mutateAsync({
        family_slug: familySlug,
        combo_key: selectedPair.comboKey,
        bindings: bindingPaths,
        overrides,
        source_surface: "factory-map",
      });
      setQueueOk(
        res.prompt_id
          ? `Queued · prompt ${res.prompt_id}`
          : res.job_key
            ? `Job ${res.job_key} created`
            : "Queued",
      );
      onQueued?.();
    } catch (e) {
      setQueueError(e instanceof Error ? e : String(e));
    } finally {
      setQueueBusy(false);
    }
  }, [canQueue, familySlug, onQueued, queueMutation, runBaseline, runDraft, selectedPair?.bindings, selectedPair?.comboKey]);

  if (!job && !selectedPair) {
    return (
      <div className="sfmap-detail sfmap-detail--empty">
        Select a run to inspect source → output mapping and job details.
      </div>
    );
  }

  return (
    <div className="sfmap-detail">
      {selectedPair || source || output ? (
        <section className="sfmap-detail-section">
          <h3>Source → Output</h3>
          {selectedPair?.gap !== "none" && selectedPair?.gapNote ? (
            <div className="sfmap-detail-meta sfmap-detail-gap-note">
              {selectedPair.phase === "future"
                ? "Possible run — no job submitted yet"
                : selectedPair.gap === "output"
                  ? `Output not deposited yet (${selectedPair.gapNote})`
                  : `Source missing (${selectedPair.gapNote})`}
            </div>
          ) : null}
          {missingSourceName ? (
            <div className="sfmap-recover-row">
              <button
                type="button"
                className="sfmap-recover-btn"
                onClick={handleRecover}
                disabled={recoverBusy}
                title={`Locate/recover ${missingSourceName} into input/`}
              >
                {recoverBusy ? "Recovering…" : "Recover source"}
              </button>
              <code className="factory-muted sfmap-recover-name">{missingSourceName}</code>
              {recoverMsg ? <span className="factory-muted">{recoverMsg}</span> : null}
            </div>
          ) : null}
          <div className="sfmap-pair-detail">
            {source && mediaHasInspector(source) ? (
              <MediaAssetInspector title="Source" media={source} onOpenMedia={onOpenMedia} compact />
            ) : (
              <div className="sfmap-pair-detail__missing">No source</div>
            )}
            <span className="sfmap-pair-detail__arrow" aria-hidden="true">
              →
            </span>
            {output && mediaHasInspector(output) ? (
              <MediaAssetInspector title="Output" media={output} onOpenMedia={onOpenMedia} compact />
            ) : (
              <div className="sfmap-pair-detail__missing">
                {selectedPair?.phase === "future"
                  ? "Future"
                  : selectedPair?.gap === "output"
                    ? selectedPair.gapNote || "Pending"
                    : "No output"}
              </div>
            )}
          </div>
        </section>
      ) : null}

      {output?.relpath && selectedPair?.phase !== "future" ? (
        <section className="sfmap-detail-section">
          <h3>Signals</h3>
          <AssetInspector
            asset={
              {
                relpath: output.relpath,
                library: "og",
                name: output.basename || output.relpath,
                url: output.url,
                thumb_url: output.thumb_url,
              } as InspectorAsset
            }
            showMedia={false}
          />
        </section>
      ) : null}

      {canReplay ? (
        <section className="sfmap-detail-section sfmap-replay-section">
          <h3>Run again</h3>
          <div className="sfmap-replay-row">
            <button
              type="button"
              className="drt-btn"
              disabled={Boolean(replayBusy)}
              onClick={() => void handleReplay(false)}
              title="Re-run this combo with the same bindings"
            >
              {replayBusy === "replay" ? "Queuing…" : "Replay"}
            </button>
            {hasVideoSource ? (
              <button
                type="button"
                className="drt-btn"
                disabled={Boolean(replayBusy)}
                onClick={() => void handleReplay(true)}
                title="Chain this run's output as the next video source"
              >
                {replayBusy === "extend" ? "Queuing…" : "Extend →"}
              </button>
            ) : null}
            {replayMsg ? (
              <span className="factory-muted sfmap-replay-msg">
                {replayMsg}
                {replayMsg.startsWith("Queued") ? (
                  <>
                    {" · "}
                    <a href="/comfy-queue">queue monitor</a>
                  </>
                ) : null}
              </span>
            ) : null}
          </div>
        </section>
      ) : null}

      {job ? (
        <>
          <section className="sfmap-detail-section">
            <h3>Job</h3>
            <div className="sfmap-detail-kv">
              {selectedPair?.jobKind || job.job_kind ? (
                <span className={`sfmap-pair-kind sfmap-pair-kind--${String(selectedPair?.jobKind || job.job_kind).replace(/[^a-z0-9_-]/gi, "")}`}>
                  {selectedPair?.jobKind || job.job_kind}
                </span>
              ) : null}
              {" "}
              <span className={statusClass(job.status)}>{job.status}</span>
              {job.family_slug ? <> · {job.family_slug}</> : null}
              {job.exec_sec != null ? <> · {Math.round(Number(job.exec_sec))}s</> : null}
            </div>
            <div className="sfmap-detail-kv mono">{job.job_key}</div>
            {job.deposit_to ? (
              <div className="sfmap-detail-kv">
                deposit <span className="mono">{job.deposit_to}</span>
              </div>
            ) : null}
            {job.prompt_id ? <div className="sfmap-detail-kv mono">prompt {job.prompt_id}</div> : null}
            {job.graph_hash ? (
              <div className="sfmap-detail-kv">
                graph <span className="mono">{shortHash(job.graph_hash)}</span>
              </div>
            ) : null}
          </section>

          {job.bindings && Object.keys(job.bindings).length ? (
            <section className="sfmap-detail-section">
              <h3>Bindings</h3>
              <ul className="sfmap-binding-list">
                {Object.entries(job.bindings)
                  .filter(
                    ([slot]) =>
                      !(
                        (slot === "source_video" ||
                          slot === "source_still" ||
                          slot === "identity_anchor" ||
                          slot === "source_video_ref") &&
                        source &&
                        mediaHasInspector(source)
                      ),
                  )
                  .map(([slot, b]) => (
                  <li key={slot}>
                    {mediaHasInspector(b) ? (
                      <MediaAssetInspector
                        title={slot}
                        subtitle={[b.role, b.binding_type].filter(Boolean).join(" · ") || undefined}
                        media={b}
                        onOpenMedia={onOpenMedia}
                        compact
                      />
                    ) : (
                      <>
                        <strong>{slot}</strong>
                        {b.role ? <> · {b.role}</> : null}
                        <div className="mono sfmap-detail-path">{b.basename || b.path}</div>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {job.generated_workflow_path ? (
            <section className="sfmap-detail-section">
              <h3>Workflow</h3>
              <div className="sfmap-detail-kv mono sfmap-detail-path">{job.generated_workflow_path}</div>
            </section>
          ) : null}

          {job.outputs?.length ? (
            <section className="sfmap-detail-section">
              <h3>Outputs</h3>
              <ul className="sfmap-binding-list">
                {job.outputs.map((o, i) => (
                  <li key={`${o.path || i}`}>
                    {mediaHasInspector(o) ? (
                      <MediaAssetInspector
                        title={o.basename || o.relpath || "output"}
                        media={o}
                        onOpenMedia={onOpenMedia}
                        compact
                      />
                    ) : (
                      <span className="mono">{o.basename || o.path}</span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : selectedPair?.phase === "future" && bindings && Object.keys(bindings).length ? (
        <section className="sfmap-detail-section">
          <h3>Bindings</h3>
          <ul className="sfmap-binding-list">
            {Object.entries(bindings)
              .filter(
                ([slot]) =>
                  !(
                    (slot === "source_video" ||
                      slot === "source_still" ||
                      slot === "identity_anchor" ||
                      slot === "source_video_ref") &&
                    source &&
                    mediaHasInspector(source)
                  ),
              )
              .map(([slot, b]) => (
                <li key={slot}>
                  {mediaHasInspector(b) ? (
                    <MediaAssetInspector
                      title={slot}
                      media={b}
                      onOpenMedia={onOpenMedia}
                      compact
                    />
                  ) : (
                    <>
                      <strong>{slot}</strong>
                      <div className="mono sfmap-detail-path">{b.basename || b.path}</div>
                    </>
                  )}
                </li>
              ))}
          </ul>
        </section>
      ) : null}

      {canQueue ? (
        <FutureRunEditor
          promptProfilePath={bindings?.prompt_profile?.path}
          onDraftChange={handleDraftChange}
        />
      ) : null}

      {canQueue ? (
        <section className="sfmap-detail-section sfmap-detail-queue">
          <button type="button" className="sfmap-queue-run-btn" disabled={queueBusy} onClick={() => void handleQueue()}>
            {queueBusy ? "Queueing…" : "Queue run"}
          </button>
          {queueOk ? <div className="sfmap-detail-meta sfmap-queue-ok">{queueOk}</div> : null}
          {queueError ? <SubmitQueueErrorPanel error={queueError} className="sfmap-queue-error-panel" /> : null}
        </section>
      ) : null}
    </div>
  );
}

const INSPECTOR_TOOLTIP_WIDTH = 300;
const INSPECTOR_TOOLTIP_GAP = 10;
const INSPECTOR_SCROLL_PAD_MARGIN = 48;
const FACTORY_MAP_SCROLL_SELECTOR = ".discovery-factory-map-scroll";

function factoryMapScrollRoot(): HTMLElement | null {
  const el = document.querySelector(FACTORY_MAP_SCROLL_SELECTOR);
  return el instanceof HTMLElement ? el : null;
}

function bindInspectorScrollSync(onMove: () => void): () => void {
  const opts: AddEventListenerOptions = { passive: true, capture: true };
  window.addEventListener("resize", onMove);
  const scrollRoot = factoryMapScrollRoot();
  scrollRoot?.addEventListener("scroll", onMove, opts);
  document.querySelectorAll(".sfmap-job-list").forEach((el) => {
    el.addEventListener("scroll", onMove, opts);
  });
  return () => {
    window.removeEventListener("resize", onMove);
    scrollRoot?.removeEventListener("scroll", onMove, opts);
    document.querySelectorAll(".sfmap-job-list").forEach((el) => {
      el.removeEventListener("scroll", onMove, opts);
    });
  };
}

function inspectorOverlayPosition(anchor: HTMLElement): {
  top: number;
  left: number;
  arrowLeft: number;
} {
  const rect = anchor.getBoundingClientRect();
  const margin = 8;
  const top = rect.bottom + INSPECTOR_TOOLTIP_GAP;
  let left = rect.left + rect.width / 2 - INSPECTOR_TOOLTIP_WIDTH / 2;
  left = Math.min(Math.max(margin, left), window.innerWidth - INSPECTOR_TOOLTIP_WIDTH - margin);
  const anchorCenter = rect.left + rect.width / 2;
  const arrowLeft = Math.min(Math.max(14, anchorCenter - left), INSPECTOR_TOOLTIP_WIDTH - 14);
  return { top, left, arrowLeft };
}

/** How much extra scroll range is needed so a fixed southern tooltip fits in the viewport. */
function inspectorScrollPadPx(tooltipEl: HTMLElement, scrollRoot: HTMLElement): number {
  const viewportBottom = window.innerHeight;
  const tooltipBottom = tooltipEl.getBoundingClientRect().bottom;
  const shortfall = tooltipBottom - viewportBottom + INSPECTOR_SCROLL_PAD_MARGIN;
  if (shortfall <= 0) return 0;

  const scrollRoom = scrollRoot.scrollHeight - scrollRoot.scrollTop - scrollRoot.clientHeight;
  const extra = Math.max(0, shortfall - scrollRoom);
  return Math.ceil(shortfall + extra);
}

/** Floating tooltip below anchor (southern) — overlays content, does not shift layout. */
function InspectorTooltipOverlay({
  open,
  anchorEl,
  onClose,
  onScrollPad,
  children,
}: {
  open: boolean;
  anchorEl: HTMLElement | null;
  onClose: () => void;
  onScrollPad: (neededPx: number) => void;
  children: React.ReactNode;
}) {
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState(() =>
    anchorEl ? inspectorOverlayPosition(anchorEl) : { top: 0, left: 0, arrowLeft: 24 },
  );

  const syncPosition = useCallback(() => {
    if (anchorEl) setPos(inspectorOverlayPosition(anchorEl));
  }, [anchorEl]);

  const reportScrollPad = useCallback(() => {
    const tooltip = tooltipRef.current;
    const scrollRoot = factoryMapScrollRoot();
    if (!tooltip || !scrollRoot) return;
    const needed = inspectorScrollPadPx(tooltip, scrollRoot);
    if (needed > 0) onScrollPad(needed);
  }, [onScrollPad]);

  useLayoutEffect(() => {
    if (!open || !anchorEl) return;
    syncPosition();
  }, [open, anchorEl, syncPosition]);

  useLayoutEffect(() => {
    if (!open || !anchorEl) return;
    reportScrollPad();
  }, [open, anchorEl, pos, reportScrollPad]);

  useEffect(() => {
    if (!open || !anchorEl) return;
    const onMove = () => {
      requestAnimationFrame(() => {
        syncPosition();
      });
    };
    return bindInspectorScrollSync(onMove);
  }, [open, anchorEl, syncPosition]);

  useEffect(() => {
    if (!open || !tooltipRef.current) return;
    const tooltip = tooltipRef.current;
    const ro = new ResizeObserver(() => reportScrollPad());
    ro.observe(tooltip);
    return () => ro.disconnect();
  }, [open, reportScrollPad]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (anchorEl?.contains(target)) return;
      if (tooltipRef.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, anchorEl, onClose]);

  if (!open || !anchorEl) return null;

  return createPortal(
    <div
      ref={tooltipRef}
      className="sfmap-inspector-tooltip sfmap-inspector-tooltip--south"
      role="dialog"
      aria-label="Inspector"
      aria-modal="false"
      style={{
        top: pos.top,
        left: pos.left,
        width: INSPECTOR_TOOLTIP_WIDTH,
        ["--sfmap-tooltip-arrow-left" as string]: `${pos.arrowLeft}px`,
      }}
    >
      <div className="sfmap-inspector-tooltip__body">{children}</div>
    </div>,
    document.body,
  );
}

function FamilyGraph({ family }: { family: ShapeFactoryMapFamily }) {
  const shape = family.shape || {};
  const deposit = (family.deposit_pools || [])[0];
  const io = String(shape.io_class || "").trim();
  const role = String(shape.chain_role || "").trim();
  return (
    <div className="factory-plan sfmap-family-plan">
      <div className="factory-plan-header">
        <div>
          <h2>{family.family_slug}</h2>
          <div className="factory-muted">
            {shape.shape_id || "shape"} · graph {shortHash(shape.graph_hash)}
          </div>
          {io || role ? (
            <div className="sfmap-vocab-badges" aria-label="Station vocabulary">
              {io ? <span className="factory-pill sfmap-vocab-pill">{io}</span> : null}
              {role ? <span className="factory-pill sfmap-vocab-pill">{role}</span> : null}
            </div>
          ) : null}
        </div>
        {deposit?.member_count != null ? (
          <div className="factory-pill">
            {deposit.member_count} in {deposit.pool_id}
          </div>
        ) : null}
      </div>

      <div className="factory-graph">
        <section className="factory-column">
          <h3>Input pools</h3>
          <div className="factory-card-list">
            {(family.input_pools || []).map((pool) => (
              <div key={`${pool.name}-${pool.slot}`} className="factory-card sfmap-input-pool-card">
                <div className="factory-card-title">{pool.name || pool.slot}</div>
                <div className="factory-card-meta">{pool.description}</div>
                {pool.feeds_from?.length ? (
                  <div className="factory-card-meta">
                    from {pool.feeds_from.map((f) => f.pool_id).filter(Boolean).join(", ")}
                  </div>
                ) : (
                  <div className="factory-card-meta">{pool.member_glob_count || 0} glob source(s)</div>
                )}
              </div>
            ))}
            {!family.input_pools?.length ? <div className="factory-empty">No input pools</div> : null}
          </div>
        </section>

        <ArrowColumn label="binds" />

        <section className="factory-column">
          <h3>Shape</h3>
          <div className="factory-card">
            <div className="factory-card-title">{shape.shape_id || family.family_slug}</div>
            <div className="factory-card-meta mono">hash {shortHash(shape.graph_hash)}</div>
            {io || role ? (
              <div className="sfmap-vocab-badges">
                {io ? <span className="factory-pill sfmap-vocab-pill">{io}</span> : null}
                {role ? <span className="factory-pill sfmap-vocab-pill">{role}</span> : null}
              </div>
            ) : null}
            <ul className="sfmap-slot-list">
              {(shape.requires || []).map((r) => (
                <li key={r.slot}>
                  {r.slot} <span className="sfmap-slot-role">{r.role}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <ArrowColumn label="deposit" />

        <section className="factory-column">
          <h3>{deposit?.pool_id || "Deposit pool"}</h3>
          <div className="factory-card-meta">{deposit?.description}</div>
        </section>
      </div>
    </div>
  );
}

function PipelineStrip({ pipeline }: { pipeline: ShapeFactoryMapPipeline }) {
  const steps = pipeline.steps || [];
  const guidance = String(pipeline.input_guidance || "").trim();
  return (
    <div className="sfmap-pipeline-strip">
      <div className="sfmap-pipeline-title">
        <strong>{pipeline.pipeline_id}</strong>
        <span className="factory-muted">{pipeline.description}</span>
        {guidance ? <span className="factory-pill sfmap-vocab-pill">{guidance}</span> : null}
      </div>
      <div className="sfmap-pipeline-steps">
        {steps.map((step, idx) => (
          <React.Fragment key={step.id || idx}>
            {idx > 0 ? <span className="sfmap-pipeline-arrow">→</span> : null}
            <div className="sfmap-pipeline-step">
              <div className="sfmap-pipeline-step__id">{step.id}</div>
              {step.binds_from_pool ? (
                <div className="sfmap-pipeline-step__bind">
                  ← {step.binds_from_pool}
                  {step.binds_pick ? ` (${step.binds_pick})` : ""}
                </div>
              ) : null}
              {step.deposits_to ? <div className="sfmap-pipeline-step__dep">→ {step.deposits_to}</div> : null}
            </div>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

function PipelineIndexCard({
  pipeline,
  jobCountsByFamily,
  activity,
}: {
  pipeline: ShapeFactoryMapPipeline;
  jobCountsByFamily: Record<string, Record<string, number>>;
  activity?: FactoryMapActivity | null;
}) {
  const id = pipeline.pipeline_id || "pipeline";
  const families = pipelineFamilySlugs(pipeline);
  const totalJobs = families.reduce((sum, slug) => {
    const counts = jobCountsByFamily[slug] || {};
    return sum + Object.values(counts).reduce((a, b) => a + b, 0);
  }, 0);

  return (
    <a
      href={factoryMapPipelineHref(id)}
      className={"sfmap-pipeline-index-card" + (activity?.active ? " sfmap-pipeline-index-card--active" : "")}
    >
      <div className="sfmap-pipeline-index-card__head">
        <h2 className="sfmap-pipeline-index-card__title">{id}</h2>
        <span className="sfmap-pipeline-index-card__head-end">
          {activity?.active ? (
            <span className="sfmap-activity-badge sfmap-activity-badge--compact" title={activity.detail}>
              {activity.label}
            </span>
          ) : null}
          <span className="sfmap-pipeline-index-card__cta">Open →</span>
        </span>
      </div>
      {pipeline.description ? (
        <p className="sfmap-pipeline-index-card__desc factory-muted">{pipeline.description.trim()}</p>
      ) : null}
      <div className="sfmap-pipeline-index-card__meta">
        <span>{(pipeline.steps || []).length} steps</span>
        {families.length ? (
          <span>
            families{" "}
            {families.map((slug, i) => (
              <React.Fragment key={slug}>
                {i > 0 ? ", " : null}
                <span className="mono">{slug}</span>
              </React.Fragment>
            ))}
          </span>
        ) : null}
        {totalJobs > 0 ? <span>{totalJobs} jobs across steps</span> : null}
      </div>
      <PipelineStrip pipeline={pipeline} />
    </a>
  );
}

function PipelineStepDetail({
  step,
  stepNumber,
  familiesBySlug,
}: {
  step: ShapeFactoryMapPipelineStep;
  stepNumber: number;
  familiesBySlug: Map<string, ShapeFactoryMapFamily>;
}) {
  const slug = stepFamilySlug(step);
  const family = slug ? familiesBySlug.get(slug) : undefined;
  const shape = family?.shape;

  return (
    <article className="sfmap-pipeline-step-detail">
      <div className="sfmap-pipeline-step-detail__head">
        <span className="sfmap-pipeline-step-detail__num">Step {stepNumber}</span>
        <h3 className="sfmap-pipeline-step-detail__id">{step.id || `step-${stepNumber}`}</h3>
        {slug ? (
          <a href={factoryMapFamilyHref(slug)} className="sfmap-pipeline-step-detail__family">
            {slug} →
          </a>
        ) : null}
      </div>

      <div className="sfmap-pipeline-step-detail__flow">
        {step.binds_from_pool ? (
          <div className="sfmap-pipeline-step-detail__bind">
            <strong>Bind override</strong>
            <span>
              source from <span className="mono">{step.binds_from_pool}</span>
              {step.binds_pick ? ` · pick ${step.binds_pick}` : null}
            </span>
          </div>
        ) : (
          <div className="sfmap-pipeline-step-detail__bind factory-muted">Inputs from family pools</div>
        )}

        <div className="sfmap-pipeline-step-detail__shape">
          <strong>Shape</strong>
          {shape ? (
            <span>
              {shape.shape_id || slug}
              {shape.io_class || shape.chain_role ? (
                <>
                  {" "}
                  · {[shape.io_class, shape.chain_role].filter(Boolean).join(" · ")}
                </>
              ) : null}{" "}
              · graph <span className="mono">{shortHash(shape.graph_hash)}</span>
            </span>
          ) : step.shape ? (
            <span className="mono sfmap-detail-path">{step.shape}</span>
          ) : (
            <span className="factory-muted">—</span>
          )}
        </div>

        <div className="sfmap-pipeline-step-detail__pick">
          <strong>Pick</strong>
          <span className="mono">
            {step.pick || "zip"}
            {step.pick_index != null ? ` · index ${step.pick_index}` : null}
          </span>
        </div>

        {step.deposits_to ? (
          <div className="sfmap-pipeline-step-detail__dep">
            <strong>Deposit</strong>
            <span>
              final_video → <span className="mono">{step.deposits_to}</span>
            </span>
          </div>
        ) : null}
      </div>

      {step.pools ? (
        <div className="sfmap-pipeline-step-detail__paths">
          <div className="sfmap-detail-kv mono sfmap-detail-path">{step.pools}</div>
        </div>
      ) : null}
    </article>
  );
}

function FactoryMapPipelineView({
  data,
  pipeline,
  pipelines,
  families,
}: {
  data: ShapeFactoryMapResponse;
  pipeline: ShapeFactoryMapPipeline;
  pipelines: ShapeFactoryMapPipeline[];
  families: ShapeFactoryMapFamily[];
}) {
  const familiesBySlug = useMemo(() => {
    const m = new Map<string, ShapeFactoryMapFamily>();
    for (const fam of families) {
      if (fam.family_slug) m.set(fam.family_slug, fam);
    }
    return m;
  }, [families]);

  const involvedSlugs = pipelineFamilySlugs(pipeline);
  const pipelineJobs = useMemo(() => {
    const slugSet = new Set(involvedSlugs);
    return (data.jobs?.items || []).filter((j) => j.family_slug && slugSet.has(j.family_slug));
  }, [data, involvedSlugs]);

  const jobSummary = useMemo(() => {
    const summary: Record<string, number> = {};
    for (const j of pipelineJobs) {
      const st = (j.status || "unknown").toLowerCase();
      summary[st] = (summary[st] || 0) + 1;
    }
    return summary;
  }, [pipelineJobs]);

  const pipelineId = pipeline.pipeline_id || "pipeline";

  return (
    <>
      <FactoryMapFamilyNav families={families} />
      <FactoryMapPipelineNav pipelines={pipelines} activeId={pipelineId} />

      <div className="sfmap-pipeline-detail">
        <header className="sfmap-pipeline-detail__header">
          <h2 className="sfmap-pipeline-detail__title">{pipelineId}</h2>
          {pipeline.description ? (
            <p className="sfmap-pipeline-detail__desc">{pipeline.description.trim()}</p>
          ) : null}
          {pipeline.path ? <div className="sfmap-detail-kv mono sfmap-detail-path">{pipeline.path}</div> : null}
        </header>

        <div className="sfmap-pipeline-detail__stats">
          <span>{(pipeline.steps || []).length} steps</span>
          {involvedSlugs.map((slug) => (
            <a key={slug} href={factoryMapFamilyHref(slug)} className="sfmap-pipeline-detail__family-link">
              {slug}
            </a>
          ))}
          {pipelineJobs.length ? (
            <span>
              {pipelineJobs.length} jobs
              {jobSummary.complete ? ` · ${jobSummary.complete} complete` : ""}
            </span>
          ) : null}
        </div>

        <section className="sfmap-pipeline-detail__steps">
          <h3 className="sfmap-index-section__title">Steps</h3>
          <div className="sfmap-pipeline-step-list">
            {(pipeline.steps || []).map((step, idx) => (
              <React.Fragment key={step.id || idx}>
                {idx > 0 ? <div className="sfmap-pipeline-step-list__arrow" aria-hidden="true">↓</div> : null}
                <PipelineStepDetail step={step} stepNumber={idx + 1} familiesBySlug={familiesBySlug} />
              </React.Fragment>
            ))}
          </div>
        </section>

        <section className="sfmap-pipeline-detail__run">
          <h3 className="sfmap-index-section__title">Run</h3>
          <pre className="sfmap-pipeline-detail__cmd mono">
            {`python3 shape_factory.py pipeline run --pipeline ${pipeline.path || `.data/pipelines/${pipelineId}.pipeline.yaml`}`}
          </pre>
          <p className="factory-muted sfmap-index-section__hint">
            Runs each step in order: generate → submit → wait → deposit. Step 2+ uses binds_override to pull from
            earlier deposit pools.
          </p>
        </section>

        {pipelineJobs.length > 0 ? (
          <section className="sfmap-pipeline-detail__jobs">
            <h3 className="sfmap-index-section__title">Recent jobs on this pipeline</h3>
            <div className="sfmap-job-list">
              {pipelineJobs.slice(0, 20).map((job) => (
                <div key={job.job_key} className="sfmap-pipeline-job-row">
                  <span className={statusClass(job.status)}>{job.status || "?"}</span>
                  <a href={factoryMapFamilyHref(job.family_slug || "")} className="sfmap-pipeline-job-row__family">
                    {job.family_slug}
                  </a>
                  <span className="sfmap-job-row__key mono">{job.job_key || "—"}</span>
                </div>
              ))}
            </div>
          </section>
        ) : null}
      </div>
    </>
  );
}

function FamilyIndexCard({
  family,
  jobCounts,
  activity,
  promotion,
  busy,
  onQuickTemporary,
  onQuickToday,
  onQuickLongTerm,
  onOpenPromotionEditor,
}: {
  family: ShapeFactoryMapFamily;
  jobCounts: Record<string, number>;
  activity?: FactoryMapActivity | null;
  promotion?: { scope?: string; expires_at?: string | null; intents?: string[]; note?: string | null } | null;
  busy?: boolean;
  onQuickTemporary?: (familySlug: string) => void;
  onQuickToday?: (familySlug: string) => void;
  onQuickLongTerm?: (familySlug: string) => void;
  onOpenPromotionEditor?: (familySlug: string) => void;
}) {
  const shape = family.shape || {};
  const deposit = (family.deposit_pools || [])[0];
  const totalJobs = Object.values(jobCounts).reduce((a, b) => a + b, 0);
  const inflight = (jobCounts.running || 0) + (jobCounts.queued || 0);

  const promoScope = String(promotion?.scope || "").trim();
  const promoBadge = promoScope === "temporary" ? "TEMP" : promoScope === "long_term" ? "DEFAULT" : "";
  const promoHint =
    promoScope === "temporary" && promotion?.expires_at
      ? `temporary until ${formatIsoDateTime(promotion.expires_at)}`
      : promoScope === "long_term"
        ? "long-term default"
        : "";

  return (
    <div className="sfmap-family-index-card-wrap">
      <a
        href={factoryMapFamilyHref(family.family_slug)}
        className={"sfmap-family-index-card" + (activity?.active ? " sfmap-family-index-card--active" : "")}
      >
        <div className="sfmap-family-index-card__head">
          <h2 className="sfmap-family-index-card__title">{family.family_slug}</h2>
          <span className="sfmap-family-index-card__head-end">
            {promoBadge ? (
              <span className={`sfmap-promo-badge sfmap-promo-badge--${promoScope || "none"}`} title={promoHint}>
                {promoBadge}
              </span>
            ) : null}
            {activity?.active ? (
              <span className="sfmap-activity-badge sfmap-activity-badge--compact" title={activity.detail}>
                {activity.label}
              </span>
            ) : null}
            <span className="sfmap-family-index-card__cta">Open →</span>
          </span>
        </div>
        <div className="sfmap-family-index-card__meta">
          <span>{shape.shape_id || "shape"}</span>
          <span className="mono">graph {shortHash(shape.graph_hash)}</span>
        </div>
        {deposit ? (
          <div className="sfmap-family-index-card__pool">
            Deposits to <strong>{deposit.pool_id}</strong>
            {deposit.member_count != null ? ` · ${deposit.member_count} members` : null}
          </div>
        ) : null}
        {(family.input_pools || []).some((p) => p.feeds_from?.length) ? (
          <div className="sfmap-family-index-card__pool factory-muted">
            Pulls from{" "}
            {(family.input_pools || [])
              .flatMap((p) => (p.feeds_from || []).map((f) => f.pool_id))
              .filter(Boolean)
              .join(", ")}
          </div>
        ) : null}
        <div className="sfmap-family-index-card__stats">
          <span>{totalJobs} jobs</span>
          {inflight > 0 ? <span>{inflight} in flight</span> : null}
          {(jobCounts.pending || 0) > 0 ? <span>{jobCounts.pending} pending submit</span> : null}
        </div>
      </a>
      <div className="sfmap-promo-actions" role="group" aria-label={`Template promotion controls for ${family.family_slug}`}>
        <button type="button" className="btn" disabled={busy} title="Temporary promotion for 2 hours" onClick={() => onQuickTemporary?.(family.family_slug)}>
          2h
        </button>
        <button type="button" className="btn" disabled={busy} title="Temporary promotion until local midnight" onClick={() => onQuickToday?.(family.family_slug)}>
          Today
        </button>
        <button type="button" className="btn" disabled={busy} title="Set long-term default promotion" onClick={() => onQuickLongTerm?.(family.family_slug)}>
          Default
        </button>
        <button type="button" className="btn" disabled={busy} onClick={() => onOpenPromotionEditor?.(family.family_slug)}>
          Promote…
        </button>
      </div>
    </div>
  );
}

function QuarantineWorkflowsPanel() {
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const quarantineQuery = useQuery({
    queryKey: queryKeys.shapeFactory.quarantine({ status: "quarantined" }),
    queryFn: () => fetchShapeFactoryQuarantine({ status: "quarantined" }),
    staleTime: 60_000,
  });
  const releaseMutation = useMutation({
    mutationFn: releaseShapeFactoryQuarantine,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.quarantineRoot });
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.mapRoot });
    },
  });
  const entries = quarantineQuery.data?.entries || [];
  const loading = quarantineQuery.isLoading;
  const error = quarantineQuery.error instanceof Error ? quarantineQuery.error.message : null;

  const onRelease = async (entry: ShapeFactoryQuarantineEntry) => {
    const key = entry.workflow_path || entry.workflow_name || "";
    if (!key) return;
    setBusyKey(key);
    setMsg(null);
    try {
      await releaseMutation.mutateAsync({
        workflow_path: entry.workflow_path,
        workflow_name: entry.workflow_name,
        note: (notes[key] || "").trim() || "released from factory map",
      });
      setMsg(`Released ${entry.workflow_name || key}`);
      setNotes((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyKey(null);
    }
  };

  const summaryLine = loading
    ? "loading…"
    : error
      ? "failed to load"
      : entries.length
        ? `${entries.length} blocked template${entries.length === 1 ? "" : "s"}`
        : "none blocked";

  return (
    <FactoryMapAccordionSection
      sectionId="sfmap-quarantine"
      title="Quarantined workflows"
      summaryLine={summaryLine}
      activities={entries.length ? [{ active: true, label: `${entries.length} quarantined` }] : undefined}
      defaultOpen={entries.length > 0}
      hint="Templates blocked from factory generate/submit until a human releases them. Soft convert failures alone will not undo a release."
    >
      {error ? <p className="factory-error">{error}</p> : null}
      {msg ? <p className="factory-muted sfmap-quarantine-msg">{msg}</p> : null}
      {loading ? <p className="factory-muted">Loading quarantine registry…</p> : null}
      {!loading && !error && entries.length === 0 ? (
        <p className="factory-muted">No quarantined workflows.</p>
      ) : null}
      {!loading && entries.length > 0 ? (
        <ul className="sfmap-quarantine-list">
          {entries.map((entry) => {
            const key = entry.workflow_path || entry.workflow_name || "";
            const reasons = (entry.reasons || []).join(", ") || "—";
            const busy = busyKey === key;
            return (
              <li key={key || entry.workflow_name} className="sfmap-quarantine-row">
                <div className="sfmap-quarantine-row__meta">
                  <strong className="sfmap-quarantine-row__name">{entry.workflow_name || "workflow"}</strong>
                  <span className="factory-muted">
                    {entry.category || "unknown"} · {reasons}
                    {entry.repair_outcome ? ` · repair=${entry.repair_outcome}` : ""}
                    {entry.validated_at ? ` · ${formatIsoDateTime(entry.validated_at)}` : ""}
                  </span>
                </div>
                <div className="sfmap-quarantine-row__actions">
                  <input
                    type="text"
                    className="sfmap-quarantine-note"
                    placeholder="Release note"
                    value={notes[key] || ""}
                    disabled={busy}
                    onChange={(ev) => {
                      setNotes((prev) => ({ ...prev, [key]: ev.target.value }));
                    }}
                  />
                  <button type="button" className="btn" disabled={busy || !key} onClick={() => void onRelease(entry)}>
                    {busy ? "Releasing…" : "Release"}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </FactoryMapAccordionSection>
  );
}

function PromotionScoreboard({
  entries,
}: {
  entries: Array<{
    family_slug: string;
    intent: string;
    scope: string;
    expires_at?: string | null;
    note?: string | null;
  }>;
}) {
  const rows = entries.slice().sort((a, b) => {
    const sa = a.scope === "temporary" ? 0 : 1;
    const sb = b.scope === "temporary" ? 0 : 1;
    if (sa !== sb) return sa - sb;
    return a.family_slug.localeCompare(b.family_slug);
  });
  const summaryLine = rows.length
    ? `${rows.length} active promotion${rows.length === 1 ? "" : "s"}`
    : "no active promotions";
  return (
    <FactoryMapAccordionSection
      sectionId="sfmap-promotions-scoreboard"
      title="Promoted templates"
      summaryLine={summaryLine}
      activities={rows.length ? [{ active: true, label: `${rows.length} active` }] : undefined}
      defaultOpen={rows.length > 0}
      hint="Temporary promotions override long-term defaults while active."
    >
      {rows.length === 0 ? (
        <p className="factory-muted">No promoted templates right now.</p>
      ) : (
        <ul className="sfmap-promo-scoreboard">
          {rows.map((row, idx) => (
            <li key={`${row.family_slug}:${row.intent}:${row.scope}:${idx}`} className="sfmap-promo-scoreboard__row">
              <span className="mono">{row.family_slug}</span>
              <span className={`sfmap-promo-badge sfmap-promo-badge--${row.scope || "none"}`}>
                {row.scope === "temporary" ? "TEMP" : "DEFAULT"}
              </span>
              <span className="factory-muted">{row.intent}</span>
              {row.scope === "temporary" && row.expires_at ? (
                <span className="factory-muted" title={formatIsoDateTime(row.expires_at)}>
                  {formatPromotionCountdown(row.expires_at)}
                </span>
              ) : null}
              {row.note ? <span className="factory-muted">{row.note}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </FactoryMapAccordionSection>
  );
}

function InputCurationPanel({ families }: { families: ShapeFactoryMapFamily[] }) {
  const queryClient = useQueryClient();
  const [stillQ, setStillQ] = useState("");
  const [selectedCollectionId, setSelectedCollectionId] = useState("");
  const [newCollectionName, setNewCollectionName] = useState("");
  const [selectedFamilySlug, setSelectedFamilySlug] = useState("");
  const [msg, setMsg] = useState("");

  const sourceStillFamilies = useMemo(
    () =>
      families
        .filter((fam) =>
          (fam.shape?.requires || []).some((req) => String(req?.slot || "").trim() === "source_still"),
        )
        .map((fam) => fam.family_slug)
        .filter(Boolean)
        .sort(),
    [families],
  );

  useEffect(() => {
    if (!selectedFamilySlug && sourceStillFamilies.length > 0) setSelectedFamilySlug(sourceStillFamilies[0]);
  }, [selectedFamilySlug, sourceStillFamilies]);

  const stateQuery = useQuery({
    queryKey: queryKeys.shapeFactory.inputCurationState,
    queryFn: () => fetchShapeFactoryInputCurationState(),
    staleTime: 15_000,
    refetchOnWindowFocus: false,
  });
  const stillsQuery = useQuery({
    queryKey: queryKeys.shapeFactory.inputCurationStills({ q: stillQ, limit: 120 }),
    queryFn: () => fetchShapeFactoryInputCurationStills({ q: stillQ, limit: 120 }),
    staleTime: 20_000,
    refetchOnWindowFocus: false,
  });
  const effectiveSourcesQuery = useQuery({
    queryKey: queryKeys.shapeFactory.inputCurationEffectiveSources(selectedFamilySlug || ""),
    queryFn: () => fetchShapeFactoryInputCurationEffectiveSources(selectedFamilySlug),
    enabled: Boolean(selectedFamilySlug),
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });

  const invalidateAll = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.inputCurationRoot }),
      queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.mapRoot }),
    ]);
  }, [queryClient]);

  const mutateCollection = useMutation({
    mutationFn: mutateShapeFactoryInputCollection,
    onSuccess: () => void invalidateAll(),
  });
  const mutateBindings = useMutation({
    mutationFn: mutateShapeFactoryInputBindings,
    onSuccess: () => void invalidateAll(),
  });

  const collections = (stateQuery.data?.collections || []) as InputCurationCollection[];
  const bindings = stateQuery.data?.bindings || {};
  const selectedCollection =
    collections.find((c) => c.id === selectedCollectionId) || (collections.length ? collections[0] : null);
  const selectedCollectionItems = selectedCollection?.items || [];
  const attachedForFamily = selectedFamilySlug ? bindings[selectedFamilySlug] || [] : [];
  const summaryLine = `${collections.length} collections · ${sourceStillFamilies.length} source_still families`;

  useEffect(() => {
    if (!selectedCollectionId && collections.length > 0) setSelectedCollectionId(collections[0].id);
  }, [selectedCollectionId, collections]);

  const onRescan = useCallback(async () => {
    try {
      await fetchShapeFactoryInputCurationStills({ q: stillQ, limit: 120, scan: true });
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.inputCurationStills({ q: stillQ, limit: 120 }) });
      setMsg("Still catalog rescanned.");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }, [queryClient, stillQ]);

  return (
    <FactoryMapAccordionSection
      sectionId="sfmap-input-curation"
      title="Input curation"
      summaryLine={summaryLine}
      defaultOpen={false}
      hint="Collections attach per family; source selection merges pool stills with attached collections and de-dupes by path/content id."
    >
      {msg ? <p className="factory-muted">{msg}</p> : null}
      <div className="sfmap-curation-grid">
        <div>
          <h4>Still catalog</h4>
          <div className="sfmap-curation-row">
            <input value={stillQ} onChange={(e) => setStillQ(e.target.value)} placeholder="Search still path…" />
            <button type="button" className="btn" onClick={() => void onRescan()}>
              Rescan
            </button>
          </div>
          {stillsQuery.isLoading ? <p className="factory-muted">Loading still catalog…</p> : null}
          {stillsQuery.error instanceof Error ? <p className="factory-error">{stillsQuery.error.message}</p> : null}
          <ul className="sfmap-curation-list">
            {(stillsQuery.data?.items || []).slice(0, 40).map((it) => (
              <li key={it.path}>
                <button
                  type="button"
                  className="btn"
                  disabled={!selectedCollection || mutateCollection.isPending}
                  onClick={() =>
                    void mutateCollection
                      .mutateAsync({
                        op: "add_item",
                        collection_id: selectedCollection?.id,
                        path: it.path,
                      })
                      .then(() => setMsg(`Added to ${selectedCollection?.name || "collection"}`))
                      .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
                  }
                >
                  + Add
                </button>
                <span className="mono">{it.basename || it.path}</span>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h4>Collections</h4>
          <div className="sfmap-curation-row">
            <input
              value={newCollectionName}
              onChange={(e) => setNewCollectionName(e.target.value)}
              placeholder="New collection name"
            />
            <button
              type="button"
              className="btn"
              disabled={!newCollectionName.trim() || mutateCollection.isPending}
              onClick={() =>
                void mutateCollection
                  .mutateAsync({ op: "create", name: newCollectionName.trim() })
                  .then(() => {
                    setMsg(`Created collection ${newCollectionName.trim()}`);
                    setNewCollectionName("");
                  })
                  .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
              }
            >
              Create
            </button>
          </div>
          <ul className="sfmap-curation-list">
            {collections.map((c) => (
              <li key={c.id}>
                <button type="button" className="btn" onClick={() => setSelectedCollectionId(c.id)}>
                  {selectedCollectionId === c.id ? "Selected" : "Select"}
                </button>
                <span className="mono">{c.name}</span>
                <span className="factory-muted">({(c.items || []).length})</span>
                <button
                  type="button"
                  className="btn"
                  disabled={mutateCollection.isPending}
                  onClick={() =>
                    void mutateCollection
                      .mutateAsync({ op: "delete", collection_id: c.id })
                      .then(() => setMsg(`Deleted ${c.name}`))
                      .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
                  }
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
          {selectedCollection ? (
            <>
              <p className="factory-muted">
                {selectedCollection.name} items: {(selectedCollection.items || []).length}
              </p>
              <ul className="sfmap-curation-list">
                {selectedCollectionItems.slice(0, 30).map((it) => (
                  <li key={it.path}>
                    <span className="mono">{it.path}</span>
                    <button
                      type="button"
                      className="btn"
                      disabled={mutateCollection.isPending}
                      onClick={() =>
                        void mutateCollection
                          .mutateAsync({
                            op: "remove_item",
                            collection_id: selectedCollection.id,
                            path: it.path,
                          })
                          .then(() => setMsg(`Removed item from ${selectedCollection.name}`))
                          .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
                      }
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>

        <div>
          <h4>Family attachments</h4>
          <div className="sfmap-curation-row">
            <select value={selectedFamilySlug} onChange={(e) => setSelectedFamilySlug(e.target.value)}>
              {sourceStillFamilies.map((slug) => (
                <option key={slug} value={slug}>
                  {slug}
                </option>
              ))}
            </select>
          </div>
          <ul className="sfmap-curation-list">
            {collections.map((c) => {
              const attached = attachedForFamily.includes(c.id);
              return (
                <li key={`${selectedFamilySlug}:${c.id}`}>
                  <span className="mono">{c.name}</span>
                  <button
                    type="button"
                    className="btn"
                    disabled={!selectedFamilySlug || mutateBindings.isPending}
                    onClick={() =>
                      void mutateBindings
                        .mutateAsync({
                          op: attached ? "detach" : "attach",
                          family_slug: selectedFamilySlug,
                          collection_id: c.id,
                        })
                        .then(() => setMsg(`${attached ? "Detached" : "Attached"} ${c.name}`))
                        .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
                    }
                  >
                    {attached ? "Detach" : "Attach"}
                  </button>
                </li>
              );
            })}
          </ul>
          {effectiveSourcesQuery.data ? (
            <div className="factory-muted">
              Effective sources: {effectiveSourcesQuery.data.effective_count || 0} (pool{" "}
              {effectiveSourcesQuery.data.pool_count || 0} + added {effectiveSourcesQuery.data.added_count || 0})
            </div>
          ) : null}
        </div>
      </div>
    </FactoryMapAccordionSection>
  );
}

function FactoryMapIndexView({
  data,
  families,
  pipelines,
  promotionsByFamily,
  promotionEntries,
  promotionsBusy,
  onQuickTemporary,
  onQuickToday,
  onQuickLongTerm,
  onOpenPromotionEditor,
}: {
  data: ShapeFactoryMapResponse;
  families: ShapeFactoryMapFamily[];
  pipelines: ShapeFactoryMapPipeline[];
  promotionsByFamily?: Record<string, { scope?: string; expires_at?: string | null; intents?: string[]; note?: string | null }>;
  promotionEntries?: Array<{
    family_slug: string;
    intent: string;
    scope: string;
    expires_at?: string | null;
    note?: string | null;
  }>;
  promotionsBusy?: boolean;
  onQuickTemporary?: (familySlug: string) => void;
  onQuickToday?: (familySlug: string) => void;
  onQuickLongTerm?: (familySlug: string) => void;
  onOpenPromotionEditor?: (familySlug: string) => void;
}) {
  const summary = data.jobs?.summary || {};
  const queue = data.queue;
  const hourly = data.hourly?.next_sample;
  const allJobs = data.jobs?.items || [];

  const jobCountsByFamily = useMemo(() => {
    const out: Record<string, Record<string, number>> = {};
    for (const fam of families) {
      if (fam.family_slug) {
        out[fam.family_slug] = jobCountsForFamily(allJobs, fam.family_slug);
      }
    }
    return out;
  }, [families, allJobs]);

  const indexCtx = useMemo<FactoryMapIndexContext>(
    () => ({
      jobCountsByFamily,
      hourlyPhase: (data.hourly?.state?.phase as string | undefined) ?? hourly?.phase_if_idle ?? null,
    }),
    [jobCountsByFamily, data.hourly?.state, hourly?.phase_if_idle],
  );

  const familyActivities = useMemo(
    () =>
      families.flatMap((family) => {
        const activity = getFamilyActivity(family, indexCtx);
        return activity?.active ? [activity] : [];
      }),
    [families, indexCtx],
  );

  const pipelineActivities = useMemo(
    () =>
      pipelines.flatMap((pipe) => {
        const activity = getPipelineActivity(pipe, indexCtx);
        return activity?.active ? [activity] : [];
      }),
    [pipelines, indexCtx],
  );

  const pipelinesDefaultOpen = pipelineActivities.some((a) => a.active);

  return (
    <>
      <FactoryMapFamilyNav families={families} />
      {pipelines.length > 0 ? <FactoryMapPipelineNav pipelines={pipelines} /> : null}

      <div className="factory-summary sfmap-summary sfmap-summary--compact">
        <div>
          <strong>{families.length}</strong>
          <span>families</span>
        </div>
        <div>
          <strong>{data.jobs?.total || 0}</strong>
          <span>jobs total</span>
        </div>
        <div>
          <strong>{summary.complete || 0}</strong>
          <span>complete</span>
        </div>
        <div>
          <strong>{(summary.running || 0) + (summary.queued || 0)}</strong>
          <span>in flight</span>
        </div>
        {queue?.ok ? (
          <div>
            <strong>{queue.running_count || 0}</strong>
            <span>comfy running</span>
          </div>
        ) : null}
        <div className="factory-db-path mono" title={data.updated_at || undefined}>
          updated {formatIsoDateTime(data.updated_at)}
        </div>
      </div>

      {hourly ? (
        <div className="sfmap-hourly-banner">
          <strong>Hourly next (if idle):</strong> {hourly.sample_id || "—"} · pick {hourly.pick_index} ·{" "}
          {hourly.gex2_prompt}
          {hourly.note ? <span className="factory-muted"> — {hourly.note}</span> : null}
        </div>
      ) : null}

      <PromotionScoreboard entries={promotionEntries || []} />
      <InputCurationPanel families={families} />

      <FactoryMapAccordionSection
        sectionId="sfmap-families"
        title="Families"
        summaryLine={summarizeFamiliesSection(families, indexCtx)}
        activities={familyActivities}
        defaultOpen
        hint="Each family is one workflow shape plus its pools and deposit registry. Open a family for pools, members, and jobs."
      >
        <div className="sfmap-family-index-grid">
          {families.map((family) => (
            <FamilyIndexCard
              key={family.family_slug}
              family={family}
              jobCounts={jobCountsByFamily[family.family_slug || ""] || {}}
              activity={getFamilyActivity(family, indexCtx)}
              promotion={promotionsByFamily?.[family.family_slug] || null}
              busy={promotionsBusy}
              onQuickTemporary={onQuickTemporary}
              onQuickToday={onQuickToday}
              onQuickLongTerm={onQuickLongTerm}
              onOpenPromotionEditor={onOpenPromotionEditor}
            />
          ))}
        </div>
      </FactoryMapAccordionSection>

      {(data.pipelines || []).length > 0 ? (
        <FactoryMapAccordionSection
          sectionId="sfmap-pipelines"
          title="Pipelines"
          summaryLine={summarizePipelinesSection(pipelines, indexCtx)}
          activities={pipelineActivities}
          defaultOpen={pipelinesDefaultOpen}
          hint="Multi-step recipes that wire families together. Open a pipeline for step-by-step bindings and run commands."
        >
          <div className="sfmap-pipeline-index-grid">
            {pipelines.map((pipe) => (
              <PipelineIndexCard
                key={pipe.pipeline_id || pipe.path}
                pipeline={pipe}
                jobCountsByFamily={jobCountsByFamily}
                activity={getPipelineActivity(pipe, indexCtx)}
              />
            ))}
          </div>
        </FactoryMapAccordionSection>
      ) : null}

      <QuarantineWorkflowsPanel />
    </>
  );
}

function PairEnd({
  media,
  missing,
  missingLabel,
}: {
  media?: ShapeFactoryMapMediaRef;
  missing?: boolean;
  missingLabel?: string;
}) {
  const thumb = media?.thumb_url || media?.url;
  if (!missing && thumb) {
    return (
      <div className="sfmap-pair-end">
        <img src={thumb} alt="" loading="lazy" />
      </div>
    );
  }
  return (
    <div className="sfmap-pair-end sfmap-pair-end--missing" aria-hidden={!missingLabel}>
      <span>{missingLabel || "—"}</span>
    </div>
  );
}

function SourceOutputPairCard({
  pair,
  selected,
  onSelect,
}: {
  pair: SourceOutputPair;
  selected?: boolean;
  onSelect: (anchor: HTMLElement) => void;
}) {
  const status =
    pair.phase === "future" ? "future" : pair.job?.status || pair.gapNote || "—";
  const kind = pair.jobKind || (pair.phase === "future" ? "possible" : pair.phase === "seed" ? "seed" : "factory");
  return (
    <button
      type="button"
      className={
        "sfmap-pair-card" +
        (selected ? " sfmap-pair-card--selected" : "") +
        (pair.phase === "future" ? " sfmap-pair-card--future" : "")
      }
      onClick={(e) => onSelect(e.currentTarget)}
      title={`${kind} · ${pair.jobKey || pair.gapNote || ""}`}
    >
      <div className="sfmap-pair-card__track">
        <PairEnd
          media={pair.source}
          missing={pair.gap === "source" || !pair.source}
          missingLabel={pair.gap === "source" ? pair.gapNote || "no source" : undefined}
        />
        <span className="sfmap-pair-card__arrow" aria-hidden="true">
          →
        </span>
        <PairEnd
          media={pair.output}
          missing={pair.gap === "output" || !pair.output}
          missingLabel={
            pair.phase === "future"
              ? "future"
              : pair.gap === "output"
                ? pair.gapNote || "pending"
                : undefined
          }
        />
      </div>
      <div className="sfmap-pair-card__meta">
        <span className={`sfmap-pair-kind sfmap-pair-kind--${String(kind).replace(/[^a-z0-9_-]/gi, "")}`}>
          {kind}
        </span>
        <span className={statusClass(status)}>{status}</span>
        <span className="sfmap-pair-card__label">{shortPairLabel(pair)}</span>
      </div>
    </button>
  );
}

function FactoryMapFamilyView({
  data,
  family,
  families,
  onReload,
}: {
  data: ShapeFactoryMapResponse;
  family: ShapeFactoryMapFamily;
  families: ShapeFactoryMapFamily[];
  onReload: () => void;
}) {
  const [selectedPairKey, setSelectedPairKey] = useState<string | null>(null);
  const [selectedJobKey, setSelectedJobKey] = useState<string | null>(null);
  const [inspectorAnchor, setInspectorAnchor] = useState<HTMLElement | null>(null);
  const [inspectorScrollPad, setInspectorScrollPad] = useState(0);
  const [mediaModal, setMediaModal] = useState<MediaFullscreenPayload | null>(null);

  const openMedia = useCallback((media: MediaFullscreenPayload) => {
    setMediaModal(media);
  }, []);

  const closeInspector = useCallback(() => {
    setSelectedPairKey(null);
    setSelectedJobKey(null);
    setInspectorAnchor(null);
  }, []);

  const reportInspectorScrollPad = useCallback((neededPx: number) => {
    if (neededPx <= 0) return;
    setInspectorScrollPad((prev) => Math.max(prev, neededPx));
  }, []);

  useEffect(() => () => setInspectorScrollPad(0), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (mediaModal) {
        setMediaModal(null);
        return;
      }
      closeInspector();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeInspector, mediaModal]);

  const familyJobs = useMemo(
    () => (data.jobs?.items || []).filter((j) => j.family_slug === family.family_slug),
    [data, family.family_slug],
  );

  const jobsByKey = useMemo(() => {
    const m = new Map<string, ShapeFactoryMapJob>();
    for (const j of familyJobs) {
      if (j.job_key) m.set(j.job_key, j);
    }
    return m;
  }, [familyJobs]);

  const pairs = useMemo(() => buildSourceOutputPairs(family, familyJobs), [family, familyJobs]);
  const missingSourceCount = useMemo(
    () => pairs.filter((p) => p.gap === "source" && p.phase !== "future").length,
    [pairs],
  );
  const [recoverAllBusy, setRecoverAllBusy] = useState(false);
  const [recoverAllMsg, setRecoverAllMsg] = useState("");
  const recoverAllMutation = useMutation({
    mutationFn: recoverAssets,
    onSuccess: async () => {
      onReload();
    },
  });

  const handleRecoverAll = useCallback(async () => {
    setRecoverAllBusy(true);
    setRecoverAllMsg("");
    try {
      const res = await recoverAllMutation.mutateAsync({ family: family.family_slug });
      setRecoverAllMsg(
        res.total ? `Recovered ${res.recovered}/${res.total}` : "Nothing to recover",
      );
      if (res.recovered) onReload();
    } catch (e) {
      setRecoverAllMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setRecoverAllBusy(false);
    }
  }, [family.family_slug, onReload, recoverAllMutation]);

  const depositPool = (family.deposit_pools || [])[0];
  const pairGapSummary = useMemo(() => summarizePairGaps(pairs), [pairs]);
  const { runs: pairRunCount, future: pairFutureCount } = useMemo(() => countPairPhases(pairs), [pairs]);

  const selectedPair = useMemo(
    () => (selectedPairKey ? pairs.find((p) => p.pairKey === selectedPairKey) ?? null : null),
    [pairs, selectedPairKey],
  );

  const selectedJob = selectedPair?.job ?? (selectedJobKey ? jobsByKey.get(selectedJobKey) ?? null : null);
  const jobForMember = selectedPair?.jobKey ? jobsByKey.get(selectedPair.jobKey) ?? null : null;

  const inflight = familyJobs.filter((j) => {
    const pid = j.prompt_id;
    if (!pid) return false;
    return (data.queue?.shape_factory_matches || []).some((m) => m.prompt_id === pid);
  });

  const pending = familyJobs.filter((j) => j.status === "pending");

  const inspectorOpen = Boolean(inspectorAnchor && (selectedPair || selectedJob));

  const openPairInspector = useCallback(
    (anchor: HTMLElement, pair: SourceOutputPair) => {
      if (selectedPairKey === pair.pairKey) {
        closeInspector();
        return;
      }
      setSelectedPairKey(pair.pairKey);
      setSelectedJobKey(pair.jobKey || null);
      setInspectorAnchor(anchor);
    },
    [closeInspector, selectedPairKey],
  );

  const openJobInspector = useCallback(
    (anchor: HTMLElement, jobKey: string | null | undefined) => {
      if (!jobKey) return;
      const pair = pairs.find((p) => p.jobKey === jobKey);
      if (pair) {
        openPairInspector(anchor, pair);
        return;
      }
      if (selectedJobKey === jobKey) {
        closeInspector();
        return;
      }
      setSelectedPairKey(null);
      setSelectedJobKey(jobKey);
      setInspectorAnchor(anchor);
    },
    [closeInspector, openPairInspector, pairs, selectedJobKey],
  );

  return (
    <>
      <FactoryMapFamilyNav families={families} activeSlug={family.family_slug} />

      <div className="sfmap-family-block">
        <FamilyGraph family={family} />
        <section className="sfmap-pool-members sfmap-pair-section">
          <h3 className="sfmap-pool-members__title">
            Source → Output
            {depositPool?.pool_id ? (
              <span className="factory-muted"> · {depositPool.pool_id}</span>
            ) : null}
            <span className="factory-muted">
              {" "}
              · {pairRunCount} run{pairRunCount === 1 ? "" : "s"}
              {pairFutureCount ? ` · ${pairFutureCount} possible` : ""}
              {depositPool?.member_count != null ? ` (${depositPool.member_count} deposited)` : ""}
            </span>
          </h3>
          {pairGapSummary ? (
            <div className="sfmap-pair-gap-summary factory-muted">{pairGapSummary}</div>
          ) : null}
          {missingSourceCount > 0 ? (
            <div className="sfmap-missing-sources">
              <span className="sfmap-missing-badge" title="Source stills not found on disk">
                {missingSourceCount} missing source{missingSourceCount === 1 ? "" : "s"}
              </span>
              <button
                type="button"
                className="sfmap-recover-btn"
                onClick={handleRecoverAll}
                disabled={recoverAllBusy}
              >
                {recoverAllBusy ? "Recovering…" : "Recover all"}
              </button>
              {recoverAllMsg ? <span className="factory-muted">{recoverAllMsg}</span> : null}
            </div>
          ) : null}
          <div className="sfmap-pair-grid">
            {pairs.map((pair) => (
              <SourceOutputPairCard
                key={pair.pairKey}
                pair={pair}
                selected={selectedPairKey === pair.pairKey}
                onSelect={(anchor) => openPairInspector(anchor, pair)}
              />
            ))}
            {!pairs.length ? <div className="factory-empty">No source → output runs yet</div> : null}
          </div>
        </section>
      </div>

      <section className="sfmap-jobs-panel sfmap-jobs-panel--solo">
        <h2>Jobs · {family.family_slug}</h2>
        <div className="sfmap-job-list">
          {familyJobs.slice(0, 40).map((job) => (
            <JobRow
              key={job.job_key}
              job={job}
              selected={selectedPairKey === job.job_key || selectedJobKey === job.job_key}
              onSelect={(anchor) => openJobInspector(anchor, job.job_key)}
            />
          ))}
          {!familyJobs.length ? <div className="factory-empty">No jobs for this family</div> : null}
        </div>

        {inflight.length > 0 ? (
          <>
            <h3>On Comfy queue</h3>
            <div className="sfmap-job-list">
              {inflight.map((job) => (
                <JobRow
                  key={`inflight-${job.job_key}`}
                  job={job}
                  selected={selectedPairKey === job.job_key || selectedJobKey === job.job_key}
                  onSelect={(anchor) => openJobInspector(anchor, job.job_key)}
                />
              ))}
            </div>
          </>
        ) : null}

        {pending.length > 0 ? (
          <>
            <h3>Pending submit</h3>
            <div className="sfmap-job-list">
              {pending.slice(0, 10).map((job) => (
                <JobRow
                  key={`pending-${job.job_key}`}
                  job={job}
                  selected={selectedPairKey === job.job_key || selectedJobKey === job.job_key}
                  onSelect={(anchor) => openJobInspector(anchor, job.job_key)}
                />
              ))}
            </div>
          </>
        ) : null}
      </section>

      {inspectorScrollPad > 0 ? (
        <div className="sfmap-inspector-scroll-pad" style={{ height: inspectorScrollPad }} aria-hidden="true" />
      ) : null}

      <InspectorTooltipOverlay
        open={inspectorOpen}
        anchorEl={inspectorAnchor}
        onClose={closeInspector}
        onScrollPad={reportInspectorScrollPad}
      >
        <DetailPanel
          familySlug={family.family_slug}
          selectedPair={selectedPair}
          selectedJob={selectedJob}
          jobForMember={jobForMember}
          onOpenMedia={openMedia}
          onQueued={onReload}
        />
      </InspectorTooltipOverlay>

      <MediaFullscreenModal media={mediaModal} onClose={() => setMediaModal(null)} />
    </>
  );
}

export function DiscoveryFactoryMapApp() {
  const queryClient = useQueryClient();
  const shapeFactoryFetchCount = useIsFetching({ queryKey: queryKeys.shapeFactory.root });
  const shapeFactoryMutationCount = useIsMutating();
  const [route, setRoute] = useState<FactoryMapRoute>(() => parseFactoryMapRoute());

  useEffect(() => {
    const onPop = () => setRoute(parseFactoryMapRoute());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const membersLimit = route.view === "family" ? 24 : 8;
  const jobsPerFamily = route.view === "family" ? 48 : 24;
  const mapQuery = useQuery({
    queryKey: queryKeys.shapeFactory.map({ membersLimit, jobsLimit: 800, jobsPerFamily }),
    queryFn: () =>
      fetchShapeFactoryMap({
        members_limit: membersLimit,
        jobs_limit: 800,
        jobs_per_family: jobsPerFamily,
      }),
    staleTime: 30_000,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
    placeholderData: (prev) => prev,
  });
  const promotionsQuery = useQuery({
    queryKey: queryKeys.shapeFactory.promotions({ includeExpired: false }),
    queryFn: () => fetchShapeFactoryTemplatePromotions(),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
  const setPromotionMutation = useMutation({
    mutationFn: setShapeFactoryTemplatePromotion,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.promotionsRoot });
      await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.mapRoot });
    },
  });
  const data = mapQuery.data || null;
  const loading = mapQuery.isLoading && !data;
  const refreshing = mapQuery.isFetching && !loading;
  const error =
    mapQuery.error instanceof Error
      ? mapQuery.error.message
      : data && !data.ok
        ? data.error || "Map unavailable"
        : "";
  const reload = useCallback(() => mapQuery.refetch(), [mapQuery]);
  const invalidateMap = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.mapRoot });
  }, [queryClient]);

  const families = data?.families || [];
  const pipelines = data?.pipelines || [];
  const activeFamily =
    route.view === "family" ? families.find((f) => f.family_slug === route.familySlug) : undefined;
  const activePipeline =
    route.view === "pipeline" ? pipelines.find((p) => p.pipeline_id === route.pipelineId) : undefined;
  const promotionsByFamily =
    (promotionsQuery.data?.effective as
      | Record<string, { scope?: string; expires_at?: string | null; intents?: string[]; note?: string | null }>
      | undefined) || {};
  const promotionEntries = Array.isArray(promotionsQuery.data?.active_entries)
    ? (promotionsQuery.data?.active_entries as Array<{
        family_slug: string;
        intent: string;
        scope: string;
        expires_at?: string | null;
        note?: string | null;
      }>)
    : [];
  const promotionBusy = setPromotionMutation.isPending;
  const applyQuickPromotion = useCallback(
    async (familySlug: string, scope: "temporary" | "long_term", ttlHours?: number) => {
      try {
        await setPromotionMutation.mutateAsync({
          family_slug: familySlug,
          intents: ["extend"],
          scope,
          ttl_hours: scope === "temporary" ? ttlHours : undefined,
          note:
            scope === "temporary"
              ? "quick temporary promotion from factory map"
              : "quick long-term promotion from factory map",
          actor: "operator",
        });
        const suffix = scope === "temporary" ? (ttlHours ? `${Math.round(ttlHours * 100) / 100}h` : "temporary") : "long-term";
        setPromotionToast({ kind: "ok", text: `Promoted ${familySlug} (${suffix})` });
      } catch (e) {
        setPromotionToast({ kind: "err", text: e instanceof Error ? e.message : String(e) });
      }
    },
    [setPromotionMutation],
  );
  const onQuickTemporary = useCallback(
    (familySlug: string) => {
      void applyQuickPromotion(familySlug, "temporary", 2);
    },
    [applyQuickPromotion],
  );
  const onQuickToday = useCallback(
    (familySlug: string) => {
      const now = new Date();
      const midnight = new Date(now);
      midnight.setHours(24, 0, 0, 0);
      const ttl = Math.max(0.25, (midnight.getTime() - now.getTime()) / 3_600_000);
      void applyQuickPromotion(familySlug, "temporary", ttl);
    },
    [applyQuickPromotion],
  );
  const onQuickLongTerm = useCallback(
    (familySlug: string) => {
      void applyQuickPromotion(familySlug, "long_term");
    },
    [applyQuickPromotion],
  );
  const [promotionEditorFamily, setPromotionEditorFamily] = useState<string>("");
  const [promotionEditorScope, setPromotionEditorScope] = useState<"temporary" | "long_term">("temporary");
  const [promotionEditorIntents, setPromotionEditorIntents] = useState<Array<"extend" | "vary" | "derive">>(["extend"]);
  const [promotionEditorTtl, setPromotionEditorTtl] = useState<number>(2);
  const [promotionEditorNote, setPromotionEditorNote] = useState<string>("");
  const [promotionEditorMsg, setPromotionEditorMsg] = useState<string>("");
  const [promotionToast, setPromotionToast] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const promotionEditorOpen = Boolean(promotionEditorFamily);
  useEffect(() => {
    if (!promotionToast) return;
    const t = window.setTimeout(() => setPromotionToast(null), 4200);
    return () => window.clearTimeout(t);
  }, [promotionToast]);
  const openPromotionEditor = useCallback(
    (familySlug: string) => {
      const current = promotionsByFamily[familySlug];
      const intents = (current?.intents || []).filter((v): v is "extend" | "vary" | "derive" =>
        v === "extend" || v === "vary" || v === "derive",
      );
      setPromotionEditorFamily(familySlug);
      setPromotionEditorScope(current?.scope === "long_term" ? "long_term" : "temporary");
      setPromotionEditorIntents(intents.length ? intents : ["extend"]);
      setPromotionEditorTtl(2);
      setPromotionEditorNote(String(current?.note || ""));
      setPromotionEditorMsg("");
    },
    [promotionsByFamily],
  );
  const closePromotionEditor = useCallback(() => {
    setPromotionEditorFamily("");
    setPromotionEditorMsg("");
  }, []);
  const applyPromotionEditor = useCallback(async () => {
    if (!promotionEditorFamily || !promotionEditorIntents.length) {
      setPromotionEditorMsg("Select at least one intent.");
      return;
    }
    setPromotionEditorMsg("");
    try {
      await setPromotionMutation.mutateAsync({
        family_slug: promotionEditorFamily,
        intents: promotionEditorIntents,
        scope: promotionEditorScope,
        ttl_hours: promotionEditorScope === "temporary" ? promotionEditorTtl : undefined,
        note: promotionEditorNote.trim() || undefined,
        actor: "operator",
      });
      setPromotionEditorMsg("Saved.");
      setPromotionToast({
        kind: "ok",
        text: `Saved ${promotionEditorFamily} (${promotionEditorScope === "temporary" ? "temporary" : "long-term"})`,
      });
      closePromotionEditor();
    } catch (e) {
      setPromotionEditorMsg(e instanceof Error ? e.message : String(e));
      setPromotionToast({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    }
  }, [
    closePromotionEditor,
    promotionEditorFamily,
    promotionEditorIntents,
    promotionEditorNote,
    promotionEditorScope,
    promotionEditorTtl,
    setPromotionMutation,
  ]);
  const clearPromotionEditor = useCallback(async () => {
    if (!promotionEditorFamily || !promotionEditorIntents.length) return;
    setPromotionEditorMsg("");
    try {
      for (const scope of ["temporary", "long_term"] as const) {
        await setPromotionMutation.mutateAsync({
          family_slug: promotionEditorFamily,
          intents: promotionEditorIntents,
          scope,
          enabled: false,
          actor: "operator",
        });
      }
      setPromotionEditorMsg("Cleared.");
      setPromotionToast({ kind: "ok", text: `Cleared promotions for ${promotionEditorFamily}` });
      closePromotionEditor();
    } catch (e) {
      setPromotionEditorMsg(e instanceof Error ? e.message : String(e));
      setPromotionToast({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    }
  }, [closePromotionEditor, promotionEditorFamily, promotionEditorIntents, setPromotionMutation]);
  const statusLine = useMemo(() => {
    if (loading) return "Loading factory map cache…";
    const details: string[] = [];
    if (shapeFactoryFetchCount > 0) {
      details.push(
        `${shapeFactoryFetchCount} background fetch${shapeFactoryFetchCount === 1 ? "" : "es"}`,
      );
    }
    if (shapeFactoryMutationCount > 0) {
      details.push(
        `${shapeFactoryMutationCount} write${shapeFactoryMutationCount === 1 ? "" : "s"} in flight`,
      );
    }
    if (details.length > 0) return `Syncing: ${details.join(" · ")}`;
    return `Cache warm · last sync ${formatStatusTimestamp(mapQuery.dataUpdatedAt)}`;
  }, [loading, mapQuery.dataUpdatedAt, shapeFactoryFetchCount, shapeFactoryMutationCount]);

  return (
    <FactoryMapShell
      route={route}
      loading={loading}
      refreshing={refreshing}
      statusLine={statusLine}
      onRefresh={() => void reload()}
    >
      {error ? (
        <div className="factory-error" role="alert">
          {error}
        </div>
      ) : null}
      {promotionToast ? (
        <div className={promotionToast.kind === "ok" ? "sfmap-promo-toast sfmap-promo-toast--ok" : "sfmap-promo-toast sfmap-promo-toast--err"}>
          {promotionToast.text}
        </div>
      ) : null}

      <div className="discovery-factory-map-scroll">
        {data?.ok ? (
          route.view === "index" ? (
            <FactoryMapIndexView
              data={data}
              families={families}
              pipelines={pipelines}
              promotionsByFamily={promotionsByFamily}
              promotionEntries={promotionEntries}
              promotionsBusy={promotionBusy}
              onQuickTemporary={onQuickTemporary}
              onQuickToday={onQuickToday}
              onQuickLongTerm={onQuickLongTerm}
              onOpenPromotionEditor={openPromotionEditor}
            />
          ) : route.view === "pipeline" ? (
            activePipeline ? (
              <FactoryMapPipelineView
                data={data}
                pipeline={activePipeline}
                pipelines={pipelines}
                families={families}
              />
            ) : (
              <div className="sfmap-not-found">
                <p>
                  Pipeline <span className="mono">{route.pipelineId}</span> not found.
                </p>
                <a href={factoryMapIndexHref()} className="wx-screen-tab">
                  Back to factory map
                </a>
              </div>
            )
          ) : activeFamily ? (
            <FactoryMapFamilyView data={data} family={activeFamily} families={families} onReload={invalidateMap} />
          ) : (
            <div className="sfmap-not-found">
              <p>
                Family <span className="mono">{route.view === "family" ? route.familySlug : ""}</span> not found.
              </p>
              <a href={factoryMapIndexHref()} className="wx-screen-tab">
                Back to all families
              </a>
            </div>
          )
        ) : data && !data.ok ? (
          <div className="factory-error">{data.error || "Factory map data missing"}</div>
        ) : loading ? (
          <div className="factory-muted">Loading factory map…</div>
        ) : null}
      </div>
      {promotionEditorOpen ? (
        <div className="sfmap-promo-modal-backdrop" role="dialog" aria-modal="true" aria-label="Promotion editor">
          <div className="sfmap-promo-modal">
            <h3>Promote {promotionEditorFamily}</h3>
            <div className="sfmap-promo-modal__row">
              <label>
                <input
                  type="radio"
                  name="promo-scope"
                  checked={promotionEditorScope === "temporary"}
                  onChange={() => setPromotionEditorScope("temporary")}
                />{" "}
                Temporary
              </label>
              <label>
                <input
                  type="radio"
                  name="promo-scope"
                  checked={promotionEditorScope === "long_term"}
                  onChange={() => setPromotionEditorScope("long_term")}
                />{" "}
                Long-term
              </label>
            </div>
            <div className="sfmap-promo-modal__row">
              <span className="factory-muted">Intents</span>
              {(["extend", "vary", "derive"] as const).map((intent) => (
                <label key={intent}>
                  <input
                    type="checkbox"
                    checked={promotionEditorIntents.includes(intent)}
                    onChange={(e) => {
                      setPromotionEditorIntents((prev) => {
                        const next = e.target.checked ? [...prev, intent] : prev.filter((v) => v !== intent);
                        return Array.from(new Set(next));
                      });
                    }}
                  />{" "}
                  {intent}
                </label>
              ))}
            </div>
            {promotionEditorScope === "temporary" ? (
              <div className="sfmap-promo-modal__row">
                <span className="factory-muted">Duration</span>
                <select value={String(promotionEditorTtl)} onChange={(e) => setPromotionEditorTtl(Number(e.target.value) || 2)}>
                  <option value="2">2h</option>
                  <option value="6">6h</option>
                  <option value="12">12h</option>
                  <option value="24">24h</option>
                </select>
              </div>
            ) : null}
            <div className="sfmap-promo-modal__row">
              <input
                type="text"
                placeholder="Note (optional)"
                value={promotionEditorNote}
                onChange={(e) => setPromotionEditorNote(e.target.value)}
              />
            </div>
            {promotionEditorMsg ? <p className="factory-muted">{promotionEditorMsg}</p> : null}
            <div className="sfmap-promo-modal__actions">
              <button type="button" className="btn" disabled={promotionBusy} onClick={() => void applyPromotionEditor()}>
                {promotionBusy ? "Saving…" : "Save promotion"}
              </button>
              <button type="button" className="btn" disabled={promotionBusy} onClick={() => void clearPromotionEditor()}>
                Clear selected intents
              </button>
              <button type="button" className="btn" onClick={closePromotionEditor}>
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </FactoryMapShell>
  );
}
