import type { InputCurationStillItem } from "./types";

export type StillGalleryViewMode = "focus" | "strip" | "grid" | "deck";

export const STILL_GALLERY_VIEW_KEY = "still-gallery.view";

export function loadStillGalleryViewPreference(narrow: boolean): StillGalleryViewMode {
  try {
    const raw = localStorage.getItem(STILL_GALLERY_VIEW_KEY);
    if (raw === "focus" || raw === "strip" || raw === "grid" || raw === "deck") return raw;
  } catch {
    /* ignore */
  }
  return narrow ? "focus" : "grid";
}

export function persistStillGalleryViewPreference(view: StillGalleryViewMode) {
  try {
    localStorage.setItem(STILL_GALLERY_VIEW_KEY, view);
  } catch {
    /* ignore */
  }
}

export function stillGalleryItemPath(it: InputCurationStillItem): string {
  return String(it.path || "").trim();
}

export function stillGalleryItemUrl(it: InputCurationStillItem): string | null {
  return it.thumb_url || it.url || null;
}

export type StillMediaLoadMode = "eager" | "lazy" | "off";

/** First grid tiles decode immediately; the rest wait for the viewport. */
export const GRID_EAGER_THUMBS = 12;

/**
 * Focus / swipe list: decode the current still and its neighbors.
 * Farther cards stay empty so a long gallery does not fetch dozens of JPEGs.
 */
export function stillFocusMediaLoadMode(index: number, focusIndex: number): StillMediaLoadMode {
  const focus = focusIndex >= 0 ? focusIndex : 0;
  const dist = Math.abs(index - focus);
  if (dist <= 1) return "eager";
  if (dist <= 2) return "lazy";
  return "off";
}
