import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchDiscoveryLibraryItem,
  listShapeFactoryClipsLibrary,
  type ShapeFactoryClip,
} from "./api";
import { formatClipTimecode } from "./ClipBookmarksRail";
import { DiscoveryQueueFromClip } from "./DiscoveryQueueFromClip";
import { discoveryLibraryHref, parseClipsDeepLink } from "./discoveryDeepLink";
import { cachedEnsureThumbUrl, enqueueEnsureThumb } from "./ensureThumbQueue";
import { PageHeader } from "./PageHeader";
import type { DiscoveryLibraryItem } from "./types";
import { useTrimPlaybackEnforcement } from "./useTrimPlayback";
import { VideoTrimControls, type VideoTrimPlaybackMode } from "./VideoTrimControls";

const PAGE_SIZE = 80;
const CLIPS_DETAIL_LAYOUT_KEY = "clips_library_detail_layout_v1";

type ClipsDetailLayout = "stacked" | "split";

function loadClipsDetailLayout(): ClipsDetailLayout {
  try {
    const v = localStorage.getItem(CLIPS_DETAIL_LAYOUT_KEY);
    if (v === "split" || v === "stacked") return v;
  } catch {
    /* ignore */
  }
  return "stacked";
}

function persistClipsDetailLayout(layout: ClipsDetailLayout) {
  try {
    localStorage.setItem(CLIPS_DETAIL_LAYOUT_KEY, layout);
  } catch {
    /* ignore */
  }
}

function fileUrlFromRel(relpath: string): string {
  return "/files/" + encodeURIComponent(relpath.replace(/\\/g, "/"));
}

function originLabel(origin: string | null | undefined): string {
  const o = String(origin || "").trim();
  if (!o) return "unknown";
  if (o === "workflow_import") return "workflow";
  if (o === "png_embed_import") return "png";
  if (o === "trims_backfill") return "trims";
  return o;
}

function stubLibraryItem(relpath: string): DiscoveryLibraryItem {
  const name = relpath.split("/").pop() || relpath;
  return {
    relpath,
    library: "clips",
    name,
    mtime: 0,
    size: 0,
    sha256: "",
    url: fileUrlFromRel(relpath),
    video_relpath: relpath,
    video_url: fileUrlFromRel(relpath),
  };
}

function ClipThumb({ relpath, markIn }: { relpath: string | null | undefined; markIn: number }) {
  const [url, setUrl] = useState<string | null>(() =>
    relpath ? cachedEnsureThumbUrl(relpath) ?? null : null,
  );
  useEffect(() => {
    if (!relpath) {
      setUrl(null);
      return;
    }
    const cached = cachedEnsureThumbUrl(relpath);
    if (cached !== undefined) {
      setUrl(cached);
      return;
    }
    let cancelled = false;
    void enqueueEnsureThumb(relpath)
      .then(() => {
        if (!cancelled) setUrl(cachedEnsureThumbUrl(relpath) ?? null);
      })
      .catch(() => {
        if (!cancelled) setUrl(null);
      });
    return () => {
      cancelled = true;
    };
  }, [relpath]);

  if (url) {
    return <img className="clips-lib-card__thumb-img" src={url} alt="" loading="lazy" />;
  }
  return (
    <div className="clips-lib-card__thumb-fallback" title={`in ${formatClipTimecode(markIn)}`}>
      {formatClipTimecode(markIn)}
    </div>
  );
}

