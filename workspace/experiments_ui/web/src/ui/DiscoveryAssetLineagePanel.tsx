import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchDiscoveryAssetLineage } from "./api";
import type {
  DiscoveryAssetLineageAncestryNavEntry,
  DiscoveryAssetLineageEdgeRow,
  DiscoveryAssetLineageItemSummary,
  DiscoveryAssetLineageResponse,
  DiscoveryAssetLineageSiblingRow,
  DiscoveryLibraryItem,
} from "./types";

function str(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

function filesUrlFromRelpath(relpath: string): string {
  const norm = relpath.replace(/\\/g, "/").replace(/^\/+/, "");
  return `/files/${encodeURIComponent(norm)}`;
}

function lineageThumbUrl(
  item: DiscoveryAssetLineageItemSummary | undefined,
  resolveLibraryItem?: (s: DiscoveryAssetLineageItemSummary) => DiscoveryLibraryItem | null,
  workspaceRelpath?: string | null
): string | null {
  if (workspaceRelpath?.trim()) {
    return filesUrlFromRelpath(workspaceRelpath.trim());
  }
  if (!item) return null;
  const lib = resolveLibraryItem?.(item);
  if (lib?.thumb_url) return lib.thumb_url;
  if (lib?.url && /\.(png|jpe?g|webp|gif)$/i.test(lib.relpath || lib.name || "")) return lib.url;
  if (item.thumb_url) return item.thumb_url;
  if (item.url && (item.media_kind === "png" || item.media_kind === "image")) return item.url;
  const tr = str(item.thumb_relpath);
  if (tr) return filesUrlFromRelpath(tr);
  const rp = str(item.relpath);
  if (rp && /\.(png|jpe?g|webp|gif)$/i.test(rp)) return filesUrlFromRelpath(rp);
  return null;
}

function nodeLabel(item: DiscoveryAssetLineageItemSummary | undefined, fallback: string): string {
  if (!item) return fallback;
  const kind = str(item.media_kind);
  const name = str(item.name) || str(item.relpath).split("/").pop() || "";
  if (kind && name) return `${kind}: ${name}`;
  return name || fallback;
}

function ratingBadge(item: DiscoveryAssetLineageItemSummary | undefined): React.ReactNode {
  if (!item) return null;
  const explicit = typeof item.rating_explicit === "number" ? item.rating_explicit : null;
  const inferred = typeof item.rating_inferred === "number" ? item.rating_inferred : null;
  const effective = typeof item.rating_effective === "number" ? item.rating_effective : explicit ?? inferred;
  if (effective == null) return null;
  const ev = item.rating_evidence;
  const title =
    explicit != null
      ? `Explicit ${explicit}★`
      : inferred != null
        ? `Inferred ${inferred}★ (${ev?.n ?? "?"} outputs, ${ev?.keepers_4plus ?? "?"} keepers)`
        : `${effective}★`;
  const kind = explicit != null ? "explicit" : "inferred";
  return (
    <span className={`dal-rating dal-rating--${kind}`} title={title} aria-label={title}>
      {explicit != null ? "★" : "◇"}
      {Number.isInteger(effective) ? String(effective) : effective.toFixed(1)}
    </span>
  );
}

function externalInputLabel(item: DiscoveryAssetLineageItemSummary | undefined): string {
  const rp = str(item?.relpath) || str((item as { workspace_relpath?: string }).workspace_relpath);
  if (rp) {
    const norm = rp.replace(/^\/+/, "");
    if (norm.toLowerCase().startsWith("input/")) return norm;
    const base = norm.split("/").pop() || norm;
    return base ? `input/${base}` : norm;
  }
  return nodeLabel(item, "—");
}

function LineageNodeCard({
  item,
  groupId,
  onOpen,
  current,
  layout = "row",
  external = false,
  compact = false,
  resolveLibraryItem,
}: {
  item?: DiscoveryAssetLineageItemSummary;
  groupId: string;
  onOpen: (s: DiscoveryAssetLineageItemSummary) => void;
  current?: boolean;
  layout?: "row" | "chain";
  external?: boolean;
  /** Smaller pill for descendant outline lists. */
  compact?: boolean;
  resolveLibraryItem?: (s: DiscoveryAssetLineageItemSummary) => DiscoveryLibraryItem | null;
}) {
  const summary: DiscoveryAssetLineageItemSummary = item ?? { group_id: groupId };
  const label = external ? externalInputLabel(item) : nodeLabel(item, groupId || "—");
  const thumbRel =
    external ? str(item?.relpath) || str((item as { workspace_relpath?: string }).workspace_relpath) : null;
  const thumb = lineageThumbUrl(item, resolveLibraryItem, thumbRel);
  const kind = str(item?.media_kind);

  return (
    <button
      type="button"
      className={
        "dal-node-card" +
        (current ? " dal-node-card--current" : "") +
        (layout === "chain" ? " dal-node-card--chain" : "") +
        (external ? " dal-node-card--external" : "") +
        (compact ? " dal-node-card--compact" : "")
      }
      onClick={() =>
        onOpen({
          ...summary,
          relpath: str(item?.relpath) || str(item?.workspace_relpath) || summary.relpath,
          external: external || undefined,
        })
      }
      title={str(item?.relpath) || groupId}
    >
      <span className="dal-node-thumb-wrap" aria-hidden={thumb ? undefined : true}>
        {thumb ? (
          <img className="dal-node-thumb" src={thumb} alt="" loading="lazy" decoding="async" />
        ) : (
          <span className="dal-node-thumb dal-node-thumb--placeholder">{kind === "video" ? "▶" : external ? "in" : "◇"}</span>
        )}
      </span>
      <span className="dal-node-text">
        {current ? <span className="dal-node-badge">current</span> : null}
        {external ? <span className="dal-node-badge">input</span> : null}
        {ratingBadge(item)}
        <span className="dal-node-label">{label}</span>
      </span>
    </button>
  );
}

/** Nested outline nodes built from descendant edge rows (transitive preferred). */
type DescendantOutlineNode = {
  edge: DiscoveryAssetLineageEdgeRow;
  childGid: string;
  children: DescendantOutlineNode[];
};

function buildDescendantOutlineTree(seedGid: string, edges: DiscoveryAssetLineageEdgeRow[]): DescendantOutlineNode[] {
  const root = seedGid.trim();
  const byParent = new Map<string, DiscoveryAssetLineageEdgeRow[]>();
  for (const e of edges) {
    const p = str(e.parent_group_id).trim();
    if (!p) continue;
    const arr = byParent.get(p);
    if (arr) arr.push(e);
    else byParent.set(p, [e]);
  }
  for (const arr of byParent.values()) {
    arr.sort((a, b) => {
      const an = str(a.child?.name) || str(a.child_group_id);
      const bn = str(b.child?.name) || str(b.child_group_id);
      return an.localeCompare(bn, undefined, { sensitivity: "base" });
    });
  }

  const placed = new Set<string>();
  function walk(parent: string): DescendantOutlineNode[] {
    const rows = byParent.get(parent) ?? [];
    const out: DescendantOutlineNode[] = [];
    for (const edge of rows) {
      const cid = str(edge.child_group_id).trim();
      if (!cid || placed.has(cid)) continue;
      placed.add(cid);
      out.push({
        edge,
        childGid: cid,
        children: walk(cid),
      });
    }
    return out;
  }

  return root ? walk(root) : [];
}

function DescendantOutlineList({
  nodes,
  depth,
  onOpenSummary,
  resolveLibraryItem,
}: {
  nodes: DescendantOutlineNode[];
  /** 0 = direct descendants of seed (expanded by default); deeper rows start collapsed. */
  depth: number;
  onOpenSummary: (s: DiscoveryAssetLineageItemSummary) => void;
  resolveLibraryItem?: (s: DiscoveryAssetLineageItemSummary) => DiscoveryLibraryItem | null;
}) {
  if (nodes.length === 0) return null;
  return (
    <ul className={depth === 0 ? "dal-desc-outline dal-desc-outline--root" : "dal-desc-outline"}>
      {nodes.map((n, idx) => (
        <DescendantOutlineRow
          key={`${n.childGid}-${idx}`}
          node={n}
          depth={depth}
          onOpenSummary={onOpenSummary}
          resolveLibraryItem={resolveLibraryItem}
        />
      ))}
    </ul>
  );
}

function DescendantOutlineRow({
  node,
  depth,
  onOpenSummary,
  resolveLibraryItem,
}: {
  node: DescendantOutlineNode;
  depth: number;
  onOpenSummary: (s: DiscoveryAssetLineageItemSummary) => void;
  resolveLibraryItem?: (s: DiscoveryAssetLineageItemSummary) => DiscoveryLibraryItem | null;
}) {
  const hasKids = node.children.length > 0;
  const [expanded, setExpanded] = useState(depth === 0);

  return (
    <li className="dal-desc-outline-li">
      <div className="dal-desc-outline-row">
        {hasKids ? (
          <button
            type="button"
            className="dal-desc-caret"
            aria-expanded={expanded}
            aria-label={expanded ? "Collapse descendants" : "Expand descendants"}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
          >
            <span className={"dal-desc-caret-icon" + (expanded ? " dal-desc-caret-icon--open" : "")} aria-hidden>
              ▸
            </span>
          </button>
        ) : (
          <span className="dal-desc-caret dal-desc-caret--spacer" aria-hidden />
        )}
        <LineageNodeCard
          item={node.edge.child ?? undefined}
          groupId={node.childGid}
          onOpen={onOpenSummary}
          compact
          resolveLibraryItem={resolveLibraryItem}
        />
      </div>
      {hasKids && expanded ? (
        <DescendantOutlineList nodes={node.children} depth={depth + 1} onOpenSummary={onOpenSummary} resolveLibraryItem={resolveLibraryItem} />
      ) : null}
    </li>
  );
}

export function DiscoveryAssetLineagePanel({
  seedItem,
  onOpenSummary,
  resolveLibraryItem,
}: {
  seedItem: DiscoveryLibraryItem | null;
  onOpenSummary: (s: DiscoveryAssetLineageItemSummary) => void;
  resolveLibraryItem?: (s: DiscoveryAssetLineageItemSummary) => DiscoveryLibraryItem | null;
}) {
  const [data, setData] = useState<DiscoveryAssetLineageResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [advOpen, setAdvOpen] = useState(false);

  const load = useCallback(async () => {
    if (!seedItem) return;
    setLoading(true);
    setError("");
    try {
      // graph_only + no live infer: persisted edges only (fast). Live infer can take tens of seconds
      // and would block other Discovery panels while the API holds the GIL.
      const body = await fetchDiscoveryAssetLineage(seedItem.relpath, { graphOnly: true, inferParents: false });
      setData(body);
    } catch (e) {
      setData(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [seedItem]);

  useEffect(() => {
    setData(null);
    setError("");
    void load();
  }, [load]);

  const provenance = useMemo(() => {
    if (!data?.ok) return [] as DiscoveryAssetLineageAncestryNavEntry[];
    const chain = data.provenance_chain ?? data.ancestry_nav;
    return Array.isArray(chain) ? chain : [];
  }, [data]);

  const siblings = useMemo(() => {
    if (!data?.ok || !Array.isArray(data.siblings)) return [] as DiscoveryAssetLineageSiblingRow[];
    return data.siblings;
  }, [data]);

  const descendants = useMemo(() => {
    if (!data?.ok || !Array.isArray(data.descendants_direct_seed)) return [] as DiscoveryAssetLineageEdgeRow[];
    return data.descendants_direct_seed;
  }, [data]);

  /** Prefer transitive list (multi-level); fall back to direct children only. */
  const descendantEdgeRows = useMemo(() => {
    if (!data?.ok) return [] as DiscoveryAssetLineageEdgeRow[];
    const t = data.descendants_transitive;
    if (Array.isArray(t) && t.length > 0) return t;
    return descendants;
  }, [data, descendants]);

  const seedGidForDescendants = str(data?.seed?.group_id || seedItem?.group_id);

  const descendantOutline = useMemo(
    () => buildDescendantOutlineTree(seedGidForDescendants, descendantEdgeRows),
    [seedGidForDescendants, descendantEdgeRows]
  );

  const staleLineageApi = Boolean(
    data?.ok &&
      data.graph_only !== true &&
      data.provenance_chain === undefined &&
      data.siblings === undefined &&
      data.descendants_direct_seed === undefined
  );

  const seedGid = str(seedItem?.group_id);

  if (!seedItem) {
    return (
      <p className="discovery-mock-hint" style={{ marginTop: 0 }}>
        Select a library item to see provenance, siblings, and descendants.
      </p>
    );
  }

  return (
    <div className="dal-wrap" aria-label="Asset lineage">
      {loading && !data ? <p className="discovery-mock-hint">Loading lineage…</p> : null}
      {error ? <div className="dal-err">{error}</div> : null}
      {!loading && data && !data.ok ? (
        <div className="dal-err">
          {data.error}
          {data.detail ? <span className="dal-muted"> · {String(data.detail)}</span> : null}
        </div>
      ) : null}

      {staleLineageApi ? (
        <div className="dal-err">
          The Experiments API on port 8790 is outdated (missing graph-only lineage fields). Restart it so the Lineage tab can
          read <span className="mono">discovery_lineage_edges.json</span>:{" "}
          <span className="mono">npm run restart</span> or{" "}
          <span className="mono">docker compose restart comfyui</span>, then hard-refresh this page.
        </div>
      ) : null}

      {data?.ok && !staleLineageApi ? (
        <>
          <section className="dal-section" aria-labelledby="dal-provenance-heading">
            <h4 id="dal-provenance-heading" className="dal-h4">
              Provenance
            </h4>
            {provenance.length <= 1 ? (
              <p className="discovery-mock-hint">
                No upstream chain in the saved graph for this asset. Parent links appear when embedded prompt paths resolve to
                other indexed rows (backfill or persist while exploring).
              </p>
            ) : (
              <div className="dal-provenance-chain" role="list">
                {provenance.map((row, i) => {
                  const gid = str(row.group_id);
                  const isCurrent = gid === seedGid || row.role === "seed";
                  return (
                    <React.Fragment key={`${gid}-${i}`}>
                      {i > 0 ? (
                        <span className="dal-provenance-arrow" aria-hidden="true">
                          →
                        </span>
                      ) : null}
                      <span role="listitem">
                        <LineageNodeCard
                          item={row.item}
                          groupId={gid}
                          onOpen={onOpenSummary}
                          current={isCurrent}
                          layout="chain"
                          external={row.external === true}
                          resolveLibraryItem={resolveLibraryItem}
                        />
                      </span>
                    </React.Fragment>
                  );
                })}
              </div>
            )}
          </section>

          <section className="dal-section" aria-labelledby="dal-siblings-heading">
            <h4 id="dal-siblings-heading" className="dal-h4">
              Siblings
            </h4>
            {siblings.length === 0 ? (
              <p className="discovery-mock-hint">No siblings share a parent with this asset in the lineage graph.</p>
            ) : (
              <ul className="dal-nav-list">
                {siblings.map((srow, idx) => {
                  const item = srow.item;
                  const gid = str(srow.group_id);
                  return (
                    <li key={`${gid}-${idx}`}>
                      <LineageNodeCard
                        item={item}
                        groupId={gid}
                        onOpen={onOpenSummary}
                        resolveLibraryItem={resolveLibraryItem}
                      />
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section className="dal-section" aria-labelledby="dal-descendants-heading">
            <h4 id="dal-descendants-heading" className="dal-h4">
              Descendants
            </h4>
            {descendantEdgeRows.length === 0 ? (
              <p className="discovery-mock-hint">
                {seedItem.library === "input"
                  ? "No indexed og/wip outputs cite this file in embedded prompts yet."
                  : "No downstream assets in the graph list this row as a parent."}
              </p>
            ) : descendantOutline.length === 0 ? (
              <ul className="dal-nav-list dal-nav-list--desc-flat">
                {descendantEdgeRows.map((row, idx) => {
                  const child = row.child;
                  const cid = str(row.child_group_id);
                  return (
                    <li key={`${cid}-${idx}`}>
                      <LineageNodeCard
                        item={child ?? undefined}
                        groupId={cid}
                        onOpen={onOpenSummary}
                        compact
                        resolveLibraryItem={resolveLibraryItem}
                      />
                    </li>
                  );
                })}
              </ul>
            ) : (
              <nav className="dal-desc-outline-root" aria-label="Descendants tree">
                <DescendantOutlineList
                  nodes={descendantOutline}
                  depth={0}
                  onOpenSummary={onOpenSummary}
                  resolveLibraryItem={resolveLibraryItem}
                />
              </nav>
            )}
          </section>

          <details className="dal-details" open={advOpen} onToggle={(e) => setAdvOpen(e.currentTarget.open)}>
            <summary>Advanced</summary>
            <div className="dal-adv-body">
              <button type="button" className="dal-btn dal-btn--ghost" disabled={loading} onClick={() => void load()}>
                {loading ? "Refreshing…" : "Refresh from graph"}
              </button>
              {typeof data.merged_edge_count === "number" ? (
                <p className="dal-muted">Merged edges in graph: {String(data.merged_edge_count)}</p>
              ) : null}
            </div>
          </details>
        </>
      ) : null}
    </div>
  );
}
