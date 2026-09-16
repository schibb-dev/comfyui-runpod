/**
 * Pluggable Workbench job kinds.
 *
 * Factory `.job.json` rows are the default kind. Additional kinds (still_tag, tune
 * experiments, …) register here so WorkProductsApp does not sprawl with
 * `isStillTagWorkProduct` branches.
 *
 * Backend mirror (shape_factory_work_products.py): each kind should expose
 * attach → list rows, match_comfy_prompt → live promote, refresh_live_row → preview.
 */
import type { WorkProductItem } from "./types";
import {
  isStillTagWorkProduct,
  stillTagCurrentPreviewUrl,
  stillTagDisplayTitle,
  stillTagIdentityLabel,
  stillTagStatusLabel,
} from "./stillTagWorkProduct";

/** How the main viewer renders preview media for this kind. */
export type WorkProductPreviewMode = "output_video" | "still_tag" | "source_thumb" | "comfy_latent";

export type WorkProductKindDef = {
  id: string;
  matches: (item: WorkProductItem) => boolean;
  displayTitle: (item: WorkProductItem) => string;
  identityLabel: (item: WorkProductItem) => string;
  previewUrl: (item: WorkProductItem) => string | null;
  /** List/detail chrome meant for shape-factory families (variant badge, family link, …). */
  showFactoryChrome: boolean;
  previewMode: WorkProductPreviewMode;
  /** Kind may set output_url while still running (still tag live preview). */
  runningLiveDespiteOutput: boolean;
  statusLabel?: (item: WorkProductItem) => string;
};

const FACTORY_KIND: WorkProductKindDef = {
  id: "factory",
  matches: () => true,
  displayTitle: (item) => {
    const title = String(item.display_title || item.spec_title || "").trim();
    if (title) return title;
    return String(item.family_slug || item.job_key || "Job");
  },
  identityLabel: (item) => String(item.job_key || ""),
  previewUrl: (item) => item.output_thumb_url || item.output_url || null,
  showFactoryChrome: true,
  previewMode: "output_video",
  runningLiveDespiteOutput: false,
};

const STILL_TAG_KIND: WorkProductKindDef = {
  id: "still_tag",
  matches: isStillTagWorkProduct,
  displayTitle: stillTagDisplayTitle,
  identityLabel: stillTagIdentityLabel,
  previewUrl: stillTagCurrentPreviewUrl,
  showFactoryChrome: false,
  previewMode: "still_tag",
  runningLiveDespiteOutput: true,
  statusLabel: stillTagStatusLabel,
};

/** Specific kinds first; factory is the fallback. */
const REGISTERED_KINDS: WorkProductKindDef[] = [STILL_TAG_KIND, FACTORY_KIND];

const KIND_BY_ID = new Map(REGISTERED_KINDS.map((k) => [k.id, k]));

export function registeredWorkProductKinds(): readonly WorkProductKindDef[] {
  return REGISTERED_KINDS;
}

export function workProductKindOf(item: WorkProductItem): WorkProductKindDef {
  const explicit = String(item.work_kind || "").trim();
  if (explicit && KIND_BY_ID.has(explicit)) {
    return KIND_BY_ID.get(explicit)!;
  }
  for (const kind of REGISTERED_KINDS) {
    if (kind.id !== "factory" && kind.matches(item)) return kind;
  }
  return FACTORY_KIND;
}

export function workProductKindId(item: WorkProductItem): string {
  return workProductKindOf(item).id;
}

export function workProductShowFactoryChrome(item: WorkProductItem): boolean {
  return workProductKindOf(item).showFactoryChrome;
}

export function workProductPreviewMode(item: WorkProductItem): WorkProductPreviewMode {
  return workProductKindOf(item).previewMode;
}

export function workProductRunningLiveDespiteOutput(item: WorkProductItem): boolean {
  return workProductKindOf(item).runningLiveDespiteOutput;
}

export function workProductDisplayTitle(item: WorkProductItem): string {
  return workProductKindOf(item).displayTitle(item);
}

export function workProductIdentityLabel(item: WorkProductItem): string {
  return workProductKindOf(item).identityLabel(item);
}

export function workProductPreviewUrl(item: WorkProductItem): string | null {
  return workProductKindOf(item).previewUrl(item);
}

export function workProductKindStatusLabel(item: WorkProductItem): string | null {
  const fn = workProductKindOf(item).statusLabel;
  return fn ? fn(item) : null;
}

export function workProductKindIs(item: WorkProductItem, kindId: string): boolean {
  return workProductKindId(item) === kindId;
}