export function ClipsLibraryApp() {
  const deep = useMemo(() => parseClipsDeepLink(), []);
  const [qDraft, setQDraft] = useState(deep.q || "");
  const [q, setQ] = useState(deep.q || "");
  const [origin, setOrigin] = useState<string>(deep.origin || "");
  const [defaultsOnly, setDefaultsOnly] = useState(false);
  const [clips, setClips] = useState<ShapeFactoryClip[]>([]);
  const [total, setTotal] = useState(0);
  const [originCounts, setOriginCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(deep.clipId);
  const [libraryItem, setLibraryItem] = useState<DiscoveryLibraryItem | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [videoDuration, setVideoDuration] = useState(0);
  const [videoTime, setVideoTime] = useState(0);
  const [trimMode, setTrimMode] = useState<VideoTrimPlaybackMode>("repeat");
  const [detailLayout, setDetailLayout] = useState<ClipsDetailLayout>(() => loadClipsDetailLayout());

  const setDetailLayoutFromUser = useCallback((layout: ClipsDetailLayout) => {
    setDetailLayout(layout);
    persistClipsDetailLayout(layout);
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listShapeFactoryClipsLibrary({
        limit: PAGE_SIZE,
        offset: 0,
        origin: origin || null,
        q: q || null,
        defaultsOnly,
      });
      const rows = res.clips || [];
      setClips(rows);
      setTotal(res.total ?? rows.length);
      setOriginCounts(res.origin_counts || {});
      setSelectedId((prev) => {
        if (prev && rows.some((c) => c.clip_id === prev)) return prev;
        return rows[0]?.clip_id || null;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setClips([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [origin, q, defaultsOnly]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const selected = useMemo(
    () => clips.find((c) => c.clip_id === selectedId) || null,
    [clips, selectedId],
  );

  const mediaRelpath = selected?.media_relpath || null;
  const markIn = selected != null ? selected.mark_in_s : null;
  const markOut = selected != null ? selected.mark_out_s : null;

  useEffect(() => {
    if (!mediaRelpath) {
      setLibraryItem(null);
      return;
    }
    let cancelled = false;
    setLibraryItem(stubLibraryItem(mediaRelpath));
    void fetchDiscoveryLibraryItem({ relpath: mediaRelpath })
      .then((res) => {
        if (cancelled || !res.item) return;
        setLibraryItem(res.item);
      })
      .catch(() => {
        /* stub is enough for queue */
      });
    return () => {
      cancelled = true;
    };
  }, [mediaRelpath]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v || markIn == null) return;
    const seek = () => {
      try {
        v.currentTime = markIn;
      } catch {
        /* ignore */
      }
    };
    if (v.readyState >= 1) seek();
    else v.addEventListener("loadedmetadata", seek, { once: true });
  }, [selectedId, markIn, mediaRelpath]);

  useTrimPlaybackEnforcement(videoRef, {
    mediaKey: selectedId || "",
    markIn,
    markOut,
    mode: trimMode,
    enabled: Boolean(selected && mediaRelpath),
  });

  useEffect(() => {
    if (!selectedId) return;
    const sp = new URLSearchParams(window.location.search);
    sp.set("clip_id", selectedId);
    if (q) sp.set("q", q);
    else sp.delete("q");
    if (origin) sp.set("origin", origin);
    else sp.delete("origin");
    const next = `${window.location.pathname}?${sp.toString()}`;
    window.history.replaceState(null, "", next);
  }, [selectedId, q, origin]);

  const originOptions = useMemo(() => {
    const keys = Object.keys(originCounts).sort((a, b) => (originCounts[b] || 0) - (originCounts[a] || 0));
    return keys;
  }, [originCounts]);

  const queueItem = libraryItem || (mediaRelpath ? stubLibraryItem(mediaRelpath) : null);

  return (
    <div className="discovery-screen clips-lib-screen">
      <div className="panel discovery-panel clips-lib-root">
        <PageHeader
          title="Clips"
          subtitle="Browse clip bookmarks across parent videos — preview the window, open the parent, or queue from the selected clip."
          actions={
            <div className="clips-lib-header-actions">
              <form
                className="clips-lib-search"
                onSubmit={(e) => {
                  e.preventDefault();
                  setQ(qDraft.trim());
                }}
              >
                <input
                  type="search"
                  className="clips-lib-search__input"
                  placeholder="Search label, path, notes…"
                  value={qDraft}
                  onChange={(e) => setQDraft(e.target.value)}
                  aria-label="Search clips"
                />
                <button type="submit" className="drt-btn">
                  Search
                </button>
              </form>
              <label className="clips-lib-filter">
                Origin
                <select value={origin} onChange={(e) => setOrigin(e.target.value)} aria-label="Filter by origin">
                  <option value="">All</option>
                  {originOptions.map((o) => (
                    <option key={o} value={o}>
                      {originLabel(o === "(none)" ? "" : o)} ({originCounts[o]})
                    </option>
                  ))}
                </select>
              </label>
              <label className="clips-lib-defaults">
                <input
                  type="checkbox"
                  checked={defaultsOnly}
                  onChange={(e) => setDefaultsOnly(e.target.checked)}
                />
                Defaults only
              </label>
              <button type="button" className="drt-btn" disabled={loading} onClick={() => void reload()}>
                Refresh
              </button>
            </div>
          }
        >
          <p className="clips-lib-meta factory-muted">
            {loading ? "Loading…" : `${total} clip${total === 1 ? "" : "s"}`}
            {!loading && clips.length < total ? ` · showing ${clips.length}` : ""}
          </p>
        </PageHeader>

        {error ? <p className="drt-err clips-lib-state">{error}</p> : null}

        <div className="clips-lib-layout">
          <aside className="clips-lib-list" aria-label="Clip library">
            {!loading && clips.length === 0 ? (
              <p className="factory-muted clips-lib-empty">No clips match these filters.</p>
            ) : null}
            {clips.map((c) => {
              const active = c.clip_id === selectedId;
              const span = Math.max(0, c.mark_out_s - c.mark_in_s);
              return (
                <button
                  key={c.clip_id}
                  type="button"
                  className={"clips-lib-card" + (active ? " clips-lib-card--active" : "")}
                  onClick={() => setSelectedId(c.clip_id)}
                >
                  <div className="clips-lib-card__thumb">
                    <ClipThumb relpath={c.media_relpath} markIn={c.mark_in_s} />
                  </div>
                  <div className="clips-lib-card__body">
                    <div className="clips-lib-card__title">
                      {c.label || formatClipTimecode(c.mark_in_s)}
                      {c.is_default ? <span className="clips-lib-badge clips-lib-badge--default">default</span> : null}
                    </div>
                    <div className="clips-lib-card__meta mono">
                      {formatClipTimecode(c.mark_in_s)}–{formatClipTimecode(c.mark_out_s)}
                      <span className="factory-muted"> · {span.toFixed(1)}s</span>
                    </div>
                    <div className="clips-lib-card__path" title={c.media_relpath || undefined}>
                      {c.media_basename || c.media_relpath || "(unresolved parent)"}
                    </div>
                    <div className="clips-lib-card__origin">{originLabel(c.origin)}</div>
                  </div>
                </button>
              );
            })}
          </aside>

          <section
            className={
              "clips-lib-detail" +
              (detailLayout === "split" ? " clips-lib-detail--split" : " clips-lib-detail--stacked")
            }
            aria-label="Selected clip"
          >
            {!selected ? (
              <p className="factory-muted clips-lib-empty">Select a clip to preview.</p>
            ) : !mediaRelpath ? (
              <p className="drt-err">Parent media path missing for this clip — cannot preview.</p>
            ) : (
              <>
                <div className="clips-lib-detail__head">
                  <div>
                    <h2 className="clips-lib-detail__title">{selected.label || "Untitled clip"}</h2>
                    <p className="clips-lib-detail__sub mono">
                      {formatClipTimecode(selected.mark_in_s)}–{formatClipTimecode(selected.mark_out_s)}
                      {" · "}
                      {originLabel(selected.origin)}
                      {selected.is_default ? " · default" : ""}
                    </p>
                    <p className="clips-lib-detail__path" title={mediaRelpath}>
                      {mediaRelpath}
                    </p>
                  </div>
                  <div className="clips-lib-detail__actions">
                    <div
                      className="discovery-preview-layout-switch clips-lib-layout-switch"
                      data-layout={detailLayout}
                    >
                      <span className="discovery-preview-layout-switch__label">Layout</span>
                      <div className="segmented" role="radiogroup" aria-label="Viewer layout">
                        <button
                          type="button"
                          role="radio"
                          className={"seg-btn" + (detailLayout === "stacked" ? " active" : "")}
                          aria-checked={detailLayout === "stacked"}
                          onClick={() => setDetailLayoutFromUser("stacked")}
                          title="Stack viewer above controls"
                        >
                          Stack
                        </button>
                        <button
                          type="button"
                          role="radio"
                          className={"seg-btn" + (detailLayout === "split" ? " active" : "")}
                          aria-checked={detailLayout === "split"}
                          onClick={() => setDetailLayoutFromUser("split")}
                          title="Viewer beside controls"
                        >
                          Split
                        </button>
                      </div>
                    </div>
                    <a className="drt-btn" href={discoveryLibraryHref(mediaRelpath)}>
                      Open in Library
                    </a>
                  </div>
                </div>

                <div
                  className={
                    "clips-lib-stage" +
                    (detailLayout === "split" ? " clips-lib-stage--split" : " clips-lib-stage--stacked")
                  }
                >
                  <div className="clips-lib-stage__viewer">
                    <div className="clips-lib-player-wrap">
                      <video
                        key={mediaRelpath + "::" + selected.clip_id}
                        ref={videoRef}
                        className="clips-lib-player"
                        src={fileUrlFromRel(mediaRelpath)}
                        controls
                        playsInline
                        preload="metadata"
                        onLoadedMetadata={(e) => {
                          const d = e.currentTarget.duration;
                          if (Number.isFinite(d) && d > 0) setVideoDuration(d);
                          setVideoTime(e.currentTarget.currentTime || 0);
                        }}
                        onDurationChange={(e) => {
                          const d = e.currentTarget.duration;
                          if (Number.isFinite(d) && d > 0) setVideoDuration(d);
                        }}
                        onTimeUpdate={(e) => setVideoTime(e.currentTarget.currentTime || 0)}
                        onSeeked={(e) => setVideoTime(e.currentTarget.currentTime || 0)}
                      />
                    </div>

                    <VideoTrimControls
                      className="clips-lib-trim"
                      videoRef={videoRef}
                      duration={videoDuration}
                      currentTime={videoTime}
                      markIn={markIn}
                      markOut={markOut}
                      mode={trimMode}
                      mediaSyncKey={selected.clip_id}
                      size="default"
                      readOnly
                      onSeek={setVideoTime}
                      onSyncTime={setVideoTime}
                      onMarkInChange={() => undefined}
                      onMarkOutChange={() => undefined}
                      onModeChange={setTrimMode}
                      onClear={() => undefined}
                    />
                  </div>

                  <aside className="clips-lib-stage__controls" aria-label="Clip controls">
                    {selected.notes ? (
                      <p className="clips-lib-notes factory-muted" title={selected.notes}>
                        {selected.notes}
                      </p>
                    ) : null}

                    {queueItem ? (
                      <DiscoveryQueueFromClip
                        item={queueItem}
                        mediaRelpath={mediaRelpath}
                        markIn={markIn}
                        markOut={markOut}
                        duration={videoDuration}
                        fps={queueItem.frame_rate || 16}
                        activeClip={selected}
                      />
                    ) : null}
                  </aside>
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
