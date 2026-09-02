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

export type QueueJobGlance = {
  family_slug?: string | null;
  shape_id?: string | null;
  pick_mode?: string | null;
  step?: string | null;
  seed_mode?: string | null;
  noise_seed?: number | null;
  is_hourly?: boolean;
  prompt_profile?: string | null;
  source_name?: string | null;
  identity_name?: string | null;
  sampler_name?: string | null;
  scheduler?: string | null;
  cfg?: number | string | null;
  steps?: number | string | null;
  denoise?: number | string | null;
  /** Still-source vs video-extend workflow (for media overlay badge). */
  workflow_kind?: "image" | "extend" | null;
};

export type QueueComfyItem = {
  prompt_id?: string | null;
  raw?: unknown;
  external: boolean;
  exp_id?: string | null;
  run_id?: string | null;
  workflow_name?: string | null;
  /** Shape-factory job_key when this prompt maps to a factory job. */
  job_key?: string | null;
  queue_index?: number | null;
  /** Best-effort enqueue/first-seen time (ISO). */
  queued_at?: string | null;
  /** Best-effort last change time (ISO). */
  changed_at?: string | null;
  input_media_relpath?: string | null;
  input_media_url?: string | null;
  input_media_kind?: "image" | "video" | null;
  input_thumb_url?: string | null;
  key_params?: Record<string, unknown>;
  /** Factory Use / VHS window (skip/cap + optional mark_in/out seconds). */
  vhs_window?: {
    skip_first_frames?: number;
    frame_load_cap?: number;
    mark_in?: number;
    mark_out?: number;
  } | null;
  /** At-a-glance factory / graph fields for Queue chips. */
  glance?: QueueJobGlance | null;
  /** Decoded prompt profile for Workbench-style prompt peek. */
  prompt_profile?: WorkProductPromptProfile | null;
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
  queued_at?: string | null;
  changed_at?: string | null;
  error_message?: string | null;
  error_node?: string | null;
  /** True when Comfy said success but no image/video outputs were produced. */
  hollow_success?: boolean;
  workflow_name?: string | null;
  /** Shape-factory job_key when workflow_name maps to a factory job. */
  job_key?: string | null;
  queue_index?: number | null;
  key_params?: Record<string, unknown>;
  vhs_window?: {
    skip_first_frames?: number;
    frame_load_cap?: number;
    mark_in?: number;
    mark_out?: number;
  } | null;
  glance?: QueueJobGlance | null;
  /** Decoded prompt profile for Workbench-style prompt peek. */
  prompt_profile?: WorkProductPromptProfile | null;
  primary_video_relpath?: string | null;
  primary_image_relpath?: string | null;
  primary_video_url?: string | null;
  primary_image_url?: string | null;
  output_thumb_url?: string | null;
  input_media_relpath?: string | null;
  input_media_url?: string | null;
  input_media_kind?: "image" | "video" | null;
  input_thumb_url?: string | null;
  outputs: RunOutput[];
};

export type ComfyHistoryResponse = {
  items: ComfyHistoryItem[];
};

export type RequeueRunRequest = { exp_id: string; run_id: string; front?: boolean };
export type RequeueRunResponse = { ok: boolean; exp_id: string; run_id: string; front: boolean; submit?: unknown };

/** POST /api/queue/submit-prompt — generic Comfy graph submit (no experiment run artifacts). */
export type QueueSubmitPromptRequest = {
  prompt: Record<string, unknown>;
  front?: boolean;
  client_id?: string;
};
export type QueueSubmitPromptResponse = {
  ok: boolean;
  front: boolean;
  client_id: string;
  submit?: unknown;
};

export type ComfyCancelRequest = { prompt_id: string; kind: "pending" | "running" };
export type ComfyCancelResponse = { ok: boolean; kind: "pending" | "running"; prompt_id: string; result?: unknown };

export type QueueMovePromptRequest = {
  prompt_id: string;
  to: "front" | "back";
  client_id?: string;
};

export type QueueMovePromptResponse = {
  ok: boolean;
  prompt_id: string;
  new_prompt_id?: string | null;
  to: "front" | "back";
  moved?: boolean;
  detail?: string;
  submit?: unknown;
  factory_rebind?: {
    ok?: boolean;
    factory_job?: boolean;
    job_key?: string;
    new_prompt_id?: string;
    old_prompt_id?: string;
    error?: string;
    detail?: string;
  };
  ledger_forgot_old?: boolean;
};

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
  /** Video container frame rate when known (e.g. from metadata). */
  frame_rate?: number | null;
  members?: DiscoveryMember[];
  /** Compact rating rollup attached by the library list (explicit XMP + source-inferred + appetite). */
  ratings?: {
    rating_explicit?: number;
    rating_inferred?: number;
    rating_effective?: number;
    rating_evidence?: { n?: number; keepers_4plus?: number };
    appetite?: Appetite | null;
    appetite_facet?: AppetiteFacet | null;
  };
  disposition_markers?: string[];
  work_items_open_count?: number;
  work_items_total_count?: number;
  work_items_open?: WorkItem[];
  work_items?: WorkItem[];
};

export type WorkItemStatus = "draft" | "queued" | "running" | "done" | "failed" | "cancelled";
export type WorkItemPool = "extend" | "vary" | "refine_backlog" | "extract" | "investigate" | string;
export type WorkItemPriority = "normal" | "front";

export type WorkItem = {
  work_id: string;
  source_relpath: string;
  source_group_id?: string | null;
  pool: WorkItemPool;
  priority: WorkItemPriority;
  disposition_entry: string;
  disposition_step?: string | null;
  status: WorkItemStatus;
  created_at?: string;
  updated_at?: string;
  factory_job_key?: string | null;
  factory_family?: string | null;
  child_relpaths?: string[];
  error?: string | null;
  idempotency_key?: string;
};

export type WorkItemsListResponse = {
  ok: boolean;
  items: WorkItem[];
  count: number;
  path?: string;
  pool?: string;
  filters?: Record<string, unknown>;
  error?: string;
  detail?: string;
};

export type WorkItemsCreateResponse = {
  ok: boolean;
  item?: WorkItem;
  items?: WorkItem[];
  results?: Array<Record<string, unknown>>;
  created?: boolean;
  reused?: boolean;
  count?: number;
  upgraded?: number;
  demoted?: number;
  skipped_running?: number;
  queue_now?: boolean;
  error?: string;
  detail?: string;
};

export type WorkItemsCancelResponse = {
  ok: boolean;
  item?: WorkItem;
  already_terminal?: boolean;
  skipped_running?: boolean;
  cancelled?: boolean;
  error?: string;
  detail?: string;
};

export type WorkItemsPriorityResponse = {
  ok: boolean;
  item?: WorkItem;
  changed?: boolean;
  upgraded?: boolean;
  demoted?: boolean;
  skipped_running?: boolean;
  skipped_terminal?: boolean;
  error?: string;
  detail?: string;
};

export type DiscoveryLibraryFolder = {
  name: string;
  path_prefix: string;
  item_count: number;
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
  /** Active folder-browse prefix (empty = corpus root). */
  path_prefix?: string;
  /** Immediate child folders under path_prefix. */
  folders?: DiscoveryLibraryFolder[];
  /** Items whose parent directory equals path_prefix (not nested deeper). */
  files_in_folder?: number;
  health?: {
    generated_at?: string;
    reason?: string;
    from_cache?: boolean;
    previous_updated_at?: string | null;
    current_updated_at?: string | null;
    previous_item_count?: number | null;
    current_item_count?: number;
    summary?: {
      missing_primary?: number;
      missing_video?: number;
      missing_thumb?: number;
      orphan_sidecar?: number;
      orphan_thumb?: number;
      removed_since_previous_index?: number;
    };
    samples?: Record<string, unknown[]>;
  };
  items: DiscoveryLibraryItem[];
};

/** GET /api/discovery/embed-api-prompt — API-format prompt from PNG metadata (+ optional Comfy /workflow/convert). */
export type DiscoveryEmbedApiPromptResponse =
  | {
      ok: true;
      source: string;
      png_relpath: string;
      prompt: Record<string, unknown>;
      comfy_convert_http?: number | null;
    }
  | {
      ok: false;
      error: string;
      detail?: string;
      hint?: string;
      png_relpath?: string;
      comfy_convert_http?: number | null;
    };

/** GET /api/discovery/asset-ratings — per-asset explicit + inferred ratings with evidence. */
export type DiscoveryAssetRatingsContributor = {
  output_discovery_key?: string;
  rating?: number;
  via_source?: string;
};

export type DiscoveryAssetRatingsHumanReview = {
  verified?: boolean;
  verified_at?: string | null;
  override_rating?: number | null;
  note?: string | null;
};

export type DiscoveryAssetRatingsLensBlock = {
  inferred?: number;
  n?: number;
  keepers_4plus?: number;
  contributors?: DiscoveryAssetRatingsContributor[];
  basename?: string;
  graph_hash?: string;
  catalog_slug?: string;
  shape_id?: string;
  shape_recipe?: string;
  human?: DiscoveryAssetRatingsHumanReview;
};

export type QualityAxis = "subject_beauty" | "render_quality" | "action_quality";

export const QUALITY_AXES: readonly QualityAxis[] = [
  "subject_beauty",
  "render_quality",
  "action_quality",
] as const;

export const QUALITY_AXIS_LABELS: Record<QualityAxis, string> = {
  subject_beauty: "Subject",
  render_quality: "Render",
  action_quality: "Action",
};

export type QualityAxesMap = Partial<Record<QualityAxis, number>>;

export type DiscoveryAssetRatingsResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  query_relpath?: string;
  asset_key?: string;
  ratings_index_path?: string;
  human_verifications_path?: string;
  rating_effective?: number | null;
  basename?: string | null;
  axes?: QualityAxesMap | null;
  explicit?: {
    rating?: number;
    axes?: QualityAxesMap | null;
    axes_complete?: boolean;
    xmp?: string;
    verification?: {
      ok?: boolean;
      match?: boolean;
      xmp_on_disk?: number | null;
      index_explicit?: number | null;
      xmp_path?: string;
      xmp_mtime_iso?: string;
      error?: string;
    };
  } | null;
  as_source?: DiscoveryAssetRatingsLensBlock | null;
  workflow?: DiscoveryAssetRatingsLensBlock | null;
  recipe?: DiscoveryAssetRatingsLensBlock | null;
  sources_cited?: Array<{
    basename?: string;
    via_source?: string;
    source_inferred?: number;
    source_n?: number;
  }>;
  appetite?: Appetite | null;
  appetite_facet?: AppetiteFacet | null;
  disposition_markers?: string[];
  disposition_notes?: Record<string, string>;
  disposition_reason_detail?: Record<string, { modifiers?: string[]; note?: string }>;
  disposition_updated_at?: string | null;
  disposition_outcomes?: DispositionOutcome[];
  disposition_last_outcome?: DispositionOutcome | null;
  disposition_archived?: boolean;
  disposition_saved?: boolean;
  needs_triage?: boolean;
  last_triaged_at?: string | null;
  triage_pass_count?: number;
  work_items_open_count?: number;
  work_items_total_count?: number;
  work_items_open?: WorkItem[];
  work_items?: WorkItem[];
  index_updated_at?: string;
};

export type DispositionOutcome = {
  at?: string;
  action?: string;
  detail?: unknown;
};

export type DiscoveryAssetRatingsVerifyRequest = {
  relpath: string;
  lens: "as_source" | "workflow" | "recipe";
  verified: boolean;
  override_rating?: number | null;
  note?: string;
};

export type DiscoveryAssetRatingsVerifyResponse = {
  ok: boolean;
  error?: string;
  asset_key?: string;
  lens?: string;
  saved?: DiscoveryAssetRatingsHumanReview;
  ratings?: DiscoveryAssetRatingsResponse;
};

/** Two-axis rating: appetite ("do more WITH this") is distinct from the quality star. */
export type Appetite = "less" | "neutral" | "more" | "fast_track";
export type AppetiteFacet = "both" | "source" | "processing";

/** GET /api/discovery/rating-sampler — heuristic queue of videos to rate next. */
export type DiscoveryRatingSamplerCandidate = {
  relpath: string;
  group_id?: string;
  predicted_score?: number;
  heuristic_confidence?: number;
  evidence?: string[];
  signals?: Record<string, number | string>;
  vision_recommended?: boolean;
  vision_reasons?: string[];
  discovery_href?: string;
  /** Stratified session slice: easy_down | easy_up | middle */
  session_bucket?: "easy_down" | "easy_up" | "middle";
  /** Current appetite (direction) recorded for this asset, if any. */
  appetite?: Appetite | null;
  appetite_facet?: AppetiteFacet | null;
  disposition_markers?: string[];
  tags?: string[];
  needs_triage?: boolean;
  last_triaged_at?: string | null;
  triage_pass_count?: number;
  /** Discovery companion still when known (png/jpg/webp). */
  thumb_relpath?: string | null;
  mtime?: number;
  /** Origin / generated band inputs for the rate scrubber (from factory job). */
  extension_range?: {
    job_key?: string | null;
    pick_mode?: string | null;
    frames_before?: number | null;
    generation_frames?: number | null;
    output_frame_count?: number | null;
    overlap?: number | null;
    fps?: number | null;
  } | null;
};

export type DispositionMarkerKind = "entry" | "step" | "reason";

export type DispositionModifierMode = "none" | "exclusive" | "multi";

export type DispositionReasonModifier = {
  id: string;
  label: string;
  hint?: string;
};

export type DispositionHook =
  | "none"
  | "replay"
  | "replay_front"
  | "extend"
  | "open_trim"
  | "trash"
  | "archive"
  | "extract_frame"
  | "sampler_pin"
  | "appetite_more"
  | "set_marker";

export type DispositionCatalogMarker = {
  id: string;
  kind: DispositionMarkerKind;
  process?: string;
  label: string;
  hint?: string;
  enabled?: boolean;
  order?: number;
  hook?: DispositionHook | string;
  narrows_to?: string[];
  promote_when?: string[];
  hook_args?: Record<string, unknown>;
  modifier_mode?: DispositionModifierMode | string;
  modifiers?: DispositionReasonModifier[];
  requires_note?: boolean;
};

export type DispositionReasonDetail = {
  modifiers?: string[];
  note?: string;
};

export type DispositionPromotions = {
  promote: string[];
  secondary: string[];
  matched_rules?: string[];
};

export type DispositionCatalogResponse = {
  ok: boolean;
  catalog?: {
    version?: number;
    schema?: string;
    promotion_rules?: Record<string, unknown>;
    markers?: DispositionCatalogMarker[];
  };
  entries?: DispositionCatalogMarker[];
  steps?: DispositionCatalogMarker[];
  reasons?: DispositionCatalogMarker[];
  catalog_path?: string;
  seed_path?: string;
  error?: string;
};

export type DispositionSuggestResponse = {
  ok: boolean;
  relpath?: string;
  promotions?: DispositionPromotions;
  inputs?: Record<string, unknown>;
  error?: string;
};

export type ToggleDispositionResponse = {
  ok: boolean;
  saved?: {
    relpath?: string;
    marker?: string;
    on?: boolean;
    markers?: string[];
    notes?: Record<string, string>;
    reason_detail?: Record<string, DispositionReasonDetail>;
    cleared?: boolean;
    updated_at?: string | null;
  };
  promotions?: DispositionPromotions;
  error?: string;
};

export type RunDispositionStepResponse = {
  ok: boolean;
  step_id?: string;
  hook?: string;
  result?: Record<string, unknown>;
  work_item?: WorkItem;
  work_item_meta?: { created?: boolean; reused?: boolean };
  work_item_error?: string;
  error?: string;
  detail?: string;
};

export type RecordTriageCompleteResponse = {
  ok: boolean;
  saved?: {
    relpath?: string;
    last_triaged_at?: string;
    pass_count?: number;
    needs_triage?: boolean;
  };
  error?: string;
  detail?: string;
};

export type RecordBatchTriageCompleteResponse = {
  ok: boolean;
  committed?: Array<{ relpath?: string; pass_count?: number }>;
  skipped?: string[];
  committed_count?: number;
  skipped_count?: number;
  error?: string;
  detail?: string;
};

export type DiscoveryRatingSamplerResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  session_path?: string;
  created_at?: string;
  bootstrapped?: boolean;
  selection_mode?: "mixed" | "random" | "search" | "latest" | string;
  include_done?: boolean;
  query?: string;
  session_mix?: { easy_down?: number; easy_up?: number; middle?: number };
  request?: {
    limit?: number;
    mode?: string;
    query?: string;
    include_done?: boolean;
    min_predicted?: number;
    seed?: number;
  };
  stats?: {
    unrated_videos?: number;
    scored_pool?: number;
    selected?: number;
    bucket_easy_down?: number;
    bucket_easy_up?: number;
    bucket_middle?: number;
    vision_recommended?: number;
    vision_priority_shortlist?: number;
  };
  candidates?: DiscoveryRatingSamplerCandidate[];
  vision_priority?: DiscoveryRatingSamplerCandidate[];
  vision_gaps?: {
    reasons?: { reason: string; count: number }[];
    guidance?: string[];
  };
  next_steps?: string[];
};

/** GET /api/discovery/asset-audit — missing load_image source refs for a family. */
export type AssetAuditMissing = {
  basename: string;
  sha?: string | null;
  slot?: string;
  job_key?: string | null;
  output?: string | null;
};

export type AssetAuditResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  family?: string;
  scanned?: number;
  missing_count?: number;
  missing?: AssetAuditMissing[];
};

/** POST /api/discovery/asset-recover — locate/verify/place a source into input/. */
export type AssetRecoverResult = {
  name: string;
  ok: boolean;
  method?: "present" | "local" | "remote" | "walk" | "none";
  source?: string;
  relpath?: string;
  content_id?: string | null;
  error?: string;
};

export type AssetRecoverResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  recovered?: number;
  total?: number;
  note?: string;
  results?: AssetRecoverResult[];
};

/** POST /api/discovery/asset-ratings/set — set one quality axis (or all three); apply from ``saved``. */
export type SetAssetRatingResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  saved?: {
    ok?: boolean;
    relpath?: string;
    stars?: number;
    axis?: QualityAxis | string | null;
    axes?: QualityAxesMap;
    explicit?: number | null;
    cleared?: boolean;
    xmp_path?: string | null;
    discovery_key?: string;
    short_key?: string;
    sources?: string[];
  };
  /** @deprecated No longer returned; UI should use ``saved.axes`` / ``saved.explicit``. */
  ratings?: DiscoveryAssetRatingsResponse | null;
};

/** POST /api/discovery/asset-appetite/set — record a "do more WITH this" direction + facet. */
export type SetAppetiteResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  saved?: {
    ok?: boolean;
    relpath?: string;
    appetite?: Appetite | "";
    facet?: AppetiteFacet | null;
    cleared?: boolean;
    discovery_key?: string;
    short_key?: string;
    /** Present when appetite === "fast_track": the immediate Extend/replay result. */
    queued?: {
      ok?: boolean;
      reason?: string;
      extend_fallback?: string;
      job_key?: string;
      [k: string]: unknown;
    };
  };
};

