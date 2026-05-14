import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  addWorkflowExplorerAsset,
  addWorkflowExplorerWorkflow,
  fetchWorkflowExplorerBrowse,
  fetchWorkflowExplorerFactory,
  removeWorkflowExplorerAsset,
  removeWorkflowExplorerWorkflow,
} from "./api";
import { MediaAssetCard } from "./MediaAssetCard";
import { VideoAutoplayToggle } from "./VideoAutoplayToggle";
import type {
  WorkflowExplorerAsset,
  WorkflowExplorerBrowseEntry,
  WorkflowExplorerBrowseResponse,
  WorkflowExplorerFactoryResponse,
  WorkflowExplorerPlannedJob,
  WorkflowExplorerRunPlan,
  WorkflowExplorerWorkflow,
} from "./types";

type FactoryMutator = () => Promise<WorkflowExplorerFactoryResponse>;
const FACTORY_BROWSER_VIDEO_AUTOPLAY_KEY = "workflow_explorer_browser_video_autoplay";

function basename(path: string): string {
  const parts = String(path || "").split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function shortHash(value?: string | null): string {
  return value ? value.slice(0, 12) : "-";
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatSeconds(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const s = Math.floor(sec % 60);
  const m = Math.floor((sec / 60) % 60);
  const h = Math.floor(sec / 3600);
  const pad = (n: number) => (n < 10 ? `0${n}` : String(n));
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function loadFactoryBrowserVideoAutoplay(): boolean {
  try {
    return localStorage.getItem(FACTORY_BROWSER_VIDEO_AUTOPLAY_KEY) === "1";
  } catch {
    return false;
  }
}

function persistFactoryBrowserVideoAutoplay(on: boolean) {
  try {
    localStorage.setItem(FACTORY_BROWSER_VIDEO_AUTOPLAY_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function FactoryCardActions({
  onRemove,
  busy,
}: {
  onRemove?: () => void;
  busy?: boolean;
}) {
  if (!onRemove) return null;
  return (
    <details className="factory-card-actions">
      <summary aria-label="Card actions" title="Card actions">
        ⋯
      </summary>
      <div className="factory-card-actions-menu">
        <button type="button" disabled={busy} onClick={onRemove}>
          Remove from bucket
        </button>
      </div>
    </details>
  );
}

function FactoryAssetCard({
  asset,
  onRemove,
  busy,
}: {
  asset: WorkflowExplorerAsset;
  onRemove?: () => void;
  busy?: boolean;
}) {
  const isVideo = asset.media_type === "video";
  return (
    <div className="factory-edit-card">
      <MediaAssetCard
        name={basename(asset.path)}
        path={asset.path}
        mediaType={asset.media_type}
        role={asset.role}
        status={asset.status}
        thumbUrl={!isVideo ? asset.url : null}
        videoUrl={isVideo ? asset.url : null}
        badge={asset.bucket_name}
        className="factory-asset-card"
      />
      <FactoryCardActions onRemove={onRemove} busy={busy} />
    </div>
  );
}

function FactoryWorkflowCard({
  workflow,
  onRemove,
  busy,
}: {
  workflow: WorkflowExplorerWorkflow;
  onRemove?: () => void;
  busy?: boolean;
}) {
  const inputTypes = Array.isArray(workflow.input_contract?.media_types)
    ? workflow.input_contract?.media_types.join(", ")
    : "unknown";
  const outputTypes = Array.isArray(workflow.output_contract?.media_types)
    ? workflow.output_contract?.media_types.join(", ")
    : "unknown";
  return (
    <div className="factory-edit-card">
      <div className="factory-card">
        <div className="factory-card-title">{basename(workflow.path)}</div>
        <div className="factory-card-meta">
          {workflow.workflow_type} · graph {shortHash(workflow.graph_hash)}
        </div>
        <div className="factory-card-meta">
          accepts {inputTypes} → produces {outputTypes}
        </div>
        <div className="factory-card-path">{workflow.path}</div>
      </div>
      <FactoryCardActions onRemove={onRemove} busy={busy} />
    </div>
  );
}

function FactoryJobCard({ job }: { job: WorkflowExplorerPlannedJob }) {
  const metadata = job.metadata || {};
  const outputPrefix = typeof metadata.output_prefix === "string" ? metadata.output_prefix : "";
  return (
    <div className="factory-card">
      <div className="factory-card-title">{job.job_key}</div>
      <div className="factory-card-meta">status · {job.status}</div>
      {job.generated_workflow_path ? <div className="factory-card-path">{job.generated_workflow_path}</div> : null}
      {outputPrefix ? <div className="factory-card-path">output · {outputPrefix}</div> : null}
    </div>
  );
}

function ArrowColumn({ label }: { label: string }) {
  return (
    <div className="factory-arrow-column" aria-hidden="true">
      <div className="factory-arrow-line" />
      <div className="factory-arrow">{label}</div>
      <div className="factory-arrow-line" />
    </div>
  );
}

function BucketAddForm({
  label,
  browseKind,
  disabled,
  onAdd,
}: {
  label: string;
  browseKind: "asset" | "workflow";
  disabled?: boolean;
  onAdd: (path: string) => Promise<void>;
}) {
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browse, setBrowse] = useState<WorkflowExplorerBrowseResponse | null>(null);
  const [browseRoot, setBrowseRoot] = useState("");
  const [browseDir, setBrowseDir] = useState("");
  const [browseQuery, setBrowseQuery] = useState("");
  const [browseMediaType, setBrowseMediaType] = useState<"all" | "image" | "video">("image");
  const [selectedEntry, setSelectedEntry] = useState<WorkflowExplorerBrowseEntry | null>(null);
  const [activeEntryPath, setActiveEntryPath] = useState("");
  const [videoAutoplay, setVideoAutoplay] = useState(loadFactoryBrowserVideoAutoplay);
  const [clipIn, setClipIn] = useState<number | null>(null);
  const [clipOut, setClipOut] = useState<number | null>(null);
  const [previewDuration, setPreviewDuration] = useState(0);
  const [previewCurrentTime, setPreviewCurrentTime] = useState(0);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [browseError, setBrowseError] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);
  const previewVideoRef = useRef<HTMLVideoElement | null>(null);

  const loadBrowse = async (next?: { root?: string; dir?: string; q?: string; mediaType?: "all" | "image" | "video" }) => {
    const root = next?.root ?? browseRoot;
    const dir = next?.dir ?? browseDir;
    const q = next?.q ?? browseQuery;
    const mediaType = next?.mediaType ?? browseMediaType;
    setBrowseLoading(true);
    setBrowseError("");
    try {
      const data = await fetchWorkflowExplorerBrowse({
        root: root || undefined,
        dir,
        kind: browseKind,
        media_type: browseKind === "asset" ? mediaType : undefined,
        q,
        limit: 300,
      });
      setBrowse(data);
      const resolvedRoot = data.root?.id || data.roots.find((r) => r.kind === browseKind)?.id || data.roots[0]?.id || "";
      setBrowseRoot(resolvedRoot);
      setBrowseDir(data.dir || "");
      setSelectedEntry(null);
      setActiveEntryPath("");
    } catch (e) {
      setBrowseError(e instanceof Error ? e.message : String(e));
    } finally {
      setBrowseLoading(false);
    }
  };

  const openBrowser = async () => {
    setBrowserOpen(true);
    if (!browse) {
      await loadBrowse();
    }
  };

  const chooseEntry = (entry: WorkflowExplorerBrowseEntry) => {
    if (entry.is_dir) {
      void loadBrowse({ dir: entry.relpath });
      return;
    }
    setActiveEntryPath(entry.path);
    setSelectedEntry(entry);
    setClipIn(null);
    setClipOut(null);
    setPreviewDuration(0);
    setPreviewCurrentTime(0);
  };

  const setVideoAutoplayFromUser = (on: boolean) => {
    setVideoAutoplay(on);
    persistFactoryBrowserVideoAutoplay(on);
  };

  const entries = browse?.entries || [];
  const activeIndex = activeEntryPath ? entries.findIndex((entry) => entry.path === activeEntryPath) : -1;

  const focusEntryAt = (index: number) => {
    if (!entries.length) return;
    const nextIndex = Math.max(0, Math.min(entries.length - 1, index));
    const entry = entries[nextIndex];
    setActiveEntryPath(entry.path);
    if (!entry.is_dir) {
      setSelectedEntry(entry);
    }
    window.requestAnimationFrame(() => {
      const row = listRef.current?.querySelector<HTMLButtonElement>(`[data-entry-index="${nextIndex}"]`);
      row?.scrollIntoView({ block: "nearest" });
      row?.focus();
    });
  };

  const onBrowserKeyDown = (e: React.KeyboardEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest("input, select, textarea")) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closeBrowser();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusEntryAt(activeIndex < 0 ? 0 : activeIndex + 1);
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      focusEntryAt(activeIndex < 0 ? entries.length - 1 : activeIndex - 1);
      return;
    }
    if (e.key === "Home") {
      e.preventDefault();
      focusEntryAt(0);
      return;
    }
    if (e.key === "End") {
      e.preventDefault();
      focusEntryAt(entries.length - 1);
      return;
    }
    if (e.key === "Enter" && activeIndex >= 0) {
      e.preventDefault();
      chooseEntry(entries[activeIndex]);
    }
  };

  const closeBrowser = () => {
    setBrowserOpen(false);
  };

  const selectedIsImage = selectedEntry?.media_type === "image" && selectedEntry.url;
  const selectedIsVideo = selectedEntry?.media_type === "video" && selectedEntry.url;
  const clipActive = selectedIsVideo && clipIn != null && clipOut != null && clipOut > clipIn;

  const seekPreview = (sec: number) => {
    const video = previewVideoRef.current;
    const next = Math.max(0, Math.min(previewDuration || Number.POSITIVE_INFINITY, sec));
    if (video) video.currentTime = next;
    setPreviewCurrentTime(next);
  };

  const playClip = () => {
    const video = previewVideoRef.current;
    if (!video || !clipActive || clipIn == null) return;
    video.currentTime = clipIn;
    setPreviewCurrentTime(clipIn);
    void video.play().catch(() => {});
  };

  return (
    <div className="factory-add-panel">
      <div className="factory-add-actions">
        <button type="button" disabled={disabled || browseLoading} onClick={() => void openBrowser()}>
          {browseKind === "asset" ? "Browse Assets" : "Browse Workflows"}
        </button>
      </div>
      {browserOpen ? (
        <div
          className="factory-browser-modal"
          role="dialog"
          aria-modal="true"
          aria-label={label}
          onKeyDown={onBrowserKeyDown}
        >
          <div className="factory-browser-backdrop" onClick={closeBrowser} />
          <div className="factory-browser-dialog">
            <div className="factory-browser-head">
              <div>
                <h3>{label}</h3>
                <div className="factory-muted">Select a file, preview it, then add it to this bucket.</div>
              </div>
              <button type="button" onClick={closeBrowser}>Close</button>
            </div>
            <div className="factory-browser-toolbar">
              <select
                value={browseRoot}
                disabled={browseLoading}
                onChange={(e) => {
                  const root = e.target.value;
                  setBrowseRoot(root);
                  void loadBrowse({ root, dir: "" });
                }}
              >
                {(browse?.roots || []).map((root) => (
                  <option key={root.id} value={root.id}>
                    {root.label}
                  </option>
                ))}
              </select>
              {browseKind === "asset" ? (
                <select
                  value={browseMediaType}
                  disabled={browseLoading}
                  onChange={(e) => {
                    const mediaType = e.target.value as "all" | "image" | "video";
                    setBrowseMediaType(mediaType);
                    void loadBrowse({ mediaType });
                  }}
                >
                  <option value="image">Images</option>
                  <option value="video">Videos</option>
                  <option value="all">Images + Videos</option>
                </select>
              ) : (
                <select value="workflow" disabled>
                  <option value="workflow">Workflows</option>
                </select>
              )}
              <input
                type="search"
                value={browseQuery}
                disabled={browseLoading}
                onChange={(e) => setBrowseQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void loadBrowse({ q: browseQuery });
                  }
                }}
                placeholder="Filter current folder…"
              />
              <button type="button" disabled={browseLoading} onClick={() => void loadBrowse({ q: browseQuery })}>
                Search
              </button>
            </div>
            <div className="factory-browser-path">
              <span>{browse?.root?.path || "loading roots…"}</span>
              {browseDir ? <span>/{browseDir}</span> : null}
            </div>
            <div className="factory-browser-main">
              <div className="factory-browser-left">
                <div className="factory-browser-key-hint">Arrow keys move selection · Enter opens/selects · Esc closes</div>
                {browse?.parent !== undefined && browse?.parent !== null ? (
                  <button type="button" className="factory-browser-up" disabled={browseLoading} onClick={() => void loadBrowse({ dir: browse.parent || "" })}>
                    ../
                  </button>
                ) : null}
                {browseError ? <div className="factory-browser-error">{browseError}</div> : null}
                <div className="factory-browser-list" ref={listRef} tabIndex={0}>
                  {browseLoading ? <div className="factory-browser-empty">Loading…</div> : null}
                  {!browseLoading && entries.map((entry, idx) => (
                    <button
                      type="button"
                      key={`${entry.is_dir ? "d" : "f"}:${entry.relpath}`}
                      data-entry-index={idx}
                      className={
                        "factory-browser-row" +
                        (entry.is_dir ? " factory-browser-row--dir" : "") +
                        (activeEntryPath === entry.path ? " factory-browser-row--selected" : "")
                      }
                      onClick={() => {
                        setActiveEntryPath(entry.path);
                        chooseEntry(entry);
                      }}
                      onFocus={() => setActiveEntryPath(entry.path)}
                    >
                      {entry.is_dir ? (
                        <>
                          <span className="factory-browser-row-name">[dir] {entry.name}</span>
                          <span className="factory-browser-row-meta">folder</span>
                        </>
                      ) : (
                        <MediaAssetCard
                          name={entry.name}
                          path={entry.path}
                          mediaType={entry.media_type}
                          badge={entry.media_type}
                          detail={formatBytes(entry.size)}
                          thumbUrl={entry.media_type === "image" ? entry.url : null}
                          videoUrl={entry.media_type === "video" ? entry.url : null}
                          showVideoThumb
                          showPath={false}
                          className="factory-browser-row-card"
                        />
                      )}
                    </button>
                  ))}
                  {!browseLoading && browse && !browse.entries.length ? (
                    <div className="factory-browser-empty">No matching files in this folder.</div>
                  ) : null}
                </div>
              </div>
              <div className="factory-browser-preview">
                {selectedEntry ? (
                  <>
                    <div className="factory-browser-preview-frame">
                      {selectedIsImage ? (
                        <img className="factory-browser-preview-media" src={selectedEntry.url || ""} alt="" />
                      ) : selectedIsVideo ? (
                        <video
                          className="factory-browser-preview-media"
                          ref={previewVideoRef}
                          key={selectedEntry.path}
                          src={selectedEntry.url || ""}
                          controls
                          autoPlay={videoAutoplay}
                          muted={videoAutoplay}
                          loop={videoAutoplay}
                          onLoadedMetadata={(e) => {
                            const video = e.currentTarget;
                            const duration = Number.isFinite(video.duration) ? video.duration : 0;
                            setPreviewDuration(duration);
                            setPreviewCurrentTime(video.currentTime || 0);
                          }}
                          onTimeUpdate={(e) => {
                            const video = e.currentTarget;
                            setPreviewCurrentTime(video.currentTime || 0);
                            if (clipActive && clipOut != null && video.currentTime >= clipOut) {
                              video.pause();
                              video.currentTime = clipOut;
                            }
                          }}
                        />
                      ) : (
                        <div className="factory-browser-preview-empty">No preview for this file type.</div>
                      )}
                    </div>
                    {selectedEntry.media_type === "video" ? (
                      <VideoAutoplayToggle
                        className="factory-browser-autoplay"
                        videoAutoplay={videoAutoplay}
                        onVideoAutoplayChange={setVideoAutoplayFromUser}
                        label="Autoplay selected videos (muted)"
                      />
                    ) : null}
                    {selectedEntry.media_type === "video" ? (
                      <div className="factory-browser-clip-controls" aria-label="Preview clip controls">
                        <div className="factory-browser-clip-time">
                          {formatSeconds(previewCurrentTime)} / {formatSeconds(previewDuration)}
                        </div>
                        <input
                          type="range"
                          min={0}
                          max={Math.max(0, previewDuration)}
                          step={0.01}
                          value={Math.min(previewCurrentTime, Math.max(0, previewDuration))}
                          disabled={!previewDuration}
                          onChange={(e) => seekPreview(Number(e.target.value))}
                        />
                        <div className="factory-browser-clip-buttons">
                          <button
                            type="button"
                            disabled={!previewDuration}
                            onClick={() => setClipIn(previewCurrentTime)}
                          >
                            Set In
                          </button>
                          <button
                            type="button"
                            disabled={!previewDuration}
                            onClick={() => setClipOut(previewCurrentTime)}
                          >
                            Set Out
                          </button>
                          <button type="button" disabled={!clipActive} onClick={playClip}>
                            Play Clip
                          </button>
                          <button
                            type="button"
                            disabled={clipIn == null && clipOut == null}
                            onClick={() => {
                              setClipIn(null);
                              setClipOut(null);
                            }}
                          >
                            Clear
                          </button>
                        </div>
                        <div className="factory-browser-clip-readout">
                          In {clipIn == null ? "--" : formatSeconds(clipIn)} · Out{" "}
                          {clipOut == null ? "--" : formatSeconds(clipOut)}
                          {clipIn != null && clipOut != null && clipOut <= clipIn ? " · out must be after in" : ""}
                        </div>
                      </div>
                    ) : null}
                    <div className="factory-browser-preview-title">{selectedEntry.name}</div>
                    <div className="factory-browser-preview-meta">
                      {selectedEntry.media_type} · {formatBytes(selectedEntry.size)}
                    </div>
                    <div className="factory-browser-preview-path">{selectedEntry.path}</div>
                  </>
                ) : (
                  <div className="factory-browser-preview-empty">Select an image or video to preview it here.</div>
                )}
              </div>
            </div>
            <div className="factory-browser-footer">
              <div className="factory-browser-selected-path">{selectedEntry?.path || "No file selected"}</div>
              <button
                type="button"
                disabled={!selectedEntry || disabled}
                onClick={async () => {
                  if (!selectedEntry) return;
                  try {
                    await onAdd(selectedEntry.path);
                    closeBrowser();
                  } catch {
                    /* Parent error banner handles details. */
                  }
                }}
              >
                Add Selected
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function RunPlanGraph({
  plan,
  busy,
  onAddAsset,
  onRemoveAsset,
  onAddWorkflow,
  onRemoveWorkflow,
}: {
  plan: WorkflowExplorerRunPlan;
  busy?: boolean;
  onAddAsset: (bucketId: number, path: string) => Promise<void>;
  onRemoveAsset: (assetId: number) => Promise<void>;
  onAddWorkflow: (bucketId: number, path: string) => Promise<void>;
  onRemoveWorkflow: (workflowId: number) => Promise<void>;
}) {
  return (
    <div className="factory-plan">
      <div className="factory-plan-header">
        <div>
          <h2>{plan.name}</h2>
          <div className="factory-muted">
            {plan.input_bucket_name} → {plan.workflow_bucket_name} → {plan.output_bucket_name}
          </div>
        </div>
        <div className="factory-pill">{plan.planned_jobs.length} planned job{plan.planned_jobs.length === 1 ? "" : "s"}</div>
      </div>

      <div className="factory-graph">
        <section className="factory-column">
          <h3>Input Assets</h3>
          <div className="factory-bucket-name">
            {plan.input_bucket_name} <span>#{plan.input_bucket_id}</span>
          </div>
          <BucketAddForm
            label="Add asset"
            browseKind="asset"
            disabled={busy}
            onAdd={(path) => onAddAsset(plan.input_bucket_id, path)}
          />
          <div className="factory-card-list">
            {plan.input_assets.map((asset) => (
              <FactoryAssetCard
                key={asset.id}
                asset={asset}
                busy={busy}
                onRemove={() => {
                  void onRemoveAsset(asset.id).catch(() => {});
                }}
              />
            ))}
            {!plan.input_assets.length ? <div className="factory-empty">No input assets</div> : null}
          </div>
        </section>

        <ArrowColumn label="maps to" />

        <section className="factory-column">
          <h3>Workflows</h3>
          <div className="factory-bucket-name">
            {plan.workflow_bucket_name} <span>#{plan.workflow_bucket_id}</span>
          </div>
          <BucketAddForm
            label="Add workflow"
            browseKind="workflow"
            disabled={busy}
            onAdd={(path) => onAddWorkflow(plan.workflow_bucket_id, path)}
          />
          <div className="factory-card-list">
            {plan.workflow_items.map((workflow) => (
              <FactoryWorkflowCard
                key={workflow.id}
                workflow={workflow}
                busy={busy}
                onRemove={() => {
                  void onRemoveWorkflow(workflow.id).catch(() => {});
                }}
              />
            ))}
            {!plan.workflow_items.length ? <div className="factory-empty">No workflows</div> : null}
          </div>
        </section>

        <ArrowColumn label="produces" />

        <section className="factory-column">
          <h3>Output Assets</h3>
          <div className="factory-bucket-name">
            {plan.output_bucket_name} <span>#{plan.output_bucket_id}</span>
          </div>
          <div className="factory-card-list">
            {plan.planned_jobs.map((job) => (
              <FactoryJobCard key={job.id} job={job} />
            ))}
            {!plan.planned_jobs.length
              ? plan.output_assets.map((asset) => <FactoryAssetCard key={asset.id} asset={asset} />)
              : null}
            {!plan.planned_jobs.length && !plan.output_assets.length ? <div className="factory-empty">No output assets</div> : null}
          </div>
        </section>
      </div>
    </div>
  );
}

export function WorkflowExplorerApp() {
  const [data, setData] = useState<WorkflowExplorerFactoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");

  const reload = async () => {
    setLoading(true);
    setError("");
    try {
      const next = await fetchWorkflowExplorerFactory();
      setData(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  const runFactoryUpdate = async (label: string, mutator: FactoryMutator) => {
    setBusyAction(label);
    setError("");
    try {
      const next = await mutator();
      setData(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      throw e;
    } finally {
      setBusyAction("");
    }
  };

  const summary = useMemo(() => {
    const plans = data?.run_plans || [];
    return {
      buckets: data?.buckets?.length || 0,
      plans: plans.length,
      jobs: plans.reduce((acc, p) => acc + p.planned_jobs.length, 0),
    };
  }, [data]);

  return (
    <div className="workflow-explorer-screen">
      <div className="factory-topbar">
        <div>
          <h1>Workflow Explorer · Factory Spike</h1>
          <div className="factory-muted">
            First pass: input asset bucket → workflow bucket → output asset bucket
          </div>
        </div>
        <button type="button" disabled={loading || Boolean(busyAction)} onClick={() => void reload()}>
          {loading ? "Refreshing…" : busyAction || "Refresh"}
        </button>
      </div>

      {error ? <div className="factory-error">{error}</div> : null}
      {data && !data.ok ? <div className="factory-error">{data.error || "Factory data unavailable"}</div> : null}

      <div className="factory-summary">
        <div>
          <strong>{summary.buckets}</strong>
          <span>buckets</span>
        </div>
        <div>
          <strong>{summary.plans}</strong>
          <span>run plans</span>
        </div>
        <div>
          <strong>{summary.jobs}</strong>
          <span>planned jobs</span>
        </div>
        <div className="factory-db-path">{data?.db_path || "loading factory registry…"}</div>
      </div>

      <div className="factory-plans">
        {(data?.run_plans || []).map((plan) => (
          <RunPlanGraph
            key={plan.id}
            plan={plan}
            busy={Boolean(busyAction)}
            onAddAsset={(bucketId, path) =>
              runFactoryUpdate("Adding asset…", () => addWorkflowExplorerAsset({ bucket_id: bucketId, path }))
            }
            onRemoveAsset={(assetId) =>
              runFactoryUpdate("Removing asset…", () => removeWorkflowExplorerAsset({ item_id: assetId }))
            }
            onAddWorkflow={(bucketId, path) =>
              runFactoryUpdate("Adding workflow…", () => addWorkflowExplorerWorkflow({ bucket_id: bucketId, path }))
            }
            onRemoveWorkflow={(workflowId) =>
              runFactoryUpdate("Removing workflow…", () => removeWorkflowExplorerWorkflow({ item_id: workflowId }))
            }
          />
        ))}
        {data && data.ok && !data.run_plans.length ? <div className="factory-empty">No run plans in the factory registry.</div> : null}
      </div>
    </div>
  );
}
