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
  input_thumb_url?: string | null;
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
  workflow_name?: string | null;
  key_params?: Record<string, unknown>;
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
};

export type DispositionMarkerKind = "entry" | "step";

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
  session_mix?: { easy_down?: number; easy_up?: number; middle?: number };
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

/** POST /api/discovery/asset-ratings/set — set one quality axis (or all three) + refresh index. */
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
  family_slug?: string;
  combo_key?: string;
  job_key?: string;
  job_path?: string;
  workflow_path?: string;
  prompt_id?: string;
  dry_run?: boolean;
  skipped?: boolean;
};

/** POST /api/shape-factory/replay — re-run (or extend) a prior job/pair. */
export type ShapeFactoryReplayRequest = {
  job_key?: string;
  family_slug?: string;
  extend?: boolean;
  front?: boolean;
  overrides?: ShapeFactoryMapQueueOverrides;
};

export type ShapeFactoryReplayResponse = ShapeFactoryMapQueueResponse & {
  extend?: boolean;
  replay_of_job_key?: string | null;
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
  };
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
  prompt_id?: string | null;
  submitted_at?: string | null;
  deposited_at?: string | null;
  output_relpath?: string | null;
  output_url?: string | null;
  output_thumb_url?: string | null;
  bindings?: Record<string, WorkProductBinding>;
  prompt_profile?: WorkProductPromptProfile | null;
  shape_profile?: WorkProductShapeProfile | null;
  work_items_open?: WorkItem[];
  work_items?: WorkItem[];
  work_items_open_count?: number;
  work_items_total_count?: number;
  construction?: Record<string, unknown>;
  warnings?: unknown[];
  details?: WorkProductDetailRow[];
  /** Synthetic / promoted from Comfy queue — always pin first in the UI. */
  live_from_comfy?: boolean;
};

export type WorkProductFamilyOption = {
  slug: string;
  shape_id?: string | null;
  shape_path?: string;
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

