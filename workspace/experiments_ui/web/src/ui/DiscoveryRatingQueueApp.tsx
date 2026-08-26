import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchDiscoveryRatingSampler,
  fetchDispositionCatalog,
  fetchDispositionSuggest,
  createWorkItems,
  recordBatchTriageComplete,
  runDispositionStep,
  saveDispositionCatalog,
  setAssetAppetite,
  setAssetRating,
  toggleAssetDisposition,
} from "./api";
import {
  patchCachedAppetite,
  patchCachedDisposition,
  patchCachedQuality,
  peekAssetRatings,
  prefetchAssetRatings,
  ratingsSeedFromCandidate,
  rememberAssetRatings,
  revalidateAssetRatings,
} from "./assetRatingsCache";
import { cachedEnsureThumbUrl, enqueueEnsureThumb } from "./ensureThumbQueue";
import { AppetiteBar, APPETITE_FACET_CYCLE, APPETITE_KEYMAP } from "./AppetiteBar";
import { DispositionBar, DispositionReasonsPanel, DispositionRouter } from "./DispositionBar";
import { DispositionCatalogEditor } from "./DispositionCatalogEditor";
import { DispositionStatusPanel } from "./DispositionStatusPanel";
import { discoveryLibraryHref } from "./discoveryDeepLink";
import {
  TRIM_CONTEXT_DISCOVERY_PLAYER,
  loadDiscoveryTrimAsync,
  persistDiscoveryTrimAsync,
} from "./discoveryTrimStorage";
import { PageHeader } from "./PageHeader";
import { useTrimPlaybackEnforcement } from "./useTrimPlayback";
import { VideoTrimControls, type VideoTrimPlaybackMode } from "./VideoTrimControls";
import { originGenerationBands, parseFps } from "./workProductTrim";
import type {
  Appetite,
  AppetiteFacet,
  DiscoveryAssetRatingsResponse,
  DiscoveryRatingSamplerCandidate,
  DiscoveryRatingSamplerResponse,
  DispositionCatalogMarker,
  DispositionCatalogResponse,
  DispositionOutcome,
  DispositionPromotions,
  DispositionReasonDetail,
  QualityAxis,
  QualityAxesMap,
} from "./types";
import { QUALITY_AXES, QUALITY_AXIS_LABELS } from "./types";

const QUEUE_LIMIT_KEY = "rating_queue_limit";
const DEFAULT_QUEUE_LIMIT = 15;
const QUEUE_LIMIT_OPTIONS = [5, 10, 15, 20, 25] as const;
const APPETITE_FACET_KEY = "appetite_facet";
const LOOP_PLAYBACK_KEY = "rating_queue_loop_playback";
const SELECTION_MODE_KEY = "rating_selection_mode";
const INCLUDE_DONE_KEY = "rating_include_done";
const SEARCH_QUERY_KEY = "rating_search_query";

type SelectionMode = "mixed" | "random" | "search" | "latest";
const SELECTION_MODES: { id: SelectionMode; label: string }[] = [
  { id: "mixed", label: "Mixed" },
  { id: "random", label: "Random" },
  { id: "search", label: "Search" },
  { id: "latest", label: "Latest" },
];

function loadStickyFacet(): AppetiteFacet {
  try {
    const raw = localStorage.getItem(APPETITE_FACET_KEY);
    if (raw === "both" || raw === "source" || raw === "processing") return raw;
  } catch {
    /* ignore */
  }
  return "both";
}

function loadQueueLimit(): number {
  try {
    const raw = localStorage.getItem(QUEUE_LIMIT_KEY);
    const n = raw ? parseInt(raw, 10) : DEFAULT_QUEUE_LIMIT;
    return QUEUE_LIMIT_OPTIONS.includes(n as (typeof QUEUE_LIMIT_OPTIONS)[number]) ? n : DEFAULT_QUEUE_LIMIT;
  } catch {
    return DEFAULT_QUEUE_LIMIT;
  }
}

function loadLoopPlayback(): boolean {
  try {
    const raw = localStorage.getItem(LOOP_PLAYBACK_KEY);
    if (raw === "0") return false;
    if (raw === "1") return true;
  } catch {
    /* ignore */
  }
  return true;
}

function loadSelectionMode(): SelectionMode {
  try {
    const raw = localStorage.getItem(SELECTION_MODE_KEY);
    if (raw === "mixed" || raw === "random" || raw === "search" || raw === "latest") return raw;
    if (raw === "heuristic" || raw === "stratified") return "mixed";
  } catch {
    /* ignore */
  }
  return "mixed";
}

function loadIncludeDone(): boolean {
  try {
    return localStorage.getItem(INCLUDE_DONE_KEY) === "1";
  } catch {
    return false;
  }
}

function loadSearchQuery(): string {
  try {
    return localStorage.getItem(SEARCH_QUERY_KEY) || "";
  } catch {
    return "";
  }
}

function emptyQualityAxes(): QualityAxesMap {
  return {};
}

function axesFromRatings(r: DiscoveryAssetRatingsResponse): QualityAxesMap {
  const raw = r.axes ?? r.explicit?.axes ?? null;
  const out: QualityAxesMap = {};
  if (!raw || typeof raw !== "object") return out;
  for (const axis of QUALITY_AXES) {
    const n = raw[axis];
    if (typeof n === "number" && n >= 1 && n <= 5) out[axis] = n;
  }
  return out;
}

function axesComplete(axes: QualityAxesMap): boolean {
  return QUALITY_AXES.every((axis) => {
    const n = axes[axis];
    return typeof n === "number" && n >= 1 && n <= 5;
  });
}

function aggregateFromAxes(axes: QualityAxesMap): number | null {
  const vals = QUALITY_AXES.map((a) => axes[a]).filter((n): n is number => typeof n === "number" && n >= 1);
  if (!vals.length) return null;
  return Math.round(vals.reduce((s, n) => s + n, 0) / vals.length);
}

/** Split explicit XMP stars from derived/external/inferred ratings for the quality row. */
function resolveQualityDisplay(r: DiscoveryAssetRatingsResponse): {
  explicit: number | null;
  derived: number | null;
  derivedLabel: string | null;
  axes: QualityAxesMap;
} {
  const axes = axesFromRatings(r);
  const explicitBlock = r.explicit;
  const explicitRating =
    explicitBlock && typeof explicitBlock === "object" && typeof explicitBlock.rating === "number" && explicitBlock.rating >= 1
      ? explicitBlock.rating
      : aggregateFromAxes(axes);
  if (axesComplete(axes) || explicitRating != null) {
    return {
      explicit: explicitRating ?? aggregateFromAxes(axes),
      derived: null,
      derivedLabel: null,
      axes,
    };
  }

  const disk =
    explicitBlock && typeof explicitBlock === "object" ? explicitBlock.verification?.xmp_on_disk : undefined;
  if (typeof disk === "number" && disk >= 1) {
    return {
      explicit: null,
      derived: disk,
      derivedLabel: "External XMP on disk — stars shown below are not your explicit rating yet",
      axes,
    };
  }

  const effective = r.rating_effective;
  if (typeof effective === "number" && effective >= 1) {
    let derivedLabel = "Derived from ratings index";
    const src = r.as_source?.inferred;
    const wf = r.workflow?.inferred;
    const rec = r.recipe?.inferred;
    if (src != null && (wf == null || src === effective)) {
      const n = r.as_source?.n;
      derivedLabel = n
        ? `Inferred from source material (${n} rated outputs)`
        : "Inferred from source material";
    } else if (wf != null) {
      const n = r.workflow?.n;
      derivedLabel = n
        ? `Inferred from workflow pattern (${n} rated outputs)`
        : "Inferred from workflow pattern";
    } else if (rec != null) {
      derivedLabel = "Inferred from shape recipe";
    }
    return { explicit: null, derived: Math.round(effective), derivedLabel, axes };
  }

  return { explicit: null, derived: null, derivedLabel: null, axes };
}

function applyQualityFromRatings(
  r: DiscoveryAssetRatingsResponse,
  setExplicit: (n: number | null) => void,
  setDerived: (n: number | null) => void,
  setDerivedLabel: (s: string | null) => void,
  setAxes: (a: QualityAxesMap) => void,
) {
  try {
    const q = resolveQualityDisplay(r);
    setExplicit(q.explicit);
    setDerived(q.derived);
    setDerivedLabel(q.derivedLabel);
    setAxes(q.axes);
  } catch {
    setExplicit(null);
    setDerived(null);
    setDerivedLabel(null);
    setAxes(emptyQualityAxes());
  }
}

