#!/usr/bin/env python3
"""
Fix seed determinism for already-generated experiments.

Why: Some workflows contain multiple seed inputs (e.g. RandomNoise.noise_seed and KSampler.seed),
and may also have "after generate" behaviors that increment/randomize seeds. We want existing
experiment runs to be re-runnable deterministically using the experiment's fixed seed, regardless
of any embedded "after run" seed settings.

Behavior:
- For each experiment run's prompt.json:
  - If an input key exists and looks seed-like, set it to manifest.fixed_seed
  - If "after generate" control keys exist, pin them to "fixed"/disabled
- Optionally reset run state for incomplete runs (archive+remove submit.json/history.json) so
  they will be cleanly requeued.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


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


def _has_run_mp4_outputs(exp_dir: Path, run_id: str) -> bool:
    # Most of our pipelines write outputs into experiment dir root.
    try:
        return any((exp_dir / f"{run_id}_").parent.glob(f"{run_id}_*.mp4"))
    except Exception:
        return False


def _force_fixed_seed_everywhere(prompt: Dict[str, Any], seed: int) -> int:
    n = 0
    for _, node in prompt.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        changed = False
        if "seed" in inputs and isinstance(inputs.get("seed"), int):
            inputs["seed"] = int(seed)
            changed = True
        if "noise_seed" in inputs and isinstance(inputs.get("noise_seed"), int):
            inputs["noise_seed"] = int(seed)
            changed = True

        for k in ("control_after_generate", "seed_after_generate", "seed_after_run"):
            v = inputs.get(k)
            if isinstance(v, str):
                if v.lower() != "fixed":
                    inputs[k] = "fixed"
                    changed = True
            elif isinstance(v, int):
                if v != 0:
                    inputs[k] = 0
                    changed = True
            elif isinstance(v, bool):
                if v is True:
                    inputs[k] = False
                    changed = True

        if changed:
            n += 1
    return n


def _archive_and_remove(p: Path, *, tag: str) -> None:
    if not p.exists():
        return
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = p.parent / f"{p.stem}.{tag}.{ts}{p.suffix}"
    try:
        if not dst.exists():
            dst.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        p.unlink(missing_ok=True)
    except Exception:
        # best effort; if we can't archive/delete, don't fail whole run
        pass


def _fix_run(
    *,
    exp_dir: Path,
    run_dir: Path,
    run_id: str,
    fixed_seed: int,
    indent: int,
    dry_run: bool,
    reset_incomplete: bool,
) -> Tuple[bool, bool]:
    """
    Returns (prompt_changed, state_reset)
    """
    prompt_path = run_dir / "prompt.json"
    if not prompt_path.exists():
        return False, False
    try:
        prompt = _read_json(prompt_path)
    except Exception:
        return False, False
    if not isinstance(prompt, dict):
        return False, False

    before = json.dumps(prompt, sort_keys=True)
    _force_fixed_seed_everywhere(prompt, int(fixed_seed))
    after = json.dumps(prompt, sort_keys=True)
    changed = before != after

    if changed and not dry_run:
        _write_json(prompt_path, prompt, indent=indent)

    state_reset = False
    if reset_incomplete and not _has_run_mp4_outputs(exp_dir, run_id):
        submit = run_dir / "submit.json"
        hist = run_dir / "history.json"
        if (submit.exists() or hist.exists()) and not dry_run:
            _archive_and_remove(submit, tag="seedfix")
            _archive_and_remove(hist, tag="seedfix")
            state_reset = True

    return changed, state_reset


def main() -> int:
    ap = argparse.ArgumentParser(description="Fix existing experiments to use fixed seeds deterministically.")
    ap.add_argument(
        "root_or_exp",
        nargs="?",
        default="output/output/experiments",
        help="Experiment dir or experiments root (default: output/output/experiments)",
    )
    ap.add_argument("--indent", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true", help="Report changes but do not write files.")
    ap.add_argument(
        "--reset-incomplete",
        action="store_true",
        help="For runs with no mp4 outputs yet, archive+remove submit/history so they can be requeued cleanly.",
    )
    args = ap.parse_args()

    root_or_exp = Path(args.root_or_exp)
    exp_dirs = _iter_experiment_dirs(root_or_exp)
    total_runs = 0
    changed_runs = 0
    reset_runs = 0

    for exp_dir in exp_dirs:
        mf = exp_dir / "manifest.json"
        try:
            manifest = _read_json(mf)
        except Exception:
            continue
        if not isinstance(manifest, dict):
            continue
        fixed_seed = manifest.get("fixed_seed")
        if not isinstance(fixed_seed, int):
            # Not a tune experiment manifest shape.
            continue
        runs_dir = exp_dir / "runs"
        if not runs_dir.exists():
            continue
        for run_dir in sorted([d for d in runs_dir.iterdir() if d.is_dir()], key=lambda x: x.name):
            run_id = run_dir.name
            total_runs += 1
            changed, reset = _fix_run(
                exp_dir=exp_dir,
                run_dir=run_dir,
                run_id=run_id,
                fixed_seed=int(fixed_seed),
                indent=int(args.indent),
                dry_run=bool(args.dry_run),
                reset_incomplete=bool(args.reset_incomplete),
            )
            if changed:
                changed_runs += 1
            if reset:
                reset_runs += 1

    print(
        json.dumps(
            {
                "experiments": len(exp_dirs),
                "runs_total": total_runs,
                "runs_prompt_changed": changed_runs,
                "runs_state_reset": reset_runs,
                "dry_run": bool(args.dry_run),
                "reset_incomplete": bool(args.reset_incomplete),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

