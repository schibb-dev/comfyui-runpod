import type { Appetite, AppetiteFacet, DiscoveryAssetRatingsResponse, DiscoveryLibraryItem } from "./types";

export type DiscoveryLibraryRatingsRollup = NonNullable<DiscoveryLibraryItem["ratings"]>;

/** Map a full asset-ratings API payload to the compact list-row rollup. */
export function discoveryRatingsRollupFromResponse(r: DiscoveryAssetRatingsResponse): DiscoveryLibraryRatingsRollup {
  const out: DiscoveryLibraryRatingsRollup = {};
  const explicit = r.explicit?.rating;
  if (typeof explicit === "number" && Number.isFinite(explicit)) {
    out.rating_explicit = explicit;
  }
  const inferred = r.as_source?.inferred;
  if (typeof inferred === "number" && Number.isFinite(inferred)) {
    out.rating_inferred = inferred;
    out.rating_evidence = {
      n: r.as_source?.n,
      keepers_4plus: r.as_source?.keepers_4plus,
    };
  }
  if (typeof r.rating_effective === "number" && Number.isFinite(r.rating_effective)) {
    out.rating_effective = r.rating_effective;
  } else if (out.rating_explicit != null) {
    out.rating_effective = out.rating_explicit;
  } else if (out.rating_inferred != null) {
    out.rating_effective = out.rating_inferred;
  }
  if (r.appetite) out.appetite = r.appetite;
  if (r.appetite_facet) out.appetite_facet = r.appetite_facet;
  return out;
}

export const APPETITE_ROW_GLYPH: Record<Appetite, string> = {
  less: "−",
  neutral: "○",
  more: "+",
  fast_track: "»",
};

export const APPETITE_ROW_LABEL: Record<Appetite, string> = {
  less: "Less",
  neutral: "Neutral",
  more: "More",
  fast_track: "Fast-track",
};

export function appetiteRowTitle(appetite: Appetite, facet?: AppetiteFacet | null): string {
  const facetLabel =
    facet === "source" ? "source" : facet === "processing" ? "look" : facet === "both" ? "both" : "";
  const base = `Appetite: ${APPETITE_ROW_LABEL[appetite]}`;
  return facetLabel ? `${base} (${facetLabel})` : base;
}
