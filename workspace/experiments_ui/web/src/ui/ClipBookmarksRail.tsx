import React, { useCallback, useEffect, useRef, useState } from "react";
import { mutateShapeFactoryClip, type ShapeFactoryClip, type ShapeFactoryClipsListResponse } from "./api";
import { loadClipsForMedia, peekClipsForMedia } from "./shapeFactorySessionCache";

export function formatClipTimecode(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const s = Math.floor(sec % 60);
  const m = Math.floor(sec / 60) % 60;
  const h = Math.floor(sec / 3600);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

const MARK_EPS = 1e-3;
const MIN_SPAN = 0.05;

function marksEqual(a: number, b: number): boolean {
  return Math.abs(a - b) < MARK_EPS;
}

/** Null in → 0; null out → duration (full-video ends). Explicit I/O can save without duration. */
export function resolveClipMarks(
  markIn: number | null,
  markOut: number | null,
  duration: number,
): { markIn: number; markOut: number } | null {
  const explicitIn = markIn != null && Number.isFinite(markIn);
  const explicitOut = markOut != null && Number.isFinite(markOut);
  if (explicitIn && explicitOut) {
    const tin = Math.max(0, markIn as number);
    const tout = duration > 0 ? Math.min(duration, markOut as number) : (markOut as number);
    if (tout > tin + MIN_SPAN) return { markIn: tin, markOut: tout };
    return null;
  }
  if (!(duration > 0) || !Number.isFinite(duration)) return null;
  const tin = Math.max(0, explicitIn ? (markIn as number) : 0);
  const tout = Math.min(duration, explicitOut ? (markOut as number) : duration);
  if (!(tout > tin + MIN_SPAN)) return null;
  return { markIn: tin, markOut: tout };
}

export function pickDefaultClip(res: ShapeFactoryClipsListResponse | null | undefined): ShapeFactoryClip | null {
  if (!res) return null;
  const clips = res.clips || [];
  const id = String(res.default_clip_id || "").trim();
  if (id) {
    const hit = clips.find((c) => c.clip_id === id);
    if (hit) return hit;
  }
  return clips.find((c) => c.is_default) || null;
}

export type ClipBookmarksRailProps = {
  mediaRelpath: string | null;
  duration: number;
  markIn: number | null;
  markOut: number | null;
  trimEditable: boolean;
  /** When false, only show sibling chips (no save/default actions). Default true. */
  showActions?: boolean;
  origin?: "discovery" | "workbench" | "submit";
  selectedClipId?: string | null;
  onSelectClip?: (clip: ShapeFactoryClip | null) => void;
  onApplyClip: (markIn: number, markOut: number, clip?: ShapeFactoryClip) => void;
  /** Fires once per media when a default clip exists — use to seed the displayed window. */
  onDefaultClip?: (clip: ShapeFactoryClip) => void;
  onUseForExtend?: (clip: ShapeFactoryClip) => void;
  className?: string;
};

export function ClipBookmarksRail({
  mediaRelpath,
  duration,
  markIn,
  markOut,
  trimEditable,
  showActions = true,
  origin = "workbench",
  selectedClipId = null,
  onSelectClip,
  onApplyClip,
  onDefaultClip,
  onUseForExtend,
  className,
}: ClipBookmarksRailProps) {
  const [clips, setClips] = useState<ShapeFactoryClip[]>([]);
  const [defaultId, setDefaultId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const defaultAnnouncedRef = useRef<string | null>(null);

  const applyClipsList = useCallback((res: { clips?: ShapeFactoryClip[]; default_clip_id?: string | null }) => {
    setClips(res.clips || []);
    setDefaultId(res.default_clip_id || null);
  }, []);

  const reload = useCallback(
    async (opts?: { force?: boolean }) => {
      if (!mediaRelpath) {
        setClips([]);
        setDefaultId(null);
        return;
      }
      if (!opts?.force) {
        const cached = peekClipsForMedia(mediaRelpath);
        if (cached) applyClipsList(cached);
      }
      try {
        const res = await loadClipsForMedia(mediaRelpath, { force: opts?.force });
        applyClipsList(res);
        setErr(null);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      }
    },
    [applyClipsList, mediaRelpath],
  );

  useEffect(() => {
    defaultAnnouncedRef.current = null;
    void reload();
  }, [reload]);

  useEffect(() => {
    const def = pickDefaultClip({ clips, default_clip_id: defaultId, ok: true });
    if (!def || !onDefaultClip) return;
    const key = `${mediaRelpath || ""}:${def.clip_id}`;
    if (defaultAnnouncedRef.current === key) return;
    defaultAnnouncedRef.current = key;
    onDefaultClip(def);
  }, [clips, defaultId, mediaRelpath, onDefaultClip]);

  useEffect(() => {
    if (!selectedClipId) return;
    if (!clips.some((c) => c.clip_id === selectedClipId)) {
      onSelectClip?.(null);
    }
  }, [clips, selectedClipId, onSelectClip]);

  if (!mediaRelpath) return null;

  const resolved = resolveClipMarks(markIn, markOut, duration);
  const canSave = Boolean(resolved);
  const selected = selectedClipId ? clips.find((c) => c.clip_id === selectedClipId) || null : null;
  const selectedDirty = Boolean(
    selected &&
      resolved &&
      (!marksEqual(resolved.markIn, selected.mark_in_s) || !marksEqual(resolved.markOut, selected.mark_out_s)),
  );
  const identicalToSelected = Boolean(
    selected &&
      resolved &&
      marksEqual(resolved.markIn, selected.mark_in_s) &&
      marksEqual(resolved.markOut, selected.mark_out_s),
  );

  return (
    <div
      className={["work-product-viewer__clips", className].filter(Boolean).join(" ")}
      title="Clips are bookmarks on this source video"
    >
      <div className="work-product-viewer__clips-row">
        <span className="work-product-viewer__clips-label">Clips</span>
        {clips.length === 0 ? (
          <span className="factory-muted">none — save a span as a bookmark</span>
        ) : (
          clips.map((c) => {
            const isSelected = selectedClipId === c.clip_id;
            const isDefault = defaultId === c.clip_id;
            return (
              <button
                key={c.clip_id}
                type="button"
                className={
                  "work-product-viewer__clip-chip" +
                  (isDefault ? " work-product-viewer__clip-chip--default" : "") +
                  (isSelected ? " work-product-viewer__clip-chip--selected" : "")
                }
                title={`${c.label || "Clip"} · ${formatClipTimecode(c.mark_in_s)}–${formatClipTimecode(c.mark_out_s)}`}
                disabled={busy}
                onClick={() => {
                  onSelectClip?.(c);
                  onApplyClip(c.mark_in_s, c.mark_out_s, c);
                }}
              >
                {isDefault ? "★ " : ""}
                {c.label || "Clip"} {formatClipTimecode(c.mark_in_s)}–{formatClipTimecode(c.mark_out_s)}
              </button>
            );
          })
        )}
      </div>
      {showActions ? (
        <div className="work-product-viewer__clips-actions">
          {selected ? (
            <button
              type="button"
              disabled={!canSave || !selectedDirty || busy}
              title={
                selectedDirty
                  ? "Update this clip (either in or out may change)"
                  : "Change in and/or out to update this clip"
              }
              onClick={() => {
                if (!canSave || !selectedDirty || !resolved || !selected) return;
                setBusy(true);
                void mutateShapeFactoryClip({
                  op: "update",
                  clip_id: selected.clip_id,
                  mark_in: resolved.markIn,
                  mark_out: resolved.markOut,
                })
                  .then((res) => {
                    void reload({ force: true });
                    if (res.clip) {
                      onSelectClip?.(res.clip);
                      onApplyClip(res.clip.mark_in_s, res.clip.mark_out_s, res.clip);
                    }
                  })
                  .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
                  .finally(() => setBusy(false));
              }}
            >
              Update clip
            </button>
          ) : null}
          <button
            type="button"
            disabled={!canSave || identicalToSelected || busy}
            title={
              identicalToSelected
                ? "Window matches the selected clip — update it, or change marks"
                : markIn == null || markOut == null
                  ? "Missing end defaults to start of file / end of file"
                  : "Save current window as a new clip bookmark"
            }
            onClick={() => {
              if (!canSave || !resolved || identicalToSelected) return;
              setBusy(true);
              void mutateShapeFactoryClip({
                op: "create",
                media_relpath: mediaRelpath,
                mark_in: resolved.markIn,
                mark_out: resolved.markOut,
                label: "Clip",
                origin,
              })
                .then((res) => {
                  void reload({ force: true });
                  if (res.clip) {
                    onSelectClip?.(res.clip);
                    onApplyClip(res.clip.mark_in_s, res.clip.mark_out_s, res.clip);
                  }
                })
                .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
                .finally(() => setBusy(false));
            }}
          >
            Save as clip
          </button>
          <button
            type="button"
            disabled={!canSave || busy}
            title="Save current window and make it the default for this source"
            onClick={() => {
              if (!canSave || !resolved) return;
              setBusy(true);
              void mutateShapeFactoryClip({
                op: "create",
                media_relpath: mediaRelpath,
                mark_in: resolved.markIn,
                mark_out: resolved.markOut,
                label: "Default",
                origin,
                set_default: true,
              })
                .then((res) => {
                  void reload({ force: true });
                  if (res.clip) {
                    onSelectClip?.(res.clip);
                    onApplyClip(res.clip.mark_in_s, res.clip.mark_out_s, res.clip);
                  }
                })
                .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
                .finally(() => setBusy(false));
            }}
          >
            Save as default
          </button>
          {defaultId ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void mutateShapeFactoryClip({
                  op: "set_default",
                  media_relpath: mediaRelpath,
                  clip_id: null,
                })
                  .then(() => reload({ force: true }))
                  .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
                  .finally(() => setBusy(false));
              }}
            >
              Clear default
            </button>
          ) : null}
          {onUseForExtend && selected ? (
            <button
              type="button"
              disabled={busy}
              className="work-product-viewer__clip-use-extend"
              onClick={() => onUseForExtend(selected)}
            >
              Use for Extend
            </button>
          ) : null}
        </div>
      ) : null}
      {err ? <p className="work-product-viewer__trim-warn">{err}</p> : null}
    </div>
  );
}
