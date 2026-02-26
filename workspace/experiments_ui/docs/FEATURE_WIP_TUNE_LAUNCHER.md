# Feature: WIP browser + tune experiment launcher

## Goal

Let users **browse a directory of output** (e.g. `output/output/wip`), **select one or more videos**, and **set parameters for tune experiments**, then **generate and optionally run** those experiments from the Experiments UI—instead of driving everything from the CLI.

## Current state

- **Experiments UI** today:
  - Lists experiments under `experiments_root`, shows runs and status, queue view, requeue/cancel/clear.
  - **Next experiment**: `POST /api/next-experiment` takes an **anchor** `{ exp_id, run_id }` and uses that *run’s output MP4* as `base_mp4` for a new experiment (same code path as `tune_experiment.py generate`).
- **Tune experiments** are created by:
  - `tune_experiment.py generate <base_mp4> --out-root ... --exp-id ... --seed ... --duration ... --cfg ... --denoise ... --steps ...`
  - `base_mp4` must be an MP4 with embedded ComfyUI prompt/workflow (e.g. from wip).
- **Wip layout**: `output/output/wip/<YYYY-MM-DD>/<stem>_OG_00001.mp4`, `<stem>_UPIN_00001.mp4`, etc.

## Proposed behavior

### 1. Backend: WIP browse API

- **GET /api/wip**  
  - Query: optional `dir` = relative path under wip (e.g. `""` or `"2026-02-14"`).  
  - Response: list of **date subdirs** (for root) or **media entries** (for a date dir).
- **Date list** (when `dir` is empty or root):
  - Subdirs of `output_root/output/wip` (or configured wip root) named `YYYY-MM-DD`, sorted by date desc.
  - Each: `{ "name": "2026-02-14", "path": "output/output/wip/2026-02-14", "date": "2026-02-14" }`.
- **Media list** (when `dir` is e.g. `2026-02-14`):
  - Scan `wip_root / dir` for `*.mp4` (and optionally `*.png` for thumbnails).
  - Each MP4: `{ "name": "FB8VA5L-2026-02-14-234042_UPIN_00001.mp4", "path": "output/output/wip/2026-02-14/FB8VA5L-...", "relpath" for /files, "size", "mtime" }`.
  - Paths returned as **relative to output_root** so the UI can use existing `/files/` for preview.
- **Config**: Server already has `output_root` and `experiments_root`; add optional `wip_root` (default `output_root / "output" / "wip"`).

### 2. Backend: Create experiment from base_mp4 (not anchor)

- **POST /api/create-experiment**  
  - Body:
    - `base_mp4_relpath`: string (e.g. `output/output/wip/2026-02-14/FB8VA5L-2026-02-14-234042_UPIN_00001.mp4`) — relative to `output_root`.
    - `exp_id`: optional string (default: derive from stem + timestamp).
    - `seed`: number.
    - `duration_sec`: number (e.g. 5).
    - `baseline_first`: boolean.
    - `max_runs`: number.
    - `sweep`: object (same as next-experiment: `cfg`, `denoise`, `steps`, `speed`, `teacache`, etc. — arrays of values).
  - Server resolves `base_mp4 = output_root / base_mp4_relpath`, then runs:
    - `tune_experiment.py generate <base_mp4> --out-root <experiments_root> --exp-id ... --seed ... --duration ...` plus sweep flags derived from `sweep`.
  - Response: `{ ok, exp_id, exp_dir, run_count, ... }` (and optionally stderr if generate fails).
- **Batch**: Either accept an array of `base_mp4_relpath` and create N experiments (same params), or keep one-by-one and let the UI call N times. Batch is nicer for “select 3 videos → create 3 experiments.”

### 3. Frontend: WIP browser + launcher

- **New section or tab**: e.g. “New from WIP” or “Tune from output”.
- **Layout**:
  - **Left**: Tree or list of wip:
    - Root: list of date folders (from `GET /api/wip`).
    - Click a date: show MP4s in that folder (from `GET /api/wip?dir=2026-02-14`).
  - **Right**: Parameter form (reuse same concepts as next-experiment):
    - **Seed** (number).
    - **Duration (sec)** (number, e.g. 5).
    - **Baseline first** (checkbox).
    - **Sweep**:
      - **cfg**: comma or list (e.g. 5.0, 5.5).
      - **denoise**: e.g. 0.82, 0.84.
      - **steps**: e.g. 28, 32.
      - Optional: speed, teacache, etc.
    - **Max runs** (cap).
    - **Exp ID** (optional prefix; server can append timestamp if needed).
  - **Selection**:
    - In the wip file list, multi-select MP4s (checkboxes).
    - “Create N experiments” button: for each selected video, call `POST /api/create-experiment` (or single batch endpoint) with current form params.
  - **Preview**: Use existing `/files/<relpath>` to show a small video thumbnail or first frame for each row (optional but useful).
- **After create**: Show success + link to the new experiment(s) in the main list; optionally auto-navigate or refresh experiment list.

### 4. Optional: “Run” from launcher

- After create, offer “Run” (submit to ComfyUI) for the new experiment(s), reusing existing queue/run logic (e.g. same as “Run” on an experiment in the list). No new API needed if we already have “submit runs for experiment X” (or we call existing run flow server-side for the new exp_id).

## Implementation order

1. **Backend**
   - Add `wip_root` to server config (default `output_root / "output" / "wip"`).
   - Implement `GET /api/wip` (date list + media list by `dir`).
   - Implement `POST /api/create-experiment` (single base_mp4_relpath → generate); then optionally extend to batch (array of relpaths).
   - Keep `tune_experiment.py generate` as the single source of truth (subprocess call from server).
2. **Frontend**
   - Add API client: `fetchWip(dir?)`, `createExperimentFromWip(body)`.
   - Add WIP browser component (date list → file list with selection).
   - Add parameter form (mirror next-experiment sweep + duration/seed).
   - Wire “Create experiments” to API; show result and link to experiments list.
3. **Polish**
   - Optional video preview via `/files/`.
   - Optional “Run after create” button.
   - Validation: only allow `.mp4` with embedded metadata (server can fail generate if not; or add a lightweight “probe” endpoint later).

## Files to touch

- **Server** (repo-root `scripts/experiments_ui_server.py` or workspace copy if that’s the one run in prod):
  - Config: `wip_root`.
  - `GET /api/wip` handler.
  - `POST /api/create-experiment` handler (subprocess to `tune_experiment.py generate`).
- **Frontend** (`experiments_ui/web/src/ui/`):
  - `api.ts`: `fetchWip`, `createExperimentFromWip`.
  - `types.ts`: WipDateDir, WipMediaEntry, CreateExperimentRequest/Response.
  - New component(s): `WipBrowser.tsx`, `TuneParamsForm.tsx` (or inline), and a small “Create from WIP” view/section in `App.tsx` or a new route.

## Edge cases

- **Missing ffprobe / invalid MP4**: Generate will fail; return 400/502 with stderr so UI can show “This file doesn’t have ComfyUI metadata or isn’t a valid base.”
- **Path safety**: Restrict `dir` and `base_mp4_relpath` to under `wip_root` / `output_root` (no `..`, no absolute paths in response).
- **Concurrent create**: Multiple creates are fine (different exp_id); optional lock per exp_id if we ever allow overwrite.

---

This plan gives a clear path to “browse wip → select videos → set params → create (and optionally run) experiments” entirely from the experiment interface.
