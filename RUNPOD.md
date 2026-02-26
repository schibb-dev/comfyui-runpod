# RunPod deployment notes

## Image

- Build/push your image (or use your registry/tag).
- The container starts via `entrypoint.sh`, then delegates to the base image `/start_script.sh`.

## Required volume mounts (recommended)

Mount a persistent RunPod volume to:

- `/workspace` (recommended): keeps workflows, tokens, and helper state
- `/ComfyUI/models` (recommended): keeps models between pod restarts
- `/ComfyUI/custom_nodes` (optional but recommended): keeps node repos cached between restarts

If you only mount one thing, mount `/workspace` and ensure it contains `models/` and `credentials/`.

## Environment variables

- `HUGGINGFACE_TOKEN` (optional)
- `CIVITAI_TOKEN` (optional)
- Any WAN download flags you want to enable/disable (see `docker-compose.yml`)

## Credentials

On your persistent volume, create:

- `/workspace/credentials/huggingface_token` (plain text token)
- `/workspace/credentials/civitai_token` (plain text token)

The entrypoint loads these at startup and exports the env vars.

## Port

Expose `8188/tcp` (ComfyUI).

Optional:
- Expose `8790/tcp` if you enable the Experiments UI (`EXPERIMENTS_UI=true`).