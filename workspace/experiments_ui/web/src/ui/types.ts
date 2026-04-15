export type RunStatus = "not_submitted" | "submitted" | "complete";

/** Display state for a run in the UI (finished / waiting / queued / in process). */
export type RunDisplayStatus = "finished" | "waiting" | "queued" | "in_process";

export type ExperimentSummary = {
  exp_id: string;
  created_at?: string;
  /** Original source image path (for grouping). */
  source_image?: string;
  base_mp4?: string;
  fixed_seed?: number;
  fixed_duration_sec?: number;
  sweep?: Record<string, unknown>;
  run_counts?: {
    total: number;
    complete: number;
    submitted: number;
    not_submitted: number;
  };
};

/** Cached indices for navigation: same-source experiments and output media -> run */
export type ExperimentsRelations = {
  by_base_mp4: Record<string, string[]>;
  output_to_run: Record<string, { exp_id: string; run_id: string }>;
};

export type ExperimentsResponse = {
  experiments: ExperimentSummary[];
  relations?: ExperimentsRelations;
};

export type RunOutput = {
  node_id: string;
  kind: string;
  filename: string;
  subfolder?: string;
  format?: string;
  type?: string;
  frame_rate?: number;
  workflow?: string;
  fullpath?: string;
  relpath: string;
  url?: string;
};

export type RunsItem = {
  exp_id: string;
  run_id: string;
  status: RunStatus;
  status_str?: string | null;
  prompt_id?: string | null;
  status_live?: Record<string, unknown> | null;
  params: Record<string, unknown>;
  metrics?: Record<string, unknown> | null;
  outputs: RunOutput[];
  primary_video?: { relpath?: string | null; url?: string | null };
  primary_image?: { relpath?: string | null; url?: string | null };
  node_errors?: unknown;
  experiment?: ExperimentSummary;
};

export type ExperimentRunsResponse = {
  exp_id: string;
  manifest?: Record<string, unknown>;
  runs: RunsItem[];
};

export type MultiRunsResponse = {
  exp_ids: string[];
  experiments: Record<string, unknown>;
  runs: RunsItem[];
};

export type QueueComfyItem = {
  prompt_id?: string | null;
  raw?: unknown;
  external: boolean;
  exp_id?: string | null;
  run_id?: string | null;
  workflow_name?: string | null;
  input_media_relpath?: string | null;
  input_media_url?: string | null;
  input_media_kind?: "image" | "video" | null;
  key_params?: Record<string, unknown>;
};

export type QueueResponse = {
  experiments: RunsItem[];
  comfyui: {
    running: QueueComfyItem[];
    pending: QueueComfyItem[];
    raw: Record<string, unknown>;
  };
};

export type ComfyHistoryItem = {
  prompt_id: string;
  status: string;
  primary_video_url?: string | null;
  primary_image_url?: string | null;
  outputs: RunOutput[];
};

export type ComfyHistoryResponse = {
  items: ComfyHistoryItem[];
};

export type RequeueRunRequest = { exp_id: string; run_id: string; front?: boolean };
export type RequeueRunResponse = { ok: boolean; exp_id: string; run_id: string; front: boolean; submit?: unknown };

export type ComfyCancelRequest = { prompt_id: string; kind: "pending" | "running" };
export type ComfyCancelResponse = { ok: boolean; kind: "pending" | "running"; prompt_id: string; result?: unknown };

export type ComfyClearResponse = { ok: boolean; result?: unknown };

export type OrchestratorProject = {
  id: string;
  name: string;
  description?: string;
  defaultQueueId?: string | null;
  workflowIds: string[];
  collectionIds: string[];
  pipelineIds: string[];
};

export type OrchestratorCollectionMedia = {
  path: string;
  type: "image" | "video";
  title?: string;
};

export type OrchestratorCollection = {
  id: string;
  name: string;
  media: OrchestratorCollectionMedia[];
  tags?: string[];
};

export type OrchestratorWorkflowRef = {
  id: string;
  name: string;
  path: string;
};

export type OrchestratorStepRule = {
  type: string;
  config: Record<string, unknown>;
};

