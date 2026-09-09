/** Shared extend / I2V family defaults for Submit compose. */

import type { WorkProductFamilyOption, WorkProductFamilyPromptProfile } from "./types";

export const PREFERRED_EXTEND_FAMILIES = ["FB9_GEX2", "FB9_GEX_FACIAL", "FB9_GEX"] as const;
export const PREFERRED_I2V_FAMILIES = [
  "X-KNEEL-FB9",
  "X-KNEEL-FB9-bare",
  "FB9-FaceBlast",
  "BounceDanceA",
] as const;

/** Slug-only gate (I2V/still families are not video Extend targets). */
export function familyDefaultFrames(families: WorkProductFamilyOption[], slug: string): number | null {
  const hit = families.find((f) => f.slug === slug);
  const n = Number(hit?.params_defaults?.frames);
  return Number.isFinite(n) && n > 0 ? Math.round(n) : null;
}

export function isExtendFamilySlug(slug: string): boolean {
  const s = String(slug || "").trim();
  return Boolean(s);
}

/**
 * Families that can run Extend on a video Use (need a source_video / V2V contract).
 * I2V / still-only shapes belong in still doors, not video Extend.
 * GEX2 identity-anchor is VI2V extend with an extra still slot — keep it listed.
 */
export function isExtendFamilyOption(f: WorkProductFamilyOption): boolean {
  const slug = String(f.slug || "").trim();
  if (!isExtendFamilySlug(slug)) return false;
  const role = String(f.chain_role || "").trim().toLowerCase();
  if (role === "extend") return true;
  if (role === "origin") return false;
  const io = String(f.io_class || "").trim().toUpperCase();
  if (io === "V2V" || io === "VI2V" || io === "EXT") return true;
  if (io === "I2V") return false;
  const sid = String(f.shape_id || "").toLowerCase();
  if (!sid) return true;
  if (sid.includes("vi2v") || sid.includes("identity")) return true;
  if (sid.includes("i2v") || (sid.includes("still") && !sid.includes("identity"))) return false;
  return (
    sid.includes("v2v") ||
    sid.includes("facial") ||
    sid.includes("source")
  );
}

/** Still → I2V origin families (Kneel / FaceBlast / Bounce…). */
export function isI2VFamilyOption(f: WorkProductFamilyOption): boolean {
  const slug = String(f.slug || "").trim();
  if (!slug) return false;
  if (f.source_still_required) return true;
  const role = String(f.chain_role || "").trim().toLowerCase();
  if (role === "origin") return true;
  const io = String(f.io_class || "").trim().toUpperCase();
  if (io === "I2V") return true;
  if (io === "V2V" || io === "VI2V" || io === "EXT") return false;
  const sid = String(f.shape_id || "").toLowerCase();
  if (sid.includes("i2v") || (sid.includes("still") && !sid.includes("identity"))) return true;
  if (PREFERRED_I2V_FAMILIES.includes(slug as (typeof PREFERRED_I2V_FAMILIES)[number])) return true;
  return false;
}

