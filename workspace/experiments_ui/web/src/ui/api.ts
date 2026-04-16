import type {
  ExperimentRunsResponse,
  ExperimentsResponse,
  QueueResponse,
  RequeueRunRequest,
  RequeueRunResponse,
  QueueSubmitPromptRequest,
  QueueSubmitPromptResponse,
  ComfyCancelRequest,
  ComfyCancelResponse,
  ComfyClearResponse,
  MultiRunsResponse,
  NextExperimentRequest,
  NextExperimentResponse,
  WipResponse,
  CreateExperimentRequest,
  CreateExperimentResponse,
  ComfyHistoryResponse,
  OrchestratorState,
  DiscoveryLibraryResponse,
  DiscoveryLibraryItem,
  DiscoveryEmbedApiPromptResponse,
} from "./types";

export async function fetchDiscoveryLibrary(opts?: {
  refresh?: boolean;
  q?: string;
  since_days?: number;
  library?: "og" | "wip" | "all";
  limit?: number;
}): Promise<DiscoveryLibraryResponse> {
  const sp = new URLSearchParams();
  if (opts?.refresh) sp.set("refresh", "1");
  if (opts?.q != null && opts.q !== "") sp.set("q", opts.q);
  if (opts?.since_days != null && opts.since_days > 0) sp.set("since_days", String(opts.since_days));
  if (opts?.library && opts.library !== "all") sp.set("library", opts.library);
  if (opts?.limit != null && opts.limit > 0) sp.set("limit", String(opts.limit));
  const qs = sp.toString();
  const r = await fetch(`/api/discovery/library${qs ? `?${qs}` : ""}`);
  if (!r.ok) throw new Error(`GET /api/discovery/library failed: ${r.status}`);
  return (await r.json()) as DiscoveryLibraryResponse;
}

export async function fetchDiscoveryEmbedApiPrompt(it: DiscoveryLibraryItem): Promise<DiscoveryEmbedApiPromptResponse> {
  const sp = new URLSearchParams();
  sp.set("relpath", it.relpath);
  sp.set("library", it.library);
  if (it.thumb_relpath) sp.set("thumb_relpath", it.thumb_relpath);
  if (it.video_relpath) sp.set("video_relpath", it.video_relpath);
  const r = await fetch(`/api/discovery/embed-api-prompt?${sp.toString()}`);
  const j = (await r.json()) as DiscoveryEmbedApiPromptResponse & { error?: string; path?: string };
  if (!r.ok) {
    const isStale =
      r.status === 404 &&
      j &&
      typeof j === "object" &&
      j.error === "unknown_api_route" &&
      String(j.path || "").includes("embed-api-prompt");
    if (isStale) {
      throw new Error(
        "The Experiments UI API you are hitting is too old: it does not have GET /api/discovery/embed-api-prompt. " +
          "Restart the Python server that serves /api (pick the setup you use): " +
          "host dev — stop and re-run experiments_ui_server on port 8791 (e.g. npm run ui:dev:all or scripts/experiments-ui-dev.mjs all); " +
          "Docker — docker compose restart comfyui so the container reloads scripts/experiments_ui_server.py from the bind mount. " +
          "If Vite proxies to port 8790, that is usually the container; after a git pull you must restart the container, not only refresh the browser."
      );
    }
    throw new Error(`GET /api/discovery/embed-api-prompt failed: ${r.status}: ${JSON.stringify(j)}`);
  }
  return j;
}

/** Sidecar trim presets per media file + context (e.g. discovery-player). */
export type DiscoveryTrimPreset = {
  id: string;
  label: string;
  in: number;
  out: number;
  at?: number;
};

export type DiscoveryTrimGetResponse = {
  found: boolean;
  media_relpath: string;
  context: string;
  active_preset_id: string | null;
  active: DiscoveryTrimPreset | null;
  presets: DiscoveryTrimPreset[];
};

export async function fetchDiscoveryTrim(
  mediaRelpath: string,
  context: string
): Promise<DiscoveryTrimGetResponse> {
  const sp = new URLSearchParams();
  sp.set("media_relpath", mediaRelpath);
  sp.set("context", context);
  const r = await fetch(`/api/discovery/trim?${sp.toString()}`);
  if (!r.ok) throw new Error(`GET /api/discovery/trim failed: ${r.status}`);
  return (await r.json()) as DiscoveryTrimGetResponse;
}

export type DiscoveryTrimSaveBody = {
  media_relpath: string;
  context: string;
  op?: "save_trim";
  duration_sec: number;
  in?: number | null;
  out?: number | null;
  clear?: boolean;
  preset_id?: string | null;
  label?: string | null;
};

export async function postDiscoveryTrimSave(body: DiscoveryTrimSaveBody): Promise<void> {
  const r = await fetch("/api/discovery/trim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ op: "save_trim", ...body }),
  });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error(`POST /api/discovery/trim failed: ${r.status}${t ? `\n${t}` : ""}`);
  }
}

