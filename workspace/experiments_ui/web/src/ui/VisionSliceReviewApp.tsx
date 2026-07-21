import React, { useEffect, useMemo, useRef, useState } from "react";
import { fetchVisionSliceCaptions } from "./api";
import { PageHeader } from "./PageHeader";
import type {
  VisionSliceAsset,
  VisionSliceAssetQuality,
  VisionSliceCaptionRow,
  VisionSliceCaptionsResponse,
  VisionSliceExcerpt,
  VisionSliceFrameQuality,
  VisionSliceVariantMeta,
} from "./types";

function fmtRange(t0: unknown, t1: unknown): string {
  const a = typeof t0 === "number" ? t0.toFixed(1) : String(t0 ?? "?");
  const b = typeof t1 === "number" ? t1.toFixed(1) : String(t1 ?? "?");
  return `${a}–${b}s`;
}

function fmtWall(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s)) return "—";
  if (s < 60) return `${s.toFixed(0)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return `${m}m ${r}s`;
}

function fmtPct(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return "—";
  return `${p.toFixed(0)}%`;
}

function fmtRate(r: number | null | undefined): string {
  if (r == null || !Number.isFinite(r)) return "—";
  return `${(r * 100).toFixed(0)}% empty`;
}

function fmtQ(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function meanQ(q: VisionSliceAssetQuality | null | undefined, key: keyof VisionSliceAssetQuality): number | null {
  if (!q) return null;
  const block = q[key];
  if (block && typeof block === "object" && typeof (block as { mean?: number }).mean === "number") {
    return (block as { mean: number }).mean;
  }
  return null;
}

type AssetSortMode = "default" | "sharpest" | "most_artifacted" | "most_stable";

function sortAssets(assets: VisionSliceAsset[], mode: AssetSortMode): VisionSliceAsset[] {
  const copy = [...assets];
  if (mode === "default") {
    return copy.sort((a, b) => {
      const ae = a.excerpts?.length ? 1 : 0;
      const be = b.excerpts?.length ? 1 : 0;
      if (ae !== be) return be - ae;
      return a.basename.localeCompare(b.basename);
    });
  }
  return copy.sort((a, b) => {
    if (mode === "sharpest") {
      return (meanQ(b.quality, "sharpness") ?? -1) - (meanQ(a.quality, "sharpness") ?? -1);
    }
    if (mode === "most_artifacted") {
      return (meanQ(b.quality, "artifacting") ?? -1) - (meanQ(a.quality, "artifacting") ?? -1);
    }
    // most_stable
    return (meanQ(b.quality, "convergence") ?? -1) - (meanQ(a.quality, "convergence") ?? -1);
  });
}

/** True when every selected variant has at least one non-empty caption on this asset. */
export function assetCoveredByVariants(
  asset: VisionSliceAsset,
  variantIds: string[],
): boolean {
  if (!variantIds.length) return true;
  for (const vid of variantIds) {
    let found = false;
    for (const s of asset.slices || []) {
      const cell = s.captions?.[vid];
      const text = (cell?.caption || (vid === "base_caption" ? s.caption : "") || "").trim();
      if (text) {
        found = true;
        break;
      }
    }
    if (!found) return false;
  }
  return true;
}

function QualityChips({
  q,
  compact,
}: {
  q: VisionSliceFrameQuality | VisionSliceAssetQuality | null | undefined;
  compact?: boolean;
}) {
  if (!q) return null;
  const sharp =
    "sharpness" in q && q.sharpness && typeof q.sharpness === "object"
      ? (q.sharpness as { mean?: number }).mean
      : (q as VisionSliceFrameQuality).sharpness;
  const conv =
    "convergence" in q && q.convergence && typeof q.convergence === "object"
      ? (q.convergence as { mean?: number }).mean
      : (q as VisionSliceFrameQuality).convergence;
  const art =
    "artifacting" in q && q.artifacting && typeof q.artifacting === "object"
      ? (q.artifacting as { mean?: number }).mean
      : (q as VisionSliceFrameQuality).artifacting;
  if (sharp == null && conv == null && art == null) return null;
  return (
    <span className={"vision-slice-qchips" + (compact ? " is-compact" : "")}>
      <span title="Sharpness">sh {fmtQ(sharp)}</span>
      <span title="Convergence (temporal stability)">cv {fmtQ(conv)}</span>
      <span title="Artifacting (higher = worse)">ar {fmtQ(art)}</span>
    </span>
  );
}

function shortVariantLabel(v: VisionSliceVariantMeta): string {
  if (v.label) return v.label;
  const parts = [v.id];
  if (v.task) parts.push(v.task);
  return parts.join(" · ");
}

/** Max variants shown side-by-side in the compare pane. */
export const MAX_COMPARE_VARIANTS = 5;

export type VariantPreset = {
  id: string;
  label: string;
  /** Short help for title/tooltip. */
  hint?: string;
  match: (v: VisionSliceVariantMeta) => boolean;
};

const ID = (v: VisionSliceVariantMeta) => v.id.toLowerCase();
const TASK = (v: VisionSliceVariantMeta) => String(v.task || "").toLowerCase();
const LABEL = (v: VisionSliceVariantMeta) => String(v.label || "").toLowerCase();

export const VARIANT_PRESETS: VariantPreset[] = [
  {
    id: "cohort",
    label: "Cohort",
    hint: "Large-cohort runs (cohort_*)",
    match: (v) => ID(v).startsWith("cohort_"),
  },
  {
    id: "tags",
    label: "Tags",
    hint: "PromptGen / Danbooru tag variants",
    match: (v) =>
      ID(v).includes("tag") ||
      TASK(v).includes("tag") ||
      LABEL(v).includes("· tags") ||
      LABEL(v).includes(" tags"),
  },
  {
    id: "more_detailed",
    label: "More detailed",
    hint: "more_detailed_caption runs",
    match: (v) =>
      ID(v).includes("more_detailed") || TASK(v).includes("more_detailed"),
  },
  {
    id: "spike",
    label: "Spike12",
    hint: "Non-cohort spike variants",
    match: (v) => !ID(v).startsWith("cohort_"),
  },
  {
    id: "winner_vs_tags",
    label: "Winner + tags",
    hint: "Florence-2-large more_detailed + tag models",
    match: (v) => {
      const id = ID(v);
      if (id === "cohort_large_more_detailed" || id === "florence_large_more_detailed") {
        return true;
      }
      return id.includes("tag") || TASK(v).includes("tag");
    },
  },
  {
    id: "cog",
    label: "Cog",
    hint: "CogFlorence variants",
    match: (v) => ID(v).includes("cog") || LABEL(v).includes("cog"),
  },
];

/** Prefer denser / named favorites when capping a preset to MAX. */
export function rankVariantForCompare(v: VisionSliceVariantMeta): number {
  const id = ID(v);
  let score = typeof v.caption_count === "number" ? v.caption_count : 0;
  if (id === "cohort_large_more_detailed") score += 10_000;
  if (id === "florence_large_more_detailed") score += 9_000;
  if (id === "florence_cog_more_detailed") score += 8_500;
  if (id.includes("pg_large_tags") || id.includes("cohort_pg_large_tags")) score += 8_000;
  if (id.includes("pg_tags") || id.includes("cohort_pg_tags")) score += 7_500;
  if (id.startsWith("cohort_x2_")) score += 2_000;
  if (id.startsWith("cohort_")) score += 500;
  return score;
}

export function pickVariantIds(
  variants: VisionSliceVariantMeta[],
  match: (v: VisionSliceVariantMeta) => boolean,
  limit: number = MAX_COMPARE_VARIANTS,
): string[] {
  return [...variants]
    .filter(match)
    .sort((a, b) => rankVariantForCompare(b) - rankVariantForCompare(a) || a.id.localeCompare(b.id))
    .slice(0, Math.max(1, limit))
    .map((v) => v.id);
}

/** Sensible default when the page loads (≤ MAX_COMPARE_VARIANTS). */
export function defaultEnabledVariantIds(
  variants: VisionSliceVariantMeta[],
  limit: number = MAX_COMPARE_VARIANTS,
): string[] {
  if (!variants.length) return [];
  const cohort = pickVariantIds(variants, (v) => ID(v).startsWith("cohort_"), limit);
  if (cohort.length) return cohort;
  const winnerTags = pickVariantIds(
    variants,
    (v) => {
      const id = ID(v);
      return (
        id === "florence_large_more_detailed" ||
        id.includes("tag") ||
        id.includes("cog_more_detailed")
      );
    },
    limit,
  );
  if (winnerTags.length) return winnerTags;
  return pickVariantIds(variants, () => true, limit);
}

/** Enable `id`; if over the cap, drop the oldest enabled id that isn't `id`. */
export function enableVariantCapped(
  prev: string[],
  id: string,
  limit: number = MAX_COMPARE_VARIANTS,
): string[] {
  if (prev.includes(id)) return prev;
  const next = [...prev, id];
  while (next.length > limit) next.shift();
  return next;
}

/** Prefer comma-separated Danbooru / PromptGen tag lists when present. */
export function parseDanbooruTags(caption: string): string[] {
  const text = (caption || "").trim();
  if (!text || text.startsWith("[dry-run]")) return [];
  const commas = (text.match(/,/g) || []).length;
  if (commas < 2) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const part of text.split(",")) {
    const t = part.replace(/\s+/g, " ").trim().toLowerCase();
    // Reject prose clauses falsely split on commas (mixed_plus / more_detailed).
    if (!t || t.length < 2 || t.length > 48 || seen.has(t)) continue;
    if (t.split(" ").length > 4) continue;
    if (/[.!?;:]/.test(t)) continue;
    seen.add(t);
    out.push(t);
  }
  // Need a real tag list, not a few leftover fragments from prose.
  if (out.length < 3) return [];
  const longFrac = out.filter((t) => t.length > 28).length / out.length;
  if (longFrac > 0.35) return [];
  return out;
}

/** True when this looks like a Danbooru/PromptGen tag list (not prose fragments). */
export function isTagListLike(tags: string[]): boolean {
  if (tags.length < 3) return false;
  let long = 0;
  let wordy = 0;
  for (const t of tags) {
    if (t.length > 40) long += 1;
    if (t.split(/\s+/).length > 4) wordy += 1;
  }
  if (long / tags.length > 0.25) return false;
  if (wordy / tags.length > 0.25) return false;
  return true;
}

export function variantLooksLikeTagModel(v: {
  id?: string;
  task?: string | null;
  label?: string | null;
}): boolean {
  const id = String(v.id || "").toLowerCase();
  const task = String(v.task || "").toLowerCase();
  const label = String(v.label || "").toLowerCase();
  if (id.includes("tag") || task.includes("tag")) return true;
  if (label.includes("· tags") || label.endsWith(" tags") || label.includes(" tags")) {
    return true;
  }
  return false;
}

export type TagCompareResult = {
  common: string[];
  /** Tags present in ≥2 tag models (softer than all-must-match). */
  shared?: string[];
  uniqueByVariant: Record<string, string[]>;
  /** True when at least two variants contributed tags. */
  comparable: boolean;
  /** Variant ids included in the overlap (tag-like only). */
  comparedIds?: string[];
  /** Variant ids skipped because they are prose / non-tag. */
  skippedIds?: string[];
};

export function compareTagsByVariant(
  tagsByVariant: Record<string, string[]>,
  opts?: { preferTagModels?: string[] },
): TagCompareResult {
  const prefer = new Set(opts?.preferTagModels || []);
  const skippedIds: string[] = [];
  let entries = Object.entries(tagsByVariant).filter(([id, tags]) => {
    if (!tags.length || !isTagListLike(tags)) {
      skippedIds.push(id);
      return false;
    }
    return true;
  });
  if (prefer.size) {
    const preferredEntries = entries.filter(([id]) => prefer.has(id));
    if (preferredEntries.length >= 2) {
      for (const [id] of entries) {
        if (!prefer.has(id)) skippedIds.push(id);
      }
      entries = preferredEntries;
    }
  }
  if (entries.length === 0) {
    return { common: [], uniqueByVariant: {}, comparable: false, comparedIds: [], skippedIds };
  }
  if (entries.length === 1) {
    const [id, tags] = entries[0];
    return {
      common: [],
      shared: [],
      uniqueByVariant: { [id]: [...tags] },
      comparable: false,
      comparedIds: [id],
      skippedIds,
    };
  }
  const sets = entries.map(([id, tags]) => [id, new Set(tags)] as const);
  const common: string[] = [];
  const shared: string[] = [];
  const allTags = new Set<string>();
  for (const [, s] of sets) {
    for (const t of s) allTags.add(t);
  }
  for (const tag of allTags) {
    let hit = 0;
    for (const [, s] of sets) {
      if (s.has(tag)) hit += 1;
    }
    if (hit === sets.length) common.push(tag);
    else if (hit >= 2) shared.push(tag);
  }
  common.sort();
  shared.sort();
  const commonSet = new Set(common);
  const uniqueByVariant: Record<string, string[]> = {};
  for (const [id, set] of sets) {
    uniqueByVariant[id] = [...set].filter((t) => !commonSet.has(t)).sort();
  }
  return {
    common,
    shared,
    uniqueByVariant,
    comparable: true,
    comparedIds: entries.map(([id]) => id),
    skippedIds,
  };
}

function TagChipList({
  tags,
  tone,
  empty,
}: {
  tags: string[];
  tone: "common" | "unique";
  empty?: string;
}) {
  if (!tags.length) {
    return <span className="vision-slice-tags__empty">{empty || "—"}</span>;
  }
  return (
    <ul className={"vision-slice-tags__list tone-" + tone}>
      {tags.map((t) => (
        <li key={t} className={"vision-slice-tag tone-" + tone}>
          {t}
        </li>
      ))}
    </ul>
  );
}

export function VisionSliceReviewApp() {
  const [data, setData] = useState<VisionSliceCaptionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedRel, setSelectedRel] = useState<string | null>(null);
  const [activeSliceIdx, setActiveSliceIdx] = useState(0);
  const [activeExcerptIdx, setActiveExcerptIdx] = useState(0);
  const [enabledVariants, setEnabledVariants] = useState<string[]>([]);
  const [variantsOpen, setVariantsOpen] = useState(false);
  const [assetSort, setAssetSort] = useState<AssetSortMode>("default");
  /** Hide clips that lack captions for the currently enabled variants (avoids empty cohort columns). */
  const [onlyCovered, setOnlyCovered] = useState(true);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const selectedRelRef = useRef<string | null>(null);
  const enabledVariantsRef = useRef<string[]>([]);

  useEffect(() => {
    selectedRelRef.current = selectedRel;
  }, [selectedRel]);
  useEffect(() => {
    enabledVariantsRef.current = enabledVariants;
  }, [enabledVariants]);

  function applyCaptionsPayload(res: VisionSliceCaptionsResponse, opts?: { preserveSelection?: boolean }) {
    setData(res);
    setError(null);
    const preserve = opts?.preserveSelection !== false;
    if (!preserve || !selectedRelRef.current) {
      const withEx =
        res.assets?.find((a) => (a.excerpts?.length || 0) > 0)?.asset_relpath ||
        res.assets?.[0]?.asset_relpath;
      if (withEx) setSelectedRel(withEx);
    } else if (!res.assets.some((a) => a.asset_relpath === selectedRelRef.current)) {
      setSelectedRel(res.assets[0]?.asset_relpath ?? null);
    }
    setEnabledVariants((prev) => {
      const ids = (res.variants || []).map((v) => v.id);
      const keep = (prev.length ? prev : enabledVariantsRef.current).filter((id) =>
        ids.includes(id),
      );
      return keep.length
        ? keep.slice(0, MAX_COMPARE_VARIANTS)
        : defaultEnabledVariantIds(res.variants || []);
    });
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void fetchVisionSliceCaptions()
      .then((res) => {
        if (cancelled) return;
        applyCaptionsPayload(res, { preserveSelection: false });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Live poll while any variant is still running.
  useEffect(() => {
    const ms = data?.stats?.poll_suggested_ms;
    if (!ms || ms < 2000) return;
    const id = window.setInterval(() => {
      void fetchVisionSliceCaptions()
        .then((res) => applyCaptionsPayload(res))
        .catch(() => {
          /* keep last good payload */
        });
    }, ms);
    return () => window.clearInterval(id);
  }, [data?.stats?.poll_suggested_ms, data?.stats?.running_count]);

  const variants: VisionSliceVariantMeta[] = data?.variants?.length
    ? data.variants
    : [{ id: "base_caption", label: "base_caption" }];

  const visibleVariants = useMemo(
    () => variants.filter((v) => enabledVariants.includes(v.id)),
    [variants, enabledVariants],
  );

  const coveredAssets = useMemo(() => {
    const all = data?.assets || [];
    if (!onlyCovered || !enabledVariants.length) return all;
    return all.filter((a) => assetCoveredByVariants(a, enabledVariants));
  }, [data?.assets, onlyCovered, enabledVariants]);

  const listedAssets = useMemo(
    () => sortAssets(coveredAssets, assetSort),
    [coveredAssets, assetSort],
  );

  // Keep selection inside the filtered list.
  useEffect(() => {
    if (!listedAssets.length) return;
    if (!selectedRel || !listedAssets.some((a) => a.asset_relpath === selectedRel)) {
      const withEx =
        listedAssets.find((a) => (a.excerpts?.length || 0) > 0)?.asset_relpath ||
        listedAssets[0]?.asset_relpath;
      if (withEx) setSelectedRel(withEx);
    }
  }, [listedAssets, selectedRel]);

  const runningVariants = useMemo(
    () => variants.filter((v) => v.status === "running"),
    [variants],
  );

  const asset: VisionSliceAsset | null = useMemo(() => {
    if (!data?.assets?.length || !selectedRel) return null;
    return data.assets.find((a) => a.asset_relpath === selectedRel) ?? null;
  }, [data, selectedRel]);

  const excerpts: VisionSliceExcerpt[] = asset?.excerpts?.length ? asset.excerpts : [];

  const activeExcerpt: VisionSliceExcerpt | null = useMemo(() => {
    if (!excerpts.length) return null;
    return excerpts.find((e) => e.index === activeExcerptIdx) ?? excerpts[0] ?? null;
  }, [excerpts, activeExcerptIdx]);

  const visibleSlices = useMemo(() => {
    if (!asset) return [];
    if (!excerpts.length) return asset.slices;
    return asset.slices.filter((s) => (s.excerpt_index ?? 0) === (activeExcerpt?.index ?? 0));
  }, [asset, excerpts, activeExcerpt]);

  const activeSlice: VisionSliceCaptionRow | null =
    visibleSlices[activeSliceIdx] ? visibleSlices[activeSliceIdx] : null;

  // Prefer 10s excerpt MP4s whenever present — never fall back to the full source.
  const playbackUrl = useMemo(() => {
    if (activeSlice?.excerpt_video_url) return activeSlice.excerpt_video_url;
    if (activeExcerpt?.video_url) return activeExcerpt.video_url;
    if (excerpts.length) {
      const fallback =
        excerpts.find((e) => e.index === activeExcerptIdx)?.video_url ||
        excerpts[0]?.video_url;
      if (fallback) return fallback;
    }
    return asset?.video_url || "";
  }, [activeSlice, activeExcerpt, excerpts, activeExcerptIdx, asset]);

  const playingExcerpt = Boolean(
    excerpts.length > 0 &&
      playbackUrl &&
      playbackUrl !== asset?.video_url,
  );
  useEffect(() => {
    setActiveSliceIdx(0);
    setActiveExcerptIdx(asset?.excerpts?.[0]?.index ?? 0);
  }, [selectedRel]);

  useEffect(() => {
    setActiveSliceIdx(0);
  }, [activeExcerptIdx]);

  function seekToSlice(idx: number) {
    setActiveSliceIdx(idx);
    const slice = visibleSlices[idx];
    const v = videoRef.current;
    if (!v || !slice) return;
    const local =
      typeof slice.excerpt_local_t === "number"
        ? slice.excerpt_local_t
        : typeof slice.frame_t === "number" && activeExcerpt?.source_t0 != null
          ? slice.frame_t - Number(activeExcerpt.source_t0)
          : typeof slice.frame_t === "number"
            ? slice.frame_t
            : Number(slice.t0) || 0;
    try {
      v.currentTime = Math.max(0, local);
      void v.play().catch(() => undefined);
    } catch {
      /* ignore seek errors */
    }
  }

  function toggleVariant(id: string) {
    setEnabledVariants((prev) => {
      if (prev.includes(id)) {
        if (prev.length <= 1) return prev;
        return prev.filter((x) => x !== id);
      }
      return enableVariantCapped(prev, id, MAX_COMPARE_VARIANTS);
    });
  }

  function applyPreset(preset: VariantPreset) {
    const ids = pickVariantIds(variants, preset.match, MAX_COMPARE_VARIANTS);
    if (!ids.length) return;
    setEnabledVariants(ids);
  }

  const availablePresets = useMemo(() => {
    return VARIANT_PRESETS.filter((p) => variants.some((v) => p.match(v)));
  }, [variants]);

  function captionFor(slice: VisionSliceCaptionRow, variantId: string): string {
    const fromMap = slice.captions?.[variantId]?.caption;
    if (fromMap) return fromMap;
    if (variantId === "base_caption" && slice.caption) return slice.caption;
    return "";
  }

  function tagsFor(slice: VisionSliceCaptionRow, variantId: string): string[] {
    const fromMap = slice.captions?.[variantId]?.tags;
    const caption = captionFor(slice, variantId);
    const fromCaption = parseDanbooruTags(caption);
    // Prefer danbooru parse from caption when it looks like a tag list;
    // stored tags may be legacy word-tokens.
    if (fromCaption.length) return fromCaption;
    if (Array.isArray(fromMap) && fromMap.length) {
      return fromMap.map((t) => String(t).toLowerCase());
    }
    return [];
  }

  const activeTagCompare = useMemo(() => {
    if (!activeSlice || visibleVariants.length < 2) {
      return null;
    }
    const preferTagModels = visibleVariants
      .filter((v) => variantLooksLikeTagModel(v))
      .map((v) => v.id);
    const tagsByVariant: Record<string, string[]> = {};
    for (const v of visibleVariants) {
      tagsByVariant[v.id] = tagsFor(activeSlice, v.id);
    }
    const cmp = compareTagsByVariant(tagsByVariant, { preferTagModels });
    if (!cmp.comparable && !Object.values(tagsByVariant).some((t) => t.length)) {
      return null;
    }
    // Still show panel when we skipped down to <2 tag models (explain why).
    if (!cmp.comparable && (cmp.skippedIds?.length || 0) > 0) {
      return cmp;
    }
    return cmp;
  }, [activeSlice, visibleVariants]);

  const subtitle = data
    ? [
        `${data.asset_count ?? 0} videos`,
        `${data.slice_count ?? data.caption_count ?? 0} slices`,
        `${(data.variants || []).length} variants`,
        data.stats?.running_count
          ? `${data.stats.running_count} running`
          : data.stats?.complete_count != null
            ? `${data.stats.complete_count} complete`
            : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "V1 time-slice caption spike";

  return (
    <div className="vision-slice layout">
      <PageHeader
        title="Vision slices"
        subtitle={subtitle}
        actions={
          <>
            <a className="btn" href="/vision/tag-judge">
              Tag judge
            </a>
            <button
              type="button"
              className="btn"
              disabled={loading}
              onClick={() => {
                setLoading(true);
                void fetchVisionSliceCaptions()
                  .then((res) => applyCaptionsPayload(res))
                  .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
                  .finally(() => setLoading(false));
              }}
            >
              Refresh
            </button>
          </>
        }
      />

      {error ? <div className="vision-slice-error">{error}</div> : null}
      {loading && !data ? <div className="vision-slice-empty">Loading…</div> : null}
      {!loading && data && data.asset_count === 0 && !(data.variants || []).length ? (
        <div className="vision-slice-empty">
          No slice captions found. Expected{" "}
          <code>output/_status/vision_slice_captions__*.ndjson</code> (or legacy{" "}
          <code>vision_slice_captions.ndjson</code>).
        </div>
      ) : null}

      {data && ((data.asset_count ?? 0) > 0 || (data.variants || []).length > 0) ? (
        <>
          <div className="vision-slice-stats" aria-label="Run statistics">
            <div className="vision-slice-stats__roll">
              <span>
                Expected frames{" "}
                <strong>{data.stats?.expected_frames ?? data.slice_count ?? "—"}</strong>
              </span>
              <span>
                Complete{" "}
                <strong>
                  {data.stats?.complete_count ?? 0}/{data.stats?.variant_count ?? variants.length}
                </strong>
              </span>
              {data.stats?.running_count ? (
                <span className="vision-slice-stats__running">
                  Running <strong>{data.stats.running_count}</strong>
                  {runningVariants[0] ? (
                    <>
                      {" "}
                      · {runningVariants[0].id}{" "}
                      {runningVariants[0].caption_count ?? 0}
                      {runningVariants[0].frame_count
                        ? `/${runningVariants[0].frame_count}`
                        : ""}
                    </>
                  ) : null}
                </span>
              ) : null}
              {data.stats?.video_quality ? (
                <span title="Corpus mean classical video quality">
                  VQ sh {fmtQ(data.stats.video_quality.sharpness)} · cv{" "}
                  {fmtQ(data.stats.video_quality.convergence)} · ar{" "}
                  {fmtQ(data.stats.video_quality.artifacting)}
                </span>
              ) : null}
            </div>
            {runningVariants.length || variants.some((v) => (v.caption_count || 0) > 0) ? (
              <div className="vision-slice-stats__runs" role="list">
                {[...variants]
                  .filter((v) => v.status === "running" || (v.caption_count || 0) > 0)
                  .sort((a, b) => {
                    const ar = a.status === "running" ? 0 : a.status === "complete" ? 1 : 2;
                    const br = b.status === "running" ? 0 : b.status === "complete" ? 1 : 2;
                    if (ar !== br) return ar - br;
                    return (b.caption_count || 0) - (a.caption_count || 0);
                  })
                  .slice(0, 8)
                  .map((v) => {
                    const done = v.caption_count ?? 0;
                    const total = v.frame_count || data.stats?.expected_frames || null;
                    const pct =
                      v.progress_pct ??
                      (total && total > 0 ? Math.min(100, (done / total) * 100) : null);
                    return (
                      <div
                        key={v.id}
                        className={
                          "vision-slice-stats__run" +
                          (v.status === "running" ? " is-running" : "") +
                          (enabledVariants.includes(v.id) ? " is-selected" : "")
                        }
                        role="listitem"
                        title={shortVariantLabel(v)}
                      >
                        <div className="vision-slice-stats__run-top">
                          <span className="vision-slice-stats__run-id">{v.id}</span>
                          <span className="vision-slice-stats__run-meta">
                            {done}
                            {total ? `/${total}` : ""}
                            {pct != null ? ` · ${fmtPct(pct)}` : ""}
                            {v.error_count ? ` · ${v.error_count} err` : ""}
                            {v.captions_per_min != null
                              ? ` · ${v.captions_per_min.toFixed(1)}/min`
                              : ""}
                            {v.wall_s != null ? ` · ${fmtWall(v.wall_s)}` : ""}
                          </span>
                        </div>
                        <div
                          className="vision-slice-stats__bar"
                          aria-valuenow={pct ?? undefined}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          role="progressbar"
                        >
                          <span
                            style={{ width: `${pct != null ? Math.max(2, pct) : 0}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
              </div>
            ) : null}
            {visibleVariants.length > 0 ? (
              <div className="vision-slice-stats__compare" aria-label="Compare-set quality">
                <div className="vision-slice-stats__compare-label">Compare set</div>
                <div className="vision-slice-stats__compare-grid">
                  {visibleVariants.map((v) => {
                    const q = v.quality;
                    return (
                      <div key={v.id} className="vision-slice-stats__compare-cell">
                        <div className="vision-slice-stats__compare-id">{v.id}</div>
                        <div className="vision-slice-stats__compare-nums">
                          <span title="Mean caption length">
                            {q?.mean_chars != null ? `${Math.round(q.mean_chars)} ch` : "—"}
                          </span>
                          <span title="Empty caption rate">{fmtRate(q?.empty_rate)}</span>
                          <span title="Mean tag count">
                            {q?.mean_tags != null && q.mean_tags > 0
                              ? `${q.mean_tags.toFixed(1)} tags`
                              : "—"}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </div>

          {variants.length > 1 ? (
            <div
              className={
                "vision-slice-variants-panel" + (variantsOpen ? " is-open" : " is-collapsed")
              }
              aria-label="Caption variants"
            >
              <div className="vision-slice-variants-toolbar">
                <button
                  type="button"
                  className="vision-slice-variants-toggle"
                  aria-expanded={variantsOpen}
                  onClick={() => setVariantsOpen((o) => !o)}
                >
                  <span className="vision-slice-variants-toggle__chev" aria-hidden>
                    {variantsOpen ? "▾" : "▸"}
                  </span>
                  Models
                  <span className="vision-slice-variants-toolbar__meta">
                    {visibleVariants.length}/{MAX_COMPARE_VARIANTS}
                    {enabledVariants.length >= MAX_COMPARE_VARIANTS ? " · at cap" : ""}
                  </span>
                </button>
                <div className="vision-slice-presets" role="group" aria-label="Variant sets">
                  {availablePresets.map((p) => {
                    const count = variants.filter((v) => p.match(v)).length;
                    const activeCount = variants.filter(
                      (v) => p.match(v) && enabledVariants.includes(v.id),
                    ).length;
                    const isActive =
                      activeCount > 0 &&
                      activeCount === Math.min(count, MAX_COMPARE_VARIANTS) &&
                      enabledVariants.every((id) => {
                        const v = variants.find((x) => x.id === id);
                        return v ? p.match(v) : false;
                      });
                    return (
                      <button
                        key={p.id}
                        type="button"
                        className={
                          "vision-slice-preset-btn" + (isActive ? " is-active" : "")
                        }
                        title={p.hint || p.label}
                        onClick={() => applyPreset(p)}
                      >
                        {p.label}
                        <span className="vision-slice-preset-btn__n">
                          {Math.min(count, MAX_COMPARE_VARIANTS)}
                          {count > MAX_COMPARE_VARIANTS ? `/${count}` : ""}
                        </span>
                      </button>
                    );
                  })}
                  <button
                    type="button"
                    className="vision-slice-preset-btn"
                    title="Clear to a single variant"
                    onClick={() => {
                      const keep =
                        enabledVariants[0] ||
                        defaultEnabledVariantIds(variants, 1)[0] ||
                        variants[0]?.id;
                      if (keep) setEnabledVariants([keep]);
                    }}
                  >
                    Solo
                  </button>
                </div>
              </div>
              {variantsOpen ? (
                <div className="vision-slice-variants" aria-label="Toggle individual variants">
                  {variants.map((v) => {
                    const on = enabledVariants.includes(v.id);
                    const atCap = !on && enabledVariants.length >= MAX_COMPARE_VARIANTS;
                    return (
                      <label
                        key={v.id}
                        className={
                          "vision-slice-variant-toggle" +
                          (on ? " is-on" : "") +
                          (atCap ? " is-capped" : "")
                        }
                        title={
                          atCap
                            ? `At ${MAX_COMPARE_VARIANTS} max — enabling will drop the oldest`
                            : shortVariantLabel(v)
                        }
                      >
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() => toggleVariant(v.id)}
                        />
                        <span className="vision-slice-variant-toggle__label">
                          {shortVariantLabel(v)}
                        </span>
                        {typeof v.caption_count === "number" ? (
                          <span className="vision-slice-variant-toggle__count">
                            {v.caption_count}
                            {v.frame_count ? `/${v.frame_count}` : ""}
                            {v.status === "running" ? "…" : ""}
                          </span>
                        ) : null}
                      </label>
                    );
                  })}
                </div>
              ) : (
                <div className="vision-slice-variants-summary" title={enabledVariants.join(", ")}>
                  {visibleVariants.map((v) => (
                    <span key={v.id} className="vision-slice-variants-summary__chip">
                      {v.id}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ) : null}

          <div className="vision-slice-body">
            <aside className="vision-slice-list" aria-label="Assets">
              <div className="vision-slice-list__sort">
                <label>
                  Sort{" "}
                  <select
                    value={assetSort}
                    onChange={(e) => setAssetSort(e.target.value as AssetSortMode)}
                  >
                    <option value="default">Default</option>
                    <option value="sharpest">Sharpest</option>
                    <option value="most_stable">Most stable</option>
                    <option value="most_artifacted">Most artifacted</option>
                  </select>
                </label>
                <label className="vision-slice-list__filter" title="Hide clips that have no captions for the enabled models">
                  <input
                    type="checkbox"
                    checked={onlyCovered}
                    onChange={(e) => setOnlyCovered(e.target.checked)}
                  />
                  Covered only
                </label>
                <span className="vision-slice-list__count">
                  {listedAssets.length}
                  {data?.assets?.length != null && listedAssets.length !== data.assets.length
                    ? `/${data.assets.length}`
                    : ""}
                </span>
              </div>
              {listedAssets.map((a) => (
                  <button
                    key={a.asset_relpath}
                    type="button"
                    className={
                      "vision-slice-list__item" +
                      (a.asset_relpath === selectedRel ? " is-active" : "")
                    }
                    onClick={() => setSelectedRel(a.asset_relpath)}
                    title={a.asset_relpath}
                  >
                    <span className="vision-slice-list__name">{a.basename}</span>
                    <span className="vision-slice-list__meta">
                      {a.slice_count} slices
                      {a.excerpts?.length ? ` · ${a.excerpts.length} excerpts` : ""}
                      {a.has_whole ? " · whole" : ""}
                    </span>
                    <QualityChips q={a.quality} compact />
                  </button>
                ))}
              {!listedAssets.length ? (
                <div className="vision-slice-list__empty">
                  No clips have captions for the selected models. Uncheck “Covered only” or enable fewer variants.
                </div>
              ) : null}
            </aside>

            <section className="vision-slice-stage">
              {asset ? (
                <>
                  <div className="vision-slice-player">
                    {excerpts.length > 1 ? (
                      <div className="vision-slice-excerpts" aria-label="Excerpts">
                        {excerpts.map((ex) => (
                          <button
                            key={ex.index}
                            type="button"
                            className={
                              "vision-slice-excerpt-btn" +
                              ((activeExcerpt?.index ?? 0) === ex.index ? " is-active" : "")
                            }
                            onClick={() => setActiveExcerptIdx(ex.index)}
                          >
                            Ex {ex.index + 1}
                            {ex.source_t0 != null && ex.source_t1 != null
                              ? ` · ${fmtRange(ex.source_t0, ex.source_t1)}`
                              : ""}
                          </button>
                        ))}
                      </div>
                    ) : null}
                    <div className="vision-slice-video-frame">
                      <video
                        key={playbackUrl || "no-video"}
                        ref={videoRef}
                        className="vision-slice-video"
                        src={playbackUrl || undefined}
                        controls
                        playsInline
                        preload="metadata"
                      />
                    </div>
                    <div className="vision-slice-path" title={asset.asset_relpath}>
                      {playingExcerpt
                        ? `10s excerpt ${
                            (activeExcerpt?.index ?? activeExcerptIdx) + 1
                          }`
                        : "full source"}
                    </div>
                    {asset.quality ? (
                      <div className="vision-slice-asset-quality" aria-label="Video quality">
                        <div className="vision-slice-asset-quality__label">Quality</div>
                        <div className="vision-slice-asset-quality__bars">
                          {(
                            [
                              ["sharpness", "Sharp"],
                              ["convergence", "Conv"],
                              ["artifacting", "Art"],
                              ["exposure", "Exp"],
                              ["contrast", "Ctr"],
                            ] as const
                          ).map(([key, label]) => {
                            const m = meanQ(asset.quality, key);
                            const pct = m == null ? 0 : Math.max(0, Math.min(100, m * 100));
                            return (
                              <div key={key} className="vision-slice-asset-quality__row">
                                <span className="vision-slice-asset-quality__name">{label}</span>
                                <span className="vision-slice-asset-quality__bar" title={fmtQ(m)}>
                                  <span style={{ width: `${pct}%` }} />
                                </span>
                                <span className="vision-slice-asset-quality__val">{fmtQ(m)}</span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ) : null}
                  </div>

                  <div className="vision-slice-table">
                    <ul className="vision-slice-captions">
                      {visibleSlices.map((s, idx) => (
                        <li key={`${s.excerpt_index}-${s.slice}-${s.t0}-${s.t1}-${idx}`}>
                          <button
                            type="button"
                            className={
                              "vision-slice-cap" + (idx === activeSliceIdx ? " is-active" : "")
                            }
                            onClick={() => seekToSlice(idx)}
                          >
                            <span className="vision-slice-cap__when">
                              <span
                                className={
                                  "vision-slice-cap__kind kind-" + (s.slice || "window")
                                }
                              >
                                {s.slice || "window"}
                              </span>
                              {typeof s.excerpt_local_t === "number"
                                ? `@ ${s.excerpt_local_t.toFixed(1)}s`
                                : fmtRange(s.t0, s.t1)}
                              <QualityChips q={s.quality} compact />
                            </span>
                            {visibleVariants.length > 1 ? (
                              <div
                                className="vision-slice-cap__compare"
                                style={
                                  {
                                    "--vision-compare-cols": String(visibleVariants.length),
                                  } as React.CSSProperties
                                }
                              >
                                {visibleVariants.map((v) => {
                                  const text = captionFor(s, v.id);
                                  return (
                                    <div key={v.id} className="vision-slice-cap__col">
                                      <span className="vision-slice-cap__col-label">
                                        {v.id}
                                      </span>
                                      <span className="vision-slice-cap__text">
                                        {text || "—"}
                                      </span>
                                    </div>
                                  );
                                })}
                              </div>
                            ) : (
                              <span className="vision-slice-cap__text">
                                {captionFor(s, visibleVariants[0]?.id || "base_caption") ||
                                  s.caption ||
                                  "—"}
                              </span>
                            )}
                          </button>
                        </li>
                      ))}
                    </ul>
                    {activeTagCompare && visibleVariants.length > 1 ? (
                      <div className="vision-slice-tag-compare" aria-label="Tag comparison">
                        <div className="vision-slice-tag-compare__head">
                          Tag overlap (selected slice)
                          {activeTagCompare.comparedIds?.length ? (
                            <span className="vision-slice-tag-compare__meta">
                              {" "}
                              · {activeTagCompare.comparedIds.length} tag model
                              {activeTagCompare.comparedIds.length === 1 ? "" : "s"}
                            </span>
                          ) : null}
                        </div>
                        {activeTagCompare.skippedIds?.length ? (
                          <div className="vision-slice-tag-compare__note">
                            Ignoring prose models for overlap:{" "}
                            {activeTagCompare.skippedIds.join(", ")}
                          </div>
                        ) : null}
                        {!activeTagCompare.comparable ? (
                          <div className="vision-slice-tag-compare__note">
                            Need at least two tag-list models enabled (e.g. PromptGen · tags) to
                            compute overlap.
                          </div>
                        ) : null}
                        {activeTagCompare.comparable ? (
                          <>
                            <div className="vision-slice-tag-compare__block">
                              <div className="vision-slice-tag-compare__label">
                                In all tag models ({activeTagCompare.common.length})
                              </div>
                              <TagChipList
                                tags={activeTagCompare.common}
                                tone="common"
                                empty="none"
                              />
                            </div>
                            {(activeTagCompare.shared?.length || 0) > 0 ? (
                              <div className="vision-slice-tag-compare__block">
                                <div className="vision-slice-tag-compare__label">
                                  In ≥2 tag models ({activeTagCompare.shared!.length})
                                </div>
                                <TagChipList
                                  tags={activeTagCompare.shared || []}
                                  tone="common"
                                  empty="none"
                                />
                              </div>
                            ) : null}
                            <div
                              className="vision-slice-tag-compare__unique-grid"
                              style={
                                {
                                  "--vision-compare-cols": String(
                                    (activeTagCompare.comparedIds || []).length || 1,
                                  ),
                                } as React.CSSProperties
                              }
                            >
                              {(activeTagCompare.comparedIds || []).map((id) => (
                                <div key={id} className="vision-slice-tag-compare__block">
                                  <div className="vision-slice-tag-compare__label">
                                    Unique · {id} (
                                    {(activeTagCompare.uniqueByVariant[id] || []).length})
                                  </div>
                                  <TagChipList
                                    tags={activeTagCompare.uniqueByVariant[id] || []}
                                    tone="unique"
                                    empty="none"
                                  />
                                </div>
                              ))}
                            </div>
                          </>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </>
              ) : (
                <div className="vision-slice-empty">Select a video.</div>
              )}
            </section>
          </div>
        </>
      ) : null}
    </div>
  );
}
