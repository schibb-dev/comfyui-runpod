import React, { useEffect } from "react";
import { createPortal } from "react-dom";

export type MediaFullscreenPayload = {
  kind: "video" | "image";
  url: string;
  title?: string;
};

export function MediaFullscreenModal({
  media,
  onClose,
}: {
  media: MediaFullscreenPayload | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!media) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopImmediatePropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [media, onClose]);

  if (!media) return null;

  return createPortal(
    <div
      className="modal-overlay sfmap-media-modal-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={media.title || "Media viewer"}
    >
      <div className="modal sfmap-media-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{media.title || "Media"}</div>
          <div className="modal-actions">
            <a href={media.url} target="_blank" rel="noreferrer">
              <button type="button">Open in tab</button>
            </a>
            <button type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
        <div className="modal-body">
          {media.kind === "video" ? (
            <video
              className="modal-media fit-contain"
              controls
              autoPlay
              playsInline
              preload="metadata"
              src={media.url}
            />
          ) : (
            <img className="modal-media fit-contain" alt={media.title || ""} src={media.url} />
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
