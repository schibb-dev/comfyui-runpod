import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AppetitePreviewBadge } from "./AppetitePreviewBadge";
import { PipelineMediaPlayer } from "./PipelineMediaPlayer";
import { useIsPhone } from "./viewport";
import { useAssetAppetite } from "./WorkProductAppetiteStrip";
import type { AppetiteFacet } from "./types";

/** Thumbs shown per Fresh strip page (matches server `_HOME_FRESH_PAGE_SIZE`). */
export const HOME_FRESH_PAGE_SIZE = 12;

const SWIPE_AXIS_LOCK_PX = 12;
const SWIPE_COMMIT_PX = 56;

export type HomePreviewLink = {
  href: string;
  label: string;
  disabled?: boolean;
  title?: string;
};

/** Normalized item for Home media strips + lightbox. */
export type HomePreviewItem = {
  id: string;
  label: string;
  subtitle?: string;
  thumbUrl?: string;
  mediaKind: "video" | "image";
  mediaUrl?: string;
  /** Seek video preview to this second (clips). */
  markInS?: number | null;
  /** Exclusive-ish out point — when set with markInS, playback loops in this window. */
  markOutS?: number | null;
  /** Media path for appetite badge (outputs / stills only — not clips until clip appetite exists). */
  appetiteRelpath?: string | null;
  defaultFacet?: AppetiteFacet;
  badge?: string | null;
  links: HomePreviewLink[];
};

function HomeAppetiteBadge({
  relpath,
  defaultFacet,
  size = "sm",
}: {
  relpath?: string | null;
  defaultFacet?: AppetiteFacet;
  size?: "default" | "sm";
}) {
  const { key } = useAssetAppetite(relpath, defaultFacet);
  if (!key) return null;
  return <AppetitePreviewBadge relpath={relpath} size={size} defaultFacet={defaultFacet} />;
}

function HomeThumbButton({
  item,
  onOpen,
}: {
  item: HomePreviewItem;
  onOpen: () => void;
}) {
  return (
    <button type="button" className="home-thumb" title={`${item.label} — preview`} onClick={onOpen}>
      {item.thumbUrl ? (
        <img
          className="home-thumb__img"
          src={item.thumbUrl}
          alt=""
          loading="lazy"
          onError={(e) => {
            const img = e.currentTarget;
            if (img.dataset.fallback === "1") return;
            img.dataset.fallback = "1";
            if (item.mediaKind === "image" && item.mediaUrl && img.src !== item.mediaUrl) {
              img.src = item.mediaUrl;
            } else {
              img.style.visibility = "hidden";
            }
          }}
        />
      ) : (
        <span className="home-thumb__fallback">{item.badge || item.label.slice(0, 8)}</span>
      )}
      {item.badge ? <span className="home-thumb__rating">{item.badge}</span> : null}
      {item.appetiteRelpath ? (
        <HomeAppetiteBadge relpath={item.appetiteRelpath} defaultFacet={item.defaultFacet} size="sm" />
      ) : null}
    </button>
  );
}

