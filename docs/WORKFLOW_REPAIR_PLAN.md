# Workflow repair rules — plan

General model: **pattern → fix → retry** (`workspace/scripts/workflow_repair.py`).

Each rule has a `phase` (`ui_workflow` | `prompt` | future: `pool_bind`), implements
`matches(ctx)` + `apply(ctx)`, and is registered in `default_repair_rules()` or loaded from YAML.

Validation / quarantine runs `repair_until_stable` before updating registry status.

---

## Current state (2026-07-03)

| Rule | Phase | Status |
|------|-------|--------|
| `node_type_rename` | ui_workflow | **Shipped** — driven by `scripts/workflow_node_id_map.yaml` |
| `prompt_string_image_mismatch` | prompt | **Shipped** — IMAGE→STRING on caption inputs |

Catalog: **11/20 pass** `--comfy-check` after repair loop. **9 quarantined**:

| Category | Count | Examples |
|----------|-------|----------|
| `missing_module` | 4 | `GetNode` (subgraph) |
| `missing_asset` | 4 | LoadImage / VHS invalid file |
| `prompt_wiring` | 1 | FB8VA5-laying-down node 422 |

---

## Rule taxonomy (planned)

### Tier A — YAML-only (cheap, high volume)

**A1. `node_type_rename`** *(done)*  
- **Pattern:** `nodes[].type` not in `/object_info`, key in `workflow_node_id_map.yaml`  
- **Fix:** Rename type + optional `POST_TYPE_RENAME_HOOKS`  
- **Examples:** `LoadImageWithFilename|pysssss` → `LoadImage`, `Text Concatenate` → `StringConcatenate`, `CFGZeroStarAndInit` → `CFGZeroStar`

**A2. `ui_only_ignore`** *(config, not a repair)*  
- **Pattern:** type in allowlist (`PrimitiveNode`, rgthree bypassers)  
- **Fix:** None — exclude from quarantine `missing_required_nodes`  
- **Status:** Partially in `_VALIDATE_UI_NODE_TYPES`

**A3. Declarative `prompt_error_rules` in YAML** *(planned)*  
- **Pattern:** match `report.node_errors.*.errors[]` by `type`, `details` regex, `input_name`  
- **Fix:** action enum (see Tier B prompts)  
- **File:** extend `workflow_node_id_map.yaml` or new `workflow_repair_rules.yaml`

```yaml
prompt_error_rules:
  - id: string_a_image_mismatch
    match:
      error_type: return_type_mismatch
      details_contains: "received_type(IMAGE)"
      input_name: string_a
    action: set_string_input_empty   # or drop_string_input
```

---

### Tier B — Python rules (structural / graph-aware)

**B1. `prompt_string_image_mismatch`** *(done)*  
- Proactive + reactive (after comfy-check failure)

**B2. `load_image_pysssss_structural`** *(merged into A1 hook)*  
- Trim 3rd output, fix widgets — `POST_TYPE_RENAME_HOOKS`

**B3. `missing_asset_remap`** *(planned — high value)*  
- **Pattern:** comfy-check `invalid image file` / `invalid video file`; or LoadImage widget basename not on disk  
- **Fix:** Resolve via pool / inventory / glob under `input/`, `comfyui_user/input/`, og artifact dirs  
- **Phase:** `ui_workflow` (rewrite `widgets_values`) or `pool_bind` at generate time  
- **Retry:** re-run `--comfy-check`  
- **Fallback:** quarantine category `missing_asset` + suggest `pool sync` / manual path  
- **Needs:** `data_root`, optional link to snowflake inventory / pool index by stem

**B4. `subgraph_getnode_inline`** *(planned — hard)*  
- **Pattern:** missing `GetNode` / `SetNode`  
- **Fix options (pick one):**  
  1. Install subgraph support in Comfy (infra, not repair)  
  2. **Inline subgraph:** expand Get/Set pairs to direct links (requires subgraph metadata in workflow)  
  3. **Quarantine + candidate re-export** without subgraphs  
- **Phase:** `ui_workflow`  
- **Priority:** Medium — 4/20 catalog workflows

**B5. `dead_link_prune`** *(planned)*  
- **Pattern:** convert relink warnings; API input points at node id not in prompt  
- **Fix:** Drop input or relink from LiteGraph link table (`sync_prompt_inputs_from_ui_workflow` already partial)  
- **Phase:** `prompt`

