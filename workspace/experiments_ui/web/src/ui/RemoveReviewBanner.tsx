import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchAssetRemoveReview, purgeAssetRemove } from "./api";
import { patchCachedAppetite, peekAssetRatings, subscribeAssetRatings } from "./assetRatingsCache";
import { discoveryPoolsHref, workbenchHrefForMedia } from "./discoveryDeepLink";
import { queryKeys } from "./queryKeys";
import type { AssetRemoveReviewResponse } from "./types";

export function RemoveReviewBanner({ enabled }: { enabled: boolean }) {
  const queryClient = useQueryClient();
  const [msg, setMsg] = useState("");
  const [listOpen, setListOpen] = useState(false);
  const q = useQuery({
    queryKey: queryKeys.discovery.assetRemoveReview,
    queryFn: () => fetchAssetRemoveReview({ limit: 80 }),
    enabled,
    staleTime: 15_000,
  });
  useEffect(() => {
    if (!enabled) return;
    return subscribeAssetRatings((relpath) => {
      const appetite = peekAssetRatings(relpath)?.appetite ?? null;
      const listed = (
        queryClient.getQueryData(queryKeys.discovery.assetRemoveReview) as
          | AssetRemoveReviewResponse
          | undefined
      )?.items?.some((it) => it.relpath === relpath);
      if (appetite === "remove" || listed) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.discovery.assetRemoveReview });
      }
    });
  }, [enabled, queryClient]);
  const readyItems = (q.data?.items || []).filter((it) => it.purge_ready);
  const purgeMutation = useMutation({
    mutationFn: (relpaths: string[]) => purgeAssetRemove({ relpaths }),
    onSuccess: (res, relpaths) => {
      for (const rel of relpaths) patchCachedAppetite(rel, null, null);
      for (const row of res.results || []) {
        if (row.ok && row.relpath) patchCachedAppetite(row.relpath, null, null);
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.discovery.assetRemoveReview });
      void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.workProductsRoot });
      const n = res.purged ?? relpaths.length;
      setMsg(n === 1 ? "Deleted 1 output and its ratings." : `Deleted ${n} outputs and their ratings.`);
    },
    onError: (err) => {
      setMsg(err instanceof Error ? err.message : String(err));
    },
  });

  const items = q.data?.items || [];
  const count = q.data?.count ?? items.length;
  const purgeReady = q.data?.purge_ready ?? readyItems.length;
  const hasRefs = q.data?.has_references ?? 0;
  const busy = purgeMutation.isPending;
  const hasMarked = count > 0 || items.length > 0;
  if (!enabled) return null;
  if (!hasMarked && !busy && !q.isError) return null;

  const hint = q.isLoading
    ? "Scanning references…"
    : q.isError
      ? "Could not load review"
      : [
          purgeReady
            ? `${purgeReady} queued to delete`
            : count
              ? "none queued to delete"
              : null,
          count ? `${count} marked` : null,
          hasRefs ? `${hasRefs} have references` : null,
        ]
          .filter(Boolean)
          .join(" · ");

  const confirmDelete = (relpaths: string[]) => {
    if (!relpaths.length || busy) return;
    const n = relpaths.length;
    const ok = window.confirm(
      n === 1
        ? "Delete this output and its ratings? Nothing else uses it. The Workbench job is archived if it has no other videos."
        : `Delete ${n} outputs that have no dependents (and their ratings)? Workbench jobs are archived when they have no other videos left.`,
    );
    if (!ok) return;
    setMsg("");
    purgeMutation.mutate(relpaths);
  };

  return (
    <aside
      className={`remove-review-banner${listOpen ? "" : " remove-review-banner--collapsed"}`}
      aria-label="Remove appetite review"
    >
      <div className="remove-review-banner__head">
        <button
          type="button"
          className="remove-review-banner__toggle"
          aria-expanded={listOpen}
          aria-controls="remove-review-details"
          title={listOpen ? "Hide the delete queue" : "Show the delete queue"}
          onClick={() => setListOpen((open) => !open)}
        >
          <span className="remove-review-banner__chevron" aria-hidden>
            {listOpen ? "▾" : "▸"}
          </span>
          <strong>Remove review</strong>
        </button>
        <a className="remove-review-banner__follow-up" href={discoveryPoolsHref("retire")}>
          Follow-up Retire
        </a>
        <span className="remove-review-banner__meta">{hint}</span>
        {purgeReady ? (
          <button
            type="button"
            className="remove-review-banner__delete-all"
            disabled={busy}
            onClick={() => confirmDelete(readyItems.map((it) => it.relpath).filter(Boolean))}
          >
            {busy ? "Deleting…" : `Delete ${purgeReady} ready`}
          </button>
        ) : null}
      </div>
      {msg ? <p className="remove-review-banner__msg">{msg}</p> : null}
      {listOpen ? (
        <div id="remove-review-details">
          <p className="remove-review-banner__note">
            Hidden from lists and factory jobs. Marking Remove also stamps Retire on Follow-up.
            Ready items have no downstream jobs or pool memberships — delete removes the file,
            its ratings, and the Workbench job when no other videos remain. Change appetite to
            restore instead. Trash (Retire step) is recoverable; this Delete is not.
          </p>
          {items.length ? (
            <ul className="remove-review-banner__list">
              {items.slice(0, 24).map((it) => {
                const href = workbenchHrefForMedia({ relpath: it.relpath });
                const blockers = (it.blockers || []).join(" · ");
                return (
                  <li key={it.relpath} className={it.purge_ready ? "is-ready" : "has-refs"}>
                    <a href={href} title={it.relpath}>
                      {it.relpath.split("/").pop() || it.relpath}
                    </a>
                    <span>
                      {it.purge_ready ? "no dependents" : blockers || "has references"}
                    </span>
                    {it.purge_ready ? (
                      <button
                        type="button"
                        className="remove-review-banner__delete"
                        disabled={busy}
                        onClick={() => confirmDelete([it.relpath])}
                      >
                        Delete
                      </button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}
