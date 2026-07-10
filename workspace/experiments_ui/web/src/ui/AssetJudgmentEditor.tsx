import React, { useCallback, useEffect, useState } from "react";
import { fetchDiscoveryAssetRatings, setAssetAppetite, setAssetRating } from "./api";
import { AppetiteBar } from "./AppetiteBar";
import type { Appetite, AppetiteFacet, DiscoveryAssetRatingsResponse } from "./types";

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
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
  const [derivedRating, setDerivedRating] = useState<number | null>(null);
  const [derivedSourceLabel, setDerivedSourceLabel] = useState<string | null>(null);
  const [appetite, setAppetite] = useState<Appetite | null>(null);
  const [appetiteFacet, setAppetiteFacet] = useState<AppetiteFacet>("both");
  const [rateBusy, setRateBusy] = useState(false);
  const [appetiteBusy, setAppetiteBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const applySeed = useCallback((r: DiscoveryAssetRatingsResponse | null | undefined) => {
    if (!r) return;
    const explicit = num(r.explicit?.rating);
    setExplicitRating(explicit);
    if (explicit == null) {
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
    async (stars: number) => {
      if (!relpath || rateBusy) return;
      setRateBusy(true);
      setMsg("");
      try {
        await setAssetRating({ relpath, stars });
        setExplicitRating(stars > 0 ? stars : null);
        if (stars > 0) {
          setDerivedRating(null);
          setDerivedSourceLabel(null);
          setMsg(`Saved ${stars}★`);
        } else {
          setMsg("Cleared rating");
        }
        await refreshAfterSave();
      } catch (e) {
        setMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setRateBusy(false);
      }
    },
    [relpath, rateBusy, refreshAfterSave],
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

  const showingDerived = explicitRating == null && derivedRating != null;
  const busy = rateBusy || appetiteBusy;

  const starBar = (
    <div className="drq-rate-bar" role="group" aria-label="Rate quality">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          className={
            "drq-star-btn" +
            (explicitRating != null && explicitRating >= n ? " drq-star-btn--on" : "") +
            (showingDerived && derivedRating != null && derivedRating >= n ? " drq-star-btn--derived" : "")
          }
          disabled={busy}
          onClick={() => void rate(n)}
          title={`Rate ${n}★`}
          aria-label={`Rate ${n} stars`}
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
        disabled={busy || explicitRating == null}
        onClick={() => void rate(0)}
        title="Clear rating"
      >
        {layout === "cards" ? "Clear" : "clear"}
      </button>
    </div>
  );

  if (layout === "cards") {
    return (
      <div className="drq-judgment drt-judgment" aria-label="Your judgment">
        <div className="drq-judgment-card">
          <div className="drq-judgment-card__head">
            <h3 className="drq-judgment-card__title">Quality</h3>
            <p className="drq-judgment-card__hint">
              Hand-tagged stars — saved to XMP and the ratings index
            </p>
          </div>
          {showingDerived && derivedSourceLabel ? (
            <p className="drq-judgment-card__derived">{derivedSourceLabel}</p>
          ) : null}
          {starBar}
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
      {starBar}
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
