import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchFamilyDiscoveryGallery,
  fetchFamilyDiscoveryIndex,
  fetchFamilyDiscoveryProp,
  updateFamilyDiscoveryProp,
} from "./api";
import type {
  FamilyDiscoveryBucketRow,
  FamilyDiscoveryIndexResponse,
  FamilyDiscoveryIndexRow,
  FamilyDiscoveryProp,
  FamilyDiscoverySampleVideo,
  FamilyDiscoverySourceRow,
  FamilyDiscoveryStatus,
} from "./types";

const STATUS_OPTIONS: { value: FamilyDiscoveryStatus; label: string }[] = [
  { value: "pending_review", label: "Pending review" },
  { value: "new_family", label: "New family" },
  { value: "merge", label: "Merge into enrolled" },
  { value: "skip", label: "Skip" },
  { value: "enrolled", label: "Enrolled (CLI)" },
];

const AUTOPLAY_KEY = "family-review.lightbox.autoplay";
const END_MODE_KEY = "family-review.lightbox.endMode";
const GALLERY_SORT_KEY = "family-review.gallery.sort";
const GALLERY_GROUP_KEY = "family-review.gallery.groupBySource";
const PAGE_SIZE = 48;

type Scope = "buckets" | "proposals" | "sources";
type EndMode = "advance" | "repeat";
type GalleryMode = "fingerprint" | "unmatched_all" | "source" | null;
type GallerySort = "newest" | "source";

function readGallerySort(): GallerySort {
  try {
    const v = localStorage.getItem(GALLERY_SORT_KEY);
    if (v === "source") return "source";
  } catch {
    /* ignore */
  }
  return "newest";
}

function writeGallerySort(sort: GallerySort) {
  try {
    localStorage.setItem(GALLERY_SORT_KEY, sort);
  } catch {
    /* ignore */
  }
}

function readGalleryGroup(): boolean {
  try {
    const v = localStorage.getItem(GALLERY_GROUP_KEY);
    return v === "1" || v === "true";
  } catch {
    return false;
  }
}

