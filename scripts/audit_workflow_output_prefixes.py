#!/usr/bin/env python3
"""
Audit (and optionally fix) nested output prefixes in saved ComfyUI workflows.

Scans LiteGraph workflow JSON under the comfyui_user workflows tree for save nodes
whose filename_prefix would write to <bind>/output/og/... instead of <bind>/og/...
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "workspace" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from output_path_lib import normalize_ui_workflow_output_prefixes, read_bind_output_dir  # noqa: E402


@dataclass
class AuditFinding:
    path: str
    changes: list[str] = field(default_factory=list)


def default_workflows_root(repo_root: Path) -> Path:
    bind_user = read_bind_output_dir(repo_root)
    if bind_user is not None:
        candidate = bind_user.parent / "comfyui_user" / "default" / "workflows"
        if candidate.is_dir():
            return candidate
    return Path("/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows")


def audit_workflows(root: Path, *, apply: bool = False) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"workflows root missing: {root}")

    for path in sorted(root.rglob("*.json")):
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            workflow: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(workflow.get("nodes"), list):
            continue
        probe = json.loads(json.dumps(workflow))
        changes = normalize_ui_workflow_output_prefixes(probe)
        if not changes:
            continue
        finding = AuditFinding(path=str(path), changes=changes)
        findings.append(finding)
        if apply:
            normalize_ui_workflow_output_prefixes(workflow)
            try:
                path.write_text(
                    json.dumps(workflow, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                finding.changes.append(f"WRITE_FAILED: {exc}")
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--workflows-root",
        type=Path,
        default=None,
        help="Workflow JSON root (default: comfyui_user/default/workflows from .env bind)",
    )
    ap.add_argument("--apply", action="store_true", help="Rewrite workflow files in place")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = args.workflows_root or default_workflows_root(_REPO_ROOT)
    findings = audit_workflows(root, apply=bool(args.apply))

    if args.json:
        print(json.dumps([f.__dict__ for f in findings], indent=2))
    else:
        print(f"workflows_root: {root}")
        print(f"files_with_nested_prefixes: {len(findings)}")
        for f in findings:
            print(f"\n{f.path}")
            for line in f.changes:
                print(f"  - {line}")
        if findings and not args.apply:
            print("\nRe-run with --apply to fix in place.")
        elif findings and args.apply:
            print(f"\nApplied fixes to {len(findings)} workflow file(s).")

    return 1 if findings and not args.apply else 0


if __name__ == "__main__":
    raise SystemExit(main())
