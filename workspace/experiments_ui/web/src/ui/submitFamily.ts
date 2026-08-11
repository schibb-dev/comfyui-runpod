/** Shared extend-family defaults for Submit compose. */

import type { WorkProductFamilyOption } from "./types";

export const PREFERRED_EXTEND_FAMILIES = ["FB9_GEX2", "FB9_GEX_FACIAL", "FB9_GEX"] as const;

export function isExtendFamilySlug(slug: string): boolean {
  const s = String(slug || "").trim();
  if (!s) return false;
  if (/identity/i.test(s)) return false;
  return true;
}

export function pickDefaultExtendFamily(
  families: WorkProductFamilyOption[],
  extendDefaults: Record<string, string>,
  hintFamily?: string | null,
  mediaRelpath?: string | null,
): string {
  const slugs = families.map((f) => f.slug).filter(Boolean);
  const has = (slug: string) => slugs.includes(slug);

  const hint = String(hintFamily || "").trim();
  if (hint && extendDefaults[hint] && has(extendDefaults[hint])) return extendDefaults[hint];
  if (hint && has(hint) && isExtendFamilySlug(hint)) return hint;

  for (const succ of Object.values(extendDefaults || {})) {
    if (succ && has(succ) && isExtendFamilySlug(succ)) return succ;
  }

  const base =
    String(mediaRelpath || "")
      .replace(/\\/g, "/")
      .split("/")
      .pop()
      ?.toUpperCase() || "";
  if (base.includes("GEX2_FACIAL") || base.includes("GEX_FACIAL")) {
    if (has("FB9_GEX_FACIAL")) return "FB9_GEX_FACIAL";
  }
  if (base.includes("GEX2") && has("FB9_GEX2")) return "FB9_GEX2";
  if (base.includes("GEX") && has("FB9_GEX")) return "FB9_GEX";

  for (const pref of PREFERRED_EXTEND_FAMILIES) {
    if (has(pref)) return pref;
  }
  const first = slugs.find(isExtendFamilySlug);
  return first || slugs[0] || PREFERRED_EXTEND_FAMILIES[0];
}