export function HomeMediaLightbox({
  items,
  index,
  onClose,
  onIndex,
}: {
  items: HomePreviewItem[];
  index: number;
  onClose: () => void;
  onIndex: (next: number) => void;
}) {
  const isPhone = useIsPhone();
  const n = items.length;
  const item = n > 0 ? items[((index % n) + n) % n] : null;
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const swipeStartRef = useRef<{ x: number; y: number; id: number } | null>(null);
  const swipeAxisRef = useRef<null | "x" | "y">(null);

  const markIn =
    item && typeof item.markInS === "number" && Number.isFinite(item.markInS)
      ? Math.max(0, item.markInS)
      : null;
  const markOut =
    item && typeof item.markOutS === "number" && Number.isFinite(item.markOutS) ? item.markOutS : null;
  const hasClipWindow =
    markIn != null && markOut != null && markOut > markIn + 0.05 && Boolean(item?.mediaUrl);

  const go = useCallback(
    (delta: number) => {
      if (n <= 0) return;
      onIndex((((index + delta) % n) + n) % n);
    },
    [index, n, onIndex],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        go(-1);
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        go(1);
      }
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [go, onClose]);

  // Phone: vertical swipe on the stage → prev / next (matches Queue / Workbench).
  useEffect(() => {
    if (!isPhone || n <= 1) return;
    const el = stageRef.current;
    if (!el) return;

    const reset = () => {
      swipeStartRef.current = null;
      swipeAxisRef.current = null;
    };

    const onDown = (e: PointerEvent) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      const target = e.target;
      if (
        target instanceof Element &&
        target.closest(
          "button, a, input, textarea, select, .video-trim-controls, .work-product-viewer__trim",
        )
      ) {
        return;
      }
      swipeStartRef.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
      swipeAxisRef.current = null;
    };

    const onMove = (e: PointerEvent) => {
      const start = swipeStartRef.current;
      if (!start || e.pointerId !== start.id) return;
      const dx = e.clientX - start.x;
      const dy = e.clientY - start.y;
      if (!swipeAxisRef.current) {
        if (Math.abs(dx) < SWIPE_AXIS_LOCK_PX && Math.abs(dy) < SWIPE_AXIS_LOCK_PX) return;
        swipeAxisRef.current = Math.abs(dy) > Math.abs(dx) * 1.15 ? "y" : "x";
        if (swipeAxisRef.current === "y") {
          try {
            el.setPointerCapture(e.pointerId);
          } catch {
            /* ignore */
          }
        }
      }
      if (swipeAxisRef.current === "y") e.preventDefault();
    };

    const onUp = (e: PointerEvent) => {
      const start = swipeStartRef.current;
      if (!start || e.pointerId !== start.id) return;
      const dx = e.clientX - start.x;
      const dy = e.clientY - start.y;
      const vertical = swipeAxisRef.current === "y" && Math.abs(dy) > Math.abs(dx);
      reset();
      if (!vertical) return;
      // Swipe up → next; swipe down → previous (same as Queue / Workbench).
      if (dy <= -SWIPE_COMMIT_PX) go(1);
      else if (dy >= SWIPE_COMMIT_PX) go(-1);
    };

    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove, { passive: false });
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", reset);
    return () => {
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", reset);
    };
  }, [go, isPhone, n, item?.id]);

  // Plain (non-clip) videos: autoplay once metadata is ready.
  useEffect(() => {
    if (hasClipWindow) return;
    const v = videoRef.current;
    if (!v || !item || item.mediaKind !== "video") return;
    const play = () => void v.play().catch(() => undefined);
    v.addEventListener("loadedmetadata", play);
    if (v.readyState >= 1) play();
    return () => v.removeEventListener("loadedmetadata", play);
  }, [item, hasClipWindow]);

  if (!item) return null;

  return createPortal(
    <div
      className={"home-fresh-lightbox" + (isPhone ? " home-fresh-lightbox--phone" : "")}
      role="dialog"
      aria-modal="true"
      aria-label={item.label}
      onClick={onClose}
    >
      <button
        type="button"
        className="home-fresh-lightbox__nav home-fresh-lightbox__nav--prev"
        aria-label="Previous"
        title="Previous (←)"
        disabled={n <= 1}
        onClick={(e) => {
          e.stopPropagation();
          go(-1);
        }}
      >
        <span aria-hidden="true">‹</span>
      </button>
      <div className="home-fresh-lightbox__panel" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          className="home-fresh-lightbox__close"
          onClick={onClose}
          aria-label="Close"
          title="Close (Esc)"
        >
          ×
        </button>
        <div className="home-fresh-lightbox__head">
          <div className="mono home-fresh-lightbox__title" title={item.subtitle || item.label}>
            {index + 1}/{n}
            {item.subtitle ? ` · ${item.subtitle}` : ""}
            {" · "}
            {item.label}
          </div>
          <div className="home-fresh-lightbox__controls">
            <button
              type="button"
              className="home-fresh-lightbox__icon-btn"
              aria-label="Previous"
              title={isPhone ? "Previous (swipe down)" : "Previous (←)"}
              disabled={n <= 1}
              onClick={() => go(-1)}
            >
              {isPhone ? "˄" : "‹"}
            </button>
            <button
              type="button"
              className="home-fresh-lightbox__icon-btn"
              aria-label="Next"
              title={isPhone ? "Next (swipe up)" : "Next (→)"}
              disabled={n <= 1}
              onClick={() => go(1)}
            >
              {isPhone ? "˅" : "›"}
            </button>
          </div>
        </div>
        {isPhone && n > 1 ? (
          <p className="factory-muted home-fresh-lightbox__swipe-hint">Swipe ↑↓ for next · buttons also work</p>
        ) : null}
        <div
          className="home-fresh-lightbox__stage"
          ref={stageRef}
          style={isPhone && n > 1 ? { touchAction: "pan-y" } : undefined}
        >
          {hasClipWindow && item.mediaUrl ? (
            <PipelineMediaPlayer
              className="home-fresh-lightbox__player"
              videoUrl={item.mediaUrl}
              thumbUrl={item.thumbUrl}
              mediaKey={item.id}
              alt={item.label}
              markIn={markIn}
              markOut={markOut}
              appetiteRelpath={item.appetiteRelpath}
              autoplay
              loop
            />
          ) : item.mediaKind === "video" && item.mediaUrl ? (
            <div className="home-fresh-lightbox__appetite-host">
              <video
                ref={videoRef}
                key={item.mediaUrl}
                className="home-fresh-lightbox__media"
                src={item.mediaUrl}
                controls
                playsInline
                muted
                loop
                poster={item.thumbUrl || undefined}
              />
              <HomeAppetiteBadge
                relpath={item.appetiteRelpath}
                defaultFacet={item.defaultFacet}
                size="default"
              />
            </div>
          ) : item.mediaKind === "image" && item.mediaUrl ? (
            <div className="home-fresh-lightbox__appetite-host">
              <img className="home-fresh-lightbox__media" src={item.mediaUrl} alt={item.label} />
              <HomeAppetiteBadge
                relpath={item.appetiteRelpath}
                defaultFacet={item.defaultFacet}
                size="default"
              />
            </div>
          ) : (
            <p className="factory-muted">No preview media</p>
          )}
        </div>
        <div className="home-fresh-lightbox__links">
          {item.links.map((link) =>
            link.disabled || !link.href ? (
              <span
                key={link.label}
                className="home-fresh-lightbox__link-disabled"
                title={link.title || undefined}
              >
                {link.label}
              </span>
            ) : (
              <a key={link.label} className="home-cta" href={link.href} title={link.title || undefined}>
                {link.label}
              </a>
            ),
          )}
        </div>
      </div>
      <button
        type="button"
        className="home-fresh-lightbox__nav home-fresh-lightbox__nav--next"
        aria-label="Next"
        title="Next (→)"
        disabled={n <= 1}
        onClick={(e) => {
          e.stopPropagation();
          go(1);
        }}
      >
        <span aria-hidden="true">›</span>
      </button>
    </div>,
    document.body,
  );
}

