# Troubleshooting

## "This workflow uses custom nodes you haven't installed" (but Docker image has them)

**Typical cause on Windows:** **two different ComfyUI processes** are bound to port **8188**:

- **Docker** publishes `0.0.0.0:8188` → container ComfyUI with **full** `custom_nodes` (thousands of `object_info` keys).
- **Another install** (e.g. **ComfyUI portable**) listens on **`127.0.0.1:8188` only**.

Browsers and `http://127.0.0.1:8188` usually connect to the **loopback** listener (portable) first, which has **fewer** nodes. The UI then flags almost every custom node as missing.

**Check (PowerShell):**

```powershell
netstat -ano | findstr ":8188"
```

If you see **two** `LISTENING` lines (e.g. `0.0.0.0:8188` and `127.0.0.1:8188`), you have a conflict.

**Fix (pick one):**

1. **Exit / stop** the other ComfyUI (portable) while using Docker.
2. Map Docker to another **host** port — in `.env` set `COMFYUI_HOST_PORT=8189` (see `.env.example`), then `docker compose up -d` and open **`http://127.0.0.1:8189`**.
3. Open Docker’s UI via your **LAN IP** (e.g. `http://192.168.x.x:8188`) so traffic hits `0.0.0.0:8188` / Docker instead of `127.0.0.1`.

**Sanity check:** Inside the container, `object_info` should list types like `mxSlider` and `VHS_VideoCombine`:

```bash
docker exec comfyui0-runpod python3 -c "import urllib.request,json; o=json.loads(urllib.request.urlopen('http://127.0.0.1:8188/object_info').read()); print(len(o), 'mxSlider' in o, 'VHS_VideoCombine' in o)"
```

---

## NumPy 2 vs OpenCV: "numpy.core.multiarray failed to import" / "_ARRAY_API not found"

If the log shows **NumPy 2.2.x** and errors like:

- `AttributeError: _ARRAY_API not found` when a custom node does `import cv2`
- `ImportError: numpy.core.multiarray failed to import`
- *"A module that was compiled using NumPy 1.x cannot be run in NumPy 2.2.x"*

then **OpenCV (cv2)** in the environment was built for **NumPy 1.x** and is incompatible with NumPy 2. Affected nodes (e.g. ComfyUI-Easy-Use, ComfyUI-KJNodes, ComfyUI-VideoHelperSuite, ComfyUI-Impact-Pack, comfyui_controlnet_aux, was-node-suite-comfyui) will show **IMPORT FAILED**.

**Fix (recommended):** Pin NumPy to 1.x in the image. This repo’s **Dockerfile** already does:

```dockerfile
RUN pip install --no-cache-dir "numpy<2"
```

**Rebuild the image** so the pin is applied:

```bash
docker compose build --no-cache comfyui
docker compose up -d
```

If you use a different image (e.g. RunPod template), add a build step or run at container start:

```bash
pip install --no-cache-dir "numpy<2"
```

Then restart ComfyUI. Do **not** upgrade numpy to 2 in that environment unless every dependency (including OpenCV) has NumPy 2–compatible wheels.

---

## Missing "deepdiff" (ComfyUI-Crystools)

If you see:

- `ModuleNotFoundError: No module named 'deepdiff'` when loading **ComfyUI-Crystools**

install it in the container (or ensure the node’s `requirements.txt` is installed by bootstrap):

```bash
pip install deepdiff
```

This repo’s Dockerfile also installs `deepdiff` in the image so Crystools loads without an extra step.

---

## ComfyUI-TeaCache: `cannot import name 'precompute_freqs_cis' from 'comfy.ldm.lightricks.model'`

**Cause:** **ComfyUI #11632** (*Support the LTXV 2 model*) refactored `comfy/ldm/lightricks/model.py`: the **module-level** function `precompute_freqs_cis` was removed (logic moved onto **`LTXBaseModel._precompute_freqs_cis`**). **ComfyUI-TeaCache** (welltop-cn) still does `from comfy.ldm.lightricks.model import precompute_freqs_cis`, so it breaks on ComfyUI commits **at and after** that merge.

**Verified boundary (comfyanonymous/ComfyUI):**

