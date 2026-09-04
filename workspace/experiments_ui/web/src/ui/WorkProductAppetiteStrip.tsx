import React, { useCallback, useEffect, useState } from "react";
import { setAssetAppetite } from "./api";
import { AppetiteBar } from "./AppetiteBar";
import {
  loadAssetRatings,
  patchCachedAppetite,
  peekAssetRatings,
  revalidateAssetRatings,
  subscribeAssetRatings,
} from "./assetRatingsCache";
import type { Appetite, AppetiteFacet } from "./types";

export function normalizeAppetiteRelpath(raw: string | null | undefined): string {
  return String(raw || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
}

function ratingsToAppetite(
  relpath: string,
  defaultFacet: AppetiteFacet,
): { appetite: Appetite | null; facet: AppetiteFacet } {
  const seed = peekAssetRatings(relpath);
  return {
    appetite: (seed?.appetite as Appetite | null) ?? null,
    facet: (seed?.appetite_facet as AppetiteFacet) || defaultFacet,
  };
}

function inferDefaultFacet(relpath: string): AppetiteFacet {
  return /^(input\/)/i.test(relpath) ? "source" : "both";
}

/** Shared appetite read path so the preview badge and the Workbench strip stay in sync. */
export function useAssetAppetite(
  relpath?: string | null,
  defaultFacet?: AppetiteFacet,
): {
  key: string;
  appetite: Appetite | null;
  facet: AppetiteFacet;
} {
  const key = normalizeAppetiteRelpath(relpath);
  const fallbackFacet = defaultFacet || (key ? inferDefaultFacet(key) : "both");
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    const bump = () => {
      if (!cancelled) setTick((n) => n + 1);
    };
    void loadAssetRatings(key)
      .then(bump)
      .catch(() => {
        /* keep seed / empty */
      });
    const unsub = subscribeAssetRatings((changed) => {
      if (changed === key) bump();
    });
    return () => {
      cancelled = true;
      unsub();
    };
  }, [key]);

  if (!key) return { key, appetite: null, facet: fallbackFacet };
  const { appetite, facet } = ratingsToAppetite(key, fallbackFacet);
  return { key, appetite, facet };
}

/**
 * Compact appetite mark for any workproduct / media surface (Workbench, Factory Map, Still Gallery, …).
 * Same store as Rate queue / AssetInspector — biases this asset for future use.
 */
export function WorkProductAppetiteStrip({
  relpath,
  jobKey,
  familySlug,
  defaultFacet = "both",
  disabledHint = "Appetite needs a media path",
  onSaved,
}: {
  relpath?: string | null;
  jobKey?: string | null;
  familySlug?: string | null;
  /** Used when the asset has no recorded facet yet (stills default to ``source``). */
  defaultFacet?: AppetiteFacet;
  disabledHint?: string;
  onSaved?: (appetite: Appetite, facet: AppetiteFacet) => void;
}) {
  const { key, appetite, facet } = useAssetAppetite(relpath, defaultFacet);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [facetOverride, setFacetOverride] = useState<AppetiteFacet | null>(null);
  const shownFacet = facetOverride || facet;

  const onSet = useCallback(
    async (state: Appetite, nextFacet: AppetiteFacet) => {
      if (!key || busy) return;
      const prevAppetite = appetite;
      const prevFacet = shownFacet;
      setFacetOverride(nextFacet);
      patchCachedAppetite(key, state, nextFacet);
      setBusy(true);
      setMsg("");
      try {
        await setAssetAppetite({
          relpath: key,
          appetite: state,
          facet: nextFacet,
          job_key: jobKey || undefined,
          family_slug: familySlug || undefined,
        });
        setMsg(`${state} · ${nextFacet}`);
        onSaved?.(state, nextFacet);
        void revalidateAssetRatings(key);
      } catch (e) {
        setFacetOverride(null);
        patchCachedAppetite(key, prevAppetite, prevFacet);
        setMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [key, busy, appetite, shownFacet, jobKey, familySlug, onSaved],
  );

  if (!key) {
    return (
      <div className="wp-appetite-strip wp-appetite-strip--disabled" aria-label="Appetite unavailable">
        <span className="factory-muted">{disabledHint}</span>
      </div>
    );
  }

  return (
    <div className="wp-appetite-strip" aria-label="Appetite — do more with this">
      <AppetiteBar
        appetite={appetite}
        facet={shownFacet}
        busy={busy}
        onSet={(state, f) => void onSet(state, f)}
        onFacetChange={setFacetOverride}
      />
      {msg ? <span className="wp-appetite-strip__msg factory-muted">{msg}</span> : null}
    </div>
  );
}