function isRatingComplete(axes: QualityAxesMap, appetite: Appetite | null | undefined): boolean {
  return axesComplete(axes) && Boolean(appetite);
}

function applyTriageFromRatings(
  r: DiscoveryAssetRatingsResponse,
  setLastTriagedAt: (t: string | null) => void,
  setTriagePassCount: (n: number) => void,
) {
  setLastTriagedAt(r.last_triaged_at ?? null);
  setTriagePassCount(r.triage_pass_count ?? 0);
}

function applyJudgmentFromRatings(
  r: DiscoveryAssetRatingsResponse,
  candidate: DiscoveryRatingSamplerCandidate,
  setters: {
    setExplicitRating: (n: number | null) => void;
    setDerivedRating: (n: number | null) => void;
    setDerivedSourceLabel: (s: string | null) => void;
    setQualityAxes: (a: QualityAxesMap) => void;
    setAppetite: (a: Appetite | null) => void;
    setAppetiteFacet: (f: AppetiteFacet) => void;
    setDispositionMarkers: (m: string[]) => void;
    setDispositionUpdatedAt: (t: string | null) => void;
    setDispositionLastOutcome: (o: DispositionOutcome | null) => void;
    setReasonDetail: (d: Record<string, DispositionReasonDetail>) => void;
    setLastTriagedAt: (t: string | null) => void;
    setTriagePassCount: (n: number) => void;
  },
  markBatchRated: (relpath: string, axes: QualityAxesMap, app: Appetite | null | undefined) => void,
) {
  applyQualityFromRatings(
    r,
    setters.setExplicitRating,
    setters.setDerivedRating,
    setters.setDerivedSourceLabel,
    setters.setQualityAxes,
  );
  setters.setAppetite(r.appetite ?? candidate.appetite ?? null);
  if (r.appetite_facet) setters.setAppetiteFacet(r.appetite_facet);
  else if (candidate.appetite_facet) setters.setAppetiteFacet(candidate.appetite_facet);
  applyDispositionFromRatings(
    r,
    setters.setDispositionMarkers,
    setters.setDispositionUpdatedAt,
    setters.setDispositionLastOutcome,
    setters.setReasonDetail,
  );
  applyTriageFromRatings(r, setters.setLastTriagedAt, setters.setTriagePassCount);
  markBatchRated(candidate.relpath, axesFromRatings(r), (r.appetite as Appetite | null) ?? candidate.appetite ?? null);
}

function applyDispositionFromRatings(
  r: DiscoveryAssetRatingsResponse,
  setMarkers: (m: string[]) => void,
  setUpdatedAt: (t: string | null) => void,
  setLastOutcome: (o: DispositionOutcome | null) => void,
  setReasonDetail?: (d: Record<string, DispositionReasonDetail>) => void,
) {
  setMarkers(r.disposition_markers ?? []);
  setUpdatedAt(r.disposition_updated_at ?? null);
  setLastOutcome(r.disposition_last_outcome ?? null);
  setReasonDetail?.(r.disposition_reason_detail ?? {});
}

function reasonIdsForProcess(reasons: DispositionCatalogMarker[], process: string): string[] {
  const proc = process.trim();
  return reasons.filter((r) => String(r.process || "").trim() === proc).map((r) => r.id);
}

/** Mirror server toggle rules so disposition tiles update before the POST returns. */
function optimisticDispositionToggle(
  markers: string[],
  reasonDetail: Record<string, DispositionReasonDetail>,
  entries: DispositionCatalogMarker[],
  reasons: DispositionCatalogMarker[],
  markerId: string,
  on: boolean,
  extra?: { note?: string; modifiers?: string[] },
): { markers: string[]; reasonDetail: Record<string, DispositionReasonDetail> } {
  const entryIds = new Set(entries.map((e) => e.id));
  const spec = entries.find((e) => e.id === markerId) ?? reasons.find((r) => r.id === markerId);
  if (!spec) return { markers: [...markers], reasonDetail: { ...reasonDetail } };

  const nextMarkers = new Set(markers);
  const nextDetail: Record<string, DispositionReasonDetail> = { ...reasonDetail };
  const kind = spec.kind;

  if (on) {
    if (kind === "entry") {
      for (const id of entryIds) nextMarkers.delete(id);
      if (markerId !== "refine") {
        for (const rid of reasonIdsForProcess(reasons, "refine")) {
          nextMarkers.delete(rid);
          delete nextDetail[rid];
        }
      }
      nextMarkers.add(markerId);
    } else if (kind === "reason") {
      const process = String(spec.process || "").trim();
      if (process && entryIds.has(process)) {
        for (const id of entryIds) nextMarkers.delete(id);
        nextMarkers.add(process);
      }
      nextMarkers.add(markerId);
      const detail: DispositionReasonDetail = {};
      if (extra?.modifiers !== undefined) {
        if (extra.modifiers.length) detail.modifiers = extra.modifiers;
      } else {
        const prev = nextDetail[markerId];
        if (prev?.modifiers?.length) detail.modifiers = [...prev.modifiers];
      }
      const note = (extra?.note || nextDetail[markerId]?.note || "").trim();
      if (note) detail.note = note;
      nextDetail[markerId] = detail;
    } else {
      nextMarkers.add(markerId);
    }
  } else {
    nextMarkers.delete(markerId);
    if (kind === "reason") {
      delete nextDetail[markerId];
    } else if (kind === "entry") {
      const process = String(spec.process || markerId).trim();
      for (const rid of reasonIdsForProcess(reasons, process)) {
        nextMarkers.delete(rid);
        delete nextDetail[rid];
      }
    }
  }

  for (const rid of Object.keys(nextDetail)) {
    if (!nextMarkers.has(rid)) delete nextDetail[rid];
  }

  return { markers: [...nextMarkers].sort(), reasonDetail: nextDetail };
}

function entryLabelForMarker(markers: string[], catalog: DispositionCatalogMarker[]): string | null {
  const entry = markers.find((m) => catalog.some((c) => c.id === m && c.kind === "entry"));
  if (!entry) return null;
  return catalog.find((c) => c.id === entry)?.label ?? entry;
}

function fileUrlFromRel(relpath: string): string {
  return "/files/" + encodeURIComponent(relpath.replace(/\\/g, "/"));
}

function thumbRelFromVideo(relpath: string): string {
  return relpath.replace(/\.mp4$/i, ".png");
}

function stripThumbSrc(c: DiscoveryRatingSamplerCandidate): string {
  const thumb = (c.thumb_relpath || "").trim();
  if (thumb) return fileUrlFromRel(thumb);
  return fileUrlFromRel(thumbRelFromVideo(c.relpath));
}

function basename(rel: string): string {
  const p = rel.replace(/\\/g, "/");
  return p.split("/").pop() || p;
}

function parentDir(rel: string): string {
  const p = rel.replace(/\\/g, "/");
  const parts = p.split("/");
  if (parts.length <= 2) return p;
  return parts.slice(0, -1).join("/");
}

function bucketLabel(bucket?: string): { text: string; className: string; hint: string } {
  switch (bucket) {
    case "easy_down":
      return { text: "Quick reject", className: "drq-bucket drq-bucket--down", hint: "Heuristic thinks weak — fast 1–2★" };
    case "easy_up":
      return { text: "Likely keeper", className: "drq-bucket drq-bucket--up", hint: "Heuristic thinks strong — fast 4–5★" };
    default:
      return { text: "Ambiguous", className: "drq-bucket drq-bucket--mid", hint: "Typical middle band — your call" };
  }
}

function scorePct(score: number): number {
  return Math.max(0, Math.min(100, (score / 5) * 100));
}

function SessionProgress({ done, total, label = "Session" }: { done: number; total: number; label?: string }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="drq-progress" aria-label={`${label} progress ${done} of ${total}`}>
      <div className="drq-progress__labels">
        <span className="drq-progress__label">{label}</span>
        <span className="drq-progress__count mono">
          {done}/{total} <span className="drq-progress__pct">({pct}%)</span>
        </span>
      </div>
      <div className="drq-progress__track">
        <div className="drq-progress__fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function ScoreMeter({ score, label }: { score: number; label: string }) {
  return (
    <div className="drq-score-meter" title={`${label}: ${score.toFixed(2)} / 5`}>
      <div className="drq-score-meter-label">
        <span>{label}</span>
        <span className="mono">{score.toFixed(2)}</span>
      </div>
      <div className="drq-score-meter-track">
        <div className="drq-score-meter-fill" style={{ width: `${scorePct(score)}%` }} />
      </div>
    </div>
  );
}

