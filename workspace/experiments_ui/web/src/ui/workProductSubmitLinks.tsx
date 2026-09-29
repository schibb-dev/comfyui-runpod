import React from "react";
import { discoveryLibraryHref, submitHref, workbenchHref } from "./discoveryDeepLink";
import { isStillMediaPath } from "./submitFamily";

/**
 * Workproduct action chrome: hide Open-in-Library for now.
 * Flip to true to re-enable Library as a next-step door from Queue / peers.
 * `/discovery` browse nav stays regardless.
 */
export const SHOW_LIBRARY_WORKPRODUCT_ACTION = false;

export function normalizeWorkProductRelpath(path?: string | null): string | null {
  const rel = String(path || "")
    .trim()
    .replace(/^\/+/, "")
    .replace(/\\/g, "/");
  return rel || null;
}

/** Output media for Submit advance (not input fallback). */
export function workProductOutputRelpath(opts: {
  primaryVideoRelpath?: string | null;
  primaryImageRelpath?: string | null;
  outputRelpath?: string | null;
}): string | null {
  return (
    normalizeWorkProductRelpath(opts.outputRelpath) ||
    normalizeWorkProductRelpath(opts.primaryVideoRelpath) ||
    normalizeWorkProductRelpath(opts.primaryImageRelpath)
  );
}

export type WorkProductSubmitDoorOpts = {
  mediaRelpath?: string | null;
  fromJob?: string | null;
  family?: string | null;
  origin?: string | null;
  markIn?: number | null;
  markOut?: number | null;
  clipId?: string | null;
  step?: string | null;
};

/** Submit with this workproduct's *output* as the subject (Extend / Vary / Derive). */
export function submitOutputHref(opts: WorkProductSubmitDoorOpts): string | null {
  const media = normalizeWorkProductRelpath(opts.mediaRelpath);
  if (!media) return null;
  return submitHref({
    mediaRelpath: media,
    fromJob: opts.fromJob || null,
    family: opts.family || null,
    markIn: opts.markIn ?? null,
    markOut: opts.markOut ?? null,
    clipId: opts.clipId || null,
    step: opts.step || "advance.extend",
    origin: opts.origin || null,
  });
}

/**
 * Submit with this workproduct's *input* as the subject.
 * Stills → I2V (no step); videos → advance.extend. Does not pass from_job.
 */
export function submitInputHref(opts: WorkProductSubmitDoorOpts): string | null {
  const media = normalizeWorkProductRelpath(opts.mediaRelpath);
  if (!media) return null;
  const still = isStillMediaPath(media);
  return submitHref({
    mediaRelpath: media,
    family: opts.family || null,
    markIn: still ? null : opts.markIn ?? null,
    markOut: still ? null : opts.markOut ?? null,
    clipId: still ? null : opts.clipId || null,
    step: still ? null : opts.step || "advance.extend",
    origin: opts.origin || null,
  });
}

export type WorkProductSubmitActionLinksProps = {
  outputRelpath?: string | null;
  inputRelpath?: string | null;
  /** Preferred Library target when the stub flag is on (usually output, else input). */
  libraryRelpath?: string | null;
  jobKey?: string | null;
  promptId?: string | null;
  family?: string | null;
  origin?: string | null;
  className?: string;
  linkClassName?: string;
  showWorkbench?: boolean;
};

/**
 * Shared Submit output / Submit input (+ Workbench) links for workproduct action chrome.
 * Library link is gated by SHOW_LIBRARY_WORKPRODUCT_ACTION.
 */
export function WorkProductSubmitActionLinks({
  outputRelpath,
  inputRelpath,
  libraryRelpath,
  jobKey,
  promptId,
  family,
  origin = "queue",
  className,
  linkClassName = "pipeline-row__link",
  showWorkbench = true,
}: WorkProductSubmitActionLinksProps) {
  const outHref = submitOutputHref({
    mediaRelpath: outputRelpath,
    fromJob: jobKey,
    family,
    origin,
  });
  const inHref = submitInputHref({
    mediaRelpath: inputRelpath,
    family,
    origin,
  });
  const workbenchUrl = workbenchHref({ jobKey: jobKey || null, promptId: promptId || null });
  const libRel =
    normalizeWorkProductRelpath(libraryRelpath) ||
    normalizeWorkProductRelpath(outputRelpath) ||
    normalizeWorkProductRelpath(inputRelpath);

  return (
    <span className={className} style={{ display: "contents" }} role="group" aria-label="Submit doors">
      {outHref ? (
        <a
          className={linkClassName}
          href={outHref}
          title="Open Submit with this output (Extend / Vary / Derive)"
        >
          Submit output
        </a>
      ) : null}
      {inHref ? (
        <a
          className={linkClassName}
          href={inHref}
          title={
            isStillMediaPath(inputRelpath || "")
              ? "Open Submit with this input still (I2V)"
              : "Open Submit with this input video (Extend / Vary)"
          }
        >
          Submit input
        </a>
      ) : null}
      {showWorkbench ? (
        <a
          className={linkClassName}
          href={workbenchUrl}
          title={
            jobKey
              ? `Open ${jobKey} in Workbench`
              : promptId
                ? `Find prompt ${promptId} in Workbench`
                : "Open Workbench"
          }
        >
          Workbench
        </a>
      ) : null}
      {SHOW_LIBRARY_WORKPRODUCT_ACTION && libRel ? (
        <a className={linkClassName} href={discoveryLibraryHref(libRel)} title="Open in Library">
          Open in Library
        </a>
      ) : null}
    </span>
  );
}