function writeGalleryGroup(on: boolean) {
  try {
    localStorage.setItem(GALLERY_GROUP_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function statusClass(status: string | null | undefined): string {
  const s = String(status || "pending_review").toLowerCase();
  if (s === "new_family") return "family-review-status--new";
  if (s === "merge") return "family-review-status--merge";
  if (s === "skip") return "family-review-status--skip";
  if (s === "enrolled") return "family-review-status--enrolled";
  return "family-review-status--pending";
}

function formatOutputDateRange(
  first?: string | null,
  last?: string | null,
  days?: number | null
): string {
  const a = String(first || "").trim();
  const b = String(last || "").trim();
  if (!a && !b) return "";
  if (a && b && a !== b) {
    const dayBit = typeof days === "number" && days > 0 ? ` (${days}d)` : "";
    return `${a} → ${b}${dayBit}`;
  }
  return a || b;
}

function shortFp(fp?: string | null): string {
  const s = String(fp || "").trim();
  return s ? `${s.slice(0, 12)}…` : "—";
}

function readAutoplay(): boolean {
  try {
    const v = localStorage.getItem(AUTOPLAY_KEY);
    if (v === null) return true;
    return v === "1" || v === "true";
  } catch {
    return true;
  }
}

function writeAutoplay(on: boolean) {
  try {
    localStorage.setItem(AUTOPLAY_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function readEndMode(): EndMode {
  try {
    const v = localStorage.getItem(END_MODE_KEY);
    if (v === "repeat") return "repeat";
    if (v === "advance") return "advance";
  } catch {
    /* ignore */
  }
  return "advance";
}

function writeEndMode(mode: EndMode) {
  try {
    localStorage.setItem(END_MODE_KEY, mode);
  } catch {
    /* ignore */
  }
}

function nextEndMode(m: EndMode): EndMode {
  return m === "repeat" ? "advance" : "repeat";
}

/** Loop current clip (same glyph family as Discovery trim-out repeat). */
function IconEndRepeat() {
  return (
    <svg className="family-review__end-mode-svg" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="m17 2 4 4-4 4"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M3 11v-1a4 4 0 0 1 4-4h14"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="m7 22-4-4 4-4"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M21 13v1a4 4 0 0 1-4 4H3"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Advance to next clip when current ends. */
function IconEndAdvance() {
  return (
    <svg className="family-review__end-mode-svg" viewBox="0 0 24 24" aria-hidden>
      <path d="M5 5v14l9-7-9-7z" fill="currentColor" />
      <path d="M16 5v14h3V5h-3z" fill="currentColor" />
    </svg>
  );
}

export function FamilyDiscoveryReview() {
  const [index, setIndex] = useState<FamilyDiscoveryIndexResponse | null>(null);
  const [scope, setScope] = useState<Scope>("buckets");
  const [selectedId, setSelectedId] = useState<string>("");
  const [selectedFp, setSelectedFp] = useState<string>("");
  const [selectedSource, setSelectedSource] = useState<string>("");
  const [prop, setProp] = useState<FamilyDiscoveryProp | null>(null);
  const [enrolled, setEnrolled] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("pending_review");
  const [bucketFilter, setBucketFilter] = useState<string>("unmatched");
  const [sourceKindFilter, setSourceKindFilter] = useState<string>("all");
  const [loadingList, setLoadingList] = useState(false);
  const [loadingProp, setLoadingProp] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [status, setStatus] = useState<FamilyDiscoveryStatus>("pending_review");
  const [slug, setSlug] = useState("");
  const [nearest, setNearest] = useState("");
  const [notes, setNotes] = useState("");

  const [galleryItems, setGalleryItems] = useState<FamilyDiscoverySampleVideo[]>([]);
  const [galleryTotal, setGalleryTotal] = useState(0);
  const [galleryLoading, setGalleryLoading] = useState(false);
  const [galleryQ, setGalleryQ] = useState("");
  const [galleryDate, setGalleryDate] = useState("");
  const [gallerySort, setGallerySort] = useState<GallerySort>(readGallerySort);
  const [galleryGroupBySource, setGalleryGroupBySource] = useState(readGalleryGroup);
  const [galleryMode, setGalleryMode] = useState<GalleryMode>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [autoplay, setAutoplay] = useState(readAutoplay);
  const [endMode, setEndMode] = useState<EndMode>(readEndMode);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const loadMoreRef = useRef<() => Promise<void>>(async () => {});
  const endModeRef = useRef(endMode);
  endModeRef.current = endMode;

  const reloadIndex = async (preferId?: string, preferFp?: string) => {
    setLoadingList(true);
    setError("");
    try {
      const next = await fetchFamilyDiscoveryIndex();
      setIndex(next);
      if (next.enrolled_families?.length) setEnrolled(next.enrolled_families);
      const buckets = next.buckets || [];
      const rows = next.proposals || [];
      const sources = next.sources || [];
      if (scope === "buckets" || (!preferId && scope !== "sources" && scope !== "proposals")) {
        const fp =
          (preferFp && buckets.some((b) => b.fingerprint === preferFp) && preferFp) ||
          selectedFp ||
          buckets.find((b) => String(b.match_class || "") === "unmatched" && (b.video_count || 0) > 0)
            ?.fingerprint ||
          buckets.find((b) => (b.video_count || 0) > 0)?.fingerprint ||
          "";
        if (fp) setSelectedFp(fp);
      }
      if (scope === "sources") {
        const pick =
          (selectedSource && sources.some((s) => s.key === selectedSource) && selectedSource) ||
          sources[0]?.key ||
          "";
        if (pick) setSelectedSource(pick);
      }
      if (scope === "proposals") {
        const pick =
          (preferId && rows.some((r) => r.id === preferId) && preferId) ||
          rows.find((r) => String(r.status || "") === "pending_review")?.id ||
          rows[0]?.id ||
          "";
        if (pick) setSelectedId(pick);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingList(false);
    }
  };

  useEffect(() => {
    void reloadIndex();
  }, []);

  useEffect(() => {
    if (scope !== "proposals" || !selectedId) {
      if (scope !== "proposals") setProp(null);
      return;
    }
    let cancelled = false;
    setLoadingProp(true);
    setError("");
    setNotice("");
    void (async () => {
      try {
        const res = await fetchFamilyDiscoveryProp(selectedId);
        if (cancelled) return;
        const p = res.prop || null;
        setProp(p);
        if (res.enrolled_families?.length) setEnrolled(res.enrolled_families);
        setStatus((String(p?.status || "pending_review") as FamilyDiscoveryStatus) || "pending_review");
        setSlug(String(p?.proposed_family_slug || ""));
        setNearest(String(p?.nearest_enrolled || ""));
        setNotes(String(p?.operator_notes || ""));
        const fp = String(p?.fingerprint || "").trim();
        if (fp) setSelectedFp(fp);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoadingProp(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, scope]);

  const filteredProposals = useMemo(() => {
    const rows = index?.proposals || [];
    // Judgment queue: only cards that already have exemplar video to examine.
    const withVideo = rows.filter((r) => (r.sample_count || 0) > 0);
    if (statusFilter === "all") return withVideo;
    return withVideo.filter((r) => String(r.status || "pending_review") === statusFilter);
  }, [index, statusFilter]);

  const filteredBuckets = useMemo(() => {
    const rows = (index?.buckets || []).filter((b) => (b.video_count || 0) > 0);
    if (bucketFilter === "all") return rows;
    if (bucketFilter === "unmatched_all") return rows.filter((r) => r.match_class === "unmatched");
    return rows.filter((r) => String(r.match_class || "") === bucketFilter);
  }, [index, bucketFilter]);

  const filteredSources = useMemo(() => {
    const rows = (index?.sources || []).filter((s) => (s.video_count || 0) > 0);
    if (sourceKindFilter === "all") return rows;
    return rows.filter((s) => String(s.kind || "") === sourceKindFilter);
  }, [index, sourceKindFilter]);

  const bucketTotalWithVideo = useMemo(
    () => (index?.buckets || []).filter((b) => (b.video_count || 0) > 0).length,
    [index]
  );

  const sourceTotal = useMemo(() => (index?.sources || []).length, [index]);

  // Keep selection inside the active bucket filter (skip aggregate "all unclassified" mode).
  useEffect(() => {
    if (scope !== "buckets" || bucketFilter === "unmatched_all") return;
    if (!filteredBuckets.length) {
      setSelectedFp("");
      return;
    }
    if (!selectedFp || !filteredBuckets.some((b) => b.fingerprint === selectedFp)) {
      setSelectedFp(filteredBuckets[0].fingerprint);
    }
  }, [scope, bucketFilter, filteredBuckets, selectedFp]);

  useEffect(() => {
    if (scope !== "sources") return;
    if (!filteredSources.length) {
      setSelectedSource("");
      return;
    }
    if (!selectedSource || !filteredSources.some((s) => s.key === selectedSource)) {
      setSelectedSource(filteredSources[0].key);
    }
  }, [scope, filteredSources, selectedSource]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: 0 };
    for (const r of index?.proposals || []) {
      if ((r.sample_count || 0) <= 0) continue;
      const s = String(r.status || "pending_review");
      c[s] = (c[s] || 0) + 1;
      c.all += 1;
    }
    return c;
  }, [index]);

  const bc = index?.bucket_counts || {};
  const sc = index?.source_counts || {};

  const effectiveGallerySort: GallerySort =
    galleryGroupBySource && gallerySort === "newest" ? "source" : gallerySort;

  const resetGallery = useCallback(() => {
    setGalleryItems([]);
    setGalleryTotal(0);
    setViewerIndex(null);
  }, []);

  const loadGalleryPage = useCallback(
    async (offset: number, replace: boolean) => {
      if (!galleryMode) return;
      setGalleryLoading(true);
      setError("");
      try {
        const res = await fetchFamilyDiscoveryGallery({
          fingerprint: galleryMode === "fingerprint" ? selectedFp || undefined : undefined,
          match_class: galleryMode === "unmatched_all" ? "unmatched" : undefined,
          source: galleryMode === "source" ? selectedSource || undefined : undefined,
          offset,
          limit: PAGE_SIZE,
          q: galleryQ.trim() || undefined,
          date: galleryDate.trim() || undefined,
          sort: effectiveGallerySort,
          group: galleryGroupBySource,
        });
        setGalleryTotal(res.total || 0);
        setGalleryItems((prev) => (replace ? res.items || [] : [...prev, ...(res.items || [])]));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setGalleryLoading(false);
      }
    },
    [
      galleryMode,
      selectedFp,
      selectedSource,
      galleryQ,
      galleryDate,
      effectiveGallerySort,
      galleryGroupBySource,
    ]
  );

  const loadMore = useCallback(async () => {
    if (galleryLoading) return;
    if (galleryItems.length >= galleryTotal && galleryTotal > 0) return;
    await loadGalleryPage(galleryItems.length, false);
  }, [galleryLoading, galleryItems.length, galleryTotal, loadGalleryPage]);

  loadMoreRef.current = loadMore;

  useEffect(() => {
    if (scope === "proposals" && prop?.fingerprint) {
      setGalleryMode("fingerprint");
      setSelectedFp(String(prop.fingerprint));
    } else if (scope === "sources" && selectedSource) {
      setGalleryMode("source");
    } else if (scope === "sources") {
      setGalleryMode(null);
      resetGallery();
    } else if (scope === "buckets" && bucketFilter === "unmatched_all") {
      setGalleryMode("unmatched_all");
      setSelectedFp("");
    } else if (scope === "buckets" && selectedFp) {
      setGalleryMode("fingerprint");
    } else if (scope === "buckets") {
      setGalleryMode(null);
      resetGallery();
    }
  }, [scope, prop?.fingerprint, selectedFp, selectedSource, bucketFilter, resetGallery]);

  useEffect(() => {
    if (!galleryMode) return;
    if (galleryMode === "fingerprint" && !selectedFp) return;
    if (galleryMode === "source" && !selectedSource) return;
    resetGallery();
    void loadGalleryPage(0, true);
  }, [galleryMode, selectedFp, selectedSource, galleryQ, galleryDate, effectiveGallerySort, galleryGroupBySource]); // eslint-disable-line react-hooks/exhaustive-deps

  const gallerySections = useMemo(() => {
    type Sec = { key: string; label: string; start: number; items: FamilyDiscoverySampleVideo[] };
    if (!galleryGroupBySource) {
      return [{ key: "__all__", label: "", start: 0, items: galleryItems }] as Sec[];
    }
    const out: Sec[] = [];
    for (let i = 0; i < galleryItems.length; i++) {
      const s = galleryItems[i];
      const key = String(s.source_key || "").trim() || "__unknown__";
      const label =
        String(s.source_label || s.source_key || "").trim() ||
        (key === "__unknown__" ? "Unknown source" : key);
      const last = out[out.length - 1];
      if (last && last.key === key) {
        last.items.push(s);
      } else {
        out.push({ key, label, start: i, items: [s] });
      }
    }
    return out;
  }, [galleryItems, galleryGroupBySource]);

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !galleryMode) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) void loadMoreRef.current();
      },
      { rootMargin: "240px" }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [galleryMode, galleryItems.length]);

  const openViewer = (idx: number) => setViewerIndex(idx);

  const viewerItem =
    viewerIndex != null && viewerIndex >= 0 && viewerIndex < galleryItems.length
      ? galleryItems[viewerIndex]
      : null;

  const goViewer = useCallback(
    async (delta: number) => {
      if (viewerIndex == null) return;
      const next = viewerIndex + delta;
      if (next < 0) return;
      if (next >= galleryItems.length) {
        if (galleryItems.length < galleryTotal) {
          await loadMoreRef.current();
          setViewerIndex(next);
        }
        return;
      }
      setViewerIndex(next);
      if (next >= galleryItems.length - 4 && galleryItems.length < galleryTotal) {
        void loadMoreRef.current();
      }
    },
    [viewerIndex, galleryItems.length, galleryTotal]
  );

  useEffect(() => {
    if (viewerIndex == null) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
      if (e.key === "Escape") setViewerIndex(null);
      if (e.key === "ArrowLeft" || e.key === "j") {
        e.preventDefault();
        void goViewer(-1);
      }
      if (e.key === "ArrowRight" || e.key === "k") {
        e.preventDefault();
        void goViewer(1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [viewerIndex, goViewer]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v || viewerIndex == null) return;
    v.load();
    if (autoplay) {
      void v.play().catch(() => undefined);
    } else {
      v.pause();
    }
  }, [viewerIndex, viewerItem?.url, autoplay]);

  const save = async () => {
    if (!selectedId) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const res = await updateFamilyDiscoveryProp(selectedId, {
        status,
        proposed_family_slug: slug.trim() || null,
        nearest_enrolled: nearest.trim() || null,
        operator_notes: notes.trim() || null,
        operator_decision: status,
      });
      setProp(res.prop || null);
      setNotice("Saved.");
      await reloadIndex(selectedId, selectedFp);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const playlistForLightbox =
    scope === "proposals" && galleryItems.length === 0 && (prop?.sample_videos || []).length
      ? prop!.sample_videos || []
      : galleryItems;

  const lightboxItems = playlistForLightbox;
  const lightboxItem =
    viewerIndex != null && viewerIndex >= 0 && viewerIndex < lightboxItems.length
      ? lightboxItems[viewerIndex]
      : null;

  const openFromSamples = (samples: FamilyDiscoverySampleVideo[], idx: number) => {
    if (galleryItems.length === 0 && samples.length) {
      setGalleryItems(samples);
      setGalleryTotal(samples.length);
    }
    setViewerIndex(idx);
  };

  return (
    <div className="family-review">
      <div className="family-review__intro factory-muted">
        Focus on <strong>buckets</strong> (topology fingerprints with og video) or{" "}
        <strong>sources</strong> (input still/video across buckets). Unclassified = unmatched.
        Proposals remain a judgment queue for buckets that already have exemplars.
        {index?.exemplar_generated_at ? <> · exemplars {index.exemplar_generated_at}</> : null}
        {typeof bc.unmatched === "number" ? (
          <>
            {" "}
            · unclassified {bc.unmatched} buckets ({bc.videos_unmatched ?? "?"} vids)
          </>
        ) : null}
        {typeof sc.sources === "number" ? (
          <>
            {" "}
            · {sc.sources} sources ({sc.videos_with_source ?? "?"} vids)
          </>
        ) : null}
      </div>

      <div className="family-review__toolbar">
        <div className="family-review__scopes" role="tablist" aria-label="Browse scope">
          {(
            [
              ["buckets", "Buckets"],
              ["sources", "Sources"],
              ["proposals", "Proposals"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={scope === id}
              className={"family-review__scope" + (scope === id ? " family-review__scope--active" : "")}
              onClick={() => {
                setScope(id);
                setViewerIndex(null);
                if (id === "buckets" && bucketFilter === "unmatched_all") {
                  setSelectedFp("");
                }
              }}
            >
              {label}
              {id === "buckets" ? ` (${bucketTotalWithVideo})` : null}
              {id === "sources" ? ` (${sourceTotal})` : null}
              {id === "proposals" ? ` (${counts.all || 0})` : null}
            </button>
          ))}
        </div>

        {scope === "proposals" ? (
          <label className="family-review__filter">
            Status
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="pending_review">Pending ({counts.pending_review || 0})</option>
              <option value="new_family">New family ({counts.new_family || 0})</option>
              <option value="merge">Merge ({counts.merge || 0})</option>
              <option value="skip">Skip ({counts.skip || 0})</option>
              <option value="enrolled">Enrolled ({counts.enrolled || 0})</option>
              <option value="all">All ({counts.all || 0})</option>
            </select>
          </label>
        ) : null}

        {scope === "buckets" ? (
          <label className="family-review__filter">
            Class
            <select
              value={bucketFilter}
              onChange={(e) => {
                const v = e.target.value;
                setBucketFilter(v);
                if (v === "unmatched_all") {
                  setSelectedFp("");
                  setGalleryMode("unmatched_all");
                }
              }}
            >
              <option value="unmatched">Unclassified ({bc.unmatched || 0})</option>
              <option value="unmatched_all">All unclassified videos</option>
              <option value="enrolled">Enrolled ({bc.enrolled || 0})</option>
              <option value="catalog_only">Catalog ({bc.catalog_only || 0})</option>
              <option value="all">All buckets</option>
            </select>
          </label>
        ) : null}

        {scope === "sources" ? (
          <label className="family-review__filter">
            Kind
            <select value={sourceKindFilter} onChange={(e) => setSourceKindFilter(e.target.value)}>
              <option value="all">All ({sourceTotal})</option>
              <option value="image">Images ({sc.images ?? 0})</option>
              <option value="video">Videos ({sc.videos ?? 0})</option>
            </select>
          </label>
        ) : null}

        <button
          type="button"
          disabled={loadingList}
          onClick={() => void reloadIndex(selectedId, selectedFp)}
        >
          {loadingList ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error ? <div className="factory-error">{error}</div> : null}
      {notice ? <div className="family-review__notice">{notice}</div> : null}
      {index?.browse_error ? (
        <div className="factory-muted">Browse warning: {index.browse_error}</div>
      ) : null}

      <div className="family-review__layout">
        <aside className="family-review__list" aria-label="Browse list">
          {scope === "proposals"
            ? filteredProposals.map((row: FamilyDiscoveryIndexRow) => (
                <button
                  key={row.id}
                  type="button"
                  className={
                    "family-review__row" + (row.id === selectedId ? " family-review__row--active" : "")
                  }
                  onClick={() => {
                    setSelectedId(row.id);
                    setViewerIndex(null);
                  }}
                >
                  <span className="family-review__row-id">{row.id}</span>
                  <span className={"family-review-status " + statusClass(row.status)}>
                    {row.status || "pending_review"}
                  </span>
                  <span className="family-review__row-meta">
                    {row.io_guess || "—"} · {row.members ?? "?"} mem ·{" "}
                    <span
                      className={
                        typeof row.sample_count === "number" && row.sample_count > 0
                          ? ""
                          : "factory-muted"
                      }
                    >
                      {typeof row.sample_count === "number"
                        ? `${row.sample_count}${
                            typeof row.sample_target === "number" ? `/${row.sample_target}` : ""
                          } ex`
                        : "? ex"}
                    </span>
                    {" · "}
                    {row.representative || "—"}
                  </span>
                  {formatOutputDateRange(
                    row.output_date_first,
                    row.output_date_last,
                    row.output_date_days
                  ) ? (
                    <span className="family-review__row-dates mono">
                      {formatOutputDateRange(
                        row.output_date_first,
                        row.output_date_last,
                        row.output_date_days
                      )}
                    </span>
                  ) : (
                    <span className="family-review__row-dates factory-muted">no dated outputs</span>
                  )}
                </button>
              ))
            : null}

          {scope === "buckets"
            ? filteredBuckets.map((row: FamilyDiscoveryBucketRow) => {
                const active =
                  bucketFilter !== "unmatched_all" && selectedFp === row.fingerprint;
                const vids = row.video_count ?? 0;
                return (
                  <button
                    key={row.fingerprint}
                    type="button"
                    className={
                      "family-review__row family-review__row--bucket" +
                      (active ? " family-review__row--active" : "")
                    }
                    onClick={() => {
                      if (bucketFilter === "unmatched_all") return;
                      setSelectedFp(row.fingerprint);
                      setGalleryMode("fingerprint");
                      setViewerIndex(null);
                      if (row.prop_id) setSelectedId(row.prop_id);
                    }}
                  >
                    <span className="family-review__bucket-top">
                      <span className="family-review__bucket-top-left">
                        <span className="family-review__row-id mono">{shortFp(row.fingerprint)}</span>
                        <span
                          className={
                            "family-review-status " +
                            (row.match_class === "enrolled"
                              ? "family-review-status--enrolled"
                              : row.match_class === "catalog_only"
                                ? "family-review-status--merge"
                                : "family-review-status--pending")
                          }
                        >
                          {row.match_class || "unmatched"}
                        </span>
                      </span>
                      <span className="family-review__badge family-review__badge--videos" title="Videos in this bucket">
                        <span className="family-review__badge-n">{vids.toLocaleString()}</span>
                        <span className="family-review__badge-unit">vids</span>
                      </span>
                    </span>
                    <span className="family-review__bucket-bottom">
                      <span className="family-review__bucket-label" title={row.label || ""}>
                        {row.label || "—"}
                      </span>
                      <span
                        className={
                          "family-review__bucket-prop" +
                          (row.prop_id ? "" : " family-review__bucket-prop--none")
                        }
                        title={row.prop_id ? `Proposal ${row.prop_id}` : "No proposal card"}
                      >
                        {row.prop_id || "no prop"}
                      </span>
                    </span>
                  </button>
                );
              })
            : null}

          {scope === "sources"
            ? filteredSources.map((row: FamilyDiscoverySourceRow) => {
                const active = selectedSource === row.key;
                const vids = row.video_count ?? 0;
                const bucketsN = row.bucket_count ?? 0;
                return (
                  <button
                    key={row.key}
                    type="button"
                    className={
                      "family-review__row family-review__row--bucket" +
                      (active ? " family-review__row--active" : "")
                    }
                    onClick={() => {
                      setSelectedSource(row.key);
                      setGalleryMode("source");
                      setViewerIndex(null);
                    }}
                    title={row.key}
                  >
                    <span className="family-review__bucket-top">
                      <span className="family-review__bucket-top-left">
                        <span className="family-review__row-id mono" title={row.label || row.key}>
                          {(row.label || row.key).length > 28
                            ? `${(row.label || row.key).slice(0, 26)}…`
                            : row.label || row.key}
                        </span>
                        <span
                          className={
                            "family-review-status " +
                            (row.kind === "video"
                              ? "family-review-status--merge"
                              : "family-review-status--pending")
                          }
                        >
                          {row.kind || "?"}
                        </span>
                      </span>
                      <span className="family-review__badge family-review__badge--videos" title="Videos from this source">
                        <span className="family-review__badge-n">{vids.toLocaleString()}</span>
                        <span className="family-review__badge-unit">vids</span>
                      </span>
                    </span>
                    <span className="family-review__bucket-bottom">
                      <span className="family-review__bucket-label factory-muted">
                        {bucketsN} bucket{bucketsN === 1 ? "" : "s"}
                      </span>
                    </span>
                  </button>
                );
              })
            : null}

          {scope === "proposals" && !filteredProposals.length ? (
            <div className="factory-empty">No proposals with videos in this filter.</div>
          ) : null}
          {scope === "buckets" && !filteredBuckets.length ? (
            <div className="factory-empty">No buckets in this filter.</div>
          ) : null}
          {scope === "sources" && !filteredSources.length ? (
            <div className="factory-empty">
              No sources yet. Rebuild exemplars with{" "}
              <code>index-exemplars</code> (v4) to extract LoadImage/LoadVideo paths.
            </div>
          ) : null}
        </aside>

        <section className="family-review__detail">
          {scope === "proposals" && loadingProp ? <div className="factory-muted">Loading…</div> : null}

          {scope === "proposals" && !loadingProp && prop ? (
            <>
              <header className="family-review__detail-head">
                <h2>{prop.id}</h2>
                <div className="factory-muted">
                  {prop.io_guess || "—"} · {prop.input_profile_guess || "—"} ·{" "}
                  {prop.chain_role_guess || "—"} · {prop.member_count ?? prop.members?.length ?? 0}{" "}
                  members · fp {shortFp(prop.fingerprint)}
                </div>
              </header>

              <div className="family-review__block family-review__form">
                <h3>Operator decision</h3>
                <label>
                  Status
                  <select
                    value={status}
                    onChange={(e) => setStatus(e.target.value as FamilyDiscoveryStatus)}
                    disabled={saving}
                  >
                    {STATUS_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Proposed family slug
                  <input
                    value={slug}
                    onChange={(e) => setSlug(e.target.value)}
                    placeholder="e.g. FB8VA5_LAYING"
                    disabled={saving || status === "skip"}
                  />
                </label>
                <label>
                  Merge into enrolled
                  <select
                    value={nearest}
                    onChange={(e) => setNearest(e.target.value)}
                    disabled={saving || status !== "merge"}
                  >
                    <option value="">—</option>
                    {enrolled.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Notes
                  <textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    rows={3}
                    disabled={saving}
                  />
                </label>
                <div className="family-review__form-actions">
                  <button type="button" disabled={saving} onClick={() => void save()}>
                    {saving ? "Saving…" : "Save decision"}
                  </button>
                  <span className="factory-muted">
                    Writes <code>{selectedId}.json</code>. Enroll via CLI when ready.
                  </span>
                </div>
              </div>

              <div className="family-review__block">
                <h3>Members</h3>
                <ul className="family-review__members">
                  {(prop.members || []).map((m) => (
                    <li key={m.path || m.name} className="mono" title={m.path || ""}>
                      [{m.source || "?"}] {m.name || m.path}
                    </li>
                  ))}
                </ul>
              </div>
            </>
          ) : null}

          {scope === "proposals" && !loadingProp && !prop ? (
            <div className="factory-empty">Select a proposal.</div>
          ) : null}

          {scope === "buckets" && !galleryMode ? (
            <div className="factory-empty">Select a bucket to load its video gallery.</div>
          ) : null}

          {scope === "sources" && !galleryMode ? (
            <div className="factory-empty">Select a source to load its cross-bucket gallery.</div>
          ) : null}

          {galleryMode || (scope === "proposals" && prop) ? (
            <div className="family-review__block">
              <div className="family-review__gallery-head">
                <h3>
                  Gallery{" "}
                  <span className="factory-muted">
                    {galleryItems.length}/{galleryTotal || (prop?.sample_videos || []).length || 0}
                    {galleryMode === "unmatched_all" ? " · all unclassified" : null}
                    {galleryMode === "fingerprint" && selectedFp
                      ? ` · ${shortFp(selectedFp)}`
                      : null}
                    {galleryMode === "source" && selectedSource
                      ? ` · src ${(selectedSource.length > 24 ? `${selectedSource.slice(0, 22)}…` : selectedSource)}`
                      : null}
                  </span>
                </h3>
                <div className="family-review__gallery-filters">
                  <label className="family-review__gallery-opt">
                    Sort
                    <select
                      value={effectiveGallerySort}
                      onChange={(e) => {
                        const v = e.target.value === "source" ? "source" : "newest";
                        setGallerySort(v);
                        writeGallerySort(v);
                      }}
                      aria-label="Sort gallery"
                    >
                      <option value="newest">Newest</option>
                      <option value="source">By source</option>
                    </select>
                  </label>
                  <label className="family-review__gallery-opt family-review__gallery-opt--check">
                    <input
                      type="checkbox"
                      checked={galleryGroupBySource}
                      onChange={(e) => {
                        const on = e.target.checked;
                        setGalleryGroupBySource(on);
                        writeGalleryGroup(on);
                        if (on && gallerySort === "newest") {
                          setGallerySort("source");
                          writeGallerySort("source");
                        }
                      }}
                    />
                    Group by source
                  </label>
                  <input
                    value={galleryQ}
                    onChange={(e) => setGalleryQ(e.target.value)}
                    placeholder="Filter name/source…"
                    aria-label="Filter gallery by name or source"
                  />
                  <input
                    value={galleryDate}
                    onChange={(e) => setGalleryDate(e.target.value)}
                    placeholder="Date prefix YYYY-MM"
                    aria-label="Filter gallery by date prefix"
                    className="mono"
                  />
                </div>
              </div>

              {scope === "proposals" &&
              galleryItems.length === 0 &&
              (prop?.sample_videos || []).length ? (
                <div className="family-review__samples">
                  {(prop?.sample_videos || []).map((s, i) => (
                    <div key={s.path || s.name || i} className="family-review__sample">
                      <div className="family-review__sample-cap mono" title={s.path || ""}>
                        {s.name || s.path}
                      </div>
                      {s.url ? (
                        <button
                          type="button"
                          className="family-review__video-btn"
                          onClick={() => openFromSamples(prop?.sample_videos || [], i)}
                        >
                          <video
                            className="family-review__video"
                            src={s.url}
                            muted
                            playsInline
                            preload="metadata"
                          />
                          <span className="family-review__video-play">Play</span>
                        </button>
                      ) : (
                        <div className="factory-muted">No preview URL</div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <>
                  {gallerySections.map((sec) => (
                    <div key={sec.key + String(sec.start)} className="family-review__gallery-section">
                      {galleryGroupBySource && sec.label ? (
                        <div className="family-review__gallery-group-head" title={sec.key}>
                          <span className="family-review__gallery-group-label mono">{sec.label}</span>
                          <span className="factory-muted">{sec.items.length}</span>
                        </div>
                      ) : null}
                      <div className="family-review__gallery-grid">
                        {sec.items.map((s, j) => {
                          const i = sec.start + j;
                          return (
                            <button
                              key={(s.path || s.name || "") + String(i)}
                              type="button"
                              className="family-review__gallery-tile"
                              onClick={() => openViewer(i)}
                              title={
                                [s.source_label || s.source_key, s.path || s.name]
                                  .filter(Boolean)
                                  .join(" · ") || undefined
                              }
                            >
                              {s.thumb_url || s.url ? (
                                s.thumb_url ? (
                                  <img src={s.thumb_url} alt="" loading="lazy" />
                                ) : (
                                  <video src={s.url || undefined} muted playsInline preload="metadata" />
                                )
                              ) : (
                                <span className="factory-muted">no thumb</span>
                              )}
                              <span className="family-review__gallery-cap mono">
                                {s.date ? `${s.date} · ` : ""}
                                {!galleryGroupBySource && (s.source_label || s.source_key)
                                  ? `${String(s.source_label || s.source_key).slice(0, 18)} · `
                                  : ""}
                                {s.name || "video"}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                  <div ref={sentinelRef} className="family-review__gallery-sentinel">
                    {galleryLoading
                      ? "Loading…"
                      : galleryItems.length < galleryTotal
                        ? "Scroll for more"
                        : galleryTotal
                          ? "End"
                          : ""}
                  </div>
                </>
              )}
            </div>
          ) : null}
        </section>
      </div>

      {lightboxItem?.url && viewerIndex != null ? (
        <div
          className="family-review__lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={lightboxItem.name || "Sample video"}
          onClick={() => setViewerIndex(null)}
        >
          <div className="family-review__lightbox-panel" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="family-review__lightbox-close"
              onClick={() => setViewerIndex(null)}
              aria-label="Close"
              title="Close"
            >
              ×
            </button>
            <div className="family-review__lightbox-head">
              <div className="mono family-review__lightbox-title" title={lightboxItem.path || ""}>
                {viewerIndex + 1}/{lightboxItems.length}
                {galleryTotal > lightboxItems.length ? ` (of ${galleryTotal})` : ""} ·{" "}
                {lightboxItem.source_label || lightboxItem.source_key
                  ? `${lightboxItem.source_label || lightboxItem.source_key} · `
                  : ""}
                {lightboxItem.name || "video"}
              </div>
              <div className="family-review__lightbox-controls">
                <button
                  type="button"
                  className="drt-btn"
                  disabled={viewerIndex <= 0}
                  onClick={() => void goViewer(-1)}
                >
                  Prev
                </button>
                <button
                  type="button"
                  className="drt-btn"
                  disabled={viewerIndex >= lightboxItems.length - 1 && lightboxItems.length >= galleryTotal}
                  onClick={() => void goViewer(1)}
                >
                  Next
                </button>
                <label className="family-review__autoplay">
                  <input
                    type="checkbox"
                    checked={autoplay}
                    onChange={(e) => {
                      const on = e.target.checked;
                      setAutoplay(on);
                      writeAutoplay(on);
                    }}
                  />
                  Autoplay
                </label>
                <button
                  type="button"
                  className={
                    "family-review__end-mode-toggle" +
                    (endMode === "repeat"
                      ? " family-review__end-mode-toggle--repeat"
                      : " family-review__end-mode-toggle--advance")
                  }
                  title={
                    endMode === "repeat"
                      ? "End behavior: repeat (click for advance)"
                      : "End behavior: advance (click for repeat)"
                  }
                  aria-label={
                    endMode === "repeat"
                      ? "End behavior: repeat. Click to advance to next"
                      : "End behavior: advance to next. Click to repeat"
                  }
                  aria-pressed={endMode === "repeat"}
                  onClick={() => {
                    const next = nextEndMode(endMode);
                    setEndMode(next);
                    writeEndMode(next);
                  }}
                >
                  <span className="family-review__end-mode-icon">
                    {endMode === "repeat" ? <IconEndRepeat /> : <IconEndAdvance />}
                  </span>
                </button>
              </div>
            </div>
            <video
              ref={videoRef}
              key={lightboxItem.url}
              className="family-review__lightbox-video"
              src={lightboxItem.url}
              controls
              playsInline
              loop={endMode === "repeat"}
              autoPlay={autoplay}
              onEnded={() => {
                if (endModeRef.current === "repeat") {
                  const v = videoRef.current;
                  if (v) {
                    v.currentTime = 0;
                    if (autoplay) void v.play().catch(() => undefined);
                  }
                  return;
                }
                void goViewer(1);
              }}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}