export async function fetchExperiments(): Promise<ExperimentsResponse> {
  const r = await fetch("/api/experiments");
  if (!r.ok) throw new Error(`GET /api/experiments failed: ${r.status}`);
  return (await r.json()) as ExperimentsResponse;
}

export async function fetchExperimentRuns(expId: string): Promise<ExperimentRunsResponse> {
  const r = await fetch(`/api/experiments/${encodeURIComponent(expId)}/runs`);
  if (!r.ok) throw new Error(`GET /api/experiments/${expId}/runs failed: ${r.status}`);
  return (await r.json()) as ExperimentRunsResponse;
}

export async function fetchRunsMulti(expIds: string[]): Promise<MultiRunsResponse> {
  const qs = expIds.map((id) => `exp_id=${encodeURIComponent(id)}`).join("&");
  const r = await fetch(`/api/runs?${qs}`);
  if (!r.ok) throw new Error(`GET /api/runs failed: ${r.status}`);
  return (await r.json()) as MultiRunsResponse;
}

export async function createNextExperiment(req: NextExperimentRequest): Promise<NextExperimentResponse> {
  const r = await fetch("/api/next-experiment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error(`POST /api/next-experiment failed: ${r.status}${t ? `\n${t}` : ""}`);
  }
  return (await r.json()) as NextExperimentResponse;
}

export async function fetchQueue(): Promise<QueueResponse> {
  const r = await fetch("/api/queue");
  if (!r.ok) throw new Error(`GET /api/queue failed: ${r.status}`);
  return (await r.json()) as QueueResponse;
}

export async function requeueRun(req: RequeueRunRequest): Promise<RequeueRunResponse> {
  const r = await fetch("/api/queue/requeue-run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error(`POST /api/queue/requeue-run failed: ${r.status}${t ? `\n${t}` : ""}`);
  }
  return (await r.json()) as RequeueRunResponse;
}

export async function submitPromptToQueue(req: QueueSubmitPromptRequest): Promise<QueueSubmitPromptResponse> {
  const r = await fetch("/api/queue/submit-prompt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error(`POST /api/queue/submit-prompt failed: ${r.status}${t ? `\n${t}` : ""}`);
  }
  return (await r.json()) as QueueSubmitPromptResponse;
}

export async function comfyCancel(req: ComfyCancelRequest): Promise<ComfyCancelResponse> {
  const r = await fetch("/api/queue/comfy-cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error(`POST /api/queue/comfy-cancel failed: ${r.status}${t ? `\n${t}` : ""}`);
  }
  return (await r.json()) as ComfyCancelResponse;
}

export async function fetchWip(dir?: string): Promise<WipResponse> {
  const qs = dir != null && dir !== "" ? `?dir=${encodeURIComponent(dir)}` : "";
  const r = await fetch(`/api/wip${qs}`);
  if (!r.ok) throw new Error(`GET /api/wip failed: ${r.status}`);
  return (await r.json()) as WipResponse;
}

export async function createExperimentFromWip(req: CreateExperimentRequest): Promise<CreateExperimentResponse> {
  const r = await fetch("/api/create-experiment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error(`POST /api/create-experiment failed: ${r.status}${t ? `\n${t}` : ""}`);
  }
  return (await r.json()) as CreateExperimentResponse;
}

export async function comfyClear(): Promise<ComfyClearResponse> {
  const r = await fetch("/api/queue/comfy-clear", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error(`POST /api/queue/comfy-clear failed: ${r.status}${t ? `\n${t}` : ""}`);
  }
  return (await r.json()) as ComfyClearResponse;
}

export async function fetchComfyHistory(limit = 30): Promise<ComfyHistoryResponse> {
  const r = await fetch(`/api/comfy/history?limit=${encodeURIComponent(String(limit))}`);
  if (!r.ok) throw new Error(`GET /api/comfy/history failed: ${r.status}`);
  return (await r.json()) as ComfyHistoryResponse;
}

export async function fetchOrchestratorState(): Promise<OrchestratorState> {
  const r = await fetch("/api/orchestrator/state");
  if (!r.ok) throw new Error(`GET /api/orchestrator/state failed: ${r.status}`);
  return (await r.json()) as OrchestratorState;
}

export async function saveOrchestratorState(payload: OrchestratorState): Promise<OrchestratorState> {
  const r = await fetch("/api/orchestrator/state", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error(`POST /api/orchestrator/state failed: ${r.status}${t ? `\n${t}` : ""}`);
  }
  return (await r.json()) as OrchestratorState;
}

export async function saveQueueItemForLater(payload: {
  title: string;
  prompt_id?: string;
  tags?: string[];
  notes?: string;
  payload?: Record<string, unknown>;
}): Promise<Record<string, unknown>> {
  const r = await fetch("/api/orchestrator/saved-items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error(`POST /api/orchestrator/saved-items failed: ${r.status}${t ? `\n${t}` : ""}`);
  }
  return (await r.json()) as Record<string, unknown>;
}