/** GET /api/discovery/workflow-facets — exploratory PNG+MP4 workflow metadata + derived facet hashes. */
export type DiscoveryWorkflowFacetsResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  query_relpath?: string;
  discovery_index_path?: string;
  item?: Record<string, unknown>;
  mp4?: Record<string, unknown>;
  png_workflow_probes?: unknown[];
  provenance?: Record<string, unknown>;
  ratings_index_path?: string;
  workflow_ratings?: {
    graph_hash?: string;
    rating_inferred?: number;
    rating_effective?: number;
    rating_evidence?: { n?: number; keepers_4plus?: number };
    catalog_slug?: string;
  };
};

/** Summarized Discovery row returned by GET /api/discovery/asset-lineage. */
export type DiscoveryAssetLineageItemSummary = {
  group_id?: string;
  name?: string;
  library?: string;
  relpath?: string;
  workspace_relpath?: string | null;
  video_relpath?: string | null;
  thumb_relpath?: string | null;
  media_kind?: string;
  url?: string | null;
  thumb_url?: string | null;
  video_url?: string | null;
  /** True for Comfy ``input/`` uploads (not in the og/wip index). */
  external?: boolean;
  /** Hand-tagged XMP star rating on this output (when indexed). */
  rating_explicit?: number;
  /** Lineage-backed inferred score from downstream keepers. */
  rating_inferred?: number;
  /** Blended score for sorting badges. */
  rating_effective?: number;
  rating_evidence?: { n?: number; keepers_4plus?: number };
};

export type DiscoveryAssetLineageExternalSource = {
  via_source_raw?: string;
  abs_path?: string;
  workspace_relpath?: string | null;
  kind?: string;
};

export type DiscoveryAssetLineageExpansion = {
  depth: number;
  item: DiscoveryAssetLineageItemSummary;
  parent_group_ids: string[];
  parents: DiscoveryAssetLineageItemSummary[];
  external_sources?: DiscoveryAssetLineageExternalSource[];
  source_strings_seen: number;
};

/** One row walking up the merged parent graph (seed → ancestors). */
export type DiscoveryAssetLineageAncestryNavEntry = {
  depth: number;
  role: "seed" | "ancestor" | "source" | "root";
  group_id?: string;
  item?: DiscoveryAssetLineageItemSummary;
  external?: boolean;
  via_source_raw?: string;
};

/** Another indexed child that shares at least one parent with the seed. */
export type DiscoveryAssetLineageSiblingRow = {
  group_id?: string;
  item?: DiscoveryAssetLineageItemSummary;
  shared_parent_group_ids?: string[];
};

/** Edge row with optional resolved child summary (direct children / descendants lists). */
export type DiscoveryAssetLineageEdgeRow = {
  parent_group_id?: string;
  child_group_id?: string;
  via_source_raw?: string;
  child?: DiscoveryAssetLineageItemSummary | null;
  /** Present on transitive descendant rows (BFS generation from seed). */
  generation?: number;
};

/** Sidecar file merged into the same Discovery row as the primary asset. */
export type DiscoverySameRowMemberSummary = {
  relpath?: string;
  name?: string;
  kind?: string;
};

/** GET /api/discovery/asset-lineage — inferred parent/child edges from embedded prompt paths + optional graph persistence. */
export type DiscoveryAssetLineageResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  query_relpath?: string;
  discovery_index_path?: string;
  ratings_index_path?: string;
  lineage_graph_path?: string;
  max_depth?: number;
  persist?: boolean;
  persisted_new_edges?: number;
  peek_parent_group_id?: string;
  seed?: DiscoveryAssetLineageItemSummary;
  graph_only?: boolean;
  infer_parents?: boolean;
  infer_children?: boolean;
  provenance_chain?: DiscoveryAssetLineageAncestryNavEntry[];
  external_sources?: DiscoveryAssetLineageExternalSource[];
  ancestry_nav?: DiscoveryAssetLineageAncestryNavEntry[];
  siblings?: DiscoveryAssetLineageSiblingRow[];
  descendants_direct_seed?: DiscoveryAssetLineageEdgeRow[];
  descendants_transitive?: DiscoveryAssetLineageEdgeRow[];
  same_row_members?: DiscoverySameRowMemberSummary[];
  merged_edge_count?: number;
  expansions?: DiscoveryAssetLineageExpansion[];
  edges?: unknown[];
  child_scan_edges?: DiscoveryAssetLineageEdgeRow[];
  unresolved_source_strings?: string[];
  descendants?: unknown[];
  errors?: string[];
  notes?: string[];
};

export type DiscoveryLibraryItemLookupResponse = {
  ok: boolean;
  error?: string;
  item?: DiscoveryLibraryItem;
};

export type WorkflowExplorerBucket = {
  id: number;
  name: string;
  bucket_type: "asset" | "workflow";
  asset_count?: number;
  workflow_count?: number;
  metadata?: Record<string, unknown>;
};

export type WorkflowExplorerAsset = {
  id: number;
  bucket_id: number;
  bucket_name: string;
  path: string;
  media_type: string;
  role: string;
  status: string;
  url?: string | null;
  metadata?: Record<string, unknown>;
};

export type WorkflowExplorerWorkflow = {
  id: number;
  bucket_id: number;
  bucket_name: string;
  path: string;
  workflow_type: string;
  graph_hash?: string | null;
  input_contract?: Record<string, unknown>;
  output_contract?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
};

export type WorkflowExplorerPlannedJob = {
  id: number;
  run_plan_id: number;
  asset_item_id: number;
  workflow_item_id: number;
  output_asset_item_id?: number | null;
  job_key: string;
  status: string;
  generated_workflow_path?: string | null;
  metadata?: Record<string, unknown>;
};

export type WorkflowExplorerRunPlan = {
  id: number;
  name: string;
  input_bucket_id: number;
  workflow_bucket_id: number;
  output_bucket_id: number;
  input_bucket_name: string;
  workflow_bucket_name: string;
  output_bucket_name: string;
  rules?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  input_assets: WorkflowExplorerAsset[];
  workflow_items: WorkflowExplorerWorkflow[];
  output_assets: WorkflowExplorerAsset[];
  planned_jobs: WorkflowExplorerPlannedJob[];
};

export type WorkflowExplorerFactoryResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  db_path: string;
  buckets: WorkflowExplorerBucket[];
  assets?: WorkflowExplorerAsset[];
  workflows?: WorkflowExplorerWorkflow[];
  run_plans: WorkflowExplorerRunPlan[];
};

export type WorkflowExplorerAddAssetRequest = {
  bucket_id: number;
  path: string;
  media_type?: string;
  role?: string;
  allow_missing?: boolean;
};

export type WorkflowExplorerRemoveAssetRequest = {
  item_id: number;
};

export type WorkflowExplorerAddWorkflowRequest = {
  bucket_id: number;
  path: string;
  workflow_type?: string;
};

export type WorkflowExplorerRemoveWorkflowRequest = {
  item_id: number;
};

export type WorkflowExplorerBrowseRoot = {
  id: string;
  label: string;
  kind: "asset" | "workflow" | string;
  path: string;
  exists?: boolean;
};

export type WorkflowExplorerBrowseEntry = {
  name: string;
  path: string;
  relpath: string;
  is_dir: boolean;
  kind: string;
  media_type: string;
  size: number;
  mtime: number;
  url?: string | null;
};

export type WorkflowExplorerBrowseResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  roots: WorkflowExplorerBrowseRoot[];
  root?: WorkflowExplorerBrowseRoot;
  dir: string;
  parent?: string | null;
  entries: WorkflowExplorerBrowseEntry[];
  truncated?: boolean;
  limit?: number;
  media_type?: "all" | "image" | "video" | string;
};

export type FamilyDiscoveryStatus =
  | "pending_review"
  | "new_family"
  | "merge"
  | "skip"
  | "enrolled";

export type FamilyDiscoveryIndexRow = {
  id: string;
  io_guess?: string | null;
  members?: number;
  representative?: string | null;
  status?: FamilyDiscoveryStatus | string | null;
  output_date_first?: string | null;
  output_date_last?: string | null;
  output_date_days?: number | null;
  match_stems?: string[];
  /** Fingerprint-matched exemplar clips available for review. */
  sample_count?: number | null;
  sample_target?: number | null;
  fingerprint?: string | null;
};

export type FamilyDiscoveryMatchClass = "enrolled" | "catalog_only" | "unmatched" | string;

export type FamilyDiscoveryClusterRow = {
  id?: string | null;
  fingerprint?: string;
  member_count?: number;
  covered?: boolean;
  covered_by?: string | null;
  io_guess?: string | null;
  representative?: string | null;
  video_count?: number;
  prop_id?: string | null;
  match_class?: string | null;
};

export type FamilyDiscoveryBucketRow = {
  id?: string;
  fingerprint: string;
  match_class?: FamilyDiscoveryMatchClass;
  label?: string | null;
  video_count?: number;
  prop_id?: string | null;
};

export type FamilyDiscoverySourceRow = {
  key: string;
  label?: string | null;
  kind?: "image" | "video" | string | null;
  video_count?: number;
  bucket_count?: number;
};

export type FamilyDiscoverySampleVideo = {
  path?: string;
  name?: string;
  url?: string | null;
  thumb_url?: string | null;
  date?: string | null;
  fingerprint?: string | null;
  source_key?: string | null;
  source_label?: string | null;
  source_kind?: string | null;
  [key: string]: unknown;
};

export type FamilyDiscoveryMember = {
  source?: string;
  path?: string;
  name?: string;
  node_count?: number;
  stem?: Record<string, unknown> | null;
  exists?: boolean;
};

