import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchShapeFactoryPoolMembers } from "./api";
import { AppetitePreviewBadge } from "./AppetitePreviewBadge";
import { ProvenanceAppetitePanel } from "./ProvenanceAppetitePanel";
import { normalizeAppetiteRelpath } from "./workProductAppetite";
import { queryKeys } from "./queryKeys";
import type { ShapeFactoryMapDepositPool, ShapeFactoryPoolMember } from "./types";

const PAGE = 24;

function memberRelpath(member: ShapeFactoryPoolMember): string {
  return (
    normalizeAppetiteRelpath(member.relpath) ||
    normalizeAppetiteRelpath(member.thumb_relpath) ||
    ""
  );
}

export function FactoryPoolReview({
  familySlug,
  depositPool,
}: {
  familySlug: string;
  depositPool?: ShapeFactoryMapDepositPool | null;
}) {
  const [limit, setLimit] = useState(PAGE);
  const [selectedPath, setSelectedPath] = useState("");
  const query = useQuery({
    queryKey: queryKeys.shapeFactory.poolMembers({
      family: familySlug,
      offset: 0,
      limit,
    }),
    queryFn: () =>
      fetchShapeFactoryPoolMembers({
        family: familySlug,
        pool_id: depositPool?.pool_id,
        offset: 0,
        limit,
      }),
    staleTime: 15_000,
    refetchOnWindowFocus: false,
  });

  const items = query.data?.items?.length
    ? query.data.items
    : (depositPool?.members_preview || []).map((member) => ({
        path: member.path,
        relpath: member.relpath,
        thumb_url: member.thumb_url,
        url: member.url,
        basename: member.basename,
        job_key: member.job_key,
      }));
  const selected = useMemo(
    () => items.find((item) => (item.path || item.relpath) === selectedPath) || items[0] || null,
    [items, selectedPath],
  );
  const seedRel = selected ? memberRelpath(selected) : "";
  const total = query.data?.total ?? depositPool?.member_count ?? 0;

  return (
    <section className="sfmap-pool-review" id="sfmap-pool-review">
      <div className="sfmap-pool-review__head">
        <h3 className="sfmap-pool-review__title">
          Review deposits
          {depositPool?.pool_id ? <span className="factory-muted"> · {depositPool.pool_id}</span> : null}
        </h3>
        <p className="factory-muted sfmap-pool-review__hint">
          Newest deposited work first. Open a card, then mark appetite on the output or any older hop
          so hourlies stop (or prefer) that layer.
        </p>
      </div>
      {query.isLoading ? <p className="factory-muted">Loading pool members…</p> : null}
      {query.error instanceof Error ? <p className="factory-error">{query.error.message}</p> : null}
      <div className="sfmap-pool-review__grid">
        {items.map((item) => {
          const key = item.path || item.relpath || item.basename || "";
          const rel = memberRelpath(item);
          const on = (item.path || item.relpath) === (selected?.path || selected?.relpath);
          return (
            <button
              key={key}
              type="button"
              className={"sfmap-pool-review-card" + (on ? " sfmap-pool-review-card--on" : "")}
              onClick={() => setSelectedPath(item.path || item.relpath || "")}
            >
              <span className="sfmap-pool-review-card__thumb">
                {item.thumb_url || item.url ? (
                  <img src={item.thumb_url || item.url} alt="" loading="lazy" />
                ) : (
                  <span className="sfmap-pool-review-card__empty">no thumb</span>
                )}
                {rel ? <AppetitePreviewBadge relpath={rel} size="sm" /> : null}
              </span>
              <span className="sfmap-pool-review-card__name" title={item.basename || item.path}>
                {item.basename || "member"}
              </span>
            </button>
          );
        })}
        {!items.length && !query.isLoading ? (
          <div className="factory-empty">No deposited members yet</div>
        ) : null}
      </div>
      {items.length < total ? (
        <button
          type="button"
          className="sfmap-pool-review__more"
          onClick={() => setLimit((n) => n + PAGE)}
        >
          Load more ({items.length} / {total})
        </button>
      ) : null}
      {seedRel ? (
        <ProvenanceAppetitePanel
          layers={[
            {
              id: seedRel,
              role: "output",
              label: "This output",
              relpath: seedRel,
              thumbUrl: selected?.thumb_url,
              url: selected?.url,
            },
          ]}
          seedRelpath={seedRel}
          jobKey={selected?.job_key}
          familySlug={familySlug}
        />
      ) : null}
    </section>
  );
}
