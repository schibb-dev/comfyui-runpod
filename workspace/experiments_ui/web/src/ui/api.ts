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
  DiscoveryWorkflowFacetsResponse,
  DiscoveryAssetLineageResponse,
  DiscoveryAssetRatingsResponse,
  DiscoveryAssetRatingsVerifyRequest,
  DiscoveryAssetRatingsVerifyResponse,
  DiscoveryRatingSamplerResponse,
  DiscoveryLibraryItemLookupResponse,
  WorkflowExplorerFactoryResponse,
  WorkflowExplorerAddAssetRequest,
  WorkflowExplorerRemoveAssetRequest,
  WorkflowExplorerAddWorkflowRequest,
  WorkflowExplorerRemoveWorkflowRequest,
  WorkflowExplorerBrowseResponse,
  ShapeFactoryMapResponse,
  ShapeFactoryMapQueueRequest,
  ShapeFactoryMapQueueResponse,
  ShapeFactoryReplayRequest,
  ShapeFactoryReplayResponse,
  ShapeFactoryDeriveRequest,
  ShapeFactoryDeriveResponse,
  ShapeFactoryUnqueueRequest,
  ShapeFactoryUnqueueResponse,
  ShapeFactoryBeginEditRequest,
  ShapeFactoryBeginEditResponse,
  ShapeFactoryFinishEditRequest,
  ShapeFactoryFinishEditResponse,
  ShapeFactoryJobEditSnapshot,
  ShapeFactoryDiscardRequest,
  ShapeFactoryDiscardResponse,
  ShapeFactoryUpdatePendingTrimRequest,
  ShapeFactoryUpdatePendingTrimResponse,
  ShapeFactoryUpdatePendingBindingRequest,
  ShapeFactoryUpdatePendingBindingResponse,
  ShapeFactoryQuarantineListResponse,
  ShapeFactoryQuarantineReleaseResponse,
  ShapeFactoryTemplatePromotionsResponse,
  InputCurationStateResponse,
  InputCurationStillsResponse,
  InputCurationEffectiveSourcesResponse,
  ShapeFactoryPromptProfile,
  ShapeFactoryMapQueueOverrides,
  FutureRunDraft,
  AssetAuditResponse,
  AssetRecoverResponse,
  SetAssetRatingResponse,
  SetAppetiteResponse,
  Appetite,
  AppetiteFacet,
  QualityAxis,
  DispositionCatalogResponse,
  DispositionSuggestResponse,
  ToggleDispositionResponse,
  RunDispositionStepResponse,
  RecordTriageCompleteResponse,
  RecordBatchTriageCompleteResponse,
  DispositionCatalogMarker,
  HomeSummaryResponse,
  HourlyScheduleStatus,
  HourlySubmitMode,
  QueueLedgerControlAction,
  QueueLedgerControlResponse,
  QueueLedgerEventsResponse,
  QueueLedgerStatus,
  QueueMovePromptRequest,
  QueueMovePromptResponse,
  WorkItemsListResponse,
  WorkItemsCreateResponse,
  WorkItemsCancelResponse,
  WorkItemsPriorityResponse,
  WorkProductsResponse,
  ShapeFactoryFamiliesResponse,
  VisionSliceCaptionsResponse,
  VisionTagJudgmentResponse,
  VisionTagJudgmentSaveResponse,
  JsonPeekResponse,
  ComfyLiveStatusResponse,
  ComfyLogsResponse,
} from "./types";

function experimentsUiStaleApiHint(): string {
  if (!import.meta.env.DEV) return "";
  const t = typeof __DEV_EXPERIMENTS_PROXY_TARGET__ !== "undefined" ? __DEV_EXPERIMENTS_PROXY_TARGET__ : "";
  if (!t) return "";
  return (
    `\n\nDev: Vite proxies /api → ${t}. Restart that process after pulling new routes (e.g. docker compose restart for :8790, or \`node scripts/experiments-ui-dev.mjs api-watch\` for repo Python on :8791).`
  );
}