export type FamilyDiscoveryProp = {
  id: string;
  status?: FamilyDiscoveryStatus | string | null;
  proposed_family_slug?: string | null;
  fingerprint?: string;
  io_guess?: string | null;
  primary_input_guess?: string | null;
  input_profile_guess?: string | null;
  chain_role_guess?: string | null;
  member_count?: number;
  representative?: FamilyDiscoveryMember;
  members?: FamilyDiscoveryMember[];
  sample_videos?: FamilyDiscoverySampleVideo[];
  sample_source?: string | null;
  sample_target?: number | null;
  quarantine_notes?: string[];
  nearest_enrolled?: string | null;
  operator_decision?: string | null;
  operator_notes?: string | null;
  enrolled_at?: string | null;
  enrolled_shape?: string | null;
  output_date_first?: string | null;
  output_date_last?: string | null;
  output_date_days?: number | null;
  match_stems?: string[];
};

export type FamilyDiscoveryIndexResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  path?: string;
  schema_version?: string;
  generated_at?: string;
  review_instructions?: string;
  covered_clusters?: number;
  uncovered_clusters?: number;
  proposals: FamilyDiscoveryIndexRow[];
  enrolled_families?: string[];
  clusters?: FamilyDiscoveryClusterRow[];
  buckets?: FamilyDiscoveryBucketRow[];
  sources?: FamilyDiscoverySourceRow[];
  source_counts?: Record<string, number>;
  bucket_counts?: Record<string, number>;
  exemplar_index_ok?: boolean;
  exemplar_generated_at?: string | null;
  browse_error?: string;
};

export type FamilyDiscoveryGalleryResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  items: FamilyDiscoverySampleVideo[];
  total: number;
  offset: number;
  limit: number;
  sort?: string;
  group_by_source?: boolean;
  fingerprint?: string;
  match_class?: string;
  source?: string;
  source_label?: string | null;
  source_kind?: string | null;
  label?: string | null;
  total_count?: number;
  bucket_count?: number;
  index_ok?: boolean;
};

export type FamilyDiscoveryPropResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  path?: string;
  prop?: FamilyDiscoveryProp;
  enrolled_families?: string[];
};

export type FamilyDiscoveryPropPatch = {
  status?: FamilyDiscoveryStatus | string;
  proposed_family_slug?: string | null;
  nearest_enrolled?: string | null;
  operator_notes?: string | null;
  operator_decision?: string | null;
};

export type ShapeFactoryMapMediaRef = {
  path?: string;
  basename?: string;
  relpath?: string;
  url?: string;
  thumb_url?: string;
  thumb_relpath?: string;
  role?: string;
  binding_type?: string;
  source?: string;
  kind?: string;
  job_key?: string;
  added_at?: string;
  source_kind?: string;
  inferred?: boolean;
  /** Recovered source still for seeded (job-less) outputs, inferred from the embedded LoadImage. */
  source_still?: ShapeFactoryMapMediaRef;
};

export type ShapeFactoryMapMember = ShapeFactoryMapMediaRef;

export type ShapeFactoryMapDepositPool = {
  pool_id: string;
  slot?: string;
  description?: string;
  member_count?: number;
  members_preview?: ShapeFactoryMapMember[];
  latest_member?: ShapeFactoryMapMember | null;
};

export type ShapeFactoryMapInputPool = {
  name?: string;
  slot?: string;
  description?: string;
  feeds_from?: Array<{ pool_id?: string; from_index?: string; limit?: number }> | null;
  member_glob_count?: number;
  members_preview?: ShapeFactoryMapMember[];
  member_preview_count?: number;
};

export type ShapeFactoryMapShape = {
  shape_path?: string;
  shape_id?: string;
  family_slug?: string;
  graph_hash?: string;
  template?: string;
  primary_input?: string;
  input_profile?: string;
  chain_role?: string;
  io_class?: string;
  requires?: Array<{ slot?: string; role?: string; media?: string; optional?: boolean }>;
  deposits?: Array<{ slot?: string; to_pool?: string }>;
};

export type ShapeFactoryMapProjectedPair = {
  pair_key?: string;
  combo_key?: string;
  phase?: "job" | "future" | "seed";
  gap?: "none" | "source" | "output";
  gap_note?: string;
  source?: ShapeFactoryMapMediaRef;
  bindings?: Record<string, ShapeFactoryMapMediaRef>;
};

export type ShapeFactoryMapFamily = {
  family_slug: string;
  shape: ShapeFactoryMapShape;
  pools_yaml?: string | null;
  index_path?: string;
  index_updated_at?: string;
  input_pools?: ShapeFactoryMapInputPool[];
  deposit_pools?: ShapeFactoryMapDepositPool[];
  projected_pairs?: ShapeFactoryMapProjectedPair[];
};

export type ShapeFactoryMapQueueRequest = {
  family_slug: string;
  combo_key?: string;
  bindings: Record<string, string>;
  front?: boolean;
  dry_run?: boolean;
  /** Operator surface that initiated the queue (submit, factory-map, …). */
  source_surface?: string;
  overrides?: ShapeFactoryMapQueueOverrides;
};

export type ShapeFactoryPromptProfile = {
  ok?: boolean;
  path?: string;
  basename?: string;
  label?: string;
  positive?: string;
  negative?: string;
  profile?: Record<string, unknown>;
};

export type ShapeFactoryMapQueueOverrides = {
  source_clip_id?: string;
  clip_id?: string;
  prompt_profile?: {
    label?: string;
    positive?: string;
    negative?: string;
  };
  parameters?: {
    frames?: number;
    steps?: number;
    overlap?: number;
    frame_load_cap?: number;
    skip_first_frames?: number;
    /** Seconds — preferred Use window; factory re-derives skip/cap with probed fps. */
    mark_in?: number;
    mark_out?: number;
    /** Comfy noise seed (RandomNoise / KSampler); fixed on apply. */
    seed?: number;
    noise_seed?: number;
  };
};

export type FutureRunDraft = {
  promptProfile: {
    label: string;
    positive: string;
    negative: string;
  };
  parameters: {
    frames: string;
    steps: string;
    overlap: string;
    frame_load_cap: string;
  };
};

export type ShapeFactoryMapQueueResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  hint?: string;
  attempt_id?: string;
  family_slug?: string;
  bindings?: Record<string, string>;
  exc_type?: string;
  path_hint?: string;
  ts?: string;
  combo_key?: string;
  job_key?: string;
  job_path?: string;
  workflow_path?: string;
  prompt_id?: string;
  dry_run?: boolean;
  skipped?: boolean;
};

export type ShapeFactorySubmitAttempt = {
  attempt_id?: string;
  ts?: string;
  ok?: boolean;
  http_status?: number;
  error?: string;
  detail?: string;
  hint?: string;
  family_slug?: string;
  bindings?: Record<string, string>;
  media_relpath?: string | null;
  thumb_url?: string | null;
  job_key?: string;
  prompt_id?: string;
  path_hint?: string;
  exc_type?: string;
  source_surface?: string;
};

export type ShapeFactorySubmitAttemptsResponse = {
  ok: boolean;
  path?: string;
  count?: number;
  errors_only?: boolean;
  family_slug?: string | null;
  items?: ShapeFactorySubmitAttempt[];
  error?: string;
  detail?: string;
};

/** POST /api/shape-factory/replay — re-run (or extend) a prior job/pair. */
export type ShapeFactoryReplayRequest = {
  job_key?: string;
  family_slug?: string;
  extend?: boolean;
  front?: boolean;
  /** Hold job seed (`same`) or draw a new one (`new`). */
  seed_mode?: "same" | "new";
  overrides?: ShapeFactoryMapQueueOverrides;
};

export type ShapeFactoryReplayResponse = ShapeFactoryMapQueueResponse & {
  extend?: boolean;
  replay_of_job_key?: string | null;
  noise_seed?: number | null;
  seed_mode?: string | null;
  trim_clamped?: {
    source?: string;
    message?: string;
    requested_skip_first_frames?: number;
    requested_frame_load_cap?: number;
    skip_first_frames?: number;
    frame_load_cap?: number;
    frame_count?: number;
  };
};

/** POST /api/shape-factory/derive — rewire prompt and/or source from a seed job. */
export type ShapeFactoryDeriveRequest = {
  job_key: string;
  family_slug?: string;
  facet?: "source" | "processing" | "both";
  front?: boolean;
  overrides?: ShapeFactoryMapQueueOverrides;
};

export type ShapeFactoryDeriveResponse = ShapeFactoryMapQueueResponse & {
  derive_of_job_key?: string | null;
  derive_action?: string | null;
  appetite_facet?: string | null;
  trim_clamped?: ShapeFactoryReplayResponse["trim_clamped"];
};

/** POST /api/shape-factory/unqueue — remove waiting Comfy prompt; demote factory job to pending. */
export type ShapeFactoryUnqueueRequest = {
  prompt_id: string;
  job_key?: string;
  job_path?: string;
  actor?: string;
  reason?: string;
  source_surface?: string;
};

export type ShapeFactoryUnqueueResponse = {
  ok: boolean;
  prompt_id?: string;
  previous_prompt_id?: string;
  factory_job?: boolean;
  job_key?: string;
  job_path?: string;
  status?: string | null;
  comfy_deleted?: boolean;
  error?: string;
  detail?: string;
  comfy_delete_error?: string;
};

/** POST /api/shape-factory/begin-edit — lock job as editing; unqueue if waiting. */
export type ShapeFactoryBeginEditRequest = {
  job_key?: string;
  job_path?: string;
  actor?: string;
  reason?: string;
  source_surface?: string;
};

export type ShapeFactoryBeginEditResponse = {
  ok: boolean;
  job_key?: string;
  job_path?: string;
  status?: string;
  editing_from_status?: string;
  editing_started_at?: string;
  comfy_deleted?: boolean;
  previous_prompt_id?: string | null;
  error?: string;
  detail?: string;
  comfy_delete_error?: string;
};

/** POST /api/shape-factory/finish-edit — release editing (later|cancel|now). */
export type ShapeFactoryFinishEditRequest = {
  job_key?: string;
  job_path?: string;
  action: "later" | "cancel" | "now";
  front?: boolean;
  actor?: string;
  reason?: string;
  source_surface?: string;
};

