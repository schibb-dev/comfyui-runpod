# Documentation index

This page mirrors the planning-relevant sections of the repo-root [DOCUMENTATION.md](../DOCUMENTATION.md) index. Use it from the MkDocs sidebar when browsing design notes.

**Browse this site:** `./scripts/serve_planning_docs.sh` → [http://127.0.0.1:8000](http://127.0.0.1:8000)

**Print:** open any page → browser Print (Ctrl+P). Custom print CSS hides nav chrome. For a polished PDF of the bucket model, run `./scripts/build_bucket_model_pdf.sh` from the repo root.

---

## Start here

| Document | What it is |
|----------|------------|
| [CURRENT_GOAL.md](CURRENT_GOAL.md) | **Active handoff** — current goal (ComfyUI + GPU + mounts + Docker Desktop). |
| [PLANNING_OVERVIEW.md](PLANNING_OVERVIEW.md) | **Planning hub** — programs P1–P9, focus, doc index. |
| [WORKFLOW_INTENT.md](WORKFLOW_INTENT.md) | Factory + pipeline metaphor; station vocab; pipeline catalog pointer. |
| [family_discovery/REVIEW.md](family_discovery/REVIEW.md) | Phase 2 provisional family proposals (operator naming gate). |
| [../README.md](../README.md) | Main project guide (repo root). |
| [../TROUBLESHOOTING.md](../TROUBLESHOOTING.md) | Common failures. |

---

## Programs & vision

| Document | What it is |
|----------|------------|
| [DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md](DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md) | Discovery / similarity / HITL vision (V1–V5 sequence). |
| [VISION_V1_TIME_SLICE_CAPTION_SPIKE.md](VISION_V1_TIME_SLICE_CAPTION_SPIKE.md) | P1 V1 time-slice caption spike (impl). |
| [SOURCE_FACET_SIMILARITY_PLAN.md](SOURCE_FACET_SIMILARITY_PLAN.md) | Source facet hold axes for hourly derive. |
| [LINEAGE_INDEX_SKETCH.md](LINEAGE_INDEX_SKETCH.md) | Lineage index sketch. |
| [SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md](SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md) | Queue & container jobs. |
| [WORKSPACE_PROJECTS_RUNDOWN.md](WORKSPACE_PROJECTS_RUNDOWN.md) | Workspace projects & resubmit MVP. |
| [PROJECT_ORGANIZATION_PROPOSAL.md](PROJECT_ORGANIZATION_PROPOSAL.md) | Repo split proposal. |
| [WORKFLOW_COMPATIBILITY.md](WORKFLOW_COMPATIBILITY.md) | Workflow node upgrades. |

---

## Active implementation plans

| Document | What it is |
|----------|------------|
| [VISION_V1_TIME_SLICE_CAPTION_SPIKE.md](VISION_V1_TIME_SLICE_CAPTION_SPIKE.md) | **Primary** — time-slice captions (~12 videos, run-anywhere). |
| [DISCOVERY_INDEX_WATCHER_PLAN.md](DISCOVERY_INDEX_WATCHER_PLAN.md) | Discovery FS watcher + enrichment jobs (V2). |
| [RATINGS_V1_PLAN.md](RATINGS_V1_PLAN.md) | Ratings, disposition, triage. |
| [DISPOSITION_BUCKET_MODEL.md](DISPOSITION_BUCKET_MODEL.md) | Bucket model reference (diagram-first). |
| [CLIP_SELECTION_MODEL.md](CLIP_SELECTION_MODEL.md) | Asset / Clip / Use, starring, soft-delete. |
| [HOURLY_UTILITY_PLAN.md](HOURLY_UTILITY_PLAN.md) | Hourlies as ruleset utility: policy file, clip ★+newer bind, decision ledger, Home UI. |
| [STILL_GALLERY_HUB_PLAN.md](STILL_GALLERY_HUB_PLAN.md) | Still gallery as launch hub: Submit / collections / map / ratings from one image. |
| [STILL_AUTO_TAGGER_PLAN.md](STILL_AUTO_TAGGER_PLAN.md) | Still auto-tagger (PromptGen-large pin → provisional gallery tags). |
| [HOURLY_CLIP_GUIDANCE_PLAN.md](HOURLY_CLIP_GUIDANCE_PLAN.md) | Stub → `HOURLY_UTILITY_PLAN.md` (clip bind = U0/U2). |
| [HOURLY_RULESET_UTILITY.md](HOURLY_RULESET_UTILITY.md) | Stub → `HOURLY_UTILITY_PLAN.md` (policy/ledger/UI = U1/U3/U4). |
| [BUCKET_MODEL_PHASE2_PLAN.md](BUCKET_MODEL_PHASE2_PLAN.md) | Work items, pool pages, multi-route Advance. |
| [ASSET_LIFECYCLE_PLAN.md](ASSET_LIFECYCLE_PLAN.md) | Asset lifecycle phase 2. |
| [WORKFLOW_REPAIR_PLAN.md](WORKFLOW_REPAIR_PLAN.md) | Workflow repair tiers. |

---

## Infra & misc

| Document | What it is |
|----------|------------|
| [CHECKIN_STRATEGY.md](CHECKIN_STRATEGY.md) | Check-in / layering strategy. |
| [KRITA_AI_SETUP.md](KRITA_AI_SETUP.md) | Krita AI setup. |
| [WSL_MOVE_TO_E_FOLLOWUP.md](WSL_MOVE_TO_E_FOLLOWUP.md) | WSL vhdx move follow-up. |
| [CURSOR_AGENT_SUDO_ASKPASS.md](CURSOR_AGENT_SUDO_ASKPASS.md) | Cursor agent sudo/askpass. |
| [RDP_UBUNTU_SETUP.md](RDP_UBUNTU_SETUP.md) | RDP / Ubuntu setup. |

---

## Repo-root docs (outside this site)

These live at the repository root and are not in the MkDocs `docs/` tree:

| Document | What it is |
|----------|------------|
| [../DOCUMENTATION.md](../DOCUMENTATION.md) | Full documentation index (all `*.md` in repo). |
| [../RUNPOD.md](../RUNPOD.md) | RunPod deployment. |
| [../GPU_CONFIGURATION_GUIDE.md](../GPU_CONFIGURATION_GUIDE.md) | GPU configuration. |
| [../workspace/README.md](../workspace/README.md) | Workspace layout and tooling. |

---

*For the complete list of markdown files, run `git ls-files '*.md'` from the repo root.*
