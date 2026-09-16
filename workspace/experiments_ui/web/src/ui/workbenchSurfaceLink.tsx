import React from "react";
import { workbenchHref, workbenchHrefForMedia } from "./discoveryDeepLink";

export type WorkbenchSurfaceTarget = {
  relpath?: string | null;
  jobKey?: string | null;
  promptId?: string | null;
  name?: string | null;
};

/** Prefer job/prompt identity; fall back to media focus on Workbench. */
export function resolveWorkbenchSurfaceHref(target: WorkbenchSurfaceTarget): string | null {
  const jobKey = String(target.jobKey || "").trim();
  const promptId = String(target.promptId || "").trim();
  const relpath = String(target.relpath || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  if (jobKey || promptId) {
    return workbenchHref({
      jobKey: jobKey || null,
      promptId: promptId || null,
      media: relpath || null,
    });
  }
  if (relpath) return workbenchHrefForMedia({ relpath, name: target.name });
  return null;
}

/** Toolbar / action-row Workbench link (Library-adjacent). */
export function WorkbenchSurfaceLink({
  relpath,
  jobKey,
  promptId,
  name,
  className = "drt-btn",
  label = "Workbench",
  title = "Open related factory jobs in Workbench",
}: WorkbenchSurfaceTarget & {
  className?: string;
  label?: string;
  title?: string;
}) {
  const href = resolveWorkbenchSurfaceHref({ relpath, jobKey, promptId, name });
  if (!href) return null;
  return (
    <a className={className} href={href} title={title}>
      {label}
    </a>
  );
}

/** Lower-left overlay on preview frames (appetite badge stays upper-right). */
export function WorkbenchPreviewLink({
  relpath,
  jobKey,
  promptId,
  name,
  size = "default",
  className,
}: WorkbenchSurfaceTarget & {
  size?: "default" | "sm";
  className?: string;
}) {
  const href = resolveWorkbenchSurfaceHref({ relpath, jobKey, promptId, name });
  if (!href) return null;
  return (
    <a
      className={[
        "workbench-preview-link",
        size === "sm" ? "workbench-preview-link--sm" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      href={href}
      title="Open related factory jobs in Workbench"
    >
      Workbench
    </a>
  );
}
