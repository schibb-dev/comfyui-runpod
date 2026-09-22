import React, { useEffect, useRef, useState } from "react";
import { ComfyLivePreview } from "./ComfyLivePreview";
import { PipelineMediaPlayer } from "./PipelineMediaPlayer";
import {
  queueFocusDetailMode,
  queueSwipeCommitIndex,
  queueSwipePageIndex,
} from "./queueMonitorSections";
import { scrollFocusItemIntoView } from "./stillTagScrollFocus";
import type { WorkProductItem } from "./types";
import {
  workProductDisplayTitle,
  workProductKindIs,
  workProductPreviewUrl,
} from "./workProductKind";
import {
  workProductNavSection,
  type WorkProductNavSectionId,
} from "./workProductListSort";

const SWIPE_AXIS_LOCK_PX = 12;
const SWIPE_COMMIT_PX = 56;

export type WorkbenchSwipeEntry = {
  key: string;
  section: WorkProductNavSectionId;
  item: WorkProductItem;
};

export function workbenchJobSwipeKey(item: Pick<WorkProductItem, "job_key" | "prompt_id" | "output_relpath">): string {
  const job = String(item.job_key || "").trim();
  if (job) return job;
  const prompt = String(item.prompt_id || "").trim();
  if (prompt) return `prompt:${prompt}`;
  const out = String(item.output_relpath || "").trim();
  return out ? `out:${out}` : "";
}

export function workbenchSwipeSectionLabel(section: WorkProductNavSectionId | null | undefined): string {
  if (section === "live") return "Comfy Queue";
  if (section === "pending") return "Pending";
  if (section === "error") return "Errors";
  if (section === "done") return "Completed";
  return "Jobs";
}

/** Keep vertical swipe inside the section the user opened from. */
export function buildWorkbenchSwipeEntries(
  items: WorkProductItem[],
  section?: WorkProductNavSectionId | null,
): WorkbenchSwipeEntry[] {
  const out: WorkbenchSwipeEntry[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    const key = workbenchJobSwipeKey(item);
    if (!key || seen.has(key)) continue;
    const sec = workProductNavSection(item.status);
    if (section && sec !== section) continue;
    seen.add(key);
    out.push({ key, section: sec, item });
  }
  return out;
}

function statusVisual(status?: string | null): string {
  const s = String(status || "").toLowerCase();
  if (s === "running") return "running";
  if (s === "queued" || s === "submitted") return "queued";
  if (s === "editing") return "editing";
  if (s === "error" || s === "failed") return "error";
  if (s === "interrupted") return "interrupted";
  if (s === "complete" || s === "deposited") return "ok";
  if (s === "abandoned" || s === "unknown") return "muted";
  return "pending";
}

function focusTitle(item: WorkProductItem): string {
  if (workProductKindIs(item, "still_tag")) return workProductDisplayTitle(item);
  return String(item.family_slug || "").trim() || workProductDisplayTitle(item) || "job";
}

function isRunningLive(item: WorkProductItem): boolean {
  const status = String(item.status || "").toLowerCase();
  if (status !== "running") return false;
  if (item.output_url && !item.live_from_comfy) return false;
  return Boolean(item.prompt_id) || Boolean(item.live_from_comfy);
}

function sourceThumb(item: WorkProductItem): string | null {
  const src = item.bindings?.source_video;
  return src?.thumb_url || workProductPreviewUrl(item) || item.output_thumb_url || null;
}

