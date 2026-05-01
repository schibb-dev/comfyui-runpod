# Krita AI (Krita AI Diffusion) + ComfyUI

This doc covers using **Krita AI Diffusion** (by Acly) with this repo’s ComfyUI instance as the backend. Official reference: [ComfyUI Setup | Krita AI Handbook](https://docs.interstice.cloud/comfyui-setup/).

## 1. ComfyUI side (this repo)

**Important:** ComfyUI does **not** run the Krita desktop app. This repo can optionally prepare ComfyUI as a **backend** for the **Krita AI Diffusion** plugin (which runs inside Krita on your PC and connects to ComfyUI over the network).

**Defaults (non-Krita workflows):** Krita-specific startup behavior is **off** unless you opt in:

- `AUTO_DOWNLOAD_KRITA_AI_MODELS=false` — no Krita model manifest / `download_models.py` on container start  
- `INSTALL_KRITA_BACKEND_NODES=false` — bootstrap **skips** Acly bridge nodes `comfyui-tooling-nodes` and `comfyui-inpaint-nodes`

**To enable Krita backend support**, set in `.env` (then recreate/restart `comfyui`):

```env
AUTO_DOWNLOAD_KRITA_AI_MODELS=true
INSTALL_KRITA_BACKEND_NODES=true
```

The following custom nodes are **used** by the Krita plugin and are listed in `custom_nodes.yaml` (optional section). The two **Acly** nodes above are gated by `INSTALL_KRITA_BACKEND_NODES`; the others are general-purpose optional nodes and still install with the rest of `optional:` unless you remove them from the YAML.

- **comfyui-tooling-nodes** (Acly) – image/mask over WebSocket for external tools  
- **comfyui-inpaint-nodes** (Acly) – Fooocus inpaint, LaMa, MAT, mask tools  
- **ComfyUI_IPAdapter_plus** (cubiq) – IP-Adapter for image conditioning  
- **comfyui_controlnet_aux** (Fannovel16) – ControlNet preprocessors  

If you added them recently, rebuild or restart so bootstrap installs them:

```bash
docker compose build comfyui
docker compose up -d comfyui
```

Or, if you mount `./custom_nodes`, just restart; bootstrap will clone any missing nodes.

### Required models (ComfyUI)

Krita AI Diffusion expects specific models in your ComfyUI `models/` tree. **All paths below are relative to your models root (e.g. E:\models when mounted).**

**Auto-download when enabled (profile `krita_ai`):**

With `AUTO_DOWNLOAD_KRITA_AI_MODELS=true`, the entrypoint downloads into `/ComfyUI/models` (i.e. your mounted folder, e.g. E:\models):

- **Shared:** MAT inpainting, NMKD Superscale, OmniSR X2/X3/X4, CLIP Vision (`clip-vision_vit-h.safetensors`)
- **SD 1.5:** Hyper-SD LoRA (SD1.5), ControlNet Inpaint, ControlNet Tile (Unblur), IP-Adapter (SD1.5)
- **SD XL:** Hyper-SD LoRA (SDXL), Fooocus Inpaint (patch + head), IP-Adapter (SDXL)

Existing files are skipped. The entrypoint also runs **Krita’s official download script** ([download_models.py](https://github.com/Acly/krita-ai-diffusion/blob/main/scripts/download_models.py)) with destination **ComfyUI root** (`/ComfyUI`), as in the [handbook](https://docs.interstice.cloud/comfyui-setup/#download-script). Preset: `KRITA_DOWNLOAD_PRESET` (default `--recommended`). Use `--all` to download every supported model like the handbook example; `--minimal` for the smallest set. Manual run (inside container):  
`python3 /workspace/scripts/download_models_manifest.py --profile krita_ai --models-dir /ComfyUI/models`  
`cd /opt/krita-ai-diffusion && python3 scripts/download_models.py /ComfyUI --recommended`

**Not in the manifest (add manually if you use these pipelines):**

- **SD 3:** Text Encoder clip_g, diffusion checkpoint  
- **Flux Kontext / Flux 2 Klein / Chroma / Qwen / Z-Image:** Diffusion checkpoints and (where applicable) Qwen / VAE encoders are large or vendor-specific; install from the Krita plugin’s `download_models.py` or from Hugging Face as needed.

**Checkpoints:** You still need at least one SD1.5 or SDXL checkpoint in `checkpoints/` (or `diffusion_models/`). See the [Krita ComfyUI setup](https://github.com/Acly/krita-ai-diffusion/wiki/ComfyUI-Setup) wiki for optional checkpoint suggestions.

**Using the plugin’s download script (after installing the plugin in Krita):**

```bash
# From the plugin folder (ai_diffusion) on your machine
python -m pip install aiohttp tqdm
python download_models.py /path/to/comfyui/models
```

If ComfyUI runs in Docker, use the path that mounts into the container (e.g. `./workspace/models` or `COMFYUI_MODELS_DIR`). The script writes into the given path; point it at your ComfyUI models root (or the mounted equivalent).

---

## 2. Krita + Krita AI Diffusion (desktop)

Krita AI runs on your **local machine** and talks to ComfyUI (local or remote).

### Install Krita

- **Windows:** https://krita.org/en/download/krita-desktop/ (or Microsoft Store).  
- Need **Krita 5.2.0 or newer**.

### Install the Krita AI Diffusion plugin

1. Download the plugin ZIP from:  
   https://github.com/Acly/krita-ai-diffusion/releases  
   (e.g. `Krita-AI-Diffusion-1.48.0.zip` or latest.)

2. In Krita: **Tools → Scripts → Import Python Plugin from File…**  
   Select the downloaded ZIP and confirm.

3. Restart Krita if prompted. The AI Diffusion UI should appear (e.g. dock or menu).

### Connect Krita to ComfyUI

1. In Krita, open **Settings / AI Diffusion** (or the plugin’s connection/ComfyUI settings).
2. Set **ComfyUI URL** to your ComfyUI server:
   - Local Docker: `http://127.0.0.1:8188` (or the port in `docker-compose.yml`).
   - Same machine, different port: `http://localhost:8188`.
   - Remote RunPod/Server: `http://<host>:8188` (and ensure 8188 is reachable/firewalled).
3. Save and use **Connect** / **Check connection**. The plugin will check for required nodes and models; fix any missing-node or missing-model warnings using the wiki/docs and the model list above.

---

## References

- Krita AI Diffusion: https://github.com/Acly/krita-ai-diffusion  
- ComfyUI setup (required nodes + models): https://github.com/Acly/krita-ai-diffusion/wiki/ComfyUI-Setup  
- Newer docs: https://docs.interstice.cloud/comfyui-setup  
- Plugin releases: https://github.com/Acly/krita-ai-diffusion/releases  
