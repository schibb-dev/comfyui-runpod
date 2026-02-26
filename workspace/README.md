## comfyui-runpod workspace utilities

This repo contains a small set of Python scripts to make **ComfyUI workflows/media reproducible and Git-friendly**:

- **Extract** embedded ComfyUI metadata (prompt/workflow) from PNG/MP4
- **Extract** a compact **preset** JSON (run parameters) from media
- **Clean** workflow JSONs into stable **templates**
- **Apply** presets back onto templates (roundtrip)
- **Write/merge** seeds + hashes into `.XMP` sidecars

Most of the tooling lives in `scripts/`.

### One entrypoint (`comfy_tool.py`)

If you prefer a single entrypoint, use:

```bash
python scripts/comfy_tool.py <command> -- <args-for-underlying-script>
```

Commands:
- `metadata` → `scripts/extract_comfy_metadata.py`
- `preset` → `scripts/extract_comfy_preset.py`
- `update-xmp` → `scripts/update_comfy_seed_xmp.py`
- `clean-workflow` → `scripts/clean_comfy_workflow.py`
- `canonicalize-titles` → `scripts/canonicalize_comfy_titles.py`
- `apply-preset` → `scripts/apply_comfy_preset.py`
- `process-wip-dir` → `scripts/process_wip_dir.py`
- `check-wip-agreement` → `scripts/check_wip_agreement.py`
- `check-roundtrip` → `scripts/check_roundtrip_dir.py`
- `canonicalize-xmp` → `scripts/canonicalize_xmp_filenames.py`
- `tune-sweep` → `scripts/tune_experiment.py generate` (create an experiment directory)
- `tune-run` → `scripts/tune_experiment.py run` (validate/list runs; submission is done by `experiment_queue_manager.py` or via `queue_incomplete_experiments` ops job)
- `tune-materialize` → `scripts/tune_experiment.py materialize` (retro-generate per-run workflow JSONs for an existing experiment)
- `tune-apply` → `scripts/tune_experiment.py apply` (apply tuning params to your original workflow and export tuned workflows)
- `watch-queue` → `scripts/watch_queue.py` (asynchronously submit + collect histories)

### Tuning experiments: per-run candidate workflows (usable like tuned workflows)

When you generate an experiment sweep (`tune-sweep`), each run folder contains:

- `prompt.json`: the runnable prompt payload submitted to the ComfyUI API (may include output isolation to keep trial outputs separate)
- `params.json`: the parameter values for that run (used by the Experiments UI columns)
- `<stem>.workflow.<run_id>.json`: a **loadable candidate workflow** JSON you can run like a final tuned workflow (open this in the ComfyUI UI)
- `<stem>.workflow.<run_id>.cleaned.json`: same idea, but materialized from the cleaned template (handy for diffs)

This makes it easy to try any run as if it could be the final workflow, while still keeping trial executions isolated via `prompt.json`.

### Experiments: keep original workflows next to inputs

During experiment generation (and when using `copy-inputs`), we copy the base MP4/PNG inputs into `<exp_dir>/inputs/`.
We also extract the embedded ComfyUI metadata from those media files into sidecars in the same folder:

- `inputs/<stem>.workflow.json`
- `inputs/<stem>.prompt.json`

These are useful as the “original workflow” baseline you can tune/apply against later.

If you have an older experiment directory created before `workflow.json` files were emitted, you can retro-generate them:

```bash
python scripts/comfy_tool.py tune-materialize -- <exp_dir>
```

To overwrite existing files:

```bash
python scripts/comfy_tool.py tune-materialize -- <exp_dir> --overwrite
```

### Export tuned workflows (no output bookkeeping)

If what you want is a “production” workflow based on your **original workflow** (not the experiment’s `prompt.json` bookkeeping), export **tuned workflows** from `params.json`:

```bash
python scripts/comfy_tool.py tune-apply -- <exp_dir>
```

By default this writes to `<exp_dir>/tuned_workflows/` and preserves things like your original output naming/location and seed behavior (it only applies the tuning knobs).
To apply onto a specific workflow template file:

```bash
python scripts/comfy_tool.py tune-apply -- <exp_dir> --workflow-template path/to/original.workflow.json
```

To see the underlying tool's help:

```bash
python scripts/comfy_tool.py process-wip-dir -- --help
```

### Roundtrip integration test (media fixtures)

We have an end-to-end integration test that verifies “roundtripping is possible” on real sample media:

- **Test**: `tests/test_integration_media_roundtrip.py`
- **Fixtures directory**: `tests/fixtures/media/`

How it works:
- You drop **ComfyUI-saved** `*.mp4` + matching `*.png` (same stem) into `tests/fixtures/media/`
- The test copies fixtures into a temp folder, runs:
  - `scripts/process_wip_dir.py` (generates sidecars: `.preset.json`, `.metadata.json`, `.workflow.json`, `.template.cleaned.json`, `.XMP`)
  - `scripts/check_roundtrip_dir.py` (verifies hashes + preset→template application)

Notes:
- Requires `ffprobe` available in `PATH` (from ffmpeg). If not present, the integration test will **skip**.
- The test will also **skip** if there are no fixture pairs yet.
- If `tests/fixtures/media/manifest.json` is present but references files that don't exist locally, the test will **warn** (listing missing paths) and **skip** those entries.

See `tests/fixtures/media/README.md` for the fixture format.

### Experiments UI (React dashboard)

This repo includes a small **React app** + **Python server** that lets you browse tuning experiments as a dynamic table and preview outputs side-by-side.

- **Python server**: `scripts/experiments_ui_server.py`
- **React source**: `experiments_ui/web/`
- **Built assets output** (served by Python): `experiments_ui/dist/`

The UI automatically discovers “axes” from each run’s `params.json` keys (plus a few computed fields like `status` and `prompt_id`), so adding new sweep parameters automatically adds new columns you can select.

#### Run inside the comfy-runpod container

`docker-compose.yml` exposes port **8790** for the UI. To enable it in the container, set:

- `EXPERIMENTS_UI=true`: start the server
- `EXPERIMENTS_UI_PORT=8790`: port to listen on (default 8790)
- `EXPERIMENTS_UI_BUILD=true`: if `experiments_ui/dist/index.html` is missing, build the React app once at startup using `npm` (default true)

Then open `http://127.0.0.1:8790/`.Note: on some Windows setups, `http://localhost:8790/` can prefer IPv6 (`::1`) and fail with `ERR_EMPTY_RESPONSE`. Using `127.0.0.1` avoids that.

#### Local dev loop (optional)

If you want hot-reload while iterating on the UI, run Vite dev server and proxy API calls:

```bash
cd experiments_ui/web
npm install
npm run dev
```

The Vite config proxies `/api` and `/files` to `http://127.0.0.1:8790`.

#### Convenience scripts

- **Start UI dev server (HMR) on your host** (recommended):

```powershell
.\scripts\dev_experiments_ui.ps1
```

This starts Vite on `http://127.0.0.1:5178/` and proxies `/api` + `/files` to the container UI server at `http://127.0.0.1:8790/`.

- **Start UI dev server (HMR) inside the container** (no host Node.js required):

```powershell
.\scripts\dev_experiments_ui_container.ps1 -EnsureContainer
```

Note: the container currently ships with Node 18, so the UI dev server uses Vite 5 for compatibility.

- **Rebuild `dist/` inside the running container** (no full image rebuild):

```bash
./scripts/build_experiments_ui.sh
```
