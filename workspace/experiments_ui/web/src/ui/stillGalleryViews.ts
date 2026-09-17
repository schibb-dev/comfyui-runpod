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
