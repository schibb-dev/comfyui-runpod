import React, { useEffect, useRef, useState } from "react";

export type MediaCompareSide = {
  id: string;
  label: string;
  url?: string | null;
  kind?: "video" | "image" | "auto";
};

export type MediaCompareMode = "pair" | "slide";

export type MediaCompareStageProps = {
  sideA: MediaCompareSide;
  sideB: MediaCompareSide;
  mode?: MediaCompareMode;
  onModeChange?: (mode: MediaCompareMode) => void;
  className?: string;
};

function inferKind(url: string | null | undefined, kind?: string): "video" | "image" {
  if (kind === "video" || kind === "image") return kind;
  const u = String(url || "").toLowerCase();
  if (/\.(mp4|webm|mov)(\?|$)/.test(u)) return "video";
  return "image";
}

function MediaPane({
  side,
  active,
  syncRef,
  onTimeUpdate,
}: {
  side: MediaCompareSide;
  active: boolean;
  syncRef?: React.MutableRefObject<HTMLVideoElement | null>;
  onTimeUpdate?: (t: number) => void;
}) {
  const url = side.url || "";
  const kind = inferKind(url, side.kind);
  if (!url) {
    return (
      <div className={`media-compare-pane ${active ? "ab-active" : "ab-inactive"}`}>
        <div className="media-compare-empty">No media</div>
      </div>
    );
  }
  if (kind === "video") {
    return (
      <div className={`media-compare-pane ${active ? "ab-active" : "ab-inactive"}`}>
        <video
          ref={(el) => {
            if (syncRef) syncRef.current = el;
          }}
          className="media-compare-media"
          src={url}
          controls={active}
          muted={!active}
          playsInline
          loop
          onTimeUpdate={(e) => onTimeUpdate?.(e.currentTarget.currentTime)}
        />
      </div>
    );
  }
  return (
    <div className={`media-compare-pane ${active ? "ab-active" : "ab-inactive"}`}>
      <img className="media-compare-media" src={url} alt={side.label} />
    </div>
  );
}

/**
 * Reusable Pair / Slide A-B compare stage (lifted from /experiments patterns).
 * Caller owns data + judgment; this only renders two media sides.
 */
export function MediaCompareStage({
  sideA,
  sideB,
  mode: modeProp,
  onModeChange,
  className,
}: MediaCompareStageProps) {
  const [mode, setMode] = useState<MediaCompareMode>(modeProp || "slide");
  const [slideSlot, setSlideSlot] = useState<"A" | "B">("A");
  const [syncOn, setSyncOn] = useState(true);
  const videoA = useRef<HTMLVideoElement | null>(null);
  const videoB = useRef<HTMLVideoElement | null>(null);
  const syncLock = useRef(false);

  useEffect(() => {
    if (modeProp && modeProp !== mode) setMode(modeProp);
  }, [modeProp, mode]);

  function changeMode(next: MediaCompareMode) {
    setMode(next);
    onModeChange?.(next);
  }

  function syncFrom(source: "A" | "B", t: number) {
    if (!syncOn || syncLock.current) return;
    const other = source === "A" ? videoB.current : videoA.current;
    if (!other || !Number.isFinite(t)) return;
    if (Math.abs(other.currentTime - t) < 0.12) return;
    syncLock.current = true;
    try {
      other.currentTime = t;
    } catch {
      /* ignore */
    }
    window.setTimeout(() => {
      syncLock.current = false;
    }, 50);
  }

  const pair = mode === "pair";

  return (
    <div className={`media-compare-stage ${className || ""}`.trim()}>
      <div className="media-compare-toolbar">
        <div className="segmented" role="radiogroup" aria-label="Compare mode">
          <button
            type="button"
            className={`seg-btn ${mode === "slide" ? "active" : ""}`}
            onClick={() => changeMode("slide")}
          >
            Slide
          </button>
          <button
            type="button"
            className={`seg-btn ${mode === "pair" ? "active" : ""}`}
            onClick={() => changeMode("pair")}
          >
            Pair
          </button>
        </div>
        {mode === "slide" ? (
          <div className="segmented" role="radiogroup" aria-label="A/B view">
            <button
              type="button"
              className={`seg-btn ${slideSlot === "A" ? "active" : ""}`}
              onClick={() => setSlideSlot("A")}
            >
              A · {sideA.label}
            </button>
            <button
              type="button"
              className={`seg-btn ${slideSlot === "B" ? "active" : ""}`}
              onClick={() => setSlideSlot("B")}
            >
              B · {sideB.label}
            </button>
          </div>
        ) : (
          <label className="media-compare-sync">
            <input type="checkbox" checked={syncOn} onChange={(e) => setSyncOn(e.target.checked)} />
            Sync playback
          </label>
        )}
      </div>

      {pair ? (
        <div className="pair-stage media-compare-pair">
          <div className="pair-card">
            <div className="media-compare-label">{sideA.label}</div>
            <MediaPane
              side={sideA}
              active
              syncRef={videoA}
              onTimeUpdate={(t) => syncFrom("A", t)}
            />
          </div>
          <div className="pair-card">
            <div className="media-compare-label">{sideB.label}</div>
            <MediaPane
              side={sideB}
              active
              syncRef={videoB}
              onTimeUpdate={(t) => syncFrom("B", t)}
            />
          </div>
        </div>
      ) : (
        <div className="media-compare-slide">
          <MediaPane
            side={sideA}
            active={slideSlot === "A"}
            syncRef={videoA}
            onTimeUpdate={(t) => syncFrom("A", t)}
          />
          <MediaPane
            side={sideB}
            active={slideSlot === "B"}
            syncRef={videoB}
            onTimeUpdate={(t) => syncFrom("B", t)}
          />
        </div>
      )}
    </div>
  );
}
