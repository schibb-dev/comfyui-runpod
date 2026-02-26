# Troubleshooting

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
