import React, { useCallback, useEffect, useMemo, useRef } from "react";
import { AppetitePreviewBadge, AppetitePreviewFrame } from "./AppetitePreviewBadge";
import { StillTagTagsPanel } from "./StillTagResultTags";
import { scrollFocusItemIntoView, useScrollCenterFocus } from "./stillTagScrollFocus";
import type { InputCurationStillItem } from "./types";
import type { StillGalleryViewMode } from "./stillGalleryViews";
import { stillGalleryItemPath, stillGalleryItemUrl } from "./stillGalleryViews";

type TagState = "untagged" | "queued" | "done";

function tagStateOf(it: Pick<InputCurationStillItem, "tag_status" | "provisional_tags">): TagState {
  const explicit = String(it.tag_status || "").trim().toLowerCase();
  if (explicit === "done" || explicit === "queued" || explicit === "untagged") return explicit;
  return (it.provisional_tags || []).length ? "done" : "untagged";
}

function tagStateLabel(status: TagState): string {
  if (status === "done") return "Tagged";
  if (status === "queued") return "Queued";
  return "Untagged";
}

function tagStateTone(status: TagState): "ok" | "queued" | "muted" {
  if (status === "done") return "ok";
  if (status === "queued") return "queued";
  return "muted";
}

function StillGalleryFilmstrip({
  items,
  focusedPath,
  onSelect,
  scrollFocus = true,
}: {
  items: InputCurationStillItem[];
  focusedPath: string | null;
  onSelect: (it: InputCurationStillItem) => void;
  scrollFocus?: boolean;
}) {
  const stripRef = useRef<HTMLDivElement | null>(null);
  const refs = useRef<Map<string, HTMLElement>>(new Map());
  const ids = useMemo(() => items.map(stillGalleryItemPath), [items]);

  const onFocusId = useCallback(
    (path: string) => {
      const hit = items.find((it) => stillGalleryItemPath(it) === path);
      if (hit && path !== focusedPath) onSelect(hit);
    },
    [items, focusedPath, onSelect],
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
    const el = focusedPath ? refs.current.get(focusedPath) : null;
    if (!el || !stripRef.current) return;
    scrollFocusItemIntoView(stripRef.current, el, "x");
  }, [focusedPath, items.length]);

  return (
    <div className="still-tag-results__filmstrip-wrap still-gallery__filmstrip-wrap">
      <div className="still-tag-results__filmstrip still-gallery__filmstrip" ref={stripRef} role="listbox" aria-label="Stills">
        {items.map((it) => {
          const path = stillGalleryItemPath(it);
          const status = tagStateOf(it);
          const tone = tagStateTone(status);
          const selected = path === focusedPath;
          const url = stillGalleryItemUrl(it);
          return (
            <button
              key={path}
              type="button"
              role="option"
              aria-selected={selected}
              ref={(el) => {
                if (el) refs.current.set(path, el);
                else refs.current.delete(path);
              }}
              className={`still-tag-filmstrip__tile still-gallery__filmstrip-tile still-tag-filmstrip__tile--${tone}${
                selected ? " is-selected" : ""
              }`}
              title={`${tagStateLabel(status)} · ${it.basename || path}`}
              onClick={() => onSelect(it)}
            >
              {url ? (
                <AppetitePreviewFrame relpath={it.relpath || it.path}>
                  <img className="still-tag-filmstrip__img" src={url} alt="" loading="lazy" />
                </AppetitePreviewFrame>
              ) : (
                <span className="still-tag-filmstrip__empty">—</span>
              )}
              <span className={`still-tag-filmstrip__badge still-tag-filmstrip__badge--${tone}`}>
                {tagStateLabel(status)}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function StillGalleryCompactCard({ it }: { it: InputCurationStillItem }) {
  const url = stillGalleryItemUrl(it);
  const rel = String(it.relpath || it.path || "").trim();
  const status = tagStateOf(it);
  const hasTags =
    (it.provisional_tags || it.effective_tags || it.editorial_tags || it.tags || []).length > 0;

  return (
    <div className="still-gallery__compact-card">
      <div className="still-gallery__compact-media">
        {url ? (
          <AppetitePreviewFrame relpath={rel}>
            <img className="still-gallery__compact-img" src={url} alt="" />
          </AppetitePreviewFrame>
        ) : (
          <div className="still-gallery__compact-empty">No preview</div>
        )}
        <span className={`still-gallery__tag-state still-gallery__tag-state--${status === "done" ? "done" : status === "queued" ? "queued" : ""}`}>
          {tagStateLabel(status)}
        </span>
      </div>
      <div className="still-gallery__compact-body">
        <strong className="still-gallery__compact-name">{it.basename || rel}</strong>
        {it.content_id ? (
          <code className="still-gallery__compact-cid" title={it.content_id}>
            {it.content_id.slice(0, 16)}…
          </code>
        ) : null}
        {hasTags ? (
          <StillTagTagsPanel
            item={{
              content_id: String(it.content_id || ""),
              status: status === "done" ? "done" : "pending",
              provisional_tags: it.provisional_tags || [],
              editorial_tags: it.editorial_tags || it.tags || [],
              effective_tags: it.effective_tags || [],
              relpath: rel,
            }}
            layout="effective_only"
          />
        ) : (
          <p className="factory-muted">No tags yet</p>
        )}
      </div>
    </div>
  );
}

export function StillGalleryFocusView({
  items,
  focusedPath,
  onFocus,
}: {
  items: InputCurationStillItem[];
  focusedPath: string | null;
  onFocus: (it: InputCurationStillItem) => void;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sectionRefs = useRef<Map<string, HTMLElement>>(new Map());
  const skipScrollSyncRef = useRef(false);
  const ids = useMemo(() => items.map(stillGalleryItemPath), [items]);
  const focusIndex = items.findIndex((it) => stillGalleryItemPath(it) === focusedPath);

  const onFocusPath = useCallback(
    (path: string) => {
      const hit = items.find((it) => stillGalleryItemPath(it) === path);
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
    onFocus: onFocusPath,
    enabled: true,
    axis: "y",
  });

  useEffect(() => {
    if (!focusedPath) return;
    if (skipScrollSyncRef.current) {
      skipScrollSyncRef.current = false;
      return;
    }
    const el = sectionRefs.current.get(focusedPath);
    scrollFocusItemIntoView(scrollRef.current, el || null, "y");
  }, [focusedPath]);

  const jumpTo = (it: InputCurationStillItem) => {
    skipScrollSyncRef.current = true;
    onFocus(it);
    const path = stillGalleryItemPath(it);
    const el = sectionRefs.current.get(path);
    scrollFocusItemIntoView(scrollRef.current, el || null, "y");
  };

  return (
    <div className="still-tag-focus still-gallery__focus">
      <div className="still-tag-focus__rail">
        <StillGalleryFilmstrip items={items} focusedPath={focusedPath} onSelect={jumpTo} scrollFocus={false} />
        <p className="still-tag-focus__hint factory-muted">
          Scroll to focus · {focusIndex >= 0 ? `${focusIndex + 1} / ${items.length}` : items.length} stills
        </p>
      </div>
      <div className="still-tag-focus__scroll still-gallery__focus-scroll" ref={scrollRef} aria-label="Scroll stills">
        {items.map((it) => {
          const path = stillGalleryItemPath(it);
          const focused = path === focusedPath;
          return (
            <section
              key={path}
              ref={(el) => {
                if (el) sectionRefs.current.set(path, el);
                else sectionRefs.current.delete(path);
              }}
              className={`still-tag-focus__section still-gallery__focus-section${focused ? " is-focused" : ""}`}
              aria-current={focused ? "true" : undefined}
            >
              <StillGalleryCompactCard it={it} />
            </section>
          );
        })}
      </div>
    </div>
  );
}

export function StillGalleryStripView({
  items,
  focusedPath,
  onFocus,
}: {
  items: InputCurationStillItem[];
  focusedPath: string | null;
  onFocus: (it: InputCurationStillItem) => void;
}) {
  const focused = items.find((it) => stillGalleryItemPath(it) === focusedPath) || items[0] || null;
  return (
    <div className="still-tag-results__stack still-gallery__strip">
      <StillGalleryFilmstrip items={items} focusedPath={focusedPath} onSelect={onFocus} />
      <div className="still-tag-results__stack-detail still-gallery__strip-detail">
        {focused ? <StillGalleryCompactCard it={focused} /> : <p className="factory-muted">Select a still from the strip.</p>}
      </div>
    </div>
  );
}

export function StillGalleryGridView({
  items,
  focusedPath,
  selectedPaths,
  deepLinkHitPath,
  onTileClick,
  stillTileDomId,
  stillMediaRelpath,
}: {
  items: InputCurationStillItem[];
  focusedPath: string | null;
  selectedPaths: Set<string>;
  deepLinkHitPath: string | null;
  onTileClick: (it: InputCurationStillItem, e: React.MouseEvent) => void;
  stillTileDomId: (it: InputCurationStillItem) => string;
  stillMediaRelpath: (it: InputCurationStillItem) => string;
}) {
  return (
    <div className="still-gallery__grid" role="listbox" aria-multiselectable="true" aria-label="Stills">
      {items.map((it) => {
        const path = stillGalleryItemPath(it);
        const active = focusedPath === path;
        const checked = selectedPaths.has(path);
        const deepHit = deepLinkHitPath === path;
        const src = stillGalleryItemUrl(it);
        const tagState = tagStateOf(it);
        return (
          <button
            key={path}
            id={stillTileDomId(it)}
            type="button"
            role="option"
            aria-selected={checked || active}
            className={
              "still-gallery__tile" +
              (active ? " still-gallery__tile--active" : "") +
              (checked ? " still-gallery__tile--checked" : "") +
              (deepHit ? " still-gallery__tile--deep-link" : "")
            }
            onClick={(e) => onTileClick(it, e)}
            title={it.basename || it.path}
          >
            {checked ? <span className="still-gallery__check" aria-hidden="true" /> : null}
            {tagState !== "untagged" ? (
              <span
                className={
                  "still-gallery__tag-state" +
                  (tagState === "done" ? " still-gallery__tag-state--done" : " still-gallery__tag-state--queued")
                }
                title={tagStateLabel(tagState)}
              >
                {tagStateLabel(tagState)}
              </span>
            ) : null}
            <AppetitePreviewBadge relpath={stillMediaRelpath(it)} />
            {src ? (
              <img className="still-gallery__thumb" src={src} alt="" loading="lazy" />
            ) : (
              <div className="still-gallery__thumb still-gallery__thumb--empty">No preview</div>
            )}
            <span className="still-gallery__tile-label">{it.basename || it.relpath}</span>
            {(it.tags || []).length ? (
              <span className="still-gallery__tile-tags">{(it.tags || []).slice(0, 3).join(" · ")}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export function StillGalleryDeckView({
  items,
  deckIndex,
  onDeckIndexChange,
  onOpenFocus,
}: {
  items: InputCurationStillItem[];
  deckIndex: number;
  onDeckIndexChange: (index: number) => void;
  onOpenFocus: (it: InputCurationStillItem) => void;
}) {
  const deckItem = items[deckIndex] || null;
  if (!deckItem) return null;
  return (
    <div className="still-tag-results__deck still-gallery__deck">
      <div className="still-tag-results__deck-nav">
        <button type="button" disabled={deckIndex <= 0} onClick={() => onDeckIndexChange(Math.max(0, deckIndex - 1))}>
          ← Prev
        </button>
        <span className="factory-muted mono">
          {deckIndex + 1} / {items.length}
        </span>
        <button
          type="button"
          disabled={deckIndex >= items.length - 1}
          onClick={() => onDeckIndexChange(Math.min(items.length - 1, deckIndex + 1))}
        >
          Next →
        </button>
      </div>
      <StillGalleryCompactCard it={deckItem} />
      <button type="button" className="drt-btn" onClick={() => onOpenFocus(deckItem)}>
        Use in launch pad →
      </button>
    </div>
  );
}

export function StillGalleryViewToggle({
  view,
  onChange,
}: {
  view: StillGalleryViewMode;
  onChange: (view: StillGalleryViewMode) => void;
}) {
  return (
    <div className="still-gallery__view-toggle still-tag-results__view-toggle">
      {(["focus", "strip", "grid", "deck"] as StillGalleryViewMode[]).map((mode) => (
        <button
          key={mode}
          type="button"
          className={view === mode ? "is-on" : ""}
          onClick={() => onChange(mode)}
        >
          {mode.charAt(0).toUpperCase() + mode.slice(1)}
        </button>
      ))}
    </div>
  );
}
