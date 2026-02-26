#!/usr/bin/env python3
"""
Backfill experiment input media copies.

For each experiment directory (manifest.json + runs/):
- Read manifest.base_mp4
- Copy base mp4 + matching OG/UPIN mp4 variants and their companion PNGs (if present)
  into: <exp_dir>/inputs/
- Update manifest.json with/merged into `base_media_copies`

Idempotent: won't overwrite existing files; merges manifest list.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import comfy_meta_lib as cml


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(p: Path, obj: Any, *, indent: int) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=indent, ensure_ascii=False), encoding="utf-8")


def _iter_experiment_dirs(root_or_exp: Path) -> List[Path]:
    p = root_or_exp
    if (p / "manifest.json").exists():
        return [p]
    if not p.exists() or not p.is_dir():
        return []
    out: List[Path] = []
    for child in sorted([c for c in p.iterdir() if c.is_dir()], key=lambda x: x.name):
        if (child / "manifest.json").exists() and (child / "runs").exists():
            out.append(child)
    return out


_WIP_VARIANT_RE = re.compile(r"_(OG|UPIN)_(\d+)$", re.IGNORECASE)


def _copy_variants(*, base_mp4: Path, exp_dir: Path) -> List[str]:
    inputs_dir = exp_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    src_dir = base_mp4.parent
    stem = base_mp4.stem
    m = _WIP_VARIANT_RE.search(stem)

    candidates: List[Path] = [base_mp4, base_mp4.with_suffix(".png")]

    if m:
        group = stem[: m.start()]
        idx = m.group(2)

        groups = {group}
        if group.startswith("Test_"):
            groups.add(group[len("Test_") :])
        else:
            groups.add("Test_" + group)

        for g in sorted(groups):
            for var in ("OG", "UPIN"):
                s = src_dir / f"{g}_{var}_{idx}.mp4"
                candidates.append(s)
                candidates.append(s.with_suffix(".png"))

    copied: List[str] = []
    for src in candidates:
        try:
            if not src.exists() or not src.is_file():
                continue
            dst = inputs_dir / src.name
            if dst.exists():
                continue
            shutil.copy2(src, dst)
            copied.append(str(dst.relative_to(exp_dir)).replace("\\", "/"))
        except Exception:
            continue

    return copied


def _extract_inputs_sidecars(*, exp_dir: Path, indent: int) -> Dict[str, int]:
    """
    Best-effort extraction of embedded ComfyUI prompt/workflow JSON from files under <exp_dir>/inputs/.
    Writes sidecars next to the media:
      <stem>.prompt.json
      <stem>.workflow.json

    Preference order:
    - PNG chunks first (usually most faithful)
    - Video container tags second (ffprobe required)
    """
    inputs_dir = exp_dir / "inputs"
    if not inputs_dir.exists():
        return {"written": 0, "skipped_exists": 0, "errors": 0}

    stats = {"written": 0, "skipped_exists": 0, "errors": 0}

    def _write_if_missing(path: Path, obj: Any) -> None:
        if path.exists():
            stats["skipped_exists"] += 1
            return
        if obj is None or not isinstance(obj, dict):
            return
        _write_json(path, obj, indent=indent)
        stats["written"] += 1

    # PNGs
    for p in sorted(inputs_dir.glob("*.png"), key=lambda x: x.name):
        try:
            chunks = cml.read_png_text_chunks(p)
            prompt_obj, workflow_obj = cml.extract_prompt_workflow_from_png_chunks(chunks)
            _write_if_missing(inputs_dir / f"{p.stem}.prompt.json", prompt_obj)
            _write_if_missing(inputs_dir / f"{p.stem}.workflow.json", workflow_obj)
        except Exception:
            stats["errors"] += 1

    # Videos (only if missing)
    for p in sorted(
        list(inputs_dir.glob("*.mp4"))
        + list(inputs_dir.glob("*.mov"))
        + list(inputs_dir.glob("*.mkv"))
        + list(inputs_dir.glob("*.webm")),
        key=lambda x: x.name,
    ):
        try:
            prompt_path = inputs_dir / f"{p.stem}.prompt.json"
            workflow_path = inputs_dir / f"{p.stem}.workflow.json"
            if prompt_path.exists() and workflow_path.exists():
                continue
            tags = cml.ffprobe_format_tags(p)
            prompt_obj, workflow_obj = cml.extract_prompt_workflow_from_tags(tags)
            _write_if_missing(prompt_path, prompt_obj)
            _write_if_missing(workflow_path, workflow_obj)
        except Exception:
            stats["errors"] += 1

    return stats


def _merge_str_list(existing: Any, add: List[str]) -> List[str]:
    out: List[str] = []
    if isinstance(existing, list):
        for x in existing:
            if isinstance(x, str) and x not in out:
                out.append(x)
    for x in add:
        if isinstance(x, str) and x not in out:
            out.append(x)
    return out


def _resolve_base_mp4(*, base_mp4_str: str, workspace_root: Path) -> Optional[Path]:
    s = (base_mp4_str or "").strip()
    if not s:
        return None

    # Handle container-style absolute paths like /workspace/output/output/wip/...
    # Map them back into this repo by stripping the /workspace prefix.
    ss = s.replace("\\", "/")
    if ss.startswith("/workspace/"):
        rel = ss[len("/workspace/") :]
        cand = (workspace_root / rel).resolve()
        if cand.exists():
            return cand
        # Some users keep these under workspace/...
        cand2 = (workspace_root / "workspace" / rel).resolve()
        if cand2.exists():
            return cand2

    p = Path(s)
    if p.is_absolute():
        return p

    # Manifests often store repo-relative paths like output/output/wip/...
    cand = (workspace_root / p).resolve()
    if cand.exists():
        return cand

    # Common alternative: files live under workspace/output/... while manifest references output/...
    cand2 = (workspace_root / "workspace" / p).resolve()
    if cand2.exists():
        return cand2

    # Fallback: search known wip roots by filename (best-effort).
    fname = Path(s).name
    if fname:
        for root in [
            (workspace_root / "output" / "output" / "wip"),
            (workspace_root / "workspace" / "output" / "output" / "wip"),
        ]:
            try:
                if not root.exists():
                    continue
                for hit in root.rglob(fname):
                    if hit.is_file():
                        return hit.resolve()
            except Exception:
                continue

    return None


def _maybe_set_base_mp4_fallback(*, manifest: Dict[str, Any], exp_dir: Path, base_mp4_str: str) -> bool:
    """
    If manifest.base_mp4 can't be resolved anymore but the file exists under <exp_dir>/inputs/,
    record a best-effort fallback path.

    Returns True if manifest was modified.
    """
    if not isinstance(base_mp4_str, str) or not base_mp4_str.strip():
        return False
    fname = Path(base_mp4_str).name
    if not fname:
        return False
    inputs_dir = exp_dir / "inputs"
    if not inputs_dir.exists():
        return False
    hit = inputs_dir / fname
    if not hit.exists():
        return False

    rel = str(hit.relative_to(exp_dir)).replace("\\", "/")
    prev = manifest.get("base_mp4_fallback")
    if prev == rel:
        return False
    manifest["base_mp4_fallback"] = rel
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Copy OG/UPIN/PNG base media into existing experiments' inputs/ dirs.")
    ap.add_argument(
        "root_or_exp",
        nargs="?",
        default="output/output/experiments",
        help="Experiment dir or experiments root (default: output/output/experiments)",
    )
    ap.add_argument("--workspace-root", default=".", help="Workspace root for resolving relative base_mp4 paths (default: .)")
    ap.add_argument("--indent", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--no-extract-sidecars",
        action="store_true",
        help="Do not extract inputs/<stem>.{prompt,workflow}.json from copied media.",
    )
    args = ap.parse_args()

    ws_root = Path(args.workspace_root).resolve()
    exp_dirs = _iter_experiment_dirs(Path(args.root_or_exp))

    stats = {
        "experiments": len(exp_dirs),
        "updated_manifests": 0,
        "copied_files": 0,
        "skipped_no_base_mp4": 0,
        "skipped_missing_base_mp4": 0,
        "set_base_mp4_fallback": 0,
        "sidecars_written": 0,
        "sidecars_skipped_exists": 0,
        "sidecars_errors": 0,
        "dry_run": bool(args.dry_run),
    }

    for exp_dir in exp_dirs:
        mf_path = exp_dir / "manifest.json"
        try:
            manifest = _read_json(mf_path)
        except Exception:
            continue
        if not isinstance(manifest, dict):
            continue

        base_mp4_str = manifest.get("base_mp4")
        if not isinstance(base_mp4_str, str) or not base_mp4_str.strip():
            stats["skipped_no_base_mp4"] += 1
            # Still try to extract sidecars from whatever is already in inputs/.
            if not args.dry_run and not args.no_extract_sidecars:
                s = _extract_inputs_sidecars(exp_dir=exp_dir, indent=int(args.indent))
                stats["sidecars_written"] += int(s.get("written", 0))
                stats["sidecars_skipped_exists"] += int(s.get("skipped_exists", 0))
                stats["sidecars_errors"] += int(s.get("errors", 0))
            continue

        base_mp4 = _resolve_base_mp4(base_mp4_str=base_mp4_str, workspace_root=ws_root)
        if base_mp4 is None or not base_mp4.exists():
            stats["skipped_missing_base_mp4"] += 1
            # Even if we can't find the base mp4 anymore, inputs/ may already contain copies.
            if not args.dry_run and not args.no_extract_sidecars:
                s = _extract_inputs_sidecars(exp_dir=exp_dir, indent=int(args.indent))
                stats["sidecars_written"] += int(s.get("written", 0))
                stats["sidecars_skipped_exists"] += int(s.get("skipped_exists", 0))
                stats["sidecars_errors"] += int(s.get("errors", 0))
            if not args.dry_run:
                if _maybe_set_base_mp4_fallback(manifest=manifest, exp_dir=exp_dir, base_mp4_str=base_mp4_str):
                    stats["set_base_mp4_fallback"] += 1
                    _write_json(mf_path, manifest, indent=int(args.indent))
                    stats["updated_manifests"] += 1
            continue

        if args.dry_run:
            # still compute what would be copied
            would = _copy_variants(base_mp4=base_mp4, exp_dir=exp_dir)
            stats["copied_files"] += len(would)
            continue

        copied = _copy_variants(base_mp4=base_mp4, exp_dir=exp_dir)
        stats["copied_files"] += len(copied)

        if not args.no_extract_sidecars:
            s = _extract_inputs_sidecars(exp_dir=exp_dir, indent=int(args.indent))
            stats["sidecars_written"] += int(s.get("written", 0))
            stats["sidecars_skipped_exists"] += int(s.get("skipped_exists", 0))
            stats["sidecars_errors"] += int(s.get("errors", 0))

        before = manifest.get("base_media_copies")
        merged = _merge_str_list(before, copied)
        changed = False
        if merged != before:
            manifest["base_media_copies"] = merged
            changed = True
        # If base_mp4 is resolvable we generally don't need a fallback, but if an inputs/ copy exists,
        # recording it doesn't hurt and makes tooling more robust when wip dirs move.
        if _maybe_set_base_mp4_fallback(manifest=manifest, exp_dir=exp_dir, base_mp4_str=base_mp4_str):
            stats["set_base_mp4_fallback"] += 1
            changed = True
        if changed:
            _write_json(mf_path, manifest, indent=int(args.indent))
            stats["updated_manifests"] += 1

    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

