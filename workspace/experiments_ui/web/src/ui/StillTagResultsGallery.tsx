import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchShapeFactoryStillTagResults } from "./api";
import { AppetitePreviewFrame } from "./AppetitePreviewBadge";
import { queryKeys } from "./queryKeys";
import { StillTagResultDetailModal, StillTagResultDetailPanel, StillTagTagsPanel } from "./StillTagResultTags";
import { scrollFocusItemIntoView, useScrollCenterFocus } from "./stillTagScrollFocus";
import { useNarrowLayout } from "./useNarrowLayout";
import {
  filterStillTagResults,
  stillTagResultFilterLabel,
  stillTagResultStatusLabel,
  stillTagResultStatusTone,
  stillTagResultTagGroups,
  type StillTagResultFilter,
  type StillTagResultItem,
} from "./stillTagResults";

type ResultsView = "focus" | "strip" | "grid" | "deck";

const VIEW_KEY = "still-tag-results.view";

function loadViewPreference(narrow: boolean): ResultsView {
  try {
    const raw = localStorage.getItem(VIEW_KEY);
    if (raw === "focus" || raw === "strip" || raw === "grid" || raw === "deck") return raw;
  } catch {
    /* ignore */
  }
  return narrow ? "focus" : "grid";
}

function persistViewPreference(view: ResultsView) {
  try {
    localStorage.setItem(VIEW_KEY, view);
  } catch {
    /* ignore */
  }
}

function shortCid(cid: string): string {
  const s = String(cid || "").trim();
  return s.length <= 14 ? s : `${s.slice(0, 10)}…`;
}

function StillTagResultCard({
  item,
  compact,
  onOpen,
}: {
  item: StillTagResultItem;
  compact?: boolean;
  onOpen?: (item: StillTagResultItem) => void;
}) {
  const tone = stillTagResultStatusTone(item.status);
  const { effective: tags } = stillTagResultTagGroups(item);
  const previewLimit = compact ? 6 : 10;
  const preview = tags.slice(0, previewLimit);
  return (
    <article
      className={`still-tag-result-card still-tag-result-card--${tone}${compact ? " still-tag-result-card--compact" : ""}${
        onOpen ? " still-tag-result-card--clickable" : ""
      }`}
    >
      <button
        type="button"
        className="still-tag-result-card__open"
        onClick={() => onOpen?.(item)}
        disabled={!onOpen}
        aria-label={tags.length ? `View all ${tags.length} tags` : "View still details"}
      >
        <div className="still-tag-result-card__media">
          {item.url ? (
            <AppetitePreviewFrame relpath={item.relpath}>
              <img className="still-tag-result-card__img" src={item.url} alt="" loading="lazy" />
            </AppetitePreviewFrame>
          ) : (
            <div className="still-tag-result-card__empty">No preview</div>
          )}
          <span className={`still-tag-result-card__status still-tag-result-card__status--${tone}`}>
            {stillTagResultStatusLabel(item.status)}
          </span>
        </div>
        <div className="still-tag-result-card__body">
          <code className="still-tag-result-card__cid" title={item.content_id}>
            {shortCid(item.content_id)}
          </code>
          {tags.length ? (
            <>
              <p className="still-tag-result-card__tags">{preview.join(", ")}</p>
              <span className="still-tag-result-card__view-all">
                View all {tags.length} tag{tags.length === 1 ? "" : "s"}
              </span>
            </>
          ) : item.status === "pending" ? (
            <p className="factory-muted still-tag-result-card__hint">Waiting for index hour / drain</p>
          ) : (
            <span className="still-tag-result-card__view-all">View details</span>
          )}
        </div>
      </button>
      {item.error_message ? (
        <p className="still-tag-result-card__error" title={item.error_message}>
          {item.error_message}
        </p>
      ) : null}
      {item.warning && !item.error_message ? (
        <p className="still-tag-result-card__warn" title={item.warning}>
          {item.warning}
        </p>
      ) : null}
    </article>
  );
}

