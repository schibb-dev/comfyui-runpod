import React from "react";
import type { ShapeFactoryClip } from "./api";
import { submitHref } from "./discoveryDeepLink";
import type { DiscoveryLibraryItem } from "./types";

/**
 * Thin door into `/submit` — compose lives on the Submit screen, not inline here.
 */
export function DiscoveryQueueFromClip({
  item,
  mediaRelpath,
  markIn,
  markOut,
  activeClip,
  origin = "library",
}: {
  item: DiscoveryLibraryItem;
  mediaRelpath: string | null;
  markIn: number | null;
  markOut: number | null;
  duration?: number;
  fps?: number;
  activeClip: ShapeFactoryClip | null;
  origin?: string;
}) {
  const relpath = String(mediaRelpath || item.video_relpath || item.relpath || "").trim();
  if (!relpath) return null;

  const windowOk =
    markIn != null &&
    markOut != null &&
    Number.isFinite(markIn) &&
    Number.isFinite(markOut) &&
    markOut > markIn + 0.05;

  const href = submitHref({
    mediaRelpath: relpath,
    clipId: activeClip?.clip_id,
    markIn: windowOk ? markIn : null,
    markOut: windowOk ? markOut : null,
    origin,
  });

  const status = activeClip
    ? activeClip.label || "Clip selected"
    : windowOk
      ? "Scrubber window"
      : "Select a clip or set marks";

  return (
    <div className="discovery-queue-from-clip" aria-label="Open Submit from clip">
      <a
        className={"drt-btn discovery-queue-from-clip__cta" + (windowOk ? "" : " discovery-queue-from-clip__cta--disabled")}
        href={href}
        aria-disabled={!windowOk}
        title={status}
        onClick={(e) => {
          if (!windowOk) e.preventDefault();
        }}
      >
        Open in Submit
      </a>
      <span className="discovery-queue-from-clip__status factory-muted">{status}</span>
    </div>
  );
}