export function pickDefaultI2VFamily(
  families: WorkProductFamilyOption[],
  hintFamily?: string | null,
): string {
  const i2v = families.filter(isI2VFamilyOption);
  const slugs = (i2v.length ? i2v : families).map((f) => f.slug).filter(Boolean);
  const has = (slug: string) => slugs.includes(slug);
  const hint = String(hintFamily || "").trim();
  if (hint && has(hint)) return hint;
  for (const pref of PREFERRED_I2V_FAMILIES) {
    if (has(pref)) return pref;
  }
  return slugs[0] || PREFERRED_I2V_FAMILIES[0];
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

function familySlugTokens(slug: string): string[] {
  return slug.split(/[-_]/).filter(Boolean);
}

/** Slice of `slug` covering tokens `[from, to)`. Keeps the original separators. */
function familySlugTokenSlice(slug: string, tokens: string[], from: number, to: number): string {
  if (from < 0 || to > tokens.length || from >= to) return "";
  let search = 0;
  let start = 0;
  let end = 0;
  for (let i = 0; i < tokens.length; i++) {
    const at = slug.indexOf(tokens[i], search);
    if (at < 0) return tokens.slice(from, to).join("-");
    if (i === from) start = at;
    if (i === to - 1) end = at + tokens[i].length;
    search = at + tokens[i].length;
  }
  return slug.slice(start, end);
}

function familyTokenIsGeneric(token: string): boolean {
  return token.length <= 4 || /^(bare|og|ext)$/i.test(token);
}

/**
 * Short picker labels that stay unique in `slugs` and keep distinctive tokens
 * (`Breast-shake`, `identity_anchor`, `KNEEL-FB9-bare`) instead of mid-word chops.
 */
export function distinctiveFamilyLabels(slugs: string[], maxChars = 16): Map<string, string> {
  const list = [...new Set(slugs.map((s) => String(s || "").trim()).filter(Boolean))];
  const freq = new Map<string, number>();
  const tokensOf = new Map<string, string[]>();
  for (const slug of list) {
    const tokens = familySlugTokens(slug);
    tokensOf.set(slug, tokens);
    for (const t of new Set(tokens)) freq.set(t, (freq.get(t) || 0) + 1);
  }
  const out = new Map<string, string>();
  for (const slug of list) {
    out.set(slug, distinctiveFamilyLabelOne(slug, list, tokensOf.get(slug) || [], freq, maxChars));
  }
  const owners = new Map<string, string[]>();
  for (const [slug, label] of out) {
    const group = owners.get(label) || [];
    group.push(slug);
    owners.set(label, group);
  }
  for (const group of owners.values()) {
    if (group.length < 2) continue;
    for (const slug of group) {
      out.set(slug, slug.length <= maxChars ? slug : distinctiveFamilyFallback(slug, list, maxChars));
    }
  }
  return out;
}

function distinctiveFamilyLabelOne(
  slug: string,
  all: string[],
  tokens: string[],
  freq: Map<string, number>,
  maxChars: number,
): string {
  if (slug.length <= maxChars) return slug;
  const uniqueAt = tokens
    .map((t, i) => (freq.get(t) === 1 ? i : -1))
    .filter((i) => i >= 0);
  if (uniqueAt.length) {
    let from = uniqueAt[0];
    const to = uniqueAt[uniqueAt.length - 1] + 1;
    while (
      from > 0 &&
      (familySlugTokenSlice(slug, tokens, from, to).length < 8 || familyTokenIsGeneric(tokens[from]))
    ) {
      const wider = familySlugTokenSlice(slug, tokens, from - 1, to);
      if (wider.length > maxChars) break;
      from -= 1;
    }
    let piece = familySlugTokenSlice(slug, tokens, from, to);
    while (piece.length > maxChars && from < to - 1) {
      from += 1;
      piece = familySlugTokenSlice(slug, tokens, from, to);
    }
    if (piece && piece.length <= maxChars) return piece;
  }
  return distinctiveFamilyFallback(slug, all, maxChars);
}

function distinctiveFamilyFallback(slug: string, all: string[], maxChars: number): string {
  let need = 1;
  for (const other of all) {
    if (other === slug) continue;
    let i = 0;
    while (i < slug.length && i < other.length && slug[i] === other[i]) i += 1;
    need = Math.max(need, Math.min(slug.length, i + 1));
  }
  if (need >= slug.length) return slug;
  if (need <= maxChars) {
    const dash = slug.indexOf("-", need);
    const und = slug.indexOf("_", need);
    const nextSep = [dash, und].filter((n) => n >= need);
    if (nextSep.length) {
      const cut = Math.min(...nextSep);
      if (cut <= maxChars && cut > 0) return slug.slice(0, cut);
    }
    return slug.slice(0, need);
  }
  const tokens = familySlugTokens(slug);
  const tail = tokens[tokens.length - 1] || "";
  if (tail && tail.length + 4 <= maxChars) {
    const headBudget = maxChars - tail.length - 1;
    const head = slug.slice(0, Math.max(3, headBudget)).replace(/[-_]$/, "");
    return `${head}…${tail}`;
  }
  return `${slug.slice(0, Math.max(1, maxChars - 1))}…`;
}

/** Families that can take the same kind of input as `fromSlug` (I2V↔I2V, extend↔extend). */
export function familySwapTargets(
  families: WorkProductFamilyOption[],
  fromSlug: string,
): WorkProductFamilyOption[] {
  const from = families.find((f) => f.slug === fromSlug);
  return families.filter((f) => {
    if (!f.slug || f.slug === fromSlug) return false;
    if (!from) return true;
    const fromI2v = isI2VFamilyOption(from);
    const toI2v = isI2VFamilyOption(f);
    if (fromI2v && toI2v) return true;
    const fromExt = isExtendFamilyOption(from);
    const toExt = isExtendFamilyOption(f);
    if (fromExt && toExt) return true;
    return false;
  });
}

/** Sibling `-bare` / preferred I2V or extend family, else the first compatible target. */
export function pickDefaultSwapTarget(
  families: WorkProductFamilyOption[],
  fromSlug: string,
): string {
  const slugs = familySwapTargets(families, fromSlug)
    .map((f) => f.slug)
    .filter(Boolean);
  if (!slugs.length) return "";
  const from = String(fromSlug || "").trim();
  const bare = `${from}-bare`;
  if (slugs.includes(bare)) return bare;
  if (from.endsWith("-bare")) {
    const base = from.slice(0, -"-bare".length);
    if (slugs.includes(base)) return base;
  }
  for (const pref of PREFERRED_I2V_FAMILIES) {
    if (pref !== from && slugs.includes(pref)) return pref;
  }
  for (const pref of PREFERRED_EXTEND_FAMILIES) {
    if (pref !== from && slugs.includes(pref)) return pref;
  }
  return slugs[0];
}

/** Quarantine registry rows that can mark a family picker option. */
export type FamilyQuarantineHint = {
  workflow_name?: string;
  workflow_path?: string;
  status?: string;
};

/**
 * True when a quarantined workflow belongs to this family slug
 * (`{slug}-readable.json` or `{slug}_2026-…` / `{slug}_OG_…`), not a longer
 * sibling slug (`FB9` must not match `FB9_GEX2_…`).
 */
export function familySlugIsQuarantined(
  slug: string,
  entries?: FamilyQuarantineHint[] | null,
): boolean {
  const s = String(slug || "").trim();
  if (!s || !entries?.length) return false;
  return entries.some((e) => {
    if (e.status && String(e.status).toLowerCase() !== "quarantined") return false;
    const raw = String(e.workflow_name || e.workflow_path || "")
      .trim()
      .replace(/\\/g, "/");
    const base = raw.split("/").pop() || raw;
    if (!base) return false;
    if (base === `${s}-readable.json` || base === `${s}.json`) return true;
    if (base.startsWith(`${s}_`) || base.startsWith(`${s}-`)) {
      const rest = base.slice(s.length + 1);
      return /^(readable|\d{4}|OG[_-])/i.test(rest);
    }
    return false;
  });
}

/** True when a media path is a still (not a video Use). */
export function isStillMediaPath(path?: string | null): boolean {
  const p = String(path || "").trim().toLowerCase();
  if (!p) return false;
  if (/\.(mp4|webm|mov|mkv)(\?|$)/i.test(p)) return false;
  return /\.(png|jpe?g|webp|gif)(\?|$)/i.test(p);
}

export function familyPromptProfiles(
  families: WorkProductFamilyOption[],
  slug: string,
): WorkProductFamilyPromptProfile[] {
  const hit = families.find((f) => f.slug === slug);
  return Array.isArray(hit?.prompt_profiles) ? hit.prompt_profiles : [];
}

const VARIANT_WORD_FIX: Record<string, string> = {
  faceblast: "FaceBlast",
  gex: "GEX",
  gex2: "GEX2",
  i2v: "I2V",
  default: "Default",
};

const VARIANT_WRAPPERS = ["pp-catalog-", "catalog-", "prompt_profile-", "pp-"] as const;

/** Kebab slug: lowercase, hyphen separators. Guided by catalog/pp stems; not limited to them. */
export function slugifyPromptVariant(raw?: string | null): string {
  let s = String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/[_\s]+/g, "-")
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^[-._]+|[-._]+$/g, "");
  return s.slice(0, 80);
}

