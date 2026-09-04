import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { setAssetAppetite } from "./api";
import { AppetiteBar } from "./AppetiteBar";
import { patchCachedAppetite, revalidateAssetRatings } from "./assetRatingsCache";
import { APPETITE_ROW_GLYPH, appetiteRowTitle } from "./discoveryRatingsRollup";
import type { Appetite, AppetiteFacet } from "./types";
import { useAssetAppetite } from "./WorkProductAppetiteStrip";

const HOVER_CLOSE_MS = 220;
const UNSET_GLYPH = "?";

type PopoverPos = { top: number; left: number; placeAbove: boolean };

/** Upper-right appetite glyph on a media preview. Hidden when there is no media path. */
export function AppetitePreviewBadge({
  relpath,
  size = "default",
  className,
  jobKey,
  familySlug,
  defaultFacet,
}: {
  relpath?: string | null;
  size?: "default" | "sm";
  className?: string;
  jobKey?: string | null;
  familySlug?: string | null;
  defaultFacet?: AppetiteFacet;
}) {
  const { key, appetite, facet } = useAssetAppetite(relpath, defaultFacet);
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [pos, setPos] = useState<PopoverPos | null>(null);
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  const closeTimer = useRef<number | null>(null);

  const cancelClose = useCallback(() => {
    if (closeTimer.current != null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);

  const scheduleClose = useCallback(() => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => {
      if (pinned) return;
      setOpen(false);
    }, HOVER_CLOSE_MS);
  }, [cancelClose, pinned]);

  const openNow = useCallback(() => {
    cancelClose();
    setOpen(true);
  }, [cancelClose]);

  useEffect(() => () => cancelClose(), [cancelClose]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setPinned(false);
        setOpen(false);
      }
    };
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (wrapRef.current?.contains(t) || popRef.current?.contains(t)) return;
      setPinned(false);
      setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !wrapRef.current) {
      setPos(null);
      return;
    }
    const place = () => {
      const r = wrapRef.current?.getBoundingClientRect();
      if (!r) return;
      const width = popRef.current?.offsetWidth || 128;
      const height = popRef.current?.offsetHeight || 40;
      const gap = 6;
      const placeAbove = r.bottom + gap + height > window.innerHeight - 8 && r.top > height + gap;
      const top = placeAbove ? r.top - height - gap : r.bottom + gap;
      const left = Math.min(Math.max(8, r.right - width), window.innerWidth - width - 8);
      setPos({ top, left, placeAbove });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, appetite, facet, msg, size]);

  const onSet = useCallback(
    async (state: Appetite, nextFacet: AppetiteFacet) => {
      if (!key || busy) return;
      patchCachedAppetite(key, state, nextFacet);
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
        void revalidateAssetRatings(key);
      } catch (e) {
        setMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [key, busy, jobKey, familySlug],
  );

  if (!key) return null;
  const title = appetite ? appetiteRowTitle(appetite, facet) : "Appetite unset — hover to set";
  const popover = open
    ? createPortal(
        <div
          ref={popRef}
          className={"appetite-preview-popover" + (pos?.placeAbove ? " appetite-preview-popover--above" : "")}
          role="dialog"
          aria-label="Set appetite"
          style={pos ? { top: pos.top, left: pos.left } : { visibility: "hidden", top: 0, left: 0 }}
          onMouseEnter={openNow}
          onMouseLeave={scheduleClose}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
        >
          <AppetiteBar
            appetite={appetite}
            facet="both"
            busy={busy}
            iconsOnly
            onSet={(state) => void onSet(state, "both")}
          />
          {msg ? <p className="appetite-preview-popover__msg">{msg}</p> : null}
        </div>,
        document.body,
      )
    : null;

  return (
    <span
      ref={wrapRef}
      className={
        "appetite-preview-badge-wrap" +
        (size === "sm" ? " appetite-preview-badge-wrap--sm" : "") +
        (open ? " appetite-preview-badge-wrap--open" : "")
      }
      onMouseEnter={openNow}
      onMouseLeave={scheduleClose}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setPinned((p) => {
          const next = !p;
          setOpen(true);
          return next;
        });
      }}
    >
      <span
        className={
          "appetite-preview-badge" +
          (appetite ? ` appetite-preview-badge--${appetite}` : " appetite-preview-badge--unset") +
          (size === "sm" ? " appetite-preview-badge--sm" : "") +
          (className ? ` ${className}` : "")
        }
        title={open ? undefined : title}
        aria-label={title}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        {appetite ? APPETITE_ROW_GLYPH[appetite] : UNSET_GLYPH}
      </span>
      {popover}
    </span>
  );
}

/** Positions an appetite badge over a preview frame. */
export function AppetitePreviewFrame({
  relpath,
  size = "default",
  className,
  jobKey,
  familySlug,
  defaultFacet,
  children,
}: {
  relpath?: string | null;
  size?: "default" | "sm";
  className?: string;
  jobKey?: string | null;
  familySlug?: string | null;
  defaultFacet?: AppetiteFacet;
  children: React.ReactNode;
}) {
  return (
    <div className={["appetite-preview-host", className].filter(Boolean).join(" ")}>
      {children}
      <AppetitePreviewBadge
        relpath={relpath}
        size={size}
        jobKey={jobKey}
        familySlug={familySlug}
        defaultFacet={defaultFacet}
      />
    </div>
  );
}
