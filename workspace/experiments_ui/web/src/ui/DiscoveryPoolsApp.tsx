import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchDispositionBuckets, toggleAssetDisposition } from "./api";
import { patchCachedDisposition, revalidateAssetRatings } from "./assetRatingsCache";
import { discoveryLibraryHref, workbenchHref, workbenchHrefForMedia } from "./discoveryDeepLink";
import { PageHeader } from "./PageHeader";
import { queryKeys } from "./queryKeys";
import { WorkProductDispositionStrip, FALLBACK_DISPOSITION_ENTRIES } from "./WorkProductDispositionStrip";
import { DISPOSITION_CLEAR_ALL } from "./dispositionOptimistic";
import type { DispositionBucketItem } from "./types";

/** Disposition index is one JSON file — concurrent toggles lose writes. */
const BATCH_CONCURRENCY = 1;

function parseEntry(): string {
  try {
    return (new URLSearchParams(window.location.search).get("entry") || "").trim();
  } catch {
    return "";
  }
}

function setEntryInUrl(entry: string) {
  const url = new URL(window.location.href);
  if (entry) url.searchParams.set("entry", entry);
  else url.searchParams.delete("entry");
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function basename(rel?: string | null): string {
  const p = (rel || "").replace(/\\/g, "/");
  return p.split("/").pop() || p;
}

function thumbFor(item: DispositionBucketItem): string {
  if (item.thumb_url) return item.thumb_url;
  if (item.url && /\.(png|jpe?g|webp|gif)$/i.test(item.url)) return item.url;
  return "";
}

function isTrashed(item: DispositionBucketItem): boolean {
  return Boolean(item.trashed) || /\/_trash\//.test(item.relpath || "");
}

function isHiddenRemove(item: DispositionBucketItem): boolean {
  return item.appetite === "remove";
}

function itemEntries(item: DispositionBucketItem): string[] {
  if (item.entries?.length) return item.entries;
  return item.entry ? [item.entry] : [];
}

function itemEntryLabels(
  item: DispositionBucketItem,
  catalog: { id: string; label: string }[],
): string {
  return itemEntries(item)
    .map((id) => catalog.find((e) => e.id === id)?.label || id)
    .join(" · ");
}

async function mapPool<T>(rows: T[], limit: number, fn: (row: T) => Promise<void>): Promise<void> {
  let i = 0;
  const n = Math.max(1, Math.min(limit, rows.length));
  const workers = Array.from({ length: n }, async () => {
    while (i < rows.length) {
      const cur = rows[i++];
      await fn(cur);
    }
  });
  await Promise.all(workers);
}

function PoolThumb({ src, trashed }: { src: string; trashed?: boolean }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => {
    setBroken(false);
  }, [src]);
  if (!src || broken) {
    return (
      <div className="discovery-pools__thumb discovery-pools__thumb--empty">
        {trashed ? "Trashed" : "No preview"}
      </div>
    );
  }
  return (
    <img
      className="discovery-pools__thumb"
      src={src}
      alt=""
      loading="lazy"
      onError={() => setBroken(true)}
    />
  );
}

