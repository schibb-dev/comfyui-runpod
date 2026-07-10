import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchDiscoveryLibrary, fetchDiscoveryWorkflowFacets } from "./api";
import { DiscoveryWorkflowFacetsPanel } from "./DiscoveryWorkflowFacetsPanel";
import type { DiscoveryLibraryItem, DiscoveryWorkflowFacetsResponse } from "./types";
import { MediaAssetCard } from "./MediaAssetCard";

type ExplorerMode = "workflow" | "video";

type FpGroup = {
  fingerprint: string;
  count: number;
  items: DiscoveryLibraryItem[];
};

function normFp(it: DiscoveryLibraryItem): string | null {
  const fp = it.workflow_fingerprint?.trim();
  return fp ? fp : null;
}

function isVideoPath(p: string): boolean {
  return /\.(mp4|webm|mov|mkv)$/i.test(String(p || ""));
}

/** Match Discovery `discoveryPlayUrl`: prefer `video_url`, else main `url` when relpath looks like video. */
function playUrlForItem(it: DiscoveryLibraryItem): string | null {
  if (it.video_url) return it.video_url;
  if (isVideoPath(it.relpath)) return it.url;
  return null;
}

function WxLinkerVideoViewer({ it, label }: { it: DiscoveryLibraryItem | null; label?: string }) {
  if (!it) return null;
  const src = playUrlForItem(it);
  const poster = it.thumb_url ?? undefined;
  return (
    <div className="wx-linker-video-player-wrap">
      {label ? <div className="wx-linker-video-player-label">{label}</div> : null}
      <div className="wx-linker-video-player">
        {src ? (
          <video key={it.relpath} className="wx-linker-video-el" src={src} poster={poster} controls playsInline preload="metadata" />
        ) : poster ? (
          <img className="wx-linker-video-poster-fallback" src={poster} alt="" decoding="async" />
        ) : (
          <div className="wx-linker-video-player-fallback">No preview URL for this item.</div>
        )}
      </div>
      {!src && it.video_relpath ? (
        <div className="wx-linker-muted wx-linker-video-player-hint">Indexed video path: {it.video_relpath}</div>
      ) : null}
    </div>
  );
}

