/** Workbench list sections as URL paths (navigational state, not only React state). */

import type { WorkProductNavSectionId } from "./workProductListSort";

const PREFIXES = ["/workbench", "/work-products"] as const;

const SLUG_TO_SECTION: Record<string, WorkProductNavSectionId> = {
  live: "live",
  pending: "pending",
  error: "error",
  errors: "error",
  done: "done",
};

const SECTION_TO_SLUG: Record<WorkProductNavSectionId, string> = {
  live: "live",
  pending: "pending",
  error: "errors",
  done: "done",
};

function normalizePath(pathname: string): string {
  const p = (pathname || "/").replace(/\/+$/, "") || "/";
  return p;
}

export function parseWorkbenchSection(pathname: string = window.location.pathname): WorkProductNavSectionId | null {
  const path = normalizePath(pathname);
  for (const prefix of PREFIXES) {
    if (path === prefix) return null;
    if (!path.startsWith(`${prefix}/`)) continue;
    const slug = decodeURIComponent(path.slice(prefix.length + 1).split("/")[0] || "")
      .trim()
      .toLowerCase();
    return SLUG_TO_SECTION[slug] ?? null;
  }
  return null;
}

export function workbenchSectionPath(section: WorkProductNavSectionId | null): string {
  if (!section) return "/workbench";
  return `/workbench/${SECTION_TO_SLUG[section]}`;
}

/** Keep query + hash; point the path at a Workbench section (or the index). */
export function workbenchSectionHref(
  section: WorkProductNavSectionId | null,
  search: string = window.location.search,
  hash: string = window.location.hash,
): string {
  return `${workbenchSectionPath(section)}${search || ""}${hash || ""}`;
}

export function workbenchSectionIsActive(
  section: WorkProductNavSectionId,
  pathname: string = window.location.pathname,
): boolean {
  return parseWorkbenchSection(pathname) === section;
}

/** Opening a section addresses it. Closing the addressed section returns to the index. */
export function sectionAfterToggle(
  current: WorkProductNavSectionId | null,
  id: WorkProductNavSectionId,
  open: boolean,
): WorkProductNavSectionId | null {
  if (open) return id;
  return current === id ? null : current;
}

/** Address-bar update only. Does not dispatch popstate (that remounts the screen). */
export const CLIENT_PATH_EVENT = "experiments-ui-path";

export function replaceWorkbenchSection(section: WorkProductNavSectionId | null): void {
  const next = workbenchSectionHref(section);
  const now = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next === now) return;
  window.history.replaceState(null, "", next);
  window.dispatchEvent(new Event(CLIENT_PATH_EVENT));
}
