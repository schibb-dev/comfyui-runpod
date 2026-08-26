/** Shared extend-family defaults for Submit compose. */

import type { WorkProductFamilyOption } from "./types";

export const PREFERRED_EXTEND_FAMILIES = ["FB9_GEX2", "FB9_GEX_FACIAL", "FB9_GEX"] as const;

/** Slug-only gate (I2V/still families are not video Extend targets). */
export function isExtendFamilySlug(slug: string): boolean {
  const s = String(slug || "").trim();
  return Boolean(s);
}

/**
 * Families that can run Extend on a video Use (need a source_video / V2V contract).
 * I2V / still-only shapes belong in still doors, not video Extend.
 * GEX2 identity-anchor is a V2V extend with an extra still slot — keep it listed.
 */
export function isExtendFamilyOption(f: WorkProductFamilyOption): boolean {
  const slug = String(f.slug || "").trim();
  if (!isExtendFamilySlug(slug)) return false;
  const sid = String(f.shape_id || "").toLowerCase();
  if (!sid) return true;
  if (sid.includes("i2v") || sid.includes("still")) return false;
  return (
    sid.includes("v2v") ||
    sid.includes("facial") ||
    sid.includes("source") ||
    sid.includes("identity_anchor")
  );
}

export function pickDefaultExtendFamily(
  families: WorkProductFamilyOption[],
  extendDefaults: Record<string, string>,
  hintFamily?: string | null,
  mediaRelpath?: string | null,
): string {
  const extendable = families.filter(isExtendFamilyOption);
  const slugs = (extendable.length ? extendable : families).map((f) => f.slug).filter(Boolean);
  const has = (slug: string) => slugs.includes(slug);

  const hint = String(hintFamily || "").trim();
  if (hint && extendDefaults[hint] && has(extendDefaults[hint])) return extendDefaults[hint];
  if (extendDefaults["*"] && has(extendDefaults["*"])) return extendDefaults["*"];
  if (hint && has(hint) && isExtendFamilySlug(hint)) return hint;

  const base =
    String(mediaRelpath || "")
      .replace(/\\/g, "/")
      .split("/")
      .pop()
      ?.toUpperCase() || "";
  if (base.includes("GEX2_FACIAL") || base.includes("GEX_FACIAL")) {
    if (has("FB9_GEX_FACIAL")) return "FB9_GEX_FACIAL";
  }
  if ((base.includes("BOUNCE") || base.includes("DANCEA")) && has("FB9_GEX")) return "FB9_GEX";
  if ((base.includes("FACEBLAST") || base.includes("FACE_BLAST")) && has("FB9_GEX")) return "FB9_GEX";
  if (base.includes("GEX2") && has("FB9_GEX2")) return "FB9_GEX2";
  if (base.includes("GEX") && has("FB9_GEX")) return "FB9_GEX";
  // Image-started / Kneel OG clips → first V2V hop is FB9_GEX (not GEX2).
  if ((base.includes("KNEEL") || base.includes("X-KNEEL")) && has("FB9_GEX")) return "FB9_GEX";
  if ((base.includes("KNEEL") || base.includes("X-KNEEL")) && has("FB9_GEX2")) return "FB9_GEX2";

  for (const pref of PREFERRED_EXTEND_FAMILIES) {
    if (has(pref)) return pref;
  }
  const first = slugs.find(isExtendFamilySlug);
  return first || slugs[0] || PREFERRED_EXTEND_FAMILIES[0];
}
