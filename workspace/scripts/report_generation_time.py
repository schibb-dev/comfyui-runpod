#!/usr/bin/env python3
"""
Report per-run generation time from experiments (metrics.json).

This consumes the experiment layout produced by tune_experiment.py:
  output/output/experiments/<exp_id>/runs/run_###/{params.json,submit.json,history.json,metrics.json}

Goal:
- Make it easy to see how parameters affect generation time.
- Keep it stdlib-only so it works in the container too.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _read_json_dict(p: Path) -> Dict[str, Any]:
    try:
        obj = _read_json(p)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        v = float(x)
        return v if math.isfinite(v) else None
    try:
        v = float(str(x))
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _iter_experiment_dirs(root_or_exp: Path) -> List[Path]:
    p = root_or_exp
    if (p / "manifest.json").exists():
        return [p]
    if not p.exists() or not p.is_dir():
        return []
    out: List[Path] = []
    try:
        for child in sorted([c for c in p.iterdir() if c.is_dir()], key=lambda x: x.name):
            if (child / "manifest.json").exists() and (child / "runs").exists():
                out.append(child)
    except Exception:
        return out
    return out


_RUN_RE = re.compile(r"^run_\d+$")


def _iter_run_dirs(exp_dir: Path) -> List[Path]:
    runs = exp_dir / "runs"
    if not runs.exists():
        return []
    return sorted([d for d in runs.iterdir() if d.is_dir() and _RUN_RE.match(d.name)], key=lambda x: x.name)


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    deny = math.sqrt(sum((y - my) ** 2 for y in ys))
    if denx == 0.0 or deny == 0.0:
        return None
    return float(num / (denx * deny))


def main() -> int:
    ap = argparse.ArgumentParser(description="Report generation time metrics for ComfyUI tuning experiments")
    ap.add_argument(
        "root_or_exp",
        nargs="?",
        default="output/output/experiments",
        help="Experiment dir (manifest.json) OR root containing experiments (default: output/output/experiments)",
    )
    ap.add_argument(
        "--param",
        action="append",
        default=[],
        help="Parameter key to include in report (repeatable). Example: --param steps --param cfg",
    )
    ap.add_argument("--format", choices=["csv", "json"], default="csv", help="Output format (default: csv)")
    ap.add_argument("--only-with-metrics", action="store_true", help="Only include runs that have metrics.json")
    ap.add_argument("--sort", choices=["gen_time_sec", "run_id"], default="gen_time_sec", help="Sort key (default: gen_time_sec)")
    args = ap.parse_args()

    root = Path(args.root_or_exp)
    exps = _iter_experiment_dirs(root)
    if not exps:
        raise SystemExit(f"No experiments found under: {root}")

    params_wanted: List[str] = [p.strip() for p in (args.param or []) if isinstance(p, str) and p.strip()]
    if not params_wanted:
        params_wanted = ["steps", "cfg", "denoise", "speed", "teacache", "duration_sec"]

    rows: List[Dict[str, Any]] = []
    for exp_dir in exps:
        mf = _read_json_dict(exp_dir / "manifest.json")
        exp_id = str(mf.get("exp_id") or exp_dir.name)
        for run_dir in _iter_run_dirs(exp_dir):
            params = _read_json_dict(run_dir / "params.json")
            metrics_path = run_dir / "metrics.json"
            if args.only_with_metrics and not metrics_path.exists():
                continue
            metrics = _read_json_dict(metrics_path) if metrics_path.exists() else {}

            row: Dict[str, Any] = {
                "exp_id": exp_id,
                "run_id": run_dir.name,
                "gen_time_sec": metrics.get("generation_time_sec"),
                "wait_sec": metrics.get("wait_history_sec"),
                "submit_sec": metrics.get("submit_http_sec"),
                "submitted_at": metrics.get("submitted_at"),
                "done_at": metrics.get("history_collected_at"),
            }
            for k in params_wanted:
                row[k] = params.get(k)
            rows.append(row)

    def sort_key(r: Dict[str, Any]) -> Tuple[int, float, str]:
        if args.sort == "run_id":
            return (0, 0.0, str(r.get("run_id") or ""))
        v = _safe_float(r.get("gen_time_sec"))
        # Put missing at end.
        return (0 if v is not None else 1, float(v) if v is not None else 0.0, str(r.get("run_id") or ""))

    rows.sort(key=sort_key)

    if args.format == "json":
        print(json.dumps({"params": params_wanted, "rows": rows}, ensure_ascii=False, indent=2))
    else:
        fieldnames = ["exp_id", "run_id", *params_wanted, "gen_time_sec", "wait_sec", "submit_sec", "submitted_at", "done_at"]
        import sys

        dw = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        dw.writeheader()
        for r in rows:
            dw.writerow({k: r.get(k) for k in fieldnames})

    # Correlation summary to stderr (so CSV stays clean).
    import sys

    ys: List[float] = []
    for r in rows:
        y = _safe_float(r.get("gen_time_sec"))
        if y is not None:
            ys.append(y)
    if len(ys) < 2:
        return 0

    print("", file=sys.stderr)
    print("Correlation vs gen_time_sec (Pearson):", file=sys.stderr)
    for k in params_wanted:
        xs: List[float] = []
        ys2: List[float] = []
        for r in rows:
            x = _safe_float(r.get(k))
            y = _safe_float(r.get("gen_time_sec"))
            if x is None or y is None:
                continue
            xs.append(x)
            ys2.append(y)
        corr = _pearson(xs, ys2)
        if corr is None:
            continue
        print(f"  {k:<16} r={corr:+.3f}  (n={len(xs)})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