export async function fetchHomeSummary(): Promise<HomeSummaryResponse> {
  const r = await fetch("/api/home/summary");
  const j = (await r.json().catch(() => ({}))) as HomeSummaryResponse & { error?: string; detail?: string };
  if (!r.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`GET /api/home/summary failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function fetchHourlySchedule(): Promise<HourlyScheduleStatus> {
  const r = await fetch("/api/shape-factory/hourly-schedule");
  const j = (await r.json().catch(() => ({}))) as HourlyScheduleStatus;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/shape-factory/hourly-schedule failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function setHourlySchedule(body: {
  interval_minutes?: number;
  enabled?: boolean;
  submit_mode?: HourlySubmitMode | string;
  comfy_queue_min?: number;
  comfy_queue_max?: number;
  pending_queue_max?: number;
  mark_tick?: boolean;
}): Promise<HourlyScheduleStatus> {
  const r = await fetch("/api/shape-factory/hourly-schedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as HourlyScheduleStatus;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/shape-factory/hourly-schedule failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

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

export async function fetchWorkflowExplorerFactory(): Promise<WorkflowExplorerFactoryResponse> {
  const r = await fetch("/api/workflow-explorer/factory");
  if (!r.ok) throw new Error(`GET /api/workflow-explorer/factory failed: ${r.status}`);
  return (await r.json()) as WorkflowExplorerFactoryResponse;
}

export async function fetchShapeFactoryMap(opts?: {
  members_limit?: number;
  jobs_limit?: number;
  family?: string;
  skip_queue?: boolean;
}): Promise<ShapeFactoryMapResponse> {
  const sp = new URLSearchParams();
  if (opts?.members_limit != null && opts.members_limit > 0) {
    sp.set("members_limit", String(opts.members_limit));
  }
  if (opts?.jobs_limit != null && opts.jobs_limit > 0) {
    sp.set("jobs_limit", String(opts.jobs_limit));
  }
  if (opts?.family) sp.set("family", opts.family);
  if (opts?.skip_queue) sp.set("skip_queue", "1");
  const qs = sp.toString();
  const r = await fetch(`/api/shape-factory/map${qs ? `?${qs}` : ""}`);
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryMapResponse & { error?: string; detail?: string };
  if (!r.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`GET /api/shape-factory/map failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function fetchShapeFactoryPromptProfile(path: string): Promise<ShapeFactoryPromptProfile> {
  const sp = new URLSearchParams();
  sp.set("path", path);
  const r = await fetch(`/api/shape-factory/prompt-profile?${sp.toString()}`);
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryPromptProfile & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/shape-factory/prompt-profile failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchShapeFactoryFamilies(): Promise<ShapeFactoryFamiliesResponse> {
  const r = await fetch("/api/shape-factory/families");
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryFamiliesResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/shape-factory/families failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchShapeFactoryWorkProducts(opts?: {
  limit?: number;
  hourlyOnly?: boolean;
  family?: string;
}): Promise<WorkProductsResponse> {
  const sp = new URLSearchParams();
  if (opts?.limit != null) sp.set("limit", String(opts.limit));
  if (opts?.hourlyOnly === false) sp.set("hourly_only", "0");
  if (opts?.family) sp.set("family", opts.family);
  const qs = sp.toString();
  const r = await fetch(`/api/shape-factory/work-products${qs ? `?${qs}` : ""}`);
  const j = (await r.json().catch(() => ({}))) as WorkProductsResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/shape-factory/work-products failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchShapeFactoryQuarantine(opts?: {
  status?: "quarantined" | "released" | "ok" | "all";
}): Promise<ShapeFactoryQuarantineListResponse> {
  const sp = new URLSearchParams();
  sp.set("status", opts?.status || "quarantined");
  const r = await fetch(`/api/shape-factory/quarantine?${sp.toString()}`);
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryQuarantineListResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/shape-factory/quarantine failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function releaseShapeFactoryQuarantine(body: {
  workflow_path?: string;
  workflow_name?: string;
  note?: string;
}): Promise<ShapeFactoryQuarantineReleaseResponse> {
  const r = await fetch("/api/shape-factory/quarantine/release", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryQuarantineReleaseResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/shape-factory/quarantine/release failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchShapeFactoryTemplatePromotions(opts?: {
  include_expired?: boolean;
}): Promise<ShapeFactoryTemplatePromotionsResponse> {
  const sp = new URLSearchParams();
  if (opts?.include_expired) sp.set("include_expired", "1");
  const qs = sp.toString();
  const r = await fetch(`/api/shape-factory/template-promotions${qs ? `?${qs}` : ""}`);
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryTemplatePromotionsResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/shape-factory/template-promotions failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function setShapeFactoryTemplatePromotion(body: {
  family_slug: string;
  intents?: Array<"extend" | "vary" | "derive">;
  intent?: "extend" | "vary" | "derive";
  scope: "temporary" | "long_term";
  enabled?: boolean;
  ttl_hours?: number;
  note?: string;
  actor?: string;
}): Promise<ShapeFactoryTemplatePromotionsResponse> {
  const r = await fetch("/api/shape-factory/template-promotions/set", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryTemplatePromotionsResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/shape-factory/template-promotions/set failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchShapeFactoryInputCurationState(): Promise<InputCurationStateResponse> {
  const r = await fetch("/api/shape-factory/input-curation/state");
  const j = (await r.json().catch(() => ({}))) as InputCurationStateResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/shape-factory/input-curation/state failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchShapeFactoryInputCurationStills(opts?: {
  q?: string;
  limit?: number;
  offset?: number;
  scan?: boolean;
}): Promise<InputCurationStillsResponse> {
  const sp = new URLSearchParams();
  if (opts?.q?.trim()) sp.set("q", opts.q.trim());
  if (opts?.limit != null) sp.set("limit", String(opts.limit));
  if (opts?.offset != null) sp.set("offset", String(opts.offset));
  if (opts?.scan) sp.set("scan", "1");
  const qs = sp.toString();
  const r = await fetch(`/api/shape-factory/input-curation/stills${qs ? `?${qs}` : ""}`);
  const j = (await r.json().catch(() => ({}))) as InputCurationStillsResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/shape-factory/input-curation/stills failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchShapeFactoryInputCurationEffectiveSources(
  familySlug: string,
): Promise<InputCurationEffectiveSourcesResponse> {
  const sp = new URLSearchParams({ family_slug: familySlug });
  const r = await fetch(`/api/shape-factory/input-curation/effective-sources?${sp.toString()}`);
  const j = (await r.json().catch(() => ({}))) as InputCurationEffectiveSourcesResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/shape-factory/input-curation/effective-sources failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function mutateShapeFactoryInputCollection(body: {
  op: "create" | "rename" | "delete" | "add_item" | "remove_item";
  collection_id?: string;
  name?: string;
  description?: string;
  path?: string;
  note?: string;
}): Promise<InputCurationStateResponse> {
  const r = await fetch("/api/shape-factory/input-curation/collections", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as InputCurationStateResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/shape-factory/input-curation/collections failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function mutateShapeFactoryInputBindings(body: {
  op: "attach" | "detach" | "set";
  family_slug: string;
  collection_id?: string;
  collection_ids?: string[];
}): Promise<InputCurationStateResponse> {
  const r = await fetch("/api/shape-factory/input-curation/bindings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as InputCurationStateResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/shape-factory/input-curation/bindings failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchVisionSliceCaptions(): Promise<VisionSliceCaptionsResponse> {
  const r = await fetch("/api/vision/slice-captions");
  const j = (await r.json().catch(() => ({}))) as VisionSliceCaptionsResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/vision/slice-captions failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchVisionTagJudgment(): Promise<VisionTagJudgmentResponse> {
  const r = await fetch("/api/vision/tag-judgment");
  const raw = await r.text();
  let j: VisionTagJudgmentResponse = { ok: false };
  try {
    j = JSON.parse(raw) as VisionTagJudgmentResponse;
  } catch {
    j = { ok: false };
  }
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ") || raw.slice(0, 240).trim();
    throw new Error(
      `GET /api/vision/tag-judgment failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function saveVisionTagJudgment(body: {
  sample_id: string;
  asset_relpath?: string;
  t0?: number;
  t1?: number;
  slice?: string;
  labels?: Record<string, "good" | "bad">;
  important?: string[];
  missing?: string[];
  skipped?: boolean;
}): Promise<VisionTagJudgmentSaveResponse> {
  const r = await fetch("/api/vision/tag-judgment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as VisionTagJudgmentSaveResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/vision/tag-judgment failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchShapeFactoryJsonPeek(path: string): Promise<JsonPeekResponse> {
  const sp = new URLSearchParams({ path });
  const r = await fetch(`/api/shape-factory/json-peek?${sp}`);
  const j = (await r.json().catch(() => ({}))) as JsonPeekResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/shape-factory/json-peek failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

/** URL for the latest Comfy latent preview frame (poll with cache-bust query). */
export function comfyLivePreviewUrl(promptId: string, bust?: number | string, frame?: number): string {
  const sp = new URLSearchParams({ prompt_id: promptId });
  if (bust != null) sp.set("t", String(bust));
  if (frame != null && Number.isFinite(frame)) sp.set("frame", String(frame));
  return `/api/comfy/live-preview?${sp}`;
}

export async function fetchComfyLiveStatus(promptIds: string[]): Promise<ComfyLiveStatusResponse> {
  const sp = new URLSearchParams();
  const ids = promptIds.map((p) => p.trim()).filter(Boolean);
  if (ids.length) sp.set("prompt_id", ids.join(","));
  const r = await fetch(`/api/comfy/live-status?${sp}`);
  const j = (await r.json().catch(() => ({}))) as ComfyLiveStatusResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/comfy/live-status failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchComfyLogs(opts?: { tail?: number }): Promise<ComfyLogsResponse> {
  const sp = new URLSearchParams();
  if (opts?.tail != null) sp.set("tail", String(opts.tail));
  const qs = sp.toString();
  const r = await fetch(`/api/comfy/logs${qs ? `?${qs}` : ""}`);
  const j = (await r.json().catch(() => ({}))) as ComfyLogsResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`GET /api/comfy/logs failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  if (!Array.isArray(j.entries)) j.entries = [];
  return j;
}

export async function queueShapeFactoryCombo(req: ShapeFactoryMapQueueRequest): Promise<ShapeFactoryMapQueueResponse> {
  const r = await fetch("/api/shape-factory/queue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryMapQueueResponse & {
    error?: string;
    detail?: string;
  };
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/shape-factory/queue failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function fetchAssetAudit(family: string): Promise<AssetAuditResponse> {
  const sp = new URLSearchParams();
  sp.set("family", family);
  const r = await fetch(`/api/discovery/asset-audit?${sp.toString()}`);
  const j = (await r.json().catch(() => ({}))) as AssetAuditResponse & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`GET /api/discovery/asset-audit failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function recoverAssets(body: {
  family?: string;
  names?: string[];
  allow_remote?: boolean;
}): Promise<AssetRecoverResponse> {
  const r = await fetch("/api/discovery/asset-recover", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as AssetRecoverResponse & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/discovery/asset-recover failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function setAssetRating(body: {
  relpath: string;
  stars: number;
  axis?: QualityAxis;
}): Promise<SetAssetRatingResponse> {
  const r = await fetch("/api/discovery/asset-ratings/set", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as SetAssetRatingResponse & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/discovery/asset-ratings/set failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function setAssetAppetite(body: {
  relpath: string;
  appetite: Appetite | "";
  facet?: AppetiteFacet;
  job_key?: string;
  family_slug?: string;
}): Promise<SetAppetiteResponse> {
  const r = await fetch("/api/discovery/asset-appetite/set", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as SetAppetiteResponse & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/discovery/asset-appetite/set failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function fetchDispositionCatalog(): Promise<DispositionCatalogResponse> {
  const r = await fetch("/api/discovery/disposition-catalog");
  const j = (await r.json().catch(() => ({}))) as DispositionCatalogResponse & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, (j as { detail?: string }).detail].filter(Boolean).join(": ");
    throw new Error(`GET /api/discovery/disposition-catalog failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function saveDispositionCatalog(body: {
  markers?: DispositionCatalogMarker[];
  promotion_rules?: Record<string, unknown>;
}): Promise<DispositionCatalogResponse> {
  const r = await fetch("/api/discovery/disposition-catalog", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as DispositionCatalogResponse & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, (j as { detail?: string }).detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/discovery/disposition-catalog failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function fetchDispositionSuggest(opts: {
  relpath?: string;
  quality?: number | null;
  appetite?: Appetite | null;
  facet?: AppetiteFacet;
  predicted_score?: number;
  explicit_quality_missing?: boolean;
}): Promise<DispositionSuggestResponse> {
  const sp = new URLSearchParams();
  if (opts.relpath) sp.set("relpath", opts.relpath);
  if (opts.quality != null) sp.set("quality", String(opts.quality));
  if (opts.appetite) sp.set("appetite", opts.appetite);
  if (opts.facet) sp.set("facet", opts.facet);
  if (opts.predicted_score != null) sp.set("predicted_score", String(opts.predicted_score));
  if (opts.explicit_quality_missing) sp.set("explicit_quality_missing", "1");
  const r = await fetch(`/api/discovery/disposition-suggest?${sp.toString()}`);
  const j = (await r.json().catch(() => ({}))) as DispositionSuggestResponse & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, (j as { detail?: string }).detail].filter(Boolean).join(": ");
    throw new Error(`GET /api/discovery/disposition-suggest failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function toggleAssetDisposition(body: {
  relpath: string;
  marker: string;
  on?: boolean;
  note?: string;
  modifiers?: string[];
  quality?: number;
  appetite?: Appetite | null;
  facet?: AppetiteFacet;
}): Promise<ToggleDispositionResponse> {
  const r = await fetch("/api/discovery/asset-disposition/toggle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as ToggleDispositionResponse & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, (j as { detail?: string }).detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/discovery/asset-disposition/toggle failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function runDispositionStep(body: {
  relpath: string;
  step_id: string;
  job_key?: string;
  family_slug?: string;
  facet?: AppetiteFacet;
  front?: boolean;
  overrides?: ShapeFactoryMapQueueOverrides;
  identity_anchor?: string;
  source_still?: string;
}): Promise<RunDispositionStepResponse> {
  const r = await fetch("/api/discovery/asset-disposition/run-step", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as RunDispositionStepResponse & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/discovery/asset-disposition/run-step failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export type IdentityStillCandidate = {
  id: string;
  path: string;
  relpath?: string;
  url?: string;
  thumb_url?: string;
  evidence?: string;
  label?: string;
  lineage_depth?: number;
  source_video_relpath?: string;
};

export type IdentityStillMintTarget = {
  video_relpath?: string;
  video_path?: string;
  at?: string;
  evidence?: string;
  label?: string;
  lineage_depth?: number;
  family_slug?: string;
};

export type IdentityStillCandidatesResponse = {
  ok: boolean;
  needed?: boolean;
  slots?: string[];
  recommended_id?: string | null;
  candidates?: IdentityStillCandidate[];
  mint_targets?: IdentityStillMintTarget[];
  lineage_summary?: Array<{
    relpath?: string;
    depth?: number;
    family_slug?: string | null;
    job_key?: string | null;
  }>;
  family_slug?: string | null;
  relpath?: string;
  error?: string;
  detail?: string;
};

export async function fetchIdentityStillCandidates(params: {
  relpath: string;
  job_key?: string;
  family_slug?: string;
}): Promise<IdentityStillCandidatesResponse> {
  const sp = new URLSearchParams();
  sp.set("relpath", params.relpath);
  if (params.job_key) sp.set("job_key", params.job_key);
  if (params.family_slug) sp.set("family_slug", params.family_slug);
  const r = await fetch(`/api/discovery/identity-still/candidates?${sp.toString()}`);
  const j = (await r.json().catch(() => ({}))) as IdentityStillCandidatesResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/discovery/identity-still/candidates failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function mintIdentityStill(body: {
  video_relpath?: string;
  video_path?: string;
  at?: string;
}): Promise<{ ok: boolean; candidate?: IdentityStillCandidate; sha256?: string; error?: string; detail?: string }> {
  const r = await fetch("/api/discovery/identity-still/mint", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as {
    ok?: boolean;
    candidate?: IdentityStillCandidate;
    sha256?: string;
    error?: string;
    detail?: string;
  };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/discovery/identity-still/mint failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return { ok: true, candidate: j.candidate, sha256: j.sha256 };
}

export async function listWorkItems(params: {
  source_relpath?: string;
  source_group_id?: string;
  pool?: string;
  status?: string;
  include_terminal?: boolean;
}): Promise<WorkItemsListResponse> {
  const q = new URLSearchParams();
  if (params.source_relpath) q.set("source_relpath", params.source_relpath);
  if (params.source_group_id) q.set("source_group_id", params.source_group_id);
  if (params.pool) q.set("pool", params.pool);
  if (params.status) q.set("status", params.status);
  if (params.include_terminal === false) q.set("include_terminal", "0");
  const r = await fetch(`/api/discovery/work-items?${q.toString()}`);
  const j = (await r.json().catch(() => ({}))) as WorkItemsListResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`GET /api/discovery/work-items failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function listWorkItemsPool(pool: string): Promise<WorkItemsListResponse> {
  const q = new URLSearchParams({ pool });
  const r = await fetch(`/api/discovery/work-items/pool?${q.toString()}`);
  const j = (await r.json().catch(() => ({}))) as WorkItemsListResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`GET /api/discovery/work-items/pool failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function createWorkItems(body: {
  source_relpath?: string;
  relpath?: string;
  routes?: Array<{ step_id?: string; pool?: string; priority?: string; factory_family?: string; recipe?: string }>;
  step_id?: string;
  pool?: string;
  disposition_entry?: string;
  queue_now?: boolean;
  force_new?: boolean;
}): Promise<WorkItemsCreateResponse> {
  const r = await fetch("/api/discovery/work-items/create", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as WorkItemsCreateResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/discovery/work-items/create failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

/** Single client entry for Submit compose: work-item(s) + disposition advance routes. */
export type ComposeSubmitRoute = {
  stepId: string;
  family: string;
  identityAnchor?: string | null;
};

export type ComposeSubmitAdvanceRequest = {
  mediaRelpath: string;
  when: "now" | "later";
  routes: ComposeSubmitRoute[];
  overrides?: ShapeFactoryMapQueueOverrides;
  /** Seed job when advancing from Workbench / from_job deep-link. */
  jobKey?: string | null;
};

export type ComposeSubmitAdvanceResult = {
  ok: true;
  when: "now" | "later";
  jobKeys: string[];
  workItemCount: number | null;
  message: string;
};

function advanceRouteLabel(stepId: string): string {
  if (stepId === "advance.extend") return "Extend";
  if (stepId === "advance.derive") return "Derive";
  if (stepId === "advance.vary") return "Vary";
  return stepId || "route";
}

export async function composeSubmitAdvance(req: ComposeSubmitAdvanceRequest): Promise<ComposeSubmitAdvanceResult> {
  const relpath = String(req.mediaRelpath || "").trim().replace(/\\/g, "/");
  if (!relpath) throw new Error("composeSubmitAdvance: missing mediaRelpath");
  const routes = (req.routes || [])
    .map((r) => ({
      stepId: String(r.stepId || "").trim() || "advance.extend",
      family: String(r.family || "").trim(),
      identityAnchor: r.identityAnchor ? String(r.identityAnchor).trim() : "",
    }))
    .filter((r) => r.family);
  if (!routes.length) throw new Error("composeSubmitAdvance: select Extend, Vary, and/or Derive with a family");
  const when = req.when === "now" ? "now" : "later";
  const jobKeySeed = String(req.jobKey || "").trim() || undefined;

  const created = await createWorkItems({
    source_relpath: relpath,
    routes: routes.map((r) => ({ step_id: r.stepId, factory_family: r.family })),
    queue_now: when === "now",
  });

  const parts: string[] = [
    when === "now" ? "Submit now" : "Submit later",
    created.count != null ? `${created.count} route(s)` : null,
  ].filter(Boolean) as string[];
  const jobKeys: string[] = [];

  for (const route of routes) {
    const res = await runDispositionStep({
      relpath,
      step_id: route.stepId,
      family_slug: route.family,
      job_key: jobKeySeed,
      front: when === "now",
      overrides: req.overrides,
      ...(route.stepId === "advance.extend" && route.identityAnchor
        ? { identity_anchor: route.identityAnchor }
        : {}),
    });
    const nested = (res.result as Record<string, unknown> | undefined) || {};
    const nestedResult = (nested.result as Record<string, unknown> | undefined) || {};
    const reason = String(nested.reason || nested.error || nestedResult.reason || "").trim();
    if (nested.ok === false) {
      throw new Error(`${advanceRouteLabel(route.stepId)}: ${reason || "submit failed"}`);
    }
    const jobKey = String(nested.job_key || nestedResult.job_key || "").trim();
    if (jobKey) jobKeys.push(jobKey);
    const clampMessage =
      String(
        (nested.trim_clamped as { message?: string } | undefined)?.message ||
          (nestedResult.trim_clamped as { message?: string } | undefined)?.message ||
          "",
      ).trim() || "";
    const fam = route.family ? `@${route.family}` : "";
    const label = advanceRouteLabel(route.stepId);
    parts.push(
      [jobKey ? `${label}${fam}→${jobKey}` : `${label}${fam}: ${res.hook || "ok"}`, clampMessage]
        .filter(Boolean)
        .join(" · "),
    );
  }

  return {
    ok: true,
    when,
    jobKeys,
    workItemCount: created.count != null ? Number(created.count) : null,
    message: parts.join(" · "),
  };
}

/** @deprecated Prefer composeSubmitAdvance — kept for single-route callers. */
export type ComposeSubmitExtendRequest = {
  mediaRelpath: string;
  family: string;
  when: "now" | "later";
  overrides?: ShapeFactoryMapQueueOverrides;
  identityAnchor?: string | null;
  stepId?: string;
  jobKey?: string | null;
};

export type ComposeSubmitExtendResult = {
  ok: true;
  when: "now" | "later";
  jobKey: string | null;
  workItemCount: number | null;
  freshCombo: boolean;
  clampMessage: string | null;
  hook: string | null;
  message: string;
};

export async function composeSubmitExtend(req: ComposeSubmitExtendRequest): Promise<ComposeSubmitExtendResult> {
  const stepId = String(req.stepId || "advance.extend").trim() || "advance.extend";
  const advanced = await composeSubmitAdvance({
    mediaRelpath: req.mediaRelpath,
    when: req.when,
    overrides: req.overrides,
    jobKey: req.jobKey,
    routes: [
      {
        stepId,
        family: req.family,
        identityAnchor: req.identityAnchor,
      },
    ],
  });
  return {
    ok: true,
    when: advanced.when,
    jobKey: advanced.jobKeys[0] || null,
    workItemCount: advanced.workItemCount,
    freshCombo: false,
    clampMessage: null,
    hook: null,
    message: advanced.message,
  };
}

export async function cancelWorkItem(body: { work_id: string; reason?: string }): Promise<WorkItemsCancelResponse> {
  const r = await fetch("/api/discovery/work-items/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as WorkItemsCancelResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/discovery/work-items/cancel failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function setWorkItemPriority(body: {
  work_id: string;
  priority: "front" | "normal" | string;
}): Promise<WorkItemsPriorityResponse> {
  const r = await fetch("/api/discovery/work-items/priority", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as WorkItemsPriorityResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/discovery/work-items/priority failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function recordAssetTriageComplete(body: { relpath: string }): Promise<RecordTriageCompleteResponse> {
  const r = await fetch("/api/discovery/asset-triage/complete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as RecordTriageCompleteResponse & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/discovery/asset-triage/complete failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function recordBatchTriageComplete(body: { relpaths: string[] }): Promise<RecordBatchTriageCompleteResponse> {
  const r = await fetch("/api/discovery/asset-triage/complete-batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as RecordBatchTriageCompleteResponse & { error?: string; detail?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/discovery/asset-triage/complete-batch failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function replayShapeFactory(req: ShapeFactoryReplayRequest): Promise<ShapeFactoryReplayResponse> {
  const r = await fetch("/api/shape-factory/replay", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryReplayResponse & { error?: string; detail?: string };
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/shape-factory/replay failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function deriveShapeFactory(req: ShapeFactoryDeriveRequest): Promise<ShapeFactoryDeriveResponse> {
  const r = await fetch("/api/shape-factory/derive", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryDeriveResponse & { error?: string; detail?: string };
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/shape-factory/derive failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function unqueueShapeFactory(req: ShapeFactoryUnqueueRequest): Promise<ShapeFactoryUnqueueResponse> {
  const r = await fetch("/api/shape-factory/unqueue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryUnqueueResponse;
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/shape-factory/unqueue failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function discardShapeFactoryJob(req: ShapeFactoryDiscardRequest): Promise<ShapeFactoryDiscardResponse> {
  const r = await fetch("/api/shape-factory/discard", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryDiscardResponse;
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/shape-factory/discard failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`);
  }
  return j;
}

export async function beginShapeFactoryEdit(
  req: ShapeFactoryBeginEditRequest,
): Promise<ShapeFactoryBeginEditResponse> {
  const r = await fetch("/api/shape-factory/begin-edit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryBeginEditResponse;
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/shape-factory/begin-edit failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function finishShapeFactoryEdit(
  req: ShapeFactoryFinishEditRequest,
): Promise<ShapeFactoryFinishEditResponse> {
  const r = await fetch("/api/shape-factory/finish-edit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryFinishEditResponse;
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/shape-factory/finish-edit failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchShapeFactoryJobEdit(opts: {
  jobKey?: string;
  jobPath?: string;
}): Promise<ShapeFactoryJobEditSnapshot> {
  const sp = new URLSearchParams();
  if (opts.jobKey) sp.set("job_key", opts.jobKey);
  if (opts.jobPath) sp.set("job_path", opts.jobPath);
  const r = await fetch(`/api/shape-factory/job-edit?${sp.toString()}`);
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryJobEditSnapshot;
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/shape-factory/job-edit failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export type {
  ShapeFactoryBeginEditRequest,
  ShapeFactoryBeginEditResponse,
  ShapeFactoryFinishEditRequest,
  ShapeFactoryFinishEditResponse,
  ShapeFactoryJobEditSnapshot,
};

export async function updatePendingShapeFactoryTrim(
  req: ShapeFactoryUpdatePendingTrimRequest,
): Promise<ShapeFactoryUpdatePendingTrimResponse> {
  const r = await fetch("/api/shape-factory/update-pending-trim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryUpdatePendingTrimResponse;
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/shape-factory/update-pending-trim failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function updatePendingShapeFactoryBinding(
  req: ShapeFactoryUpdatePendingBindingRequest,
): Promise<ShapeFactoryUpdatePendingBindingResponse> {
  const r = await fetch("/api/shape-factory/update-pending-binding", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryUpdatePendingBindingResponse;
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/shape-factory/update-pending-binding failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export type ShapeFactoryClip = {
  clip_id: string;
  parent_content_id: string;
  mark_in_s: number;
  mark_out_s: number;
  label?: string | null;
  origin?: string | null;
  notes?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  media_relpath?: string | null;
  media_basename?: string | null;
  media_url?: string | null;
  asset_kind?: string | null;
  asset_ext?: string | null;
  asset_mtime?: number | null;
  is_default?: boolean;
  duration_s?: number;
};

export type ShapeFactoryClipsListResponse = {
  ok: boolean;
  parent_content_id?: string;
  default_clip_id?: string | null;
  clips?: ShapeFactoryClip[];
  error?: string;
  detail?: string;
};

export type ShapeFactoryClipsLibraryParent = {
  media_relpath: string;
  media_basename?: string | null;
  clip_count: number;
  has_default?: boolean;
  asset_mtime?: number | null;
};

export type ShapeFactoryClipsLibraryResponse = {
  ok: boolean;
  clips?: ShapeFactoryClip[];
  count?: number;
  total?: number;
  limit?: number;
  offset?: number;
  origin_counts?: Record<string, number>;
  /** Source videos with clip counts (for by-source browse). */
  parents?: ShapeFactoryClipsLibraryParent[];
  filters?: {
    origin?: string | null;
    q?: string | null;
    defaults_only?: boolean;
    media_relpath?: string | null;
  };
  error?: string;
  detail?: string;
};

export async function listShapeFactoryClips(opts: {
  mediaRelpath?: string;
  parentContentId?: string;
}): Promise<ShapeFactoryClipsListResponse> {
  const sp = new URLSearchParams();
  if (opts.mediaRelpath) sp.set("media_relpath", opts.mediaRelpath);
  if (opts.parentContentId) sp.set("parent_content_id", opts.parentContentId);
  const r = await fetch(`/api/shape-factory/clips?${sp.toString()}`);
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryClipsListResponse;
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`GET /api/shape-factory/clips failed: ${r.status}${detail ? `: ${detail}` : ""}`);
  }
  return j;
}

export type ShapeFactoryClipDerivedItem = {
  job_key: string;
  family_slug?: string | null;
  status?: string | null;
  created_at?: string | null;
  deposited_at?: string | null;
  source_clip_id: string;
  clip_label?: string | null;
  clip_mark_in_s?: number | null;
  clip_mark_out_s?: number | null;
  source_media_relpath?: string | null;
  source_media_basename?: string | null;
  vhs_source?: string | null;
  output_relpath?: string | null;
  output_basename?: string | null;
  output_url?: string | null;
  output_thumb_url?: string | null;
  is_hourly?: boolean;
};

export type ShapeFactoryClipsDerivedResponse = {
  ok: boolean;
  items?: ShapeFactoryClipDerivedItem[];
  count?: number;
  total?: number;
  scanned_jobs?: number;
  matched_jobs?: number;
  filters?: {
    clip_id?: string | null;
    media_relpath?: string | null;
    include_without_output?: boolean;
  };
  error?: string;
  detail?: string;
};

export async function listShapeFactoryClipsDerived(opts?: {
  limit?: number;
  clipId?: string | null;
  mediaRelpath?: string | null;
  includePending?: boolean;
}): Promise<ShapeFactoryClipsDerivedResponse> {
  const sp = new URLSearchParams();
  if (opts?.limit != null) sp.set("limit", String(opts.limit));
  if (opts?.clipId?.trim()) sp.set("clip_id", opts.clipId.trim());
  if (opts?.mediaRelpath?.trim()) sp.set("media_relpath", opts.mediaRelpath.trim().replace(/\\/g, "/"));
  if (opts?.includePending) sp.set("include_pending", "1");
  const qs = sp.toString();
  const r = await fetch(`/api/shape-factory/clips/derived${qs ? `?${qs}` : ""}`);
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryClipsDerivedResponse;
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`GET /api/shape-factory/clips/derived failed: ${r.status}${detail ? `: ${detail}` : ""}`);
  }
  return j;
}

export async function listShapeFactoryClipsLibrary(opts?: {
  limit?: number;
  offset?: number;
  origin?: string | null;
  q?: string | null;
  defaultsOnly?: boolean;
  mediaRelpath?: string | null;
}): Promise<ShapeFactoryClipsLibraryResponse> {
  const sp = new URLSearchParams();
  if (opts?.limit != null) sp.set("limit", String(opts.limit));
  if (opts?.offset != null) sp.set("offset", String(opts.offset));
  if (opts?.origin?.trim()) sp.set("origin", opts.origin.trim());
  if (opts?.q?.trim()) sp.set("q", opts.q.trim());
  if (opts?.defaultsOnly) sp.set("defaults_only", "1");
  if (opts?.mediaRelpath?.trim()) sp.set("media_relpath", opts.mediaRelpath.trim().replace(/\\/g, "/"));
  const qs = sp.toString();
  const r = await fetch(`/api/shape-factory/clips/library${qs ? `?${qs}` : ""}`);
  const j = (await r.json().catch(() => ({}))) as ShapeFactoryClipsLibraryResponse;
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`GET /api/shape-factory/clips/library failed: ${r.status}${detail ? `: ${detail}` : ""}`);
  }
  return j;
}

export async function mutateShapeFactoryClip(body: Record<string, unknown>): Promise<{
  ok: boolean;
  clip?: ShapeFactoryClip;
  default_clip_id?: string | null;
  error?: string;
  detail?: string;
}> {
  const r = await fetch("/api/shape-factory/clips", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as {
    ok: boolean;
    clip?: ShapeFactoryClip;
    default_clip_id?: string | null;
    error?: string;
    detail?: string;
  };
  if (!r.ok || !j.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/shape-factory/clips failed: ${r.status}${detail ? `: ${detail}` : ""}`);
  }
  return j;
}

async function postWorkflowExplorerFactoryUpdate(
  path: "/api/workflow-explorer/factory/assets" | "/api/workflow-explorer/factory/workflows",
  body: Record<string, unknown>,
): Promise<WorkflowExplorerFactoryResponse> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as WorkflowExplorerFactoryResponse & { error?: string; detail?: string };
  if (!r.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST ${path} failed: ${r.status}${detail ? `: ${detail}` : ""}`);
  }
  return j;
}

export async function addWorkflowExplorerAsset(
  req: WorkflowExplorerAddAssetRequest,
): Promise<WorkflowExplorerFactoryResponse> {
  return postWorkflowExplorerFactoryUpdate("/api/workflow-explorer/factory/assets", { op: "add", ...req });
}

export async function removeWorkflowExplorerAsset(
  req: WorkflowExplorerRemoveAssetRequest,
): Promise<WorkflowExplorerFactoryResponse> {
  return postWorkflowExplorerFactoryUpdate("/api/workflow-explorer/factory/assets", { op: "remove", ...req });
}

export async function addWorkflowExplorerWorkflow(
  req: WorkflowExplorerAddWorkflowRequest,
): Promise<WorkflowExplorerFactoryResponse> {
  return postWorkflowExplorerFactoryUpdate("/api/workflow-explorer/factory/workflows", { op: "add", ...req });
}

export async function removeWorkflowExplorerWorkflow(
  req: WorkflowExplorerRemoveWorkflowRequest,
): Promise<WorkflowExplorerFactoryResponse> {
  return postWorkflowExplorerFactoryUpdate("/api/workflow-explorer/factory/workflows", { op: "remove", ...req });
}

export async function fetchWorkflowExplorerBrowse(opts?: {
  root?: string;
  dir?: string;
  kind?: "asset" | "workflow" | "all";
  media_type?: "all" | "image" | "video";
  q?: string;
  limit?: number;
}): Promise<WorkflowExplorerBrowseResponse> {
  const sp = new URLSearchParams();
  if (opts?.root) sp.set("root", opts.root);
  if (opts?.dir) sp.set("dir", opts.dir);
  if (opts?.kind) sp.set("kind", opts.kind);
  if (opts?.media_type) sp.set("media_type", opts.media_type);
  if (opts?.q) sp.set("q", opts.q);
  if (opts?.limit) sp.set("limit", String(opts.limit));
  const qs = sp.toString();
  const r = await fetch(`/api/workflow-explorer/factory/browse${qs ? `?${qs}` : ""}`);
  if (!r.ok) throw new Error(`GET /api/workflow-explorer/factory/browse failed: ${r.status}`);
  return (await r.json()) as WorkflowExplorerBrowseResponse;
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
        "Experiments API is outdated (missing GET /api/discovery/embed-api-prompt). Restart the server that handles /api.\n\n" +
          "npm run ui:dev:start\n" +
          "npm run ui:dev:all\n" +
          "npm run ui:dev:api\n" +
          "npm run ui:dev:api:watch   (with npm run ui:dev:vite in another terminal)\n" +
          "npm run ui:dev:vite\n" +
          "Docker: npm run restart" +
          experimentsUiStaleApiHint(),
      );
    }
    throw new Error(`GET /api/discovery/embed-api-prompt failed: ${r.status}: ${JSON.stringify(j)}`);
  }
  return j;
}

export async function fetchDiscoveryWorkflowFacets(relpath: string): Promise<DiscoveryWorkflowFacetsResponse> {
  const sp = new URLSearchParams();
  sp.set("relpath", relpath);
  const r = await fetch(`/api/discovery/workflow-facets?${sp.toString()}`);
  const j = (await r.json()) as DiscoveryWorkflowFacetsResponse & { error?: string; path?: string };
  if (!r.ok) {
    const isStale =
      r.status === 404 &&
      j &&
      typeof j === "object" &&
      j.error === "unknown_api_route" &&
      String(j.path || "").includes("workflow-facets");
    if (isStale) {
      throw new Error(
        "Experiments API is outdated (missing GET /api/discovery/workflow-facets). Restart the process that serves /api " +
          "(the Vite proxy target in EXPERIMENTS_UI_PROXY_TARGET, often the ComfyUI container or python experiments_ui_server.py).\n\n" +
          "Local dev: npm run ui:dev:api   or   npm run ui:dev:api:watch   (and restart if not using watch)\n" +
          "Combined: npm run ui:dev:start / ui:dev:all\n" +
          "Docker: restart the service that binds the Experiments API port (e.g. npm run restart)." +
          experimentsUiStaleApiHint(),
      );
    }
    throw new Error(`GET /api/discovery/workflow-facets failed: ${r.status}: ${JSON.stringify(j)}`);
  }
  return j;
}

export async function fetchDiscoveryLibraryItem(opts: {
  groupId?: string;
  relpath?: string;
}): Promise<DiscoveryLibraryItemLookupResponse> {
  const sp = new URLSearchParams();
  if (opts.groupId?.trim()) sp.set("group_id", opts.groupId.trim());
  if (opts.relpath?.trim()) sp.set("relpath", opts.relpath.trim());
  const r = await fetch(`/api/discovery/library/item?${sp.toString()}`);
  const j = (await r.json()) as DiscoveryLibraryItemLookupResponse & { error?: string };
  if (!r.ok) {
    throw new Error(`GET /api/discovery/library/item failed: ${r.status}${j.error ? `: ${j.error}` : ""}`);
  }
  return j;
}

export async function fetchDiscoveryAssetLineage(
  relpath: string,
  opts?: {
    maxDepth?: number;
    persist?: boolean;
    peekGroupId?: string;
    graphOnly?: boolean;
    inferParents?: boolean;
    inferChildren?: boolean;
  }
): Promise<DiscoveryAssetLineageResponse> {
  const sp = new URLSearchParams();
  sp.set("relpath", relpath);
  if (opts?.maxDepth != null && Number.isFinite(opts.maxDepth)) sp.set("max_depth", String(opts.maxDepth));
  if (opts?.persist) sp.set("persist", "1");
  if (opts?.graphOnly) sp.set("graph_only", "1");
  if (opts?.inferParents === false) sp.set("infer_parents", "0");
  else if (opts?.inferParents === true) sp.set("infer_parents", "1");
  else if (opts?.graphOnly) sp.set("infer_parents", "0");
  if (opts?.inferChildren === true) sp.set("infer_children", "1");
  else if (opts?.inferChildren === false) sp.set("infer_children", "0");
  if (opts?.peekGroupId) sp.set("peek_group_id", opts.peekGroupId);
  const r = await fetch(`/api/discovery/asset-lineage?${sp.toString()}`);
  const j = (await r.json()) as DiscoveryAssetLineageResponse & { error?: string; path?: string };
  if (!r.ok) {
    const isStale =
      r.status === 404 &&
      j &&
      typeof j === "object" &&
      j.error === "unknown_api_route" &&
      String((j as { path?: string }).path || "").includes("asset-lineage");
    if (isStale) {
      throw new Error(
        "Experiments API is outdated (missing GET /api/discovery/asset-lineage). Restart the process that serves /api." +
          experimentsUiStaleApiHint(),
      );
    }
    throw new Error(`GET /api/discovery/asset-lineage failed: ${r.status}: ${JSON.stringify(j)}`);
  }
  return j;
}

export async function fetchDiscoveryAssetRatings(relpath: string): Promise<DiscoveryAssetRatingsResponse> {
  const sp = new URLSearchParams();
  sp.set("relpath", relpath);
  const r = await fetch(`/api/discovery/asset-ratings?${sp.toString()}`);
  const j = (await r.json()) as DiscoveryAssetRatingsResponse & { error?: string; path?: string };
  if (!r.ok) {
    const isStale =
      r.status === 404 &&
      j &&
      typeof j === "object" &&
      j.error === "unknown_api_route" &&
      String((j as { path?: string }).path || "").includes("asset-ratings");
    if (isStale) {
      throw new Error(
        "Experiments API is outdated (missing GET /api/discovery/asset-ratings). Restart the process that serves /api." +
          experimentsUiStaleApiHint(),
      );
    }
    throw new Error(`GET /api/discovery/asset-ratings failed: ${r.status}: ${JSON.stringify(j)}`);
  }
  return j;
}

export async function fetchDiscoveryRatingSampler(opts?: {
  refresh?: boolean;
  limit?: number;
  minPredicted?: number;
  mode?: "mixed" | "random" | "search" | "latest" | string;
  query?: string;
  includeDone?: boolean;
}): Promise<DiscoveryRatingSamplerResponse> {
  const sp = new URLSearchParams();
  if (opts?.refresh) sp.set("refresh", "1");
  if (opts?.limit != null) sp.set("limit", String(opts.limit));
  if (opts?.minPredicted != null) sp.set("min_predicted", String(opts.minPredicted));
  if (opts?.mode) sp.set("mode", String(opts.mode));
  if (opts?.query != null && String(opts.query).trim()) sp.set("q", String(opts.query).trim());
  if (opts?.includeDone) sp.set("include_done", "1");
  const r = await fetch(`/api/discovery/rating-sampler?${sp.toString()}`);
  const j = (await r.json()) as DiscoveryRatingSamplerResponse & { error?: string; path?: string };
  if (!r.ok) {
    const isStale =
      r.status === 404 &&
      j &&
      typeof j === "object" &&
      j.error === "unknown_api_route" &&
      String((j as { path?: string }).path || "").includes("rating-sampler");
    if (isStale) {
      throw new Error(
        "Experiments API is outdated (missing GET /api/discovery/rating-sampler). Restart the process that serves /api." +
          experimentsUiStaleApiHint(),
      );
    }
    throw new Error(`GET /api/discovery/rating-sampler failed: ${r.status}: ${JSON.stringify(j)}`);
  }
  return j;
}

export type DiscoveryEnsureThumbResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  relpath?: string;
  thumb_relpath?: string | null;
  thumb_url?: string | null;
  created?: boolean;
  skipped?: boolean;
  reason?: string;
};

export async function ensureDiscoveryThumb(opts: {
  relpath: string;
  force?: boolean;
}): Promise<DiscoveryEnsureThumbResponse> {
  const r = await fetch("/api/discovery/ensure-thumb", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ relpath: opts.relpath, force: Boolean(opts.force) }),
  });
  const j = (await r.json()) as DiscoveryEnsureThumbResponse & { path?: string };
  if (!r.ok) {
    const isStale =
      r.status === 404 &&
      j &&
      typeof j === "object" &&
      (j as { error?: string }).error === "unknown_api_route" &&
      String((j as { path?: string }).path || "").includes("ensure-thumb");
    if (isStale) {
      throw new Error(
        "Experiments API is outdated (missing POST /api/discovery/ensure-thumb). Restart the process that serves /api." +
          experimentsUiStaleApiHint(),
      );
    }
    throw new Error(`POST /api/discovery/ensure-thumb failed: ${r.status}: ${JSON.stringify(j)}`);
  }
  return j;
}

export async function postDiscoveryAssetRatingsVerify(
  body: DiscoveryAssetRatingsVerifyRequest
): Promise<DiscoveryAssetRatingsVerifyResponse> {
  const r = await fetch("/api/discovery/asset-ratings/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json()) as DiscoveryAssetRatingsVerifyResponse & { error?: string; path?: string };
  if (!r.ok) {
    const isStale =
      r.status === 404 &&
      j &&
      typeof j === "object" &&
      j.error === "unknown_api_route" &&
      String((j as { path?: string }).path || "").includes("asset-ratings");
    if (isStale) {
      throw new Error(
        "Experiments API is outdated (missing POST /api/discovery/asset-ratings/verify). Restart the process that serves /api." +
          experimentsUiStaleApiHint(),
      );
    }
    throw new Error(`POST /api/discovery/asset-ratings/verify failed: ${r.status}: ${JSON.stringify(j)}`);
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

export async function moveQueuePrompt(req: QueueMovePromptRequest): Promise<QueueMovePromptResponse> {
  const r = await fetch("/api/queue/move-prompt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  const j = (await r.json().catch(() => ({}))) as QueueMovePromptResponse & { error?: string };
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(`POST /api/queue/move-prompt failed: ${r.status}${detail ? `: ${detail}` : ""}`);
  }
  return j;
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

export async function fetchQueueLedgerStatus(): Promise<QueueLedgerStatus> {
  const r = await fetch("/api/queue/ledger-status");
  const j = (await r.json().catch(() => ({}))) as QueueLedgerStatus;
  if (!r.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/queue/ledger-status failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function fetchQueueLedgerEvents(limit = 30): Promise<QueueLedgerEventsResponse> {
  const r = await fetch(`/api/queue/ledger-events?limit=${encodeURIComponent(String(limit))}`);
  const j = (await r.json().catch(() => ({}))) as QueueLedgerEventsResponse;
  if (!r.ok) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `GET /api/queue/ledger-events failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
}

export async function setQueueLedgerControl(
  action: QueueLedgerControlAction,
): Promise<QueueLedgerControlResponse> {
  const r = await fetch("/api/queue/ledger-control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const j = (await r.json().catch(() => ({}))) as QueueLedgerControlResponse;
  if (!r.ok || j.ok === false) {
    const detail = [j.error, j.detail].filter(Boolean).join(": ");
    throw new Error(
      `POST /api/queue/ledger-control failed: ${r.status}${detail ? `: ${detail}` : ""}${experimentsUiStaleApiHint()}`,
    );
  }
  return j;
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