export type ShapeFactoryFinishEditResponse = {
  ok: boolean;
  job_key?: string;
  job_path?: string;
  status?: string;
  action?: string;
  prompt_id?: string;
  error?: string;
  detail?: string;
  submit?: Record<string, unknown>;
};

/** GET /api/shape-factory/job-edit — snapshot for Submit edit-in-place mode. */
export type ShapeFactoryJobEditSnapshot = {
  ok: boolean;
  job_key?: string;
  job_path?: string;
  family_slug?: string;
  shape_path?: string;
  status?: string;
  prompt_id?: string | null;
  editing_from_status?: string;
  editing_started_at?: string;
  vhs_window?: {
    skip_first_frames?: number;
    frame_load_cap?: number;
    mark_in?: number;
    mark_out?: number;
    clip_id?: string;
  } | null;
  source_clip_id?: string | null;
  bindings?: Record<string, { path?: string; relpath?: string; url?: string; thumb_url?: string; slot?: string }>;
  source?: {
    slot?: string;
    path?: string;
    relpath?: string;
    url?: string;
    thumb_url?: string;
  } | null;
  output_prefix?: string;
  created_at?: string;
  construction?: Record<string, unknown> | null;
  error?: string;
  detail?: string;
};

/** POST /api/shape-factory/discard — archive or expunge a pending/terminal factory job. */
export type ShapeFactoryDiscardRequest = {
  job_key?: string;
  job_path?: string;
  prompt_id?: string;
  /** Comfy-history failure stub with no factory .job.json — dismiss instead of file delete. */
  history_from_comfy?: boolean;
  reason?: string;
  /** When true, permanently delete job + sidecars. When false, rename to `.discarded` (archive). */
  expunge?: boolean;
  actor?: string;
  source_surface?: string;
};

export type ShapeFactoryDiscardResponse = {
  ok: boolean;
  job_key?: string;
  job_path?: string | null;
  status?: string | null;
  discarded?: boolean;
  expunged?: boolean;
  dismissed?: boolean;
  history_stub?: boolean;
  renamed?: string[];
  deleted?: string[];
  previous_status?: string;
  error?: string;
  detail?: string;
  prompt_id?: string;
  reason?: string;
};

/** POST /api/shape-factory/update-pending-trim — patch VHS window on a pending job. */
export type ShapeFactoryUpdatePendingTrimRequest = {
  job_key?: string;
  job_path?: string;
  skip_first_frames: number;
  frame_load_cap: number;
  mark_in?: number | null;
  mark_out?: number | null;
  actor?: string;
  reason?: string;
  source_surface?: string;
};

export type ShapeFactoryUpdatePendingTrimResponse = {
  ok: boolean;
  job_key?: string;
  job_path?: string;
  workflow_path?: string;
  vhs_window?: {
    skip_first_frames?: number;
    frame_load_cap?: number;
    mark_in?: number;
    mark_out?: number;
    updated_at?: string;
    source?: string;
  };
  prompt_cleared?: boolean;
  status?: string | null;
  error?: string;
  detail?: string;
  prompt_id?: string;
};

/** POST /api/shape-factory/update-pending-binding — patch one binding path on pending/editing jobs. */
export type ShapeFactoryUpdatePendingBindingRequest = {
  job_key?: string;
  job_path?: string;
  slot: string;
  path: string;
  actor?: string;
  reason?: string;
  source_surface?: string;
};

export type ShapeFactoryUpdatePendingBindingResponse = {
  ok: boolean;
  job_key?: string;
  job_path?: string;
  slot?: string;
  path?: string;
  prompt_cleared?: boolean;
  status?: string | null;
  error?: string;
  detail?: string;
  prompt_id?: string;
};

/** GET /api/shape-factory/quarantine */
export type ShapeFactoryQuarantineEntry = {
  workflow_path?: string;
  workflow_name?: string;
  status?: string;
  category?: string;
  reasons?: string[];
  repair_outcome?: string | null;
  validated_at?: string | null;
  convert_error?: string | null;
  release_note?: string | null;
  released_at?: string | null;
};

export type ShapeFactoryQuarantineListResponse = {
  ok: boolean;
  status_filter?: string;
  quarantine_path?: string;
  count?: number;
  entries?: ShapeFactoryQuarantineEntry[];
  error?: string;
  detail?: string;
};

export type ShapeFactoryQuarantineReleaseResponse = {
  ok: boolean;
  entry?: ShapeFactoryQuarantineEntry;
  quarantine_path?: string;
  error?: string;
  detail?: string;
};

/** GET /api/home/summary — resume-the-loop dashboard aggregation. */
export type HomeSummaryFreshOutput = {
  group_id?: string | null;
  relpath?: string;
  name?: string;
  library?: string;
  mtime?: number;
  url?: string;
  video_url?: string | null;
  thumb_url?: string | null;
  ratings?: DiscoveryLibraryItem["ratings"];
};

export type HomeSummaryResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  errors?: Record<string, string>;
  rating?: {
    ok?: boolean;
    error?: string;
    session_path?: string | null;
    unrated_videos?: number | null;
    scored_pool?: number | null;
    selected?: number | null;
    buckets?: { easy_down?: number | null; easy_up?: number | null; middle?: number | null };
    vision_recommended?: number | null;
  };
  fresh_outputs?: HomeSummaryFreshOutput[];
  attention?: {
    missing_sources_total?: number;
    families?: Array<{ family_slug?: string; missing?: number }>;
    library_health?: {
      missing_primary?: number;
      missing_video?: number;
      missing_thumb?: number;
      orphan_sidecar?: number;
      orphan_thumb?: number;
      removed_since_previous_index?: number;
    } | null;
  };
  jobs?: {
    total?: number | null;
    summary?: Record<string, number>;
  };
  hourly?: {
    next_sample?: {
      cursor?: number;
      sample_index?: number;
      sample_id?: string;
      pick_index?: number;
      gex2_prompt?: string;
      note?: string;
      phase_if_idle?: string;
    } | null;
    state_path?: string;
    schedule?: HourlyScheduleStatus;
  };
};

export type HourlySubmitMode = "auto" | "comfy" | "pending";

export type HourlySchedule = {
  interval_minutes?: number;
  enabled?: boolean;
  submit_mode?: HourlySubmitMode | string;
  comfy_queue_min?: number;
  comfy_queue_max?: number;
  pending_queue_max?: number;
  last_tick_at?: string | null;
  updated_at?: string | null;
};

export type HourlyScheduleStatus = {
  ok?: boolean;
  path?: string;
  schedule?: HourlySchedule;
  due?: boolean;
  next_due_at?: string | null;
  now?: string;
  interval_presets?: number[];
  submit_modes?: string[];
  comfy_waiting?: number | null;
  comfy_running?: number | null;
  factory_pending?: number | null;
  saved?: HourlySchedule;
  error?: string;
  detail?: string;
};

/** GET /api/queue/ledger-status — Comfy queue shadow + restore controls. */
export type QueueLedgerBreaker = {
  open?: boolean;
  reason?: string;
  opened_ts?: number;
  open_until_ts?: number;
};

export type QueueLedgerStats = {
  restored_startup?: number;
  restored_outage?: number;
  restored_refill?: number;
  spillover_removed?: number;
  suppressed_breaker?: number;
  suppressed_cap?: number;
  suppressed_cooldown?: number;
  cleared?: number;
};

export type QueueLedgerEntryRole = "running" | "pending" | "backlog" | "remembered";

/** Slim row from ledger state (known / snapshot / backlog) — no prompt payload. */
export type QueueLedgerEntry = {
  prompt_id?: string;
  role?: QueueLedgerEntryRole | string;
  client_id?: string | null;
  last_seen_at?: string | null;
  last_phase?: string | null;
  has_prompt?: boolean;
};

export type QueueLedgerOpsStatus = {
  ok?: boolean;
  comfy?: { ok?: boolean; running?: number | null; pending?: number | null; error?: string };
  hourly?: { enabled?: boolean | null };
  drain?: { active?: boolean | null; enabled?: boolean; label?: string };
  watch_queue?: { running?: boolean; status?: string };
  ledger?: {
    paused?: boolean | null;
    last_park_at?: string | null;
    last_park?: { added?: number; skipped?: number; no_prompt?: number } | null;
  };
  docker_ok?: boolean;
  systemd_ok?: boolean;
  error?: string;
  detail?: string;
};

export type QueueLedgerStatus = {
  enabled?: boolean;
  state_path?: string;
  events_path?: string;
  mode?: string;
  updated_at?: string | null;
  paused?: boolean;
  pending_target?: number;
  backlog_count?: number;
  known_count?: number;
  breaker?: QueueLedgerBreaker;
  stats?: QueueLedgerStats;
  snapshot?: { running?: string[]; pending?: string[] };
  entries?: QueueLedgerEntry[];
  ops?: QueueLedgerOpsStatus;
  error?: string;
  detail?: string;
};

export type QueueLedgerControlAction =
  | "pause"
  | "resume"
  | "drain-once"
  | "clear"
  | "reset-breaker"
  | "suspend"
  | "resume-ops"
  | "hourlies-on"
  | "hourlies-off"
  | "drain-on"
  | "drain-off"
  | "watch-on"
  | "watch-off";

/** POST /api/queue/ledger-control */
export type QueueLedgerControlResponse = {
  ok?: boolean;
  action?: QueueLedgerControlAction | string;
  paused?: boolean;
  cleared?: { known?: number; backlog?: number; snapshot?: number };
  note?: string;
  error?: string;
  detail?: string;
  expected?: string[];
};

