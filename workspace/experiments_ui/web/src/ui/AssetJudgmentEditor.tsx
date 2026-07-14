import React, { useCallback, useEffect, useState } from "react";
import { fetchDiscoveryAssetRatings, setAssetAppetite, setAssetRating } from "./api";
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
  const [rateBusy, setRateBusy] = useState(false);
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
    setExplicitRating(null);
    setQualityAxes({});
    setDerivedRating(null);
    setDerivedSourceLabel(null);
    setAppetite(null);
    setMsg("");
    if (!relpath) return;
    let cancelled = false;
    void fetchDiscoveryAssetRatings(relpath)
      .then((r) => {
        if (!cancelled) applySeed(r);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [relpath, applySeed]);

  useEffect(() => {
    applySeed(seed);
  }, [seed, applySeed]);

  const refreshAfterSave = useCallback(async () => {
    const ratings = await fetchDiscoveryAssetRatings(relpath);
    applySeed(ratings);
    onSaved?.(ratings);
    return ratings;
  }, [relpath, applySeed, onSaved]);

  const rate = useCallback(
    async (stars: number, axis: QualityAxis = activeQualityAxis) => {
      if (!relpath || rateBusy) return;
      setRateBusy(true);
      setMsg("");
      try {
        await setAssetRating({ relpath, stars, axis });
        const label = QUALITY_AXIS_LABELS[axis];
        if (stars > 0) {
          setMsg(`Saved ${label} ${stars}★`);
        } else {
          setMsg(`Cleared ${label}`);
        }
        await refreshAfterSave();
      } catch (e) {
        setMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setRateBusy(false);
      }
    },
    [relpath, rateBusy, refreshAfterSave, activeQualityAxis],
  );

  const setAppetiteState = useCallback(
    async (state: Appetite, facet: AppetiteFacet) => {
      if (!relpath || appetiteBusy) return;
      setAppetiteBusy(true);
      setMsg("");
      try {
        const res = await setAssetAppetite({ relpath, appetite: state, facet });
        setAppetite(state);
        if (state === "fast_track") {
          const q = res.saved?.queued;
          setMsg(
            q?.ok
              ? q.extend_fallback === "replay"
                ? "Fast-tracked — queued replay"
                : "Fast-tracked — queued Extend"
              : `Fast-track saved (${q?.reason || "no queue context"})`,
          );
        } else {
          setMsg(`Appetite: ${state} · ${facet}`);
        }
        await refreshAfterSave();
      } catch (e) {
        setMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setAppetiteBusy(false);
      }
    },
    [relpath, appetiteBusy, refreshAfterSave],
  );

  if (!relpath) return null;

  const showingDerived = Object.keys(qualityAxes).length === 0 && explicitRating == null && derivedRating != null;
  const busy = rateBusy || appetiteBusy;

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
                  disabled={busy}
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
                disabled={busy || value == null}
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
            busy={busy}
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
        busy={busy}
        onSet={(state, facet) => void setAppetiteState(state, facet)}
        onFacetChange={setAppetiteFacet}
      />
      {msg ? <span className="drq-rate-hint factory-muted">{msg}</span> : null}
    </div>
  );
}
