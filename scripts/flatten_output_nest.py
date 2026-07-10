#!/usr/bin/env python3
"""
Idempotent flatten of Comfy bind-root nesting:

  <bind>/output/{og,wip,experiments,_status}  ->  <bind>/{og,wip,experiments,_status}

Safety:
  - Prefers same-FS directory rename when destination is absent (atomic, preserves inodes).
  - If both source and dest exist: per-file ensure-at-dest (copy if missing; verify size+mtime;
    never overwrite on mismatch); delete source only after verify.
  - Completion gate: zero media remaining under nested library paths; counts match plan.
  - Triple nest <bind>/output/output/ is quarantined separately (disposable).

Dry-run by default. Use --apply to execute.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LIBRARY_DIRS = ("og", "wip", "experiments", "_status")
MEDIA_EXTS = {".mp4", ".png", ".webm", ".jpg", ".jpeg", ".webp", ".gif", ".xmp", ".XMP"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class FilePlan:
    rel: str
    src: str
    dest: str
    size: int
    mtime_ns: int
    status: str = "pending"  # pending|verified|copied|renamed_with_tree|conflict|missing_src


@dataclass
class CutoverReport:
    version: int = 1
    created_at: str = field(default_factory=utc_now)
    bind_root: str = ""
    complete: bool = False
    dry_run: bool = True
    library_dirs: List[str] = field(default_factory=lambda: list(LIBRARY_DIRS))
    planned_files: int = 0
    verified_files: int = 0
    conflicts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    dir_actions: List[Dict[str, str]] = field(default_factory=list)
    nest_remaining_media: int = 0
    nest_samples: List[str] = field(default_factory=list)
    triple_quarantine: Optional[str] = None
    counts_by_dir: Dict[str, Dict[str, int]] = field(default_factory=dict)


def _file_meta(p: Path) -> Tuple[int, int]:
    st = p.stat()
    return int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))


def _same_file(a: Path, b: Path) -> bool:
    if not a.is_file() or not b.is_file():
        return False
    sa, ma = _file_meta(a)
    sb, mb = _file_meta(b)
    return sa == sb and ma == mb


def inventory_dir(src_root: Path, dest_root: Path) -> List[FilePlan]:
    plans: List[FilePlan] = []
    if not src_root.is_dir():
        return plans
    for f in sorted(src_root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(src_root).as_posix()
        dest = dest_root / rel
        size, mtime_ns = _file_meta(f)
        plans.append(
            FilePlan(
                rel=rel,
                src=str(f),
                dest=str(dest),
                size=size,
                mtime_ns=mtime_ns,
            )
        )
    return plans


def nest_media_remaining(bind: Path) -> List[Path]:
    """Media still under nested library locations that should be empty after cutover."""
    leftover: List[Path] = []
    # Nested library: bind/output/{og,wip,experiments,_status}
    nested_lib = bind / "output"
    for name in LIBRARY_DIRS:
        root = nested_lib / name
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if f.is_file():
                leftover.append(f)
    return leftover


def ensure_file(plan: FilePlan, *, apply: bool) -> str:
    src = Path(plan.src)
    dest = Path(plan.dest)
    if not src.is_file():
        if dest.is_file():
            plan.status = "verified"
            return plan.status
        plan.status = "missing_src"
        return plan.status
    if dest.is_file():
        if _same_file(src, dest):
            plan.status = "verified"
            return plan.status
        plan.status = "conflict"
        return plan.status
    if not apply:
        plan.status = "pending"
        return plan.status
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    if not _same_file(src, dest):
        plan.status = "conflict"
        try:
            dest.unlink()
        except OSError:
            pass
        return plan.status
    plan.status = "copied"
    return plan.status


def delete_verified_sources(plans: List[FilePlan], *, apply: bool) -> int:
    deleted = 0
    for plan in plans:
        if plan.status not in {"verified", "copied", "renamed_with_tree"}:
            continue
        src = Path(plan.src)
        dest = Path(plan.dest)
        if not dest.is_file():
            continue
        if src.is_file():
            if not _same_file(src, dest):
                plan.status = "conflict"
                continue
            if apply:
                src.unlink()
                deleted += 1
            plan.status = "verified"
    return deleted


def prune_empty_dirs(root: Path) -> int:
    if not root.is_dir():
        return 0
    removed = 0
    # bottom-up
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        p = Path(dirpath)
        try:
            if not any(p.iterdir()):
                p.rmdir()
                removed += 1
        except OSError:
            pass
    return removed


def try_dir_rename(src: Path, dest: Path, *, apply: bool) -> Optional[str]:
    """Return action label if whole-dir rename applies, else None."""
    if not src.is_dir():
        return None
    if dest.exists():
        return None
    if not apply:
        return "would_rename_dir"
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.rename(src, dest)
    return "renamed_dir"


def quarantine_triple(bind: Path, *, apply: bool) -> Optional[str]:
    triple = bind / "output" / "output"
    if not triple.exists():
        return None
    qdir = bind / f"_quarantine_triple_nest_{stamp()}"
    if not apply:
        return f"would_quarantine:{triple}->{qdir}"
    # Ensure parent output exists
    (bind / "output").mkdir(parents=True, exist_ok=True)
    os.rename(triple, qdir)
    return str(qdir)


def run_cutover(*, bind: Path, apply: bool, quarantine_triple_nest: bool) -> CutoverReport:
    report = CutoverReport(bind_root=str(bind), dry_run=not apply)
    nested = bind / "output"
    all_plans: List[FilePlan] = []

    # Preflight: refuse unexpected bind-root conflicts (non-dir or foreign content)
    for name in LIBRARY_DIRS:
        dest = bind / name
        src = nested / name
        if dest.exists() and src.is_dir():
            # Partial prior run OK if dest is dir — we'll reconcile file-by-file
            if not dest.is_dir():
                report.errors.append(f"conflict: {dest} exists and is not a directory")
        if dest.exists() and not src.exists():
            report.dir_actions.append({"dir": name, "action": "already_flat"})

    if report.errors:
        return report

    for name in LIBRARY_DIRS:
        src = nested / name
        dest = bind / name
        if not src.is_dir() and dest.is_dir():
            report.counts_by_dir[name] = {"src": 0, "dest": sum(1 for f in dest.rglob("*") if f.is_file())}
            continue
        if not src.is_dir() and not dest.is_dir():
            report.counts_by_dir[name] = {"src": 0, "dest": 0}
            continue

        action = try_dir_rename(src, dest, apply=apply)
        if action == "renamed_dir":
            report.dir_actions.append({"dir": name, "action": "renamed_dir", "from": str(src), "to": str(dest)})
            n = sum(1 for f in dest.rglob("*") if f.is_file())
            report.counts_by_dir[name] = {"src": 0, "dest": n, "moved": n}
            # Represent as verified plans for counting
            for f in dest.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(dest).as_posix()
                    size, mtime_ns = _file_meta(f)
                    all_plans.append(
                        FilePlan(
                            rel=f"{name}/{rel}",
                            src=str(src / rel),  # old location (gone)
                            dest=str(f),
                            size=size,
                            mtime_ns=mtime_ns,
                            status="renamed_with_tree",
                        )
                    )
            continue
        if action == "would_rename_dir":
            plans = inventory_dir(src, dest)
            report.dir_actions.append({"dir": name, "action": "would_rename_dir", "files": str(len(plans))})
            report.counts_by_dir[name] = {"src": len(plans), "dest": 0}
            all_plans.extend(plans)
            continue

        # Both exist or rename not possible: file-level reconcile
        plans = inventory_dir(src, dest)
        report.dir_actions.append({"dir": name, "action": "reconcile_files", "files": str(len(plans))})
        for plan in plans:
            # rewrite rel to include dir name for reporting
            plan.rel = f"{name}/{plan.rel}"
            st = ensure_file(plan, apply=apply)
            if st == "conflict":
                report.conflicts.append(f"{plan.src} != {plan.dest}")
        # delete verified sources
        delete_verified_sources(plans, apply=apply)
        if apply:
            prune_empty_dirs(src)
        src_left = sum(1 for f in src.rglob("*") if f.is_file()) if src.is_dir() else 0
        dest_n = sum(1 for f in dest.rglob("*") if f.is_file()) if dest.is_dir() else 0
        report.counts_by_dir[name] = {"src_remaining": src_left, "dest": dest_n, "planned": len(plans)}
        all_plans.extend(plans)

    report.planned_files = len(all_plans)
    report.verified_files = sum(1 for p in all_plans if p.status in {"verified", "copied", "renamed_with_tree"})

    # Triple nest quarantine (not merged into library)
    if quarantine_triple_nest:
        report.triple_quarantine = quarantine_triple(bind, apply=apply)

    # Prune empty nested output/og etc and empty bind/output if empty of library
    if apply:
        for name in LIBRARY_DIRS:
            prune_empty_dirs(nested / name)
        # If bind/output only has empty shells or leftover non-library, leave it

    leftover = nest_media_remaining(bind)
    report.nest_remaining_media = len(leftover)
    report.nest_samples = [str(p.relative_to(bind)) for p in leftover[:20]]

    conflicts = [p for p in all_plans if p.status == "conflict"]
    if conflicts:
        report.conflicts.extend(f"{p.src} != {p.dest}" for p in conflicts[:50])

    if not apply:
        report.complete = False
        report.errors.append("dry_run: not applied")
        # Predict success if every library dir can whole-rename or is already flat
        renameable = all(
            (not (nested / name).is_dir())
            or (not (bind / name).exists())
            or ((bind / name).is_dir() and not (nested / name).is_dir())
            for name in LIBRARY_DIRS
        )
        report.dir_actions.append({"dir": "_dry_run", "action": "renameable_all" if renameable else "needs_reconcile"})
        return report

    src_remaining = 0
    for name in LIBRARY_DIRS:
        src = nested / name
        if src.is_dir():
            src_remaining += sum(1 for f in src.rglob("*") if f.is_file())
    leftover = nest_media_remaining(bind)
    report.nest_remaining_media = len(leftover)
    report.nest_samples = [str(p.relative_to(bind)) for p in leftover[:20]]

    if not (bind / "og").is_dir():
        report.errors.append("missing destination og/")
    if src_remaining:
        report.errors.append(f"nested library media remaining={src_remaining}")
    if leftover:
        report.errors.append(f"nest_media_remaining={len(leftover)}")
    if conflicts:
        report.errors.append(f"file_conflicts={len(conflicts)}")

    report.complete = (
        (bind / "og").is_dir()
        and src_remaining == 0
        and len(leftover) == 0
        and not conflicts
        and not [e for e in report.errors if not e.startswith("dry_run")]
    )
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--bind-root",
        type=Path,
        default=Path("/home/yuji/comfyui-runpod-data/output"),
        help="COMFYUI_BIND_OUTPUT_DIR",
    )
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--quarantine-triple", action="store_true", default=True)
    ap.add_argument("--no-quarantine-triple", action="store_false", dest="quarantine_triple")
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args(argv)

    bind = args.bind_root.expanduser().resolve()
    if not bind.is_dir():
        print(f"error: bind root missing: {bind}", file=sys.stderr)
        return 2

    report = run_cutover(bind=bind, apply=bool(args.apply), quarantine_triple_nest=bool(args.quarantine_triple))

    # Prefer writing report under flat _status if present, else nested, else bind
    status_dir = bind / "_status"
    if not status_dir.is_dir():
        status_dir = bind / "output" / "_status"
    status_dir.mkdir(parents=True, exist_ok=True)
    out = args.report or (status_dir / f"flatten_output_nest_report_{stamp()}.json")
    out = out.expanduser().resolve()
    payload = asdict(report)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    print(f"report={out}")
    if args.apply:
        return 0 if report.complete else 1
    print("Dry-run OK. Re-run with --apply to execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