export function HomeMediaStrip({
  items,
  emptyLabel,
  previewIndex,
  onPreviewIndex,
  pageSize = HOME_FRESH_PAGE_SIZE,
}: {
  items: HomePreviewItem[];
  emptyLabel: string;
  previewIndex: number | null;
  onPreviewIndex: (index: number | null) => void;
  /** Items per page; pager hidden when everything fits on one page. */
  pageSize?: number;
}) {
  const ps = Math.max(1, Math.trunc(pageSize) || HOME_FRESH_PAGE_SIZE);
  const pageCount = Math.max(1, Math.ceil(items.length / ps));
  const [page, setPage] = useState(0);

  const itemsKey = useMemo(() => items.map((it) => it.id).join("|"), [items]);
  useEffect(() => {
    setPage(0);
  }, [itemsKey]);

  const safePage = Math.min(page, pageCount - 1);
  const pageItems = items.slice(safePage * ps, safePage * ps + ps);
  const showPager = items.length > ps;

  // Keep the strip page aligned when the lightbox walks across all loaded items.
  useEffect(() => {
    if (previewIndex == null || items.length <= 0) return;
    const want = Math.floor(previewIndex / ps);
    if (want !== safePage && want >= 0 && want < pageCount) setPage(want);
  }, [previewIndex, ps, pageCount, safePage, items.length]);

  if (!items.length) {
    return <p className="factory-muted">{emptyLabel}</p>;
  }
  return (
    <>
      <div className="home-thumb-strip">
        {pageItems.map((it, i) => (
          <HomeThumbButton
            key={it.id}
            item={it}
            onOpen={() => onPreviewIndex(safePage * ps + i)}
          />
        ))}
      </div>
      {showPager ? (
        <div className="pager home-thumb-pager" role="navigation" aria-label="Fresh strip pages">
          <button
            type="button"
            className="pager-nav"
            aria-label="Previous page"
            disabled={safePage <= 0}
            onClick={() => setPage(safePage - 1)}
          >
            ‹
          </button>
          <span className="pager-text">
            {safePage + 1} / {pageCount}
          </span>
          <button
            type="button"
            className="pager-nav"
            aria-label="Next page"
            disabled={safePage >= pageCount - 1}
            onClick={() => setPage(safePage + 1)}
          >
            ›
          </button>
          <span className="pager-label">{items.length} items</span>
        </div>
      ) : null}
      {previewIndex != null ? (
        <HomeMediaLightbox
          items={items}
          index={previewIndex}
          onClose={() => onPreviewIndex(null)}
          onIndex={onPreviewIndex}
        />
      ) : null}
    </>
  );
}
