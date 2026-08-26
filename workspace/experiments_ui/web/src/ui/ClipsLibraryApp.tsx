import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchDiscoveryLibraryItem,
  listShapeFactoryClipsDerived,
  listShapeFactoryClipsLibrary,
  mutateShapeFactoryClip,
  type ShapeFactoryClip,
  type ShapeFactoryClipDerivedItem,
  type ShapeFactoryClipsLibraryParent,
} from "./api";
import { ClipBookmarksRail, formatClipTimecode } from "./ClipBookmarksRail";
import { DiscoveryQueueFromClip } from "./DiscoveryQueueFromClip";
import { discoveryLibraryHref, parseClipsDeepLink, workbenchHref } from "./discoveryDeepLink";
import { cachedEnsureThumbUrl, enqueueEnsureThumb } from "./ensureThumbQueue";
import { PageHeader } from "./PageHeader";
import {
  loadIdentityStillCandidates,
  peekFamiliesBootstrap,
  prefetchFamiliesBootstrap,
  putClipsForMedia,
} from "./shapeFactorySessionCache";
import { pickDefaultExtendFamily } from "./submitFamily";
import type { DiscoveryLibraryItem } from "./types";
import { useTrimPlaybackEnforcement } from "./useTrimPlayback";
import { VideoTrimControls, type VideoTrimPlaybackMode } from "./VideoTrimControls";

const PAGE_SIZE = 80;
const CLIPS_DETAIL_LAYOUT_KEY = "clips_library_detail_layout_v1";
const CLIPS_VIEW_KEY = "clips_library_view_v1";
const CLIPS_AUTOPLAY_KEY = "clips_library_video_autoplay";
const CLIPS_LOOP_KEY = "clips_library_loop_playback";
const MARK_EPS = 1e-3;

type ClipsDetailLayout = "stacked" | "split";
type ClipsBrowseView = "all" | "by_source" | "derived";

type ClipDraft = {
  markIn: number;
  markOut: number;
  label: string;
  notes: string;
};

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

function loadClipsBrowseView(deepView: ClipsBrowseView | null): ClipsBrowseView {
  if (deepView === "all" || deepView === "by_source" || deepView === "derived") return deepView;
  try {
    const v = localStorage.getItem(CLIPS_VIEW_KEY);
    if (v === "by_source" || v === "all" || v === "derived") return v;
  } catch {
    /* ignore */
  }
  return "all";
}

function persistClipsBrowseView(view: ClipsBrowseView) {
  try {
    localStorage.setItem(CLIPS_VIEW_KEY, view);
  } catch {
    /* ignore */
  }
}

function loadClipsAutoplay(): boolean {
  try {
    return localStorage.getItem(CLIPS_AUTOPLAY_KEY) === "1";
  } catch {
    return false;
  }
}

