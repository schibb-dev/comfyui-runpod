import type { DispositionBucketItem, WorkProductItem } from "./types";
import {
  workProductIdentityLabel as kindIdentityLabel,
  workProductKindId,
} from "./workProductKind";
import {
  filesUrlForRelpath,
  isMediaOutputOfJob,
  mediaFocusLabel,
  workProductMatchesMedia,
} from "./workProductMediaFocus";

export type WorkbenchWorkingSetId =
  | "recent"
  | "follow-up"
  | "refine"
  | "investigate"
  | "advance"
  | "park"
  | "retire";

export const FOLLOW_UP_JOB_PREFIX = "follow-up:";

export const WORKBENCH_WORKING_SETS: Array<{
  id: WorkbenchWorkingSetId;
  label: string;
  entry: string | null;
}> = [
  { id: "recent", label: "Recent", entry: null },
  { id: "follow-up", label: "Follow-up", entry: null },
  { id: "refine", label: "Follow-up · Refine", entry: "refine" },
  { id: "investigate", label: "Follow-up · Investigate", entry: "investigate" },
  { id: "advance", label: "Follow-up · Advance", entry: "advance" },
  { id: "park", label: "Follow-up · Re-evaluate later", entry: "park" },
  { id: "retire", label: "Follow-up · Retire", entry: "retire" },
];

const WORKING_SET_KEY = "work-products-working-set";

const ENTRY_IDS: WorkbenchWorkingSetId[] = ["refine", "investigate", "advance", "park", "retire"];

export function parseWorkingSetId(raw?: string | null): WorkbenchWorkingSetId {
  const v = String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/_/g, "-");
  if (v === "follow-up" || v === "followup" || v === "pools") return "follow-up";
  if ((ENTRY_IDS as string[]).includes(v)) return v as WorkbenchWorkingSetId;
  return "recent";
}

export function loadWorkingSet(): WorkbenchWorkingSetId {
  try {
    return parseWorkingSetId(localStorage.getItem(WORKING_SET_KEY));
  } catch {
    return "recent";
  }
}

export function persistWorkingSet(id: WorkbenchWorkingSetId) {
  try {
    localStorage.setItem(WORKING_SET_KEY, id);
  } catch {
    /* ignore */
  }
}

export function isFollowUpWorkingSet(id: WorkbenchWorkingSetId): boolean {
  return id !== "recent";
}

export function isFollowUpWorkingSetJob(jobKey?: string | null): boolean {
  return String(jobKey || "").startsWith(FOLLOW_UP_JOB_PREFIX);
}

export function workProductIdentityLabel(
  item: Pick<WorkProductItem, "job_key" | "output_relpath" | "exp_id" | "run_id" | "work_kind" | "construction">,
): string {
  if (workProductKindId(item as WorkProductItem) !== "factory") {
    return kindIdentityLabel(item as WorkProductItem);
  }
  if (isFollowUpWorkingSetJob(item.job_key)) {
    return mediaFocusLabel(item.output_relpath || item.job_key);
  }
  const exp = String(item.exp_id || "").trim();
  const run = String(item.run_id || "").trim();
  if (exp && run) return `${exp} / ${run}`;
  return item.job_key || "";
}

export function workingSetEntry(id: WorkbenchWorkingSetId): string | null {
  return WORKBENCH_WORKING_SETS.find((s) => s.id === id)?.entry ?? null;
}

function itemEntries(item: DispositionBucketItem): string[] {
  if (item.entries?.length) return item.entries;
  return item.entry ? [item.entry] : [];
}

function findJobForMedia(jobs: WorkProductItem[], relpath: string): WorkProductItem | undefined {
  return (
    jobs.find((j) => isMediaOutputOfJob(j, relpath)) ||
    jobs.find((j) => workProductMatchesMedia(j, relpath))
  );
}

export function filterFollowUpBucketItems(
  rows: DispositionBucketItem[],
  setId: WorkbenchWorkingSetId,
): DispositionBucketItem[] {
  const entry = workingSetEntry(setId);
  if (!entry) return rows;
  return rows.filter((r) => itemEntries(r).includes(entry));
}

/** Play a `?media=` clip that has no factory/follow-up producer row. */
export function workProductFromFocusedMedia(media: string): WorkProductItem | null {
  const rel = String(media || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  if (!rel) return null;
  return {
    job_key: FOLLOW_UP_JOB_PREFIX + rel,
    output_relpath: rel,
    output_url: filesUrlForRelpath(rel),
    status: "complete",
  };
}

export function workProductFromFollowUpItem(
  it: DispositionBucketItem,
  jobs: WorkProductItem[],
): WorkProductItem {
  const rel = String(it.relpath || "")
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  const primary = it.entry || itemEntries(it)[0] || null;
  const hit = rel ? findJobForMedia(jobs, rel) : undefined;
  if (hit) {
    return {
      ...hit,
      status: "complete",
      disposition_entry: primary || hit.disposition_entry,
      output_relpath: hit.output_relpath || rel,
      output_url: hit.output_url || it.video_url || it.url || null,
      output_thumb_url: hit.output_thumb_url || it.thumb_url || null,
    };
  }
  return {
    job_key: FOLLOW_UP_JOB_PREFIX + rel,
    output_relpath: rel,
    output_url: it.video_url || it.url || null,
    output_thumb_url: it.thumb_url || null,
    status: "complete",
    disposition_entry: primary,
    created_at: it.updated_at || undefined,
  };
}