function StillTagFilmstrip({
  items,
  selectedId,
  onSelect,
  scrollFocus = true,
}: {
  items: StillTagResultItem[];
  selectedId: string | null;
  onSelect: (item: StillTagResultItem) => void;
  /** When true, scrolling the strip updates the selected still. */
  scrollFocus?: boolean;
}) {
  const stripRef = useRef<HTMLDivElement | null>(null);
  const ids = useMemo(() => items.map((it) => it.content_id), [items]);
  const refs = useRef<Map<string, HTMLElement>>(new Map());

  const onFocusId = useCallback(
    (id: string) => {
      const hit = items.find((it) => it.content_id === id);
      if (hit && id !== selectedId) onSelect(hit);
    },
    [items, onSelect, selectedId],
  );

  useScrollCenterFocus({
    rootRef: stripRef,
    itemRefs: refs,
    itemIds: ids,
    onFocus: onFocusId,
    enabled: scrollFocus,
    axis: "x",
  });

  useEffect(() => {
    const el = selectedId ? refs.current.get(selectedId) : null;
    if (!el || !stripRef.current) return;
    scrollFocusItemIntoView(stripRef.current, el, "x");
  }, [selectedId, items.length]);

  const setTileRef = (id: string, el: HTMLButtonElement | null) => {
    if (el) refs.current.set(id, el);
    else refs.current.delete(id);
  };

  return (
    <div className="still-tag-results__filmstrip-wrap">
      <div className="still-tag-results__filmstrip" ref={stripRef} role="listbox" aria-label="Batch stills">
        {items.map((it) => {
          const tone = stillTagResultStatusTone(it.status);
          const selected = it.content_id === selectedId;
          return (
            <button
              key={it.content_id}
              type="button"
              role="option"
              aria-selected={selected}
              ref={(el) => setTileRef(it.content_id, el)}
              className={`still-tag-filmstrip__tile still-tag-filmstrip__tile--${tone}${selected ? " is-selected" : ""}`}
              title={`${stillTagResultStatusLabel(it.status)} · ${it.content_id}`}
              onClick={() => onSelect(it)}
            >
              {it.url ? (
                <AppetitePreviewFrame relpath={it.relpath}>
                  <img className="still-tag-filmstrip__img" src={it.url} alt="" loading="lazy" />
                </AppetitePreviewFrame>
              ) : (
                <span className="still-tag-filmstrip__empty">—</span>
              )}
              <span className={`still-tag-filmstrip__badge still-tag-filmstrip__badge--${tone}`}>
                {stillTagResultStatusLabel(it.status)}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function StillTagFocusView({
  items,
  focusedId,
  onFocus,
  compact,
}: {
  items: StillTagResultItem[];
  focusedId: string | null;
  onFocus: (item: StillTagResultItem) => void;
  compact?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sectionRefs = useRef<Map<string, HTMLElement>>(new Map());
  const skipScrollSyncRef = useRef(false);
  const ids = useMemo(() => items.map((it) => it.content_id), [items]);
  const focusIndex = items.findIndex((it) => it.content_id === focusedId);

  const onFocusId = useCallback(
    (id: string) => {
      const hit = items.find((it) => it.content_id === id);
      if (!hit) return;
      skipScrollSyncRef.current = true;
      onFocus(hit);
    },
    [items, onFocus],
  );

  useScrollCenterFocus({
    rootRef: scrollRef,
    itemRefs: sectionRefs,
    itemIds: ids,
    onFocus: onFocusId,
    enabled: true,
    axis: "y",
  });

  useEffect(() => {
    if (!focusedId) return;
    if (skipScrollSyncRef.current) {
      skipScrollSyncRef.current = false;
      return;
    }
    const el = sectionRefs.current.get(focusedId);
    scrollFocusItemIntoView(scrollRef.current, el || null, "y");
  }, [focusedId]);

  const jumpTo = (item: StillTagResultItem) => {
    skipScrollSyncRef.current = true;
    onFocus(item);
    const el = sectionRefs.current.get(item.content_id);
    scrollFocusItemIntoView(scrollRef.current, el || null, "y");
  };

  return (
    <div className="still-tag-focus">
      <div className="still-tag-focus__rail">
        <StillTagFilmstrip items={items} selectedId={focusedId} onSelect={jumpTo} scrollFocus={false} />
        <p className="still-tag-focus__hint factory-muted">
          Scroll to focus · {focusIndex >= 0 ? `${focusIndex + 1} / ${items.length}` : items.length} stills
        </p>
      </div>
      <div className="still-tag-focus__scroll" ref={scrollRef} aria-label="Scroll batch stills">
        {items.map((it) => {
          const focused = it.content_id === focusedId;
          return (
            <section
              key={it.content_id}
              ref={(el) => {
                if (el) sectionRefs.current.set(it.content_id, el);
                else sectionRefs.current.delete(it.content_id);
              }}
              className={`still-tag-focus__section${focused ? " is-focused" : ""}`}
              aria-current={focused ? "true" : undefined}
            >
              <StillTagResultDetailPanel item={it} compact={compact} />
            </section>
          );
        })}
      </div>
    </div>
  );
}

export function StillTagResultsGallery({
  runId,
  live,
  className,
}: {
  runId: string;
  live?: boolean;
  className?: string;
}) {
  const narrowLayout = useNarrowLayout(960);
  const [filter, setFilter] = useState<StillTagResultFilter>("all");
  const [view, setView] = useState<ResultsView>(() => loadViewPreference(narrowLayout));
  const [deckIndex, setDeckIndex] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailItem, setDetailItem] = useState<StillTagResultItem | null>(null);

  const resultsQuery = useQuery({
    queryKey: queryKeys.shapeFactory.stillTagResults(runId),
    queryFn: () => fetchShapeFactoryStillTagResults(runId),
    enabled: Boolean(runId),
    staleTime: live ? 3_000 : 30_000,
    refetchInterval: live ? 5_000 : false,
  });

  const items = Array.isArray(resultsQuery.data?.items) ? resultsQuery.data.items : [];
  const summary = resultsQuery.data?.summary;
  const filtered = useMemo(() => filterStillTagResults(items, filter), [items, filter]);

  const selectedItem = filtered.find((it) => it.content_id === selectedId) || filtered[0] || null;

  useEffect(() => {
    setDeckIndex(0);
    if (filtered.length) {
      setSelectedId((prev) => {
        if (prev && filtered.some((it) => it.content_id === prev)) return prev;
        return filtered[0]?.content_id || null;
      });
    } else {
      setSelectedId(null);
    }
  }, [filter, runId, filtered]);

  useEffect(() => {
    if (view !== "deck" && view !== "strip" && view !== "focus") return;
    const onKey = (e: KeyboardEvent) => {
      const vertical = view === "focus";
      const okKey = vertical
        ? e.key === "ArrowUp" || e.key === "ArrowDown"
        : e.key === "ArrowLeft" || e.key === "ArrowRight";
      if (!okKey) return;
      const target = e.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable=true]")) return;
      const idx = filtered.findIndex((it) => it.content_id === selectedId);
      if (idx < 0) return;
      e.preventDefault();
      const back = vertical ? e.key === "ArrowUp" : e.key === "ArrowLeft";
      const next = back ? Math.max(0, idx - 1) : Math.min(filtered.length - 1, idx + 1);
      const it = filtered[next];
      if (!it) return;
      if (view === "deck") setDeckIndex(next);
      else setSelectedId(it.content_id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view, filtered, selectedId]);

  const deckItem = filtered[deckIndex] || null;

  const setViewMode = (next: ResultsView) => {
    setView(next);
    persistViewPreference(next);
  };

  const focusItem = useCallback((it: StillTagResultItem) => {
    setSelectedId(it.content_id);
  }, []);

  return (
    <section className={`still-tag-results${className ? ` ${className}` : ""}`} aria-label="Still tag batch results">
      <div className="still-tag-results__toolbar">
        <div className="still-tag-results__counts factory-muted mono">
          {summary
            ? `${summary.done} tagged · ${summary.errors} err · ${summary.pending} pending`
            : `${items.length} stills`}
        </div>
        <div className="still-tag-results__filters" role="tablist" aria-label="Filter batch results">
          {(["all", "done", "errors", "pending"] as StillTagResultFilter[]).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={filter === key}
              className={filter === key ? "is-on" : ""}
              onClick={() => setFilter(key)}
            >
              {stillTagResultFilterLabel(key)}
            </button>
          ))}
        </div>
        <div className="still-tag-results__view-toggle">
          <button type="button" className={view === "focus" ? "is-on" : ""} onClick={() => setViewMode("focus")}>
            Focus
          </button>
          <button type="button" className={view === "strip" ? "is-on" : ""} onClick={() => setViewMode("strip")}>
            Strip
          </button>
          <button type="button" className={view === "grid" ? "is-on" : ""} onClick={() => setViewMode("grid")}>
            Grid
          </button>
          <button type="button" className={view === "deck" ? "is-on" : ""} onClick={() => setViewMode("deck")}>
            Deck
          </button>
        </div>
      </div>

      {resultsQuery.isLoading ? <p className="factory-muted">Loading batch results…</p> : null}
      {resultsQuery.error instanceof Error ? (
        <p className="factory-error">{resultsQuery.error.message}</p>
      ) : null}

      {!resultsQuery.isLoading && !filtered.length ? (
        <p className="factory-muted">No stills match this filter.</p>
      ) : null}

      {view === "focus" && filtered.length ? (
        <StillTagFocusView
          items={filtered}
          focusedId={selectedItem?.content_id || null}
          onFocus={focusItem}
          compact={narrowLayout}
        />
      ) : null}

      {view === "strip" && filtered.length ? (
        <div className="still-tag-results__stack">
          <StillTagFilmstrip
            items={filtered}
            selectedId={selectedItem?.content_id || null}
            onSelect={focusItem}
          />
          <div className="still-tag-results__stack-detail">
            {selectedItem ? (
              <StillTagResultDetailPanel item={selectedItem} compact={narrowLayout} />
            ) : (
              <p className="factory-muted">Select a still from the strip.</p>
            )}
          </div>
        </div>
      ) : null}

      {view === "grid" && filtered.length ? (
        <div className="still-tag-results__grid">
          {filtered.map((it) => (
            <StillTagResultCard key={it.content_id} item={it} onOpen={setDetailItem} />
          ))}
        </div>
      ) : null}

      {view === "deck" && deckItem ? (
        <div className="still-tag-results__deck">
          <div className="still-tag-results__deck-nav">
            <button
              type="button"
              disabled={deckIndex <= 0}
              onClick={() => setDeckIndex((i) => Math.max(0, i - 1))}
            >
              ← Prev
            </button>
            <span className="factory-muted mono">
              {deckIndex + 1} / {filtered.length}
            </span>
            <button
              type="button"
              disabled={deckIndex >= filtered.length - 1}
              onClick={() => setDeckIndex((i) => Math.min(filtered.length - 1, i + 1))}
            >
              Next →
            </button>
          </div>
          <StillTagResultCard item={deckItem} onOpen={setDetailItem} />
          <div className="still-tag-results__deck-tags">
            <StillTagTagsPanel item={deckItem} />
          </div>
        </div>
      ) : null}

      {detailItem ? <StillTagResultDetailModal item={detailItem} onClose={() => setDetailItem(null)} /> : null}
    </section>
  );
}
