#!/usr/bin/env python3
"""
Rename og/ artifacts whose basename still contains a literal ``%filename%`` token.

Why: Comfy/VHS save prefixes sometimes left ``%filename%`` unexpanded on disk.
Templates inside embeds are unrelated and are left alone.

Default strategy (``strip_token``): delete the literal token and tidy underscores.
  132430_%filename%_OG_00001.mp4 -> 132430_OG_00001.mp4
  2025-12-06-021851_%filename%_RAW_00001.mp4 -> 2025-12-06-021851_RAW_00001.mp4
  %filename%_104043_FaceBlast8K_OG_00001.mp4 -> 104043_FaceBlast8K_OG_00001.mp4

Dry-run by default. Use ``--apply`` to rename.

Optional: ``--patch-jobs`` rewrites shape-factory job output paths that still
contain the token; ``--write-map`` saves the old→new basename map as JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PLACEHOLDER = "%filename%"
PLACEHOLDER_RE = re.compile(r"%[a-zA-Z_][a-zA-Z0-9_:]*%")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def strip_token_name(name: str) -> str:
    new = name.replace(PLACEHOLDER, "")
    new = PLACEHOLDER_RE.sub("", new)  # belt-and-suspenders for other tokens in basename
    new = re.sub(r"__+", "_", new)
    new = re.sub(r"(^|/)_+", r"\1", new)
    new = re.sub(r"_+(\.[^.]+)$", r"\1", new)
    return new


def rename_basename(name: str, strategy: str) -> str:
    if strategy == "strip_token":
        return strip_token_name(name)
    if strategy == "replace_UNKNOWN":
        new = name.replace(PLACEHOLDER, "UNKNOWN")
        new = re.sub(r"__+", "_", new)
        return new
    raise ValueError(f"unknown strategy: {strategy}")


def collect_placeholder_files(og_root: Path) -> List[Path]:
    out: List[Path] = []
    if not og_root.is_dir():
        return out
    for p in sorted(og_root.rglob("*")):
        if p.is_file() and PLACEHOLDER in p.name:
            out.append(p)
    return out


def build_plan(
    og_root: Path,
    *,
    strategy: str = "strip_token",
) -> Tuple[List[Dict[str, str]], List[str]]:
    """Return (moves, errors). Each move: {day, old_name, new_name, old_rel, new_rel}."""
    files = collect_placeholder_files(og_root)
    existing_by_day: Dict[str, set[str]] = {}
    for day_dir in og_root.iterdir():
        if day_dir.is_dir():
            existing_by_day[day_dir.name] = {f.name for f in day_dir.iterdir() if f.is_file()}

    proposed: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    moves: List[Dict[str, str]] = []
    errors: List[str] = []

    for src in files:
        try:
            rel = src.relative_to(og_root)
            day = rel.parts[0] if len(rel.parts) > 1 else "_"
        except ValueError:
            errors.append(f"not under og_root: {src}")
            continue
        new_name = rename_basename(src.name, strategy)
        if not Path(new_name).stem:
            errors.append(f"empty stem after rename: {src.name}")
            continue
        if new_name == src.name:
            errors.append(f"unchanged: {src.name}")
            continue
        key = (day, new_name)
        proposed[key].append(src.name)
        exist = existing_by_day.get(day, set())
        if new_name in exist and new_name != src.name:
            errors.append(f"collision with existing: {day}/{src.name} -> {new_name}")
            continue
        moves.append(
            {
                "day": day,
                "old_name": src.name,
                "new_name": new_name,
                "old_rel": str(rel).replace("\\", "/"),
                "new_rel": str(Path(day) / new_name).replace("\\", "/"),
                "old_abs": str(src),
                "new_abs": str(src.with_name(new_name)),
            }
        )

    for (day, new_name), sources in proposed.items():
        if len(sources) > 1:
            errors.append(f"batch collision: {day}/{new_name} <= {sources}")

    return moves, errors


def apply_moves(moves: List[Dict[str, str]]) -> List[Dict[str, str]]:
    done: List[Dict[str, str]] = []
    for m in moves:
        src = Path(m["old_abs"])
        dst = Path(m["new_abs"])
        if not src.is_file():
            raise FileNotFoundError(m["old_abs"])
        if dst.exists():
            raise FileExistsError(m["new_abs"])
        src.rename(dst)
        done.append(m)
    return done


def patch_text_paths(text: str, basename_map: Dict[str, str]) -> Tuple[str, int]:
    """Replace old basenames (and common path suffixes) with new ones."""
    n = 0
    out = text
    # Longest-first so we don't partially replace
    for old, new in sorted(basename_map.items(), key=lambda kv: -len(kv[0])):
        if old in out:
            out2 = out.replace(old, new)
            if out2 != out:
                n += out.count(old)
                out = out2
    return out, n


def patch_jobs(jobs_root: Path, basename_map: Dict[str, str]) -> Dict[str, Any]:
    patched = 0
    files = 0
    for jp in sorted(jobs_root.rglob("*.job.json")):
        raw = jp.read_text(encoding="utf-8")
        if PLACEHOLDER not in raw:
            continue
        files += 1
        new, n = patch_text_paths(raw, basename_map)
        if n and new != raw:
            jp.write_text(new, encoding="utf-8")
            patched += 1
    return {"jobs_scanned_with_token": files, "jobs_patched": patched}


def patch_pool_index(path: Path, basename_map: Dict[str, str]) -> Dict[str, Any]:
    if not path.is_file():
        return {"ok": False, "error": "missing", "path": str(path)}
    raw = path.read_text(encoding="utf-8")
    if PLACEHOLDER not in raw:
        return {"ok": True, "patched": False, "replacements": 0, "path": str(path)}
    new, n = patch_text_paths(raw, basename_map)
    if new != raw:
        path.write_text(new, encoding="utf-8")
    return {"ok": True, "patched": new != raw, "replacements": n, "path": str(path)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--og-root",
        type=Path,
        default=Path("/home/yuji/comfyui-runpod-data/output/og"),
    )
    ap.add_argument("--strategy", choices=["strip_token", "replace_UNKNOWN"], default="strip_token")
    ap.add_argument("--apply", action="store_true", help="Perform renames (default is dry-run)")
    ap.add_argument("--write-map", type=Path, default=None, help="Write rename map JSON here")
    ap.add_argument(
        "--patch-jobs",
        action="store_true",
        help="Rewrite %filename% basenames inside shape_factory job JSONs",
    )
    ap.add_argument(
        "--jobs-root",
        type=Path,
        default=Path("/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs"),
    )
    ap.add_argument(
        "--patch-pool",
        type=Path,
        default=None,
        help="Optional pool index.json to rewrite (e.g. .data/pools/FB9-FaceBlast/index.json)",
    )
    ap.add_argument("--limit", type=int, default=0, help="Only plan first N moves (debug)")
    args = ap.parse_args(argv)

    og_root = args.og_root.expanduser().resolve()
    moves, errors = build_plan(og_root, strategy=args.strategy)
    if args.limit and args.limit > 0:
        moves = moves[: int(args.limit)]

    basename_map = {m["old_name"]: m["new_name"] for m in moves}
    # Also map stem forms used in some indexes (without extension) — only for patch helpers
    for old, new in list(basename_map.items()):
        basename_map.setdefault(Path(old).stem, Path(new).stem)

    summary: Dict[str, Any] = {
        "ok": not errors,
        "strategy": args.strategy,
        "og_root": str(og_root),
        "planned_moves": len(moves),
        "error_count": len(errors),
        "errors": errors[:50],
        "samples": [{"old": m["old_rel"], "new": m["new_rel"]} for m in moves[:12]],
        "applied": False,
        "updated_at": utc_now(),
    }

    if errors:
        print(json.dumps(summary, indent=2))
        print(f"Refusing to proceed: {len(errors)} plan errors", file=sys.stderr)
        return 1

    map_path = args.write_map
    if map_path is None and args.apply:
        map_path = og_root.parent / "_status" / f"rename_filename_placeholders_{utc_now().replace(':', '')}.json"
    if map_path is not None:
        map_path = map_path.expanduser().resolve()
        map_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "strategy": args.strategy,
            "created_at": utc_now(),
            "og_root": str(og_root),
            "moves": moves,
        }
        map_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        summary["map_path"] = str(map_path)

    if not args.apply:
        summary["dry_run"] = True
        print(json.dumps(summary, indent=2))
        print(f"Dry-run OK: {len(moves)} renames planned. Re-run with --apply to execute.")
        return 0

    done = apply_moves(moves)
    summary["applied"] = True
    summary["renamed"] = len(done)

    extras: Dict[str, Any] = {}
    if args.patch_jobs:
        extras["jobs"] = patch_jobs(args.jobs_root.expanduser().resolve(), basename_map)
    pool_path = args.patch_pool
    if pool_path is None:
        default_pool = Path("/home/yuji/src/comfyui-runpod/.data/pools/FB9-FaceBlast/index.json")
        if default_pool.is_file() and PLACEHOLDER in default_pool.read_text(encoding="utf-8", errors="ignore"):
            pool_path = default_pool
    if pool_path is not None:
        extras["pool"] = patch_pool_index(Path(pool_path).expanduser().resolve(), basename_map)
    if extras:
        summary["patches"] = extras

    print(json.dumps(summary, indent=2))
    print(
        "Next: rebuild discovery index, lineage (or remap), "
        "`shape_factory.py ratings build`, then tags/heuristics if used."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
