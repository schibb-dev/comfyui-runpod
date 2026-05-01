#!/usr/bin/env python3
"""
Scan ComfyUI workflow JSON files for referenced model filenames and report missing files.

This is intentionally conservative: it detects *filenames* referenced by common loader nodes,
but it cannot always infer where to download them from.

Useful for answering:
  - "Which models do my workflows reference?"
  - "Which ones are missing from /ComfyUI/models (aka COMFYUI_MODELS_DIR)?"

Example (inside container):
  python3 /workspace/scripts/scan_workflows_for_models.py --models-dir /ComfyUI/models
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def _iter_workflow_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if not p.exists():
            continue
        if p.is_file() and p.suffix.lower() == ".json":
            out.append(p)
        elif p.is_dir():
            out.extend(sorted(p.rglob("*.json")))
    return out


def _as_list_widgets(node: dict[str, Any]) -> list[Any] | None:
    w = node.get("widgets_values")
    if isinstance(w, list):
        return w
    return None


def _as_dict_widgets(node: dict[str, Any]) -> dict[str, Any] | None:
    w = node.get("widgets_values")
    if isinstance(w, dict):
        return w
    return None


def _extract_model_refs(node: dict[str, Any]) -> list[tuple[str, str]]:
    """
    Return list of (category, filename) pairs.
    Category matches common ComfyUI model subfolders.
    """
    t = node.get("type")
    if not t:
        return []

    w_list = _as_list_widgets(node) or []
    w_dict = _as_dict_widgets(node) or {}

    refs: list[tuple[str, str]] = []

    if t in ("CheckpointLoaderSimple", "CheckpointLoader", "CheckpointLoaderAdvanced"):
        name = w_dict.get("ckpt_name") if w_dict else (w_list[0] if w_list else None)
        if isinstance(name, str) and name:
            refs.append(("checkpoints", name))

    if t in ("VAELoader",):
        name = w_dict.get("vae_name") if w_dict else (w_list[0] if w_list else None)
        if isinstance(name, str) and name:
            # Some workflows use sentinel/non-file values here (e.g. "pixel_space").
            # Treat these as non-downloadable and ignore them to keep the report actionable.
            if name in {"pixel_space"}:
                return refs
            refs.append(("vae", name))

    if t in ("UpscaleModelLoader",):
        name = w_dict.get("model_name") if w_dict else (w_list[0] if w_list else None)
        if isinstance(name, str) and name:
            refs.append(("upscale_models", name))

    if t in ("ControlNetLoader", "DiffControlNetLoader"):
        name = w_dict.get("control_net_name") if w_dict else (w_list[0] if w_list else None)
        if isinstance(name, str) and name:
            refs.append(("controlnet", name))

    if t in ("CLIPVisionLoader", "CLIPVisionLoaderMultiGPU", "CLIPVisionLoaderDisTorch2MultiGPU"):
        name = w_dict.get("clip_name") if w_dict else (w_list[0] if w_list else None)
        if isinstance(name, str) and name:
            refs.append(("clip_vision", name))

    # AnimateDiff Evolved motion modules (SD1.5)
    if t in ("ADE_AnimateDiffLoaderGen1",):
        # widgets_values typically: [model_name, beta_schedule]
        name = w_dict.get("model_name") if w_dict else (w_list[0] if w_list else None)
        if isinstance(name, str) and name:
            refs.append(("animatediff_models", name))

    # IP-Adapter Plus "Model Loader" node (if user uses it)
    if t in ("IPAdapterModelLoader",):
        name = w_dict.get("ipadapter_file") if w_dict else (w_list[0] if w_list else None)
        if isinstance(name, str) and name:
            refs.append(("ipadapter", name))

    return refs


def _exists_in_models_dir(models_dir: Path, category: str, filename: str) -> bool:
    # ComfyUI folder_paths generally treats category as a folder under models_dir.
    # It also supports nested subfolders in the filename, so we join directly.
    return (models_dir / category / filename).exists()


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan workflows for model references.")
    parser.add_argument(
        "--models-dir",
        default=os.getenv("COMFYUI_MODELS_DIR_IN_CONTAINER", "/ComfyUI/models"),
        help="ComfyUI models directory inside the container (default: /ComfyUI/models)",
    )
    parser.add_argument(
        "--workflows",
        action="append",
        default=[],
        help="Workflow file or directory to scan (repeatable). Defaults to /ComfyUI/user/default/workflows.",
    )
    parser.add_argument(
        "--show-sources",
        action="store_true",
        help="Show which workflow file(s) referenced each missing model.",
    )
    args = parser.parse_args()

    models_dir = Path(args.models_dir)
    workflow_roots = [Path(p) for p in args.workflows] if args.workflows else []
    if not workflow_roots:
        workflow_roots = [
            Path("/ComfyUI/user/default/workflows"),
        ]

    wf_paths = _iter_workflow_paths(workflow_roots)
    if not wf_paths:
        print("⚠️  No workflow JSON files found to scan.")
        return 0

    found: dict[str, set[str]] = {}
    missing: dict[str, set[str]] = {}
    sources: dict[str, dict[str, set[str]]] = {}

    for p in wf_paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            # many ComfyUI jsons are huge/minified; still text. If parsing fails, skip.
            continue

        nodes = data.get("nodes") or []
        if not isinstance(nodes, list):
            continue

        for node in nodes:
            if not isinstance(node, dict):
                continue
            for category, filename in _extract_model_refs(node):
                found.setdefault(category, set()).add(filename)
                sources.setdefault(category, {}).setdefault(filename, set()).add(str(p))
                if not _exists_in_models_dir(models_dir, category, filename):
                    missing.setdefault(category, set()).add(filename)

    def _print_cat(title: str, m: dict[str, set[str]]) -> None:
        print(f"\n{title}")
        for cat in sorted(m.keys()):
            items = sorted(m[cat])
            if not items:
                continue
            print(f"- {cat} ({len(items)})")
            for it in items:
                print(f"  - {it}")

    # Avoid non-ASCII characters for Windows consoles with legacy encodings.
    print("Workflow model scan")
    print(f"  models_dir: {models_dir}")
    print(f"  scanned:    {len(wf_paths)} workflow json files")

    _print_cat("## Referenced by workflows", found)
    _print_cat("## Missing from models_dir", missing)

    if args.show_sources and missing:
        print("\n## Missing model sources")
        for cat in sorted(missing.keys()):
            for fn in sorted(missing[cat]):
                srcs = sorted(sources.get(cat, {}).get(fn, set()))
                print(f"- {cat}/{fn}")
                for s in srcs:
                    print(f"  - {s}")

    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())