**B6. `vhs_path_normalize`** *(planned)*  
- **Pattern:** Windows paths, wrong subfolder, triple `output/output/output` prefix drift  
- **Fix:** Normalize slashes; align with `discover_job_outputs` path variants  
- **Phase:** `ui_workflow` + `prompt`

**B7. `node_mode_bypass`** *(optional)*  
- **Pattern:** Optional optimizers enabled (TeaCache, compile) causing failures on current stack  
- **Fix:** Set `mode=4` (bypass) — already done in snowflake_inventory canonicalize  
- **Phase:** `ui_workflow`

---

### Tier C — Generate-time / factory (not file repair)

These are **bindings**, not workflow JSON patches — run in `generate`, not quarantine sync:

| Rule | When | Fix |
|------|------|-----|
| `slot_asset_bind` | shape generate | Replace LoadImage / VHS path from pool pick |
| `prompt_profile_bind` | shape generate | Inject prompt JSON into text nodes |
| `output_prefix_bind` | shape generate | Already in shape_factory |

Quarantine should **pass** after generate if only issue was hardcoded catalog seed paths.

---

## Retry / quarantine integration

```
quarantine sync --comfy-check
  └─ for each catalog workflow:
       repair_until_stable (max 5 rounds)
       ├─ ui_workflow rules
       ├─ validate (convert + comfy-check)
       ├─ prompt rules (from node_errors)
       └─ retry validate if fixes applied
       update quarantine.json
         ok          → status=ok
         fixable     → stay quarantined + repair_fixes[] audit trail
         unfixable   → quarantined + category + human release
```

Report fields: `repair_fixes`, `repair_rounds`, `repair_stable`, `category`.

**Exit criteria per workflow:**
- `ok=true` after repair → `status=ok`, `repair_outcome=cleared`  
- Fixes applied but still failing → **stay quarantined** with full audit (`repair_outcome=exhausted`)  
- No rule matched → quarantined, `repair_outcome=failed_no_repair`  

Quarantine entry captures **remaining errors** + **attempted fixes** for human review (P5).

---

## Implementation order

| Priority | Rule / capability | Effort | Unblocks |
|----------|-------------------|--------|----------|
| P0 | YAML `prompt_error_rules` | S | Faster prompt fixes without Python ✅ |
| P1 | `missing_asset_remap` | M | 4 OG catalog workflows ✅ (+2 cleared) |
| P2 | More `node_type_rename` entries | S | As upgrades find renames |
| P3 | `subgraph_getnode_inline` or install pack | L | 4 GetNode workflows |
| P4 | `vhs_path_normalize` | S | Deposit / output discovery |
| **P5** | **`repair_exhausted` quarantine audit** | **S** | **Registry + report trail when unfixable** ✅ |
| P6 | `dead_link_prune` | M | FB8VA5-laying-down node 422 |
| P7 | Declarative rule loader tests + `repair rules --verbose` | S | DX |

### P5 — quarantine with error + fix audit (shipped)

When the repair loop ends without `ok`:

| Field | Purpose |
|-------|---------|
| `status` | `quarantined` — blocks generate/submit |
| `repair_outcome` | `exhausted` \| `failed_no_repair` \| `cleared` |
| `repair_fixes[]` | Each tried rule: `rule_id`, `phase`, `summary`, `node_id` |
| `repair_rounds`, `repair_stable` | Loop metadata |
| `reasons`, `category`, `node_errors`, `missing_required_node_types` | What still failed |
| `report_path` | Full `*.validate.json` |

Review: `quarantine list --status quarantined` → fix upstream or `release --note`.

## Adding a new rule (checklist)

1. Define **pattern** (what does `matches()` see?)  
2. Define **fix** (UI graph, prompt dict, or binding only?)  
3. Choose **phase** and whether it needs **retry of comfy-check only** vs full convert  
4. Add **unit test** in `workspace/tests/test_workflow_repair.py`  
5. Run `shape_factory repair run --workflow ... --comfy-check`  
6. Run `quarantine sync --comfy-check` on catalog  
7. Document in `workflow_node_id_map.yaml` or `workflow_repair_rules.yaml`

---

## Open questions

1. **Subgraph strategy:** install GetNode support vs inline vs re-export catalog?  
2. **Asset remap authority:** pool index, snowflake inventory, or glob `input/**`?  
3. **Write policy:** always write UI fixes to catalog, or keep patched copies under `shape_factory/patched/`?  
4. **Generate gate:** should `missing_asset` auto-clear when generate binds fresh pool paths (Tier C)?
