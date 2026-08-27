import React from "react";
import type { ShapeFactoryClip } from "./api";
import { submitHref } from "./discoveryDeepLink";
import { isStillMediaPath } from "./submitFamily";
import type { DiscoveryLibraryItem } from "./types";

/**
 * Thin door into `/submit` — compose lives on the Submit screen, not inline here.
 * Videos need a clip or scrubber window; stills open immediately (I2V seed on Submit).
 */
export function DiscoveryQueueFromClip({
  item,
  mediaRelpath,
  markIn,
  markOut,
  activeClip,
  origin = "library",
  family,
}: {
  item: DiscoveryLibraryItem;
  mediaRelpath: string | null;
  markIn: number | null;
  markOut: number | null;
  duration?: number;
  fps?: number;
  activeClip: ShapeFactoryClip | null;
  origin?: string;
  /** Optional I2V / extend family hint for Submit. */
  family?: string | null;
}) {
  const relpath = String(mediaRelpath || item.video_relpath || item.relpath || "").trim();
  if (!relpath) return null;

  const still = isStillMediaPath(relpath) || (!item.video_url && isStillMediaPath(item.relpath));
  let mediaForSubmit = still ? String(item.relpath || relpath).trim() : relpath;
  if (still && mediaForSubmit && !mediaForSubmit.includes("/") && !mediaForSubmit.startsWith("input/")) {
    mediaForSubmit = `input/${mediaForSubmit}`;
  }
  if (still && item.library === "input" && mediaForSubmit && !mediaForSubmit.toLowerCase().startsWith("input/")) {
    const bn = mediaForSubmit.split("/").pop() || mediaForSubmit;
    mediaForSubmit = `input/${bn}`;
  }

  const windowOk =
    markIn != null &&
    markOut != null &&
    Number.isFinite(markIn) &&
    Number.isFinite(markOut) &&
    markOut > markIn + 0.05;

  const canOpen = still || windowOk || Boolean(activeClip?.clip_id);

  const href = submitHref({
    mediaRelpath: mediaForSubmit,
    clipId: still ? null : activeClip?.clip_id,
    markIn: still ? null : windowOk ? markIn : null,
    markOut: still ? null : windowOk ? markOut : null,
    family: family || null,
    origin,
  });

  const status = still
    ? "Still → Submit (pick I2V family)"
    : activeClip
      ? activeClip.label || "Clip selected"
      : windowOk
        ? "Scrubber window"
        : "Select a clip or set marks";

  return (
    <div className="discovery-queue-from-clip" aria-label={still ? "Open Submit from still" : "Open Submit from clip"}>
      <a
        className={"drt-btn discovery-queue-from-clip__cta" + (canOpen ? "" : " discovery-queue-from-clip__cta--disabled")}
        href={href}
        aria-disabled={!canOpen}
        title={status}
        onClick={(e) => {
          if (!canOpen) e.preventDefault();
        }}
      >
        Open in Submit
      </a>
      <span className="discovery-queue-from-clip__status factory-muted">{status}</span>
    </div>
  );
}
