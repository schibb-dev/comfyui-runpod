import type {
  ExperimentRunsResponse,
  ExperimentsResponse,
  QueueResponse,
  RequeueRunRequest,
  RequeueRunResponse,
  ComfyCancelRequest,
  ComfyCancelResponse,
  ComfyClearResponse,
  MultiRunsResponse,
  NextExperimentRequest,
  NextExperimentResponse,
  WipResponse,
  CreateExperimentRequest,
  CreateExperimentResponse,
} from "./types";

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