function StatusToast({ message }: { message: string }) {
  const kind = !message
    ? "empty"
    : /fail|error|no xmp/i.test(message)
      ? "warn"
      : /saved|found|fast-track|appetite|disposition|queued|trash|archived|routed|replay|extend/i.test(message)
        ? "ok"
        : "info";
  return (
    <div className="drq-toast-slot" aria-live="polite">
      <div
        className={"drq-toast drq-toast--" + kind}
        role="status"
        title={message || undefined}
      >
        {message || "\u00a0"}
      </div>
    </div>
  );
}

function CandidateStrip({
  candidates,
  index,
  batchRated,
  onSelect,
}: {
  candidates: DiscoveryRatingSamplerCandidate[];
  index: number;
  batchRated: Record<string, boolean>;
  onSelect: (i: number) => void;
}) {
  const stripRef = useRef<HTMLDivElement>(null);
  /** Override src after lazy ensure (or null when permanently missing). */
  const [thumbOverrides, setThumbOverrides] = useState<Record<string, string | null>>({});
  const [generating, setGenerating] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const strip = stripRef.current;
    if (!strip) return;
    const el = strip.querySelector(".drq-strip-item--active");
    if (!(el instanceof HTMLElement)) return;
    // Keep active thumb in view horizontally only — never scroll the page vertically.
    const pad = 8;
    const elStart = el.offsetLeft;
    const elEnd = elStart + el.offsetWidth;
    const viewStart = strip.scrollLeft;
    const viewEnd = viewStart + strip.clientWidth;
    if (elStart < viewStart + pad) {
      strip.scrollLeft = Math.max(0, elStart - pad);
    } else if (elEnd > viewEnd - pad) {
      strip.scrollLeft = elEnd - strip.clientWidth + pad;
    }
  }, [index]);

  // Seed overrides from the shared ensure cache (survives strip remounts within the session).
  useEffect(() => {
    setThumbOverrides((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const c of candidates) {
        if (next[c.relpath] !== undefined) continue;
        const cached = cachedEnsureThumbUrl(c.relpath);
        if (cached !== undefined) {
          next[c.relpath] = cached;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [candidates]);

  const requestEnsure = useCallback((relpath: string) => {
    const cached = cachedEnsureThumbUrl(relpath);
    if (cached !== undefined) {
      setThumbOverrides((prev) => (prev[relpath] === cached ? prev : { ...prev, [relpath]: cached }));
      return;
    }
    setGenerating((g) => (g[relpath] ? g : { ...g, [relpath]: true }));
    void enqueueEnsureThumb(relpath)
      .then((res) => {
        let url: string | null =
          res.ok && res.thumb_url
            ? res.thumb_url
            : res.ok && res.thumb_relpath
              ? fileUrlFromRel(res.thumb_relpath)
              : null;
        if (url && res.created) {
          url += (url.includes("?") ? "&" : "?") + "t=" + Date.now();
        }
        setThumbOverrides((prev) => ({ ...prev, [relpath]: url }));
      })
      .catch(() => {
        setThumbOverrides((prev) => ({ ...prev, [relpath]: null }));
      })
      .finally(() => {
        setGenerating((g) => {
          if (!g[relpath]) return g;
          const next = { ...g };
          delete next[relpath];
          return next;
        });
      });
  }, []);

  const onThumbError = (c: DiscoveryRatingSamplerCandidate) => {
    const key = c.relpath;
    if (thumbOverrides[key] !== undefined || generating[key]) return;
    requestEnsure(key);
  };

  return (
    <div className="drq-filmstrip">
      <div className="drq-filmstrip__label">Queue</div>
      <div className="drq-strip" ref={stripRef} role="listbox" aria-label="Rating queue">
        {candidates.map((c, i) => {
          const active = i === index;
          const isDone = Boolean(batchRated[c.relpath]);
          const b = bucketLabel(c.session_bucket);
          const override = thumbOverrides[c.relpath];
          const isGenerating = Boolean(generating[c.relpath]);
          const isMissing = override === null && !isGenerating;
          // While generating, drop the broken guess so the spinner has a clean tile.
          const src = isGenerating
            ? override ?? null
            : override === undefined
              ? stripThumbSrc(c)
              : override;
          return (
            <button
              key={c.group_id || c.relpath}
              type="button"
              role="option"
              aria-selected={active}
              className={
                "drq-strip-item" +
                (active ? " drq-strip-item--active" : "") +
                (isDone ? " drq-strip-item--done" : "") +
                (isGenerating ? " drq-strip-item--generating" : "") +
                (c.session_bucket === "easy_down"
                  ? " drq-strip-item--down"
                  : c.session_bucket === "easy_up"
                    ? " drq-strip-item--up"
                    : "")
              }
              onClick={() => onSelect(i)}
              title={`${basename(c.relpath)} — ${b.text}${isGenerating ? " (generating thumb…)" : ""}`}
            >
              {src ? (
                <img
                  className="drq-strip-thumb"
                  src={src}
                  alt=""
                  loading="lazy"
                  onError={() => onThumbError(c)}
                />
              ) : (
                <span className="drq-strip-thumb drq-strip-thumb--empty" aria-hidden="true" />
              )}
              {isGenerating ? (
                <span className="drq-strip-spinner" aria-label="Generating thumbnail">
                  <span className="drq-strip-spinner__ring" />
                </span>
              ) : null}
              {isMissing ? <span className="drq-strip-thumb-miss" aria-hidden="true" /> : null}
              {isDone ? <span className="drq-strip-done" aria-hidden="true">✓</span> : null}
              <span className="drq-strip-score mono">{c.predicted_score?.toFixed(1) ?? "—"}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function DiscoveryRatingQueueApp() {
  const [session, setSession] = useState<DiscoveryRatingSamplerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [index, setIndex] = useState(0);
  const [batchRated, setBatchRated] = useState<Record<string, boolean>>({});
  const [explicitRating, setExplicitRating] = useState<number | null>(null);
  const [qualityAxes, setQualityAxes] = useState<QualityAxesMap>(emptyQualityAxes);
  const [activeQualityAxis, setActiveQualityAxis] = useState<QualityAxis>("subject_beauty");
  const [derivedRating, setDerivedRating] = useState<number | null>(null);
  const [derivedSourceLabel, setDerivedSourceLabel] = useState<string | null>(null);
  const [checkMsg, setCheckMsg] = useState("");
  const rateSeqRef = useRef(0);
  const dispositionToggleSeqRef = useRef(0);
  const [queueLimit, setQueueLimit] = useState(loadQueueLimit);
  const [appetite, setAppetite] = useState<Appetite | null>(null);
  const [appetiteFacet, setAppetiteFacet] = useState<AppetiteFacet>(loadStickyFacet);
  const [appetiteBusy, setAppetiteBusy] = useState(false);
  const [dispositionMarkers, setDispositionMarkers] = useState<string[]>([]);
  const [dispositionUpdatedAt, setDispositionUpdatedAt] = useState<string | null>(null);
  const [dispositionLastOutcome, setDispositionLastOutcome] = useState<DispositionOutcome | null>(null);
  const [dispositionLastAction, setDispositionLastAction] = useState<string>("");
  const [lastStepId, setLastStepId] = useState<string | null>(null);
  const [dispositionBusy, setDispositionBusy] = useState(false);
  const [triageBusy, setTriageBusy] = useState(false);
  const [lastTriagedAt, setLastTriagedAt] = useState<string | null>(null);
  const [triagePassCount, setTriagePassCount] = useState(0);
  const [catalogEntries, setCatalogEntries] = useState<DispositionCatalogMarker[]>([]);
  const [catalogSteps, setCatalogSteps] = useState<DispositionCatalogMarker[]>([]);
  const [catalogReasons, setCatalogReasons] = useState<DispositionCatalogMarker[]>([]);
  const [catalogMarkers, setCatalogMarkers] = useState<DispositionCatalogMarker[]>([]);
  const [promotions, setPromotions] = useState<DispositionPromotions | null>(null);
  const [reasonDetail, setReasonDetail] = useState<Record<string, DispositionReasonDetail>>({});
  const [catalogEditorOpen, setCatalogEditorOpen] = useState(false);
  const [loopPlayback, setLoopPlayback] = useState(loadLoopPlayback);
  const [selectionMode, setSelectionMode] = useState<SelectionMode>(loadSelectionMode);
  const [includeDone, setIncludeDone] = useState(loadIncludeDone);
  const [searchQuery, setSearchQuery] = useState(loadSearchQuery);
  const [searchDraft, setSearchDraft] = useState(loadSearchQuery);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [videoAspect, setVideoAspect] = useState<"portrait" | "landscape" | "square" | null>(null);
  const [videoDuration, setVideoDuration] = useState(0);
  const [videoTime, setVideoTime] = useState(0);
  const [markIn, setMarkIn] = useState<number | null>(null);
  const [markOut, setMarkOut] = useState<number | null>(null);

  const onVideoMetadata = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    const d = v.duration;
    if (Number.isFinite(d) && d > 0) setVideoDuration(d);
    setVideoTime(v.currentTime || 0);
    if (!v.videoWidth || !v.videoHeight) return;
    const ratio = v.videoWidth / v.videoHeight;
    if (ratio >= 1.12) setVideoAspect("landscape");
    else if (ratio <= 0.88) setVideoAspect("portrait");
    else setVideoAspect("square");
  }, []);

  const load = useCallback(async (refresh: boolean) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const data = await fetchDiscoveryRatingSampler({
        refresh,
        limit: queueLimit,
        mode: selectionMode,
        query: selectionMode === "search" ? searchQuery : undefined,
        includeDone,
      });
      if (!data.ok) {
        setError(data.error || "Failed to load rating queue");
        setSession(data);
        return;
      }
      setSession(data);
      setBatchRated({});
      setIndex(0);
      prefetchAssetRatings((data.candidates ?? []).map((c) => c.relpath));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [queueLimit, selectionMode, searchQuery, includeDone]);

  useEffect(() => {
    void load(false);
  }, [load]);

  useEffect(() => {
    void fetchDispositionCatalog()
      .then((c) => {
        setCatalogEntries(c.entries ?? []);
        setCatalogSteps(c.steps ?? []);
        const fromPayload = c.reasons ?? [];
        const fromMarkers = (c.catalog?.markers ?? []).filter((m) => m.kind === "reason");
        setCatalogReasons(fromPayload.length ? fromPayload : fromMarkers);
        setCatalogMarkers(
          c.catalog?.markers ?? [...(c.entries ?? []), ...(c.steps ?? []), ...(fromPayload.length ? fromPayload : fromMarkers)],
        );
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!session?.candidates?.length) return;
    setBatchRated((prev) => {
      const next = { ...prev };
      for (const c of session.candidates ?? []) {
        if (next[c.relpath] === undefined) {
          // Sampler only returns incomplete items; seed false unless already marked this session.
          next[c.relpath] = false;
        }
      }
      return next;
    });
  }, [session]);

  const onQueueLimitChange = (n: number) => {
    setQueueLimit(n);
    try {
      localStorage.setItem(QUEUE_LIMIT_KEY, String(n));
    } catch {
      /* ignore */
    }
  };

  const onSelectionModeChange = (mode: SelectionMode) => {
    setSelectionMode(mode);
    try {
      localStorage.setItem(SELECTION_MODE_KEY, mode);
    } catch {
      /* ignore */
    }
  };

  const onIncludeDoneChange = (on: boolean) => {
    setIncludeDone(on);
    try {
      localStorage.setItem(INCLUDE_DONE_KEY, on ? "1" : "0");
    } catch {
      /* ignore */
    }
  };

  const commitSearchQuery = (raw: string) => {
    const q = raw.trim();
    setSearchDraft(q);
    setSearchQuery(q);
    try {
      localStorage.setItem(SEARCH_QUERY_KEY, q);
    } catch {
      /* ignore */
    }
  };

  const candidates = session?.candidates ?? [];
  const current = candidates[index] ?? null;

  const trimMode: VideoTrimPlaybackMode = loopPlayback ? "repeat" : "stop_at_end";
  const extensionRange = current?.extension_range;
  const trimFps = parseFps(extensionRange?.fps, 18);
  const generationBands = useMemo(
    () =>
      originGenerationBands({
        duration: videoDuration,
        fps: trimFps,
        framesBefore: extensionRange?.frames_before,
        generationFrames: extensionRange?.generation_frames,
        outputFrameCount: extensionRange?.output_frame_count,
        overlapFrames: extensionRange?.overlap,
      }),
    [
      videoDuration,
      trimFps,
      extensionRange?.frames_before,
      extensionRange?.generation_frames,
      extensionRange?.output_frame_count,
      extensionRange?.overlap,
    ],
  );

  useTrimPlaybackEnforcement(videoRef, {
    mediaKey: current?.relpath || "",
    markIn,
    markOut,
    mode: trimMode,
    enabled: Boolean(current?.relpath),
  });

  useEffect(() => {
    let cancelled = false;
    setVideoDuration(0);
    setVideoTime(0);
    setMarkIn(null);
    setMarkOut(null);
    setVideoAspect(null);
    const rel = current?.relpath;
    if (!rel) return;
    void loadDiscoveryTrimAsync(TRIM_CONTEXT_DISCOVERY_PLAYER, rel, rel).then((loaded) => {
      if (cancelled || !loaded) return;
      setMarkIn(loaded.in);
      setMarkOut(loaded.out);
    });
    return () => {
      cancelled = true;
    };
  }, [current?.relpath]);

  const persistRateTrim = useCallback(
    (nextIn: number | null, nextOut: number | null, duration: number) => {
      const rel = current?.relpath;
      if (!rel) return;
      void persistDiscoveryTrimAsync({
        context: TRIM_CONTEXT_DISCOVERY_PLAYER,
        mediaRelpath: rel,
        legacyAssetKey: rel,
        markIn: nextIn,
        markOut: nextOut,
        duration: duration > 0 ? duration : 1,
      });
    },
    [current?.relpath],
  );

  const dismissBatchAndLoad = useCallback(async () => {
    if (triageBusy) return;
    setTriageBusy(true);
    try {
      const rels = candidates.map((c) => c.relpath);
      if (rels.length) {
        const res = await recordBatchTriageComplete({ relpaths: rels });
        setCheckMsg(
          `Batch dismissed — ${res.committed_count ?? 0} rated (all axes + appetite), ${res.skipped_count ?? 0} returned to pool`,
        );
      }
      setBatchRated({});
      await load(true);
    } catch (e) {
      setCheckMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setTriageBusy(false);
    }
  }, [candidates, triageBusy, load]);

  const refreshPromotions = useCallback(
    async (rel: string, predicted?: number) => {
      const q =
        explicitRating != null
          ? explicitRating
          : derivedRating != null
            ? derivedRating
            : predicted ?? null;
      try {
        const res = await fetchDispositionSuggest({
          relpath: rel,
          quality: q,
          appetite,
          facet: appetiteFacet,
          predicted_score: predicted,
          explicit_quality_missing: explicitRating == null && derivedRating != null,
        });
        setPromotions(res.promotions ?? null);
      } catch {
        setPromotions(null);
      }
    },
    [appetite, appetiteFacet, derivedRating, explicitRating],
  );

  const markBatchRated = useCallback((relpath: string, axes: QualityAxesMap, app: Appetite | null | undefined) => {
    setBatchRated((prev) => ({
      ...prev,
      [relpath]: isRatingComplete(axes, app),
    }));
  }, []);

  const goNext = useCallback(() => {
    setIndex((i) => {
      const n = candidates.length;
      if (n <= 0) return 0;
      return (i + 1) % n;
    });
    setCheckMsg("");
  }, [candidates.length]);

  const goPrev = useCallback(() => {
    setIndex((i) => {
      const n = candidates.length;
      if (n <= 0) return 0;
      return (i - 1 + n) % n;
    });
    setCheckMsg("");
  }, [candidates.length]);

  const skipCurrent = useCallback(() => {
    goNext();
  }, [goNext]);

  const setFacet = useCallback((f: AppetiteFacet) => {
    setAppetiteFacet(f);
    try {
      localStorage.setItem(APPETITE_FACET_KEY, f);
    } catch {
      /* ignore */
    }
  }, []);

  const toggleLoopPlayback = useCallback(() => {
    setLoopPlayback((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(LOOP_PLAYBACK_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const setAppetiteCurrent = useCallback(
    async (state: Appetite, facet: AppetiteFacet) => {
      if (!current || appetiteBusy) return;
      const prevAppetite = appetite;
      const prevFacet = appetiteFacet;
      setAppetite(state);
      setAppetiteFacet(facet);
      markBatchRated(current.relpath, qualityAxes, state);
      patchCachedAppetite(current.relpath, state, facet);
      setAppetiteBusy(true);
      setCheckMsg("");
      try {
        const res = await setAssetAppetite({ relpath: current.relpath, appetite: state, facet });
        if (state === "fast_track") {
          const q = res.saved?.queued;
          if (q?.ok) {
            setCheckMsg(q.extend_fallback === "replay" ? "Fast-tracked — queued replay" : "Fast-tracked — queued Extend");
          } else {
            setCheckMsg(`Fast-track saved (${q?.reason || "no queue context"})`);
          }
        } else {
          setCheckMsg(`Appetite: ${state} · ${facet}`);
        }
      } catch (e) {
        setAppetite(prevAppetite);
        setAppetiteFacet(prevFacet);
        markBatchRated(current.relpath, qualityAxes, prevAppetite);
        patchCachedAppetite(current.relpath, prevAppetite, prevFacet);
        setCheckMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setAppetiteBusy(false);
      }
    },
    [current, appetiteBusy, appetite, appetiteFacet, qualityAxes, markBatchRated],
  );

  const formatDispositionResult = (stepId: string, result: Record<string, unknown> | undefined): string => {
    if (!result) return `Step ${stepId} done`;
    const hook = String(result.hook || "");
    const inner = (result.result as Record<string, unknown> | undefined) || result;
    if (inner.trim_ui || hook === "open_trim") {
      const href = String(inner.discovery_href || "");
      return href ? `Open trim in library` : "Trim — open discovery library";
    }
    if (hook === "trash" || inner.moved) return "Moved to trash";
    if (hook === "archive" || inner.archived) return "Archived";
    if (inner.ok && (inner.extend || inner.replay_of_job_key || inner.job_key || inner.prompt_id)) {
      const recovered = inner.prompt_profile_recovered ? " (prompt recovered)" : "";
      if (inner.extend_fallback === "replay") return `Queued replay${recovered}`;
      if (inner.extend) return `Queued extend${recovered}`;
      return `Queued${recovered}`;
    }
    if (hook === "set_marker" && inner.toggled) {
      const m = (inner.toggled as { markers?: string[] }).markers;
      return m?.length ? `Routed → ${m.join(", ")}` : "Routed";
    }
    if (inner.placeholder) return String(inner.detail || "Extract (placeholder)");
    if (inner.error) return String(inner.error);
    if (inner.reason) return String(inner.reason);
    return `Step ${stepId} done`;
  };

  const toggleDispositionMarker = useCallback(
    async (
      markerId: string,
      on: boolean,
      extra?: { note?: string; modifiers?: string[] },
    ) => {
      if (!current) return;
      const seq = ++dispositionToggleSeqRef.current;
      setCheckMsg("");

      const prevMarkers = dispositionMarkers;
      const prevDetail = reasonDetail;
      const prevUpdatedAt = dispositionUpdatedAt;
      const prevPromotions = promotions;

      const optimistic = optimisticDispositionToggle(
        dispositionMarkers,
        reasonDetail,
        catalogEntries,
        catalogReasons,
        markerId,
        on,
        extra,
      );
      setDispositionMarkers(optimistic.markers);
      setReasonDetail(optimistic.reasonDetail);
      patchCachedDisposition(current.relpath, optimistic.markers, optimistic.reasonDetail);

      try {
        const q =
          explicitRating != null
            ? explicitRating
            : derivedRating != null
              ? derivedRating
              : undefined;
        const res = await toggleAssetDisposition({
          relpath: current.relpath,
          marker: markerId,
          on,
          note: extra?.note,
          modifiers: extra?.modifiers,
          quality: q,
          appetite,
          facet: appetiteFacet,
        });
        if (seq !== dispositionToggleSeqRef.current) return;

        if (res.saved?.markers) setDispositionMarkers(res.saved.markers);
        setReasonDetail(res.saved?.reason_detail ?? {});
        if (res.promotions) setPromotions(res.promotions);
        if (res.saved?.updated_at) setDispositionUpdatedAt(String(res.saved.updated_at));
        patchCachedDisposition(
          current.relpath,
          res.saved?.markers ?? optimistic.markers,
          res.saved?.reason_detail ?? optimistic.reasonDetail,
          res.saved?.updated_at ?? null,
        );

        const label =
          catalogEntries.find((e) => e.id === markerId)?.label ??
          catalogReasons.find((e) => e.id === markerId)?.label ??
          markerId;
        const msg = on ? `Saved disposition: ${label}` : `Cleared: ${label}`;
        setDispositionLastAction(msg);
        setCheckMsg(msg);

        void revalidateAssetRatings(current.relpath)
          .then((ratings) => {
            if (seq !== dispositionToggleSeqRef.current) return;
            rememberAssetRatings(current.relpath, ratings);
            applyDispositionFromRatings(
              ratings,
              setDispositionMarkers,
              setDispositionUpdatedAt,
              setDispositionLastOutcome,
              setReasonDetail,
            );
            applyTriageFromRatings(ratings, setLastTriagedAt, setTriagePassCount);
          })
          .catch(() => {});
      } catch (e) {
        if (seq !== dispositionToggleSeqRef.current) return;
        setDispositionMarkers(prevMarkers);
        setReasonDetail(prevDetail);
        setDispositionUpdatedAt(prevUpdatedAt);
        setPromotions(prevPromotions);
        setCheckMsg(e instanceof Error ? e.message : String(e));
      }
    },
    [
      current,
      dispositionMarkers,
      reasonDetail,
      dispositionUpdatedAt,
      promotions,
      appetite,
      appetiteFacet,
      explicitRating,
      derivedRating,
      catalogEntries,
      catalogReasons,
    ],
  );

  const commitAdvanceRoutes = useCallback(
    async (opts: { extend: boolean; vary: boolean; queueNow: boolean }) => {
      if (!current || dispositionBusy) return;
      if (!opts.extend && !opts.vary) {
        setCheckMsg("Select Extend and/or Vary before committing");
        return;
      }
      setDispositionBusy(true);
      setCheckMsg("");
      const routes: Array<{ step_id: string }> = [];
      if (opts.extend) routes.push({ step_id: "advance.extend" });
      if (opts.vary) routes.push({ step_id: "advance.vary" });
      const parts: string[] = [];
      try {
        const created = await createWorkItems({
          source_relpath: current.relpath,
          routes,
          queue_now: opts.queueNow,
        });
        parts.push(`Work items: ${created.count ?? created.items?.length ?? routes.length}`);
        for (const route of routes) {
          setLastStepId(route.step_id);
          try {
            const res = await runDispositionStep({
              relpath: current.relpath,
              step_id: route.step_id,
              facet: appetiteFacet,
              front: opts.queueNow || undefined,
            });
            const stepLabel = catalogSteps.find((s) => s.id === route.step_id)?.label ?? route.step_id;
            const hookResult = (res.result as Record<string, unknown> | undefined) || {};
            const merged: Record<string, unknown> = {
              ...hookResult,
              ...(typeof res.work_item_error === "string" ? { error: res.work_item_error } : {}),
            };
            const nested = (hookResult.result as Record<string, unknown> | undefined) || hookResult;
            if (nested && typeof nested === "object") {
              Object.assign(merged, nested);
            }
            parts.push(
              `${stepLabel}: ${formatDispositionResult(route.step_id, { hook: res.hook, result: merged, ...merged })}`,
            );
          } catch (e) {
            const stepLabel = catalogSteps.find((s) => s.id === route.step_id)?.label ?? route.step_id;
            parts.push(`${stepLabel}: ${e instanceof Error ? e.message : String(e)}`);
          }
        }
        const msg = parts.join(" · ");
        setDispositionLastAction(msg);
        setCheckMsg(msg);
        void revalidateAssetRatings(current.relpath)
          .then((ratings) => {
            rememberAssetRatings(current.relpath, ratings);
            applyDispositionFromRatings(
              ratings,
              setDispositionMarkers,
              setDispositionUpdatedAt,
              setDispositionLastOutcome,
              setReasonDetail,
            );
            applyTriageFromRatings(ratings, setLastTriagedAt, setTriagePassCount);
          })
          .catch(() => {});
      } catch (e) {
        setLastStepId(null);
        setCheckMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setDispositionBusy(false);
      }
    },
    [current, dispositionBusy, appetiteFacet, catalogSteps, ],
  );

  const runDispositionStepCurrent = useCallback(
    async (stepId: string) => {
      if (!current || dispositionBusy) return;
      setDispositionBusy(true);
      setCheckMsg("");
      try {
        setLastStepId(stepId);
        const res = await runDispositionStep({
          relpath: current.relpath,
          step_id: stepId,
          facet: appetiteFacet,
        });
        const stepLabel = catalogSteps.find((s) => s.id === stepId)?.label ?? stepId;
        const msg = formatDispositionResult(stepId, res.result as Record<string, unknown> | undefined);
        const fullMsg = `${stepLabel}: ${msg}`;
        setDispositionLastAction(fullMsg);
        setCheckMsg(fullMsg);
        const inner = (res.result?.result as Record<string, unknown> | undefined) || res.result;
        if (inner?.discovery_href && (inner.trim_ui || res.hook === "open_trim")) {
          window.open(String(inner.discovery_href), "_blank", "noopener,noreferrer");
        }
        if (inner?.toggled && typeof inner.toggled === "object") {
          const m = (inner.toggled as { markers?: string[] }).markers;
          if (m) setDispositionMarkers(m);
        }
        void revalidateAssetRatings(current.relpath)
          .then((ratings) => {
            rememberAssetRatings(current.relpath, ratings);
            applyDispositionFromRatings(
              ratings,
              setDispositionMarkers,
              setDispositionUpdatedAt,
              setDispositionLastOutcome,
              setReasonDetail,
            );
            applyTriageFromRatings(ratings, setLastTriagedAt, setTriagePassCount);
          })
          .catch(() => {});
      } catch (e) {
        setLastStepId(null);
        setCheckMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setDispositionBusy(false);
      }
    },
    [current, dispositionBusy, appetiteFacet, catalogSteps, ],
  );

  const saveCatalog = useCallback(async (markers: DispositionCatalogMarker[]) => {
    setDispositionBusy(true);
    try {
      const res = await saveDispositionCatalog({ markers });
      const nested = res.catalog as DispositionCatalogResponse | undefined;
      const entries = res.entries ?? nested?.entries ?? [];
      const steps = res.steps ?? nested?.steps ?? [];
      const reasons = res.reasons ?? nested?.reasons ?? [];
      setCatalogEntries(entries);
      setCatalogSteps(steps);
      setCatalogReasons(reasons);
      setCatalogMarkers(nested?.catalog?.markers ?? res.catalog?.markers ?? markers);
      setCatalogEditorOpen(false);
      setCheckMsg("Disposition catalog saved");
    } catch (e) {
      setCheckMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setDispositionBusy(false);
    }
  }, []);

  const rateCurrent = useCallback(
    async (stars: number, axis: QualityAxis = activeQualityAxis) => {
      if (!current) return;
      const seq = ++rateSeqRef.current;
      setCheckMsg("");

      const prevAxes = qualityAxes;
      const prevExplicit = explicitRating;
      const prevDerived = derivedRating;
      const prevDerivedLabel = derivedSourceLabel;

      let nextAxes: QualityAxesMap = { ...qualityAxes };
      if (stars > 0) nextAxes[axis] = stars;
      else delete nextAxes[axis];
      setQualityAxes(nextAxes);
      setExplicitRating(aggregateFromAxes(nextAxes));
      setDerivedRating(null);
      setDerivedSourceLabel(null);
      markBatchRated(current.relpath, nextAxes, appetite);
      patchCachedQuality(current.relpath, nextAxes, aggregateFromAxes(nextAxes));

      try {
        const res = await setAssetRating({ relpath: current.relpath, stars, axis });
        if (seq !== rateSeqRef.current) return;

        if (res.saved?.axes && typeof res.saved.axes === "object") {
          nextAxes = { ...nextAxes };
          for (const a of QUALITY_AXES) {
            const n = res.saved.axes[a];
            if (typeof n === "number" && n >= 1) nextAxes[a] = n;
            else delete nextAxes[a];
          }
          setQualityAxes(nextAxes);
        }
        setExplicitRating(
          typeof res.saved?.explicit === "number" ? res.saved.explicit : aggregateFromAxes(nextAxes),
        );
        markBatchRated(current.relpath, nextAxes, appetite);
        patchCachedQuality(
          current.relpath,
          nextAxes,
          typeof res.saved?.explicit === "number" ? res.saved.explicit : aggregateFromAxes(nextAxes),
        );

        const label = QUALITY_AXIS_LABELS[axis];
        setCheckMsg(stars > 0 ? `Saved ${label} ${stars}★` : `Cleared ${label}`);
      } catch (e) {
        if (seq !== rateSeqRef.current) return;
        setQualityAxes(prevAxes);
        setExplicitRating(prevExplicit);
        setDerivedRating(prevDerived);
        setDerivedSourceLabel(prevDerivedLabel);
        markBatchRated(current.relpath, prevAxes, appetite);
        setCheckMsg(e instanceof Error ? e.message : String(e));
      }
    },
    [
      current,
      appetite,
      markBatchRated,
      activeQualityAxis,
      qualityAxes,
      explicitRating,
      derivedRating,
      derivedSourceLabel,
    ],
  );

  useEffect(() => {
    if (!current) return;
    const rel = current.relpath;
    setVideoAspect(null);
    setCheckMsg("");
    setDispositionLastAction("");
    setLastStepId(null);

    const cached = peekAssetRatings(rel);
    const seed = cached ?? ratingsSeedFromCandidate(current);
    applyJudgmentFromRatings(
      seed,
      current,
      {
        setExplicitRating,
        setDerivedRating,
        setDerivedSourceLabel,
        setQualityAxes,
        setAppetite,
        setAppetiteFacet,
        setDispositionMarkers,
        setDispositionUpdatedAt,
        setDispositionLastOutcome,
        setReasonDetail,
        setLastTriagedAt,
        setTriagePassCount,
      },
      markBatchRated,
    );

    let cancelled = false;
    void revalidateAssetRatings(rel)
      .then((r) => {
        if (cancelled) return;
        rememberAssetRatings(rel, r);
        applyJudgmentFromRatings(
          r,
          current,
          {
            setExplicitRating,
            setDerivedRating,
            setDerivedSourceLabel,
            setQualityAxes,
            setAppetite,
            setAppetiteFacet,
            setDispositionMarkers,
            setDispositionUpdatedAt,
            setDispositionLastOutcome,
            setReasonDetail,
            setLastTriagedAt,
            setTriagePassCount,
          },
          markBatchRated,
        );
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [current?.relpath, markBatchRated]);

  useEffect(() => {
    if (!candidates.length) return;
    const n = candidates.length;
    const rels = [index, index + 1, index - 1, index + 2, index + 3]
      .map((i) => candidates[((i % n) + n) % n]?.relpath)
      .filter((r): r is string => Boolean(r));
    prefetchAssetRatings(rels);
  }, [index, candidates]);

  useEffect(() => {
    if (!current) return;
    void refreshPromotions(current.relpath, current.predicted_score);
  }, [current?.relpath, current?.predicted_score, refreshPromotions]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "ArrowRight") {
        e.preventDefault();
        goNext();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        goPrev();
      } else if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        skipCurrent();
      } else if (e.key >= "1" && e.key <= "5") {
        e.preventDefault();
        void rateCurrent(parseInt(e.key, 10), activeQualityAxis);
      } else if (e.key === "0" || e.key === "Backspace") {
        e.preventDefault();
        void rateCurrent(0, activeQualityAxis);
      } else if (e.key === "q" || e.key === "Q") {
        e.preventDefault();
        setActiveQualityAxis((prev) => {
          const i = QUALITY_AXES.indexOf(prev);
          return QUALITY_AXES[(i + 1) % QUALITY_AXES.length];
        });
      } else if (e.key.toLowerCase() in APPETITE_KEYMAP) {
        e.preventDefault();
        void setAppetiteCurrent(APPETITE_KEYMAP[e.key.toLowerCase()], appetiteFacet);
      } else if (e.key === "g" || e.key === "G") {
        e.preventDefault();
        const i = APPETITE_FACET_CYCLE.indexOf(appetiteFacet);
        setFacet(APPETITE_FACET_CYCLE[(i + 1) % APPETITE_FACET_CYCLE.length]);
      } else if (e.key === "l" || e.key === "L") {
        e.preventDefault();
        toggleLoopPlayback();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goNext, goPrev, skipCurrent, rateCurrent, setAppetiteCurrent, appetiteFacet, setFacet, toggleLoopPlayback, activeQualityAxis]);

  const stats = session?.stats;
  const ratedCount = useMemo(() => {
    return candidates.filter((c) => batchRated[c.relpath]).length;
  }, [candidates, batchRated]);

  const bucket = current ? bucketLabel(current.session_bucket) : null;
  const showingDerived = !axesComplete(qualityAxes) && explicitRating == null && derivedRating != null;
  const dispositionEntryBadge = useMemo(
    () => entryLabelForMarker(dispositionMarkers, catalogEntries),
    [dispositionMarkers, catalogEntries],
  );
  const dispositionCatalogAll = useMemo(
    () => [...catalogEntries, ...catalogSteps, ...catalogReasons],
    [catalogEntries, catalogSteps, catalogReasons],
  );

  return (
    <div className="discovery-screen drq-screen">
      <div className="panel discovery-panel drq-root">
        <PageHeader
          title="Rating"
          subtitle="Work through a fixed batch — set Subject / Render / Action and appetite. Disposition is optional. Dismiss commits rated clips; the rest return to the pool."
          actions={
            <div className="drq-header-actions">
              <div className="segmented drq-mode-segmented" role="radiogroup" aria-label="Selection mode">
                {SELECTION_MODES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    role="radio"
                    aria-checked={selectionMode === m.id}
                    className={"seg-btn" + (selectionMode === m.id ? " active" : "")}
                    disabled={loading || refreshing || triageBusy}
                    onClick={() => onSelectionModeChange(m.id)}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
              <label className="drq-include-done">
                <input
                  type="checkbox"
                  checked={includeDone}
                  disabled={loading || refreshing || triageBusy}
                  onChange={(e) => onIncludeDoneChange(e.target.checked)}
                />
                Show rated / disposed
              </label>
              {selectionMode === "search" ? (
                <form
                  className="drq-search-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    commitSearchQuery(searchDraft);
                  }}
                >
                  <input
                    type="search"
                    className="drq-search-input"
                    placeholder="Search path / group…"
                    value={searchDraft}
                    disabled={loading || refreshing || triageBusy}
                    onChange={(e) => setSearchDraft(e.target.value)}
                    aria-label="Search rating pool"
                  />
                  <button type="submit" className="drt-btn" disabled={loading || refreshing || triageBusy}>
                    Go
                  </button>
                </form>
              ) : null}
              <label className="drq-limit-label">
                Batch size
                <select
                  className="drq-limit-select"
                  value={queueLimit}
                  disabled={loading || refreshing || triageBusy}
                  onChange={(e) => onQueueLimitChange(parseInt(e.target.value, 10))}
                >
                  {QUEUE_LIMIT_OPTIONS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          }
        >
          {stats ? (
            <p className="drq-session-hint factory-muted">
              {selectionMode === "mixed"
                ? `Mixed: ↓${stats.bucket_easy_down ?? "—"} quick rejects · ↑${stats.bucket_easy_up ?? "—"} likely keepers · ~${stats.bucket_middle ?? "—"} ambiguous`
                : selectionMode === "random"
                  ? `Random batch · pool ${stats.unrated_videos ?? "—"} · selected ${stats.selected ?? "—"}`
                  : selectionMode === "latest"
                    ? `Latest by mtime · pool ${stats.unrated_videos ?? "—"} · selected ${stats.selected ?? "—"}`
                    : searchQuery
                      ? `Search “${searchQuery}” · matches ${stats.unrated_videos ?? "—"} · selected ${stats.selected ?? "—"}`
                      : "Enter a search query to populate the queue"}
              {includeDone ? " · including rated/disposed" : ""}
            </p>
          ) : null}
        </PageHeader>

        <details className="drq-shortcuts">
          <summary>Keyboard shortcuts</summary>
          <div className="drq-shortcuts-grid">
            <span><kbd>1</kbd>–<kbd>5</kbd> active quality axis</span>
            <span><kbd>0</kbd> clear active axis</span>
            <span><kbd>q</kbd> cycle Subject / Render / Action</span>
            <span><kbd>z</kbd><kbd>x</kbd><kbd>c</kbd><kbd>v</kbd> appetite</span>
            <span><kbd>g</kbd> cycle facet</span>
            <span><kbd>←</kbd><kbd>→</kbd> prev / next (wraps)</span>
            <span><kbd>s</kbd> skip to next</span>
            <span><kbd>l</kbd> loop</span>
          </div>
        </details>

        {loading ? <p className="drq-muted drq-state">Loading rating queue…</p> : null}
        {error ? <p className="drt-err drq-state">{error}</p> : null}

        {!loading && current ? (
          <>
            <div className="drq-main">
              <section
                className={
                  "drq-stage" +
                  (videoAspect === "landscape"
                    ? " drq-stage--landscape"
                    : videoAspect === "square"
                      ? " drq-stage--square"
                      : " drq-stage--portrait")
                }
                aria-label="Current clip"
              >
                <div className="drq-stage__top">
                  {bucket ? (
                    <span className={bucket.className} title={bucket.hint}>
                      {bucket.text}
                    </span>
                  ) : null}
                  <span className="drq-stage__pos mono">
                    {index + 1} / {candidates.length}
                  </span>
                </div>

                <div className="drq-video-slot">
                  <div className="drq-player-wrap">
                    <video
                      key={current.relpath}
                      ref={videoRef}
                      className="drq-player"
                      src={fileUrlFromRel(current.relpath)}
                      controls
                      autoPlay
                      loop={loopPlayback && markIn == null && markOut == null}
                      playsInline
                      preload="metadata"
                      onLoadedMetadata={onVideoMetadata}
                      onDurationChange={(e) => {
                        const d = e.currentTarget.duration;
                        if (Number.isFinite(d) && d > 0) setVideoDuration(d);
                      }}
                      onTimeUpdate={(e) => setVideoTime(e.currentTarget.currentTime || 0)}
                      onSeeked={(e) => setVideoTime(e.currentTarget.currentTime || 0)}
                    />
                    {explicitRating != null ? (
                      <span className="drq-rated-badge dal-rating dal-rating--explicit">★ {explicitRating}</span>
                    ) : derivedRating != null ? (
                      <span className="drq-rated-badge drq-rated-badge--derived" title={derivedSourceLabel ?? undefined}>
                        ≈ {derivedRating}★
                      </span>
                    ) : null}
                    {current.vision_recommended ? (
                      <span className="drq-vision-badge" title={(current.vision_reasons || []).join(", ")}>
                        Vision hint
                      </span>
                    ) : null}
                    {dispositionEntryBadge ? (
                      <span className="drq-disposition-badge" title="Active disposition entry">
                        {dispositionEntryBadge}
                      </span>
                    ) : null}
                  </div>
                  <VideoTrimControls
                    className="drq-player-trim"
                    videoRef={videoRef}
                    duration={videoDuration}
                    currentTime={videoTime}
                    markIn={markIn}
                    markOut={markOut}
                    mode={trimMode}
                    mediaSyncKey={current.relpath}
                    size="default"
                    seamMark={generationBands?.seamSec ?? null}
                    blendEndMark={generationBands?.blendEndSec ?? null}
                    onSeek={setVideoTime}
                    onSyncTime={setVideoTime}
                    onMarkInChange={(v) => {
                      setMarkIn(v);
                      persistRateTrim(v, markOut, videoDuration);
                    }}
                    onMarkOutChange={(v) => {
                      setMarkOut(v);
                      persistRateTrim(markIn, v, videoDuration);
                    }}
                    onModeChange={(m) => {
                      const next = m === "repeat";
                      setLoopPlayback(next);
                      try {
                        localStorage.setItem(LOOP_PLAYBACK_KEY, next ? "1" : "0");
                      } catch {
                        /* ignore */
                      }
                    }}
                    onClear={() => {
                      setMarkIn(null);
                      setMarkOut(null);
                      persistRateTrim(null, null, videoDuration);
                    }}
                  />
                  <div className="drq-player-tools">
                    <button
                      type="button"
                      className={"drq-loop-toggle" + (loopPlayback ? " drq-loop-toggle--on" : "")}
                      aria-pressed={loopPlayback}
                      title={loopPlayback ? "Loop on (press L)" : "Loop off (press L)"}
                      onClick={toggleLoopPlayback}
                    >
                      <span className="drq-loop-toggle__icon" aria-hidden="true">
                        ↻
                      </span>
                      Loop {loopPlayback ? "on" : "off"}
                    </button>
                  </div>
                  <div className="drq-asset-meta">
                    <h2 className="drq-title">{basename(current.relpath)}</h2>
                    <p className="drq-path mono" title={current.relpath}>
                      {parentDir(current.relpath)}
                    </p>
                  </div>
                </div>

                <div className="drq-control-stack">
                  <div className="drq-meters">
                    <ScoreMeter score={current.predicted_score ?? 0} label="Predicted keeper" />
                    <ScoreMeter score={(current.heuristic_confidence ?? 0) * 5} label="Heuristic confidence" />
                  </div>

                  <StatusToast message={checkMsg} />

                  <div className="drq-judgment">
                    <div className="drq-judgment-card">
                      <div className="drq-judgment-card__head">
                        <h3 className="drq-judgment-card__title">Quality</h3>
                      </div>
                      {showingDerived && derivedSourceLabel ? (
                        <p className="drq-judgment-card__derived">{derivedSourceLabel}</p>
                      ) : null}
                      <div className="drq-quality-axes">
                        {QUALITY_AXES.map((axis) => {
                          const value = qualityAxes[axis] ?? null;
                          const active = activeQualityAxis === axis;
                          return (
                            <div
                              key={axis}
                              className={"drq-quality-axis" + (active ? " drq-quality-axis--active" : "")}
                            >
                              <button
                                type="button"
                                className="drq-quality-axis__label"
                                onClick={() => setActiveQualityAxis(axis)}
                                title={`Focus ${QUALITY_AXIS_LABELS[axis]} (press q to cycle)`}
                              >
                                {QUALITY_AXIS_LABELS[axis]}
                              </button>
                              <div className="drq-rate-bar" role="group" aria-label={`Rate ${QUALITY_AXIS_LABELS[axis]}`}>
                                {[1, 2, 3, 4, 5].map((n) => (
                                  <button
                                    key={n}
                                    type="button"
                                    className={
                                      "drq-star-btn" +
                                      (value != null && value >= n ? " drq-star-btn--on" : "") +
                                      (showingDerived &&
                                      value == null &&
                                      derivedRating != null &&
                                      derivedRating >= n
                                        ? " drq-star-btn--derived"
                                        : "")
                                    }
                                    onClick={() => {
                                      setActiveQualityAxis(axis);
                                      void rateCurrent(n, axis);
                                    }}
                                    title={`Rate ${QUALITY_AXIS_LABELS[axis]} ${n}★`}
                                    aria-label={`Rate ${QUALITY_AXIS_LABELS[axis]} ${n} stars`}
                                  >
                                    <span className="drq-star-btn__n">{n}</span>
                                    <span className="drq-star-btn__glyph" aria-hidden="true">
                                      ★
                                    </span>
                                  </button>
                                ))}
                                <button
                                  type="button"
                                  className="drt-btn drq-clear-btn"
                                  disabled={value == null}
                                  onClick={() => {
                                    setActiveQualityAxis(axis);
                                    void rateCurrent(0, axis);
                                  }}
                                  title={`Clear ${QUALITY_AXIS_LABELS[axis]}`}
                                >
                                  Clear
                                </button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    <div className="drq-judgment-card">
                      <div className="drq-judgment-card__head">
                        <h3 className="drq-judgment-card__title" title="Appetite axis — separate from quality ★">
                          Appetite
                        </h3>
                      </div>
                      <AppetiteBar
                        embedded
                        appetite={appetite}
                        facet={appetiteFacet}
                        busy={appetiteBusy}
                        onSet={(state, facet) => void setAppetiteCurrent(state, facet)}
                        onFacetChange={setFacet}
                      />
                    </div>

                    <nav className="drq-nav" aria-label="Queue navigation">
                      <button type="button" className="drt-btn drq-nav__btn" onClick={goPrev} disabled={candidates.length <= 1}>
                        ← Prev
                      </button>
                      <button type="button" className="drt-btn drq-nav__btn" onClick={skipCurrent}>
                        Skip
                      </button>
                      <button type="button" className="drt-btn drq-nav__btn" onClick={goNext} disabled={candidates.length <= 1}>
                        Next →
                      </button>
                    </nav>

                    <div className="drq-batch-actions">
                      {candidates.length > 0 ? (
                        <SessionProgress done={ratedCount} total={candidates.length} label="Rated" />
                      ) : null}
                      <button
                        type="button"
                        className="drt-btn drq-batch-actions__dismiss"
                        disabled={refreshing || triageBusy}
                        onClick={() => void dismissBatchAndLoad()}
                      >
                        {refreshing || triageBusy ? "Dismissing…" : "Dismiss batch"}
                      </button>
                    </div>

                    <div className="drq-judgment-card">
                      <div className="drq-judgment-card__head">
                        <h3 className="drq-judgment-card__title">Disposition</h3>
                      </div>
                      <DispositionStatusPanel
                        markers={dispositionMarkers}
                        catalog={dispositionCatalogAll}
                        reasonDetail={reasonDetail}
                        updatedAt={dispositionUpdatedAt}
                        lastOutcome={dispositionLastOutcome}
                        lastActionMessage={dispositionLastAction}
                        saved={dispositionMarkers.length > 0}
                        lastTriagedAt={lastTriagedAt}
                        triagePassCount={triagePassCount}
                      />
                      <DispositionBar
                        embedded
                        entries={catalogEntries}
                        markers={dispositionMarkers}
                        promotions={promotions}
                        onToggle={(id, on) => void toggleDispositionMarker(id, on)}
                        onEditCatalog={() => setCatalogEditorOpen(true)}
                      />
                      <DispositionReasonsPanel
                        reasons={catalogReasons}
                        activeEntries={dispositionMarkers.filter((m) => catalogEntries.some((e) => e.id === m))}
                        markers={dispositionMarkers}
                        reasonDetail={reasonDetail}
                        onToggleReason={({ markerId, on, modifiers, note }) =>
                          void toggleDispositionMarker(markerId, on, { modifiers, note })
                        }
                      />
                      <DispositionRouter
                        steps={catalogSteps}
                        activeEntries={dispositionMarkers.filter((m) => catalogEntries.some((e) => e.id === m))}
                        lastStepId={lastStepId}
                        busy={dispositionBusy || triageBusy}
                        onRunStep={(id) => void runDispositionStepCurrent(id)}
                        onCommitAdvanceRoutes={(opts) => void commitAdvanceRoutes(opts)}
                      />
                    </div>
                  </div>
                </div>
              </section>

              <aside className="drq-side" aria-label="Sampler context">
                {current.tags && current.tags.length > 0 ? (
                  <div className="drq-side-block">
                    <h3 className="drq-side-h">Tags</h3>
                    <div className="drq-tags">
                      {current.tags.slice(0, 12).map((t) => (
                        <span key={t} className="drq-tag">
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : null}

                <div className="drq-side-block">
                  <h3 className="drq-side-h">Why this clip?</h3>
                  <ul className="drq-evidence">
                    {(current.evidence || []).length > 0 ? (
                      current.evidence!.map((ev) => <li key={ev}>{ev}</li>)
                    ) : (
                      <li className="drq-evidence--empty">No sampler notes for this row.</li>
                    )}
                  </ul>
                </div>

                {current.signals && Object.keys(current.signals).length > 0 ? (
                  <div className="drq-side-block">
                    <h3 className="drq-side-h">Signals</h3>
                    <dl className="drq-signals">
                      {Object.entries(current.signals).map(([k, v]) => (
                        <div key={k} className="drq-signal-row">
                          <dt>{k}</dt>
                          <dd className="mono">{typeof v === "number" ? v.toFixed(2) : String(v)}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                ) : null}

                {current.vision_reasons && current.vision_reasons.length > 0 ? (
                  <div className="drq-side-block">
                    <h3 className="drq-side-h">Vision would help</h3>
                    <ul className="drq-evidence drq-evidence--vision">
                      {current.vision_reasons.map((r) => (
                        <li key={r}>{r.replace(/_/g, " ")}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <div className="drq-side-links">
                  <a className="drt-btn" href={discoveryLibraryHref(current.relpath)}>
                    Open in Library
                  </a>
                  <a className="drt-btn" href={fileUrlFromRel(current.relpath)} target="_blank" rel="noreferrer">
                    Open file
                  </a>
                </div>
              </aside>
            </div>

            <CandidateStrip candidates={candidates} index={index} batchRated={batchRated} onSelect={setIndex} />
          </>
        ) : null}

        {!loading && !current && !error ? (
          <div className="drq-empty drq-state">
            <p className="drq-muted">Queue empty — start a new batch.</p>
            <button type="button" className="drq-btn-primary" disabled={refreshing} onClick={() => void load(true)}>
              New batch ({queueLimit})
            </button>
          </div>
        ) : null}

        {session?.next_steps && session.next_steps.length > 0 ? (
          <details className="drq-next-steps">
            <summary>After a batch</summary>
            <ol>
              {session.next_steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
          </details>
        ) : null}
      </div>
      {catalogEditorOpen ? (
        <DispositionCatalogEditor
          markers={catalogMarkers}
          busy={dispositionBusy}
          onClose={() => setCatalogEditorOpen(false)}
          onSave={saveCatalog}
        />
      ) : null}
    </div>
  );
}
