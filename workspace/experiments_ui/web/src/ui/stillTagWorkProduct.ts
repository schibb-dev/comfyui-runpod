import type { WorkProductItem } from "./types";

export type StillTagOutput = {
  tags?: string[];
  tag_count?: number;
  done_count?: number;
  total?: number;
  error_count?: number;
  preview_content_id?: string | null;
  /** Still currently on Comfy / next in the batch (live preview). */
  current_content_id?: string | null;
  current_relpath?: string | null;
  current_url?: string | null;
  caption?: string | null;
};

export function isStillTagWorkProduct(item: Pick<WorkProductItem, "work_kind" | "construction">): boolean {
  if (item.work_kind === "still_tag") return true;
  const step = String(item.construction?.step || "");
  return step === "still_tag";
}

/** Queued SLA / dry-run queue tests are not Workbench jobs. */
export function stillTagBelongsOnWorkbench(item: Pick<WorkProductItem, "status" | "work_kind" | "construction">): boolean {
  if (!isStillTagWorkProduct(item)) return false;
  const s = String(item.status || "").toLowerCase();
  return s === "running" || s === "complete" || s === "done" || s === "error" || s === "failed";
}

/** Factory jobs first; still-tag stubs prepend when the lazy query arrives. */
export function mergeStillTagWorkProducts(
  jobs: WorkProductItem[],
  stillTags: WorkProductItem[] | null | undefined,
): WorkProductItem[] {
  const factory = jobs.filter((it) => !isStillTagWorkProduct(it));
  const tags = (stillTags || []).filter((it) => stillTagBelongsOnWorkbench(it));
  if (!tags.length) return factory;
  const tagKeys = new Set(tags.map((it) => String(it.job_key || "")).filter(Boolean));
  const rest = factory.filter((it) => !tagKeys.has(String(it.job_key || "")));
  return [...tags, ...rest];
}

export function stillTagProgressLabel(item: WorkProductItem): string | null {
  const out = item.still_tag_output;
  const total = Number(out?.total ?? item.construction?.total ?? 0);
  const done = Number(out?.done_count ?? item.construction?.done_count ?? 0);
  if (!total) return null;
  const err = Number(out?.error_count ?? item.construction?.error_count ?? 0);
  return `${done}/${total} tagged${err ? ` · ${err} err` : ""}`;
}

/** Sidebar / list primary title for a still-tag batch. */
export function stillTagDisplayTitle(item: WorkProductItem): string {
  const fromApi = String(item.display_title || "").trim();
  if (fromApi) return fromApi;
  const progress = stillTagProgressLabel(item);
  return progress ? `Still tag · ${progress}` : "Still tag";
}

/** Short identity under the title (not the LoadImage hash or full run id). */
export function stillTagIdentityLabel(item: WorkProductItem): string {
  const runId = String(item.still_tag_run_id || item.job_key || "").trim();
  if (!runId) return "still tag";
  const compact = runId.replace(/^still_tag_/, "");
  return compact.length > 28 ? `${compact.slice(0, 28)}…` : compact;
}

export function stillTagCurrentContentId(item: WorkProductItem): string | null {
  const out = item.still_tag_output;
  const cid = String(out?.current_content_id || item.content_id || "").trim();
  return cid || null;
}

/** Live preview: the still Florence is tagging now (not a stale batch head). */
export function stillTagCurrentPreviewUrl(item: WorkProductItem): string | null {
  const out = item.still_tag_output;
  return (
    out?.current_url ||
    item.output_thumb_url ||
    item.output_url ||
    item.bindings?.source_still?.thumb_url ||
    item.bindings?.source_still?.url ||
    item.parent_output_thumb_url ||
    item.parent_output_url ||
    null
  );
}

/** @deprecated use stillTagCurrentPreviewUrl */
export function stillTagPreviewUrl(item: WorkProductItem): string | null {
  return stillTagCurrentPreviewUrl(item);
}

export function stillTagStatusLabel(item: WorkProductItem): string {
  const s = String(item.status || "").toLowerCase();
  if (s === "running") return "tagging";
  if (s === "pending" || s === "queued") return "queued";
  if (s === "complete" || s === "done") return "done";
  if (s === "error" || s === "failed") return "error";
  return s || "still tag";
}