/** One line from comfy_queue_ledger.jsonl (GET /api/queue/ledger-events). */
export type QueueLedgerEvent = {
  ts?: string;
  type?: string;
  [key: string]: unknown;
};

/** GET /api/queue/ledger-events */
export type QueueLedgerEventsResponse = {
  ok?: boolean;
  events_path?: string;
  limit?: number;
  include_noise?: boolean;
  events?: QueueLedgerEvent[];
  error?: string;
  detail?: string;
};

export type ShapeFactoryMapPipelineStep = {
  id?: string;
  shape?: string;
  pools?: string;
  pick?: string;
  pick_index?: number;
  family_slug?: string;
  binds_from_pool?: string;
  binds_pick?: string;
  deposits_to?: string;
};

export type ShapeFactoryMapPipeline = {
  pipeline_id?: string;
  description?: string;
  path?: string;
  input_guidance?: string;
  affinity?: Array<{ when?: string } | string>;
  steps?: ShapeFactoryMapPipelineStep[];
};

export type ShapeFactoryMapEdge = {
  from: string;
  to: string;
  kind: string;
  slot?: string;
  pick?: string | number;
  pipeline_id?: string;
  step_id?: string;
  from_step?: string;
  to_step?: string;
};

export type ShapeFactoryMapJob = {
  job_key?: string;
  family_slug?: string;
  status?: string;
  /** hourly | ui | pipeline | factory | replay | derive | extend */
  job_kind?: string;
  graph_hash?: string;
  shape_id?: string;
  prompt_id?: string;
  bindings?: Record<string, ShapeFactoryMapMediaRef>;
  deposit_to?: string;
  generated_workflow_path?: string;
  template_path?: string;
  outputs?: Array<ShapeFactoryMapMediaRef>;
  exec_sec?: number;
  created_at?: string;
  pick_index?: number;
  pick_mode?: string;
};

export type ShapeFactoryMapResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  hint?: string;
  schema_version?: string;
  updated_at?: string;
  data_root?: string;
  paths?: Record<string, string>;
  families?: ShapeFactoryMapFamily[];
  pipelines?: ShapeFactoryMapPipeline[];
  edges?: ShapeFactoryMapEdge[];
  jobs?: {
    summary?: Record<string, number>;
    total?: number;
    items?: ShapeFactoryMapJob[];
    pending_submit?: ShapeFactoryMapJob[];
    inflight?: ShapeFactoryMapJob[];
    active?: ShapeFactoryMapJob[];
  };
  queue?: {
    ok?: boolean;
    skipped?: boolean;
    error?: string;
    detail?: string;
    running_count?: number;
    pending_count?: number;
    shape_factory_matches?: Array<{ prompt_id?: string; queue_state?: string; job?: ShapeFactoryMapJob }>;
  };
  hourly?: {
    state_path?: string;
    state?: Record<string, unknown>;
    chain_manifest?: string | null;
    next_sample?: {
      cursor?: number;
      sample_index?: number;
      sample_id?: string;
      pick_index?: number;
      gex2_prompt?: string;
      note?: string;
      phase_if_idle?: string;
    } | null;
  };
};

/** GET /api/shape-factory/work-products — construction-debug list of recent factory jobs. */
export type WorkProductDetailRow = {
  label: string;
  value: string;
  /** Absolute path to a JSON file that can be opened in the peek tooltip. */
  json_path?: string;
  /** Inline peek kind (e.g. shape contract, decoded prompt). */
  peek?: "shape" | "prompt" | string;
  /** Binding / media preview thumbnail (/files/…). */
  thumb_url?: string | null;
  /** Direct asset URL (/files/…) when the binding is media. */
  asset_url?: string | null;
  /** Workspace-relative path for deep links (Discovery / files). */
  relpath?: string | null;
};

export type WorkProductBinding = {
  path?: string;
  basename?: string;
  relpath?: string | null;
  url?: string | null;
  thumb_url?: string | null;
  binding_type?: string;
  role?: string;
};

export type WorkProductPromptRow = {
  text: string;
  weight: number;
  raw?: string;
};

export type WorkProductPromptProfile = {
  path?: string;
  basename?: string;
  label?: string | null;
  positive?: string;
  negative?: string;
  positive_rows?: WorkProductPromptRow[];
  negative_rows?: WorkProductPromptRow[];
  positive_excerpt?: string;
  positive_chars?: number;
  negative_excerpt?: string;
  negative_chars?: number;
  missing?: boolean;
  error?: string;
  owned?: boolean;
  frozen?: boolean;
  frozen_at?: string | null;
  content_hash?: string | null;
  /** True when owned content_hash differs from seed at source_profile. */
  snowflake?: boolean;
  /** Seed template baseline for snowflake diff / lineage. */
  seed?: {
    path?: string;
    label?: string | null;
    basename?: string;
    positive?: string;
    negative?: string;
    positive_rows?: WorkProductPromptRow[];
    negative_rows?: WorkProductPromptRow[];
    content_hash?: string | null;
  } | null;
};

export type WorkProductParamsValues = {
  frames?: number;
  steps?: number;
  overlap?: number;
  seed?: number;
};

export type WorkProductParamsProfile = {
  current?: WorkProductParamsValues;
  seed?: WorkProductParamsValues;
  snowflake?: boolean;
  diffs?: Record<string, { job?: number | null; seed?: number | null }>;
  template_path?: string | null;
  keys?: string[];
};

export type WorkProductShapeSlot = {
  slot?: string;
  role?: string;
  role_gloss?: string | null;
  media?: string;
  binding_type?: string;
  node_id?: string | number;
};

export type WorkProductShapeDeposit = {
  slot?: string;
  to_pool?: string | null;
};

export type WorkProductShapeProfile = {
  path?: string;
  basename?: string;
  shape_id?: string | null;
  family_slug?: string | null;
  graph_hash?: string | null;
  primary_input?: string | null;
  input_profile?: string | null;
  chain_role?: string | null;
  io_class?: string | null;
  template?: string | null;
  template_basename?: string | null;
  output_prefix_root?: string | null;
  requires?: WorkProductShapeSlot[];
  produces?: WorkProductShapeSlot[];
  deposits?: WorkProductShapeDeposit[];
  text?: string;
  missing?: boolean;
  error?: string;
};

export type WorkProductItem = {
  job_key: string;
  job_path?: string;
  family_slug?: string;
  created_at?: string;
  pick_mode?: string;
  pick_index?: number;
  /** True when produced by the hourly planner (`hourly__` job key), not merely derived from an hourly video. */
  is_hourly?: boolean;
  rating_kind?: string | null;
  disposition_entry?: string | null;
  disposition_note?: string | null;
  step?: string | null;
  combo_key?: string | null;
  parent_output?: string | null;
  parent_output_relpath?: string | null;
  parent_output_url?: string | null;
  parent_output_thumb_url?: string | null;
  shape_id?: string | null;
  shape_path?: string | null;
  template_path?: string | null;
  template_basename?: string | null;
  graph_hash?: string | null;
  output_prefix?: string | null;
  status?: string;
  flow_state?: string;
  flow_phase?: string;
  remediation_actions?: string[];
  flow_events?: Array<{
    at?: string | null;
    action?: string | null;
    actor?: string | null;
    source_surface?: string | null;
    reason?: string | null;
    ok?: boolean;
  }>;
  /** Short Comfy/UI error (OOM, VHS load failure, interrupt reason, …). */
  error?: string | null;
  error_node?: string | null;
  error_type?: string | null;
  prompt_id?: string | null;
  submitted_at?: string | null;
  deposited_at?: string | null;
  output_relpath?: string | null;
  output_url?: string | null;
  output_thumb_url?: string | null;
  /** Content-addressed id when resolved via asset registry. */
  content_id?: string | null;
  /** Work-product markers (decode.vae, notes, …) — not disposition pick-mode. */
  markers?: Record<string, string>;
  bindings?: Record<string, WorkProductBinding>;
  prompt_profile?: WorkProductPromptProfile | null;
  /** Simple run knobs vs template seed (frames/steps/overlap/seed). */
  params_profile?: WorkProductParamsProfile | null;
  shape_profile?: WorkProductShapeProfile | null;
  media_meta?: WorkProductMediaMeta | null;
  /** Compact run timing from job/sidecar (exec, queue wait, …). */
  timing?: WorkProductTiming | null;
  /** VHS loader window actually used on this job (from .prompt.json). */
  applied_vhs?: {
    skip_first_frames?: number;
    frame_load_cap?: number;
  } | null;
  /** Comfy noise seed extracted from prompt / construction. */
  noise_seed?: number | null;
  /** How seed was chosen on replay (same / new / …), when known. */
  seed_mode?: string | null;
  work_items_open?: WorkItem[];
  work_items?: WorkItem[];
  work_items_open_count?: number;
  work_items_total_count?: number;
  construction?: Record<string, unknown>;
  warnings?: unknown[];
  details?: WorkProductDetailRow[];
  /** Synthetic / promoted from Comfy queue — always pin first in the UI. */
  live_from_comfy?: boolean;
  /** Attached from Comfy /history (may lack a factory .job.json). */
  history_from_comfy?: boolean;
};

export type WorkProductFamilyOption = {
  slug: string;
  shape_id?: string | null;
  shape_path?: string;
  primary_input?: string | null;
  input_profile?: string | null;
  chain_role?: string | null;
  io_class?: string | null;
  source_still_required?: boolean;
  promotion?: {
    scope?: "temporary" | "long_term" | string;
    intents?: string[];
    expires_at?: string | null;
    note?: string | null;
  };
  vhs_defaults?: {
    skip_first_frames?: number;
    frame_load_cap?: number;
  };
};

