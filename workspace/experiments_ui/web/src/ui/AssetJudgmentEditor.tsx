import React, { useCallback, useEffect, useRef, useState } from "react";
import { setAssetAppetite, setAssetRating } from "./api";
import {
  patchCachedAppetite,
  patchCachedQuality,
  peekAssetRatings,
  revalidateAssetRatings,
  rememberAssetRatings,
} from "./assetRatingsCache";
import { AppetiteBar } from "./AppetiteBar";
import type {
  Appetite,
  AppetiteFacet,
  DiscoveryAssetRatingsResponse,
  QualityAxis,
  QualityAxesMap,
} from "./types";
import { QUALITY_AXES, QUALITY_AXIS_LABELS } from "./types";

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
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

function aggregateFromAxes(axes: QualityAxesMap): number | null {
  const vals = QUALITY_AXES.map((a) => axes[a]).filter((n): n is number => typeof n === "number" && n >= 1);
  if (!vals.length) return null;
  return Math.round(vals.reduce((s, n) => s + n, 0) / vals.length);
}

export function AssetJudgmentEditor({
  relpath,
  seed,
  layout = "inline",
  onSaved,
}: {
  relpath: string;
  /** Optional seed from a parent ratings fetch — avoids a flash before the first load. */
  seed?: DiscoveryAssetRatingsResponse | null;
  layout?: "inline" | "cards";
  onSaved?: (ratings: DiscoveryAssetRatingsResponse) => void;
}) {
  const [explicitRating, setExplicitRating] = useState<number | null>(null);
  const [qualityAxes, setQualityAxes] = useState<QualityAxesMap>({});
  const [activeQualityAxis, setActiveQualityAxis] = useState<QualityAxis>("subject_beauty");
  const [derivedRating, setDerivedRating] = useState<number | null>(null);
  const [derivedSourceLabel, setDerivedSourceLabel] = useState<string | null>(null);
  const [appetite, setAppetite] = useState<Appetite | null>(null);
  const [appetiteFacet, setAppetiteFacet] = useState<AppetiteFacet>("both");
  const rateSeqRef = useRef(0);
  const [appetiteBusy, setAppetiteBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const applySeed = useCallback((r: DiscoveryAssetRatingsResponse | null | undefined) => {
    if (!r) return;
    const axes = axesFromRatings(r);
    setQualityAxes(axes);
    const explicit = num(r.explicit?.rating);
    setExplicitRating(explicit);
    if (explicit == null && Object.keys(axes).length === 0) {
      const eff = num(r.rating_effective);
      const inferred = num(r.as_source?.inferred);
      const derived = eff ?? inferred;
      setDerivedRating(derived);
      if (derived != null) {
        const n = r.as_source?.n;
        setDerivedSourceLabel(
          n != null ? `Inferred ~${derived}★ from ${n} rated downstream output(s)` : `Inferred ~${derived}★`,
        );
      } else {
        setDerivedSourceLabel(null);
      }
    } else {
      setDerivedRating(null);
      setDerivedSourceLabel(null);
    }
    setAppetite(r.appetite ?? null);
    if (r.appetite_facet) setAppetiteFacet(r.appetite_facet);
  }, []);

  useEffect(() => {
    setMsg("");
    if (!relpath) return;
    const cached = peekAssetRatings(relpath);
    if (cached) applySeed(cached);
    else if (seed) applySeed(seed);
    else {
      setExplicitRating(null);
      setQualityAxes({});
      setDerivedRating(null);
      setDerivedSourceLabel(null);
      setAppetite(null);
    }
    let cancelled = false;
    void revalidateAssetRatings(relpath)
      .then((r) => {
        if (!cancelled) {
          rememberAssetRatings(relpath, r);
          applySeed(r);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [relpath, applySeed, seed]);

  useEffect(() => {
    if (!seed || !relpath) return;
    applySeed(seed);
    rememberAssetRatings(relpath, seed);
  }, [seed, applySeed, relpath]);

  const refreshAfterSave = useCallback(async () => {
    const ratings = await revalidateAssetRatings(relpath);
    rememberAssetRatings(relpath, ratings);
    applySeed(ratings);
    onSaved?.(ratings);
    return ratings;
  }, [relpath, applySeed, onSaved]);

  const rate = useCallback(
    async (stars: number, axis: QualityAxis = activeQualityAxis) => {
      if (!relpath) return;
      const seq = ++rateSeqRef.current;
      setMsg("");

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
      patchCachedQuality(relpath, nextAxes, aggregateFromAxes(nextAxes));

      try {
        const res = await setAssetRating({ relpath, stars, axis });
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

        const label = QUALITY_AXIS_LABELS[axis];
        setMsg(stars > 0 ? `Saved ${label} ${stars}★` : `Cleared ${label}`);
        void refreshAfterSave();
      } catch (e) {
        if (seq !== rateSeqRef.current) return;
        setQualityAxes(prevAxes);
        setExplicitRating(prevExplicit);
        setDerivedRating(prevDerived);
        setDerivedSourceLabel(prevDerivedLabel);
        setMsg(e instanceof Error ? e.message : String(e));
      }
    },
    [
      relpath,
      refreshAfterSave,
      activeQualityAxis,
      qualityAxes,
      explicitRating,
      derivedRating,
      derivedSourceLabel,
    ],
  );

  const setAppetiteState = useCallback(
    async (state: Appetite, facet: AppetiteFacet) => {
      if (!relpath || appetiteBusy) return;
      const prevAppetite = appetite;
      const prevFacet = appetiteFacet;
      setAppetite(state);
      setAppetiteFacet(facet);
      patchCachedAppetite(relpath, state, facet);
      setAppetiteBusy(true);
      setMsg("");
      try {
        await setAssetAppetite({ relpath, appetite: state, facet });
        setMsg(`Appetite: ${state} · ${facet}`);
        void refreshAfterSave();
      } catch (e) {
        setAppetite(prevAppetite);
        setAppetiteFacet(prevFacet);
        patchCachedAppetite(relpath, prevAppetite, prevFacet);
        setMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setAppetiteBusy(false);
      }
    },
    [relpath, appetiteBusy, appetite, appetiteFacet, refreshAfterSave],
  );

  if (!relpath) return null;

  const showingDerived = Object.keys(qualityAxes).length === 0 && explicitRating == null && derivedRating != null;

  const axesBars = (
    <div className="drq-quality-axes">
      {QUALITY_AXES.map((axis) => {
        const value = qualityAxes[axis] ?? null;
        const active = activeQualityAxis === axis;
        return (
          <div key={axis} className={"drq-quality-axis" + (active ? " drq-quality-axis--active" : "")}>
            <button
              type="button"
              className="drq-quality-axis__label"
              onClick={() => setActiveQualityAxis(axis)}
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
                    (showingDerived && value == null && derivedRating != null && derivedRating >= n
                      ? " drq-star-btn--derived"
                      : "")
                  }

                  onClick={() => {
                    setActiveQualityAxis(axis);
                    void rate(n, axis);
                  }}
                  title={`Rate ${QUALITY_AXIS_LABELS[axis]} ${n}★`}
                  aria-label={`Rate ${QUALITY_AXIS_LABELS[axis]} ${n} stars`}
                >
                  {layout === "cards" ? (
                    <>
                      <span className="drq-star-btn__n">{n}</span>
                      <span className="drq-star-btn__glyph" aria-hidden="true">
                        ★
                      </span>
                    </>
                  ) : (
                    "★"
                  )}
                </button>
              ))}
              <button
                type="button"
                className="drt-btn drq-clear-btn"
                disabled={value == null}
                onClick={() => {
                  setActiveQualityAxis(axis);
                  void rate(0, axis);
                }}
                title={`Clear ${QUALITY_AXIS_LABELS[axis]}`}
              >
                {layout === "cards" ? "Clear" : "clear"}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );

  if (layout === "cards") {
    return (
      <div className="drq-judgment drt-judgment" aria-label="Your judgment">
        <div className="drq-judgment-card">
          <div className="drq-judgment-card__head">
            <h3 className="drq-judgment-card__title">Quality</h3>
            <p className="drq-judgment-card__hint">
              Subject · Render · Action — aggregate saved to XMP
            </p>
          </div>
          {showingDerived && derivedSourceLabel ? (
            <p className="drq-judgment-card__derived">{derivedSourceLabel}</p>
          ) : null}
          {axesBars}
          {explicitRating != null ? (
            <p className="drq-rate-hint factory-muted">Aggregate ★ {explicitRating}</p>
          ) : null}
        </div>
        <div className="drq-judgment-card">
          <div className="drq-judgment-card__head">
            <h3 className="drq-judgment-card__title">Appetite</h3>
            <p className="drq-judgment-card__hint">Direction for more work — separate from quality ★</p>
          </div>
          <AppetiteBar
            embedded
            appetite={appetite}
            facet={appetiteFacet}
            busy={appetiteBusy}
            onSet={(state, facet) => void setAppetiteState(state, facet)}
            onFacetChange={setAppetiteFacet}
          />
        </div>
        {msg ? <p className="drq-rate-hint factory-muted drt-judgment-msg">{msg}</p> : null}
      </div>
    );
  }

  return (
    <div className="asset-inspector__judgment" aria-label="Your judgment">
      {axesBars}
      <AppetiteBar
        appetite={appetite}
        facet={appetiteFacet}
        busy={appetiteBusy}
        onSet={(state, facet) => void setAppetiteState(state, facet)}
        onFacetChange={setAppetiteFacet}
      />
      {msg ? <span className="drq-rate-hint factory-muted">{msg}</span> : null}
    </div>
  );
}
