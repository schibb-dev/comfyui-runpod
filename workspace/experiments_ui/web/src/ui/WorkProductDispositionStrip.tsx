import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchDispositionCatalog, toggleAssetDisposition } from "./api";
import {
  loadAssetRatings,
  patchCachedDisposition,
  peekAssetRatings,
  revalidateAssetRatings,
  subscribeAssetRatings,
} from "./assetRatingsCache";
import { discoveryPoolsHref } from "./discoveryDeepLink";
import { DispositionBar } from "./DispositionBar";
import { DISPOSITION_CLEAR_ALL, optimisticDispositionToggle } from "./dispositionOptimistic";
import { queryKeys } from "./queryKeys";
import type { DispositionCatalogMarker, DispositionReasonDetail } from "./types";
import { normalizeAppetiteRelpath } from "./workProductAppetite";

export const FALLBACK_DISPOSITION_ENTRIES: DispositionCatalogMarker[] = [
  { id: "refine", kind: "entry", label: "Refine", hint: "Fix this — known delta, act later" },
  { id: "investigate", kind: "entry", label: "Investigate", hint: "Look closer before routing" },
  { id: "advance", kind: "entry", label: "Advance", hint: "Good seed — extend / vary / derive" },
  { id: "park", kind: "entry", label: "Park", hint: "Re-evaluate later" },
  { id: "retire", kind: "entry", label: "Retire", hint: "Out of active work — not the same as Remove/delete", exclusive: true },
];

export function useAssetDisposition(relpath?: string | null): {
  key: string;
  markers: string[];
  reasonDetail: Record<string, DispositionReasonDetail>;
  updatedAt: string | null;
} {
  const key = normalizeAppetiteRelpath(relpath);
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    const bump = () => {
      if (!cancelled) setTick((n) => n + 1);
    };
    void loadAssetRatings(key)
      .then(bump)
      .catch(() => {});
    const unsub = subscribeAssetRatings((changed) => {
      if (changed === key) bump();
    });
    return () => {
      cancelled = true;
      unsub();
    };
  }, [key]);

  if (!key) return { key, markers: [], reasonDetail: {}, updatedAt: null };
  const seed = peekAssetRatings(key);
  return {
    key,
    markers: seed?.disposition_markers ?? [],
    reasonDetail: seed?.disposition_reason_detail ?? {},
    updatedAt: seed?.disposition_updated_at ?? null,
  };
}

/**
 * Compact follow-up mark — corollary to appetite.
 * Same disposition_index as the Rate queue (several entries may stack; Retire is exclusive).
 */
export function WorkProductDispositionStrip({
  relpath,
  disabledHint = "Follow-up needs a media path",
  showFollowUpLink = true,
}: {
  relpath?: string | null;
  disabledHint?: string;
  showFollowUpLink?: boolean;
}) {
  const queryClient = useQueryClient();
  const { key, markers, reasonDetail } = useAssetDisposition(relpath);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const catalogQuery = useQuery({
    queryKey: queryKeys.discovery.dispositionCatalog,
    queryFn: fetchDispositionCatalog,
    staleTime: 5 * 60_000,
  });

  const entries = useMemo(() => {
    const rows = catalogQuery.data?.entries;
    if (rows && rows.length) return rows.filter((e) => e.enabled !== false);
    return FALLBACK_DISPOSITION_ENTRIES;
  }, [catalogQuery.data?.entries]);

  const reasons = useMemo(
    () => (catalogQuery.data?.reasons || []).filter((r) => r.enabled !== false),
    [catalogQuery.data?.reasons],
  );

  const onToggle = useCallback(
    async (markerId: string, on: boolean) => {
      if (!key || busy) return;
      const prev = [...markers];
      const prevDetail = { ...reasonDetail };
      const optimistic = optimisticDispositionToggle(
        markers,
        reasonDetail,
        entries,
        reasons,
        markerId,
        on,
      );
      patchCachedDisposition(key, optimistic.markers, optimistic.reasonDetail);
      setBusy(true);
      setMsg("");
      try {
        const res = await toggleAssetDisposition({ relpath: key, marker: markerId, on });
        const saved = res.saved?.markers;
        patchCachedDisposition(key, Array.isArray(saved) ? saved : optimistic.markers, res.saved?.reason_detail, res.saved?.updated_at);
        setMsg(markerId === DISPOSITION_CLEAR_ALL ? "cleared" : on ? markerId : "cleared");
        void revalidateAssetRatings(key);
        void queryClient.invalidateQueries({ queryKey: ["discovery", "dispositionBuckets"] });
      } catch (e) {
        patchCachedDisposition(key, prev, prevDetail);
        setMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [key, busy, markers, reasonDetail, entries, reasons, queryClient],
  );

  if (!key) {
    return (
      <div className="wp-appetite-strip wp-disposition-strip wp-appetite-strip--disabled" aria-label="Follow-up unavailable">
        <span className="factory-muted">{disabledHint}</span>
      </div>
    );
  }

  const activeIds = entries.filter((e) => markers.includes(e.id)).map((e) => e.id);
  const followUpEntry = activeIds.length === 1 ? activeIds[0] : undefined;

  return (
    <div className="wp-appetite-strip wp-disposition-strip" aria-label="Follow-up — look at this later">
      <span className="wp-disposition-strip__label factory-muted">Follow-up</span>
      <DispositionBar
        entries={entries}
        markers={markers}
        busy={busy || catalogQuery.isLoading}
        embedded
        onToggle={(id, on) => void onToggle(id, on)}
      />
      {showFollowUpLink ? (
        <a
          className="wp-disposition-strip__all"
          href={discoveryPoolsHref(followUpEntry)}
          title="Browse videos marked for follow-up"
        >
          All marked
        </a>
      ) : null}
      {msg ? <span className="wp-appetite-strip__msg factory-muted">{msg}</span> : null}
    </div>
  );
}
