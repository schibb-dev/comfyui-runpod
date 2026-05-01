# Workflow node type compatibility (ongoing)

ComfyUI workflows store each node’s **registered class name** in `nodes[].type`. When you upgrade ComfyUI or custom nodes, authors sometimes **rename** node classes. Old workflows then show “missing node” even though the feature still exists under a new name.

This repo mitigates that with a **small, versioned map** and two scripts.

## 1. Verify against what the server actually registers

Point at a running ComfyUI (same stack as production):

```bash
python scripts/verify_workflow_node_types.py -w path/to/workflow.json --server http://127.0.0.1:8188
```

- Exit **0**: every `nodes[].type` appears in `GET /object_info`.
- Exit **1**: prints types that are **not** registered (rename, removed node, or missing custom node).

Optional: save a snapshot when you cut a release (pin Comfy + custom node SHAs) and compare offline:

```bash
curl -s http://127.0.0.1:8188/object_info -o object_info_release.json
python scripts/verify_workflow_node_types.py -w workflow.json --object-info-json object_info_release.json
```

## 2. Add mappings for renames only

Edit `scripts/workflow_node_id_map.yaml`:

```yaml
mappings:
  "OldClassNameAsInWorkflow": "NewClassNameFromObjectInfo"
```

- Keys/values must match **exact** strings from the old workflow and from `/object_info`.
- Do **not** guess long Easy-Use titles; confirm the new name in `/object_info` or the node’s Python `NODE_CLASS_MAPPINGS`.

## 3. Migrate the workflow file

```bash
python scripts/migrate_workflow_node_types.py -w workflow.json --dry-run
python scripts/migrate_workflow_node_types.py -w workflow.json -o workflow.migrated.json
```

Also updates `properties["Node name for S&R"]` when it still matched the old type.

## 4. Re-verify

```bash
python scripts/verify_workflow_node_types.py -w workflow.migrated.json --server http://127.0.0.1:8188
```

## Ongoing process (after upgrades)

1. **Pin** ComfyUI + custom nodes (`custom_nodes.yaml` / Docker) so prod is reproducible.
2. After any upgrade, run **verify** on representative workflows.
3. For each missing type, either install the missing pack or add a **mapping** + **migrate** + commit the map change.
4. Optionally archive `object_info` JSON per release for diffing without a live GPU.

## API vs UI workflows

- These scripts target **UI-exported** graph JSON (`nodes` array).
- **API prompt** JSON uses `class_type` per node; if you use that format, apply the same renames there or re-export from the UI after migration.

## Example: `FB9_GEX_FACIAL` pinned stack

`custom_nodes.yaml` includes git SHAs aligned with that workflow’s embedded CNR metadata (see comments on each entry). Highlights:

| Node pack | Pinned ref role |
|-----------|-----------------|
| ComfyUI-KJNodes | pyproject **1.2.9** |
| ComfyUI-Easy-Use | tag **v1.3.6** |
| ComfyUI-VideoHelperSuite | pyproject **1.7.9** (existing ref unchanged) |
| ComfyUI-MultiGPU | workflow CNR commit **`ac3df4ed…`** (if MultiGPU fails to import, try **`ee41f46beb…`** per Dockerfile comments) |
| was-node-suite-comfyui | registry-rename commit **`30ca7500…`** |
| ComfyUI-Impact-Pack | CNR commit **`6a517ebe…`** |

ComfyUI itself stays on `COMFYUI_REF` (default **38d049382533c6662d815b08ca3395e96cca9f57** for TeaCache safety). Workflow `comfy-core` **0.14.1** is the **registry** package version for built-in nodes, not the ComfyUI `pyproject` version.
