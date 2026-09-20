import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchDiscoveryAssetLineage, setAssetAppetite } from "./api";
import { APPETITE_KEYMAP, AppetiteBar } from "./AppetiteBar";
import { AppetitePreviewBadge } from "./AppetitePreviewBadge";
import { patchCachedAppetite, revalidateAssetRatings } from "./assetRatingsCache";
import { layersFromLineage, mergeAppetiteLayers, type ProvenanceAppetiteLayer } from "./provenanceAppetite";
import { queryKeys } from "./queryKeys";
import type { Appetite, AppetiteFacet } from "./types";
import { afterAppetiteCommitted } from "./workProductAppetite";
import { useAssetAppetite } from "./WorkProductAppetiteStrip";

function LayerAppetiteRow({
  layer,
  selected,
  jobKey,
  familySlug,
  onSelect,
}: {
  layer: ProvenanceAppetiteLayer;
  selected: boolean;
  jobKey?: string | null;
  familySlug?: string | null;
  onSelect: () => void;
}) {
  const queryClient = useQueryClient();
  const { key, appetite, facet } = useAssetAppetite(layer.relpath);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const onSet = useCallback(
    async (state: Appetite | "", nextFacet: AppetiteFacet) => {
      if (!key || busy) return;
      if (!state && !appetite) return;
      const prevAppetite = appetite;
      const prevFacet = facet;
      patchCachedAppetite(key, state || null, state ? nextFacet : null);
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
        afterAppetiteCommitted(queryClient, key, state || null);
        setMsg(state || "unset");
        void revalidateAssetRatings(key);
      } catch (e) {
        patchCachedAppetite(key, prevAppetite, prevFacet);
        setMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [key, busy, appetite, facet, jobKey, familySlug, queryClient],
  );

  const thumb = layer.thumbUrl || (/\.(png|jpe?g|webp|gif)(\?|$)/i.test(layer.url || "") ? layer.url : "");

  return (
    <div
      className={"prov-appetite-layer" + (selected ? " prov-appetite-layer--selected" : "")}
      data-role={layer.role}
    >
      <button type="button" className="prov-appetite-layer__pick" onClick={onSelect}>
        <span className="prov-appetite-layer__thumb" aria-hidden="true">
          {thumb ? <img src={thumb} alt="" /> : <span className="prov-appetite-layer__thumb-empty" />}
          <AppetitePreviewBadge relpath={layer.relpath} size="sm" />
        </span>
        <span className="prov-appetite-layer__meta">
          <span className="prov-appetite-layer__role">{layer.label}</span>
          <span className="prov-appetite-layer__path" title={layer.relpath}>
            {layer.relpath.split("/").pop() || layer.relpath}
          </span>
        </span>
      </button>
      {key ? (
        <AppetiteBar
          appetite={appetite}
          facet={facet}
          busy={busy}
          iconsOnly
          onSet={(state, nextFacet) => void onSet(state, nextFacet)}
        />
      ) : (
        <span className="factory-muted">No path</span>
      )}
      {msg ? <span className="prov-appetite-layer__msg factory-muted">{msg}</span> : null}
    </div>
  );
}

export function ProvenanceAppetitePanel({
  layers,
  seedRelpath,
  jobKey,
  familySlug,
  fetchLineage = true,
  disabledHint = "Appetite needs a media path",
}: {
  layers: ProvenanceAppetiteLayer[];
  seedRelpath?: string | null;
  jobKey?: string | null;
  familySlug?: string | null;
  fetchLineage?: boolean;
  disabledHint?: string;
}) {
  const seed = String(seedRelpath || layers.find((layer) => layer.role === "output")?.relpath || "").trim();
  const lineageQuery = useQuery({
    queryKey: queryKeys.discovery.assetLineage(seed),
    queryFn: () =>
      fetchDiscoveryAssetLineage(seed, {
        maxDepth: 4,
        inferParents: true,
        inferChildren: false,
      }),
    enabled: Boolean(fetchLineage && seed),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const merged = useMemo(
    () => mergeAppetiteLayers(layersFromLineage(lineageQuery.data), layers),
    [layers, lineageQuery.data],
  );
  const [selectedRel, setSelectedRel] = useState("");

  useEffect(() => {
    if (!merged.length) {
      setSelectedRel("");
      return;
    }
    if (selectedRel && merged.some((layer) => layer.relpath === selectedRel)) return;
    const output = merged.find((layer) => layer.role === "output");
    setSelectedRel((output || merged[merged.length - 1]).relpath);
  }, [merged, selectedRel]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }
      const raw = e.key.length === 1 ? e.key.toLowerCase() : "";
      if (raw === "j" || raw === "k") {
        e.preventDefault();
        const idx = merged.findIndex((layer) => layer.relpath === selectedRel);
        const next = raw === "j" ? Math.min(merged.length - 1, idx + 1) : Math.max(0, idx <= 0 ? 0 : idx - 1);
        if (merged[next]) setSelectedRel(merged[next].relpath);
        return;
      }
      const appetite = raw === "n" ? "" : APPETITE_KEYMAP[raw];
      if (appetite === undefined && raw !== "n") return;
      const selectedRow = document.querySelector(".prov-appetite-layer--selected");
      const btn = selectedRow?.querySelector(
        raw === "n" ? ".appetite-btn--unset" : `.appetite-btn--${appetite}`,
      );
      if (btn instanceof HTMLButtonElement && !btn.disabled) {
        e.preventDefault();
        btn.click();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [merged, selectedRel]);

  if (!merged.length) {
    return (
      <div className="prov-appetite prov-appetite--disabled" aria-label="Appetite unavailable">
        <span className="factory-muted">{disabledHint}</span>
      </div>
    );
  }

  return (
    <section className="prov-appetite" aria-label="Appetite by provenance layer">
      <div className="prov-appetite__head">
        <h3 className="prov-appetite__title">Appetite</h3>
        <p className="prov-appetite__hint factory-muted">
          Mark any hop — source, ancestor, or this output. Remove/less on a hop also drops
          later pool members that reused it.
          {merged.length > 1 ? " j/k moves layers; z/x/c/v/b marks the selected one." : ""}
        </p>
      </div>
      <div className="prov-appetite__layers">
        {merged.map((layer) => (
          <LayerAppetiteRow
            key={layer.id}
            layer={layer}
            selected={layer.relpath === selectedRel}
            jobKey={jobKey}
            familySlug={familySlug}
            onSelect={() => setSelectedRel(layer.relpath)}
          />
        ))}
      </div>
    </section>
  );
}
