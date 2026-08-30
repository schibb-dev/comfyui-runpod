import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  beginShapeFactoryEdit,
  composeSubmitAdvance,
  fetchShapeFactoryJobEdit,
  fetchShapeFactorySubmitAttempts,
  finishShapeFactoryEdit,
  listShapeFactoryClipsLibrary,
  mintIdentityStill,
  queueShapeFactoryCombo,
  updatePendingShapeFactoryBinding,
  updatePendingShapeFactoryTrim,
  type IdentityStillCandidate,
  type IdentityStillMintTarget,
  type ShapeFactoryClip,
  type ShapeFactoryJobEditSnapshot,
} from "./api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RecentSubmitsPanel, SubmitQueueErrorPanel } from "./SubmitAttemptError";
import { queryKeys } from "./queryKeys";
import { ClipBookmarksRail } from "./ClipBookmarksRail";
import {
  clipsLibraryHref,
  discoveryLibraryHref,
  hasSubmitIntent,
  parseSubmitDeepLink,
  queueHref,
  submitOriginHref,
  workbenchHref,
  type SubmitDeepLink,
} from "./discoveryDeepLink";
import { PageHeader } from "./PageHeader";
import {
  invalidateIdentityStill,
  loadFamiliesBootstrap,
  loadIdentityStillCandidates,
  peekFamiliesBootstrap,
  peekIdentityStill,
  type FamiliesBootstrap,
} from "./shapeFactorySessionCache";
import {
  isExtendFamilyOption,
  isI2VFamilyOption,
  isStillMediaPath,
  pickDefaultExtendFamily,
  pickDefaultI2VFamily,
} from "./submitFamily";
import type { ShapeFactoryMapQueueOverrides, WorkProductFamilyOption } from "./types";
import { VideoTrimControls, type VideoTrimPlaybackMode } from "./VideoTrimControls";
import { useTrimPlaybackEnforcement } from "./useTrimPlayback";
import { marksToVhsWindow } from "./workProductTrim";

type RowLayout = "split" | "stacked";

const LAYOUT_KEY = "submit-composer-row-layout";

function loadLayout(): RowLayout {
  try {
    const v = localStorage.getItem(LAYOUT_KEY);
    if (v === "stacked" || v === "split") return v;
  } catch {
    /* ignore */
  }
  return "split";
}

function persistLayout(layout: RowLayout) {
  try {
    localStorage.setItem(LAYOUT_KEY, layout);
  } catch {
    /* ignore */
  }
}

function filesUrl(relpath: string): string {
  return "/files/" + encodeURIComponent(relpath.replace(/\\/g, "/"));
}

function thumbUrlForMedia(relpath: string): string | null {
  const norm = relpath.replace(/\\/g, "/");
  if (/\.(mp4|webm|mov|mkv)$/i.test(norm)) {
    return filesUrl(norm.replace(/\.(mp4|webm|mov|mkv)$/i, ".png"));
  }
  if (/\.(png|jpe?g|webp|gif)$/i.test(norm)) return filesUrl(norm);
  return null;
}

