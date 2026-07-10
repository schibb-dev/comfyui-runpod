import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchDiscoveryAssetRatings,
  fetchDiscoveryRatingSampler,
  fetchDispositionCatalog,
  fetchDispositionSuggest,
  recordBatchTriageComplete,
  runDispositionStep,
  saveDispositionCatalog,
  setAssetAppetite,
  setAssetRating,
  toggleAssetDisposition,
} from "./api";
import { AppetiteBar, APPETITE_FACET_CYCLE, APPETITE_KEYMAP } from "./AppetiteBar";
import { DispositionBar, DispositionRouter } from "./DispositionBar";
import { DispositionCatalogEditor } from "./DispositionCatalogEditor";
import { DispositionStatusPanel } from "./DispositionStatusPanel";
import { discoveryLibraryHref } from "./discoveryDeepLink";
import { PageHeader } from "./PageHeader";
import type {
  Appetite,
  AppetiteFacet,
  DiscoveryAssetRatingsResponse,
  DiscoveryRatingSamplerCandidate,
  DiscoveryRatingSamplerResponse,
  DispositionCatalogMarker,
  DispositionOutcome,
  DispositionPromotions,
} from "./types";

const QUEUE_LIMIT_KEY = "rating_queue_limit";
const DEFAULT_QUEUE_LIMIT = 100;
const QUEUE_LIMIT_OPTIONS = [25, 50, 100, 150] as const;
const APPETITE_FACET_KEY = "appetite_facet";
const LOOP_PLAYBACK_KEY = "rating_queue_loop_playback";

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

