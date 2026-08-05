import React from "react";

/**
 * Workbench-style media preview for pipeline lists (Queue, etc.):
 * HTML5 video/image with native controls — no trim scrubber.
 */
export function PipelineMediaPlayer({
  videoUrl,
  thumbUrl,
  alt = "",
  className,
}: {
  videoUrl?: string | null;
  thumbUrl?: string | null;
  /** Kept for call-site compatibility; unused without trim transport. */
  mediaKey?: string;
  alt?: string;
  readOnly?: boolean;
  className?: string;
}) {
  if (videoUrl) {
    return (
      <div className={["work-product-viewer", "pipeline-media-player", className].filter(Boolean).join(" ")}>
        <div className="work-product-viewer__main">
          <video
            className="work-product-viewer__video"
            src={videoUrl}
            poster={thumbUrl || undefined}
            controls
            playsInline
            muted
            preload="metadata"
          />
        </div>
      </div>
    );
  }

  if (thumbUrl) {
    return (
      <div className={["work-product-viewer", "pipeline-media-player", className].filter(Boolean).join(" ")}>
        <div className="work-product-viewer__main">
          <img className="work-product-viewer__img" src={thumbUrl} alt={alt} />
        </div>
      </div>
    );
  }

  return (
    <div className={["work-product-viewer", "pipeline-media-player", className].filter(Boolean).join(" ")}>
      <div className="work-product-viewer__empty">No preview</div>
    </div>
  );
}