/** GET /api/shape-factory/families — config-only picker bootstrap (no jobs/Comfy). */
export type ShapeFactoryFamiliesResponse = {
  ok: boolean;
  schema_version?: string;
  fingerprint?: string;
  families?: WorkProductFamilyOption[];
  sets?: {
    extend?: WorkProductFamilyOption[];
    vary?: WorkProductFamilyOption[];
    derive?: WorkProductFamilyOption[];
  };
  extend_family_defaults?: Record<string, string>;
  template_promotions?: {
    effective?: Record<
      string,
      {
        scope?: "temporary" | "long_term" | string;
        intents?: string[];
        expires_at?: string | null;
        note?: string | null;
      }
    >;
    path?: string;
  };
  error?: string;
  detail?: string;
};

export type ShapeFactoryTemplatePromotionEntry = {
  family_slug: string;
  intent: "extend" | "vary" | "derive";
  scope: "temporary" | "long_term";
  note?: string | null;
  actor?: string | null;
  created_at?: string | null;
  starts_at?: string | null;
  expires_at?: string | null;
};

export type ShapeFactoryTemplatePromotionsResponse = {
  ok: boolean;
  path?: string;
  schema_version?: string;
  entries?: ShapeFactoryTemplatePromotionEntry[];
  active_entries?: ShapeFactoryTemplatePromotionEntry[];
  effective?: Record<
    string,
    {
      scope?: "temporary" | "long_term" | string;
      intents?: string[];
      expires_at?: string | null;
      note?: string | null;
    }
  >;
  error?: string;
  detail?: string;
};

export type InputCurationStillItem = {
  path: string;
  catalog_path?: string;
  basename?: string;
  relpath?: string;
  url?: string;
  thumb_url?: string;
  size?: number;
  mtime?: number;
  first_seen?: number;
  last_seen?: number;
  content_id?: string | null;
  tags?: string[];
  editorial_tags?: string[];
  provisional_tags?: string[];
  effective_tags?: string[];
  note?: string | null;
  appetite?: Appetite | null;
  appetite_facet?: AppetiteFacet | null;
};

export type InputCurationCollectionItem = {
  path: string;
  added_at?: string | null;
  note?: string | null;
  content_id?: string | null;
};

export type InputCurationCollection = {
  id: string;
  name: string;
  description?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  items?: InputCurationCollectionItem[];
};

export type InputCurationStateResponse = {
  ok: boolean;
  schema_version?: string;
  data_root?: string;
  paths?: Record<string, string>;
  collections?: InputCurationCollection[];
  bindings?: Record<string, string[]>;
  updated_at?: string | null;
  error?: string;
  detail?: string;
};

export type InputCurationStillsResponse = {
  ok: boolean;
  data_root?: string;
  catalog_path?: string;
  input_root?: string;
  items?: InputCurationStillItem[];
  count?: number;
  total?: number;
  resolved_total?: number;
  skipped_missing?: number;
  limit?: number;
  offset?: number;
  next_offset?: number;
  has_more?: boolean;
  tag?: string | null;
  error?: string;
  detail?: string;
};

export type InputCurationEffectiveSourcesResponse = {
  ok: boolean;
  family_slug?: string;
  source_still_required?: boolean;
  pool_count?: number;
  effective_count?: number;
  added_count?: number;
  deduped_count?: number;
  missing_count?: number;
  attached_collection_ids?: string[];
  items?: Array<{ path: string; basename?: string }>;
  error?: string;
  detail?: string;
};

export type InputCurationAppetiteSeedItem = {
  path: string;
  basename?: string;
  content_id?: string | null;
  appetite?: Appetite | string | null;
  facet?: AppetiteFacet | string | null;
  updated_at?: string | null;
  job_key?: string | null;
  output_key?: string | null;
};

export type InputCurationAppetiteSeedsResponse = {
  ok: boolean;
  family_slug?: string;
  count?: number;
  total?: number;
  items?: InputCurationAppetiteSeedItem[];
  min_states?: string[];
  facets?: string[];
  error?: string;
  detail?: string;
};

export type StillTagRun = {
  run_id: string;
  status: string;
  scope?: Record<string, unknown>;
  enqueued_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  total?: number;
  done_count?: number;
  error_count?: number;
  skipped_count?: number;
  pin_policy?: string | null;
  model_pin?: string | null;
  provider?: string | null;
  comfy_server?: string | null;
  detail?: string | null;
};

export type StillTagEvent = {
  id: number;
  run_id: string;
  ts: string;
  kind: string;
  content_id?: string | null;
  message?: string | null;
  payload?: Record<string, unknown> | null;
};

export type StillTagEnqueueResponse = {
  ok: boolean;
  run_id?: string;
  enqueued?: number;
  skipped?: number;
  model_pin?: string;
  pin_policy?: string;
  provider?: string;
  comfy_server?: string;
  drain_kicked?: boolean;
  queued_for_index_hour?: boolean;
  error?: string;
  detail?: string;
};

export type StillTagSchedule = {
  schema_version?: number;
  enabled?: boolean;
  timezone?: string;
  window_start?: string;
  window_duration_min?: number;
  front?: boolean;
  max_inflight?: number;
  max_items_per_tick?: number;
  comfy_server?: string | null;
  auto_drain_on_enqueue?: boolean;
};

export type StillTagWindowStatus = {
  enabled?: boolean;
  in_window?: boolean;
  reason?: string;
  timezone?: string | null;
  local_now?: string;
  window_start_local?: string;
  window_end_local?: string;
  window_duration_min?: number;
  front?: boolean;
  max_inflight?: number;
  max_items_per_tick?: number;
  auto_drain_on_enqueue?: boolean;
  comfy_server?: string | null;
};

export type StillTagBacklogResponse = {
  ok: boolean;
  db_path?: string;
  queued_runs?: number;
  queued_targets?: number;
  running_runs?: number;
  items_total?: number;
  items_with_provisional?: number;
  oldest_queued_at?: string | null;
  queued_run_ids?: string[];
  schedule?: StillTagSchedule;
  window?: StillTagWindowStatus;
  error?: string;
  detail?: string;
};

export type StillTagScheduleResponse = {
  ok: boolean;
  path?: string;
  schedule?: StillTagSchedule;
  window?: StillTagWindowStatus;
  error?: string;
  detail?: string;
};

export type StillTagDrainResponse = {
  ok: boolean;
  started?: boolean;
  reason?: string;
  sync?: boolean;
  result?: Record<string, unknown>;
  skipped?: boolean;
  front?: boolean;
  done_items?: number;
  runs_processed?: number;
  error?: string;
  detail?: string;
};

export type WorkProductMediaMeta = {
  fps?: number | null;
  frame_count?: number | null;
  duration?: number | null;
};

export type WorkProductTiming = {
  exec_sec?: number | null;
  wait_sec?: number | null;
  wall_sec?: number | null;
  load_sec?: number | null;
  unload_to_reload_sec?: number | null;
  load_count?: number | null;
  unload_event_count?: number | null;
  load_models?: string[] | null;
  frames?: number | null;
  steps?: number | null;
  /** Context/overlap frames from workload (Wan extend blend width), when known. */
  overlap?: number | null;
  sec_per_frame?: number | null;
  terminal?: string | null;
  error?: boolean | null;
  source?: string | null;
  /** Preformatted chip text, e.g. "15.7m exec · 12m queue". */
  label?: string | null;
};

export type WorkProductsResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  schema_version?: string;
  data_root?: string;
  jobs_root?: string;
  hourly_only?: boolean;
  family?: string | null;
  limit?: number;
  count?: number;
  families?: WorkProductFamilyOption[];
  /** Source family → next pipeline-step family for Extend picker defaults. */
  extend_family_defaults?: Record<string, string>;
  items?: WorkProductItem[];
};

/** GET /api/vision/slice-captions */
export type VisionSliceVariantCaption = {
  caption?: string;
  tags?: string[];
  provider?: string;
  model_pin?: string;
  run_id?: string;
  task?: string;
};

export type VisionSliceFrameQuality = {
  sharpness?: number | null;
  convergence?: number | null;
  artifacting?: number | null;
  exposure?: number | null;
  contrast?: number | null;
};

export type VisionSliceMetricRollup = {
  mean?: number;
  p10?: number;
  p90?: number;
  n?: number;
};

export type VisionSliceAssetQuality = {
  frame_count?: number;
  sharpness?: VisionSliceMetricRollup;
  convergence?: VisionSliceMetricRollup;
  artifacting?: VisionSliceMetricRollup;
  exposure?: VisionSliceMetricRollup;
  contrast?: VisionSliceMetricRollup;
};

export type VisionSliceCaptionRow = {
  t0?: number;
  t1?: number;
  frame_t?: number;
  slice?: string;
  /** First available caption (compact / legacy). */
  caption?: string;
  /** Per comparative variant id. */
  captions?: Record<string, VisionSliceVariantCaption>;
  tags?: string[];
  provider?: string;
  model_pin?: string;
  run_id?: string;
  quality?: VisionSliceFrameQuality | null;
  excerpt_index?: number | null;
  excerpt_video_relpath?: string | null;
  excerpt_video_url?: string | null;
  /** Seek time inside the excerpt MP4 (seconds from excerpt start). */
  excerpt_local_t?: number | null;
};

export type VisionSliceExcerpt = {
  index: number;
  video_relpath?: string;
  video_url: string;
  source_t0?: number | null;
  source_t1?: number | null;
};

export type VisionSliceVariantQuality = {
  n?: number;
  empty_count?: number;
  empty_rate?: number;
  mean_chars?: number | null;
  median_chars?: number | null;
  mean_tags?: number | null;
  median_tags?: number | null;
};

