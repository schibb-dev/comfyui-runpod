import React, { useMemo, useState } from "react";
import type { ExperimentSummary, QueueResponse, RunsItem } from "./types";
import { getRunDisplayStatus, RUN_DISPLAY_LABELS } from "./runDisplayStatus";
import type { RunDisplayStatus } from "./types";

function runKey(r: Pick<RunsItem, "exp_id" | "run_id">): string {
  return `${r.exp_id}::${r.run_id}`;
}

const UNKNOWN_SOURCE_KEY = "\0unknown";
const UNKNOWN_SOURCE_IMAGE_KEY = "\0unknown_image";

const SEP = "\0";

function normalizeSourceKey(s: string | undefined): string {
  const t = (s ?? "").trim().replace(/\\/g, "/");
  return t || UNKNOWN_SOURCE_KEY;
}

function normalizeSourceImageKey(s: string | undefined): string {
  const t = (s ?? "").trim().replace(/\\/g, "/");
  return t || UNKNOWN_SOURCE_IMAGE_KEY;
}

function sourceDisplayLabel(sourceKey: string): string {
  if (sourceKey === UNKNOWN_SOURCE_KEY) return "Unknown source";
  const name = sourceKey.replace(/^.*\//, "");
  return name || sourceKey;
}

function sourceImageDisplayLabel(key: string): string {
  if (key === UNKNOWN_SOURCE_IMAGE_KEY) return "Unknown source image";
  const name = key.replace(/^.*\//, "");
  return name || key;
}

export type SourceVideoGroup = {
  sourceKey: string;
  displayLabel: string;
  experiments: ExperimentSummary[];
};

export type SourceImageGroup = {
  sourceImageKey: string;
  sourceImageLabel: string;
  videos: SourceVideoGroup[];
};

function groupExperimentsBySourceImageThenVideo(experiments: ExperimentSummary[]): SourceImageGroup[] {
  const byImage = new Map<string, Map<string, ExperimentSummary[]>>();
  for (const exp of experiments) {
    const imgKey = normalizeSourceImageKey(exp.source_image);
    const vidKey = normalizeSourceKey(exp.base_mp4);
    let byVid = byImage.get(imgKey);
    if (!byVid) {
      byVid = new Map();
      byImage.set(imgKey, byVid);
    }
    const list = byVid.get(vidKey) ?? [];
    list.push(exp);
    byVid.set(vidKey, list);
  }
  const imageGroups: SourceImageGroup[] = [];
  for (const [imgKey, byVid] of byImage.entries()) {
    const videos: SourceVideoGroup[] = Array.from(byVid.entries())
      .map(([sourceKey, exps]) => ({
        sourceKey,
        displayLabel: sourceDisplayLabel(sourceKey),
        experiments: exps,
      }))
      .sort((a, b) => {
        if (a.sourceKey === UNKNOWN_SOURCE_KEY) return 1;
        if (b.sourceKey === UNKNOWN_SOURCE_KEY) return -1;
        return a.displayLabel.localeCompare(b.displayLabel);
      });
    imageGroups.push({
      sourceImageKey: imgKey,
      sourceImageLabel: sourceImageDisplayLabel(imgKey),
      videos,
    });
  }
  imageGroups.sort((a, b) => {
    if (a.sourceImageKey === UNKNOWN_SOURCE_IMAGE_KEY) return 1;
    if (b.sourceImageKey === UNKNOWN_SOURCE_IMAGE_KEY) return -1;
    return a.sourceImageLabel.localeCompare(b.sourceImageLabel);
  });
  return imageGroups;
}

export type ExperimentListRunsEntry = {
  runs: RunsItem[];
  manifest?: Record<string, unknown>;
};

export type ExperimentListProps = {
  experiments: ExperimentSummary[];
  filterValue: string;
  onFilterChange: (value: string) => void;
  expandedExpIds: string[];
  onToggleExpand: (expId: string) => void;
  runsByExpId: Record<string, ExperimentListRunsEntry>;
  loadingRunsExpId: string | null;
  onLoadRuns: (expId: string) => void;
  queue: QueueResponse | null;
  selectedExpId: string | null;
  onSelectExperiment: (expId: string | null) => void;
  selectedRunKeys: string[]; // [A_key, B_key] for highlighting
  onSetSlot: (slot: "A" | "B", run: RunsItem) => void;
  onRefresh: () => void;
  open: boolean;
  onToggleOpen: () => void;
};

const BADGE_STYLE: Record<RunDisplayStatus, React.CSSProperties> = {
  finished: { background: "var(--ok-bg, #1a3d1a)", color: "var(--ok-fg, #b8e0b8)", padding: "2px 6px", borderRadius: 4, fontSize: 10 },
  waiting: { background: "var(--bg-2)", color: "var(--muted)", padding: "2px 6px", borderRadius: 4, fontSize: 10 },
  queued: { background: "var(--warn-bg, #3d3a1a)", color: "var(--warn-fg, #e0dcb8)", padding: "2px 6px", borderRadius: 4, fontSize: 10 },
  in_process: { background: "var(--accent-bg, #1a2a3d)", color: "var(--accent-fg, #b8d0e0)", padding: "2px 6px", borderRadius: 4, fontSize: 10 },
};

export function ExperimentList({
  experiments,
  filterValue,
  onFilterChange,
  expandedExpIds,
  onToggleExpand,
  runsByExpId,
  loadingRunsExpId,
  onLoadRuns,
  queue,
  selectedExpId,
  onSelectExperiment,
  selectedRunKeys,
  onSetSlot,
  onRefresh,
  open,
  onToggleOpen,
}: ExperimentListProps) {
  const imageGroups = useMemo(() => groupExperimentsBySourceImageThenVideo(experiments), [experiments]);
  const [collapsedSourceImageKeys, setCollapsedSourceImageKeys] = useState<Set<string>>(() => new Set());
  const [collapsedSourceKeys, setCollapsedSourceKeys] = useState<Set<string>>(() => new Set());

  function videoRowKey(sourceImageKey: string, sourceKey: string): string {
    return sourceImageKey + SEP + sourceKey;
  }

  const toggleSourceImage = (sourceImageKey: string, event?: React.MouseEvent | React.KeyboardEvent) => {
    setCollapsedSourceImageKeys((prev) => {
      const next = new Set(prev);
      if (event?.altKey) {
        for (const g of imageGroups) if (g.sourceImageKey !== sourceImageKey) next.add(g.sourceImageKey);
      }
      if (next.has(sourceImageKey)) next.delete(sourceImageKey);
      else next.add(sourceImageKey);
      return next;
    });
  };

  const toggleSource = (compositeKey: string, sameImageVideoKeys: string[], event?: React.MouseEvent | React.KeyboardEvent) => {
    setCollapsedSourceKeys((prev) => {
      const next = new Set(prev);
      if (event?.altKey) {
        for (const k of sameImageVideoKeys) if (k !== compositeKey) next.add(k);
      }
      if (next.has(compositeKey)) next.delete(compositeKey);
      else next.add(compositeKey);
      return next;
    });
  };

  return (
    <div className="facet">
      <div className="facet-header">
        <button type="button" className="facet-toggle" onClick={onToggleOpen} aria-expanded={open} aria-controls="experiment-list-body">
          <span className={`facet-caret ${open ? "open" : ""}`} aria-hidden="true">
            ▸
          </span>
          <span className="facet-title">Experiments</span>
        </button>
        <div className="facet-meta">
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            <span className="mono">{experiments.length}</span> experiments
          </span>
        </div>
        <div className="facet-actions">
          <button type="button" className="icon-btn" onClick={onRefresh} title="Refresh experiments" aria-label="Refresh experiments">
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
              <path d="M20 12a8 8 0 1 1-2.34-5.66" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              <path d="M20 4v6h-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
      </div>
      <div id="experiment-list-body" className={`facet-body ${open ? "open" : ""}`}>
        <div className="facet-body-inner">
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <input
              type="text"
              value={filterValue}
              onChange={(e) => onFilterChange(e.target.value)}
              placeholder="Filter experiments…"
              style={{ padding: "6px 8px", fontSize: 12 }}
              aria-label="Filter experiments"
            />
            <div className="experiment-tree" style={{ overflowY: "auto", minHeight: 120, maxHeight: "50vh" }}>
              {imageGroups.length === 0 ? (
                <div style={{ color: "var(--muted)", fontSize: 12, padding: 8 }}>No experiments match the filter.</div>
              ) : (
                <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                  {imageGroups.map(({ sourceImageKey, sourceImageLabel, videos }) => {
                    const isImageExpanded = !collapsedSourceImageKeys.has(sourceImageKey);
                    const totalExpsInImage = videos.reduce((s, v) => s + v.experiments.length, 0);
                    const totalRunsInImage = videos.reduce((s, v) => s + v.experiments.reduce((t, e) => t + (e.run_counts?.total ?? 0), 0), 0);
                    const hasARunInImage = videos.some((v) => v.experiments.some((exp) => selectedRunKeys[0]?.startsWith(exp.exp_id + "::") ?? false));
                    const hasBRunInImage = videos.some((v) => v.experiments.some((exp) => selectedRunKeys[1]?.startsWith(exp.exp_id + "::") ?? false));
                    return (
                    <li key={sourceImageKey} style={{ marginBottom: 8 }}>
                      <div
                        role="button"
                        tabIndex={0}
                        onClick={(e) => toggleSourceImage(sourceImageKey, e)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            toggleSourceImage(sourceImageKey, e);
                          }
                        }}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          fontSize: 11,
                          fontWeight: 600,
                          color: "var(--muted)",
                          padding: "4px 6px 2px",
                          borderBottom: "1px solid var(--border)",
                          marginBottom: 4,
                          cursor: "pointer",
                        }}
                        title={
                          sourceImageKey === UNKNOWN_SOURCE_IMAGE_KEY
                            ? "Source image (grouping). Option+click: collapse all other source images"
                            : `${sourceImageKey} — Option+click: collapse all other source images`
                        }
                        aria-expanded={isImageExpanded}
                      >
                        <span style={{ display: "inline-block", transform: isImageExpanded ? "rotate(90deg)" : "none", transition: "transform 0.15s", flexShrink: 0 }}>
                          ▸
                        </span>
                        {(hasARunInImage || hasBRunInImage) && (
                          <span style={{ display: "flex", gap: 2, flexShrink: 0 }}>
                            {hasARunInImage && (
                              <span
                                className="mono"
                                style={{ fontSize: 9, fontWeight: 700, background: "var(--accent)", color: "var(--bg)", padding: "1px 3px", borderRadius: 2 }}
                                title="This source image has the A view run"
                              >
                                A
                              </span>
                            )}
                            {hasBRunInImage && (
                              <span
                                className="mono"
                                style={{ fontSize: 9, fontWeight: 700, background: "var(--accent)", color: "var(--bg)", padding: "1px 3px", borderRadius: 2 }}
                                title="This source image has the B view run"
                              >
                                B
                              </span>
                            )}
                          </span>
                        )}
                        <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{sourceImageLabel}</span>
                        <span style={{ fontWeight: 400, flexShrink: 0 }}>
                          <span className="mono">{videos.length}</span> video{videos.length !== 1 ? "s" : ""} · <span className="mono">{totalExpsInImage}</span> exp · <span className="mono">{totalRunsInImage}</span> run{totalRunsInImage !== 1 ? "s" : ""}
                        </span>
                      </div>
                      {isImageExpanded && (
                      <ul style={{ listStyle: "none", margin: 0, padding: 0, marginLeft: 8 }}>
                        {videos.map(({ sourceKey, displayLabel, experiments: groupExps }) => {
                          const compositeKey = videoRowKey(sourceImageKey, sourceKey);
                          const isSourceExpanded = !collapsedSourceKeys.has(compositeKey);
                          const sameImageVideoKeys = videos.map((v) => videoRowKey(sourceImageKey, v.sourceKey));
                          const totalRuns = groupExps.reduce((sum, e) => sum + (e.run_counts?.total ?? 0), 0);
                          const hasARunInSource = groupExps.some((exp) => selectedRunKeys[0]?.startsWith(exp.exp_id + "::") ?? false);
                          const hasBRunInSource = groupExps.some((exp) => selectedRunKeys[1]?.startsWith(exp.exp_id + "::") ?? false);
                          return (
                          <li key={compositeKey} style={{ marginBottom: 6 }}>
                            <div
                              role="button"
                              tabIndex={0}
                              onClick={(e) => toggleSource(compositeKey, sameImageVideoKeys, e)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault();
                                  toggleSource(compositeKey, sameImageVideoKeys, e);
                                }
                              }}
                              style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 6,
                                fontSize: 11,
                                fontWeight: 600,
                                color: "var(--muted)",
                                padding: "2px 6px 2px",
                                borderBottom: "1px solid var(--border)",
                                marginBottom: 2,
                                cursor: "pointer",
                              }}
                              title={
                                sourceKey === UNKNOWN_SOURCE_KEY
                                  ? "Source video. Option+click: collapse all other videos under this source image"
                                  : `${sourceKey} — Option+click: collapse all other videos here`
                              }
                              aria-expanded={isSourceExpanded}
                            >
                              <span style={{ display: "inline-block", transform: isSourceExpanded ? "rotate(90deg)" : "none", transition: "transform 0.15s", flexShrink: 0 }}>
                                ▸
                              </span>
                              {(hasARunInSource || hasBRunInSource) && (
                                <span style={{ display: "flex", gap: 2, flexShrink: 0 }}>
                                  {hasARunInSource && (
                                    <span
                                      className="mono"
                                      style={{ fontSize: 9, fontWeight: 700, background: "var(--accent)", color: "var(--bg)", padding: "1px 3px", borderRadius: 2 }}
                                      title="This source has the A view run"
                                    >
                                      A
                                    </span>
                                  )}
                                  {hasBRunInSource && (
                                    <span
                                      className="mono"
                                      style={{ fontSize: 9, fontWeight: 700, background: "var(--accent)", color: "var(--bg)", padding: "1px 3px", borderRadius: 2 }}
                                      title="This source has the B view run"
                                    >
                                      B
                                    </span>
                                  )}
                                </span>
                              )}
                              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{displayLabel}</span>
                              <span style={{ fontWeight: 400, flexShrink: 0 }}>
                                <span className="mono">{groupExps.length}</span> exp · <span className="mono">{totalRuns}</span> run{totalRuns !== 1 ? "s" : ""}
                              </span>
                            </div>
                            {isSourceExpanded && (
                            <ul style={{ listStyle: "none", margin: 0, padding: 0, marginLeft: 8 }}>
                              {groupExps.map((exp) => {
                          const isExpanded = expandedExpIds.includes(exp.exp_id);
                          const entry = runsByExpId[exp.exp_id];
                          const runs = entry?.runs ?? [];
                          const isLoading = loadingRunsExpId === exp.exp_id;
                          const isSelected = selectedExpId === exp.exp_id;
                          const total = exp.run_counts?.total ?? 0;
                          const prefix = exp.exp_id + "::";
                          const hasARun = selectedRunKeys[0]?.startsWith(prefix) ?? false;
                          const hasBRun = selectedRunKeys[1]?.startsWith(prefix) ?? false;
                          return (
                            <li key={exp.exp_id} style={{ marginBottom: 2 }}>
                              <div
                                className="axis-item"
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: 6,
                                  padding: "4px 6px",
                                  cursor: "pointer",
                                  background: isSelected ? "var(--bg-2)" : undefined,
                                  borderRadius: 4,
                                }}
                              >
                                <button
                                  type="button"
                                  className="icon-btn"
                                  style={{ flexShrink: 0 }}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    onToggleExpand(exp.exp_id);
                                    if (!entry && !isLoading) onLoadRuns(exp.exp_id);
                                  }}
                                  aria-expanded={isExpanded}
                                  title={isExpanded ? "Collapse" : "Expand runs"}
                                  disabled={isLoading}
                                >
                                  <span style={{ display: "inline-block", transform: isExpanded ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}>
                                    ▸
                                  </span>
                                </button>
                                {(hasARun || hasBRun) && (
                                  <span style={{ display: "flex", gap: 2, flexShrink: 0 }}>
                                    {hasARun && (
                                      <span
                                        className="mono"
                                        style={{
                                          fontSize: 10,
                                          fontWeight: 700,
                                          background: "var(--accent)",
                                          color: "var(--bg)",
                                          padding: "2px 4px",
                                          borderRadius: 3,
                                        }}
                                        title="This experiment has the A view run"
                                      >
                                        A
                                      </span>
                                    )}
                                    {hasBRun && (
                                      <span
                                        className="mono"
                                        style={{
                                          fontSize: 10,
                                          fontWeight: 700,
                                          background: "var(--accent)",
                                          color: "var(--bg)",
                                          padding: "2px 4px",
                                          borderRadius: 3,
                                        }}
                                        title="This experiment has the B view run"
                                      >
                                        B
                                      </span>
                                    )}
                                  </span>
                                )}
                                <button
                                  type="button"
                                  style={{ flex: 1, textAlign: "left", background: "none", border: "none", padding: 0, cursor: "pointer", font: "inherit", color: "inherit", minWidth: 0 }}
                                  onClick={() => onSelectExperiment(exp.exp_id)}
                                >
                                  <code className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>
                                    {exp.exp_id}
                                  </code>
                                  {total > 0 && (
                                    <span style={{ marginLeft: 6, color: "var(--muted)", fontSize: 11 }}>
                                      {total} run{total !== 1 ? "s" : ""}
                                    </span>
                                  )}
                                </button>
                              </div>
                              {isExpanded && (
                                <div style={{ marginLeft: 20, marginTop: 4, marginBottom: 8, paddingLeft: 8, borderLeft: "2px solid var(--border)" }}>
                                  {isLoading ? (
                                    <div style={{ color: "var(--muted)", fontSize: 12, padding: "4px 0" }}>Loading runs…</div>
                                  ) : runs.length === 0 ? (
                                    <div style={{ color: "var(--muted)", fontSize: 12, padding: "4px 0" }}>No runs.</div>
                                  ) : (
                                    <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                                      {runs.map((r) => {
                                        const key = runKey(r);
                                        const displayStatus = getRunDisplayStatus(r, queue);
                                        const isA = selectedRunKeys[0] === key;
                                        const isB = selectedRunKeys[1] === key;
                                        const canSelect = r.status === "complete";
                                        return (
                                          <li key={key} style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 0", fontSize: 12 }}>
                                            <span style={{ display: "flex", gap: 2, flexShrink: 0 }} role="radiogroup" aria-label={`Set run ${r.run_id} as A or B view`}>
                                              <button
                                                type="button"
                                                role="radio"
                                                aria-checked={isA}
                                                className={`icon-btn slot-btn ${isA ? "slot-btn-selected" : ""}`}
                                                style={{ fontSize: 10, minWidth: 24 }}
                                                onClick={(e) => {
                                                  e.stopPropagation();
                                                  if (canSelect) onSetSlot("A", r);
                                                }}
                                                disabled={!canSelect}
                                                title={canSelect ? "Set as A view" : "Only finished runs can be set as A"}
                                                aria-label={`Set run ${r.run_id} as A`}
                                              >
                                                A
                                              </button>
                                              <button
                                                type="button"
                                                role="radio"
                                                aria-checked={isB}
                                                className={`icon-btn slot-btn ${isB ? "slot-btn-selected" : ""}`}
                                                style={{ fontSize: 10, minWidth: 24 }}
                                                onClick={(e) => {
                                                  e.stopPropagation();
                                                  if (canSelect) onSetSlot("B", r);
                                                }}
                                                disabled={!canSelect}
                                                title={canSelect ? "Set as B view" : "Only finished runs can be set as B"}
                                                aria-label={`Set run ${r.run_id} as B`}
                                              >
                                                B
                                              </button>
                                            </span>
                                            <code className="mono" style={{ flex: "0 0 auto", minWidth: 60 }}>
                                              {r.run_id}
                                            </code>
                                            <span style={BADGE_STYLE[displayStatus]} title={RUN_DISPLAY_LABELS[displayStatus]}>
                                              {RUN_DISPLAY_LABELS[displayStatus]}
                                            </span>
                                          </li>
                                        );
                                      })}
                                    </ul>
                                  )}
                                </div>
                              )}
                            </li>
                          );
                        })}
                            </ul>
                            )}
                          </li>
                          );
                        })}
                      </ul>
                      )}
                    </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
