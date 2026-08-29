import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchDiscoveryWorkflowFacets } from "./api";
import { AssetJudgmentEditor } from "./AssetJudgmentEditor";
import { DiscoveryAssetLineagePanel } from "./DiscoveryAssetLineagePanel";
import { DiscoveryAssetRatingsPanel } from "./DiscoveryAssetRatingsPanel";
import { DiscoveryWorkflowFacetsPanel } from "./DiscoveryWorkflowFacetsPanel";
import { discoveryLibraryHref } from "./discoveryDeepLink";
import type { DiscoveryAssetLineageItemSummary, DiscoveryLibraryItem, DiscoveryWorkflowFacetsResponse } from "./types";

/** Minimal selection the Inspector needs. Any screen (Library / Factory / Rate) can build one. */
export type InspectorAsset = {
  relpath: string;
  library?: string;
  name?: string;
  group_id?: string | null;
  url?: string | null;
  video_url?: string | null;
  thumb_url?: string | null;
};

function fileUrlFromRel(relpath?: string | null): string {
  if (!relpath) return "";
  return "/files/" + encodeURIComponent(relpath.replace(/\\/g, "/"));
}

function basename(rel?: string | null): string {
  const p = (rel || "").replace(/\\/g, "/");
  return p.split("/").pop() || p;
}

function isVideoRel(rel?: string | null): boolean {
  return /\.(mp4|webm|mov|mkv)$/i.test(rel || "");
}

/**
 * Shared right-rail inspector for a selected media asset: preview + inline star
 * rating + progressive-disclosure sections (ratings rollup, lineage, workflow
 * facets). Screens pass their own action buttons (recover / replay / extend) via
 * `actions` so factory-specific logic stays in the factory screen.
 *
 * This is the composition that the Workbench reuses across every lens so a
 * selection reads the same everywhere.
 */
export function AssetInspector({
  asset,
  onOpenRelpath,
  actions,
  showMedia = true,
}: {
  asset: InspectorAsset | null;
  onOpenRelpath?: (relpath: string) => void | Promise<boolean | string>;
  actions?: React.ReactNode;
  showMedia?: boolean;
}) {
  const [facets, setFacets] = useState<DiscoveryWorkflowFacetsResponse | null>(null);
  const [facetsLoading, setFacetsLoading] = useState(false);
  const [facetsError, setFacetsError] = useState("");
  const [facetsRel, setFacetsRel] = useState<string | null>(null);

  const relpath = asset?.relpath || "";

  // Build a DiscoveryLibraryItem-shaped seed for the sub-panels (they key on relpath).
  const seedItem = useMemo<DiscoveryLibraryItem | null>(() => {
    if (!asset) return null;
    return {
      group_id: asset.group_id ?? undefined,
      relpath: asset.relpath,
      library: asset.library || "og",
      name: asset.name || basename(asset.relpath),
      mtime: 0,
      size: 0,
      sha256: "",
      url: asset.url || fileUrlFromRel(asset.relpath),
      video_url: asset.video_url ?? undefined,
      thumb_url: asset.thumb_url ?? undefined,
    } as DiscoveryLibraryItem;
  }, [asset]);

  useEffect(() => {
    setFacets(null);
    setFacetsError("");
    setFacetsRel(null);
  }, [relpath]);

  const loadFacets = useCallback(async () => {
    if (!relpath) return;
    setFacetsLoading(true);
    setFacetsError("");
    try {
      const d = await fetchDiscoveryWorkflowFacets(relpath);
      setFacets(d);
      setFacetsRel(relpath);
    } catch (e) {
      setFacetsError(e instanceof Error ? e.message : String(e));
    } finally {
      setFacetsLoading(false);
    }
  }, [relpath]);

  const onOpenSummary = useCallback(
    (s: DiscoveryAssetLineageItemSummary) => {
      const rel = s.relpath || s.video_relpath || s.thumb_relpath;
      if (rel && onOpenRelpath) void onOpenRelpath(rel);
      else if (rel) window.location.assign(discoveryLibraryHref(rel));
    },
    [onOpenRelpath],
  );

  if (!asset) {
    return <div className="asset-inspector asset-inspector--empty">Select an asset to inspect.</div>;
  }

  const video = Boolean(asset.video_url) || isVideoRel(asset.relpath);
  const previewUrl = asset.video_url || asset.url || fileUrlFromRel(asset.relpath);
  const posterUrl = asset.thumb_url || (video ? fileUrlFromRel(asset.relpath.replace(/\.mp4$/i, ".png")) : undefined);

  return (
    <div className="asset-inspector">
      <div className="asset-inspector__head">
        <h3 className="asset-inspector__name mono" title={asset.relpath}>
          {asset.name || basename(asset.relpath)}
        </h3>
        <div className="asset-inspector__links">
          <a className="drt-btn" href={discoveryLibraryHref(asset.relpath)}>
            Library
          </a>
          <a className="drt-btn" href={fileUrlFromRel(asset.relpath)} target="_blank" rel="noreferrer">
            File
          </a>
        </div>
      </div>

      {showMedia ? (
        <div className="asset-inspector__media">
          {video ? (
            <video
              key={asset.relpath}
              className="asset-inspector__player"
              src={previewUrl}
              poster={posterUrl}
              controls
              playsInline
              preload="metadata"
            />
          ) : (
            <img className="asset-inspector__img" src={previewUrl} alt="" loading="lazy" />
          )}
        </div>
      ) : null}

      <AssetJudgmentEditor relpath={relpath} layout="inline" />

      {actions ? <div className="asset-inspector__actions">{actions}</div> : null}

      <details className="asset-inspector__section" open>
        <summary>Ratings</summary>
        <DiscoveryAssetRatingsPanel seedItem={seedItem} onOpenRelpath={onOpenRelpath} />
      </details>

      <details className="asset-inspector__section">
        <summary>Lineage</summary>
        <DiscoveryAssetLineagePanel seedItem={seedItem} onOpenSummary={onOpenSummary} />
      </details>

      <details className="asset-inspector__section" onToggle={(e) => {
        if ((e.currentTarget as HTMLDetailsElement).open && !facets && !facetsLoading) void loadFacets();
      }}>
        <summary>Workflow facets</summary>
        <DiscoveryWorkflowFacetsPanel
          relpath={relpath}
          data={facets}
          probedRelpath={facetsRel}
          loading={facetsLoading}
          error={facetsError}
          onLoad={() => void loadFacets()}
        />
      </details>
    </div>
  );
}