export function WorkflowVideoLinker() {
  const [explorerMode, setExplorerMode] = useState<ExplorerMode>("workflow");
  const [items, setItems] = useState<DiscoveryLibraryItem[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [itemCountTotal, setItemCountTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [fpFilter, setFpFilter] = useState("");
  const [videoFilter, setVideoFilter] = useState("");
  const [selectedFp, setSelectedFp] = useState<string | null>(null);
  const [workflowPreviewRelpath, setWorkflowPreviewRelpath] = useState<string | null>(null);
  const [selectedVideo, setSelectedVideo] = useState<DiscoveryLibraryItem | null>(null);
  const [facetsProbe, setFacetsProbe] = useState<{ rel: string; body: DiscoveryWorkflowFacetsResponse } | null>(null);
  const [facetsLoading, setFacetsLoading] = useState(false);
  const [facetsError, setFacetsError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetchDiscoveryLibrary({ limit: 8000, library: "all" });
      setItems(res.items);
      setTruncated(Boolean(res.truncated));
      setItemCountTotal(res.item_count_total ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setFacetsProbe(null);
    setFacetsError("");
  }, [selectedVideo?.relpath, explorerMode, workflowPreviewRelpath]);

  const runWorkflowFacetsProbe = useCallback(async (rel: string) => {
    setFacetsLoading(true);
    setFacetsError("");
    try {
      const body = await fetchDiscoveryWorkflowFacets(rel);
      setFacetsProbe({ rel, body });
    } catch (e) {
      setFacetsError(e instanceof Error ? e.message : String(e));
    } finally {
      setFacetsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const fpGroups = useMemo(() => {
    const m = new Map<string, DiscoveryLibraryItem[]>();
    for (const it of items) {
      const fp = normFp(it);
      if (!fp) continue;
      const arr = m.get(fp);
      if (arr) arr.push(it);
      else m.set(fp, [it]);
    }
    const out: FpGroup[] = [];
    for (const [fingerprint, list] of m) {
      out.push({ fingerprint, count: list.length, items: list });
    }
    out.sort((a, b) => b.count - a.count || a.fingerprint.localeCompare(b.fingerprint));
    return out;
  }, [items]);

  const noFingerprintCount = useMemo(
    () => items.reduce((n, it) => n + (normFp(it) ? 0 : 1), 0),
    [items],
  );

  const filteredFpGroups = useMemo(() => {
    const q = fpFilter.trim().toLowerCase();
    if (!q) return fpGroups;
    return fpGroups.filter(
      (g) =>
        g.fingerprint.toLowerCase().includes(q) ||
        g.items.some((it) => it.name.toLowerCase().includes(q) || it.relpath.toLowerCase().includes(q)),
    );
  }, [fpGroups, fpFilter]);

  const selectedGroup = useMemo(() => {
    if (!selectedFp) return null;
    return fpGroups.find((g) => g.fingerprint === selectedFp) ?? null;
  }, [fpGroups, selectedFp]);

  const videoItemsSorted = useMemo(() => [...items].sort((a, b) => b.mtime - a.mtime), [items]);

  const filteredVideoItems = useMemo(() => {
    const q = videoFilter.trim().toLowerCase();
    if (!q) return videoItemsSorted;
    return videoItemsSorted.filter(
      (it) => it.name.toLowerCase().includes(q) || it.relpath.toLowerCase().includes(q),
    );
  }, [videoItemsSorted, videoFilter]);

  const relatedForSelectedVideo = useMemo(() => {
    if (!selectedVideo) return [];
    const fp = normFp(selectedVideo);
    if (!fp) return [];
    return items.filter((it) => normFp(it) === fp && it.relpath !== selectedVideo.relpath);
  }, [selectedVideo, items]);

  const jumpToFingerprint = (fp: string, previewRelpath?: string | null) => {
    setExplorerMode("workflow");
    setSelectedFp(fp);
    setFpFilter("");
    if (previewRelpath) setWorkflowPreviewRelpath(previewRelpath);
  };

  useEffect(() => {
    if (explorerMode === "workflow" && selectedFp && !fpGroups.some((g) => g.fingerprint === selectedFp)) {
      setSelectedFp(null);
    }
  }, [explorerMode, selectedFp, fpGroups]);

  const workflowPreviewItem = useMemo(() => {
    if (!selectedGroup) return null;
    if (workflowPreviewRelpath) {
      const hit = selectedGroup.items.find((x) => x.relpath === workflowPreviewRelpath);
      if (hit) return hit;
    }
    return selectedGroup.items[0] ?? null;
  }, [selectedGroup, workflowPreviewRelpath]);

  useEffect(() => {
    if (!selectedGroup) {
      setWorkflowPreviewRelpath(null);
      return;
    }
    setWorkflowPreviewRelpath((prev) => {
      if (prev && selectedGroup.items.some((x) => x.relpath === prev)) return prev;
      return selectedGroup.items[0]?.relpath ?? null;
    });
  }, [selectedGroup]);

  return (
    <div className="wx-linker">
      <div className="wx-linker-toolbar">
        <div className="wx-linker-toolbar__meta">
          <span className="wx-linker-pill">
            {loading ? "Loading index…" : `${items.length} items loaded`}
            {itemCountTotal != null && itemCountTotal !== items.length ? ` · index total ${itemCountTotal}` : null}
            {truncated ? " · truncated to limit" : null}
          </span>
          {noFingerprintCount > 0 ? (
            <span className="wx-linker-pill wx-linker-pill--warn">{noFingerprintCount} without fingerprint</span>
          ) : null}
        </div>
        <button type="button" className="wx-linker-refresh" disabled={loading} onClick={() => void load()}>
          {loading ? "Refreshing…" : "Reload discovery"}
        </button>
      </div>

      {error ? <div className="factory-error">{error}</div> : null}

      <div className="wx-linker-intro">
        <strong>Two entry points, same graph.</strong> Mode 1 clusters by <code>workflow_fingerprint</code> from the
        Discovery index; mode 2 starts from a video and shows the fingerprint plus siblings. This is a layout spike —
        neighbors can later include stem families, time windows, and factory workflows.
      </div>

      <div className="wx-linker-modes" role="tablist" aria-label="Workflow or video first">
        <button
          type="button"
          role="tab"
          aria-selected={explorerMode === "workflow"}
          className={`wx-linker-mode${explorerMode === "workflow" ? " wx-linker-mode--active" : ""}`}
          onClick={() => setExplorerMode("workflow")}
        >
          <span className="wx-linker-mode__n">1</span>
          From workflow fingerprint
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={explorerMode === "video"}
          className={`wx-linker-mode${explorerMode === "video" ? " wx-linker-mode--active" : ""}`}
          onClick={() => setExplorerMode("video")}
        >
          <span className="wx-linker-mode__n">2</span>
          From video
        </button>
      </div>

      {explorerMode === "workflow" ? (
        <div className="wx-linker-split">
          <div className="wx-linker-pane wx-linker-pane--list">
            <label className="wx-linker-field">
              <span className="wx-linker-field__label">Search fingerprints or paths</span>
              <input
                type="search"
                value={fpFilter}
                onChange={(e) => setFpFilter(e.target.value)}
                placeholder="Substring on fingerprint, filename, or relpath"
                autoComplete="off"
              />
            </label>
            <div className="wx-linker-list" role="listbox" aria-label="Workflow fingerprints">
              {filteredFpGroups.map((g) => (
                <button
                  key={g.fingerprint}
                  type="button"
                  role="option"
                  aria-selected={selectedFp === g.fingerprint}
                  className={`wx-linker-row${selectedFp === g.fingerprint ? " wx-linker-row--active" : ""}`}
                  onClick={() => setSelectedFp(g.fingerprint)}
                >
                  <span className="wx-linker-row__title">{g.items[0]?.name || "Unnamed"}</span>
                  <span className="wx-linker-row__count">{g.count} video{g.count === 1 ? "" : "s"}</span>
                  <code className="wx-linker-row__fp">{g.fingerprint}</code>
                </button>
              ))}
              {!filteredFpGroups.length && !loading ? (
                <div className="wx-linker-empty">No fingerprint groups match this filter.</div>
              ) : null}
            </div>
          </div>
          <div className="wx-linker-pane wx-linker-pane--detail">
            {!selectedGroup ? (
              <div className="wx-linker-empty">Select a fingerprint to list every indexed output that shares it.</div>
            ) : (
              <>
                <div className="wx-linker-detail-head">
                  <div>
                    <div className="wx-linker-detail-title">{selectedGroup.items[0]?.name || "Cluster"}</div>
                    <code className="wx-linker-detail-fp">{selectedGroup.fingerprint}</code>
                  </div>
                  <div className="wx-linker-detail-head-actions">
                    <button
                      type="button"
                      className="wx-linker-ghost"
                      onClick={() => {
                        const v = workflowPreviewItem || selectedGroup.items[0];
                        if (v) {
                          setSelectedVideo(v);
                          setExplorerMode("video");
                        }
                      }}
                    >
                      Open mode 2 with preview
                    </button>
                  </div>
                </div>
                <WxLinkerVideoViewer it={workflowPreviewItem} label="Preview" />
                <div className="wx-linker-section">
                  <div className="wx-linker-section__label">Workflow metadata</div>
                  <DiscoveryWorkflowFacetsPanel
                    relpath={workflowPreviewItem?.relpath || ""}
                    data={facetsProbe?.body ?? null}
                    probedRelpath={facetsProbe?.rel}
                    loading={facetsLoading}
                    error={facetsError}
                    onLoad={() => workflowPreviewItem && void runWorkflowFacetsProbe(workflowPreviewItem.relpath)}
                    loadDisabled={!workflowPreviewItem}
                    intro="PNG text chunks + optional MP4 tags, split into graph / sources / LoRA / litegraph hashes."
                  />
                </div>
                <div className="wx-linker-card-grid">
                  {selectedGroup.items.map((it) => (
                    <MediaAssetCard
                      key={it.relpath}
                      name={it.name}
                      path={it.relpath}
                      mediaType="video"
                      thumbUrl={it.thumb_url ?? null}
                      videoUrl={it.video_url ?? null}
                      showVideoThumb
                      badge={it.library}
                      className={workflowPreviewItem?.relpath === it.relpath ? "wx-linker-card--previewing" : ""}
                      detail={
                        it.class_types_preview?.length
                          ? it.class_types_preview.slice(0, 4).join(" · ")
                          : undefined
                      }
                      onClick={() => setWorkflowPreviewRelpath(it.relpath)}
                    />
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      ) : (
        <div className="wx-linker-split">
          <div className="wx-linker-pane wx-linker-pane--list">
            <label className="wx-linker-field">
              <span className="wx-linker-field__label">Search videos</span>
              <input
                type="search"
                value={videoFilter}
                onChange={(e) => setVideoFilter(e.target.value)}
                placeholder="Filename or relpath"
                autoComplete="off"
              />
            </label>
            <div className="wx-linker-list" role="listbox" aria-label="Discovery videos">
              {filteredVideoItems.map((it) => (
                <button
                  key={it.relpath}
                  type="button"
                  role="option"
                  aria-selected={selectedVideo?.relpath === it.relpath}
                  className={`wx-linker-vrow${selectedVideo?.relpath === it.relpath ? " wx-linker-vrow--active" : ""}`}
                  onClick={() => setSelectedVideo(it)}
                >
                  <span className="wx-linker-vrow__thumb" aria-hidden>
                    {it.thumb_url ? <img src={it.thumb_url} alt="" loading="lazy" decoding="async" /> : null}
                  </span>
                  <span className="wx-linker-vrow__body">
                    <span className="wx-linker-vrow__name">{it.name}</span>
                    <span className="wx-linker-vrow__lib">{it.library}</span>
                  </span>
                </button>
              ))}
              {!filteredVideoItems.length && !loading ? (
                <div className="wx-linker-empty">No videos match this filter.</div>
              ) : null}
            </div>
          </div>
          <div className="wx-linker-pane wx-linker-pane--detail">
            {!selectedVideo ? (
              <div className="wx-linker-empty">Pick a video to inspect its workflow fingerprint and related outputs.</div>
            ) : (
              <>
                <WxLinkerVideoViewer it={selectedVideo} label="Video" />
                <div className="wx-linker-video-meta-block">
                  <div className="wx-linker-detail-title">{selectedVideo.name}</div>
                  <div className="wx-linker-muted">{selectedVideo.relpath}</div>
                  <div className="wx-linker-chip-row">
                    <span className="wx-linker-chip">{selectedVideo.library}</span>
                    {selectedVideo.has_embedded_prompt ? (
                      <span className="wx-linker-chip wx-linker-chip--good">embedded prompt</span>
                    ) : (
                      <span className="wx-linker-chip">no embedded prompt flag</span>
                    )}
                  </div>
                </div>

                <div className="wx-linker-section">
                  <div className="wx-linker-section__label">Workflow fingerprint</div>
                  {normFp(selectedVideo) ? (
                    <>
                      <code className="wx-linker-detail-fp">{normFp(selectedVideo)}</code>
                      <div className="wx-linker-actions">
                        <button
                          type="button"
                          className="wx-linker-primary"
                          onClick={() => jumpToFingerprint(normFp(selectedVideo)!, selectedVideo.relpath)}
                        >
                          Show all videos with this fingerprint (mode 1)
                        </button>
                        <a className="wx-linker-ghostlink" href="/discovery">
                          Open full Discovery library
                        </a>
                      </div>
                    </>
                  ) : (
                    <div className="wx-linker-muted">No fingerprint on this item — indexer could not derive one.</div>
                  )}
                </div>

                {selectedVideo.class_types_preview?.length ? (
                  <div className="wx-linker-section">
                    <div className="wx-linker-section__label">Class types (preview)</div>
                    <div className="wx-linker-chips">
                      {selectedVideo.class_types_preview.map((c) => (
                        <span key={c} className="wx-linker-chip">
                          {c}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : null}

                <div className="wx-linker-section">
                  <div className="wx-linker-section__label">Workflow metadata</div>
                  <DiscoveryWorkflowFacetsPanel
                    relpath={selectedVideo.relpath}
                    data={facetsProbe?.body ?? null}
                    probedRelpath={facetsProbe?.rel}
                    loading={facetsLoading}
                    error={facetsError}
                    onLoad={() => void runWorkflowFacetsProbe(selectedVideo.relpath)}
                    loadDisabled={!selectedVideo}
                    intro="Same payload as Discovery: merged stem group, PNG chunks, MP4 tags, facet hashes for similarity work."
                  />
                </div>

                <div className="wx-linker-section">
                  <div className="wx-linker-section__label">Same fingerprint ({relatedForSelectedVideo.length})</div>
                  {relatedForSelectedVideo.length ? (
                    <div className="wx-linker-card-grid wx-linker-card-grid--compact">
                      {relatedForSelectedVideo.map((it) => (
                        <MediaAssetCard
                          key={it.relpath}
                          name={it.name}
                          path={it.relpath}
                          mediaType="video"
                          thumbUrl={it.thumb_url ?? null}
                          videoUrl={it.video_url ?? null}
                          showVideoThumb
                          badge={it.library}
                          onClick={() => setSelectedVideo(it)}
                        />
                      ))}
                    </div>
                  ) : normFp(selectedVideo) ? (
                    <div className="wx-linker-muted">No other indexed outputs share this fingerprint.</div>
                  ) : (
                    <div className="wx-linker-muted">—</div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