function formatTc(s: number): string {
  if (!Number.isFinite(s) || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function stepToRouteFlags(step: string | null): { extend: boolean; vary: boolean; derive: boolean } {
  const s = String(step || "").trim().toLowerCase();
  if (s === "advance.vary" || s === "vary") return { extend: false, vary: true, derive: false };
  if (s === "advance.derive" || s === "derive") return { extend: false, vary: false, derive: true };
  return { extend: true, vary: false, derive: false };
}

function basenamePath(path: string): string {
  const norm = String(path || "").replace(/\\/g, "/");
  const parts = norm.split("/").filter(Boolean);
  return parts[parts.length - 1] || norm || "—";
}

type IdentityStillPeek = {
  src: string;
  left: number;
  top: number;
  place: "above" | "below";
};

function IdentityStillThumbButton({
  candidate,
  selected,
  disabled,
  onSelect,
}: {
  candidate: IdentityStillCandidate;
  selected: boolean;
  disabled?: boolean;
  onSelect: () => void;
}) {
  const [peek, setPeek] = useState<IdentityStillPeek | null>(null);
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);
  const thumb = candidate.thumb_url || candidate.url;
  const full = candidate.url || candidate.thumb_url || null;

  const showPeek = (el: HTMLElement) => {
    if (!full) return;
    const r = el.getBoundingClientRect();
    const place: "above" | "below" = r.top < 360 ? "below" : "above";
    const left = Math.min(Math.max(r.left + r.width / 2, 168), window.innerWidth - 168);
    setDims(null);
    setPeek({
      src: full,
      left,
      top: place === "above" ? r.top - 8 : r.bottom + 8,
      place,
    });
  };

  const hidePeek = () => {
    setPeek(null);
    setDims(null);
  };

  return (
    <>
      <button
        type="button"
        role="option"
        aria-selected={selected}
        className={`work-product-identity-still__thumb${selected ? " is-selected" : ""}`}
        disabled={disabled}
        title={candidate.label || candidate.evidence || "still"}
        onClick={onSelect}
        onMouseEnter={(e) => showPeek(e.currentTarget)}
        onMouseLeave={hidePeek}
        onFocus={(e) => showPeek(e.currentTarget)}
        onBlur={hidePeek}
      >
        {thumb ? <img src={thumb} alt="" loading="lazy" /> : <span>{(candidate.evidence || "?").slice(0, 3)}</span>}
        <span className="work-product-identity-still__ev">{candidate.evidence || ""}</span>
      </button>
      {peek
        ? createPortal(
            <div
              className={`work-product-identity-still__popover work-product-identity-still__popover--${peek.place}`}
              style={{ left: peek.left, top: peek.top }}
              role="presentation"
            >
              <img
                src={peek.src}
                alt=""
                ref={(img) => {
                  if (!img || !img.complete) return;
                  const w = img.naturalWidth;
                  const h = img.naturalHeight;
                  if (w > 0 && h > 0) setDims((prev) => (prev?.w === w && prev?.h === h ? prev : { w, h }));
                }}
                onLoad={(e) => {
                  const img = e.currentTarget;
                  const w = img.naturalWidth;
                  const h = img.naturalHeight;
                  if (w > 0 && h > 0) setDims({ w, h });
                }}
              />
              <div className="work-product-identity-still__popover-meta">
                {dims ? `${dims.w}×${dims.h}` : "…"}
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

function familyShapeId(families: WorkProductFamilyOption[], slug: string): string | null {
  const hit = families.find((f) => f.slug === slug);
  const sid = String(hit?.shape_id || "").trim();
  return sid || null;
}

function readStickyIdentity(): string {
  try {
    return String(window.sessionStorage.getItem("submit_sticky_identity") || "").trim();
  } catch {
    return "";
  }
}

function clearStickyIdentity(): void {
  try {
    window.sessionStorage.removeItem("submit_sticky_identity");
  } catch {
    /* ignore */
  }
}

/** Edit an existing pending/queued factory job in place (not advance). */
function SubmitEditJobApp({
  editJob,
  origin,
  presentation = "page",
  onClose,
}: {
  editJob: string;
  origin: string | null;
  presentation?: "page" | "modal";
  onClose?: () => void;
}) {
  const isModal = presentation === "modal";
  const [busy, setBusy] = useState(false);
  const [bootError, setBootError] = useState<string | null>(null);
  const [snap, setSnap] = useState<ShapeFactoryJobEditSnapshot | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [markIn, setMarkIn] = useState<number | null>(null);
  const [markOut, setMarkOut] = useState<number | null>(null);
  const [clipId, setClipId] = useState("");
  const [activeClip, setActiveClip] = useState<ShapeFactoryClip | null>(null);
  const [videoDuration, setVideoDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [trimMode, setTrimMode] = useState<VideoTrimPlaybackMode>("repeat");
  const [finished, setFinished] = useState(false);
  const [sourcePathDraft, setSourcePathDraft] = useState("");
  const [promptProfileDraft, setPromptProfileDraft] = useState("");
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const releasedRef = useRef(false);

  const mediaRelpath = String(snap?.source?.relpath || "").trim();
  const playUrl = mediaRelpath ? filesUrl(mediaRelpath) : snap?.source?.url || null;
  const posterUrl = mediaRelpath ? thumbUrlForMedia(mediaRelpath) : snap?.source?.thumb_url || null;
  const isVideo = Boolean(playUrl && /\.(mp4|webm|mov|mkv)(\?|$)/i.test(playUrl));
  const duration =
    videoDuration > 0 ? videoDuration : Math.max(markOut ?? 0, markIn ?? 0, 0);
  const fps = 18;

  const originBack = useMemo(
    () =>
      submitOriginHref(origin, {
        mediaRelpath: mediaRelpath || null,
        editJob,
        fromJob: editJob,
      }) || { href: workbenchHref({ jobKey: editJob }), label: "Back to Workbench" },
    [editJob, mediaRelpath, origin],
  );

  const releaseEdit = useCallback(
    async (action: "later" | "cancel" | "now", opts?: { front?: boolean; navigate?: boolean }) => {
      if (releasedRef.current && action !== "now") return;
      setBusy(true);
      setMsg(null);
      try {
        const res = await finishShapeFactoryEdit({
          job_key: editJob,
          action,
          front: opts?.front,
          actor: "operator",
          reason: `finish_edit:${action}`,
          source_surface: "submit_edit",
        });
        releasedRef.current = true;
        setFinished(true);
        setMsg(
          action === "now"
            ? `Queued · ${res.prompt_id || res.job_key || editJob}`
            : action === "later"
              ? "Saved for later (pending)"
              : "Edit cancelled (pending)",
        );
        if (opts?.navigate !== false && !isModal) {
          window.setTimeout(() => {
            window.location.href = originBack.href;
          }, action === "now" ? 600 : 200);
        } else if (isModal && onClose && opts?.navigate !== false) {
          window.setTimeout(() => onClose(), action === "now" ? 600 : 200);
        }
      } catch (e) {
        setMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [editJob, isModal, onClose, originBack.href],
  );

  const refreshSnapshot = useCallback(async () => {
    const doc = await fetchShapeFactoryJobEdit({ jobKey: editJob });
    setSnap(doc);
    return doc;
  }, [editJob]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy(true);
      setBootError(null);
      try {
        await beginShapeFactoryEdit({
          job_key: editJob,
          actor: "operator",
          reason: "begin_edit",
          source_surface: "submit_edit",
        });
        const doc = await refreshSnapshot();
        if (cancelled) return;
        const win = doc.vhs_window || {};
        const mi = win.mark_in != null ? Number(win.mark_in) : null;
        const mo = win.mark_out != null ? Number(win.mark_out) : null;
        setMarkIn(Number.isFinite(mi as number) ? (mi as number) : null);
        setMarkOut(Number.isFinite(mo as number) ? (mo as number) : null);
        const cid = String(doc.source_clip_id || win.clip_id || "").trim();
        if (cid) setClipId(cid);
      } catch (e) {
        if (!cancelled) setBootError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [editJob, refreshSnapshot]);

  useEffect(() => {
    const sourceSlot = String(snap?.source?.slot || "").trim();
    const sourceBinding =
      sourceSlot && snap?.bindings && typeof snap.bindings === "object"
        ? (snap.bindings[sourceSlot] as { path?: string; relpath?: string } | undefined)
        : undefined;
    const sourceSeed = String(
      sourceBinding?.relpath || sourceBinding?.path || snap?.source?.relpath || snap?.source?.path || "",
    ).trim();
    setSourcePathDraft(sourceSeed);
    const promptSeed = String(
      (snap?.bindings?.prompt_profile as { relpath?: string; path?: string } | undefined)?.relpath ||
        (snap?.bindings?.prompt_profile as { relpath?: string; path?: string } | undefined)?.path ||
        "",
    ).trim();
    setPromptProfileDraft(promptSeed);
  }, [snap?.job_key, snap?.source?.slot, snap?.source?.path, snap?.source?.relpath, snap?.bindings]);

  useEffect(() => {
    const onUnload = () => {
      if (releasedRef.current || finished) return;
      try {
        const body = JSON.stringify({
          job_key: editJob,
          action: "cancel",
          actor: "operator",
          reason: "finish_edit:cancel",
          source_surface: "submit_edit",
        });
        navigator.sendBeacon?.(
          "/api/shape-factory/finish-edit",
          new Blob([body], { type: "application/json" }),
        );
      } catch {
        /* ignore */
      }
    };
    window.addEventListener("pagehide", onUnload);
    return () => window.removeEventListener("pagehide", onUnload);
  }, [editJob, finished]);

  useTrimPlaybackEnforcement(videoRef, {
    mediaKey: mediaRelpath || editJob,
    markIn,
    markOut,
    mode: trimMode,
    enabled: Boolean(isVideo && playUrl),
  });

  const persistTrim = (nextIn: number | null, nextOut: number | null) => {
    if (!(duration > 0)) return;
    const win = marksToVhsWindow(nextIn, nextOut, duration, fps, null);
    void updatePendingShapeFactoryTrim({
      job_key: editJob,
      skip_first_frames: win.skip_first_frames,
      frame_load_cap: win.frame_load_cap,
      mark_in: nextIn,
      mark_out: nextOut,
      actor: "operator",
      reason: "trim_adjustment",
      source_surface: "submit_edit",
    }).catch((err) => {
      console.warn("update-pending-trim failed", err);
    });
  };

  const applyBindingEdit = async (slot: "source_still" | "source_video" | "prompt_profile", value: string) => {
    const trimmed = String(value || "").trim();
    if (!trimmed || busy || finished) return;
    setBusy(true);
    setMsg(null);
    try {
      await updatePendingShapeFactoryBinding({
        job_key: editJob,
        slot,
        path: trimmed,
        actor: "operator",
        reason: `binding_adjustment:${slot}`,
        source_surface: "submit_edit",
      });
      await refreshSnapshot();
      setMsg(`Updated ${slot}`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (bootError) {
    return (
      <div className={`submit-composer${isModal ? " submit-composer--modal" : ""}`}>
        <PageHeader
          title="Edit job"
          subtitle={editJob}
          actions={
            isModal && onClose ? (
              <button type="button" className="drt-btn" onClick={onClose}>
                Close
              </button>
            ) : (
              <a className="drt-btn" href={originBack.href}>
                {originBack.label}
              </a>
            )
          }
        />
        <p className="work-product-viewer__trim-warn">{bootError}</p>
      </div>
    );
  }

  return (
    <div className={`submit-composer${isModal ? " submit-composer--modal" : ""}`}>
      <PageHeader
        title="Edit job"
        subtitle={`${snap?.family_slug || "…"} · ${editJob}`}
        actions={
          isModal && onClose ? (
            <button
              type="button"
              className="drt-btn"
              onClick={() => {
                if (releasedRef.current || finished) {
                  onClose();
                  return;
                }
                void releaseEdit("cancel");
              }}
            >
              Close
            </button>
          ) : (
            <a
              className="drt-btn"
              href={originBack.href}
              onClick={(e) => {
                if (releasedRef.current || finished) return;
                e.preventDefault();
                void releaseEdit("cancel");
              }}
            >
              {originBack.label}
            </a>
          )
        }
      />
      <div className="work-product-row work-product-row--split submit-composer__stage" aria-label="Edit job">
        <div className="work-product-row__head">
          <div className="work-product-row__head-main">
            <div className="work-product-row__title">
              <span className="work-product-badge work-product-badge--pending">editing</span>
              <strong>{snap?.family_slug || "job"}</strong>
            </div>
            <code className="work-product-row__key">{editJob}</code>
          </div>
        </div>

        <div className="work-product-row__body">
          <div className="work-product-viewer">
            <div className="work-product-viewer__main">
              {isVideo && playUrl ? (
                <video
                  ref={videoRef}
                  className="work-product-viewer__video"
                  src={playUrl}
                  poster={posterUrl || undefined}
                  controls
                  playsInline
                  muted
                  preload="metadata"
                  onLoadedMetadata={(e) => {
                    const d = e.currentTarget.duration;
                    if (Number.isFinite(d) && d > 0) setVideoDuration(d);
                    setCurrentTime(e.currentTarget.currentTime || 0);
                  }}
                  onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime || 0)}
                />
              ) : playUrl ? (
                <img className="work-product-viewer__video" src={playUrl} alt="" />
              ) : (
                <div className="work-product-viewer__empty">
                  {busy ? "Loading…" : "No source media on this job"}
                </div>
              )}
            </div>
            {isVideo && playUrl ? (
              <VideoTrimControls
                className="work-product-viewer__trim"
                videoRef={videoRef}
                currentTime={currentTime}
                duration={duration}
                markIn={markIn}
                markOut={markOut}
                mode={trimMode}
                mediaSyncKey={mediaRelpath || editJob}
                onSeek={(t) => {
                  const v = videoRef.current;
                  if (v) v.currentTime = t;
                  setCurrentTime(t);
                }}
                onSyncTime={setCurrentTime}
                onMarkInChange={(v) => {
                  setMarkIn(v);
                  persistTrim(v, markOut);
                }}
                onMarkOutChange={(v) => {
                  setMarkOut(v);
                  persistTrim(markIn, v);
                }}
                onModeChange={setTrimMode}
                onClear={() => {
                  setMarkIn(null);
                  setMarkOut(null);
                  persistTrim(null, null);
                }}
              />
            ) : null}
            {isVideo && mediaRelpath ? (
              <ClipBookmarksRail
                mediaRelpath={mediaRelpath}
                markIn={markIn}
                markOut={markOut}
                duration={duration}
                trimEditable
                origin="submit"
                selectedClipId={clipId || null}
                onSelectClip={(clip) => {
                  setActiveClip(clip);
                  setClipId(clip?.clip_id || "");
                }}
                onApplyClip={(mi, mo, clip) => {
                  setMarkIn(mi);
                  setMarkOut(mo);
                  if (clip) {
                    setActiveClip(clip);
                    setClipId(clip.clip_id);
                  }
                  persistTrim(mi, mo);
                }}
              />
            ) : null}
          </div>

          <div className="work-product-quick-queue">
            <p className="factory-muted">
              Editing this run in place. Pending drain will not queue it until you finish.
              {activeClip ? ` · clip ${activeClip.clip_id}` : ""}
            </p>
            <div className="submit-composer__edit-bindings">
              {snap?.source?.slot === "source_still" || snap?.source?.slot === "source_video" ? (
                <label className="submit-composer__edit-binding">
                  <span>Source ({snap?.source?.slot})</span>
                  <div className="submit-composer__edit-binding-row">
                    <input
                      type="text"
                      value={sourcePathDraft}
                      disabled={busy || finished}
                      onChange={(e) => setSourcePathDraft(e.target.value)}
                      placeholder={snap?.source?.path || "input/foo.jpeg"}
                    />
                    <button
                      type="button"
                      className="drt-btn"
                      disabled={busy || finished || !sourcePathDraft.trim()}
                      onClick={() => void applyBindingEdit(snap?.source?.slot as "source_still" | "source_video", sourcePathDraft)}
                    >
                      Apply
                    </button>
                  </div>
                </label>
              ) : null}
              {snap?.bindings?.prompt_profile ? (
                <label className="submit-composer__edit-binding">
                  <span>Prompt profile</span>
                  <div className="submit-composer__edit-binding-row">
                    <input
                      type="text"
                      value={promptProfileDraft}
                      disabled={busy || finished}
                      onChange={(e) => setPromptProfileDraft(e.target.value)}
                      placeholder="input/prompt-profiles/..."
                    />
                    <button
                      type="button"
                      className="drt-btn"
                      disabled={busy || finished || !promptProfileDraft.trim()}
                      onClick={() => void applyBindingEdit("prompt_profile", promptProfileDraft)}
                    >
                      Apply
                    </button>
                  </div>
                </label>
              ) : null}
            </div>
            <div className="work-product-quick-queue__actions" role="group" aria-label="Finish edit">
              <button
                type="button"
                className="drt-btn"
                disabled={busy || finished}
                onClick={() => void releaseEdit("now")}
              >
                Queue now
              </button>
              <button
                type="button"
                className="drt-btn"
                disabled={busy || finished}
                onClick={() => void releaseEdit("later")}
              >
                Save for later
              </button>
              <button
                type="button"
                className="drt-btn"
                disabled={busy || finished}
                onClick={() => void releaseEdit("cancel")}
              >
                Cancel
              </button>
            </div>
            {msg ? <p className="factory-muted">{msg}</p> : null}
          </div>
        </div>
      </div>
    </div>
  );
}

type ConstructionReady = {
  ok: boolean;
  label: string;
  detail: string | null;
};

function SubmitConstructionPreview({
  routes,
  useLabel,
  useWindow,
  vhs,
  vhsWarning,
  identity,
  preferredWhen,
  origin,
  fromJob,
  ready,
}: {
  routes: { kind: string; family: string; shapeId: string | null }[];
  useLabel: string;
  useWindow: string | null;
  vhs: { skip: number; cap: number } | null;
  vhsWarning: string | null;
  identity: {
    mode: "off" | "loading" | "not_required" | "needed" | "set";
    path: string;
    thumbUrl: string | null;
  };
  preferredWhen: "now" | "later";
  origin: string | null;
  fromJob: string | null;
  ready: ConstructionReady;
}) {
  const identityThumb =
    identity.mode === "set" && identity.thumbUrl ? (
      <img className="submit-composer__construction-ident-thumb" src={identity.thumbUrl} alt="" loading="lazy" />
    ) : null;
  const identityValue =
    identity.mode === "off"
      ? "—"
      : identity.mode === "loading"
        ? "Loading…"
        : identity.mode === "not_required"
          ? "Not required"
          : identity.mode === "needed"
            ? "Needed — pick or mint"
            : basenamePath(identity.path);

  return (
    <div className="submit-composer__construction" aria-label="Construction preview">
      <div className="submit-composer__construction-head">
        <span className="work-product-quick-queue__label">Construction</span>
        <span
          className={`work-product-badge submit-composer__ready${
            ready.ok ? " submit-composer__ready--ok" : " submit-composer__ready--blocked"
          }`}
          title={ready.detail || ready.label}
        >
          {ready.label}
        </span>
      </div>
      <div className="work-product-details__chips submit-composer__construction-chips">
        {routes.length ? (
          routes.map((r) => (
            <span
              key={`${r.kind}:${r.family}`}
              className="work-product-badge"
              title={r.shapeId ? `${r.kind} · shape ${r.shapeId}` : r.kind}
            >
              {r.kind}@{r.family || "?"}
              {r.shapeId ? ` · ${r.shapeId}` : ""}
            </span>
          ))
        ) : (
          <span className="work-product-badge">no route</span>
        )}
        <span
          className={`work-product-badge ${
            preferredWhen === "now" ? "work-product-badge--front" : "work-product-badge--pending"
          }`}
          title="Intended priority (Now / Later buttons commit)"
        >
          {preferredWhen === "now" ? "now" : "later"}
        </span>
      </div>
      <dl className="submit-composer__construction-list">
        <div className="submit-composer__construction-row">
          <dt>Use</dt>
          <dd title={useWindow || useLabel}>
            {useLabel}
            {useWindow ? <span className="submit-composer__construction-sub"> · {useWindow}</span> : null}
          </dd>
        </div>
        <div className="submit-composer__construction-row">
          <dt>VHS</dt>
          <dd title={vhsWarning || undefined}>
            {vhs ? (
              <>
                skip {vhs.skip}
                {vhs.cap > 0 ? ` · cap ${vhs.cap}` : ""}
              </>
            ) : (
              "—"
            )}
            {vhsWarning ? <span className="submit-composer__construction-warn"> · {vhsWarning}</span> : null}
          </dd>
        </div>
        {identity.mode !== "off" ? (
          <div className="submit-composer__construction-row">
            <dt>Identity</dt>
            <dd className="submit-composer__construction-ident" title={identity.path || undefined}>
              {identityThumb}
              <span>{identityValue}</span>
            </dd>
          </div>
        ) : null}
        {origin || fromJob ? (
          <div className="submit-composer__construction-row">
            <dt>Context</dt>
            <dd title={[origin && `from ${origin}`, fromJob && `job ${fromJob}`].filter(Boolean).join(" · ")}>
              {[origin ? `from ${origin}` : null, fromJob ? fromJob : null].filter(Boolean).join(" · ")}
            </dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}

export type SubmitComposerProps = {
  /** When omitted, intent comes from the URL (full /submit page). */
  intent?: SubmitDeepLink | null;
  presentation?: "page" | "modal";
  onClose?: () => void;
  onSubmitted?: (info: { jobKeys: string[] }) => void;
};

export function SubmitComposerApp({
  intent: intentProp,
  presentation = "page",
  onClose,
  onSubmitted,
}: SubmitComposerProps = {}) {
  const urlIntent = useMemo(() => parseSubmitDeepLink(), []);
  const intent = intentProp ?? urlIntent;
  if (intent.editJob) {
    return (
      <SubmitEditJobApp
        editJob={intent.editJob}
        origin={intent.origin}
        presentation={presentation}
        onClose={onClose}
      />
    );
  }
  return (
    <SubmitAdvanceComposerApp
      intent={intent}
      presentation={presentation}
      onClose={onClose}
      onSubmitted={onSubmitted}
    />
  );
}

function SubmitAdvanceComposerApp({
  intent,
  presentation = "page",
  onClose,
  onSubmitted,
}: {
  intent: SubmitDeepLink;
  presentation?: "page" | "modal";
  onClose?: () => void;
  onSubmitted?: (info: { jobKeys: string[] }) => void;
}) {
  const isModal = presentation === "modal";
  const initialRoutes = useMemo(() => stepToRouteFlags(intent.step), [intent.step]);
  const cachedFamiliesBoot = useMemo(() => peekFamiliesBootstrap(), []);
  const [layout, setLayout] = useState<RowLayout>(() => loadLayout());
  const [mediaRelpath, setMediaRelpath] = useState(intent.mediaRelpath || "");
  const isStill = isStillMediaPath(mediaRelpath);
  const [clipId, setClipId] = useState(intent.clipId || "");
  const [markIn, setMarkIn] = useState<number | null>(intent.markIn);
  const [markOut, setMarkOut] = useState<number | null>(intent.markOut);
  const [activeClip, setActiveClip] = useState<ShapeFactoryClip | null>(null);
  const [videoDuration, setVideoDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [trimMode, setTrimMode] = useState<VideoTrimPlaybackMode>("repeat");
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [i2vFamily, setI2vFamily] = useState(() => {
    if (!isStillMediaPath(intent.mediaRelpath)) return "";
    if (intent.family) return intent.family;
    if (!cachedFamiliesBoot) return "";
    return pickDefaultI2VFamily(cachedFamiliesBoot.families || [], intent.family);
  });

  const duration =
    videoDuration > 0
      ? videoDuration
      : // Never use clip.duration_s here — that is the bookmark span (out−in), not media length.
        // Until <video> metadata loads, span at least to markOut so trim handles remain usable.
        Math.max(markOut ?? 0, markIn ?? 0, 0);
  const fps = 18;

  const [families, setFamilies] = useState<WorkProductFamilyOption[]>(
    () => cachedFamiliesBoot?.families || [],
  );
  const [extendFamilyRows, setExtendFamilyRows] = useState<WorkProductFamilyOption[]>(
    () => cachedFamiliesBoot?.extend_families || cachedFamiliesBoot?.families || [],
  );
  const [varyFamilyRows, setVaryFamilyRows] = useState<WorkProductFamilyOption[]>(
    () => cachedFamiliesBoot?.vary_families || cachedFamiliesBoot?.families || [],
  );
  const [deriveFamilyRows, setDeriveFamilyRows] = useState<WorkProductFamilyOption[]>(
    () => cachedFamiliesBoot?.derive_families || cachedFamiliesBoot?.families || [],
  );
  const [extendOn, setExtendOn] = useState(initialRoutes.extend);
  const [varyOn, setVaryOn] = useState(initialRoutes.vary);
  const [deriveOn, setDeriveOn] = useState(initialRoutes.derive);
  const [extendFamily, setExtendFamily] = useState(() => {
    if (intent.family) return intent.family;
    if (!cachedFamiliesBoot) return "";
    const pool = cachedFamiliesBoot.extend_families?.length
      ? cachedFamiliesBoot.extend_families
      : cachedFamiliesBoot.families;
    return pickDefaultExtendFamily(
      pool,
      cachedFamiliesBoot.extend_family_defaults,
      intent.family,
      intent.mediaRelpath,
    );
  });
  const [varyFamily, setVaryFamily] = useState(() => {
    if (intent.family) return intent.family;
    if (!cachedFamiliesBoot) return "";
    const pool = cachedFamiliesBoot.extend_families?.length
      ? cachedFamiliesBoot.extend_families
      : cachedFamiliesBoot.families;
    return (
      pickDefaultExtendFamily(
        pool,
        cachedFamiliesBoot.extend_family_defaults,
        intent.family,
        intent.mediaRelpath,
      ) || ""
    );
  });
  const [deriveFamily, setDeriveFamily] = useState(() => {
    if (intent.family) return intent.family;
    if (!cachedFamiliesBoot) return "";
    const pool = cachedFamiliesBoot.extend_families?.length
      ? cachedFamiliesBoot.extend_families
      : cachedFamiliesBoot.families;
    return (
      pickDefaultExtendFamily(
        pool,
        cachedFamiliesBoot.extend_family_defaults,
        intent.family,
        intent.mediaRelpath,
      ) || ""
    );
  });

  const applyFamiliesBoot = useCallback(
    (boot: FamiliesBootstrap) => {
      const rows = boot.families || [];
      const extendRows = boot.extend_families?.length ? boot.extend_families : rows;
      const varyRows = boot.vary_families?.length ? boot.vary_families : rows;
      const deriveRows = boot.derive_families?.length ? boot.derive_families : rows;
      const defaults = boot.extend_family_defaults || {};
      setFamilies(rows);
      setExtendFamilyRows(extendRows);
      setVaryFamilyRows(varyRows);
      setDeriveFamilyRows(deriveRows);
      const extendDefault = pickDefaultExtendFamily(
        extendRows,
        defaults,
        intent.family,
        mediaRelpath || intent.mediaRelpath,
      );
      const routeSeedFamily = String(intent.family || "").trim() || extendDefault;
      setExtendFamily((prev) => {
        const prevOk = Boolean(prev) && extendRows.some((f) => f.slug === prev && isExtendFamilyOption(f));
        return prevOk ? prev : extendDefault;
      });
      setVaryFamily((prev) => prev || routeSeedFamily);
      setDeriveFamily((prev) => prev || routeSeedFamily);
      if (isStillMediaPath(mediaRelpath || intent.mediaRelpath)) {
        const i2vDefault = pickDefaultI2VFamily(rows, intent.family);
        setI2vFamily((prev) => {
          const prevOk = Boolean(prev) && rows.some((f) => f.slug === prev && isI2VFamilyOption(f));
          return prevOk ? prev : i2vDefault;
        });
      }
    },
    [intent.family, intent.mediaRelpath, mediaRelpath],
  );

  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<Error | null>(null);
  const [lastJobKey, setLastJobKey] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const recentSubmitsQuery = useQuery({
    queryKey: queryKeys.shapeFactory.submitAttempts({ limit: 12, errorsOnly: false }),
    queryFn: () => fetchShapeFactorySubmitAttempts({ limit: 12, errorsOnly: false }),
    staleTime: 15_000,
    refetchOnWindowFocus: true,
  });
  const [preferredWhen, setPreferredWhen] = useState<"now" | "later">(
    () => (intent.when === "now" || intent.when === "later" ? intent.when : "later"),
  );

  const cachedIdentity =
    intent.mediaRelpath && (initialRoutes.extend || intent.family)
      ? peekIdentityStill({
          relpath: intent.mediaRelpath,
          family_slug: intent.family || undefined,
          job_key: intent.fromJob || undefined,
        })
      : null;

  const [identityNeeded, setIdentityNeeded] = useState(() => Boolean(cachedIdentity?.needed));
  const [identityLoading, setIdentityLoading] = useState(false);
  const [identityCandidates, setIdentityCandidates] = useState<IdentityStillCandidate[]>(
    () => (Array.isArray(cachedIdentity?.candidates) ? cachedIdentity!.candidates! : []),
  );
  const [identityMintTargets, setIdentityMintTargets] = useState<IdentityStillMintTarget[]>(
    () => (Array.isArray(cachedIdentity?.mint_targets) ? cachedIdentity!.mint_targets! : []),
  );
  const [identitySelectedPath, setIdentitySelectedPath] = useState(intent.identity || "");
  const [identitySelectedId, setIdentitySelectedId] = useState("");
  const [identityMintBusy, setIdentityMintBusy] = useState(false);

  const playUrl = mediaRelpath.trim() ? filesUrl(mediaRelpath.trim()) : null;
  const posterUrl = mediaRelpath.trim() ? thumbUrlForMedia(mediaRelpath.trim()) : null;
  const mediaKey = mediaRelpath.trim() || "submit-empty";

  useTrimPlaybackEnforcement(videoRef, {
    mediaKey,
    markIn,
    markOut,
    mode: trimMode,
    enabled: Boolean(playUrl) && !isStill,
  });

  useEffect(() => {
    setVideoDuration(0);
    setCurrentTime(0);
  }, [mediaKey]);

  // Load families (session cache first; soft-refresh in background)
  useEffect(() => {
    let cancelled = false;
    const cached = peekFamiliesBootstrap();
    if (cached) applyFamiliesBoot(cached);
    void loadFamiliesBootstrap()
      .then((boot) => {
        if (cancelled) return;
        applyFamiliesBoot(boot);
      })
      .catch(() => {
        /* surface on submit */
      });
    return () => {
      cancelled = true;
    };
  }, [applyFamiliesBoot]);

  // Resolve clip_id → marks / media (skip when deep-link already has a window and no clip to resolve)
  useEffect(() => {
    const id = clipId.trim();
    if (!id) return;
    let cancelled = false;
    void (async () => {
      try {
        const lib = await listShapeFactoryClipsLibrary({ q: id, limit: 40 });
        const hit =
          (lib.clips || []).find((c) => c.clip_id === id) ||
          (lib.clips || []).find((c) => (c.clip_id || "").startsWith(id));
        if (cancelled || !hit) return;
        setActiveClip(hit);
        if (hit.media_relpath) setMediaRelpath(hit.media_relpath);
        setMarkIn(hit.mark_in_s);
        setMarkOut(hit.mark_out_s);
      } catch {
        /* keep deep-link marks */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [clipId]);

  // Identity candidates — only when Extend is checked
  useEffect(() => {
    const rel = mediaRelpath.trim();
    if (!extendOn || !rel || !extendFamily) {
      setIdentityNeeded(false);
      setIdentityCandidates([]);
      setIdentityMintTargets([]);
      return;
    }
    let cancelled = false;
    const opts = {
      relpath: rel,
      family_slug: extendFamily,
      job_key: intent.fromJob || undefined,
    };
    const cached = peekIdentityStill(opts);
    if (cached) {
      const needed = Boolean(cached.needed);
      setIdentityNeeded(needed);
      const cands = Array.isArray(cached.candidates) ? cached.candidates : [];
      setIdentityCandidates(cands);
      setIdentityMintTargets(Array.isArray(cached.mint_targets) ? cached.mint_targets : []);
      if (needed) {
        if (intent.identity && cands.some((c) => c.path === intent.identity)) {
          setIdentitySelectedPath(intent.identity);
          setIdentitySelectedId(cands.find((c) => c.path === intent.identity)?.id || "");
        } else {
          const sticky = readStickyIdentity();
          if (sticky) {
            setIdentitySelectedPath(sticky);
            setIdentitySelectedId(cands.find((c) => c.path === sticky)?.id || "");
          } else {
            const rec = cands.find((c) => c.id === cached.recommended_id) || cands[0];
            setIdentitySelectedPath(rec?.path || intent.identity || "");
            setIdentitySelectedId(rec?.id || "");
          }
        }
      }
      setIdentityLoading(false);
    } else {
      setIdentityLoading(true);
    }
    void loadIdentityStillCandidates(opts)
      .then((res) => {
        if (cancelled) return;
        const needed = Boolean(res.needed);
        setIdentityNeeded(needed);
        const cands = Array.isArray(res.candidates) ? res.candidates : [];
        setIdentityCandidates(cands);
        setIdentityMintTargets(Array.isArray(res.mint_targets) ? res.mint_targets : []);
        if (needed) {
          if (intent.identity && cands.some((c) => c.path === intent.identity)) {
            setIdentitySelectedPath(intent.identity);
            setIdentitySelectedId(cands.find((c) => c.path === intent.identity)?.id || "");
          } else {
            const sticky = readStickyIdentity();
            if (sticky) {
              setIdentitySelectedPath(sticky);
              setIdentitySelectedId(cands.find((c) => c.path === sticky)?.id || "");
            } else {
              const rec = cands.find((c) => c.id === res.recommended_id) || cands[0];
              setIdentitySelectedPath(rec?.path || intent.identity || "");
              setIdentitySelectedId(rec?.id || "");
            }
          }
        } else if (!intent.identity) {
          setIdentitySelectedPath("");
          setIdentitySelectedId("");
        }
      })
      .catch(() => {
        if (cancelled) return;
        if (!cached) {
          setIdentityNeeded(false);
          setIdentityCandidates([]);
          setIdentityMintTargets([]);
        }
      })
      .finally(() => {
        if (!cancelled) setIdentityLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [extendOn, extendFamily, mediaRelpath, intent.identity, intent.fromJob]);

  const windowOk =
    markIn != null &&
    markOut != null &&
    Number.isFinite(markIn) &&
    Number.isFinite(markOut) &&
    markOut > markIn + 0.05;

  const anyRoute = extendOn || varyOn || deriveOn;
  const i2vFamilyOpts = useMemo(() => {
    const rows = families.filter(isI2VFamilyOption);
    if (i2vFamily && !rows.some((f) => f.slug === i2vFamily)) {
      rows.unshift(families.find((f) => f.slug === i2vFamily) || { slug: i2vFamily });
    }
    return rows.length ? rows : families;
  }, [families, i2vFamily]);

  const canSubmit = isStill
    ? Boolean(mediaRelpath.trim()) && Boolean(i2vFamily) && !busy
    : Boolean(mediaRelpath.trim()) &&
      anyRoute &&
      (!extendOn || Boolean(extendFamily)) &&
      (!varyOn || Boolean(varyFamily)) &&
      (!deriveOn || Boolean(deriveFamily)) &&
      windowOk &&
      !busy &&
      !(extendOn && identityLoading) &&
      !(extendOn && identityNeeded && !identitySelectedPath);

  const familyOpts = useMemo(() => {
    const rows = [...varyFamilyRows];
    for (const slug of [extendFamily, varyFamily, deriveFamily]) {
      if (slug && !rows.some((f) => f.slug === slug)) rows.unshift({ slug });
    }
    return rows.length ? rows : families;
  }, [varyFamilyRows, families, extendFamily, varyFamily, deriveFamily]);

  /** Prefer server-partitioned extend set; fall back to client filter. */
  const extendFamilyOpts = useMemo(() => {
    const rows = [...(extendFamilyRows.length ? extendFamilyRows : families.filter(isExtendFamilyOption))];
    if (extendFamily && !rows.some((f) => f.slug === extendFamily)) {
      const hit = families.find((f) => f.slug === extendFamily) || extendFamilyRows.find((f) => f.slug === extendFamily);
      rows.unshift(hit || { slug: extendFamily });
    }
    return rows.length ? rows : familyOpts;
  }, [extendFamilyRows, families, extendFamily, familyOpts]);

  const deriveFamilyOpts = useMemo(() => {
    const rows = [...(deriveFamilyRows.length ? deriveFamilyRows : familyOpts)];
    if (deriveFamily && !rows.some((f) => f.slug === deriveFamily)) {
      rows.unshift({ slug: deriveFamily });
    }
    return rows;
  }, [deriveFamilyRows, familyOpts, deriveFamily]);

  const buildOverrides = useCallback((): {
    overrides?: ShapeFactoryMapQueueOverrides;
    warning: string | null;
  } => {
    if (!windowOk || markIn == null || markOut == null) return { warning: "Set mark in/out or select a clip" };
    const mediaDur =
      videoRef.current && Number.isFinite(videoRef.current.duration) && videoRef.current.duration > 0
        ? videoRef.current.duration
        : videoDuration > 0
          ? videoDuration
          : 0;
    const win = marksToVhsWindow(markIn, markOut, mediaDur, fps > 0 ? fps : 18, null);
    const overrides: ShapeFactoryMapQueueOverrides = {
      parameters: {
        // Seconds are authoritative — backend probes real fps/duration and derives skip/cap.
        mark_in: markIn,
        mark_out: markOut,
        skip_first_frames: win.skip_first_frames,
        frame_load_cap: win.frame_load_cap,
      },
    };
    if (activeClip?.clip_id || clipId) overrides.source_clip_id = activeClip?.clip_id || clipId;
    return { overrides, warning: win.warning };
  }, [activeClip?.clip_id, clipId, fps, markIn, markOut, videoDuration, windowOk]);

  const submit = async (when: "now" | "later") => {
    if (!canSubmit) return;
    setPreferredWhen(when);
    setBusy(true);
    setMsg(null);
    setSubmitError(null);
    setLastJobKey(null);
    try {
      if (isStill) {
        const stillPath = mediaRelpath.trim().replace(/\\/g, "/");
        const bindingPath =
          stillPath.toLowerCase().startsWith("input/") || stillPath.includes("/")
            ? stillPath
            : `input/${stillPath.split("/").pop() || stillPath}`;
        const res = await queueShapeFactoryCombo({
          family_slug: i2vFamily,
          bindings: { source_still: bindingPath },
          front: when === "now",
          source_surface: "submit",
        });
        if (res.job_key) setLastJobKey(res.job_key);
        setMsg(
          res.prompt_id
            ? `Queued ${i2vFamily} · prompt ${res.prompt_id}`
            : res.job_key
              ? `Created ${res.job_key}${when === "later" ? " (pending)" : ""}`
              : `Seeded ${i2vFamily}`,
        );
        void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.submitAttemptsRoot });
        if (res.job_key) {
          onSubmitted?.({ jobKeys: [res.job_key] });
          clearStickyIdentity();
        }
        return;
      }
      const { overrides, warning } = buildOverrides();
      if (!overrides) {
        setMsg(warning || "Need a clip window");
        return;
      }
      const routes = [];
      if (extendOn && extendFamily) {
        routes.push({
          stepId: "advance.extend",
          family: extendFamily,
          identityAnchor: identitySelectedPath || null,
        });
      }
      if (varyOn && varyFamily) {
        routes.push({ stepId: "advance.vary", family: varyFamily });
      }
      if (deriveOn && deriveFamily) {
        routes.push({ stepId: "advance.derive", family: deriveFamily });
      }
      if (!routes.length) {
        setMsg("Select Extend, Vary, and/or Derive");
        return;
      }
      const result = await composeSubmitAdvance({
        mediaRelpath: mediaRelpath.trim(),
        when,
        routes,
        overrides,
        jobKey: intent.fromJob,
      });
      setLastJobKey(result.jobKeys[0] || null);
      setMsg([result.message, warning].filter(Boolean).join(" · "));
      void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.submitAttemptsRoot });
      if (result.jobKeys.length) {
        onSubmitted?.({ jobKeys: result.jobKeys });
        if (identitySelectedPath) clearStickyIdentity();
      }    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      setSubmitError(err);
      setMsg(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.submitAttemptsRoot });
    } finally {
      setBusy(false);
    }
  };

  const mintIdentity = async (target: IdentityStillMintTarget) => {
    if (identityMintBusy || busy) return;
    setIdentityMintBusy(true);
    setMsg(null);
    try {
      const res = await mintIdentityStill({
        video_relpath: target.video_relpath,
        video_path: target.video_path,
        at: target.at || "start",
      });
      const cand = res.candidate;
      if (cand?.path) {
        invalidateIdentityStill({
          relpath: mediaRelpath.trim(),
          family_slug: extendFamily || undefined,
          job_key: intent.fromJob || undefined,
        });
        setIdentityCandidates((prev) => {
          if (prev.some((c) => c.id === cand.id || c.path === cand.path)) return prev;
          return [cand, ...prev];
        });
        setIdentitySelectedPath(cand.path);
        setIdentitySelectedId(cand.id);
        setMsg(`Minted identity still · ${cand.relpath || cand.path}`);
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setIdentityMintBusy(false);
    }
  };

  const familySelect = (
    value: string,
    onChange: (slug: string) => void,
    label: string,
    title: string,
    opts: WorkProductFamilyOption[] = familyOpts,
  ) => (
    <label className="work-product-quick-queue__family-wrap">
      <span className="work-product-quick-queue__family-label">{label}</span>
      <select
        className="work-product-quick-queue__family"
        value={value}
        disabled={busy || !opts.length}
        aria-label={`${label} target family`}
        title={title}
        onChange={(e) => onChange(e.target.value)}
      >
        {opts.length === 0 ? <option value="">Loading…</option> : null}
        {opts.map((f) => {
          const promoScope = String(f.promotion?.scope || "").trim();
          const promoSuffix = promoScope === "temporary" ? " [TEMP]" : promoScope === "long_term" ? " [DEFAULT]" : "";
          return (
            <option key={f.slug} value={f.slug}>
              {f.slug}
              {promoSuffix}
            </option>
          );
        })}
      </select>
    </label>
  );

  const hasIntent = hasSubmitIntent({
    mediaRelpath: mediaRelpath || intent.mediaRelpath,
    clipId: clipId || intent.clipId,
    fromJob: intent.fromJob,
    editJob: intent.editJob,
  });
  const originBack = useMemo(
    () =>
      submitOriginHref(intent.origin, {
        mediaRelpath: mediaRelpath || intent.mediaRelpath,
        clipId: clipId || activeClip?.clip_id || intent.clipId,
        fromJob: intent.fromJob,
        editJob: intent.editJob,
      }),
    [activeClip?.clip_id, clipId, intent.clipId, intent.editJob, intent.fromJob, intent.mediaRelpath, intent.origin, mediaRelpath],
  );

  const constructionPreview = useMemo(() => {
    const routes: { kind: string; family: string; shapeId: string | null }[] = [];
    if (isStill) {
      routes.push({
        kind: "Seed",
        family: i2vFamily || "",
        shapeId: familyShapeId(families, i2vFamily),
      });
    } else {
      if (extendOn) {
        routes.push({
          kind: "Extend",
          family: extendFamily || "",
          shapeId: familyShapeId(families, extendFamily),
        });
      }
      if (varyOn) {
        routes.push({
          kind: "Vary",
          family: varyFamily || "",
          shapeId: familyShapeId(families, varyFamily),
        });
      }
      if (deriveOn) {
        routes.push({
          kind: "Derive",
          family: deriveFamily || "",
          shapeId: familyShapeId(families, deriveFamily),
        });
      }
    }

    const { overrides, warning } = isStill
      ? { overrides: undefined as ShapeFactoryMapQueueOverrides | undefined, warning: null as string | null }
      : buildOverrides();
    const params = (overrides?.parameters || {}) as Record<string, unknown>;
    const vhs =
      overrides && windowOk
        ? {
            skip: Number(params.skip_first_frames ?? 0) || 0,
            cap: Number(params.frame_load_cap ?? 0) || 0,
          }
        : null;

    const useLabel = isStill
      ? "Still (full image)"
      : activeClip
        ? `Clip · ${activeClip.label || activeClip.clip_id}`
        : windowOk
          ? "Scrubber window"
          : "No Use window";
    const useWindow =
      !isStill && windowOk && markIn != null && markOut != null
        ? `${formatTc(markIn)}–${formatTc(markOut)}`
        : null;

    let identityMode: "off" | "loading" | "not_required" | "needed" | "set" = "off";
    if (!isStill && extendOn) {
      if (identityLoading) identityMode = "loading";
      else if (!identityNeeded) identityMode = "not_required";
      else if (identitySelectedPath) identityMode = "set";
      else identityMode = "needed";
    }
    const identityCand =
      identityCandidates.find((c) => c.id === identitySelectedId || c.path === identitySelectedPath) || null;

    const blockers: string[] = [];
    if (!mediaRelpath.trim()) blockers.push("need media");
    if (isStill) {
      if (!i2vFamily) blockers.push("I2V family");
    } else {
      if (!anyRoute) blockers.push("select a route");
      if (extendOn && !extendFamily) blockers.push("Extend family");
      if (varyOn && !varyFamily) blockers.push("Vary family");
      if (deriveOn && !deriveFamily) blockers.push("Derive family");
      if (!windowOk) blockers.push("set Use window");
      if (extendOn && identityLoading) blockers.push("identity loading");
      if (extendOn && identityNeeded && !identitySelectedPath) blockers.push("pick identity");
    }
    if (busy) blockers.push("submitting");

    const ready: ConstructionReady = canSubmit
      ? { ok: true, label: "Ready", detail: null }
      : {
          ok: false,
          label: blockers[0] ? `Blocked · ${blockers[0]}` : "Blocked",
          detail: blockers.join(" · ") || null,
        };

    return {
      routes,
      useLabel,
      useWindow,
      vhs,
      vhsWarning: warning,
      identity: {
        mode: identityMode,
        path: identitySelectedPath,
        thumbUrl: identityCand?.thumb_url || identityCand?.url || null,
      },
      ready,
    };
  }, [
    activeClip,
    anyRoute,
    buildOverrides,
    busy,
    canSubmit,
    deriveFamily,
    deriveOn,
    extendFamily,
    extendOn,
    families,
    i2vFamily,
    identityCandidates,
    identityLoading,
    identityNeeded,
    identitySelectedId,
    identitySelectedPath,
    isStill,
    markIn,
    markOut,
    mediaRelpath,
    varyFamily,
    varyOn,
    windowOk,
  ]);

  return (
    <div className={`layout submit-composer panel${isModal ? " submit-composer--modal" : ""}`}>
      <PageHeader
        title="Submit"
        subtitle={
          isModal
            ? "Compose without leaving Workbench"
            : "Compose a factory job from a door handoff — Library, Clips, and Workbench find the subject; Submit only composes."
        }
        actions={
          <div className="submit-composer__header-actions">
            {isModal && onClose ? (
              <button type="button" className="drt-btn" onClick={onClose}>
                Close
              </button>
            ) : originBack ? (
              <a className="drt-btn" href={originBack.href}>
                {originBack.label}
              </a>
            ) : null}
            {hasIntent ? (
              <div className="discovery-preview-layout-switch" role="group" aria-label="Compose layout">
                <span className="discovery-preview-layout-switch__label">Layout</span>
                <div className="segmented">
                  <button
                    type="button"
                    className={layout === "split" ? "seg-btn active" : "seg-btn"}
                    onClick={() => {
                      setLayout("split");
                      persistLayout("split");
                    }}
                  >
                    Side by side
                  </button>
                  <button
                    type="button"
                    className={layout === "stacked" ? "seg-btn active" : "seg-btn"}
                    onClick={() => {
                      setLayout("stacked");
                      persistLayout("stacked");
                    }}
                  >
                    Stacked
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        }
      />
      {!hasIntent ? (
        <div className="submit-composer__empty" aria-label="Submit needs intent">
          <p className="submit-composer__empty-lead">
            Submit is <strong>intent-only</strong> — open it from a doorway with a still, clip, scrubber window, or job.
            This screen does not browse the corpus.
          </p>
          <p className="factory-muted">
            Deep link shape: <span className="mono">/submit?media=…</span> (still or video),{" "}
            <span className="mono">clip_id=…</span>, or <span className="mono">from_job=…</span> (+ optional{" "}
            <span className="mono">origin</span>).
          </p>
          <div className="submit-composer__empty-doors" role="list">
            <a className="drt-btn" href="/discovery" role="listitem">
              Library
            </a>
            <a className="drt-btn" href={clipsLibraryHref({ view: "all" })} role="listitem">
              Clips
            </a>
            <a className="drt-btn" href="/workbench" role="listitem">
              Workbench
            </a>
            <span className="factory-muted submit-composer__empty-soon" role="listitem">
              Factory · Rating doors next
            </span>
          </div>
          <RecentSubmitsPanel items={recentSubmitsQuery.data?.items || []} />
        </div>
      ) : (
        <div
          className={`work-product-row work-product-row--${layout} submit-composer__stage`}
          aria-label="Compose"
        >
          <div className="work-product-row__head">
            <div className="work-product-row__head-main">
              <div className="work-product-row__title">
                <span className="work-product-badge">compose</span>
                <span title={mediaRelpath}>{mediaRelpath.split("/").pop() || mediaRelpath}</span>
              </div>
              <code className="work-product-row__key" title={mediaRelpath}>
                {mediaRelpath}
              </code>
            </div>
            <div className="submit-composer__links">
              {isModal && onClose ? (
                <button type="button" className="drt-btn" onClick={onClose}>
                  Close
                </button>
              ) : originBack ? (
                <a className="drt-btn" href={originBack.href}>
                  {originBack.label}
                </a>
              ) : null}
              {mediaRelpath ? (
                <a className="drt-btn" href={discoveryLibraryHref(mediaRelpath)}>
                  Library
                </a>
              ) : null}
              {mediaRelpath ? (
                <a
                  className="drt-btn"
                  href={clipsLibraryHref({
                    mediaRelpath,
                    clipId: clipId || activeClip?.clip_id,
                    view: "by_source",
                  })}
                >
                  Clips
                </a>
              ) : null}
            </div>
          </div>

          <div className="work-product-row__body">
            <div className="work-product-viewer">
              <div className="work-product-viewer__main">
                {playUrl && isStill ? (
                  <img className="work-product-viewer__video" src={posterUrl || playUrl} alt="" />
                ) : playUrl ? (
                  <video
                    ref={videoRef}
                    className="work-product-viewer__video"
                    src={playUrl}
                    poster={posterUrl || undefined}
                    controls
                    playsInline
                    muted
                    preload="metadata"
                    onLoadedMetadata={(e) => {
                      const d = e.currentTarget.duration;
                      if (Number.isFinite(d) && d > 0) setVideoDuration(d);
                      setCurrentTime(e.currentTarget.currentTime || 0);
                    }}
                    onDurationChange={(e) => {
                      const d = e.currentTarget.duration;
                      if (Number.isFinite(d) && d > 0) setVideoDuration(d);
                    }}
                    onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime || 0)}
                    onSeeked={(e) => setCurrentTime(e.currentTarget.currentTime || 0)}
                  />
                ) : (
                  <div className="work-product-viewer__empty">No media</div>
                )}
              </div>
              {playUrl && !isStill ? (
                <>
                  <VideoTrimControls
                    className="work-product-viewer__trim"
                    videoRef={videoRef}
                    duration={duration}
                    currentTime={currentTime}
                    markIn={markIn}
                    markOut={markOut}
                    mode={trimMode}
                    mediaSyncKey={mediaKey}
                    onSeek={setCurrentTime}
                    onSyncTime={setCurrentTime}
                    onMarkInChange={(v) => {
                      setMarkIn(v);
                      setActiveClip(null);
                      setClipId("");
                    }}
                    onMarkOutChange={(v) => {
                      setMarkOut(v);
                      setActiveClip(null);
                      setClipId("");
                    }}
                    onClear={() => {
                      setMarkIn(null);
                      setMarkOut(null);
                      setActiveClip(null);
                      setClipId("");
                    }}
                    onModeChange={setTrimMode}
                  />
                  {!windowOk ? (
                    <p className="work-product-viewer__trim-warn">Set mark in/out or pick a clip for Use.</p>
                  ) : null}
                  <ClipBookmarksRail
                    mediaRelpath={mediaRelpath.trim() || null}
                    duration={duration}
                    markIn={markIn}
                    markOut={markOut}
                    trimEditable
                    origin="submit"
                    selectedClipId={activeClip?.clip_id || clipId || null}
                    onSelectClip={(c) => {
                      setActiveClip(c);
                      setClipId(c?.clip_id || "");
                    }}
                    onApplyClip={(mi, mo, clip) => {
                      setMarkIn(mi);
                      setMarkOut(mo);
                      if (clip) {
                        setActiveClip(clip);
                        setClipId(clip.clip_id);
                      }
                    }}
                  />
                </>
              ) : null}
              {isStill ? (
                <p className="work-product-viewer__trim-warn factory-muted">
                  Still seed — pick an I2V family and Submit now/later.
                </p>
              ) : null}
            </div>

            <div className="work-product-details submit-composer__compose">
              {isStill ? (
                <div className="work-product-quick-queue" role="group" aria-label="Submit still seed">
                  <div className="work-product-quick-queue__row">
                    <span className="work-product-quick-queue__label" title="I2V origin family for this still">
                      Seed
                    </span>
                    <span className="work-product-quick-queue__sep" aria-hidden="true" />
                    <button
                      type="button"
                      className={
                        "drt-btn work-product-quick-queue__now" +
                        (preferredWhen === "now" ? " submit-composer__when--preferred" : "")
                      }
                      disabled={!canSubmit}
                      title="Generate and enqueue now"
                      onClick={() => void submit("now")}
                    >
                      {busy && preferredWhen === "now" ? "Submitting…" : "Now"}
                    </button>
                    <button
                      type="button"
                      className={
                        "drt-btn work-product-quick-queue__later" +
                        (preferredWhen === "later" ? " submit-composer__when--preferred" : "")
                      }
                      disabled={!canSubmit}
                      title="Generate job for later / hourly"
                      onClick={() => void submit("later")}
                    >
                      {busy && preferredWhen === "later" ? "Submitting…" : "Later"}
                    </button>
                  </div>
                  <div className="work-product-quick-queue__families">
                    {familySelect(
                      i2vFamily,
                      setI2vFamily,
                      "I2V family",
                      "Still → video origin family (Kneel / FaceBlast / Bounce…)",
                      i2vFamilyOpts,
                    )}
                  </div>
                </div>
              ) : (
              <div className="work-product-quick-queue" role="group" aria-label="Submit advance">
                <div className="work-product-quick-queue__row">
                  <span className="work-product-quick-queue__label" title="Advance routes to create from this Use">
                    Advance
                  </span>
                  <label
                    className="work-product-quick-queue__check"
                    title="Extend — chain this media as the next source_video"
                  >
                    <input
                      type="checkbox"
                      checked={extendOn}
                      disabled={busy}
                      onChange={(e) => setExtendOn(e.target.checked)}
                    />
                    Extend
                  </label>
                  <label
                    className="work-product-quick-queue__check"
                    title="Vary — same bindings (exact replay style)"
                  >
                    <input
                      type="checkbox"
                      checked={varyOn}
                      disabled={busy}
                      onChange={(e) => setVaryOn(e.target.checked)}
                    />
                    Vary
                  </label>
                  <label
                    className="work-product-quick-queue__check"
                    title="Derive — new combo from this seed (rewire prompt and/or source)"
                  >
                    <input
                      type="checkbox"
                      checked={deriveOn}
                      disabled={busy}
                      onChange={(e) => setDeriveOn(e.target.checked)}
                    />
                    Derive
                  </label>
                  <span className="work-product-quick-queue__sep" aria-hidden="true" />
                  <button
                    type="button"
                    className={
                      "drt-btn work-product-quick-queue__now" +
                      (preferredWhen === "now" ? " submit-composer__when--preferred" : "")
                    }
                    disabled={!canSubmit}
                    title="Commit checked routes at front of queue and enqueue now"
                    onClick={() => void submit("now")}
                  >
                    {busy && preferredWhen === "now" ? "Submitting…" : "Now"}
                  </button>
                  <button
                    type="button"
                    className={
                      "drt-btn work-product-quick-queue__later" +
                      (preferredWhen === "later" ? " submit-composer__when--preferred" : "")
                    }
                    disabled={!canSubmit}
                    title="Commit checked routes at normal priority"
                    onClick={() => void submit("later")}
                  >
                    {busy && preferredWhen === "later" ? "Submitting…" : "Later"}
                  </button>
                </div>
                {anyRoute ? (
                  <div className="work-product-quick-queue__families">
                    {extendOn
                      ? familySelect(
                          extendFamily,
                          setExtendFamily,
                          "Extend",
                          "Family whose shape runs this Extend (video source_video)",
                          extendFamilyOpts,
                        )
                      : null}
                    {varyOn
                      ? familySelect(
                          varyFamily,
                          setVaryFamily,
                          "Vary",
                          "Family whose shape runs this Vary",
                          varyFamilyRows.length ? varyFamilyRows : familyOpts,
                        )
                      : null}
                    {deriveOn
                      ? familySelect(
                          deriveFamily,
                          setDeriveFamily,
                          "Derive",
                          "Family whose shape runs this Derive",
                          deriveFamilyOpts,
                        )
                      : null}
                  </div>
                ) : (
                  <p className="work-product-quick-queue__hint">Select Extend, Vary, and/or Derive</p>
                )}
                {extendOn && identityNeeded ? (
                  <div className="work-product-identity-still" aria-label="Identity still">
                    <div className="work-product-identity-still__head">
                      <span className="work-product-quick-queue__label">Identity</span>
                      {identityLoading ? <span className="work-product-quick-queue__hint">Loading…</span> : null}
                      {!identityLoading && identitySelectedPath ? (
                        <span className="work-product-quick-queue__hint" title={identitySelectedPath}>
                          selected
                        </span>
                      ) : null}
                      {!identityLoading && !identitySelectedPath ? (
                        <span className="work-product-quick-queue__hint">pick or mint a still</span>
                      ) : null}
                    </div>
                    {identityCandidates.length ? (
                      <div className="work-product-identity-still__strip" role="listbox">
                        {identityCandidates.slice(0, 8).map((c) => {
                          const selected = identitySelectedId === c.id || identitySelectedPath === c.path;
                          return (
                            <IdentityStillThumbButton
                              key={c.id || c.path}
                              candidate={c}
                              selected={selected}
                              disabled={busy}
                              onSelect={() => {
                                setIdentitySelectedPath(c.path);
                                setIdentitySelectedId(c.id);
                              }}
                            />
                          );
                        })}
                      </div>
                    ) : (
                      <p className="work-product-quick-queue__hint">No identity still yet — mint a frame or pick another family.</p>
                    )}
                    {identityMintTargets.length ? (
                      <div className="work-product-identity-still__mints">
                        {identityMintTargets.slice(0, 3).map((t) => (
                          <button
                            key={`${t.video_relpath || t.video_path}-${t.lineage_depth}`}
                            type="button"
                            className="drt-btn work-product-identity-still__mint"
                            disabled={busy || identityMintBusy}
                            onClick={() => void mintIdentity(t)}
                          >
                            {identityMintBusy ? "Minting…" : t.label || "First frame"}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
              )}
                <SubmitConstructionPreview
                  routes={constructionPreview.routes}
                  useLabel={constructionPreview.useLabel}
                  useWindow={constructionPreview.useWindow}
                  vhs={constructionPreview.vhs}
                  vhsWarning={constructionPreview.vhsWarning}
                  identity={constructionPreview.identity}
                  preferredWhen={preferredWhen}
                  origin={intent.origin}
                  fromJob={intent.fromJob}
                  ready={constructionPreview.ready}
                />
                {submitError ? <SubmitQueueErrorPanel error={submitError} /> : null}
                {msg ? (
                  <p className="work-product-quick-queue__msg work-product-quick-queue__msg--ok" title={msg}>
                    {msg}
                  </p>
                ) : null}
                <RecentSubmitsPanel items={recentSubmitsQuery.data?.items || []} />
                {lastJobKey ? (
                  <div className="submit-composer__links">
                    {isModal && onClose ? (
                      <button type="button" className="drt-btn" onClick={onClose}>
                        Done
                      </button>
                    ) : null}
                    <a className="drt-btn" href={workbenchHref({ jobKey: lastJobKey })}>
                      Open in Workbench
                    </a>
                    <a className="drt-btn" href={queueHref({ jobKey: lastJobKey })}>
                      Open Queue
                    </a>
                    {!isModal && originBack ? (
                      <a className="drt-btn" href={originBack.href}>
                        {originBack.label}
                      </a>
                    ) : null}
                  </div>
                ) : null}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
