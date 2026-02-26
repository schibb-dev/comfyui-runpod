#!/usr/bin/env python3
"""
Download models listed in scripts/model_download_manifest.yaml into a ComfyUI models folder.

Designed for comfyui-runpod where /ComfyUI/models is commonly bind-mounted to a host folder
(e.g. E:\\models on Windows via COMFYUI_MODELS_DIR).

Examples (inside container):
  python3 /workspace/scripts/download_models_manifest.py --profile animatediff_ipadapter_controlnet
  python3 /workspace/scripts/download_models_manifest.py --tag ipadapter --tag clip_vision
  python3 /workspace/scripts/download_models_manifest.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from typing import Any

import requests
import yaml


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_stream(url: str, dst: Path, headers: dict[str, str] | None = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".partial")

    with requests.get(url, headers=headers or {}, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)

        downloaded = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = 100.0 * downloaded / total
                    print(f"\r  {dst.name}: {pct:5.1f}% ({downloaded}/{total} bytes)", end="", flush=True)
                else:
                    print(f"\r  {dst.name}: {downloaded} bytes", end="", flush=True)
        print("")

    tmp.replace(dst)


def _load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "models" not in data:
        raise ValueError(f"Invalid manifest: {path}")
    return data


def _resolve_selection(manifest: dict[str, Any], tags: list[str], profiles: list[str]) -> list[dict[str, Any]]:
    models = manifest.get("models") or []
    if not isinstance(models, list):
        raise ValueError("Manifest 'models' must be a list")

    selected_tags: set[str] = set(tags)
    profile_map = manifest.get("profiles") or {}
    if profiles:
        for p in profiles:
            prof = profile_map.get(p)
            if not prof:
                raise ValueError(f"Unknown profile: {p}")
            prof_tags = prof.get("tags") if isinstance(prof, dict) else None
            if not isinstance(prof_tags, list):
                raise ValueError(f"Profile '{p}' must define a list of tags")
            selected_tags.update(str(t) for t in prof_tags)

    if not selected_tags:
        # If nothing specified, default to all models in the manifest.
        return models

    out: list[dict[str, Any]] = []
    for m in models:
        mtags = m.get("tags") or []
        if any(t in mtags for t in selected_tags):
            out.append(m)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Download models from a curated manifest.")
    parser.add_argument(
        "--models-dir",
        default=os.getenv("COMFYUI_MODELS_DIR_IN_CONTAINER", "/ComfyUI/models"),
        help="ComfyUI models directory in the container (default: /ComfyUI/models).",
    )
    parser.add_argument(
        "--manifest",
        default="/workspace/scripts/model_download_manifest.yaml",
        help="Path to manifest YAML (default: /workspace/scripts/model_download_manifest.yaml).",
    )
    parser.add_argument("--tag", action="append", default=[], help="Tag to include (repeatable).")
    parser.add_argument("--profile", action="append", default=[], help="Profile to include (repeatable).")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only; do not download.")
    parser.add_argument("--force", action="store_true", help="Re-download even if the file exists.")
    args = parser.parse_args()

    models_dir = Path(args.models_dir)
    manifest_path = Path(args.manifest)

    if not manifest_path.exists():
        print(f"ERROR: Manifest not found: {manifest_path}")
        return 2

    manifest = _load_manifest(manifest_path)
    selected = _resolve_selection(manifest, tags=args.tag, profiles=args.profile)

    # Avoid non-ASCII characters for Windows consoles with legacy encodings.
    print("Manifest downloader")
    print(f"  Models dir: {models_dir}")
    print(f"  Manifest:   {manifest_path}")
    if args.profile:
        print(f"  Profiles:   {args.profile}")
    if args.tag:
        print(f"  Tags:       {args.tag}")
    if args.dry_run:
        print("  Mode:       dry-run")

    # Token handling (optional). Most URLs are public.
    hf_token = os.getenv("HUGGINGFACE_TOKEN") or ""
    headers = {"Authorization": f"Bearer {hf_token}"} if hf_token else {}

    ok = 0
    skipped = 0
    failed = 0

    for m in selected:
        mid = m.get("id") or m.get("filename") or "unknown"
        url = m.get("url")
        dest_rel = m.get("dest")
        expected_sha = (m.get("sha256") or "").strip().lower()

        if not url or not dest_rel:
            print(f"WARNING: Skipping {mid}: missing url/dest in manifest")
            skipped += 1
            continue

        dst = models_dir / str(dest_rel)
        if dst.exists() and not args.force:
            # verify sha if provided
            if expected_sha:
                have_sha = _sha256_file(dst)
                if have_sha != expected_sha:
                    print(f"INFO: {mid}: sha256 mismatch, will re-download")
                else:
                    print(f"OK: {mid}: already present ({dst})")
                    skipped += 1
                    continue
            else:
                print(f"OK: {mid}: already present ({dst})")
                skipped += 1
                continue

        print(f"\nDownloading {mid}")
        print(f"  url:  {url}")
        print(f"  dest: {dst}")
        if args.dry_run:
            ok += 1
            continue

        try:
            _download_stream(url, dst, headers=headers)
            if expected_sha:
                have_sha = _sha256_file(dst)
                if have_sha != expected_sha:
                    raise RuntimeError(f"sha256 mismatch: expected {expected_sha} got {have_sha}")
            print(f"OK: Done: {mid}")
            ok += 1
        except Exception as e:
            print(f"ERROR: Failed: {mid}: {e}")
            failed += 1

    print("\nSummary")
    print(f"  downloaded: {ok}")
    print(f"  skipped:    {skipped}")
    print(f"  failed:     {failed}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

