/** Still-tag batch result rows from GET …/tag/runs/{id}/results */

export type StillTagResultStatus = "done" | "error" | "missing" | "pending";

export type StillTagResultItem = {
  content_id: string;
  relpath?: string | null;
  url?: string | null;
  missing?: boolean;
  status: StillTagResultStatus;
  /** Tags from this batch event (or provisional fallback). */
  tags?: string[];
  provisional_tags?: string[];
  editorial_tags?: string[];
  effective_tags?: string[];
  tag_count?: number;
  error_message?: string | null;
  warning?: string | null;
  tagged_at?: string | null;
};

export type StillTagResultsSummary = {
  done: number;
  errors: number;
  pending: number;
};

export type StillTagResultsResponse = {
  ok: boolean;
  run_id?: string;
  items?: StillTagResultItem[];
  count?: number;
  summary?: StillTagResultsSummary;
  run?: Record<string, unknown>;
  error?: string;
  detail?: string;
};

export type StillTagResultFilter = "all" | "done" | "errors" | "pending";

export function stillTagResultFilterLabel(filter: StillTagResultFilter): string {
  if (filter === "done") return "Tagged";
  if (filter === "errors") return "Errors";
  if (filter === "pending") return "Pending";
  return "All";
}

export function filterStillTagResults(items: StillTagResultItem[], filter: StillTagResultFilter): StillTagResultItem[] {
  if (filter === "all") return items;
  if (filter === "done") return items.filter((it) => it.status === "done");
  if (filter === "errors") return items.filter((it) => it.status === "error" || it.status === "missing");
  return items.filter((it) => it.status === "pending");
}

export function stillTagResultStatusLabel(status: StillTagResultStatus): string {
  if (status === "done") return "Tagged";
  if (status === "error") return "Error";
  if (status === "missing") return "Missing";
  return "Pending";
}

export function stillTagResultStatusTone(status: StillTagResultStatus): "ok" | "error" | "muted" | "queued" {
  if (status === "done") return "ok";
  if (status === "error" || status === "missing") return "error";
  if (status === "pending") return "queued";
  return "muted";
}

export type StillTagResultTagGroups = {
  auto: string[];
  editorial: string[];
  effective: string[];
};

/** Resolve auto / editorial / merged tag lists for display. */
export function stillTagResultTagGroups(item: StillTagResultItem): StillTagResultTagGroups {
  const auto = (item.provisional_tags?.length ? item.provisional_tags : item.tags) || [];
  const editorial = item.editorial_tags || [];
  const effective =
    item.effective_tags?.length ? item.effective_tags : [...new Set([...editorial, ...auto])];
  return { auto, editorial, effective };
}