export function DiscoveryPoolsApp() {
  const queryClient = useQueryClient();
  const [entry, setEntry] = useState<string>(() => parseEntry());
  const [selected, setSelected] = useState<DispositionBucketItem | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const anchorRef = useRef<string | null>(null);

  const bucketsQuery = useQuery({
    queryKey: queryKeys.discovery.dispositionBuckets(null),
    queryFn: () => fetchDispositionBuckets(),
    staleTime: 15_000,
  });

  const entries = bucketsQuery.data?.entries?.length
    ? bucketsQuery.data.entries
    : FALLBACK_DISPOSITION_ENTRIES.map((e) => ({
        id: e.id,
        label: e.label,
        hint: e.hint,
        count: bucketsQuery.data?.counts?.[e.id] ?? 0,
      }));

  const items = useMemo(() => {
    const rows = bucketsQuery.data?.items || [];
    if (!entry) return rows;
    return rows.filter((r) => itemEntries(r).includes(entry));
  }, [bucketsQuery.data?.items, entry]);

  const pickedSet = useMemo(() => new Set(picked), [picked]);
  const visiblePickedCount = useMemo(
    () => items.reduce((n, it) => n + (pickedSet.has(it.relpath) ? 1 : 0), 0),
    [items, pickedSet],
  );
  const allVisiblePicked = items.length > 0 && visiblePickedCount === items.length;

  const selectTab = (id: string) => {
    const next = entry === id ? "" : id;
    setEntry(next);
    setEntryInUrl(next);
    setSelected(null);
    anchorRef.current = null;
  };

  const exitSelectMode = useCallback(() => {
    setSelectMode(false);
    setPicked([]);
    anchorRef.current = null;
  }, []);

  const enterSelectMode = useCallback(() => {
    setSelectMode(true);
    setPicked((prev) => {
      if (prev.length) return prev;
      return selected?.relpath ? [selected.relpath] : [];
    });
    if (selected?.relpath) anchorRef.current = selected.relpath;
  }, [selected]);

  useEffect(() => {
    if (!selectMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (busy) return;
      if (e.key === "Escape") {
        e.preventDefault();
        exitSelectMode();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
        e.preventDefault();
        setPicked((prev) => {
          const next = new Set(prev);
          for (const it of items) next.add(it.relpath);
          return [...next];
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectMode, busy, items, exitSelectMode]);

  const onTileClick = (it: DispositionBucketItem, e: React.MouseEvent) => {
    if (!selectMode) {
      setSelected(it);
      return;
    }
    const path = it.relpath;
    if (e.shiftKey && anchorRef.current) {
      const i0 = items.findIndex((x) => x.relpath === anchorRef.current);
      const i1 = items.findIndex((x) => x.relpath === path);
      if (i0 >= 0 && i1 >= 0) {
        const lo = Math.min(i0, i1);
        const hi = Math.max(i0, i1);
        const range = items.slice(lo, hi + 1).map((x) => x.relpath);
        setPicked((prev) => {
          const next = new Set(prev);
          for (const p of range) next.add(p);
          return [...next];
        });
        return;
      }
    }
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return [...next];
    });
    anchorRef.current = path;
  };

  const selectAllVisible = () => {
    setPicked((prev) => {
      const next = new Set(prev);
      for (const it of items) next.add(it.relpath);
      return [...next];
    });
  };

  const clearPicked = () => {
    setPicked([]);
    anchorRef.current = null;
  };

  const onClear = async (item: DispositionBucketItem) => {
    setMsg("");
    const marker = entry || DISPOSITION_CLEAR_ALL;
    try {
      await toggleAssetDisposition({ relpath: item.relpath, marker, on: false });
      const remaining = marker === DISPOSITION_CLEAR_ALL ? [] : itemEntries(item).filter((id) => id !== marker);
      patchCachedDisposition(item.relpath, remaining);
      void revalidateAssetRatings(item.relpath);
      void queryClient.invalidateQueries({ queryKey: ["discovery", "dispositionBuckets"] });
      if (selected?.relpath === item.relpath) setSelected(null);
      const hiddenNote = isHiddenRemove(item) ? " Still hidden from lists until you change appetite." : "";
      setMsg(
        marker === DISPOSITION_CLEAR_ALL
          ? `Cleared follow-up marks.${hiddenNote}`
          : `Cleared ${marker}.${hiddenNote}`,
      );
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const applyBatch = async (marker: string, on: boolean, label: string) => {
    if (busy || !picked.length) return;
    if (marker === "retire" && on && picked.length > 1) {
      const ok = window.confirm(
        `Retire ${picked.length} videos? Other follow-up marks on them will drop.`,
      );
      if (!ok) return;
    }
    if (marker === DISPOSITION_CLEAR_ALL && picked.length > 1) {
      const ok = window.confirm(`Clear all follow-up marks on ${picked.length} videos?`);
      if (!ok) return;
    }
    setBusy(true);
    setMsg("");
    setProgress({ done: 0, total: picked.length });
    const targets = [...picked];
    let failed = 0;
    let done = 0;
    try {
      await mapPool(targets, BATCH_CONCURRENCY, async (relpath) => {
        try {
          const res = await toggleAssetDisposition({ relpath, marker, on });
          const saved = res.saved?.markers;
          patchCachedDisposition(relpath, Array.isArray(saved) ? saved : on ? [marker] : []);
          void revalidateAssetRatings(relpath);
        } catch {
          failed += 1;
        } finally {
          done += 1;
          setProgress({ done, total: targets.length });
        }
      });
      void queryClient.invalidateQueries({ queryKey: ["discovery", "dispositionBuckets"] });
      const ok = targets.length - failed;
      const verb = on ? `Applied ${label}` : label;
      setMsg(failed ? `${verb} on ${ok}. ${failed} failed.` : `${verb} on ${ok}.`);
      if (selected && targets.includes(selected.relpath)) {
        if (!on && (marker === DISPOSITION_CLEAR_ALL || marker === selected.entry)) {
          setSelected(null);
        }
      }
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  const total = bucketsQuery.data?.count ?? items.length;
  const activeHint = entries.find((e) => e.id === entry)?.hint;
  const pileLabel = entries.find((e) => e.id === entry)?.label || entry;

  return (
    <div
      className={"pipeline-screen layout discovery-pools" + (selectMode ? " discovery-pools--selecting" : "")}
    >
      <PageHeader
        title="Follow-up"
        subtitle="Videos you marked to fix, look at, or vary — the corollary to appetite"
        actions={
          <>
            <a className="drt-btn" href="/discovery/rate" title="Rate queue still owns reasons and Advance steps">
              Rate queue
            </a>
            <a
              className="drt-btn"
              href={workbenchHref({ set: entry || "follow-up" })}
              title="Open this pile as a Workbench working set"
            >
              Workbench
            </a>
            <button
              type="button"
              className={"drt-btn" + (selectMode ? " discovery-pools__tab--on" : "")}
              aria-pressed={selectMode}
              disabled={busy}
              onClick={() => (selectMode ? exitSelectMode() : enterSelectMode())}
            >
              {selectMode ? "Done" : "Select"}
            </button>
          </>
        }
      />
      {msg ? <p className="factory-muted discovery-pools__msg">{msg}</p> : null}
      <p className="factory-muted discovery-pools__lead">
        Stack Refine / Investigate / Advance / Park on one video. Retire replaces those marks (and they
        replace Retire). Remove (appetite) hides from lists and factory and also stamps Retire here;
        Delete lives on <a href="/discovery/remove">Remove review</a>. Trash is recoverable; Delete is not.
      </p>
      <div className="discovery-pools__tabs" role="tablist" aria-label="Follow-up buckets">
        <button
          type="button"
          role="tab"
          aria-selected={!entry}
          className={"drt-btn" + (!entry ? " discovery-pools__tab--on" : "")}
          onClick={() => selectTab("")}
        >
          All ({total})
        </button>
        {entries.map((e) => (
          <button
            key={e.id}
            type="button"
            role="tab"
            aria-selected={entry === e.id}
            className={"drt-btn" + (entry === e.id ? " discovery-pools__tab--on" : "")}
            title={e.hint}
            onClick={() => selectTab(e.id)}
          >
            {e.label} ({e.count ?? 0})
          </button>
        ))}
      </div>
      {activeHint ? <p className="factory-muted discovery-pools__hint">{activeHint}</p> : null}

      {selectMode ? (
        <div className="discovery-pools__batch" role="region" aria-label="Batch selection">
          <div className="discovery-pools__batch-count">
            <span>
              {picked.length
                ? `${picked.length} selected${
                    picked.length !== visiblePickedCount ? ` (${visiblePickedCount} in this pile)` : ""
                  }`
                : "Click tiles to select · Shift-click a range"}
            </span>
            {progress ? (
              <span className="factory-muted">
                Working {progress.done}/{progress.total}
              </span>
            ) : null}
          </div>
          <div className="discovery-pools__batch-pick">
            <button
              type="button"
              className="drt-btn"
              disabled={busy || !items.length}
              onClick={allVisiblePicked ? () => setPicked((prev) => prev.filter((p) => !items.some((it) => it.relpath === p))) : selectAllVisible}
            >
              {allVisiblePicked ? "Unselect pile" : "Select pile"}
            </button>
            <button type="button" className="drt-btn" disabled={busy || !picked.length} onClick={clearPicked}>
              Clear selection
            </button>
          </div>
          <div className="discovery-pools__batch-actions" role="group" aria-label="Apply to selection">
            <span className="factory-muted discovery-pools__batch-label">Apply</span>
            {entries.map((e) => (
              <button
                key={e.id}
                type="button"
                className="drt-btn"
                disabled={busy || !picked.length}
                title={e.hint || `Mark ${e.label}`}
                onClick={() => void applyBatch(e.id, true, e.label)}
              >
                {e.label}
              </button>
            ))}
            {entry ? (
              <button
                type="button"
                className="drt-btn"
                disabled={busy || !picked.length}
                title={`Drop ${pileLabel} from the selection`}
                onClick={() => void applyBatch(entry, false, `Removed ${pileLabel}`)}
              >
                Remove from pile
              </button>
            ) : null}
            <button
              type="button"
              className="drt-btn"
              disabled={busy || !picked.length}
              title="Clear every follow-up mark on the selection"
              onClick={() => void applyBatch(DISPOSITION_CLEAR_ALL, false, "Cleared marks")}
            >
              Clear marks
            </button>
          </div>
        </div>
      ) : null}

      {bucketsQuery.isLoading ? <p className="factory-muted">Loading marked videos…</p> : null}
      {bucketsQuery.error instanceof Error ? (
        <p className="factory-error">{bucketsQuery.error.message}</p>
      ) : null}

      <div className="discovery-pools__body">
        <div className="discovery-pools__scroll">
          <div className="discovery-pools__grid" role={selectMode ? "listbox" : "list"} aria-multiselectable={selectMode || undefined}>
            {items.map((it) => {
              const src = thumbFor(it);
              const on = selected?.relpath === it.relpath;
              const checked = pickedSet.has(it.relpath);
              const trashed = isTrashed(it);
              const hidden = isHiddenRemove(it);
              return (
                <button
                  key={it.relpath}
                  type="button"
                  role={selectMode ? "option" : undefined}
                  aria-selected={selectMode ? checked : undefined}
                  className={
                    "discovery-pools__tile" +
                    (on && !selectMode ? " discovery-pools__tile--active" : "") +
                    (selectMode ? " discovery-pools__tile--select" : "") +
                    (checked ? " discovery-pools__tile--checked" : "")
                  }
                  title={it.relpath}
                  disabled={busy}
                  onClick={(e) => onTileClick(it, e)}
                >
                  {selectMode ? <span className="discovery-pools__check" aria-hidden="true" /> : null}
                  <PoolThumb src={src} trashed={trashed} />
                  <span className="discovery-pools__tile-entry">{itemEntryLabels(it, entries)}</span>
                  {trashed ? <span className="discovery-pools__tile-trashed">trashed</span> : null}
                  {hidden ? <span className="discovery-pools__tile-hidden">hidden</span> : null}
                  <span className="discovery-pools__tile-label">{it.name || basename(it.relpath)}</span>
                </button>
              );
            })}
          </div>
          {!bucketsQuery.isLoading && !items.length ? (
            <p className="factory-muted discovery-pools__empty">
              Nothing in this pile yet. Mark a video from Workbench, Library, or the Rate queue.
            </p>
          ) : null}
        </div>

        <aside className="discovery-pools__side" aria-label={selectMode ? "Batch selection" : "Selected video"}>
          {selectMode ? (
            <>
              <h2>{picked.length ? `${picked.length} selected` : "Select videos"}</h2>
              <p className="factory-muted">
                Actions in the bar apply to the whole selection. Shift-click selects a range. Esc or Done
                leaves select mode.
              </p>
              {picked.length ? (
                <ul className="discovery-pools__picked">
                  {picked.slice(0, 12).map((rel) => (
                    <li key={rel} className="mono">
                      {basename(rel)}
                    </li>
                  ))}
                  {picked.length > 12 ? <li className="factory-muted">+{picked.length - 12} more</li> : null}
                </ul>
              ) : null}
            </>
          ) : selected ? (
            <>
              <h2>{selected.name || basename(selected.relpath)}</h2>
              {selected.video_url || selected.url ? (
                <video
                  key={selected.relpath}
                  className="discovery-pools__player"
                  src={selected.video_url || selected.url || ""}
                  poster={thumbFor(selected)}
                  controls
                  playsInline
                  preload="metadata"
                />
              ) : null}
              <p className="mono discovery-pools__path">{selected.relpath}</p>
              {isTrashed(selected) ? (
                <p className="factory-muted">
                  In trash
                  {selected.original_relpath ? ` (was ${selected.original_relpath})` : ""}.
                </p>
              ) : null}
              {isHiddenRemove(selected) ? (
                <p className="factory-muted">
                  Hidden from lists and factory (Remove appetite). Delete is on{" "}
                  <a href="/discovery/remove">Remove review</a>
                  {" "}when nothing depends on it. Clearing Retire here leaves it hidden until you change appetite.
                </p>
              ) : null}
              {selected.note ? <p className="discovery-pools__note">{selected.note}</p> : null}
              <WorkProductDispositionStrip relpath={selected.relpath} showFollowUpLink={false} />
              <div className="discovery-pools__actions">
                <a className="drt-btn" href={discoveryLibraryHref(selected.relpath)}>
                  Library
                </a>
                <a
                  className="drt-btn"
                  href={workbenchHref({ set: entry || selected.entry || "follow-up" })}
                  title="Open this pile on Workbench"
                >
                  Workbench
                </a>
                <button type="button" className="drt-btn" onClick={() => void onClear(selected)}>
                  {entry ? "Remove from pile" : "Clear marks"}
                </button>
                <button
                  type="button"
                  className="drt-btn"
                  onClick={() => {
                    enterSelectMode();
                    if (selected?.relpath) setPicked([selected.relpath]);
                  }}
                >
                  Select more…
                </button>
              </div>
            </>
          ) : (
            <p className="factory-muted">Select a video to change its follow-up mark, or turn on Select for a batch.</p>
          )}
        </aside>
      </div>
    </div>
  );
}