/** Phone: horizontal swipe opens sheets; vertical pan still browses jobs. */
function WorkbenchSwipeActionsLayer({
  enabled,
  onSwipeRight,
  onSwipeLeft,
  children,
}: {
  enabled: boolean;
  onSwipeRight?: () => void;
  onSwipeLeft?: () => void;
  children: React.ReactNode;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const startRef = useRef<{ x: number; y: number; id: number } | null>(null);
  const axisRef = useRef<null | "x" | "y">(null);
  const [tx, setTx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const rightRef = useRef(onSwipeRight);
  const leftRef = useRef(onSwipeLeft);
  rightRef.current = onSwipeRight;
  leftRef.current = onSwipeLeft;

  useEffect(() => {
    if (!enabled) return;
    const el = rootRef.current;
    if (!el) return;

    const reset = () => {
      startRef.current = null;
      axisRef.current = null;
      setTx(0);
      setDragging(false);
    };

    const onDown = (e: PointerEvent) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      const target = e.target;
      if (target instanceof Element && target.closest("button, a, input, textarea, select")) return;
      startRef.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
      axisRef.current = null;
    };

    const onMove = (e: PointerEvent) => {
      const start = startRef.current;
      if (!start || e.pointerId !== start.id) return;
      const dx = e.clientX - start.x;
      const dy = e.clientY - start.y;
      if (!axisRef.current) {
        if (Math.abs(dx) < SWIPE_AXIS_LOCK_PX && Math.abs(dy) < SWIPE_AXIS_LOCK_PX) return;
        axisRef.current = Math.abs(dx) > Math.abs(dy) * 1.15 ? "x" : "y";
        if (axisRef.current === "x") {
          try {
            el.setPointerCapture(e.pointerId);
          } catch {
            /* ignore */
          }
          setDragging(true);
        }
      }
      if (axisRef.current !== "x") return;
      e.preventDefault();
      const min = leftRef.current ? -128 : 0;
      const max = rightRef.current ? 128 : 0;
      setTx(Math.min(max, Math.max(min, dx)));
    };

    const onUp = (e: PointerEvent) => {
      const start = startRef.current;
      if (!start || e.pointerId !== start.id) return;
      const dx = e.clientX - start.x;
      const dy = e.clientY - start.y;
      const horizontal = axisRef.current === "x" && Math.abs(dx) > Math.abs(dy);
      reset();
      if (!horizontal) return;
      if (dx >= SWIPE_COMMIT_PX) rightRef.current?.();
      else if (dx <= -SWIPE_COMMIT_PX) leftRef.current?.();
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
  }, [enabled]);

  return (
    <div className="work-products__swipe-layer" ref={rootRef}>
      {enabled && onSwipeRight ? (
        <div
          className="work-products__swipe-action work-products__swipe-action--right"
          style={{ opacity: tx > 0 ? Math.min(1, tx / SWIPE_COMMIT_PX) : 0 }}
          aria-hidden="true"
        >
          Actions
        </div>
      ) : null}
      {enabled && onSwipeLeft ? (
        <div
          className="work-products__swipe-action work-products__swipe-action--left"
          style={{ opacity: tx < 0 ? Math.min(1, -tx / SWIPE_COMMIT_PX) : 0 }}
          aria-hidden="true"
        >
          Details
        </div>
      ) : null}
      <div
        className={"work-products__swipe-card" + (dragging ? " is-dragging" : "")}
        style={tx ? { transform: `translateX(${tx}px)` } : undefined}
      >
        {children}
      </div>
    </div>
  );
}

export function WorkbenchFocusStage({
  title,
  statusLabel,
  statusVisual,
  metrics,
  children,
}: {
  title: string;
  statusLabel: string;
  statusVisual: string;
  metrics?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="work-products__focus-stage">
      <div className="work-products__focus-media">{children}</div>
      <div className="work-products__focus-hud">
        <span className={`work-products__compact-status work-products__compact-status--${statusVisual}`}>
          {statusLabel}
        </span>
        <strong className="work-products__focus-hud-title">{title}</strong>
        {metrics}
      </div>
    </div>
  );
}

export function WorkbenchFocusMedia({
  item,
  decode,
  focused,
  autoplay,
  loop,
}: {
  item: WorkProductItem;
  decode: boolean;
  focused?: boolean;
  autoplay?: boolean;
  loop?: boolean;
}) {
  const title = focusTitle(item);
  const statusLabel = String(item.status || "pending");
  const visual = statusVisual(item.status);
  const thumb = sourceThumb(item);
  const play = Boolean(autoplay && focused && decode);

  if (!decode) {
    return (
      <WorkbenchFocusStage title={title} statusLabel={statusLabel} statusVisual={visual}>
        {thumb ? (
          <img className="work-products__focus-img" src={thumb} alt="" />
        ) : (
          <div className="work-products__focus-empty">No preview</div>
        )}
      </WorkbenchFocusStage>
    );
  }

  if (isRunningLive(item) && item.prompt_id) {
    return (
      <WorkbenchFocusStage title={title} statusLabel={statusLabel} statusVisual={visual}>
        <ComfyLivePreview
          promptId={String(item.prompt_id)}
          submittedAt={item.submitted_at || item.created_at}
          className="work-products__focus-live"
          showMetrics={false}
        />
      </WorkbenchFocusStage>
    );
  }

  const videoUrl = item.output_url || null;
  const applied = item.applied_vhs;
  const vhsWindow =
    applied && (applied.skip_first_frames != null || applied.frame_load_cap != null)
      ? {
          skip_first_frames: Math.max(0, Math.floor(Number(applied.skip_first_frames ?? 0) || 0)),
          frame_load_cap: Math.max(0, Math.floor(Number(applied.frame_load_cap ?? 0) || 0)),
        }
      : null;

  return (
    <WorkbenchFocusStage title={title} statusLabel={statusLabel} statusVisual={visual}>
      {videoUrl || thumb ? (
        <PipelineMediaPlayer
          videoUrl={videoUrl}
          thumbUrl={thumb || item.output_thumb_url}
          mediaKey={`workbench-focus:${workbenchJobSwipeKey(item)}`}
          alt={title}
          readOnly
          autoplay={play}
          loop={loop}
          showControls={false}
          vhsWindow={vhsWindow}
          fpsHint={item.media_meta?.fps}
          appetiteRelpath={item.output_relpath}
        />
      ) : (
        <div className="work-products__focus-empty">No preview</div>
      )}
    </WorkbenchFocusStage>
  );
}

export function WorkbenchFocusSwipeView({
  entries,
  focusedKey,
  onFocus,
  onSwipeLeft,
  onSwipeRight,
  renderEntry,
}: {
  entries: WorkbenchSwipeEntry[];
  focusedKey: string | null;
  onFocus: (key: string) => void;
  onSwipeLeft?: (entry: WorkbenchSwipeEntry) => void;
  onSwipeRight?: (entry: WorkbenchSwipeEntry) => void;
  renderEntry: (entry: WorkbenchSwipeEntry, mode: "full" | "off", focused: boolean) => React.ReactNode;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sectionRefs = useRef<Map<string, HTMLElement>>(new Map());
  const skipScrollSyncRef = useRef(false);
  const focusIndex = entries.findIndex((e) => e.key === focusedKey);
  const entriesRef = useRef(entries);
  const focusedKeyRef = useRef(focusedKey);
  const onFocusRef = useRef(onFocus);
  entriesRef.current = entries;
  focusedKeyRef.current = focusedKey;
  onFocusRef.current = onFocus;

  useEffect(() => {
    if (!focusedKey) return;
    if (skipScrollSyncRef.current) {
      skipScrollSyncRef.current = false;
      return;
    }
    const el = sectionRefs.current.get(focusedKey);
    scrollFocusItemIntoView(scrollRef.current, el || null, "y", { align: "start" });
  }, [focusedKey]);

  useEffect(() => {
    const root = scrollRef.current;
    if (!root) return;
    const gesture = {
      x: 0,
      y: 0,
      top: 0,
      id: -1,
      axis: null as null | "x" | "y",
      active: false,
    };
    let settleTimer = 0;

    const pageHeight = () => root.clientHeight || 1;

    const pageTo = (index: number) => {
      const list = entriesRef.current;
      if (!list.length) return;
      const clamped = Math.max(0, Math.min(list.length - 1, index));
      const next = list[clamped];
      if (!next) return;
      const el = sectionRefs.current.get(next.key);
      const top = el ? el.offsetTop : clamped * pageHeight();
      if (Math.abs(root.scrollTop - top) > 2) {
        root.scrollTo({ top, behavior: "auto" });
      }
      if (next.key !== focusedKeyRef.current) {
        skipScrollSyncRef.current = true;
        onFocusRef.current(next.key);
      }
    };

    const settleFromScroll = () => {
      pageTo(queueSwipePageIndex(root.scrollTop, pageHeight(), entriesRef.current.length));
    };

    const onDown = (e: PointerEvent) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      const target = e.target;
      if (target instanceof Element && target.closest("button, a, input, textarea, select")) return;
      window.clearTimeout(settleTimer);
      gesture.x = e.clientX;
      gesture.y = e.clientY;
      gesture.top = root.scrollTop;
      gesture.id = e.pointerId;
      gesture.axis = null;
      gesture.active = true;
    };

    const onMove = (e: PointerEvent) => {
      if (!gesture.active || e.pointerId !== gesture.id) return;
      if (gesture.axis) return;
      const dx = e.clientX - gesture.x;
      const dy = e.clientY - gesture.y;
      if (Math.abs(dx) < SWIPE_AXIS_LOCK_PX && Math.abs(dy) < SWIPE_AXIS_LOCK_PX) return;
      gesture.axis = Math.abs(dx) > Math.abs(dy) * 1.15 ? "x" : "y";
    };

    const finish = (e: PointerEvent) => {
      if (!gesture.active || e.pointerId !== gesture.id) return;
      const dy = e.clientY - gesture.y;
      const vertical =
        gesture.axis === "y" || (gesture.axis == null && Math.abs(dy) >= Math.abs(e.clientX - gesture.x));
      gesture.active = false;
      gesture.axis = null;
      const list = entriesRef.current;
      const h = pageHeight();
      const fromScroll = queueSwipePageIndex(root.scrollTop, h, list.length);
      const fromStart = queueSwipePageIndex(gesture.top, h, list.length);
      if (!vertical) return;
      if (fromScroll !== fromStart) return;
      pageTo(queueSwipeCommitIndex(fromStart, list.length, dy, h));
    };

    const onScroll = () => {
      if (gesture.active) return;
      window.clearTimeout(settleTimer);
      settleTimer = window.setTimeout(settleFromScroll, 90);
    };

    root.addEventListener("pointerdown", onDown);
    root.addEventListener("pointermove", onMove, { passive: true });
    root.addEventListener("pointerup", finish);
    root.addEventListener("pointercancel", finish);
    root.addEventListener("scroll", onScroll, { passive: true });
    root.addEventListener("scrollend", settleFromScroll);
    return () => {
      window.clearTimeout(settleTimer);
      root.removeEventListener("pointerdown", onDown);
      root.removeEventListener("pointermove", onMove);
      root.removeEventListener("pointerup", finish);
      root.removeEventListener("pointercancel", finish);
      root.removeEventListener("scroll", onScroll);
      root.removeEventListener("scrollend", settleFromScroll);
    };
  }, []);

  return (
    <div className="work-products__focus">
      <p className="work-products__focus-hint factory-muted">
        ← Details · Actions → · {focusIndex >= 0 ? `${focusIndex + 1} / ${entries.length}` : entries.length}
      </p>
      <div className="work-products__focus-scroll" ref={scrollRef} aria-label="Swipe workbench jobs">
        {entries.map((entry, i) => {
          const focused = entry.key === focusedKey;
          const mode = queueFocusDetailMode(i, focusIndex);
          return (
            <section
              key={entry.key}
              ref={(el) => {
                if (el) sectionRefs.current.set(entry.key, el);
                else sectionRefs.current.delete(entry.key);
              }}
              className={`work-products__focus-section${focused ? " is-focused" : ""}`}
              aria-current={focused ? "true" : undefined}
            >
              <WorkbenchSwipeActionsLayer
                enabled={Boolean((onSwipeLeft || onSwipeRight) && focused)}
                onSwipeLeft={onSwipeLeft ? () => onSwipeLeft(entry) : undefined}
                onSwipeRight={onSwipeRight ? () => onSwipeRight(entry) : undefined}
              >
                {renderEntry(entry, mode, focused)}
              </WorkbenchSwipeActionsLayer>
            </section>
          );
        })}
      </div>
    </div>
  );
}