/** Canonical variant slug (`default`, `faceblast-extend`, or any kebab id). */
export function promptVariantSlug(input?: string | null | PromptVariantNameSource): string {
  if (input && typeof input === "object") {
    return (
      promptVariantSlug(input.slug) ||
      promptVariantSlug(input.seed?.slug) ||
      promptVariantSlug(input.label) ||
      promptVariantSlug(input.file_stem) ||
      promptVariantSlug(input.basename) ||
      promptVariantSlug(input.path) ||
      promptVariantSlug(input.seed?.label) ||
      promptVariantSlug(input.seed?.basename) ||
      promptVariantSlug(input.name) ||
      promptVariantSlug(input.seed?.name)
    );
  }
  let s = String(input || "")
    .trim()
    .replace(/\\/g, "/");
  if (s.includes("/")) s = s.split("/").pop() || s;
  s = s.replace(/\.json$/i, "");
  const lower = s.toLowerCase();
  for (const prefix of VARIANT_WRAPPERS) {
    if (lower.startsWith(prefix)) {
      s = s.slice(prefix.length);
      break;
    }
  }
  return slugifyPromptVariant(s);
}

/** @deprecated use promptVariantSlug */
export function promptVariantStem(raw?: string | null): string {
  return promptVariantSlug(raw);
}