export type OrchestratorPipelineStep = {
  id: string;
  workflowRefId: string;
  inputCollectionId?: string;
  inputFromStepId?: string;
  rules: OrchestratorStepRule[];
};

export type OrchestratorPipeline = {
  id: string;
  name: string;
  projectId?: string;
  steps: OrchestratorPipelineStep[];
};

export type OrchestratorQueueRule = {
  type: string;
  config: Record<string, unknown>;
};

export type OrchestratorQueue = {
  id: string;
  name: string;
  rules: OrchestratorQueueRule[];
};

export type OrchestratorSavedItem = {
  id: string;
  prompt_id?: string;
  created_at: string;
  title: string;
  tags: string[];
  notes?: string;
  payload: Record<string, unknown>;
};

export type OrchestratorState = {
  projects: OrchestratorProject[];
  collections: OrchestratorCollection[];
  workflows: OrchestratorWorkflowRef[];
  pipelines: OrchestratorPipeline[];
  queues: OrchestratorQueue[];
  saved_items: OrchestratorSavedItem[];
};

export type NextExperimentRequest = {
  anchor: { exp_id: string; run_id: string };
  exp_id?: string;
  out_root?: string;
  seed?: number;
  duration_sec?: number;
  baseline_first?: boolean;
  max_runs?: number;
  server?: string;
  submit_all?: boolean;
  no_wait?: boolean;
  sweep: Record<string, unknown>;
};

export type NextExperimentResponse = {
  ok: boolean;
  exp_id: string;
  exp_dir: string;
  seed: number;
  duration_sec: number;
  queued: boolean;
  sweep: Record<string, unknown>;
  anchor?: { exp_id: string; run_id: string; base_mp4_relpath?: string };
  stdout?: string;
  stderr?: string;
};

// Unified create-experiment source (WIP video or run)
export type CreateSource =
  | { type: "wip"; relpath: string; videoName: string }
  | { type: "run"; run: RunsItem; relpath: string; videoName: string };

// WIP browser + create experiment from base_mp4
export type WipDateDir = { name: string; path: string; date: string };
export type WipMediaEntry = { name: string; path: string; relpath: string; size: number; mtime: number };
export type WipResponse = { dates: WipDateDir[]; media: WipMediaEntry[]; dir: string };

export type CreateExperimentRequest = {
  base_mp4_relpath: string;
  exp_id?: string;
  seed: number;
  duration_sec: number;
  baseline_first?: boolean;
  max_runs?: number;
  sweep: Record<string, unknown>;
};
export type CreateExperimentResponse = {
  ok: boolean;
  exp_id: string;
  exp_dir: string;
  base_mp4_relpath: string;
  seed: number;
  duration_sec: number;
  sweep: Record<string, unknown>;
  stdout?: string;
  stderr?: string;
};

// Planned experiment (video + params) before creating via API
export type WipPlannedExperiment = {
  id: string;
  base_mp4_relpath: string;
  videoName: string;
  seed: number;
  duration_sec: number;
  baseline_first: boolean;
  max_runs: number;
  sweep: Record<string, unknown>;
};

export type DiscoveryMember = {
  relpath: string;
  name: string;
  kind: string;
};

/** One logical output: mp4 + companion png (and similar) merged on the server when they share folder + stem. */
export type DiscoveryLibraryItem = {
  group_id?: string;
  relpath: string;
  library: string;
  name: string;
  mtime: number;
  size: number;
  sha256: string;
  workflow_fingerprint?: string | null;
  class_types_preview?: string[];
  has_embedded_prompt?: boolean;
  url: string;
  video_relpath?: string | null;
  thumb_relpath?: string | null;
  video_url?: string | null;
  thumb_url?: string | null;
  members?: DiscoveryMember[];
};

export type DiscoveryLibraryResponse = {
  version: number;
  updated_at?: string;
  index_path: string;
  from_cache: boolean;
  scan_ms?: number | null;
  item_count_total?: number | null;
  item_count_filtered: number;
  truncated: boolean;
  limit: number;
  items: DiscoveryLibraryItem[];
};