| Commit | `precompute_freqs_cis` at module level? |
|--------|----------------------------------------|
| `38d049382533c6662d815b08ca3395e96cca9f57` (parent of #11632) | **Yes** — TeaCache import works |
| `f2b002372b71cf0671a4cf1fa539e1c386d727e4` (#11632 merge) | **No** — TeaCache import fails |

**Default in this repo:** `docker-compose.yml` and the `Dockerfile` **default** `COMFYUI_REF` to **`38d049382533c6662d815b08ca3395e96cca9f57`** so new builds get a TeaCache-compatible ComfyUI unless you override.

1. **Rebuild** so the checkout runs (required after changing the default or your `.env`):

   ```bash
   docker compose build --no-cache comfyui
   docker compose up -d
   ```

2. If you have **`.env`** with `COMFYUI_REF=origin/master`, that **overrides** the pinned default — remove it or set the hash above.

**Bleeding-edge ComfyUI:** set `COMFYUI_REF=origin/master` (in `.env` or `--build-arg`), rebuild — expect TeaCache to fail until upstream aligns with the new LTX API.

**Tradeoff:** That pin is **just before LTXV 2** in ComfyUI. Some newer WAN/LTX paths may need a newer ComfyUI; then choose updated TeaCache vs newer ComfyUI.

**Find your own “last good” hash:** On any working ComfyUI tree, run `git rev-parse HEAD` and use that as `COMFYUI_REF`.

### ComfyUI-MultiGPU + this ComfyUI pin

Newer **ComfyUI-MultiGPU** (`main`) does `import comfy.memory_management`, but ComfyUI **before** that split still has memory helpers only in `comfy.model_management`. On the TeaCache-safe ComfyUI pin (`38d04938…`), you will see:

`ModuleNotFoundError: No module named 'comfy.memory_management'`

**Fix in this repo:** `custom_nodes.yaml` pins **ComfyUI-MultiGPU** to commit **`ee41f46beb0dfe5b221d2791d88ebce0d0b39df0`** (2025-10-04), which does **not** require `comfy.memory_management` and avoids a bad intermediate snapshot that referenced missing WanVideo symbols. If you bump ComfyUI to a revision that includes `comfy/memory_management.py`, you can bump MultiGPU toward `main` again (and retest).

**Runtime toggle:** `INSTALL_MULTIGPU` (default **true** in `docker-compose.yml`) controls whether the entrypoint keeps `ComfyUI-MultiGPU` under `/ComfyUI/custom_nodes` or moves it aside. Set `INSTALL_MULTIGPU=false` for single-GPU hosts that should not load the pack.

---

## Custom nodes show "IMPORT FAILED" (ComfyUI-Easy-Use, ComfyUI-KJNodes, ComfyUI-VideoHelperSuite, etc.)

When the ComfyUI UI reports **IMPORT FAILED** for one or more custom nodes, the **exact Python error** is printed in the ComfyUI process output. In RunPod/Docker that is the **container log**.

### 1. Get the actual error from the log

**Docker (local or RunPod):**

```bash
# Last 500 lines (adjust as needed); look for "Traceback", "Error", "ModuleNotFoundError"
docker logs comfyui0-runpod 2>&1 | tail -500
```

Or search for import-related lines:

```bash
docker logs comfyui0-runpod 2>&1 | grep -A 20 "Traceback\|ModuleNotFoundError\|ImportError\|IMPORT FAILED"
```

On **RunPod**, use the pod’s **Logs** tab and scroll to startup; the traceback appears **above** the “(IMPORT FAILED)” line for each node.

The traceback will show the real cause, e.g.:

- `ModuleNotFoundError: No module named 'color_matcher'` → missing pip dependency
- `ModuleNotFoundError: No module named 'cv2'` → OpenCV not installed or wrong variant (see below)
- Version or compatibility errors → upgrade/downgrade the package or the node

### 2. Force reinstall node requirements

Bootstrap skips reinstalling a node’s `requirements.txt` if it thinks it’s already up to date. After a base image or node update, dependencies may be missing or stale. Force a full reinstall:

**Docker Compose:** add the env var and restart:

```yaml
environment:
  - FORCE_REINSTALL_NODE_REQUIREMENTS=true
```

Then:

```bash
docker compose down
docker compose up -d
```

**RunPod:** set the same env var in the pod template and restart the pod.

Watch the startup log: you should see “🔄 FORCE_REINSTALL_NODE_REQUIREMENTS is set” and “📋 Installing requirements for …” for each node. Any **pip** errors will appear there. After a successful run you can set `FORCE_REINSTALL_NODE_REQUIREMENTS` back to `false` or remove it.

### 3. ComfyUI-VideoHelperSuite and OpenCV (Docker)

**"Node 'Preview' not found" / VHS_VideoCombine not found:** This usually means VideoHelperSuite failed to load because OpenCV (`cv2`) is missing. The repo’s Dockerfile now installs **opencv-python-headless** at build time so `VHS_VideoCombine` registers. If you see this error, rebuild the image (`docker compose build --no-cache`) and redeploy so the new layer is used.

Many ComfyUI Docker images use **opencv-python-headless** (no GUI). ComfyUI-VideoHelperSuite’s `requirements.txt` lists **opencv-python**. Installing the full `opencv-python` in the same environment can conflict or fail.

**If the traceback points to `cv2` or OpenCV when loading VideoHelperSuite:**

- Ensure the container has the headless variant installed **before** (or instead of) the full package:

  ```bash
  pip install opencv-python-headless
  ```

- Then either:
  - Force reinstall that node’s requirements (see above), or  
  - Temporarily change `custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt` to use `opencv-python-headless` instead of `opencv-python` for that run, then run bootstrap with `FORCE_REINSTALL_NODE_REQUIREMENTS=true`.

After fixing, restart ComfyUI and check the logs again for any remaining IMPORT FAILED messages.

---

## ComfyUI-Custom-Scripts: clone/download fails (repo moved)

The ComfyUI-Custom-Scripts repository was originally under GitHub user **pysssss** and is now maintained under **pythongosssss**. If Docker startup fails with an error cloning or downloading ComfyUI-Custom-Scripts (e.g. 404 or repository not found), ensure `custom_nodes.yaml` uses the current URL:

- **Correct:** `https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git`
- **Old (fails):** `https://github.com/pysssss/ComfyUI-Custom-Scripts.git`

Update `custom_nodes.yaml`, then restart the container so bootstrap runs again (e.g. `docker compose restart comfyui`).

---

## LoadText|pysssss / SaveText|pysssss: "No such file or directory: ... user/text_file_dirs.json"

ComfyUI-Custom-Scripts (by pythongosssss; formerly pysssss) expects a config file for its text-file nodes. If you see:

- `FileNotFoundError: [Errno 2] No such file or directory: '.../ComfyUI-Custom-Scripts/user/text_file_dirs.json'`
- `[ERROR] An error occurred while retrieving information for the 'LoadText|pysssss' node` or `'SaveText|pysssss' node`

then the node’s `user` directory or `text_file_dirs.json` is missing.

**Fix:** Create the directory and an empty config file so the node can load.

Inside the container (e.g. Docker/RunPod), where ComfyUI is at `/ComfyUI`:

```bash
mkdir -p /ComfyUI/custom_nodes/ComfyUI-Custom-Scripts/user
echo '[]' > /ComfyUI/custom_nodes/ComfyUI-Custom-Scripts/user/text_file_dirs.json
```

If ComfyUI is elsewhere (e.g. under `/workspace`), use that path:

```bash
mkdir -p /workspace/ComfyUI/custom_nodes/ComfyUI-Custom-Scripts/user
echo '[]' > /workspace/ComfyUI/custom_nodes/ComfyUI-Custom-Scripts/user/text_file_dirs.json
```

Then restart ComfyUI (or reload the UI). You can later edit `text_file_dirs.json` to add allowed directories if you use those nodes.

---

## Experiments UI dev (`npm run ui:dev:start` / `ui:dev:all`) and Docker port **8790**

Docker Compose maps **host `8790` → container `8790`**, where the container may run `experiments_ui_server.py` (see `entrypoint.sh` / `EXPERIMENTS_UI`).

If you run **Vite on the host** with the proxy target set to `http://127.0.0.1:8790`, browser requests to `/api/...` go to **whatever is listening on the host’s 8790** — usually the **container’s** Experiments UI, not a Python process you started separately on Windows. That can cause:

- **404** on newer routes (e.g. `/api/comfy/history`) if the container is running an older mounted copy of the server or hasn’t been restarted after you changed `scripts/experiments_ui_server.py`
- **Extra HTTP load** on the same process that serves Comfy-related workflows (poll timers from the queue monitor)

**Fix (current repo defaults):**

- **`npm run ui:dev:all`** / **`npm run ui:dev:vite`**: Vite on the host only; proxy defaults to **`http://127.0.0.1:8790`** (the published comfyui Experiments API). Set **`EXPERIMENTS_UI_PROXY_TARGET`** to override (`scripts/experiments-ui-dev.mjs` and `workspace/experiments_ui/web/vite.config.ts` agree on the fallback).
- **`npm run ui:dev:start`**: nodemon-watched Python API on **`127.0.0.1:8791`** plus Vite proxied to that API (for editing `experiments_ui_server.py` on the host).
- Container workflow: `scripts/dev_experiments_ui_container.ps1` sets `EXPERIMENTS_UI_PROXY_TARGET=http://127.0.0.1:8790` so Vite inside the container talks to the in-container API.

After changing `experiments_ui_server.py`, restart the ComfyUI container so the background Experiments UI picks up the script: `docker compose restart comfyui`.

**If ComfyUI “crashed” or hung:** check `docker compose logs comfyui --tail 200`. Messages like **“Ran out of memory when regular VAE encoding, retrying with tiled VAE encoding”** are **normal** for some workflows—ComfyUI falls back to tiled encoding and the run continues; do not treat that alone as a failure. For real crashes, look for process exit, repeated errors after the tiled retry, or CUDA errors that abort the job.