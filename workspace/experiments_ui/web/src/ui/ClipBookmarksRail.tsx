import React, { useCallback, useEffect, useState } from "react";
import { mutateShapeFactoryClip, type ShapeFactoryClip } from "./api";
import { loadClipsForMedia, peekClipsForMedia } from "./shapeFactorySessionCache";

export function formatClipTimecode(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const s = Math.floor(sec % 60);
  const m = Math.floor(sec / 60) % 60;
  const h = Math.floor(sec / 3600);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
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
  onUseForExtend,
  className,
}: ClipBookmarksRailProps) {
  const [clips, setClips] = useState<ShapeFactoryClip[]>([]);
  const [defaultId, setDefaultId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
    void reload();
  }, [reload]);

  useEffect(() => {
    if (!selectedClipId) return;
    if (!clips.some((c) => c.clip_id === selectedClipId)) {
      onSelectClip?.(null);
    }
  }, [clips, selectedClipId, onSelectClip]);

  if (!mediaRelpath) return null;

  const canSave =
    trimEditable &&
    markIn != null &&
    markOut != null &&
    Number.isFinite(markIn) &&
    Number.isFinite(markOut) &&
    markOut > markIn + 0.05;

  const selected = selectedClipId ? clips.find((c) => c.clip_id === selectedClipId) || null : null;

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
          <button
            type="button"
            disabled={!canSave || busy}
            onClick={() => {
              if (!canSave || markIn == null || markOut == null) return;
              setBusy(true);
              void mutateShapeFactoryClip({
                op: "create",
                media_relpath: mediaRelpath,
                mark_in: markIn,
                mark_out: markOut,
                label: "Clip",
                origin,
              })
                .then((res) => {
                  void reload({ force: true });
                  if (res.clip) onSelectClip?.(res.clip);
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
            onClick={() => {
              if (!canSave || markIn == null || markOut == null) return;
              setBusy(true);
              void mutateShapeFactoryClip({
                op: "create",
                media_relpath: mediaRelpath,
                mark_in: markIn,
                mark_out: markOut,
                label: "Default",
                origin,
                set_default: true,
              })
                .then((res) => {
                  void reload({ force: true });
                  if (res.clip) onSelectClip?.(res.clip);
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