/** Split explicit XMP stars from derived/external/inferred ratings for the quality row. */
function resolveQualityDisplay(r: DiscoveryAssetRatingsResponse): {
  explicit: number | null;
  derived: number | null;
  derivedLabel: string | null;
} {
  const explicitBlock = r.explicit;
  const explicitRating =
    explicitBlock && typeof explicitBlock === "object" && typeof explicitBlock.rating === "number" && explicitBlock.rating >= 1
      ? explicitBlock.rating
      : null;
  if (explicitRating != null) {
    return { explicit: explicitRating, derived: null, derivedLabel: null };
  }

  const disk =
    explicitBlock && typeof explicitBlock === "object" ? explicitBlock.verification?.xmp_on_disk : undefined;
  if (typeof disk === "number" && disk >= 1) {
    return {
      explicit: null,
      derived: disk,
      derivedLabel: "External XMP on disk — stars shown below are not your explicit rating yet",
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
    return { explicit: null, derived: Math.round(effective), derivedLabel };
  }

  return { explicit: null, derived: null, derivedLabel: null };
}

function applyQualityFromRatings(
  r: DiscoveryAssetRatingsResponse,
  setExplicit: (n: number | null) => void,
  setDerived: (n: number | null) => void,
  setDerivedLabel: (s: string | null) => void,
) {
  try {
    const q = resolveQualityDisplay(r);
    setExplicit(q.explicit);
    setDerived(q.derived);
    setDerivedLabel(q.derivedLabel);
  } catch {
    setExplicit(null);
    setDerived(null);
    setDerivedLabel(null);
  }
}

function hasEntryDisposition(markers: string[], catalog: DispositionCatalogMarker[]): boolean {
  return markers.some((m) => catalog.some((c) => c.id === m && c.kind === "entry"));
}

function applyTriageFromRatings(
  r: DiscoveryAssetRatingsResponse,
  setLastTriagedAt: (t: string | null) => void,
  setTriagePassCount: (n: number) => void,
) {
  setLastTriagedAt(r.last_triaged_at ?? null);
  setTriagePassCount(r.triage_pass_count ?? 0);
}

function applyDispositionFromRatings(
  r: DiscoveryAssetRatingsResponse,
  setMarkers: (m: string[]) => void,
  setUpdatedAt: (t: string | null) => void,
  setLastOutcome: (o: DispositionOutcome | null) => void,
) {
  setMarkers(r.disposition_markers ?? []);
  setUpdatedAt(r.disposition_updated_at ?? null);
  setLastOutcome(r.disposition_last_outcome ?? null);
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
  if (!message) return null;
  const kind = /fail|error|no xmp/i.test(message)
    ? "warn"
    : /saved|found|fast-track|appetite|disposition|queued|trash|archived|routed|replay|extend/i.test(message)
      ? "ok"
      : "info";
  return (
    <div className={"drq-toast drq-toast--" + kind} role="status" aria-live="polite">
      {message}
    </div>
  );
}

function CandidateStrip({
  candidates,
  index,
  batchDisposition,
  onSelect,
}: {
  candidates: DiscoveryRatingSamplerCandidate[];
  index: number;
  batchDisposition: Record<string, boolean>;
  onSelect: (i: number) => void;
}) {
  const stripRef = useRef<HTMLDivElement>(null);

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

  return (
    <div className="drq-filmstrip">
      <div className="drq-filmstrip__label">Queue</div>
      <div className="drq-strip" ref={stripRef} role="listbox" aria-label="Rating queue">
        {candidates.map((c, i) => {
          const active = i === index;
          const isDone = Boolean(batchDisposition[c.relpath]);
          const b = bucketLabel(c.session_bucket);
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
                (c.session_bucket === "easy_down"
                  ? " drq-strip-item--down"
                  : c.session_bucket === "easy_up"
                    ? " drq-strip-item--up"
                    : "")
              }
              onClick={() => onSelect(i)}
              title={`${basename(c.relpath)} — ${b.text}`}
            >
              <img
                className="drq-strip-thumb"
                src={fileUrlFromRel(thumbRelFromVideo(c.relpath))}
                alt=""
                loading="lazy"
                onError={(e) => {
                  const img = e.currentTarget;
                  if (img.dataset.fallback !== "1") {
                    img.dataset.fallback = "1";
                    img.src = fileUrlFromRel(c.relpath);
                  }
                }}
              />
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
  const [batchDisposition, setBatchDisposition] = useState<Record<string, boolean>>({});
  const [explicitRating, setExplicitRating] = useState<number | null>(null);
  const [derivedRating, setDerivedRating] = useState<number | null>(null);
  const [derivedSourceLabel, setDerivedSourceLabel] = useState<string | null>(null);
  const [checkMsg, setCheckMsg] = useState("");
  const [rateBusy, setRateBusy] = useState(false);
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
  const [catalogMarkers, setCatalogMarkers] = useState<DispositionCatalogMarker[]>([]);
  const [promotions, setPromotions] = useState<DispositionPromotions | null>(null);
  const [catalogEditorOpen, setCatalogEditorOpen] = useState(false);
  const [loopPlayback, setLoopPlayback] = useState(loadLoopPlayback);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [videoAspect, setVideoAspect] = useState<"portrait" | "landscape" | "square" | null>(null);

  const onVideoMetadata = useCallback(() => {
    const v = videoRef.current;
    if (!v?.videoWidth || !v.videoHeight) return;
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
      const data = await fetchDiscoveryRatingSampler({ refresh, limit: queueLimit });
      if (!data.ok) {
        setError(data.error || "Failed to load rating queue");
        setSession(data);
        return;
      }
      setSession(data);
      setBatchDisposition({});
      setIndex(0);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [queueLimit]);

  useEffect(() => {
    void load(false);
  }, [load]);

  useEffect(() => {
    void fetchDispositionCatalog()
      .then((c) => {
        setCatalogEntries(c.entries ?? []);
        setCatalogSteps(c.steps ?? []);
        setCatalogMarkers(c.catalog?.markers ?? [...(c.entries ?? []), ...(c.steps ?? [])]);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!session?.candidates?.length || !catalogEntries.length) return;
    setBatchDisposition((prev) => {
      const next = { ...prev };
      for (const c of session.candidates ?? []) {
        if (next[c.relpath] === undefined) {
          next[c.relpath] = hasEntryDisposition(c.disposition_markers ?? [], catalogEntries);
        }
      }
      return next;
    });
  }, [session, catalogEntries]);

  const onQueueLimitChange = (n: number) => {
    setQueueLimit(n);
    try {
      localStorage.setItem(QUEUE_LIMIT_KEY, String(n));
    } catch {
      /* ignore */
    }
  };

  const candidates = session?.candidates ?? [];
  const current = candidates[index] ?? null;

  const dismissBatchAndLoad = useCallback(async () => {
    if (triageBusy) return;
    setTriageBusy(true);
    try {
      const rels = candidates.map((c) => c.relpath);
      if (rels.length) {
        const res = await recordBatchTriageComplete({ relpaths: rels });
        setCheckMsg(
          `Batch dismissed — ${res.committed_count ?? 0} with disposition saved, ${res.skipped_count ?? 0} returned to pool`,
        );
      }
      setBatchDisposition({});
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

  const markBatchDisposition = useCallback(
    (relpath: string, markers: string[]) => {
      setBatchDisposition((prev) => ({
        ...prev,
        [relpath]: hasEntryDisposition(markers, catalogEntries),
      }));
    },
    [catalogEntries],
  );

  const goNext = useCallback(() => {
    setIndex((i) => Math.min(i + 1, Math.max(0, candidates.length - 1)));
    setCheckMsg("");
    setExplicitRating(null);
    setDerivedRating(null);
    setDerivedSourceLabel(null);
    setAppetite(null);
    setDispositionMarkers([]);
    setDispositionUpdatedAt(null);
    setDispositionLastOutcome(null);
    setDispositionLastAction("");
    setLastStepId(null);
    setLastTriagedAt(null);
    setTriagePassCount(0);
  }, [candidates.length]);

  const goPrev = useCallback(() => {
    setIndex((i) => Math.max(0, i - 1));
    setCheckMsg("");
    setExplicitRating(null);
    setDerivedRating(null);
    setDerivedSourceLabel(null);
    setAppetite(null);
    setDispositionMarkers([]);
    setDispositionUpdatedAt(null);
    setDispositionLastOutcome(null);
    setDispositionLastAction("");
    setLastStepId(null);
    setLastTriagedAt(null);
    setTriagePassCount(0);
  }, []);

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
      setAppetiteBusy(true);
      setCheckMsg("");
      try {
        const res = await setAssetAppetite({ relpath: current.relpath, appetite: state, facet });
        setAppetite(state);
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
        setCheckMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setAppetiteBusy(false);
      }
    },
    [current, appetiteBusy],
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
    if (inner.ok && (inner.extend || inner.replay_of_job_key)) {
      return inner.extend_fallback === "replay" ? "Queued replay" : "Queued extend / replay";
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
    async (markerId: string, on: boolean) => {
      if (!current || dispositionBusy) return;
      setDispositionBusy(true);
      setCheckMsg("");
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
          quality: q,
          appetite,
          facet: appetiteFacet,
        });
        setDispositionMarkers(res.saved?.markers ?? []);
        if (res.promotions) setPromotions(res.promotions);
        const label = catalogEntries.find((e) => e.id === markerId)?.label ?? markerId;
        const msg = on ? `Saved disposition: ${label}` : `Cleared: ${label}`;
        setDispositionLastAction(msg);
        setCheckMsg(msg);
        markBatchDisposition(current.relpath, res.saved?.markers ?? []);
        const ratings = await fetchDiscoveryAssetRatings(current.relpath);
        applyDispositionFromRatings(ratings, setDispositionMarkers, setDispositionUpdatedAt, setDispositionLastOutcome);
        applyTriageFromRatings(ratings, setLastTriagedAt, setTriagePassCount);
        markBatchDisposition(current.relpath, ratings.disposition_markers ?? []);
        if (res.saved?.updated_at) setDispositionUpdatedAt(String(res.saved.updated_at));
      } catch (e) {
        setCheckMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setDispositionBusy(false);
      }
    },
    [current, dispositionBusy, appetite, appetiteFacet, explicitRating, derivedRating, catalogEntries, markBatchDisposition],
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
        const ratings = await fetchDiscoveryAssetRatings(current.relpath);
        applyDispositionFromRatings(ratings, setDispositionMarkers, setDispositionUpdatedAt, setDispositionLastOutcome);
        applyTriageFromRatings(ratings, setLastTriagedAt, setTriagePassCount);
        markBatchDisposition(current.relpath, ratings.disposition_markers ?? []);
      } catch (e) {
        setLastStepId(null);
        setCheckMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setDispositionBusy(false);
      }
    },
    [current, dispositionBusy, appetiteFacet, catalogSteps, markBatchDisposition],
  );

  const saveCatalog = useCallback(async (markers: DispositionCatalogMarker[]) => {
    setDispositionBusy(true);
    try {
      const res = await saveDispositionCatalog({ markers });
      setCatalogEntries(res.entries ?? []);
      setCatalogSteps(res.steps ?? []);
      setCatalogMarkers(res.catalog?.markers ?? markers);
      setCatalogEditorOpen(false);
      setCheckMsg("Disposition catalog saved");
    } catch (e) {
      setCheckMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setDispositionBusy(false);
    }
  }, []);

  const rateCurrent = useCallback(
    async (stars: number) => {
      if (!current || rateBusy) return;
      setRateBusy(true);
      setCheckMsg("");
      try {
        await setAssetRating({ relpath: current.relpath, stars });
        setExplicitRating(stars > 0 ? stars : null);
        setDerivedRating(null);
        setDerivedSourceLabel(null);
        if (stars > 0) {
          setCheckMsg(`Saved ${stars}★`);
        } else {
          setCheckMsg("Cleared rating");
        }
      } catch (e) {
        setCheckMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setRateBusy(false);
      }
    },
    [current, rateBusy],
  );

  useEffect(() => {
    if (!current) return;
    setVideoAspect(null);
    setExplicitRating(null);
    setDerivedRating(null);
    setDerivedSourceLabel(null);
    setCheckMsg("");
    setDispositionMarkers(current.disposition_markers ?? []);
    setDispositionUpdatedAt(null);
    setDispositionLastOutcome(null);
    setDispositionLastAction("");
    setLastStepId(null);
    setLastTriagedAt(current.last_triaged_at ?? null);
    setTriagePassCount(current.triage_pass_count ?? 0);
    setAppetite(current.appetite ?? null);
    if (current.appetite_facet) setAppetiteFacet(current.appetite_facet);
    void fetchDiscoveryAssetRatings(current.relpath)
      .then((r) => {
        applyQualityFromRatings(r, setExplicitRating, setDerivedRating, setDerivedSourceLabel);
        if (r.appetite) setAppetite(r.appetite);
        if (r.appetite_facet) setAppetiteFacet(r.appetite_facet);
        applyDispositionFromRatings(r, setDispositionMarkers, setDispositionUpdatedAt, setDispositionLastOutcome);
        applyTriageFromRatings(r, setLastTriagedAt, setTriagePassCount);
        markBatchDisposition(current.relpath, r.disposition_markers ?? []);
      })
      .catch(() => {});
  }, [current?.relpath]);

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
        void rateCurrent(parseInt(e.key, 10));
      } else if (e.key === "0" || e.key === "Backspace") {
        e.preventDefault();
        void rateCurrent(0);
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
  }, [goNext, goPrev, skipCurrent, rateCurrent, setAppetiteCurrent, appetiteFacet, setFacet, toggleLoopPlayback]);

  const stats = session?.stats;
  const dispositionCount = useMemo(() => {
    return candidates.filter((c) => batchDisposition[c.relpath]).length;
  }, [candidates, batchDisposition]);

  const bucket = current ? bucketLabel(current.session_bucket) : null;
  const showingDerived = explicitRating == null && derivedRating != null;
  const dispositionEntryBadge = useMemo(
    () => entryLabelForMarker(dispositionMarkers, catalogEntries),
    [dispositionMarkers, catalogEntries],
  );
  const dispositionCatalogAll = useMemo(
    () => [...catalogEntries, ...catalogSteps],
    [catalogEntries, catalogSteps],
  );

  return (
    <div className="discovery-screen drq-screen">
      <div className="panel discovery-panel drq-root">
        <PageHeader
          title="Rate queue"
          subtitle="Work through a fixed batch — rotate with Next/Prev. Dismiss batch when done; clips without disposition return to the pool."
          actions={
            <div className="drq-header-actions">
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
              <button type="button" className="drt-btn" disabled={refreshing || triageBusy} onClick={() => void dismissBatchAndLoad()}>
                {refreshing || triageBusy ? "Dismissing…" : "Dismiss batch"}
              </button>
            </div>
          }
        >
          {candidates.length > 0 ? <SessionProgress done={dispositionCount} total={candidates.length} label="Disposition" /> : null}
          {stats ? (
            <p className="drq-session-hint factory-muted">
              Mix: ↓{stats.bucket_easy_down ?? "—"} quick rejects · ↑{stats.bucket_easy_up ?? "—"} likely keepers · ~
              {stats.bucket_middle ?? "—"} ambiguous
            </p>
          ) : null}
        </PageHeader>

        <details className="drq-shortcuts">
          <summary>Keyboard shortcuts</summary>
          <div className="drq-shortcuts-grid">
            <span><kbd>1</kbd>–<kbd>5</kbd> quality stars</span>
            <span><kbd>0</kbd> clear stars</span>
            <span><kbd>z</kbd><kbd>x</kbd><kbd>c</kbd><kbd>v</kbd> appetite</span>
            <span><kbd>g</kbd> cycle facet</span>
            <span><kbd>←</kbd><kbd>→</kbd> prev / next in batch</span>
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
                      loop={loopPlayback}
                      playsInline
                      preload="metadata"
                      onLoadedMetadata={onVideoMetadata}
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
                        <p className="drq-judgment-card__hint">
                          Do more <em>of</em> this — saves XMP, drives replay
                        </p>
                      </div>
                      {showingDerived && derivedSourceLabel ? (
                        <p className="drq-judgment-card__derived">{derivedSourceLabel}</p>
                      ) : null}
                      <div className="drq-rate-bar" role="group" aria-label="Rate quality">
                        {[1, 2, 3, 4, 5].map((n) => (
                          <button
                            key={n}
                            type="button"
                            className={
                              "drq-star-btn" +
                              (explicitRating != null && explicitRating >= n ? " drq-star-btn--on" : "") +
                              (showingDerived && derivedRating != null && derivedRating >= n
                                ? " drq-star-btn--derived"
                                : "")
                            }
                            disabled={rateBusy}
                            onClick={() => void rateCurrent(n)}
                            title={`Rate ${n}★ (press ${n})`}
                            aria-label={`Rate ${n} stars`}
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
                          disabled={rateBusy || explicitRating == null}
                          onClick={() => void rateCurrent(0)}
                          title="Clear rating (press 0)"
                        >
                          Clear
                        </button>
                      </div>
                    </div>

                    <div className="drq-judgment-card">
                      <div className="drq-judgment-card__head">
                        <h3 className="drq-judgment-card__title" title="Appetite axis — separate from quality ★">
                          Appetite
                        </h3>
                        <p className="drq-judgment-card__hint">
                          Do more <em>with</em> this — steers derive / extend
                        </p>
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

                    <div className="drq-judgment-card">
                      <div className="drq-judgment-card__head">
                        <h3 className="drq-judgment-card__title">Disposition</h3>
                        <p className="drq-judgment-card__hint">
                          What to do next — routes into refine, extract, advance, or retire
                        </p>
                      </div>
                      <DispositionStatusPanel
                        markers={dispositionMarkers}
                        catalog={dispositionCatalogAll}
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
                        busy={dispositionBusy || appetiteBusy || rateBusy || triageBusy}
                        onToggle={(id, on) => void toggleDispositionMarker(id, on)}
                        onEditCatalog={() => setCatalogEditorOpen(true)}
                      />
                      <DispositionRouter
                        steps={catalogSteps}
                        activeEntries={dispositionMarkers.filter((m) => catalogEntries.some((e) => e.id === m))}
                        lastStepId={lastStepId}
                        busy={dispositionBusy}
                        onRunStep={(id) => void runDispositionStepCurrent(id)}
                      />
                    </div>
                  </div>

                  <nav className="drq-nav" aria-label="Queue navigation">
                    <button type="button" className="drt-btn" onClick={goPrev} disabled={index <= 0}>
                      ← Prev
                    </button>
                    <button type="button" className="drt-btn" onClick={skipCurrent}>
                      Skip
                    </button>
                    <button type="button" className="drt-btn" onClick={goNext} disabled={index >= candidates.length - 1}>
                      Next →
                    </button>
                  </nav>
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

            <CandidateStrip candidates={candidates} index={index} batchDisposition={batchDisposition} onSelect={setIndex} />
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
