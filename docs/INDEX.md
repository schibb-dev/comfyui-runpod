# Documentation index

This page mirrors the planning-relevant sections of the repo-root [DOCUMENTATION.md](../DOCUMENTATION.md) index. Use it from the MkDocs sidebar when browsing design notes.

**Browse this site:** `./scripts/serve_planning_docs.sh` → [http://127.0.0.1:8000](http://127.0.0.1:8000)

**Print:** open any page → browser Print (Ctrl+P). Custom print CSS hides nav chrome. For a polished PDF of the bucket model, run `./scripts/build_bucket_model_pdf.sh` from the repo root.

---

## Start here

| Document | What it is |
|----------|------------|
| [plans/README.md](plans/README.md) | **Planning hub** — A1–A4 / S1–S2, focus, doc index. |
| [plans/archive/MAP.md](plans/archive/MAP.md) | Current ↔ archive map; defunct / conflict notes. |
| [WORKFLOW_INTENT.md](WORKFLOW_INTENT.md) | Factory + pipeline metaphor; station vocab. |
| [family_discovery/REVIEW.md](family_discovery/REVIEW.md) | Phase 2 provisional family proposals (operator naming gate). |
| [../README.md](../README.md) | Main project guide (repo root). |
| [../TROUBLESHOOTING.md](../TROUBLESHOOTING.md) | Common failures. |

Old paths such as `PLANNING_OVERVIEW.md` / `CURRENT_GOAL.md` are **stubs** → live plans + archive.

---

## Live plans (`plans/`)

| Document | What it is |
|----------|------------|
| [plans/a1-discovery.md](plans/a1-discovery.md) | A1 Discovery |
| [plans/a2-experimentation.md](plans/a2-experimentation.md) | A2 Experimentation |
| [plans/a3-refinement.md](plans/a3-refinement.md) | A3 Refinement (hourlies as manifestation) |
| [plans/a3-hourly-drain-policy.md](plans/a3-hourly-drain-policy.md) | **Primary Next** — drain policy as data (U1/M1) |
| [plans/a4-production.md](plans/a4-production.md) | A4 Production (stub) |
| [plans/s1-custody.md](plans/s1-custody.md) | S1 Custody |
| [plans/s2-platform.md](plans/s2-platform.md) | S2 Platform |
| [plans/asset-gallery.md](plans/asset-gallery.md) | Asset galleries / starter roles |
| [plans/judgment.md](plans/judgment.md) | Judgment (ratings + disposition) |
| [plans/archive/README.md](plans/archive/README.md) | Archived drafts index → MAP |

---

## Product models & runbooks (docs root)

| Document | What it is |
|----------|------------|
| [DISPOSITION_BUCKET_MODEL.md](DISPOSITION_BUCKET_MODEL.md) | Bucket model reference |
| [CLIP_SELECTION_MODEL.md](CLIP_SELECTION_MODEL.md) | Asset / Clip / Use, starring, soft-delete |
| [CORPUS_LIFECYCLE.md](CORPUS_LIFECYCLE.md) | Corpus lifecycle |
| [CATALOG_IDENTITY.md](CATALOG_IDENTITY.md) | Opaque id, mutable name, default designation |
| [VARIANT_MANAGEMENT.md](VARIANT_MANAGEMENT.md) | Prompt variants |
| [FACTORY_MCP.md](FACTORY_MCP.md) | Factory MCP verbs |
| [SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md](SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md) | Queue & container jobs |
| [WORKSPACE_PROJECTS_RUNDOWN.md](WORKSPACE_PROJECTS_RUNDOWN.md) | Workspace projects & resubmit |
| [WORKFLOW_COMPATIBILITY.md](WORKFLOW_COMPATIBILITY.md) | Workflow node upgrades |
| [PROJECT_ORGANIZATION_PROPOSAL.md](PROJECT_ORGANIZATION_PROPOSAL.md) | Repo split proposal |

---

## Infra & misc

| Document | What it is |
|----------|------------|
| [CHECKIN_STRATEGY.md](CHECKIN_STRATEGY.md) | Check-in / layering strategy |
| [EXPERIMENTS_UI_API_CONCURRENCY.md](EXPERIMENTS_UI_API_CONCURRENCY.md) | Parked concurrency note |
| [KRITA_AI_SETUP.md](KRITA_AI_SETUP.md) | Krita AI setup |
| [WSL_MOVE_TO_E_FOLLOWUP.md](WSL_MOVE_TO_E_FOLLOWUP.md) | WSL vhdx move follow-up |
| [CURSOR_AGENT_SUDO_ASKPASS.md](CURSOR_AGENT_SUDO_ASKPASS.md) | Cursor agent sudo/askpass |
| [RDP_UBUNTU_SETUP.md](RDP_UBUNTU_SETUP.md) | RDP / Ubuntu setup |

---

## Repo-root docs (outside this site)

| Document | What it is |
|----------|------------|
| [../DOCUMENTATION.md](../DOCUMENTATION.md) | Full documentation index |
| [../RUNPOD.md](../RUNPOD.md) | RunPod deployment |
| [../GPU_CONFIGURATION_GUIDE.md](../GPU_CONFIGURATION_GUIDE.md) | GPU configuration |
| [../workspace/README.md](../workspace/README.md) | Workspace layout and tooling |

---

*For the complete list of markdown files, run `git ls-files '*.md'` from the repo root.*
