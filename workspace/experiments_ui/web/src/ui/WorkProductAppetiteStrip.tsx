import React, { useCallback, useEffect, useState } from "react";
import { setAssetAppetite } from "./api";
import { AppetiteBar } from "./AppetiteBar";
import {
  loadAssetRatings,
  patchCachedAppetite,
  peekAssetRatings,
  revalidateAssetRatings,
} from "./assetRatingsCache";
import type { Appetite, AppetiteFacet } from "./types";

export function normalizeAppetiteRelpath(raw: string | null | undefined): string {
  return String(raw || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
}

/**
 * Compact appetite mark for any workproduct surface (Workbench, Factory Map, …).
 * Same store as Rate queue / AssetInspector — biases this output for future use.
 */
export function WorkProductAppetiteStrip({
  relpath,
  jobKey,
  familySlug,
  disabledHint = "Appetite needs an output path",
}: {
  relpath?: string | null;
  jobKey?: string | null;
  familySlug?: string | null;
  disabledHint?: string;
}) {
  const key = normalizeAppetiteRelpath(relpath);
  const [appetite, setAppetite] = useState<Appetite | null>(null);
  const [facet, setFacet] = useState<AppetiteFacet>("both");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!key) {
      setAppetite(null);
      setFacet("both");
      setMsg("");
      return;
    }
    const seed = peekAssetRatings(key);
    if (seed) {
      setAppetite((seed.appetite as Appetite | null) ?? null);
      setFacet((seed.appetite_facet as AppetiteFacet) || "both");
    }
    let cancelled = false;
    void loadAssetRatings(key)
      .then((r) => {
        if (cancelled) return;
        setAppetite((r.appetite as Appetite | null) ?? null);
        setFacet((r.appetite_facet as AppetiteFacet) || "both");
      })
      .catch(() => {
        /* keep seed / empty */
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  const onSet = useCallback(
    async (state: Appetite, nextFacet: AppetiteFacet) => {
      if (!key || busy) return;
      const prevAppetite = appetite;
      const prevFacet = facet;
      setAppetite(state);
      setFacet(nextFacet);
      patchCachedAppetite(key, state, nextFacet);
      setBusy(true);
      setMsg("");
      try {
        const res = await setAssetAppetite({
          relpath: key,
          appetite: state,
          facet: nextFacet,
          job_key: jobKey || undefined,
          family_slug: familySlug || undefined,
        });
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
          setMsg(`${state} · ${nextFacet}`);
        }
        void revalidateAssetRatings(key).then((r) => {
          setAppetite((r.appetite as Appetite | null) ?? state);
          setFacet((r.appetite_facet as AppetiteFacet) || nextFacet);
        });
      } catch (e) {
        setAppetite(prevAppetite);
        setFacet(prevFacet);
        patchCachedAppetite(key, prevAppetite, prevFacet);
        setMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [key, busy, appetite, facet, jobKey, familySlug],
  );

  if (!key) {
    return (
      <div className="wp-appetite-strip wp-appetite-strip--disabled" aria-label="Appetite unavailable">
        <span className="factory-muted">{disabledHint}</span>
      </div>
    );
  }

  return (
    <div className="wp-appetite-strip" aria-label="Appetite — do more with this workproduct">
      <AppetiteBar
        appetite={appetite}
        facet={facet}
        busy={busy}
        onSet={(state, f) => void onSet(state, f)}
        onFacetChange={setFacet}
      />
      {msg ? <span className="wp-appetite-strip__msg factory-muted">{msg}</span> : null}
    </div>
  );
}
