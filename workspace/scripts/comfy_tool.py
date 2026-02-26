#!/usr/bin/env python3
"""
One entrypoint that composes the various ComfyUI metadata/template utilities.

This is intentionally a thin wrapper around the existing scripts so we don't
duplicate business logic. It forwards arguments to the underlying scripts.

Examples:
  python scripts/comfy_tool.py process-wip-dir output/output/wip/2026-02-01
  python scripts/comfy_tool.py check-roundtrip output/output/wip/2026-02-01

To see the underlying script's help for a subcommand, pass `-- --help`:
  python scripts/comfy_tool.py process-wip-dir -- --help
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List


SCRIPTS_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable


COMMANDS = {
    # name: script filename
    "metadata": "extract_comfy_metadata.py",
    "preset": "extract_comfy_preset.py",
    "update-xmp": "update_comfy_seed_xmp.py",
    "clean-workflow": "clean_comfy_workflow.py",
    "canonicalize-titles": "canonicalize_comfy_titles.py",
    "apply-preset": "apply_comfy_preset.py",
    "process-wip-dir": "process_wip_dir.py",
    "check-wip-agreement": "check_wip_agreement.py",
    "check-roundtrip": "check_roundtrip_dir.py",
    "canonicalize-xmp": "canonicalize_xmp_filenames.py",
    "tune-sweep": "tune_experiment.py",
    "tune-run": "tune_experiment.py",
    "tune-materialize": "tune_experiment.py",
    "tune-apply": "tune_experiment.py",
    "watch-queue": "watch_queue.py",
    "fix-seeds": "fix_experiment_seeds.py",
    "copy-inputs": "copy_experiment_inputs.py",
}

COMMAND_HELP = {
    "metadata": "Extract embedded ComfyUI metadata from PNG/MP4 (redacted by default).",
    "preset": "Extract a compact preset JSON (run parameters) from PNG/MP4 metadata.",
    "update-xmp": "Merge comfy:* seed/hashes into an XMP sidecar (safe merge).",
    "clean-workflow": "Clean a workflow JSON into a git-friendly template (strip UI noise, delocalize paths).",
    "canonicalize-titles": "Rename/normalize node titles to stable RUN_/PROMPT_/IN_/OUT_ names.",
    "apply-preset": "Apply a preset JSON onto a template workflow to produce a runnable workflow JSON.",
    "process-wip-dir": "Batch-generate sidecars (.preset/.metadata/.workflow/.template/.XMP) for a wip folder.",
    "check-wip-agreement": "Verify OG/UPIN/PNG variants agree on seeds/hashes within a wip folder.",
    "check-roundtrip": "Verify end-to-end roundtrip: embedded MP4 <-> sidecars and preset->template application.",
    "canonicalize-xmp": "Rename XMP sidecars to use uppercase .XMP extension.",
    "tune-sweep": "Generate a tuning experiment sweep directory (fixed seed + short duration + parameter grid).",
    "tune-run": "Run a generated tuning experiment sweep via ComfyUI HTTP API (default: 127.0.0.1:8188).",
    "tune-materialize": "Retroactively write per-run candidate workflows from params.json (usable like tuned workflows).",
    "tune-apply": "Apply per-run tuning params onto a workflow template and export tuned workflows (no output bookkeeping).",
    "watch-queue": "Watch experiments root/dir and submit+collect runs asynchronously (queue now, check later).",
    "fix-seeds": "Rewrite existing experiments to force fixed seeds deterministically (optionally reset incomplete runs).",
    "copy-inputs": "Backfill experiments with copied OG/UPIN MP4s + PNGs in inputs/ (for self-contained experiment folders).",
}


def _commands_epilog() -> str:
    lines = ["available commands:"]
    for name in sorted(COMMANDS.keys()):
        one = COMMAND_HELP.get(name, "").strip()
        lines.append(f"  {name:<18} {one}")
    lines.append("")
    lines.append("pass-through args:")
    lines.append("  python scripts/comfy_tool.py <command> -- <args-for-underlying-script>")
    lines.append("")
    lines.append("example:")
    lines.append("  python scripts/comfy_tool.py process-wip-dir -- --help")
    return "\n".join(lines)


def _run(command_name: str, script_name: str, forwarded: List[str]) -> int:
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        raise SystemExit(f"Missing script: {script_path}")
    # Special case: tune_experiment.py has subcommands. Map comfy_tool commands to its subcommands.
    if script_name == "tune_experiment.py":
        if forwarded and forwarded[0] in ("generate", "run", "materialize", "apply"):
            subcmd = []
        else:
            if command_name == "tune-sweep":
                subcmd = ["generate"]
            elif command_name == "tune-materialize":
                subcmd = ["materialize"]
            elif command_name == "tune-apply":
                subcmd = ["apply"]
            else:
                subcmd = ["run"]
        cmd = [PYTHON, str(script_path), *subcmd, *forwarded]
    else:
        cmd = [PYTHON, str(script_path), *forwarded]
    proc = subprocess.run(cmd)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ComfyUI workflow/media utility toolbox",
        epilog=_commands_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "command",
        choices=sorted(COMMANDS.keys()),
        help="Which tool to run (forwards args to underlying script).",
    )
    ap.add_argument("args", nargs=argparse.REMAINDER, help="Arguments for the underlying script (use -- to separate)")
    ns = ap.parse_args()

    # If user typed: comfy_tool.py preset -- --help
    forwarded = ns.args
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    return _run(ns.command, COMMANDS[ns.command], forwarded)


if __name__ == "__main__":
    raise SystemExit(main())

