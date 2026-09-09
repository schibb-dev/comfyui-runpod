import type { WorkProductFailure, WorkProductFailureAction, WorkProductItem } from "./types";

const HEADLINES: Record<WorkProductFailure["kind"], string> = {
  submit_missed: "Never reached Comfy",
  interrupted: "Interrupted on Comfy — no output",
  comfy_failed: "Failed during the Comfy run",
  permanent: "This job cannot be submitted as-is",
  abandoned: "Retries exhausted",
};

const PERMANENT_NEEDLES = [
  "invalid image file",
  "custom_validation_failed",
  "no companion png",
  "cannot build api prompt",
  "workflow missing",
  "shape missing",
  "quarantined template",
  "not a litegraph workflow",
];

function statusKey(status?: string | null): string {
  const s = String(status || "").toLowerCase().trim();
  if (s === "failed" || s === "completed") return s === "failed" ? "error" : "complete";
  return s;
}

function isHistoryFailureStub(item: Pick<WorkProductItem, "job_path" | "history_from_comfy" | "construction" | "status" | "prompt_id" | "job_key">): boolean {
  if (String(item.job_path || "").trim()) return false;
  if (item.history_from_comfy) return true;
  if (String(item.construction?.source || "") === "comfy_history") return true;
  const s = statusKey(item.status);
  if (!(s === "error" || s === "interrupted" || s === "abandoned")) return false;
  return Boolean(String(item.prompt_id || "").trim() || String(item.job_key || "").trim());
}

function classifyFailure(
  status?: string | null,
  promptId?: string | null,
  error?: string | null,
): WorkProductFailure | null {
  const st = statusKey(status);
  if (st !== "error" && st !== "interrupted" && st !== "abandoned") return null;
  const pid = String(promptId || "").trim();
  const err = String(error || "").trim();
  const errL = err.toLowerCase();
  const permanent = PERMANENT_NEEDLES.some((n) => errL.includes(n));
  const interrupted = st === "interrupted" || errL.includes("interrupted") || errL.includes("lost from comfy");
  let kind: WorkProductFailure["kind"];
  let primary: WorkProductFailureAction;
  if (st === "abandoned") {
    kind = "abandoned";
    primary = pid ? "replay" : "retry_same";
  } else if (permanent) {
    kind = "permanent";
    primary = "edit";
  } else if (!pid) {
    kind = "submit_missed";
    primary = "retry_same";
  } else if (interrupted) {
    kind = "interrupted";
    primary = "replay";
  } else {
    kind = "comfy_failed";
    primary = "replay";
  }
  const actionsByKind: Record<WorkProductFailure["kind"], WorkProductFailureAction[]> = {
    submit_missed: ["retry_same", "edit", "replay", "discard"],
    interrupted: ["replay", "edit", "discard"],
    comfy_failed: ["replay", "edit", "discard"],
    permanent: ["edit", "discard"],
    abandoned: pid ? ["replay", "edit", "discard"] : ["retry_same", "replay", "edit", "discard"],
  };
  const actions = actionsByKind[kind];
  return {
    kind,
    headline: HEADLINES[kind],
    primary,
    actions,
    can_retry_same: actions.includes("retry_same"),
    detail: err || null,
  };
}

function asFailure(raw: WorkProductItem["failure"]): WorkProductFailure | null {
  if (!raw || typeof raw !== "object") return null;
  const kind = raw.kind;
  if (
    kind !== "submit_missed" &&
    kind !== "interrupted" &&
    kind !== "comfy_failed" &&
    kind !== "permanent" &&
    kind !== "abandoned"
  ) {
    return null;
  }
  const actions = (raw.actions || []).filter(
    (a): a is WorkProductFailureAction =>
      a === "retry_same" || a === "replay" || a === "edit" || a === "discard",
  );
  const primary =
    raw.primary === "retry_same" || raw.primary === "replay" || raw.primary === "edit" || raw.primary === "discard"
      ? raw.primary
      : actions[0];
  if (!primary) return null;
  return {
    kind,
    headline: String(raw.headline || HEADLINES[kind]),
    primary,
    actions: actions.length ? actions : [primary],
    can_retry_same: Boolean(raw.can_retry_same) || actions.includes("retry_same"),
    detail: raw.detail || null,
  };
}

/** Server `failure` when present; otherwise classify from status/error. History stubs archive only. */
export function workProductFailure(item: WorkProductItem): WorkProductFailure | null {
  const classified = asFailure(item.failure) || classifyFailure(item.status, item.prompt_id, item.error);
  if (!classified) return null;
  if (!isHistoryFailureStub(item)) return classified;
  return {
    ...classified,
    primary: "discard",
    actions: ["discard"],
    can_retry_same: false,
    headline: classified.headline,
  };
}

export function failurePrimaryLabel(failure: WorkProductFailure): string {
  if (failure.primary === "retry_same") return "Return to pending";
  if (failure.primary === "replay") return "Replay to pending";
  if (failure.primary === "edit") return "Edit in Submit";
  return "Archive";
}

const FAILURE_EVENT_ACTIONS = new Set(["submit_failed", "comfy_failed", "interrupted", "abandoned"]);

/** Timeline rows: persisted control events, plus a failure-origin event when missing. */
export function workProductFlowEvents(item: WorkProductItem): NonNullable<WorkProductItem["flow_events"]> {
  const existing = Array.isArray(item.flow_events) ? [...item.flow_events] : [];
  if (existing.some((ev) => FAILURE_EVENT_ACTIONS.has(String(ev?.action || "")))) return existing;
  const failure = workProductFailure(item);
  if (!failure) return existing;
  const action =
    failure.kind === "interrupted"
      ? "interrupted"
      : failure.kind === "abandoned"
        ? "abandoned"
        : failure.kind === "comfy_failed"
          ? "comfy_failed"
          : "submit_failed";
  const comfy = action === "interrupted" || action === "comfy_failed";
  existing.unshift({
    at: item.finished_at || item.submitted_at || item.created_at || null,
    action,
    actor: comfy ? "comfy" : "drain",
    source_surface: comfy ? "comfy" : "submit",
    reason: failure.detail || item.error || null,
    ok: false,
  });
  return existing;
}