export type VisionSliceVariantMeta = {
  id: string;
  label?: string;
  model_pin?: string | null;
  task?: string | null;
  provider?: string | null;
  run_id?: string | null;
  ndjson?: string;
  caption_count?: number;
  frame_count?: number | null;
  error_count?: number | null;
  started_utc?: string | null;
  finished_utc?: string | null;
  status?: "complete" | "running" | "idle" | string;
  progress_pct?: number | null;
  wall_s?: number | null;
  captions_per_min?: number | null;
  timing?: {
    wall_s?: number;
    mean_s?: number;
    steady_mean_s?: number;
    captions_per_min_steady?: number;
    [k: string]: unknown;
  } | null;
  quality?: VisionSliceVariantQuality;
};

export type VisionSliceReviewStats = {
  variant_count?: number;
  complete_count?: number;
  running_count?: number;
  idle_count?: number;
  expected_frames?: number | null;
  max_caption_count?: number;
  slice_count?: number;
  any_running?: boolean;
  poll_suggested_ms?: number | null;
  video_quality?: {
    asset_count?: number;
    sharpness?: number;
    convergence?: number;
    artifacting?: number;
    exposure?: number;
    contrast?: number;
  };
};

export type VisionSliceAsset = {
  asset_relpath: string;
  basename: string;
  video_url: string;
  excerpts?: VisionSliceExcerpt[];
  slice_count: number;
  has_whole?: boolean;
  quality?: VisionSliceAssetQuality | null;
  slices: VisionSliceCaptionRow[];
};

export type VisionSliceCaptionsResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  ndjson_path?: string;
  manifest_path?: string | null;
  manifest?: {
    run_id?: string;
    provider?: string;
    model_pin?: string;
    caption_count?: number;
    asset_count?: number;
    finished_utc?: string;
    note?: string;
    variant?: string;
    task?: string;
  } | null;
  variants?: VisionSliceVariantMeta[];
  stats?: VisionSliceReviewStats;
  quality_ndjson?: string | null;
  asset_count?: number;
  caption_count?: number;
  slice_count?: number;
  assets: VisionSliceAsset[];
};

/** GET /api/shape-factory/json-peek?path=... */
export type JsonPeekResponse = {
  ok: boolean;
  error?: string;
  detail?: string;
  path?: string;
  resolved?: string;
  basename?: string;
  bytes?: number;
  truncated?: boolean;
  max_bytes?: number;
  parse_error?: string | null;
  text?: string;
};

/** GET /api/comfy/live-status */
export type ComfyLiveStatusItem = {
  prompt_id: string;
  has_preview?: boolean;
  value?: number | null;
  max?: number | null;
  node?: string | null;
  status?: string | null;
  updated_at?: number | null;
  finished_at?: number | null;
  started_at?: number | null;
  elapsed_s?: number | null;
  eta_s?: number | null;
  mime?: string | null;
  vhs_length?: number | null;
  vhs_rate?: number | null;
  frames_ready?: number[];
  frames_count?: number;
};

export type ComfyLiveStatusResponse = {
  ok: boolean;
  bridge?: boolean;
  items?: ComfyLiveStatusItem[];
  count?: number;
  error?: string;
  detail?: string;
};

/** GET /api/comfy/logs — ComfyUI in-memory log ring (proxied from /internal/logs/raw). */
export type ComfyLogEntry = {
  t?: string | null;
  m: string;
};

export type ComfyLogsResponse = {
  ok: boolean;
  source?: string;
  size?: number;
  tail?: number;
  entries: ComfyLogEntry[];
  error?: string;
  detail?: string;
};

/** GET/POST /api/vision/tag-judgment */
export type VisionTagLabel = "good" | "bad";

export type VisionTagJudgmentItem = {
  sample_id: string;
  asset_relpath: string;
  basename?: string;
  t0?: number;
  t1?: number;
  frame_t?: number;
  slice?: string;
  excerpt_index?: number | null;
  excerpt_local_t?: number | null;
  video_url?: string | null;
  excerpt_video_url?: string | null;
  frame_url?: string | null;
  tags: string[];
  labels?: Record<string, VisionTagLabel> | null;
  /** Prefill for unjudged samples (chronic FPs → bad). Not counted as done until saved. */
  suggested_labels?: Record<string, VisionTagLabel> | null;
  /** Orthogonal to good/bad — significant tags for coverage scoring. */
  important?: string[] | null;
  /** Gold tags that should be present but were absent from the model union. */
  missing?: string[] | null;
  /** Suggested missing candidates (important vocab − sample tags). */
  missing_candidates?: string[] | null;
  judged_utc?: string | null;
  skipped?: boolean;
};

export type VisionTagJudgmentScoreRow = {
  id: string;
  kind?: string;
  members?: string[];
  emitted?: number;
  true_positives?: number;
  false_positives?: number;
  precision?: number | null;
  recall?: number | null;
  f1?: number | null;
  fp_rate_among_judged?: number | null;
  gold_good?: number;
  gold_good_covered?: number;
  important_n?: number;
  important_hit?: number;
  important_recall?: number | null;
  missing_n?: number;
  missing_hit?: number;
  missing_recall?: number | null;
  missing_fn?: number;
  extended_recall?: number | null;
};

export type VisionTagJudgmentTagStat = {
  tag: string;
  n_labeled?: number;
  n_good?: number;
  n_bad?: number;
  n_important?: number;
  n_missing?: number;
  good_rate?: number | null;
  bad_rate?: number | null;
  fp_rate?: number | null;
  tp_rate?: number | null;
};

export type VisionTagJudgmentLeaderboard = {
  schema?: number;
  scored_utc?: string;
  judged_samples?: number;
  queue_samples?: number;
  labeled_tags?: number;
  good_tags?: number;
  bad_tags?: number;
  important_tags?: number;
  missing_tags?: number;
  models?: VisionTagJudgmentScoreRow[];
  combos?: VisionTagJudgmentScoreRow[];
  tag_stats?: {
    tag_count?: number;
    min_n?: number;
    commonly_correct?: VisionTagJudgmentTagStat[];
    commonly_misidentified?: VisionTagJudgmentTagStat[];
    commonly_important?: VisionTagJudgmentTagStat[];
    commonly_missing?: VisionTagJudgmentTagStat[];
    contested?: VisionTagJudgmentTagStat[];
    path?: string;
    note?: string;
  };
  note?: string;
};

export type VisionTagJudgmentResponse = {
  ok: boolean;
  schema?: number;
  queue?: {
    built_utc?: string;
    seed?: number;
    variants?: string[];
    candidate_count?: number;
    item_count?: number;
    note?: string;
  };
  items?: VisionTagJudgmentItem[];
  done_sample_ids?: string[];
  done_count?: number;
  total_count?: number;
  important_vocabulary?: string[];
  missing_vocabulary?: string[];
  label_priors?: {
    min_n?: number;
    bad_rate_threshold?: number;
    good_rate_threshold?: number;
    default_bad_tags?: string[];
    default_good_tags?: string[];
    default_bad?: Record<
      string,
      { label?: string; n_labeled?: number; n_bad?: number; n_good?: number; bad_rate?: number; good_rate?: number }
    >;
    default_good?: Record<
      string,
      { label?: string; n_labeled?: number; n_bad?: number; n_good?: number; bad_rate?: number; good_rate?: number }
    >;
  };
  leaderboard?: VisionTagJudgmentLeaderboard | null;
  min_score_samples?: number;
  note?: string;
  error?: string;
  detail?: string;
};

export type VisionTagJudgmentSaveResponse = {
  ok: boolean;
  saved?: {
    sample_id?: string;
    labels?: Record<string, VisionTagLabel>;
    important?: string[];
    skipped?: boolean;
    judged_utc?: string;
  };
  done_count?: number;
  leaderboard?: VisionTagJudgmentLeaderboard | null;
  score_error?: string;
  error?: string;
  detail?: string;
};


export type AbCatalogDisposition =
  | "no_distinction"
  | "keep_as_variant"
  | "improve_base"
  | "new_family"
  | "inconclusive";

export type AbJobSide = {
  job_key?: string | null;
  family_slug?: string | null;
  status?: string | null;
  outputs?: string[];
  output_urls?: string[];
  prompt_id?: string | null;
  ok?: boolean;
  error?: string | null;
};

export type AbJudgment = {
  catalog_disposition: AbCatalogDisposition | string;
  observed_effect?: string | null;
  embody_side?: "a" | "b" | string | null;
  notes?: string | null;
  judged_at?: string | null;
};

export type AbExperiment = {
  ab_id: string;
  created_at?: string;
  updated_at?: string;
  status?: string;
  label?: string | null;
  hypothesis?: string | null;
  family_a?: string;
  family_b?: string;
  exemplar?: {
    job_key?: string | null;
    output_relpath?: string | null;
    family_slug?: string | null;
  };
  shared?: Record<string, string>;
  side_a?: Record<string, unknown>;
  side_b?: Record<string, unknown>;
  notes_engine?: string[];
  job_a?: AbJobSide;
  job_b?: AbJobSide;
  judgment?: AbJudgment | null;
  dry_run?: boolean;
};

export type AbExperimentsListResponse = {
  ok: boolean;
  experiments?: AbExperiment[];
  error?: string;
  detail?: string;
};

export type AbExperimentResponse = {
  ok: boolean;
  ab?: AbExperiment;
  error?: string;
  detail?: string;
  markers_stamped?: unknown[];
};

export type AbQueueRequest = {
  exemplar?: { job_key?: string; output_relpath?: string };
  job_key?: string;
  output_relpath?: string;
  family_a?: string;
  family_b: string;
  label?: string;
  hypothesis?: string;
  seed_mode?: string;
  front?: boolean;
  dry_run?: boolean;
  side_a?: Record<string, unknown>;
  side_b?: Record<string, unknown>;
  identity_anchor?: string;
  knobs?: Record<string, number | string>;
};

export type AbJudgmentRequest = {
  catalog_disposition: AbCatalogDisposition | string;
  observed_effect?: string;
  embody_side?: "a" | "b" | string;
  notes?: string;
};