function persistClipsAutoplay(on: boolean) {
  try {
    localStorage.setItem(CLIPS_AUTOPLAY_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function loadClipsLoop(): boolean {
  try {
    const raw = localStorage.getItem(CLIPS_LOOP_KEY);
    if (raw === "0") return false;
    if (raw === "1") return true;
  } catch {
    /* ignore */
  }
  return true;
}

function persistClipsLoop(on: boolean) {
  try {
    localStorage.setItem(CLIPS_LOOP_KEY, on ? "1" : "0");
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

function draftFromClip(clip: ShapeFactoryClip): ClipDraft {
  return {
    markIn: clip.mark_in_s,
    markOut: clip.mark_out_s,
    label: String(clip.label || "").trim() || "Clip",
    notes: String(clip.notes || ""),
  };
}

function marksEqual(a: number, b: number): boolean {
  return Math.abs(a - b) < MARK_EPS;
}

function isDraftDirty(draft: ClipDraft, clip: ShapeFactoryClip): boolean {
  const base = draftFromClip(clip);
  return (
    !marksEqual(draft.markIn, base.markIn) ||
    !marksEqual(draft.markOut, base.markOut) ||
    draft.label !== base.label ||
    draft.notes !== base.notes
  );
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
  const [showRetired, setShowRetired] = useState(false);
  const [usageFilter, setUsageFilter] = useState<"all" | "unused" | "used">("all");
  const [starredOnly, setStarredOnly] = useState(false);
  const [browseView, setBrowseView] = useState<ClipsBrowseView>(() =>
    loadClipsBrowseView(deep.view || (deep.mediaRelpath ? "by_source" : null)),
  );
  const [mediaFilter, setMediaFilter] = useState<string | null>(deep.mediaRelpath);
  /** When set in Derived view, only show outputs from this clip. */
  const [derivedClipFilter, setDerivedClipFilter] = useState<string | null>(
    deep.view === "derived" ? deep.clipId : null,
  );
  const [clips, setClips] = useState<ShapeFactoryClip[]>([]);
  const [parents, setParents] = useState<ShapeFactoryClipsLibraryParent[]>([]);
  const [derived, setDerived] = useState<ShapeFactoryClipDerivedItem[]>([]);
  const [derivedTotal, setDerivedTotal] = useState(0);
  const [selectedDerivedKey, setSelectedDerivedKey] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [originCounts, setOriginCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(deep.clipId);
  const [libraryItem, setLibraryItem] = useState<DiscoveryLibraryItem | null>(null);
  const [draft, setDraft] = useState<ClipDraft | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [editMsg, setEditMsg] = useState<string | null>(null);
  const [siblingsEpoch, setSiblingsEpoch] = useState(0);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const videoAutoplayRef = useRef(false);
  const dirtyRef = useRef(false);
  const [videoDuration, setVideoDuration] = useState(0);
  const [videoTime, setVideoTime] = useState(0);
  const [videoAutoplay, setVideoAutoplay] = useState(loadClipsAutoplay);
  const [loopPlayback, setLoopPlayback] = useState(loadClipsLoop);
  const [detailLayout, setDetailLayout] = useState<ClipsDetailLayout>(() => loadClipsDetailLayout());

  videoAutoplayRef.current = videoAutoplay;
  const trimMode: VideoTrimPlaybackMode = loopPlayback ? "repeat" : "stop_at_end";
  const showParentPicker = browseView === "by_source" && !mediaFilter;
  const showDerivedList = browseView === "derived";

  const setDetailLayoutFromUser = useCallback((layout: ClipsDetailLayout) => {
    setDetailLayout(layout);
    persistClipsDetailLayout(layout);
  }, []);

  const setBrowseViewFromUser = useCallback((view: ClipsBrowseView) => {
    setBrowseView(view);
    persistClipsBrowseView(view);
    if (view === "all") {
      setMediaFilter(null);
      setDerivedClipFilter(null);
    }
    if (view === "derived") {
      setDerivedClipFilter(null);
    }
  }, []);

  const setVideoAutoplayFromUser = useCallback((on: boolean) => {
    setVideoAutoplay(on);
    persistClipsAutoplay(on);
    if (on) {
      const v = videoRef.current;
      if (v) void v.play().catch(() => {});
    }
  }, []);

  const setLoopPlaybackFromUser = useCallback((on: boolean) => {
    setLoopPlayback(on);
    persistClipsLoop(on);
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (browseView === "derived") {
        const res = await listShapeFactoryClipsDerived({
          limit: 200,
          clipId: derivedClipFilter,
          mediaRelpath: mediaFilter,
          includePending: true,
        });
        const rows = res.items || [];
        setDerived(rows);
        setDerivedTotal(res.total ?? rows.length);
        setSelectedDerivedKey((prev) => {
          if (prev && rows.some((r) => r.job_key === prev)) return prev;
          return rows[0]?.job_key || null;
        });
        return;
      }
      const activeMedia = browseView === "by_source" ? mediaFilter : null;
      const res = await listShapeFactoryClipsLibrary({
        limit: PAGE_SIZE,
        offset: 0,
        origin: origin || null,
        q: q || null,
        defaultsOnly,
        mediaRelpath: activeMedia,
        deletedOnly: showRetired,
        unusedOnly: usageFilter === "unused",
        usedOnly: usageFilter === "used",
        starredOnly,
      });
      const rows = res.clips || [];
      setClips(rows);
      setParents(res.parents || []);
      setTotal(res.total ?? rows.length);
      setOriginCounts(res.origin_counts || {});
      if (activeMedia) {
        putClipsForMedia(activeMedia, {
          ok: true,
          clips: rows,
          default_clip_id: rows.find((c) => c.is_default)?.clip_id || null,
        });
      }
      setSelectedId((prev) => {
        if (browseView === "by_source" && !activeMedia) return null;
        if (prev && rows.some((c) => c.clip_id === prev)) return prev;
        return rows[0]?.clip_id || null;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setClips([]);
      setParents([]);
      setDerived([]);
      setDerivedTotal(0);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [origin, q, defaultsOnly, showRetired, usageFilter, starredOnly, browseView, mediaFilter, derivedClipFilter]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    prefetchFamiliesBootstrap();
  }, []);

  const selected = useMemo(
    () => clips.find((c) => c.clip_id === selectedId) || null,
    [clips, selectedId],
  );

  // Warm Submit Extend identity while browsing Clips.
  useEffect(() => {
    const rel = String(selected?.media_relpath || "").trim();
    if (!rel) return;
    const boot = peekFamiliesBootstrap();
    if (!boot?.families?.length) return;
    const family = pickDefaultExtendFamily(
      boot.extend_families?.length ? boot.extend_families : boot.families,
      boot.extend_family_defaults || {},
      null,
      rel,
    );
    if (!family) return;
    void loadIdentityStillCandidates({ relpath: rel, family_slug: family }).catch(() => {
      /* ignore */
    });
  }, [selected?.media_relpath]);

  const dirty = Boolean(selected && draft && isDraftDirty(draft, selected));
  dirtyRef.current = dirty;

  // Sync draft when selection identity changes (not on every list patch of same clip).
  const draftClipIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selected) {
      setDraft(null);
      draftClipIdRef.current = null;
      return;
    }
    if (draftClipIdRef.current !== selected.clip_id) {
      draftClipIdRef.current = selected.clip_id;
      setDraft(draftFromClip(selected));
      setEditError(null);
      setEditMsg(null);
    }
  }, [selected]);

  const mediaRelpath = selected?.media_relpath || null;
  const markIn = draft?.markIn ?? null;
  const markOut = draft?.markOut ?? null;

  const confirmDiscardIfDirty = useCallback((): boolean => {
    if (!dirtyRef.current) return true;
    return window.confirm("Discard unsaved clip edits?");
  }, []);

  const selectClipId = useCallback(
    (nextId: string | null) => {
      if (nextId === selectedId) return;
      if (!confirmDiscardIfDirty()) return;
      setSelectedId(nextId);
    },
    [confirmDiscardIfDirty, selectedId],
  );

  const ensureClipInList = useCallback((clip: ShapeFactoryClip) => {
    setClips((prev) => {
      const idx = prev.findIndex((c) => c.clip_id === clip.clip_id);
      if (idx >= 0) {
        const next = prev.slice();
        next[idx] = { ...next[idx], ...clip };
        return next;
      }
      return [clip, ...prev];
    });
  }, []);

  const selectClipRow = useCallback(
    (clip: ShapeFactoryClip) => {
      if (clip.clip_id === selectedId) return;
      if (!confirmDiscardIfDirty()) return;
      ensureClipInList(clip);
      setSelectedId(clip.clip_id);
    },
    [confirmDiscardIfDirty, ensureClipInList, selectedId],
  );

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
    if (!v || !selected) return;
    const target = selected.mark_in_s;
    const seek = () => {
      try {
        v.currentTime = target;
      } catch {
        /* ignore */
      }
      if (videoAutoplayRef.current) void v.play().catch(() => {});
    };
    if (v.readyState >= 1) seek();
    else v.addEventListener("loadedmetadata", seek, { once: true });
  }, [selectedId, mediaRelpath, selected?.mark_in_s]);

  useTrimPlaybackEnforcement(videoRef, {
    mediaKey: selectedId || "",
    markIn,
    markOut,
    mode: trimMode,
    enabled: Boolean(selected && mediaRelpath && draft),
  });

  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    if (selectedId) sp.set("clip_id", selectedId);
    else sp.delete("clip_id");
    if (q) sp.set("q", q);
    else sp.delete("q");
    if (origin) sp.set("origin", origin);
    else sp.delete("origin");
    sp.set("view", browseView);
    if ((browseView === "by_source" || browseView === "derived") && mediaFilter) {
      sp.set("media", mediaFilter);
    } else {
      sp.delete("media");
      sp.delete("media_relpath");
    }
    if (browseView === "derived" && derivedClipFilter) sp.set("clip_id", derivedClipFilter);
    const next = `${window.location.pathname}?${sp.toString()}`;
    window.history.replaceState(null, "", next);
  }, [selectedId, q, origin, browseView, mediaFilter, derivedClipFilter]);

  const selectParentMedia = useCallback(
    (rel: string) => {
      if (!confirmDiscardIfDirty()) return;
      setMediaFilter(rel.replace(/\\/g, "/"));
      setSelectedId(null);
    },
    [confirmDiscardIfDirty],
  );

  const clearParentMedia = useCallback(() => {
    if (!confirmDiscardIfDirty()) return;
    setMediaFilter(null);
    setSelectedId(null);
  }, [confirmDiscardIfDirty]);

  const selectedParent = useMemo(() => {
    if (!mediaFilter) return null;
    return parents.find((p) => p.media_relpath === mediaFilter) || null;
  }, [mediaFilter, parents]);

  const selectedDerived = useMemo(
    () => derived.find((d) => d.job_key === selectedDerivedKey) || null,
    [derived, selectedDerivedKey],
  );

  const originOptions = useMemo(() => {
    const keys = Object.keys(originCounts).sort((a, b) => (originCounts[b] || 0) - (originCounts[a] || 0));
    return keys;
  }, [originCounts]);

  const queueItem = libraryItem || (mediaRelpath ? stubLibraryItem(mediaRelpath) : null);

  const revertDraft = useCallback(() => {
    if (!selected) return;
    setDraft(draftFromClip(selected));
    setEditError(null);
    setEditMsg(null);
  }, [selected]);

  const patchDefaultsForParent = useCallback((parentMedia: string, defaultId: string | null) => {
    setClips((prev) =>
      prev.map((c) => {
        if (c.media_relpath !== parentMedia) return c;
        return { ...c, is_default: defaultId != null && c.clip_id === defaultId };
      }),
    );
  }, []);

  const saveDraft = useCallback(async () => {
    if (!selected || !draft) return;
    setEditBusy(true);
    setEditError(null);
    setEditMsg(null);
    try {
      const res = await mutateShapeFactoryClip({
        op: "update",
        clip_id: selected.clip_id,
        mark_in: draft.markIn,
        mark_out: draft.markOut,
        label: draft.label,
        notes: draft.notes,
      });
      const updated = res.clip;
      if (!updated) throw new Error("update returned no clip");
      const merged: ShapeFactoryClip = {
        ...selected,
        ...updated,
        media_relpath: selected.media_relpath,
        media_basename: selected.media_basename,
        media_url: selected.media_url,
        is_default: selected.is_default,
      };
      ensureClipInList(merged);
      setDraft(draftFromClip(merged));
      draftClipIdRef.current = merged.clip_id;
      setSiblingsEpoch((n) => n + 1);
      setEditMsg("Saved");
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setEditBusy(false);
    }
  }, [draft, ensureClipInList, selected]);

  const saveAsNew = useCallback(async () => {
    if (!selected || !draft || !mediaRelpath) return;
    setEditBusy(true);
    setEditError(null);
    setEditMsg(null);
    try {
      const res = await mutateShapeFactoryClip({
        op: "create",
        media_relpath: mediaRelpath,
        mark_in: draft.markIn,
        mark_out: draft.markOut,
        label: draft.label || "Clip",
        notes: draft.notes,
        origin: "discovery",
      });
      const created = res.clip;
      if (!created) throw new Error("create returned no clip");
      const merged: ShapeFactoryClip = {
        ...created,
        media_relpath: mediaRelpath,
        media_basename: selected.media_basename,
        media_url: selected.media_url,
        is_default: false,
      };
      ensureClipInList(merged);
      setTotal((t) => t + 1);
      draftClipIdRef.current = null; // force draft resync
      setSelectedId(merged.clip_id);
      setSiblingsEpoch((n) => n + 1);
      setEditMsg("Saved as new clip");
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setEditBusy(false);
    }
  }, [draft, ensureClipInList, mediaRelpath, selected]);

  const setDefault = useCallback(
    async (makeDefault: boolean) => {
      if (!selected || !mediaRelpath) return;
      setEditBusy(true);
      setEditError(null);
      setEditMsg(null);
      try {
        await mutateShapeFactoryClip({
          op: "set_default",
          media_relpath: mediaRelpath,
          clip_id: makeDefault ? selected.clip_id : null,
        });
        patchDefaultsForParent(mediaRelpath, makeDefault ? selected.clip_id : null);
        setClips((prev) =>
          prev.map((c) =>
            c.clip_id === selected.clip_id
              ? { ...c, is_default: makeDefault, is_starred: makeDefault ? true : c.is_starred }
              : makeDefault
                ? { ...c, is_default: false }
                : c,
          ),
        );
        setSiblingsEpoch((n) => n + 1);
        setEditMsg(makeDefault ? "Set as default (also starred)" : "Cleared default");
      } catch (e) {
        setEditError(e instanceof Error ? e.message : String(e));
      } finally {
        setEditBusy(false);
      }
    },
    [mediaRelpath, patchDefaultsForParent, selected],
  );

  const toggleStar = useCallback(
    async (makeStarred: boolean) => {
      if (!selected) return;
      setEditBusy(true);
      setEditError(null);
      setEditMsg(null);
      try {
        const res = await mutateShapeFactoryClip({
          op: makeStarred ? "star" : "unstar",
          clip_id: selected.clip_id,
        });
        const next = (res.clip || null) as ShapeFactoryClip | null;
        setClips((prev) =>
          prev.map((c) => {
            if (c.clip_id !== selected.clip_id) return c;
            return {
              ...c,
              ...(next || {}),
              is_starred: makeStarred,
              is_default: next?.is_default ?? c.is_default,
            };
          }),
        );
        if (starredOnly && !makeStarred) {
          const deletedId = selected.clip_id;
          const idx = clips.findIndex((c) => c.clip_id === deletedId);
          const neighbor = clips[idx + 1] || clips[idx - 1] || null;
          setClips((prev) => prev.filter((c) => c.clip_id !== deletedId));
          setTotal((t) => Math.max(0, t - 1));
          setSelectedId(neighbor?.clip_id || null);
        }
        setSiblingsEpoch((n) => n + 1);
        setEditMsg(makeStarred ? "Starred for hourly lottery" : "Unstarred");
      } catch (e) {
        setEditError(e instanceof Error ? e.message : String(e));
      } finally {
        setEditBusy(false);
      }
    },
    [clips, selected, starredOnly],
  );

  const deleteSelected = useCallback(async () => {
    if (!selected) return;
    if (
      !window.confirm(
        `Retire clip “${selected.label || "Clip"}”? It will be hidden from defaults and lists, but you can restore it later.`,
      )
    )
      return;
    setEditBusy(true);
    setEditError(null);
    setEditMsg(null);
    try {
      await mutateShapeFactoryClip({ op: "delete", clip_id: selected.clip_id });
      const deletedId = selected.clip_id;
      const idx = clips.findIndex((c) => c.clip_id === deletedId);
      const neighbor = clips[idx + 1] || clips[idx - 1] || null;
      setClips((prev) => prev.filter((c) => c.clip_id !== deletedId));
      setTotal((t) => Math.max(0, t - 1));
      dirtyRef.current = false;
      draftClipIdRef.current = null;
      setSelectedId(neighbor?.clip_id || null);
      setSiblingsEpoch((n) => n + 1);
      setEditMsg("Retired — switch to Retired to restore");
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setEditBusy(false);
    }
  }, [clips, selected]);

  const restoreSelected = useCallback(async () => {
    if (!selected) return;
    setEditBusy(true);
    setEditError(null);
    setEditMsg(null);
    try {
      const res = await mutateShapeFactoryClip({ op: "restore", clip_id: selected.clip_id });
      const restored = (res.clip || null) as ShapeFactoryClip | null;
      const restoredId = selected.clip_id;
      if (showRetired) {
        const idx = clips.findIndex((c) => c.clip_id === restoredId);
        const neighbor = clips[idx + 1] || clips[idx - 1] || null;
        setClips((prev) => prev.filter((c) => c.clip_id !== restoredId));
        setTotal((t) => Math.max(0, t - 1));
        setSelectedId(neighbor?.clip_id || null);
      } else if (restored) {
        setClips((prev) =>
          prev.map((c) => (c.clip_id === restoredId ? { ...c, ...restored, deleted: false, deleted_at: null } : c)),
        );
      }
      setSiblingsEpoch((n) => n + 1);
      setEditMsg("Restored");
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setEditBusy(false);
    }
  }, [clips, selected, showRetired]);

  const displayTitle = draft?.label || selected?.label || "Untitled clip";
  const derivedOutRel = selectedDerived?.output_relpath || null;

  return (
    <div className="discovery-screen clips-lib-screen">
      <div className="panel discovery-panel clips-lib-root">
        <PageHeader
          title="Clips"
          subtitle="Browse and edit clip bookmarks across parent videos — adjust the window, set defaults, or queue from the selected clip."
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
                  disabled={showRetired}
                />
                Defaults only
              </label>
              <label className="clips-lib-defaults">
                <input
                  type="checkbox"
                  checked={starredOnly}
                  onChange={(e) => setStarredOnly(e.target.checked)}
                  disabled={showRetired}
                />
                Starred only
              </label>
              <label className="clips-lib-defaults">
                <input
                  type="checkbox"
                  checked={showRetired}
                  onChange={(e) => setShowRetired(e.target.checked)}
                />
                Retired
              </label>
              <label className="clips-lib-filter">
                Usage
                <select
                  value={usageFilter}
                  onChange={(e) => setUsageFilter(e.target.value as "all" | "unused" | "used")}
                  aria-label="Filter by job usage"
                >
                  <option value="all">All</option>
                  <option value="unused">Unused</option>
                  <option value="used">Used</option>
                </select>
              </label>
              <div className="segmented clips-lib-view-switch" role="radiogroup" aria-label="Browse by">
                <button
                  type="button"
                  role="radio"
                  className={"seg-btn" + (browseView === "all" ? " active" : "")}
                  aria-checked={browseView === "all"}
                  onClick={() => setBrowseViewFromUser("all")}
                >
                  All clips
                </button>
                <button
                  type="button"
                  role="radio"
                  className={"seg-btn" + (browseView === "by_source" ? " active" : "")}
                  aria-checked={browseView === "by_source"}
                  onClick={() => setBrowseViewFromUser("by_source")}
                  title="Browse clips grouped by source video"
                >
                  By source
                </button>
                <button
                  type="button"
                  role="radio"
                  className={"seg-btn" + (browseView === "derived" ? " active" : "")}
                  aria-checked={browseView === "derived"}
                  onClick={() => setBrowseViewFromUser("derived")}
                  title="Videos produced from clip bookmarks"
                >
                  Derived
                </button>
              </div>
              <button type="button" className="drt-btn" disabled={loading} onClick={() => void reload()}>
                Refresh
              </button>
            </div>
          }
        >
          <p className="clips-lib-meta factory-muted">
            {loading
              ? "Loading…"
              : showDerivedList
                ? `${derivedTotal} derived video${derivedTotal === 1 ? "" : "s"}`
                : showParentPicker
                  ? `${parents.length} source video${parents.length === 1 ? "" : "s"}`
                  : `${total} clip${total === 1 ? "" : "s"}`}
            {!loading && !showParentPicker && !showDerivedList && clips.length < total
              ? ` · showing ${clips.length}`
              : ""}
            {!loading && browseView === "by_source" && mediaFilter
              ? ` · ${selectedParent?.media_basename || mediaFilter.split("/").pop()}`
              : ""}
            {!loading && showDerivedList && derivedClipFilter ? " · filtered to one clip" : ""}
          </p>
        </PageHeader>

        {error ? <p className="drt-err clips-lib-state">{error}</p> : null}

        <div className="clips-lib-layout">
          <aside
            className="clips-lib-list"
            aria-label={
              showDerivedList ? "Derived videos" : showParentPicker ? "Source videos" : "Clip library"
            }
          >
            {browseView === "by_source" && mediaFilter ? (
              <div className="clips-lib-source-bar">
                <button type="button" className="drt-btn clips-lib-source-bar__back" onClick={clearParentMedia}>
                  ← Sources
                </button>
                <span className="clips-lib-source-bar__name" title={mediaFilter}>
                  {selectedParent?.media_basename || mediaFilter.split("/").pop() || mediaFilter}
                </span>
                <a
                  className="drt-btn clips-lib-source-bar__library"
                  href={discoveryLibraryHref(mediaFilter)}
                  title="Open this source video in Library"
                >
                  Library
                </a>
              </div>
            ) : null}

            {showDerivedList && (derivedClipFilter || mediaFilter) ? (
              <div className="clips-lib-source-bar">
                <button
                  type="button"
                  className="drt-btn clips-lib-source-bar__back"
                  onClick={() => {
                    setDerivedClipFilter(null);
                    setMediaFilter(null);
                  }}
                >
                  Clear filter
                </button>
                <span className="clips-lib-source-bar__name">
                  {derivedClipFilter ? `clip ${derivedClipFilter.slice(0, 12)}…` : mediaFilter}
                </span>
              </div>
            ) : null}

            {showParentPicker ? (
              <>
                {!loading && parents.length === 0 ? (
                  <p className="factory-muted clips-lib-empty">No source videos match these filters.</p>
                ) : null}
                {parents.map((p) => (
                  <div key={p.media_relpath} className="clips-lib-card clips-lib-parent-card">
                    <button
                      type="button"
                      className="clips-lib-parent-card__main"
                      onClick={() => selectParentMedia(p.media_relpath)}
                    >
                      <div className="clips-lib-card__thumb">
                        <ClipThumb relpath={p.media_relpath} markIn={0} />
                      </div>
                      <div className="clips-lib-card__body">
                        <div className="clips-lib-card__title">
                          {p.media_basename || p.media_relpath}
                          {p.has_default ? (
                            <span className="clips-lib-badge clips-lib-badge--default">has default</span>
                          ) : null}
                        </div>
                        <div className="clips-lib-card__meta">
                          {p.clip_count} clip{p.clip_count === 1 ? "" : "s"}
                        </div>
                        <div className="clips-lib-card__path" title={p.media_relpath}>
                          {p.media_relpath}
                        </div>
                      </div>
                    </button>
                    <a
                      className="drt-btn clips-lib-parent-card__library"
                      href={discoveryLibraryHref(p.media_relpath)}
                      title="Open this source video in Library"
                      onClick={(e) => e.stopPropagation()}
                    >
                      Library
                    </a>
                  </div>
                ))}
              </>
            ) : showDerivedList ? (
              <>
                {!loading && derived.length === 0 ? (
                  <p className="factory-muted clips-lib-empty">
                    No derived videos yet — queue from a clip to populate this list.
                  </p>
                ) : null}
                {derived.map((d) => {
                  const active = d.job_key === selectedDerivedKey;
                  return (
                    <button
                      key={d.job_key}
                      type="button"
                      className={"clips-lib-card" + (active ? " clips-lib-card--active" : "")}
                      onClick={() => setSelectedDerivedKey(d.job_key)}
                    >
                      <div className="clips-lib-card__thumb">
                        <ClipThumb relpath={d.output_relpath} markIn={0} />
                      </div>
                      <div className="clips-lib-card__body">
                        <div className="clips-lib-card__title">
                          {d.output_basename || d.job_key}
                          {d.is_hourly ? <span className="clips-lib-badge">hourly</span> : null}
                        </div>
                        <div className="clips-lib-card__meta">
                          {d.family_slug || "—"}
                          {d.status ? <span className="factory-muted"> · {d.status}</span> : null}
                        </div>
                        <div className="clips-lib-card__path" title={d.source_clip_id}>
                          from {d.clip_label || "clip"}
                          {d.source_media_basename ? ` · ${d.source_media_basename}` : ""}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </>
            ) : (
              <>
                {!loading && clips.length === 0 ? (
                  <p className="factory-muted clips-lib-empty">
                    {showRetired ? "No retired clips." : "No clips match these filters."}
                  </p>
                ) : null}
                {clips.map((c) => {
                  const active = c.clip_id === selectedId;
                  const span = Math.max(0, c.mark_out_s - c.mark_in_s);
                  const retired = Boolean(c.deleted || c.deleted_at);
                  return (
                    <button
                      key={c.clip_id}
                      type="button"
                      className={
                        "clips-lib-card" +
                        (active ? " clips-lib-card--active" : "") +
                        (retired ? " clips-lib-card--retired" : "")
                      }
                      onClick={() => selectClipId(c.clip_id)}
                    >
                      <div className="clips-lib-card__thumb">
                        <ClipThumb relpath={c.media_relpath} markIn={c.mark_in_s} />
                      </div>
                      <div className="clips-lib-card__body">
                        <div className="clips-lib-card__title">
                          {c.label || formatClipTimecode(c.mark_in_s)}
                          {c.is_default ? (
                            <span className="clips-lib-badge clips-lib-badge--default">default</span>
                          ) : null}
                          {c.is_starred ? (
                            <span className="clips-lib-badge clips-lib-badge--starred">starred</span>
                          ) : null}
                          {retired ? (
                            <span className="clips-lib-badge clips-lib-badge--retired">retired</span>
                          ) : null}
                          {c.used ? (
                            <span
                              className="clips-lib-badge clips-lib-badge--used"
                              title={`${c.use_count ?? 1} job(s) reference this clip`}
                            >
                              used{typeof c.use_count === "number" && c.use_count > 0 ? ` · ${c.use_count}` : ""}
                            </span>
                          ) : (
                            <span className="clips-lib-badge clips-lib-badge--unused">unused</span>
                          )}
                          {active && dirty ? (
                            <span className="clips-lib-badge clips-lib-badge--dirty">unsaved</span>
                          ) : null}
                        </div>
                        <div className="clips-lib-card__meta mono">
                          {formatClipTimecode(c.mark_in_s)}–{formatClipTimecode(c.mark_out_s)}
                          <span className="factory-muted"> · {span.toFixed(1)}s</span>
                        </div>
                        {browseView === "all" ? (
                          <div className="clips-lib-card__path" title={c.media_relpath || undefined}>
                            {c.media_basename || c.media_relpath || "(unresolved parent)"}
                          </div>
                        ) : null}
                        <div className="clips-lib-card__origin">{originLabel(c.origin)}</div>
                      </div>
                    </button>
                  );
                })}
              </>
            )}
          </aside>

          <section
            className={
              "clips-lib-detail" +
              (detailLayout === "split" ? " clips-lib-detail--split" : " clips-lib-detail--stacked")
            }
            aria-label={showDerivedList ? "Selected derived video" : "Selected clip"}
          >
            {showDerivedList ? (
              !selectedDerived ? (
                <p className="factory-muted clips-lib-empty">Select a derived video to preview.</p>
              ) : (
                <>
                  <div className="clips-lib-detail__head">
                    <div>
                      <h2 className="clips-lib-detail__title">
                        {selectedDerived.output_basename || selectedDerived.job_key}
                      </h2>
                      <p className="clips-lib-detail__sub mono">
                        {selectedDerived.family_slug || "—"}
                        {selectedDerived.status ? ` · ${selectedDerived.status}` : ""}
                        {selectedDerived.is_hourly ? " · hourly" : ""}
                      </p>
                      <p className="clips-lib-detail__path" title={derivedOutRel || undefined}>
                        {derivedOutRel || "(no output file yet)"}
                      </p>
                      <p className="clips-lib-detail__sub">
                        From clip{" "}
                        <button
                          type="button"
                          className="clips-lib-inline-link"
                          onClick={() => {
                            setDerivedClipFilter(selectedDerived.source_clip_id);
                            setBrowseViewFromUser("all");
                            setSelectedId(selectedDerived.source_clip_id);
                          }}
                        >
                          {selectedDerived.clip_label || selectedDerived.source_clip_id}
                        </button>
                        {selectedDerived.source_media_basename
                          ? ` · ${selectedDerived.source_media_basename}`
                          : ""}
                      </p>
                    </div>
                    <div className="clips-lib-detail__actions">
                      {derivedOutRel ? (
                        <a className="drt-btn" href={discoveryLibraryHref(derivedOutRel)}>
                          Open in Library
                        </a>
                      ) : null}
                      <a className="drt-btn" href={workbenchHref({ jobKey: selectedDerived.job_key })}>
                        Open in Workbench
                      </a>
                      <button
                        type="button"
                        className="drt-btn"
                        onClick={() => setDerivedClipFilter(selectedDerived.source_clip_id)}
                        title="Show only outputs from this source clip"
                      >
                        Filter to clip
                      </button>
                    </div>
                  </div>
                  {derivedOutRel ? (
                    <div className="clips-lib-stage clips-lib-stage--stacked">
                      <div className="clips-lib-stage__viewer">
                        <div className="clips-lib-player-wrap">
                          <video
                            key={derivedOutRel}
                            className="clips-lib-player"
                            src={fileUrlFromRel(derivedOutRel)}
                            controls
                            playsInline
                            preload="metadata"
                            autoPlay={videoAutoplay}
                            muted={videoAutoplay}
                          />
                        </div>
                      </div>
                    </div>
                  ) : (
                    <p className="factory-muted clips-lib-empty">
                      Job matched a clip but no output file is on disk yet ({selectedDerived.status || "pending"}).
                    </p>
                  )}
                </>
              )
            ) : showParentPicker ? (
              <p className="factory-muted clips-lib-empty">Select a source video to browse its clips.</p>
            ) : !selected || !draft ? (
              <p className="factory-muted clips-lib-empty">Select a clip to edit.</p>
            ) : !mediaRelpath ? (
              <p className="drt-err">Parent media path missing for this clip — cannot preview.</p>
            ) : (
              <>
                <div
                  className={
                    "clips-lib-detail__head" +
                    (detailLayout === "split" ? " clips-lib-detail__head--compact" : "")
                  }
                >
                  <div className="clips-lib-detail__head-main">
                    <h2 className="clips-lib-detail__title" title={mediaRelpath}>
                      {displayTitle}
                      {dirty ? <span className="clips-lib-badge clips-lib-badge--dirty">unsaved</span> : null}
                    </h2>
                    <p className="clips-lib-detail__sub mono">
                      {formatClipTimecode(draft.markIn)}–{formatClipTimecode(draft.markOut)}
                      {" · "}
                      {originLabel(selected.origin)}
                      {selected.is_default ? " · default" : ""}
                      {selected.is_starred ? " · starred" : ""}
                      {selected.deleted || selected.deleted_at ? " · retired" : ""}
                      {selected.used
                        ? ` · used${typeof selected.use_count === "number" ? ` (${selected.use_count})` : ""}`
                        : " · unused"}
                      {detailLayout === "split" && selected.media_basename
                        ? ` · ${selected.media_basename}`
                        : ""}
                    </p>
                    {detailLayout !== "split" ? (
                      <p className="clips-lib-detail__path" title={mediaRelpath}>
                        {mediaRelpath}
                      </p>
                    ) : null}
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
                      Library
                    </a>
                    <button
                      type="button"
                      className="drt-btn"
                      onClick={() => {
                        setDerivedClipFilter(selected.clip_id);
                        setBrowseView("derived");
                        persistClipsBrowseView("derived");
                      }}
                      title="List videos produced from this clip"
                    >
                      Derived
                    </button>
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
                        autoPlay={videoAutoplay}
                        muted={videoAutoplay}
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
                      autoplay={videoAutoplay}
                      onAutoplayChange={setVideoAutoplayFromUser}
                      onSeek={setVideoTime}
                      onSyncTime={setVideoTime}
                      onMarkInChange={(t) => setDraft((d) => (d ? { ...d, markIn: t } : d))}
                      onMarkOutChange={(t) => setDraft((d) => (d ? { ...d, markOut: t } : d))}
                      onModeChange={(m) => setLoopPlaybackFromUser(m === "repeat")}
                      onClear={revertDraft}
                    />

                    {detailLayout !== "split" ? (
                      <ClipBookmarksRail
                        key={`${mediaRelpath}::${siblingsEpoch}`}
                        className="clips-lib-siblings"
                        mediaRelpath={mediaRelpath}
                        duration={videoDuration}
                        markIn={markIn}
                        markOut={markOut}
                        trimEditable={false}
                        showActions={false}
                        origin="discovery"
                        selectedClipId={selected.clip_id}
                        onSelectClip={(clip) => {
                          if (clip) selectClipRow(clip);
                        }}
                        onApplyClip={(_mi, _mo, clip) => {
                          if (clip) selectClipRow(clip);
                        }}
                      />
                    ) : null}
                  </div>

                  <aside className="clips-lib-stage__controls" aria-label="Clip controls">
                    {detailLayout === "split" ? (
                      <ClipBookmarksRail
                        key={`${mediaRelpath}::${siblingsEpoch}::panel`}
                        className="clips-lib-siblings clips-lib-siblings--panel"
                        mediaRelpath={mediaRelpath}
                        duration={videoDuration}
                        markIn={markIn}
                        markOut={markOut}
                        trimEditable={false}
                        showActions={false}
                        origin="discovery"
                        selectedClipId={selected.clip_id}
                        onSelectClip={(clip) => {
                          if (clip) selectClipRow(clip);
                        }}
                        onApplyClip={(_mi, _mo, clip) => {
                          if (clip) selectClipRow(clip);
                        }}
                      />
                    ) : null}

                    <div className="clips-lib-editor">
                      <label className="clips-lib-editor__field">
                        <span>Label</span>
                        <input
                          type="text"
                          value={draft.label}
                          disabled={editBusy}
                          onChange={(e) => setDraft((d) => (d ? { ...d, label: e.target.value } : d))}
                          aria-label="Clip label"
                        />
                      </label>
                      <label className="clips-lib-editor__field">
                        <span>Notes</span>
                        <textarea
                          value={draft.notes}
                          disabled={editBusy}
                          rows={detailLayout === "split" ? 2 : 3}
                          onChange={(e) => setDraft((d) => (d ? { ...d, notes: e.target.value } : d))}
                          aria-label="Clip notes"
                        />
                      </label>
                      <div className="clips-lib-editor__actions">
                        <button
                          type="button"
                          className="drt-btn"
                          disabled={!dirty || editBusy}
                          onClick={() => void saveDraft()}
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          className="drt-btn"
                          disabled={!dirty || editBusy}
                          onClick={revertDraft}
                        >
                          Revert
                        </button>
                        <button
                          type="button"
                          className="drt-btn"
                          disabled={!dirty || editBusy}
                          onClick={() => void saveAsNew()}
                          title="Create a new clip with the current draft window"
                        >
                          Save as new
                        </button>
                        {selected.is_starred ? (
                          <button
                            type="button"
                            className="drt-btn"
                            disabled={editBusy || Boolean(selected.deleted || selected.deleted_at)}
                            onClick={() => void toggleStar(false)}
                            title="Remove from hourly lottery"
                          >
                            Unstar
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="drt-btn"
                            disabled={editBusy || Boolean(selected.deleted || selected.deleted_at)}
                            onClick={() => void toggleStar(true)}
                            title="Add to hourly lottery (prefer newer among ★)"
                          >
                            Star
                          </button>
                        )}
                        {selected.is_default ? (
                          <button
                            type="button"
                            className="drt-btn"
                            disabled={editBusy || Boolean(selected.deleted || selected.deleted_at)}
                            onClick={() => void setDefault(false)}
                          >
                            Clear default
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="drt-btn"
                            disabled={editBusy || Boolean(selected.deleted || selected.deleted_at)}
                            onClick={() => void setDefault(true)}
                          >
                            Set default
                          </button>
                        )}
                        {selected.deleted || selected.deleted_at ? (
                          <button
                            type="button"
                            className="drt-btn"
                            disabled={editBusy}
                            onClick={() => void restoreSelected()}
                            title="Clear retirement and show this clip in the active library again"
                          >
                            Restore
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="drt-btn clips-lib-editor__delete"
                            disabled={editBusy}
                            onClick={() => void deleteSelected()}
                            title="Retire this clip (soft-delete). You can restore it later."
                          >
                            Retire
                          </button>
                        )}
                      </div>
                      {editError ? <p className="drt-err clips-lib-editor__status">{editError}</p> : null}
                      {editMsg && !editError ? (
                        <p className="factory-muted clips-lib-editor__status">{editMsg}</p>
                      ) : null}
                    </div>

                    {queueItem ? (
                      <DiscoveryQueueFromClip
                        item={queueItem}
                        mediaRelpath={mediaRelpath}
                        markIn={markIn}
                        markOut={markOut}
                        duration={videoDuration}
                        fps={queueItem.frame_rate || 16}
                        activeClip={selected}
                        origin="clips"
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