export function formatPromptVariantStem(stem: string): string {
  const parts = String(stem || "")
    .split(/[-_]+/)
    .filter(Boolean);
  if (!parts.length) return "";
  return parts
    .map((w) => VARIANT_WORD_FIX[w.toLowerCase()] || `${w.charAt(0).toUpperCase()}${w.slice(1).toLowerCase()}`)
    .join(" ");
}

export type PromptVariantNameSource = {
  name?: string | null;
  label?: string | null;
  slug?: string | null;
  file_stem?: string | null;
  basename?: string | null;
  path?: string | null;
  seed?: {
    slug?: string | null;
    name?: string | null;
    label?: string | null;
    basename?: string | null;
  } | null;
};

/** Operator-facing variant name: JSON `name`, else formatted slug. */
export function promptVariantName(input?: string | null | PromptVariantNameSource): string {
  if (input && typeof input === "object") {
    const named = String(input.name || input.seed?.name || "").trim();
    if (named) return named;
  }
  const slug = promptVariantSlug(input);
  return formatPromptVariantStem(slug) || slug;
}

/** This job's first `__pp-` / `__prompt_profile-` token (not a later source clip's). */
export function jobPromptVariantSlug(jobKey?: string | null): string {
  for (const part of String(jobKey || "").split("__")) {
    const m = part.match(/^(?:pp|prompt_profile)-(.+)$/i);
    if (m) return promptVariantSlug(m[1]);
  }
  return "";
}

/** @deprecated use jobPromptVariantSlug */
export function jobPromptVariantStem(jobKey?: string | null): string {
  return jobPromptVariantSlug(jobKey);
}

export function jobPromptVariantName(item: {
  job_key?: string | null;
  prompt_profile?: string | null | PromptVariantNameSource;
}): string {
  const fromProfile = promptVariantName(item.prompt_profile);
  if (fromProfile) return fromProfile;
  const slug = jobPromptVariantSlug(item.job_key);
  return formatPromptVariantStem(slug) || slug;
}

export type PromptOverrideHint = {
  snowflake?: boolean | null;
};

function looksLikeScratchPrompt(profile?: string | null | PromptVariantNameSource | null): boolean {
  const bits =
    profile && typeof profile === "object"
      ? [profile.path, profile.basename, profile.seed?.basename]
      : [profile];
  return bits.some((bit) => /\/_scratch\/|__draft_/i.test(String(bit || "")));
}

/** True when owned prompt text diverged from the catalog seed. */
export function promptTextIsOverridden(
  profile?: string | null | (PromptVariantNameSource & PromptOverrideHint) | null,
  glance?: { prompt_snowflake?: boolean | null } | null,
): boolean {
  if (profile && typeof profile === "object" && profile.snowflake) return true;
  if (glance?.prompt_snowflake) return true;
  return looksLikeScratchPrompt(profile);
}

/** Variant name plus `` · edited`` when the job no longer matches the catalog. */
export function jobPromptVariantDisplayName(item: {
  job_key?: string | null;
  prompt_profile?: string | null | (PromptVariantNameSource & PromptOverrideHint);
  glance?: { prompt_snowflake?: boolean | null } | null;
}): string {
  const name = jobPromptVariantName(item);
  if (promptTextIsOverridden(item.prompt_profile, item.glance)) {
    return name ? `${name} · edited` : "edited";
  }
  return name;
}

export function isDefaultPromptVariant(input?: string | null | PromptVariantNameSource): boolean {
  return promptVariantSlug(input) === "default";
}

export function isDefaultPromptVariantName(name?: string | null): boolean {
  return isDefaultPromptVariant(name);
}

export function promptProfileOptionLabel(p: WorkProductFamilyPromptProfile): string {
  return promptVariantName(p) || p.slug || p.basename || p.path;
}

function profileMatchesPrefer(p: WorkProductFamilyPromptProfile, prefer: string): boolean {
  if (p.path === prefer || p.basename === prefer || p.file_stem === prefer || p.label === prefer) return true;
  const want = promptVariantSlug(prefer);
  return Boolean(want) && promptVariantSlug(p) === want;
}

export function pickDefaultPromptProfile(
  profiles: WorkProductFamilyPromptProfile[],
  opts?: { familySlug?: string | null; mediaRelpath?: string | null; prefer?: string | null },
): string {
  if (!profiles.length) return "";
  const prefer = String(opts?.prefer || "").trim();
  if (prefer) {
    const hit = profiles.find((p) => profileMatchesPrefer(p, prefer));
    if (hit) return hit.path;
  }
  const family = String(opts?.familySlug || "").trim();
  const media = String(opts?.mediaRelpath || "").toLowerCase();
  if (family === "FB9_GEX" && /faceblast|face_blast/.test(media)) {
    const ext = profiles.find((p) => promptVariantSlug(p) === "faceblast-extend");
    if (ext) return ext.path;
  }
  const def = profiles.find((p) => promptVariantSlug(p) === "default" || p.basename === "catalog-default.json");
  return (def || profiles[0]).path;
}

/**
 * Catalog preset path for Re-run. Matches the job's variant slug when that
 * preset exists on the selected family; never a scratch / snowflake path.
 */
export function pickRerunPromptPreset(
  profiles: WorkProductFamilyPromptProfile[],
  prefer?: string | null | PromptVariantNameSource,
): string {
  if (!profiles.length) return "";
  const slug = promptVariantSlug(prefer);
  if (slug) {
    const hit = profiles.find((p) => promptVariantSlug(p) === slug);
    if (hit?.path) return hit.path;
  }
  if (prefer && typeof prefer === "object") {
    const path = String(prefer.path || prefer.seed?.path || "").trim();
    const byPath = profiles.find((p) => p.path === path);
    if (byPath?.path) return byPath.path;
  }
  return pickDefaultPromptProfile(profiles);
}

/** Send a prompt_profile binding only when the operator picked a different catalog preset. */
export function rerunPromptPresetDiffers(
  selectedPath: string,
  jobProfiles: WorkProductFamilyPromptProfile[],
  jobPrefer?: string | null | PromptVariantNameSource,
): boolean {
  const selected = String(selectedPath || "").trim();
  if (!selected) return false;
  return selected !== pickRerunPromptPreset(jobProfiles, jobPrefer);
}
